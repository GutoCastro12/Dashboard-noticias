#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b6_carteira.py — 4I.2 Wave B6.

Carteira / produto / instrumento ≠ default do emissor, e risco PROSPECTIVO ≠
evento consumado. Causa classificada como CLASSIFICATION_FALSE_POSITIVE
(§3/§19): "créditos con mayor RIESGO DE impago" não é default nenhum.

Nenhuma taxonomia nova (§11); a proteção cede sempre a devedor corporativo
explícito, então não vira blindagem de banco (§8).
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


def nc(t, emp, ev="default"):
    return sa.detect_evento_nao_consumado(t, KW[ev], emp, AL.get(emp) or [emp])


_BAN = "Grupo Financiero Banorte"

print("=" * 96)
print("BLOCO A — caso crítico do gold")
print("=" * 96)
_G = ("Banorte, Banamex e Inbursa disparan créditos con mayor riesgo de impago y "
      "Moody's lanza alerta")
check("default" not in pontua(_G, _BAN),
      "[1 Banorte/gold] 'créditos con mayor RIESGO DE impago' NÃO pontua default")
check(nc(_G, _BAN)["motivo"] == "risco_prospectivo",
      "[1b Banorte] causa reconhecida como risco prospectivo, não evento consumado")

print()
print("=" * 96)
print("BLOCO B — TRUE PORTFOLIO × TRUE ISSUER DEFAULT (§16)")
print("=" * 96)
_P = "Banorte reporta aumento de morosidad en su cartera de crédito"
_I = "Banorte não paga debênture e entra em inadimplência da dívida própria"
check("default" not in pontua(_P, _BAN), f"[16-P] carteira: '{_P[:40]}…' → NÃO pontua")
check("default" in pontua(_I, _BAN), f"[16-I] obrigação própria: '{_I[:40]}…' → PONTUA")
check("default" not in pontua(_P, _BAN) and not nc(_I, _BAN)["nao_consumado"],
      "[16b] a diferença vem do objeto/sujeito, não da presença de banco+termo de default")

print()
print("=" * 96)
print("BLOCO C — carteira/NPL/taxa (§5/§6/§12)")
print("=" * 96)
check("default" not in pontua(
        "Banorte registra alta do NPL e de créditos problemáticos na carteira", _BAN),
      "[4] NPL/créditos problemáticos ≠ default do banco")
check("default" not in pontua(
        "Default rate da carteira de crédito do Banorte sobe no trimestre", _BAN),
      "[3/§12] 'default rate' da carteira ≠ default do emissor")
check("default" not in pontua(
        "Clientes do Banorte entram em default em empréstimos concedidos", _BAN),
      "[2] default de clientes na carteira ≠ default do banco")

print()
print("=" * 96)
print("BLOCO D — §8: não vira blindagem de banco")
print("=" * 96)
check("default" in pontua(
        "Banorte deixa de pagar juros de sua dívida e entra em calote", _BAN),
      "[5/6] banco que não honra obrigação PRÓPRIA continua pontuando default")
check("falencia" in pontua("CIBanco, una quiebra no merecida", "CIBanco"),
      "[6b] falência própria de banco preservada")

print()
print("=" * 96)
print("BLOCO E — §10: não fabricar sujeito quando não há entidade nomeada")
print("=" * 96)
_SEM = "Banorte reports increase in defaults across its loan portfolio"
check(sa.detect_debtor_subject(_SEM, _BAN, AL.get(_BAN) or [_BAN]) == "",
      "[10] sem devedor nomeado, nenhum subject_company é fabricado")
check(sa.detect_debtor_subject("inadimplência da dívida própria", _BAN,
                               AL.get(_BAN) or [_BAN]) == "",
      "[10b] 'dívida própria' não vira entidade devedora")
check(sa.detect_debtor_subject("recuperação judicial de R$ 1,1 bilhão", "X", ["X"]) == "",
      "[11] valor monetário continua não sendo entidade")

print()
print("=" * 96)
print("BLOCO F — interoperação com B3 (§7/§9), sem duplicar")
print("=" * 96)
check("default" in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Pemex (Petróleos Mexicanos)"), "[12a] devedor nomeado (Pemex) continua pontuando")
check("default" not in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Grupo México"), "[8] credor (Grupo México) não pontua — atribuição por empresa")
check("falencia" not in pontua(
        "Itaú Unibanco tenta suspender na Justiça decisão que converteu recuperação da Oi "
        "em falência", "Itaú Unibanco"), "[12b] B3 credor/devedor preservado")

print()
print("=" * 96)
print("BLOCO G — invariantes das waves anteriores")
print("=" * 96)
check("default" not in pontua(
        "Apoyo de EEUU en litigio YPF impulsa avances en acuerdos millonarios por "
        "default argentino", "YPF"), "[13] B5 soberano preservado")
check("fraude" not in pontua(
        "Truist Bank warns customers about phishing, check fraud and text scams",
        "Truist Financial"), "[14] B4 vítima preservado")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Samarco Mineração"),
      "[15] Vale/Samarco preservado nos dois lados")
check("fraude" in pontua(
        "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC",
        "TIM Brasil"), "[17] fraude confirmada preservada")
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[18] M&A legítimo preservado")
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"), "[19] rating legítimo preservado")

print()
print("=" * 96)
print("BLOCO H — §11/§26: nenhuma taxonomia nova, nenhum peso alterado")
print("=" * 96)
_ids = {e["id"] for e in cfg["taxonomy"]}
check(not {"deterioracao_carteira", "npl", "credit_losses", "portfolio_default"} & _ids,
      "[11] nenhuma família de carteira criada na taxonomia")
check(next(e for e in cfg["taxonomy"] if e["id"] == "default")["score"] == 100,
      "[26] peso-base de 'default' inalterado (100)")

print()
print("=" * 96)
print(f"RESULTADO WAVE B6: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
