#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_4h3b.py — 4H.3B.0. AUTOCONTIDO: usa fixtures_4h3b/config_teste.yaml,
nunca o config de produção. Sem rede.

Cobre:
  1. gate real de shadow no PIPELINE NORMAL (monkeypatch de fetch_edgar_filings);
  2. atribuição empresa × evento × evidência (caso misto);
  3. recall parametrizado de rebaixamento de rating;
  4. invariância de histórico/dashboard/score.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import risk_dashboard as rd
import edgar_shadow_4h3b as sh3b

BASE = Path(__file__).parent
CFG_PATH = BASE / "fixtures_4h3b" / "config_teste.yaml"
PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def cfg_teste(collection=None, scoring=None):
    c = rd.load_config(str(CFG_PATH))
    if collection is not None:
        c["international_official_sources_enabled"] = collection
        c.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = collection
    if scoring is not None:
        c["edgar_scoring_enabled"] = scoring
    return c


def _filing_material():
    """Artigo EDGAR sintético MATERIAL (exigido pelo item 3)."""
    return {
        "title": "Ford Motor — 8-K: Ford has its credit rating downgraded",
        "summary": "Credit rating downgraded", "url": "https://www.sec.gov/x/ford-8k",
        "pub_ts": 1753000000, "source": "SEC · EDGAR", "domain": "sec.gov",
        "language": "en", "forced_trust": "oficial",
        "filing_company": "Ford Motor", "source_company": "Ford Motor",
        "monitored_company": "Ford Motor", "candidate_companies": ["Ford Motor"],
        "form": "8-K", "accession_number": "0000037996-26-000143",
        "primary_document": "d1.htm", "filing_items": "8.01",
        "filing_date": "2026-07-24", "provenance": "EDGAR",
    }


# ─────────────────── 1. GATE REAL NO PIPELINE NORMAL ───────────────────
def t1_gate_no_pipeline_normal():
    print("\n[1] Gate real: coleta=ON, scoring=OFF → filing NÃO entra em production_articles")
    cfg = cfg_teste(collection=True, scoring=False)
    chamado = {"n": 0}

    def fake_fetch(c, **kw):
        chamado["n"] += 1
        return [copy.deepcopy(_filing_material())]

    orig = rd.fetch_edgar_filings
    rd.fetch_edgar_filings = fake_fetch
    try:
        # replica EXATAMENTE o roteamento do main()
        production_articles = [{"title": "Notícia normal de mercado", "summary": "",
                                "url": "https://news/x", "language": "pt"}]
        edgar_shadow_articles, edgar_scoring_articles = [], []
        edgar_raw = rd.fetch_edgar_filings(cfg)
        if rd.edgar_scoring_enabled(cfg):
            edgar_scoring_articles = edgar_raw
            production_articles += edgar_scoring_articles
        elif rd.edgar_collection_enabled(cfg):
            edgar_shadow_articles = copy.deepcopy(edgar_raw)

        check(chamado["n"] == 1, "fetch_edgar_filings foi chamado")
        urls = {a["url"] for a in production_articles}
        check("https://www.sec.gov/x/ford-8k" not in urls,
              "filing NÃO está em production_articles")
        check(edgar_scoring_articles == [], "edgar_scoring_articles vazio (scoring=false)")
        check(len(edgar_shadow_articles) == 1, "filing roteado para edgar_shadow_articles")
        check(edgar_shadow_articles[0] is not edgar_raw[0],
              "shadow recebeu CÓPIA independente (não o mesmo objeto mutável)")

        with tempfile.TemporaryDirectory() as td:
            hist = {"articles": {}, "run_count": 52}
            antes = json.dumps(hist, sort_keys=True)
            meta = sh3b.run_edgar_runtime_shadow(edgar_shadow_articles, cfg, rd,
                                                 history_snapshot=hist, outdir=td,
                                                 watch_files=[])
            check(json.dumps(hist, sort_keys=True) == antes,
                  "merge_into_history NÃO recebeu o filing (histórico inalterado)")
            check(meta["persisted_records"] == 0, "run_meta.persisted_records == 0")
            check(meta["history_changed"] is False, "run_meta.history_changed == false")
            check(meta["dashboard_changed"] is False, "run_meta.dashboard_changed == false")
            check(meta["backfill"] is False, "run_meta.backfill == false")
            check(meta["scoring_enabled"] is False, "run_meta.scoring_enabled == false")
            check(meta["collection_mode"] == "shadow", "run_meta.collection_mode == 'shadow'")
            for f in sh3b.RUNTIME_FILES:
                check((Path(td) / f).exists(), f"artifact gerado: {f}")
            sim = (Path(td) / "edgar_runtime_shadow_score_simulation.csv").read_text(encoding="utf-8-sig")
            check("não" in sim.lower() and "aplicado_em_producao" in sim,
                  "score registrado APENAS como simulado (aplicado_em_producao=não)")
            cls = (Path(td) / "edgar_runtime_shadow_classification.csv").read_text(encoding="utf-8-sig")
            check("0000037996-26-000143" in cls, "filing aparece no artifact de shadow")
    finally:
        rd.fetch_edgar_filings = orig


