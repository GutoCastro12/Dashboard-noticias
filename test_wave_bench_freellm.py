#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_bench_freellm.py — preflight do benchmark entre modelos gratuitos.

O QUE ESTE TESTE PROTEGE

1. Que os dois modelos sejam medidos ISOLADOS. Zero retry, zero fallback
   entre eles e disjuntor POR MODELO — a cota do Gemini é contada por
   projeto+modelo, então parar os dois porque um esgotou jogaria fora metade
   da medição.

2. Que o teto de invocações seja real e verificado ANTES de gastar.

3. Que a verdade humana não chegue ao modelo. Ela existe só do lado da
   avaliação; um vazamento transformaria o benchmark num teste de leitura.

4. Que a DISCOVERY continue cega por assinatura de função, não por
   disciplina de quem escreve o prompt.

5. Que o benchmark de tradução meça o que a produção pode perder:
   mapeamento artigo→tradução, entidades e números.

NENHUMA CHAMADA A PROVIDER. Os modos exercitados são dry e mock.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re

os.environ.pop("GEMINI_API_KEY", None)

import bench_free_llm as bench
import risk_dashboard as rd

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
MAN = json.load(io.open("test_fixtures_reliability/pilot1_sample_manifest_v2.json",
                        encoding="utf-8"))
PORID = {i["sample_id"]: i for i in MAN["itens"]}

print("=" * 98)
print("BLOCO A — CANDIDATOS, TETO E PLANO")
print("=" * 98)
check(bench.MODELOS == ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite"),
      f"[1] mede exatamente os dois candidatos: {bench.MODELOS}")
check(bench.EXCLUIDO == "gemini-3.6-flash"
      and bench.EXCLUIDO not in bench.MODELOS,
      "[2] gemini-3.6-flash explicitamente FORA — sua cota já foi medida")
check("llama-3.3" not in io.open("bench_free_llm.py", encoding="utf-8").read(),
      "[3] nenhuma dependência de llama-3.3 foi introduzida")
check(bench.MAX_PROVIDER_CALLS == 30, "[4] teto duro de 30 invocações reais")

_d = bench.executar("dry", confirmado=False)
_g = _d["gates"]
check(_d["invocacoes_sdk"] == 0 and _d["execucoes_cliente"] == 0,
      "[5] dry: ZERO invocações do SDK e ZERO execuções no cliente")
check(_g["plano_dentro_do_teto"] and _g["chamadas_planejadas"] <= _g["teto"],
      f"[6] plano dentro do teto ({_g['chamadas_planejadas']}/{_g['teto']})")
# escopo default = semântico: a tradução já foi medida no run 31754386165 e
# não é repetida.
_esperado = (len(bench.AUDIT_CASES) + len(bench.DISCOVERY_CASES)) * 2
check(_g["chamadas_planejadas"] == _esperado == 22,
      f"[7] {len(bench.AUDIT_CASES)} audit + {len(bench.DISCOVERY_CASES)} "
      f"discovery, ×2 modelos = {_esperado}")
check(8 <= len(bench.AUDIT_CASES) <= 12,
      f"[8] amostra de audit dentro de 8–12 ({len(bench.AUDIT_CASES)})")
check(2 <= len(bench.DISCOVERY_CASES) <= 4,
      f"[9] discovery dentro de 2–4 ({len(bench.DISCOVERY_CASES)})")
check(_g["itens_ausentes"] == [],
      "[10] nenhum caso planejado está ausente do manifesto congelado")
check(_g["vazamento"] == "OK", "[11] auditoria de vazamento: OK")
check(_g["retry"] == 0 and _g["fallback_entre_modelos"] == 0,
      "[12] zero retry e zero fallback entre modelos")
check(_g["structured_output"] is True and bench.OUTPUT_TOKEN_CAP == 1600,
      "[13] structured output nos dois, cap 1600 igual para ambos")

print()
print("=" * 98)
print("BLOCO B — AMOSTRA: nada inventado, nada substituído")
print("=" * 98)
check(all(PORID[s]["input"]["texto"] for s in bench.AUDIT_CASES),
      "[14] todo caso de audit tem texto — nenhum item vazio foi arrastado")
check(all((PORID[s].get("evaluation_only") or {}).get("human_truth")
          for s in bench.AUDIT_CASES),
      "[15] e todos têm verdade humana adjudicada")
check("P1-X001" not in bench.AUDIT_CASES and "P1-X006" not in bench.AUDIT_CASES,
      "[16] os dois casos SEM input ficaram de fora, sem substituto 'parecido'")
check(bench.ps.SAMPLE_VERSION == "r7b.pilot1.sample.v2",
      f"[17] a amostra de origem continua congelada: {bench.ps.SAMPLE_VERSION}")

_ent, _aus = bench.montar_plano(bench.ps.carregar_manifesto(), CFG)
_PROIB = ("human_truth", "human_label", "FALSE_POSITIVE", "evaluation_only",
          "DEV_CONTROL", "HOLDOUT", "human_scoreable", "failure_dimension",
          "s3_family", "deterministic", "R_EVENTO")
