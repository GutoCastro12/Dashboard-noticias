#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_recompra_acoes_proprias.py — comprar as próprias ações não é comprar
ninguém.

O QUE ACONTECEU

Seis Fatos Relevantes da CVM intitulados "Aquisição de Ações de Emissão da
Própria Companhia" — Porto, Embraer, Gerdau, Ultrapar, Eneva e Vale — pontuavam
como M&A. Na Vale a recompra respondia por 53% do score e definia o pior evento
do emissor no painel executivo.

O DIAGNÓSTICO QUE MUDOU O ENUNCIADO

Não faltava política. `ma_is_legitimate` já devolvia
`recompra_de_acoes_proprias_nao_e_ma`, `R_MA_OBJETO_ESCOPO` já mandava esses
casos para `recompra_acoes`, e duas ocorrências do MESMO formulário já eram
barradas. Elas eram barradas por acidente: traziam "Cancelamento de Ações" no
resumo e casavam por outro padrão da lista.

Faltava vocabulário. Nenhum dos dez padrões de `acoes_proprias` reconhecia a
construção do formulário da CVM.

O QUE ESTE ARQUIVO PROTEGE, NOS DOIS SENTIDOS

O que separa recompra de aquisição real é a AUTORREFERÊNCIA. "Ações de emissão
da Companhia Beta" é M&A legítimo; "ações de emissão da própria companhia" não
é. Uma regra que casasse "ações de emissão" sem o possessivo apagaria aquisição
de participação verdadeira — e falso negativo não aparece no painel. Por isso os
near-negatives de terceiro pesam tanto quanto os seis casos positivos.
"""
from __future__ import annotations

import inspect
import io
import json
import re

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
BOILERPLATE = "Aquisição de Ações de Emissão da Própria Companhia"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def objeto(texto: str) -> str:
    return sa.detect_transaction(texto)["transaction_object"]


def legitimo(texto: str) -> tuple:
    return sa.ma_is_legitimate(texto)


def pontua(titulo: str, empresa: str, resumo: str = "") -> set:
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "s",
                             "domain": "cvm.gov.br", "pub_ts": 1785000000,
                             "pub_iso": "2026-07-20 10:00",
                             "companies": [empresa]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set((h["articles"]["u1"].get("events_by_company") or {}).get(empresa)
               or [])


def informativo(titulo: str, empresa: str, resumo: str = "") -> list:
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "s",
                             "domain": "cvm.gov.br", "pub_ts": 1785000000,
                             "pub_iso": "2026-07-20 10:00",
                             "companies": [empresa]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return ((h["articles"]["u1"].get("informational_events_by_company") or {})
            .get(empresa) or [])


print("=" * 98)
print("BLOCO A — OS SEIS CASOS REAIS, UM A UM")
print("=" * 98)
SEIS = ["Porto", "Embraer", "Gerdau", "Ultrapar", "Eneva", "Vale"]
for _n, _emp in enumerate(SEIS, start=1):
    _t = f"[Fato Relevante] {_emp}: {BOILERPLATE}"
    _r = pontua(_t, _emp, BOILERPLATE)
    check("ma" not in _r,
          f"[{_n}] {_emp}: não pontua mais como M&A ({sorted(_r)})")
for _n, _emp in enumerate(SEIS, start=7):
    _inf = informativo(f"[Fato Relevante] {_emp}: {BOILERPLATE}", _emp,
                       BOILERPLATE)
    _ids = {e.get("event_id") for e in _inf}
    check("recompra_acoes" in _ids,
          f"[{_n}] {_emp}: vai para `recompra_acoes` ({sorted(_ids)})")

print()
print("=" * 98)
print("BLOCO B — PROVENIÊNCIA: A FAMÍLIA EXISTENTE, NÃO UMA PARALELA")
print("=" * 98)
_inf = informativo(f"[Fato Relevante] Vale: {BOILERPLATE}", "Vale", BOILERPLATE)
_regras = {e.get("attribution_rule") for e in _inf}
check(_regras == {"R_MA_OBJETO_ESCOPO"},
      f"[13] a rejeição sai por R_MA_OBJETO_ESCOPO, a regra que já existia "
      f"({_regras})")
_obs = {e.get("observation") for e in _inf}
check(_obs == {"recompra_de_acoes_proprias_nao_e_ma"},
      f"[14] com a observação que já estava no código ({_obs})")
check(objeto(BOILERPLATE) == "acoes_proprias",
      f"[15] o objeto é classificado como `acoes_proprias` ({objeto(BOILERPLATE)})")
check(legitimo(BOILERPLATE) == (False, "recompra_de_acoes_proprias_nao_e_ma"),
      "[16] e `ma_is_legitimate` devolve o motivo já existente")
check("recompra_acoes" in {e["id"] for e in cfg.get("taxonomy") or []},
      "[17] `recompra_acoes` é família de produção pré-existente — nenhuma "
      "família, peso ou severidade nova foi criada")

print()
print("=" * 98)
print("BLOCO C — OS DOIS QUE JÁ ESTAVAM CERTOS CONTINUAM CERTOS")
print("=" * 98)
_g = f"{BOILERPLATE}||Cancelamento de Ações de Emissão da Própria Companhia"
_c = f"{BOILERPLATE}||Cancelamento de Ações em Tesouraria"
for _n, (_emp, _res, _rot) in enumerate([("Gerdau", _g, "Gerdau/fev"),
                                         ("Cyrela Brazil Realty", _c, "Cyrela")],
                                        start=18):
    _r = pontua(f"[Fato Relevante] {_emp}: {_res}", _emp, _res)
    _ids = {e.get("event_id") for e in informativo(
        f"[Fato Relevante] {_emp}: {_res}", _emp, _res)}
    check("ma" not in _r and "recompra_acoes" in _ids,
          f"[{_n}] {_rot}: segue fora de M&A e em `recompra_acoes`")
check(objeto("Cancelamento de Ações em Tesouraria") == "acoes_proprias",
      "[20] os padrões antigos seguem funcionando sozinhos — a cobertura foi "
      "ampliada, não substituída")

print()
print("=" * 98)
print("BLOCO D — AUTORREFERÊNCIA É OBRIGATÓRIA (near-negatives de terceiro)")
print("=" * 98)
TERCEIROS = [
    "Aquisição de ações de emissão da Companhia Beta pela Alfa",
    "Alfa adquire ações de emissão da Gamma S.A.",
    "Alfa adquire 30% das ações da Beta",
    "Cade aprova aquisição de 50% da Fibrasil pela Telefônica Brasil",
    "Engie Brasil aprova aquisição de fatia de 40% da Jirau Energia",
    "Cade aprova aquisição pela Suzano de 51% de sociedade de tissue da "
    "Kimberly-Clark",
    "Em recuperação judicial, FMU é recomprada pela Ânima",
    "Alfa anuncia aquisição do controle da Beta",
    "Alfa compra participação societária na Gamma",
]
for _n, _t in enumerate(TERCEIROS, start=21):
    check(objeto(_t) != "acoes_proprias",
          f"[{_n}] NÃO é recompra: {_t[:60]}")

print()
print("=" * 98)
print("BLOCO E — VARIANTES AUTORREFERENTES QUE DEVEM SER RECONHECIDAS")
print("=" * 98)
VARIANTES = [
    "Aquisição de Ações de Emissão da Própria Companhia",
    "Aquisição de ações de emissão da própria empresa",
    "Aquisição de ações de sua própria emissão",
    "Companhia adquire ações de emissão própria",
    "Programa de recompra de ações próprias",
    "Recompra de ações",
    "Aquisição de ações em tesouraria",
    "Company announces repurchase of its own shares",
    "The board approved a buyback of its own stock",
    "Adquisición de acciones propias",
    "Recompra de acciones de su propia emisión",
]
for _n, _t in enumerate(VARIANTES, start=30):
    check(objeto(_t) == "acoes_proprias",
          f"[{_n}] reconhecida: {_t[:60]}")

print()
print("=" * 98)
print("BLOCO F — REGRA GERAL, SEM NOME DE EMPRESA NA PRODUÇÃO")
print("=" * 98)
_padroes = json.dumps(sa.OBJ_NAO_EMPRESA["acoes_proprias"], ensure_ascii=False)
for _n, _nome in enumerate(("Porto", "Embraer", "Gerdau", "Ultrapar", "Eneva",
                            "Vale", "Cyrela"), start=41):
    check(_nome.lower() not in _padroes.lower(),
          f"[{_n}] o vocabulário não menciona '{_nome}'")
_fonte = inspect.getsource(sa.detect_transaction)
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _fonte.splitlines()))
check(not any(n.lower() in _cod.lower() for n in SEIS + ["Cyrela"]),
      "[48] nem o classificador de objeto")
check("Fato Relevante" not in _padroes,
      "[49] e não depende do rótulo do formulário — a regra é sobre o objeto, "
      "não sobre a fonte")

print()
print("=" * 98)
print("BLOCO G — BLAST RADIUS SOBRE O CORPUS REAL")
print("=" * 98)
# ATUALIZADO 2026-08-15. A versão original contava quantos artigos que AINDA
# pontuam `ma` são recompras. Isso media o mundo de antes da reclassificação:
# depois que ela foi aplicada a resposta virou 0 e o bloco passaria a "passar"
# por vacuidade — o mesmo erro que já havia sido corrigido na wave do Duke e
# que eu repeti aqui por rodar a bateria antes do apply, não depois.
#
# Agora mede os DOIS lados: nenhuma recompra pode estar pontuável, e as oito
# têm de estar presentes na família informativa correta.
_h = json.load(io.open("risk_history.json", encoding="utf-8"))
_pontuaveis, _recompras, _preservados = [], [], 0
for _u, _a in _h["articles"].items():
    _txt = (_a.get("title") or "") + " " + (_a.get("summary") or "")
    _e_recompra = objeto(_txt) == "acoes_proprias"
    for _emp, _evs in (_a.get("events_by_company") or {}).items():
        if "ma" in (_evs or []):
            if _e_recompra:
                _pontuaveis.append((_emp, _u))
            else:
                _preservados += 1
    if _e_recompra:
        for _emp, _evs in ((_a.get("informational_events_by_company") or {})
                           .items()):
            if any(e.get("event_id") == "recompra_acoes" for e in (_evs or [])):
                _recompras.append((_emp, _u))
check(not _pontuaveis,
      f"[50] nenhuma recompra segue pontuável como M&A ({_pontuaveis})")
check(len(_recompras) == 8,
      f"[51] há 8 registros em `recompra_acoes`: os do formulário da CVM "
      f"({len(_recompras)})")
check({e for e, _ in _recompras} == set(SEIS) | {"Cyrela Brazil Realty"},
      f"[51b] cobrem as seis adjudicadas e os dois controles "
      f"({sorted({e for e, _ in _recompras})})")
# ATUALIZADO 2026-08-15. Esta checagem registrava um nono item — a BRF, cuja
# matéria sobre a fusão com a Marfrig era suprimida como recompra porque
# "cancelamento de ações" entrava solto no vocabulário. Aquele achado FOI
# CORRIGIDO numa wave posterior, estreitando o token para construções
# autorreferentes.
#
# Uma asserção que continuasse exigindo a presença do defeito passaria a
# proteger o bug em vez do comportamento. Ela vira o seu oposto: a BRF NÃO pode
# mais estar aqui. `test_wave_cancelamento_acoes_fusao.py` cobre o caso por
# inteiro.
check(not [u for e, u in _recompras if e == "BRF"],
      "[51c] a BRF saiu da população de recompra — o falso negativo de M&A "
      "foi corrigido, não silenciado")
check(_preservados >= 120,
      f"[52] os demais artigos que pontuam M&A seguem intactos ({_preservados})")

print()
print("=" * 98)
print("BLOCO H — A VERDADE HUMANA ESTÁ REGISTRADA E NÃO É AUTORIDADE DE SCORE")
print("=" * 98)
_rev = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                         encoding="utf-8"))
_buy = {k: v for k, v in _rev.items()
        if v.get("family_id") == "MA_OBJECT_SCOPE_OWN_SHARE_BUYBACK"}
check(len(_buy) == 6, f"[53] seis adjudicações registradas ({len(_buy)})")
check({v["company"] for v in _buy.values()} == set(SEIS),
      "[54] uma para cada empresa confirmada")
check(all(v["status"] == "FALSE_POSITIVE" and v["reviewer_type"] == "human"
          for v in _buy.values()),
      "[55] no enum que o store já usa — nenhum valor de schema inventado")
for _n, _mod in ((56, "risk_dashboard.py"), (57, "semantic_audit.py")):
    _src = io.open(_mod, encoding="utf-8").read()
    check("live_reviews" not in _src,
          f"[{_n}] {_mod} não lê verdade humana — a autoridade de produção é a "
          f"regra, não a adjudicação")

print()
print("=" * 98)
print("BLOCO I — O QUE NÃO PODE TER SIDO TOCADO")
print("=" * 98)
_vale_gip = ("Mattos Filho e Demarest atuam em aquisição da Vale pela GIP na "
             "Aliança Energia")
check("ma" in pontua(_vale_gip, "Vale"),
      "[58] o caso Vale/GIP (backlog de papel vendedor) segue como estava")
_eneva_cade = "Cade aprova aquisição, pela Eneva, da parcela da Atem em Japiim"
check("ma" in pontua(_eneva_cade, "Eneva"),
      "[59] o caso Eneva/Japiim (backlog de escopo) segue como estava")
_samarco = "Vale informa sobre Plano de Recuperação Judicial da Samarco"
check("recuperacao_judicial" in pontua(_samarco, "Samarco Mineração"),
      "[60] a classificação semântica da Samarco não mudou — a correção dela "
      "foi de DATA, e continua sendo")

print()
print("=" * 98)
print(f"RESULTADO RECOMPRA DE AÇÕES PRÓPRIAS: {PASS}/{PASS + FAIL} checagens "
      f"passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
