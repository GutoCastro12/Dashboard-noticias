#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_rehearsal.py — 4I.2 R7c §10–§47.

A ESCADA COMPLETA, MEDIDA EM DADOS REAIS, SEM UMA ÚNICA CHAMADA DE LLM.

R0-EXTENDED é a novidade barata: o parser de produção lê `description` e
descarta `content:encoded` e os campos ricos do Atom. Para o Google News isso
não custa nada — a `description` é a manchete repetida. Para os feeds próprios
custa o artigo inteiro. Medir isso separadamente importa porque é ganho SEM
REQUISIÇÃO: se boa parte da cobertura vier daí, a discussão de robots e
paywall perde urgência.

A ESCADA PARA CEDO POR DESENHO. R1 só roda se R0-EXTENDED não bastar; R2 só se
R1 não bastar. E o enrichment é UMA VEZ POR ARTIGO — não por empresa, não por
evento, não por família. Um artigo que cita três emissores é uma requisição,
não três.

`best_input` é TAXONOMY-NEUTRAL. Nenhuma sentença é escolhida por estar perto
de "default", "aquisição" ou "fraude". Recortar por keyword produziria um texto
que só confirma o que a taxonomia já sabe — e a descoberta aberta, que é o
motivo de tudo isto, morreria antes de nascer.

CONTRATO HERDADO E MANTIDO: contexto sujo é pior que nenhum contexto. Um menu
de site já reintroduziu um falso positivo corrigido (caso W&W); fragmento que
não passa no filtro não entra nem como "melhor esforço".

Sem LLM, sem backfill, sem escrita em produção.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import statistics
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import link_debt_audit as lda
import risk_dashboard as rd
import reliability_input_layer as il
import reliability_input_capture as ic
import reliability_pilot_input as pi
from reliability_pilot_contract import normalizar

REHEARSAL_VERSION = "r7c.rehearsal1"
EXTRACTOR_VERSION = "r7c.x1"
NORMALIZATION_VERSION = "r7c.n1"
POLICY_VERSION = "r7c.policy2"
SCHEMA_VERSION = "r7c.s1"

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7c"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))

MAX_FETCH_ARTIGOS = 40          # §26 — teto do rehearsal LOCAL (o prospectivo passa o seu)
PAUSA_POR_HOST = 1.5            # §17 — espacamento a partir da 2a requisicao ao mesmo host
BEST_INPUT_CAP = 8000           # §17
CAP_MINIMO_REPORTADO = 5000

# ── §13 taxonomia de falha ──────────────────────────────────────────────────
ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
HTTP_403 = "HTTP_403"
HTTP_429 = "HTTP_429"
HTTP_404 = "HTTP_404"
PAYWALL = "PAYWALL"
TIMEOUT = "TIMEOUT"
PARSE_FAILED = "PARSE_FAILED"
EMPTY = "EMPTY"
THIN_AFTER_ENRICHMENT = "THIN_AFTER_ENRICHMENT"
DIRTY_ONLY = "DIRTY_ONLY"
RESOLUTION_FAILED = "RESOLUTION_FAILED"
CAP_REACHED = "CAP_REACHED"
OTHER = "OTHER"
OK = "OK"
FALHAS = (ROBOTS_BLOCKED, HTTP_403, HTTP_429, HTTP_404, PAYWALL, TIMEOUT,
          PARSE_FAILED, EMPTY, THIN_AFTER_ENRICHMENT, DIRTY_ONLY,
          RESOLUTION_FAILED, CAP_REACHED, OTHER)

TIER_R0_LEGACY = "R0_LEGACY"
TIER_R0_EXT = "R0_EXTENDED"
TIER_R1 = "R1_STRUCTURED"
TIER_R2 = "R2_BODY"
TIER_NENHUM = "THIN"

_PAYWALL = re.compile(
    r"(assine|assinante|subscribe to (?:read|continue)|suscr[ií]b|"
    r"para continuar lendo|conte[uú]do exclusivo para|paywall)", re.I)
_BOILER = re.compile(
    r"(cookie|aceitar todos|newsletter|todos os direitos|all rights reserved|"
    r"leia mais|read more|compartilhe|siga[- ]nos|menu principal|"
    r"pular para o conte[uú]do|skip to (?:main )?content)", re.I)
