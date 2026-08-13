#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot1_payloads.py — R7b piloto 1: payloads, cache, mock, comparação.

NENHUMA CHAMADA A PROVIDER. Este módulo constrói exatamente o que seria enviado,
prova que não vaza o que não pode vazar, e roda a cadeia inteira com respostas
FALSAS para mostrar que o encanamento funciona antes de gastar um token.

ARQUITETURA — ARCH-A, duas calls:

  AUDIT      empresa×artigo. Recebe empresa, aliases e os event_ids candidatos,
             porque essa É a tarefa: avaliar um candidato conhecido.
  DISCOVERY  artigo. NÃO recebe empresa, candidato, regra, score nem watchlist.
             UM ARTIGO = UMA CALL, mesmo com três monitoradas dentro; o
             casamento com a watchlist é local, depois.

O QUE NUNCA ENTRA EM PAYLOAD NENHUM: verdade humana, rótulo esperado, nota de
revisor e o veredito determinístico. O modelo não pode ser avaliado com a
resposta no bolso.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

import reliability_pilot_contract as pc
import reliability_pilot1_sample as ps
import reliability_pilot_validators as pv
import risk_dashboard as rd

PILOT_VERSION = "r7b.pilot1"
AUDIT_TASK_VERSION = "r7b.pilot1.audit.v1"
DISCOVERY_TASK_VERSION = "r7b.pilot1.discovery.v1"
CACHE_VERSION = "r7b.pilot1.cache.v1"

OUTDIR = ps.OUTDIR
PAYLOADS = OUTDIR / "pilot1_payloads.json"
CACHE = OUTDIR / "pilot1_llm_cache.json"
MOCKOUT = OUTDIR / "pilot1_mock_run.json"

# Chaves cuja simples presença num payload significa vazamento de avaliação.
CHAVES_PROIBIDAS = ("evaluation_only", "human_truth", "human_label",
                    "human_scoreable", "deterministic", "reviewer_type",
                    "failure_dimension", "s3_family", "rule", "selection_reason",
                    "input_track", "role", "stratum")
# Conteúdo que jamais pode aparecer, mesmo dentro de um valor de string.
CONTEUDO_PROIBIDO = ("FALSE_POSITIVE", "DEV_CONTROL", "HOLDOUT",
                     "R_MA_", "R_PAPEL_", "R_EVENTO_", "R_NEGACAO_",
                     "scoreable", "ground truth", "verdade humana")


def _modelo(cfg: dict) -> str:
    return (cfg.get("llm") or {}).get("model") or "gemini-3-flash"


# ── construção ──────────────────────────────────────────────────────────────
def construir_payloads(man: dict, cfg: dict) -> dict:
    al = pc and None  # placeholder legível; aliases vêm do config abaixo
    import semantic_audit as sa
    aliases = sa._aliases_map(cfg)
    model = _modelo(cfg)

    audits, discoveries, artigos_vistos = [], [], set()
    for item in man["itens"]:
        texto = item["input"]["texto"]
        if not texto:
            continue
        genero = pc.genero_da_fonte(item.get("domain") or "")
        pub = item.get("pub_iso") or ""

        # AUDIT — só faz sentido com empresa e candidato
        if item["company"] and item["candidate_events"]:
            p = pc.payload_audit(
                texto=texto, organizacao=item["company"],
                aliases=list(aliases.get(item["company"]) or []),
                event_ids=list(item["candidate_events"]),
                pub_iso=pub, genero=genero)
            audits.append({
                "sample_id": item["sample_id"],
                "article_id": item["article_id"],
                "call_type": pc.CALL_AUDIT,
                "task_version": AUDIT_TASK_VERSION,
                "cache_key": chave_de_cache(
                    pc.CALL_AUDIT, texto, model,
                    extra=f"{item['company']}|{','.join(item['candidate_events'])}"),
                "payload": p,
            })

        # DISCOVERY — UM por ARTIGO, cega
        if item["article_id"] not in artigos_vistos:
            artigos_vistos.add(item["article_id"])
            p = pc.payload_discovery(texto=texto, pub_iso=pub, genero=genero)
            discoveries.append({
                "sample_id": item["sample_id"],
                "article_id": item["article_id"],
                "call_type": pc.CALL_DISCOVERY,
                "task_version": DISCOVERY_TASK_VERSION,
                "cache_key": chave_de_cache(pc.CALL_DISCOVERY, texto, model),
                "payload": p,
            })

    return {
        "_meta": {
            "pilot_version": PILOT_VERSION,
            "arquitetura": pc.ARCH_A,
            "audit_task_version": AUDIT_TASK_VERSION,
            "discovery_task_version": DISCOVERY_TASK_VERSION,
            "cache_version": CACHE_VERSION,
            **pc.versoes(model),
            "sample_version": man["_meta"]["sample_version"],
            "input_version": man["_meta"]["input_version"],
        },
        "audit": audits,
        "discovery": discoveries,
    }


