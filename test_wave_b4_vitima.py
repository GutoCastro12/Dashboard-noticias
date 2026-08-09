#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b4_vitima.py — 4I.2 Wave B4: vítima ≠ autora da fraude.

Papel e fase jurídica são EIXOS DISTINTOS (§5). O papel de AGENTE vence
sempre: cue incidental de vítima noutra oração não apaga fraude realmente
cometida/condenada pela companhia (§10). Peso de `fraude` intocado (§15).
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

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


def pontua(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def papel(t, emp):
    return sa.detect_fraud_role(t, emp, AL.get(emp) or [emp])


print("=" * 96)
print("BLOCO A — caso crítico do gold + vítimas explícitas")
print("=" * 96)
_TRUIST = "Truist Bank warns customers about phishing, check fraud and text scams"
check("fraude" not in pontua(_TRUIST, "Truist Financial"),
      "[1 Truist/gold] banco que ALERTA clientes NÃO recebe fraude direta")
check(papel(_TRUIST, "Truist Financial") == "vitima",
      "[1b Truist] papel reconhecido como vítima mesmo com qualificador ('Truist BANK warns')")
check("fraude" not in pontua(
        "Vale foi vítima de fraude contábil praticada por fornecedor", "Vale"),
      "[2] empresa explicitamente vítima NÃO recebe fraude direta")
check("fraude" not in pontua(
        "Cliente apresenta documentos fraudulentos e causa prejuízo ao Bradesco", "Bradesco"),
      "[3/§8] banco fraudado por cliente NÃO recebe fraude")

print()
print("=" * 96)
print("BLOCO B — fraude REALMENTE cometida continua pontuando (§4/§15)")
print("=" * 96)
check("fraude" in pontua(
        "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC",
        "TIM Brasil"),
      "[5 TIM/gold] condenação da companhia continua pontuando")
check(papel("Vale admitiu fraude contábil nas demonstrações financeiras", "Vale") == "agente",
      "[4] companhia que ADMITE fraude é agente")
check(papel("Vale manipulou documentos e falsificou registros contábeis", "Vale") == "agente",
      "[4b] companhia que manipula/falsifica é agente")

print()
print("=" * 96)
print("BLOCO C — TRUE AGENT × TRUE VICTIM pareados (§12)")
print("=" * 96)
_V = "Golpistas aplicaram fraude contra a Vale e desviaram R$ 5 milhões"
_A = "Vale cometeu fraude contábil e desviou R$ 5 milhões em esquema fraudulento"
check(papel(_V, "Vale") == "vitima", f"[12-V] '{_V[:44]}…' → vítima")
check(papel(_A, "Vale") == "agente", f"[12-A] '{_A[:44]}…' → agente")
check("fraude" not in pontua(_V, "Vale"), "[12-V2] variante vítima NÃO pontua fraude")
check("fraude" in pontua(_A, "Vale"), "[12-A2] variante agente PONTUA fraude")

print()
print("=" * 96)
print("BLOCO D — multilíngue (pt/en/es)")
print("=" * 96)
check(papel("Estafadores defraudaron a Cemex por millones", "Cemex") == "vitima",
      "[es] 'defraudaron a <empresa>' → vítima")
check(papel("Esquema de fraude contra a Petrobras é desmontado", "Petrobras") == "vitima",
      "[pt] 'fraude contra <empresa>' → vítima")
check(papel("Scheme targeting Citigroup customers uncovered", "Citigroup") == "vitima",
      "[en] 'scheme targeting <empresa>' → vítima")

print()
print("=" * 96)
print("BLOCO E — funcionário/executivo (§7): sem regra automática")
print("=" * 96)
check(papel("Funcionário fraudou o Itaú Unibanco em esquema de desvio",
            "Itaú Unibanco") == "vitima",
      "[7] funcionário frauda a própria empresa → empresa é vítima, não autora")
check(papel("Justiça condena a Gerdau por fraude fiscal", "Gerdau") == "agente",
      "[8] fraude confirmada da companhia NÃO é apagada por regra ampla de vítima")

print()
print("=" * 96)
print("BLOCO F — precedência: agente vence cue incidental de vítima (§10)")
print("=" * 96)
_MISTO = ("Justiça condena a Vale por fraude contábil; separadamente, "
          "golpistas aplicaram golpe contra a Vale")
check(papel(_MISTO, "Vale") == "agente",
      "[10] com AGENTE e vítima no mesmo texto, o papel de agente prevalece")

print()
print("=" * 96)
print("BLOCO G — invariantes canônicas e Waves anteriores")
print("=" * 96)
check("fraude" not in pontua(
        "JPMorgan Chase (NYSE:JPM) fraud-claim scrutiny weighs on stock at analysts' target",
        "JPMorgan Chase"), "[13a] JPMorgan (Wave A1) preservado")
check("fraude" not in pontua(
        "2 of Michigan's largest health systems sue CVS Health alleging fraud in drug "
        "pricing program", "CVS Health"), "[13b] CVS (Wave A1) preservado")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Samarco Mineração"),
      "[11] Vale/Samarco preservado nos dois lados")
check("falencia" not in pontua(
        "A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo "
        "BTG Pactual", "BTG Pactual"), "[12c] BTG/Digimais preservado")
check("falencia" in pontua("CIBanco, una quiebra no merecida", "CIBanco"),
      "[14] default/falência própria (CIBanco) preservado")
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[15] M&A legítimo preservado")
check(next(e for e in cfg["taxonomy"] if e["id"] == "fraude")["score"] == 90,
      "[§15] peso-base de 'fraude' inalterado (90)")

print()
print("=" * 96)
print(f"RESULTADO WAVE B4: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
