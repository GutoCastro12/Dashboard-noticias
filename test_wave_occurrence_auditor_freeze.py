#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_freeze.py — o experimento congelado antes de medir.

POR QUE ISTO É UM TESTE E NÃO UMA FORMALIDADE

Se prompt, exemplos ou esquema forem ajustados depois de ver o erro do modelo e
a versão continuar a mesma, o resultado deixa de ser holdout e vira ajuste — e
nada no arquivo denuncia. Os hashes existem para que essa mudança seja
impossível de fazer em silêncio.

A DISTINÇÃO QUE MAIS IMPORTA AQUI

Exemplo curado CONTÉM saída humana adjudicada. É essa a supervisão, e é
deliberada. Um teste que proibisse todo rótulo humano no prompt estaria
proibindo o few-shot de existir.

O proibido é a verdade do ALVO, ou verdade da mesma ocorrência ou da mesma
empresa. Por isso a suíte separa as duas coisas: exige que o exemplo do
Santander traga a resposta adjudicada dele, e exige que ela desapareça quando o
Santander é o alvo.

E A REGRA QUE PARECE PEQUENA E NÃO É

Quando um exemplo sai, nada entra no lugar. Repor exigiria escolher um
substituto olhando o que o alvo é — viés que nenhum número posterior
denunciaria. Fold com dois exemplos é aceitável; fold com três escolhidos a
dedo, não.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_archival_source as arq
import reliability_occurrence_auditor_freeze as fz
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import reliability_pilot_contract_v2 as v2

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
FOLDS = fz.folds(D, historico=arq.HISTORICO)
POR_EMPRESA = {}
for _f in FOLDS:
    POR_EMPRESA.setdefault(_f["company"], []).append(_f)
EX = fz.exemplos_congelados(D, historico=arq.HISTORICO)
MAN = fz.manifesto(D, historico=arq.HISTORICO, git_sha="teste", criado_em="2026-08-16T00:00:00Z")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


print("=" * 98)
print("§6/§64 MANIFESTO DO CONGELAMENTO")
print("=" * 98)
check(fz.FREEZE_VERSION == "occurrence.auditor.freeze.v1",
      f"[1] versão do congelamento ({fz.FREEZE_VERSION})")
# Checar `len(hash) == 16` confirma que o hash EXISTE, nunca que ele É o
# publicado. Foi por isso que a suíte ficou verde depois de eu editar
# `avaliar_v1` e o checkpoint registrar um hash obsoleto: a asserção não tinha
# como falhar. Agora cada valor é comparado ao literal canônico da V1, e a
# checagem de formato fica como secundária.
_n = 2
for k in ("input_hash", "output_hash", "prompt_hash", "example_set_hash",
          "example_outputs_hash", "dev_manifest_hash", "evaluator_hash",
          "freeze_manifest_hash"):
    check(MAN[k] == fz.HASHES_V1[k],
          f"[{_n}] `{k}` == `{fz.HASHES_V1[k]}` ({MAN[k]})")
    _n += 1
check(all(len(v) == 16 for v in fz.HASHES_V1.values()),
      f"[{_n}] e todos têm o formato esperado")
_n += 1
check(fz.verificar_congelamento(D, historico=arq.HISTORICO) == [],
      f"[{_n}] a verificação do módulo confirma integridade "
      f"({fz.verificar_congelamento(D, historico=arq.HISTORICO) or 'sem divergência'})")
_n += 1
_lit = io.open("reliability_occurrence_auditor_freeze.py", encoding="utf-8").read()
_bloco = _lit.split("HASHES_V1 = {")[1].split("}")[0]
check('"' in _bloco and "_hash(" not in _bloco and "manifesto(" not in _bloco,
      f"[{_n}] §11 os esperados são LITERAIS, não recalculados — "
      "`esperado = calcular_agora()` passaria depois de qualquer alteração")
