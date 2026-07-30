#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_shadow_4h3a.py — 4H.3A Blocos C–G.

Dois modos, ambos NÃO-PERSISTENTES:

  --edgar-dry-run     coleta real do EDGAR, telemetria por estágio, sem classificar
  --edgar-shadow-run  coleta real + classificador COMPLETO em sombra + score simulado

Garantias (críticas):
  - não escreve risk_history.json;
  - não publica index.html;
  - não altera score de produção;
  - não executa backfill;
  - não executa Google News / CVM / RI / outros coletores;
  - roda independentemente das flags (é modo diagnóstico isolado), sem alterar
    silenciosamente o comportamento normal de produção.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

STAGES = ["raw_submissions", "date_filtered", "form_filtered",
          "issuer_filtered", "parsed", "deduplicated", "accepted"]

# Eventos que exigem evidência textual explícita para pontuar (Bloco E).
EVENTOS_EXIGEM_EVIDENCIA = {
    "recuperacao_judicial", "falencia", "default", "inadimplencia",
    "rebaixamento_rating", "fusao_aquisicao", "m&a", "reestruturacao_divida",
}
# Itens de 8-K que, por si, NÃO provam evento material de crédito (Bloco E).
ITENS_8K_NAO_PROBATORIOS = {"7.01", "8.01", "9.01", "2.02", "5.02", "5.03", "5.07"}


def _write_csv(path: Path, rows: list[dict], keys: list[str] | None = None):
    keys = keys or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# ─────────────────────────── Bloco C: dry-run real ───────────────────────────
