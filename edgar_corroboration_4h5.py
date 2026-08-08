#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_corroboration_4h5.py — 4H.5: EDGAR como fonte OFICIAL DE CORROBORAÇÃO
de ocorrências já validadas pelas fontes normais do Radar (notícia/RI/CVM).

Decisão herdada da 4H.4/4H.4B (não reaberta aqui): EDGAR não tem precisão
suficiente para ORIGINAR sozinho uma ocorrência pontuável em nenhuma família
(Gate 1B reprovado, Validation independente: precisão 90% N=20, Wilson
inferior 69,9%, 1 FP_CRITICAL, falsos negativos materiais). Esta fase NÃO
reabre essa decisão — apenas usa a infraestrutura de classificação já
validada (edgar_canonical/edgar_dom) para uma tarefa estruturalmente mais
fácil e mais conservadora: CONFIRMAR uma ocorrência que outra fonte já
encontrou, nunca criar uma nova.

Duas capacidades deliberadamente separadas (arquitetura já existente em
risk_dashboard.py, não inventada aqui):
    edgar_collection_enabled  → liga/desliga a COLETA (buscar filings)
    edgar_scoring_enabled     → liga/desliga EDGAR como ORIGEM de score
Este módulo só roda no CASO B (`collection=True, scoring=False`) — o mesmo
gate que já protege `run_edgar_runtime_shadow`. Nunca cria registro novo em
`history["articles"]`; só ANEXA `corrob_sources`/`corroborations` a um
registro EXISTENTE, usando o mecanismo de bônus já existente em
`build_evolution` (peso-base único, nunca duplicado — ver `_persist_source`
em risk_dashboard.py, mesma forma, mesmo dedup por domínio).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import edgar_canonical as ec
import edgar_dom as ed
import edgar_normalizer as en
import edgar_sections as es

SEC_DOMAIN = "sec.gov"

# 4H.7C: formas com corpo tentado nesta fase. 8-K usa o parser DOM
# (edgar_dom, validado 4H.3C-F). 6-K usa o extrator de seção já existente
# da 4H.3E (edgar_sections.split_6k_release, calibrado em corpus real de
# 113 documentos) — NENHUM parser novo foi escrito. 10-Q e qualquer outro
# form permanecem fora deste caminho (decisão explícita, não revisitada).
_FORMS_COM_CORPO = frozenset({"8-K", "6-K"})


def _texto_da_secao_do_candidato(candidato: dict, secoes: list[dict]) -> str:
    """Localiza a seção (edgar_sections) que originou um candidato 6-K e
    devolve o texto real dela — para propagar em `evidence_text` (§8/4H.7C).

    Correlação por (kind, heading) — a única chave disponível no candidato,
    já que candidatos 6-K não carregam offset próprio (não têm Item). Se
    houver mais de uma seção com o MESMO kind+heading (ambíguo) ou nenhuma
    correspondência exata: falha para o lado seguro (string vazia) — nunca
    usa o documento inteiro como fallback."""
    kind = candidato.get("section_kind")
    heading = candidato.get("section_heading")
    if not kind or not heading:
        return ""
    candidatas = [s for s in secoes if s.get("kind") == kind and s.get("heading") == heading]
    if len(candidatas) != 1:
        return ""
    return candidatas[0].get("text") or ""