_NAV = re.compile(r"(^|\s)(home|in[ií]cio|contato|sobre n[óo]s|termos de uso|"
                  r"pol[ií]tica de privacidade)(\s|$)", re.I)


_BREADCRUMB = re.compile(
    r"^(?:\s*(?:home|in[ií]cio|not[ií]cias|news|secci[oó]n|se[cç][aã]o|blog|"
    r"artigos?|colunas?|mercados?|economia|neg[oó]cios|empresas)(?![\wáéíóúãõçñ])[\s>|/·–—-]*){1,6}",
    re.I)
_RODAPE = re.compile(
    r"(?:leia mais|read more|compartilhe|siga[- ]nos|newsletter|"
    r"todos os direitos reservados|all rights reserved|"
    r"aceit(?:ar|e) (?:todos os )?cookies)[\s\S]{0,200}$", re.I)


def limpar_bordas(titulo: str, texto: str) -> tuple:
    """Remove breadcrumb no início e rodapé no fim — nunca no meio.

    O caso Ambev: 1200 caracteres de corpo narrativo real precedidos de
    "Home Notícias <título>". A regra anterior via "Home" na posição 0 e
    descartava o texto inteiro. O contrato R5b é barrar menu NO LUGAR DE
    narrativa, não narrativa PRECEDIDA POR menu — e o caso W&W continua
    reprovado porque, removidas as bordas, não sobra nenhuma frase.

    Recortar só nas bordas é deliberado: remover do meio começaria a fabricar
    um texto que não existe na página."""
    t = normalizar(texto or "")
    if not t:
        return "", []
    removido = []
    m = _BREADCRUMB.match(t)
    if m and m.end() < len(t) * 0.5:
        removido.append(("breadcrumb", t[:m.end()].strip()))
        t = t[m.end():].lstrip(" >|/·–—-")
    tit = normalizar(titulo or "")
    if tit:
        while t.lower().startswith(tit.lower()):
            t = t[len(tit):].lstrip(" .:-–—|>")
            removido.append(("titulo_repetido", tit))
    m2 = _RODAPE.search(t)
    if m2 and m2.start() > len(t) * 0.5:
        removido.append(("rodape", t[m2.start():].strip()[:80]))
        t = t[:m2.start()].rstrip()
    return t.strip(), removido


# ── §14 componentes de qualidade ────────────────────────────────────────────
def componentes(titulo: str, texto: str) -> dict:
    """Nunca reduzir suficiência a `chars >= N`. Os componentes ficam
    visíveis para que a conclusão possa ser contestada — foi assim que a R7a
    descobriu que estava contando sentinela como conclusão."""
    t, bordas = limpar_bordas(titulo, texto)
    tit = normalizar(titulo or "")
    frases = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    sent_like = [s for s in frases if len(s) > 40 and " " in s.strip()
                 and re.search(r"[a-záéíóúãõçñ]{3}", s, re.I)]
    toks_t = set(re.findall(r"[\wáéíóúãõâêôçñ]{4,}", t.lower()))
    toks_tit = set(re.findall(r"[\wáéíóúãõâêôçñ]{4,}", tit.lower()))
    novos = toks_t - toks_tit
    sobrep = (len(toks_t & toks_tit) / max(1, len(toks_tit))) if toks_tit else 0.0
    return {
        "useful_chars": pi.chars_uteis(t, tit),
        "sentence_like_count": len(sent_like),
        "unique_meaningful_tokens": len(toks_t),
        "meaningful_gain_vs_title": len(novos),
        "title_summary_overlap": round(sobrep, 3),
        "title_duplication": round(min(1.0, len(tit) / max(1, len(t))), 3)
        if tit and tit.lower() in t.lower() else 0.0,
        "boilerplate_flag": bool(_BOILER.search(t)),
        "nav_flag": bool(_NAV.search(t)),
        "paywall_flag": bool(_PAYWALL.search(t)),
        "chars_totais": len(t),
        "bordas_removidas": [b[0] for b in bordas],
    }


# ── §15 três políticas ──────────────────────────────────────────────────────
def _sujo(c: dict) -> bool:
    return c["boilerplate_flag"] or c["nav_flag"]


