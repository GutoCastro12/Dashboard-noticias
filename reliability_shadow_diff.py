#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_shadow_diff.py — 4I.2 R6c.

Compara, para os artigos que têm evidência no side-car, a decisão de PRODUÇÃO
com a decisão SHADOW — a semântica de papel que ainda não está ativa.

É observabilidade, não ativação: produção continua classificando com o input
atual e a semântica publicada. Este relatório existe para que a diferença
entre as duas seja visível ANTES de qualquer decisão de ligar a nova
semântica, e não descoberta depois.

Uso:
    python reliability_shadow_diff.py
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
SAIDA = OUTDIR / "shadow_diff.json"

CLASSES = ("IGUAL", "SHADOW_REMOVE_EVENTO", "SHADOW_ADICIONA_EVENTO",
           "SHADOW_MUDA_REGRA", "SHADOW_MUDA_SUJEITO")


def _decidir(cfg, titulo, resumo, empresas, shadow: bool):
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "shadow",
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
    det = {(d.get("empresa"), d.get("event_id")): (d.get("regra") or "",
                                                   d.get("subject_company") or "")
           for d in (r.get("semantic_discards") or [])}
    return ev, det


def classificar(ev_p, det_p, ev_s, det_s, c) -> str:
    if set(ev_s[c]) - set(ev_p[c]):
        return "SHADOW_ADICIONA_EVENTO"
    if set(ev_p[c]) - set(ev_s[c]):
        return "SHADOW_REMOVE_EVENTO"
    rp = {k[1]: v for k, v in det_p.items() if k[0] == c}
    rs = {k[1]: v for k, v in det_s.items() if k[0] == c}
    if {e: v[1] for e, v in rp.items()} != {e: v[1] for e, v in rs.items()}:
        return "SHADOW_MUDA_SUJEITO"
    if {e: v[0] for e, v in rp.items()} != {e: v[0] for e, v in rs.items()}:
        return "SHADOW_MUDA_REGRA"
    return "IGUAL"


def main() -> int:
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(sc.HISTORY, encoding="utf-8"))
    side = sc.carregar_sidecar()
    linhas = []
    for ident, reg in (side.get("articles") or {}).items():
        if reg.get("status") != "OK" or not reg.get("selected"):
            continue
        url = next((u for u, r in hist["articles"].items()
                    if (r.get("canonical_url") or u) == ident), None)
        if url is None:
            continue
        rec = hist["articles"][url]
        frags = [f for f in reg["fragments"]
                 if f["content_hash"] == reg["selected"]["content_hash"]]
        empresas = list((rec.get("events_by_company") or {}).keys()) or \
            list(rec.get("companies") or [])
        if not empresas or not frags:
            continue
        titulo, resumo = rec.get("title") or "", rec.get("summary") or ""
        enr = f"{resumo} " + " ".join(f["text_excerpt"] for f in frags)
        ev_p, det_p = _decidir(cfg, titulo, resumo, empresas, False)
        ev_s, det_s = _decidir(cfg, titulo, enr, empresas, True)
        for c in empresas:
            classe = classificar(ev_p, det_p, ev_s, det_s, c)
            for e in sorted(set(ev_p[c]) | set(ev_s[c]) |
                            {k[1] for k in det_p if k[0] == c} |
                            {k[1] for k in det_s if k[0] == c}):
                linhas.append({
                    "identity": ident, "company": c, "event": e,
                    "production_decision": "scoreable" if e in ev_p[c] else "nao_scoreable",
                    "shadow_decision": "scoreable" if e in ev_s[c] else "nao_scoreable",
                    "production_rule": det_p.get((c, e), ("", ""))[0],
                    "shadow_rule": det_s.get((c, e), ("", ""))[0],
                    "subject_production": det_p.get((c, e), ("", ""))[1],
                    "subject_shadow": det_s.get((c, e), ("", ""))[1],
                    "evidence_span": (reg["selected"].get("selection_reason") or "")[:120],
                    "fragment_method": reg["selected"]["method"],
                    "fragment_tier": reg["selected"]["tier"],
                    "schema_version": reg.get("schema_version"),
                    "extractor_version": reg.get("extractor_version"),
                    "classe": classe,
                })
    OUTDIR.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf-8")

    cont = {k: sum(1 for l in linhas if l["classe"] == k) for k in CLASSES}
    print("=" * 100)
    print("SHADOW DIFF — PRODUÇÃO vs SEMÂNTICA DE PAPEL (não ativada)")
    print("=" * 100)
    for l in linhas:
        if l["classe"] == "IGUAL":
            continue
        print(f"  [{l['classe']}] {l['company']}/{l['event']}")
        print(f"     produção : {l['production_decision']:14s} regra={l['production_rule'] or '-'}")
        print(f"     shadow   : {l['shadow_decision']:14s} regra={l['shadow_rule'] or '-'}")
        print(f"     fragmento: {l['fragment_method']} (tier {l['fragment_tier']}) "
              f"schema {l['schema_version']} / {l['extractor_version']}")
    print("=" * 100)
    for k in CLASSES:
        print(f"  {k:26s} {cont[k]}")
    print(f"  pares avaliados            {len(linhas)}")
    print(f"  relatório                  {SAIDA}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
