#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_page_date.py — a data do feed não é a data do fato.

O QUE ACONTECEU

O Google News reapresentou, em julho de 2026, uma página oficial da Vale
publicada em **31/05/2023** sobre o plano de recuperação judicial da Samarco. O
`<pubDate>` do RSS dizia 2026-07-22, e esse valor era a única autoridade de
data do pipeline. A matéria de 2023 entrou nas janelas de 30, 90 e 365 dias e
sustentou sozinha a Samarco como **CRÍTICA**, com `hard_critical`, num painel
que vai para leitura executiva.

A página declarava a própria data em dois lugares legíveis por máquina —
`datePublished` em JSON-LD e a data visível no corpo. Ninguém olhava.

Havia precedente: um artigo de 2014 do Law.com ressurgiu com data de 2026 e foi
corrigido por lock manual, com a anotação de que não havia como validar
page-date genericamente naquela fonte. Esta é a segunda ocorrência conhecida da
classe e a primeira a produzir falso crítico.

O QUE ESTA CAMADA FAZ

Quando a página resolvida declara uma data de publicação FORTE e ela conflita
materialmente com a data do feed, a página vence — e o conflito fica registrado
com proveniência. O feed nunca é apagado: sem ele não dá para auditar a decisão
depois.

O QUE ELA NÃO FAZ

Não adivinha data. Sem JSON-LD, sem OpenGraph, com HTTP 403 ou HTML quebrado,
a resposta é "não verificado" e o feed permanece — porque uma data inventada é
pior que uma data suspeita. É por isso que ela reduz o risco sem eliminá-lo: o
caso do Law.com, que responde 403, continuaria fora do alcance.

Não altera semântica, peso, severidade nem limiar. O falso crítico da Samarco
desaparece por ter a data certa, não por uma regra nova de risco.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone

POLICY_VERSION = "pubdate.p1"

# Tolerância entre a data do feed e a data forte da página. Abaixo disto a
# diferença é fuso horário, republicação legítima ou atraso de indexação —
# não é ressurgimento. Acima, é material: a Samarco divergia por mais de três
# anos. O valor é nomeado e testado no limite, nos dois sentidos.
TOLERANCIA_S = 7 * 86400

# Tipos de objeto JSON-LD que representam a MATÉRIA. `WebPage`, `Organization`
# e `BreadcrumbList` aparecem na mesma página e não carregam a data do texto.
TIPOS_EDITORIAIS = {"newsarticle", "article", "blogposting", "reportagenewsarticle",
                    "scholarlyarticle", "techarticle", "liveblogposting",
                    "socialmediaposting"}

# Uma data anterior a isto não é publicação de notícia — é default de sistema.
_ANO_MINIMO = 1995
# Publicação no futuro além disto é relógio errado, não furo jornalístico.
_FOLGA_FUTURO_S = 2 * 86400


