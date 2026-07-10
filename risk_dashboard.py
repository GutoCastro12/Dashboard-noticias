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
from urllib.parse import quote, urlparse

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

SEVERITY_ORDER = {"critico": 0, "alto": 1, "medio": 2, "info": 3}
MARKET_LABEL = "Mercado (geral)"
SEVERITY_META = {
    "critico": {"emoji": "🔴", "label": "Crítico", "sub": "alerta imediato"},
    "alto":    {"emoji": "🟠", "label": "Alto impacto", "sub": ""},
    "medio":   {"emoji": "🟡", "label": "Médio", "sub": ""},
    "info":    {"emoji": "🟢", "label": "Contexto positivo", "sub": "não pontua risco"},
}

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


def _parse_rss(xml_text: str) -> list[dict]:
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
        for alias in company.get("aliases", []) + [company["name"]]:
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
            ev_a = {e["id"] for e in a.get("events", [])}
            ev_b = {e["id"] for e in b.get("events", [])}
            if ev_a and ev_a == ev_b and entities(ta) & entities(tb):
                return True
        return False

    def comparable(comps_a: set[str], comps_b: set[str]) -> bool:
        # mesmas empresas OU ambas sem empresa (notícias de mercado)
        return bool(comps_a & comps_b) or (not comps_a and not comps_b)

    # referência: registros recentes do histórico (mesma empresa, ±3 dias)
    hist_recent = [r for r in history.get("articles", {}).values() if r.get("pub_ts")]

    kept: list[dict] = []
    removed = 0
    for art in sorted(articles, key=lambda a: a.get("pub_ts", 0)):
        comps = set(art.get("companies", []))
        dup = False
        for other in kept:
            if comparable(comps, set(other.get("companies", []))) and \
               abs(art.get("pub_ts", 0) - other.get("pub_ts", 0)) <= 3 * 86400 and \
               similar(art, other):
                dup = True
                break
        if not dup and art["url"] not in history.get("articles", {}):
            for rec in hist_recent:
                if comparable(comps, set(rec.get("companies", []))) and \
                   abs(art.get("pub_ts", 0) - rec.get("pub_ts", 0)) <= 3 * 86400 and \
                   similar(art, rec):
                    dup = True
                    break
        if dup:
            removed += 1
        else:
            kept.append(art)
    if removed:
        print(f" 🧹 Deduplicação: {removed} matéria(s) replicada(s) removida(s).")
    return kept


def validate_critical_events(articles: list[dict], cfg: dict, history: dict) -> None:
    """Valida via Gemini os eventos CRÍTICOS detectados por keyword, antes
    de pontuarem: confirma que o evento de fato OCORREU (não é especulação,
    negação ou 'risco de'), é sobre a empresa e é negativo. Falha da API =
    mantém a classificação por keyword (fail-open). Artigos já presentes no
    histórico não são revalidados (custo ~zero por execução)."""
    llm = cfg.get("llm", {})
    if not llm.get("enabled") or not llm.get("validate_critical", True) or not genai:
        return
    api_key = llm.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(" " + "!" * 62)
        print(" ⚠️  VALIDAÇÃO LLM INATIVA: secret GEMINI_API_KEY não configurado.")
        print("     Falsos positivos de atribuição (ex.: RJ de terceiros citando")
        print("     a empresa) NÃO serão filtrados. Configure em: GitHub →")
        print("     Settings → Secrets and variables → Actions → GEMINI_API_KEY")
        print(" " + "!" * 62)
        return

    severities = set(llm.get("validate_severities", ["critico", "alto"]))
    pending = [
        a for a in articles
        if a.get("companies")
        and a["url"] not in history.get("articles", {})
        and any(e["severity"] in severities for e in a.get("events", []))
    ]
    if not pending:
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(llm.get("model", "gemini-2.5-flash-lite"))
    print(f" 🤖 Validando {len(pending)} notícia(s) com evento crítico/alto via Gemini…")

    for art in pending:
        criticals = [e for e in art["events"] if e["severity"] in severities]
        others = [e for e in art["events"] if e["severity"] not in severities]
        ev_desc = "\n".join(f"- {e['id']}: {e['label']}" for e in criticals)
        prompt = (
            "Você é um analista de risco de crédito. A manchete abaixo foi "
            "classificada por keywords nos eventos críticos listados, atribuídos "
            f"às empresas: {', '.join(art['companies'])}. Para cada evento, "
            "confirme APENAS se ele de fato OCORREU (descarte especulação como "
            "'pode ser rebaixada', negação como 'afasta risco de', condicional, "
            "contexto histórico ou caso antigo reaquecido) e diga a quais das "
            "empresas listadas o evento se aplica DIRETAMENTE como protagonista "
            "— empresas citadas de passagem, como assessoras, credoras, "
            "investigadoras ou vítimas de terceiros, NÃO contam. Responda "
            'APENAS JSON: {"confirmed": [{"event_id": "...", '
            '"applies_to": ["Empresa"]}]} (lista vazia se nada se confirma).\n\n'
            f"EVENTOS DETECTADOS:\n{ev_desc}\n\n"
            f"TÍTULO: {art['title']}\nRESUMO: {art.get('summary', '')}"
        )
        try:
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0, response_mime_type="application/json"),
                request_options={"timeout": 60},
            )
            confirmed = json.loads(resp.text).get("confirmed", [])
            confirmed_ids = {c.get("event_id") for c in confirmed}
            applies = {comp for c in confirmed
                       for comp in c.get("applies_to", []) if comp in art["companies"]}
            kept_criticals = [e for e in criticals if e["id"] in confirmed_ids]
            dropped = [e["label"] for e in criticals if e["id"] not in confirmed_ids]
            if dropped:
                print(f"   🚫 Descartado por validação em '{art['title'][:55]}…': "
                      f"{', '.join(dropped)}")
            art["events"] = kept_criticals + others
            # Se sobraram só eventos críticos, restringe às empresas protagonistas
            if kept_criticals and not others and applies:
                before = set(art["companies"])
                art["companies"] = [c for c in art["companies"] if c in applies]
                cut = before - set(art["companies"])
                if cut:
                    print(f"   ✂️  Atribuição refinada em '{art['title'][:45]}…': "
                          f"removidas {', '.join(sorted(cut))}")
        except Exception as exc:
            print(f"   ⚠️  Validação falhou para '{art['title'][:40]}…' "
                  f"(mantendo keywords): {exc}")


