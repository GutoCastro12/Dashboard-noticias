#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_pilot.py — o benchmark não pode se salvar sozinho.

O QUE ESTA SUÍTE PROTEGE

Um runner de benchmark tem uma tentação óbvia: repetir a chamada quando a
resposta vem ruim. Isso mede a melhor de duas, e a melhor de duas não é o que
roda em produção. Então a suíte prova que não existe caminho de retry, nem de
fallback de modelo, nem de conserto de saída.

Prova também que falha de provider e abstenção do modelo continuam separadas:
converter cota esgotada em `UNDETERMINED` inventaria uma resposta semântica que
ninguém deu, e contaminaria a métrica de abstenção com defeito de rede.

E prova que o disjuntor só dispara por falha de INFRAESTRUTURA. Um disjuntor que
reagisse a resposta semanticamente errada calaria o modelo justamente quando ele
está sendo medido.

O PORTÃO DE SANIDADE PRECISA SABER REPROVAR

Se um modelo que responde sempre "CANDIDATE_1" passasse, o portão não valeria
nada. A suíte roda essa estratégia e exige reprovação — por falso merge na
Hapvida, que é o controle negativo principal.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

os.environ["RISK_AUDITOR_PACING_S"] = "0"

import reliability_occurrence_auditor_freeze as fz
import reliability_occurrence_auditor_pilot as pl
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
ALVOS = pl.alvos_congelados(D)
SRC = io.open("reliability_occurrence_auditor_pilot.py", encoding="utf-8").read()
import re as _re
COD = "\n".join(l.split("#")[0] for l in
                _re.sub(r'"""(?:.|\n)*?"""', " ", SRC).splitlines())


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def tmp():
    return str(Path(tempfile.mkdtemp()) / "r.jsonl")


