#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_freeze.py — o experimento congelado antes de medir.

POR QUE CONGELAR ANTES

Estamos prestes a medir um modelo. Se o prompt, os exemplos ou o esquema forem
ajustados depois de ver os erros e a versão continuar a mesma, o resultado deixa
de ser holdout e vira ajuste — sem que nada no arquivo denuncie isso. É a mesma
disciplina que o shadow do Contract V2 já usa, e pelo mesmo motivo.

Depois desta onda, mudar qualquer elemento congelado exige versão nova.

MODO DE OPERAÇÃO — E O QUE ISSO PROÍBE AFIRMAR

Este é um AUDITOR DE ANOMALIA PÓS-BUILD, não um classificador em tempo de
ingestão. A fonte de candidatos é o `ceo.dup.v1`, que roda depois do painel
montado. Portanto o pacote pode conter artigos posteriores ao alvo — irmãos
legítimos, já observáveis no momento da reconciliação.

Registrar isso importa porque o número que sair daqui NÃO mede desempenho em
tempo de ingestão. Um auditor que classifica na chegada não pode ver o futuro, e
seria desonesto apresentar um resultado de reconciliação como se medisse aquilo.
Auditor de ingestão, se houver, é V2.

O QUE É VAZAMENTO E O QUE NÃO É

Exemplo curado CONTÉM saída humana adjudicada — é essa a supervisão, e é
deliberada. O proibido é a verdade do ALVO, ou verdade da mesma ocorrência ou
da mesma empresa, que entregaria a resposta. Um teste que proibisse todo rótulo
humano no prompt inteiro estaria proibindo o few-shot de existir.

POR QUE NÃO EXISTE REPOSIÇÃO DE EXEMPLO

Quando o alvo é o Santander, o Santander sai do conjunto e ficam dois exemplos.
Não reponho com a Tupy "porque é parecida": essa escolha usaria a verdade de
desenvolvimento para montar o prompt, e enviesaria a medição de um jeito que
nenhum número depois denunciaria. Menos exemplo é aceitável; escolha informada
pela resposta, não.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json

import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import reliability_pilot_contract_v2 as v2

FREEZE_VERSION = "occurrence.auditor.freeze.v1"
PROMPT_VERSION = "occurrence.auditor.prompt.v1"
EXAMPLE_SET_VERSION = "occurrence.auditor.examples.v1"
DEV_MANIFEST_VERSION = "occurrence.auditor.devset.v1"
EVALUATOR_VERSION = "occurrence.auditor.eval.v1"

MODO_DE_OPERACAO = "POST_BUILD_OCCURRENCE_ANOMALY_AUDITOR"

# Hashes canônicos da V1, do artefato publicado em `b1a9902`.
#
# POR QUE ISTO PRECISOU EXISTIR
#
# O congelamento foi publicado sem que ninguém afirmasse os valores. O teste
# checava `len(hash) == 16` — confirmava que o hash EXISTE, nunca que ele É o
# publicado. Resultado: editei `avaliar_v1` depois de imprimir o relatório, a
# suíte seguiu verde, e o checkpoint registrou dois hashes obsoletos. Um
# congelamento cujos hashes não são afirmados não está congelado; ele pegaria a
# troca de um arquivo, mas não pegou a minha própria edição — que é justamente o
# caso contra o qual ele existe.
#
# Estes são LITERAIS. Escrevê-los como `esperado = calcular_agora()` provaria
# nada: passaria depois de qualquer alteração. A verificação só tem valor
# porque o número está fixo aqui e falha quando o conteúdo muda sem trocar de
# versão. Mudança legítima exige `freeze.v2`.
#
# Não houve deriva de artefato: cada componente entrou em UM commit e nenhum
# mudou depois. O que se corrige aqui é o registro, não a V1.
HASHES_V1 = {
    "input_hash": "e9d33218fd811d13",
    "output_hash": "6de8d0e452552de3",
    "prompt_hash": "bff1ae906e67963f",
    "example_set_hash": "83f8233d21881caf",
    "example_outputs_hash": "fe90648e7749282e",
    "dev_manifest_hash": "82cda660cdece064",
    "evaluator_hash": "6c6511f94306f7cc",
    "freeze_manifest_hash": "cfb16c04bddd7e5d",
}


