#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_sections.py — 4H.3E: evidência ESCOPADA POR SEÇÃO do filing.

Diagnóstico que motiva a fase (run 31193786617): a classificação varria o
documento inteiro e encontrava vocabulário econômico em lugares irrelevantes.
Dos 37 candidatos pontuáveis, os 4 originados de `item_8k` eram bons e
praticamente todo falso positivo veio de `texto_do_documento` — capa, bloco de
assinatura, sumário, fluxo de caixa, biografia de executivo.

A unidade de evidência passa a ser:

    filing → item/seção → janela de evidência → candidato

Medição do corpus real (211 filings) que define a estratégia por formulário:

    8-K   79 docs, 61 (77%) com marcador "Item N.NN"  → escopo por ITEM
    6-K  113 docs,  0 com item (não existe no formulário) → escopo por
                    estrutura de press release: linha "Ref.:", manchete,
                    dateline e lista de "Contents"
    10-Q  19 docs — já não prova fato novo (4H.3C), sem seção econômica

Sinal real encontrado nos 6-K, que a varredura livre ignorava:
    Nubank  "Nubank to Add a Banking License in Brazil through the Acquisition
             of Banco Porto Real" + "São Paulo, July 20, 2026 – … announced
             today that it has entered into a share purchase agreement"
    YPF     "Ref.: Material Event – Portfolio Optimization Strategy: …"
    Cemex   "Contents 1. Press release dated July 23, 2026 announcing …"

