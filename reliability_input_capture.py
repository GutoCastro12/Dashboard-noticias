#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_capture.py — 4I.2 R7c.

DUAS FONTES DE TEXTO QUE O PIPELINE HOJE JOGA FORA.

1. `content:encoded` DOS FEEDS PRÓPRIOS. `_parse_rss` lê `title`, `link`,
   `pubDate`, `description` e `source`. Para o Google News isso é tudo o que
   existe — a `description` é a manchete repetida. Mas o MESMO parser atende os
   custom feeds, que são RSS de publicadores reais. Medido nesta wave: dos 240
   itens dos 11 feeds configurados, 98 trazem `content:encoded` com mediana de
   3474 caracteres, contra 117 da `description`. Trinta vezes mais texto, já
   baixado, sem requisição adicional e sem esbarrar em robots — descartado no
   parse.

2. ARTIGOS ATRIBUÍDOS SEM CANDIDATO. `build_feed` descarta o artigo quando
   nenhum evento da taxonomia se aplica ao emissor. O tap da R7b-A mediu 104 de
   134 artigos atribuídos (78%) morrendo aí. Enquanto esse fluxo não for
   observável, qualquer promessa de descoberta aberta é retórica: o sistema só
   pode encontrar o que a taxonomia já sabe nomear.

Este módulo OBSERVA as duas. Não altera `_parse_rss`, não altera `build_feed`,
não escreve em produção. O parser aqui é uma SEGUNDA implementação, deliberada:
tocar o de produção mudaria o texto que alimenta classificação e atribuição, e
esta wave tem contrato de equivalência.

DESLIGADO POR PADRÃO (`reliability_input_layer.ATIVO`). Usa rede apenas quando
explicitamente autorizado.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import risk_dashboard as rd
import reliability_input_layer as il
import reliability_pilot_input as pi

CAPTURE_VERSION = "r7c.capture1"
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7c"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))

NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"

PROIBIDO_ESCREVER = ("risk_history.json", "config_risco.yaml",
                     "risk_enrichment_shadow.json", "index.html",
                     "dashboard_risco.html", "run_meta.json",
                     "international_search_history.json")


def _guardar(destino: Path) -> None:
    d = destino.resolve()
    if d.name in PROIBIDO_ESCREVER:
        raise PermissionError(f"a camada de input não escreve em {d.name}")
    if OUTDIR.resolve() not in d.parents:
        raise PermissionError(f"só se escreve sob {OUTDIR}: {d}")


