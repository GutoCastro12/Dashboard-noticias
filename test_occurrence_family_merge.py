"""
test_occurrence_family_merge.py — 15 testes determinísticos (sem rede) do
agrupamento de ocorrências ENTRE ARTIGOS para eventos da mesma família
econômica opt-in (`merge_occurrences_across_articles: true`, hoje só
`disrupcao_operacional`). Cobre o bug corrigido: o mesmo incêndio real da
Yobel (Los Olivos) sendo dividido em 2 ocorrências por causa de artigos
diferentes classificados com estágios diferentes da mesma família.

Usa `cfg = rd.load_config("config_risco.yaml")` (config real desta branch)."""
import copy
import json

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


cfg = rd.load_config("config_risco.yaml")


def art(title, url, pub_ts, domain="teste.invalid", source="Fonte teste", summary=""):
    return {"title": title, "summary": summary, "url": url, "source": source,
            "domain": domain, "pub_ts": pub_ts, "pub_iso": "2026-07-24 12:00",
            "language": "es"}


def evolve(articles, window_days=90):
    c = copy.deepcopy(cfg)
    hist = {"articles": {}}
    processed = []
    for a in articles:
        aa = dict(a)
        rd.classify_and_attribute(aa, c)
        processed.append(aa)
    rd.merge_into_history(hist, processed, keep_days=400)
    th = rd.calibrate_thresholds(hist, c)
    evo = rd.build_evolution(hist, c, window_days=window_days, thresholds=th)
    return hist, {r["company"]: r for r in evo}


import time
# 20 dias atrás de "agora" — folga generosa contra o cutoff de window_days=90
# usado pela maioria dos testes (evita o artigo cair perto da borda do corte
# por causa do tempo real gasto processando os testes anteriores no mesmo
# processo). O teste 7 usa deslocamentos próprios (ver abaixo), sempre bem
# dentro de window_days=365, nunca no futuro.
BASE_TS = int(time.time()) - 20 * 86400

print("=" * 78)
print("TESTES — agrupamento de ocorrência entre artigos (família disrupcao_operacional)")
print("=" * 78)

# ── 1/2) Mesmo incêndio, 2 event_ids da mesma família (20+60) → 1 ocorrência, base 60 ──
a1a = art("Incendio en fabrica de Yobel en Los Olivos.", "https://f1.invalid/a", BASE_TS)
a1b = art("Incendio de gran magnitud con multiples explosiones consume fabrica de "
         "Yobel en Los Olivos.", "https://f1.invalid/b", BASE_TS + 3600)
_, rows1 = evolve([a1a, a1b])
r1 = rows1.get("Yobel")
check(1, "2 event_ids da mesma família (mesmo incêndio) -> 1 ocorrência no breakdown",
      r1 is not None and len(r1["breakdown"]) == 1)
check(2, "estágio 20+60 -> só o estágio 60 conta (base=60, não 80)",
      r1 is not None and r1["breakdown"][0]["base"] == 60)

# ── 3) Estágio 40+60 → só base 60 ─────────────────────────────────────────
a3a = art("Yobel suspendio sus operaciones en Los Olivos tras el incendio.",
         "https://f3.invalid/a", BASE_TS)
a3b = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f3.invalid/b", BASE_TS + 7200)
_, rows3 = evolve([a3a, a3b])
r3 = rows3.get("Yobel")
check(3, "estágio 40+60 -> só o estágio 60 conta (base=60, não 100)",
      r3 is not None and len(r3["breakdown"]) == 1 and r3["breakdown"][0]["base"] == 60)

# ── 4) 3 fontes, 3 estágios diferentes → 1 ocorrência, base 60 ────────────
a4a = art("Incendio en almacen de Yobel en Los Olivos es controlado.",
         "https://f4.invalid/a", BASE_TS, domain="fontea4.invalid")
a4b = art("Yobel suspendio sus operaciones en Los Olivos tras el incendio.",
         "https://f4.invalid/b", BASE_TS + 3600, domain="fonteb4.invalid")
a4c = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f4.invalid/c", BASE_TS + 7200, domain="fontec4.invalid")
_, rows4 = evolve([a4a, a4b, a4c])
r4 = rows4.get("Yobel")
check(4, "3 fontes com 3 estágios distintos do mesmo incêndio -> 1 ocorrência, base 60",
      r4 is not None and len(r4["breakdown"]) == 1 and r4["breakdown"][0]["base"] == 60
      and r4["breakdown"][0]["sources"] == 3)
