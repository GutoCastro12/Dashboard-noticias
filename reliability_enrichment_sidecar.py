#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment_sidecar.py — 4I.2 R5b.

Coleta seletiva de contexto, com persistência em SIDE-CAR separado do
`risk_history.json`. Shadow: nada aqui alimenta scoring.

Três decisões de projeto, todas vindas de medição da R5a e não de preferência:

1. STRUCTURED-FIRST. A extração de `<p>` capturou script inline num artigo
   real e, por ter mais tokens, venceu a escolha de melhor excerto. Metadata
   estruturada (JSON-LD, og/meta) tem perfil de risco muito menor, então vem
   primeiro e o corpo só é considerado se ela não bastar.

2. QUALIDADE ANTES DE TAMANHO. A seleção ordena por procedência e limpeza; o
   comprimento é o último critério de desempate. "Maior texto vence" é
   exatamente a regra que escolheu JavaScript.

3. UMA REQUISIÇÃO POR URL. O mesmo HTML alimenta todos os métodos. O
   early-stop economiza PROCESSAMENTO e reduz superfície de contaminação, não
   requisições — a requisição já aconteceu.

Robots é gate de entrada: `disallow` encerra o artigo sem fetch, sem bypass,
sem rota alternativa.

Uso:
    python reliability_enrichment_sidecar.py --sample     # amostra da wave
    python reliability_enrichment_sidecar.py --report     # só telemetria
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import reliability_enrichment as enr
import reliability_enrichment_policy as pol
import reliability_input_audit as ia
import risk_dashboard as rd

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY") or "risk_history.json")
SIDECAR = Path(os.environ.get("RELIABILITY_SIDECAR") or "risk_enrichment_shadow.json")

SCHEMA_VERSION = "1.0"
EXTRACTOR_VERSION = "r5b.1"

# Excerto por fragmento. 1200 caracteres cobrem com folga o lead de uma
# notícia — nos artigos auditados o trecho decisivo apareceu nos primeiros
# ~400 — e mantêm o side-car na ordem de KB por artigo, não MB. Página
# inteira nunca é persistida.
MAX_EXCERPT = 1200
MAX_FRAGMENTOS = 6

# Ladder: procedência em ordem de confiança. Índice menor = mais confiável.
TIER = {
    "jsonld:description": (1, "structured"),
    "jsonld:articleBody": (1, "structured"),
    "meta:og:description": (1, "structured"),
    "meta:description": (1, "structured"),
    "meta:twitter:description": (1, "structured"),
    "html:paragrafos": (2, "page_text"),
}

# Suficiência é TÉCNICA, não semântica: diz que já há texto limpo bastante
# para valer a pena, nunca se a empresa é vítima ou autora — isso continua
# com o runner semântico.
MIN_TOKENS_SUFICIENTE = 8
MIN_CHARS_SENTENCA = 60


def _sentence_like(txt: str) -> bool:
    """Tem cara de frase: comprimento, espaços e pontuação terminal."""
    return (len(txt) >= MIN_CHARS_SENTENCA and txt.count(" ") >= 8
            and bool(re.search(r"[.!?…]", txt)))


def qualidade(texto: str, base: str) -> dict:
    g = ia.ganho_efetivo(base, texto)
    flags = list(enr._boilerplate(texto))
    if g["duplicado"]:
        flags.append("duplicated_base")
    if not _sentence_like(texto):
        flags.append("malformed_text")
    return {"effective_new_tokens": g["tokens_novos"],
            "effective_new_chars": g["chars_novos"],
            "containment": g["containment"],
            "quality_flags": sorted(set(flags)),
            "sentence_like": _sentence_like(texto),
            "length": len(texto)}


def suficiente(frag: dict) -> bool:
    """Parar de descer a ladder? Só com texto limpo e materialmente novo."""
    return (frag["effective_new_tokens"] >= MIN_TOKENS_SUFICIENTE
            and frag["sentence_like"]
            and not frag["quality_flags"])


