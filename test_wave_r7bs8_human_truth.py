#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7bs8_human_truth.py — 4I.2 R7b-S2/S8.

VERDADE HUMANA É DADO AUDITÁVEL, NÃO AJUSTE DE TESTE.

Esta wave registrou quatro decisões humanas. Este teste existe para provar que
elas ficaram registradas COM PROVENIÊNCIA e que nada mais foi tocado — em
particular, que o gold base da auditoria 4I continua intacto e que a correção
do veredito da Petrobras é um OVERRIDE declarado, não uma edição silenciosa do
label histórico.

O ponto que mais importa aqui é o [7]: sem a correção do loader, adjudicar um
POSITIVO para negativo passaria VAZIO. `forbidden_event_id` nasce "" nos casos
`keep`, e `"" not in pontuaveis` é sempre verdadeiro — o caso "passaria" sem
testar nada. O mecanismo só tinha implementado a direção negativo→positivo
porque era a única adjudicação que existia.

E o [15]: a verdade da B3 diz apenas que `troca_ceo` é FALSE. Nenhuma label
humana foi criada por inferência para `incidente_operacional`.
"""
from __future__ import annotations

import io
import json
import subprocess

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


GOLD = json.load(io.open("test_fixtures_4i/gold_set_4i.json", encoding="utf-8"))
ADJ = json.load(io.open("test_fixtures_4i/gold_adjudications_4i2.json",
                        encoding="utf-8"))
S8 = json.load(io.open("test_fixtures_reliability/occurrence_currentness_reviews.json",
                       encoding="utf-8"))
MA = json.load(io.open("test_fixtures_reliability/ma_transaction_reviews.json",
                       encoding="utf-8"))

print("=" * 98)
print("BLOCO A — gold override auditável, gold base intacto")
print("=" * 98)
_g183 = [c for c in GOLD["casos"] if c["id"] == "G183"]
check(len(_g183) == 1, "[1] o caso base G183 existe")
_b = _g183[0]
check(_b["assertion"] == "keep" and _b["audit_verdict"] == "CORRECT",
      f"[2] o gold BASE segue dizendo o que sempre disse "
      f"({_b['assertion']}/{_b['audit_verdict']}) — não foi reescrito")
_over = [a for a in ADJ["adjudicacoes"] if a.get("case_id") == "G183"]
check(len(_over) == 1, f"[3] há exatamente UM override para G183 ({len(_over)})")
_o = _over[0]
check(_o["old_verdict"] == "CORRECT" and _o["new_verdict"] == "WRONG_EVENT",
      f"[4] override CORRECT → WRONG_EVENT ({_o['old_verdict']}→{_o['new_verdict']})")
check(_o.get("reviewer_type") == "human" and _o.get("adjudicated_by"),
      "[5] com autoria humana declarada")
check(all(k in _o for k in ("reason", "evidence_summary", "impacto_no_gold",
                            "adjudicated_at")),
      "[6] e com motivo, evidência, impacto e data — auditável")

print()
print("=" * 98)
print("BLOCO B — o override NÃO passa vazio (a lacuna do loader)")
print("=" * 98)
check(_b["forbidden_event_id"] == "",
      "[7] no gold base o caso `keep` não tem forbidden_event_id...")
import test_gold_4i as G  # noqa: E402
_c = [x for x in G.casos if x["id"] == "G183"][0]
check(_c["assertion"] == "reclass",
      f"[8] ...e depois do override a assertion vira reclass ({_c['assertion']})")
check(_c["forbidden_event_id"] == "ma",
      f"[9] com forbidden_event_id preenchido ({_c['forbidden_event_id']!r}) — "
      f"sem isto a checagem seria '' not in pontuaveis, sempre verdadeira")
check(_c.get("audit_verdict_original") == "CORRECT",
      "[10] o veredito original fica registrado no caso, não some")
_r = G.resultado[_c["id"]]
check("ma" not in _r["pontuaveis"] and "aquisicao_capex" in _r["informativo"],
      f"[11] e o motor de fato move o evento para informativo "
      f"(pontuáveis={sorted(_r['pontuaveis'])}, informativo={sorted(_r['informativo'])})")

print()
print("=" * 98)
print("BLOCO C — verdades S8 registradas")
print("=" * 98)
_itens = {k: v for k, v in S8.items() if k != "_meta"}
# A fixture guarda verdade de OCORRÊNCIA/ATUALIDADE, que é a mesma pergunta
# para S8 e para o controle S3 do Grupo Security. Por isso a checagem é por
# ESTRATO, não por contagem total — assim ela não vira um número a bumpar toda
# vez que o conjunto cresce legitimamente.
_s8 = {k: v for k, v in _itens.items() if v.get("stratum") == "S8"}
_s3 = {k: v for k, v in _itens.items() if v.get("stratum") == "S3"}
check(len(_s8) == 3 and len(_s3) == 1,
      f"[12] três verdades S8 e uma S3 registradas ({len(_s8)}/{len(_s3)})")
check(all(v["reviewer_type"] == "human" for v in _itens.values()),
      "[13] todos com reviewer_type human")
check(all(v["human_scoreable"] is False for v in _itens.values()),
      "[14] e todos dizem: não pontua")
_ev = {v["company"]: v["event_id"] for v in _s8.values()}
check(_ev == {"YPF": "falencia", "Sabesp": "ma", "B3": "troca_ceo"},
      f"[15] sobre os eventos candidatos corretos ({_ev})")
_todos = json.dumps(S8, ensure_ascii=False)
_b3 = [v for v in _itens.values() if v["company"] == "B3"][0]
check(_b3.get("event_id") == "troca_ceo"
      and "incidente_operacional" not in json.dumps(
          {k: v for k, v in _b3.items() if k != "note"}, ensure_ascii=False),
      "[16] NENHUMA label humana foi atribuída a `incidente_operacional`")
check(all("provenance" in v and "note" in v for v in _itens.values()),
      "[17] cada verdade tem proveniência e nota humana")
check(all("deterministic_comparison" in v for v in _itens.values()),
      "[18] e a comparação com o determinístico está registrada")
_cls = {v["company"]: v["deterministic_comparison"]["classification"]
        for v in _s8.values()}
check(_cls == {"YPF": "correct output / fragile reason",
               "Sabesp": "conflict", "B3": "conflict"},
      f"[19] com a classificação honesta de cada um ({_cls})")

print()
print("=" * 98)
print("BLOCO C2 — controle S3 do Grupo Security (R7b-S3)")
print("=" * 98)
_gs = list(_s3.values())[0]
check(_gs["company"] == "Grupo Security" and _gs["event_id"] == "ma",
      f"[19b] a verdade S3 é do Grupo Security / `ma` ({_gs['company']})")
check(_gs.get("underlying_ma_transaction") is True
      and _gs.get("transaction_completed") is True,
      "[19c] a fusão VERDADEIRA não foi apagada — underlying e completed seguem True")
check(_gs.get("new_ma_occurrence_in_this_article") is False
      and _gs.get("scoreable_as_new_ma") is False,
      "[19d] o que é falso é a NOVA OCORRÊNCIA neste artigo, não a transação")
check(_gs.get("transaction_completed_at") == "2025-09-01"
      and _gs.get("combined_entity_brand") == "BICE",
      "[19e] data de conclusão e marca da entidade combinada registradas")
check("HUMAN PROVIDED CONTEXT" in (_gs.get("truth_source") or ""),
      "[19f] a origem da verdade é contexto humano, não fetch experimental")
check(_gs["deterministic_comparison"]["classification"]
      == "correct output / wrong reason",
      "[19g] e o diagnóstico do motor é honesto: acerta por acidente morfológico")

print()
print("=" * 98)
print("BLOCO D — populações separadas, nada contaminado")
print("=" * 98)
_live = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                          encoding="utf-8"))
_shadow = json.load(io.open("test_fixtures_reliability/shadow_reviews.json",
                            encoding="utf-8"))
check(len([k for k in _live if k != "_meta"]) == 31,
      "[20] live_reviews continua com 31 itens — nada foi mexido lá")
check(len([k for k in _shadow if k != "_meta"]) == 1,
      "[21] shadow_reviews continua com 1 item")
check(len([k for k in MA if k != "_meta"]) == 3,
      "[22] ma_transaction_reviews continua com os 3 controles S2")
check(set(k for k in S8 if k != "_meta").isdisjoint(k for k in _live if k != "_meta"),
      "[23] as chaves S8 não colidem com o inventário crítico")
check(len(ADJ["adjudicacoes"]) == 2,
      f"[24] o arquivo de adjudicações tem 2 entradas — a antiga preservada "
      f"({len(ADJ['adjudicacoes'])})")
check(any(a["company"] == "Grupo Nutresa" for a in ADJ["adjudicacoes"]),
      "[25] inclusive a adjudicação da Nutresa, intocada")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7b-S8 (verdade humana consolidada): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