_n += 1
check(set(fz.HASHES_V1) <= set(MAN),
      f"[{_n}] §9 e todo hash fixado existe no manifesto")
_n += 1
_amarrados = {k for k in MAN if k.endswith("_hash") and k != "freeze_manifest_hash"}
check(_amarrados <= set(fz.HASHES_V1),
      f"[{_n}] §36 o manifesto não tem hash de componente fora do conjunto "
      f"fixado ({sorted(_amarrados - set(fz.HASHES_V1)) or 'nenhum'})")
_n += 1
check(MAN["freeze_version"] != v2.__name__ and "v2" not in fz.FREEZE_VERSION,
      f"[{_n}] §6 o congelamento é identificador PRÓPRIO, separado do Contract V2")
_n += 1
check(fz.FREEZE_VERSION != "ceo.dup.v1" and fz.PROMPT_VERSION != ai.INPUT_CONTRACT,
      f"[{_n}] e separado do detector e dos esquemas de entrada/saída")
_n += 1
_m2 = fz.manifesto(D, historico=arq.HISTORICO, git_sha="outro", criado_em="2027-01-01T00:00:00Z")
check(_m2["freeze_manifest_hash"] == MAN["freeze_manifest_hash"],
      f"[{_n}] o hash do manifesto não depende de SHA nem de relógio — só do "
      "que descreve o experimento")
_n += 1
check(MAN["model_results"] is None,
      f"[{_n}] §65 nenhum campo de resultado de modelo: congelamento descreve "
      "o experimento, não o desfecho")
_n += 1
check(MAN["prospective_start_at"] is None,
      f"[{_n}] §44 e nenhuma marca de início prospectivo — um período em que "
      "nenhum auditor rodou seria ficção")
_n += 1

print()
print("=" * 98)
print("§29/§30 MODO DE OPERAÇÃO DECLARADO")
print("=" * 98)
check(MAN["operating_mode"] == "POST_BUILD_OCCURRENCE_ANOMALY_AUDITOR",
      f"[{_n}] modo pós-build ({MAN['operating_mode']})")
_n += 1
check(MAN["ingestion_time_auditor"] is False,
      f"[{_n}] §21 e o manifesto NEGA explicitamente ser auditor de ingestão — "
      "o número daqui não mede desempenho na chegada do artigo")
_n += 1
_sf = POR_EMPRESA["Smart Fit"][0]
_alvo = _sf["alvos_elegiveis"][0]
_datas = [a["publication_date"]
          for c in _alvo["pacote"]["prompt_payload"]["candidate_occurrences"]
          for a in c["representative_articles"]]
check(any(d > _alvo["pacote"]["prompt_payload"]["target_article"]["publication_date"]
          for d in _datas),
      f"[{_n}] §31 e de fato há irmão POSTERIOR ao alvo entre os candidatos — "
      "legítimo em reconciliação, e é por isso que o modo tem de estar escrito")
_n += 1

print()
print("=" * 98)
print("§21-§25 FOLDS: EXCLUSÃO SEM REPOSIÇÃO")
print("=" * 98)
check(len(FOLDS) == 7, f"[{_n}] um fold por ocorrência humana ({len(FOLDS)})")
_n += 1
_esp = {"Santander Brasil": 2, "Tupy": 3, "Yura": 3, "Smart Fit": 2, "BRF": 3}
for emp, n_ex in _esp.items():
    f = POR_EMPRESA[emp][0]
    check(len(f["exemplos_permitidos"]) == n_ex,
          f"[{_n}] {emp}: {n_ex} exemplo(s) ({f['exemplos_permitidos']})")
    _n += 1
for f in POR_EMPRESA["Hapvida"]:
    check("Hapvida" not in f["exemplos_permitidos"]
          and len(f["exemplos_permitidos"]) == 2,
          f"[{_n}] §22 Hapvida: os exemplos da PRÓPRIA empresa saem nos dois "
          f"folds ({f['exemplos_permitidos']})")
    _n += 1
