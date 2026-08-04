# -*- coding: utf-8 -*-
"""Gera uma prévia OFFLINE do dashboard (fix B3: bolsa x companhia + dedup CEO)
em out_b3_fix_preview/, sem tocar em index.html/dashboard_risco.html reais e
sem rede. Usa risk_history.json e config_risco.yaml deste worktree (já
corrigidos)."""
import json
from pathlib import Path

import risk_dashboard as rd

cfg = rd.load_config("config_risco.yaml")
history_path = Path("risk_history.json")
history = json.loads(history_path.read_text(encoding="utf-8"))

# resolve_history_urls não faz rede quando tudo já está resolvido/cacheado;
# ainda assim, para uma prévia 100% offline, pulamos essa etapa e usamos os
# campos de link já persistidos no histórico.
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

html = rd.render_html(data_by_window, cfg, demo=False, changes=changes,
                      payload_thresholds=thresholds)

outdir = Path("out_b3_fix_preview")
outdir.mkdir(exist_ok=True)
(outdir / "dashboard_risco_preview.html").write_text(html, encoding="utf-8")

b3_90 = next(r for r in data_by_window["90"]["evolution"] if r["company"] == "B3")
print("=== B3 (janela 90d) ===")
print("total_score:", b3_90["total_score"], "| status:", b3_90["status"])
for e in b3_90["breakdown"]:
    print(f"  - {e['label']} ({e['date']}) contrib={e['contrib']} sources={e['sources']}")
print("timeline events:", len(b3_90["events"]))
print()
print("Prévia escrita em:", (outdir / "dashboard_risco_preview.html").resolve())
