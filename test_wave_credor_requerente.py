#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_credor_requerente.py — quem PEDE a falência não é quem vai à falência.

O CASO QUE ORIGINOU

"Santander surpreende e entra na Justiça para solicitar falência"
(Diário do Comércio, 2026-08-12). Adjudicado por humano como FALSE_POSITIVE,
dimensão ATTRIBUTION, família CREDITOR_VS_DEBTOR: o Santander é o credor
requerente; a falência é da Minera Cobre Verde, que tentou reorganização
judicial e acabou declarada falida.

POR QUE A FAMÍLIA EXISTENTE NÃO PEGOU

A regra credor≠devedor cobria duas construções:
  • devedor nomeado por possessivo — "falência DA Oi";
  • monitorada como FINANCIADORA — "junto ao banco", "financiado pelo X".

Nenhuma alcança este caso: o devedor NÃO é nomeado na manchete, e o banco não
aparece financiando — aparece REQUERENDO. Sem possessivo, o extrator devolvia
vazio e o evento seguia atribuído ao próprio requerente.

O QUE ESTE TESTE PROTEGE

Dois níveis, e ambos são necessários:
  • a REGRESSÃO EXATA do artigo real, que protege a ocorrência;
  • a FAMÍLIA geral, que protege os casos equivalentes que ainda não vimos.

E, com igual peso, os NEAR-NEGATIVES: a autofalência, o pedido de recuperação
judicial da própria empresa e a falência de fato decretada continuam
pontuando. Uma regra que apagasse esses trocaria um falso positivo por um
falso negativo — pior, porque falso negativo não aparece no painel.
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


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(title: str, company: str, summary: str = "") -> set:
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1785000000,
                             "pub_iso": "2026-07-20 10:00",
                             "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def regra(title: str, company: str, summary: str = "") -> str:
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1785000000,
                             "pub_iso": "2026-07-20 10:00",
                             "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    ds = h["articles"]["u1"].get("semantic_discards") or []
    return (ds[0].get("regra") if ds else "") or ""


URL_REAL = ("https://diariodocomercio.com.br/mix/"
            "santander-surpreende-e-entra-na-justica-para-solicitar-falencia/")
TITULO_REAL = "Santander surpreende e entra na Justiça para solicitar falência"

print("=" * 98)
print("BLOCO A — REGRESSÃO EXATA: o artigo real do Santander")
print("=" * 98)
_r = pontua(TITULO_REAL, "Santander Brasil")
check("falencia" not in _r,
      f"[1] o artigo real NÃO pontua falência para o Santander ({sorted(_r)})")
check(regra(TITULO_REAL, "Santander Brasil")
      == "R_REQUERENTE_DE_FALENCIA_NAO_E_O_FALIDO",
      "[2] e a rejeição é atribuída à regra nomeada, não a um acaso")
_ev = sa.is_monitored_requerente_insolvencia(TITULO_REAL, "Santander Brasil",
                                             ["Santander Brasil", "Santander"])
check(_ev, f"[3] o detector devolve a EVIDÊNCIA que sustenta a decisão "
           f"(\"{_ev[:60]}…\")")

# o resumo do coletor era a repetição do título — o caso é title-only
_r2 = pontua(TITULO_REAL, "Santander Brasil",
             summary=TITULO_REAL + " &nbsp; Diário do Comércio")
check("falencia" not in _r2,
      "[4] funciona com a evidência REAL disponível: título + resumo duplicado")

_revs = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                          encoding="utf-8"))
_k = f"{URL_REAL}||Santander Brasil||falencia"
check(_k in _revs, "[5] a adjudicação humana está registrada no mecanismo oficial")
check(_revs[_k]["status"] == "FALSE_POSITIVE"
      and _revs[_k]["reviewer_type"] == "human",
      f"[6] veredito FALSE_POSITIVE por revisor humano "
      f"({_revs[_k]['status']}/{_revs[_k]['reviewer_type']})")
check(_revs[_k]["family_id"] == "CREDITOR_VS_DEBTOR",
      "[7] classificada na família credor×devedor")
check("Minera Cobre Verde" in _revs[_k]["note"],
      "[8] e a nota nomeia o devedor real apurado externamente")

print()
print("=" * 98)
print("BLOCO B — A FAMÍLIA: requerente ≠ falido, de forma geral")
print("=" * 98)
_familia = [
    ("Bradesco requer falência de fornecedor", "Bradesco", 9),
    ("Itaú Unibanco ajuizou pedido de falência contra a Beta",
     "Itaú Unibanco", 10),
    ("Banco do Brasil pede falência da Empresa Alfa", "Banco do Brasil", 11),
    ("BTG Pactual protocolou pedido de falência contra a Gama",
     "BTG Pactual", 12),
    ("Santander Brasil entrou na Justiça para pedir falência de devedor",
     "Santander Brasil", 13),
]
for _t, _c, _n in _familia:
    check("falencia" not in pontua(_t, _c),
          f"[{_n}] {_t[:62]}")

