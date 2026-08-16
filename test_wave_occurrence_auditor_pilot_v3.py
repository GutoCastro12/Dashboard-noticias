#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_pilot_v3.py — o arnês, exercitado sem provider.

Nenhum teste aqui chama a rede. Todos usam respostas falsas construídas à mão,
porque o que precisa ser provado ANTES de gastar cota é que o arnês mede o que
diz medir — e o histórico deste experimento é de arnês defeituoso, não de
modelo ruim.

O teste que mais importa é o do preditor constante: alimenta-se o executor com
exatamente o comportamento que os modelos tiveram na V2 (`CANDIDATE_1` em toda
resposta) e exige-se que ele REPROVE. Sob a métrica da V2 esse comportamento
marcava 82% e passava por competência.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from collections import Counter

import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_auditor_pilot_v3 as p3

PASS = FAIL = 0
D = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
BL = v3.baselines_triviais(D)
TMP = tempfile.mkdtemp(prefix="v3pilot")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def resposta(lk, nov="FOLLOW_UP"):
    return {"estado": "OK", "modelo": "mock", "uso": {}, "finish": "STOP",
            "saida": {"linked_candidate": lk, "occurrence_novelty": nov,
                      "material_phase_assessment": "ANNOUNCEMENT",
                      "should_refresh_anchor_assessment": "UNKNOWN",
                      "confidence": "HIGH",
                      "evidence": [{"quote": "x", "origin": "TARGET_TITLE"}]}}


def roda(fn, nome):
    j = os.path.join(TMP, f"{nome}.jsonl")
    p3.PACING_S = 0.0
    tel = p3.executar(D, chamada=fn, saida_jsonl=j)
    return tel, p3.pontuar(D, j), j


ALVOS = p3.alvos_congelados(D)
VERD = {a["target_id"]: a for a in ALVOS}
n = 1

print("=" * 98)
print("§21/§22 TETO, SEM RETRY, SEM FALLBACK")
print("=" * 98)
_tel, _sc, _j = roda(lambda m, t: resposta("CANDIDATE_1"), "const")
check(_tel["planned_calls"] == 34 and _tel["attempted_calls"] == 34,
      f"[{n}] 34 planejadas, 34 tentadas ({_tel['attempted_calls']})"); n += 1
check(p3.MAX_CHAMADAS == 34 and _tel["config"]["retry"] == 0
      and _tel["config"]["fallback"] == 0,
      f"[{n}] teto 34, retry 0, fallback 0"); n += 1
_L = [json.loads(l) for l in io.open(_j, encoding="utf-8")]
_p = Counter((r["model"], r["target_id"]) for r in _L)
check(len(_p) == 34 and max(_p.values()) == 1,
      f"[{n}] uma primeira resposta por par modelo×alvo, nenhuma repetida"); n += 1
check(all(r["prompt_hash"] == v3.HASHES_V3["prompt_hash"]
          and r["freeze_manifest_hash"] == v3.HASHES_V3["freeze_manifest_hash"]
          for r in _L),
      f"[{n}] cada registro carrega o congelamento V3 que o produziu"); n += 1
check(set(r["model"] for r in _L) == set(p3.MODELOS),
      f"[{n}] só os dois modelos declarados"); n += 1

print()
print("=" * 98)
print("O PREDITOR CONSTANTE DA V2 REPROVA AQUI")
print("=" * 98)
for m, ag in _sc["por_modelo"].items():
    check(ag["linkage_correct"] == 14,
          f"[{n}] {m}: sempre CANDIDATE_1 acerta {ag['linkage_correct']}/17 — "
          "o mesmo 82,4% que a V2 celebrou"); n += 1
    check(ag["development_sane"] is False and ag["beats_trivial"] is False
          and ag["non_collapse"] is False,
          f"[{n}] {m}: REPROVA — não supera o trivial ({ag['linkage_correct']} "
          f"< {BL['_required_to_beat']}) e prediz uma classe só "
          f"{ag['classes_preditas']}"); n += 1

print()
print("=" * 98)
print("UM RESPONDEDOR PERFEITO PASSA — O PORTÃO É EXIGENTE, NÃO IMPOSSÍVEL")
print("=" * 98)


def _titulo(a):
    return a["pacote"]["prompt_payload"]["target_article"]["title"]


_POR_TITULO = {a["pacote"]["prompt_payload"]["target_article"]["title"]: a
               for a in ALVOS}


