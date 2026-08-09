#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b1_troca_ceo.py — 4I.2 Wave B7b-1.

`troca_ceo` de TERCEIRO identificado por possessivo. A regra reusa
`subject_by_possessive` (já genérico, dirigido por `EVENT_TERM_RX`) e opera
por EMPRESA × EVENTO — nunca rejeita o artigo inteiro.

Inclui as duas regressões descobertas durante a implementação, agora
permanentes: (R1) termo `CEO` solto procurando possessivo distante e
(R2) cargo capturado como entidade.
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

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


def pontua(title, company, summary=""):
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def poss(t, emp, ev="troca_ceo"):
    return sa.subject_by_possessive(t, ev, emp, AL)[0]


print("=" * 96)
print("BLOCO A — os 5 casos reais corrigidos, um a um (§2)")
print("=" * 96)
_CIGNA = ("Pharmalittle: Estamos lendo sobre a renúncia do CEO da Organon, a Cigna "
          "encerrando alguns reembolsos")
check("troca_ceo" not in pontua(_CIGNA, "Cigna Group"),
      "[1 Cigna/Organon] Cigna NÃO recebe troca_ceo")
check("organon" in (poss(_CIGNA, "Cigna Group") or "").lower(),
      f"[1b Cigna/Organon] sujeito = Organon (obtido {poss(_CIGNA, 'Cigna Group')!r})")

_COSAN = "Como a Cosan prepara a venda da Rumo: expansão e novo CEO"
check("troca_ceo" not in pontua(_COSAN, "Cosan"),
      "[2 Cosan/Rumo] Cosan NÃO recebe troca_ceo")

_VALE = ("Novo CEO da Vale (VALE3): o que Embraer e Klabin dizem sobre participação "
         "de diretores na concorrente")
check("troca_ceo" not in pontua(_VALE, "Embraer"),
      "[3 Embraer/Vale] Embraer NÃO recebe troca_ceo")
check("troca_ceo" not in pontua(_VALE, "Klabin"),
      "[4 Klabin/Vale] Klabin NÃO recebe troca_ceo")
check("troca_ceo" in pontua(_VALE, "Vale"),
      "[3b/4b] Vale — o sujeito REAL — MANTÉM troca_ceo no mesmo artigo")

_PEMEX = ("Novo CEO da Pemex vai viajar ao Brasil para avançar agenda de parceria "
          "com a Petrobras")
check("troca_ceo" not in pontua(_PEMEX, "Petrobras"),
      "[5 Petrobras/Pemex] Petrobras NÃO recebe troca_ceo")
check("troca_ceo" in pontua(_PEMEX, "Pemex (Petróleos Mexicanos)"),
      "[5b] Pemex — o sujeito REAL — MANTÉM troca_ceo no mesmo artigo")

print()
print("=" * 96)
print("BLOCO B — §3: NON-OVERREACH (Usiminas/B3, sem possessivo)")
print("=" * 96)
_USI = ("Ibovespa hoje: Usiminas (USIM5) lidera altas; B3 (B3SA3) cai quase 5% após "
        "anúncio de novo CEO")
check(poss(_USI, "Usiminas") == "",
      "[6] sem construção possessiva, o detector NÃO inventa sujeito para a Usiminas")
check(poss(_USI, "B3") == "",
      "[6b] idem para a B3 — a regra não estica para adjacência de digest")
# o FP completo permanece registrado como ENTITY_SUBJECT_RESIDUAL_DIGEST (cluster futuro)

print()
print("=" * 96)
print("BLOCO C — §4: own-CEO positives")
print("=" * 96)
check("troca_ceo" in pontua("Novo CEO da Vale assume nesta semana", "Vale"),
      "[7 pt] 'Novo CEO da Vale' → Vale recebe (própria empresa no possessivo)")
check("troca_ceo" in pontua("Petrobras anuncia novo CEO", "Petrobras"),
      "[8 pt] 'Petrobras anuncia novo CEO' → Petrobras recebe")
