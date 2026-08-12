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

# ── 4I.2 R6f: TELEMETRIA P/I/IS PROSPECTIVA ─────────────────────────────────
# Três modos, nunca somados. A R6e mostrou por quê: o caso CVS muda só por
# receber mais texto, com a semântica publicada; o caso Duke só muda pela
# razão certa quando a semântica de papel entra. Confundir os dois faria a
# decisão de ativação ser tomada sobre o número errado.
PROSPECTIVO = Path("test_fixtures_reliability/shadow_prospective.json")
EVENTO_ESCOPO = "fraude"          # §11: fraude e nada mais, nesta fase
DELTAS = ("NO_CHANGE", "EVENT_REMOVED", "EVENT_INTRODUCED",
          "SUBJECT_CHANGED", "CAUSALITY_CHANGED")
FONTES = ("NO_CHANGE", "INPUT_ONLY", "SEMANTICS_ONLY", "INPUT_AND_SEMANTICS")
# Regras que representam papel/sujeito consumido — o que separa CQ-1 de CQ-2.
RAZOES_DE_PAPEL = {"R_FRAUDE_ATOR_EXTERNO", "R_FRAUDE_VITIMA_DETECTORA",
                   "R_FRAUDE_PREJUIZO_DE_TERCEIRO", "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE",
                   "R_LIABILITY_DE_TERCEIRO", "R_LIABILITY_VENCE_RESOLUCAO"}


def classificar_delta(ev_a, reg_a, ev_b, reg_b, c, e) -> str:
    a, b = e in ev_a.get(c, []), e in ev_b.get(c, [])
    if a and not b:
        return "EVENT_REMOVED"
    if b and not a:
        return "EVENT_INTRODUCED"
    if reg_a.get((c, e), ("", ""))[1] != reg_b.get((c, e), ("", ""))[1]:
        return "SUBJECT_CHANGED"
    if reg_a.get((c, e), ("", ""))[0] != reg_b.get((c, e), ("", ""))[0]:
        return "CAUSALITY_CHANGED"
    return "NO_CHANGE"


def fonte_do_delta(p, i, s) -> str:
    if p == i == s:
        return "NO_CHANGE"
    if p != i and i == s:
        return "INPUT_ONLY"
    if p == i and i != s:
        return "SEMANTICS_ONLY"
    return "INPUT_AND_SEMANTICS"


def qualidade_causal(gt: str, modo_ok: bool, regra: str) -> str:
    """CQ só se aplica onde já existe review humano — nunca cria ground truth."""
    if gt not in ("TRUE", "FALSE_POSITIVE") or not modo_ok:
        return ""
    return "CQ-2" if regra in RAZOES_DE_PAPEL else "CQ-1"


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


