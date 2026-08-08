#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_dom.py — 4H.3F: parser DOM estrutural do 8-K.

A 4H.3E provou (run 31206358785) que o texto achatado não sustenta escopo por
item: dos 4 pontuáveis finais, 0 eram verdadeiros — todos vinham de capa,
assinatura ou referência cruzada ("as described in Item 5.03 below") que o
regex de texto plano não conseguia distinguir de um heading real.

Investigação do HTML BRUTO dos mesmos filings (arquivo por arquivo, sem
biblioteca externa — só `html.parser` da stdlib) revelou a causa raiz e o
sinal que a resolve:

  1. O item real da Truist (accession 0001193125-26-226701) É encontrado no
     HTML: `<span style="font-weight:bold">Item&#8201;3.03</span>` — só que
     com U+2009 (thin space) entre "Item" e o número, que nenhum regex `\\s`
     casava no texto achatado. O que sobrava era só a referência cruzada
     "described in Item 5.03 below" (espaço normal) — daí o marcador único
     e a seção que engolia o documento inteiro.
  2. NextEra (accession 0001104659-26-062992): a evidência "Co-Registrant
     City Juno Beach" vinha de uma TABELA COM `display:none;visibility:hidden`
     — metadado de co-registrante, nunca visível a um leitor humano. O texto
     achatado não distinguia conteúdo oculto de conteúdo visível.
  3. Em AMBOS os templates observados (Truist: `<span style="font-weight:
     bold">`; Ford/Bunge/NextEra: `<b>`/`<span style="...font-weight:700">`),
     o heading real está SEMPRE dentro de um trecho em NEGRITO, e a referência
     cruzada está SEMPRE em texto normal (font-weight:400). É um sinal
     estrutural único, e generalizável entre os >3 templates de agência de
     arquivamento observados no corpus (Donnelley/EDGAR Online, Toppan,
     Workiva).

Regra desta fase: **"Item N.NN" só é heading se as 4 letras de "Item" caem
inteiramente dentro de um trecho em negrito** (`<b>`/`<strong>` ou
`font-weight` ≥ 600/bold/bolder), e nunca dentro de conteúdo oculto
(`display:none`, `visibility:hidden`, `-sec-ix-hidden`, `aria-hidden`).

Sem BeautifulSoup/lxml: usa `html.parser.HTMLParser` (stdlib), que já tokeniza
XHTML o bastante para os filings iXBRL da SEC. Não altera `requirements.txt`
nem o pipeline de produção — este módulo só é usado pelo shadow do EDGAR.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags cuja mera presença já é negrito, independente de CSS.
_BOLD_TAGS = frozenset({"b", "strong"})
_VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "col", "area",
                   "base", "wbr"})
_BLOCK = frozenset({"p", "div", "tr", "table", "li", "h1", "h2", "h3", "h4",
                    "h5", "h6", "td", "th"})

_FW_BOLD = re.compile(r"font-weight\s*:\s*(?:bold|bolder|[6-9]\d\d)", re.I)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)

_ITEM_RX = re.compile(r"Item\D{0,10}?(\d+\.\d{2})", re.I)
SIGNATURE_MARK = re.compile(r"^\s*SIGNATURES?\s*$", re.I | re.M)


def _flags(tag: str, attrs: list[tuple[str, str | None]]) -> tuple[bool, bool]:
    d = {k: (v or "") for k, v in attrs}
    bold = tag in _BOLD_TAGS or bool(_FW_BOLD.search(d.get("style", "")))
    hidden = (bool(_HIDDEN_STYLE.search(d.get("style", "")))
              or d.get("aria-hidden") == "true"
              or any(k.lower().startswith("-sec-ix-hidden") for k in d)
              or "hidden" in d)
    return bold, hidden


class _DomWalker(HTMLParser):
    """Extrai o texto VISÍVEL do documento, marcando cada trecho como
    negrito/não-negrito e preservando a tag de origem, na ORDEM DO DOM."""

    def __init__(self, raw: str):
        super().__init__(convert_charrefs=True)
        self._raw = raw
        self._lines = raw.split("\n")
        self.stack: list[dict] = []
        self.runs: list[tuple[int, bool, bool, str, str]] = []  # off,bold,hidden,tag,text

    def _offset(self) -> int:
        line, col = self.getpos()
        return sum(len(l) + 1 for l in self._lines[:line - 1]) + col

    def _bold_now(self) -> bool:
        return any(s["bold"] for s in self.stack)

    def _hidden_now(self) -> bool:
        return any(s["hidden"] for s in self.stack)

    def _cur_tag(self) -> str:
        return self.stack[-1]["tag"] if self.stack else ""

    def handle_starttag(self, tag, attrs):
        bold, hidden = _flags(tag, attrs)
        self.stack.append({"tag": tag, "bold": bold, "hidden": hidden})
        if tag in _BLOCK or tag in _VOID:
            self.runs.append((self._offset(), self._bold_now(),
                              self._hidden_now(), tag, "\n"))

    def handle_startendtag(self, tag, attrs):
        bold, hidden = _flags(tag, attrs)
        self.runs.append((self._offset(), self._bold_now() or bold,
                          self._hidden_now() or hidden, tag, "\n"))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data:
            return
        off = self._offset() - len(data)
        self.runs.append((off, self._bold_now(), self._hidden_now(),
                          self._cur_tag(), data))


