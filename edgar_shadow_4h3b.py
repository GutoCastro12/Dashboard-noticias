#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_shadow_4h3b.py — 4H.3B.0: gate real de shadow mode + atribuição por
empresa × evento × EVIDÊNCIA TEXTUAL.

Duas responsabilidades:

1. `run_edgar_runtime_shadow()` — executado DENTRO da execução normal quando
   coleta=ON e scoring=OFF. Classifica os filings em sombra e grava artifacts
   `edgar_runtime_shadow_*`. Não persiste histórico, não altera score, feed,
   build_changes nem HTML. Prova a invariância por hash.

2. `attribute_events_by_evidence()` — corrige o VAZAMENTO empresa × evento.
   O papel geral do artigo não pode decidir todos os eventos: cada evento é
   ancorado à entidade citada na mesma oração / construção possessiva / janela
   textual válida.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_FILES = [
    "edgar_runtime_shadow_classification.csv",
    "edgar_runtime_shadow_context_events.csv",
    "edgar_runtime_shadow_score_simulation.csv",
    "edgar_runtime_shadow_false_positive_review.csv",
    "edgar_runtime_shadow_deduplication.csv",
    "edgar_runtime_shadow_run_meta.json",
]

# Termos que ancoram cada família de evento no texto (multilíngue).
EVENT_ANCHORS = {
    "rebaixamento_rating": [
        r"rating", r"ratings", r"classifica[çc][ãa]o", r"calificaci[óo]n",
        r"nota de cr[ée]dito", r"downgrade", r"downgraded", r"downgrades",
        r"rebaix\w*", r"rebaj\w*", r"cut\w*\s+to", r"junk",
    ],
    "recuperacao_judicial": [
        r"recupera[çc][ãa]o\s+judicial", r"\brj\b", r"chapter\s*11",
        r"concurso\s+mercantil", r"reorganiza[çc][ãa]o\s+judicial",
    ],
    "falencia": [r"fal[êe]nci\w*", r"bankrupt\w*", r"liquida[çc][ãa]o\s+judicial"],
    "default": [r"\bdefault\w*", r"inadimpl\w*", r"n[ãa]o\s+pagamento",
                r"missed\s+payment", r"calote"],
    "reestruturacao_divida": [r"reestrutura\w*\s+(?:da\s+)?d[íi]vida",
                              r"debt\s+restructur\w*"],
    "fusao_aquisicao": [r"fus[ãa]o", r"aquisi[çc][ãa]o", r"merger", r"acquisition",
                        r"incorpora[çc][ãa]o"],
}

# Preposições possessivas que ligam o termo do evento ao dono do evento.
_POSS = r"(?:d[aeo]s?|of|del|de\s+l[ao]s?)"


def _sentences(text: str) -> list[tuple[int, int, str]]:
    """Divide em orações preservando offsets (usa ; e conectivos além de . ! ?)."""
    out, start = [], 0
    for m in re.finditer(r"[.;!?]|\s+(?:e|and|y)\s+atualiza\w*\s+sobre\s+|\s+—\s+", text):
        end = m.start()
        if end > start:
            out.append((start, end, text[start:end]))
        start = m.end()
    if start < len(text):
        out.append((start, len(text), text[start:]))
    return out or [(0, len(text), text)]


def _company_aliases(nome: str, watchlist: list[dict]) -> list[str]:
    c = next((x for x in watchlist if x.get("name") == nome), None)
    return [str(a) for a in ((c or {}).get("aliases") or [nome]) if a]


