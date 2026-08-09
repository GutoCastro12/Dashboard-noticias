#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b2_subsidiaria.py — 4I.2 Waves B2 + B2.1.

B2: subsidiária nomeada ao lado da controladora — corrige o SUJEITO, usando a
política JÁ existente (subject != monitorada → contexto de terceiro).

B2.1: normalização do `subject_company`. A causa do "Omnicare to GenieRX" era
sintática: `_ENT_NOME_B2` exigia inicial maiúscula com `[A-Z]`, mas as buscas
rodam sob `re.I`, o que ANULA essa exigência — conectivos e verbos minúsculos
passavam a casar como tokens de nome próprio. Corrigido com flag local
`(?-i:…)`, não com lista de palavras de corte.
"""
from __future__ import annotations
import json
import semantic_audit as sa
import risk_dashboard as rd

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


def sub(t, m):
    return sa.detect_subsidiary_subject(t, m, [m])


def rodar(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    ctx = (rec.get("context_events_by_company") or {}).get(company) or []
    return set(rd.event_ids_for(rec, company) or []), ctx


print("=" * 96)
print("BLOCO A — B2.1: sujeito normalizado (§7.1/§7.2)")
print("=" * 96)
check(sub("Bankruptcy judge approves sale of CVS Health subsidiary Omnicare to GenieRX",
          "CVS Health") == "Omnicare",
      "[1] 'CVS Health subsidiary Omnicare to GenieRX' → subject = 'Omnicare'")
check(sub("CVS Health’s Omnicare files for Chapter 11 bankruptcy", "CVS Health") == "Omnicare",
      "[2] \"CVS Health's Omnicare files for Chapter 11\" → subject = 'Omnicare'")

print()
print("=" * 96)
print("BLOCO B — §6: nomes multipalavra preservados inteiros")
print("=" * 96)
check(sub("Vale subsidiary Banco Digimais entra em falência", "Vale") == "Banco Digimais",
      "[3] nome de DUAS palavras preservado ('Banco Digimais')")
check(sub("Cencosud subsidiary St. Marche pede recuperação judicial",
          "Cencosud") == "St. Marche",
      "[3b] nome com ponto preservado ('St. Marche')")
check(sub("Itau subsidiary Banco de Brasilia entra em falencia",
          "Itau") == "Banco de Brasilia",
      "[4] nome com conectivo minúsculo interno preservado ('Banco de Brasilia')")
check(sub("Bradesco subsidiary Alpha Beta Gamma Holdings pede RJ",
          "Bradesco") == "Alpha Beta Gamma Holdings",
      "[4b] nome de QUATRO palavras preservado")

print()
print("=" * 96)
print("BLOCO C — §5/§10: contraparte não entra no sujeito")
print("=" * 96)
check("GenieRX" not in (sub("Bankruptcy judge approves sale of CVS Health subsidiary "
                            "Omnicare to GenieRX", "CVS Health") or ""),
      "[5] contraparte após 'to' NÃO entra no subject (GenieRX fora)")
check(sub("Vale subsidiaria Alpha Mineradora para Beta Corp", "Vale") == "Alpha Mineradora",
      "[6] contraparte após 'para' NÃO entra no subject")
check(sub("Cemex subsidiaria Alpha Cementos a Gamma SA", "Cemex") == "Alpha Cementos",
      "[7] contraparte após 'a' (es) NÃO entra no subject")

print()
print("=" * 96)
print("BLOCO D — §9: o valor limpo chega ao consumidor final (payload)")
print("=" * 96)
ids, ctx = rodar("Bankruptcy judge approves sale of CVS Health subsidiary Omnicare to GenieRX",
                 "CVS Health")
ev = next((x for x in ctx if x.get("event_id") == "falencia"), {})
check("falencia" not in ids, "[9a] scoreable=False para CVS (falência não pontua)")
check(ev.get("subject_company") == "Omnicare",
      f"[9b] payload: subject_company = 'Omnicare' (obtido {ev.get('subject_company')!r})")
check(ev.get("relation_type") == "subsidiaria", "[9c] payload: relation_type = 'subsidiaria'")
check(ev.get("event_scope") == "indireto", "[9d] payload: event_scope = 'indireto'")
check(ev.get("scoreable") is False, "[9e] payload: scoreable = False")
ids2, ctx2 = rodar("CVS Health’s Omnicare files for Chapter 11 bankruptcy", "CVS Health")
ev2 = next((x for x in ctx2 if x.get("event_id") == "falencia"), {})
check("falencia" not in ids2 and ev2.get("subject_company") == "Omnicare",
      "[9f] segundo caso CVS/Omnicare idem, com sujeito limpo")

print()
print("=" * 96)
print("BLOCO E — §8: nenhum efeito econômico alterado")
print("=" * 96)
check("falencia" in rodar("CVS Health files for Chapter 11 bankruptcy", "CVS Health")[0],
      "[10] falência PRÓPRIA da CVS continua pontuando")
check("ma" in rodar("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group")[0],
      "[8] M&A da Cigna/Evernorth preservado (subsidiária que PRATICA o evento)")
check("recuperacao_judicial" not in rodar(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")[0]
      and "recuperacao_judicial" in rodar(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração")[0],
      "[9] Vale/Samarco preservado nos dois lados")
check("falencia" not in rodar(
        "Bankruptcy judge approves sale of CVS Health subsidiary Omnicare to GenieRX",
        "CVS Health")[0],
      "[11] CVS/Omnicare continua contexto e não volta a pontuar")

print()
print("=" * 96)
print(f"RESULTADO WAVE B2/B2.1: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
