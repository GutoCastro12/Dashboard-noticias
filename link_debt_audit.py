#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
link_debt_audit.py — itens 16–19: saúde dos links e ocorrências de emissão.

Duas regras centrais:

1. LINKS — distinguir "a fonte existia e corroborou o evento" de "o link
   continua acessível hoje". Uma corroboração histórica válida NÃO é apagada
   automaticamente; mas não se exibe botão para link sabidamente inválido, nem
   se substitui silenciosamente pela homepage do veículo.

2. EMISSÕES — a ocorrência econômica é identificada por
   emissor + instrumento + valor + série/tranche, não por contagem de notícias.
   O número de notícias NUNCA é o ordinal da emissão.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse, parse_qs, parse_qsl, unquote


def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


# ─────────────────────────── 19. saúde dos links ───────────────────────────
DOMINIOS_OFICIAIS = ("cvm.gov.br", "sec.gov", "b3.com.br", "rad.cvm.gov.br",
                     "bcb.gov.br", "gov.br")
REDIRECIONADORES = ("news.google.com", "google.com/url", "t.co", "bit.ly",
                    "lnkd.in", "flip.it")
# domínios sem reputação editorial verificável (exigem corroboração adicional)
DOMINIOS_SUSPEITOS = ("po-news-eg.net", "newsbreak.com", "blogspot.", "wordpress.com",
                      ".buzz", ".click", ".top", ".xyz")


def classify_link(url: str) -> dict:
    """Classificação ESTRUTURAL do link (sem rede).

    Não confunde bloqueio de rede corporativa com URL inválida: a verificação
    HTTP é separada (`http_status`), e só ela pode dizer 404/403."""
    out = {
        "original_url": url or "", "google_news_url": "", "resolved_url": "",
        "canonical_url": "", "redirect_chain": "", "http_status": "",
        "link_health": "", "resolution_method": "", "fallback_url": "",
        "observacao": "",
    }
    if not url or not str(url).strip():
        out.update(link_health="url_ausente", resolution_method="nenhum",
                   observacao="registro sem URL persistida")
        return out
    u = str(url).strip()
    if not re.match(r"^https?://", u):
        # sem esquema: pode ser URL truncada ou malformada
        if re.match(r"^[\w.\-]+\.[a-z]{2,}(/|$)", u, re.I):
            out.update(link_health="url_malformada", resolution_method="requer_normalizacao",
                       observacao="URL sem esquema http(s)")
            if any(d in u.lower() for d in DOMINIOS_SUSPEITOS):
                out["link_health"] = "dominio_suspeito"
            return out
        out.update(link_health="url_malformada", resolution_method="nenhum",
                   observacao="string não é URL")
        return out
    p = urlparse(u)
    host = (p.netloc or "").lower()
    if any(r in host or r in u for r in REDIRECIONADORES):
        out.update(google_news_url=u, link_health="redirecionador_google",
                   resolution_method="requer_resolucao",
                   observacao="redirecionador: precisa resolver para a URL final "
                              "antes de exibir botão")
        q = parse_qs(p.query)
        if "url" in q:
            out["resolved_url"] = unquote(q["url"][0])
            out["resolution_method"] = "querystring"
        return out
    if any(d in host for d in DOMINIOS_SUSPEITOS):
        out.update(link_health="dominio_suspeito", resolution_method="direto",
                   observacao="domínio sem reputação editorial verificável — "
                              "exige corroboração independente")
        return out
    if any(d in host for d in DOMINIOS_OFICIAIS):
        out.update(link_health="estruturalmente_valido", resolution_method="direto",
                   canonical_url=u, observacao="fonte oficial")
        return out
    if not p.path or p.path == "/":
        out.update(link_health="homepage_generica", resolution_method="direto",
                   observacao="aponta para a homepage, não para a notícia")
        return out
    out.update(link_health="estruturalmente_valido", resolution_method="direto",
               canonical_url=u)
    return out