O `raw_document_text` continua íntegro; as seções carregam offsets para ele.
"""
from __future__ import annotations

import re

# Formulários periódicos não têm seção econômica pontuável (defesa da 4H.3C).
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})

# ── 8-K: marcadores de item ──────────────────────────────────────────────────
# O heading não pode atravessar o próximo "Item N.NN": num 8-K extraído em
# linha corrida, `[^\n]{0,90}` engolia o marcador seguinte e o item sumia,
# porque finditer não sobrepõe matches.
_ITEM_MARK = re.compile(
    r"\bItem[ \t]+(\d+\.\d{2})[ \t]*\.?[ \t]*"
    r"((?:(?!\bItem[ \t]+\d+\.\d{2})[^\n]){0,90})")

# ── 6-K: estrutura de press release ──────────────────────────────────────────
# "Ref.: Material Event – ..." é a linha de assunto dos ofícios latino-americanos
_REF_LINE = re.compile(
    r"\b(?:Ref\.?|Re\.?|Subject|Asunto|Assunto)[ \t]*:[ \t]*([^\n]{8,220})", re.I)
# dateline clássico de press release: "São Paulo, July 20, 2026 – Company ..."
# O corpo do release quebra linha várias vezes ("Nu Holdings Ltd. (NYSE:\nNU)"),
# então o trecho capturado precisa atravessar \n — com [^\n] o dateline do
# Nubank não casava e o release inteiro ficava sem seção.
_DATELINE = re.compile(
    r"([A-Z][A-Za-zÀ-ÿ.\- ]{2,30},[ \t\n]+(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December)[ \t\n]+\d{1,2},[ \t\n]*\d{4}"
    r"[ \t\n]*[–—\-][ \t\n]*)((?s:.{20,600}))")
# entradas da lista "Contents 1. Press release dated ... announcing ..."
_CONTENTS = re.compile(
    r"\b(?:Contents|INDEX\s+TO\s+FURNISHED\s+MATERIAL|The\s+following\s+exhibits?\s+"
    r"(?:is|are)\s+attached)\b[ \t]*:?[ \t]*((?:\s*\d+\.[^\n]{10,220}){1,6})", re.I)
# manchete: linha com muitas palavras capitalizadas, típica de título de release
_MANCHETE = re.compile(
    r"(?m)^[ \t]*((?:[A-Z][A-Za-zÀ-ÿ'’\-]+[ \t]+){3,18}"
    r"(?:[A-Z][A-Za-zÀ-ÿ'’\-]+))[ \t]*$")

# Palavras que denunciam manchete/assunto meramente administrativo.
_TITULO_ADMIN = re.compile(
    r"\b(?:table\s+of\s+contents|exhibit\s+index|signature|financial\s+statements|"
    r"index\s+to|forward[-\s]looking|address\s+of\s+principal|translation\s+of)\b", re.I)

MAX_SECAO = 6000        # teto por seção; releases longos viram várias janelas

# Só a seção cuja ESTRUTURA é garantida por regra da SEC pode sustentar evento
# pontuável. Medido no corpus (211 filings): `headline` deu 28,6% de precisão
# aparente e nenhum sobrevivente real na inspeção manual — o padrão casa runs
# de palavras capitalizadas em capa e tabela. `ref_line` e `contents` são
# assunto declarado pelo emissor, úteis como corroboração, fracos como prova.
KINDS_PONTUAVEIS = frozenset({"item"})


def _limpa(s: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", str(s or "")).strip()


def _secao(kind, heading, texto, a, b, form, item="", prioridade=2) -> dict:
    return {
        "kind": kind, "item": item, "heading": _limpa(heading)[:120],
        "text": _limpa(texto)[:MAX_SECAO],
        "start_offset": a, "end_offset": b,
        "source": form, "prioridade": prioridade,
    }


# "Item N.NN" nem sempre é cabeçalho de seção: com frequência é REFERÊNCIA
# CRUZADA no meio de uma frase ("as described in Item 5.03 below", "the
# information set forth in Item 2.02"). Medido no corpus: o 8-K da Truist
# declara os items 3.03/5.03/8.01/9.01 no metadata da SEC e o texto extraído
# tem UM único marcador — uma referência cruzada — a partir da qual a "seção"
# engolia capa e assinatura. Tratar isso como cabeçalho é pior que não
# segmentar.
_XREF_ANTES = re.compile(
    r"(?:described|set\s+forth|referred\s+to|included|disclosed|reported|see|under|"
    r"in|of|to|per)\s+$", re.I)
_XREF_DEPOIS = re.compile(r"^\s*(?:below|above)\b", re.I)


def _eh_referencia_cruzada(raw: str, m: re.Match) -> bool:
    antes = raw[max(0, m.start() - 40):m.start()]
    depois = raw[m.end():m.end() + 12]
    return bool(_XREF_ANTES.search(antes) or _XREF_DEPOIS.match(depois))


def split_8k_items(raw: str, form: str = "8-K") -> list[dict]:
    """Fatia um 8-K em seções por `Item N.NN`, cada uma até o próximo item.

    Marcadores que são referência cruzada não abrem seção — ver
    `_eh_referencia_cruzada`.
    """
    marcas = [m for m in _ITEM_MARK.finditer(raw)
              if not _eh_referencia_cruzada(raw, m)]
    if not marcas:
        return []
    out = []
    for i, m in enumerate(marcas):
        a = m.start()
        b = marcas[i + 1].start() if i + 1 < len(marcas) else len(raw)
        item = m.group(1)
        # ignora repetição do mesmo item já capturado com corpo maior
        out.append(_secao("item", f"Item {item} {m.group(2)}", raw[a:b], a, b,
                          form, item=item, prioridade=1))
    # itens duplicados (sumário + corpo): fica o trecho MAIOR de cada item
    melhor: dict[str, dict] = {}
    for s in out:
        k = s["item"]
        if k not in melhor or len(s["text"]) > len(melhor[k]["text"]):
            melhor[k] = s
    return sorted(melhor.values(), key=lambda s: s["start_offset"])


def split_6k_release(raw: str, form: str = "6-K") -> list[dict]:
    """Extrai as regiões de alto sinal de um 6-K (não existe `Item` aqui)."""
    out: list[dict] = []

    for m in _REF_LINE.finditer(raw):
        txt = m.group(1)
        if _TITULO_ADMIN.search(txt):
            continue
        b = min(len(raw), m.end() + 1500)
        out.append(_secao("ref_line", txt, raw[m.start():b], m.start(), b,
                          form, prioridade=1))

    for m in _DATELINE.finditer(raw):
        # a MANCHETE precede o dateline num press release ("Nubank to Add a
        # Banking License … \n São Paulo, July 20, 2026 – …"). Sem recuar, a
        # contraparte do negócio fica de fora da seção.
        a = max(0, m.start() - 300)
        b = min(len(raw), m.end() + 2500)
        out.append(_secao("dateline", m.group(2)[:120], raw[a:b], a, b,
                          form, prioridade=1))

    for m in _CONTENTS.finditer(raw):
        b = min(len(raw), m.end())
        out.append(_secao("contents", "Contents", m.group(1), m.start(), b,
                          form, prioridade=2))

    # manchete: só as que ficam antes de metade do documento e não são
    # administrativas — o título do release vem no começo.
    limite = max(2000, len(raw) // 2)
    for m in _MANCHETE.finditer(raw):
        if m.start() > limite:
            break
        titulo = m.group(1)
        if len(titulo) < 25 or _TITULO_ADMIN.search(titulo):
            continue
        if sum(1 for w in titulo.split() if w[:1].isupper()) < 4:
            continue
        b = min(len(raw), m.end() + 2000)
        out.append(_secao("headline", titulo, raw[m.start():b], m.start(), b,
                          form, prioridade=1))

    return _dedup(out)


def _dedup(secs: list[dict]) -> list[dict]:
    """Remove seções contidas em outra já presente (mesma região)."""
    secs = sorted(secs, key=lambda s: (s["start_offset"], -len(s["text"])))
    out: list[dict] = []
    for s in secs:
        if any(o["start_offset"] <= s["start_offset"]
               and s["end_offset"] <= o["end_offset"] for o in out):
            continue
        out.append(s)
    return out


def evidence_sections(raw: str, *, form: str, items: list[str] | None = None) -> dict:
    """Seções econômicas do filing + diagnóstico de cobertura.

    Devolve `{"sections": [...], "estrategia": ..., "cobertura": ...}`.
    `cobertura="documento_inteiro"` sinaliza que NÃO houve seção identificável
    — o consumidor decide se aceita evidência de varredura livre (e, na 4H.3E,
    ela deixa de ser pontuável).
    """
    raw = str(raw or "")
    form = str(form or "").upper()
    if not raw:
        return {"sections": [], "estrategia": "sem_corpo", "cobertura": "nenhuma"}

    if form in PERIODIC_FORMS:
        return {"sections": [], "estrategia": "periodico_sem_secao_economica",
                "cobertura": "nao_aplicavel"}

    if form.startswith("8-K"):
        secs = split_8k_items(raw, form)
        if secs:
            return {"sections": secs, "estrategia": "item_8k", "cobertura": "por_item"}
        return {"sections": split_6k_release(raw, form) or [],
                "estrategia": "8k_sem_item_fallback_release",
                "cobertura": "por_release" if split_6k_release(raw, form)
                else "documento_inteiro"}

    if form.startswith("6-K"):
        secs = split_6k_release(raw, form)
        return {"sections": secs, "estrategia": "release_6k",
                "cobertura": "por_release" if secs else "documento_inteiro"}

    secs = split_8k_items(raw, form) or split_6k_release(raw, form)
    return {"sections": secs, "estrategia": "generico",
            "cobertura": "por_secao" if secs else "documento_inteiro"}


def section_at(sections: list[dict], pos: int) -> dict | None:
    """Seção que contém o offset `pos` (o menor trecho que o cobre)."""
    cand = [s for s in sections
            if s["start_offset"] <= pos < s["end_offset"]]
    if not cand:
        return None
    return min(cand, key=lambda s: s["end_offset"] - s["start_offset"])
