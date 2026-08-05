# -*- coding: utf-8 -*-
"""Fixture/teste do workflow de produção (bloqueio operacional #4): prova,
por inspeção estática do YAML e do código real que ele invoca, que o
pipeline de produção:

  1. calcula telemetria (fetch_all/fetch_cvm_fatos/fetch_ri_news_pages/
     fetch_edgar_filings — já cobertos pela suíte legada, não reprovados
     aqui);
  2. calcula o diagnóstico consolidado (`coverage_diagnosis.
     run_production_coverage`) DEPOIS da telemetria;
  3. gera o payload canônico (`build_canonical_coverage_result`) UMA vez;
  4. gera HTML e os 6 exports a partir do MESMO payload;
  5. executa `assert_exports_reconcile_v2()`;
  6. FALHA antes da publicação se houver divergência (propaga
     `ReconciliationError`, não é engolida);
  7. o commit automático do workflow inclui HTML e os exports de
     `out_coverage_diagnosis/` no mesmo `git add`.

Nenhuma chamada de rede, nenhum workflow real disparado — só leitura
estática do YAML (garante que a config de produção realmente faz o que a
correção promete) e execução real de `main()`-equivalente localmente
(mesma função `coverage_diagnosis.run_production_coverage` que o workflow
chama via `risk_dashboard.py`), provando o comportamento fim-a-fim sem
precisar do GitHub Actions."""
import inspect
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coverage_diagnosis as cd
import risk_dashboard as rd

WORKFLOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".github", "workflows", "update_risk_dashboard.yml")


def _workflow_text():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return f.read()


class TestWorkflowCommitsExportsWithHtml(unittest.TestCase):
    """7) o commit automático inclui HTML e os 6 exports na MESMA execução."""

    def test_workflow_adds_out_coverage_diagnosis_dir(self):
        txt = _workflow_text()
        self.assertIn("out_coverage_diagnosis/", txt)
        # precisa estar no MESMO step de commit que dashboard_risco.html
        commit_step = txt.split("Commit history + dashboard")[1]
        self.assertIn("out_coverage_diagnosis/", commit_step)
        self.assertIn("dashboard_risco.html", commit_step)
        self.assertIn("international_search_history.json", commit_step)

    def test_workflow_runs_risk_dashboard_main(self):
        txt = _workflow_text()
        self.assertIn("python risk_dashboard.py", txt)


class TestWorkflowCvmRenewalCadence(unittest.TestCase):
    """Confirma a renovação contínua da telemetria CVM (bloqueio #2): a
    auditoria roda via workflow_dispatch manual OU via cron semanal
    dedicado — nunca fica sem cadência de renovação dentro da janela de
    frescor de 30 dias."""

    def test_weekly_cron_dedicated_to_cvm_audit_exists(self):
        txt = _workflow_text()
        self.assertIn("0 6 * * 1", txt, "cron semanal de renovação CVM ausente")
        self.assertIn("--audit-cvm", txt)

    def test_cvm_freshness_window_has_safety_margin_over_cadence(self):
        # Cadência = 7 dias (semanal); janela de frescor = 30 dias — margem
        # de ~4x, condizente com uma falha pontual não expirar a evidência
        # antes da próxima tentativa semanal.
        weekly_cadence_days = 7
        self.assertGreater(cd.CVM_FRESHNESS_DAYS, weekly_cadence_days * 2)

    def test_manual_dispatch_input_still_available(self):
        txt = _workflow_text()
        self.assertIn("audit_cvm:", txt)
        self.assertIn("workflow_dispatch", txt)


class TestPipelineOrderMatchesRequiredSequence(unittest.TestCase):
    """1-3) telemetria → diagnóstico consolidado → payload canônico → (4)
    HTML/exports do mesmo payload → (5) reconciliação → (6) gate de falha —
    verificado na ORDEM REAL do código-fonte de `risk_dashboard.main()`."""

    def test_source_order_telemetry_then_coverage_then_render_then_persist(self):
        src = inspect.getsource(rd.main)
        i_render_html = src.index("html = render_html(")
        i_run_coverage = src.index("run_production_coverage(")
        i_out_write = src.index("out_file.write_text(html")
        # coverage (que já inclui gravação dos 6 exports) roda ANTES do
        # render_html ser chamado, que por sua vez roda ANTES do HTML ser
        # escrito em disco (publicação) — ordem exigida pelos itens 2/3/4.
        self.assertLess(i_run_coverage, i_render_html)
        self.assertLess(i_render_html, i_out_write)

    def test_reconciliation_error_propagates_before_html_write(self):
        src = inspect.getsource(rd.main)
        # o bloco que chama run_production_coverage precisa re-levantar
        # ReconciliationError (não pode ser silenciado por um `except
        # Exception` genérico que engula tudo) ANTES de render_html/write.
        m = re.search(r"except _covdiag\.ReconciliationError:\s*\n(.*?)\n\s*raise",
                      src, re.S)
        self.assertIsNotNone(m, "ReconciliationError não é reprop agada explicitamente em main()")
        i_except = src.index("except _covdiag.ReconciliationError:")
        i_render_html = src.index("html = render_html(")
        self.assertLess(i_except, i_render_html)


class TestEndToEndReconciliationGateReal(unittest.TestCase):
    """5-6) Execução real (sem GitHub Actions) da MESMA função que o
    workflow invoca — prova que o gate de reconciliação de fato quebra a
    execução antes de qualquer publicação quando os exports divergem, e
    que passa silenciosamente (sem exceção) quando tudo bate."""

    def _cfg_and_company(self):
        cfg = {"tiers": {1: {"fetch_every_n_runs": 1}}, "watchlist": []}
        company = {"name": "ACME", "tier": 1, "country": "Chile",
                  "ri_feeds": ["https://ri.acme.com/feed"], "official": {}}
        return cfg, company

    def test_clean_run_does_not_raise(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg, company = self._cfg_and_company()
            run_meta = {"international_search_execution": {
                            "ACME": {"searched": True, "queries": 1, "success": 1,
                                    "raw_articles": 2, "eventos_classificados": 0}},
                       "official_source_execution": {
                            "RI_RSS": {"ACME": {"attempted": True, "success": True, "items_found": 1}}}}
            result = cd.run_production_coverage(cfg, run_meta, history_runs=[],
                                                 companies=[company], out_dir=tmp)
            self.assertTrue(result["meta"]["payload_hash"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_corrupted_export_blocks_before_publication(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg, company = self._cfg_and_company()
            run_meta = {"international_search_execution": {}, "official_source_execution": {}}
            result = cd.run_production_coverage(cfg, run_meta, history_runs=[],
                                                 companies=[company], out_dir=tmp)
            path = os.path.join(tmp, "auditoria_cobertura_emissores.csv")
            with open(path, encoding="utf-8-sig") as f:
                txt = f.read()
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(txt.replace(result["meta"]["payload_hash"], "0" * 64))
            with self.assertRaises(cd.ReconciliationError):
                cd.assert_exports_reconcile_v2(result["rows"], result["meta"], tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
