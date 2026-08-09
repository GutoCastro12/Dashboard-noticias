#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_a1b_fase_fora_fraude.py — 4I.2 Wave A1b.

O mínimo de fase que torna um evento pontuável depende do que o event_id
SIGNIFICA (§8). Duas semânticas opostas, deliberadamente separadas:

  `default_cri` (score 80, "Default de CRI na carteira") = fato ECONÔMICO
      consumado. Denúncia/processo/investigação NÃO provam o default (§10/§21).

  `investigacao_regulatoria` (score 30) = o evento É a investigação, e suas
      keywords são atos formais ("CVM abre processo", "busca e apreensão",
      "expediente sancionador"). Exigir condenação mudaria o significado da
      família (§9/§20). Só rumor/alegação sem ato formal não basta.
"""
from __future__ import annotations
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


def pontua(title, company):
    h = {"articles": {"u1": {"title": title, "summary": "", "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


print("=" * 96)
print("BLOCO A — default_cri exige FATO consumado (§10/§21)")
print("=" * 96)
check("default_cri" not in pontua(
        "Vibra é denunciada na CVM por inadimplência de CRI, e risco jurídico acende "
        "alerta no mercado", "Vibra Energia"),
      "[1 Vibra/gold] 'denunciada na CVM por inadimplência de CRI' NÃO pontua default_cri")
check("default_cri" in pontua(
        "CRI da operação entra em default: emissor confirma inadimplência de CRI com "
        "não pagamento da parcela devida", "Vibra Energia"),
      "[2] default de CRI REALMENTE confirmado continua pontuando")
check("default_cri" not in pontua(
        "Investidores movem ação judicial contra a Vibra Energia alegando inadimplência "
        "de CRI", "Vibra Energia"),
      "[3] ação judicial ALEGANDO inadimplência de CRI não pontua default_cri")

print()
print("=" * 96)
print("BLOCO B — investigacao_regulatoria: ato FORMAL pontua (§9/§20)")
print("=" * 96)
check("investigacao_regulatoria" in pontua(
        "Ministerio del Trabajo abre investigación a Quala y Nutresa por presunta "
        "explotación laboral de vendedores informales", "Grupo Nutresa"),
      "[4 Nutresa] regulador ABRE investigação formal → PONTUA "
      "(o evento é a investigação; 'presunta' qualifica a conduta, não o ato)")
check("investigacao_regulatoria" in pontua(
        "Exclusivo: Após consulta de investidor, CVM abre processo sobre apoio da Previ "
        "a candidato ao conselho da Vale", "Vale"),
      "[5] 'CVM abre processo' → ato formal, continua pontuando")
check("investigacao_regulatoria" in pontua(
        "Fraude nas Americanas: executivos de Itaú, Bradesco e Santander são alvo de buscas",
        "Itaú Unibanco"),
      "[6] 'alvo de buscas' (busca e apreensão) → ato formal, continua pontuando")

print()
print("=" * 96)
print("BLOCO C — rumor/alegação SEM ato formal não basta")
print("=" * 96)
check("investigacao_regulatoria" not in pontua(
        "Blog acusa companhia de irregularidades e afirma que haveria suspeita de "
        "conduta indevida", "Vale"),
      "[7] acusação sem ato formal de autoridade NÃO pontua investigação regulatória")

print()
print("=" * 96)
print("BLOCO D — a regra de fraude NÃO foi reaproveitada cegamente (§8)")
print("=" * 96)
check(sa.EVENTOS_CREDITO_EXIGEM_FATO.isdisjoint(sa.EVENTOS_INVESTIGACAO_E_O_PROPRIO_EVENTO),
      "os dois conjuntos são disjuntos — semânticas tratadas separadamente")
check("investigacao_regulatoria" not in sa.EVENTOS_FRAUDE,
      "investigacao_regulatoria NÃO entrou no gate de fraude")
check("default_cri" not in sa.EVENTOS_FRAUDE,
      "default_cri NÃO entrou no gate de fraude")
_t = cfg["taxonomy"]
check(next(e for e in _t if e["id"] == "default_cri")["score"] == 80,
      "peso-base de default_cri inalterado (80)")
check(next(e for e in _t if e["id"] == "investigacao_regulatoria")["score"] == 30,
      "peso-base de investigacao_regulatoria inalterado (30)")

print()
print("=" * 96)
print(f"RESULTADO WAVE A1b: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
