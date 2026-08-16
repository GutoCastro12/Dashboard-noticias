#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_freeze_v3.py — duas perguntas, não uma.

O ERRO ARQUITETURAL QUE V1 E V2 COMPARTILHAVAM

Ambas exigiam `NEW_OCCURRENCE` ⇒ nenhum candidato. Em modo pós-build isso é
falso. O artigo que ABRIU uma ocorrência econômica continua tendo aberto,
mesmo que hoje exista, entre os candidatos, um irmão posterior da mesma
ocorrência. São duas perguntas diferentes:

  A. PERTINÊNCIA — a que ocorrência econômica este artigo pertence, na
     estrutura reconstruída AGORA?
  B. NOVIDADE — qual foi o papel deste artigo na cronologia daquela
     ocorrência, QUANDO foi publicado?

A regra que as amarrava chamava de contradição uma combinação que é verdade
humana registrada. A medição disso está no repositório: o alvo `54defbfc` da
Hapvida tem pertinência correta `CANDIDATE_2` e novidade humana
`NEW_OCCURRENCE`.

CONSEQUÊNCIA DESAGRADÁVEL E NECESSÁRIA

O exemplo da Hapvida na V1 — `CANDIDATE_2` + `NEW_OCCURRENCE` — estava CERTO.
Quem estava errado era o validador. A V2 "consertou" o exemplo, e ao fazê-lo
deixou os três exemplos com forma idêntica (`CANDIDATE_1` + `FOLLOW_UP`); os
modelos então responderam `CANDIDATE_1` em 34 de 34, inclusive nos 18 casos com
mais de um candidato. Consertei o lado errado da contradição.

A V3 desfaz isso na raiz: separa as perguntas e devolve a combinação ao
conjunto de saídas legítimas.

O QUE A MEDIÇÃO REVELOU SOBRE O BENCHMARK

Em 5 dos 17 alvos o agrupamento provisório PARTIU a ocorrência humana entre os
dois candidatos — são as duplicatas de Santander e Tupy que o detector de CEO
já sinaliza. Aí não existe um rótulo único correto: escolher qualquer um dos
dois pedaços acerta a identidade econômica, e o erro é do agrupamento. Por isso
a verdade de pertinência aqui é um CONJUNTO de rótulos aceitáveis, não um
rótulo. Tratá-la como rótulo único puniria o modelo por ruído nosso.

Com isso medido, `ALWAYS_CANDIDATE_1` acerta 14/17 = 82,4%. É um baseline alto,
e é a razão de a V3 congelar os baselines triviais ANTES de qualquer chamada e
exigir 15/17 para superá-lo. Sob a métrica da V2, um preditor constante marcava
82% e parecia competente; aqui ele reprova por definição.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import reliability_pilot_contract_v2 as cv2

FREEZE_VERSION = "occurrence.auditor.freeze.v3"
PROMPT_VERSION = "occurrence.auditor.prompt.v3"
OUTPUT_CONTRACT = "occurrence.auditor.output.v3"
EXAMPLE_SET_VERSION = "occurrence.auditor.examples.v3"
EVALUATOR_VERSION = "occurrence.auditor.eval.v3"
BASELINE_VERSION = "occurrence.auditor.baselines.v3"

MODO_DE_OPERACAO = "POST_BUILD_OCCURRENCE_ANOMALY_AUDITOR"
SEM_CANDIDATO = "NO_CANDIDATE"
INDETERMINADO = "UNDETERMINED"

OUT_NOVELTY = cv2.OCCURRENCE_NOVELTY          # Contract V2, intocado
OUT_PHASE = ot.MATERIAL_PHASE
OUT_ANCHOR = v1.OUT_ANCHOR
OUT_CONFIDENCE = v1.OUT_CONFIDENCE
OUT_EVIDENCE_ORIGIN = v1.OUT_EVIDENCE_ORIGIN
SEM_AVALIACAO = v1.SEM_AVALIACAO
ABSTENCAO = v1.ABSTENCAO

# Quatro empresas, não três. Com três, tirar uma pela regra LOOCV podia deixar
# o fold sem contraste — que foi exatamente como a V2 colapsou.
DEFAULT_CURATED_SET = ("BRF", "Hapvida", "Santander Brasil", "Smart Fit")