class DomDocument:
    """Texto visível + mapeamento de offset para o HTML bruto + flags de
    negrito por caractere. `raw_html` nunca é copiado/alterado — só lido."""

    def __init__(self, raw_html: str):
        self.raw_html = raw_html
        w = _DomWalker(raw_html)
        try:
            w.feed(raw_html)
        except Exception:
            pass
        visiveis = [(off, bold, tag, text) for off, bold, hidden, tag, text
                    in w.runs if not hidden]
        self.text_parts = [t for _, _, _, t in visiveis]
        self.flat_text = "".join(self.text_parts)
        # offsets[i] = posição em raw_html do i-ésimo char de flat_text
        offsets = []
        bold_flags = []
        for off, bold, _tag, t in visiveis:
            offsets.extend(range(off, off + len(t)))
            bold_flags.extend([bold] * len(t))
        self.offsets = offsets
        self.bold_flags = bold_flags
        assert len(self.offsets) == len(self.flat_text) == len(self.bold_flags)

    def raw_span(self, a: int, b: int) -> tuple[int, int]:
        """Converte [a,b) de `flat_text` no span correspondente de `raw_html`."""
        if not self.offsets or a >= len(self.offsets):
            return (0, 0)
        b = min(b, len(self.offsets))
        return (self.offsets[a], self.offsets[max(a, b - 1)] + 1)


_WS_CHARS = frozenset(" \t\xa0    ")


def _is_bold_heading(doc: DomDocument, m: re.Match) -> bool:
    """'Item' precisa cair INTEIRO num trecho em negrito para ser heading."""
    a = m.start()
    b = min(len(doc.bold_flags), a + 4)  # as 4 letras de "Item"
    if b - a < 4:
        return False
    return all(doc.bold_flags[a:b])


def _is_first_in_block(doc: DomDocument, m: re.Match) -> bool:
    """'Item' é o PRIMEIRO conteúdo do seu bloco (célula/parágrafo)?

    Segundo sinal estrutural, independente de negrito — descoberto em dois
    templates da SEC (Energy Transfer, Loews) que não estilizam o heading em
    negrito. `\\n` no `flat_text` só existe onde o walker abriu um bloco
    (p/div/tr/td/table/li/h*); se o único caractere não-espaço antes da
    âncora é esse `\\n` (ou a âncora está no início do documento), "Item"
    abre o bloco — heading real. Uma referência cruzada como "as defined in
    Item 5.03 below" está no MEIO do texto do próprio bloco: o caractere
    anterior é prosa, nunca a marca de bloco.
    """
    i = m.start() - 1
    while i >= 0 and doc.flat_text[i] in _WS_CHARS:
        i -= 1
    if i < 0:
        return True
    return doc.flat_text[i] == "\n"


def _is_heading(doc: DomDocument, m: re.Match) -> bool:
    return _is_bold_heading(doc, m) or _is_first_in_block(doc, m)


def find_dom_headings(doc: DomDocument) -> list[dict]:
    """Todos os headings reais de Item, na ordem do documento."""
    out = []
    vistos_pos = set()
    for m in _ITEM_RX.finditer(doc.flat_text):
        if not _is_heading(doc, m):
            continue
        if m.start() in vistos_pos:
            continue
        vistos_pos.add(m.start())
        out.append({"item": m.group(1), "flat_start": m.start(),
                    "flat_end": m.end()})
    return out


