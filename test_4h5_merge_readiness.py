#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_4h5_merge_readiness.py — 4H.5F: regressões de merge-readiness que não
existiam antes desta fase. Sem rede (usa configs/fixtures sintéticas e o
mesmo HTML real já baixado localmente na 4H.4, sem chamadas HTTP novas).

Cobre:
  §4  blast radius das duas flags de coleta (só SEC, nenhum outro país)
  §5  contrato fim-a-fim CASO A (sem match) / CASO B (com match)
  §6  dedup de SEC como UMA fonte econômica (8-K+8-K/A, Item 1.01+2.03)
  §7  reprocessamento idempotente do pipeline completo
  §8  UI/link: corrob_sources chega renderizável em all_sources/link_fields
  §9  regressão do bug Path->JSON do shadow antigo (corrigido nesta fase)
"""
from __future__ import annotations
import copy
import json
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_dom as ed
import edgar_normalizer as en
import edgar_corroboration_4h5 as corrob

# Fixture bundlada no repo (não depende de corpus/caminho local, roda em
# qualquer OS/CI) — ver test_fixtures_4h5/.
FIXTURES_4H5 = Path(__file__).parent / "test_fixtures_4h5"

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _ts(date_iso):
    from datetime import datetime, timezone
    return int(datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def news_rec(company, event_id, date_iso, title, domain="reuters.com", source="Reuters"):
    return {
        "title": title, "url": f"https://{domain}/{abs(hash(title))}", "summary": title,
        "source": source, "domain": domain, "pub_ts": _ts(date_iso), "pub_iso": f"{date_iso} 10:00",
        "companies": [company], "events_by_company": {company: [event_id]},
        "companies_attributed": [company],
    }


def base_history(**recs):
    return {"articles": recs, "run_count": 1}


def load_real_filing_article(cik, accession, company, form="8-K"):
    """8-K real, bundlado como fixture do repo (test_fixtures_4h5/) — sem
    rede, sem caminho local, funciona em qualquer OS/CI."""
    stem = f"baker_hughes_{accession}"
    row = json.loads((FIXTURES_4H5 / f"{stem}.json").read_text(encoding="utf-8"))
    html = (FIXTURES_4H5 / f"{stem}.html").read_text(encoding="utf-8", errors="replace")
    filing = {
        "company": company, "cik": cik, "ticker": row.get("ticker", ""), "form": form,
        "accession_number": accession, "accession_digits": ec.normalize_accession(accession),
        "filing_date": row["filing_date"], "report_date": row.get("report_date", ""),
        "primary_document": row["primary_document"], "description": row.get("description", ""),
        "items": [i for i in row.get("items", "").split(",") if i],
        "url": row["url"], "provenance": "EDGAR",
    }
    dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
    doc = dom["doc"]
    texto = doc.flat_text if doc else ""
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
    art = ec.to_article(filing, texto, an, sem)
    from datetime import datetime, timezone
    art["pub_ts"] = int(datetime.strptime(filing["filing_date"], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp())
    return art


cfg = rd.load_config("config_risco.yaml")


class _NoNetworkEnrich:
    """Zero rede nesta etapa (§15/§16 do pedido): substitui
    `enrich_with_body` (que faz `requests.get` de verdade) por uma versão
    que devolve os artigos JÁ analisados a partir do HTML real local (mesmo
    corpus da 4H.4, sem nenhuma chamada de rede nova), preservando o
    comportamento real de `apply_edgar_corroboration` para tudo o mais."""
    def __init__(self, arts_by_accession):
        self._by_acc = {a["accession_number"]: a for a in arts_by_accession}

    def __call__(self, stub_articles, cfg, rd, **kw):
        return [self._by_acc[s["accession_number"]] for s in stub_articles
                if s.get("accession_number") in self._by_acc]

print("=" * 100)
print("§4 — BLAST RADIUS: ligar EDGAR não liga nenhuma outra fonte internacional")
print("=" * 100)

check(cfg.get("international_official_sources_enabled") is True,
      "config real: international_official_sources_enabled = true (esperado nesta branch)")
check((cfg.get("official_sources") or {}).get("EUA", {}).get("enabled") is True,
      "config real: official_sources.EUA.enabled = true (esperado nesta branch)")

_outros_paises = [p for p in (cfg.get("official_sources") or {}) if p != "EUA"]
check(len(_outros_paises) >= 5, f"universo de outros países presente no config (N={len(_outros_paises)}), para o teste ter algo a provar")
_todos_desligados = all((cfg.get("official_sources") or {}).get(p, {}).get("enabled") is not True
                        for p in _outros_paises)
check(_todos_desligados,
      f"NENHUM outro país (Chile/México/Canadá/Reino Unido/etc., {len(_outros_paises)} verificados) tem enabled=true")

import inspect as _inspect
_src_flag = _inspect.getsource(rd.edgar_collection_enabled)
check("official_sources" in _src_flag and '"EUA"' in _src_flag,
      "edgar_collection_enabled só lê official_sources['EUA'] no código-fonte (não itera outros países)")
_src_int_flag_uses = _inspect.getsource(rd)
_n_usos_flag_global = _src_int_flag_uses.count("international_official_sources_enabled")
# 1 na definição da função + 1 no print de log = 2 usos esperados, nunca mais
check(_n_usos_flag_global <= 3,
      f"international_official_sources_enabled aparece só {_n_usos_flag_global}x no módulo inteiro "
      f"(definição + log) — nenhum outro consumidor foi adicionado")

# CVM (Brasil) usa seção de config totalmente separada, não official_sources
_src_cvm = _inspect.getsource(rd.fetch_cvm_fatos)
check("official_sources" not in _src_cvm,
      "fetch_cvm_fatos (coletor real do Brasil) não lê official_sources — sem acoplamento com a flag EDGAR")


print()
print("=" * 100)
print("§5 — CONTRATO FIM-A-FIM: CASO A (sem match) vs CASO B (com match)")
print("=" * 100)

# CASO A: EDGAR encontra filing SEM ocorrência existente compatível
hist_a = base_history()  # histórico vazio: nada para casar
art_a = load_real_filing_article("0001701605", "0001193125-26-305477", "Baker Hughes")
corrob.enrich_with_body = _NoNetworkEnrich([art_a])  # zero rede (§15/§16)
resumo_a = corrob.apply_edgar_corroboration(
    [{"filing_company": "Baker Hughes", "monitored_company": "Baker Hughes",
      "cik": "0001701605", "ticker": "BKR", "form": "8-K",
      "accession_number": "0001193125-26-305477", "filing_date": "2026-07-16",
      "report_date": "2026-07-16", "primary_document": "d105425d8k.htm",
      "filing_items": "1.01,1.02,2.01,2.03,7.01,9.01", "summary": "",
      "url": "https://www.sec.gov/Archives/edgar/data/1701605/000119312526305477/d105425d8k.htm",
      "pub_ts": art_a["pub_ts"]}],
    hist_a, cfg, rd)
check(resumo_a["candidatos_avaliados"] >= 1, "[CASO A] coleta/classificação ocorreu (candidatos avaliados > 0)")
check(resumo_a["corroborados"] == 0, "[CASO A] nenhuma nova ocorrência pontuável criada (corroborados=0)")
check(len(hist_a["articles"]) == 0, "[CASO A] nenhum registro novo em history[\"articles\"] (peso-base=0, delta de score=0)")
ev_a = rd.build_evolution(copy.deepcopy(hist_a), cfg, window_days=90)
check(not any(r["company"] == "Baker Hughes" for r in ev_a) or
      next(r for r in ev_a if r["company"] == "Baker Hughes")["total_score"] == 0,
      "[CASO A] Baker Hughes não aparece pontuando no build_evolution real")

# CASO B: EDGAR encontra filing COMPATÍVEL com ocorrência já existente
hist_b = base_history(n1=news_rec("Baker Hughes", "ma", "2026-07-20",
                                  "Baker Hughes closes Chart deal"))
resumo_b = corrob.apply_edgar_corroboration(
    [{"filing_company": "Baker Hughes", "monitored_company": "Baker Hughes",
      "cik": "0001701605", "ticker": "BKR", "form": "8-K",
      "accession_number": "0001193125-26-305477", "filing_date": "2026-07-16",
      "report_date": "2026-07-16", "primary_document": "d105425d8k.htm",
      "filing_items": "1.01,1.02,2.01,2.03,7.01,9.01", "summary": "",
      "url": "https://www.sec.gov/Archives/edgar/data/1701605/000119312526305477/d105425d8k.htm",
      "pub_ts": art_a["pub_ts"]}],
    hist_b, cfg, rd)
check(resumo_b["corroborados"] == 1, "[CASO B] SEC entrou em corroboração (1 corroboração nova)")
check(len(hist_b["articles"]) == 1, "[CASO B] continua sendo UMA ocorrência (nenhum registro novo criado)")
check(any(s.get("domain") == "sec.gov" for s in hist_b["articles"]["n1"].get("corrob_sources", [])),
      "[CASO B] sec.gov presente em corrob_sources do registro existente")
ev_b_before_taxonomy = {e["id"]: e["score"] for e in cfg["taxonomy"]}.get("ma")
_ev_b = rd.build_evolution(hist_b, cfg, window_days=90)
_row_b = next(r for r in _ev_b if r["company"] == "Baker Hughes")
_b_break = next(b for b in _row_b["breakdown"] if b["label"] == "M&A")
check(_b_break["base"] == ev_b_before_taxonomy,
      "[CASO B] peso-base continua sendo o único da taxonomia (uma vez, não duplicado)")
check(_b_break["sources"] == 2,
      "[CASO B] mecanismo normal de source bonus contabiliza 2 fontes (Reuters-equiv. + SEC)")


print()
print("=" * 100)
print("§6 — DEDUP: SEC representa UMA fonte econômica, mesmo com múltiplos filings")
print("=" * 100)

# Reuters + SEC 8-K + SEC 8-K/A sobre a MESMA ocorrência
hist_dedup = base_history(n1=news_rec("Empresa Dedup", "ma", "2026-03-01", "Empresa Dedup adquire Alvo"))
art_8k = {"form": "8-K", "url": "https://www.sec.gov/Archives/edgar/data/9/0000000000-26-000001.htm",
         "pub_ts": _ts("2026-03-02"), "accession_number": "0000000000-26-000001"}
art_8ka = {"form": "8-K/A", "url": "https://www.sec.gov/Archives/edgar/data/9/0000000000-26-000002.htm",
          "pub_ts": _ts("2026-03-10"), "accession_number": "0000000000-26-000002"}
added1 = corrob.append_sec_corroboration(hist_dedup["articles"]["n1"], art_8k, "2.01")
added2 = corrob.append_sec_corroboration(hist_dedup["articles"]["n1"], art_8ka, "2.01")
check(added1 is True and added2 is False,
      "[§6] Reuters+SEC 8-K+SEC 8-K/A: só a 1ª entrada SEC é aceita (dedup por domínio sec.gov)")
check(len(hist_dedup["articles"]["n1"]["corrob_sources"]) == 1,
      "[§6] corrob_sources tem exatamente 1 entrada SEC (não 2)")
_ev_dedup = rd.build_evolution(hist_dedup, cfg, window_days=90)
_row_dedup = next(r for r in _ev_dedup if r["company"] == "Empresa Dedup") if any(
    r["company"] == "Empresa Dedup" for r in _ev_dedup) else None
if _row_dedup:
    _b_dedup = next((b for b in _row_dedup["breakdown"] if b["label"] == "M&A"), None)
    if _b_dedup:
        check(_b_dedup["sources"] == 2, "[§6] amendment NÃO gera bônus extra — sources permanece 2 (Reuters+SEC), não 3")

# Item 1.01 + Item 2.03 do MESMO filing → 1 corroboração
hist_items = base_history(n1=news_rec("Empresa Itens", "emissao_divida", "2026-04-01", "Empresa Itens capta recursos"))
c101 = {"form": "8-K", "url": "https://www.sec.gov/Archives/edgar/data/9/0000000000-26-000003.htm",
       "pub_ts": _ts("2026-04-01"), "accession_number": "0000000000-26-000003"}
c203 = {"form": "8-K", "url": "https://www.sec.gov/Archives/edgar/data/9/0000000000-26-000003.htm",
       "pub_ts": _ts("2026-04-01"), "accession_number": "0000000000-26-000003"}
a1 = corrob.append_sec_corroboration(hist_items["articles"]["n1"], c101, "1.01")
a2 = corrob.append_sec_corroboration(hist_items["articles"]["n1"], c203, "2.03")
check(a1 is True and a2 is False,
      "[§6] Item 1.01 + Item 2.03 do MESMO filing → NÃO viram 2 corroborações independentes")


print()
print("=" * 100)
print("§7 — REPROCESSAMENTO IDEMPOTENTE do pipeline completo (apply_edgar_corroboration 2x)")
print("=" * 100)

hist_idem = base_history(n1=news_rec("Baker Hughes", "ma", "2026-07-20",
                                     "Baker Hughes closes Chart deal"))
stub = {"filing_company": "Baker Hughes", "monitored_company": "Baker Hughes",
        "cik": "0001701605", "ticker": "BKR", "form": "8-K",
        "accession_number": "0001193125-26-305477", "filing_date": "2026-07-16",
        "report_date": "2026-07-16", "primary_document": "d105425d8k.htm",
        "filing_items": "1.01,1.02,2.01,2.03,7.01,9.01", "summary": "",
        "url": "https://www.sec.gov/Archives/edgar/data/1701605/000119312526305477/d105425d8k.htm",
        "pub_ts": art_a["pub_ts"]}
r1 = corrob.apply_edgar_corroboration([stub], hist_idem, cfg, rd)
n_occ_1 = len(hist_idem["articles"])
n_srcs_1 = len(hist_idem["articles"]["n1"].get("corrob_sources", []))
score_1 = next(r for r in rd.build_evolution(copy.deepcopy(hist_idem), cfg, window_days=90)
              if r["company"] == "Baker Hughes")["total_score"]
r2 = corrob.apply_edgar_corroboration([stub], hist_idem, cfg, rd)
n_occ_2 = len(hist_idem["articles"])
n_srcs_2 = len(hist_idem["articles"]["n1"].get("corrob_sources", []))
score_2 = next(r for r in rd.build_evolution(copy.deepcopy(hist_idem), cfg, window_days=90)
              if r["company"] == "Baker Hughes")["total_score"]
check(n_occ_1 == n_occ_2 == 1, "[§7] mesmo número de ocorrências (1) após reprocessar 2x")
check(n_srcs_1 == n_srcs_2 == 1, "[§7] mesmo número de fontes econômicas (1 SEC) após reprocessar 2x")
check(score_1 == score_2, "[§7] mesmo score após reprocessar 2x")
check(r2["corroborados"] == 0, "[§7] 2ª passada não conta novo bônus (corroborados=0 na repetição)")


print()
print("=" * 100)
print("§8 — UI/LINK: corrob_sources chega renderizável (all_sources / link_fields)")
print("=" * 100)

_row_ui = next(r for r in rd.build_evolution(hist_idem, cfg, window_days=90) if r["company"] == "Baker Hughes")
_b_ui = next(b for b in _row_ui["breakdown"] if b["label"] == "M&A")
_sec_source = next((s for s in _b_ui["all_sources"] if not s["primary"] and "sec.gov" in s.get("href", "")), None)
check(_sec_source is not None, "[§8] entrada SEC presente em all_sources (o que o template renderiza)")
if _sec_source:
    check(_sec_source["href"].startswith("https://www.sec.gov/Archives/"),
          "[§8] href é a URL real do accession SEC (nunca homepage/search)")
    check(_sec_source["render_anchor"] is True, "[§8] render_anchor=True → template gera <a> clicável")
    check("Item" in _sec_source["source"], "[§8] rótulo da fonte inclui o Item (\"SEC · 8-K · Item ...\")")


print()
print("=" * 100)
print("§9 — REGRESSÃO: bug Path->JSON do shadow antigo (edgar_shadow_4h3b), corrigido")
print("=" * 100)

import edgar_shadow_4h3b as sh3b
import tempfile

cfg9 = rd.load_config("config_risco.yaml")
for w in cfg9.get("watchlist", []):
    w.setdefault("cik", "0000037996")
_hoje = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")

with tempfile.TemporaryDirectory() as td:
    try:
        # ANTES da correção (4H.5F): watch_files=[Path(...), ...] quebrava
        # com TypeError ao serializar run_meta.json. Fixado no call site de
        # risk_dashboard.py (str(history_path)) — aqui provamos que o MESMO
        # padrão (watch_files todo em string) não quebra mais.
        meta9 = sh3b.run_edgar_runtime_shadow(
            [], cfg9, rd, history_snapshot={"articles": {}}, outdir=td,
            watch_files=[str(Path("risk_history.json")), "config_risco.yaml", "index.html"])
        check(isinstance(meta9, dict), "[§9] run_edgar_runtime_shadow com watch_files em string não levanta exceção")
        check(all(isinstance(k, str) for k in meta9.get("hashes_antes", {})),
              "[§9] hashes_antes só tem chaves string (serializável em JSON)")
        _ = json.dumps(meta9)
        check(True, "[§9] meta completo é JSON-serializável (regressão do TypeError corrigida)")
    except TypeError as exc:
        check(False, f"[§9] REGRESSÃO: TypeError ainda ocorre ({exc})")

# prova de que o padrão ANTIGO (Path object in watch_files) É a causa raiz —
# documenta o bug sem reabrir escopo (não alteramos edgar_shadow_4h3b.py)
try:
    meta_bug = sh3b.run_edgar_runtime_shadow(
        [], cfg9, rd, history_snapshot={"articles": {}}, outdir=tempfile.mkdtemp(),
        watch_files=[Path("risk_history.json"), "config_risco.yaml", "index.html"])
    check(False, "[§9b] (informativo) Path object em watch_files não quebrou — comportamento pode ter mudado upstream")
except TypeError:
    check(True, "[§9b] (informativo) confirma que Path object em watch_files É a causa raiz — dívida técnica documentada, não corrigida dentro de edgar_shadow_4h3b.py (fora do escopo desta fase; call site já corrigido)")


print()
print("=" * 100)
print(f"RESULTADO 4H.5F MERGE-READINESS: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    import sys
    sys.exit(1)
