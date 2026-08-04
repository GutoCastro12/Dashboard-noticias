# -*- coding: utf-8 -*-
"""Testes do onboarding histórico de 180 dias dos emissores peruanos
(Yura, Trupal, Coazucar, Yobel + related_entities autorizadas da Coazucar).

Fixtures determinísticas, SEM rede — usa a watchlist/taxonomia REAIS de
`config_risco.yaml` e as funções reais de risk_dashboard.py
(merge_into_history, build_evolution, resolve_event_families via
classify_and_attribute-like helpers). Não reimplementa score/dedup/
atribuição: só monta registros de histórico já no formato que
merge_into_history/build_evolution esperam (mesmo padrão de
test_b3_entity_role.py / test_occurrence_family_merge.py) e verifica as
invariantes do onboarding.

Rodar: PYTHONIOENCODING=utf-8 python test_peru_onboarding_180d.py
"""
from __future__ import annotations

import copy
import time

import yaml

import risk_dashboard as rd

with open("config_risco.yaml", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)
WATCHLIST_BY_NAME = {c["name"]: c for c in CFG["watchlist"]}
PERU = ["Yura", "Trupal", "Coazucar", "Yobel"]
DAY = 86400
NOW = time.time()

passed = 0
failed = 0


def check(label: str, cond: bool) -> None:
    global passed, failed
    if cond:
        print(f"  OK  {label}")
        passed += 1
    else:
        print(f"  FAIL {label}")
        failed += 1


# ── [0] Config: os 4 candidatos e as related_entities autorizadas da Coazucar
def test_config_presence():
    print("\n[0] Presença/forma da configuração dos 4 emissores")
    for name in PERU:
        check(f"'{name}' está na watchlist", name in WATCHLIST_BY_NAME)
    coaz = WATCHLIST_BY_NAME.get("Coazucar", {})
    check("Coazucar tem fetch_related_entities=true", bool(coaz.get("fetch_related_entities")))
    rel_names = {r.get("entity_name", "") for r in (coaz.get("related_entities") or [])}
    expected_subs = {"Casa Grande S.A.A.", "Cartavio S.A.A.", "Agroindustrias San Jacinto S.A.",
                      "Empresa Agrícola Sintuco S.A.", "Agrolmos S.", "Empresa Agraria Chiquitoy S.A."}
    check("as 6 subsidiárias autorizadas estão declaradas",
          expected_subs.issubset(rel_names))
    yobel = WATCHLIST_BY_NAME.get("Yobel", {})
    check("Yobel RUC = 20100074029", yobel.get("ruc") == "20100074029")
    yura = WATCHLIST_BY_NAME.get("Yura", {})
    check("Yura tem exclusion_cues geográficas (distrito de yura)",
          any("distrito de yura" in c for c in (yura.get("exclusion_cues") or [])))


# ── [1] Janela: mesma ocorrência incluída em 7/30/90/180 mantém contribuição
#     idêntica — só inclui/exclui, nunca recalcula (mesmo cuidado da B3).
def _mk_history_single_event(days_ago: int, company: str = "Yura") -> dict:
    ts = int(NOW - days_ago * DAY)
    url = f"https://example.pe/yura-evento-{days_ago}"
    rec = {
        "title": "Yura S.A. sofre rebaixamento de classificação de risco",
        "url": url,
        "summary": "Agência rebaixa nota de crédito da Yura S.A.",
        "source": "Diario Real Perú", "domain": "diarioreal.pe",
        "pub_ts": ts, "pub_iso": "",
        "companies": [company],
        "event_ids": ["rebaixamento_rating"],
        "events_by_company": {company: ["rebaixamento_rating"]},
        "captured_ts": ts, "cap_iso": "",
    }
    return {"articles": {url: rec}, "run_count": 1}


def test_window_consistency():
    print("\n[1] Janelas 7/30/90/180 — mesma ocorrência, mesma contribuição")
    # evento publicado há 45 dias: aparece em 90 e 180, não em 7/30
    hist = _mk_history_single_event(45)
    rows_by_window = {}
    for w in (7, 30, 90, 180):
        rows = rd.build_evolution(copy.deepcopy(hist), CFG, window_days=w)
        row = next((r for r in rows if r["company"] == "Yura"), None)
        rows_by_window[w] = row

    check("janela 7d NÃO inclui evento de 45 dias atrás (nenhuma linha OU score 0)",
          rows_by_window[7] is None or rows_by_window[7]["total_score"] == 0)
    check("janela 30d NÃO inclui evento de 45 dias atrás",
          rows_by_window[30] is None or rows_by_window[30]["total_score"] == 0)
    check("janela 90d inclui o evento (score > 0)",
          rows_by_window[90] is not None and rows_by_window[90]["total_score"] > 0)
    check("janela 180d inclui o evento (score > 0)",
          rows_by_window[180] is not None and rows_by_window[180]["total_score"] > 0)

    # a contribuição do MESMO evento é idêntica entre 90d e 180d (ambas
    # janelas o incluem por inteiro — não há "recálculo por janela", só
    # inclusão/exclusão, igual ao padrão corrigido na B3).
    c90 = rows_by_window[90]["total_score"]
    c180 = rows_by_window[180]["total_score"]
    check("contribuição idêntica entre janelas 90d e 180d (evento presente em ambas)",
          c90 == c180)