def _ordem(frag: dict) -> tuple:
    """Qualidade e procedência antes de comprimento (§10)."""
    return (TIER.get(frag["method"], (9, "?"))[0],
            len(frag["quality_flags"]),
            0 if frag["sentence_like"] else 1,
            -frag["effective_new_tokens"],
            -frag["length"])


def selecionar(frags: list) -> tuple[dict | None, str]:
    """Só fragmento LIMPO é selecionado. Sem contexto é melhor que contexto sujo.

    Medido em R5b: no artigo do White & Williams não havia metadata alguma, o
    fallback caiu no Tier 2 e capturou o menu do site — cujas CATEGORIAS
    ("Bankruptcy Sales, Chapter 11") reintroduziram termos de insolvência
    fora do nome do tribunal e ressuscitaram um falso positivo que já havia
    sido corrigido em produção. O fragmento estava marcado com `navegacao` e
    ainda assim foi aceito por ser "o melhor disponível". Não é mais.
    """
    uteis = [f for f in frags if f["effective_new_tokens"] > 0
             and "duplicated_base" not in f["quality_flags"]]
    if not uteis:
        return None, "nenhum fragmento com conteúdo novo"
    limpos = [f for f in uteis if not f["quality_flags"] and f["sentence_like"]]
    if not limpos:
        return None, ("nenhum fragmento limpo; enriquecimento descartado "
                      f"(descartados: {sorted({t for f in uteis for t in f['quality_flags']})})")
    return sorted(limpos, key=_ordem)[0], "melhor fragmento limpo, por procedência"


def processar_html(html: str, base: str) -> tuple[list, bool]:
    """Extrai seguindo a ladder e para cedo se a metadata já bastar."""
    achados = dict(enr.extrair(html))
    frags, early = [], False
    for tier in (1, 2):
        metodos = [m for m, (t, _) in TIER.items() if t == tier and m in achados]
        for m in metodos:
            q = qualidade(achados[m], base)
            frags.append({"method": m, "tier": TIER[m][0], "kind": TIER[m][1],
                          "text_excerpt": achados[m][:MAX_EXCERPT],
                          "content_hash": hashlib.sha256(
                              achados[m].encode("utf-8", "replace")).hexdigest()[:16],
                          **q})
        if tier == 1 and any(suficiente(f) for f in frags):
            early = True
            break                      # §14: metadata bastou, não abre o corpo
    return frags[:MAX_FRAGMENTOS], early


def carregar_sidecar() -> dict:
    if SIDECAR.exists():
        return json.load(io.open(SIDECAR, encoding="utf-8"))
    return {"schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION,
            "policy_version": pol.POLICY_VERSION, "articles": {}}


def _reaproveitavel(anterior: dict) -> bool:
    """§21/§22: só reaproveita o que veio deste extractor e desta política."""
    return (anterior.get("extractor_version") == EXTRACTOR_VERSION
            and anterior.get("policy_version") == pol.POLICY_VERSION
            and anterior.get("status") in ("OK", "BLOCKED_BY_ROBOTS",
                                           "BLOCKED_BY_SOURCE"))


