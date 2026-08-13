#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translation_cache.py — cache persistente de traduções, em sidecar próprio.

POR QUE UM SIDECAR E NÃO O `risk_history.json`:

O histórico é EVIDÊNCIA — o texto como o veículo publicou. Guardar tradução lá
misturaria dado operacional com prova, tornaria a invalidação por versão de
prompt uma migração de histórico, e faria qualquer rollback de política de
tradução virar edição de registro histórico. O history segue intacto e original.

O QUE ISTO RESOLVE:

`translate_articles` decide o que traduzir por `not a.get("title_pt")`, mas
`title_pt` nunca é persistido — não existe no schema do history. Consequência
medida: dos 769 artigos do corpus, ZERO têm tradução guardada, e todo artigo em
inglês ou espanhol é retraduzido a cada run, para sempre.

REGRAS:

  SUCCESS-ONLY   erro de provider, cota esgotada, saída vazia ou item faltando
                 no lote NÃO viram registro de cache. Falha volta ao original.
  VERSIONADO     a chave carrega versão de prompt, política e modelo. Mudou a
                 política, o cache antigo não é reaproveitado em silêncio.
  ATÔMICO        escreve em temporário e troca; um kill no meio não deixa
                 sidecar truncado.
  FAIL-OPEN      sidecar corrompido ou ilegível vira cache vazio. Cache é
                 otimização; ele nunca pode derrubar o pipeline.

NÃO CHAMA REDE. NÃO CONHECE PROVIDER.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import unicodedata
from pathlib import Path

CACHE_VERSION = "tc.v1"
# Versão da POLÍTICA de tradução (prompt + campos + limite de chars). Mudou o
# prompt de forma material? Suba isto e o cache antigo deixa de casar.
TRANSLATION_POLICY_VERSION = "trad.p1"

CAMINHO_PADRAO = Path(os.environ.get("RISK_TRANSLATION_CACHE",
                                     "risk_translation_cache.json"))
MAX_REGISTROS = 20000


def _norm(s: str) -> str:
    """Normalização só para a CHAVE — o texto guardado continua íntegro."""
    s = unicodedata.normalize("NFC", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def chave(titulo: str, resumo: str, idioma: str, alvo: str,
          modelo: str = "", max_chars: int = 0) -> str:
    """Identidade do TRABALHO de tradução, não do artigo.

    Deliberadamente não usa URL: a mesma manchete republicada por outro veículo
    é o mesmo trabalho e deve reaproveitar. E inclui o resumo porque ele também
    é traduzido — dois artigos de mesmo título e resumos diferentes são
    trabalhos distintos.
    """
    partes = [CACHE_VERSION, TRANSLATION_POLICY_VERSION, modelo or "",
              str(max_chars or ""), (idioma or "").lower(), (alvo or "").lower(),
              _norm(titulo), _norm(resumo)]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()


def carregar(caminho: Path | None = None) -> dict:
    """Fail-open: qualquer problema devolve cache vazio, nunca levanta."""
    p = Path(caminho or CAMINHO_PADRAO)
    if not p.exists():
        return {"_meta": _meta(), "entradas": {}}
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return {"_meta": _meta(), "entradas": {}, "_aviso": "sidecar ilegível — recomeçado vazio"}
    if not isinstance(d, dict) or not isinstance(d.get("entradas"), dict):
        return {"_meta": _meta(), "entradas": {}, "_aviso": "formato inesperado — recomeçado vazio"}
    # registro individual corrompido não contamina os demais
    limpas = {}
    for k, v in d["entradas"].items():
        if isinstance(v, dict) and isinstance(v.get("title"), str) and v.get("ok"):
            limpas[k] = v
    d["entradas"] = limpas
    return d


def _meta() -> dict:
    return {"cache_version": CACHE_VERSION,
            "translation_policy_version": TRANSLATION_POLICY_VERSION,
            "proposito": "cache operacional de traduções; NÃO é histórico e "
                         "não substitui o texto original do risk_history.json"}


def _serializar(cache: dict) -> str:
    # chaves ordenadas: dois runs com o mesmo conteúdo produzem bytes iguais,
    # o que é o que permite detectar "nada mudou" de forma confiável
    return json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True)