# ── [2] Idempotência do merge: reaplicar os mesmos artigos não duplica nada.
def test_merge_idempotency():
    print("\n[2] Idempotência de merge_into_history")
    hist = {"articles": {}, "run_count": 0}
    art = {
        "title": "Trupal S.A. anuncia investimento em nova planta",
        "url": "https://example.pe/trupal-investimento-1",
        "summary": "Trupal investe em modernização de planta em Trujillo.",
        "source": "Gestión", "domain": "gestion.pe",
        "pub_ts": int(NOW - 20 * DAY), "pub_iso": "",
        "companies": ["Trupal"],
        "events": [{"id": "investimento_expansao", "label": "Investimento/expansão",
                    "severity": "info", "direction": "positiva", "score": 0,
                    "dimensions": [], "applies_to": []}],
        "events_by_company": {"Trupal": ["investimento_expansao"]},
    }
    added_1 = rd.merge_into_history(hist, [copy.deepcopy(art)], keep_days=400)
    check("1a mescla adiciona 1 registro", len(added_1) == 1)
    n_after_first = len(hist["articles"])

    added_2 = rd.merge_into_history(hist, [copy.deepcopy(art)], keep_days=400)
    check("2a mescla do MESMO artigo não adiciona nada (0 novos)", len(added_2) == 0)
    check("nº de registros no histórico não muda na 2a mescla",
          len(hist["articles"]) == n_after_first)


# ── [3] related_entities da Coazucar: contexto, nunca score na holding.
def test_coazucar_related_entities_never_scoreable():
    print("\n[3] Related entities da Coazucar não pontuam a holding")
    coaz = WATCHLIST_BY_NAME["Coazucar"]
    cfg180 = dict(CFG)
    dash = dict(CFG.get("dashboard", {}))
    dash["period"] = "180d"
    cfg180["dashboard"] = dash
    # smoke test estrutural: sem rede, monta um artigo do jeito que
    # fetch_related_entities_context monta de verdade (mesmo shape) e
    # confirma que merge_into_history + build_evolution nunca atribuem
    # score à holding a partir dele.
    rel = coaz["related_entities"][0]
    art = {
        "title": f"{rel['entity_name']} enfrenta paralisação de colheita por clima",
        "url": "https://example.pe/casa-grande-clima-1",
        "summary": "Subsidiária da Coazucar reporta impacto climático na safra.",
        "source": "El Comercio", "domain": "elcomercio.pe",
        "pub_ts": int(NOW - 10 * DAY), "pub_iso": "",
        "companies": ["Coazucar"],
        "events": [{"id": "sem_evento_taxonomico", "label": "Evento a revisar",
                    "severity": "info", "direction": "neutra", "score": 0,
                    "dimensions": [], "applies_to": []}],
        "events_by_company": {"Coazucar": []},  # nunca pontua a holding
        "context_events_by_company": {"Coazucar": [{
            "event_id": "sem_evento_taxonomico", "event_label": "Evento a revisar",
            "subject_company": rel["entity_name"], "relation_type": "subsidiary",
            "impact_type": "indireto_material", "event_scope": "indireto",
            "event_phase": "", "direction": "neutra", "scoreable": False,
            "attribution_confidence": "media",
            "attribution_evidence": f"related_entity '{rel['entity_name']}' de 'Coazucar'",
        }]},
        "query_scope": "related_entity", "query_related_entity": rel["entity_name"],
    }
    hist = {"articles": {}, "run_count": 0}
    rd.merge_into_history(hist, [art], keep_days=400)
    rows = rd.build_evolution(copy.deepcopy(hist), CFG, window_days=180)
    coaz_row = next((r for r in rows if r["company"] == "Coazucar"), None)
    check("Coazucar aparece no radar (card de cobertura, fetch_related_entities=true)",
          coaz_row is not None)
    if coaz_row is not None:
        check("score da Coazucar permanece 0 (nenhuma transferência de subsidiária)",
              coaz_row["total_score"] == 0)


# ── [4] Escopo: nenhum artigo do onboarding pode pontuar empresa fora dos 4
def test_scope_isolation_helper():
    print("\n[4] Filtro de escopo (companies fora de PERU são rejeitadas)")
    import persist_peru_onboarding_180d as pod
    art_ok = {"title": "x", "url": "https://x.pe/1", "pub_ts": int(NOW - DAY),
              "events": [{"id": "e1"}], "companies": ["Yura"]}
    art_bad = {"title": "y", "url": "https://x.pe/2", "pub_ts": int(NOW - DAY),
               "events": [{"id": "e1"}], "companies": ["Yura", "B3"]}
    problems_ok = pod.validate_final_pool([art_ok])
    problems_bad = pod.validate_final_pool([art_bad])
    check("artigo só com empresa autorizada passa na validação", problems_ok == [])
    check("artigo com empresa fora do escopo (B3) é rejeitado na validação",
          any("B3" in p for p in problems_bad))


def main():
    test_config_presence()
    test_window_consistency()
    test_merge_idempotency()
    test_coazucar_related_entities_never_scoreable()
    test_scope_isolation_helper()
    print("\n" + "=" * 70)
    print(f"RESULTADO ONBOARDING PERU 180D: {passed}/{passed + failed} checagens passaram")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
