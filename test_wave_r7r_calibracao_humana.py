#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7r_calibracao_humana.py — 4I.2 R7r · OCCURRENCE SHADOW V2.

CALIBRACAO POR DECISAO HUMANA — `HUMAN_REVIEW_2026_08_20`.

Quatro adjudicacoes chegaram e sao AUTORIDADE para esta onda:

  COSAN   agencias de rating DIFERENTES sao ocorrencias DISTINTAS.
  VALE    desenvolvimentos do MESMO processo sao a mesma ocorrencia, e o
          desenvolvimento substantivo mais RECENTE e o REPRESENTANTE.
  NATURA  30/03 compromisso vinculante, 02/07 6,6% e 31/07 8% sao UMA
          transacao economica, com marcos materiais sucessivos.
  ENGIE   "lucra R$ 694 mi no 2o tri e conclui follow-on" e RECAPITULACAO de
          fato ja ocorrido: acompanhamento, sem renovacao.

O QUE ESTE ARQUIVO NAO FAZ

Nao inventa verdade que o humano nao deu. A renovacao da Vale segue EM ABERTO;
a da Natura idem. O teste mede os cenarios e afirma que a sombra NAO os
carimba como decididos.

Nao codifica article_id na regra: os ids aparecem em FIXTURE, a regra generaliza
de agencia, identificador de processo, vinculo de compromisso e evidencia
temporal. As metamorficas usam historico sintetico exatamente por isso.

PROVENIENCIA DA NATURA

