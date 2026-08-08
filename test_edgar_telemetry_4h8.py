#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_telemetry_4h8.py — 4H.8: telemetria operacional EDGAR
(form_fora_do_escopo vs body_fetch_failure vs section_extracted vs
matches new/idempotent/rejected/edgar_only). Fixtures reais bundladas,
sem rede, sem caminho local. NÃO testa nenhuma decisão econômica nova —
só observabilidade sobre a MESMA lógica de coleta/matching já validada
em 4H.5/4H.6/4H.7C.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

import risk_dashboard as rd
import edgar_canonical as ec
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


def stub_from_meta(stem: str, company: str, form: str) -> dict:
    meta = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return {
        "filing_company": company, "cik": meta["cik"], "ticker": meta["ticker"], "form": form,
        "accession_number": meta["accession"], "filing_date": meta["filing_date"],
        "report_date": meta["report_date"], "primary_document": meta["primary_document"],
        "filing_items": meta["items"], "url": meta["url"], "summary": "",
        "pub_ts": _ts(meta["filing_date"]),
    }


class _FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")


class _NetworkSim:
    """Monkeypatch de requests.Session.get, servindo HTML LOCAL por
    substring de URL (fixture real) ou simulando falha (status != 200 ou
    exceção) para URLs de teste. Isola apenas a camada de transporte —
    enrich_with_body roda de verdade, sem bypass."""
    def __init__(self, html_by_url_frag: dict, fail_url_frags: dict | None = None):
        self._html = html_by_url_frag
        self._fail = fail_url_frags or {}

    def __enter__(self):
        self._orig = requests.Session.get
        sim = self

        def fake_get(self_session, url, headers=None, timeout=None):
            for frag, exc in sim._fail.items():
                if frag in url:
                    if exc == "raise":
                        raise ConnectionError("simulado (teste 4H.8)")
                    return _FakeResp(exc, "")
            for frag, html in sim._html.items():
                if frag in url:
                    return _FakeResp(200, html)
            return _FakeResp(404, "")

        requests.Session.get = fake_get
        return self

    def __exit__(self, *a):
        requests.Session.get = self._orig


cfg = rd.load_config("config_risco.yaml")

_bh_html = (FIXTURES / "baker_hughes_0001193125-26-305477.html").read_text(encoding="utf-8")
_nu_html = (FIXTURES / "nubank_0001292814-26-003814.html").read_text(encoding="utf-8")
_unstructured_html = ("<html><body><p>Some unstructured filing text with no recognizable "
                       "structure at all, just prose about quarterly operations without any "
                       "dateline or reference markers whatsoever.</p></body></html>")

stub_8k = stub_from_meta("baker_hughes_0001193125-26-305477", "Baker Hughes", "8-K")
stub_6k = stub_from_meta("nubank_0001292814-26-003814", "Nubank (Nu Holdings)", "6-K")
stub_10q = {**stub_8k, "form": "10-Q", "accession_number": "0000000000-26-000099",
            "url": "https://www.sec.gov/Archives/edgar/data/1701605/000000000026000099/x-10q.htm"}
stub_8k_fail = {**stub_8k, "accession_number": "0001193125-26-999999",
                "url": "https://www.sec.gov/Archives/edgar/data/1701605/000000000000000000/nao-existe.htm"}
stub_6k_nosection = {**stub_6k, "accession_number": "0001292814-26-999999",
                      "url": "https://www.sec.gov/Archives/edgar/data/1691493/000000000000000000/sem-secao.htm"}

print("=" * 100)
print("BLOCO A — retrieval real (enrich_with_body): body/form/section (checks 1-6)")
print("=" * 100)

with _NetworkSim({"305477": _bh_html, "003814": _nu_html}):
    arts = corrob.enrich_with_body([stub_8k, stub_6k, stub_10q], cfg, rd)
r8k = next(a["_retrieval_4h8"] for a in arts if a["form"] == "8-K")
r6k = next(a["_retrieval_4h8"] for a in arts if a["form"] == "6-K")
r10q = next(a["_retrieval_4h8"] for a in arts if a["form"] == "10-Q")

check(r8k["form_suportado"] and r8k["body_fetch_attempted"] and r8k["body_fetch_success"],
      "[1] 8-K success: form_suportado=True, body_fetch_attempted=True, body_fetch_success=True")