PROMPT_V3 = """Você audita a identidade de ocorrências econômicas já agrupadas.

MODO PÓS-BUILD. Os grupos candidatos são a reconstrução ATUAL. Podem conter
artigos posteriores ao artigo-alvo, inclusive da mesma ocorrência que o alvo
abriu. O alvo nunca aparece entre seus próprios candidatos.

Responda DUAS perguntas independentes.

PERGUNTA A — PERTINÊNCIA (`linked_candidate`)
A que ocorrência econômica o artigo-alvo pertence?
- `CANDIDATE_n`: o alvo pertence à mesma ocorrência econômica que o candidato n.
- `{sem}`: nenhum dos candidatos fornecidos representa a ocorrência do alvo.
- `{indef}`: a evidência textual disponível não permite decidir.

PERGUNTA B — NOVIDADE (`occurrence_novelty`)
Qual foi o papel HISTÓRICO do artigo na cronologia daquela ocorrência, na data
em que foi publicado?
{novidade}

AS DUAS SÃO INDEPENDENTES. Um artigo pode ter ABERTO a ocorrência
(`NEW_OCCURRENCE`) e ainda assim pertencer a um candidato, porque esse
candidato reúne artigos posteriores da MESMA ocorrência. Artigos posteriores
não mudam o papel histórico do alvo.

OS RÓTULOS DOS CANDIDATOS NÃO TÊM ORDEM DE PREFERÊNCIA. `CANDIDATE_1` não é
mais provável que `CANDIDATE_2`. São nomes arbitrários. Decida por evidência de
identidade: mesma transação, mesma pessoa, mesmo objeto, mesma série — não por
posição na lista.

TAMBÉM RESPONDA
- `material_phase_assessment`: fase material do fato no alvo. {fase}
- `should_refresh_anchor_assessment`: {ancora}
- `confidence`: {confianca}
- `evidence`: citações LITERAIS do texto fornecido, cada uma com `origin`
  entre {origem}. Sem raciocínio explicado, sem paráfrase.

Devolva apenas JSON no esquema pedido."""