POLITICAS = {
    # Aceita menos texto se for claramente narrativo e trouxer conteúdo novo.
    "PERMISSIVE": dict(min_chars=250, min_sent=2, min_novos=15),
    # Recomendada: exige narrativa reconhecível e ganho real sobre o título.
    "SELECTED": dict(min_chars=400, min_sent=3, min_novos=30),
    # Exige contexto robusto; tende a rejeitar og:description curta.
    "CONSERVATIVE": dict(min_chars=900, min_sent=6, min_novos=70),
}


def pronto(c: dict, politica: str = "SELECTED") -> dict:
    p = POLITICAS[politica]
    falhas = []
    if c["useful_chars"] < p["min_chars"]:
        falhas.append(f"useful_chars<{p['min_chars']}")
    if c["sentence_like_count"] < p["min_sent"]:
        falhas.append(f"sentences<{p['min_sent']}")
    if c["meaningful_gain_vs_title"] < p["min_novos"]:
        falhas.append(f"novos_tokens<{p['min_novos']}")
    if _sujo(c):
        falhas.append("dirty")
    return {"input_ready_under_r7c_policy": not falhas, "faltou": falhas,
            "policy": politica, "policy_version": POLICY_VERSION}


# ── §11/§35 R0-EXTENDED: campos ricos do próprio feed ───────────────────────
NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
NS_ATOM = "{http://www.w3.org/2005/Atom}"


def campos_ricos(item) -> dict:
    """Todos os campos textuais que o item traz, com o método nomeado.

    Separar por método é o que permite responder §35 sem generalizar: se o
    ganho for só de um feed, o número agregado esconderia isso."""
    out = {}

    def _add(metodo, bruto):
        t = ic._texto_limpo(bruto or "")
        if t:
            out[metodo] = t

    _add("rss:description", item.findtext("description"))
    _add("content:encoded", item.findtext(NS_CONTENT))
    if "content:encoded" not in out:
        for ch in item:
            if ch.tag.endswith("encoded") and ch.text:
                _add("content:encoded", ch.text)
                break
    _add("atom:content", item.findtext(NS_ATOM + "content"))
    _add("atom:summary", item.findtext(NS_ATOM + "summary"))
    for tag in ("summary", "fulltext", "encoded"):
        if f"rss:{tag}" not in out:
            _add(f"rss:{tag}", item.findtext(tag))
    return out


def r0_extended(titulo: str, ricos: dict) -> tuple:
    """Escolhe o melhor campo já baixado. Ordem por RIQUEZA medida, não por
    preferência declarada: um `atom:summary` longo vence um `content:encoded`
    vazio."""
    melhor, metodo, melhor_c = "", TIER_R0_LEGACY, None
    for m, t in ricos.items():
        c = componentes(titulo, t)
        if melhor_c is None or c["useful_chars"] > melhor_c["useful_chars"]:
            melhor, metodo, melhor_c = t, m, c
    return melhor, metodo, (melhor_c or componentes(titulo, ""))


# ── §17/§19/§30 best_input ──────────────────────────────────────────────────
def montar_best_input(titulo: str, fragmentos: list) -> dict:
    """Título + melhor texto, sem HTML, sem duplicação óbvia, sem recorte por
    keyword. `fragmentos` é [(metodo, texto)], em ordem de preferência já
    resolvida pela escada."""
    partes, proveniencia, visto = [], [], set()
    tit = normalizar(titulo or "")
    if tit:
        partes.append(tit)
        proveniencia.append({"metodo": "title", "chars": len(tit)})
    for metodo, texto in fragmentos:
        t = normalizar(texto or "")
        if not t:
            continue
        # duplicação óbvia: o fragmento repete o título ou outro fragmento
        chave = t[:160].lower()
        if chave in visto:
            continue
        if tit and t.lower() == tit.lower():
            continue
        visto.add(chave)
        if tit and t.lower().startswith(tit.lower()):
            t = t[len(tit):].lstrip(" .-–—:")
        if not t:
            continue
        partes.append(t)
        proveniencia.append({"metodo": metodo, "chars": len(t)})
    texto = re.sub(r"\s+", " ", " ".join(partes)).strip()
    bruto = len(texto)
    truncado = bruto > BEST_INPUT_CAP
    if truncado:
        corte = texto[:BEST_INPUT_CAP]
        ult = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
        texto = corte[:ult + 1] if ult > BEST_INPUT_CAP * 0.6 else corte
    return {"best_input": texto, "chars_antes_do_cap": bruto,
            "truncado": truncado, "provenance": proveniencia,
            "content_hash": content_hash(texto),
            "normalization_version": NORMALIZATION_VERSION}


