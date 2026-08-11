#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r4b_possessive_subsidiary.py — 4I.2 R4b / regressão B2.

CAPITALIZAÇÃO NÃO É PROVA DE ENTIDADE. Em manchete tudo vem capitalizado, e
o padrão possessivo da B2 (`X's <Capitalizado>`) tratava qualquer token como
razão social: `Vale's Chapter 11` produzia `subsidiary_subject = "Chapter"`,
e `R_EVENTO_DE_SUBSIDIARIA_NOMEADA` removia o evento antes de a F2 poder
avaliá-lo.

A correção é ESTRUTURAL, não uma exceção por caso: a sequência capturada é
truncada no primeiro token que pertence a uma classe de substantivo comum ou
verbo — termo jurídico, papel corporativo, atributo financeiro, substantivo
de transação. Sobrando nome, ele é a entidade; não sobrando, não há
subsidiária. O possessivo legítimo continua funcionando.
"""
from __future__ import annotations

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def sub(t, emp):
    return sa.detect_subsidiary_subject(t, emp, AL.get(emp) or [emp])


def pontua(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    return (set(rec.get("events_by_company", {}).get(company) or []),
            {d.get("regra") for d in (rec.get("semantic_discards") or [])
             if d.get("empresa") == company})


print("=" * 96)
print("BLOCO A — o bug: `Vale's Chapter 11` não é uma subsidiária chamada Chapter")
print("=" * 96)
_T = "Bankruptcy Court approves Vale's Chapter 11 reorganization plan"
check(sub(_T, "Vale") == "", f"[1] nenhuma subsidiária extraída (obtido {sub(_T, 'Vale')!r})")
_g, _r = pontua(_T, "Vale")
check("R_EVENTO_DE_SUBSIDIARIA_NOMEADA" not in _r,
      "[2] R_EVENTO_DE_SUBSIDIARIA_NOMEADA não dispara mais nesse título")
check("falencia" in _g, f"[3] a insolvência da própria Vale volta a pontuar ({sorted(_g)})")

print()
print("=" * 96)
print("BLOCO B — a CLASSE, não o token: complemento possessivo que não é entidade")
print("=" * 96)
for t, emp, classe in [
    ("Bankruptcy Court approves Vale's Chapter 11 reorganization plan", "Vale", "termo jurídico"),
    ("Petrobras's bankruptcy filing draws objections", "Petrobras", "termo jurídico"),
    ("Ecopetrol's restructuring plan advances", "Ecopetrol", "termo jurídico"),
    ("Bradesco's CEO steps down", "Bradesco", "papel corporativo"),
    ("Duke Energy's Board approves buyback", "Duke Energy", "papel corporativo"),
    ("Cosan's Credit Rating cut by Moody's", "Cosan", "atributo financeiro"),
    ("Vale's Debt Load Worries Investors", "Vale", "atributo financeiro"),
    ("Vale's Acquisition Of Alpha Wins Approval", "Vale", "substantivo de transação"),
    ("Capital One's Acquisition Of Discover Won't Save It From Declining ROE",
     "Capital One Financial", "substantivo de transação"),
]:
    check(sub(t, emp) == "",
          f"[4..12] {classe} não vira subsidiária (obtido {sub(t, emp)!r}) :: {t[:52]}")

print()
print("=" * 96)
print("BLOCO C — subsidiária legítima continua detectada")
print("=" * 96)
for t, emp, esperado in [
    ("CVS Health’s Omnicare files for Chapter 11 bankruptcy", "CVS Health", "Omnicare"),
    ("Sterling Specialty Chemicals to acquire stake in Halliburton’s Multi-Chem business",
     "Halliburton", "Multi-Chem"),
    ("Vale subsidiary Alpha files for bankruptcy", "Vale", "Alpha"),
    ("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group", "Evernorth"),
]:
    check(sub(t, emp) == esperado,
          f"[13..16] subsidiária preservada: esperado {esperado!r}, obtido {sub(t, emp)!r}")

print()
print("=" * 96)
print("BLOCO D — a truncagem melhora a extração, não apenas rejeita")
print("=" * 96)
check(sub("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group") == "Evernorth",
      "[17] o predicado deixa de ser engolido junto com o nome")
check(sub("Virginia Governor Is ‘Skeptical’ of NextEra Energy’s Dominion Acquisition. "
          "Virginia Governor weighs in", "NextEra Energy") == "Dominion",
      "[18] a captura não atravessa o fim da frase")
check(sub("Capital One’s Discover Card Migration Reshapes Payments Economics",
          "Capital One Financial") == "Discover Card",
      "[19] substantivo de evento corta a sequência, o nome fica")
check(sub("Prudential Financial's Japan Operations Suspected Of Fraud At Gibraltar Life Unit",
          "Prudential Financial") == "Japan Operations",
      "[20] particípio não entra no nome da unidade")

print()
print("=" * 96)
print("BLOCO E — helper puro: sem lista de exceção por caso, sem rede/NER")
print("=" * 96)
check(sa._nome_entidade_limpo("Chapter 11") == "",
      "[21] `Chapter 11` não sobrevive à truncagem")
check(sa._nome_entidade_limpo("Omnicare") == "Omnicare",
      "[22] nome próprio simples sobrevive intacto")
check(sa._nome_entidade_limpo("Banco de Brasilia") == "Banco de Brasilia",
      "[23] nome multipalavra com conectivo sobrevive intacto")
check(sa._nome_entidade_limpo("St. Marche") == "St. Marche",
      "[24] abreviação com ponto não é confundida com fim de frase")
import inspect  # noqa: E402  (usado só nas asserções de escopo)

# a asserção olha o MECANISMO novo — a classe de tokens e o helper —, não a
# documentação pré-existente da B2, que cita exemplos por escrito.
_codigo = (inspect.getsource(sa._nome_entidade_limpo)
           + "\n" + "\n".join(sorted(sa._NAO_ENTIDADE_TOKENS)))
for termo in ("Vale", "Omnicare", "Multi-Chem", "Evernorth", "Discover", "Capital One"):
    check(termo not in _codigo, f"[25..30] nenhum hard-code de '{termo}'")
for termo in ("requests", "urlopen", "openai", "anthropic", "spacy"):
    check(termo not in _codigo, f"[31..35] sem rede/NER/LLM ('{termo}')")

print()
print("=" * 96)
print(f"RESULTADO WAVE R4b (possessivo/subsidiária): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