def parse_8k_dom_sections(raw_html: str, *, items_metadata: list[str] | None = None,
                          source: str = "8-K") -> dict:
    """Fatia um 8-K pela estrutura REAL do DOM.

    Devolve seções no MESMO formato de `edgar_sections.evidence_sections`
    (compatível com `edgar_canonical.evaluate_candidate`), mais o diagnóstico
    de cobertura exigido em §6: `items_metadata` × `items_dom_found`.
    """
    items_metadata = [str(i).strip() for i in (items_metadata or []) if str(i).strip()]
    if not raw_html:
        return {"sections": [], "doc": None, "estrategia": "sem_html",
                "items_metadata": items_metadata, "items_dom_found": [],
                "items_missing_in_dom": items_metadata, "items_extra_in_dom": []}

    doc = DomDocument(raw_html)
    headings = find_dom_headings(doc)

    # heading de assinatura ("SIGNATURES") fecha a última seção de conteúdo —
    # sem isso a última seção (normalmente 9.01) engoliria bloco de assinatura.
    sig = SIGNATURE_MARK.search(doc.flat_text)
    fim_conteudo = sig.start() if sig else len(doc.flat_text)

    sections = []
    for i, h in enumerate(headings):
        a = h["flat_start"]
        b = headings[i + 1]["flat_start"] if i + 1 < len(headings) else fim_conteudo
        b = max(a, min(b, fim_conteudo)) if b <= fim_conteudo or i + 1 >= len(headings) \
            else b
        texto = doc.flat_text[a:b]
        # offsets em RAW_HTML — só para auditoria/citação (§5 dom_start/dom_end).
        ra, _ = doc.raw_span(a, a + 1) if a < len(doc.offsets) else (0, 0)
        _, rb = doc.raw_span(max(a, b - 1), b) if b > a else (ra, ra)
        # Fim do heading = primeiro PONTO FINAL após o número do item. Título
        # de item da SEC sempre termina em "." — mesmo com várias cláusulas
        # separadas por ";" no meio (ex.: 5.02 "Departure of Directors or
        # Certain Officers; Election of Directors; Appointment of Certain
        # Officers; Compensatory Arrangements of Certain Officers."). Esse
        # sinal funciona INDEPENDENTE de o título estar em negrito — achado
        # real: no 8-K da Truist (0001193125-26-270320) só "Item 5.02" em si
        # é negrito, o título vem em peso normal, e o walker antigo (que só
        # avançava enquanto bold_flags fosse True) parava depois de "5.02",
        # deixando o título inteiro dentro da região de busca — a âncora de
        # troca_ceo travava em "Appointment" do TÍTULO, não em "will retire"
        # do corpo real.
        _ponto = doc.flat_text.find(".", h["flat_end"], h["flat_end"] + 220)
        if _ponto != -1:
            heading_fim = _ponto + 1
        else:
            heading_fim = a
            while heading_fim < len(doc.flat_text) and doc.bold_flags[heading_fim] \
                    and doc.flat_text[heading_fim] != "\n":
                heading_fim += 1
        heading_fim = min(heading_fim, b)
        sections.append({
            "kind": "item_dom", "item": h["item"],
            "heading": re.sub(r"\s+", " ", doc.flat_text[a:heading_fim]).strip()[:160],
            "text": re.sub(r"\s{2,}", " ", texto).strip()[:8000],
            # start_offset/end_offset ficam no MESMO espaço de coordenadas de
            # `doc.flat_text` — é ele, não raw_html, que vira `bruto`/`sem`
            # rio abaixo (`_restrito`/`_janela` fatiam por esses índices). Usar
            # offsets de raw_html aqui faria a evidência sair de posição
            # errada (raw_html tem tags; flat_text não).
            "start_offset": a, "end_offset": b,
            # body_start_offset EXCLUI o heading da busca de âncora. O título
            # do Item 5.02 é boilerplate OBRIGATÓRIO da SEC — "Departure of
            # Directors or Certain Officers; Election of Directors;
            # Appointment of Certain Officers; Compensatory Arrangements..." —
            # e contém "appoint"/"director"/"officer" em TODO filing, real ou
            # não. Sem excluir o heading, a âncora de troca_ceo trava no
            # título em vez do corpo (achado real: Truist CEO retirement,
            # 0001193125-26-270320, evidência saía como texto de capa porque
            # o match de "Appointment" no título vinha antes do "will retire"
            # real, e a janela ficava ancorada no lugar errado).
            "body_start_offset": heading_fim,
            "dom_start": ra, "dom_end": rb,
            "source": source, "prioridade": 1,
            "source_tags": "b/strong/font-weight",
            "section_confidence": "alta",
        })

    achados = {s["item"] for s in sections}
    return {
        "sections": sections, "doc": doc, "estrategia": "dom_8k",
        "items_metadata": items_metadata,
        "items_dom_found": sorted(achados),
        "items_missing_in_dom": sorted(set(items_metadata) - achados),
        "items_extra_in_dom": sorted(achados - set(items_metadata)),
    }


# ───────────────────────────── exhibits ───────────────────────────────────
# Só recupera exhibit quando a SEÇÃO diz explicitamente que o conteúdo
# econômico está nele — nunca a partir do índice de exhibits (§9).
_EXHIBIT_REF = re.compile(
    r"(?:attached|filed|furnished)\s+(?:hereto\s+)?as\s+Exhibit\s+(\d+\.\d+)"
    r"|Exhibit\s+(\d+\.\d+)\s+(?:hereto\s+)?is\s+(?:attached|filed|furnished|"
    r"incorporated)", re.I)
_EXHIBIT_INDEX_HEADING = re.compile(r"exhibit\s+index|exhibit\s+no\.?\s*description",
                                    re.I)


def referenced_exhibits(section: dict) -> list[dict]:
    """Exhibits citados DENTRO do corpo de uma seção (não do índice)."""
    texto = section.get("text", "")
    if _EXHIBIT_INDEX_HEADING.search(texto[:80]):
        return []
    out = []
    for m in _EXHIBIT_REF.finditer(texto):
        num = m.group(1) or m.group(2)
        out.append({
            "exhibit_number": num,
            "referenced_by_item": section.get("item", ""),
            "evidence_used": re.sub(r"\s+", " ", texto[max(0, m.start() - 60):
                                                       m.end() + 60])[:160],
        })
    return out
