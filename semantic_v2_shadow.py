#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow prospectivo do Contract V2 — observação, nunca score.

POR QUE PROSPECTIVO E NÃO MAIS RETROSPECTIVO

No benchmark do run 31798963989 os dois Flash-Lite fizeram 8/8 contra 6/8 do
determinístico. O número é bom e é otimista por construção: o Contract V2 foi
desenhado depois de analisar exatamente aqueles 8 casos. Não é vazamento — a
verdade humana nunca entrou em prompt e a projeção foi congelada antes de
tocar o artefato —, mas 8/8 na amostra que motivou o desenho não autoriza
esperar 8/8 fora dela.

Esta camada existe para responder a pergunta que aquele número não responde:
o V2 sobrevive em artigos que ninguém viu quando o contrato foi escrito?

O QUE ELA NÃO FAZ

Não altera evento, score, status, `build_evolution` ou history. O resultado
vive num sidecar próprio e não é lido por nenhum caminho de produção. Falha de
provider, de cota ou de schema não derruba o cron: shadow é observação.

O FREEZE É O QUE TORNA ISTO UM HOLDOUT

Contract V2 foi congelado no commit 7526667, em 2026-08-14T12:06:33Z. Só conta
como validação prospectiva o par artigo×empresa que (a) foi capturado depois
disso e (b) não está no corpus de desenvolvimento. Se ajustarmos o contrato a
cada caso novo, nunca teremos holdout — por isso o V2 está congelado.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path

SHADOW_VERSION = "sem.v2.shadow.v1"
CONTRACT_FREEZE_COMMIT = "7526667d36e0266fc01f9f15af2aea1aa92404ac"
CONTRACT_FREEZE_ISO = "2026-08-14T12:06:33Z"
CONTRACT_FREEZE_TS = 1786795593        # epoch do commit acima

CAMINHO = Path(os.environ.get("RISK_SEMANTIC_V2_SHADOW",
                              "risk_semantic_v2_shadow.json"))

# Teto por execução. Não é um limite de cota do provider — é segurança
# operacional. A cota externa muda sem aviso e não deve virar constante aqui.
MAX_CASOS_POR_RUN = 5
MODELOS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
MAX_CHAMADAS_POR_RUN = MAX_CASOS_POR_RUN * len(MODELOS)

# Famílias priorizadas na seleção. Vêm das dimensões onde o V2 mudou algo ou
# onde a atribuição historicamente erra — não de verdade humana.
FAMILIAS_PRIORITARIAS = ("ma", "falencia", "recuperacao_judicial", "troca_ceo",
                         "rebaixamento_rating", "fraude", "default")

# Marcadores textuais de follow-up/vigência. Servem só para PRIORIZAR o que
# entra no shadow; não decidem nada e não vão ao modelo.
_PISTAS_NOVIDADE = ("após", "apos", "depois de", "conclusão", "conclusao",
                    "aprovação", "aprovacao", "homologa", "recurso",
                    "estratégi", "estrategi", "nova marca", "passa a se chamar")

INTERROMPEM = frozenset({"QUOTA_EXHAUSTED", "RATE_LIMITED", "AUTH_ERROR",
                         "INVALID_REQUEST", "CLIENT_SCHEMA_ERROR"})


# ── identidade estável e chave versionada ───────────────────────────────────
def id_artigo(url: str, titulo: str = "") -> str:
    """Identidade do ARTIGO, estável entre runs. A URL manda; o título é
    desempate quando a URL falta."""
    base = (url or "").strip() or ("titulo:" + (titulo or "").strip())
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def chave(artigo_id: str, empresa: str, event_id: str, contrato: str,
          prompt_v: str, modelo: str) -> str:
    """Chave ciente de VERSÃO: um resultado V2 nunca é sobrescrito por V3.

    Sem o contrato e o prompt na chave, uma mudança futura de prompt
    silenciosamente reescreveria observações prospectivas já feitas — e o
    holdout deixaria de existir sem ninguém notar.
    """
    return "|".join((artigo_id, empresa, event_id or "-", contrato,
                     prompt_v, modelo))


# ── sidecar ─────────────────────────────────────────────────────────────────
def carregar(caminho: Path | None = None) -> dict:
    p = caminho or CAMINHO
    if not p.exists():
        return {"_meta": {"shadow_version": SHADOW_VERSION,
                          "contract_freeze_commit": CONTRACT_FREEZE_COMMIT,
                          "contract_freeze_iso": CONTRACT_FREEZE_ISO},
                "observacoes": {}}
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        # fail-open: sidecar corrompido não derruba o cron nem apaga o resto
        return {"_meta": {"shadow_version": SHADOW_VERSION,
                          "recuperado_de_corrupcao": True},
                "observacoes": {}}
    if not isinstance(d, dict) or not isinstance(d.get("observacoes"), dict):
        return {"_meta": {"shadow_version": SHADOW_VERSION}, "observacoes": {}}
    return d


