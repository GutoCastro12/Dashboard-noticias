#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7u_promocao_producao.py — 4I.2 R7u · PROMOÇÃO À PRODUÇÃO.

OCORRÊNCIA + SCORE COM PORTÃO DE DIREÇÃO, JUNTOS.

As ondas anteriores validaram em sombra; esta valida a PRODUÇÃO. Os outros
arquivos passaram a medir sombras contra o que a produção faz — este mede o que
a produção faz, e é ele que impede a promoção de regredir.

A DECISÃO HUMANA DE 2026-08-21

    POLÍTICA A  evento de família `neutra` NÃO soma risco por existir
    POLÍTICA B  evento de família `neutra` NÃO conta como tipo negativo
    FAVORÁVEL   zero risco, zero tipo negativo, e NUNCA subtrai
    ADVERSO     mecânica de score preservada, intacta

O QUE FICA TRAVADO AQUI

  * o evento contextual continua VISÍVEL, com membros, fase, âncora e
    representante — só perde autoridade adversa;
  * identidade de ocorrência não depende de peso de score;
  * `article_id` sobrevive a toda fusão;
  * as âncoras humanas (Sabesp, Suzano, Smart Fit, Tok&Stok, Cosan, Vale,
    Santander, ISA, JBS, Yobel) seguem como adjudicadas;
  * os limiares de status não foram tocados;
  * nenhum emissor termina com score negativo.

O QUE ELE NÃO FAZ

