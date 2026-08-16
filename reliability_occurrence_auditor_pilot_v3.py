#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_pilot_v3.py — o executor do contrato ortogonal.

POR QUE NÃO É UM ADAPTADOR COMO O DA V2

A V2 pôde emprestar o arnês da V1 trocando o módulo de congelamento por baixo,
porque só o contrato de saída havia mudado. Aqui a avaliação mudou de FORMA: a
verdade de pertinência é um CONJUNTO de rótulos aceitáveis, pertinência e
novidade são medidas separadamente, e existem baselines triviais a superar. Um
shim que fingisse a assinatura antiga esconderia essa diferença justamente onde
ela importa.

O que continua emprestado é a única parte que precisa ser idêntica para as três
versões serem comparáveis em custo e comportamento de provider: a chamada em si
(`base._chamada_real`, que delega ao shadow semântico já existente), o ritmo, o
teto de 34, o disjuntor de infraestrutura e a gravação incremental. Nada de
retry, nada de fallback, nada de segundo stack de autenticação.

GRAVAÇÃO INCREMENTAL

Cada resposta vai para o disco assim que existe. Segurar 34 em memória até o
fim significa perder tudo se a execução morrer na vigésima — e o §21 proíbe
gastar cota de novo para reproduzir uma primeira resposta.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from pathlib import Path

import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_auditor_pilot as base

PILOT_VERSION = "occurrence.auditor.v3.dev.results.v1"
DATASET_ROLE = "DEVELOPMENT"
AUTORIDADE = "NONE"

G1 = base.G1
G2 = base.G2
MODELOS = base.MODELOS
MAX_CHAMADAS = 34
DISJUNTOR_FALHAS_CONSECUTIVAS = base.DISJUNTOR_FALHAS_CONSECUTIVAS
NAO_CHAMADO = base.NAO_CHAMADO
SCHEMA_SAIDA = v3.SCHEMA_SAIDA
PACING_S = float(os.environ.get("RISK_AUDITOR_PACING_S", "8"))


def config_efetiva() -> dict:
    c = dict(base.config_efetiva())
    c["parser_version"] = v3.OUTPUT_CONTRACT
    c["evaluator_version"] = v3.EVALUATOR_VERSION
    c["pacing_s"] = PACING_S
    return c


def alvos_congelados(dados, historico="risk_history.json",
                     config="config_risco.yaml") -> list:
    ex = v3.exemplos_congelados(dados, historico, config)
    out = []
    for a in v3.alvos_com_verdade(dados, historico, config):
        out.append({**a, "target_id": f"{a['event_id']}::{a['article_ref']}",
                    "prompt": v3.montar_prompt(a["exemplos_permitidos"],
                                               a["pacote"], ex)})
    return sorted(out, key=lambda x: x["target_id"])


def _chamada_real(modelo: str, texto: str) -> dict:
    """Delegação explícita: a mesma integração das V1/V2, para que uma
    diferença de resultado não possa vir do transporte."""
    import google.generativeai as genai
    import semantic_v2_shadow_run as run
    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave:
        return {"estado": "AUTH_ERROR", "saida": None, "invocou_sdk": False,
                "erro": {"classe": "AUTH_ERROR", "interrompe": True,
                         "mensagem": "GEMINI_API_KEY ausente"}}
    genai.configure(api_key=chave)
    return run._uma_chamada(genai, modelo,
                            {"prompt": texto, "schema": SCHEMA_SAIDA})


