#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_tap.py — 4I.2 R7b-A.

O QUE O PIPELINE JOGA FORA ANTES DE OLHAR.

`build_feed` descarta o artigo quando nenhum evento da taxonomia se aplica ao
emissor ("nenhum evento se aplica à natureza do(s) emissor(es) → a notícia não
é um sinal classificado para eles"). Consequência: o history só tem 4 artigos
sem candidato, e nenhum deles é o caso que interessa. Sem ver esse fluxo, a
promessa de descoberta aberta é retórica — o modelo só receberia artigos que a
taxonomia já reconheceu.

Este tap reexecuta a MESMA coleta e a MESMA atribuição de produção, e captura
os artigos que morrem no filtro. Nada mais.

FRONTEIRAS, verificadas por teste:
  - não escreve `risk_history.json`, `config_risco.yaml`, workflow ou sidecar;
  - grava só sob `out_reliability/`;
  - deduplica contra o history atual (não recaptura o que já é conhecido);
  - limite explícito de emissores por execução — não é um crawler.

USA REDE. Executar sob autorização explícita (§20 do brief da R7b-A).
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import time
from pathlib import Path

import requests

import risk_dashboard as rd

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7b_a"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
TAP_VERSION = "r7ba.tap1"

PROIBIDO_ESCREVER = ("risk_history.json", "config_risco.yaml",
                     "risk_enrichment_shadow.json", "index.html",
                     "dashboard_risco.html", "run_meta.json")


def _guardar(destino: Path) -> None:
    """Fail-closed: qualquer caminho que não esteja sob OUTDIR é recusado
    antes de abrir o arquivo. É barato e elimina a classe inteira de erro
    'experimento escreveu em produção'."""
    d = destino.resolve()
    if d.name in PROIBIDO_ESCREVER:
        raise PermissionError(f"o tap não escreve em {d.name}")
    if OUTDIR.resolve() not in d.parents:
        raise PermissionError(f"o tap só escreve sob {OUTDIR}: {d}")


def coletar(cfg: dict, *, max_emissores: int = 12,
            pausa: float = 1.0) -> dict:
    """Roda a coleta real para um punhado de emissores e separa em três
    baldes: com candidato, SEM candidato (o alvo), e sem empresa atribuída."""
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    conhecidos = set((hist.get("articles") or {}))
    watch = cfg.get("watchlist", [])
    tax = cfg.get("taxonomy", [])
    session = requests.Session()

    tel = collections.Counter()
    com_candidato, sem_candidato = [], []
    vistos = set()

    alvo = watch[:max_emissores]
    for comp in alvo:
        try:
            queries = rd.build_company_queries(comp, cfg)
        except Exception:
            queries = [comp.get("name") or ""]
        for q in (queries or [])[:1]:
            try:
                arts = rd.fetch_query(q, cfg, session)
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
                    evs = rd.classify_article(art, tax)
                except Exception:
                    evs = []
                item = {"url": url, "titulo": art.get("title") or "",
                        "resumo": art.get("summary") or "",
                        "dominio": art.get("domain") or "",
                        "pub_iso": art.get("pub_iso") or "",
                        "empresas": comps,
                        "event_ids": [e.get("id") for e in (evs or [])]}
                if evs:
                    tel["com_candidato"] += 1
                    com_candidato.append(item)
                else:
                    tel["sem_candidato"] += 1
                    sem_candidato.append(item)
            time.sleep(pausa)

    itens = []
    for it in sem_candidato:
        for emp in it["empresas"]:
            itens.append({"url": it["url"], "empresa": emp, "evento": "",
                          "titulo": it["titulo"], "resumo": it["resumo"],
                          "dominio": it["dominio"], "pub_iso": it["pub_iso"],
                          "origem": "tap_pre_filtro"})
    return {"tap_version": TAP_VERSION,
            "emissores_consultados": len(alvo),
            "telemetria": dict(tel),
            "sem_candidato": sem_candidato,
            "com_candidato_amostra": com_candidato[:20],
            "itens": itens}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-emissores", type=int, default=12)
    ap.add_argument("--out", default=str(OUTDIR / "tap_pre_filtro.json"))
    ap.add_argument("--confirmar-rede", action="store_true",
                    help="obrigatório: o tap usa rede real")
    a = ap.parse_args()
    if not a.confirmar_rede:
        print("⚠️  o tap usa rede. Rode com --confirmar-rede.")
        return 2
    cfg = rd.load_config("config_risco.yaml")
    r = coletar(cfg, max_emissores=a.max_emissores)
    dest = Path(a.out)
    _guardar(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    io.open(dest, "w", encoding="utf-8").write(
        json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True))
    t = r["telemetria"]
    print(f"  emissores           : {r['emissores_consultados']}")
    print(f"  bruto               : {t.get('bruto', 0)}")
    print(f"  já no history       : {t.get('ja_no_history', 0)}")
    print(f"  sem empresa         : {t.get('sem_empresa', 0)}")
    print(f"  atribuídos          : {t.get('atribuido', 0)}")
    print(f"    com candidato     : {t.get('com_candidato', 0)}")
    print(f"    SEM candidato     : {t.get('sem_candidato', 0)}  ← alvo OW-3")
    print(f"  pares p/ amostra    : {len(r['itens'])}")
    print(f"  → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