check((r8k["section_count"] or 0) > 0, "[1b] 8-K success: section_count > 0")
check(r6k["form_suportado"] and r6k["body_fetch_attempted"] and r6k["body_fetch_success"],
      "[2] 6-K success: form_suportado=True, body_fetch_attempted=True, body_fetch_success=True")
check((r6k["section_count"] or 0) > 0, "[2b] 6-K success: section_count > 0")
check(r10q["form_suportado"] is False, "[3] 10-Q: form_suportado=False (fora do escopo por desenho)")
check(r10q["body_fetch_attempted"] is False,
      "[4] 10-Q: body_fetch_attempted=False — NÃO conta como retrieval failure")
check(r10q["body_fetch_success"] is False and r10q["failure_reason"] == "",
      "[4b] 10-Q: sem body_fetch_success e SEM failure_reason (não é uma falha, é fora de escopo)")

with _NetworkSim({}, fail_url_frags={"999999": 404}):
    arts_fail = corrob.enrich_with_body([stub_8k_fail], cfg, rd)
rfail = arts_fail[0]["_retrieval_4h8"]
check(rfail["form_suportado"] and rfail["body_fetch_attempted"] and not rfail["body_fetch_success"],
      "[5] 8-K com HTTP 404: form suportado, fetch tentado, SEM sucesso → conta como retrieval failure")
check(rfail["failure_reason"] == "http_404", "[5b] failure_reason captura o motivo real (http_404)")

with _NetworkSim({"sem-secao": _unstructured_html}):
    arts_nosec = corrob.enrich_with_body([stub_6k_nosection], cfg, rd)
rnosec = arts_nosec[0]["_retrieval_4h8"]
check(rnosec["body_fetch_success"] is True,
      "[6] 6-K com corpo válido mas sem seção reconhecível: body_fetch_success=True")
check(rnosec["section_count"] == 0,
      "[6b] section_count=0 (explicitamente, não None) — NÃO é retrieval failure, é outcome de parsing/seção diferente")

print()
print("=" * 100)
print("BLOCO B — agregação em apply_edgar_corroboration: por_form + fetch layer (checks 3b, 4c, 12)")
print("=" * 100)

with _NetworkSim({"305477": _bh_html, "003814": _nu_html}):
    hist_vazio = base_history()
    resumo_agg = corrob.apply_edgar_corroboration([stub_8k, stub_6k, stub_10q], hist_vazio, cfg, rd)

check(resumo_agg["forms_suportados_total"] == 2 and resumo_agg["forms_fora_do_escopo_total"] == 1,
      "[3c] agregado: 2 forms suportados (8-K+6-K), 1 fora do escopo (10-Q)")
check(resumo_agg["body_fetch_failure"] == 0,
      "[4c] agregado: 0 body_fetch_failure quando 8-K/6-K tiveram sucesso e 10-Q nem foi tentado")
check(resumo_agg["por_form"]["10-Q"]["body_fetch_attempted"] == 0,
      "[4d] por_form['10-Q'].body_fetch_attempted == 0")
check(resumo_agg["por_form"]["8-K"]["total"] == 1 and resumo_agg["por_form"]["6-K"]["total"] == 1,
      "[12] breakdown por form fecha: por_form['8-K'].total==1, por_form['6-K'].total==1")
check(sum(pf["total"] for pf in resumo_agg["por_form"].values()) == resumo_agg["filings_recebidos"],
      "[12b] soma de por_form[*].total == filings_recebidos")

print()
print("=" * 100)
print("BLOCO C — classificação de matches: new/idempotent/rejected/edgar_only (checks 7-10)")
print("=" * 100)


def build_article_from_fixture(stem: str, company: str, form: str) -> dict:
    import edgar_dom as ed
    import edgar_normalizer as en
    import edgar_sections as es
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
    else:
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
    art["_retrieval_4h8"] = {"form": form, "form_suportado": True, "body_fetch_attempted": True,
                              "body_fetch_success": True, "failure_reason": "", "section_count": 1}
    return art


art_bh = build_article_from_fixture("baker_hughes_0001193125-26-305477", "Baker Hughes", "8-K")
art_nu = build_article_from_fixture("nubank_0001292814-26-003814", "Nubank (Nu Holdings)", "6-K")
stub_bh_meta = stub_from_meta("baker_hughes_0001193125-26-305477", "Baker Hughes", "8-K")
stub_nu_meta = stub_from_meta("nubank_0001292814-26-003814", "Nubank (Nu Holdings)", "6-K")


