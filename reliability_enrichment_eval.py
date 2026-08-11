#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment_eval.py — 4I.2 R5b §24/§25/§26.

Roda o pipeline semântico ATUAL, sem alterá-lo, sobre CURRENT vs
CURRENT + FRAGMENTO SELECIONADO, e mede o que mudaria.

Mais texto não é automaticamente melhor: eventos que APARECEM são reportados
com o mesmo destaque dos que somem, e um TRUE crítico que desaparecesse seria
alerta grave, não vitória.

Nada é gravado no history.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import reliability_enrichment_sidecar as sc
import reliability_live_audit as la
import risk_dashboard as rd

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR") or "out_reliability")
SAIDA = OUTDIR / "enrichment_eval.json"


def _rodar(cfg, titulo, resumo, empresas):
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "shadow",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": list(empresas)}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    return {c: sorted((rec.get("events_by_company") or {}).get(c) or [])
            for c in empresas}, {
        (d.get("empresa"), d.get("event_id")): (d.get("regra"), d.get("subject_company"))
        for d in (rec.get("semantic_discards") or [])}


def main() -> int:
    cfg = rd.load_config("config_risco.yaml")
    sev = {e["id"]: e.get("severity") for e in (cfg.get("taxonomy") or [])}
    hist = json.load(io.open(sc.HISTORY, encoding="utf-8"))
    side = sc.carregar_sidecar()
    res = la.coletar()
    review = {(l["url"], l["company"], l["event_id"]): l["review_status"]
              for l in res["linhas"]}

    linhas = []
    for ident, reg in side["articles"].items():
        if reg.get("status") != "OK" or not reg.get("selected"):
            continue
        url = next((u for u, r in hist["articles"].items()
                    if (r.get("canonical_url") or u) == ident), None)
        if url is None:
            continue
        rec = hist["articles"][url]
        frag = next(f for f in reg["fragments"]
                    if f["content_hash"] == reg["selected"]["content_hash"])
        empresas = list((rec.get("events_by_company") or {}).keys()) or \
            list(rec.get("companies") or [])
        if not empresas:
            continue
        titulo, resumo = rec.get("title") or "", rec.get("summary") or ""
        at, at_r = _rodar(cfg, titulo, resumo, empresas)
        en, en_r = _rodar(cfg, titulo, f"{resumo} {frag['text_excerpt']}", empresas)
        for c in empresas:
            ap = sorted(set(en[c]) - set(at[c]))
            so = sorted(set(at[c]) - set(en[c]))
            regras = sorted({f"{k[1]}:{v[0]}" for k, v in en_r.items()
                             if k[0] == c and v[0]} -
                            {f"{k[1]}:{v[0]}" for k, v in at_r.items()
                             if k[0] == c and v[0]})
            if not (ap or so or regras):
                continue
            linhas.append({
                "url": url, "company": c, "title": titulo[:120],
                "metodo": reg["selected"]["method"], "tier": reg["selected"]["tier"],
                "aparecem": [(e, sev.get(e)) for e in ap],
                "somem": [(e, sev.get(e)) for e in so],
                "regras_novas": regras,
                "review_dos_que_somem": {e: review.get((url, c, e), "n/a") for e in so},
                "review_dos_que_aparecem": {e: review.get((url, c, e), "UNREVIEWED")
                                            for e in ap},
            })

    OUTDIR.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf-8")

    ap_c = ap_a = so_c = so_a = 0
    true_perdidos = []
    for l in linhas:
        ap_c += sum(1 for _, s in l["aparecem"] if s == "critico")
        ap_a += sum(1 for _, s in l["aparecem"] if s == "alto")
        so_c += sum(1 for _, s in l["somem"] if s == "critico")
        so_a += sum(1 for _, s in l["somem"] if s == "alto")
        for e, st in l["review_dos_que_somem"].items():
            if st == "TRUE":
                true_perdidos.append((l["company"], e, l["title"]))

    print("=" * 100)
    print("SHADOW SEMANTIC EVAL — CURRENT vs CURRENT + FRAGMENTO SELECIONADO")
    print("=" * 100)
    for l in linhas:
        print(f"  {l['company']}  [{l['metodo']} tier{l['tier']}]")
        if l["aparecem"]:
            print(f"     APARECEM {l['aparecem']}  review={l['review_dos_que_aparecem']}")
        if l["somem"]:
            print(f"     SOMEM    {l['somem']}  review={l['review_dos_que_somem']}")
        if l["regras_novas"]:
            print(f"     regras   {l['regras_novas']}")
        print(f"     {l['title'][:92]}")
    print("=" * 100)
    print(f"  artigos avaliados            : "
          f"{sum(1 for r in side['articles'].values() if r.get('status') == 'OK')}")
    print(f"  registros com mudança        : {len(linhas)}")
    print(f"  eventos que APARECEM         : críticos {ap_c} · altos {ap_a}")
    print(f"  eventos que SOMEM            : críticos {so_c} · altos {so_a}")
    print(f"  TRUE críticos perdidos       : {len(true_perdidos)}")
    for t in true_perdidos:
        print(f"     ⚠️  {t}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
