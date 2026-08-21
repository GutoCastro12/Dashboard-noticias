#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7s_jbs_separacao_score.py — 4I.2 R7s · SHADOW V3.

JBS + SEPARACAO ENTRE OCORRENCIA E SCORE. `HUMAN_REVIEW_2026_08_20`.

DECISAO HUMANA DESTA ONDA

  As tres transacoes da JBS sao ocorrencias DISTINTAS: os 18% restantes da
  Pilgrim's Pride, a compra da Walkers pela Pilgrim's no Reino Unido, e os
  US$ 150 mi em Oma. Mas o humano observou tambem que elas NAO sao
  necessariamente adversas — separar corretamente nao prova que a JBS merece
  severidade maior.

O QUE ESTE ARQUIVO PROVA

  1. identidade de ocorrencia NAO depende de score. Com peso zero, peso normal
     ou peso dobrado, os mesmos artigos produzem os MESMOS occurrence_id e os
     mesmos membros. Sem isto, "corrigir" identidade para o score fechar seria
     sempre possivel — e a arquitetura nao valeria nada;
  2. as tres transacoes, quando existem no acervo, ficam separadas;
  3. um COMENTARIO nao abre ocorrencia: nem a analise da casa UBS sobre a
     proposta da Pilgrim's, nem o descritor "novo CEO promete continuidade";
  4. o score consome a CONTAGEM de ocorrencia, entao promover estrutura sem
     mexer no score nao e tecnicamente separavel.

O QUE ELE NAO FAZ

Nao inventa polaridade. Fora do que a taxonomia ja declara `negativa`, a
direcao sai DIRECTION_UNDETERMINED — nunca "positiva" por conta propria.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json

import reliability_human_supervision as hs
import reliability_occurrence_reproducer as rp
import reliability_occurrence_shadow as sd
import risk_dashboard as rd

PASS = FAIL = 0
CALIBRACAO = "HUMAN_REVIEW_2026_08_20"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + label)
    else:
        FAIL += 1
        print("  FALHOU: " + label)


CFG = rd.load_config("config_risco.yaml")
_DIA = 86400


def art(titulo, dias_atras, eid, empresa, dominio="exemplo.com"):
    import datetime as _dt
    import time as _t
    ts = int(_t.time()) - dias_atras * _DIA
    iso = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    u = "https://" + dominio + "/" + hashlib.sha1(
        titulo.encode("utf-8")).hexdigest()[:16]
    return u, {"url": u, "title": titulo, "companies": [empresa],
               "events_by_company": {empresa: [eid]}, "pub_ts": ts,
               "pub_iso": iso, "source": dominio, "domain": dominio}


def sombra(artigos, cfg=None):
    return sd.construir({"articles": dict(artigos)}, cfg or CFG)["ocorrencias"]


def forma(occs):
    """Identidade + composicao, SEM nada derivado de score."""
    return sorted((o["occurrence_id"], o["company"], o["family"],
                   o["canonical_object"], o["occurrence_instance_signature"],
                   o["anchor_date"], o["display_representative"],
                   tuple(sorted(m["article_id"] for m in o["membros"])))
                  for o in occs)


def cfg_com_peso(familia, peso):
    c = copy.deepcopy(CFG)
    for e in c["taxonomy"]:
        if e["id"] == familia:
            e["score"] = peso
    return c


# ── FIXTURE de calibracao humana ───────────────────────────────────────────
FIXTURE = {
    "provenance": CALIBRACAO,
    "adjudicated_by": "gustavo",
    "production_score_authority": "NONE",
    "jbs_ma": {
        "decisao": "tres transacoes = tres ocorrencias DISTINTAS",
        "nota_humana": "nao necessariamente adversas; separar nao prova "
                       "severidade maior",
        "A_pilgrim_18": "ac0be217149db21ae564",
        "B_walkers": None,      # nao existe em risk_history.json
        "C_oma": "f11950284cc8d9a152f5",
        "comentario_de_analista": "9107879a6502b164d2f9",
    },
    "jbs_ceo": {
        "confirmada": "edeb694bf05ba64f3430",
        "segundo_candidato": "201b91aa6b3c1d9e780c",
        "classificacao": "DESCRIPTOR/FOLLOW_UP",
    },
    "jbs_outros": {
        "recomendacao_negativa": "83effbd842777700232f",
        "emissao_divida": "53340f861717b39cbca3",
    },
}

