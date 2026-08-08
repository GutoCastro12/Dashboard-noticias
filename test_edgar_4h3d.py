#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_4h3d.py — normalização semântica SOURCE-AWARE do EDGAR.

Sem rede. Cobre os 18 casos exigidos pela fase 4H.3D, na ordem do pedido.
O replay determinístico (16/17) usa o corpus local quando disponível e,
quando não, um corpus sintético equivalente — o teste nunca depende de rede.
"""
import copy
import json
import re
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_normalizer as en

BASE = Path(__file__).parent
CFG_PATH = BASE / "fixtures_4h3b" / "config_teste.yaml"
CORPUS = Path(r"C:\Users\Gustavo\DashRisk-corpus-4h3c")
PASS, FAIL = "✅", "❌"
results = []

CAPA_SEC = (
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 "
    "FORM 8-K CURRENT REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES "
    "EXCHANGE ACT OF 1934 Indicate by check mark whether the registrant is an "
    "emerging growth company as defined in Rule 405 of the Securities Act of 1933 "
    "(§230.405 of this chapter) or Rule 12b-2 of the Securities Exchange Act of "
    "1934 (§240.12b-2 of this chapter). "
)


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def _filing(form="8-K", items=(), **kw):
    d = {"company": "Ford Motor", "cik": "0000037996", "ticker": "F",
         "form": form, "accession_number": "0000037996-26-000143",
         "accession_digits": "000003799626000143", "filing_date": "2026-07-24",
         "report_date": "2026-07-20", "primary_document": "d8k.htm",
         "description": "", "items": list(items), "item_labels": [],
         "url": "https://www.sec.gov/x.htm", "provenance": "EDGAR"}
    d.update(kw)
    return d


def _norm(t):
    return en.normalize_edgar_semantic_text(t, provenance="EDGAR")["semantic_text"]


# ───────────────────────── 1–3: anos de lei vs. ano econômico ────────────────
def t01_securities_act_1933():
    print("\n[1] 'Securities Act of 1933' não vira fato de 1933")
    s = _norm(CAPA_SEC)
    check("1933" not in s, "1933 do boilerplate desapareceu do semantic_text")
    check("Securities Act" not in s, "referência legislativa neutralizada")
    check("1933" in CAPA_SEC, "o BRUTO continua contendo 1933 (não foi alterado)")


def t02_exchange_act_1934():
    print("\n[2] 'Securities Exchange Act of 1934' não vira fato de 1934")
    s = _norm(CAPA_SEC)
    check("1934" not in s, "1934 do boilerplate desapareceu")
    seis_k = ("REPORT OF FOREIGN PRIVATE ISSUER PURSUANT TO RULE 13a-16 OR 15d-16 "
              "UNDER THE SECURITIES EXCHANGE ACT OF 1934 For the month of June 2026")
    check("1934" not in _norm(seis_k), "variante em CAIXA ALTA do 6-K também")


def t03_founded_in_1933_preservado():
    print("\n[3] 'company founded in 1933' continua referência histórica real")
    t = "The company was founded in 1933 and remains family controlled."
    s = _norm(t)
    check("1933" in s, "ano econômico genuíno PRESERVADO")
    check(s == t, "nada foi removido de um texto sem boilerplate")
    misto = ("The company was founded in 1933. Pursuant to the requirements of the "
             "Securities Exchange Act of 1934, the registrant signed this report.")
    sm = _norm(misto)
    check("founded in 1933" in sm, "no texto MISTO o ano econômico sobrevive")
    check(sm.count("1934") == 0, "e o ano de lei some")


# ───────────────────────── 4: datas de contrato ──────────────────────────────
def t04_contrato_2025_em_filing_2026():
    print("\n[4] Contrato de 2025 citado em filing de 2026 não vira evento 2025")
    f = _filing(filing_date="2026-07-24", report_date="2026-07-16")
    corpo = ("The Merger Agreement, dated as of July 28, 2025, by and among the "
             "Company and Chart Industries, Inc.")
    check(ec.economic_date(f, corpo) == "2026-07-16",
          "data de contrato antigo descartada (fora da janela de plausibilidade)")
    recente = "On July 16, 2026, the Company completed the acquisition."
    check(ec.economic_date(f, recente) == "2026-07-16",
          "data recente e explícita é aceita")


# ───────────────────────── 5–7: estrutura do documento ───────────────────────
def t05_heading_nao_vira_ocorrencia():
    print("\n[5] Heading estrutural não vira ocorrência")
    t = "Item 3. Defaults Upon Senior Securities 61 Item 4. Mine Safety Disclosures"
    an = ec.analyze_filing(_filing(form="10-Q"), t, _norm(t))
    check("default" not in an["event_ids"], "título de seção não gera default")


def t06_table_of_contents():
    print("\n[6] Table of Contents ignorado")
    toc = ("TABLE OF CONTENTS Item 1. Business 3 Item 1A. Risk Factors 12 "
           "Item 3. Defaults Upon Senior Securities 61 PART II")
    s = _norm(toc)
    check("Defaults Upon Senior Securities" not in s, "sumário neutralizado")
    check(len(s) == len(toc), "comprimento preservado (offsets continuam válidos)")


def t07_assinatura_ignorada():
    print("\n[7] Bloco de assinatura puro ignorado")
    sig = ("SIGNATURES Pursuant to the requirements of the Securities Exchange Act "
           "of 1934, the registrant has duly caused this report to be signed on its "
           "behalf by the undersigned. /s/ David S. Maltz Name: David S. Maltz "
           "Title: Vice President")
    s = _norm(sig)
    check("1934" not in s, "ano de lei do bloco de assinatura removido")
    check("/s/" not in s, "linha de assinatura removida")


# ───────────────────────── 8–9: 5.02 ─────────────────────────────────────────
def t08_evidencia_no_502_preservada():
    print("\n[8] Evidência econômica dentro do Item 5.02 é preservada")
    corpo = (CAPA_SEC + "Item 5.02 Departure of Directors or Certain Officers. "
             "On July 20, 2026, John Lawler resigned as Chief Executive Officer "
             "of the Company. The Board appointed Sherry House as Chief Executive "
             "Officer, effective August 1, 2026.")
    an = ec.analyze_filing(_filing(items=("5.02",)), corpo, _norm(corpo))
    check("troca_ceo" in an["event_ids"], "troca de CEO real sobrevive à normalização")
    a = next(c for c in an["aceitos"] if c["event_id"] == "troca_ceo")
    check(a["officer_detail"]["quem_saiu"], f"quem saiu = {a['officer_detail']['quem_saiu']!r}")
    check(a.get("evidence_section", "").startswith("Item 5.02")
          or "5.02" in a.get("evidence_section", ""),
          f"seção preservada: {a.get('evidence_section')!r}")
    check(a.get("evidence_source") == "raw_document_text",
          "evidência vem do BRUTO, não do semântico")


def t09_codm_rejeitado():
    print("\n[9] CEO citado apenas como CODM é rejeitado")
    corpo = ("our chief operating decision maker (CODM) is Jeffrey Miller, Chairman "
             "of the Board, President and Chief Executive Officer. Throughout the "
             "year, our CODM assesses the performance of the two segments based on "
             "segment revenue and operating income.")
    an = ec.analyze_filing(_filing(form="10-Q"), corpo, _norm(corpo))
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "CODM não gera troca de CEO pontuável (regressão Halliburton)")


# ───────────────────────── 10–11: falsos positivos de 10-Q ───────────────────
def t10_item3_padrao_nao_e_default():
    print("\n[10] Item 3 padrão do 10-Q não vira default")
    t = ("60 Item2. Unregistered Sales of Equity Securities and Use of Proceeds 61 "
         "Item3. Defaults Upon Senior Securities 61 Item4. Mine Safety Disclosures")
    an = ec.analyze_filing(_filing(form="10-Q"), t, _norm(t))
    a = next((c for c in an["aceitos"] if c["event_id"] == "default"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "Item 3 padrão não gera default pontuável")


def t11_notes_na_capa():
    print("\n[11] Notes na capa não viram emissão nova")
    t = ("Title of each class Trading Symbol Name of each exchange on which "
         "registered Common Stock F New York Stock Exchange 6.200% Notes due "
         "June 1, 2059 FPRB New York Stock Exchange")
    an = ec.analyze_filing(_filing(form="10-Q"), t, _norm(t))
    a = next((c for c in an["aceitos"] if c["event_id"] == "emissao_divida"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "capa de valores registrados não gera emissão pontuável")


# ───────────────────────── 12–13: M&A e terceiro ─────────────────────────────
def t12_ma_real_preservado():
    print("\n[12] M&A real preservado após normalização")
    corpo = (CAPA_SEC + "Item 2.01 Completion of Acquisition. On July 16, 2026, the "
             "Company completed the acquisition of Chart Industries, Inc. for "
             "$13.6 billion in cash.")
    an = ec.analyze_filing(_filing(items=("2.01",)), corpo, _norm(corpo))
    check("ma" in an["event_ids"], "M&A legítimo sobrevive (invariante 8)")
    a = next(c for c in an["aceitos"] if c["event_id"] == "ma")
    check(not a.get("nao_pontuavel_por_forma"), "8-K com item 2.01 continua pontuável")
    fp = ec.entity_fingerprint(a["evidence_text"], exclude=["Ford Motor"])
    check(any("chart" in x for x in fp), f"contraparte extraída da evidência: {sorted(fp)[:3]}")


def t13_contexto_de_terceiro_preservado():
    print("\n[13] Contexto de terceiro preservado")
    import semantic_audit as sa
    cfg = rd.load_config(str(CFG_PATH))
    al = {c["name"]: (c.get("aliases") or [c["name"]]) for c in cfg["watchlist"]}
    corpo = (CAPA_SEC + "Vale informa sobre o Plano de Recuperação Judicial da "
             "Samarco Mineração S.A.")
    s = _norm(corpo)
    check("Samarco" in s, "o fato de terceiro sobrevive à normalização")
    r = sa.resolve_article_semantics("Vale informa sobre Plano de Recuperação "
                                     "Judicial da Samarco", s, "Vale",
                                     ["recuperacao_judicial"], al, article_year=2026)
    d = next((x for x in r["decisoes"] if x["event_id"] == "recuperacao_judicial"), {})
    check(d.get("scoreable") is False, "Vale continua NÃO pontuando a RJ da Samarco")
    check("Samarco" in str(d.get("subject_company")), "sujeito continua sendo a Samarco")


# ───────────────────────── 14–15: contrato do normalizador ───────────────────
def t14_nunca_altera_bruto():
    print("\n[14] Normalizador NUNCA altera raw_document_text")
    original = CAPA_SEC + "On July 16, 2026, the Company completed the acquisition."
    copia = str(original)
    res = en.normalize_edgar_semantic_text(original, provenance="EDGAR")
    check(original == copia, "a string de entrada é idêntica após a chamada")
    check(len(res["semantic_text"]) == len(original),
          "semantic_text preserva o COMPRIMENTO (offsets válidos no bruto)")
    check(res["semantic_text"] != original, "mas o conteúdo foi neutralizado")
    art = ec.to_article(_filing(), original, semantic_text=res["semantic_text"])
    check(art["raw_document_text"] == original.strip(), "artigo carrega o bruto íntegro")
    check(art["semantic_text"] == res["semantic_text"], "e o semântico separado")
    check(art["edgar_normalized"] is True, "marca que houve normalização")


def t15_source_aware():
    print("\n[15] Fonte diferente de EDGAR não passa pelo normalizador")
    for fonte in ("Google News", "RI", "CVM", "", None):
        r = en.normalize_edgar_semantic_text(CAPA_SEC, provenance=fonte)
        check(r["semantic_text"] == CAPA_SEC and r["applied"] is False,
              f"fonte {fonte!r}: texto intacto")
    check(en.is_edgar("EDGAR") and en.is_edgar("sec"), "EDGAR/SEC reconhecidos")


# ───────────────────────── 16–17: replay determinístico ──────────────────────
def _corpus_docs(n=25):
    if CORPUS.exists() and (CORPUS / "index.json").exists():
        idx = json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))
        docs = []
        for r in idx:
            if r.get("corpus_file") and (CORPUS / r["corpus_file"]).exists():
                docs.append((CORPUS / r["corpus_file"]).read_text(encoding="utf-8"))
            if len(docs) >= n:
                break
        if docs:
            return docs, "corpus real"
    return [CAPA_SEC + f" On July {i+1}, 2026, the Company completed the "
            f"acquisition of Target{i} Inc." for i in range(n)], "corpus sintético"


def t16_replay_deterministico():
    print("\n[16] Replay é determinístico")
    docs, origem = _corpus_docs()
    print(f"     ({origem}, {len(docs)} documentos)")
    h1 = [en.normalize_edgar_semantic_text(d)["semantic_text"] for d in docs]
    h2 = [en.normalize_edgar_semantic_text(d)["semantic_text"] for d in docs]
    check(h1 == h2, "duas passadas produzem exatamente o mesmo semantic_text")
    ev1 = [ec.analyze_filing(_filing(), d, s)["event_ids"] for d, s in zip(docs, h1)]
    ev2 = [ec.analyze_filing(_filing(), d, s)["event_ids"] for d, s in zip(docs, h2)]
    check(ev1 == ev2, "e a mesma classificação")


def t17_idempotente():
    print("\n[17] Segunda normalização é idempotente")
    docs, _ = _corpus_docs(12)
    for d in docs[:12]:
        um = en.normalize_edgar_semantic_text(d)["semantic_text"]
        dois = en.normalize_edgar_semantic_text(um)["semantic_text"]
        if um != dois:
            check(False, "normalizar o já-normalizado mudou o texto")
            return
    check(True, "normalizar duas vezes é igual a normalizar uma vez")


# ───────────────────────── 18: isolamento de produção ────────────────────────
def t18_shadow_nao_altera_producao():
    print("\n[18] Shadow não altera produção")
    prod = rd.load_config(str(BASE / "config_risco.yaml"))
    check(rd.edgar_scoring_enabled(prod) is False, "edgar_scoring_enabled = False")
    check(rd.edgar_collection_enabled(prod) is False, "coleta EDGAR desligada")

    import edgar_shadow_4h3c as sh
    cfg = rd.load_config(str(CFG_PATH))
    cfg["international_official_sources_enabled"] = True
    cfg.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    cfg["edgar_scoring_enabled"] = False
    for w in cfg.get("watchlist", []):
        w.setdefault("cik", "0000037996")

    import tempfile
    from datetime import datetime, timezone
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subs = {"name": "Ford Motor Co", "tickers": ["F"], "filings": {"recent": {
        "form": ["8-K"], "filingDate": [hoje], "reportDate": [hoje],
        "accessionNumber": ["0000037996-26-000143"],
        "primaryDocument": ["d.htm"], "primaryDocDescription": [""],
        "items": ["2.01"]}}}
    corpo = ("<html>" + CAPA_SEC + " On July 16, 2026, the Company completed the "
             "acquisition of Chart Industries, Inc.</html>")
    with tempfile.TemporaryDirectory() as td:
        meta = sh.run_shadow_4h3c(rd, cfg, outdir=td, watch_files=[], history={},
                                  submissions_fetcher=lambda c: subs,
                                  fetcher=lambda u: corpo)
        check(meta["scoring_enabled"] is False, "scoring_enabled = False")
        check(meta["persisted_records"] == 0, "persisted_records = 0")
        check(meta["arquivos_alterados"] == [], "nenhum arquivo de produção alterado")
        check(meta["corpos_recuperados"] >= 1, "corpo recuperado e normalizado")


TESTES = [t01_securities_act_1933, t02_exchange_act_1934,
          t03_founded_in_1933_preservado, t04_contrato_2025_em_filing_2026,
          t05_heading_nao_vira_ocorrencia, t06_table_of_contents,
          t07_assinatura_ignorada, t08_evidencia_no_502_preservada,
          t09_codm_rejeitado, t10_item3_padrao_nao_e_default,
          t11_notes_na_capa, t12_ma_real_preservado,
          t13_contexto_de_terceiro_preservado, t14_nunca_altera_bruto,
          t15_source_aware, t16_replay_deterministico, t17_idempotente,
          t18_shadow_nao_altera_producao]


def main():
    print("=" * 78)
    print("TESTE 4H.3D — normalização semântica source-aware do EDGAR (sem rede)")
    print("=" * 78)
    for t in TESTES:
        t()
    ok = sum(1 for c, _ in results if c)
    tot = len(results)
    print("\n" + "=" * 78)
    if ok == tot:
        print(f"RESULTADO 4H.3D: {ok}/{tot} checagens passaram")
    else:
        print(f"RESULTADO 4H.3D: {ok}/{tot} — FALHAS:")
        for c, l in results:
            if not c:
                print(f"  {FAIL} {l}")
    print("=" * 78)
    return 0 if ok == tot else 1


if __name__ == "__main__":
    raise SystemExit(main())
