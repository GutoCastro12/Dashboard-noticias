#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_fraud_activation_rehearsal.py — 4I.2 R6e.

Ensaio de ativação, sem ativar nada. Responde: se o enrichment passasse a ser
consumido prospectivamente PARA FRAUDE, o que mudaria — e por quê.

A R6d mostrou que essas são duas perguntas diferentes, não uma. O caso CVS
muda de decisão só por receber mais texto, com a semântica publicada; o caso
Duke só muda pela razão certa quando a semântica de papel também entra. Por
isso o ensaio calcula TRÊS modos e nunca os soma:

  P   input atual        + semântica publicada     (o que existe hoje)
  I   input enriquecido  + semântica publicada     (ganho/risco do INPUT)
  IS  input enriquecido  + semântica shadow        (INPUT + SEMÂNTICA)

Escopo: `fraude`. Nenhum outro evento consome enrichment aqui.

FAIL-CLOSED: sem enrichment limpo e completo, o candidato é P. Ausência de
contexto nunca piora a decisão.

Este runner NÃO é chamado pelo workflow e não escreve em history, side-car de
produção, score ou dashboard.

Uso:
    python reliability_fraud_activation_rehearsal.py
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import reliability_enrichment_sidecar as sc
import risk_dashboard as rd
import semantic_audit as sa

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR") or "out_reliability")
SAIDA = OUTDIR / "fraud_activation_rehearsal.json"
REVIEWS = Path("test_fixtures_reliability/live_reviews.json")
SHADOW_REVIEWS = Path("test_fixtures_reliability/shadow_reviews.json")
ARTEFATO_LOCAL = OUTDIR / "enrichment_shadow.json"

EVENTO = "fraude"


def _classificar(cfg, titulo, resumo, empresas, shadow):
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "rehearsal",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": list(empresas)}}, "run_count": 1}
    if shadow:
        with sa.shadow_fraud_roles():
            rd._reclassify_only_pass(h, cfg)
    else:
        rd._reclassify_only_pass(h, cfg)
    r = h["articles"]["u1"]
    ev = {c: sorted((r.get("events_by_company") or {}).get(c) or []) for c in empresas}
    reg = {(d.get("empresa"), d.get("event_id")): (d.get("regra") or "")
           for d in (r.get("semantic_discards") or [])}
    return ev, reg


def _fragmentos(reg_side, base):
    """Fragmentos do side-car de produção OU do artefato local da R5a."""
    if reg_side.get("fragments"):
        return reg_side["fragments"]
    return [{"method": e["extraction_method"],
             "tier": sc.TIER.get(e["extraction_method"], (2, "?"))[0],
             "text_excerpt": e["text"][:sc.MAX_EXCERPT],
             "content_hash": e.get("content_hash") or str(abs(hash(e["text"])))[:16],
             **sc.qualidade(e["text"], base)}
            for e in (reg_side.get("enrichment") or [])]


def _delta_source(p, i, s):
    if p == i == s:
        return "NO_CHANGE"
    if p != i and i == s:
        return "INPUT_ONLY"
    if p == i and i != s:
        return "SEMANTICS_ONLY"
    return "INPUT_AND_SEMANTICS"


