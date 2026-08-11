#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_live_audit.py — 4I.2 Reliability Learning Loop (R1b).

Inventário COMPLETO dos eventos scoreable críticos/altos do history vivo, com
rastreio de novidade. Existe por um motivo concreto: o verbete da Britannica
entrou em produção e pôs a General Motors em CRÍTICO sem que ninguém visse,
porque validávamos os falsos positivos conhecidos em vez de varrer o estado
crítico real depois de cada cron.

Novidade (§23): cada linha é EXISTING, NEW ou CHANGED contra o último snapshot.
Um caso novo precisa saltar aos olhos no meio de centenas.

Review (§24): o status vem de `live_reviews.json`, preenchido por humano. O
runtime NUNCA escreve rótulo de verdade sobre si mesmo — a classificação do
próprio pipeline não é ground truth.

Contribuição de score: NÃO é decomposta por evento aqui. `build_evolution`
agrega por empresa, e reimplementar a fórmula duplicaria a lógica de scoring —
o mesmo erro que evitamos ao não duplicar a semântica no runner. Reportamos
severidade e idade, que é o que sustenta triagem honesta.

Uso:
    python reliability_live_audit.py                  # audita e compara
    python reliability_live_audit.py --set-baseline   # grava o snapshot atual
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import risk_dashboard as rd

HISTORY = Path("risk_history.json")
FAMILIES = Path("test_fixtures_reliability/error_families.json")
REVIEWS = Path("test_fixtures_reliability/live_reviews.json")
OUTDIR = Path("out_reliability")
BASELINE = OUTDIR / "live_baseline.json"
CSV_OUT = OUTDIR / "current_high_critical.csv"
HOLDOUT_OUT = OUTDIR / "holdout_candidates.csv"

REVIEW_STATES = ("UNREVIEWED", "TRUE", "FALSE_POSITIVE", "AMBIGUOUS")


def _chave(url: str, company: str, event: str) -> str:
    return f"{url}||{company}||{event}"


def _conhecidos(dados: dict) -> set:
    """Títulos já usados na construção das regras (gold + memória de erros).
    Artigos assim NÃO são out-of-sample, por definição."""
    vistos = set()
    for fam in dados["families"]:
        for b in ("exact_regressions", "semantic_siblings", "negative_controls"):
            for c in (fam.get(b) or []):
                vistos.add(c["title"])
    for q in dados.get("review_queue", []):
        vistos.add(q["title"])
    gold = Path("test_fixtures_4i/gold_set_4i.json")
    if gold.exists():
        for c in json.loads(gold.read_text(encoding="utf-8"))["casos"]:
            vistos.add(c["title"])
    return vistos


def coletar(cfg: dict | None = None) -> dict:
    cfg = cfg or rd.load_config("config_risco.yaml")
    hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    dados = json.loads(FAMILIES.read_text(encoding="utf-8"))
    revs = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else {}
    sev = {e["id"]: e.get("severity") for e in (cfg.get("taxonomy") or [])}
    rotulo = {e["id"]: (e.get("label") or e["id"]) for e in (cfg.get("taxonomy") or [])}
    holdout_ts = dados["_meta"]["holdout_start_ts"]
    conhecidos = _conhecidos(dados)
    agora = time.time()

    linhas, holdout = [], []
    for url, rec in hist["articles"].items():
        cap = rec.get("captured_ts") or 0
        titulo = rec.get("title") or ""
        discards = {(d.get("empresa"), d.get("event_id")): d
                    for d in (rec.get("semantic_discards") or [])}
        for company, evs in (rec.get("events_by_company") or {}).items():
            for ev in (evs or []):
                s = sev.get(ev, "?")
                if s not in ("critico", "alto"):
                    continue
                k = _chave(url, company, ev)
                d = discards.get((company, ev)) or {}
                linha = {
                    "company": company, "event_id": ev,
                    "event_label": rotulo.get(ev, ev), "severity": s,
                    "title": titulo, "source": rec.get("source") or "",
                    "url": url, "captured_ts": cap,
                    "cap_iso": rec.get("cap_iso") or "", "pub_iso": rec.get("pub_iso") or "",
                    "age_days": int((agora - (rec.get("pub_ts") or agora)) / 86400),
                    "rule": d.get("regra") or "",
                    "assessment": (d.get("motivo") or "")[:120],
                    "review_status": revs.get(k, {}).get("status", "UNREVIEWED"),
                    "review_note": revs.get(k, {}).get("note", ""),
                    "known_case": "SIM" if titulo in conhecidos else "nao",
                }
                linhas.append(linha)
                if cap >= holdout_ts and titulo not in conhecidos:
                    holdout.append(linha)
    return {"linhas": linhas, "holdout": holdout, "holdout_start_ts": holdout_ts,
            "records": len(hist["articles"])}