def ok(sel="CANDIDATE_1", nov="FOLLOW_UP"):
    def f(modelo, texto):
        return {"estado": "OK", "invocou_sdk": True, "latencia_s": 0.1,
                "uso": {"prompt_token_count": 100, "candidates_token_count": 20},
                "saida": {"selected_candidate": sel, "occurrence_novelty": nov,
                          "material_phase_assessment": "NONE",
                          "should_refresh_anchor_assessment": "UNKNOWN",
                          "confidence": "MEDIUM",
                          "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}}
    return f


print("=" * 98)
print("§8/§13 MODELOS E CONFIG FIXADOS ANTES DE QUALQUER SAÍDA")
print("=" * 98)
check(pl.G1 == "gemini-3.1-flash-lite", f"[1] G1 = {pl.G1}")
check(pl.G2 == "gemini-3.5-flash-lite", f"[2] G2 = {pl.G2}")
check(pl.MODELOS == (pl.G1, pl.G2) and len(pl.MODELOS) == 2,
      "[3] exatamente dois modelos, nenhum terceiro")
import semantic_v2_shadow as sh
check(set(pl.MODELOS) == set(sh.MODELOS),
      "[4] §12 os mesmos do shadow semântico — a comparação futura fala a "
      "mesma língua, e não há segundo stack de autenticação")
_cfg = pl.config_efetiva()
check(_cfg["retry"] == 0 and _cfg["fallback"] == 0,
      f"[5] retry e fallback declarados ZERO ({_cfg['retry']}/{_cfg['fallback']})")
check(_cfg["temperature"] == 0.0, "[6] temperatura 0")
check(_cfg["circuit_breaker_consecutive_infra_failures"] == 3,
      "[7] regra do disjuntor congelada: 3 falhas consecutivas de infraestrutura")
check(pl.MAX_CHAMADAS == 34, f"[8] teto duro de 34 chamadas ({pl.MAX_CHAMADAS})")
check(pl.DATASET_ROLE == "DEVELOPMENT" and pl.PRODUCTION_AUTHORITY == "NONE",
      "[9] papel do dataset e autoridade declarados no módulo")

print()
print("=" * 98)
print("§16/§15 TETO E ORDEM DETERMINÍSTICA")
print("=" * 98)
check(len(ALVOS) == 17, f"[10] 17 alvos congelados ({len(ALVOS)})")
check([a["target_id"] for a in ALVOS] == sorted(a["target_id"] for a in ALVOS),
      "[11] ordem determinística por identificador de alvo")
_p = tmp()
_tel = pl.executar(D, chamada=ok(), saida_jsonl=_p)
check(_tel["planned_calls"] == 34 and _tel["attempted_calls"] == 34,
      f"[12] 34 planejadas, 34 tentadas ({_tel['attempted_calls']})")
_linhas = [json.loads(l) for l in io.open(_p, encoding="utf-8")]
check(len(_linhas) == 34, f"[13] 34 registros gravados ({len(_linhas)})")
_seq = [r["model"] for r in _linhas[:4]]
check(_seq == [pl.G1, pl.G2, pl.G1, pl.G2],
      f"[14] §15 G1 antes de G2 em cada alvo — cobertura pareada se a execução "
      f"morrer no meio ({_seq})")
_pares = {(r["model"], r["target_id"]) for r in _linhas}
check(len(_pares) == 34, "[15] um registro por par modelo×alvo, sem duplicata")
try:
    pl.executar(D, chamada=ok(), saida_jsonl=tmp(), teto=5)
    check(False, "[16] o teto deveria interromper")
except SystemExit as e:
    check("TETO_DE_CHAMADAS" in str(e), f"[16] §16 o teto interrompe de verdade ({e})")

print()
print("=" * 98)
print("§12/§26 NÃO EXISTE RETRY, FALLBACK NEM CONSERTO")
print("=" * 98)
_chamadas = []


def conta(modelo, texto):
    _chamadas.append((modelo, texto[:20]))
    return {"estado": "OK", "invocou_sdk": True, "latencia_s": 0.1,
            "saida": {"selected_candidate": "NAO_EXISTE",
                      "occurrence_novelty": "FOLLOW_UP",
                      "material_phase_assessment": "NONE",
                      "should_refresh_anchor_assessment": "UNKNOWN",
                      "confidence": "HIGH",
                      "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}}


_p2 = tmp()
pl.executar(D, chamada=conta, saida_jsonl=_p2)
check(len(_chamadas) == 34,
      f"[17] saída inválida em TODAS as 34 e ainda assim 34 chamadas — "
      f"nenhuma segunda tentativa ({len(_chamadas)})")
_l2 = [json.loads(l) for l in io.open(_p2, encoding="utf-8")]
check(all(r["parse_problems"] for r in _l2),
      "[18] o parser congelado recusa o rótulo inexistente")
check(all("CANDIDATO_DESCONHECIDO" in r["parse_problems"] for r in _l2),
      "[19] com o motivo nomeado")
check(all(r["raw_response"]["selected_candidate"] == "NAO_EXISTE" for r in _l2),
      "[20] §26 e a resposta bruta é preservada COMO VEIO, sem conserto")
# `"retry": 0` e `"fallback": 0` são DECLARAÇÕES de ausência, e aparecem no
# manifesto de propósito. A checagem tem de olhar lógica, não a palavra.
_SEM_DECL = COD.replace('"retry": 0', "").replace('"fallback": 0', "")
for termo in ("retry", "tentar_de_novo", "for _ in range(2)", "backoff",
              "while tentativa", "except: continue"):
    check(termo not in _SEM_DECL.lower(),
          f"[21] nenhum caminho de `{termo}` no código")
check("model_fallbacks" not in COD and "fallback" not in COD.replace('"fallback": 0', ''),
      "[22] nenhum fallback de modelo")

print()
print("=" * 98)
print("§17/§25 DISJUNTOR — SÓ INFRAESTRUTURA, E POR MODELO")
print("=" * 98)
_n = 23
_estado = {"n": 0}


def falha_g1(modelo, texto):
    if modelo == pl.G1:
        _estado["n"] += 1
        return {"estado": "RATE_LIMITED", "saida": None, "invocou_sdk": True,
                "latencia_s": 0.1,
                "erro": {"classe": "RATE_LIMITED", "interrompe": False}}
    return ok()(modelo, texto)


_p3 = tmp()
_t3 = pl.executar(D, chamada=falha_g1, saida_jsonl=_p3)
check(_t3["por_modelo"][pl.G1]["breaker_tripped"] == "RATE_LIMITED",
      f"[{_n}] o disjuntor de G1 dispara após 3 falhas consecutivas")
_n += 1
check(_t3["por_modelo"][pl.G1]["attempted"] == 3,
      f"[{_n}] e G1 para em 3 tentativas ({_t3['por_modelo'][pl.G1]['attempted']})")
_n += 1
check(_t3["por_modelo"][pl.G2]["attempted"] == 17,
      f"[{_n}] §17 enquanto G2 continua as 17 — cota é por modelo, e o disjuntor "
      f"de um não cala o outro ({_t3['por_modelo'][pl.G2]['attempted']})")
_n += 1
_pulados = [json.loads(l) for l in io.open(_p3, encoding="utf-8")
            if json.loads(l)["estado"] == pl.NAO_CHAMADO]
check(len(_pulados) == 14,
      f"[{_n}] os 14 restantes de G1 viram NOT_CALLED_PROVIDER_CIRCUIT_BREAKER "
      f"({len(_pulados)})")
_n += 1
check(all(r["raw_response"] is None for r in _pulados),
      f"[{_n}] §25 sem resposta inventada para eles")
_n += 1
_sc3 = pl.pontuar(D, _p3)
_est = {d["estado"] for d in _sc3["detalhe"] if d["model"] == pl.G1}
check("MODEL_ABSTENTION" not in _est,
      f"[{_n}] §25 falha de provider NUNCA vira abstenção semântica ({sorted(_est)})")
_n += 1
_estado["n"] = 0


def erra_semantica(modelo, texto):
    return ok("CANDIDATE_1", "FOLLOW_UP")(modelo, texto)


_t4 = pl.executar(D, chamada=erra_semantica, saida_jsonl=tmp())
check(all(v["breaker_tripped"] is None for v in _t4["por_modelo"].values()),
      f"[{_n}] resposta semanticamente errada NÃO aciona o disjuntor — seria "
      "calar o modelo justamente quando ele está sendo medido")
_n += 1

print()
print("=" * 98)
print("§18 A PRIMEIRA RESPOSTA É IMUTÁVEL")
print("=" * 98)
_p5 = tmp()
pl.executar(D, chamada=ok("CANDIDATE_1", "FOLLOW_UP"), saida_jsonl=_p5)
_antes = io.open(_p5, encoding="utf-8").read()
pl.executar(D, chamada=ok(None, "NEW_OCCURRENCE"), saida_jsonl=_p5)
_depois = io.open(_p5, encoding="utf-8").read()
check(_depois.startswith(_antes),
      f"[{_n}] uma segunda execução ANEXA e nunca sobrescreve o já gravado")
_n += 1
check(len(_depois.splitlines()) == 68,
      f"[{_n}] os 34 primeiros registros continuam lá, intactos "
      f"({len(_depois.splitlines())} linhas)")
_n += 1
for campo in ("seq", "model", "target_id", "fold_id", "prompt_hash",
              "freeze_manifest_hash", "estado", "raw_response", "at_iso"):
    check(all(campo in json.loads(l) for l in io.open(_p5, encoding="utf-8")
              if json.loads(l)["estado"] != pl.NAO_CHAMADO),
          f"[{_n}] cada registro traz `{campo}`")
    _n += 1
check(all(json.loads(l)["freeze_manifest_hash"] == fz.HASHES_V1["freeze_manifest_hash"]
          for l in io.open(_p5, encoding="utf-8")
          if json.loads(l)["estado"] != pl.NAO_CHAMADO),
      f"[{_n}] §17-freeze e amarra o hash do congelamento a cada resposta")
_n += 1

print()
print("=" * 98)
print("§14 O PORTÃO DE SANIDADE PRECISA SABER REPROVAR")
print("=" * 98)
_p6 = tmp()
pl.executar(D, chamada=ok("CANDIDATE_1", "FOLLOW_UP"), saida_jsonl=_p6)
_sc6 = pl.pontuar(D, _p6)
_s6 = pl.sanidade(_sc6["por_modelo"][pl.G1], _sc6["detalhe"], pl.G1)
check(_s6["hapvida_false_merge"] > 0,
      f"[{_n}] responder sempre CANDIDATE_1 produz falso merge na Hapvida "
      f"({_s6['hapvida_false_merge']})")
_n += 1
check(_s6["development_sane"] is False,
      f"[{_n}] §32 e reprova na sanidade — o portão não vale nada se um modelo "
      "que sempre chuta passasse")
_n += 1
_lim = io.open("reliability_occurrence_auditor_pilot.py", encoding="utf-8").read()
check("<= 1" in _lim and "== 0" in _lim and "0.60" in _lim and "0.80" in _lim,
      f"[{_n}] §14 os quatro limiares estão no código, congelados antes de "
      "qualquer saída")
_n += 1

print()
print("=" * 98)
print("§19/§27/§28 ARTEFATO E MÉTRICAS")
print("=" * 98)
check(pl.PILOT_VERSION == "occurrence.auditor.dev.results.v1",
      f"[{_n}] artefato versionado ({pl.PILOT_VERSION})")
_n += 1
_tel6 = pl.executar(D, chamada=ok(), saida_jsonl=tmp())
check(_tel6["dataset_role"] == "DEVELOPMENT"
      and _tel6["production_authority"] == "NONE",
      f"[{_n}] §2 o manifesto declara desenvolvimento e ausência de autoridade")
_n += 1
check(_tel6["freeze_hashes"] == dict(fz.HASHES_V1),
      f"[{_n}] §17-freeze o manifesto amarra os oito hashes da V1")
_n += 1
_inexp = sum(1 for d in _sc6["detalhe"]
             if d["model"] == pl.G1 and not d["novelty_expressivel"])
check(_inexp == 6,
      f"[{_n}] §28 seis alvos com novidade inexpressível ({_inexp}) — eles "
      "contam para ligação e não contam para novidade")
_n += 1
_ag = _sc6["por_modelo"][pl.G1]
check(_ag["novelty_avaliaveis"] == 11,
      f"[{_n}] denominador de novidade = 11 ({_ag['novelty_avaliaveis']})")
_n += 1
check("false_merge" in _ag and "false_split" in _ag
      and "acuracia" not in _ag,
      f"[{_n}] falso merge e falso split contados separados, nunca somados")
_n += 1

print()
print("=" * 98)
print("§64/§21 SEM AUTORIDADE E SEM IMPORTAÇÃO DE PRODUÇÃO")
print("=" * 98)
check("build_evolution" not in COD and "assign_occurrence_clusters" not in COD,
      f"[{_n}] o runner não lê score nem agrupador")
_n += 1
check("criar_ocorrencia" not in COD and "adicionar_membership" not in COD
      and "gravar" not in COD,
      f"[{_n}] §62 e não escreve verdade humana")
_n += 1
_rd = io.open("risk_dashboard.py", encoding="utf-8").read()
check("occurrence_auditor_pilot" not in _rd,
      f"[{_n}] produção não importa o piloto")
_n += 1
check("groq" not in COD.lower(), f"[{_n}] §9 nenhuma menção a Groq")
_n += 1
for termo in ("billing", "paid", "tier", "api_key=", "genai.configure(api_key=chave)"):
    presente = termo in COD
    if termo == "genai.configure(api_key=chave)":
        check(presente, f"[{_n}] §24 a chave só é passada ao cliente, nunca impressa")
    else:
        check(not presente or termo == "api_key=",
              f"[{_n}] §10 nenhuma ação de billing/tier (`{termo}`)")
    _n += 1
check("print(chave" not in COD and "len(chave" not in COD
      and "chave[:" not in COD,
      f"[{_n}] §24 a chave não é impressa, medida nem fatiada")
_n += 1
_antes_truth = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json",
                                            encoding="utf-8")), sort_keys=True)
pl.executar(D, chamada=ok(), saida_jsonl=tmp())
_depois_truth = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json",
                                             encoding="utf-8")), sort_keys=True)
check(_antes_truth == _depois_truth,
      f"[{_n}] rodar o benchmark não altera o store de verdade")
_n += 1
check(len(ot.ocorrencias(D)) == 7 and len(ot.memberships_ativas(D)) == 17,
      f"[{_n}] verdades seguem 7/17")
_n += 1

print()
print("=" * 98)
print("§5 O RUNNER RECUSA RODAR COM CONGELAMENTO VIOLADO")
print("=" * 98)
_orig = fz.PROMPT_V1
try:
    fz.PROMPT_V1 = _orig + "\nmutação"
    try:
        pl.executar(D, chamada=ok(), saida_jsonl=tmp())
        check(False, f"[{_n}] deveria recusar com congelamento violado")
    except SystemExit as e:
        check("CONGELAMENTO_VIOLADO" in str(e),
              f"[{_n}] §5 recusa antes de qualquer chamada quando um componente "
              f"congelado muda")
finally:
    fz.PROMPT_V1 = _orig
_n += 1
check(fz.verificar_congelamento(D) == [],
      f"[{_n}] e o congelamento volta íntegro")
_n += 1

print()
print("=" * 98)
print(f"RESULTADO PILOTO DO AUDITOR (ARNÊS): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
