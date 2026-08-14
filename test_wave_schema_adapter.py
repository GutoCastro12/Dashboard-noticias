#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_schema_adapter.py — adaptação de schema, normalização e contabilidade.

O QUE ESTE TESTE PROTEGE

1. Que o CONTRATO CANÔNICO não seja rebaixado por limitação de um SDK. A
   ontologia descreve o problema de risco; não deve encolher porque uma
   biblioteca não converte `{"type": ["string","null"]}`.

2. Que ausência volte a ser `None`, e nunca `""`. Campo faltando significa "o
   modelo não afirmou isso"; string vazia significa "afirmou o vazio".
   Confundir os dois transforma silêncio em asserção.

3. Que `required` só perca campos genuinamente anuláveis. Resolver
   compatibilidade esvaziando `required` trocaria um erro de serialização por
   um buraco semântico — o modelo poderia omitir `company_role` e ninguém veria.

4. Que a contabilidade pare de chamar falha de cliente de chamada ao provider.
   O primeiro artefato reportou 26 chamadas quando 22 morreram antes da
   serialização.

NENHUMA CHAMADA A PROVIDER.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re

os.environ.pop("GEMINI_API_KEY", None)

import gemini_schema_adapter as ga
import bench_free_llm as bench
import risk_dashboard as rd
import reliability_pilot1_sample as ps

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


CFG = rd.load_config("config_risco.yaml")
ENTRADAS, AUSENTES = bench.montar_plano(ps.carregar_manifesto(), CFG)
AUDIT = [e for e in ENTRADAS if e["call_type"] == bench.pc.CALL_AUDIT][0]
DISC = [e for e in ENTRADAS if e["call_type"] == bench.pc.CALL_DISCOVERY][0]
S_AUDIT = AUDIT["payload"]["schema"]
S_DISC = DISC["payload"]["schema"]

NULOS_AUDIT = ["event_quote", "subject", "subject_quote", "role_quote",
               "relation", "related_entity", "relation_quote",
               "currentness_quote", "phase_quote"]

print("=" * 98)
print("BLOCO A — O DEFEITO E O QUE ELE ERA")
print("=" * 98)
check(sorted(ga.campos_anulaveis(S_AUDIT)) == sorted(NULOS_AUDIT),
      f"[1] os 9 campos anuláveis do audit são exatamente os que quebraram "
      f"({len(NULOS_AUDIT)})")
check(ga.eh_anulavel({"type": ["string", "null"]}) is True
      and ga.eh_anulavel({"type": "string"}) is False,
      "[2] anulável é definido pelo tipo canônico admitir null, não por nome")
_it = S_AUDIT["properties"]["events"]["items"]
check(isinstance(_it["properties"]["subject"]["type"], list),
      "[3] o canônico declara `subject` como lista de tipos — a causa do "
      "`unhashable type: 'list'`")

print()
print("=" * 98)
print("BLOCO B — CANÔNICO INTOCADO")
print("=" * 98)
_antes = copy.deepcopy(S_AUDIT)
_adaptado = ga.adaptar_schema(S_AUDIT)
check(S_AUDIT == _antes,
      "[4] adaptar NÃO muta o schema canônico recebido")
check(isinstance(_antes["properties"]["events"]["items"]
                 ["properties"]["subject"]["type"], list),
      "[5] o canônico continua aceitando null depois da adaptação")
check("subject" in _antes["properties"]["events"]["items"]["required"],
      "[6] e `subject` continua obrigatório no contrato canônico")
check(_adaptado is not S_AUDIT, "[7] a adaptação devolve uma cópia")

print()
print("=" * 98)
print("BLOCO C — REPRESENTAÇÃO DO PROVIDER")
print("=" * 98)
_ai = _adaptado["properties"]["events"]["items"]
check(_ai["properties"]["subject"]["type"] == "string",
      "[8] anulável vira tipo simples no provider")
check(ga.campos_anulaveis(_adaptado) == [],
      "[9] nenhum tipo em lista sobrevive à adaptação")
check("subject" not in _ai["required"],
      "[10] anulável sai de `required` — ausência é como o provider diz null")
_obrig = ["event_id", "event_asserted", "company_role", "currentness",
          "phase", "centrality", "field_support"]
check(all(c in _ai["required"] for c in _obrig),
      f"[11] os obrigatórios NÃO anuláveis continuam obrigatórios ({_obrig})")
_d = ga.descrever(S_AUDIT)
check(_d["removidos_de_required"] == ["subject"],
      f"[12] apenas UM campo saiu de required, e é anulável "
      f"({_d['removidos_de_required']})")
check(_d["tipos_em_lista_restantes"] == [],
      "[13] o resumo auditável confirma zero tipos em lista")