def check_link_live(url: str, session=None, timeout: int = 12) -> dict:
    """Verificação HTTP real. Separa bloqueio de AMBIENTE de 404/410 do site.
    Só deve rodar onde houver rede (ex.: GitHub Actions)."""
    res = {"http_status": "", "link_health": "", "redirect_chain": "",
           "resolved_url": "", "observacao": ""}
    try:
        import requests
    except Exception:
        res.update(link_health="nao_verificado", observacao="requests indisponível")
        return res
    s = session or requests.Session()
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (compatible; RadarRisco/1.0)"})
        res["http_status"] = r.status_code
        res["resolved_url"] = r.url
        res["redirect_chain"] = " → ".join(h.url for h in r.history)[:300]
        if r.status_code == 200:
            res["link_health"] = "ok"
        elif r.status_code in (301, 302, 307, 308):
            res["link_health"] = "redirect_resolvido"
        elif r.status_code in (401, 403):
            res["link_health"] = "bloqueado_ou_paywall"
            res["observacao"] = ("403/401 pode ser antibot/paywall — NÃO é prova "
                                 "de que a notícia sumiu")
        elif r.status_code in (404, 410):
            res["link_health"] = "removido"
        else:
            res["link_health"] = f"http_{r.status_code}"
    except Exception as exc:  # noqa: BLE001
        nome = type(exc).__name__
        if any(k in nome for k in ("ConnectionError", "Timeout", "SSLError", "Proxy")):
            res.update(link_health="bloqueio_de_ambiente",
                       observacao=f"{nome}: falha de rede/proxy — NÃO confundir "
                                  f"com URL inválida")
        else:
            res.update(link_health="erro_desconhecido", observacao=nome)
    return res


def link_display_decision(link_health: str, corroborado: bool) -> dict:
    """Decide o que a interface faz. Nunca aponta botão para link inválido, nem
    apaga corroboração histórica válida."""
    if link_health in ("ok", "estruturalmente_valido", "redirect_resolvido"):
        return {"exibir_botao": True, "rotulo": "Abrir fonte", "manter_corroboracao": True}
    if link_health in ("removido", "url_malformada", "url_ausente"):
        return {"exibir_botao": False, "rotulo": "link indisponível",
                "manter_corroboracao": corroborado,
                "nota": "a fonte corroborou o evento à época; o link não responde hoje"
                        if corroborado else "sem evidência acessível"}
    if link_health in ("bloqueado_ou_paywall", "bloqueio_de_ambiente"):
        return {"exibir_botao": True, "rotulo": "Abrir fonte (pode exigir acesso)",
                "manter_corroboracao": True}
    if link_health == "homepage_generica":
        return {"exibir_botao": False, "rotulo": "link indisponível",
                "manter_corroboracao": corroborado,
                "nota": "homepage não é a notícia original"}
    if link_health in ("redirecionador_google", "dominio_suspeito"):
        return {"exibir_botao": False, "rotulo": "verificar fonte",
                "manter_corroboracao": corroborado,
                "nota": "requer resolução/corroboração antes de exibir"}
    return {"exibir_botao": False, "rotulo": "link indisponível",
            "manter_corroboracao": corroborado}


# ────────────────────── 18. ocorrências de emissão de dívida ──────────────────────
_INSTRUMENTOS = {
    "debentures": r"deb[êe]ntures?",
    "bonds": r"\bbonds?\b|notes?\b|senior\s+notes",
    "cra_cri": r"\bcra\b|\bcri\b",
    "nota_comercial": r"notas?\s+comerciais?",
    "fidc": r"\bfidc\b",
    "emprestimo": r"empr[ée]stimo|financiamento banc[áa]rio|linha de cr[ée]dito",
}
_FASES_EMISSAO = [
    ("liquidacao", r"liquida[çc][ãa]o|conclu[íi]|liquidou|settlement"),
    ("precificacao", r"precifica|pricing|definiu taxa|bookbuilding"),
    ("aprovacao", r"aprova|autoriza|conselho aprovou"),
    ("anuncio", r"anuncia|capta|emite|emiss[ãa]o de|planeja captar|vai captar"),
    ("reabertura", r"reabertura|tap\b|nova s[ée]rie|s[ée]rie adicional"),
]


