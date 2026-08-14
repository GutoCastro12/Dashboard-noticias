#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_contract_v2.py — Contract V2: expressividade, compatibilidade e régua.

O QUE ESTE TESTE PROTEGE

1. Que o V1 continue existindo intacto. As medições anteriores só permanecem
   reproduzíveis se o contrato que as produziu não for reescrito por baixo.

2. Que `currentness` NÃO tenha sido redefinida. Ela continua respondendo
   quando o FATO ocorreu — nada mais. A segunda pergunta ganhou dimensão
   própria justamente para que nenhuma das duas fique ambígua.

3. Que o V2 seja EXPRESSIVO o bastante para as adjudicações já registradas —
   participação acionária é M&A legítimo; fazenda, bloco exploratório e ativo
   não são. Estes são testes de EXPRESSIVIDADE do vocabulário, não regras por
   empresa: nenhum nome entra no prompt nem na projeção.

4. Que o prompt V2 não vaze nome de controle nem verdade humana.

NENHUMA CHAMADA A PROVIDER.
"""
from __future__ import annotations

import inspect
import io
import re

import gemini_schema_adapter as ga
import bench_semantic_eval as ev
import reliability_pilot_contract as v1
import reliability_pilot_contract_v2 as v2

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def E(**kw):
    base = {"event_id": "ma", "event_asserted": "ASSERTED", "subject": "Acme",
            "company_role": "SUBJECT", "currentness": "CURRENT",
            "phase": "CONCLUDED", "centrality": "MAIN", "related_entity": None,
            "occurrence_novelty": "NEW_OCCURRENCE",
            "transaction_object": "COMPANY_CONTROL"}
    base.update(kw)
    return base


print("=" * 98)
print("BLOCO A — V1 PRESERVADO")
print("=" * 98)
check(v1.SCHEMA_VERSION == "r7ba.s1" and v1.PROMPT_VERSION == "r7ba.p1",
      f"[1] V1 mantém suas versões ({v1.SCHEMA_VERSION}/{v1.PROMPT_VERSION})")
check(v2.SCHEMA_VERSION == "r7ba.s2" and v2.PROMPT_VERSION == "r7ba.p2",
      f"[2] V2 é versionado à parte ({v2.SCHEMA_VERSION}/{v2.PROMPT_VERSION})")
_v1req = v1.SCHEMA_AUDIT["properties"]["events"]["items"]["required"]
check("occurrence_novelty" not in _v1req and "transaction_object" not in _v1req,
      "[3] o schema V1 NÃO foi contaminado pelas dimensões novas")
_v1props = v1.SCHEMA_AUDIT["properties"]["events"]["items"]["properties"]
check("occurrence_novelty" not in _v1props,
      "[4] nem as propriedades do V1 foram tocadas")
check(v2.CURRENTNESS_INALTERADA if hasattr(v2, "CURRENTNESS_INALTERADA")
      else v1.CURRENTNESS == ("CURRENT", "HISTORICAL", "UNDATABLE",
                              "CONFLICTING"),
      "[5] `currentness` continua com o mesmo enum — não foi redefinida")
check(v2.descrever()["currentness_inalterada"] == list(v1.CURRENTNESS),
      "[6] e o V2 declara isso explicitamente")

print()
print("=" * 98)
print("BLOCO B — AS DUAS DIMENSÕES NOVAS")
print("=" * 98)
_it = v2.SCHEMA_AUDIT["properties"]["events"]["items"]
check("occurrence_novelty" in _it["properties"]
      and "transaction_object" in _it["properties"],
      "[7] o schema V2 declara as duas dimensões")
check("occurrence_novelty" in _it["required"]
      and "transaction_object" in _it["required"],
      "[8] e ambas são obrigatórias — não podem ser omitidas em silêncio")
check(set(v2.OCCURRENCE_NOVELTY) == {"NEW_OCCURRENCE", "FOLLOW_UP",
                                     "HISTORICAL_CONTEXT",
                                     "DESCRIPTOR_OR_BACKGROUND",
                                     "UNDETERMINED"},
      f"[9] novidade cobre as cinco situações ({len(v2.OCCURRENCE_NOVELTY)})")
check(all(o in v2.TRANSACTION_OBJECT for o in
          ("COMPANY_CONTROL", "EQUITY_STAKE", "ASSET_OR_BUSINESS_UNIT",
           "PROPERTY_OR_REAL_ESTATE", "CONCESSION_OR_LICENSE",
           "EXPLORATION_OR_RESOURCE_RIGHT", "OTHER", "UNDETERMINED",
           "NOT_APPLICABLE")),
      f"[10] objeto cobre os nove casos exigidos ({len(v2.TRANSACTION_OBJECT)})")
check("NOT_APPLICABLE" in v2.TRANSACTION_OBJECT,
      "[11] evento que não é transação tem resposta própria — não é forçado a "
      "inventar objeto")
check("occurrence_novelty_quote" in _it["properties"]
      and "transaction_object_quote" in _it["properties"],
      "[12] as duas dimensões pedem citação literal, como as demais")

print()
print("=" * 98)
print("BLOCO C — EXPRESSIVIDADE contra adjudicações já registradas")
print("=" * 98)
# Testes de VOCABULÁRIO: o contrato consegue REPRESENTAR cada veredito?
# Nenhum nome de empresa aparece — o que se testa é o enum, não o caso.
_casos_expr = [
    ("participação acionária relevante", "EQUITY_STAKE", True, 13),
    ("controle societário", "COMPANY_CONTROL", True, 14),
    ("propriedade rural", "PROPERTY_OR_REAL_ESTATE", False, 15),
    ("bloco exploratório", "EXPLORATION_OR_RESOURCE_RIGHT", False, 16),
    ("ativo/unidade de negócio", "ASSET_OR_BUSINESS_UNIT", False, 17),
    ("concessão", "CONCESSION_OR_LICENSE", False, 18),
]
for _rot, _obj, _societario, _n in _casos_expr:
    _r = ev.projetar_pontuavel_v2(E(transaction_object=_obj), "Acme", [], "ma")
    ok = (_r["pontuavel"] is True) if _societario else (
        _r["pontuavel"] is False and _r["porta"] == "OBJETO")
    check(ok, f"[{_n}] {_rot} → {'M&A legítimo' if _societario else 'não é M&A societário'}")

check(ev.projetar_pontuavel_v2(E(transaction_object="ASSET_OR_BUSINESS_UNIT"),
                               "Acme", [], "falencia")["pontuavel"] is True,
      "[19] a porta de OBJETO é escopada a M&A — não veta outras famílias")

print()
print("=" * 98)
print("BLOCO D — NOVIDADE DA OCORRÊNCIA")
print("=" * 98)
_nov = [("NEW_OCCURRENCE", True, 20), ("FOLLOW_UP", False, 21),
        ("HISTORICAL_CONTEXT", False, 22),
        ("DESCRIPTOR_OR_BACKGROUND", False, 23), ("UNDETERMINED", False, 24)]
for _v, _pont, _n in _nov:
    _r = ev.projetar_pontuavel_v2(E(occurrence_novelty=_v), "Acme", [], "ma")
    ok = (_r["pontuavel"] is True) if _pont else (
        _r["pontuavel"] is False and _r["porta"] == "NOVIDADE")
    check(ok, f"[{_n}] {_v} → {'pontua' if _pont else 'não cria ocorrência'}")

# A distinção central: fato antigo + artigo novo.
_r = ev.projetar_pontuavel_v2(E(currentness="HISTORICAL",
                                occurrence_novelty="NEW_OCCURRENCE"),
                              "Acme", [], "ma")
check(_r["pontuavel"] is True,
      "[25] fato datado como HISTORICAL mas relatado como ocorrência NOVA "
      "ainda pontua — as duas perguntas são independentes")
_r2 = ev.projetar_pontuavel_v2(E(currentness="CURRENT",
                                 occurrence_novelty="FOLLOW_UP"),
                               "Acme", [], "ma")
check(_r2["pontuavel"] is False and _r2["porta"] == "NOVIDADE",
      "[26] e fato CURRENT em artigo de follow-up NÃO pontua — que é "
      "exatamente o padrão que o V1 não sabia expressar")

print()
print("=" * 98)
print("BLOCO E — COMPATIBILIDADE: a projeção V2 lê saída V1")
print("=" * 98)
_v1ev = {"event_id": "ma", "event_asserted": "ASSERTED", "subject": "Acme",
         "company_role": "SUBJECT", "currentness": "HISTORICAL",
         "phase": "CONCLUDED", "centrality": "MAIN"}
_c = ev.projetar_pontuavel_v2(_v1ev, "Acme", [], "ma")
check(_c["pontuavel"] is False and _c["porta"] == "VIGENCIA",
      "[27] sem a dimensão nova, a porta VIGENCIA do V1 continua valendo")
_v1ok = dict(_v1ev, currentness="CURRENT")
check(ev.projetar_pontuavel_v2(_v1ok, "Acme", [], "ma")["pontuavel"] is True,
      "[28] e uma saída V1 válida continua sendo avaliável")
check(ev.projetar_pontuavel_v2(None, "Acme", [], "ma")["pontuavel"] is None,
      "[29] evento ausente segue indecidível, não 'não pontua'")
for _porta, _kw, _n in (("ASSERCAO", {"event_asserted": "DENIED"}, 30),
                        ("PAPEL", {"company_role": "UNRELATED"}, 31),
                        ("CENTRALIDADE", {"centrality": "BACKGROUND"}, 32),
                        ("SUJEITO_E_PAPEL", {"company_role": "SELLER"}, 33)):
    _r = ev.projetar_pontuavel_v2(E(**_kw), "Acme", [], "ma")
    check(_r["pontuavel"] is False and _r["porta"] == _porta,
          f"[{_n}] a porta {_porta} do V1 sobrevive no V2")

print()
print("=" * 98)
print("BLOCO F — REGRA GERAL: nada de empresa ou caso")
print("=" * 98)
_fonte = inspect.getsource(ev.projetar_pontuavel_v2)
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _fonte.splitlines()))
for _n, _nome in enumerate(("Cemig", "Sabesp", "BTG", "B3", "Security",
                            "YPF", "PRIO", "Suzano", "Petrobras"), start=34):
    check(_nome not in _cod, f"[{_n}] a projeção V2 não menciona {_nome}")
check(not re.search(r"P1-\d", _cod), "[43] nem sample_id algum")

_src2 = io.open("reliability_pilot_contract_v2.py", encoding="utf-8").read()
_cod2 = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
               "\n".join(l.split("#")[0] for l in _src2.splitlines()))
for _n, _nome in enumerate(("Cemig", "BTG", "Suzano", "Petrobras"), start=44):
    check(_nome not in _cod2,
          f"[{_n}] e o contrato V2 tampouco cita {_nome} fora de comentário")

print()
print("=" * 98)
print("BLOCO G — PAYLOAD V2: sem vazamento, com schema convertível")
print("=" * 98)
_p = v2.payload_audit(texto="A empresa Alfa concluiu a compra de uma fazenda.",
                      organizacao="Alfa", aliases=[], event_ids=["ma"],
                      pub_iso="2026-01-01")
_pr = _p["prompt"]
check("occurrence_novelty" in _pr and "transaction_object" in _pr,
      "[48] o prompt explica as duas dimensões novas")
check("independentes e podem divergir" in _pr,
      "[49] e diz explicitamente que as duas perguntas de tempo são distintas")
_vaz = [n for n in ("Cemig", "Sabesp", "BTG", "Grupo Security", "YPF", "PRIO",
                    "Suzano", "Petrobras", "Âmbar", "Emae", "Aconcagua")
        if re.search(r"\b" + re.escape(n) + r"\b", _pr, re.I)]
check(not _vaz, f"[50] nenhum nome de controle no prompt ({_vaz or 'limpo'})")
_proib = ("human_truth", "human_label", "FALSE_POSITIVE", "evaluation_only",
          "DEV_CONTROL", "HOLDOUT", "scoreable")
check(not [x for x in _proib if x in _pr],
      "[51] e nenhuma verdade humana ou vocabulário de avaliação")
check(ga.campos_anulaveis(ga.adaptar_schema(v2.SCHEMA_AUDIT)) == [],
      "[52] o schema V2 adaptado não deixa tipo em lista para o SDK")
_ad = ga.adaptar_schema(v2.SCHEMA_AUDIT)["properties"]["events"]["items"]
check("occurrence_novelty" in _ad["required"]
      and "transaction_object" in _ad["required"],
      "[53] e as duas dimensões seguem obrigatórias na representação do provider")
_norm = ga.normalizar_saida(
    {"events": [{"event_id": "ma", "occurrence_novelty": "FOLLOW_UP",
                 "transaction_object": "ASSET_OR_BUSINESS_UNIT"}]},
    v2.SCHEMA_AUDIT)["events"][0]
check(_norm["occurrence_novelty_quote"] is None
      and _norm["transaction_object_quote"] is None,
      "[54] as citações novas, quando ausentes, viram None — não string vazia")

print()
print("=" * 98)
print(f"RESULTADO CONTRACT V2: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