def verificar_congelamento(dados: dict, historico="risk_history.json",
                           config="config_risco.yaml") -> list:
    """Divergências entre o artefato de hoje e a V1 canônica.

    Lista vazia = íntegro. Qualquer item significa que um componente congelado
    mudou sem trocar de versão, e nenhuma chamada de modelo deve acontecer:
    medir contra um experimento alterado não é medir."""
    m = manifesto(dados, historico=historico, config=config)
    return sorted((k, HASHES_V1[k], m.get(k))
                  for k in HASHES_V1 if m.get(k) != HASHES_V1[k])

# §8/§11/§12/§13 — enums fechados. Novidade vem do Contract V2 por importação.
OUT_NOVELTY = v2.OCCURRENCE_NOVELTY
OUT_PHASE = ot.MATERIAL_PHASE
OUT_ANCHOR = ("true", "false", "UNKNOWN")
OUT_CONFIDENCE = ("HIGH", "MEDIUM", "LOW")
OUT_EVIDENCE_ORIGIN = ("TARGET_TITLE", "TARGET_SNIPPET", "CANDIDATE_TITLE")

# §53 — falha de provider e abstenção do modelo são coisas diferentes. Tratar
# parse quebrado como "UNDETERMINED" inventaria uma resposta semântica que
# ninguém deu, e contaminaria a taxa de abstenção com defeito de infraestrutura.
SEM_AVALIACAO = "MODEL_FAILURE"
ABSTENCAO = "MODEL_ABSTENTION"

# §17 — o conjunto padrão cobre os três caminhos que a saída precisa percorrer.
DEFAULT_CURATED_SET = ("Santander Brasil", "Hapvida", "Smart Fit")

PROMPT_V1 = """\
Você audita IDENTIDADE DE OCORRÊNCIA ECONÔMICA.

A pergunta não é se o artigo menciona um tema. É se ele relata um FATO NOVO ou
se pertence a um fato que já está representado entre as ocorrências fornecidas.

REGRAS

1. Classifique novidade ECONÔMICA, não presença de palavra-chave.
2. A data de publicação não é a data do fato. Um artigo recente pode falar de
   um fato antigo.
3. Uma mesma ocorrência econômica pode ter artigos separados por muitos meses.
4. Aprovação regulatória, nomeação, fechamento e conclusão são FASES da mesma
   ocorrência, não ocorrências novas.
5. Análise, comentário e repercussão sobre um fato já conhecido não criam
   ocorrência nova.
6. Um fato genuinamente novo da mesma família continua sendo novo, mesmo na
   mesma empresa e a poucos dias de distância.
7. Escolha um dos candidatos fornecidos, ou nenhum.
8. Não force ligação quando a evidência não bastar. Abster-se é melhor que
   fundir dois fatos distintos.
9. Cite evidência literal do título ou do trecho fornecido.
10. Responda apenas no esquema estruturado pedido.
11. Não escreva raciocínio.

ESQUEMA DE SAÍDA

selected_candidate: o rótulo do candidato escolhido, ou null.
occurrence_novelty: {novidade}
material_phase_assessment: {fase}
should_refresh_anchor_assessment: {ancora}
confidence: {confianca}
evidence: lista de {{"quote": trecho literal, "origin": {origem}}}
abstention_reason: texto curto, apenas quando occurrence_novelty for UNDETERMINED.

COERÊNCIA OBRIGATÓRIA

- occurrence_novelty = NEW_OCCURRENCE exige selected_candidate = null.
- occurrence_novelty = FOLLOW_UP exige um selected_candidate.
  Se a evidência indicar acompanhamento mas a ocorrência anterior não estiver
  entre os candidatos, responda UNDETERMINED em vez de inventar a ligação.
- occurrence_novelty = UNDETERMINED exige selected_candidate = null e
  abstention_reason preenchido.
"""


