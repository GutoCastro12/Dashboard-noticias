#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_shadow_4h3c.py — 4H.3C: ciclo REAL do EDGAR em modo SOMBRA, com o parser
canônico e evidência textual do documento.

Responde à pergunta da fase — "com conteúdo real, a classificação é precisa o
bastante para o EDGAR servir de fonte oficial de CORROBORAÇÃO?" — e produz a
auditoria de qualidade que sustenta a resposta.

NÃO pontua, NÃO persiste, NÃO publica. `edgar_scoring_enabled` continua false
e é verificado como pré-condição dura: o módulo se recusa a rodar com scoring
ligado.

Artifacts (§9 do pedido):
    edgar_4h3c_por_emissor.csv
    edgar_4h3c_filings.csv
    edgar_4h3c_event_candidates.csv
    edgar_4h3c_classification.csv
    edgar_4h3c_false_positive_review.csv
    edgar_4h3c_dedup.csv
    relatorio_edgar_4h3c.md
    edgar_4h3c_run_meta.json
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import edgar_canonical as ec

ARTIFACTS = [
    "edgar_4h3c_por_emissor.csv",
    "edgar_4h3c_filings.csv",
    "edgar_4h3c_event_candidates.csv",
    "edgar_4h3c_classification.csv",
    "edgar_4h3c_false_positive_review.csv",
    "edgar_4h3c_dedup.csv",
    "relatorio_edgar_4h3c.md",
    "edgar_4h3c_run_meta.json",
]

# Arquivos de produção cuja invariância é PROVADA por hash.
WATCH_DEFAULT = ["risk_history.json", "config_risco.yaml", "index.html",
                 "dashboard_risco.html", "run_meta.json",
                 "international_search_history.json"]

# Teto de corpo baixado por filing. A SEC pede uso comedido; e 60k chars já
# cobrem com folga a seção narrativa de um 8-K.
MAX_BODY_CHARS = 60000
MAX_BODIES_PER_COMPANY = 25


def _sha(p) -> str:
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "AUSENTE"