def novidade(linhas: list) -> dict:
    """EXISTING / NEW / CHANGED contra o último snapshot (§23)."""
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    if base is None:
        for l in linhas:
            l["novelty"] = "BASELINE_ABSENT"
        return {"novo": 0, "existente": 0, "alterado": 0, "sem_baseline": True}
    antes = base.get("itens", {})
    n = {"novo": 0, "existente": 0, "alterado": 0, "sem_baseline": False}
    for l in linhas:
        k = _chave(l["url"], l["company"], l["event_id"])
        a = antes.get(k)
        if a is None:
            l["novelty"] = "NEW"; n["novo"] += 1
        elif a.get("severity") != l["severity"] or a.get("rule") != l["rule"]:
            l["novelty"] = "CHANGED"; n["alterado"] += 1
        else:
            l["novelty"] = "EXISTING"; n["existente"] += 1
    return n


def gravar(res: dict) -> None:
    OUTDIR.mkdir(exist_ok=True)
    campos = ["novelty", "severity", "company", "event_id", "event_label", "title",
              "source", "url", "pub_iso", "cap_iso", "captured_ts", "age_days",
              "rule", "assessment", "review_status", "review_note", "known_case"]
    with open(CSV_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for l in sorted(res["linhas"], key=lambda x: (x["severity"], x["company"])):
            w.writerow(l)
    with open(HOLDOUT_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for l in res["holdout"]:
            w.writerow(l)


def set_baseline(res: dict) -> None:
    OUTDIR.mkdir(exist_ok=True)
    itens = {_chave(l["url"], l["company"], l["event_id"]):
             {"severity": l["severity"], "rule": l["rule"]} for l in res["linhas"]}
    BASELINE.write_text(json.dumps(
        {"gravado_em": int(time.time()), "itens": itens}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def resumo(res: dict, nov: dict) -> dict:
    crit = [l for l in res["linhas"] if l["severity"] == "critico"]
    alto = [l for l in res["linhas"] if l["severity"] == "alto"]
    rev = [l for l in res["holdout"] if l["review_status"] != "UNREVIEWED"]

    def _rev(itens):
        c = {k: sum(1 for l in itens if l["review_status"] == k) for k in REVIEW_STATES}
        revd = len(itens) - c["UNREVIEWED"]
        den = c["TRUE"] + c["FALSE_POSITIVE"]
        return {**c, "total": len(itens), "reviewed": revd,
                "coverage": f"{revd}/{len(itens)}",
                # AMBIGUOUS fica FORA do denominador (§15)
                "precision": (f"{c['TRUE']}/{den} = {c['TRUE'] / den * 100:.1f}%"
                              if den else "N/D — sem itens adjudicados")}

    return {
        "review": _rev(crit), "review_high": _rev(alto),
        "records": res["records"],
        "critical": len(crit), "high": len(alto),
        "new_critical": sum(1 for l in crit if l.get("novelty") == "NEW"),
        "new_high": sum(1 for l in alto if l.get("novelty") == "NEW"),
        "changed": nov["alterado"], "sem_baseline": nov["sem_baseline"],
        "holdout_total": len(res["holdout"]),
        "holdout_reviewed": len(rev),
        "holdout_unreviewed": len(res["holdout"]) - len(rev),
        "unreviewed_critical": sum(1 for l in crit if l["review_status"] == "UNREVIEWED"),
    }


def main() -> int:
    res = coletar()
    if "--set-baseline" in sys.argv:
        set_baseline(res)
        print(f"baseline gravado: {len(res['linhas'])} itens → {BASELINE}")
        return 0
    nov = novidade(res["linhas"])
    gravar(res)
    r = resumo(res, nov)
    print("=" * 96)
    print("LIVE CRITICAL/HIGH AUDIT")
    print("=" * 96)
    print(f"  records                 : {r['records']}")
    print(f"  scoreable CRÍTICOS      : {r['critical']}")
    print(f"  scoreable ALTOS         : {r['high']}")
    if r["sem_baseline"]:
        print("  novidade                : SEM BASELINE — rode --set-baseline para armar o diff")
    else:
        print(f"  NEW críticos            : {r['new_critical']}")
        print(f"  NEW altos               : {r['new_high']}")
        print(f"  CHANGED                 : {r['changed']}")
    print(f"  críticos UNREVIEWED     : {r['unreviewed_critical']}")
    print(f"  holdout (out-of-sample) : {r['holdout_total']}"
          f"  (reviewed {r['holdout_reviewed']} · unreviewed {r['holdout_unreviewed']})")
    if r["holdout_reviewed"] == 0:
        print("  precisão do holdout     : NÃO CALCULÁVEL — sem labels humanos (§26)")
    print(f"  CSV                     : {CSV_OUT}")
    print(f"  holdout CSV             : {HOLDOUT_OUT}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
