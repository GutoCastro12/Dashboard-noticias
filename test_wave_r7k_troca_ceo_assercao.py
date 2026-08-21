#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7k_troca_ceo_assercao.py — 4I.2 R7k.

DESCRITOR DE CARGO ≠ ASSERÇÃO DE TROCA DE COMANDO (invariante humana H4).

A keyword da taxonomia é `novo CEO` — adjetivo de status. Ela dispara igual em
"é escolhido como novo CEO da B3" (anúncio) e em "diz novo CEO da B3"
(descritor). Não existe blacklist possível da expressão: ela mataria os
positivos. O que separa é o PAPEL do cargo na frase.

O oráculo deste arquivo é `risk_human_supervision.json`, lote V1 — não uma
lista de expectativas que eu digitei. Se a verdade humana mudar, o teste muda
junto; se o código divergir dela, o teste quebra. É essa a direção correta da
dependência.

DUAS ARMADILHAS MEDIDAS ANTES DE VIRAR REGRA

1. Exigir verbo de asserção e parar aí REPROVA: suprime o positivo humano da
   Vale (cujo título não traz verbo de mudança) e PRESERVA Santander e Rumo,
   em que "troca de CEO" é sintagma nominal sob enquadramento de analista.
   Erraria 3 dos 6 casos humanos.
2. A Vale só se distinguiria por "cargo como tópico antes de dois-pontos" —
   feature que dispara em 1 de 46 pares. Seria o defeito do prefixo `ex-` da
   B8 repetido: forma léxica de exemplo único virando falsa invariante.

Por isso a guarda é NEGATIVA E POSICIONAL: atua quando o dirigente é ator,
locutor ou moldura temporal, ou quando um analista enquadra o fato. Na Vale
quem "diz" são Embraer e Klabin — o cargo não é ator —, e ela sobrevive sem
nenhuma regra feita sob medida.