def _write_csv(path: Path, rows: list[dict], keys: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _cut(s, n=300) -> str:
    s = str(s or "").replace("\n", " ").replace("\r", " ")
    return s[:n]


def run_shadow_4h3c(rd, cfg: dict, *, outdir: str = "out_4h3c",
                    fetcher=None, submissions_fetcher=None,
                    watch_files: list[str] | None = None,
                    history: dict | None = None,
                    max_companies: int | None = None) -> dict:
    """Executa o ciclo de sombra 4H.3C.

    `submissions_fetcher(cik10) -> dict` e `fetcher(url) -> str` são injetáveis
    para teste. Em produção usam a sessão HTTP real com o User-Agent da SEC.
    """
    if rd.edgar_scoring_enabled(cfg):
        raise RuntimeError("4H.3C recusa rodar com edgar_scoring_enabled=true: "
                           "esta fase mede qualidade, não ativa score.")

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    watch = watch_files if watch_files is not None else WATCH_DEFAULT
    hashes_antes = {f: _sha(f) for f in watch}

    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    forms_default = rd._normalize_edgar_forms(src.get("formularios_gatilho"))
    rps = max(1, int(src.get("rate_limit_rps", 8)))
    janela = int((cfg.get("evolution") or {}).get("window_days", 90))
    cutoff = int(datetime.now(timezone.utc).timestamp()) - janela * 86400

    watchlist = cfg.get("watchlist", [])
    alvos = [c for c in watchlist if rd.edgar_eligible(c)]
    if max_companies:
        alvos = alvos[:max_companies]

    # ── rede real (ou injetada) ──
    session = None
    cikmap = {}
    if submissions_fetcher is None or fetcher is None:
        import requests
        session = requests.Session()
        cikmap = rd._load_cik_map(session) or {}

    def _subs(cik10):
        if submissions_fetcher is not None:
            return submissions_fetcher(cik10)
        r = session.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                        headers=rd._edgar_headers(), timeout=25)
        r.raise_for_status()
        return r.json()

    def _body(url):
        if fetcher is not None:
            return fetcher(url)
        # headers dos ARCHIVES — nunca os de data.sec.gov (ver archive_headers)
        r = session.get(url, headers=ec.archive_headers(rd._EDGAR_UA), timeout=30)
        r.raise_for_status()
        return r.text

    por_emissor, filings_rows, cand_rows = [], [], []
    class_rows, fp_rows, dedup_rows = [], [], []
    todos_filings: list[dict] = []

    # ocorrências já conhecidas por OUTRAS fontes (Google News / RI / CVM)
    existentes = _occurrences_from_history(history or {}, rd, cutoff_ts=cutoff)

    for c in alvos:
        nome = c.get("name", "")
        t0 = time.time()
        linha = {
            "emissor": nome, "pais": c.get("country", ""), "ticker": c.get("ticker", ""),
            "elegivel": True, "cik": "", "http": "", "erro": "",
            "filings_encontrados": 0, "filings_aceitos": 0, "forms": "", "items": "",
            "candidatos_evento": 0, "eventos_classificados": 0,
            "eventos_diretos": 0, "eventos_contextuais": 0, "eventos_informativos": 0,
            "rejeitados": 0, "motivo_rejeicao": "", "corpos_baixados": 0,
            "erro_corpo": "", "elapsed_ms": 0,
        }
        cik10 = c.get("cik") or cikmap.get(str(c.get("ticker") or "").upper())
        if not cik10:
            linha.update(erro="cik_nao_resolvido", http="",
                         motivo_rejeicao=f"ticker={c.get('ticker') or '—'} sem CIK",
                         elapsed_ms=int((time.time() - t0) * 1000))
            por_emissor.append(linha)
            continue
        linha["cik"] = cik10

        try:
            data = _subs(cik10)
            linha["http"] = "200"
        except Exception as exc:
            linha.update(http=getattr(getattr(exc, "response", None), "status_code", "") or "erro",
                         erro=type(exc).__name__,
                         motivo_rejeicao=_cut(exc, 160),
                         elapsed_ms=int((time.time() - t0) * 1000))
            por_emissor.append(linha)
            time.sleep(1.0 / rps)
            continue

        forms_c = rd.edgar_forms_for(c, forms_default)
        fs = ec.parse_submissions(data, company=nome, cik10=str(cik10),
                                  forms=forms_c, cutoff_ts=cutoff,
                                  ticker=str(c.get("ticker") or ""))
        linha["filings_encontrados"] = len(fs)

        unicos, dups = ec.dedup_filings(fs)
        for d in dups:
            dedup_rows.append({
                "emissor": nome, "tipo": "documento_repetido",
                "accession": d.get("accession_number", ""),
                "duplicate_of": d.get("duplicate_of", ""),
                "form": d.get("form", ""), "event_id": "",
                "acao": "descartado", "cria_ocorrencia": False,
                "motivo": "mesmo accession já processado",
            })
        linha["filings_aceitos"] = len(unicos)
        linha["forms"] = ",".join(sorted({f["form"] for f in unicos}))
        linha["items"] = ",".join(sorted({i for f in unicos for i in f["items"]}))
        todos_filings.extend(unicos)

        corpos = 0
        for f in unicos:
            texto, erros_corpo = "", []
            if corpos < MAX_BODIES_PER_COMPANY and f.get("url"):
                texto = ec.fetch_document_text(f["url"], _body,
                                               max_chars=MAX_BODY_CHARS,
                                               errors=erros_corpo)
                if texto:
                    corpos += 1
                elif not linha["erro_corpo"]:
                    linha["erro_corpo"] = _cut(erros_corpo[0] if erros_corpo else "", 160)
                if fetcher is None:
                    time.sleep(1.0 / rps)
            an = ec.analyze_filing(f, texto)

            filings_rows.append({
                "emissor": nome, "cik": f["cik"], "ticker": f["ticker"],
                "form": f["form"], "accession": f["accession_number"],
                "filing_date": f["filing_date"], "report_date": f["report_date"],
                "items": ",".join(f["items"]), "description": _cut(f["description"], 120),
                "primary_document": f["primary_document"], "url": f["url"],
                "corpo_recuperado": bool(texto), "corpo_chars": len(texto),
                "erro_corpo": _cut(erros_corpo[0] if erros_corpo else "", 160),
                "titulo_canonico": _cut(ec.canonical_title(f), 200),
                "eventos_aceitos": ",".join(an["event_ids"]),
            })

            for cand in an["candidatos"]:
                linha["candidatos_evento"] += 1
                cand_rows.append({
                    "emissor": nome, "form": f["form"],
                    "accession": f["accession_number"],
                    "item": cand.get("item", ""), "event_id": cand.get("event_id", ""),
                    "origem": cand.get("origem", ""), "forca": cand.get("forca", ""),
                    "aceito": cand.get("aceito", False),
                    "confianca": cand.get("confianca", ""),
                    "motivo": _cut(cand.get("motivo_decisao") or cand.get("motivo"), 200),
                    "evidencia": _cut(cand.get("evidence_text"), 200),
                })
                if not cand.get("aceito"):
                    linha["rejeitados"] += 1
                    if not linha["motivo_rejeicao"]:
                        linha["motivo_rejeicao"] = _cut(
                            cand.get("motivo_decisao") or cand.get("motivo"), 120)

            # ── atribuição pelo pipeline REAL (filer ≠ sujeito automático) ──
            art = ec.to_article(f, texto, an)
            for ev in an["event_ids"]:
                veredito = _atribuir(rd, cfg, art, nome, ev)
                # forma periódica nunca pontua, qualquer que seja o veredito
                _c = next((x for x in an["aceitos"] if x["event_id"] == ev), {})
                if _c.get("nao_pontuavel_por_forma"):
                    veredito["scoreable"] = False
                    veredito["categoria"] = "informativo"
                    veredito["motivo"] = _c.get("motivo_decisao", "")
                linha["eventos_classificados"] += 1
                if veredito["categoria"] == "direto":
                    linha["eventos_diretos"] += 1
                elif veredito["categoria"] == "contexto":
                    linha["eventos_contextuais"] += 1
                else:
                    linha["eventos_informativos"] += 1

                cand_do_ev = next((x for x in an["aceitos"] if x["event_id"] == ev), {})
                # data ECONÔMICA (corpo > filing_date > report_date se aplicável)
                _near = cand_do_ev.get("evidence_start")
                data_econ = ec.economic_date(f, texto, _near if isinstance(_near, int) else None)
                fp_edgar = ec.entity_fingerprint(
                    (cand_do_ev.get("evidence_text") or "") + " " + ec.canonical_title(f),
                    exclude=[nome, f.get("company", "")])
                dd = ec.match_occurrence(veredito["subject_company"] or nome, ev,
                                         data_econ, fp_edgar, existentes)
                _m = dd.get("match") or {}
                dedup_rows.append({
                    "emissor": nome, "tipo": "ocorrencia_economica",
                    "accession": f["accession_number"], "duplicate_of": "",
                    "form": f["form"], "event_id": ev,
                    "acao": dd["acao"], "cria_ocorrencia": dd["cria_ocorrencia"],
                    "nivel": _m.get("nivel", ""), "lag_dias": _m.get("lag", ""),
                    "data_economica": data_econ,
                    "occurrence_id": _m.get("occurrence_id", ""),
                    "entidades_comuns": ", ".join(_m.get("entidades_comuns", [])),
                    "rejeitados": len(dd.get("rejeitados") or []),
                    "motivo": _cut(dd["motivo"], 220),
                })
                for rj in (dd.get("rejeitados") or [])[:3]:
                    dedup_rows.append({
                        "emissor": nome, "tipo": "match_rejeitado",
                        "accession": f["accession_number"], "duplicate_of": "",
                        "form": f["form"], "event_id": ev, "acao": "rejeitado",
                        "cria_ocorrencia": "", "nivel": "", "lag_dias": rj.get("lag", ""),
                        "data_economica": data_econ,
                        "occurrence_id": rj.get("occurrence_id", ""),
                        "entidades_comuns": "", "rejeitados": "",
                        "motivo": _cut(rj.get("motivo"), 220),
                    })
                row = {
                    "empresa_monitorada": nome,
                    "filer": f["company"],
                    "sujeito_economico": veredito["subject_company"] or nome,
                    "evento": ev,
                    "form": f["form"],
                    "item": cand_do_ev.get("item", ""),
                    "evidencia": _cut(cand_do_ev.get("evidence_text"), 240),
                    "confianca": cand_do_ev.get("confianca", ""),
                    "scoreable_simulado": veredito["scoreable"],
                    "decisao_final": veredito["categoria"],
                    "motivo": _cut(veredito["motivo"], 200),
                    "accession": f["accession_number"],
                    "report_date": f["report_date"],
                    "data_economica": data_econ,
                    "url": f["url"],
                    "corroboracao": dd["acao"],
                    "nivel_match": _m.get("nivel", ""),
                    "lag_dias": _m.get("lag", ""),
                    "nao_pontuavel_por_forma": bool(
                        cand_do_ev.get("nao_pontuavel_por_forma")),
                }
                class_rows.append(row)

                if _suspeito(veredito, cand_do_ev, f, texto):
                    fp_rows.append({**row, "suspeita": _suspeito(veredito, cand_do_ev,
                                                                 f, texto)})

        linha["corpos_baixados"] = corpos
        linha["elapsed_ms"] = int((time.time() - t0) * 1000)
        por_emissor.append(linha)

    # ── artifacts ──
    _write_csv(out / "edgar_4h3c_por_emissor.csv", por_emissor, list(
        por_emissor[0].keys()) if por_emissor else ["emissor"])
    _write_csv(out / "edgar_4h3c_filings.csv", filings_rows, [
        "emissor", "cik", "ticker", "form", "accession", "filing_date", "report_date",
        "items", "description", "primary_document", "url", "corpo_recuperado",
        "corpo_chars", "erro_corpo", "titulo_canonico", "eventos_aceitos"])
    _write_csv(out / "edgar_4h3c_event_candidates.csv", cand_rows, [
        "emissor", "form", "accession", "item", "event_id", "origem", "forca",
        "aceito", "confianca", "motivo", "evidencia"])
    _write_csv(out / "edgar_4h3c_classification.csv", class_rows, [
        "empresa_monitorada", "filer", "sujeito_economico", "evento", "form", "item",
        "evidencia", "confianca", "scoreable_simulado", "decisao_final", "motivo",
        "accession", "report_date", "data_economica", "url", "corroboracao",
        "nivel_match", "lag_dias", "nao_pontuavel_por_forma"])
    _write_csv(out / "edgar_4h3c_false_positive_review.csv", fp_rows, [
        "empresa_monitorada", "filer", "sujeito_economico", "evento", "form", "item",
        "evidencia", "confianca", "scoreable_simulado", "decisao_final", "suspeita",
        "accession", "url"])
    _write_csv(out / "edgar_4h3c_dedup.csv", dedup_rows, [
        "emissor", "tipo", "accession", "duplicate_of", "form", "event_id",
        "acao", "cria_ocorrencia", "nivel", "lag_dias", "data_economica",
        "occurrence_id", "entidades_comuns", "rejeitados", "motivo"])

    hashes_depois = {f: _sha(f) for f in watch}
    alterados = [f for f in watch if hashes_antes[f] != hashes_depois[f]]

    meta = {
        "fase": "4H.3C",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "janela_dias": janela,
        "universo_elegivel": len(alvos),
        "emissores_com_http_200": sum(1 for l in por_emissor if l["http"] == "200"),
        "cik_nao_resolvido": sum(1 for l in por_emissor if l["erro"] == "cik_nao_resolvido"),
        "filings_encontrados": sum(l["filings_encontrados"] for l in por_emissor),
        "filings_aceitos": sum(l["filings_aceitos"] for l in por_emissor),
        "corpos_recuperados": sum(l["corpos_baixados"] for l in por_emissor),
        "candidatos_evento": len(cand_rows),
        "candidatos_aceitos": sum(1 for r in cand_rows if r["aceito"]),
        "eventos_classificados": len(class_rows),
        "eventos_diretos": sum(1 for r in class_rows if r["decisao_final"] == "direto"),
        "eventos_contextuais": sum(1 for r in class_rows if r["decisao_final"] == "contexto"),
        "eventos_informativos": sum(1 for r in class_rows
                                    if r["decisao_final"] == "informativo"),
        "falsos_positivos_para_revisao": len(fp_rows),
        # ── métricas separadas (§6): pontuável nunca se mistura com informativo ──
        "eventos_potencialmente_pontuaveis": sum(1 for r in class_rows
                                                 if r["scoreable_simulado"]),
        "falsos_positivos_pontuaveis": sum(1 for r in fp_rows
                                           if r.get("scoreable_simulado")),
        "pontuaveis_confirmados_verdadeiros": (
            sum(1 for r in class_rows if r["scoreable_simulado"])
            - sum(1 for r in fp_rows if r.get("scoreable_simulado"))),
        "nao_pontuavel_por_forma": sum(1 for r in class_rows
                                       if r.get("nao_pontuavel_por_forma")),
        "dedup_documentos_repetidos": sum(1 for r in dedup_rows
                                          if r["tipo"] == "documento_repetido"),
        "dedup_corroboracoes": sum(1 for r in dedup_rows if r["acao"] == "corroborar"),
        "corroboracoes_nivel_1": sum(1 for r in dedup_rows if r.get("nivel") == 1),
        "corroboracoes_nivel_2": sum(1 for r in dedup_rows if r.get("nivel") == 2),
        "matches_rejeitados": sum(1 for r in dedup_rows if r["tipo"] == "match_rejeitado"),
        "dedup_novas_ocorrencias": sum(1 for r in dedup_rows
                                       if r["acao"] == "nova_ocorrencia"),
        "lag_distribuicao": _lag_dist(dedup_rows),
        "ocorrencias_conhecidas_na_janela": len(existentes),
        # invariantes duras
        "scoring_enabled": rd.edgar_scoring_enabled(cfg),
        "persisted_records": 0,
        "history_changed": False,
        "dashboard_changed": False,
        "backfill": False,
        "arquivos_alterados": alterados,
        "hashes_antes": hashes_antes,
        "hashes_depois": hashes_depois,
    }
    (out / "edgar_4h3c_run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "relatorio_edgar_4h3c.md").write_text(
        _relatorio(meta, por_emissor, class_rows, fp_rows, dedup_rows),
        encoding="utf-8")

    if alterados:
        raise RuntimeError(f"INVARIÂNCIA VIOLADA — arquivos alterados: {alterados}")
    return meta