def chave_de_cache(call_type: str, texto: str, model: str, extra: str = "") -> str:
    """Sete componentes do contrato + a versão da TAREFA do piloto.

    Sem o `task_version`, uma resposta gerada com o prompt do piloto 1 seria
    reutilizada em silêncio pelo piloto 2 com prompt diferente — que é
    exatamente o modo de falha que cache versionado existe para impedir.
    """
    base = pc.identidade_de_cache(call_type=call_type, texto=texto,
                                  model=model, extra=extra)
    tarefa = (AUDIT_TASK_VERSION if call_type == pc.CALL_AUDIT
              else DISCOVERY_TASK_VERSION)
    return hashlib.sha256(f"{CACHE_VERSION}|{tarefa}|{base}".encode()).hexdigest()


# ── vazamento ───────────────────────────────────────────────────────────────
def _achatar(o, acc=None):
    acc = [] if acc is None else acc
    if isinstance(o, dict):
        for k, v in o.items():
            acc.append(("KEY", str(k)))
            _achatar(v, acc)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _achatar(v, acc)
    elif o is not None:
        acc.append(("VAL", str(o)))
    return acc


def auditar_vazamento(entrada: dict, *, texto_do_artigo: str,
                      cego_a_empresa: bool, empresa: str = "") -> dict:
    """Um payload só é aceitável se nada NOSSO vazar para dentro dele.

    O texto do artigo é removido antes de qualquer checagem — inclusive de
    dentro do prompt, onde ele vai embutido. Duas armadilhas que isso evita:

    1. uma notícia que cita "Carteira Valor" no menu do site reprovaria o
       payload por "vocabulário de negócio";
    2. o nome da empresa monitorada aparece no corpo da notícia porque a
       notícia É sobre ela. Isso NÃO é quebra de cegueira: o DISCOVERY existe
       justamente para ler o artigo e descobrir de quem se trata. Vazamento
       seria NÓS dizermos qual empresa olhar — e é isso que se testa aqui,
       fora do texto.
    """
    sem_artigo = pc._sem_o_artigo(entrada["payload"], texto_do_artigo)
    partes = _achatar(sem_artigo)
    chaves = {v for t, v in _achatar(entrada["payload"]) if t == "KEY"}
    valores = " ".join(v for t, v in partes
                       if t == "VAL" and v != texto_do_artigo)

    problemas = []
    for k in CHAVES_PROIBIDAS:
        if k in chaves:
            problemas.append(f"chave proibida: {k}")
    baixo = valores.lower()
    for c in CONTEUDO_PROIBIDO:
        if c.lower() in baixo:
            problemas.append(f"conteúdo proibido: {c}")
    for t in pc.checar_payload(sem_artigo, texto_do_artigo=texto_do_artigo):
        problemas.append(f"termo de negócio: {t}")
    if cego_a_empresa and empresa:
        # fronteira de palavra, não substring: "PRIO" casa dentro de
        # "próprio"/"prioridade" e acusaria quebra de cegueira que não existe.
        # Mesma armadilha do "underscore" que continha "score", na R7a.
        if re.search(r"\b" + re.escape(empresa.lower()) + r"\b", baixo):
            problemas.append(f"DISCOVERY recebeu a empresa monitorada fora do "
                             f"texto do artigo: {empresa}")
    return {"ok": not problemas, "problemas": problemas}