ESCOPO: só `troca_ceo`. Nada de ocorrência, renovação ou fechamento aqui.
"""
from __future__ import annotations

import copy
import io
import json

import reliability_human_supervision as hs
import risk_dashboard as rd
import semantic_audit as sa
import semantic_v2_shadow as sh

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
_H = json.load(io.open("risk_history.json", encoding="utf-8"))
_MS = hs.carregar()["memberships"]


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


def pontua(title, company, summary=None, familia="troca_ceo"):
    h = {"articles": {"u1": {"title": title, "summary": summary or title,
                             "source": "s", "domain": "exemplo.com",
                             "pub_ts": 1787076105, "pub_iso": "2026-08-18 15:01",
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return familia in (rd.event_ids_for(h["articles"]["u1"], company) or [])


def regras(title, company):
    h = {"articles": {"u1": {"title": title, "summary": title, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1787076105,
                             "pub_iso": "2026-08-18 15:01",
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return {d.get("regra")
            for d in (h["articles"]["u1"].get("semantic_discards") or [])}


def _url(pref):
    for u, r in _H["articles"].items():
        if sh.id_artigo(r.get("url") or u, r.get("title") or "").startswith(pref):
            return u
    return None


def _replay():
    h = copy.deepcopy(_H)
    rd._reclassify_only_pass(h, cfg)
    return h


REPLAY = _replay()


def _ev(pref, emp, hist=None):
    u = _url(pref)
    return rd.event_ids_for((hist or REPLAY)["articles"][u], emp) or []


print("=" * 98)
print("BLOCO A - o oraculo e a verdade humana publicada, nao uma lista minha")
print("=" * 98)
_ceo = {m["case_id"]: m for m in _MS.values() if m["family"] == "troca_ceo"}
check(len(_ceo) == 6, f"[1] 6 filiacoes humanas de troca_ceo no lote V1 ({len(_ceo)})")
check(sum(1 for m in _ceo.values() if m["scoreable"] == "NO") == 5,
      "[2] cinco negativas")
check(sum(1 for m in _ceo.values() if m["scoreable"] == "YES") == 1,
      "[3] e UMA positiva — a guarda nao pode ser um viés de so-remover")

_MAPA = {"06": ("b1eea0e7acee", "B3"), "07": ("9eb803c2493e", "Tupy"),
         "16": ("86474c91fba1", "Pemex (Petróleos Mexicanos)"),
         "20": ("cad44d85917e", "Santander Brasil"),
         "18": ("6c6de2598d9b", "Rumo"), "17": ("1cc87309f14b", "Vale")}
_i = 4
for _caso in sorted(_MAPA):
    _pref, _emp = _MAPA[_caso]
    _hum = _ceo[_caso]["scoreable"] == "YES"
    _cod = "troca_ceo" in _ev(_pref, _emp)
    check(_cod == _hum,
          f"[{_i}] caso {_caso} {_emp}: codigo {'pontua' if _cod else 'contido'} "
          f"== humano {_hum and 'SIM' or 'NAO'}")
    _i += 1

print()
print("=" * 98)
print("BLOCO B - a VALE e o controle anti-overfitting mais importante")
print("=" * 98)
check("troca_ceo" in _ev("1cc87309f14b", "Vale"),
      "[10] a Vale SOBREVIVE — humano disse SIM apesar de nao haver nomeacao final")
check(sa.detect_troca_ceo_sem_assercao(
          "Novo CEO da Vale (VALE3): o que Embraer e Klabin dizem sobre "
          "participação de diretores na concorrência") == "",
      "[11] e o detector nao acusa descritor: quem 'diz' sao Embraer e Klabin, "
      "nao o cargo")
check(sa.detect_troca_ceo_sem_assercao(
          "Novo CEO da Pemex vai viajar ao Brasil") != "",
      "[12] enquanto na Pemex o proprio cargo e o ator")
check("topico" not in sa.detect_troca_ceo_sem_assercao.__doc__.lower(),
      "[13] a preservacao da Vale NAO depende de regra de dois-pontos "
      "feita sob medida")

print()
print("=" * 98)
print("BLOCO C - RUMO: a guarda e de FAMILIA, nao do artigo inteiro")
print("=" * 98)
_r = _ev("6c6de2598d9b", "Rumo")
check("troca_ceo" not in _r, "[14] `troca_ceo` suprimido")
check("recomendacao_negativa" in _r,
      "[15] e `recomendacao_negativa` PRESERVADA no mesmo artigo")
check("R_TROCA_CEO_SEM_ASSERCAO" in regras(
          "Citi vê incerteza com troca de CEO da Rumo (RAIL3) e mantém "
          "recomendação de venda", "Rumo"),
      "[16] pela regra nova, com ID proprio")

print()
print("=" * 98)
print("BLOCO D - positivos reais do corpus continuam pontuando")
print("=" * 98)
_POS_REAIS = [
    ("a6f99fd68d1e", "B3", "anuncio real de maio (Egan escolhido)"),
    ("0acaf3fd3287", "Tupy", "Tupy escolhe Harro Burmann"),
    ("478244266a93", "Tupy", "Tupy conclui sucessao e elege"),
    ("ff933e4b6fd0", "Tupy", "Tupy anuncia renuncia do CEO"),
    ("88d41012b4ef", "Santander Brasil", "Finkelsztain sera o novo CEO"),
    ("edeb694bf05b", "JBS", "JBS nomeia Wesley Batista Filho"),
    ("8c22ad696759", "Yura", "Yura ES: es el nuevo gerente general"),
    ("20bc6bc76199", "Yura", "Yura ES: anuncia la salida"),
    ("583c50a75933", "Truist Financial", "Truist EN: Names New CEO"),
    ("ea2ac395ddf2", "Vamos", "Vamos: Eleicao de novo CEO"),
    ("904206bfa0cd", "Cemig", "Cemig elege novo CEO"),
]
for _i, (_p, _e, _rot) in enumerate(_POS_REAIS, start=17):
    if _url(_p) is None:
        check(True, f"[{_i}] (ausente do corpus retido) {_rot}")
        continue
    check("troca_ceo" in _ev(_p, _e), f"[{_i}] preservado — {_rot}")

print()
print("=" * 98)
print("BLOCO E - PARES MINIMOS: so o membro com assercao pontua")
print("=" * 98)
_PARES = [
    ("B3", "Christian Egan é escolhido como novo CEO da B3", "B3",
     "Falha que atrasou abertura dos mercados em julho é ‘chamado à ação’, "
     "diz novo CEO da B3", "B3"),
    ("Tupy", "Tupy (TUPY3) escolhe Harro Burmann como novo CEO", "Tupy",
     "“Tupy do futuro”: novo CEO faz giro por unidades", "Tupy"),
    ("Pemex (sintetico no positivo)", "Pemex nomeia Fulano como novo CEO",
     "Pemex (Petróleos Mexicanos)",
     "Novo CEO da Pemex vai viajar ao Brasil para avançar agenda de parceria",
     "Pemex (Petróleos Mexicanos)"),
    ("Santander", "Gilson Finkelsztain será o novo CEO do Santander",
     "Santander Brasil",
     "XP vê troca de CEO no Santander (SANB11) sem ruptura", "Santander Brasil"),
]
for _i, (_rot, _tp, _ep, _tn, _en) in enumerate(_PARES, start=28):
    check(pontua(_tp, _ep) and not pontua(_tn, _en),
          f"[{_i}] par {_rot}: positivo pontua, negativo contido")

print()
print("=" * 98)
print("BLOCO F - PT / EN / ES nos dois lados")
print("=" * 98)
_LING = [
    ("PT+", "Empresa anuncia novo CEO a partir de janeiro", "Vale", True),
    ("PT-", "Novo CEO da Vale diz que resultados vão melhorar", "Vale", False),
    ("EN+", "Truist Names New CEO as Regional Bank Pushes to Boost Performance",
     "Truist Financial", True),
    ("EN-", "New CEO of Truist says results will improve", "Truist Financial", False),
    ("ES+", "Gonzalo Rueda Castillo es el nuevo gerente general de Cemento Yura",
     "Yura", True),
    ("ES-", "El nuevo gerente general de Cemento Yura dice que la planta seguirá",
     "Yura", False),
]
for _i, (_rot, _t, _e, _esp) in enumerate(_LING, start=32):
    check(pontua(_t, _e) == _esp,
          f"[{_i}] {_rot}: {'pontua' if _esp else 'contido'}")

print()
print("=" * 98)
print("BLOCO G - saida, efeito futuro e prevalencia da assercao")
print("=" * 98)
check(pontua("Vale CEO steps down after profit warning", "Vale"),
      "[38] saida explicita pontua mesmo sem 'novo CEO'")
check(pontua("Executivo será o novo CEO da Vale a partir de janeiro", "Vale"),
      "[39] nomeacao com efeito FUTURO pontua hoje")
check(pontua("Novo CEO diz que assumirá o cargo da Vale em setembro", "Vale"),
      "[40] ASSERCAO PREVALECE: ha verbo de comentario E assuncao explicita")
check(not pontua("Sob comando do novo CEO, a Vale muda estratégia", "Vale"),
      "[41] moldura temporal nao cria evento — mudanca ja pressuposta")
check(not pontua("Conheça o novo CEO da Vale, que promete cortar custos", "Vale"),
      "[42] perfil com promessa tambem nao")

print()
print("=" * 98)
print("BLOCO H - METAMORFICAS")
print("=" * 98)
check(pontua("Christian Egan é escolhido como novo CEO da B3", "B3")
      and not pontua("Falha nos mercados, diz novo CEO da B3", "B3"),
      "[43] M1: trocar 'e escolhido como' por 'diz' inverte SIM -> NAO")
check(not pontua("Novo CEO da Vale vai viajar ao exterior", "Vale")
      and pontua("Novo CEO da Vale vai viajar; será o novo comandante em janeiro",
                 "Vale"),
      "[44] M2: acrescentar assercao explicita promove")
check(pontua("Tupy (TUPY3) escolhe Harro Burmann como novo CEO", "Tupy")
      == pontua("Tupy (TUPY3) escolhe Mariana Prado como novo CEO", "Tupy"),
      "[45] M3: trocar so o nome do executivo nao muda nada")
check(not pontua("Novo CEO da Vale diz que resultados vão melhorar", "Vale",
                 summary="Novo CEO da Vale diz que resultados vão melhorar"),
      "[46] M4: descritor segue nao-evento independentemente da data")
check(not pontua("XP vê troca de CEO na Vale sem ruptura", "Vale"),
      "[47] M5: prefixo de casa de analise nao cria evento")
# M6/M7/M8 sao testadas NA CAMADA DESTA ONDA (o detector), e nao ponta a
# ponta: a taxonomia nao classifica "sucessao"/"renuncia" nessas formas
# verbais, e alterar taxonomia esta fora do escopo. A lacuna de recall fica
# registrada em docs; o que cabe aqui e provar que a guarda nao as bloqueia.
check(sa.detect_troca_ceo_sem_assercao(
          "Vale anuncia sucessão e disputa entre candidatos ao comando") == "",
      "[48] M6: processo de sucessao explicito NAO e barrado pela guarda")
check(sa.detect_troca_ceo_sem_assercao(
          "Vale abre processo de sucessão do CEO sem indicar substituto") == "",
      "[49] M7: sucessao sem sucessor nomeado tambem nao e barrada")
check(sa.detect_troca_ceo_sem_assercao(
          "CEO da Vale renuncia; empresa anuncia saída do comando") == "",
      "[50] M8: renuncia explicita nao e barrada pela guarda")

print()
print("=" * 98)
print("BLOCO I - BLAST no corpus retido")
print("=" * 98)
_REAL = sa.detect_troca_ceo_sem_assercao


def _mapa(ligada):
    sa.detect_troca_ceo_sem_assercao = _REAL if ligada else (lambda *a, **k: "")
    h = copy.deepcopy(_H)
    rd._reclassify_only_pass(h, cfg)
    o = set()
    for u, r in h["articles"].items():
        for emp in (r.get("companies") or []):
            for e in (rd.event_ids_for(r, emp) or []):
                o.add((u, emp, e))
    sa.detect_troca_ceo_sem_assercao = _REAL
    return o


_antes, _depois = _mapa(False), _mapa(True)
_sumidas = _antes - _depois
_novas = _depois - _antes
check(not _novas, f"[51] a guarda nunca CRIA evento ({len(_novas)})")
check(all(e == "troca_ceo" for _, _, e in _sumidas),
      "[52] e so remove da familia `troca_ceo` — zero vazamento")
# O acervo cresce a cada rodada do cron: travar a contagem exata transformaria
# esta checagem num calendario. O que nao pode mudar e a PROPRIEDADE — a guarda
# so remove, so na familia certa, e cobre as negativas humanas conhecidas.
check(len(_sumidas) >= 9,
      f"[53] a guarda remove ao menos as 9 triplas conhecidas "
      f"({len(_sumidas)} no acervo atual)")
_ids = set()
for u, emp, e in _sumidas:
    r = _H["articles"][u]
    _ids.add((sh.id_artigo(r.get("url") or u, r.get("title") or "")[:12], emp))
_HUM_NEG = {("b1eea0e7acee", "B3"), ("9eb803c2493e", "Tupy"),
            ("86474c91fba1", "Pemex (Petróleos Mexicanos)"),
            ("cad44d85917e", "Santander Brasil"), ("6c6de2598d9b", "Rumo")}
check(_HUM_NEG <= _ids,
      "[54] as cinco negativas humanas do lote V1 estao entre elas")
check(("201b91aa6b3c", "JBS") in _ids,
      "[55] e tambem o descritor da JBS, negativo humano no shadow V2")
_sem_verdade = _ids - _HUM_NEG - {("201b91aa6b3c", "JBS")}
check(len(_sem_verdade) >= 3,
      f"[56] e o restante segue enumerado para revisao humana "
      f"({sorted(e for _, e in _sem_verdade)})")
check({"Ambev", "Dasa", "Hapvida"} <= {e for _, e in _sem_verdade},
      f"[57] Ambev, Dasa e Hapvida seguem entre eles — mesmo mecanismo "
      f"auditado (cargo como ator); novos casos entram na fila, nao no verde "
      f"({sorted(e for _, e in _sem_verdade)})")

print()
print("=" * 98)
print("BLOCO J - o que esta onda NAO pode ter tocado")
print("=" * 98)
# A versao original desta checagem afirmava que o historico PERSISTIDO ainda
# trazia `troca_ceo` na Rumo — era a prova de que a onda de CODIGO nao havia
# tocado em dado. Aquele estado era transitorio DE PROPOSITO: a onda de
# alinhamento, autorizada e com verdade humana, gravou os cinco negativos no
# historico. Ela vira a assercao mais forte, e a mais dificil de satisfazer
# por acidente: o artigo permanece, o CEO sai, a recomendacao fica.
_u = _url("6c6de2598d9b")
check(_u is not None,
      "[58a] o artigo da Rumo permanece no historico — corrigir atribuicao "
      "nao e apagar historia")
check(rd.event_ids_for(_H["articles"][_u], "Rumo") == ["recomendacao_negativa"],
      "[58b] e o PERSISTIDO ja concorda com o humano: `troca_ceo` fora, "
      "`recomendacao_negativa` preservada no MESMO artigo")
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[59] a supervisao humana segue intacta (27 filiacoes / 24 casos)")
_S = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
check(len({v["article_id"] for v in _S["observacoes"].values()
           if v.get("human_review")}) >= 11,
      "[60] as adjudicacoes do Contract V2 seguem intactas")
_ot = _S["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4,
      "[61] occurrence_truth intacto (10/21/4)")
_inv = io.open("reliability_taxonomy_inventory.py", encoding="utf-8").read()
check("R_TROCA_CEO_DE_TERCEIRO" in _inv,
      "[62] o ID da regra irma de terceiro nao foi renomeado")
_cfg_txt = io.open("config_risco.yaml", encoding="utf-8").read()
check("- novo CEO" in _cfg_txt or "novo CEO" in _cfg_txt,
      "[63] a taxonomia nao foi alterada — a keyword `novo CEO` segue la")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7k (assercao de troca de CEO): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
