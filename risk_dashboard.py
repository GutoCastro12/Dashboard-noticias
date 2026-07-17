#!/usr/bin/env python3
"""
risk_dashboard.py
-----------------
Radar de Risco — monitor de notícias de emissores com classificação de
severidade e scoring agregado semanal.

Pipeline (mesma arquitetura do dashboard Irã x Israel):
 1. Lê um arquivo de configuração YAML
 2. Busca notícias no Google News RSS (por emissor da watchlist + buscas de mercado)
 3. Classifica cada notícia pela taxonomia de eventos (keywords, acento/caixa-insensível)
 4. Atribui score por evento e agrega por emissor na janela semanal
 5. Persiste histórico em JSON (agregação entre execuções)
 6. Gera um HTML estático interativo via Jinja2

Uso:
    python risk_dashboard.py --config config_risco.yaml          # execução normal
    python risk_dashboard.py --config config_risco.yaml --demo   # dados simulados
"""

import base64
import argparse
import csv
import difflib
import functools
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

try:
    import requests
except ImportError:
    sys.exit(" requests não encontrado. Rode: pip install requests")
try:
    import yaml
except ImportError:
    sys.exit(" pyyaml não encontrado. Rode: pip install pyyaml")
try:
    from jinja2 import Template
except ImportError:
    sys.exit(" jinja2 não encontrado. Rode: pip install jinja2")

# Gemini é opcional (classificação assistida por LLM, como no dashboard original)
try:
    import google.generativeai as genai
except ImportError:
    genai = None

SEVERITY_ORDER = {"critico": 0, "alto": 1, "medio": 2, "baixa": 3, "info": 3}
MARKET_LABEL = "Mercado (geral)"
SEVERITY_META = {
    "critico": {"emoji": "🔴", "label": "Crítico", "sub": "alerta imediato"},
    "alto":    {"emoji": "🟠", "label": "Alto impacto", "sub": ""},
    "medio":   {"emoji": "🟡", "label": "Médio", "sub": ""},
    "baixa":   {"emoji": "🔵", "label": "Baixa", "sub": ""},
    "info":    {"emoji": "🟢", "label": "Contexto positivo", "sub": "não pontua risco"},
}
DIRECTION_META = {
    "negativa": {"arrow": "▼", "label": "Negativa"},
    "neutra":   {"arrow": "◆", "label": "Neutra/incerta"},
    "positiva": {"arrow": "▲", "label": "Positiva"},
}
DIMENSION_LABELS = {
    "credito": "Crédito", "mercado": "Mercado", "liquidez": "Liquidez",
    "governanca": "Governança", "operacional": "Operacional", "regulatorio": "Regulatório",
}


def is_positive(ev: dict) -> bool:
    return ev.get("direction") == "positiva" or bool(ev.get("positive"))


def trust_of(domain: str, cfg: dict) -> tuple[str, float, str]:
    """Resolve (tier_id, peso, rótulo) do domínio pela config source_trust."""
    st = cfg.get("source_trust", {})
    tiers = st.get("tiers", {})
    d = (domain or "").lower().replace("www.", "")
    for tier_id, domains in (st.get("domains") or {}).items():
        for known in domains:
            k = known.lower()
            if d == k or d.endswith("." + k) or k in d:
                t = tiers.get(tier_id, {})
                return tier_id, t.get("weight", 1.0), t.get("label", tier_id)
    tier_id = st.get("default_tier", "outros")
    t = tiers.get(tier_id, {})
    return tier_id, t.get("weight", 0.6), t.get("label", tier_id)


def _domain_override(domain: str, cfg: dict) -> float | None:
    d = (domain or "").lower().replace("www.", "")
    for k, w in (cfg.get("source_trust", {}).get("overrides") or {}).items():
        k = k.lower()
        if d == k or d.endswith("." + k):
            return float(w)
    return None


CONFIRMATION_META = {
    "confirmado":  {"emoji": "🟢", "label": "Confirmado — fonte oficial"},
    "duas_fontes": {"emoji": "🟡", "label": "2+ fontes independentes"},
    "uma_fonte":   {"emoji": "🟠", "label": "Uma fonte confiável"},
    "rumor":       {"emoji": "🔴", "label": "Não confirmada / rumor"},
}


def confirmation_of(rec: dict, cfg: dict) -> str:
    """Nível de confirmação da INFORMAÇÃO (independente da gravidade do evento):
    🟢 fonte oficial (RI/CVM/B3/SEC) · 🟡 2+ fontes independentes confiáveis ·
    🟠 uma fonte confiável · 🔴 fonte não verificada sem corroboração."""
    tier, _, _ = trust_of_rec(rec, cfg)
    corr = rec.get("corroborations", []) or []
    trusted_corr = sum(1 for e in corr
                       if trust_of(e.get("domain", ""), cfg)[0] != "outros")
    if tier == "oficial":
        return "confirmado"
    if tier in ("agencia", "imprensa"):
        return "duas_fontes" if trusted_corr >= 1 else "uma_fonte"
    # fonte não verificada: sobe para 2 fontes se corroborada por confiáveis
    return "duas_fontes" if trusted_corr >= 2 else "rumor"


def trust_of_rec(rec: dict, cfg: dict) -> tuple[str, float, str]:
    """Confiança de um artigo/registro: tier forçado (feeds de RI/custom) tem
    prioridade; depois o domínio, com ajuste fino por veículo (overrides)."""
    forced = rec.get("trust_override") or rec.get("forced_trust")
    if forced:
        t = cfg.get("source_trust", {}).get("tiers", {}).get(forced, {})
        return forced, t.get("weight", 1.0), t.get("label", forced)
    tier_id, w, label = trust_of(rec.get("domain", ""), cfg)
    ov = _domain_override(rec.get("domain", ""), cfg)
    return tier_id, (ov if ov is not None else w), label

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={gl}:{ceid_lang}"
)


# ── utilidades ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Minúsculas e sem acentos, para matching de keywords."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def get_brt_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=3)


def fmt_date_br(dt: datetime) -> str:
    meses = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]
    return f"{dt.day:02d} {meses[dt.month - 1]} {dt.year} · {dt:%H:%M} BRT"


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f" Config não encontrada: {path}")
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("dashboard", "watchlist", "taxonomy", "scoring", "output"):
        if key not in cfg:
            sys.exit(f" Config inválida: seção '{key}' ausente.")
    return cfg


# ── Etapa 1: fetch (Google News RSS) ─────────────────────────────────────────

def clean_gnews_title(title: str, source: str = "") -> str:
    """Remove o sufixo ' - Veículo' que o Google News anexa ao título — ele
    varia por veículo e quebra a deduplicação por similaridade."""
    title = (title or "").strip()
    if source and title.lower().endswith(" - " + source.lower()):
        title = title[: -(len(source) + 3)].rstrip()
    elif " - " in title:
        head, _, tail = title.rpartition(" - ")
        # sufixo típico de veículo: curto e sem verbo/pontuação de frase
        if head and 0 < len(tail) <= 45 and len(tail.split()) <= 6:
            title = head.rstrip()
    # sufixo de seção do veículo (ex.: "… | Empresas", "… | Economia")
    if " | " in title:
        head, _, tail = title.rpartition(" | ")
        if head and 0 < len(tail) <= 30 and len(tail.split()) <= 3:
            title = head.rstrip()
    return title


