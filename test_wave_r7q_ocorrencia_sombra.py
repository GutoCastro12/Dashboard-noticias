#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7q_ocorrencia_sombra.py — 4I.2 R7q · OCCURRENCE SHADOW V1.

A SOMBRA TEM DE SER PROVADA POR CONSTRUCAO, NAO POR CONCORDANCIA COM O ACERVO.

Os achados de corpus mudam quando o cron acrescenta artigos. O que NAO pode
mudar sao as invariantes de arquitetura, e e delas que este arquivo trata:

  * id estavel: acrescentar fonte, etapa, fechamento ou comentario NAO troca o
    `occurrence_id`; um evento economico genuinamente novo TROCA;
  * ruido de regulador nao funde objetos distintos;
  * alias DECLARADO funde, similaridade de nome NAO;
  * fase != autoridade de renovacao;
  * a ancora nunca anda para tras;
  * nenhum artigo local perde `article_id` ao virar membro.

As metamorficas usam historico SINTETICO montado sobre a config real: assim
elas provam o comportamento sem depender de qual noticia entrou hoje.

O QUE ESTE ARQUIVO DELIBERADAMENTE NAO FAZ

Nao exige que a sombra concorde com o humano onde ela discorda. Onde ha
divergencia, o teste afirma a DIVERGENCIA MEDIDA e a fila de revisao a carrega.
Codificar a preferencia humana como expectativa transformaria sombra em
promocao disfarcada — e esta onda e explicitamente §39 DO NOT PROMOTE YET.
"""
from __future__ import annotations

import hashlib
import io
import json

import reliability_occurrence_reproducer as rp
import reliability_occurrence_shadow as sd
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
_DIA = 86400
_BASE = 1780000000          # instante fixo; nada aqui depende do relogio


def art(titulo, dias_atras, eid, empresa, dominio="exemplo.com"):
    """Um registro de historico minimo, no formato que a producao le."""
    ts = int(__import__("time").time()) - dias_atras * _DIA
    import datetime as _dt
    iso = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    u = "https://" + dominio + "/" + hashlib.sha1(
        titulo.encode("utf-8")).hexdigest()[:16]
    return u, {"url": u, "title": titulo, "companies": [empresa],
               "events_by_company": {empresa: [eid]}, "pub_ts": ts,
               "pub_iso": iso, "source": dominio, "domain": dominio}


def hist(*artigos):
    return {"articles": dict(artigos)}


def sombra(*artigos):
    return sd.construir(hist(*artigos), CFG)["ocorrencias"]


def ids(occs):
    return {o["occurrence_id"] for o in occs}


print("=" * 98)
print("BLOCO A - a sombra nao tem autoridade e nao escreve")
print("=" * 98)
_src = io.open("reliability_occurrence_shadow.py", encoding="utf-8").read()
_prod = io.open("risk_dashboard.py", encoding="utf-8").read()
_sem = io.open("semantic_audit.py", encoding="utf-8").read()
check(sd.AUTORIDADE["production_score_authority"] == "NONE",
      "[1] autoridade de score NENHUMA")
check(sd.AUTORIDADE["production_occurrence_authority"] == "NONE",
      "[2] autoridade de ocorrencia NENHUMA")
check(sd.AUTORIDADE["write_authority"] == "NONE", "[3] autoridade de escrita NENHUMA")
check("reliability_occurrence_shadow" not in _prod,
      "[4] nenhum caminho de producao importa a sombra")
check("reliability_occurrence_shadow" not in _sem, "[5] nem o motor semantico")
_escrita = [t for t in ('open(", "w"', ", 'w'", ', "w"', ".write(", "json.dump(",
                        "rd.save_history", "hs.salvar(", "os.replace")
            if t in _src]
check(not _escrita, f"[6] o modulo nao abre nada para escrita ({_escrita})")

print()
print("=" * 98)
print("BLOCO B - §32 id ESTAVEL sob acrescimo de membro")
print("=" * 98)
_A = art("Alfa Energia anuncia aquisicao da Betaco por R$ 2,0 bilhoes", 40, "ma", "Vale")
_B = art("Alfa compra Betaco: leia o anuncio na integra", 39, "ma", "Vale", "outro.com")
_C = art("Cade aprova a aquisicao da Betaco", 30, "ma", "Vale", "terceiro.com")
_D = art("Alfa conclui aquisicao da Betaco por R$ 2,0 bilhoes", 20, "ma", "Vale",
         "quarto.com")
_E = art("O que muda para o setor apos a compra da Betaco", 10, "ma", "Vale",
         "quinto.com")
_oA, _oAB, _oABC = sombra(_A), sombra(_A, _B), sombra(_A, _B, _C)
_oABCD, _oTudo = sombra(_A, _B, _C, _D), sombra(_A, _B, _C, _D, _E)
check(len(_oA) == 1 and len(_oTudo) == 1,
      f"[7] cinco artigos do mesmo fato = UMA ocorrencia ({len(_oTudo)})")
check(ids(_oA) == ids(_oAB), "[8] acrescentar 2a FONTE nao troca o occurrence_id")
check(ids(_oA) == ids(_oABC), "[9] acrescentar ETAPA regulatoria nao troca o id")
check(ids(_oA) == ids(_oABCD), "[10] acrescentar FECHAMENTO nao troca o id")
check(ids(_oA) == ids(_oTudo), "[11] acrescentar COMENTARIO nao troca o id")
check(_oTudo[0]["n_membros"] == 5, f"[12] e os 5 membros ficam ({_oTudo[0]['n_membros']})")
check(all(m["article_id"] for m in _oTudo[0]["membros"]),
      "[13] todos com article_id — nenhum vira corroboracao anonima")

print()
print("=" * 98)
print("BLOCO C - §33 MESMO objeto, evento economico DISTINTO")
print("=" * 98)
# Duas sucessoes reais de CEO na mesma empresa: e o caso adjudicado da Hapvida
# (DISTINCT_OCCURRENCE em occurrence_truth), reproduzido em forma minima.
_S1 = art("Vale anuncia Joao Pereira como novo presidente", 80, "troca_ceo", "Vale")
_S2 = art("Vale anuncia Maria Andrade como nova presidente", 15, "troca_ceo",
          "Vale", "outro.com")
_o2 = sombra(_S1, _S2)
check(len(_o2) == 2, f"[14] duas sucessoes = DUAS ocorrencias ({len(_o2)})")
check(len(ids(_o2)) == 2, "[15] com occurrence_ids DIFERENTES")
check(all(o["id_stability"] == "CONTENT_STABLE" for o in _o2),
      "[16] e ambos estaveis por CONTEUDO (as pessoas), nao por data")
# duas emissoes distintas separadas por SERIE: MESMO objeto (o proprio
# emissor), evento economico diferente — a demonstracao mais limpa de que
# `company|family|objeto` nao pode ser a identidade final
_E1 = art("Vale aprova 16a emissao de debentures", 70, "emissao_divida", "Vale")
_E2 = art("Vale aprova 17a emissao de debentures", 12, "emissao_divida", "Vale",
          "outro.com")
_oe = sombra(_E1, _E2)
_chave_ingenua = {(o["company"], o["family"], o["canonical_object"]) for o in _oe}
check(len(_chave_ingenua) == 1,
      f"[17] `company|family|objeto` daria UMA chave para as duas emissoes "
      f"({_chave_ingenua}) — e por isso que a camada de INSTANCIA existe")
check(len(_oe) == 2, f"[18] mas 16a x 17a = DUAS ocorrencias ({len(_oe)})")

print()
print("=" * 98)
print("BLOCO D - §34 objetos DISTINTOS, MESMO regulador")
print("=" * 98)
_R1 = art("Sabesp: Cade analisa aquisicao da Emae", 40, "ma", "Sabesp")
_R2 = art("Sabesp: Cade aprova aquisicao da Sanessol", 35, "ma", "Sabesp",
          "outro.com")
_or = sombra(_R1, _R2)
check(len(_or) == 2, f"[19] EMAE e Sanessol NAO se fundem por `cade` ({len(_or)})")
check(sd.papel_marcador("cade") == sd.REGULATOR_MARKER,
      "[20] `cade` e REGULATOR_MARKER — contexto, nunca identidade")
check(sd.papel_marcador("emae") == sd.OBJECT_MARKER,
      "[21] e `emae` e OBJECT_MARKER")
check(any("cade" in m["marcadores_regulador"] for o in _or for m in o["membros"]),
      "[22] o marcador de regulador nao e apagado — ele so muda de papel")
check(sd.papel_marcador("reuters") == sd.CONTEXT_MARKER
      and sd.papel_marcador("milhoes") == sd.GENERIC_MARKER,
      "[23] os quatro papeis existem e sao distintos")

print()
print("=" * 98)
print("BLOCO E - §35/§15 alias DECLARADO funde; nome parecido NAO")
print("=" * 98)
_K = art("Cade aprova aquisicao pela Suzano de sociedade de tissue da Kimberly-Clark",
         60, "ma", "Suzano")
_X = art("Suzano conclui aquisicao de 51% da Arbex", 25, "ma", "Suzano", "outro.com")
_ok = sombra(_K, _X)
check(len(_ok) == 1, f"[24] com alias declarado, Kimberly-Clark e Arbex sao UMA "
                     f"ocorrencia ({len(_ok)})")
check(_ok[0]["aliases"] == ["ma:suzano:tissue-ifp"],
      f"[25] e a relacao de alias fica EXPLICITA no registro ({_ok[0]['aliases']})")
_A1 = sd.carregar_aliases()
check(all(r["source"] for v in _A1.values() for r in v),
      "[26] todo alias carregado tem FONTE — sem proveniencia nao entra")
_N1 = art("Vale conclui aquisicao da Nortec Mineracao", 40, "ma", "Vale")
_N2 = art("Vale conclui aquisicao da Nortek Servicos", 20, "ma", "Vale", "outro.com")
check(len(sombra(_N1, _N2)) == 2,
      "[27] `Nortec` e `Nortek` NAO se fundem — a sombra nao tem autoridade de "
      "similaridade difusa")
_tok, _usados = sd.canonicalizar({"kimberly"}, "Suzano", "ma", _A1)
check(_tok == {"suzano-tissue-ifp"} and _usados == ["ma:suzano:tissue-ifp"],
      "[28] canonicalizar troca o token pelo grupo e registra qual regra usou")
check(sd.canonicalizar({"kimberly"}, "Outra Empresa", "ma", _A1)[0] == {"kimberly"},
      "[29] e o alias e ESCOPADO: nao vaza para outro emissor")

print()
print("=" * 98)
print("BLOCO F - §36 multi-fonte: uma ocorrencia, uma autoridade de score")
print("=" * 98)
_M1 = art("Gama anuncia aquisicao da Deltaco por R$ 900 milhoes", 30, "ma", "Vale")
_M2 = art("Gama anuncia aquisicao da Deltaco por R$ 900 milhoes", 30, "ma", "Vale",
          "segundoveiculo.com")
_om = sombra(_M1, _M2)
check(len(_om) == 1, f"[30] dois veiculos, UMA ocorrencia ({len(_om)})")
check(_om[0]["n_membros"] == 2 and _om[0]["n_dominios"] == 2,
      "[31] dois membros, dois dominios — a corroboracao e preservada")
check(_om[0]["simulated_contribution"] == sombra(_M1)[0]["simulated_contribution"],
      "[32] e a 2a fonte NAO multiplica o score simulado")

print()
print("=" * 98)
print("BLOCO G - §37 metamorficas de fase e renovacao")
print("=" * 98)
_P = art("Omega anuncia aquisicao da Zetaco por R$ 1,0 bilhao", 50, "ma", "Vale")
# M1 · anuncio + comentario
_cmt = art("Analistas veem a compra da Zetaco como positiva", 5, "ma", "Vale", "o.com")
_m1 = sombra(_P, _cmt)
check(len(_m1) == 1 and _m1[0]["anchor_date"] == _m1[0]["initial_date"],
      "[33] M1 comentario nao renova a ancora")
check(not any(m["refresh_effective"] for m in _m1[0]["membros"]),
      "[34] M1 e nenhum membro marca renovacao efetiva")
# M2 · anuncio + etapa de processo
_etp = art("Cade aprova sem restricoes a compra da Zetaco", 5, "ma", "Vale", "o2.com")
_m2 = sombra(_P, _etp)
check(len(_m2) == 1 and _m2[0]["anchor_date"] == _m2[0]["initial_date"],
      "[35] M2 etapa regulatoria nao renova a ancora")
check(_m2[0]["membros"][1]["phase"] == sd.ETAPA
      and not _m2[0]["membros"][1]["refresh_eligible"],
      "[36] M2 a etapa e ETAPA e NAO e elegivel")
# M3 · anuncio + fechamento material posterior
_fec = art("Omega conclui aquisicao da Zetaco por R$ 1,0 bilhao", 5, "ma", "Vale",
           "o3.com")
_m3 = sombra(_P, _fec)
check(len(_m3) == 1 and _m3[0]["anchor_date"] > _m3[0]["initial_date"],
      f"[37] M3 fechamento material AVANCA a ancora "
      f"({_m3[0]['initial_date']} -> {_m3[0]['anchor_date']})")
check(_m3[0]["refresh_reason"].startswith("MATERIAL_PHASE"),
      f"[38] M3 e o motivo fica auditavel ({_m3[0]['refresh_reason']})")
check(_m3[0]["simulated_contribution"] > _m1[0]["simulated_contribution"],
      "[39] M3 a contribuicao simulada reflete a ancora mais nova")
# M4 · fase material ANTERIOR a uma ancora ja mais nova
check(sd.ancora_efetiva("2026-08-14", "2026-05-05") == "2026-08-14",
      "[40] M4 material mais ANTIGO nao move a ancora para tras — e o caso "
      "Citigroup, que custaria -28,4 pontos")
check(sd.ancora_efetiva("2026-05-05", "2026-08-14") == "2026-08-14",
      "[41] M4 e material mais NOVO move para frente")
check(sd.ancora_efetiva("", "2026-05-05") == "2026-05-05"
      and sd.ancora_efetiva("2026-05-05", "") == "2026-05-05",
      "[42] M4 a regra e total: sem candidata ou sem ancora nao quebra")
_mm = sombra(_P, _fec, _cmt, _etp)
check(_mm[0]["anchor_date"] == _m3[0]["anchor_date"],
      "[43] M4 acrescentar etapa e comentario DEPOIS nao altera a ancora material")
# M5/M6 ja provados nos blocos D e E
check(len(sombra(_K, _X)) == 1 and len(sombra(_R1, _R2)) == 2,
      "[44] M5 alias funde e M6 regulador comum nao funde")

print()
print("=" * 98)
print("BLOCO H - §18/§19 representante NAO e a ancora de score")
print("=" * 98)
check(_m3[0]["display_representative"] != _m3[0]["anchor_member"],
      "[45] no caso anuncio+fechamento, representante e ancora sao membros "
      "DIFERENTES — sao perguntas diferentes")
check(_m3[0]["display_representative_date"] == _m3[0]["initial_date"],
      "[46] o representante e o artigo que EXPLICA (o anuncio)")
check(_m3[0]["anchor_date"] != _m3[0]["display_representative_date"],
      "[47] e a ancora e o marco material — a recencia do risco")
_sos = sombra(_cmt)
check(_sos and _sos[0]["display_representative"] == _sos[0]["anchor_member"],
      "[48] com um membro so, os dois coincidem sem regra especial")

print()
print("=" * 98)
print("BLOCO I - §4/§20 o fallback e CONSERVADOR, e diz que e fallback")
print("=" * 98)
# todas as palavras capitalizadas sao genericas de comunicado: nao sobra
# token de objeto nenhum
_vago = art("Fato Relevante da Companhia sobre Acoes", 30, "ma", "Vale")
_vago2 = art("Comunicado ao Mercado da Companhia", 3, "ma", "Vale", "o.com")
_ov = sombra(_vago, _vago2)
check(all(o["object_confidence"] in ("UNKNOWN", "WEAK", "STRONG", "SELF")
          for o in _ov), "[49] toda ocorrencia declara a confianca do objeto")
check(any(o["object_confidence"] == "UNKNOWN" for o in _ov),
      "[50] sem objeto identificavel a confianca sai UNKNOWN, nao inventada")
check(all(m["effective_event_date_source"] == "ARTICLE_DATE_FALLBACK"
          for o in _ov for m in o["membros"]),
      "[51] §20 a data efetiva diz que caiu no fallback — sem inferencia silenciosa")
check(sd.fase_de("Titulo sem verbo de fase nenhum", "ma")["fase"] == sd.UNKNOWN,
      "[52] §7 familia de PROCESSO sem evidencia = UNKNOWN, nao chute")
check(sd.fase_de("Moody's rebaixa o rating", "rebaixamento_rating")["fase"]
      == sd.INICIACAO,
      "[53] §7 familia PONTUAL: a assercao do fato E a iniciacao dele")
check(set(sd.FASES) == {"INICIACAO", "ETAPA", "MATERIAL", "ACOMPANHAMENTO",
                        "UNKNOWN"}, "[54] quatro estados + UNKNOWN, nem um a mais")

print()
print("=" * 98)
print("BLOCO J - §31 PROVENIENCIA: o ganho arquitetural duro")
print("=" * 98)
S = sd.construir()
P = rp.reproduzir()
F = sd.fidelidade(S, P)
check(F["membros_sem_article_id"] == 0,
      f"[55] NENHUM membro da sombra perde article_id "
      f"({F['membros_com_article_id']}/{F['membros_sombra']})")
# Esta checagem media um DEFEITO da producao legada: artigos absorvidos que
# perdiam o `article_id`. A promocao de `f880419`+ fechou a lacuna. Manter a
# expectativa antiga exigiria que o defeito continuasse existindo — o oposto
# de uma invariante. O que fica travado agora e o FECHAMENTO.
_prod_mem = [m for l in rd.build_evolution(
    json.load(io.open("risk_history.json", encoding="utf-8")),
    rd.load_config("config_risco.yaml"))
    for o in (l.get("events") or []) if o.get("_ocorrencia")
    for m in o["_ocorrencia"]["members"]]
check(_prod_mem and all(m["article_id"] for m in _prod_mem),
      f"[56] e a producao PROMOVIDA nao perde mais nenhum: "
      f"{len(_prod_mem)}/{len(_prod_mem)} membros com article_id")
check(F["membros_com_article_id"] == F["membros_sombra"],
      "[57] retencao de proveniencia = 100%")
check(F["representantes_cobertos_pela_sombra"] >= F["representantes_producao"] - 2,
      f"[58] e a sombra cobre os representantes da producao "
      f"({F['representantes_cobertos_pela_sombra']}/{F['representantes_producao']})")

print()
print("=" * 98)
print("BLOCO K - §23/§24 verdade humana e occurrence_truth (MEDIDAS, nao exigidas)")
print("=" * 98)
M = sd.matriz_humana(S)
T = sd.avaliar_occurrence_truth(S)
check(M["identidade"]["avaliaveis"] + M["fase"]["avaliaveis"]
      + M["refresh"]["avaliaveis"] > 0, "[59] a matriz humana tem casos avaliaveis")
check(M["identidade"]["acertos"] > M["identidade"]["erros"],
      f"[60] identidade: {M['identidade']['acertos']} acertos x "
      f"{M['identidade']['erros']} erros")
check(M["fase"]["erros"] <= 2,
      f"[61] fase: {M['fase']['acertos']}/{M['fase']['avaliaveis']}")
check(M["refresh"]["acertos"] >= 4,
      f"[62] renovacao: {M['refresh']['acertos']}/{M['refresh']['avaliaveis']}")
check(M["identidade_limitada_pela_janela"] > 0,
      f"[63] e a metrica separa erro de identidade de LIMITE DE JANELA "
      f"({M['identidade_limitada_pela_janela']} caso(s)) em vez de mentir")
check(M["fase_indefinida"] >= 0 and isinstance(M["fase_indefinida"], int),
      f"[64] UNKNOWN e contado a parte de fase ERRADA ({M['fase_indefinida']})")
check(T["memberships_totais"] == 21,
      f"[65] occurrence_truth lido inteiro ({T['memberships_totais']} memberships)")
check(T["fase"]["erros"] == 0,
      f"[66] e onde ele e avaliavel a fase bate "
      f"({T['fase']['acertos']}/{T['fase']['avaliaveis']})")
check(not T["verdades_fragmentadas_pela_sombra"],
      "[67] nenhuma ocorrencia-verdade foi partida em duas pela sombra")

print()
print("=" * 98)
print("BLOCO L - §26/§27/§28/§29 os blasts")
print("=" * 98)
sim = sd.simular(S, P)
B = sd.blast(S, P, M, sim)
check(sim["modelo_status_fidelidade"]["reproduzidas"] >= 55,
      f"[68] o modelo de status derivado reproduz a producao em "
      f"{sim['modelo_status_fidelidade']['reproduzidas']}/63 — e as divergentes "
      f"sao EXCLUIDAS do blast de status")
check(len(B["sobre_fusao_divisoes"]) > 0,
      f"[69] §26 ha divisao de sobre-fusao ({len(B['sobre_fusao_divisoes'])} pares)")
check(all(x["confianca"] in ("HUMAN_CONFIRMED", "HIGH_CONFIDENCE", "AMBIGUOUS")
          for x in B["sobre_fusao_divisoes"]),
      "[70] §26 toda divisao e classificada por confianca")
# As divisoes confirmadas por humano (Cosan, Vale, JBS, Sabesp) ja foram
# PROMOVIDAS: a producao as faz sozinha, entao elas somem da lista de
# "divergencias da sombra contra a producao". O que resta e o que ainda nao
# foi promovido, e nao ha por que exigir que uma delas seja humana.
check(all(x["confianca"] in ("HUMAN_CONFIRMED", "HIGH_CONFIDENCE", "AMBIGUOUS")
          for x in B["sobre_fusao_divisoes"]),
      f"[71] §26 e as remanescentes seguem todas classificadas "
      f"({len(B['sobre_fusao_divisoes'])} pares)")
check(len(B["sub_fusao_fusoes"]) == 0,
      f"[72] §27 nenhuma sub-fusao: a sombra nao colapsa o que a producao separa "
      f"({len(B['sub_fusao_fusoes'])})")
check(len(B["renovacoes"]) > 0,
      f"[73] §28 ha renovacoes materiais ({len(B['renovacoes'])})")
check(all(x["classificacao"] in ("HUMAN_CONFIRMED", "UNREVIEWED")
          for x in B["renovacoes"]),
      "[74] §28 cada renovacao e HUMAN_CONFIRMED ou UNREVIEWED — nunca promovida "
      "a verdade sozinha")
check(sum(1 for x in B["controle_negativo_sem_renovacao"] if x["renovou"]) == 0,
      f"[75] §29 NENHUM membro ETAPA/ACOMPANHAMENTO posterior a ancora renovou "
      f"({len(B['controle_negativo_sem_renovacao'])} verificados)")
check(all(x["fase"] in (sd.ETAPA, sd.ACOMPANHAMENTO)
          for x in B["controle_negativo_sem_renovacao"]),
      "[76] §29 e o controle so olha as fases que NAO podem renovar")
check(len(B["delta_status"]) <= 5,
      f"[77] §26 o blast de status e pequeno e enumeravel "
      f"({len(B['delta_status'])})")
# A sombra NAO tem portao de direcao e a producao promovida tem: divergencia
# de status entre as duas passou a medir a POLITICA HUMANA, nao um defeito.
# O que continua proibido e divergir por IDENTIDADE errada.
check(not [x for x in sd.prontidao(S, P, M, T, B, F, sd.simular(S, P))["score"]
           ["status_deltas"] if x["categoria"] == sd.ERRO_OCORRENCIA],
      f"[78] §42 nenhuma divergencia de status por identidade ERRADA "
      f"({[x['company'] for x in B['delta_status']]} divergem por politica)")

print()
print("=" * 98)
print("BLOCO M - §40/§41/§42 fila de revisao e bloqueadores")
print("=" * 98)
Q = sd.fila_revisao(B, M)
PR = sd.promocao(S, P, M, T, B, F)
check(len(Q) <= 10, f"[79] §41 a fila tem no maximo 10 casos ({len(Q)})")
check(all(x["veredito"] == "REVIEW_CANDIDATE" for x in Q),
      "[80] §40 todo item e REVIEW_CANDIDATE — nenhum vira verdade sozinho")
check(all(x["prioridade"] <= 3 for x in Q)
      and Q == sorted(Q, key=lambda x: (x["prioridade"], x["company"])),
      "[81] §41 priorizada: status primeiro, depois score, depois ambiguidade")
check(all(x["prioridade"] == 1 for x in Q if x["tipo"] == "STATUS_IMPACT"),
      "[82] §41 impacto de status, QUANDO existe, vem em prioridade 1 — a "
      "contagem varia com o acervo, a ordenacao nao")
check(sd.prontidao(S, P, M, T, B, F, sd.simular(S, P))["score"]["pronta"]
      is False,
      "[83] §42 e o lado do SCORE segue bloqueado: identidade limpa nao "
      "autoriza autoridade de pontuacao")
check(PR["retencao_de_proveniencia"].split("/")[0]
      == PR["retencao_de_proveniencia"].split("/")[1],
      "[84] §42 a retencao de proveniencia entra nas metricas de promocao")
check(sd.POLITICA_REFRESH["ma"]["status"] == "HUMAN_CONFIRMED"
      and sd.POLITICA_REFRESH["ma"]["source"],
      "[85] §8 a politica de renovacao de `ma` tem fonte humana declarada")
check(sd.politica_refresh("familia_inexistente")["status"] == "UNREVIEWED",
      "[86] §8 e as demais familias ficam UNREVIEWED — nenhuma regra universal "
      "inferida alem da evidencia")

print()
print("=" * 98)
print("BLOCO N - §43/§39 a producao continua exata e intocada")
print("=" * 98)
check(rp.equivalencia(P)["ok"], "[87] o reprodutor de producao segue EXATO")
check("def build_evolution" in _prod and "def assign_occurrence_clusters" in _prod,
      "[88] build_evolution e o clustering seguem sem reescrita")
# o determinismo que importa e o ESTRUTURAL: `simulated_contribution` cai
# continuamente com o relogio (decaimento), entao compara-lo entre duas
# chamadas mediria o tempo passar, nao a estabilidade da arquitetura
def _forma(X):
    return [(o["occurrence_id"], o["company"], o["family"], o["canonical_object"],
             o["occurrence_instance_signature"], o["anchor_date"],
             o["display_representative"],
             tuple(m["article_id"] for m in o["membros"]))
            for o in X["ocorrencias"]]


_S2 = sd.construir()
check(_forma(_S2) == _forma(S),
      "[89] a sombra e deterministica: duas construcoes dao a mesma estrutura "
      "de ocorrencia, membro por membro")
check(rp.equivalencia(rp.reproduzir())["ok"],
      "[90] §43 e roda-la NAO contaminou o estado da producao")
_sh = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ot = _sh["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[91] occurrence_truth intacto (10/21/4)")
import reliability_human_supervision as hs
_MS = hs.carregar()["memberships"]
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[92] supervisao humana intacta (27/24)")
check(all(o["authority"] == "SHADOW / SIMULATED" for o in S["ocorrencias"]),
      "[93] §38 toda ocorrencia da sombra sai rotulada SHADOW / SIMULATED")
_col = sd.colisoes_de_id(S)
check(not _col,
      f"[94] nenhum occurrence_id colide no acervo real ({_col}) — foi este "
      f"teste que expos a acao de rating tratando o EMISSOR como objeto externo")
check(len({o["occurrence_id"] for o in S["ocorrencias"]}) == len(S["ocorrencias"]),
      "[95] e o id e chave, nao rotulo")
check("colisoes_de_id" in PR,
      "[96] a colisao entra nas metricas de promocao — detectada e reportada, "
      "nunca remendada com sufixo de ordem (isso seria o indice de cluster de "
      "volta, que §3 proibe)")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7q (occurrence shadow v1): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
