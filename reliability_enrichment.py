#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment.py — 4I.2 R5a. SHADOW, nunca produção.

Mede se dá para obter das fontes o sinal semântico que o history não tem. O
`input_audit` mostrou que o classificador enxerga praticamente só o título;
antes de decidir qualquer arquitetura de enriquecimento, é preciso saber se o
texto adicional EXISTE, se é OBTÍVEL sem burlar ninguém, e se ele de fato
resolve os casos hoje `BLOCKED_BY_INPUT`.

Regras que este módulo respeita, por construção:
  · `robots.txt` é consultado ANTES de cada busca; `disallow` vira resultado
    `BLOCKED_BY_ROBOTS` e a URL não é buscada. Não há bypass.
  · nada de LLM, embedding ou NER — só HTTP, JSON-LD, meta tags e regex.
  · o resultado vai para arquivo separado; `risk_history.json` não é tocado.
  · uma requisição por artigo, com pausa entre hosts.

Uso:
    python reliability_enrichment.py --scope duke      # só os 3 bloqueados
    python reliability_enrichment.py --scope amostra   # escopo do R5a §12
    python reliability_enrichment.py --shadow          # diff semântico
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.robotparser
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import requests

import reliability_input_audit as ia

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY") or "risk_history.json")
REVIEWS = Path(os.environ.get("RELIABILITY_REVIEWS")
               or "test_fixtures_reliability/live_reviews.json")
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR") or "out_reliability")
SHADOW = OUTDIR / "enrichment_shadow.json"

UA = ("Mozilla/5.0 (compatible; DashRiskEnrichmentAudit/1.0; "
      "+shadow-only; contact=risco)")
TIMEOUT = 20
PAUSA_ENTRE_HOSTS = 1.0

# Marcas de boilerplate que costumam contaminar extração de corpo.
BOILERPLATE = [
    (r"cookie|consent|aceitar (?:os )?cookies|gerenciar prefer", "cookie_consent"),
    (r"newsletter|assine|inscreva-se|subscribe", "newsletter"),
    (r"leia (?:tambem|mais)|read more|veja tambem|related stories", "leia_tambem"),
    (r"compartilh|share this|siga-nos|follow us", "social"),
    (r"todos os direitos reservados|all rights reserved|copyright", "rodape"),
    (r"assinante|paywall|para continuar lendo|subscribers only", "paywall"),
    (r"menu|navega[cç][aã]o|trending|mais lidas|most read", "navegacao"),
    # Extração de <p> pode capturar script inline quando o HTML é malformado —
    # aconteceu de verdade num dos artigos auditados, e o texto resultante
    # venceu a escolha por volume. Sem esta marca, lixo vira "enriquecimento".
    (r"function\s*\(|typeof |window\.|prototype|xmlhttprequest|"
     r"addeventlistener|var \w+=|\breturn\b.{0,20}\bfunction", "codigo_javascript"),
]

_robots_cache: dict = {}


def _robots_permite(url: str) -> tuple[bool, str]:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
            _robots_cache[base] = rp
        except Exception as exc:                       # noqa: BLE001
            _robots_cache[base] = exc
    rp = _robots_cache[base]
    if isinstance(rp, Exception):
        # robots inacessível não é permissão: registramos e seguimos com o UA
        # identificado, que é o comportamento padrão de um leitor de feed.
        return True, f"robots_indisponivel ({type(rp).__name__})"
    try:
        return bool(rp.can_fetch(UA, url)), "robots_lido"
    except Exception:                                  # noqa: BLE001
        return True, "robots_ilegivel"


def _texto(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", unescape(s)).strip()


def _jsonld(html: str) -> list:
    out = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            out.append(json.loads(m.group(1).strip()))
        except Exception:                              # noqa: BLE001
            continue
    return out


def _achatar(obj) -> list:
    if isinstance(obj, list):
        return [x for o in obj for x in _achatar(o)]
    if isinstance(obj, dict):
        saida = [obj]
        for k in ("@graph", "mainEntity", "mainEntityOfPage"):
            if k in obj:
                saida += _achatar(obj[k])
        return saida
    return []


def _meta(html: str, *nomes) -> str:
    for nome in nomes:
        for pat in (rf'<meta[^>]+property="{nome}"[^>]+content="([^"]*)"',
                    rf'<meta[^>]+content="([^"]*)"[^>]+property="{nome}"',
                    rf'<meta[^>]+name="{nome}"[^>]+content="([^"]*)"',
                    rf'<meta[^>]+content="([^"]*)"[^>]+name="{nome}"'):
            m = re.search(pat, html, re.I)
            if m and m.group(1).strip():
                return _texto(m.group(1))
    return ""


def _paragrafos(html: str, limite: int = 1200) -> str:
    """Excerto controlado do corpo: só <p> com frase de verdade."""
    ps = []
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.S | re.I):
        t = _texto(m.group(1))
        if len(t) >= 60 and t.count(" ") >= 8:
            ps.append(t)
        if sum(len(x) for x in ps) > limite:
            break
    return " ".join(ps)[:limite]


def _boilerplate(txt: str) -> list:
    n = ia._norm(txt)
    return sorted({tag for pat, tag in BOILERPLATE if re.search(pat, n)})


