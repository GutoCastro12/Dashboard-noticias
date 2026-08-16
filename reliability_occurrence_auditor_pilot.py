#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_pilot.py — o primeiro benchmark real da V1.

O QUE ELE FAZ

Roda os 17 alvos congelados do LOOCV contra dois modelos, uma chamada cada,
e guarda a PRIMEIRA resposta bruta de cada par modelo×alvo. Depois pontua pelo
avaliador congelado.

E O QUE ELE NÃO FAZ, QUE É O PONTO

Zero retry. Zero fallback de modelo. Zero conserto de saída malformada. Se a
resposta vier com prosa em volta do JSON, com enum inválido ou contraditória, o
parser congelado decide e o registro fica `PARSE_FAILURE`.

Isso não é rigidez: uma segunda tentativa mede a melhor de duas, e a melhor de
duas não é o que roda em produção. Disciplina de saída estruturada é parte do
que está sendo medido.

FALHA DE PROVIDER NÃO É ABSTENÇÃO

Cota esgotada, timeout e modelo indisponível são `MODEL_FAILURE`. Converter isso
em `UNDETERMINED` inventaria uma resposta semântica que ninguém deu e
contaminaria a taxa de abstenção com defeito de infraestrutura.

O DISJUNTOR É POR MODELO, E SÓ CONTA FALHA DE INFRAESTRUTURA

Três falhas consecutivas de API param aquele modelo; o outro segue, porque cota
é por projeto e modelo. Resposta semanticamente errada NUNCA aciona o disjuntor
— seria calar o modelo justamente quando ele está sendo medido.

ESTE É DESENVOLVIMENTO, NÃO PROSPECTIVO

Os 17 alvos foram usados para desenhar arquitetura, prompt, exemplos e
avaliador. Servem para checar sanidade do congelamento, nunca para afirmar
generalização. `dataset_role = DEVELOPMENT` está no artefato para que ninguém
leia estes números como holdout.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from pathlib import Path

import reliability_occurrence_auditor_freeze as fz
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import semantic_v2_shadow as sh

PILOT_VERSION = "occurrence.auditor.dev.results.v1"
DATASET_ROLE = "DEVELOPMENT"
PRODUCTION_AUTHORITY = "NONE"

# §8 — fixados antes de qualquer saída. São os mesmos dois do shadow semântico,
# para que a comparação futura fale a mesma língua.
G1 = "gemini-3.1-flash-lite"
G2 = "gemini-3.5-flash-lite"
MODELOS = (G1, G2)

MAX_CHAMADAS = 34                 # 17 alvos × 2 modelos, teto duro
DISJUNTOR_FALHAS_CONSECUTIVAS = 3
PACING_S = float(os.environ.get("RISK_AUDITOR_PACING_S", "8"))
TIMEOUT_S = 90
OUTPUT_TOKEN_CAP = 1600
TEMPERATURA = 0.0

NAO_CHAMADO = "NOT_CALLED_PROVIDER_CIRCUIT_BREAKER"

# §13 — esquema de saída para o modo estruturado do provider. Os valores vêm
# dos enums congelados, nunca reescritos aqui.
SCHEMA_SAIDA = {
    "type": "object",
    "properties": {
        "selected_candidate": {"type": ["string", "null"]},
        "occurrence_novelty": {"type": "string", "enum": list(fz.OUT_NOVELTY)},
        "material_phase_assessment": {"type": "string", "enum": list(fz.OUT_PHASE)},
        "should_refresh_anchor_assessment": {"type": "string",
                                             "enum": list(fz.OUT_ANCHOR)},
        "confidence": {"type": "string", "enum": list(fz.OUT_CONFIDENCE)},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"quote": {"type": "string"},
                           "origin": {"type": "string",
                                      "enum": list(fz.OUT_EVIDENCE_ORIGIN)}},
            "required": ["quote", "origin"]}},
        "abstention_reason": {"type": ["string", "null"]},
    },
    "required": ["selected_candidate", "occurrence_novelty",
                 "material_phase_assessment",
                 "should_refresh_anchor_assessment", "confidence"],
}


