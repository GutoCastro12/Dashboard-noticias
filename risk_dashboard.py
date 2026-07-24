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


def link_for_display(rec: dict) -> str:
    """URL a exibir no dashboard. Se o link ainda for o redirecionador do
    Google News (não resolvido para o artigo), usa o display_url (home do
    veículo) como fallback — melhor que mostrar a tela de aviso do Google.
    O token original permanece em rec['url'] para ser retentado depois."""
    u = rec.get("url", "") or ""
    if "news.google.com" in u:
        return rec.get("display_url") or u
    return u


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


# Termos de risco por GRUPO DE ATIVOS (chave = asset_group canônico).
# Antes indexado por `type`, o que fazia os emissores novos (que usam
# `asset_class`) caírem sempre nos termos corporativos genéricos.
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
    "fii": "FIIs",
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
    risk_terms = RISK_TERMS_BY_GROUP.get(grp, RISK_TERMS_BY_GROUP["listed_companies"])
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



def _cvm_filers_index(year: int) -> dict[str, dict] | None:
    """Índice {nome_normalizado_da_companhia: {n, categorias, ultima}} de TODAS
    as companhias que protocolaram algo no IPE no ano. Diferente de
    fetch_cvm_fatos, aqui não se filtra categoria nem janela: a pergunta é
    "esta entidade é filiante na CVM?", não "houve fato relevante recente?"."""
    try:
        resp = requests.get(CVM_IPE_URL.format(year=year), timeout=180,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
    except Exception as exc:
        print(f"   ⚠️  Dataset IPE/CVM indisponível ({exc}).")
        return None
    idx: dict[str, dict] = {}
    with zf.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter=";")
        for row in reader:
            cia = normalize(row.get("Nome_Companhia", ""))
            if not cia:
                continue
            e = idx.setdefault(cia, {"n": 0, "categorias": set(), "ultima": ""})
            e["n"] += 1
            cat = (row.get("Categoria") or "").strip()
            if cat:
                e["categorias"].add(cat)
            d = (row.get("Data_Entrega") or "")[:10]
            if d > e["ultima"]:
                e["ultima"] = d
    return idx


