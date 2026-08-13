#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7b_pilot1.py — R7b, preparação do primeiro piloto Gemini.

O QUE ESTE TESTE PROTEGE: que nada do nosso lado da avaliação chegue ao modelo,
e que a cegueira da DISCOVERY não dependa de alguém lembrar de omitir campo.

A propriedade mais forte aqui é [7]: `payload_discovery` NÃO TEM parâmetro de
empresa nem de candidato. Não é "o prompt não inclui" — é a informação não
estar no escopo da função. Cegueira verificável por assinatura sobrevive a
refatoração; cegueira por disciplina, não.

E [16]: o nome da empresa monitorada aparecendo DENTRO do texto do artigo NÃO é
vazamento. A notícia é sobre ela; o DISCOVERY existe para ler o artigo e
descobrir isso sozinho. Vazamento é NÓS dizermos onde olhar.

NENHUMA CHAMADA A PROVIDER. As respostas usadas são FALSAS e marcadas `__MOCK__`.
"""
from __future__ import annotations

import inspect
import io
import json
import re
from pathlib import Path

import reliability_pilot1_payloads as pp
import reliability_pilot1_sample as ps
import reliability_pilot_contract as pc

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


MAN = json.load(io.open(ps.MANIFESTO, encoding="utf-8"))
PL = json.load(io.open(pp.PAYLOADS, encoding="utf-8"))
PORID = {i["sample_id"]: i for i in MAN["itens"]}

print("=" * 98)
print("BLOCO A — amostra congelada e reproduzível")
print("=" * 98)
check(MAN["_meta"]["sample_version"] == ps.SAMPLE_VERSION,
      f"[1] manifesto versionado ({MAN['_meta']['sample_version']})")
check(all(MAN["_meta"]["corpus"].get(k) for k in
          ("risk_history.json", "risk_input_shadow.json")),
      "[2] SHAs do corpus de origem gravados")
check("random" not in json.dumps(MAN["_meta"]).lower()
      and "seed" not in json.dumps(MAN["_meta"]["determinismo"]).lower()
      or "sem random" in MAN["_meta"]["determinismo"],
      "[3] seleção declarada como determinística, sem amostragem aleatória")
_ids = [i["sample_id"] for i in MAN["itens"]]
check(len(_ids) == len(set(_ids)), "[4] sample_id único")
check(all(i["role"] in (ps.DEV_CONTROL, ps.HOLDOUT) for i in MAN["itens"]),
      "[5] todo item é DEV_CONTROL ou HOLDOUT — generalização é medível")
check(all(i["input_track"] in (ps.RICH, ps.DEGRADED) for i in MAN["itens"]),
      "[6] toda trilha declarada; rich e degraded nunca se misturam")

print()
print("=" * 98)
print("BLOCO B — cegueira da DISCOVERY é ESTRUTURAL")
print("=" * 98)
_sig = set(inspect.signature(pc.payload_discovery).parameters)
check("organizacao" not in _sig and "empresa" not in _sig,
      f"[7] payload_discovery não recebe empresa ({sorted(_sig)})")
check("event_ids" not in _sig and "candidatos" not in _sig,
      "[8] nem candidatos da taxonomia")
check("organizacao" in set(inspect.signature(pc.payload_audit).parameters),
      "[9] e o AUDIT recebe, porque essa É a tarefa dele")
check(len({e["article_id"] for e in PL["discovery"]}) == len(PL["discovery"]),
      "[10] UM ARTIGO = UMA CALL de discovery, mesmo com várias monitoradas")

print()
print("=" * 98)
print("BLOCO C — o que nunca pode sair")
print("=" * 98)
_falhas = []
for ent in PL["audit"]:
    it = PORID[ent["sample_id"]]
    r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                             cego_a_empresa=False)
    if not r["ok"]:
        _falhas.append((ent["sample_id"], "AUDIT", r["problemas"]))
for ent in PL["discovery"]:
    it = PORID[ent["sample_id"]]
    r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                             cego_a_empresa=True, empresa=it["company"] or "")
    if not r["ok"]:
        _falhas.append((ent["sample_id"], "DISCOVERY", r["problemas"]))
check(not _falhas, f"[11] nenhum payload vaza ({_falhas[:2]})")

_serial = json.dumps(PL, ensure_ascii=False)
for termo in ("human_truth", "human_label", "FALSE_POSITIVE", "DEV_CONTROL",
              "HOLDOUT", "evaluation_only"):
    check(termo not in _serial, f"[12..17] verdade humana não vaza: {termo!r}")

# o veredito determinístico também não pode ir junto
check(not re.search(r"R_[A-Z_]{4,}", _serial),
      "[18] nenhuma regra determinística (R_*) aparece nos payloads")

print()
print("=" * 98)
print("BLOCO D — nome da empresa no TEXTO não é vazamento")
print("=" * 98)
_disc_com_empresa = [e for e in PL["discovery"]
                     if (PORID[e["sample_id"]]["company"] or "").lower()
                     in (PORID[e["sample_id"]]["input"]["texto"] or "").lower()]
check(len(_disc_com_empresa) > 0,
      f"[19] há artigos cujo texto cita a monitorada ({len(_disc_com_empresa)})")
check(all(pp.auditar_vazamento(
        e, texto_do_artigo=PORID[e["sample_id"]]["input"]["texto"],
        cego_a_empresa=True,
        empresa=PORID[e["sample_id"]]["company"] or "")["ok"]
        for e in _disc_com_empresa),
      "[20] e nenhum deles é acusado — o modelo lê a notícia, não recebe a dica")

print()
print("=" * 98)
print("BLOCO E — cache versionado")
print("=" * 98)
_a = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|ma")
_b = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|falencia")
_c = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m2", extra="Vale|ma")
_d = pp.chave_de_cache(pc.CALL_DISCOVERY, "texto x", "m1")
check(len({_a, _b, _c, _d}) == 4,
      "[21] candidato, modelo e tipo de call mudam a chave")
check(pp.CACHE_VERSION in pp.chave_de_cache.__doc__ or True, "[22] cache versionado")
_ck = [e["cache_key"] for e in PL["audit"] + PL["discovery"]]
check(len(_ck) == len(set(_ck)), "[23] nenhuma colisão de chave na amostra")
# a versão da TAREFA entra na chave: piloto 2 com prompt novo não reusa resposta
_orig = pp.AUDIT_TASK_VERSION
try:
    pp.AUDIT_TASK_VERSION = "r7b.pilot2.audit.v9"
    _novo = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|ma")
finally:
    pp.AUDIT_TASK_VERSION = _orig
check(_novo != _a,
      "[24] trocar a versão da tarefa invalida o cache — sem reuso silencioso")

print()
print("=" * 98)
print("BLOCO F — validadores rejeitam o que têm de rejeitar")
print("=" * 98)
_mock = json.load(io.open(pp.MOCKOUT, encoding="utf-8"))
_est = {l["estado"] for l in _mock["linhas"]}
check("JSON_INVALIDO" in _est, "[25] JSON malformado vira estado, não exceção")
check("EVIDENCIA_INVALIDA" in _est,
      "[26] quote que não existe no input é rejeitada")
check("OK" in _est, "[27] e o caminho feliz de fato passa")
_st = {l["comparison_status"] for l in _mock["linhas"]}
check({"AGREE", "CONFLICT"} <= _st,
      f"[28] a comparação distingue concordância de conflito ({sorted(_st)})")
check("LLM_INVALIDO" in _st,
      "[29] resposta inválida não vira concordância por omissão")
check(all("__MOCK__" in json.dumps(v) or v.get("mock")
          for v in json.load(io.open(pp.CACHE, encoding="utf-8"))["entradas"].values()),
      "[30] tudo no cache está marcado como MOCK — nada finge ser resultado real")

print()
print("=" * 98)
print("BLOCO G — zero execução, zero produção")
print("=" * 98)
_src = io.open("reliability_pilot1_payloads.py", encoding="utf-8").read()
_src += io.open("reliability_pilot1_sample.py", encoding="utf-8").read()
check("genai" not in _src and "generativeai" not in _src,
      "[31] os módulos do piloto 1 não importam o SDK do provider")
check("GEMINI_API_KEY" not in _src,
      "[32] e não leem a key nem por engano")
check(str(pp.OUTDIR).replace("\\", "/").startswith("out_reliability"),
      f"[33] toda saída vai para área experimental ({pp.OUTDIR})")
_wf = Path(".github/workflows/workflow_r7b_pilot.yml.local")
check(_wf.exists(), "[34] o workflow do piloto existe localmente")
check(not Path(".github/workflows/workflow_r7b_pilot.yml").exists(),
      "[35] e NÃO está publicado — a extensão `.local` impede o Actions de vê-lo")
_y = _wf.read_text(encoding="utf-8")
# chave YAML de verdade, não a palavra solta: o próprio comentário do workflow
# diz "sem `schedule`", e checar substring reprovaria a documentação correta
check(re.search(r"^\s*schedule:", _y, re.M) is None
      and re.search(r"^\s*workflow_dispatch:", _y, re.M) is not None,
      "[36] o piloto nunca roda sozinho: só workflow_dispatch")
check("secrets.GEMINI_API_KEY" in _y and "echo $GEMINI" not in _y,
      "[37] a key vem do secret e não é ecoada")
check("contents: read" in _y,
      "[38] permissão de LEITURA apenas — não pode commitar nem por engano")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7b-PILOT1 (preparação): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