# ── respostas FALSAS, marcadas como tal ─────────────────────────────────────
# Não são resultados de modelo. Existem para exercitar validador, cache e
# comparação antes de gastar um token — e para provar que resposta inválida
# vira estado registrado, não exceção que derruba a corrida.
MOCK_AUDIT_OK = {
    "__MOCK__": True,
    "events": [{
        "event_id": "ma", "event_asserted": "ASSERTED",
        "event_support": "SUPPORTED", "event_quote": "",
        "subject": "", "subject_basis": "EXPLICIT", "subject_quote": "",
        "company_role": "SELLER", "role_quote": "",
        "relation_type": "", "relation_quote": "",
        "currentness": "CURRENT", "phase": "CONCLUDED",
        "centrality": "MAIN", "scope": "ASSET",
        "transaction_role": "SELLER", "transaction_object": "ASSET",
        "field_support": "SUPPORTED",
        "semantic_scoreable_for_evaluation": False,
        "reason": "a organizacao aparece como parte vendedora",
    }],
}
MOCK_AUDIT_ABSTAIN = {
    "__MOCK__": True,
    "events": [{"event_id": "ma", "event_asserted": "UNCLEAR",
                "event_support": "INSUFFICIENT_INPUT", "event_quote": "",
                "abstain_reason": "apenas titulo disponivel",
                "semantic_scoreable_for_evaluation": None}],
}
MOCK_AUDIT_QUOTE_INVALIDA = {
    "__MOCK__": True,
    "events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                "event_support": "SUPPORTED",
                "event_quote": "esta frase nao existe no artigo em lugar nenhum",
                "semantic_scoreable_for_evaluation": True}],
}
MOCK_JSON_INVALIDO = '{"events": [ {"event_id": "ma" '
#  e a chave do SCHEMA_DISCOVERY do contrato — os mocks seguem o
# schema real, nao um inventado, senao o teste do validador vira teatro.
MOCK_DISCOVERY_VAZIO = {
    "__MOCK__": True,
    "events": [],
    "abstain_reason": "nenhum evento materialmente relevante para risco",
}
MOCK_DISCOVERY_NOVEL = {
    "__MOCK__": True,
    "events": [{
        "organization": "Empresa Alfa",
        "event_description": "interrupcao de fornecimento por terceiro",
        "risk_channel": "cadeia_suprimentos",
        "currentness": "CURRENT",
        "centrality": "MAIN",
        "field_support": "SUPPORTED",
        "evidence_quote": "",
        "novel_event_candidate": True,
    }],
}


def _com_quote_real(mock: dict, texto: str) -> dict:
    """Ancora a quote do mock num trecho REAL do input — sem isto o teste do
    validador de evidência não distinguiria 'quote inválida' de 'mock mal
    montado'."""
    m = json.loads(json.dumps(mock))
    trecho = " ".join(texto.split()[:8])
    for e in m.get("events", []):
        if "event_quote" in e and not e["event_quote"]:
            e["event_quote"] = trecho
    for e in m.get("discovered_events", []):
        if not e.get("evidence_quote"):
            e["evidence_quote"] = trecho
    return m


def rodar_mock(pl: dict, man: dict) -> dict:
    """Cadeia completa com respostas falsas: cache -> validação -> comparação."""
    porid = {i["sample_id"]: i for i in man["itens"]}
    cache, linhas = {}, []
    contagem = collections.Counter()

    for i, ent in enumerate(pl["audit"]):
        item = porid[ent["sample_id"]]
        texto = item["input"]["texto"]
        if i % 7 == 3:
            bruto, rotulo = MOCK_JSON_INVALIDO, "json_invalido"
        elif i % 7 == 5:
            bruto, rotulo = MOCK_AUDIT_QUOTE_INVALIDA, "quote_invalida"
        elif item["input_track"] == ps.DEGRADED and i % 3 == 0:
            bruto, rotulo = MOCK_AUDIT_ABSTAIN, "abstencao"
        else:
            bruto, rotulo = _com_quote_real(MOCK_AUDIT_OK, texto), "ok"

        estado, saida = "OK", None
        if isinstance(bruto, str):
            try:
                saida = json.loads(bruto)
            except Exception:
                estado = "JSON_INVALIDO"
        else:
            saida = bruto
        val = {}
        if estado == "OK":
            try:
                val = pv.validar_audit(saida, texto=texto,
                                       organizacao=item["company"],
                                       event_ids=item["candidate_events"])
            except Exception as exc:
                val = {"ok": False, "erro": str(exc)[:80]}
            if not val.get("ok", True):
                estado = "EVIDENCIA_INVALIDA"
        cache[ent["cache_key"]] = {"mock": True, "estado": estado}
        contagem["audit:" + estado] += 1
        linhas.append(comparar(item, saida, estado, rotulo, pc.CALL_AUDIT, val))

    for i, ent in enumerate(pl["discovery"]):
        item = porid[ent["sample_id"]]
        texto = item["input"]["texto"]
        bruto = (MOCK_DISCOVERY_VAZIO if item["stratum"] == "S6"
                 else _com_quote_real(MOCK_DISCOVERY_NOVEL, texto))
        try:
            val = pv.validar_discovery(bruto, texto=texto)
        except Exception as exc:
            val = {"ok": False, "erro": str(exc)[:80]}
        estado = "OK" if val.get("ok", True) else "EVIDENCIA_INVALIDA"
        cache[ent["cache_key"]] = {"mock": True, "estado": estado}
        contagem["discovery:" + estado] += 1
        linhas.append(comparar(item, bruto, estado, "mock", pc.CALL_DISCOVERY, val))

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"_meta": {"cache_version": CACHE_VERSION, "MOCK": True,
                         "aviso": "respostas FALSAS; nenhum provider foi chamado"},
               "entradas": cache},
              io.open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"linhas": linhas, "contagem": dict(contagem),
            "chaves_de_cache_unicas": len(cache)}


