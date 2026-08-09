#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b3a_b3_praca.py — 4I.2 Wave B7b-3a.

B3 como PRAÇA de negociação: operação de terceiro que ocorre/é negociada
NA bolsa não é operação DA bolsa. Solução CONFIG-ONLY, via um
`mention_guard.contexto_patterns` novo na entrada da B3 — mecanismo que a
B3 já usava para "índice da B3".

As asserções usam `detect_companies` (camada real de atribuição). O gold
semeado não é usado aqui porque, quando a empresa é corretamente removida,
`event_ids_for` cai no fallback legado (artefato F1 já caracterizado).
"""
from __future__ import annotations
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


def det(t):
    return set(rd.detect_companies({"title": t, "summary": ""}, cfg["watchlist"]))


print("=" * 96)
print("BLOCO A — FP real e estruturas equivalentes de praça (§15.1/§15.3)")
print("=" * 96)
check("B3" not in det("Banco Mercantil avalia novo follow-on na B3 em março"),
      "[1 FP real] 'follow-on na B3' NÃO atribui B3")
check("B3" not in det("Banco X fará follow-on na B3"),
      "[3a] 'follow-on na B3' (genérico) NÃO atribui B3")
check("B3" not in det("Oferta de ações do Banco X será negociada na B3"),
      "[3b] 'oferta … negociada na B3' NÃO atribui B3")
check("B3" not in det("IPO da companhia terá estreia na B3 em abril"),
      "[3c] 'estreia na B3' NÃO atribui B3")

print()
print("=" * 96)
print("BLOCO B — §6/§15.2: B3 como SUJEITO real continua atribuída")
print("=" * 96)
for _t in ("Christian Egan é escolhido como novo CEO da B3",
           "B3 anuncia novo CEO",
           "B3 divulga resultado do trimestre",
           "B3 comunica fato relevante ao mercado",
           "B3 aprova programa de recompra de ações próprias"):
    check("B3" in det(_t), f"[TRUE] B3 sujeito preservada: {_t[:52]}")

print()
print("=" * 96)
print("BLOCO C — §8: emissor terceiro preservado, artigo não é rejeitado")
print("=" * 96)
_T = "Banco Mercantil avalia novo follow-on na B3 em março"
check("B3" not in det(_T), "[4] B3 perde só a atribuição lateral")
check(det("Santander Brasil fará follow-on na B3") == {"Santander Brasil"},
      "[5] emissor terceiro monitorado MANTÉM a atribuição; só a B3 sai")

print()
print("=" * 96)
print("BLOCO D — §15.6: guards antigos da B3 continuam funcionando")
print("=" * 96)
check("B3" not in det("Empresas listadas no pregão da B3 sofrem queda"),
      "[6a] guard 'pregão da B3' preservado")
check("B3" not in det("Ações negociadas na bolsa da B3 caem"),
      "[6b] guard de bolsa/praça preservado")

print()
print("=" * 96)
print("BLOCO E — §10/§15.7: digest Usiminas/B3 NÃO é capturado")
print("=" * 96)
_DIG = ("Ibovespa hoje: Usiminas (USIM5) lidera altas; B3 (B3SA3) cai quase 5% após "
        "anúncio de novo CEO")
check("B3" in det(_DIG),
      "[7] digest de CEO continua atribuindo B3 — cluster separado, NÃO resolvido aqui")
check("Usiminas" in det(_DIG),
      "[7b] Usiminas também continua — nada foi alterado nesse residual")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-3a: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