# ── 1) Enriquecimento: metadado → corpo real + candidatos do parser ─────────
def enrich_with_body(stub_articles: list[dict], cfg: dict, rd, *,
                      rate_limit_rps: int = 6) -> list[dict]:
    """Para cada artigo EDGAR leve (metadado de `fetch_edgar_filings`, sem
    corpo), baixa o documento primário e roda o MESMO parser/classificador
    canônico validado em 4H.3C-F/4H.3E, produzindo um artigo completo via
    `ec.to_article()` (com `edgar_candidates`/evidência real).

    Sem isto, o matching de corroboração teria só o título/descrição curta da
    SEC como texto — praticamente sem contraparte extraível, o que faria
    `entity_fingerprint` ficar vazio e `match_occurrence` nunca corroborar
    nada. Escopo limitado (só os filings já filtrados por elegibilidade/
    janela/forms pelo `fetch_edgar_filings` existente — tipicamente poucos
    por execução, não o histórico inteiro)."""
    import requests
    session = requests.Session()
    out = []
    for stub in stub_articles:
        acc = stub.get("accession_number", "")
        url = stub.get("url", "")
        form = stub.get("form", "8-K")
        filing = {
            "company": stub.get("filing_company", ""), "cik": stub.get("cik", ""),
            "ticker": stub.get("ticker", ""), "form": form,
            "accession_number": acc, "accession_digits": ec.normalize_accession(acc),
            "filing_date": stub.get("filing_date", ""),
            "report_date": stub.get("report_date", ""),
            "primary_document": stub.get("primary_document", ""),
            "description": stub.get("summary", ""),
            "items": [i.strip() for i in str(stub.get("filing_items", "")).split(",") if i.strip()],
            "url": url, "provenance": "EDGAR", "pub_ts": stub.get("pub_ts"),
        }
        # 4H.8: telemetria de retrieval, SEPARADA de "form fora de escopo".
        # form_suportado=False (ex.: 10-Q) nunca conta como body_fetch_failure
        # — nem sequer tenta, por desenho (fora deste caminho desde a 4H.7C).
        # Só um form suportado (8-K/6-K) que teve fetch TENTADO e não teve
        # sucesso conta como falha real de retrieval. Comportamento (o `html`
        # resultante, o fallback silencioso) é IDÊNTICO a antes — só passou
        # a ser observado, nunca alterado.
        form_suportado = form in _FORMS_COM_CORPO
        retrieval = {
            "form": form, "form_suportado": form_suportado,
            "body_fetch_attempted": False, "body_fetch_success": False,
            "failure_reason": "", "section_count": None,
        }
        html = ""
        if url and form_suportado:
            retrieval["body_fetch_attempted"] = True
            try:
                r = session.get(url, headers=ec.archive_headers(rd._EDGAR_UA), timeout=25)
                if r.status_code == 200:
                    html = r.text
                    retrieval["body_fetch_success"] = True
                else:
                    retrieval["failure_reason"] = f"http_{r.status_code}"
            except Exception as exc:
                html = ""
                retrieval["failure_reason"] = type(exc).__name__
            time.sleep(1.0 / max(1, rate_limit_rps))
        an = None
        texto = sem = ""
        secoes_6k: list[dict] = []
        if html and form == "8-K":
            dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
            doc = dom["doc"]
            texto = doc.flat_text if doc else ""
            sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
            an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
            retrieval["section_count"] = len(dom["sections"] or [])
        elif html and form == "6-K":
            # 6-K não tem estrutura de Item (8-K) — usa o extrator de seção
            # de press release já existente (edgar_sections, 4H.3E), NUNCA
            # o parser DOM de 8-K (produziria seções sem sentido para 6-K).
            texto = ec.strip_html(html)
            sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
            info = es.evidence_sections(texto, form="6-K")
            secoes_6k = info["sections"]
            an = ec.analyze_filing(filing, texto, sem, sections=secoes_6k)
            retrieval["section_count"] = len(secoes_6k)
        art = ec.to_article(filing, texto, an, sem)
        art["_retrieval_4h8"] = retrieval
        if secoes_6k and an:
            # §8/4H.7C: propaga o texto real da seção para `evidence_text`
            # nos candidatos 6-K (o classificador canônico não populava esse
            # campo para candidatos `nao_pontuavel_por_forma`, porque nunca
            # foram feitos para exibição/pontuação). Falha para o lado
            # seguro quando a correlação seção↔candidato é ambígua.
            for c in (art.get("edgar_candidates") or []):
                if c.get("form") == "6-K" and not c.get("evidence_text"):
                    txt = _texto_da_secao_do_candidato(c, secoes_6k)
                    if txt:
                        c["evidence_text"] = txt[:2000]
        # preserva o pub_ts original (to_article não define um valor próprio
        # confiável para todos os forms) e o rótulo de fonte já usado hoje
        art["pub_ts"] = filing.get("pub_ts")
        out.append(art)
    return out