def _hash(obj) -> str:
    bruto = (obj if isinstance(obj, str)
             else json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def prompt_texto() -> str:
    return PROMPT_V1.format(
        novidade=" | ".join(OUT_NOVELTY), fase=" | ".join(OUT_PHASE),
        ancora=" | ".join(OUT_ANCHOR), confianca=" | ".join(OUT_CONFIDENCE),
        origem=" | ".join(OUT_EVIDENCE_ORIGIN))


# ── manifesto de desenvolvimento (§42/§43) ──────────────────────────────────
def manifesto_desenvolvimento(dados: dict) -> dict:
    """Metadado de AVALIAÇÃO. Nunca entra em prompt."""
    occ = ot.ocorrencias(dados)
    saida = []
    for oid, o in sorted(occ.items()):
        mem = ot.membros_de(dados, oid)
        saida.append({
            "occurrence_truth_id": oid, "company": o["company"],
            "event_id": o["event_id"], "material_event_date": o["material_event_date"],
            "family_identity": o["family_identity"],
            "memberships": sorted(
                [{"article_ref": m["article_ref"],
                  "occurrence_novelty": m["occurrence_novelty"],
                  "material_phase": m["material_phase"],
                  "should_refresh_anchor": m["should_refresh_anchor"]}
                 for m in mem], key=lambda x: x["article_ref"]),
        })
    return {"manifest_version": DEV_MANIFEST_VERSION,
            "status": "DEVELOPMENT_CURATION_REGRESSION",
            "prospective": False, "occurrences": saida,
            "relations": [{"a": r["occurrence_a"], "b": r["occurrence_b"],
                           "relation": r["relation"]}
                          for r in ot.relacoes(dados)]}


# ── LOOCV (§21–§26) ─────────────────────────────────────────────────────────
def exemplos_permitidos(dados: dict, empresa_alvo: str,
                        occurrence_alvo: str) -> tuple:
    """§25 — regra fixa: parte do conjunto padrão, tira a mesma ocorrência,
    tira a mesma empresa, e NÃO repõe.

    Repor exigiria escolher um substituto, e escolher exigiria olhar o que o
    alvo é — o que enviesa a medição sem deixar rastro."""
    occ = ot.ocorrencias(dados)
    permitidos, excluidos = [], []
    for emp in DEFAULT_CURATED_SET:
        ids = [k for k, o in occ.items() if o["company"] == emp]
        # A distinção importa para auditoria: quando o alvo é a Hapvida B, a
        # Hapvida A sai por ser da MESMA EMPRESA, não por ser a mesma
        # ocorrência. Registrar o motivo errado esconderia que a proteção que
        # atuou ali foi a de empresa — a única que impede o modelo de acertar
        # pelo nome do emissor em vez de pela semântica.
        if occurrence_alvo in ids and emp == empresa_alvo:
            motivo = ("MESMA_OCORRENCIA" if len(ids) == 1
                      else "MESMA_OCORRENCIA_E_MESMA_EMPRESA")
            excluidos.append((emp, motivo))
        elif emp == empresa_alvo:
            excluidos.append((emp, "MESMA_EMPRESA"))
        else:
            permitidos.append(emp)
    return tuple(permitidos), tuple(excluidos)


def folds(dados: dict, historico: str = "risk_history.json",
          config: str = "config_risco.yaml") -> list:
    """Um fold por OCORRÊNCIA humana — nunca por artigo. Segurar só um artigo
    deixaria os irmãos da mesma ocorrência disponíveis como exemplo, e o
    modelo veria a resposta pela porta dos fundos."""
    occ = ot.ocorrencias(dados)
    out = []
    for oid, o in sorted(occ.items()):
        perm, excl = exemplos_permitidos(dados, o["company"], oid)
        alvos, inelegiveis = [], []
        for m in sorted(ot.membros_de(dados, oid), key=lambda x: x["article_ref"]):
            try:
                pac = ai.construir_pacote(o["company"], o["event_id"],
                                          m["article_ref"], historico, config)
            except ValueError as e:
                inelegiveis.append({"article_ref": m["article_ref"],
                                    "motivo": f"ARTIGO_AUSENTE:{e}"})
                continue
            if not pac["prompt_payload"]["candidate_occurrences"]:
                inelegiveis.append({"article_ref": m["article_ref"],
                                    "motivo": "SEM_CANDIDATO: nada a decidir"})
                continue
            alvos.append({"article_ref": m["article_ref"], "pacote": pac,
                          "verdade_humana": {
                              "occurrence_truth_id": oid,
                              "occurrence_novelty": m["occurrence_novelty"],
                              "material_phase": m["material_phase"],
                              "should_refresh_anchor": m["should_refresh_anchor"]}})
        out.append({"occurrence_truth_id": oid, "company": o["company"],
                    "event_id": o["event_id"],
                    "exemplos_permitidos": list(perm),
                    "exemplos_excluidos": [{"empresa": e, "motivo": r} for e, r in excl],
                    "alvos_elegiveis": alvos, "alvos_inelegiveis": inelegiveis})
    return out


def montar_prompt(fold_exemplos: list, pacote_alvo: dict,
                  exemplos_congelados: dict) -> dict:
    """§49 — ordem determinística: instrução, exemplos, alvo. Mesmo fold
    produz o mesmo prompt byte a byte."""
    return {
        "prompt_version": PROMPT_VERSION,
        "instructions": prompt_texto(),
        "examples": [exemplos_congelados[e]["prompt_payload"]
                     for e in fold_exemplos if e in exemplos_congelados],
        "example_outputs": [exemplos_congelados[e]["expected_output"]
                            for e in fold_exemplos if e in exemplos_congelados],
        "target": pacote_alvo["prompt_payload"],
    }


# ── validação de saída (§10/§52) ────────────────────────────────────────────
def validar_saida(s: dict, rotulos_validos) -> list:
    probs = []
    if not isinstance(s, dict):
        return ["MALFORMED"]
    nov, sel = s.get("occurrence_novelty"), s.get("selected_candidate")
    if nov not in OUT_NOVELTY:
        probs.append("NOVELTY_INVALIDA")
    if sel is not None and sel not in rotulos_validos:
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


# ── avaliador congelado (§32–§41) ───────────────────────────────────────────
def avaliar_v1(pacote: dict, saida, dados_verdade: dict, verdade_alvo: dict) -> dict:
    """Autoridade de métrica da V1. Supersede o `avaliar` do módulo de insumo,
    que era o auxiliar pré-congelamento: aqui as definições estão fixas e
    incluem o candidato MISTO, que aquele não distinguia."""
    pp = pacote["prompt_payload"]
    emp, fam = pp["company"], pp["event_id"]
    alvo_ref = pacote["evaluation_metadata"]["target_article_ref"]
    oid_alvo = verdade_alvo["occurrence_truth_id"]
    verdade = {m["article_ref"]: m["occurrence_truth_id"]
               for m in ot.memberships_ativas(dados_verdade)
               if m["company"] == emp and m["event_id"] == fam}
    rotulos = [c["candidate_label"] for c in pp["candidate_occurrences"]]
    r = {"evaluator_version": EVALUATOR_VERSION, "target_article_ref": alvo_ref,
         "human_occurrence": oid_alvo, "estado": None, "linkage_correct": None,
         "false_merge": False, "false_split": False, "mixed_candidate_link": False,
         "novelty_correct": None, "novelty_inexpressivel": False,
         "material_phase_correct": None,
         "anchor_exact": None, "high_impact_error": False}
    if saida is None:
        r["estado"] = SEM_AVALIACAO
        return r
    probs = validar_saida(saida, rotulos)
    if probs:
        r["estado"] = SEM_AVALIACAO
        r["parse_problems"] = probs
        return r
    nov, sel = saida["occurrence_novelty"], saida["selected_candidate"]
    if nov == "UNDETERMINED":
        r["estado"] = ABSTENCAO
        return r
    r["estado"] = "ASSESSED"
    por_rotulo_pre = {c["candidate_label"]:
                      {verdade.get(a["article_ref"])
                       for a in c["representative_articles"]} - {None}
                      for c in pp["candidate_occurrences"]}
    tem_irmao = any(oid_alvo in v for v in por_rotulo_pre.values())
    # O rótulo humano de novidade descreve o papel do artigo DENTRO da sua
    # ocorrência — `NEW_OCCURRENCE` quer dizer "foi ele que a abriu". A tarefa
    # do auditor em modo pós-build é outra: pertinência. Quando o artigo abriu a
    # ocorrência E um irmão posterior está entre os candidatos, a resposta certa
    # de pertinência é LIGAR, mas o contrato exige que `NEW_OCCURRENCE` venha
    # com candidato nulo. As duas coisas não cabem juntas, e cobrar a novidade
    # humana aqui puniria o modelo por dar a resposta correta.
    # Medido no corpus: 6 dos 17 alvos caem nesse caso. Contá-los como erro
    # inventaria 35% de erro que não existe; contá-los como acerto esconderia
    # que a pergunta não foi feita. Ficam explicitamente não avaliáveis.
    r["novelty_inexpressivel"] = (verdade_alvo["occurrence_novelty"] == "NEW_OCCURRENCE"
                                  and tem_irmao)
    r["novelty_correct"] = (None if r["novelty_inexpressivel"]
                            else nov == verdade_alvo["occurrence_novelty"])
    if saida.get("material_phase_assessment") != "UNKNOWN":
        r["material_phase_correct"] = (saida["material_phase_assessment"]
                                       == verdade_alvo["material_phase"])
    if verdade_alvo["should_refresh_anchor"] is not None:
        r["anchor_exact"] = (str(saida.get("should_refresh_anchor_assessment")).lower()
                             == str(verdade_alvo["should_refresh_anchor"]).lower())
    por_rotulo = {c["candidate_label"]:
                  {verdade.get(a["article_ref"])
                   for a in c["representative_articles"]} - {None}
                  for c in pp["candidate_occurrences"]}
    if sel is None:
        # §34 disse NOVA. É falso split se algum candidato carrega a verdade do alvo.
        r["false_split"] = any(oid_alvo in v for v in por_rotulo.values())
        r["linkage_correct"] = not r["false_split"]
    else:
        verdades = por_rotulo.get(sel, set())
        acertou = oid_alvo in verdades
        # §33/§36 candidato MISTO: o agrupador juntou ocorrências humanas
        # distintas. Escolhê-lo não é ligação limpa nem erro puro do modelo —
        # a impureza é do cluster, e contar como acerto esconderia isso.
        r["mixed_candidate_link"] = len(verdades) > 1
        r["linkage_correct"] = acertou and not r["mixed_candidate_link"]
        r["false_merge"] = bool(verdades) and not acertou
    r["high_impact_error"] = bool(r["false_merge"])
    return r


def agregar(resultados: list) -> dict:
    av = [r for r in resultados if r["estado"] == "ASSESSED"]
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "n_total": len(resultados),
        "n_avaliados": len(av),
        "n_abstencoes": sum(1 for r in resultados if r["estado"] == ABSTENCAO),
        "n_falhas_de_modelo": sum(1 for r in resultados if r["estado"] == SEM_AVALIACAO),
        "linkage_correct": sum(1 for r in av if r["linkage_correct"]),
        "false_merge": sum(1 for r in av if r["false_merge"]),
        "false_split": sum(1 for r in av if r["false_split"]),
        "mixed_candidate_link": sum(1 for r in av if r["mixed_candidate_link"]),
        "novelty_avaliaveis": sum(1 for r in av if r.get("novelty_correct") is not None),
        "novelty_correct": sum(1 for r in av if r["novelty_correct"]),
        "novelty_inexpressivel": sum(1 for r in av if r.get("novelty_inexpressivel")),
        "material_phase_avaliaveis": sum(1 for r in av
                                         if r["material_phase_correct"] is not None),
        "material_phase_correct": sum(1 for r in av if r["material_phase_correct"]),
        "anchor_avaliaveis": sum(1 for r in av if r["anchor_exact"] is not None),
        "anchor_exact": sum(1 for r in av if r["anchor_exact"]),
        "high_impact_error": sum(1 for r in av if r["high_impact_error"]),
    }


