#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_attribution_gold_4i.py — 4I.2: gate COMPLEMENTAR de ATRIBUIÇÃO.

Por que existe (§1/§2): `test_gold_4i.py` mede a camada de RECLASSIFICAÇÃO —
dada uma empresa, o evento deve/não deve pontuar. Ele roda `detect_companies`
de verdade, mas o veredito é lido por `event_ids_for(rec, empresa)`, que tem
um FALLBACK LEGADO: quando o registro não tem `events_by_company` (porque
nenhuma empresa da watchlist foi detectada), devolve a lista GLOBAL de
`event_ids`. Resultado: uma atribuição corretamente REMOVIDA continua
aparecendo como se pontuasse.

Este harness mede o outro eixo, sem semear empresa nenhuma:

    QUEM foi atribuído a QUÊ?

Entrada = title/summary + config real. Saída = `detect_companies`.
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


def det(title, summary=""):
    return set(rd.detect_companies({"title": title, "summary": summary}, cfg["watchlist"]))


# (titulo, empresa, deve_atribuir_direto, rótulo)
CASOS = [
    # ── B1: aliases ambíguos observados ──────────────────────────────────
    ("IBS Energy pede recuperação judicial em meio à pressão no mercado livre",
     "MercadoLibre", False, "B1 FP#1 MercadoLibre (mercado livre de energia)"),
    ("Prefeitura do Rio e BBCE assinam acordo para aquisição de energia do mercado livre",
     "MercadoLibre", False, "B1 FP#2 MercadoLibre (energia do mercado livre)"),
    ("ISA Energia Brasil capta R$ 1 bilhão em debêntures e reforça posição como "
     "“porto seguro” do setor de transmissão", "Porto", False, "B1 FP Porto (idiomático)"),
    ("Zurich Santander Brasil anuncia Alejandro Widder como novo CEO",
     "Santander Brasil", False, "B1 FP Zurich Santander (entidade composta)"),
    ("Mercado Livre anuncia nova aquisição no Brasil",
     "MercadoLibre", True, "B1 TRUE MercadoLibre (marca)"),
    ("[Fato Relevante] Porto: Aquisição de Ações de Emissão da Própria Companhia",
     "Porto", True, "B1 TRUE Porto (fato relevante)"),
    ("Santander Brasil anuncia troca de CEO",
     "Santander Brasil", True, "B1 TRUE Santander Brasil"),
    ("Cade aprova aquisição pelo Santander de fatia da Estapar na Loop",
     "Santander Brasil", True, "B1 TRUE Santander (alias curto legítimo)"),
    # ── B2/B3/B4/B5/B6: a EMPRESA continua detectada; quem muda é o SUJEITO ──
    ("CVS Health’s Omnicare files for Chapter 11 bankruptcy",
     "CVS Health", True, "B2 CVS detectada (sujeito tratado na semântica)"),
    ("CVS Health files for Chapter 11 bankruptcy",
     "CVS Health", True, "B2 CVS própria detectada"),
    ("Vazamento sobre calote de R$ 3,6 bi do Banco do Brasil está na mira da CVM",
     "Banco do Brasil", True, "B3 BB detectada (papel tratado na semântica)"),
    ("Detuvo Grupo México sus plataformas petroleras por impago de Pemex",
     "Pemex (Petróleos Mexicanos)", True, "B3 Pemex (devedor) detectada"),
    ("Truist Bank warns customers about phishing, check fraud and text scams",
     "Truist Financial", True, "B4 Truist detectada (papel vítima na semântica)"),
    ("Apoyo de EEUU en litigio YPF impulsa avances en acuerdos millonarios por "
     "default argentino", "YPF", True, "B5 YPF detectada (soberano na semântica)"),
    ("Banorte, Banamex e Inbursa disparan créditos con mayor riesgo de impago y "
     "Moody's lanza alerta", "Grupo Financiero Banorte", True,
     "B6 Banorte detectada (carteira na semântica)"),
    # ── invariantes canônicas de atribuição ──────────────────────────────
    ("Vale informa sobre Plano de Recuperação Judicial da Samarco",
     "Vale", True, "INV Vale detectada"),
    ("Vale informa sobre Plano de Recuperação Judicial da Samarco",
     "Samarco Mineração", True, "INV Samarco detectada"),
    ("A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo "
     "BTG Pactual", "BTG Pactual", True, "INV BTG detectada"),
    ("Empresas listadas no pregão da B3 sofrem queda", "B3", False,
     "INV mention_guard preexistente da B3 (pregão da B3)"),
    ("B3 anuncia novo CEO", "B3", True, "INV B3 como sujeito"),
]

print("=" * 100)
print("ATTRIBUTION GOLD 4I — quem foi atribuído a quê (detect_companies, sem semear)")
print("=" * 100)
tp = fp_ok = 0
for titulo, emp, esperado, rot in CASOS:
    got = emp in det(titulo)
    ok = (got == esperado)
    if ok and esperado:
        tp += 1
    if ok and not esperado:
        fp_ok += 1
    check(ok, f"[{'DIRETO' if esperado else 'NAO-DIRETO'}] {rot}"
               f"{'' if ok else f' (obtido atribuido={got})'}")

print()
print("=" * 100)
print(f"DIRECT ATTRIBUTION POSITIVES preservados : {tp}/{sum(1 for c in CASOS if c[2])}")
print(f"FALSE ATTRIBUTIONS corrigidas            : {fp_ok}/{sum(1 for c in CASOS if not c[2])}")
print(f"RESULTADO ATTRIBUTION GOLD: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    import sys
    sys.exit(1)