def _parse_rss(xml_text: str, clean_titles: bool = True) -> list[dict]:
    """Converte o RSS do Google News em dicts normalizados de artigo."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return articles
    for item in root.iter("item"):
        title = (item.findtext("title") or "Sem título").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        if clean_titles:
            title = clean_gnews_title(title, source)

        # Descrição do Google News vem em HTML; extrai só o texto
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()

        pub_ts = 0
        pub_iso = ""
        try:
            pub_dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            pub_ts = int(pub_dt.timestamp())
            pub_iso = (pub_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        articles.append({
            "title": title,
            "url": link,
            "summary": desc,
            "source": source or domain_from_url(source_url or link),
            "domain": domain_from_url(source_url or link),
            "pub_ts": pub_ts,
            "pub_iso": pub_iso,
        })
    return articles


def fetch_query(query: str, cfg: dict, session: requests.Session) -> list[dict]:
    dash = cfg["dashboard"]
    lang = dash.get("language", "pt-BR")
    country = dash.get("country", "BR")
    period = dash.get("period", "7d")
    url = GOOGLE_NEWS_RSS.format(
        query=quote(f"{query} when:{period}"),
        hl=lang, gl=country, ceid_lang=lang.split("-")[0],
    )
    try:
        resp = session.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        print(f"   ⚠️  Falha na busca '{query[:50]}…': {exc}")
        return []
    arts = _parse_rss(resp.text)
    limit = dash.get("max_articles_per_query", 15)
    return arts[:limit]


RISK_TERMS_BY_TYPE = {
    # empresa listada (padrão)
    "empresa": [
        "recuperação judicial", "falência", "default", "rating",
        "covenant", "fraude", "CVM", "auditor", "CEO", "aquisição",
        "debêntures", "follow-on", "guidance", "resultado", "prejuízo",
    ],
    # fundo imobiliário / FIAGRO / FIP listado
    "fii": [
        "rendimento", "dividendo", "vacância", "inadimplência",
        "emissão de cotas", "fato relevante", "venda de ativo",
        "aluguel", "CRI", "laudo de avaliação", "amortização",
    ],
    # empresa fechada (private equity, crédito privado): sem ticker,
    # busca por nome com termos de crédito/reestruturação
    "nao_listada": [
        "recuperação judicial", "falência", "dívida", "credores",
        "reestruturação", "fraude", "CEO", "aquisição", "venda",
        "captação", "debêntures", "calote",
    ],
}


def build_company_query(company: dict, taxonomy: list[dict]) -> str:
    """Consulta ampla por emissor: nome + termos de risco da classe de ativo."""
    alias = company["aliases"][0] if company.get("aliases") else company["name"]
    ctype = company.get("type", "empresa")
    risk_terms = RISK_TERMS_BY_TYPE.get(ctype, RISK_TERMS_BY_TYPE["empresa"])
    terms = " OR ".join(f'"{t}"' for t in risk_terms)
    return f'"{alias}" ({terms})'


def should_fetch_company(company: dict, cfg: dict, run_count: int) -> bool:
    """Decide se o emissor entra nesta execução, conforme o tier."""
    tier = company.get("tier", 2)
    tier_cfg = (cfg.get("tiers") or {}).get(tier, {})
    n = tier_cfg.get("fetch_every_n_runs", 1)
    if n == 0:
        return bool(company.get("force_fetch"))
    return run_count % n == 0


def fetch_all(cfg: dict, run_count: int = 0) -> list[dict]:
    session = requests.Session()
    all_articles: list[dict] = []
    seen: set[str] = set()

    watch = cfg.get("watchlist", [])
    active = [c for c in watch if should_fetch_company(c, cfg, run_count)]
    skipped = len(watch) - len(active)
    print(f" 📡 Buscando notícias por emissor "
          f"({len(active)} nesta execução, {skipped} agendados p/ próximas runs)…")
    for company in active:
        q = build_company_query(company, cfg["taxonomy"])
        print(f"   • [T{company.get('tier', 2)}] {company['name']}")
        for art in fetch_query(q, cfg, session):
            if art["url"] and art["url"] not in seen:
                seen.add(art["url"])
                art["query_company"] = company["name"]
                all_articles.append(art)
        time.sleep(0.5)  # respeita o rate do RSS

    mq = cfg.get("market_queries", {})
    if mq.get("enabled"):
        print(" 📡 Buscas de mercado (empresas fora da watchlist)…")
        for q in mq.get("queries", []):
            print(f"   • {q}")
            for art in fetch_query(q, cfg, session):
                if art["url"] and art["url"] not in seen:
                    seen.add(art["url"])
                    art["query_company"] = None
                    all_articles.append(art)
            time.sleep(0.5)

    # Filtro opcional por fontes
    dash = cfg["dashboard"]
    if dash.get("restrict_to_sources") and dash.get("sources"):
        allowed = {d.lower().replace("www.", "") for d in dash["sources"]}
        before = len(all_articles)
        all_articles = [a for a in all_articles if a["domain"].lower() in allowed]
        print(f" 🔎 Filtro de fontes: {before} → {len(all_articles)} artigos")

    print(f" ✅ {len(all_articles)} artigos únicos coletados.")
    return all_articles


CVM_IPE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"


def fetch_cvm_fatos(cfg: dict) -> list[dict]:
    """Fatos relevantes da CVM (dados abertos, dataset IPE). RJ, waiver,
    renúncia de auditor e troca de comando saem primeiro aqui, antes da
    imprensa. A empresa vem do próprio protocolo (atribuição sem ambiguidade)."""
    cv = cfg.get("cvm_fatos_relevantes", {})
    if not cv.get("enabled"):
        return []

    year = datetime.now(timezone.utc).year
    lookback = cv.get("lookback_days", 7)
    categories = {normalize(c) for c in cv.get("categories", ["Fato Relevante"])}
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    print(f" 📡 Baixando protocolos IPE/CVM de {year} (fatos relevantes)…")
    try:
        resp = requests.get(CVM_IPE_URL.format(year=year), timeout=120,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
    except Exception as exc:
        print(f"   ⚠️  CVM indisponível nesta execução ({exc}). Seguindo só com notícias.")
        return []

    watch = cfg.get("watchlist", [])
    articles, seen = [], set()
    with zf.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter=";")
        for row in reader:
            if normalize(row.get("Categoria", "")) not in categories:
                continue
            entrega = (row.get("Data_Entrega") or "")[:10]
            if not entrega or entrega < cutoff_date:
                continue
            cia = normalize(row.get("Nome_Companhia", ""))
            assunto = (row.get("Assunto") or "").strip()
            if not cia or not assunto:
                continue

            company = None
            for c in watch:
                if any(_word_pattern(a).search(cia)
                       for a in c.get("aliases", []) + [c["name"]]):
                    company = c["name"]
                    break
            if not company:
                continue

            key = (company, normalize(assunto), entrega)
            if key in seen:
                continue
            seen.add(key)

            try:
                dt = datetime.strptime(entrega, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                pub_ts = int(dt.timestamp())
            except ValueError:
                pub_ts = 0
            articles.append({
                "title": f"[Fato Relevante] {company}: {assunto}",
                "url": row.get("Link_Download", "") or f"https://dados.cvm.gov.br/#{'-'.join(map(str, key))}",
                "summary": assunto,
                "source": "CVM · Fato Relevante",
                "domain": "cvm.gov.br",
                "pub_ts": pub_ts,
                "pub_iso": entrega,
                "forced_companies": [company],
            })
    print(f"   ✅ {len(articles)} fatos relevantes de emissores da watchlist.")
    return articles


def fetch_custom_feeds(cfg: dict) -> list[dict]:
    """Feeds RSS diretos, sem passar pelo Google News:
    • RI das empresas: 'ri_feeds' na watchlist → confiança 'oficial' (1.0) e
      atribuição forçada ao emissor. Chega antes da imprensa.
    • 'custom_feeds' genéricos (ex.: feed contratado de agência de rating)
      com tier configurável."""
    specs = []
    for c in cfg.get("watchlist", []):
        urls = list(c.get("ri_feeds", []) or [])
        off = c.get("official") or {}
        if off.get("rss"):
            urls.append(off["rss"])
        for url in urls:
            specs.append({"name": f"{c['name']} · RI", "url": url,
                          "trust_tier": "oficial", "company": c["name"]})
    for f in cfg.get("custom_feeds", []) or []:
        if f.get("url"):
            specs.append({"name": f.get("name", "Feed"), "url": f["url"],
                          "trust_tier": f.get("trust_tier", "imprensa"),
                          "company": f.get("company") or None})
    if not specs:
        return []

    print(f" 📡 Feeds diretos (RI/custom): {len(specs)} feed(s)…")
    articles, session = [], requests.Session()
    for spec in specs:
        try:
            resp = session.get(spec["url"], timeout=20,
                               headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except Exception as exc:
            print(f"   ⚠️  Feed '{spec['name']}' indisponível: {exc}")
            continue
        arts = _parse_rss(resp.text, clean_titles=False)
        for art in arts:
            art["source"] = spec["name"]
            art["domain"] = art["domain"] or domain_from_url(spec["url"])
            art["forced_trust"] = spec["trust_tier"]
            if spec["company"]:
                art["forced_companies"] = [spec["company"]]
        print(f"   • {spec['name']}: {len(arts)} itens")
        articles.extend(arts)
        time.sleep(0.3)
    return articles


MONTHS_PT = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
             "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
             "outubro": 10, "novembro": 11, "dezembro": 12}

_NAV_NOISE = re.compile(
    r"pol[ií]tica de privacidade|fale conosco|trabalhe conosco|termos de uso|"
    r"mapa do site|acessibilidade|cookies|newsletter|login|cadastr", re.I)
_NEWS_PATH = re.compile(
    r"noticia|comunicado|fato[s]?-?relevante|aviso|imprensa|press|release|"
    r"divulgacao|resultado|informe", re.I)


def _all_dates(text: str) -> list[tuple[int, int]]:
    """Todas as datas do texto como (posição, timestamp)."""
    out = []
    for m in re.finditer(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text):
        d, mo, y = map(int, m.groups())
        out.append((m.start(), (y, mo, d)))
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        y, mo, d = map(int, m.groups())
        out.append((m.start(), (y, mo, d)))
    for m in re.finditer(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", normalize(text)):
        if m.group(2) in MONTHS_PT:
            out.append((m.start(), (int(m.group(3)), MONTHS_PT[m.group(2)], int(m.group(1)))))
    dated = []
    for pos, (y, mo, d) in out:
        try:
            dated.append((pos, int(datetime(y, mo, d, 12, tzinfo=timezone.utc).timestamp())))
        except ValueError:
            continue
    return dated


def _parse_any_date(text: str) -> int | None:
    dates = _all_dates(text)
    return dates[0][1] if dates else None


def _discover_rss(html: str, base_url: str) -> list[str]:
    """Procura feeds RSS/Atom anunciados no <head> ou linkados na página."""
    urls = []
    for m in re.finditer(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', html, re.I):
        h = re.search(r'href="([^"]+)"', m.group(0))
        if h:
            urls.append(urljoin(base_url, h.group(1)))
    for m in re.finditer(r'href="([^"]*(?:/rss|/feed|\.rss|\.xml)[^"]*)"', html, re.I):
        u = urljoin(base_url, m.group(1))
        if "sitemap" not in u.lower() and u not in urls:
            urls.append(u)
    return urls[:3]


def _extract_anchors(html: str, base_url: str) -> list[tuple[str, str, int | None]]:
    """Manchetes de páginas HTML estáticas: âncoras com cara de notícia,
    com a data de publicação capturada do entorno do link quando existir."""
    items, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        text = re.sub(r"<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", text).strip()
        newsy = bool(_NEWS_PATH.search(href)) or bool(_NEWS_PATH.search(text))
        if _NAV_NOISE.search(text) or not text:
            continue
        # manchete: comprida o bastante; links "newsy" podem ser mais curtos
        if not (30 <= len(text) <= 220 or (newsy and 15 <= len(text) <= 220)):
            continue
        if len(text.split()) < (3 if newsy else 5):
            continue
        url = urljoin(base_url, href)
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        # data mais próxima ANTES da âncora (janela curta p/ não herdar do
        # item vizinho); senão, a primeira DEPOIS
        before = html[max(0, m.start() - 150): m.start()]
        after = html[m.end(): m.end() + 150]
        b = _all_dates(before)
        pub = b[-1][1] if b else _parse_any_date(after)
        items.append((text, url, pub))
        if len(items) >= 25:
            break
    return items


def _mine_embedded_json(html: str) -> list[tuple[str, str, int | None]]:
    """SPAs (Next.js/Nuxt e plataformas de RI) embutem os dados no HTML.
    Minera objetos JSON com cara de notícia (título + url/data) sem precisar
    renderizar JavaScript."""
    blobs = []
    for pat in (r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                r'<script[^>]+type="application/(?:ld\+)?json"[^>]*>(.*?)</script>'):
        blobs += re.findall(pat, html, re.S | re.I)
    items, seen = [], set()

    def walk(node):
        if len(items) >= 25:
            return
        if isinstance(node, dict):
            title = next((str(node[k]) for k in
                          ("title", "titulo", "headline", "nome", "name", "assunto")
                          if isinstance(node.get(k), str)), None)
            if title and 20 <= len(title.strip()) <= 220 and len(title.split()) >= 4:
                url = next((str(node[k]) for k in ("url", "link", "slug", "permalink")
                            if isinstance(node.get(k), str)), "")
                date_raw = next((str(node[k]) for k in
                                 ("date", "data", "publishedAt", "published_at",
                                  "datePublished", "createdAt") if node.get(k)), "")
                key = normalize(title)
                if key not in seen:
                    seen.add(key)
                    items.append((title.strip(), url, _parse_any_date(date_raw)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for blob in blobs:
        try:
            walk(json.loads(blob))
        except Exception:
            continue
    return items


def _render_with_browser(url: str, cfg: dict) -> str | None:
    """Último recurso: renderiza a página com Chromium headless (Playwright).
    Requer 'pip install playwright && playwright install chromium' — o
    workflow do GitHub Actions já faz isso quando ri_scraper.use_browser."""
    rc = cfg.get("ri_scraper", {})
    if not rc.get("use_browser", True):
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("   ℹ️  Playwright não instalado — pulando renderização de páginas JS.")
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64)")
            page.goto(url, wait_until="networkidle",
                      timeout=rc.get("browser_timeout_s", 35) * 1000)
            html = page.content()
            browser.close()
        return html
    except Exception as exc:
        print(f"   ⚠️  Navegador falhou em {url[:60]}: {exc}")
        return None


def scrape_ri_page(company: str, url: str, cfg: dict,
                   session: requests.Session) -> tuple[list[dict], str]:
    """Raspagem multi-estratégia de uma página de RI:
    1) auto-descoberta de RSS anunciado na página → parse estruturado
    2) âncoras do HTML estático (com data do entorno)
    3) JSON embutido de SPAs (__NEXT_DATA__ / ld+json)
    4) renderização headless (Playwright) e repetição de 2-3
    Retorna (artigos, estratégia_usada)."""
    rc = cfg.get("ri_scraper", {})
    min_items = rc.get("min_items", 3)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    now_iso = get_brt_now().strftime("%Y-%m-%d %H:%M")

    def to_articles(raw: list[tuple[str, str, int | None]]) -> list[dict]:
        arts = []
        for title, link, pub_ts in raw:
            ts_ = pub_ts or now_ts
            arts.append({
                "title": title, "url": link or url, "summary": "",
                "source": f"{company} · RI",
                "domain": domain_from_url(link or url),
                "pub_ts": ts_,
                "pub_iso": (datetime.fromtimestamp(ts_, tz=timezone.utc)
                            - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"),
                "forced_trust": "oficial", "forced_companies": [company],
            })
        return arts

    html = None
    try:
        resp = session.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        if "charset" not in (resp.headers.get("Content-Type") or "").lower():
            resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception as exc:
        # 403/anti-bot em requests simples não significa que o site está
        # inacessível — um navegador de verdade (passo 4) costuma passar.
        # Só desiste de vez se o headless também falhar/estiver desligado.
        print(f"   ⚠️  RI de {company} via requests indisponível ({exc}); "
              "tentando navegador headless…")
        rendered = _render_with_browser(url, cfg)
        if not rendered:
            return [], "erro"
        raw = _extract_anchors(rendered, url)
        if len(raw) < min_items:
            raw += [x for x in _mine_embedded_json(rendered)
                    if normalize(x[0]) not in {normalize(t) for t, _, _ in raw}]
        return (to_articles(raw), "navegador-headless") if raw else ([], "erro")

    # 1) RSS anunciado na própria página
    for rss_url in _discover_rss(html, url):
        try:
            r = session.get(rss_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            arts = _parse_rss(r.text, clean_titles=False)
            if len(arts) >= 1:
                for a in arts:
                    a.update({"source": f"{company} · RI",
                              "forced_trust": "oficial",
                              "forced_companies": [company]})
                return arts[:25], f"rss-descoberto ({rss_url[:50]}…)"
        except Exception:
            continue

    # 2) HTML estático + 3) JSON embutido
    raw = _extract_anchors(html, url)
    if len(raw) < min_items:
        raw += [x for x in _mine_embedded_json(html)
                if normalize(x[0]) not in {normalize(t) for t, _, _ in raw}]
    if len(raw) >= min_items:
        return to_articles(raw), "html-estatico/json-embutido"

    # 4) renderização headless para SPAs
    rendered = _render_with_browser(url, cfg)
    if rendered:
        raw = _extract_anchors(rendered, url)
        if len(raw) < min_items:
            raw += [x for x in _mine_embedded_json(rendered)
                    if normalize(x[0]) not in {normalize(t) for t, _, _ in raw}]
        if raw:
            return to_articles(raw), "navegador-headless"
    return to_articles(raw), "parcial"


def discover_news_url(home_url: str, cfg: dict,
                      session: requests.Session) -> list[str]:
    """Resiliência a mudanças de caminho: varre os links da home do RI e
    retorna candidatos a seção de notícias, ranqueados pela ordem de
    ri_scraper.preferred_paths (fatos-relevantes > comunicados > …)."""
    paths = cfg.get("ri_scraper", {}).get("preferred_paths", [
        "fatos-relevantes", "comunicados", "noticias", "resultados"])
    try:
        resp = session.get(home_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        if "charset" not in (resp.headers.get("Content-Type") or "").lower():
            resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception:
        return []
    hrefs = {urljoin(home_url, h) for h in re.findall(r'href="([^"#]+)"', html)}
    home_domain = domain_from_url(home_url)
    ranked = []
    for u in hrefs:
        if domain_from_url(u) != home_domain:
            continue
        path = normalize(urlparse(u).path)
        for prio, term in enumerate(paths):
            if normalize(term) in path:
                ranked.append((prio, len(path), u))
                break
    ranked.sort()
    return [u for _, _, u in ranked[:4]]


def fetch_ri_news_pages(cfg: dict) -> list[dict]:
    """Coleta multi-estratégia das páginas de notícias de RI (official.news)."""
    pages = [(c["name"], (c.get("official") or {}).get("news"))
             for c in cfg.get("watchlist", [])]
    pages = [(n, u) for n, u in pages if u]
    if not pages:
        return []
    homes = {c["name"]: (c.get("official") or {}).get("ri")
             for c in cfg.get("watchlist", [])}
    min_items = cfg.get("ri_scraper", {}).get("min_items", 3)
    print(f" 📡 Páginas de notícias de RI: {len(pages)} página(s)…")
    articles, session = [], requests.Session()
    for company, url in pages:
        arts, strategy = scrape_ri_page(company, url, cfg, session)
        # URL configurada quebrou/rendeu pouco → localiza a seção pela home
        home = homes.get(company)
        if len(arts) < min_items and home and home.rstrip("/") != url.rstrip("/"):
            for cand in discover_news_url(home, cfg, session):
                if cand.rstrip("/") == url.rstrip("/"):
                    continue
                alt, alt_strat = scrape_ri_page(company, cand, cfg, session)
                if len(alt) > len(arts):
                    arts = alt
                    strategy = f"auto-localizado ({urlparse(cand).path}) via {alt_strat}"
                if len(arts) >= min_items:
                    break
        print(f"   • {company}: {len(arts)} itens via {strategy}")
        articles.extend(arts)
        time.sleep(0.5)
    return articles


# ── Etapa 2: classificação ───────────────────────────────────────────────────

@functools.lru_cache(maxsize=2048)
def _word_pattern(term: str) -> re.Pattern:
    """Regex de termo por palavra inteira sobre texto normalizado — evita que
    'OPA' case com 'opaco' ou 'RJ' com 'RJU', por exemplo."""
    return re.compile(r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)")


def classify_article(art: dict, taxonomy: list[dict]) -> list[dict]:
    """Retorna lista de eventos da taxonomia detectados no título+resumo,
    por palavra inteira e sem acento/caixa. Eventos com 'suppresses'
    removem os genéricos quando ambos disparam (ex.: 'Pequenas aquisições'
    suprime 'M&A'; 'Outlook positivo' suprime 'Outlook negativo')."""
    text = normalize(f"{art.get('title','')} {art.get('summary','')}")
    hits = []
    for event in taxonomy:
        matched = any(_word_pattern(kw).search(text) for kw in event.get("keywords", []))
        if not matched:
            continue
        # guardas de negação: "evitar RJ", "afasta rebaixamento", "deixou a RJ
        # para trás"… — a manchete cita o evento para negá-lo ou dá-lo por superado
        if any(_word_pattern(ng).search(text) for ng in event.get("negations", [])):
            continue
        hits.append(event)
    suppressed = {eid for ev in hits for eid in ev.get("suppresses", [])}
    return [ev for ev in hits if ev["id"] not in suppressed]


def detect_companies(art: dict, watchlist: list[dict]) -> list[str]:
    """Detecta emissores da watchlist citados no TÍTULO (palavra inteira,
    sem acento/caixa). Título-apenas prioriza precisão sobre recall: evita
    atribuir a notícia a toda empresa citada de passagem no resumo."""
    if art.get("forced_companies"):
        return list(art["forced_companies"])
    title = normalize(art.get("title", ""))
    found = []
    for company in watchlist:
        patterns = company.get("aliases") or [company["name"]]
        for alias in patterns:
            if _word_pattern(alias).search(title):
                found.append(company["name"])
                break
    return found


def dedupe_articles(articles: list[dict], history: dict, cfg: dict) -> list[dict]:
    """Remove a mesma matéria replicada em vários veículos (título muito
    similar, mesmas empresas, datas próximas) — dentro do lote e contra o
    histórico. Mantém a ocorrência mais antiga (primeiro a publicar)."""
    dd = cfg.get("dedup", {})
    if not dd.get("enabled", True):
        return articles
    threshold = dd.get("similarity", 0.75)
    tok_threshold = dd.get("token_overlap", 0.50)
    ev_cfg = cfg.get("evolution", {})
    taxonomy_sev = {e["id"]: e.get("severity") for e in cfg.get("taxonomy", [])}

    def tokens(t: str) -> set[str]:
        return {w for w in re.findall(r"\w{4,}", normalize(t))}

    _ENTITY_STOP = {
        "grupo", "empresa", "empresas", "companhia", "banco", "brasil",
        "justica", "uniao", "governo", "entenda", "apos", "para", "fim",
        "marca", "pedido", "processo", "estado", "recorde", "veja", "como",
    }

    def entities(t: str) -> set[str]:
        """Nomes próprios do título (palavras capitalizadas, sem genéricos) —
        identificam o protagonista da notícia (ex.: 'Dolly', 'Ambipar')."""
        caps = re.findall(r"\b[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\w\u00C0-\u00FF]{3,}", t)
        return {normalize(w) for w in caps} - _ENTITY_STOP

    def ev_ids(x: dict) -> frozenset:
        if x.get("events"):
            return frozenset(e["id"] for e in x["events"])
        return frozenset(x.get("event_ids", []))

    def art_trust_w(x: dict) -> float:
        return trust_of_rec(x, cfg)[1]

    def is_official(x: dict) -> bool:
        return trust_of_rec(x, cfg)[0] == "oficial"

    def similar(a: dict, b: dict) -> bool:
        ta, tb = a["title"], b["title"]
        if difflib.SequenceMatcher(None, normalize(ta), normalize(tb)).ratio() >= threshold:
            return True
        wa, wb = tokens(ta), tokens(tb)
        if wa and wb and len(wa & wb) / len(wa | wb) >= tok_threshold:
            return True
        # Notícias de mercado: a mesma história (mesmo tipo de evento, mesmo
        # protagonista) reescrita por vários veículos — ex.: 15 manchetes
        # diferentes sobre a mesma falência.
        if not a.get("companies") and not b.get("companies"):
            if ev_ids(a) and ev_ids(a) == ev_ids(b) and entities(ta) & entities(tb):
                return True
        # Fonte oficial × imprensa: o mesmo fato anunciado pelo RI/CVM e
        # replicado pela mídia — mesmo emissor, mesmos eventos, ≤ 2 dias,
        # com um dos lados oficial → é o mesmo anúncio.
        comps_a = set(a.get("companies", []))
        comps_b = set(b.get("companies", []))
        if (comps_a & comps_b and ev_ids(a) and ev_ids(a) == ev_ids(b)
                and abs(a.get("pub_ts", 0) - b.get("pub_ts", 0)) <= 2 * 86400
                and (is_official(a) or is_official(b))):
            return True
        # Mesma história entre veículos de imprensa: mesmo emissor + exatamente
        # o(s) mesmo(s) evento(s) + janela curta. Cobre 3 jornais noticiando a
        # mesma RJ com títulos diferentes ("pede RJ" / "Justiça aceita RJ" /
        # "entra em recuperação"). Só para eventos graves (crítico/alto), onde
        # a cobertura múltipla do mesmo fato é comum e não queremos contar 3×.
        same_window = ev_cfg.get("same_event_window_days", 10)
        if (comps_a & comps_b and ev_ids(a) and ev_ids(a) == ev_ids(b)
                and abs(a.get("pub_ts", 0) - b.get("pub_ts", 0)) <= same_window * 86400):
            sev = {e.get("severity") for e in (a.get("events") or [])
                   if isinstance(e, dict)}
            sev |= {taxonomy_sev.get(eid) for eid in ev_ids(a)}
            if sev & {"critico", "alto"}:
                return True
        return False

    def comparable(comps_a: set[str], comps_b: set[str]) -> bool:
        # mesmas empresas OU ambas sem empresa (notícias de mercado)
        return bool(comps_a & comps_b) or (not comps_a and not comps_b)

    def _persist_source(survivor: dict, dropped: dict) -> None:
        """Grava a fonte da duplicata como corroboração PERSISTIDA no registro
        sobrevivente (com horário), para que a evolução — que lê do histórico —
        consiga listar todas as fontes mesmo em execuções futuras."""
        srcs = survivor.setdefault("corrob_sources", [])
        dom = dropped.get("domain", "")
        if not dom or dom == survivor.get("domain"):
            return
        if any(s.get("domain") == dom for s in srcs):
            return
        when = ""
        if dropped.get("pub_ts"):
            when = (datetime.fromtimestamp(dropped["pub_ts"], tz=timezone.utc)
                    - timedelta(hours=3)).strftime("%d/%m %H:%M")
        srcs.append({"source": dropped.get("source", ""), "domain": dom,
                     "url": dropped.get("url", ""), "when": when})
        del srcs[8:]

    def add_corroboration(survivor: dict, dropped: dict) -> None:
        """A duplicata removida vira registro de corroboração do sobrevivente:
        'o mesmo fato foi reportado também por X' — insumo do confirmation_level."""
        corr = survivor.setdefault("corroborations", [])
        entry = {"source": dropped.get("source", ""),
                 "domain": dropped.get("domain", ""),
                 "url": dropped.get("url", "")}
        if entry["domain"] and entry["domain"] != survivor.get("domain") and \
           all(e["domain"] != entry["domain"] for e in corr):
            corr.append(entry)
        # herda corroborações que a duplicata já tinha acumulado
        for e in dropped.get("corroborations", []) or []:
            if e["domain"] != survivor.get("domain") and \
               all(x["domain"] != e["domain"] for x in corr):
                corr.append(e)
        del corr[8:]

    # referência: registros recentes do histórico (mesma empresa, ±3 dias)
    hist_recent = [r for r in history.get("articles", {}).values() if r.get("pub_ts")]

    kept: list[dict] = []
    removed = 0
    for art in sorted(articles, key=lambda a: a.get("pub_ts", 0)):
        comps = set(art.get("companies", []))
        dup = False
        for idx, other in enumerate(kept):
            if comparable(comps, set(other.get("companies", []))) and \
               abs(art.get("pub_ts", 0) - other.get("pub_ts", 0)) <= 3 * 86400 and \
               similar(art, other):
                dup = True
                if art_trust_w(art) > art_trust_w(other):
                    add_corroboration(art, other)
                    _persist_source(art, other)
                    kept[idx] = art  # fonte mais confiável sobrevive
                else:
                    add_corroboration(other, art)
                    _persist_source(other, art)
                break
        if not dup and art["url"] not in history.get("articles", {}):
            for rec in hist_recent:
                if comparable(comps, set(rec.get("companies", []))) and \
                   abs(art.get("pub_ts", 0) - rec.get("pub_ts", 0)) <= 3 * 86400 and \
                   similar(art, rec):
                    dup = True
                    if art_trust_w(art) > art_trust_w(rec):
                        # a versão mais confiável assume; a antiga vira
                        # corroboração persistida no NOVO registro
                        add_corroboration(art, rec)
                        _persist_source(art, rec)
                        history["articles"].pop(rec.get("url", ""), None)
                        dup = False
                    else:
                        # a duplicata nova vira corroboração persistida no
                        # registro que já está no histórico (grava de volta)
                        add_corroboration(rec, art)
                        _persist_source(rec, art)
                    break
        if dup:
            removed += 1
        else:
            kept.append(art)
    if removed:
        print(f" 🧹 Deduplicação: {removed} matéria(s) replicada(s) removida(s).")
    return kept


class GeminiModelUnavailable(Exception):
    """Modelo descontinuado/indisponível para a conta (404 'no longer
    available'). Tentar o mesmo modelo de novo é inútil — o chamador deve
    trocar para o próximo fallback ou abortar a análise LLM."""


class GeminiQuotaExhausted(Exception):
    """Cota DIÁRIA do free tier estourada — esperar não resolve dentro da
    mesma execução; melhor abortar as chamadas restantes (fail-open) do
    que gastar minutos em retries que vão falhar de novo, um por um."""


def _gemini_call(model, prompt: str, sleep_s: float):
    """Chamada com backoff só para limite POR MINUTO (transitório). Cota
    DIÁRIA (per-day) esgotada levanta GeminiQuotaExhausted imediatamente —
    o chamador deve parar de tentar outras empresas nesta execução."""
    for attempt in (1, 2):
        try:
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0, response_mime_type="application/json"),
                request_options={"timeout": 90},
            )
            time.sleep(sleep_s)
            return json.loads(resp.text)
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if "no longer available" in low or ("404" in msg and "model" in low):
                raise GeminiModelUnavailable(msg) from exc
            is_429 = "429" in msg or "quota" in msg.lower()
            is_daily = "perday" in msg.lower().replace(" ", "").replace("-", "")                        or "requests per day" in msg.lower()
            if is_429 and is_daily:
                raise GeminiQuotaExhausted(msg) from exc
            if attempt == 1 and is_429:
                print("   ⏳ Rate limit (por minuto) do Gemini — aguardando 30s…")
                time.sleep(30)
                continue
            raise


def consolidate_with_llm(articles: list[dict], cfg: dict, history: dict) -> list[dict]:
    """Análise em lote por emissor via Gemini. Para cada empresa com artigos
    novos classificados, uma única chamada: agrupa manchetes que cobrem o
    MESMO fato (dedup semântico), confirma os eventos que de fato ocorreram
    e verifica se a empresa é protagonista. Resolve o padrão que o dedup
    textual não pega: a mesma operação reescrita por N veículos, inflando
    todos os eventos ×N. Fail-open: erro da API mantém as keywords."""
    llm = cfg.get("llm", {})
    if not llm.get("enabled") or not llm.get("consolidate", True) or not genai:
        return articles
    api_key = llm.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(" " + "!" * 62)
        print(" ⚠️  ANÁLISE LLM INATIVA: secret GEMINI_API_KEY não configurado.")
        print("     Dupla contagem semântica e falsos positivos de atribuição")
        print("     NÃO serão filtrados. Configure em: GitHub → Settings →")
        print("     Secrets and variables → Actions → GEMINI_API_KEY")
        print(" " + "!" * 62)
        return articles

    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    sleep_s = llm.get("rpm_sleep_seconds", 6.5)

    # empresas com artigos NOVOS classificados (histórico já foi analisado)
    by_company: dict[str, list[dict]] = {}
    for art in articles:
        if not art.get("events") or art["url"] in history.get("articles", {}):
            continue
        for comp in art.get("companies", []):
            if comp != MARKET_LABEL:
                by_company.setdefault(comp, []).append(art)

    if not by_company:
        return articles

    genai.configure(api_key=api_key)
    model_names = [llm.get("model", "gemini-3-flash")] + \
                  list(llm.get("model_fallbacks", []) or [])
    model_idx = 0
    model = genai.GenerativeModel(model_names[0])
    print(f" 🤖 Análise por emissor via Gemini ({len(by_company)} empresa(s), "
          f"modelo {model_names[0]})…")

    drop_urls: dict[str, set[str]] = {}          # empresa -> urls duplicadas
    not_protag: dict[str, set[str]] = {}         # empresa -> urls onde não é protagonista
    confirmed: dict[tuple, set[str]] = {}        # (url, empresa) -> event_ids confirmados

    tax_desc = "\n".join(f"- {eid}: {e['label']}" for eid, e in taxonomy.items())
    quota_exhausted = False
    for comp, arts in by_company.items():
        if quota_exhausted:
            break
        arts_sorted = sorted(arts, key=lambda a: a.get("pub_ts", 0))
        lines = []
        for i, a in enumerate(arts_sorted):
            kws = ", ".join(e["id"] for e in a["events"])
            lines.append(f"[{i}] ({(a.get('pub_iso') or '')[:10]}) {a['title']} — "
                         f"{a.get('summary','')[:200]} | keywords: [{kws}]")
        prompt = (
            "Você é um analista sênior de risco de crédito. Abaixo estão "
            f"manchetes recentes sobre {comp}, coletadas de vários veículos, com "
            "classificação preliminar por palavras-chave.\n\n"
            "TAREFAS:\n"
            "1. AGRUPE manchetes que cobrem o MESMO fato subjacente (a mesma "
            "operação/anúncio reportado por veículos diferentes, mesmo com "
            "palavras totalmente diferentes), atribuindo o mesmo story_id.\n"
            "2. Para cada manchete, liste em event_ids APENAS os eventos que:\n"
            "   • de fato OCORRERAM (descarte especulação, negação, 'para "
            "evitar', condicional, caso antigo requentado);\n"
            f"   • têm {comp} como PROTAGONISTA (descarte se ela é apenas "
            "assessora, credora, investigadora, compradora citada de passagem "
            "ou vítima de terceiros);\n"
            "   • constam na taxonomia abaixo.\n"
            "3. Uma mesma operação pode gerar mais de um evento legítimo (ex.: "
            "aquisição financiada por emissão de dívida), mas NÃO infle: se a "
            "manchete trata só de uma emissão de debêntures, não marque também "
            "follow-on por causa da palavra 'captação'.\n"
            f"4. Se {comp} não é protagonista da manchete, use protagonista=false.\n\n"
            f"TAXONOMIA (id: rótulo):\n{tax_desc}\n\n"
            f"MANCHETES:\n" + "\n".join(lines) + "\n\n"
            'Responda APENAS JSON: {"analises": [{"i": 0, "story_id": "s1", '
            '"event_ids": ["..."], "protagonista": true}, ...]}'
        )
        analises = None
        while True:
            try:
                data = _gemini_call(model, prompt, sleep_s)
                analises = {int(x["i"]): x for x in data.get("analises", [])
                            if isinstance(x.get("i"), int)
                            or (isinstance(x.get("i"), str) and x["i"].isdigit())}
                break
            except GeminiModelUnavailable:
                model_idx += 1
                if model_idx < len(model_names):
                    print(f"   🔄 Modelo {model_names[model_idx-1]} indisponível "
                          f"nesta conta — trocando para {model_names[model_idx]}.")
                    model = genai.GenerativeModel(model_names[model_idx])
                    continue  # retenta a MESMA empresa com o próximo modelo
                print("   🛑 Nenhum modelo da lista está disponível para esta "
                      "conta (o Google reorganiza o free tier periodicamente). "
                      "Interrompendo a análise LLM desta execução (fail-open). "
                      "Atualize llm.model/llm.model_fallbacks no config — veja "
                      "os modelos vigentes em ai.google.dev/gemini-api/docs/models")
                quota_exhausted = True
                break
            except GeminiQuotaExhausted:
                remaining = len(by_company) - list(by_company).index(comp) - 1
                print(f"   🛑 Cota DIÁRIA do Gemini esgotada. Interrompendo a análise "
                      f"LLM aqui — as {remaining} empresa(s) restante(s) desta "
                      "execução seguem só com classificação por keyword (fail-open). "
                      "Normal ocorrer 1x/dia com o free tier em bases grandes.")
                quota_exhausted = True
                break
            except Exception as exc:
                print(f"   ⚠️  Análise falhou para {comp} (mantendo keywords): {exc}")
                break
        if analises is None:
            continue

        # dedup semântico: por story_id, mantém a manchete mais antiga
        first_of_story: dict[str, int] = {}
        for i, a in enumerate(arts_sorted):
            x = analises.get(i)
            if not x:
                continue
            sid = str(x.get("story_id", f"solo-{i}"))
            if not x.get("protagonista", True):
                not_protag.setdefault(comp, set()).add(a["url"])
                continue
            ids = {e for e in x.get("event_ids", []) if e in taxonomy}
            confirmed[(a["url"], comp)] = ids
            if sid in first_of_story:
                drop_urls.setdefault(comp, set()).add(a["url"])
                keeper = arts_sorted[first_of_story[sid]]
                corr = keeper.setdefault("corroborations", [])
                if a.get("domain") and a["domain"] != keeper.get("domain") and \
                   all(e["domain"] != a["domain"] for e in corr):
                    corr.append({"source": a.get("source", ""),
                                 "domain": a["domain"], "url": a.get("url", "")})
                print(f"   🔁 Mesma história p/ {comp}: '{a['title'][:48]}…' agrupada")
            else:
                first_of_story[sid] = i

    # aplica os vereditos artigo a artigo
    kept: list[dict] = []
    removed_dups = 0
    for art in articles:
        comps = art.get("companies", [])
        analyzed = [c for c in comps if (art["url"], c) in confirmed
                    or art["url"] in not_protag.get(c, set())
                    or art["url"] in drop_urls.get(c, set())]
        if not analyzed:
            kept.append(art)
            continue
        new_comps, event_ids = [], set()
        for c in comps:
            if art["url"] in not_protag.get(c, set()):
                print(f"   ✂️  {c} não é protagonista em '{art['title'][:45]}…'")
                continue
            if art["url"] in drop_urls.get(c, set()):
                continue
            new_comps.append(c)
            if (art["url"], c) in confirmed:
                event_ids |= confirmed[(art["url"], c)]
        if not new_comps:
            removed_dups += 1
            continue
        art["companies"] = new_comps
        if event_ids or any((art["url"], c) in confirmed for c in new_comps):
            dropped = [e["label"] for e in art["events"] if e["id"] not in event_ids]
            if dropped:
                print(f"   🚫 Descartado em '{art['title'][:45]}…': {', '.join(dropped)}")
            art["events"] = [taxonomy[eid] for eid in event_ids]
        if art["events"]:
            kept.append(art)
        else:
            removed_dups += 1
    if removed_dups:
        print(f" 🧹 Consolidação LLM: {removed_dups} artigo(s) removido(s) "
              "(duplicata semântica ou sem evento confirmado).")
    return kept


# ── Etapa 3: histórico e agregação ───────────────────────────────────────────

def load_history(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"articles": {}}


def save_history(path: Path, history: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def resolve_history_urls(history: dict, cfg: dict, budget: int = 40) -> None:
    """Resolve links do Google News em registros JÁ no histórico (gravados por
    execuções anteriores com o link-redirecionador). Faz no máximo `budget`
    por execução para não estourar o tempo/rate-limit — como roda 4x/dia, o
    passivo é limpo em poucas execuções e o que sobra é retentado depois."""
    resolved_cache = history.setdefault("resolved_urls", {})
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    pending = []
    for url, rec in history["articles"].items():
        if "news.google.com" not in url:
            continue
        entry = resolved_cache.get(url)
        if isinstance(entry, dict) and entry.get("exact"):
            # já resolvido antes → aplica e segue
            rec["url"] = entry["url"]
            continue
        pending.append((url, rec))
    if not pending:
        return
    pending = pending[:budget]
    print(f" 🔗 Corrigindo {len(pending)} link(s) antigo(s) do Google News no histórico…")
    fixed = 0
    for url, rec in pending:
        real = _resolve_one_gnews(url, rec.get("domain", ""), session)
        exact = bool(real and rec.get("domain", "") and
                     real.rstrip("/") != f"https://{rec['domain']}".rstrip("/"))
        resolved_cache[url] = {"url": real or "", "exact": exact}
        if real:
            rec["url"] = real   # atualiza o registro exibido
            if exact:
                fixed += 1
        time.sleep(0.4)
    print(f"   ✅ {fixed}/{len(pending)} corrigidos para o artigo direto.")


def resolve_google_news_urls(articles: list[dict], history: dict, cfg: dict) -> None:
    """Converte links news.google.com/rss/articles/… no URL real do veículo.

    Esses links são redirects que só funcionam no navegador de quem os abriu
    a partir do Google — para outras pessoas caem em 'Aviso de redirecionamento'.
    Resolve seguindo o redirect HTTP (Location) e, na falha, decodifica o
    base64 do token. Se nada funcionar, cai para a home do veículo (melhor que
    um link quebrado). Roda só nos artigos do dashboard, com cache no histórico."""
    resolved_cache = history.setdefault("resolved_urls", {})
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    # resolve os ainda não resolvidos E os que ficaram no fallback (home) numa
    # execução anterior — assim um rate-limit temporário do Google não deixa o
    # link imperfeito para sempre; a próxima run tenta de novo o artigo exato.
    def needs_resolve(a):
        u = a.get("url") or ""
        if "news.google.com" not in u:
            return False
        cached = resolved_cache.get(u)
        if cached is None:
            return True
        return isinstance(cached, dict) and not cached.get("exact")
    to_resolve = [a for a in articles if needs_resolve(a)]
    if to_resolve:
        print(f" 🔗 Resolvendo {len(to_resolve)} link(s) do Google News…")
    n_exact = 0
    for art in to_resolve:
        gurl = art["url"]
        real = _resolve_one_gnews(gurl, art.get("domain", ""), session)
        # link "exato" = artigo do veículo; fallback = só a home (marcado p/ retry)
        exact = bool(real and art.get("domain", "") and
                     real.rstrip("/") != f"https://{art['domain']}".rstrip("/"))
        resolved_cache[gurl] = {"url": real or "", "exact": exact}
        if exact:
            n_exact += 1
        time.sleep(0.4)  # gentil com o Google (evita 429)
    # aplica o cache a todos
    for art in articles:
        u = art.get("url") or ""
        entry = resolved_cache.get(u)
        if "news.google.com" in u and isinstance(entry, dict) and entry.get("url"):
            art["url"] = entry["url"]
    if to_resolve:
        print(f"   ✅ {n_exact}/{len(to_resolve)} resolvidos para o artigo direto "
              f"(demais usam a home do veículo e serão retentados na próxima run).")
    if len(resolved_cache) > 3000:
        for k in list(resolved_cache)[:len(resolved_cache) - 3000]:
            del resolved_cache[k]


def _resolve_one_gnews(gurl: str, domain: str, session: requests.Session) -> str | None:
    # 1) decodificação inline do base64 (rápida, sem rede) — funciona nos
    #    tokens que embutem o URL diretamente
    try:
        token = gurl.split("/articles/")[1].split("?")[0]
        pad = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(pad)
        m = re.search(rb"https?://[\w\-./%?=&#:+,~]+", raw)
        if m:
            cand = m.group(0).decode("utf-8", "ignore")
            if "google.com" not in cand and len(cand) > 15:
                return cand
    except Exception:
        pass
    # 2) método batchexecute: o Google embute os parâmetros de decodificação
    #    na página do artigo e resolve via endpoint interno. É o mais confiável
    #    para os tokens novos que a decodificação inline não pega.
    try:
        real = _gnews_batchexecute(gurl, session)
        if real and "google.com" not in real:
            return real
    except Exception:
        pass
    # 3) segue o redirect HTTP e lê a URL final
    try:
        r = session.get(gurl, timeout=15, allow_redirects=True)
        final = r.url
        if final and "google.com" not in final:
            return final
        m = re.search(r'data-n-au="([^"]+)"|url=(https?://[^"\'>]+)', r.text)
        if m:
            cand = m.group(1) or m.group(2)
            if cand and "google.com" not in cand:
                return cand
    except Exception:
        pass
    # 4) fallback: home do veículo (melhor que link quebrado)
    return f"https://{domain}" if domain and "google" not in domain else None


def _gnews_batchexecute(gurl: str, session: requests.Session) -> str | None:
    """Resolve o token via o endpoint interno batchexecute do Google News —
    o método que as bibliotecas especializadas usam e que funciona nos tokens
    atuais. Busca os parâmetros de decodificação na página do artigo e faz
    UMA chamada POST que retorna o URL real do veículo."""
    art_id = gurl.split("/articles/")[1].split("?")[0]
    # a página do artigo traz data-n-a-sg (assinatura) e data-n-a-ts (timestamp)
    page = session.get(gurl, timeout=15)
    sig = re.search(r'data-n-a-sg="([^"]+)"', page.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
    if not (sig and ts):
        return None
    payload = json.dumps([[[
        "Fbv4je",
        json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1,
                                    "US:en", None, 1, None, None, None, None,
                                    None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1,
                                   None, 0, 0, None, 0],
                    art_id, int(ts.group(1)), sig.group(1)]),
    ]]])
    resp = session.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        data={"f.req": payload},
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=15,
    )
    m = re.search(r'"(https?://[^"]+)"', resp.text.split('garturlres')[-1] if 'garturlres' in resp.text else resp.text)
    if m and "google.com" not in m.group(1):
        return m.group(1).encode().decode("unicode_escape")
    return None


def merge_into_history(history: dict, articles: list[dict], keep_days: int = 120) -> list[str]:
    added_urls: list[str] = []
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=keep_days)).timestamp())
    for art in articles:
        if art["url"] in history["articles"]:
            continue  # preserva a classificação original (já validada)
        if not art.get("events"):
            continue
        # Evento identificado mas nenhum emissor: entra no feed como notícia de mercado
        if not art.get("companies"):
            art["companies"] = [MARKET_LABEL]
        rec = {k: art.get(k) for k in
               ("title", "url", "summary", "source", "domain", "pub_ts", "pub_iso")}
        rec["companies"] = art["companies"]
        rec["event_ids"] = [e["id"] for e in art["events"]]
        if art.get("forced_trust"):
            rec["trust_override"] = art["forced_trust"]
        if art.get("corroborations"):
            rec["corroborations"] = art["corroborations"][:8]
        if art.get("corrob_sources"):
            rec["corrob_sources"] = art["corrob_sources"][:8]
        rec["captured_ts"] = int(datetime.now(timezone.utc).timestamp())
        rec["cap_iso"] = (get_brt_now()).strftime("%Y-%m-%d %H:%M")
        history["articles"][art["url"]] = rec
        added_urls.append(art["url"])
    # poda registros antigos
    history["articles"] = {
        u: r for u, r in history["articles"].items()
        if r.get("pub_ts", 0) >= cutoff
    }
    return added_urls


STATUS_META = {
    "critico":   {"label": "Crítico", "severity": "critico"},
    "atencao":   {"label": "Atenção elevada", "severity": "alto"},
    "monitorar": {"label": "Monitorar", "severity": "medio"},
}
STATUS_ORDER = {"critico": 0, "atencao": 1, "monitorar": 2}


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Percentil com interpolação linear (sem numpy)."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def calibrate_thresholds(history: dict, cfg: dict) -> dict:
    """Limiares de acumulação calibrados na distribuição REAL da carteira.

    Amostra: pico SEMANAL do score ponderado (decaimento × confiança) por
    emissor, apenas emissor-semanas com sinal (score > 0) — um episódio longo
    contribui poucos pontos, evitando a autocorrelação de amostrar o mesmo
    sinal 90 vezes. Limiar efetivo = percentil da amostra, TRAVADO na banda
    em torno do valor base (anti-normalização de crise sistêmica). Amostra
    insuficiente → valores base (modo 'base')."""
    st = cfg.get("evolution", {}).get("status", {})
    base_at, base_cr = st.get("atencao_total_min", 60), st.get("critico_total_min", 125)
    ad = st.get("adaptive", {})
    out = {"atencao": base_at, "critico": base_cr, "mode": "base",
           "sample_n": 0, "sample": []}
    if not ad.get("enabled"):
        return out

    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    decay_cfg = cfg.get("evolution", {}).get("decay", {})
    decay_on = decay_cfg.get("enabled", True)
    half_life = max(1, decay_cfg.get("half_life_days", 30))
    cal_days = ad.get("calibration_days", 90)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff = now_ts - cal_days * 86400

    # ocorrências negativas por emissor (score>0, direção não positiva)
    per_company: dict[str, list[tuple[str, float, int, float]]] = {}
    for rec in history["articles"].values():
        if rec.get("pub_ts", 0) < cutoff:
            continue
        _, t_w, _ = trust_of_rec(rec, cfg)
        for company in rec.get("companies", []):
            if company == MARKET_LABEL:
                continue
            for eid in rec.get("event_ids", []):
                ev = taxonomy.get(eid)
                if not ev or is_positive(ev) or ev["score"] <= 0:
                    continue
                per_company.setdefault(company, []).append(
                    (eid, float(ev["score"]), rec["pub_ts"], t_w))

    def total_as_of(occs, as_of_ts):
        best: dict[str, float] = {}
        for eid, score, pub_ts, t_w in occs:
            if pub_ts > as_of_ts or pub_ts < as_of_ts - cal_days * 86400:
                continue
            d = 0.5 ** (max(0.0, (as_of_ts - pub_ts) / 86400) / half_life) if decay_on else 1.0
            best[eid] = max(best.get(eid, 0.0), score * d * t_w)
        return sum(best.values())

    # pico semanal por emissor
    samples = []
    n_weeks = max(1, cal_days // 7)
    for occs in per_company.values():
        for w in range(n_weeks):
            week_end = now_ts - w * 7 * 86400
            peak = max((total_as_of(occs, week_end - d * 86400) for d in range(7)),
                       default=0.0)
            if peak > 0:
                samples.append(round(peak, 1))

    out["sample_n"] = len(samples)
    if len(samples) < ad.get("min_sample", 40):
        return out

    samples.sort()
    lo, hi = ad.get("band", [0.6, 1.5])
    at = _percentile(samples, ad.get("atencao_percentile", 75))
    cr = _percentile(samples, ad.get("critico_percentile", 95))
    at_eff = min(max(at, base_at * lo), base_at * hi)
    cr_eff = min(max(cr, base_cr * lo), base_cr * hi)
    cr_eff = max(cr_eff, at_eff * 1.4)  # crítico sempre bem acima de atenção
    out.update({"atencao": round(at_eff), "critico": round(cr_eff),
                "mode": "adaptativo", "sample": samples,
                "p_raw": {"atencao": round(at, 1), "critico": round(cr, 1)}})
    return out


def build_evolution(history: dict, cfg: dict, window_days: int | None = None,
                    thresholds: dict | None = None,
                    prev_scores: dict | None = None) -> list[dict]:
    """Radar de longo prazo: agrega os eventos de cada emissor na janela de
    evolução (padrão 90 dias), monta a timeline cronológica e classifica o
    status. Regras:
      • Score acumulado com DECAIMENTO por meia-vida — deterioração recente
        pesa mais que sinais antigos.
      • Fatos duros (evento com score bruto >= critico_event_min_score, como
        RJ/default/falência/fraude) mantêm o status Crítico sem envelhecer.
      • Eventos positivos (score 0) entram na timeline como contexto, mas
        não pontuam nem contam para o status.
      • Trajetória do score reconstruída dia a dia a partir do histórico,
        para a sparkline mostrar a INCLINAÇÃO da deterioração."""
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    meta = {c["name"]: {"tier": c.get("tier", 2), "type": c.get("type", "empresa")}
            for c in cfg.get("watchlist", [])}
    ev_cfg = cfg.get("evolution", {})
    if window_days is None:
        window_days = ev_cfg.get("window_days", 90)
    decay_cfg = ev_cfg.get("decay", {})
    decay_on = decay_cfg.get("enabled", True)
    half_life = max(1, decay_cfg.get("half_life_days", 30))
    st = ev_cfg.get("status", {})
    critico_event = st.get("critico_event_min_score", 90)
    th = thresholds or {}
    critico_total = th.get("critico", st.get("critico_total_min", 125))
    atencao_total = th.get("atencao", st.get("atencao_total_min", 60))
    cal_sample = th.get("sample", [])

    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff = now_ts - window_days * 86400

    def decay_weight(pub_ts: int, as_of_ts: int) -> float:
        if not decay_on:
            return 1.0
        days = max(0.0, (as_of_ts - pub_ts) / 86400)
        return 0.5 ** (days / half_life)

    per_company: dict[str, list[dict]] = {}
    for rec in history["articles"].values():
        if rec.get("pub_ts", 0) < cutoff:
            continue
        for company in rec.get("companies", []):
            if company == MARKET_LABEL:
                continue
            for eid in rec.get("event_ids", []):
                if eid not in taxonomy:
                    continue
                ev = taxonomy[eid]
                days_ago = max(0.0, (now_ts - rec["pub_ts"]) / 86400)
                t_id, t_w, t_label = trust_of_rec(rec, cfg)
                per_company.setdefault(company, []).append({
                    "event_id": eid,
                    "label": ev["label"],
                    "severity": ev["severity"],
                    "score": ev["score"],
                    "direction": ev.get("direction", "negativa"),
                    "dimensions": ev.get("dimensions", []),
                    "trust_w": t_w,
                    "trust_label": t_label,
                    "source": rec.get("source", ""),
                    "positive": is_positive(ev),
                    "pub_ts": rec["pub_ts"],
                    "date": (rec.get("pub_iso") or "")[:10],
                    "title": rec.get("title", ""),
                    "url": rec.get("url", ""),
                    "domain": rec.get("domain", ""),
                    "persisted_corrob": rec.get("corrob_sources", []),
                    "pos_pct": round(100.0 * (window_days - days_ago) / window_days, 2),
                    "opacity": round(0.35 + 0.65 * decay_weight(rec["pub_ts"], now_ts), 2),
                })

    # Dedup determinístico do MESMO evento (independe do LLM): várias notícias
    # do mesmo tipo de evento, para o mesmo emissor, dentro de uma janela curta
    # são a MESMA história — colapsam em 1 sinal (o de maior confiança), com as
    # demais fontes viradas corroboração. Sem isso, "RJ ×3" da mesma cobertura
    # infla a contagem de sinais e some com a credibilidade multi-fonte.
    collapse_days = ev_cfg.get("same_event_window_days", 10)
    for company, occs in list(per_company.items()):
        occs.sort(key=lambda o: (-o.get("trust_w", 1.0), o["pub_ts"]))
        merged: list[dict] = []
        for o in occs:
            twin = next((m for m in merged
                         if m["event_id"] == o["event_id"]
                         and abs(m["pub_ts"] - o["pub_ts"]) <= collapse_days * 86400), None)
            if twin is None:
                # começa já com as fontes corroborantes persistidas no histórico
                o["corrob"] = list(o.get("persisted_corrob", []))
                merged.append(o)
            else:
                dom = o.get("domain", "")
                if dom and dom != twin.get("domain") and \
                   all(c.get("domain") != dom for c in twin["corrob"]):
                    o_iso = (datetime.fromtimestamp(o["pub_ts"], tz=timezone.utc)
                             - timedelta(hours=3)).strftime("%d/%m %H:%M") if o.get("pub_ts") else ""
                    twin["corrob"].append({"source": o.get("source", ""),
                                           "domain": dom, "url": o.get("url", ""),
                                           "when": o_iso})
                # herda também as fontes que a duplicata já tinha persistido
                for c in o.get("persisted_corrob", []):
                    if c.get("domain") and c["domain"] != twin.get("domain") and \
                       all(x.get("domain") != c["domain"] for x in twin["corrob"]):
                        twin["corrob"].append(c)
        per_company[company] = merged

    def best_contribs(negatives: list[dict], as_of_ts: int) -> dict[str, dict]:
        """Por tipo de evento, a ocorrência de MAIOR contribuição até as_of_ts:
        contribuição = peso-base × decaimento × confiança da fonte + bônus de
        corroboração. Republicações NÃO multiplicam o score — a 1ª notícia dá o
        peso principal e cada fonte independente adicional dá um bônus pequeno e
        decrescente (config: corroboration_bonus), capado. Confirmação por vários
        veículos aumenta a confiança sem triplicar pontos."""
        bonus_steps = ev_cfg.get("corroboration_bonus", [4, 2, 1])
        best: dict[str, dict] = {}
        for o in negatives:
            if o["pub_ts"] > as_of_ts:
                continue
            d = decay_weight(o["pub_ts"], as_of_ts)
            base_contrib = o["score"] * d * o.get("trust_w", 1.0)
            n_extra = len(o.get("corrob", []))  # fontes além da principal
            bonus = sum(bonus_steps[i] if i < len(bonus_steps) else 0
                        for i in range(n_extra)) * d
            contrib = base_contrib + bonus
            cur = best.get(o["event_id"])
            if cur is None or contrib > cur["contrib"]:
                best[o["event_id"]] = {**o, "decay_f": d, "contrib": contrib,
                                       "base_contrib": round(base_contrib, 1),
                                       "corrob_bonus": round(bonus, 1)}
        return best

    def weighted_total(negatives: list[dict], as_of_ts: int) -> float:
        return sum(b["contrib"] for b in best_contribs(negatives, as_of_ts).values())

    rows = []
    for company, occurrences in per_company.items():
        occurrences.sort(key=lambda o: o["pub_ts"])
        negatives = [o for o in occurrences if not o["positive"]]
        if not negatives:
            continue  # só contexto positivo: não é linha de risco

        distinct: dict[str, dict] = {}
        for o in occurrences:  # chips resumem tudo, inclusive positivos
            d = distinct.setdefault(o["event_id"], {**o, "count": 0, "sources": 0})
            d["count"] += 1
            d["sources"] = max(d["sources"], 1 + len(o.get("corrob", [])))
        distinct_events = sorted(distinct.values(),
                                 key=lambda e: (SEVERITY_ORDER[e["severity"]], -e["score"]))

        total = weighted_total(negatives, now_ts)
        n_negative_types = len({o["event_id"] for o in negatives})
        has_hard_critical = any(o["score"] >= critico_event for o in negatives)

        # Decomposição auditável do score (o que compõe cada ponto)
        breakdown = []
        for b in sorted(best_contribs(negatives, now_ts).values(),
                        key=lambda x: -x["contrib"]):
            # fonte principal + corroborantes, com horário, para listar ao abrir
            src_ts = b.get("pub_ts", 0)
            src_iso = (datetime.fromtimestamp(src_ts, tz=timezone.utc)
                       - timedelta(hours=3)).strftime("%d/%m %H:%M") if src_ts else ""
            all_sources = [{"source": b["source"], "url": b.get("url", ""),
                            "when": src_iso, "trust": b.get("trust_label", ""),
                            "primary": True}]
            for c in b.get("corrob", []):
                all_sources.append({"source": c.get("source", ""),
                                    "url": c.get("url", ""), "when": c.get("when", ""),
                                    "trust": "", "primary": False})
            breakdown.append({
                "label": b["label"], "date": b["date"], "source": b["source"],
                "trust_label": b["trust_label"], "severity": b["severity"],
                "direction": b["direction"],
                "dimensions": [DIMENSION_LABELS.get(d, d) for d in b.get("dimensions", [])],
                "base": b["score"],
                "decay_f": round(b["decay_f"], 2),
                "trust_f": b.get("trust_w", 1.0),
                "base_contrib": b.get("base_contrib", round(b["contrib"], 1)),
                "corrob_bonus": b.get("corrob_bonus", 0),
                "contrib": round(b["contrib"], 1),
                "url": b["url"], "title": b["title"],
                "sources": 1 + len(b.get("corrob", [])),
                "all_sources": all_sources,
            })

        # Deterioração persistente: acúmulo de sinais negativos em janela curta
        pa = ev_cfg.get("persistence_alert", {})
        pa_days = pa.get("days", 45)
        pa_cutoff = now_ts - pa_days * 86400
        recent = [o for o in negatives if o["pub_ts"] >= pa_cutoff]
        persistent = (len(recent) >= pa.get("min_signals", 3)
                      and len({o["event_id"] for o in recent}) >= pa.get("min_types", 2))
        persistence_text = (f"{len(recent)} sinais negativos em {pa_days} dias"
                            if persistent else "")

        if has_hard_critical or total >= critico_total:
            status = "critico"
        elif persistent or total >= atencao_total or n_negative_types >= 2:
            status = "atencao"
        else:
            status = "monitorar"

        # Trajetória: score ponderado reconstruído em ~18 pontos da janela
        n_points = 18
        traj = []
        for i in range(n_points + 1):
            as_of = cutoff + int(window_days * 86400 * i / n_points)
            traj.append(weighted_total(negatives, as_of))
        max_traj = max(traj) or 1.0
        spark_points = " ".join(
            f"{round(100.0 * i / n_points, 1)},{round(24 - 20 * v / max_traj, 1)}"
            for i, v in enumerate(traj)
        )

        book_pct = None
        if cal_sample:
            below = sum(1 for v in cal_sample if v <= total)
            book_pct = round(100.0 * below / len(cal_sample))

        # variação vs execução anterior · evento principal · última notícia
        prev_score = (prev_scores or {}).get(company)
        score_delta = None if prev_score is None else round(total) - prev_score
        top_ev = max(best_contribs(negatives, now_ts).values(),
                     key=lambda b: b["contrib"], default=None)
        top_event_label = top_ev["label"] if top_ev else None
        # evento MAIS GRAVE (por severidade), distinto do de maior contribuição
        worst_ev = min(negatives, key=lambda o: (SEVERITY_ORDER[o["severity"]],
                                                 -o["score"]), default=None)
        worst_event_label = worst_ev["label"] if worst_ev else None
        worst_event_sev = worst_ev["severity"] if worst_ev else None
        n_critical_distinct = len({o["event_id"] for o in negatives
                                   if o["severity"] == "critico"})
        last_ts = max((o["pub_ts"] for o in occurrences), default=0)
        last_iso = (datetime.fromtimestamp(last_ts, tz=timezone.utc)
                    - timedelta(hours=3)).strftime("%d/%m %H:%M") if last_ts else ""
        last_ago_h = round((now_ts - last_ts) / 3600) if last_ts else None
        m = meta.get(company, {"tier": 2, "type": "empresa"})
        rows.append({
            "company": company,
            "tier": m["tier"],
            "type": m["type"],
            "book_pct": book_pct,
            "status": status,
            "total_score": round(total),
            "score_delta": score_delta,
            "top_event": top_event_label,
            "worst_event": worst_event_label,
            "worst_event_sev": worst_event_sev,
            "n_critical": n_critical_distinct,
            "last_news": last_iso,
            "last_ago_h": last_ago_h,
            "hard_critical": has_hard_critical,
            "events": distinct_events,
            "timeline": occurrences,
            "breakdown": breakdown,
            "persistent": persistent,
            "persistence_text": persistence_text,
            "spark_points": spark_points,
            "first_date": occurrences[0]["date"],
            "last_date": occurrences[-1]["date"],
        })
    # Ordenação (regra explícita, documentada na UI): score total desc →
    # desempate por evento mais grave → crítico mais recente → mais eventos
    # críticos distintos. Não ordena por nº de notícias (republicações do
    # mesmo fato não devem influenciar a posição).
    def sort_key(r):
        worst_sev_rank = SEVERITY_ORDER.get(r.get("worst_event_sev") or "baixa", 9)
        recency = -(r.get("last_ago_h") or 1e9)  # mais recente primeiro
        return (-r["total_score"], worst_sev_rank, recency, -r.get("n_critical", 0))
    rows.sort(key=sort_key)
    return rows


def aggregate_scores(history: dict, cfg: dict, window_days: int | None = None) -> list[dict]:
    """Score semanal por emissor: soma dos scores dos tipos de evento distintos
    na janela + bônus por intensidade (artigos repetidos do mesmo evento)."""
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    scoring = cfg["scoring"]
    if window_days is None:
        window_days = cfg["dashboard"].get("default_window", 7)
    attention_max = cfg["dashboard"].get("attention_max_window", 30)
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())

    per_company: dict[str, dict] = {}
    for rec in history["articles"].values():
        if rec.get("pub_ts", 0) < cutoff:
            continue
        for company in rec.get("companies", []):
            if company == MARKET_LABEL:
                continue  # notícias de mercado ficam no feed, mas não pontuam emissor
            entry = per_company.setdefault(company, {"events": {}, "articles": []})
            entry["articles"].append(rec)
            for eid in rec.get("event_ids", []):
                if eid in taxonomy:
                    entry["events"].setdefault(eid, []).append(rec)

    ranking = []
    meta = {c["name"]: {"tier": c.get("tier", 2), "type": c.get("type", "empresa")}
            for c in cfg.get("watchlist", [])}
    for company, entry in per_company.items():
        score = 0
        worst = "medio"
        event_chips = []
        for eid, recs in entry["events"].items():
            ev = taxonomy[eid]
            if is_positive(ev) or ev["score"] <= 0:
                continue  # direção positiva não pontua risco
            n = len(recs)
            trust_w = max(trust_of_rec(r, cfg)[1] for r in recs)
            if scoring.get("count_repeated_events"):
                base = round(ev["score"] * trust_w) * n
                extra = 0
            else:
                base = round(ev["score"] * trust_w)
                extra = min(scoring.get("extra_cap", 20),
                            scoring.get("extra_per_article", 5) * (n - 1))
            score += base + extra
            if SEVERITY_ORDER[ev["severity"]] < SEVERITY_ORDER[worst]:
                worst = ev["severity"]
            event_chips.append({
                "id": eid, "label": ev["label"], "severity": ev["severity"],
                "score": ev["score"], "count": n,
            })
        event_chips.sort(key=lambda c: (SEVERITY_ORDER[c["severity"]], -c["score"]))
        m = meta.get(company, {"tier": 2, "type": "empresa"})
        ranking.append({
            "company": company,
            "tier": m["tier"],
            "type": m["type"],
            "score": score,
            "worst_severity": worst,
            "events": event_chips,
            "n_articles": len({r["url"] for r in entry["articles"]}),
            "attention": (window_days <= attention_max
                          and score >= scoring.get("attention_threshold", 80)),
        })
    ranking = [r for r in ranking if r["score"] > 0]
    ranking.sort(key=lambda r: -r["score"])
    return ranking


def build_feed(history: dict, cfg: dict, window_days: int | None = None) -> list[dict]:
    """Lista de notícias da janela, com severidade/score por artigo."""
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    if window_days is None:
        window_days = cfg["dashboard"].get("default_window", 7)
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())

    feed = []
    for rec in history["articles"].values():
        if rec.get("pub_ts", 0) < cutoff:
            continue
        events = [taxonomy[eid] for eid in rec.get("event_ids", []) if eid in taxonomy]
        if not events:
            continue
        worst = min(events, key=lambda e: SEVERITY_ORDER[e["severity"]])
        t_id, t_w, t_label = trust_of_rec(rec, cfg)
        dims = sorted({DIMENSION_LABELS.get(d, d) for e in events
                       for d in e.get("dimensions", [])})
        feed.append({
            **rec,
            "events": [{"id": e["id"], "label": e["label"],
                        "severity": e["severity"], "score": e["score"],
                        "direction": e.get("direction", "negativa")} for e in events],
            "severity": worst["severity"],
            "score": max(e["score"] for e in events),
            "trust": t_id, "trust_label": t_label, "trust_w": t_w,
            "confirmation": confirmation_of(rec, cfg),
            "corroborations": [{"source": e.get("source", ""), "url": e.get("url", "")}
                               for e in (rec.get("corroborations") or [])[:5]],
            "dimensions": dims,
        })
    feed.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], -a["score"], -a.get("pub_ts", 0)))
    return feed


# ── Dados de demonstração ────────────────────────────────────────────────────

def demo_articles() -> list[dict]:
    """Notícias simuladas (marcadas como DEMO) espalhadas por 90 dias, para
    visualizar o radar semanal, a evolução por emissor e o feed."""
    now = datetime.now(timezone.utc)

    def ts(days_ago: float) -> tuple[int, str]:
        dt = now - timedelta(days=days_ago)
        return int(dt.timestamp()), (dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")

    raw = [
        # ── Hapvida: sequência clássica de deterioração (outlook → downgrade →
        #    covenant → resultado) ao longo de 90 dias → status Crítico
        ("Moody's revisa perspectiva da Hapvida para negativa e cita alavancagem",
         "Revisão de outlook para perspectiva negativa reflete sinistralidade elevada e geração de caixa fraca.",
         "Valor Econômico", "valor.globo.com", 75, None),
        ("S&P rebaixa rating da Hapvida após queima de caixa acima do esperado",
         "Agência rebaixa nota de crédito em um degrau e mantém viés negativo.",
         "InfoMoney", "infomoney.com.br", 42, None),
        ("Hapvida negocia waiver com credores após quebra de covenant de alavancagem",
         "Companhia descumpriu cláusula de dívida líquida/EBITDA e busca acordo com debenturistas.",
         "Brazil Journal", "braziljournal.com", 6, None),
        ("Lucro da Hapvida decepciona e fica bem abaixo das expectativas no 2T26",
         "Resultado decepciona analistas; sinistralidade segue pressionando margens.",
         "Money Times", "moneytimes.com.br", 4, None),

        # ── Natura: dois sinais em sequência → Atenção elevada
        ("Natura reduz guidance de margem para 2026 com câmbio pressionado",
         "Companhia corta projeção de margem EBITDA citando custos de insumos.",
         "Exame", "exame.com", 20, None),
        ("CEO deixa a Natura; conselho inicia busca por sucessor",
         "Troca de comando ocorre em meio à revisão do plano de reestruturação internacional.",
         "NeoFeed", "neofeed.com.br", 5, None),

        # ── Sinais isolados → Monitorar
        ("BRF e Marfrig avançam em fusão e convocam assembleias de acionistas",
         "Combinação de negócios criaria gigante global de proteínas; minoritários questionam relação de troca.",
         "Pipeline Valor", "pipelinevalor.globo.com", 30, None),
        ("Governo confirma troca de CEO na Petrobras; mercado reage com cautela",
         "Novo comando assume com discurso de disciplina de capital.",
         "Estadão", "estadao.com.br", 60, None),
        ("JPMorgan corta preço-alvo da Vale com minério de ferro mais fraco",
         "Banco reduz preço-alvo e mantém recomendação neutra para os ADRs.",
         "InfoMoney", "infomoney.com.br", 3, None),
        # mesma história em segundo veículo confiável → confirmação "2+ fontes"
        ("JPMorgan reduz preço-alvo da Vale citando minério de ferro fraco",
         "Banco corta preço-alvo dos ADRs e mantém recomendação neutra.",
         "Money Times", "moneytimes.com.br", 2.9, None),
        # fonte não verificada, sem corroboração → "rumor"
        ("Natura estudaria follow-on bilionário, dizem fontes de mercado",
         "Segundo pessoas a par do assunto, a companhia sondou bancos para uma oferta de ações.",
         "Radar do Mercado Blog", "radardomercado.blog.br", 1.5, None),
        ("Suzano capta US$ 1 bilhão em emissão de bonds de 10 anos",
         "Emissão de dívida teve demanda de 3x o book; recursos alongam o perfil.",
         "Bloomberg Línea", "bloomberglinea.com.br", 15, None),
        ("Novo marco regulatório de energia muda regras de concessão; Eletrobras é a mais afetada",
         "Mudança regulatória altera cálculo de indenizações do setor elétrico.",
         "Valor Econômico", "valor.globo.com", 10, None),
        ("CCR adquire participação em concessão de aeroportos na região Sul",
         "Aquisição minoritária amplia presença do grupo em infraestrutura aeroportuária.",
         "Estadão", "estadao.com.br", 25, None),
        ("Smart Fit anuncia follow-on de R$ 1,2 bilhão para acelerar expansão",
         "Oferta de ações financiará abertura de novas unidades na América Latina.",
         "Brazil Journal", "braziljournal.com", 2, None),
        # ── Contexto positivo (score 0 — não pontua risco)
        ("Fitch revisa perspectiva da Cemig para positiva após venda de ativos",
         "Revisão para perspectiva positiva reflete desalavancagem mais rápida que o previsto.",
         "Reuters", "reuters.com", 55, None),
        ("Moody's eleva rating da Suzano com geração de caixa robusta",
         "Elevação de rating reconhece desalavancagem consistente após ciclo de investimento.",
         "Valor Econômico", "valor.globo.com", 8, None),

        # ── Duplicata proposital: mesma matéria da Hapvida em outro veículo
        #    (deve ser removida pela deduplicação)
        ("Hapvida negocia waiver com credores depois de quebra de covenant",
         "Companhia descumpriu cláusula de dívida líquida/EBITDA e busca acordo com debenturistas.",
         "InfoMoney", "infomoney.com.br", 6, None),

        # ── Fato relevante CVM (chega antes da imprensa)
        ("[Fato Relevante] Klabin: Aprovação de emissão de debêntures — 14ª emissão",
         "Aprovação de emissão de debêntures simples, não conversíveis, em série única.",
         "CVM · Fato Relevante", "cvm.gov.br", 12, "Klabin"),

        # ── FIIs da Vinci (eventos típicos de fundo imobiliário)
        ("Vinci Shopping Centers (VISC11) anuncia redução do rendimento mensal para R$ 0,72 por cota",
         "Corte reflete menor resultado com estacionamentos e despesas não recorrentes.",
         "Clube FII", "clubefii.com.br", 9, None),
        ("Inquilino devolve galpão em Extrema e eleva vacância do Vinci Logística (VILG11)",
         "Devolução de área representa 4% da receita; gestora negocia nova locação.",
         "InfoMoney", "infomoney.com.br", 3, None),

        # ── Mercado (sem emissor da watchlist no título)
        ("CVM edita resolução que endurece divulgação de emissores em reestruturação",
         "Mudança regulatória exige relatórios mensais de liquidez para companhias em crise.",
         "Valor Econômico", "valor.globo.com", 1, None),

        # ── Fora da janela de 90 dias — não deve aparecer na evolução
        ("Klabin conclui emissão de debêntures de R$ 2 bilhões",
         "Notícia antiga, fora da janela de evolução: não deve pontuar.",
         "Money Times", "moneytimes.com.br", 100, None),
    ]

    articles = []
    for i, (title, summary, source, domain, days_ago, _q) in enumerate(raw):
        pub_ts, pub_iso = ts(days_ago)
        articles.append({
            "title": title,
            "url": f"https://demo.local/noticia-{i}",
            "summary": summary,
            "source": source,
            "domain": domain,
            "pub_ts": pub_ts,
            "pub_iso": pub_iso,
            "query_company": None,
        })

    # ── Feed de RI: o MESMO anúncio da Suzano, publicado antes pela empresa —
    #    o dedup deve fundir com a matéria de imprensa e manter a versão oficial
    pub_ts, pub_iso = ts(15.4)
    articles.append({
        "title": "Aviso ao mercado — Precificação de emissão de bonds de US$ 1 bilhão",
        "url": "https://demo.local/ri-suzano-bonds",
        "summary": "A Suzano comunica a precificação de emissão de bonds com vencimento em 10 anos.",
        "source": "Suzano · RI", "domain": "ri.suzano.com.br",
        "pub_ts": pub_ts, "pub_iso": pub_iso,
        "forced_trust": "oficial", "forced_companies": ["Suzano"],
    })
    # ── Agência de rating (via feed direto): peso 1.0 pelo domínio
    pub_ts, pub_iso = ts(2.5)
    articles.append({
        "title": "Fitch rebaixa rating da Cosan para BB e revisa perspectiva para negativa",
        "url": "https://demo.local/fitch-cosan",
        "summary": "Rebaixamento de rating reflete alavancagem da holding; perspectiva negativa.",
        "source": "Fitch Ratings", "domain": "fitchratings.com",
        "pub_ts": pub_ts, "pub_iso": pub_iso,
    })
    return articles


# ── Etapa 4: renderização ────────────────────────────────────────────────────

def build_changes(history: dict, cfg: dict, added_urls: list[str],
                  prev_run: dict, evolution_now: list[dict]) -> dict:
    """Visão 'o que mudou desde a última atualização': sinais novos capturados
    nesta execução e transições de status/score por emissor (janela de 90d)."""
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    new_signals = []
    for url in added_urls:
        rec = history["articles"].get(url)
        if not rec:
            continue
        events = [taxonomy[eid] for eid in rec.get("event_ids", []) if eid in taxonomy]
        if not events:
            continue
        worst = min(events, key=lambda e: SEVERITY_ORDER[e["severity"]])
        t_id, t_w, t_label = trust_of_rec(rec, cfg)
        new_signals.append({
            "title": rec.get("title", ""), "url": url,
            "source": rec.get("source", ""), "pub_iso": rec.get("pub_iso", ""),
            "companies": rec.get("companies", []),
            "severity": worst["severity"],
            "events": [{"label": e["label"], "severity": e["severity"],
                        "direction": e.get("direction", "negativa")} for e in events],
            "trust": t_id, "trust_label": t_label,
            "confirmation": confirmation_of(rec, cfg),
        })
    new_signals.sort(key=lambda a: SEVERITY_ORDER[a["severity"]])

    prev_status = (prev_run or {}).get("status", {})
    transitions, score_moves = [], []
    for row in evolution_now:
        prev = prev_status.get(row["company"])
        if prev is None:
            if prev_status:  # emissor que entrou no radar agora
                transitions.append({"company": row["company"], "from": None,
                                    "to": row["status"], "score_to": row["total_score"]})
            continue
        if prev.get("status") != row["status"]:
            transitions.append({"company": row["company"], "from": prev["status"],
                                "to": row["status"], "score_from": prev.get("score"),
                                "score_to": row["total_score"]})
        elif abs(row["total_score"] - prev.get("score", 0)) >= 10:
            score_moves.append({"company": row["company"],
                                "from": prev.get("score"), "to": row["total_score"]})
    score_moves.sort(key=lambda m: -abs(m["to"] - (m["from"] or 0)))

    return {
        "since_iso": (prev_run or {}).get("iso"),
        "new_signals": new_signals[:60],
        "transitions": transitions,
        "score_moves": score_moves[:10],
    }


def render_html(data_by_window: dict, cfg: dict, demo: bool,
                changes: dict | None = None,
                payload_thresholds: dict | None = None) -> str:
    template_path = Path(__file__).parent / "template_risco.html.j2"
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    payload = {
        "windows": data_by_window,
        "changes": changes or {},
        "direction_meta": DIRECTION_META,
        "confirmation_meta": CONFIRMATION_META,
        "thresholds": {k: v for k, v in (payload_thresholds or {}).items() if k != "sample"},
        "default_window": str(cfg["dashboard"].get("default_window", 7)),
        "attention_threshold": cfg["scoring"].get("attention_threshold", 80),
        "severity_meta": SEVERITY_META,
        "status_meta": STATUS_META,
        "generated_at": fmt_date_br(get_brt_now()),
        "generated_ts": int(datetime.now(timezone.utc).timestamp()),
        "demo": demo,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return template.render(
        title=cfg["dashboard"].get("title", "Radar de Risco"),
        generated_at=fmt_date_br(get_brt_now()),
        payload_json=payload_json,
        demo=demo,
    )


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Radar de Risco — dashboard de notícias")
    parser.add_argument("--config", default="config_risco.yaml")
    parser.add_argument("--demo", action="store_true",
                        help="Usa notícias simuladas (sem acessar o Google News)")
    parser.add_argument("--no-history", action="store_true",
                        help="Ignora o histórico persistido (usa só esta execução)")
    parser.add_argument("--backfill", action="store_true",
                        help="Execução única com busca ampliada (notícias 30d, "
                             "CVM 365d, todos os tiers) para semear o histórico")
    parser.add_argument("--reclassify", action="store_true",
                        help="Reprocessa TODO o histórico com as regras atuais "
                             "(taxonomia, negações, dedup, validação LLM) — use "
                             "após atualizar keywords para limpar classificações antigas")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.backfill:
        cfg["dashboard"]["period"] = "30d"
        cfg["dashboard"]["max_articles_per_query"] = max(
            30, cfg["dashboard"].get("max_articles_per_query", 15))
        cfg.setdefault("cvm_fatos_relevantes", {})["lookback_days"] = 365
        for tc in (cfg.get("tiers") or {}).values():
            if tc.get("fetch_every_n_runs", 1) > 1:
                tc["fetch_every_n_runs"] = 1
        print(" ⏪ Backfill: notícias de 30d, CVM de 365d, todos os tiers ativos.")
    out_cfg = cfg["output"]
    history_path = Path(out_cfg.get("history_file", "risk_history.json"))

    print(f"\n🛰️  {cfg['dashboard'].get('title')}\n{'=' * 60}")

    # Histórico carregado antes do fetch: o contador de execuções decide
    # quais tiers entram nesta run.
    history = {"articles": {}} if (args.no_history or args.demo) else load_history(history_path)
    history.setdefault("run_count", 0)
    history["run_count"] += 1

    # 1) Fetch — notícias + fatos relevantes da CVM
    if args.demo:
        articles = demo_articles()
        print(f" 🧪 Modo demo: {len(articles)} notícias simuladas.")
    else:
        articles = fetch_all(cfg, run_count=history["run_count"])
        articles += fetch_cvm_fatos(cfg)
        articles += fetch_custom_feeds(cfg)
        articles += fetch_ri_news_pages(cfg)

    # 1b) Reclassificação: joga o histórico de volta no pipeline como se
    # fosse recém-coletado — reclassifica (regras/negações atuais), revalida
    # via LLM e deduplica retroativamente. Corrige classificações gravadas
    # com regras antigas.
    if args.reclassify and history["articles"]:
        print(f" ♻️  Reclassificando {len(history['articles'])} registros do histórico…")
        prior = []
        for url, rec in history["articles"].items():
            prior.append({
                "title": clean_gnews_title(rec.get("title", ""), rec.get("source", "")),
                "url": url,
                "summary": rec.get("summary", ""),
                "source": rec.get("source", ""),
                "domain": rec.get("domain", ""),
                "pub_ts": rec.get("pub_ts", 0),
                "pub_iso": rec.get("pub_iso", ""),
                # preserva as fontes corroborantes já acumuladas — senão o
                # reclassify apagaria a contagem multi-fonte (duplicatas
                # originais já não estão mais no histórico para recontar)
                "corrob_sources": rec.get("corrob_sources", []),
                "corroborations": rec.get("corroborations", []),
                "trust_override": rec.get("trust_override"),
            })
        seen_now = {a["url"] for a in articles}
        articles = articles + [a for a in prior if a["url"] not in seen_now]
        history["articles"] = {}

    # 2) Classificação → deduplicação → validação dos críticos
    print(" 🏷️  Classificando eventos pela taxonomia…")
    for art in articles:
        art["events"] = classify_article(art, cfg["taxonomy"])
        art["companies"] = detect_companies(art, cfg["watchlist"])
    articles = dedupe_articles(articles, history, cfg)
    articles = consolidate_with_llm(articles, cfg, history)
    matched = [a for a in articles if a["events"] and a["companies"]]
    print(f" ✅ {len(matched)} notícias com evento + emissor identificados.")

    # Resolve os redirects do Google News → link direto do veículo. Passa o
    # histórico inteiro (não só os novos): registros antigos com link do Google
    # ainda não resolvido, ou que caíram no fallback, são corrigidos aqui.
    resolve_google_news_urls(matched, history, cfg)

    # 3) Histórico + agregações
    added_urls = merge_into_history(
        history, articles, keep_days=cfg["dashboard"].get("history_keep_days", 120))

    # aplica a resolução de URLs aos registros JÁ no histórico (execuções
    # anteriores gravaram o link-redirecionador do Google; corrige todos)
    resolve_history_urls(history, cfg)

    prev_run = history.get("last_run") or {}
    prev_scores = {c: v.get("score") for c, v in (prev_run.get("status") or {}).items()}
    thresholds = calibrate_thresholds(history, cfg)
    if thresholds["mode"] == "adaptativo":
        print(f" 🎚️  Limiares adaptativos (n={thresholds['sample_n']} emissor-semanas): "
              f"Atenção ≥ {thresholds['atencao']} · Crítico ≥ {thresholds['critico']}")
    else:
        print(f" 🎚️  Limiares base (amostra de calibração: {thresholds['sample_n']} "
              f"emissor-semanas — adaptativo ativa com ≥ "
              f"{cfg['evolution']['status'].get('adaptive', {}).get('min_sample', 40)})")

    windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
    data_by_window = {}
    for w in windows:
        data_by_window[str(w)] = {
            "ranking": aggregate_scores(history, cfg, window_days=w),
            "evolution": build_evolution(history, cfg, window_days=w,
                                         thresholds=thresholds,
                                         prev_scores=prev_scores),
            "feed": build_feed(history, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
    ranking = data_by_window[default_w]["ranking"]
    evolution = data_by_window[default_w]["evolution"]

    # "O que mudou" usa a janela de 90d (status mais estável) p/ transições
    evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
    changes = build_changes(history, cfg, added_urls, prev_run, evo_ref)
    history["last_run"] = {
        "ts": int(datetime.now(timezone.utc).timestamp()),
        "iso": fmt_date_br(get_brt_now()),
        "status": {r["company"]: {"status": r["status"], "score": r["total_score"]}
                   for r in evo_ref},
    }
    if not args.demo:
        save_history(history_path, history)
        print(f" 💾 Histórico salvo em {history_path} ({len(history['articles'])} registros)")
    if changes["new_signals"]:
        print(f" 🆕 {len(changes['new_signals'])} sinal(is) novo(s) desde a última execução")
    for t in changes["transitions"]:
        print(f" 🔀 {t['company']}: {t.get('from') or 'novo'} → {t['to']}")

    if ranking:
        print(f"\n 📊 Radar (janela padrão de {default_w} dias):")
        for r in ranking[:5]:
            flag = " ⚠️ ATENÇÃO" if r["attention"] else ""
            print(f"   {r['score']:>4} pts — {r['company']}{flag}")
    if evolution:
        print(f"\n 📈 Evolução ({default_w} dias):")
        for row in evolution[:6]:
            seq = " → ".join(f"{o['date'][5:]} {o['label']}" for o in row["timeline"][:4])
            print(f"   [{STATUS_META[row['status']]['label']:<15}] {row['company']}: {seq}")

    # 4) Render
    html = render_html(data_by_window, cfg, demo=args.demo, changes=changes,
                       payload_thresholds=thresholds)
    out_file = Path(out_cfg.get("filename", "dashboard_risco.html"))
    out_file.write_text(html, encoding="utf-8")
    print(f"\n ✅ Dashboard gerado: {out_file.resolve()}")

    if out_cfg.get("open_browser"):
        webbrowser.open(out_file.resolve().as_uri())


if __name__ == "__main__":
    main()