# ── comparação semântica ────────────────────────────────────────────────────
AGREE, D_ONLY, L_ONLY, CONFLICT, BOTH_ABSTAIN = (
    "AGREE", "D_ONLY", "L_ONLY", "CONFLICT", "BOTH_ABSTAIN")


def comparar(item: dict, saida, estado: str, rotulo: str,
             call_type: str, validacao: dict) -> dict:
    """Determinístico x LLM, com a verdade humana ao lado — nunca dentro.

    A comparação NÃO é score e o LLM NÃO é verdade: quando existe verdade
    humana, ela é soberana para avaliação; quando não existe, o par fica
    registrado como divergência a investigar, não como erro de alguém.
    """
    det = (item["evaluation_only"].get("deterministic") or {})
    d_pos = det.get("scoreable")
    l_pos = None
    if estado == "OK" and isinstance(saida, dict):
        evs = saida.get("events") or []
        if evs:
            l_pos = evs[0].get("semantic_scoreable_for_evaluation")

    if estado != "OK":
        status = "LLM_INVALIDO"
    elif d_pos is None and l_pos is None:
        status = BOTH_ABSTAIN
    elif l_pos is None:
        status = D_ONLY if d_pos else BOTH_ABSTAIN
    elif d_pos is None:
        status = L_ONLY if l_pos else BOTH_ABSTAIN
    elif bool(d_pos) == bool(l_pos):
        status = AGREE
    else:
        status = CONFLICT

    gt = item["evaluation_only"].get("human_truth")
    return {
        "sample_id": item["sample_id"], "call_type": call_type,
        "stratum": item["stratum"], "input_track": item["input_track"],
        "role": item["role"], "mock_variant": rotulo, "estado": estado,
        "deterministic_scoreable": d_pos,
        "llm_scoreable_for_evaluation": l_pos,
        "comparison_status": status,
        "human_truth_available": bool(gt),
        "human_scoreable": (gt or {}).get("human_scoreable"),
        "evidence_valid": validacao.get("ok") if validacao else None,
    }


def main() -> int:
    cfg = rd.load_config("config_risco.yaml")
    man = json.load(io.open(ps.MANIFESTO, encoding="utf-8"))
    pl = construir_payloads(man, cfg)
    porid = {i["sample_id"]: i for i in man["itens"]}

    falhas = []
    for ent in pl["audit"]:
        it = porid[ent["sample_id"]]
        r = auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                              cego_a_empresa=False)
        if not r["ok"]:
            falhas.append((ent["sample_id"], "AUDIT", r["problemas"]))
    for ent in pl["discovery"]:
        it = porid[ent["sample_id"]]
        r = auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                              cego_a_empresa=True, empresa=it["company"] or "")
        if not r["ok"]:
            falhas.append((ent["sample_id"], "DISCOVERY", r["problemas"]))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    json.dump(pl, io.open(PAYLOADS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    mock = rodar_mock(pl, man)
    json.dump({"_meta": {"MOCK": True, "pilot_version": PILOT_VERSION,
                         "aviso": "nenhuma chamada a provider foi feita"},
               **mock}, io.open(MOCKOUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("=" * 96)
    print("PILOT-1 PAYLOADS + MOCK — " + PILOT_VERSION + " (" + pc.ARCH_A + ")")
    print("=" * 96)
    print("  AUDIT payloads     : %d" % len(pl["audit"]))
    print("  DISCOVERY payloads : %d" % len(pl["discovery"]))
    print("  chaves de cache unicas: %d" % mock["chaves_de_cache_unicas"])
    print("  vazamento: %s" % ("NENHUM" if not falhas else falhas))
    print("  mock: %s" % mock["contagem"])
    st = collections.Counter(l["comparison_status"] for l in mock["linhas"])
    print("  comparison_status: %s" % dict(st))
    print("  payloads -> %s" % PAYLOADS)
    print("  mock     -> %s" % MOCKOUT)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