_hb = POR_EMPRESA["Hapvida"][1]
_mot = [e["motivo"] for e in _hb["exemplos_excluidos"] if e["empresa"] == "Hapvida"]
check(_mot and "MESMA_EMPRESA" in _mot[0],
      f"[{_n}] e o motivo registrado diz a verdade: a Hapvida A sai por ser da "
      f"mesma EMPRESA, não por ser a mesma ocorrência ({_mot})")
_n += 1
check(all(set(f["exemplos_permitidos"]) <= set(fz.DEFAULT_CURATED_SET)
          for f in FOLDS),
      f"[{_n}] §24 nenhum fold ganha exemplo fora do conjunto padrão — não há "
      "reposição, e repor exigiria escolher olhando a resposta")
_n += 1
check(any(len(f["exemplos_permitidos"]) < 3 for f in FOLDS),
      f"[{_n}] e folds com menos exemplos existem, que é o preço aceito")
_n += 1

print()
print("=" * 98)
print("§26/§27 ALVOS ELEGÍVEIS E DEGENERADOS")
print("=" * 98)
_tot = sum(len(f["alvos_elegiveis"]) for f in FOLDS)
_ine = sum(len(f["alvos_inelegiveis"]) for f in FOLDS)
check(_tot == 17, f"[{_n}] 17 alvos elegíveis ({_tot})")
_n += 1
check(_ine == 0, f"[{_n}] e nenhum degenerado: toda pertinência adjudicada tem "
                 f"pelo menos um candidato a decidir ({_ine})")
_n += 1
_brf = POR_EMPRESA["BRF"][0]
check(len(_brf["alvos_elegiveis"]) == 1,
      f"[{_n}] §59 BRF avalia SÓ a pertinência adjudicada — os irmãos que o "
      "agrupador junta não são verdade humana")
_n += 1
_deg = [i for f in FOLDS for i in f["alvos_inelegiveis"]]
check(all("SEM_CANDIDATO" in i["motivo"] or "AUSENTE" in i["motivo"] for i in _deg),
      f"[{_n}] §27 e o inelegível traz motivo explícito ({_deg})")
_n += 1

print()
print("=" * 98)
print("§46/§47/§48 VAZAMENTO — O QUE É PROIBIDO E O QUE É SUPERVISÃO")
print("=" * 98)
_ids = set(ot.ocorrencias(D))
for f in FOLDS:
    for alvo in f["alvos_elegiveis"]:
        p = fz.montar_prompt(f["exemplos_permitidos"], alvo["pacote"], EX)
        bruto = json.dumps(p["target"], ensure_ascii=False)
        assert not any(i in bruto for i in _ids)
check(True, f"[{_n}] §46 nenhum dos 7 ids humanos aparece no ALVO de nenhum "
            f"dos {_tot} prompts")
_n += 1
for f in FOLDS:
    for alvo in f["alvos_elegiveis"]:
        p = fz.montar_prompt(f["exemplos_permitidos"], alvo["pacote"], EX)
        assert ai.vazamentos({"prompt_payload": p["target"]}) == []
check(True, f"[{_n}] e a varredura de termos proibidos passa em todos")
_n += 1
_san_fold = POR_EMPRESA["Santander Brasil"][0]
_p = fz.montar_prompt(_san_fold["exemplos_permitidos"],
                      _san_fold["alvos_elegiveis"][0]["pacote"], EX)
check("Santander" not in json.dumps(_p["examples"], ensure_ascii=False),
      f"[{_n}] §55 no fold do Santander, o exemplo Santander sumiu do prompt")
_n += 1
for f in POR_EMPRESA["Hapvida"]:
    p = fz.montar_prompt(f["exemplos_permitidos"], f["alvos_elegiveis"][0]["pacote"], EX)
    check("Hapvida" not in json.dumps(p["examples"], ensure_ascii=False)
          and "DISTINCT_OCCURRENCE" not in json.dumps(p, ensure_ascii=False),
          f"[{_n}] §22/§60 no fold da Hapvida, nem o exemplo dela nem o rótulo "
          "DISTINCT aparecem — seriam a resposta de graça")
    _n += 1