def _texto_limpo(html: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "",
               flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
         .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", t).strip()


def extrair_do_item(item) -> dict:
    """Lê o item de RSS incluindo `content:encoded`.

    Aceita o namespace canônico e também a variante sem namespace declarado —
    feeds reais erram isso com frequência, e cair fora por causa de um prefixo
    perderia justamente o texto que se quer."""
    desc = _texto_limpo(item.findtext("description") or "")
    corpo = item.findtext(NS_CONTENT) or ""
    if not corpo:
        for ch in item:
            if ch.tag.endswith("encoded") and ch.text:
                corpo = ch.text
                break
    corpo = _texto_limpo(corpo)
    link = (item.findtext("link") or "").strip()
    return {"titulo": (item.findtext("title") or "").strip(),
            "url": link, "description": desc, "content_encoded": corpo,
            "melhor": corpo if len(corpo) > len(desc) else desc,
            "ganho_chars": max(0, len(corpo) - len(desc))}


def auditar_feeds(cfg: dict, *, limite_feeds: int | None = None,
                  permitir_rede: bool = False) -> dict:
    """Quanto texto os feeds próprios oferecem e o parser de produção ignora."""
    if not permitir_rede:
        return {"erro": "rede não autorizada", "feeds": []}
    feeds = (cfg.get("custom_feeds") or [])[:limite_feeds or None]
    S = requests.Session()
    linhas, tot, com_ce = [], 0, 0
    ganhos = []
    for spec in feeds:
        reg = {"nome": spec.get("name", "?"), "itens": 0, "com_content": 0,
               "mediana_desc": 0, "mediana_content": 0, "erro": ""}
        try:
            r = S.get(spec["url"], timeout=20,
                      headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.text)
        except Exception as exc:
            reg["erro"] = type(exc).__name__
            linhas.append(reg)
            continue
        ds, cs = [], []
        for it in root.iter("item"):
            e = extrair_do_item(it)
            reg["itens"] += 1
            tot += 1
            ds.append(len(e["description"]))
            if e["ganho_chars"] > 0:
                reg["com_content"] += 1
                com_ce += 1
                cs.append(len(e["content_encoded"]))
                ganhos.append(e["ganho_chars"])
        reg["mediana_desc"] = sorted(ds)[len(ds) // 2] if ds else 0
        reg["mediana_content"] = sorted(cs)[len(cs) // 2] if cs else 0
        linhas.append(reg)
        time.sleep(0.3)
    return {"capture_version": CAPTURE_VERSION, "feeds": linhas,
            "itens_totais": tot, "itens_com_content_encoded": com_ce,
            "ganho_mediano_chars": (sorted(ganhos)[len(ganhos) // 2]
                                    if ganhos else 0)}


def capturar_pre_descarte(cfg: dict, *, max_emissores: int = 12,
                          permitir_rede: bool = False,
                          pausa: float = 1.0) -> dict:
    """Reexecuta coleta e atribuição reais e separa o que morre no filtro.

    Diferente do tap da R7b-A, que era escopado ao experimento, aqui a unidade
    é o ARTIGO e o registro produzido é o mesmo `ArticleInput` da camada — de
    modo que artigo com candidato e artigo sem candidato tenham exatamente a
    mesma forma. Era essa simetria que faltava: hoje um deles é um registro de
    produção e o outro não existe."""
    if not permitir_rede:
        return {"erro": "rede não autorizada", "itens": []}
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    conhecidos = set(hist.get("articles") or {})
    watch = cfg.get("watchlist", [])
    tax = cfg.get("taxonomy", [])
    S = requests.Session()

    tel = collections.Counter()
    vistos = set()
    artigos = []
    for comp in watch[:max_emissores]:
        try:
            queries = rd.build_company_queries(comp, cfg)
        except Exception:
            queries = [comp.get("name") or ""]
        for q in (queries or [])[:1]:
            try:
                arts = rd.fetch_query(q, cfg, S)
            except Exception as exc:
                tel[f"erro_query:{type(exc).__name__}"] += 1
                continue
            tel["bruto"] += len(arts or [])
            for art in (arts or []):
                url = art.get("url") or ""
                if not url or url in vistos:
                    continue
                vistos.add(url)
                if url in conhecidos:
                    tel["ja_no_history"] += 1
                    continue
                try:
                    comps = rd.detect_companies(art, watch)
                except Exception:
                    comps = []
                if not comps:
                    tel["sem_empresa"] += 1
                    continue
                tel["atribuido"] += 1
                try:
                    evs = [e.get("id") for e in (rd.classify_article(art, tax) or [])]
                except Exception:
                    evs = []
                tel["com_candidato" if evs else "sem_candidato"] += 1
                ai = il.montar_article_input(
                    url=url, titulo=art.get("title") or "",
                    resumo=art.get("summary") or "",
                    dominio=art.get("domain") or "",
                    pub_iso=art.get("pub_iso") or "",
                    empresas={c: list(evs) for c in comps})
                ai["descartado_pelo_filtro"] = not evs
                artigos.append(ai)
            time.sleep(pausa)

    sem = [a for a in artigos if a["descartado_pelo_filtro"]]
    return {"capture_version": CAPTURE_VERSION,
            "emissores_consultados": min(max_emissores, len(watch)),
            "telemetria": dict(tel),
            "artigos": artigos,
            "descartados": len(sem),
            "taxa_descarte": round(len(sem) / max(1, len(artigos)), 3),
            "itens": [{"url": a["url"], "empresa": e["empresa"], "evento": "",
                       "titulo": a["titulo"], "resumo": "",
                       "dominio": a["dominio"], "pub_iso": a["pub_iso"],
                       "origem": "captura_pre_descarte"}
                      for a in sem for e in a["empresas"]]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auditar-feeds", action="store_true")
    ap.add_argument("--capturar", action="store_true")
    ap.add_argument("--max-emissores", type=int, default=12)
    ap.add_argument("--confirmar-rede", action="store_true")
    a = ap.parse_args()
    if not a.confirmar_rede:
        print("⚠️  esta ferramenta usa rede. Rode com --confirmar-rede.")
        return 2
    cfg = rd.load_config("config_risco.yaml")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    if a.auditar_feeds:
        r = auditar_feeds(cfg, permitir_rede=True)
        dest = OUTDIR / "feed_content_audit.json"
        _guardar(dest)
        io.open(dest, "w", encoding="utf-8").write(
            json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True))
        print("=" * 92)
        print("AUDITORIA DE content:encoded NOS FEEDS PRÓPRIOS")
        print("=" * 92)
        for f in r["feeds"]:
            print(f"  {f['nome'][:30]:30s} itens {f['itens']:4d} · "
                  f"com corpo {f['com_content']:4d} · "
                  f"desc {f['mediana_desc']:5d} → corpo {f['mediana_content']:6d}"
                  + (f"  ERRO {f['erro']}" if f["erro"] else ""))
        print(f"\n  itens totais {r['itens_totais']} · "
              f"com content:encoded {r['itens_com_content_encoded']} · "
              f"ganho mediano {r['ganho_mediano_chars']} chars")
        print(f"  → {dest}")

    if a.capturar:
        r = capturar_pre_descarte(cfg, max_emissores=a.max_emissores,
                                  permitir_rede=True)
        dest = OUTDIR / "pre_discard_capture.json"
        _guardar(dest)
        io.open(dest, "w", encoding="utf-8").write(
            json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True,
                       default=str))
        t = r["telemetria"]
        print("=" * 92)
        print("CAPTURA PRÉ-DESCARTE (unidade = artigo)")
        print("=" * 92)
        print(f"  emissores            : {r['emissores_consultados']}")
        print(f"  bruto                : {t.get('bruto', 0)}")
        print(f"  já no history        : {t.get('ja_no_history', 0)}")
        print(f"  sem empresa          : {t.get('sem_empresa', 0)}")
        print(f"  atribuídos           : {t.get('atribuido', 0)}")
        print(f"    com candidato      : {t.get('com_candidato', 0)}")
        print(f"    SEM candidato      : {t.get('sem_candidato', 0)}")
        print(f"  descartados pelo filtro: {r['descartados']} "
              f"({r['taxa_descarte']*100:.1f}% dos atribuídos)")
        print(f"  → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