def attribute_events_by_evidence(titulo: str, resumo: str, event_ids: list[str],
                                 companies: list[str], watchlist: list[dict],
                                 normalize, filer: str = "") -> dict:
    """Resolve empresa × evento × evidência, ESCOPADO POR ORAÇÃO.

    A janela textual nunca atravessa fronteira de oração: em
    "rating rebaixado pela Fitch e atualização sobre plano de RJ da Samarco",
    o termo 'rating' está na oração 1 e 'da Samarco' na oração 2, logo o
    rebaixamento NÃO pode ser atribuído à Samarco.

    Regras, em ordem de precedência (todas dentro da MESMA oração):
      R1  possessivo explícito: "<termo> ... d[ao] <EMPRESA>" / "d[ao] <EMPRESA> ... <termo>"
      R4  empresa única citada na oração
      R3  oração sem terceiro citado → atribui ao EMISSOR do documento (filer)
      R5  fallback global: empresa mais próxima do termo (≤120 chars, mesma oração)
      R0  sem âncora textual → nenhuma atribuição (não pontua)
    """
    texto = f"{titulo} {resumo}".strip()
    orações = _sentences(texto)
    alias_map = {co: _company_aliases(co, watchlist) for co in companies}
    por_evento, sem_evid = {}, []

    for ev in event_ids:
        anchors = EVENT_ANCHORS.get(ev)
        if not anchors:
            sem_evid.append(ev)
            continue
        anchor_rx = re.compile("|".join(anchors), re.I)
        best = None
        for (s0, s1, frase) in orações:
            m = anchor_rx.search(frase)
            if not m:
                continue
            e0, e1 = s0 + m.start(), s0 + m.end()
            # R1 — possessivo dentro da MESMA oração
            for co, aliases in alias_map.items():
                for al in aliases:
                    mm = re.search(rf"{_POSS}\s+{re.escape(al)}\b", frase, re.I)
                    if mm:
                        best = (co, "R1_possessivo_mesma_oracao", "alta",
                                e0, e1, mm.group(0), frase)
                        break
                if best:
                    break
            if best:
                break
            # R4 — uma única empresa citada na oração
            presentes = [co for co, als in alias_map.items()
                         if any(re.search(rf"\b{re.escape(a)}\b", frase, re.I) for a in als)]
            if len(presentes) == 1:
                best = (presentes[0], "R4_empresa_unica_na_oracao", "media",
                        e0, e1, presentes[0], frase)
                break
            # R3 — nenhum terceiro citado na oração: o evento é do próprio emissor
            if not presentes and filer and filer in alias_map:
                best = (filer, "R3_oracao_sem_terceiro_atribui_ao_emissor", "media",
                        e0, e1, f"(emissor do documento: {filer})", frase)
                break
            # R5 — desempate por proximidade DENTRO da oração
            cands = []
            for co, als in alias_map.items():
                for a in als:
                    for mm in re.finditer(rf"\b{re.escape(a)}\b", frase, re.I):
                        d = abs(mm.start() - m.start())
                        if d <= 120:
                            cands.append((d, co, mm.group(0)))
            if cands:
                cands.sort()
                best = (cands[0][1], "R5_proximidade_na_oracao", "baixa",
                        e0, e1, cands[0][2], frase)
                break
        if not best:
            sem_evid.append(ev)
            continue
        co, regra, conf, e0, e1, subj_ev, frase = best
        por_evento[ev] = {
            "subject_company": co,
            "evidence_text": texto[max(0, e0 - 30):min(len(texto), e1 + 50)],
            "evidence_start": e0, "evidence_end": e1,
            "evidence_sentence": frase.strip()[:160],
            "subject_evidence": subj_ev,
            "attribution_rule": regra,
            "attribution_confidence": conf,
        }
    return {"por_evento": por_evento, "sem_evidencia": sem_evid}


def apply_evidence_attribution(art: dict, cfg: dict, normalize) -> dict:
    """Reescreve events_by_company/context_events_by_company do artigo usando
    a atribuição por evidência. Só age quando há ≥2 empresas e ≥2 eventos
    (caso misto) ou quando o papel do artigo divergiria do evento."""
    companies = art.get("companies") or []
    ebc = art.get("events_by_company") or {}
    ctx = art.get("context_events_by_company") or {}
    todos = sorted({e for evs in ebc.values() for e in (evs or [])}
                   | {e.get("event_id") for evs in ctx.values() for e in (evs or [])}
                   - {None})
    if len(companies) < 2 or len(todos) < 1:
        return {"aplicado": False, "detalhe": []}
    res = attribute_events_by_evidence(art.get("title", ""), art.get("summary", ""),
                                       todos, companies, cfg.get("watchlist", []),
                                       normalize,
                                       filer=(art.get("filing_company") or ""))
    por_ev = res["por_evento"]
    if not por_ev:
        return {"aplicado": False, "detalhe": []}
    novo_ebc = {co: [] for co in companies}
    novo_ctx = {}
    detalhe = []
    for ev, info in por_ev.items():
        dono = info["subject_company"]
        if dono in novo_ebc:
            novo_ebc[dono].append(ev)
        # as OUTRAS empresas citadas recebem o evento como CONTEXTO, não score
        for co in companies:
            if co == dono:
                continue
            novo_ctx.setdefault(co, []).append({
                "event_id": ev, "event_label": ev,
                "subject_company": dono,
                "relation_type": "evento_de_terceiro_por_evidencia",
                "impact_type": "indireto_material",
                "event_scope": "indireto", "event_phase": "",
                "direction": "neutra", "scoreable": False,
                "attribution_confidence": info["attribution_confidence"],
                "attribution_evidence": info["evidence_text"][:110],
            })
        detalhe.append({"event_id": ev, **info})
    # eventos sem evidência não pontuam para ninguém
    for ev in res["sem_evidencia"]:
        detalhe.append({"event_id": ev, "subject_company": "",
                        "attribution_rule": "R0_sem_ancora_textual",
                        "attribution_confidence": "nenhuma",
                        "evidence_text": "", "evidence_start": -1, "evidence_end": -1,
                        "evidence_sentence": "", "subject_evidence": ""})
    art["events_by_company"] = {k: v for k, v in novo_ebc.items()}
    if novo_ctx:
        art["context_events_by_company"] = novo_ctx
    art["event_attribution_evidence"] = detalhe
    art["companies_attributed"] = [c for c, v in novo_ebc.items() if v]
    return {"aplicado": True, "detalhe": detalhe}


