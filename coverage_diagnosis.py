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
import hashlib
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


# Frases de UI exigidas pela Fase 4H.2 (dashboard/resumo executivo) — texto
# curto, para leigo, deliberadamente DIFERENTE do rótulo técnico acima (que
# fica em `reasons`/CSV para auditoria). Nunca usa a palavra "risco".
_STATUS_UI_PHRASE = {
    NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN: "Cobertura confirmada, sem evento relevante",
    ONLY_INFORMATIONAL_FOUND: "Notícias encontradas, apenas informativas",
    PARTIAL_COVERAGE: "Cobertura parcial",
    SOURCE_CONFIGURED_NOT_EXECUTED: "Fonte configurada, ainda não executada",
    COLLECTION_FAILURE: "Falha de coleta",
    NO_VALIDATED_OFFICIAL_SOURCE: "Sem fonte oficial validada",
    FALLBACK_ONLY: "Somente fontes complementares",
    COVERAGE_OK_EVENTS_FOUND: "Cobertura confirmada, evento relevante encontrado",
}


def status_ui_phrase(status: str) -> str:
    return _STATUS_UI_PHRASE.get(status, status)


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
                   validated=None, note="", source_type="", method="",
                   last_attempt="", last_success="", latest_item_date="",
                   latest_item_title="", error="", link=""):
    return {
        "source": name,
        "source_type": source_type,
        "configured": bool(configured),
        "attempted": bool(attempted),
        "technical_success": bool(technical_success) if attempted else False,
        "items_found": int(items_found or 0),
        "validated": bool(validated) if validated is not None else bool(configured),
        "note": note,
        "method": method,
        "last_attempt": last_attempt,
        "last_success": last_success,
        "latest_item_date": latest_item_date,
        "latest_item_title": latest_item_title,
        "error": error,
        "link": link,
    }


# ── 4H.2b — Telemetria CVM REAL por emissor (não mais um dict vazio) ─────
# Construída a partir da saída REAL de `risk_dashboard.audit_cvm_coverage()`
# (dataset IPE/CVM baixado uma vez, casado INDIVIDUALMENTE por emissor via
# identificador forte — código CVM/CNPJ/razão social/nome — nunca por cópia
# do resultado agregado). O método é honesto sobre sua própria natureza:
# é um cruzamento em lote contra um dataset anual, não uma consulta viva
# por-RUC (a CVM não expõe essa API); por isso `metodo_consulta` diz
# explicitamente "ipe_dataset_match_por_identificador_forte" — nunca finge
# ser uma chamada individual em tempo real.
def build_cvm_telemetry(cvm_audit_rows: list, generated_at: str | None = None) -> dict:
    """Transforma as linhas de `audit_cvm_coverage()` (uma por emissor
    brasileiro, já casada individualmente) no formato de telemetria por
    fonte exigido pela Fase 4H.2: company_id, company_name, source_type,
    source_name, identificador cadastral usado, tentativa realizada,
    resultado da consulta, documentos retornados/aceitos, datas, erro,
    última tentativa/sucesso, latência, método."""
    ts = generated_at or datetime.now(timezone.utc).isoformat()
    out = {}
    for row in cvm_audit_rows:
        status = row.get("status", "")
        id_usado = (row.get("identificador_usado") or row.get("codigo_cvm_casado")
                   or row.get("cnpj_casado") or "")
        protocolos = int(row.get("protocolos_no_ano") or 0)
        # "aceito" = casamento com confiança suficiente para contar como
        # filiante confirmado — não é o mesmo que "retornado": um emissor
        # com match ambíguo ('revisar') teve documentos RETORNADOS no
        # cruzamento por nome, mas ZERO são ACEITOS até desambiguação manual.
        aceito_status = status in ("filiante_cvm",)
        documentos_aceitos = protocolos if aceito_status else 0
        erro = ""
        if status == "revisar":
            erro = f"Casamento ambíguo ({row.get('tipo_match','')}): {row.get('motivo_decisao','')[:180]}"
        elif status == "esperado_filiante_sem_protocolo_no_ano":
            erro = ("Identificador forte resolvido, mas 0 protocolo(s) IPE no ano corrente "
                    "— pode ser janela do dataset, não necessariamente ausência de filiação.")
        tentativa = True  # a função iterou este emissor especificamente (alvo = country==Brasil)
        out[row["emissor"]] = {
            "company_id": id_usado or row.get("emissor"),
            "company_name": row["emissor"],
            "source_type": "regulator",
            "source_name": "CVM",
            "identificador_usado": id_usado,
            "identificador_tipo": row.get("tipo_match", ""),
            "tentativa_realizada": tentativa,
            "resultado_consulta": status,
            "documentos_retornados": protocolos,
            "documentos_aceitos": documentos_aceitos,
            "data_ultimo_documento": row.get("ultima_entrega", ""),
            "erro": erro,
            "ultima_tentativa": ts,
            "ultimo_sucesso": ts if documentos_aceitos > 0 else "",
            "latencia_ms": None,  # download do dataset é único e compartilhado
            "latencia_nota": ("download único do dataset IPE compartilhado entre todos os "
                             "emissores brasileiros; latência por emissor não é aplicável ao "
                             "método atual (ver latência do lote no resumo da auditoria)."),
            "metodo_consulta": "ipe_dataset_match_por_identificador_forte",
            "confianca_match": row.get("confianca_match", ""),
            "companhia_casada": row.get("companhia_casada", ""),
            "n_candidatos": row.get("n_candidatos", 0),
        }
    return out


# ── 4H.2c — Validação REAL de fontes oficiais peruanas ───────────────────
# Pesquisa manual feita em 2026-08-05 (WebSearch + WebFetch + curl, sem
# fabricar dado): cada entrada documenta o que foi de fato tentado e o
# resultado técnico observado — inclusive quando o resultado é "não
# encontrado"/"bloqueado". `metodo="manual_research"` é honesto: isto NÃO
# é telemetria automática de uma execução do pipeline — é o levantamento
# que fundamenta a config oficial; a partir daqui, `official_source_execution`
# (RI_RSS/RI_NEWS) passa a poder tentar de verdade nas próximas execuções
# reais do pipeline, uma vez que `official.*` seja preenchido no config.
PERU_SOURCE_VALIDATION_TIMESTAMP = "2026-08-05T00:00:00+00:00"