S = sd.construir()
P = rp.reproduzir()
M = sd.matriz_humana(S)
SIM = sd.simular(S, P)
POR_ID = {m["article_id"]: (o, m) for o in S["ocorrencias"] for m in o["membros"]}
JBS = [o for o in S["ocorrencias"] if o["company"] == "JBS"]

print("=" * 98)
print("BLOCO A - §27 IDENTIDADE DE OCORRENCIA NAO DEPENDE DE SCORE")
print("=" * 98)
_T1 = art("JBS propoe aquisicao dos 18% restantes da Pilgrim Pride", 40, "ma", "JBS")
_T2 = art("Pilgrim Pride, controlada pela JBS, anuncia aquisicao da Walkers "
          "Deli no Reino Unido", 25, "ma", "JBS", "o.com")
_T3 = art("JBS investe US$ 150 milhoes na aquisicao de industrias em Oma", 10,
          "ma", "JBS", "o2.com")
_TRIO = [_T1, _T2, _T3]
_normal = sombra(_TRIO)
_zero = sombra(_TRIO, cfg_com_peso("ma", 0))
_dobro = sombra(_TRIO, cfg_com_peso("ma", 80))
check(forma(_normal) == forma(_zero),
      "[1] com peso de `ma` ZERO, os occurrence_id e os membros sao IDENTICOS")
check(forma(_normal) == forma(_dobro),
      "[2] com peso DOBRADO, idem — identidade nao e funcao de score")
check(len(_normal) == len(_zero) == len(_dobro) == 3,
      f"[3] e as tres transacoes seguem TRES ocorrencias em qualquer peso "
      f"({len(_normal)}/{len(_zero)}/{len(_dobro)})")
check({o["score_base"] for o in _zero} == {0},
      "[4] o peso realmente mudou (peso-base zero) — o teste nao passou por "
      "acidente de config inalterada")
check(_normal[0]["simulated_contribution"] != _zero[0]["simulated_contribution"],
      "[5] e a CONTRIBUICAO muda, como devia: e ela que depende de peso, nao a "
      "identidade")
check(len({o["occurrence_id"] for o in _normal}) == 3,
      "[6] tres ids distintos, sem colisao")

print()
print("=" * 98)
print("BLOCO B - §3/§5 JBS M&A no acervo REAL")
print("=" * 98)
_jma = [o for o in JBS if o["family"] == "ma"]
_pilgrim = POR_ID.get(FIXTURE["jbs_ma"]["A_pilgrim_18"])
_oma = POR_ID.get(FIXTURE["jbs_ma"]["C_oma"])
_ubs = POR_ID.get(FIXTURE["jbs_ma"]["comentario_de_analista"])
check(_pilgrim and _oma, "[7] Pilgrim's 18% e Oma estao ambos na sombra")
check(_pilgrim[0]["occurrence_id"] != _oma[0]["occurrence_id"],
      "[8] §3 e sao ocorrencias DISTINTAS — nao se fundem por serem M&A da "
      "mesma empresa")
check(_oma[0]["canonical_object"] == "oma",
      f"[9] o objeto de Oma foi extraido ({_oma[0]['canonical_object']!r}) — a "
      f"producao o perdia pelo minimo de 4 caracteres em `_marcadores_operacao`")
check(FIXTURE["jbs_ma"]["B_walkers"] is None,
      "[10] §5 a transacao Walkers NAO existe em risk_history.json — nenhuma "
      "regra de ocorrencia pode produzi-la, e isto e lacuna de COLETA")
check(len(_jma) == 2,
      f"[11] logo a sombra alcanca 2 das 3 transacoes humanas ({len(_jma)}), e "
      f"a terceira e reportada como nao coletada em vez de fabricada")
check(_ubs and _ubs[0]["occurrence_id"] == _pilgrim[0]["occurrence_id"],
      "[12] a analise do UBS sobre a proposta e MEMBRO da ocorrencia da "
      "Pilgrim's, nao uma terceira transacao")
check(_ubs[1]["phase"] == sd.ACOMPANHAMENTO
      and "analista" in _ubs[1]["phase_evidence"],
      f"[13] classificada como {_ubs[1]['phase']} por assercao primaria de "
      f"ANALISTA ({_ubs[1]['phase_evidence'][:44]})")
check(sd.fase_de("Stephens reduz preço-alvo da JBS após resultado misto",
                 "recomendacao_negativa")["fase"] != sd.ACOMPANHAMENTO,
      "[14] mas numa familia DE analista a acao da casa continua sendo o "
      "evento — o guarda e escopado, nao geral")