def _valor_normalizado(texto: str) -> str:
    """Extrai valor e normaliza magnitude para comparar ocorrências."""
    m = re.search(r"(r\$|us\$|\$|€)\s*([\d.,]+)\s*(bilh|bi\b|milh|mi\b|mm\b|k\b)?",
                  _n(texto))
    if not m:
        return ""
    moeda = m.group(1).replace("$", "").strip() or "usd"
    num = m.group(2).replace(".", "").replace(",", ".")
    try:
        v = float(num)
    except ValueError:
        return ""
    mag = (m.group(3) or "")
    if mag.startswith(("bilh", "bi")):
        v *= 1_000
    elif mag.startswith(("milh", "mi", "mm")):
        v *= 1
    # arredonda para faixa (tolera 1,5 bi vs 1.500 milhões)
    return f"{moeda}:{round(v)}"


def _serie(texto: str) -> str:
    m = re.search(r"(\d{1,2})[ªa]?\s*s[ée]rie", _n(texto))
    if m:
        return f"s{m.group(1)}"
    m = re.search(r"s[ée]rie\s+([ivx]+|\d{1,2})", _n(texto))
    return f"s{m.group(1)}" if m else ""


def _instrumento(texto: str) -> str:
    t = _n(texto)
    for k, rx in _INSTRUMENTOS.items():
        if re.search(rx, t):
            return k
    return "indefinido"


def debt_occurrence_key(emissor: str, titulo: str, data: str = "") -> str:
    """Chave da OCORRÊNCIA ECONÔMICA da emissão.

    Múltiplas notícias sobre a mesma emissão colapsam nesta chave. Emissões
    realmente distintas (valor/série/instrumento diferentes) NÃO colapsam,
    mesmo com datas próximas."""
    return "|".join([_n(emissor), _instrumento(titulo),
                     _valor_normalizado(titulo) or "sem_valor",
                     _serie(titulo) or "sem_serie"])


def debt_phase(titulo: str) -> str:
    t = _n(titulo)
    for fase, rx in _FASES_EMISSAO:
        if re.search(rx, t):
            return fase
    return "indefinida"


def group_debt_occurrences(registros: list[dict]) -> dict:
    """Agrupa notícias em ocorrências econômicas.

    `registros`: [{emissor, titulo, data, url, fonte}]
    Devolve {chave: {ocorrencia, fontes, fases, tranches, titulos}}."""
    grupos: dict[str, dict] = {}
    for r in registros:
        k = debt_occurrence_key(r.get("emissor", ""), r.get("titulo", ""), r.get("data", ""))
        g = grupos.setdefault(k, {"chave": k, "emissor": r.get("emissor", ""),
                                  "instrumento": _instrumento(r.get("titulo", "")),
                                  "valor": _valor_normalizado(r.get("titulo", "")),
                                  "serie": _serie(r.get("titulo", "")),
                                  "fontes": set(), "fases": set(), "titulos": [],
                                  "urls": []})
        g["fontes"].add(r.get("fonte", "") or "")
        g["fases"].add(debt_phase(r.get("titulo", "")))
        g["titulos"].append(r.get("titulo", ""))
        g["urls"].append(r.get("url", ""))
    for g in grupos.values():
        g["qtd_fontes"] = len([f for f in g["fontes"] if f])
        g["qtd_noticias"] = len(g["titulos"])
        g["fase_atual"] = next((f for f in ("liquidacao", "precificacao", "aprovacao",
                                            "anuncio", "reabertura")
                                if f in g["fases"]), "indefinida")
        g["fontes"] = ";".join(sorted(x for x in g["fontes"] if x))
        g["fases"] = ";".join(sorted(g["fases"]))
    return grupos


