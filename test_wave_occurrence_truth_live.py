#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_truth_live.py — as verdades gravadas, conferidas por semântica.

POR QUE ESTE ARQUIVO EXISTE

A suíte de esquema testa o mecanismo com fixtures. Esta testa o que foi de fato
adjudicado e persistido: sete ocorrências econômicas, dezessete pertinências e
a relação negativa da Hapvida.

Ela casa por CAMPO, nunca por id. Os `occurrence_truth_id` trazem sufixo opaco
emitido na escrita — fixá-los aqui quebraria o teste na primeira vez que
alguém recriasse uma verdade, e pior, ensinaria que o id é reproduzível. Ele
não é: é chave primária de uma decisão, e o que o teste precisa afirmar é a
decisão.

O QUE ELA PROTEGE

Que a verdade humana continue dizendo o que foi adjudicado, mesmo quando o
algoritmo mudar. Hoje o painel ainda mostra duas trocas de CEO no Santander —
e deve continuar mostrando, porque este store não tem autoridade nenhuma sobre
score, ocorrência ou âncora. Se um dia ele passar a ter, será por decisão
explícita, não porque alguém ligou um fio.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_truth as ot

PASS = FAIL = 0
D = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
OCC = ot.ocorrencias(D)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def por(empresa, evento):
    """Ocorrências de uma empresa/família, ordenadas pela data do fato.
    Nenhum id literal — é assim que o teste sobrevive a uma reescrita."""
    achadas = [o for o in OCC.values()
               if o["company"] == empresa and o["event_id"] == evento]
    return sorted(achadas, key=lambda o: (o["material_event_date"] or "",
                                          o["occurrence_truth_id"]))


def membros(o):
    return ot.membros_de(D, o["occurrence_truth_id"])


def fases(o):
    return {m["material_phase"] for m in membros(o)}


def novidades(o):
    return {m["occurrence_novelty"] for m in membros(o)}


print("=" * 98)
print("§32/§33 O STORE INTEIRO")
print("=" * 98)
check(not ot.validar(D), f"[1] validador sem problemas ({ot.validar(D)})")
check(len(OCC) == 7, f"[2] sete ocorrências econômicas ({len(OCC)})")
check(len(ot.memberships(D)) == 17 and len(ot.memberships_ativas(D)) == 17,
      f"[3] dezessete pertinências, todas ativas ({len(ot.memberships(D))})")
check(len(ot.relacoes(D)) == 1, f"[4] uma relação ({len(ot.relacoes(D))})")
check(len(set(OCC)) == len(OCC), "[5] nenhum id repetido")
_ids = {m["occurrence_truth_id"] for m in ot.memberships(D)}
check(_ids <= set(OCC), "[6] toda pertinência resolve para uma ocorrência existente")
check(all(ot.OCCURRENCE_TRUTH_SCHEMA_VERSION == o["schema_version"]
          for o in OCC.values()), "[7] todas na versão 1 do esquema")
check(all(o["adjudicator_type"] == "human" for o in OCC.values()),
      "[8] §8 adjudicadas por humano — nenhuma verdade nasce de modelo ou detector")

print()
print("=" * 98)
print("§9/§10 SANTANDER")
print("=" * 98)
_san = por("Santander Brasil", "troca_ceo")
check(len(_san) == 1, f"[9] UMA ocorrência ({len(_san)})")
_s = _san[0]
check(_s["material_event_date"] == "2026-03-19",
      f"[10] data do fato em março ({_s['material_event_date']})")
_fi = _s["family_identity"]
check(_fi.get("incoming_person") == "gilson finkelsztain"
      and _fi.get("outgoing_person") == "mario leao",
      f"[11] com os dois papéis ({_fi})")
check(len(membros(_s)) == 3, f"[12] os três artigos ({len(membros(_s))})")
_nov = [m["occurrence_novelty"] for m in
        sorted(membros(_s), key=lambda m: m["material_phase"])]
check(sorted(novidades(_s)) == ["FOLLOW_UP", "NEW_OCCURRENCE"],
      f"[13] um anúncio e dois acompanhamentos ({sorted(novidades(_s))})")
_ann = [m for m in membros(_s) if m["material_phase"] == "ANNOUNCEMENT"]
check(len(_ann) == 1 and _ann[0]["occurrence_novelty"] == "NEW_OCCURRENCE",
      "[14] o anúncio é a ocorrência nova")
_seg = [m for m in membros(_s) if m["occurrence_novelty"] == "FOLLOW_UP"]
check(len(_seg) == 2 and all(m["should_refresh_anchor"] is False for m in _seg),
      "[15] e os dois acompanhamentos gravam âncora FALSE — a análise da XP não "
      "renova a recência de um fato de março")
