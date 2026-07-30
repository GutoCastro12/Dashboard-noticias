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
from urllib.parse import urlparse, parse_qs, unquote


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
