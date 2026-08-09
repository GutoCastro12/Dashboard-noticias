#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b5_soberano.py — 4I.2 Wave B5: soberano ≠ emissor corporativo.

Evento de crédito do país/governo/tesouro não é automaticamente evento da
companhia. Estatal NÃO é o Estado (§5), e a proteção não pode blindar
estatal contra risco próprio (§6). Nacionalidade nunca é a regra (§7).
Nenhuma taxonomia nova (§9); `country` do cadastro é usado só para
RECONHECER o soberano, nunca para atribuir (§11).
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
KW = sa._keywords_por_evento(cfg)


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


def sob(t, emp, ev="default"):
    return sa.detect_sovereign_subject(t, KW[ev], emp, AL.get(emp) or [emp],
                                       sa._country_de(cfg, emp))["soberano"]


print("=" * 96)
print("BLOCO A — caso crítico do gold")
print("=" * 96)
_YPF = ("Apoyo de EEUU en litigio YPF impulsa avances en acuerdos millonarios por "
        "default argentino")
check("default" not in pontua(_YPF, "YPF"),
      "[1 YPF/gold] 'default argentino' NÃO pontua default para a YPF")
check(sob(_YPF, "YPF"), "[1b YPF] sujeito reconhecido como soberano (demônimo adjetivo)")

print()
print("=" * 96)
print("BLOCO B — TRUE SOVEREIGN × TRUE CORPORATE (§15)")
print("=" * 96)
_S = "Argentina defaults on sovereign debt; YPF bonds fall"
_C = "YPF defaults on its bond payment amid Argentina crisis"
check("default" not in pontua(_S, "YPF"), f"[15-S] '{_S[:42]}…' → YPF NÃO recebe default")
check("default" in pontua(_C, "YPF"), f"[15-C] '{_C[:42]}…' → YPF RECEBE default")
check(sob(_S, "YPF") and not sob(_C, "YPF"),
      "[15b] a diferença vem do sujeito/devedor, não da presença de 'Argentina'+'default'")

print()
print("=" * 96)
print("BLOCO C — estatal ≠ Estado, mas mantém risco próprio (§5/§6)")
print("=" * 96)
check("default" in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Pemex (Petróleos Mexicanos)"),
      "[3 Pemex/gold] estatal com inadimplemento PRÓPRIO continua pontuando")
check("default" in pontua("Petrobras não paga debênture no vencimento e entra em inadimplência da dívida", "Petrobras"),
      "[6] estatal brasileira em default próprio pontua (país mencionado ou não)")
check(not sob("Petrobras não paga debênture no vencimento e entra em inadimplência da dívida", "Petrobras"),
      "[6b] devedor corporativo explícito vence qualquer marca soberana")
check("default" not in pontua(
        "Governo brasileiro renegocia dívida pública soberana e Petrobras acompanha",
        "Petrobras"),
      "[3b] evento soberano não vira evento da estatal")

print()
print("=" * 96)
print("BLOCO D — companhia privada e §7 (nacionalidade não é regra)")
print("=" * 96)
check("default" not in pontua(
        "Argentina entra em default de sua dívida soberana; Telecom Argentina cai na bolsa",
        "Telecom Argentina"),
      "[4] companhia privada não herda evento soberano do país")
check("default" in pontua(
        "Telecom Argentina deixa de pagar juros de sua dívida corporativa", "Telecom Argentina"),
      "[7] evento corporativo próprio sobrevive mesmo com país no nome da empresa")

print()
print("=" * 96)
print("BLOCO E — família de RATING preservada (§13/§25)")
print("=" * 96)
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"), "[9] downgrade corporativo real preservado")
check("rebaixamento_rating" in pontua(
        "Rebaja de calificación de Colombia a manos de S&P también arrastró a Ecopetrol",
        "Ecopetrol"), "[8b] rebaixamento real por arrasto soberano (CORRECT no gold) preservado")
check("outlook_negativo" in pontua(
        "Cosan (CSAN3): Fitch revisa perspectiva para negativa por alavancagem e mantém "
        "rating 'BB' para dívidas internacionais", "Cosan"), "[A3] outlook negativo preservado")
check("default" not in pontua(
        "Fitch Ratings Upgrades Term Issuer Default Rating on British American Tobacco (BTI)",
        "British American Tobacco"), "[A4] IDR da Wave A4 preservado")

print()
print("=" * 96)
print("BLOCO F — nenhuma taxonomia nova (§9) e nenhum peso alterado (§27)")
print("=" * 96)
_ids = {e["id"] for e in cfg["taxonomy"]}
check(not {"default_soberano", "risco_pais", "sovereign_stress"} & _ids,
      "[9] nenhuma família soberana criada na taxonomia")
check(next(e for e in cfg["taxonomy"] if e["id"] == "default")["score"] == 100,
      "[27] peso-base de 'default' inalterado (100)")

print()
print("=" * 96)
print("BLOCO G — invariantes das waves anteriores")
print("=" * 96)
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Samarco Mineração"),
      "[11] Vale/Samarco preservado nos dois lados")
check("falencia" not in pontua(
        "Itaú Unibanco tenta suspender na Justiça decisão que converteu recuperação da Oi "
        "em falência", "Itaú Unibanco"), "[12] B3 credor/devedor preservado")
check("fraude" not in pontua(
        "Truist Bank warns customers about phishing, check fraud and text scams",
        "Truist Financial"), "[13] B4 vítima preservado")
check("fraude" not in pontua(
        "JPMorgan Chase (NYSE:JPM) fraud-claim scrutiny weighs on stock at analysts' target",
        "JPMorgan Chase"), "[14] Wave A1 (JPMorgan) preservado")
check("falencia" in pontua("CIBanco, una quiebra no merecida", "CIBanco"),
      "[15c] falência própria (CIBanco) preservada")
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[16] M&A legítimo preservado")

print()
print("=" * 96)
print(f"RESULTADO WAVE B5: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