# ── manifesto do congelamento (§64) ─────────────────────────────────────────
def exemplos_congelados(dados: dict, historico="risk_history.json",
                        config="config_risco.yaml") -> dict:
    """§19 — cada exemplo guarda o pacote de entrada (observável) e a saída
    adjudicada. Os ids humanos ficam SÓ no metadado de avaliação."""
    occ = ot.ocorrencias(dados)
    ESCOLHA = {  # o artigo de cada caso que exemplifica o padrão semântico
        "Santander Brasil": ("troca_ceo", "cad44d85917e8bb50e46"),
        "Hapvida": ("troca_ceo", "54defbfc21b61d431ead"),
        "Smart Fit": ("ma", "601562a812028d796edb"),
    }
    out = {}
    for emp in DEFAULT_CURATED_SET:
        fam, ref = ESCOLHA[emp]
        pac = ai.construir_pacote(emp, fam, ref, historico, config)
        mem = [m for m in ot.memberships_ativas(dados)
               if m["article_ref"] == ref and m["company"] == emp]
        if not mem:
            continue
        m = mem[0]
        rot = [c["candidate_label"] for c in pac["prompt_payload"]["candidate_occurrences"]]
        oid = m["occurrence_truth_id"]
        irmaos = {a["article_ref"] for c in pac["prompt_payload"]["candidate_occurrences"]
                  for a in c["representative_articles"]}
        ver = {x["article_ref"]: x["occurrence_truth_id"]
               for x in ot.memberships_ativas(dados)}
        alvo_rot = next((c["candidate_label"] for c in
                         pac["prompt_payload"]["candidate_occurrences"]
                         if any(ver.get(a["article_ref"]) == oid
                                for a in c["representative_articles"])), None)
        saida = {
            "selected_candidate": alvo_rot,
            "occurrence_novelty": m["occurrence_novelty"] if alvo_rot else "NEW_OCCURRENCE",
            "material_phase_assessment": m["material_phase"],
            "should_refresh_anchor_assessment":
                "UNKNOWN" if m["should_refresh_anchor"] is None
                else str(m["should_refresh_anchor"]).lower(),
            "confidence": "HIGH",
            "evidence": [{"quote": pac["prompt_payload"]["target_article"]["title"][:90],
                          "origin": "TARGET_TITLE"}],
        }
        out[emp] = {"prompt_payload": pac["prompt_payload"],
                    "expected_output": saida,
                    "evaluation_metadata": {"occurrence_truth_id": oid,
                                            "article_ref": ref,
                                            "language": pac["prompt_payload"]
                                            ["target_article"].get("language"),
                                            "event_id": fam,
                                            "semantic_pattern": {
                                                "Santander Brasil": "SAME_EVENT_ANALYST_FOLLOW_UP",
                                                "Hapvida": "DISTINCT_EVENT_SAME_FAMILY",
                                                "Smart Fit": "SAME_EVENT_MATERIAL_PHASE"}[emp]},
                    "candidate_labels": rot}
    return out


