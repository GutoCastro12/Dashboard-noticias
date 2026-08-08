#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_replay_4h5.py — replay offline BASE vs EDGAR-CORROBORATION usando o
build_evolution REAL e o risk_history.json de produção (lido, NUNCA escrito).
Aplica corroboração SEC aos 3 casos reais já validados nos testes (Baker
Hughes/Chart Industries, Capital One/Brex, Truist/CEO) e mostra TODOS os
deltas de score/status/ranking, para qualquer emissor que mude.
"""
import copy
import json
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_dom as ed
import edgar_normalizer as en
import edgar_corroboration_4h5 as corrob

CORPUS = Path(r"C:\Users\Gustavo\DashRisk-corpus-4h4-html")


def load_real_filing_article(cik, accession, company):
    idx = json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))
    row = next(r for r in idx if r["accession"] == accession)
    html = (CORPUS / row["html_file"]).read_text(encoding="utf-8", errors="replace")
    filing = {
        "company": company, "cik": cik, "ticker": row.get("ticker", ""), "form": "8-K",
        "accession_number": accession, "accession_digits": ec.normalize_accession(accession),
        "filing_date": row["filing_date"], "report_date": row.get("report_date", ""),
        "primary_document": row["primary_document"], "description": row.get("description", ""),
        "items": [i for i in row.get("items", "").split(",") if i],
        "url": row["url"], "provenance": "EDGAR",
    }
    dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
    doc = dom["doc"]
    texto = doc.flat_text if doc else ""
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
    art = ec.to_article(filing, texto, an, sem)
    from datetime import datetime, timezone
    art["pub_ts"] = int(datetime.strptime(filing["filing_date"], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp())
    return art


CASOS = [
    dict(cik="0001701605", accession="0001193125-26-305477", company="Baker Hughes",
        event_id="ma", item="2.01"),
    dict(cik="0000927628", accession="0001193125-26-145764", company="Capital One Financial",
        event_id="ma", item="3.02"),
    dict(cik="0001193125", accession="0001193125-26-270320", company="Truist Financial",
        event_id="troca_ceo", item="5.02"),
]

cfg = rd.load_config("config_risco.yaml")
history_real = json.loads(Path("risk_history.json").read_text(encoding="utf-8"))

print("=" * 100)
print("REPLAY OFFLINE — BASE (producao real) vs EDGAR-CORROBORATION")
print("=" * 100)

hist_base = copy.deepcopy(history_real)
hist_exp = copy.deepcopy(history_real)

ev_base = rd.build_evolution(hist_base, cfg, window_days=90)
by_company_base = {r["company"]: r for r in ev_base}

manual_review = []
applied = []
for caso in CASOS:
    art = load_real_filing_article(caso["cik"], caso["accession"], caso["company"])
    aceitos = [c for c in (art.get("edgar_candidates") or [])
               if c.get("aceito") and not c.get("nao_pontuavel_por_forma")]
    cand = next((c for c in aceitos if c.get("event_id") == caso["event_id"]), None)
    status = "candidato_nao_aceito_pelo_classificador_canonico"
    if cand:
        aliases = corrob._aliases_for(caso["company"], cfg)
        fp = ec.entity_fingerprint(cand.get("evidence_text") or "", exclude=aliases)
        data_edgar = ec.economic_date(
            {"form": "8-K", "filing_date": art["filing_date"], "report_date": art.get("report_date", "")},
            text=cand.get("evidence_text") or "")
        conhecidas = corrob.known_occurrences_for(hist_exp, caso["company"], caso["event_id"], cfg, rd)
        res = ec.match_occurrence(caso["company"], caso["event_id"], data_edgar, fp, conhecidas)
        status = res["acao"]
        if res["acao"] == "corroborar":
            matched_url = res["match"]["occurrence_id"]
            target = hist_exp["articles"][matched_url]
            added = corrob.append_sec_corroboration(target, art, caso["item"])
            applied.append(dict(**caso, matched_url=matched_url,
                               matched_source=res["match"].get("source", ""),
                               matched_title=res["match"].get("title", ""),
                               lag_dias=res["match"].get("lag"),
                               entidades_comuns=res["match"].get("entidades_comuns", []),
                               novo_bonus=added))
    manual_review.append({**caso, "status": status})

ev_exp = rd.build_evolution(hist_exp, cfg, window_days=90)
by_company_exp = {r["company"]: r for r in ev_exp}

ranking_base = {r["company"]: i for i, r in enumerate(
    sorted(ev_base, key=lambda x: -x["total_score"]), 1)}
ranking_exp = {r["company"]: i for i, r in enumerate(
    sorted(ev_exp, key=lambda x: -x["total_score"]), 1)}

print("\n--- CASOS AVALIADOS (revisão manual, 100% — nenhuma amostragem) ---")
for m in manual_review:
    print(f"  {m['company']:25s} accession={m['accession']:22s} event={m['event_id']:12s} status={m['status']}")

print("\n--- MATCHES APLICADOS ---")
for a in applied:
    print(f"  {a['company']}: casou com '{a['matched_title'][:70]}' "
          f"({a['matched_source']}, lag={a['lag_dias']}d, entidades comuns={a['entidades_comuns']}, "
          f"novo_bonus={a['novo_bonus']})")

print("\n--- TODOS OS EMISSORES COM DELTA DE SCORE (BASE -> EDGAR-CORROBORATION) ---")
n_changed = 0
all_companies = sorted(set(by_company_base) | set(by_company_exp))
for co in all_companies:
    b = by_company_base.get(co)
    e = by_company_exp.get(co)
    sb = b["total_score"] if b else 0
    se = e["total_score"] if e else 0
    if round(sb, 4) != round(se, 4):
        n_changed += 1
        stb = b["status"] if b else "—"
        ste = e["status"] if e else "—"
        rb = ranking_base.get(co, "—")
        re_ = ranking_exp.get(co, "—")
        print(f"  {co:25s} score {sb:7.2f} -> {se:7.2f} (Δ{se-sb:+.2f})  "
              f"status {stb} -> {ste}  ranking #{rb} -> #{re_}")

print(f"\nTotal de emissores com delta: {n_changed} (esperado: {len(applied)})")

print("\n--- DECOMPOSIÇÃO DETALHADA POR EVENTO (breakdown), para cada emissor com delta ---")
for a in applied:
    co = a["company"]
    _label = {e["id"]: e["label"] for e in cfg["taxonomy"]}.get(a["event_id"], "")
    b_before = next((b for b in by_company_base[co]["breakdown"] if b.get("label") == _label), None)
    b_after = next((b for b in by_company_exp[co]["breakdown"] if b.get("label") == _label), None)
    print(f"\n  {co} / {a['event_id']}:")
    if b_before:
        print(f"    ANTES: base={b_before['base']} decay_f={b_before['decay_f']} trust_f={b_before['trust_f']} "
              f"corrob_bonus={b_before['corrob_bonus']} contrib={b_before['contrib']} sources={b_before['sources']}")
    if b_after:
        print(f"    DEPOIS: base={b_after['base']} decay_f={b_after['decay_f']} trust_f={b_after['trust_f']} "
              f"corrob_bonus={b_after['corrob_bonus']} contrib={b_after['contrib']} sources={b_after['sources']}")
        print(f"    fontes (all_sources): {[s['source'] for s in b_after['all_sources']]}")

print("\n--- INVARIANTES ---")
inv_ok = True
for a in applied:
    co = a["company"]
    _label = {e["id"]: e["label"] for e in cfg["taxonomy"]}.get(a["event_id"], "")
    b_before = next((b for b in by_company_base[co]["breakdown"] if b.get("label") == _label), None)
    b_after = next((b for b in by_company_exp[co]["breakdown"] if b.get("label") == _label), None)
    if b_before and b_after and b_before["base"] != b_after["base"]:
        inv_ok = False
        print(f"  ❌ peso-base MUDOU para {co}: {b_before['base']} -> {b_after['base']}")
print(f"  ✅ peso-base (taxonomy score) idêntico antes/depois, para todos os matches: {inv_ok}")
print(f"  ✅ nenhum evento novo criado em history[\"articles\"] (len base={len(hist_base['articles'])}, "
      f"len exp={len(hist_exp['articles'])}, iguais={len(hist_base['articles'])==len(hist_exp['articles'])})")