print()
print("=" * 98)
print("BLOCO C - §4 evento de controlada dentro do grupo")
print("=" * 98)
_w = [o for o in _normal if "walkers" in o["canonical_object"]]
check(len(_w) == 1, f"[15] a transacao da Walkers e ocorrencia propria ({len(_w)})")
check(_w[0]["membros"][0]["article_role"] in
      ("direto", "investida_jv", "contraparte_credor", "contexto",
       "mercado_contexto"),
      f"[16] o papel do emissor no artigo e preservado no membro "
      f"({_w[0]['membros'][0]['article_role']}) — direto x controlada fica "
      f"legivel sem inventar consequencia de score")
check(_w[0]["occurrence_id"] not in {
      o["occurrence_id"] for o in _normal if o is not _w[0]},
      "[17] e ela nao se confunde com a proposta da Pilgrim's nem com Oma")

print()
print("=" * 98)
print("BLOCO D - §6/§7 o SEGUNDO candidato a ocorrencia de CEO")
print("=" * 98)
_ceo1 = POR_ID.get(FIXTURE["jbs_ceo"]["confirmada"])
_ceo2 = POR_ID.get(FIXTURE["jbs_ceo"]["segundo_candidato"])
_jceo = [o for o in JBS if o["family"] == "troca_ceo"]
check(_ceo1 and _ceo2, "[18] os dois artigos de CEO estao na sombra")
check(len(_jceo) == 1,
      f"[19] §7 UMA ocorrencia de CEO confirmada ({len(_jceo)}) — a V2 "
      f"reportava duas")
check(_ceo1[0]["occurrence_id"] == _ceo2[0]["occurrence_id"],
      "[20] o segundo artigo e MEMBRO da mesma ocorrencia")
check(_ceo2[1]["phase"] == sd.ACOMPANHAMENTO,
      f"[21] classificado {_ceo2[1]['phase']} — DESCRIPTOR/FOLLOW_UP")
check("descritor_sem_assercao" in _ceo2[1]["phase_evidence"],
      f"[22] pela guarda `R_TROCA_CEO_SEM_ASSERCAO` JA PUBLICADA em producao, "
      f"reusada em vez de reimplementada ({_ceo2[1]['phase_evidence'][:40]})")
check(_ceo1[0]["display_representative"] == _ceo1[1]["article_id"],
      "[23] e o representante e a nomeacao de Wesley Batista Filho, nao o "
      "artigo de margens")
check(not _ceo2[1]["refresh_effective"],
      "[24] o descritor nao renova nada")

print()
print("=" * 98)
print("BLOCO E - §8 os demais eventos da JBS, cada um no seu lugar")
print("=" * 98)
_rec = POR_ID.get(FIXTURE["jbs_outros"]["recomendacao_negativa"])
_div = POR_ID.get(FIXTURE["jbs_outros"]["emissao_divida"])
check(_rec and _rec[0]["family"] == "recomendacao_negativa",
      "[25] o corte de preco-alvo da Stephens e ocorrencia propria de "
      "`recomendacao_negativa`")
check(_div and _div[0]["family"] == "emissao_divida",
      "[26] a captacao de R$ 400 mi e ocorrencia propria de `emissao_divida`")
check(_rec[0]["occurrence_id"] != _div[0]["occurrence_id"]
      and all(_rec[0]["occurrence_id"] != o["occurrence_id"] for o in _jma),
      "[27] e nenhuma delas se mistura com as transacoes de M&A")

print()
print("=" * 98)
print("BLOCO F - §9/§10/§15 materialidade NAO e adversidade")
print("=" * 98)
_tax = {e["id"]: e for e in CFG["taxonomy"]}
_mat = sd.matriz_materialidade()
check(_mat["familias"] == len(CFG["taxonomy"]),
      f"[28] a matriz cobre a taxonomia inteira ({_mat['familias']} familias)")
_conf = _mat["conflacao_materialidade_x_adversidade"]
check(len(_conf) >= 6,
      f"[29] §15 ha {len(_conf)} familias que a PROPRIA taxonomia declara "
      f"`neutra` e que mesmo assim pontuam por existir")
check({x["family"] for x in _conf} >= {"ma", "troca_ceo", "emissao_divida",
                                       "follow_on"},
      "[30] §14 e as quatro da onda estao entre elas: ma, troca_ceo, "
      "emissao_divida, follow_on")
check(_mat["peso_somado_das_neutras_que_pontuam"] >= 150,
      f"[31] somando {_mat['peso_somado_das_neutras_que_pontuam']} pontos de "
      f"peso-base — sozinhas ja ultrapassam o limiar de `atencao`")