def _hash(obj) -> str:
    bruto = (obj if isinstance(obj, str)
             else json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def prompt_texto() -> str:
    return PROMPT_V3.format(
        sem=SEM_CANDIDATO, indef=INDETERMINADO,
        novidade="\n".join(f"- `{n}`" for n in OUT_NOVELTY),
        fase=" | ".join(OUT_PHASE), ancora=" | ".join(OUT_ANCHOR),
        confianca=" | ".join(OUT_CONFIDENCE),
        origem=" | ".join(OUT_EVIDENCE_ORIGIN))


SCHEMA_SAIDA = {
    "type": "object",
    "properties": {
        "linked_candidate": {"type": "string"},
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
    },
    "required": ["linked_candidate", "occurrence_novelty",
                 "material_phase_assessment",
                 "should_refresh_anchor_assessment", "confidence"],
}


def validar_saida(s: dict, rotulos_validos) -> list:
    """Só rejeita violação ESTRUTURAL. Nenhuma combinação de pertinência com
    novidade é proibida — foi a proibição falsa que criou a contradição da V1."""
    probs = []
    if not isinstance(s, dict):
        return ["MALFORMED"]
    lk = s.get("linked_candidate")
    if lk is None:
        probs.append("LINKED_AUSENTE")
    elif lk not in list(rotulos_validos) + [SEM_CANDIDATO, INDETERMINADO]:
        probs.append("CANDIDATO_DESCONHECIDO")
    if s.get("occurrence_novelty") not in OUT_NOVELTY:
        probs.append("NOVELTY_INVALIDA")
    if s.get("material_phase_assessment") not in OUT_PHASE:
        probs.append("FASE_INVALIDA")
    if str(s.get("should_refresh_anchor_assessment")) not in OUT_ANCHOR:
        probs.append("ANCORA_INVALIDA")
    if s.get("confidence") not in OUT_CONFIDENCE:
        probs.append("CONFIANCA_INVALIDA")
    if lk != INDETERMINADO:
        ev = s.get("evidence") or []
        if not ev:
            probs.append("SEM_EVIDENCIA")
        for e in ev:
            if not isinstance(e, dict) or not e.get("quote"):
                probs.append("EVIDENCIA_MALFORMADA")
            elif e.get("origin") not in OUT_EVIDENCE_ORIGIN:
                probs.append("ORIGEM_DE_EVIDENCIA_INVALIDA")
    return sorted(set(probs))


def _mapa_verdade(dados):
    return {m["article_ref"]: m["occurrence_truth_id"]
            for m in ot.memberships_ativas(dados)}


def verdade_de_pertinencia(pacote, occ_id, ver):
    """Devolve o CONJUNTO de rótulos aceitáveis, quais são mistos, e se a
    ocorrência humana está partida entre candidatos.

    Um único rótulo correto seria uma simplificação falsa. Em 5 dos 17 alvos
    (Santander e Tupy — as mesmas duplicatas que o detector de CEO sinaliza) o
    agrupamento provisório PARTIU a ocorrência humana em dois candidatos.
    Nesses casos, escolher qualquer um dos dois está certo quanto à identidade
    econômica: o erro é do agrupamento, não de quem responde. Punir o modelo
    por escolher "o outro pedaço do mesmo fato" mediria ruído."""
    aceitaveis, mistos = [], set()
    for c in pacote["prompt_payload"]["candidate_occurrences"]:
        oids = {ver.get(x["article_ref"]) for x in c["representative_articles"]}
        oids.discard(None)
        if occ_id in oids:
            aceitaveis.append(c["candidate_label"])
            if len(oids) > 1:
                mistos.add(c["candidate_label"])
    if not aceitaveis:
        return [SEM_CANDIDATO], set(), False
    return aceitaveis, mistos, len(aceitaveis) > 1


folds = v2.folds
montar_prompt = v1.montar_prompt


def exemplos_do_fold(empresa: str) -> list:
    """LOOCV genérico: todo o conjunto curado, menos a empresa do fold.

    A lista herdada da V1 foi montada para três empresas e não conhece a BRF —
    usá-la deixaria três folds sem contraste de pertinência, que é exatamente
    a condição que fez a V2 colapsar. A regra aqui não olha o alvo: exclui pela
    empresa e mais nada."""
    return [e for e in DEFAULT_CURATED_SET if e != empresa]
manifesto_desenvolvimento = v1.manifesto_desenvolvimento
exemplos_permitidos = v1.exemplos_permitidos


def alvos_com_verdade(dados, historico="risk_history.json",
                      config="config_risco.yaml") -> list:
    ver = _mapa_verdade(dados)
    out = []
    for f in folds(dados, historico, config):
        for a in f["alvos_elegiveis"]:
            m = a["verdade_humana"]
            rot, mistos, partida = verdade_de_pertinencia(
                a["pacote"], m["occurrence_truth_id"], ver)
            out.append({"company": f["company"], "fold_id": f.get("fold_id"),
                        "event_id": f["event_id"],
                        "article_ref": a["article_ref"],
                        "target_id": a.get("target_id"),
                        "pacote": a["pacote"], "verdade_humana": m,
                        "linkage_aceitaveis": rot, "candidatos_mistos": mistos,
                        "ocorrencia_partida": partida,
                        "novelty_verdade": m["occurrence_novelty"],
                        "exemplos_permitidos": exemplos_do_fold(f["company"])})
    return sorted(out, key=lambda x: (x["company"], x["article_ref"]))


BASELINES = ("ALWAYS_CANDIDATE_1", "ALWAYS_CANDIDATE_2", "ALWAYS_NO_CANDIDATE",
             "ALWAYS_UNDETERMINED")


def baselines_triviais(dados, historico="risk_history.json",
                       config="config_risco.yaml") -> dict:
    """Congelado ANTES de qualquer chamada. Sem isto, um preditor constante
    volta a parecer inteligente."""
    alvos = alvos_com_verdade(dados, historico, config)
    out = {}
    for b in BASELINES:
        pred = {"ALWAYS_CANDIDATE_1": "CANDIDATE_1",
                "ALWAYS_CANDIDATE_2": "CANDIDATE_2",
                "ALWAYS_NO_CANDIDATE": SEM_CANDIDATO,
                "ALWAYS_UNDETERMINED": INDETERMINADO}[b]
        ok = [a for a in alvos if pred in a["linkage_aceitaveis"]]
        fm = [a for a in alvos if pred not in a["linkage_aceitaveis"]
              and pred.startswith("CANDIDATE")]
        out[b] = {"correct": len(ok), "total": len(alvos),
                  "accuracy": round(len(ok) / len(alvos), 3),
                  "false_merge_like": len(fm),
                  "hapvida_wrong": sum(1 for a in alvos
                                       if a["company"] == "Hapvida"
                                       and pred not in a["linkage_aceitaveis"])}
    forte = max(out, key=lambda k: out[k]["correct"])
    out["_strongest"] = forte
    out["_strongest_correct"] = out[forte]["correct"]
    out["_required_to_beat"] = out[forte]["correct"] + 1
    _pf = {"ALWAYS_CANDIDATE_1": "CANDIDATE_1",
           "ALWAYS_CANDIDATE_2": "CANDIDATE_2",
           "ALWAYS_NO_CANDIDATE": SEM_CANDIDATO,
           "ALWAYS_UNDETERMINED": INDETERMINADO}[forte]
    alvos_min = [a["article_ref"] for a in alvos
                 if _pf not in a["linkage_aceitaveis"]]
    out["_minority_targets"] = sorted(alvos_min)
    out["_minority_n"] = len(alvos_min)
    return out


def _rotulo_canonico(a):
    """Para exemplos e baselines é preciso UM rótulo. Usa-se o menor aceitável,
    critério determinístico e independente de qualquer saída de modelo."""
    return sorted(a["linkage_aceitaveis"])[0]


def _forma(a):
    lk = _rotulo_canonico(a)
    return ("CANDIDATE" if lk.startswith("CANDIDATE") else lk, lk)


def exemplos_congelados(dados, historico="risk_history.json",
                        config="config_risco.yaml") -> dict:
    """Política determinística de contraste: para cada empresa do conjunto
    curado, em ordem fixa, escolhe a pertinência cujo par
    (forma-de-pertinência, novidade) é o MENOS representado até então;
    empate resolvido pelo menor `article_ref`.

    O desempate usa a raridade de cada rótulo e de cada novidade NO PRÓPRIO
    devset: entre opções igualmente novas para o conjunto, ensina-se a classe
    mais rara. Sem isso, um passe guloso otimiza um eixo e arruína o outro —
    medi as duas variantes antes de chegar aqui.

    Não olha o alvo em avaliação — a escolha depende só da verdade humana e da
    ordem congelada. Foi a ausência de contraste, e não a escolha em si, que
    fez a V2 colapsar."""
    alvos = alvos_com_verdade(dados, historico, config)
    # Frequência de cada novidade no próprio devset. Serve de desempate:
    # entre duas opções igualmente novas para o conjunto, ensina-se a classe
    # MAIS RARA. É genérico e não olha o alvo em avaliação — sem isso, o fold
    # de Santander ficava sem nenhum exemplo de `NEW_OCCURRENCE`, porque o
    # único era o dele mesmo, excluído pela regra LOOCV.
    freq_nov, freq_lk = {}, {}
    for a in alvos:
        freq_nov[a["novelty_verdade"]] = freq_nov.get(a["novelty_verdade"], 0) + 1
        for r in a["linkage_aceitaveis"]:
            freq_lk[r] = freq_lk.get(r, 0) + 1
    vistos, out = {}, {}
    for emp in DEFAULT_CURATED_SET:
        cands = sorted((a for a in alvos if a["company"] == emp),
                       key=lambda x: x["article_ref"])
        if not cands:
            continue
        esc = min(cands, key=lambda a: (
            vistos.get((_rotulo_canonico(a), a["novelty_verdade"]), 0),
            freq_lk.get(_rotulo_canonico(a), 0),
            freq_nov.get(a["novelty_verdade"], 0),
            a["article_ref"]))
        _ch = (_rotulo_canonico(esc), esc["novelty_verdade"])
        vistos[_ch] = vistos.get(_ch, 0) + 1
        m = esc["verdade_humana"]
        out[emp] = {
            "prompt_payload": esc["pacote"]["prompt_payload"],
            "expected_output": {
                "linked_candidate": _rotulo_canonico(esc),
                "occurrence_novelty": esc["novelty_verdade"],
                "material_phase_assessment": m["material_phase"],
                "should_refresh_anchor_assessment":
                    "UNKNOWN" if m["should_refresh_anchor"] is None
                    else str(m["should_refresh_anchor"]).lower(),
                "confidence": "HIGH",
                "evidence": [{"quote": esc["pacote"]["prompt_payload"]
                              ["target_article"]["title"][:90],
                              "origin": "TARGET_TITLE"}]},
            "evaluation_metadata": {
                "occurrence_truth_id": m["occurrence_truth_id"],
                "article_ref": esc["article_ref"],
                "provenance": "HUMAN_ADJUDICATED"},
            "candidate_labels": [c["candidate_label"] for c in
                                 esc["pacote"]["prompt_payload"]
                                 ["candidate_occurrences"]]}
    return out


def diversidade(exs: dict) -> dict:
    lk = {("CANDIDATE" if e["expected_output"]["linked_candidate"]
           .startswith("CANDIDATE") else e["expected_output"]["linked_candidate"])
          for e in exs.values()}
    lk_exato = {e["expected_output"]["linked_candidate"] for e in exs.values()}
    nov = {e["expected_output"]["occurrence_novelty"] for e in exs.values()}
    return {"linkage_classes": sorted(lk), "linkage_labels": sorted(lk_exato),
            "novelty_classes": sorted(nov),
            "ok": len(lk_exato) >= 2 and len(nov) >= 2}


def avaliar_v3(alvo, saida) -> dict:
    """Pertinência e novidade avaliadas SEPARADAMENTE. Nenhum alvo é
    descartado por 'novidade inexprimível' — essa categoria era consequência da
    regra falsa, não do dado."""
    r = {"company": alvo["company"], "article_ref": alvo["article_ref"],
         "linkage_aceitaveis": sorted(alvo["linkage_aceitaveis"]),
         "ocorrencia_partida": alvo["ocorrencia_partida"],
         "novelty_verdade": alvo["novelty_verdade"],
         "evaluator_version": EVALUATOR_VERSION,
         "linkage_correct": False, "novelty_correct": False,
         "false_merge": False, "false_split": False,
         "mixed_candidate": False, "abstencao": False, "erro": None}
    if not isinstance(saida, dict):
        r["erro"] = SEM_AVALIACAO
        return r
    rot = [c["candidate_label"] for c in
           alvo["pacote"]["prompt_payload"]["candidate_occurrences"]]
    probs = validar_saida(saida, rot)
    if probs:
        r["erro"] = SEM_AVALIACAO
        r["parse_problems"] = probs
        return r
    lk, vd = saida["linked_candidate"], alvo["linkage_aceitaveis"]
    r["linked_predito"] = lk
    r["novelty_predito"] = saida["occurrence_novelty"]
    r["novelty_correct"] = saida["occurrence_novelty"] == alvo["novelty_verdade"]
    if lk == INDETERMINADO:
        r["abstencao"] = True
        r["erro"] = ABSTENCAO
        return r
    if lk in vd:
        if lk in alvo["candidatos_mistos"]:
            r["mixed_candidate"] = True
            r["erro"] = "MIXED_CANDIDATE"
        else:
            r["linkage_correct"] = True
    elif lk == SEM_CANDIDATO:
        r["false_split"] = True
        r["erro"] = "FALSE_SPLIT"
    else:
        r["false_merge"] = True
        r["erro"] = "FALSE_MERGE"
    return r


LIMIAR_PARSE = 0.80
LIMIAR_FALSE_MERGE = 1
LIMIAR_HAPVIDA_FM = 0


def agregar_v3(resultados: list, baselines: dict) -> dict:
    n = len(resultados)
    falhas = sum(1 for r in resultados if r["erro"] == SEM_AVALIACAO)
    lk_ok = sum(1 for r in resultados if r["linkage_correct"])
    preditos = {r.get("linked_predito") for r in resultados
                if r.get("linked_predito")}
    minor = set(baselines["_minority_targets"])
    minor_ok = sum(1 for r in resultados
                   if r["article_ref"] in minor and r["linkage_correct"])
    hap_fm = sum(1 for r in resultados
                 if r["company"] == "Hapvida" and r["false_merge"])
    fm = sum(1 for r in resultados if r["false_merge"])
    parse = (n - falhas) / n if n else 0.0
    nao_colapso = len(preditos) >= 2 and minor_ok >= 1
    return {
        "n_total": n, "n_falhas_de_parse": falhas,
        "parse_success": round(parse, 3),
        "linkage_correct": lk_ok,
        "linkage_accuracy": round(lk_ok / n, 3) if n else 0.0,
        "novelty_correct": sum(1 for r in resultados if r["novelty_correct"]),
        "novelty_denominador": n,
        "false_merge": fm,
        "false_split": sum(1 for r in resultados if r["false_split"]),
        "mixed_candidate": sum(1 for r in resultados if r["mixed_candidate"]),
        "abstencoes": sum(1 for r in resultados if r["abstencao"]),
        "hapvida_false_merge": hap_fm,
        "classes_preditas": sorted(preditos),
        "minority_n": baselines["_minority_n"],
        "minority_correct": minor_ok,
        "strongest_trivial_baseline": baselines["_strongest"],
        "strongest_trivial_correct": baselines["_strongest_correct"],
        "required_to_beat": baselines["_required_to_beat"],
        "beats_trivial": lk_ok >= baselines["_required_to_beat"],
        "non_collapse": nao_colapso,
        "development_sane": (parse >= LIMIAR_PARSE
                             and hap_fm <= LIMIAR_HAPVIDA_FM
                             and fm <= LIMIAR_FALSE_MERGE
                             and lk_ok >= baselines["_required_to_beat"]
                             and nao_colapso),
    }


def manifesto(dados, git_sha="", criado_em="", historico="risk_history.json",
              config="config_risco.yaml") -> dict:
    ex = exemplos_congelados(dados, historico, config)
    bl = baselines_triviais(dados, historico, config)
    esq_saida = {"linkage": ["CANDIDATE_n", SEM_CANDIDATO, INDETERMINADO],
                 "novelty": list(OUT_NOVELTY), "phase": list(OUT_PHASE),
                 "anchor": list(OUT_ANCHOR), "confidence": list(OUT_CONFIDENCE),
                 "evidence_origin": list(OUT_EVIDENCE_ORIGIN),
                 "orthogonal": True, "cross_field_prohibitions": []}
    esq_entrada = {"contract": ai.INPUT_CONTRACT,
                   "forbidden_in_payload": list(ai.PROIBIDOS_NO_PAYLOAD),
                   "text_evidence": list(ai.TEXT_EVIDENCE)}
    m = {
        "freeze_version": FREEZE_VERSION, "supersedes": v2.FREEZE_VERSION,
        "operating_mode": MODO_DE_OPERACAO, "ingestion_time_auditor": False,
        "linkage_novelty_orthogonal": True,
        "candidate_order_rule": "chronological_by_first_date_oldest_is_1",
        "candidate_order_policy": "PRESERVE_RUNTIME_ORDER",
        "input_version": ai.INPUT_CONTRACT, "input_hash": _hash(esq_entrada),
        "output_version": OUTPUT_CONTRACT, "output_hash": _hash(esq_saida),
        "prompt_version": PROMPT_VERSION, "prompt_hash": _hash(prompt_texto()),
        "example_set_version": EXAMPLE_SET_VERSION,
        "example_set": list(DEFAULT_CURATED_SET),
        "example_set_hash": _hash({k: v["prompt_payload"] for k, v in ex.items()}),
        "example_outputs_hash": _hash({k: v["expected_output"]
                                       for k, v in ex.items()}),
        "synthetic_controls": [], "synthetic_control_hash": None,
        "dev_manifest_version": v1.DEV_MANIFEST_VERSION,
        "dev_manifest_hash": _hash(manifesto_desenvolvimento(dados)),
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": _hash(io.open(__file__, encoding="utf-8").read()
                                .split("def avaliar_v3")[1]
                                .split("def manifesto")[0]),
        "baseline_version": BASELINE_VERSION,
        "baseline_hash": _hash({k: v for k, v in bl.items()
                                if not k.startswith("_")}),
        "sanity_gate": {"parse_success": LIMIAR_PARSE,
                        "false_merge_max": LIMIAR_FALSE_MERGE,
                        "hapvida_false_merge_max": LIMIAR_HAPVIDA_FM,
                        "linkage_correct_min": bl["_required_to_beat"],
                        "non_collapse": "≥2 classes preditas E ≥1 acerto "
                                        "em alvo de classe minoritária"},
        "git_sha": git_sha, "created_at_iso": criado_em, "model_results": None,
    }
    m["freeze_manifest_hash"] = _hash({k: v for k, v in m.items()
                                       if k not in ("git_sha", "created_at_iso")})
    return m


HASHES_V3 = {
    "input_hash": "e9d33218fd811d13",
    "output_hash": "bb0ee5497d352542",
    "prompt_hash": "e527dad21516f853",
    "example_set_hash": "6ea9a6519b3066bb",
    "example_outputs_hash": "20508067c2274365",
    "dev_manifest_hash": "82cda660cdece064",
    "evaluator_hash": "99985aac21da670b",
    "baseline_hash": "41ffcd20d2afd027",
    "freeze_manifest_hash": "d5c31ee1810a770b",
}


def verificar_congelamento(dados, historico="risk_history.json",
                           config="config_risco.yaml") -> list:
    if not HASHES_V3:
        return []
    m = manifesto(dados, historico=historico, config=config)
    return sorted((k, HASHES_V3[k], m.get(k))
                  for k in HASHES_V3 if m.get(k) != HASHES_V3[k])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Congelamento V3 (somente leitura).")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--json-out", default=None)
    a = p.parse_args(argv)
    dados = json.load(io.open(a.shadow, encoding="utf-8"))
    m = manifesto(dados)
    saida = json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True)
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(saida)
    else:
        print(saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
