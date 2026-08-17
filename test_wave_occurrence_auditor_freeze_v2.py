#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_freeze_v2.py — a V2 só vale se consertar o que diz.

Uma V2 que apenas renomeia um campo e reivindica ter consertado o experimento
seria pior que nenhuma V2: gastaria cota e produziria um número que ninguém
poderia interpretar. Então a suíte não pergunta "o código roda". Pergunta três
coisas verificáveis:

1. A V1 continua intacta — os oito hashes publicados em `b1a9902` seguem batendo.
   Uma V2 que altera a V1 destrói o registro contra o qual ela seria comparada.

2. O defeito existia mesmo. A suíte reprova a V1 com as próprias regras da V1:
   o exemplo congelado da Hapvida é uma saída que `validar_saida` recusa.
   Afirmar um defeito sem exibi-lo é opinião.

3. A correção mede. As 9 respostas que falharam no parse do piloto são
   reexecutadas contra o parser V2. As 7 que eram a string "null" têm de passar;
   as 2 que eram contradição genuína têm de continuar falhando. Se as duas
   passassem, a V2 teria escondido erro semântico em vez de isolá-lo — que é o
   oposto do objetivo.

Os hashes são literais escritos à mão. `expected = calcular()` não prova nada:
passa por construção depois de qualquer edição.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot

PASS = FAIL = 0
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
PILOTO = [json.loads(l) for l in
          io.open("out_auditor_pilot_v1/dev_results.jsonl", encoding="utf-8")]


def saida(r):
    """A resposta do modelo está em `raw_response`, às vezes ainda como texto."""
    rr = r.get("raw_response")
    if isinstance(rr, str):
        try:
            rr = json.loads(rr)
        except Exception:
            return {}
    return rr or {}

ESPERADO_V2 = {
    "input_hash": "e9d33218fd811d13",
    "output_hash": "58c974d167b819c5",
    "prompt_hash": "f1baf77e20d54cc5",
    "example_set_hash": "6b05974f265ffaff",
    "example_outputs_hash": "7e5c7a0edc47f921",
    "dev_manifest_hash": "82cda660cdece064",
    "evaluator_hash": "b24da8c74ede6504",
    "freeze_manifest_hash": "62f037f52dbbcf65",
}


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
print("A V1 NÃO FOI TOCADA")
print("=" * 98)
check(not v1.verificar_congelamento(D),
      f"[{n}] os oito hashes da V1 seguem batendo — a V2 não reescreve o "
      "registro contra o qual será comparada"); n += 1
check(v1.FREEZE_VERSION == "occurrence.auditor.freeze.v1"
      and v2.FREEZE_VERSION == "occurrence.auditor.freeze.v2"
      and v2.manifesto(D)["supersedes"] == v1.FREEZE_VERSION,
      f"[{n}] identidade NOVA que declara quem supera, não reedição da V1"); n += 1

print()
print("=" * 98)
print("O DEFEITO EXISTIA — EXIBIDO, NÃO AFIRMADO")
print("=" * 98)
_ex1 = v1.exemplos_congelados(D)
_ruins = {k: v1.validar_saida(v["expected_output"], v["candidate_labels"])
          for k, v in _ex1.items()
          if v1.validar_saida(v["expected_output"], v["candidate_labels"])}
check(_ruins and "Hapvida" in _ruins
      and "CONTRADICAO_NOVA_COM_CANDIDATO" in _ruins["Hapvida"],
      f"[{n}] o exemplo da Hapvida na V1 é uma saída que o parser da V1 RECUSA "
      f"({_ruins.get('Hapvida')}) — ensinar o proibido"); n += 1
check(len(_ruins) == 1,
      f"[{n}] e era só ele: os outros dois exemplos da V1 eram válidos "
      f"({len(_ruins)} inválido)"); n += 1
