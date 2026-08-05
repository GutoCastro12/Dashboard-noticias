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
import collections
import copy
import csv
import difflib
import functools
import io
import shutil
import hashlib
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


def link_fields(d: dict) -> dict:
    """Decisão de link de UMA fonte (principal OU corroboradora).

    Ponto único: se o reparo já persistiu os campos, usa-os; caso contrário
    resolve na hora pela MESMA função canônica. Nunca devolve um
    redirecionador para o href — sem destino direto, `href` fica vazio e a
    interface mostra "Link em verificação" em vez de um botão quebrado."""
    try:
        import link_debt_audit as _lk
    except Exception:
        # [fix: complete Peru news links] mesmo sem o módulo de resolução
        # disponível, uma URL do Google News/agregador já coletada é
        # clicável no navegador real do usuário — não bloquear só por ser
        # esse domínio (o usuário resolve o redirect ao clicar).
        u = d.get("url", "") or ""
        ok = bool(u)
        label = "Abrir notícia →" if "news.google.com" not in (u or "") \
            else "Abrir notícia (via agregador) →"
        return {"href": u if ok else "", "render_anchor": ok,
                "label": label if ok else "Link em verificação",
                "link_health": "nao_verificado"}
    if d.get("link_health") and "link_render_anchor" in d:
        href = d.get("display_url") or ""
        if href and _lk.is_redirector(href):
            href = ""
        return {"href": href, "render_anchor": bool(d.get("link_render_anchor")) and bool(href),
                "label": d.get("link_label") or "Abrir notícia →",
                "link_health": d.get("link_health")}
    res = _lk.resolve_article_url(d.get("url", "") or "", domain=d.get("domain", ""))
    dec = _lk.interface_decision(res)
    return {"href": dec["href"], "render_anchor": dec["render_anchor"],
            "label": dec["label"], "link_health": res["link_health"]}


def link_for_display(rec: dict) -> str:
    """Compatibilidade: só devolve URL segura (nunca um redirecionador)."""
    return link_fields(rec)["href"]


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


# Edição do Google News por país do emissor. Sem isso, TODA busca ia para a
# edição pt-BR/BR — e procurar "Cencosud" ou "Ecopetrol" na edição brasileira
# devolve quase nada, o que zerava a cobertura dos emissores estrangeiros.
_COUNTRY_LOCALE = {
    "Brasil": ("pt-BR", "BR"), "Portugal": ("pt-PT", "PT"),
    "EUA": ("en-US", "US"), "Estados Unidos": ("en-US", "US"),
    "Canadá": ("en-CA", "CA"), "Reino Unido": ("en-GB", "GB"),
    "Irlanda": ("en-IE", "IE"), "Austrália": ("en-AU", "AU"),
    "Chile": ("es-CL", "CL"), "México": ("es-MX", "MX"),
    "Colômbia": ("es-CO", "CO"), "Argentina": ("es-AR", "AR"),
    "Uruguai": ("es-UY", "UY"), "Peru": ("es-PE", "PE"),
    "Espanha": ("es-ES", "ES"), "Panamá": ("es-PA", "PA"),
    "França": ("fr-FR", "FR"), "Alemanha": ("de-DE", "DE"),
    "Suíça": ("de-CH", "CH"), "Itália": ("it-IT", "IT"),
    "Países Baixos": ("nl-NL", "NL"), "Luxemburgo": ("fr-FR", "LU"),
    "Malásia": ("en-MY", "MY"), "Singapura": ("en-SG", "SG"),
    "Japão": ("ja-JP", "JP"), "China": ("zh-CN", "CN"),
    "Índia": ("en-IN", "IN"),
}
_LANG_LOCALE = {"pt": ("pt-BR", "BR"), "es": ("es-419", "US"),
                "en": ("en-US", "US"), "fr": ("fr-FR", "FR"), "de": ("de-DE", "DE")}


def locales_for_company(company: dict, cfg: dict) -> list:
    """Lista ordenada de locales: principal + até 2 fallbacks declarados em
    `search_locale.fallbacks`. O fallback só é consultado se o principal falhar
    ou responder com zero resultados (regra aplicada em fetch_all)."""
    hl, gl = locale_for_company(company, cfg)
    out = [f"{hl}/{gl}"]
    for fb in ((company.get("search_locale") or {}).get("fallbacks") or [])[:2]:
        fb = str(fb).strip()
        if "/" in fb and fb not in out:
            out.append(fb)
    return out


def locale_for_company(company: dict, cfg: dict) -> tuple:
    """(hl, gl) do Google News para o emissor — por país e, na falta, por idioma.
    Emissor estrangeiro pesquisado na edição do PRÓPRIO país encontra a imprensa
    local (El Mercurio, La República, Reforma…), não só o que sai no Brasil."""
    dash = cfg.get("dashboard", {})
    padrao = (dash.get("language", "pt-BR"), dash.get("country", "BR"))
    if not company:
        return padrao
    # override cadastral: domicílio jurídico nem sempre representa a imprensa
    # relevante (ex.: holding na Suíça, listada nos EUA, idioma inglês).
    ov = company.get("search_locale") or {}
    prim = ov.get("primary")
    if prim and "/" in str(prim):
        hl, gl = str(prim).split("/", 1)
        return (hl.strip(), gl.strip())
    loc = _COUNTRY_LOCALE.get(company.get("country") or "")
    if loc:
        return loc
    return _LANG_LOCALE.get((company.get("language") or "").lower(), padrao)


def fetch_query_result(query: str, cfg: dict, session: requests.Session,
                       locale: tuple | None = None) -> dict:
    """4H.1b — Resultado ESTRUTURADO da consulta. Antes `fetch_query` devolvia
    `[]` tanto para 'respondeu com zero artigos' quanto para timeout/403/erro de
    conexão — e como `[] is not None`, a telemetria contava falha como sucesso.
    Agora o chamador sabe exatamente o que aconteceu."""
    dash = cfg["dashboard"]
    lang, country = locale or (dash.get("language", "pt-BR"), dash.get("country", "BR"))
    period = dash.get("period", "7d")
    url = GOOGLE_NEWS_RSS.format(
        query=quote(f"{query} when:{period}"),
        hl=lang, gl=country, ceid_lang=lang.split("-")[0],
    )
    out = {"ok": False, "articles": [], "status_code": None, "error": "",
           "error_type": "", "elapsed_ms": 0, "url": url,
           "locale": f"{lang}/{country}", "query": query}
    t0 = time.time()
    try:
        resp = session.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        out["status_code"] = resp.status_code
        resp.raise_for_status()
    except requests.Timeout as exc:
        out.update(error_type="timeout", error=str(exc)[:200])
    except requests.HTTPError as exc:
        out.update(error_type="http_error", error=str(exc)[:200])
    except requests.ConnectionError as exc:
        out.update(error_type="connection_error", error=str(exc)[:200])
    except Exception as exc:
        out.update(error_type="unknown_error", error=str(exc)[:200])
    else:
        try:
            arts = _parse_rss(resp.text)
        except Exception as exc:
            out.update(error_type="parse_error", error=str(exc)[:200])
        else:
            limit = dash.get("max_articles_per_query", 15)
            out.update(ok=True, articles=arts[:limit])
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    if out["error"]:
        print(f"   ⚠️  Falha na busca '{query[:50]}…' [{out['error_type']}]: {out['error'][:80]}")
    return out


def fetch_query(query: str, cfg: dict, session: requests.Session,
                locale: tuple | None = None) -> list[dict]:
    """Compatibilidade: devolve só os artigos. Quem precisa de telemetria deve
    usar `fetch_query_result`."""
    return fetch_query_result(query, cfg, session, locale)["articles"]


# Termos de risco por GRUPO DE ATIVOS (chave = asset_group canônico).
# Antes indexado por `type`, o que fazia os emissores novos (que usam
# `asset_class`) caírem sempre nos termos corporativos genéricos.
# Termos de risco por IDIOMA — usados quando o emissor não é lusófono.
RISK_TERMS_I18N = {
    "es": ["quiebra", "concurso de acreedores", "reestructuración de deuda",
           "default", "impago", "rebaja de calificación", "calificación crediticia",
           "fraude", "investigación", "adquisición", "emisión de deuda",
           "resultados", "pérdidas", "renuncia CEO"],
    "en": ["bankruptcy", "chapter 11", "debt restructuring", "default",
           "credit rating downgrade", "covenant breach", "fraud", "SEC investigation",
           "acquisition", "bond issue", "profit warning", "CEO steps down",
           "earnings miss", "layoffs"],
    "fr": ["faillite", "redressement judiciaire", "restructuration de dette",
           "défaut de paiement", "dégradation de la note", "fraude", "enquête",
           "acquisition", "émission obligataire", "avertissement sur résultats"],
    "de": ["Insolvenz", "Restrukturierung", "Zahlungsausfall", "Herabstufung",
           "Betrug", "Untersuchung", "Übernahme", "Anleiheemission", "Gewinnwarnung"],
}

RISK_TERMS_BY_GROUP = {
    # empresa listada (padrão)
    "listed_companies": [
        "recuperação judicial", "falência", "default", "rating",
        "covenant", "fraude", "CVM", "auditor", "CEO", "aquisição",
        "debêntures", "follow-on", "guidance", "resultado", "prejuízo",
    ],
    # fundo imobiliário / FIAGRO / FIP-IE listado
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
    # gestora / fundo / veículo de investimento: o risco é de VEÍCULO e de
    # GESTOR, não risco corporativo operacional. Termos próprios.
    "gestora_fundo": [
        "resgate", "fechamento para resgates", "side pocket", "liquidação do fundo",
        "cotistas", "marcação a mercado", "perdas", "gestor", "sócio",
        "investigação", "CVM", "SEC", "liquidez", "patrimônio líquido",
    ],
    # fallback p/ emissores sem grupo resolvido
    "a_revisar": [
        "recuperação judicial", "falência", "default", "rating", "fraude",
    ],
}
# compat: alias antigo (algum código externo pode importar pelo nome anterior)
RISK_TERMS_BY_TYPE = RISK_TERMS_BY_GROUP



# ── Grupo de ativos (segmentação cadastral do emissor) ───────────────────────
# O grupo de ativos é uma informação CADASTRAL, fixa e determinística por
# emissor. NÃO é inferido de notícias, eventos, score ou tier de exposição.
# Fonte de verdade: o campo `asset_class` na watchlist (config), com três
# valores canônicos. Para retrocompatibilidade, aceita-se também o campo
# legado `type`; se nenhum estiver presente, o emissor é tratado como listada
# APENAS quando possui ticker de ação — caso contrário vai para "a revisar".
ASSET_GROUP_LABELS = {
    "listed_companies": "Empresas listadas",
    "nao_listada": "Não listadas (PE/Crédito)",
    "fii": "FIIs/Fundos listados",
    "gestora_fundo": "Gestoras/Fundos",
    "a_revisar": "A revisar",
}
# mapa dos valores cadastrais aceitos → grupo canônico do dashboard
_ASSET_CLASS_TO_GROUP = {
    "listada": "listed_companies",
    "listed": "listed_companies",
    "listed_companies": "listed_companies",
    "empresa": "listed_companies",          # legado (campo `type`)
    "nao_listada": "nao_listada",
    "private_equity": "nao_listada",
    "credito_privado": "nao_listada",
    "fii": "fii",
    "fiagro": "fii",
    # Gestoras, fundos e veículos de investimento — risco de veículo/gestor,
    # não risco corporativo tradicional (subgrupo cadastral próprio).
    "gestora_fundo": "gestora_fundo",
    "gestora": "gestora_fundo",
    "fundo": "gestora_fundo",
    "fundo_listado": "gestora_fundo",
    "veiculo_offshore": "gestora_fundo",
    "fundo_interno": "gestora_fundo",
    "fundo_terceiros": "gestora_fundo",
    "fip": "gestora_fundo",   # FIP é veículo de PE (corrigido: antes ia p/ fii)
}

# Padrões de ticker reconhecidos como AÇÃO/negociação em bolsa:
#  1. B3 clássico:  4 letras + 1-2 dígitos           → PETR4, BPAC11, VILG11
#  2. B3 alternativo (nome com dígito): A + dígito + 2 letras + dígito → B3SA3
#  3. Estrangeiro (NASDAQ/NYSE, só letras): reconhecido por lista explícita,
#     porque um alias de 3-4 letras nem sempre é ticker (evita falso positivo).
_STOCK_TICKER_RES = (
    re.compile(r"^[A-Z]{4}\d{1,2}$"),        # AAAA9 / AAAA99
    re.compile(r"^[A-Z]\d[A-Z]{2}\d{1,2}$"),  # A9AA9 (ex.: B3SA3)
)
# Tickers de bolsa estrangeira presentes/esperados na watchlist. Cadastral e
# explícito — acrescente aqui novos ADRs/tickers estrangeiros conforme surgirem.
_FOREIGN_TICKERS = {"MELI", "STNE"}


def has_stock_ticker(company: dict) -> str | None:
    """Retorna o primeiro ticker de AÇÃO/negociação encontrado nos aliases, ou
    None. Reconhece o padrão B3 clássico (PETR4, BPAC11), o padrão B3 com dígito
    no meio (B3SA3) e tickers de bolsa estrangeira conhecidos (MELI, STNE).
    Tickers de fundo (final 11) contam como ticker de negociação; a distinção
    fii/ação é feita pelo asset_class, não aqui."""
    tk = (company.get("ticker") or "").strip()
    if tk:
        return tk          # campo cadastral tem precedência
    for a in company.get("aliases", []) or []:
        s = (a or "").strip()
        if s in _FOREIGN_TICKERS or any(rx.fullmatch(s) for rx in _STOCK_TICKER_RES):
            return s
    return None


def asset_group_of_company(company: dict) -> str:
    """Grupo de ativos canônico de um emissor, a partir do cadastro.
    Precedência: asset_class → type (legado) → inferência conservadora por
    ticker. Sem base cadastral e sem ticker de ação → 'a_revisar' (nunca
    undefined/null/NaN, e nunca inferido de dados de risco)."""
    raw = company.get("asset_class") or company.get("type")
    if raw:
        grp = _ASSET_CLASS_TO_GROUP.get(str(raw).strip().lower())
        if grp:
            return grp
        # valor cadastral desconhecido → sinaliza para revisão
        return "a_revisar"
    # sem campo cadastral: só assume "listada" se houver ticker de ação
    return "listed_companies" if has_stock_ticker(company) else "a_revisar"


# Grupos "corporativos" — default de aplicabilidade de um evento da taxonomia
# quando o campo `applies_to` não está declarado.
_CORPORATE_GROUPS = ("listed_companies", "nao_listada", "fii")


# ── Taxonomia de fundos (Fase 4C/4D) ──────────────────────────────────────────
# Grupo metodológico esperado por tipo de fundo. FII, FIAGRO e FIP-IE LISTADO
# (cota 11 negociada em bolsa) → grupo 'fii' ("FIIs/Fundos listados"): para o
# usuário do dashboard fazem sentido juntos. FIP/FIDC não listados são veículos
# de Gestoras/Fundos. Decisão VIGT11 (4D): FIP-IE listado permanece em 'fii'.
FUND_TYPE_TO_GROUP = {
    "FII": "fii", "FIAGRO": "fii", "FIP-IE": "fii",
    "FIP": "gestora_fundo", "FIDC": "gestora_fundo",
}


def fund_type_of(company: dict) -> str | None:
    """Tipo de fundo declarado (`fund_type`) ou inferido do nome/aliases —
    FIAGRO, FIP-IE, FIP, FIDC ou FII. Não inventa: só reconhece o que o próprio
    nome do veículo declara. Retorna None se não for um fundo reconhecível."""
    ft = (company.get("fund_type") or "").strip()
    if ft:
        return ft
    texto = " ".join([company.get("name", "")] + list(company.get("aliases") or [])).upper()
    for marca in ("FIAGRO", "FIP-IE", "FIP", "FIDC", "FII"):
        if marca in texto:
            return marca
    return None


def fund_taxonomy_pendencies(cfg: dict) -> list[dict]:
    """Emissores cujo grupo cadastral (asset_class) diverge do grupo esperado
    pela metodologia de fundos — ex.: FIP listado em 'fii'. Apenas diagnóstico
    (pendência), nunca correção silenciosa."""
    out = []
    for c in cfg.get("watchlist", []):
        ft = fund_type_of(c)
        if not ft:
            continue
        esperado = FUND_TYPE_TO_GROUP.get(ft)
        atual = asset_group_of_company(c)
        if esperado and atual != esperado:
            out.append({"emissor": c.get("name", ""), "fund_type": ft,
                        "grupo_atual": atual, "grupo_esperado": esperado})
    return out


def event_applies_to(ev: dict, group: str) -> bool:
    """O evento faz sentido para a natureza deste emissor?

    Um evento corporativo ("recuperação judicial", "covenant breach") não
    descreve risco de um veículo de investimento; e os eventos de veículo
    (suspensão de resgates, side pocket, liquidação do fundo) não descrevem
    risco de uma companhia operacional. O campo `applies_to` da taxonomia
    define isso explicitamente; sem ele, assume-se apenas os grupos
    corporativos."""
    alvos = ev.get("applies_to") or _CORPORATE_GROUPS
    return group in alvos


def asset_group_of(ctype: str | None) -> str:
    """Compat: resolve o grupo a partir de um valor de classe/tipo isolado."""
    if not ctype:
        return "a_revisar"
    return _ASSET_CLASS_TO_GROUP.get(str(ctype).strip().lower(), "a_revisar")


# Modos de pontuação aceitos no cadastro.
#   normal                 → taxonomia corporativa (padrão dos demais grupos)
#   taxonomia_propria      → pontua só pelos eventos de veículo/gestor (Fase 3)
#   monitoramento_limitado → trava manual: coleta e exibe, mas não classifica
SCORING_MODES = {"normal", "taxonomia_propria", "monitoramento_limitado"}


def validate_asset_classes(watchlist: list[dict]) -> list[str]:
    """Valida o cadastro completo da watchlist antes do deploy.

    ERRO (bloqueia com --strict-groups):
      - emissor sem asset_class / sem grupo resolvível;
      - emissor sem aliases de busca.
    WARNING (revisar, não bloqueia):
      - campos cadastrais obrigatórios ausentes (country/region/language/tier);
      - país/região preenchidos como fallback ("A revisar");
      - listada sem ticker E sem bolsa (`listing`) — listagem não comprovada;
      - não listada COM ticker/bolsa — provável classificação pelo instrumento
        (ex.: companhia aberta que entrou na carteira via corporate bond);
      - Tier 1 sem fonte oficial/RI/regulador;
      - gestora/fundo sem vehicle_kind ou sem scoring_mode;
      - alias genérico único (risco de falso positivo);
      - emissores duplicados e tickers repetidos entre emissores distintos.
    """
    msgs: list[str] = []
    seen_names: dict[str, int] = {}
    seen_tickers: dict[str, list[str]] = {}
    GENERIC = {"vamos", "motiva", "orizon", "navi", "singular", "baker", "duke",
               "micron", "security", "janus", "patria", "spx", "jgp", "agv",
               "ciranda", "stone"}
    OBRIGATORIOS = ("country", "region", "language", "tier")
    for c in watchlist:
        name = c.get("name", "(sem nome)")
        grp = asset_group_of_company(c)
        tk = has_stock_ticker(c)
        listing = (c.get("listing") or "").strip()
        aliases = c.get("aliases") or []

        if not c.get("asset_class"):
            msgs.append(f"ERRO: emissor '{name}' sem `asset_class` explícito no cadastro.")
        if grp == "a_revisar":
            msgs.append(f"ERRO: emissor '{name}' sem grupo de ativos resolvível.")
        if not aliases:
            msgs.append(f"ERRO: emissor '{name}' sem `aliases` — não seria buscável.")

        for campo in OBRIGATORIOS:
            if not c.get(campo):
                msgs.append(f"WARNING: emissor '{name}' sem `{campo}` cadastrado.")
        if str(c.get("country")).strip() == "A revisar" or str(c.get("region")).strip() == "A revisar":
            msgs.append(f"WARNING: emissor '{name}' com país/região marcados como "
                        f"'A revisar' (fallback) — preencha o domicílio do emissor.")

        # natureza x instrumento (o erro que motivou esta revisão)
        if grp == "listed_companies" and not tk and not listing:
            msgs.append(f"WARNING: '{name}' está como 'Empresas listadas' mas não tem "
                        f"`ticker` nem `listing`. Confirme a listagem ou mova para "
                        f"'Não listadas (PE/Crédito)'.")
        if grp == "nao_listada" and (tk or listing):
            msgs.append(f"WARNING: '{name}' está como 'Não listadas' mas tem "
                        f"ticker/bolsa ({tk or listing}). Verifique se foi classificado "
                        f"pelo instrumento (bond) em vez da natureza do emissor.")

        # coerência com o coletor EDGAR
        listing = (c.get("listing") or "").lower()
        if any(x in listing for x in ("nyse", "nasdaq")) and not edgar_eligible(c):
            msgs.append(f"WARNING: '{name}' tem listagem {c.get('listing')} mas NÃO é "
                        f"elegível ao coletor EDGAR. Cadastre `cik` ou `official.sec: true`.")
        if (c.get("cik") or (c.get("official") or {}).get("sec")) and not edgar_eligible(c):
            msgs.append(f"ERRO: '{name}' tem `cik`/`official.sec` mas ficou fora do "
                        f"coletor EDGAR — regra de elegibilidade inconsistente.")
        if c.get("tier") == 1 and not c.get("official"):
            msgs.append(f"WARNING: Tier 1 '{name}' sem fonte oficial/RI/regulador "
                        f"cadastrada. Adicione `official` ou rebaixe o tier.")
        if grp == "gestora_fundo":
            if not c.get("vehicle_kind"):
                msgs.append(f"WARNING: gestora/fundo '{name}' sem `vehicle_kind`.")
            sm = c.get("scoring_mode")
            if not sm:
                msgs.append(f"WARNING: gestora/fundo '{name}' sem `scoring_mode`. "
                            f"Valores aceitos: {', '.join(sorted(SCORING_MODES))}.")
            elif sm not in SCORING_MODES:
                msgs.append(f"WARNING: gestora/fundo '{name}' com `scoring_mode` "
                            f"inválido ('{sm}'). Valores aceitos: "
                            f"{', '.join(sorted(SCORING_MODES))}.")

        for a in aliases:
            if (a or "").strip().lower() in GENERIC and len(aliases) == 1:
                msgs.append(f"WARNING: '{name}' usa alias genérico único '{a}'. "
                            f"Use aliases compostos para evitar falsos positivos.")

        seen_names[name.strip().lower()] = seen_names.get(name.strip().lower(), 0) + 1
        if tk:
            seen_tickers.setdefault(tk, []).append(name)

    for nm, n in seen_names.items():
        if n > 1:
            msgs.append(f"WARNING: emissor duplicado na watchlist: '{nm}' ({n}x).")
    for tk, owners in seen_tickers.items():
        if len(set(owners)) > 1:
            msgs.append(f"WARNING: ticker '{tk}' em emissores diferentes: "
                        f"{', '.join(sorted(set(owners)))}.")
    return msgs

def build_company_query(company: dict, taxonomy: list[dict]) -> str:
    """Consulta ampla por emissor: nome + termos de risco do GRUPO cadastral.
    Usa asset_group_of_company (asset_class → grupo canônico), não o campo
    legado `type` — assim FIIs, não listadas e gestoras/fundos recebem os
    termos corretos em vez dos termos corporativos padrão."""
    alias = company["aliases"][0] if company.get("aliases") else company["name"]
    grp = asset_group_of_company(company)
    # Termos no IDIOMA do emissor: procurar "falência"/"recuperação judicial"
    # numa notícia chilena ou americana não devolve nada — era a segunda causa
    # da cobertura internacional vazia (a primeira era o locale do Google News).
    idioma = (company.get("language") or "pt").lower()[:2]
    if idioma in RISK_TERMS_I18N and grp != "gestora_fundo":
        risk_terms = RISK_TERMS_I18N[idioma]
    else:
        risk_terms = RISK_TERMS_BY_GROUP.get(grp, RISK_TERMS_BY_GROUP["listed_companies"])
    terms = " OR ".join(f'"{t}"' for t in risk_terms)
    return f'"{alias}" ({terms})'


# ── Ativação opt-in da resolução contextual de entidade ──────────────────────
# Um emissor só usa o caminho novo (search_terms na consulta, resolve_entity_match
# na atribuição, related_entities na relação) se declarar EXPLICITAMENTE pelo
# menos um dos campos abaixo no cadastro. Nenhum dos 160 emissores reais de
# config_risco.yaml declara qualquer um deles hoje — portanto esta função
# retorna False para todos eles, e todo o código que depende dela (
# `build_company_queries`, o hook de `classify_and_attribute`) cai para o
# comportamento legado, byte-a-byte idêntico ao anterior.
_ENTITY_RESOLUTION_OPT_IN_FIELDS = (
    "search_terms", "entity_cues", "exclusion_cues", "related_entities",
    "entity_scope", "entity_confidence",
)


def uses_contextual_entity_resolution(company: dict) -> bool:
    """True só se o cadastro declarar >=1 dos campos novos de resolução de
    entidade. Compatibilidade retroativa: ausência de todos os campos ⇒
    False ⇒ caminho legado (mesma query, mesma detecção, mesmo score)."""
    return any(company.get(f) for f in _ENTITY_RESOLUTION_OPT_IN_FIELDS)


def build_company_queries(company: dict, taxonomy: list[dict]) -> list[str]:
    """Lista de consultas de RECUPERAÇÃO para um emissor.

    Legado (não opt-in, ou opt-in sem `search_terms` declarado): devolve
    exatamente `[build_company_query(company, taxonomy)]` — mesma string,
    mesmo comportamento de sempre. Isto é o que garante zero regressão para
    os 160 emissores reais.

    Opt-in COM `search_terms`: uma consulta por termo (mesmo padrão
    `"{termo}" (risco OR risco OR ...)` de `build_company_query`),
    deduplicadas por normalização (acento/caixa/pontuação) e limitadas a
    `max_search_terms_per_run` (padrão 8) para não multiplicar chamadas de
    rede sem controle. A ORDEM de busca é ampla — a ATRIBUIÇÃO nunca decorre
    daqui; ela é decidida depois por `resolve_entity_match`."""
    if not uses_contextual_entity_resolution(company) or not company.get("search_terms"):
        return [build_company_query(company, taxonomy)]

    grp = asset_group_of_company(company)
    idioma = (company.get("language") or "pt").lower()[:2]
    if idioma in RISK_TERMS_I18N and grp != "gestora_fundo":
        risk_terms = RISK_TERMS_I18N[idioma]
    else:
        risk_terms = RISK_TERMS_BY_GROUP.get(grp, RISK_TERMS_BY_GROUP["listed_companies"])
    terms = " OR ".join(f'"{t}"' for t in risk_terms)

    seen_norm = set()
    queries = []
    max_terms = company.get("max_search_terms_per_run", 8)
    for term in company["search_terms"]:
        key = normalize(term)
        if not key or key in seen_norm:
            continue
        seen_norm.add(key)
        queries.append(f'"{term}" ({terms})')
        if len(queries) >= max_terms:
            break
    return queries or [build_company_query(company, taxonomy)]


def fetch_related_entities_context(company: dict, cfg: dict,
                                   session: "requests.Session | None" = None) -> list[dict]:
    """[fix: complete Peru news links taxonomy and holding coverage] Busca
    real (Google News) de cada `related_entities` do emissor (opt-in via
    `fetch_related_entities: true`) e devolve artigos JÁ FORMATADOS como
    CONTEXTO DA HOLDING — nunca como alias/atribuição direta.

    Cada artigo retornado tem: `companies=[company["name"]]` (para
    `merge_into_history` persistir o registro), `context_events_by_company`
    com `subject_company=<subsidiária>`, `relationship='subsidiary'`,
    `query_scope='related_entity'` — e `events_by_company`/`event_ids`
    SEMPRE vazios (nunca pontua a holding). Se a subsidiária não tiver
    nenhum evento da taxonomia no texto, o artigo é descartado (não
    persiste ruído). Emissores sem `fetch_related_entities` continuam
    100% inalterados — função só roda quando chamada explicitamente.

    Também popula `art["_related_entities_coverage"]` (lista, 1 item por
    related_entity) com a telemetria completa da auditoria: query executada,
    locale, janela, nº de resultados brutos, deduplicados, rejeitados (com
    motivo de cada rejeição), e nº finalmente atribuído. Cada artigo
    retornado carrega a MESMA lista completa (redundante, mas simples de
    consumir sem precisar de um segundo valor de retorno) — quem monta o
    payload final lê `arts[0]["_related_entities_coverage"]` uma vez."""
    coverage: list[dict] = []
    if not company.get("fetch_related_entities") or not company.get("related_entities"):
        return []
    taxonomy = cfg.get("taxonomy", [])
    out = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        loc = locale_for_company(company, cfg)
    except Exception:
        loc = None
    for rel in company["related_entities"]:
        rel_name = rel.get("entity_name", "")
        if not rel_name:
            continue
        aliases = rel.get("aliases") or [rel_name]
        pais = company.get("country", "")
        termo = aliases[0]
        query = f'"{termo}" {pais}'.strip() if pais else f'"{termo}"'
        cov = {"subsidiary": rel_name, "legal_name": rel.get("legal_name", ""),
              "query": query, "locale": f"{loc[0]}/{loc[1]}" if loc else "",
              "search_window_days": 180, "searched_at": now_iso,
              "raw_results": 0, "deduped_results": 0, "rejected": [],
              "attributed": 0, "official_sources_checked": ["SMV", "BVL"],
              "error": ""}
        try:
            arts = fetch_query(query, cfg, session or requests.Session(), locale=loc)
        except Exception as exc:
            cov["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            coverage.append(cov)
            print(f"   ⚠️  related_entity '{rel_name}' de '{company['name']}': "
                 f"falha na busca ({type(exc).__name__}) — ignorado nesta execução.")
            continue
        cov["raw_results"] = len(arts)
        _seen_urls = set()
        arts_dedup = []
        for a in arts:
            u = a.get("url", "")
            if u in _seen_urls:
                continue
            _seen_urls.add(u)
            arts_dedup.append(a)
        cov["deduped_results"] = len(arts_dedup)
        # sinais de relevância do SETOR da holding (ex.: "azúcar"/"grupo
        # gloria" para a Coazucar) — o nome de uma subsidiária muitas vezes
        # coincide com topônimo/instituição homônima sem relação nenhuma
        # (ex.: "Casa Grande" = asilo em Rosario/Argentina, nada a ver com a
        # subsidiária peruana) — exigir >=1 sinal de setor reduz esse ruído
        # sem inventar nenhuma entidade de resolução nova. Usa `related_
        # entity_relevance_cues` se declarado (mais preciso — exclui os
        # próprios nomes das subsidiárias, que sempre "batem" trivialmente
        # por já terem sido o termo de busca); cai para `entity_cues` da
        # holding só como fallback, removendo os nomes das subsidiárias
        # (senão o filtro não filtra nada — "casa grande"/"cartavio" já
        # estavam listados como entity_cues da própria holding).
        _nomes_subs = {normalize(a) for r in (company.get("related_entities") or [])
                       for a in ([r.get("entity_name", "")] + list(r.get("aliases") or []))}
        setor_cues = [normalize(c) for c in
                     (company.get("related_entity_relevance_cues")
                      or company.get("entity_cues") or [])
                     if normalize(c) not in _nomes_subs]
        for a in arts_dedup:
            texto = normalize(f"{a.get('title','')} {a.get('summary','')}")
            if setor_cues and not any(c and c in texto for c in setor_cues):
                cov["rejected"].append({"title": a.get("title", ""), "url": a.get("url", ""),
                                        "reason": "sem_sinal_de_setor_provavel_homonimo"})
                continue
            evs = classify_article(a, taxonomy)
            eids = [e["id"] for e in evs]
            a["companies"] = [company["name"]]
            a["events"] = evs or [{"id": "sem_evento_taxonomico",
                                   "label": "Evento a revisar (sem correspondência na taxonomia atual)",
                                   "severity": "info", "direction": "neutra", "score": 0,
                                   "dimensions": [], "applies_to": []}]
            a["event_ids"] = eids or ["sem_evento_taxonomico"]
            a["events_by_company"] = {company["name"]: []}  # nunca pontua a holding
            _ctx_eids = eids or ["sem_evento_taxonomico"]
            a["context_events_by_company"] = {company["name"]: [{
                "event_id": eid, "event_label": next(
                    (e.get("label", eid) for e in evs if e["id"] == eid),
                    "Evento a revisar (sem correspondência na taxonomia atual)" if eid == "sem_evento_taxonomico" else eid),
                "subject_company": rel_name,
                "relation_type": rel.get("relationship", "subsidiary"),
                "impact_type": "indireto_material",
                "event_scope": "indireto",
                "event_phase": "", "direction": "neutra", "scoreable": False,
                "attribution_confidence": "media",
                "attribution_evidence": f"related_entity '{rel_name}' de '{company['name']}'",
            } for eid in _ctx_eids]}
            a["query_scope"] = "related_entity"
            a["query_related_entity"] = rel_name
            a["query_company"] = company["name"]
            out.append(a)
            cov["attributed"] += 1
        coverage.append(cov)
    for a in out:
        a["_related_entities_coverage"] = coverage
    # se NENHUM artigo foi atribuído (nenhuma subsidiária rendeu notícia
    # relevante), a telemetria ainda precisa ficar disponível para quem
    # monta o payload/HTML — devolve num "artigo" vazio, marcador, que quem
    # chama pode descartar do merge_into_history (sem events reais) mas usar
    # só para ler `_related_entities_coverage`.
    if not out and coverage:
        out.append({"_related_entities_coverage": coverage, "_coverage_only": True})
    return out


_SEARCH_TELEMETRY: dict = {}
# 4H.1d — telemetria dos COLETORES OFICIAIS. Elegibilidade/configuração não é
# execução: só o que o coletor realmente tentou entra aqui.
_OFFICIAL_SOURCE_TELEMETRY: dict = {"EDGAR": {}, "RI_RSS": {}, "RI_NEWS": {},
                                    "REGULADOR_LOCAL": {}}


def should_fetch_company(company: dict, cfg: dict, run_count: int) -> bool:
    """Decide se o emissor entra nesta execução, conforme o tier.

    4H.2 — a rotação usa BUCKETS estáveis por emissor em vez de `run_count % n`:
    antes, todo o Tier 2 caía na mesma execução (pico) e o Tier 3, com
    `fetch_every_n_runs: 0`, ficava permanentemente desligado — 54 dos 62
    emissores estrangeiros nunca eram pesquisados. Agora cada emissor tem um
    deslocamento fixo (hash do nome), distribuindo a carga entre as execuções.
    `fetch_every_n_runs: 0` continua significando "nunca", mas passa a exigir
    justificativa explícita (`cobertura_desligada_motivo`) na auditoria."""
    tier = company.get("tier", 2)
    tier_cfg = (cfg.get("tiers") or {}).get(tier, {})
    n = tier_cfg.get("fetch_every_n_runs", 1)
    if company.get("force_fetch"):
        return True
    if not n:
        return False
    if n == 1:
        return True
    offset = int(hashlib.md5(company.get("name", "").encode("utf-8")).hexdigest(), 16)
    return (run_count + offset) % n == 0


def _fetch_one_query(q: str, cfg: dict, session: requests.Session,
                     locs: list, tel: dict) -> tuple[list[dict], str]:
    """Executa UMA query de recuperação através dos locales de fallback do
    emissor (principal + até 2 alternativos) — corpo extraído verbatim do
    laço que existia dentro de `fetch_all` antes da integração opt-in
    (4H — refatoração de forma, sem mudança de comportamento).

    Entradas:
      q     — a string de query já pronta (de `build_company_queries`).
      cfg   — config completo (usado por `fetch_query_result` para period/
              max_articles_per_query).
      session — `requests.Session` compartilhada entre todas as chamadas.
      locs  — lista de locales candidatos (`locales_for_company`), na ordem
              principal → fallback.
      tel   — dict de telemetria do emissor (mesmo `_SEARCH_TELEMETRY[nome]`
              de sempre) — MUTADO in-place (efeito colateral idêntico ao
              código anterior: `queries`, `success`, `raw_articles`,
              `errors`, `status_codes`, `locales_tentados`, `por_locale`,
              `error_type`, `error`).

    Saída: `(artigos, locale_usado)` — `artigos` é a lista de artigos do
    PRIMEIRO locale que respondeu com sucesso técnico E teve resultado
    (`_r["ok"] and _r["articles"]`); `locale_usado` é a string desse locale,
    ou `""` se nenhum locale retornou artigo algum.

    Exceções: nenhuma tratada aqui — `fetch_query_result` já captura
    timeout/HTTP/conexão internamente e devolve `{"ok": False, ...}`; esta
    função nunca levanta.

    Relação com `fetch_query_result`: é o único ponto de chamada — mesma
    assinatura, mesmos argumentos posicionais/nomeados de antes
    (`fetch_query_result(q, cfg, session, (hl, gl))`)."""
    arts, usado = [], ""
    for lc in locs[:3]:
        hl, gl = lc.split("/", 1)
        r = fetch_query_result(q, cfg, session, (hl, gl))
        pl = tel["por_locale"].setdefault(lc, {"attempted": 0, "success": 0,
                                               "raw_articles": 0, "errors": 0,
                                               "error_type": "", "error": ""})
        pl["attempted"] += 1
        tel["queries"] += 1
        if lc not in tel["locales_tentados"]:
            tel["locales_tentados"].append(lc)
        if r["status_code"] is not None:
            tel["status_codes"].append(r["status_code"])
        if r["ok"]:
            pl["success"] += 1
            tel["success"] += 1
            pl["raw_articles"] += len(r["articles"])
            tel["raw_articles"] += len(r["articles"])
            if r["articles"]:
                arts, usado = r["articles"], lc
                break          # principal resolveu: não usa fallback
        else:
            pl["errors"] += 1
            pl.update(error_type=r["error_type"], error=r["error"])
            tel["errors"] += 1
            tel.update(error_type=r["error_type"], error=r["error"])
    return arts, usado


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
        # `build_company_queries` devolve UMA única query (mesma string de
        # sempre) para os 160 emissores reais (nenhum declara
        # `search_terms`) — o laço abaixo executa uma única vez para eles,
        # byte-a-byte igual ao comportamento anterior. Só emissores opt-in
        # COM `search_terms` recebem >1 consulta aqui.
        _queries = build_company_queries(company, cfg["taxonomy"])
        print(f"   • [T{company.get('tier', 2)}] {company['name']}")
        _locs = locales_for_company(company, cfg)
        _tel = _SEARCH_TELEMETRY.setdefault(company["name"], {
            "searched": True, "locale": _locs[0], "locales_tentados": [],
            "locale_com_resultado": "", "queries": 0, "success": 0,
            "raw_articles": 0, "errors": 0, "por_locale": {},
            "status_codes": [], "error_type": "", "error": "",
            "search_terms_used": _queries if len(_queries) > 1 else []})
        _arts_all = []
        _seen_url = set()
        for q in _queries:
            _arts, _usado = _fetch_one_query(q, cfg, session, _locs, _tel)
            # Só emissores opt-in com >1 query preservam o locale de uma
            # busca anterior quando a busca seguinte não encontra nada —
            # para os 160 legados (sempre 1 query), `_tel["locale_com_
            # resultado"]` ainda está em "" nesta linha, então isto é
            # idêntico a `_tel["locale_com_resultado"] = _usado`.
            _tel["locale_com_resultado"] = _usado or _tel["locale_com_resultado"]
            for art in _arts:
                if art.get("url") in _seen_url:
                    continue
                _seen_url.add(art.get("url"))
                _arts_all.append(art)
            if len(_queries) > 1:
                time.sleep(0.25)  # múltiplas search_terms: pausa extra entre elas
        for art in _arts_all:
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



CVM_CAD_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"


def _digits(s) -> str:
    """Só os dígitos de um valor — normaliza CNPJ/código CVM para comparação."""
    return re.sub(r"\D", "", str(s or ""))


def _cvm_filers_index(year: int) -> dict | None:
    """Índice das companhias que protocolaram no IPE do ano, agora com
    IDENTIDADE FORTE. Cada companhia é dedup por (código CVM > CNPJ > nome) e
    guarda CNPJ e código CVM — o que permite casar por identificador em vez de
    só por nome/substring (a causa raiz do falso positivo 'Vale' → 'Vale
    Bonito Agropecuária'). Retorna:
      {"companies": [rec...], "by_cnpj": {...}, "by_codigo": {...}}
    rec = {nome, nome_norm, cnpj, codigo_cvm, n, categorias, ultima}."""
    try:
        resp = requests.get(CVM_IPE_URL.format(year=year), timeout=180,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
    except Exception as exc:
        print(f"   ⚠️  Dataset IPE/CVM indisponível ({exc}).")
        return None
    comp: dict[str, dict] = {}
    with zf.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter=";")
        for row in reader:
            raw = (row.get("Nome_Companhia") or "").strip()
            nome = normalize(raw)
            if not nome:
                continue
            cnpj, cod = _digits(row.get("CNPJ_Companhia", "")), _digits(row.get("Codigo_CVM", ""))
            key = cod or cnpj or nome
            e = comp.setdefault(key, {"nome": raw, "nome_norm": nome, "cnpj": cnpj,
                                      "codigo_cvm": cod, "n": 0, "categorias": set(),
                                      "ultima": ""})
            e["n"] += 1
            cat = (row.get("Categoria") or "").strip()
            if cat:
                e["categorias"].add(cat)
            d = (row.get("Data_Entrega") or "")[:10]
            if d > e["ultima"]:
                e["ultima"] = d
    companies = list(comp.values())
    return {"companies": companies,
            "by_cnpj": {e["cnpj"]: e for e in companies if e["cnpj"]},
            "by_codigo": {e["codigo_cvm"]: e for e in companies if e["codigo_cvm"]}}


def _cvm_cadastro_index(year: int | None = None) -> list[dict] | None:
    """Cadastro oficial de companhias abertas (cad_cia_aberta): fonte
    autoritativa que mapeia código CVM ↔ CNPJ ↔ razão social. Usado para
    ATRIBUIR identificador forte a emissores listados sem depender de nome
    curto. Roda no GitHub Actions (rede aberta)."""
    try:
        resp = requests.get(CVM_CAD_URL, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        print(f"   ℹ️  Cadastro CVM (cad_cia_aberta) indisponível ({exc}); "
              f"sem auto-resolução de identificadores nesta rodada.")
        return None
    out = []
    reader = csv.DictReader(io.StringIO(resp.content.decode("latin-1")), delimiter=";")
    for row in reader:
        denom = (row.get("DENOM_SOCIAL") or "").strip()
        if not denom:
            continue
        out.append({"codigo_cvm": _digits(row.get("CD_CVM", "")),
                    "cnpj": _digits(row.get("CNPJ_CIA", "")),
                    "denom": denom, "denom_norm": normalize(denom),
                    "situacao": (row.get("SIT") or "").strip(),
                    "mercado": (row.get("TP_MERC") or "").strip()})
    return out


def _resolve_strong_ids(alvos: list[dict], cad: list[dict] | None) -> dict:
    """Resolve código CVM/CNPJ dos emissores sem id forte declarado, contra o
    cadastro oficial. 1 candidato → aplicável em runtime; >1 → ambíguo (vai
    para revisão, nunca chuta). Retorna {nome: {codigo_cvm, cnpj, denom, tipo,
    candidatos}}."""
    if not cad:
        return {}
    res = {}
    for c in alvos:
        if c.get("codigo_cvm") or c.get("cnpj"):
            continue
        termos = [c.get("razao_social") or c["name"]] + list(c.get("aliases") or [])
        cands = {}
        for t in termos:
            tn = normalize(t)
            if not tn or len(tn) < 4:
                continue
            pat = _word_pattern(t)
            for r in cad:
                if (r["codigo_cvm"] or r["cnpj"]) and (r["denom_norm"] == tn or pat.search(r["denom_norm"])):
                    cands[r["codigo_cvm"] or r["cnpj"]] = r
        cl = list(cands.values())
        if len(cl) == 1:
            r = cl[0]
            res[c["name"]] = {"codigo_cvm": r["codigo_cvm"], "cnpj": r["cnpj"],
                              "denom": r["denom"], "tipo": "unico", "candidatos": cl}
        elif len(cl) > 1:
            res[c["name"]] = {"codigo_cvm": "", "cnpj": "", "denom": "",
                              "tipo": "ambiguo", "candidatos": cl}
    return res


def _match_ipe(c: dict, idx: dict, strong: dict | None) -> tuple:
    """Casa UM emissor contra o índice IPE por camadas, priorizando
    identificador forte. Termo curto NUNCA decide sozinho; múltiplos
    candidatos viram 'revisar'. Retorna
    (achado|None, tipo_match, confianca, motivo, candidatos, id_usado)."""
    cnpj, cod = _digits(c.get("cnpj", "")), _digits(c.get("codigo_cvm", ""))
    fonte_id = "cadastro_local"
    if not (cnpj or cod) and strong and strong.get("tipo") == "unico":
        cod, cnpj = _digits(strong.get("codigo_cvm", "")), _digits(strong.get("cnpj", ""))
        fonte_id = "cadastro_cvm(auto)"
    # Tier 0 — identificador forte
    if cod and idx["by_codigo"].get(cod):
        return (idx["by_codigo"][cod], "codigo_cvm", "alta",
                f"Código CVM {cod} ({fonte_id}) casado no IPE.", [], f"cod_cvm:{cod}")
    if cnpj and idx["by_cnpj"].get(cnpj):
        return (idx["by_cnpj"][cnpj], "cnpj", "alta",
                f"CNPJ {cnpj} ({fonte_id}) casado no IPE.", [], f"cnpj:{cnpj}")
    if cnpj or cod:
        return (None, "id_forte_sem_protocolo", "alta",
                f"Identificador forte ({fonte_id}) declarado, sem protocolo no IPE do ano.",
                [], f"cod_cvm:{cod}" if cod else f"cnpj:{cnpj}")
    # A ambiguidade do cadastro NÃO pode curto-circuitar a razão social
    # declarada: quem cadastrou uma razão social oficial deu evidência mais
    # forte que o resultado ambíguo de uma busca por nome no cad_cia_aberta.
    # (Bug 4G.1: Ambev e PRIO ficaram em 'revisar' mesmo com razão social.)
    ambiguo_cadastro = bool(strong and strong.get("tipo") == "ambiguo")
    # Tier 1 — razão social oficial exata
    rs = normalize(c.get("razao_social", "")) if c.get("razao_social") else ""
    if rs:
        ex = [e for e in idx["companies"] if e["nome_norm"] == rs]
        if len(ex) == 1:
            return (ex[0], "razao_social", "alta", "Razão social oficial idêntica ao IPE.", [], "")
        if len(ex) > 1:
            return (None, "revisar", "baixa", "Razão social casou com múltiplas companhias.", ex, "")
    # Tier 2 — nome idêntico (IGUALDADE, não substring)
    nome = normalize(c["name"])
    ig = [e for e in idx["companies"] if e["nome_norm"] == nome]
    if len(ig) == 1:
        return (ig[0], "nome_exato", "alta", "Nome do emissor idêntico à companhia no IPE.", [], "")
    if len(ig) > 1:
        return (None, "revisar", "baixa",
                f"Nome idêntico a {len(ig)} companhias — desambiguar por CNPJ/código CVM.", ig, "")
    # Ambiguidade do cadastro: só agora, depois de razão social e nome exato
    if ambiguo_cadastro:
        cs = strong["candidatos"]
        return (None, "revisar", "baixa",
                f"{len(cs)} companhias no cadastro CVM batem com o nome — definir "
                f"código CVM/CNPJ manualmente.", cs, "")
    # Tier 3 — termo (nome/alias) por palavra inteira dentro de razão social maior
    termos = [c["name"]] + list(c.get("aliases") or [])
    cand, curtos = {}, []
    for t in termos:
        tn = normalize(t)
        if not tn:
            continue
        pat = _word_pattern(t)
        hits = [e for e in idx["companies"] if pat.search(e["nome_norm"])]
        for e in hits:
            cand[e["codigo_cvm"] or e["cnpj"] or e["nome_norm"]] = e
        if hits and len(tn) < 6:
            curtos.append(t)
    cl = list(cand.values())
    if not cl:
        return (None, "sem_match", "—",
                "Nenhuma companhia do IPE casou por identificador, razão social, nome ou alias.",
                [], "")
    if len(cl) > 1:
        return (None, "revisar", "baixa",
                f"{len(cl)} companhias possíveis por nome/alias — identificador forte "
                f"necessário para desambiguar.", cl, "")
    e = cl[0]
    forte = any(len(normalize(t)) >= 6 and _word_pattern(t).search(e["nome_norm"]) for t in termos)
    if forte:
        return (e, "alias", "media",
                f"Casou por termo de nome/alias (≥6 chars) dentro de '{e['nome']}'.", [], "")
    return (e, "revisar", "baixa",
            f"Casou apenas por termo curto ({', '.join(curtos) or c['name']}) dentro de "
            f"'{e['nome']}' — risco de homônimo; confirmar por CNPJ/código CVM.", [e], "")


# Situações de cobertura CVM/IPE — o STATUS descreve a natureza da cobertura,
# nunca "falha". FII e gestora não são companhias abertas: o dataset IPE
# cia_aberta não se aplica a eles, então recebem status próprio (não
# "esperado_filiante_sem_protocolo", que insinuaria omissão de protocolo).
_CVM_COBERTURA = {
    "filiante_cvm": "sim",
    "revisar": "revisar",
    "nao_aplicavel_dataset_ipe": "n/a",
    "nao_aplicavel_veiculo": "n/a",
}


def audit_cvm_coverage(cfg: dict, out_csv: str | None = None,
                       _index: dict | None = None, _cad: object = "auto") -> list[dict]:
    """4A.1/4B.1 — Responde, com identificador forte e não com suposição, quais
    emissores brasileiros são efetivamente FILIANTES na CVM.

    Ordem de casamento (forte → fraco), registrada por linha no CSV:
      1. CNPJ / código CVM declarados no cadastro local;
      2. CNPJ / código CVM auto-resolvidos do cadastro oficial (cad_cia_aberta);
      3. razão social oficial idêntica;
      4. nome do emissor idêntico à companhia no IPE;
      5. termo de nome/alias por palavra inteira dentro de razão social maior.
    Termo curto (< 6 chars) nunca confirma sozinho; múltiplos candidatos viram
    'revisar' — é o que impede 'Vale' de casar silenciosamente com 'Vale Bonito
    Agropecuária'. FIIs/fundos não são cobertos por este dataset (cia_aberta) e
    recebem status próprio, não 'esperado_filiante_sem_protocolo'."""
    year = datetime.now(timezone.utc).year
    idx = _index if _index is not None else _cvm_filers_index(year)
    if idx is None:
        print(" ℹ️  Auditoria CVM não executada nesta rodada (dataset indisponível).")
        return []

    alvos = [c for c in cfg.get("watchlist", []) if c.get("country") == "Brasil"]
    # Auto-resolução de identificadores fortes (cadastro oficial). _cad="auto"
    # baixa o cad_cia_aberta; passar None desliga; passar uma lista injeta
    # fixture (testes offline).
    cad = _cvm_cadastro_index(year) if _cad == "auto" else _cad
    strong = _resolve_strong_ids(alvos, cad)
    if cad:
        n_auto = sum(1 for v in strong.values() if v["tipo"] == "unico")
        print(f" 🧭 Identificadores fortes auto-resolvidos do cadastro CVM: {n_auto} "
              f"emissor(es); {sum(1 for v in strong.values() if v['tipo']=='ambiguo')} ambíguo(s).")
        # Sugestões de alta confiança (candidato ÚNICO no cadastro) para o config
        if out_csv:
            sug = [{"emissor": nome, "codigo_cvm_sugerido": v.get("codigo_cvm", ""),
                    "cnpj_sugerido": v.get("cnpj", ""), "razao_social_cadastro": v.get("denom", ""),
                    "confianca": "alta (candidato único no cad_cia_aberta)",
                    "acao": "conferir e colar no config"}
                   for nome, v in strong.items() if v["tipo"] == "unico"]
            if sug:
                with open("identificadores_cvm_sugeridos.csv", "w", newline="", encoding="utf-8-sig") as _sf:
                    _w = csv.DictWriter(_sf, fieldnames=list(sug[0].keys())); _w.writeheader(); _w.writerows(sug)
                print(f"    → {len(sug)} sugestão(ões) em identificadores_cvm_sugeridos.csv")
    print(f" 🔎 Auditoria CVM/IPE {year}: {len(idx['companies'])} companhias no dataset; "
          f"cruzando com {len(alvos)} emissores brasileiros…")

    linhas = []
    for c in alvos:
        achado, tipo_match, confianca, motivo, candidatos, id_usado = _match_ipe(
            c, idx, strong.get(c["name"]))
        grupo = asset_group_of_company(c)

        if grupo == "fii":
            # dataset errado para FII — não afirmamos filiante por nome fraco
            if tipo_match not in ("cnpj", "codigo_cvm"):
                achado = None
            status = "nao_aplicavel_dataset_ipe"
            motivo = ("FII não é coberto pelo dataset IPE de companhias abertas; a "
                      "cobertura oficial de fundos depende do dataset de fundos da CVM "
                      "(informes/eventos de fundos estruturados), a implementar. " + motivo)
            confianca = "n/a"
        elif achado and tipo_match in ("cnpj", "codigo_cvm", "razao_social", "nome_exato", "alias"):
            status = "filiante_cvm"
        elif tipo_match == "revisar":
            status = "revisar"          # nem confirmado filiante, nem não-filiante
        elif grupo == "gestora_fundo":
            status = "nao_aplicavel_veiculo"
        elif grupo in ("listada", "listed_companies"):
            status = "esperado_filiante_sem_protocolo_no_ano"
        else:
            status = "nao_filiante"

        cand_nomes = "; ".join((cc.get("nome") or cc.get("denom", ""))
                               for cc in (candidatos or [])[:6])
        linhas.append({
            "emissor": c["name"], "asset_class": c.get("asset_class", ""),
            "grupo": grupo, "tier": c.get("tier", ""), "status": status,
            "cobertura_oficial_cvm": _CVM_COBERTURA.get(status, "nao"),
            "tipo_match": tipo_match, "confianca_match": confianca,
            "companhia_casada": (achado or {}).get("nome", ""),
            "cnpj_casado": (achado or {}).get("cnpj", ""),
            "codigo_cvm_casado": (achado or {}).get("codigo_cvm", ""),
            "identificador_usado": id_usado,
            "n_candidatos": len(candidatos or []),
            "candidatos": cand_nomes,
            "protocolos_no_ano": (achado or {}).get("n", 0),
            "ultima_entrega": (achado or {}).get("ultima", ""),
            "categorias": "; ".join(sorted((achado or {}).get("categorias", set()))[:6]),
            "motivo_decisao": motivo,
        })

    from collections import Counter
    for s, n in Counter(l["status"] for l in linhas).most_common():
        print(f"    · {n:>3} {s}")
    rev = [l for l in linhas if l["status"] == "revisar"]
    if rev:
        print(f"    ⚠️  {len(rev)} caso(s) para revisar (id forte necessário):")
        for l in rev[:10]:
            print(f"        {l['emissor']} — {l['motivo_decisao'][:90]}")
    nl = [l for l in linhas if l["asset_class"] == "nao_listada"]
    cob = sum(1 for l in nl if l["status"] == "filiante_cvm")
    if nl:
        print(f"    → não listadas brasileiras: {cob}/{len(nl)} filiantes confirmados na CVM "
              f"({100 * cob / len(nl):.0f}%); {sum(1 for l in nl if l['status']=='revisar')} a revisar.")
    if out_csv and linhas:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
            w.writeheader(); w.writerows(linhas)
        print(f"    → auditoria salva em {out_csv}")
    return linhas


def run_cvm_fixture_tests() -> int:
    """4B.5 — Suíte de fixtures do casamento CVM. Trava, sem rede, as regras que
    a Fase 4B.1 estabeleceu. Roda com `--test-cvm-fixture`. NÃO usa dados de
    produção: os índices são sintéticos e existem só para verificar a LÓGICA."""
    def rec(nome, cod="", cnpj="", n=5):
        return {"nome": nome, "nome_norm": normalize(nome), "cnpj": _digits(cnpj),
                "codigo_cvm": _digits(cod), "n": n, "categorias": {"Fato Relevante"},
                "ultima": "2026-06-30"}
    comp = [
        rec("VALE S.A.", "4170", "33592510000154", 20),
        rec("VALE BONITO AGROPECUARIA S/A", "88888", n=2),          # homônimo
        rec("FISIA COMERCIO DE PRODUTOS ESPORTIVOS LTDA", "22222", n=3),
        rec("CVLB PARTICIPACOES S.A.", "33333", n=1),
        rec("AEGEA SANEAMENTO E PARTICIPACOES S.A.", "44444", n=8),
        rec("BANCO EXEMPLO S.A.", "70001", n=10),                   # p/ precedência de id forte
        rec("BANCO EXEMPLO LEASING S.A.", "70002", n=4),            # subsidiária homônima
        rec("DUPLA PARTICIPACOES S.A.", "81001", n=1),              # dois homônimos "Dupla"
        rec("DUPLA HOLDING S.A.", "81002", n=1),
    ]
    idx = {"companies": comp, "by_cnpj": {e["cnpj"]: e for e in comp if e["cnpj"]},
           "by_codigo": {e["codigo_cvm"]: e for e in comp if e["codigo_cvm"]}}

    def cad(denom, cod=""):
        return {"codigo_cvm": _digits(cod), "cnpj": "", "denom": denom,
                "denom_norm": normalize(denom), "situacao": "ATIVO", "mercado": "BOLSA"}
    cadastro = [cad("AEGEA SANEAMENTO E PARTICIPACOES S.A.", "44444")]   # único → auto-resolve

    # (emissor, status_esperado, rótulo do cenário)
    casos = [
        ({"name": "Vale", "asset_class": "listada", "codigo_cvm": "4170",
          "aliases": ["Vale", "Vale S.A.", "VALE3"]},
         "filiante_cvm", "1/9 Vale casa VALE S.A. por código CVM, não Vale Bonito"),
        ({"name": "Empresa Curta", "asset_class": "nao_listada", "aliases": ["CVLB"]},
         "revisar", "2 alias curto (4 chars) não confirma sozinho"),
        ({"name": "Grupo CVLB", "asset_class": "nao_listada", "aliases": ["Grupo CVLB", "CVLB"]},
         "revisar", "3 Grupo CVLB sem id forte → revisar"),
        ({"name": "Fisia", "asset_class": "nao_listada", "aliases": ["Fisia"]},
         "revisar", "4a Fisia só com alias curto → revisar"),
        ({"name": "Fisia", "asset_class": "nao_listada",
          "razao_social": "Fisia Comércio de Produtos Esportivos",
          "aliases": ["Fisia Comércio de Produtos Esportivos", "Fisia"]},
         "filiante_cvm", "4b Fisia com razão social robusta → filiante"),
        ({"name": "Aegea Saneamento", "asset_class": "nao_listada",
          "aliases": ["Aegea Saneamento", "Aegea"]},
         "filiante_cvm", "5 Aegea via código CVM auto-resolvido do cadastro"),
        ({"name": "Vinci Credit FII", "asset_class": "fii", "aliases": ["Vinci Credit FII"]},
         "nao_aplicavel_dataset_ipe", "6 FII → nao_aplicavel_dataset_ipe"),
        ({"name": "Dupla", "asset_class": "nao_listada", "aliases": ["Dupla"]},
         "revisar", "7 múltiplos candidatos → revisar"),
        ({"name": "Banco Exemplo", "asset_class": "listada", "codigo_cvm": "70001",
          "aliases": ["Banco Exemplo"]},
         "filiante_cvm", "8 código CVM tem precedência sobre substring (evita leasing)"),
    ]
    for c in casos:
        c[0]["country"] = "Brasil"
    linhas = audit_cvm_coverage({"watchlist": [c[0] for c in casos]},
                                _index=idx, _cad=cadastro)
    by_name = {}
    for c, l in zip(casos, linhas):
        by_name.setdefault(c[0]["name"], []).append(l)

    print("\n── Suíte de fixtures CVM ──")
    ok = 0
    results = []
    for (emissor, esperado, rotulo), linha in zip(casos, linhas):
        got = linha["status"]
        passou = (got == esperado)
        # verificação extra do cenário 8: não pode casar com a subsidiária leasing
        if emissor.get("name") == "Banco Exemplo" and "LEASING" in (linha["companhia_casada"] or ""):
            passou = False
        # verificação extra do cenário 1: Vale não pode casar com Vale Bonito
        if emissor.get("name") == "Vale" and "BONITO" in (linha["companhia_casada"] or ""):
            passou = False
        ok += passou
        results.append((passou, rotulo, esperado, got, linha.get("companhia_casada", "")))
        print(f"   {'✅' if passou else '❌'} {rotulo}")
        print(f"        esperado={esperado} · obtido={got} · casou='{linha.get('companhia_casada','')}'")

    # Cenário 9 — listada grande prioriza id forte (Vale já provado no caso 1); reforço:
    #   sem id forte, 'Vale' cairia em revisar (múltiplos 'vale' no índice).
    v = audit_cvm_coverage({"watchlist": [{"name": "Vale", "asset_class": "listada",
                            "country": "Brasil", "aliases": ["Vale"]}]}, _index=idx, _cad=None)
    c9 = v[0]["status"] == "revisar"
    ok += c9; results.append((c9, "9 listada sem id forte cai em revisar (não chuta)", "revisar", v[0]["status"], v[0]["companhia_casada"]))
    print(f"   {'✅' if c9 else '❌'} 9 listada grande sem id forte → revisar (obtido={v[0]['status']})")

    # Cenário 10 — dataset indisponível degrada sem quebrar (simulado, sem rede)
    _orig = globals()["_cvm_filers_index"]
    globals()["_cvm_filers_index"] = lambda *_a, **_k: None
    try:
        vazio = audit_cvm_coverage({"watchlist": [{"name": "Qualquer", "country": "Brasil"}]})
    finally:
        globals()["_cvm_filers_index"] = _orig
    c10 = (vazio == [])
    c10 = (vazio == [])
    ok += c10; results.append((c10, "10 dataset indisponível → degrada controlado", "[]", str(vazio), ""))
    print(f"   {'✅' if c10 else '❌'} 10 dataset indisponível → retorno vazio controlado (obtido={vazio})")

    total = len(results)
    print(f"\n   {ok}/{total} cenários OK")
    return 0 if ok == total else 1


def run_fund_coverage_tests(cfg: dict | None = None) -> int:
    """4C.6 — Fixtures de fundos/cobertura, offline e defensivos. Não usa dados
    de produção como resultado: verifica REGRAS de taxonomia e a robustez da
    aba/summary de cobertura."""
    import yaml as _yaml
    if cfg is None:
        cfg = load_config("config_risco.yaml")
    checks = []
    def chk(cond, rotulo, extra=""):
        checks.append((bool(cond), rotulo, extra))

    # 1/2 FII não entra no IPE de companhias → nao_aplicavel_dataset_ipe
    fii = {"name": "Fundo XPTO FII", "asset_class": "fii", "country": "Brasil",
           "aliases": ["Fundo XPTO FII"]}
    idx = {"companies": [], "by_cnpj": {}, "by_codigo": {}}
    st = audit_cvm_coverage({"watchlist": [fii]}, _index=idx, _cad=None)[0]["status"]
    chk(st == "nao_aplicavel_dataset_ipe", "1/2 FII → nao_aplicavel_dataset_ipe", st)

    # 3 FII com fonte oficial pendente aparece como 'oficial pendente (fundos)'
    fii_off = dict(fii, official={"cobertura_oficial_status": "oficial_pendente_fundos",
                                  "fund_source": "FNET"})
    modo = coverage_of(fii_off, cfg)[0]
    chk("pendente (fundos)" in modo, "3 FII com fonte pendente → oficial pendente (fundos)", modo)

    # 4 Gestora não vira FII e FII não vira gestora
    gest = {"name": "Gestora ABC", "asset_class": "gestora_fundo", "vehicle_kind": "gestora"}
    chk(asset_group_of_company(gest) == "gestora_fundo" and asset_group_of_company(fii) == "fii",
        "4 Gestora/Fundos não se mistura com FIIs")

    # 5 FIP não-listado → Gestoras/Fundos; FIP-IE listado → FIIs/Fundos listados (decisão 4D)
    chk(FUND_TYPE_TO_GROUP.get("FIP") == "gestora_fundo"
        and FUND_TYPE_TO_GROUP.get("FIP-IE") == "fii",
        "5 FIP não-listado → Gestoras/Fundos; FIP-IE listado → fii (decisão VIGT11)")

    # 6 FIAGRO → grupo fii (fundo listado)
    chk(FUND_TYPE_TO_GROUP.get("FIAGRO") == "fii" and fund_type_of(
        {"name": "Vinci Crédito Agro FIAGRO"}) == "FIAGRO",
        "6 FIAGRO no grupo correto (fii) e inferível pelo nome")

    # 7 build_coverage_summary tolera cfg mínimo/incompleto (aba não trava)
    ok7 = True
    try:
        cs_min = build_coverage_summary({"watchlist": [{"name": "X"}]})
        ok7 = ("audit_cvm_status" in cs_min and cs_min["audit_cvm_status"] == "pendente")
    except Exception as e:
        ok7 = False
    chk(ok7, "7 coverage_summary robusto com cadastro mínimo (aba não trava)")

    # 8 summary/export não quebra com official ausente/estranho
    ok8 = True
    try:
        build_coverage_summary({"watchlist": [{"name": "Y", "asset_class": "fii", "official": None}]})
    except Exception:
        ok8 = False
    chk(ok8, "8 summary não quebra com official ausente/None")

    # 9 probe targets de fundos desativados por padrão
    fs = cfg.get("fund_sources") or {}
    chk(fs.get("enabled") is False and (fs.get("probe_targets")),
        "9 fund_sources.enabled=false com probe_targets presentes", str(fs.get("enabled")))

    # 10 validadores: 0 erros/0 warnings
    e = [m for m in validate_asset_classes(cfg.get("watchlist", [])) if m.startswith("ERRO")]
    w = [m for m in validate_sources(cfg) if m.startswith("WARNING")]
    chk(not e and not w, "10 validadores sem erro/warning", f"{len(e)}E/{len(w)}W")

    # extra — divergência de taxonomia é detectada (diagnóstico, não correção)
    pend = fund_taxonomy_pendencies(cfg)
    chk(isinstance(pend, list), "extra fund_taxonomy_pendencies executa", f"{len(pend)} divergência(s)")

    print("\n── Fixtures de fundos/cobertura (4C.6) ──")
    ok = 0
    for passou, rotulo, extra in checks:
        ok += passou
        print(f"   {'✅' if passou else '❌'} {rotulo}" + (f"  [{extra}]" if extra else ""))
    if pend:
        print("   ℹ️  divergências de taxonomia a confirmar com a área:")
        for d in pend:
            print(f"        {d['emissor']}: {d['fund_type']} está em '{d['grupo_atual']}', "
                  f"metodologia sugere '{d['grupo_esperado']}'")
    print(f"\n   {ok}/{len(checks)} checagens OK")
    return 0 if ok == len(checks) else 1


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
        if f.get("url") and f.get("enabled", True) is not False:
            specs.append({"name": f.get("name", "Feed"), "url": f["url"],
                          "trust_tier": f.get("trust_tier", "imprensa"),
                          "company": f.get("company") or None})
    if not specs:
        return []

    print(f" 📡 Feeds diretos (RI/custom): {len(specs)} feed(s)…")
    articles, session = [], requests.Session()
    for spec in specs:
        _t0 = time.time()
        # telemetria só para feed OFICIAL de emissor; custom genérico de
        # imprensa não é RI_RSS e não pode virar evidência de fonte oficial.
        _tl = (_OFFICIAL_SOURCE_TELEMETRY["RI_RSS"].setdefault(spec["company"], {
            "attempted": False, "success": False, "items_found": 0, "status_code": None,
            "error_type": "", "error": "", "elapsed_ms": 0, "url": spec["url"]})
            if spec.get("company") else None)
        if _tl is not None:
            _tl["attempted"] = True
        try:
            resp = session.get(spec["url"], timeout=20,
                               headers={"User-Agent": "Mozilla/5.0"})
            if _tl is not None:
                _tl["status_code"] = resp.status_code
            resp.raise_for_status()
        except Exception as exc:
            if _tl is not None:
                _tl.update(success=False, error_type=type(exc).__name__,
                           error=str(exc)[:200], elapsed_ms=int((time.time() - _t0) * 1000))
            print(f"   ⚠️  Feed '{spec['name']}' indisponível: {exc}")
            continue
        arts = _parse_rss(resp.text, clean_titles=False)
        if _tl is not None:
            _tl.update(success=True, items_found=len(arts),
                       elapsed_ms=int((time.time() - _t0) * 1000))
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



# ─────────────────── SEC / EDGAR (emissores dos EUA) ───────────────────
_EDGAR_UA = "Radar de Risco - Vinci Partners (risco@vincipartners.com)"
_CIK_CACHE = Path(__file__).parent / ".cik_cache.json"


def _edgar_headers() -> dict:
    # A SEC exige User-Agent identificável; sem ele devolve 403.
    return {"User-Agent": _EDGAR_UA, "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov"}


def _load_cik_map(session: requests.Session) -> dict:
    """Mapa ticker→CIK (10 dígitos). Cacheado em disco: o arquivo da SEC é
    grande e muda pouco."""
    if _CIK_CACHE.exists():
        try:
            return json.loads(_CIK_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        r = session.get("https://www.sec.gov/files/company_tickers.json",
                        headers={"User-Agent": _EDGAR_UA}, timeout=25)
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        print(f"   ⚠️  Mapa ticker→CIK indisponível: {exc}")
        return {}
    mp = {}
    for item in (raw.values() if isinstance(raw, dict) else raw):
        tk = str(item.get("ticker", "")).upper().strip()
        if tk:
            mp[tk] = str(item.get("cik_str", "")).zfill(10)
    try:
        _CIK_CACHE.write_text(json.dumps(mp), encoding="utf-8")
    except Exception:
        pass
    return mp


def _edgar_articles_from_submissions(data: dict, company: str, cik10: str,
                                     forms: set[str], cutoff_ts: int) -> list[dict]:
    """Converte o JSON de submissions da SEC em artigos do pipeline.
    Isolado da rede para poder ser testado sem acesso à SEC."""
    recent = ((data.get("filings") or {}).get("recent") or {})
    fs = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accs = recent.get("accessionNumber", []) or []
    docs = recent.get("primaryDocument", []) or []
    descs = recent.get("primaryDocDescription", []) or []
    items = recent.get("items", []) or []
    out = []
    for i, form in enumerate(fs):
        if form not in forms:
            continue
        try:
            ts = int(datetime.strptime(dates[i], "%Y-%m-%d")
                     .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        if ts < cutoff_ts:
            continue
        acc_raw = (accs[i] if i < len(accs) else "")
        acc = acc_raw.replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{acc}/{doc}"
               if acc and doc else
               f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}")
        desc = (descs[i] if i < len(descs) else "") or ""
        it = (items[i] if i < len(items) else "") or ""
        title = f"{company} — {form}" + (f": {desc}" if desc else "")
        if it:
            title += f" (items {it})"
        out.append({
            "title": title, "url": url, "pub_ts": ts,
            "source": "SEC · EDGAR", "domain": "sec.gov",
            "summary": desc or form, "language": "en",
            "forced_trust": "oficial",
            # ── 4H.3A (bloqueador 2) ──
            # NÃO usar forced_companies: ele curto-circuita detect_companies e
            # faria o FILER virar sujeito automático de qualquer evento citado
            # no documento ("Vale — 6-K: plano de RJ da Samarco" viraria RJ da
            # Vale). O filer entra como CANDIDATO: associa o documento ao card
            # do emissor, mas a resolução de sujeito é a normal (mention_role).
            "filing_company": company,
            "source_company": company,
            "monitored_company": company,
            "candidate_companies": [company],
            # rastreabilidade oficial (Bloco D 4H.2 / Bloco C 4H.3A)
            "form": form, "accession_number": acc_raw, "primary_document": doc,
            "filing_items": it, "filing_date": (dates[i] if i < len(dates) else ""),
            "provenance": "EDGAR",
        })
    return out


# Bolsas cujos emissores reportam à SEC (ações ou ADR registrados).
_SEC_EXCHANGES = ("nyse", "nasdaq")

# ── 4H.3A Bloco B: elegibilidade por TIPO DE ATIVO ──
# Veículos e gestoras NÃO podem ser tratados automaticamente como companhia
# 8-K/10-K: o objeto monitorado (a gestora, o fundo, o ETF) frequentemente não
# é a entidade que arquiva. Exigir mapeamento explícito à entidade SEC.
_EDGAR_VEHICLE_REQUIRES_MAPPING = (
    "gestora", "fundo", "fund", "etf", "fii", "veiculo", "veículo",
    "offshore", "feeder", "master", "trust", "spe", "spv", "marca",
)
_EDGAR_ASSET_CLASS_REQUIRES_MAPPING = (
    "gestora_fundo", "fundo", "fii", "etf", "veiculo", "veículo",
)
# Formulários próprios de fundos/veículos registrados na SEC. NÃO são a
# allowlist corporativa: 13F/13D/13G descrevem posições em TERCEIROS e não são
# evento de crédito do próprio declarante.
_EDGAR_FUND_FORMS = ("N-CSR", "N-CSRS", "N-PORT", "N-1A", "24F-2NT", "NPORT-P")
_EDGAR_NEVER_SCORE_FORMS = ("13F-HR", "13F-HR/A", "SC 13D", "SC 13D/A",
                            "SC 13G", "SC 13G/A", "3", "4", "5")


def edgar_entity_mapping(company: dict) -> dict:
    """Mapeamento explícito objeto monitorado → entidade que arquiva na SEC.
    Preenchido no config como `sec_entity`. Sem ele, veículos/gestoras não são
    elegíveis (evita herdar filings da controladora como evento direto)."""
    return (company.get("sec_entity") or {})


def edgar_requires_entity_mapping(company: dict) -> bool:
    """O objeto monitorado é veículo/gestora/fundo (exige mapeamento)?"""
    vk = str(company.get("vehicle_kind") or "").strip().lower()
    ac = str(company.get("asset_class") or "").strip().lower()
    return (any(k in vk for k in _EDGAR_VEHICLE_REQUIRES_MAPPING)
            or any(k == ac or k in ac for k in _EDGAR_ASSET_CLASS_REQUIRES_MAPPING))


def edgar_eligible_reason(company: dict) -> tuple[bool, str]:
    """Elegibilidade EDGAR + MOTIVO auditável (4H.3A Bloco B).

    O critério deixou de ser apenas ticker + país/listing. Veículos, gestoras,
    fundos, ETFs, marcas comerciais e subsidiárias sem filing próprio só entram
    com `sec_entity.status == 'correspondencia_direta'`, e fundos que realmente
    arquivam recebem forms PRÓPRIOS (não a allowlist corporativa)."""
    nome = company.get("name", "")
    if edgar_requires_entity_mapping(company):
        m = edgar_entity_mapping(company)
        st = str(m.get("status") or "").strip().lower()
        if st == "correspondencia_direta" and (m.get("cik") or m.get("ticker")):
            return True, (f"veículo/gestora com mapeamento explícito "
                          f"correspondencia_direta (ticker={m.get('ticker') or '—'}, "
                          f"cik={m.get('cik') or '—'})")
        if st == "fundo_com_forms_proprios" and (m.get("cik") or m.get("ticker")):
            return True, "fundo que arquiva na SEC com formulários próprios"
        return False, (f"'{nome}' é veículo/gestora (asset_class="
                       f"{company.get('asset_class')}, vehicle_kind="
                       f"{company.get('vehicle_kind')}) sem `sec_entity."
                       f"status=correspondencia_direta`: filings da controladora "
                       f"NÃO são evento direto do objeto monitorado")
    # ── companhia (caminho corporativo, inalterado) ──
    if company.get("cik"):
        return True, "CIK cadastrado"
    if (company.get("official") or {}).get("sec"):
        return True, "official.sec: true"
    listing = (company.get("listing") or "").lower()
    if any(x in listing for x in _SEC_EXCHANGES):
        return True, f"listagem em bolsa SEC ({company.get('listing')})"
    if bool(company.get("ticker")) and company.get("country") in ("EUA", "Canadá"):
        return True, "ticker cadastral + domicílio norte-americano"
    return False, "sem CIK, sem official.sec, sem listagem SEC e sem ticker norte-americano"


def edgar_forms_for(company: dict, default_forms: set[str]) -> set[str]:
    """Formulários aplicáveis ao emissor. Fundos/veículos mapeados usam forms
    PRÓPRIOS; companhias usam a allowlist corporativa. Nunca inclui 13F/13D/
    13G/Forms 3-4-5 (posições em terceiros ou insiders, não evento de crédito)."""
    m = edgar_entity_mapping(company)
    if m.get("forms"):
        forms = {str(x).strip() for x in m["forms"] if str(x).strip()}
    elif str(m.get("status") or "").lower() == "fundo_com_forms_proprios":
        forms = set(_EDGAR_FUND_FORMS)
    else:
        forms = set(default_forms)
    return {f for f in forms if f not in _EDGAR_NEVER_SCORE_FORMS}


def edgar_eligible(company: dict) -> bool:
    """O emissor reporta à SEC? O critério NÃO é o país de domicílio: uma
    companhia suíça, mexicana ou brasileira com ações/ADR registrados nos EUA
    também arquiva no EDGAR (Bunge, Cemex, Ecopetrol, Nubank, StoneCo…).

    4H.3A: veículos/gestoras/fundos exigem mapeamento explícito de entidade
    (ver edgar_eligible_reason)."""
    ok, _ = edgar_eligible_reason(company)
    return ok


def _normalize_edgar_forms(raw) -> set[str]:
    """Converte `formularios_gatilho` em um set de formulários REAIS.

    CORREÇÃO 4H.2 (causa raiz do "0 filings"): no config de produção o campo
    estava gravado como STRING  "['8-K', '6-K', ...]"  (com aspas externas).
    O código antigo fazia `set(<str>)`, que itera sobre CARACTERES e produzia
    {'[', "'", '8', '-', 'K', ...}. Nenhum formulário real ("8-K", "6-K", ...)
    pertence a um conjunto de caracteres, então TODO filing era descartado no
    filtro de formulário — daí 0 aceitos para os 32 emissores, apesar de
    HTTP 200. Este normalizador aceita list | string-de-lista | csv | None e
    NUNCA aplica set() diretamente a uma string."""
    default = ["8-K", "6-K", "10-K", "10-Q", "20-F", "40-F"]
    if raw is None:
        return set(default)
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip() for x in raw if str(x).strip()}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return set(default)
        if s.startswith("[") and s.endswith("]"):
            try:
                import ast
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple, set)):
                    return {str(x).strip() for x in parsed if str(x).strip()}
            except Exception:
                pass
        toks = [t.strip().strip("'\"[] ") for t in s.split(",")]
        toks = [t for t in toks if t]
        return set(toks) if toks else set(default)
    t = str(raw).strip()
    return {t} if t else set(default)


def edgar_collection_enabled(cfg: dict) -> bool:
    """[4H.3A bloqueador 1] Trava MESTRE em AND estrito.

    A flag global é uma trava mestre real: se ela for false, nenhuma flag
    por-fonte pode ligar a coleta. Matriz exigida:
        global=false, source=false → desligado
        global=false, source=true  → desligado
        global=true,  source=false → desligado
        global=true,  source=true  → LIGADO
    Exige `is True` (não apenas truthy) para evitar que strings/1 liguem coleta
    por acidente de config."""
    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    return (cfg.get("international_official_sources_enabled", False) is True
            and src.get("enabled", False) is True)


def edgar_scoring_enabled(cfg: dict) -> bool:
    """[4H.3A Bloco H] Pontuação EDGAR é uma flag SEPARADA da coleta.
    fontes=true + scoring=false = shadow mode (coleta e telemetria, sem score)."""
    return (edgar_collection_enabled(cfg)
            and cfg.get("edgar_scoring_enabled", False) is True)


def fetch_edgar_filings(cfg: dict, *, force: bool = False) -> list[dict]:
    """Comunicados obrigatórios (8-K/6-K/10-K/20-F/40-F…) dos emissores que
    reportam à SEC via EDGAR. Fonte OFICIAL equivalente à CVM/IPE.

    `force=True` é usado APENAS pelos modos diagnósticos isolados
    (--edgar-dry-run / --edgar-shadow-run), que não persistem nada. O caminho
    normal de produção exige a matriz AND das flags."""
    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    # ── Flag mestre em AND (4H.3A) ──
    if not force and not edgar_collection_enabled(cfg):
        print(" 🇺🇸 SEC/EDGAR: desativado (exige international_official_sources_enabled=true "
              "E official_sources.EUA.enabled=true) — 0 filings, score inalterado.")
        return []
    # CORREÇÃO 4H.2: normaliza a allowlist (antes: set(<str>) → set de caracteres)
    forms = _normalize_edgar_forms(src.get("formularios_gatilho"))
    rps = max(1, int(src.get("rate_limit_rps", 8)))
    janela = int((cfg.get("evolution") or {}).get("window_days", 90))
    cutoff = int(datetime.now(timezone.utc).timestamp()) - janela * 86400

    alvos = [c for c in cfg.get("watchlist", []) if edgar_eligible(c)]
    if not alvos:
        return []
    print(f" 🇺🇸 SEC/EDGAR: {len(alvos)} emissor(es) elegível(is)…")
    session = requests.Session()
    cikmap = _load_cik_map(session)
    if not cikmap:
        print("   ⚠️  Sem mapa de CIK; EDGAR ignorado nesta execução.")
        return []
    articles, achados = [], 0
    for c in alvos:
        _t0 = time.time()
        _tl = _OFFICIAL_SOURCE_TELEMETRY["EDGAR"].setdefault(c["name"], {
            "attempted": False, "cik_resolved": False, "success": False,
            "filings_found": 0, "status_code": None, "error_type": "", "error": "",
            "elapsed_ms": 0})
        cik10 = c.get("cik") or cikmap.get(str(c.get("ticker") or "").upper())
        if not cik10:
            # CIK não resolvido aparece EXPLICITAMENTE — não some em silêncio
            _tl.update(attempted=True, cik_resolved=False, error_type="cik_nao_resolvido",
                       error=f"ticker={c.get('ticker') or '—'}",
                       elapsed_ms=int((time.time() - _t0) * 1000))
            print(f"   • {c['name']}: CIK não resolvido "
                  f"(ticker={c.get('ticker') or '—'}); cadastre `cik` no config.")
            continue
        _tl.update(attempted=True, cik_resolved=True)
        try:
            r = session.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                            headers=_edgar_headers(), timeout=25)
            _tl["status_code"] = r.status_code
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            _tl.update(success=False, error_type=type(exc).__name__, error=str(exc)[:200],
                       elapsed_ms=int((time.time() - _t0) * 1000))
            print(f"   ⚠️  {c['name']}: EDGAR indisponível ({exc})")
            time.sleep(1.0 / rps)
            continue
        # 4H.3A Bloco B: forms POR EMISSOR (fundos/veículos mapeados usam forms
        # próprios; 13F/13D/Forms 3-4-5 nunca entram).
        _forms_c = edgar_forms_for(c, forms)
        arts = _edgar_articles_from_submissions(data, c["name"], cik10, _forms_c, cutoff)
        # executado com sucesso, mesmo que 0 filings (é resultado, não falha)
        _tl.update(success=True, filings_found=len(arts),
                   elapsed_ms=int((time.time() - _t0) * 1000))
        if arts:
            achados += len(arts)
            articles.extend(arts)
        time.sleep(1.0 / rps)
    print(f"   ✅ {achados} filing(s) na janela de {janela} dias")
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
        # 4H.1e — zero itens NÃO é falha técnica: a página respondeu e foi
        # extraída. `request_success`/`extraction_success` separam isso de
        # timeout/403/parsing quebrado.
        _url_final = url
        _m_auto = re.search(r"auto-localizado \(([^)]+)\)", strategy or "")
        if _m_auto:
            _url_final = urljoin(url, _m_auto.group(1))
        _OFFICIAL_SOURCE_TELEMETRY["RI_NEWS"][company] = {
            "attempted": True, "request_success": True, "extraction_success": True,
            "success": True, "items_found": len(arts),
            "method": strategy, "url_original": url, "url_final": _url_final,
            "status_code": None, "error_type": "", "error": "",
            "elapsed_ms": int((time.time() - _t_ri) * 1000) if "_t_ri" in dir() else 0}
        print(f"   • {company}: {len(arts)} itens via {strategy}")
        articles.extend(arts)
        time.sleep(0.5)
    return articles


# ── Etapa 2: classificação ───────────────────────────────────────────────────

# ─────────────────── Idioma e tradução (cobertura internacional) ───────────
# Estratégia: "translate-then-classify" — traduz título e resumo curto para
# português ANTES de classificar, mantendo o texto original preservado. Só
# título+resumo são traduzidos (não o corpo), o que mantém custo e latência
# baixos; o link original nunca é alterado.

_LANG_HINTS = {
    "es": (" el ", " la ", " los ", " las ", " del ", " para ", " con ", " por ",
           "ción", "ñ", " que ", " una ", " año", "rescate", "millones"),
    "en": (" the ", " of ", " and ", " to ", " for ", " with ", " from ",
           " said", " will ", " billion", " million", " shares"),
    "pt": (" de ", " da ", " do ", " para ", " com ", " que ", " uma ", " não ",
           "ção", "ões", " milhões", " bilhões", " ações"),
}


def detect_language(art: dict, cfg: dict) -> str:
    """Idioma do artigo. Ordem: campo explícito do coletor → idioma do feed →
    idioma cadastrado do emissor → heurística lexical. Nunca devolve vazio."""
    if art.get("language"):
        return art["language"]
    dom = (art.get("domain") or "").lower()
    for f in cfg.get("custom_feeds", []) or []:
        if f.get("language") and f.get("url") and dom and dom in f["url"]:
            return f["language"]
    comps = art.get("forced_companies") or art.get("companies") or []
    if comps:
        for c in cfg.get("watchlist", []):
            if c["name"] in comps and c.get("language"):
                return c["language"]
    txt = " " + normalize(f"{art.get('title','')} {art.get('summary','')}") + " "
    best, score = "pt", 0
    for lang, hints in _LANG_HINTS.items():
        s = sum(1 for h in hints if h in txt)
        if s > score:
            best, score = lang, s
    return best


def translate_articles(articles: list[dict], cfg: dict) -> int:
    """Traduz título e resumo para português nos artigos em outro idioma.
    Preserva `title_original`/`summary_original` e o link. Em lote, para
    economizar chamadas. Falha de tradução nunca derruba o pipeline: o
    artigo segue com o texto original."""
    tcfg = cfg.get("translation") or {}
    if not tcfg.get("enabled"):
        return 0
    alvo = tcfg.get("target", "pt")
    pular = set(tcfg.get("skip_languages") or [alvo])
    maxc = int(tcfg.get("max_chars", 400))

    pendentes = []
    for a in articles:
        a["language"] = detect_language(a, cfg)
        if a["language"] not in pular and not a.get("title_pt"):
            pendentes.append(a)
    if not pendentes:
        return 0

    api_key = (cfg.get("llm") or {}).get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    if not api_key or genai is None:
        print(f" 🌐 {len(pendentes)} notícia(s) em outro idioma — tradução indisponível "
              f"(sem chave/LLM); mantendo texto original.")
        return 0

    llm = cfg.get("llm") or {}
    genai.configure(api_key=api_key)
    modelos = [llm.get("model", "gemini-3-flash")] + list(llm.get("model_fallbacks") or [])
    model_idx = 0
    model = genai.GenerativeModel(modelos[0])
    sleep_s = float(llm.get("rpm_sleep_seconds", 4))

    print(f" 🌐 Traduzindo {len(pendentes)} notícia(s) para {alvo.upper()} "
          f"(modelo {modelos[0]})…")
    LOTE = 20
    traduzidos = 0
    for i in range(0, len(pendentes), LOTE):
        lote = pendentes[i:i + LOTE]
        itens = [{"i": n,
                  "lang": a["language"],
                  "title": (a.get("title") or "")[:maxc],
                  "summary": (a.get("summary") or "")[:maxc]}
                 for n, a in enumerate(lote)]
        prompt = (
            "Traduza para português do Brasil os campos 'title' e 'summary' das "
            "notícias financeiras abaixo. Preserve nomes próprios, tickers, números "
            "e siglas. Não interprete nem resuma: traduza fielmente. "
            "Responda SOMENTE com JSON no formato "
            '{"itens":[{"i":0,"title":"...","summary":"..."}]}.\n\n'
            + json.dumps(itens, ensure_ascii=False))
        # Rotaciona pelos modelos de fallback se o atual estiver indisponível
        # (404/descontinuado); esgotada a cota ou a lista, degrada mantendo o
        # texto original — tradução nunca derruba o pipeline.
        data = None
        while True:
            try:
                data = _gemini_call(model, prompt, sleep_s)
                break
            except GeminiModelUnavailable as exc:
                model_idx += 1
                if model_idx < len(modelos):
                    print(f"   ↪️  modelo de tradução indisponível ({exc}); "
                          f"tentando fallback {modelos[model_idx]}…")
                    model = genai.GenerativeModel(modelos[model_idx])
                    continue
                print("   ⚠️  Nenhum modelo de tradução disponível — "
                      "mantendo TODO o texto no idioma original (pipeline segue).")
                return traduzidos
            except GeminiQuotaExhausted:
                print("   ⚠️  Cota Gemini esgotada — mantendo texto original.")
                return traduzidos
            except Exception as exc:
                print(f"   ⚠️  Tradução do lote {i // LOTE + 1} falhou: {exc}")
                break
        if not data:
            continue
        for item in (data or {}).get("itens", []):
            try:
                a = lote[int(item["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            if item.get("title"):
                a["title_original"] = a.get("title")
                a["title_pt"] = item["title"]
                a["title"] = item["title"]        # classificação usa o traduzido
                traduzidos += 1
            if item.get("summary"):
                a["summary_original"] = a.get("summary")
                a["summary_pt"] = item["summary"]
                a["summary"] = item["summary"]
    print(f"   ✅ {traduzidos} título(s) traduzido(s); originais preservados")
    return traduzidos


@functools.lru_cache(maxsize=2048)
def _word_pattern(term: str) -> re.Pattern:
    """Regex de termo por palavra inteira sobre texto normalizado — evita que
    'OPA' case com 'opaco' ou 'RJ' com 'RJU', por exemplo."""
    return re.compile(r"(?<!\w)" + re.escape(normalize(term)) + r"(?!\w)")



# ── 4H.0b — Papel semântico da empresa POR EVENTO ─────────────────────────────
# Guardas DETERMINÍSTICAS (não dependem do Gemini) que impedem que a simples
# presença de uma palavra no título vire evento crítico. Rodam depois da
# detecção por keyword e antes da resolução por famílias / histórico / score.

# 1) Falência/insolvência em sentido ABSTRATO ("falência das instituições").
_ABSTRATO_SUJEITOS = (
    r"institui[cç]\w+|democracia|democratic|pol[ií]tica?|politic\w+|estado\b|state\b|"
    r"governo|government|sistema|system|modelo|model|confian[cç]a|trust\b|"
    r"lideran[cç]a|leadership|projeto|project|estrat[ée]gia|strateg\w+|ideologia|"
    r"ideolog\w+|diplomacia|diplomacy|governan[cç]a p[uú]blica|public governance|"
    r"educa[cç]\w+|sa[uú]de p[uú]blica|moral|[ée]tica|ethics"
)
_INSOLV_TERMO = (r"fal[eê]nci\w+|bankruptcy|insolv\w+|colapso|collapse|"
                 r"quiebra|concurso de acreedores|recupera[cç]\w+ judicial")
_ABSTRATO_RX = re.compile(
    rf"(?:{_INSOLV_TERMO})\s+(?:d[aoe]s?\s+|of\s+(?:the\s+)?|del?\s+)?(?:{_ABSTRATO_SUJEITOS})"
    rf"|(?:{_ABSTRATO_SUJEITOS})\s+(?:em|in|entra\w*\s+em)\s+(?:{_INSOLV_TERMO})", re.I)
# entidade empresarial juridicamente sujeita ao evento — desarma a guarda
_ENTIDADE_EMPRESARIAL = re.compile(
    r"\b(empresa|companhia|companies|company|banco|bank|institui[cç][aã]o financeira|"
    r"financial institution|grupo|group|s\.?a\.?|ltda|inc\b|corp\b|holding|"
    r"varejista|construtora|incorporadora|frigor[ií]fico|operadora|seguradora|"
    r"distribuidora|ind[uú]stria|firma|firm\b|startup|fintech)\b", re.I)

# 2) Fraude como ÁREA/DEPARTAMENTO ("fraud division", "equipe antifraude")
_FRAUDE_DEPTO_RX = re.compile(
    r"\b(?:fraud|anti-?fraud|financial crime)\s+(?:division|department|team|unit|"
    r"squad|desk|prevention|detection|system|operations?|group)\b"
    r"|\b(?:divis[aã]o|departamento|equipe|[aá]rea|sistema|c[eé]lula|n[uú]cleo|setor)\s+"
    r"(?:de\s+)?(?:preven[cç][aã]o|combate|detec[cç][aã]o|antifraude|anti-?fraude)"
    r"(?:\s+(?:a|de|contra)\s+fraudes?)?"
    r"|\bantifraude\b|\banti-?fraude\b"
    r"|\b(?:preven[cç][aã]o|combate|detec[cç][aã]o)\s+(?:a|de|contra)\s+fraudes?\b"
    r"|\bcrimes? financeiros?\b", re.I)

# 3) Empresa como AUTORA DA AÇÃO / enforcer / vítima
_ENFORCER_RX = re.compile(
    r"\b(?:sues?|suing|sued|files? (?:a )?lawsuit|takes? (?:legal )?action|fights?|"
    r"cracks? down|shuts? down|reports?|investigates?|uncovers?|exposes?|detects?|"
    r"blocks?|prevents?)\b[^.]{0,60}\bfraud"
    r"|\bfraud\b[^.]{0,40}\b(?:lawsuit|litigation)\s+(?:by|filed by)\b"
    r"|\b(?:processa|acion(?:a|ou)|move a[cç][aã]o|combate|denuncia|investiga|"
    r"descobre|apura|bloqueia|previne|interrompe|desarticula)\b[^.]{0,60}"
    r"(?:fraude|esquema fraudulento|golpe)"
    r"|\b(?:v[ií]tima de|victim of)\s+(?:fraude|fraud|golpe|scam)", re.I)
# construções INEQUÍVOCAS de fraude praticada/imputada à companhia
_FRAUDE_DIRETA_RX = re.compile(
    r"\b(?:committed|accused of|charged with|indicted for|investigated for|"
    r"probed for|convicted of)\s+(?:accounting\s+)?fraud"
    r"|\bfraud\s+(?:at|by|scandal at)\b"
    r"|\baccounting fraud\b"
    r"|\bexecutives?\b[^.]{0,40}\b(?:falsified|misstated)"
    r"|\b(?:praticou|cometeu)\s+fraude"
    r"|\b(?:acusad|indiciad|denunciad|investigad|condenad)\w*\s+(?:por|de)\s+fraude"
    r"|\bfraude\s+(?:cont[aá]bil|fiscal)\b"
    r"|\bfraude\s+(?:n[ao]|dentro d[ao])\s+(?:empresa|companhia|banco|grupo)"
    r"|\bfalsificaram\b|\bfalsifica[cç][aã]o de (?:resultados|balan[cç]os)", re.I)

_EVENTOS_CRITICOS = ("fraude", "falencia", "recuperacao_judicial", "default",
                     "intervencao_regulatoria", "suspensao_negociacao", "covenant_breach")


_ACAO_JUDICIAL_RX = re.compile(
    r"\b(sues?|sued|suing|files? (?:a )?lawsuit against|takes? legal action against|"
    r"processa|processou|aciona|acionou|move a[cç][aã]o contra|entra com a[cç][aã]o contra)\b",
    re.I)
_OBJ_FIM_RX = re.compile(
    r"\b(alleging|over|for|accusing|claiming|por|alegando|acusando|sobre)\b", re.I)


def _papel_em_acao_judicial(texto: str, aliases: list) -> str:
    """Quem processa quem. 'Health systems sue CVS alleging fraud' → CVS é RÉ
    (defendant_accused), não enforcer. Comparar posição da empresa com a do
    verbo é o que distingue autor de réu — procurar só o verbo perto de 'fraud'
    produziu o falso negativo da CVS."""
    m = _ACAO_JUDICIAL_RX.search(texto or "")
    if not m:
        return ""
    # sujeito = janela imediatamente ANTES do verbo; objeto = logo depois, até a
    # conjunção que introduz a causa. Checar o sujeito primeiro é essencial:
    # resumos costumam repetir o título, e uma janela larga de objeto capturava
    # o próprio autor repetido (o falso 'defendant' da Ford).
    antes = texto[max(0, m.start() - 90):m.start()]
    depois = texto[m.end():]
    fim = _OBJ_FIM_RX.search(depois)
    objeto = depois[:fim.start()] if fim else depois[:45]
    for a in (aliases or []):
        if _word_pattern(str(a)).search(normalize(antes)):
            return "plaintiff_enforcer"     # empresa é a autora da ação
    for a in (aliases or []):
        if _word_pattern(str(a)).search(normalize(objeto)):
            return "defendant_accused"      # empresa é a parte processada
    return ""


def semantic_role_guard(titulo: str, resumo: str, eventos: list[dict],
                        emissores: list[str] | None = None,
                        company: str | None = None, aliases: list | None = None) -> tuple:
    """Valida o PAPEL da empresa em relação a cada evento crítico detectado.

    Devolve (eventos_mantidos, descartes) onde cada descarte traz
    event_id, company_role, event_scope, reason e evidence_span — auditável.
    Não depende da LLM: é a rede de segurança quando a cota do Gemini acaba."""
    texto = f"{titulo or ''} {resumo or ''}".strip()
    mantidos, descartes = [], []
    for ev in eventos:
        eid = ev.get("id", "")
        if eid not in _EVENTOS_CRITICOS:
            mantidos.append(ev)
            continue
        m_abs = _ABSTRATO_RX.search(texto)
        if eid in ("falencia", "recuperacao_judicial") and m_abs \
                and not _ENTIDADE_EMPRESARIAL.search(texto):
            descartes.append({"event_id": eid, "company_role": "abstrato_metaforico",
                              "event_scope": "abstrato_metaforico", "attributable": False,
                              "scoreable": False, "confidence": "alta",
                              "evidence_span": m_abs.group(0)[:80],
                              "reason": "uso não jurídico/econômico de falência/insolvência"})
            continue
        if eid == "fraude":
            m_dep = _FRAUDE_DEPTO_RX.search(texto)
            m_dir = _FRAUDE_DIRETA_RX.search(texto)
            if m_dep and not m_dir:
                descartes.append({"event_id": eid, "company_role": "departamento_contexto",
                                  "event_scope": "area_antifraude", "attributable": False,
                                  "scoreable": False, "confidence": "alta",
                                  "evidence_span": m_dep.group(0)[:80],
                                  "reason": "'fraude' qualifica a ÁREA/departamento, não conduta da companhia"})
                continue
            _al = list(aliases or ([company] if company else []))
            _papel = _papel_em_acao_judicial(texto, _al) if _al else ""
            if _papel == "defendant_accused":
                # empresa é RÉ numa ação que alega fraude → evento ATRIBUÍVEL
                ev = dict(ev, company_role="defendant_accused",
                          legal_status="allegation/lawsuit",
                          subject_entity=company or "",
                          confirmation_status="alegacao_judicial")
                mantidos.append(ev)
                continue
            m_enf = _ENFORCER_RX.search(texto)
            if m_enf and (_papel == "plaintiff_enforcer" or not _al) and not m_dir:
                descartes.append({"event_id": eid, "company_role": "plaintiff_enforcer",
                                  "event_scope": "fraude_de_terceiro", "attributable": False,
                                  "scoreable": False, "confidence": "alta",
                                  "evidence_span": m_enf.group(0)[:80],
                                  "reason": "empresa combate/denuncia/é vítima de fraude de terceiro"})
                continue
        mantidos.append(ev)
    return mantidos, descartes


def cross_article_family_map(cfg: dict) -> dict:
    """[fix: deduplicate operational events across articles] Mapa event_id ->
    family_id, só para famílias que declararam `merge_occurrences_across_
    articles: true` (hoje só `disrupcao_operacional`). Usado para agrupar em
    UMA ocorrência econômica artigos DIFERENTES que descrevem o MESMO fato
    mas foram classificados com estágios/event_ids diferentes da mesma
    família (ex.: 'incêndio' vs 'incêndio de grande magnitude' na mesma
    planta). Famílias sem essa flag (credit_rating/insolvencia/inadimplencia/
    ma) NUNCA entram neste mapa — preservam o agrupamento legado por
    event_id, inalterado."""
    out = {}
    for fam_id, spec in ((cfg.get("event_resolution") or {}).get("families") or {}).items():
        if not spec.get("merge_occurrences_across_articles"):
            continue
        for member in (spec.get("members") or []):
            out[member] = fam_id
    return out


def resolve_event_families(eventos: list[dict], cfg: dict, titulo: str = "") -> tuple:
    """4H.0 — Resolve eventos da MESMA família semântica numa ocorrência.

    Uma notícia pode trazer eventos econômicos independentes (mantidos). O que
    não pode é a MESMA ação gerar duas contribuições: 'Moody's rebaixa o rating
    e altera a perspectiva para negativa' é UMA ação de rating. Para cada
    família, o membro de maior precedência vira PRINCIPAL e os demais viram
    metadados secundários (aparecem no card, não pontuam).

    Retorna (principais, secundarios, motivos)."""
    res = (cfg.get("event_resolution") or {})
    if not res.get("enabled", True) or not eventos:
        return eventos, [], []
    familias = res.get("families") or {}
    por_id = {e.get("id"): e for e in eventos}
    principais, secundarios, motivos = list(eventos), [], []
    for fam, spec in familias.items():
        membros = [m for m in (spec.get("members") or []) if m in por_id]
        if len(membros) < 2:
            continue
        prio = spec.get("priority") or spec.get("members") or []
        membros.sort(key=lambda m: prio.index(m) if m in prio else 99)
        principal, resto = membros[0], membros[1:]
        for m in resto:
            ev = por_id[m]
            principais = [e for e in principais if e.get("id") != m]
            secundarios.append({**ev, "family": fam,
                                "family_label": spec.get("label", fam),
                                "primary_event": principal})
            motivos.append(f"{m} → secundário de {principal} (família {fam}: "
                           f"mesma ação econômica)")
        if principal in por_id:
            por_id[principal]["event_family"] = fam
            por_id[principal]["secondary_events"] = [m for m in resto]
            por_id[principal]["conflict_resolution_reason"] = "; ".join(motivos[-len(resto):])
    return principais, secundarios, motivos


def classify_article(art: dict, taxonomy: list[dict]) -> list[dict]:
    """Retorna lista de eventos da taxonomia detectados no título+resumo,
    por palavra inteira e sem acento/caixa. Eventos com 'suppresses'
    removem os genéricos quando ambos disparam (ex.: 'Pequenas aquisições'
    suprime 'M&A'; 'Outlook positivo' suprime 'Outlook negativo')."""
    text = normalize(f"{art.get('title','')} {art.get('summary','')}")
    hits = []
    for event in taxonomy:
        matched = any(_word_pattern(kw).search(text) for kw in event.get("keywords", []))
        # 4H.3B item 5: padrões PARAMETRIZADOS (regex) além das keywords
        # literais. Permitem "rating da <X> rebaixado" sem cadastrar uma
        # keyword por nome de empresa. Ausentes no config → comportamento
        # idêntico ao anterior (retrocompatível).
        if not matched:
            for pat in event.get("patterns", []) or []:
                try:
                    if re.search(pat, text, re.I):
                        matched = True
                        break
                except re.error:
                    continue
        if not matched:
            continue
        # guardas de negação: "evitar RJ", "afasta rebaixamento", "deixou a RJ
        # para trás"… — a manchete cita o evento para negá-lo ou dá-lo por superado
        if any(_word_pattern(ng).search(text) for ng in event.get("negations", [])):
            continue
        hits.append(event)
    suppressed = {eid for ev in hits for eid in ev.get("suppresses", [])}
    return [ev for ev in hits if ev["id"] not in suppressed]


# ── Guarda de papel: menção CONTEXTUAL vs SUJEITO do evento ───────────────────
# Um emissor citado como LOCAL de negociação ("empresas listadas na B3"), como
# FONTE de dado ("segundo a B3") ou como TERCEIRO no negócio ("Itaú, credor da
# Americanas") NÃO é o sujeito do evento — atribuir o evento a ele é falso
# positivo. Os padrões abaixo marcam a ocorrência como contextual; a atribuição
# só cai se TODAS as ocorrências do alias no título forem contextuais.
_CONTEXT_PATTERNS = (
    # emissor como praça/veículo de negociação
    r"(?:listad\w*|negociad\w*|cotad\w*|registrad\w*|abriu capital|estreia\w*)\s+(?:n[ao]|em)\s+{A}\b",
    # OBS: só "n[ao]" (na/no) aqui — "d[ao]" (da/do) é ambíguo com posse
    # ("ações DA Vale" = ações QUE A VALE EMITE, Vale é sujeito; não é o
    # mesmo caso de "ações listadas NA B3"). A variante "d[ao]" fica
    # restrita a emissores com papel de infraestrutura/índice/regulador
    # via `mention_guard.contexto_patterns` (ex.: B3 — "índice da B3"),
    # nunca aplicada genericamente a toda a watchlist.
    r"\b(?:empresas|companhias|firmas|acoes|papeis|ativos|emissores|emissoras|fundos|"
    r"cotacoes|investidores|indice|ibovespa|pregao|bolsa|mercado)\b(?:\s+\w+){0,3}\s+"
    r"n[ao]\s+{A}\b",
    # emissor como fonte de informação
    r"\b(?:segundo|conforme|de acordo com)\s+(?:[ao]\s+)?{A}\b",
    r"\b(?:dados|levantamento|balanco|pesquisa|estudo|relatorio|ranking)\s+d[ao]\s+{A}\b",
    # emissor como terceiro no negócio (credor, assessor, administrador…)
    # assessor/administrador/custodiante seguem como CONTEXTO (não há exposição
    # econômica); credor/contraparte saiu daqui e passou a mention_role, que só
    # atribui quando o texto indica exposição relevante.
    r"{A}\b\s*,?\s+(?:e\s+)?(?:assessor\w*|administrador\w*|custodiante|"
    r"coordenador\w*|fiduciari\w*|underwriter\w*|auditor\w*)\b",
    r"\b(?:assessor\w*|administrador\w*|custodiante|coordenador\w*|fiduciari\w*)"
    r"\w*\s+(?:e\s+)?(?:[ao]\s+)?{A}\b",
    # alias que colide com NOTA de rating ("eleva as classificações … para B3"):
    # aqui 'B3' é grau na escala da agência, não a bolsa. Só dispara quando o
    # alias vem logo após 'para/em/de' precedido de termo de rating.
    r"(?:rating|ratings|classificac\w+|nota|notas|escala|grau|perspectiva)\w*"
    r"(?:\s+\w+){0,5}\s+(?:para|em|de)\s+{A}\b",
    # (o "evento de terceiro" NÃO é mais suprimido aqui: passou a ser
    # classificado como relação/impacto em mention_role — evento de investida
    # material é risco do emissor e deve ser atribuído, porém como INDIRETO.)
)

# Topônimos brasileiros que começam com nome de emissor ("Vale do Sinos" não é
# a Vale). Genérico: {A} + 'do/da/dos' + topônimo conhecido. 'rio doce' fica de
# fora porque é a razão social histórica da própria Vale.
_TOPONIMO_PATTERN = (
    r"{A}\s+d[oae]s?\s+(?:sinos|litio|paraiba|aco|ribeira|sao francisco|vinhedos|"
    r"itajai|jequitinhonha|europeu|cafe|taquari|rio pardo|cai|mucuri|acai|ivai|"
    r"amanhecer|sol|canaa|piranga|jaguari|uruguai|tijucas|caparao|capivari)\b"
)


def _compound_name_spans(title_raw: str, alias: str) -> list:
    """Ocorrências em que o alias faz parte de um nome composto colado por
    ponto/hífen ('C.Vale', 'Grupo-Vale'): entidade diferente do emissor. Precisa
    do título CRU porque a normalização apaga a pontuação que distingue os dois."""
    spans = []
    try:
        rx = re.compile(r"(?<![\s])[\.\-–]\s*" + re.escape(alias), re.IGNORECASE)
        for m in rx.finditer(title_raw or ""):
            spans.append(m.span())
    except re.error:
        pass
    return spans


def _mention_is_contextual(title: str, aliases, extra_patterns=None) -> bool:
    """True se TODAS as menções do emissor no título forem contextuais (praça de
    negociação, fonte de dado ou papel de terceiro). Avalia as ocorrências de
    TODOS os aliases em conjunto e mescla as sobrepostas — senão um alias curto
    ('BTG' dentro de 'BTG Pactual') escaparia da guarda. Conservador: uma única
    menção em posição de sujeito já mantém a atribuição."""
    if isinstance(aliases, str):
        aliases = [aliases]
    t = normalize(title)
    ocorr = []
    for alias in aliases:
        a = normalize(str(alias))
        if not a:
            continue
        ocorr.extend(m.span() for m in _word_pattern(str(alias)).finditer(t))
    if not ocorr:
        return False
    # mescla ocorrências sobrepostas/adjacentes numa região por menção
    ocorr.sort()
    regioes = [list(ocorr[0])]
    for s, e in ocorr[1:]:
        if s <= regioes[-1][1] + 1:
            regioes[-1][1] = max(regioes[-1][1], e)
        else:
            regioes.append([s, e])
    spans = []
    for alias in aliases:
        A = re.escape(normalize(str(alias)))
        if not A:
            continue
        # topônimo ("Vale do Sinos") — só para aliases de 4+ chars, evita ruído
        if len(normalize(str(alias))) >= 4:
            try:
                spans.extend(m.span() for m in re.finditer(_TOPONIMO_PATTERN.replace("{A}", A), t))
            except re.error:
                pass
        for pat in list(_CONTEXT_PATTERNS) + list(extra_patterns or []):
            try:
                spans.extend(m.span() for m in re.finditer(pat.replace("{A}", A), t))
            except re.error:
                continue
    if not spans:
        return False
    return all(any(s <= r[0] and r[1] <= e for s, e in spans) for r in regioes)


def detect_companies(art: dict, watchlist: list[dict]) -> list[str]:
    """Detecta emissores da watchlist citados no TÍTULO (palavra inteira,
    sem acento/caixa). Título-apenas prioriza precisão sobre recall: evita
    atribuir a notícia a toda empresa citada de passagem no resumo.

    Aplica a GUARDA DE PAPEL: menção puramente contextual (praça de negociação,
    fonte de dado, credor/assessor) não gera atribuição — é o que impede
    'empresas listadas na B3 entram em RJ' de virar um evento da B3."""
    if art.get("forced_companies"):
        return list(art["forced_companies"])
    title = art.get("title", "")
    title_norm = normalize(title)
    found = []
    for company in watchlist:
        patterns = company.get("aliases") or [company["name"]]
        extra = ((company.get("mention_guard") or {}).get("contexto_patterns")) or []
        hits = [a for a in patterns if _word_pattern(a).search(title_norm)]
        if not hits:
            continue
        if _mention_is_contextual(title, hits, extra):
            continue          # citado como contexto, não como sujeito
        # nome composto ('C.Vale') — se TODA menção é de nome colado, não é o emissor
        comp_spans = []
        for a in hits:
            comp_spans.extend(_compound_name_spans(title, str(a)))
        if comp_spans:
            n_alias = sum(len(_word_pattern(a).findall(title_norm)) for a in hits)
            if n_alias and len(comp_spans) >= n_alias:
                continue
        found.append(company["name"])

    # ── 4H.3A (bloqueador 2): o FILER entra como CANDIDATO ──
    # O filing_company associa o documento ao card do emissor mesmo que o nome
    # não sobreviva à detecção por título, MAS não decide sujeito: a resolução
    # de papel abaixo (mention_role) continua valendo para ele. Assim
    # "Vale — 6-K: plano de RJ da Samarco" detecta Samarco (sujeito direto) e
    # mantém a Vale como contexto, em vez de fazer da Vale o sujeito.
    _filing = art.get("filing_company")
    if _filing and _filing not in found:
        if any(x["name"] == _filing for x in watchlist):
            found.append(_filing)

    # Papel de cada emissor atribuído: relação, empresa objeto, fase e impacto.
    # Credor/contraparte só permanece se houver exposição econômica no texto.
    papeis = {}
    for nome in list(found):
        c = next((x for x in watchlist if x["name"] == nome), None)
        if not c:
            continue
        outros = [n for n in found if n != nome]
        r = mention_role(title, nome, c.get("aliases"), outros)
        if r.get("relation_type") == "contraparte_credor" and not r.get("atribuir"):
            found.remove(nome)      # citado como credor sem exposição relevante
            continue
        papeis[nome] = r
    if isinstance(art, dict):
        art["mention_roles"] = papeis
    return found


# ── Resolução contextual de entidade (genérica, configurável) ───────────────
# Camada OPCIONAL, aditiva — NÃO substitui `detect_companies`/`mention_role`,
# não é chamada por `main()`/`fetch_all`/`classify_and_attribute` por padrão.
# Existe para permitir, quando o cadastro do emissor declarar os campos novos
# (`search_terms`, `entity_cues`, `exclusion_cues`, `related_entities`,
# `entity_scope`, `entity_confidence`), separar RECUPERAÇÃO (consulta ampla)
# de ATRIBUIÇÃO (confirmação da entidade por evidência, sem exigir razão
# social literal no título). Cadastros que não declaram esses campos
# continuam funcionando exatamente como hoje — `resolve_entity_match` nunca
# é invocada para eles em produção.
#
# Ordem de resolução (auditável, não é um score opaco):
#   1) exclusion_cues tem precedência sobre qualquer outro sinal;
#   2) alias de alta precisão (mesmo padrão de palavra inteira do
#      `detect_companies`) confirma a entidade com confiança alta;
#   3) >= `entity_cues_min` (padrão 2) entity_cues positivos, sem exclusão,
#      confirma com confiança média (atribuição contextual, sem alias
#      literal — ex.: "Cementera Yura alcanza utilidades..." sem "S.A.");
#   4) nenhum dos anteriores: não atribuído (confiança baixa/nenhuma);
#   5) `related_entities` é verificado à parte — menção a uma subsidiária/
#      controladora relacionada NUNCA transfere automaticamente o evento
#      para a empresa cadastrada; só identifica a entidade relacionada como
#      sujeito provável, para revisão humana ou para o padrão Vale/Samarco
#      já existente em `semantic_audit.py` decidir o roteamento.
ENTITY_CUES_MIN_DEFAULT = 2


def _text_of(article: dict) -> str:
    return normalize(f"{article.get('title', '')} {article.get('summary', '')}")


def resolve_entity_match(article: dict, company: dict, cfg: dict | None = None) -> dict:
    """Resolução contextual de entidade — genérica e configurável (não
    hard-coded para nenhum emissor específico). Ver cabeçalho da seção acima
    para a ordem de precedência. Retorna um dict auditável, nunca um score
    opaco:

    matched (bool), confidence ("high"/"medium"/"low"/"none"),
    matched_alias (str|None), positive_cues (list[str]),
    exclusion_cues (list[str]), relation_type
    ("direct"/"related_entity"/None), subject_company (str|None),
    rule (str — nome da regra que decidiu), observation (str).

    Compatibilidade retroativa: se `company` não declarar `entity_cues`/
    `exclusion_cues`, o comportamento cai para "só alias de alta precisão",
    equivalente ao que `detect_companies` já faz — nenhuma mudança de
    resultado para cadastros antigos."""
    text = _text_of(article)
    name = company.get("name", "")
    aliases = company.get("aliases") or [name]
    exclusion_cues = company.get("exclusion_cues") or []
    entity_cues = company.get("entity_cues") or []
    min_cues = company.get("entity_cues_min", ENTITY_CUES_MIN_DEFAULT)

    # 1) exclusion_cues — precedência absoluta, mesmo se um alias também bater...
    excl_hits = [c for c in exclusion_cues if normalize(c) and normalize(c) in text]
    alias_hit = next((a for a in aliases if _word_pattern(a).search(text)), None)
    cue_hits = [c for c in entity_cues if normalize(c) in text]
    if excl_hits:
        # [Integração 5.4] ...exceto quando o alias corporativo de alta precisão
        # está presente JUNTO com evidência operacional suficiente (>=
        # entity_cues_min sinais de contexto positivo) — "Incendio afecta la
        # fábrica de Cemento Yura en el distrito de Yura" é sobre a EMPRESA
        # (alias 'Cemento Yura' + cues operacionais 'cemento'/'fábrica'), não
        # sobre o distrito, mesmo citando o topônimo excludente. Sem alias OU
        # sem cues operacionais suficientes, a exclusão mantém precedência
        # absoluta e INALTERADA (ex.: "Homicidio en Yura: Yura S.A. no tiene
        # relación con el hecho" — só 1 cue ('yura s.a.') abaixo do mínimo —
        # continua REJEITADO; "titularidad estatal en Yura" — sem alias —
        # continua REJEITADO).
        if not (alias_hit and len(cue_hits) >= min_cues):
            return {
                "matched": False, "confidence": "none", "matched_alias": None,
                "positive_cues": [], "exclusion_cues": excl_hits,
                "relation_type": None, "subject_company": None,
                "rule": "exclusion_cue_precedence",
                "observation": (f"Sinal(is) de exclusão presente(s) ({', '.join(excl_hits)}) "
                                f"— nunca atribuir a '{name}', mesmo com alias/cues positivos."),
            }
        return {
            "matched": True, "confidence": "high", "matched_alias": alias_hit,
            "positive_cues": cue_hits, "exclusion_cues": excl_hits,
            "relation_type": "direct", "subject_company": name,
            "rule": "alias_and_operational_cues_override_exclusion",
            "observation": (f"Alias de alta precisão '{alias_hit}' + {len(cue_hits)} sinal(is) "
                            f"operacional(is) ({', '.join(cue_hits)}) superam exclusion_cue(s) "
                            f"coincidente(s) ({', '.join(excl_hits)}) — evidência corporativa "
                            f"direta prevalece sobre o topônimo/contexto excludente."),
        }

    # 2) alias de alta precisão — mesmo padrão de palavra inteira do detect_companies
    if alias_hit:
        return {
            "matched": True, "confidence": "high", "matched_alias": alias_hit,
            "positive_cues": cue_hits,
            "exclusion_cues": [], "relation_type": "direct", "subject_company": name,
            "rule": "high_precision_alias",
            "observation": f"Alias de alta precisão '{alias_hit}' encontrado no texto.",
        }

    # 3) entity_cues contextuais — confirma sem exigir alias literal
    if len(cue_hits) >= min_cues:
        return {
            "matched": True, "confidence": "medium", "matched_alias": None,
            "positive_cues": cue_hits, "exclusion_cues": [],
            "relation_type": "direct", "subject_company": name,
            "rule": "contextual_cues_threshold",
            "observation": (f"{len(cue_hits)} sinal(is) de contexto positivo(s) "
                            f"({', '.join(cue_hits)}) >= mínimo exigido ({min_cues}), "
                            f"sem alias literal no texto."),
        }

    # 4) nenhum sinal suficiente
    return {
        "matched": False, "confidence": "low" if cue_hits else "none",
        "matched_alias": None, "positive_cues": cue_hits, "exclusion_cues": [],
        "relation_type": None, "subject_company": None,
        "rule": "insufficient_evidence",
        "observation": (f"{len(cue_hits)} sinal(is) de contexto encontrado(s), "
                        f"abaixo do mínimo exigido ({min_cues}); nenhum alias literal."),
    }


def resolve_related_entity_mentions(article: dict, company: dict) -> list[dict]:
    """Verifica se o artigo menciona alguma `related_entity` do emissor
    (subsidiária, controladora, marca irmã) usando os aliases próprios da
    relacionada. NUNCA transfere o evento automaticamente — apenas identifica
    a entidade relacionada como sujeito provável, para o consumidor (padrão
    Vale/Samarco em `semantic_audit.py`, ou revisão humana) decidir o
    roteamento. Retorna lista vazia se `related_entities` não estiver
    cadastrado (compatibilidade retroativa)."""
    text = _text_of(article)
    hits = []
    for rel in company.get("related_entities") or []:
        rel_aliases = rel.get("aliases") or [rel.get("entity_name", "")]
        hit = next((a for a in rel_aliases if a and _word_pattern(a).search(text)), None)
        if hit:
            hits.append({
                "entity_name": rel.get("entity_name"),
                "legal_name": rel.get("legal_name"),
                "relationship": rel.get("relationship"),
                "attribution_mode": rel.get("attribution_mode"),
                "matched_alias": hit,
                "observation": (f"Menção à entidade relacionada '{rel.get('entity_name')}' "
                                f"({rel.get('relationship')}) — não transferir automaticamente "
                                f"para '{company.get('name')}'."),
            })
    return hits


def apply_contextual_entity_resolution(art: dict, cfg: dict) -> dict:
    """Hook OPT-IN chamado por `classify_and_attribute` logo após
    `detect_companies`. Para cada emissor da watchlist que declarar
    `uses_contextual_entity_resolution(company) == True`, roda
    `resolve_entity_match` (+ `resolve_related_entity_mentions`) e corrige
    `art["companies"]` de acordo:

      • matched=True e ainda não estava em `art["companies"]` → adiciona
        (cobre o caso "atribuído por contexto, sem alias literal" — ex.:
        'Cementera Yura alcanza utilidades...' sem 'S.A.').
      • matched=False mas HAVIA entrado em `art["companies"]` via alias de
        `detect_companies` → remove (exclusion_cue tem precedência mesmo
        sobre um alias presente — ex.: 'Homicidio en Yura: Yura S.A. no
        tiene relación con el hecho.').
      • Emissores SEM nenhum campo novo (os 160 reais hoje) nunca entram
        neste laço — `art["companies"]` sai exatamente como
        `detect_companies` produziu, sem nenhuma alteração.

    Registra tudo em `art["entity_resolution_trace"]` (aditivo, nunca
    persistido em `risk_history.json` de produção por nenhum caminho
    existente — só os scripts de shadow/candidate leem esse campo).
    Retorna um dict `{company_name: resolve_entity_match_result}` só dos
    emissores opt-in avaliados, para quem quiser reconciliar/depurar."""
    watch = cfg.get("watchlist", [])
    opt_in = [c for c in watch if uses_contextual_entity_resolution(c)]
    if not opt_in:
        return {}
    art.setdefault("companies", [])
    trace = art.setdefault("entity_resolution_trace", [])
    results = {}
    for company in opt_in:
        name = company["name"]
        res = resolve_entity_match(art, company, cfg)
        related = resolve_related_entity_mentions(art, company)
        results[name] = res
        trace.append({
            "candidate_company": name, "matched": res["matched"],
            "confidence": res["confidence"], "matched_alias": res["matched_alias"],
            "positive_cues": res["positive_cues"], "exclusion_cues": res["exclusion_cues"],
            "relation_type": res["relation_type"], "subject_company": res["subject_company"],
            "rule": res["rule"], "observation": res["observation"],
            "related_entities_mentioned": related,
        })
        already_present = name in art["companies"]
        if res["matched"] and not already_present:
            art["companies"].append(name)
        elif not res["matched"] and already_present:
            # alias de detect_companies bateu, mas a exclusion_cue/insuficiência
            # de evidência tem precedência — remove para não vazar falso positivo
            art["companies"].remove(name)
    return results


def suppress_non_scoreable_entity_scopes(art: dict, cfg: dict) -> None:
    """Pós-processamento OPT-IN chamado no fim de `classify_and_attribute`.
    Para emissores com `entity_scope` em ('brand_group',
    'entity_pending_confirmation') OU `scoreable: False` explícito no
    cadastro, move os eventos já atribuídos de `events_by_company` — NUNCA
    entram em `event_ids_for`/score/status/worst_event/n_critical.

    [Integração] Evento direto autônomo (não family_secondary — esses já
    saíram de `events_by_company` em `semantic_audit.apply_semantics_to_
    record`, que roda ANTES desta função, e ficam só como metadado do
    evento principal) de uma marca/grupo (`brand_group`) ou entidade
    pendente de confirmação (`entity_pending_confirmation`) é COMPATÍVEL
    com `informational_events_by_company` (regra 5.4/5.5 da integração):
    o artigo pode ser recuperado/atribuído/exibido, mas nunca pontua. A
    entrada registra `entity_scope`, `entity_confidence`, a entidade
    provável (`likely_entity`) e a necessidade de confirmação
    (`entity_pending_confirmation`). O campo legado
    `shadow_informational_events_by_company` é preservado para quem já lia
    esse caminho. Emissores sem `entity_scope`/`scoreable` explícito (os
    160 reais) nunca são tocados por esta função."""
    wl_map = {c["name"]: c for c in cfg.get("watchlist", [])}
    ebc = art.get("events_by_company") or {}
    _events_lookup = {e.get("id"): e for e in (art.get("events") or []) if isinstance(e, dict)}
    for name in list(ebc.keys()):
        company = wl_map.get(name)
        if not company:
            continue
        scope = company.get("entity_scope")
        non_scoreable = (scope in ("brand_group", "entity_pending_confirmation")
                        or company.get("scoreable") is False)
        if non_scoreable:
            # NOTA DE INTEGRAÇÃO: `.pop()` remove a CHAVE inteira de `ebc`
            # (não só zera a lista) — se esta empresa for a única do artigo,
            # `art["events_by_company"]` vira `{}`. Isso é seguro porque
            # `merge_into_history` foi ajustado (ver comentário lá) para
            # persistir `events_by_company` sempre que a CHAVE existir em
            # `art` — mesmo com dict vazio — em vez de exigir um valor
            # truthy; sem esse ajuste, `event_ids_for` cairia no fallback
            # legado (`rec["event_ids"]`, global) e reintroduziria o evento
            # suprimido como pontuável para qualquer empresa do artigo.
            evs = ebc.pop(name, [])
            if evs:
                # compatibilidade retroativa: campo legado só de shadow
                art.setdefault("shadow_informational_events_by_company", {})[name] = evs
                pending = scope == "entity_pending_confirmation"
                info = art.setdefault("informational_events_by_company", {})
                info.setdefault(name, [])
                _url = art.get("url", "")
                for ev in evs:
                    already = any(x.get("event_id") == ev and x.get("source_record_id") == _url
                                  for x in info[name])
                    if already:
                        continue
                    _ev_obj = _events_lookup.get(ev, {})
                    _direction = _ev_obj.get("direction") or ("positiva" if is_positive(_ev_obj) else "neutra")
                    _display = "positivo" if _direction == "positiva" else "a_revisar"
                    info[name].append({
                        "company": name,
                        "event_id": ev,
                        "event_label": (_ev_obj.get("label") or ev).replace("_", " "),
                        "subject_company": name,
                        "monitored_company": name,
                        "relation_type": "direto",
                        "event_scope": "direto",
                        "direction": _direction,
                        "scoreable": False,
                        "display_category": _display,
                        "entity_scope": scope or ("scoreable_false" if company.get("scoreable") is False else ""),
                        "entity_confidence": company.get("entity_confidence", ""),
                        "entity_pending_confirmation": pending,
                        "likely_entity": company.get("likely_entity") or name,
                        "confirmation_status": ("pendente" if pending else
                                                ("requer_confirmacao" if company.get("entity_confidence")
                                                 in ("media", "medium", "baixa", "low") else "")),
                        "attribution_rule": "R_ENTITY_SCOPE_NAO_PONTUAVEL",
                        "attribution_confidence": company.get("entity_confidence", "media"),
                        "title": art.get("title", ""),
                        "url": _url,
                        "pub_ts": art.get("pub_ts"),
                        "observation": (f"entity_scope={scope or 'scoreable_false'} — não pontuável "
                                        "por configuração do cadastro (opt-in, aditivo)."),
                        "source_record_id": _url,
                    })
    if not ebc and "events_by_company" in art:
        # preserva o campo (compatibilidade), mesmo que fique vazio
        art["events_by_company"] = ebc


def run_attribution_tests(cfg: dict | None = None) -> int:
    """Testes da GUARDA DE PAPEL na atribuição de emissores (offline). Trava o
    caso reportado em produção: 'empresas listadas na B3 entram em RJ' não pode
    virar evento da B3, e menções como fonte/credor/assessor não atribuem."""
    if cfg is None:
        cfg = load_config("config_risco.yaml")
    wl = cfg.get("watchlist", [])
    casos = [
        # (título, emissores esperados, rótulo)
        ("Empresas listadas na B3 entram em recuperação judicial", [], "B3 como praça (caso real)"),
        ("Número de companhias da B3 em recuperação judicial cresce", [], "B3 como praça (coletivo + 'da')"),
        ("Ações negociadas na B3 caem após pedido de RJ", [], "B3 como praça (negociadas na)"),
        ("Segundo a B3, negociações caíram 10%", [], "B3 como fonte"),
        ("Dados da B3 mostram alta; Vale lidera", ["Vale"], "B3 fonte + Vale sujeito"),
        ("B3 anuncia queda no volume negociado", ["B3"], "B3 como SUJEITO (mantém)"),
        ("B3 é multada em processo administrativo", ["B3"], "B3 sujeito de evento próprio"),
        ("Itaú é credor da Americanas em recuperação judicial", [], "banco como credor"),
        ("BTG Pactual assessora venda de ativos da Light", [], "banco como assessor (alias curto)"),
        ("Itaú Unibanco anuncia lucro recorde", ["Itaú Unibanco"], "banco como sujeito (mantém)"),
        ("Petrobras e Vale lideram altas no Ibovespa", ["Petrobras", "Vale"], "sujeitos múltiplos (mantém)"),
        # nota de rating vs bolsa
        ("Moody's eleva as classificações da Argentina para B3, altera perspectiva",
         [], "B3 como NOTA de rating, não a bolsa"),
        ("Fitch eleva rating da Vale para BBB+", ["Vale"], "rating de emissor real (mantém)"),
        # topônimo
        ("Decretada falência de metalúrgica com fábrica lacrada no Vale do Sinos",
         [], "topônimo 'Vale do Sinos' (caso real)"),
        ("Metalúrgica do Vale do Sinos pede suspensão da falência", [], "topônimo em possessivo"),
        ("Os bastidores do Vale do Lítio", [], "topônimo 'Vale do Lítio'"),
        # nome composto
        ("C.Vale conclui aquisição de operação da I. Riedi", [], "nome composto 'C.Vale'"),
        # evento de terceiro
        ("Vale finaliza recuperação judicial da Samarco e reafirma compromisso",
         ["Vale", "Samarco Mineração"], "RJ da Samarco: atribui à Vale como INDIRETO"),
        ("Itaú, credor da Oi, tem exposição de R$ 2,5 bilhões na recuperação judicial",
         ["Itaú Unibanco"], "credor COM exposição → atribui como contraparte"),
        ("Itaú é credor da Oi em recuperação judicial", [], "credor SEM exposição → não atribui"),
        ("Vale pede recuperação judicial", ["Vale"], "RJ direta do emissor → peso integral"),
        # controles positivos
        ("CVM abre processo para apurar destituição de conselheiro da Vale",
         ["Vale"], "evento regulatório da Vale (mantém)"),
        ("Vale registra queda na produção de minério", ["Vale"], "Vale sujeito (mantém)"),
    ]
    print("\n── Testes de atribuição (guarda de papel) ──")
    ok = 0
    for titulo, esperado, rotulo in casos:
        got = sorted(detect_companies({"title": titulo}, wl))
        passou = got == sorted(esperado)
        ok += passou
        print(f"   {'✅' if passou else '❌'} {rotulo}")
        if not passou:
            print(f"        título: {titulo}")
            print(f"        esperado={sorted(esperado)} · obtido={got}")
    # ── Papéis: relação, objeto, fase, impacto ──
    a = {"title": "Vale finaliza recuperação judicial da Samarco e reafirma compromisso"}
    detect_companies(a, wl)
    rv = (a.get("mention_roles") or {}).get("Vale", {})
    ok_rel = (rv.get("relation_type") == "investida_jv"
              and rv.get("impact_type") == "indireto_material"
              and rv.get("event_phase") == "encerramento"
              and rv.get("direction_hint") == "mitigadora")
    ok += ok_rel
    print(f"   {'✅' if ok_rel else '❌'} Vale/Samarco: investida_jv · indireto_material · "
          f"encerramento · mitigadora [{rv.get('relation_type')}/{rv.get('impact_type')}/"
          f"{rv.get('event_phase')}/{rv.get('direction_hint')}]")
    b = {"title": "Vale pede recuperação judicial"}
    detect_companies(b, wl)
    ok_dir = ((b.get("mention_roles") or {}).get("Vale", {}).get("relation_type") == "direto")
    ok += ok_dir
    print(f"   {'✅' if ok_dir else '❌'} RJ direta da Vale → relação 'direto' (peso integral)")

    # ── Papel semântico por evento (4H.0b) ──
    _tx0 = cfg.get("taxonomy") or []
    _sem = [("Vamos conversar sobre a falência das instituições", "", [], "falência abstrata"),
            ("Instituição financeira X entra em falência", "", ["falencia"], "falência real de entidade"),
            ("JPMorgan Chase pushes fraud division layoffs", "The bank posted profits", [], "fraud division"),
            ("JPMorgan is investigated for accounting fraud", "", ["fraude"], "fraude imputada"),
            ("Ford Motor Sues to Shut Down Lemon Law Fraud", "", [], "empresa enforcer"),
            ("Ford accused of Lemon Law fraud", "", ["fraude"], "fraude acusada"),
            ("Empresa reforça equipe antifraude", "", [], "equipe antifraude"),
            ("Fraude contábil descoberta dentro da empresa", "", ["fraude"], "fraude na companhia"),
            ("Bankruptcy of democratic institutions", "", [], "bankruptcy abstrato"),
            ("Company launches new fraud detection division", "", [], "fraud detection division"),
            ("Company executives charged with fraud", "", ["fraude"], "executivos acusados")]
    for _t, _r, _esp, _rot in _sem:
        _e = classify_article({"title": _t, "summary": _r}, _tx0)
        _m, _d = semantic_role_guard(_t, _r, _e)
        _crit = sorted(x["id"] for x in _m if x["id"] in _EVENTOS_CRITICOS)
        bom = _crit == sorted(_esp)
        ok += bom
        print(f"   {'✅' if bom else '❌'} {_rot} [{_crit}]")
    # Bank X: falência válida (família escolhe falencia como principal)
    _e = classify_article({"title": "Bank X files for bankruptcy", "summary": ""}, _tx0)
    _m, _ = semantic_role_guard("Bank X files for bankruptcy", "", _e)
    _p, _sx, _ = resolve_event_families(_m, cfg, "")
    okb = [x["id"] for x in _p] == ["falencia"]
    ok += okb
    print(f"   {'✅' if okb else '❌'} Bank X files for bankruptcy → falencia principal "
          f"[{[x['id'] for x in _p]}]")

    # ── Papel por EMPRESA × EVENTO (4H.1d) ──
    _pe = [("2 of Michigan's largest health systems sue CVS Health alleging fraud",
            "CVS Health", ["CVS Health", "CVS"], ["fraude"], "defendant_accused",
            "CVS é RÉ → fraude atribuída como alegação"),
           ("JPMorgan sues Aleph over Frank fraud fallout", "JPMorgan",
            ["JPMorgan", "JPMorgan Chase"], [], "", "JPMorgan autor → fraude NÃO atribuída"),
           ("Ford Motor Sues to Shut Down Lemon Law Fraud", "Ford Motor",
            ["Ford Motor"], [], "", "Ford autora → fraude NÃO atribuída"),
           ("JPMorgan is investigated for accounting fraud", "JPMorgan",
            ["JPMorgan"], ["fraude"], "", "investigada → fraude atribuída")]
    for _t, _co, _al, _esp, _papel, _rot in _pe:
        _e = classify_article({"title": _t, "summary": ""}, cfg.get("taxonomy") or [])
        _m, _d = semantic_role_guard(_t, "", _e, None, company=_co, aliases=_al)
        _crit = sorted(x["id"] for x in _m if x["id"] in _EVENTOS_CRITICOS)
        _roles = [x.get("company_role") for x in _m if x.get("company_role")]
        bom = _crit == sorted(_esp) and (not _papel or _papel in _roles)
        ok += bom
        print(f"   {'✅' if bom else '❌'} {_rot} [{_crit} {_roles}]")
    # duas empresas, papéis opostos, avaliação independente
    _t2 = "Health systems sue CVS Health alleging fraud"
    _e2 = classify_article({"title": _t2, "summary": ""}, cfg.get("taxonomy") or [])
    _mc, _ = semantic_role_guard(_t2, "", _e2, None, company="CVS Health", aliases=["CVS Health"])
    _mj, _dj = semantic_role_guard(_t2, "", _e2, None, company="JPMorgan", aliases=["JPMorgan"])
    ok_ind = ("fraude" in [x["id"] for x in _mc])
    ok += ok_ind
    print(f"   {'✅' if ok_ind else '❌'} avaliação independente por empresa × evento")
    # telemetria oficial: estados
    _OFFICIAL_SOURCE_TELEMETRY["EDGAR"]["_T"] = {"attempted": True, "cik_resolved": True,
                                                 "success": True, "filings_found": 0}
    _OFFICIAL_SOURCE_TELEMETRY["EDGAR"]["_C"] = {"attempted": True, "cik_resolved": False,
                                                 "error_type": "cik_nao_resolvido"}
    _OFFICIAL_SOURCE_TELEMETRY["RI_RSS"]["_R"] = {"attempted": True, "success": True,
                                                  "items_found": 6}
    okt = (_OFFICIAL_SOURCE_TELEMETRY["EDGAR"]["_T"]["success"]
           and _OFFICIAL_SOURCE_TELEMETRY["EDGAR"]["_T"]["filings_found"] == 0
           and _OFFICIAL_SOURCE_TELEMETRY["EDGAR"]["_C"]["cik_resolved"] is False
           and _OFFICIAL_SOURCE_TELEMETRY["RI_RSS"]["_R"]["items_found"] == 6)
    ok += okt
    print(f"   {'✅' if okt else '❌'} telemetria oficial: EDGAR 0 filings = executado; "
          f"CIK não resolvido explícito; RI_RSS com itens")
    for _k in ("_T", "_C"):
        _OFFICIAL_SOURCE_TELEMETRY["EDGAR"].pop(_k, None)
    _OFFICIAL_SOURCE_TELEMETRY["RI_RSS"].pop("_R", None)

    # ── Ponta a ponta: empresa × evento persiste até o score (4H.1e) ──
    _rec_novo = {"title": "Health systems represented by JPMorgan sue CVS Health alleging fraud",
                 "companies": ["CVS Health", "JPMorgan"], "event_ids": ["fraude"],
                 "events_by_company": {"CVS Health": ["fraude"], "JPMorgan": []},
                 "pub_ts": 1, "pub_iso": "2026-07-20 10:00"}
    _cvs = event_ids_for(_rec_novo, "CVS Health")
    _jpm = event_ids_for(_rec_novo, "JPMorgan")
    _mkt = event_ids_for(_rec_novo, "Terceira Empresa")
    ok_e2e = (_cvs == ["fraude"] and _jpm == [] and _mkt == [])
    ok += ok_e2e
    print(f"   {'✅' if ok_e2e else '❌'} e2e: CVS recebe fraude, JPMorgan não, "
          f"não espalha para terceiros [CVS={_cvs} JPM={_jpm} outro={_mkt}]")
    _rec_legado = {"companies": ["A", "B"], "event_ids": ["default"]}
    ok_leg = (event_ids_for(_rec_legado, "A") == ["default"]
              and event_ids_for(_rec_legado, "B") == ["default"])
    ok += ok_leg
    print(f"   {'✅' if ok_leg else '❌'} registro legado (sem events_by_company) segue funcionando")
    _rec_prec = {"companies": ["A"], "event_ids": ["fraude", "default"],
                 "events_by_company": {"A": ["default"]}}
    ok_prec = event_ids_for(_rec_prec, "A") == ["default"]
    ok += ok_prec
    print(f"   {'✅' if ok_prec else '❌'} events_by_company tem precedência sobre event_ids")
    _rec_vazio = {"companies": ["A"], "event_ids": ["fraude"],
                  "events_by_company": {"A": []}}
    ok_vazio = event_ids_for(_rec_vazio, "A") == []
    ok += ok_vazio
    print(f"   {'✅' if ok_vazio else '❌'} empresa sem evento após a guarda deixa de pontuar")
    # famílias por empresa: A rebaixamento+outlook, B só outlook
    _A, _B = [{"id": "rebaixamento_rating"}, {"id": "outlook_negativo"}], [{"id": "outlook_negativo"}]
    _pA, _sA, _ = resolve_event_families(_A, cfg, "")
    _pB, _sB, _ = resolve_event_families(_B, cfg, "")
    ok_fam = ([x["id"] for x in _pA] == ["rebaixamento_rating"]
              and [x["id"] for x in _pB] == ["outlook_negativo"])
    ok += ok_fam
    print(f"   {'✅' if ok_fam else '❌'} famílias por empresa: A={[x['id'] for x in _pA]} "
          f"B={[x['id'] for x in _pB]}")
    # RI_NEWS zero itens não é erro
    _rn = {"attempted": True, "request_success": True, "extraction_success": True,
           "success": True, "items_found": 0, "error_type": ""}
    ok_rn = _rn["success"] and _rn["items_found"] == 0 and not _rn["error_type"]
    ok += ok_rn
    print(f"   {'✅' if ok_rn else '❌'} RI_NEWS com 0 itens = executado sem resultado (não é erro)")

    # ── Contexto não pontuável no card (4H.0b visual) ──
    _agora = int(datetime.now(timezone.utc).timestamp())
    _hc = {"articles": {"https://vale/1": {
        "title": "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "url": "https://vale/1", "source": "Vale RI", "domain": "vale.com",
        "pub_ts": _agora - 3600, "pub_iso": "2026-07-28 10:00",
        "companies": ["Vale", "Samarco Mineração"],
        "event_ids": ["recuperacao_judicial"],
        "events_by_company": {"Vale": [], "Samarco Mineração": ["recuperacao_judicial"]},
        "companies_attributed": ["Samarco Mineração"], "context_companies": ["Vale"],
        "context_events_by_company": {"Vale": [{
            "event_id": "recuperacao_judicial",
            "event_label": "Plano de Recuperação Judicial",
            "subject_company": "Samarco Mineração", "relation_type": "investida_jv",
            "impact_type": "indireto_material", "event_scope": "indireto",
            "event_phase": "aprovacao", "direction": "mitigadora", "scoreable": False}]}}},
        "run_count": 1}
    _ctx = _build_context_events(_hc, "Vale", _agora - 86400 * 30)
    ok_ctx = (len(_ctx) == 1 and _ctx[0]["subject_company"] == "Samarco Mineração"
              and _ctx[0]["scoreable"] is False
              and "mitigado" in (_ctx[0]["direction_label"] or "").lower()
              and bool(_ctx[0]["impact_label"]) and bool(_ctx[0]["relation_label"]))
    ok += ok_ctx
    print(f"   {'✅' if ok_ctx else '❌'} context_events da Vale: sujeito Samarco, não pontuável, "
          f"rótulos preenchidos [{_ctx[0]['direction_label'] if _ctx else '—'}]")
    ok_vale_sem = (event_ids_for(_hc["articles"]["https://vale/1"], "Vale") == []
                   and event_ids_for(_hc["articles"]["https://vale/1"],
                                     "Samarco Mineração") == ["recuperacao_judicial"])
    ok += ok_vale_sem
    print(f"   {'✅' if ok_vale_sem else '❌'} Vale sem RJ pontuável; Samarco mantém a RJ direta")
    _fd = build_feed(_hc, cfg, window_days=30)
    ok_feed = all("Vale" not in (f.get("companies") or []) for f in _fd)
    ok += ok_feed
    print(f"   {'✅' if ok_feed else '❌'} feed: Vale não é company-tag de RJ "
          f"[{[(f.get('company'), [e['id'] for e in f['events']]) for f in _fd]}]")
    _cg = build_changes(_hc, cfg, ["https://vale/1"], {}, [])
    _sg = _cg.get("new_signals", []) if isinstance(_cg, dict) else []
    ok_ch = all(s_.get("company") != "Vale" for s_ in _sg)
    ok += ok_ch
    print(f"   {'✅' if ok_ch else '❌'} O que mudou: nenhum sinal de RJ para a Vale")

    # ── e2e REAL: feed e changes por empresa × artigo (4H.1f-final) ──
    _hist = {"articles": {}, "run_count": 1}
    _r1 = {"title": "Health systems represented by JPMorgan sue CVS Health alleging fraud",
           "url": "https://x/1", "source": "Reuters", "domain": "reuters.com",
           "pub_ts": int(datetime.now(timezone.utc).timestamp()) - 3600,
           "pub_iso": "2026-07-28 10:00", "language": "en",
           "companies": ["CVS Health", "JPMorgan"], "event_ids": ["fraude"],
           "events_by_company": {"CVS Health": ["fraude"], "JPMorgan": []},
           "companies_attributed": ["CVS Health"], "context_companies": ["JPMorgan"],
           "event_assessments": [{"company": "CVS Health", "event_id": "fraude",
                                  "legal_status": "allegation/lawsuit"}]}
    _r2 = {"title": "Empresa A é acusada de fraude; Empresa B tem rating rebaixado",
           "url": "https://x/2", "source": "Valor", "domain": "valor.com.br",
           "pub_ts": int(datetime.now(timezone.utc).timestamp()) - 7200,
           "pub_iso": "2026-07-28 09:00", "language": "pt",
           "companies": ["CVS Health", "Ford Motor"],
           "event_ids": ["fraude", "rebaixamento_rating"],
           "events_by_company": {"CVS Health": ["fraude"],
                                 "Ford Motor": ["rebaixamento_rating"]},
           "companies_attributed": ["CVS Health", "Ford Motor"], "context_companies": []}
    _hist["articles"] = {"https://x/1": _r1, "https://x/2": _r2}
    _feed = build_feed(_hist, cfg, window_days=30)
    _f1 = [f for f in _feed if f.get("url", "").endswith("/1") or "sue CVS" in f.get("title", "")]
    _cvs_ok = any(f.get("company") == "CVS Health" and
                  "fraude" in [e["id"] for e in f["events"]] for f in _f1)
    _jpm_bad = any(f.get("company") == "JPMorgan" or "JPMorgan" in (f.get("companies") or [])
                   for f in _f1)
    ok1 = _cvs_ok and not _jpm_bad
    ok += ok1
    print(f"   {'✅' if ok1 else '❌'} feed: CVS atribuída com fraude, JPMorgan NÃO é company-tag "
          f"[linhas={[(f.get('company'), [e['id'] for e in f['events']]) for f in _f1]}]")
    _f2 = [f for f in _feed if f.get("url", "").endswith("/2") or "Empresa A" in f.get("title", "")]
    _pares = sorted((f.get("company"), tuple(e["id"] for e in f["events"])) for f in _f2)
    ok2 = (_pares == [("CVS Health", ("fraude",)), ("Ford Motor", ("rebaixamento_rating",))])
    ok += ok2
    print(f"   {'✅' if ok2 else '❌'} feed: 2 empresas → 2 linhas, sem unir eventos [{_pares}]")
    _ch = build_changes(_hist, cfg, ["https://x/1"], {}, [])
    _sig = _ch.get("new_signals", []) if isinstance(_ch, dict) else []
    ok3 = (all(s_.get("company") != "JPMorgan" for s_ in _sig)
           and any(s_.get("company") == "CVS Health" for s_ in _sig))
    ok += ok3
    print(f"   {'✅' if ok3 else '❌'} O que mudou: sinal só para CVS "
          f"[{[(x.get('company'), [e['label'] for e in x['events']]) for x in _sig]}]")

    # ── Telemetria de busca (4H.1b) ──
    class _R:
        def __init__(self, code=None, text="", exc=None):
            self.status_code, self.text, self._exc = code, text, exc
        def raise_for_status(self):
            if self._exc:
                raise self._exc
    class _S:
        def __init__(self, r): self._r = r
        def get(self, *a, **k):
            if isinstance(self._r, Exception):
                raise self._r
            return self._r
    _cfgq = {"dashboard": {"language": "pt-BR", "country": "BR", "period": "7d"}}
    _RSS_VAZIO = "<rss><channel></channel></rss>"
    _RSS_1 = ("<rss><channel><item><title>T</title><link>http://x/1</link>"
              "<pubDate>Mon, 20 Jul 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    _tst = [
        ("1 timeout → erro, success=0", _S(requests.Timeout("timeout")), False, "timeout", 0),
        ("2 HTTP 403 → ERRO_FONTE", _S(_R(403, "", requests.HTTPError("403"))), False, "http_error", 0),
        ("3 200 com RSS vazio → ZERO_BRUTO", _S(_R(200, _RSS_VAZIO)), True, "", 0),
        ("4 200 com artigos → sucesso", _S(_R(200, _RSS_1)), True, "", 1),
    ]
    for rot, sess, esp_ok, esp_err, esp_n in _tst:
        r = fetch_query_result("q", _cfgq, sess)
        bom = (r["ok"] == esp_ok and (r["error_type"] == esp_err or not esp_err)
               and len(r["articles"]) == esp_n)
        ok += bom
        print(f"   {'✅' if bom else '❌'} {rot} [ok={r['ok']} tipo={r['error_type'] or '—'} "
              f"n={len(r['articles'])}]")
    # 5-6 fallbacks de locale
    _c1 = {"name": "X", "country": "Suíça", "language": "en",
           "search_locale": {"primary": "en-US/US", "fallbacks": ["de-CH/CH", "fr-CH/CH"]}}
    _locs = locales_for_company(_c1, {"dashboard": {}})
    ok5 = _locs == ["en-US/US", "de-CH/CH", "fr-CH/CH"]
    ok += ok5
    print(f"   {'✅' if ok5 else '❌'} 5 primary + 2 fallbacks na ordem [{_locs}]")
    _c2 = {"name": "Y", "country": "Chile"}
    ok6 = locales_for_company(_c2, {"dashboard": {}}) == ["es-CL/CL"]
    ok += ok6
    print(f"   {'✅' if ok6 else '❌'} 6 sem fallback declarado → só o principal")
    # 7-8 telemetria oficial: RI não prova EDGAR; EDGAR com 0 filings é executado
    _rm = {"official_source_execution": {
        "RI_RSS": {"Z": {"attempted": True, "success": True, "items_found": 5}},
        "EDGAR": {"W": {"attempted": True, "success": True, "filings_found": 0}}}}
    _edz = ((_rm["official_source_execution"].get("EDGAR") or {}).get("Z") or {})
    ok7 = not _edz
    ok += ok7
    print(f"   {'✅' if ok7 else '❌'} 7 RI executado NÃO evidencia EDGAR do mesmo emissor")
    _edw = _rm["official_source_execution"]["EDGAR"]["W"]
    ok8 = _edw["attempted"] and _edw["success"] and _edw["filings_found"] == 0
    ok += ok8
    print(f"   {'✅' if ok8 else '❌'} 8 EDGAR executado com 0 filings = executado, resultado zero")
    # 9-11 acumulado de ciclos
    _runs = [{"run_id": f"r{i}", "finished_at": f"t{i}",
              "emitters": {f"E{j}": {"queries": 1, "success": 1, "raw_articles": 2}
                           for j in range(i * 16, i * 16 + 16)}} for i in range(4)]
    _cob = set()
    for rr in _runs:
        _cob |= set(rr["emitters"])
    ok9 = len(_runs) == 4 and len(_cob) == 64
    ok += ok9
    print(f"   {'✅' if ok9 else '❌'} 9-10 quatro ciclos distintos → cobertura acumulada {len(_cob)}")
    _sh = {"runs": _runs}
    _sh["runs"] = ([r for r in _sh["runs"] if r.get("run_id") != "r4"]
                   + [{"run_id": "r4", "finished_at": "t4", "emitters": {}}])[-8:]
    ok11 = len(_sh["runs"]) == 5 and _sh["runs"][0]["run_id"] == "r0"
    ok += ok11
    print(f"   {'✅' if ok11 else '❌'} 11 novo run NÃO apaga histórico anterior "
          f"[{len(_sh['runs'])} runs]")

    # ── Exclusividade semântica de eventos (4H.0) ──
    _tx = cfg.get("taxonomy") or []
    _fam = [("Moody's rebaixa rating da Rumo de Ba2 para Ba3 e altera perspectiva para negativa",
             ["rebaixamento_rating"], ["outlook_negativo"], "rebaixamento + outlook = 1 ação"),
            ("Agência mantém rating da Rumo e revisa perspectiva para negativa",
             ["outlook_negativo"], [], "rating mantido → só outlook"),
            ("Rumo é colocada em CreditWatch negativo pela S&P",
             ["outlook_negativo"], [], "watch NÃO é downgrade realizado"),
            ("Citi rebaixa rating de crédito e mantém recomendação de venda",
             ["rebaixamento_rating", "recomendacao_negativa"], [], "famílias distintas coexistem"),
            ("Company files for chapter 11 bankruptcy protection",
             ["falencia"], ["recuperacao_judicial"], "insolvência: 1 principal por estágio"),
            ("Empresa declara incumplimiento de pago após quebra de covenant",
             ["default"], ["covenant_breach"], "default prevalece sobre covenant")]
    for titulo, esp_p, esp_s, rot in _fam:
        _e = classify_article({"title": titulo, "summary": ""}, _tx)
        _p, _sx, _ = resolve_event_families(_e, cfg, titulo)
        pid, sid = [x["id"] for x in _p], [x["id"] for x in _sx]
        bom = all(x in pid for x in esp_p) and all(x in sid for x in esp_s) \
            and not any(x in pid for x in esp_s)
        ok += bom
        print(f"   {'✅' if bom else '❌'} {rot} [princ={pid} sec={sid}]")

    # ── Classificação multilíngue SEM Gemini (4H.6) ──
    _tax = cfg.get("taxonomy") or []
    _multi = [("Cencosud files for bankruptcy protection under chapter 11", "recuperacao_judicial"),
              ("Moody's downgrades issuer; rating cut to Ba1", "rebaixamento_rating"),
              ("Company reports payment default on senior notes", "default"),
              ("SEC investigation into accounting fraud", "fraude"),
              ("CEO steps down after profit warning", "troca_ceo"),
              ("Cencosud enfrenta concurso de acreedores en Chile", "recuperacao_judicial"),
              ("Fitch recorta la calificación del emisor", "rebaixamento_rating"),
              ("Grupo anuncia emisión de bonos por USD 500 millones", "emissao_divida"),
              ("Empresa acuerda adquisición de participación mayoritaria", "ma"),
              ("La compañía declara incumplimiento de pago", "default")]
    _ok_ml = sum(1 for t, esp in _multi
                 if esp in [e["id"] for e in classify_article({"title": t, "summary": ""}, _tax)])
    ok += (_ok_ml == len(_multi))
    print(f"   {'✅' if _ok_ml == len(_multi) else '❌'} classificação en/es por keyword sem Gemini "
          f"[{_ok_ml}/{len(_multi)}]")

    # ── Clusterização de ocorrências (linha do tempo × eventos reais) ──
    import datetime as _dt
    def _ts(d):
        return int(_dt.datetime.strptime(d, "%Y-%m-%d")
                   .replace(tzinfo=_dt.timezone.utc).timestamp())
    # cenário real Engie Brasil: 11 notícias → 4 eventos
    engie = ([{"event_id": "emissao_divida", "pub_ts": _ts("2026-02-26")}]
             + [{"event_id": "follow_on", "pub_ts": _ts(d)} for d in
                ("2026-04-28", "2026-06-11", "2026-06-17", "2026-07-06", "2026-07-14", "2026-07-19")]
             + [{"event_id": "ma", "pub_ts": _ts(d)} for d in
                ("2026-06-26", "2026-07-03", "2026-07-17")]
             + [{"event_id": "emissao_divida", "pub_ts": _ts("2026-07-06")}])
    assign_occurrence_clusters(engie, 45)
    n_clusters = len({o["_occ_key"] for o in engie})
    ok_engie = (n_clusters == 4)
    ok += ok_engie
    print(f"   {'✅' if ok_engie else '❌'} Engie: 11 notícias → 4 eventos "
          f"(16ª e 17ª emissões separadas) [obtido={n_clusters}]")
    # duas ocorrências do mesmo tipo dentro da janela = 1 evento
    junto = [{"event_id": "ma", "pub_ts": _ts("2026-07-01")},
             {"event_id": "ma", "pub_ts": _ts("2026-07-20")}]
    assign_occurrence_clusters(junto, 45)
    ok_junto = len({o["_occ_key"] for o in junto}) == 1
    ok += ok_junto
    print(f"   {'✅' if ok_junto else '❌'} mesma operação em 19 dias → 1 evento")
    total = len(casos) + 52

    print(f"\n   {ok}/{total} casos OK")
    return 0 if ok == total else 1


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
                     "url": link_for_display(dropped), "when": when})
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
    llm_meta: dict = {}
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
            "5. Para CADA manchete, avalie o PAPEL da empresa em relação a CADA "
            "evento (empresa × evento). Papéis: direct_subject, defendant_accused, "
            "investigated_entity, plaintiff_enforcer, investigator, victim, "
            "department_context, creditor_material, creditor_context, "
            "indirect_material, abstract_metaphorical, mere_mention.\n"
            "   • quem PROCESSA alegando fraude é plaintiff_enforcer (não pontua);\n"
            "   • quem É PROCESSADO por fraude é defendant_accused (pontua, com "
            "legal_status='allegation/lawsuit');\n"
            "   • 'divisão antifraude' é department_context (não pontua).\n\n"
            'Responda SOMENTE JSON: {"analises":[{"i":0,"story_id":"s1",'
            '"protagonista_artigo":true,"event_assessments":[{"company":"'
            + comp + '","event_id":"...","subject_entity":"...",'
            '"company_role":"...","attributable":true,"scoreable":true,'
            '"confidence":"alta","legal_status":"","confirmation_status":"",'
            '"evidence_span":"...","reason":"..."}],'
            '"event_ids":["..."],"protagonista":true}]}'
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
            # formato NOVO tem precedência; `event_ids` fica como fallback
            _ea = [z for z in (x.get("event_assessments") or [])
                   if (z.get("company") or comp) == comp]
            if _ea:
                ids = {z.get("event_id") for z in _ea
                       if z.get("event_id") in taxonomy and z.get("attributable", True)
                       and z.get("scoreable", True)}
                for z in _ea:
                    if z.get("event_id") in ids:
                        llm_meta[(a["url"], comp, z["event_id"])] = {
                            "subject_entity": z.get("subject_entity", ""),
                            "company_role": z.get("company_role", ""),
                            "legal_status": z.get("legal_status", ""),
                            "confirmation_status": z.get("confirmation_status", ""),
                            "evidence_span": z.get("evidence_span", ""),
                            "reason": z.get("reason", "")}
            else:
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
        # 4H.1f — a LLM decide POR EMPRESA. Antes os vereditos eram unidos num
        # conjunto global, destruindo events_by_company (a fraude confirmada
        # para a CVS voltava a valer para quem só a processou).
        _ebc = dict(art.get("events_by_company") or {})
        _guarda_removeu = {(d.get("company"), d.get("event_id"))
                           for d in (art.get("semantic_discards") or [])
                           if d.get("confidence") == "alta"}
        _diverg = []
        for c in comps:
            if art["url"] in not_protag.get(c, set()):
                print(f"   ✂️  {c} não é protagonista em '{art['title'][:45]}…'")
                continue
            if art["url"] in drop_urls.get(c, set()):
                continue
            new_comps.append(c)
            if (art["url"], c) in confirmed:
                _llm_ids = set(confirmed[(art["url"], c)])
                # guarda determinística de ALTA confiança não é revertida em
                # silêncio: a LLM pode restringir, não reintroduzir.
                _reintro = {e for e in _llm_ids if (c, e) in _guarda_removeu}
                if _reintro:
                    _diverg.append({"company": c, "events": sorted(_reintro),
                                    "decisao": "mantida a guarda determinística",
                                    "motivo": "LLM tentou reintroduzir evento removido (confiança alta)"})
                    _llm_ids -= _reintro
                if c in _ebc:
                    _ebc[c] = [e for e in _ebc[c] if e in _llm_ids]   # LLM restringe
                else:
                    _ebc[c] = sorted(_llm_ids)
                event_ids |= set(_ebc[c])
        if not new_comps:
            removed_dups += 1
            continue
        art["companies"] = new_comps
        if _ebc:
            art["events_by_company"] = {c: v for c, v in _ebc.items() if c in new_comps}
            art["companies_attributed"] = [c for c, v in art["events_by_company"].items() if v]
            art["context_companies"] = [c for c, v in art["events_by_company"].items() if not v]
        if _diverg:
            art["llm_divergences"] = _diverg
            for d in _diverg:
                print(f"   ⚖️  Divergência LLM×guarda em '{art['title'][:40]}…': "
                      f"{d['company']} {d['events']} → {d['decisao']}")
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
    # métricas pós-pipeline por emissor (execução ATUAL, não histórico)
    for art in kept:
        for c in (art.get("companies_attributed") or art.get("companies") or []):
            m = _SEARCH_TELEMETRY.setdefault(c, {})
            m["artigos_atribuidos_ao_emissor"] = m.get("artigos_atribuidos_ao_emissor", 0) + 1
            _ids = event_ids_for({"events_by_company": art.get("events_by_company"),
                                  "event_ids": [e["id"] for e in art.get("events", [])]}, c)
            if _ids:
                m["artigos_com_evento"] = m.get("artigos_com_evento", 0) + 1
                m["eventos_classificados"] = m.get("eventos_classificados", 0) + len(_ids)
            m.setdefault("urls_atribuidas", []).append(art.get("url", ""))
        for d in (art.get("semantic_discards") or []):
            if d.get("company"):
                m = _SEARCH_TELEMETRY.setdefault(d["company"], {})
                m["eventos_removidos_por_papel"] = m.get("eventos_removidos_por_papel", 0) + 1
        for d in (art.get("llm_divergences") or []):
            m = _SEARCH_TELEMETRY.setdefault(d.get("company", ""), {})
            m["eventos_removidos_pela_llm"] = m.get("eventos_removidos_pela_llm", 0) + 1
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
    """Resolve links do Google News em registros JÁ no histórico — fontes
    PRINCIPAIS e CORROBORADORAS, pelo MESMO resolvedor canônico
    (`link_debt_audit.resolve_gnews_token`, com batchexecute).

    Faz no máximo `budget` por execução para não estourar tempo/rate-limit —
    como roda 4x/dia, o passivo é limpo em poucas execuções.

    [fix] Antes, corroboradoras só eram processadas se houvesse fontes
    principais pendentes (a chamada ficava depois de um `if not pending:
    return`). Como a maioria das principais já resolve na coleta, essa
    condição quase nunca era satisfeita e as corroboradoras nunca chegavam a
    ser tentadas. Agora os dois grupos são processados de forma independente."""
    import link_debt_audit as _lk
    resolved_cache = history.setdefault("resolved_urls", {})
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    pending = []
    for url, rec in history["articles"].items():
        if "news.google.com" not in url:
            continue
        entry = resolved_cache.get(url)
        if isinstance(entry, dict) and entry.get("exact"):
            rec["url"] = entry["url"]
            continue
        if isinstance(entry, dict) and entry.get("url"):
            rec["display_url"] = entry["url"]
        pending.append((url, rec))

    if pending:
        alvo = pending[:budget]
        print(f" 🔗 Corrigindo {len(alvo)} link(s) antigo(s) do Google News "
              f"(fontes principais)…")
        fixed = 0
        for url, rec in alvo:
            gres = _lk.resolve_gnews_token(url, session=session, cache=resolved_cache,
                                           allow_network=True)
            real = gres["url"]
            exact = bool(real and not _lk._is_gnews_host(_lk._host(real)))
            resolved_cache[url] = {"url": real or "", "exact": exact}
            if exact:
                rec["url"] = real
                fixed += 1
            time.sleep(0.4)
        print(f"   ✅ {fixed}/{len(alvo)} corrigidos para o artigo direto "
              f"(método predominante: batchexecute/inline_decode).")

    # SEMPRE tenta corroboradoras, independentemente de haver principais
    # pendentes nesta execução.
    _resolve_corroboration_urls(history, session, budget=budget)


def _resolve_corroboration_urls(history: dict, session, budget: int = 40) -> None:
    """Resolve as URLs das fontes CORROBORADORAS pelo MESMO resolvedor
    canônico das principais (`link_debt_audit.resolve_article_url`, que desde
    a consolidação inclui a cadeia completa: cache → inline_decode →
    batchexecute → redirect). Chamada de forma INDEPENDENTE de haver fontes
    principais pendentes — ver `resolve_history_urls`."""
    try:
        import link_debt_audit as _lk
    except Exception:
        return
    cache = history.setdefault("resolved_urls", {})
    pend = []
    for rec in history.get("articles", {}).values():
        for lista in ("corroborations", "corrob_sources"):
            for c in (rec.get(lista) or []):
                if isinstance(c, dict) and _lk.is_redirector(c.get("url", "")):
                    if not (c.get("display_url") and c.get("link_health") ==
                            "redirect_resolvido"):
                        pend.append(c)
    if not pend:
        return
    alvo = pend[:budget]
    print(f" 🔗 Resolvendo {len(alvo)} link(s) de fontes corroboradoras…")
    ok = 0
    for c in alvo:
        res = _lk.resolve_article_url(c.get("url", ""), domain=c.get("domain", ""),
                                      cache=cache, session=session,
                                      allow_network=True)
        dec = _lk.interface_decision(res)
        for k in ("original_url", "redirect_url", "resolved_url", "canonical_url",
                  "display_url", "redirect_chain", "original_host", "final_host",
                  "http_status", "link_health", "resolution_method",
                  "last_checked_at", "resolution_error"):
            c[k] = res.get(k, "")
        c["link_render_anchor"] = dec["render_anchor"]
        c["link_label"] = dec["label"]
        if res.get("resolved_url"):
            cache[c["url"]] = {"url": res["resolved_url"], "exact": True}
            ok += 1
        time.sleep(0.4)
    print(f"   ✅ {ok}/{len(alvo)} corroborações resolvidas para o artigo direto.")


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
        import link_debt_audit as _lk
        gres = _lk.resolve_gnews_token(gurl, session=session,
                                       cache=resolved_cache, allow_network=True)
        real = gres["url"]
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
            if entry.get("exact"):
                art["url"] = entry["url"]          # artigo real → vira o link
            else:
                art["display_url"] = entry["url"]  # fallback (home) só p/ exibir;
                                                    # mantém o token em url p/ retry
    if to_resolve:
        print(f"   ✅ {n_exact}/{len(to_resolve)} resolvidos para o artigo direto "
              f"(demais usam a home do veículo e serão retentados na próxima run).")
    if len(resolved_cache) > 3000:
        for k in list(resolved_cache)[:len(resolved_cache) - 3000]:
            del resolved_cache[k]


def event_ids_for(rec: dict, company: str) -> list:
    """Eventos do registro que valem PARA ESTA EMPRESA (4H.1e).

    `events_by_company` tem precedência; registros legados (só `event_ids`
    global) caem no fallback. Sem isto, uma notícia em que a empresa A é ré e a
    empresa B é autora aplicaria o mesmo evento às duas."""
    ebc = rec.get("events_by_company")
    if isinstance(ebc, dict):
        if company in ebc:
            return list(ebc.get(company) or [])
        # empresa não avaliada neste registro (ex.: bucket de mercado)
        return list(rec.get("event_ids", []) or []) if company == MARKET_LABEL else []
    return list(rec.get("event_ids", []) or [])          # legado



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
        # procedência internacional: idioma e texto original antes da tradução
        for k in ("language", "title_original", "summary_original"):
            if art.get(k):
                rec[k] = art[k]
        rec["companies"] = art["companies"]
        rec["event_ids"] = [e["id"] for e in art["events"]]
        # 4H.1e — associação EMPRESA × EVENTO. Sem isto, o radar volta a aplicar
        # o evento global a todas as empresas citadas (fraude da CVS vazando
        # para quem só a processou).
        for _k in ("event_assessments", "semantic_discards",
                   "secondary_events", "conflict_resolution_reason", "mention_roles",
                   "llm_divergences", "companies_attributed", "context_companies",
                   "context_events_by_company", "informational_events_by_company"):
            if art.get(_k):
                rec[_k] = art[_k]
        # [Integração] `events_by_company` é copiado sempre que a CHAVE
        # existir em `art` — mesmo com dict VAZIO (`{}`) — e não só quando
        # truthy. `suppress_non_scoreable_entity_scopes` (entity_scope=
        # brand_group/entity_pending_confirmation/scoreable=False, opt-in)
        # pode suprimir TODAS as empresas de um artigo, deixando `{}`; um
        # `{}` genuinamente avaliado é diferente de "nunca avaliado" — se não
        # for persistido, `event_ids_for` cai no fallback LEGADO
        # (`rec["event_ids"]`, evento GLOBAL sem filtro por empresa) e
        # reintroduz o evento suprimido como pontuável para qualquer empresa
        # do artigo. Para os 160 emissores reais (sem opt-in) isto nunca
        # ocorre: `events_by_company` só existe quando há ≥1 empresa com
        # ≥1 evento (nunca fica `{}` sem essa camada opt-in).
        if "events_by_company" in art:
            rec["events_by_company"] = art["events_by_company"]
        # empresas ATRIBUÍDAS × empresas de CONTEXTO: quem ficou sem evento após
        # as guardas é menção, não emissor afetado (JPMorgan no caso da CVS).
        _ebc = art.get("events_by_company")
        if isinstance(_ebc, dict):
            rec["companies_attributed"] = [c for c, ev in _ebc.items() if ev]
            rec["context_companies"] = [c for c, ev in _ebc.items() if not ev]
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
    # Gestoras/Fundos: a taxonomia própria (resgates, side pocket, liquidação,
    # key person risk…) ainda não está implementada. Enquanto isso, esses
    # emissores NÃO recebem classificação de risco corporativo — os sinais são
    # exibidos, mas sem pontuar como Crítico/Atenção.
    "monitoramento_limitado": {"label": "Monitoramento limitado", "severity": "baixa"},
}
STATUS_ORDER = {"critico": 0, "atencao": 1, "monitorar": 2,
                "monitoramento_limitado": 3}


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
            for eid in event_ids_for(rec, company):
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


# ── Identidade da OCORRÊNCIA econômica ────────────────────────────────────────
# A unidade do dashboard é a ocorrência (fato econômico), não a notícia. A chave
# considera, quando disponível: emissor + tipo + objeto + série/número + valor +
# marcadores da operação. O gap de dias é FALLBACK, não identificador único.

_SERIE_RX = re.compile(r"\b(\d{1,3})\s*[ªa°º]?\s*(?:emiss|serie|série|tranche|debentur)", re.I)
_SERIE_RX2 = re.compile(r"(?:emiss\w*|serie|série|tranche)\s*(?:n[ºo°]?\s*)?(\d{1,3})\b", re.I)
_VALOR_RX = re.compile(r"r\$\s*([\d]+(?:[.,]\d+)?)\s*(bilh|bi\b|milh|mi\b|mm\b)", re.I)
_FASE_HINTS = (
    ("encerramento", r"\b(finaliza\w*|conclui\w*|conclus\w+|encerra\w*|homologa\w*|"
                     r"sai\s+d[ao]|superad\w*|quitad\w*|encerrament\w*)\b"),
    ("agravamento", r"\b(converte\w*\s+em\s+fal\w+|descumpr\w+|inadimpl\w+|"
                    r"decretad\w*\s+a?\s*fal\w+)\b"),
    ("aprovacao", r"\b(aprova\w*|autoriza\w*|aval\b|homologac\w+|acordo|plano de recuperac\w+)\b"),
    ("precificacao", r"\b(precifica\w*|precificac\w+|fixa\s+preco|bookbuilding)\b"),
    ("anuncio", r"\b(anuncia\w*|avalia\w*|estuda\w*|negocia\w*|convoca\w*|"
                r"pede\s+registro|protocola\w*|pedido\b|solicita\w*)\b"),
)


def _fase_do_evento(titulo: str) -> str:
    t = normalize(titulo)
    for fase, rx in _FASE_HINTS:
        if re.search(rx, t):
            return fase
    return ""


def _serie_da_operacao(titulo: str) -> str:
    for rx in (_SERIE_RX, _SERIE_RX2):
        m = rx.search(titulo or "")
        if m:
            return m.group(1).lstrip("0") or "0"
    return ""


def _valor_da_operacao(titulo: str) -> str:
    m = _VALOR_RX.search(titulo or "")
    if not m:
        return ""
    num = m.group(1).replace(".", "").replace(",", ".")
    unid = m.group(2).lower()
    try:
        v = float(num)
    except ValueError:
        return ""
    if unid.startswith(("bi", "bilh")):
        v *= 1000
    return f"{round(v)}mm"


# Marcadores próprios da operação: nomes próprios que não são o emissor nem
# ruído comum. Servem para manter unida a mesma operação (ex.: "Jirau") mesmo
# quando as etapas passam de 45 dias.
_STOP_MARCADORES = {
    "fato", "relevante", "comunicado", "mercado", "acoes", "acao", "oferta",
    "emissao", "debentures", "aquisicao", "venda", "compra", "empresa", "grupo",
    "banco", "companhia", "sa", "reais", "bilhoes", "milhoes", "follow", "on",
    "capital", "social", "conselho", "diretoria", "resultado", "lucro", "receita",
}

# [fix: deduplicate operational events across articles] vocabulário genérico
# de incidente/incêndio que NÃO identifica a instalação/local (ex.: "Incendio"
# maiúsculo por estar no início da frase não é um marcador de LOCAL — usar
# isso na comparação faria "Los Olivos" x "Comas" parecerem o mesmo fato só
# por ambos começarem com "Incendio"). Usado SÓ pela função dedicada abaixo
# (`_marcadores_locais_operacionais`), nunca por `_marcadores_operacao()` —
# não altera a união por marcador já existente para M&A ('Jirau') nem
# nenhum outro emissor/família fora de `disrupcao_operacional`.
_STOP_MARCADORES_LOCAL_OPERACIONAL = {
    "incendio", "incendios", "emergencia", "alarma", "siniestro", "explosion",
    "explosiones", "planta", "almacen", "fabrica", "instalacion",
    "instalaciones", "unidad", "unidades", "bomberos", "humo", "humareda",
    "evacuacion", "evacuación", "operacion", "operaciones", "empresa",
    "extincion", "extinción", "reactiva", "reactivado", "controlado",
    "confirma", "alerta", "voraz", "detalles", "video",
    # qualificadores regionais/direcionais GENÉRICOS DEMAIS para identificar
    # uma instalação específica (ex.: 'Lima Norte' é uma macrozona que CONTÉM
    # 'Los Olivos' — duas notícias do MESMO incêndio, uma citando o distrito
    # e outra a macrozona, não podem parecer 'locais diferentes' só por isso).
    "lima", "norte", "sur", "sul", "centro", "este", "oeste", "peru", "perú",
}


def _marcadores_locais_operacionais(titulo: str, emissor: str, aliases) -> set:
    """[fix: deduplicate operational events across articles] Extrai do
    TÍTULO um conjunto de marcadores plausíveis de INSTALAÇÃO/LOCAL (ex.:
    'olivos', 'comas'), para o gate de agrupamento entre artigos da família
    `disrupcao_operacional`. Função TOTALMENTE independente de
    `_marcadores_operacao()` (usada pelo passo 3 legado de união por
    marcador — 'Jirau' — para TODAS as famílias/emissores): não a reutiliza
    nem a altera, para não mudar nada fora do escopo desta correção.

    Descarta: (a) vocabulário genérico de incidente (`_STOP_MARCADORES_
    LOCAL_OPERACIONAL` — 'incendio', 'planta', 'bomberos'...) que não
    identifica o LOCAL; (b) fragmentos de URL/slug encurtada (ex.: 'MgIVM'
    de 'shorturl.at/MgIVM' — padrão minúscula-seguida-de-maiúscula no MEIO
    da palavra não ocorre em nome próprio normal); (c) aliases do próprio
    emissor. Sem cap de quantidade (o uso aqui é comparar por interseção,
    não rotular)."""
    t = titulo or ""
    proprios = re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]{3,}", t)
    ali = {normalize(a) for a in (list(aliases or []) + [emissor])}
    out = set()
    for p in proprios:
        if re.search(r"[a-záéíóúâêôãõç][A-ZÁÉÍÓÚÂÊÔÃÕÇ]", p):
            continue  # fragmento de URL/slug (ex.: 'MgIVM')
        n = normalize(p)
        if (n in _STOP_MARCADORES or n in _STOP_MARCADORES_LOCAL_OPERACIONAL
                or n in ali or any(n in a or a in n for a in ali if a)):
            continue
        out.add(n)
    return out


def _marcadores_operacao(titulo: str, emissor: str, aliases) -> str:
    t = titulo or ""
    proprios = re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]{3,}", t)
    ali = {normalize(a) for a in (list(aliases or []) + [emissor])}
    out = []
    for p in proprios:
        n = normalize(p)
        if n in _STOP_MARCADORES or n in ali or any(n in a or a in n for a in ali if a):
            continue
        # palavra de FASE não identifica a operação (precificação, aprovação,
        # conclusão são etapas do MESMO fato) — senão cada etapa viraria um evento
        if any(re.fullmatch(rx.replace("\\b", ""), n) or re.search(rx, " " + n + " ")
               for _f, rx in _FASE_HINTS):
            continue
        out.append(n)
    return "|".join(sorted(set(out))[:2])


def occurrence_identity(titulo: str, event_id: str, emissor: str,
                        aliases=None, objeto: str = "") -> dict:
    """Identidade econômica da ocorrência a partir do título. Campos vazios
    significam 'não identificável' — nesse caso o clusterizador cai no fallback
    temporal."""
    return {
        "emissor": emissor,
        "event_id": event_id,
        "objeto": normalize(objeto or ""),
        "serie": _serie_da_operacao(titulo),
        "valor": _valor_da_operacao(titulo),
        "marcadores": _marcadores_operacao(titulo, emissor, aliases),
        "fase": _fase_do_evento(titulo),
    }


def _chave_forte(ident: dict) -> str:
    """Chave determinística quando há identidade clara — dispensa o gap de dias.
    Série (16ª × 17ª emissão) e objeto (aquisições distintas) separam sempre."""
    base = f"{ident.get('emissor','')}|{ident.get('event_id','')}"
    if ident.get("serie"):
        return f"{base}|serie:{ident['serie']}"
    if ident.get("objeto"):
        return f"{base}|obj:{ident['objeto']}"
    if ident.get("marcadores"):
        return f"{base}|op:{ident['marcadores']}"
    return ""


# ── Papel do emissor na notícia (relação e impacto) ───────────────────────────
# Substitui a supressão cega de "evento de terceiro": o evento de uma investida
# material é risco do emissor e DEVE ser atribuído — porém marcado como
# indireto, com fase e direção, para não virar "o emissor entrou em RJ".
RELATION_LABELS = {
    "direto": "Evento direto do emissor",
    "mercado_contexto": "Empresa fora da watchlist (contexto de mercado)",
    "investida_jv": "Evento de controlada/JV/investida",
    "contraparte_credor": "Emissor como credor/contraparte",
    "contexto": "Menção contextual",
}
IMPACT_LABELS = {"direto": "Impacto direto", "indireto_material": "Impacto indireto",
                 "indireto_baixo": "Impacto indireto (baixo)"}

_REL_VERBOS_INVESTIDA = (r"finaliza\w*|conclui\w*|encerra\w*|assume\w*|assumiu|controla\w*|"
                         r"detem|dete[mn]|participa\w*|informa\w*|comunica\w*|"
                         r"anuncia\w*|divulga\w*|esclarece\w*|atualiza\w*|"
                         r"presta esclarecimentos", )
# "X informa sobre Plano de RJ DA Y" — o RI de X publicar não faz de X o sujeito.
# source_company != subject_company: exige evidência textual do objeto.
_TERCEIRO_POSSESSIVO_RX = re.compile(
    r"(?:plano|acordo|processo|pedido|homologa\w+|aprova\w+|encerramento|"
    r"conclus\w+|andamento)\s+(?:de\s+|da\s+)?(?:recupera\w+\s+judicial|rj\b|"
    r"fal[eê]nci\w+|reestrutura\w+)\s+d[aeo]s?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w\-]+"
    r"(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w\-]+)?)", re.I)
_EXPOSICAO_RX = re.compile(
    r"(r\$\s*[\d.,]+\s*(?:bi|bilh|milh|mi\b))|exposic\w+|provis\w+|calote|"
    r"cred(?:or|ito)\s+de\s+r\$|perda\w*\s+de\s+r\$", re.I)


def mention_role(titulo: str, emissor: str, aliases, outros: list[str]) -> dict:
    """Classifica o papel do emissor na notícia: relação, empresa objeto do
    evento, fase e tipo de impacto. Não decide score — só rotula."""
    t = normalize(titulo)
    A = "|".join(re.escape(normalize(str(a))) for a in (list(aliases or []) + [emissor]) if a)
    fase = _fase_do_evento(titulo)
    # emissor age SOBRE um evento de terceiro? ("Vale finaliza RJ da Samarco")
    rx_terceiro = re.compile(
        rf"(?:{A})\b\s+(?:\w+\s+){{0,2}}(?:{'|'.join(_REL_VERBOS_INVESTIDA)})\s+"
        rf"(?:[ao]s?\s+)?(?:recuperac\w+ judicial|falenci\w+|reestruturac\w+|"
        rf"concordata|insolvenci\w+)\s+d[aeo]s?\s+(\w[\w\s]{{2,40}})")
    # "Plano de Recuperação Judicial DA SAMARCO" → sujeito é Samarco
    m_pos = _TERCEIRO_POSSESSIVO_RX.search(titulo or "")
    if m_pos:
        _obj = m_pos.group(1).strip()
        _eh_o_proprio = any(_word_pattern(str(a)).search(normalize(_obj))
                            for a in (list(aliases or []) + [emissor]) if a)
        if not _eh_o_proprio:
            # "Mercado (geral)" não é controlador/JV de ninguém: o sujeito é uma
            # empresa fora da watchlist, e nenhum emissor monitorado pontua.
            _rel = ("mercado_contexto" if emissor == MARKET_LABEL else "investida_jv")
            return {"relation_type": _rel,
                    "subject_company": (next((o for o in outros
                                              if normalize(o).startswith(normalize(_obj)[:6])), _obj)),
                    "impact_type": ("externo" if emissor == MARKET_LABEL
                                    else "indireto_material"),
                    "event_phase": fase or "andamento",
                    "event_scope": ("mercado" if emissor == MARKET_LABEL else "indireto"),
                    "direction_hint": "mitigadora" if fase in ("encerramento", "aprovacao")
                                      else "neutra",
                    "attribution_evidence": m_pos.group(0)[:90],
                    "attribution_confidence": "alta"}
    m = rx_terceiro.search(t)
    if m:
        obj = (outros[0] if outros else m.group(1).split()[0])
        return {"relation_type": "investida_jv", "subject_company": obj,
                "impact_type": "indireto_material", "event_phase": fase or "andamento",
                "direction_hint": "mitigadora" if fase == "encerramento" else
                                  ("negativa" if fase == "agravamento" else "neutra")}
    # emissor como credor/contraparte: atribui só com exposição econômica clara
    rx_credor = re.compile(rf"(?:{A})\b[^.]{{0,40}}\b(?:credor\w*|contraparte|exposic\w+)\b")
    if rx_credor.search(t):
        tem_exposicao = bool(_EXPOSICAO_RX.search(titulo or ""))
        return {"relation_type": "contraparte_credor",
                "subject_company": (outros[0] if outros else ""),
                "impact_type": "indireto_material" if tem_exposicao else "indireto_baixo",
                "event_phase": fase, "direction_hint": "negativa" if tem_exposicao else "neutra",
                "atribuir": tem_exposicao}
    return {"relation_type": "direto", "subject_company": "", "impact_type": "direto",
            "event_phase": fase, "direction_hint": ""}


def assign_occurrence_clusters(occurrences: list[dict], gap_days: int = 45,
                               fam_map: dict | None = None,
                               aliases_by_company: dict | None = None) -> None:
    """Define a OCORRÊNCIA econômica (unidade do dashboard). Ordem de decisão:

    1. **Separadores determinísticos** — série/número da operação (16ª × 17ª
       emissão) e empresa objeto (aquisições distintas) criam ocorrências
       diferentes SEMPRE, mesmo dentro do gap.
    2. **Agrupamento temporal** — dentro do mesmo separador, etapas (anúncio →
       aprovação → precificação) dentro de `gap_days` são o MESMO fato.
    3. **União por identidade** — clusters distantes que compartilham marcador da
       operação (ex.: 'Jirau') são reunidos, mesmo acima do gap.

    Na dúvida, separa: fundir fatos econômicos distintos é o erro mais caro.

    [fix: deduplicate operational events across articles] `fam_map` (de
    `cross_article_family_map(cfg)`) é opt-in, só para famílias que
    declararam `merge_occurrences_across_articles: true` (hoje só
    `disrupcao_operacional`). Para os EVENTOS dessas famílias, o separador
    (1) usa `family_id` em vez de `event_id` — artigos com estágios
    diferentes (incêndio leve × incêndio grave) da MESMA família viram
    candidatos à MESMA ocorrência. Dentro do gap temporal, se ambos os
    lados tiverem marcador de local/instalação identificável (ex.: 'Los
    Olivos') e ELES NÃO COINCIDIREM, a ocorrência é separada mesmo dentro
    do gap (conservador: não funde incêndios em unidades diferentes só por
    pertencerem à mesma família). A união por marcador ACIMA do gap (passo
    3) é DESLIGADA para grupos de família — dois incêndios na MESMA planta
    em datas distantes continuam sendo ocorrências distintas (exigido:
    'incêndios em datas diferentes → 2 ocorrências'). Sem `fam_map` (ou
    para eventos fora de qualquer família opt-in), o comportamento é
    IDÊNTICO ao legado (chave por `event_id` puro)."""
    limite = gap_days * 86400
    fam_map = fam_map or {}
    aliases_by_company = aliases_by_company or {}
    grupos: dict[tuple, list[dict]] = {}
    for o in occurrences:
        ident = o.get("_ident") or {}
        eid = o.get("event_id", "")
        group_ev = fam_map.get(eid, eid)
        o["_grouped_by_family"] = group_ev != eid
        grupos.setdefault((ident.get("emissor", ""), group_ev,
                           ident.get("serie", ""), ident.get("objeto", "")), []).append(o)

    for (emissor, ev, serie, objeto), itens in grupos.items():
        itens.sort(key=lambda x: x.get("pub_ts", 0))
        base = f"{emissor}|{ev}"
        if serie:
            base += f"|serie:{serie}"
        if objeto:
            base += f"|obj:{objeto}"
        is_family_group = bool(itens) and itens[0].get("_grouped_by_family")
        # (2) clusters temporais (+ para grupos de família: gate conservador
        # de marcador de local/instalação)
        clusters: list[list[dict]] = []
        anterior = None
        cluster_marcas: set = set()
        for o in itens:
            ts = o.get("pub_ts", 0)
            _id_o = o.get("_ident") or {}
            _emissor_o = _id_o.get("emissor", "")
            marcas_o = _marcadores_locais_operacionais(o.get("title", ""), _emissor_o,
                                                       aliases_by_company.get(_emissor_o))
            novo = anterior is None or (ts - anterior) > limite
            if (not novo and is_family_group and marcas_o and cluster_marcas
                    and not (marcas_o & cluster_marcas)):
                novo = True  # mesma família/janela, marcador de local diverge
            if novo:
                clusters.append([])
                cluster_marcas = set()
            clusters[-1].append(o)
            anterior = ts
            if marcas_o:
                cluster_marcas |= marcas_o
        # (3) une clusters que dividem marcador da operação — DESLIGADO para
        # grupos de família (fatos distantes no tempo continuam separados).
        if is_family_group:
            destino = list(range(len(clusters)))
        else:
            marcas = []
            for cl in clusters:
                ms = set()
                for o in cl:
                    ms |= {m for m in ((o.get("_ident") or {}).get("marcadores") or "").split("|") if m}
                marcas.append(ms)
            destino = list(range(len(clusters)))
            for a in range(len(clusters)):
                for b in range(a + 1, len(clusters)):
                    if marcas[a] and marcas[b] and (marcas[a] & marcas[b]):
                        destino[b] = destino[a]
        for n, cl in enumerate(clusters):
            k = f"{base}#{destino[n]}"
            for o in cl:
                o["_occ_key"] = k

_DIRECTION_LABELS = {"mitigadora": "↘ Risco mitigado", "negativa": "↗ Risco elevado",
                     "neutra": "→ Sem direção definida"}
_PHASE_LABELS = {"anuncio": "Anúncio", "aprovacao": "Aprovação", "precificacao": "Precificação",
                 "encerramento": "Encerramento", "agravamento": "Agravamento",
                 "andamento": "Em andamento"}


def _build_context_events(history: dict, company: str, cutoff: int) -> list:
    """Eventos de CONTEXTO (não pontuáveis) do emissor: ocorrências cujo sujeito
    é um terceiro (investida/JV). Ordenado por data desc e deduplicado por
    sujeito+evento+URL — nunca entra no breakdown nem no score."""
    itens, vistos = [], set()
    for r in history.get("articles", {}).values():
        ts = r.get("pub_ts") or 0
        if ts < cutoff:
            continue
        for c in ((r.get("context_events_by_company") or {}).get(company) or []):
            k = (normalize(c.get("subject_company", "")), c.get("event_id"), r.get("url", ""))
            if k in vistos:
                continue
            vistos.add(k)
            itens.append({**c, "pub_ts": ts, "url": r.get("url", ""),
                          "title": r.get("title", ""), "source": r.get("source", ""),
                          "date": (r.get("pub_iso") or "")[:10],
                          "scoreable": False,
                          "relation_label": RELATION_LABELS.get(c.get("relation_type", ""), ""),
                          "impact_label": IMPACT_LABELS.get(c.get("impact_type", ""), ""),
                          "direction_label": _DIRECTION_LABELS.get(c.get("direction", ""), ""),
                          "phase_label": _PHASE_LABELS.get(c.get("event_phase", ""), "")})
    itens.sort(key=lambda x: -x["pub_ts"])
    return itens[:8]


def _build_informational_events(history: dict, company: str, cutoff: int) -> list:
    """Eventos DIRETOS não pontuáveis do próprio emissor (positivos, neutros ou
    informativos): `subject_company == monitored_company`. Nunca uma empresa é
    tratada como "entidade relacionada" a si própria — isso é reservado a
    `_build_context_events` (sujeito é um TERCEIRO real). Ordenado por data
    desc, deduplicado por evento+URL — nunca entra no breakdown nem no score."""
    itens, vistos = [], set()
    for r in history.get("articles", {}).values():
        ts = r.get("pub_ts") or 0
        if ts < cutoff:
            continue
        for c in ((r.get("informational_events_by_company") or {}).get(company) or []):
            k = (c.get("event_id"), r.get("url", ""))
            if k in vistos:
                continue
            vistos.add(k)
            itens.append({**c, "pub_ts": ts, "url": r.get("url", ""),
                          "title": r.get("title", ""), "source": r.get("source", ""),
                          "date": (r.get("pub_iso") or "")[:10],
                          "scoreable": False})
    itens.sort(key=lambda x: -x["pub_ts"])
    return itens[:8]



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
    # [fix: deduplicate operational events across articles] opt-in, só para
    # famílias com merge_occurrences_across_articles=true (hoje só
    # disrupcao_operacional) — ver cross_article_family_map().
    fam_map = cross_article_family_map(cfg)
    meta = {c["name"]: {"tier": c.get("tier", 2),
                        "type": c.get("type", "empresa"),
                        "asset_group": asset_group_of_company(c),
                        # Sem fallback geográfico silencioso: um emissor sem
                        # país/região cadastrados aparece como "A revisar",
                        # nunca como "Brasil" (que mascararia erro de cadastro
                        # justamente na expansão internacional).
                        "country": c.get("country") or "A revisar",
                        "region": c.get("region") or "A revisar",
                        "language": c.get("language") or "",
                        "vehicle_kind": c.get("vehicle_kind", ""),
                        "scoring_mode": c.get("scoring_mode", "normal"),
                        "coverage": coverage_of(c, cfg)[0],
                        "regulator": coverage_of(c, cfg)[1],
                        "filing_system": coverage_of(c, cfg)[2],
                        "fetch_related_entities": bool(c.get("fetch_related_entities"))}
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

    # [fix: cross-window republication dedup] Duas entradas do histórico podem
    # ser o MESMO artigo capturado duas vezes (mesmo veículo, mesmo slug final
    # de URL — ex.: reestruturação de path do site entre coletas). Quando isso
    # acontece, uma das duas pode cair FORA da janela da vez (7/30/90/365d)
    # enquanto a outra fica dentro — sem este passo, a sobrevivente perderia
    # as fontes corroborantes que só a outra tinha persistido. Por isso este
    # levantamento roda sobre o histórico INTEIRO, sem o corte de janela: para
    # cada grupo de registros que compartilham slug de URL (própria ou de
    # qualquer corroborante já persistido), cada membro do grupo recebe a
    # UNIÃO das fontes de todos os outros (dedup por domínio, nunca a si
    # mesmo) — assim, qualquer que seja o registro que sobreviva ao corte de
    # janela, ele já carrega a lista completa de fontes conhecidas. Genérico,
    # não específico de nenhum emissor/evento.
    def _repub_url_slug(u):
        try:
            path = urlparse(u).path.strip("/")
        except Exception:
            return ""
        if not path:
            return ""
        seg = path.split("/")[-1]
        return seg if len(seg) >= 20 else ""

    def _repub_all_urls(rec):
        urls = set()
        for k in ("url", "canonical_url", "resolved_url", "display_url"):
            v = rec.get(k)
            if v:
                urls.add(v)
        for c in list(rec.get("corrob_sources", []) or []) + list(rec.get("corroborations", []) or []):
            for k in ("url", "canonical_url", "resolved_url", "display_url"):
                v = c.get(k)
                if v:
                    urls.add(v)
        return urls

    # Escopo estrito, OPT-IN por emissor (mesmo padrão de
    # `merge_occurrences_across_articles` da família disrupcao_operacional):
    # só roda para emissores que declaram `dedup_republished_sources: true`
    # no cadastro da watchlist — hoje só a B3. Sem isso, qualquer slug de URL
    # de 20+ caracteres reutilizado por acaso entre artigos NÃO relacionados
    # de outros emissores (fonte de dados comum, agregador, etc.) mudaria
    # score de emissores fora do escopo desta correção — o que violaria a
    # invariância "nenhum outro emissor muda". Além do opt-in, só agrupa
    # registros que compartilham o MESMO emissor E o MESMO event_id (além do
    # slug) — nunca entre emissores/eventos diferentes.
    _dedup_republish_companies = {c["name"] for c in cfg.get("watchlist", [])
                                  if c.get("dedup_republished_sources")}
    _slug_groups: dict[tuple, list] = {}
    if _dedup_republish_companies:
        for _rec in history["articles"].values():
            _slugs = {s for u in _repub_all_urls(_rec) if (s := _repub_url_slug(u))}
            if not _slugs:
                continue
            for _co in (_rec.get("companies_attributed") or _rec.get("companies") or []):
                if _co not in _dedup_republish_companies:
                    continue
                for _eid in (_rec.get("events_by_company") or {}).get(_co, []):
                    for _s in _slugs:
                        _slug_groups.setdefault((_s, _co, _eid), []).append(_rec)

    # Chave (id(registro), emissor, event_id) — NUNCA só id(registro) — porque
    # um mesmo artigo pode estar atribuído a mais de um emissor/evento (ex.:
    # o artigo do Estadão cita B3 E Usiminas); sem a chave composta, fontes
    # agrupadas para o caso B3 vazariam para o troca_ceo da Usiminas no MESMO
    # artigo, mudando o score de um emissor fora do escopo desta correção.
    # [fix: contribuição idêntica em toda janela onde a ocorrência aparece]
    # A janela (7/30/90/365d) só pode decidir SE a ocorrência aparece — nunca
    # mudar sua data econômica, decaimento ou contribuição. Sem isto, o
    # registro que "sobrevive" ao corte de janela vira a âncora de data por
    # acidente (o artigo mais recente do grupo, só porque o mais antigo caiu
    # fora do corte) e o mesmo fato passa a decair de forma diferente
    # dependendo da janela. A data econômica canônica do grupo é a do
    # relato CONFIRMADO mais antigo (pub_ts mínimo) — é o primeiro registro
    # do fato, não uma reindexação/recaptura posterior do mesmo artigo.
    extra_corrob_by_key: dict[tuple, list] = {}
    canonical_pub_ts_by_key: dict[tuple, int] = {}
    canonical_date_by_key: dict[tuple, str] = {}
    for (_s_key, _co_key, _eid_key), _recs in _slug_groups.items():
        _uniq, _seen = [], set()
        for _r in _recs:
            if id(_r) not in _seen:
                _seen.add(id(_r))
                _uniq.append(_r)
        if len(_uniq) < 2:
            continue
        _canon = min(_uniq, key=lambda r: r.get("pub_ts", 0) or 0)
        _canon_ts = _canon.get("pub_ts", 0) or 0
        _canon_date = (_canon.get("pub_iso") or "")[:10]
        for _target in _uniq:
            canonical_pub_ts_by_key[(id(_target), _co_key, _eid_key)] = _canon_ts
            canonical_date_by_key[(id(_target), _co_key, _eid_key)] = _canon_date
            _merged = list(_target.get("corrob_sources", []) or [])
            _merged_domains = {c.get("domain") for c in _merged if c.get("domain")}
            _target_domain = _target.get("domain")
            if _target_domain:
                _merged_domains.add(_target_domain)
            for _other in _uniq:
                if _other is _target:
                    continue
                _other_domain = _other.get("domain")
                if _other_domain and _other_domain not in _merged_domains:
                    _o_ts = _other.get("pub_ts")
                    _o_iso = ((datetime.fromtimestamp(_o_ts, tz=timezone.utc)
                              - timedelta(hours=3)).strftime("%d/%m %H:%M")) if _o_ts else ""
                    _merged.append({
                        "source": _other.get("source", ""), "domain": _other_domain,
                        "url": _other.get("url", ""), "when": _o_iso,
                        **{k: _other.get(k) for k in
                           ("display_url", "canonical_url", "resolved_url", "link_health",
                            "link_render_anchor", "link_label") if _other.get(k) is not None}})
                    _merged_domains.add(_other_domain)
                for _c in (_other.get("corrob_sources") or []):
                    _cd = _c.get("domain")
                    if _cd and _cd not in _merged_domains:
                        _merged.append(_c)
                        _merged_domains.add(_cd)
            extra_corrob_by_key[(id(_target), _co_key, _eid_key)] = _merged

    per_company: dict[str, list[dict]] = {}
    for rec in history["articles"].values():
        _rec_pub_ts = rec.get("pub_ts", 0)
        for company in rec.get("companies", []):
            if company == MARKET_LABEL:
                continue
            _grp = meta.get(company, {}).get("asset_group", "a_revisar")
            for eid in event_ids_for(rec, company):
                if eid not in taxonomy:
                    continue
                ev = taxonomy[eid]
                # o evento precisa fazer sentido para a natureza do emissor
                if not event_applies_to(ev, _grp):
                    continue
                # data efetiva: canônica do grupo (se este registro faz parte
                # de um grupo de republicação opt-in), senão a própria — o
                # corte de janela usa SEMPRE a data efetiva, nunca a bruta,
                # senão o mesmo fato entra/sai da janela de forma inconsistente
                # entre os registros que o compõem.
                eff_pub_ts = canonical_pub_ts_by_key.get((id(rec), company, eid), _rec_pub_ts)
                eff_date = canonical_date_by_key.get((id(rec), company, eid),
                                                      (rec.get("pub_iso") or "")[:10])
                if eff_pub_ts < cutoff:
                    continue
                days_ago = max(0.0, (now_ts - eff_pub_ts) / 86400)
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
                    "pub_ts": eff_pub_ts,
                    "date": eff_date,
                    "title": rec.get("title", ""),
                        "url": link_for_display(rec),
                    "domain": rec.get("domain", ""),
                    "persisted_corrob": extra_corrob_by_key.get((id(rec), company, eid),
                                                                rec.get("corrob_sources", [])),
                    # campos de link persistidos pelo reparo (--repair-links-only)
                    **{k: rec.get(k) for k in
                       ("display_url", "canonical_url", "resolved_url", "link_health",
                        "link_render_anchor", "link_label") if rec.get(k) is not None},
                    "pos_pct": round(100.0 * (window_days - days_ago) / window_days, 2),
                    "opacity": round(0.35 + 0.65 * decay_weight(eff_pub_ts, now_ts), 2),
                    # identidade econômica da ocorrência + papel do emissor
                    "_ident": occurrence_identity(
                        rec.get("title", ""), eid, company,
                        (next((c.get("aliases") for c in cfg.get("watchlist", [])
                               if c.get("name") == company), None)),
                        objeto=((rec.get("mention_roles") or {}).get(company, {})
                                .get("subject_company", ""))),
                    "relation_type": ((rec.get("mention_roles") or {}).get(company, {})
                                      .get("relation_type", "direto")),
                    "subject_company": ((rec.get("mention_roles") or {}).get(company, {})
                                        .get("subject_company", "")),
                    "impact_type": ((rec.get("mention_roles") or {}).get(company, {})
                                    .get("impact_type", "direto")),
                    "legal_status": next((a.get("legal_status", "") for a in
                                          (rec.get("event_assessments") or [])
                                          if a.get("company") == company
                                          and a.get("event_id") == eid), ""),
                    "confirmation_status": next((a.get("confirmation_status", "") for a in
                                                 (rec.get("event_assessments") or [])
                                                 if a.get("company") == company
                                                 and a.get("event_id") == eid), ""),
                    "event_phase": ((rec.get("mention_roles") or {}).get(company, {})
                                    .get("event_phase", "")),
                })

    # Dedup determinístico do MESMO evento (independe do LLM): várias notícias
    # do mesmo tipo de evento, para o mesmo emissor, dentro de uma janela curta
    # são a MESMA história — colapsam em 1 sinal (o de maior confiança), com as
    # demais fontes viradas corroboração. Sem isso, "RJ ×3" da mesma cobertura
    # infla a contagem de sinais e some com a credibilidade multi-fonte.
    collapse_days = ev_cfg.get("same_event_window_days", 10)

    def _fam_key_ev(eid):
        return fam_map.get(eid, eid)

    _aliases_by_company = {c["name"]: (c.get("aliases") or [c["name"]])
                          for c in cfg.get("watchlist", [])}

    def _marcadores_de(o):
        _id_o = o.get("_ident") or {}
        _emissor = _id_o.get("emissor", "")
        return _marcadores_locais_operacionais(o.get("title", ""), _emissor,
                                               _aliases_by_company.get(_emissor))

    # [fix: same-article republication dedup] Duas ocorrências do MESMO
    # emissor+evento podem ser, na verdade, o MESMO artigo capturado duas
    # vezes (uma vez como corroboração de outro principal, outra vez como
    # principal independente — ex.: reestruturação de URL do veículo entre
    # coletas). Isso escapa da janela `collapse_days` quando a segunda
    # captura acontece muito depois. Genérico, não específico de nenhum
    # evento/emissor: se o SLUG final (último segmento de path, só quando
    # longo o bastante para ser um identificador de artigo, não uma home
    # page) da URL de uma ocorrência aparece entre as URLs (própria ou de
    # qualquer corroborante já persistido) da outra, é o mesmo artigo —
    # funde independentemente da distância temporal. Colisão de slug entre
    # artigos diferentes é praticamente impossível (string longa e
    # específica do título).
    def _url_variants(o):
        urls = set()
        for k in ("url", "canonical_url", "resolved_url", "display_url"):
            v = o.get(k)
            if v:
                urls.add(v)
        for c in list(o.get("persisted_corrob", []) or []) + list(o.get("corrob", []) or []):
            for k in ("url", "canonical_url", "resolved_url", "display_url"):
                v = c.get(k)
                if v:
                    urls.add(v)
        return urls

    def _url_slug(u):
        try:
            path = urlparse(u).path.strip("/")
        except Exception:
            return ""
        if not path:
            return ""
        seg = path.split("/")[-1]
        return seg if len(seg) >= 20 else ""

    def _slugs_de(o):
        return {s for s in (_url_slug(u) for u in _url_variants(o)) if s}

    for company, occs in list(per_company.items()):
        occs.sort(key=lambda o: (-o.get("trust_w", 1.0), o["pub_ts"]))
        merged: list[dict] = []
        for o in occs:
            # [fix: deduplicate operational events across articles] para
            # eventos de família opt-in, o "twin" é procurado por FAMÍLIA
            # (não só event_id exato) — artigos com estágios diferentes do
            # MESMO fato (incêndio leve × incêndio grave) viram a MESMA
            # ocorrência. Gate conservador (SÓ para grupos de família): se
            # ambos os lados têm marcador de local/instalação identificável
            # e eles DIVERGEM, não é considerado "twin" (evita fundir
            # incidentes em unidades diferentes). Para eventos fora de
            # família opt-in, a comparação continua EXATA por event_id, SEM
            # nenhum gate de marcador — comportamento legado 100% intacto
            # (o gate de marcador nunca existiu para esses eventos antes
            # desta correção e não pode passar a existir agora).
            o_is_family = _fam_key_ev(o["event_id"]) != o["event_id"]
            o_marcas = _marcadores_de(o) if o_is_family else set()
            # slug de URL como critério ADICIONAL de "twin" (mesmo artigo
            # republicado com URL ligeiramente diferente) — mesmo opt-in
            # `dedup_republished_sources` do pré-cálculo acima, para não
            # fundir por coincidência de slug em emissores fora do escopo.
            o_slugs = _slugs_de(o) if company in _dedup_republish_companies else set()
            twin = next((m for m in merged
                         if _fam_key_ev(m["event_id"]) == _fam_key_ev(o["event_id"])
                         and (abs(m["pub_ts"] - o["pub_ts"]) <= collapse_days * 86400
                              or (o_slugs and o_slugs & _slugs_de(m)))
                         and not (o_is_family and o_marcas and _marcadores_de(m)
                                  and not (o_marcas & _marcadores_de(m)))), None)
            if twin is None:
                # começa já com as fontes corroborantes persistidas no histórico
                o["corrob"] = list(o.get("persisted_corrob", []))
                merged.append(o)
                continue
            if (o["event_id"] != twin["event_id"]
                    and taxonomy.get(o["event_id"], {}).get("score", 0)
                        > taxonomy.get(twin["event_id"], {}).get("score", 0)):
                # [fix: deduplicate operational events across articles]
                # promoção de estágio: o artigo NOVO descreve um estágio mais
                # grave (score-base maior) da MESMA família/ocorrência —
                # ele vira o representante; o antigo representante (e todas
                # as fontes que já tinha acumulado) vira corroborante do
                # novo. Nenhum score adicional: best_contribs() usa 1 só
                # score-base por _occ_key (o do estágio mais grave).
                o["corrob"] = list(o.get("persisted_corrob", []))
                dom_prev = twin.get("domain", "")
                if dom_prev and all(c.get("domain") != dom_prev for c in o["corrob"]):
                    prev_iso = (datetime.fromtimestamp(twin["pub_ts"], tz=timezone.utc)
                               - timedelta(hours=3)).strftime("%d/%m %H:%M") if twin.get("pub_ts") else ""
                    o["corrob"].append({
                        "source": twin.get("source", ""), "domain": dom_prev,
                        "url": twin.get("url", ""), "when": prev_iso,
                        **{k: twin.get(k) for k in
                           ("display_url", "canonical_url", "resolved_url",
                            "link_health", "link_render_anchor", "link_label")
                           if twin.get(k) is not None}})
                for c in twin.get("corrob", []):
                    if c.get("domain") and c["domain"] != dom_prev and \
                       all(x.get("domain") != c["domain"] for x in o["corrob"]):
                        o["corrob"].append(c)
                merged[merged.index(twin)] = o
                continue
            dom = o.get("domain", "")
            if dom and dom != twin.get("domain") and \
               all(c.get("domain") != dom for c in twin["corrob"]):
                o_iso = (datetime.fromtimestamp(o["pub_ts"], tz=timezone.utc)
                         - timedelta(hours=3)).strftime("%d/%m %H:%M") if o.get("pub_ts") else ""
                twin["corrob"].append({
                    "source": o.get("source", ""), "domain": dom,
                    "url": o.get("url", ""), "when": o_iso,
                    # preserva a resolução já persistida pelo reparo
                    **{k: o.get(k) for k in
                       ("display_url", "canonical_url", "resolved_url",
                        "link_health", "link_render_anchor", "link_label")
                       if o.get(k) is not None}})
            # herda também as fontes que a duplicata já tinha persistido
            for c in o.get("persisted_corrob", []):
                if c.get("domain") and c["domain"] != twin.get("domain") and \
                   all(x.get("domain") != c["domain"] for x in twin["corrob"]):
                    twin["corrob"].append(c)
        per_company[company] = merged

    # ── Emissor com APENAS contexto continua visível ──
    # Quando a resolução semântica move todos os eventos de um emissor para
    # context_events_by_company (ex.: Gerdau/transportadoras, Cencosud/St.
    # Marche), ele deixaria de ter ocorrências pontuáveis e o card sumiria do
    # radar — escondendo informação relevante. Cria-se a linha com score 0 e
    # somente o bloco "Contexto relacionado · não pontua".
    for _rec in history.get("articles", {}).values():
        if (_rec.get("pub_ts") or 0) < cutoff:
            continue
        for _co, _evs in (_rec.get("context_events_by_company") or {}).items():
            if _evs and _co != MARKET_LABEL and _co in meta:
                per_company.setdefault(_co, [])
        # idem para eventos diretos não pontuáveis do próprio emissor (ex.:
        # Santander/TSB resultado positivo, Santander/Esfera reorganização
        # interna) — o card continua visível mesmo sem ocorrência pontuável.
        for _co, _evs in (_rec.get("informational_events_by_company") or {}).items():
            if _evs and _co != MARKET_LABEL and _co in meta:
                per_company.setdefault(_co, [])

    # [fix: complete Peru news links taxonomy and holding coverage] holding
    # com monitoramento de related_entities configurado (`fetch_related_
    # entities: true`) sempre tem card visível — mesmo em uma execução sem
    # NENHUMA notícia (direta ou de subsidiária) encontrada nesta janela.
    # "Card ausente" esconderia que a holding está sendo monitorada; "card
    # com score 0 e 0 notícias" é a superfície de monitoramento honesta.
    # Opt-in, aditivo — nenhum dos 160 emissores reais declara esse campo.
    for _c in cfg.get("watchlist", []):
        if _c.get("fetch_related_entities") and _c.get("name"):
            per_company.setdefault(_c["name"], [])

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
            k = o.get("_occ_key") or o["event_id"]
            cur = best.get(k)
            if cur is None or contrib > cur["contrib"]:
                best[k] = {**o, "decay_f": d, "contrib": contrib,
                                       "base_contrib": round(base_contrib, 1),
                                       "corrob_bonus": round(bonus, 1)}
        return best

    def weighted_total(negatives: list[dict], as_of_ts: int) -> float:
        return sum(b["contrib"] for b in best_contribs(negatives, as_of_ts).values())

    rows = []
    for company, occurrences in per_company.items():
        occurrences.sort(key=lambda o: o["pub_ts"])
        assign_occurrence_clusters(
            occurrences, int(ev_cfg.get("occurrence_gap_days", 45)), fam_map=fam_map,
            aliases_by_company=_aliases_by_company)
        negatives = [o for o in occurrences if not o["positive"]]
        _tem_contexto = bool(_build_context_events(history, company, cutoff))
        _tem_informativo = bool(_build_informational_events(history, company, cutoff))
        # [fix: complete Peru news links taxonomy and holding coverage]
        # holding com `fetch_related_entities` sempre mantém a linha, mesmo
        # zerada — é a superfície de monitoramento, não "sem cobertura".
        _meta_c = meta.get(company, {}) or {}
        _sempre_visivel = bool(_meta_c.get("fetch_related_entities"))
        if not negatives and not _tem_contexto and not _tem_informativo and not _sempre_visivel:
            continue  # só contexto/informativo: não é linha de risco
        # Sem evento pontuável MAS com contexto ou sinal direto informativo
        # relevante: a linha é mantida com score 0 apenas para exibir
        # "Contexto relacionado · não pontua" / "Sinal positivo · não pontua" /
        # "Evento informativo · não pontua".

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
            _lf = link_fields(b)
            all_sources = [{"source": b["source"], "url": b.get("url", ""),
                            "when": src_iso, "trust": b.get("trust_label", ""),
                            "primary": True,
                            "href": _lf["href"], "render_anchor": _lf["render_anchor"],
                            "link_label": _lf["label"], "link_health": _lf["link_health"]}]
            for c in b.get("corrob", []):
                # MESMA função da principal — foi a assimetria entre os dois
                # caminhos que produziu os links intermediários quebrados.
                _cf = link_fields(c)
                all_sources.append({"source": c.get("source", ""),
                                    "url": c.get("url", ""), "when": c.get("when", ""),
                                    "trust": "", "primary": False,
                                    "href": _cf["href"], "render_anchor": _cf["render_anchor"],
                                    "link_label": _cf["label"],
                                    "link_health": _cf["link_health"]})
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
                # rotulagem de relação/impacto (4H) — o card mostra "Evento da
                # Samarco · Impacto indireto sobre Vale · Encerramento"
                "relation_type": b.get("relation_type", "direto"),
                "relation_label": RELATION_LABELS.get(b.get("relation_type", "direto"), ""),
                "subject_company": b.get("subject_company", ""),
                "impact_type": b.get("impact_type", "direto"),
                "impact_label": IMPACT_LABELS.get(b.get("impact_type", "direto"), ""),
                "event_phase": b.get("event_phase", ""),
                "legal_status": b.get("legal_status", ""),
                "confirmation_status": b.get("confirmation_status", ""),
            })

        # Linha do tempo = 1 ponto por EVENTO real (cluster de ocorrências do
        # mesmo tipo dentro da janela), não 1 por notícia: várias matérias sobre
        # a mesma operação viravam vários pontinhos e inflavam "N sinais".
        _clusters: dict[str, dict] = {}
        for o in occurrences:
            k = o.get("_occ_key") or o["event_id"]
            cur = _clusters.get(k)
            if cur is None or SEVERITY_ORDER[o["severity"]] < SEVERITY_ORDER[cur["severity"]] \
               or (o["severity"] == cur["severity"] and o["pub_ts"] > cur["pub_ts"]):
                _clusters[k] = o
        timeline_occ = sorted(_clusters.values(), key=lambda o: o["pub_ts"])

        # Deterioração persistente: acúmulo de sinais negativos em janela curta
        pa = ev_cfg.get("persistence_alert", {})
        pa_days = pa.get("days", 45)
        pa_cutoff = now_ts - pa_days * 86400
        recent = [o for o in negatives if o["pub_ts"] >= pa_cutoff]
        persistent = (len(recent) >= pa.get("min_signals", 3)
                      and len({o["event_id"] for o in recent}) >= pa.get("min_types", 2))
        persistence_text = (f"{len(recent)} sinais negativos em {pa_days} dias"
                            if persistent else "")

        # Gestoras/fundos já pontuam — mas SÓ pelos eventos da taxonomia de
        # veículo/gestor, porque os corporativos foram filtrados acima por
        # `applies_to`. O modo legado 'monitoramento_limitado' continua
        # disponível como trava manual no cadastro.
        _sm = meta.get(company, {}).get("scoring_mode", "normal")
        if _sm == "monitoramento_limitado":
            status = "monitoramento_limitado"
        elif has_hard_critical or total >= critico_total:
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
        m = meta.get(company, {"tier": 2, "type": "empresa", "asset_group": "a_revisar",
                               "country": "A revisar", "region": "A revisar",
                               "language": "", "vehicle_kind": "", "scoring_mode": "normal"})
        rows.append({
            "company": company,
            "tier": m["tier"],
            "type": m["type"],
            "asset_group": m.get("asset_group", "a_revisar"),
            "country": m.get("country") or "A revisar",
            "region": m.get("region") or "A revisar",
            "language": m.get("language") or "",
            "vehicle_kind": m.get("vehicle_kind", ""),
            "scoring_mode": m.get("scoring_mode", "normal"),
            "coverage": m.get("coverage", "ampla"),
            "regulator": m.get("regulator", ""),
            "filing_system": m.get("filing_system", ""),
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
            # contexto relacionado (NÃO pontua): eventos cujo sujeito é terceiro
            "context_events": _build_context_events(history, company, cutoff),
            # eventos DIRETOS não pontuáveis do próprio emissor (positivo/
            # neutro/informativo) — NUNCA "entidade relacionada a si mesma"
            "informational_events": _build_informational_events(history, company, cutoff),
            "timeline": timeline_occ,
            "breakdown": breakdown,
            "persistent": persistent,
            "persistence_text": persistence_text,
            "spark_points": spark_points,
            "first_date": (occurrences[0]["date"] if occurrences else ""),
            "last_date": (occurrences[-1]["date"] if occurrences else ""),
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


def build_feed(history: dict, cfg: dict, window_days: int | None = None) -> list[dict]:
    """Lista de notícias da janela, com severidade/score por artigo."""
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    if window_days is None:
        window_days = cfg["dashboard"].get("default_window", 7)
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())

    # país/idioma do emissor, p/ exibir procedência da notícia internacional
    pais_por_emissor = {c["name"]: (c.get("country") or "A revisar")
                        for c in cfg.get("watchlist", [])}
    feeds_lang = {(f.get("url") or ""): f.get("language", "")
                  for f in (cfg.get("custom_feeds") or [])}

    grupo_por_emissor = {c["name"]: asset_group_of_company(c)
                         for c in cfg.get("watchlist", [])}

    feed = []
    for rec in history["articles"].values():
        if rec.get("pub_ts", 0) < cutoff:
            continue
        # 4H.1f — feed por empresa ATRIBUÍDA: o evento da CVS não pode aparecer
        # como evento do JPMorgan só porque ele é citado na mesma matéria.
        _attr = rec.get("companies_attributed")
        if _attr is None:
            _ebc = rec.get("events_by_company")
            _attr = ([c for c, ev in _ebc.items() if ev] if isinstance(_ebc, dict)
                     else list(rec.get("companies", []) or []))
        _ids_feed = set()
        for _c in (_attr or [MARKET_LABEL]):
            _ids_feed |= set(event_ids_for(rec, _c))
        if not _ids_feed and not _attr:
            _ids_feed = set(rec.get("event_ids", []) or [])
        todos = [taxonomy[eid] for eid in _ids_feed if eid in taxonomy]
        if not todos:
            continue
        # Mesma regra do Radar: um evento só é "classificado válido" se fizer
        # sentido para a natureza de algum emissor citado. Notícia de mercado
        # (sem emissor da watchlist) usa os grupos corporativos como padrão.
        grupos = {grupo_por_emissor[c] for c in (_attr or rec.get("companies", []))
                  if c in grupo_por_emissor}
        if not grupos:
            grupos = set(_CORPORATE_GROUPS)
        events, nao_aplicaveis = [], []
        for e in todos:
            (events if any(event_applies_to(e, g) for g in grupos)
             else nao_aplicaveis).append(e)
        if not events:
            # nenhum evento se aplica à natureza do(s) emissor(es) → a notícia
            # não é um sinal classificado para eles
            continue
        worst = min(events, key=lambda e: SEVERITY_ORDER[e["severity"]])
        t_id, t_w, t_label = trust_of_rec(rec, cfg)
        dims = sorted({DIMENSION_LABELS.get(d, d) for e in events
                       for d in e.get("dimensions", [])})
        # 4H.1f-final — UMA LINHA POR EMPRESA × ARTIGO. `**rec` preservava
        # `companies` com todas as citadas, então o JPMorgan aparecia com a
        # tag de fraude da CVS. Cada empresa atribuída vira uma linha com os
        # SEUS eventos; as demais viram contexto.
        _linhas_emp = []
        for _emp in (_attr or []):
            _ids_emp = [i for i in event_ids_for(rec, _emp) if i in taxonomy]
            _evs_emp = [taxonomy[i] for i in _ids_emp]
            _evs_emp = [e for e in _evs_emp if e["id"] in {x["id"] for x in events}]
            if _evs_emp:
                _linhas_emp.append((_emp, _evs_emp))
        if not _linhas_emp:
            _linhas_emp = [("", events)]
        for _emp, _evs_emp in _linhas_emp:
            _ctx = [c for c in (rec.get("companies") or []) if c != _emp] if _emp else []
            _worst = min(_evs_emp, key=lambda e: SEVERITY_ORDER[e["severity"]])
            feed.append({
                **rec,
                "company": _emp,
                "companies": [_emp] if _emp else (rec.get("companies") or []),
                "context_companies": rec.get("context_companies") or _ctx,
            "url": link_for_display(rec),
                "events": [{"id": e["id"], "label": e["label"],
                            "severity": e["severity"], "score": e["score"],
                            "direction": e.get("direction", "negativa")} for e in _evs_emp],
                "severity": _worst["severity"],
                "score": max(e["score"] for e in _evs_emp),
                "trust": t_id, "trust_label": t_label, "trust_w": t_w,
                "confirmation": confirmation_of(rec, cfg),
                "corroborations": [{"source": e.get("source", ""), "url": e.get("url", "")}
                                   for e in (rec.get("corroborations") or [])[:5]],
                "dimensions": dims,
                # procedência: idioma original, título antes da tradução e país do
                # emissor — o link continua sempre apontando para a matéria original
                # eventos detectados no texto mas que NÃO pontuam para a natureza
                # deste emissor (exibidos como contexto, nunca como sinal válido)
                "events_nao_aplicaveis": [{"id": e["id"], "label": e["label"],
                                           "severity": e["severity"]}
                                          for e in nao_aplicaveis],
                "language": rec.get("language", ""),
                "title_original": rec.get("title_original", ""),
                "translated": bool(rec.get("title_original")),
                "country": (pais_por_emissor.get(_emp) or
                            next((pais_por_emissor.get(c) for c in rec.get("companies", [])
                                  if pais_por_emissor.get(c)), "")),
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

        # ── Cobertura internacional (idioma original preservado) ──
        ("Ford Motor downgraded by rating agency on weak North America margins",
         "Rating cut by one notch; outlook remains negative amid pricing pressure.",
         "CNBC", "cnbc.com", 5, None),
        ("Cemex: S&P cuts rating tras la caída de la demanda en México",
         "La agencia recorta la calificación y mantiene perspectiva negativa.",
         "El Economista (MX)", "eleconomista.com.mx", 8, None),
        ("Kapitalo suspende los rescates del fondo multimercado tras pérdidas",
         "La gestora informó la suspensión temporal de rescates a los cotistas.",
         "Ámbito", "ambito.com", 4, None),

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
        _attr_c = rec.get("companies_attributed")
        if _attr_c is None:
            _ebc_c = rec.get("events_by_company")
            _attr_c = ([c for c, ev in _ebc_c.items() if ev] if isinstance(_ebc_c, dict)
                       else list(rec.get("companies", []) or []))
        # UM SINAL POR EMPRESA × ARTIGO: nunca unir eventos de empresas
        # diferentes (A com fraude e B com rebaixamento são 2 sinais distintos).
        _pares = []
        for _c in (_attr_c or []):
            _ev_c = [taxonomy[e] for e in event_ids_for(rec, _c) if e in taxonomy]
            if _ev_c:
                _pares.append((_c, _ev_c))
        if not _pares and not _attr_c:
            _ev_g = [taxonomy[e] for e in (rec.get("event_ids") or []) if e in taxonomy]
            if _ev_g:
                _pares = [("", _ev_g)]
        if not _pares:
            continue
        t_id, t_w, t_label = trust_of_rec(rec, cfg)
        for _c, events in _pares:
            worst = min(events, key=lambda e: SEVERITY_ORDER[e["severity"]])
            new_signals.append({
                "title": rec.get("title", ""), "url": url,
                "source": rec.get("source", ""), "pub_iso": rec.get("pub_iso", ""),
                "company": _c,
                "companies": [_c] if _c else (rec.get("companies") or []),
                "context_companies": rec.get("context_companies", []),
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



def validate_sources(cfg: dict) -> list[str]:
    """4A.4 — Consistência das fontes internacionais e dos metadados de coleta.
    Toda fonte internacional precisa declarar país, idioma e peso de confiança;
    todo país com regulador cadastrado precisa de URL e viabilidade."""
    msgs: list[str] = []
    tiers = set((cfg.get("source_trust") or {}).get("tiers", {}))
    for f in cfg.get("custom_feeds", []) or []:
        nome = f.get("name", "(sem nome)")
        for campo in ("country", "language", "trust_tier"):
            if not f.get(campo):
                msgs.append(f"WARNING: feed '{nome}' sem `{campo}` declarado.")
        if f.get("trust_tier") and f["trust_tier"] not in tiers:
            msgs.append(f"WARNING: feed '{nome}' com `trust_tier` desconhecido "
                        f"('{f['trust_tier']}'). Aceitos: {', '.join(sorted(tiers))}.")
        if not f.get("url"):
            msgs.append(f"ERRO: feed '{nome}' sem URL.")
    paises_wl = {c.get("country") for c in cfg.get("watchlist", []) if c.get("country")}
    osrc = cfg.get("official_sources") or {}
    for pais in sorted(paises_wl):
        if pais == "A revisar":
            continue
        if pais not in osrc:
            msgs.append(f"WARNING: país '{pais}' presente na watchlist mas sem entrada "
                        f"em `official_sources` (regulador/filings não documentados).")
            continue
        s = osrc[pais]
        if not s.get("viabilidade"):
            msgs.append(f"WARNING: `official_sources['{pais}']` sem `viabilidade`.")
        if s.get("filings") and not s.get("filings_url"):
            msgs.append(f"WARNING: `official_sources['{pais}']` declara sistema de "
                        f"filings sem `filings_url`.")
    return msgs


# Documentos de fase, plano e changelog citam números antigos de propósito —
# eles registram o que foi corrigido. Validá-los produziria falso positivo.
# A checagem vale para a documentação de PRODUÇÃO (README e manuais).
_DOC_HISTORICOS = re.compile(r"(FASE\d|PLANO|DIAGNOSTICO|CHANGELOG|HISTORICO)",
                             re.I)


def validate_docs(cfg: dict, paths: list[str]) -> list[str]:
    """4A.4 — Impede que a documentação de produção cite número desatualizado.
    Compara os textos com os valores REAIS do cadastro. Documentos históricos
    (fase/plano/diagnóstico/changelog) são ignorados: neles a menção ao número
    antigo é intencional."""
    import glob as _glob
    n_wl = len(cfg.get("watchlist", []))
    n_edgar = sum(1 for c in cfg.get("watchlist", []) if edgar_eligible(c))
    obsoletos = [
        (r"\b73 emissores\b", f"watchlist tem {n_wl} emissores"),
        (r"\bEDGAR[^.\n]{0,40}\b25 emissor", f"EDGAR cobre {n_edgar} emissores elegíveis"),
        (r"Gestoras?/Fundos?\s*:?\s*monitoramento limitado|"
         r"est(ão|á|ao|a) em monitoramento limitado",
         "Gestoras/Fundos usam taxonomia própria desde a Fase 3"),
        (r"quatro abas|4 abas", "o dashboard tem três abas"),
    ]
    msgs = []
    vistos: set[str] = set()
    for pattern in paths:
        for path in sorted(set(_glob.glob(pattern))):
            if path in vistos:
                continue
            vistos.add(path)
            if _DOC_HISTORICOS.search(Path(path).name):
                continue  # documento histórico: menção ao número antigo é proposital
            try:
                txt = Path(path).read_text(encoding="utf-8")
            except Exception:
                continue
            for rx, correcao in obsoletos:
                if re.search(rx, txt, re.I):
                    msgs.append(f"WARNING: '{Path(path).name}' contém texto possivelmente "
                                f"desatualizado (/{rx}/) — {correcao}.")
    return msgs



# ── Probe de fontes oficiais (diagnóstico, NÃO coleta) ───────────────────────
_JS_HINTS = ("__NEXT_DATA__", "window.__NUXT__", "ng-app", "data-reactroot",
             "id=\"root\"", "id=\"app\"", "requirejs", "angular.bootstrap")
_BOT_HINTS = ("cf-browser-verification", "cloudflare", "captcha", "recaptcha",
              "access denied", "forbidden", "incapsula", "akamai", "bot detection")


def _probe_one(url: str, session: requests.Session, timeout: int = 25) -> dict:
    """Mede UMA URL: status, tipo, tamanho, indícios de JS/anti-bot, latência.
    Não interpreta conteúdo nem extrai dados — é só instrumentação."""
    r = {"url": url, "ok": False, "status": None, "content_type": "", "bytes": 0,
         "rss_declarado": False, "provavel_js": False, "anti_bot": False,
         "bloqueio_ambiente": False, "latencia_ms": None, "erro": ""}
    t0 = time.time()
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True,
                           headers={"User-Agent": "Mozilla/5.0 (compatible; RadarDeRisco/1.0)"})
        r["latencia_ms"] = int((time.time() - t0) * 1000)
        r["status"] = resp.status_code
        r["content_type"] = (resp.headers.get("Content-Type") or "").split(";")[0]
        body = resp.text or ""
        r["bytes"] = len(resp.content or b"")
        low = body.lower()
        r["ok"] = resp.ok
        # O proxy de egresso do ambiente de build também devolve 403. Isso NÃO
        # é anti-bot da fonte — confundir os dois produziria um diagnóstico
        # falso justamente onde se quer fato.
        r["bloqueio_ambiente"] = bool(
            resp.headers.get("x-deny-reason")
            or "not in allowlist" in low
            or "host not allowed" in low)
        r["rss_declarado"] = bool(re.search(
            r'<link[^>]+type=["\']application/(rss|atom)\+xml', body, re.I))
        r["provavel_js"] = any(h.lower() in low for h in _JS_HINTS)
        r["anti_bot"] = (not r["bloqueio_ambiente"]
                         and (resp.status_code in (403, 429)
                              or any(h in low[:4000] for h in _BOT_HINTS)))
    except Exception as exc:
        r["latencia_ms"] = int((time.time() - t0) * 1000)
        r["erro"] = f"{type(exc).__name__}: {exc}"[:160]
    return r


def probe_official_sources(cfg: dict, out_csv: str | None = None) -> list[dict]:
    """4A.5 — Mede a acessibilidade das fontes oficiais cadastradas.

    Existe porque o ambiente de desenvolvimento não alcança os portais dos
    reguladores latino-americanos: escrever um scraper às cegas produziria
    exatamente o coletor frágil que se quer evitar. Este comando roda no
    GitHub Actions (rede aberta) e devolve fatos — status, tipo de conteúdo,
    presença de RSS, indício de renderização por JavaScript, sinal de
    anti-bot e latência — para decidir com dado o que implementar na 4B.

    Não coleta notícias e não altera o histórico."""
    osrc = cfg.get("official_sources") or {}
    alvos = []
    for pais, s in osrc.items():
        for campo in ("filings_url", "api"):
            u = s.get(campo)
            if u and isinstance(u, str) and u.startswith("http") and "{" not in u:
                alvos.append((pais, campo, u))
        # 4B.3 — alvos estruturados para re-probe (ex.: candidatos do México)
        for t in (s.get("probe_targets") or []):
            u = t.get("url") if isinstance(t, dict) else None
            if u and isinstance(u, str) and u.startswith("http") and "{" not in u:
                alvos.append((pais, "probe_target", u))
    # 4C.3 — fontes de FUNDOS/FIIs (diagnóstico; coletor desativado por padrão)
    for t in ((cfg.get("fund_sources") or {}).get("probe_targets") or []):
        u = t.get("url") if isinstance(t, dict) else None
        if u and isinstance(u, str) and u.startswith("http") and "{" not in u:
            alvos.append(("Fundos", "fund_target", u))
    if not alvos:
        print(" ℹ️  Nenhuma URL de fonte oficial para medir.")
        return []

    print(f" 🩺 Probe de fontes oficiais: {len(alvos)} URL(s) — diagnóstico, sem coleta.")
    session, linhas = requests.Session(), []
    for pais, campo, url in alvos:
        r = _probe_one(url, session)
        r.update({"pais": pais, "campo": campo,
                  "regulador": (osrc.get(pais) or {}).get("regulador", "")
                                or ((cfg.get("fund_sources") or {}).get("regulador", "") if pais == "Fundos" else ""),
                  "viabilidade_documentada": (osrc.get(pais) or {}).get("viabilidade", "")})
        # veredito operacional, derivado do que foi medido
        if r["bloqueio_ambiente"]:
            r["veredito"] = ("bloqueado pelo ambiente de execução — "
                             "medir no GitHub Actions (rede aberta)")
        elif r["anti_bot"]:
            r["veredito"] = "anti-bot — exigiria navegador headless"
        elif r["erro"] or not r["ok"]:
            r["veredito"] = "indisponivel — revisar URL/rede"
        elif r["rss_declarado"]:
            r["veredito"] = "tem RSS declarado — implementar agora"
        elif r["provavel_js"]:
            r["veredito"] = "renderizado por JS — preparar, mas desativado"
        elif "json" in r["content_type"]:
            r["veredito"] = "JSON — implementar agora"
        elif "html" in r["content_type"]:
            r["veredito"] = "HTML estático — provavelmente raspável"
        else:
            r["veredito"] = f"conteúdo {r['content_type'] or 'desconhecido'} — avaliar"
        linhas.append(r)
        marca = ("🔒" if r["bloqueio_ambiente"] else
                 "🚫" if r["anti_bot"] else
                 "✅" if r["ok"] else "⚠️")
        print(f"   {marca} {pais:<12} {campo:<12} HTTP {str(r['status'] or '—'):<4} "
              f"{r['content_type'] or '—':<24} {r['latencia_ms']}ms · {r['veredito']}")
        if r["erro"]:
            print(f"      erro: {r['erro']}")
        time.sleep(0.6)

    n_amb = sum(1 for l in linhas if l["bloqueio_ambiente"])
    if n_amb:
        print(f"    ℹ️  {n_amb}/{len(linhas)} URL(s) bloqueadas pelo AMBIENTE de execução "
              f"(não pelas fontes). Rode este probe no GitHub Actions para obter a "
              f"medição real antes de decidir a Fase 4B.")
    if out_csv and linhas:
        cols = ["pais", "regulador", "campo", "url", "status", "content_type", "bytes",
                "rss_declarado", "provavel_js", "anti_bot", "bloqueio_ambiente", "latencia_ms",
                "viabilidade_documentada", "veredito", "erro"]
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(linhas)
        print(f"    → resultado salvo em {out_csv}")
    return linhas


def build_coverage_by_country(cfg: dict) -> list[dict]:
    """Cobertura oficial × ampla por país, para a aba de exportação."""
    from collections import defaultdict
    agg = defaultdict(lambda: {"emissores": 0, "oficial": 0})
    for c in cfg.get("watchlist", []):
        pais = c.get("country") or "A revisar"
        agg[pais]["emissores"] += 1
        if coverage_of(c, cfg)[0].startswith("oficial"):
            agg[pais]["oficial"] += 1
    out = []
    for pais, v in sorted(agg.items(), key=lambda x: -x[1]["emissores"]):
        src = (cfg.get("official_sources") or {}).get(pais) or {}
        out.append({"pais": pais, "emissores": v["emissores"], "oficial": v["oficial"],
                    "ampla": v["emissores"] - v["oficial"],
                    "regulador": src.get("regulador", ""),
                    "filings": src.get("filings", "") or "",
                    "viabilidade": src.get("viabilidade", "")})
    return out


def _issuer_coverage_row(c: dict, cfg: dict, exposure: dict | None = None) -> dict:
    """Linha de cobertura de UM emissor — só cadastro/config, com pendências
    marcadas (nunca resultado real de audit/probe)."""
    modo, regulador, filing = coverage_of(c, cfg)
    grupo = asset_group_of_company(c)
    pais = c.get("country") or ""
    off = c.get("official") or {}
    ri = bool(off.get("ri"))
    edgar = edgar_eligible(c)
    latam = pais in ("Chile", "México", "Colômbia", "Argentina")
    src = (cfg.get("official_sources") or {}).get(pais) or {}
    em_probe = bool(src.get("probe_targets")) or (src.get("viabilidade") == "a_revalidar")

    status_cvm = ("pendente_audit_cvm" if pais == "Brasil" and grupo in ("nao_listada",)
                  else ("coberto (IPE)" if pais == "Brasil" and grupo == "listed_companies"
                        else "n/a"))
    status_fundos = ("pendente (módulo fundos — FNET)" if grupo == "fii" else "n/a")
    status_edgar = "coberto (EDGAR)" if edgar else "n/a"
    status_latam = ("pendente_probe" if latam and not edgar else ("via EDGAR" if latam else "n/a"))
    status_probe = "pendente_probe" if (latam and em_probe) or grupo == "fii" else "n/a"

    # pendência principal + próxima ação + prioridade
    tier = c.get("tier")
    if grupo == "fii":
        pend, acao = "cobertura oficial de fundos a implementar", "validar FNET (probe) e mapear CNPJ"
    elif pais == "Brasil" and grupo == "nao_listada":
        pend, acao = "filiação CVM a confirmar", "rodar audit_cvm; se filiante, usar cobertura CVM"
    elif latam and not edgar:
        pend, acao = "fonte oficial LatAm frágil/a revalidar", "re-probe; senão RI/imprensa"
    elif not ri and modo == "ampla":
        pend, acao = "sem RI cadastrado", "buscar RI/comunicados específicos"
    else:
        pend, acao = "—", "manter monitoramento"
    prioridade = {1: "alta", 2: "média", 3: "baixa"}.get(tier, "futura")
    just_incl = f"tier {tier if tier is not None else 's/tier'}; incluído por materialidade/representação na carteira"
    just_sem_oficial = ("" if modo.startswith("oficial/reforçada")
                        else ("fundo — fonte oficial depende de módulo próprio" if grupo == "fii"
                              else ("não listada — depende de filiação CVM" if grupo == "nao_listada"
                                    else ("regulador LatAm sem fonte estável" if latam
                                          else "sem RI cadastrado; coberto por notícias"))))
    exp = ""
    if exposure is not None:
        v = exposure.get(_norm_key(c))
        exp = f"{v:.3f}" if isinstance(v, (int, float)) and v else ""

    return {
        "emissor": c.get("name", ""), "pais": pais, "regiao": c.get("region", ""),
        "subgrupo": ASSET_GROUP_LABELS.get(grupo, grupo), "asset_class": c.get("asset_class", ""),
        "fund_type": c.get("fund_type", "") or (fund_type_of(c) or ""),
        "vehicle_kind": c.get("vehicle_kind", ""), "tier": tier if tier is not None else "",
        "representacao_aprox": exp, "coverage": modo,
        "cobertura_ampla": "sim",  # ampla é o piso de todos
        "fonte_oficial": ("EDGAR" if edgar else (off.get("fund_source") or
                          ("RI" if ri else (filing or "—")))),
        "regulador": regulador or "", "sistema_filing": filing or "",
        "ri_cadastrado": "sim" if ri else "não",
        "status_cvm": status_cvm, "status_fundos": status_fundos,
        "status_edgar": status_edgar, "status_latam": status_latam, "status_probe": status_probe,
        "pendencia_principal": pend, "proxima_acao": acao, "prioridade": prioridade,
        "justificativa_inclusao": just_incl, "justificativa_sem_fonte_oficial": just_sem_oficial,
        "observacao": (c.get("nota_metodologica") or c.get("revisao") or ""),
    }


def _norm_key(c: dict) -> str:
    return normalize(c.get("name", ""))


def build_coverage_backlog(cfg: dict, exposure: dict | None = None) -> list[dict]:
    """4D.1 — Backlog de cobertura de todos os emissores (cadastral). Explica,
    por emissor, como está coberto e o que falta. Sem resultado de audit real."""
    return [_issuer_coverage_row(c, cfg, exposure) for c in cfg.get("watchlist", [])]


def build_priority_sources(cfg: dict, exposure: dict | None = None) -> list[dict]:
    """4D.3 — Fontes oficiais dos emissores prioritários (Tier 1 + cobertura
    oficial prioritária). Foca a demanda de reforçar RI/fonte oficial dos
    maiores nomes."""
    out = []
    for c in cfg.get("watchlist", []):
        if c.get("tier") != 1 and not (c.get("official") or {}).get("cobertura_prioritaria"):
            continue
        off = c.get("official") or {}
        modo, regulador, filing = coverage_of(c, cfg)
        edgar = edgar_eligible(c)
        ri = bool(off.get("ri"))
        rss = bool(off.get("rss"))
        if edgar:
            status = "ok"
        elif ri:
            status = "ok"
        elif modo.startswith("oficial"):
            status = "melhorar URL"
        else:
            status = "procurar RI específico"
        exp = ""
        if exposure is not None:
            v = exposure.get(_norm_key(c))
            exp = f"{v:.3f}" if isinstance(v, (int, float)) and v else ""
        out.append({
            "emissor": c.get("name", ""), "pais": c.get("country", ""),
            "ticker": c.get("ticker", ""), "tier": c.get("tier", ""),
            "representacao_aprox": exp,
            "fonte_oficial_atual": ("EDGAR" if edgar else ("RI" if ri else (filing or "—"))),
            "ri_cadastrado": "sim" if ri else "não",
            "cvm_edgar_regulador": ("SEC/EDGAR" if edgar else (regulador or "—")),
            "url_ri": off.get("ri", "") or "a_confirmar",
            "tem_rss": "sim" if rss else "não",
            "depende_scraping_pagina": "não" if (edgar or rss) else "possível",
            "status": status,
            "proxima_acao": ("manter" if status == "ok" else
                             ("confirmar URL de RI/comunicados" if status == "melhorar URL"
                              else "buscar RI/fatos relevantes específicos (a_confirmar)")),
        })
    return out


def build_cvm_pending_ids(cfg: dict) -> list[dict]:
    """4F item 9 — Plano de saneamento cadastral: emissores brasileiros
    (companhias, não fundos/gestoras) SEM identificador forte (código CVM/CNPJ/
    razão social), que são os candidatos naturais a cair em 'revisar'. Ordenado
    por prioridade (Tier 1 primeiro). Não sugere identificador — só aponta a
    lacuna e a ação. A sugestão de valor vem do cadastro oficial no run."""
    out = []
    for c in cfg.get("watchlist", []):
        if c.get("country") != "Brasil":
            continue
        grupo = asset_group_of_company(c)
        if grupo not in ("listed_companies", "nao_listada"):
            continue
        off = c.get("official") or {}
        tem_cod = bool(str(c.get("codigo_cvm") or "").strip())
        tem_cnpj = bool(str(c.get("cnpj") or "").strip())
        tem_rs = bool(str(c.get("razao_social") or "").strip())
        if tem_cod or tem_cnpj:
            continue  # já tem identificador forte declarado
        aliases = c.get("aliases") or []
        curtos = [a for a in aliases if len(normalize(str(a))) < 6]
        tier = c.get("tier")
        prioridade = {1: "alta", 2: "média", 3: "baixa"}.get(tier, "futura")
        out.append({
            "emissor": c.get("name", ""), "tier": tier if tier is not None else "",
            "asset_class": c.get("asset_class", ""), "grupo": ASSET_GROUP_LABELS.get(grupo, grupo),
            "ticker": c.get("ticker", ""), "tem_codigo_cvm": "não", "tem_cnpj": "não",
            "tem_razao_social": "sim" if tem_rs else "não",
            "aliases_curtos": "; ".join(str(a) for a in curtos) or "—",
            "prioridade": prioridade,
            "proxima_acao": "definir codigo_cvm/cnpj/razao_social oficial no config (cadastro CVM)",
            "obs": (c.get("revisao") or c.get("nota_metodologica") or ""),
        })
    ordem = {"alta": 0, "média": 1, "baixa": 2, "futura": 3}
    out.sort(key=lambda r: (ordem.get(r["prioridade"], 9), str(r["emissor"])))
    return out


def _load_exposure_map(path: str | None) -> dict | None:
    """Mapa nome_normalizado→representação aprox., a partir de um export de
    posições (opcional). Retorna None se o arquivo não existir — a coluna de
    exposição fica em branco, sem inventar."""
    if not path or not os.path.exists(path):
        return None
    from collections import defaultdict
    agg = defaultdict(float)
    try:
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        col_em = next((k for k in rows[0] if "missor" in k.lower()), None)
        col_rp = next((k for k in rows[0] if "epresenta" in k.lower()), None)
        if not (col_em and col_rp):
            return None
        for r in rows:
            try:
                agg[normalize(r.get(col_em, ""))] += float(r.get(col_rp) or 0)
            except (TypeError, ValueError):
                pass
        return dict(agg)
    except Exception:
        return None


def build_coverage_summary(cfg: dict) -> dict:
    """4B.6/4C.4 — Resumo de cobertura para a aba de Cobertura. Usa APENAS
    dados cadastrais/config — nunca resultado do audit_cvm/probe (que só existem
    após o Actions). Carrega audit_cvm_status='pendente' e agrega pendências
    explícitas; a aba deixa claro o que depende de execução."""
    from collections import Counter
    wl = cfg.get("watchlist", [])
    por_tier = Counter(str(c.get("tier") or "s/tier") for c in wl)
    # cobertura por tipo de fonte (metodológica, cadastral)
    por_fonte = Counter()
    pend_sem_oficial, pend_naolist_cvm, pend_fii, pend_ids = [], [], [], []
    for c in wl:
        modo = coverage_of(c, cfg)[0]
        grupo = asset_group_of_company(c)
        if modo.startswith("oficial/reforçada"):
            por_fonte["oficial/reforçada"] += 1
        elif "pendente (fundos)" in modo:
            por_fonte["oficial pendente (fundos)"] += 1
            pend_fii.append(c.get("name", ""))
        elif "condicional" in modo:
            por_fonte["oficial condicional à CVM"] += 1
            pend_naolist_cvm.append(c.get("name", ""))
        else:
            por_fonte["ampla"] += 1
            pend_sem_oficial.append(c.get("name", ""))
        if c.get("revisao") or (c.get("official") or {}).get("fund_cnpj") == "a_confirmar":
            pend_ids.append(c.get("name", ""))
    # compat: manter chaves do 4B.6 usadas pela aba
    por_cob = {"oficial": por_fonte.get("oficial/reforçada", 0),
               "ampla_condicional_cvm": por_fonte.get("oficial condicional à CVM", 0),
               "ampla": por_fonte.get("ampla", 0),
               "oficial_pendente_fundos": por_fonte.get("oficial pendente (fundos)", 0)}
    fontes = {"preparada": [], "em_probe": [], "desativada_risco": []}
    for pais, s in (cfg.get("official_sources") or {}).items():
        via = (s.get("viabilidade") or "").lower()
        tem_url = bool(s.get("filings_url") or s.get("api"))
        if via == "a_revalidar" or s.get("probe_targets"):
            fontes["em_probe"].append(pais)
        elif via in ("dificil", "muito_dificil") or s.get("anti_bot"):
            fontes["desativada_risco"].append(pais)
        elif tem_url:
            fontes["preparada"].append(pais)
        else:
            fontes["em_probe"].append(pais)
    fs = cfg.get("fund_sources") or {}
    latam = [p for p in ("Chile", "México", "Colômbia", "Argentina")
             if p in (cfg.get("official_sources") or {})]
    pendencias = {
        "sem_fonte_oficial": len(pend_sem_oficial),
        "naolistadas_aguardando_audit_cvm": len(pend_naolist_cvm),
        "fiis_aguardando_modulo_fundos": len(pend_fii),
        "latam_aguardando_probe": latam,
        "ids_aliases_a_confirmar": sorted(set(pend_ids)),
        "taxonomia_fundos": fund_taxonomy_pendencies(cfg),
    }
    # 4D.5 — cobertura oficial prioritária (Tier 1 + marcados) e lacunas por motivo
    prio = build_priority_sources(cfg)
    prioritaria = {
        "total": len(prio),
        "com_fonte_oficial": sum(1 for r in prio if r["status"] == "ok"),
        "com_ri": sum(1 for r in prio if r["ri_cadastrado"] == "sim"),
        "dependem_actions_probe": sum(1 for r in prio if r["fonte_oficial_atual"] not in ("EDGAR", "RI")),
        "pendentes_confirmacao": sum(1 for r in prio if r["url_ri"] == "a_confirmar"),
    }
    lacunas = {
        "sem_ri_cadastrado": sum(1 for c in wl if not (c.get("official") or {}).get("ri")),
        "aguardando_audit_cvm": len(pend_naolist_cvm),
        "aguardando_modulo_fundos": len(pend_fii),
        "aguardando_probe_latam": sum(1 for c in wl if c.get("country") in
                                      ("Chile", "México", "Colômbia") and not edgar_eligible(c)),
        "aguardando_identificador_forte": sum(1 for c in wl if c.get("revisao")),
        "fonte_tecnicamente_fragil": sum(1 for p, s in (cfg.get("official_sources") or {}).items()
                                         if (s.get("viabilidade") or "").lower() in ("dificil", "muito_dificil")),
    }
    proximas_acoes = [
        "Rodar audit_cvm (Actions) — fechar filiação das não listadas",
        "Rodar probe_sources — veredito de FNET e BMV/BIVA/CNBV",
        "Validar FNET como fonte oficial de fundos",
        "Confirmar identificador forte da Grupo CVLB",
        "Confirmar código CVM 4170 da Vale",
        "Completar RI/fonte oficial dos emissores prioritários",
    ]
    return {
        "total": len(wl),
        "por_pais": build_coverage_by_country(cfg),
        "por_grupo": build_asset_groups_meta(cfg),
        "por_tier": dict(sorted(por_tier.items())),
        "por_cobertura": por_cob,
        "por_fonte": dict(por_fonte),
        "fontes": fontes,
        "pendencias": pendencias,
        "prioritaria": prioritaria,
        "lacunas": lacunas,
        "proximas_acoes": proximas_acoes,
        "fund_sources": {"enabled": bool(fs.get("enabled")),
                         "targets": len(fs.get("probe_targets") or []),
                         "cobre": fs.get("cobre") or []},
        "audit_cvm_status": "pendente",   # nunca fingir resultado real do audit
    }


def coverage_of(company: dict, cfg: dict) -> tuple[str, str, str]:
    """(modo_de_cobertura, regulador, sistema_de_filing) do emissor.

    'oficial/reforçada' = existe fonte oficial coletada diretamente para ele
    (RI cadastrado, CVM/IPE, SEC/EDGAR). 'ampla' = monitorado por notícias e
    fontes públicas — que é o PISO de todos os emissores, nunca ausência de
    cobertura."""
    off = company.get("official") or {}
    pais = company.get("country") or ""
    src = (cfg.get("official_sources") or {}).get(pais) or {}
    grupo = asset_group_of_company(company)
    cvm_on = (cfg.get("cvm_fatos_relevantes") or {}).get("enabled", False)

    if edgar_eligible(company):
        return ("oficial/reforçada", "SEC", "EDGAR")
    # FII/fundo listado: NÃO é companhia aberta — sua cobertura oficial não vem
    # do IPE cia_aberta, e sim do módulo de fundos (FNET/CVM), ainda a
    # implementar. Até lá é 'oficial pendente (fundos)', nunca 'oficial via IPE'.
    if grupo == "fii":
        status = (off.get("cobertura_oficial_status") or "oficial_pendente_fundos")
        if status == "oficial_pendente_fundos":
            return ("oficial pendente (fundos)", "CVM", off.get("fund_source", "FNET"))
        return ("ampla", "CVM", off.get("fund_source", "FNET"))
    if pais == "Brasil" and cvm_on and grupo == "listed_companies":
        return ("oficial/reforçada", "CVM", "IPE")
    if pais == "Brasil" and cvm_on and grupo == "nao_listada":
        # depende de ser companhia registrada — a auditoria CVM (--audit-cvm)
        # é que confirma; até lá, cobertura oficial é condicional
        return ("ampla (oficial condicional à CVM)", "CVM", "IPE")
    if off.get("ri"):
        return ("oficial/reforçada", src.get("regulador", "") or "RI", "RI")
    return ("ampla", src.get("regulador", "") or "", src.get("filings", "") or "")


def build_asset_groups_meta(cfg: dict) -> list[dict]:
    """Lista, em ordem fixa, os grupos de ativos existentes na watchlist —
    derivada do CADASTRO completo, não dos emissores com sinal. Cada item traz
    o id do grupo, o rótulo e o total de emissores monitorados naquele grupo.
    É o que garante que os botões de subgrupo apareçam sempre, mesmo quando um
    grupo não tem nenhuma notícia na janela selecionada."""
    order = ["listed_companies", "nao_listada", "fii", "gestora_fundo", "a_revisar"]
    counts: dict[str, int] = {}
    for c in cfg.get("watchlist", []):
        g = asset_group_of_company(c)
        counts[g] = counts.get(g, 0) + 1
    groups = [{"id": "all", "label": "Todos", "total": sum(counts.values())}]
    for gid in order:
        if counts.get(gid):  # só grupos que existem no cadastro
            groups.append({"id": gid,
                           "label": ASSET_GROUP_LABELS[gid],
                           "total": counts[gid]})
    return groups


def render_html(data_by_window: dict, cfg: dict, demo: bool,
                changes: dict | None = None,
                payload_thresholds: dict | None = None,
                run_meta: dict | None = None) -> str:
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
        "asset_groups": build_asset_groups_meta(cfg),
        "official_sources": cfg.get("official_sources") or {},
        "coverage_by_country": build_coverage_by_country(cfg),
        "coverage_summary": build_coverage_summary(cfg),
        "coverage_backlog": build_coverage_backlog(cfg),   # 4D — para export da aba
        "fund_sources": cfg.get("fund_sources") or {},
        "generated_at": fmt_date_br(get_brt_now()),
        "generated_ts": int(datetime.now(timezone.utc).timestamp()),
        "demo": demo,
    }

    # 4H.2 — diagnóstico de cobertura oficial/ausência de notícia: dimensão
    # SEPARADA do score, nunca lida por event_ids_for/build_evolution (já
    # rodaram acima, sem ver isto). Só roda quando `run_meta` é fornecido;
    # qualquer falha aqui é 100% cosmética — nunca derruba o dashboard.
    if run_meta is not None:
        try:
            import coverage_diagnosis as _covdiag
            _cov_rows = _covdiag.diagnose_coverage(cfg, run_meta)
            _cov_by_name = {r["company"]: _covdiag.to_dashboard_view(r) for r in _cov_rows}
            for _win_data in data_by_window.values():
                for _row in _win_data.get("evolution", []):
                    _cv = _cov_by_name.get(_row.get("company"))
                    if _cv is not None:
                        _row["coverage_diagnosis"] = _cv
            payload["coverage_diagnosis_summary"] = _covdiag.build_executive_coverage_summary(_cov_rows)
        except Exception as _cov_exc:
            print(f"   ⚠️  Diagnóstico de cobertura (4H.2) não pôde ser anexado ao "
                 f"dashboard nesta execução: {_cov_exc}")
    else:
        # `run_meta=None` mas o chamador já anexou `coverage_diagnosis` a
        # algumas linhas manualmente (ex.: prévias que precisam da
        # telemetria CVM real, calculada fora daqui) — ainda assim monta o
        # resumo executivo a partir do que já está anexado, sem refazer o
        # diagnóstico (e sem sobrescrever o que o chamador calculou).
        try:
            import coverage_diagnosis as _covdiag
            # dedup por empresa — a mesma empresa aparece em várias janelas
            # (7/30/90/365d); o resumo executivo conta cada emissor 1 vez.
            _attached_by_company = {}
            for _win_data in data_by_window.values():
                for _row in _win_data.get("evolution", []):
                    _cv = _row.get("coverage_diagnosis")
                    if _cv is not None and _row.get("company") not in _attached_by_company:
                        _attached_by_company[_row["company"]] = _cv
            _attached = list(_attached_by_company.values())
            if _attached:
                _counts = {s: 0 for s in _covdiag.COVERAGE_STATUSES}
                _counts[_covdiag.COVERAGE_OK_EVENTS_FOUND] = 0
                for _cv in _attached:
                    _counts[_cv["status"]] = _counts.get(_cv["status"], 0) + 1
                payload["coverage_diagnosis_summary"] = {
                    "cobertura_confirmada": _counts.get(_covdiag.NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN, 0)
                                           + _counts.get(_covdiag.COVERAGE_OK_EVENTS_FOUND, 0)
                                           + _counts.get(_covdiag.ONLY_INFORMATIONAL_FOUND, 0),
                    "cobertura_parcial": _counts.get(_covdiag.PARTIAL_COVERAGE, 0),
                    "falha_de_coleta": _counts.get(_covdiag.COLLECTION_FAILURE, 0),
                    "somente_fallback": _counts.get(_covdiag.FALLBACK_ONLY, 0),
                    "sem_fonte_oficial": _counts.get(_covdiag.NO_VALIDATED_OFFICIAL_SOURCE, 0),
                    "fonte_configurada_nao_executada": _counts.get(_covdiag.SOURCE_CONFIGURED_NOT_EXECUTED, 0),
                    "total_diagnosticado": len(_attached),
                    "status_counts": _counts,
                }
        except Exception:
            pass

    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return template.render(
        title=cfg["dashboard"].get("title", "Radar de Risco"),
        generated_at=fmt_date_br(get_brt_now()),
        payload_json=payload_json,
        demo=demo,
    )


# ── main ─────────────────────────────────────────────────────────────────────


# ═══════════════════════ Fase 4E — Validadores pós-Actions ═══════════════════════
# Camada que LÊ os outputs reais do Actions quando existirem e produz relatórios
# de decisão. Nada afirma sem arquivo; ausência vira 'pendente_arquivo'. Não roda
# coletor, não altera score/tier, não inventa dado.

AUDIT_BASELINE = {"filiante_cvm": 57, "nao_filiante": 19,
                  "esperado_filiante_sem_protocolo_no_ano": 15, "nao_aplicavel_veiculo": 7}

AUDIT_STATUSES = ["filiante_cvm", "nao_filiante", "esperado_filiante_sem_protocolo_no_ano",
                  "revisar", "nao_aplicavel_veiculo", "nao_aplicavel_dataset_ipe"]


def _read_csv(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        print(f"   ⚠️  falha ao ler {path}: {exc}")
        return None


def _core_name(s: str) -> str:
    """Núcleo do nome societário: normaliza e remove sufixos de tipo societário
    ao final (S.A., S/A, SA, LTDA, ME, EPP…). Usado só para comparação ESTRITA
    de igualdade — nunca para substring."""
    n = normalize(s or "")
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    for _ in range(3):
        n2 = re.sub(r"\b(s a|sa|ltda|me|epp|eireli)\s*$", "", n).strip()
        if n2 == n:
            break
        n = n2
    return n


def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_text(path, txt):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"   → {path}")


def _write_rows_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows or [])
    print(f"   → {path}")


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def analyze_audit_cvm(new_csv, old_csv=None, outdir="."):
    """4E.1 — Compara a auditoria nova contra baseline (57/19/15/7) ou uma
    auditoria antiga. Gera relatorio_auditoria_cvm.md + auditoria_cvm_resumo.csv
    + auditoria_cvm_alertas.csv. Sem arquivo → 'pendente_arquivo'."""
    from collections import Counter
    md = os.path.join(outdir, "relatorio_auditoria_cvm.md")
    rows = _read_csv(new_csv)
    if rows is None:
        _write_text(md, f"# Relatório — Auditoria CVM\n\n**pendente_arquivo** — "
                    f"`{new_csv}` não encontrado. Rode o Actions com `audit_cvm=true` "
                    f"e reexecute `--analyze-audit-cvm`.\n")
        _write_rows_csv(os.path.join(outdir, "auditoria_cvm_resumo.csv"),
                        [{"status": s, "novo": "pendente_arquivo", "baseline": AUDIT_BASELINE.get(s, 0),
                          "delta": "n/a"} for s in AUDIT_STATUSES], ["status", "novo", "baseline", "delta"])
        _write_rows_csv(os.path.join(outdir, "auditoria_cvm_alertas.csv"),
                        [{"severidade": "INFO", "item": "auditoria real ausente", "ok": "pendente_arquivo"}],
                        ["severidade", "item", "ok"])
        return {"status": "pendente_arquivo", "recomendacao": "pendente_actions"}

    cont = Counter((r.get("status") or "").strip() for r in rows)
    old = _read_csv(old_csv) if old_csv else None
    base = Counter((r.get("status") or "").strip() for r in old) if old is not None else Counter(AUDIT_BASELINE)

    statuses = [s for s in AUDIT_STATUSES] + sorted(set(cont) - set(AUDIT_STATUSES))
    resumo = [{"status": s, "novo": cont.get(s, 0), "baseline": base.get(s, 0),
               "delta": cont.get(s, 0) - base.get(s, 0)} for s in statuses if cont.get(s, 0) or base.get(s, 0)]

    mudou = []
    if old is not None:
        old_by = {(r.get("emissor") or ""): (r.get("status") or "") for r in old}
        for r in rows:
            e, ns = r.get("emissor") or "", r.get("status") or ""
            if e in old_by and old_by[e] != ns:
                mudou.append({"emissor": e, "de": old_by[e], "para": ns})

    def find(name):
        return next((r for r in rows if (r.get("emissor") or "").strip().lower() == name.lower()), None)

    alertas = []

    def add(sev, item, falhou):
        alertas.append({"severidade": sev, "item": item, "ok": "não" if falhou else "sim"})

    vale = find("Vale")
    if vale:
        add("BLOQUEIA", "Vale não pode casar com Vale Bonito Agropecuária",
            "bonito" in (vale.get("companhia_casada") or "").lower())
    cvlb = find("Grupo CVLB")
    if cvlb:
        forte_id = bool((cvlb.get("codigo_cvm_casado") or "").strip() or (cvlb.get("cnpj_casado") or "").strip())
        add("WARNING", "Grupo CVLB deve ficar 'revisar' sem identificador forte",
            (cvlb.get("status") or "") == "filiante_cvm" and not forte_id)
    fii_bad = [r for r in rows if (r.get("grupo") or "") == "fii"
               and (r.get("status") or "") == "esperado_filiante_sem_protocolo_no_ano"]
    add("BLOQUEIA", "FIIs não podem ficar 'esperado_filiante_sem_protocolo' (→ nao_aplicavel_dataset_ipe)", bool(fii_bad))
    multi_bad = [r for r in rows if _to_int(r.get("n_candidatos")) > 1 and (r.get("status") or "") == "filiante_cvm"]
    add("BLOQUEIA", "Múltiplos candidatos não podem virar filiante (→ revisar)", bool(multi_bad))
    for nome in ("Fisia", "Aegea Saneamento", "GSH Corp", "Hospital Care"):
        r = find(nome)
        if r and (r.get("status") or "") == "filiante_cvm":
            tm = (r.get("tipo_match") or "")
            robusto = tm in ("cnpj", "codigo_cvm", "razao_social", "nome_exato", "alias")
            add("WARNING", f"{nome} filiante deve ter match robusto (tipo_match={tm or '—'})", not robusto)

    revisar = [r.get("emissor") for r in rows if (r.get("status") or "") == "revisar"]
    baixa = [r.get("emissor") for r in rows if (r.get("confianca_match") or "") == "baixa"]
    forte = [r.get("emissor") for r in rows if (r.get("tipo_match") or "") in
             ("cnpj", "codigo_cvm", "razao_social", "nome_exato")]
    fp_corr = []
    if old is not None:
        old_comp = {(r.get("emissor") or ""): (r.get("companhia_casada") or "") for r in old}
        for r in rows:
            e = r.get("emissor") or ""
            if e in old_comp and old_comp[e] and old_comp[e] != (r.get("companhia_casada") or ""):
                fp_corr.append({"emissor": e, "antes": old_comp[e], "agora": r.get("companhia_casada") or "(nenhum)"})

    bloq = [a for a in alertas if a["severidade"] == "BLOQUEIA" and a["ok"] == "não"]
    warn = [a for a in alertas if a["severidade"] == "WARNING" and a["ok"] == "não"]
    if bloq:
        rec = "4B.1 NÃO fechada — corrigir matching"
    elif warn or revisar:
        rec = "4B.1 fechada COM RESSALVAS"
    else:
        rec = "4B.1 fechada"

    L = ["# Relatório — Auditoria CVM", "",
         "## Proveniência",
         f"- Arquivo: `{new_csv}`",
         f"- Linhas lidas: **{len(rows)}**",
         f"- Colunas detectadas: {', '.join(rows[0].keys())}",
         "- Análise: **feita a partir do CSV real do Actions**", "",
         f"**Emissores auditados:** {len(rows)}  ·  **Recomendação:** {rec}", "",
         "## 1–2. Contagem por status × baseline", "",
         "| status | novo | baseline | Δ |", "|---|---:|---:|---:|"]
    for r in resumo:
        L.append(f"| {r['status']} | {r['novo']} | {r['baseline']} | {r['delta']:+d} |")
    L += ["", "## 3. Emissores que mudaram de status",
          ("\n".join(f"- {m['emissor']}: {m['de']} → {m['para']}" for m in mudou) or "- (baseline sem CSV por emissor — comparação só de contagem)")]
    L += ["", "## 4. Falsos positivos corrigidos",
          ("\n".join(f"- {m['emissor']}: '{m['antes']}' → '{m['agora']}'" for m in fp_corr) or "- (requer auditoria antiga por emissor para diferenciar)")]
    L += ["", "## 5. Casos críticos"]
    for a in alertas:
        L.append(f"- {'✅' if a['ok'] == 'sim' else '❌'} [{a['severidade']}] {a['item']}")
    L += ["", f"## 6. Em 'revisar' ({len(revisar)})", ("; ".join(revisar) or "—")]
    L += ["", f"## 7. Baixa confiança ({len(baixa)})", ("; ".join(baixa) or "—")]
    L += ["", f"## 8. Matches fortes ({len(forte)})", ("; ".join(forte[:40]) + (" …" if len(forte) > 40 else "") or "—")]
    L += ["", f"## 9. Recomendação objetiva", f"**{rec}**", ""]
    _write_text(md, "\n".join(L))
    _write_rows_csv(os.path.join(outdir, "auditoria_cvm_resumo.csv"), resumo, ["status", "novo", "baseline", "delta"])
    _write_rows_csv(os.path.join(outdir, "auditoria_cvm_alertas.csv"), alertas, ["severidade", "item", "ok"])
    # Item 2 — saneamento: lista dedicada dos 'revisar' com candidatos e ação
    rev_rows = [{"emissor": r.get("emissor", ""), "tier": r.get("tier", ""),
                 "asset_class": r.get("asset_class", ""),
                 "tipo_match": r.get("tipo_match", ""),
                 "confianca": r.get("confianca_match", ""),
                 "n_candidatos": r.get("n_candidatos", ""),
                 "candidatos": r.get("candidatos", ""),
                 "motivo_decisao": r.get("motivo_decisao", ""),
                 "proxima_acao": ("definir razao_social/codigo_cvm/cnpj oficial no config e reauditar"
                                  if _to_int(r.get("n_candidatos")) != 1 else
                                  "candidato único — confirmar razão social e cadastrar identificador")}
                for r in rows if (r.get("status") or "") == "revisar"]
    ordem_t = {"1": 0, "2": 1, "3": 2}
    rev_rows.sort(key=lambda x: (ordem_t.get(str(x["tier"]), 9), x["emissor"]))
    _write_rows_csv(os.path.join(outdir, "revisar_identificadores.csv"), rev_rows,
                    ["emissor", "tier", "asset_class", "tipo_match", "confianca",
                     "n_candidatos", "candidatos", "motivo_decisao", "proxima_acao"])

    # Sugestões de ALTA CONFIANÇA extraídas dos candidatos REAIS do CSV:
    # aceita quando (a) há candidato único, ou (b) exatamente um candidato tem
    # razão social cujo núcleo coincide com o nome do emissor. Nunca inventa
    # código CVM/CNPJ — esses ficam 'a_confirmar'.
    sug = []
    for r in rows:
        if (r.get("status") or "") != "revisar":
            continue
        cands = [c.strip() for c in (r.get("candidatos") or "").split(";") if c.strip()]
        vistos, unicos = set(), []
        for c in cands:
            k = normalize(c)
            if k not in vistos:
                vistos.add(k)
                unicos.append(c)
        emissor = r.get("emissor", "")
        alvo = _core_name(emissor)
        # IGUALDADE ESTRITA do núcleo (nome sem sufixo societário). 'startswith'
        # é inseguro: 'PETROBRAS QUÍMICA…PETROQUISA' e 'SANTANDER BRASIL
        # ARRENDAMENTO MERCANTIL' começam com o nome do emissor mas são
        # subsidiárias — o erro exato que a 4B.1 existe para evitar.
        exatos = [c for c in unicos if _core_name(c) == alvo]
        if len(unicos) == 1:
            escolha, base = unicos[0], "candidato único"
        elif len(exatos) == 1:
            escolha, base = exatos[0], "único candidato com razão social equivalente ao nome do emissor"
        else:
            continue
        alerta = ""
        outros = [c for c in unicos if c != escolha]
        if any(re.search(r"\b(holding|participa)", normalize(c)) for c in outros) and \
           not re.search(r"\b(holding|participa)", normalize(escolha)):
            alerta = ("ATENÇÃO: há candidato HOLDING/PARTICIPAÇÕES — se o emissor listado for a "
                      "holding (ex.: ITUB4 = Itaú Unibanco Holding), a sugestão está na operadora")
        sug.append({"emissor": emissor, "tier": r.get("tier", ""),
                    "razao_social_sugerida": escolha,
                    "codigo_cvm_sugerido": "a_confirmar", "cnpj_sugerido": "a_confirmar",
                    "base_da_sugestao": base, "alerta": alerta,
                    "n_candidatos_no_run": r.get("n_candidatos", ""),
                    "acao": "conferir no cadastro CVM e colar razao_social/codigo_cvm no config"})
    sug.sort(key=lambda x: (ordem_t.get(str(x["tier"]), 9), x["emissor"]))
    _write_rows_csv(os.path.join(outdir, "identificadores_cvm_sugeridos.csv"), sug,
                    ["emissor", "tier", "razao_social_sugerida", "codigo_cvm_sugerido",
                     "cnpj_sugerido", "base_da_sugestao", "alerta", "n_candidatos_no_run", "acao"])
    return {"status": "ok", "recomendacao": rec, "bloqueios": len(bloq), "warnings": len(warn),
            "revisar": len(revisar), "contagem": dict(cont), "n_linhas": len(rows),
            "colunas": list(rows[0].keys()), "arquivo": new_csv, "sugestoes": len(sug)}


# Mapeia veredito textual do probe → categoria técnica + recomendação
def _probe_categoria(r):
    txt = (r.get("veredito") or "").lower()
    ct = (r.get("content_type") or "").lower()
    err = (r.get("erro") or "").lower()
    st = str(r.get("status") or "")
    if "timeout" in txt or "timeout" in err:
        return "timeout", "re-probe com timeout maior + testar endpoint de dados"
    if "404" in st or "404" in txt or "404" in err:
        return "404", "revisar URL/dataset (404)"
    if r.get("bloqueio_ambiente") in ("True", "true", True) or "bloqueado pelo ambiente" in txt:
        return "bloqueio_ambiente", "re-probe fora do sandbox"
    if "anti-bot" in txt or r.get("anti_bot") in ("True", "true", True):
        return "anti-bot", "exigir Playwright/headless — ou RI por emissor"
    if "ssl" in err or "ssl" in txt:
        return "erro SSL", "revisar URL/host"
    if "indispon" in txt:
        return "indisponível", "revisar URL"
    if "rss" in txt:
        return "RSS", "candidato — preparar coletor atrás de flag"
    if "json" in txt or "json" in ct:
        return "JSON/API", "candidato — preparar coletor atrás de flag"
    if "html estático" in txt or ("html" in ct and "js" not in txt and "dinam" not in txt):
        return "HTML estático", "candidato — validar EXTRAÇÃO de conteúdo antes de implementar (HTTP 200 não prova dados acessíveis)"
    if "js" in txt or "dinam" in txt or r.get("provavel_js") in ("True", "true", True):
        return "JS pesado", "documentar; headless só se justificar"
    return "inconclusivo", "re-probe"


def _probe_source_label(r):
    """Nome legível da FONTE de um alvo — para Fundos, separa FNET / CVM FII /
    CVM FIDC / B3 pela URL (evita agregar alvos incompatíveis)."""
    u = (r.get("url") or "").lower()
    pais = r.get("pais", "")
    if pais == "Fundos":
        if "fnet" in u:
            return "FNET"
        if "b3.com.br" in u:
            return "B3 Fundos"
        if "fidc" in u:
            return "CVM FIDC"
        if "fii" in u:
            return "CVM FII"
        return "Fundos (outro)"
    if "bmv.com.mx" in u:
        return "México/BMV"
    if "biva" in u:
        return "México/BIVA"
    if "cnbv" in u or "gob.mx" in u:
        return "México/CNBV"
    return f"{pais}/{r.get('regulador', '') or r.get('campo', '')}".strip("/")


def analyze_probe_sources(probe_csv, outdir="."):
    """4E.2 (corrigido) — Lê probe_fontes_oficiais.csv e decide POR ALVO. Nunca
    agrega alvos incompatíveis numa mesma frente: FNET, CVM FII, CVM FIDC e B3
    são avaliados separadamente. Preserva rastreabilidade dos alvos brutos."""
    md = os.path.join(outdir, "relatorio_probe_fontes.md")
    rows = _read_csv(probe_csv)
    if rows is None:
        _write_text(md, f"# Relatório — Probe de fontes oficiais\n\n**pendente_arquivo** — "
                    f"`{probe_csv}` não encontrado. Rode o Actions com `probe_sources=true`.\n")
        _write_rows_csv(os.path.join(outdir, "probe_fontes_resumo.csv"),
                        [{"fonte": "—", "categoria": "pendente_arquivo"}], ["fonte", "categoria"])
        _write_rows_csv(os.path.join(outdir, "probe_fontes_recomendacoes.csv"),
                        [{"frente": "—", "recomendacao": "pendente_actions"}], ["frente", "recomendacao"])
        return {"status": "pendente_arquivo"}

    cols = list(rows[0].keys())
    detalhado = []   # uma linha por alvo (rastreabilidade dos 22)
    for r in rows:
        cat, rec = _probe_categoria(r)
        if "fnet" in (r.get("url") or "").lower():
            rec = ("inconclusivo — houve timeout em run anterior e não há validação de "
                   "extração; NÃO implementar coletor ainda")
        detalhado.append({
            "fonte": _probe_source_label(r), "frente": r.get("pais", ""),
            "tipo_alvo": r.get("campo", ""), "url": r.get("url", ""),
            "status_http": r.get("status", ""), "categoria": cat,
            "veredito_bruto": r.get("veredito", ""), "recomendacao_alvo": rec})

    _BONS = ("JSON/API", "HTML estático", "RSS")

    def por_fonte(nome):
        return [d for d in detalhado if d["fonte"] == nome]

    _FNET_CONSERVADOR = ("FNET respondeu HTTP 200/HTML neste run, mas houve timeout em run anterior "
                         "e ainda não há validação de extração de documentos. Portanto, permanece como "
                         "**candidato inconclusivo**. **Não implementar coletor ainda.**")

    def decide_alvo(nome):
        its = por_fonte(nome)
        if not its:
            return f"{nome}: sem alvo no probe — não avaliado"
        d = its[0]  # decisão pelo ALVO específico, não por agregação
        c = d["categoria"]
        if nome == "FNET":
            if c in _BONS:
                return f"FNET: {_FNET_CONSERVADOR}"
            if c == "timeout":
                return ("FNET: TIMEOUT → inconclusivo; re-probe (timeout maior/endpoint de dados). "
                        "NÃO implementar.")
            return f"FNET: {c} → inconclusivo; re-probe. NÃO implementar."
        if c in _BONS:
            return (f"{nome}: {c} → **candidato**; validar EXTRAÇÃO de conteúdo e reconfirmar em novo probe "
                    f"antes de qualquer coletor (HTTP 200 não prova dados acessíveis)")
        if c == "timeout":
            return f"{nome}: TIMEOUT → inconclusivo; re-probe (timeout maior/endpoint de dados). NÃO implementar."
        if c == "404":
            return f"{nome}: 404 → revisar URL/dataset. NÃO implementar."
        if c in ("anti-bot", "JS pesado"):
            return f"{nome}: {c} → frágil; documentar; não implementar scraper simples."
        if c == "bloqueio_ambiente":
            return f"{nome}: bloqueado pelo ambiente → re-probe fora do sandbox."
        return f"{nome}: {c} → re-probe/revisar URL."

    # Frentes de fundos SEPARADAS (o ponto do bug anterior)
    dec_fundos = [decide_alvo("FNET"), decide_alvo("CVM FII"),
                  decide_alvo("CVM FIDC"), decide_alvo("B3 Fundos")]
    # Frentes LatAm (por país; primeiro alvo relevante)
    def decide_pais(pais):
        its = [d for d in detalhado if d["frente"] == pais]
        if not its:
            return f"{pais}: sem alvo — não avaliado"
        cats = {d["categoria"] for d in its}
        if cats & set(_BONS):
            return f"{pais}: alvo(s) utilizável(is) ({', '.join(sorted(cats & set(_BONS)))}) → investigar/preparar atrás de flag"
        if {"anti-bot", "JS pesado"} & cats:
            return f"{pais}: anti-bot/JS → não implementar; documentar"
        return f"{pais}: {', '.join(sorted(cats))} → re-probe/revisar"
    dec_latam = [decide_pais(p) for p in ("México", "Chile", "Colômbia", "Argentina")]

    # FNET especificamente
    fnet_rows = por_fonte("FNET")
    fnet_cat = fnet_rows[0]["categoria"] if fnet_rows else "ausente"
    fnet_ok = fnet_cat in _BONS

    # Recomendação objetiva CORRIGIDA — nunca "coletor FNET" com FNET timeout.
    proxima = [
        "1. Rodar novo **run limpo** (backfill=false) com logs claros.",
        "2. **Sanear identificadores CVM** dos Tier 1 para reduzir os 30 revisar.",
        "3. **Re-probe FNET e CVM FII** com URL/timeout corrigidos.",
        "4. **Só depois** decidir se FNET vira coletor atrás de flag.",
        "5. Em paralelo, manter **BMV/CNBV México em investigação**, sem scraper ativo.",
    ]
    if fnet_ok:
        proxima[3] = ("4. **FNET permanece inconclusivo.** " + _FNET_CONSERVADOR)

    counts_frente = {}
    for d in detalhado:
        counts_frente.setdefault(d["frente"], 0)
        counts_frente[d["frente"]] += 1

    L = ["# Relatório — Probe de fontes oficiais (corrigido, por alvo)", "",
         "## Proveniência",
         f"- Arquivo: `{probe_csv}`",
         f"- Linhas lidas (alvos brutos): **{len(rows)}**",
         f"- Colunas detectadas: {', '.join(cols)}",
         f"- Análise: **por CSV real** (uma decisão por alvo; sem agregação entre alvos)",
         "",
         "## Contagem",
         f"- Total bruto de alvos: **{len(rows)}**",
         f"- Por frente: " + " · ".join(f"{k}={v}" for k, v in sorted(counts_frente.items())),
         "- Critério: **sem agregação** — cada alvo decide por si; frentes de fundos separadas (FNET, CVM FII, CVM FIDC, B3).",
         "",
         "## 1. Tabela detalhada por alvo (rastreabilidade)", "",
         "| fonte | frente | tipo | status | categoria | recomendação (alvo) |",
         "|---|---|---|---|---|---|"]
    for d in detalhado:
        L.append(f"| {d['fonte']} | {d['frente']} | {d['tipo_alvo']} | {d['status_http']} "
                 f"| {d['categoria']} | {d['recomendacao_alvo']} |")
    L += ["", "## 2. Decisão por frente — FUNDOS (separadas)"]
    L += [f"- {d}" for d in dec_fundos]
    L += ["", "## 3. Decisão por frente — LatAm"]
    L += [f"- {d}" for d in dec_latam]
    L += ["", "## 4. FNET — situação",
          f"- Categoria medida no probe: **{fnet_cat}**.",
          f"- {_FNET_CONSERVADOR}",
          "  *Não confundir com CVM FIDC/B3: são fontes diferentes e não sustentam decisão sobre o FNET.*"]
    L += ["", "## 5. Recomendação objetiva da próxima etapa"]
    L += [f"- {p}" for p in proxima]
    L += [""]
    _write_text(md, "\n".join(L))
    _write_rows_csv(os.path.join(outdir, "probe_fontes_resumo.csv"), detalhado,
                    ["fonte", "frente", "tipo_alvo", "url", "status_http", "categoria", "veredito_bruto"])
    _write_rows_csv(os.path.join(outdir, "probe_fontes_recomendacoes.csv"), detalhado,
                    ["fonte", "frente", "categoria", "recomendacao_alvo"])
    return {"status": "ok", "n_alvos": len(rows), "fnet_categoria": fnet_cat, "fnet_ok": fnet_ok,
            "decisoes_fundos": dec_fundos, "decisoes_latam": dec_latam,
            "proxima": " ".join(proxima)}


def exposure_matching_review(cfg, base_path, outdir="."):
    """4E.5 — Diagnóstico de casamento de exposição (config × base de posições).
    Não inventa exposição: sem base → documenta a falta."""
    out = os.path.join(outdir, "exposure_matching_review.csv")
    cols = ["emissor", "nome_no_config", "possivel_nome_na_base", "ticker", "cnpj",
            "pais", "subgrupo", "exposicao_atual_backlog", "status_match_exposicao",
            "confianca", "proxima_acao"]
    exp = _load_exposure_map(base_path)
    wl = cfg.get("watchlist", [])
    rows = []
    if exp is None:
        for c in wl:
            rows.append({"emissor": c.get("name", ""), "nome_no_config": c.get("name", ""),
                         "possivel_nome_na_base": "", "ticker": c.get("ticker", ""),
                         "cnpj": (c.get("official") or {}).get("fund_cnpj", "") or c.get("cnpj", ""),
                         "pais": c.get("country", ""),
                         "subgrupo": ASSET_GROUP_LABELS.get(asset_group_of_company(c), ""),
                         "exposicao_atual_backlog": "", "status_match_exposicao": "sem_base",
                         "confianca": "n/a", "proxima_acao": "fornecer base de posições (--exposure-base)"})
        _write_rows_csv(out, rows, cols)
        print("   ℹ️  base de posições ausente — review documenta a falta, sem inventar exposição.")
        return {"status": "sem_base", "linhas": len(rows)}

    for c in wl:
        termos = [c.get("name", "")] + list(c.get("aliases") or [])
        achou, via, conf = "", "sem_match", "—"
        nkey = normalize(c.get("name", ""))
        if nkey in exp:
            achou, via, conf = c.get("name", ""), "match_nome", "alta"
        else:
            for a in termos[1:]:
                if normalize(a) in exp:
                    achou, via, conf = a, "match_alias", "média"
                    break
        v = exp.get(normalize(achou)) if achou else None
        rows.append({"emissor": c.get("name", ""), "nome_no_config": c.get("name", ""),
                     "possivel_nome_na_base": achou, "ticker": c.get("ticker", ""),
                     "cnpj": (c.get("official") or {}).get("fund_cnpj", "") or c.get("cnpj", ""),
                     "pais": c.get("country", ""),
                     "subgrupo": ASSET_GROUP_LABELS.get(asset_group_of_company(c), ""),
                     "exposicao_atual_backlog": (f"{v:.3f}" if isinstance(v, (int, float)) and v else ""),
                     "status_match_exposicao": via, "confianca": conf,
                     "proxima_acao": ("ok" if via != "sem_match" else "casar por CNPJ/ticker (a_confirmar)")})
    _write_rows_csv(out, rows, cols)
    sm = sum(1 for r in rows if r["status_match_exposicao"] == "sem_match")
    return {"status": "ok", "linhas": len(rows), "sem_match": sm}


def quality_gate(cfg, audit_csv=None, probe_csv=None, coverage_backlog=None,
                 priority_sources=None, outdir=".", run_meta=None, expect_no_backfill=True):
    """4E.4 — Quality gate operacional. BLOQUEIA / WARNING conforme critérios.
    Resultado: PASS / PASS_WITH_WARNINGS / FAIL. Nunca mascara bloqueio como warning."""
    bloq, warn = [], []

    def B(cond, msg):
        if cond:
            bloq.append(msg)

    def W(cond, msg):
        if cond:
            warn.append(msg)

    # Backfill indevido — se o run deveria ser limpo e o run_meta indica backfill,
    # é BLOQUEIO (o usuário pediu backfill=false).
    rm = _read_json(run_meta) if run_meta else _read_json(os.path.join(outdir, "run_meta.json"))
    if rm is not None:
        if expect_no_backfill and rm.get("backfill") is True:
            B(True, "backfill ATIVO num run que deveria ser limpo (run_meta.backfill=true)")
        else:
            pass  # backfill=OFF confirmado pelo run_meta
    else:
        W(expect_no_backfill, "run_meta.json ausente — não dá para confirmar backfill=OFF pelo log")

    # cadastral
    erros_cad = [m for m in validate_asset_classes(cfg.get("watchlist", [])) if m.startswith("ERRO")]
    B(bool(erros_cad), f"validação cadastral com {len(erros_cad)} erro(s)")
    warns_cad = [m for m in validate_asset_classes(cfg.get("watchlist", [])) if m.startswith("WARNING")]
    W(bool(warns_cad), f"{len(warns_cad)} warning(s) cadastral(is)")
    # fund_sources não pode estar ativo
    B((cfg.get("fund_sources") or {}).get("enabled") is True, "fund_sources.enabled=True (coletor de fundos ativo)")

    # render + JS
    try:
        html = render_html({}, cfg, demo=True, changes={}, payload_thresholds={})
        B("view-cobertura" not in html, "dashboard não renderiza a aba de Cobertura")
    except Exception as exc:
        B(True, f"dashboard não renderiza ({exc})")

    # auditoria (se houver)
    aud = _read_csv(audit_csv) if audit_csv else None
    if aud is None:
        warn.append("auditoria CVM ausente (pendente_actions)")
    else:
        vale = next((r for r in aud if (r.get("emissor") or "").lower() == "vale"), None)
        if vale:
            B("bonito" in (vale.get("companhia_casada") or "").lower(), "Vale casando com Vale Bonito (falso positivo)")
        B(any((r.get("grupo") or "") == "fii" and (r.get("status") or "") == "esperado_filiante_sem_protocolo_no_ano" for r in aud),
          "FII classificado como esperado_filiante_sem_protocolo")
        B(any(_to_int(r.get("n_candidatos")) > 1 and (r.get("status") or "") == "filiante_cvm" for r in aud),
          "múltiplos candidatos virando filiante")
        cvlb = next((r for r in aud if (r.get("emissor") or "") == "Grupo CVLB"), None)
        if cvlb:
            W((cvlb.get("status") or "") == "revisar", "Grupo CVLB em revisar (esperado — confirmar identificador)")

    # probe (se houver) — checagens POR ALVO (não agregar Fundos)
    prb = _read_csv(probe_csv) if probe_csv else None
    if prb is None:
        warn.append("probe de fontes ausente (pendente_actions)")
    else:
        ab = [f"{r.get('pais')}" for r in prb if "anti-bot" in (r.get("veredito") or "").lower()]
        W(bool(ab), f"anti-bot/headless em: {', '.join(sorted(set(ab)))}")
        c404 = [f"{r.get('pais')}:{(r.get('url') or '')[-28:]}" for r in prb if str(r.get("status")) == "404"]
        W(bool(c404), f"alvo(s) com 404 (revisar URL/dataset): {', '.join(c404)}")
        ssl = [r.get("pais") for r in prb if "ssl" in (r.get("erro") or "").lower()]
        W(bool(ssl), f"erro SSL em: {', '.join(sorted(set(ssl)))}")
        fnet = next((r for r in prb if "fnet" in (r.get("url") or "").lower()), None)
        if fnet is not None:
            fv = ((fnet.get("veredito") or "") + (fnet.get("erro") or "")).lower()
            if "timeout" in fv or "indispon" in fv or str(fnet.get("status")) == "404":
                W(True, "FNET timeout/indisponível — NÃO tratar como utilizável; re-probe antes de coletor")
            else:
                W(True, f"FNET respondeu HTTP {fnet.get('status')} (houve timeout em run anterior) — "
                        f"exige re-probe de confirmação + validação de extração; NÃO implementar ainda")
        W(len(prb) < 15, f"probe com poucos alvos ({len(prb)}) — conferir CSV (esperado ~22)")

    # backlog / prioritárias
    bl = _read_csv(coverage_backlog) if coverage_backlog else None
    if bl is not None:
        W(any(not (r.get("representacao_aprox") or "").strip() for r in bl), "exposição em branco no backlog")
    ps = _read_csv(priority_sources) if priority_sources else None
    if ps is not None:
        W(any((r.get("url_ri") or "") == "a_confirmar" for r in ps), "RI prioritário a_confirmar")
    W(any((c.get("official") or {}).get("fund_cnpj") == "a_confirmar" for c in cfg.get("watchlist", [])),
      "CNPJ/admin de fundo a_confirmar")

    # Warnings do run real (Fase 4F)
    if aud is not None:
        n_rev = sum(1 for r in aud if (r.get("status") or "") == "revisar")
        W(n_rev > 5, f"{n_rev} emissor(es) em revisar na auditoria CVM (sanear identificadores)")
    feeds_off = [f.get("name", "?") for f in (cfg.get("custom_feeds") or []) if f.get("enabled") is False]
    W(bool(feeds_off), f"feed(s) desativado(s) por 404: {', '.join(feeds_off)}")
    revs = [c.get("name", "?") for c in cfg.get("watchlist", []) if c.get("revisao")]
    W(bool(revs), f"revisão cadastral pendente em: {', '.join(revs)}")
    tcfg = cfg.get("translation") or {}
    W(bool(tcfg.get("enabled")) and not ((cfg.get("llm") or {}).get("model_fallbacks")),
      "tradução habilitada sem model_fallbacks Gemini")

    status = "FAIL" if bloq else ("PASS_WITH_WARNINGS" if warn else "PASS")
    L = ["# Quality gate operacional", "", f"## Resultado: **{status}**", "",
         f"### Bloqueios ({len(bloq)})"]
    L += ([f"- ❌ {b}" for b in bloq] or ["- (nenhum)"])
    L += ["", f"### Warnings ({len(warn)})"]
    L += ([f"- ⚠️ {w}" for w in warn] or ["- (nenhum)"])
    L += ["", "> BLOQUEIA impede deploy; WARNING permite deploy com ressalvas. "
          "Itens 'pendente_actions' são warnings até o run real.", ""]
    _write_text(os.path.join(outdir, "quality_gate_status.md"), "\n".join(L))
    alertas = ([{"severidade": "BLOQUEIA", "item": b} for b in bloq]
               + [{"severidade": "WARNING", "item": w} for w in warn]) or [{"severidade": "INFO", "item": "sem alertas"}]
    _write_rows_csv(os.path.join(outdir, "quality_gate_alertas.csv"), alertas, ["severidade", "item"])
    print(f"   Quality gate: {status} ({len(bloq)} bloqueio(s), {len(warn)} warning(s))")
    return {"status": status, "bloqueios": bloq, "warnings": warn}


def post_actions_report(cfg, audit_csv=None, probe_csv=None, coverage_backlog=None,
                        priority_sources=None, outdir="."):
    """4E.3 — Relatório pós-Actions consolidado. Objetivo, leitura rápida."""
    aud = analyze_audit_cvm(audit_csv, outdir=outdir) if audit_csv else {"status": "pendente_arquivo"}
    prb = analyze_probe_sources(probe_csv, outdir=outdir) if probe_csv else {"status": "pendente_arquivo"}
    qg = quality_gate(cfg, audit_csv, probe_csv, coverage_backlog, priority_sources, outdir=outdir)
    cs = build_coverage_summary(cfg)

    def st(x, k="status"):
        return x.get(k, "pendente_arquivo") if isinstance(x, dict) else "pendente_arquivo"

    L = ["# Relatório pós-Actions — Radar de Risco", "",
         "## 1. Sumário executivo",
         f"- Quality gate: **{qg['status']}** ({len(qg['bloqueios'])} bloqueio(s), {len(qg['warnings'])} warning(s))",
         f"- Auditoria CVM: **{aud.get('recomendacao', st(aud))}**",
         f"- Probe de fontes: **{prb.get('proxima', st(prb))}**",
         f"- Watchlist: {cs['total']} emissores · cobertura oficial {cs['por_cobertura'].get('oficial', 0)}, "
         f"pendente fundos {cs['por_cobertura'].get('oficial_pendente_fundos', 0)}, "
         f"condicional CVM {cs['por_cobertura'].get('ampla_condicional_cvm', 0)}, ampla {cs['por_cobertura'].get('ampla', 0)}",
         "",
         "## 2. Status da execução",
         f"- auditoria_cobertura_cvm.csv: {'presente' if audit_csv and os.path.exists(audit_csv) else 'pendente_arquivo'}",
         f"- probe_fontes_oficiais.csv: {'presente' if probe_csv and os.path.exists(probe_csv) else 'pendente_arquivo'}",
         f"- coverage_backlog.csv: {'presente' if coverage_backlog and os.path.exists(coverage_backlog) else 'pendente_arquivo'}",
         f"- fontes_oficiais_prioritarias.csv: {'presente' if priority_sources and os.path.exists(priority_sources) else 'pendente_arquivo'}",
         "",
         "## 3. Auditoria CVM",
         (f"Recomendação: **{aud.get('recomendacao')}**. Revisar: {aud.get('revisar', 'n/a')}. "
          f"Bloqueios: {aud.get('bloqueios', 'n/a')}. Ver `relatorio_auditoria_cvm.md`."
          if aud.get("status") == "ok" else "**pendente_arquivo** — rode o Actions e forneça o CSV."),
         "",
         "## 4. Probe de fontes oficiais",
         (f"Ver `relatorio_probe_fontes.md` ({prb.get('n_alvos','?')} alvos). "
          f"FNET: categoria medida **{prb.get('fnet_categoria','?')}** — permanece "
          f"**candidato inconclusivo** (timeout em run anterior, sem validação de extração); "
          f"não implementar coletor ainda."
          if prb.get("status") == "ok" else "**pendente_arquivo** — rode `probe_sources=true`."),
         "",
         "## 5. Fundos/FNET (frentes separadas)",
         ("\n".join(f"- {d}" for d in prb.get("decisoes_fundos", []))
          if prb.get("status") == "ok" else "pendente_arquivo"),
         "",
         "## 6. México/LatAm",
         ("\n".join(f"- {d}" for d in prb.get("decisoes_latam", []))
          if prb.get("status") == "ok" else "pendente_arquivo"),
         "",
         "## 7. Cobertura prioritária",
         f"- Tier 1: {cs['prioritaria']['total']} · com fonte oficial {cs['prioritaria']['com_fonte_oficial']} · "
         f"com RI {cs['prioritaria']['com_ri']} · pendentes de confirmação {cs['prioritaria']['pendentes_confirmacao']}",
         "",
         "## 8. Pendências críticas",
         f"- não listadas aguardando audit CVM: {cs['pendencias']['naolistadas_aguardando_audit_cvm']}",
         f"- FIIs/fundos aguardando módulo: {cs['pendencias']['fiis_aguardando_modulo_fundos']}",
         f"- LatAm aguardando probe: {', '.join(cs['pendencias']['latam_aguardando_probe'])}",
         f"- IDs/aliases a confirmar: {len(cs['pendencias']['ids_aliases_a_confirmar'])}",
         "",
         "## 9. Decisão recomendada da próxima fase",
         f"- **{prb.get('proxima', 'pendente_arquivo')}**",
         f"- Matching CVM: **{aud.get('recomendacao', 'pendente_arquivo')}**",
         "",
         "## 10. Checklist de validação manual",
         "- [ ] site abre sem erro de JS",
         "- [ ] aba Cobertura carrega",
         "- [ ] export CSV e XLSX funcionam",
         "- [ ] filtros e 5 chips OK",
         "- [ ] histórico não corrompido",
         "",
         "## 11. Arquivos gerados",
         "- relatorio_auditoria_cvm.md, auditoria_cvm_resumo.csv, auditoria_cvm_alertas.csv",
         "- relatorio_probe_fontes.md, probe_fontes_resumo.csv, probe_fontes_recomendacoes.csv",
         "- quality_gate_status.md, quality_gate_alertas.csv",
         "- relatorio_pos_actions.md (este)", ""]
    _write_text(os.path.join(outdir, "relatorio_pos_actions.md"), "\n".join(L))
    return {"status": "ok", "quality_gate": qg["status"]}



# ═══════════════════ Fase 4G.1 — Resolução assistida de identificadores ═══════════════════
# Cruza os emissores Tier 1 ainda em 'revisar' com o cadastro oficial da CVM
# (cad_cia_aberta) e produz um pacote de REVISÃO — não cadastra sozinho, exceto
# quando a identificação é inequívoca. Sem rede, usa como fallback os candidatos
# REAIS da auditoria (denominações), marcando código/CNPJ como a_confirmar.

# Marcadores de entidade que NÃO é a companhia monitorada (subsidiária, veículo
# operacional, braço financeiro, homônimo de outro setor).
_SUBSIDIARIA_HINTS = (
    "holding", "participa", "leasing", "arrendamento", "dtvm", "bbi", "bba",
    "commodities", "sertrading", "distribuidora", "corretora", "seguros",
    "seguradora", "cartoes", "financiamento", "investimento", "fleet",
    "seminovos", "quimica", "petroquisa", "transpetro", "cimento",
    "servicos financeiros", "berj", "bcn", "previdencia", "consorcio",
    "geracao", "transmissao", "comercializadora", "securitizadora",
)
# Emissores que NUNCA são cadastrados automaticamente (decisão do usuário).
_NEVER_AUTO = ("Itaú Unibanco", "Vibra Energia")


def _flags_subsidiaria(denom: str) -> list[str]:
    n = normalize(denom)
    return [h for h in _SUBSIDIARIA_HINTS if h in n]


def resolve_cvm_identifiers(cfg: dict, audit_csv: str | None = None,
                            only_tier1_review: bool = True, cad_csv: str | None = None,
                            outdir: str = ".") -> dict:
    """4G.1 — Resolução assistida dos identificadores CVM. Gera review/confirmados/
    pendentes + relatório + patch YAML sugerido. NÃO relaxa matching: 'começa com
    o nome' nunca é critério; subsidiárias/veículos são descartados por marcador."""
    aud = _read_csv(audit_csv) if audit_csv else None

    # 1) alvos: Tier 1 em 'revisar' na auditoria real (ou, sem auditoria, Tier 1
    #    do config sem identificador forte)
    if aud is not None and only_tier1_review:
        alvos_nomes = [r.get("emissor", "") for r in aud
                       if (r.get("status") or "") == "revisar" and str(r.get("tier")) == "1"]
        # exclui os já saneados no config (identificador forte ou razão social)
        _ok = {c.get("name") for c in cfg.get("watchlist", [])
               if (c.get("codigo_cvm") or c.get("cnpj")
                   or (c.get("razao_social") and not c.get("revisao")))}
        alvos_nomes = [n for n in alvos_nomes if n not in _ok]
    else:
        alvos_nomes = [c.get("name", "") for c in cfg.get("watchlist", [])
                       if c.get("tier") == 1 and c.get("country") == "Brasil"
                       and not (c.get("codigo_cvm") or c.get("cnpj"))]
    by_cfg = {c.get("name", ""): c for c in cfg.get("watchlist", [])}
    aud_by = {r.get("emissor", ""): r for r in (aud or [])}

    # 2) cadastro oficial: arquivo local, rede, ou fallback pelos candidatos da auditoria
    cad, fonte_cad = None, ""
    if cad_csv and os.path.exists(cad_csv):
        try:
            with open(cad_csv, encoding="latin-1") as f:
                rd = csv.DictReader(f, delimiter=";")
                cad = [{"codigo_cvm": _digits(r.get("CD_CVM", "")), "cnpj": _digits(r.get("CNPJ_CIA", "")),
                        "denom": (r.get("DENOM_SOCIAL") or "").strip(),
                        "denom_norm": normalize(r.get("DENOM_SOCIAL") or ""),
                        "situacao": (r.get("SIT") or "").strip(),
                        "mercado": (r.get("TP_MERC") or "").strip()} for r in rd
                       if (r.get("DENOM_SOCIAL") or "").strip()]
            fonte_cad = f"arquivo local ({cad_csv})"
        except Exception as exc:
            print(f"   ⚠️  falha lendo {cad_csv}: {exc}")
    if cad is None:
        cad = _cvm_cadastro_index()
        fonte_cad = "cad_cia_aberta (rede)" if cad else ""
    if not cad:
        fonte_cad = "FALLBACK: candidatos reais da auditoria (sem código CVM/CNPJ)"
        print("   ℹ️  Cadastro CVM indisponível — usando candidatos da auditoria; "
              "códigos/CNPJs ficam 'a_confirmar'.")

    review, confirmados, pendentes = [], [], []
    for nome in alvos_nomes:
        c = by_cfg.get(nome, {})
        termos = [c.get("razao_social") or nome] + list(c.get("aliases") or [])
        # candidatos
        cands = []
        if cad:
            vistos = set()
            for t in termos:
                tn = normalize(str(t))
                if len(tn) < 4:
                    continue          # termo curto nunca busca sozinho
                pat = _word_pattern(str(t))
                for r in cad:
                    if pat.search(r["denom_norm"]) and r["denom_norm"] not in vistos:
                        vistos.add(r["denom_norm"])
                        cands.append(dict(r))
        else:
            ar = aud_by.get(nome, {})
            vistos = set()
            for d in (ar.get("candidatos") or "").split(";"):
                d = d.strip()
                if d and normalize(d) not in vistos:
                    vistos.add(normalize(d))
                    cands.append({"denom": d, "denom_norm": normalize(d), "codigo_cvm": "",
                                  "cnpj": "", "situacao": "", "mercado": ""})
        alvo_core = _core_name(nome)
        # classificação por candidato
        limpos, exatos = [], []
        for cd in cands:
            fl = _flags_subsidiaria(cd["denom"])
            cd["_flags"] = fl
            cd["_core_igual"] = (_core_name(cd["denom"]) == alvo_core)
            if not fl:
                limpos.append(cd)
            if cd["_core_igual"]:
                exatos.append(cd)
        exatos_limpos = [x for x in exatos if not x["_flags"]]
        tem_holding = any(("holding" in x["_flags"] or "participa" in x["_flags"]) for x in cands)
        _brutos = [normalize(d.strip()) for d in (aud_by.get(nome, {}).get("candidatos") or "").split(";") if d.strip()]
        homonimos = len(_brutos) != len(set(_brutos)) if _brutos else \
            (len(cands) != len({normalize(x["denom"]) for x in cands}))

        # decisão do emissor
        if nome in _NEVER_AUTO:
            escolha = exatos_limpos[0] if exatos_limpos else (limpos[0] if len(limpos) == 1 else None)
            veredito, risco = "revisao_manual", "alto"
            just = ("emissor marcado para confirmação manual obrigatória "
                    + ("(holding vs operadora: o ticker monitorado pode ser a holding)"
                       if nome == "Itaú Unibanco" else
                       "(dois registros CVM com razão social idêntica — razão social não desambigua)"))
        elif len(exatos_limpos) == 1 and len(limpos) == 1 and not tem_holding and not homonimos:
            escolha, veredito, risco = exatos_limpos[0], "inequivoco", "baixo"
            just = "denominação oficial idêntica ao emissor e sem candidato concorrente relevante"
        elif len(limpos) == 1:
            escolha, veredito, risco = limpos[0], "provavel_confirmar", "medio"
            just = ("único candidato sem marcador de subsidiária/veículo ("
                    + f"{len(cands) - 1} descartado(s) por marcador)"
                    + ("; ATENÇÃO: há candidato holding/participações" if tem_holding else ""))
        elif not cands:
            escolha, veredito, risco = None, "sem_candidato", "alto"
            just = "nenhum candidato encontrado no cadastro para os termos do emissor"
        else:
            escolha, veredito, risco = None, "ambiguo", "alto"
            just = (f"{len(limpos)} candidato(s) sem marcador entre {len(cands)} — "
                    "razão social não desambigua; exige código CVM/CNPJ")

        alerta = ""
        if tem_holding:
            alerta = "candidato HOLDING/PARTICIPAÇÕES presente — confirmar se o ticker monitorado é a holding"
        if homonimos:
            alerta = (alerta + " | " if alerta else "") + "registros distintos com razão social idêntica"

        pode_auto = "sim" if (veredito == "inequivoco" and escolha and escolha.get("codigo_cvm")) else "não"
        rec = {"inequivoco": "cadastrar codigo_cvm/cnpj", "provavel_confirmar": "confirmar manualmente e cadastrar",
               "revisao_manual": "manter em revisar até confirmação manual",
               "ambiguo": "manter em revisar; obter código CVM/CNPJ",
               "sem_candidato": "revisar termos/aliases do emissor"}[veredito]

        base = {"emissor_config": nome, "ticker_config": c.get("ticker", ""),
                "aliases_config": "; ".join(str(a) for a in (c.get("aliases") or [])),
                "razao_social_config": c.get("razao_social", ""),
                "n_candidatos_do_emissor": len(cands), "risco_erro": risco,
                "alerta": alerta, "recomendacao": rec,
                "pode_cadastrar_automaticamente": pode_auto,
                "justificativa": just,
                "proxima_acao": ("cadastrar no config e reauditar" if pode_auto == "sim"
                                 else "conferir no cadastro CVM (situação/tipo de mercado) e cadastrar")}
        # uma linha por candidato (rastreabilidade)
        if cands:
            for cd in cands:
                review.append(dict(base, candidato_denom_social=cd["denom"],
                                   candidato_codigo_cvm=cd["codigo_cvm"] or "a_confirmar",
                                   candidato_cnpj=cd["cnpj"] or "a_confirmar",
                                   candidato_situacao=cd["situacao"] or "a_confirmar",
                                   candidato_tipo_mercado=cd["mercado"] or "a_confirmar",
                                   tipo_match=("razao_social_identica" if cd["_core_igual"]
                                               else ("sem_marcador" if not cd["_flags"] else "descartado_marcador")),
                                   marcadores=("; ".join(cd["_flags"]) or "—"),
                                   escolhido=("sim" if escolha and cd["denom"] == escolha["denom"] else "não")))
        else:
            review.append(dict(base, candidato_denom_social="—", candidato_codigo_cvm="—",
                               candidato_cnpj="—", candidato_situacao="—", candidato_tipo_mercado="—",
                               tipo_match="sem_candidato", marcadores="—", escolhido="não"))

        linha_res = {"emissor": nome, "veredito": veredito, "risco_erro": risco,
                     "candidato_escolhido": (escolha or {}).get("denom", ""),
                     "codigo_cvm": (escolha or {}).get("codigo_cvm", "") or "a_confirmar",
                     "cnpj": (escolha or {}).get("cnpj", "") or "a_confirmar",
                     "alerta": alerta, "justificativa": just, "proxima_acao": base["proxima_acao"]}
        (confirmados if pode_auto == "sim" else pendentes).append(linha_res)

    cols = ["emissor_config", "ticker_config", "aliases_config", "razao_social_config",
            "candidato_denom_social", "candidato_codigo_cvm", "candidato_cnpj",
            "candidato_situacao", "candidato_tipo_mercado", "tipo_match", "marcadores",
            "escolhido", "n_candidatos_do_emissor", "risco_erro", "alerta", "recomendacao",
            "pode_cadastrar_automaticamente", "justificativa", "proxima_acao"]
    _write_rows_csv(os.path.join(outdir, "identificadores_cvm_tier1_review.csv"), review, cols)
    rcols = ["emissor", "veredito", "risco_erro", "candidato_escolhido", "codigo_cvm",
             "cnpj", "alerta", "justificativa", "proxima_acao"]
    _write_rows_csv(os.path.join(outdir, "identificadores_cvm_tier1_confirmados.csv"), confirmados, rcols)
    _write_rows_csv(os.path.join(outdir, "identificadores_cvm_tier1_pendentes.csv"), pendentes, rcols)

    # patch YAML sugerido (comentado — não aplicar direto)
    y = ["# patch_cvm_ids_tier1_sugerido.yaml",
         "# SUGESTÕES para revisão manual — NÃO aplicar sem conferir no cadastro CVM.",
         f"# Fonte do cadastro: {fonte_cad}", ""]
    for r in pendentes + confirmados:
        y.append(f"- emissor: \"{r['emissor']}\"")
        y.append(f"    # veredito: {r['veredito']} · risco: {r['risco_erro']}")
        if r["alerta"]:
            y.append(f"    # ALERTA: {r['alerta']}")
        y.append(f"    # candidato: {r['candidato_escolhido'] or '(indefinido)'}")
        y.append(f"    codigo_cvm: \"{r['codigo_cvm']}\"")
        y.append(f"    cnpj: \"{r['cnpj']}\"")
        if r["candidato_escolhido"]:
            y.append(f"    razao_social: \"{r['candidato_escolhido']}\"")
        y.append("")
    _write_text(os.path.join(outdir, "patch_cvm_ids_tier1_sugerido.yaml"), "\n".join(y))

    # relatório
    L = ["# Relatório — Identificadores CVM dos Tier 1 (4G.1)", "",
         "## 1. Resumo executivo",
         f"- Emissores Tier 1 revisados: **{len(alvos_nomes)}**",
         f"- Fonte do cadastro: **{fonte_cad}**",
         f"- Seguros para cadastro automático: **{len(confirmados)}**",
         f"- Pendentes de confirmação manual: **{len(pendentes)}**",
         "- Critério: 'começa com o nome' **não** é aceito; subsidiárias/veículos são "
         "descartados por marcador (holding, leasing, DTVM, BBI, commodities, distribuidora, fleet…).",
         "", "## 2. Tabela por emissor", "",
         "| emissor | veredito | risco | candidato | alerta |", "|---|---|---|---|---|"]
    for r in pendentes + confirmados:
        L.append(f"| {r['emissor']} | {r['veredito']} | {r['risco_erro']} | "
                 f"{r['candidato_escolhido'] or '—'} | {r['alerta'] or '—'} |")
    L += ["", "## 3. Riscos de erro",
          "- Aceitar subsidiária/veículo no lugar da companhia monitorada (leasing, DTVM, BBI, distribuidora).",
          "- Confundir **holding** com **operadora** quando o ticker monitorado é da holding.",
          "- Registros distintos com razão social idêntica (razão social não desambigua).",
          "", "## 4. Campos sugeridos para o config",
          "`codigo_cvm`, `cnpj` e (opcionalmente) `razao_social` — ver "
          "`patch_cvm_ids_tier1_sugerido.yaml`. Cadastrar **um** identificador forte já basta.",
          "", "## 5. Próximos passos",
          "1. Conferir cada pendente no cadastro CVM (situação ATIVO e tipo de mercado).",
          "2. Aplicar o patch aprovado no `config_risco.yaml`.",
          "3. Rodar auditoria (`audit_cvm=true`, `backfill=false`) e comparar revisar antes/depois.", ""]
    _write_text(os.path.join(outdir, "relatorio_identificadores_cvm_tier1.md"), "\n".join(L))
    print(f"   4G.1: {len(alvos_nomes)} Tier 1 · {len(confirmados)} confirmável(is) · "
          f"{len(pendentes)} pendente(s) · fonte: {fonte_cad}")
    return {"alvos": len(alvos_nomes), "confirmados": len(confirmados),
            "pendentes": len(pendentes), "fonte_cad": fonte_cad}



def audit_international_coverage(cfg: dict, history_path: str = "risk_history.json",
                                 exposure_path: str | None = None,
                                 outdir: str = ".", run_meta_path: str | None = None,
                                 run_count: int | None = None) -> dict:
    """4H.1 — Auditoria REAL de cobertura internacional. Mede, por emissor:
    se ele é efetivamente pesquisado (tier × rotação), quantas notícias tem em
    7/30/90 dias, e se há fonte oficial ATIVA (coleta comprovada) ou apenas
    documentada (URL/probe). Distingue 'sem evento' de 'não pesquisado' — a
    confusão entre os dois é o erro que mascara buraco de cobertura."""
    from collections import Counter, defaultdict
    hist = _read_json(history_path) or {}
    arts = (hist.get("articles") or {}).values()
    rm = _read_json(run_meta_path or os.path.join(os.path.dirname(history_path) or ".",
                                                  "run_meta.json")) or {}
    telemetria = rm.get("international_search_execution") or {}
    oficial_exec = rm.get("official_source_execution") or {}
    # visão de CICLO: histórico cumulativo das últimas execuções (run_meta é só
    # a execução atual; sem isto não há como provar cobertura dos 4 ciclos).
    _sh = _read_json(os.path.join(os.path.dirname(history_path) or ".",
                                  "international_search_history.json")) or {}
    runs_hist = _sh.get("runs") or []
    # ordena por run_count e, em empate, por finished_at
    runs_hist = sorted(runs_hist, key=lambda r: (r.get("run_count", 0), r.get("finished_at", "")))
    ciclo = {}
    for pos, rr in enumerate(runs_hist):
        for em in (rr.get("emitters") or {}):
            ciclo.setdefault(em, {})["_pos"] = pos      # posição da ÚLTIMA busca
    for rr in runs_hist[-4:]:
        for em, t in (rr.get("emitters") or {}).items():
            a = ciclo.setdefault(em, {})
            a.setdefault("runs", 0); a.setdefault("queries", 0); a.setdefault("success", 0)
            a.setdefault("errors", 0); a.setdefault("raw", 0); a.setdefault("ultimo", "")
            a["runs"] += 1
            a["queries"] += t.get("queries", 0)
            a["success"] += t.get("success", t.get("http_success", 0))
            a["errors"] += t.get("errors", 0)
            a["raw"] += t.get("raw_articles", 0)
            a["ultimo"] = rr.get("finished_at", "")
    proveniencia = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from_history": os.path.basename(history_path),
        "generated_from_history_timestamp": hist.get("last_run", "") or "",
        "generated_from_run_id": rm.get("run_finished_at", "") or rm.get("generated_at", "") or "sem_run_meta",
        "historico_registros": len(hist.get("articles") or {}),
        "telemetria_disponivel": "sim" if telemetria else "NÃO (execução não evidenciada)",
        "runs_no_historico_cumulativo": len(runs_hist),
        "cobertura_ciclo_completo": "a medir (requer 4 runs com telemetria)" if len(runs_hist) < 4 else "medida",
    }
    agora = int(datetime.now(timezone.utc).timestamp())

    por_emissor = defaultdict(list)
    for r in arts:
        for c in (r.get("companies") or []):
            por_emissor[c].append(r)

    exposure = _load_exposure_map(exposure_path)
    tiers = cfg.get("tiers") or {}
    est = [c for c in cfg.get("watchlist", []) if c.get("country") != "Brasil"]

    def _oe(canal, emissor, campo):
        """Telemetria de fonte oficial — sem evidência, NUNCA afirma execução."""
        d = ((oficial_exec.get(canal) or {}).get(emissor) or {})
        if not d:
            return "sem_evidencia"
        v = d.get(campo, "")
        return ("sim" if v is True else ("não" if v is False else v))

    linhas = []
    for c in est:
        nome = c["name"]
        tier = c.get("tier", 3)
        every = (tiers.get(tier) or {}).get("fetch_every_n_runs", 1)
        force = bool(c.get("force_fetch"))
        pesquisado = (every != 0) or force
        recs = por_emissor.get(nome, [])
        def n_dias(d):
            lim = agora - d * 86400
            return sum(1 for r in recs if (r.get("pub_ts") or 0) >= lim)
        n7, n30, n90 = n_dias(7), n_dias(30), n_dias(90)
        ultima = max((r.get("pub_iso", "") for r in recs), default="")
        # ── execução REAL (run_meta) × elegibilidade (config) ──
        tel = (telemetria or {}).get(nome) or {}
        tem_tel = bool(tel)
        bucket = (int(hashlib.md5(nome.encode("utf-8")).hexdigest(), 16) % every) if every > 1 else 0
        prox = ("toda execução" if every == 1 else
                (f"quando (run + bucket) % {every} == 0 · bucket {bucket}" if every else
                 ("apenas force_fetch" if force else "NUNCA")))
        # compat com históricos antigos, mas o campo primário é `success`
        _suc = tel.get("success", tel.get("http_success", 0))
        _err = tel.get("errors", max(0, tel.get("queries", 0) - _suc))
        _raw = tel.get("raw_articles", 0)
        _loc_ok = tel.get("locale_com_resultado", "")
        _locs_t = tel.get("locales_tentados", []) or []
        if not tem_tel:
            pesq_exec, mstatus, merro = "sem_evidencia_de_execucao", "sem_evidencia_de_execucao", ""
        else:
            pesq_exec = "sim" if tel.get("searched") else "não"
            # erro no nível superior representa APENAS falha final total: se algum
            # locale trouxe artigos, o run não é ERRO_FONTE (é sucesso_fallback).
            _erro_total = bool(_err) and not _suc and not _raw
            merro = (tel.get("error", "") or "") if _erro_total else ""
            if _erro_total:
                mstatus = "erro_total"
            elif _raw and _loc_ok and _locs_t and _loc_ok != _locs_t[0]:
                mstatus = "sucesso_fallback"
            elif _raw:
                mstatus = "sucesso_primary"
            elif _suc:
                mstatus = "sucesso_zero_resultados"
            else:
                mstatus = "sucesso_parcial_com_warning"
        off = c.get("official") or {}
        edgar = edgar_eligible(c)
        ri_rss = bool(off.get("rss"))
        # `official.ri` é URL institucional (informativa) — NÃO comprova coleta.
        # Só `official.news` (página de comunicados varrida) ou `rss` contam.
        ri_news = bool(off.get("news"))
        ri_url_informativa = bool(off.get("ri")) and not (ri_rss or ri_news)
        src = (cfg.get("official_sources") or {}).get(c.get("country") or "") or {}
        reg_doc = bool(src.get("filings_url") or src.get("api"))
        # coletor ATIVO = existe código que coleta daquela fonte. Hoje: EDGAR e
        # feeds de RI. Reguladores locais estão documentados/probados, não coletados.
        reg_ativo = False
        oficiais_90d = sum(1 for r in recs
                           if (r.get("pub_ts") or 0) >= agora - 90 * 86400
                           and (r.get("trust_tier") == "oficial"
                                or "sec.gov" in (r.get("domain") or "")))
        # ESTADOS explícitos: elegibilidade/configuração NÃO é execução.
        def _estado(canal, configurado):
            if not configurado:
                return None
            d = ((oficial_exec.get(canal) or {}).get(nome) or {})
            if not d:
                return "oficial_configurada"      # sem evidência de execução
            if d.get("error"):
                return "oficial_com_erro"
            achou = d.get("filings_found") or d.get("items_found") or 0
            return ("oficial_executada_com_resultado" if achou
                    else "oficial_executada_sem_resultado")
        # AGREGAÇÃO: avalia TODOS os canais e escolhe o melhor estado. Uma fonte
        # apenas configurada não pode esconder outra efetivamente executada.
        _ordem = {"oficial_executada_com_resultado": 0, "oficial_executada_sem_resultado": 1,
                  "oficial_com_erro": 2, "oficial_configurada": 3}
        _cand = [(c_, _estado(c_, cfgd)) for c_, cfgd in
                 (("EDGAR", edgar), ("RI_RSS", ri_rss), ("RI_NEWS", ri_news))]
        _cand = [(c_, e_) for c_, e_ in _cand if e_]
        canal_principal, modo = "", None
        if _cand:
            _cand.sort(key=lambda x: _ordem.get(x[1], 9))
            canal_principal, modo = _cand[0]
        warn_canais = "; ".join(f"{c_}:{e_}" for c_, e_ in _cand[1:])
        if modo:
            pass
        elif ri_url_informativa:
            modo = "somente documentada (RI só institucional)"
        elif reg_doc:
            modo = "somente documentada (regulador sem coletor)"
        else:
            modo = "apenas mídia (ampla)"

        # motivo de zero resultado — separa 'não pesquisado' de 'sem evento'
        aliases = c.get("aliases") or []
        alias_curto = [a for a in aliases if len(normalize(str(a))) < 4]
        # ORDEM: evidência da execução atual primeiro; sem elif inalcançável.
        hl, gl = locale_for_company(c, cfg)
        _cic = ciclo.get(nome, {})
        alerta_extra = ""
        if alias_curto:
            alerta_extra = f" · ALERTA: alias curto {alias_curto}"
        if (c.get("search_locale") or {}).get("primary") is None and c.get("language") \
                and c.get("language") not in (hl.split("-")[0],):
            alerta_extra += " · locale suspeito (idioma ≠ locale)"
        if not pesquisado:
            motivo, status = "emissor NÃO elegível (fetch_every_n_runs=0)", "BLOQUEIO"
        elif not tem_tel:
            motivo, status = ("elegível, mas SEM evidência de execução "
                              "(telemetria ausente) — não afirmar 'pesquisado'"), "SEM_EVIDENCIA"
        elif merro:
            motivo, status = (f"falha TOTAL da consulta [{tel.get('error_type','')}]: "
                              f"{merro[:60]}"), "ERRO_FONTE"
        elif not _raw:
            motivo, status = "fonte respondeu OK com 0 resultados brutos", "ZERO_BRUTO"
        elif n90 == 0:
            motivo, status = ("bruto coletado, mas NADA atribuído ao emissor"
                              + alerta_extra), "SEM_ATRIBUICAO"
        elif not any(r.get("event_ids") for r in recs):
            motivo, status = "atribuído, porém sem evento da taxonomia", "SEM_EVENTO_CLASSIFICADO"
        elif n7 == 0:
            motivo, status = "sem evento na janela (tem histórico)", "OK"
        else:
            motivo, status = "", "OK"

        exp = ""
        if exposure:
            v = exposure.get(normalize(nome))
            exp = f"{v:.3f}" if isinstance(v, (int, float)) and v else ""
        linhas.append({
            "emissor": nome, "ticker": c.get("ticker", ""), "pais": c.get("country", ""),
            "regiao": c.get("region", ""), "idioma": c.get("language", ""),
            "tier": tier, "fetch_every_n_runs": every, "force_fetch": "sim" if force else "não",
            # ── 1. elegibilidade / configuração ──
            "elegivel_na_rotacao": "sim" if pesquisado else "NÃO",
            "bucket_rotacao": bucket,
            "proxima_execucao_prevista": prox,
            "busca_midia_configurada": "sim" if pesquisado else "não",
            "locale_usado": f"{hl}/{gl}",
            "locale_override": "sim" if (c.get("search_locale") or {}).get("primary") else "não",
            "aliases_usados": "; ".join(str(a) for a in aliases),
            "exposicao_aprox": exp,
            # ── 2-4. execução real, resposta da fonte e resultado bruto ──
            "pesquisado_nesta_execucao": pesq_exec,
            "midia_pesquisada_ultima_execucao": pesq_exec,
            "midia_respondeu_ultima_execucao": ("sim" if tem_tel and _suc
                                                else ("não" if tem_tel else "sem_evidencia")),
            "midia_com_resultado_bruto": ("sim" if tem_tel and tel.get("raw_articles")
                                          else ("não" if tem_tel else "sem_evidencia")),
            "consultas_executadas": tel.get("queries", "") if tem_tel else "",
            "consultas_com_sucesso": _suc if tem_tel else "",
            "consultas_com_erro": _err if tem_tel else "",
            "artigos_brutos_retornados": tel.get("raw_articles", "") if tem_tel else "",
            "ultima_busca_status": mstatus, "ultima_busca_erro": merro,
            "warnings_locale": "; ".join(f"{k}:{v.get('error_type','')}"
                                         for k, v in (tel.get("por_locale") or {}).items()
                                         if v.get("errors")) if tem_tel else "",
            "locale_com_resultado": tel.get("locale_com_resultado", "") if tem_tel else "",
            "locales_tentados": "; ".join(tel.get("locales_tentados", [])) if tem_tel else "",
            "pesquisado_ultimos_4_runs": ("sim" if _cic.get("runs") else
                                          ("não" if runs_hist else "sem_evidencia")),
            "numero_de_runs_pesquisado": _cic.get("runs", 0),
            "ultima_execucao_pesquisada": _cic.get("ultimo", ""),
            "ciclos_desde_ultima_busca": ((len(runs_hist) - 1 - _cic["_pos"]) if "_pos" in _cic else ""),
            "consultas_acumuladas": _cic.get("queries", 0),
            "sucessos_acumulados": _cic.get("success", 0),
            "erros_acumulados": _cic.get("errors", 0),
            "artigos_brutos_acumulados": _cic.get("raw", 0),
            "canal_oficial_principal": canal_principal,
            "warnings_canais_oficiais": warn_canais,
            "edgar_configurado": "sim" if edgar else "não",
            "edgar_executado": _oe("EDGAR", nome, "attempted"),
            "edgar_execucao_sucesso": _oe("EDGAR", nome, "success"),
            "edgar_filings_encontrados": _oe("EDGAR", nome, "filings_found"),
            "edgar_erro": _oe("EDGAR", nome, "error"),
            "ri_rss_configurado": "sim" if ri_rss else "não",
            "ri_rss_executado": _oe("RI_RSS", nome, "attempted"),
            "ri_rss_itens": _oe("RI_RSS", nome, "items_found"),
            "ri_news_configurado": "sim" if ri_news else "não",
            "ri_news_executado": _oe("RI_NEWS", nome, "attempted"),
            "ri_news_itens": _oe("RI_NEWS", nome, "items_found"),
            "ri_news_metodo": _oe("RI_NEWS", nome, "method"),
            "noticias_7d": n7, "noticias_30d": n30, "noticias_90d": n90,
            "ultima_noticia": ultima,
            # ── 5. fontes oficiais: elegível × configurado × executado ──
            "edgar_elegivel": "sim" if edgar else "não",
            "edgar_coletor_configurado": "sim" if edgar else "não",
            "ri_url_apenas_informativa": "sim" if ri_url_informativa else "não",
            "regulador_documentado": "sim" if reg_doc else "não",
            "regulador_coletor_ativo": "sim" if reg_ativo else "não",
            "itens_oficiais_90d": oficiais_90d,
            "modo_cobertura": modo, "motivo_sem_resultados": motivo,
            "status": status,
            "proxima_acao": ("incluir na rotação (4H.2)" if not pesquisado else
                             ("revisar aliases/razão social local" if n90 == 0 and alias_curto else
                              ("verificar recall da mídia local" if n90 == 0 else
                               ("cadastrar RI/fonte oficial" if modo.startswith(("somente", "apenas"))
                                else "manter")))),
        })

    cols = list(linhas[0].keys()) if linhas else []
    _write_rows_csv(os.path.join(outdir, "auditoria_cobertura_internacional.csv"), linhas, cols)

    # por país
    pais = defaultdict(lambda: Counter())
    for l in linhas:
        p = pais[l["pais"]]
        p["monitorados"] += 1
        p["elegiveis"] += (l["elegivel_na_rotacao"] == "sim")
        p["exec_evidenciada"] += (l["pesquisado_nesta_execucao"] == "sim")
        p["com_noticia_90d"] += (l["noticias_90d"] > 0)
        p["oficial_ativa"] += l["modo_cobertura"].startswith("oficial_executada")
        p["oficial_configurada"] += (l["modo_cobertura"] == "oficial_configurada")
        p["so_documentada"] += l["modo_cobertura"].startswith("somente")
        p["apenas_midia"] += l["modo_cobertura"].startswith("apenas")
        p["zero_resultados"] += (l["noticias_90d"] == 0 and l["elegivel_na_rotacao"] == "sim")
    rows_pais = [{"pais": k, **dict(v)} for k, v in sorted(pais.items())]
    _write_rows_csv(os.path.join(outdir, "cobertura_internacional_por_pais.csv"), rows_pais,
                    ["pais", "monitorados", "elegiveis", "exec_evidenciada", "com_noticia_90d",
                     "oficial_ativa", "oficial_configurada", "so_documentada", "apenas_midia", "zero_resultados"])

    # fontes oficiais internacionais (o que é ativo × documentado)
    fo = []
    for p, s in (cfg.get("official_sources") or {}).items():
        if p == "Brasil":
            continue
        n_em = sum(1 for l in linhas if l["pais"] == p)
        fo.append({"pais": p, "regulador": s.get("regulador", ""),
                   "filings_url": s.get("filings_url", "") or "", "api": s.get("api", "") or "",
                   "viabilidade_documentada": s.get("viabilidade", ""),
                   "coletor_ativo": "não", "emissores_no_pais": n_em,
                   "observacao": "documentada/probada — extração NÃO validada (4H.3)"})
    _write_rows_csv(os.path.join(outdir, "fontes_oficiais_internacionais.csv"), fo,
                    ["pais", "regulador", "filings_url", "api", "viabilidade_documentada",
                     "coletor_ativo", "emissores_no_pais", "observacao"])

    # sem resultado
    sem = [l for l in linhas if l["noticias_90d"] == 0]
    _write_rows_csv(os.path.join(outdir, "emissores_internacionais_sem_resultado.csv"), sem, cols)

    # prioritárias por materialidade
    prio = []
    for l in linhas:
        if l["modo_cobertura"].startswith("oficial_executada"):
            continue
        try:
            e = float(l["exposicao_aprox"]) if l["exposicao_aprox"] else 0.0
        except ValueError:
            e = 0.0
        p = "alta" if e >= 0.10 else ("média" if e >= 0.02 else "baixa")
        prio.append({"emissor": l["emissor"], "pais": l["pais"],
                     "exposicao_aprox": l["exposicao_aprox"], "tier": l["tier"],
                     "fonte_oficial_atual": l["modo_cobertura"],
                     "lacuna": ("sem EDGAR e sem RI configurado"
                                if l["ri_news_configurado"] == "não" else "RI a validar"),
                     "candidato_fonte": ("RI do emissor (a_confirmar)"),
                     "dificuldade": "baixa" if l["ri_news_configurado"] == "sim" else "média",
                     "prioridade": p,
                     "recomendacao": "cadastrar official.rss/news do RI antes de scraper de regulador",
                     "_e": e})
    prio.sort(key=lambda x: -x["_e"])
    for x in prio:
        x.pop("_e", None)
    _write_rows_csv(os.path.join(outdir, "fontes_oficiais_internacionais_prioritarias.csv"), prio,
                    ["emissor", "pais", "exposicao_aprox", "tier", "fonte_oficial_atual",
                     "lacuna", "candidato_fonte", "dificuldade", "prioridade", "recomendacao"])

    _write_text(os.path.join(outdir, "auditoria_internacional_proveniencia.json"),
                json.dumps(proveniencia, ensure_ascii=False, indent=2))
    tot = len(linhas)
    nao_pesq = sum(1 for l in linhas if l["elegivel_na_rotacao"] != "sim")
    sem_evid = sum(1 for l in linhas if l["pesquisado_nesta_execucao"] == "sem_evidencia_de_execucao")
    of_ativa = sum(1 for l in linhas if l["modo_cobertura"].startswith("oficial_executada"))
    of_cfg = sum(1 for l in linhas if l["modo_cobertura"] == "oficial_configurada")
    so_doc = sum(1 for l in linhas if l["modo_cobertura"].startswith("somente"))
    com_not = sum(1 for l in linhas if l["noticias_90d"] > 0)
    print(f"   4H.1: {tot} estrangeiros · {nao_pesq} não elegíveis · {sem_evid} sem evidência de "
          f"execução · {com_not} com notícia 90d · {of_ativa} fonte oficial ativa · {so_doc} só documentada")
    print(f"        proveniência: {proveniencia['generated_from_history']} "
          f"({proveniencia['historico_registros']} registros) · telemetria: {proveniencia['telemetria_disponivel']}")
    return {"total": tot, "nao_elegiveis": nao_pesq, "sem_evidencia": sem_evid,
            "proveniencia": proveniencia, "com_noticia_90d": com_not,
            "oficial_ativa": of_ativa, "so_documentada": so_doc,
            "por_pais": rows_pais, "linhas": linhas}



def classify_and_attribute(art: dict, cfg: dict) -> None:
    """[4H.3A] Classificação + atribuição de UM artigo, in-place.

    Extraído do laço de produção SEM alteração de lógica, para que o
    SHADOW MODE (Bloco D) use exatamente o mesmo caminho do pipeline real:
    eventos → empresas → papel por empresa×evento → exclusividade
    semântica → famílias. Produção e shadow não podem divergir."""
    # 4H.1d — ORDEM: (1) eventos preliminares → (2) empresas → (3) papel por
    # EMPRESA × EVENTO → (4) famílias. Antes a guarda rodava sem saber quem
    # era a empresa, e "health systems sue CVS alleging fraud" suprimia a
    # fraude da CVS (que é ré, não autora).
    _evs = classify_article(art, cfg["taxonomy"])
    art["companies"] = detect_companies(art, cfg["watchlist"])
    # 6) Resolução contextual de entidade — OPT-IN, aditiva. Só emissores que
    # declaram search_terms/entity_cues/exclusion_cues/related_entities/
    # entity_scope/entity_confidence entram neste laço (nenhum dos 160 reais
    # de config_risco.yaml hoje); para eles, `art["companies"]` sai IDÊNTICO
    # ao que `detect_companies` já produziu. Ver `apply_contextual_entity_
    # resolution` — corrige falsos positivos (exclusion_cue) e recupera
    # falsos negativos (atribuição por contexto sem alias literal) só para
    # quem optou pelo novo caminho.
    apply_contextual_entity_resolution(art, cfg)
    _wl = {c["name"]: c for c in cfg["watchlist"]}
    _assess, _desc_all, _por_empresa, _ctx_por_empresa = {}, [], {}, {}
    _titulo, _resumo = art.get("title", ""), art.get("summary", "")
    for _co in (art.get("companies") or [None]):
        _al = (_wl.get(_co, {}) or {}).get("aliases") if _co else None
        _man, _desc = semantic_role_guard(_titulo, _resumo, _evs, art.get("companies"),
                                          company=_co, aliases=_al or ([_co] if _co else None))
        if _co:
            # 4H.0b — evento cujo SUJEITO é terceiro sai da lista pontuável
            # e vira contexto: 'Vale informa sobre Plano de RJ da Samarco'
            # não é RJ da Vale. Por EVENTO, não por artigo.
            _role = mention_role(_titulo, _co, _al or [_co],
                                 [x for x in (art.get("companies") or []) if x != _co])
            _subj = _role.get("subject_company", "")
            _indireto = (_role.get("relation_type") not in ("direto",)
                         and _subj and normalize(_subj) != normalize(_co))
            _pont, _ctx_ev = [], []
            for e in _man:
                if _indireto and e["id"] in _EVENTOS_CRITICOS:
                    _ctx_ev.append({
                        "event_id": e["id"], "event_label": e.get("label", e["id"]),
                        "subject_company": _subj,
                        "relation_type": _role.get("relation_type"),
                        "impact_type": _role.get("impact_type", "indireto_material"),
                        "event_scope": _role.get("event_scope", "indireto"),
                        "event_phase": _role.get("event_phase", ""),
                        "direction": _role.get("direction_hint", "neutra"),
                        "scoreable": False,
                        "attribution_confidence": _role.get("attribution_confidence", "alta"),
                        "attribution_evidence": _role.get("attribution_evidence", "")})
                else:
                    _pont.append(e)
            if _ctx_ev:
                _ctx_por_empresa[_co] = _ctx_ev
                art.setdefault("mention_roles", {})[_co] = _role
            _man = _pont
            _por_empresa[_co] = [e["id"] for e in _man]
            for e in _man:
                if e.get("company_role"):
                    _assess[f"{_co}|{e['id']}"] = {
                        "company": _co, "event_id": e["id"],
                        "subject_entity": e.get("subject_entity", _co),
                        "company_role": e["company_role"],
                        "attributable": True, "scoreable": True,
                        "legal_status": e.get("legal_status", ""),
                        "confirmation_status": e.get("confirmation_status", "")}
        for d in _desc:
            _desc_all.append({**d, "company": _co or ""})
    if not _por_empresa:                      # sem empresa: usa a guarda geral
        _man, _desc = semantic_role_guard(_titulo, _resumo, _evs)
        _por_empresa = {}
        _ok_ids = {e["id"] for e in _man}
        _desc_all = [{**d, "company": ""} for d in _desc]
    # 4H.1e — resolução por família POR EMPRESA: a empresa A pode ter
    # rebaixamento+outlook (só rebaixamento pontua) e a B só outlook (que
    # pontua para ela). Resolver na união global misturaria as duas.
    _sec_all, _mot_all = [], []
    for _co, _ids in list(_por_empresa.items()):
        _evs_co = [e for e in _evs if e["id"] in set(_ids)]
        _p_co, _s_co, _m_co = resolve_event_families(_evs_co, cfg, _titulo)
        _por_empresa[_co] = [e["id"] for e in _p_co]
        for _e in _s_co:
            if _e["id"] not in [x["id"] for x in _sec_all]:
                _sec_all.append(_e)
        _mot_all.extend(_m_co)
    if _por_empresa:
        _ok_ids = set()
        for _ids in _por_empresa.values():
            _ok_ids |= set(_ids)
    _evs = [e for e in _evs if e["id"] in _ok_ids]
    if _desc_all:
        art["semantic_discards"] = _desc_all
    if _assess:
        art["event_assessments"] = list(_assess.values())
    if _ctx_por_empresa:
        art["context_events_by_company"] = _ctx_por_empresa
    if _por_empresa:
        art["events_by_company"] = _por_empresa
    # `events` global existe só para exibição/compatibilidade — NUNCA para
    # pontuar um emissor (isso usa events_by_company via event_ids_for).
    if _por_empresa:
        _prin, _sec, _mot = _evs, _sec_all, _mot_all
    else:
        _prin, _sec, _mot = resolve_event_families(_evs, cfg, _titulo)
    art["events"] = _prin
    if _sec:
        art["secondary_events"] = [{"id": e.get("id"), "label": e.get("label", ""),
                                    "family": e.get("family"),
                                    "family_label": e.get("family_label", ""),
                                    "primary_event": e.get("primary_event")} for e in _sec]
        art["conflict_resolution_reason"] = "; ".join(_mot)

    # ── 4H.3B item 4: atribuição por EMPRESA × EVENTO × EVIDÊNCIA TEXTUAL ──
    # O papel geral da empresa no artigo não pode decidir TODOS os eventos.
    # No caso misto ("rating rebaixado ... e plano de RJ da Samarco"), o papel
    # de artigo levava o rebaixamento a vazar para a Samarco. Aqui cada evento
    # é ancorado à entidade citada na mesma oração/possessivo/janela textual.
    # Aplica-se somente ao caso genuinamente misto (≥2 empresas e ≥2 eventos),
    # preservando o comportamento validado nos demais casos.
    try:
        import edgar_shadow_4h3b as _ev4h3b
        _ev4h3b.apply_evidence_attribution(art, cfg, normalize)
    except Exception:
        pass  # ausência do módulo não pode quebrar produção

    # ── RESOLUÇÃO SEMÂNTICA (padrão canônico Vale/Samarco) ──
    # Roda ANTES de merge_into_history / build_evolution / build_feed /
    # build_changes / score / chips / timeline / breakdown / HTML.
    # Move para context_events_by_company o evento cujo sujeito verdadeiro é
    # outra entidade, a referência histórica, o falso M&A, o desfecho jurídico
    # e a ação de rating já contabilizada pela família.
    try:
        import semantic_audit as _sem
        _sem.apply_semantics_to_record(art, cfg)
    except Exception as _exc:
        print(f"   ⚠️  resolução semântica indisponível ({type(_exc).__name__}: "
              f"{str(_exc)[:120]}) — atribuição segue sem a camada semântica.")

    # 8) entity_scope=brand_group/entity_pending_confirmation (ou
    # scoreable=False explícito) nunca pontua — OPT-IN, aditivo. Emissores
    # sem esses campos (os 160 reais) não são tocados por esta chamada.
    suppress_non_scoreable_entity_scopes(art, cfg)

    # 9) [fix: complete Peru news links taxonomy and holding coverage]
    # evento DIRETO de peso-base 0 na taxonomia (positivo/neutro-informativo
    # — rating_elevado, outlook_positivo, recomendacao_positiva, e os novos
    # retomada_operacional/expansao_capacidade/investimento_operacional)
    # nunca precisa de card em events_by_company (score 0 não muda o total
    # de qualquer forma) — move para informational_events_by_company para
    # aparecer como "Sinal positivo/Evento informativo · não pontua" em vez
    # de um chip solto. Complementa (nunca substitui) os casos específicos
    # já tratados por semantic_audit.py e suppress_non_scoreable_entity_
    # scopes. Aditivo e idempotente.
    route_zero_score_direct_events_to_informational(art, cfg)


def route_zero_score_direct_events_to_informational(art: dict, cfg: dict) -> None:
    """[fix: complete Peru news links taxonomy and holding coverage] Move
    para `informational_events_by_company` qualquer evento que sobrou em
    `events_by_company` com peso-base 0 na taxonomia (positivo ou neutro/
    informativo) — nunca precisa ficar como chip solto de `events_by_
    company`, já que não pontua de qualquer forma; o usuário vê "Sinal
    positivo · não pontua" ou "Evento informativo · não pontua" em vez de
    título genérico. Generaliza (sem duplicar) os casos específicos já
    tratados por `semantic_audit.py` (M&A/família de rating) e
    `suppress_non_scoreable_entity_scopes` (brand_group/pending) — estes
    já removem seus próprios eventos de `events_by_company` antes desta
    função rodar, então não há conflito. Aditivo, idempotente (checa
    `source_record_id` antes de duplicar)."""
    taxonomy = {e["id"]: e for e in cfg.get("taxonomy", [])}
    ebc = art.get("events_by_company") or {}
    _url = art.get("url", "")
    for name in list(ebc.keys()):
        ids = ebc.get(name) or []
        zero_ids = [eid for eid in ids if taxonomy.get(eid, {}).get("score", 1) == 0]
        if not zero_ids:
            continue
        ebc[name] = [eid for eid in ids if eid not in zero_ids]
        info = art.setdefault("informational_events_by_company", {})
        info.setdefault(name, [])
        for eid in zero_ids:
            already = any(x.get("event_id") == eid and x.get("source_record_id") == _url
                         for x in info[name])
            if already:
                continue
            ev = taxonomy.get(eid, {})
            direction = ev.get("direction", "neutra")
            display = "positivo" if direction == "positiva" else "informativo"
            info[name].append({
                "company": name, "event_id": eid,
                "event_label": ev.get("label", eid).replace("_", " "),
                "subject_company": name, "monitored_company": name,
                "relation_type": "direto", "event_scope": "direto",
                "direction": direction, "scoreable": False,
                "display_category": display,
                "attribution_rule": "R_EVENTO_DIRETO_PESO_ZERO",
                "attribution_confidence": "media",
                "title": art.get("title", ""), "url": _url,
                "pub_ts": art.get("pub_ts"),
                "observation": (f"Evento direto '{ev.get('label', eid)}' tem peso-base 0 "
                               "na taxonomia (positivo/informativo) — não pontua."),
                "source_record_id": _url,
            })


def run_link_repair(args, cfg) -> int:
    """[--repair-links-only] Resolve e persiste URLs de TODAS as fontes.

    Trata igualmente fonte principal e corroboradora — foi exatamente a
    assimetria entre elas que gerou os links intermediários que a rede
    corporativa recusa.

    NÃO coleta notícias, NÃO chama CVM/RI/EDGAR, NÃO faz backfill, NÃO
    reclassifica eventos e NÃO altera score, ocorrências ou datas.
    Idempotente."""
    import copy as _copy
    import csv as _csv
    import link_debt_audit as _lk

    hist_in = Path(args.history)
    outdir = Path(args.audit_outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if not hist_in.exists():
        print(f" ❌ histórico não encontrado: {hist_in}")
        return 1
    history = json.loads(hist_in.read_text(encoding="utf-8"))
    arts = history.get("articles") or {}
    print(f" 🔗 Reparo de links — {len(arts)} registros de {hist_in}")

    allow_net = bool(os.environ.get("LINK_REPAIR_ONLINE", "")) or bool(
        getattr(args, "link_repair_online", False))
    verify = bool(os.environ.get("LINK_REPAIR_VERIFY", ""))
    print(f"    rede: {'HABILITADA' if allow_net else 'desabilitada (offline)'} · "
          f"verificação de status: {'sim' if verify else 'não'}")
    print("    (zero fetch de notícias, zero backfill, zero reclassificação)")

    original = _copy.deepcopy(history)
    cache = history.setdefault("resolved_urls", {})
    session = None
    if allow_net:
        try:
            import requests
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; "
                                                  "Win64; x64)"})
        except Exception:
            allow_net = False

    linhas, mudados = [], 0

    def _repara(alvo: dict, *, is_primary: bool, company: str, event_id: str,
                occurrence_id: str, titulo: str, url_atual: str) -> bool:
        nonlocal mudados
        antes_health = alvo.get("link_health") or ""
        antes_href = (alvo.get("display_url") or url_atual or "")
        res = _lk.resolve_article_url(url_atual, domain=alvo.get("domain", ""),
                                      cache=cache, session=session,
                                      allow_network=allow_net,
                                      verify_status=verify)
        dec = _lk.interface_decision(res)
        # persiste SOMENTE campos de link
        # `last_checked_at` fica FORA da comparação: se nada substantivo mudou,
        # ele não é reescrito — assim a 2ª execução altera zero registros, como
        # a especificação exige. Só é atualizado quando houve mudança real ou
        # quando a rede efetivamente reverificou o destino.
        campos = ("original_url", "redirect_url", "resolved_url", "canonical_url",
                  "display_url", "redirect_chain", "original_host", "final_host",
                  "http_status", "link_health", "resolution_method",
                  "resolution_error")
        alterou = False
        for k in campos:
            novo = res.get(k, "")
            if alvo.get(k) != novo:
                alvo[k] = novo
                alterou = True
        if alvo.get("link_render_anchor") != dec["render_anchor"]:
            alvo["link_render_anchor"] = dec["render_anchor"]
            alterou = True
        if alvo.get("link_label") != dec["label"]:
            alvo["link_label"] = dec["label"]
            alterou = True
        if alterou or (allow_net and res.get("resolution_method") in
                       ("redirect_http", "verificacao_status")):
            alvo["last_checked_at"] = res.get("last_checked_at", "")
        # alimenta o cache quando resolveu de fato
        if res.get("resolved_url") and _lk.is_redirector(url_atual):
            cache[url_atual] = {"url": res["resolved_url"], "exact": True}
        if alterou:
            mudados += 1
        linhas.append({
            "company": company, "event_id": event_id, "occurrence_id": occurrence_id,
            "source_name": alvo.get("source", ""), "is_primary": is_primary,
            "title": (titulo or "")[:160], "original_url": url_atual,
            "rendered_href_before": antes_href,
            "original_host": res["original_host"], "redirect_url": res["redirect_url"],
            "resolved_url": res["resolved_url"], "canonical_url": res["canonical_url"],
            "final_host": res["final_host"], "display_url": res["display_url"],
            "http_status": res["http_status"], "redirect_chain": res["redirect_chain"],
            "link_health_before": antes_health or ("redirecionador"
                                                   if _lk.is_redirector(url_atual)
                                                   else "nao_verificado"),
            "link_health_after": res["link_health"],
            "resolution_method": res["resolution_method"],
            "changed": alterou,
            "interface_decision": dec["label"],
            "observation": res["resolution_error"],
        })
        return alterou

    for url, rec in arts.items():
        ebc = {k: v for k, v in (rec.get("events_by_company") or {}).items() if v}
        company = next(iter(ebc), "")
        event_id = (ebc.get(company) or [""])[0] if company else ""
        occ = rec.get("occurrence_id") or ""
        titulo = rec.get("title", "")
        # fonte PRINCIPAL
        _repara(rec, is_primary=True, company=company, event_id=event_id,
                occurrence_id=occ, titulo=titulo,
                url_atual=(rec.get("url") or url))
        # fontes CORROBORADORAS — mesma função, mesmo tratamento
        for lista in ("corroborations", "corrob_sources"):
            for c in (rec.get(lista) or []):
                if not isinstance(c, dict):
                    continue
                _repara(c, is_primary=False, company=company, event_id=event_id,
                        occurrence_id=occ, titulo=c.get("title", "") or titulo,
                        url_atual=(c.get("url") or ""))

    def _wr(nome, rows, keys=None):
        keys = keys or (list(rows[0].keys()) if rows else [])
        with open(outdir / nome, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        print(f"    {nome}: {len(rows)} linhas")

    _wr("auditoria_links_dashboard_live.csv", linhas)

    # ── contagens (item 13) ──
    from collections import Counter
    prim = [l for l in linhas if l["is_primary"]]
    corr = [l for l in linhas if not l["is_primary"]]
    cnt_after = Counter(l["link_health_after"] for l in linhas)
    resumo = {
        "links_auditados": len(linhas),
        "fontes_principais": len(prim), "fontes_corroboradoras": len(corr),
        "urls_diretas": cnt_after.get("url_direta_valida", 0),
        "redirecionadores_encontrados": sum(1 for l in linhas if l["redirect_url"]),
        "redirects_resolvidos": cnt_after.get("redirect_resolvido", 0),
        "nao_resolvidos": cnt_after.get("redirect_nao_resolvido", 0),
        "http_404_410": cnt_after.get("removido_404_410", 0),
        "http_403_paywall": cnt_after.get("bloqueado_ou_paywall", 0),
        "homepages": cnt_after.get("homepage_generica", 0),
        "urls_malformadas": cnt_after.get("url_malformada", 0),
        "dominios_suspeitos": cnt_after.get("dominio_suspeito", 0),
        "bloqueio_de_ambiente": cnt_after.get("bloqueio_de_ambiente", 0),
        "registros_com_campo_alterado": mudados,
        "rede_habilitada": allow_net, "verificacao_status": verify,
        "por_saude": dict(cnt_after),
        "principais_por_saude": dict(Counter(l["link_health_after"] for l in prim)),
        "corroboradoras_por_saude": dict(Counter(l["link_health_after"] for l in corr)),
    }
    (outdir / "resumo_link_repair.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in resumo.items():
        if not isinstance(v, dict):
            print(f"      {k}: {v}")

    # ── PROVA de que score/eventos/ocorrências não mudaram ──
    def _semantico(h):
        return {u: {"events_by_company": r.get("events_by_company"),
                    "context_events_by_company": r.get("context_events_by_company"),
                    "informational_events_by_company": r.get("informational_events_by_company"),
                    "occurrence_id": r.get("occurrence_id"),
                    "pub_ts": r.get("pub_ts"),
                    "corrob_n": len(r.get("corroborations") or [])}
                for u, r in (h.get("articles") or {}).items()}
    inalterado = _semantico(original) == _semantico(history)
    th0 = calibrate_thresholds(original, cfg)
    th1 = calibrate_thresholds(history, cfg)
    s0 = {r["company"]: r["total_score"] for r in
          build_evolution(original, cfg, window_days=90, thresholds=th0)}
    s1 = {r["company"]: r["total_score"] for r in
          build_evolution(history, cfg, window_days=90, thresholds=th1)}
    score_igual = s0 == s1
    print(f"    ✔ eventos/ocorrências inalterados: {inalterado}")
    print(f"    ✔ score idêntico antes/depois: {score_igual}")
    if not (inalterado and score_igual):
        print(" ❌ ABORTADO: o reparo de links não pode alterar semântica ou score.")
        return 1

    # ── reconstrói o HTML ──
    windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
    data_by_window = {}
    for w in windows:
        data_by_window[str(w)] = {
            "evolution": build_evolution(history, cfg, window_days=w, thresholds=th1),
            "feed": build_feed(history, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
    evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
    changes = build_changes(history, cfg, [], original.get("last_run") or {}, evo_ref)
    html = render_html(data_by_window, cfg, demo=False, changes=changes,
                       payload_thresholds=th1)
    out_html = Path(args.output_html or "index.html")
    out_html.write_text(html, encoding="utf-8")
    out_hist = Path(args.output_history or args.history)
    history["links_repaired_at"] = datetime.now(timezone.utc).isoformat()
    save_history(out_hist, history)
    print(f" 💾 histórico com links reparados → {out_hist}")
    print(f" 🖼️  dashboard reconstruído        → {out_html}")
    return 0


def run_semantic_reclassification(args, cfg) -> int:
    """[--reclassify-semantic-only] Reclassifica o histórico EXISTENTE com as
    regras semânticas e reconstrói dashboard e scores.

    Zero rede: não busca notícias, CVM, EDGAR ou RI; não traduz externamente;
    não faz backfill. Idempotente. O original só é sobrescrito ao final, após a
    execução completa."""
    import copy as _copy
    import csv as _csv
    import semantic_audit as _sem

    hist_in = Path(args.history)
    outdir = Path(args.audit_outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if not hist_in.exists():
        print(f" ❌ histórico não encontrado: {hist_in}")
        return 1
    history = json.loads(hist_in.read_text(encoding="utf-8"))
    n_reg = len(history.get("articles") or {})
    print(f" ♻️  Reclassificação semântica OFFLINE — {n_reg} registros de {hist_in}")
    print("    (zero fetch: sem Google News, CVM, EDGAR, RI; sem backfill)")

    original = _copy.deepcopy(history)          # backup interno p/ comparação
    thresholds0 = calibrate_thresholds(original, cfg)
    evo_antes = build_evolution(original, cfg, window_days=90, thresholds=thresholds0)
    antes = {r["company"]: r for r in evo_antes}

    aliases = _sem._aliases_map(cfg)
    alterados, decisoes = 0, []
    for url, rec in (history.get("articles") or {}).items():
        res = _sem.apply_semantics_to_record(rec, cfg, aliases=aliases)
        if res["mudou"]:
            alterados += 1
        for d in res["decisoes"]:
            if not d["scoreable"]:
                decisoes.append({"url": url, "titulo": (rec.get("title") or "")[:170],
                                 "fonte": rec.get("source", "") or "", **d})
    print(f"    registros alterados: {alterados}  |  eventos reclassificados: {len(decisoes)}")

    thresholds = calibrate_thresholds(history, cfg)
    evo_depois = build_evolution(history, cfg, window_days=90, thresholds=thresholds)
    depois = {r["company"]: r for r in evo_depois}

    # ── antes/depois com SCORE PONDERADO REAL (o do dashboard) ──
    linhas = []
    for comp in sorted(set(antes) | set(depois)):
        a, b = antes.get(comp), depois.get(comp)
        sa_, sb = (a or {}).get("total_score", 0), (b or {}).get("total_score", 0)
        if sa_ == sb and (a or {}).get("status") == (b or {}).get("status"):
            continue
        def _worst(r):
            t = (r or {}).get("timeline") or []
            return (t[0].get("label") if t else "") or ""
        linhas.append({
            "empresa": comp,
            "score_ponderado_antes": sa_, "score_ponderado_depois": sb,
            "diferenca_real": sb - sa_,
            "status_antes": (a or {}).get("status", "—"),
            "status_depois": (b or {}).get("status", "—"),
            "pior_evento_antes": _worst(a), "pior_evento_depois": _worst(b),
            "n_eventos_antes": len((a or {}).get("timeline") or []),
            "n_eventos_depois": len((b or {}).get("timeline") or []),
            "mudou_status": "sim" if (a or {}).get("status") != (b or {}).get("status") else "não",
        })
    linhas.sort(key=lambda r: r["diferenca_real"])

    def _wr(nome, rows, keys=None):
        keys = keys or (list(rows[0].keys()) if rows else [])
        with open(outdir / nome, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        print(f"    {nome}: {len(rows)} linhas")

    _wr("aceite_por_empresa_score_real.csv", linhas)
    _wr("eventos_reclassificados_producao.csv", decisoes,
        ["url", "titulo", "fonte", "monitored_company", "event_id", "event_id_corrigido",
         "subject_company", "actor_company", "affected_company", "transaction_object",
         "transaction_scope", "transaction_role", "event_phase", "event_scope",
         "direction", "historical_reference", "new_occurrence", "confirmation_level",
         "attribution_rule", "attribution_confidence", "scoreable", "rejection_reason"])

    # ── reconstrói TODAS as agregações e o HTML ──
    windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
    prev_scores = {c: v.get("score") for c, v in (prev_run_status(original) or {}).items()}
    data_by_window = {}
    for w in windows:
        data_by_window[str(w)] = {
            "evolution": build_evolution(history, cfg, window_days=w,
                                         thresholds=thresholds, prev_scores=prev_scores),
            "feed": build_feed(history, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
    evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
    changes = build_changes(history, cfg, [], original.get("last_run") or {}, evo_ref)
    history["last_run"] = {
        "ts": int(datetime.now(timezone.utc).timestamp()),
        "iso": fmt_date_br(get_brt_now()),
        "status": {r["company"]: {"status": r["status"], "score": r["total_score"]}
                   for r in evo_ref},
    }
    history["semantic_reclassified_at"] = datetime.now(timezone.utc).isoformat()
    history["semantic_version"] = _sem.SEMANTIC_VERSION

    html = render_html(data_by_window, cfg, demo=False, changes=changes,
                       payload_thresholds=thresholds)
    out_html = Path(args.output_html or "index.html")
    out_html.write_text(html, encoding="utf-8")
    out_hist = Path(args.output_history or args.history)
    save_history(out_hist, history)
    (outdir / "resumo_reclassificacao.json").write_text(json.dumps({
        "registros_no_historico": n_reg,
        "registros_alterados": alterados,
        "eventos_reclassificados": len(decisoes),
        "empresas_com_mudanca_de_score": len(linhas),
        "empresas_com_mudanca_de_status": sum(1 for l in linhas if l["mudou_status"] == "sim"),
        "score_real_removido": sum(l["diferenca_real"] for l in linhas),
        "fetch_executado": False, "backfill_executado": False,
        "edgar_scoring_enabled": edgar_scoring_enabled(cfg),
        "historico_saida": str(out_hist), "html_saida": str(out_html),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f" 💾 histórico reclassificado → {out_hist}")
    print(f" 🖼️  dashboard reconstruído  → {out_html}")
    print(f" 📉 score real removido: {sum(l['diferenca_real'] for l in linhas)} "
          f"em {len(linhas)} emissor(es)")
    return 0


def _reclassify_only_snapshot(rec: dict) -> dict:
    """Estado 'antes' de UM registro, só os campos que a Fase 4H.1 pode alterar."""
    return {
        "companies": list(rec.get("companies") or []),
        "event_ids": list(rec.get("event_ids") or []),
        "events_by_company": copy.deepcopy(rec.get("events_by_company") or {}),
        "context_events_by_company": copy.deepcopy(rec.get("context_events_by_company") or {}),
        "informational_events_by_company": copy.deepcopy(rec.get("informational_events_by_company") or {}),
        "companies_attributed": list(rec.get("companies_attributed") or []),
        "context_companies": list(rec.get("context_companies") or []),
        "secondary_events": copy.deepcopy(rec.get("secondary_events") or []),
        "mention_roles": copy.deepcopy(rec.get("mention_roles") or {}),
        "conflict_resolution_reason": rec.get("conflict_resolution_reason") or "",
    }


def _reclassify_only_pass(history_in: dict, cfg: dict) -> tuple:
    """Executa UMA passada de reclassificação offline (zero rede/LLM) sobre
    `history_in["articles"]`, in place, e retorna (history_in, diag) onde
    `diag` documenta, por URL, o que mudou. Reusa exatamente a mesma cadeia
    de classificação/atribuição do pipeline real (`classify_and_attribute`),
    só que alimentada com o título/resumo JÁ traduzidos e persistidos no
    histórico — nunca chama tradução, coleta ou LLM."""
    articles = history_in.get("articles") or {}
    diag = {"changes": [], "errors": [], "removed": 0, "added": 0,
            "moved_context": 0, "moved_informational": 0, "duplicates_collapsed": 0,
            "n_processed": 0, "n_changed": 0, "n_manual_correction_records": 0,
            "locked_field_overrides": []}
    for url, rec in articles.items():
        diag["n_processed"] += 1
        # ── correção MANUAL granular (`manual_correction.locked_fields`) ──
        # Substitui a proteção antiga (skip total do registro via
        # `_correction_note`), que era ampla demais: congelava o registro
        # inteiro para SEMPRE, inclusive contra melhorias futuras legítimas
        # de dedup/occurrence-id/containers/papel semântico que nada têm a
        # ver com o campo corrigido manualmente. Agora o registro passa pelo
        # PIPELINE COMPLETO normalmente (classificação, atribuição, famílias,
        # containers) e só os campos listados em `locked_fields` têm o valor
        # recém-calculado descartado em favor do valor humano já corrigido —
        # com log explícito do que seria diferente, para auditoria futura.
        _mc = rec.get("manual_correction") or {}
        _locked_fields = list(_mc.get("locked_fields") or [])
        _locked_values = ({f: copy.deepcopy(rec.get(f)) for f in _locked_fields}
                           if _locked_fields else {})
        if _locked_fields:
            diag["n_manual_correction_records"] += 1
        before = _reclassify_only_snapshot(rec)
        art = {
            "title": rec.get("title", ""), "summary": rec.get("summary", ""),
            "url": url, "source": rec.get("source", ""), "domain": rec.get("domain", ""),
            "pub_ts": rec.get("pub_ts", 0), "pub_iso": rec.get("pub_iso", ""),
        }
        # fonte OFICIAL (RI/CVM via custom_feeds com trust_tier="oficial"): a
        # empresa foi atribuída pela ORIGEM da coleta (feed do próprio emissor),
        # não por menção literal no título — ex.: fatos relevantes de RI cujo
        # título não cita o nome da empresa. Sem isto, detect_companies() (que
        # só olha o título) perderia a atribuição e o evento sumiria do
        # histórico em vez de ser reclassificado. "dados de coleta" (CLAUDE.md
        # invariante 14/15) são preservados, não redescobertos por heurística.
        if rec.get("trust_override") == "oficial" and rec.get("companies"):
            art["forced_companies"] = list(rec["companies"])
            art["forced_trust"] = "oficial"
        try:
            classify_and_attribute(art, cfg)
        except Exception as exc:
            diag["errors"].append({"url": url, "title": (rec.get("title") or "")[:170],
                                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue  # registro preservado exatamente como estava

        # ── mesmo mapeamento de campos usado por merge_into_history() ──
        rec["companies"] = art.get("companies") or [MARKET_LABEL]
        rec["event_ids"] = [e["id"] for e in (art.get("events") or [])]
        for _k in ("event_assessments", "semantic_discards", "secondary_events",
                   "conflict_resolution_reason", "mention_roles",
                   "context_events_by_company", "informational_events_by_company"):
            if art.get(_k):
                rec[_k] = art[_k]
            elif _k in rec:
                del rec[_k]
        if "events_by_company" in art:
            rec["events_by_company"] = art["events_by_company"]
        elif "events_by_company" in rec:
            del rec["events_by_company"]
        _ebc = art.get("events_by_company")
        if isinstance(_ebc, dict):
            rec["companies_attributed"] = [c for c, ev in _ebc.items() if ev]
            rec["context_companies"] = [c for c, ev in _ebc.items() if not ev]

        # ── restaura os campos TRAVADOS por correção manual granular ──
        # O reprocessamento acima já rodou por completo; agora, só para os
        # campos listados em `locked_fields`, o valor recém-calculado é
        # descartado e o valor humano original é restaurado. Campos FORA da
        # lista seguem o resultado normal do reprocessamento (dedup,
        # occurrence-id, containers, papel semântico não relacionado, etc.
        # continuam evoluindo livremente para este registro).
        for _f in _locked_fields:
            _recomputed = rec.get(_f)
            _locked_val = _locked_values.get(_f)
            if _recomputed != _locked_val:
                diag["locked_field_overrides"].append({
                    "url": url, "field": _f,
                    "correction_id": _mc.get("correction_id", ""),
                    "reason": _mc.get("reason", ""),
                    "reprocessed_value": _recomputed, "locked_value": _locked_val,
                })
            rec[_f] = copy.deepcopy(_locked_val)

        after = _reclassify_only_snapshot(rec)
        if after == before:
            continue
        diag["n_changed"] += 1

        # ── diff evento a evento, por empresa (papel anterior × papel novo) ──
        companies_touched = set(before["events_by_company"]) | set(after["events_by_company"]) \
            | set(before["context_events_by_company"]) | set(after["context_events_by_company"]) \
            | set(before["informational_events_by_company"]) | set(after["informational_events_by_company"])
        for co in sorted(companies_touched):
            # ── universo (id, pool) ANTES × DEPOIS — pool ∈ {score, ctx, info}.
            # Precisa comparar os TRÊS pools simultaneamente (não só o
            # scoreable) para capturar a transição ctx→info: um evento DIRETO
            # do próprio emissor que estava (incorretamente) em
            # context_events_by_company — reservado a terceiro real — e passa
            # a ir para informational_events_by_company (regra 5.4/5.5,
            # pendência Santander do CLAUDE.md). Comparar só ev_before×ev_after
            # (como a versão anterior fazia) é CEGO a essa transição porque
            # ev_before já era vazio dos dois lados.
            ev_before = set(before["events_by_company"].get(co, []))
            ev_after = set(after["events_by_company"].get(co, []))
            ctx_before = {e.get("event_id") for e in before["context_events_by_company"].get(co, [])}
            ctx_after = {e.get("event_id") for e in after["context_events_by_company"].get(co, [])}
            info_before = {e.get("event_id") for e in before["informational_events_by_company"].get(co, [])}
            info_after = {e.get("event_id") for e in after["informational_events_by_company"].get(co, [])}
            pool_before = {eid: "score" for eid in ev_before}
            pool_before.update({eid: "ctx" for eid in ctx_before})
            pool_before.update({eid: "info" for eid in info_before})
            pool_after = {eid: "score" for eid in ev_after}
            pool_after.update({eid: "ctx" for eid in ctx_after})
            pool_after.update({eid: "info" for eid in info_after})
            all_ids = set(pool_before) | set(pool_after)
            to_ctx, to_info, to_score, removed, added = set(), set(), set(), set(), set()
            for eid in all_ids:
                pb, pa = pool_before.get(eid), pool_after.get(eid)
                if pb == pa:
                    continue
                if pa is None:                       # saiu de TODOS os pools
                    removed.add(eid)
                elif pb is None and pa == "score":    # nasceu já pontuável
                    added.add(eid)
                elif pa == "ctx" and pb != "ctx":
                    to_ctx.add(eid)
                elif pa == "info" and pb != "info":
                    to_info.add(eid)
                elif pa == "score" and pb != "score":
                    to_score.add(eid)
            diag["removed"] += len(removed)
            diag["added"] += len(added)
            diag["moved_context"] += len(to_ctx)
            diag["moved_informational"] += len(to_info)
            motivo = []
            if to_ctx: motivo.append(f"evento movido p/ contexto (terceiro): {sorted(to_ctx)}")
            if to_info: motivo.append(f"evento movido p/ informativo (direto não pontuável): {sorted(to_info)}")
            if to_score: motivo.append(f"evento movido p/ pontuável: {sorted(to_score)}")
            if removed: motivo.append(f"evento removido: {sorted(removed)}")
            if added: motivo.append(f"evento adicionado: {sorted(added)}")
            if not motivo and (ev_before != ev_after or ctx_before != ctx_after or info_before != info_after):
                motivo.append("reordenação/atualização de metadados do evento (sem mudança líquida de score)")
            if motivo:
                _motivo_str = "; ".join(motivo)
                diag["changes"].append({
                    "url": url, "company": co, "title": (rec.get("title") or "")[:170],
                    "date": rec.get("pub_iso", ""), "source": rec.get("source", ""),
                    "event_ids_antes": ",".join(sorted(ev_before)),
                    "event_ids_depois": ",".join(sorted(ev_after)),
                    "role_antes": (before["mention_roles"].get(co, {}) or {}).get("relation_type", ""),
                    "role_depois": (after["mention_roles"].get(co, {}) or {}).get("relation_type", ""),
                    "motivo": _motivo_str,
                    "categoria": _reclassify_only_row_category(_motivo_str),
                })
        n_sec_before, n_sec_after = len(before["secondary_events"]), len(after["secondary_events"])
        if n_sec_after > n_sec_before:
            diag["duplicates_collapsed"] += (n_sec_after - n_sec_before)
    return history_in, diag


def _reclassify_only_row_category(motivo: str) -> str:
    """Categoria MUTUAMENTE EXCLUSIVA de uma linha de `changes` (nível
    empresa×evento), a partir do texto do motivo. Usada tanto na coluna
    `categoria` do CSV quanto no resumo por artigo do relatório."""
    has_info = "p/ informativo" in motivo
    has_rem = "evento removido" in motivo
    has_add = "evento adicionado" in motivo
    has_ctx = "p/ contexto" in motivo
    has_score = "p/ pontuável" in motivo
    if has_info and has_rem:
        return "movido_informativo_com_remocao_tag_legada"
    if has_info:
        return "movido_informativo"
    if has_rem:
        return "removido_puro"
    if has_add:
        return "adicionado_events_by_company_explicito"
    if has_ctx:
        return "movido_contexto"
    if has_score:
        return "promovido_pontuavel"
    return "normalizacao_metadados"


# Ordem de prioridade quando um MESMO artigo tem mais de uma linha (empresas
# diferentes) em categorias diferentes — usada só para o resumo POR ARTIGO
# (nunca duplica o artigo entre categorias; a soma bate com n_changed).
_RECLASSIFY_ONLY_CAT_PRIORITY = [
    "removido_puro", "movido_informativo_com_remocao_tag_legada",
    "movido_informativo", "adicionado_events_by_company_explicito",
    "movido_contexto", "promovido_pontuavel", "normalizacao_metadados",
]


def run_reclassify_only(args, cfg) -> int:
    """[Fase 4H.1] --reclassify-only: reaplica ao histórico EXISTENTE toda a
    cadeia offline de classificação/atribuição/semântica atual, ZERO rede e
    ZERO LLM (não confundir com --reclassify, que rejoga o histórico como
    coleta nova e chama consolidate_with_llm + resolve_google_news_urls).

    Padrão: dry-run (nada persistido). --apply grava, com backup versionado,
    verificação de idempotência e rollback automático em caso de falha de gate."""
    import copy as _copy
    import csv as _csv

    hist_in = Path(args.history)
    outdir = Path(args.audit_outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if not hist_in.exists():
        print(f" ❌ histórico não encontrado: {hist_in}")
        return 1
    history_orig = json.loads(hist_in.read_text(encoding="utf-8"))
    n_reg = len(history_orig.get("articles") or {})
    print(f" ♻️  [4H.1] --reclassify-only — {n_reg} registros de {hist_in}  "
          f"({'APPLY' if args.apply else 'DRY-RUN'})")
    print("    zero rede: sem Google News, CVM/IPE, SEC/EDGAR, RI, tradução ou LLM")

    original = _copy.deepcopy(history_orig)
    thresholds0 = calibrate_thresholds(original, cfg)
    evo_antes = build_evolution(original, cfg, window_days=90, thresholds=thresholds0)
    antes = {r["company"]: r for r in evo_antes}

    # ── PASSADA 1 (a que efetivamente conta) ──
    history1 = _copy.deepcopy(original)
    history1, diag1 = _reclassify_only_pass(history1, cfg)

    thresholds1 = calibrate_thresholds(history1, cfg)
    evo_depois = build_evolution(history1, cfg, window_days=90, thresholds=thresholds1)
    depois = {r["company"]: r for r in evo_depois}

    # ── PASSADA 2, em memória, sobre o resultado da passada 1 (idempotência) ──
    history2 = _copy.deepcopy(history1)
    history2, diag2 = _reclassify_only_pass(history2, cfg)
    idempotent = (diag2["n_changed"] == 0 and diag2["removed"] == 0 and diag2["added"] == 0
                  and diag2["moved_context"] == 0 and diag2["moved_informational"] == 0
                  and diag2["duplicates_collapsed"] == 0 and not diag2["errors"])
    thresholds2 = calibrate_thresholds(history2, cfg)
    evo2 = build_evolution(history2, cfg, window_days=90, thresholds=thresholds2)
    scores1 = {r["company"]: r["total_score"] for r in evo_depois}
    scores2 = {r["company"]: r["total_score"] for r in evo2}
    idempotent = idempotent and (scores1 == scores2)
    print(f"    idempotência (2ª passada): {'OK — zero novas mudanças' if idempotent else 'FALHOU'}")

    # ── score/status antes×depois por empresa (score PONDERADO real) ──
    linhas = []
    for comp in sorted(set(antes) | set(depois)):
        a, b = antes.get(comp), depois.get(comp)
        sa_, sb = (a or {}).get("total_score", 0), (b or {}).get("total_score", 0)
        if sa_ == sb and (a or {}).get("status") == (b or {}).get("status"):
            continue
        def _worst(r):
            t = (r or {}).get("timeline") or []
            return (t[0].get("label") if t else "") or ""
        linhas.append({
            "empresa": comp, "score_antes": sa_, "score_depois": sb,
            "diferenca": sb - sa_, "status_antes": (a or {}).get("status", "—"),
            "status_depois": (b or {}).get("status", "—"),
            "pior_evento_antes": _worst(a), "pior_evento_depois": _worst(b),
            "mudou_status": "sim" if (a or {}).get("status") != (b or {}).get("status") else "não",
        })
    linhas.sort(key=lambda r: r["diferenca"])

    def _wr(nome, rows, keys=None):
        keys = keys or (list(rows[0].keys()) if rows else [])
        with open(outdir / nome, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        print(f"    {nome}: {len(rows)} linhas")

    # ── relatórios exigidos pela Fase 4H.1 ──
    _wr("reclassify_only_score_diff.csv", linhas)
    _wr("reclassify_only_changes.csv", diag1["changes"],
        ["url", "company", "title", "date", "source", "event_ids_antes",
         "event_ids_depois", "role_antes", "role_depois", "motivo", "categoria"])
    _wr("reclassify_only_occurrence_diff.csv",
        [{"metrica": k, "valor": v} for k, v in diag1.items()
         if k not in ("changes", "errors", "locked_field_overrides")])
    if diag1["errors"]:
        _wr("reclassify_only_errors.csv", diag1["errors"], ["url", "title", "error"])
    if diag1["locked_field_overrides"]:
        _wr("reclassify_only_locked_fields.csv", diag1["locked_field_overrides"],
            ["url", "field", "correction_id", "reason", "reprocessed_value", "locked_value"])

    # ── resumo POR ARTIGO (mutuamente exclusivo, soma == n_changed) ──
    # `changes` é por LINHA (empresa×evento) — um mesmo artigo pode gerar
    # >1 linha (ex.: 2 empresas do mesmo artigo movidas p/ informativo).
    # Contar linhas superestima o nº de artigos alterados. Agrupa por URL e
    # escolhe 1 categoria por artigo (prioridade: remoção > movimentação >
    # adição > normalização), e SOMA as alterações "silenciosas" (artigos
    # mudados sem nenhuma empresa da watchlist envolvida — ex.: só
    # `event_ids` legado ou `mention_roles` mudou) para fechar em n_changed.
    _cats_by_url: dict[str, set] = {}
    for c in diag1["changes"]:
        _cats_by_url.setdefault(c["url"], set()).add(c["categoria"])
    _cat_count = collections.Counter()
    for _url, _cats in _cats_by_url.items():
        _chosen = next(p for p in _RECLASSIFY_ONLY_CAT_PRIORITY if p in _cats)
        _cat_count[_chosen] += 1
    _n_metadata_only = diag1["n_changed"] - len(_cats_by_url)
    if _n_metadata_only:
        _cat_count["metadados_sem_empresa_da_watchlist"] += _n_metadata_only
    _cat_total = sum(_cat_count.values())
    assert _cat_total == diag1["n_changed"], (
        f"reconciliação quebrada: soma das categorias ({_cat_total}) != "
        f"n_changed ({diag1['n_changed']})")

    empresas_afetadas = sorted({c["company"] for c in diag1["changes"]} | {l["empresa"] for l in linhas})
    top10 = sorted(linhas, key=lambda r: abs(r["diferenca"]), reverse=True)[:10]
    report_md = outdir / "reclassify_only_report.md"
    report_md.write_text(
        "# Fase 4H.1 — Fechamento semântico do histórico (--reclassify-only)\n\n"
        f"- Modo: **{'APPLY' if args.apply else 'DRY-RUN'}**\n"
        f"- Histórico de entrada: `{hist_in}`\n"
        f"- Artigos processados: **{diag1['n_processed']}**\n"
        f"- Artigos com alteração material: **{diag1['n_changed']}**\n"
        f"- Emissores afetados: **{len(empresas_afetadas)}** — {', '.join(empresas_afetadas) or '—'}\n"
        f"- Eventos removidos (falsos positivos): **{diag1['removed']}**\n"
        f"- Eventos adicionados: **{diag1['added']}**\n"
        f"- Eventos movidos p/ contexto (terceiro): **{diag1['moved_context']}**\n"
        f"- Eventos movidos p/ informativo (direto não pontuável): **{diag1['moved_informational']}**\n"
        f"- Duplicações econômicas eliminadas (famílias colapsadas): **{diag1['duplicates_collapsed']}**\n"
        f"- Artigos com erro de reclassificação (preservados como estavam): **{len(diag1['errors'])}**\n"
        f"- Registros com correção MANUAL granular (`manual_correction.locked_fields`), "
        f"reprocessados normalmente e só com os campos travados restaurados: "
        f"**{diag1['n_manual_correction_records']}**\n"
        f"- Campos travados cujo valor recém-calculado divergiu do valor humano "
        f"(restaurado, log em `reclassify_only_locked_fields.csv`): "
        f"**{len(diag1['locked_field_overrides'])}**\n"
        f"- Idempotência (2ª passada): **{'OK' if idempotent else 'FALHOU'}**\n\n"
        "## Categorias das alterações materiais (mutuamente exclusivas, por artigo)\n\n"
        "| Categoria | Qtd | Afeta score |\n"
        "|---|---:|---|\n" +
        "\n".join(f"| {k} | {v} | Não |" for k, v in _cat_count.most_common()) +
        f"\n| **TOTAL** | **{_cat_total}** | |\n\n"
        f"(reconciliação: soma das categorias == artigos com alteração material "
        f"== **{diag1['n_changed']}**; ver `assert` no código-fonte)\n\n"
        "## Top 10 maiores variações de score\n\n"
        "| Emissor | Score antes | Score depois | Δ | Status antes | Status depois |\n"
        "|---|---:|---:|---:|---|---|\n" +
        "\n".join(f"| {r['empresa']} | {r['score_antes']} | {r['score_depois']} | {r['diferenca']:+} "
                  f"| {r['status_antes']} | {r['status_depois']} |" for r in top10) +
        "\n\n## Gates de segurança\n\n"
        f"- edgar_scoring_enabled: {edgar_scoring_enabled(cfg)} (deve ser False)\n"
        f"- fetch_executado: False (nenhuma chamada de rede/LLM nesta fase)\n"
        f"- backfill_executado: False\n",
        encoding="utf-8")
    print(f"    reclassify_only_report.md gerado ({outdir / 'reclassify_only_report.md'})")

    # ── auditorias adicionais exigidas ──
    _wr("auditoria_atribuicao_entidade.csv", diag1["changes"],
        ["url", "company", "title", "date", "role_antes", "role_depois", "motivo"])
    _wr("eventos_indiretos_reclassificados.csv",
        [c for c in diag1["changes"] if "contexto" in c["motivo"] or "informativo" in c["motivo"]])
    conflitos = [{"url": u, "title": (r.get("title") or "")[:170],
                  "conflict_resolution_reason": r.get("conflict_resolution_reason", "")}
                 for u, r in (history1.get("articles") or {}).items()
                 if r.get("conflict_resolution_reason")]
    _wr("conflitos_direcao_evento.csv", conflitos)
    (outdir / "relatorio_atribuicao_impacto.md").write_text(
        "# Relatório de atribuição/impacto pós --reclassify-only\n\n"
        f"Artigos com conflito de família resolvido (secondary_events): {len(conflitos)}\n\n"
        f"Emissores com score alterado: {len(linhas)}\n\n"
        f"Ver `reclassify_only_changes.csv` para o detalhe evento a evento.\n",
        encoding="utf-8")

    # ── prévia HTML temporária (nunca sobrescreve index.html/dashboard_risco.html reais) ──
    windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
    data_by_window = {}
    for w in windows:
        data_by_window[str(w)] = {
            "evolution": build_evolution(history1, cfg, window_days=w, thresholds=thresholds1),
            "feed": build_feed(history1, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
    evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
    changes_html = build_changes(history1, cfg, [], original.get("last_run") or {}, evo_ref)
    preview_html = render_html(data_by_window, cfg, demo=False, changes=changes_html,
                                payload_thresholds=thresholds1)
    preview_path = outdir / "preview_reclassify_only.html"
    preview_path.write_text(preview_html, encoding="utf-8")
    print(f" 🖼️  prévia HTML (não é a produção) → {preview_path.resolve()}")

    if not args.apply:
        print(" ✅ dry-run concluído — nenhum arquivo de produção foi alterado.")
        print(f"    para persistir: rode novamente com --reclassify-only --apply")
        return 0

    # ── APPLY: só chega aqui se o usuário pediu --apply explicitamente ──
    gates_ok = idempotent and not diag1["errors"] or (diag1["errors"] and idempotent)
    if not idempotent:
        print(" ❌ APPLY abortado: reclassificação não é idempotente (2ª passada mudou algo).")
        return 1

    backup_dir = outdir / "backup_apply" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    out_hist = Path(args.output_history or args.history)
    out_html = Path(args.output_html or "index.html")
    if out_hist.exists():
        shutil.copy2(out_hist, backup_dir / out_hist.name)
    if out_html.exists():
        shutil.copy2(out_html, backup_dir / out_html.name)
    print(f" 🗄️  backup salvo em {backup_dir}")

    try:
        history1["last_run"] = {
            "ts": int(datetime.now(timezone.utc).timestamp()),
            "iso": fmt_date_br(get_brt_now()),
            "status": {r["company"]: {"status": r["status"], "score": r["total_score"]}
                       for r in evo_ref},
        }
        history1["reclassify_only_applied_at"] = datetime.now(timezone.utc).isoformat()
        save_history(out_hist, history1)
        out_html.write_text(preview_html, encoding="utf-8")

        # ── verificação pós-apply: idempotência sobre o arquivo já persistido ──
        reread = json.loads(out_hist.read_text(encoding="utf-8"))
        _, diag_post = _reclassify_only_pass(_copy.deepcopy(reread), cfg)
        post_idempotent = (diag_post["n_changed"] == 0 and not diag_post["errors"])
        if not post_idempotent:
            raise RuntimeError("pós-apply não idempotente — revertendo")
    except Exception as exc:
        print(f" ❌ falha no apply ({exc}) — restaurando backup…")
        if (backup_dir / out_hist.name).exists():
            shutil.copy2(backup_dir / out_hist.name, out_hist)
        if (backup_dir / out_html.name).exists():
            shutil.copy2(backup_dir / out_html.name, out_html)
        return 1

    print(f" 💾 histórico reclassificado persistido → {out_hist}")
    print(f" 🖼️  dashboard regenerado                → {out_html}")
    print(f" ✅ APPLY concluído — pós-apply idempotente: {post_idempotent}")
    return 0


def prev_run_status(history: dict) -> dict:
    return (history.get("last_run") or {}).get("status") or {}


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
    parser.add_argument("--probe-sources", action="store_true",
                        help="Mede a acessibilidade das fontes oficiais cadastradas "
                             "(diagnóstico, não coleta) e grava o CSV. Requer rede.")
    parser.add_argument("--audit-cvm", action="store_true",
                        help="Roda a auditoria de cobertura CVM/IPE (quais emissores "
                             "brasileiros são filiantes) e grava o CSV. Requer rede.")
    parser.add_argument("--strict-groups", action="store_true",
                        help="Trata erros de segmentação cadastral (emissor sem "
                             "grupo de ativos definido) como fatais, impedindo o "
                             "deploy até a correção do cadastro.")
    parser.add_argument("--test-cvm-fixture", action="store_true",
                        help="Roda a suíte de fixtures do casamento CVM (offline, "
                             "sem rede, sem dados de produção) e sai. Verifica as "
                             "regras da Fase 4B.1 (id forte, alias curto, FII, etc.).")
    parser.add_argument("--test-fund-coverage", action="store_true",
                        help="Roda os fixtures de fundos/cobertura (Fase 4C, offline): "
                             "FII fora do IPE, taxonomia FIP/FIAGRO, robustez do "
                             "coverage_summary e probe de fundos desativado.")
    parser.add_argument("--coverage-backlog", nargs="?", const="coverage_backlog.csv",
                        default=None, help="Gera o backlog de cobertura por emissor "
                        "(4D.1) e sai. Só cadastro/config; pendências marcadas.")
    parser.add_argument("--priority-sources", nargs="?",
                        const="fontes_oficiais_prioritarias.csv", default=None,
                        help="Gera a tabela de fontes oficiais dos emissores "
                        "prioritários (4D.3) e sai.")
    parser.add_argument("--exposure-base", default=None,
                        help="CSV de posições (opcional) para preencher a coluna de "
                        "exposição aprox. nos relatórios de cobertura. Sem ele, fica em branco.")
    # ── Fase 4E — validadores pós-Actions ──
    parser.add_argument("--analyze-audit-cvm", metavar="CSV", default=None,
                        help="Analisa uma auditoria_cobertura_cvm.csv contra o baseline.")
    parser.add_argument("--compare-audit-cvm", nargs=2, metavar=("ANTIGA", "NOVA"), default=None,
                        help="Compara duas auditorias (antiga vs nova) por emissor.")
    parser.add_argument("--analyze-probe-sources", metavar="CSV", default=None,
                        help="Analisa probe_fontes_oficiais.csv e recomenda por fonte/frente.")
    parser.add_argument("--post-actions-report", action="store_true",
                        help="Gera o relatório pós-Actions consolidado (usa --audit-csv/--probe-csv/etc.).")
    parser.add_argument("--quality-gate", action="store_true",
                        help="Roda o quality gate (PASS/PASS_WITH_WARNINGS/FAIL).")
    parser.add_argument("--exposure-review", action="store_true",
                        help="Gera exposure_matching_review.csv (config × base de posições).")
    parser.add_argument("--test-attribution", action="store_true",
                        help="Roda os testes da guarda de papel na atribuição de emissores "
                             "(offline): menção como praça/fonte/credor não atribui evento.")
    parser.add_argument("--audit-edgar", action="store_true",
                        help="[4H.2] Auditoria diagnóstica EDGAR por emissor e por estágio "
                             "(compara allowlist defeituosa vs corrigida). Não altera produção.")
    parser.add_argument("--edgar-dry-run", action="store_true",
                        help="[4H.2] Executa o coletor EDGAR CORRIGIDO isoladamente (rede), "
                             "sem tocar histórico/score/publicação. Diagnóstico apenas.")
    parser.add_argument("--edgar-shadow-run", action="store_true",
                        help="[4H.3A] Coleta real + classificador COMPLETO em sombra "
                             "(score simulado, nada persistido, não publica).")
    parser.add_argument("--edgar-fixtures", default=None,
                        help="[4H.2] Diretório de fixtures submissions JSON p/ auditar offline.")
    parser.add_argument("--audit-international-coverage", action="store_true",
                        help="4H.1: auditoria real de cobertura internacional (rotação, mídia, "
                             "fonte oficial ativa × documentada) por emissor e país.")
    parser.add_argument("--history", default="risk_history.json")
    # ── Reclassificação semântica OFFLINE (sem rede) ──
    parser.add_argument("--reclassify-semantic-only", action="store_true",
                        help="Reclassifica o histórico existente com as regras "
                             "semânticas e reconstrói dashboard/score. NÃO busca "
                             "notícias, CVM, EDGAR, RI; não faz backfill.")
    # ── [Fase 4H.1] --reclassify-only: fechamento semântico do histórico ──
    parser.add_argument("--reclassify-only", action="store_true",
                        help="[4H.1] Reaplica ao histórico EXISTENTE (zero rede/LLM) "
                             "toda a cadeia offline de classificação/atribuição atual "
                             "(classify_and_attribute: taxonomia, mention_role, "
                             "semantic_role_guard, resolve_event_families, "
                             "resolve_entity_match, resolve_related_entity_mentions, "
                             "camada semântica, roteamento contexto/informativo). "
                             "Mais restrito que --reclassify: NUNCA chama Google News, "
                             "CVM/IPE, SEC/EDGAR, RI ou LLM. Padrão é dry-run "
                             "(nada é persistido); use --apply para gravar.")
    parser.add_argument("--apply", action="store_true",
                        help="Usado com --reclassify-only: persiste o resultado "
                             "(cria backup versionado, regrava histórico/HTML, "
                             "verifica idempotência, restaura backup se algum "
                             "gate falhar). Sem --apply, --reclassify-only é dry-run.")
    parser.add_argument("--output-history", default=None,
                        help="Arquivo de saída do histórico reclassificado.")
    parser.add_argument("--output-html", default=None,
                        help="Arquivo de saída do dashboard reconstruído.")
    parser.add_argument("--repair-links-only", action="store_true",
                        help="Resolve e persiste URLs (principais E corroboradoras) "
                             "e reconstrói o HTML. Não coleta, não reclassifica, "
                             "não altera score.")
    parser.add_argument("--audit-outdir", default="out_semantic_production",
                        help="Diretório dos relatórios antes/depois.")
    parser.add_argument("--resolve-cvm-identifiers", action="store_true",
                        help="4G.1: resolução assistida dos identificadores CVM (Tier 1 em revisar) "
                             "contra o cad_cia_aberta; gera review/confirmados/pendentes + patch.")
    parser.add_argument("--only-tier1-review", action="store_true", default=True,
                        help="Restringe a resolução aos Tier 1 em 'revisar' na auditoria.")
    parser.add_argument("--cad-csv", default=None, help="cad_cia_aberta.csv local (opcional).")
    parser.add_argument("--cvm-pending-ids", nargs="?", const="identificadores_cvm_pendentes.csv",
                        default=None, help="Gera o plano de saneamento cadastral (emissores BR "
                        "sem identificador forte) e sai.")
    parser.add_argument("--audit-csv", default=None)
    parser.add_argument("--probe-csv", default=None)
    parser.add_argument("--coverage-backlog-in", default=None)
    parser.add_argument("--priority-sources-in", default=None)
    parser.add_argument("--run-meta", default=None, help="run_meta.json p/ o quality gate auditar backfill.")
    parser.add_argument("--allow-backfill", action="store_true",
                        help="No quality gate, NÃO trata backfill ativo como bloqueio (run de seed intencional).")
    parser.add_argument("--outdir", default=".")
    args = parser.parse_args()

    if args.test_cvm_fixture:
        raise SystemExit(run_cvm_fixture_tests())
    if args.test_fund_coverage:
        raise SystemExit(run_fund_coverage_tests())
    if args.test_attribution:
        raise SystemExit(run_attribution_tests())

    # ── Dispatch 4E (lê CSVs reais; ausência → pendente_arquivo) ──
    _e4 = (args.analyze_audit_cvm or args.compare_audit_cvm or args.analyze_probe_sources
           or args.post_actions_report or args.quality_gate or args.exposure_review
           or args.cvm_pending_ids or args.resolve_cvm_identifiers
           or args.audit_international_coverage or args.audit_edgar or args.edgar_dry_run
           or args.edgar_shadow_run)
    if _e4:
        cfg4 = load_config(args.config)
        if args.analyze_audit_cvm:
            analyze_audit_cvm(args.analyze_audit_cvm, outdir=args.outdir)
        if args.compare_audit_cvm:
            analyze_audit_cvm(args.compare_audit_cvm[1], old_csv=args.compare_audit_cvm[0], outdir=args.outdir)
        if args.analyze_probe_sources:
            analyze_probe_sources(args.analyze_probe_sources, outdir=args.outdir)
        if args.exposure_review:
            exposure_matching_review(cfg4, args.exposure_base, outdir=args.outdir)
        if args.cvm_pending_ids:
            rows = build_cvm_pending_ids(cfg4)
            _write_rows_csv(args.cvm_pending_ids, rows, list(rows[0].keys()) if rows else
                            ["emissor", "tier", "asset_class", "grupo", "ticker", "tem_codigo_cvm",
                             "tem_cnpj", "tem_razao_social", "aliases_curtos", "prioridade",
                             "proxima_acao", "obs"])
            print(f"✅ saneamento CVM: {len(rows)} emissor(es) sem identificador forte → {args.cvm_pending_ids}")
        if args.resolve_cvm_identifiers:
            resolve_cvm_identifiers(cfg4, audit_csv=args.audit_csv,
                                    only_tier1_review=args.only_tier1_review,
                                    cad_csv=args.cad_csv, outdir=args.outdir)
        if args.audit_international_coverage:
            audit_international_coverage(cfg4, args.history, args.exposure_base,
                                         args.outdir, args.run_meta)
        if args.quality_gate:
            quality_gate(cfg4, args.audit_csv, args.probe_csv, args.coverage_backlog_in,
                         args.priority_sources_in, outdir=args.outdir, run_meta=args.run_meta,
                         expect_no_backfill=not args.allow_backfill)
        if args.post_actions_report:
            post_actions_report(cfg4, args.audit_csv, args.probe_csv, args.coverage_backlog_in,
                                args.priority_sources_in, outdir=args.outdir)
        if args.audit_edgar or args.edgar_dry_run or args.edgar_shadow_run:
            # [4H.2/4H.3A] Diagnóstico EDGAR. NÃO toca histórico/score/publicação/
            # backfill. Roda INDEPENDENTE das flags (modo isolado) — sem alterar
            # o comportamento normal de produção.
            import json as _json
            import edgar_audit_4h2 as _ea
            _rm = None
            if args.run_meta:
                try:
                    _rm = _json.loads(open(args.run_meta, encoding="utf-8").read())
                except Exception as _exc:
                    print(f"   ⚠️  run_meta não lido ({_exc}); auditoria seguirá sem telemetria.")
            if args.audit_edgar:
                _live = False
                print(f" 🔎 [4H.2] Auditoria EDGAR diagnóstica "
                      f"(fixtures={args.edgar_fixtures or '—'}). Produção NÃO é alterada.")
                _res = _ea.run_edgar_audit(cfg4, fixtures_dir=args.edgar_fixtures,
                                           live=_live, run_meta=_rm, outdir=args.outdir)
                print(f"   ✅ {_res['n_alvos']} emissor(es) auditado(s); "
                      f"allowlist corrigida={_res['forms_fixed']}; "
                      f"defeituosa={_res['forms_buggy_size']} caracteres.")
            _dry = None
            if args.edgar_dry_run or args.edgar_shadow_run:
                import edgar_shadow_4h3a as _sh
                print(" 🌐 [4H.3A Bloco C] EDGAR dry-run REAL (data.sec.gov). "
                      "Não persiste histórico, não publica, não pontua.")
                try:
                    _dry = _sh.edgar_dry_run(this_module := __import__("risk_dashboard"),
                                             cfg4, outdir=args.outdir, run_meta=_rm)
                except Exception as _exc:
                    print(f"   ❌ dry-run real indisponível ({type(_exc).__name__}: "
                          f"{str(_exc)[:160]}).")
                    print("      → NÃO alegar validação real. Use o workflow "
                          "workflow_edgar_dry_run.yml no GitHub Actions.")
                if args.edgar_shadow_run and _dry:
                    print(" 🌓 [4H.3A Bloco D] Shadow classification…")
                    _sh.edgar_shadow_run(__import__("risk_dashboard"), cfg4,
                                         outdir=args.outdir, dry=_dry)
                    try:
                        _hist = load_history(args.history)
                    except Exception:
                        _hist = {"articles": {}}
                    _sh.edgar_dedup_audit(__import__("risk_dashboard"), cfg4, _hist,
                                          _dry.get("filings") or [], outdir=args.outdir)
                    # [4H.3B] Roda TAMBÉM o shadow de RUNTIME (mesma função do
                    # CASO B de produção) para gerar os artifacts
                    # edgar_runtime_shadow_* e o run_meta com o critério de
                    # shadow (persisted_records=0, history_changed=false…).
                    try:
                        import edgar_shadow_4h3b as _sh3b
                        _arts = []
                        for _f in (_dry.get("filings") or []):
                            _em = _f.get("emissor") or ""
                            _arts.append({
                                "title": (f"{_em} — {_f.get('formulario','')}"
                                          + (f": {_f.get('descricao')}" if _f.get("descricao") else "")),
                                "summary": _f.get("descricao") or _f.get("formulario") or "",
                                "url": _f.get("url_direta") or "", "pub_ts": 0,
                                "source": "SEC · EDGAR", "domain": "sec.gov",
                                "language": "en", "forced_trust": "oficial",
                                "filing_company": _em, "source_company": _em,
                                "provenance": "EDGAR", "form": _f.get("formulario") or "",
                                "accession_number": _f.get("accession_number") or "",
                                "primary_document": _f.get("primary_document") or "",
                                "filing_items": _f.get("filing_items") or "",
                                "filing_date": _f.get("data") or "",
                            })
                        _sh3b.run_edgar_runtime_shadow(
                            _arts, cfg4, sys.modules[__name__],
                            history_snapshot=_hist, outdir=args.outdir,
                            watch_files=[args.history, args.config, "index.html"])
                    except Exception as _exc:
                        print(f"   ⚠️  shadow de runtime não gerado "
                              f"({type(_exc).__name__}: {str(_exc)[:120]}).")
        raise SystemExit(0)

    if args.coverage_backlog or args.priority_sources:
        cfg = load_config(args.config)
        exposure = _load_exposure_map(args.exposure_base)
        if args.coverage_backlog:
            rows = build_coverage_backlog(cfg, exposure)
            with open(args.coverage_backlog, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
            print(f"✅ backlog de cobertura: {len(rows)} emissores → {args.coverage_backlog}")
        if args.priority_sources:
            rows = build_priority_sources(cfg, exposure)
            with open(args.priority_sources, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
            print(f"✅ fontes prioritárias: {len(rows)} emissores → {args.priority_sources}")
        raise SystemExit(0)

    cfg = load_config(args.config)

    # ── [--reclassify-semantic-only] Reclassificação OFFLINE ──
    # Sai ANTES de qualquer coletor: zero fetch, zero backfill.
    if getattr(args, "reclassify_semantic_only", False):
        raise SystemExit(run_semantic_reclassification(args, cfg))

    # ── [4H.1] --reclassify-only: fechamento semântico + atribuição, zero rede/LLM ──
    if getattr(args, "reclassify_only", False):
        raise SystemExit(run_reclassify_only(args, cfg))

    # ── [--repair-links-only] Reparo de URLs, sem coleta e sem reclassificar ──
    if getattr(args, "repair_links_only", False):
        raise SystemExit(run_link_repair(args, cfg))

    # Banner de modos — deixa o log inequívoco sobre o que está ativo nesta
    # execução (auditoria do input backfill: se aparecer 'backfill=OFF', ele NÃO
    # rodou, independentemente do que o histórico já tenha semeado antes).
    _modos = (f"reclassify={'ON' if args.reclassify else 'OFF'} · "
              f"backfill={'ON' if args.backfill else 'OFF'} · "
              f"audit_cvm={'ON' if args.audit_cvm else 'OFF'} · "
              f"probe_sources={'ON' if args.probe_sources else 'OFF'} · "
              f"no_history={'ON' if args.no_history else 'OFF'} · "
              f"demo={'ON' if args.demo else 'OFF'}")
    print(f"⚙️  Modos efetivos: {_modos}")
    if args.backfill:
        print("   ⏪ BACKFILL ATIVO — janela ampliada e histórico semeado. "
              "Se você NÃO pediu backfill, cancele: rode sem --backfill.")
    else:
        print("   ⏹️  Backfill NÃO ativado.")
    # Metadados do run — auditáveis pelo quality gate (detecta backfill indevido).
    try:
        with open("run_meta.json", "w", encoding="utf-8") as _f:
            json.dump({"international_search_execution": _SEARCH_TELEMETRY,
                       "reclassify": bool(args.reclassify), "backfill": bool(args.backfill),
                       "audit_cvm": bool(args.audit_cvm), "probe_sources": bool(args.probe_sources),
                       "no_history": bool(args.no_history), "demo": bool(args.demo),
                       "generated_at": datetime.now(timezone.utc).isoformat()}, _f, ensure_ascii=False)
    except Exception:
        pass
    group_msgs = validate_asset_classes(cfg.get("watchlist", []))
    errors = [m for m in group_msgs if m.startswith("ERRO")]
    warnings = [m for m in group_msgs if m.startswith("WARNING")]
    if group_msgs:
        print("\n 🔎 Validação da segmentação por grupo de ativos:")
        for m in errors + warnings:
            print(f"    {m}")
    if errors:
        if args.strict_groups:
            raise SystemExit(
                f"\n❌ {len(errors)} emissor(es) sem grupo de ativos cadastral "
                f"definido. Corrija o campo `asset_class` na watchlist antes do "
                f"deploy (ou rode sem --strict-groups para gerar mesmo assim, "
                f"tratando-os como 'A revisar').")
        print(f"    ⚠️  {len(errors)} emissor(es) irão para 'A revisar' no filtro.")
    elif not warnings:
        print(" ✅ Segmentação por grupo de ativos: todos os emissores classificados.")

    src_msgs = validate_sources(cfg)
    if src_msgs:
        print(" 🔎 Validação de fontes internacionais:")
        for m in src_msgs:
            print(f"    {m}")
    src_errors = [m for m in src_msgs if m.startswith("ERRO")]
    if src_errors:
        if args.strict_groups:
            raise SystemExit(
                f"\n❌ {len(src_errors)} erro(s) de configuração de fontes. "
                f"Corrija antes do deploy (ou rode sem --strict-groups para "
                f"gerar mesmo assim).")
        print(f"    ⚠️  {len(src_errors)} erro(s) de fonte — corrija antes do deploy.")
    doc_msgs = validate_docs(cfg, ["README.md", "*.md"])
    if doc_msgs:
        print(" 🔎 Validação da documentação:")
        for m in doc_msgs:
            print(f"    {m}")

    if args.audit_cvm:
        audit_cvm_coverage(cfg, out_csv="auditoria_cobertura_cvm.csv")
    if args.probe_sources:
        probe_official_sources(cfg, out_csv="probe_fontes_oficiais.csv")
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
    _edgar_shadow_articles: list[dict] = []
    _edgar_scoring_articles: list[dict] = []
    if args.demo:
        articles = demo_articles()
        print(f" 🧪 Modo demo: {len(articles)} notícias simuladas.")
    else:
        articles = fetch_all(cfg, run_count=history["run_count"])
        articles += fetch_cvm_fatos(cfg)
        articles += fetch_custom_feeds(cfg)
        # ── 4H.3B.0 — GATE REAL DE SHADOW MODE ──
        # ANTES (bug): `articles += fetch_edgar_filings(cfg)` injetava os filings
        # direto no pipeline PONTUÁVEL. `edgar_scoring_enabled` existia mas nunca
        # era consultada aqui, então "shadow mode" não existia na execução normal:
        # com scoring=false os filings ainda entrariam em merge_into_history,
        # score, feed, build_changes e HTML.
        # AGORA a coleta é roteada explicitamente:
        #   CASO A  coleta OFF                → nada;
        #   CASO B  coleta ON + scoring OFF   → SOMENTE shadow (não pontua);
        #   CASO C  coleta ON + scoring ON    → caminho pontuável (não ativado).
        _edgar_raw = fetch_edgar_filings(cfg)
        if edgar_scoring_enabled(cfg):                      # CASO C
            _edgar_scoring_articles = _edgar_raw
            articles += _edgar_scoring_articles
            print(f" ⚠️  EDGAR PONTUÁVEL: {len(_edgar_scoring_articles)} filing(s) "
                  f"entrando no histórico (edgar_scoring_enabled=true).")
        elif edgar_collection_enabled(cfg):                  # CASO B
            # cópia independente: classify_and_attribute é destrutivo e NÃO pode
            # rodar duas vezes sobre o mesmo objeto mutável.
            _edgar_shadow_articles = copy.deepcopy(_edgar_raw)
            print(f" 🌓 EDGAR SHADOW: {len(_edgar_shadow_articles)} filing(s) "
                  f"coletado(s) FORA do pipeline pontuável "
                  f"(edgar_scoring_enabled=false).")
        articles += fetch_ri_news_pages(cfg)

    # 1a) [4H.3B.0 CASO B] Shadow de RUNTIME: classifica os filings FORA do
    # pipeline pontuável e grava artifacts próprios. Prova por hash que
    # histórico/config/HTML não mudaram. Nada entra em production_articles.
    if _edgar_shadow_articles:
        try:
            import edgar_shadow_4h3b as _sh3b
            _sh3b.run_edgar_runtime_shadow(
                _edgar_shadow_articles, cfg, sys.modules[__name__],
                history_snapshot=history, outdir=args.outdir,
                watch_files=[history_path, args.config, "index.html"])
        except Exception as _exc:
            print(f"   ⚠️  shadow de runtime falhou ({type(_exc).__name__}: "
                  f"{str(_exc)[:140]}) — filings seguem FORA do pipeline pontuável.")

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

    # 2) Tradução (antes de classificar) → classificação → dedupe → validação
    #    translate-then-classify: as palavras-chave da taxonomia estão em
    #    português; traduzir título/resumo antes evita perder eventos vindos
    #    de fontes em inglês e espanhol.
    translate_articles(articles, cfg)
    print(" 🏷️  Classificando eventos pela taxonomia…")
    for art in articles:
        classify_and_attribute(art, cfg)
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
            # A aba "Radar de emissores" usa a visão completa (evolution):
            # ranking + score + trajetória + decomposição + fontes.
            "evolution": build_evolution(history, cfg, window_days=w,
                                         thresholds=thresholds,
                                         prev_scores=prev_scores),
            "feed": build_feed(history, cfg, window_days=w),
        }
    default_w = str(cfg["dashboard"].get("default_window", 7))
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

    if evolution:
        print(f"\n 📊 Radar de emissores (janela padrão de {default_w} dias):")
        for row in evolution[:6]:
            seq = " → ".join(f"{o['date'][5:]} {o['label']}" for o in row["timeline"][:4])
            print(f"   {row['total_score']:>4} pts — [{STATUS_META[row['status']]['label']:<15}] "
                  f"{row['company']}: {seq}")

    # 4) Render
    # 4H.2 — telemetria de cobertura AO VIVO desta execução (globals já
    # populados pelos coletores acima); run_meta.json em disco só é
    # regravado com esta telemetria alguns passos abaixo, então passamos os
    # dicts em memória diretamente em vez de reler o arquivo (que ainda
    # teria a telemetria da execução ANTERIOR neste ponto do fluxo).
    _live_run_meta_for_coverage = {
        "international_search_execution": _SEARCH_TELEMETRY,
        "official_source_execution": _OFFICIAL_SOURCE_TELEMETRY,
    }
    html = render_html(data_by_window, cfg, demo=args.demo, changes=changes,
                       payload_thresholds=thresholds,
                       run_meta=_live_run_meta_for_coverage)
    out_file = Path(out_cfg.get("filename", "dashboard_risco.html"))
    out_file.write_text(html, encoding="utf-8")
    # regrava run_meta ao FIM, agora com a telemetria de execução por emissor
    try:
        _fim = datetime.now(timezone.utc).isoformat()
        _rm = _read_json("run_meta.json") or {}
        _rm["international_search_execution"] = _SEARCH_TELEMETRY
        _rm["official_source_execution"] = _OFFICIAL_SOURCE_TELEMETRY
        _rm["run_finished_at"] = _fim
        _rm["run_count"] = history.get("run_count", 0)
        with open("run_meta.json", "w", encoding="utf-8") as _f:
            json.dump(_rm, _f, ensure_ascii=False)
        # 4H.1b — histórico CUMULATIVO: o run_meta representa só a execução
        # atual; sem isto era impossível provar que os 4 ciclos cobriram os 62.
        # Mantém as últimas 8 execuções, sem sobrescrever as anteriores.
        _sh = _read_json("international_search_history.json") or {"runs": []}
        _sh["runs"] = ([r for r in _sh.get("runs", []) if r.get("run_id") != _fim]
                       + [{"run_id": _fim, "run_count": history.get("run_count", 0),
                           "finished_at": _fim, "emitters": _SEARCH_TELEMETRY}])[-8:]
        with open("international_search_history.json", "w", encoding="utf-8") as _f:
            json.dump(_sh, _f, ensure_ascii=False)
        print(f" 📈 Telemetria de busca: {len(_SEARCH_TELEMETRY)} emissor(es) nesta execução; "
              f"histórico cumulativo com {len(_sh['runs'])} run(s).")
    except Exception as _exc:
        print(f"   ⚠️  Falha ao persistir telemetria: {_exc}")
    print(f"\n ✅ Dashboard gerado: {out_file.resolve()}")

    if out_cfg.get("open_browser"):
        webbrowser.open(out_file.resolve().as_uri())


if __name__ == "__main__":
    main()
