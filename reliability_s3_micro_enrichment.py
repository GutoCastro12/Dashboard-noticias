#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_s3_micro_enrichment.py — 4I.2 R7b-S3.

MICRO-AMOSTRA RETROSPECTIVA, ISOLADA, SÓ PARA AVALIAÇÃO.

O estrato S3 (M&A não pontuável) estava bloqueado por uma razão estrutural, não
por volume: os 59 candidatos do corpus são artigos ANTIGOS, e o shadow
prospectivo só enriquece o que coleta a partir do marco do run 111. Esperar
crons nunca os alcançaria. Gustavo autorizou ATÉ 5 fetches retrospectivos para
quebrar esse bloqueio.

O QUE ESTE MÓDULO NÃO FAZ, por construção:
  - não lê nem escreve `risk_enrichment_shadow.json` (passa sidecar={});
  - não escreve `risk_history.json`;
  - não escreve `risk_input_shadow.json`;
  - não toca dashboard, score, config ou cron;
  - não insere artigo nenhum como registro de produção;
  - não faz bypass de robots — herda `_robots_permite` do extrator de produção;
  - uma requisição por artigo, teto rígido de 5.

A saída vai para um único arquivo experimental que produção nenhuma lê.

E não rotula nada: o objetivo é preparar material legível para adjudicação
humana. Classificação determinística entra no relatório como COMPARAÇÃO, nunca
como verdade.
"""
from __future__ import annotations

import collections
import io
import json
import os
import sys
import time
from pathlib import Path

import reliability_input_rehearsal as rh
import reliability_pilot_input as pi
import risk_dashboard as rd
import semantic_audit as sa

MAX_FETCH_S3 = 5
SAIDA = Path(os.environ.get("R7B_S3_OUT")
             or "out_reliability/r7b_s3_experimental_inputs.json")
HISTORY = Path("risk_history.json")

# Selecionados no checkpoint anterior por DIVERSIDADE de motivo de rejeição —
# um por perfil semântico, não os cinco primeiros de uma lista.
CANDIDATOS = [
    ("S3-C1", "Klabin", "ma", "Não há conversas sobre fusão e aquisição",
     "negacao_explicita"),
    ("S3-C2", "Cemig", "ma", "Âmbar Energia conclui a aquisição de 4 hidrelétricas",
     "seller_only_asset_sale"),
    ("S3-C3", "BTG Pactual", "ma", "BTG Pactual mira aquisição do Banco Digimais",
     "rumor_intencao"),
    ("S3-C4", "BRF", "ma", "S&P eleva rating global da BRF",
     "referencia_historica_pos_fusao"),
    ("S3-C5", "TIM Brasil", "ma", "Presidente da TIM Brasil vê potencial estratégico",
     "comentarista_terceiro"),
]


CACHE_FETCH = Path(os.environ.get("R7B_S3_CACHE")
                   or "out_reliability/r7b_s3_fetch_cache.json")


def _ler_cache() -> dict:
    if CACHE_FETCH.exists():
        try:
            return json.load(io.open(CACHE_FETCH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _gravar_cache(c: dict) -> None:
    CACHE_FETCH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(c, io.open(CACHE_FETCH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def localizar(arts: dict, fragmento: str):
    for u, r in arts.items():
        if fragmento.lower() in (r.get("title") or "").lower():
            return u, r
    return None, None


def main() -> int:
    permitir_rede = "--rede" in sys.argv
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    arts = hist["articles"]
    cfg = rd.load_config("config_risco.yaml")
    kws = sa._keywords_por_evento(cfg)
    al = sa._aliases_map(cfg)

    contador = {"fetches": 0, "resolucoes": 0, "resolucao_falhou": 0,
                "duplicatas_evitadas": 0, "por_artigo": {},
                "por_host": collections.Counter(),
                "limite_fetch": MAX_FETCH_S3}
    cache_fetch = _ler_cache()

    registros = []
    for tag, empresa, evento, frag, perfil in CANDIDATOS:
        url, rec = localizar(arts, frag)
        if not url:
            registros.append({"s3_id": tag, "company": empresa,
                              "status": "NAO_ENCONTRADO_NO_CORPUS",
                              "fragmento": frag})
            continue
        titulo = rec.get("title") or ""
        # Reaproveita fetch já feito numa execução anterior: uma falha MINHA
        # não pode consumir de novo o orçamento de rede autorizado.
        enr = cache_fetch.get(url)
        if enr is None:
            # sidecar={} é a prova de isolamento: nada do sidecar de produção é
            # lido, nada é gravado nele.
            enr = rh.enriquecer_uma_vez(url, titulo, rec, sidecar={},
                                        permitir_rede=permitir_rede,
                                        contador=contador)
            if permitir_rede:
                cache_fetch[url] = enr
                _gravar_cache(cache_fetch)
        else:
            contador["reaproveitados"] = contador.get("reaproveitados", 0) + 1
        texto = enr.get("texto") or ""
        best = rh.montar_best_input(
            titulo, [(enr.get("metodo") or "fetch", texto)] if texto else [])
        comp = rh.componentes(titulo, best.get("best_input") or "")
        pronto = rh.pronto(comp, "SELECTED")

        d = sa.resolve_article_semantics(
            titulo, rec.get("summary") or "", empresa, [evento], al,
            article_year=sa._ano_do_registro(rec),
            source_domain=rec.get("domain") or "",
            keywords_por_evento=kws, country=rec.get("country") or "")["decisoes"][0]

        registros.append({
            "s3_id": tag,
            "perfil_esperado": perfil,
            "company": empresa,
            "candidate_event": evento,
            "title": titulo,
            "source": rec.get("source") or "",
            "domain": rec.get("domain") or "",
            "pub_iso": rec.get("pub_iso") or rec.get("date") or "",
            "url": url,
            "canonical_url": enr.get("url_resolvida") or url,
            "fetch": {
                "status": enr.get("falha"),
                "origem": enr.get("origem"),
                "extraction_method": enr.get("metodo") or "",
                "tier": enr.get("tier"),
                "metodo_resolucao": enr.get("metodo_resolucao") or "",
            },
            "quality": {
                "useful_chars": comp.get("chars_totais"),
                "sentence_like_count": comp.get("sentence_like_count"),
                "unique_meaningful_tokens": comp.get("unique_meaningful_tokens"),
                "meaningful_gain_vs_title": comp.get("meaningful_gain_vs_title"),
                "paywall_flag": comp.get("paywall_flag"),
                "nav_flag": comp.get("nav_flag"),
                "boilerplate_flag": comp.get("boilerplate_flag"),
                "sufficient": bool(pronto.get("pronto")),
                "faltou": pronto.get("faltou"),
            },
            "evidence_text": (best.get("best_input") or "")[:4000],
            "content_hash": best.get("content_hash") or "",
            "provenance": best.get("provenance") or [],
            "deterministic_comparison": {
                "scoreable": bool(d.get("scoreable")),
                "rule": d.get("attribution_rule") or "",
                "reason": (d.get("rejection_reason") or "")[:200],
                "subject_company": d.get("subject_company") or "",
                "transaction_role": d.get("transaction_role") or "",
                "transaction_object": d.get("transaction_object") or "",
                "transaction_scope": d.get("transaction_scope") or "",
                "event_phase": d.get("event_phase") or "",
                "historical_reference": bool(d.get("historical_reference")),
                "new_occurrence": bool(d.get("new_occurrence")),
            },
            "human_label": None,
            "human_label_note": "NAO ADJUDICADO — aguarda verdade humana. A "
                                "classificacao deterministica acima e COMPARACAO, "
                                "nunca ground truth.",
        })

    ready = [r for r in registros if (r.get("quality") or {}).get("sufficient")]
    out = {
        "_meta": {
            "proposito": "Inputs experimentais retrospectivos para construir "
                         "controles S3. Evaluation only. Producao nenhuma le "
                         "este arquivo.",
            "isolamento": ["sidecar de producao nao lido nem escrito "
                           "(sidecar={})",
                           "risk_history.json nao alterado",
                           "risk_input_shadow.json nao alterado",
                           "dashboard/score/config/cron nao alterados",
                           "artigos NAO inseridos como registros de producao"],
            "autorizacao": "Gustavo, wave R7b-S3: ate 5 fetches retrospectivos, "
                           "cache experimental isolado, robots respeitados.",
            "gerado_em": int(time.time()),
            "max_fetch": MAX_FETCH_S3,
            "permitiu_rede": permitir_rede,
            "extractor_version": rh.EXTRACTOR_VERSION
            if hasattr(rh, "EXTRACTOR_VERSION") else "",
            "policy_version": rh.POLICY_VERSION
            if hasattr(rh, "POLICY_VERSION") else "",
        },
        "network_accounting": {
            "candidates_selected": len(CANDIDATOS),
            "requests_attempted": contador["fetches"],
            "resolucoes": contador["resolucoes"],
            "resolucao_falhou": contador["resolucao_falhou"],
            "duplicatas_evitadas": contador["duplicatas_evitadas"],
            "requests_por_host": dict(contador["por_host"]),
            "falhas": dict(collections.Counter(
                (r.get("fetch") or {}).get("status") or r.get("status")
                for r in registros)),
            "ready": len(ready),
        },
        "registros": registros,
    }
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, io.open(SAIDA, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("=" * 96)
    print(f"S3 MICRO-ENRICHMENT — rede={'SIM' if permitir_rede else 'NAO (dry)'}")
    print("=" * 96)
    for r in registros:
        q = r.get("quality") or {}
        f = r.get("fetch") or {}
        print(f"  {r['s3_id']} {r.get('company','?'):14s} {f.get('status') or r.get('status'):22s} "
              f"chars={q.get('useful_chars', 0):5d} sent={q.get('sentence_like_count', 0):3d} "
              f"toks={q.get('unique_meaningful_tokens', 0):4d} pronto={q.get('sufficient')}")
    print()
    print(f"  requests: {contador['fetches']}/{MAX_FETCH_S3} | ready: {len(ready)}")
    print(f"  saida   : {SAIDA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
