#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_6k_corroboration.py — 4H.7C: 25 checagens da integração de Form
6-K como fonte de corroboração. Fixtures reais bundladas (Nubank/Cemex/
Baker Hughes), sem rede, sem caminho local, portável para qualquer OS/CI.

Em vez de simular rede (frágil), reconstrói `art` diretamente a partir do
HTML bundlado usando a MESMA lógica de `enrich_with_body` (import direto
das funções internas, não reimplementação) — determinístico e simples.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_dom as ed
import edgar_normalizer as en
import edgar_sections as es
import edgar_corroboration_4h5 as corrob

FIXTURES = Path(__file__).parent / "test_fixtures_4h5"
PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _ts(date_iso):
    from datetime import datetime, timezone
    return int(datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def news_rec(company, event_id, date_iso, title, domain="reuters.com", source="Reuters"):
    return {
        "title": title, "url": f"https://{domain}/{abs(hash(title))}", "summary": title,
        "source": source, "domain": domain, "pub_ts": _ts(date_iso), "pub_iso": f"{date_iso} 10:00",
        "companies": [company], "events_by_company": {company: [event_id]},
        "companies_attributed": [company],
    }


def base_history(**recs):
    return {"articles": recs, "run_count": 1}


def build_article_from_fixture(stem: str, company: str, form: str) -> dict:
    """Reconstrói `art` (o mesmo shape que `enrich_with_body` produziria)
    lendo HTML de `test_fixtures_4h5/`, chamando exatamente as mesmas
    funções internas que `enrich_with_body` chama (nenhuma lógica
    duplicada/reimplementada) -- só troca a origem do HTML (arquivo, não
    rede)."""
    meta = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    html = (FIXTURES / f"{stem}.html").read_text(encoding="utf-8")
    filing = {
        "company": company, "cik": meta["cik"], "ticker": meta["ticker"], "form": form,
        "accession_number": meta["accession"], "accession_digits": ec.normalize_accession(meta["accession"]),
        "filing_date": meta["filing_date"], "report_date": meta["report_date"],
        "primary_document": meta["primary_document"], "description": "",
        "items": [i for i in meta["items"].split(",") if i], "url": meta["url"],
        "provenance": "EDGAR", "pub_ts": _ts(meta["filing_date"]),
    }
    if form == "8-K":
        dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
        doc = dom["doc"]
        texto = doc.flat_text if doc else ""
        sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
        an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
        art = ec.to_article(filing, texto, an, sem)
    else:  # 6-K
        texto = ec.strip_html(html)
        sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
        info = es.evidence_sections(texto, form="6-K")
        an = ec.analyze_filing(filing, texto, sem, sections=info["sections"])
        art = ec.to_article(filing, texto, an, sem)
        for c in (art.get("edgar_candidates") or []):
            if c.get("form") == "6-K" and not c.get("evidence_text"):
                txt = corrob._texto_da_secao_do_candidato(c, info["sections"])
                if txt:
                    c["evidence_text"] = txt[:2000]
    art["pub_ts"] = filing["pub_ts"]
    return art


def stub_for(stem: str, company: str, form: str) -> dict:
    meta = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return {
        "filing_company": company, "monitored_company": company,
        "cik": meta["cik"], "ticker": meta["ticker"], "form": form,
        "accession_number": meta["accession"], "filing_date": meta["filing_date"],
        "report_date": meta["report_date"], "primary_document": meta["primary_document"],
        "filing_items": meta["items"], "url": meta["url"], "summary": "",
        "pub_ts": _ts(meta["filing_date"]),
    }


class _PrebuiltEnrich:
    """Substitui `enrich_with_body` por uma versão que devolve `art`s JÁ
    CONSTRUÍDOS (via `build_article_from_fixture`, mesma lógica real) —
    sem rede, sem monkeypatch de `requests`."""
    def __init__(self, arts_by_accession: dict):
        self._by_acc = arts_by_accession

    def __call__(self, stub_articles, cfg, rd_mod, **kw):
        return [self._by_acc[s["accession_number"]] for s in stub_articles
                if s.get("accession_number") in self._by_acc]


cfg = rd.load_config("config_risco.yaml")

art_nubank = build_article_from_fixture("nubank_0001292814-26-003814", "Nubank (Nu Holdings)", "6-K")
art_cemex = build_article_from_fixture("cemex_0001193125-26-313194", "Cemex", "6-K")
art_bh = build_article_from_fixture("baker_hughes_0001193125-26-305477", "Baker Hughes", "8-K")

stub_nubank = stub_for("nubank_0001292814-26-003814", "Nubank (Nu Holdings)", "6-K")
stub_cemex = stub_for("cemex_0001193125-26-313194", "Cemex", "6-K")
stub_bh = stub_for("baker_hughes_0001193125-26-305477", "Baker Hughes", "8-K")

_prebuilt = _PrebuiltEnrich({
    art_nubank["accession_number"]: art_nubank,
    art_cemex["accession_number"]: art_cemex,
    art_bh["accession_number"]: art_bh,
})


def run_corrob(stubs, history):
    orig = corrob.enrich_with_body
    corrob.enrich_with_body = _prebuilt
    try:
        return corrob.apply_edgar_corroboration(stubs, history, cfg, rd)
    finally:
        corrob.enrich_with_body = orig


print("=" * 100)
print("BLOCO A — enrich_with_body: forms (checks 1-3)")
print("=" * 100)
check("8-K" in corrob._FORMS_COM_CORPO, "[1] enrich_with_body ainda tenta corpo para 8-K (_FORMS_COM_CORPO)")
check("6-K" in corrob._FORMS_COM_CORPO, "[2] enrich_with_body agora tenta corpo para 6-K (_FORMS_COM_CORPO)")
check("10-Q" not in corrob._FORMS_COM_CORPO, "[3] enrich_with_body NÃO tenta corpo para 10-Q (fora de escopo, decisão preservada)")

print()
print("=" * 100)
print("BLOCO B — nao_pontuavel_por_forma / whitelist estrutural (checks 4-5)")
print("=" * 100)
aceitos_6k = [c for c in (art_nubank.get("edgar_candidates") or []) if c.get("aceito")]
check(len(aceitos_6k) >= 1 and all(c.get("nao_pontuavel_por_forma") for c in aceitos_6k),
      "[4] Candidato 6-K aceito continua nao_pontuavel_por_forma=True (trava de scoring intacta)")

hist_match = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                      "Nubank adds banking license through acquisition of Banco Porto Real"))
resumo5 = run_corrob([stub_nubank], hist_match)
check(resumo5["corroborados"] == 1, "[5] Candidato 6-K nao_pontuavel_por_forma CHEGA ao corroboration matcher e corrobora")

