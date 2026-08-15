#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_cancelamento_acoes_fusao.py — cancelar ações não é comprar as próprias.

O CASO

"BRF aprova cancelamento de ações e pede mudança de registro na CVM para
concretizar fusão com Marfrig na Bolsa" era suprimido como `recompra_acoes`.
É M&A real — a fusão BRF/Marfrig —, e o cancelamento ali é etapa de
implementação da combinação. Falso negativo: M&A verdadeiro sumindo do painel,
que é a classe de erro que menos se percebe, porque nada aparece.

A CAUSA

`OBJ_NAO_EMPRESA["acoes_proprias"]` tinha `cancelamento de ações` solto — o
único padrão da família que NÃO exigia autorreferência. Ações também são
canceladas em fusão, incorporação e redução de capital.

Medido antes de trocar: no corpus inteiro esse padrão era evidência ÚNICA em um
só artigo, justamente o falso negativo. Nas recompras verdadeiras havia sempre
outra evidência autorreferente no mesmo texto — ele era redundante. Estreitá-lo
removeu só o dano.

O QUE ESTE ARQUIVO PROTEGE, NOS DOIS SENTIDOS

Que cancelamento de ações PRÓPRIAS/em tesouraria continue sendo recompra, e que
cancelamento como etapa de fusão não seja mais confundido com ela. E que a
correção não tenha criado o erro oposto: o artigo entra na ocorrência de fusão
que já existia, sem nova ocorrência e sem novo score.
"""
from __future__ import annotations

import io
import json
import re
import time
import unicodedata

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
BRF_T = ("BRF aprova cancelamento de ações e pede mudança de registro na CVM "
         "para concretizar fusão com Marfrig na Bolsa")
BRF_URL = ("https://www.estadao.com.br/einvestidor/cenarios-e-mercado/"
           "brf-cancelamento-acoes-registro-cvm-fusao-marfrig/")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def objeto(t: str) -> str:
    return sa.detect_transaction(t)["transaction_object"]


def _n(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def clusters(historico: dict, empresa: str) -> tuple:
    """Reproduz o agrupamento de ocorrência do caminho de produção."""
    occ = []
    for _u, a in historico["articles"].items():
        if empresa not in (a.get("companies") or []):
            continue
        if "ma" not in ((a.get("events_by_company") or {}).get(empresa) or []):
            continue
        t = a.get("title") or ""
        occ.append({"event_id": "ma", "pub_ts": a.get("pub_ts"), "title": t,
                    "_ident": {"emissor": empresa, "event_id": "ma",
                               "objeto": "", "serie": rd._serie_da_operacao(t),
                               "marcadores": rd._marcadores_operacao(
                                   t, empresa, AL.get(empresa)),
                               "fase": rd._fase_do_evento(t)}})
    rd.assign_occurrence_clusters(occ, 45, None, AL)
    return occ, {o["_occ_key"] for o in occ}


print("=" * 98)
print("BLOCO A — O CASO REAL")
print("=" * 98)
check(objeto(BRF_T) != "acoes_proprias",
      f"[1] o artigo da BRF não é mais objeto de ações próprias ({objeto(BRF_T)})")
check(sa.ma_is_legitimate(BRF_T)[0],
      "[2] e volta a ser reconhecido como transação empresarial legítima")
_H = json.load(io.open("risk_history.json", encoding="utf-8"))
_a = _H["articles"][BRF_URL]
# O REGISTRO ARMAZENADO NÃO FOI CORRIGIDO, e isso é deliberado.
#
# Os dois caminhos oficiais de reclassificação recusam ACRESCENTAR evento a um
# registro: `--reclassify-only --apply` aborta no portão `G5 added == 0`, e
# `--reclassify-semantic-only` só remove score (devolveu 0 mudanças). Como a
# correção precisa devolver `ma` ao registro, ela é estruturalmente impossível
# por esses caminhos, e cirurgia manual em JSON não é opção.
#
# A regra — que é a autoridade de produção — está corrigida: qualquer artigo
# desta classe que chegue daqui em diante é classificado certo. O registro
# antigo fica pendente, e este teste registra isso em vez de fingir que foi
# resolvido.
_inf_brf = [e.get("event_id") for e in
            ((_a.get("informational_events_by_company") or {}).get("BRF") or [])]
check("recompra_acoes" in _inf_brf,
      "[3] o registro ARMAZENADO segue em `recompra_acoes` — pendência "
      "conhecida, bloqueada pelo portão G5 do mecanismo oficial")
check(not ((_a.get("events_by_company") or {}).get("BRF") or []),
      "[4] e por isso ainda não pontua M&A no histórico")

print()
print("=" * 98)
print("BLOCO B — A OCORRÊNCIA ECONÔMICA NÃO AUMENTOU")
print("=" * 98)
# Este é o ponto que fez a wave anterior bloquear o apply. A identidade de
# transação já existia: os artigos da fusão compartilham o marcador `marfrig` e
# são unidos ACIMA do gap de 45 dias. O artigo entra nessa ocorrência, não cria
# outra — e por ser de 2025 não vira âncora.
_occ, _ks = clusters(_H, "BRF")
check(len(_ks) == 1,
      f"[5] os artigos de M&A da BRF formam UMA ocorrência ({len(_ks)})")
# Simula a entrada do artigo corrigido na ocorrência, que é o que aconteceria
# se o registro pudesse ser atualizado. Mede a consequência real, sem depender
# de o histórico ter sido alterado.
_sim = list(_occ) + [{"event_id": "ma", "pub_ts": _a["pub_ts"], "title": BRF_T,
                      "_ident": {"emissor": "BRF", "event_id": "ma",
                                 "objeto": "",
                                 "serie": rd._serie_da_operacao(BRF_T),
                                 "marcadores": rd._marcadores_operacao(
                                     BRF_T, "BRF", AL.get("BRF")),
                                 "fase": rd._fase_do_evento(BRF_T)}}]
rd.assign_occurrence_clusters(_sim, 45, None, AL)
check(len({o["_occ_key"] for o in _sim}) == 1,
      f"[6] com o artigo dentro, continua sendo UMA ocorrência "
      f"({len({o['_occ_key'] for o in _sim})}) — a identidade de transação já "
      f"existia e resolve o caso")
_marc = rd._marcadores_operacao(BRF_T, "BRF", AL.get("BRF"))
check("marfrig" in (_marc or ""),
      f"[7] o artigo carrega o marcador da operação ({_marc!r}), que é o que "
      f"une o cluster acima do gap de 45 dias")
_anc = max(_sim, key=lambda o: o["pub_ts"])
check(_anc["title"] != BRF_T,
      f"[8] e com a data real NÃO seria a âncora "
      f"({time.strftime('%Y-%m-%d', time.gmtime(_anc['pub_ts']))}) — por isso "
      f"não criaria score novo")
_d = [x for x in rd.build_evolution(_H, cfg, 365) if x["company"] == "BRF"][0]
check(len([b for b in _d["breakdown"] if b["label"] == "M&A"]) == 1,
      "[9] o dashboard mostra uma única ocorrência de M&A para a BRF")

print()
print("=" * 98)
print("BLOCO C — RECOMPRA VERDADEIRA SEGUE SENDO RECOMPRA")
print("=" * 98)
GERDAU = ("[Fato Relevante] Gerdau: Aquisição de Ações de Emissão da Própria "
          "Companhia||Cancelamento de Ações de Emissão da Própria Companhia")
CYRELA = ("[Fato Relevante] Cyrela Brazil Realty: Aquisição de Ações de Emissão "
          "da Própria Companhia||Cancelamento de Ações em Tesouraria")
for _n_, (_rot, _t) in enumerate((("Gerdau/fev", GERDAU), ("Cyrela", CYRELA)),
                                 start=11):
    check(objeto(_t) == "acoes_proprias",
          f"[{_n_}] {_rot} continua como recompra")
_pat = [p for p in sa.OBJ_NAO_EMPRESA["acoes_proprias"] if re.search(p, _n(GERDAU))]
check(len(_pat) >= 1,
      f"[13] e por evidência autorreferente própria, não pelo token removido "
      f"({len(_pat)} padrão(ões))")
check(objeto("Cancelamento de ações em tesouraria") == "acoes_proprias"
      and objeto("Cancelamento de ações próprias") == "acoes_proprias",
      "[14] cancelamento explicitamente de ações próprias/tesouraria segue "
      "reconhecido")
check(objeto("Cancelamento das ações de sua própria emissão") == "acoes_proprias"
      and objeto("Cancellation of its own shares") == "acoes_proprias",
      "[15] inclusive nas variantes autorreferentes e em inglês")

print()
print("=" * 98)
print("BLOCO D — CANCELAMENTO COMO ETAPA DE FUSÃO NÃO É RECOMPRA")
print("=" * 98)
for _n_, _t in enumerate((
        "Empresa aprova cancelamento de ações para concretizar fusão",
        "Cancelamento de ações no âmbito da incorporação",
        "Share cancellation as part of the merger",
        "Assembleia aprova cancelamento de ações da combinação de negócios"),
        start=16):
    check(objeto(_t) != "acoes_proprias",
          f"[{_n_}] não é recompra: {_t[:56]}")

print()
print("=" * 98)
print("BLOCO E — AS SEIS RECOMPRAS HUMANAS, INTACTAS")
print("=" * 98)
SEIS = ["Porto", "Embraer", "Gerdau", "Ultrapar", "Eneva", "Vale"]
_bp = "Aquisição de Ações de Emissão da Própria Companhia"
for _n_, _emp in enumerate(SEIS, start=20):
    check(objeto(f"[Fato Relevante] {_emp}: {_bp}") == "acoes_proprias",
          f"[{_n_}] {_emp}: segue como recompra")
_ainda = []
for _u, _a in _H["articles"].items():
    if "Ações de Emissão" not in (_a.get("title") or ""):
        continue
    for _emp, _evs in (_a.get("events_by_company") or {}).items():
        if "ma" in (_evs or []):
            _ainda.append(_emp)
check(not _ainda,
      f"[26] e nenhuma delas voltou a pontuar M&A no histórico ({_ainda})")

print()
print("=" * 98)
print("BLOCO F — SEM NOME DE EMPRESA NA PRODUÇÃO")
print("=" * 98)
_vocab = json.dumps(sa.OBJ_NAO_EMPRESA["acoes_proprias"], ensure_ascii=False)
for _n_, _nome in enumerate(("BRF", "Marfrig", "Gerdau", "Cyrela", "Porto",
                             "Embraer", "Ultrapar", "Eneva", "Vale"), start=27):
    check(_nome.lower() not in _vocab.lower(),
          f"[{_n_}] o vocabulário não menciona '{_nome}'")
check("fusao" not in _n(_vocab) and "merger" not in _vocab.lower(),
      "[36] e não depende de detectar fusão — a regra é sobre a natureza das "
      "ações canceladas, sem override de precedência")

print()
print("=" * 98)
print("BLOCO G — CONTROLES DE AGRUPAMENTO QUE NÃO PODEM REGREDIR")
print("=" * 98)
_eo, _eks = clusters(_H, "Eneva")
check(len(_eks) == 1 and len(_eo) >= 2,
      f"[37] Eneva: CADE e ANP seguem na MESMA ocorrência ({len(_eo)} artigos, "
      f"{len(_eks)} ocorrência)")
_gd = [x for x in rd.build_evolution(_H, cfg, 365) if x["company"] == "Engie Brasil"][0]
_ema = [b for b in _gd["breakdown"] if b["label"] == "M&A"]
check(_ema and _ema[0].get("sources", 0) >= 4,
      f"[38] Engie/Jirau preserva a consolidação multi-fonte "
      f"({_ema[0].get('sources') if _ema else 0} fontes)")
_sep = []
for _ts, _t in ((1750000000, "Acme anuncia aquisicao da Betamax"),
                (1750000000 + 200 * 86400, "Acme anuncia aquisicao da Gamatech")):
    _sep.append({"event_id": "ma", "pub_ts": _ts, "title": _t,
                 "_ident": {"emissor": "Acme", "event_id": "ma", "objeto": "",
                            "serie": "", "marcadores": rd._marcadores_operacao(
                                _t, "Acme", ["Acme"]), "fase": ""}})
rd.assign_occurrence_clusters(_sep, 45, None, AL)
check(len({o["_occ_key"] for o in _sep}) == 2,
      "[39] negócios com contrapartes diferentes seguem separados")

print()
print("=" * 98)
print("BLOCO H — NADA MAIS MUDOU NO CORPUS")
print("=" * 98)
_ma_total = _recompra = 0
for _u, _a in _H["articles"].items():
    _t = (_a.get("title") or "") + " " + (_a.get("summary") or "")
    for _emp, _evs in (_a.get("events_by_company") or {}).items():
        if "ma" in (_evs or []):
            _ma_total += 1
    for _emp, _evs in ((_a.get("informational_events_by_company") or {}).items()):
        if any(e.get("event_id") == "recompra_acoes" for e in (_evs or [])):
            _recompra += 1
check(_ma_total == 127,
      f"[40] o corpus de M&A segue íntegro — 127 registros pontuáveis, o mesmo "
      f"de antes da mudança ({_ma_total})")
check(_recompra == 9,
      f"[41] a população ARMAZENADA de recompra segue em 9 — a do registro pendente "
      f"({_recompra})")
_supr = [(_a.get("title") or "")[:50] for _a in _H["articles"].values()
         if re.search(r"fus[ãa]o|incorpora|merger", _n(_a.get("title") or ""))
         and any(e.get("event_id") == "recompra_acoes"
                 for _evs in (_a.get("informational_events_by_company")
                              or {}).values() for e in (_evs or []))]
check(len(_supr) == 1,
      f"[42] resta UMA matéria de fusão suprimida como recompra no histórico — "
      f"a pendência da BRF, que a regra já resolve mas o mecanismo não pode "
      f"gravar ({_supr})")

print()
print("=" * 98)
print("BLOCO I — VERDADE HUMANA PRESERVADA, SEM `ma=TRUE` ENGANOSO")
print("=" * 98)
_rev = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                         encoding="utf-8"))
_brf = _rev.get(f"{BRF_URL}||BRF||ma")
check(_brf is not None, "[43] a adjudicação da BRF segue registrada")
check(_brf["family_id"] == "MA_POST_MERGER_PROCESS_STEP"
      and _brf["status"] == "FALSE_POSITIVE",
      f"[44] como etapa pós-fusão, não como `ma=TRUE` ({_brf['family_id']})")
check("nao deve gerar novo score" in _brf["note"],
      "[45] e a nota preserva que o artigo não deve gerar score novo")
# A verdade humana diz 'não deve gerar score' e a REGRA diz 'a família certa é
# M&A'. As duas convivem porque quem impede o score não é a adjudicação — é a
# camada de ocorrência, que absorveria o artigo no cluster já existente. Esta
# checagem existe para que ninguém futuramente "resolva" a divergência
# transformando a verdade humana em autoridade de score.
check("live_reviews" not in io.open("risk_dashboard.py", encoding="utf-8").read()
      and "live_reviews" not in io.open("semantic_audit.py", encoding="utf-8").read(),
      "[46] e a verdade humana continua sem autoridade sobre score — nenhum "
      "módulo de produção a lê")

print()
print("=" * 98)
print(f"RESULTADO CANCELAMENTO / FUSÃO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
