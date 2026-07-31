"""
test_fetch_all.py — testes determinísticos (mocks, sem rede) do coletor
`fetch_all`/`build_company_queries`/`_fetch_one_query`, cobrindo os
invariantes permanentes da integração opt-in de resolução de entidade:
construção de queries, fallback legado, deduplicação, locale, force_fetch,
resiliência a cadastro malformado e determinismo."""
import copy
import sys
import time

import risk_dashboard as rd

PASS = FAIL = 0


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  ❌ [{n:02d}] {desc}")


time.sleep = lambda *a, **k: None
rd.time.sleep = lambda *a, **k: None

CALL_LOG = []


def mock_fetch_query_result(query, cfg, session, locale=None):
    CALL_LOG.append({"query": query, "locale": locale})
    idx = len(CALL_LOG)
    if idx % 4 == 0:
        return {"ok": False, "articles": [], "status_code": None,
                "error": "connection refused", "error_type": "connection_error",
                "elapsed_ms": 3, "url": "mock://" + query,
                "locale": f"{locale[0]}/{locale[1]}", "query": query}
    if idx % 3 == 0:
        return {"ok": True, "articles": [], "status_code": 200, "error": "",
                "error_type": "", "elapsed_ms": 5, "url": "mock://" + query,
                "locale": f"{locale[0]}/{locale[1]}", "query": query}
    arts = [{"title": f"Mock {idx}-1 {query[:20]}", "summary": "",
            "url": f"mock://{idx}/1", "source": "Mock", "domain": "mock.test",
            "pub_ts": 1700000000, "pub_iso": ""},
           {"title": f"Mock {idx}-2 {query[:20]}", "summary": "",
            "url": f"mock://{idx}/2", "source": "Mock", "domain": "mock.test",
            "pub_ts": 1700000100, "pub_iso": ""}]
    return {"ok": True, "articles": arts, "status_code": 200, "error": "",
           "error_type": "", "elapsed_ms": 9, "url": "mock://" + query,
           "locale": f"{locale[0]}/{locale[1]}", "query": query}


rd.fetch_query_result = mock_fetch_query_result

cfg = rd.load_config("config_risco.yaml")

legacy_company = copy.deepcopy(next(c for c in cfg["watchlist"] if c["name"] == "Ambev"))
legacy_company["force_fetch"] = True

optin_company = {
    "name": "Yura Teste", "asset_class": "nao_listada", "country": "Peru",
    "region": "América Latina", "language": "es", "tier": 2, "force_fetch": True,
    "aliases": ["Yura S.A.", "Cemento Yura"],
    "search_terms": ["Yura S.A.", "Cemento Yura", "Cementera Yura",
                    "Yura cemento", "Yura Grupo Gloria", "Yura resultados",
                    "Yura Indecopi", "Yura planta", "Yura bonos"],
    "entity_scope": "legal_entity",
}

print("=" * 70)
print("1) Emissor legado gera exatamente 1 query (build_company_queries == fallback)")
q_legacy = rd.build_company_queries(legacy_company, cfg["taxonomy"])
q_legacy_original = rd.build_company_query(legacy_company, cfg["taxonomy"])
check(1, "build_company_queries(legado) == [build_company_query(legado)]",
      q_legacy == [q_legacy_original])

print("2) Nenhum dos 160 emissores reais é opt-in")
check(2, "uses_contextual_entity_resolution() é False para todos os 160 "
        "emissores de config_risco.yaml",
      all(not rd.uses_contextual_entity_resolution(c) for c in cfg["watchlist"]))

print("3) Nenhum dos 160 gera mais de 1 query")
check(3, "build_company_queries devolve exatamente 1 elemento para todos os "
        "160 emissores reais",
      all(len(rd.build_company_queries(c, cfg["taxonomy"])) == 1
          for c in cfg["watchlist"]))

print("4) Search terms opt-in geram múltiplas queries, limitadas")
q_optin = rd.build_company_queries(optin_company, cfg["taxonomy"])
check(4, "Yura Teste (9 search_terms) gera >1 e <=8 queries "
        "(max_search_terms_per_run padrão)",
      1 < len(q_optin) <= 8)

print("5) Termos equivalentes (acento/caixa) são deduplicados")
dup_company = copy.deepcopy(optin_company)
dup_company["search_terms"] = ["Yura S.A.", "yura s.a.", "YURA S.A.", "Cemento Yura"]
q_dedup = rd.build_company_queries(dup_company, cfg["taxonomy"])
check(5, "3 variantes do mesmo termo colapsam para 1 query",
      len(q_dedup) == 2)