# ── 2) Ocorrências conhecidas (fontes normais) para o matching ──────────────
def _aliases_for(company: str, cfg: dict) -> list[str]:
    c = next((x for x in cfg.get("watchlist", []) if x.get("name") == company), None)
    return list((c or {}).get("aliases") or [company])


def known_occurrences_for(history: dict, company: str, event_id: str, cfg: dict,
                          rd) -> list[dict]:
    """Ocorrências já registradas por fontes NÃO-SEC, para esta empresa+família
    — o universo contra o qual `ec.match_occurrence` compara. Nunca inclui
    registros já `domain=="sec.gov"` (SEC não corrobora SEC)."""
    aliases = _aliases_for(company, cfg)
    out = []
    for url, rec in history.get("articles", {}).items():
        if rec.get("domain") == SEC_DOMAIN:
            continue
        # CORREÇÃO DE INFRAESTRUTURA (1x, permitida): `event_ids_for` tem um
        # fallback LEGADO para registros sem `events_by_company` (ex.: notícia
        # de mercado geral, `companies=["Mercado (geral)"]`) que devolve
        # `rec["event_ids"]` GLOBAL, sem checar empresa nenhuma — encontrado
        # nesta própria rodada de replay: um 8-K real da Baker Hughes
        # corroborou (por engano) uma notícia de mercado sobre a fusão
        # Dominion/NextEra, porque essa notícia tinha `event_ids=["ma"]`
        # global e nenhum `events_by_company`. Correção: a empresa precisa
        # estar EXPLICITAMENTE nas empresas do registro antes de sequer
        # perguntar `event_ids_for` — nunca confiar no fallback legado para
        # decidir de quem é o evento.
        empresas_do_registro = set(rec.get("companies_attributed")
                                   or rec.get("companies") or [])
        if company not in empresas_do_registro:
            continue
        if event_id not in rd.event_ids_for(rec, company):
            continue
        date = (rec.get("pub_iso") or "")[:10]
        text = f"{rec.get('title', '')} {rec.get('summary', '')}"
        out.append({
            "occurrence_id": url, "company": company, "event_id": event_id,
            "date": date, "fingerprint": ec.entity_fingerprint(text, exclude=aliases),
            "source": rec.get("source", ""), "title": rec.get("title", ""),
        })
    return out


# ── 3) Persistência da corroboração (mesma forma de `_persist_source`) ──────
def _fmt_when(pub_ts) -> str:
    if not pub_ts:
        return ""
    return (datetime.fromtimestamp(pub_ts, tz=timezone.utc)
            - timedelta(hours=3)).strftime("%d/%m %H:%M")


def _sec_source_label(edgar_art: dict, item: str) -> str:
    form = edgar_art.get("form", "8-K")
    return f"SEC · {form} · Item {item}" if item else f"SEC · {form}"


def append_sec_corroboration(target: dict, edgar_art: dict, item: str) -> bool:
    """Anexa SEC como fonte corroboradora do registro EXISTENTE `target`.

    Dedup: domínio "sec.gov" é SEMPRE o mesmo, qualquer que seja o accession —
    o dedup por domínio já existente em `build_evolution`/`dedupe_articles`
    (nunca duas entradas com o mesmo domínio em `corrob_sources`) garante, de
    graça, que um segundo filing SEC sobre o MESMO fato (8-K/A, item 1.01 e
    2.03 do mesmo documento, um novo accession relatando o mesmo fato) NUNCA
    vira um segundo bônus — sem nenhum código novo de dedup. Retorna False
    quando já havia corroboração SEC (idempotente, seguro para reprocessar)."""
    srcs = target.setdefault("corrob_sources", [])
    if any(s.get("domain") == SEC_DOMAIN for s in srcs):
        return False
    url = edgar_art.get("url", "")
    entry = {
        "source": _sec_source_label(edgar_art, item), "domain": SEC_DOMAIN,
        "url": url, "when": _fmt_when(edgar_art.get("pub_ts")),
        "display_url": url, "canonical_url": url, "resolved_url": url,
        "link_health": "url_direta_valida", "link_render_anchor": True,
        "link_label": "Abrir filing SEC →",
    }
    srcs.append(entry)
    del srcs[8:]
    corr = target.setdefault("corroborations", [])
    if not any(c.get("domain") == SEC_DOMAIN for c in corr):
        corr.append({"source": entry["source"], "domain": SEC_DOMAIN, "url": url})
    del corr[8:]
    return True