def audit_cvm_coverage(cfg: dict, out_csv: str | None = None,
                       _index: dict | None = None) -> list[dict]:
    """4A.1 — Responde, com dado e não com suposição, quais emissores da
    watchlist são efetivamente FILIANTES na CVM.

    Motivação: `fetch_cvm_fatos` varre o dataset IPE inteiro e casa por alias,
    então toda companhia registrada já tem cobertura oficial automática. O que
    não se sabia é quais dos emissores brasileiros NÃO LISTADOS (private
    equity, crédito privado, SPE) são registrados e protocolam no IPE — eles
    representam a maior incerteza de cobertura oficial da carteira."""
    year = datetime.now(timezone.utc).year
    idx = _index if _index is not None else _cvm_filers_index(year)
    if idx is None:
        print(" ℹ️  Auditoria CVM não executada nesta rodada (dataset indisponível).")
        return []

    alvos = [c for c in cfg.get("watchlist", []) if c.get("country") == "Brasil"]
    print(f" 🔎 Auditoria CVM/IPE {year}: {len(idx)} companhias filiantes no dataset; "
          f"cruzando com {len(alvos)} emissores brasileiros…")
    linhas = []
    for c in alvos:
        nome = c["name"]
        termos = [nome] + list(c.get("aliases") or [])
        achado, cia_match, alias_casado = None, "", ""
        for cia, info in idx.items():
            hit = next((a for a in termos if _word_pattern(a).search(cia)), None)
            if hit:
                achado, cia_match, alias_casado = info, cia, hit
                break

        # Rastreabilidade do casamento: por que este emissor foi considerado
        # filiante? Nome curto casando por substring é a principal fonte de
        # falso positivo, então recebe confiança baixa e vai para revisão.
        tipo_match, confianca, observacao = "", "", ""
        if achado:
            n_norm, cia_norm = normalize(nome), normalize(cia_match)
            a_norm = normalize(alias_casado)
            if n_norm == cia_norm:
                tipo_match, confianca = "exato", "alta"
                observacao = "Nome do emissor idêntico ao da companhia no IPE."
            elif len(a_norm) < 6:
                # o comprimento do termo pesa mais que sua origem: "BRF" ou
                # "TIM" casando dentro de uma razão social longa é a principal
                # fonte de homônimo, seja o termo o nome ou um alias
                tipo_match, confianca = "revisar", "baixa"
                observacao = (f"Casou por termo curto ('{alias_casado}', "
                              f"{len(a_norm)} caracteres) dentro de "
                              f"'{cia_match}' — risco de homônimo. Confirmar.")
            elif a_norm == n_norm:
                tipo_match, confianca = "parcial", "media"
                observacao = (f"Nome do emissor encontrado dentro de "
                              f"'{cia_match}' (razão social mais longa).")
            elif len(a_norm) >= 8:
                tipo_match, confianca = "alias", "media"
                observacao = f"Casou pelo alias '{alias_casado}'."
            else:
                tipo_match, confianca = "revisar", "baixa"
                observacao = (f"Casou por alias de {len(a_norm)} caracteres "
                              f"('{alias_casado}') — confirmar manualmente.")
            if not (achado.get("categorias") or achado.get("n")):
                observacao += " Companhia presente no dataset sem protocolos no período."
        else:
            tipo_match, confianca = "sem_match", "—"
            observacao = "Nenhuma companhia do IPE casou com nome ou aliases."
        grupo = asset_group_of_company(c)
        if achado:
            status = "filiante_cvm"
        elif grupo == "gestora_fundo":
            status = "nao_aplicavel_veiculo"
        elif grupo in ("listada", "listed_companies", "fii"):
            status = "esperado_filiante_sem_protocolo_no_ano"
        else:
            status = "nao_filiante"
        linhas.append({
            "emissor": nome, "asset_class": c.get("asset_class", ""),
            "tier": c.get("tier", ""), "companhia_no_ipe": cia_match,
            "alias_casado": alias_casado, "tipo_match": tipo_match,
            "confianca_match": confianca, "observacao": observacao,
            "protocolos_no_ano": (achado or {}).get("n", 0),
            "ultima_entrega": (achado or {}).get("ultima", ""),
            "categorias": "; ".join(sorted((achado or {}).get("categorias", set()))[:6]),
            "cobertura_oficial_cvm": "sim" if achado else "nao",
            "status": status,
        })
    from collections import Counter
    resumo = Counter(l["status"] for l in linhas)
    for s, n in resumo.most_common():
        print(f"    · {n:>3} {s}")
    rev = [l for l in linhas if l["tipo_match"] == "revisar"]
    if rev:
        print(f"    ⚠️  {len(rev)} casamento(s) por alias curto — confirmar manualmente:")
        for l in rev[:8]:
            print(f"        {l['emissor']} ~ '{l['companhia_no_ipe']}' "
                  f"(alias '{l['alias_casado']}')")
    nl = [l for l in linhas if l["asset_class"] == "nao_listada"]
    cob = sum(1 for l in nl if l["cobertura_oficial_cvm"] == "sim")
    if nl:
        print(f"    → não listadas brasileiras: {cob}/{len(nl)} são filiantes na CVM "
              f"({100 * cob / len(nl):.0f}%)")
    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
            w.writeheader(); w.writerows(linhas)
        print(f"    → auditoria salva em {out_csv}")
    return linhas


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
        acc = (accs[i] if i < len(accs) else "").replace("-", "")
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
            "forced_trust": "oficial", "forced_companies": [company],
        })
    return out


# Bolsas cujos emissores reportam à SEC (ações ou ADR registrados).
_SEC_EXCHANGES = ("nyse", "nasdaq")