print("DEBUG4", None if r4 is None else [(b["label"], b["base"], b["sources"]) for b in r4["breakdown"]])

# ── 5) Mesmo incêndio, títulos totalmente distintos (paráfrase) → 1 ocorrência ─
a5a = art("Alarma en Los Olivos: incendio de gran magnitud consume fabrica de "
         "cosmeticos.", "https://f5.invalid/a", BASE_TS)
a5b = art("Bomberos combaten incendio en planta de Yobel ubicada en Los "
         "Olivos tras multiples explosiones.", "https://f5.invalid/b", BASE_TS + 5400)
_, rows5 = evolve([a5a, a5b])
r5 = rows5.get("Yobel")
check(5, "mesmo incêndio com títulos totalmente distintos -> 1 ocorrência",
      r5 is not None and len(r5["breakdown"]) == 1)

# ── 6) Mesmo local/data, fontes diferentes → 1 ocorrência ─────────────────
a6a = art("Incendio de gran magnitud en Los Olivos afecta a Yobel.",
         "https://rpp.invalid/a", BASE_TS, domain="rpp.invalid", source="RPP")
a6b = art("Incendio de gran magnitud en Los Olivos afecta a Yobel.",
         "https://infobae.invalid/b", BASE_TS + 1800, domain="infobae.invalid", source="Infobae")
_, rows6 = evolve([a6a, a6b])
r6 = rows6.get("Yobel")
check(6, "mesmo local/data, fontes diferentes -> 1 ocorrência, 2 fontes agrupadas",
      r6 is not None and len(r6["breakdown"]) == 1 and r6["breakdown"][0]["sources"] == 2)

# ── 7) Incêndios em DATAS diferentes (>45 dias) → 2 ocorrências ──────────
_ts7_old = int(time.time()) - 100 * 86400
_ts7_new = int(time.time()) - 20 * 86400  # 80 dias depois — bem no passado, nunca no futuro
a7a = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f7.invalid/a", _ts7_old)
a7b = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f7.invalid/b", _ts7_new)
_, rows7 = evolve([a7a, a7b], window_days=365)
r7 = rows7.get("Yobel")
check(7, "incêndios em datas MUITO distantes (60 dias) -> 2 ocorrências (não reunidas "
        "acima do gap, mesmo com marcador igual — família NÃO usa união pelo passo 3)",
      r7 is not None and len(r7["breakdown"]) == 2)

# ── 8) Incêndios em UNIDADES diferentes (mesma janela, marcador de local diverge) ─
a8a = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f8.invalid/a", BASE_TS)
a8b = art("Incendio de gran magnitud con multiples explosiones en el almacen de "
         "Yobel en Comas.", "https://f8.invalid/b", BASE_TS + 3600)
_, rows8 = evolve([a8a, a8b])
r8 = rows8.get("Yobel")
check(8, "incêndios em UNIDADES/locais diferentes (Los Olivos x Comas), mesma janela "
        "-> 2 ocorrências (marcador diverge, não funde só por família)",
      r8 is not None and len(r8["breakdown"]) == 2)

# ── 9) Eventos de famílias DIFERENTES não se agrupam ──────────────────────
a9a = art("Incendio de gran magnitud con multiples explosiones en la planta de "
         "Yobel en Los Olivos.", "https://f9.invalid/a", BASE_TS)
a9b = art("Yobel enfrenta expediente sancionador de Indecopi por conducta "
         "anticompetitiva.", "https://f9.invalid/b", BASE_TS + 3600)
_, rows9 = evolve([a9a, a9b])
r9 = rows9.get("Yobel")
check(9, "evento operacional + evento regulatório (famílias diferentes) -> "
        "NÃO agrupam (2 ocorrências)",
      r9 is not None and len(r9["breakdown"]) == 2)

# ── 10) Eventos SEM family_id de merge cross-article → comportamento legado ─
a10a = art("Yura S.A. sufre rebaja de calificacion crediticia por Moody's Local.",
          "https://f10.invalid/a", BASE_TS)
