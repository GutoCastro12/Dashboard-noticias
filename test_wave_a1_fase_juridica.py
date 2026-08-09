#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_a1_fase_juridica.py — 4I.2 Wave A1.

Duas falhas independentes encontradas na auditoria 4I, testadas separadamente:
  (1) `FASES_JURIDICAS` cobria mal inglês/espanhol e não disparava;
  (2) mesmo detectando fase não confirmada, o evento continuava PONTUANDO.

Invariante econômica protegida: alegação/claim/lawsuit/investigação NÃO
equivalem a fraude comprovada — mas fraude realmente comprovada continua
pontuando integralmente. Nenhum peso, threshold ou taxonomia alterado.
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def fase(texto):
    return sa.detect_juridical_phase(texto)


print("=" * 96)
print("BLOCO A — deteção de fase multilíngue (pt/en/es)")
print("=" * 96)
CASOS_NAO_CONSUMADOS = [
    ("en", "2 of Michigan's largest health systems sue CVS Health alleging fraud in drug pricing"),
    ("en", "JPMorgan Chase (NYSE:JPM) fraud-claim scrutiny weighs on stock at analysts' target"),
    ("en", "ACC probes Tk3 lakh crore fraud allegations against BAT"),
    ("en", "Prudential Financial's Japan Operations Suspected Of Fraud At Gibraltar Life Unit"),
    ("en", "Company faces civil lawsuit over accounting irregularities"),
    ("es", "Ministerio del Trabajo abre investigación a Quala y Nutresa por presunta explotación laboral"),
    ("es", "Fiscalía presenta querella por presunto fraude contable"),
    ("pt", "AEGEA SOB MAIS UMA SUSPEITA: CRISE FINANCEIRA, FRAUDE CONTÁBIL E CORRUPÇÃO"),
    ("pt", "Empresa é denunciada na CVM por inadimplência de CRI"),
]
for lang, t in CASOS_NAO_CONSUMADOS:
    f = fase(t)
    check(f["event_phase"] in sa.FASES_NAO_CONSUMADAS,
          f"[{lang}] fase não consumada detectada ({f['event_phase'] or 'NENHUMA'}): {t[:56]}")

print()
print("=" * 96)
print("BLOCO B — fatos CONSUMADOS continuam consumados (não podem virar 'alegação')")
print("=" * 96)
CASOS_CONSUMADOS = [
    ("pt", "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC", "condenacao"),
    ("en", "Former executive pleaded guilty to securities fraud", "condenacao"),
    ("en", "Executive found liable for accounting fraud", "condenacao"),
    ("es", "Tribunal declarado culpable al exdirectivo por fraude", "condenacao"),
    ("en", "Prosecutors charged the company with criminal charges over bribery", "acusacao_formal"),
]
for lang, t, esperado in CASOS_CONSUMADOS:
    f = fase(t)
    check(f["event_phase"] == esperado and f["event_phase"] not in sa.FASES_NAO_CONSUMADAS,
          f"[{lang}] fase consumada '{esperado}' (obtido '{f['event_phase']}'): {t[:52]}")

print()
print("=" * 96)
print("BLOCO C — desfechos mitigadores continuam mitigadores (pt/en/es)")
print("=" * 96)
CASOS_MITIG = [
    ("en", "CVS Health Defeats Whistleblower's Drug Collusion Fraud Suit"),
    ("en", "Judge throws out fraud claims against the bank"),
    ("en", "Company settles with regulator over disclosure case"),
    ("es", "Tribunal absuelto el exdirectivo tras el proceso"),
    ("pt", "Empresa faz acordo judicial e encerra processo"),
]
for lang, t in CASOS_MITIG:
    f = fase(t)
    check(f["direction"] == "mitigadora",
          f"[{lang}] direção mitigadora (obtido '{f['direction']}'): {t[:56]}")

print()
print("=" * 96)
print("BLOCO D — a fase não consumada TRAVA o scoring (falha #2 da auditoria)")
print("=" * 96)
cfg = rd.load_config("config_risco.yaml")


def _pontua(title, company, summary=""):
    """Roda a cadeia REAL (mesma de --reclassify-only) e devolve os event_ids
    que efetivamente pontuam para a empresa."""
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    return set(rd.event_ids_for(rec, company) or []), rec


ids, rec = _pontua("2 of Michigan's largest health systems sue CVS Health alleging fraud "
                   "in drug pricing program", "CVS Health")
check("fraude" not in ids, "[CVS/auditoria] 'sue … alleging fraud' NÃO pontua fraude")

ids, _ = _pontua("JPMorgan Chase (NYSE:JPM) fraud-claim scrutiny weighs on stock at "
                 "analysts' target", "JPMorgan Chase")
check("fraude" not in ids, "[JPMorgan/auditoria] 'fraud-claim scrutiny' NÃO pontua fraude")

ids, _ = _pontua("ACC probes Tk3 lakh crore fraud allegations against BAT",
                 "British American Tobacco")
check("fraude" not in ids, "[BAT/auditoria] 'probes … allegations' NÃO pontua fraude")

ids, _ = _pontua("Prudential Financial's Japan Operations Suspected Of Fraud At "
                 "Gibraltar Life Unit, Nikkei reports", "Prudential Financial")
check("fraude" not in ids, "[Prudential/auditoria] 'suspected of fraud' NÃO pontua fraude")

ids, _ = _pontua("AEGEA SOB MAIS UMA SUSPEITA: CRISE FINANCEIRA, FRAUDE CONTÁBIL E "
                 "CORRUPÇÃO COLOCAM EM XEQUE ENTREGA DO DMAE", "Aegea Saneamento")
check("fraude" not in ids, "[Aegea/auditoria] 'sob suspeita' NÃO pontua fraude")

ids, _ = _pontua("CVS Health Defeats Whistleblower's Drug Collusion Fraud Suit", "CVS Health")
check("fraude" not in ids, "[CVS/auditoria] desfecho favorável ('Defeats') NÃO pontua fraude")

print()
print("=" * 96)
print("BLOCO E — NÃO REGRESSÃO: fraude comprovada continua pontuando")
print("=" * 96)
ids, _ = _pontua("Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC",
                 "TIM Brasil")
check("fraude" in ids, "[TIM/auditoria] condenação real CONTINUA pontuando fraude")

ids, _ = _pontua("Operação da PF prende diretores da empresa por fraude contábil comprovada; "
                 "Justiça condena os responsáveis", "Vale")
check("fraude" in ids, "[sintético] fraude com condenação continua pontuando")

print()
print("=" * 96)
print("BLOCO F — invariantes de peso/taxonomia intocadas")
print("=" * 96)
_fr = next((e for e in cfg["taxonomy"] if e["id"] == "fraude"), None)
check(_fr and _fr["score"] == 90, "peso-base de 'fraude' continua 90 (nenhum peso alterado)")
check("acusacao_civil" not in {e["id"] for e in cfg["taxonomy"]},
      "nenhuma taxonomia nova criada — 'acusacao_civil' é FASE, não evento (§7)")
check(sa.FASES_NAO_CONSUMADAS == frozenset({"alegacao", "acusacao_civil", "investigacao"}),
      "conjunto de fases não consumadas é explícito e fechado")

print()
print("=" * 96)
print(f"RESULTADO WAVE A1 (fase jurídica): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
