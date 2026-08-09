#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b3b_banorte_naming.py — 4I.2 Wave B7b-3b.

NAMING RIGHTS: "Estadio Banorte" é o nome do LOCAL, não o banco. Solução
CONFIG-ONLY via `mention_guard.contexto_patterns` na entrada do Banorte —
mesmo mecanismo já usado pela B3 e pelos aliases da B1.

O guard exige o substantivo do local IMEDIATAMENTE antes do alias, então a
estrutura inversa ("Banorte anuncia naming rights de novo estádio", com o
banco como sujeito) continua atribuída.

Asserções sobre `detect_companies` — camada real de atribuição. O gold
semeado não move aqui: removida a empresa, `event_ids_for` cai no fallback
legado (artefato F1 já caracterizado na B7a).
"""
from __future__ import annotations
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
_E = "Grupo Financiero Banorte"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def det(t):
    return set(rd.detect_companies({"title": t, "summary": ""}, cfg["watchlist"]))


print("=" * 96)
print("BLOCO A — §10.1/§10.4: FP real e named place")
print("=" * 96)
check(_E not in det("Fiscalía CDMX abre investigación por muerte en el Estadio Banorte "
                     "previo al México vs Portugal"),
      "[1 FP real] investigação de morte no Estadio Banorte NÃO atribui o banco")
check(_E not in det("Incendio en el Estadio Banorte deja heridos"),
      "[4] outro evento no mesmo local também não atribui o banco")

print()
print("=" * 96)
print("BLOCO B — §10.2/§10.3: Banorte como SUJEITO real preservado")
print("=" * 96)
for _t in ("CNBV abre investigación contra Banorte por irregularidades",
           "Banorte é investigado pelo regulador mexicano",
           "Banorte anuncia resultados del trimestre",
           "Banorte, Banamex e Inbursa disparan créditos con mayor riesgo de impago"):
    check(_E in det(_t), f"[TRUE] Banorte sujeito preservado: {_t[:52]}")

print()
print("=" * 96)
print("BLOCO C — §6/§10.5: estrutura INVERSA (banco é sujeito, cita o local)")
print("=" * 96)
check(_E in det("Banorte anuncia naming rights de novo estádio"),
      "[5] 'Banorte anuncia naming rights de novo estádio' → banco CONTINUA atribuído")
check(_E in det("Banorte patrocina estádio e amplia presença de marca"),
      "[5b] banco como sujeito citando estádio → continua atribuído")

print()
print("=" * 96)
print("BLOCO D — §10.6/§10.7: sem impacto fora do Banorte")
print("=" * 96)
check("B3" not in det("Banco Mercantil avalia novo follow-on na B3 em março"),
      "[6a] B7b-3a (B3 praça) preservado")
check("B3" in det("B3 anuncia novo CEO"), "[6b] B3 como sujeito preservada")
check(det("Vale informa sobre Plano de Recuperação Judicial da Samarco")
      >= {"Vale", "Samarco Mineração"},
      "[7] artigo não é removido globalmente — Vale/Samarco seguem detectadas")
check("MercadoLibre" not in det("Prefeitura do Rio e BBCE assinam acordo para aquisição "
                                 "de energia do mercado livre"),
      "[6c] B1 (MercadoLibre) preservado")

print()
print("=" * 96)
print("BLOCO E — cadastro: nada além de mention_guard")
print("=" * 96)
_w = next(x for x in cfg["watchlist"] if x["name"] == _E)
check(_w.get("aliases") == ["Banorte", "Grupo Financiero Banorte"],
      "[§17] aliases do Banorte inalterados")
check(list((_w.get("mention_guard") or {}).get("contexto_patterns") or []) == [r"\bestadio\s+{A}\b"],
      "[§5] um único pattern, com termo comprovado pelo registro real")
check("entity_cues" not in _w and "search_terms" not in _w,
      "[§16] nenhum outro campo cadastral adicionado")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-3b: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