def content_hash(texto: str) -> str:
    """Hash do TEXTO normalizado. Espaço em branco e mudança de procedência não
    alteram; fragmento novo altera. É o que permitirá cache de LLM sem
    reprocessar o corpus a cada mudança de metadado."""
    t = re.sub(r"\s+", " ", normalizar(texto or "")).strip().lower()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:32]


# ── §24 diagnóstico do Google News ──────────────────────────────────────────
def diagnosticar_gnews(urls: list, *, permitir_rede: bool = False,
                       limite: int | None = None) -> dict:
    """Quantos links são wrapper, quantos resolvem, e a que custo."""
    cache, S = {}, requests.Session()
    tel = collections.Counter()
    resolvidos = []
    for u in (urls[:limite] if limite else urls):
        if lda.is_redirector(u):
            tel["wrapper"] += 1
            if not permitir_rede:
                tel["nao_tentado"] += 1
                continue
            try:
                r = lda.resolve_gnews_token(u, session=S, cache=cache,
                                            allow_network=True)
            except Exception as exc:
                tel[f"erro:{type(exc).__name__}"] += 1
                continue
            if r.get("url") and not r.get("error"):
                tel["resolvido"] += 1
                tel[f"metodo:{r.get('method', '?')}"] += 1
                resolvidos.append({"gnews": u, "publisher": r["url"],
                                   "metodo": r.get("method", "")})
            else:
                tel["resolucao_falhou"] += 1
        else:
            tel["direto"] += 1
            resolvidos.append({"gnews": u, "publisher": u, "metodo": "direto"})
    return {"telemetria": dict(tel), "resolvidos": resolvidos,
            "total": len(urls[:limite] if limite else urls)}


# ── §12 R1/R2 via infraestrutura existente ──────────────────────────────────
def classificar_falha(reg: dict, texto_sujo: bool = False) -> str:
    st = (reg.get("status") or "").upper()
    err = (str(reg.get("error") or "") + " " + str(reg.get("robots_status") or "")).upper()
    if "ROBOTS" in st or "ROBOTS" in err:
        return ROBOTS_BLOCKED
    if "403" in st or "403" in err:
        return HTTP_403
    if "429" in st or "429" in err:
        return HTTP_429
    if "404" in st or "404" in err:
        return HTTP_404
    if "TIMEOUT" in st or "TIMEOUT" in err:
        return TIMEOUT
    if "PARSE" in st or "PARSE" in err:
        return PARSE_FAILED
    if texto_sujo:
        return DIRTY_ONLY
    if st in ("OK", "") and not reg.get("fragments"):
        return EMPTY
    return OTHER if st not in ("OK",) else OK


def resolver_url(url: str, *, permitir_rede: bool, contador: dict) -> tuple:
    """Wrapper do Google News → URL do publisher, pelo resolvedor canônico."""
    if not lda.is_redirector(url):
        return url, "direto", ""
    if not permitir_rede:
        return url, "nao_resolvido", RESOLUTION_FAILED
    try:
        r = lda.resolve_gnews_token(url, session=contador.setdefault(
            "session", requests.Session()), cache=contador.setdefault(
            "cache_resolucao", {}), allow_network=True)
        contador["resolucoes"] = contador.get("resolucoes", 0) + 1
    except Exception:
        return url, "erro", RESOLUTION_FAILED
    if r.get("url") and not r.get("error") and not lda.is_redirector(r["url"]):
        return r["url"], r.get("method", "?"), ""
    return url, "falhou", RESOLUTION_FAILED