def config_efetiva() -> dict:
    return {"models": {"G1": G1, "G2": G2}, "temperature": TEMPERATURA,
            "max_output_tokens": OUTPUT_TOKEN_CAP,
            "structured_output": "response_mime_type=application/json + response_schema",
            "timeout_s": TIMEOUT_S, "pacing_s": PACING_S,
            "retry": 0, "fallback": 0,
            "circuit_breaker_consecutive_infra_failures":
                DISJUNTOR_FALHAS_CONSECUTIVAS}


def alvos_congelados(dados: dict, historico="risk_history.json",
                     config="config_risco.yaml") -> list:
    """§15 — ordem determinística: alvos ordenados por (ocorrência, artigo).
    Fixada antes de qualquer resposta e nunca reordenada depois."""
    fs = fz.folds(dados, historico, config)
    ex = fz.exemplos_congelados(dados, historico, config)
    out = []
    for f in sorted(fs, key=lambda x: x["occurrence_truth_id"]):
        for a in sorted(f["alvos_elegiveis"], key=lambda x: x["article_ref"]):
            out.append({
                "target_id": f"{f['occurrence_truth_id']}::{a['article_ref']}",
                "fold_id": f["occurrence_truth_id"],
                "company": f["company"], "event_id": f["event_id"],
                "article_ref": a["article_ref"],
                "pacote": a["pacote"], "verdade_humana": a["verdade_humana"],
                "prompt": fz.montar_prompt(f["exemplos_permitidos"], a["pacote"], ex),
            })
    return out


def _texto_do_prompt(p: dict) -> str:
    """Serialização determinística do prompt para o provider. Instrução,
    exemplos e alvo — a ordem congelada."""
    partes = [p["instructions"], ""]
    for i, (ex, sa_) in enumerate(zip(p["examples"], p["example_outputs"]), 1):
        partes.append(f"EXEMPLO {i} — ENTRADA")
        partes.append(json.dumps(ex, ensure_ascii=False, sort_keys=True))
        partes.append(f"EXEMPLO {i} — SAÍDA")
        partes.append(json.dumps(sa_, ensure_ascii=False, sort_keys=True))
        partes.append("")
    partes.append("ALVO")
    partes.append(json.dumps(p["target"], ensure_ascii=False, sort_keys=True))
    return "\n".join(partes)


def _chamada_real(modelo: str, texto: str) -> dict:
    """Uma invocação. Reusa a integração já existente do shadow semântico —
    não constrói um segundo stack de autenticação."""
    import semantic_v2_shadow_run as run
    import google.generativeai as genai
    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave:
        return {"estado": "AUTH_ERROR", "saida": None, "invocou_sdk": False,
                "erro": {"classe": "AUTH_ERROR", "interrompe": True,
                         "mensagem": "GEMINI_API_KEY ausente"}}
    genai.configure(api_key=chave)
    return run._uma_chamada(genai, modelo,
                            {"prompt": texto, "schema": SCHEMA_SAIDA})