def enriquecer_url(url: str, base: str) -> dict:
    reg = {"attempted_at": int(time.time()), "fragments": [],
           "extractor_version": EXTRACTOR_VERSION,
           "policy_version": pol.POLICY_VERSION,
           "schema_version": SCHEMA_VERSION}
    permitido, robots_status = enr._robots_permite(url)
    reg["robots_status"] = robots_status
    if not permitido:
        reg["status"] = "BLOCKED_BY_ROBOTS"
        return reg
    ini = time.time()
    try:
        import requests
        r = requests.get(url, timeout=enr.TIMEOUT, headers={"User-Agent": enr.UA})
        reg["latency_ms"] = int((time.time() - ini) * 1000)
        reg["http_status"] = r.status_code
        if r.status_code >= 400:
            reg["status"] = ("BLOCKED_BY_SOURCE"
                             if r.status_code in (401, 402, 403, 451)
                             else f"HTTP_{r.status_code}")
            return reg
        if "charset" not in (r.headers.get("Content-Type") or "").lower():
            r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
    except Exception as exc:                            # noqa: BLE001
        reg["latency_ms"] = int((time.time() - ini) * 1000)
        reg["status"] = "ERROR"
        reg["error"] = type(exc).__name__
        return reg

    frags, early = processar_html(html, base)
    sel, motivo = selecionar(frags)
    reg.update(status="OK" if frags else "NO_CONTENT", fragments=frags,
               early_stop=early,
               selected=({"method": sel["method"], "tier": sel["tier"],
                          "content_hash": sel["content_hash"],
                          "effective_new_tokens": sel["effective_new_tokens"],
                          "selection_reason": motivo} if sel else None))
    return reg


def rodar(limite: int = 60) -> dict:
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    side = carregar_sidecar()
    tel = {"elegiveis": 0, "tentados": 0, "cache_hit": 0, "OK": 0,
           "BLOCKED_BY_ROBOTS": 0, "BLOCKED_BY_SOURCE": 0, "ERROR": 0,
           "NO_CONTENT": 0, "early_stop": 0, "latencias": [], "metodos": {}}
    ultimo_host = ""
    for url, rec in hist["articles"].items():
        ok, s = pol.should_enrich(rec, cfg)
        if not ok:
            continue
        tel["elegiveis"] += 1
        ident = rec.get("canonical_url") or url
        anterior = side["articles"].get(ident)
        if anterior and _reaproveitavel(anterior):
            tel["cache_hit"] += 1
            continue
        if tel["tentados"] >= limite:
            continue
        host = urlparse(url).netloc
        if host == ultimo_host:
            time.sleep(enr.PAUSA_ENTRE_HOSTS)
        ultimo_host = host
        base = f"{rec.get('title') or ''}. {rec.get('summary') or ''}"
        reg = enriquecer_url(url, base)
        reg.update(canonical_url=ident, title=(rec.get("title") or "")[:200],
                   eligibility=s)
        side["articles"][ident] = reg
        tel["tentados"] += 1
        tel[reg["status"]] = tel.get(reg["status"], 0) + 1
        tel["early_stop"] += bool(reg.get("early_stop"))
        if reg.get("latency_ms"):
            tel["latencias"].append(reg["latency_ms"])
        for f in reg["fragments"]:
            tel["metodos"][f["method"]] = tel["metodos"].get(f["method"], 0) + 1
        print(f"  [{reg['status']:18s}] early={str(reg.get('early_stop')):5s} "
              f"frags={len(reg['fragments'])} {reg.get('latency_ms', 0):>5d}ms "
              f"{reg['title'][:48]}")

    side.update(schema_version=SCHEMA_VERSION, extractor_version=EXTRACTOR_VERSION,
                policy_version=pol.POLICY_VERSION, gerado_em=int(time.time()))
    json.dump(side, io.open(SIDECAR, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    tel["latencia_media_ms"] = (int(sum(tel["latencias"]) / len(tel["latencias"]))
                                if tel["latencias"] else 0)
    tel.pop("latencias")
    return tel


def main() -> int:
    if "--report" in sys.argv:
        side = carregar_sidecar()
        print(json.dumps({"schema": side.get("schema_version"),
                          "extractor": side.get("extractor_version"),
                          "artigos": len(side.get("articles") or {})},
                         ensure_ascii=False, indent=1))
        return 0
    tel = rodar()
    print("\n" + "=" * 96)
    print("TELEMETRIA DA COLETA SELETIVA")
    print("=" * 96)
    for k, v in tel.items():
        print(f"  {k:22s} {v}")
    print(f"  side-car               {SIDECAR} "
          f"({SIDECAR.stat().st_size / 1024:.1f} KB)")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
