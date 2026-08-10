#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_c2_commercial_sale_not_ma.py — 4I.2 Wave C2.

VENDA COMERCIAL DE PRODUTO ≠ M&A. Um fabricante que vende N unidades de um
produto não está fazendo aquisição empresarial, mesmo que a matéria use o
substantivo "aquisição" (do ponto de vista do COMPRADOR do produto).

Reusa a infraestrutura que JÁ EXISTE: `detect_transaction` classifica o
objeto da transação e `ma_is_legitimate` já rejeita objetos não empresariais
(`aeronaves`, `equipamento`, `imovel`, `capex_ativo`). O defeito era que o
objeto saía `indefinido` nos dois casos reais. Correção: duas entradas de
vocabulário ancoradas no texto observado.

  G151 "aquisição dos três Embraer KC-390 Millennium" → modelo de aeronave
  G152 "…adicionar US$ 690 milhões à carteira da Embraer" → carteira de pedidos

São o MESMO fato econômico (Grécia compra KC-390) em duas construções.
Idioma: PT — os dois casos reais são PT. `order backlog` acompanha a entrada
`pedido_comercial` por simetria com o vocabulário EN já existente na tabela.
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


def pontua(title, company, summary=""):
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-30 17:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def obj(t):
    return sa.detect_transaction(t)["transaction_object"]


def legit(t):
    return sa.ma_is_legitimate(t)


_G151 = "Grécia investirá 600 milhões de euros na aquisição dos três Embraer KC-390 Millennium"
_G152 = "Aquisição da Grécia pode adicionar US$ 690 milhões à carteira da Embraer"

print("=" * 96)
print("BLOCO A — §14: os dois casos reais")
print("=" * 96)
check("ma" not in pontua(_G151, "Embraer"), "[G151] Embraer NÃO recebe `ma`")
check(obj(_G151) == "aeronaves", f"[G151b] objeto detectado = aeronaves (obtido {obj(_G151)!r})")
check("ma" not in pontua(_G152, "Embraer"), "[G152] Embraer NÃO recebe `ma`")
check(obj(_G152) == "pedido_comercial",
      f"[G152b] objeto detectado = pedido_comercial (obtido {obj(_G152)!r})")
check("Embraer" in rd.detect_companies({"title": _G151, "summary": ""}, cfg["watchlist"]),
      "[§14] Embraer CONTINUA detectada — a notícia comercial é dela")
check(legit(_G151)[0] is False and "objeto_nao_empresarial" in legit(_G151)[1],
      f"[§13] motivo auditável: {legit(_G151)[1]}")

print()
print("=" * 96)
print("BLOCO B — §15: FALSE sintéticos, só sobre o objeto observado")
print("=" * 96)
for _t in ("Embraer conclui venda de jatos para cliente europeu",
           "Companhia aérea anuncia aquisição de 20 aeronaves da Embraer",
           "Aquisição adiciona US$ 400 milhões à carteira da Embraer"):
    check("ma" not in pontua(_t, "Embraer"), f"[FALSE] {_t[:64]}")

print()
print("=" * 96)
print("BLOCO C — §16: TRUE controls — M&A corporativo sobrevive")
print("=" * 96)
# Asserções no NÍVEL DO GUARD — a camada que esta wave altera. Estes dois
# títulos não são classificados como `ma` pela taxonomia de produção nem
# ANTES nem DEPOIS (BEFORE == AFTER, verificado): `adquire`/`compra
# participação` não estão nas keywords. O que importa aqui é que o guard de
# objeto NÃO os rejeita — se um dia a taxonomia os classificar, o `ma`
# sobrevive.
check(legit("Embraer adquire empresa de tecnologia aeroespacial")[0] is True,
      "[TRUE 4] 'Embraer adquire empresa' → guard NÃO rejeita")
check(legit("Embraer compra participação na companhia X")[0] is True,
      "[TRUE 5] 'compra participação na companhia' → guard NÃO rejeita")
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[TRUE 1] aquisição de companhia → `ma` sobrevive")
check("ma" in pontua("Cade aprova fusão entre Marfrig e BRF que cria gigante do setor",
                     "BRF"), "[TRUE 2] fusão → `ma` sobrevive")
check(obj("Embraer adquire empresa de tecnologia aeroespacial") != "pedido_comercial",
      "[TRUE 3] M&A corporativo não é classificado como pedido comercial")
check(obj("Bradesco anuncia aquisição de carteira de crédito do BRB")
      != "pedido_comercial",
      "[§9] `carteira de crédito` NÃO colide com `carteira de pedidos`")

print()
print("=" * 96)
print("BLOCO D — §22/§23: C1 e C3 intocados")
print("=" * 96)
check("ma" in pontua("Âmbar Energia conclui a aquisição de 4 hidrelétricas da Cemig em MG",
                     "Cemig"),
      "[C1] seller/Cemig CONTINUA errado — fora do escopo desta wave")
check("ma" in pontua("Usiminas: Cade aprova aquisição de fatia da Nippon e Mitsubishi "
                     "pela Ternium", "Usiminas"),
      "[C3] shareholder/Usiminas CONTINUA errado — fora do escopo")

print()
print("=" * 96)
print("BLOCO E — regressões de waves anteriores")
print("=" * 96)
check("ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group"),
      "[R1] Cigna/Evernorth preservado")
check("follow_on" not in pontua(
        "Aegea aprova aumento de capital e Itaúsa (ITSA4) pode aportar até R$ 1,5 bilhão",
        "Itaúsa"), "[R2] B7b-2 preservado")
check("investigacao_regulatoria" not in pontua(
        "CVM abre processo administrativo contra ex-presidente do conselho da Vale, "
        "diz jornal", "Vale"), "[R3] B8 preservado")
check(obj("Vale informa sobre Plano de Recuperação Judicial da Samarco") != "aeronaves",
      "[R4] objeto não vaza para famílias não transacionais")

print()
print("=" * 96)
print(f"RESULTADO WAVE C2: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
