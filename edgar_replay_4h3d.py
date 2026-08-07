#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_replay_4h3d.py — replay OFFLINE dos 211 filings do run 31144073562.

Reclassifica exatamente os mesmos documentos, com e sem o normalizador
source-aware, para comparação apples-to-apples (4H.3D §10/§11). Não toca a
rede, não toca produção, não pontua.

Corpus: diretório com os corpos já recuperados + `index.json` (o mesmo
conteúdo dos 2.433.518 chars do run original).

Saídas:
    edgar_boilerplate_year_audit.csv
    edgar_historical_reference_before_after.csv
    edgar_replay_4h3d_resumo.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import edgar_canonical as ec
import edgar_normalizer as en

CORPUS_PADRAO = r"C:\Users\Gustavo\DashRisk-corpus-4h3c"
_ANO_ANTIGO = re.compile(r"ano_antigo_citado:(\d{4})")


def _cut(s, n=220):
    return re.sub(r"\s+", " ", str(s or ""))[:n]


def _write_csv(path: Path, rows: list[dict], keys: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _filing_de(rec: dict) -> dict:
    """Reconstrói o filing canônico a partir da linha do artifact."""
    acc = rec.get("accession", "")
    return {
        "company": rec.get("emissor", ""), "cik": rec.get("cik", ""),
        "ticker": rec.get("ticker", ""), "form": rec.get("form", ""),
        "accession_number": acc,
        "accession_digits": ec.normalize_accession(acc),
        "filing_date": rec.get("filing_date", ""),
        "report_date": rec.get("report_date", ""),
        "primary_document": rec.get("primary_document", ""),
        "description": rec.get("description", ""),
        "items": [i for i in str(rec.get("items", "")).split(",") if i.strip()],
        "url": rec.get("url", ""), "provenance": "EDGAR",
    }


def _semantica(cfg, rd, art, empresa, event_id):
    """Veredito do semantic_audit — o mesmo caminho do orquestrador."""
    import semantic_audit as sa
    from datetime import datetime, timezone
    aliases = {c["name"]: (c.get("aliases") or [c["name"]])
               for c in cfg.get("watchlist", [])}
    r = sa.resolve_article_semantics(art.get("title", ""), art.get("summary", ""),
                                     empresa, [event_id], aliases,
                                     article_year=datetime.now(timezone.utc).year)
    return next((x for x in r.get("decisoes", []) if x.get("event_id") == event_id), {})


def replay(corpus_dir: str, outdir: str, config: str) -> dict:
    import risk_dashboard as rd
    cfg = rd.load_config(config)
    corpus = Path(corpus_dir)
    idx = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    year_rows, ba_rows = [], []
    antes_c = Counter()
    depois_c = Counter()
    tot = {"filings": 0, "com_corpo": 0, "chars": 0, "chars_neutralizados": 0}
    ev_antes, ev_depois = [], []

    for rec in idx:
        arq = rec.get("corpus_file")
        if not arq:
            continue
        raw = (corpus / arq).read_text(encoding="utf-8")
        filing = _filing_de(rec)
        tot["filings"] += 1
        tot["com_corpo"] += 1 if raw else 0
        tot["chars"] += len(raw)

        norm = en.normalize_edgar_semantic_text(raw, provenance="EDGAR")
        sem = norm["semantic_text"]
        tot["chars_neutralizados"] += norm["stats"].get("chars_neutralizados", 0)
        assert raw == (corpus / arq).read_text(encoding="utf-8"), "bruto alterado!"

        # inventário de anos (amostra por documento, para não explodir o CSV)
        vistos = set()
        for y in en.year_inventory(raw, sem):
            chave = (y["ano"], y["classificacao"])
            if chave in vistos:
                continue
            vistos.add(chave)
            year_rows.append({"emissor": filing["company"],
                              "accession": filing["accession_number"],
                              "form": filing["form"], **y})

        # ── ANTES: reprodução FIEL do run 31144073562 ──
        # Não usar `to_article` aqui: ele já é a versão 4H.3D (janela local).
        # O comportamento original era `summary = corpo[:4000]` — os primeiros
        # 4 mil chars, isto é, a CAPA do filing, onde mora todo o boilerplate
        # "Securities Act of 1933/1934". Era exatamente isso que neutralizava.
        an_a = ec.analyze_filing(filing, raw)
        art_a = {"title": ec.canonical_title(filing), "summary": raw[:4000]}
        # ── DEPOIS: com normalizador ──
        an_d = ec.analyze_filing(filing, raw, sem)
        art_d = ec.to_article(filing, raw, an_d, semantic_text=sem)

        for ev in an_a["event_ids"]:
            d = _semantica(cfg, rd, art_a, filing["company"], ev)
            motivo = str(d.get("rejection_reason") or "")
            antes_c[_classe(d, motivo)] += 1
            ev_antes.append({"emissor": filing["company"], "evento": ev,
                             "scoreable": bool(d.get("scoreable")), "motivo": motivo})

        for ev in an_d["event_ids"]:
            d = _semantica(cfg, rd, art_d, filing["company"], ev)
            motivo = str(d.get("rejection_reason") or "")
            depois_c[_classe(d, motivo)] += 1
            cand = next((c for c in an_d["aceitos"] if c["event_id"] == ev), {})
            ev_depois.append({"emissor": filing["company"], "evento": ev,
                              "scoreable": bool(d.get("scoreable")), "motivo": motivo,
                              "form": filing["form"], "item": cand.get("item", ""),
                              "nao_pontuavel_por_forma": bool(
                                  cand.get("nao_pontuavel_por_forma"))})

        # ── before/after dos casos de referência histórica ──
        for ev in sorted(set(an_a["event_ids"]) | set(an_d["event_ids"])):
            da = _semantica(cfg, rd, art_a, filing["company"], ev) if ev in an_a["event_ids"] else {}
            dd = _semantica(cfg, rd, art_d, filing["company"], ev) if ev in an_d["event_ids"] else {}
            m_a = str(da.get("rejection_reason") or "")
            m_d = str(dd.get("rejection_reason") or "")
            ano = _ANO_ANTIGO.search(m_a)
            if not ano:
                continue
            ca = next((c for c in an_a["aceitos"] if c["event_id"] == ev), {})
            cd = next((c for c in an_d["aceitos"] if c["event_id"] == ev), {})
            gat = _gatilho(raw, ano.group(1))
            ba_rows.append({
                "emissor": filing["company"], "accession": filing["accession_number"],
                "form": filing["form"], "item": cd.get("item", ca.get("item", "")),
                "evento": ev,
                "ano_detectado": ano.group(1),
                "trecho_gatilho": _cut(gat, 200),
                "classificacao_antes": _classe(da, m_a),
                "motivo_antes": _cut(m_a, 160),
                "trecho_apos_normalizacao": _cut(cd.get("evidence_text"), 220),
                "secao": cd.get("evidence_section", ""),
                "classificacao_depois": _classe(dd, m_d) if dd else "removido",
                "motivo_depois": _cut(m_d, 160) or "—",
                "boilerplate_removido": _boiler_do_ano(norm, raw, ano.group(1)),
                "razao": ("boilerplate jurídico neutralizado antes da classificação"
                          if ano.group(1) not in _cut(cd.get("evidence_text"), 400)
                          else "ano permanece na evidência local"),
            })

    _write_csv(out / "edgar_boilerplate_year_audit.csv", year_rows, [
        "emissor", "accession", "form", "ano", "contexto", "secao",
        "classificacao", "acao"])
    _write_csv(out / "edgar_historical_reference_before_after.csv", ba_rows, [
        "emissor", "accession", "form", "item", "evento", "ano_detectado",
        "trecho_gatilho", "classificacao_antes", "motivo_antes",
        "trecho_apos_normalizacao", "secao", "classificacao_depois",
        "motivo_depois", "boilerplate_removido", "razao"])

    resumo = {
        "corpus": str(corpus), **tot,
        "pct_neutralizado": round(100.0 * tot["chars_neutralizados"] / max(1, tot["chars"]), 1),
        "eventos_antes": len(ev_antes),
        "eventos_depois": len(ev_depois),
        "classificacao_antes": dict(antes_c),
        "classificacao_depois": dict(depois_c),
        "neutralizados_por_ano_antes": antes_c.get("referencia_historica", 0),
        "neutralizados_por_ano_depois": depois_c.get("referencia_historica", 0),
        "pontuaveis_antes": sum(1 for e in ev_antes if e["scoreable"]),
        "pontuaveis_depois": sum(1 for e in ev_depois if e["scoreable"]),
        "nao_pontuavel_por_forma": sum(1 for e in ev_depois
                                       if e["nao_pontuavel_por_forma"]),
        "casos_before_after": len(ba_rows),
        "anos_por_classe": dict(Counter(y["classificacao"] for y in year_rows)),
    }
    (out / "edgar_replay_4h3d_resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumo


def _classe(d: dict, motivo: str) -> str:
    if not d:
        return "ausente"
    if "ano_antigo_citado" in motivo:
        return "referencia_historica"
    if d.get("scoreable"):
        return "pontuavel"
    return "informativo"


def _gatilho(raw: str, ano: str) -> str:
    m = re.search(rf"\b{ano}\b", raw)
    return raw[max(0, m.start() - 90):m.end() + 50] if m else ""


def _boiler_do_ano(norm: dict, raw: str, ano: str) -> str:
    for r in norm.get("removed", []):
        if ano in r.get("amostra", ""):
            return f"{r['padrao']} ({r['classe']})"
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="4H.3D — replay offline dos 211 filings")
    ap.add_argument("--corpus", default=CORPUS_PADRAO)
    ap.add_argument("--outdir", default="out_4h3d")
    ap.add_argument("--config", default="config_risco.yaml")
    a = ap.parse_args(argv)
    r = replay(a.corpus, a.outdir, a.config)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
