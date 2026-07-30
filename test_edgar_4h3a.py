#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_edgar_4h3a.py — Bloco I da 4H.3A (20 casos). Offline, sem rede."""
import copy
import json
from pathlib import Path

import risk_dashboard as rd
import edgar_shadow_4h3a as sh

PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def _cfg_flags(g=None, s=None, sc=None):
    c = {"official_sources": {"EUA": {}}, "watchlist": []}
    if g is not None:
        c["international_official_sources_enabled"] = g
    if s is not None:
        c["official_sources"]["EUA"]["enabled"] = s
    if sc is not None:
        c["edgar_scoring_enabled"] = sc
    return c


CFG = rd.load_config("config_risco.yaml")


def t01_matriz_and():
    print("\n[1] Matriz AND das flags")
    check(rd.edgar_collection_enabled(_cfg_flags(False, False)) is False, "global=F source=F → desligado")
    check(rd.edgar_collection_enabled(_cfg_flags(False, True)) is False, "global=F source=T → desligado (trava mestre)")
    check(rd.edgar_collection_enabled(_cfg_flags(True, False)) is False, "global=T source=F → desligado")
    check(rd.edgar_collection_enabled(_cfg_flags(True, True)) is True, "global=T source=T → ligado")
    check(rd.edgar_collection_enabled(_cfg_flags()) is False, "flags ausentes (produção) → desligado")


def t02_dry_run_independe_flags():
    print("\n[2] --edgar-dry-run independe das flags")
    c = _cfg_flags(False, False)
    c["watchlist"] = []
    out = rd.fetch_edgar_filings(c, force=True)
    check(out == [], "force=True executa o caminho do coletor sem exigir flags (watchlist vazia → [])")
    c2 = dict(CFG)
    check(rd.fetch_edgar_filings(c2) == [], "sem force, config de produção continua retornando [] (score inalterado)")