def fundir(base: dict, outro: dict) -> dict:
    """União por chave, e o PRIMEIRO resultado vence.

    O oposto do cache de tradução, de propósito: lá a corrida atual vence
    porque o dado é intercambiável; aqui a primeira observação é o dado
    prospectivo válido, e sobrescrevê-la destruiria a medição.
    """
    saida = {"_meta": dict(base.get("_meta") or {}),
             "observacoes": dict(outro.get("observacoes") or {})}
    saida["observacoes"].update(base.get("observacoes") or {})
    for k, v in (outro.get("observacoes") or {}).items():
        if k in (base.get("observacoes") or {}):
            saida["observacoes"][k] = (base["observacoes"])[k]
        else:
            saida["observacoes"][k] = v
    return saida


def gravar(dados: dict, caminho: Path | None = None) -> bool:
    """Escrita atômica. Devolve False quando nada mudou — um cron sem caso
    novo não deve gerar commit só por ter olhado o arquivo."""
    p = caminho or CAMINHO
    novo = json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True)
    if p.exists():
        try:
            if io.open(p, encoding="utf-8").read() == novo:
                return False
        except Exception:
            pass
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    io.open(tmp, "w", encoding="utf-8").write(novo)
    os.replace(tmp, p)
    return True


# ── elegibilidade prospectiva ───────────────────────────────────────────────
def corpus_de_desenvolvimento(caminho="test_fixtures_reliability/"
                                      "pilot1_sample_manifest_v2.json") -> set:
    """URLs que participaram do desenho/avaliação do V2. Ficam de fora."""
    try:
        m = json.load(io.open(caminho, encoding="utf-8"))
    except Exception:
        return set()
    return {i.get("url") for i in m.get("itens", []) if i.get("url")}


def elegivel(artigo: dict, desenvolvimento: set) -> tuple[bool, str]:
    """Prospectivo = capturado DEPOIS do freeze e fora do desenvolvimento."""
    if (artigo.get("url") or "") in desenvolvimento:
        return False, "corpus de desenvolvimento do V2"
    ts = artigo.get("captured_ts") or artigo.get("pub_ts") or 0
    if not ts:
        return False, "sem timestamp — não dá para provar que é pós-freeze"
    if ts <= CONTRACT_FREEZE_TS:
        return False, "anterior ao freeze do Contract V2"
    return True, "prospectivo"


def prioridade(artigo: dict, event_id: str) -> int:
    """Quanto MAIOR, mais cedo entra. Não usa verdade humana."""
    p = 0
    if event_id in FAMILIAS_PRIORITARIAS:
        p += 10
    if event_id in ("ma", "falencia", "recuperacao_judicial"):
        p += 5                      # atribuição a terceiro erra mais aqui
    texto = ((artigo.get("title") or "") + " "
             + (artigo.get("summary") or "")).lower()
    if any(t in texto for t in _PISTAS_NOVIDADE):
        p += 8                      # candidato a follow-up: o que o V2 mudou
    if len(artigo.get("companies") or []) > 1:
        p += 3                      # mais de uma empresa: ambiguidade de sujeito
    return p


def selecionar(artigos: list, historico: dict, ja_observados: set,
               limite: int = MAX_CASOS_POR_RUN) -> list:
    """Casos artigo×empresa novos, ordenados por prioridade e cortados no teto.

    Determinístico: empate desempata por identidade estável, nunca por ordem
    de dicionário, para que o mesmo corpus produza a mesma seleção.
    """
    desenv = corpus_de_desenvolvimento()
    candidatos = []
    for a in artigos:
        ok, _motivo = elegivel(a, desenv)
        if not ok:
            continue
        for empresa, eventos in (a.get("events_by_company") or {}).items():
            for ev in (eventos or []):
                aid = id_artigo(a.get("url") or "", a.get("title") or "")
                if (aid, empresa, ev) in ja_observados:
                    continue
                candidatos.append({"artigo": a, "artigo_id": aid,
                                   "empresa": empresa, "event_id": ev,
                                   "prioridade": prioridade(a, ev)})
    candidatos.sort(key=lambda c: (-c["prioridade"], c["artigo_id"],
                                   c["empresa"], c["event_id"]))
    return candidatos[:limite]


def snapshot_deterministico(artigo: dict, empresa: str, event_id: str) -> dict:
    """O veredito determinístico NO MOMENTO em que o caso entrou no shadow.

    Recalcular meses depois e chamar de comparação prospectiva seria comparar
    o modelo de hoje com um determinístico que já mudou.
    """
    papeis = (artigo.get("mention_roles") or {}).get(empresa) or {}
    return {
        "event_id": event_id,
        "scoreable": event_id in ((artigo.get("events_by_company") or {})
                                  .get(empresa) or []),
        "relation_type": papeis.get("relation_type"),
        "subject_company": papeis.get("subject_company"),
        "impact_type": papeis.get("impact_type"),
        "event_phase": papeis.get("event_phase"),
        "atribuida": empresa in (artigo.get("companies_attributed") or []),
        "contexto": empresa in (artigo.get("context_companies") or []),
        "capturado_em": artigo.get("cap_iso") or "",
    }