def edgar_eligible(company: dict) -> bool:
    """O emissor reporta à SEC? O critério NÃO é o país de domicílio: uma
    companhia suíça, mexicana ou brasileira com ações/ADR registrados nos EUA
    também arquiva no EDGAR (Bunge, Cemex, Ecopetrol, Nubank, StoneCo…).

    É elegível quem tiver: CIK cadastrado, `official.sec: true`, listagem em
    NYSE/Nasdaq, ou ticker cadastral (resolvível no company_tickers.json)."""
    if company.get("cik"):
        return True
    if (company.get("official") or {}).get("sec"):
        return True
    listing = (company.get("listing") or "").lower()
    if any(x in listing for x in _SEC_EXCHANGES):
        return True
    # ticker cadastral + domicílio norte-americano (caso sem `listing` preenchido)
    return bool(company.get("ticker")) and company.get("country") in ("EUA", "Canadá")


def fetch_edgar_filings(cfg: dict) -> list[dict]:
    """Comunicados obrigatórios (8-K/6-K/10-K/20-F…) dos emissores dos EUA via
    EDGAR. É a fonte OFICIAL equivalente à CVM/IPE para a carteira americana.

    Usa o endpoint documentado `data.sec.gov/submissions/CIK##########.json`
    (não o full-text, que não é formalmente documentado pela SEC). Respeita o
    limite de 10 req/s anunciado pela SEC — aqui usamos margem de segurança."""
    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    forms = set(src.get("formularios_gatilho") or ["8-K", "6-K", "10-K", "20-F"])
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
        cik10 = c.get("cik") or cikmap.get(str(c.get("ticker") or "").upper())
        if not cik10:
            print(f"   • {c['name']}: CIK não resolvido "
                  f"(ticker={c.get('ticker') or '—'}); cadastre `cik` no config.")
            continue
        try:
            r = session.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                            headers=_edgar_headers(), timeout=25)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"   ⚠️  {c['name']}: EDGAR indisponível ({exc})")
            time.sleep(1.0 / rps)
            continue
        arts = _edgar_articles_from_submissions(data, c["name"], cik10, forms, cutoff)
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
    model = genai.GenerativeModel(modelos[0])
    sleep_s = float(llm.get("rpm_sleep_seconds", 4))

    print(f" 🌐 Traduzindo {len(pendentes)} notícia(s) para {alvo.upper()}…")
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
        try:
            data = _gemini_call(model, prompt, sleep_s)
        except Exception as exc:
            print(f"   ⚠️  Tradução do lote {i // LOTE + 1} falhou: {exc}")
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
            # já resolvido antes → aplica o artigo real e segue
            rec["url"] = entry["url"]
            continue
        if isinstance(entry, dict) and entry.get("url"):
            rec["display_url"] = entry["url"]  # fallback conhecido p/ exibição
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
        # só sobrescreve o link do registro quando for o ARTIGO exato. Se for
        # apenas o fallback (home), mantém o token original do Google News no
        # rec para que a próxima execução possa retentar — o fallback é aplicado
        # apenas na renderização, não gravado como verdade permanente.
        if exact:
            rec["url"] = real
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
    """Resolve o token via o endpoint interno batchexecute do Google News.
    Método atual (jul/2026): busca os parâmetros de decodificação
    (signature + timestamp) na página do artigo e faz UMA chamada POST que
    retorna o URL real do veículo. Baseado no protocolo googlenewsdecoder."""
    art_id = gurl.split("/articles/")[1].split("?")[0]
    # 1) obter os parâmetros de decodificação. Tenta /articles/ primeiro
    #    (mais estável) e cai para /rss/articles/ se necessário.
    params = None
    for base in ("https://news.google.com/articles/",
                 "https://news.google.com/rss/articles/"):
        try:
            page = session.get(base + art_id, timeout=15)
            if page.status_code != 200:
                continue
            sig = re.search(r'data-n-a-sg="([^"]+)"', page.text)
            ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
            if sig and ts:
                params = (sig.group(1), ts.group(1))
                break
        except Exception:
            continue
    if not params:
        return None
    signature, timestamp = params
    # 2) montar o payload no formato aceito atualmente pelo endpoint
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
          None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        art_id, int(timestamp), signature,
    ])
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
    try:
        resp = session.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
            data={"f.req": freq},
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                "Referer": "https://news.google.com/",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        # a resposta é um stream com prefixo )]}'; procura o array com a URL
        text = resp.text
        # o URL real vem dentro de um array JSON escapado após "garturlres"
        m = re.search(r'\[\\"garturlres\\",\\"(https?://[^\\"]+)\\"', text)
        if not m:
            # formato alternativo: procura a primeira URL http não-google
            m = re.search(r'"(https?://(?!news\.google|www\.google)[^"\\]+)"', text)
        if m:
            url = m.group(1).encode().decode("unicode_escape")
            if "google.com" not in url:
                return url
    except Exception:
        pass
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
        # procedência internacional: idioma e texto original antes da tradução
        for k in ("language", "title_original", "summary_original"):
            if art.get(k):
                rec[k] = art[k]
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
                        "filing_system": coverage_of(c, cfg)[2]}
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
            _grp = meta.get(company, {}).get("asset_group", "a_revisar")
            for eid in rec.get("event_ids", []):
                if eid not in taxonomy:
                    continue
                ev = taxonomy[eid]
                # o evento precisa fazer sentido para a natureza do emissor
                if not event_applies_to(ev, _grp):
                    continue
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
                    "url": link_for_display(rec),
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
        todos = [taxonomy[eid] for eid in rec.get("event_ids", []) if eid in taxonomy]
        if not todos:
            continue
        # Mesma regra do Radar: um evento só é "classificado válido" se fizer
        # sentido para a natureza de algum emissor citado. Notícia de mercado
        # (sem emissor da watchlist) usa os grupos corporativos como padrão.
        grupos = {grupo_por_emissor[c] for c in rec.get("companies", [])
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
        feed.append({
            **rec,
            "url": link_for_display(rec),
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
            "country": next((pais_por_emissor.get(c) for c in rec.get("companies", [])
                             if pais_por_emissor.get(c)), ""),
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
    if not alvos:
        print(" ℹ️  Nenhuma URL de fonte oficial para medir.")
        return []

    print(f" 🩺 Probe de fontes oficiais: {len(alvos)} URL(s) — diagnóstico, sem coleta.")
    session, linhas = requests.Session(), []
    for pais, campo, url in alvos:
        r = _probe_one(url, session)
        r.update({"pais": pais, "campo": campo,
                  "regulador": osrc[pais].get("regulador", ""),
                  "viabilidade_documentada": osrc[pais].get("viabilidade", "")})
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
    if pais == "Brasil" and cvm_on and grupo in ("listed_companies", "fii"):
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
        "asset_groups": build_asset_groups_meta(cfg),
        "official_sources": cfg.get("official_sources") or {},
        "coverage_by_country": build_coverage_by_country(cfg),
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
    args = parser.parse_args()

    cfg = load_config(args.config)

    # ── Validação da segmentação cadastral (grupo de ativos) ──
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
    if args.demo:
        articles = demo_articles()
        print(f" 🧪 Modo demo: {len(articles)} notícias simuladas.")
    else:
        articles = fetch_all(cfg, run_count=history["run_count"])
        articles += fetch_cvm_fatos(cfg)
        articles += fetch_custom_feeds(cfg)
        articles += fetch_edgar_filings(cfg)
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

    # 2) Tradução (antes de classificar) → classificação → dedupe → validação
    #    translate-then-classify: as palavras-chave da taxonomia estão em
    #    português; traduzir título/resumo antes evita perder eventos vindos
    #    de fontes em inglês e espanhol.
    translate_articles(articles, cfg)
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
    html = render_html(data_by_window, cfg, demo=args.demo, changes=changes,
                       payload_thresholds=thresholds)
    out_file = Path(out_cfg.get("filename", "dashboard_risco.html"))
    out_file.write_text(html, encoding="utf-8")
    print(f"\n ✅ Dashboard gerado: {out_file.resolve()}")

    if out_cfg.get("open_browser"):
        webbrowser.open(out_file.resolve().as_uri())


if __name__ == "__main__":
    main()