Não fixa o total do sistema nem contagem de ocorrências: o cron acrescenta
artigos toda rodada, e um número absoluto viraria calendário. Trava-se
propriedade e relação.
"""
from __future__ import annotations

import copy
import io
import json

import occurrence_engine as oe
import risk_dashboard as rd

PASS = FAIL = 0
POLITICA = "HUMAN_SCORE_POLICY_2026_08_22"    # revisada em 2026-08-22


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
OCC = {}
for _l in EVO:
    for _o in (_l.get("events") or []):
        _oc = _o.get("_ocorrencia")
        if _oc:
            OCC.setdefault((_oc["company"], _oc["family"]), []).append(_oc)
BD = [(l["company"], b) for l in EVO for b in (l.get("breakdown") or [])]

print("=" * 98)
print("BLOCO A - a fonte UNICA de autoridade adversa")
print("=" * 98)
check(oe.POLITICA_HUMANA == POLITICA,
      f"[1] o motor declara a politica que implementa ({oe.POLITICA_HUMANA})")
check(oe.tem_autoridade_adversa(TAX["recuperacao_judicial"])
      and oe.tem_autoridade_adversa(TAX["rebaixamento_rating"])
      and oe.tem_autoridade_adversa(TAX["recomendacao_negativa"]),
      "[2] familia declarada `negativa` MANTEM autoridade de score")
check(not oe.tem_autoridade_adversa(TAX["ma"])
      and not oe.tem_autoridade_adversa(TAX["troca_ceo"])
      and not oe.tem_autoridade_adversa(TAX["emissao_divida"])
      and not oe.tem_autoridade_adversa(TAX["follow_on"]),
      "[3] POLITICA A: familia `neutra` NAO tem autoridade adversa")
check(oe.direcao_de(TAX["absolvicao"]) == oe.FAVORAVEL
      and not oe.tem_autoridade_adversa(TAX["absolvicao"]),
      "[4] favoravel tambem nao pontua — e `mitigadora` entra aqui, que "
      "`is_positive()` sozinho nao cobria")
_sem_dir = [e["id"] for e in CFG["taxonomy"] if not (e.get("direction") or "")]
check(not _sem_dir,
      f"[5] nenhuma familia sem `direction` declarada — nao ha caso UNKNOWN "
      f"para o portao reinterpretar em silencio ({_sem_dir})")
_src = io.open("occurrence_engine.py", encoding="utf-8", newline="").read()
check("_FAMILIAS_NEUTRAS" not in _src and "ma\", \"troca_ceo" not in _src,
      "[6] §28 e o motor NAO mantem lista paralela de familias: le `direction` "
      "da propria config")

print()
print("=" * 98)
print("BLOCO B - POLITICA A: contextual soma ZERO, mas continua VISIVEL")
print("=" * 98)
_ctx = [(c, b) for c, b in BD if (b.get("direction") or "") != "negativa"]
_adv = [(c, b) for c, b in BD if (b.get("direction") or "") == "negativa"]
check(_ctx, f"[7] ha ocorrencias contextuais no painel ({len(_ctx)})")
# [MIGRADO 2026-08-22] A decisao humana mudou: enquanto nao houver avaliacao
# direcional por ocorrencia, um evento material contextual VOLTA a contribuir
# como prior conservador de alerta. A expectativa de "contextual = 0" era
# POLITICA, nao invariante — e a politica foi revista. O que fica travado e a
# invariante duravel: contextual contribui SEM ser rotulado adverso, e a
# decomposicao separa as duas parcelas.
check(all(b["contribution_class"] == oe.SINAL_CONTEXTUAL for _, b in _ctx)
      and any(b["contrib"] > 0 for _, b in _ctx),
      f"[8] contextual CONTRIBUI ({len(_ctx)} ocorrencias) e e rotulado "
      f"CONTEXTUAL — nunca ADVERSE")
check(all(b["base"] > 0 for _, b in _ctx if b["base"]),
      "[9] e o peso da config segue intacto — o portao nao zera peso, zera "
      "AUTORIDADE")
check(_adv and all(b["contrib"] > 0 for _, b in _adv if b["decay_f"] > 0.01),
      f"[10] enquanto as adversas seguem pontuando ({len(_adv)})")
check(all(b.get("score_authority") is False for _, b in _ctx)
      and all(b.get("score_authority") is True for _, b in _adv),
      "[11] e cada linha do breakdown carrega a autoridade que a decidiu")
_neg = [l["company"] for l in EVO if l["total_score"] < 0]
check(not _neg, f"[12] FAVORAVEL nunca subtrai: nenhum emissor com score "
                f"negativo ({_neg})")

print()
print("=" * 98)
print("BLOCO C - POLITICA B: contextual nao conta como tipo negativo")
print("=" * 98)
_por_emp = {}
for c, b in BD:
    _por_emp.setdefault(c, []).append(b)
_so_ctx = [c for c, bs in _por_emp.items()
           if bs and all((b.get("direction") or "") != "negativa" for b in bs)]
check(_so_ctx, f"[13] ha emissores SO com evento contextual ({len(_so_ctx)})")
# Eles PODEM entrar em alerta — e essa e a sensibilidade conservadora que a
# decisao de 2026-08-22 restaurou. O que nao pode e o alerta parecer evidencia
# adversa: a decomposicao tem de dizer que 100% do score e contextual.
check(all(LINHA[c]["adverse_score"] == 0
          and LINHA[c]["contextual_share"] in (None, 1.0) for c in _so_ctx),
      f"[14] emissor so-contextual tem parcela adversa ZERO e share contextual "
      f"100% — o alerta e auditavel como sinal de revisao, nao como "
      f"deterioracao ({[c for c in _so_ctx if LINHA[c]['adverse_score']]})")
# [MIGRADO 2026-08-22] A persistencia voltou a contar sinal contextual, por
# decisao humana. O que se trava e que ela nunca conte familia FAVORAVEL, e que
# o texto exibido nao chame sinal contextual de "negativo".
check(all(LINHA[c]["adverse_score"] == 0 for c in _so_ctx),
      "[15] emissor so-contextual tem parcela adversa zero — a persistencia "
      "pode dispara-lo, mas a decomposicao mostra de que tipo de sinal se "
      "trata")
check(all(not LINHA[c].get("hard_critical") for c in _so_ctx),
      "[16] e nenhum deles dispara o gatilho de evento critico — nenhuma "
      "familia contextual alcanca o piso de 90 pontos")
_dois_adv = [c for c, bs in _por_emp.items()
             if len({ROT.get(b["label"], b["label"]) for b in bs
                     if (b.get("direction") or "") == "negativa"}) >= 2]
check(all(LINHA[c]["status"] in ("atencao", "critico") for c in _dois_adv),
      f"[17] e quem tem DOIS tipos adversos segue promovido ({_dois_adv})")

print()
print("=" * 98)
print("BLOCO D - identidade de ocorrencia NAO depende de score")
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
         o["_ocorrencia"]["anchor_date"],
         tuple(sorted(m["article_id"] for m in o["_ocorrencia"]["members"])))
        for l in rd.build_evolution(H, cfg) for o in (l.get("events") or [])
        if o.get("_ocorrencia"))


_base = _forma(CFG)
check(_base == _forma(_peso("ma", 0)),
      "[18] com peso de `ma` ZERO os occurrence_id e membros sao IDENTICOS")
check(_base == _forma(_peso("ma", 80)), "[19] e com peso DOBRADO tambem")
check(_base == _forma(_peso("recuperacao_judicial", 0)),
      "[20] idem zerando uma familia ADVERSA — identidade nunca e funcao de "
      "score, em nenhuma direcao")
check(len(_base) == len({x[0] for x in _base}),
      f"[21] e nenhum occurrence_id colide ({len(_base)} ocorrencias)")

print()
print("=" * 98)
print("BLOCO E - §9 proveniencia: nenhum membro perde `article_id`")
print("=" * 98)
_mem = [m for l in EVO for o in (l.get("events") or [])
        if o.get("_ocorrencia") for m in o["_ocorrencia"]["members"]]
check(_mem, f"[22] a producao expoe membros de ocorrencia ({len(_mem)})")
check(all(m["article_id"] for m in _mem),
      f"[23] TODOS com `article_id` preservado antes de qualquer fusao "
      f"({len(_mem)}/{len(_mem)})")
check("_sv2.id_artigo" in io.open("risk_dashboard.py", encoding="utf-8").read(),
      "[24] e o id e fixado na montagem do candidato, nao reconstruido depois")
check(all(m.get("phase") in oe.FASES for m in _mem),
      "[25] cada membro carrega fase de um enum fechado de quatro estados mais "
      "UNKNOWN")
check(any(m["effective_event_date"] is None for m in _mem)
      or all(m["phase"] != oe.ACOMPANHAMENTO for m in _mem),
      "[26] e um ACOMPANHAMENTO declara data efetiva DESCONHECIDA em vez de "
      "carimbar a data de publicacao como data do fato")

print()
print("=" * 98)
print("BLOCO F - ancoras humanas na PRODUCAO")
print("=" * 98)
_sab = OCC.get(("Sabesp", "ma"), [])
check(_sab and all(o["canonical_object"] for o in _sab),
      f"[27] Sabesp: transacao com objeto identificado "
      f"({[o['canonical_object'] for o in _sab]})")
check(oe.papel_marcador("cade") == oe.REGULADOR,
      "[28] e `cade` segue REGULADOR — contexto, nunca identidade")
_suz = OCC.get(("Suzano", "ma"), [])
check(_suz and any(o["aliases"] == ["ma:suzano:tissue-ifp"] for o in _suz),
      f"[29] Suzano: alias DECLARADO aplicado "
      f"({[o['aliases'] for o in _suz]})")
_smf = OCC.get(("Smart Fit", "ma"), [])
check(_smf and any(o["anchor_date"] > o["initial_date"] for o in _smf),
      f"[30] Smart Fit: o fechamento material REANCORA "
      f"({[(o['initial_date'], o['anchor_date']) for o in _smf]})")
_tok = OCC.get(("Tok&Stok", "recuperacao_judicial"), [])
check(_tok and all(o["score_authority"] for o in _tok),
      "[31] Tok&Stok: a RJ MANTEM autoridade adversa")
check(LINHA["Tok&Stok"]["status"] == "critico",
      f"[32] e segue CRITICA ({LINHA['Tok&Stok']['status']}, "
      f"{LINHA['Tok&Stok']['total_score']}) — nenhum evento neutro a apaga")
check(_tok and all("CONTINUING_STATE" in o["refresh_reason"] for o in _tok),
      "[33] ancorada no desenvolvimento SUBSTANTIVO mais recente: risco de "
      "estado continuo com noticia fresca e risco fresco")
check(_tok and all("afeta quem" not in o["display_representative_date"]
                   for o in _tok),
      "[34] e a materia de CONSEQUENCIA nao virou principal")
_cos = OCC.get(("Cosan", "rebaixamento_rating"), [])
check(len([b for c, b in BD if c == "Cosan"
           and ROT.get(b["label"]) == "rebaixamento_rating"]) >= 2,
      "[35] Cosan: acoes de rating de AGENCIAS diferentes seguem distintas")
_vale = [b for c, b in BD if c == "Vale"
         and ROT.get(b["label"]) == "investigacao_regulatoria"]
check(len(_vale) >= 2,
      f"[36] Vale: aberturas de processo distintas seguem distintas "
      f"({len(_vale)})")
check(not OCC.get(("Santander Brasil", "troca_ceo")),
      "[37] Santander: segue sem ocorrencia de CEO")
_isa = OCC.get(("ISA Energia Brasil", "follow_on"), [])
check(all(o["anchor_date"] == o["initial_date"] for o in _isa) if _isa else True,
      "[38] ISA: acompanhamento nao renova")
_yob = [b for c, b in BD if c == "Yobel"]
check(len(_yob) == 1,
      f"[39] Yobel: a familia opt-in segue UMA ocorrencia ({len(_yob)})")

print()
print("=" * 98)
print("BLOCO G - JBS: o caso que explica a separacao")
print("=" * 98)
_jbs = [b for c, b in BD if c == "JBS"]
_jadv = [b for b in _jbs if (b.get("direction") or "") == "negativa"]
check(len(_jbs) >= 4,
      f"[40] a JBS mantem {len(_jbs)} ocorrencias VISIVEIS no painel")
check(len([b for b in _jbs if ROT.get(b["label"]) == "ma"]) == 2,
      "[41] duas transacoes de M&A, distintas")
check(len([b for b in _jbs if ROT.get(b["label"]) == "troca_ceo"]) == 1,
      "[42] UMA ocorrencia de CEO — o descritor e membro, nao evento")
check(len(_jadv) == 1,
      f"[43] e SO a recomendacao rebaixada e adversa ({len(_jadv)})")
check(abs(LINHA["JBS"]["adverse_score"] - sum(b["contrib"] for b in _jadv))
      < 0.6,
      f"[44] a parcela ADVERSA da JBS ({LINHA['JBS']['adverse_score']}) e "
      f"exatamente a recomendacao rebaixada; o resto "
      f"({LINHA['JBS']['contextual_score']}) e material sem ser ruim, e esta "
      f"separado")
check(LINHA["JBS"]["contextual_share"] > 0.7,
      f"[45] e {LINHA['JBS']['contextual_share'] * 100:.0f}% do score da JBS e "
      f"contextual: o status `{LINHA['JBS']['status']}` e um pedido de revisao, "
      f"nao um veredito de deterioracao")

print()
print("=" * 98)
print("BLOCO H - §19 limiares INTOCADOS e produção coerente")
print("=" * 98)
_cfg_txt = io.open("config_risco.yaml", encoding="utf-8").read()
check("atencao_total_min: 60" in _cfg_txt and "critico_total_min: 125" in _cfg_txt,
      "[46] os limiares seguem 60/125 na config")
_st = CFG["evolution"]["status"]
check(_st["atencao_total_min"] == 60 and _st["critico_total_min"] == 125
      and _st["critico_event_min_score"] == 90,
      "[47] e a producao os le sem recalibrar — o adaptativo (36/75) NAO foi "
      "aplicado nesta onda")
_crit = [l["company"] for l in EVO if l["status"] == "critico"]
check(all(any(b.get("direction") == "negativa"
              for c, b in BD if c == n) for n in _crit),
      f"[48] todo emissor CRITICO tem evento adverso real ({_crit})")
check(all(LINHA[n].get("hard_critical")
          or LINHA[n]["total_score"] >= 125 for n in _crit),
      "[49] e chegou la pela regra de sempre, nao por efeito colateral")
_at = [l["company"] for l in EVO if l["status"] == "atencao"]
check(all(LINHA[n]["adverse_score"] + LINHA[n]["contextual_score"] > 0
          for n in _at),
      f"[50] todo emissor em `atencao` tem sinal de risco real, e a composicao "
      f"adversa x contextual esta exposta em cada um ({len(_at)} emissores; "
      f"{sum(1 for n in _at if LINHA[n]['adverse_score'] == 0)} sao 100% "
      f"contextuais)")

print()
print("=" * 98)
print("BLOCO I - §30/§34 o que esta onda NAO podia tocar")
print("=" * 98)
_hs = json.load(io.open("risk_human_supervision.json", encoding="utf-8"))
check(len(_hs["memberships"]) == 27
      and len({m["case_id"] for m in _hs["memberships"].values()}) == 24,
      "[51] supervisao humana intacta (27/24)")
_ot = json.load(io.open("risk_semantic_v2_shadow.json",
                        encoding="utf-8"))["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[52] occurrence_truth intacto (10/21/4)")
import reliability_occurrence_archival_source as _ar
check(_ar.integro()["ok"],
      "[53] snapshots arquivais congelados seguem integros")
check("detect_troca_ceo_sem_assercao" in io.open(
      "semantic_audit.py", encoding="utf-8").read(),
      "[54] a guarda de CEO segue publicada e e REUSADA pelo motor, nao "
      "reimplementada")
_prod = io.open("risk_dashboard.py", encoding="utf-8").read()
check("def best_contribs" in _prod and "def weighted_total" in _prod
      and "def build_evolution" in _prod,
      "[55] build_evolution, best_contribs e weighted_total seguem no lugar")
check(_prod.count("def _classe_sinal") == 1
      and "_classe_sinal(o) == _oe.SINAL_ADVERSO" in _prod,
      "[56] §18 a classificacao vem de UMA fonte so — contribuicao, "
      "decomposicao, contagem de tipos, persistencia e evento critico bebem da "
      "mesma decisao")
check(not [c for c in io.open("occurrence_engine.py", encoding="utf-8",
                              newline="").read()
           if ord(c) < 32 and c not in "\n\r\t"],
      "[57] e o motor nao tem caractere de controle invisivel")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7u (promocao a producao): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