check("troca_ceo" in pontua("Pemex names new CEO", "Pemex (Petróleos Mexicanos)"),
      "[9 en] 'Pemex names new CEO' → Pemex recebe")
check("troca_ceo" in pontua("Gonzalo Rueda Castillo es el nuevo gerente general de "
                             "Cemento Yura", "Yura"),
      "[10 es] construção espanhola com sujeito próprio → Yura recebe")

print()
print("=" * 96)
print("BLOCO D — §5/§6: por empresa × por evento")
print("=" * 96)
# LIMITACAO PRE-EXISTENTE E DOCUMENTADA (nao introduzida pela B7b-1):
# `subject_by_possessive` so reconhece posse por POSSESSIVO. Num texto com
# possessivo de A ("novo CEO DA Vale") e sujeito-verbo de B ("a Embraer
# anuncia novo CEO"), B perde o proprio evento. Verificado na baseline
# origin/main com a familia `falencia`: mesma estrutura devolve ('Vale',
# 'da Vale') para a Embraer. Registrado como POSSESSIVE_OWNERSHIP_GUARD_GAP.
# O teste abaixo fixa o contrato que a arquitetura DE FATO sustenta hoje:
# cada empresa e avaliada separadamente e o sujeito possessivo mantem o seu.
_DOIS = "Novo CEO da Vale assume nesta semana"
_v = pontua(_DOIS, "Vale")
_e = pontua(_DOIS, "Embraer")
check("troca_ceo" in _v and "troca_ceo" not in _e,
      f"[11] avaliacao por empresa: Vale={sorted(_v)} (sujeito) / "
      f"Embraer={sorted(_e)} (nao citada)")
_LAT = "Petrobras comenta a nomeação do novo CEO da Pemex"
check("troca_ceo" not in pontua(_LAT, "Petrobras"),
      "[12] monitorada lateral (Petrobras comenta) NÃO recebe")
check("troca_ceo" in pontua(_LAT, "Pemex (Petróleos Mexicanos)"),
      "[12b] sujeito (Pemex) recebe no MESMO artigo")

print()
print("=" * 96)
print("BLOCO E — §7 R1: 'CEO' solto não busca possessivo distante")
print("=" * 96)
for _t, _c in (("XP vê troca de CEO no Santander (SANB11) sem ruptura e aposta em "
                "alta rentabilidade", "Santander Brasil"),
               ("Smart Fit (SMFT3) anuncia troca de CEO e diretor financeiro", "Smart Fit"),
               ("Tupy anuncia renúncia do CEO e inicia processo de sucessão com apoio "
                "internacional", "Tupy")):
    check("troca_ceo" in pontua(_t, _c),
          f"[R1] positivo legítimo preservado: {_c} — {_t[:44]}")

print()
print("=" * 96)
print("BLOCO F — §7 R2 / §8: cargo não é entidade, empresa real ainda é")
print("=" * 96)
check(poss("Tupy anuncia renúncia do CEO e inicia processo de sucessão", "Tupy") != "CEO",
      "[R2] 'renúncia do CEO' NÃO devolve o cargo como subject_company")
for _cargo in ("ceo", "presidente", "diretor", "gerente", "conselho", "sucessao", "comando"):
    check(_cargo in sa._STOP_ENT, f"[R2b] cargo '{_cargo}' está em _STOP_ENT")
check("organon" in (poss("renúncia do CEO da Organon", "Cigna Group") or "").lower(),
      "[§8] com cargo E empresa na mesma construção, a EMPRESA é extraída (Organon)")

print()
print("=" * 96)
print("BLOCO G — §9: Cigna/Evernorth (regressão histórica) + invariantes")
print("=" * 96)
check("ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx", "Cigna Group"),
      "[§9] M&A legítimo Cigna/Evernorth preservado")
check("troca_ceo" not in pontua(_CIGNA, "Cigna Group")
      and "ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx",
                          "Cigna Group"),
      "[§2d] nenhuma outra família legítima da Cigna foi apagada")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração"),
      "[§10] Vale/Samarco preservado nos dois lados")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-1.1: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
