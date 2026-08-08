#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_4h3c.py — 4H.3C: parser canônico + evidência + atribuição + dedup.

AUTOCONTIDO e SEM REDE: usa `fixtures_4h3b/config_teste.yaml` (Vale, Ford,
Samarco) e um `fetcher` injetado. Nunca toca o config de produção nem
`risk_history.json`.

Cobre os 20 casos exigidos pela fase, na ordem do pedido.
"""
import copy
import hashlib
import json
import tempfile
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa
import edgar_canonical as ec
import edgar_shadow_4h3b as sh3b

BASE = Path(__file__).parent
CFG_PATH = BASE / "fixtures_4h3b" / "config_teste.yaml"
PASS, FAIL = "✅", "❌"
results = []

CFG = rd.load_config(str(CFG_PATH))
AL = {c["name"]: (c.get("aliases") or [c["name"]]) for c in CFG["watchlist"]}
FORMS = {"8-K", "6-K", "10-K", "10-Q", "20-F"}
CUTOFF = 0


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def _subs(*filings, name="Ford Motor Co", tickers=("F",)):
    """Monta um JSON de submissions da SEC no formato real (colunas paralelas)."""
    cols = {k: [] for k in ("form", "filingDate", "reportDate", "accessionNumber",
                            "primaryDocument", "primaryDocDescription", "items")}
    for f in filings:
        cols["form"].append(f.get("form", "8-K"))
        cols["filingDate"].append(f.get("filingDate", "2026-07-20"))
        cols["reportDate"].append(f.get("reportDate", ""))
        cols["accessionNumber"].append(f.get("accessionNumber", "0000037996-26-000143"))
        cols["primaryDocument"].append(f.get("primaryDocument", "d8k.htm"))
        cols["primaryDocDescription"].append(f.get("primaryDocDescription", ""))
        cols["items"].append(f.get("items", ""))
    return {"name": name, "tickers": list(tickers), "sic": "3711",
            "sicDescription": "Motor Vehicles", "exchanges": ["NYSE"],
            "filings": {"recent": cols}}


def _parse(*filings, company="Ford Motor", cik="0000037996", **kw):
    return ec.parse_submissions(_subs(*filings, **kw), company=company, cik10=cik,
                                forms=FORMS, cutoff_ts=CUTOFF, ticker="F")


# ───────────────────────── 1–3: parser canônico ──────────────────────────────
def t01_parser_preserva_items():
    print("\n[1] Parser preserva os items do 8-K")
    fs = _parse({"items": "5.02,9.01", "primaryDocDescription": "8-K"})
    check(len(fs) == 1, "1 filing parseado")
    check(fs[0]["items"] == ["5.02", "9.01"], f"items = {fs[0]['items']}")
    check("Departure/Election of Directors or Certain Officers" in fs[0]["item_labels"],
          "rótulo oficial do item 5.02 resolvido")
    # items com rótulo colado (formato real alternativo da SEC)
    fs2 = _parse({"items": "5.02 Departure of Directors or Certain Officers"})
    check(fs2[0]["items"] == ["5.02"], "extrai código mesmo com rótulo colado")
    # o título NUNCA pode ser "Empresa — 8-K: 8-K"
    check(ec.canonical_title(fs[0]) != "Ford Motor — 8-K: 8-K",
          "título artificial 'Empresa — 8-K: 8-K' não é produzido")
    check("Departure" in ec.canonical_title(fs[0]),
          f"título usa o rótulo real do item: {ec.canonical_title(fs[0])[:70]}")


def t02_parser_preserva_report_date():
    print("\n[2] Parser preserva reportDate (ausente em origin/main)")
    fs = _parse({"filingDate": "2026-07-24", "reportDate": "2026-07-21"})
    check(fs[0]["report_date"] == "2026-07-21", "report_date preservado")
    check(fs[0]["filing_date"] == "2026-07-24", "filing_date preservado e distinto")
    check(fs[0]["cik"] == "0000037996", "CIK preservado")
    check(fs[0]["ticker"] == "F", "ticker preservado")
    check(fs[0]["metadata"]["exchanges"] == ["NYSE"], "metadata do emissor preservada")
    fs2 = _parse({"filingDate": "2026-07-24", "reportDate": ""})
    check(fs2[0]["report_date"] == "", "reportDate ausente não inventa valor")


def t03_accession_estavel():
    print("\n[3] Accession estável entre formatos")
    a, b = "0000037996-26-000143", "000003799626000143"
    check(ec.normalize_accession(a) == ec.normalize_accession(b),
          "com e sem hífen normalizam igual")
    check(ec.accession_dashed(b) == a, "formato canônico com hífens reconstruído")
    fs = _parse({"accessionNumber": b})
    check(fs[0]["accession_number"] == a, "parser devolve accession canônico")
    check(fs[0]["accession_digits"] == "000003799626000143", "dígitos preservados")
    check(f"/{fs[0]['accession_digits']}/" in fs[0]["url"],
          "URL oficial usa o accession sem hífen (padrão dos Archives)")


# ───────────────────── 4–5: formulário não prova evento ──────────────────────
def t04_8k_generico_nao_cria_evento():
    print("\n[4] 8-K genérico (7.01/8.01/9.01) não cria evento")
    for item in ("7.01", "8.01", "9.01"):
        fs = _parse({"items": item})
        an = ec.analyze_filing(fs[0], "The Company issued a press release.")
        check(an["event_ids"] == [], f"item {item} sozinho → 0 eventos")
    # nem mesmo com o texto falando de "material"
    fs = _parse({"items": "8.01"})
    an = ec.analyze_filing(fs[0], "Other events of a material nature were disclosed.")
    check(an["event_ids"] == [], "8.01 + texto genérico continua sem evento")
    ger = [c for c in an["candidatos"] if c["origem"] == "item_generico"]
    check(ger and "não prova evento sozinho" in ger[0]["motivo"],
          "motivo de rejeição registrado explicitamente")


def t05_6k_generico_nao_cria_evento():
    print("\n[5] 6-K genérico não cria evento")
    fs = _parse({"form": "6-K", "items": "", "primaryDocDescription": "6-K"})
    an = ec.analyze_filing(fs[0], "Vale announces its monthly production report.")
    check(an["event_ids"] == [], "6-K sem fato material → 0 eventos")
    check(ec.canonical_title(fs[0]) != "Ford Motor — 6-K: 6-K",
          "título do 6-K não é artificial")
    an2 = ec.analyze_filing(fs[0], "")
    check(an2["event_ids"] == [], "6-K sem corpo recuperado → 0 eventos")
    check(any("corpo não recuperado" in c.get("motivo_decisao", "")
              or "candidatos só por evidência" in c.get("motivo", "")
              for c in an2["candidatos"]),
          "ausência de corpo registrada como motivo")


# ─────────────────────── 6–7: RJ direta vs. de terceiro ──────────────────────
def t06_rj_direta_verdadeira():
    print("\n[6] RJ direta verdadeira (item 1.03 + chapter 11)")
    fs = _parse({"items": "1.03"})
    an = ec.analyze_filing(fs[0], "On July 20, 2026, the Company filed a voluntary "
                                  "petition for relief under chapter 11 of the "
                                  "United States Bankruptcy Code.")
    check("recuperacao_judicial" in an["event_ids"], "RJ reconhecida")
    check("falencia" not in an["event_ids"], "chapter 11 não vira falência")
    a = next(c for c in an["aceitos"] if c["event_id"] == "recuperacao_judicial")
    check(a["confianca"] == "alta", "confiança alta (item 1.03 + evidência)")
    check("chapter 11" in a["evidence_text"].lower(), "evidência textual capturada")
    # chapter 7 → falência, não RJ
    an7 = ec.analyze_filing(fs[0], "The Company filed a petition under chapter 7 "
                                   "of the Bankruptcy Code.")
    check("falencia" in an7["event_ids"] and "recuperacao_judicial" not in an7["event_ids"],
          "chapter 7 → falência, não RJ")


def t07_rj_de_terceiro_vira_contexto():
    print("\n[7] RJ de TERCEIRO vira contexto (Vale/Samarco via 6-K)")
    fs = _parse({"form": "6-K", "items": ""}, company="Vale", cik="0000917851")
    texto = ("Vale S.A. hereby informs its shareholders about the judicial "
             "reorganization plan of Samarco Mineração S.A.")
    an = ec.analyze_filing(fs[0], texto)
    check("recuperacao_judicial" in an["event_ids"],
          "o parser levanta o evento (a atribuição é do pipeline semântico)")
    # o veredito de SUJEITO é do semantic_audit, não do EDGAR
    r = sa.resolve_article_semantics(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", texto,
        "Vale", ["recuperacao_judicial"], AL, article_year=2026)
    d = next((x for x in r["decisoes"] if x["event_id"] == "recuperacao_judicial"), {})
    check(d.get("scoreable") is False, "Vale NÃO pontua a RJ")
    check("Samarco" in str(d.get("subject_company")), f"sujeito = {d.get('subject_company')}")
    check(d.get("event_scope") == "indireto", "event_scope = indireto (contexto)")
    r2 = sa.resolve_article_semantics(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", texto,
        "Samarco Mineração", ["recuperacao_judicial"], AL, article_year=2026)
    d2 = next((x for x in r2["decisoes"] if x["event_id"] == "recuperacao_judicial"), {})
    check(d2.get("scoreable") is True, "Samarco mantém a RJ como evento direto")


# ─────────────────────────── 8–9: rating ─────────────────────────────────────
def t08_downgrade_com_evidencia():
    print("\n[8] Downgrade com evidência textual")
    fs = _parse({"items": "8.01"})
    an = ec.analyze_filing(fs[0], "S&P Global Ratings downgraded the Company's "
                                  "senior unsecured notes to BB+ from BBB-.")
    check("rebaixamento_rating" in an["event_ids"],
          "downgrade reconhecido pelo TEXTO mesmo com item genérico 8.01")
    a = next(c for c in an["aceitos"] if c["event_id"] == "rebaixamento_rating")
    check(a["origem"] == "texto_do_documento", "origem = texto, não item")
    check("downgrad" in a["evidence_match"].lower(), "âncora textual registrada")


def t09_rating_reafirmado_nao_e_downgrade():
    print("\n[9] Rating reafirmado NÃO é downgrade")
    fs = _parse({"items": "8.01"})
    # (a) reafirmação pura: nem âncora de rebaixamento existe
    an = ec.analyze_filing(fs[0], "Fitch Ratings affirmed the Company's BBB- rating "
                                  "with a stable outlook; the rating is unchanged.")
    check("rebaixamento_rating" not in an["event_ids"], "reafirmação não vira downgrade")
    # (b) âncora presente mas NEGADA — o falso positivo clássico
    an_neg = ec.analyze_filing(fs[0], "Fitch Ratings affirmed the Company's BBB- "
                                      "rating; no downgrade was taken at this time.")
    check("rebaixamento_rating" not in an_neg["event_ids"],
          "'no downgrade' NÃO vira rebaixamento")
    rej = [c for c in an_neg["candidatos"]
           if c["event_id"] == "rebaixamento_rating" and not c["aceito"]]
    check(rej and "NEGADA" in rej[0]["motivo_decisao"],
          f"motivo explícito: {rej[0]['motivo_decisao'] if rej else '(sem candidato)'}")
    # (c) downgrade de verdade continua passando (não virou regra cega)
    an_ok = ec.analyze_filing(fs[0], "Moody's downgraded the Company to Ba1.")
    check("rebaixamento_rating" in an_ok["event_ids"],
          "downgrade real continua reconhecido")


# ─────────────────────────── 10–11: M&A ──────────────────────────────────────
def t10_ma_legitimo():
    print("\n[10] M&A legítimo (item 2.01 + evidência)")
    fs = _parse({"items": "2.01"})
    an = ec.analyze_filing(fs[0], "On July 20, 2026, the Company completed the "
                                  "acquisition of Peregrino Field for $3.5 billion "
                                  "pursuant to the purchase agreement.")
    check("ma" in an["event_ids"], "M&A reconhecido")
    a = next(c for c in an["aceitos"] if c["event_id"] == "ma")
    check(a["confianca"] == "alta", "confiança alta (item + evidência)")
    check(a["item"] == "2.01", "item 2.01 registrado como origem")


def t11_falso_ma_rejeitado():
    print("\n[11] Falso M&A rejeitado (2.01 sem evidência de aquisição)")
    fs = _parse({"items": "2.01"})
    an = ec.analyze_filing(fs[0], "The Company announced a change in its "
                                  "quarterly dividend policy.")
    check("ma" not in an["event_ids"], "sem âncora de M&A → rejeitado")
    rej = [c for c in an["candidatos"] if c["event_id"] == "ma" and not c["aceito"]]
    check(rej and "sem evidência textual" in rej[0]["motivo_decisao"],
          "motivo = sem evidência textual")
    # invariante 8: M&A legítimo não pode ser apagado por regra ampla
    an_ok = ec.analyze_filing(fs[0], "completed the acquisition of Peregrino Field")
    check("ma" in an_ok["event_ids"], "a regra NÃO apaga M&A legítimo (invariante 8)")


# ─────────────────────────── 12–13: 5.02 ─────────────────────────────────────
def t12_troca_ceo_real():
    print("\n[12] Troca de CEO real (5.02 com cargo + movimento)")
    fs = _parse({"items": "5.02"})
    an = ec.analyze_filing(fs[0], "On July 20, 2026, John Lawler resigned as Chief "
                                  "Executive Officer of the Company. The Board "
                                  "appointed Sherry House as Chief Executive Officer, "
                                  "effective August 1, 2026.")
    check("troca_ceo" in an["event_ids"], "troca de CEO reconhecida")
    a = next(c for c in an["aceitos"] if c["event_id"] == "troca_ceo")
    det = a["officer_detail"]
    check(det["cargo_relevante"] is True, "cargo CEO identificado")
    check(det["tem_saida"] and det["tem_entrada"], "saída e entrada identificadas")
    check(det["quem_saiu"] and det["quem_entrou"],
          f"quem saiu={det['quem_saiu']!r}, quem entrou={det['quem_entrou']!r}")


def t13_502_sem_evidencia_suficiente():
    print("\n[13] 5.02 sem evidência suficiente NÃO vira troca de CEO")
    fs = _parse({"items": "5.02"})
    an = ec.analyze_filing(fs[0], "At the annual meeting, the shareholders elected "
                                  "the following nominees to the Board of Directors "
                                  "for a one-year term.")
    check("troca_ceo" not in an["event_ids"], "eleição de conselheiro não é troca de CEO")
    rej = [c for c in an["candidatos"] if c["event_id"] == "troca_ceo" and not c["aceito"]]
    check(rej and "CEO/CFO" in rej[0]["motivo_decisao"],
          f"motivo: {rej[0]['motivo_decisao'] if rej else '(ausente)'}")
    # 5.02 sem corpo recuperado também não pode passar
    an2 = ec.analyze_filing(fs[0], "")
    check("troca_ceo" not in an2["event_ids"], "5.02 sem corpo → sem evento")


# ─────────────────────────── 14: dívida ──────────────────────────────────────
def t14_emissao_divida():
    print("\n[14] Emissão de dívida (item 2.03 + evidência)")
    fs = _parse({"items": "2.03"})
    an = ec.analyze_filing(fs[0], "The Company issued $1.5 billion aggregate "
                                  "principal amount of 5.250% senior unsecured "
                                  "notes due 2034 under an indenture.")
    check("emissao_divida" in an["event_ids"], "emissão de dívida reconhecida")
    an2 = ec.analyze_filing(fs[0], "The Company entered into an amendment to an "
                                   "office lease agreement.")
    check("emissao_divida" not in an2["event_ids"], "2.03 sem âncora de dívida → rejeitado")


# ───────────────────── 15–17: deduplicação ───────────────────────────────────
def t15_duplicacao_por_accession():
    print("\n[15] Mesmo accession duas vezes → uma ocorrência")
    fs = _parse({"accessionNumber": "0000037996-26-000143", "items": "1.03"},
                {"accessionNumber": "000003799626000143", "items": "1.03"},
                {"accessionNumber": "0000037996-26-000999", "items": "1.03"})
    check(len(fs) == 3, "3 filings antes da dedup")
    unicos, dups = ec.dedup_filings(fs)
    check(len(unicos) == 2, f"2 documentos únicos após dedup (obtido {len(unicos)})")
    check(len(dups) == 1, "1 duplicata detectada")
    check(dups[0]["duplicate_of"] == "0000037996-26-000143",
          "duplicata aponta para o original")


def t16_dedup_edgar_x_noticia():
    print("\n[16] EDGAR + Google News sobre o mesmo fato → corrobora, não soma")
    fs = _parse({"items": "1.03", "reportDate": "2026-07-20"})
    existentes = {ec.occurrence_key("Ford Motor", "recuperacao_judicial", fs[0]):
                  {"source": "Google News"}}
    r = ec.corroborates(existentes, "Ford Motor", "recuperacao_judicial", fs[0])
    check(r["acao"] == "corroborar", "ação = corroborar")
    check(r["cria_ocorrencia"] is False, "NÃO cria ocorrência adicional")
    check(r["existing_source"] == "Google News", "fonte original preservada")
    # fato novo continua criando ocorrência
    r2 = ec.corroborates(existentes, "Ford Motor", "ma", fs[0])
    check(r2["cria_ocorrencia"] is True, "fato realmente novo cria ocorrência")


def t17_dedup_edgar_x_ri():
    print("\n[17] EDGAR + RI sobre o mesmo fato → mesma regra")
    fs = _parse({"items": "1.03", "reportDate": "2026-07-20"})
    existentes = {ec.occurrence_key("Ford Motor", "recuperacao_judicial", fs[0]):
                  {"source": "RI Ford"}}
    r = ec.corroborates(existentes, "Ford Motor", "recuperacao_judicial", fs[0])
    check(r["acao"] == "corroborar" and r["cria_ocorrencia"] is False,
          "RI também é corroborado, não somado")
    # mesmo fato em filing POSTERIOR (8-K/A) não cria nova ocorrência
    fs_a = _parse({"items": "1.03", "reportDate": "2026-07-20", "form": "8-K",
                   "accessionNumber": "0000037996-26-000200",
                   "filingDate": "2026-07-28"})
    check(ec.occurrence_key("Ford Motor", "recuperacao_judicial", fs_a[0])
          == ec.occurrence_key("Ford Motor", "recuperacao_judicial", fs[0]),
          "8-K/A posterior tem a MESMA chave de ocorrência (report_date manda)")
    r3 = ec.corroborates(existentes, "Ford Motor", "recuperacao_judicial", fs_a[0])
    check(r3["cria_ocorrencia"] is False,
          "documento posterior sobre o mesmo fato não cria ocorrência nova")
    # filing sem evento material não cria ocorrência
    fs_g = _parse({"items": "8.01"})
    an = ec.analyze_filing(fs_g[0], "Press release issued.")
    check(an["event_ids"] == [], "filing sem evento material não gera ocorrência")


# ───────────── 18–20: filer, flag de scoring e invariância ───────────────────
def t18_filer_nao_vira_sujeito():
    print("\n[18] Filer NÃO vira sujeito automaticamente")
    fs = _parse({"form": "6-K"}, company="Vale", cik="0000917851")
    art = ec.to_article(fs[0], "Vale informs about the judicial reorganization "
                               "plan of Samarco Mineração S.A.")
    check("forced_companies" not in art,
          "artigo NÃO usa forced_companies (curto-circuitaria detect_companies)")
    check(art["candidate_companies"] == ["Vale"], "filer entra como CANDIDATO")
    check(art["filing_company"] == "Vale", "filer registrado para rastreabilidade")
    check(art["summary"].strip().startswith("Vale informs"),
          "corpo real do documento vira o texto classificado, não 'desc or form'")
    check(art["report_date"] == "" or isinstance(art["report_date"], str),
          "report_date propagado para o artigo")
    check(art["cik"] == "0000917851" and art["ticker"], "CIK e ticker propagados")


def t19_scoring_desligado_impede_persistencia():
    print("\n[19] edgar_scoring_enabled=false impede persistência")
    c = rd.load_config(str(CFG_PATH))
    c["international_official_sources_enabled"] = True
    c.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    c["edgar_scoring_enabled"] = False
    check(rd.edgar_collection_enabled(c) is True, "coleta LIGADA")
    check(rd.edgar_scoring_enabled(c) is False, "scoring DESLIGADO")

    fs = _parse({"items": "1.03"})
    art = ec.to_article(fs[0], "filed a voluntary petition under chapter 11")
    production_articles = [{"title": "notícia normal", "url": "https://n/1"}]
    shadow = []
    if rd.edgar_scoring_enabled(c):
        production_articles.append(art)
    elif rd.edgar_collection_enabled(c):
        shadow = copy.deepcopy([art])
    check(len(production_articles) == 1, "filing NÃO entrou em production_articles")
    check(len(shadow) == 1, "filing roteado para o caminho de sombra")

    # NOVA INVARIANTE (4H.5F): nesta branch, collection EDGAR está
    # deliberadamente LIGADA (arquitetura de corroboração validada em
    # 4H.5 — run real, 1 TRUE CORROBORATION, 0 FALSE MATCH); scoring
    # autônomo EDGAR continua e sempre continuará OFF. A premissa antiga
    # ("config mantém EDGAR totalmente desligado") foi substituída pela
    # premissa correta desta fase: collection ON não implica scoring ON.
    prod = rd.load_config(str(BASE / "config_risco.yaml"))
    check(rd.edgar_collection_enabled(prod) is True,
          "config: coleta EDGAR LIGADA (invariante desta branch)")
    check(rd.edgar_scoring_enabled(prod) is False,
          "config: scoring EDGAR permanece DESLIGADO")
    check(prod.get("edgar_scoring_enabled") in (None, False),
          "config: chave edgar_scoring_enabled ausente ou false")


def t20_shadow_nao_altera_historico():
    print("\n[20] Shadow não altera histórico nem score")
    c = rd.load_config(str(CFG_PATH))
    c["international_official_sources_enabled"] = True
    c.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    c["edgar_scoring_enabled"] = False

    fs = _parse({"items": "1.03"})
    art = ec.to_article(fs[0], "filed a voluntary petition under chapter 11 of the "
                               "United States Bankruptcy Code")
    hist = {"articles": {"x": {"title": "antiga"}}, "run_count": 7}
    antes = hashlib.sha256(json.dumps(hist, sort_keys=True).encode()).hexdigest()

    with tempfile.TemporaryDirectory() as td:
        meta = sh3b.run_edgar_runtime_shadow([copy.deepcopy(art)], c, rd,
                                             history_snapshot=hist, outdir=td,
                                             watch_files=[])
        depois = hashlib.sha256(json.dumps(hist, sort_keys=True).encode()).hexdigest()
        check(antes == depois, "hash do histórico idêntico antes/depois")
        check(meta.get("persisted_records") == 0, "persisted_records = 0")
        check(meta.get("history_changed") is False, "history_changed = False")
        check(meta.get("scoring_enabled") is False, "scoring_enabled = False")
        check(meta.get("backfill") is False, "backfill = False")
        gerados = {p.name for p in Path(td).iterdir()}
        check(any(n.startswith("edgar_runtime_shadow") for n in gerados),
              "artifacts de sombra gravados em diretório separado")


def t23_boilerplate_do_10q():
    print("\n[23] REGRESSÃO — sumário/capa do 10-Q não é evidência")
    fs = _parse({"form": "10-Q", "items": ""})
    # texto REAL que produziu falso positivo no run 31142988539
    toc = ("60 Item2. Unregistered Sales of Equity Securities and Use of Proceeds 61 "
           "Item3. Defaults Upon Senior Securities 61 Item4. Mine Safety Disclosures")
    an = ec.analyze_filing(fs[0], toc)
    check("default" not in an["event_ids"],
          "'Item 3. Defaults Upon Senior Securities' (título de seção) não é default")
    capa = ("Title of each class Trading Symbol Name of each exchange on which "
            "registered Common Stock F New York Stock Exchange 6.200% Notes due "
            "June 1, 2059 New York Stock Exchange")
    an2 = ec.analyze_filing(fs[0], capa)
    check("emissao_divida" not in an2["event_ids"],
          "capa listando notes registradas não é emissão nova")
    check(ec.is_boilerplate(toc, toc.index("Defaults"), toc.index("Defaults") + 8),
          "is_boilerplate reconhece o contexto de sumário")
    # e o texto REAL de um default continua passando
    an3 = ec.analyze_filing(_parse({"items": "2.04"})[0],
                            "The Company failed to pay the principal due on the "
                            "notes, constituting an event of default under the "
                            "indenture.")
    check("default" in an3["event_ids"], "default real continua reconhecido")


def t24_periodico_nunca_pontua():
    print("\n[24] REGRESSÃO — formulário periódico corrobora, não prova fato novo")
    for form in ("10-Q", "10-K", "20-F"):
        fs = _parse({"form": form, "items": ""})
        an = ec.analyze_filing(fs[0], "The Company issued $1.5 billion aggregate "
                                      "principal amount of senior notes under an "
                                      "indenture during the quarter.")
        a = next((c for c in an["aceitos"] if c["event_id"] == "emissao_divida"), None)
        check(a is not None, f"{form}: evento ainda é RECONHECIDO (não some)")
        check(a and a.get("nao_pontuavel_por_forma") is True,
              f"{form}: marcado como não pontuável por forma")
        check(a and a["decisao"] == "aceito_nao_pontuavel",
              f"{form}: decisão = aceito_nao_pontuavel")
    # 8-K com item continua pontuando normalmente
    an8 = ec.analyze_filing(_parse({"items": "2.03"})[0],
                            "issued $1.5 billion aggregate principal amount of "
                            "senior notes under an indenture")
    a8 = next(c for c in an8["aceitos"] if c["event_id"] == "emissao_divida")
    check(not a8.get("nao_pontuavel_por_forma"), "8-K com item 2.03 continua pontuável")
    check(a8["confianca"] == "alta", "8-K mantém confiança alta")


def t25_data_economica():
    print("\n[25] reportDate NÃO é data econômica universal")
    per = _parse({"form": "10-Q", "filingDate": "2026-08-05",
                  "reportDate": "2026-06-30"})[0]
    check(ec.economic_date(per) == "2026-08-05",
          "10-Q usa filing_date, não o fechamento contábil 2026-06-30")
    oito = _parse({"form": "8-K", "filingDate": "2026-07-20",
                   "reportDate": "2026-07-16"})[0]
    check(ec.economic_date(oito) == "2026-07-16", "8-K pode usar report_date")
    corpo = ("On July 16, 2026, the Company completed the acquisition of Chart "
             "Industries, Inc. for $13.6 billion.")
    check(ec.economic_date(oito, corpo) == "2026-07-16",
          "data explícita do corpo tem precedência")
    check(ec.economic_date_from_text("nothing here") == "",
          "sem data explícita não inventa")
    check(ec.economic_date(per, corpo) == "2026-07-16",
          "corpo vence até no periódico")
    # REGRESSÃO run 31143754520: recitação do contrato original virava a data
    # econômica do 8-K de conclusão (Baker Hughes/Chart, lag de 354 dias).
    antigo = ("The Merger Agreement, dated as of July 28, 2025, by and among "
              "the Company and Chart Industries, Inc.")
    d = ec.economic_date(oito, antigo)
    check(d == "2026-07-16",
          f"data de contrato antigo é descartada (obtido {d}, esperado report_date)")
    check(ec.MAX_DIAS_DATA_EXPLICITA <= 180,
          "limite de plausibilidade da data explícita é estreito")


def t26_matching_hierarquico():
    print("\n[26] Matching hierárquico exige contraparte, não só data")
    conhecidas = [{
        "company": "Baker Hughes", "event_id": "ma", "date": "2026-07-17",
        "fingerprint": ec.entity_fingerprint(
            "Baker Hughes wraps up $13.6bn Chart Industries acquisition",
            exclude=["Baker Hughes"]),
        "source": "Yahoo Finance", "title": "Chart Industries acquisition",
        "occurrence_id": "oc-1",
    }]
    fp_ok = ec.entity_fingerprint(
        "completed the acquisition of Chart Industries, Inc.",
        exclude=["Baker Hughes"])
    r1 = ec.match_occurrence("Baker Hughes", "ma", "2026-07-16", fp_ok, conhecidas)
    check(r1["acao"] == "corroborar", "mesma contraparte + lag 1d → corrobora")
    check(r1["match"]["nivel"] == 1, f"nível 1 (obtido {r1['match']['nivel']})")
    check(r1["cria_ocorrencia"] is False, "não cria ocorrência nova")

    # MESMA empresa e família, contraparte DIFERENTE → rejeitar
    fp_outro = ec.entity_fingerprint("acquisition of Waygate Technologies",
                                     exclude=["Baker Hughes"])
    r2 = ec.match_occurrence("Baker Hughes", "ma", "2026-07-18", fp_outro, conhecidas)
    check(r2["acao"] == "nova_ocorrencia",
          "aquisição DIFERENTE no mesmo período NÃO é a mesma ocorrência")
    check(r2["rejeitados"] and "sem contraparte em comum" in r2["rejeitados"][0]["motivo"],
          "rejeição registrada com motivo econômico")

    # contraparte igual, data muito distante → nível 2, nunca descartada
    r3 = ec.match_occurrence("Baker Hughes", "ma", "2026-04-01", fp_ok, conhecidas)
    check(r3["acao"] == "corroborar" and r3["match"]["nivel"] == 2,
          "contraparte igual + data distante → nível 2")

    # empresa diferente nunca casa
    r4 = ec.match_occurrence("Halliburton", "ma", "2026-07-16", fp_ok, conhecidas)
    check(r4["acao"] == "nova_ocorrencia", "empresa diferente não casa")

    # tolerância calibrada por família
    check(ec.TOLERANCIA_DIAS["ma"] > ec.TOLERANCIA_DIAS["rebaixamento_rating"],
          "M&A tolera mais que rating (anúncio/assinatura/fechamento)")
    check(ec.entity_fingerprint("The Company and the Board of Directors") == set(),
          "boilerplate societário não vira contraparte")


def t22_headers_dos_archives():
    print("\n[22] REGRESSÃO — Archives nunca recebe Host=data.sec.gov")
    h = ec.archive_headers(rd._EDGAR_UA)
    check("Host" not in h,
          "archive_headers NÃO envia Host (data.sec.gov roteia para o bucket errado)")
    check(h.get("User-Agent") == rd._EDGAR_UA, "User-Agent identificável preservado")
    check("Host" in rd._edgar_headers() and
          rd._edgar_headers()["Host"] == "data.sec.gov",
          "a API de submissions continua exigindo Host=data.sec.gov (não regredir)")
    check(ec.archive_headers("x") != rd._edgar_headers(),
          "os dois conjuntos de headers são distintos por construção")

    # falha de corpo precisa ser REGISTRADA, nunca silenciosa
    erros = []
    def _boom(url):
        raise RuntimeError("404 NoSuchKey")
    txt = ec.fetch_document_text("https://www.sec.gov/x.htm", _boom, errors=erros)
    check(txt == "", "falha devolve texto vazio (nunca evento presumido)")
    check(erros and "NoSuchKey" in erros[0], f"motivo registrado: {erros[0] if erros else '—'}")
    erros2 = []
    ec.fetch_document_text("https://www.sec.gov/x.htm", lambda u: "", errors=erros2)
    check(erros2 == ["resposta vazia"], "resposta vazia também é registrada")


def t21_orquestrador_offline():
    print("\n[21] Orquestrador 4H.3C roda offline e prova invariância")
    import edgar_shadow_4h3c as sh3c
    c = rd.load_config(str(CFG_PATH))
    c["international_official_sources_enabled"] = True
    c.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    c["edgar_scoring_enabled"] = False
    for w in c.get("watchlist", []):
        w.setdefault("cik", "0000037996")

    hoje = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    subs = _subs({"items": "1.03", "filingDate": hoje, "reportDate": hoje,
                  "accessionNumber": "0000037996-26-000143"})
    corpo = ("<html><body><p>The Company filed a voluntary petition for relief "
             "under chapter 11 of the Bankruptcy Code.</p></body></html>")

    with tempfile.TemporaryDirectory() as td:
        meta = sh3c.run_shadow_4h3c(rd, c, outdir=td,
                                    submissions_fetcher=lambda cik: subs,
                                    fetcher=lambda url: corpo,
                                    watch_files=[], history={})
        gerados = {p.name for p in Path(td).iterdir()}
        faltando = [a for a in sh3c.ARTIFACTS if a not in gerados]
        check(not faltando, f"todos os 8 artifacts gerados (faltando: {faltando})")
        check(meta["scoring_enabled"] is False, "scoring_enabled = False")
        check(meta["persisted_records"] == 0, "persisted_records = 0")
        check(meta["arquivos_alterados"] == [], "nenhum arquivo de produção alterado")
        check(meta["filings_aceitos"] >= 1, "filings processados")
        check(meta["corpos_recuperados"] >= 1, "corpo do documento recuperado (HTML → texto)")
        check(meta["eventos_classificados"] >= 1, "evento classificado a partir do corpo")
        rel = (Path(td) / "relatorio_edgar_4h3c.md").read_text(encoding="utf-8")
        check("NÃO ativa scoring" in rel, "relatório declara que não ativa scoring")
        check("Recomendação" in rel, "relatório traz recomendação A/B/C")

    # recusa dura se alguém tentar rodar com scoring ligado
    c2 = dict(c)
    c2["edgar_scoring_enabled"] = True
    try:
        sh3c.run_shadow_4h3c(rd, c2, outdir=tempfile.mkdtemp(), watch_files=[],
                             submissions_fetcher=lambda cik: subs,
                             fetcher=lambda url: corpo, history={})
        check(False, "deveria ter recusado scoring ligado")
    except RuntimeError as exc:
        check("recusa rodar" in str(exc), "recusa dura com edgar_scoring_enabled=true")


TESTES = [t01_parser_preserva_items, t02_parser_preserva_report_date,
          t03_accession_estavel, t04_8k_generico_nao_cria_evento,
          t05_6k_generico_nao_cria_evento, t06_rj_direta_verdadeira,
          t07_rj_de_terceiro_vira_contexto, t08_downgrade_com_evidencia,
          t09_rating_reafirmado_nao_e_downgrade, t10_ma_legitimo,
          t11_falso_ma_rejeitado, t12_troca_ceo_real,
          t13_502_sem_evidencia_suficiente, t14_emissao_divida,
          t15_duplicacao_por_accession, t16_dedup_edgar_x_noticia,
          t17_dedup_edgar_x_ri, t18_filer_nao_vira_sujeito,
          t19_scoring_desligado_impede_persistencia,
          t20_shadow_nao_altera_historico, t21_orquestrador_offline,
          t22_headers_dos_archives, t23_boilerplate_do_10q,
          t24_periodico_nunca_pontua, t25_data_economica,
          t26_matching_hierarquico]


def main():
    print("=" * 70)
    print("TESTE 4H.3C — parser canônico EDGAR + evidência + dedup (sem rede)")
    print("=" * 70)
    for t in TESTES:
        t()
    ok = sum(1 for c, _ in results if c)
    tot = len(results)
    print("\n" + "=" * 70)
    if ok == tot:
        print(f"RESULTADO 4H.3C: {ok}/{tot} checagens passaram")
    else:
        print(f"RESULTADO 4H.3C: {ok}/{tot} — FALHAS:")
        for c, l in results:
            if not c:
                print(f"  {FAIL} {l}")
    print("=" * 70)
    return 0 if ok == tot else 1


if __name__ == "__main__":
    raise SystemExit(main())
