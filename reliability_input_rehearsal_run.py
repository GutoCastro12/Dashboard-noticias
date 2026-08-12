#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_rehearsal_run.py — 4I.2 R7c §20–§47.

O DRIVER: REHEARSAL AO VIVO, SUBCONJUNTO HISTÓRICO E REMEDIÇÃO DA AMOSTRA R7b-A.

Três execuções separadas porque respondem perguntas diferentes e não podem
compartilhar denominador:

  --feeds       o que os feeds próprios já entregam de graça (R0-EXTENDED)
  --historico   até ~30 URLs do corpus, para comparar métodos e tiers
  --live        rehearsal com teto duro de 40 fetches ÚNICOS por artigo
  --amostra     as 62 linhas da R7b-A remedidas com o input novo

Nada disso atualiza history, reclassifica ou persiste texto em produção. O
resultado é diagnóstico: quantos itens teriam input suficiente se a camada
fosse ligada — não quantos itens estão certos.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import link_debt_audit as lda
import risk_dashboard as rd
import reliability_input_capture as ic
import reliability_input_layer as il
import reliability_input_rehearsal as rh

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7c"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
SIDECAR = Path("risk_enrichment_shadow.json")


def _sidecar() -> dict:
    if SIDECAR.exists():
        try:
            return json.load(io.open(SIDECAR, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _novo_contador():
    return {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {}}


def _gravar(nome: str, dados) -> Path:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    dest = OUTDIR / nome
    ic._guardar(dest)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    io.open(tmp, "w", encoding="utf-8").write(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True,
                   default=str))
    os.replace(tmp, dest)          # §33 — escrita atômica
    return dest


# ── §11/§25/§35 feeds próprios ──────────────────────────────────────────────
def rodar_feeds(cfg: dict) -> dict:
    """Mede campo a campo o que cada feed entrega, e diagnostica os que falham.

    A versão anterior deste auditor não chamava `raise_for_status` e por isso
    classificou como PARSE_FAILED dois feeds que na verdade respondem HTTP
    404 — a produção sempre soube disso e loga 'Feed indisponível'. O status
    HTTP vem primeiro agora."""
    S = requests.Session()
    linhas = []
    por_metodo = collections.defaultdict(list)
    for spec in (cfg.get("custom_feeds") or []):
        reg = {"nome": spec.get("name", "?"), "url": spec.get("url", ""),
               "http": None, "content_type": "", "bytes": 0,
               "itens": 0, "falha": "", "detalhe": "",
               "metodos": {}, "recuperavel": False}
        try:
            r = S.get(spec["url"], timeout=25,
                      headers={"User-Agent": "Mozilla/5.0"})
            reg["http"] = r.status_code
            reg["content_type"] = r.headers.get("content-type", "")
            reg["bytes"] = len(r.content)
        except Exception as exc:
            reg["falha"] = rh.TIMEOUT if "Timeout" in type(exc).__name__ else rh.OTHER
            reg["detalhe"] = f"{type(exc).__name__}: {exc}"[:180]
            linhas.append(reg)
            continue
        if r.status_code >= 400:
            reg["falha"] = (rh.HTTP_404 if r.status_code == 404
                            else rh.HTTP_403 if r.status_code == 403
                            else rh.HTTP_429 if r.status_code == 429 else rh.OTHER)
            reg["detalhe"] = ("corpo vazio" if not r.content
                              else "corpo HTML de erro" if b"<html" in r.content[:400].lower()
                              else "corpo não-XML")
            # Recuperação seria fabricar dado: não há feed do outro lado.
            reg["recuperavel"] = False
            linhas.append(reg)
            continue
        try:
            root = ET.fromstring(r.content)
        except Exception as exc:
            reg["falha"] = rh.PARSE_FAILED
            reg["detalhe"] = str(exc)[:180]
            linhas.append(reg)
            continue
        met = collections.defaultdict(list)
        for it in root.iter("item"):
            reg["itens"] += 1
            for m, t in rh.campos_ricos(it).items():
                met[m].append(len(t))
        for it in root.iter(rh.NS_ATOM + "entry"):
            reg["itens"] += 1
            for m, t in rh.campos_ricos(it).items():
                met[m].append(len(t))
        reg["metodos"] = {m: {"n": len(v),
                              "mediana_chars": sorted(v)[len(v) // 2]}
                          for m, v in met.items()}
        for m, v in met.items():
            por_metodo[m].extend(v)
        reg["falha"] = rh.OK if reg["itens"] else rh.EMPTY
        linhas.append(reg)
        time.sleep(0.3)
    return {"feeds": linhas,
            "por_metodo_global": {m: {"n": len(v),
                                      "mediana_chars": sorted(v)[len(v) // 2]}
                                  for m, v in por_metodo.items()},
            "feeds_ok": sum(1 for f in linhas if f["falha"] == rh.OK),
            "feeds_falhos": sum(1 for f in linhas if f["falha"] != rh.OK)}


# ── §24 Google News ─────────────────────────────────────────────────────────
def rodar_gnews(hist: dict, *, limite: int, permitir_rede: bool) -> dict:
    urls = list((hist.get("articles") or {}))
    wrappers = [u for u in urls if lda.is_redirector(u)]
    diretos = [u for u in urls if not lda.is_redirector(u)]
    d = rh.diagnosticar_gnews(wrappers, permitir_rede=permitir_rede,
                              limite=limite)
    return {"urls_no_corpus": len(urls), "wrappers": len(wrappers),
            "diretos": len(diretos), "amostra_resolvida": d}


# ── varredura genérica ──────────────────────────────────────────────────────
def _processar(itens: list, *, permitir_rede: bool, politica: str,
               contador: dict, sidecar: dict) -> list:
    out = []
    for it in itens:
        out.append(rh.processar_artigo(
            url=it["url"], titulo=it.get("titulo") or "",
            resumo=it.get("resumo") or "", dominio=it.get("dominio") or "",
            pub_iso=it.get("pub_iso") or "", empresas=it.get("empresas") or {},
            ricos=it.get("ricos") or {}, rec=it.get("rec") or {},
            sidecar=sidecar, permitir_rede=permitir_rede, contador=contador,
            politica=politica, query_kind=it.get("query_kind", ""),
            fonte=it.get("fonte", "")))
    return out


def _do_historico(hist: dict, limite: int) -> list:
    itens = []
    for url, rec in list((hist.get("articles") or {}).items()):
        ebc = rec.get("events_by_company") or {}
        emps = {c: list(ebc.get(c) or []) for c in (rec.get("companies") or [])}
        for c, evs in ebc.items():
            emps.setdefault(c, list(evs or []))
        itens.append({"url": url, "titulo": rec.get("title") or "",
                      "resumo": rec.get("summary") or "",
                      "dominio": rec.get("domain") or "",
                      "pub_iso": rec.get("pub_iso") or "",
                      "empresas": emps, "rec": rec,
                      "query_kind": "company_query", "fonte": "history"})
    # diversidade: com e sem candidato, domínios distintos
    com = [i for i in itens if any(i["empresas"].values())]
    sem = [i for i in itens if not any(i["empresas"].values())]
    sel, doms = [], collections.Counter()
    for pool in (com, sem):
        for i in pool:
            d = i["dominio"]
            if doms[d] < 3 and len(sel) < limite:
                sel.append(i)
                doms[d] += 1
    return sel[:limite]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", action="store_true")
    ap.add_argument("--gnews", action="store_true")
    ap.add_argument("--historico", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--amostra", action="store_true")
    ap.add_argument("--politica", default="SELECTED")
    ap.add_argument("--limite-historico", type=int, default=30)
    ap.add_argument("--max-emissores", type=int, default=14)
    ap.add_argument("--confirmar-rede", action="store_true")
    a = ap.parse_args()
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    side = _sidecar()
    rede = bool(a.confirmar_rede)

    if a.feeds:
        r = rodar_feeds(cfg) if rede else {"erro": "rede não autorizada"}
        if rede:
            print("=" * 100)
            print("§11/§35 — CAMPOS RICOS POR FEED (ganho SEM requisição extra)")
            print("=" * 100)
            for f in r["feeds"]:
                ms = ", ".join(f"{m}={v['n']}@{v['mediana_chars']}"
                               for m, v in sorted(f["metodos"].items()))
                print(f"  {f['nome'][:26]:26s} http={str(f['http']):>4s} "
                      f"itens={f['itens']:4d} {f['falha']:14s} {ms[:70]}")
            print(f"\n  por método (global): {json.dumps(r['por_metodo_global'], ensure_ascii=False)}")
            print(f"  feeds ok {r['feeds_ok']} · falhos {r['feeds_falhos']}")
            print(f"  → {_gravar('feed_rich_fields.json', r)}")

    if a.gnews:
        r = rodar_gnews(hist, limite=25, permitir_rede=rede)
        print("=" * 100)
        print("§24 — DIAGNÓSTICO GOOGLE NEWS")
        print("=" * 100)
        print(f"  urls no corpus {r['urls_no_corpus']} · wrappers {r['wrappers']} "
              f"· diretos {r['diretos']}")
        print(f"  amostra: {r['amostra_resolvida']['telemetria']}")
        print(f"  → {_gravar('gnews_diagnosis.json', r)}")

    if a.historico:
        itens = _do_historico(hist, a.limite_historico)
        cont = _novo_contador()
        regs = _processar(itens, permitir_rede=rede, politica=a.politica,
                          contador=cont, sidecar=side)
        f = rh.funil(regs, cont)
        m = rh.marginal(regs)
        s = rh.sensibilidade(regs)
        print("=" * 100)
        print(f"§27/§37 — SUBCONJUNTO HISTÓRICO ({len(regs)} artigos, "
              f"política {a.politica})")
        print("=" * 100)
        for k, v in f.items():
            if k not in ("falhas", "tiers"):
                print(f"  {k:26s} {v}")
        print(f"  falhas: {f['falhas']}")
        print(f"  tiers : {f['tiers']}")
        print(f"\n  marginal: {json.dumps(m, ensure_ascii=False)}")
        print(f"\n  sensibilidade de política:")
        for p, v in s.items():
            print(f"    {p:14s} {v}")
        _gravar("historical_subset.json",
                {"funil": f, "marginal": m, "sensibilidade": s,
                 "registros": [{k: v for k, v in r.items() if k != "_best_input"}
                               for r in regs]})
        print(f"  → {OUTDIR / 'historical_subset.json'}")

    if a.live:
        if not rede:
            print("⚠️  --live exige --confirmar-rede")
            return 2
        cap = ic.capturar_pre_descarte(cfg, max_emissores=a.max_emissores,
                                       permitir_rede=True)
        itens, vistos = [], set()
        for art in cap["artigos"]:
            if art["url"] in vistos:
                continue
            vistos.add(art["url"])
            itens.append({"url": art["url"], "titulo": art["titulo"],
                          "resumo": art["texto_base"],
                          "dominio": art["dominio"], "pub_iso": art["pub_iso"],
                          "empresas": {e["empresa"]: e["candidatos"]
                                       for e in art["empresas"]},
                          "rec": {}, "query_kind": "company_query",
                          "fonte": "live_tap"})
        cont = _novo_contador()
        regs = _processar(itens, permitir_rede=True, politica=a.politica,
                          contador=cont, sidecar=side)
        f = rh.funil(regs, cont)
        print("=" * 100)
        print(f"§26 — LIVE REHEARSAL ({len(regs)} artigos únicos, "
              f"teto {rh.MAX_FETCH_ARTIGOS} fetches)")
        print("=" * 100)
        for k, v in f.items():
            if k not in ("falhas", "tiers"):
                print(f"  {k:26s} {v}")
        print(f"  falhas: {f['falhas']}")
        print(f"  tiers : {f['tiers']}")
        print(f"\n  cobertura por source_kind:")
        for k, v in rh.cobertura(regs, lambda r: r["source_kind"]).items():
            print(f"    {k:20s} N={v['N']:3d} final_ready={v['final_ready']:3d} "
                  f"blocked={v['blocked']:3d}")
        print(f"\n  candidato vs sem candidato:")
        for k, v in rh.cobertura(regs, lambda r: "com_candidato"
                                 if r["tem_algum_candidato"] else "sem_candidato").items():
            print(f"    {k:20s} N={v['N']:3d} final_ready={v['final_ready']:3d} "
                  f"falhas={v['falhas']}")
        _gravar("live_rehearsal.json",
                {"funil": f, "telemetria_captura": cap["telemetria"],
                 "cobertura_source": rh.cobertura(regs, lambda r: r["source_kind"]),
                 "cobertura_dominio": rh.cobertura(regs, lambda r: r["dominio"]),
                 "registros": [{k: v for k, v in r.items() if k != "_best_input"}
                               for r in regs]})
        print(f"  → {OUTDIR / 'live_rehearsal.json'}")

    if a.amostra:
        man = json.load(io.open(OUTDIR.parent / "r7b_a" / "sample_manifest.json",
                                encoding="utf-8"))
        tapf = OUTDIR.parent / "r7b_a" / "tap_pre_filtro.json"
        tap = {}
        if tapf.exists():
            for it in (json.load(io.open(tapf, encoding="utf-8")).get("itens") or []):
                tap[(it.get("url"), it.get("empresa"))] = it
        porart = {}
        for it in man["itens"]:
            rec = (hist.get("articles") or {}).get(it["url"]) or {}
            tr = tap.get((it["url"], it.get("empresa"))) or {}
            d = porart.setdefault(it["url"], {
                "url": it["url"],
                "titulo": rec.get("title") or tr.get("titulo") or "",
                "resumo": rec.get("summary") or tr.get("resumo") or "",
                "dominio": rec.get("domain") or tr.get("dominio") or "",
                "pub_iso": rec.get("pub_iso") or tr.get("pub_iso") or "",
                "empresas": {}, "rec": rec, "estratos": set(),
                "query_kind": "company_query", "fonte": "r7b_a"})
            if it.get("empresa"):
                d["empresas"].setdefault(it["empresa"],
                                         [it["evento"]] if it.get("evento") else [])
            d["estratos"].add(it["estrato"])
        itens = [{**v, "estratos": sorted(v["estratos"])} for v in porart.values()]
        cont = _novo_contador()
        regs = _processar(itens, permitir_rede=rede, politica=a.politica,
                          contador=cont, sidecar=side)
        for r, it in zip(regs, itens):
            r["estratos"] = it["estratos"]
        f = rh.funil(regs, cont)
        por_estrato = collections.defaultdict(lambda: {"N": 0, "ready": 0,
                                                       "blocked": 0})
        for r in regs:
            for e in r["estratos"]:
                por_estrato[e]["N"] += 1
                por_estrato[e]["ready"] += 1 if r["final"]["input_ready_under_r7c_policy"] else 0
                por_estrato[e]["blocked"] += 1 if r["falha"] in (
                    rh.ROBOTS_BLOCKED, rh.HTTP_403, rh.HTTP_429, rh.PAYWALL) else 0
        print("=" * 100)
        print(f"§38/§39 — AMOSTRA R7b-A REMEDIDA ({len(regs)} artigos únicos, "
              f"política {a.politica})")
        print("=" * 100)
        for k in ("r0_legacy_ready", "r0_extended_ready", "r1_attempted",
                  "r1_ready", "r2_ready", "final_ready", "final_insufficient",
                  "network_fetches", "duplicatas_evitadas", "reuso_sidecar"):
            print(f"  {k:26s} {f[k]}")
        print(f"  falhas: {f['falhas']}")
        print(f"\n  prontidão por estrato R7b-A:")
        for e in sorted(por_estrato):
            v = por_estrato[e]
            print(f"    {e}  N={v['N']:3d}  input_ready={v['ready']:3d}  "
                  f"blocked={v['blocked']:3d}")
        _gravar("r7b_sample_reinput.json",
                {"funil": f, "por_estrato": dict(por_estrato),
                 "registros": [{k: v for k, v in r.items() if k != "_best_input"}
                               for r in regs]})
        print(f"  → {OUTDIR / 'r7b_sample_reinput.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