def manifesto(dados: dict, git_sha: str = "", criado_em: str = "",
              historico="risk_history.json", config="config_risco.yaml") -> dict:
    ex = exemplos_congelados(dados, historico, config)
    dev = manifesto_desenvolvimento(dados)
    esquema_saida = {"novelty": list(OUT_NOVELTY), "phase": list(OUT_PHASE),
                     "anchor": list(OUT_ANCHOR), "confidence": list(OUT_CONFIDENCE),
                     "evidence_origin": list(OUT_EVIDENCE_ORIGIN),
                     "failure_states": [SEM_AVALIACAO, ABSTENCAO]}
    esquema_entrada = {"contract": ai.INPUT_CONTRACT,
                       "forbidden_in_payload": list(ai.PROIBIDOS_NO_PAYLOAD),
                       "text_evidence": list(ai.TEXT_EVIDENCE)}
    m = {
        "freeze_version": FREEZE_VERSION,
        "operating_mode": MODO_DE_OPERACAO,
        "ingestion_time_auditor": False,
        "input_version": ai.INPUT_CONTRACT, "input_hash": _hash(esquema_entrada),
        "output_version": ai.OUTPUT_CONTRACT, "output_hash": _hash(esquema_saida),
        "prompt_version": PROMPT_VERSION, "prompt_hash": _hash(prompt_texto()),
        "example_set_version": EXAMPLE_SET_VERSION,
        "example_set": list(DEFAULT_CURATED_SET),
        "example_set_hash": _hash({k: v["prompt_payload"] for k, v in ex.items()}),
        "example_outputs_hash": _hash({k: v["expected_output"] for k, v in ex.items()}),
        "dev_manifest_version": DEV_MANIFEST_VERSION,
        "dev_manifest_hash": _hash(dev),
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": _hash(io.open(__file__, encoding="utf-8").read()
                                .split("def avaliar_v1")[1].split("def agregar")[0]),
        "git_sha": git_sha, "created_at_iso": criado_em,
        # §44/§65 — nem marca de início prospectivo nem resultado de modelo.
        # Congelamento descreve o experimento, não o desfecho; e um período
        # prospectivo em que nenhum auditor rodou seria ficção.
        "prospective_start_at": None,
        "model_results": None,
    }
    m["freeze_manifest_hash"] = _hash({k: v for k, v in m.items()
                                       if k not in ("git_sha", "created_at_iso")})
    return m


