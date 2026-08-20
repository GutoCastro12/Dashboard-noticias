#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_results_v2.py — o resultado V2, e o que ele custou.

A V2 consertou o que prometia consertar: o parse de G1 foi a 100% e o de G2 a
94%. E, ao consertar, quebrou outra coisa — os modelos passaram a responder
`CANDIDATE_1` em 34 de 34 respostas, inclusive nos 18 casos em que havia mais de
um candidato para escolher. Um preditor constante.

A explicação mais provável é o próprio reparo dos exemplos: na V1, o exemplo da
Hapvida era o único que exibia `CANDIDATE_2` e o único que exibia
`NEW_OCCURRENCE`. Ao torná-lo coerente com o validador, os três exemplos
passaram a ter a MESMA forma — `CANDIDATE_1` + `FOLLOW_UP`. O few-shot deixou de
mostrar variação, e a saída deixou de variar.

Por isso esta suíte fixa a degenerescência como fato registrado, e não como nota
de rodapé. Uma acurácia de linkage de 82% obtida por um preditor constante, num
conjunto com proporção SAME:DISTINCT de 10:1, não é evidência de competência —
é evidência de que a métrica sozinha não distingue raciocínio de chute
enviesado. Sem esta asserção, um leitor futuro veria "linkage subiu" e
concluiria o contrário.
"""
from __future__ import annotations

import io
import json
from collections import Counter

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_archival_source as arq
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_pilot_v2 as p2

PASS = FAIL = 0
B = "out_auditor_pilot_v2"
LIN = [json.loads(l) for l in io.open(f"{B}/dev_results.jsonl", encoding="utf-8")]
MAN = json.load(io.open(f"{B}/execution_manifest.json", encoding="utf-8"))
REL = json.load(io.open(f"{B}/dev_report.json", encoding="utf-8"))
SCO = json.load(io.open(f"{B}/dev_score.json", encoding="utf-8"))
V1L = [json.loads(l) for l in
       io.open("out_auditor_pilot_v1/dev_results.jsonl", encoding="utf-8")]


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
print("§9 IMUTABILIDADE E UNICIDADE")
print("=" * 98)
check(len(LIN) == 34, f"[{n}] 34 registros ({len(LIN)})"); n += 1
_p = Counter((r["model"], r["target_id"]) for r in LIN)
check(len(_p) == 34 and max(_p.values()) == 1,
      f"[{n}] 34 pares modelo×alvo distintos, nenhum repetido — sem segunda "
      "tentativa"); n += 1
check(sorted(r["seq"] for r in LIN) == list(range(1, 35)),
      f"[{n}] sequência contígua 1..34"); n += 1
check(len({r["target_id"] for r in LIN}) == 17,
      f"[{n}] 17 alvos distintos"); n += 1

print()
print("=" * 98)
print("§7 O RESULTADO ESTÁ AMARRADO À V2 PUBLICADA — E A V1 SEGUE INTACTA")
print("=" * 98)
check(MAN["git_sha"] == "92493ee1de28979d31ee2ea6a0aa62977b098b90",
      f"[{n}] executou o SHA publicado `92493ee`"); n += 1
check(MAN["freeze_version"] == v2.FREEZE_VERSION
      and MAN["freeze_hashes"] == dict(v2.HASHES_V2),
      f"[{n}] manifesto amarra os oito hashes da V2"); n += 1
check(MAN["freeze_hashes"] != dict(v1.HASHES_V1),
      f"[{n}] e não são os da V1 — os dois experimentos são distinguíveis"); n += 1
check(all(r["freeze_manifest_hash"] == v2.HASHES_V2["freeze_manifest_hash"]
          and r["prompt_hash"] == v2.HASHES_V2["prompt_hash"] for r in LIN),
      f"[{n}] cada resposta carrega o congelamento que a produziu"); n += 1
check(MAN["dataset_role"] == "DEVELOPMENT"
      and MAN["production_authority"] == "NONE",
      f"[{n}] desenvolvimento, sem autoridade de produção"); n += 1
check(MAN["config"]["retry"] == 0 and MAN["config"]["fallback"] == 0
      and MAN["attempted_calls"] <= 34,
      f"[{n}] retry 0, fallback 0, ≤34 chamadas "
      f"({MAN['attempted_calls']})"); n += 1
check(set(r["model"] for r in LIN) == set(p2.MODELOS),
      f"[{n}] só os dois modelos declarados, sem substituição"); n += 1

print()
print("=" * 98)
print("O QUE A V2 CONSERTOU — MECÂNICO, E REAL")
print("=" * 98)
_v1f = Counter(r["model"] for r in V1L if r.get("parse_problems"))
_v2f = Counter(r["model"] for r in LIN if r.get("parse_problems"))
check(_v1f[p2.G1] == 3 and _v2f[p2.G1] == 0,
      f"[{n}] G1: falhas de parse 3 → 0 (parse 82,4% → 100%)"); n += 1
check(_v1f[p2.G2] == 6 and _v2f[p2.G2] == 1,
      f"[{n}] G2: falhas de parse 6 → 1 (parse 64,7% → 94,1%)"); n += 1
check(not any(saida(r).get("selected_candidate") == "null" for r in LIN),
      f"[{n}] a string \"null\" desapareceu — o sentinela resolveu a "
      "ambiguidade do esquema"); n += 1

print()
print("=" * 98)
print("O QUE A V2 QUEBROU — PREDITOR CONSTANTE")
print("=" * 98)
_sel = Counter(saida(r).get("selected_candidate") for r in LIN)
check(_sel["CANDIDATE_1"] == 34,
      f"[{n}] TODAS as 34 respostas escolheram `CANDIDATE_1` ({dict(_sel)}) — "
      "nenhuma variação"); n += 1
check(REL["degeneracy_evidence"]["on_multi_candidate_targets"] == "18/18",
      f"[{n}] e nos 18 casos com MAIS DE UM candidato, as 18 escolheram o "
      "primeiro — não é acerto fácil, é ausência de escolha"); n += 1
check(sum(1 for r in LIN
          if saida(r).get("selected_candidate") == v2.SEM_CANDIDATO) == 0,
      f"[{n}] o sentinela `NO_CANDIDATE` nunca foi usado, embora a V1 tenha "
      "dito \"sem candidato\" 7 vezes"); n += 1
_v1nov = Counter(saida(r).get("occurrence_novelty") for r in V1L)
_v2nov = Counter(saida(r).get("occurrence_novelty") for r in LIN)
check(_v1nov["NEW_OCCURRENCE"] == 9 and _v2nov["NEW_OCCURRENCE"] == 1,
      f"[{n}] `NEW_OCCURRENCE` caiu de 9 para 1 — a direção DISTINCT quase "
      "desapareceu da saída"); n += 1
# 4I.2 R7m: os exemplos são ARQUIVAIS — a fonte de artigos é o snapshot
# imutável, não o acervo vivo. Hoje esta checagem não quebraria de qualquer
# forma, mas deixar a dependência implícita é o defeito que já custou uma
# reversão de alinhamento de produção.
_ex = v2.exemplos_congelados(
    json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8")),
    historico=arq.HISTORICO)
_formas = {(e["expected_output"]["selected_candidate"],
            e["expected_output"]["occurrence_novelty"]) for e in _ex.values()}
check(len(_formas) == 1,
      f"[{n}] causa provável: os três exemplos V2 têm a MESMA forma {_formas} — "
      "o reparo tirou o único exemplo que mostrava CANDIDATE_2 e "
      "NEW_OCCURRENCE"); n += 1
check(REL["degenerate_constant_predictor"] is True,
      f"[{n}] e o relatório canônico registra isso, em vez de publicar só a "
      "acurácia que subiu"); n += 1

print()
print("=" * 98)
print("§25 SANIDADE — LIMIARES DA V1, NÃO ALTERADOS APÓS VER O RESULTADO")
print("=" * 98)
for m in p2.MODELOS:
    s = REL["por_modelo"][m]["sanidade"]
    check(s["development_sane"] is False,
          f"[{n}] {m}: REPROVADO (fm={s['false_merge']} "
          f"hapvida={s['hapvida_false_merge']} "
          f"linkage={s['clean_linkage_accuracy']} parse={s['parse_success']})")
    n += 1
check(all(REL["por_modelo"][m]["sanidade"]["hapvida_false_merge"] == 2
          for m in p2.MODELOS),
      f"[{n}] §16 ambos falso-mergeiam a Hapvida DUAS vezes — pior que a V1 "
      "(G1 2, G2 1). O controle negativo piorou"); n += 1
check(all(REL["por_modelo"][m]["sanidade"]["clean_linkage_accuracy"] >= 0.80
          for m in p2.MODELOS),
      f"[{n}] a acurácia de linkage passou de 80% em ambos — e isso NÃO é "
      "competência: um preditor constante pontua assim num conjunto 10:1"); n += 1

print()
print("=" * 98)
print("§13 DENOMINADOR DE NOVIDADE PRESERVADO")
print("=" * 98)
check(all(SCO["por_modelo"][m]["novelty_avaliaveis"] == 11 for m in p2.MODELOS),
      f"[{n}] 11 avaliáveis, como na V1 — denominador não foi trocado em "
      "silêncio"); n += 1

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
print(f"RESULTADO ARTEFATO V2: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
