#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_gold_4i.py — GOLD SET da auditoria 4I como regressão executável.

Responde automaticamente à pergunta que motivou a fase 4I.2:

    "alguma mudança reintroduziu um dos falsos positivos da auditoria 4I?"
    "alguma correção apagou um dos eventos que já estavam corretos?"

Roda a cadeia de classificação/atribuição/semântica REAL do pipeline
(`risk_dashboard._reclassify_only_pass`, o mesmo caminho de `--reclassify-only`,
zero rede e zero LLM) contra um histórico sintético montado a partir dos
casos auditados, e compara com o veredito humano.

O gold guarda os DOIS lados (4I.2 §3):
  POSITIVOS (116 após adjudicação) — corretas que NÃO podem sumir;
  NEGATIVOS ( 89 após adjudicação) — falsos positivos que NÃO podem voltar;
  DEDUP     ( 21) — o mesmo fato econômico deve colapsar em 1 ocorrência;
  SKIP      (  6) — indecidíveis pela evidência (fora do pass/fail, §27 da 4I.1).

Este arquivo NÃO é um teste de "tudo verde": no estado atual do repositório
ele DEVE falhar nos 90 negativos — é a linha de base da dívida. O objetivo da
4I.2 é levar as falhas a zero SEM derrubar os positivos.
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

import risk_dashboard as rd

GOLD = Path(__file__).parent / "test_fixtures_4i" / "gold_set_4i.json"
ADJ = Path(__file__).parent / "test_fixtures_4i" / "gold_adjudications_4i2.json"
cfg = rd.load_config("config_risco.yaml")
dados = json.loads(GOLD.read_text(encoding="utf-8"))
casos = dados["casos"]

# ── adjudicações (4I.2 §2) ───────────────────────────────────────────────
# Aplicadas POR CIMA da auditoria original, que fica intacta como evidência
# histórica. Corrigem o veredito HUMANO — nunca acomodam uma regra ruim.
_MAP_ASSERT = {"CORRECT": "keep", "DUPLICATE_OCCURRENCE": "dedup",
               "WRONG_SUBJECT": "drop", "WRONG_RELATION": "drop",
               "NEGATED_EVENT": "drop", "HISTORICAL_REFERENCE": "drop",
               "HISTORICAL_MA": "drop", "RESOLUTION_OF_PRIOR_NEGATIVE_EVENT": "drop",
               "WRONG_EVENT": "reclass", "WRONG_LEGAL_PHASE": "phase",
               "NEEDS_MANUAL_REVIEW": "skip"}
_adjs = json.loads(ADJ.read_text(encoding="utf-8"))["adjudicacoes"] if ADJ.exists() else []
_aplicadas = []
for _a in _adjs:
    for c in casos:
        if (c["monitored_company"] == _a["company"]
                and c.get("current_event_id") == _a["event_id"]
                and _a["title_fragment"].lower() in (c["title"] or "").lower()):
            c["audit_verdict_original"] = c["audit_verdict"]
            c["audit_verdict"] = _a["new_verdict"]
            c["assertion"] = _MAP_ASSERT[_a["new_verdict"]]
            c["adjudicated"] = True
            if _a["new_verdict"] == "CORRECT":
                c["expected_scoreable"] = True
                c["expected_event_id"] = c["current_event_id"]
                c["forbidden_event_id"] = ""
            _aplicadas.append(_a)
            break


def _hist_sintetico(casos):
    """Monta um history mínimo — uma entrada por caso — com exatamente os
    campos que `_reclassify_only_pass` consome. Chave = id do gold (estável
    e independente de URL, que em 5 casos nem existe — inconsistência D)."""
    arts = {}
    for c in casos:
        arts[c["id"]] = {
            "title": c["title"], "summary": c["summary"],
            "source": c["source"], "domain": c["domain"],
            "pub_ts": int(c["pub_ts"]) if str(c["pub_ts"]).isdigit() else 0,
            "pub_iso": c["pub_iso"],
            "companies": [c["monitored_company"]],
        }
        if c.get("forced_trust") == "oficial":
            arts[c["id"]]["trust_override"] = "oficial"
    return {"articles": arts, "run_count": 1}


hist = _hist_sintetico(casos)
rd._reclassify_only_pass(hist, cfg)

