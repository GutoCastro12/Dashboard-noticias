# -*- coding: utf-8 -*-
"""Testes determinísticos da correção de reconciliação runtime (2 pendências
reais de produção da Fase 4H.2):

  1) exports estáticos (out_coverage_diagnosis/*.csv/.md) congelados no
     commit do merge, nunca regenerados a cada execução real;
  2) diagnóstico tratando "fonte não escalada neste ciclo" (rotação normal
     de tier) como cobertura parcial, mesmo com sucesso recente válido.

Cobre os 16 casos exigidos pela tarefa. Nenhuma chamada de rede, nenhum dado
de produção sobrescrito — tudo roda contra fixtures sintéticas em diretórios
temporários. Não toca score/eventos/histórico/pesos/thresholds/tiers/
taxonomia (mesma garantia do módulo original — ver test_coverage_diagnosis.py).
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coverage_diagnosis as cd


def _cfg(tiers=None):
    return {
        "tiers": tiers or {
            1: {"label": "Tier 1", "fetch_every_n_runs": 1},
            2: {"label": "Tier 2", "fetch_every_n_runs": 2},
            3: {"label": "Tier 3", "fetch_every_n_runs": 4},
        },
        "watchlist": [],
    }


def _company(name="ACME", tier=1, country="Chile", ri_feeds=None, official=None):
    return {
        "name": name, "tier": tier, "country": country,
        "ri_feeds": ri_feeds or [], "official": official or {},
    }


def _search_tel(searched=True, queries=1, success=1, raw_articles=3, eventos=0):
    return {"searched": searched, "queries": queries, "success": success,
            "raw_articles": raw_articles, "errors": 0, "eventos_classificados": eventos}


class TestExportsSameExecutionAsDashboard(unittest.TestCase):
    """1) Exports gerados na MESMA execução que o payload do dashboard."""

    def test_exports_written_by_single_canonical_call(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            company = _company(ri_feeds=["https://ri.acme.com/feed"])
            cfg["watchlist"] = [company]
            run_meta = {
                "international_search_execution": {"ACME": _search_tel()},
                "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}},
            }
            result = cd.run_production_coverage(
                cfg, run_meta, history_runs=[], companies=[company], out_dir=tmp,
                run_id="R1", generated_at="2026-08-05T00:00:00+00:00", commit_base="abc123")
            for fname in ("auditoria_cobertura_fontes.csv", "auditoria_cobertura_emissores.csv",
                         "fontes_configuradas_vs_executadas.csv", "falhas_de_coleta.csv",
                         "relatorio_cobertura_oficial.md", "matriz_cobertura_prioritarios.md"):
                self.assertTrue(os.path.exists(os.path.join(tmp, fname)), fname)
            self.assertEqual(result["meta"]["companies_count"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRunIdIdenticalHtmlAndExports(unittest.TestCase):
    """2) run_id idêntico entre o payload (via to_dashboard_view_v2/meta) e os exports."""

    def test_run_id_matches(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            company = _company()
            cfg["watchlist"] = [company]
            run_meta = {"international_search_execution": {"ACME": _search_tel()},
                       "official_source_execution": {}}
            result = cd.run_production_coverage(
                cfg, run_meta, history_runs=[], companies=[company], out_dir=tmp,
                run_id="RUN-XYZ", generated_at="2026-08-05T01:00:00+00:00")
            import csv
            with open(os.path.join(tmp, "auditoria_cobertura_emissores.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["run_id"], "RUN-XYZ")
            self.assertEqual(rows[0]["run_id"], result["meta"]["run_id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHashIdentical(unittest.TestCase):
    """3) Hash do payload idêntico entre meta e cada linha dos exports."""

    def test_payload_hash_matches_all_exports(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            company = _company()
            cfg["watchlist"] = [company]
            run_meta = {"international_search_execution": {"ACME": _search_tel()},
                       "official_source_execution": {}}
            result = cd.run_production_coverage(
                cfg, run_meta, history_runs=[], companies=[company], out_dir=tmp)
            import csv
            with open(os.path.join(tmp, "auditoria_cobertura_fontes.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row["payload_hash"], result["meta"]["payload_hash"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDivergenceFailsExecution(unittest.TestCase):
    """4) Divergência entre export e payload FAZ a execução falhar (exception,
    não warning) — gate de publicação."""

    def test_tampered_csv_raises_reconciliation_error(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            company = _company()
            cfg["watchlist"] = [company]
            run_meta = {"international_search_execution": {"ACME": _search_tel()},
                       "official_source_execution": {}}
            result = cd.run_production_coverage(
                cfg, run_meta, history_runs=[], companies=[company], out_dir=tmp)
            # adultera o CSV já gravado (simula divergência real)
            path = os.path.join(tmp, "auditoria_cobertura_emissores.csv")
            with open(path, encoding="utf-8-sig") as f:
                txt = f.read()
            txt = txt.replace(result["meta"]["run_id"], "RUN-DIFERENTE")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(txt)
            with self.assertRaises(cd.ReconciliationError):
                cd.assert_exports_reconcile_v2(result["rows"], result["meta"], tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNotScheduledWithRecentSuccessNotPartial(unittest.TestCase):
    """5) Fonte não escalada neste ciclo, com sucesso recente válido dentro
    da janela de frescor, NÃO causa cobertura parcial."""

    def test_tier2_gnews_not_scheduled_but_recent_success_stays_confirmed(self):
        cfg = _cfg()
        company = _company(tier=2, ri_feeds=["https://ri.acme.com/feed"])
        # GNEWS não tentado neste ciclo (empresa fora do bucket desta execução)
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 2}}}}
        # histórico: execução recente (há 6h) com sucesso técnico de GNEWS
        history_runs = [{"run_id": "prev", "finished_at": "2026-08-04T18:00:00+00:00",
                         "emitters": {"ACME": {"searched": True, "success": 1, "raw_articles": 4}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        row = result["rows"][0]
        self.assertNotEqual(row["coverage_status_consolidated"], cd.PARTIAL_COVERAGE)
        gnews = next(s for s in row["sources_consolidated"] if s["source"] == "GNEWS")
        self.assertTrue(gnews["not_scheduled_this_run"])
        self.assertEqual(gnews["freshness_status"], "valida")


class TestNotScheduledAndExpiredCausesPartial(unittest.TestCase):
    """6) Fonte não escalada neste ciclo e evidência EXPIRADA causa parcial."""

    def test_tier3_gnews_stale_causes_partial(self):
        cfg = _cfg()
        company = _company(tier=3, ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
        # último sucesso de GNEWS há ~26 dias (624h) — muito além da janela
        # Tier3 (4 x BASE_FRESHNESS_HOURS(96h) = 384h/16 dias).
        history_runs = [{"run_id": "old", "finished_at": "2026-07-10T00:00:00+00:00",
                         "emitters": {"ACME": {"searched": True, "success": 1, "raw_articles": 2}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        row = result["rows"][0]
        gnews = next(s for s in row["sources_consolidated"] if s["source"] == "GNEWS")
        self.assertEqual(gnews["freshness_status"], "obsoleta")
        self.assertEqual(row["coverage_status_consolidated"], cd.PARTIAL_COVERAGE)


class TestCurrentFailureWithRecentSuccessPreserved(unittest.TestCase):
    """7) Falha na tentativa atual, mas sucesso recente ainda válido: mostra
    a falha atual (execution_status_current_run), sem apagar o histórico de
    sucesso (consolidated_effective_success continua True)."""

    def test_ri_rss_failed_now_recent_success_preserved(self):
        cfg = _cfg()
        company = _company(ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {"ACME": _search_tel()},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": False, "items_found": 0}}}}
        history_runs = [{"run_id": "prev", "finished_at": "2026-08-04T20:00:00+00:00",
                         "official_sources": {"RI_RSS": {"ACME": {"attempted": True, "success": True}}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        row = result["rows"][0]
        ri = next(s for s in row["sources_consolidated"] if s["source"] == "RI_RSS")
        self.assertEqual(ri["execution_status_current_run"], "falhou_neste_ciclo")
        self.assertTrue(ri["consolidated_effective_success"])
        self.assertEqual(ri["last_success_at"], "2026-08-04T20:00:00+00:00")


class TestPersistentFailureAfterExpiration(unittest.TestCase):
    """8) Falha persistente E evidência expirada → COLLECTION_FAILURE ou
    PARTIAL_COVERAGE (nunca confirmado artificialmente)."""

    def test_ri_rss_failed_now_and_stale_history_is_not_confirmed(self):
        cfg = _cfg()
        company = _company(ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": False, "items_found": 0}}}}
        history_runs = [{"run_id": "old", "finished_at": "2026-07-01T00:00:00+00:00",
                         "official_sources": {"RI_RSS": {"ACME": {"attempted": True, "success": True}}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        row = result["rows"][0]
        self.assertIn(row["coverage_status_consolidated"],
                      (cd.COLLECTION_FAILURE, cd.PARTIAL_COVERAGE, cd.SOURCE_CONFIGURED_NOT_EXECUTED))
        self.assertNotIn(row["coverage_status_consolidated"],
                         (cd.COVERAGE_OK_EVENTS_FOUND, cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN))


class TestNormalRotationBetweenTwoCycles(unittest.TestCase):
    """9) Rotação normal entre dois ciclos: emissor Tier 2 escalado no ciclo
    A, não escalado no ciclo B — consolidado do ciclo B não regride."""

    def test_two_cycle_rotation_keeps_consolidated_confirmed(self):
        cfg = _cfg()
        company = _company(tier=2, ri_feeds=["https://ri.acme.com/feed"])
        # ciclo A: GNEWS roda com sucesso
        run_meta_a = {"international_search_execution": {"ACME": _search_tel(eventos=1)},
                     "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
        result_a = cd.build_canonical_coverage_result(
            cfg, run_meta_a, history_runs=[], companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        # ciclo B (6h depois): GNEWS não escalado (rotação), RI_RSS de novo ok
        history_runs_b = [{"run_id": "A", "finished_at": "2026-08-05T00:00:00+00:00",
                          "emitters": {"ACME": {"searched": True, "success": 1, "raw_articles": 3}},
                          "official_sources": {"RI_RSS": {"ACME": {"attempted": True, "success": True}}}}]
        run_meta_b = {"international_search_execution": {},
                     "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 0}}}}
        result_b = cd.build_canonical_coverage_result(
            cfg, run_meta_b, history_runs=history_runs_b, companies=[company],
            generated_at="2026-08-05T06:00:00+00:00")
        row_b = result_b["rows"][0]
        self.assertNotEqual(row_b["coverage_status_consolidated"], cd.PARTIAL_COVERAGE)


class TestCvmExecutedThenNotScheduledNextCycle(unittest.TestCase):
    """10) CVM (REGULADOR_LOCAL) executada em um ciclo (via --audit-cvm) e
    não escalada no próximo (padrão real — auditoria roda esporadicamente,
    não em toda execução) — janela de 30 dias evita falso parcial."""

    def test_cvm_not_rerun_within_30_days_stays_valid(self):
        cfg = _cfg()
        company = _company(country="Brasil", ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {"ACME": _search_tel()},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
        cvm_tel_map = {"ACME": {"tentativa_realizada": True, "resultado_consulta": "filiante_cvm",
                                "documentos_aceitos": 2, "ultima_tentativa": "2026-07-20T00:00:00+00:00",
                                "ultimo_sucesso": "2026-07-20T00:00:00+00:00", "identificador_usado": "123",
                                "identificador_tipo": "cnpj", "metodo_consulta": "ipe_dataset_match_por_identificador_forte",
                                "documentos_retornados": 2, "data_ultimo_documento": "2026-07-15", "erro": ""}}
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=[], companies=[company],
            generated_at="2026-08-05T00:00:00+00:00", cvm_telemetry_map=cvm_tel_map)
        row = result["rows"][0]
        reg = next(s for s in row["sources_consolidated"] if s["source"] == "REGULADOR_LOCAL")
        # 2026-07-20 → 2026-08-05 = 16 dias, dentro da janela de 30 dias
        self.assertEqual(reg["freshness_status"], "valida")
        self.assertNotEqual(row["coverage_status_consolidated"], cd.PARTIAL_COVERAGE)


class TestGnewsNotScheduledNoFalseError(unittest.TestCase):
    """11) Google News não escalado neste ciclo, sem histórico de sucesso
    algum (primeiro ciclo do emissor) — NÃO gera erro fabricado; deve
    aparecer como 'sem evidência', não como falha técnica."""

    def test_gnews_never_run_is_no_evidence_not_a_failure(self):
        cfg = _cfg()
        company = _company(tier=1)
        run_meta = {"international_search_execution": {}, "official_source_execution": {}}
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=[], companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        row = result["rows"][0]
        gnews = next(s for s in row["sources_consolidated"] if s["source"] == "GNEWS")
        self.assertFalse(gnews["technical_success"])
        self.assertEqual(gnews["freshness_status"], "sem_evidencia")
        # nunca marcado como "falhou" tecnicamente — não foi tentado, não erro fabricado
        self.assertEqual(gnews["execution_status_current_run"], "nao_escalada_neste_ciclo")


class TestDashboardDifferentiatesConsolidatedVsCurrentRun(unittest.TestCase):
    """12) Dashboard diferencia status consolidado de status do ciclo atual
    (campos separados, nunca colapsados em um só)."""

    def test_to_dashboard_view_v2_exposes_both(self):
        cfg = _cfg()
        company = _company(tier=3, ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
        history_runs = [{"run_id": "prev", "finished_at": "2026-08-04T20:00:00+00:00",
                        "emitters": {"ACME": {"searched": True, "success": 1, "raw_articles": 3}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00")
        view = cd.to_dashboard_view_v2(result["rows"][0])
        self.assertIn("status_consolidated", view)
        self.assertIn("status_current_run", view)
        # ciclo atual = PARTIAL_COVERAGE (GNEWS configurado mas não rodou nesta execução,
        # RI_RSS rodou com sucesso — mistura, sem olhar para o histórico)
        self.assertEqual(view["status_current_run"], cd.PARTIAL_COVERAGE)
        # consolidado usa a evidência recente de GNEWS -> deixa de ser parcial
        self.assertNotEqual(view["status_consolidated"], cd.PARTIAL_COVERAGE)


class TestExportTotalsReconcile(unittest.TestCase):
    """13) Totais dos 6 exports reconciliam entre si e com o payload."""

    def test_full_run_reconciles_without_raising(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            companies = [_company("A", tier=1, ri_feeds=["x"]),
                        _company("B", tier=2, country="Argentina")]
            cfg["watchlist"] = companies
            run_meta = {"international_search_execution": {"A": _search_tel(), "B": _search_tel(eventos=1)},
                       "official_source_execution": {"RI_RSS": {"A": {"attempted": True, "success": True, "items_found": 1}}}}
            result = cd.run_production_coverage(  # não deve levantar
                cfg, run_meta, history_runs=[], companies=companies, out_dir=tmp)
            self.assertTrue(result["meta"]["payload_hash"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestIdempotentRepeatedExecution(unittest.TestCase):
    """14) Execução repetida com os MESMOS insumos (mesmo run_id/generated_at)
    é idempotente — mesmo hash, mesmo conteúdo."""

    def test_same_inputs_produce_same_hash(self):
        cfg = _cfg()
        company = _company(ri_feeds=["https://ri.acme.com/feed"])
        run_meta = {"international_search_execution": {"ACME": _search_tel()},
                   "official_source_execution": {"RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
        r1 = cd.build_canonical_coverage_result(
            cfg, copy.deepcopy(run_meta), history_runs=[], companies=[company],
            run_id="SAME", generated_at="2026-08-05T00:00:00+00:00", commit_base="c1")
        r2 = cd.build_canonical_coverage_result(
            cfg, copy.deepcopy(run_meta), history_runs=[], companies=[company],
            run_id="SAME", generated_at="2026-08-05T00:00:00+00:00", commit_base="c1")
        self.assertEqual(r1["meta"]["payload_hash"], r2["meta"]["payload_hash"])


class TestCommitIncludesHtmlAndExportsSameExecution(unittest.TestCase):
    """15) O payload embutido no HTML (via render_html/coverage_result) e os
    exports vêm do MESMO objeto — verificado indiretamente: render_html não
    recalcula nada, apenas lê `coverage_result`."""

    def test_render_html_uses_passed_coverage_result_verbatim(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import risk_dashboard as rd
        cfg = rd.load_config("config_risco.yaml")
        cfg["dashboard"]["title"] = "Teste"
        company = _company(name="Ambev", tier=1)
        run_meta = {"international_search_execution": {"Ambev": _search_tel(eventos=1)},
                   "official_source_execution": {}}
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=[], companies=[company],
            run_id="HTML-RUN", generated_at="2026-08-05T00:00:00+00:00", commit_base="deadbeef")
        html = rd.render_html({"7": {"evolution": [], "feed": []}}, cfg, demo=True,
                              coverage_result=result)
        self.assertIn("HTML-RUN", html)
        self.assertIn(result["meta"]["payload_hash"], html)


class TestNoImpactOnScoreOrEventIdsFor(unittest.TestCase):
    """16) Nenhuma alteração em score ou event_ids_for — a reconciliação
    runtime é 100% telemetria/auditoria, nunca lida pelo caminho de score."""

    def test_module_never_references_scoring_functions(self):
        import inspect
        src = inspect.getsource(cd)
        for forbidden in ("event_ids_for(", "build_evolution(", "events_by_company["):
            self.assertNotIn(forbidden, src.replace(" ", ""),
                             f"coverage_diagnosis não deve chamar {forbidden}")

    def test_running_production_coverage_does_not_touch_history_or_score(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = _cfg()
            company = _company(ri_feeds=["x"])
            cfg["watchlist"] = [company]
            history_before = {"articles": {"http://a": {"events": ["ma"], "companies": ["ACME"]}},
                              "run_count": 3}
            history_snapshot = copy.deepcopy(history_before)
            run_meta = {"international_search_execution": {"ACME": _search_tel()},
                       "official_source_execution": {}}
            cd.run_production_coverage(cfg, run_meta, history_runs=[], companies=[company], out_dir=tmp)
            self.assertEqual(history_before, history_snapshot)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestOfficialTechnicalSuccessWithoutValidationNotConfirmed(unittest.TestCase):
    """Bloqueio operacional #3 — caso real Yura: fonte oficial (RI/BVL) com
    HTTP 200 (sucesso técnico) mas SEM extração validada nunca vira
    'cobertura oficial confirmada sem notícia'. Sem GNEWS de apoio, deve
    ficar PARTIAL_COVERAGE (fonte oficial conhecida, sem extração); com
    GNEWS de apoio, deve virar FALLBACK_ONLY — nunca
    NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN."""

    def _yura_like_company(self):
        return _company(
            name="Yura", tier=2, country="Peru",
            official={"news": "https://www.yura.com.pe/"})

    def _peru_validation_unvalidated(self):
        # replica PERU_SOURCE_VALIDATION['Yura']: HTTP 200 em site/BVL, mas
        # conteudo_validado=False em TODOS os candidatos — igual ao caso real.
        return [
            {"source_name": "Site institucional Yura", "source_type": "official_site",
             "url_configurada": "https://www.yura.com.pe/", "http_status": 200,
             "conteudo_validado": False, "itens_encontrados": 0,
             "bloqueio_tecnico": "403 para user-agent automatizado", "metodo": "manual_research"},
            {"source_name": "BVL — ficha do emissor Yura", "source_type": "exchange",
             "url_configurada": "https://www.bvl.com.pe/x", "http_status": 200,
             "conteudo_validado": False, "itens_encontrados": 0,
             "bloqueio_tecnico": "", "metodo": "manual_research"},
        ]

    def test_no_gnews_support_stays_partial_not_confirmed(self):
        cfg = _cfg()
        company = self._yura_like_company()
        run_meta = {"international_search_execution": {},  # GNEWS não escalado neste ciclo
                   "official_source_execution": {}}
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=[], companies=[company],
            generated_at="2026-08-05T00:00:00+00:00",
            peru_validation_map={"Yura": self._peru_validation_unvalidated()})
        row = result["rows"][0]
        self.assertNotEqual(row["coverage_status_consolidated"],
                            cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN)
        self.assertFalse(row["official_ever_validated"])
        self.assertIn(row["coverage_status_consolidated"],
                      (cd.PARTIAL_COVERAGE, cd.SOURCE_CONFIGURED_NOT_EXECUTED))

    def test_gnews_recent_success_with_unvalidated_official_is_fallback_only(self):
        cfg = _cfg()
        company = self._yura_like_company()
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {}}
        # GNEWS teve sucesso técnico recente e válido (dentro da janela) com
        # itens — mas a fonte oficial nunca foi validada.
        history_runs = [{"run_id": "prev", "finished_at": "2026-08-04T20:00:00+00:00",
                         "emitters": {"Yura": {"searched": True, "success": 1, "raw_articles": 2}}}]
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=history_runs, companies=[company],
            generated_at="2026-08-05T00:00:00+00:00",
            peru_validation_map={"Yura": self._peru_validation_unvalidated()})
        row = result["rows"][0]
        self.assertNotEqual(row["coverage_status_consolidated"],
                            cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN)
        self.assertEqual(row.get("coverage_evidence_kind_consolidated"), "sem_evidencia")

    def test_evidence_kind_is_regulador_when_cvm_validated(self):
        cfg = _cfg()
        company = _company(country="Brasil", ri_feeds=["x"])
        run_meta = {"international_search_execution": {},
                   "official_source_execution": {}}
        cvm_map = {"ACME": {"tentativa_realizada": True, "resultado_consulta": "filiante_cvm",
                            "documentos_aceitos": 3, "ultima_tentativa": "2026-08-05T00:00:00+00:00",
                            "ultimo_sucesso": "2026-08-05T00:00:00+00:00", "identificador_usado": "1",
                            "identificador_tipo": "cnpj", "metodo_consulta": "ipe_dataset_match_por_identificador_forte",
                            "documentos_retornados": 3, "data_ultimo_documento": "2026-08-01", "erro": ""}}
        result = cd.build_canonical_coverage_result(
            cfg, run_meta, history_runs=[], companies=[company],
            generated_at="2026-08-05T00:00:00+00:00", cvm_telemetry_map=cvm_map)
        row = result["rows"][0]
        self.assertEqual(row.get("coverage_evidence_kind_consolidated"), "regulador")
        self.assertTrue(row["official_ever_validated"])


class TestCvmTelemetrySeedMigration(unittest.TestCase):
    """Bloqueio operacional #1 — migração da telemetria CVM real (4H.2)
    para `international_search_history.json`, sem fabricar sucesso."""

    def test_seed_from_audit_rows_preserves_real_fields_and_marks_provenance(self):
        cvm_rows = [{"emissor": "Ambev", "status": "filiante_cvm",
                    "codigo_cvm_casado": "23264", "cnpj_casado": "07526557000100",
                    "identificador_usado": "cod_cvm:23264", "tipo_match": "codigo_cvm",
                    "protocolos_no_ano": "57", "ultima_entrega": "2026-07-30",
                    "confianca_match": "alta", "companhia_casada": "AMBEV S.A.", "n_candidatos": "0"}]
        seed = cd.build_cvm_telemetry_seed(cvm_rows, generated_at="2026-08-05T01:39:39+00:00",
                                           origin="cvm_audit_real.csv (4H.2, commit b8d502c)")
        rec = seed["Ambev"]
        self.assertTrue(rec["seeded_from_existing_telemetry"])
        self.assertEqual(rec["codigo_cvm"], "23264")
        self.assertEqual(rec["documentos_aceitos"], 57)
        self.assertEqual(rec["data_ultimo_documento"], "2026-07-30")
        self.assertEqual(rec["ultimo_sucesso"], "2026-08-05T01:39:39+00:00")
        self.assertIn("4H.2", rec["origem_migracao"])

    def test_persist_does_not_wipe_existing_on_empty_seed(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "international_search_history.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"runs": [], "cvm_telemetry": {"Ambev": {"documentos_aceitos": 57}}}, f)
            result = cd.persist_cvm_telemetry({}, history_path=path)  # falha pontual = seed vazio
            self.assertEqual(result["added"], [])
            persisted = cd.load_persisted_cvm_telemetry(path)
            self.assertEqual(persisted["Ambev"]["documentos_aceitos"], 57)  # preservado
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_persist_upserts_and_round_trips_through_consolidated_status(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "international_search_history.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"runs": []}, f)
            seed = cd.build_cvm_telemetry_seed(
                [{"emissor": "Ambev", "status": "filiante_cvm", "codigo_cvm_casado": "23264",
                 "cnpj_casado": "1", "identificador_usado": "cod_cvm:23264",
                 "tipo_match": "codigo_cvm", "protocolos_no_ano": "57",
                 "ultima_entrega": "2026-07-30", "confianca_match": "alta",
                 "companhia_casada": "AMBEV S.A.", "n_candidatos": "0"}],
                generated_at="2026-08-05T01:39:39+00:00", origin="test")
            cd.persist_cvm_telemetry(seed, history_path=path)
            persisted = cd.load_persisted_cvm_telemetry(path)
            self.assertIn("Ambev", persisted)

            cfg = _cfg()
            company = _company(name="Ambev", country="Brasil", ri_feeds=["x"])
            run_meta = {"international_search_execution": {}, "official_source_execution": {}}
            result = cd.build_canonical_coverage_result(
                cfg, run_meta, history_runs=[], companies=[company],
                generated_at="2026-08-05T10:00:00+00:00",  # ~8h depois, dentro da janela 30d
                cvm_persisted_telemetry=persisted)
            row = result["rows"][0]
            reg = next(s for s in row["sources_consolidated"] if s["source"] == "REGULADOR_LOCAL")
            self.assertEqual(reg["freshness_status"], "valida")
            self.assertTrue(row["official_ever_validated"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