def t03_source_diferente_subject():
    print("\n[3] source_company != subject_company")
    art = {"title": "Vale — 6-K: plano de recuperação judicial da Samarco", "summary": "",
           "url": "u1", "filing_company": "Vale", "source_company": "Vale",
           "provenance": "EDGAR", "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(art, CFG)
    role = (art.get("mention_roles") or {}).get("Vale", {})
    check(role.get("subject_company") == "Samarco Mineração",
          f"subject resolvido = {role.get('subject_company')!r} (≠ filer 'Vale')")


def t04_vale_samarco_filing_oficial():
    print("\n[4] Caso Vale/Samarco em filing OFICIAL")
    art = {"title": "Vale — 6-K: plano de recuperação judicial da Samarco", "summary": "",
           "url": "u2", "filing_company": "Vale", "provenance": "EDGAR",
           "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(art, CFG)
    ebc = art.get("events_by_company") or {}
    ctx = art.get("context_events_by_company") or {}
    check(ebc.get("Vale") == [], f"events_by_company['Vale'] == [] (obtido {ebc.get('Vale')})")
    check("recuperacao_judicial" in [e["event_id"] for e in (ctx.get("Vale") or [])],
          "context_events_by_company['Vale'] contém a RJ da Samarco")
    check("recuperacao_judicial" in (ebc.get("Samarco Mineração") or []),
          "events_by_company['Samarco Mineração'] contém recuperacao_judicial")
    check(all(not e.get("scoreable", False) for e in (ctx.get("Vale") or [])),
          "evento de contexto da Vale é scoreable=False")


def t05_artigo_direto_e_misto():
    print("\n[5] Filing direto e filing misto")
    d = {"title": "Vale — 6-K: Vale conclui emissão de dívida", "summary": "", "url": "u3",
         "filing_company": "Vale", "provenance": "EDGAR", "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(d, CFG)
    check((d.get("events_by_company") or {}).get("Vale"),
          f"evento direto preservado p/ Vale: {(d.get('events_by_company') or {}).get('Vale')}")
    m = {"title": ("Vale — 6-K: rating rebaixado pela Fitch e atualização sobre "
                   "plano de recuperação judicial da Samarco"), "summary": "", "url": "u4",
         "filing_company": "Vale", "provenance": "EDGAR", "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(m, CFG)
    ebc, ctx = (m.get("events_by_company") or {}), (m.get("context_events_by_company") or {})
    check(ebc.get("Vale") == ["rebaixamento_rating"],
          f"misto: Vale fica SÓ com rebaixamento_rating (obtido {ebc.get('Vale')})")
    check([e["event_id"] for e in (ctx.get("Vale") or [])] == ["recuperacao_judicial"],
          "misto: RJ da Samarco fica somente em contexto da Vale")


def t06_elegibilidade_companhia_listada():
    print("\n[6] Elegibilidade de companhia listada")
    c = {"name": "Ford Motor", "country": "EUA", "ticker": "F", "listing": "NYSE",
         "asset_class": "listada"}
    ok, mot = rd.edgar_eligible_reason(c)
    check(ok, f"companhia listada permanece elegível ({mot})")
    n = sum(1 for x in CFG["watchlist"] if rd.edgar_eligible(x))
    check(n == 32, f"universo elegível preservado em 32 (obtido {n}) → score de produção idêntico")


def t07_etf_nao_e_companhia():
    print("\n[7] ETF não tratado como companhia comum")
    etf = {"name": "ETF X", "country": "EUA", "ticker": "SPY", "asset_class": "etf",
           "vehicle_kind": "etf"}
    ok, mot = rd.edgar_eligible_reason(etf)
    check(not ok, "ETF com ticker NÃO é elegível sem mapeamento de entidade")
    check("sec_entity" in mot, "motivo cita a exigência de sec_entity")


def t08_fundo_forms_proprios():
    print("\n[8] Fundo com forms próprios")
    f = {"name": "Fundo Y", "asset_class": "fundo", "vehicle_kind": "fundo",
         "sec_entity": {"status": "fundo_com_forms_proprios", "cik": "0001234567"}}
    forms = rd.edgar_forms_for(f, rd._normalize_edgar_forms(None))
    check(rd.edgar_eligible(f), "fundo que arquiva na SEC é elegível via mapeamento")
    check("N-CSR" in forms and "8-K" not in forms,
          f"usa forms próprios de fundo, não a allowlist corporativa ({sorted(forms)[:3]}…)")


def t09_marca_gestora_vs_controladora():
    print("\n[9] Marca de gestora × controladora")
    g = {"name": "Jp Morgan Asset", "asset_class": "gestora_fundo", "vehicle_kind": "gestora",
         "country": "EUA", "ticker": "JPM"}
    ok, mot = rd.edgar_eligible_reason(g)
    check(not ok, "gestora com ticker da CONTROLADORA não é elegível automaticamente")
    check("controladora" in mot.lower(), "motivo explica que filings da controladora não são evento direto")
    b = {"name": "Blackstone", "asset_class": "gestora_fundo", "vehicle_kind": "gestora",
         "sec_entity": {"status": "correspondencia_direta", "ticker": "BX"}}
    check(rd.edgar_eligible(b), "correspondencia_direta explícita habilita a gestora")


def t10_cik_incorreto():
    print("\n[10] CIK incorreto")
    import edgar_audit_4h2 as ea
    data = {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-07-01"],
                                   "accessionNumber": ["0000000000-26-000001"],
                                   "primaryDocument": ["d.htm"], "primaryDocDescription": ["x"]}}}
    arts = rd._edgar_articles_from_submissions(data, "Empresa Errada", "0000000000",
                                              {"8-K"}, 0)
    check(arts and arts[0]["filing_company"] == "Empresa Errada",
          "filing_company reflete o emissor consultado (rastreável se o CIK estiver errado)")
    check(arts[0].get("forced_companies") is None,
          "artigo EDGAR NÃO usa forced_companies (não força sujeito)")


def t11_cik_nao_resolvido():
    print("\n[11] CIK não resolvido")
    c = {"name": "Sem Ticker", "country": "EUA", "asset_class": "listada"}
    ok, mot = rd.edgar_eligible_reason(c)
    check(not ok and "sem ticker" in mot.lower(), f"sem ticker → inelegível com motivo explícito")


def t12_accession_dedup():
    print("\n[12] Deduplicação por accession")
    f = [{"emissor": "Ford Motor", "accession_number": "A-1", "event_id": ""},
         {"emissor": "Ford Motor", "accession_number": "A-1", "event_id": ""}]
    rows = sh.edgar_dedup_audit(rd, CFG, {"articles": {}}, f, outdir="/tmp/_t12")
    check(any(r["acao"] == "descartar_duplicata_accession" for r in rows),
          "segunda ocorrência do mesmo accession é descartada")
    check(all(r["score_incremental"] == 0 for r in rows), "score incremental = 0 em todas as linhas")


def t13_edgar_x_google_news():
    print("\n[13] EDGAR × Google News (corroboração, não nova ocorrência)")
    hist = {"articles": {"https://news.example/vale-rj": {
        "events_by_company": {"Vale": ["recuperacao_judicial"]},
        "source": "Google News"}}}
    rows = sh.edgar_dedup_audit(rd, CFG, hist,
                               [{"emissor": "Vale", "accession_number": "B-1",
                                 "event_id": "recuperacao_judicial"}], outdir="/tmp/_t13")
    check(rows[0]["matched_existing_occurrence"] == "sim", "filing casa com ocorrência de mídia já existente")
    check(rows[0]["acao"] == "corroborar_ocorrencia_existente", "ação = corroborar (não criar segunda ocorrência)")
    check(rows[0]["score_incremental"] == 0, "não infla score")


def t14_edgar_x_ri():
    print("\n[14] EDGAR × RI")
    hist = {"articles": {"https://ri.example/fato": {
        "events_by_company": {"Cemex": ["rebaixamento_rating"]}, "source": "RI Cemex"}}}
    rows = sh.edgar_dedup_audit(rd, CFG, hist,
                               [{"emissor": "Cemex", "accession_number": "C-1",
                                 "event_id": "rebaixamento_rating"}], outdir="/tmp/_t14")
    check(rows[0]["matched_source"].startswith("RI"), "corrobora item vindo do RI")
    check(rows[0]["score_incremental"] == 0, "sem score incremental")


def t15_filing_informativo_sem_score():
    print("\n[15] Filing informativo não pontua")
    fake = [{"emissor": "Ford Motor", "formulario": "8-K", "data": "2026-07-01",
             "accession_number": "D-1", "descricao": "Current report",
             "url_direta": "https://sec.gov/x"}]
    r = sh.edgar_shadow_run(rd, CFG, outdir="/tmp/_t15", dry={"filings": fake})
    cl = r["classificacao"]
    check(cl and cl[0]["categoria_filing"] == "informativo", "8-K genérico → categoria informativo")
    check(all(x["pontuaria"] == "não" for x in cl), "nenhuma linha pontua")
    check(all(s["score_simulado"] == 0 for s in r["score"]) if r["score"] else True,
          "score simulado 0 para filing informativo")


def t16_17_shadow_nao_altera_historico_nem_publica():
    print("\n[16-17] Shadow não altera histórico nem publica")
    hist = {"articles": {"u": {"events_by_company": {"Vale": ["default"]}}}, "run_count": 52}
    antes = json.dumps(hist, sort_keys=True)
    idx_antes = Path("index.html").exists()
    fake = [{"emissor": "Vale", "formulario": "6-K", "data": "2026-07-01",
             "accession_number": "E-1", "descricao": "plano de recuperação judicial da Samarco",
             "url_direta": "https://sec.gov/y"}]
    sh.edgar_shadow_run(rd, CFG, outdir="/tmp/_t16", dry={"filings": fake})
    sh.edgar_dedup_audit(rd, CFG, hist, fake, outdir="/tmp/_t16")
    check(json.dumps(hist, sort_keys=True) == antes, "histórico em memória inalterado (byte-a-byte)")
    check(Path("index.html").exists() == idx_antes, "shadow não criou/publicou index.html")


def t18_scoring_flag_false():
    print("\n[18] Flag de scoring false")
    c = dict(CFG)
    c["international_official_sources_enabled"] = True
    c["official_sources"] = copy.deepcopy(CFG.get("official_sources") or {})
    c["official_sources"].setdefault("EUA", {})["enabled"] = True
    c["edgar_scoring_enabled"] = False
    check(rd.edgar_collection_enabled(c) is True, "coleta LIGADA (fontes=true)")
    check(rd.edgar_scoring_enabled(c) is False, "scoring DESLIGADO → shadow mode")
    c["edgar_scoring_enabled"] = True
    check(rd.edgar_scoring_enabled(c) is True, "scoring liga só com as duas flags + scoring=true")


def t19_idempotencia_duas_execucoes():
    print("\n[19] Idempotência entre duas execuções")
    fake = [{"emissor": "Nubank (Nu Holdings)", "formulario": "6-K", "data": "2026-07-10",
             "accession_number": "F-1", "descricao": "Notice to the market",
             "url_direta": "https://sec.gov/z"}]
    r1 = sh.edgar_shadow_run(rd, CFG, outdir="/tmp/_t19a", dry={"filings": fake})
    r2 = sh.edgar_shadow_run(rd, CFG, outdir="/tmp/_t19b", dry={"filings": fake})
    k = ("filing_company", "form", "accession_number", "categoria_filing", "pontuaria")
    a = [{x: r[x] for x in k} for r in r1["classificacao"]]
    b = [{x: r[x] for x in k} for r in r2["classificacao"]]
    check(a == b, "duas execuções produzem classificação idêntica")
    rows = sh.edgar_dedup_audit(rd, CFG, {"articles": {}}, fake + fake, outdir="/tmp/_t19c")
    check(sum(1 for x in rows if x["acao"] == "descartar_duplicata_accession") == 1,
          "reexecução do mesmo accession é reconhecida como duplicata")


def t20_preservacao_config_producao():
    print("\n[20] Preservação do config de produção")
    prod = rd.load_config("config_risco.yaml")
    check(prod.get("international_official_sources_enabled") is None,
          "config de produção NÃO tem a flag global (permanece desligado)")
    check(((prod.get("official_sources") or {}).get("EUA") or {}).get("enabled") is None,
          "config de produção NÃO tem EUA.enabled")
    check(rd.edgar_collection_enabled(prod) is False, "coleta EDGAR desligada em produção")
    check(rd.fetch_edgar_filings(prod) == [], "fetch_edgar_filings(produção) == [] → score idêntico")
    cand = Path("config_risco_4h3a_candidato.yaml")
    if cand.exists():
        cc = rd.load_config(str(cand))
        check(cc.get("edgar_scoring_enabled") is False,
              "candidato 4H.3A tem edgar_scoring_enabled=false")


def main():
    print("=" * 68)
    print("TESTES 4H.3A — EDGAR shadow / flags / atribuição (Bloco I)")
    print("=" * 68)
    for fn in [t01_matriz_and, t02_dry_run_independe_flags, t03_source_diferente_subject,
               t04_vale_samarco_filing_oficial, t05_artigo_direto_e_misto,
               t06_elegibilidade_companhia_listada, t07_etf_nao_e_companhia,
               t08_fundo_forms_proprios, t09_marca_gestora_vs_controladora,
               t10_cik_incorreto, t11_cik_nao_resolvido, t12_accession_dedup,
               t13_edgar_x_google_news, t14_edgar_x_ri, t15_filing_informativo_sem_score,
               t16_17_shadow_nao_altera_historico_nem_publica, t18_scoring_flag_false,
               t19_idempotencia_duas_execucoes, t20_preservacao_config_producao]:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 68)
    print(f"RESULTADO 4H.3A: {ok}/{len(results)} checagens passaram")
    print("=" * 68)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
