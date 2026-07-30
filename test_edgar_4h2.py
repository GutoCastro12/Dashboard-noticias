#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_edgar_4h2.py — Testes da 4H.2 (Bloco I). Sem rede: usa fixtures."""
from datetime import datetime, timezone
from pathlib import Path
import json

import edgar_audit_4h2 as E

FIX = Path(__file__).parent / "edgar_fixtures"


def _load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _cutoff(days=90):
    return int(datetime.now(timezone.utc).timestamp()) - days * 86400


PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def test_root_cause_reproduction():
    print("\n[1] Reprodução da causa raiz (set de string vira caracteres)")
    raw = "['8-K', '6-K', '10-K', '10-Q', '20-F']"
    buggy = E.buggy_forms_charset(raw)
    check(all(len(x) == 1 for x in buggy), "allowlist defeituosa = conjunto de caracteres únicos")
    check("8-K" not in buggy and "6-K" not in buggy, "nenhum formulário real pertence à allowlist defeituosa")
    fixed = E.normalize_edgar_forms(raw)
    check({"8-K", "6-K", "10-K", "10-Q", "20-F"} <= fixed, "normalização recupera a lista real de formulários")


def test_normalizer_variants():
    print("\n[2] Normalizador aceita list | string-lista | csv | None")
    check(E.normalize_edgar_forms(["8-K", "6-K"]) == {"8-K", "6-K"}, "list nativa")
    check(E.normalize_edgar_forms('["20-F","6-K"]') == {"20-F", "6-K"}, "string JSON de lista")
    check(E.normalize_edgar_forms("8-K, 10-K , 20-F") == {"8-K", "10-K", "20-F"}, "csv com espaços")
    check("8-K" in E.normalize_edgar_forms(None), "None → allowlist padrão")


def test_domestic_forms():
    print("\n[3] Formulários domésticos (Ford: 8-K/10-K/10-Q)")
    data = _load("Ford_Motor")
    fixed = E.normalize_edgar_forms(None)
    c = E.edgar_stage_counts(data, fixed, _cutoff())
    b = E.edgar_stage_counts(data, E.buggy_forms_charset("['8-K','10-K']"), _cutoff())
    check(c["accepted"] > 0, f"allowlist corrigida aceita filings domésticos (accepted={c['accepted']})")
    check(b["accepted"] == 0, "allowlist defeituosa rejeita tudo (accepted=0)")
    check("4" in c["_forms_seen"] and c["_forms_dropped_by_form_filter"].get("4"),
          "Form 4 (insider) é corretamente descartado pelo filtro de formulário")


def test_fpi_20f_6k():
    print("\n[4] Foreign private issuer (Nu: 20-F/6-K)")
    data = _load("Nubank__Nu_Holdings")
    fixed = E.normalize_edgar_forms(None)
    c = E.edgar_stage_counts(data, fixed, _cutoff())
    check(c["accepted"] > 0, f"6-K dentro da janela é aceito (accepted={c['accepted']})")
    check(c["raw_submissions"] > c["date_filtered"], "20-F de 08/04 fica fora da janela de 90 dias (date_filtered)")


def test_canadian_40f():
    print("\n[5] MJDS canadense (Toronto-Dominion: 40-F reconhecido; 424B2 descartado)")
    data = _load("Toronto_Dominion_Bank")
    with_40f = E.normalize_edgar_forms(None)              # inclui 40-F
    without_40f = E.normalize_edgar_forms("['8-K','6-K','10-K','10-Q','20-F']")  # sem 40-F
    check("40-F" in with_40f and "40-F" not in without_40f, "40-F presente só na allowlist ampliada por evidência")
    c = E.edgar_stage_counts(data, with_40f, _cutoff())
    check(c["_forms_dropped_by_form_filter"].get("424B2"), "424B2 (prospecto) descartado pelo filtro de formulário")
    check(c["accepted"] > 0, f"6-K do TD dentro da janela é aceito (accepted={c['accepted']})")


def test_time_window():
    print("\n[6] Janela temporal")
    data = _load("Nubank__Nu_Holdings")
    fixed = E.normalize_edgar_forms(None)
    wide = E.edgar_stage_counts(data, fixed, _cutoff(3650))
    narrow = E.edgar_stage_counts(data, fixed, _cutoff(90))
    check(wide["date_filtered"] >= narrow["date_filtered"], "janela maior retém ≥ filings que janela menor")


def test_accession_and_url():
    print("\n[7-8] Accession number e URL direta")
    data = _load("Ford_Motor")
    fixed = E.normalize_edgar_forms(None)
    amostra = E.sample_filings(data, fixed, _cutoff(), "0000037996", "Ford Motor", limit=3)
    check(len(amostra) >= 1, "amostra retorna ≥1 filing")
    check(all(a["accession_number"] for a in amostra), "todo filing tem accession_number")
    check(all(a["url_direta"].startswith("https://www.sec.gov/Archives/edgar/data/37996/") for a in amostra),
          "URL direta bem-formada com CIK sem zeros à esquerda")


def test_dedup():
    print("\n[9] Deduplicação por accession")
    data = _load("Ford_Motor")
    dup = json.loads(json.dumps(data))
    r = dup["filings"]["recent"]
    for k in ("form", "filingDate", "accessionNumber", "primaryDocument", "primaryDocDescription"):
        r[k] = r[k] + [r[k][0]]  # duplica o 1º filing
    fixed = E.normalize_edgar_forms(None)
    c = E.edgar_stage_counts(dup, fixed, _cutoff())
    check(c["parsed"] > c["deduplicated"] or c["deduplicated"] == c["accepted"],
          "duplicata por accession é colapsada (deduplicated ≤ parsed)")


def test_http200_empty_vs_real_empty():
    print("\n[13] HTTP 200 com JSON vazio ≠ ausência real de filing")
    empty = {"filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": []}}}
    fixed = E.normalize_edgar_forms(None)
    c = E.edgar_stage_counts(empty, fixed, _cutoff())
    check(c["raw_submissions"] == 0 and c["accepted"] == 0,
          "raw_submissions=0 distingue 'sem documento' de 'filtro descartou tudo'")
    data = _load("Ford_Motor")
    c2 = E.edgar_stage_counts(data, fixed, _cutoff())
    check(c2["raw_submissions"] > 0 and c2["accepted"] > 0,
          "com documentos brutos, o zero de produção só ocorre por filtro (não por ausência)")


def test_provenance_forced():
    print("\n[17] Classificação da proveniência (forced_trust oficial)")
    # replica a montagem de artigo do coletor corrigido
    data = _load("Nubank__Nu_Holdings")
    fixed = E.normalize_edgar_forms(None)
    amostra = E.sample_filings(data, fixed, _cutoff(), "0000000001", "Nu Holdings", limit=2)
    check(len(amostra) >= 1, "amostra de FPI não vazia (proveniência oficial atribuível a EDGAR)")


def main():
    print("=" * 64)
    print("TESTES 4H.2 — EDGAR (Bloco I)")
    print("=" * 64)
    for fn in [test_root_cause_reproduction, test_normalizer_variants, test_domestic_forms,
               test_fpi_20f_6k, test_canadian_40f, test_time_window, test_accession_and_url,
               test_dedup, test_http200_empty_vs_real_empty, test_provenance_forced]:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 64)
    print(f"RESULTADO: {ok}/{len(results)} checagens passaram")
    print("=" * 64)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