def gravar(cache: dict, caminho: Path | None = None) -> bool:
    """Escrita atômica e SÓ SE MUDOU. Devolve True se escreveu.

    Um run inteiro de acertos de cache não deve tocar o arquivo: sem isto, todo
    cron produziria diff e o pipeline commitaria dado idêntico quatro vezes por
    dia. `os.replace` sobre temporário no MESMO diretório garante que um kill
    no meio não deixe sidecar truncado.
    """
    p = Path(caminho or CAMINHO_PADRAO)
    cache.setdefault("_meta", _meta())
    ent = cache.get("entradas") or {}
    if len(ent) > MAX_REGISTROS:
        # poda simples por data de criação; não é LRU sofisticado de propósito,
        # e nem precisa ser — no ritmo medido, o teto leva mais de uma década
        ordenadas = sorted(ent.items(),
                           key=lambda kv: kv[1].get("created_at") or 0,
                           reverse=True)[:MAX_REGISTROS]
        cache["entradas"] = dict(ordenadas)

    novo = _serializar(cache)
    if p.exists():
        try:
            if io.open(p, encoding="utf-8").read() == novo:
                return False          # nada mudou: nem toca no arquivo
        except Exception:
            pass                      # ilegível: reescreve
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(novo)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return True


def fundir(base: dict, outro: dict) -> dict:
    """Une dois caches por CHAVE, preservando o registro mais antigo.

    Registros de tradução são independentes e imutáveis: a mesma chave sempre
    descreve o mesmo trabalho, então não há conflito real a resolver — só união.
    Existe para o caso de dois runs concorrentes terem lido o mesmo snapshot;
    sem isso, o segundo a gravar apagaria as traduções do primeiro.
    """
    saida = {"_meta": base.get("_meta") or _meta(), "entradas": {}}
    saida["entradas"].update(outro.get("entradas") or {})
    saida["entradas"].update(base.get("entradas") or {})
    return saida


def consultar(cache: dict, k: str) -> dict | None:
    """Leitura PURA — não marca uso, de propósito.

    A primeira versão gravava `last_used_at` a cada acerto. Com centenas de
    acertos por run, isso mudava o sidecar em TODO cron mesmo sem nenhuma
    tradução nova: diff garantido, commit de dados inútil quatro vezes por dia
    e conflito de merge à toa. O cache é pequeno (centenas de registros, teto
    de poda em 20 mil) — precisão de LRU não paga esse preço.
    """
    v = (cache.get("entradas") or {}).get(k)
    if not v or not v.get("ok"):
        return None
    return v


def armazenar(cache: dict, k: str, *, titulo: str, resumo: str,
              idioma: str, alvo: str, modelo: str = "") -> None:
    """SUCCESS-ONLY. Título vazio não é tradução — é falha silenciosa."""
    if not titulo:
        return
    cache.setdefault("entradas", {})[k] = {
        "title": titulo,
        "summary": resumo or "",
        "src_lang": (idioma or "").lower(),
        "target": (alvo or "").lower(),
        "model": modelo or "",
        "policy": TRANSLATION_POLICY_VERSION,
        "cache_version": CACHE_VERSION,
        "created_at": int(time.time()),
        "last_used_at": int(time.time()),
        "ok": True,
    }


def estatisticas(cache: dict) -> dict:
    ent = cache.get("entradas") or {}
    return {"registros": len(ent),
            "bytes_aprox": len(json.dumps(cache, ensure_ascii=False)),
            "cache_version": CACHE_VERSION,
            "policy": TRANSLATION_POLICY_VERSION}
