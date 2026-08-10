#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_hotfix_gm_company_profile.py — 4I.2 hotfix P0.

VERBETE/PERFIL DE COMPANHIA ≠ EVENTO ATUAL. Páginas enciclopédicas listam a
trajetória da empresa como TÓPICOS ("History, Growth, Bankruptcy, & Recovery").
A falência ali é item de sumário, não ocorrência.

Reusa a infraestrutura histórica existente: `detect_company_profile` alimenta
`detect_historical_reference`, e o caso cai na regra `R_HISTORICO` já validada
no precedente WSAW/2009 — sem mecanismo paralelo e sem event_id novo.

Sinal ESTRUTURAL, não lexical: exige lista de ≥2 tópicos de trajetória
separados por vírgula/&/pipe. `history` solto num título nunca basta.
Override obrigatório: verbo de ocorrência atual vence o marcador de perfil.

Sem hard-code de General Motors, Britannica ou da URL.
"""
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


def pontua(title, company, source="s"):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": source,
                              "domain": "exemplo.com", "pub_ts": 1786000000,
                              "pub_iso": "2026-08-07 04:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


_BRIT = "General Motors (GM) | History, Growth, Bankruptcy, & Recovery"

print("=" * 96)
print("BLOCO A — §8: o caso real (Encyclopedia Britannica)")
print("=" * 96)
check("falencia" not in pontua(_BRIT, "General Motors"),
      "[1] General Motors NÃO recebe `falencia` do verbete")
check(sa.detect_company_profile(_BRIT) != "",
      f"[2] perfil detectado: {sa.detect_company_profile(_BRIT)!r}")
check(sa.detect_historical_reference(_BRIT, 2026)["historical_reference"] is True,
      "[3] classificado como referência histórica — reusa R_HISTORICO")
check("General Motors" in rd.detect_companies({"title": _BRIT, "summary": ""},
                                               cfg["watchlist"]),
      "[4] a empresa CONTINUA detectada — a notícia não desaparece")

print()
print("=" * 96)
print("BLOCO B — §7: override de ocorrência atual (o gate que protege TRUEs)")
print("=" * 96)
for _t, _c, _ev in (("General Motors files for bankruptcy protection", "General Motors", "falencia"),
                    ("GM seeks bankruptcy protection amid growth crisis", "General Motors", "falencia"),
                    ("Company enters Chapter 11 after years of growth and recovery efforts",
                     "General Motors", "recuperacao_judicial"),
                    ("Americanas pede recuperação judicial", "Americanas", "recuperacao_judicial"),
                    ("Justiça decreta falência da Oi", "Oi", "falencia")):
    check(_ev in pontua(_t, _c), f"[TRUE atual] `{_ev}` preservado: {_t[:52]}")
    check(sa.detect_company_profile(_t) == "",
          f"[override] perfil NÃO dispara com verbo de ocorrência: {_t[:44]}")

print()
print("=" * 96)
print("BLOCO C — §5: `history` solto NÃO é perfil (regra estrutural, não lexical)")
print("=" * 96)
for _t in ("NextEra’s acquisition of Dominion would bring history of political fights",
           "EQT Corporation (EQT): A High-Growth Large Cap Stock Upgraded at Morgan Stanley",
           "Omnicare bankruptcy sale process, timeline approved by court",
           "Empresa comemora aniversário com foco em crescimento"):
    check(sa.detect_company_profile(_t) == "", f"[não-perfil] {_t[:64]}")

print()
print("=" * 96)
print("BLOCO D — §10: controles GM já corrigidos e o FP de causa distinta")
print("=" * 96)
_WSAW = ("This Day in History: June 1, 2009 - General Motors files for Chapter 11 "
         "reorganization")
check("recuperacao_judicial" not in pontua(_WSAW, "General Motors")
      and "falencia" not in pontua(_WSAW, "General Motors"),
      "[WSAW] artigo de 2009 continua não-scoreable")
check(sa.detect_historical_reference(_WSAW, 2026)["historical_reference"] is True,
      "[WSAW b] continua marcado como histórico pelo marcador original")
_LAW = "Justice Watch: General Motors Dilemma May Leave Scar on Bankruptcy Law"
check(sa.detect_company_profile(_LAW) == "",
      "[Law.com] não é perfil — sua correção veio de manual_correction, não desta regra")
_WW = ("Bankruptcy Court Orders Texas to Strike Allegations In State Data Privacy "
       "Suit Against General Motors")
check(sa.detect_company_profile(_WW) == "",
      "[White&Williams] NÃO é perfil — causa distinta (foro judicial), fora deste hotfix")

print()
print("=" * 96)
print("BLOCO E — §11: TRUE controls de insolvência preservados")
print("=" * 96)
check("recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Samarco Mineração"),
      "[R1] Samarco preservada")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale"),
      "[R2] Vale continua fora (Wave A)")
check("ma" not in pontua(
        "Âmbar Energia conclui a aquisição de 4 hidrelétricas da Cemig em MG", "Cemig"),
      "[R3] C1 seller preservado")
check("ma" not in pontua(
        "Grécia investirá 600 milhões de euros na aquisição dos três Embraer "
        "KC-390 Millennium", "Embraer"), "[R4] C2 preservado")

print()
print("=" * 96)
print(f"RESULTADO HOTFIX GM/PERFIL: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
