#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_metrics.py — 4I.2 R3b.

Duas métricas que estavam sendo confundidas em contas ad hoc. Elas respondem
perguntas DIFERENTES e têm DENOMINADORES DIFERENTES:

  MÉTRICA A — CLASSIFICATION ACCURACY ON FIXED ADJUDICATED CRITICAL BASELINE
      "Sobre o mesmo conjunto adjudicado por humano, quantos itens o runtime
       classifica como o humano classificou?"
      Denominador FIXO = os itens do baseline rotulados TRUE ou FALSE_POSITIVE.
      AMBIGUOUS fora. Um FP que deixa de pontuar VIRA ACERTO — o denominador
      NÃO encolhe. É a métrica que mede EVOLUÇÃO.

  MÉTRICA B — CANDIDATE LIVE REVIEWED CRITICAL PRECISION
      "Dos críticos que o runtime ainda apresenta, quantos são verdadeiros?"
      Denominador VARIÁVEL = só os itens que PERMANECEM scoreable e têm
      rótulo. Um FP que deixa de pontuar SAI do denominador. É a métrica que
      descreve o estado atual da tela.

Usar o mesmo denominador nas duas é o erro que este módulo existe para
impedir — `test_reliability_metrics.py` falha se alguém o fizer.

Dois universos, sempre nomeados (§19):
  STORED PRODUCTION — `risk_history.json` como está.
  CANDIDATE RUNTIME — projeção em memória com o código atual. NÃO é produção.

Uso:
    python reliability_metrics.py                 # relatório
    python reliability_metrics.py --set-baseline  # congela a identidade