def extrair(html: str) -> list:
    """Todas as extrações determinísticas possíveis, por método."""
    achados = []
    for bloco in _achatar(_jsonld(html)):
        for campo, metodo in (("articleBody", "jsonld:articleBody"),
                              ("description", "jsonld:description")):
            v = bloco.get(campo)
            if isinstance(v, str) and v.strip():
                achados.append((metodo, _texto(v)))
    for nomes, metodo in ((("og:description",), "meta:og:description"),
                          (("description",), "meta:description"),
                          (("twitter:description",), "meta:twitter:description")):
        v = _meta(html, *nomes)
        if v:
            achados.append((metodo, v))
    corpo = _paragrafos(html)
    if corpo:
        achados.append(("html:paragrafos", corpo))
    return achados


def enriquecer(url: str, titulo: str, sumario: str) -> dict:
    """Um artigo. Devolve o registro do schema §11 — sempre, mesmo em falha."""
    reg = {"url": url, "title": titulo, "current_summary": sumario,
           "fetched_at": int(time.time()), "enrichment": [], "quality": {}}
    permitido, motivo_robots = _robots_permite(url)
    reg["quality"]["robots"] = motivo_robots
    if not permitido:
        reg["quality"].update(extraction_success=False, reason="BLOCKED_BY_ROBOTS")
        return reg
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        reg["quality"]["http_status"] = r.status_code
        if r.status_code >= 400:
            reg["quality"].update(
                extraction_success=False,
                reason=("BLOCKED_BY_SOURCE" if r.status_code in (401, 402, 403, 451)
                        else f"HTTP_{r.status_code}"))
            return reg
        if "charset" not in (r.headers.get("Content-Type") or "").lower():
            r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
    except requests.Timeout:
        reg["quality"].update(extraction_success=False, reason="TIMEOUT")
        return reg
    except Exception as exc:                           # noqa: BLE001
        reg["quality"].update(extraction_success=False,
                              reason=f"ERRO:{type(exc).__name__}")
        return reg

    reg["quality"]["content_hash"] = hashlib.sha256(html.encode("utf-8",
                                                                "replace")).hexdigest()[:16]
    base = f"{titulo}. {sumario}"
    for metodo, texto in extrair(html):
        g = ia.ganho_efetivo(base, texto)
        reg["enrichment"].append({
            "source_kind": urlparse(url).netloc,
            "extraction_method": metodo,
            "text": texto[:4000],
            "text_length": len(texto),
            "effective_new_tokens": g["tokens_novos"],
            "effective_new_chars": g["chars_novos"],
            "containment": g["containment"],
            "duplicated_title": g["duplicado"],
            "boilerplate": _boilerplate(texto),
        })
    reg["quality"]["extraction_success"] = bool(reg["enrichment"])
    reg["quality"]["reason"] = "OK" if reg["enrichment"] else "SEM_CAMPO_EXTRAIVEL"
    return reg


def melhor(reg: dict) -> dict | None:
    """A extração com mais conteúdo novo e sem boilerplate detectado."""
    cands = [e for e in reg.get("enrichment") or [] if not e["duplicated_title"]]
    if not cands:
        return None
    limpos = [e for e in cands if not e["boilerplate"]] or cands
    return max(limpos, key=lambda e: e["effective_new_tokens"])


def _alvos(escopo: str) -> list:
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    revs = json.load(io.open(REVIEWS, encoding="utf-8"))
    import reliability_live_audit as la
    res = la.coletar()
    crit = [l for l in res["linhas"] if l["severity"] == "critico"]
    alto = [l for l in res["linhas"] if l["severity"] == "alto"]

    def _mk(l, grupo):
        rec = hist["articles"][l["url"]]
        return {"grupo": grupo, "url": l["url"], "company": l["company"],
                "event_id": l["event_id"], "title": rec.get("title") or "",
                "summary": rec.get("summary") or "",
                "review": l["review_status"], "source": l["source"]}

    fp = [_mk(l, "FP_BLOQUEADO") for l in crit if l["review_status"] == "FALSE_POSITIVE"]
    if escopo == "duke":
        return fp
    tru = [_mk(l, "CONTROLE_TRUE") for l in crit if l["review_status"] == "TRUE"][:6]
    alt = [_mk(l, "AMOSTRA_ALTO") for l in alto][:6]
    hol = [_mk(l, "HOLDOUT") for l in res["holdout"]][:4]
    vistos, saida = set(), []
    for x in fp + tru + alt + hol:
        k = (x["url"], x["company"], x["event_id"])
        if k not in vistos:
            vistos.add(k)
            saida.append(x)
    return saida


def coletar_shadow(escopo: str) -> dict:
    alvos = _alvos(escopo)
    OUTDIR.mkdir(exist_ok=True)
    registros, ultimo_host = [], ""
    t0 = time.time()
    for a in alvos:
        host = urlparse(a["url"]).netloc
        if host == ultimo_host:
            time.sleep(PAUSA_ENTRE_HOSTS)
        ultimo_host = host
        ini = time.time()
        reg = enriquecer(a["url"], a["title"], a["summary"])
        reg.update(grupo=a["grupo"], company=a["company"], event_id=a["event_id"],
                   review=a["review"], source=a["source"],
                   latencia_s=round(time.time() - ini, 2))
        registros.append(reg)
        print(f"  [{a['grupo']:14s}] {reg['quality'].get('reason'):22s} "
              f"{reg['latencia_s']:>5.1f}s  {a['title'][:52]}")
    out = {"gerado_em": int(time.time()), "escopo": escopo,
           "duracao_total_s": round(time.time() - t0, 1), "registros": registros}
    SHADOW.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    escopo = "amostra"
    if "--scope" in sys.argv:
        escopo = sys.argv[sys.argv.index("--scope") + 1]
    r = coletar_shadow(escopo)
    print(f"\n{len(r['registros'])} artigos · {r['duracao_total_s']}s → {SHADOW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
