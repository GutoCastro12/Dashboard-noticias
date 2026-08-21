#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7v_sensibilidade_contextual.py — 4I.2 R7v.

SENSIBILIDADE CONTEXTUAL RESTAURADA, COM DECOMPOSIÇÃO.

Correção PARA A FRENTE sobre `eb2f4b7`. A arquitetura de ocorrência daquela
promoção permanece inteira — só a política de score para evento
contexto-dependente foi reconsiderada.

A DECISÃO HUMANA DE 2026-08-22

    Enquanto não existir avaliação direcional por OCORRÊNCIA, um evento
    material de família `neutra` volta a contribuir com o seu peso
    determinístico, como PRIOR CONSERVADOR de alerta. O radar prefere levar um
    M&A, uma troca de comando ou uma emissão à inspeção humana a atribuir zero
    só porque a taxonomia determinística não sabe dizer se o fato é bom ou ruim.

    O que impede isso de virar "deterioração confirmada" é a DECOMPOSIÇÃO:

        total = contribuição adversa + contribuição contextual

O QUE ESTE ARQUIVO TRAVA

  1. a arquitetura de ocorrência de `eb2f4b7` NÃO regride — ids, membros,
     representantes, âncoras, alias e fases idênticos;
  2. identidade de ocorrência segue independente de peso de score;
  3. `total == adverso + contextual`, em precisão canônica;
  4. favorável e mitigador somam ZERO e NUNCA subtraem;
  5. contextual é rotulado CONTEXTUAL, nunca ADVERSE;
  6. a costura para modulação direcional futura muda o SCORE sem tocar em
     identidade — e não chama modelo nenhum.

O QUE ELE NÃO FAZ