"""
from __future__ import annotations

import copy
import io
import json
import sys
import time
from pathlib import Path

import risk_dashboard as rd

HISTORY = Path("risk_history.json")
REVIEWS = Path("test_fixtures_reliability/live_reviews.json")
BASELINE = Path("test_fixtures_reliability/critical_baseline.json")

BASELINE_ID = "critical_baseline_r1c"
ADJUDICADOS = ("TRUE", "FALSE_POSITIVE")


def _chave(url: str, company: str, event: str) -> str:
    return f"{url}||{company}||{event}"


def _cfg():
    return rd.load_config("config_risco.yaml")


def inventario_stored(cfg: dict) -> dict:
    """{chave: True} para todo (url, empresa, evento) crítico scoreable HOJE."""
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    return _inventario(hist, cfg)


def inventario_candidate(cfg: dict) -> dict:
    """Mesma coisa, projetando o history com o código atual — em memória.

    Não escreve nada. Não é produção.
    """
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    proj = copy.deepcopy(hist)
    rd._reclassify_only_pass(proj, cfg)
    return _inventario(proj, cfg)


def _inventario(hist: dict, cfg: dict) -> dict:
    sev = {e["id"]: e.get("severity") for e in (cfg.get("taxonomy") or [])}
    out = {}
    for url, rec in hist["articles"].items():
        for company, evs in (rec.get("events_by_company") or {}).items():
            for ev in (evs or []):
                if sev.get(ev) == "critico":
                    out[_chave(url, company, ev)] = rec.get("title") or ""
    return out


def carregar_baseline() -> dict:
    if not BASELINE.exists():
        raise SystemExit(
            f"baseline ausente: {BASELINE}. Rode `python reliability_metrics.py "
            f"--set-baseline` uma vez para congelar a identidade dos itens.")
    return json.load(io.open(BASELINE, encoding="utf-8"))


def gravar_baseline(cfg: dict) -> dict:
    """Congela QUEM está no baseline (§22). Só chaves e rótulos — nenhum
    artigo é duplicado aqui."""
    revs = json.load(io.open(REVIEWS, encoding="utf-8"))
    inv = inventario_stored(cfg)
    itens = {}
    for k, v in revs.items():
        if k == "_meta" or k not in inv:
            continue
        itens[k] = {"status": v.get("status", "UNREVIEWED"),
                    "company": v.get("company", ""), "event_id": v.get("event_id", ""),
                    "family_id": v.get("family_id", ""), "title": inv[k][:160]}
    adj = [k for k, v in itens.items() if v["status"] in ADJUDICADOS]
    dados = {
        "baseline_id": BASELINE_ID,
        "created_at": int(time.time()),
        "reference": "R1c critical review · history commit 63f95ab (stored production)",
        "purpose": ("Denominador FIXO e reproduzível da MÉTRICA A. Não é gold "
                    "(§23): gold_set_4i.json permanece independente. Cron novo "
                    "NÃO reescreve este conjunto (§21) — críticos novos entram "
                    "em live inventory / novelty / holdout."),
        "counts": {"total": len(itens),
                   "TRUE": sum(1 for v in itens.values() if v["status"] == "TRUE"),
                   "FALSE_POSITIVE": sum(1 for v in itens.values()
                                         if v["status"] == "FALSE_POSITIVE"),
                   "AMBIGUOUS": sum(1 for v in itens.values() if v["status"] == "AMBIGUOUS"),
                   "denominador_fixo": len(adj)},
        "itens": itens,
    }
    json.dump(dados, io.open(BASELINE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return dados


# ── as duas métricas ────────────────────────────────────────────────────────
def metrica_a(base: dict, inventario: dict) -> dict:
    """FIXED ADJUDICATED BASELINE ACCURACY — denominador FIXO (§16).

    TRUE  acerta quando CONTINUA scoreable.
    FALSE_POSITIVE acerta quando DEIXA de ser scoreable.
    """
    itens = {k: v for k, v in base["itens"].items() if v["status"] in ADJUDICADOS}
    acertos, detalhe = 0, []
    for k, v in itens.items():
        presente = k in inventario
        ok = presente if v["status"] == "TRUE" else not presente
        acertos += ok
        detalhe.append({"chave": k, "label": v["status"], "scoreable": presente,
                        "acerto": ok, "company": v["company"],
                        "event_id": v["event_id"], "title": v["title"]})
    den = len(itens)                       # FIXO — não encolhe nunca
    return {"acertos": acertos, "denominador": den, "detalhe": detalhe,
            "pct": (acertos / den * 100) if den else 0.0,
            "texto": f"{acertos}/{den} = {acertos / den * 100:.1f}%" if den else "N/D"}


def metrica_b(base: dict, inventario: dict) -> dict:
    """CANDIDATE LIVE REVIEWED CRITICAL PRECISION — denominador VARIÁVEL (§17).

    Só entram os itens que PERMANECEM scoreable e têm rótulo adjudicado.
    """
    vivos = [(k, v) for k, v in base["itens"].items()
             if v["status"] in ADJUDICADOS and k in inventario]
    t = sum(1 for _, v in vivos if v["status"] == "TRUE")
    f = sum(1 for _, v in vivos if v["status"] == "FALSE_POSITIVE")
    den = t + f                            # VARIÁVEL — encolhe quando um FP sai
    return {"true": t, "false_positive": f, "denominador": den,
            "pct": (t / den * 100) if den else 0.0,
            "texto": f"{t}/{den} = {t / den * 100:.1f}%" if den else "N/D"}


def relatorio(cfg: dict | None = None) -> dict:
    cfg = cfg or _cfg()
    base = carregar_baseline()
    inv_s = inventario_stored(cfg)
    inv_c = inventario_candidate(cfg)
    r = {
        "baseline_id": base["baseline_id"], "created_at": base["created_at"],
        "counts": base["counts"],
        "stored": {"a": metrica_a(base, inv_s), "b": metrica_b(base, inv_s),
                   "criticos": len(inv_s)},
        "candidate": {"a": metrica_a(base, inv_c), "b": metrica_b(base, inv_c),
                      "criticos": len(inv_c)},
    }
    r["corrigidos"] = [d for d in r["candidate"]["a"]["detalhe"]
                       if d["label"] == "FALSE_POSITIVE" and d["acerto"]]
    r["fp_restantes"] = [d for d in r["candidate"]["a"]["detalhe"]
                         if d["label"] == "FALSE_POSITIVE" and not d["acerto"]]
    r["true_perdidos"] = [d for d in r["candidate"]["a"]["detalhe"]
                          if d["label"] == "TRUE" and not d["acerto"]]
    return r


def imprimir(r: dict) -> int:
    print("=" * 96)
    print(f"RELIABILITY METRICS — baseline `{r['baseline_id']}`")
    print("=" * 96)
    c = r["counts"]
    print(f"  itens no baseline      : {c['total']}  "
          f"(TRUE {c['TRUE']} · FALSE_POSITIVE {c['FALSE_POSITIVE']} · "
          f"AMBIGUOUS {c['AMBIGUOUS']})")
    print(f"  denominador FIXO da A  : {c['denominador_fixo']}  (AMBIGUOUS fora)")
    for universo, rot in (("stored", "STORED PRODUCTION"),
                          ("candidate", "CANDIDATE RUNTIME (projeção, não é produção)")):
        u = r[universo]
        print(f"\n  {rot}")
        print(f"    críticos scoreable no inventário     : {u['criticos']}")
        print(f"    A · fixed adjudicated accuracy       : {u['a']['texto']}"
              f"   (denominador fixo {u['a']['denominador']})")
        print(f"    B · live reviewed critical precision : {u['b']['texto']}"
              f"   (denominador variável {u['b']['denominador']})")
    print(f"\n  FPs corrigidos pelo candidate : {len(r['corrigidos'])}")
    for d in r["corrigidos"]:
        print(f"      · {d['company']}/{d['event_id']} :: {d['title'][:64]}")
    print(f"  FPs que PERMANECEM            : {len(r['fp_restantes'])}")
    for d in r["fp_restantes"]:
        print(f"      · {d['company']}/{d['event_id']} :: {d['title'][:64]}")
    print(f"  TRUE perdidos                 : {len(r['true_perdidos'])}")
    for d in r["true_perdidos"]:
        print(f"      · {d['company']}/{d['event_id']} :: {d['title'][:64]}")
    print("=" * 96)
    return 0


def main() -> int:
    cfg = _cfg()
    if "--set-baseline" in sys.argv:
        d = gravar_baseline(cfg)
        print(f"baseline `{d['baseline_id']}` gravado: {d['counts']} → {BASELINE}")
        return 0
    return imprimir(relatorio(cfg))


if __name__ == "__main__":
    raise SystemExit(main())