check(any(e["expected_output"]["occurrence_novelty"] in v2.OCCURRENCE_NOVELTY
          for e in EX.values()),
      f"[{_n}] §48 mas os exemplos NÃO-alvo TRAZEM saída humana adjudicada — "
      "essa é a supervisão, é deliberada, e proibi-la seria proibir o few-shot")
_n += 1
check(all("occurrence_truth_id" not in json.dumps(e["prompt_payload"], ensure_ascii=False)
          for e in EX.values()),
      f"[{_n}] §18 e ainda assim nenhum id humano entra no payload do exemplo")
_n += 1
check(all("occurrence_truth_id" in e["evaluation_metadata"] for e in EX.values()),
      f"[{_n}] §19 eles vivem só no metadado de avaliação")
_n += 1
_injetado = {"prompt_payload": {"x": "occurrence_truth_id troca_ceo:x:aaaaaaaaaaaa"}}
check(ai.vazamentos(_injetado) != [],
      f"[{_n}] §68 e a varredura sabe falhar quando um id é injetado")
_n += 1

print()
print("=" * 98)
print("§8-§13 ESQUEMA DE SAÍDA E COERÊNCIA")
print("=" * 98)
check(fz.OUT_NOVELTY is v2.OCCURRENCE_NOVELTY,
      f"[{_n}] §45 novidade é O MESMO enum do Contract V2")
_n += 1
check(fz.OUT_PHASE is ot.MATERIAL_PHASE,
      f"[{_n}] §46 e a fase é a do esquema humano, não um enum paralelo")
_n += 1
check(set(fz.OUT_ANCHOR) == {"true", "false", "UNKNOWN"},
      f"[{_n}] §47 âncora aceita UNKNOWN")
_n += 1
check(set(fz.OUT_CONFIDENCE) == {"HIGH", "MEDIUM", "LOW"},
      f"[{_n}] §13 confiança é enum fechado, sem probabilidade inventada")
_n += 1
check(fz.SEM_AVALIACAO == "MODEL_FAILURE" and fz.ABSTENCAO == "MODEL_ABSTENTION",
      f"[{_n}] §53 falha de provider e abstenção do modelo são estados "
      "DISTINTOS — tratar parse quebrado como UNDETERMINED inventaria uma "
      "resposta que ninguém deu")
