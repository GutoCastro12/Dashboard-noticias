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


def fetch_document_text(url: str, fetcher, *, max_chars: int = 60000) -> str:
    """Busca LIMITADA do corpo do filing.

    `fetcher(url) -> str` é injetado (a suíte passa um stub; produção passa o
    cliente HTTP real com o User-Agent exigido pela SEC). Falha de rede devolve
    string vazia — ausência de evidência, nunca evento presumido.
    """
    if not url or fetcher is None:
        return ""
    try:
        raw = fetcher(url)
    except Exception:
        return ""
    if not raw:
        return ""
    return strip_html(raw)[:max_chars]


def _window(text: str, m: re.Match, before: int = 90, after: int = 140) -> str:
    return text[max(0, m.start() - before):min(len(text), m.end() + after)].strip()


def find_evidence(text: str, event_id: str) -> dict | None:
    """Procura âncora textual do evento. Devolve trecho e offsets, ou None."""
    pats = EVENT_EVIDENCE.get(event_id)
    if not pats or not text:
        return None
    rx = re.compile("|".join(pats), re.I)
    m = rx.search(text)
    if not m:
        return None
    return {"evidence_text": _window(text, m), "evidence_match": m.group(0),
            "evidence_start": m.start(), "evidence_end": m.end()}


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


def evaluate_candidate(filing: dict, candidate: dict, text: str) -> dict:
    """Decide um candidato à luz da EVIDÊNCIA. Não pontua: só qualifica."""
    ev = _clean(candidate.get("event_id"))
    item = _clean(candidate.get("item"))
    forca = _clean(candidate.get("forca"))
    base = {
        **candidate,
        "evidence_text": "", "evidence_match": "",
        "aceito": False, "confianca": "nenhuma", "decisao": "rejeitado",
    }

    if not ev:
        base["motivo_decisao"] = candidate.get("motivo") or "sem candidato de evento"
        return base

    if item and item in GENERIC_ITEMS:
        base["motivo_decisao"] = f"item genérico {item} nunca prova evento"
        return base

    # 5.02 é, por definição, sobre administradores: a pergunta não é "o texto
    # fala de executivo?" e sim "é executivo RELEVANTE e houve movimento?".
    # Avaliar antes da âncora genérica produz motivo de rejeição útil no CSV
    # ("eleição de conselheiro") em vez de um "sem evidência" opaco.
    if ev == "troca_ceo" and item == "5.02":
        det = analyze_officer_change(text)
        base["officer_detail"] = det
        ach502 = find_evidence(text, ev)
        if ach502:
            base.update({"evidence_text": ach502["evidence_text"],
                         "evidence_match": ach502["evidence_match"]})
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
            "motivo_decisao": "item 1.03 é o próprio fato jurídico",
        })
        return base

    if not achado:
        base["motivo_decisao"] = (f"sem evidência textual para '{ev}' "
                                  f"({'corpo não recuperado' if not text else 'âncora ausente no texto'})")
        return base

    base.update({"evidence_text": achado["evidence_text"],
                 "evidence_match": achado["evidence_match"]})

    # 5.02 exige detalhe de cargo + movimento
    if ev == "troca_ceo":
        det = analyze_officer_change(text)
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

    conf = "alta" if forca == "forte" else ("alta" if item else "media")
    if ev in MATERIAL_EVENTS and not item and filing.get("form") in PERIODIC_FORMS:
        conf = "baixa"
    base.update({"aceito": True, "decisao": "aceito", "confianca": conf,
                 "motivo_decisao": f"evidência textual compatível com '{ev}'"})
    return base


def analyze_filing(filing: dict, text: str = "") -> dict:
    """Pipeline canônico de um filing: candidatos → evidência → decisão."""
    cands = candidate_events(filing)
    avaliados = [evaluate_candidate(filing, c, text or "") for c in cands]
    aceitos = [a for a in avaliados if a["aceito"]]

    # texto pode revelar evento material que o item não anunciou (ex.: 6-K)
    ja = {a["event_id"] for a in avaliados if a.get("event_id")}
    for ev in sorted(MATERIAL_EVENTS):
        if ev in ja:
            continue
        achado = find_evidence(text or "", ev)
        if not achado:
            continue
        extra = evaluate_candidate(filing, {
            "event_id": ev, "item": "", "form": filing.get("form", ""),
            "origem": "texto_do_documento", "forca": "candidato",
            "motivo": f"âncora textual de '{ev}' sem item correspondente",
        }, text or "")
        avaliados.append(extra)
        if extra["aceito"]:
            aceitos.append(extra)

    return {
        "filing": filing,
        "candidatos": avaliados,
        "aceitos": aceitos,
        "event_ids": sorted({a["event_id"] for a in aceitos if a["event_id"]}),
        "tem_texto": bool(text),
    }


def to_article(filing: dict, text: str = "", analysis: dict | None = None) -> dict:
    """Converte o filing canônico em artigo do pipeline.

    Mantém a decisão 4H.3A: o filer entra como CANDIDATO (`candidate_companies`),
    NUNCA como `forced_companies`. Quem resolve o sujeito continua sendo
    `detect_companies`/`mention_role`/`semantic_audit`.
    """
    an = analysis or analyze_filing(filing, text)
    corpo = (text or "").strip()
    resumo = corpo[:4000] if corpo else _clean(filing.get("description"))
    empresa = _clean(filing.get("company"))
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
    }


# ───────────────────────────────── deduplicação ──────────────────────────────
def occurrence_key(company: str, event_id: str, filing: dict) -> str:
    """Chave da OCORRÊNCIA ECONÔMICA (não do documento).

    Deliberadamente NÃO inclui o accession: dois filings distintos sobre o
    mesmo fato (o 8-K e o 8-K/A, ou o 8-K e o 10-Q que o repete) são UMA
    ocorrência. Usa `report_date` quando existe — é a data do FATO, enquanto
    `filing_date` é a data do protocolo.
    """
    data = _clean(filing.get("report_date")) or _clean(filing.get("filing_date"))
    return f"{_clean(company).lower()}|{_clean(event_id)}|{data}"


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