def edgar_dry_run(rd, cfg: dict, *, outdir: str = ".", run_meta: dict | None = None,
                  limit: int | None = None) -> dict:
    """Coleta REAL por emissor com telemetria por estágio. Não usa fixtures.

    `rd` é o módulo risk_dashboard já importado (evita import circular)."""
    import requests
    outp = Path(outdir); outp.mkdir(parents=True, exist_ok=True)
    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    forms_default = rd._normalize_edgar_forms(src.get("formularios_gatilho"))
    rps = max(1, int(src.get("rate_limit_rps", 8)))
    janela = int((cfg.get("evolution") or {}).get("window_days", 90))
    cutoff = int(datetime.now(timezone.utc).timestamp()) - janela * 86400
    data_ini = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d")
    data_fim = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    alvos = [c for c in cfg.get("watchlist", []) if rd.edgar_eligible(c)]
    if limit:
        alvos = alvos[:limit]
    session = requests.Session()
    cikmap = rd._load_cik_map(session)

    # ── Guarda de AMBIENTE (aprendizado 4H.2) ──
    # Distinguir bloqueio de sandbox/proxy de resultado real da SEC. Sem o mapa
    # de CIK, todo emissor viraria "cik_nao_resolvido" e o CSV registraria zeros
    # que PARECEM resultado real. Isso é proibido: abortar com status explícito.
    if not cikmap:
        probe_err = ""
        try:
            pr = session.get("https://data.sec.gov/submissions/CIK0000037996.json",
                             headers=rd._edgar_headers(), timeout=15)
            probe_err = f"HTTP {pr.status_code}"
        except Exception as exc:  # noqa: BLE001
            probe_err = f"{type(exc).__name__}: {str(exc)[:120]}"
        linha = [{
            "status": "AMBIENTE_SEM_ACESSO_SEC",
            "detalhe": ("mapa ticker→CIK indisponível E endpoint submissions "
                        f"inacessível ({probe_err})"),
            "interpretacao": ("bloqueio de sandbox/proxy — NÃO é resultado da SEC "
                              "e NÃO constitui validação real"),
            "emissores_elegiveis": len(alvos),
            "acao": ("executar workflow_edgar_dry_run.yml no GitHub Actions "
                     "e coletar os artifacts"),
        }]
        _write_csv(outp / "edgar_dry_run_real_por_emissor.csv", linha)
        _write_csv(outp / "edgar_dry_run_real_por_estagio.csv", linha)
        _write_csv(outp / "edgar_filings_reais.csv", linha)
        print("   ❌ AMBIENTE SEM ACESSO À SEC "
              f"({probe_err}). Nenhum número real foi produzido; "
              "validação real NÃO realizada.")
        print("      → rode workflow_edgar_dry_run.yml (workflow_dispatch) "
              "e traga os artifacts.")
        return {"por_emissor": linha, "por_estagio": [], "filings": [],
                "agg": {s: 0 for s in STAGES}, "n_alvos": len(alvos),
                "environment_blocked": True}

    por_emissor, por_estagio, filings = [], [], []
    agg = {s: 0 for s in STAGES}
    for c in alvos:
        nome = c["name"]
        t0 = time.time()
        forms_c = rd.edgar_forms_for(c, forms_default)
        cik10 = c.get("cik") or (cikmap or {}).get(str(c.get("ticker") or "").upper())
        row = {
            "emissor": nome, "pais": c.get("country") or "",
            "asset_class": c.get("asset_class") or "",
            "vehicle_kind": c.get("vehicle_kind") or "",
            "ticker": c.get("ticker") or "", "tentativa": "sim",
            "cik_configurado": c.get("cik") or "—",
            "cik_consultado": cik10 or "—",
            "endpoint": (f"https://data.sec.gov/submissions/CIK{cik10}.json"
                         if cik10 else "—"),
            "intervalo_datas": f"{data_ini}..{data_fim}",
            "formularios_aceitos": ";".join(sorted(forms_c)),
            "http": "", "erro": "", "tempo_ms": 0,
            **{s: 0 for s in STAGES},
            "formularios_vistos": "", "formularios_rejeitados": "",
            "accession_numbers": "", "urls_diretas": "",
        }
        if not cik10:
            row.update(erro="cik_nao_resolvido", http="",
                       tempo_ms=int((time.time() - t0) * 1000))
            por_emissor.append(row)
            continue
        data, status, err = {}, None, ""
        try:
            r = session.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                            headers=rd._edgar_headers(), timeout=25)
            status = r.status_code
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:180]}"
        row["http"] = status if status is not None else ""
        row["erro"] = err
        if data:
            import edgar_audit_4h2 as ea
            cnt = ea.edgar_stage_counts(data, forms_c, cutoff)
            for s in STAGES:
                row[s] = cnt[s]
                agg[s] += cnt[s]
            row["formularios_vistos"] = ";".join(
                f"{k}:{v}" for k, v in sorted(cnt["_forms_seen"].items()))
            row["formularios_rejeitados"] = ";".join(
                f"{k}:{v}" for k, v in sorted(cnt["_forms_dropped_by_form_filter"].items()))
            amostra = ea.sample_filings(data, forms_c, cutoff, str(cik10), nome, limit=50)
            row["accession_numbers"] = ";".join(a["accession_number"] for a in amostra[:10])
            row["urls_diretas"] = ";".join(a["url_direta"] for a in amostra[:3])
            for a in amostra:
                filings.append({**a, "pais": c.get("country") or "",
                                "asset_class": c.get("asset_class") or ""})
            por_estagio.append({"emissor": nome, **{s: cnt[s] for s in STAGES}})
        row["tempo_ms"] = int((time.time() - t0) * 1000)
        por_emissor.append(row)
        time.sleep(1.0 / rps)

    _write_csv(outp / "edgar_dry_run_real_por_emissor.csv", por_emissor)
    _write_csv(outp / "edgar_dry_run_real_por_estagio.csv", por_estagio)
    _write_csv(outp / "edgar_filings_reais.csv", filings)
    print(f"   ✅ dry-run real: {len(alvos)} emissor(es), "
          f"{agg['accepted']} filing(s) aceitos na janela de {janela}d.")
    return {"por_emissor": por_emissor, "por_estagio": por_estagio,
            "filings": filings, "agg": agg, "n_alvos": len(alvos)}


# ────────────────── Blocos D/E/F: shadow classification ──────────────────
def _evidence_for(art: dict, event_id: str) -> tuple[str, str]:
    """Devolve (fonte_da_evidencia, trecho). Metadados do submissions só
    sustentam classificação se o texto contiver o termo do evento."""
    titulo = art.get("title") or ""
    desc = art.get("summary") or ""
    itens = str(art.get("filing_items") or "")
    blob = f"{titulo} {desc}"
    if not blob.strip():
        return "insufficient_metadata", ""
    # o item do 8-K, isolado, não é prova de evento material
    if event_id in EVENTOS_EXIGEM_EVIDENCIA:
        alvo = event_id.split("_")[0][:6].lower()
        if alvo and alvo in blob.lower():
            i = blob.lower().index(alvo)
            return "metadata", blob[max(0, i - 40):i + 60]
        if itens and any(it.strip() in ITENS_8K_NAO_PROBATORIOS
                         for it in itens.split(",")):
            return "insufficient_metadata", f"items={itens} (não probatório)"
        return "insufficient_metadata", ""
    return "metadata", blob[:90]