def _lag_dist(dedup_rows: list[dict]) -> dict:
    """Distribuição real de |lag| entre data econômica do filing e da notícia.
    É o dado que justifica (ou não) a tolerância por família — nunca escolher
    ±7/±14/±30 por gosto."""
    d = {}
    for r in dedup_rows:
        lag = r.get("lag_dias")
        if isinstance(lag, int):
            d[abs(lag)] = d.get(abs(lag), 0) + 1
    return dict(sorted(d.items()))


def _occurrences_from_history(history: dict, rd, cutoff_ts: int = 0) -> list[dict]:
    """Ocorrências econômicas já conhecidas por Google News / RI / CVM.

    Cada ocorrência carrega a IMPRESSÃO DIGITAL de contraparte extraída do
    título — é ela, não a data, que decide identidade econômica.
    """
    out = []
    for url, rec in (history.get("articles") or {}).items():
        ts = int(rec.get("pub_ts") or 0)
        if cutoff_ts and ts and ts < cutoff_ts:
            continue
        data = str(rec.get("date") or "")[:10]
        if not data and ts:
            data = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        titulo = rec.get("title") or ""
        for empresa, evs in (rec.get("events_by_company") or {}).items():
            fp = ec.entity_fingerprint(titulo, exclude=[empresa])
            for ev in (evs or []):
                out.append({
                    "company": empresa, "event_id": ev, "date": data,
                    "fingerprint": fp, "source": rec.get("source", ""),
                    "title": titulo[:120], "url": url,
                    "occurrence_id": f"{empresa}|{ev}|{data}|{str(url)[-24:]}",
                })
    return out


