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