def _norm(s: str) -> str:
    """Normalização só para COMPARAR manchete — nunca para exibir."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"&[a-z]+;|&#\d+;", " ", s.lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _parse_iso(valor) -> int:
    """ISO-8601 → epoch UTC. Devolve 0 quando não é data utilizável."""
    if not isinstance(valor, str) or not valor.strip():
        return 0
    t = valor.strip().replace("Z", "+00:00")
    # `2023-05-31T03:00:00.000Z` e `2023-05-31` são ambos válidos; formatos
    # com fuso escrito por extenso não são, e viram 0 em vez de exceção.
    for corte in (t, t[:19], t[:10]):
        try:
            dt = datetime.fromisoformat(corte)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # O ano é conferido ANTES de virar epoch: no Windows, converter uma
        # data de ano 1 levanta OSError em vez de devolver número. Um default
        # de sistema não pode derrubar a coleta.
        if not (_ANO_MINIMO <= dt.year <= 2200):
            return -1                      # sinaliza "data implausível", não 0
        try:
            return int(dt.timestamp())
        except (OverflowError, OSError, ValueError):
            return -1
    return 0


def _plausivel(ts: int, agora: int) -> tuple[bool, str]:
    if ts == -1:
        return False, "ano implausível — provável default de sistema"
    if ts <= 0:
        return False, "sem data"
    if ts > agora + _FOLGA_FUTURO_S:
        return False, "data no futuro além da folga de relógio"
    return True, ""


def _objetos_jsonld(html: str) -> list:
    """Todos os objetos JSON-LD da página, achatando `@graph` e listas."""
    saida = []
    for m in re.finditer(
            r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html or "", re.I | re.S):
        try:
            dado = json.loads(m.group(1).strip())
        except Exception:                                   # noqa: BLE001
            continue                     # JSON-LD quebrado não derruba a página
        pilha = [dado]
        while pilha:
            item = pilha.pop()
            if isinstance(item, list):
                pilha.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    pilha.append(item["@graph"])
                saida.append(item)
    return saida


def _tipos(obj: dict) -> set:
    t = obj.get("@type") or obj.get("type") or ""
    vals = t if isinstance(t, list) else [t]
    return {str(v).strip().lower() for v in vals if v}


def _combina(obj: dict, url: str, headline: str) -> int:
    """Quão bem este objeto corresponde ao artigo em questão. Maior é melhor.

    Uma página pode trazer vários objetos editoriais — relacionados, "leia
    também", cards de outras matérias. Pegar o `datePublished` do primeiro
    seria pegar a data de outro texto.
    """
    p = 0
    if headline and _norm(obj.get("headline") or obj.get("name")) == _norm(headline):
        p += 4
    alvo = _norm(url).replace(" ", "")
    for chave in ("mainEntityOfPage", "@id", "url"):
        v = obj.get(chave)
        if isinstance(v, dict):
            v = v.get("@id") or v.get("url")
        if v and alvo and _norm(str(v)).replace(" ", "") in alvo:
            p += 2
            break
    return p


def extrair_data_da_pagina(html: str, *, url: str = "", headline: str = "",
                           agora: int | None = None) -> dict:
    """Data de publicação FORTE declarada pela própria página.

    Ordem: JSON-LD editorial (com desempate por manchete/URL) e, na ausência,
    `article:published_time` do OpenGraph. Nada de heurística sobre números
    soltos no HTML — data plausível inventada é pior que data ausente.
    """
    agora = int(agora if agora is not None else datetime.now(timezone.utc).timestamp())
    vazio = {"published_ts": 0, "published_iso": "", "fonte": "",
             "modified_iso": "", "motivo": "", "candidatos": 0}
    if not html:
        return {**vazio, "motivo": "sem html"}

    melhores, implausiveis = [], 0
    for obj in _objetos_jsonld(html):
        if not (_tipos(obj) & TIPOS_EDITORIAIS):
            continue
        ts = _parse_iso(obj.get("datePublished"))
        if ts == -1:
            implausiveis += 1
            continue        # não concorre: senão o menor timestamp venceria a
                            # ordenação e derrubaria uma data válida ao lado
        if not ts:
            continue
        melhores.append((_combina(obj, url, headline), ts,
                         obj.get("dateModified") or ""))
    if not melhores and implausiveis:
        return {**vazio, "motivo": "jsonld: ano implausível — provável default "
                                   "de sistema", "candidatos": implausiveis}
    if melhores:
        # Maior afinidade primeiro; empate resolve pela data mais ANTIGA, que é
        # a conservadora: nunca inventa novidade que a página não afirma.
        melhores.sort(key=lambda x: (-x[0], x[1]))
        afinidade, ts, modificado = melhores[0]
        ok, motivo = _plausivel(ts, agora)
        if ok:
            return {"published_ts": ts,
                    "published_iso": datetime.fromtimestamp(
                        ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "fonte": "jsonld", "modified_iso": str(modificado)[:32],
                    "motivo": "", "candidatos": len(melhores),
                    "afinidade": afinidade}
        return {**vazio, "motivo": f"jsonld: {motivo}",
                "candidatos": len(melhores)}

    m = re.search(r'<meta[^>]+(?:property|name)\s*=\s*["\']article:published_time["\']'
                  r'[^>]+content\s*=\s*["\']([^"\']+)["\']', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+'
                      r'(?:property|name)\s*=\s*["\']article:published_time["\']',
                      html, re.I)
    if m:
        ts = _parse_iso(m.group(1))
        ok, motivo = _plausivel(ts, agora)
        if ok:
            return {"published_ts": ts,
                    "published_iso": datetime.fromtimestamp(
                        ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "fonte": "opengraph", "modified_iso": "", "motivo": "",
                    "candidatos": 1}
        return {**vazio, "motivo": f"opengraph: {motivo}"}

    return {**vazio, "motivo": "sem data forte na página"}


def decidir_data_efetiva(feed_ts: int, pagina: dict, *,
                         tolerancia: int = TOLERANCIA_S) -> dict:
    """Qual data vale, e por quê.

    A página só vence quando o conflito é MATERIAL. Diferença dentro da
    tolerância mantém o feed: corrigir minutos criaria ruído de proveniência
    sem corrigir risco algum.
    """
    pts = int(pagina.get("published_ts") or 0)
    if not pts:
        return {"efetivo_ts": int(feed_ts or 0), "origem": "feed",
                "verificacao": "nao_verificado", "conflito": False,
                "delta_s": 0, "policy": POLICY_VERSION,
                "motivo": pagina.get("motivo") or "sem data forte na página"}
    if not feed_ts:
        return {"efetivo_ts": pts, "origem": "pagina",
                "verificacao": "verificado_pagina", "conflito": False,
                "delta_s": 0, "policy": POLICY_VERSION,
                "motivo": "feed sem data; página declara publicação"}
    delta = abs(int(feed_ts) - pts)
    if delta <= tolerancia:
        return {"efetivo_ts": int(feed_ts), "origem": "feed",
                "verificacao": "verificado_sem_conflito", "conflito": False,
                "delta_s": delta, "policy": POLICY_VERSION,
                "motivo": f"divergência de {delta}s dentro da tolerância"}
    return {"efetivo_ts": pts, "origem": "pagina",
            "verificacao": "verificado_pagina", "conflito": True,
            "delta_s": delta, "policy": POLICY_VERSION,
            "motivo": (f"FEED_PAGE_DATE_CONFLICT: feed e página divergem "
                       f"{delta // 86400} dia(s); a página é a autoridade")}


def campos_de_proveniencia(registro: dict, pagina: dict, decisao: dict) -> dict:
    """Campos a gravar. O feed original é SEMPRE preservado."""
    saida = {
        "feed_pub_ts": registro.get("feed_pub_ts", registro.get("pub_ts")),
        "feed_pub_iso": registro.get("feed_pub_iso", registro.get("pub_iso")),
        "page_pub_ts": pagina.get("published_ts") or 0,
        "page_pub_iso": pagina.get("published_iso") or "",
        "page_date_source": pagina.get("fonte") or "",
        "page_date_modified": pagina.get("modified_iso") or "",
        "pub_date_verification": decisao["verificacao"],
        "pub_date_origin": decisao["origem"],
        "pub_date_conflict_s": decisao["delta_s"],
        "pub_date_policy": decisao["policy"],
        "pub_date_note": decisao["motivo"],
    }
    if decisao["origem"] == "pagina" and decisao["efetivo_ts"]:
        ts = decisao["efetivo_ts"]
        saida["pub_ts"] = ts
        # mesma convenção de exibição do resto do pipeline: BRT = UTC-3
        saida["pub_iso"] = datetime.fromtimestamp(
            ts - 3 * 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return saida


def campos_travados(registro: dict) -> set:
    """Campos sob correção manual. Auditoria humana vence verificador
    automático — foi assim que o caso do Law.com foi contido, e reverter isso
    apagaria uma decisão que já custou investigação."""
    mc = registro.get("manual_correction") or {}
    return set(mc.get("locked_fields") or [])


def verificar_registro(registro: dict, html: str, *,
                       agora: int | None = None,
                       tolerancia: int = TOLERANCIA_S) -> dict:
    """Decide para UM registro. Devolve só os campos que devem ser gravados."""
    travados = campos_travados(registro)
    if "pub_ts" in travados or "pub_iso" in travados:
        return {"pub_date_verification": "ignorado_correcao_manual",
                "pub_date_policy": POLICY_VERSION}
    pagina = extrair_data_da_pagina(
        html, url=registro.get("canonical_url") or registro.get("url") or "",
        headline=registro.get("title") or "", agora=agora)
    decisao = decidir_data_efetiva(int(registro.get("pub_ts") or 0), pagina,
                                   tolerancia=tolerancia)
    return campos_de_proveniencia(registro, pagina, decisao)
