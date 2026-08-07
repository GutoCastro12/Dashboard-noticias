#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_normalizer.py — 4H.3D: normalização semântica SOURCE-AWARE do EDGAR.

Problema medido na 4H.3C (run 31144073562): 122 de 141 eventos (87%) foram
neutralizados pelo `semantic_audit` como `referência histórica
(ano_antigo_citado:1933/1934)`. Causa: todo filing da SEC carrega
`Securities Act of 1933` e `Securities Exchange Act of 1934` no boilerplate
jurídico. Auditoria do corpus real dos 211 filings:

    1934 → 578 ocorrências em 203 dos 211 documentos (96%)
    1933 → 116 ocorrências em  82 documentos
    1995 →  10 ocorrências em  10 documentos
            ("Private Securities Litigation Reform Act of 1995" — MESMO padrão
             estrutural, outro ano: por isso a regra é por CONTEXTO, nunca uma
             blacklist de anos)
    2026 → 10.512 ocorrências, quase todas XBRL cru
            ("us-gaap:ValuationTechniqueOptionPricingModelMember 2026-06-30 …")

DECISÕES DE ARQUITETURA

1. O texto bruto NUNCA é alterado. `raw_document_text` continua íntegro para
   auditoria, evidência, compliance e reconstrução. A normalização produz um
   `semantic_text` derivado.

2. A neutralização preenche com ESPAÇOS, preservando o comprimento. Assim todo
   offset achado em `semantic_text` é válido em `raw_document_text` — a
   evidência exibida ao usuário sai sempre do bruto.

3. Só se aplica a documentos cuja proveniência é EDGAR/SEC. Google News, RI e
   CVM não passam por aqui, e o `semantic_audit` global fica intocado.