check(all(ot.should_create_occurrence(m["occurrence_novelty"]) is False
          for m in _seg), "[16] nem criam ocorrência (derivado)")

print()
print("=" * 98)
print("§12/§13 TUPY — o que NÃO foi adjudicado fica vazio")
print("=" * 98)
_tup = por("Tupy", "troca_ceo")
check(len(_tup) == 1, f"[17] UMA ocorrência ({len(_tup)})")
_t = _tup[0]
check(_t["material_event_date"] is None,
      "[18] data do fato VAZIA — ninguém adjudicou se o fato é a renúncia de "
      "março ou a escolha de maio, e inventar seria pior")
check(_t["family_identity"] == {"incoming_person": "harro burmann"},
      f"[19] só quem entra; o CEO que saiu não é nomeado em nenhum título ({_t['family_identity']})")
check(len(membros(_t)) == 4, f"[20] os quatro artigos ({len(membros(_t))})")
check(fases(_t) == {"ANNOUNCEMENT", "APPOINTMENT", "COMPLETION", "NONE"},
      f"[21] quatro fases distintas na MESMA ocorrência ({sorted(fases(_t))})")
check(all(m["should_refresh_anchor"] is None for m in membros(_t)),
      "[22] e âncora desconhecida nos quatro — fase material não é política de âncora")

print()
print("=" * 98)
print("§14/§15 YURA — papéis complementares")
print("=" * 98)
_yur = por("Yura", "troca_ceo")
check(len(_yur) == 1, f"[23] UMA ocorrência ({len(_yur)})")
_y = _yur[0]
check(_y["family_identity"].get("outgoing_person") == "juan carlos burga",
      f"[24] quem sai ({_y['family_identity'].get('outgoing_person')})")
check(_y["family_identity"].get("incoming_person") == "gonzalo rueda castillo",
      f"[25] quem entra ({_y['family_identity'].get('incoming_person')})")
check(len(membros(_y)) == 2, f"[26] os dois relatos ({len(membros(_y))})")
check(fases(_y) == {"ANNOUNCEMENT", "APPOINTMENT"},
      f"[27] anúncio da saída e nomeação do sucessor ({sorted(fases(_y))})")
check(len({m["occurrence_truth_id"] for m in membros(_y)}) == 1,
      "[28] e os dois apontam para a mesma ocorrência, embora não compartilhem "
      "uma palavra — é o papel que os liga, não o texto")

print()
print("=" * 98)
print("§16/§17 SMART FIT — fora de `troca_ceo`")
print("=" * 98)
_sf = por("Smart Fit", "ma")
check(len(_sf) == 1, f"[29] UMA ocorrência de M&A ({len(_sf)})")
_f = _sf[0]
check(_f["family_identity"] == {"target": "evolve"},
      f"[30] alvo Evolve, sem marcador genérico nem `_occ_key` ({_f['family_identity']})")
check(len(membros(_f)) == 3, f"[31] os três artigos ({len(membros(_f))})")
check(fases(_f) == {"ANNOUNCEMENT", "REGULATORY_APPROVAL", "CLOSING"},
      f"[32] anúncio, Cade e fechamento ({sorted(fases(_f))})")
check(all(m["should_refresh_anchor"] is None for m in membros(_f)
          if m["occurrence_novelty"] == "FOLLOW_UP"),
      "[33] âncora desconhecida nas fases posteriores — se uma aprovação "
      "regulatória renova recência é questão de política, ainda aberta")

print()
print("=" * 98)
print("§19/§20/§21 BRF — verdade parcial é verdade")
print("=" * 98)
_brf = por("BRF", "ma")
check(len(_brf) == 1, f"[34] UMA ocorrência ({len(_brf)})")
_b = _brf[0]
check(_b["family_identity"] == {"counterparties": ["marfrig"]},
      f"[35] contraparte Marfrig ({_b['family_identity']})")
check(_b["material_event_date"] is None,
      "[36] data do fato VAZIA: o anúncio da fusão é anterior ao corpus, e "
      "nenhuma onda adjudicou uma data")
check(len(membros(_b)) == 1,
      f"[37] §21 UMA pertinência — só o artigo adjudicado, não todos que o "
      f"agrupador atual junta ({len(membros(_b))})")
_m = membros(_b)[0]
check(_m["occurrence_novelty"] == "FOLLOW_UP",
      f"[38] a etapa societária é acompanhamento ({_m['occurrence_novelty']})")
check(_m["material_phase"] == "IMPLEMENTATION",
      f"[39] com fase de implementação ({_m['material_phase']})")
check(_m["should_refresh_anchor"] is None,
      "[40] e âncora desconhecida — a adjudicação disse 'não gera score novo', "
      "que é sobre criar ocorrência, não sobre renovar âncora")
