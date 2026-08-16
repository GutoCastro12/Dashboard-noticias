#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_results.py — os primeiros resultados são imutáveis.

POR QUE ESTE ARQUIVO EXISTE

O resultado de um benchmark só vale se ninguém puder melhorá-lo depois. Um
segundo registro para o mesmo par modelo×alvo, ou um resultado desamarrado dos
hashes do congelamento, transformaria a medição em algo que não se pode
auditar.

Então a suíte afirma: um registro por par, nenhuma repetição, os hashes da V1
gravados em cada resposta, os modelos exatamente os declarados antes da
primeira saída, e `dataset_role = DEVELOPMENT` — porque estes 17 alvos foram
usados para desenhar o experimento e chamá-los de holdout seria mentira.
"""
from __future__ import annotations

import io
import json
from collections import Counter

import reliability_occurrence_auditor_freeze as fz
import reliability_occurrence_auditor_pilot as pl

PASS = FAIL = 0
BASE = "out_auditor_pilot_v1"
LIN = [json.loads(l) for l in io.open(f"{BASE}/dev_results.jsonl", encoding="utf-8")]
MAN = json.load(io.open(f"{BASE}/execution_manifest.json", encoding="utf-8"))
REL = json.load(io.open(f"{BASE}/dev_report.json", encoding="utf-8"))


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


print("=" * 98)
print("§57 IMUTABILIDADE E UNICIDADE")
print("=" * 98)
check(len(LIN) == 34, f"[1] 34 registros ({len(LIN)})")
_pares = Counter((r["model"], r["target_id"]) for r in LIN)
check(len(_pares) == 34, f"[2] 34 pares distintos modelo×alvo ({len(_pares)})")
check(max(_pares.values()) == 1,
      f"[3] nenhum par aparece duas vezes — sem segunda tentativa "
      f"(máx {max(_pares.values())})")
_seqs = [r["seq"] for r in LIN]
check(sorted(_seqs) == list(range(1, 35)),
      "[4] sequência contígua de 1 a 34, sem buraco nem repetição")
check(len({r["target_id"] for r in LIN}) == 17,
      f"[5] 17 alvos distintos ({len({r['target_id'] for r in LIN})})")
for m in pl.MODELOS:
    n = sum(1 for r in LIN if r["model"] == m)
    check(n == 17, f"[6] {m}: 17 registros ({n})")

print()
print("=" * 98)
print("§17-freeze OS RESULTADOS ESTÃO AMARRADOS À V1")
print("=" * 98)
_n = 8
check(MAN["freeze_version"] == fz.FREEZE_VERSION,
      f"[{_n}] manifesto declara `{fz.FREEZE_VERSION}`")
_n += 1
check(MAN["freeze_hashes"] == dict(fz.HASHES_V1),
      f"[{_n}] e amarra os oito hashes da V1 — resultado sem hash correspondente "
      "seria inválido")
_n += 1
check(all(r["freeze_manifest_hash"] == fz.HASHES_V1["freeze_manifest_hash"]
          for r in LIN),
      f"[{_n}] cada resposta carrega o hash do congelamento")
_n += 1
check(all(r["prompt_hash"] == fz.HASHES_V1["prompt_hash"] for r in LIN),
      f"[{_n}] e o do prompt")
_n += 1
check(set(r["model"] for r in LIN) == {pl.G1, pl.G2},
      f"[{_n}] §8 só os dois modelos declarados antes da primeira saída")
_n += 1
check(MAN["config"]["retry"] == 0 and MAN["config"]["fallback"] == 0,
      f"[{_n}] retry e fallback zero, registrados no manifesto")
_n += 1
check(MAN["attempted_calls"] <= pl.MAX_CHAMADAS,
      f"[{_n}] §16 tentativas dentro do teto de 34 ({MAN['attempted_calls']})")
_n += 1

print()
print("=" * 98)
print("§2/§19 O PAPEL DO DATASET ESTÁ DITO")
print("=" * 98)
check(MAN["dataset_role"] == "DEVELOPMENT"
      and MAN["production_authority"] == "NONE",
      f"[{_n}] manifesto: desenvolvimento, sem autoridade")
_n += 1
check(REL["is_prospective"] is False and REL["is_holdout"] is False,
      f"[{_n}] §2 o relatório NEGA ser prospectivo e holdout — estes 17 alvos "
      "desenharam o experimento, e chamá-los de holdout seria mentira")
_n += 1
check(REL["operating_mode"] == "POST_BUILD_OCCURRENCE_ANOMALY_AUDITOR",
      f"[{_n}] e declara o modo pós-build")
_n += 1

print()
print("=" * 98)
print("§14/§44 O VEREDITO REGISTRADO")
print("=" * 98)
for m in pl.MODELOS:
    s = REL["por_modelo"][m]["sanidade"]
    check(s["development_sane"] is False,
          f"[{_n}] {m}: reprovado na sanidade "
          f"(fm={s['false_merge']} hapvida={s['hapvida_false_merge']} "
          f"linkage={s['clean_linkage_accuracy']} parse={s['parse_success']})")
    _n += 1
check(all(REL["por_modelo"][m]["sanidade"]["hapvida_false_merge"] > 0
          for m in pl.MODELOS),
      f"[{_n}] §32 ambos falham no controle negativo da Hapvida — é ele que "
      "impede aprender que mesma empresa + mesma família = mesmo fato")
_n += 1
check(sum(REL["por_modelo"][m]["parse_failure_provider_null_string"]
          for m in pl.MODELOS) == 7,
      f"[{_n}] 7 das falhas de parse são a string \"null\" devolvida pelo "
      "provider, não contradição do modelo")
_n += 1
check(REL["por_modelo"][pl.G2]["parse_failure_genuine_contradiction"] == 2,
      f"[{_n}] e 2 são contradição genuína (candidato escolhido com "
      "NEW_OCCURRENCE)")
_n += 1
check(REL["freeze_hashes"] == dict(fz.HASHES_V1),
      f"[{_n}] §38 e nada disso alterou a V1 — o conserto do esquema, se "
      "houver, é V2")
_n += 1

print()
print("=" * 98)
print("§23 NENHUM SEGREDO NO ARTEFATO")
print("=" * 98)
_bruto = (io.open(f"{BASE}/dev_results.jsonl", encoding="utf-8").read()
          + io.open(f"{BASE}/execution_manifest.json", encoding="utf-8").read()
          + io.open(f"{BASE}/dev_report.json", encoding="utf-8").read())
for termo in ("AIza", "Authorization", "Bearer ", "api_key"):
    check(termo not in _bruto, f"[{_n}] `{termo}` ausente do artefato")
    _n += 1

print()
print("=" * 98)
print(f"RESULTADO ARTEFATO DO PILOTO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