# ── 4) Entrada principal ─────────────────────────────────────────────────────
def apply_edgar_corroboration(edgar_stub_articles: list[dict], history: dict,
                              cfg: dict, rd) -> dict:
    """CASO B only (chamador garante `collection=True, scoring=False`).

    Para cada filing EDGAR: classifica pelo MESMO `classify_and_attribute`
    usado por notícias, tenta casar cada (empresa, event_id) candidato contra
    ocorrências já conhecidas via `ec.match_occurrence` (empresa + família +
    contraparte em comum + data dentro da tolerância da família — nunca só
    empresa+data). Se casar: anexa corroboração ao registro existente. Se não
    casar: NÃO cria registro novo em `history` (decisão 4H.4B, não reaberta).
    """
    resumo = {
        # ── LEGADO (4H.5/4H.6/4H.7C) — mesma semântica de sempre. Consumido
        # por risk_dashboard.py (log) e pelos testes 4H.5/4H.5F/6-K; mantido
        # como estava, nunca removido/renomeado (4H.8 é só telemetria).
        "filings_recebidos": len(edgar_stub_articles), "filings_com_corpo": 0,
        "candidatos_avaliados": 0, "corroborados": 0, "sem_match": 0,
        "matches": [], "sem_match_detalhe": [],
        # ── NOVO (4H.8) — granular: form_fora_do_escopo NUNCA é contado como
        # body_fetch_failure. Nenhum destes campos influencia matching/score;
        # são só a mesma informação já existente, quebrada em buckets que não
        # se confundem entre si (ver `docstring` do módulo).
        "por_form": {}, "forms_suportados_total": 0, "forms_fora_do_escopo_total": 0,
        "body_fetch_attempted": 0, "body_fetch_success": 0, "body_fetch_failure": 0,
        "body_fetch_failure_detalhe": [],
        "section_extracted": 0, "section_not_found": 0,
        "matches_new": 0, "matches_idempotent": 0, "matches_rejected": 0,
        "edgar_only": 0,
    }
    if not edgar_stub_articles:
        return resumo

    arts = enrich_with_body(edgar_stub_articles, cfg, rd)
    resumo["filings_com_corpo"] = sum(1 for a in arts if a.get("edgar_has_body"))

    # 4H.8: agregação por form a partir da telemetria de retrieval de cada
    # filing (`_retrieval_4h8`, anexada por `enrich_with_body`) — ANTES do
    # loop de matching, porque é sobre TODOS os filings recebidos, não só os
    # que produziram candidato aceito.
    for art in arts:
        r = art.get("_retrieval_4h8") or {}
        form = r.get("form") or art.get("form", "")
        pf = resumo["por_form"].setdefault(form, {
            "total": 0, "form_suportado": r.get("form_suportado", False),
            "body_fetch_attempted": 0, "body_fetch_success": 0,
            "body_fetch_failure": 0, "section_extracted": 0, "section_not_found": 0,
        })
        pf["total"] += 1
        if r.get("form_suportado"):
            resumo["forms_suportados_total"] += 1
            if r.get("body_fetch_attempted"):
                pf["body_fetch_attempted"] += 1
                resumo["body_fetch_attempted"] += 1
                if r.get("body_fetch_success"):
                    pf["body_fetch_success"] += 1
                    resumo["body_fetch_success"] += 1
                    sc = r.get("section_count")
                    if sc:
                        pf["section_extracted"] += 1
                        resumo["section_extracted"] += 1
                    else:
                        pf["section_not_found"] += 1
                        resumo["section_not_found"] += 1
                else:
                    pf["body_fetch_failure"] += 1
                    resumo["body_fetch_failure"] += 1
                    resumo["body_fetch_failure_detalhe"].append({
                        "accession": art.get("accession_number", ""), "form": form,
                        "issuer": art.get("filing_company", ""),
                        "failure_reason": r.get("failure_reason", ""),
                    })
        else:
            resumo["forms_fora_do_escopo_total"] += 1

    for art in arts:
        # ── candidatos: SÓ o classificador canônico validado (edgar_canonical
        # /edgar_dom, `art["edgar_candidates"]`), NUNCA o `classify_article`
        # genérico de palavra-chave usado para notícias. Achado do teste [1]
        # desta fase: rodar o texto EDGAR pelo classificador genérico produz
        # falsos candidatos de `default`/`falencia` a partir de boilerplate de
        # covenant (a EXATA família de erro que a 4H.4 mediu como 0% de
        # precisão) — o classificador canônico, escopado por Item/seção DOM,
        # não comete esse erro. O `subject_company` de cada candidato é o
        # próprio filer (`monitored_company`): `to_article()` só produz UMA
        # empresa candidata por filing (nunca `forced_companies` — 4H.3A),
        # então não há ambiguidade de sujeito a resolver aqui.
        company = art.get("monitored_company") or art.get("filing_company", "")
        filer = art.get("filing_company", "")
        # WHITELIST ESTRUTURAL (4H.7C, §7): `nao_pontuavel_por_forma=True`
        # continua bloqueando TODO candidato para fins de scoring (essa
        # trava nunca é tocada — `_KINDS_PONTUAVEIS` em edgar_canonical.py
        # permanece intacta). Para CORROBORAÇÃO especificamente, candidatos
        # 6-K (sempre `nao_pontuavel_por_forma=True`, por desenho da 4H.3E/F,
        # já que 6-K não tem estrutura de Item garantida pela SEC) podem
        # participar do matching — nunca originar score sozinhos, e o
        # match continua exigindo empresa+família+contraparte+data via
        # `ec.match_occurrence`, o mesmo filtro do 8-K. Nenhuma outra forma
        # (10-Q/10-K/20-F/40-F) entra nesta whitelist — só "6-K" literal.
        aceitos = [c for c in (art.get("edgar_candidates") or [])
                   if c.get("aceito")
                   and (not c.get("nao_pontuavel_por_forma") or c.get("form") == "6-K")]
        cand_by_ev = {}
        for c in aceitos:
            eid = c.get("event_id")
            if eid and eid not in cand_by_ev:
                cand_by_ev[eid] = c

        if company:
            for event_id, cand in cand_by_ev.items():
                resumo["candidatos_avaliados"] += 1
                item = cand.get("item", "") or art.get("filing_items", "").split(",")[0].strip()
                evidence_text = (cand.get("evidence_text") or art.get("summary") or "")
                aliases = _aliases_for(company, cfg)
                fingerprint = ec.entity_fingerprint(evidence_text, exclude=aliases)
                data_edgar = ec.economic_date(
                    {"form": art.get("form", ""), "filing_date": art.get("filing_date", ""),
                     "report_date": art.get("report_date", "")},
                    text=evidence_text)
                conhecidas = known_occurrences_for(history, company, event_id, cfg, rd)
                resultado = ec.match_occurrence(company, event_id, data_edgar,
                                                fingerprint, conhecidas)
                base = {
                    "filer": filer, "company": company, "event_id": event_id,
                    "item": item, "accession": art.get("accession_number", ""),
                    "url": art.get("url", ""), "data_edgar": data_edgar,
                    "fingerprint": sorted(fingerprint)[:6],
                }
                if resultado["acao"] == "corroborar":
                    matched_url = resultado["match"]["occurrence_id"]
                    target = history["articles"].get(matched_url)
                    if target is None:
                        # Corrida: occurrence_id não encontrado (raríssimo, não é
                        # uma decisão de matching — mais próximo de "rejeitado"
                        # para fins de telemetria, mas com motivo próprio.
                        resumo["sem_match"] += 1
                        resumo["matches_rejected"] += 1
                        resumo["sem_match_detalhe"].append(
                            {**base, "motivo": "occurrence_id não encontrado em history (corrida)"})
                        continue
                    added = append_sec_corroboration(target, art, item)
                    resumo["corroborados"] += 1 if added else 0
                    # 4H.8: NEW = primeira vez que a SEC é anexada; IDEMPOTENT =
                    # match correto, mas a SEC já estava presente (reprocessar
                    # não duplica bônus — comportamento inalterado, só nomeado).
                    resumo["matches_new" if added else "matches_idempotent"] += 1
                    resumo["matches"].append({
                        **base, "matched_url": matched_url,
                        "matched_source": resultado["match"].get("source", ""),
                        "matched_title": resultado["match"].get("title", ""),
                        "lag_dias": resultado["match"].get("lag"),
                        "entidades_comuns": resultado["match"].get("entidades_comuns", []),
                        "novo_bonus": added,
                        "match_kind": "new" if added else "idempotent",
                        "motivo": resultado["motivo"],
                    })
                else:
                    resumo["sem_match"] += 1
                    # 4H.8: EDGAR-ONLY = candidato válido, mas ZERO ocorrência
                    # independente já conhecida (nada para corroborar contra —
                    # não é rejeição, é ausência de universo de comparação).
                    # REJECTED = havia ocorrência(s) da mesma empresa+família,
                    # mas nenhuma compartilhou contraparte/data suficiente
                    # (`ec.match_occurrence` já fazia essa distinção internamente
                    # via `rejeitados`; aqui só é OBSERVADA, não decidida de novo).
                    kind = "edgar_only" if not conhecidas else "rejected"
                    resumo["edgar_only" if kind == "edgar_only" else "matches_rejected"] += 1
                    resumo["sem_match_detalhe"].append(
                        {**base, "motivo": resultado["motivo"], "match_kind": kind})
    return resumo