def _atribuir(rd, cfg, art: dict, monitorada: str, event_id: str) -> dict:
    """Veredito de SUJEITO pelo pipeline semântico real — nunca pelo filer."""
    try:
        import semantic_audit as sa
        aliases = {c["name"]: (c.get("aliases") or [c["name"]])
                   for c in cfg.get("watchlist", [])}
        r = sa.resolve_article_semantics(art.get("title", ""), art.get("summary", ""),
                                         monitorada, [event_id], aliases,
                                         article_year=datetime.now(timezone.utc).year)
        d = next((x for x in r.get("decisoes", []) if x.get("event_id") == event_id), {})
        subj = d.get("subject_company") or ""
        scoreable = bool(d.get("scoreable"))
        if scoreable:
            cat = "direto"
        elif subj and ec._clean(subj).lower() != ec._clean(monitorada).lower():
            cat = "contexto"
        else:
            cat = "informativo"
        return {"subject_company": subj or monitorada, "scoreable": scoreable,
                "categoria": cat,
                "motivo": d.get("rejection_reason") or d.get("attribution_rule") or "—"}
    except Exception as exc:
        return {"subject_company": monitorada, "scoreable": False,
                "categoria": "informativo",
                "motivo": f"falha na atribuição semântica: {type(exc).__name__}"}