# ═══════════════════════════════════════════════════════════════════════
# FUNÇÃO CANÔNICA ÚNICA DE RESOLUÇÃO DE URL (fonte principal E corroboradora)
# ═══════════════════════════════════════════════════════════════════════
#
# CAUSA RAIZ (comprovada nos dados e no código):
#   `resolve_history_urls()` iterava apenas as CHAVES de history["articles"],
#   isto é, as fontes PRINCIPAIS. As URLs dentro de `corroborations` /
#   `corrob_sources` nunca eram resolvidas. Medição no histórico real:
#       fontes principais    : 39,0% ainda com redirecionador do Google
#       fontes corroboradoras: 98,6% ainda com redirecionador do Google
#   O template caía em `linkOf()` e, sem `display_url`, emitia o próprio
#   endereço `news.google.com/rss/articles/...` no href — que o proxy
#   corporativo recusa ("endereço da Web inválido"). Fora da rede da empresa o
#   navegador completa o redirecionamento e a mesma URL abre normalmente.
#
# Esta função é o ÚNICO caminho de resolução: principal, corroboradora,
# oficial, jornalística, antiga ou nova passam todas por aqui.

import html as _html
from urllib.parse import urlsplit, urlunsplit

MAX_DECODE_ROUNDS = 3
MAX_HTTP_REDIRECTS = 5
RESOLVE_TIMEOUT_S = 12

ESQUEMAS_PERIGOSOS = ("javascript:", "data:", "file:", "vbscript:", "about:")
PARAMS_DESTINO = ("url", "q", "u", "target", "destination", "dest", "redirect")

# Estados de saúde do link (item 8) — bloqueio de rede NUNCA vira "removido".
LINK_HEALTH = (
    "url_direta_valida", "redirect_resolvido", "redirect_nao_resolvido",
    "bloqueado_ou_paywall", "possivel_bloqueio_corporativo", "bloqueio_de_ambiente",
    "removido_404_410", "homepage_generica", "url_malformada",
    "dominio_suspeito", "nao_verificado",
)


def _vazio_resolucao(original_url: str = "") -> dict:
    return {
        "original_url": original_url or "", "redirect_url": "", "resolved_url": "",
        "canonical_url": "", "display_url": "", "redirect_chain": "",
        "original_host": "", "final_host": "", "http_status": "",
        "link_health": "nao_verificado", "resolution_method": "",
        "last_checked_at": "", "resolution_error": "",
    }


def _host(u: str) -> str:
    try:
        return (urlsplit(u).netloc or "").lower()
    except Exception:
        return ""


def is_redirector(url: str) -> bool:
    """A URL é um endereço intermediário (não a matéria final)?"""
    if not url:
        return False
    u = str(url)
    host = _host(u)
    if any(r in host for r in ("news.google.com", "t.co", "bit.ly", "lnkd.in",
                               "flip.it", "trib.al", "ow.ly")):
        return True
    if "google.com" in host and "/url" in u:
        return True
    # qualquer host que carregue um destino http(s) em parâmetro conhecido é
    # intermediário — inclusive encadeado/duplamente codificado
    try:
        q = parse_qs(urlsplit(_html.unescape(u)).query)
        for chave in PARAMS_DESTINO:
            for v in q.get(chave, []):
                cand = unquote(unquote(_html.unescape(v)))
                if cand.lower().startswith(("http://", "https://")):
                    return True
    except Exception:
        pass
    return False


