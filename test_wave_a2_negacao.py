#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_a2_negacao.py — 4I.2 Wave A2.

Negação ESCOPADA AO EVENTO (§12): a negação só derruba o evento cujas
menções estão todas em proposição negada. Nunca "existe 'não' no texto →
nada pontua". O vocabulário de cada evento vem da PRÓPRIA taxonomia de
produção (`_keywords_por_evento`), não de lista paralela.
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
check("ma" not in pontua("Não há conversas sobre fusão e aquisição neste momento, "
                          "diz diretor-geral da Klabin", "Klabin"),
      "[1 Klabin] 'não há conversas sobre fusão e aquisição' NÃO pontua M&A")
check("ma" not in pontua("Citigroup Em Foco Após Banco Negar Relatório de Planos de "
                          "Aquisição de Credor Regional", "Citigroup"),
      "[2 Citigroup] 'negar planos de aquisição' NÃO pontua M&A")
check("ma" not in pontua("Hapvida (HAPV3) anuncia rescisão de contrato vinculante para "
                          "aquisição de hospital da Oncoclínicas", "Hapvida"),
      "[3 Hapvida] 'rescisão do contrato de aquisição' NÃO pontua M&A")
check("ma" not in pontua("Vibra (VBBR3): CVM confirma que OPA não pode ser exigida por "
                          "compra de ações da Comerc", "Vibra Energia"),
      "[3b Vibra] 'OPA não pode ser exigida' NÃO pontua M&A")

print()
print("=" * 96)
print("BLOCO B — negação por família (pt/en/es)")
print("=" * 96)
check("ma" not in pontua("Vale denies it is in talks for an acquisition of Alpha Mining Corp", "Vale"),
      "[4 en] 'denies ... in talks for an acquisition' NÃO pontua M&A")
check("ma" not in pontua("Cemex niega que existan conversaciones para una adquisición de Alpha Cementos", "Cemex"),
      "[4b es] 'niega ... conversaciones para una adquisición' NÃO pontua M&A")
check("recuperacao_judicial" not in pontua(
        "Vale nega pedido de recuperação judicial e diz que não há decisão", "Vale"),
      "[5] negação de RJ NÃO pontua recuperacao_judicial")
check("falencia" not in pontua(
        "Vale desmente rumores de falência e afirma que não há pedido de falência em curso", "Vale"),
      "[6] negação de falência NÃO pontua falencia")
check("default" not in pontua(
        "Vale nega default e afirma que não houve calote no pagamento", "Vale"),
      "[7] negação de default NÃO pontua default")

print()
print("=" * 96)
print("BLOCO C — NÃO REGRESSÃO: afirmativo equivalente continua pontuando")
print("=" * 96)
check("ma" in pontua("Klabin anuncia aquisição da Embalagens Alpha S.A. por R$ 1 bilhão",
                     "Klabin"),
      "[8] M&A afirmativo continua pontuando")
check("recuperacao_judicial" in pontua(
        "Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão que impacta "
        "2,2 mil funcionários", "Tok&Stok"),
      "[8b] RJ afirmativa REAL (caso CORRECT do gold) continua pontuando")

print()
print("=" * 96)
print("BLOCO D — ESCOPO: negar A não pode apagar B (§12)")
print("=" * 96)
ids = pontua("Vale nega conversas sobre aquisição da Alpha Mineração, mas confirma emissão de debêntures "
             "de R$ 1 bilhão", "Vale")
check("ma" not in ids, "[9] evento negado (M&A) não pontua")
check("emissao_divida" in ids,
      f"[9b] evento confirmado na MESMA matéria (emissão de dívida) É PRESERVADO "
      f"(obtido={sorted(ids)})")

print()
print("=" * 96)
print("BLOCO E — negação de TERCEIRO não apaga evento direto da monitorada")
print("=" * 96)
# Verificado no nível da regra (o pipeline completo depende de atribuição de
# sujeito, coberta pelo gold com dados reais — 115/115 positivos preservados).
_txt_terceiro = ("Vale nega que a Samarco tenha pedido nova recuperação judicial; "
                 "Vale anuncia aquisição da Alpha Mineração S.A.")
check(not sa.detect_event_negation(_txt_terceiro, KW["ma"])["negated"],
      "[10] negação sobre TERCEIRO (RJ da Samarco) NÃO nega o M&A direto da monitorada")
check(sa.detect_event_negation(_txt_terceiro, KW["recuperacao_judicial"])["negated"],
      "[10b] …e a RJ negada do terceiro continua corretamente negada")

print()
print("=" * 96)
print("BLOCO F — a negação NUNCA é global")
print("=" * 96)
r = sa.detect_event_negation("Empresa não comentou o mercado. Empresa anuncia aquisição da Beta",
                             KW["ma"])
check(not r["negated"],
      "negação em proposição SEM o evento não nega o evento (mentions/negadas separados)")
r2 = sa.detect_event_negation("Empresa anuncia aquisição da Beta", KW["ma"])
check(not r2["negated"] and r2["mentions"] == 1,
      "texto afirmativo: evento mencionado, nada negado")
r3 = sa.detect_event_negation("Empresa nega qualquer aquisição", [])
check(not r3["negated"], "sem keywords do evento, gate não atua (nunca adivinha)")

print()
print("=" * 96)
print(f"RESULTADO WAVE A2 (negação): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