check(ot.should_create_occurrence(_m["occurrence_novelty"]) is False,
      "[41] não cria segunda ocorrência de M&A (derivado)")

print()
print("=" * 98)
print("§22/§23 HAPVIDA — O NEGATIVO")
print("=" * 98)
_hap = por("Hapvida", "troca_ceo")
check(len(_hap) == 2, f"[42] DUAS ocorrências ({len(_hap)})")
check([o["material_event_date"] for o in _hap] == ["2025-12-22", "2026-04-06"],
      f"[43] dezembro e abril ({[o['material_event_date'] for o in _hap]})")
check(len(membros(_hap[0])) == 2 and len(membros(_hap[1])) == 2,
      "[44] duas pertinências em cada")
check(all("NEW_OCCURRENCE" in novidades(o) for o in _hap),
      "[45] e CADA UMA tem seu próprio anúncio — é isso que as torna distintas")
_ids_hap = {o["occurrence_truth_id"] for o in _hap}
_rel = ot.relacoes(D)[0]
check(_rel["relation"] == "DISTINCT_OCCURRENCE",
      f"[46] a relação registrada é DISTINCT ({_rel['relation']})")
check({_rel["occurrence_a"], _rel["occurrence_b"]} == _ids_hap,
      "[47] e liga exatamente as duas ocorrências da Hapvida")
check(_rel["occurrence_a"] != _rel["occurrence_b"], "[48] não é reflexiva")
check(len(_ids_hap) == 2,
      "[49] §14 sem este negativo, um conjunto só de duplicatas ensinaria a fundir")

print()
print("=" * 98)
print("§25/§26/§27 O QUE NÃO FOI ESCRITO")
print("=" * 98)
_emp = {o["company"] for o in OCC.values()}
for nome in ("Ambev", "B3", "Capital One Financial", "Petrobras"):
    check(nome not in _emp, f"[{50 + ('Ambev B3 Capital One Financial Petrobras'.split().index(nome.split()[0]))}] "
          f"{nome} não tem verdade gravada")
check(_emp == {"Santander Brasil", "Tupy", "Yura", "Smart Fit", "BRF", "Hapvida"},
      f"[54] exatamente as seis empresas autorizadas ({sorted(_emp)})")

print()
print("=" * 98)
print("§30/§31 O QUE JÁ EXISTIA CONTINUA COMO ESTAVA")
print("=" * 98)
_obs = D["observacoes"]
check(len(_obs) == 4, f"[55] as quatro observações seguem lá ({len(_obs)})")
_com_hr = [o for o in _obs.values() if o.get("human_review")]
check(len(_com_hr) == 4, "[56] todas com revisão humana")
_nov_legado = [(o.get("human_review") or {}).get("dimensoes_adjudicadas", {})
               .get("occurrence_novelty") for o in _obs.values()]
check(sorted(set(_nov_legado)) == ["FOLLOW_UP", "NEW_OCCURRENCE"],
      f"[57] com `occurrence_novelty` preservado ({sorted(set(_nov_legado))})")
_eneva = [o for o in _obs.values()
          if (o.get("human_review") or {}).get("artigo_anterior_mesma_transacao")]
check(len(_eneva) == 2,
      "[58] §31 e a ligação em texto livre da Eneva NÃO foi migrada — converter "
      "título em id exigiria casamento aproximado, que é como se inventa verdade")
check(all("occurrence_truth_id" not in json.dumps(o.get("human_review") or {})
          for o in _obs.values()),
      "[59] nenhuma revisão de artigo foi reescrita para apontar para o store novo")
check(sorted(D) == ["_meta", "observacoes", "occurrence_truth"],
      f"[60] o arquivo ganhou UM namespace e nada mais ({sorted(D)})")

print()
print("=" * 98)
print("§38/§39/§40 SEM AUTORIDADE")
print("=" * 98)
_src = io.open("reliability_occurrence_truth.py", encoding="utf-8").read()
_rd = io.open("risk_dashboard.py", encoding="utf-8").read()
check(ot.OCCURRENCE_TRUTH_NS not in _rd
      and "reliability_occurrence_truth" not in _rd,
      "[61] `risk_dashboard.py` não lê a verdade de ocorrência")
check(all(m["should_refresh_anchor"] is not None or True for m in ot.memberships(D))
      and "should_refresh_anchor" not in _rd,
      "[62] §40 e a âncora gravada do Santander é ignorada por produção — o "
      "campo é supervisão para uma política futura, não comportamento de hoje")
check("occurrence_truth" not in io.open("semantic_audit.py", encoding="utf-8").read(),
      "[63] `semantic_audit.py` também não")

print()
print("=" * 98)
print(f"RESULTADO VERDADE DE OCORRÊNCIA (VIVA): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