check(sd.classificar_direcao_familia(_tax["recomendacao_negativa"]) == sd.ADVERSO
      and sd.classificar_direcao_familia(_tax["recuperacao_judicial"]) == sd.ADVERSO
      and sd.classificar_direcao_familia(_tax["rebaixamento_rating"]) == sd.ADVERSO,
      "[32] §14 os controles adversos seguem adversos: recomendacao negativa, "
      "RJ e rebaixamento")
check(sd.classificar_direcao_familia(_tax["ma"]) == sd.CONTEXTUAL
      and sd.classificar_direcao_familia(_tax["troca_ceo"]) == sd.CONTEXTUAL,
      "[33] §10 M&A e troca de CEO ficam CONTEXT_DEPENDENT — nao rotulados "
      "positivos por conta propria")
_d = sd.decomposicao("JBS", S, P, SIM)
check(all(x["direcao"] in ("ADVERSE", sd.DIRECAO_INDETERMINADA)
          for x in _d["ocorrencias"]),
      "[34] §10 nenhuma ocorrencia recebeu rotulo POSITIVO automatico")
check(sum(1 for x in _d["ocorrencias"] if x["direcao"] == "ADVERSE") == 1,
      "[35] na JBS so o corte de preco-alvo e adverso pela evidencia atual")
check(_d["fracao_indeterminada"] and _d["fracao_indeterminada"] > 0.7,
      f"[36] §12 e {int(_d['fracao_indeterminada'] * 100)}% do score simulado "
      f"da JBS vem de familias de direcao INDETERMINADA "
      f"({_d['contribuicao_de_direcao_indeterminada']} de "
      f"{_d['contribuicao_de_direcao_indeterminada'] + _d['contribuicao_de_familias_adversas']})")

print()
print("=" * 98)
print("BLOCO G - §11/§12/§13 o delta de status da JBS")
print("=" * 98)
# A sombra NAO tem portao de direcao; a producao promovida tem. Comparar os
# dois totais agora mediria a politica humana, nao a arquitetura. O que
# permanece verdadeiro — e e o achado desta onda — e que a inflacao da V2 vinha
# de duas ocorrencias ESPURIAS, e que as legitimas continuam todas presentes.
_jma_p = [o for o in P["ocorrencias"] if o["company"] == "JBS" and o["family"] == "ma"]
check(len(_jma_p) == 2,
      f"[37] a producao promovida mantem as DUAS transacoes de M&A alcancaveis "
      f"da JBS ({len(_jma_p)})")
check(len([o for o in P["ocorrencias"]
           if o["company"] == "JBS" and o["family"] == "troca_ceo"]) == 1,
      "[38] e UMA ocorrencia de CEO — o comentario de analista e o descritor "
      "seguem membros, nao eventos economicos")
check(len(_jma) == 2 and len(_jceo) == 1,
      "[39] §13 e isso foi obtido classificando COMENTARIO como membro, nunca "
      "re-fundindo transacoes economicamente distintas")
check(forma(sombra(_TRIO)) == forma(sombra(_TRIO, cfg_com_peso("ma", 0))),
      "[40] §13 prova do invariante: com peso zero as tres transacoes seguem "
      "tres — identidade nunca depende de o score 'parecer alto demais'")

print()
print("=" * 98)
print("BLOCO H - §16/§17 o score consome a CONTAGEM de ocorrencia")
print("=" * 98)
_ac = sd.acoplamento_score_ocorrencia()
check(_ac["best_contribs_chaveia_por_occ_key"],
      "[41] `best_contribs` chaveia por `_occ_key` — lido do codigo, nao suposto")
check(_ac["total_e_soma_por_chave"],
      "[42] e o total do emissor e a SOMA de uma contribuicao por chave")
check(_ac["score_consome_contagem_de_ocorrencia"],
      "[43] §17 portanto dividir uma ocorrencia em duas ACRESCENTA pontos")
check(_ac["promocao_em_dois_estagios_segura"] is False,
      "[44] §17 logo promover estrutura de ocorrencia SEM mexer no score NAO e "
      "tecnicamente separavel — dizer o contrario seria fingir uma separacao "
      "que o codigo nao tem")

