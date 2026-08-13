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


def gravar(cache: dict, caminho: Path | None = None) -> None:
    """Escrita atômica: temporário no MESMO diretório e `os.replace`."""
    p = Path(caminho or CAMINHO_PADRAO)
    p.parent.mkdir(parents=True, exist_ok=True)
    cache.setdefault("_meta", _meta())
    ent = cache.get("entradas") or {}
    if len(ent) > MAX_REGISTROS:
        # poda simples por uso mais recente; não é LRU sofisticado de propósito
        ordenadas = sorted(ent.items(),
                           key=lambda kv: kv[1].get("last_used_at") or 0,
                           reverse=True)[:MAX_REGISTROS]
        cache["entradas"] = dict(ordenadas)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def consultar(cache: dict, k: str) -> dict | None:
    v = (cache.get("entradas") or {}).get(k)
    if not v or not v.get("ok"):
        return None
    v["last_used_at"] = int(time.time())
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