# ───────────────── gate de runtime: shadow dentro da execução normal ─────────
def _sha(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return "AUSENTE"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict], keys: list[str] | None = None):
    keys = keys or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def run_edgar_runtime_shadow(edgar_articles: list[dict], cfg: dict, rd,
                             *, history_snapshot: dict | None = None,
                             outdir: str = ".",
                             watch_files: list[str] | None = None) -> dict:
    """CASO B: coleta ON + scoring OFF. Classifica em sombra e grava artifacts.

    NÃO adiciona nada a production_articles, NÃO chama merge_into_history, NÃO
    altera score/feed/build_changes/HTML. Recebe cópia independente."""
    outp = Path(outdir)
    outp.mkdir(parents=True, exist_ok=True)
    watch = watch_files or ["risk_history.json", "config_risco.yaml", "index.html"]
    antes = {f: _sha(f) for f in watch}

    arts = copy.deepcopy(edgar_articles)          # blindagem extra
    classific, contexto, score_sim, fp, dedup = [], [], [], [], []
    hist_recs = ((history_snapshot or {}).get("articles") or {})
    idx = {}
    for url, rec in hist_recs.items():
        for co, evs in (rec.get("events_by_company") or {}).items():
            for ev in (evs or []):
                idx.setdefault((rd.normalize(co), ev), []).append(
                    {"url": url, "source": rec.get("source", "")})

    tax = {e.get("id"): e for e in (cfg.get("taxonomy") or [])}
    vistos = set()
    total_sim = 0
    for art in arts:
        try:
            rd.translate_articles([art], cfg)
        except Exception:
            pass
        rd.classify_and_attribute(art, cfg)
        apply_evidence_attribution(art, cfg, rd.normalize)
        filer = art.get("filing_company") or ""
        acc = art.get("accession_number") or ""
        ebc = art.get("events_by_company") or {}
        ctx = art.get("context_events_by_company") or {}
        evid = {d["event_id"]: d for d in (art.get("event_attribution_evidence") or [])}
        com_evento = any(v for v in ebc.values())
        for co, evs in ebc.items():
            for ev in (evs or []):
                d = evid.get(ev, {})
                sc = (tax.get(ev) or {}).get("score", 0) or 0
                total_sim += sc
                classific.append({
                    "filing_company": filer, "subject_company": co,
                    "form": art.get("form", ""), "filing_date": art.get("filing_date", ""),
                    "accession_number": acc,
                    "primary_document": art.get("primary_document", ""),
                    "title": (art.get("title") or "")[:170],
                    "event_id": ev,
                    "attribution_rule": d.get("attribution_rule", ""),
                    "attribution_confidence": d.get("attribution_confidence", ""),
                    "evidence_text": (d.get("evidence_text") or "")[:120],
                    "evidence_sentence": (d.get("evidence_sentence") or "")[:140],
                    "score_simulado": sc,
                    "pontuou_em_producao": "não (shadow: edgar_scoring_enabled=false)",
                })
                score_sim.append({
                    "filing_company": filer, "subject_company": co, "event_id": ev,
                    "accession_number": acc,
                    "severidade": (tax.get(ev) or {}).get("severity", ""),
                    "score_taxonomia": sc, "score_simulado": sc,
                    "aplicado_em_producao": "não",
                })
                if sc > 5:
                    fp.append({
                        "filing_company": filer, "subject_company": co, "event_id": ev,
                        "accession_number": acc, "form": art.get("form", ""),
                        "title": (art.get("title") or "")[:150],
                        "attribution_rule": d.get("attribution_rule", ""),
                        "evidence_text": (d.get("evidence_text") or "")[:110] or "(vazia)",
                        "revisao_necessaria": "sim (score>5)",
                        "risco": ("filer≠sujeito" if co != filer else "verificar evidência"),
                    })
        if not com_evento:
            classific.append({
                "filing_company": filer, "subject_company": "",
                "form": art.get("form", ""), "filing_date": art.get("filing_date", ""),
                "accession_number": acc,
                "primary_document": art.get("primary_document", ""),
                "title": (art.get("title") or "")[:170], "event_id": "",
                "attribution_rule": "", "attribution_confidence": "",
                "evidence_text": "", "evidence_sentence": "",
                "score_simulado": 0,
                "pontuou_em_producao": "não (filing informativo/contextual)",
            })
        for co, evs in ctx.items():
            for e in (evs or []):
                contexto.append({
                    "filing_company": filer, "card_company": co,
                    "subject_company": e.get("subject_company", ""),
                    "event_id": e.get("event_id"),
                    "relation_type": e.get("relation_type"),
                    "impact_type": e.get("impact_type"),
                    "scoreable": e.get("scoreable", False),
                    "accession_number": acc,
                    "attribution_evidence": (e.get("attribution_evidence") or "")[:110],
                })
        dup = acc in vistos
        vistos.add(acc)
        primeiro_ev = next((ev for evs in ebc.values() for ev in (evs or [])), "")
        match = idx.get((rd.normalize(filer), primeiro_ev), []) if primeiro_ev else []
        dedup.append({
            "accession_number": acc, "filing_company": filer,
            "event_id": primeiro_ev or "—",
            "occurrence_id": f"{rd.normalize(filer)}|{primeiro_ev}" if primeiro_ev else "—",
            "matched_existing_occurrence": "sim" if match else "não",
            "matched_url": (match[0]["url"] if match else ""),
            "matched_source": (match[0]["source"] if match else ""),
            "acao": ("descartar_duplicata_accession" if dup else
                     ("corroborar_ocorrencia_existente" if match else
                      ("nova_ocorrencia_potencial" if primeiro_ev else "sem_evento_nao_pontua"))),
            "score_incremental": 0,
            "justificativa": "shadow mode: nenhum score aplicado em produção",
        })

    depois = {f: _sha(f) for f in watch}
    mudou = {f: (antes[f] != depois[f]) for f in watch}
    meta = {
        "collection_enabled": rd.edgar_collection_enabled(cfg),
        "scoring_enabled": rd.edgar_scoring_enabled(cfg),
        "collection_mode": "shadow",
        "filings_collected": len(edgar_articles),
        "filings_classified": len(arts),
        "filings_with_event": sum(1 for a in arts
                                  if any(v for v in (a.get("events_by_company") or {}).values())),
        "filings_contextual": sum(1 for a in arts if (a.get("context_events_by_company") or {})),
        "simulated_score_total": total_sim,
        "persisted_records": 0,
        "history_changed": bool(mudou.get("risk_history.json", False)),
        "dashboard_changed": bool(mudou.get("index.html", False)),
        "backfill": False,
        "run_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hashes_antes": antes, "hashes_depois": depois,
    }

    _write_csv(outp / "edgar_runtime_shadow_classification.csv", classific,
               keys=["filing_company", "subject_company", "form", "filing_date",
                     "accession_number", "primary_document", "title", "event_id",
                     "attribution_rule", "attribution_confidence", "evidence_text",
                     "evidence_sentence", "score_simulado", "pontuou_em_producao"])
    _write_csv(outp / "edgar_runtime_shadow_context_events.csv", contexto,
               keys=["filing_company", "card_company", "subject_company", "event_id",
                     "relation_type", "impact_type", "scoreable", "accession_number",
                     "attribution_evidence"])
    _write_csv(outp / "edgar_runtime_shadow_score_simulation.csv", score_sim,
               keys=["filing_company", "subject_company", "event_id", "accession_number",
                     "severidade", "score_taxonomia", "score_simulado", "aplicado_em_producao"])
    _write_csv(outp / "edgar_runtime_shadow_false_positive_review.csv", fp,
               keys=["filing_company", "subject_company", "event_id", "accession_number",
                     "form", "title", "attribution_rule", "evidence_text",
                     "revisao_necessaria", "risco"])
    _write_csv(outp / "edgar_runtime_shadow_deduplication.csv", dedup,
               keys=["accession_number", "filing_company", "event_id", "occurrence_id",
                     "matched_existing_occurrence", "matched_url", "matched_source",
                     "acao", "score_incremental", "justificativa"])
    (outp / "edgar_runtime_shadow_run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = (meta["persisted_records"] == 0 and not meta["history_changed"]
          and not meta["dashboard_changed"] and not meta["backfill"])
    print(f"   {'✅' if ok else '❌'} shadow runtime: {len(arts)} filing(s), "
          f"score simulado={total_sim}, persistidos=0, "
          f"histórico_alterado={meta['history_changed']}, "
          f"dashboard_alterado={meta['dashboard_changed']}")
    return meta
