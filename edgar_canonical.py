#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_canonical.py — 4H.3C: parser CANÔNICO de submissions da SEC + evidência.

Motivação (medida em `origin/main` antes desta fase):

  * `_edgar_articles_from_submissions` (risk_dashboard.py) já preservava form,
    accession, primaryDocument, description e items — mas **não** `reportDate`,
    **não** CIK e **não** ticker, e o "conteúdo" classificado era apenas
    `desc or form`. Com `primaryDocDescription == form` o título virava
    literalmente `Ford Motor — 8-K: 8-K`.
  * `edgar_audit_4h2.sample_filings` é um SEGUNDO parser, divergente, usado
    pelo shadow 4H.3A. Dois parsers = duas verdades.

Este módulo é a fonte única e canônica. Ele NÃO pontua nada: devolve filings
canônicos e candidatos a evento com a evidência que os sustenta (ou a falta
dela). A decisão de score continua fora daqui e continua desligada
(`edgar_scoring_enabled=false`).

Princípio inegociável (invariante 7 do CLAUDE.md, aplicado ao EDGAR):

    O FORMULÁRIO GERA CANDIDATO. A EVIDÊNCIA DETERMINA O EVENTO.

Um `8-K` sozinho não prova default, RJ, downgrade, M&A, fraude, troca de CEO
nem incidente operacional. Os itens genéricos (7.01 Reg FD, 8.01 Other Events,
9.01 Exhibits) não provam absolutamente nada sozinhos.

Sem rede por padrão: `fetch_document_text` recebe um `fetcher` injetável, de
modo que toda a suíte roda offline.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

# ───────────────────────── semântica dos itens do 8-K ─────────────────────────
# Fonte: SEC Form 8-K, General Instruction B. Mapeia item → (rótulo, eventos
# CANDIDATOS, força probatória do item por si só).
#
# forca:
#   "generico"  → nunca prova evento sozinho (Reg FD, Other Events, Exhibits)
#   "candidato" → levanta candidato, exige evidência textual compatível
#   "forte"     → o próprio item já é o fato jurídico (1.03 = bankruptcy)
ITEM_SEMANTICS: dict[str, dict] = {
    "1.01": {"label": "Entry into a Material Definitive Agreement",
             "eventos": ["ma", "emissao_divida"], "forca": "candidato"},
    "1.02": {"label": "Termination of a Material Definitive Agreement",
             "eventos": ["encerramento_litigio"], "forca": "candidato"},
    "1.03": {"label": "Bankruptcy or Receivership",
             "eventos": ["recuperacao_judicial", "falencia"], "forca": "forte"},
    "2.01": {"label": "Completion of Acquisition or Disposition of Assets",
             "eventos": ["ma"], "forca": "candidato"},
    "2.02": {"label": "Results of Operations and Financial Condition",
             "eventos": ["resultado_financeiro_negativo",
                         "resultado_acima_expectativas"], "forca": "candidato"},
    "2.03": {"label": "Creation of a Direct Financial Obligation",
             "eventos": ["emissao_divida"], "forca": "candidato"},
    "2.04": {"label": "Triggering Events That Accelerate a Direct Financial Obligation",
             "eventos": ["default", "covenant_breach"], "forca": "candidato"},
    "2.05": {"label": "Costs Associated with Exit or Disposal Activities",
             "eventos": ["deterioracao_operacional"], "forca": "candidato"},
    "2.06": {"label": "Material Impairments",
             "eventos": ["deterioracao_operacional"], "forca": "candidato"},
    "3.01": {"label": "Notice of Delisting or Failure to Satisfy a Listing Rule",
             "eventos": ["suspensao_negociacao"], "forca": "candidato"},
    "4.01": {"label": "Changes in Registrant's Certifying Accountant",
             "eventos": ["renuncia_auditor"], "forca": "candidato"},
    "4.02": {"label": "Non-Reliance on Previously Issued Financial Statements",
             "eventos": ["fraude", "renuncia_auditor"], "forca": "candidato"},
    "5.02": {"label": "Departure/Election of Directors or Certain Officers",
             "eventos": ["troca_ceo"], "forca": "candidato"},
    "5.03": {"label": "Amendments to Articles of Incorporation or Bylaws",
             "eventos": ["reorganizacao_societaria_interna"], "forca": "candidato"},
    "7.01": {"label": "Regulation FD Disclosure", "eventos": [], "forca": "generico"},
    "8.01": {"label": "Other Events", "eventos": [], "forca": "generico"},
    "9.01": {"label": "Financial Statements and Exhibits", "eventos": [],
             "forca": "generico"},
}

# Itens que JAMAIS provam evento sozinhos (§5 do pedido 4H.3C).
GENERIC_ITEMS = frozenset(k for k, v in ITEM_SEMANTICS.items()
                          if v["forca"] == "generico")

# Formulários periódicos: são relatório, não fato relevante. Geram candidato
# apenas se o texto trouxer o fato; nunca por existirem.
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})

