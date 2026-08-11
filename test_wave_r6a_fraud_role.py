#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6a_fraud_role.py — 4I.2 R6a / família F3.

PAPEL EXPLÍCITO SUPERA FASE.

A auditoria da R5c mostrou que os artigos do caso Duke deixavam de pontuar só
porque `allegedly`/`investigation` marcavam a fase como não consumada — o
runtime continuava com `subject_company = Duke Energy`, isto é, seguia
achando que a fraude era dela. Bastaria a fraude ser confirmada para o evento
voltar a pontuar CONTRA A VÍTIMA.

Papel e fase são dimensões diferentes: fase diz se um fato está confirmado,
papel diz de quem é o fato. Por isso a evidência de papel passou a ser
avaliada ANTES da fase — e só com evidência POSITIVA.

Os contrafactuais de fase são o coração deste arquivo: trocar `allegedly` por
`was responsible` não pode fazer a vítima virar autora.
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


def pontua(titulo, resumo, empresa):
    h = {"articles": {"u1": {"title": titulo, "summary": resumo, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": [empresa]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    r = h["articles"]["u1"]
    got = set((r.get("events_by_company") or {}).get(empresa) or [])
    regras = {d.get("event_id"): d.get("regra")
              for d in (r.get("semantic_discards") or [])
              if d.get("empresa") == empresa}
    return got, regras


def papel(txt, emp):
    return sa.detect_fraud_victim_evidence(txt, emp, AL.get(emp) or [emp])


D2_T = ("Dazmyn Person: Another person was arrested, charged in the ongoing "
        "Duke Energy fraud case")
D2_E = ("WCSO identified a woman allegedly responsible for stealing victims' "
        "identities to assist in creating fraudulent Duke Energy accounts.")
D2_CONF = D2_E.replace("allegedly responsible", "was responsible")
D3_T = "Third suspect arrested in Duke Energy fraud case"
D3_E = ("On Tuesday deputies arrested a suspect after deputies said Duke Energy "
        "representatives discovered numerous accounts were opened in the name of "
        "living and deceased customers without their knowledge, costing the "
        "company 495904 in losses.")
D3_CONF = D3_E.replace("arrested a suspect", "convicted a suspect")

print("=" * 96)
print("BLOCO A — Duke: current, enriched e o contrafactual de fase")
print("=" * 96)
for tag, tit, enr, conf in (("Duke #2", D2_T, D2_E, D2_CONF),
                            ("Duke #3", D3_T, D3_E, D3_CONF)):
    g, _ = pontua(tit, "", "Duke Energy")
    check("fraude" in g, f"[1..2] {tag} CURRENT ainda pontua — o input pobre não decide papel")
    g, r = pontua(tit, enr, "Duke Energy")
    p = papel(f"{tit}. {enr}", "Duke Energy")
    check("fraude" not in g and p.get("role") == "vitima",
          f"[3..4] {tag} ENRICHED: papel de vítima reconhecido ({p.get('rule')})")
    check(r.get("fraude") not in (None, "R_FRAUDE_NAO_CONFIRMADA"),
          f"[5..6] {tag} não cai mais por fase — regra: {r.get('fraude')}")
    g, r = pontua(tit, conf, "Duke Energy")
    p = papel(f"{tit}. {conf}", "Duke Energy")
    check("fraude" not in g and p.get("role") == "vitima",
          f"[7..8] {tag} CONFIRMADO: vítima continua vítima ({p.get('rule')})")

print()
print("=" * 96)
print("BLOCO B — as três formas de evidência positiva de papel")
print("=" * 96)
for tag, txt, emp, regra in (
    ("V1 detecta", "Petrobras detected a fraud scheme with accounts opened by suspects",
     "Petrobras", "R_FRAUDE_VITIMA_DETECTORA"),
    ("P1 ator externo", "Suspect convicted in fraud for creating fraudulent Citigroup accounts",
     "Citigroup", "R_FRAUDE_ATOR_EXTERNO"),
    ("P1 impersonação", "Scammers impersonated Vale in a fraud that stole customer information",
     "Vale", "R_FRAUDE_ATOR_EXTERNO"),
    ("V2 prejuízo", "Fraud scheme hit Bradesco, costing the company 495000 in losses",
     "Bradesco", "R_FRAUDE_PREJUIZO_DE_TERCEIRO"),
):
    p = papel(txt, emp)
    check(p.get("rule") == regra,
          f"[9..12] {tag}: {p.get('rule')} (esperado {regra})")

print()
print("=" * 96)
print("BLOCO C — responsabilização e agência própria anulam o papel de vítima")
print("=" * 96)
for tag, txt, emp in (
    ("N1 liable", "Vale was found liable for fraud", "Vale"),
    ("N2 admitiu", "Vale admitted fraud in regulatory filing", "Vale"),
    ("N3 executivos", "Vale executives orchestrated fraud scheme", "Vale"),
    ("N6 própria casa", "Vale discovered that its executives had committed fraud", "Vale"),
    ("N7 esquema próprio",
     "Vale suffered losses after its own fraudulent scheme was uncovered in a fraud case", "Vale"),
    ("CVS liable", "Omnicare and its parent company, CVS Health, were found liable "
     "for fraudulently dispensing drugs in a fraud case", "CVS Health"),
):
    check(papel(txt, emp) == {}, f"[13..18] {tag}: nenhuma evidência de vítima produzida")

_g, _ = pontua("Vale was found liable for fraud", "", "Vale")
check("fraude" in _g, "[19] empresa responsabilizada continua pontuando")
_g, _ = pontua("Vale executives orchestrated fraud scheme", "", "Vale")
check("fraude" in _g, "[20] fraude de executivos da própria empresa continua pontuando")

print()
print("=" * 96)
print("BLOCO D — papel e fase são independentes")
print("=" * 96)
_alegada_ext = "Suspect allegedly created fraudulent Vale accounts in a fraud"
_confirm_ext = "Suspect convicted of creating fraudulent Vale accounts in a fraud"
check(papel(_alegada_ext, "Vale").get("role") == "vitima"
      and papel(_confirm_ext, "Vale").get("role") == "vitima",
      "[21] fraude externa alegada OU confirmada: a empresa segue vítima")
_aleg_own = "Vale allegedly committed fraud"
_conf_own = "Vale was convicted of fraud"
check(papel(_aleg_own, "Vale") == {} and papel(_conf_own, "Vale") == {},
      "[22] fraude da própria empresa, alegada ou confirmada: nunca vítima")
_g, _r = pontua(_aleg_own, "", "Vale")
check("fraude" not in _g and _r.get("fraude") == "R_FRAUDE_NAO_CONFIRMADA",
      "[23] e a fase continua fazendo o seu trabalho onde é o caso")

print()
print("=" * 96)
print("BLOCO E — multi-entidade, subsidiária e ausência de hard-code")
print("=" * 96)
check(papel("Vale alleges Petrobras committed fraud", "Petrobras") == {},
      "[24] a acusada não vira vítima por outra empresa acusá-la")
_g, _ = pontua("Vale and subsidiary Alpha were found liable for fraud", "", "Vale")
check("fraude" in _g, "[25] responsabilização de controladora + subsidiária preservada")
_g, _ = pontua("Subsidiary Alpha of Vale committed fraud", "", "Vale")
check("fraude" in _g, "[26] atribuição existente de subsidiária não é quebrada")

_src = open("semantic_audit.py", encoding="utf-8").read()
_bloco = _src.split("# ── 4I.2 R6a/F3")[1].split("def detect_fraud_role")[0]
_codigo = "\n".join(l for l in _bloco.splitlines() if not l.strip().startswith("#"))
for termo in ("Duke", "Dazmyn", "WCSO", "Petrobras", "Citigroup", "CVS", "abc11", "wral"):
    check(termo not in _codigo, f"[27..34] nenhum hard-code de '{termo}'")
for termo in ("requests", "urlopen", "openai", "anthropic", "spacy"):
    check(termo not in _codigo, f"[35..39] sem rede/NER/LLM ('{termo}')")

print()
print("=" * 96)
print("BLOCO F — provenance auditável")
print("=" * 96)
p = papel(D2_E, "Duke Energy")
check(set(p) == {"role", "rule", "evidence"},
      f"[40] o papel devolve role, rule e evidência determinística: {sorted(p)}")
check(p["evidence"] and len(p["evidence"]) <= 120,
      f"[41] a evidência é um trecho literal limitado: {p['evidence'][:60]!r}")
_, _r = pontua(D2_T, D2_E, "Duke Energy")
check(_r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO",
      f"[42] a regra vencedora fica registrada no discard: {_r.get('fraude')}")

print()
print("=" * 96)
print(f"RESULTADO WAVE R6a (papel de fraude): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