_ad_disc = ga.adaptar_schema(S_DISC)
check(ga.campos_anulaveis(_ad_disc) == [],
      "[14] a discovery também fica limpa")
check(all(c in _ad_disc["properties"]["events"]["items"]["required"]
          for c in ("organization", "risk_channel", "event_description")),
      "[15] e os obrigatórios da discovery seguem obrigatórios")

print()
print("=" * 98)
print("BLOCO D — NORMALIZAÇÃO: ausente vira None, nunca string vazia")
print("=" * 98)
_completo = {"events": [{
    "event_id": "ma", "event_asserted": "ASSERTED", "subject": "Âmbar",
    "company_role": "SELLER", "currentness": "CURRENT", "phase": "CONCLUDED",
    "centrality": "MAIN", "field_support": "SUPPORTED",
    "event_quote": "q1", "subject_quote": "q2", "role_quote": "q3",
    "relation": "vendedora", "related_entity": "Âmbar", "relation_quote": "q4",
    "currentness_quote": "q5", "phase_quote": "q6"}]}
_n1 = ga.normalizar_saida(_completo, S_AUDIT)
check(_n1["events"][0]["subject"] == "Âmbar"
      and _n1["events"][0]["event_quote"] == "q1",
      "[16] com TODOS os 9 presentes, nada é alterado")

_vazio = {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                      "company_role": "SELLER", "currentness": "CURRENT",
                      "phase": "CONCLUDED", "centrality": "MAIN",
                      "field_support": "SUPPORTED"}]}
_n2 = ga.normalizar_saida(_vazio, S_AUDIT)
_ev = _n2["events"][0]
check(all(c in _ev for c in NULOS_AUDIT),
      "[17] com os 9 AUSENTES, todos reaparecem no objeto canônico")
check(all(_ev[c] is None for c in NULOS_AUDIT),
      "[18] e todos valem None")
check(not any(_ev[c] == "" for c in NULOS_AUDIT),
      "[19] NENHUM virou string vazia — silêncio não vira asserção")
check(_ev["company_role"] == "SELLER" and _ev["event_id"] == "ma",
      "[20] os campos obrigatórios presentes ficam intactos")

_misto = {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                      "company_role": "SELLER", "currentness": "CURRENT",
                      "phase": "CONCLUDED", "centrality": "MAIN",
                      "field_support": "SUPPORTED",
                      "subject": "Âmbar", "relation": "vendedora"}]}
_n3 = ga.normalizar_saida(_misto, S_AUDIT)["events"][0]
check(_n3["subject"] == "Âmbar" and _n3["relation"] == "vendedora",
      "[21] mistura: o que veio é preservado")
check(_n3["subject_quote"] is None and _n3["role_quote"] is None
      and _n3["event_quote"] is None,
      "[22] e só o que faltou vira None")
_faltas = [("event_quote", "evidência do evento"),
           ("relation", "relação"), ("currentness_quote", "citação de vigência"),
           ("phase_quote", "citação de fase")]
_n = 23
for _campo, _rot in _faltas:
    _um = copy.deepcopy(_completo)
    del _um["events"][0][_campo]
    _r = ga.normalizar_saida(_um, S_AUDIT)["events"][0]
    check(_r[_campo] is None and _r["subject"] == "Âmbar",
          f"[{_n}] {_rot} ausente vira None sem afetar os demais")
    _n += 1

_n4 = ga.normalizar_saida({"events": []}, S_AUDIT)
check(_n4 == {"events": []}, "[27] lista vazia não ganha itens fantasma")
_n5 = ga.normalizar_saida({"events": [{"event_id": "x", "inventado": 1}]},
                          S_AUDIT)
check(_n5["events"][0]["inventado"] == 1,
      "[28] campo fora do contrato é preservado como veio, não descartado")

print()
print("=" * 98)
print("BLOCO E — CONTABILIDADE: falha de cliente não é chamada ao provider")
print("=" * 98)
_esperados = ("planejadas", "execucoes_cliente", "falhas_de_cliente",
              "invocacoes_sdk", "sucessos_provider", "rejeitadas_por_cota",
              "puladas_pelo_disjuntor")
_m = bench.executar("mock", confirmado=False)
check(all(k in (_m.get("contadores") or {}) for k in _esperados),
      f"[29] os contadores existem e são distintos: {_esperados}")
check("provider_calls" not in _m,
      "[30] o campo ambíguo `provider_calls` não existe mais no resultado")
_src = "\n".join(l.split("#")[0] for l in
                 io.open("bench_free_llm.py", encoding="utf-8").read().splitlines())
check("provider_calls" not in _src,
      "[31] e não sobrou referência a ele no código")