A cronologia humana da Natura NAO e verificavel no acervo: `risk_history.json`
tem um unico artigo Advent (02/07). O anuncio de 30/03 e o marco de 31/07nunca
foram coletados. Por isso a cronologia entra como FIXTURE com proveniencia
explicita, e o artefato de supervisao humana fica INTOCADO — o schema dele e
indexado por article_id, e esses artigos nao tem um.
"""
from __future__ import annotations

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


def sombra(*artigos):
    return sd.construir({"articles": dict(artigos)}, CFG)["ocorrencias"]


def ids(occs):
    return {o["occurrence_id"] for o in occs}


# ── FIXTURE de calibracao humana ────────────────────────────────────────────
# Artigos REAIS do acervo, por article_id, para as tres decisoes verificaveis.
# A Natura entra so como cronologia declarada, porque os artigos nao existem.
FIXTURE = {
    "provenance": CALIBRACAO,
    "adjudicated_by": "gustavo",
    "production_score_authority": "NONE",
    "cosan": {
        "decisao": "DISTINCT_OCCURRENCE por AGENCIA diferente",
        "artigos": {
            "875f72493508e2fd1945": ("2026-07-08", "S&P", "b+"),
            "826b47491b3703fc1269": ("2026-07-16", "Moody's", "b1"),
            "c8b94e33ce070747ba16": ("2026-08-05", "S&P", "b+"),
            "6bc05e51969c6be1e11f": ("2026-08-10", "Moody's", ""),
        },
        "caso_humano": "02",
    },
    "vale": {
        "decisao": "mesmo processo -> SAME_OCCURRENCE; desenvolvimento "
                   "substantivo mais recente -> REPRESENTANTE",
        "artigos": {
            "7c9ff9161799290c2e41": ("2026-07-15", "eleicao de interino"),
            "3c5fafeb65e405877347": ("2026-07-20", "abre processo Previ"),
            "eea8fb0b2e52f38f04b8": ("2026-07-23", "abre processo destituicao"),
        },
        "caso_humano": "22",
    },
    "natura": {
        "decisao": "30/03 + 02/07 + 31/07 = UMA ocorrencia economica",
        "cronologia": [
            ("2026-03-30", "INICIACAO",
             "compromisso vinculante da Advent/Lotus por 8%-10% do capital"),
            ("2026-07-02", "MATERIAL",
             "6,6% do capital mais 1,4% via TRS, decorre do Compromisso "
             "Vinculante de 30/03"),
            ("2026-07-31", "MATERIAL",
             "8%, minimo estabelecido pelo compromisso original"),
        ],
        "artigo_local_unico": "9d0d1be36f6921025838",
        "caso_humano": "14",
        "nota": "30/03 e 31/07 NAO existem em risk_history.json",
    },
    "engie": {
        "decisao": "recapitulacao coordenada -> ACOMPANHAMENTO, sem renovacao",
        "artigos": {
            "a3de08f211694408beb1": ("2026-08-05", "lucra 2o tri E conclui"),
            "f3125011635de116cfcb": ("2026-08-10", "finaliza oferta"),
        },
        "caso_humano": "11",
    },
}

S = sd.construir()
P = rp.reproduzir()
M = sd.matriz_humana(S)
POR_ID = {m["article_id"]: (o, m) for o in S["ocorrencias"] for m in o["membros"]}

print("=" * 98)
print("BLOCO A - §3/§4/§22 COSAN: a agencia e discriminante de instancia")
print("=" * 98)
_c = {k: POR_ID.get(k) for k in FIXTURE["cosan"]["artigos"]}
check(all(_c.values()), "[1] os quatro artigos de rating da Cosan estao na sombra")
_sp1, _mo1 = _c["875f72493508e2fd1945"][0], _c["826b47491b3703fc1269"][0]
_sp2, _mo2 = _c["c8b94e33ce070747ba16"][0], _c["6bc05e51969c6be1e11f"][0]
check(_sp1["occurrence_id"] != _mo1["occurrence_id"],
      "[2] S&P 08/07 e Moody's 16/07 sao ocorrencias DISTINTAS")
check(_sp2["occurrence_id"] != _mo2["occurrence_id"],
      "[3] S&P 05/08 e Moody's 10/08 tambem — nem empresa, nem familia, nem "
      "direcao, nem proximidade de data as fundem")
check(_sp1["occurrence_id"] == _sp2["occurrence_id"],
      "[4] mas as duas notas da S&P, ambas B+, sao a MESMA acao renoticiada")
check(_mo1["occurrence_id"] != _mo2["occurrence_id"],
      "[5] §22-C e as duas da Moody's sao acoes DIFERENTES: 16/07 fixa B1, "
      "10/08 nao repete nivel e esta fora da janela de corroboracao")
_l02 = next(l for l in M["linhas"] if l.get("case_id") == "02")
check(_l02["identidade_ok"] is True,
      f"[6] o caso humano 02 passa a bater ({_l02['identidade_humana']} = "
      f"{_l02['identidade_sombra']}) — bloqueador P1 resolvido")
check("nivel" in sd.adaptador_de("rebaixamento_rating")(
      {"agencia": "s&p", "direcao_rating": "rebaixa", "nivel_rating": "b+"})
      ["features"], "[7] o adaptador de rating passou a carregar o NIVEL")

print()
print("=" * 98)
print("BLOCO B - §22 minimos de rating, em historico sintetico")
print("=" * 98)
_A1 = art("Fitch rebaixa rating da Cosan para BB-", 40, "rebaixamento_rating", "Vale")
_A2 = art("Moody's rebaixa rating da Cosan para Ba3", 39, "rebaixamento_rating",
          "Vale", "o.com")
check(len(sombra(_A1, _A2)) == 2,
      "[8] §22-A mesma empresa, familia e direcao, AGENCIAS diferentes = DISTINTAS")
_B1 = art("Fitch rebaixa rating da Vale para BB-", 40, "rebaixamento_rating", "Vale")
_B2 = art("Fitch rebaixa rating da Vale para BB- , diz agencia", 38,
          "rebaixamento_rating", "Vale", "o2.com")
check(len(sombra(_B1, _B2)) == 1,
      "[9] §22-B mesma agencia, mesmo nivel, outra fonte = MESMA (corroboracao)")
_C1 = art("Fitch rebaixa rating da Vale para BB-", 90, "rebaixamento_rating", "Vale")
_C2 = art("Fitch rebaixa rating da Vale para B+", 5, "rebaixamento_rating",
          "Vale", "o3.com")
check(len(sombra(_C1, _C2)) == 2,
      "[10] §22-C mesma agencia, acao POSTERIOR e nivel diferente = DISTINTAS")
check(len(ids(sombra(_C1, _C2))) == 2,
      "[11] e com occurrence_ids diferentes — sem colisao")
check(sd._mesmo_episodio_rating(
      {"pub_ts": 0, "nivel_rating": "b1"},
      {"pub_ts": 5 * _DIA, "nivel_rating": "b2"}, 10)[0],
      "[12] dentro da janela de corroboracao o nivel nao decide sozinho")

print()
print("=" * 98)
print("BLOCO C - §5/§6/§23 VALE: regulador sozinho nao e identidade")
print("=" * 98)
_v = {k: POR_ID.get(k) for k in FIXTURE["vale"]["artigos"]}
check(all(_v.values()), "[13] os tres artigos de investigacao da Vale estao na sombra")
check(_v["3c5fafeb65e405877347"][0]["occurrence_id"]
      != _v["eea8fb0b2e52f38f04b8"][0]["occurrence_id"],
      "[14] duas ABERTURAS de processo com assuntos disjuntos (Previ/candidato "
      "x destituicao/conselheiro) sao processos DISTINTOS")
check(_v["7c9ff9161799290c2e41"][0]["occurrence_id"]
      != _v["eea8fb0b2e52f38f04b8"][0]["occurrence_id"],
      "[15] e abrir processo nao e corroboracao de um artigo anterior que nao "
      "abriu nenhum — nem dentro da janela de 10 dias")
_l22 = next(l for l in M["linhas"] if l.get("case_id") == "22")
check(_l22["identidade_ok"] is True,
      f"[16] o caso humano 22 passa a bater ({_l22['identidade_humana']}) — "
      f"bloqueador P1 resolvido")
check(sd.mesmo_episodio("investigacao_regulatoria",
                        {"processo_id": "19957.001234/2026-11", "pub_ts": 0,
                         "abre_processo": True, "objeto_tokens": {"a"}},
                        {"processo_id": "19957.001234/2026-11", "pub_ts": 99 * _DIA,
                         "abre_processo": False, "objeto_tokens": {"b"}}, 10)[0],
      "[17] §23 identificador de processo IGUAL e evidencia forte de MESMA "
      "ocorrencia, mesmo com assunto e data distantes")
check(not sd.mesmo_episodio("investigacao_regulatoria",
                            {"processo_id": "19957.001/2026-11", "pub_ts": 0,
                             "abre_processo": True, "objeto_tokens": {"a"}},
                            {"processo_id": "19957.999/2026-11", "pub_ts": _DIA,
                             "abre_processo": True, "objeto_tokens": {"a"}}, 10)[0],
      "[18] §23 processos DIFERENTES sao distintos ainda que no dia seguinte")
check(not sd.mesmo_episodio("investigacao_regulatoria",
                            {"processo_id": "", "pub_ts": 0, "abre_processo": False,
                             "objeto_tokens": {"alfa"}},
                            {"processo_id": "", "pub_ts": 200 * _DIA,
                             "abre_processo": False, "objeto_tokens": {"beta"}},
                            10)[0],
      "[19] §23 sem identificador e sem assunto comum, NAO se forca a mesma "
      "ocorrencia so por serem do mesmo regulador")

print()
print("=" * 98)
print("BLOCO D - §6/§25 VALE: o representante de um estado CONTINUO")
print("=" * 98)
_pid = "Processo 19957.001234/2026-11"
_D1 = art("CVM abre " + _pid + " sobre conselho da Vale", 60,
          "investigacao_regulatoria", "Vale")
_D2 = art("CVM intima diretores no " + _pid + " da Vale", 30,
          "investigacao_regulatoria", "Vale", "o.com")
_D3 = art("Novo depoimento no " + _pid + " amplia apuracao na Vale", 6,
          "investigacao_regulatoria", "Vale", "o2.com")
_od = sombra(_D1, _D2, _D3)
check(len(_od) == 1,
      f"[20] tres desenvolvimentos do MESMO processo = UMA ocorrencia ({len(_od)})")
check(_od[0]["display_representative_date"]
      == max(m["article_date"] for m in _od[0]["membros"]),
      "[21] §6 e o desenvolvimento substantivo mais RECENTE e o representante")
check(_od[0]["anchor_date"] == _od[0]["initial_date"],
      "[22] §7 mas a ANCORA nao se move — a decisao humana foi sobre exibicao, "
      "e renovacao de investigacao segue EM ABERTO")
check(_od[0]["display_representative"] != _od[0]["anchor_member"],
      "[23] representante e ancora sao campos diferentes, e aqui divergem")
check(not any(m["refresh_effective"] for m in _od[0]["membros"]),
      "[24] §7 nenhum membro marca renovacao efetiva — nada foi inventado")
_tok = sd.escolher_representante(
    [{"fase": sd.ETAPA, "pub_ts": 10, "trust_w": 1.0, "title": "aceita RJ",
      "article_id": "a"},
     {"fase": sd.ACOMPANHAMENTO, "pub_ts": 99, "trust_w": 1.0,
      "title": "afeta clientes", "article_id": "b"}], "recuperacao_judicial")
check(_tok["article_id"] == "a",
      "[25] §25 e 'mais recente' NAO significa 'o ultimo publicado': um "
      "acompanhamento nao vira principal de um estado continuo")

print()
print("=" * 98)
print("BLOCO E - §8..§14 NATURA: um compromisso, varios marcos")
print("=" * 98)
_N1 = art("Natura informa compromisso vinculante da Advent para adquirir de 8% "
          "a 10% do capital", 70, "ma", "Natura &Co")
_N2 = art("Natura: Advent atinge 6,6% do capital; aquisicao decorre do "
          "Compromisso Vinculante de 30 de marco", 40, "ma", "Natura &Co",
          "o.com")
_N3 = art("Natura: Advent alcanca 8% do capital, minimo do Compromisso "
          "Vinculante, e convoca assembleia", 10, "ma", "Natura &Co", "o2.com")
_oN1, _oN2, _oN3 = sombra(_N1), sombra(_N1, _N2), sombra(_N1, _N2, _N3)
check(len(_oN3) == 1,
      f"[26] §9/§10 30/03 + 02/07 + 31/07 = UMA ocorrencia economica ({len(_oN3)})")
check(ids(_oN1) == ids(_oN2) == ids(_oN3),
      "[27] §13 acrescentar o marco de 6,6% e depois o de 8% NAO troca o "
      "occurrence_id")
_fases = [m["phase"] for m in _oN3[0]["membros"]]
check(_fases[0] == sd.INICIACAO,
      f"[28] §11 30/03 e INICIACAO ({_fases[0]})")
check(_fases[1] == sd.MATERIAL and _fases[2] == sd.MATERIAL,
      f"[29] §11 02/07 e 31/07 sao marcos MATERIAIS ({_fases[1:]}) — dentro dos "
      f"quatro estados, sem inventar um quinto")
check(sd.FASES == ("INICIACAO", "ETAPA", "MATERIAL", "ACOMPANHAMENTO", "UNKNOWN"),
      "[30] §11 o enum de fase continua com cinco valores")
check("decorre_de_compromisso" in _oN3[0]["membros"][1]["phase_evidence"],
      f"[31] §14 e a evidencia e o VINCULO ECONOMICO explicito, nao "
      f"similaridade de nome ({_oN3[0]['membros'][1]['phase_evidence'][:44]})")
_semvinculo = art("Natura: Advent atinge 6,6% do capital", 40, "ma",
                  "Natura &Co", "o3.com")
check(sd.fase_de(_semvinculo[1]["title"], "ma")["fase"] != sd.MATERIAL,
      "[32] §14 sem a frase de vinculo o mesmo fato NAO vira marco material — "
      "a regra le a evidencia, nao o nome Advent")

print()
print("=" * 98)
print("BLOCO F - §12 NATURA: os cenarios de renovacao, MEDIDOS e nao decididos")
print("=" * 98)
_m2, _m3 = _oN3[0]["membros"][1], _oN3[0]["membros"][2]
check(_oN3[0]["anchor_date"] == _m3["article_date"],
      f"[33] cenario C medido: com renovacao material a ancora vai ao marco de "
      f"8% ({_oN3[0]['anchor_date']})")
check(_oN3[0]["initial_date"] != _oN3[0]["anchor_date"],
      f"[34] cenario A medido: sem renovacao a ancora ficaria em "
      f"{_oN3[0]['initial_date']}")
check(_m2["refresh_eligible"] and _m3["refresh_eligible"],
      "[35] cenario B medido: ambos os marcos sao ELEGIVEIS")
check(sd.politica_refresh("ma")["status"] == "HUMAN_CONFIRMED",
      "[36] a politica de renovacao de `ma` ja tinha respaldo humano ANTERIOR "
      "(casos 05 e 08) — nao e atribuida a revisao de agora")
_pol = sd.politica_familias()["politicas"]
check(_pol["investigacao_regulatoria"]["status"] == "REPRESENTATIVE_ONLY"
      and _pol["investigacao_regulatoria"]["aberto"],
      "[37] §7/§35 investigacao: representante DECIDIDO, renovacao EM ABERTO")

print()
print("=" * 98)
print("BLOCO G - §15..§17 ENGIE: `conclui` sozinho nao decide fase")
print("=" * 98)
_e1, _e2 = POR_ID["a3de08f211694408beb1"], POR_ID["f3125011635de116cfcb"]
check(_e1[1]["phase"] == sd.ACOMPANHAMENTO,
      f"[38] o artigo de resultado que RECAPITULA a conclusao e "
      f"{_e1[1]['phase']}, nao MATERIAL")
check(not _e1[1]["refresh_effective"] and not _e1[1]["refresh_eligible"],
      "[39] e portanto nao renova nada")
check(_e2[1]["phase"] == sd.MATERIAL,
      "[40] enquanto a conclusao CORRENTE de 10/08 continua MATERIAL — o guarda "
      "nao apagou o fechamento legitimo")
check(_e1[0]["occurrence_id"] == _e2[0]["occurrence_id"],
      "[41] §39 os dois seguem na MESMA ocorrencia")
check(_e1[0]["anchor_date"] == "2026-08-10",
      f"[42] a ancora e a conclusao real ({_e1[0]['anchor_date']})")
check(_e1[1]["effective_event_date"] is None
      and _e1[1]["effective_event_date_source"]
      == "RETROSPECTIVE_REFERENCE_DATE_UNKNOWN",
      "[43] §16 a data efetiva da recapitulacao e declarada DESCONHECIDA em vez "
      "de carimbar a data de publicacao como data do fato")
_l11 = next(l for l in M["linhas"] if l.get("case_id") == "11")
check(_l11["fase_ok"] is True and _l11["refresh_ok"] is True,
      "[44] o caso humano 11 passa a bater em fase E renovacao — bloqueador "
      "P1 resolvido")
check(sd.fase_de("Empresa X conclui follow-on de R$ 1 bi", "follow_on")["fase"]
      == sd.MATERIAL,
      "[45] §17 uma assercao de conclusao CORRENTE segue material")
check(sd.fase_de("Empresa X lucra R$ 100 mi no trimestre e conclui follow-on",
                 "follow_on")["fase"] == sd.ACOMPANHAMENTO,
      "[46] §17 mas a mesma palavra em oracao coordenada apos outra assercao "
      "primaria vira acompanhamento — a distincao e sintatica, nao lexical")
check(sd.fase_de("Empresa X conclui aquisicao e lucra R$ 100 mi", "ma")["fase"]
      == sd.MATERIAL,
      "[47] §17 e a ordem importa: conclusao na oracao PRINCIPAL continua "
      "material mesmo com resultado depois")

print()
print("=" * 98)
print("BLOCO H - §27..§31 as ancoras humanas anteriores NAO regridem")
print("=" * 98)
_por_par = {}
for o in S["ocorrencias"]:
    _por_par.setdefault((o["company"], o["family"]), []).append(o)
_tok = _por_par.get(("Tok&Stok", "recuperacao_judicial"), [])
check(len(_tok) == 1 and _tok[0]["anchor_date"] == _tok[0]["initial_date"],
      "[48] §31 Tok&Stok: uma ocorrencia, acompanhamento nao renova")
_sab = _por_par.get(("Sabesp", "ma"), [])
check(len(_sab) == 2 and {x["canonical_object"] for x in _sab} == {"emae", "castilho"},
      f"[49] §30 Sabesp: EMAE separado de Castilho "
      f"({[x['canonical_object'] for x in _sab]})")
check(all(not m["refresh_effective"] for x in _sab for m in x["membros"]
          if m["phase"] == sd.ETAPA),
      "[50] §30 e a etapa da EMAE nao renova")
check(sd.papel_marcador("cade") == sd.REGULATOR_MARKER,
      "[51] §30 `cade` segue sem carregar identidade")
_smf = _por_par.get(("Smart Fit", "ma"), [])
check(len(_smf) == 1 and _smf[0]["anchor_date"] > _smf[0]["initial_date"]
      and any(m["refresh_effective"] for m in _smf[0]["membros"]),
      f"[52] §28 Smart Fit: fechamento material, renovacao TRUE, ancora avanca "
      f"({_smf[0]['initial_date']} -> {_smf[0]['anchor_date']})")
_suz = _por_par.get(("Suzano", "ma"), [])
check(len(_suz) == 1 and _suz[0]["aliases"] == ["ma:suzano:tissue-ifp"],
      "[53] §29 Suzano: uma transacao, alias declarado")
check(_suz[0]["anchor_date"] == _suz[0]["initial_date"]
      and not any(m["refresh_effective"] for m in _suz[0]["membros"]),
      "[54] §29 e a ancora ja E o fechamento — nenhum delta artificial")
check(not _por_par.get(("Santander Brasil", "troca_ceo")),
      "[55] §27 Santander: segue sem ocorrencia de CEO")
_isa = _por_par.get(("ISA Energia Brasil", "follow_on"), [])
check(len(_isa) == 1 and _isa[0]["anchor_date"] == _isa[0]["initial_date"],
      "[56] §31 ISA: acompanhamento nao renova")
check(any(m["article_date"] == "2026-07-15" for m in _isa[0]["membros"]),
      "[57] §31 e o anuncio original existe no acervo — a suspeita de recall "
      "segue NAO confirmada, sem reabertura")

print()
print("=" * 98)
print("BLOCO I - §27 YOBEL: familia opt-in nao pode ser partida")
print("=" * 98)
_yb = [o for o in S["ocorrencias"] if o["company"] == "Yobel"]
check(len(_yb) == 1,
      f"[58] o incendio da Yobel e UMA ocorrencia ({len(_yb)})")
check(len(_yb[0]["familias_membros"]) > 1,
      f"[59] reunindo event_ids de estagios diferentes "
      f"({_yb[0]['familias_membros']}) — como a producao ja fazia")
check(_yb[0]["score_base"] == max(
      x["score_base"] for x in P["ocorrencias"] if x["company"] == "Yobel"),
      "[60] com o peso-base do estagio mais grave, sem multiplicar score")
_sim = sd.simular(S, P)
check(_sim["empresas"]["Yobel"]["simulated_status"] == P["empresas"]["Yobel"]["status"],
      "[61] e o status simulado volta a bater com a producao — a V1 o "
      "quebrava, e era regressao contra comportamento que ja estava certo")

print()
print("=" * 98)
print("BLOCO J - §26 matriz humana V2, por DIMENSAO")
print("=" * 98)
for _i, (_k, _min) in enumerate(
        (("identidade", 13), ("fase", 14), ("refresh", 6),
         ("representante", 3), ("data_efetiva", 3)), start=62):
    _d = M[_k]
    check(_d["erros"] == 0 and _d["avaliaveis"] >= _min,
          f"[{_i}] {_k}: {_d['acertos']}/{_d['avaliaveis']}, zero erros")
check(M["identidade_limitada_pela_janela"] == 2,
      f"[67] 2 casos limitados pela JANELA de score, contados a parte "
      f"({M['identidade_limitada_pela_janela']})")
check(M["identidade_limitada_pelo_corpus"] == 1,
      "[68] e 1 limitado pelo CORPUS — Natura: o anuncio de 30/03 nunca foi "
      "coletado, entao nenhuma regra de ocorrencia poderia liga-lo")
_l14 = next(l for l in M["linhas"] if l.get("case_id") == "14")
check(_l14["identidade_limitada_pelo_corpus"],
      "[69] o caso 14 e classificado como lacuna de COLETA, nao como erro de "
      "identidade — a distincao evita que a metrica minta")

print()
print("=" * 98)
print("BLOCO K - §19/§20 a verdade humana anterior fica INTOCADA")
print("=" * 98)
_MS = hs.carregar()["memberships"]
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[70] supervisao humana intacta (27/24) — nenhuma substituicao destrutiva")
check(all(m.get("adjudicated_by") == "gustavo" for m in _MS.values()),
      "[71] e nenhuma resposta humana anterior foi reinterpretada")
_sh = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ot = _sh["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[72] occurrence_truth intacto (10/21/4)")
check(FIXTURE["provenance"] == CALIBRACAO
      and FIXTURE["natura"]["nota"].startswith("30/03"),
      "[73] §19 a cronologia da Natura vive em FIXTURE com proveniencia "
      "explicita, porque o schema humano indexa por article_id e esses "
      "artigos nao existem no acervo")
check(FIXTURE["production_score_authority"] == "NONE",
      "[74] e a fixture declara que nao tem autoridade de producao")

print()
print("=" * 98)
print("BLOCO L - §32..§37 blast V2, politica e portao")
print("=" * 98)
_sim2 = sd.simular(S, P)
B = sd.blast(S, P, M, _sim2)
F = sd.fidelidade(S, P)
T = sd.avaliar_occurrence_truth(S)
PR = sd.promocao(S, P, M, T, B, F)
check(F["membros_sem_article_id"] == 0
      and F["membros_com_article_id"] == F["membros_sombra"],
      f"[75] §38 proveniencia 100% preservada "
      f"({F['membros_com_article_id']}/{F['membros_sombra']})")
check(not sd.colisoes_de_id(S),
      "[76] §37 zero colisao de occurrence_id")
check(len(B["sub_fusao_fusoes"]) == 0,
      "[77] §32 nenhuma sub-fusao: a sombra nao colapsa o que a producao separa")
check(sum(1 for x in B["controle_negativo_sem_renovacao"] if x["renovou"]) == 0,
      "[78] §29 nenhum ETAPA/ACOMPANHAMENTO posterior a ancora renovou")
check(len(B["delta_status"]) == 1 and B["delta_status"][0]["company"] == "JBS",
      f"[79] §33 sobra UMA mudanca de status ({[x['company'] for x in B['delta_status']]}) "
      f"— a da Yobel foi eliminada pela correcao de familia opt-in")
check(not B["delta_status"][0]["suportado_por_humano"],
      "[80] §33 e ela NAO e promovida so por ter a causa nomeada: a divisao "
      "que a produz ainda e AMBIGUOUS")
check(any("NAO confirmada por humano" in b for b in PR["bloqueadores"]),
      f"[81] §37 por isso ela BLOQUEIA a promocao ({PR['bloqueadores']})")
check(PR["pronto_para_promover"] is False, "[82] §46 promocao nao e automatica")
_q = sd.fila_revisao(B, M)
check(len(_q) <= 10 and all(x["veredito"] == "REVIEW_CANDIDATE" for x in _q),
      f"[83] §36 fila com no maximo 10 candidatos ({len(_q)})")
check(not any(x.get("case_id") in ("02", "11", "14", "22") for x in _q),
      "[84] §36 e nenhum dos quatro P1 resolvidos continua na fila")
check(all(x["classificacao"] in ("HUMAN_CONFIRMED", "UNREVIEWED")
          for x in B["renovacoes"]), "[85] §34 renovacoes seguem classificadas")
_btg = [x for x in B["renovacoes"] if x["company"] in ("BTG Pactual", "Baker Hughes")]
check(_btg and all(x["classificacao"] == "UNREVIEWED" for x in _btg),
      f"[86] §34 BTG e Baker Hughes seguem UNREVIEWED — a calibracao de hoje "
      f"nao lhes empresta verdade ({len(_btg)} casos)")
_pf = sd.politica_familias()["politicas"]
check(_pf["follow_on"]["status"] == "PARTIALLY_ESTABLISHED" and _pf["follow_on"]["aberto"],
      "[87] §35 follow_on: recapitulacao decidida, conclusao corrente EM ABERTO")
check(_pf["emissao_divida"]["status"] == "UNREVIEWED",
      "[88] §35 emissao_divida: segue sem verdade humana")
check(_pf["rebaixamento_rating"]["status"] == "IDENTITY_ONLY"
      and "nao politica de renovacao" in _pf["rebaixamento_rating"]["aberto"],
      "[89] §35 rating: a decisao foi de IDENTIDADE, nao de renovacao")

print()
print("=" * 98)
print("BLOCO M - §40/§41 producao intocada e reprodutor exato")
print("=" * 98)
_prod = io.open("risk_dashboard.py", encoding="utf-8").read()
_sem = io.open("semantic_audit.py", encoding="utf-8").read()
check(rp.equivalencia(P)["ok"], "[90] o reprodutor de producao segue EXATO")
check("reliability_occurrence_shadow" not in _prod
      and "reliability_occurrence_shadow" not in _sem,
      "[91] nenhum caminho de producao importa a sombra")
check("def build_evolution" in _prod and "def assign_occurrence_clusters" in _prod,
      "[92] build_evolution e o clustering seguem sem reescrita")
check(all(o["authority"] == "SHADOW / SIMULATED" for o in S["ocorrencias"]),
      "[93] §21 toda ocorrencia sai rotulada SHADOW / SIMULATED")


def _forma(X):
    return [(o["occurrence_id"], o["company"], o["family"], o["canonical_object"],
             o["occurrence_instance_signature"], o["anchor_date"],
             o["display_representative"],
             tuple(m["article_id"] for m in o["membros"]))
            for o in X["ocorrencias"]]


check(_forma(sd.construir()) == _forma(S),
      "[94] a sombra calibrada segue deterministica")
check(rp.equivalencia(rp.reproduzir())["ok"],
      "[95] e roda-la nao contaminou o estado da producao")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7r (calibracao humana V2): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