"""
from __future__ import annotations

import re

PROVENANCE_EDGAR = ("EDGAR", "SEC")

# ── blocos estruturais, por classe ───────────────────────────────────────────
# Cada padrão é (nome, regex, classe). A classe alimenta o inventário exigido
# em §5 e explica no CSV por que o trecho saiu.
BOILERPLATE_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # -- referência legislativa: QUALQUER "<Alguma Coisa> Act of <ano>" --------
    # ATENÇÃO: usar [ \t] em vez de \s dentro de grupos repetidos. Normalizar
    # um texto JÁ normalizado o deixa com runs enormes de espaços, e \s+ sob
    # repetição vira backtracking quadrático — isso travou o teste de
    # idempotência da 4H.3D.
    ("ato_legislativo", re.compile(
        r"\b(?:the[ \t]+)?(?:U\.?S\.?[ \t]+)?[A-Z][A-Za-z']*(?:[ \t]+[A-Z][A-Za-z']*){0,5}[ \t]+"
        r"Act[ \t]+of[ \t]+(?:18|19|20)\d{2}\b", re.M), "legal_boilerplate"),
    ("ato_legislativo_abrev", re.compile(
        r"\b(?:Securities\s+Act|Exchange\s+Act|Investment\s+Company\s+Act|"
        r"Sarbanes[-\s]?Oxley|Dodd[-\s]?Frank)\b", re.I), "legal_boilerplate"),
    # variante em CAIXA ALTA do cabeçalho de formulário: o padrão acima exige
    # inicial maiúscula seguida de minúsculas e não pega "ACT OF 1934".
    ("ato_legislativo_caps", re.compile(
        r"\bSECURITIES\s+(?:EXCHANGE\s+)?(?:ACT\s+)?OF\s+(?:18|19|20)\d{2}\b"),
     "legal_boilerplate"),
    # -- cabeçalho padrão do formulário (capa de 6-K/10-Q/10-K/8-K) -----------
    ("cabecalho_formulario", re.compile(
        r"\b(?:ANNUAL|QUARTERLY|TRANSITION|CURRENT)\s+REPORT\s+PURSUANT\s+TO\b"
        r"[^\n]{0,220}", re.I), "structural"),
    ("cabecalho_6k", re.compile(
        r"\bREPORT\s+OF\s+FOREIGN\s+PRIVATE\s+ISSUER\b[^\n]{0,220}", re.I),
     "structural"),
    ("periodo_capa", re.compile(
        r"\bFor\s+the\s+(?:month|quarterly\s+period|fiscal\s+year|transition\s+period)"
        r"[^\n]{0,80}", re.I), "structural"),
    ("commission_file", re.compile(
        r"\bCommission\s+File\s+(?:Number|No\.?)[^\n]{0,60}", re.I), "structural"),
    # -- citação de regra / seção / CFR ---------------------------------------
    ("regra_sec", re.compile(
        r"\bRule\s+\d+[A-Za-z]?[\d\-]*(?:\([a-z0-9]\))*(?:\s+of\s+this\s+chapter)?",
        re.I), "legal_boilerplate"),
    # Exige marcador § ou "17 CFR": prefixos TODOS opcionais seguidos de \s*
    # adjacentes tornam o casamento ambíguo e quadrático em texto cheio de
    # espaços (o caso do texto já normalizado).
    ("secao_cfr", re.compile(
        r"(?:§+|17[ \t]+CFR[ \t]+§?)[ \t]*\d{3}\.\d+[a-z0-9\-]*"
        r"(?:[ \t]+of[ \t]+this[ \t]+chapter)?", re.I), "legal_boilerplate"),
    ("pursuant_to", re.compile(
        r"\bPursuant\s+to\s+(?:the\s+)?(?:requirements?|provisions?|Section|Rule|General\s+Instruction)"
        r"[^.]{0,180}\.", re.I | re.S), "legal_boilerplate"),
    ("secao_lei", re.compile(
        r"\bSection\s+\d+[A-Za-z]?(?:\([a-z0-9]\))*\s+of\s+the\b[^.]{0,90}\.",
        re.I), "legal_boilerplate"),
    # -- bloco de assinatura ---------------------------------------------------
    ("bloco_assinatura", re.compile(
        r"\bSIGNATURES?\b.{0,900}?(?:duly\s+caused\s+this\s+(?:report|document)"
        r"[^.]{0,200}\.)", re.I | re.S), "structural"),
    # sem \b antes de "/s/": '/' não é caractere de palavra, então \b falha
    # justamente no caso normal (precedido de espaço).
    ("linha_assinatura", re.compile(
        r"(?:By:[ \t]*)?/s/[ \t]*[A-Z][A-Za-z.\-]+(?:[ \t]+[A-Z][A-Za-z.\-]+){0,3}",
        re.M), "structural"),
    # -- safe harbor / forward-looking ----------------------------------------
    ("safe_harbor", re.compile(
        r"\b(?:forward[-\s]looking\s+statements?|safe\s+harbor)\b[^.]{0,240}\.",
        re.I | re.S), "legal_boilerplate"),
    # -- XBRL cru: prefixo:Tag, CIK de 10 dígitos, datas ISO soltas ------------
    ("xbrl_tag", re.compile(
        r"\b[a-z][a-z0-9\-]{1,9}:[A-Za-z][A-Za-z0-9_]{2,}\b"), "structural"),
    ("xbrl_cik", re.compile(r"\b000\d{7}\b"), "structural"),
    ("xbrl_iso_data", re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"), "structural"),
    # -- capa: tabela de valores mobiliários registrados -----------------------
    ("capa_securities", re.compile(
        r"\bTitle\s+of\s+each\s+class\b.{0,1600}?"
        r"(?:Name\s+of\s+each\s+exchange[^\n]{0,120})", re.I | re.S),
     "structural"),
    ("notes_due", re.compile(
        r"\b\d+(?:\.\d+)?[ \t]*%[ \t]+(?:(?:Senior|Subordinated|Callable|Fixed|Rate)[ \t]+){0,4}"
        r"Notes?[ \t]+[Dd]ue[ \t]+(?:[A-Z][a-z]+[ \t]+\d{1,2},?[ \t]*)?(?:19|20)\d{2}",
     ), "structural"),
    # -- índice / sumário / cabeçalho / rodapé --------------------------------
    ("indice", re.compile(
        r"\bTABLE\s+OF\s+CONTENTS\b.{0,2500}?(?=\n\s*(?:PART|Item)\b|\Z)",
        re.I | re.S), "structural"),
    ("indice_itens", re.compile(
        r"(?:\bItem[ \t]*\d+[A-Za-z]?\.?[ \t]+[A-Z][^\n]{0,70}[ \t]+\d{1,3}[ \t]*){2,}"),
     "structural"),
    ("numero_pagina", re.compile(
        r"\n[ \t]*(?:Page[ \t]+)?\d{1,3}[ \t]*(?=\n)", re.I), "structural"),
    ("cabecalho_sec", re.compile(
        r"UNITED\s+STATES\s+SECURITIES\s+AND\s+EXCHANGE\s+COMMISSION[^\n]{0,120}"
        r"(?:\s*Washington,?\s*D\.?C\.?\s*20549)?", re.I), "structural"),
    ("checkbox_capa", re.compile(
        r"\b(?:Emerging\s+growth\s+company|Large\s+accelerated\s+filer|"
        r"Non-accelerated\s+filer|Smaller\s+reporting\s+company|"
        r"Indicate\s+by\s+check\s+mark)\b[^.]{0,220}\.?", re.I | re.S),
     "legal_boilerplate"),
    # -- certificação formal ---------------------------------------------------
    ("certificacao", re.compile(
        r"\bI,\s+[A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){0,3},\s+certify\s+that\b"
        r".{0,900}?(?=\n\s*(?:Date|/s/)|\Z)", re.I | re.S), "structural"),
    ("incorporado_por_referencia", re.compile(
        r"\bincorporated\s+by\s+reference\b[^.]{0,200}\.", re.I | re.S),
     "legal_boilerplate"),
]

# Padrões que, quando presentes, indicam que um ANO é econômico de verdade e
# não deve ser tocado (§4): "founded in 1933", "since 1933", "in 1933 the
# company acquired…". Usado só pelo inventário/diagnóstico.
_ANO_ECONOMICO = re.compile(
    r"\b(?:founded|established|incorporated|since|began|started|opened|"
    r"acquired|merged|built|fundad\w*|desde)\b[^.]{0,60}\b(?:18|19|20)\d{2}\b"
    r"|\b(?:18|19|20)\d{2}\b[^.]{0,40}\b(?:foundation|founding)\b", re.I)


def is_edgar(provenance: str | None) -> bool:
    """A normalização é SOURCE-AWARE: só documentos EDGAR/SEC passam."""
    return str(provenance or "").strip().upper() in PROVENANCE_EDGAR


def _blank(texto: str, spans: list[tuple[int, int]]) -> str:
    """Neutraliza preservando COMPRIMENTO — offsets continuam válidos no bruto."""
    if not spans:
        return texto
    buf = list(texto)
    for a, b in spans:
        for i in range(max(0, a), min(len(buf), b)):
            if buf[i] != "\n":
                buf[i] = " "
    return "".join(buf)


def normalize_edgar_semantic_text(raw_document_text: str, *,
                                  provenance: str = "EDGAR") -> dict:
    """Produz o `semantic_text` de um documento EDGAR.

    Devolve dict com:
      semantic_text  — texto neutralizado (MESMO comprimento do bruto)
      removed        — blocos removidos, com nome, classe, offsets e amostra
      stats          — contagem por classe e por padrão
      applied        — False se a fonte não for EDGAR (nada é alterado)

    O bruto NUNCA é modificado: `raw_document_text` entra e sai igual.
    """
    raw = str(raw_document_text or "")
    if not is_edgar(provenance):
        return {"semantic_text": raw, "removed": [], "stats": {},
                "applied": False, "motivo": f"fonte '{provenance}' não é EDGAR"}

    spans: list[tuple[int, int]] = []
    removed: list[dict] = []
    por_padrao: dict[str, int] = {}
    por_classe: dict[str, int] = {}

    for nome, rx, classe in BOILERPLATE_PATTERNS:
        n = 0
        for m in rx.finditer(raw):
            a, b = m.start(), m.end()
            if b - a <= 0:
                continue
            spans.append((a, b))
            n += 1
            if len(removed) < 400:      # amostra para auditoria
                removed.append({
                    "padrao": nome, "classe": classe, "start": a, "end": b,
                    "amostra": re.sub(r"\s+", " ", raw[a:b])[:120],
                })
        if n:
            por_padrao[nome] = n
            por_classe[classe] = por_classe.get(classe, 0) + n

    semantic = _blank(raw, spans)
    assert len(semantic) == len(raw), "normalização mudou o comprimento"

    return {
        "semantic_text": semantic,
        "removed": removed,
        "stats": {"por_padrao": por_padrao, "por_classe": por_classe,
                  "chars_bruto": len(raw),
                  "chars_neutralizados": sum(b - a for a, b in spans),
                  "blocos": len(spans)},
        "applied": True,
        "motivo": "",
    }


# ── seções e janelas de evidência (§6) ───────────────────────────────────────
_HEADING = re.compile(
    r"^\s*(Item\s+\d+\.\d{2}[^\n]{0,90}|Item\s+\d+[A-Za-z]?\.[^\n]{0,90}|"
    r"PART\s+[IVX]+[^\n]{0,60}|[A-Z][A-Z &/,'\-]{8,80})\s*$", re.M)


# O texto extraído da SEC muitas vezes vem em linha corrida, sem quebra antes
# do heading — então o padrão ancorado em ^…$ sozinho perde a seção. O marcador
# "Item N.NN" inline é o sinal mais confiável dentro de 8-K/6-K.
_ITEM_INLINE = re.compile(r"\bItem[ \t]+\d+\.\d{2}[^\n]{0,60}")


def section_of(raw: str, pos: int) -> str:
    """Heading da seção que contém `pos` — preservado junto da evidência."""
    t = str(raw or "")
    melhor, melhor_ini = "", -1
    for rx, grupo in ((_HEADING, 1), (_ITEM_INLINE, 0)):
        for m in rx.finditer(t):
            if m.start() > pos:
                break
            if m.start() >= melhor_ini:
                melhor_ini = m.start()
                melhor = re.sub(r"\s+", " ", m.group(grupo)).strip()
    return melhor[:90]


def evidence_window(raw: str, start: int, end: int, *,
                    antes: int = 320, depois: int = 480) -> dict:
    """Janela LOCAL de evidência ao redor da âncora, com heading da seção.

    Classificar a janela em vez dos 30 mil chars do filing inteiro elimina a
    interferência de boilerplate distante — que é o que gerava o falso
    positivo de troca de CEO a partir de divulgação de segmento.
    """
    t = str(raw or "")
    a = max(0, start - antes)
    b = min(len(t), end + depois)
    # não cortar palavra ao meio
    while a > 0 and t[a] not in " \n\t":
        a -= 1
    while b < len(t) and t[b] not in " \n\t":
        b += 1
    trecho = re.sub(r"[ \t]+", " ", t[a:b]).strip()
    return {
        "evidence_text": trecho,
        "evidence_start": start,
        "evidence_end": end,
        "evidence_window_start": a,
        "evidence_window_end": b,
        "evidence_section": section_of(t, start),
        "evidence_source": "raw_document_text",
    }


# ── inventário de anos (§5) ──────────────────────────────────────────────────
_ANO = re.compile(r"\b(18\d{2}|19\d{2}|20[0-4]\d)\b")


def classify_year_context(raw: str, m: re.Match, semantic: str) -> str:
    """Classifica a ocorrência de um ano no documento."""
    a, b = m.start(), m.end()
    jan = raw[max(0, a - 90):min(len(raw), b + 60)]
    if semantic[a:b].strip() == "":
        # o ano foi neutralizado: descobrir por quê
        if re.search(r"Act\s+of\s+\d{4}|Rule\s+\d|CFR|Pursuant", jan, re.I):
            return "legal_boilerplate"
        return "structural"
    if _ANO_ECONOMICO.search(jan):
        return "economic"
    return "potentially_economic"


def year_inventory(raw: str, semantic: str) -> list[dict]:
    """Inventário ano × contexto × seção × classificação × ação."""
    out = []
    for m in _ANO.finditer(raw):
        classe = classify_year_context(raw, m, semantic)
        out.append({
            "ano": m.group(1),
            "contexto": re.sub(r"\s+", " ", raw[max(0, m.start() - 80):m.end() + 50]).strip()[:160],
            "secao": section_of(raw, m.start()),
            "classificacao": classe,
            "acao": ("neutralizado" if classe in ("legal_boilerplate", "structural")
                     else "preservado"),
        })
    return out