def _suspeito(veredito: dict, cand: dict, filing: dict, texto: str) -> str:
    """Heurísticas de FALSO POSITIVO para revisão humana."""
    if veredito["scoreable"] and not texto:
        return "evento pontuável sem corpo do documento recuperado"
    if veredito["scoreable"] and cand.get("confianca") in ("baixa", "nenhuma", ""):
        return f"evento pontuável com confiança {cand.get('confianca') or 'vazia'}"
    if veredito["scoreable"] and not cand.get("item") and filing.get("form") in ec.PERIODIC_FORMS:
        return "evento material inferido de formulário periódico sem item"
    if cand.get("item") in ec.GENERIC_ITEMS and veredito["scoreable"]:
        return f"item genérico {cand.get('item')} sustentando evento pontuável"
    if veredito["categoria"] == "direto" and ec._clean(
            veredito["subject_company"]).lower() != ec._clean(filing.get("company")).lower():
        return "sujeito diverge do filer mas foi classificado como direto"
    return ""


def _relatorio(meta, por_emissor, class_rows, fp_rows, dedup_rows) -> str:
    tot_evt = meta["eventos_classificados"]
    fp = meta["falsos_positivos_para_revisao"]
    cobertura_corpo = (100.0 * meta["corpos_recuperados"] / meta["filings_aceitos"]
                       if meta["filings_aceitos"] else 0.0)

    # A precisão que decide scoring é sobre os eventos que PONTUARIAM, não sobre
    # o total. Diluir os falsos positivos entre eventos informativos (que nunca
    # pontuam) produziu "91,1% → C" no run 31142988539 quando a precisão real
    # sobre pontuáveis era 0/13. Métrica errada é pior que métrica ausente.
    pont = meta["eventos_diretos"]
    fp_pont = sum(1 for r in fp_rows if r.get("scoreable_simulado"))
    prec_pont = (100.0 * (pont - fp_pont) / pont) if pont else None
    prec_geral = (100.0 * (tot_evt - fp) / tot_evt) if tot_evt else 0.0

    if meta["filings_aceitos"] == 0 or meta["corpos_recuperados"] == 0:
        rec, just = "A", ("nenhum corpo de documento recuperado — sem conteúdo real "
                          "não há como medir precisão")
    elif cobertura_corpo < 70:
        rec, just = "A", (f"apenas {cobertura_corpo:.0f}% dos filings tiveram corpo "
                          f"recuperado; evidência insuficiente")
    elif tot_evt == 0:
        rec, just = "B", ("nenhum evento no período — pipeline estruturalmente correto, "
                          "sem falso positivo, mas sem massa para medir recall")
    elif pont == 0:
        # Bloquear TODO evento pontuável e depois exibir "zero falso positivo"
        # não é evidência de qualidade — é ausência de medição. Nunca C aqui.
        rec, just = "B", (
            f"{tot_evt} evento(s) classificado(s) e NENHUM pontuável. Extração "
            f"real estável ({cobertura_corpo:.0f}% de corpo) e atribuição correta, "
            f"mas zero falso positivo depois de bloquear todos os pontuáveis é "
            f"AUSÊNCIA DE MEDIÇÃO, não precisão. Sem massa pontuável verdadeira, "
            f"C está descartado por construção.")
    elif prec_pont >= 90 and pont >= 10:
        rec, just = "C", (f"precisão sobre PONTUÁVEIS {prec_pont:.1f}% ({pont - fp_pont}"
                          f"/{pont}, massa n={pont}) com cobertura de corpo "
                          f"{cobertura_corpo:.0f}% — candidato a avaliação futura")
    elif prec_pont >= 90:
        rec, just = "B", (f"precisão sobre pontuáveis {prec_pont:.1f}%, mas massa "
                          f"pequena demais (n={pont} < 10) para sustentar C")
    elif prec_pont >= 75:
        rec, just = "B", (f"precisão sobre pontuáveis {prec_pont:.1f}% — confiável como "
                          f"corroboração, insuficiente para score")
    else:
        rec, just = "A", (f"precisão sobre pontuáveis {prec_pont:.1f}% "
                          f"({pont - fp_pont}/{pont}) — abaixo do aceitável")

    L = [
        "# Relatório 4H.3C — EDGAR canônico em modo sombra", "",
        f"Gerado em {meta['generated_at']} · janela de {meta['janela_dias']} dias.", "",
        "> **Esta fase NÃO ativa scoring.** `edgar_scoring_enabled` permanece "
        f"`{meta['scoring_enabled']}` e nenhum filing foi persistido.", "",
        "## Universo", "",
        "| Métrica | Valor |", "|---|---|",
        f"| Emissores elegíveis | {meta['universo_elegivel']} |",
        f"| HTTP 200 | {meta['emissores_com_http_200']} |",
        f"| CIK não resolvido | {meta['cik_nao_resolvido']} |",
        f"| Filings encontrados | {meta['filings_encontrados']} |",
        f"| Filings aceitos (pós-dedup) | {meta['filings_aceitos']} |",
        f"| Corpos recuperados | {meta['corpos_recuperados']} ({cobertura_corpo:.0f}%) |",
        "",
        "## Classificação", "",
        "| Métrica | Valor |", "|---|---|",
        f"| Candidatos a evento | {meta['candidatos_evento']} |",
        f"| Candidatos aceitos | {meta['candidatos_aceitos']} |",
        f"| Eventos classificados | {tot_evt} |",
        f"| Diretos | {meta['eventos_diretos']} |",
        f"| Contextuais (terceiro) | {meta['eventos_contextuais']} |",
        f"| Informativos | {meta['eventos_informativos']} |",
        f"| Suspeitas de falso positivo | {fp} |",
        f"| **Eventos PONTUÁVEIS** | **{pont}** |",
        f"| Falsos positivos entre pontuáveis | {fp_pont} |",
        f"| **Precisão sobre PONTUÁVEIS** | "
        f"**{f'{prec_pont:.1f}%' if prec_pont is not None else 'n/a (nenhum pontuável)'}** |",
        f"| Precisão geral (diluída — NÃO usar para decidir scoring) | {prec_geral:.1f}% |",
        "",
        "## Deduplicação e corroboração", "",
        f"- Ocorrências conhecidas na janela (News/RI): **{meta['ocorrencias_conhecidas_na_janela']}**",
        f"- Documentos repetidos descartados: **{meta['dedup_documentos_repetidos']}**",
        f"- **Corroborações nível 1** (contraparte + data compatível): "
        f"**{meta['corroboracoes_nivel_1']}**",
        f"- **Corroborações nível 2** (contraparte, data fora da tolerância): "
        f"**{meta['corroboracoes_nivel_2']}**",
        f"- **Matches rejeitados** (empresa+família sem contraparte comum): "
        f"**{meta['matches_rejeitados']}**",
        f"- Ocorrências novas: **{meta['dedup_novas_ocorrencias']}**",
        f"- Não pontuável por forma (periódico): **{meta['nao_pontuavel_por_forma']}**",
        "",
        f"Distribuição de |lag| (dias entre data econômica do filing e da "
        f"notícia): `{meta['lag_distribuicao'] or 'sem pares comparáveis'}`", "",
        "## Invariância de produção", "",
        f"- `scoring_enabled`: **{meta['scoring_enabled']}**",
        f"- `persisted_records`: **{meta['persisted_records']}**",
        f"- `history_changed`: **{meta['history_changed']}**",
        f"- `backfill`: **{meta['backfill']}**",
        f"- Arquivos alterados: **{meta['arquivos_alterados'] or 'nenhum'}**", "",
        f"## Recomendação: **{rec}**", "", just, "",
        "| Classificação | Significado |", "|---|---|",
        "| A | shadow ainda insuficiente |",
        "| B | tecnicamente confiável como fonte oficial/corroborante, SEM score |",
        "| C | candidato futuro a scoring (exige aprovação explícita) |", "",
    ]
    if fp_rows:
        L += ["## Suspeitas de falso positivo", "",
              "| Empresa | Evento | Form/Item | Suspeita |", "|---|---|---|---|"]
        for r in fp_rows[:40]:
            L.append(f"| {r['empresa_monitorada']} | {r['evento']} | "
                     f"{r['form']}/{r['item'] or '—'} | {r['suspeita']} |")
        L.append("")
    return "\n".join(L)