# ── 5) Log humano (4H.8) ─────────────────────────────────────────────────────
def format_telemetry_log(resumo: dict) -> str:
    """Linha de log operacional legível, distinguindo explicitamente form
    fora de escopo (10-Q) de falha real de retrieval (8-K/6-K que não
    respondeu) — o objetivo desta fase (4H.8) é impedir a confusão que já
    aconteceu uma vez (relatório da 4H.7 mencionando "135 sem corpo" como se
    fossem falhas de rede, quando eram formas fora de escopo por desenho)."""
    linhas = [f" 🇺🇸 SEC/EDGAR: {resumo['filings_recebidos']} filing(s)"]
    if resumo["forms_suportados_total"] or resumo["forms_fora_do_escopo_total"]:
        linhas.append(f"   {resumo['forms_suportados_total']} form(s) suportado(s) processado(s)"
                       f" (8-K/6-K), {resumo['forms_fora_do_escopo_total']} fora do escopo"
                       f" (form não coberto por este caminho — ex.: 10-Q)")
        for form, pf in sorted(resumo["por_form"].items()):
            if pf["form_suportado"]:
                linhas.append(f"     {pf['body_fetch_success']}/{pf['body_fetch_attempted']} {form} com corpo")
            else:
                linhas.append(f"     {pf['total']} {form} fora do escopo (sem tentativa de fetch)")
    linhas.append(f"   {resumo['candidatos_avaliados']} candidato(s) avaliado(s), "
                   f"{resumo['matches_new']} corroboração(ões) nova(s), "
                   f"{resumo['matches_idempotent']} idempotente(s) (já anexada), "
                   f"{resumo['matches_rejected']} rejeitado(s), "
                   f"{resumo['edgar_only']} sem ocorrência independente (EDGAR-only, não pontuam)")
    if resumo["body_fetch_failure"]:
        linhas.append(f"   ⚠️  {resumo['body_fetch_failure']} falha(s) REAL(is) de retrieval "
                       f"(form suportado, fetch tentado, sem sucesso)")
    else:
        linhas.append("   0 falhas reais de retrieval")
    return "\n".join(linhas)
