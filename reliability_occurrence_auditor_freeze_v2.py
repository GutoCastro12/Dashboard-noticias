#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_freeze_v2.py — V2 mínima: só como se diz "nenhum".

POR QUE EXISTE UMA V2

O piloto V1 mediu duas coisas ao mesmo tempo: capacidade semântica e dois
defeitos do próprio experimento. A V2 corrige só os defeitos, para que o
segundo número seja interpretável.

DEFEITO 1 — A STRING "null"

`selected_candidate` foi declarado `[string, null]`. A saída estruturada do
provider devolveu a STRING "null". O parser recusou, e recusou certo — mas a
intenção era recuperável, e isso sozinho causou 7 das 9 falhas de parse.

União anulável é o problema. A V2 troca por um sentinela explícito: o campo é
string simples, e "nenhum candidato" se escreve `NO_CANDIDATE`
(não `NONE`, que já é um valor do enum de FASE).

DEFEITO 2 — UM EXEMPLO QUE O PRÓPRIO PARSER RECUSA

O exemplo congelado da Hapvida ensinava `CANDIDATE_2` junto de
`NEW_OCCURRENCE` — exatamente a combinação que a regra de coerência proíbe.
G2 produziu esse padrão duas vezes; a explicação mais provável é que copiou o
exemplo.

Isso não é um defeito separado. É a mesma pergunta mal representada: quando o
artigo ABRIU a ocorrência e um irmão posterior está entre os candidatos, o
rótulo humano (`NEW_OCCURRENCE`) e a resposta de pertinência (ligar) não cabem
juntos. A V1 chamou isso de `novelty_inexpressivel` na AVALIAÇÃO, mas deixou
vazar para o EXEMPLO.

A V2 resolve com regra determinística: entre as pertinências da ocorrência,
o exemplo usa a primeira, em ordem fixa, cuja saída esperada PASSA no próprio
validador. Não é escolher pelo resultado — é recusar ensinar o que o contrato
recusa.

O QUE NÃO MUDA

Folds, alvos, conjunto de empresas dos exemplos, regras de exclusão, métricas,
limiares de sanidade, modo de operação e o manifesto de desenvolvimento. A V1
segue publicada e íntegra, com seus hashes fixados — esta é uma identidade
NOVA, não uma reedição.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import reliability_pilot_contract_v2 as cv2

FREEZE_VERSION = "occurrence.auditor.freeze.v2"
PROMPT_VERSION = "occurrence.auditor.prompt.v2"
OUTPUT_CONTRACT = "occurrence.auditor.output.v2"
EXAMPLE_SET_VERSION = "occurrence.auditor.examples.v2"
EVALUATOR_VERSION = "occurrence.auditor.eval.v2"

MODO_DE_OPERACAO = v1.MODO_DE_OPERACAO
# `NONE` colidiria com o valor `NONE` do enum de FASE, que já existe. Duas
# coisas diferentes com o mesmo nome no mesmo prompt é convite a erro — e o
# ponto desta V2 é justamente remover ambiguidade de representação.
SEM_CANDIDATO = "NO_CANDIDATE"

OUT_NOVELTY = cv2.OCCURRENCE_NOVELTY
OUT_PHASE = ot.MATERIAL_PHASE
OUT_ANCHOR = v1.OUT_ANCHOR
OUT_CONFIDENCE = v1.OUT_CONFIDENCE
OUT_EVIDENCE_ORIGIN = v1.OUT_EVIDENCE_ORIGIN
SEM_AVALIACAO = v1.SEM_AVALIACAO
ABSTENCAO = v1.ABSTENCAO
DEFAULT_CURATED_SET = v1.DEFAULT_CURATED_SET