_g2 = [r for r in PILOTO if r["model"] == "gemini-3.5-flash-lite"
       and r.get("parse_problems")
       and saida(r).get("occurrence_novelty") == "NEW_OCCURRENCE"
       and saida(r).get("selected_candidate") not in (None, "null")]
check(len(_g2) == 2,
      f"[{n}] e G2 reproduziu esse mesmo padrão 2 vezes ({len(_g2)}) — o "
      "exemplo é a explicação mais plausível"); n += 1

print()
print("=" * 98)
print("TODO EXEMPLO DA V2 PASSA NO PRÓPRIO VALIDADOR DA V2")
print("=" * 98)
_ex2 = v2.exemplos_congelados(D)
check(len(_ex2) == 3 and set(_ex2) == set(v2.DEFAULT_CURATED_SET),
      f"[{n}] as mesmas três empresas da V1 — o conjunto curado não mudou"); n += 1
for emp, v in sorted(_ex2.items()):
    p = v2.validar_saida(v["expected_output"], v["candidate_labels"])
    check(not p, f"[{n}] exemplo {emp}: aceito pelo parser V2 ({p or 'sem problema'})")
    n += 1
_hap = _ex2["Hapvida"]
check(len(_hap["candidate_labels"]) >= 2,
      f"[{n}] o exemplo da Hapvida ainda põe ≥2 candidatos lado a lado "
      f"({len(_hap['candidate_labels'])}) — a lição de discriminação sobrevive "
      "à correção, ela está na ESCOLHA, não no rótulo"); n += 1

print()
print("=" * 98)
print("O SENTINELA NÃO É AMBÍGUO")
print("=" * 98)
check(v2.SEM_CANDIDATO == "NO_CANDIDATE",
      f"[{n}] sentinela explícito `{v2.SEM_CANDIDATO}`"); n += 1
check(v2.SEM_CANDIDATO not in v2.OUT_PHASE,
      f"[{n}] e NÃO colide com o `NONE` do enum de fase — dois sentidos com o "
      "mesmo nome no mesmo prompt é o defeito que a V2 existe para remover"); n += 1
check(v2.SCHEMA_SAIDA["properties"]["selected_candidate"] == {"type": "string"},
      f"[{n}] o esquema não tem mais união anulável — foi ela que deixou o "
      f"provider devolver a string \"null\""); n += 1
check('"NO_CANDIDATE"' in v2.prompt_texto() and " null." not in v2.prompt_texto(),
      f"[{n}] e o prompt fala do sentinela, não de null"); n += 1

print()
print("=" * 98)
print("A CORREÇÃO MEDE: AS 9 FALHAS DO PILOTO REEXECUTADAS SOB A V2")
print("=" * 98)
_falhas = [r for r in PILOTO if r.get("parse_problems")]
check(len(_falhas) == 9, f"[{n}] 9 falhas de parse no piloto ({len(_falhas)})"); n += 1
_rec = _ainda = 0
for r in _falhas:
    p = dict(saida(r))
    if p.get("selected_candidate") == "null":       # a string, não o valor
        p["selected_candidate"] = v2.SEM_CANDIDATO  # única tradução aplicada
    rot = [f"CANDIDATE_{i}" for i in range(1, 12)]
    if v2.validar_saida(p, rot):
        _ainda += 1
    else:
        _rec += 1
check(_rec == 7,
      f"[{n}] 7 voltam a passar — eram a string \"null\", defeito de "
      f"representação, não do modelo ({_rec})"); n += 1
check(_ainda == 2,
      f"[{n}] e 2 CONTINUAM falhando ({_ainda}) — eram contradição genuína, e a "
      "V2 não pode escondê-las: esconder erro semântico é o oposto de isolá-lo"); n += 1
_g1f = [r for r in _falhas if r["model"] == "gemini-3.1-flash-lite"]
check(all(saida(r).get("selected_candidate") == "null" for r in _g1f),
      f"[{n}] as 3 falhas de G1 eram TODAS a string \"null\" — sob a V2, G1 "
      "parsearia 17/17"); n += 1

