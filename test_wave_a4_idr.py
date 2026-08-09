#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_wave_a4_idr.py — 4I.2 Wave A4: "Issuer Default Rating" ≠ default econômico."""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


print("=" * 96)
print("BLOCO A — nomenclatura de rating não é default econômico")
print("=" * 96)
check("default" not in pontua(
        "Fitch Ratings Upgrades Term Issuer Default Rating on British American Tobacco (BTI)",
        "British American Tobacco"),
      "[1 BAT/gold] UPGRADE de Issuer Default Rating NÃO pontua default")
check(sa.is_default_nomenclatura_de_rating(
        "Fitch affirms Long-Term Issuer Default Rating at BBB"),
      "[2] IDR affirmation reconhecida como nomenclatura")
check(sa.is_default_nomenclatura_de_rating(
        "Fitch downgrades Foreign Currency Issuer Default Rating to BB"),
      "[3] IDR downgrade também é nomenclatura (a ação é de RATING, não default)")
check(sa.is_default_nomenclatura_de_rating(
        "O contrato prevê cláusulas de default para descumprimento de covenants"),
      "[6] 'cláusulas de default' citadas juridicamente não são default acionado")

print()
print("=" * 96)
print("BLOCO B — default econômico REAL continua sendo default")
print("=" * 96)
check(not sa.is_default_nomenclatura_de_rating(
        "Company defaults on its bonds after missed payment of USD 300 million"),
      "[4] 'defaults on its bonds / missed payment' NÃO é nomenclatura")
check("default" in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Pemex (Petróleos Mexicanos)"),
      "[4b] default econômico real (impago) continua pontuando")
check(not sa.is_default_nomenclatura_de_rating(
        "Fitch downgrades Issuer Default Rating after the company defaults on its debt "
        "with a missed payment"),
      "[5] IDR mencionado JUNTO de default econômico real → NÃO desarma o evento")

print()
print("=" * 96)
print("BLOCO C — ação de rating legítima preservada (§13/§25)")
print("=" * 96)
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"),
      "[7] downgrade real continua pontuando rebaixamento_rating")
check("rebaixamento_rating" in pontua(
        "Fitch rebaixa rating da Tupy e acende sinal de alerta para investidores na "
        "bolsa de valores", "Tupy"),
      "[8] segundo downgrade real preservado")
_ids = {e["id"] for e in cfg["taxonomy"]}
check("idr_action" not in _ids,
      "[9] nenhum evento novo criado para IDR (§25)")
check(next(e for e in cfg["taxonomy"] if e["id"] == "default")["score"] == 100,
      "[10] peso-base de 'default' inalterado (100)")

print()
print("=" * 96)
print(f"RESULTADO WAVE A4 (IDR): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
