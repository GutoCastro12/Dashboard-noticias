#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b3_credor_devedor.py — 4I.2 Wave B3.

O evento de crédito pertence a QUEM DEVE. A decisão vem da relação textual
credor/devedor, NUNCA do setor da empresa (§5): Grupo México demonstra que o
credor pode não ser instituição financeira, e um banco continua podendo
sofrer evento de crédito próprio (§6).
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


print("=" * 96)
print("BLOCO A — os 3 casos críticos auditados (regressão canônica)")
print("=" * 96)
_BB = "Vazamento sobre calote de R$ 3,6 bi do Banco do Brasil está na mira da CVM"
check("default" not in pontua(_BB, "Banco do Brasil"),
      "[1 BB/gold] credor lesado NÃO recebe default direto")
check(sa.detect_debtor_subject(_BB, "Banco do Brasil", ["Banco do Brasil"])
      == "__monitorada_e_credora__",
      "[1b BB] detectado como credor por construção de valor ('calote de R$ X DO <monitorada>')")
check("default" not in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex", "Grupo México"),
      "[2 Grupo México/gold] credor não financeiro NÃO herda default do devedor")
check("falencia" not in pontua(
        "Itaú Unibanco tenta suspender na Justiça decisão que converteu recuperação da Oi "
        "em falência", "Itaú Unibanco"),
      "[3 Itaú/gold] credor NÃO herda falência do devedor")

print()
print("=" * 96)
print("BLOCO B — o DEVEDOR real continua pontuando (§6)")
print("=" * 96)
check("default" in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
        "Pemex (Petróleos Mexicanos)"),
      "[7a devedor+credor monitorados] Pemex (devedor) PONTUA o mesmo evento")
check("default" not in pontua(
        "Detuvo Grupo México sus plataformas petroleras por impago de Pemex", "Grupo México"),
      "[7b devedor+credor monitorados] Grupo México (credor) NÃO pontua — atribuição por empresa")
check("recuperacao_judicial" in pontua(
        "Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão que impacta "
        "2,2 mil funcionários", "Tok&Stok"),
      "[5] companhia não financeira em evento PRÓPRIO continua pontuando (com valor na manchete)")
check("falencia" in pontua("CIBanco, una quiebra no merecida", "CIBanco"),
      "[4] banco em evento de crédito PRÓPRIO continua pontuando (nada de exceção por setor)")

print()
print("=" * 96)
print("BLOCO C — valor monetário nunca é entidade (invariante adjudicada de test_semantica[13])")
print("=" * 96)
for _t in ("Empresa X pede recuperação judicial de R$ 1,1 bilhão",
           "Companhia Y assume dívida de R$ 500 milhões",
           "Emissor Z declara default de R$ 3,6 bilhões",
           "Grupo W registra inadimplência de US$ 250 mi"):
    _e = sa.detect_debtor_subject(_t, "Tok&Stok", ["Tok&Stok"])
    check(not __import__("re").search(r"r\$|us\$|\d|bilh|milh|^bi$|^mi$", _e or "",
                                       __import__("re").I),
          f"[12] valor monetário não vira entidade (obtido {_e or 'vazio'!r})")

print()
print("=" * 96)
print("BLOCO D — entidade curta legítima (§8): 'Oi' é entidade, 'R$'/'bi' não são")
print("=" * 96)
check(sa.detect_debtor_subject(
        "decisão que converteu recuperação da Oi em falência", "Itaú Unibanco",
        ["Itaú Unibanco"]) == "oi",
      "[8a] 'Oi' (2 caracteres) É reconhecida como entidade devedora")
check(sa.detect_debtor_subject("recuperação judicial de R$ 1,1 bilhão", "X", ["X"]) == "",
      "[8b] 'R$ 1,1 bilhão' NÃO é entidade — proteção é semântica, não por comprimento")

print()
print("=" * 96)
print("BLOCO E — varredura de candidatos: 1º inválido não aborta a busca (§7)")
print("=" * 96)
# 1º possessivo nomeia a própria monitorada (inválido) → deve CONTINUAR e achar o 2º
check(sa.detect_debtor_subject(
        "recuperação da Vale foi citada; a falência da Alpha Mineradora foi decretada",
        "Vale", ["Vale"]) == "alpha mineradora",
      "[7-i] 1º candidato inválido (própria monitorada) → detector segue e acha o 2º")
check(sa.detect_debtor_subject(
        "recuperação da Vale e falência da Vale foram discutidas", "Vale", ["Vale"]) == "",
      "[7-ii] nenhum candidato válido → devolve vazio (não inventa entidade)")
check(sa.detect_debtor_subject("empresa comenta o mercado de crédito", "Vale", ["Vale"]) == "",
      "[7-iii] sem construção possessiva de insolvência → vazio")

print()
print("=" * 96)
print("BLOCO F — invariantes canônicas preservadas")
print("=" * 96)
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale"),
      "[9] Vale/Samarco: Vale não recebe a RJ")
check("recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Samarco Mineração"),
      "[9b] Vale/Samarco: Samarco MANTÉM a RJ direta")
check("falencia" not in pontua(
        "A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo "
        "BTG Pactual", "BTG Pactual"),
      "[10] BTG/Digimais preservado")
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"),
      "[12b] M&A legítimo sem efeito colateral")
check("fraude" in pontua(
        "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC",
        "TIM Brasil"),
      "[11] fraude confirmada da Wave A continua correta")

print()
print("=" * 96)
print(f"RESULTADO WAVE B3: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