def executar(dados, *, chamada=None, saida_jsonl: str,
             historico="risk_history.json", config="config_risco.yaml",
             teto: int = MAX_CHAMADAS) -> dict:
    div = v3.verificar_congelamento(dados, historico, config)
    if div:
        raise SystemExit(f"CONGELAMENTO_V3_VIOLADO: {div}")
    chamada = chamada or _chamada_real
    alvos = alvos_congelados(dados, historico, config)
    p = Path(saida_jsonl)
    p.parent.mkdir(parents=True, exist_ok=True)
    consec = {m: 0 for m in MODELOS}
    parado = {m: None for m in MODELOS}
    tel = {"pilot_version": PILOT_VERSION, "dataset_role": DATASET_ROLE,
           "production_authority": AUTORIDADE,
           "freeze_version": v3.FREEZE_VERSION,
           "freeze_hashes": dict(v3.HASHES_V3),
           "operating_mode": v3.MODO_DE_OPERACAO,
           "git_sha": os.environ.get("GITHUB_SHA", ""),
           "planned_calls": len(alvos) * len(MODELOS),
           "attempted_calls": 0, "config": config_efetiva(),
           "por_modelo": {m: {"attempted": 0, "raw_ok": 0, "model_failure": 0,
                              "parse_failure": 0, "skipped_breaker": 0,
                              "breaker_tripped": None} for m in MODELOS}}
    seq = 0
    with io.open(p, "w", encoding="utf-8") as fh:
        for alvo in alvos:
            for m in MODELOS:
                if tel["attempted_calls"] >= teto:
                    break
                pm = tel["por_modelo"][m]
                if parado[m]:
                    pm["skipped_breaker"] += 1
                    seq += 1
                    fh.write(json.dumps({
                        "seq": seq, "model": m, "target_id": alvo["target_id"],
                        "company": alvo["company"],
                        "article_ref": alvo["article_ref"],
                        "estado": NAO_CHAMADO, "raw_response": None,
                        "prompt_hash": v3.HASHES_V3["prompt_hash"],
                        "freeze_manifest_hash":
                            v3.HASHES_V3["freeze_manifest_hash"]},
                        ensure_ascii=False) + "\n")
                    fh.flush()
                    continue
                if tel["attempted_calls"]:
                    time.sleep(PACING_S)
                t0 = time.time()
                res = chamada(m, base._texto_do_prompt(alvo["prompt"]))
                dur = round(time.time() - t0, 2)
                seq += 1
                tel["attempted_calls"] += 1
                pm["attempted"] += 1
                estado = res.get("estado")
                bruto = res.get("saida")
                if estado != "OK":
                    pm["model_failure"] += 1
                    consec[m] += 1
                    if consec[m] >= DISJUNTOR_FALHAS_CONSECUTIVAS:
                        parado[m] = seq
                        pm["breaker_tripped"] = seq
                else:
                    consec[m] = 0
                    pm["raw_ok"] += 1
                rot = [c["candidate_label"] for c in
                       alvo["pacote"]["prompt_payload"]["candidate_occurrences"]]
                probs = (v3.validar_saida(bruto, rot) if estado == "OK"
                         else ["SEM_RESPOSTA"])
                if estado == "OK" and probs:
                    pm["parse_failure"] += 1
                fh.write(json.dumps({
                    "seq": seq, "model": m, "modelo_real": res.get("modelo"),
                    "target_id": alvo["target_id"], "company": alvo["company"],
                    "article_ref": alvo["article_ref"],
                    "event_id": alvo["event_id"], "estado": estado,
                    "raw_response": bruto, "parse_problems": probs or None,
                    "provider_error": (res.get("erro") or {}).get("classe"),
                    "latencia_s": dur, "uso": res.get("uso"),
                    "finish": res.get("finish"),
                    "prompt_hash": v3.HASHES_V3["prompt_hash"],
                    "freeze_manifest_hash":
                        v3.HASHES_V3["freeze_manifest_hash"]},
                    ensure_ascii=False) + "\n")
                fh.flush()
    return tel


def pontuar(dados, jsonl: str, historico="risk_history.json",
            config="config_risco.yaml") -> dict:
    alvos = {a["target_id"]: a for a in
             alvos_congelados(dados, historico, config)}
    bl = v3.baselines_triviais(dados, historico, config)
    linhas = [json.loads(l) for l in io.open(jsonl, encoding="utf-8")]
    por_modelo, detalhe = {}, []
    for r in linhas:
        a = alvos.get(r["target_id"])
        if a is None:
            continue
        bruto = r.get("raw_response")
        if isinstance(bruto, str):
            try:
                bruto = json.loads(bruto)
            except Exception:
                bruto = None
        res = v3.avaliar_v3(a, bruto if r.get("estado") == "OK" else None)
        res["model"] = r["model"]
        res["target_id"] = r["target_id"]
        detalhe.append(res)
        por_modelo.setdefault(r["model"], []).append(res)
    return {"baselines": {k: v for k, v in bl.items() if not k.startswith("_")},
            "strongest_trivial": bl["_strongest"],
            "strongest_trivial_correct": bl["_strongest_correct"],
            "required_to_beat": bl["_required_to_beat"],
            "minority_targets": bl["_minority_targets"],
            "por_modelo": {m: v3.agregar_v3(v, bl)
                           for m, v in por_modelo.items()},
            "detalhe": detalhe}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Benchmark de desenvolvimento do auditor — congelamento V3.")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--out", default="out_auditor_pilot_v3/dev_results.jsonl")
    p.add_argument("--manifest-out",
                   default="out_auditor_pilot_v3/execution_manifest.json")
    p.add_argument("--score-out", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    dados = json.load(io.open(a.shadow, encoding="utf-8"))

    def falso(m, t):
        return {"estado": "OK", "modelo": m, "uso": {}, "finish": "STOP",
                "saida": {"linked_candidate": "CANDIDATE_1",
                          "occurrence_novelty": "FOLLOW_UP",
                          "material_phase_assessment": "ANNOUNCEMENT",
                          "should_refresh_anchor_assessment": "UNKNOWN",
                          "confidence": "HIGH",
                          "evidence": [{"quote": "x",
                                        "origin": "TARGET_TITLE"}]}}

    global PACING_S
    if a.dry_run:
        PACING_S = 0.0
    tel = executar(dados, chamada=falso if a.dry_run else None,
                   saida_jsonl=a.out)
    Path(a.manifest_out).parent.mkdir(parents=True, exist_ok=True)
    io.open(a.manifest_out, "w", encoding="utf-8").write(
        json.dumps(tel, ensure_ascii=False, indent=1, sort_keys=True))
    if a.score_out:
        io.open(a.score_out, "w", encoding="utf-8").write(
            json.dumps(pontuar(dados, a.out), ensure_ascii=False, indent=1,
                       sort_keys=True))
    print(json.dumps({k: tel[k] for k in
                      ("planned_calls", "attempted_calls", "por_modelo")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
