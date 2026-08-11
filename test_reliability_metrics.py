#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reliability_metrics.py — 4I.2 R3b §18.

As duas métricas respondem perguntas diferentes e NÃO podem compartilhar
denominador. Este arquivo existe para que a confusão volte a doer:

  A — FIXED ADJUDICATED BASELINE ACCURACY
      denominador FIXO. Corrigir um FP AUMENTA o numerador; o denominador
      não se move. Mede EVOLUÇÃO contra um conjunto humano congelado.

  B — LIVE REVIEWED CRITICAL PRECISION
      denominador VARIÁVEL. Corrigir um FP o RETIRA do denominador; o
      numerador não se move. Descreve o estado da tela.

Se alguém reimplementar B com o denominador de A (ou vice-versa), o BLOCO B
falha: os dois números divergem por construção na fixture sintética.
"""
from __future__ import annotations

import reliability_metrics as rm

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


# baseline sintético: 3 TRUE, 2 FALSE_POSITIVE, 1 AMBIGUOUS
BASE = {"baseline_id": "sintetico", "counts": {}, "itens": {
    "u1||A||falencia": {"status": "TRUE", "company": "A", "event_id": "falencia", "title": "t1"},
    "u2||B||default": {"status": "TRUE", "company": "B", "event_id": "default", "title": "t2"},
    "u3||C||fraude": {"status": "TRUE", "company": "C", "event_id": "fraude", "title": "t3"},
    "u4||D||falencia": {"status": "FALSE_POSITIVE", "company": "D", "event_id": "falencia", "title": "t4"},
    "u5||E||fraude": {"status": "FALSE_POSITIVE", "company": "E", "event_id": "fraude", "title": "t5"},
    "u6||F||ma": {"status": "AMBIGUOUS", "company": "F", "event_id": "ma", "title": "t6"},
}}
TODOS = {k: "" for k in BASE["itens"]}                       # nada removido
SEM_UM_FP = {k: "" for k in BASE["itens"] if k != "u5||E||fraude"}   # 1 FP some

print("=" * 96)
print("BLOCO A — AMBIGUOUS fora das duas contas")
print("=" * 96)
a0, b0 = rm.metrica_a(BASE, TODOS), rm.metrica_b(BASE, TODOS)
check(a0["denominador"] == 5, f"[1] A ignora o AMBIGUOUS: denominador {a0['denominador']} == 5")
check(b0["denominador"] == 5, f"[2] B ignora o AMBIGUOUS: denominador {b0['denominador']} == 5")
check(a0["acertos"] == 3, f"[3] A conta só os 3 TRUE ainda scoreable ({a0['texto']})")
check(b0["true"] == 3 and b0["false_positive"] == 2, f"[4] B parte de 3 TRUE / 2 FP ({b0['texto']})")

print()
print("=" * 96)
print("BLOCO B — corrigir UM falso positivo move as duas de formas DIFERENTES")
print("=" * 96)
a1, b1 = rm.metrica_a(BASE, SEM_UM_FP), rm.metrica_b(BASE, SEM_UM_FP)
check(a1["denominador"] == a0["denominador"] == 5,
      f"[5] o denominador de A NÃO se move: {a0['denominador']} → {a1['denominador']}")
check(a1["acertos"] == a0["acertos"] + 1,
      f"[6] o numerador de A sobe: {a0['acertos']} → {a1['acertos']}")
check(b1["denominador"] == b0["denominador"] - 1,
      f"[7] o denominador de B encolhe: {b0['denominador']} → {b1['denominador']}")
check(b1["true"] == b0["true"],
      f"[8] o numerador de B NÃO se move: {b0['true']} → {b1['true']}")
check(abs(a1["pct"] - b1["pct"]) > 1e-9,
      f"[9] os dois valores DIVERGEM ({a1['texto']} vs {b1['texto']}) — "
      f"denominador compartilhado seria detectado aqui")
check(a1["texto"] == "4/5 = 80.0%", f"[10] A derivada: {a1['texto']}")
check(b1["texto"] == "3/4 = 75.0%", f"[11] B derivada: {b1['texto']}")

print()
print("=" * 96)
print("BLOCO C — o caso degenerado: todos os FP corrigidos")
print("=" * 96)
SO_TRUE = {k: "" for k, v in BASE["itens"].items() if v["status"] == "TRUE"}
a2, b2 = rm.metrica_a(BASE, SO_TRUE), rm.metrica_b(BASE, SO_TRUE)
check(a2["texto"] == "5/5 = 100.0%", f"[12] A chega a 100% sobre o denominador fixo: {a2['texto']}")
check(b2["texto"] == "3/3 = 100.0%", f"[13] B chega a 100% sobre um denominador menor: {b2['texto']}")
check(a2["denominador"] != b2["denominador"],
      f"[14] mesmo empatadas em 100%, os denominadores diferem "
      f"({a2['denominador']} vs {b2['denominador']})")

print()
print("=" * 96)
print("BLOCO D — TRUE perdido penaliza AS DUAS")
print("=" * 96)
SEM_UM_TRUE = {k: "" for k in BASE["itens"] if k != "u1||A||falencia"}
a3, b3 = rm.metrica_a(BASE, SEM_UM_TRUE), rm.metrica_b(BASE, SEM_UM_TRUE)
check(a3["acertos"] == a0["acertos"] - 1, f"[15] A cai quando um TRUE some: {a3['texto']}")
check(b3["true"] == b0["true"] - 1 and b3["denominador"] == 4,
      f"[16] B também cai — e o denominador encolhe: {b3['texto']}")

print()
print("=" * 96)
print("BLOCO E — os números reais, derivados e não escritos à mão")
print("=" * 96)
r = rm.relatorio()
check(r["counts"]["denominador_fixo"] == r["stored"]["a"]["denominador"]
      == r["candidate"]["a"]["denominador"],
      f"[17] o denominador de A é SEMPRE o denominador fixo do baseline "
      f"({r['counts']['denominador_fixo']}), nos dois universos")
# A relação, não o snapshot: o denominador de B é o de A menos os itens
# adjudicados que deixaram de pontuar. Coincidem só enquanto nada saiu — e
# isso muda assim que um apply entra em produção.
for u in ("stored", "candidate"):
    saiu = sum(1 for d in r[u]["a"]["detalhe"] if not d["scoreable"])
    check(r[u]["b"]["denominador"] == r[u]["a"]["denominador"] - saiu,
          f"[18] {u}: denominador de B = denominador fixo − itens que saíram "
          f"({r[u]['a']['denominador']} − {saiu} = {r[u]['b']['denominador']})")
check(r["candidate"]["a"]["acertos"] >= r["stored"]["a"]["acertos"],
      f"[19] o candidate nunca regride contra o stored: "
      f"{r['stored']['a']['texto']} → {r['candidate']['a']['texto']}")
check(r["candidate"]["b"]["denominador"] <= r["candidate"]["a"]["denominador"],
      f"[20] o denominador de B nunca excede o de A: "
      f"B={r['candidate']['b']['denominador']} ≤ A={r['candidate']['a']['denominador']}")
check(not r["true_perdidos"],
      f"[21] nenhum TRUE perdido na projeção ({len(r['true_perdidos'])})")
check(len(r["corrigidos"]) + len(r["fp_restantes"]) == r["counts"]["FALSE_POSITIVE"],
      "[22] corrigidos + remanescentes fecham com o total de FP do baseline")

print()
print("=" * 96)
print("BLOCO F — o baseline é congelado e não é o gold (§21/§23)")
print("=" * 96)
b = rm.carregar_baseline()
check(b["baseline_id"] == rm.BASELINE_ID, f"[23] baseline versionado: {b['baseline_id']}")
check(isinstance(b.get("created_at"), int) and b.get("reference"),
      "[24] o baseline registra created_at e a referência de onde veio")
check(all(set(v) >= {"status", "company", "event_id"} for v in b["itens"].values()),
      "[25] cada item guarda identidade suficiente para reproduzir o denominador")
check("gold" not in str(rm.BASELINE).lower(),
      "[26] o baseline vive fora do gold — gold_set_4i.json permanece independente")

print()
print("=" * 96)
print(f"RESULTADO RELIABILITY METRICS: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