def enriquecer_uma_vez(url: str, titulo: str, rec: dict, *,
                       sidecar: dict, permitir_rede: bool,
                       contador: dict) -> dict:
    """UMA requisição por ARTIGO (§21). O contador é compartilhado entre
    chamadas e é a prova de que empresas repetidas não geram fetch novo."""
    art_id = il.identidade(url)
    if art_id in contador["por_artigo"]:
        contador["duplicatas_evitadas"] += 1
        return contador["por_artigo"][art_id]

    alvo, metodo_res, falha_res = resolver_url(
        url, permitir_rede=permitir_rede, contador=contador)
    if falha_res:
        out = {"texto": "", "metodo": "", "origem": "NAO_RESOLVIDO",
               "falha": falha_res, "tier": TIER_NENHUM,
               "url_resolvida": "", "metodo_resolucao": metodo_res}
        contador["por_artigo"][art_id] = out
        return out

    reg = (((sidecar or {}).get("articles") or {}).get(url)
           or ((sidecar or {}).get("articles") or {}).get(alvo) or {})
    origem = "REUSED" if reg else "NAO_TENTADO"
    # §29 — compatibilidade explícita: schema/extractor desconhecido não é
    # reutilizado em silêncio.
    if reg:
        sv = str(reg.get("schema_version") or "")
        if sv and sv not in ("1.0", "1.1"):
            reg, origem = {}, "INCOMPATIBLE"
    # O teto vem do CONTADOR, não de uma constante de módulo. Antes o
    # `max_fetch` do coletor prospectivo era decorativo: `coletar()` recebia o
    # parâmetro e nunca o repassava, então quem mandava de fato era esta
    # constante — mudar o número no lugar errado não teria efeito algum.
    limite = contador.get("limite_fetch", MAX_FETCH_ARTIGOS)
    if not reg and permitir_rede and contador["fetches"] >= limite:
        out = {"texto": "", "metodo": "", "origem": "CAP",
               "falha": CAP_REACHED, "tier": TIER_NENHUM,
               "url_resolvida": alvo, "metodo_resolucao": metodo_res}
        contador["por_artigo"][art_id] = out
        return out
    if not reg and permitir_rede and contador["fetches"] < limite:
        # §17 — dobrar o teto global não pode virar martelo num único
        # publisher. Espaçamento mínimo por HOST, contado só quando há mais de
        # uma requisição ao mesmo domínio no run.
        host = lda._host(alvo) or "?"
        por_host = contador.setdefault("por_host", collections.Counter())
        if por_host[host] >= 1:
            time.sleep(PAUSA_POR_HOST)
        por_host[host] += 1
        try:
            import reliability_enrichment_sidecar as sc
            reg = sc.enriquecer_url(alvo, titulo, rec or {})
            origem = "FETCHED"
            contador["fetches"] += 1
        except Exception as exc:
            reg = {"status": f"ERRO_{type(exc).__name__}"}
            origem = "FETCHED"
            contador["fetches"] += 1
    texto, metodo = pi._fragmentos_uteis(reg, titulo) if reg else ("", "")
    sujo = bool(reg.get("fragments")) and not texto
    falha = OK if texto else classificar_falha(reg, texto_sujo=sujo)
    out = {"texto": texto, "metodo": metodo, "origem": origem, "falha": falha,
           "tier": (TIER_R2 if "paragrafo" in (metodo or "") else TIER_R1)
                   if texto else TIER_NENHUM,
           "url_resolvida": alvo, "metodo_resolucao": metodo_res}
    contador["por_artigo"][art_id] = out
    return out