def telemetria_prospectiva(cfg, hist, side) -> dict:
    """P/I/IS para candidatos de FRAUDE, separando controle de observação.

    Um artigo usado na construção das regras nunca é evidência out-of-sample.
    A separação vem de `first_seen_run` — carimbo de coleta é reprocessável e
    traria o passado de volta disfarçado de novidade.
    """
    import reliability_enrichment_sidecar as sc
    revs = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                             encoding="utf-8"))
    srev = ({} if not Path("test_fixtures_reliability/shadow_reviews.json").exists()
            else json.load(io.open("test_fixtures_reliability/shadow_reviews.json",
                                   encoding="utf-8")))
    fam = json.load(io.open("test_fixtures_reliability/error_families.json",
                            encoding="utf-8"))
    controles = {c["title"] for f in fam["families"]
                 for b in ("exact_regressions", "semantic_siblings", "negative_controls")
                 for c in (f.get(b) or [])}
    marco = side.get("r6f_publicado_no_run")
    first_seen = side.get("first_seen_run") or {}
    AL = sa._aliases_map(cfg)

    obs, gate = [], {"fraud_candidates": 0, "eligible": 0, "enriched": 0,
                     "evidence_complete": 0, "primary_only": 0,
                     "primary_supporting": 0, "fallback_P": 0,
                     "historical_controls": 0, "prospective": 0}
    for url, rec in hist["articles"].items():
        if EVENTO_ESCOPO not in (rec.get("event_ids") or []):
            continue
        gate["fraud_candidates"] += 1
        empresas = list((rec.get("events_by_company") or {}).keys()) or \
            list(rec.get("companies") or [])
        if not empresas:
            continue
        gate["eligible"] += 1
        ident = rec.get("canonical_url") or url
        reg = (side.get("articles") or {}).get(ident) or {}
        titulo, resumo = rec.get("title") or "", rec.get("summary") or ""
        base = f"{titulo}. {resumo}"
        sel, _mot = (sc.selecionar_evidencias(reg.get("fragments") or [], base,
                                              empresas[0], EVENTO_ESCOPO,
                                              AL.get(empresas[0]))
                     if reg.get("fragments") else ([], ""))
        run = first_seen.get(ident)
        # Prospectivo exige CARIMBO e run POSTERIOR ao marco. Medido no
        # primeiro run em produção: 18 candidatos de fraude do estoque nunca
        # foram elegíveis para enrichment, logo nunca receberam
        # `first_seen_run` — e sem esta condição apareciam como out-of-sample,
        # inflando justamente a métrica que a separação existe para proteger.
        historico = (titulo in controles) or (marco is None) or (run is None) \
            or run <= marco
        gate["historical_controls" if historico else "prospective"] += 1

        ev_p, reg_p = _decidir(cfg, titulo, resumo, empresas, False)
        if not sel:
            gate["fallback_P"] += 1          # §12 FAIL-CLOSED
            ev_i, reg_i, ev_s, reg_s = ev_p, reg_p, ev_p, reg_p
            completo = False
        else:
            gate["enriched"] += 1
            gate["primary_supporting" if len(sel) > 1 else "primary_only"] += 1
            enr = resumo + " " + " ".join(f["text_excerpt"] for f in sel)
            ev_i, reg_i = _decidir(cfg, titulo, enr, empresas, False)
            ev_s, reg_s = _decidir(cfg, titulo, enr, empresas, True)
            completo = not sc.papel_do_evento_indefinido(
                f"{titulo}. {enr}", empresas[0], EVENTO_ESCOPO, AL.get(empresas[0]))
            gate["evidence_complete"] += completo
        for c in empresas:
            k = f"{url}||{c}||{EVENTO_ESCOPO}"
            gt = (revs.get(k) or {}).get("status") or next(
                (v.get("status") for kk, v in srev.items()
                 if kk != "_meta" and v.get("company") == c
                 and v.get("event_id") == EVENTO_ESCOPO), "UNREVIEWED")
            p = EVENTO_ESCOPO in ev_p.get(c, [])
            i = EVENTO_ESCOPO in ev_i.get(c, [])
            s = EVENTO_ESCOPO in ev_s.get(c, [])
            certo_i = (gt == "TRUE") == i if gt in ("TRUE", "FALSE_POSITIVE") else False
            certo_s = (gt == "TRUE") == s if gt in ("TRUE", "FALSE_POSITIVE") else False
            obs.append({
                "url": url, "company": c, "event": EVENTO_ESCOPO,
                "title": titulo[:110], "source": rec.get("source") or "",
                "language": rec.get("language") or "",
                "classe": "HISTORICAL_CONTROL" if historico else "PROSPECTIVE",
                "first_seen_run": run,
                "P": "scoreable" if p else "nao", "I": "scoreable" if i else "nao",
                "IS": "scoreable" if s else "nao",
                "rule_P": reg_p.get((c, EVENTO_ESCOPO), ("", ""))[0],
                "rule_I": reg_i.get((c, EVENTO_ESCOPO), ("", ""))[0],
                "rule_IS": reg_s.get((c, EVENTO_ESCOPO), ("", ""))[0],
                "P_TO_I": classificar_delta(ev_p, reg_p, ev_i, reg_i, c, EVENTO_ESCOPO),
                "I_TO_IS": classificar_delta(ev_i, reg_i, ev_s, reg_s, c, EVENTO_ESCOPO),
                "delta_source": fonte_do_delta(p, i, s),
                "ground_truth": gt,
                "enrichment_status": reg.get("status") or "SEM_ENRICHMENT",
                "evidence_complete": completo,
                "fragments": [f["method"] for f in sel],
                "tiers": sorted({f["tier"] for f in sel}),
                "CQ_I": qualidade_causal(gt, certo_i, reg_i.get((c, EVENTO_ESCOPO), ("", ""))[0]),
                "CQ_IS": qualidade_causal(gt, certo_s, reg_s.get((c, EVENTO_ESCOPO), ("", ""))[0]),
            })
    PROSPECTIVO.write_text(json.dumps({"gate": gate, "observacoes": obs},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    return {"gate": gate, "obs": obs}


def _prec(itens, modo):
    t = sum(1 for l in itens if l[modo] == "scoreable" and l["ground_truth"] == "TRUE")
    f = sum(1 for l in itens if l[modo] == "scoreable"
            and l["ground_truth"] == "FALSE_POSITIVE")
    den = t + f
    if den == 0:
        return "N/A — INSUFFICIENT_SAMPLE"
    marca = "  (INSUFFICIENT_SAMPLE)" if den < 10 else ""
    return f"{t}/{den} = {t / den * 100:.1f}%{marca}"


def imprimir_prospectiva(r) -> None:
    from collections import Counter
    gate, obs = r["gate"], r["obs"]
    prosp = [l for l in obs if l["classe"] == "PROSPECTIVE"]
    hist = [l for l in obs if l["classe"] == "HISTORICAL_CONTROL"]
    print()
    print("=" * 100)
    print("PROSPECTIVE FRAUD SHADOW — P / I / IS  (telemetria, nunca scoring)")
    print("=" * 100)
    for k, v in gate.items():
        print(f"  {k:22s} {v}")
    print(f"\n  observações: {len(obs)}  "
          f"(controle histórico {len(hist)} · prospectivas {len(prosp)})")
    print("  P_TO_I :", dict(Counter(l["P_TO_I"] for l in obs)))
    print("  I_TO_IS:", dict(Counter(l["I_TO_IS"] for l in obs)))
    print("  fonte  :", dict(Counter(l["delta_source"] for l in obs)))
    print("\n  PRECISÃO REVISADA (controles históricos — NÃO é out-of-sample)")
    for m in ("P", "I", "IS"):
        print(f"    {m:2s}: {_prec(hist, m)}")
    print("\n  PRECISÃO REVISADA (observações prospectivas)")
    for m in ("P", "I", "IS"):
        print(f"    {m:2s}: {_prec(prosp, m)}")
    rev = [l for l in prosp if l["ground_truth"] != "UNREVIEWED"]
    print(f"\n  prospectivas revisadas: {len(rev)}  "
          + str(dict(Counter(l["ground_truth"] for l in prosp))))
    print("  CQ (I) :", dict(Counter(l["CQ_I"] for l in obs if l["CQ_I"])))
    print("  CQ (IS):", dict(Counter(l["CQ_IS"] for l in obs if l["CQ_IS"])))
    print("  regras exercidas em IS:",
          dict(Counter(l["rule_IS"] for l in obs if l["rule_IS"])))
    print("  diversidade de fontes (prospectivas):",
          dict(Counter(l["source"] for l in prosp)) or "—")
    print("  tiers usados:", dict(Counter(str(l["tiers"]) for l in obs if l["tiers"])))
    print(f"  relatório: {PROSPECTIVO}")
    print("=" * 100)


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
    prospectivas = telemetria_prospectiva(cfg, hist, side)
    OUTDIR.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf-8")
    imprimir_prospectiva(prospectivas)

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
