#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_caso_nomeado_fraude.py — o nome do processo não imputa autoria.

O CASO QUE ORIGINOU

Três matérias faziam a Duke Energy aparecer como CRÍTICA por `fraude`:

  1. "Eileen Taylor Weighs in on Duke Energy Fraud Case"
  2. "Dazmyn Person: Another person was arrested, charged in the ongoing
      Duke Energy fraud case"
  3. "Third suspect arrested in Duke Energy fraud case"

Nas três a Duke é vítima/entidade que dá nome ao caso; quem responde é
terceiro. As três já tinham adjudicação humana FALSE_POSITIVE por URL exata —
e continuavam pontuando, porque a verdade humana é avaliação, não policy de
produção. A autoridade de produção é a REGRA, e a regra tinha uma lacuna.

A LACUNA, MEDIDA

`FRAUDE_VITIMA` já cobria "empresa combate fraude" e "fraude contra clientes",
e tinha UM padrão para caso nomeado — `charged in {m} fraud` — que exige
"charged in" colado ao nome. As três construções reais escapavam: palavras
intercaladas ("the ongoing"), outro verbo ("arrested in") e comentário
("weighs in on").

O QUE ESTE TESTE PROTEGE, NOS DOIS SENTIDOS

A regra exige DUAS evidências — o caso nomeado E um ator terceiro ou
comentário. Só o nome jamais basta, porque uma regra ampla apagaria fraude
real: Citigroup é RÉU e TIM foi CONDENADA, ambos com verdade humana TRUE, e
ambos precisam continuar pontuando. Trocar um falso positivo por um falso
negativo seria pior — falso negativo não aparece no painel.
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
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def regra(title: str, company: str) -> str:
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1785000000,
                             "pub_iso": "2026-07-20 10:00",
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    ds = h["articles"]["u1"].get("semantic_discards") or []
    return (ds[0].get("regra") if ds else "") or ""


D1 = "Eileen Taylor Weighs in on Duke Energy Fraud Case"
D2 = ("Dazmyn Person: Another person was arrested, charged in the ongoing "
      "Duke Energy fraud case")
D3 = "Third suspect arrested in Duke Energy fraud case"
D4 = ("Duke Energy leverages artificial intelligence to combat fraud "
      "targeting customers")

print("=" * 98)
print("BLOCO A — REGRESSÃO EXATA: os três artigos reais da Duke")
print("=" * 98)
for _n, (_t, _rot) in enumerate([(D1, "comentário sobre o caso"),
                                 (D2, "pessoa indiciada no caso"),
                                 (D3, "suspeito preso no caso")], start=1):
    _r = pontua(_t, "Duke Energy")
    check("fraude" not in _r,
          f"[{_n}] {_rot}: NÃO pontua fraude para a Duke ({sorted(_r)})")
check(regra(D1, "Duke Energy") == "R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA",
      "[4] e a rejeição é atribuída à regra nomeada, não a um acaso")
check(regra(D3, "Duke Energy") == "R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA",
      "[5] idem para o suspeito preso")

print()
print("=" * 98)
print("BLOCO B — CONTROLE POSITIVO EXISTENTE: não regredir")
print("=" * 98)
check("fraude" not in pontua(D4, "Duke Energy"),
      "[6] 'Duke combate fraude' segue não pontuando")
check(regra(D4, "Duke Energy") == "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE",
      "[7] e continua saindo pela regra de VÍTIMA, não pela nova — a família "
      "antiga não foi canibalizada")

print()
print("=" * 98)
print("BLOCO C — NEAR-NEGATIVES CRÍTICOS: fraude REAL continua pontuando")
print("=" * 98)
CITI = "SCOTUS Declines to Intervene in Billion-Dollar Fraud Showdown Against Citigroup"
TIM = "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC"
check("fraude" in pontua(CITI, "Citigroup"),
      "[8] Citigroup RÉU (verdade humana TRUE) segue pontuando")
check("fraude" in pontua(TIM, "TIM Brasil"),
      "[9] TIM CONDENADA (verdade humana TRUE) segue pontuando")
_reais = [
    ("Empresa Alfa is accused of accounting fraud", "Empresa Alfa", 10),
    ("Court finds Empresa Alfa liable for fraud", "Empresa Alfa", 11),
    ("Fraud allegations against Empresa Alfa mount", "Empresa Alfa", 12),
    ("Empresa Alfa charged with fraud by regulators", "Empresa Alfa", 13),
    ("Empresa Alfa committed fraud, says prosecutor", "Empresa Alfa", 14),
]
for _t, _c, _n in _reais:
    check("fraude" in pontua(_t, _c),
          f"[{_n}] segue pontuando: {_t[:56]}")

print()
print("=" * 98)
print("BLOCO D — A REGRA EXIGE DUAS EVIDÊNCIAS, NUNCA SÓ O NOME")
print("=" * 98)
_só_nome = "Novidades sobre o Empresa Alfa fraud case"
check(sa.is_caso_nomeado_com_autor_terceiro(_só_nome, "Empresa Alfa",
                                            ["Empresa Alfa"]) == "",
      "[15] só a expressão '<Empresa> fraud case' NÃO dispara a regra")
check(sa.is_caso_nomeado_com_autor_terceiro(
    "Third suspect arrested in Empresa Alfa fraud case", "Empresa Alfa",
    ["Empresa Alfa"]),
      "[16] caso nomeado + ator terceiro responsabilizado dispara")
check(sa.is_caso_nomeado_com_autor_terceiro(
    "Analyst weighs in on Empresa Alfa fraud case", "Empresa Alfa",
    ["Empresa Alfa"]),
      "[17] caso nomeado + comentário dispara")