def edgar_shadow_run(rd, cfg: dict, *, outdir: str = ".",
                     filings_csv: str | None = None,
                     dry: dict | None = None) -> dict:
    """Passa os filings REAIS pelo classificador completo em modo sombra.
    Nada é persistido: não toca histórico, não publica, não altera score."""
    outp = Path(outdir); outp.mkdir(parents=True, exist_ok=True)
    # fonte dos filings: resultado do dry-run ou CSV já coletado
    if dry and dry.get("filings"):
        base = dry["filings"]
    elif filings_csv and Path(filings_csv).exists():
        base = list(csv.DictReader(open(filings_csv, encoding="utf-8-sig")))
    else:
        print("   ⚠️  shadow-run sem filings reais (rode --edgar-dry-run primeiro).")
        return {"classificacao": [], "contexto": [], "score": [], "fp": []}

    scoring_on = rd.edgar_scoring_enabled(cfg)
    classificacao, contexto, score_sim, fp = [], [], [], []
    vistos = {}

    for f in base:
        emissor = f.get("emissor") or f.get("filing_company") or ""
        art = {
            "title": f.get("titulo") or f"{emissor} — {f.get('formulario','')}"
                     + (f": {f.get('descricao')}" if f.get("descricao") else ""),
            "summary": f.get("descricao") or f.get("formulario") or "",
            "url": f.get("url_direta") or "", "pub_ts": 0,
            "source": "SEC · EDGAR", "domain": "sec.gov", "language": "en",
            "forced_trust": "oficial",
            "filing_company": emissor, "source_company": emissor,
            "provenance": "EDGAR", "form": f.get("formulario") or "",
            "accession_number": f.get("accession_number") or "",
            "primary_document": f.get("primary_document") or "",
            "filing_items": f.get("filing_items") or "",
            "filing_date": f.get("data") or "",
        }
        # tradução + classificação + atribuição: MESMO caminho da produção
        try:
            rd.translate_articles([art], cfg)
        except Exception:
            pass
        rd.classify_and_attribute(art, cfg)

        ebc = art.get("events_by_company") or {}
        ctx = art.get("context_events_by_company") or {}
        subj = ""
        for _co, _r in (art.get("mention_roles") or {}).items():
            if _co == emissor:
                subj = _r.get("subject_company", "")
        eventos_diretos = ebc.get(emissor, [])

        # ── Bloco E: classificação de qualidade do filing ──
        if not eventos_diretos and not ctx:
            categoria = "informativo"
        elif not eventos_diretos and ctx:
            categoria = "contextual"
        else:
            categoria = "evento_material"

        for ev in (eventos_diretos or [None]):
            ev_fonte, ev_trecho = _evidence_for(art, ev or "")
            pontua = bool(ev) and ev_fonte != "insufficient_metadata"
            classificacao.append({
                "filing_company": emissor,
                "subject_company": subj or (emissor if ev else ""),
                "form": art["form"], "filing_date": art["filing_date"],
                "accession_number": art["accession_number"],
                "primary_document": art["primary_document"],
                "title": art["title"][:180], "description": art["summary"][:140],
                "items_8k": art["filing_items"],
                "event_ids_candidatos": ";".join(eventos_diretos),
                "eventos_removidos": ";".join(
                    e.get("event_id", "") for e in (ctx.get(emissor) or [])),
                "events_by_company": json.dumps(ebc, ensure_ascii=False)[:200],
                "context_events_by_company": json.dumps(
                    {k: [e.get("event_id") for e in v] for k, v in ctx.items()},
                    ensure_ascii=False)[:200],
                "categoria_filing": categoria,
                "classification_evidence": ev_fonte,
                "evidencia_textual": ev_trecho[:120],
                "pontuaria": "sim" if pontua else "não",
                "motivo": ("evidência textual presente" if pontua else
                           ("sem evento direto" if not ev else
                            "metadados insuficientes — não pontua")),
                "scoring_flag_ativa": "sim" if scoring_on else "não",
            })
            # score simulado (NUNCA aplicado)
            if ev:
                sev = next((e.get("severity") for e in cfg.get("taxonomy", [])
                            if e.get("id") == ev), "")
                sc = next((e.get("score") for e in cfg.get("taxonomy", [])
                           if e.get("id") == ev), 0)
                score_sim.append({
                    "filing_company": emissor, "event_id": ev,
                    "accession_number": art["accession_number"],
                    "severidade": sev, "score_taxonomia": sc,
                    "score_simulado": (sc if pontua else 0),
                    "aplicado_em_producao": "não (shadow mode)",
                    "direcao": next((e.get("direction") for e in cfg.get("taxonomy", [])
                                     if e.get("id") == ev), ""),
                })
                if pontua and (sc or 0) > 5:
                    fp.append({
                        "filing_company": emissor, "subject_company": subj or emissor,
                        "event_id": ev, "form": art["form"],
                        "accession_number": art["accession_number"],
                        "title": art["title"][:150],
                        "evidencia": ev_trecho[:110] or "(vazia)",
                        "revisao_necessaria": "sim (score>5)",
                        "risco": ("filer≠sujeito — verificar atribuição"
                                  if subj and subj != emissor else
                                  "verificar evidência textual do evento"),
                    })
        for _co, _evs in ctx.items():
            for e in _evs:
                contexto.append({
                    "filing_company": emissor, "card_company": _co,
                    "subject_company": e.get("subject_company", ""),
                    "event_id": e.get("event_id"),
                    "relation_type": e.get("relation_type"),
                    "impact_type": e.get("impact_type"),
                    "scoreable": e.get("scoreable", False),
                    "accession_number": art["accession_number"],
                    "attribution_evidence": (e.get("attribution_evidence") or "")[:110],
                })
        vistos.setdefault(art["accession_number"], 0)
        vistos[art["accession_number"]] += 1

    _write_csv(outp / "edgar_shadow_classification.csv", classificacao)
    _write_csv(outp / "edgar_shadow_context_events.csv", contexto,
               keys=["filing_company", "card_company", "subject_company", "event_id",
                     "relation_type", "impact_type", "scoreable", "accession_number",
                     "attribution_evidence"])
    _write_csv(outp / "edgar_shadow_score_simulation.csv", score_sim,
               keys=["filing_company", "event_id", "accession_number", "severidade",
                     "score_taxonomia", "score_simulado", "aplicado_em_producao", "direcao"])
    _write_csv(outp / "edgar_shadow_false_positive_review.csv", fp,
               keys=["filing_company", "subject_company", "event_id", "form",
                     "accession_number", "title", "evidencia", "revisao_necessaria", "risco"])
    print(f"   ✅ shadow: {len(classificacao)} linha(s), {len(contexto)} contexto, "
          f"{len(fp)} p/ revisão de falso positivo. Nada persistido.")
    return {"classificacao": classificacao, "contexto": contexto,
            "score": score_sim, "fp": fp, "dup": vistos}