print("6) fetch_all: emissor legado gera exatamente 1 chamada a fetch_query_result")
cfg_iso = copy.deepcopy(cfg)
cfg_iso["watchlist"] = [legacy_company]
cfg_iso.setdefault("market_queries", {})["enabled"] = False
CALL_LOG.clear()
arts = rd.fetch_all(cfg_iso, run_count=0)
check(6, "1 chamada mockada para o emissor legado (Ambev)", len(CALL_LOG) == 1)

print("7) fetch_all: emissor opt-in gera múltiplas chamadas, todas tentadas mesmo com falhas")
cfg_iso2 = copy.deepcopy(cfg)
cfg_iso2["watchlist"] = [optin_company]
cfg_iso2.setdefault("market_queries", {})["enabled"] = False
CALL_LOG.clear()
arts_optin = rd.fetch_all(cfg_iso2, run_count=0)
n_queries = len(rd.build_company_queries(optin_company, cfg["taxonomy"]))
check(7, "todas as N queries do emissor opt-in foram tentadas, mesmo com "
        "falhas simuladas de rede (mock 1-em-4)",
      len(CALL_LOG) >= n_queries)

print("8) Nenhuma URL duplicada na saída do emissor opt-in")
urls = [a["url"] for a in arts_optin]
check(8, "dedup por URL preservado através de múltiplas queries do mesmo emissor",
      len(urls) == len(set(urls)))

print("9) Locale correto (Peru -> es-PE/PE)")
check(9, "locale_for_company(Yura Teste) == ('es-PE', 'PE')",
      rd.locale_for_company(optin_company, cfg) == ("es-PE", "PE"))

print("10) force_fetch continua funcionando")
no_force = copy.deepcopy(optin_company)
no_force.pop("force_fetch")
no_force["tier"] = 9
cfg_noforce = copy.deepcopy(cfg)
cfg_noforce["tiers"] = {9: {"fetch_every_n_runs": 0}}
check(10, "should_fetch_company honra force_fetch=True e "
         "fetch_every_n_runs=0 exatamente como antes",
      rd.should_fetch_company(optin_company, cfg, 0) is True
      and rd.should_fetch_company(no_force, cfg_noforce, 0) is False)

print("11) Cadastro sem aliases/search_terms usa fallback seguro (nome da empresa)")
empty_company = {"name": "Emissor Vazio de Teste"}
q_empty = rd.build_company_queries(empty_company, cfg["taxonomy"])
check(11, "fallback para company['name'] preservado quando não há aliases",
      len(q_empty) == 1 and "Emissor Vazio de Teste" in q_empty[0])

print("12) Cadastro opt-in malformado não impede coleta do emissor legado na mesma execução")
cfg_iso3 = copy.deepcopy(cfg)
cfg_iso3["watchlist"] = [legacy_company,
                        {"name": "Config Quebrada de Teste",
                         "entity_cues": None, "exclusion_cues": None}]
cfg_iso3.setdefault("market_queries", {})["enabled"] = False
CALL_LOG.clear()
try:
    arts_mixed = rd.fetch_all(cfg_iso3, run_count=0)
    ok12 = any(a.get("query_company") == "Ambev" for a in arts_mixed)
except Exception:
    ok12 = False
check(12, "emissor com cadastro opt-in malformado (cues=None) não impede a "
         "coleta do emissor legado na mesma execução", ok12)

print("13) Ordem final determinística")
CALL_LOG.clear()
arts_run1 = rd.fetch_all(cfg_iso, run_count=0)
CALL_LOG.clear()
arts_run2 = rd.fetch_all(cfg_iso, run_count=0)
check(13, "duas execuções idênticas produzem a mesma ordem final de artigos",
      [a["url"] for a in arts_run1] == [a["url"] for a in arts_run2])

print("14) _fetch_one_query preserva o formato de retorno (artigos, locale_usado)")
_tel = {"por_locale": {}, "queries": 0, "success": 0, "raw_articles": 0,
       "errors": 0, "locales_tentados": [], "status_codes": [],
       "error_type": "", "error": ""}
CALL_LOG.clear()
_arts_h, _usado_h = rd._fetch_one_query(
    q_legacy[0], cfg, __import__("requests").Session(),
    rd.locales_for_company(legacy_company, cfg), _tel)
check(14, "_fetch_one_query devolve (artigos: list, locale_usado: str)",
      isinstance(_arts_h, list) and isinstance(_usado_h, str))

print("=" * 70)
print(f"RESULTADO FETCH_ALL: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 70)
sys.exit(1 if FAIL else 0)
