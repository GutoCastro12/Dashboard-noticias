# -*- coding: utf-8 -*-
"""Fase 4H.2 — Prévia visual TEMPORÁRIA do diagnóstico de cobertura.

Gera um HTML standalone em `out_coverage_diagnosis/coverage_preview.html`,
por emissor, com o status de cobertura e a telemetria por fonte. NÃO toca em
`template_risco.html.j2` nem em `index.html`/`dashboard_risco.html` de
produção.

Decisão de design (documentada, não implementada em produção agora): a
telemetria de cobertura é candidata natural a um selo/seção no dashboard
real (ex.: "⚠️ Sem fonte oficial validada" ao lado do nome do emissor), mas
isso muda a leitura do dashboard publicado e afeta apresentação executiva —
por isso fica de fora do `template_risco.html.j2` nesta fase, como prévia
separada, até aprovação explícita do usuário sobre COMO (selo discreto?
seção separada? só emissores Tier 1?) integrar isso sem confundir "risco"
com "cobertura", que são dimensões distintas (ver CLAUDE.md)."""
from __future__ import annotations

import html
import os

import coverage_diagnosis as cd

_STATUS_COLOR = {
    cd.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN: "#2e7d32",
    cd.COVERAGE_OK_EVENTS_FOUND: "#2e7d32",
    cd.ONLY_INFORMATIONAL_FOUND: "#1565c0",
    cd.FALLBACK_ONLY: "#f9a825",
    cd.PARTIAL_COVERAGE: "#ef6c00",
    cd.SOURCE_CONFIGURED_NOT_EXECUTED: "#8d6e63",
    cd.COLLECTION_FAILURE: "#c62828",
    cd.NO_VALIDATED_OFFICIAL_SOURCE: "#6a1b9a",
}

_PAGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>Prévia — Diagnóstico de Cobertura (4H.2)</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 24px;
        background:#0f1115; color:#eaeaea; }}
 h1 {{ font-size: 20px; }}
 .disclaimer {{ background:#2a2113; border:1px solid #6a5117; padding:10px 14px;
               border-radius:8px; font-size:13px; margin-bottom:18px; }}
 table {{ border-collapse: collapse; width:100%; font-size:13px; }}
 th, td {{ border:1px solid #333; padding:6px 10px; text-align:left;
          vertical-align:top; }}
 th {{ background:#1b1e26; position:sticky; top:0; }}
 .badge {{ display:inline-block; padding:2px 8px; border-radius:12px;
          color:#111; font-weight:600; font-size:12px; white-space:nowrap; }}
 .subsidiary {{ opacity:0.8; font-style:italic; }}
 .summary {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }}
 .summary div {{ padding:8px 12px; border-radius:8px; background:#1b1e26;
                font-size:13px; }}
 .src {{ font-size:11px; color:#9aa; }}
</style></head><body>
<h1>Radar de Risco — Diagnóstico de cobertura oficial (Fase 4H.2)</h1>
<div class="disclaimer">
 Prévia TEMPORÁRIA, gerada localmente a partir da telemetria REAL de
 <code>run_meta.json</code> (execução finalizada em {run_finished_at}).
 Isto NÃO é o dashboard de produção e NÃO é score de risco — é diagnóstico
 de cobertura/ausência de notícia, dimensão separada do score.
</div>
<div class="summary">{summary_html}</div>
<table>
<tr><th>Emissor</th><th>Tier</th><th>País</th><th>Status de cobertura</th>
<th>Motivo</th><th>Fontes (config/tentada/sucesso técnico/itens)</th></tr>
{rows_html}
</table>
</body></html>
"""


def _summary_html(counts: dict) -> str:
    parts = []
    for status, n in counts.items():
        color = _STATUS_COLOR.get(status, "#555")
        parts.append(f'<div><span class="badge" style="background:{color}">{n}</span> '
                     f'{html.escape(cd.status_label(status))}</div>')
    return "".join(parts)


def _row_html(rec: dict) -> str:
    color = _STATUS_COLOR.get(rec["coverage_status"], "#555")
    name = html.escape(rec["company"])
    cls = ' class="subsidiary"' if rec.get("is_subsidiary") else ""
    if rec.get("is_subsidiary"):
        name += f' <span class="src">(subsidiária de {html.escape(rec.get("parent_company", ""))})</span>'
    src_bits = []
    for s in rec["sources"]:
        src_bits.append(
            f'{html.escape(s["source"])}: '
            f'{"cfg" if s["configured"] else "—"}/'
            f'{"tent" if s["attempted"] else "—"}/'
            f'{"ok" if s["technical_success"] else "—"}/'
            f'{s["items_found"]}it')
    reasons = html.escape(" | ".join(rec["reasons"]))
    return (f'<tr{cls}><td>{name}</td><td>{rec.get("tier", "")}</td>'
           f'<td>{html.escape(rec.get("country") or "")}</td>'
           f'<td><span class="badge" style="background:{color}">'
           f'{html.escape(rec["coverage_status"])}</span><br>'
           f'<span class="src">{html.escape(cd.status_label(rec["coverage_status"]))}</span></td>'
           f'<td>{reasons}</td>'
           f'<td class="src">{"; ".join(src_bits)}</td></tr>')


def build_html_preview(rows: list, summary: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rows_html = "\n".join(_row_html(r) for r in rows)
    summary_html = _summary_html(summary["status_counts"])
    page = _PAGE_TEMPLATE.format(
        run_finished_at=html.escape(str(summary.get("run_meta_run_finished_at") or "—")),
        summary_html=summary_html, rows_html=rows_html)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


if __name__ == "__main__":
    res = cd.run_retroactive_diagnosis(out_dir="out_coverage_diagnosis")
    path = build_html_preview(res["rows"], res["summary"],
                              "out_coverage_diagnosis/coverage_preview.html")
    print(f"Prévia gerada em: {path}")