resultado = {}
for c in casos:
    rec = hist["articles"][c["id"]]
    emp = c["monitored_company"]
    ids = set(rd.event_ids_for(rec, emp) or [])

    def _eids(container):
        # os containers de contexto/informativo guardam DICTS de avaliação
        # (event_id + subject + relation + scoreable…), não ids soltos.
        out = set()
        for it in ((rec.get(container) or {}).get(emp) or []):
            out.add(it.get("event_id") if isinstance(it, dict) else it)
        return out

    ctx = _eids("context_events_by_company")
    inf = _eids("informational_events_by_company")
    resultado[c["id"]] = {"pontuaveis": ids, "contexto": ctx, "informativo": inf}

PASS = FAIL = SKIP = 0
falhas_por_causa = defaultdict(list)
falhas_pos = []


def avaliar(c):
    """Devolve (ok, detalhe). `None` = caso ignorado (skip)."""
    r = resultado[c["id"]]
    a = c["assertion"]
    if a in ("skip", "bucket_only"):
        return None, ""
    if a == "keep":
        # o evento correto tem de continuar pontuando
        ok = c["expected_event_id"] in r["pontuaveis"]
        return ok, (f"esperado pontuar '{c['expected_event_id']}', "
                     f"pontuáveis={sorted(r['pontuaveis']) or '[]'}")
    if a in ("drop", "reclass", "phase"):
        # o evento errado NÃO pode continuar pontuando para esta empresa
        proib = c["forbidden_event_id"]
        ok = proib not in r["pontuaveis"]
        destino = ("contexto" if proib in r["contexto"] else
                   "informativo" if proib in r["informativo"] else
                   "ausente" if not r["pontuaveis"] else "AINDA PONTUA")
        return ok, f"'{proib}' não pode pontuar; destino atual={destino}"
    if a == "dedup":
        # tratado em bloco, depois
        return None, ""
    return None, ""


print("=" * 100)
print("GOLD SET 4I — regressão contra os vereditos humanos da auditoria")
print(f"origem: {dados['_meta']['origem']}")
print(f"adjudicações aplicadas: {len(_aplicadas)}")
print("=" * 100)

for c in casos:
    ok, det = avaliar(c)
    if ok is None:
        SKIP += 1
        continue
    if ok:
        PASS += 1
    else:
        FAIL += 1
        if c["assertion"] == "keep":
            falhas_pos.append(c)
        else:
            falhas_por_causa[c["root_cause"]].append((c, det))

# ── dedup: cada grupo deve colapsar em UMA ocorrência econômica ──────────
grupos = defaultdict(list)
for c in casos:
    if c["assertion"] == "dedup":
        grupos[c["dedup_group"]].append(c)
dedup_fail = 0
for g, lst in sorted(grupos.items()):
    ainda = sum(1 for c in lst
                if c["current_event_id"] in resultado[c["id"]]["pontuaveis"])
    if ainda:
        dedup_fail += 1
        FAIL += 1
    else:
        PASS += 1

print(f"\nPOSITIVOS (não podem sumir):  "
      f"{sum(1 for c in casos if c['assertion']=='keep') - len(falhas_pos)}"
      f"/{sum(1 for c in casos if c['assertion']=='keep')} preservados")
print(f"NEGATIVOS (não podem voltar): "
      f"{sum(1 for c in casos if c['assertion'] in ('drop','reclass','phase')) - sum(len(v) for v in falhas_por_causa.values())}"
      f"/{sum(1 for c in casos if c['assertion'] in ('drop','reclass','phase'))} corrigidos")
print(f"DEDUP (grupos colapsados):    {len(grupos)-dedup_fail}/{len(grupos)}")
print(f"SKIP (indecidíveis/contexto): {SKIP}")

if falhas_pos:
    print(f"\n{'!'*100}")
    print(f"REGRESSÃO EM EVENTO CORRETO — {len(falhas_pos)} ocorrência(s) legítima(s) deixaram de pontuar:")
    for c in falhas_pos[:20]:
        print(f"   {c['monitored_company'][:24]:24s} {c['expected_event_id']:14s} | {c['title'][:56]}")

if falhas_por_causa:
    print(f"\nFALSOS POSITIVOS AINDA PRESENTES, por causa-raiz:")
    for causa, lst in sorted(falhas_por_causa.items(), key=lambda kv: -len(kv[1])):
        print(f"   {causa:26s} {len(lst):3d}")

print("\n" + "=" * 100)
print(f"RESULTADO GOLD 4I: {PASS}/{PASS+FAIL} asserções satisfeitas  "
      f"({FAIL} falha(s))")
print("=" * 100)