_sujos = []
for _e in _ent:
    _t = json.dumps(_e["payload"], ensure_ascii=False)
    _sujos += [(_e["sample_id"], p) for p in _PROIB if p in _t]
check(not _sujos, f"[18] nenhum payload carrega verdade humana nem veredito "
                  f"determinístico ({_sujos[:2] or 'limpo'})")
_sig = inspect.signature(bench.pc.payload_discovery)
check(not any(p in _sig.parameters
              for p in ("empresa", "company", "candidatos", "watchlist")),
      f"[19] DISCOVERY cega por ASSINATURA: {tuple(_sig.parameters)}")

print()
print("=" * 98)
print("BLOCO C — ISOLAMENTO: disjuntor por modelo, sem contaminação cruzada")
print("=" * 98)
_m = bench.executar("mock", confirmado=False)
check(_m["invocacoes_sdk"] == 0 and _m["estado"] == "OK",
      "[20] mock percorre o caminho completo sem provider real")
check(set(_m["por_modelo"]) == set(bench.MODELOS),
      "[21] os dois modelos são medidos SEPARADAMENTE")

_quota = type("ResourceExhausted", (Exception,),
              {})("429 quota exceeded requests per day")
_g1 = bench.ProvedorFalsoBench([_quota])
_g2 = bench.ProvedorFalsoBench(['{"events":[]}'] * 11)
_iso = bench.executar("mock", confirmado=False,
                      provedores={bench.G1: _g1, bench.G2: _g2})
_l1, _l2 = _iso["por_modelo"][bench.G1], _iso["por_modelo"][bench.G2]
check(_l1["circuit_breaker"] is not None, "[22] G1 com cota: disjuntor disparou")
check(_g1.invocacoes == 1, f"[23] G1 fez UMA tentativa e parou (={_g1.invocacoes})")
_skip = sum(1 for l in _l1["linhas"] if str(l["estado"]).startswith("SKIPPED_"))
check(_skip == 10, f"[24] as 10 restantes de G1 viraram SKIPPED (={_skip})")
check(_l1["contadores"]["invocacoes_sdk"] == 1
      and _l1["contadores"]["rejeitadas_por_cota"] == 1,
      "[25] a contabilidade de G1 registra 1 invocação e 1 rejeição por cota")
check(_l2["circuit_breaker"] is None and _g2.invocacoes == 11,
      f"[26] G2 rodou os 11 planejados INTEIROS — cota é por modelo "
      f"(={_g2.invocacoes})")
check(all(l["modelo"] == bench.G1 for l in _l1["linhas"]),
      "[27] nenhuma linha de G1 foi respondida por G2 — zero fallback cruzado")

print()
print("=" * 98)
print("BLOCO D — AVALIAÇÃO SEMÂNTICA: verdade humana só do lado da medição")
print("=" * 98)
_cemig = PORID["P1-002"]
_errado = {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                       "subject": "Cemig", "company_role": "BUYER",
                       "currentness": "CURRENT", "phase": "CONCLUDED",
                       "centrality": "MAIN"}]}
_a = bench.avaliar_caso(_cemig, _errado, "OK")
check(_a["human_scoreable"] is False and _a["llm_scoreable"] is True
      and _a["acertou"] is False,
      "[28] Cemig como COMPRADORA: LLM pontua, humano não → erro contabilizado")
_certo = {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                      "subject": "Âmbar Energia", "company_role": "SELLER",
                      "currentness": "CURRENT", "phase": "CONCLUDED",
                      "centrality": "MAIN"}]}
_b = bench.avaliar_caso(_cemig, _certo, "OK")
check(_b["llm_scoreable"] is False and _b["acertou"] is True,
      "[29] sujeito é a Âmbar, não a Cemig → não pontua → acerto")
_prio = bench.avaliar_caso(
    PORID["P1-009"],
    {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                 "subject": "PRIO", "company_role": "SUBJECT",
                 "currentness": "HISTORICAL", "phase": "CONCLUDED",
                 "centrality": "BACKGROUND"}]}, "OK")
check(_prio["llm_scoreable"] is False and _prio["acertou"] is True,
      "[30] PRIO com M&A como contexto histórico de fundo → não pontua")
check(_a["deterministic_scoreable"] == (
        (_cemig.get("evaluation_only") or {}).get("deterministic") or {}
      ).get("scoreable"),
      "[31] o baseline determinístico viaja junto, sem ser reescrito")
check("human_truth" not in json.dumps(_ent[0]["payload"], ensure_ascii=False)
      and _a["human_scoreable"] is not None,
      "[32] a verdade existe na AVALIAÇÃO e não existe no payload")
_sem = bench.avaliar_caso(_cemig, None, "QUOTA_EXHAUSTED")
check(_sem["llm_scoreable"] is None and _sem["acertou"] is None,
      "[33] chamada que não respondeu não conta como acerto NEM como erro")

