# -*- coding: utf-8 -*-
"""Fase 4H.2 (fechamento) — Prévia do DASHBOARD REAL (não standalone), com a
seção 'Cobertura das fontes' integrada via `template_risco.html.j2` real.

Usa `risk_history.json`/`config_risco.yaml`/`run_meta.json` deste worktree
(read-only) + a telemetria CVM real (`--audit-cvm`, rede — mesmo dataset já
buscado nesta fase) para popular `row.coverage_diagnosis` de verdade.
NÃO sobrescreve `index.html`/`dashboard_risco.html` de produção — escreve em
`out_coverage_diagnosis/dashboard_preview.html`."""
import json
from pathlib import Path

import risk_dashboard as rd
import coverage_diagnosis as cd

cfg = rd.load_config("config_risco.yaml")
history = json.loads(Path("risk_history.json").read_text(encoding="utf-8"))
run_meta = json.loads(Path("run_meta.json").read_text(encoding="utf-8"))

thresholds = rd.calibrate_thresholds(history, cfg)
prev_run = history.get("last_run") or {}
prev_scores = {c: v.get("score") for c, v in (prev_run.get("status") or {}).items()}

windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
data_by_window = {}
for w in windows:
    data_by_window[str(w)] = {
        "evolution": rd.build_evolution(history, cfg, window_days=w,
                                        thresholds=thresholds, prev_scores=prev_scores),
        "feed": rd.build_feed(history, cfg, window_days=w),
    }
default_w = str(cfg["dashboard"].get("default_window", 7))
evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
changes = rd.build_changes(history, cfg, [], prev_run, evo_ref)

# telemetria CVM real (mesmo cruzamento já usado no diagnóstico retroativo
# desta fase) para que Tier 1 apareça com cobertura CVM de verdade, não
# heurística — reaproveita o audit real já rodado nesta sessão.
cvm_telemetry_map = {}
try:
    cvm_rows = rd.audit_cvm_coverage(cfg)
    cvm_telemetry_map = cd.build_cvm_telemetry(cvm_rows)
except Exception as exc:
    print(f"[aviso] auditoria CVM indisponível para a prévia: {exc}")

run_meta_live = dict(run_meta)
# injeta a telemetria CVM no formato que render_html espera repassar ao
# coverage_diagnosis (via diagnose_coverage -> classify_company_coverage).
# render_html chama diagnose_coverage(cfg, run_meta) sem cvm_telemetry_map
# explícito, então construímos aqui as linhas já com a marca "coverage_diagnosis"
# usando diagnose_coverage diretamente, e injetamos manualmente no lugar de
# confiar no caminho automático (mantém a prévia 100% equivalente ao real).
cov_rows = cd.diagnose_coverage(cfg, run_meta_live, cvm_telemetry_map=cvm_telemetry_map)
cov_by_name = {r["company"]: cd.to_dashboard_view(r) for r in cov_rows}
for win_data in data_by_window.values():
    for row in win_data["evolution"]:
        cv = cov_by_name.get(row["company"])
        if cv is not None:
            row["coverage_diagnosis"] = cv

html = rd.render_html(data_by_window, cfg, demo=False, changes=changes,
                      payload_thresholds=thresholds, run_meta=None)
# run_meta=None acima porque já injetamos coverage_diagnosis manualmente
# (com a telemetria CVM real) — passar run_meta de novo re-rodaria o
# diagnóstico SEM a telemetria CVM e sobrescreveria o que acabamos de anexar.

outdir = Path("out_coverage_diagnosis")
outdir.mkdir(exist_ok=True)
out_path = outdir / "dashboard_preview.html"
out_path.write_text(html, encoding="utf-8")

print("=== Prévia do dashboard real (4H.2) ===")
for name in ("Ambev", "Yura", "Trupal", "Coazucar", "Yobel", "Vale", "Petrobras"):
    row90 = next((r for r in data_by_window.get("90", {}).get("evolution", [])
                 if r["company"] == name), None)
    if row90 and row90.get("coverage_diagnosis"):
        cv = row90["coverage_diagnosis"]
        print(f"{name:20s} status={cv['status']:38s} ui='{cv['status_ui']}'")
    else:
        print(f"{name:20s} (sem linha na janela 90d — sem sinal de score nesse período; "
             f"cobertura é diagnosticada independentemente do score)")

print()
print("Prévia escrita em:", out_path.resolve())