print()
print("=" * 98)
print("O RESTO DO EXPERIMENTO NÃO MUDOU")
print("=" * 98)
_f = v2.folds(D)
_alvos = [(f, a) for f in _f for a in f["alvos_elegiveis"]]
check(len(_alvos) == 17, f"[{n}] os mesmos 17 alvos ({len(_alvos)})"); n += 1
_ids = set(ot.ocorrencias(D))
_vaz = _same = 0
for f, a in _alvos:
    p = v2.montar_prompt(f["exemplos_permitidos"], a["pacote"], _ex2)
    if ai.vazamentos({"prompt_payload": p["target"]}):
        _vaz += 1
    if any(i in json.dumps(p["target"], ensure_ascii=False) for i in _ids):
        _vaz += 1
    if f["company"] in json.dumps(p["examples"], ensure_ascii=False):
        _same += 1
check(_vaz == 0, f"[{n}] vazamento de verdade humana para o alvo: zero ({_vaz})"); n += 1
check(_same == 0,
      f"[{n}] nenhum alvo vê exemplo da própria empresa ({_same})"); n += 1
check(v2.MODO_DE_OPERACAO == v1.MODO_DE_OPERACAO
      and v2.manifesto(D)["ingestion_time_auditor"] is False,
      f"[{n}] mesmo modo pós-build, ainda não é auditor de ingestão"); n += 1
check(list(v2.OUT_NOVELTY) == list(v1.OUT_NOVELTY),
      f"[{n}] enum de novidade idêntico — o Contract V2 segue congelado"); n += 1
check(v2.agregar is v1.agregar,
      f"[{n}] a agregação de métricas é literalmente a mesma função"); n += 1
check(v2.manifesto(D)["change_scope"] == "no_candidate_representation_only",
      f"[{n}] e o manifesto declara o escopo da mudança"); n += 1

print()
print("=" * 98)
print("HASHES FIXADOS À MÃO, E A FIXAÇÃO DETECTA MUTAÇÃO")
print("=" * 98)
check(dict(v2.HASHES_V2) == ESPERADO_V2,
      f"[{n}] os 8 hashes batem com literais escritos nesta suíte, não com "
      "`esperado = calcular()`"); n += 1
check(not v2.verificar_congelamento(D),
      f"[{n}] e o módulo concorda consigo mesmo"); n += 1
_orig = dict(v2.HASHES_V2)
try:
    v2.HASHES_V2["prompt_hash"] = "0" * 16
    _div = v2.verificar_congelamento(D)
    check(any(k == "prompt_hash" for k, _, _ in _div),
          f"[{n}] mutando um hash, a verificação REPROVA — a fixação tem efeito")
    n += 1
finally:
    v2.HASHES_V2.clear()
    v2.HASHES_V2.update(_orig)
check(not v2.verificar_congelamento(D),
      f"[{n}] e volta a passar depois de restaurado"); n += 1
_mud = {k for k in ESPERADO_V2 if ESPERADO_V2[k] != v1.HASHES_V1[k]}
check(_mud == {"output_hash", "prompt_hash", "example_set_hash",
               "example_outputs_hash", "evaluator_hash", "freeze_manifest_hash"},
      f"[{n}] exatamente 6 hashes mudaram: saída, prompt, exemplos e avaliador"); n += 1
check(ESPERADO_V2["input_hash"] == v1.HASHES_V1["input_hash"]
      and ESPERADO_V2["dev_manifest_hash"] == v1.HASHES_V1["dev_manifest_hash"],
      f"[{n}] e entrada e manifesto de desenvolvimento seguem IGUAIS aos da V1 "
      "— prova de que a V2 não mexeu nos dados nem no recorte"); n += 1

print()
print("=" * 98)
print(f"RESULTADO CONGELAMENTO V2: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
