# -*- coding: utf-8 -*-
"""Testes determinísticos da Fase 4H.2 — cobertura oficial e diagnóstico de
ausência de notícias. Nenhuma rede, nenhum dado de produção sobrescrito.

Cobre:
  * os 7 status de cobertura, cada um a partir de telemetria SINTÉTICA
    controlada (isolada, sem sobreposição de sinais);
  * a telemetria de cobertura nunca é lida por `event_ids_for`/score
    (prova: `coverage_diagnosis` não importa nem referencia essas funções,
    e a classificação usa apenas os campos de telemetria, nunca escreve em
    `events_by_company`);
  * EDGAR permanece sem score (`edgar_scoring_enabled` continua ausente/
    False no config de produção real);
  * Coazucar/subsidiárias não recebem score/cobertura transferida
    automaticamente da holding;
  * HTTP 200 sem extração real (`raw_articles=0`/`items_found=0` apesar de
    sucesso técnico) NUNCA conta como "retornou item relevante".
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coverage_diagnosis as cd


def _company(name="ACME", tier=1, country="Chile", ri_feeds=None, official=None,
            cik=None, ticker=None, listing=None):
    # country default != "Brasil" de propósito: isola os testes de RI_RSS/
    # GNEWS do sinal CVM/IPE (REGULADOR_LOCAL só é "configurado" para
    # emissores brasileiros) — os testes de CVM têm suíte própria.
    return {
        "name": name, "tier": tier, "country": country,
        "ri_feeds": ri_feeds or [], "official": official or {},
        "cik": cik, "ticker": ticker, "listing": listing,
    }


class TestSevenCoverageStatuses(unittest.TestCase):
    """Cada teste isola UM status com telemetria sintética limpa (sem
    sobreposição de sinais de outro status)."""

    def test_no_validated_official_source(self):
        # nenhuma fonte oficial configurada: sem ri_feeds/official.rss/news,
        # sem CIK/ticker SEC, país não-Brasil (sem CVM/IPE aplicável).
        c = _company(country="Argentina")
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 5,
                      "errors": 0, "eventos_classificados": 0}
        rec = cd.classify_company_coverage(c, search_tel, {}, cvm_status=None)
        self.assertEqual(rec["coverage_status"], cd.NO_VALIDATED_OFFICIAL_SOURCE)

    def test_source_configured_not_executed(self):
        # fonte oficial configurada (RI RSS), mas NADA foi tentado nesta
        # execução — nem a busca genérica, nem o RI.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        rec = cd.classify_company_coverage(c, None, {"RI_RSS": {}})
        self.assertEqual(rec["coverage_status"], cd.SOURCE_CONFIGURED_NOT_EXECUTED)

    def test_collection_failure(self):
        # única fonte configurada (GNEWS, sempre configurada) foi tentada e
        # falhou tecnicamente em todas as queries.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 2, "success": 0, "raw_articles": 0,
                      "errors": 2, "eventos_classificados": 0}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": False,
                                             "items_found": 0}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel)
        self.assertEqual(rec["coverage_status"], cd.COLLECTION_FAILURE)

    def test_partial_coverage(self):
        # GNEWS rodou com sucesso; RI_RSS configurado mas NÃO tentado.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 3,
                      "errors": 0, "eventos_classificados": 1}
        official_tel = {"RI_RSS": {}}  # ACME nem aparece: não tentado
        rec = cd.classify_company_coverage(c, search_tel, official_tel)
        self.assertEqual(rec["coverage_status"], cd.PARTIAL_COVERAGE)

    def test_fallback_only(self):
        # GNEWS achou itens; RI_RSS (única fonte oficial configurada) rodou
        # com sucesso técnico mas devolveu 0 itens.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 4,
                      "errors": 0, "eventos_classificados": 1}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 0}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel)
        self.assertEqual(rec["coverage_status"], cd.FALLBACK_ONLY)

    def test_no_relevant_news_after_successful_run(self):
        # tudo tentado, tudo sucesso técnico, ZERO itens em qualquer fonte.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 0,
                      "errors": 0, "eventos_classificados": 0}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 0}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel)
        self.assertEqual(rec["coverage_status"], cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN)

    def test_only_informational_found(self):
        # itens encontrados (oficial + geral), mas 0 eventos pontuáveis.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 2,
                      "errors": 0, "eventos_classificados": 0}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 3}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel, scored_events=0)
        self.assertEqual(rec["coverage_status"], cd.ONLY_INFORMATIONAL_FOUND)

    def test_coverage_ok_events_found_is_not_one_of_the_seven_but_reported(self):
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 2,
                      "errors": 0, "eventos_classificados": 1}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 1}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel, scored_events=1)
        self.assertEqual(rec["coverage_status"], cd.COVERAGE_OK_EVENTS_FOUND)
        self.assertNotIn(cd.COVERAGE_OK_EVENTS_FOUND, cd.COVERAGE_STATUSES)
        self.assertEqual(len(cd.COVERAGE_STATUSES), 7)


class TestHttp200WithoutExtraction(unittest.TestCase):
    def test_http_200_without_articles_is_not_relevant_item(self):
        # sucesso técnico (queries>0, success>0 -> HTTP 200) mas 0 artigos
        # extraídos: NÃO pode virar FALLBACK_ONLY nem ONLY_INFORMATIONAL —
        # tem que cair em NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN.
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 0,
                      "errors": 0, "status_codes": [200], "eventos_classificados": 0}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 0, "status_code": 200}}}
        rec = cd.classify_company_coverage(c, search_tel, official_tel)
        self.assertEqual(rec["coverage_status"], cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN)
        gnews = next(s for s in rec["sources"] if s["source"] == "GNEWS")
        self.assertTrue(gnews["technical_success"])
        self.assertEqual(gnews["items_found"], 0)


class TestCoverageNeverInfluencesScore(unittest.TestCase):
    def test_coverage_diagnosis_module_never_calls_or_writes_score_functions(self):
        # Módulo pode CITAR os nomes em docstrings/comentários (documentação
        # das invariantes), mas nunca pode CHAMAR essas funções nem escrever
        # nas estruturas de score — checa uso funcional, não a palavra solta.
        import inspect
        src = inspect.getsource(cd)
        for forbidden_call in ("event_ids_for(", "build_evolution(",
                              'events_by_company["', "events_by_company['",
                              "events_by_company[c", "events_by_company.setdefault"):
            self.assertNotIn(forbidden_call, src,
                             f"coverage_diagnosis.py não deve usar '{forbidden_call}'")

    def test_classification_is_pure_read_of_telemetry(self):
        c = _company(ri_feeds=["https://ri.acme.com/feed"])
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 2,
                      "errors": 0, "eventos_classificados": 1}
        official_tel = {"RI_RSS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 1}}}
        before = dict(search_tel)
        cd.classify_company_coverage(c, search_tel, official_tel, scored_events=1)
        self.assertEqual(search_tel, before, "classificação não pode mutar a telemetria")


class TestEdgarStaysUnscored(unittest.TestCase):
    def test_edgar_scoring_enabled_absent_or_false_in_production_config(self):
        import yaml
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_risco.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        edgar_cfg = ((cfg.get("official_sources") or {}).get("EUA") or {})
        self.assertFalse(bool(edgar_cfg.get("edgar_scoring_enabled")),
                         "edgar_scoring_enabled deve permanecer false/ausente")

    def test_edgar_source_record_never_marked_as_scoring(self):
        c = _company(country="EUA", ticker="ACME", listing="NYSE")
        official_tel = {"EDGAR": {"ACME": {"attempted": True, "cik_resolved": True,
                                           "success": True, "filings_found": 3}}}
        rec = cd.classify_company_coverage(c, None, official_tel)
        edgar_src = next(s for s in rec["sources"] if s["source"] == "EDGAR")
        self.assertIn("items_found", edgar_src)
        # o record de cobertura nunca tem campo de score/peso.
        self.assertNotIn("score", edgar_src)
        self.assertNotIn("weight", edgar_src)


class TestCoazucarSubsidiariesNoAutoScoreTransfer(unittest.TestCase):
    def test_priority_companies_marks_subsidiaries_distinctly(self):
        cfg = {
            "watchlist": [
                {"name": "Coazucar", "tier": 2, "country": "Peru",
                 "related_entities": [
                     {"entity_name": "Casa Grande S.A.A.", "relationship": "subsidiary"},
                     {"entity_name": "Cartavio S.A.A.", "relationship": "subsidiary"},
                 ]},
            ]
        }
        rows = cd.priority_companies(cfg)
        holding = next(r for r in rows if r["name"] == "Coazucar")
        subs = [r for r in rows if r.get("is_subsidiary")]
        self.assertFalse(holding.get("is_subsidiary"))
        self.assertEqual({s["name"] for s in subs},
                         {"Casa Grande S.A.A.", "Cartavio S.A.A."})
        for s in subs:
            self.assertEqual(s["parent_company"], "Coazucar")

    def test_subsidiary_has_no_own_official_source_and_no_score_field(self):
        cfg = {
            "watchlist": [
                {"name": "Coazucar", "tier": 2, "country": "Peru",
                 "related_entities": [
                     {"entity_name": "Casa Grande S.A.A.", "relationship": "subsidiary"},
                 ]},
            ]
        }
        run_meta = {"international_search_execution": {}, "official_source_execution": {}}
        rows = cd.diagnose_coverage(cfg, run_meta)
        sub = next(r for r in rows if r["company"] == "Casa Grande S.A.A.")
        self.assertEqual(sub["coverage_status"], cd.NO_VALIDATED_OFFICIAL_SOURCE)
        for k in rec_keys_that_must_not_exist():
            self.assertNotIn(k, sub)


def rec_keys_that_must_not_exist():
    return ("score", "score_delta", "weight", "peso")


class TestRetroactiveDiagnosisOnRealArtifacts(unittest.TestCase):
    """Roda o diagnóstico sobre os artefatos REAIS do repositório
    (`config_risco.yaml` / `run_meta.json`) sem nenhuma rede — só valida
    que o pipeline completo corre e produz um resultado íntegro."""

    def test_runs_on_real_config_and_produces_seven_or_eight_labels_only(self):
        base = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(base, "config_risco.yaml")
        run_meta_path = os.path.join(base, "run_meta.json")
        if not (os.path.exists(cfg_path) and os.path.exists(run_meta_path)):
            self.skipTest("config_risco.yaml/run_meta.json não presentes neste ambiente")
        import yaml
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        with open(run_meta_path, encoding="utf-8") as f:
            run_meta = json.load(f)
        rows = cd.diagnose_coverage(cfg, run_meta)
        self.assertGreater(len(rows), 0)
        allowed = set(cd.COVERAGE_STATUSES) | {cd.COVERAGE_OK_EVENTS_FOUND}
        for r in rows:
            self.assertIn(r["coverage_status"], allowed)
        # Tier 1 real do YAML deve estar todo presente no diagnóstico.
        tier1_names = {c["name"] for c in cfg["watchlist"] if c.get("tier") == 1}
        diagnosed_names = {r["company"] for r in rows}
        self.assertTrue(tier1_names.issubset(diagnosed_names))


# ══════════════════════════════════════════════════════════════════════
# Fase 4H.2 (fechamento) — 16 testes adicionais exigidos pelo coordenador,
# além dos 16 já existentes acima. Cobrem: telemetria CVM real por
# emissor, fontes peruanas (SMV/BVL/homepage), rejeição de "HTTP 200 sem
# conteúdo", subsidiárias sem transferência de score, reconciliação
# dashboard/CSV, recálculo de status, renderização da seção recolhível, e
# ausência de impacto em event_ids_for/score.
# ══════════════════════════════════════════════════════════════════════

def _cvm_row(emissor, status="filiante_cvm", protocolos=5, ultima="2026-06-30",
            tipo_match="codigo_cvm", id_usado="1234", motivo="", n_cand=1,
            confianca="alta"):
    return {"emissor": emissor, "status": status, "protocolos_no_ano": protocolos,
           "ultima_entrega": ultima, "tipo_match": tipo_match,
           "identificador_usado": id_usado, "motivo_decisao": motivo,
           "n_candidatos": n_cand, "confianca_match": confianca,
           "companhia_casada": emissor.upper() + " S.A.", "codigo_cvm_casado": id_usado,
           "cnpj_casado": "", "asset_class": "listada", "grupo": "listed_companies", "tier": 1}


class Test01CvmTelemetryReconciledPerCompany(unittest.TestCase):
    def test_each_company_gets_its_own_identifier_and_counts(self):
        rows = [_cvm_row("Ambev", protocolos=57, id_usado="23264", ultima="2026-07-30"),
               _cvm_row("Vale", protocolos=185, id_usado="4170", ultima="2026-07-31")]
        tel = cd.build_cvm_telemetry(rows)
        self.assertNotEqual(tel["Ambev"]["identificador_usado"], tel["Vale"]["identificador_usado"])
        self.assertNotEqual(tel["Ambev"]["documentos_retornados"], tel["Vale"]["documentos_retornados"])
        self.assertEqual(tel["Ambev"]["company_name"], "Ambev")
        self.assertEqual(tel["Ambev"]["source_name"], "CVM")
        self.assertEqual(tel["Ambev"]["source_type"], "regulator")


class Test02CvmQueryWithoutDocuments(unittest.TestCase):
    def test_esperado_filiante_sem_protocolo_gives_zero_accepted_and_error_note(self):
        rows = [_cvm_row("Eletrobras", status="esperado_filiante_sem_protocolo_no_ano",
                        protocolos=0, ultima="")]
        tel = cd.build_cvm_telemetry(rows)
        rec = tel["Eletrobras"]
        self.assertEqual(rec["documentos_retornados"], 0)
        self.assertEqual(rec["documentos_aceitos"], 0)
        self.assertTrue(rec["tentativa_realizada"])
        self.assertNotEqual(rec["erro"], "")


class Test03CvmQueryWithDocuments(unittest.TestCase):
    def test_filiante_cvm_gives_accepted_documents_and_last_date(self):
        rows = [_cvm_row("Petrobras", status="filiante_cvm", protocolos=194,
                        ultima="2026-07-31", id_usado="9512")]
        tel = cd.build_cvm_telemetry(rows)
        rec = tel["Petrobras"]
        self.assertEqual(rec["documentos_retornados"], 194)
        self.assertEqual(rec["documentos_aceitos"], 194)
        self.assertEqual(rec["data_ultimo_documento"], "2026-07-31")
        self.assertNotEqual(rec["ultimo_sucesso"], "")


class Test04CvmErrorDoesNotContaminateOthers(unittest.TestCase):
    def test_one_ambiguous_company_does_not_affect_siblings(self):
        rows = [_cvm_row("BRF", status="revisar", protocolos=12, tipo_match="alias",
                        motivo="termo curto"),
               _cvm_row("Suzano", status="filiante_cvm", protocolos=81, id_usado="13986")]
        tel = cd.build_cvm_telemetry(rows)
        self.assertNotEqual(tel["BRF"]["erro"], "")
        self.assertEqual(tel["BRF"]["documentos_aceitos"], 0)
        self.assertEqual(tel["Suzano"]["erro"], "")
        self.assertEqual(tel["Suzano"]["documentos_aceitos"], 81)
        # o erro de BRF não vaza para o registro de Suzano
        self.assertNotIn("BRF", tel["Suzano"]["erro"])


class Test05SmvBvlSourceExecuted(unittest.TestCase):
    def test_bvl_candidate_marked_as_exchange_and_executed(self):
        cands = cd.PERU_SOURCE_VALIDATION["Yura"]
        bvl = next(c for c in cands if c["source_type"] == "exchange")
        self.assertEqual(bvl["http_status"], 200)
        self.assertIn("bvl.com.pe", bvl["url_configurada"])
        # HTTP 200 sozinho NÃO valida conteúdo (ver teste 08).
        self.assertFalse(bvl["conteudo_validado"])


class Test06PeruSourceConfiguredNotExecuted(unittest.TestCase):
    def test_smv_hechos_de_importancia_not_a_stable_url_documented_as_gap(self):
        cands = cd.PERU_SOURCE_VALIDATION["Trupal"]
        smv = next(c for c in cands if c["source_type"] == "regulator")
        self.assertFalse(smv["entidade_confirmada"])
        self.assertIn("token", smv["bloqueio_tecnico"].lower() + smv["nota_validacao"].lower())


class Test07GenericHomepageRejected(unittest.TestCase):
    def test_coazucar_homepage_200_but_zero_items_is_fallback_only_not_confirmed(self):
        company = {"name": "Coazucar", "tier": 2, "country": "Peru", "official": {}}
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 3,
                      "errors": 0, "eventos_classificados": 0}
        rec = cd.classify_company_coverage(company, search_tel, {},
                                           peru_validation=cd.PERU_SOURCE_VALIDATION["Coazucar"])
        ri_news = next(s for s in rec["sources"] if s["source"] == "RI_NEWS")
        self.assertTrue(ri_news["technical_success"])   # HTTP 200 real
        self.assertEqual(ri_news["items_found"], 0)      # mas ZERO itens
        self.assertFalse(ri_news["validated"])
        self.assertNotEqual(rec["coverage_status"], cd.NO_VALIDATED_OFFICIAL_SOURCE)
        self.assertIn(rec["coverage_status"], (cd.FALLBACK_ONLY,
                                               cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN))


class Test08Http200IncompatibleContent(unittest.TestCase):
    def test_http_200_without_content_validation_never_counts_as_relevant(self):
        for cands in cd.PERU_SOURCE_VALIDATION.values():
            for c in cands:
                if c.get("http_status") == 200:
                    self.assertFalse(c["conteudo_validado"],
                                     f"{c['source_name']}: HTTP 200 não pode implicar "
                                     f"conteudo_validado=True sem extração demonstrada")


class Test09OfficialSourceNoRecentItems(unittest.TestCase):
    def test_official_source_success_zero_items_recent(self):
        official_tel = {"RI_NEWS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 0}}}
        c = _company(ri_feeds=[], official={"news": "https://ri.acme.com/news"})
        rec = cd.classify_company_coverage(c, {"searched": True, "queries": 1, "success": 1,
                                               "raw_articles": 0, "errors": 0,
                                               "eventos_classificados": 0}, official_tel)
        ri_news = next(s for s in rec["sources"] if s["source"] == "RI_NEWS")
        self.assertEqual(ri_news["items_found"], 0)
        self.assertEqual(rec["coverage_status"], cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN)


class Test10OfficialItemOnlyInformational(unittest.TestCase):
    def test_official_item_found_but_not_scored(self):
        official_tel = {"RI_NEWS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 2}}}
        c = _company(ri_feeds=[], official={"news": "https://ri.acme.com/news"})
        rec = cd.classify_company_coverage(c, {"searched": True, "queries": 1, "success": 1,
                                               "raw_articles": 1, "errors": 0,
                                               "eventos_classificados": 0}, official_tel,
                                           scored_events=0)
        self.assertEqual(rec["coverage_status"], cd.ONLY_INFORMATIONAL_FOUND)


class Test11RelevantEventInOfficialSource(unittest.TestCase):
    def test_official_item_found_and_scored_is_not_an_absence_status(self):
        official_tel = {"RI_NEWS": {"ACME": {"attempted": True, "success": True,
                                             "items_found": 2}}}
        c = _company(ri_feeds=[], official={"news": "https://ri.acme.com/news"})
        rec = cd.classify_company_coverage(c, {"searched": True, "queries": 1, "success": 1,
                                               "raw_articles": 1, "errors": 0,
                                               "eventos_classificados": 1}, official_tel,
                                           scored_events=1)
        self.assertEqual(rec["coverage_status"], cd.COVERAGE_OK_EVENTS_FOUND)
        self.assertNotIn(rec["coverage_status"], cd.COVERAGE_STATUSES)  # não é status de ausência


class Test12SubsidiaryOwnSourceNoScoreTransfer(unittest.TestCase):
    def test_subsidiary_validation_dict_exists_and_never_carries_score(self):
        for name, cands in cd.COAZUCAR_SUBSIDIARY_SOURCE_VALIDATION.items():
            for c in cands:
                self.assertNotIn("score", c)
                self.assertNotIn("score_transferido", c)
        cfg = {"watchlist": [
            {"name": "Coazucar", "tier": 2, "country": "Peru",
             "related_entities": [{"entity_name": "Casa Grande S.A.A.",
                                   "relationship": "subsidiary"}]}]}
        run_meta = {"international_search_execution": {}, "official_source_execution": {}}
        rows = cd.diagnose_coverage(cfg, run_meta)
        sub = next(r for r in rows if r["company"] == "Casa Grande S.A.A.")
        holding = next(r for r in rows if r["company"] == "Coazucar")
        self.assertNotEqual(sub["coverage_status"], "score_herdado")
        self.assertTrue(sub["is_subsidiary"])
        self.assertFalse(holding["is_subsidiary"])


class Test13DashboardAndCsvSameTotals(unittest.TestCase):
    def test_reconciliation_passes_for_matching_data_and_fails_for_tampered_csv(self):
        rows = [
            cd.classify_company_coverage(_company("A"), None, {}),
            cd.classify_company_coverage(_company("B"), None, {}),
        ]
        tmp = tempfile.mkdtemp()
        cov_csv = cd.export_auditoria_cobertura_emissores_csv(
            rows, os.path.join(tmp, "auditoria_cobertura_emissores.csv"))
        src_csv = cd.export_auditoria_cobertura_fontes_csv(
            rows, os.path.join(tmp, "auditoria_cobertura_fontes.csv"))
        cd.assert_exports_reconcile(rows, cov_csv, src_csv)  # não deve levantar

        # agora adultera o CSV e confirma que a reconciliação QUEBRA
        with open(cov_csv, "a", encoding="utf-8-sig") as f:
            f.write("Fantasma,1,Chile,False,,NO_VALIDATED_OFFICIAL_SOURCE,x,y\n")
        with self.assertRaises(cd.ReconciliationError):
            cd.assert_exports_reconcile(rows, cov_csv, src_csv)


class Test14StatusRecalculatedAfterNewTelemetry(unittest.TestCase):
    def test_status_changes_when_cvm_telemetry_is_added(self):
        c = _company("BancoX", country="Brasil")
        search_tel = {"searched": True, "queries": 1, "success": 1, "raw_articles": 2,
                      "errors": 0, "eventos_classificados": 0}
        official_tel = {"RI_NEWS": {"BancoX": {"attempted": True, "success": True,
                                               "items_found": 2}}}
        before = cd.classify_company_coverage(c, search_tel, official_tel)
        cvm_tel = cd.build_cvm_telemetry([_cvm_row("BancoX", status="filiante_cvm",
                                                    protocolos=10)])
        after = cd.classify_company_coverage(c, search_tel, official_tel,
                                             cvm_telemetry=cvm_tel)
        # antes: sem telemetria CVM, REGULADOR_LOCAL nem aparece como
        # configurado (heurística por país só ativa p/ Brasil sem
        # telemetria explícita cai em 'configured=is_brasil' mas
        # 'attempted=False') — depois: CVM real muda o quadro de fontes.
        reg_before = next(s for s in before["sources"] if s["source"] == "REGULADOR_LOCAL")
        reg_after = next(s for s in after["sources"] if s["source"] == "REGULADOR_LOCAL")
        self.assertFalse(reg_before["attempted"])
        self.assertTrue(reg_after["attempted"])
        self.assertTrue(reg_after["technical_success"])


class Test15CollapsibleSectionRenders(unittest.TestCase):
    def test_template_contains_coverage_section_markers(self):
        base = os.path.dirname(os.path.abspath(__file__))
        tpl_path = os.path.join(base, "template_risco.html.j2")
        with open(tpl_path, encoding="utf-8") as f:
            tpl = f.read()
        self.assertIn("coverageBlockHtml", tpl)
        self.assertIn("coverage-block-cov", tpl)
        self.assertIn("Cobertura das fontes", tpl)
        self.assertIn("renderCoverageExecSummary", tpl)
        self.assertIn("coverage_diagnosis_summary", tpl)
        # nunca ao lado do score como severidade
        self.assertIn("não é severidade de risco e não altera o score", tpl)

    def test_render_html_attaches_coverage_diagnosis_when_run_meta_given(self):
        import risk_dashboard as rd
        cfg = {"dashboard": {"title": "t", "windows": [7], "default_window": 7},
              "scoring": {"attention_threshold": 80},
              "watchlist": [{"name": "ACME", "tier": 1, "country": "Chile"}]}
        data_by_window = {"7": {"evolution": [{"company": "ACME", "status": "monitorar",
                                              "total_score": 0, "timeline": [], "events": [],
                                              "breakdown": [], "spark_points": "",
                                              "first_date": "", "last_date": "", "tier": 1}],
                               "feed": []}}
        run_meta = {"international_search_execution": {
            "ACME": {"searched": True, "queries": 1, "success": 1, "raw_articles": 0,
                     "errors": 0, "eventos_classificados": 0}},
            "official_source_execution": {}}
        html = rd.render_html(data_by_window, cfg, demo=True, changes={},
                              payload_thresholds={}, run_meta=run_meta)
        self.assertIn("coverage_diagnosis", html)


class Test16NoImpactOnEventIdsForOrScore(unittest.TestCase):
    def test_render_html_coverage_wiring_does_not_touch_score_fields(self):
        import risk_dashboard as rd
        cfg = {"dashboard": {"title": "t", "windows": [7], "default_window": 7},
              "scoring": {"attention_threshold": 80},
              "watchlist": [{"name": "ACME", "tier": 1, "country": "Chile"}]}
        row = {"company": "ACME", "status": "monitorar", "total_score": 42, "timeline": [],
              "events": [], "breakdown": [], "spark_points": "", "first_date": "",
              "last_date": "", "tier": 1}
        data_by_window = {"7": {"evolution": [row], "feed": []}}
        run_meta = {"international_search_execution": {}, "official_source_execution": {}}
        rd.render_html(data_by_window, cfg, demo=True, changes={}, payload_thresholds={},
                       run_meta=run_meta)
        self.assertEqual(row["total_score"], 42, "coverage_diagnosis não pode alterar total_score")
        self.assertEqual(row["status"], "monitorar", "coverage_diagnosis não pode alterar status")

    def test_coverage_diagnosis_module_still_never_touches_event_ids_for(self):
        import inspect
        src = inspect.getsource(cd)
        self.assertNotIn("event_ids_for(", src)
        self.assertNotIn("build_evolution(", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