# reproduz o cenário do primeiro artefato: schema quebra no cliente
class _ProvedorQuebraSchema(bench.ProvedorFalsoBench):
    class types:
        @staticmethod
        def GenerationConfig(**kw):
            raise TypeError("unhashable type: 'list'")


_p1 = _ProvedorQuebraSchema([])
_p2 = _ProvedorQuebraSchema([])
_q = bench.executar("mock", confirmado=False,
                    provedores={bench.G1: _p1, bench.G2: _p2})
_c = _q["contadores"]
# Erro de schema é determinístico: a segunda tentativa falharia igual. Por isso
# ele é classificado como interruptor, e a corrida para no primeiro de CADA
# modelo — 2 falhas e 20 puladas, em vez das 22 que o primeiro benchmark
# queimou por não ter essa classificação.
check(_c["falhas_de_cliente"] == 2 and _c["puladas_pelo_disjuntor"] == 20,
      f"[32] o erro de schema interrompe no 1º de cada modelo: "
      f"{_c['falhas_de_cliente']} falhas, {_c['puladas_pelo_disjuntor']} puladas")
check(_c["invocacoes_sdk"] == 0,
      f"[33] e ZERO invocações do SDK são contadas ({_c['invocacoes_sdk']})")
check(_c["sucessos_provider"] == 0 and _c["rejeitadas_por_cota"] == 0,
      "[34] nem sucesso nem cota — o erro não foi do provider")
check(_p1.invocacoes == 0 and _p2.invocacoes == 0,
      "[35] o provedor falso confirma: generate_content nunca foi chamado")
check(_q["por_modelo"][bench.G1]["linhas"][0]["estado"]
      == bench.CLIENT_SCHEMA_ERROR,
      "[36] o estado é CLIENT_SCHEMA_ERROR, não um erro de provider genérico")

_ok = bench.executar("mock", confirmado=False)
_co = _ok["contadores"]
check(_co["invocacoes_sdk"] == _co["execucoes_cliente"] == 22,
      f"[37] no caminho saudável, execuções e invocações coincidem "
      f"({_co['invocacoes_sdk']})")
check(_co["falhas_de_cliente"] == 0, "[38] e zero falhas de cliente")

_quota = type("ResourceExhausted", (Exception,),
              {})("429 quota exceeded requests per day")
_g1 = bench.ProvedorFalsoBench([_quota])
_g2 = bench.ProvedorFalsoBench(['{"events":[]}'] * 11)
_iso = bench.executar("mock", confirmado=False,
                      provedores={bench.G1: _g1, bench.G2: _g2})
_k1 = _iso["por_modelo"][bench.G1]["contadores"]
_k2 = _iso["por_modelo"][bench.G2]["contadores"]
check(_k1["rejeitadas_por_cota"] == 1 and _k1["puladas_pelo_disjuntor"] == 10,
      f"[39] G1: 1 rejeitada por cota, 10 puladas pelo disjuntor")
check(_k1["invocacoes_sdk"] == 1,
      "[40] e só 1 invocação do SDK — as puladas não contam")
check(_k2["invocacoes_sdk"] == 11 and _k2["puladas_pelo_disjuntor"] == 0,
      f"[41] G2 roda inteiro: cota é por modelo ({_k2['invocacoes_sdk']})")

print()
print("=" * 98)
print("BLOCO F — ESCOPO SEMÂNTICO E PREFLIGHT")
print("=" * 98)
_d2 = bench.executar("dry", confirmado=False, escopo=bench.ESCOPO_SEMANTICO)
_g2t = _d2["gates"]
check(_g2t["chamadas_planejadas"] == 22 and _g2t["teto"] == 22,
      f"[42] escopo semântico: 22 planejadas, teto 22 "
      f"({_g2t['chamadas_planejadas']}/{_g2t['teto']})")
check(_g2t["lotes_traducao"] == [],
      "[43] a tradução NÃO é repetida — já foi medida no run 31754386165")
check(_g2t["escopo"] == "semantic", "[44] o artefato declara o escopo")
check(_g2t["schema_adaptado"]["n_anulaveis"] == 9,
      "[45] o artefato registra o que a adaptação fez")
_pf = io.open("preflight_gemini_schema.py", encoding="utf-8").read()
_pf_codigo = "\n".join(l.split("#")[0] for l in _pf.splitlines())
_pf_codigo = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", _pf_codigo)
check("generate_content" not in _pf_codigo,
      "[46] o preflight não contém chamada de geração — zero rede por construção")
check("GenerationConfig(" in _pf_codigo,
      "[47] mas exercita o conversor REAL que falhou")
check("google.generativeai" in _pf_codigo,
      "[48] usando o SDK de verdade, não uma imitação")

print()
print("=" * 98)
print(f"RESULTADO SCHEMA ADAPTER + TELEMETRIA: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
