#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b2_follow_on.py — 4I.2 Wave B7b-2.

Participar/aportar/subscrever no follow-on de OUTRA companhia não é
follow-on próprio. Opera por EMPRESA × EVENTO: o emissor real mantém o seu.
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


def pontua(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


_T1 = "BTG Pactual Passa A Deter 18,76% Da Light Após Homologação De Aumento De Capital"
_T2 = "Aegea aprova aumento de capital e Itaúsa (ITSA4) pode aportar até R$ 1,5 bilhão"
_T3 = "Itaúsa planeja ampliar fatia na Aegea em aumento de capital de até R$ 1,5 bilhão"

print("=" * 96)
print("BLOCO A — os 3 casos reais, um a um (§9)")
print("=" * 96)
check("follow_on" not in pontua(_T1, "BTG Pactual"),
      "[1 BTG/Light] investidora NÃO recebe follow_on")
check("follow_on" not in pontua(_T2, "Itaúsa"),
      "[2 Itaúsa/Aegea #1] investidora NÃO recebe follow_on (ticker entre parênteses)")
check("follow_on" not in pontua(_T3, "Itaúsa"),
      "[3 Itaúsa/Aegea #2] investidora NÃO recebe follow_on")
check("follow_on" in pontua(_T2, "Aegea Saneamento"),
      "[2b] Aegea — o EMISSOR real — MANTÉM follow_on no mesmo artigo")
check("follow_on" in pontua(_T3, "Aegea Saneamento"),
      "[3b] idem no segundo artigo")

print()
print("=" * 96)
print("BLOCO B — §10: pares TRUE issuer × FALSE investor")
print("=" * 96)
check("follow_on" in pontua("Itaúsa anuncia follow-on de R$ 5 bilhões", "Itaúsa"),
      "[4] TRUE issuer: 'Itaúsa anuncia follow-on' → Itaúsa recebe")
check("follow_on" in pontua("BTG Pactual anuncia follow-on de R$ 2 bilhões", "BTG Pactual"),
      "[5] TRUE issuer: 'BTG anuncia follow-on' → BTG recebe")
check("follow_on" not in pontua(
        "Itaúsa subscreve R$ 1 bilhão no aumento de capital da Aegea", "Itaúsa"),
      "[6] FALSE investor: 'subscreve … da Aegea' → Itaúsa não recebe")
check("follow_on" not in pontua(
        "BTG Pactual participa do aumento de capital da Light", "BTG Pactual"),
      "[7] FALSE investor: 'participa do aumento de capital da Light' → BTG não recebe")

print()
print("=" * 96)
print("BLOCO C — §6: por empresa × por evento (não rejeita o artigo)")
print("=" * 96)
_MISTO = "Light anuncia follow-on e BTG Pactual participa da oferta"
check("follow_on" not in pontua(_MISTO, "BTG Pactual"),
      "[8] no mesmo artigo, investidora não recebe")
check(sa.detect_follow_on_de_terceiro(_MISTO, "BTG Pactual", ["BTG Pactual"]) != "",
      "[8b] emissor terceiro (Light) identificado")
check(sa.detect_follow_on_de_terceiro(
        "Itaúsa anuncia follow-on de R$ 5 bilhões", "Itaúsa", ["Itaúsa"]) == "",
      "[9] emissora própria → detector devolve vazio (não inventa terceiro)")
check(sa.detect_follow_on_de_terceiro(
        "Aegea aprova aumento de capital", "Aegea Saneamento", ["Aegea Saneamento"]) == "",
      "[9b] sem papel de investidora, o gate não atua")

print()
print("=" * 96)
print("BLOCO D — §8: M&A não é afetado (Wave C congelada)")
print("=" * 96)
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[10] M&A legítimo do BTG preservado")
check("ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group"),
      "[11] Cigna/Evernorth preservado")

print()
print("=" * 96)
print("BLOCO E — §18: regressões das waves anteriores")
print("=" * 96)
check("troca_ceo" not in pontua(
        "Novo CEO da Pemex vai viajar ao Brasil para avançar agenda de parceria com a "
        "Petrobras", "Petrobras"), "[12] B7b-1 troca_ceo preservado")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração"), "[13] Vale/Samarco nos dois lados")
check("fraude" not in pontua(
        "Truist Bank warns customers about phishing, check fraud and text scams",
        "Truist Financial"), "[14] B4 Truist preservado")
check("default" not in pontua(
        "Apoyo de EEUU en litigio YPF impulsa avances en acuerdos millonarios por "
        "default argentino", "YPF"), "[15] B5 YPF preservado")
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"), "[16] rating legítimo preservado")

print()
print("=" * 96)
print("BLOCO F — B7b-2.1: precedência own-issuer × investidora (§3)")
print("=" * 96)
# A limitação suspeitada foi REFUTADA pelo trace: a proteção já é estrutural.
# `_PAPEL_INVESTIDORA` exige o verbo de aporte ADJACENTE ao nome da
# monitorada. Em "Itaúsa ANUNCIA follow-on … e aporta", o nome é seguido de
# "anuncia" — não há evidência de papel de investidora, e o evento próprio
# sobrevive. Nenhum override precisou ser implementado.
_ONLY_INV = "Itaúsa aporta R$ 1 bilhão no aumento de capital da Aegea"
check("follow_on" not in pontua(_ONLY_INV, "Itaúsa"),
      "[B1 ONLY INVESTOR] Itaúsa não recebe")
check("follow_on" in pontua(_ONLY_INV, "Aegea Saneamento"),
      "[B1b ONLY INVESTOR] Aegea (emissora) recebe")

check("follow_on" in pontua("Itaúsa anuncia follow-on de R$ 2 bilhões", "Itaúsa"),
      "[B2 ONLY ISSUER] Itaúsa recebe")

_BOTH = ("Itaúsa anuncia follow-on de R$ 2 bilhões e aporta R$ 1 bilhão no aumento "
         "de capital da Aegea")
check("follow_on" in pontua(_BOTH, "Itaúsa"),
      "[B3 BOTH] emissão própria vence a supressão por papel de investidora")
check("follow_on" in pontua(_BOTH, "Aegea Saneamento"),
      "[B3b BOTH] Aegea também mantém o seu")
check(sa.detect_follow_on_de_terceiro(_BOTH, "Itaúsa", ["Itaúsa"]) == "",
      "[B3c BOTH] detector não dispara: nome seguido de 'anuncia', não de 'aporta'")

_DIST = "Itaúsa avalia investimento. Aegea anuncia follow-on."
check(sa.detect_follow_on_de_terceiro(_DIST, "Itaúsa", ["Itaúsa"]) == "",
      "[B4 DISTÂNCIA] follow-on distante da Aegea não é capturado como papel da Itaúsa")

print()
print("=" * 96)
print("BLOCO G — §4: os 3 casos reais continuam corrigidos após B7b-2.1")
print("=" * 96)
check("follow_on" not in pontua(_T1, "BTG Pactual"), "[B5] BTG/Light continua corrigido")
check("follow_on" not in pontua(_T2, "Itaúsa"), "[B6] Itaúsa/Aegea #1 continua corrigido")
check("follow_on" not in pontua(_T3, "Itaúsa"), "[B7] Itaúsa/Aegea #2 continua corrigido")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-2: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
