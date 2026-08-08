#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_gold_4h3f.py — 4H.3F §10: gold set manual, curado a partir de filings
REAIS já coletados no corpus local (não construído para favorecer o algoritmo).

Cada caso é um accession + item + evento + resultado esperado + evidência real
+ razão. Metade são falsos positivos JÁ CONHECIDOS de fases anteriores (a
prova de que eles não voltam); metade são fatos verdadeiros ou plausíveis
verificáveis no próprio filing (a prova de recall).
"""
from __future__ import annotations

GOLD_CASES = [
    # ───────────────────────── falsos positivos conhecidos ────────────────
    {
        "id": "fp01_nextera_share_issuance",
        "accession": "0001104659-26-063001", "form": "8-K",
        "emissor": "NextEra Energy", "item": "1.01", "event_family": "troca_ceo",
        "expected_result": "nao_scoreable",
        "evidence_hint": "recommend that NextEra Energy's shareholders approve the Share Issuance",
        "reason": ("cláusula de governança de M&A (Dominion Energy) mencionando "
                  "Board/aprovação de acionistas — não é troca de executivo. "
                  "Falso positivo do run 31206358785 (4H.3E)."),
    },
    {
        "id": "fp02_truist_capa_referencia_cruzada",
        "accession": "0001193125-26-226701", "form": "8-K",
        "emissor": "Truist Financial", "item": "5.03", "event_family": None,
        "expected_result": "nao_scoreable",
        "evidence_hint": "as defined in Item 5.03 below",
        "reason": ("referência cruzada no meio de frase, não heading — origem "
                  "do bug estrutural que abriu a 4H.3F (a seção 'engolia' capa "
                  "e assinatura no texto achatado)."),
    },
    {
        "id": "fp03_nextera_hidden_coregistrant",
        "accession": "0001104659-26-062992", "form": "8-K",
        "emissor": "NextEra Energy", "item": "5.02", "event_family": "troca_ceo",
        "expected_result": "scoreable_via_dom",
        "evidence_hint": "Armando Pimentel, Jr., Chief Executive Officer of F[PL] ... "
                         "Scott Bores ... will succeed Mr. Pimentel as Chief Executive Officer",
        "reason": ("ESTE É O CASO INVERTIDO: a evidência antiga era 'Co-Registrant "
                  "City Juno Beach', de uma tabela display:none — falso positivo "
                  "estrutural. O 5.02 real é uma troca de CEO GENUÍNA (Pimentel → "
                  "Bores, presidente da FPL). Esperado: DOM recupera o TP real que "
                  "o texto achatado mascarava atrás de conteúdo oculto."),
    },
    {
        "id": "fp04_truist_diretor_nao_ceo",
        "accession": "0000092230-26-000066", "form": "8-K",
        "emissor": "Truist Financial", "item": "5.02", "event_family": "troca_ceo",
        "expected_result": "nao_scoreable",
        "evidence_hint": "appointed Catherine P. Bessant as a director of the Company",
        "reason": ("nomeação de DIRETOR (conselho), não de CEO/CFO — §8: "
                  "'Diretor, compensação ou CODM não viram CEO change'."),
    },
    {
        "id": "fp05_halliburton_codm_10q",
        "accession": None, "form": "10-Q",
        "emissor": "Halliburton", "item": "", "event_family": "troca_ceo",
        "expected_result": "nao_scoreable",
        "evidence_hint": "our chief operating decision maker (CODM) is Jeffrey Miller",
        "reason": ("citação de CODM em relatório periódico — regressão canônica "
                  "da 4H.3C. Formulário periódico nunca pontua (defesa mantida)."),
    },
    {
        "id": "fp06_item3_padrao_10q",
        "accession": None, "form": "10-Q",
        "emissor": "(genérico)", "item": "", "event_family": "default",
        "expected_result": "nao_scoreable",
        "evidence_hint": "Item 3. Defaults Upon Senior Securities",
        "reason": ("título de seção padrão de todo 10-Q, não um default real — "
                  "regressão canônica da 4H.3D."),
    },
    # ───────────────────────── verdadeiros/plausíveis ──────────────────────
    {
        "id": "tp01_baker_hughes_chart_industries",
        "accession": "0001193125-26-305477", "form": "8-K",
        "emissor": "Baker Hughes", "item": "2.01", "event_family": "ma",
        "expected_result": "scoreable_via_dom",
        "evidence_hint": "each share of common stock of Chart ... converted into "
                         "the right to receive $210.00 in cash",
        "reason": ("conclusão real de aquisição (Chart Industries), mecânica de "
                  "contraprestação padrão DGCL — o caso-âncora explícito do §10."),
    },
    {
        "id": "tp02_baker_hughes_term_loan",
        "accession": "0001193125-26-305477", "form": "8-K",
        "emissor": "Baker Hughes", "item": "1.01", "event_family": "emissao_divida",
        "expected_result": "scoreable_via_dom",
        "evidence_hint": "entered into (i) a term loan credit agreement",
        "reason": "nova obrigação financeira real, fato consumado ('entered into').",
    },
    {
        "id": "tp03_truist_ceo_retirement",
        "accession": "0001193125-26-270320", "form": "8-K",
        "emissor": "Truist Financial", "item": "5.02", "event_family": "troca_ceo",
        "expected_result": "scoreable_via_dom",
        "evidence_hint": "William H. Rogers, Jr. will retire as Chief Executive "
                         "Officer (“CEO”) and President",
        "reason": ("troca de CEO real e explícita — corresponde à notícia "
                  "'Truist Names New CEO' já vista em corroboração na 4H.3C."),
    },
    {
        "id": "tp04_energy_transfer_underwriting",
        "accession": "0001193125-26-298149", "form": "8-K",
        "emissor": "Energy Transfer", "item": "1.01", "event_family": "emissao_divida",
        "expected_result": "scoreable_via_dom",
        "evidence_hint": "underwriting agreement ... with Citigroup Global Markets "
                         "Inc., J.P. Morgan Securities LLC, PNC Capital Markets LLC",
        "reason": ("emissão/oferta real com sindicato de bancos nomeado — caso "
                  "plausível já identificado na 4H.3E, agora com item_dom."),
    },
]


def by_id(case_id: str) -> dict | None:
    return next((c for c in GOLD_CASES if c["id"] == case_id), None)