# ─────────────────────────── Bloco G: deduplicação ───────────────────────────
def edgar_dedup_audit(rd, cfg: dict, history: dict, filings: list[dict],
                      *, outdir: str = ".") -> list[dict]:
    """Confere se o filing oficial corrobora ocorrência existente (mídia/RI) em
    vez de criar uma segunda ocorrência econômica e inflar score."""
    outp = Path(outdir); outp.mkdir(parents=True, exist_ok=True)
    recs = (history or {}).get("articles") or {}
    # índice por (empresa normalizada, event_id) das ocorrências já no histórico
    idx = {}
    for url, rec in recs.items():
        ebc = rec.get("events_by_company") or {}
        for co, evs in ebc.items():
            for ev in (evs or []):
                idx.setdefault((rd.normalize(co), ev), []).append(
                    {"url": url, "source": rec.get("source", "")})
    rows, vistos_acc = [], set()
    for f in filings:
        acc = f.get("accession_number") or ""
        emissor = f.get("emissor") or ""
        ev = f.get("event_id") or ""
        dup_acc = acc in vistos_acc
        vistos_acc.add(acc)
        match = idx.get((rd.normalize(emissor), ev), []) if ev else []
        rows.append({
            "accession_number": acc, "filing_company": emissor,
            "event_id": ev or "—",
            "occurrence_id": f"{rd.normalize(emissor)}|{ev}" if ev else "—",
            "matched_existing_occurrence": "sim" if match else "não",
            "matched_url": (match[0]["url"] if match else ""),
            "matched_source": (match[0]["source"] if match else ""),
            "acao": ("descartar_duplicata_accession" if dup_acc else
                     ("corroborar_ocorrencia_existente" if match else
                      ("nova_ocorrencia_potencial" if ev else "sem_evento_nao_pontua"))),
            "score_incremental": 0,
            "justificativa": ("mesmo accession já processado" if dup_acc else
                              ("documento oficial corrobora ocorrência de mídia/RI; "
                               "não cria segunda ocorrência econômica" if match else
                               ("sem evento material — não pontua" if not ev else
                                "sem ocorrência prévia; entraria como nova (score OFF nesta fase)"))),
        })
    _write_csv(outp / "auditoria_deduplicacao_edgar.csv", rows,
               keys=["accession_number", "filing_company", "event_id", "occurrence_id",
                     "matched_existing_occurrence", "matched_url", "matched_source",
                     "acao", "score_incremental", "justificativa"])
    return rows
