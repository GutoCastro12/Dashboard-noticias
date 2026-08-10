#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_generalization.py — 4I.2 Reliability Learning Loop (R1).

Mede se o runtime APRENDEU A CLASSE do erro, não apenas o caso.

Contrato (§4/§6 do brief R1):
  EXACT REGRESSION  != GENERALIZATION
  Uma família só é GENERALIZED_ON_TEST_SET com exact=100%, siblings=100%
  e negatives=100%. Passar só nas exact regressions é memorização.

Cada fixture passa pelo MESMO pipeline semântico de produção
(`_reclassify_only_pass`) — não há segunda implementação da semântica aqui.

Uso:
    python reliability_generalization.py            # relatório
    python reliability_generalization.py --json     # saída legível por máquina
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import risk_dashboard as rd

FAMILIES = Path("test_fixtures_reliability/error_families.json")
OUTDIR = Path("out_reliability")

# Estados possíveis de uma família (§6).
GENERALIZED = "GENERALIZED_ON_TEST_SET"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED_BY_INPUT"
UNRESOLVED = "UNRESOLVED"


def _pontua(cfg: dict, title: str, company: str) -> set:
    """Roda o pipeline semântico REAL sobre um título e devolve os eventos
    que ficariam scoreable para a empresa."""
    hist = {"articles": {"u1": {
        "title": title, "summary": "", "source": "reliability-fixture",
        "domain": "exemplo.com", "pub_ts": 1786000000,
        "pub_iso": "2026-08-07 04:00", "companies": [company]}},
        "run_count": 1}
    rd._reclassify_only_pass(hist, cfg)
    return set(rd.event_ids_for(hist["articles"]["u1"], company) or [])


def _check(cfg: dict, caso: dict) -> tuple[bool, str]:
    """Um caso passa quando o evento proibido some, ou o exigido permanece."""
    got = _pontua(cfg, caso["title"], caso["company"])
    proibido = caso.get("forbidden")
    exigido = caso.get("required")
    if proibido and proibido != "__none__":
        return (proibido not in got), f"proibido={proibido} obtido={sorted(got)}"
    if proibido == "__none__":
        return True, "sem asserção de evento"
    if exigido:
        return (exigido in got), f"exigido={exigido} obtido={sorted(got)}"
    return True, "sem asserção"


def _validar_fixtures(cfg: dict, dados: dict) -> list:
    """Empresa fora da watchlist contamina a medição: `event_ids_for` cai no
    fallback legado F1 e devolve os `event_ids` globais. Falha alto em vez de
    produzir um número sem significado."""
    nomes = {w["name"] for w in (cfg.get("watchlist") or [])}
    ruins = []
    for fam in dados["families"]:
        for b in ("exact_regressions", "semantic_siblings", "negative_controls"):
            for c in (fam.get(b) or []):
                if c["company"] not in nomes:
                    ruins.append(f"{fam['family_id']}/{b}: {c['company']!r}")
    return ruins


def avaliar(cfg: dict | None = None) -> dict:
    cfg = cfg or rd.load_config("config_risco.yaml")
    dados = json.loads(FAMILIES.read_text(encoding="utf-8"))
    ruins = _validar_fixtures(cfg, dados)
    if ruins:
        raise SystemExit("FIXTURES INVÁLIDAS — empresa fora da watchlist: "
                         + "; ".join(ruins))
    resultado = {"families": [], "review_queue": dados.get("review_queue", []),
                 "holdout_start_ts": dados["_meta"]["holdout_start_ts"]}

    for fam in dados["families"]:
        linhas = {}
        for bucket in ("exact_regressions", "semantic_siblings", "negative_controls"):
            casos = fam.get(bucket) or []
            ok = []
            for c in casos:
                passou, detalhe = _check(cfg, c)
                ok.append({"title": c["title"], "company": c["company"],
                           "passou": passou, "detalhe": detalhe})
            linhas[bucket] = ok

        def taxa(b):
            v = linhas[b]
            return (sum(1 for x in v if x["passou"]), len(v))

        ex, exn = taxa("exact_regressions")
        sb, sbn = taxa("semantic_siblings")
        ng, ngn = taxa("negative_controls")

        # Status DERIVADO da medição — nunca copiado do arquivo, exceto quando
        # a família foi declarada sem mecanismo (BLOCKED/UNRESOLVED).
        declarado = fam.get("status", "")
        if declarado in (BLOCKED, UNRESOLVED):
            status = declarado
        elif exn and ex == exn and (sbn == 0 or sb == sbn) and (ngn == 0 or ng == ngn):
            status = GENERALIZED if sbn else PARTIAL
        else:
            status = PARTIAL

        resultado["families"].append({
            "family_id": fam["family_id"], "name": fam["name"],
            "invariant": fam["invariant"], "status": status,
            "exact": [ex, exn], "siblings": [sb, sbn], "negatives": [ng, ngn],
            "detalhes": linhas, "notes": fam.get("notes", ""),
        })
    return resultado


def imprimir(res: dict) -> int:
    print("=" * 96)
    print("RELIABILITY GENERALIZATION — o sistema aprendeu a CLASSE ou só o CASO?")
    print("=" * 96)
    tot = {"exact": [0, 0], "siblings": [0, 0], "negatives": [0, 0]}
    por_status = {}
    for f in res["families"]:
        for k in tot:
            tot[k][0] += f[k][0]
            tot[k][1] += f[k][1]
        por_status[f["status"]] = por_status.get(f["status"], 0) + 1
        marca = {GENERALIZED: "✅", PARTIAL: "⚠️ ", BLOCKED: "⛔", UNRESOLVED: "⛔"}.get(f["status"], "  ")
        print(f"\n{marca} {f['family_id']} — {f['name']}   [{f['status']}]")
        print(f"     invariante: {f['invariant'][:88]}")
        print(f"     exact {f['exact'][0]}/{f['exact'][1]} · "
              f"siblings {f['siblings'][0]}/{f['siblings'][1]} · "
              f"negatives {f['negatives'][0]}/{f['negatives'][1]}")
        for bucket, rotulo in (("exact_regressions", "exact"),
                               ("semantic_siblings", "sibling"),
                               ("negative_controls", "negativo")):
            for x in f["detalhes"][bucket]:
                if not x["passou"]:
                    print(f"       ❌ {rotulo}: {x['title'][:66]}")
                    print(f"          {x['detalhe']}")
    print()
    print("=" * 96)
    print(f"  EXACT REGRESSION RATE     : {tot['exact'][0]}/{tot['exact'][1]}")
    print(f"  SEMANTIC SIBLING RATE     : {tot['siblings'][0]}/{tot['siblings'][1]}")
    print(f"  NEGATIVE CONTROL PRESERV. : {tot['negatives'][0]}/{tot['negatives'][1]}")
    print(f"  famílias generalizadas    : {por_status.get(GENERALIZED, 0)}")
    print(f"  famílias parciais         : {por_status.get(PARTIAL, 0)}")
    print(f"  famílias bloqueadas/abertas: "
          f"{por_status.get(BLOCKED, 0) + por_status.get(UNRESOLVED, 0)}")
    print(f"  review queue (não classificado automaticamente): {len(res['review_queue'])}")
    print("=" * 96)
    return 0


def main() -> int:
    res = avaliar()
    OUTDIR.mkdir(exist_ok=True)
    (OUTDIR / "generalization_report.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    return imprimir(res)


if __name__ == "__main__":
    raise SystemExit(main())