def relatorio(dados: dict, git_sha="", criado_em="") -> str:
    m = manifesto(dados, git_sha, criado_em)
    fs = folds(dados)
    L = [f"## Congelamento {m['freeze_version']}", ""]
    L.append(f"*modo: {m['operating_mode']} — reconciliação depois do build, "
             f"não classificação em tempo de ingestão.*")
    L.append("")
    for k in ("input_version", "input_hash", "output_version", "output_hash",
              "prompt_version", "prompt_hash", "example_set_version",
              "example_set_hash", "dev_manifest_version", "dev_manifest_hash",
              "evaluator_version", "evaluator_hash", "freeze_manifest_hash"):
        L.append(f"- `{k}` = `{m[k]}`")
    L.append(f"- exemplos padrão: {', '.join(m['example_set'])}")
    L.append("")
    L.append("### Folds")
    for f in fs:
        L.append(f"- **{f['company']}** · `{f['event_id']}` — "
                 f"{len(f['alvos_elegiveis'])} alvo(s), "
                 f"{len(f['exemplos_permitidos'])} exemplo(s): "
                 f"{f['exemplos_permitidos'] or 'nenhum'}"
                 + (f" · excluídos: {[e['empresa'] + '/' + e['motivo'] for e in f['exemplos_excluidos']]}"
                    if f["exemplos_excluidos"] else ""))
        for i in f["alvos_inelegiveis"]:
            L.append(f"  - inelegível `{i['article_ref'][:10]}`: {i['motivo']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Congelamento V1 do auditor de ocorrência (somente leitura; "
                    "não chama modelo).")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--git-sha", default="")
    p.add_argument("--quando", default="")
    p.add_argument("--json-out", default=None)
    a = p.parse_args(argv)
    dados = json.load(io.open(a.shadow, encoding="utf-8"))
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(json.dumps(
            manifesto(dados, a.git_sha, a.quando), ensure_ascii=False,
            indent=1, sort_keys=True))
        print(f"JSON -> {a.json_out}")
    else:
        print(relatorio(dados, a.git_sha, a.quando))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