# ── a escada completa por artigo ────────────────────────────────────────────
def processar_artigo(*, url: str, titulo: str, resumo: str, dominio: str,
                     pub_iso: str, empresas: dict, ricos: dict | None,
                     rec: dict, sidecar: dict, permitir_rede: bool,
                     contador: dict, politica: str = "SELECTED",
                     query_kind: str = "", fonte: str = "") -> dict:
    legacy_txt = f"{titulo}. {resumo}".strip() if resumo else titulo
    c_legacy = componentes(titulo, legacy_txt)
    r_legacy = pronto(c_legacy, politica)

    ext_txt, ext_metodo, c_ext = r0_extended(titulo, ricos or {})
    if c_ext["useful_chars"] < c_legacy["useful_chars"]:
        ext_txt, ext_metodo, c_ext = legacy_txt, TIER_R0_LEGACY, c_legacy
    r_ext = pronto(c_ext, politica)

    frags = [(ext_metodo, ext_txt)]
    tier, falha = (TIER_R0_EXT if ext_metodo != TIER_R0_LEGACY
                   else TIER_R0_LEGACY), OK
    r1_tentado = r2_tentado = False
    enr = None
    if not r_ext["input_ready_under_r7c_policy"]:
        r1_tentado = True
        enr = enriquecer_uma_vez(url, titulo, rec, sidecar=sidecar,
                                 permitir_rede=permitir_rede,
                                 contador=contador)
        falha = enr["falha"]
        if enr["texto"]:
            frags.append((enr["metodo"], enr["texto"]))
            tier = enr["tier"]
            r2_tentado = enr["tier"] == TIER_R2
        else:
            tier = TIER_NENHUM

    bi = montar_best_input(titulo, frags)
    c_final = componentes(titulo, bi["best_input"])
    r_final = pronto(c_final, politica)
    if falha in (CAP_REACHED, RESOLUTION_FAILED):
        pass
    elif not r_final["input_ready_under_r7c_policy"] and falha == OK:
        falha = (DIRTY_ONLY if _sujo(c_final)
                 else THIN_AFTER_ENRICHMENT if c_final["useful_chars"] > 0
                 else EMPTY)

    return {
        "article_id": il.identidade(url), "url": url, "titulo": titulo,
        "dominio": dominio, "pub_iso": pub_iso,
        "source_kind": il.genero_da_fonte(dominio),
        "query_kind": query_kind, "fonte": fonte,
        "empresas": [{"empresa": e, "candidatos": sorted(v or []),
                      "tem_candidato": bool(v)} for e, v in sorted(empresas.items())],
        "n_empresas": len(empresas),
        "tem_algum_candidato": any(bool(v) for v in empresas.values()),
        "r0_legacy": {**c_legacy, **r_legacy},
        "r0_extended": {**c_ext, **r_ext, "metodo": ext_metodo},
        "r1_tentado": r1_tentado, "r2_tentado": r2_tentado,
        "enrichment": {k: v for k, v in (enr or {}).items() if k != "texto"},
        "tier_final": tier, "falha": falha,
        "final": {**c_final, **r_final},
        "best_input_chars": len(bi["best_input"]),
        "chars_antes_do_cap": bi["chars_antes_do_cap"],
        "truncado": bi["truncado"], "provenance": bi["provenance"],
        "content_hash": bi["content_hash"],
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "policy_version": POLICY_VERSION,
        "_best_input": bi["best_input"],
    }


# ── §28/§36 funil ───────────────────────────────────────────────────────────
def funil(regs: list, contador: dict) -> dict:
    n = len(regs)
    def _c(f): return sum(1 for r in regs if f(r))
    falhas = collections.Counter(r["falha"] for r in regs)
    return {
        "artigos_unicos": n,
        "pares_empresa_artigo": sum(r["n_empresas"] for r in regs),
        "com_candidato": _c(lambda r: r["tem_algum_candidato"]),
        "sem_candidato": _c(lambda r: not r["tem_algum_candidato"]),
        "r0_legacy_ready": _c(lambda r: r["r0_legacy"]["input_ready_under_r7c_policy"]),
        "r0_extended_ready": _c(lambda r: r["r0_extended"]["input_ready_under_r7c_policy"]),
        "r1_attempted": _c(lambda r: r["r1_tentado"]),
        "r1_ready": _c(lambda r: r["tier_final"] == TIER_R1
                       and r["final"]["input_ready_under_r7c_policy"]),
        "r2_attempted": _c(lambda r: r["r2_tentado"]),
        "r2_ready": _c(lambda r: r["tier_final"] == TIER_R2
                       and r["final"]["input_ready_under_r7c_policy"]),
        "final_ready": _c(lambda r: r["final"]["input_ready_under_r7c_policy"]),
        "final_insufficient": _c(lambda r: not r["final"]["input_ready_under_r7c_policy"]),
        "falhas": dict(falhas),
        "tiers": dict(collections.Counter(r["tier_final"] for r in regs)),
        "network_fetches": contador["fetches"],
        "limite_fetch": contador.get("limite_fetch", MAX_FETCH_ARTIGOS),
        "requests_por_host": dict((contador.get("por_host") or {}).most_common(8))
        if contador.get("por_host") else {},
        "max_requests_um_host": (max((contador.get("por_host") or {}).values())
                                 if contador.get("por_host") else 0),
        "resolucoes": contador.get("resolucoes", 0),
        "duplicatas_evitadas": contador["duplicatas_evitadas"],
        "requests_evitadas_por_dedup": sum(r["n_empresas"] for r in regs) - n,
        "nao_tentados_por_cap": _c(lambda r: r["falha"] == CAP_REACHED),
        "resolucao_falhou": _c(lambda r: r["falha"] == RESOLUTION_FAILED),
        "denominador_tentado": _c(lambda r: r["falha"] not in (CAP_REACHED,
                                                              RESOLUTION_FAILED)),
        "reuso_sidecar": sum(1 for r in regs
                             if (r.get("enrichment") or {}).get("origem") == "REUSED"),
        "truncados": _c(lambda r: r["truncado"]),
        "mediana_final_chars": int(statistics.median(
            [r["final"]["useful_chars"] for r in regs])) if regs else 0,
    }