def _acha(texto):
    """Lê o título DO CAMPO `target_article` do JSON do alvo.

    Três armadilhas, todas encontradas medindo. O `article_ref` é verdade
    interna e nunca aparece no payload, então casar por ele derruba o mock no
    fallback em silêncio. Casar o título contra o prompt inteiro encontra o
    artigo no bloco de exemplos, que sai do mesmo devset. E varrer a seção do
    alvo procurando "algum título conhecido" acha o do artigo IRMÃO, porque os
    candidatos exibem títulos de outros alvos — 11 dos 17 saíam errados assim.

    As três variantes davam 14 ou 15 de 17, perto o bastante do esperado para
    passarem por limite do conjunto em vez de defeito do mock."""
    corpo = texto.split("\nALVO\n")[-1]
    return _POR_TITULO.get(json.loads(corpo)["target_article"]["title"])


def _chamada_perfeita(m, texto):
    a = _acha(texto)
    assert a is not None, "mock não localizou o alvo no prompt"
    # Prefere um rótulo aceitável que NÃO seja candidato misto: escolher o
    # misto não é acerto limpo, e um respondedor perfeito não escolheria.
    limpos = [r for r in a["linkage_aceitaveis"]
              if r not in a["candidatos_mistos"]]
    return resposta(sorted(limpos or a["linkage_aceitaveis"])[0],
                    a["novelty_verdade"])


_tel3, _sc3, _ = roda(_chamada_perfeita, "perfeito")
for m, ag in _sc3["por_modelo"].items():
    check(ag["linkage_correct"] == 17 and ag["development_sane"] is True,
          f"[{n}] {m}: linkage {ag['linkage_correct']}/17, minoria "
          f"{ag['minority_correct']}/{ag['minority_n']}, classes "
          f"{ag['classes_preditas']} → DEVELOPMENT_SANE"); n += 1
    check(ag["novelty_correct"] == 17 and ag["novelty_denominador"] == 17,
          f"[{n}] {m}: novidade {ag['novelty_correct']}/17 — denominador 17, "
          "não 11"); n += 1

print()
print("=" * 98)
print("PERTINÊNCIA E NOVIDADE SÃO MEDIDAS SEPARADAMENTE")
print("=" * 98)


def _lk_ok_nov_errada(m, texto):
    a = _acha(texto)
    assert a is not None, "mock não localizou o alvo no prompt"
    outra = ("FOLLOW_UP" if a["novelty_verdade"] == "NEW_OCCURRENCE"
             else "NEW_OCCURRENCE")
    limpos = [r for r in a["linkage_aceitaveis"]
              if r not in a["candidatos_mistos"]]
    return resposta(sorted(limpos or a["linkage_aceitaveis"])[0], outra)


_t4, _sc4, _ = roda(_lk_ok_nov_errada, "mista")
_ag4 = list(_sc4["por_modelo"].values())[0]
check(_ag4["linkage_correct"] == 17 and _ag4["novelty_correct"] == 0,
      f"[{n}] pertinência perfeita com novidade toda errada: "
      f"{_ag4['linkage_correct']}/17 e {_ag4['novelty_correct']}/17 — os eixos "
      "não se contaminam"); n += 1
check(_ag4["development_sane"] is True,
      f"[{n}] §45 e a sanidade é decidida pela PERTINÊNCIA, que é a pergunta "
      "primária"); n += 1

print()
print("=" * 98)
print("VÁRIOS RÓTULOS ACEITÁVEIS SÃO TRATADOS COMO TAIS")
print("=" * 98)
_part = [a for a in ALVOS if a["ocorrencia_partida"]]
check(len(_part) == 5, f"[{n}] 5 alvos com ocorrência partida ({len(_part)})"); n += 1
_a = _part[0]
check(all(v3.avaliar_v3(_a, resposta(r)["saida"])["linkage_correct"]
          for r in _a["linkage_aceitaveis"]),
      f"[{n}] §29/§37 os dois fragmentos do mesmo fato contam como acerto "
      f"({_a['linkage_aceitaveis']}) — o erro é do agrupamento, não de quem "
      "responde"); n += 1

print()
print("=" * 98)
print("FALSO MERGE, FALSO SPLIT E `NO_CANDIDATE` CORRETO")
print("=" * 98)
_brf = next(a for a in ALVOS if a["company"] == "BRF")
check(v3.avaliar_v3(_brf, resposta(v3.SEM_CANDIDATO)["saida"])["linkage_correct"],
      f"[{n}] §31/§40 BRF: `NO_CANDIDATE` é a resposta CERTA — é o controle "
      "real de não-ligação"); n += 1