print()
print("=" * 98)
print("BLOCO C — OUTROS IDIOMAS")
print("=" * 98)
check("falencia" not in pontua("Santander Brasil pide la quiebra de Gamma",
                               "Santander Brasil"),
      "[14] espanhol: 'pide la quiebra de' não é falência do requerente")
check("falencia" not in pontua(
    "BTG Pactual files for bankruptcy against Delta Corp", "BTG Pactual"),
      "[15] inglês: 'files for bankruptcy AGAINST' é requerimento")
check("falencia" not in pontua("BTG Pactual seeks bankruptcy of Delta Corp",
                               "BTG Pactual"),
      "[16] inglês: 'seeks bankruptcy of' idem")

print()
print("=" * 98)
print("BLOCO D — NEAR-NEGATIVES: o evento próprio CONTINUA pontuando")
print("=" * 98)
_positivos = [
    ("Justiça decreta falência da Vale", "Vale", "falencia", 17),
    ("YPF tem falência decretada pela Justiça", "YPF", "falencia", 18),
    ("Vale pede sua própria falência", "Vale", "falencia", 19),
    ("Petrobras entra com pedido de recuperação judicial", "Petrobras",
     "recuperacao_judicial", 20),
    ("Americanas pede recuperação judicial", "Americanas",
     "recuperacao_judicial", 21),
    ("Vale files for bankruptcy", "Vale", "falencia", 22),
]
for _t, _c, _ev_id, _n in _positivos:
    _res = pontua(_t, _c)
    check(_ev_id in _res,
          f"[{_n}] {_t[:56]} → mantém {_ev_id} ({sorted(_res)})")

check("falencia" not in pontua(
    "BTG Pactual é uma das credoras no processo de falência da Ômega",
    "BTG Pactual"),
      "[23] credora citada em processo alheio não herda a falência")

print()
print("=" * 98)
print("BLOCO E — A REGRA É GERAL, NÃO UM REMENDO DE CASO")
print("=" * 98)
_fonte = inspect.getsource(sa.is_monitored_requerente_insolvencia)
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _fonte.splitlines()))
for _n, _nome in enumerate(("Santander", "Minera", "Cobre Verde",
                            "diariodocomercio"), start=24):
    check(_nome not in _cod, f"[{_n}] o detector não menciona '{_nome}'")
check("R_REQUERENTE_DE_FALENCIA_NAO_E_O_FALIDO" in
      io.open("semantic_audit.py", encoding="utf-8").read(),
      "[28] a regra tem nome próprio e aparece na proveniência da rejeição")
check("recuperacao" not in sa._INSOLVENCIA_REQUERIDA,
      "[29] `recuperação judicial` fica FORA do gatilho de requerimento — "
      "pedi-la é, quase sempre, pedido da própria empresa")

_janela = sa._VERBOS_REQUERIMENTO
check("decreta" not in _janela and "decretada" not in _janela,
      "[30] verbos de DECISÃO judicial não são verbos de requerimento")
check(sa.is_monitored_requerente_insolvencia(
    "Justiça decreta falência da Vale", "Vale", ["Vale"]) == "",
      "[31] e o detector fica calado quando a monitorada é a falida")
check(sa.is_monitored_requerente_insolvencia(
    "Vale pede sua própria falência", "Vale", ["Vale"]) == "",
      "[32] nem dispara na autofalência")
check(sa.is_monitored_requerente_insolvencia("", "Vale", ["Vale"]) == "",
      "[33] texto vazio não produz decisão")
check(sa.is_monitored_requerente_insolvencia("qualquer coisa", "", []) == "",
      "[34] sem nome de empresa, não decide")

print()
print("=" * 98)
print("BLOCO F — DEVEDOR DESCONHECIDO NÃO É INVENTADO")
print("=" * 98)
_h = {"articles": {"u1": {"title": TITULO_REAL, "summary": "", "source": "s",
                          "domain": "exemplo.com", "pub_ts": 1785000000,
                          "pub_iso": "2026-07-20 10:00",
                          "companies": ["Santander Brasil"]}}, "run_count": 1}
rd._reclassify_only_pass(_h, cfg)
_d = (_h["articles"]["u1"].get("semantic_discards") or [{}])[0]
check(not (_d.get("subject_company") or ""),
      "[35] o falido NÃO é nomeado quando o texto não o nomeia — a conclusão "
      "'a monitorada é a requerente' não depende de saber quem é o requerido")
check("REQUER" in (_d.get("motivo") or ""),
      "[36] mas o motivo registrado diz por que ela não é a falida")

print()
print("=" * 98)
print(f"RESULTADO CREDOR REQUERENTE: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
