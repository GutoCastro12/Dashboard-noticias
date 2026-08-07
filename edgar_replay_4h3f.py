#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_replay_4h3f.py — 4H.3F §10/§11/§12: replay offline ANTES × DEPOIS do
parser DOM, contra o gold set curado e contra o corpus 8-K inteiro.

Usa o corpus local já baixado (sem rede):
    C:\\Users\\Gustavo\\DashRisk-corpus-4h3f-html   (HTML bruto, 8-K/6-K)

Saídas:
    edgar_4h3f_dom_comparison.csv   — accession × item × antes/depois × gold
    edgar_4h3f_gold_matrix.json     — TP/FP/FN/TN, precision_scoreable_dom,
                                       recall_known_true_events
    edgar_4h3f_replay_resumo.json   — comparação agregada 4H.3E × 4H.3F
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import edgar_canonical as ec
import edgar_normalizer as en
import edgar_sections as es
import edgar_dom as ed
from edgar_gold_4h3f import GOLD_CASES

CORPUS_HTML = Path(r"C:\Users\Gustavo\DashRisk-corpus-4h3f-html")


def _cut(s, n=200):
    return re.sub(r"\s+", " ", str(s or ""))[:n]


def _write_csv(path: Path, rows: list[dict], keys: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _load_index(corpus: Path) -> list[dict]:
    return json.loads((corpus / "index.json").read_text(encoding="utf-8"))


def _filing_from_row(row: dict) -> dict:
    acc = row.get("accession", "")
    return {
        "company": row.get("emissor", ""), "cik": row.get("cik", ""),
        "ticker": row.get("ticker", ""), "form": row.get("form", ""),
        "accession_number": acc,
        "accession_digits": ec.normalize_accession(acc),
        "filing_date": row.get("filing_date", ""),
        "report_date": row.get("report_date", ""),
        "primary_document": row.get("primary_document", ""),
        "description": row.get("description", ""),
        "items": [i for i in str(row.get("items", "")).split(",") if i.strip()],
        "url": row.get("url", ""), "provenance": "EDGAR",
    }


def _classify_antigo(filing: dict, html: str) -> list[dict]:
    """Reproduz o pipeline 4H.3E: texto achatado + edgar_sections (kind='item',
    já demovido a não-pontuável, mas mantido aqui para comparação histórica)."""
    texto = ec.strip_html(html)[:60000]
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    secs = es.evidence_sections(texto, form=filing["form"], items=filing["items"])["sections"]
    an = ec.analyze_filing(filing, texto, sem, sections=secs)
    return an["aceitos"]


def _classify_novo(filing: dict, html: str) -> tuple[list[dict], dict]:
    """Pipeline 4H.3F: DOM real do HTML."""
    dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
    texto = dom["doc"].flat_text if dom["doc"] is not None else ""
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
    return an["aceitos"], dom


def _decisao(aceitos: list[dict], event_family: str | None) -> tuple[str, dict]:
    """'scoreable' | 'nao_scoreable' | 'ausente' para a família pedida
    (ou qualquer evento pontuável, se `event_family` for None)."""
    cands = [a for a in aceitos if event_family is None or a["event_id"] == event_family]
    if not cands:
        return "ausente", {}
    pont = [a for a in cands if not a.get("nao_pontuavel_por_forma")]
    if pont:
        return "scoreable", pont[0]
    return "nao_scoreable", cands[0]


def replay_gold(outdir: str) -> dict:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    idx = {r["accession"]: r for r in _load_index(CORPUS_HTML) if r.get("html_file")}

    rows: list[dict] = []
    tp = fp = fn = tn = 0
    verificados_sinteticos = []

    for case in GOLD_CASES:
        acc = case["accession"]
        if not acc:
            # regressão sintética (10-Q) — não tem HTML real no corpus 8-K;
            # já coberta por test_edgar_4h3c/d.py como regressão permanente.
            verificados_sinteticos.append(case["id"])
            continue
        row = idx.get(acc)
        if not row:
            rows.append({**case, "encontrado_no_corpus": False,
                        "decisao_antiga": "sem_corpus", "decisao_dom": "sem_corpus",
                        "correto": False})
            continue
        html = (CORPUS_HTML / row["html_file"]).read_text(encoding="utf-8")
        filing = _filing_from_row(row)

        antes = _classify_antigo(filing, html)
        depois, dom = _classify_novo(filing, html)

        d_antes, c_antes = _decisao(antes, case["event_family"])
        d_depois, c_depois = _decisao(depois, case["event_family"])

        esperado = case["expected_result"]
        # normaliza rótulo esperado para comparação binária de scoreable
        esperado_bin = "scoreable" if esperado == "scoreable_via_dom" else esperado
        # "ausente" (nenhum candidato aceito) e "nao_scoreable" (candidato
        # aceito mas marcado não-pontuável) são o MESMO resultado para fins de
        # scoring: nenhum dos dois pontua. Comparar como string distinguia
        # "ausente" de "nao_scoreable" e marcava um TN correto como incorreto.
        d_depois_bin = "nao_scoreable" if d_depois in ("nao_scoreable", "ausente") \
            else d_depois
        correto = (d_depois_bin == esperado_bin)
        if esperado_bin == "scoreable":
            if d_depois_bin == "scoreable":
                tp += 1
            else:
                fn += 1
        else:
            if d_depois_bin == "scoreable":
                fp += 1
            else:
                tn += 1

        rows.append({
            "id": case["id"], "emissor": case["emissor"], "accession": acc,
            "item_metadata": ",".join(filing["items"]),
            "item_antigo": c_antes.get("item", ""),
            "item_dom": c_depois.get("item", ""),
            "evento": case["event_family"] or "(qualquer)",
            "evidencia_antiga": _cut(c_antes.get("evidence_text"), 160),
            "evidencia_dom": _cut(c_depois.get("evidence_text"), 160),
            "decisao_antiga": d_antes, "decisao_nova": d_depois,
            "gold_label": esperado_bin, "correto": correto,
            "items_missing_in_dom": ",".join(dom.get("items_missing_in_dom", [])),
            "razao": case["reason"],
        })

    _write_csv(out / "edgar_4h3f_dom_comparison.csv", rows, [
        "id", "emissor", "accession", "item_metadata", "item_antigo", "item_dom",
        "evento", "evidencia_antiga", "evidencia_dom", "decisao_antiga",
        "decisao_nova", "gold_label", "correto", "items_missing_in_dom", "razao"])

    total = tp + fp + fn + tn
    matrix = {
        "casos_com_corpus_real": total,
        "casos_regressao_sintetica": len(verificados_sinteticos),
        "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "precision_scoreable_dom": round(100.0 * tp / (tp + fp), 1) if (tp + fp) else None,
        "recall_known_true_events": round(100.0 * tp / (tp + fn), 1) if (tp + fn) else None,
        "acuracia_geral": round(100.0 * (tp + tn) / total, 1) if total else None,
        "casos_incorretos": [r["id"] for r in rows if not r.get("correto", True)],
    }
    (out / "edgar_4h3f_gold_matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    return matrix


def replay_corpus_8k(outdir: str) -> dict:
    """Replay completo dos 79 8-K reais: antes (texto) × depois (DOM)."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    idx = [r for r in _load_index(CORPUS_HTML)
          if r["form"] == "8-K" and r.get("html_file")]

    pont_antes = pont_depois = 0
    ev_antes = ev_depois = 0
    items_meta_tot = items_found_tot = items_missing_tot = 0
    perfeito = 0
    for row in idx:
        html = (CORPUS_HTML / row["html_file"]).read_text(encoding="utf-8")
        filing = _filing_from_row(row)
        antes = _classify_antigo(filing, html)
        depois, dom = _classify_novo(filing, html)
        ev_antes += len(antes)
        ev_depois += len(depois)
        pont_antes += sum(1 for a in antes if not a.get("nao_pontuavel_por_forma"))
        pont_depois += sum(1 for a in depois if not a.get("nao_pontuavel_por_forma"))
        items_meta_tot += len(dom["items_metadata"])
        items_found_tot += len(dom["items_dom_found"])
        items_missing_tot += len(dom["items_missing_in_dom"])
        if not dom["items_missing_in_dom"] and not dom["items_extra_in_dom"]:
            perfeito += 1

    resumo = {
        "filings_8k": len(idx),
        "cobertura_dom_perfeita": perfeito,
        "cobertura_dom_pct": round(100.0 * perfeito / len(idx), 1) if idx else 0,
        "items_metadata_total": items_meta_tot,
        "items_encontrados_dom": items_found_tot,
        "items_faltando_no_dom": items_missing_tot,
        "eventos_antes_4h3e": ev_antes, "eventos_depois_4h3f": ev_depois,
        "pontuaveis_antes_4h3e": pont_antes, "pontuaveis_depois_4h3f": pont_depois,
    }
    (out / "edgar_4h3f_replay_resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumo


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="4H.3F — replay offline DOM × texto")
    ap.add_argument("--outdir", default="out_4h3f")
    a = ap.parse_args(argv)
    matrix = replay_gold(a.outdir)
    resumo = replay_corpus_8k(a.outdir)
    print(json.dumps({"gold": matrix, "corpus_8k": resumo}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