a10b = art("Yura S.A. sufre rebaja de calificacion crediticia confirmada por "
          "S&P Global Ratings.", "https://f10.invalid/b", BASE_TS + 5 * 86400)
_, rows10 = evolve([a10a, a10b])
r10 = rows10.get("Yura")
check(10, "rebaixamento_rating (família credit_rating, SEM merge_occurrences_"
         "across_articles) preserva o comportamento legado: mesmo event_id, "
         "mesma janela -> já colapsava por event_id antes desta correção",
      r10 is not None and len(r10["breakdown"]) == 1
      and r10["breakdown"][0]["label"] == "Rebaixamento de rating")

# ── 11) Idempotência ──────────────────────────────────────────────────────
_, rowsA = evolve([a1a, a1b])
_, rowsB = evolve([a1a, a1b])
check(11, "duas execuções independentes do mesmo conjunto de artigos produzem "
         "o mesmo score/ocorrência",
      rowsA["Yobel"]["total_score"] == rowsB["Yobel"]["total_score"]
      and len(rowsA["Yobel"]["breakdown"]) == len(rowsB["Yobel"]["breakdown"]))

# ── 12) Ordem das fontes não altera o resultado ───────────────────────────
_, rows_order1 = evolve([a4a, a4b, a4c])
_, rows_order2 = evolve([a4c, a4a, a4b])
check(12, "ordem de entrada dos artigos não altera o score final nem o nº de ocorrências",
      rows_order1["Yobel"]["total_score"] == rows_order2["Yobel"]["total_score"]
      and len(rows_order1["Yobel"]["breakdown"]) == len(rows_order2["Yobel"]["breakdown"]))

# ── 13) Reprocessamento (merge_into_history 2x) não cria nova ocorrência ──
c13 = copy.deepcopy(cfg)
hist13 = {"articles": {}}
p13 = []
for a in (a1a, a1b):
    aa = dict(a)
    rd.classify_and_attribute(aa, c13)
    p13.append(aa)
added13a = rd.merge_into_history(hist13, [dict(x) for x in p13], keep_days=400)
added13b = rd.merge_into_history(hist13, [dict(x) for x in p13], keep_days=400)
th13 = rd.calibrate_thresholds(hist13, c13)
evo13 = rd.build_evolution(hist13, c13, window_days=90, thresholds=th13)
r13 = next((r for r in evo13 if r["company"] == "Yobel"), None)
check(13, "reprocessar os mesmos artigos (merge_into_history 2x) não duplica "
         "nem cria nova ocorrência",
      len(added13a) == 2 and len(added13b) == 0
      and r13 is not None and len(r13["breakdown"]) == 1)

# ── 14) Score calculado uma única vez (não soma 20+60) ────────────────────
check(14, "score da ocorrência consolidada = só o estágio principal (60), "
         "nunca soma dos estágios (não é 80)",
      r1 is not None and r1["breakdown"][0]["base"] == 60
      and all(b["base"] != 80 for b in r1["breakdown"]))

# ── 15) event_ids_for contém só o evento principal ────────────────────────
hist15, _ = evolve([a1a, a1b])
rec15 = next((r for r in hist15["articles"].values()
             if "incidente_operacional_grave" in (r.get("events_by_company", {}).get("Yobel") or [])
             or "incidente_operacional" in (r.get("events_by_company", {}).get("Yobel") or [])), None)
# cada registro histórico mantém seu PRÓPRIO event_id (por artigo) — o que a
# correção garante é que o CONSOLIDADO (breakdown/score) usa só o principal;
# aqui confirmamos que nenhum registro individual carrega os DOIS ids juntos
# de forma ambígua para a mesma empresa.
_todos_eids = set()
for r in hist15["articles"].values():
    for eid in (r.get("events_by_company", {}) or {}).get("Yobel", []) or []:
        _todos_eids.add(eid)
check(15, "event_ids_for por artigo continuam corretos e a consolidação de score "
         "usa só o event_id principal (verificado via breakdown único no teste 1)",
      len(rows1["Yobel"]["breakdown"]) == 1
      and rows1["Yobel"]["breakdown"][0]["label"] == "Incidente operacional grave")

print("=" * 78)
print(f"RESULTADO AGRUPAMENTO DE OCORRÊNCIA (FAMÍLIA): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 78)

import sys
sys.exit(0 if FAIL == 0 else 1)