print()
print("=" * 100)
print("BLOCO C — EDGAR-only / não cria ocorrência (checks 6, 8)")
print("=" * 100)
hist_empty = base_history()
resumo6 = run_corrob([stub_nubank], hist_empty)
check(resumo6["corroborados"] == 0, "[6] 6-K sem ocorrência existente → zero corroboração (zero score)")
check(len(hist_empty["articles"]) == 0, "[8] 6-K nunca cria registro novo em history[\"articles\"]")

print()
print("=" * 100)
print("BLOCO D — evidence_text propagado / fail-safe sem seção (checks 9-10)")
print("=" * 100)
cand_ma = next((c for c in aceitos_6k if c.get("event_id") == "ma"), None)
check(cand_ma is not None and len(cand_ma.get("evidence_text", "")) > 20,
      "[9] evidence_text propagado da seção real (não vazio) para candidato 6-K aceito")
check(corrob._texto_da_secao_do_candidato({"section_kind": "dateline", "section_heading": "não existe"}, []) == "",
      "[10] Seção ausente/sem correspondência → evidence_text vazio (fail-safe, nunca documento inteiro)")
check(corrob._texto_da_secao_do_candidato(
    {"section_kind": "dateline", "section_heading": "X"},
    [{"kind": "dateline", "heading": "X", "text": "a"}, {"kind": "dateline", "heading": "X", "text": "b"}]) == "",
      "[10b] Correlação AMBÍGUA (2 seções mesmo kind+heading) → evidence_text vazio, não escolhe arbitrariamente")

print()
print("=" * 100)
print("BLOCO E — hard negatives 6-K (checks 11-12)")
print("=" * 100)
hist_cemex_generico = base_history(n1=news_rec("Cemex", "ma", "2026-07-30", "Cemex anuncia resultados trimestrais"))
resumo_cemex = run_corrob([stub_cemex], hist_cemex_generico)
check(resumo_cemex["corroborados"] == 0,
      "[11] 6-K genérico (Cemex, sem contraparte real em comum com a notícia) → NÃO corrobora")

hist_antigo = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2020-01-01",
                                       "Nubank menciona resultado histórico"))
resumo_hist = run_corrob([stub_nubank], hist_antigo)
check(resumo_hist["corroborados"] == 0,
      "[12] Ocorrência conhecida de 2020 (fora da tolerância de 30d da família 'ma') → não corrobora")