PROMPT_V2 = v1.PROMPT_V1.replace(
    "selected_candidate: o rótulo do candidato escolhido, ou null.",
    'selected_candidate: o rótulo do candidato escolhido, ou "NO_CANDIDATE".',
).replace(
    "- occurrence_novelty = NEW_OCCURRENCE exige selected_candidate = null.",
    '- occurrence_novelty = NEW_OCCURRENCE exige selected_candidate = "NO_CANDIDATE".',
).replace(
    "- occurrence_novelty = UNDETERMINED exige selected_candidate = null e",
    '- occurrence_novelty = UNDETERMINED exige selected_candidate = "NO_CANDIDATE" e',
)

SCHEMA_SAIDA = {
    "type": "object",
    "properties": {
        # Sem união anulável: o provider devolvia a string "null" e o contrato
        # a recusava. Sentinela explícito remove a ambiguidade na origem.
        "selected_candidate": {"type": "string"},
        "occurrence_novelty": {"type": "string", "enum": list(OUT_NOVELTY)},
        "material_phase_assessment": {"type": "string", "enum": list(OUT_PHASE)},
        "should_refresh_anchor_assessment": {"type": "string",
                                             "enum": list(OUT_ANCHOR)},
        "confidence": {"type": "string", "enum": list(OUT_CONFIDENCE)},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"quote": {"type": "string"},
                           "origin": {"type": "string",
                                      "enum": list(OUT_EVIDENCE_ORIGIN)}},
            "required": ["quote", "origin"]}},
        "abstention_reason": {"type": "string"},
    },
    "required": ["selected_candidate", "occurrence_novelty",
                 "material_phase_assessment",
                 "should_refresh_anchor_assessment", "confidence"],
}