# ─────────────────── evidência textual exigida por evento ────────────────────
# Eventos MATERIAIS exigem âncora textual compatível. Sem âncora → rejeitado,
# independentemente do formulário ou do item.
EVENT_EVIDENCE: dict[str, list[str]] = {
    "recuperacao_judicial": [
        r"chapter\s*11", r"recupera[çc][ãa]o\s+judicial",
        r"judicial\s+reorganizat\w+", r"concurso\s+mercantil",
        r"reorganizat\w+\s+proceeding", r"debtor[- ]in[- ]possession",
    ],
    "falencia": [
        r"chapter\s*7", r"bankrupt\w*", r"receivership", r"liquidat\w+\s+proceeding",
        r"fal[êe]nci\w*", r"winding[- ]up",
    ],
    "default": [
        r"\bdefault(?:s|ed|ing)?\b", r"event\s+of\s+default", r"missed\s+payment",
        r"failure\s+to\s+pay", r"non[- ]payment", r"inadimpl\w*",
        r"accelerat\w+\s+(?:of\s+)?(?:the\s+)?(?:indebtedness|obligation)",
    ],
    "covenant_breach": [
        r"covenant\s+(?:breach|violation|default)", r"breach\w*\s+\w*\s*covenant",
        r"fail\w*\s+to\s+comply\s+with\s+\w*\s*covenant", r"waiver\s+of\s+\w*\s*covenant",
    ],
    "rebaixamento_rating": [
        r"downgrad\w+", r"lower\w*\s+(?:its\s+)?(?:credit\s+)?rating",
        r"rebaix\w+", r"rating\s+cut", r"cut\s+(?:its\s+)?rating",
        r"placed?\s+on\s+(?:credit\s+)?watch\s+negative",
    ],
    "rating_elevado": [
        r"upgrad\w+", r"rais\w+\s+(?:its\s+)?(?:credit\s+)?rating",
        r"eleva\w+\s+(?:a\s+)?nota", r"rating\s+increase",
    ],
    "fraude": [
        r"fraud\w*", r"misappropriat\w+", r"embezzl\w+", r"accounting\s+irregularit\w+",
        r"material\s+misstatement", r"non[- ]reliance", r"restat\w+\s+\w*\s*financial",
        r"fraude", r"desvio\s+de\s+recursos",
    ],
    "ma": [
        r"merger", r"acquisi\w+", r"acquir\w+", r"business\s+combination",
        r"purchase\s+agreement", r"stock\s+purchase", r"asset\s+purchase",
        r"divestit\w+", r"dispos\w+\s+of\s+\w*\s*assets", r"tender\s+offer",
        r"fus[ãa]o", r"aquisi[çc][ãa]o", r"incorpora[çc][ãa]o",
    ],
    "troca_ceo": [
        r"chief\s+executive\s+officer", r"\bCEO\b", r"chief\s+financial\s+officer",
        r"\bCFO\b", r"president\s+and\s+chief", r"resign\w*", r"appoint\w*",
        r"step(?:ped|ping)?\s+down", r"terminat\w+\s+\w*\s*employment",
        r"succeed\w*", r"renunci\w*", r"nomea\w*",
    ],
    "emissao_divida": [
        r"notes\s+(?:due|offering)", r"senior\s+(?:secured\s+|unsecured\s+)?notes",
        r"indenture", r"credit\s+agreement", r"term\s+loan", r"bond\s+offering",
        r"debentur\w+", r"aggregate\s+principal\s+amount",
        r"emiss[ãa]o\s+de\s+d[íi]vida",
    ],
    "incidente_operacional": [
        r"accident", r"explosion", r"fire\s+at", r"spill", r"rupture",
        r"derailment", r"outage", r"shutdown\s+of", r"incident\s+at",
        r"inc[êe]ndio", r"acidente", r"vazamento", r"rompimento",
    ],
    "suspensao_negociacao": [
        r"delist\w+", r"suspen\w+\s+(?:from\s+)?trading", r"trading\s+halt",
        r"fail\w*\s+to\s+satisfy\s+\w*\s*listing", r"non[- ]compliance\s+with\s+\w*\s*listing",
    ],
    "renuncia_auditor": [
        r"dismiss\w+\s+\w*\s*(?:independent\s+)?(?:registered\s+public\s+)?accounting",
        r"resign\w+\s+as\s+\w*\s*(?:independent\s+)?auditor",
        r"chang\w+\s+in\s+\w*\s*certifying\s+accountant", r"ren[úu]nci\w+\s+do\s+auditor",
    ],
    "investigacao_regulatoria": [
        r"subpoena", r"investigat\w+\s+by", r"\bSEC\s+investigation",
        r"enforcement\s+action", r"formal\s+order\s+of\s+investigation",
        r"investiga[çc][ãa]o", r"inqu[ée]rito",
    ],
}

# Eventos MATERIAIS: exigem evidência sempre, sem exceção.
MATERIAL_EVENTS = frozenset({
    "recuperacao_judicial", "falencia", "default", "covenant_breach",
    "rebaixamento_rating", "fraude", "ma", "troca_ceo", "emissao_divida",
    "incidente_operacional", "suspensao_negociacao", "renuncia_auditor",
    "investigacao_regulatoria",
})

# ── 5.02: distinguir executivo relevante de diretor qualquer ──
_ROLE_TOP = re.compile(
    r"chief\s+executive\s+officer|\bCEO\b|chief\s+financial\s+officer|\bCFO\b"
    r"|president\s+and\s+chief|principal\s+executive\s+officer"
    r"|principal\s+financial\s+officer", re.I)
_ROLE_OTHER = re.compile(
    r"\bdirector\b|board\s+of\s+directors|chief\s+operating\s+officer|\bCOO\b"
    r"|chief\s+technology|\bCTO\b|vice\s+president|chief\s+accounting", re.I)
_DEPARTURE = re.compile(
    r"resign\w*|step(?:ped|ping)?\s+down|depart\w*|terminat\w*|retire\w*"
    r"|will\s+no\s+longer\s+serve|separation", re.I)
_ARRIVAL = re.compile(
    r"appoint\w*|elect\w*|nam\w+\s+as|succeed\w*|will\s+serve\s+as"
    r"|has\s+been\s+hired|join\w+\s+as", re.I)


def _clean(s) -> str:
    return str(s or "").strip()


def normalize_accession(acc: str) -> str:
    """Accession estável e comparável: só dígitos, sempre 18 caracteres.
    `0000037996-26-000045` e `000003799626000045` são o MESMO documento."""
    d = re.sub(r"\D", "", str(acc or ""))
    return d


def accession_dashed(acc: str) -> str:
    """Formato canônico com hífens (o que a SEC exibe)."""
    d = normalize_accession(acc)
    if len(d) != 18:
        return _clean(acc)
    return f"{d[:10]}-{d[10:12]}-{d[12:]}"