_n += 1
_ROT = ["CANDIDATE_1", "CANDIDATE_2"]
_ok = {"selected_candidate": "CANDIDATE_1", "occurrence_novelty": "FOLLOW_UP",
       "material_phase_assessment": "NONE", "should_refresh_anchor_assessment": "UNKNOWN",
       "confidence": "HIGH", "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}
check(fz.validar_saida(_ok, _ROT) == [], f"[{_n}] saída coerente é aceita")
_n += 1
for mut, esperado, rot in (
        ({"occurrence_novelty": "NEW_OCCURRENCE"}, "CONTRADICAO_NOVA_COM_CANDIDATO",
         "NOVA com candidato"),
        ({"selected_candidate": None}, "CONTRADICAO_SEGUIMENTO_SEM_CANDIDATO",
         "seguimento sem candidato"),
        ({"selected_candidate": "CANDIDATE_9"}, "CANDIDATO_DESCONHECIDO",
         "rótulo inexistente"),
        ({"occurrence_novelty": "MATERIAL_NEW_PHASE"}, "NOVELTY_INVALIDA",
         "novidade fora do Contract V2"),
        ({"material_phase_assessment": "INVENTADA"}, "FASE_INVALIDA", "fase inventada"),
        ({"confidence": 0.93}, "CONFIANCA_INVALIDA", "probabilidade em vez de enum"),
        ({"evidence": []}, "SEM_EVIDENCIA", "sem evidência"),
        ({"evidence": [{"quote": "x", "origin": "IMAGINACAO"}]},
         "ORIGEM_DE_EVIDENCIA_INVALIDA", "origem de evidência inválida")):
    s = dict(_ok)
    s.update(mut)
    check(esperado in fz.validar_saida(s, _ROT), f"[{_n}] §52 rejeita {rot}")
    _n += 1
_abs = {"selected_candidate": None, "occurrence_novelty": "UNDETERMINED",
        "material_phase_assessment": "UNKNOWN",
        "should_refresh_anchor_assessment": "UNKNOWN",
        "abstention_reason": "evidência insuficiente"}
check(fz.validar_saida(_abs, _ROT) == [],
      f"[{_n}] §9 abstenção é representável e distinta de NOVA confiante")
_n += 1
check("ABSTENCAO_SEM_MOTIVO" in fz.validar_saida(
    {k: v for k, v in _abs.items() if k != "abstention_reason"}, _ROT),
      f"[{_n}] e abster-se exige motivo")
_n += 1

print()
print("=" * 98)
print("§33-§41/§54 AVALIADOR CONGELADO — SAÍDAS SINTÉTICAS")
print("=" * 98)


def alvo_de(empresa, idx=0, fold=0):
    return POR_EMPRESA[empresa][fold]["alvos_elegiveis"][idx]


def rotulo_da_verdade(alvo):
    ver = {m["article_ref"]: m["occurrence_truth_id"]
           for m in ot.memberships_ativas(D)}
    oid = alvo["verdade_humana"]["occurrence_truth_id"]
    for c in alvo["pacote"]["prompt_payload"]["candidate_occurrences"]:
        if any(ver.get(a["article_ref"]) == oid for a in c["representative_articles"]):
            return c["candidate_label"]
    return None


def saida(sel, nov, fase="NONE", anc="UNKNOWN"):
    s = {"selected_candidate": sel, "occurrence_novelty": nov,
         "material_phase_assessment": fase,
         "should_refresh_anchor_assessment": anc, "confidence": "HIGH",
         "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}
    if nov == "UNDETERMINED":
        s = {"selected_candidate": None, "occurrence_novelty": "UNDETERMINED",
             "material_phase_assessment": "UNKNOWN",
             "should_refresh_anchor_assessment": "UNKNOWN",
             "abstention_reason": "insuficiente"}
    return s


for emp, rot in (("Santander Brasil", "Santander"), ("Tupy", "Tupy"),
                 ("Yura", "Yura"), ("Smart Fit", "Smart Fit"), ("BRF", "BRF")):
    a = alvo_de(emp)
    lab = rotulo_da_verdade(a)
    if lab is None:
        check(True, f"[{_n}] {rot}: sem candidato portador da verdade — "
                    "dizer NOVA é o correto aqui")
        _n += 1
        r = fz.avaliar_v1(a["pacote"], saida(None, "NEW_OCCURRENCE"), D,
                          a["verdade_humana"])
        check(r["linkage_correct"] is True and not r["false_split"],
              f"[{_n}] {rot}: §35 e o avaliador confirma")
        _n += 1
        continue
    # A resposta de PERTINÊNCIA é sempre ligar quando há irmão à mesa, mesmo
    # que o rótulo humano de novidade seja NEW_OCCURRENCE — são perguntas
    # diferentes, e o contrato só sabe expressar a primeira.
    r = fz.avaliar_v1(a["pacote"], saida(lab, "FOLLOW_UP"), D, a["verdade_humana"])
    check(r["linkage_correct"] is True and not r["false_merge"] and not r["false_split"],
          f"[{_n}] {rot}: §36 ligação correta é reconhecida")
    _n += 1
    r2 = fz.avaliar_v1(a["pacote"], saida(None, "NEW_OCCURRENCE"), D, a["verdade_humana"])
    check(r2["false_split"] is True,
          f"[{_n}] {rot}: §34 e dizer NOVA com irmão à mesa é FALSO SPLIT")
    _n += 1

_ha = alvo_de("Hapvida", 0, fold=0)
_hb = alvo_de("Hapvida", 0, fold=1)
_lab_a = rotulo_da_verdade(_ha)
_outro = next(c["candidate_label"] for c in
              _ha["pacote"]["prompt_payload"]["candidate_occurrences"]
              if c["candidate_label"] != _lab_a)
_fm = fz.avaliar_v1(_ha["pacote"], saida(_outro, "FOLLOW_UP"), D, _ha["verdade_humana"])
check(_fm["false_merge"] is True and _fm["high_impact_error"] is True,
      f"[{_n}] §33/§41 Hapvida: ligar à outra transição é FALSO MERGE, e é "
      "erro de alto impacto — fundir dois fatos reais apaga risco")
_n += 1
_ab = fz.avaliar_v1(_ha["pacote"], saida(None, "UNDETERMINED"), D, _ha["verdade_humana"])
check(_ab["estado"] == fz.ABSTENCAO and not _ab["false_merge"],
      f"[{_n}] §40 abstenção não conta como acerto nem como erro")
_n += 1
_falha = fz.avaliar_v1(_ha["pacote"], None, D, _ha["verdade_humana"])
check(_falha["estado"] == fz.SEM_AVALIACAO,
      f"[{_n}] §53 saída ausente é FALHA DE MODELO, não abstenção")
_n += 1
_mal = fz.avaliar_v1(_ha["pacote"], {"lixo": 1}, D, _ha["verdade_humana"])
check(_mal["estado"] == fz.SEM_AVALIACAO and _mal.get("parse_problems"),
      f"[{_n}] e saída malformada também, com os problemas listados")
_n += 1

print()
print("=" * 98)
print("§37/§38/§39 MÉTRICAS — O QUE O DATASET SUSTENTA")
print("=" * 98)
_dist = {}
for m in ot.memberships_ativas(D):
    _dist[m["occurrence_novelty"]] = _dist.get(m["occurrence_novelty"], 0) + 1
check(set(_dist) == {"NEW_OCCURRENCE", "FOLLOW_UP"},
      f"[{_n}] §65 só duas classes de novidade têm exemplo ({_dist})")
_n += 1
check(all(c not in _dist for c in ("HISTORICAL_CONTEXT", "DESCRIPTOR_OR_BACKGROUND",
                                   "UNDETERMINED")),
      f"[{_n}] três classes do enum estão sem um único exemplo — nenhuma "
      "métrica pode alegar desempenho nelas")
_n += 1
_anc = sum(1 for m in ot.memberships_ativas(D)
           if m["should_refresh_anchor"] is not None)
check(_anc == 2,
      f"[{_n}] §39 âncora tem {_anc} rótulos em 17 — NÃO é métrica de topo da V1")
_n += 1
_same = sum(max(0, len(ot.membros_de(D, k)) - 1) for k in ot.ocorrencias(D))
check(_same == 10 and len(ot.relacoes(D)) == 1,
      f"[{_n}] §64 supervisão SAME={_same} contra DISTINCT=1 — desequilíbrio "
      "10:1, e é por isso que a Hapvida entra em toda avaliação inicial")
_n += 1
_ag = fz.agregar([fz.avaliar_v1(_ha["pacote"], saida(_lab_a, "NEW_OCCURRENCE"), D,
                                _ha["verdade_humana"]),
                  _fm, _ab, _falha])
check(_ag["n_falhas_de_modelo"] >= 1 and _ag["n_abstencoes"] == 1
      and _ag["false_merge"] == 1,
      f"[{_n}] o agregador separa falha, abstenção e falso merge ({_ag['n_total']} casos)")
_n += 1
check("false_merge" in _ag and "false_split" in _ag
      and "acuracia" not in _ag and "accuracy" not in _ag,
      f"[{_n}] §32 e nunca soma os dois numa acurácia — são erros opostos")
_n += 1

print()
print("=" * 98)
print("§49/§50 PROMPT: ORDEM DETERMINÍSTICA E PEGADA")
print("=" * 98)
_p1 = fz.montar_prompt(_san_fold["exemplos_permitidos"],
                       _san_fold["alvos_elegiveis"][0]["pacote"], EX)
_p2 = fz.montar_prompt(_san_fold["exemplos_permitidos"],
                       _san_fold["alvos_elegiveis"][0]["pacote"], EX)
check(json.dumps(_p1, sort_keys=True) == json.dumps(_p2, sort_keys=True),
      f"[{_n}] §49 mesmo fold produz o mesmo prompt byte a byte")
_n += 1
check(list(_p1) == ["prompt_version", "instructions", "examples",
                    "example_outputs", "target"],
      f"[{_n}] e a ordem é fixa: instrução, exemplos, alvo")
_n += 1
_tam = []
for f in FOLDS:
    for a in f["alvos_elegiveis"]:
        _tam.append(len(json.dumps(fz.montar_prompt(f["exemplos_permitidos"],
                                                    a["pacote"], EX),
                                   ensure_ascii=False)))
_tam.sort()
import statistics
check(max(_tam) < 40000,
      f"[{_n}] §50 maior prompt ~{max(_tam) // 4} tokens, mediana ~"
      f"{int(statistics.median(_tam)) // 4} — contexto não é o gargalo")
_n += 1
check(fz.PROMPT_VERSION in _p1["prompt_version"],
      f"[{_n}] §62 e o prompt carrega a própria versão")
_n += 1
check("Gemini" not in fz.prompt_texto() and "Groq" not in fz.prompt_texto()
      and "OpenAI" not in fz.prompt_texto(),
      f"[{_n}] §51 nenhum nome de provider na instrução semântica")
_n += 1
for empresa in ("Santander", "Hapvida", "Tupy", "Yura", "Smart Fit", "BRF"):
    check(empresa not in fz.prompt_texto(),
          f"[{_n}] §15 nenhum nome de empresa na instrução genérica (`{empresa}`)")
    _n += 1
for termo in ("score", "crític", "peso", "dashboard"):
    check(termo not in fz.prompt_texto().lower(),
          f"[{_n}] §16 e nenhuma linguagem de score (`{termo}`)")
    _n += 1

print()
print("=" * 98)
print("§43/§67 SEM AUTORIDADE, SEM MODELO, SEM ESCRITA")
print("=" * 98)
_src = fz.__file__ and io.open("reliability_occurrence_auditor_freeze.py",
                               encoding="utf-8").read()
import re as _re
_COD = "\n".join(l.split("#")[0] for l in
                 _re.sub(r'"""(?:.|\n)*?"""', " ", _src).splitlines())
for prov in ("gemini", "groq", "openai", "requests.post", "urlopen"):
    check(prov not in _COD.lower(), f"[{_n}] §79-§81 nenhuma chamada a `{prov}`")
    _n += 1
check("gravar" not in _COD and "criar_ocorrencia" not in _COD,
      f"[{_n}] §87 nenhum caminho de escrita de verdade")
_n += 1
check("build_evolution" not in _COD and "assign_occurrence_clusters" not in _COD,
      f"[{_n}] §84-§86 e nenhuma leitura de score ou de agrupador")
_n += 1
check("occurrence_auditor_freeze" not in io.open("risk_dashboard.py",
                                                 encoding="utf-8").read(),
      f"[{_n}] §83 produção não importa o módulo")
_n += 1
check(MAN["dev_manifest_version"] and
      fz.manifesto_desenvolvimento(D)["prospective"] is False,
      f"[{_n}] §43 as 7 verdades são DESENVOLVIMENTO, nunca prospectivas")
_n += 1
_antes = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8")),
                    sort_keys=True)
fz.folds(D, historico=arq.HISTORICO); fz.manifesto(D, historico=arq.HISTORICO)
_depois = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8")),
                     sort_keys=True)
