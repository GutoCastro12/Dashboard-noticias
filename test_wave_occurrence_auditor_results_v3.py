#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_results_v3.py — a primeira medição que mede.

A V3 respondeu à pergunta que motivou três ondas: nenhum modelo tinha sido
avaliado sob um contrato correto. Agora foi, e o resultado é misto de um jeito
informativo.

G1 superou o baseline trivial pela primeira vez — 15/17 contra 14/17 — e não
colapsou na pertinência: usou duas classes e acertou um alvo minoritário. Ainda
assim REPROVA, por falso merge acima do teto e por falso-mergear a Hapvida uma
vez. Isso é um resultado útil: o modelo tem sinal, e o sinal não basta.

E há um colapso NOVO, no outro eixo: G1 respondeu `FOLLOW_UP` nas dezessete
respostas. Como a verdade humana tem 11 FOLLOW_UP, ele marca 11/17 sem decidir
nada. A suíte afirma isso, porque 11/17 lido isolado parece desempenho.

Nenhum dos dois modelos produziu `NO_CANDIDATE` na BRF, onde é a resposta certa.
Os dois preferiram ligar a algum candidato. É viés de ligação forçada
remanescente, e é o achado mais acionável desta rodada.
"""
from __future__ import annotations

import io
import json
from collections import Counter

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_auditor_pilot_v3 as p3

PASS = FAIL = 0
B = "out_auditor_pilot_v3"
LIN = [json.loads(l) for l in io.open(f"{B}/dev_results.jsonl", encoding="utf-8")]
MAN = json.load(io.open(f"{B}/execution_manifest.json", encoding="utf-8"))
REL = json.load(io.open(f"{B}/dev_report.json", encoding="utf-8"))
# ENTRADA HISTÓRICA, NÃO ACERVO VIVO.
#
# Estas asserções são sobre um experimento CONGELADO. O acervo humano vivo é
# cumulativo por desenho, e `manifesto_desenvolvimento()` o consome inteiro —
# então ler o acervo vivo aqui faria a suíte quebrar toda vez que uma nova
# verdade fosse adjudicada, sem que o experimento tivesse mudado.
#
# Medido: a população congelada é idêntica sob 7/17/1 e sob 10/21/4 — mesmos 17
# alvos, mesmos `article_ref`, mesma verdade de pertinência. Ler o snapshot não
# enfraquece asserção nenhuma; corrige a fonte.
#
# A verificação de que o snapshot reproduz o hash histórico está em
# `test_wave_occurrence_archival_freeze.py`.
import reliability_occurrence_archival_verifier as _av

D = _av.carregar_snapshot()


def saida(r):
    rr = r.get("raw_response")
    if isinstance(rr, str):
        try:
            rr = json.loads(rr)
        except Exception:
            return {}
    return rr or {}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


n = 1
print("=" * 98)
print("§28/§51 IMUTABILIDADE E IDENTIDADE")
print("=" * 98)
_p = Counter((r["model"], r["target_id"]) for r in LIN)
check(len(LIN) == 34 and len(_p) == 34 and max(_p.values()) == 1,
      f"[{n}] 34 registros, 34 pares distintos, nenhum repetido"); n += 1
check(MAN["git_sha"] == "955b46597d96b4eecc18dc11f7e5db2536015f41",
      f"[{n}] executou o SHA publicado `955b465`"); n += 1
check(MAN["freeze_hashes"] == dict(v3.HASHES_V3)
      and MAN["freeze_version"] == v3.FREEZE_VERSION,
      f"[{n}] amarrado aos nove hashes da V3"); n += 1
check(MAN["freeze_hashes"] != dict(v1.HASHES_V1)
      and MAN["freeze_hashes"] != dict(v2.HASHES_V2),
      f"[{n}] e distinguível de V1 e V2"); n += 1
check(MAN["config"]["retry"] == 0 and MAN["config"]["fallback"] == 0
      and MAN["attempted_calls"] == 34,
      f"[{n}] 34 tentadas, retry 0, fallback 0"); n += 1
check(MAN["dataset_role"] == "DEVELOPMENT"
      and MAN["production_authority"] == "NONE",
      f"[{n}] desenvolvimento, sem autoridade"); n += 1
check(set(r["model"] for r in LIN) == set(p3.MODELOS),
      f"[{n}] só os dois modelos declarados"); n += 1

print()
print("=" * 98)
print("§35 PELA PRIMEIRA VEZ UM MODELO SUPERA O BASELINE TRIVIAL")
print("=" * 98)
_g1 = REL["por_modelo"][p3.G1]
_g2 = REL["por_modelo"][p3.G2]
check(REL["strongest_trivial_correct"] == 14 and REL["required_to_beat"] == 15,
      f"[{n}] ALWAYS_CANDIDATE_1 = 14/17; exigido 15"); n += 1
check(_g1["linkage_correct"] == 15 and _g1["beats_trivial"] is True,
      f"[{n}] G1: {_g1['linkage_correct']}/17 — SUPERA o trivial, algo que "
      "nem V1 nem V2 conseguiram"); n += 1
check(_g2["linkage_correct"] == 11 and _g2["beats_trivial"] is False,
      f"[{n}] G2: {_g2['linkage_correct']}/17 — fica ABAIXO do trivial"); n += 1

print()
print("=" * 98)
print("§33 A PERTINÊNCIA NÃO COLAPSOU — MAS A NOVIDADE SIM")
print("=" * 98)
check(_g1["non_collapse"] is True and len(_g1["classes_preditas"]) >= 2,
      f"[{n}] G1 prediz {_g1['classes_preditas']} — na V2 eram 34/34 numa "
      "classe só"); n += 1
check(len(_g2["classes_preditas"]) >= 2,
      f"[{n}] G2 prediz {_g2['classes_preditas']}, incluindo `NO_CANDIDATE`"); n += 1
check(REL["linkage_collapse"] is False,
      f"[{n}] o relatório canônico registra que a pertinência não colapsou"); n += 1
_nov1 = REL["novelty_collapse_evidence"][p3.G1]
check(_nov1 == {"FOLLOW_UP": 17},
      f"[{n}] MAS G1 respondeu `FOLLOW_UP` nas 17 ({_nov1}) — a novidade "
      "colapsou nele"); n += 1
check(_g1["novelty_correct"] == 11,
      f"[{n}] e marca {_g1['novelty_correct']}/17 sem decidir nada, porque a "
      "verdade tem 11 FOLLOW_UP — 11/17 lido isolado pareceria desempenho"); n += 1
check(REL["novelty_collapse"] is True,
      f"[{n}] o relatório registra o colapso em vez de publicar só o 11/17"); n += 1

print()
print("=" * 98)
print("§40 NENHUM DOS DOIS PRODUZIU `NO_CANDIDATE` ONDE ELE É A RESPOSTA CERTA")
print("=" * 98)
_brf = [d for d in json.load(io.open(f"{B}/dev_score.json", encoding="utf-8"))
        ["detalhe"] if d["company"] == "BRF"]
check(len(_brf) == 2 and all(d["erro"] == "FALSE_MERGE" for d in _brf),
      f"[{n}] BRF é o controle real de não-ligação, e os DOIS modelos ligaram "
      "a um candidato — viés de ligação forçada remanescente"); n += 1
check(all(d["linkage_aceitaveis"] == ["NO_CANDIDATE"] for d in _brf),
      f"[{n}] e a verdade humana ali é inequívoca"); n += 1

print()
print("=" * 98)
print("§36 HAPVIDA — O CONTROLE NEGATIVO DECISIVO")
print("=" * 98)
check(_g1["hapvida_false_merge"] == 1 and _g2["hapvida_false_merge"] == 1,
      f"[{n}] ambos falso-mergeiam a Hapvida uma vez (G1 "
      f"{_g1['hapvida_false_merge']}, G2 {_g2['hapvida_false_merge']}) — o "
      "portão exige zero"); n += 1
check(_g1["hapvida_false_merge"] < 2,
      f"[{n}] é MENOS que na V2, onde ambos erravam duas vezes — houve ganho, "
      "só não o bastante"); n += 1

print()
print("=" * 98)
print("§44 SANIDADE — LIMIARES CONGELADOS ANTES DAS SAÍDAS")
print("=" * 98)
for m in p3.MODELOS:
    ag = REL["por_modelo"][m]
    check(ag["development_sane"] is False,
          f"[{n}] {m}: REPROVA (parse={ag['parse_success']} "
          f"linkage={ag['linkage_correct']}/17 fm={ag['false_merge']} "
          f"hapvida={ag['hapvida_false_merge']} "
          f"minoria={ag['minority_correct']}/{ag['minority_n']})"); n += 1
check(_g1["beats_trivial"] and _g1["non_collapse"]
      and not _g1["development_sane"],
      f"[{n}] G1 passa em superar-o-trivial e em não-colapsar, e reprova por "
      "falso merge — o portão é composto, e é isso que o torna informativo"); n += 1
check(_g1["minority_correct"] >= 1 and _g2["minority_correct"] >= 1,
      f"[{n}] os dois acertam ao menos um alvo minoritário "
      f"(G1 {_g1['minority_correct']}/3, G2 {_g2['minority_correct']}/3)"); n += 1

print()
print("=" * 98)
print("A STRING \"null\" REAPARECEU EM G2, SEM O ESQUEMA PEDIR")
print("=" * 98)
_nulos = [r for r in LIN if saida(r).get("linked_candidate") == "null"]
check(len(_nulos) == 2 and all(r["model"] == p3.G2 for r in _nulos),
      f"[{n}] G2 emitiu a string \"null\" {len(_nulos)}×, num campo declarado "
      "como string simples e com o prompt mandando usar `NO_CANDIDATE`"); n += 1
check(v3.SCHEMA_SAIDA["properties"]["linked_candidate"] == {"type": "string"},
      f"[{n}] ou seja, não é mais defeito do contrato — é comportamento do "
      "modelo, e conta como falha de parse, não como acerto"); n += 1
check(_g2["parse_success"] < 1.0 and _g1["parse_success"] == 1.0,
      f"[{n}] G1 parseia 17/17; G2 {_g2['parse_success']}"); n += 1

print()
print("=" * 98)
print("§5 NENHUM SEGREDO NO ARTEFATO")
print("=" * 98)
_bruto = "".join(io.open(f"{B}/{f}", encoding="utf-8").read() for f in
                 ("dev_results.jsonl", "execution_manifest.json",
                  "dev_report.json", "dev_score.json"))
for termo in ("AIza", "Authorization", "Bearer ", "api_key"):
    check(termo not in _bruto, f"[{n}] `{termo}` ausente"); n += 1

print()
print("=" * 98)
print("§4 V1, V2 E V3 CONGELADAS SEGUEM INTACTAS")
print("=" * 98)
for nome, mod in (("V1", v1), ("V2", v2), ("V3", v3)):
    check(not mod.verificar_congelamento(D), f"[{n}] pins da {nome} exatos"); n += 1

print()
print("=" * 98)
print(f"RESULTADO ARTEFATO V3: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