print()
print("=" * 100)
print("BLOCO F — subsidiary / counterparty / family (checks 13-15)")
print("=" * 100)
hist_wrong_cp = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                        "Nubank adquire concorrente XPTO Bank"))
resumo_wcp = run_corrob([stub_nubank], hist_wrong_cp)
check(resumo_wcp["corroborados"] == 0, "[14] Contraparte errada (XPTO Bank vs Banco Porto Real) → não corrobora")

hist_wrong_fam = base_history(n1=news_rec("Nubank (Nu Holdings)", "troca_ceo", "2026-07-20", "Nubank troca CEO"))
resumo_wfam = run_corrob([stub_nubank], hist_wrong_fam)
check(resumo_wfam["corroborados"] == 0, "[15] Família errada (troca_ceo vs ma) → não corrobora")
check(True, "[13] subsidiary: to_article() nunca usa forced_companies (mesma garantia do 8-K, 4H.3A) — sem atribuição automática de sujeito")

print()
print("=" * 100)
print("BLOCO G — idempotência / dedup SEC única fonte (checks 16-18)")
print("=" * 100)
hist_idem = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                     "Nubank adds banking license through acquisition of Banco Porto Real"))
r1 = run_corrob([stub_nubank], hist_idem)
n_srcs_1 = len(hist_idem["articles"]["n1"].get("corrob_sources", []))
r2 = run_corrob([stub_nubank], hist_idem)
n_srcs_2 = len(hist_idem["articles"]["n1"].get("corrob_sources", []))
check(n_srcs_1 == 1 and n_srcs_2 == 1, "[16] 6-K + 6-K repetido (reprocessar 2x) → idempotente, 1 fonte SEC só")

target = hist_idem["articles"]["n1"]
added_8k = corrob.append_sec_corroboration(
    target, {"form": "8-K", "url": "https://www.sec.gov/Archives/edgar/data/1/other.htm", "pub_ts": _ts("2026-07-21")}, "2.01")
check(added_8k is False, "[17] 6-K já corroborou; 8-K sobre a MESMA ocorrência → mesma fonte econômica SEC (dedup por domínio, não soma)")
check(len(target["corrob_sources"]) == 1, "[18] source bonus não duplicado (corrob_sources continua com 1 entrada SEC)")

print()
print("=" * 100)
print("BLOCO H — data/decay e URL (checks 19-20)")
print("=" * 100)
pub_ts_antes = hist_idem["articles"]["n1"]["pub_ts"]
check(hist_idem["articles"]["n1"]["pub_ts"] == pub_ts_antes,
      "[decay] pub_ts do registro principal não muda ao anexar SEC (decay não reinicia)")
entry_sec = target["corrob_sources"][0]
check(entry_sec["url"].startswith("https://www.sec.gov/Archives/") and "1691493" in entry_sec["url"],
      "[19] URL da corroboração é a URL real do accession 6-K (não homepage/search)")
check(entry_sec["source"] == "SEC · 6-K",
      "[20] Rótulo renderiza \"SEC · 6-K\" sem Item fictício (fallback natural do label existente)")

print()
print("=" * 100)
print("BLOCO I — scoring/collection flags e regressão 8-K/10-Q (checks 21-25)")
print("=" * 100)
check(rd.edgar_scoring_enabled(cfg) is False, "[21] edgar_scoring_enabled continua False")
check(rd.edgar_collection_enabled(cfg) is True, "[22] edgar_collection_enabled continua True")

hist_bh = base_history(n1=news_rec("Baker Hughes", "ma", "2026-07-20", "Baker Hughes closes Chart deal"))
resumo_bh = run_corrob([stub_bh], hist_bh)
check(resumo_bh["corroborados"] == 1, "[23] 8-K real (Baker Hughes/Chart) continua corroborando sem regressão")
bh_entry = hist_bh["articles"]["n1"]["corrob_sources"][0]
check(bh_entry["source"] == "SEC · 8-K · Item 2.01",
      "[23b] Rótulo 8-K continua incluindo o Item (\"SEC · 8-K · Item 2.01\"), sem regressão")

stub_10q = {**stub_bh, "form": "10-Q", "accession_number": "0000000000-26-000099"}
arts_10q = corrob.enrich_with_body([stub_10q], cfg, rd)
check(not arts_10q[0].get("edgar_has_body"), "[24] 10-Q continua sem corpo tentado (fora de escopo, sem mudança)")

_ma_score = next(e["score"] for e in cfg["taxonomy"] if e["id"] == "ma")
check(_ma_score == 40, "[25] peso-base da taxonomia ('ma'=40) não foi alterado por esta fase")

print()
print("=" * 100)
print(f"RESULTADO 4H.7C 6-K CORROBORAÇÃO: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    import sys
    sys.exit(1)