def executar(dados: dict, *, chamada=None, saida_jsonl: str,
             historico="risk_history.json", config="config_risco.yaml",
             teto: int = MAX_CHAMADAS) -> dict:
    """Roda o benchmark e grava cada resultado ASSIM QUE ELE EXISTE.

    Gravação incremental porque segurar 34 respostas em memória até o fim
    significa perder tudo se a execução morrer na vigésima."""
    div = fz.verificar_congelamento(dados, historico, config)
    if div:
        raise SystemExit(f"CONGELAMENTO_VIOLADO: {div}")
    chamada = chamada or _chamada_real
    alvos = alvos_congelados(dados, historico, config)
    p = Path(saida_jsonl)
    p.parent.mkdir(parents=True, exist_ok=True)
    consecutivas = {m: 0 for m in MODELOS}
    parado = {m: None for m in MODELOS}
    tel = {"pilot_version": PILOT_VERSION, "dataset_role": DATASET_ROLE,
           "production_authority": PRODUCTION_AUTHORITY,
           "freeze_version": fz.FREEZE_VERSION,
           "freeze_hashes": dict(fz.HASHES_V1),
           "config": config_efetiva(),
           "call_order": "target asc; G1 then G2",
           "planned_calls": len(alvos) * len(MODELOS),
           "attempted_calls": 0, "por_modelo": {
               m: {"attempted": 0, "raw_ok": 0, "model_failure": 0,
                   "parse_failure": 0, "skipped_breaker": 0,
                   "breaker_tripped": None} for m in MODELOS},
           "git_sha": os.environ.get("GITHUB_SHA", ""),
           "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    seq = 0
    with io.open(p, "a", encoding="utf-8") as fh:
        for alvo in alvos:
            texto = _texto_do_prompt(alvo["prompt"])
            for modelo in MODELOS:
                pm = tel["por_modelo"][modelo]
                if parado[modelo] is not None:
                    pm["skipped_breaker"] += 1
                    seq += 1
                    fh.write(json.dumps({
                        "seq": seq, "model": modelo,
                        "target_id": alvo["target_id"], "fold_id": alvo["fold_id"],
                        "estado": NAO_CHAMADO, "motivo": parado[modelo],
                        "raw_response": None}, ensure_ascii=False) + "\n")
                    fh.flush()
                    continue
                if tel["attempted_calls"] >= teto:
                    raise SystemExit(f"TETO_DE_CHAMADAS: {teto}")
                seq += 1
                tel["attempted_calls"] += 1
                pm["attempted"] += 1
                t0 = time.time()
                r = chamada(modelo, texto)
                lat = r.get("latencia_s", round(time.time() - t0, 2))
                estado = r.get("estado")
                bruto = r.get("saida")
                erro = r.get("erro") or {}
                infra = estado != "OK" and estado != "JSON_INVALIDO"
                if infra:
                    pm["model_failure"] += 1
                    consecutivas[modelo] += 1
                    if (erro.get("interrompe")
                            or consecutivas[modelo] >= DISJUNTOR_FALHAS_CONSECUTIVAS):
                        parado[modelo] = erro.get("classe") or estado
                        pm["breaker_tripped"] = parado[modelo]
                else:
                    consecutivas[modelo] = 0
                rot = [c["candidate_label"] for c in
                       alvo["pacote"]["prompt_payload"]["candidate_occurrences"]]
                probs = (fz.validar_saida(bruto, rot) if estado == "OK"
                         else ["SEM_RESPOSTA"])
                if estado == "OK" and probs:
                    pm["parse_failure"] += 1
                elif estado == "OK":
                    pm["raw_ok"] += 1
                elif estado == "JSON_INVALIDO":
                    pm["parse_failure"] += 1
                fh.write(json.dumps({
                    "seq": seq, "model": modelo, "target_id": alvo["target_id"],
                    "fold_id": alvo["fold_id"], "company": alvo["company"],
                    "event_id": alvo["event_id"], "article_ref": alvo["article_ref"],
                    "prompt_hash": fz.HASHES_V1["prompt_hash"],
                    "freeze_manifest_hash": fz.HASHES_V1["freeze_manifest_hash"],
                    "estado": estado, "raw_response": bruto,
                    "parse_problems": probs, "provider_error": erro or None,
                    "latencia_s": lat, "uso": r.get("uso") or {},
                    "finish": r.get("finish") or {},
                    "modelo_real": r.get("modelo_real", ""),
                    "at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }, ensure_ascii=False) + "\n")
                fh.flush()
                if PACING_S > 0 and parado[modelo] is None:
                    time.sleep(PACING_S)
    tel["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return tel


# ── pontuação pelo avaliador congelado (§55) ────────────────────────────────
def pontuar(dados: dict, jsonl: str, historico="risk_history.json",
            config="config_risco.yaml") -> dict:
    alvos = {a["target_id"]: a for a in alvos_congelados(dados, historico, config)}
    por_modelo = {m: [] for m in MODELOS}
    detalhe = []
    for linha in io.open(jsonl, encoding="utf-8"):
        r = json.loads(linha)
        a = alvos.get(r["target_id"])
        if a is None:
            continue
        if r["estado"] == NAO_CHAMADO:
            res = {"estado": NAO_CHAMADO, "linkage_correct": None,
                   "false_merge": False, "false_split": False,
                   "mixed_candidate_link": False, "novelty_correct": None,
                   "novelty_inexpressivel": False, "material_phase_correct": None,
                   "anchor_exact": None, "high_impact_error": False}
        else:
            saida = r["raw_response"] if r["estado"] == "OK" else None
            res = fz.avaliar_v1(a["pacote"], saida, dados, a["verdade_humana"])
        por_modelo[r["model"]].append(res)
        detalhe.append({"model": r["model"], "target_id": r["target_id"],
                        "company": r["company"] if "company" in r else a["company"],
                        "event_id": a["event_id"],
                        "n_candidatos": len(a["pacote"]["prompt_payload"]
                                            ["candidate_occurrences"]),
                        "novelty_expressivel": not res.get("novelty_inexpressivel"),
                        "selected": (r.get("raw_response") or {}).get("selected_candidate")
                        if r["estado"] == "OK" else None,
                        "novelty": (r.get("raw_response") or {}).get("occurrence_novelty")
                        if r["estado"] == "OK" else None,
                        "estado": res["estado"],
                        "linkage": res["linkage_correct"],
                        "erro": ("FALSE_MERGE" if res["false_merge"] else
                                 "FALSE_SPLIT" if res["false_split"] else
                                 "MIXED" if res["mixed_candidate_link"] else None)})
    return {"por_modelo": {m: fz.agregar(v) for m, v in por_modelo.items()},
            "detalhe": sorted(detalhe, key=lambda d: (d["target_id"], d["model"]))}


def sanidade(ag: dict, detalhe: list, modelo: str) -> dict:
    """§14 — limiares congelados ANTES de qualquer saída."""
    av = ag["n_avaliados"]
    hap_fm = sum(1 for d in detalhe if d["model"] == modelo
                 and d["company"] == "Hapvida" and d["erro"] == "FALSE_MERGE")
    tot = ag["n_total"] or 1
    parse_ok = (tot - ag["n_falhas_de_modelo"]) / tot
    linkage = (ag["linkage_correct"] / av) if av else 0.0
    return {"false_merge": ag["false_merge"], "hapvida_false_merge": hap_fm,
            "clean_linkage_accuracy": round(linkage, 3),
            "parse_success": round(parse_ok, 3),
            "development_sane": (ag["false_merge"] <= 1 and hap_fm == 0
                                 and linkage >= 0.60 and parse_ok >= 0.80)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Piloto de DESENVOLVIMENTO do auditor de ocorrência V1.")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--out", default="out_auditor_pilot/dev_results.jsonl")
    p.add_argument("--manifest-out", default="out_auditor_pilot/execution_manifest.json")
    p.add_argument("--score-out", default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="não chama provider: usa respostas sintéticas")
    a = p.parse_args(argv)
    dados = json.load(io.open(a.shadow, encoding="utf-8"))
    ch = None
    if a.dry_run:
        def ch(modelo, texto):
            return {"estado": "OK", "invocou_sdk": False, "latencia_s": 0.0,
                    "saida": {"selected_candidate": "CANDIDATE_1",
                              "occurrence_novelty": "FOLLOW_UP",
                              "material_phase_assessment": "NONE",
                              "should_refresh_anchor_assessment": "UNKNOWN",
                              "confidence": "LOW",
                              "evidence": [{"quote": "dry-run",
                                            "origin": "TARGET_TITLE"}]}}
    tel = executar(dados, chamada=ch, saida_jsonl=a.out)
    Path(a.manifest_out).parent.mkdir(parents=True, exist_ok=True)
    io.open(a.manifest_out, "w", encoding="utf-8").write(
        json.dumps(tel, ensure_ascii=False, indent=1, sort_keys=True))
    print(json.dumps({k: tel[k] for k in
                      ("planned_calls", "attempted_calls", "por_modelo")},
                     ensure_ascii=False, indent=1))
    if a.score_out:
        sc = pontuar(dados, a.out)
        io.open(a.score_out, "w", encoding="utf-8").write(
            json.dumps(sc, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"SCORE -> {a.score_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