def main() -> int:
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(sc.HISTORY, encoding="utf-8"))
    side = sc.carregar_sidecar()
    revs = json.load(io.open(REVIEWS, encoding="utf-8")) if REVIEWS.exists() else {}
    srev = (json.load(io.open(SHADOW_REVIEWS, encoding="utf-8"))
            if SHADOW_REVIEWS.exists() else {})
    locais = {}
    if ARTEFATO_LOCAL.exists():
        for r in json.load(io.open(ARTEFATO_LOCAL, encoding="utf-8"))["registros"]:
            locais[r.get("title") or ""] = r

    linhas, gate = [], {"fraud_candidate": 0, "elegivel": 0, "enrichment_ok": 0,
                        "evidence_complete": 0, "fallback_P": 0}
    for url, rec in hist["articles"].items():
        if EVENTO not in (rec.get("event_ids") or []):
            continue
        gate["fraud_candidate"] += 1
        ident = rec.get("canonical_url") or url
        reg = (side.get("articles") or {}).get(ident) or locais.get(rec.get("title") or "")
        titulo, resumo = rec.get("title") or "", rec.get("summary") or ""
        empresas = list((rec.get("events_by_company") or {}).keys()) or \
            list(rec.get("companies") or [])
        if not empresas:
            continue
        gate["elegivel"] += 1
        base = f"{titulo}. {resumo}"
        frags = _fragmentos(reg, base) if reg else []
        status = (reg or {}).get("status") or (reg or {}).get("quality", {}).get("reason")
        sel, motivo = (sc.selecionar_evidencias(frags, base, empresas[0], EVENTO,
                                                sa._aliases_map(cfg).get(empresas[0]))
                       if frags else ([], "sem fragmento"))
        ev_p, reg_p = _classificar(cfg, titulo, resumo, empresas, False)
        if not sel:
            # FAIL-CLOSED: sem enrichment limpo, o candidato é a produção atual.
            gate["fallback_P"] += 1
            ev_i, reg_i, ev_s, reg_s = ev_p, reg_p, ev_p, reg_p
        else:
            gate["enrichment_ok"] += 1
            enr = resumo + " " + " ".join(f["text_excerpt"] for f in sel)
            ev_i, reg_i = _classificar(cfg, titulo, enr, empresas, False)
            ev_s, reg_s = _classificar(cfg, titulo, enr, empresas, True)
            if not sc.papel_do_evento_indefinido(f"{titulo}. {enr}", empresas[0],
                                                 EVENTO,
                                                 sa._aliases_map(cfg).get(empresas[0])):
                gate["evidence_complete"] += 1
        for c in empresas:
            p = EVENTO in ev_p[c]
            i = EVENTO in ev_i[c]
            s = EVENTO in ev_s[c]
            k = f"{url}||{c}||{EVENTO}"
            gt = (revs.get(k) or {}).get("status") or \
                 next((v.get("status") for kk, v in srev.items()
                       if kk != "_meta" and v.get("company") == c
                       and v.get("event_id") == EVENTO), "UNREVIEWED")
            linhas.append({
                "url": url, "company": c, "event": EVENTO, "title": titulo[:110],
                "P": "scoreable" if p else "nao", "I": "scoreable" if i else "nao",
                "IS": "scoreable" if s else "nao",
                "rule_P": reg_p.get((c, EVENTO), ""), "rule_I": reg_i.get((c, EVENTO), ""),
                "rule_IS": reg_s.get((c, EVENTO), ""),
                "ground_truth": gt, "delta_source": _delta_source(p, i, s),
                "enrichment_status": status or "SEM_ENRICHMENT",
                "fragments": [f["method"] for f in sel],
            })

    OUTDIR.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps({"gate": gate, "linhas": linhas},
                                ensure_ascii=False, indent=1), encoding="utf-8")

    print("=" * 108)
    print("FRAUD ACTIVATION REHEARSAL — P (hoje) · I (input) · IS (input+semântica)")
    print("=" * 108)
    for l in linhas:
        if l["delta_source"] == "NO_CHANGE":
            continue
        print(f"  [{l['delta_source']:20s}] {l['company']}/{l['event']}  gt={l['ground_truth']}")
        print(f"     P ={l['P']:10s} regra={l['rule_P'] or '-'}")
        print(f"     I ={l['I']:10s} regra={l['rule_I'] or '-'}")
        print(f"     IS={l['IS']:10s} regra={l['rule_IS'] or '-'}   frags={l['fragments']}")
        print(f"     {l['title'][:92]}")
    print("=" * 108)
    print("  GATE DE ATIVAÇÃO (FRAUD-ENRICHED-PROSPECTIVE-V1)")
    for k, v in gate.items():
        print(f"    {k:20s} {v}")
    from collections import Counter
    print("  DELTA SOURCE:", dict(Counter(l["delta_source"] for l in linhas)))
    for modo in ("P", "I", "IS"):
        t = sum(1 for l in linhas if l[modo] == "scoreable" and l["ground_truth"] == "TRUE")
        f = sum(1 for l in linhas if l[modo] == "scoreable"
                and l["ground_truth"] == "FALSE_POSITIVE")
        den = t + f
        print(f"    precisão {modo:2s} (revisados): "
              + (f"{t}/{den} = {t / den * 100:.1f}%" if den else "N/A — denominador insuficiente"))
    for modo in ("I", "IS"):
        des = sum(1 for l in linhas if l[modo] != l["P"]
                  and ((l["ground_truth"] == "FALSE_POSITIVE" and l[modo] == "nao")
                       or (l["ground_truth"] == "TRUE" and l[modo] == "scoreable")))
        ind = sum(1 for l in linhas if l[modo] != l["P"]
                  and ((l["ground_truth"] == "TRUE" and l[modo] == "nao")
                       or (l["ground_truth"] == "FALSE_POSITIVE" and l[modo] == "scoreable")))
        unrev = sum(1 for l in linhas if l[modo] != l["P"]
                    and l["ground_truth"] == "UNREVIEWED")
        print(f"    {modo}: desejadas {des} · indesejadas {ind} · não revisadas {unrev} "
              f"· ganho líquido revisado {des - ind}")
    # §29 — output igual não significa razão igual. Um falso positivo que cai
    # por "fase não confirmada" volta assim que a fraude for confirmada; o que
    # cai por papel não volta. É essa diferença que o ensaio precisa expor.
    RAZOES_DE_PAPEL = {"R_FRAUDE_ATOR_EXTERNO", "R_FRAUDE_VITIMA_DETECTORA",
                       "R_FRAUDE_PREJUIZO_DE_TERCEIRO", "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE",
                       "R_LIABILITY_DE_TERCEIRO", "R_LIABILITY_VENCE_RESOLUCAO"}
    print("  CAUSALIDADE (só entre FALSE_POSITIVE corretamente removidos):")
    for modo, campo in (("I", "rule_I"), ("IS", "rule_IS")):
        certos = [l for l in linhas if l["ground_truth"] == "FALSE_POSITIVE"
                  and l[modo] == "nao"]
        bons = [l for l in certos if l[campo] in RAZOES_DE_PAPEL]
        print(f"    {modo}: output correto {len(certos)} · "
              f"razão correta {len(bons)} · razão frágil {len(certos) - len(bons)}")
    print(f"  relatório: {SAIDA}")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
