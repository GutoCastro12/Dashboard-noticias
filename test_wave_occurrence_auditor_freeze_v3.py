#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_freeze_v3.py — o instrumento antes da medição.

Duas vezes seguidas o benchmark mediu o próprio defeito em vez de medir os
modelos. A V1 rejeitava como contradição uma combinação que é verdade humana
registrada; a V2 consertou o exemplo em vez da regra e induziu preditor
constante. Esta suíte existe para que a terceira tentativa não repita nenhum
dos dois.

Ela afirma quatro coisas que, se falsas, invalidam qualquer resultado futuro:

1. Pertinência e novidade são independentes. `CANDIDATE_n` + `NEW_OCCURRENCE`
   passa no validador, porque em modo pós-build é verdade — e a suíte prova
   que o alvo `54defbfc` da Hapvida tem exatamente essa verdade humana.

2. Nenhum fold LOOCV fica sem contraste. O colapso da V2 não foi só um exemplo
   ruim: foi um conjunto sem variação DEPOIS das exclusões. Verificar o
   conjunto global e não cada fold deixaria isso passar de novo.

3. O arnês não privilegia `CANDIDATE_1`. Testes metamórficos permutam a ordem
   dos candidatos e exigem que a verdade acompanhe o conteúdo, não o rótulo.

4. Os baselines triviais estão congelados e o portão exige superá-los.
   `ALWAYS_CANDIDATE_1` acerta 14/17; um modelo que empate com ele não passa.

Os hashes são literais escritos à mão. `esperado = calcular()` passa por
construção depois de qualquer edição e não prova nada.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_archival_source as arq
import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3
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
# A entrada de ARTIGOS dos experimentos congelados e o snapshot historico,
# nao o acervo vivo: `risk_history.json` cresce e recebe correcoes legitimas
# de producao (foi o reparo da data da BRF que expos isso). Ver `4cda805`.
H_ARQ = _av.SNAPSHOT_HISTORICO
EX = v3.exemplos_congelados(D, historico=H_ARQ)
AL = v3.alvos_com_verdade(D, historico=arq.HISTORICO)
BL = v3.baselines_triviais(D, historico=H_ARQ)

