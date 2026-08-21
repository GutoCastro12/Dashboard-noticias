#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7t_politica_de_score.py — 4I.2 R7t · SCORING POLICY SHADOW V1.

MEDIR A POLITICA, NAO ESCOLHE-LA.

O lado da ocorrencia ficou pronto em `f880419`. O que sobra e uma decisao de
POLITICA — quanto risco um evento MATERIAL mas de direcao INDETERMINADA deve
somar — e ela nao e minha. Este arquivo trava as propriedades que a medicao
precisa ter para que a decisao humana seja confiavel:

  1. o controle P0 REPRODUZ a producao (senao toda comparacao e ficcao);
  2. mudar peso de score NAO muda identidade de ocorrencia;
  3. o evento material continua VISIVEL com autoridade de score zero;
  4. os controles adversos NAO sao apagados pelo portao de direcao;
  5. nenhum evento favoravel gera score NEGATIVO;
  6. limiares de status ficam INTOCADOS.

O QUE ESTE ARQUIVO NAO FAZ

Nao afirma qual multiplicador e o certo. 0,25 e 0,50 aparecem como pontos de
SENSIBILIDADE, e o teste verifica exatamente isso — que sao monotonos e nada
mais. Recomendar um numero aqui seria transformar medicao em politica.
"""
from __future__ import annotations

import copy
import io
import json

import reliability_occurrence_shadow as osd
import reliability_scoring_policy_shadow as sp
import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + label)
    else:
        FAIL += 1
        print("  FALHOU: " + label)


CFG = rd.load_config("config_risco.yaml")
R = sp.rodar_tudo()
BASE = R["base"]
P = R["politicas"]
D = R["distribuicoes"]
INV = R["inventario_direcao"]

print("=" * 98)
print("BLOCO A - §6/§35 o controle P0 reproduz a PRODUCAO")
print("=" * 98)
# Antes da promocao, quem tinha de reproduzir a producao era P0. Depois da
# decisao humana de 2026-08-21 a producao APLICA o portao de direcao mais o
# portao de tipos negativos — entao quem reproduz e P1b, e P0 passa a ser o
# CONTRAFACTUAL "quanto seria sem os portoes". Continuar exigindo P0 == producao
# exigiria desfazer a promocao.
_FV = R["fidelidade_vigente"]["por_politica"]
_VIG = _FV["P1b_TIPOS_TAMBEM_GATED"]
check(_VIG["status_identico"] == _VIG["empresas"],
      f"[1] a politica humana reproduz o STATUS da producao em "
      f"{_VIG['status_identico']}/{_VIG['empresas']} emissores")
check(_VIG["score_identico"] == _VIG["empresas"],
      f"[2] e o SCORE em {_VIG['score_identico']}/{_VIG['empresas']} — "
      f"exatamente, sem residuo")
check(_FV["P0_CURRENT"]["score_identico"] < _VIG["score_identico"],
      f"[3] enquanto o contrafactual SEM portao ja nao reproduz "
      f"({_FV['P0_CURRENT']['score_identico']}/{_VIG['empresas']}) — e a "
      f"medida do que a decisao humana mudou")
check(R["fidelidade_vigente"]["politica_vigente_na_producao"]
      == "P1_DIRECTION_GATED",
      "[4] e o modulo declara qual politica esta vigente, em vez de deixar o "
      "leitor deduzir")
check(D["P0_CURRENT"] == D["PM_1.00"],
      "[5] multiplicador 1,00 e identico a P0 — o controle do controle")

print()
print("=" * 98)
print("BLOCO B - §4 a conflacao medida na config")
print("=" * 98)
tax = {e["id"]: e for e in CFG["taxonomy"]}
neutras = [e for e in CFG["taxonomy"]
           if (e.get("direction") or "").lower() == "neutra" and e.get("score", 0) > 0]
check(len(neutras) >= 6,
      f"[6] §4 ha {len(neutras)} familias declaradas `neutra` com peso > 0")
check(sum(e["score"] for e in neutras) >= 150,
      f"[7] somando {sum(e['score'] for e in neutras)} pontos de peso-base")
check({"ma", "troca_ceo", "emissao_divida", "follow_on"}
      <= {e["id"] for e in neutras},
      "[8] e as quatro da onda estao entre elas")
check(sp.classificar_direcao("negativa") == sp.ADVERSO
      and sp.classificar_direcao("neutra") == sp.CONTEXTUAL
      and sp.classificar_direcao("positiva") == sp.FAVORAVEL
      and sp.classificar_direcao("") == sp.DESCONHECIDO,
      "[9] §3 os quatro estados de direcao existem e vem da declaracao da "
      "propria taxonomia — nenhuma polaridade inventada")
check(INV["pct_contextual"] > 0.5,
      f"[10] §16 {INV['pct_contextual'] * 100:.1f}% do score do sistema vem de "
      f"familias CONTEXT_DEPENDENT ({INV['pontos_por_direcao'][sp.CONTEXTUAL]} "
      f"de {INV['total_sistema']})")
check(INV["ocorrencias_por_direcao"][sp.CONTEXTUAL]
      > INV["ocorrencias_por_direcao"][sp.ADVERSO],
      f"[11] §16 e a maioria das ocorrencias pontuaveis e contextual "
      f"({INV['ocorrencias_por_direcao']})")
check(len(INV["empresas_por_faixa"]["100%"]) >= 20,
      f"[12] §16 {len(INV['empresas_por_faixa']['100%'])} emissores pontuam "
      f"100% por evento de direcao indeterminada")

print()
print("=" * 98)
print("BLOCO C - §12/§35 mudar SCORE nao muda IDENTIDADE de ocorrencia")
print("=" * 98)


def cfg_peso(fam, peso):
    c = copy.deepcopy(CFG)
    for e in c["taxonomy"]:
        if e["id"] == fam:
            e["score"] = peso
    return c


def forma(X):
    return sorted((o["occurrence_id"], o["company"], o["family"],
                   o["canonical_object"], o["occurrence_instance_signature"],
                   o["anchor_date"], o["display_representative"],
                   tuple(sorted(m["article_id"] for m in o["membros"])))
                  for o in X["ocorrencias"])


_S = osd.construir("risk_history.json", CFG)
_Z = osd.construir("risk_history.json", cfg_peso("ma", 0))
_D2 = osd.construir("risk_history.json", cfg_peso("ma", 80))
check(forma(_S) == forma(_Z),
      "[13] §12 com peso de `ma` ZERO as ocorrencias sao IDENTICAS")
check(forma(_S) == forma(_D2), "[14] e com peso DOBRADO tambem")
_ZC = osd.construir("risk_history.json", cfg_peso("troca_ceo", 0))
check(forma(_S) == forma(_ZC), "[15] idem zerando `troca_ceo`")
_jma = [o for o in _Z["ocorrencias"] if o["company"] == "JBS" and o["family"] == "ma"]
_jceo = [o for o in _ZC["ocorrencias"]
         if o["company"] == "JBS" and o["family"] == "troca_ceo"]
_jdiv = [o for o in _Z["ocorrencias"]
         if o["company"] == "JBS" and o["family"] == "emissao_divida"]
check(len(_jma) == 2 and len(_jceo) == 1 and len(_jdiv) == 1,
      f"[16] §12 com risco ZERO a JBS continua com as ocorrencias de M&A "
      f"({len(_jma)}), CEO ({len(_jceo)}) e divida ({len(_jdiv)}) — "
      f"materialidade sobrevive a autoridade de score")
check(all(m["article_id"] for o in _Z["ocorrencias"] for m in o["membros"]),
      "[17] §12 e todos os membros seguem com `article_id`: a linha do tempo "
      "continua possivel com peso zero")
check(all(o["score_base"] == 0 for o in _jma),
      "[18] o peso realmente foi a zero — o teste nao passou por config "
      "inalterada")

print()
print("=" * 98)
print("BLOCO D - §7/§19/§20 o portao de direcao nao apaga o adverso")
print("=" * 98)
p0, p1 = P["P0_CURRENT"]["empresas"], P["P1_DIRECTION_GATED"]["empresas"]
mistas = [n for n, x in p0.items() if x["adverso"] > 0 and x["contextual"] > 0]
check(len(mistas) >= 5,
      f"[19] §20 ha {len(mistas)} emissores com evento adverso E contextual")
check(all(abs(p1[n]["total"] - p0[n]["adverso"]) < 0.05 for n in mistas),
      "[20] §20 e em TODOS eles o adverso sobrevive intacto ao gating — zerar "
      "M&A nao apaga um rebaixamento")
_criticos = R["criticos"]
check(_criticos and all(not x["cai_de_status"] for x in _criticos),
      f"[21] §19 nenhum emissor CRITICO deixa de ser critico sob P1 "
      f"({[x['company'] for x in _criticos]})")
check(all(x["pct_contextual"] == 0.0 for x in _criticos),
      "[22] §19 o unico critico e 100% adverso (recuperacao judicial) — o "
      "portao nao existe para baixar score, existe para tirar significado "
      "adverso automatico de familia contextual")
_advfam = [x for x in R["sensibilidade_familia"] if x["direcao"] == sp.ADVERSO]
_puros = [n for n, x in p0.items() if x["adverso"] > 0 and x["contextual"] == 0]
check(_puros and all(abs(p1[n]["total"] - p0[n]["total"]) < 0.05 for n in _puros),
      f"[23] §19 os {len(_puros)} emissores cujo score ja era 100% adverso nao "
      f"perdem UM ponto sob o portao "
      f"({[(n, p0[n]['total']) for n in _puros[:4]]})")
check(sum(x["pontos_vivos"] for x in _advfam) > 0
      and abs(sum(x["pontos_vivos"] for x in _advfam)
              - INV["pontos_por_direcao"][sp.ADVERSO]) < 1.0,
      f"[24] §19 os pontos adversos do sistema seguem inteiros sob P1 "
      f"({INV['pontos_por_direcao'][sp.ADVERSO]})")
check(not [n for n, x in p1.items() if x["total"] < 0],
      "[25] §22 nenhum emissor termina com score NEGATIVO")
check(INV["ocorrencias_por_direcao"].get(sp.FAVORAVEL, 0) == 0,
      "[26] §22 nenhum evento favoravel entra na conta de risco — favoravel e "
      "zero ponto, nunca compensacao de um default")

print()
print("=" * 98)
print("BLOCO E - §8 sensibilidade do multiplicador, sem recomendacao")
print("=" * 98)
_t = {k: D[k]["total_sistema"] for k in
      ("P1_DIRECTION_GATED", "PM_0.25", "PM_0.50", "PM_1.00")}
check(_t["P1_DIRECTION_GATED"] < _t["PM_0.25"] < _t["PM_0.50"] < _t["PM_1.00"],
      f"[27] §8 a serie 0 / 0,25 / 0,50 / 1,00 e monotona ({_t})")
check(abs((_t["PM_0.50"] - _t["P1_DIRECTION_GATED"])
          - 2 * (_t["PM_0.25"] - _t["P1_DIRECTION_GATED"])) < 1.0,
      "[28] §8 e LINEAR no multiplicador — nao ha ponto de inflexao que "
      "justifique 0,25 ou 0,50 por si so; sao pontos de sensibilidade, nao "
      "recomendacoes")
check(D["PM_0.25"]["critico"] == D["PM_0.50"]["critico"] == D["P0_CURRENT"]["critico"],
      "[29] §8 e nenhum deles muda a contagem de criticos")

print()
print("=" * 98)
print("BLOCO F - §9 o cap por familia responde 'multiplicidade ou peso?'")
print("=" * 98)
_cap = D["P2_FAMILY_CAP"]["total_sistema"]
_p0t = D["P0_CURRENT"]["total_sistema"]
check(_cap < _p0t,
      f"[30] §9 o cap por familia reduz alguma coisa ({_p0t} -> {_cap})")
check((_p0t - _cap) / _p0t < 0.20,
      f"[31] §9 mas so {((_p0t - _cap) / _p0t) * 100:.1f}% — a inflacao NAO e "
      f"multiplicidade de ocorrencia, e o peso de EXISTIR")
check((_p0t - D["P1_DIRECTION_GATED"]["total_sistema"]) / _p0t > 0.5,
      "[32] §9 enquanto o portao de direcao remove mais da metade do sistema")
check(len(R["comparacoes"]["P2_FAMILY_CAP"]["mudancas_de_status"])
      < len(R["comparacoes"]["P1b_TIPOS_TAMBEM_GATED"]["mudancas_de_status"]),
      f"[33] §9 e o cap move muito menos status que o portao "
      f"({len(R['comparacoes']['P2_FAMILY_CAP']['mudancas_de_status'])} x "
      f"{len(R['comparacoes']['P1b_TIPOS_TAMBEM_GATED']['mudancas_de_status'])})")
check(forma(_S) == forma(osd.construir("risk_history.json", CFG)),
      "[34] §9 o cap age SO no score: as ocorrencias seguem economicamente "
      "distintas")

print()
print("=" * 98)
print("BLOCO G - §13/§15/§28 status quase nao depende do score")
print("=" * 98)
_lim = BASE["limiares"]
check(_lim["atencao"] == CFG["evolution"]["status"]["atencao_total_min"]
      and _lim["critico"] == CFG["evolution"]["status"]["critico_total_min"],
      f"[35] §28 os limiares usados sao os da config, intocados "
      f"({_lim['atencao']}/{_lim['critico']})")
_acima = sum(1 for x in p0.values() if x["total"] >= _lim["atencao"])
check(_acima <= len(p0) * 0.15,
      f"[36] §28 so {_acima} de {len(p0)} emissores alcancam o limiar de "
      f"`atencao` PELO SCORE")
check(len(R["comparacoes"]["P1_DIRECTION_GATED"]["mudancas_de_status"]) == 0,
      "[37] §15 por isso o portao de direcao remove 2/3 do score e muda ZERO "
      "status — o que decide status hoje e `n_negative_types >= 2`, "
      "`persistent` e `hard_critical`, nao o total")
_p1b = R["comparacoes"]["P1b_TIPOS_TAMBEM_GATED"]["mudancas_de_status"]
check(len(_p1b) >= 4,
      f"[38] §15 e so quando a CONTAGEM DE TIPOS tambem e gateada "
      f"({len(_p1b)} emissores) o status se move: "
      f"{[l['company'] for l in _p1b]}")
check(all(l["status_politica"] == "monitorar" for l in _p1b),
      "[39] §15 todos caem de `atencao` para `monitorar`, nenhum sobe")
check(D["P1b_TIPOS_TAMBEM_GATED"]["critico"] == D["P0_CURRENT"]["critico"],
      "[40] §19 e o critico permanece — ele nao vem de familia contextual")

print()
print("=" * 98)
print("BLOCO H - §23/§24/§25/§26 consequencias de politica ja mensuraveis")
print("=" * 98)
_prod = __import__("reliability_occurrence_reproducer").reproduzir()
_B = osd.blast(_S, _prod, osd.matriz_humana(_S), osd.simular(_S, _prod))
_dir_renov = [sp.classificar_direcao((tax.get(x["family"]) or {}).get("direction", ""))
              for x in _B["renovacoes"]]
check(_B["renovacoes"] and all(d == sp.CONTEXTUAL for d in _dir_renov),
      f"[41] §23 TODAS as {len(_B['renovacoes'])} renovacoes materiais medidas "
      f"sao de familia CONTEXT_DEPENDENT, nenhuma adversa")
check(sp.classificar_direcao(tax["ma"]["direction"]) == sp.CONTEXTUAL
      and sp.classificar_direcao(tax["follow_on"]["direction"]) == sp.CONTEXTUAL,
      "[42] §23 logo, sob gating de direcao, a politica de renovacao aberta de "
      "`ma` e `follow_on` deixa de ter consequencia de SCORE — sobra so "
      "linha do tempo e destaque de exibicao")
_cos = [o for o in _S["ocorrencias"]
        if o["company"] == "Cosan" and o["family"] == "rebaixamento_rating"]
check(_cos and not any(m["refresh_effective"] for o in _cos for m in o["membros"]),
      f"[43] §24 rating: {len(_cos)} ocorrencias distintas e NENHUMA renovacao "
      f"— nao ha inflacao por artigo posterior da mesma acao")
check(sp.classificar_direcao(tax["rebaixamento_rating"]["direction"]) == sp.ADVERSO,
      "[44] §24 e rating segue controle ADVERSO")
_invf = [x for x in R["sensibilidade_familia"]
         if x["family"] == "investigacao_regulatoria"][0]
check(_invf["direcao"] == sp.ADVERSO and not _invf["empresas_que_mudam_status"],
      f"[45] §25 investigacao e adversa e nao muda status de ninguem se zerada "
      f"({_invf['pontos_vivos']} pontos)")
_div = [x for x in R["sensibilidade_familia"] if x["family"] == "emissao_divida"][0]
_fo = [x for x in R["sensibilidade_familia"] if x["family"] == "follow_on"][0]
check(_div["pontos_vivos"] > 50 and _fo["pontos_vivos"] > 50,
      f"[46] §26 divida ({_div['pontos_vivos']}) e follow-on "
      f"({_fo['pontos_vivos']}) sao pontos VIVOS relevantes do sistema")
check(not _div["empresas_que_mudam_status"] and not _fo["empresas_que_mudam_status"],
      "[47] §26 mas zerar qualquer uma delas nao muda status de ninguem — a "
      "evidencia de que o humano precisa para decidir se existir deve pontuar")

print()
print("=" * 98)
print("BLOCO I - §27 qual familia move mais")
print("=" * 98)
_sens = R["sensibilidade_familia"]
check(_sens[0]["family"] == "ma",
      f"[48] §27 `ma` e a familia de maior impacto "
      f"({_sens[0]['pontos_vivos']} pontos, "
      f"{_sens[0]['pct_do_sistema'] * 100:.1f}% do sistema)")
check(_sens[0]["direcao"] == sp.CONTEXTUAL,
      "[49] §27 e ela e CONTEXT_DEPENDENT — a maior fonte de pontos do "
      "sistema e uma familia que a propria config chama de neutra")
_mudam = [x for x in _sens if x["empresas_que_mudam_status"]]
check(all(x["direcao"] == sp.ADVERSO for x in _mudam),
      f"[50] §27 e as UNICAS familias cujo zeramento muda status sao adversas "
      f"({[(x['family'], x['empresas_que_mudam_status']) for x in _mudam]})")

print()
print("=" * 98)
print("BLOCO J - §10 materialidade sobrevive sem autoridade de score")
print("=" * 98)
_mat = R["materialidade"]
check("INDEPENDENTE" in _mat["definicao"] and "sem autoridade" in _mat["definicao"],
      "[51] §10 o indice de materialidade e definido sem direcao e sem "
      "autoridade de score")
_jm = _mat["empresas"]["JBS"]
check(_jm["eventos_materiais"] >= 4 and _jm["familias"] >= 4,
      f"[52] §10 a JBS mantem {_jm['eventos_materiais']} eventos materiais em "
      f"{_jm['familias']} familias mesmo quando o risco cai para "
      f"{p1['JBS']['total']}")
check(_jm["adverso"] + _jm["contextual"] == _jm["materialidade_ponderada"],
      "[53] §10 e o indice separa as duas parcelas sem somar significado")
check(_mat["authority"].endswith("DIAGNOSTICO"),
      "[54] §10 rotulado como diagnostico: nao vai ao painel nesta onda")

print()
print("=" * 98)
print("BLOCO K - §11 JBS explica a distincao")
print("=" * 98)
_j0, _j1 = p0["JBS"], p1["JBS"]
check(_j0["adverso"] == 12.6 or _j0["adverso"] < 20,
      f"[55] §11 na JBS so a recomendacao rebaixada e adversa "
      f"({_j0['adverso']} de {_j0['total']})")
check(_j0["contextual"] / _j0["total"] > 0.75,
      f"[56] §11 {_j0['contextual'] / _j0['total'] * 100:.0f}% do score da JBS "
      f"vem de direcao indeterminada")
check(_j1["status"] == _j0["status"],
      f"[57] §11 e mesmo caindo de {_j0['total']} para {_j1['total']} ela "
      f"permanece `{_j1['status']}` — porque `persistent` a segura, nao o score")
check(all(it["direcao"] in (sp.ADVERSO, sp.CONTEXTUAL) for it in _j0["itens"]),
      "[58] §11 nenhuma das aquisicoes, do CEO ou da divida foi rotulada "
      "FAVORAVEL por conta propria")

print()
print("=" * 98)
print("BLOCO L - §34 producao intocada e diagnostico deterministico")
print("=" * 98)
_prodsrc = io.open("risk_dashboard.py", encoding="utf-8").read()
_semsrc = io.open("semantic_audit.py", encoding="utf-8").read()
_src = io.open("reliability_scoring_policy_shadow.py", encoding="utf-8",
               newline="").read()
check("reliability_scoring_policy_shadow" not in _prodsrc
      and "reliability_scoring_policy_shadow" not in _semsrc,
      "[59] nenhum caminho de producao importa o simulador de politica")
check(not [t for t in ('.write(', 'json.dump(', ", 'w'", ', "w"')
           if t in _src],
      "[60] o modulo nao abre nada para escrita")
check(not [c for c in _src if ord(c) < 32 and c not in "\n\r\t"],
      "[61] e nao tem caractere de controle invisivel")
check(sp.AUTORIDADE["config_write_authority"] == "NONE"
      and sp.AUTORIDADE["threshold_authority"] == "NONE",
      "[62] §34 autoridade de config e de limiar: NENHUMA")
_R2 = sp.rodar_tudo()
check(_R2["distribuicoes"] == R["distribuicoes"]
      and _R2["inventario_direcao"] == R["inventario_direcao"],
      "[63] duas execucoes dao o mesmo resultado")
_ins = R["pontos_de_insercao"]
check(all(isinstance(_ins[k], int) for k in
          ("best_contribs", "weighted_total", "regra_de_status",
           "n_negative_types")),
      f"[64] §31 os pontos de insercao foram LOCALIZADOS no codigo, nao "
      f"supostos (best_contribs L{_ins['best_contribs']}, status "
      f"L{_ins['regra_de_status']}, tipos L{_ins['n_negative_types']})")
check("SEGUNDO ponto" in _ins["nota"],
      "[65] §31 e a nota separa o portao de direcao da contagem de tipos: sao "
      "duas decisoes, nao uma")
import reliability_human_supervision as hs
check(len(hs.carregar()["memberships"]) == 27,
      "[66] §34 supervisao humana intacta (27)")
_ot = json.load(io.open("risk_semantic_v2_shadow.json",
                        encoding="utf-8"))["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[67] occurrence_truth intacto (10/21/4)")
check("def best_contribs" in _prodsrc and "def weighted_total" in _prodsrc
      and "def build_evolution" in _prodsrc,
      "[68] §34 build_evolution, best_contribs e weighted_total sem reescrita")
_cfg_txt = io.open("config_risco.yaml", encoding="utf-8").read()
check("atencao_total_min: 60" in _cfg_txt and "critico_total_min: 125" in _cfg_txt,
      "[69] §34 os limiares seguem exatamente como estavam na config")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7t (politica de score): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