PERU_SOURCE_VALIDATION = {
    "Yura": [
        {
            "source_name": "Site institucional Yura", "source_type": "official_site",
            "url_configurada": "https://www.yura.com.pe/", "url_final": "https://www.yura.com.pe/",
            "metodo": "manual_research (curl direto: HTTP 200; WebFetch automatizado: HTTP 403)",
            "http_status": 200, "conteudo_validado": False,
            "nota_validacao": "curl com User-Agent de navegador recebe 200; ferramenta de "
                              "extração automática (WebFetch) recebe 403 (bloqueio a bot). "
                              "Página é SPA — nenhum item de notícia/hecho relevante foi "
                              "extraído de fato; NÃO conta como cobertura confirmada.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": True, "bloqueio_tecnico": "403 para user-agent automatizado",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
        {
            "source_name": "BVL — ficha do emissor Yura", "source_type": "exchange",
            "url_configurada": "https://www.bvl.com.pe/en/issuers/detail?companyCode=58501",
            "url_final": "https://www.bvl.com.pe/en/issuers/detail?companyCode=58501",
            "metodo": "manual_research (curl: HTTP 200; WebFetch: conteúdo genérico, "
                     "sem filings/datas extraídos)",
            "http_status": 200, "conteudo_validado": False,
            "nota_validacao": "HTTP 200 não prova conteúdo relevante — página renderiza via "
                              "JS; extração estática só devolveu o nome da bolsa, nenhum "
                              "filing/comunicado individual.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": None, "bloqueio_tecnico": "conteúdo dinâmico (JS), sem API estática",
            "fallback_adotado": "nenhum — candidato mantido para reavaliação com scraper dedicado",
        },
        {
            "source_name": "SMV — Hechos de Importancia", "source_type": "regulator",
            "url_configurada": "A_REVISAR", "url_final": "",
            "metodo": "manual_research (busca no portal SMV)",
            "http_status": None, "conteudo_validado": False,
            "nota_validacao": "O portal público de 'Hechos de Importancia' da SMV usa URLs "
                              "tokenizadas por sessão de busca (parâmetro data=<hash>), não "
                              "uma URL estável por emissor/RUC — não é um alvo de RSS/scrape "
                              "confiável sem automação de formulário (fora do escopo desta fase).",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": False, "bloqueio_tecnico": "URL de resultado não é estável (tokenizada)",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
    ],
    "Trupal": [
        {
            "source_name": "Site institucional Trupal", "source_type": "official_site",
            "url_configurada": "A_REVISAR", "url_final": "",
            "metodo": "manual_research (WebSearch)",
            "http_status": None, "conteudo_validado": False,
            "nota_validacao": "Nenhum site institucional oficial confirmado nesta pesquisa — "
                              "só perfis de terceiros (EMIS, BNamericas, datosperu.org, "
                              "universidadperu.com). Lacuna real, não preenchida artificialmente.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": False, "bloqueio_tecnico": "site oficial não localizado",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
        {
            "source_name": "SMV — Hechos de Importancia", "source_type": "regulator",
            "url_configurada": "A_REVISAR", "url_final": "",
            "metodo": "manual_research (busca no portal SMV)",
            "http_status": None, "conteudo_validado": False,
            "nota_validacao": "Mesmo bloqueio estrutural do caso Yura (URL tokenizada por "
                              "sessão); nenhum documento específico de Trupal localizado.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": False, "bloqueio_tecnico": "URL de resultado não é estável (tokenizada)",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
    ],
    "Coazucar": [
        {
            "source_name": "Site institucional Coazucar", "source_type": "official_site",
            "url_configurada": "https://coazucar.com/", "url_final": "https://coazucar.com/",
            "metodo": "manual_research (WebFetch)",
            "http_status": 200, "conteudo_validado": False,
            "nota_validacao": "Página carregada com sucesso técnico (200), mas contém só "
                              "logo/banner/seletor de idioma — SEM seção de notícias/RI/"
                              "hechos de importancia. Caso de referência para 'homepage "
                              "genérica rejeitada como cobertura suficiente'.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": True, "bloqueio_tecnico": "",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
        {
            "source_name": "SMV — Ofício/registro relacionado a Coazucar", "source_type": "regulator",
            "url_configurada": "https://www.smv.gob.pe/ConsultasP8/temp/Oficio%20N%c2%ba%203112-2026-SMV.pdf",
            "url_final": "https://www.smv.gob.pe/ConsultasP8/temp/Oficio%20N%c2%ba%203112-2026-SMV.pdf",
            "metodo": "manual_research (WebSearch — documento PDF público da SMV que cita Coazucar)",
            "http_status": None, "conteudo_validado": False,
            "nota_validacao": "Evidência de que a SMV tem correspondência/registro envolvendo "
                              "Coazucar (grupo econômico com emissão de bonos), mas NÃO é uma "
                              "página de 'hechos de importancia' estável e assinável como feed "
                              "— não conta como fonte RSS/API configurável nesta fase.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": True, "bloqueio_tecnico": "documento avulso, não é feed/API estável",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
    ],
    "Yobel": [
        {
            "source_name": "Site institucional Yobel (yobelscm.biz)", "source_type": "official_site",
            "url_configurada": "https://www.yobelscm.biz/", "url_final": "",
            "metodo": "manual_research (curl + nslookup contra resolver local e 8.8.8.8)",
            "http_status": None, "conteudo_validado": False,
            "nota_validacao": "Domínio configurado (yobelscm.biz, citado como site oficial em "
                              "buscas) NÃO resolve DNS neste ambiente (NXDOMAIN local; sem "
                              "registro A/AAAA via 8.8.8.8 na consulta realizada) — falha "
                              "técnica real e documentada, não fabricada.",
            "itens_encontrados": 0, "datas": [], "titulos": [],
            "entidade_confirmada": None, "bloqueio_tecnico": "falha de resolução DNS (NXDOMAIN)",
            "fallback_adotado": "Google News genérico (GNEWS)",
        },
    ],
}

# ── 4H.2d — Subsidiárias Coazucar: diagnóstico individual real ───────────
# Nenhuma das 6 tem site institucional, ficha SMV ou ficha BVL PRÓPRIA
# encontrada nesta pesquisa (todas operam sob o guarda-chuva regulatório/
# societário da holding). Isso é reportado como lacuna real — NUNCA como
# herança automática da cobertura/score da holding (ver invariantes).
COAZUCAR_SUBSIDIARY_SOURCE_VALIDATION = {
    name: [{
        "source_name": f"Fonte oficial própria de {name}", "source_type": "official_site",
        "url_configurada": "", "url_final": "",
        "metodo": "manual_research (WebSearch)",
        "http_status": None, "conteudo_validado": False,
        "nota_validacao": (f"Nenhuma fonte oficial própria (site/RI/SMV/BVL) encontrada para "
                          f"'{name}' nesta pesquisa — subsidiária opera sob registro "
                          f"societário/regulatório da holding (Coazucar), sem ficha "
                          f"individual localizável. Cobertura de notícia (quando existir) "
                          f"vem só do contexto de related_entities, nunca de fonte oficial "
                          f"própria nem de score transferido da holding."),
        "itens_encontrados": 0, "datas": [], "titulos": [],
        "entidade_confirmada": False, "bloqueio_tecnico": "fonte própria não localizada",
        "fallback_adotado": "contexto via related_entities da holding (não pontua, não é fonte oficial)",
    }]
    for name in ("Casa Grande S.A.A.", "Cartavio S.A.A.", "Agroindustrias San Jacinto S.A.",
                "Empresa Agrícola Sintuco S.A.", "Agrolmos S.", "Empresa Agraria Chiquitoy S.A.")
}


def _official_status_from_validation(cands: list) -> tuple[bool, int, str, str]:
    """Resume uma lista de candidatos pesquisados manualmente em
    (algum_validado, itens_encontrados_total, ultimo_erro, metodo)."""
    validado = any(c.get("conteudo_validado") for c in cands)
    itens = sum(c.get("itens_encontrados", 0) for c in cands)
    erro = "; ".join(c["bloqueio_tecnico"] for c in cands if c.get("bloqueio_tecnico"))
    metodo = "; ".join(sorted({c.get("metodo", "") for c in cands if c.get("metodo")}))
    return validado, itens, erro, metodo


def build_source_records(company: dict, search_tel: dict | None,
                         official_tel_map: dict, cvm_status: str | None = None,
                         cvm_telemetry: dict | None = None,
                         peru_validation: list | None = None) -> list:
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
            # 4H.2b — telemetria CVM REAL por emissor, quando disponível
            # (`cvm_telemetry[name]`, construída por `build_cvm_telemetry` a
            # partir de `audit_cvm_coverage()`). Cada emissor tem seu PRÓPRIO
            # identificador/protocolos/data — nunca o mesmo resultado global
            # copiado para todos (ver docstring de `build_cvm_telemetry`).
            cvm_rec = (cvm_telemetry or {}).get(name)
            if cvm_rec is not None:
                attempted = bool(cvm_rec.get("tentativa_realizada"))
                # sucesso técnico = a consulta/cruzamento rodou sem erro de
                # coleta (dataset baixado, casamento tentado) — não exige
                # que o resultado seja "filiante"; "revisar"/"não filiante"
                # também são sucesso técnico (resposta definida, não falha).
                technical_success = attempted
                items_found = int(cvm_rec.get("documentos_aceitos") or 0)
                validated = cvm_rec.get("resultado_consulta") == "filiante_cvm"
                records.append(_source_record(
                    src_name, configured, attempted, technical_success, items_found,
                    validated=validated, source_type="regulator",
                    method=cvm_rec.get("metodo_consulta", ""),
                    last_attempt=cvm_rec.get("ultima_tentativa", ""),
                    last_success=cvm_rec.get("ultimo_sucesso", ""),
                    latest_item_date=cvm_rec.get("data_ultimo_documento", ""),
                    error=cvm_rec.get("erro", ""),
                    note=f"identificador={cvm_rec.get('identificador_usado') or '—'} "
                         f"({cvm_rec.get('identificador_tipo') or '—'}); "
                         f"{cvm_rec.get('documentos_retornados', 0)} documento(s) retornado(s), "
                         f"{items_found} aceito(s)."))
                continue
            attempted = cvm_status is not None
            validated = cvm_status == "filiante_cvm"
            technical_success = attempted  # auditoria CVM roda em lote; se
            # rodou e classificou o emissor, foi sucesso técnico para ele.
            records.append(_source_record(src_name, configured, attempted,
                                           technical_success, 0, validated=validated,
                                           source_type="regulator"))
            continue
        # 4H.2c — fontes peruanas com validação manual REAL (RI/notícias
        # corporativas), quando pesquisadas (`peru_validation`). Nunca
        # aceita HTTP 200 sozinho como "retornou item relevante" — só
        # `conteudo_validado=True` (extração real demonstrada) valida.
        # Só RI_NEWS recebe a validação manual peruana (site/BVL) — evita
        # contar o mesmo candidato duas vezes (RI_RSS continua refletindo só
        # feed RSS real, que nenhum dos 4 peruanos tem configurado).
        if src_name == "RI_NEWS" and peru_validation:
            cands = [c for c in peru_validation
                    if c.get("source_type") in ("official_site", "exchange")]
            if cands:
                validado, itens, erro, metodo = _official_status_from_validation(cands)
                links = "; ".join(c.get("url_configurada", "") for c in cands if c.get("url_configurada")
                                  and c["url_configurada"] != "A_REVISAR")
                records.append(_source_record(
                    src_name, configured=bool(links), attempted=True,
                    technical_success=any(c.get("http_status") == 200 for c in cands),
                    items_found=itens, validated=validado, source_type="official_site",
                    method=metodo, error=erro, link=links,
                    last_attempt=PERU_SOURCE_VALIDATION_TIMESTAMP,
                    note="Validação manual (pesquisa dirigida, não telemetria automática de "
                        "execução do pipeline) — ver PERU_SOURCE_VALIDATION."))
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
                              scored_events: int = 0, cvm_telemetry: dict | None = None,
                              peru_validation: list | None = None) -> dict:
    """Classifica UM emissor, numa execução, num dos status de cobertura.

    `scored_events` é `eventos_classificados` da telemetria de busca
    (contagem, não os eventos em si) — usado só para diferenciar
    ONLY_INFORMATIONAL_FOUND de cobertura normal com sinal real. Este
    número NUNCA é escrito de volta em `events_by_company`/score; é lido,
    não produzido, por este módulo.

    `cvm_telemetry`/`peru_validation` são as evidências REAIS (4H.2b/4H.2c)
    — quando ausentes, cai no comportamento anterior (heurística por
    país/config), preservando compatibilidade com os 16 testes da 4H.2
    original."""
    sources = build_source_records(company, search_tel, official_tel_map, cvm_status,
                                   cvm_telemetry=cvm_telemetry, peru_validation=peru_validation)
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
        "coverage_status_ui": status_ui_phrase(status),
        "reasons": reasons,
        "items_total": items_total,
        "scored_events": scored_events,
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
                      companies: list | None = None, cvm_telemetry_map: dict | None = None,
                      peru_validation_map: dict | None = None) -> list:
    """Diagnóstico: classifica cada emissor da lista priorizada (ou de
    `companies`, se fornecida) com base na telemetria REAL de UMA execução
    (`run_meta`), mais a telemetria CVM por emissor (4H.2b,
    `cvm_telemetry_map` — normalmente `build_cvm_telemetry(audit_cvm_coverage(cfg))`)
    e a validação manual de fontes peruanas (4H.2c, default =
    `PERU_SOURCE_VALIDATION` + `COAZUCAR_SUBSIDIARY_SOURCE_VALIDATION`, que
    documentam lacunas REAIS — não preenchem cobertura artificialmente)."""
    companies = companies if companies is not None else priority_companies(cfg)
    search_map = run_meta.get("international_search_execution") or {}
    official_map = run_meta.get("official_source_execution") or {}
    cvm_status_map = cvm_status_map or {}
    cvm_telemetry_map = cvm_telemetry_map or {}
    # default: validação manual real documentada nesta fase, para os 4
    # candidatos peruanos + 6 subsidiárias Coazucar; None (não fornecido)
    # para os demais 150+ emissores — sem inventar dados para eles.
    if peru_validation_map is None:
        peru_validation_map = {}
        peru_validation_map.update(PERU_SOURCE_VALIDATION)
        peru_validation_map.update(COAZUCAR_SUBSIDIARY_SOURCE_VALIDATION)

    rows = []
    for c in companies:
        name = c["name"]
        search_tel = search_map.get(name)
        cvm_status = cvm_status_map.get(name)
        cvm_tel = cvm_telemetry_map.get(name)
        peru_val = peru_validation_map.get(name)
        scored = int((search_tel or {}).get("eventos_classificados", 0) or 0)
        rec = classify_company_coverage(c, search_tel, official_map, cvm_status, scored,
                                        cvm_telemetry={name: cvm_tel} if cvm_tel else None,
                                        peru_validation=peru_val)
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


def to_dashboard_view(rec: dict) -> dict:
    """Converte um registro de `classify_company_coverage`/`diagnose_coverage`
    no formato compacto consumido por `template_risco.html.j2` (seção
    recolhível 'Cobertura das fontes' + resumo executivo). Só leitura —
    nunca escreve de volta em `rec`, nunca toca score."""
    sources = rec["sources"]
    failures = [s for s in sources if s["attempted"] and not s["technical_success"]]
    return {
        "status": rec["coverage_status"],
        "status_ui": rec.get("coverage_status_ui") or status_ui_phrase(rec["coverage_status"]),
        "status_technical_label": rec["coverage_status_label"],
        "reasons": rec["reasons"],
        "last_run": max((s.get("last_attempt") or "" for s in sources), default=""),
        "sources_configured": sum(1 for s in sources if s["configured"]),
        "sources_executed": sum(1 for s in sources if s["attempted"]),
        "sources_success": sum(1 for s in sources if s["technical_success"]),
        "items_found": rec.get("items_total", sum(s["items_found"] for s in sources)),
        "events_found": rec.get("scored_events", 0),
        "fallback_used": rec["coverage_status"] == FALLBACK_ONLY,
        "failures_count": len(failures),
        "is_subsidiary": rec.get("is_subsidiary", False),
        "parent_company": rec.get("parent_company", ""),
        "sources": [
            {
                "name": s["source"],
                "type": s["source_type"] or s["source"],
                "method": s.get("method", ""),
                "status": ("sucesso" if s["technical_success"]
                          else ("falhou" if s["attempted"] else "não tentada")),
                "last_attempt": s.get("last_attempt", ""),
                "last_success": s.get("last_success", ""),
                "items_found": s["items_found"],
                "latest_item_date": s.get("latest_item_date", ""),
                "latest_item_title": s.get("latest_item_title", ""),
                "error": s.get("error", ""),
                "link": s.get("link", ""),
                "note": s.get("note", ""),
            } for s in sources
        ],
    }


def build_executive_coverage_summary(rows: list) -> dict:
    """Área compacta para o resumo executivo do dashboard (item 7 da 4H.2):
    contagens de cobertura confirmada / parcial / falha / só fallback / sem
    fonte oficial — NUNCA reordena o radar de risco principal (é lida à
    parte, não junto do score)."""
    counts = summarize_status_counts(rows)
    return {
        "cobertura_confirmada": counts.get(NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN, 0)
                               + counts.get(COVERAGE_OK_EVENTS_FOUND, 0)
                               + counts.get(ONLY_INFORMATIONAL_FOUND, 0),
        "cobertura_parcial": counts.get(PARTIAL_COVERAGE, 0),
        "falha_de_coleta": counts.get(COLLECTION_FAILURE, 0),
        "somente_fallback": counts.get(FALLBACK_ONLY, 0),
        "sem_fonte_oficial": counts.get(NO_VALIDATED_OFFICIAL_SOURCE, 0),
        "fonte_configurada_nao_executada": counts.get(SOURCE_CONFIGURED_NOT_EXECUTED, 0),
        "total_diagnosticado": len(rows),
        "status_counts": counts,
    }


def summarize_status_counts(rows: list) -> dict:
    counts = {s: 0 for s in COVERAGE_STATUSES}
    counts[COVERAGE_OK_EVENTS_FOUND] = 0
    for r in rows:
        counts[r["coverage_status"]] = counts.get(r["coverage_status"], 0) + 1
    return counts


class ReconciliationError(AssertionError):
    """Levantado quando um export não reconcilia com os totais do payload
    (coordenação 4H.2, item 8: 'adicione assertions que quebrem se não
    reconciliarem'). Propositalmente uma subclasse de AssertionError."""


def export_auditoria_cobertura_emissores_csv(rows: list, path: str) -> str:
    """Uma linha por emissor — status final + contagens agregadas (a fonte
    de verdade para reconciliar com o resumo executivo do dashboard)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "tier", "country", "is_subsidiary", "parent_company",
                   "coverage_status", "coverage_status_ui", "sources_configured",
                   "sources_executed", "sources_success", "items_found", "events_found",
                   "failures_count"])
        for r in rows:
            v = to_dashboard_view(r)
            w.writerow([r["company"], r.get("tier", ""), r.get("country", ""),
                       r.get("is_subsidiary", False), r.get("parent_company", ""),
                       r["coverage_status"], v["status_ui"], v["sources_configured"],
                       v["sources_executed"], v["sources_success"], v["items_found"],
                       v["events_found"], v["failures_count"]])
    return path


def export_auditoria_cobertura_fontes_csv(rows: list, path: str) -> str:
    """Uma linha por (emissor, fonte) — o mesmo detalhe usado na seção
    recolhível do dashboard, para auditoria externa via planilha."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "coverage_status", "source", "source_type", "method",
                   "configured", "attempted", "technical_success", "items_found",
                   "validated", "last_attempt", "last_success", "error", "link", "note"])
        for r in rows:
            for s in r["sources"]:
                w.writerow([r["company"], r["coverage_status"], s["source"],
                           s.get("source_type", ""), s.get("method", ""),
                           s["configured"], s["attempted"], s["technical_success"],
                           s["items_found"], s["validated"], s.get("last_attempt", ""),
                           s.get("last_success", ""), s.get("error", ""), s.get("link", ""),
                           s.get("note", "")])
    return path


def export_fontes_configuradas_vs_executadas_csv(rows: list, path: str) -> str:
    """'Configurado != executado', em forma tabular: por (emissor, fonte),
    se está configurada, se foi tentada, e o gap entre as duas colunas."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "source", "configured", "executed", "gap_configured_not_executed"])
        for r in rows:
            for s in r["sources"]:
                gap = bool(s["configured"] and not s["attempted"])
                w.writerow([r["company"], s["source"], s["configured"], s["attempted"], gap])
    return path


def export_falhas_de_coleta_csv(rows: list, path: str) -> str:
    """Só as tentativas que falharam tecnicamente (attempted=True,
    technical_success=False) — o inverso de 'sucesso silencioso'."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["company", "source", "source_type", "method", "error", "last_attempt", "link"])
        for r in rows:
            for s in r["sources"]:
                if s["attempted"] and not s["technical_success"]:
                    w.writerow([r["company"], s["source"], s.get("source_type", ""),
                               s.get("method", ""), s.get("error", ""),
                               s.get("last_attempt", ""), s.get("link", "")])
    return path


def export_relatorio_cobertura_oficial_md(rows: list, summary: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    exec_sum = build_executive_coverage_summary(rows)
    lines = [
        "# Relatório de cobertura oficial (Fase 4H.2)", "",
        f"Gerado em: {summary.get('generated_at', '')}",
        f"Execução de referência (run_meta): {summary.get('run_meta_run_finished_at', '—')}", "",
        "## Resumo executivo", "",
        f"- Cobertura confirmada: **{exec_sum['cobertura_confirmada']}**",
        f"- Cobertura parcial: **{exec_sum['cobertura_parcial']}**",
        f"- Falha de coleta: **{exec_sum['falha_de_coleta']}**",
        f"- Somente fallback: **{exec_sum['somente_fallback']}**",
        f"- Sem fonte oficial validada: **{exec_sum['sem_fonte_oficial']}**",
        f"- Fonte configurada, não executada: **{exec_sum['fonte_configurada_nao_executada']}**",
        f"- Total de emissores diagnosticados: **{exec_sum['total_diagnosticado']}**", "",
        "## Detalhe por emissor", "",
        "| Emissor | Tier | País | Status | Fontes config. | Executadas | Sucesso | Itens | Eventos |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        v = to_dashboard_view(r)
        lines.append(f"| {r['company']} | {r.get('tier','')} | {r.get('country','')} | "
                     f"{v['status_ui']} | {v['sources_configured']} | {v['sources_executed']} | "
                     f"{v['sources_success']} | {v['items_found']} | {v['events_found']} |")
    lines += ["", "## Lacunas documentadas (não preenchidas artificialmente)", ""]
    for r in rows:
        if r["coverage_status"] in (NO_VALIDATED_OFFICIAL_SOURCE, COLLECTION_FAILURE,
                                    SOURCE_CONFIGURED_NOT_EXECUTED):
            lines.append(f"- **{r['company']}** ({r['coverage_status']}): "
                         f"{' '.join(r['reasons'])}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def export_matriz_cobertura_prioritarios_md(rows: list, path: str) -> str:
    """Matriz compacta só dos priorizados (Tier 1 + Peru + subsidiárias
    Coazucar) — o que a Entrega Final da 4H.2 usa para os itens 3-8."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = ["# Matriz de cobertura — emissores priorizados (4H.2)", "",
             "| Emissor | Tipo | Status | UI | Fontes ok/executadas/config. |",
             "|---|---|---|---|---|"]
    for r in rows:
        v = to_dashboard_view(r)
        tipo = "Subsidiária Coazucar" if r.get("is_subsidiary") else (
              "Tier 1" if r.get("tier") == 1 else "Peru")
        lines.append(f"| {r['company']} | {tipo} | {r['coverage_status']} | {v['status_ui']} | "
                     f"{v['sources_success']}/{v['sources_executed']}/{v['sources_configured']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def assert_exports_reconcile(rows: list, coverage_csv_path: str, source_csv_path: str) -> None:
    """Reabre os CSVs recém-escritos e confere, linha por linha, que os
    totais batem com `rows` (a mesma estrutura usada para montar o payload
    do dashboard) — quebra com `ReconciliationError` se não bater, em vez
    de silenciosamente divergir (item 8 da 4H.2)."""
    with open(coverage_csv_path, encoding="utf-8-sig") as f:
        csv_rows = list(csv.DictReader(f))
    if len(csv_rows) != len(rows):
        raise ReconciliationError(
            f"auditoria_cobertura_emissores.csv tem {len(csv_rows)} linha(s), "
            f"esperado {len(rows)} (1 por emissor diagnosticado).")
    by_company = {r["company"]: r for r in rows}
    for csv_row in csv_rows:
        rec = by_company.get(csv_row["company"])
        if rec is None:
            raise ReconciliationError(f"{csv_row['company']} está no CSV mas não em `rows`.")
        if csv_row["coverage_status"] != rec["coverage_status"]:
            raise ReconciliationError(
                f"{csv_row['company']}: CSV diz {csv_row['coverage_status']}, "
                f"payload diz {rec['coverage_status']}.")
    with open(source_csv_path, encoding="utf-8-sig") as f:
        src_rows = list(csv.DictReader(f))
    expected_src_rows = sum(len(r["sources"]) for r in rows)
    if len(src_rows) != expected_src_rows:
        raise ReconciliationError(
            f"auditoria_cobertura_fontes.csv tem {len(src_rows)} linha(s), "
            f"esperado {expected_src_rows} (soma de fontes por emissor).")


# ── Diagnóstico retroativo a partir dos artefatos reais do repositório ───
def run_retroactive_diagnosis(cfg_path: str = "config_risco.yaml",
                              run_meta_path: str = "run_meta.json",
                              out_dir: str = "out_coverage_diagnosis",
                              run_cvm_audit: bool = True) -> dict:
    """Roda o diagnóstico sobre `run_meta.json` (telemetria real da última
    execução registrada) + telemetria CVM REAL por emissor (4H.2b,
    `run_cvm_audit=True` por padrão — chama `audit_cvm_coverage(cfg)`, que
    baixa e casa o dataset IPE/CVM individualmente por emissor; é rede, mas
    é o mesmo caminho já usado por `--audit-cvm` em produção, não um novo
    tipo de coleta) + validação manual real de fontes peruanas (4H.2c).
    Gera os 6 exports obrigatórios e QUEBRA (`ReconciliationError`) se os
    CSVs não reconciliarem com `rows` (item 8 da 4H.2)."""
    import yaml
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(run_meta_path, encoding="utf-8") as f:
        run_meta = json.load(f)

    cvm_telemetry_map = {}
    cvm_audit_rows = []
    if run_cvm_audit:
        try:
            from risk_dashboard import audit_cvm_coverage
            cvm_audit_rows = audit_cvm_coverage(cfg)
            cvm_telemetry_map = build_cvm_telemetry(cvm_audit_rows)
        except Exception as exc:
            print(f" ⚠️  Auditoria CVM real indisponível nesta execução do diagnóstico "
                 f"({exc}) — REGULADOR_LOCAL cai para heurística sem telemetria "
                 f"(nunca infla cobertura, só reforça ausência de evidência).")
            cvm_telemetry_map = {}

    rows = diagnose_coverage(cfg, run_meta, cvm_telemetry_map=cvm_telemetry_map)
    counts = summarize_status_counts(rows)

    os.makedirs(out_dir, exist_ok=True)
    coverage_csv = export_coverage_csv(rows, os.path.join(out_dir, "coverage_status_by_company.csv"))
    source_csv = export_source_csv(rows, os.path.join(out_dir, "coverage_status_by_source.csv"))
    aud_emissores_csv = export_auditoria_cobertura_emissores_csv(
        rows, os.path.join(out_dir, "auditoria_cobertura_emissores.csv"))
    aud_fontes_csv = export_auditoria_cobertura_fontes_csv(
        rows, os.path.join(out_dir, "auditoria_cobertura_fontes.csv"))
    cfg_vs_exec_csv = export_fontes_configuradas_vs_executadas_csv(
        rows, os.path.join(out_dir, "fontes_configuradas_vs_executadas.csv"))
    falhas_csv = export_falhas_de_coleta_csv(
        rows, os.path.join(out_dir, "falhas_de_coleta.csv"))
    if cvm_audit_rows:
        with open(os.path.join(out_dir, "cvm_audit_real.csv"), "w", newline="",
                 encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(cvm_audit_rows[0].keys()))
            w.writeheader(); w.writerows(cvm_audit_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_meta_generated_at": run_meta.get("generated_at"),
        "run_meta_run_finished_at": run_meta.get("run_finished_at"),
        "companies_diagnosed": len(rows),
        "status_counts": counts,
        "executive_summary": build_executive_coverage_summary(rows),
        "cvm_audit_ran": bool(cvm_audit_rows),
        "cvm_companies_matched": len(cvm_audit_rows),
        "coverage_csv": coverage_csv,
        "source_csv": source_csv,
    }

    md_report = export_relatorio_cobertura_oficial_md(
        rows, summary, os.path.join(out_dir, "relatorio_cobertura_oficial.md"))
    priority_rows = [r for r in rows if r.get("tier") == 1 or r.get("is_subsidiary")
                     or r["company"] in _PERU_ONBOARDING]
    matriz_md = export_matriz_cobertura_prioritarios_md(
        priority_rows, os.path.join(out_dir, "matriz_cobertura_prioritarios.md"))

    # ── Reconciliação obrigatória (item 8): quebra se os CSVs não baterem
    # com `rows` — a mesma estrutura usada para montar o payload do
    # dashboard/resumo executivo.
    assert_exports_reconcile(rows, aud_emissores_csv, aud_fontes_csv)
    total_failures_rows = sum(1 for r in rows for s in r["sources"]
                              if s["attempted"] and not s["technical_success"])
    with open(falhas_csv, encoding="utf-8-sig") as f:
        n_falhas_csv = sum(1 for _ in csv.DictReader(f))
    if n_falhas_csv != total_failures_rows:
        raise ReconciliationError(
            f"falhas_de_coleta.csv tem {n_falhas_csv} linha(s), esperado "
            f"{total_failures_rows} (fontes com attempted=True e technical_success=False).")

    summary["reconciled"] = True
    summary["exports"] = {
        "coverage_status_by_company_csv": coverage_csv,
        "coverage_status_by_source_csv": source_csv,
        "auditoria_cobertura_emissores_csv": aud_emissores_csv,
        "auditoria_cobertura_fontes_csv": aud_fontes_csv,
        "fontes_configuradas_vs_executadas_csv": cfg_vs_exec_csv,
        "falhas_de_coleta_csv": falhas_csv,
        "relatorio_cobertura_oficial_md": md_report,
        "matriz_cobertura_prioritarios_md": matriz_md,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    return {"rows": rows, "summary": summary}


# ═══════════════════════════════════════════════════════════════════════
# RECONCILIAÇÃO RUNTIME (correção de 2 pendências reais de produção):
#
#   1) Os exports (`out_coverage_diagnosis/*.csv`, `.md`, `summary.json`)
#      eram gerados por um script SEPARADO e desconectado do fluxo real de
#      produção (`build_coverage_dashboard_preview.py`) — congelavam no
#      commit do merge e nunca eram regenerados a cada execução real do
#      workflow, enquanto o payload embutido no HTML (calculado dentro de
#      `render_html`, via `diagnose_coverage`) seguia fresco a cada run.
#      Dois caminhos de código, duas fontes de verdade, sem garantia de
#      reconciliação. CORREÇÃO: `build_canonical_coverage_result` abaixo é
#      o ÚNICO ponto de cálculo; `risk_dashboard.main()` o chama uma vez por
#      execução e passa o MESMO objeto para `render_html` (payload do HTML)
#      e para os 6 exports — nunca dois cálculos separados.
#
#   2) O diagnóstico tratava "fonte não escalada neste ciclo" (rotação
#      normal de tier — Tier 2 roda a cada ~2 execuções, Tier 3 a cada ~4,
#      ver `should_fetch_company` em risk_dashboard.py) como se fosse
#      "cobertura parcial"/falha. Isso produzia `PARTIAL_COVERAGE` alto
#      sempre que o ciclo de rotação simplesmente não escalava aquela fonte
#      neste run específico, mesmo com sucesso recente válido dentro de uma
#      janela de frescor razoável. CORREÇÃO: `classify_company_coverage_consolidated`
#      calcula um status CONSOLIDADO usando telemetria PERSISTIDA recente
#      (via `international_search_history.json`), distinto do status de
#      EXECUÇÃO DO CICLO ATUAL (que continua sendo exatamente
#      `classify_company_coverage`, sem alteração de comportamento).
#
# Nada aqui pontua, nada escreve em events_by_company/context_events_by_company,
# nada é lido por event_ids_for/build_evolution — mesma garantia do módulo
# original (ver docstring do topo do arquivo).
# ═══════════════════════════════════════════════════════════════════════

# ── Frescor por tipo de fonte ─────────────────────────────────────────────
# Calibrado pela cadência REAL do pipeline, não por valor arbitrário:
#   - O workflow de produção roda 4x/dia (`.github/workflows/
#     update_risk_dashboard.yml`, cron) → intervalo esperado entre
#     execuções ≈ 6h (`RUN_INTERVAL_HOURS`).
#   - GNEWS (busca genérica) é rotacionado por tier via `should_fetch_company`
#     (`config_risco.yaml: tiers.<n>.fetch_every_n_runs`): Tier 1 roda TODA
#     execução (n=1), Tier 2 a cada ~2 execuções (n=2), Tier 3 a cada ~4
#     (n=4). O prazo de validade tem que ser MAIOR que o ciclo de rotação
#     esperado, nunca menor — por isso aplicamos `FRESHNESS_SAFETY_FACTOR`
#     (2x) sobre `n * RUN_INTERVAL_HOURS`: Tier 1 → 12h, Tier 2 → 24h,
#     Tier 3 → 48h. Sem essa margem, uma fonte Tier 3 recém-rotacionada
#     ficaria "obsoleta" antes mesmo do próximo ciclo em que ela É esperada
#     rodar — o mesmo erro que estamos corrigindo, só que deslocado.
#   - RI_RSS/RI_NEWS/EDGAR: o código de coleta (`fetch_ri_news_pages`,
#     `fetch_edgar_filings`) NÃO usa `should_fetch_company` — tenta todo
#     emissor elegível em TODA execução. O prazo de validade é o mesmo de
#     um Tier 1 (12h) — se uma fonte oficial não teve sucesso técnico
#     dentro de 2 execuções (~12h), isso é sinal real de degradação, não
#     rotação esperada.
#   - REGULADOR_LOCAL (CVM/IPE): não é telemetria por execução — é um
#     cruzamento em lote contra um dataset anual (`audit_cvm_coverage`),
#     hoje disparado manualmente via `--audit-cvm`, não em toda execução
#     do workflow principal. Tratar isso com a mesma janela de 12h geraria
#     "obsoleto" em quase toda execução normal, mascarando o que é uma
#     característica real do método (lote, não streaming). Usamos uma
#     janela de 30 dias — compatível com a cadência observada de reemissão
#     de protocolos IPE e com o fato de o dataset ser anual.
WORKFLOW_RUNS_PER_DAY = 4
RUN_INTERVAL_HOURS = 24.0 / WORKFLOW_RUNS_PER_DAY  # 6h
FRESHNESS_SAFETY_FACTOR = 2.0
CVM_FRESHNESS_DAYS = 30
OFFICIAL_SOURCE_FRESHNESS_RUNS = 2  # RI_RSS/RI_NEWS/EDGAR: tentados toda execução

FRESHNESS_RULE_NOTES = {
    "GNEWS": "janela = fetch_every_n_runs(tier) × 6h × 2 (margem de segurança) — "
             "deriva da rotação real por tier em should_fetch_company/config_risco.yaml.",
    "RI_RSS": "janela fixa de 2 execuções (~12h) — coletor tenta todo emissor elegível "
              "em toda execução, sem rotação de tier.",
    "RI_NEWS": "janela fixa de 2 execuções (~12h) — mesmo motivo de RI_RSS.",
    "EDGAR": "janela fixa de 2 execuções (~12h) — fetch_edgar_filings tenta todo "
             "emissor elegível em toda execução, sem rotação de tier.",
    "REGULADOR_LOCAL": "janela de 30 dias — cruzamento em lote contra dataset IPE/CVM "
                       "anual, disparado via --audit-cvm, não em toda execução do "
                       "workflow principal; janela curta geraria 'obsoleto' artificial.",
}


def freshness_deadline_hours(source_name: str, company: dict, cfg: dict) -> float:
    """Prazo de validade (em horas) de uma evidência de sucesso técnico para
    esta fonte/emissor, calibrado pela cadência real do pipeline (ver notas
    acima). Nunca um valor arbitrário — sempre derivado de
    `fetch_every_n_runs`/cadência de coleta observada."""
    if source_name == "REGULADOR_LOCAL":
        return CVM_FRESHNESS_DAYS * 24.0
    if source_name == "GNEWS":
        tier = company.get("tier", 2)
        n = (cfg.get("tiers", {}) or {}).get(tier, {}) or {}
        n = n.get("fetch_every_n_runs", 1) or 1
        return n * RUN_INTERVAL_HOURS * FRESHNESS_SAFETY_FACTOR
    # RI_RSS, RI_NEWS, EDGAR
    return OFFICIAL_SOURCE_FRESHNESS_RUNS * RUN_INTERVAL_HOURS * FRESHNESS_SAFETY_FACTOR


def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _source_history_last_success(source_name: str, company_name: str,
                                 history_runs: list) -> str:
    """Varre `history_runs` (mais recente primeiro) por sucesso técnico
    PERSISTIDO para esta fonte/emissor. GNEWS usa `emitters` (a mesma
    telemetria de busca já persistida em `international_search_history.json`
    antes desta correção); fontes oficiais usam a chave `official_sources`
    (NOVA, persistida a partir desta correção — runs antigos não têm essa
    chave e são tratados como 'sem histórico', nunca como sucesso — não
    inventa cobertura para trás)."""
    for run in reversed(history_runs or []):
        ts = run.get("finished_at") or run.get("run_id") or ""
        if source_name == "GNEWS":
            rec = (run.get("emitters") or {}).get(company_name)
            if rec and rec.get("searched") and (rec.get("success", 0) or 0) > 0:
                return ts
        else:
            off = run.get("official_sources") or {}
            rec = (off.get(source_name) or {}).get(company_name)
            if rec and rec.get("attempted") and rec.get("success"):
                return ts
    return ""


def compute_source_freshness(source_name: str, company: dict, current_record: dict,
                             history_runs: list, cfg: dict, now_iso: str) -> dict:
    """Frescor de UMA fonte/emissor: cruza sucesso desta execução com
    sucesso persistido mais recente (histórico real, nunca inventado)."""
    last_success_at = ""
    if current_record.get("attempted") and current_record.get("technical_success"):
        last_success_at = now_iso
    else:
        hs = _source_history_last_success(source_name, company["name"], history_runs)
        if hs:
            last_success_at = hs
        elif current_record.get("last_success"):
            last_success_at = current_record["last_success"]
    deadline_h = freshness_deadline_hours(source_name, company, cfg)
    is_stale = True
    if last_success_at:
        now_dt, last_dt = _parse_iso(now_iso), _parse_iso(last_success_at)
        if now_dt is not None and last_dt is not None:
            age_h = (now_dt - last_dt).total_seconds() / 3600.0
            is_stale = age_h > deadline_h
    if not last_success_at:
        freshness_status = "sem_evidencia"
    elif is_stale:
        freshness_status = "obsoleta"
    else:
        freshness_status = "valida"
    return {
        "last_success_at": last_success_at,
        "expected_cadence_hours": deadline_h,
        "freshness_deadline_hours": deadline_h,
        "freshness_status": freshness_status,
        "is_stale": bool(is_stale),
    }


def classify_company_coverage_consolidated(company: dict, base_rec: dict,
                                            history_runs: list, cfg: dict,
                                            now_iso: str) -> dict:
    """Status CONSOLIDADO de cobertura: mesma árvore de decisão de
    `classify_company_coverage`, mas usando telemetria PERSISTIDA recente
    por fonte (não só o ciclo atual). Uma fonte não escalada neste ciclo,
    mas com sucesso recente válido dentro da janela de frescor, conta como
    efetivamente coberta — NUNCA como falha/parcial só por rotação normal.
    Uma falha nesta execução com sucesso recente ainda válido preserva o
    histórico de sucesso (não é apagado), mas a falha do ciclo atual
    continua visível no detalhe operacional (`execution_status_current_run`
    por fonte). NÃO força status positivo artificialmente: sem evidência
    válida (nem atual nem recente), a fonte conta como não coberta."""
    sources_now = base_rec["sources"]
    enriched = []
    for s in sources_now:
        fr = compute_source_freshness(s["source"], company, s, history_runs, cfg, now_iso)
        attempted_now = bool(s["attempted"])
        succeeded_now = bool(s["technical_success"])
        fresh_recent = bool(fr["last_success_at"]) and not fr["is_stale"]
        effective_success = succeeded_now or fresh_recent
        if attempted_now and succeeded_now:
            exec_status = "sucesso_neste_ciclo"
        elif attempted_now and not succeeded_now:
            exec_status = "falhou_neste_ciclo"
        else:
            exec_status = "nao_escalada_neste_ciclo"
        enriched.append({
            **s, **fr,
            "attempted_current_run": attempted_now,
            "scheduled_current_run": bool(s["configured"]),
            "success_current_run": succeeded_now,
            "not_scheduled_this_run": not attempted_now,
            "execution_status_current_run": exec_status,
            "consolidated_effective_success": effective_success,
        })

    all_configured = [s for s in enriched if s["configured"]]
    official_configured = [s for s in all_configured if s["source"] != "GNEWS"]
    eff_attempted = [s for s in all_configured
                     if s["attempted_current_run"]
                     or (bool(s["last_success_at"]) and not s["is_stale"])]
    eff_not_attempted = [s for s in all_configured if s not in eff_attempted]
    eff_failed = [s for s in eff_attempted if not s["consolidated_effective_success"]]
    eff_succeeded = [s for s in eff_attempted if s["consolidated_effective_success"]]

    items_total = sum(s["items_found"] for s in eff_succeeded)
    official_items = sum(s["items_found"] for s in eff_succeeded if s["source"] != "GNEWS")
    # Sucesso TÉCNICO (HTTP 200, dataset cruzado sem erro) nunca prova conteúdo
    # real — mesma regra de `link_debt_audit`/invariante 10 do CLAUDE.md.
    # `validated` (já calculado por fonte em `build_source_records`/validação
    # peru/CVM) é o único sinal aceito de que uma fonte OFICIAL realmente
    # confirmou algo. Sem isso, "0 item" não pode virar "cobertura oficial
    # confirmada sem notícia" (caso real: Yura — RI/BVL respondem HTTP 200,
    # zero hecho relevante extraído; sem essa checagem, o status consolidado
    # lia isso como confirmação oficial).
    official_ever_validated = any(s.get("validated") for s in official_configured)

    reasons = []
    if not official_configured:
        status = NO_VALIDATED_OFFICIAL_SOURCE
        reasons.append("Nenhuma fonte oficial configurada/validada para este emissor "
                       "(status consolidado — mesma avaliação do ciclo atual, pois "
                       "'configurado' não muda entre execuções).")
    elif not eff_attempted:
        status = SOURCE_CONFIGURED_NOT_EXECUTED
        cfgd = ", ".join(s["source"] for s in eff_not_attempted)
        reasons.append(f"Fonte(s) configurada(s) ({cfgd}) sem execução válida NESTE "
                       f"ciclo e sem sucesso recente dentro da janela de frescor.")
    elif eff_failed and not eff_succeeded:
        status = COLLECTION_FAILURE
        errd = ", ".join(s["source"] for s in eff_failed)
        reasons.append(f"Todas as fontes com tentativa válida ({errd}) falharam e não "
                       f"têm sucesso recente ainda dentro da janela de frescor.")
    elif eff_not_attempted or eff_failed:
        status = PARTIAL_COVERAGE
        ok = ", ".join(s["source"] for s in eff_succeeded) or "—"
        bad = ", ".join(s["source"] for s in (eff_not_attempted + eff_failed)) or "—"
        reasons.append(f"Cobertura mista consolidada: {ok} com evidência válida "
                       f"(atual ou recente dentro da janela de frescor); {bad} sem "
                       f"evidência válida.")
    elif not official_ever_validated:
        # Fonte oficial nunca teve extração VALIDADA (só sucesso técnico —
        # HTTP 200/dataset cruzado — sem conteúdo real demonstrado). Sucesso
        # técnico isolado NUNCA vira "cobertura oficial confirmada".
        if items_total > 0:
            status = FALLBACK_ONLY
            reasons.append("Fonte(s) oficial(is) com sucesso técnico mas SEM extração "
                           "validada (HTTP 200 sem conteúdo real demonstrado); item(ns) "
                           "encontrado(s) vieram do fallback (Google News).")
        else:
            status = PARTIAL_COVERAGE
            reasons.append("Fonte(s) oficial(is) configurada(s) nunca tiveram extração "
                           "validada (sucesso técnico sem conteúdo real) e nenhum item "
                           "veio do fallback — cobertura permanece incompleta, não pode "
                           "ser lida como 'sem notícia após execução bem-sucedida'.")
    elif official_items == 0 and items_total > 0:
        status = FALLBACK_ONLY
        reasons.append("Fonte(s) oficial(is) com evidência válida, mas 0 item nesta "
                       "execução; único conteúdo veio do fallback (Google News).")
    elif items_total == 0:
        status = NO_RELEVANT_NEWS_AFTER_SUCCESSFUL_RUN
        reasons.append("Fonte(s) oficial(is)/regulatória(s) com sucesso VALIDADO e "
                       "recente, e nenhuma retornou item nesta execução.")
    elif base_rec.get("scored_events", 0) == 0:
        status = ONLY_INFORMATIONAL_FOUND
        reasons.append("Item(ns) encontrado(s), 0 evento(s) pontuável(is) nesta execução.")
    else:
        status = COVERAGE_OK_EVENTS_FOUND
        reasons.append("Cobertura normal — evento(s) pontuável(is) nesta execução.")

    not_scheduled_sources = [s["source"] for s in enriched if s["not_scheduled_this_run"]]
    stale_official = [s for s in official_configured
                      if s["freshness_status"] in ("obsoleta", "sem_evidencia")]
    company_last_success = max((s["last_success_at"] for s in enriched if s["last_success_at"]),
                               default="")
    # Origem da evidência que sustenta o status consolidado — item 3 da
    # correção: o texto do dashboard tem que deixar claro se o sucesso vem
    # de fonte oficial (RI), regulador (CVM/EDGAR), ou só do fallback
    # genérico (Google News) — nunca apresentar sucesso técnico não
    # validado como se fosse confirmação oficial.
    regulator_validated_success = any(
        s["source"] == "REGULADOR_LOCAL" and s.get("validated") and s in eff_succeeded
        for s in enriched)
    if regulator_validated_success:
        evidence_kind = "regulador"
    elif official_ever_validated and official_items > 0:
        evidence_kind = "oficial"
    elif items_total > 0:
        evidence_kind = "fallback"
    else:
        evidence_kind = "sem_evidencia"
    return {
        "coverage_status_consolidated": status,
        "coverage_status_consolidated_label": status_label(status),
        "coverage_status_consolidated_ui": status_ui_phrase(status),
        "coverage_evidence_kind_consolidated": evidence_kind,
        "official_ever_validated": official_ever_validated,
        "reasons_consolidated": reasons,
        "sources_consolidated": enriched,
        "attempted_current_run": any(s["attempted_current_run"] for s in all_configured),
        "scheduled_current_run": len(all_configured),
        "not_scheduled_current_run_sources": not_scheduled_sources,
        "last_success_at": company_last_success,
        "is_stale": bool(stale_official) and not bool(eff_succeeded),
        "freshness_status": ("obsoleta" if (stale_official and not eff_succeeded)
                             else ("parcial" if stale_official else "valida")),
    }


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def compute_payload_hash(rows: list, meta: dict) -> str:
    """Hash estável (sha256) do conteúdo de cobertura — usado para provar
    que o payload embutido no HTML e os 6 exports vieram do MESMO cálculo
    nesta execução (item 2/7 da correção)."""
    meta_for_hash = {k: v for k, v in meta.items() if k != "payload_hash"}
    blob = _canonical_json({"rows": rows, "meta": meta_for_hash})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── Persistência da telemetria CVM (item 1 do bloqueio operacional) ──────
# `audit_cvm_coverage` (risk_dashboard.py) só roda via `--audit-cvm`
# (workflow_dispatch manual OU cadência semanal dedicada — ver
# .github/workflows/update_risk_dashboard.yml), nunca em toda execução do
# cron principal. Sem persistir o resultado, o status CONSOLIDADO de
# REGULADOR_LOCAL nunca teria evidência para os 16 Tier 1 até a próxima
# auditoria "orgânica" — inaceitável para emissores com evidência CVM já
# validada e real (Fase 4H.2, `out_coverage_diagnosis/cvm_audit_real.csv`).
# `build_cvm_telemetry_seed`/`persist_cvm_telemetry` fecham esse laço: tanto
# a migração ÚNICA do resultado 4H.2 quanto toda execução FUTURA de
# `--audit-cvm` alimentam o MESMO armazém persistido
# (`international_search_history.json["cvm_telemetry"]`).
def build_cvm_telemetry_seed(cvm_audit_rows: list, generated_at: str | None = None,
                             origin: str = "") -> dict:
    """Converte a saída de `audit_cvm_coverage()` (ou as linhas equivalentes
    de `cvm_audit_real.csv`) no formato persistido por emissor, reaproveitando
    `build_cvm_telemetry` (mesmos campos) e acrescentando `codigo_cvm`/`cnpj`
    (não presentes no dict compacto) mais os marcadores de proveniência
    exigidos: `seeded_from_existing_telemetry=True`, `origem_migracao`."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    base = build_cvm_telemetry(cvm_audit_rows, generated_at=generated_at)
    by_name = {r.get("emissor"): r for r in cvm_audit_rows}
    seed = {}
    for name, rec in base.items():
        row = by_name.get(name, {})
        rec = dict(rec)
        rec["codigo_cvm"] = row.get("codigo_cvm_casado", "")
        rec["cnpj"] = row.get("cnpj_casado", "")
        rec["seeded_from_existing_telemetry"] = True
        rec["origem_migracao"] = origin
        seed[name] = rec
    return seed


def load_cvm_telemetry_seed_from_audit_csv(csv_path: str, generated_at: str | None = None,
                                           origin: str | None = None) -> dict:
    """Lê um `cvm_audit_real.csv`/`auditoria_cobertura_cvm.csv` já gravado
    (mesmas colunas produzidas por `audit_cvm_coverage`) e monta o seed —
    usado pela migração única (Opção A) a partir da evidência real da 4H.2."""
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    origin = origin or f"{csv_path} (migração única)"
    return build_cvm_telemetry_seed(rows, generated_at=generated_at, origin=origin)


def load_persisted_cvm_telemetry(history_path: str = "international_search_history.json") -> dict:
    """Lê `international_search_history.json["cvm_telemetry"]` — telemetria
    CVM persistida (migrada e/ou de execuções reais de `--audit-cvm`), usada
    como fallback de frescor para REGULADOR_LOCAL quando a auditoria não
    rodou NESTA execução."""
    if not os.path.exists(history_path):
        return {}
    try:
        with open(history_path, encoding="utf-8") as f:
            sh = json.load(f)
    except Exception:
        return {}
    return sh.get("cvm_telemetry") or {}


def persist_cvm_telemetry(seed: dict, history_path: str = "international_search_history.json") -> dict:
    """Faz upsert de `seed` (por emissor) em
    `international_search_history.json["cvm_telemetry"]`, PRESERVANDO
    qualquer emissor não presente em `seed` (uma falha pontual da auditoria
    — dataset indisponível, `audit_cvm_coverage` retorna `[]` — produz
    `seed={}`; o laço abaixo não executa, e o armazenado anteriormente
    permanece intocado — nunca apagado por uma falha transitória)."""
    sh: dict = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, encoding="utf-8") as f:
                sh = json.load(f)
        except Exception:
            sh = {}
    sh.setdefault("runs", [])
    existing = dict(sh.get("cvm_telemetry") or {})
    added, updated = [], []
    for name, rec in (seed or {}).items():
        if name in existing:
            updated.append(name)
        else:
            added.append(name)
        existing[name] = rec
    sh["cvm_telemetry"] = existing
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(sh, f, ensure_ascii=False)
    return {"added": added, "updated": updated, "total": len(existing)}


def build_canonical_coverage_result(cfg: dict, run_meta: dict, history_runs: list | None = None,
                                    companies: list | None = None, run_id: str | None = None,
                                    generated_at: str | None = None, commit_base: str | None = None,
                                    cvm_status_map: dict | None = None,
                                    cvm_telemetry_map: dict | None = None,
                                    peru_validation_map: dict | None = None,
                                    cvm_persisted_telemetry: dict | None = None) -> dict:
    """FONTE CANÔNICA ÚNICA de cobertura para uma execução: calcula, para
    cada emissor priorizado, o status do CICLO ATUAL (`classify_company_coverage`,
    inalterado) e o status CONSOLIDADO (`classify_company_coverage_consolidated`,
    usando telemetria persistida). Retorna `{"rows": [...], "meta": {...}}` —
    este É o objeto que `risk_dashboard.render_html` embute no HTML E que os
    6 exports gravam; nunca dois cálculos separados (correção da pendência 1).

    `cvm_persisted_telemetry` é a telemetria CVM por emissor persistida em
    `international_search_history.json["cvm_telemetry"]` (via
    `persist_cvm_telemetry`/migração de `cvm_audit_real.csv`) — usada como
    FALLBACK para emissores sem telemetria CVM NESTA execução (a auditoria
    `--audit-cvm` roda esporadicamente, não em toda execução). `cvm_telemetry_map`
    (desta execução, quando fornecido) sempre tem prioridade sobre o
    persistido — dado mais fresco vence, nunca o inverso."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    run_id = run_id or generated_at
    history_runs = history_runs or []
    companies = companies if companies is not None else priority_companies(cfg)

    merged_cvm_telemetry_map = dict(cvm_persisted_telemetry or {})
    merged_cvm_telemetry_map.update(cvm_telemetry_map or {})

    base_rows = diagnose_coverage(cfg, run_meta, cvm_status_map=cvm_status_map,
                                  companies=companies,
                                  cvm_telemetry_map=merged_cvm_telemetry_map or None,
                                  peru_validation_map=peru_validation_map)
    rows = []
    for base_rec, company in zip(base_rows, companies):
        consolidated = classify_company_coverage_consolidated(
            company, base_rec, history_runs, cfg, generated_at)
        row = dict(base_rec)
        row["execution_status_current_run"] = base_rec["coverage_status"]
        row.update(consolidated)
        rows.append(row)

    meta = {
        "run_id": run_id,
        "generated_at": generated_at,
        "commit_base": commit_base or "unknown",
        "companies_count": len(rows),
    }
    meta["payload_hash"] = compute_payload_hash(rows, meta)
    return {"rows": rows, "meta": meta}


def to_dashboard_view_v2(rec: dict) -> dict:
    """Como `to_dashboard_view`, mas para uma linha produzida por
    `build_canonical_coverage_result` (com campos consolidado/ciclo-atual
    separados) — é o que a seção 'Cobertura das fontes' do template usa."""
    base = to_dashboard_view(rec)
    base.update({
        "status_consolidated": rec.get("coverage_status_consolidated", rec["coverage_status"]),
        "status_consolidated_ui": rec.get("coverage_status_consolidated_ui", base["status_ui"]),
        "status_current_run": rec.get("execution_status_current_run", rec["coverage_status"]),
        "status_current_run_ui": rec.get("coverage_status_ui", base["status_ui"]),
        "reasons_consolidated": rec.get("reasons_consolidated", rec.get("reasons", [])),
        "attempted_current_run": rec.get("attempted_current_run", False),
        "scheduled_current_run": rec.get("scheduled_current_run", 0),
        "not_scheduled_current_run_sources": rec.get("not_scheduled_current_run_sources", []),
        "last_success_at": rec.get("last_success_at", ""),
        "freshness_status": rec.get("freshness_status", "sem_evidencia"),
        "is_stale": rec.get("is_stale", True),
        "evidence_kind_consolidated": rec.get("coverage_evidence_kind_consolidated", "sem_evidencia"),
        "official_ever_validated": rec.get("official_ever_validated", False),
    })
    src_cons = rec.get("sources_consolidated") or rec["sources"]
    base["sources"] = [
        {
            "name": s["source"],
            "type": s.get("source_type") or s["source"],
            "method": s.get("method", ""),
            "status": ("sucesso" if s.get("technical_success") else
                      ("falhou" if s.get("attempted") else "não tentada")),
            "execution_status_current_run": s.get("execution_status_current_run", ""),
            "last_attempt": s.get("last_attempt", ""),
            "last_success": s.get("last_success", ""),
            "last_success_at": s.get("last_success_at", ""),
            "freshness_status": s.get("freshness_status", "sem_evidencia"),
            "is_stale": s.get("is_stale", True),
            "not_scheduled_this_run": s.get("not_scheduled_this_run", False),
            "items_found": s["items_found"],
            "latest_item_date": s.get("latest_item_date", ""),
            "latest_item_title": s.get("latest_item_title", ""),
            "error": s.get("error", ""),
            "link": s.get("link", ""),
            "note": s.get("note", ""),
        } for s in src_cons
    ]
    return base


# ── Exports (v2) — mesmas 6 tabelas exigidas, com colunas de reconciliação
# (run_id, generated_at, commit_base, payload_hash) e os novos campos de
# consolidado/ciclo-atual/frescor. Mantém as colunas originais + acrescenta.
def export_auditoria_cobertura_emissores_csv_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "generated_at", "commit_base", "payload_hash",
                   "company", "tier", "country", "is_subsidiary", "parent_company",
                   "coverage_status", "coverage_status_ui",
                   "coverage_status_consolidated", "coverage_status_consolidated_ui",
                   "evidence_kind_consolidated", "official_ever_validated",
                   "execution_status_current_run", "attempted_current_run",
                   "scheduled_current_run", "last_success_at", "freshness_status",
                   "is_stale", "sources_configured", "sources_executed", "sources_success",
                   "items_found", "events_found", "failures_count"])
        for r in rows:
            v = to_dashboard_view_v2(r)
            w.writerow([meta["run_id"], meta["generated_at"], meta["commit_base"],
                       meta["payload_hash"], r["company"], r.get("tier", ""),
                       r.get("country", ""), r.get("is_subsidiary", False),
                       r.get("parent_company", ""), r["coverage_status"], v["status_ui"],
                       r.get("coverage_status_consolidated", r["coverage_status"]),
                       v["status_consolidated_ui"], v["evidence_kind_consolidated"],
                       v["official_ever_validated"], v["status_current_run"],
                       v["attempted_current_run"], v["scheduled_current_run"],
                       v["last_success_at"], v["freshness_status"], v["is_stale"],
                       v["sources_configured"], v["sources_executed"], v["sources_success"],
                       v["items_found"], v["events_found"], v["failures_count"]])
    return path


def export_auditoria_cobertura_fontes_csv_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "generated_at", "commit_base", "payload_hash",
                   "company", "coverage_status", "coverage_status_consolidated",
                   "source", "source_type", "method", "configured", "attempted",
                   "technical_success", "execution_status_current_run",
                   "not_scheduled_this_run", "items_found", "validated",
                   "last_attempt", "last_success", "last_success_at",
                   "freshness_status", "is_stale", "error", "link", "note"])
        for r in rows:
            src_cons = r.get("sources_consolidated") or r["sources"]
            for s in src_cons:
                w.writerow([meta["run_id"], meta["generated_at"], meta["commit_base"],
                           meta["payload_hash"], r["company"], r["coverage_status"],
                           r.get("coverage_status_consolidated", r["coverage_status"]),
                           s["source"], s.get("source_type", ""), s.get("method", ""),
                           s["configured"], s["attempted"], s["technical_success"],
                           s.get("execution_status_current_run", ""),
                           s.get("not_scheduled_this_run", False), s["items_found"],
                           s["validated"], s.get("last_attempt", ""),
                           s.get("last_success", ""), s.get("last_success_at", ""),
                           s.get("freshness_status", "sem_evidencia"), s.get("is_stale", True),
                           s.get("error", ""), s.get("link", ""), s.get("note", "")])
    return path


def export_fontes_configuradas_vs_executadas_csv_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "generated_at", "commit_base", "payload_hash",
                   "company", "source", "configured", "executed_current_run",
                   "gap_configured_not_executed_current_run", "not_scheduled_this_run",
                   "fresh_recent_evidence", "gap_configured_not_covered_consolidated"])
        for r in rows:
            src_cons = r.get("sources_consolidated") or r["sources"]
            for s in src_cons:
                gap_now = bool(s["configured"] and not s["attempted"])
                fresh = bool(s.get("last_success_at")) and not s.get("is_stale", True)
                gap_consolidated = bool(s["configured"] and not s["attempted"] and not fresh)
                w.writerow([meta["run_id"], meta["generated_at"], meta["commit_base"],
                           meta["payload_hash"], r["company"], s["source"], s["configured"],
                           s["attempted"], gap_now, s.get("not_scheduled_this_run", False),
                           fresh, gap_consolidated])
    return path


def export_falhas_de_coleta_csv_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "generated_at", "commit_base", "payload_hash",
                   "company", "source", "source_type", "method", "error", "last_attempt",
                   "link", "fresh_recent_evidence_preserved"])
        for r in rows:
            src_cons = r.get("sources_consolidated") or r["sources"]
            for s in src_cons:
                if s["attempted"] and not s["technical_success"]:
                    fresh = bool(s.get("last_success_at")) and not s.get("is_stale", True)
                    w.writerow([meta["run_id"], meta["generated_at"], meta["commit_base"],
                               meta["payload_hash"], r["company"], s["source"],
                               s.get("source_type", ""), s.get("method", ""),
                               s.get("error", ""), s.get("last_attempt", ""),
                               s.get("link", ""), fresh])
    return path


def export_relatorio_cobertura_oficial_md_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    counts_current = summarize_status_counts(rows)
    counts_consolidated: dict = {}
    for r in rows:
        st = r.get("coverage_status_consolidated", r["coverage_status"])
        counts_consolidated[st] = counts_consolidated.get(st, 0) + 1
    exec_sum_current = build_executive_coverage_summary(rows)
    lines = [
        "# Relatório de cobertura oficial (reconciliação runtime)", "",
        f"run_id: {meta['run_id']}",
        f"generated_at: {meta['generated_at']}",
        f"commit_base: {meta['commit_base']}",
        f"payload_hash: {meta['payload_hash']}",
        f"companies_count: {meta['companies_count']}", "",
        "## Resumo executivo — CICLO ATUAL", "",
        f"- Cobertura confirmada: **{exec_sum_current['cobertura_confirmada']}**",
        f"- Cobertura parcial: **{exec_sum_current['cobertura_parcial']}**",
        f"- Falha de coleta: **{exec_sum_current['falha_de_coleta']}**",
        f"- Somente fallback: **{exec_sum_current['somente_fallback']}**",
        f"- Sem fonte oficial validada: **{exec_sum_current['sem_fonte_oficial']}**",
        f"- Fonte configurada, não executada: **{exec_sum_current['fonte_configurada_nao_executada']}**",
        "", "## Resumo executivo — CONSOLIDADO (janela de frescor por fonte)", "",
    ]
    for st in COVERAGE_STATUSES + (COVERAGE_OK_EVENTS_FOUND,):
        lines.append(f"- {status_label(st)}: **{counts_consolidated.get(st, 0)}**")
    lines += ["", "## Detalhe por emissor", "",
             "| Emissor | Ciclo atual | Consolidado | Não escaladas neste ciclo | "
             "Última evidência | Frescor |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        v = to_dashboard_view_v2(r)
        lines.append(f"| {r['company']} | {v['status_current_run']} | "
                     f"{v['status_consolidated']} | "
                     f"{', '.join(v['not_scheduled_current_run_sources']) or '—'} | "
                     f"{v['last_success_at'] or '—'} | {v['freshness_status']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def export_matriz_cobertura_prioritarios_md_v2(rows: list, meta: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = ["# Matriz de cobertura — emissores priorizados (reconciliação runtime)", "",
             f"run_id: {meta['run_id']} · generated_at: {meta['generated_at']} · "
             f"commit_base: {meta['commit_base']} · payload_hash: {meta['payload_hash']}", "",
             "| Emissor | Tipo | Ciclo atual | Consolidado | Frescor |",
             "|---|---|---|---|---|"]
    for r in rows:
        v = to_dashboard_view_v2(r)
        tipo = "Subsidiária Coazucar" if r.get("is_subsidiary") else (
              "Tier 1" if r.get("tier") == 1 else "Peru")
        lines.append(f"| {r['company']} | {tipo} | {v['status_current_run']} | "
                     f"{v['status_consolidated']} | {v['freshness_status']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def assert_exports_reconcile_v2(rows: list, meta: dict, out_dir: str) -> None:
    """Gate de reconciliação (bloqueia publicação): reabre os 6 exports
    recém-gravados e confere, campo a campo, contra `rows`/`meta` — o MESMO
    objeto usado para montar o payload embutido no HTML. Levanta
    `ReconciliationError` (subclasse de AssertionError) na primeira
    divergência — nunca um warning silencioso."""
    def _p(name):
        return os.path.join(out_dir, name)

    with open(_p("auditoria_cobertura_emissores.csv"), encoding="utf-8-sig") as f:
        emissores_csv = list(csv.DictReader(f))
    with open(_p("auditoria_cobertura_fontes.csv"), encoding="utf-8-sig") as f:
        fontes_csv = list(csv.DictReader(f))
    with open(_p("fontes_configuradas_vs_executadas.csv"), encoding="utf-8-sig") as f:
        cfg_exec_csv = list(csv.DictReader(f))
    with open(_p("falhas_de_coleta.csv"), encoding="utf-8-sig") as f:
        falhas_csv = list(csv.DictReader(f))

    # 1) total de emissores
    if len(emissores_csv) != len(rows):
        raise ReconciliationError(
            f"auditoria_cobertura_emissores.csv tem {len(emissores_csv)} linha(s), "
            f"esperado {len(rows)} (1 por emissor diagnosticado nesta execução).")
    if len(emissores_csv) != meta["companies_count"]:
        raise ReconciliationError(
            f"companies_count do meta ({meta['companies_count']}) diverge do CSV "
            f"({len(emissores_csv)}).")

    by_company = {r["company"]: r for r in rows}
    total_sources_expected = 0
    total_items_expected = 0
    total_events_expected = 0
    total_failures_expected = 0
    total_fallback_expected = 0
    status_counts_expected: dict = {}
    status_consolidated_counts_expected: dict = {}
    for r in rows:
        status_counts_expected[r["coverage_status"]] = status_counts_expected.get(
            r["coverage_status"], 0) + 1
        cst = r.get("coverage_status_consolidated", r["coverage_status"])
        status_consolidated_counts_expected[cst] = status_consolidated_counts_expected.get(
            cst, 0) + 1
        if r["coverage_status"] == FALLBACK_ONLY:
            total_fallback_expected += 1
        srcs = r.get("sources_consolidated") or r["sources"]
        total_sources_expected += len(srcs)
        total_items_expected += sum(s["items_found"] for s in srcs)
        total_events_expected += r.get("scored_events", 0)
        total_failures_expected += sum(1 for s in srcs
                                       if s["attempted"] and not s["technical_success"])

    # 2) run_id/hash/commit_base idênticos em TODAS as linhas de TODOS os exports
    for label, csv_rows in (("auditoria_cobertura_emissores.csv", emissores_csv),
                            ("auditoria_cobertura_fontes.csv", fontes_csv),
                            ("fontes_configuradas_vs_executadas.csv", cfg_exec_csv)):
        for row in csv_rows:
            if row.get("run_id") != meta["run_id"]:
                raise ReconciliationError(
                    f"{label}: run_id divergente ({row.get('run_id')} != {meta['run_id']}).")
            if row.get("payload_hash") != meta["payload_hash"]:
                raise ReconciliationError(
                    f"{label}: payload_hash divergente ({row.get('payload_hash')} != "
                    f"{meta['payload_hash']}).")
            if row.get("commit_base") != meta["commit_base"]:
                raise ReconciliationError(
                    f"{label}: commit_base divergente ({row.get('commit_base')} != "
                    f"{meta['commit_base']}).")

    # 3) status por emissor (ciclo atual e consolidado)
    for row in emissores_csv:
        rec = by_company.get(row["company"])
        if rec is None:
            raise ReconciliationError(f"{row['company']} está no CSV mas não em `rows`.")
        if row["coverage_status"] != rec["coverage_status"]:
            raise ReconciliationError(
                f"{row['company']}: CSV (ciclo atual) diz {row['coverage_status']}, "
                f"payload diz {rec['coverage_status']}.")
        exp_cons = rec.get("coverage_status_consolidated", rec["coverage_status"])
        if row["coverage_status_consolidated"] != exp_cons:
            raise ReconciliationError(
                f"{row['company']}: CSV (consolidado) diz "
                f"{row['coverage_status_consolidated']}, payload diz {exp_cons}.")

    # 4) totais por status (ciclo atual e consolidado)
    for st, n in status_counts_expected.items():
        n_csv = sum(1 for row in emissores_csv if row["coverage_status"] == st)
        if n_csv != n:
            raise ReconciliationError(
                f"Total do status (ciclo atual) {st}: CSV={n_csv}, esperado={n}.")
    for st, n in status_consolidated_counts_expected.items():
        n_csv = sum(1 for row in emissores_csv if row["coverage_status_consolidated"] == st)
        if n_csv != n:
            raise ReconciliationError(
                f"Total do status (consolidado) {st}: CSV={n_csv}, esperado={n}.")

    # 5) fontes configuradas/executadas
    if len(cfg_exec_csv) != total_sources_expected:
        raise ReconciliationError(
            f"fontes_configuradas_vs_executadas.csv tem {len(cfg_exec_csv)} linha(s), "
            f"esperado {total_sources_expected} (soma de fontes por emissor).")
    if len(fontes_csv) != total_sources_expected:
        raise ReconciliationError(
            f"auditoria_cobertura_fontes.csv tem {len(fontes_csv)} linha(s), "
            f"esperado {total_sources_expected}.")

    # 6) itens encontrados / eventos relevantes
    items_csv = sum(int(row["items_found"] or 0) for row in fontes_csv)
    if items_csv != total_items_expected:
        raise ReconciliationError(
            f"Soma de items_found no CSV de fontes ({items_csv}) diverge do payload "
            f"({total_items_expected}).")
    events_csv = sum(int(row["events_found"] or 0) for row in emissores_csv)
    if events_csv != total_events_expected:
        raise ReconciliationError(
            f"Soma de events_found no CSV de emissores ({events_csv}) diverge do payload "
            f"({total_events_expected}).")

    # 7) falhas
    if len(falhas_csv) != total_failures_expected:
        raise ReconciliationError(
            f"falhas_de_coleta.csv tem {len(falhas_csv)} linha(s), esperado "
            f"{total_failures_expected} (fontes com attempted=True e technical_success=False).")

    # 8) fallback (ciclo atual)
    fallback_csv = sum(1 for row in emissores_csv if row["coverage_status"] == FALLBACK_ONLY)
    if fallback_csv != total_fallback_expected:
        raise ReconciliationError(
            f"Total FALLBACK_ONLY (ciclo atual): CSV={fallback_csv}, "
            f"esperado={total_fallback_expected}.")


def run_production_coverage(cfg: dict, run_meta: dict, history_runs: list | None = None,
                            companies: list | None = None, out_dir: str = "out_coverage_diagnosis",
                            run_id: str | None = None, generated_at: str | None = None,
                            commit_base: str | None = None, cvm_status_map: dict | None = None,
                            cvm_telemetry_map: dict | None = None,
                            peru_validation_map: dict | None = None,
                            cvm_persisted_telemetry: dict | None = None) -> dict:
    """Ponto de entrada ÚNICO chamado por `risk_dashboard.main()` em TODA
    execução de produção (item 1/2/3 da correção): calcula o resultado
    canônico, grava os 6 exports obrigatórios e roda o gate de reconciliação
    — quebra (`ReconciliationError`) ANTES da publicação/commit se algo não
    bater. Retorna `{"rows", "meta", "exports"}`; `rows`/`meta` são o MESMO
    objeto que o chamador deve passar para `render_html`.

    `cvm_persisted_telemetry` (opcional): telemetria CVM persistida — quando
    omitido, é carregada automaticamente de
    `international_search_history.json["cvm_telemetry"]` (mesmo arquivo lido
    para `history_runs`), fechando o laço da migração/execuções de
    `--audit-cvm` sem exigir que todo chamador a passe explicitamente."""
    if cvm_persisted_telemetry is None:
        cvm_persisted_telemetry = load_persisted_cvm_telemetry()
    result = build_canonical_coverage_result(
        cfg, run_meta, history_runs=history_runs, companies=companies, run_id=run_id,
        generated_at=generated_at, commit_base=commit_base, cvm_status_map=cvm_status_map,
        cvm_telemetry_map=cvm_telemetry_map, peru_validation_map=peru_validation_map,
        cvm_persisted_telemetry=cvm_persisted_telemetry)
    rows, meta = result["rows"], result["meta"]
    os.makedirs(out_dir, exist_ok=True)

    exports = {
        "auditoria_cobertura_fontes_csv": export_auditoria_cobertura_fontes_csv_v2(
            rows, meta, os.path.join(out_dir, "auditoria_cobertura_fontes.csv")),
        "auditoria_cobertura_emissores_csv": export_auditoria_cobertura_emissores_csv_v2(
            rows, meta, os.path.join(out_dir, "auditoria_cobertura_emissores.csv")),
        "fontes_configuradas_vs_executadas_csv": export_fontes_configuradas_vs_executadas_csv_v2(
            rows, meta, os.path.join(out_dir, "fontes_configuradas_vs_executadas.csv")),
        "falhas_de_coleta_csv": export_falhas_de_coleta_csv_v2(
            rows, meta, os.path.join(out_dir, "falhas_de_coleta.csv")),
        "relatorio_cobertura_oficial_md": export_relatorio_cobertura_oficial_md_v2(
            rows, meta, os.path.join(out_dir, "relatorio_cobertura_oficial.md")),
        "matriz_cobertura_prioritarios_md": export_matriz_cobertura_prioritarios_md_v2(
            [r for r in rows if r.get("tier") == 1 or r.get("is_subsidiary")
             or r["company"] in _PERU_ONBOARDING],
            meta, os.path.join(out_dir, "matriz_cobertura_prioritarios.md")),
    }

    # ── Gate de reconciliação (item 3) — quebra ANTES de qualquer publicação ──
    assert_exports_reconcile_v2(rows, meta, out_dir)

    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows, "exports": exports}, f,
                  ensure_ascii=False, indent=2)

    return {"rows": rows, "meta": meta, "exports": exports}