def marginal(regs: list) -> dict:
    def _d(a, b, campo):
        vs = [r[a][campo] - r[b][campo] for r in regs]
        return {"mediana": int(statistics.median(vs)) if vs else 0,
                "positivos": sum(1 for v in vs if v > 0)}
    return {
        "R0EXT_vs_R0LEGACY": {
            "useful_chars": _d("r0_extended", "r0_legacy", "useful_chars"),
            "sentences": _d("r0_extended", "r0_legacy", "sentence_like_count"),
            "ready_transitions": sum(
                1 for r in regs
                if r["r0_extended"]["input_ready_under_r7c_policy"]
                and not r["r0_legacy"]["input_ready_under_r7c_policy"])},
        "FINAL_vs_R0EXT": {
            "useful_chars": _d("final", "r0_extended", "useful_chars"),
            "sentences": _d("final", "r0_extended", "sentence_like_count"),
            "ready_transitions": sum(
                1 for r in regs
                if r["final"]["input_ready_under_r7c_policy"]
                and not r["r0_extended"]["input_ready_under_r7c_policy"])},
    }


def sensibilidade(regs: list) -> dict:
    """Reavalia as tres politicas sobre os MESMOS componentes ja extraidos.

    Refazer a escada por politica mediria disponibilidade de rede, nao rigor
    de politica: o denominador tem de ser identico nas tres colunas."""
    out = {}
    for pol in POLITICAS:
        r0l = sum(1 for r in regs if pronto(r["r0_legacy"], pol)["input_ready_under_r7c_policy"])
        r0e = sum(1 for r in regs if pronto(r["r0_extended"], pol)["input_ready_under_r7c_policy"])
        fin = [r for r in regs if pronto(r["final"], pol)["input_ready_under_r7c_policy"]]
        chars = [r["final"]["useful_chars"] for r in regs]
        out[pol] = {
            "r0_legacy_ready": r0l, "r0_extended_ready": r0e,
            "final_ready": len(fin), "final_insufficient": len(regs) - len(fin),
            "mediana_final_chars": int(statistics.median(chars)) if chars else 0,
            "mediana_chars_aceitos": int(statistics.median(
                [r["final"]["useful_chars"] for r in fin])) if fin else 0,
            "dirty_rejeitados": sum(1 for r in regs if _sujo(r["final"])),
        }
    return out


def cobertura(regs: list, chave) -> dict:
    g = collections.defaultdict(list)
    for r in regs:
        g[chave(r)].append(r)
    return {k: {"N": len(v),
                "r0_ext_ready": sum(1 for r in v if r["r0_extended"]["input_ready_under_r7c_policy"]),
                "final_ready": sum(1 for r in v if r["final"]["input_ready_under_r7c_policy"]),
                "blocked": sum(1 for r in v if r["falha"] in (ROBOTS_BLOCKED, HTTP_403, HTTP_429, PAYWALL)),
                "falhas": dict(collections.Counter(r["falha"] for r in v))}
            for k, v in sorted(g.items())}