check(_antes == _depois, f"[{_n}] e rodar o arnês não altera o store")
_n += 1

print()
print("=" * 98)
print("§8/§10 O TESTE DO TESTE — MUTAÇÃO TEM DE FALHAR")
print("=" * 98)
# Nenhum arquivo real é tocado: as constantes do módulo são trocadas em memória
# e restauradas. Sem isto, a fixação de hash seria mais uma asserção que nunca
# falha — exatamente o defeito que ela veio corrigir.
_orig_prompt = fz.PROMPT_V1
try:
    fz.PROMPT_V1 = _orig_prompt + "\ninstrução extra"
    _mut = fz.manifesto(D, historico=arq.HISTORICO)
    check(_mut["prompt_hash"] != fz.HASHES_V1["prompt_hash"],
          f"[{_n}] §33 alterar UM byte do prompt muda `prompt_hash`")
    _n += 1
    check(_mut["freeze_manifest_hash"] != fz.HASHES_V1["freeze_manifest_hash"],
          f"[{_n}] e o `freeze_manifest_hash` muda junto — ele amarra o prompt")
    _n += 1
    check(len(fz.verificar_congelamento(D, historico=arq.HISTORICO)) >= 2,
          f"[{_n}] a verificação acusa a divergência")
    _n += 1