check(v3.avaliar_v3(_brf, resposta("CANDIDATE_1")["saida"])["false_merge"],
      f"[{n}] e ligar a um candidato ali é FALSO MERGE"); n += 1
_hap = next(a for a in ALVOS if a["company"] == "Hapvida"
            and a["linkage_aceitaveis"] == ["CANDIDATE_2"])
check(v3.avaliar_v3(_hap, resposta("CANDIDATE_1")["saida"])["false_merge"],
      f"[{n}] §36 Hapvida: escolher a outra transição é FALSO MERGE"); n += 1
check(v3.avaliar_v3(_hap, resposta(v3.SEM_CANDIDATO)["saida"])["false_split"],
      f"[{n}] e negar o candidato correto é FALSO SPLIT"); n += 1
_ag5 = v3.agregar_v3([v3.avaliar_v3(a, resposta("CANDIDATE_1")["saida"])
                      for a in ALVOS], BL)
check(_ag5["hapvida_false_merge"] == 2 and _ag5["development_sane"] is False,
      f"[{n}] o preditor constante falso-mergeia a Hapvida "
      f"{_ag5['hapvida_false_merge']}× e reprova por isso também"); n += 1

print()
print("=" * 98)
print("ABSTENÇÃO E FALHA DE PROVIDER SÃO COISAS DIFERENTES")
print("=" * 98)
_r = v3.avaliar_v3(ALVOS[0], resposta(v3.INDETERMINADO)["saida"])
check(_r["abstencao"] and _r["erro"] == v3.ABSTENCAO
      and not _r["false_merge"],
      f"[{n}] `UNDETERMINED` é abstenção, não erro de pertinência"); n += 1
_t6, _sc6, _ = roda(lambda m, t: {"estado": "ERRO", "saida": None,
                                  "erro": {"classe": "TIMEOUT"}}, "falha")
_ag6 = list(_sc6["por_modelo"].values())[0]
check(_ag6["n_falhas_de_parse"] == 17 and _ag6["parse_success"] == 0.0,
      f"[{n}] falha de provider conta como falha, não como resposta errada"); n += 1
check(all(v["breaker_tripped"] is not None
          for v in _t6["por_modelo"].values()),
      f"[{n}] §22 e o disjuntor dispara por falha de infraestrutura"); n += 1

print()
print("=" * 98)
print("§19 CONFIG EFETIVA E PROCEDÊNCIA")
print("=" * 98)
_c = p3.config_efetiva()
check(_c["models"]["G1"] == "gemini-3.1-flash-lite"
      and _c["models"]["G2"] == "gemini-3.5-flash-lite",
      f"[{n}] modelos exatos, sem substituição"); n += 1
check(_c["temperature"] == 0.0 and _c["retry"] == 0 and _c["fallback"] == 0,
      f"[{n}] temperatura 0, retry 0, fallback 0"); n += 1
check(_c["parser_version"] == v3.OUTPUT_CONTRACT
      and _c["evaluator_version"] == v3.EVALUATOR_VERSION,
      f"[{n}] parser e avaliador V3 declarados na config"); n += 1
check(_tel["dataset_role"] == "DEVELOPMENT"
      and _tel["production_authority"] == "NONE",
      f"[{n}] desenvolvimento, sem autoridade de produção"); n += 1
_src = io.open("reliability_occurrence_auditor_pilot_v3.py",
               encoding="utf-8").read()
check("groq" not in _src.lower(),
      f"[{n}] nenhuma menção a Groq no executor"); n += 1
check("_uma_chamada" in _src and _src.count("def _chamada_real") == 1,
      f"[{n}] uma única porta de chamada, delegando à integração já existente "
      "— para que diferença de resultado não venha do transporte"); n += 1

print()
print("=" * 98)
print("§20 ORDEM DETERMINÍSTICA")
print("=" * 98)
check([a["target_id"] for a in ALVOS] == sorted(a["target_id"] for a in ALVOS),
      f"[{n}] alvos em ordem de target_id"); n += 1
check([r["model"] for r in _L[:2]] == [p3.G1, p3.G2],
      f"[{n}] G1 antes de G2 em cada alvo, sem reordenar por desempenho "
      "anterior"); n += 1

print()
print("=" * 98)
print(f"RESULTADO ARNÊS V3: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