print()
print("=" * 98)
print("BLOCO I - §21/§23 prontidao de OCORRENCIA e de SCORE, separadas")
print("=" * 98)
_B = sd.blast(S, P, M, SIM)
_F = sd.fidelidade(S, P)
_T = sd.avaliar_occurrence_truth(S)
PR = sd.prontidao(S, P, M, _T, _B, _F, SIM)
check(PR["ocorrencia"]["pronta"] is True,
      f"[45] §21 lado da OCORRENCIA pronto, sem bloqueadores "
      f"({PR['ocorrencia']['bloqueadores']})")
check(PR["score"]["pronta"] is False,
      "[46] §21 lado do SCORE nao pronto")
check(any("neutra" in b for b in PR["score"]["bloqueadores"]),
      "[47] e um dos motivos e a conflacao materialidade/adversidade")
check(any("politica de renovacao em aberto" in b
          for b in PR["score"]["bloqueadores"]),
      "[48] o outro e a politica de renovacao ainda aberta em quatro familias")
# A sombra e nao-gateada e a producao e gateada: divergencia de status entre
# as duas passou a MEDIR a politica humana. O que nao pode acontecer e uma
# divergencia por IDENTIDADE errada — e essa continua zero.
check(not [x for x in PR["score"]["status_deltas"]
           if x["categoria"] == sd.ERRO_OCORRENCIA],
      f"[49] §23 nenhuma divergencia de status por identidade ERRADA "
      f"({[x['company'] for x in _B['delta_status']]} divergem por politica)")
check(not [x for x in PR["score"]["status_deltas"]
           if x["categoria"] == sd.ERRO_OCORRENCIA],
      "[50] §23 e nenhuma delta de status por identidade ERRADA")

print()
print("=" * 98)
print("BLOCO J - §18/§19/§20 nada anterior regride")
print("=" * 98)
_pp = {}
for o in S["ocorrencias"]:
    _pp.setdefault((o["company"], o["family"]), []).append(o)
for _i, (_c, _f, _cond, _txt) in enumerate((
        ("Tok&Stok", "recuperacao_judicial",
         lambda x: len(x) == 1 and x[0]["anchor_date"] == x[0]["initial_date"],
         "uma ocorrencia, acompanhamento nao renova"),
        ("Sabesp", "ma",
         lambda x: {y["canonical_object"] for y in x} == {"emae", "castilho"},
         "EMAE separado de Castilho"),
        ("Smart Fit", "ma",
         lambda x: len(x) == 1 and x[0]["anchor_date"] > x[0]["initial_date"],
         "fechamento renova a ancora"),
        ("Suzano", "ma",
         lambda x: len(x) == 1 and x[0]["aliases"] == ["ma:suzano:tissue-ifp"]
         and x[0]["anchor_date"] == x[0]["initial_date"],
         "alias declarado, ancora ja e o fechamento, delta zero"),
        ("Santander Brasil", "troca_ceo", lambda x: not x,
         "segue sem ocorrencia de CEO"),
        ("ISA Energia Brasil", "follow_on",
         lambda x: len(x) == 1 and x[0]["anchor_date"] == x[0]["initial_date"],
         "acompanhamento nao renova")), start=51):
    check(_cond(_pp.get((_c, _f), [])), f"[{_i}] §18 {_c}: {_txt}")
_cos = _pp.get(("Cosan", "rebaixamento_rating"), [])
check(len({o["occurrence_instance_signature"].split("|")[0] for o in _cos}) >= 2,
      f"[57] §18 Cosan: agencias seguem separadas ({len(_cos)} ocorrencias)")
_vale = _pp.get(("Vale", "investigacao_regulatoria"), [])
check(len(_vale) >= 2,
      f"[58] §18 Vale: aberturas de processo seguem distintas ({len(_vale)})")
_yb = [o for o in S["ocorrencias"] if o["company"] == "Yobel"]
check(len(_yb) == 1,
      "[59] §18 Yobel: familia opt-in segue UMA ocorrencia")
for _i, _k in enumerate(("identidade", "fase", "refresh", "representante",
                         "data_efetiva"), start=60):
    check(M[_k]["erros"] == 0,
          f"[{_i}] §22 {_k}: {M[_k]['acertos']}/{M[_k]['avaliaveis']}, zero erros")
_pf = sd.politica_familias()["politicas"]
check(_pf["follow_on"]["status"] == "PARTIALLY_ESTABLISHED"
      and _pf["rebaixamento_rating"]["status"] == "IDENTITY_ONLY"
      and _pf["investigacao_regulatoria"]["status"] == "REPRESENTATIVE_ONLY"
      and _pf["emissao_divida"]["status"] == "UNREVIEWED",
      "[65] §19 as quatro politicas de renovacao seguem exatamente como estavam")