class _Prebuilt:
    def __init__(self, by_acc):
        self._by_acc = by_acc

    def __call__(self, stubs, cfg_, rd_mod, **kw):
        return [self._by_acc[s["accession_number"]] for s in stubs if s["accession_number"] in self._by_acc]


def run_corrob(stubs, history, by_acc):
    orig = corrob.enrich_with_body
    corrob.enrich_with_body = _Prebuilt(by_acc)
    try:
        return corrob.apply_edgar_corroboration(stubs, history, cfg, rd)
    finally:
        corrob.enrich_with_body = orig


# [7] NEW MATCH — Nubank contra ocorrência real compatível
hist_new = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                    "Nubank adds banking license through acquisition of Banco Porto Real"))
r7 = run_corrob([stub_nu_meta], hist_new, {art_nu["accession_number"]: art_nu})
check(r7["matches_new"] == 1 and r7["matches_idempotent"] == 0, "[7] NEW MATCH: matches_new=1")
check(r7["matches"][0]["match_kind"] == "new", "[7b] matches[0].match_kind == 'new'")

# [8] IDEMPOTENT MATCH — Baker Hughes já corroborado, reprocessar
hist_idem = base_history(n1=news_rec("Baker Hughes", "ma", "2026-07-16", "Baker Hughes closes Chart deal"))
r8a = run_corrob([stub_bh_meta], hist_idem, {art_bh["accession_number"]: art_bh})
r8b = run_corrob([stub_bh_meta], hist_idem, {art_bh["accession_number"]: art_bh})
check(r8a["matches_new"] == 1, "[8a] 1ª passada: matches_new=1")
check(r8b["matches_new"] == 0 and r8b["matches_idempotent"] == 1,
      "[8] IDEMPOTENT MATCH: 2ª passada matches_idempotent=1, matches_new=0 "
      "(Baker Hughes idempotente NÃO conta como nova corroboração)")

# [9] REJECTED — mesma empresa+família conhecida, mas contraparte diferente
hist_wrong_cp = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                         "Nubank adquire concorrente XPTO Bank"))
r9 = run_corrob([stub_nu_meta], hist_wrong_cp, {art_nu["accession_number"]: art_nu})
check(r9["matches_rejected"] == 1 and r9["edgar_only"] == 0,
      "[9] REJECTED: ocorrência da mesma empresa+família existe mas contraparte diverge → matches_rejected=1")

# [10] EDGAR-ONLY — nenhuma ocorrência independente conhecida
hist_vazio2 = base_history()
r10 = run_corrob([stub_nu_meta], hist_vazio2, {art_nu["accession_number"]: art_nu})
check(r10["edgar_only"] == 1 and r10["matches_rejected"] == 0,
      "[10] EDGAR-ONLY: zero ocorrência independente conhecida → edgar_only=1 (não é rejeição)")

print()
print("=" * 100)
print("BLOCO D — invariantes de soma, scoring e não-mudança econômica (checks 11, 13, 14)")
print("=" * 100)

hist_multi = base_history(
    n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
               "Nubank adds banking license through acquisition of Banco Porto Real"),
    n2=news_rec("Baker Hughes", "ma", "2026-07-16", "Baker Hughes closes Chart deal"))
r11 = run_corrob([stub_nu_meta, stub_bh_meta], hist_multi,
                 {art_nu["accession_number"]: art_nu, art_bh["accession_number"]: art_bh})
soma = r11["matches_new"] + r11["matches_idempotent"] + r11["matches_rejected"] + r11["edgar_only"]
check(soma == r11["candidatos_avaliados"],
      f"[11] soma das categorias (new+idempotent+rejected+edgar_only={soma}) "
      f"fecha com candidatos_avaliados ({r11['candidatos_avaliados']})")

check(rd.edgar_scoring_enabled(cfg) is False, "[13] edgar_scoring_enabled continua False após 4H.8")
check(rd.edgar_collection_enabled(cfg) is True, "[13b] edgar_collection_enabled continua True")

hist_check14_a = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                          "Nubank adds banking license through acquisition of Banco Porto Real"))
hist_check14_b = base_history(n1=news_rec("Nubank (Nu Holdings)", "ma", "2026-07-20",
                                          "Nubank adds banking license through acquisition of Banco Porto Real"))
