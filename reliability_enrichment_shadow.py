#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment_shadow.py — 4I.2 R5a §18/§19/§20.

Roda o pipeline semântico ATUAL, sem alterá-lo, sobre dois inputs:

  CURRENT   title + summary como estão persistidos
  ENRICHED  title + summary + o melhor excerto obtido pelo enrichment

e mostra o que MUDARIA se o texto extra estivesse disponível hoje. É medição,
não proposta: nenhuma regra é criada e nada é gravado no history.

Mais texto não é automaticamente melhor. O relatório separa eventos que
APARECEM, que SOMEM e que MUDAM DE SUJEITO — o risco de falso positivo é
reportado com o mesmo peso do ganho.

Uso:
    python reliability_enrichment_shadow.py
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import reliability_enrichment as enr
import risk_dashboard as rd

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR") or "out_reliability")
SHADOW = OUTDIR / "enrichment_shadow.json"
SAIDA = OUTDIR / "enrichment_semantic_diff.json"


def _rodar(cfg: dict, titulo: str, resumo: str, empresa: str) -> dict:
    h = {"articles": {"u1": {"title": titulo, "summary": resumo,
                             "source": "shadow", "domain": "exemplo.com",
                             "pub_ts": 1786000000, "pub_iso": "2026-08-07 04:00",
                             "companies": [empresa]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    return {
        "eventos": sorted((rec.get("events_by_company") or {}).get(empresa) or []),
        "regras": sorted({d.get("regra") for d in (rec.get("semantic_discards") or [])
                          if d.get("empresa") == empresa and d.get("regra")}),
        "sujeitos": sorted({d.get("subject_company") or ""
                            for d in (rec.get("semantic_discards") or [])
                            if d.get("empresa") == empresa}),
    }


def main() -> int:
    cfg = rd.load_config("config_risco.yaml")
    dados = json.load(io.open(SHADOW, encoding="utf-8"))
    linhas = []
    for r in dados["registros"]:
        m = enr.melhor(r)
        if not m:
            linhas.append({"grupo": r["grupo"], "company": r["company"],
                           "event_id": r["event_id"], "title": r["title"],
                           "review": r["review"], "enriquecido": False,
                           "motivo": r["quality"].get("reason")})
            continue
        atual = _rodar(cfg, r["title"], r["current_summary"], r["company"])
        enriq = _rodar(cfg, r["title"],
                       f"{r['current_summary']} {m['text']}", r["company"])
        linhas.append({
            "grupo": r["grupo"], "company": r["company"], "event_id": r["event_id"],
            "title": r["title"], "review": r["review"], "enriquecido": True,
            "metodo": m["extraction_method"], "tokens_novos": m["effective_new_tokens"],
            "boilerplate": m["boilerplate"],
            "atual": atual, "enriquecido_result": enriq,
            "aparecem": sorted(set(enriq["eventos"]) - set(atual["eventos"])),
            "somem": sorted(set(atual["eventos"]) - set(enriq["eventos"])),
            "regras_novas": sorted(set(enriq["regras"]) - set(atual["regras"])),
            "sujeito_mudou": atual["sujeitos"] != enriq["sujeitos"],
        })

    OUTDIR.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf-8")

    print("=" * 100)
    print("SHADOW SEMANTIC DIFF — CURRENT INPUT vs ENRICHED INPUT")
    print("=" * 100)
    ap = so = suj = 0
    for l in linhas:
        if not l["enriquecido"]:
            print(f"  [{l['grupo']:14s}] sem enriquecimento ({l['motivo']}) :: {l['title'][:52]}")
            continue
        ap += len(l["aparecem"])
        so += len(l["somem"])
        suj += bool(l["sujeito_mudou"])
        marca = "  " if not (l["aparecem"] or l["somem"]) else "≠ "
        print(f"{marca}[{l['grupo']:14s}] {l['company']}/{l['event_id']}  "
              f"({l['review']}, +{l['tokens_novos']} tokens, {l['metodo']})")
        print(f"     atual      {l['atual']['eventos']}  regras={l['atual']['regras']}")
        print(f"     enriquecido{l['enriquecido_result']['eventos']}  "
              f"regras={l['enriquecido_result']['regras']}")
        if l["aparecem"]:
            print(f"     APARECEM: {l['aparecem']}")
        if l["somem"]:
            print(f"     SOMEM   : {l['somem']}")
        if l["boilerplate"]:
            print(f"     boilerplate: {l['boilerplate']}")
    print("=" * 100)
    n = sum(1 for l in linhas if l["enriquecido"])
    print(f"  artigos com enriquecimento : {n}/{len(linhas)}")
    print(f"  eventos que APARECEM       : {ap}")
    print(f"  eventos que SOMEM          : {so}")
    print(f"  registros com mudança de sujeito: {suj}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
