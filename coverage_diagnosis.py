# -*- coding: utf-8 -*-
"""Fase 4H.2 — Cobertura oficial e diagnóstico de ausência de notícias.

ESCOPO: este módulo é PURO DIAGNÓSTICO/TELEMETRIA. Ele responde, por emissor
e por execução: "a coleta rodou e não achou nada" é uma situação MUITO
diferente de "a coleta nunca tentou" ou "a coleta tentou e falhou" — e ambas
são diferentes de "achou notícia, mas nada pontuável". Nada aqui pontua,
nada aqui escreve em `events_by_company`/`context_events_by_company`, e
nada aqui é lido por `event_ids_for`/`build_evolution` (ver invariante 5 e
16 do CLAUDE.md). O objetivo é 100% auditoria — nunca inventar cobertura.

Este módulo NÃO cria um sistema de telemetria paralelo: ele CONSOME a
telemetria real já produzida pelo pipeline de produção
(`risk_dashboard._SEARCH_TELEMETRY` → persistido em
`run_meta["international_search_execution"]`, e
`risk_dashboard._OFFICIAL_SOURCE_TELEMETRY` → persistido em
`run_meta["official_source_execution"]`), mais o resultado de
`audit_cvm_coverage` (status de filiação CVM/IPE por emissor brasileiro).
Não faz nenhuma chamada de rede nova — o diagnóstico retroativo lê
`run_meta.json`/`international_search_history.json` já existentes no
repositório.

Os 7 status de cobertura (mutuamente exclusivos, avaliados em ordem de
prioridade — ver `classify_company_coverage`):

  NO_VALIDATED_OFFICIAL_SOURCE
      Nenhuma fonte oficial (RI RSS/página de notícias de RI, SEC/EDGAR
      elegível, CVM/IPE filiante) está configurada/validada para este
      emissor. Independe do resultado da busca genérica.

  SOURCE_CONFIGURED_NOT_EXECUTED
      Há fonte(s) configurada(s) mas NENHUMA foi sequer tentada nesta
      execução (ex.: emissor fora do bucket de rotação Tier 2/3 deste
      ciclo — "configurado ≠ executado").

  COLLECTION_FAILURE
      Houve tentativa(s) real(is), mas TODAS falharam tecnicamente (erro
      de rede/parsing/timeout) — nenhuma fonte teve sucesso técnico nesta
      execução.

  PARTIAL_COVERAGE
      Parte das fontes configuradas rodou com sucesso técnico nesta
      execução; outra parte não foi tentada e/ou falhou. Cobertura mista,
      não pode ser lida como "cobertura completa silenciosa".

  FALLBACK_ONLY
      Todas as fontes tentadas tiveram sucesso técnico; fonte(s) oficial
      (is) configurada(s) devolveu(ram) ZERO itens; o único conteúdo
      encontrado veio do fallback de busca genérica (Google News).

  ONLY_INFORMATIONAL_FOUND
      Cobertura rodou com sucesso e retornou item(ns), mas nenhum virou
      evento pontuável (`eventos_classificados == 0`) — notícia existe,
      não há sinal de risco pontuável nela.

  NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN
      Cobertura rodou com sucesso técnico em TODAS as fontes tentadas e
      configuradas, e RESULTADO REAL foi zero itens em todas elas. Esta é
      a única situação que pode ser lida como "não há risco novo" — as
      outras seis dizem "não sabemos" em algum grau.

Um oitavo rótulo interno, `COVERAGE_OK_EVENTS_FOUND`, existe apenas para
não forçar um emissor com evento real classificado nesta execução dentro de
um dos 7 status de AUSÊNCIA (ele não é "ausência de notícia" — é cobertura
normal com sinal). Não faz parte dos 7 status pedidos; é reportado à parte.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

# ── Constantes de status (nomenclatura alinhada ao pedido da Fase 4H.2) ──
NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN = "NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN"
ONLY_INFORMATIONAL_FOUND = "ONLY_INFORMATIONAL_FOUND"
PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
SOURCE_CONFIGURED_NOT_EXECUTED = "SOURCE_CONFIGURED_NOT_EXECUTED"
COLLECTION_FAILURE = "COLLECTION_FAILURE"
NO_VALIDATED_OFFICIAL_SOURCE = "NO_VALIDATED_OFFICIAL_SOURCE"
FALLBACK_ONLY = "FALLBACK_ONLY"
# rótulo interno, fora dos 7 pedidos — cobertura normal, com evento real.
COVERAGE_OK_EVENTS_FOUND = "COVERAGE_OK_EVENTS_FOUND"

COVERAGE_STATUSES = (
    NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN,
    ONLY_INFORMATIONAL_FOUND,
    PARTIAL_COVERAGE,
    SOURCE_CONFIGURED_NOT_EXECUTED,
    COLLECTION_FAILURE,
    NO_VALIDATED_OFFICIAL_SOURCE,
    FALLBACK_ONLY,
)

_STATUS_LABEL_PT = {
    NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN: "Sem notícia relevante após execução bem-sucedida",
    ONLY_INFORMATIONAL_FOUND: "Só notícia informativa (sem evento pontuável)",
    PARTIAL_COVERAGE: "Cobertura parcial",
    SOURCE_CONFIGURED_NOT_EXECUTED: "Fonte configurada, não executada neste ciclo",
    COLLECTION_FAILURE: "Falha de coleta",
    NO_VALIDATED_OFFICIAL_SOURCE: "Sem fonte oficial validada",
    FALLBACK_ONLY: "Só fallback (Google News genérico)",
    COVERAGE_OK_EVENTS_FOUND: "Cobertura normal — evento real encontrado",
}


def status_label(status: str) -> str:
    return _STATUS_LABEL_PT.get(status, status)


# ── Fontes conhecidas do pipeline ─────────────────────────────────────────
_OFFICIAL_SOURCE_NAMES = ("RI_RSS", "RI_NEWS", "EDGAR", "REGULADOR_LOCAL")


def official_sources_configured(company: dict) -> dict:
    """Quais fontes oficiais estão CONFIGURADAS para este emissor (não diz
    nada sobre execução — só sobre o que existe no cadastro)."""
    off = company.get("official") or {}
    has_ri_rss = bool(company.get("ri_feeds")) or bool(off.get("rss"))
    has_ri_news = bool(off.get("news"))
    is_brasil = (company.get("country") or "").strip().lower() == "brasil"
    try:
        from risk_dashboard import edgar_eligible
        is_edgar = bool(edgar_eligible(company))
    except Exception:
        is_edgar = bool(company.get("cik")) or bool(off.get("sec"))
    return {
        "RI_RSS": has_ri_rss,
        "RI_NEWS": has_ri_news,
        "EDGAR": is_edgar,
        "REGULADOR_LOCAL": is_brasil,
    }


def _source_record(name, configured, attempted, technical_success, items_found,
                   validated=None, note=""):
    return {
        "source": name,
        "configured": bool(configured),
        "attempted": bool(attempted),
        "technical_success": bool(technical_success) if attempted else False,
        "items_found": int(items_found or 0),
        "validated": bool(validated) if validated is not None else bool(configured),
        "note": note,
    }


def build_source_records(company: dict, search_tel: dict | None,
                         official_tel_map: dict, cvm_status: str | None = None) -> list:
    """Monta a "foto" por fonte para um emissor, numa única execução, a
    partir da telemetria REAL já produzida por `risk_dashboard.py`
    (`search_tel` = `run_meta["international_search_execution"][nome]`,
    `official_tel_map` = `run_meta["official_source_execution"]`)."""
    name = company["name"]
    cfg_sources = official_sources_configured(company)
    records = []

    # Google News / busca genérica — SEMPRE "configurada" (todo emissor tem
    # ao menos uma query construída por `build_company_queries`).
    if search_tel is None:
        records.append(_source_record("GNEWS", True, False, False, 0,
                                       validated=False,
                                       note="Emissor fora do bucket desta execução "
                                            "(sem entrada em international_search_execution)."))
    else:
        attempted = bool(search_tel.get("searched"))
        queries = int(search_tel.get("queries", 0) or 0)
        success = int(search_tel.get("success", 0) or 0)
        raw_articles = int(search_tel.get("raw_articles", 0) or 0)
        # sucesso técnico = pelo menos uma query respondeu tecnicamente OK;
        # HTTP 200 sem artigo extraído ainda conta como sucesso TÉCNICO —
        # a distinção "retornou item relevante" fica em items_found, não
        # aqui (mesma separação de `link_debt_audit`: 200 != resolvido).
        technical_success = attempted and (queries == 0 or success > 0)
        records.append(_source_record("GNEWS", True, attempted, technical_success,
                                       raw_articles, validated=False))

    for src_name in _OFFICIAL_SOURCE_NAMES:
        configured = cfg_sources.get(src_name, False)
        if src_name == "REGULADOR_LOCAL":
            attempted = cvm_status is not None
            validated = cvm_status == "filiante_cvm"
            technical_success = attempted  # auditoria CVM roda em lote; se
            # rodou e classificou o emissor, foi sucesso técnico para ele.
            records.append(_source_record(src_name, configured, attempted,
                                           technical_success, 0, validated=validated))
            continue
        tel = (official_tel_map.get(src_name) or {}).get(name)
        if tel is None:
            records.append(_source_record(src_name, configured, False, False, 0,
                                           validated=configured))
            continue
        attempted = bool(tel.get("attempted"))
        technical_success = bool(tel.get("success"))
        items_found = int(tel.get("items_found", 0) or tel.get("filings_found", 0) or 0)
        records.append(_source_record(src_name, configured, attempted, technical_success,
                                       items_found, validated=configured))
    return records


def classify_company_coverage(company: dict, search_tel: dict | None,
                              official_tel_map: dict, cvm_status: str | None = None,
                              scored_events: int = 0) -> dict:
    """Classifica UM emissor, numa execução, num dos status de cobertura.

    `scored_events` é `eventos_classificados` da telemetria de busca
    (contagem, não os eventos em si) — usado só para diferenciar
    ONLY_INFORMATIONAL_FOUND de cobertura normal com sinal real. Este
    número NUNCA é escrito de volta em `events_by_company`/score; é lido,
    não produzido, por este módulo."""
    sources = build_source_records(company, search_tel, official_tel_map, cvm_status)
    official_records = [s for s in sources if s["source"] != "GNEWS"]
    official_configured = [s for s in official_records if s["configured"]]

    all_configured = [s for s in sources if s["configured"]]
    attempted = [s for s in all_configured if s["attempted"]]
    not_attempted = [s for s in all_configured if not s["attempted"]]
    failed = [s for s in attempted if not s["technical_success"]]
    succeeded = [s for s in attempted if s["technical_success"]]

    items_total = sum(s["items_found"] for s in succeeded)
    official_items = sum(s["items_found"] for s in succeeded if s["source"] != "GNEWS")

    reasons = []
    if not official_configured:
        status = NO_VALIDATED_OFFICIAL_SOURCE
        reasons.append("Nenhuma fonte oficial (RI RSS/página de notícias, SEC/EDGAR, "
                       "CVM/IPE filiante) configurada ou validada para este emissor.")
    elif not attempted:
        status = SOURCE_CONFIGURED_NOT_EXECUTED
        cfgd = ", ".join(s["source"] for s in not_attempted)
        reasons.append(f"Fonte(s) configurada(s) ({cfgd}) não foram tentadas nesta "
                       f"execução (rotação de tier ou emissor fora do ciclo).")
    elif failed and not succeeded:
        status = COLLECTION_FAILURE
        errd = ", ".join(s["source"] for s in failed)
        reasons.append(f"Todas as fontes tentadas ({errd}) falharam tecnicamente "
                       f"nesta execução (erro de rede/parsing/timeout).")
    elif not_attempted or failed:
        status = PARTIAL_COVERAGE
        ok = ", ".join(s["source"] for s in succeeded)
        bad = ", ".join(s["source"] for s in (not_attempted + failed))
        reasons.append(f"Cobertura mista: {ok or '—'} rodou(aram) com sucesso; "
                       f"{bad} não foi(ram) tentada(s) ou falhou(aram) nesta execução.")
    elif official_items == 0 and items_total > 0:
        status = FALLBACK_ONLY
        reasons.append("Fonte(s) oficial(is) configurada(s) e tentada(s) com sucesso "
                       "técnico, mas devolveram 0 itens; único conteúdo veio do "
                       "fallback de busca genérica (Google News).")
    elif items_total == 0:
        status = NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN
        reasons.append("Todas as fontes configuradas foram tentadas com sucesso "
                       "técnico e nenhuma retornou item nesta execução.")
    elif scored_events == 0:
        status = ONLY_INFORMATIONAL_FOUND
        reasons.append(f"{items_total} item(ns) encontrado(s), 0 evento(s) pontuável(is) "
                       f"classificado(s) nesta execução.")
    else:
        status = COVERAGE_OK_EVENTS_FOUND
        reasons.append(f"{items_total} item(ns) encontrado(s), {scored_events} "
                       f"evento(s) pontuável(is) classificado(s) — cobertura normal.")

    return {
        "company": company["name"],
        "tier": company.get("tier"),
        "country": company.get("country"),
        "coverage_status": status,
        "coverage_status_label": status_label(status),
        "reasons": reasons,
        "sources": sources,
    }


# ── Lista priorizada (Tier 1 + Peru + subsidiárias Coazucar) ─────────────
_PERU_ONBOARDING = ("Yura", "Trupal", "Coazucar", "Yobel")


def priority_companies(cfg: dict) -> list:
    """Emissores priorizados pela Fase 4H.2: todo o Tier 1 (lido do YAML
    real, não hardcoded), os 4 candidatos peruanos já integrados em
    produção, e as subsidiárias relacionadas da Coazucar (sem transferência
    automática de score — mesma regra já estabelecida em `related_entities`
    / `fetch_related_entities_context`)."""
    watch = cfg.get("watchlist", [])
    out = []
    seen = set()
    for c in watch:
        if c.get("tier") == 1 and c["name"] not in seen:
            out.append(c)
            seen.add(c["name"])
    for name in _PERU_ONBOARDING:
        for c in watch:
            if c["name"] == name and c["name"] not in seen:
                out.append(c)
                seen.add(c["name"])
    # subsidiárias Coazucar: não são watchlist própria (não têm score
    # próprio, nunca tiveram) — representadas como "pseudo-emissores" só
    # para fins de diagnóstico de cobertura, marcados `is_subsidiary=True`.
    for c in watch:
        if c["name"] == "Coazucar":
            for rel in c.get("related_entities", []) or []:
                pseudo = {
                    "name": rel.get("entity_name") or rel.get("legal_name"),
                    "tier": c.get("tier"),
                    "country": c.get("country"),
                    "official": {},
                    "is_subsidiary": True,
                    "parent_company": "Coazucar",
                }
                if pseudo["name"] and pseudo["name"] not in seen:
                    out.append(pseudo)
                    seen.add(pseudo["name"])
    return out


def diagnose_coverage(cfg: dict, run_meta: dict, cvm_status_map: dict | None = None,
                      companies: list | None = None) -> list:
    """Diagnóstico retroativo: classifica cada emissor da lista priorizada
    (ou de `companies`, se fornecida) com base na telemetria REAL de UMA
    execução (`run_meta`). Subsidiárias Coazucar não têm fonte oficial
    própria cadastrada nem telemetria de busca dedicada no pipeline atual —
    são reportadas como NO_VALIDATED_OFFICIAL_SOURCE por construção, o que
    é o estado real e auditável (nenhuma fonte RI/EDGAR/CVM está cadastrada
    para elas; não há transferência de cobertura ou score da holding)."""
    companies = companies if companies is not None else priority_companies(cfg)
    search_map = run_meta.get("international_search_execution") or {}
    official_map = run_meta.get("official_source_execution") or {}
    cvm_status_map = cvm_status_map or {}

    rows = []
    for c in companies:
        name = c["name"]
        search_tel = search_map.get(name)
        cvm_status = cvm_status_map.get(name)
        scored = int((search_tel or {}).get("eventos_classificados", 0) or 0)
        rec = classify_company_coverage(c, search_tel, official_map, cvm_status, scored)
        rec["is_subsidiary"] = bool(c.get("is_subsidiary"))
        rec["parent_company"] = c.get("parent_company", "")
        rows.append(rec)
    return rows


# ── Exports de auditoria ──────────────────────────────────────────────────
def export_coverage_csv(rows: list, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "tier", "country", "is_subsidiary", "parent_company",
                   "coverage_status", "coverage_status_label", "reasons"])
        for r in rows:
            w.writerow([r["company"], r.get("tier", ""), r.get("country", ""),
                       r.get("is_subsidiary", False), r.get("parent_company", ""),
                       r["coverage_status"], r["coverage_status_label"],
                       " | ".join(r["reasons"])])
    return path


def export_source_csv(rows: list, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "coverage_status", "source", "configured", "attempted",
                   "technical_success", "items_found", "validated", "note"])
        for r in rows:
            for s in r["sources"]:
                w.writerow([r["company"], r["coverage_status"], s["source"],
                           s["configured"], s["attempted"], s["technical_success"],
                           s["items_found"], s["validated"], s["note"]])
    return path


def summarize_status_counts(rows: list) -> dict:
    counts = {s: 0 for s in COVERAGE_STATUSES}
    counts[COVERAGE_OK_EVENTS_FOUND] = 0
    for r in rows:
        counts[r["coverage_status"]] = counts.get(r["coverage_status"], 0) + 1
    return counts


# ── Diagnóstico retroativo a partir dos artefatos reais do repositório ───
def run_retroactive_diagnosis(cfg_path: str = "config_risco.yaml",
                              run_meta_path: str = "run_meta.json",
                              out_dir: str = "out_coverage_diagnosis",
                              run_cvm_audit: bool = False) -> dict:
    """Roda o diagnóstico sobre os artefatos JÁ existentes no repositório
    (`run_meta.json` da última execução real registrada) — SEM nenhuma
    coleta de rede nova. `run_cvm_audit=True` é opt-in explícito (dataset
    IPE/CVM é rede) e só deve ser usado quando estritamente necessário;
    por padrão fica False e o status CVM entra como desconhecido (o que só
    pode reforçar NO_VALIDATED_OFFICIAL_SOURCE — nunca infla cobertura)."""
    import yaml
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(run_meta_path, encoding="utf-8") as f:
        run_meta = json.load(f)

    cvm_status_map = {}
    if run_cvm_audit:
        try:
            from risk_dashboard import audit_cvm_coverage
            cvm_rows = audit_cvm_coverage(cfg)
            for row in cvm_rows:
                cvm_status_map[row.get("emissor") or row.get("company")] = row.get("status")
        except Exception:
            cvm_status_map = {}

    rows = diagnose_coverage(cfg, run_meta, cvm_status_map)
    counts = summarize_status_counts(rows)

    os.makedirs(out_dir, exist_ok=True)
    coverage_csv = export_coverage_csv(rows, os.path.join(out_dir, "coverage_status_by_company.csv"))
    source_csv = export_source_csv(rows, os.path.join(out_dir, "coverage_status_by_source.csv"))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_meta_generated_at": run_meta.get("generated_at"),
        "run_meta_run_finished_at": run_meta.get("run_finished_at"),
        "companies_diagnosed": len(rows),
        "status_counts": counts,
        "coverage_csv": coverage_csv,
        "source_csv": source_csv,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    return {"rows": rows, "summary": summary}