ESPERADO_V3 = {
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


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def base(lk="CANDIDATE_1", nov="FOLLOW_UP"):
    return {"linked_candidate": lk, "occurrence_novelty": nov,
            "material_phase_assessment": "ANNOUNCEMENT",
            "should_refresh_anchor_assessment": "UNKNOWN", "confidence": "HIGH",
            "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}


ROT = ["CANDIDATE_1", "CANDIDATE_2"]
n = 1
print("=" * 98)
print("§1/§2/§11 PERTINÊNCIA E NOVIDADE SÃO PERGUNTAS INDEPENDENTES")
print("=" * 98)
for lk in ("CANDIDATE_1", "CANDIDATE_2", v3.SEM_CANDIDATO):
    for nov in ("NEW_OCCURRENCE", "FOLLOW_UP"):
        p = v3.validar_saida(base(lk, nov), ROT)
        check(not p, f"[{n}] `{lk}` + `{nov}` é ACEITO ({p or 'sem problema'})")
        n += 1
check(v3.validar_saida(base("CANDIDATE_1", "HISTORICAL_CONTEXT"), ROT) == [],
      f"[{n}] e as demais classes de novidade do Contract V2 também"); n += 1
check(v1.validar_saida({"selected_candidate": "CANDIDATE_2",
                        "occurrence_novelty": "NEW_OCCURRENCE",
                        "material_phase_assessment": "ANNOUNCEMENT",
                        "should_refresh_anchor_assessment": "UNKNOWN",
                        "confidence": "HIGH",
                        "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]},
                       ROT) == ["CONTRADICAO_NOVA_COM_CANDIDATO"],
      f"[{n}] a V1 REJEITAVA essa mesma combinação — é a regra falsa que a V3 "
      "remove, e a V1 fica como está para o registro histórico"); n += 1
_hap = [a for a in AL if a["company"] == "Hapvida"
        and a["article_ref"].startswith("54defbfc")]
check(_hap and _hap[0]["novelty_verdade"] == "NEW_OCCURRENCE"
      and "CANDIDATE_2" in _hap[0]["linkage_aceitaveis"],
      f"[{n}] §18/§49 e a combinação é VERDADE HUMANA registrada: Hapvida "
      f"54defbfc tem pertinência {_hap[0]['linkage_aceitaveis'] if _hap else '?'} "
      "e novidade NEW_OCCURRENCE — o exemplo da V1 estava certo, o validador "
      "é que estava errado"); n += 1

print()
print("=" * 98)
print("§3/§17 TODOS OS 17 SÃO EXPRIMÍVEIS AGORA")
print("=" * 98)
check(len(AL) == 17, f"[{n}] 17 alvos ({len(AL)})"); n += 1
check(all(a["linkage_aceitaveis"] for a in AL),
      f"[{n}] pertinência exprimível em 17/17"); n += 1
check(all(a["novelty_verdade"] in v3.OUT_NOVELTY for a in AL),
      f"[{n}] novidade exprimível em 17/17 — a categoria "
      "`novelty_inexpressivel` cai de 6 para 0, porque era consequência da "
      "regra falsa e não do dado"); n += 1
_nov = {}
for a in AL:
    _nov[a["novelty_verdade"]] = _nov.get(a["novelty_verdade"], 0) + 1
check(_nov == {"FOLLOW_UP": 11, "NEW_OCCURRENCE": 6},
      f"[{n}] distribuição de novidade {_nov} — três classes do Contract V2 "
      "seguem SEM exemplo, e nada será afirmado sobre elas"); n += 1

print()
print("=" * 98)
print("A VERDADE DE PERTINÊNCIA É UM CONJUNTO, NÃO UM RÓTULO")
print("=" * 98)
_part = [a for a in AL if a["ocorrencia_partida"]]
check(len(_part) == 5,
      f"[{n}] em 5 alvos a ocorrência humana está PARTIDA entre dois "
      f"candidatos ({len(_part)}) — Santander e Tupy, as duplicatas que o "
      "detector de CEO já sinaliza"); n += 1
check(sorted({a["company"] for a in _part}) == ["Santander Brasil", "Tupy"],
      f"[{n}] e são exatamente essas duas empresas"); n += 1
_a = _part[0]
check(all(v3.avaliar_v3(_a, base(r))["linkage_correct"]
          for r in _a["linkage_aceitaveis"]),
      f"[{n}] qualquer um dos rótulos aceitáveis conta como acerto — punir a "
      "escolha do 'outro pedaço do mesmo fato' mediria ruído nosso"); n += 1

print()
print("=" * 98)
print("§19/§22 CONTRASTE NO CONJUNTO DE EXEMPLOS")
print("=" * 98)
check(len(EX) == 4 and set(EX) == set(v3.DEFAULT_CURATED_SET),
      f"[{n}] quatro empresas, não três — com três, a exclusão LOOCV podia "
      "zerar o contraste"); n += 1
for emp, e in sorted(EX.items()):
    p = v3.validar_saida(e["expected_output"], e["candidate_labels"])
    check(not p, f"[{n}] exemplo {emp}: válido no próprio parser "
                 f"({p or 'sem problema'})"); n += 1
_d = v3.diversidade(EX)
check(len(_d["linkage_labels"]) >= 2 and len(_d["novelty_classes"]) >= 2,
      f"[{n}] contraste global: pertinência {_d['linkage_labels']} · novidade "
      f"{_d['novelty_classes']}"); n += 1
check(v3.SEM_CANDIDATO in _d["linkage_labels"],
      f"[{n}] §21 e `NO_CANDIDATE` é ensinado por um caso REAL adjudicado "
      "(BRF), não por controle sintético"); n += 1
check(all(e["evaluation_metadata"]["provenance"] == "HUMAN_ADJUDICATED"
          for e in EX.values())
      and v3.manifesto(D, historico=H_ARQ)["synthetic_controls"] == [],
      f"[{n}] nenhum exemplo é sintético, e nada sintético é contado como "
      "verdade humana"); n += 1

print()
print("=" * 98)
print("§34 CONTRASTE DEPOIS DAS EXCLUSÕES LOOCV — FOLD A FOLD")
print("=" * 98)
_colapso = []
for f in v3.folds(D, historico=H_ARQ):
    perm = {k: EX[k] for k in v3.exemplos_do_fold(f["company"]) if k in EX}
    d = v3.diversidade(perm)
    if not d["ok"]:
        _colapso.append(f["company"])
    check(d["ok"], f"[{n}] fold {f['company']}: n={len(perm)} "
                   f"lk={d['linkage_labels']} nov={d['novelty_classes']}")
    n += 1
check(not _colapso,
      f"[{n}] nenhum fold colapsa ({_colapso or 'zero'}) — foi assim que a V2 "
      "quebrou, e verificar só o conjunto global não teria detectado"); n += 1

print()
print("=" * 98)
print("§33 VAZAMENTO")
print("=" * 98)
_ids = set(ot.ocorrencias(D))
_vaz = _same = _self = 0
for a in AL:
    pr = v3.montar_prompt(a["exemplos_permitidos"], a["pacote"], EX)
    t = json.dumps(pr["target"], ensure_ascii=False)
    e = json.dumps(pr["examples"], ensure_ascii=False)
    if ai.vazamentos({"prompt_payload": pr["target"]}):
        _vaz += 1
    if any(i in t for i in _ids):
        _vaz += 1
    if a["company"] in e:
        _same += 1
    if a["article_ref"] in e:
        _self += 1
check(_vaz == 0, f"[{n}] verdade humana do alvo no payload: 0 ({_vaz})"); n += 1
check(_same == 0, f"[{n}] exemplo da mesma empresa: 0 ({_same})"); n += 1
check(_self == 0, f"[{n}] alvo como próprio exemplo: 0 ({_self})"); n += 1

print()
print("=" * 98)
print("§36 O ARNÊS NÃO PRIVILEGIA `CANDIDATE_1` — TESTES METAMÓRFICOS")
print("=" * 98)
_alvo = next(a for a in AL if len(a["pacote"]["prompt_payload"]
                                  ["candidate_occurrences"]) > 1
             and not a["ocorrencia_partida"])
_orig = sorted(_alvo["linkage_aceitaveis"])[0]
_perm = json.loads(json.dumps(_alvo["pacote"]))
_cs = _perm["prompt_payload"]["candidate_occurrences"]
_cs.reverse()
for i, c in enumerate(_cs, 1):
    c["candidate_label"] = f"CANDIDATE_{i}"
_ver = v3._mapa_verdade(D)
_novo, _mist, _pt = v3.verdade_de_pertinencia(
    _perm, _alvo["verdade_humana"]["occurrence_truth_id"], _ver)
check(sorted(_novo)[0] != _orig,
      f"[{n}] invertida a ordem dos candidatos, o rótulo correto MUDA "
      f"({_orig} → {sorted(_novo)[0]}) — a verdade segue o conteúdo"); n += 1
_a2 = dict(_alvo, pacote=_perm, linkage_aceitaveis=_novo,
           candidatos_mistos=_mist, ocorrencia_partida=_pt)
check(v3.avaliar_v3(_a2, base(sorted(_novo)[0]))["linkage_correct"]
      and not v3.avaliar_v3(_a2, base(_orig))["linkage_correct"],
      f"[{n}] e o avaliador acompanha: acerta o novo rótulo, erra o antigo"); n += 1
check(v3.avaliar_v3(_a2, base(sorted(_novo)[0], "NEW_OCCURRENCE"))["novelty_correct"]
      == (_alvo["novelty_verdade"] == "NEW_OCCURRENCE"),
      f"[{n}] a novidade NÃO muda com a permutação — é papel histórico, não "
      "posição na lista"); n += 1
check(v3.manifesto(D, historico=H_ARQ)["candidate_order_rule"]
      == "chronological_by_first_date_oldest_is_1",
      f"[{n}] §38 e a regra de ordenação está documentada: cronológica, mais "
      "antigo é CANDIDATE_1"); n += 1

print()
print("=" * 98)
print("§24/§25/§26/§27 BASELINES TRIVIAIS E O PORTÃO QUE EXIGE SUPERÁ-LOS")
print("=" * 98)
check(BL["ALWAYS_CANDIDATE_1"]["correct"] == 14,
      f"[{n}] ALWAYS_CANDIDATE_1 acerta {BL['ALWAYS_CANDIDATE_1']['correct']}/17 "
      "= 82,4% — um preditor constante marcava isso na V2 e parecia "
      "competente"); n += 1
check(BL["_strongest"] == "ALWAYS_CANDIDATE_1"
      and BL["_required_to_beat"] == 15,
      f"[{n}] o baseline mais forte é esse, e o portão exige "
      f"{BL['_required_to_beat']}/17 para superá-lo"); n += 1
check(BL["_minority_n"] == 3,
      f"[{n}] §28 alvos de classe minoritária (onde o melhor trivial erra): "
      f"{BL['_minority_n']}"); n += 1
_res = [v3.avaliar_v3(a, base("CANDIDATE_1")) for a in AL]
_ag = v3.agregar_v3(_res, BL)
check(_ag["development_sane"] is False and _ag["non_collapse"] is False,
      f"[{n}] §27 simulando o preditor constante da V2: linkage "
      f"{_ag['linkage_correct']}/17 mas DEVELOPMENT_SANE=False, porque não "
      "supera o trivial e não prediz mais de uma classe"); n += 1
check(_ag["beats_trivial"] is False,
      f"[{n}] e `beats_trivial` é explicitamente falso"); n += 1
_res2 = [v3.avaliar_v3(a, base(sorted(a["linkage_aceitaveis"])[0],
                               a["novelty_verdade"])) for a in AL]
_ag2 = v3.agregar_v3(_res2, BL)
check(_ag2["linkage_correct"] == 17 and _ag2["development_sane"] is True,
      f"[{n}] e um respondedor perfeito PASSA (linkage "
      f"{_ag2['linkage_correct']}/17) — o portão é exigente, não impossível"); n += 1
check(_ag2["novelty_correct"] == 17 and _ag2["novelty_denominador"] == 17,
      f"[{n}] §30 denominador de novidade é 17, não 11"); n += 1
check(v3.manifesto(D, historico=H_ARQ)["sanity_gate"]["linkage_correct_min"] == 15,
      f"[{n}] e o portão está no manifesto congelado"); n += 1

print()
print("=" * 98)
print("§6 V1 E V2 SEGUEM INTACTAS")
print("=" * 98)
check(not v1.verificar_congelamento(D, historico=H_ARQ), f"[{n}] pins da V1 exatos"); n += 1
check(not v2.verificar_congelamento(D, historico=H_ARQ), f"[{n}] pins da V2 exatos"); n += 1
check(v3.manifesto(D, historico=H_ARQ)["input_hash"] == v1.HASHES_V1["input_hash"]
      == v2.HASHES_V2["input_hash"],
      f"[{n}] §44 a entrada NÃO mudou — mesmo contrato, mesmo hash, nenhum "
      "drift semântico gratuito"); n += 1
check(v3.manifesto(D, historico=H_ARQ)["dev_manifest_hash"] == v1.HASHES_V1["dev_manifest_hash"],
      f"[{n}] §45 e o devset é a MESMA população: 7 verdades, 17 "
      "pertinências, sem Capital One nem Petrobras"); n += 1
check(list(v3.OUT_NOVELTY) == list(v2.OUT_NOVELTY) == list(v1.OUT_NOVELTY),
      f"[{n}] o Contract V2 não foi tocado"); n += 1

print()
print("=" * 98)
print("§46/§47 HASHES FIXADOS À MÃO E A FIXAÇÃO DETECTA MUTAÇÃO")
print("=" * 98)
check(dict(v3.HASHES_V3) == ESPERADO_V3,
      f"[{n}] os 9 hashes batem com literais desta suíte"); n += 1
check(not v3.verificar_congelamento(D, historico=H_ARQ),
      f"[{n}] e o módulo concorda consigo mesmo"); n += 1
_o = dict(v3.HASHES_V3)
for alvo in ("prompt_hash", "example_outputs_hash", "evaluator_hash",
             "baseline_hash", "output_hash"):
    try:
        v3.HASHES_V3[alvo] = "0" * 16
        _div = v3.verificar_congelamento(D, historico=H_ARQ)
        check(any(k == alvo for k, _, _ in _div),
              f"[{n}] mutar `{alvo}` REPROVA a verificação")
        n += 1
    finally:
        v3.HASHES_V3.clear()
        v3.HASHES_V3.update(_o)
check(not v3.verificar_congelamento(D, historico=H_ARQ),
      f"[{n}] e tudo volta a passar depois de restaurado"); n += 1

print()
print("=" * 98)
print(f"RESULTADO CONGELAMENTO V3: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