run_corrob([stub_nu_meta], hist_check14_a, {art_nu["accession_number"]: art_nu})
run_corrob([stub_nu_meta], hist_check14_b, {art_nu["accession_number"]: art_nu})
check(hist_check14_a["articles"]["n1"]["corrob_sources"] == hist_check14_b["articles"]["n1"]["corrob_sources"],
      "[14] dados finais de risk_history (corrob_sources) idênticos entre execuções — telemetria não afeta economia")
check("_retrieval_4h8" not in json.dumps(hist_check14_a["articles"]["n1"]),
      "[14b] campo de telemetria (_retrieval_4h8) NUNCA vaza para dentro do registro persistido em history")

print()
print("=" * 100)
print("BLOCO E — replay com a ESTRUTURA real do último run (214 filings, checks 15-16)")
print("=" * 100)

import csv as _csv

_dist_csv = FIXTURES / "edgar_form_distribution_2026-08-08.csv"
_real_rows = list(_csv.DictReader(_dist_csv.open(encoding="utf-8")))
check(len(_real_rows) == 214, f"[15a] fixture real bundlada tem 214 filings (tem {len(_real_rows)})")


class _FormOnlyTelemetryEnrich:
    """Simula só a CAMADA de telemetria (form_suportado/body_fetch_*) para os
    214 filings REAIS da janela medida em 4H.7B/4H.7C — sem parsing/HTTP
    real (já provado nos Blocos A/C com fixtures completas; aqui o objetivo
    é só confirmar que a AGREGAÇÃO por_form escala corretamente para a
    distribuição real de forms, não reprovar retrieval de novo)."""
    def __call__(self, stubs, cfg_, rd_mod, **kw):
        out = []
        for s in stubs:
            form = s["form"]
            suportado = form in corrob._FORMS_COM_CORPO
            art = {
                "form": form, "accession_number": s["accession_number"],
                "filing_company": s["filing_company"], "monitored_company": s["filing_company"],
                "edgar_candidates": [], "edgar_has_body": suportado,
                "_retrieval_4h8": {
                    "form": form, "form_suportado": suportado,
                    "body_fetch_attempted": suportado, "body_fetch_success": suportado,
                    "failure_reason": "", "section_count": 1 if suportado else None,
                },
            }
            out.append(art)
        return out


_stubs_real = [{"filing_company": r["issuer"], "cik": "", "ticker": "", "form": r["form"],
                "accession_number": r["accession"], "filing_date": "2026-07-01",
                "report_date": "2026-07-01", "primary_document": "", "filing_items": "",
                "url": f"https://www.sec.gov/Archives/edgar/data/0/{r['accession']}/x.htm",
                "summary": "", "pub_ts": _ts("2026-07-01")} for r in _real_rows]

orig_enrich = corrob.enrich_with_body
corrob.enrich_with_body = _FormOnlyTelemetryEnrich()
try:
    resumo_real = corrob.apply_edgar_corroboration(_stubs_real, base_history(), cfg, rd)
finally:
    corrob.enrich_with_body = orig_enrich

check(resumo_real["filings_recebidos"] == 214, "[15b] filings_recebidos == 214 (não hardcoded — vem da fixture real)")
check(resumo_real["forms_suportados_total"] == 195,
      f"[15] forms_suportados_total == 195 (79 8-K + 116 6-K), obtido {resumo_real['forms_suportados_total']}")
check(resumo_real["forms_fora_do_escopo_total"] == 19,
      f"[15c] forms_fora_do_escopo_total == 19 (10-Q), obtido {resumo_real['forms_fora_do_escopo_total']}")
check(resumo_real["body_fetch_failure"] == 0,
      "[16] body_fetch_failure == 0 — nenhuma falha real fabricada; replay usa a mesma ausência de "
      "falha observada no run real (31273935810), não inventa cenário de erro")
check(resumo_real["por_form"].get("8-K", {}).get("total") == 79
      and resumo_real["por_form"].get("6-K", {}).get("total") == 116
      and resumo_real["por_form"].get("10-Q", {}).get("total") == 19,
      "[15d] breakdown por_form bate exatamente com a distribuição real (79/116/19)")

print()
print("=" * 100)
print(f"RESULTADO 4H.8 TELEMETRIA: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    import sys
    sys.exit(1)