Não fixa totais absolutos: decaimento e cron mudam o número a cada rodada.
Trava-se relação e invariante.
"""
from __future__ import annotations

import copy
import io
import json

import occurrence_engine as oe
import risk_dashboard as rd

PASS = FAIL = 0
POLITICA = "HUMAN_SCORE_POLICY_2026_08_22"


def _politica_vigente():
    return oe.POLITICA_HUMANA


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + label)
    else:
        FAIL += 1
        print("  FALHOU: " + label)


CFG = rd.load_config("config_risco.yaml")
H = json.load(io.open("risk_history.json", encoding="utf-8"))
EVO = rd.build_evolution(H, CFG)
TAX = {e["id"]: e for e in CFG["taxonomy"]}
ROT = {e["label"]: e["id"] for e in CFG["taxonomy"]}
LINHA = {l["company"]: l for l in EVO}
BD = [(l["company"], b) for l in EVO for b in (l.get("breakdown") or [])]

print("=" * 98)
print("BLOCO A - §33 uma classificacao canonica, consumida por todos")
print("=" * 98)
check(_politica_vigente() == POLITICA,
      f"[0] o motor declara a politica VIGENTE ({_politica_vigente()})")
check(oe.classe_de_sinal(TAX["recuperacao_judicial"]) == oe.SINAL_ADVERSO
      and oe.classe_de_sinal(TAX["rebaixamento_rating"]) == oe.SINAL_ADVERSO,
      "[1] familia `negativa` -> ADVERSE")
check(oe.classe_de_sinal(TAX["ma"]) == oe.SINAL_CONTEXTUAL
      and oe.classe_de_sinal(TAX["troca_ceo"]) == oe.SINAL_CONTEXTUAL
      and oe.classe_de_sinal(TAX["emissao_divida"]) == oe.SINAL_CONTEXTUAL
      and oe.classe_de_sinal(TAX["follow_on"]) == oe.SINAL_CONTEXTUAL,
      "[2] familia `neutra` -> CONTEXTUAL, nao ADVERSE")
check(oe.classe_de_sinal(TAX["absolvicao"]) == oe.SINAL_NAO_RISCO,
      "[3] `positiva`/`mitigadora` -> FAVORABLE_OR_MITIGATING")
_sem = [e["id"] for e in CFG["taxonomy"] if not (e.get("direction") or "")]
check(not _sem, f"[4] §7 zero familias sem `direction` ({_sem})")
_src = io.open("occurrence_engine.py", encoding="utf-8", newline="").read()
check('"ma", "troca_ceo", "emissao_divida"' not in _src,
      "[5] §33 nenhuma lista paralela de familias em codigo — tudo sai de "
      "`direction` na config")
check(not [c for c in _src if ord(c) < 32 and c not in "\n\r\t"],
      "[6] e o motor nao tem caractere de controle invisivel")

print()
print("=" * 98)
print("BLOCO B - §4/§5/§6 multiplicadores")
print("=" * 98)
check(oe.MULTIPLICADOR_DE_SINAL[oe.SINAL_ADVERSO] == 1.0,
      "[7] adverso = 1,0 — mecanica preservada")
check(oe.MULTIPLICADOR_DE_SINAL[oe.SINAL_CONTEXTUAL] == 1.0,
      "[8] §4 contextual = 1,0 — prior conservador, sem multiplicador "
      "arbitrario de 0,25 ou 0,50")
check(oe.MULTIPLICADOR_DE_SINAL[oe.SINAL_NAO_RISCO] == 0.0,
      "[9] favoravel/mitigador = 0,0")
check(all(v >= 0.0 for v in oe.MULTIPLICADOR_DE_SINAL.values()),
      "[10] §6 nenhum multiplicador NEGATIVO: evento favoravel nao abate "
      "default, RJ nem rebaixamento")
check(oe.multiplicador_de_sinal(TAX["ma"]) == 1.0
      and oe.multiplicador_de_sinal(TAX["absolvicao"]) == 0.0,
      "[11] e o multiplicador efetivo respeita a classe")
_pesos = {f: TAX[f]["score"] for f in
          ("ma", "troca_ceo", "emissao_divida", "follow_on",
           "recuperacao_judicial", "rebaixamento_rating")}
check(_pesos == {"ma": 40, "troca_ceo": 25, "emissao_divida": 35,
                 "follow_on": 30, "recuperacao_judicial": 100,
                 "rebaixamento_rating": 80},
      f"[12] §17 os pesos da config seguem INTOCADOS ({_pesos}) — calibracao "
      f"de peso e questao separada e futura")

print()
print("=" * 98)
print("BLOCO C - §9/§39 total == adverso + contextual")
print("=" * 98)
_viola = [(l["company"], l["total_score"], l["adverse_score"],
           l["contextual_score"]) for l in EVO
          if abs(l["total_score"] - (l["adverse_score"] + l["contextual_score"]))
          > 0.6]
check(not _viola, f"[13] a invariante vale em TODOS os emissores ({_viola[:3]})")
check(all(l["favorable_score"] == 0 for l in EVO),
      "[14] §39 contribuicao favoravel e zero em todo emissor")
check(not [l["company"] for l in EVO if l["total_score"] < 0],
      "[15] §6 nenhum emissor com score NEGATIVO")
check(all("contribution_class" in b for _, b in BD),
      "[16] §8 cada linha do breakdown declara sua CLASSE de contribuicao")
check(all(b["contribution_class"] in
          (oe.SINAL_ADVERSO, oe.SINAL_CONTEXTUAL, oe.SINAL_NAO_RISCO,
           oe.SINAL_DESCONHECIDO) for _, b in BD),
      "[17] e a classe vem do enum canonico")
_ctx = [b for _, b in BD if b["contribution_class"] == oe.SINAL_CONTEXTUAL]
_adv = [b for _, b in BD if b["contribution_class"] == oe.SINAL_ADVERSO]
check(_ctx and all(b["contrib"] > 0 for b in _ctx if b["decay_f"] > 0.02),
      f"[18] §2 contextual VOLTOU a contribuir ({len(_ctx)} ocorrencias)")
check(_adv and all(b["contrib"] > 0 for b in _adv if b["decay_f"] > 0.02),
      f"[19] e adverso segue contribuindo ({len(_adv)})")
check(all(b["contribution_class"] != oe.SINAL_ADVERSO for b in _ctx),
      "[20] §43 contextual NUNCA e rotulado ADVERSE — contribui sem ser "
      "confundido com evidencia confirmada")
# `contrib` no breakdown vem arredondado a 0,1 para exibicao; a tolerancia
# acompanha isso, e nao a precisao interna
check(all(abs(b["contrib"] - b["canonical_contrib"] * b["signal_multiplier"])
          <= 0.06 for _, b in BD),
      "[21] §8 e `canonical_contrib × multiplicador == contrib`: da para "
      "auditar o que a politica de direcao esta fazendo em cada linha")

print()
print("=" * 98)
print("BLOCO D - §14/§37 a arquitetura de ocorrencia NAO regrediu")
print("=" * 98)


def _peso(fam, v):
    c = copy.deepcopy(CFG)
    for e in c["taxonomy"]:
        if e["id"] == fam:
            e["score"] = v
    return c


def _forma(cfg):
    return sorted(
        (o["_ocorrencia"]["occurrence_id"], o["_ocorrencia"]["company"],
         o["_ocorrencia"]["family"], o["_ocorrencia"]["canonical_object"],
         o["_ocorrencia"]["anchor_date"], o["_ocorrencia"]["initial_date"],
         o["_ocorrencia"]["display_representative"],
         o["_ocorrencia"]["refresh_reason"],
         tuple(o["_ocorrencia"]["aliases"]),
         tuple(sorted((m["article_id"], m["phase"]) for m in
                      o["_ocorrencia"]["members"])))
        for l in rd.build_evolution(H, cfg) for o in (l.get("events") or [])
        if o.get("_ocorrencia"))


_base = _forma(CFG)
check(_base == _forma(_peso("ma", 0)),
      "[22] §14 com peso de `ma` ZERO: ids, membros, fases, representantes, "
      "ancoras e alias IDENTICOS")
check(_base == _forma(_peso("ma", 80)), "[23] com peso DOBRADO tambem")
check(_base == _forma(_peso("recuperacao_judicial", 0)),
      "[24] e zerando uma familia ADVERSA — identidade nunca e funcao de score")
check(len(_base) == len({x[0] for x in _base}),
      f"[25] nenhum occurrence_id colide ({len(_base)} ocorrencias)")
_mem = [m for l in EVO for o in (l.get("events") or [])
        if o.get("_ocorrencia") for m in o["_ocorrencia"]["members"]]
check(_mem and all(m["article_id"] for m in _mem),
      f"[26] §3 proveniencia intacta: {len(_mem)}/{len(_mem)} membros com "
      f"`article_id`")
check(all(m["phase"] in oe.FASES for m in _mem),
      "[27] e a fase segue no enum fechado de quatro estados mais UNKNOWN")

print()
print("=" * 98)
print("BLOCO E - §10/§11/§38 JBS")
print("=" * 98)
_j = LINHA["JBS"]
_jbd = [b for c, b in BD if c == "JBS"]
_jma = [b for b in _jbd if ROT.get(b["label"]) == "ma"]
_jceo = [b for b in _jbd if ROT.get(b["label"]) == "troca_ceo"]
_jdiv = [b for b in _jbd if ROT.get(b["label"]) == "emissao_divida"]
_jrec = [b for b in _jbd if ROT.get(b["label"]) == "recomendacao_negativa"]
check(len(_jma) == 2,
      f"[28] §38 as DUAS transacoes de M&A seguem distintas ({len(_jma)})")
check(len(_jceo) == 1,
      f"[29] §38 UMA ocorrencia de CEO — a analise do UBS nao virou terceira "
      f"M&A e o descritor nao virou segundo CEO ({len(_jceo)})")
check(len(_jdiv) == 1 and len(_jrec) == 1,
      "[30] divida e recomendacao seguem uma cada")
check(all(b["contribution_class"] == oe.SINAL_CONTEXTUAL
          for b in _jma + _jceo + _jdiv),
      "[31] §10 M&A, CEO e divida sao CONTEXTUAL")
check(_jrec[0]["contribution_class"] == oe.SINAL_ADVERSO,
      "[32] §10 e a recomendacao rebaixada e ADVERSE")
check(_j["contextual_score"] > _j["adverse_score"],
      f"[33] §11 o total da JBS ({_j['total_score']}) e majoritariamente "
      f"contextual: adverso {_j['adverse_score']} x contextual "
      f"{_j['contextual_score']}")
check(_j["contextual_share"] > 0.7,
      f"[34] §11 {_j['contextual_share'] * 100:.0f}% do score da JBS e "
      f"atividade corporativa material, nao evidencia adversa confirmada — e "
      f"e exatamente isso que o analista precisa conseguir dizer")
check(abs(_j["adverse_score"] - sum(b["contrib"] for b in _jrec)) < 0.6,
      "[35] §11 a parcela adversa e exatamente a recomendacao rebaixada")
check(_j["n_adverse_types"] == 1 and _j["n_contextual_types"] == 3
      and _j["n_risk_signal_types"] == 4,
      f"[36] §12 tipos: adversos {_j['n_adverse_types']}, contextuais "
      f"{_j['n_contextual_types']}, sinal de risco {_j['n_risk_signal_types']}")
check(_j["total_score"] > 50,
      f"[37] §10 e o total voltou a subir em relacao ao estado gateado de 12 "
      f"pontos ({_j['total_score']})")

print()
print("=" * 98)
print("BLOCO F - §12 contagens separadas, compatibilidade preservada")
print("=" * 98)
check(all("n_adverse_types" in l and "n_contextual_types" in l
          and "n_risk_signal_types" in l for l in EVO),
      "[38] §12 as tres contagens estao expostas em toda linha")
check(all(l["n_risk_signal_types"] == l["n_adverse_types"]
          + l["n_contextual_types"] for l in EVO),
      "[39] e sinal de risco = adverso + contextual, sem sobreposicao")
check(all(l.get("hard_critical") is not None for l in EVO),
      "[40] §32 `hard_critical` segue exposto")
_nao_risco = [c for c, b in BD
              if b["contribution_class"] == oe.SINAL_NAO_RISCO]
check(not _nao_risco,
      f"[41] §32 nenhuma familia favoravel/mitigadora entra na conta de risco "
      f"({_nao_risco[:3]})")
_rd_src = io.open("risk_dashboard.py", encoding="utf-8").read()
check(_rd_src.count("def _classe_sinal") == 1
      and "_classe_sinal(o) == _oe.SINAL_ADVERSO" in _rd_src,
      "[42] §33 uma unica funcao de classificacao, e a compat de autoridade "
      "adversa deriva dela")
check("n_negative_types = n_risk_signal_types" in _rd_src,
      "[43] §12 `n_negative_types` e MANTIDO por compatibilidade e documentado "
      "como a contagem mais ampla de sinal de risco — sem rename publico")
check("sinais de risco em" in _rd_src and "sinais negativos em" not in _rd_src,
      "[44] §26 e o texto de persistencia deixou de chamar sinal contextual de "
      "'negativo'")

print()
print("=" * 98)
print("BLOCO G - §18..§23 controles humanos de ocorrencia intactos")
print("=" * 98)
OCC = {}
for _l in EVO:
    for _o in (_l.get("events") or []):
        _oc = _o.get("_ocorrencia")
        if _oc:
            OCC.setdefault((_oc["company"], _oc["family"]), []).append(_oc)
_tok = OCC.get(("Tok&Stok", "recuperacao_judicial"), [])
check(_tok and all("CONTINUING_STATE" in o["refresh_reason"] for o in _tok),
      "[45] §18 Tok&Stok: ancora de ESTADO CONTINUO preservada, nao a ancora "
      "obsoleta de 18/06")
check(LINHA["Tok&Stok"]["status"] == "critico",
      f"[46] §18 e segue CRITICA ({LINHA['Tok&Stok']['total_score']})")
check(LINHA["Tok&Stok"]["adverse_score"] > 0
      and LINHA["Tok&Stok"]["contextual_score"] == 0,
      f"[47] §18 100% adversa — nenhum ponto contextual a sustenta "
      f"(adv {LINHA['Tok&Stok']['adverse_score']}, "
      f"ctx {LINHA['Tok&Stok']['contextual_score']})")
_cos = [b for c, b in BD if c == "Cosan"
        and ROT.get(b["label"]) == "rebaixamento_rating"]
check(len(_cos) >= 2 and all(b["contribution_class"] == oe.SINAL_ADVERSO
                             for b in _cos),
      f"[48] §20 Cosan: acoes de agencias distintas seguem distintas e "
      f"adversas ({len(_cos)})")
_vale = [b for c, b in BD if c == "Vale"
         and ROT.get(b["label"]) == "investigacao_regulatoria"]
check(len(_vale) >= 2 and all(b["contribution_class"] == oe.SINAL_ADVERSO
                              for b in _vale),
      f"[49] §21 Vale: processos distintos, contribuicao ADVERSA (nao "
      f"contextual) ({len(_vale)})")
_smf = OCC.get(("Smart Fit", "ma"), [])
check(_smf and any(o["anchor_date"] > o["initial_date"] for o in _smf),
      "[50] §22 Smart Fit: fechamento material segue reancorando")
_suz = OCC.get(("Suzano", "ma"), [])
check(_suz and any(o["aliases"] == ["ma:suzano:tissue-ifp"] for o in _suz),
      "[51] §22 Suzano: alias declarado preservado")
_sab = OCC.get(("Sabesp", "ma"), [])
check(_sab and all(o["canonical_object"] for o in _sab)
      and oe.papel_marcador("cade") == oe.REGULADOR,
      "[52] §22 Sabesp: objeto identificado e `cade` nao faz ponte")
_eng = OCC.get(("Engie Brasil", "follow_on"), [])
check(len(_eng) == 1,
      f"[53] §23 Engie: a recapitulacao nao virou ocorrencia nova ({len(_eng)})")
check(not OCC.get(("Santander Brasil", "troca_ceo")),
      "[54] §49 Santander: segue sem ocorrencia de CEO")
_isa = OCC.get(("ISA Energia Brasil", "follow_on"), [])
check(all(o["anchor_date"] == o["initial_date"] for o in _isa) if _isa else True,
      "[55] §50 ISA: acompanhamento nao renova")

print()
print("=" * 98)
print("BLOCO H - §15/§40 costura para modulacao direcional FUTURA")
print("=" * 98)
check(oe.multiplicador_direcional() == 1.0,
      "[56] §15 a costura existe e hoje devolve 1,0 — nenhuma autoridade "
      "delegada a modelo")
_real = oe.multiplicador_direcional
try:
    oe.multiplicador_direcional = lambda ocorrencia=None: 0.5
    _meio = rd.build_evolution(H, CFG)
    _forma_meio = sorted(
        (o["_ocorrencia"]["occurrence_id"], o["_ocorrencia"]["anchor_date"],
         o["_ocorrencia"]["display_representative"],
         tuple(sorted((m["article_id"], m["phase"]) for m in
                      o["_ocorrencia"]["members"])))
        for l in _meio for o in (l.get("events") or []) if o.get("_ocorrencia"))
    _forma_base = sorted(
        (o["_ocorrencia"]["occurrence_id"], o["_ocorrencia"]["anchor_date"],
         o["_ocorrencia"]["display_representative"],
         tuple(sorted((m["article_id"], m["phase"]) for m in
                      o["_ocorrencia"]["members"])))
        for l in EVO for o in (l.get("events") or []) if o.get("_ocorrencia"))
    _lm = {l["company"]: l for l in _meio}
    check(_forma_meio == _forma_base,
          "[57] §40 modular o multiplicador direcional NAO muda identidade, "
          "membros, fase nem representante")
    check(_lm["JBS"]["contextual_score"] < LINHA["JBS"]["contextual_score"],
          f"[58] §40 mas MUDA a parcela contextual "
          f"({LINHA['JBS']['contextual_score']} -> "
          f"{_lm['JBS']['contextual_score']})")
    check(abs(_lm["JBS"]["adverse_score"] - LINHA["JBS"]["adverse_score"]) < 0.6,
          "[59] §40 e nao toca na parcela ADVERSA — a modulacao e escopada ao "
          "que a taxonomia nao sabe decidir")
finally:
    oe.multiplicador_direcional = _real
check(oe.multiplicador_direcional() == 1.0,
      "[60] e a costura volta ao estado de producao apos o teste")
_proibido = [t for t in ("import llm_router", "_router.", "openai", "anthropic",
                         "requests.", "urlopen") if t in _src]
check(not _proibido,
      f"[61] §15 nenhuma chamada de modelo nem de rede no motor ({_proibido})")

print()
print("=" * 98)
print("BLOCO I - §34/§35/§44 o que esta onda NAO podia tocar")
print("=" * 98)
_cfg_txt = io.open("config_risco.yaml", encoding="utf-8").read()
check("atencao_total_min: 60" in _cfg_txt and "critico_total_min: 125" in _cfg_txt,
      "[62] §34 limiares 60/125 intocados na config")
_hs = json.load(io.open("risk_human_supervision.json", encoding="utf-8"))
check(len(_hs["memberships"]) == 27
      and len({m["case_id"] for m in _hs["memberships"].values()}) == 24,
      "[63] supervisao humana intacta (27/24)")
_ot = json.load(io.open("risk_semantic_v2_shadow.json",
                        encoding="utf-8"))["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[64] occurrence_truth intacto (10/21/4)")
import reliability_occurrence_archival_source as _ar
check(_ar.integro()["ok"], "[65] §44 snapshots arquivais integros")
check("def agrupar_ocorrencias" in _src and "def _instancias" in _src,
      "[66] §3 o motor de ocorrencia de `eb2f4b7` segue no lugar, sem "
      "reescrita")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7v (sensibilidade contextual restaurada): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