check(sa.is_caso_nomeado_com_autor_terceiro(
    "Suspect arrested after robbery downtown", "Empresa Alfa",
    ["Empresa Alfa"]) == "",
      "[18] ator terceiro SEM caso nomeado não dispara")

print()
print("=" * 98)
print("BLOCO E — GUARDA DE INSIDER: quem é da casa não é terceiro")
print("=" * 98)
for _t, _n in ((("Executives of Empresa Alfa arrested in Empresa Alfa "
                 "fraud case"), 19),
               (("Empresa Alfa executives charged in Empresa Alfa fraud "
                 "case"), 20),
               (("Justiça condena executivos e ex-diretores da Empresa Alfa "
                 "por fraude"), 21)):
    check(sa.is_caso_nomeado_com_autor_terceiro(_t, "Empresa Alfa",
                                                ["Empresa Alfa"]) == "",
          f"[{_n}] executivos DA própria empresa não acionam a regra")

print()
print("=" * 98)
print("BLOCO F — FAMÍLIA GERAL: outros nomes, outras construções")
print("=" * 98)
_familia = [
    ("Third suspect arrested in Banco Beta fraud case", "Banco Beta", 22),
    ("Another person was charged in the ongoing Banco Beta fraud "
     "investigation", "Banco Beta", 23),
    ("Customer indicted in Banco Beta fraud scheme", "Banco Beta", 24),
    ("Professor comments on Banco Beta fraud case", "Banco Beta", 25),
]
for _t, _c, _n in _familia:
    check(sa.is_caso_nomeado_com_autor_terceiro(_t, _c, [_c]),
          f"[{_n}] generaliza: {_t[:58]}")
check(sa.is_caso_nomeado_com_autor_terceiro("", "Banco Beta", ["Banco Beta"]) == "",
      "[26] texto vazio não produz decisão")
check(sa.is_caso_nomeado_com_autor_terceiro("qualquer coisa", "", []) == "",
      "[27] sem nome de empresa, não decide")

print()
print("=" * 98)
print("BLOCO G — REGRA GERAL, NÃO REMENDO DE CASO")
print("=" * 98)
_fonte = inspect.getsource(sa.is_caso_nomeado_com_autor_terceiro)
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _fonte.splitlines()))
for _n, _nome in enumerate(("Duke", "Eileen", "Dazmyn", "Citigroup", "TIM",
                            "poole", "wral", "abc11"), start=28):
    check(_nome.lower() not in _cod.lower(),
          f"[{_n}] o detector não menciona '{_nome}'")
check("R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA" in
      io.open("semantic_audit.py", encoding="utf-8").read(),
      "[36] a regra tem nome próprio e aparece na proveniência da rejeição")
check(sa.detect_fraud_role(D3, "Duke Energy", ["Duke Energy"]) == "caso_nomeado",
      "[37] o detector de papel devolve o rótulo próprio da construção")
check(sa.detect_fraud_role(D4, "Duke Energy", ["Duke Energy"]) == "vitima",
      "[38] e o controle de combate segue como 'vitima' — famílias distintas")
check(sa.detect_fraud_role(CITI, "Citigroup", ["Citigroup"]) != "caso_nomeado",
      "[39] Citigroup não recebe o rótulo de caso nomeado")

print()
print("=" * 98)
print("BLOCO H — BLAST RADIUS SOBRE O HISTÓRICO REAL")
print("=" * 98)
# Este bloco precisa continuar MEDINDO depois que a correção foi aplicada ao
# histórico. Uma contagem de "quantos ainda pontuam" viraria 0 e passaria por
# vacuidade — não provaria nada. Então mede-se os dois lados: nenhum evento de
# fraude alcançado pela regra pode estar pontuável em NENHUMA empresa, e os
# três casos adjudicados têm de estar presentes e atribuídos a esta regra.
_h = json.load(io.open("risk_history.json", encoding="utf-8"))
_al = sa._aliases_map(cfg)
_pont, _ctx = [], []
for _u, _a in (_h.get("articles") or {}).items():
    _txt = (_a.get("title") or "") + " " + (_a.get("summary") or "")
    for _emp in (_a.get("companies") or []):
        _nomes = list(_al.get(_emp) or [_emp])
        if not sa.is_caso_nomeado_com_autor_terceiro(_txt, _emp, _nomes):
            continue
        if any(e in sa.EVENTOS_FRAUDE
               for e in ((_a.get("events_by_company") or {}).get(_emp) or [])):
            _pont.append((_emp, _u))
        if any((d or {}).get("event_id") in sa.EVENTOS_FRAUDE
               for d in ((_a.get("context_events_by_company") or {}).get(_emp) or [])):
            _ctx.append((_emp, _u))
check(not _pont,
      f"[40] nenhum caso nomeado com autor terceiro segue pontuável ({_pont})")
check(len(_ctx) == 3 and {e for e, _ in _ctx} == {"Duke Energy"},
      f"[41] os três adjudicados estão preservados como contexto "
      f"({len(_ctx)}, {sorted({e for e, _ in _ctx})})")
_revs = json.load(io.open("test_fixtures_reliability/live_reviews.json",
                          encoding="utf-8"))
check(all(_revs.get(f"{u}||{e}||fraude", {}).get("status") == "FALSE_POSITIVE"
          for e, u in _ctx),
      "[42] e os três têm adjudicação humana FALSE_POSITIVE registrada")
_regras = {(d or {}).get("attribution_rule")
           for _a in (_h.get("articles") or {}).values()
           for d in ((_a.get("context_events_by_company") or {})
                     .get("Duke Energy") or [])}
check(_regras == {"R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA"},
      f"[43] o contexto da Duke carrega a proveniência da regra ({_regras})")

print()
print("=" * 98)
print(f"RESULTADO CASO NOMEADO / FRAUDE: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