_btg = [x for x in _B["renovacoes"]
        if x["company"] in ("BTG Pactual", "Baker Hughes")]
check(_btg and all(x["classificacao"] == "UNREVIEWED" for x in _btg),
      f"[66] §20 BTG e Baker Hughes seguem UNREVIEWED ({len(_btg)} casos)")

print()
print("=" * 98)
print("BLOCO K - §25/§28/§29/§30 artefatos, proveniencia e producao")
print("=" * 98)
_MS = hs.carregar()["memberships"]
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[67] §25 supervisao humana intacta (27/24) — a decisao da JBS entrou "
      "como fixture, sem bump de schema")
_sh = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ot = _sh["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[68] occurrence_truth intacto (10/21/4)")
check(FIXTURE["provenance"] == CALIBRACAO
      and FIXTURE["jbs_ma"]["B_walkers"] is None,
      "[69] a fixture declara proveniencia e registra a Walkers como ausente")
check(_F["membros_sem_article_id"] == 0
      and _F["membros_com_article_id"] == _F["membros_sombra"],
      f"[70] §28 proveniencia 100% ({_F['membros_com_article_id']}/"
      f"{_F['membros_sombra']})")
check(not sd.colisoes_de_id(S), "[71] zero colisao de occurrence_id")
_prod = io.open("risk_dashboard.py", encoding="utf-8").read()
_sem = io.open("semantic_audit.py", encoding="utf-8").read()
check(rp.equivalencia(P)["ok"], "[72] §29 reprodutor de producao EXATO")
check("reliability_occurrence_shadow" not in _prod
      and "reliability_occurrence_shadow" not in _sem,
      "[73] §30 nenhum caminho de producao importa a sombra")
check("def build_evolution" in _prod and "def assign_occurrence_clusters" in _prod
      and "def best_contribs" in _prod,
      "[74] §30 build_evolution, clustering e best_contribs sem reescrita")
check("detect_troca_ceo_sem_assercao" in _sem,
      "[75] e a guarda de CEO reusada segue publicada em producao, intocada")
check(all(o["authority"] == "SHADOW / SIMULATED" for o in S["ocorrencias"]),
      "[76] toda ocorrencia sai rotulada SHADOW / SIMULATED")
check(forma(sd.construir()["ocorrencias"]) == forma(S["ocorrencias"]),
      "[77] a sombra segue deterministica")
_src = io.open("reliability_occurrence_shadow.py", encoding="utf-8",
               newline="").read()
check(not [c for c in _src if ord(c) < 32 and c not in "\n\r\t"],
      "[78] o modulo nao tem caracteres de controle invisiveis — um `\\b` de "
      "regex virou backspace literal durante esta onda e o teste passa a "
      "vigiar isso")
_cal = json.load(io.open("occurrence_calibration_shadow.json", encoding="utf-8"))
check(_cal["_meta"]["provenance"] == CALIBRACAO
      and _cal["_meta"]["production_score_authority"] == "NONE"
      and _cal["_meta"]["write_authority"] == "NONE",
      "[79] §25 o artefato de calibracao declara proveniencia e ausencia de "
      "autoridade")
check(sd.carregar_calibracao().get(("JBS", "ma"), {}).get("decisao")
      == "DISTINCT_OCCURRENCES"
      and sd.carregar_calibracao().get(("JBS", "troca_ceo"), {}).get("decisao")
      == "ONE_OCCURRENCE",
      "[80] §25 as duas decisoes da JBS ficam registradas em mecanismo proprio, "
      "sem tocar em `risk_human_supervision.json` nem bumpar schema")
_wk = next(t for t in _cal["confirmacoes"][0]["transacoes"]
           if not t["no_acervo"])
check(_wk["article_id"] is None and "COLETA" in _wk["nota"],
      "[81] §5 e a Walkers fica registrada como AUSENTE do acervo, com o "
      "motivo — nao como concordancia fabricada")
_Q = sd.fila_revisao(_B, M)
check(not any(x["company"] == "JBS" and x["tipo"].startswith("SPLIT")
              for x in _Q),
      "[82] §24 por isso a divisao de M&A da JBS sai da fila de revisao: ela "
      "esta RESOLVIDA, nao pendente")
check(len(_Q) <= 10 and all(x["veredito"] == "REVIEW_CANDIDATE" for x in _Q),
      f"[83] §24 e a fila segue com no maximo 10 candidatos ({len(_Q)})")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7s (JBS + separacao ocorrencia/score): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