finally:
    fz.PROMPT_V1 = _orig_prompt
check(fz.verificar_congelamento(D, historico=arq.HISTORICO) == [],
      f"[{_n}] e volta a íntegro quando a mutação é desfeita")
_n += 1

_orig_ex = fz.DEFAULT_CURATED_SET
try:
    fz.DEFAULT_CURATED_SET = ("Santander Brasil", "Hapvida")
    _mut = fz.manifesto(D, historico=arq.HISTORICO)
    check(_mut["example_set_hash"] != fz.HASHES_V1["example_set_hash"]
          and _mut["freeze_manifest_hash"] != fz.HASHES_V1["freeze_manifest_hash"],
          f"[{_n}] trocar o conjunto de exemplos muda o hash dos exemplos E o "
          "do manifesto")
    _n += 1
finally:
    fz.DEFAULT_CURATED_SET = _orig_ex

_orig_src = io.open("reliability_occurrence_auditor_freeze.py", encoding="utf-8").read()
_falso = _orig_src.replace('r["high_impact_error"] = bool(r["false_merge"])',
                           'r["high_impact_error"] = False')
check(_falso != _orig_src, f"[{_n}] §34 (preparação) a lógica do avaliador é localizável")
_n += 1
import hashlib as _hl
_trecho_orig = _orig_src.split("def avaliar_v1")[1].split("def agregar")[0]
_trecho_mut = _falso.split("def avaliar_v1")[1].split("def agregar")[0]
check(_hl.sha256(_trecho_mut.encode()).hexdigest()[:16] != fz.HASHES_V1["evaluator_hash"],
      f"[{_n}] §34 e alterar a lógica do avaliador muda `evaluator_hash`")
_n += 1
check(_hl.sha256(_trecho_orig.encode()).hexdigest()[:16] == fz.HASHES_V1["evaluator_hash"],
      f"[{_n}] §35 enquanto o trecho real bate com o literal canônico")
_n += 1
check(fz.FREEZE_VERSION == "occurrence.auditor.freeze.v1"
      and fz.verificar_congelamento(D, historico=arq.HISTORICO) == [],
      f"[{_n}] §10 invariante em código: se a versão é `v1`, os hashes têm de "
      "ser estes. Mudança legítima exige versão nova.")
_n += 1

print()
print("=" * 98)
print(f"RESULTADO CONGELAMENTO DO AUDITOR: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