def _hash(obj) -> str:
    bruto = (obj if isinstance(obj, str)
             else json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def prompt_texto() -> str:
    return PROMPT_V2.format(
        novidade=" | ".join(OUT_NOVELTY), fase=" | ".join(OUT_PHASE),
        ancora=" | ".join(OUT_ANCHOR), confianca=" | ".join(OUT_CONFIDENCE),
        origem=" | ".join(OUT_EVIDENCE_ORIGIN))


def _sel(s):
    """Normaliza o sentinela para o `None` interno do avaliador."""
    v = s.get("selected_candidate")
    return None if v in (SEM_CANDIDATO, None) else v


def validar_saida(s: dict, rotulos_validos) -> list:
    probs = []
    if not isinstance(s, dict):
        return ["MALFORMED"]
    nov = s.get("occurrence_novelty")
    bruto = s.get("selected_candidate")
    sel = _sel(s)
    if nov not in OUT_NOVELTY:
        probs.append("NOVELTY_INVALIDA")
    if bruto is None:
        probs.append("SELECTED_AUSENTE")
    elif sel is not None and sel not in rotulos_validos:
        probs.append("CANDIDATO_DESCONHECIDO")
    if s.get("material_phase_assessment") not in OUT_PHASE:
        probs.append("FASE_INVALIDA")
    if str(s.get("should_refresh_anchor_assessment")) not in OUT_ANCHOR:
        probs.append("ANCORA_INVALIDA")
    if nov != "UNDETERMINED" and s.get("confidence") not in OUT_CONFIDENCE:
        probs.append("CONFIANCA_INVALIDA")
    if nov == "NEW_OCCURRENCE" and sel is not None:
        probs.append("CONTRADICAO_NOVA_COM_CANDIDATO")
    if nov == "FOLLOW_UP" and sel is None:
        probs.append("CONTRADICAO_SEGUIMENTO_SEM_CANDIDATO")
    if nov == "UNDETERMINED":
        if sel is not None:
            probs.append("CONTRADICAO_ABSTENCAO_COM_CANDIDATO")
        if not s.get("abstention_reason"):
            probs.append("ABSTENCAO_SEM_MOTIVO")
    else:
        ev = s.get("evidence") or []
        if not ev:
            probs.append("SEM_EVIDENCIA")
        for e in ev:
            if not isinstance(e, dict) or not e.get("quote"):
                probs.append("EVIDENCIA_MALFORMADA")
            elif e.get("origin") not in OUT_EVIDENCE_ORIGIN:
                probs.append("ORIGEM_DE_EVIDENCIA_INVALIDA")
    return sorted(set(probs))


def avaliar_v2(pacote, saida, dados_verdade, verdade_alvo):
    """Mesmas definições de métrica da V1. A única diferença é traduzir o
    sentinela antes de delegar — as regras de falso merge, falso split,
    candidato misto e novidade inexpressível são idênticas."""
    if isinstance(saida, dict):
        rot = [c["candidate_label"] for c in
               pacote["prompt_payload"]["candidate_occurrences"]]
        probs = validar_saida(saida, rot)
        if probs:
            r = v1.avaliar_v1(pacote, None, dados_verdade, verdade_alvo)
            r["parse_problems"] = probs
            r["evaluator_version"] = EVALUATOR_VERSION
            return r
        saida = dict(saida, selected_candidate=_sel(saida))
    r = v1.avaliar_v1(pacote, saida, dados_verdade, verdade_alvo)
    r["evaluator_version"] = EVALUATOR_VERSION
    return r


agregar = v1.agregar
folds = v1.folds
montar_prompt = v1.montar_prompt
manifesto_desenvolvimento = v1.manifesto_desenvolvimento
exemplos_permitidos = v1.exemplos_permitidos


def exemplos_congelados(dados, historico="risk_history.json",
                       config="config_risco.yaml") -> dict:
    """Mesmas três empresas da V1. A diferença é que o alvo de cada exemplo é
    escolhido por uma regra: a primeira pertinência, em ordem fixa, cuja saída
    esperada PASSA no validador.

    Na V1 o exemplo da Hapvida ensinava `CANDIDATE_2` com `NEW_OCCURRENCE` —
    a combinação que o contrato recusa. Ensinar o que o parser rejeita não é
    supervisão, é ruído."""
    occ = ot.ocorrencias(dados)
    ver = {m["article_ref"]: m["occurrence_truth_id"]
           for m in ot.memberships_ativas(dados)}
    PADRAO = {"Santander Brasil": "SAME_EVENT_ANALYST_FOLLOW_UP",
              "Hapvida": "DISCRIMINATE_BETWEEN_TWO_TRANSITIONS_SAME_COMPANY",
              "Smart Fit": "SAME_EVENT_MATERIAL_PHASE"}
    out = {}
    for emp in DEFAULT_CURATED_SET:
        cands = []
        for f in folds(dados, historico, config):
            if f["company"] != emp:
                continue
            for a in sorted(f["alvos_elegiveis"], key=lambda x: x["article_ref"]):
                cands.append((f, a))
        escolhido = None
        for f, a in cands:
            pac = a["pacote"]
            oid = a["verdade_humana"]["occurrence_truth_id"]
            rot = [c["candidate_label"] for c in
                   pac["prompt_payload"]["candidate_occurrences"]]
            alvo_rot = next((c["candidate_label"] for c in
                             pac["prompt_payload"]["candidate_occurrences"]
                             if any(ver.get(x["article_ref"]) == oid
                                    for x in c["representative_articles"])), None)
            m = a["verdade_humana"]
            saida = {
                "selected_candidate": alvo_rot or SEM_CANDIDATO,
                "occurrence_novelty": (m["occurrence_novelty"] if alvo_rot is None
                                       else ("FOLLOW_UP"
                                             if m["occurrence_novelty"] == "NEW_OCCURRENCE"
                                             else m["occurrence_novelty"])),
                "material_phase_assessment": m["material_phase"],
                "should_refresh_anchor_assessment":
                    "UNKNOWN" if m["should_refresh_anchor"] is None
                    else str(m["should_refresh_anchor"]).lower(),
                "confidence": "HIGH",
                "evidence": [{"quote": pac["prompt_payload"]["target_article"]["title"][:90],
                              "origin": "TARGET_TITLE"}],
            }
            if not validar_saida(saida, rot):
                escolhido = (f, a, saida, rot, oid)
                break
        if escolhido is None:
            continue
        f, a, saida, rot, oid = escolhido
        out[emp] = {"prompt_payload": a["pacote"]["prompt_payload"],
                    "expected_output": saida,
                    "evaluation_metadata": {"occurrence_truth_id": oid,
                                            "article_ref": a["article_ref"],
                                            "language": a["pacote"]["prompt_payload"]
                                            ["target_article"].get("language"),
                                            "event_id": f["event_id"],
                                            "semantic_pattern": PADRAO[emp]},
                    "candidate_labels": rot}
    return out


def manifesto(dados, git_sha="", criado_em="", historico="risk_history.json",
              config="config_risco.yaml") -> dict:
    ex = exemplos_congelados(dados, historico, config)
    dev = manifesto_desenvolvimento(dados)
    esq_saida = {"novelty": list(OUT_NOVELTY), "phase": list(OUT_PHASE),
                 "anchor": list(OUT_ANCHOR), "confidence": list(OUT_CONFIDENCE),
                 "evidence_origin": list(OUT_EVIDENCE_ORIGIN),
                 "no_candidate_sentinel": SEM_CANDIDATO,
                 "failure_states": [SEM_AVALIACAO, ABSTENCAO]}
    esq_entrada = {"contract": ai.INPUT_CONTRACT,
                   "forbidden_in_payload": list(ai.PROIBIDOS_NO_PAYLOAD),
                   "text_evidence": list(ai.TEXT_EVIDENCE)}
    m = {
        "freeze_version": FREEZE_VERSION,
        "supersedes": v1.FREEZE_VERSION,
        "change_scope": "no_candidate_representation_only",
        "operating_mode": MODO_DE_OPERACAO, "ingestion_time_auditor": False,
        "input_version": ai.INPUT_CONTRACT, "input_hash": _hash(esq_entrada),
        "output_version": OUTPUT_CONTRACT, "output_hash": _hash(esq_saida),
        "prompt_version": PROMPT_VERSION, "prompt_hash": _hash(prompt_texto()),
        "example_set_version": EXAMPLE_SET_VERSION,
        "example_set": list(DEFAULT_CURATED_SET),
        "example_set_hash": _hash({k: v["prompt_payload"] for k, v in ex.items()}),
        "example_outputs_hash": _hash({k: v["expected_output"] for k, v in ex.items()}),
        "dev_manifest_version": v1.DEV_MANIFEST_VERSION,
        "dev_manifest_hash": _hash(dev),
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": _hash(io.open(__file__, encoding="utf-8").read()
                                .split("def avaliar_v2")[1].split("agregar =")[0]),
        "git_sha": git_sha, "created_at_iso": criado_em,
        "prospective_start_at": None, "model_results": None,
    }
    m["freeze_manifest_hash"] = _hash({k: v for k, v in m.items()
                                       if k not in ("git_sha", "created_at_iso")})
    return m


HASHES_V2 = {
    "input_hash": "e9d33218fd811d13",
    "output_hash": "58c974d167b819c5",
    "prompt_hash": "f1baf77e20d54cc5",
    "example_set_hash": "6b05974f265ffaff",
    "example_outputs_hash": "7e5c7a0edc47f921",
    "dev_manifest_hash": "82cda660cdece064",
    "evaluator_hash": "b24da8c74ede6504",
    "freeze_manifest_hash": "62f037f52dbbcf65",
}


def verificar_congelamento(dados, historico="risk_history.json",
                           config="config_risco.yaml") -> list:
    if not HASHES_V2:
        return []
    m = manifesto(dados, historico=historico, config=config)
    return sorted((k, HASHES_V2[k], m.get(k))
                  for k in HASHES_V2 if m.get(k) != HASHES_V2[k])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Congelamento V2 (somente leitura).")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--json-out", default=None)
    a = p.parse_args(argv)
    dados = json.load(io.open(a.shadow, encoding="utf-8"))
    m = manifesto(dados)
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        print(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