# ───────────────────────────────── CLI ───────────────────────────────────────
def emit_candidate_config(base_path: str, dest: str) -> str:
    """Gera o config CANDIDATO a partir do de produção: coleta LIGADA,
    scoring DESLIGADO. Nunca sobrescreve o config de produção."""
    import yaml
    dest_p = Path(dest)
    if dest_p.resolve() == Path(base_path).resolve():
        raise RuntimeError("recusado: destino é o config de produção")
    cfg = yaml.safe_load(Path(base_path).read_text(encoding="utf-8"))
    cfg["international_official_sources_enabled"] = True
    cfg.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    cfg["edgar_scoring_enabled"] = False
    dest_p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                      encoding="utf-8")
    return str(dest_p)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="4H.3C — EDGAR canônico em sombra")
    ap.add_argument("--config", default="config_risco_4h3c_candidato.yaml")
    ap.add_argument("--outdir", default="out_4h3c")
    ap.add_argument("--history", default="risk_history.json")
    ap.add_argument("--max-companies", type=int, default=None)
    ap.add_argument("--emit-candidate-config", metavar="DEST",
                    help="gera o config candidato a partir de config_risco.yaml e sai")
    a = ap.parse_args(argv)

    if a.emit_candidate_config:
        p = emit_candidate_config("config_risco.yaml", a.emit_candidate_config)
        print(f"config candidato gerado: {p}")
        return 0

    import risk_dashboard as rd
    cfg = rd.load_config(a.config)
    if rd.edgar_scoring_enabled(cfg):
        print("ABORTADO: edgar_scoring_enabled=true não é permitido na 4H.3C")
        return 2
    hist = {}
    hp = Path(a.history)
    if hp.exists():
        try:
            hist = json.loads(hp.read_text(encoding="utf-8"))
        except Exception:
            hist = {}
    meta = run_shadow_4h3c(rd, cfg, outdir=a.outdir, history=hist,
                           max_companies=a.max_companies)
    print(json.dumps({k: meta[k] for k in (
        "universo_elegivel", "emissores_com_http_200", "filings_encontrados",
        "filings_aceitos", "corpos_recuperados", "eventos_classificados",
        "falsos_positivos_para_revisao", "scoring_enabled", "persisted_records",
        "arquivos_alterados")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
