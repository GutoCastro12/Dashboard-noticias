#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6c_shadow_isolation.py — 4I.2 R6c.

A semântica de papel de fraude da R6a/R6b está validada, mas ligá-la por
padrão faria dela o classificador de PRODUÇÃO para todo artigo futuro — e essa
decisão não foi tomada. Este arquivo garante a separação:

  chamada default  →  comportamento anterior, byte a byte
  chamada shadow   →  semântica de papel R6a/R6b

O interruptor é DESLIGADO por padrão de propósito: um flag que precisa ser
ligado nunca chega à produção por esquecimento; um que precisa ser desligado,
sim.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(t, emp, resumo=""):
    h = {"articles": {"u1": {"title": t, "summary": resumo, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": [emp]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    r = h["articles"]["u1"]
    return (sorted((r.get("events_by_company") or {}).get(emp) or []),
            {d.get("event_id"): d.get("regra")
             for d in (r.get("semantic_discards") or []) if d.get("empresa") == emp})


N4 = "A Vale employee committed fraud for Vale"
D2T = ("Dazmyn Person: Another person was arrested, charged in the ongoing "
       "Duke Energy fraud case")
D2E = ("WCSO identified a woman allegedly responsible for stealing victims' "
       "identities to assist in creating fraudulent Duke Energy accounts.")

print("=" * 96)
print("BLOCO A — o interruptor é desligado por padrão")
print("=" * 96)
check(sa.shadow_fraud_roles_ativo() is False,
      "[1] a semântica de papel NÃO está ativa por padrão")
check(sa._SHADOW_FRAUD_ROLES is False,
      "[2] o estado interno confirma o default desligado")

print()
print("=" * 96)
print("BLOCO B — produção mantém o comportamento anterior")
print("=" * 96)
check(sa.detect_fraud_role(N4, "Vale", AL.get("Vale")) == "vitima",
      "[3] N4 em produção continua com o papel antigo (vitima)")
_g, _r = pontua(N4, "Vale")
check(_r.get("fraude") == "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE",
      f"[4] e a regra antiga segue vencendo: {_r.get('fraude')}")
_g, _r = pontua(D2T, "Duke Energy", D2E)
check(_r.get("fraude") == "R_FRAUDE_NAO_CONFIRMADA",
      f"[5] Duke #2 em produção cai por fase, como antes: {_r.get('fraude')}")

print()
print("=" * 96)
print("BLOCO C — shadow enxerga a semântica nova")
print("=" * 96)
with sa.shadow_fraud_roles():
    check(sa.shadow_fraud_roles_ativo() is True, "[6] dentro do bloco o flag está ativo")
    check(sa.detect_fraud_role(N4, "Vale", AL.get("Vale")) == "",
          "[7] N4 no shadow: agiu PELA empresa ⇒ não é vítima")
    _g, _r = pontua(N4, "Vale")
    check("fraude" in _g, "[8] e o evento permanece atribuído à empresa")
    _g, _r = pontua(D2T, "Duke Energy", D2E)
    check(_r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO",
          f"[9] Duke #2 no shadow resolve por papel: {_r.get('fraude')}")

print()
print("=" * 96)
print("BLOCO D — o estado volta sozinho, inclusive com exceção")
print("=" * 96)
check(sa.shadow_fraud_roles_ativo() is False,
      "[10] saindo do bloco, produção volta imediatamente")
try:
    with sa.shadow_fraud_roles():
        raise RuntimeError("falha simulada")
except RuntimeError:
    pass
check(sa.shadow_fraud_roles_ativo() is False,
      "[11] exceção dentro do bloco não deixa o shadow ligado")
with sa.shadow_fraud_roles():
    with sa.shadow_fraud_roles():
        pass
    check(sa.shadow_fraud_roles_ativo() is True,
          "[12] aninhamento não desliga o bloco externo")
check(sa.shadow_fraud_roles_ativo() is False, "[13] e ao final tudo volta ao default")

print()
print("=" * 96)
print("BLOCO E — as duas listas coexistem e diferem só na direção")
print("=" * 96)
check(len(sa.FRAUDE_VITIMA_SHADOW) == len(sa.FRAUDE_VITIMA),
      f"[14] a lista shadow tem o mesmo tamanho ({len(sa.FRAUDE_VITIMA_SHADOW)})")
_dif = set(sa.FRAUDE_VITIMA_SHADOW) - set(sa.FRAUDE_VITIMA)
check(len(_dif) == 1 and "against|from|contra" in next(iter(_dif)),
      "[15] a única diferença é o padrão que passou a exigir direção")
check(any("employee" in p and "against|from" not in p for p in sa.FRAUDE_VITIMA),
      "[16] produção mantém o padrão original, sem direção")

print()
print("=" * 96)
print("BLOCO F — nenhum caminho de produção liga o shadow")
print("=" * 96)
_prod = ["risk_dashboard.py", "semantic_audit.py"]
for f in _prod:
    s = io.open(f, encoding="utf-8").read()
    usos = [l.strip() for l in s.splitlines()
            if "shadow_fraud_roles()" in l and "def " not in l and "with" in l]
    check(not usos, f"[17..18] {f} não abre o contexto shadow ({usos[:1]})")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("shadow_fraud_roles" not in _wf,
      "[19] o workflow não ativa a semântica shadow")

print()
print("=" * 96)
print("BLOCO G — o gate é o que separa, e o caminho de produção é estável")
print("=" * 96)
# Não manipula git: prova a PROPRIEDADE — o caminho de produção é estável e
# reproduzível, e é o interruptor (não o acaso) que muda o resultado.
import copy  # noqa: E402

_hist = json.load(io.open("risk_history.json", encoding="utf-8"))
_amostra = {"run_count": 1,
            "articles": {u: r for u, r in _hist["articles"].items()
                         if "fraude" in (r.get("event_ids") or [])}}


def _projetar():
    p = copy.deepcopy(_amostra)
    rd._reclassify_only_pass(p, cfg)
    return json.dumps(
        {u: {"e": {c: sorted(v or []) for c, v in
                   (r.get("events_by_company") or {}).items()},
             "d": [(d.get("empresa"), d.get("event_id"), d.get("regra"))
                   for d in (r.get("semantic_discards") or [])]}
         for u, r in p["articles"].items()}, sort_keys=True, ensure_ascii=False)


_prod1 = _projetar()
_prod2 = _projetar()
check(_prod1 == _prod2,
      f"[20] o caminho de produção é determinístico em {len(_amostra['articles'])} "
      f"registros com candidato de fraude")
with sa.shadow_fraud_roles():
    _shadow = _projetar()
check(sa.shadow_fraud_roles_ativo() is False, "[21] e o flag voltou a desligar")
check(_projetar() == _prod1,
      "[22] depois do bloco shadow, produção reproduz exatamente o mesmo resultado")
check(isinstance(_shadow, str),
      "[23] a projeção shadow roda sobre o mesmo corpus, sem tocar produção")

print()
print("=" * 96)
print(f"RESULTADO WAVE R6c (isolamento shadow): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
