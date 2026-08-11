#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r4_legal_venue.py — 4I.2 R4 / família F2.

FORO JUDICIAL ≠ INSOLVÊNCIA DA MONITORADA. `Bankruptcy Court` é o nome de um
tribunal; a palavra `bankruptcy` ali designa a competência da corte, não um
fato da empresa citada — o litígio pode ser de privacidade, contrato ou
descoberta.

A regra proibida — `bankruptcy court` ⇒ descarta falência — NÃO foi
implementada: ela apagaria "Bankruptcy Court approves Company's Chapter 11
plan", onde o foro é o mesmo mas a insolvência é real.
`R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA` exige DUAS condições cumulativas:

  1. TODA ocorrência de termo do evento está dentro do nome da instituição;
  2. nenhuma prova positiva liga insolvência à monitorada — possessivo com
     Chapter 11 / restructuring plan, verbo de protocolo, `debtor X`,
     `falência de X`.

Escopo: `falencia` e `recuperacao_judicial`, e lexicalmente apenas as
construções observadas no corpus (`Bankruptcy Court`, `Bankruptcy judge`, em
inglês). Não há caso PT/ES no histórico — expandir sem evidência é proibido.
"""
from __future__ import annotations

import re

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
KW = sa._keywords_por_evento(cfg)
INSOLV = {"falencia", "recuperacao_judicial"}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(title, company, summary=""):
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    got = set(rec.get("events_by_company", {}).get(company) or [])
    regras = {d.get("regra") for d in (rec.get("semantic_discards") or [])
              if d.get("empresa") == company}
    info = {(c.get("event_id") if isinstance(c, dict) else c)
            for c in ((rec.get("informational_events_by_company") or {}).get(company) or [])}
    return got, regras, info


def foro(t, emp, ev="falencia"):
    return sa.detect_foro_judicial_sem_insolvencia(t, emp, AL.get(emp) or [emp],
                                                   KW.get(ev) or [])


_REAL = ("Bankruptcy Court Orders Texas to Strike Allegations In State Data "
         "Privacy Suit Against General Motors")

print("=" * 96)
print("BLOCO A — o caso real White & Williams / General Motors")
print("=" * 96)
_g, _r, _i = pontua(_REAL, "General Motors")
check("falencia" not in _g, f"[1] GM NÃO recebe falencia (obtido {sorted(_g)})")
check("R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA" in _r,
      "[2] a remoção é atribuída a R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA")
check("falencia" in _i,
      f"[3] o evento vira INFORMATIVO da própria GM, não contexto de terceiro ({sorted(_i)})")
check(foro(_REAL, "General Motors") == "bankruptcy court",
      "[4] a evidência devolvida é o nome do foro")

print()
print("=" * 96)
print("BLOCO B — condição (1): termo de insolvência FORA do nome do tribunal")
print("=" * 96)
for t, emp in [
    ("Bankruptcy Court declares the bankruptcy of Petrobras", "Petrobras"),
    ("US Bankruptcy Court schedules hearing on Petrobras bankruptcy filing", "Petrobras"),
    ("Petrobras files for bankruptcy in federal court", "Petrobras"),
]:
    check(foro(t, emp) == "", f"[5..7] ocorrência fora do foro neutraliza a regra :: {t[:56]}")
    _g, _, _ = pontua(t, emp)
    check(bool(INSOLV & _g), f"[8..10] e a insolvência PERMANECE em {emp} ({sorted(_g)})")

print()
print("=" * 96)
print("BLOCO C — condição (2): prova positiva de insolvência da monitorada")
print("=" * 96)
for t, emp in [
    ("Bankruptcy Court approves General Motors' Chapter 11 plan", "General Motors"),
    ("Bankruptcy judge confirms General Motors' restructuring plan", "General Motors"),
    ("US Bankruptcy Court approves the sale of debtor General Motors assets", "General Motors"),
    ("Vale files Chapter 11 petition in bankruptcy court", "Vale"),
]:
    check(foro(t, emp) == "", f"[11..14] prova positiva neutraliza a regra :: {t[:56]}")

_g, _r, _ = pontua("Bankruptcy Court approves General Motors' Chapter 11 plan", "General Motors")
check(bool(INSOLV & _g) and "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA" not in _r,
      f"[15] o plano de Chapter 11 da própria GM continua pontuando ({sorted(_g)})")

print()
print("=" * 96)
print("BLOCO D — siblings: mesma invariante, outras construções")
print("=" * 96)
for t, emp in [
    ("Bankruptcy Court dismisses unrelated privacy claim against Petrobras", "Petrobras"),
    ("Bankruptcy judge rules on discovery dispute involving Citigroup", "Citigroup"),
    ("Vale wins contract dispute in US Bankruptcy Court", "Vale"),
    ("Bankruptcy Court sets deadline in trademark dispute involving Citigroup", "Citigroup"),
    ("US Bankruptcy Court hears environmental claim naming Vale as defendant", "Vale"),
]:
    _g, _r, _ = pontua(t, emp)
    check("falencia" not in _g and "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA" in _r,
          f"[16..20] foro puro, removido pela própria F2 :: {t[:56]}")

print()
print("=" * 96)
print("BLOCO E — insolvência real sem tribunal nenhum fica intacta")
print("=" * 96)
for t, emp, ev in [
    ("Petrobras enters Chapter 11", "Petrobras", "recuperacao_judicial"),
    ("Tok&Stok: Justica aceita recuperacao judicial de R$ 1,1 bilhao que impacta 2,2 mil funcionarios",
     "Tok&Stok", "recuperacao_judicial"),
    ("CIBanco, una quiebra no merecida", "CIBanco", "falencia"),
]:
    _g, _, _ = pontua(t, emp)
    check(ev in _g, f"[21..23] {ev} preservado em {emp} :: {t[:52]}")

print()
print("=" * 96)
print("BLOCO F — escopo e ausência de hard-code")
print("=" * 96)
_src = open("semantic_audit.py", encoding="utf-8").read()
_m = re.search(r'if not _papel and ev in \("falencia", "recuperacao_judicial"\):', _src)
check(_m is not None, "[24] o gate F2 está restrito a falencia/recuperacao_judicial")
check(foro("Bankruptcy Court rules on a fraud claim against Citigroup", "Citigroup",
           "fraude") == "",
      "[25] fora do escopo de insolvência a regra não produz evidência")
_bloco = _src.split("# ── 4I.2 R4/F2")[1].split("# ── 4I.2 R3/F4")[0]
_codigo = "\n".join(l for l in _bloco.splitlines() if not l.strip().startswith("#"))
for termo in ("General Motors", "whiteandwilliams", "White and Williams", "Texas",
              "Citigroup", "Petrobras"):
    check(termo not in _codigo, f"[26..31] nenhum hard-code de '{termo}' na regra")
for termo in ("requests", "urlopen", "openai", "anthropic", "spacy"):
    check(termo not in _codigo, f"[32..36] a regra não usa rede/NER/LLM ('{termo}')")
_pat = sa._FORO_INSOLVENCIA.pattern.lower()
check(not any(x in _pat for x in ("vara", "tribunal", "juizo", "corte", "quiebra")),
      f"[37] o léxico do foro é só o observado no corpus (EN): {_pat}")

print()
print("=" * 96)
print(f"RESULTADO WAVE R4 (foro judicial): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