def is_direct_valid(url: str) -> bool:
    """URL direta e estruturalmente válida para ir ao href."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if any(u.lower().startswith(e) for e in ESQUEMAS_PERIGOSOS):
        return False
    if not u.lower().startswith(("http://", "https://")):
        return False
    if is_redirector(u):
        return False
    p = urlsplit(u)
    if not p.netloc or "." not in p.netloc:
        return False
    if any(d in p.netloc.lower() for d in DOMINIOS_SUSPEITOS):
        return False
    if not p.path or p.path == "/":
        return False                      # homepage não é a matéria
    return True


def extract_destination(url: str) -> tuple[str, str]:
    """Extrai o destino de um redirecionador por parâmetro, com decodificação
    controlada (HTML entities + percent-encoding, no máximo N rodadas).
    Devolve (destino, metodo)."""
    if not url:
        return "", ""
    u = _html.unescape(str(url))
    for _ in range(MAX_DECODE_ROUNDS):
        try:
            p = urlsplit(u)
            q = parse_qs(p.query)
        except Exception:
            return "", ""
        achou = ""
        for chave in PARAMS_DESTINO:
            if chave in q and q[chave]:
                cand = _html.unescape(q[chave][0])
                # decodifica até estabilizar (trata %253A → %3A → :)
                for _ in range(MAX_DECODE_ROUNDS):
                    novo = unquote(cand)
                    if novo == cand:
                        break
                    cand = novo
                if cand.lower().startswith(("http://", "https://")):
                    achou = cand
                    break
        if not achou:
            return "", ""
        if is_direct_valid(achou):
            return achou, "parametro_destino"
        if is_redirector(achou):
            u = achou           # aninhado: decodifica de novo
            continue
        return achou, "parametro_destino"
    return "", ""


def canonicalize(url: str) -> str:
    """Remove parâmetros de tracking e fragmento, preservando o caminho."""
    if not url:
        return ""
    try:
        p = urlsplit(url)
        drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                "utm_content", "gclid", "fbclid", "oc", "hl", "gl", "ceid",
                "ref", "ref_src", "s"}
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
             if k.lower() not in drop]
        query = "&".join(f"{k}={v}" for k, v in q)
        return urlunsplit((p.scheme, p.netloc, p.path, query, ""))
    except Exception:
        return url


def resolve_article_url(original_url: str, *, domain: str = "",
                        cache: dict | None = None, session=None,
                        allow_network: bool = False,
                        verify_status: bool = False) -> dict:
    """Resolução CANÔNICA de uma URL de notícia.

    Usada igualmente por fonte principal, corroboradora, oficial e jornalística.
    Nunca devolve um redirecionador em `display_url`: se não conseguir resolver,
    `display_url` fica vazio e a interface mostra "Link em verificação".
    """
    from datetime import datetime, timezone
    r = _vazio_resolucao(original_url)
    if not original_url or not isinstance(original_url, str):
        r["link_health"] = "url_malformada"
        r["resolution_method"] = "nenhum"
        return r
    u0 = original_url.strip()
    r["original_host"] = _host(u0)

    if any(u0.lower().startswith(e) for e in ESQUEMAS_PERIGOSOS):
        r.update(link_health="url_malformada", resolution_method="esquema_bloqueado",
                 resolution_error="esquema perigoso")
        return r
    if not u0.lower().startswith(("http://", "https://")):
        r.update(link_health="url_malformada", resolution_method="sem_esquema")
        return r

    # 1) já é direta
    if not is_redirector(u0):
        if any(d in r["original_host"] for d in DOMINIOS_SUSPEITOS):
            r.update(link_health="dominio_suspeito", resolution_method="direto",
                     canonical_url=canonicalize(u0), final_host=r["original_host"])
            return r
        p = urlsplit(u0)
        if not p.path or p.path == "/":
            r.update(link_health="homepage_generica", resolution_method="direto",
                     final_host=r["original_host"])
            return r
        can = canonicalize(u0)
        r.update(resolved_url=u0, canonical_url=can, display_url=can or u0,
                 final_host=_host(can or u0), link_health="url_direta_valida",
                 resolution_method="direto")
    else:
        r["redirect_url"] = u0
        # 2) cache de resoluções já conhecidas (offline)
        ent = (cache or {}).get(u0)
        if isinstance(ent, dict) and ent.get("url") and ent.get("exact"):
            alvo = ent["url"]
            if is_direct_valid(alvo):
                can = canonicalize(alvo)
                r.update(resolved_url=alvo, canonical_url=can, display_url=can,
                         final_host=_host(can), link_health="redirect_resolvido",
                         resolution_method="cache_historico")
                r["last_checked_at"] = datetime.now(timezone.utc).isoformat()
                return r
        # 3) destino em parâmetro (google.com/url?url=…)
        alvo, metodo = extract_destination(u0)
        if alvo and is_direct_valid(alvo):
            can = canonicalize(alvo)
            r.update(resolved_url=alvo, canonical_url=can, display_url=can,
                     final_host=_host(can), link_health="redirect_resolvido",
                     resolution_method=metodo)
            r["last_checked_at"] = datetime.now(timezone.utc).isoformat()
            return r
        # 4) rede: segue o redirect
        if allow_network:
            try:
                import requests
                s = session or requests.Session()
                resp = s.get(u0, timeout=RESOLVE_TIMEOUT_S, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; "
                                                    "Win64; x64)"})
                cadeia = [h.url for h in resp.history][:MAX_HTTP_REDIRECTS]
                r["redirect_chain"] = " → ".join(cadeia)[:400]
                r["http_status"] = resp.status_code
                final = resp.url or ""
                if is_direct_valid(final):
                    can = canonicalize(final)
                    r.update(resolved_url=final, canonical_url=can, display_url=can,
                             final_host=_host(can), link_health="redirect_resolvido",
                             resolution_method="redirect_http")
                else:
                    r.update(link_health="redirect_nao_resolvido",
                             resolution_method="redirect_http",
                             resolution_error="destino final não é URL direta válida")
            except Exception as exc:  # noqa: BLE001
                nome = type(exc).__name__
                ambiente = any(k in nome for k in ("ConnectionError", "Timeout",
                                                   "SSLError", "ProxyError"))
                r.update(link_health=("bloqueio_de_ambiente" if ambiente
                                      else "redirect_nao_resolvido"),
                         resolution_method="redirect_http",
                         resolution_error=f"{nome}: {str(exc)[:120]}")
        else:
            r.update(link_health="redirect_nao_resolvido",
                     resolution_method="offline",
                     resolution_error="resolução exige rede (rodar no GitHub Actions)")

    # 5) verificação de status do destino (opcional)
    if verify_status and allow_network and r.get("display_url"):
        try:
            import requests
            s = session or requests.Session()
            resp = s.head(r["display_url"], timeout=RESOLVE_TIMEOUT_S,
                          allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 405:
                resp = s.get(r["display_url"], timeout=RESOLVE_TIMEOUT_S,
                             allow_redirects=True, stream=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            r["http_status"] = resp.status_code
            if resp.status_code in (404, 410):
                r["link_health"] = "removido_404_410"
                r["display_url"] = ""          # não gera <a> quebrado
            elif resp.status_code in (401, 403):
                r["link_health"] = "bloqueado_ou_paywall"
            elif resp.status_code >= 500:
                r["link_health"] = "possivel_bloqueio_corporativo"
        except Exception as exc:  # noqa: BLE001
            nome = type(exc).__name__
            if any(k in nome for k in ("ConnectionError", "Timeout", "SSLError",
                                       "ProxyError")):
                r["link_health"] = ("bloqueio_de_ambiente" if not r.get("display_url")
                                    else r["link_health"])
                r["resolution_error"] = f"{nome} na verificação de status"
    r["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return r


def interface_decision(res: dict) -> dict:
    """Decisão de interface por FONTE (item 12). Cada fonte tem a sua."""
    lh = res.get("link_health") or "nao_verificado"
    du = res.get("display_url") or ""
    if lh in ("url_direta_valida", "redirect_resolvido") and du:
        return {"render_anchor": True, "label": "Abrir notícia →", "href": du}
    if lh in ("bloqueado_ou_paywall", "possivel_bloqueio_corporativo") and du:
        return {"render_anchor": True, "label": "Abrir notícia — pode exigir acesso →",
                "href": du}
    if lh == "removido_404_410":
        return {"render_anchor": False, "label": "Link indisponível", "href": ""}
    if lh in ("redirect_nao_resolvido", "bloqueio_de_ambiente", "nao_verificado"):
        return {"render_anchor": False, "label": "Link em verificação", "href": ""}
    if du:
        return {"render_anchor": True, "label": "Abrir notícia →", "href": du}
    return {"render_anchor": False, "label": "Link indisponível", "href": ""}