def classify_with_gemini(articles: list[dict], cfg: dict, taxonomy: list[dict]) -> None:
    """Classificação LLM opcional para artigos sem match de keywords
    (mesmo padrão do dashboard original: Gemini + JSON estruturado)."""
    llm = cfg.get("llm", {})
    if not llm.get("enabled") or not genai:
        return
    api_key = llm.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(" ⚠️  LLM habilitado mas sem GEMINI_API_KEY. Pulando.")
        return

    pending = [a for a in articles if not a.get("events")]
    if not pending:
        return
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(llm.get("model", "gemini-2.5-flash-lite"))
    tax_desc = "\n".join(f"- {e['id']}: {e['label']} (severidade {e['severity']})" for e in taxonomy)
    tax_by_id = {e["id"]: e for e in taxonomy}

    print(f" 🤖 Classificando {len(pending)} artigos sem match via Gemini…")
    for art in pending:
        prompt = (
            "Você é um analista de risco de crédito. Classifique a notícia abaixo "
            "em zero ou mais eventos da taxonomia. Responda APENAS JSON no formato "
            '{"event_ids": ["..."]} (lista vazia se nenhum evento se aplica).\n\n'
            f"TAXONOMIA:\n{tax_desc}\n\n"
            f"TÍTULO: {art['title']}\nRESUMO: {art.get('summary','')}"
        )
        try:
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1, response_mime_type="application/json"),
            )
            data = json.loads(resp.text)
            ids = [i for i in data.get("event_ids", []) if i in tax_by_id]
            if ids:
                art["events"] = [tax_by_id[i] for i in ids]
        except Exception as exc:
            print(f"   ⚠️  Gemini falhou para '{art['title'][:40]}…': {exc}")


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


def merge_into_history(history: dict, articles: list[dict], keep_days: int = 120) -> None:
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
        history["articles"][art["url"]] = rec
    # poda registros antigos
    history["articles"] = {
        u: r for u, r in history["articles"].items()
        if r.get("pub_ts", 0) >= cutoff
    }


STATUS_META = {
    "critico":   {"label": "Crítico", "severity": "critico"},
    "atencao":   {"label": "Atenção elevada", "severity": "alto"},
    "monitorar": {"label": "Monitorar", "severity": "medio"},
}
STATUS_ORDER = {"critico": 0, "atencao": 1, "monitorar": 2}