print()
print("=" * 98)
print("BLOCO E — TRADUÇÃO: mapeamento, entidades e números")
print("=" * 98)
_lotes = bench.montar_lotes_traducao(bench.ps.carregar_manifesto())
check(len(_lotes) == 2 and {l["idioma"] for l in _lotes} == {"es", "en"},
      "[34] dois lotes fixos: um espanhol, um inglês")
check(all(l["itens"] for l in _lotes),
      "[35] os dois lotes têm itens reais do manifesto congelado")
check("Traduza para português do Brasil" in _lotes[0]["prompt"],
      "[36] usa o MESMO prompt da produção — não um inventado para o benchmark")
check("human_truth" not in _lotes[0]["prompt"]
      and "evaluation_only" not in _lotes[0]["prompt"],
      "[37] e o prompt de tradução também não carrega verdade humana")

_bom = {"itens": [{"i": o["i"],
                   "title": o["title"].replace("Fusión", "Fusão") + " (PT)",
                   "summary": "resumo traduzido"}
                  for o in _lotes[0]["itens"]]}
_av = bench.avaliar_traducao(_lotes[0], _bom, "OK")
check(_av["mapeamento_completo"] and not _av["indices_faltando"],
      "[38] mapeamento completo quando todos os índices voltam")
check(_av["entidades_preservadas"] > 0 and not _av["entidades_perdidas"],
      f"[39] entidades preservadas ({_av['entidades_preservadas']}), "
      f"nenhuma perdida")

_incompleto = {"itens": _bom["itens"][:2]}
_av2 = bench.avaliar_traducao(_lotes[0], _incompleto, "OK")
check(not _av2["mapeamento_completo"] and _av2["indices_faltando"],
      f"[40] índice ausente é DETECTADO, não ignorado "
      f"({_av2['indices_faltando']})")

_corrompido = {"itens": [{"i": o["i"], "title": "titulo generico sem entidade",
                          "summary": ""} for o in _lotes[0]["itens"]]}
_av3 = bench.avaliar_traducao(_lotes[0], _corrompido, "OK")
check(_av3["entidades_perdidas"],
      f"[41] entidade apagada pela tradução é DETECTADA "
      f"({_av3['entidades_perdidas'][:2]})")

_nao_traduziu = {"itens": [{"i": o["i"], "title": o["title"], "summary": ""}
                           for o in _lotes[0]["itens"]]}
_av4 = bench.avaliar_traducao(_lotes[0], _nao_traduziu, "OK")
check(_av4["titulos_efetivamente_traduzidos"] == 0,
      "[42] devolver o original intacto conta como NÃO traduzido")

_av5 = bench.avaliar_traducao(_lotes[0], None, "QUOTA_EXHAUSTED")
check(_av5["itens_devolvidos"] == 0 and not _av5["mapeamento_completo"],
      "[43] lote bloqueado por cota não vira falso sucesso")

_en = [l for l in _lotes if l["idioma"] == "en"][0]
check(any("440" in o["title"] for o in _en["itens"]),
      "[44] o lote inglês carrega um valor monetário verificável (US$ 440 mi)")

print()
print("=" * 98)
print("BLOCO F — WORKFLOW: manual, sem commit, sem segredo em log")
print("=" * 98)
_wf = io.open(".github/workflows/workflow_bench_freellm.yml",
              encoding="utf-8").read()
_wf_codigo = "\n".join(l.split("#")[0] for l in _wf.splitlines())
check(re.search(r"^\s*workflow_dispatch:", _wf, re.M) is not None,
      "[45] só workflow_dispatch")
check(re.search(r"^\s*schedule:", _wf, re.M) is None,
      "[46] sem schedule — nunca roda sozinho")
check("contents: read" in _wf_codigo,
      "[47] permissão de LEITURA apenas — não pode commitar nem por engano")
check("git push" not in _wf_codigo and "git commit" not in _wf_codigo,
      "[48] o workflow não commita nem empurra nada")
check("secrets.GEMINI_API_KEY" in _wf_codigo,
      "[49] usa o secret de produção (são dois modelos Gemini do mesmo projeto)")
check("secrets.GEMINI_PILOT_API_KEY" not in _wf_codigo,
      "[50] e NÃO toca o secret do piloto")
check("echo $GEMINI" not in _wf and "echo ${GEMINI" not in _wf,
      "[51] nunca ecoa o segredo")
_i_dry = _wf.find("--mode dry")
_i_live = _wf.find("--mode live")
check(0 < _i_dry < _i_live,
      "[52] o portão seco vem ANTES do live")
check("upload-artifact" in _wf_codigo, "[53] o resultado sai como artefato")
check("--confirm EXECUTAR-BENCHMARK" in _wf_codigo,
      "[54] o live exige confirmação digitada")

print()
print("=" * 98)
print(f"RESULTADO BENCH FREE-LLM (preflight): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
