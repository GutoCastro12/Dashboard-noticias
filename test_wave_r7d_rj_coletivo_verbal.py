#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7d_rj_coletivo_verbal.py — R7d.

SUJEITO COLETIVO COM PREDICADO **VERBAL** TAMBÉM É INSOLVÊNCIA SETORIAL.

Caso adjudicado por humano (holdout Contract V2, caso #8, artigo
`10e297f95c5954630150`): "Casas Bahia, Marabraz, Mobly e Tok&STok: por que
tantas empresas de varejo estão pedindo recuperação judicial?" pontuava
`recuperacao_judicial` como evento DIRETO da Tok&Stok. A verdade humana é
`event_asserted=MENTIONED_ONLY`, `company_role=MENTIONED`,
`occurrence_novelty=DESCRIPTOR_OR_BACKGROUND`, `scoreable=False`.

A regra certa já existia: `R_INSOLVENCIA_SETORIAL_OU_DE_TERCEIRO`, criada na
R7c-P2. A falha era LEXICAL, não conceitual. `_INSOLV_SUJEITO_COLETIVO` cobria
a forma NOMINAL/preposicional do sujeito coletivo ("pedidos de recuperação
judicial", "empresas EM recuperação judicial") e não a forma VERBAL
("empresas ... estão PEDINDO recuperação judicial"). Sem casar, o evento
sobrevivia por ausência de blocker — o mesmo padrão que a R7a nomeou.

Por que o PLURAL é obrigatório, e não um detalhe de estilo: no corpus há
"Empresa chilena de lácteos solicita su quiebra" e "Empresa responsável pela
Storj entra com pedido de falência". São companhias ESPECÍFICAS descritas
genericamente — sujeito próprio, não coletivo. Aceitar o singular arrastaria
as duas junto.

Fronteira que a mudança obriga a fixar: um sujeito NOMEADO e coordenado
("Casas Bahia e Tok&Stok PEDEM recuperação judicial") é sujeito próprio e tem
de continuar pontuando. Por isso `_INSOLV_SUJEITO_PROPRIO` — que é checado
ANTES — passou a reconhecer também as conjugações no plural. A regra é sobre
sujeito GENÉRICO/COLETIVO, nunca sobre "verbo no plural".

Efeito medido na mesma fotografia (blast isolado da mudança): 1 par
artigo-empresa-evento removido, 0 criados, 1 empresa afetada.

Nenhum nome próprio do caso é conhecido pela regra: nem Tok&Stok, nem Casas
Bahia, nem Marabraz, nem Mobly, nem O Globo, nem o título.
"""
from __future__ import annotations

import io
import json
import re
import time

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
KWS = sa._keywords_por_evento(cfg)
AL = sa._aliases_map(cfg)
REGRA = "R_INSOLVENCIA_SETORIAL_OU_DE_TERCEIRO"

ARTIGO_ID = "10e297f95c5954630150"
EXATO = ("Casas Bahia, Marabraz, Mobly e Tok&STok: por que tantas empresas "
         "de varejo estão pedindo recuperação judicial?")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def dec(t, emp, ev="recuperacao_judicial", resumo=""):
    r = sa.resolve_article_semantics(t, resumo, emp, [ev], AL,
                                     article_year=2026, source_domain="ex.com",
                                     keywords_por_evento=KWS, country="")
    d = r["decisoes"][0]
    return bool(d["scoreable"]), (d.get("attribution_rule") or ""), d


print("=" * 98)
print("BLOCO A — regressão EXATA do caso #8 do holdout")
print("=" * 98)
_s, _r, _d = dec(EXATO, "Tok&Stok")
check(_s is False, f"[1] o evento deixa de pontuar para a Tok&Stok ({_s})")
check(_r == REGRA, f"[2] pela regra de insolvência setorial já existente ({_r})")
check(_d.get("event_scope") == "indireto", "[3] marcado como evento indireto")
check(_d.get("relation_type") == "setorial_ou_terceiro",
      f"[4] com a relação canônica da regra ({_d.get('relation_type')})")
check(_d.get("subject_evidence"),
      f"[5] a evidência do sujeito coletivo é registrada "
      f"({str(_d.get('subject_evidence'))[:44]!r})")
check("empresas de varejo" in (_d.get("subject_company") or ""),
      f"[6] o sujeito é o coletivo, não a Tok&Stok "
      f"({str(_d.get('subject_company'))[:44]!r})")

print()
print("=" * 98)
print("BLOCO B — o artigo exato pelo caminho canônico de produção")
print("=" * 98)
art = {"title": EXATO, "summary": EXATO + " &nbsp;&nbsp; O GLOBO",
       "source": "O GLOBO", "pub_iso": "2026-08-17 15:08",
       "url": "https://exemplo.invalido/x", "domain": "exemplo.invalido"}
rd.classify_and_attribute(art, cfg)
check("recuperacao_judicial" in [e["id"] for e in (art.get("events") or [])],
      "[7] segue sendo CANDIDATO de RJ no estágio de família (não apagamos a família)")
check(art.get("companies") == ["Tok&Stok"],
      f"[8] a Tok&Stok continua atribuída ao artigo ({art.get('companies')})")
check(not (art.get("events_by_company") or {}).get("Tok&Stok"),
      f"[9] mas NÃO pontua: events_by_company vazio "
      f"({(art.get('events_by_company') or {}).get('Tok&Stok')})")
_ctx = (art.get("context_events_by_company") or {}).get("Tok&Stok") or []
check(len(_ctx) == 1 and _ctx[0]["event_id"] == "recuperacao_judicial",
      "[10] o evento vira contexto para a empresa (roteamento canônico da regra)")
check(_ctx and _ctx[0].get("scoreable") is False,
      "[11] e o contexto é explicitamente não pontuável")
check(_ctx and _ctx[0].get("attribution_rule") == REGRA,
      f"[12] com a regra nomeada no registro ({_ctx[0].get('attribution_rule') if _ctx else '-'})")
_disc = art.get("semantic_discards") or []
check(any(x.get("regra") == REGRA for x in _disc),
      "[13] e há descarte semântico auditável")

print()
print("=" * 98)
print("BLOCO C — near-negatives: sujeito PRÓPRIO continua pontuando")
print("=" * 98)
_POS = [
    ("P1", "Tok&Stok pede recuperação judicial", "Tok&Stok"),
    ("P2", "Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão "
           "que impacta 2,2 mil funcionários", "Tok&Stok"),
    ("P3", "Tok&Stok está em recuperação judicial desde junho", "Tok&Stok"),
    ("P4", "Plano de recuperação judicial da Tok&Stok é aprovado", "Tok&Stok"),
    ("P5", "Vale entra com pedido de recuperação judicial", "Vale"),
    ("P6", "Recuperação judicial da Vale é deferida pela Justiça", "Vale"),
]
for tag, t, emp in _POS:
    s, r, _ = dec(t, emp)
    check(s is True, f"[14..19] {tag}: a monitorada É a devedora e pontua "
                     f"({emp}; regra={r or '-'})")

print()
print("=" * 98)
print("BLOCO D — SUJEITO NOMEADO COORDENADO no plural NÃO é coletivo")
print("=" * 98)
_COORD = [
    ("C1", "Casas Bahia e Tok&Stok pedem recuperação judicial", "Tok&Stok"),
    ("C2", "Tok&Stok e Mobly pediram recuperação judicial nesta semana",
     "Tok&Stok"),
    ("C3", "Vale e Tok&Stok entram com pedido de recuperação judicial",
     "Tok&Stok"),
    ("C4", "Vale e Braskem solicitaram recuperação judicial", "Vale"),
]
for tag, t, emp in _COORD:
    s, r, _ = dec(t, emp)
    check(s is True, f"[20..23] {tag}: nome próprio coordenado segue "
                     f"pontuando ({emp}; regra={r or '-'})")
check(not sa.detect_insolvencia_setorial(
    "Casas Bahia e Tok&Stok pedem recuperação judicial", "Tok&Stok",
    ["Tok&Stok"]),
    "[24] a regra coletiva NÃO rouba o sujeito nomeado coordenado")

print()
print("=" * 98)
print("BLOCO E — a família linguística é REUTILIZÁVEL (sem nomes do caso)")
print("=" * 98)
_GEN = [
    ("G1", "Por que tantas companhias do agronegócio pedem recuperação "
           "judicial?", "Banco do Brasil"),
    ("G2", "Grandes varejistas estão solicitando recuperação judicial em "
           "série", "Itaú Unibanco"),
    ("G3", "Construtoras entram com pedido de falência após aperto no "
           "crédito", "Itaú Unibanco", "falencia"),
    ("G4", "Empresas do setor elétrico requerem recuperação judicial, aponta "
           "levantamento", "Banco do Brasil"),
    ("G5", "Fornecedores pediram recuperação judicial e afetam a cadeia",
     "Vale"),
]
for item in _GEN:
    tag, t, emp = item[0], item[1], item[2]
    ev = item[3] if len(item) > 3 else "recuperacao_judicial"
    s, r, _ = dec(t, emp, ev)
    check(s is False, f"[25..29] {tag}: coletivo genérico verbal não vira "
                      f"evento de {emp} ({r or 'SEM REGRA'})")

print()
print("=" * 98)
print("BLOCO F — o SINGULAR genérico NÃO é coletivo (empresa específica)")
print("=" * 98)
check(not sa.detect_insolvencia_setorial(
    "Empresa chilena de lácteos solicita su quiebra: vendía a grandes "
    "supermercados", "Salfacorp", ["Salfacorp"]),
    "[30] 'empresa chilena de lácteos solicita' é companhia específica, "
    "não setor")
# Este segundo texto JÁ era capturado antes desta onda, pelo padrão nominal
# "pedidos? de falência" (o primeiro da lista). Comportamento pré-existente,
# idêntico com e sem o fix — verificado. O que esta onda tem de provar é que o
# padrão VERBAL NOVO não é quem o pega: ele exige plural.
_PAT_VERBAL = sa._INSOLV_SUJEITO_COLETIVO[-1]
check(not re.search(_PAT_VERBAL, sa._n(
    "Empresa responsável pela Storj entra com pedido de falência nos EUA"),
    re.I),
    "[31] o padrão verbal NOVO não casa com 'empresa' no singular")

print()
print("=" * 98)
print("BLOCO G — o detector isolado, no par exato")
print("=" * 98)
_r = sa.detect_insolvencia_setorial(EXATO, "Tok&Stok", ["Tok&Stok"])
check(bool(_r), "[32] detecta o caso adjudicado")
check("pedindo" in (_r.get("sujeito_coletivo") or ""),
      f"[33] a evidência captura o predicado VERBAL "
      f"({_r.get('sujeito_coletivo')!r})")
check(not sa.detect_insolvencia_setorial(
    "Tok&Stok pede recuperação judicial", "Tok&Stok", ["Tok&Stok"]),
    "[34] não atua quando a monitorada é a devedora")
check(not sa.detect_insolvencia_setorial(
    "Tok&Stok anuncia aquisição da Alfa", "Tok&Stok", ["Tok&Stok"]),
    "[35] não atua sem insolvência no texto")

print()
print("=" * 98)
print("BLOCO H — o artigo ruim não pode mais ser representante da ocorrência")
print("=" * 98)
# Regressão de `build_evolution` SEM tocar em clustering de ocorrência: com o
# artigo semanticamente descartado, o representante volta a ser o irmão
# legítimo (regra de máximo `contrib` inalterada).
# Datas RELATIVAS ao instante do teste: assim a regressão não envelhece nem
# depende do relógio do dia em que roda. O irmão legítimo fica a 34 dias e o
# artigo setorial no presente — a mesma distância temporal do caso real, que
# é o que dá ao artigo ruim a vantagem de decaimento.
_AGORA = int(time.time())
_TS_IRMAO = _AGORA - 34 * 86400
_TS_SETORIAL = _AGORA - 3600
_HIST = {"articles": {
    "https://exemplo.invalido/aceita": {
        "url": "https://exemplo.invalido/aceita", "domain": "exemplo.invalido",
        "title": "Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão",
        "summary": "", "source": "NSC Total",
        "pub_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(_TS_IRMAO)),
        "pub_ts": _TS_IRMAO, "companies": ["Tok&Stok"],
        "event_ids": ["recuperacao_judicial"],
        "events_by_company": {"Tok&Stok": ["recuperacao_judicial"]}},
    "https://exemplo.invalido/setorial": {
        "url": "https://exemplo.invalido/setorial", "domain": "exemplo.invalido",
        "title": EXATO, "summary": "", "source": "O GLOBO",
        "pub_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(_TS_SETORIAL)),
        "pub_ts": _TS_SETORIAL,
        "companies": ["Tok&Stok"], "event_ids": ["recuperacao_judicial"],
        "events_by_company": {"Tok&Stok": ["recuperacao_judicial"]}},
}}
_D_IRMAO = time.strftime("%Y-%m-%d", time.localtime(_TS_IRMAO))
_D_SETORIAL = time.strftime("%Y-%m-%d", time.localtime(_TS_SETORIAL))
_antes = {e["company"]: e for e in rd.build_evolution(json.loads(json.dumps(_HIST)), cfg)}
_a = _antes.get("Tok&Stok")
check(_a is not None and len(_a["timeline"]) == 1,
      "[36] antes: uma única ocorrência (a camada de ocorrência já deduplicava)")
check(_a is not None and _a["breakdown"][0]["date"] == _D_SETORIAL,
      f"[37] antes: o representante era o artigo setorial "
      f"({_a['breakdown'][0]['date'] if _a else '-'})")

# reclassifica pelo caminho canônico — é isto que o fix muda
_H2 = json.loads(json.dumps(_HIST))
for _u, _r2 in _H2["articles"].items():
    _art = {"title": _r2["title"], "summary": _r2["summary"],
            "source": _r2["source"], "url": _r2["url"], "domain": _r2["domain"]}
    rd.classify_and_attribute(_art, cfg)
    _r2["events_by_company"] = _art.get("events_by_company") or {}
    _r2["event_ids"] = [e["id"] for e in (_art.get("events") or [])]
_dep = {e["company"]: e for e in rd.build_evolution(_H2, cfg)}
_b = _dep.get("Tok&Stok")
check(_b is not None, "[38] a Tok&Stok NÃO some do radar")
check(_b is not None and len(_b["timeline"]) == 1,
      f"[39] depois: continua UMA ocorrência — a RJ verdadeira não foi apagada "
      f"({len(_b['timeline']) if _b else 0})")
check(_b is not None and _b["breakdown"][0]["date"] == _D_IRMAO,
      f"[40] depois: o representante é o irmão legítimo "
      f"({_b['breakdown'][0]['date'] if _b else '-'})")
check(_b is not None and "Justiça aceita" in _b["breakdown"][0]["title"],
      "[41] e é o artigo que de fato afirma a RJ")
check(_a is not None and _b is not None and _b["total_score"] < _a["total_score"],
      f"[42] a inflação por recência é removida "
      f"({_a['total_score'] if _a else 0} -> {_b['total_score'] if _b else 0})")
check(_b is not None and _b["hard_critical"] is True,
      "[43] e a RJ verdadeira mantém a condição de crítico duro")

print()
print("=" * 98)
print("BLOCO I — as três verdades humanas da auditoria estão persistidas")
print("=" * 98)
FIX = "test_fixtures_reliability/occurrence_currentness_reviews.json"
_fx = json.load(io.open(FIX, encoding="utf-8"))
_por_id = {v.get("article_id"): v for k, v in _fx.items() if k != "_meta"}

_brf = _por_id.get("972a2d5f184545235f9d")
check(_brf is not None, "[44] BRF persistida")
check(_brf and _brf.get("event_asserted") == "ASSERTED",
      f"[45] BRF: AFIRMA o adiamento — não é MENTIONED_ONLY "
      f"({_brf.get('event_asserted') if _brf else '-'})")
check(_brf and _brf.get("currentness") == "HISTORICAL",
      "[46] BRF: atualidade HISTORICAL")
check(_brf and _brf.get("occurrence_novelty") == "HISTORICAL_CONTEXT",
      "[47] BRF: novidade HISTORICAL_CONTEXT")
check(_brf and _brf.get("human_scoreable") is False,
      "[48] BRF: não pontuável")
check(_brf and _brf.get("failure_dimension") == "occurrence_currentness",
      f"[49] BRF fica FORA da família menção-como-asserção "
      f"({_brf.get('failure_dimension') if _brf else '-'})")

_amb = _por_id.get("527b96378703e4cba06a")
check(_amb is not None, "[50] Ambev persistida")
check(_amb and _amb.get("occurrence_novelty") == "DESCRIPTOR_OR_BACKGROUND",
      "[51] Ambev: descritor/fundo")
check(_amb and _amb.get("event_asserted") == "MENTIONED_ONLY",
      "[52] Ambev: menciona, não anuncia")
check(_amb and _amb.get("human_scoreable") is False,
      "[53] Ambev: não pontuável")
check(_amb and _amb.get("failure_dimension") == "event_assertion",
      "[54] Ambev entra na família de asserção")

_aeg = _por_id.get("ede6b04347e5b2f36c16")
check(_aeg is not None, "[55] Aegea persistida com o article_id COMPLETO")
check(_aeg and _aeg.get("company_role") == "mencionada",
      f"[56] Aegea: papel = mencionada ({_aeg.get('company_role') if _aeg else '-'})")
check(_aeg and _aeg.get("event_asserted") == "MENTIONED_ONLY",
      "[57] Aegea: menção em lista/setor")
check(_aeg and _aeg.get("occurrence_novelty") == "DESCRIPTOR_OR_BACKGROUND",
      "[58] Aegea: descritor/fundo")
check(_aeg and _aeg.get("human_scoreable") is False,
      "[59] Aegea: não pontuável")
check(_aeg and _aeg.get("event_id") == "follow_on",
      "[60] Aegea: família follow_on — a lacuna existe fora da insolvência")

_dims = {_por_id[k].get("failure_dimension") for k in
         ("972a2d5f184545235f9d", "527b96378703e4cba06a",
          "ede6b04347e5b2f36c16") if k in _por_id}
check(_dims == {"occurrence_currentness", "event_assertion"},
      f"[61] os três NÃO colapsam num rótulo só ({sorted(_dims)})")
check(all(_por_id[k].get("reviewer_type") == "human" for k in
          ("972a2d5f184545235f9d", "527b96378703e4cba06a",
           "ede6b04347e5b2f36c16") if k in _por_id),
      "[62] os três são adjudicação humana")
check("none" in (_fx["_meta"].get("production_authority") or ""),
      "[63] o fixture declara autoridade de produção NENHUMA")

print()
print("=" * 98)
print("BLOCO J — nada de portão genérico de asserção")
print("=" * 98)
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
_codigo = "\n".join(l.split("#")[0] for l in _src.splitlines())
check("event_asserted" not in _codigo,
      "[64] nenhum conceito genérico `event_asserted` entrou em produção")
check('return {"relation_type": "direto", "subject_company": ""' in _codigo,
      "[65] o fallback de `mention_role` segue intacto")
check('_indireto = (_role.get("relation_type") not in ("direto",)' in _codigo,
      "[66] a condição de roteamento segue intacta")
_sa_bruto = io.open("semantic_audit.py", encoding="utf-8").read()
# so CODIGO: o comentario da regra cita os nomes do caso de
# proposito, para explicar a fronteira. Hardcode seria no codigo.
_sa = "\n".join(l.split("#")[0] for l in _sa_bruto.splitlines())
for _nome in ("Tok&Stok", "TokStok", "Casas Bahia", "Marabraz", "Mobly",
              "oglobo", "O GLOBO"):
    check(_nome not in _sa, f"[67..73] nenhum hardcode de {_nome!r} na regra")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7d (coletivo verbal em RJ): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