def _ts_from_date(s: str) -> int | None:
    try:
        return int(datetime.strptime(_clean(s), "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return None


def split_items(raw) -> list[str]:
    """`items` da SEC vem como string separada por vírgula, às vezes com o
    rótulo junto ("5.02 Departure of Directors"). Extrai só os códigos."""
    txt = _clean(raw)
    if not txt:
        return []
    return [m.group(0) for m in re.finditer(r"\b\d\.\d{2}\b", txt)]


def canonical_title(f: dict) -> str:
    """Título REAL do filing.

    Regra explícita do 4H.3C: nunca produzir `Empresa — 8-K: 8-K`. Se a
    description for redundante com o form (ou ausente), usa o rótulo oficial
    dos itens; se nem isso existir, fica só `Empresa — FORM`.
    """
    empresa = _clean(f.get("company"))
    form = _clean(f.get("form"))
    desc = _clean(f.get("description"))
    base = f"{empresa} — {form}" if empresa else form

    # description redundante ("8-K", "FORM 8-K", "8-K/A") não acrescenta nada
    desc_norm = re.sub(r"[^a-z0-9]", "", desc.lower())
    form_norm = re.sub(r"[^a-z0-9]", "", form.lower())
    if desc_norm and desc_norm not in (form_norm, "form" + form_norm):
        base += f": {desc}"
    else:
        rotulos = [ITEM_SEMANTICS[i]["label"] for i in f.get("items", [])
                   if i in ITEM_SEMANTICS]
        if rotulos:
            base += ": " + "; ".join(rotulos)

    itens = f.get("items") or []
    if itens:
        base += f" (items {', '.join(itens)})"
    return base


def parse_submissions(data: dict, *, company: str, cik10: str, forms: set[str],
                      cutoff_ts: int, ticker: str = "",
                      limit: int | None = None) -> list[dict]:
    """Parser CANÔNICO de `https://data.sec.gov/submissions/CIK##########.json`.

    Preserva tudo que a 4H.3C exige: CIK, ticker, empresa, form, accession,
    filingDate, **reportDate**, primaryDocument, items do 8-K, description,
    URL oficial e metadata do emissor. Sem rede.
    """
    filings = (data.get("filings") or {}).get("recent") or {}
    fs = filings.get("form", []) or []
    dates = filings.get("filingDate", []) or []
    rdates = filings.get("reportDate", []) or []          # ← ausente até 4H.3C
    accs = filings.get("accessionNumber", []) or []
    docs = filings.get("primaryDocument", []) or []
    descs = filings.get("primaryDocDescription", []) or []
    items = filings.get("items", []) or []

    cik_int = re.sub(r"\D", "", str(cik10 or "")) or "0"
    meta = {
        "entity_name": _clean(data.get("name")),
        "sic": _clean(data.get("sic")),
        "sic_description": _clean(data.get("sicDescription")),
        "exchanges": list(data.get("exchanges") or []),
        "tickers": list(data.get("tickers") or []),
        "fiscal_year_end": _clean(data.get("fiscalYearEnd")),
        "state_of_incorporation": _clean(data.get("stateOfIncorporation")),
    }
    tk = _clean(ticker) or (meta["tickers"][0] if meta["tickers"] else "")

    out: list[dict] = []
    for i, form in enumerate(fs):
        form = _clean(form)
        if form not in forms:
            continue
        fdate = dates[i] if i < len(dates) else ""
        ts = _ts_from_date(fdate)
        if ts is None or ts < cutoff_ts:
            continue

        acc_raw = _clean(accs[i] if i < len(accs) else "")
        acc_digits = normalize_accession(acc_raw)
        doc = _clean(docs[i] if i < len(docs) else "")
        rdate = _clean(rdates[i] if i < len(rdates) else "")
        desc = _clean(descs[i] if i < len(descs) else "")
        its = split_items(items[i] if i < len(items) else "")

        base_dir = (f"https://www.sec.gov/Archives/edgar/data/{int(cik_int)}/{acc_digits}"
                    if acc_digits else "")
        url_doc = f"{base_dir}/{doc}" if (base_dir and doc) else ""
        url_index = f"{base_dir}/{accession_dashed(acc_raw)}-index.htm" if base_dir else ""

        out.append({
            "company": _clean(company),
            "cik": cik_int.zfill(10),
            "ticker": tk,
            "form": form,
            "accession_number": accession_dashed(acc_raw),
            "accession_digits": acc_digits,
            "filing_date": _clean(fdate),
            "report_date": rdate,
            "pub_ts": ts,
            "primary_document": doc,
            "description": desc,
            "items": its,
            "item_labels": [ITEM_SEMANTICS[x]["label"] for x in its
                            if x in ITEM_SEMANTICS],
            "url": url_doc or url_index or (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int.zfill(10)}"),
            "url_index": url_index,
            "metadata": meta,
            "provenance": "EDGAR",
            "source": "SEC · EDGAR",
            "domain": "sec.gov",
        })
        if limit and len(out) >= limit:
            break
    return out


# ───────────────────────── evidência: corpo do documento ─────────────────────
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_ANYTAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def strip_html(raw: str) -> str:
    """Extrai texto legível de um documento EDGAR (HTM ou TXT)."""
    s = _TAG.sub(" ", str(raw or ""))
    s = _ANYTAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def archive_headers(user_agent: str) -> dict:
    """Headers para `www.sec.gov/Archives/...`.

    NÃO reaproveitar `risk_dashboard._edgar_headers()` aqui: aquele fixa
    `Host: data.sec.gov`, correto para a API de submissions e ERRADO para os
    Archives — a requisição é roteada para o bucket errado e volta
    `404 NoSuchKey`. Isso derrubou o primeiro ciclo real da 4H.3C (run
    31142676268): 32/32 emissores com HTTP 200 na API, 211 filings, e ZERO
    corpos recuperados.
    """
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def fetch_document_text(url: str, fetcher, *, max_chars: int = 60000,
                        errors: list | None = None) -> str:
    """Busca LIMITADA do corpo do filing.

    `fetcher(url) -> str` é injetado (a suíte passa um stub; produção passa o
    cliente HTTP real com o User-Agent exigido pela SEC). Falha devolve string
    vazia — ausência de evidência, nunca evento presumido — mas o motivo é
    REGISTRADO em `errors`: falha silenciosa é o que torna "HTTP 200" um falso
    sucesso indistinguível de sucesso real.
    """
    if not url or fetcher is None:
        if errors is not None:
            errors.append("sem url ou sem fetcher")
        return ""
    try:
        raw = fetcher(url)
    except Exception as exc:
        if errors is not None:
            errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        return ""
    if not raw:
        if errors is not None:
            errors.append("resposta vazia")
        return ""
    txt = strip_html(raw)[:max_chars]
    if not txt and errors is not None:
        errors.append(f"corpo sem texto extraível ({len(str(raw))} bytes brutos)")
    return txt


def _window(text: str, m: re.Match, before: int = 90, after: int = 140) -> str:
    return text[max(0, m.start() - before):min(len(text), m.end() + after)].strip()


# Estrutura burocrática do documento — índice, capa, rodapé. Uma âncora que cai
# aqui NÃO é fato econômico. Medido no run 31142988539: "Item 3. Defaults Upon
# Senior Securities" (título de seção padrão de todo 10-Q) e a capa listando
# "6.200% Notes due 2059" produziram 13 de 13 eventos pontuáveis — todos falsos.
_BOILERPLATE = re.compile(
    r"Item\s*3\.?\s*Defaults\s+Upon\s+Senior\s+Securities"
    r"|Mine\s+Safety\s+Disclosures"
    r"|Unregistered\s+Sales\s+of\s+Equity\s+Securities"
    r"|Securities\s+registered\s+pursuant\s+to\s+Section\s+12"
    r"|Title\s+of\s+each\s+class"
    r"|Name\s+of\s+each\s+exchange\s+on\s+which\s+registered"
    r"|Trading\s+Symbol\(?s?\)?"
    r"|Table\s+of\s+Contents"
    r"|Index\s+to\s+(?:Condensed\s+)?(?:Consolidated\s+)?Financial\s+Statements",
    re.I)


def is_boilerplate(text: str, start: int, end: int, raio: int = 220) -> bool:
    """A âncora está dentro de estrutura burocrática (índice/capa/rodapé)?"""
    jan = str(text)[max(0, start - raio):min(len(str(text)), end + raio)]
    return bool(_BOILERPLATE.search(jan))


def find_evidence_in_sections(text: str, event_id: str,
                              sections: list[dict]) -> dict | None:
    """Procura a âncora APENAS dentro das seções econômicas (4H.3E).

    Restringir a busca é o ponto da fase: no run 31193786617 os falsos
    positivos vinham de capa, assinatura, balanço e biografia — regiões que
    não são seção econômica de nenhum filing.
    """
    if not sections:
        return None
    for s in sorted(sections, key=lambda x: (x.get("prioridade", 9),
                                             x.get("start_offset", 0))):
        a, b = s.get("start_offset", 0), s.get("end_offset", 0)
        trecho = text[a:b]
        achado = find_evidence(trecho, event_id)
        if not achado:
            continue
        return {**achado,
                "evidence_start": achado["evidence_start"] + a,
                "evidence_end": achado["evidence_end"] + a,
                "section_kind": s.get("kind", ""),
                "section_item": s.get("item", ""),
                "section_heading": s.get("heading", "")}
    return None


def find_evidence(text: str, event_id: str) -> dict | None:
    """Procura âncora textual do evento, IGNORANDO estrutura burocrática.

    Percorre todas as ocorrências e devolve a primeira que não esteja em
    índice/capa — uma âncora dentro do sumário do 10-Q não é evidência.
    """
    pats = EVENT_EVIDENCE.get(event_id)
    if not pats or not text:
        return None
    rx = re.compile("|".join(pats), re.I)
    for m in rx.finditer(text):
        if is_boilerplate(text, m.start(), m.end()):
            continue
        return {"evidence_text": _window(text, m), "evidence_match": m.group(0),
                "evidence_start": m.start(), "evidence_end": m.end()}
    return None


# Tokens capitalizados que NÃO são nome de pessoa. Sem isso, "On July 20, 2026,
# John Lawler resigned" devolve "On July" como quem saiu.
_NOT_A_NAME = frozenset("""
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday the board
company corporation inc ltd llc holdings group chief executive financial
officer president director directors annual meeting effective on at as of and
""".split())


_NEG = re.compile(r"\b(?:did\s+not|does\s+not|do\s+not|will\s+not|no|never|"
                  r"without|neither|nor|n[ãa]o)\b", re.I)

# ── linguagem CONTRATUAL / HIPOTÉTICA ────────────────────────────────────────
# Medido no replay 4H.3D: depois de neutralizar o boilerplate de 1933/1934,
# emergiu outra classe de falso positivo — cláusulas de contrato de crédito que
# DEFINEM o que constituiria default/falência, e não relatam fato algum:
#   Ford, `falencia`:  "any significant guarantor with an aggregate outstanding
#                       principal amount …"
#   Baker Hughes, `default`: "Borrowings under each Term Loan Credit Agreement…"
# Um covenant que descreve hipótese não é ocorrência econômica.
_CONTRATUAL = re.compile(
    r"\bas\s+defined\s+in\b|\bcapitalized\s+terms?\b|\bshall\s+(?:mean|be|have|"
    r"constitute)\b|\bmeans\b|\bEvents?\s+of\s+Default\b\s*(?:”|\"|means|shall)"
    r"|\bCredit\s+Agreement\b|\bIndenture\b|\bTerm\s+Loan\b|\bUnderwriting\s+Agreement\b"
    r"|\bupon\s+the\s+occurrence\s+of\b|\bwould\s+(?:constitute|result)\b"
    r"|\bif\s+any\s+(?:such\s+)?\w+\s+(?:shall|were|occurs)\b"
    r"|\bfrom\s+time\s+to\s+time\b|\bamended,\s+supplemented\b"
    r"|\bExhibit\s+\d|\bEXHIBIT\s+INDEX\b|\bpursuant\s+to\s+which\b", re.I)

# Verbos que indicam FATO consumado — vencem a suspeita contratual.
_FATO_CONSUMADO = re.compile(
    r"\b(?:filed\s+a\s+(?:voluntary\s+)?petition|has\s+(?:filed|defaulted|failed)"
    r"|completed\s+the\s+acquisition|closed\s+the\s+(?:merger|acquisition)"
    r"|was\s+downgraded|downgraded\s+(?:the|its)|resigned\s+as|appointed\s+as"
    r"|entered\s+into\s+a\s+definitive|announced\s+(?:the\s+)?(?:completion|closing)"
    r"|declared\s+bankruptcy|commenced\s+(?:chapter|proceedings)"
    # emissão REALIZADA: "issued $1.5 billion …" é fato, ainda que a frase cite
    # o indenture que a rege. Sem isto, a guarda contratual apagava emissão
    # legítima só porque a palavra "indenture" aparecia.
    r"|issued\s+(?:an\s+aggregate\s+)?\$[\d.,]+|has\s+issued\b"
    r"|(?:completed|priced|closed)\s+(?:an?\s+|its\s+)?(?:public\s+|private\s+)?offering"
    r"|sold\s+\$[\d.,]+)\b", re.I)


# Enumeração de FATORES DE RISCO / forward-looking: lista tudo que "poderia"
# acontecer. No replay 4H.3D os 6-K da Cemex produziam `default`, `falencia`,
# `ma` e `incidente_operacional` a partir de trechos como "changes in the
# economy that affect demand for consumer goods" e "money laundering,
# terrorism financing and corruption". Risco enumerado não é risco ocorrido.
_RISCO_HIPOTETICO = re.compile(
    r"\bcould\s+(?:cause|affect|result|adversely)\b|\bmay\s+(?:adversely\s+)?affect\b"
    r"|\bactual\s+results\s+(?:may|could|to)\b|\brisks?\s+and\s+uncertaint\w+\b"
    r"|\bforward[-\s]looking\b|\bcautionary\b|\bwe\s+(?:may|could)\s+\w+\b"
    r"|\bchanges\s+in\s+the\s+(?:economy|market|law)\b|\bamong\s+other\s+(?:factors|things)\b"
    r"|\bthat\s+(?:can|could)\s+adversely\s+affect\b|\bsubject\s+to\s+risks\b", re.I)

# Densidade de lista: enumerações de risco vêm em séries de itens separados por
# ponto-e-vírgula ou marcadores.
_LISTA_DENSA = re.compile(r"(?:[;•]\s+\w+[^;•]{5,90}){3,}")


def is_contractual(trecho: str) -> bool:
    """A evidência é cláusula contratual/hipotética em vez de fato ocorrido?"""
    t = str(trecho or "")
    if not t:
        return False
    if _FATO_CONSUMADO.search(t):
        return False
    return bool(_CONTRATUAL.search(t) or _RISCO_HIPOTETICO.search(t)
                or _LISTA_DENSA.search(t))


def _anchor_negada(text: str, achado: dict | None, janela: int = 45) -> bool:
    """A âncora encontrada está sob escopo de negação imediatamente anterior?"""
    if not achado:
        return False
    ini = max(0, int(achado.get("evidence_start", 0)) - janela)
    return bool(_NEG.search(str(text)[ini:achado.get("evidence_start", 0)]))


def analyze_officer_change(text: str) -> dict:
    """Detalha um 5.02: quem saiu, quem entrou, cargo, e se é executivo-chave.

    Um 5.02 pode ser troca de CEO/CFO (material) ou eleição rotineira de
    conselheiro (não material). Sem distinguir os dois, o 5.02 vira fábrica de
    falso positivo de `troca_ceo`.
    """
    t = str(text or "")
    top = bool(_ROLE_TOP.search(t))
    other = bool(_ROLE_OTHER.search(t))
    saida = _DEPARTURE.search(t)
    entrada = _ARRIVAL.search(t)
    # nome próprio adjacente ao verbo (heurística conservadora)
    nome_rx = re.compile(r"\b(?:Mr\.|Ms\.|Mrs\.|Dr\.)?\s*"
                         r"([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2})\b")

    def _nome_perto(m):
        if not m:
            return ""
        jan = t[max(0, m.start() - 120):min(len(t), m.end() + 120)]
        for n in nome_rx.finditer(jan):
            toks = [w.strip(".,;").lower() for w in n.group(1).split()]
            if any(w in _NOT_A_NAME for w in toks):
                continue
            return n.group(1)
        return ""

    return {
        "cargo_relevante": top,
        "cargo_secundario": other and not top,
        "quem_saiu": _nome_perto(saida),
        "quem_entrou": _nome_perto(entrada),
        "tem_saida": bool(saida),
        "tem_entrada": bool(entrada),
        "suficiente": bool(top and (saida or entrada)),
        "motivo": ("ok" if top and (saida or entrada)
                   else "5.02 sem cargo CEO/CFO identificado" if not top
                   else "5.02 sem verbo de saída/entrada identificado"),
    }


# ─────────────────────── candidatos a evento (form + item) ───────────────────
def candidate_events(filing: dict) -> list[dict]:
    """Levanta CANDIDATOS a partir de form + items. Nunca decide evento."""
    form = _clean(filing.get("form"))
    itens = filing.get("items") or []
    cands: list[dict] = []
    vistos: set[str] = set()

    for it in itens:
        sem = ITEM_SEMANTICS.get(it)
        if not sem:
            cands.append({"event_id": "", "item": it, "form": form,
                          "origem": "item_desconhecido", "forca": "generico",
                          "motivo": f"item {it} fora do mapa 8-K conhecido"})
            continue
        if sem["forca"] == "generico":
            cands.append({"event_id": "", "item": it, "form": form,
                          "origem": "item_generico", "forca": "generico",
                          "motivo": f"item {it} ({sem['label']}) não prova evento sozinho"})
            continue
        for ev in sem["eventos"]:
            if ev in vistos:
                continue
            vistos.add(ev)
            cands.append({"event_id": ev, "item": it, "form": form,
                          "origem": "item_8k", "forca": sem["forca"],
                          "motivo": f"item {it} ({sem['label']}) → candidato {ev}"})

    if not itens:
        # 6-K/20-F/10-K não têm items: todo evento vem do TEXTO, nunca do form.
        cands.append({"event_id": "", "item": "", "form": form,
                      "origem": ("form_periodico" if form in PERIODIC_FORMS
                                 else "form_sem_item"),
                      "forca": "generico",
                      "motivo": f"{form} sem items — candidatos só por evidência textual"})
    return cands


def _restrito(texto: str, a: int, b: int) -> str:
    """Mantém só o intervalo [a,b), branqueando o resto e PRESERVANDO offsets.

    Assim a busca de âncora fica confinada à seção sem que os offsets
    encontrados deixem de valer no documento bruto.
    """
    t = str(texto or "")
    a, b = max(0, a), min(len(t), b)
    return (" " * a) + t[a:b] + (" " * (len(t) - b))


def _kinds_pontuaveis() -> frozenset:
    try:
        import edgar_sections as _es
        return _es.KINDS_PONTUAVEIS
    except Exception:
        return frozenset({"item"})


_KINDS_PONTUAVEIS = _kinds_pontuaveis()


def _janela(raw: str, achado: dict | None) -> dict:
    """Janela LOCAL de evidência no BRUTO, com heading da seção (4H.3D §6)."""
    if not achado or not raw:
        return {}
    try:
        import edgar_normalizer as _en
        return _en.evidence_window(raw, achado["evidence_start"],
                                   achado["evidence_end"])
    except Exception:
        return {}


def evaluate_candidate(filing: dict, candidate: dict, text: str,
                       raw: str | None = None) -> dict:
    """Decide um candidato à luz da EVIDÊNCIA. Não pontua: só qualifica.

    `text` é o texto de BUSCA (semântico quando houve normalização); `raw` é o
    bruto de onde a evidência é recortada.
    """
    ev = _clean(candidate.get("event_id"))
    item = _clean(candidate.get("item"))
    forca = _clean(candidate.get("forca"))
    bruto = raw if raw is not None else text
    base = {
        **candidate,
        "evidence_text": "", "evidence_match": "",
        "evidence_section": "", "evidence_source": "raw_document_text",
        "aceito": False, "confianca": "nenhuma", "decisao": "rejeitado",
    }

    if not ev:
        base["motivo_decisao"] = candidate.get("motivo") or "sem candidato de evento"
        return base

    if item and item in GENERIC_ITEMS:
        base["motivo_decisao"] = f"item genérico {item} nunca prova evento"
        return base

    # ── guarda de FORMA, antes de qualquer ramo por evento ──
    # Precisa vir aqui, não no fim: no run 31143302974 ela estava depois do ramo
    # de `troca_ceo` e por isso nunca era alcançada — a divulgação de segmento
    # do 10-Q da Halliburton ("our chief operating decision maker (CODM) is
    # Jeffrey Miller, ... Chief Executive Officer", ASC 280) passou como troca
    # de CEO com confiança ALTA. Formulário periódico corrobora, não prova.
    if ev in MATERIAL_EVENTS and filing.get("form") in PERIODIC_FORMS and not item:
        achado_p = find_evidence(text, ev)
        if not achado_p:
            base["motivo_decisao"] = (f"sem evidência textual para '{ev}' em "
                                      f"{filing.get('form')}")
            return base
        base.update({"aceito": True, "decisao": "aceito_nao_pontuavel",
                     "confianca": "baixa", "nao_pontuavel_por_forma": True,
                     "evidence_text": achado_p["evidence_text"],
                     "evidence_match": achado_p["evidence_match"],
                     **_janela(bruto, achado_p),
                     "motivo_decisao": (f"{filing.get('form')} é relatório periódico: "
                                        f"corrobora '{ev}', não prova fato novo")})
        return base

    # ═══ TODAS as guardas ESTRUTURAIS ficam AQUI, antes de qualquer ramo por
    # evento. Três vezes nesta fase um guard colocado adiante ficou inalcançável
    # porque `troca_ceo`/`1.03` retornam cedo: o CODM da Halliburton (4H.3C), a
    # manchete pontuando (4H.3E) e o item sem seção (4H.3E). Guard novo entra
    # neste bloco, não depois. ═══

    # âncora fora de qualquer seção econômica → corrobora, não prova
    if ev in MATERIAL_EVENTS and candidate.get("fora_de_secao"):
        base.update({"aceito": True, "decisao": "aceito_nao_pontuavel",
                     "confianca": "baixa", "nao_pontuavel_por_forma": True,
                     "fora_de_secao": True,
                     "motivo_decisao": (f"'{ev}' sem seção econômica localizável "
                                        f"({candidate.get('cobertura') or 'sem seção'}) "
                                        f"— corrobora, não prova")})
        return base

    # ── guarda de SEÇÃO, junto das demais guardas estruturais ──
    # Precisa vir ANTES dos ramos por evento: colocada depois, o ramo de
    # `troca_ceo` retornava primeiro e a manchete voltava a pontuar. É o mesmo
    # erro de ordenação que deixou o CODM da Halliburton passar na 4H.3C.
    # Só seção com estrutura garantida por regra da SEC sustenta pontuável.
    _kind = _clean(candidate.get("section_kind"))
    if ev in MATERIAL_EVENTS and _kind and _kind not in _KINDS_PONTUAVEIS:
        base.update({"aceito": True, "decisao": "aceito_nao_pontuavel",
                     "confianca": "baixa", "nao_pontuavel_por_forma": True,
                     "section_kind": _kind,
                     "motivo_decisao": (f"seção '{_kind}' é heurística de layout, "
                                        f"não estrutura garantida pela SEC — "
                                        f"corrobora '{ev}', não prova")})
        return base

    # 5.02 é, por definição, sobre administradores: a pergunta não é "o texto
    # fala de executivo?" e sim "é executivo RELEVANTE e houve movimento?".
    # Avaliar antes da âncora genérica produz motivo de rejeição útil no CSV
    # ("eleição de conselheiro") em vez de um "sem evidência" opaco.
    if ev == "troca_ceo" and item == "5.02":
        ach502 = find_evidence(text, ev)
        jan502 = _janela(bruto, ach502)
        # análise de cargo na JANELA LOCAL, não no filing inteiro: rodar sobre
        # 60 mil chars fazia qualquer "appointed" distante validar o evento.
        det = analyze_officer_change(jan502.get("evidence_text") or text)
        base["officer_detail"] = det
        if ach502:
            base.update({"evidence_text": ach502["evidence_text"],
                         "evidence_match": ach502["evidence_match"], **jan502})
        if not det["suficiente"]:
            base["motivo_decisao"] = det["motivo"]
            return base
        base.update({"aceito": True, "decisao": "aceito", "confianca": "alta",
                     "motivo_decisao": (f"cargo relevante + movimento "
                                        f"(saiu={det['quem_saiu'] or '?'}, "
                                        f"entrou={det['quem_entrou'] or '?'})")})
        return base

    achado = find_evidence(text, ev)

    # 1.03 (Bankruptcy or Receivership) é o próprio fato jurídico: o item basta,
    # mas o texto ainda decide QUAL evento (chapter 11 → RJ, chapter 7 → falência).
    if forca == "forte" and item == "1.03":
        if ev == "recuperacao_judicial" and re.search(r"chapter\s*7", text, re.I) \
                and not re.search(r"chapter\s*11", text, re.I):
            base["motivo_decisao"] = "1.03 é chapter 7 (falência), não RJ"
            return base
        if ev == "falencia" and re.search(r"chapter\s*11", text, re.I) \
                and not re.search(r"chapter\s*7", text, re.I):
            base["motivo_decisao"] = "1.03 é chapter 11 (RJ), não falência"
            return base
        base.update({
            "aceito": True, "decisao": "aceito",
            "confianca": "alta" if achado else "media",
            "evidence_text": (achado or {}).get("evidence_text", f"item 1.03 ({filing.get('form')})"),
            "evidence_match": (achado or {}).get("evidence_match", "item 1.03"),
            **_janela(bruto, achado),
            "motivo_decisao": "item 1.03 é o próprio fato jurídico",
        })
        return base

    if not achado:
        base["motivo_decisao"] = (f"sem evidência textual para '{ev}' "
                                  f"({'corpo não recuperado' if not text else 'âncora ausente no texto'})")
        return base

    janela = _janela(bruto, achado)
    base.update({"evidence_text": achado["evidence_text"],
                 "evidence_match": achado["evidence_match"], **janela})

    # cláusula de contrato descrevendo hipótese ≠ fato ocorrido
    if ev in MATERIAL_EVENTS and is_contractual(
            janela.get("evidence_text") or achado["evidence_text"]):
        base.update({"aceito": True, "decisao": "aceito_nao_pontuavel",
                     "confianca": "baixa", "nao_pontuavel_por_forma": True,
                     "contexto_contratual": True,
                     "motivo_decisao": (f"'{ev}' aparece em linguagem contratual/"
                                        f"hipotética (covenant, definição, exhibit) "
                                        f"— não é fato ocorrido")})
        return base

    # 5.02 exige detalhe de cargo + movimento
    if ev == "troca_ceo":
        det = analyze_officer_change(janela.get("evidence_text") or text)
        base["officer_detail"] = det
        if not det["suficiente"]:
            base["motivo_decisao"] = det["motivo"]
            return base
        base.update({"aceito": True, "decisao": "aceito", "confianca": "alta",
                     "motivo_decisao": (f"cargo relevante + movimento "
                                        f"(saiu={det['quem_saiu'] or '?'}, "
                                        f"entrou={det['quem_entrou'] or '?'})")})
        return base

    # rating: "reafirmado/mantido" NÃO é downgrade — e "did not downgrade"
    # tampouco. Âncora negada é falso positivo clássico.
    if ev == "rebaixamento_rating":
        afirma = re.search(
            r"affirm\w+|reaffirm\w+|maintain\w+\s+(?:its\s+)?rating|unchanged|reiterat\w+",
            text, re.I)
        rebaixa = re.search(r"downgrad\w+|lower\w+\s+\w*\s*rating|rating\s+cut",
                            text, re.I)
        if afirma and not rebaixa:
            base["motivo_decisao"] = "rating reafirmado/mantido — não é rebaixamento"
            return base
        if _anchor_negada(text, achado):
            base["motivo_decisao"] = "âncora de rebaixamento aparece NEGADA no texto"
            return base

    # ── 4H.3E: fora de seção econômica não pontua ──
    # A varredura livre do documento inteiro foi a origem de praticamente todo
    # falso positivo do run 31193786617 (capa, assinatura, balanço, biografia).
    # A âncora encontrada fora de item/release continua VISÍVEL como
    # corroboração informativa, mas nunca sustenta evento pontuável.
    conf = "alta" if forca == "forte" else ("alta" if item else "media")
    base.update({"aceito": True, "decisao": "aceito", "confianca": conf,
                 "section_kind": candidate.get("section_kind", ""),
                 "motivo_decisao": f"evidência textual compatível com '{ev}'"})
    return base


def analyze_filing(filing: dict, text: str = "",
                   semantic_text: str | None = None,
                   sections: list[dict] | None = None) -> dict:
    """Pipeline canônico de um filing: candidatos → evidência → decisão.

    `text` é o BRUTO (`raw_document_text`); `semantic_text` é o derivado pelo
    normalizador source-aware. A busca de âncora roda no semântico — para não
    tropeçar em boilerplate jurídico — mas a EVIDÊNCIA exibida sai sempre do
    bruto, porque a neutralização preserva comprimento e offsets.
    """
    raw = text or ""
    sem = raw if semantic_text is None else semantic_text
    cands = candidate_events(filing)

    # 4H.3E: o candidato vindo de ITEM também precisa ser avaliado DENTRO da
    # sua seção. Sem isto ele continuava buscando a âncora no documento
    # inteiro — e foi exatamente daí que vieram os 4 falsos `troca_ceo` do run
    # 31205791805, todos com evidência de capa ("indicate by check mark",
    # "Co-Registrant City Juno Beach").
    avaliados = []
    for c in cands:
        alvo = sem
        if sections is not None and c.get("item"):
            sec = next((s for s in sections
                        if s.get("kind") == "item" and s.get("item") == c["item"]),
                       None)
            if sec:
                alvo = _restrito(sem, sec["start_offset"], sec["end_offset"])
                c = {**c, "section_kind": "item",
                     "section_heading": sec.get("heading", "")}
            else:
                # O metadata da SEC declara o item, mas o texto extraído não
                # tem a seção correspondente — a estrutura de items não
                # sobrevive à conversão HTML→texto. Sem poder localizar a
                # evidência, não há como provar o evento: ele fica visível como
                # corroboração, nunca pontuável. Era daqui que vinham os 4
                # falsos `troca_ceo` do run 31205791805.
                c = {**c, "fora_de_secao": True,
                     "cobertura": "item declarado sem seção no texto"}
        avaliados.append(evaluate_candidate(filing, c, alvo, raw=raw))
    aceitos = [a for a in avaliados if a["aceito"]]

    # ── evento revelado pelo TEXTO (6-K não tem item; 8-K pode omitir) ──
    # 4H.3E: procura primeiro DENTRO das seções econômicas. Só cai na varredura
    # livre quando não há seção — e aí o candidato nasce marcado
    # `fora_de_secao`, o que o impede de pontuar.
    ja = {a["event_id"] for a in avaliados if a.get("event_id")}
    for ev in sorted(MATERIAL_EVENTS):
        if ev in ja:
            continue
        achado, fora = None, False
        if sections:
            achado = find_evidence_in_sections(sem, ev, sections)
        if not achado:
            achado = find_evidence(sem, ev)
            # "fora de seção" só faz sentido quando HOUVE segmentação. Sem
            # `sections`, marcar tudo como fora penalizava a linha de base e
            # tornava a comparação 4H.3D × 4H.3E inválida.
            fora = bool(achado) and sections is not None
        if not achado:
            continue
        extra = evaluate_candidate(filing, {
            "event_id": ev, "item": achado.get("section_item", ""),
            "form": filing.get("form", ""),
            # a origem reflete de ONDE a evidência veio: só é "secao_*" quando
            # a âncora foi de fato encontrada dentro de uma seção segmentada.
            "origem": ("secao_" + achado["section_kind"]
                       if achado.get("section_kind") else "texto_do_documento"),
            "forca": "candidato",
            "section_kind": achado.get("section_kind", ""),
            "section_heading": achado.get("section_heading", ""),
            "fora_de_secao": fora,
            "cobertura": "documento_inteiro" if fora else "por_secao",
            "motivo": f"âncora de '{ev}' em "
                      + ("varredura livre" if fora
                         else f"seção {achado.get('section_kind')}"),
        }, sem, raw=raw)
        avaliados.append(extra)
        if extra["aceito"]:
            aceitos.append(extra)

    return {
        "filing": filing,
        "candidatos": avaliados,
        "aceitos": aceitos,
        "event_ids": sorted({a["event_id"] for a in aceitos if a["event_id"]}),
        "tem_texto": bool(raw),
        "normalizado": semantic_text is not None,
        "secoes": len(sections or []),
        # `is not None`: houve segmentação, ainda que ela não tenha achado
        # nenhuma seção. Usar truthiness confundia "não segmentado" com
        # "segmentado e vazio" — e são coisas diferentes para a auditoria.
        "escopo": "por_secao" if sections is not None else "documento_inteiro",
    }


def to_article(filing: dict, text: str = "", analysis: dict | None = None,
               semantic_text: str | None = None) -> dict:
    """Converte o filing canônico em artigo do pipeline.

    Mantém a decisão 4H.3A: o filer entra como CANDIDATO (`candidate_companies`),
    NUNCA como `forced_companies`. Quem resolve o sujeito continua sendo
    `detect_companies`/`mention_role`/`semantic_audit`.
    """
    an = analysis or analyze_filing(filing, text, semantic_text)
    corpo = (text or "").strip()
    empresa = _clean(filing.get("company"))
    # O texto entregue ao classificador é o SEMÂNTICO — nunca o bruto com
    # boilerplate. Quando há evidência local, ela é o resumo: classificar a
    # janela em vez do filing inteiro é o ponto do §6 da 4H.3D.
    janelas = [a.get("evidence_text") for a in an["aceitos"] if a.get("evidence_text")]
    if janelas:
        resumo = " … ".join(dict.fromkeys(janelas))[:4000]
    elif semantic_text:
        resumo = re.sub(r"\s{2,}", " ", semantic_text).strip()[:4000]
    else:
        resumo = corpo[:4000] if corpo else _clean(filing.get("description"))
    return {
        "title": canonical_title(filing),
        "url": filing.get("url", ""),
        "pub_ts": filing.get("pub_ts"),
        "source": "SEC · EDGAR",
        "domain": "sec.gov",
        "summary": resumo,
        "language": "en",
        "forced_trust": "oficial",
        "filing_company": empresa,
        "source_company": empresa,
        "monitored_company": empresa,
        "candidate_companies": [empresa] if empresa else [],
        "form": filing.get("form", ""),
        "accession_number": filing.get("accession_number", ""),
        "accession_digits": filing.get("accession_digits", ""),
        "primary_document": filing.get("primary_document", ""),
        "filing_items": ", ".join(filing.get("items") or []),
        "filing_date": filing.get("filing_date", ""),
        "report_date": filing.get("report_date", ""),
        "cik": filing.get("cik", ""),
        "ticker": filing.get("ticker", ""),
        "provenance": "EDGAR",
        "edgar_candidates": an["candidatos"],
        "edgar_event_ids": an["event_ids"],
        "edgar_has_body": an["tem_texto"],
        # bruto e semântico coexistem: o bruto é a prova, o semântico é o que
        # se classifica. Nunca sobrescrever o bruto (4H.3D §2).
        "raw_document_text": corpo,
        "semantic_text": semantic_text or "",
        "edgar_normalized": bool(semantic_text),
    }


# ───────────────────────────────── deduplicação ──────────────────────────────
def occurrence_key(company: str, event_id: str, filing: dict) -> str:
    """Chave EXATA da ocorrência — usada só para dedup de documento idêntico.

    ATENÇÃO: NÃO usar para decidir corroboração contra notícia/RI. Medido no
    run 31143302974: entre 49 pares comparáveis (mesma empresa + mesma família)
    NENHUM tinha lag 0 — o menor era 1 dia. Igualdade exata de data produz
    0 corroborações por construção, não por ausência de fato. Para corroborar,
    usar `match_occurrence`.
    """
    data = economic_date(filing)
    return f"{_clean(company).lower()}|{_clean(event_id)}|{data}"


# ── data econômica ───────────────────────────────────────────────────────────
# `reportDate` NÃO é data do fato universalmente. Nos periódicos ele é o
# FECHAMENTO CONTÁBIL: no run 31143302974 todo 10-Q trazia report_date
# 2026-06-30 (fim do trimestre), qualquer que fosse o assunto. Usá-lo como
# identidade de ocorrência funde fatos distintos do mesmo trimestre.
_DATE_IN_TEXT = re.compile(
    r"\b(?:on|effective(?:\s+as\s+of)?|dated)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),?\s+(\d{4})", re.I)
_MESES = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def economic_date_from_text(text: str, near: int | None = None,
                            raio: int = 400) -> str:
    """Data econômica EXPLÍCITA do corpo ("On July 16, 2026, the Company…").

    Quando `near` é dado, prefere a data mais próxima da âncora do evento.
    """
    t = str(text or "")
    if not t:
        return ""
    cands = []
    for m in _DATE_IN_TEXT.finditer(t):
        mes = _MESES.get(m.group(1).lower())
        if not mes:
            continue
        try:
            d = datetime(int(m.group(3)), mes, int(m.group(2)), tzinfo=timezone.utc)
        except ValueError:
            continue
        cands.append((abs((near if near is not None else m.start()) - m.start()),
                      d.strftime("%Y-%m-%d")))
    if not cands:
        return ""
    if near is not None:
        cands = [c for c in cands if c[0] <= raio] or cands
    cands.sort()
    return cands[0][1]


#

# Um filing só pode descrever fato PRÓXIMO do seu protocolo. Sem esse limite, a
# recitação do contrato original ("dated as of July 28, 2025") vira a data
# econômica do 8-K de conclusão de 2026 — foi o que aconteceu no run
# 31143754520: Baker Hughes/Chart Industries casou com lag de 354 dias.
MAX_DIAS_DATA_EXPLICITA = 120


def economic_date(filing: dict, text: str = "", near: int | None = None) -> str:
    """Data do FATO, por ordem de confiabilidade (§2 do pedido 4H.3C):

    1. data econômica explícita no corpo, DESDE QUE plausível em relação ao
       protocolo (ver `MAX_DIAS_DATA_EXPLICITA`);
    2. `filing_date` (protocolo — sempre existe);
    3. `report_date` APENAS quando semanticamente aplicável — nunca em
       formulário periódico, onde ele é fechamento contábil.
    """
    form = _clean(filing.get("form"))
    fdate = _clean(filing.get("filing_date"))
    fallback = (fdate if form in PERIODIC_FORMS
                else (_clean(filing.get("report_date")) or fdate))

    explicita = economic_date_from_text(text, near) if text else ""
    if not explicita:
        return fallback
    d = _dias(explicita, fdate)
    if d is None or d > MAX_DIAS_DATA_EXPLICITA:
        # data do corpo é recitação de contrato antigo / referência histórica
        return fallback
    return explicita


# ── impressão digital de contraparte ─────────────────────────────────────────
_STOP_ENT = frozenset("""
the company companies inc incorporated corp corporation ltd limited llc lp plc
sa s.a holdings holding group co nv ag the board of directors common stock
new york stock exchange nasdaq securities and exchange commission form item
united states january february march april may june july august september
october november december chief executive officer financial president
""".split())
_ENTIDADE = re.compile(r"\b([A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,3})\b")


def entity_fingerprint(text: str, exclude: list[str] | None = None) -> set[str]:
    """Entidades/pessoas próprias citadas — a CONTRAPARTE do fato.

    É o que distingue "Baker Hughes adquire Chart Industries" de qualquer
    outra aquisição da Baker Hughes no mesmo trimestre. Sem isso, uma janela
    de dias funde ocorrências economicamente distintas.
    """
    ex = {_clean(e).lower() for e in (exclude or []) if _clean(e)}
    ex_tokens = {w for e in ex for w in e.split()}
    out = set()
    for m in _ENTIDADE.finditer(str(text or "")):
        frag = m.group(1).strip()
        toks = [w.strip(".,;&").lower() for w in frag.split()]
        toks = [w for w in toks if w and w not in _STOP_ENT and w not in ex_tokens]
        if not toks:
            continue
        nome = " ".join(toks)
        if len(nome) < 4 or nome in ex:
            continue
        out.add(nome)
    return out


# Tolerância por família, CALIBRADA nos lags medidos no run 31143302974
# (|lag| observado: 1,3,4×4,7,8,9,11×2,12×2,13×2,17,18×2,19,20,22,23×3,24×2,…).
# A janela sozinha NÃO decide nada: ela é filtro secundário, aplicado só depois
# que a contraparte bate. M&A é a mais larga porque anúncio, assinatura e
# fechamento são datas distintas do MESMO fato econômico.
TOLERANCIA_DIAS = {
    "ma": 30, "troca_ceo": 10, "rebaixamento_rating": 3, "rating_elevado": 3,
    "recuperacao_judicial": 7, "falencia": 7, "default": 7, "covenant_breach": 7,
    "emissao_divida": 15, "fraude": 15, "investigacao_regulatoria": 15,
    "incidente_operacional": 7, "suspensao_negociacao": 5, "renuncia_auditor": 10,
}
TOLERANCIA_PADRAO = 7


def _dias(a: str, b: str) -> int | None:
    try:
        da = datetime.strptime(_clean(a), "%Y-%m-%d")
        db = datetime.strptime(_clean(b), "%Y-%m-%d")
    except Exception:
        return None
    return abs((da - db).days)


def match_occurrence(company: str, event_id: str, data_edgar: str,
                     fingerprint: set[str], conhecidas: list[dict]) -> dict:
    """Matching HIERÁRQUICO contra ocorrências já conhecidas (notícia/RI).

    Nível 1 — forte: mesma empresa + mesma família + contraparte em comum +
                     datas dentro da tolerância da família.
    Nível 2 — provável: mesma empresa + mesma família + contraparte em comum,
                     porém fora da tolerância (fato x filing distantes).
    Rejeitado: mesma empresa + mesma família apenas, sem contraparte comum —
                     proximidade temporal NUNCA basta.
    """
    emp = _clean(company).lower()
    tol = TOLERANCIA_DIAS.get(event_id, TOLERANCIA_PADRAO)
    rejeitados = []
    melhor = None

    for oc in conhecidas:
        if _clean(oc.get("company")).lower() != emp:
            continue
        if _clean(oc.get("event_id")) != _clean(event_id):
            continue
        comum = fingerprint & set(oc.get("fingerprint") or set())
        lag = _dias(data_edgar, oc.get("date", ""))
        if not comum:
            rejeitados.append({
                "occurrence_id": oc.get("occurrence_id", ""), "lag": lag,
                "motivo": "mesma empresa e família, sem contraparte em comum — "
                          "proximidade temporal não basta"})
            continue
        nivel = 1 if (lag is not None and lag <= tol) else 2
        cand = {"nivel": nivel, "occurrence_id": oc.get("occurrence_id", ""),
                "lag": lag, "entidades_comuns": sorted(comum)[:4],
                "source": oc.get("source", ""), "title": oc.get("title", "")}
        if melhor is None or cand["nivel"] < melhor["nivel"] or (
                cand["nivel"] == melhor["nivel"]
                and (cand["lag"] or 10**6) < (melhor["lag"] or 10**6)):
            melhor = cand

    if melhor:
        return {"acao": "corroborar", "cria_ocorrencia": False, "match": melhor,
                "rejeitados": rejeitados,
                "motivo": (f"nível {melhor['nivel']}: contraparte em comum "
                           f"{melhor['entidades_comuns']}, lag {melhor['lag']}d "
                           f"(tolerância {tol}d)")}
    return {"acao": "nova_ocorrencia", "cria_ocorrencia": True, "match": None,
            "rejeitados": rejeitados,
            "motivo": ("nenhuma ocorrência conhecida com a mesma contraparte"
                       if rejeitados else "ocorrência não vista por outras fontes")}


def dedup_filings(filings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Remove o MESMO documento repetido (mesmo accession). Devolve
    (únicos, descartados)."""
    vistos: dict[str, dict] = {}
    dups: list[dict] = []
    for f in filings:
        k = f.get("accession_digits") or normalize_accession(f.get("accession_number", ""))
        if not k:
            k = f"{f.get('company')}|{f.get('form')}|{f.get('filing_date')}"
        if k in vistos:
            dups.append({**f, "duplicate_of": vistos[k].get("accession_number", "")})
            continue
        vistos[k] = f
    return list(vistos.values()), dups


def corroborates(existing_occurrences: dict, company: str, event_id: str,
                 filing: dict) -> dict:
    """EDGAR encontrou algo que Google News/RI já registraram?

    Se sim, o filing CORROBORA a ocorrência existente — acrescenta fonte
    oficial, nunca cria nova ocorrência nem score adicional (invariante 6).
    `existing_occurrences` mapeia `occurrence_key` → registro já conhecido.
    """
    k = occurrence_key(company, event_id, filing)
    if k in existing_occurrences:
        return {"acao": "corroborar", "occurrence_key": k,
                "existing_source": existing_occurrences[k].get("source", ""),
                "cria_ocorrencia": False,
                "motivo": "mesma ocorrência econômica já registrada por outra fonte"}
    return {"acao": "nova_ocorrencia", "occurrence_key": k,
            "existing_source": "", "cria_ocorrencia": True,
            "motivo": "ocorrência não vista por outras fontes"}
