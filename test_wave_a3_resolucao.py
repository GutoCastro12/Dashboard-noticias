#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_a3_resolucao.py — 4I.2 Wave A3.

RESOLUTION_OF_PRIOR_NEGATIVE_EVENT: o evento negativo aconteceu de fato no
passado e a notícia atual informa encerramento/saída/cura/quitação. Não é
negação (§4). O fato é preservado (bucket informativo), não apagado (§17).
Nenhuma taxonomia positiva criada.
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
KW = sa._keywords_por_evento(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(title, company, summary=""):
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


print("=" * 96)
print("BLOCO A — casos reais do gold 4I")
print("=" * 96)
check("recuperacao_judicial" not in pontua(
        "Samarco informa encerramento da recuperação judicial: entenda o caso",
        "Samarco Mineração"),
      "[1 Samarco] encerramento da RJ NÃO pontua nova RJ")
check("falencia" not in pontua(
        "De tocar fondo a recuperar el vuelo: Así logró Aeroméxico salir de la quiebra "
        "y volver a cotizar en Bolsa", "Grupo Aeroméxico"),
      "[2 Aeroméxico] 'salir de la quiebra' NÃO pontua nova falência")
check("emissao_divida" not in pontua(
        "Localiza (RENT3) anuncia resgate antecipado integral da 21ª emissão de debêntures",
        "Localiza"),
      "[3 Localiza] resgate antecipado NÃO pontua nova emissão de dívida")
check("indice" not in pontua("Hapvida mantém rating AA no MSCI ESG", "Hapvida"),
      "[4 Hapvida] 'rating mantido' NÃO pontua evento negativo")

print()
print("=" * 96)
print("BLOCO B — NÃO REGRESSÃO: evento negativo REAL continua pontuando")
print("=" * 96)
check("recuperacao_judicial" in pontua(
        "Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão que impacta "
        "2,2 mil funcionários", "Tok&Stok"),
      "[5] RJ realmente deferida continua pontuando")
check("falencia" in pontua("CIBanco, una quiebra no merecida", "CIBanco"),
      "[6] falência real continua pontuando")
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"),
      "[7] downgrade real continua pontuando")
check("default" in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Pemex (Petróleos Mexicanos)"),
      "[8] default real continua pontuando")

print()
print("=" * 96)
print("BLOCO C — REGRESSÃO REAL ENCONTRADA E CORRIGIDA NESTA WAVE")
print("=" * 96)
# Este caso derrubou um positivo legítimo do gold na 1ª tentativa da A3:
# "mantém rating" acionava resolução e matava um outlook negativo REAL.
check("outlook_negativo" in pontua(
        "Cosan (CSAN3): Fitch revisa perspectiva para negativa por alavancagem e "
        "mantém rating 'BB' para dívidas internacionais", "Cosan"),
      "[9] 'revisa perspectiva para negativa E mantém rating' → outlook negativo "
      "PRESERVADO (ação negativa vence a marca de resolução)")
r = sa.detect_event_resolution(
    "Fitch revisa perspectiva para negativa e mantém rating BB", KW["outlook_negativo"])
check(not r["resolved"],
      "[9b] regra: resolução não se aplica quando há ação negativa explícita na mesma proposição")

print()
print("=" * 96)
print("BLOCO D — resolução ≠ negação (§4)")
print("=" * 96)
r_neg = sa.detect_event_negation("Samarco informa encerramento da recuperação judicial",
                                 KW["recuperacao_judicial"])
r_res = sa.detect_event_resolution("Samarco informa encerramento da recuperação judicial",
                                   KW["recuperacao_judicial"])
check(not r_neg["negated"] and r_res["resolved"],
      "encerramento de RJ é RESOLUÇÃO, não negação (o evento existiu de verdade)")
r_neg2 = sa.detect_event_negation("Empresa nega pedido de recuperação judicial",
                                  KW["recuperacao_judicial"])
r_res2 = sa.detect_event_resolution("Empresa nega pedido de recuperação judicial",
                                    KW["recuperacao_judicial"])
check(r_neg2["negated"] and not r_res2["resolved"],
      "negação de RJ é NEGAÇÃO, não resolução (o evento nunca aconteceu)")

print()
print("=" * 96)
print("BLOCO E — nenhuma taxonomia positiva criada (§4/§17)")
print("=" * 96)
_ids = {e["id"] for e in cfg["taxonomy"]}
check("resolucao_evento" not in _ids and "saida_recuperacao" not in _ids,
      "nenhum evento novo de 'resolução' foi adicionado à taxonomia")
check(next(e for e in cfg["taxonomy"] if e["id"] == "recuperacao_judicial")["score"] == 100,
      "peso-base de 'recuperacao_judicial' inalterado (100)")

print()
print("=" * 96)
print(f"RESULTADO WAVE A3 (resolução): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