def build_evolution(history: dict, cfg: dict, window_days: int | None = None) -> list[dict]:
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
    critico_total = st.get("critico_total_min", 160)
    atencao_total = st.get("atencao_total_min", 60)

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
                per_company.setdefault(company, []).append({
                    "event_id": eid,
                    "label": ev["label"],
                    "severity": ev["severity"],
                    "score": ev["score"],
                    "positive": bool(ev.get("positive")),
                    "pub_ts": rec["pub_ts"],
                    "date": (rec.get("pub_iso") or "")[:10],
                    "title": rec.get("title", ""),
                    "url": rec.get("url", ""),
                    "pos_pct": round(100.0 * (window_days - days_ago) / window_days, 2),
                    "opacity": round(0.35 + 0.65 * decay_weight(rec["pub_ts"], now_ts), 2),
                })

    def weighted_total(negatives: list[dict], as_of_ts: int) -> float:
        """Soma decaída dos tipos de evento negativos distintos até as_of_ts
        (a ocorrência mais recente de cada tipo define o peso)."""
        latest: dict[str, int] = {}
        for o in negatives:
            if o["pub_ts"] <= as_of_ts:
                latest[o["event_id"]] = max(latest.get(o["event_id"], 0), o["pub_ts"])
        return sum(taxonomy[eid]["score"] * decay_weight(ts_, as_of_ts)
                   for eid, ts_ in latest.items())

    rows = []
    for company, occurrences in per_company.items():
        occurrences.sort(key=lambda o: o["pub_ts"])
        negatives = [o for o in occurrences if not o["positive"]]
        if not negatives:
            continue  # só contexto positivo: não é linha de risco

        distinct: dict[str, dict] = {}
        for o in occurrences:  # chips resumem tudo, inclusive positivos
            d = distinct.setdefault(o["event_id"], {**o, "count": 0})
            d["count"] += 1
        distinct_events = sorted(distinct.values(),
                                 key=lambda e: (SEVERITY_ORDER[e["severity"]], -e["score"]))

        total = weighted_total(negatives, now_ts)
        n_negative_types = len({o["event_id"] for o in negatives})
        has_hard_critical = any(o["score"] >= critico_event for o in negatives)

        if has_hard_critical or total >= critico_total:
            status = "critico"
        elif total >= atencao_total or n_negative_types >= 2:
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

        m = meta.get(company, {"tier": 2, "type": "empresa"})
        rows.append({
            "company": company,
            "tier": m["tier"],
            "type": m["type"],
            "status": status,
            "total_score": round(total),
            "hard_critical": has_hard_critical,
            "events": distinct_events,
            "timeline": occurrences,
            "spark_points": spark_points,
            "first_date": occurrences[0]["date"],
            "last_date": occurrences[-1]["date"],
        })
    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], -r["total_score"]))
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
            if ev.get("positive") or ev["score"] <= 0:
                continue  # contexto positivo não pontua risco
            n = len(recs)
            if scoring.get("count_repeated_events"):
                base = ev["score"] * n
                extra = 0
            else:
                base = ev["score"]
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
        feed.append({
            **rec,
            "events": [{"id": e["id"], "label": e["label"],
                        "severity": e["severity"], "score": e["score"]} for e in events],
            "severity": worst["severity"],
            "score": max(e["score"] for e in events),
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
    return articles


# ── Etapa 4: renderização ────────────────────────────────────────────────────

def render_html(data_by_window: dict, cfg: dict, demo: bool) -> str:
    template_path = Path(__file__).parent / "template_risco.html.j2"
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    payload = {
        "windows": data_by_window,
        "default_window": str(cfg["dashboard"].get("default_window", 7)),
        "attention_threshold": cfg["scoring"].get("attention_threshold", 80),
        "severity_meta": SEVERITY_META,
        "status_meta": STATUS_META,
        "generated_at": fmt_date_br(get_brt_now()),
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
    validate_critical_events(articles, cfg, history)
    classify_with_gemini(articles, cfg, cfg["taxonomy"])
    matched = [a for a in articles if a["events"] and a["companies"]]
    print(f" ✅ {len(matched)} notícias com evento + emissor identificados.")

    # 3) Histórico + agregações
    merge_into_history(history, articles,
                       keep_days=cfg["dashboard"].get("history_keep_days", 120))
    if not args.demo:
        save_history(history_path, history)
        print(f" 💾 Histórico salvo em {history_path} ({len(history['articles'])} registros)")

    windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
    data_by_window = {}
    for w in windows:
        data_by_window[str(w)] = {
            "ranking": aggregate_scores(history, cfg, window_days=w),
            "evolution": build_evolution(history, cfg, window_days=w),
            "feed": build_feed(history, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
    ranking = data_by_window[default_w]["ranking"]
    evolution = data_by_window[default_w]["evolution"]

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
    html = render_html(data_by_window, cfg, demo=args.demo)
    out_file = Path(out_cfg.get("filename", "dashboard_risco.html"))
    out_file.write_text(html, encoding="utf-8")
    print(f"\n ✅ Dashboard gerado: {out_file.resolve()}")

    if out_cfg.get("open_browser"):
        webbrowser.open(out_file.resolve().as_uri())


if __name__ == "__main__":
    main()