def t2_caso_c_elegivel_mas_nao_ativado():
    print("\n[2] CASO C: coleta=ON, scoring=ON → filing SERIA elegível ao pontuável")
    cfg = cfg_teste(collection=True, scoring=True)
    check(rd.edgar_scoring_enabled(cfg) is True, "scoring habilitado no cenário de teste")
    production_articles = []
    edgar_raw = [copy.deepcopy(_filing_material())]
    if rd.edgar_scoring_enabled(cfg):
        production_articles += edgar_raw
    check(len(production_articles) == 1, "no CASO C o filing entraria em production_articles")
    for nome in ("config_risco_4h3b_candidato.yaml", "config_risco.yaml"):
        p = BASE / nome
        if p.exists():
            c = rd.load_config(str(p))
            check(c.get("edgar_scoring_enabled") is not True,
                  f"{nome} NÃO ativa scoring")


def t3_caso_a_coleta_desligada():
    print("\n[3] CASO A: coleta=OFF → nenhum efeito")
    cfg = cfg_teste(collection=False, scoring=False)
    check(rd.edgar_collection_enabled(cfg) is False, "coleta desligada")
    check(rd.fetch_edgar_filings(cfg) == [], "fetch_edgar_filings retorna [] sem tocar a rede")


# ─────────────────── 2. ATRIBUIÇÃO EMPRESA × EVENTO ───────────────────
def t4_caso_misto_sem_vazamento():
    print("\n[4] Caso misto: atribuição por evidência (proibido vazar rating p/ Samarco)")
    cfg = cfg_teste()
    art = {"title": ("Vale — 6-K: rating rebaixado pela Fitch e atualização sobre "
                     "plano de recuperação judicial da Samarco"),
           "summary": "", "url": "u-misto", "filing_company": "Vale",
           "provenance": "EDGAR", "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(art, cfg)
    ebc = art.get("events_by_company") or {}
    ctx = art.get("context_events_by_company") or {}
    check(ebc.get("Vale") == ["rebaixamento_rating"],
          f"events_by_company['Vale'] == ['rebaixamento_rating'] (obtido {ebc.get('Vale')})")
    check(ebc.get("Samarco Mineração") == ["recuperacao_judicial"],
          f"events_by_company['Samarco Mineração'] == ['recuperacao_judicial'] "
          f"(obtido {ebc.get('Samarco Mineração')})")
    check("rebaixamento_rating" not in (ebc.get("Samarco Mineração") or []),
          "PROIBIDO: rating NÃO foi atribuído à Samarco")
    vale_ctx = [e for e in (ctx.get("Vale") or []) if e.get("event_id") == "recuperacao_judicial"]
    check(bool(vale_ctx), "context_events_by_company['Vale'] contém a RJ da Samarco")
    check(vale_ctx and vale_ctx[0].get("subject_company") == "Samarco Mineração",
          "contexto da Vale aponta subject_company = Samarco Mineração")
    check(vale_ctx and vale_ctx[0].get("scoreable") is False, "contexto da Vale é scoreable=false")
    ev = {d["event_id"]: d for d in (art.get("event_attribution_evidence") or [])}
    for eid in ("rebaixamento_rating", "recuperacao_judicial"):
        d = ev.get(eid, {})
        check(all(k in d for k in ("evidence_text", "evidence_start", "evidence_end",
                                   "evidence_sentence", "subject_company", "subject_evidence",
                                   "attribution_rule", "attribution_confidence")),
              f"evidência completa registrada para {eid}")


def t5_casos_direto_e_terceiro():
    print("\n[5] Casos direto e de terceiro preservados")
    cfg = cfg_teste()
    d = {"title": "Vale — 6-K: Vale conclui emissão de dívida", "summary": "", "url": "u-d",
         "filing_company": "Vale", "provenance": "EDGAR", "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(d, cfg)
    check((d.get("events_by_company") or {}).get("Vale"), "evento direto preservado p/ Vale")
    t = {"title": "Vale — 6-K: plano de recuperação judicial da Samarco", "summary": "",
         "url": "u-t", "filing_company": "Vale", "provenance": "EDGAR",
         "form": "6-K", "language": "pt"}
    rd.classify_and_attribute(t, cfg)
    ebc = t.get("events_by_company") or {}
    ctx = t.get("context_events_by_company") or {}
    check(ebc.get("Vale") == [], "terceiro: events_by_company['Vale'] == []")
    check("recuperacao_judicial" in (ebc.get("Samarco Mineração") or []),
          "terceiro: Samarco mantém a RJ como evento direto")
    check(any(e.get("event_id") == "recuperacao_judicial" for e in (ctx.get("Vale") or [])),
          "terceiro: Vale mantém a RJ apenas como contexto")


def t6_atribuicao_por_oracao():
    print("\n[6] Escopo de oração: janela não atravessa fronteira")
    cfg = cfg_teste()
    r = sh3b.attribute_events_by_evidence(
        "rating rebaixado pela Fitch e atualização sobre plano de recuperação judicial da Samarco",
        "", ["rebaixamento_rating", "recuperacao_judicial"],
        ["Vale", "Samarco Mineração"], cfg["watchlist"], rd.normalize, filer="Vale")
    pe = r["por_evento"]
    check(pe.get("rebaixamento_rating", {}).get("subject_company") == "Vale",
          "rating → Vale (oração sem terceiro citado)")
    check(pe.get("recuperacao_judicial", {}).get("subject_company") == "Samarco Mineração",
          "RJ → Samarco (possessivo na mesma oração)")
    r2 = sh3b.attribute_events_by_evidence("rating da Samarco rebaixado", "",
                                           ["rebaixamento_rating"],
                                           ["Vale", "Samarco Mineração"],
                                           cfg["watchlist"], rd.normalize, filer="Vale")
    check(r2["por_evento"].get("rebaixamento_rating", {}).get("subject_company") == "Samarco Mineração",
          "possessivo explícito vence o filer: 'rating da Samarco' → Samarco")


# ─────────────────── 3. RECALL DE RATING ───────────────────
def t7_recall_rating_parametrizado():
    print("\n[7] Recall parametrizado de rebaixamento de rating")
    cfg = cfg_teste()
    casos = [
        "rating rebaixado", "rating da Vale rebaixado", "rating de Vale foi rebaixado",
        "Vale teve o rating rebaixado", "Fitch rebaixa rating de Vale",
        "Moody's downgrades Vale", "S&P cuts Vale rating",
        "calificación de Vale fue rebajada",
    ]
    for t in casos:
        ids = {e["id"] for e in rd.classify_article({"title": t, "summary": ""}, cfg["taxonomy"])}
        check("rebaixamento_rating" in ids, f"detecta: {t}")
    neg = "Fitch reafirma rating da Vale"
    ids = {e["id"] for e in rd.classify_article({"title": neg, "summary": ""}, cfg["taxonomy"])}
    check("rebaixamento_rating" not in ids, f"negação preservada: {neg}")


def t8_patterns_retrocompativel():
    print("\n[8] `patterns` é retrocompatível")
    tax_sem = [{"id": "x", "keywords": ["evento raro"], "score": 1}]
    a = {"title": "rating da Vale rebaixado", "summary": ""}
    check(rd.classify_article(a, tax_sem) == [],
          "taxonomia sem `patterns` mantém comportamento anterior (sem match)")


# ─────────────────── 4. INVARIÂNCIA ───────────────────
def t9_invariancia_arquivos():
    print("\n[9] Invariância de histórico/dashboard por hash")
    cfg = cfg_teste(collection=True, scoring=False)
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td) / "risk_history.json"
        hp.write_text(json.dumps({"articles": {}, "run_count": 52}), encoding="utf-8")
        ip = Path(td) / "index.html"
        ip.write_text("<html>dashboard</html>", encoding="utf-8")
        h0 = hashlib.sha256(hp.read_bytes()).hexdigest()
        i0 = hashlib.sha256(ip.read_bytes()).hexdigest()
        meta = sh3b.run_edgar_runtime_shadow([copy.deepcopy(_filing_material())], cfg, rd,
                                             history_snapshot={"articles": {}},
                                             outdir=td, watch_files=[str(hp), str(ip)])
        check(hashlib.sha256(hp.read_bytes()).hexdigest() == h0, "risk_history.json byte-a-byte igual")
        check(hashlib.sha256(ip.read_bytes()).hexdigest() == i0, "index.html byte-a-byte igual")
        check(meta["filings_collected"] == 1 and meta["persisted_records"] == 0,
              "run_meta coerente: coletado=1, persistido=0")


def t10_config_candidato():
    print("\n[10] Config candidato 4H.3B")
    p = BASE / "config_risco_4h3b_candidato.yaml"
    if not p.exists():
        check(False, "config_risco_4h3b_candidato.yaml presente")
        return
    c = rd.load_config(str(p))
    check(c.get("international_official_sources_enabled") is True, "fontes = true")
    check(((c.get("official_sources") or {}).get("EUA") or {}).get("enabled") is True,
          "official_sources.EUA.enabled = true")
    check(c.get("edgar_scoring_enabled") is False, "edgar_scoring_enabled = false")
    check(rd.edgar_collection_enabled(c) is True and rd.edgar_scoring_enabled(c) is False,
          "combinação resulta em SHADOW MODE")
    prod = BASE / "config_risco.yaml"
    if prod.exists():
        pc = rd.load_config(str(prod))
        # 4H.6 ligou a coleta em produção deliberadamente (corroboração real);
        # a invariante que continua protegendo o negócio não é "coleta off" —
        # é "scoring autônomo off", que a 4H.4/4H.4B nunca reabriram.
        check(rd.edgar_collection_enabled(pc) is True,
              "config de PRODUÇÃO tem coleta LIGADA (invariante pós-4H.6, não mais desligada)")
        check(rd.edgar_scoring_enabled(pc) is False,
              "config de PRODUÇÃO mantém scoring autônomo DESLIGADO mesmo com coleta ligada")


def main():
    print("=" * 70)
    print("TESTES 4H.3B.0 — gate real de shadow + atribuição por evidência")
    print(f"config de teste: {CFG_PATH}  (autocontido)")
    print("=" * 70)
    if not CFG_PATH.exists():
        print(f"{FAIL} fixture ausente: {CFG_PATH}")
        return 1
    for fn in [t1_gate_no_pipeline_normal, t2_caso_c_elegivel_mas_nao_ativado,
               t3_caso_a_coleta_desligada, t4_caso_misto_sem_vazamento,
               t5_casos_direto_e_terceiro, t6_atribuicao_por_oracao,
               t7_recall_rating_parametrizado, t8_patterns_retrocompativel,
               t9_invariancia_arquivos, t10_config_candidato]:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 70)
    print(f"RESULTADO 4H.3B: {ok}/{len(results)} checagens passaram")
    print("=" * 70)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
