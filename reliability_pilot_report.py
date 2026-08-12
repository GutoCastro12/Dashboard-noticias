#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_report.py — 4I.2 R7b-A.

O MATERIAL DE ADJUDICAÇÃO E OS DENOMINADORES.

Este módulo NÃO calcula acurácia de ninguém. Ele prepara o que falta para
calculá-la: a leitura determinística de cada item, os campos em branco para a
verdade humana, e os denominadores de cada métrica.

A separação que a wave inteira existe para preservar:

    D-RULE     o motor concluiu por regra nomeada e exercida
    D-DEFAULT  o motor herdou o default (sujeito = monitorada, evento = atual)

R7a mostrou que a segunda categoria domina. Se as duas tiverem acurácias
parecidas, boa parte do trabalho semântico futuro é desnecessária; se forem
muito diferentes, sabemos exatamente onde investir. Nenhuma das duas hipóteses
pode ser resolvida sem adjudicação humana — por isso as células de verdade
saem em branco, marcadas `UNREVIEWED`, e nunca preenchidas com o resultado do
motor nem com o do modelo.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import os
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa
import reliability_pilot_input as pi
import reliability_pilot_sample as ps

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7b_a"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
GUIA_VERSION = "r7ba.guia1"

UNREVIEWED = "UNREVIEWED"

# Colunas de verdade humana. Ficam VAZIAS. `reviewer` distingue quem decidiu —
# o brief é explícito: o assistente pode preparar, não pode marcar `human`.
COLUNAS_VERDADE = ("h_event_asserted", "h_subject", "h_company_role",
                   "h_currentness", "h_centrality", "h_scoreable",
                   "h_novel_valido", "h_observacao", "reviewer", "reviewed_at")

DIMENSOES_CALIBRAVEIS = ("subject", "company_role", "currentness", "phase",
                         "centrality", "entity_attribution")


def leitura_deterministica(item: dict, hist: dict, cfg: dict, kws, al) -> dict:
    """O que o motor de produção conclui — com a origem de cada conclusão."""
    import reliability_universal_assessment as uea
    rec = (hist.get("articles") or {}).get(item["url"]) or {}
    ev = item.get("evento") or ""
    if not rec or not ev:
        return {"d_disponivel": False}
    dec = ps.assessment_determinista(rec, item["empresa"], cfg, kws, al).get(ev)
    if not dec:
        return {"d_disponivel": False}
    u = uea.montar(dec, identity=item["url"], texto=rec.get("title") or "")
    origem = {}
    for d in DIMENSOES_CALIBRAVEIS:
        campo = {"company_role": "company_role"}.get(d, d)
        dim = getattr(u, campo, None)
        origem[d] = ("D-RULE" if dim and dim.status == uea.ESTABLISHED
                     else "D-DEFAULT")
    return {"d_disponivel": True,
            "d_scoreable": bool(dec.get("scoreable")),
            "d_regra": dec.get("attribution_rule") or "",
            "d_subject": dec.get("subject_company") or "",
            "d_role": dec.get("transaction_role") or "",
            "d_currentness": ("HISTORICAL" if dec.get("historical_reference")
                              else "CURRENT"),
            "d_phase": dec.get("event_phase") or "",
            "d_centrality": dec.get("event_scope") or "",
            "d_origem": origem,
            "d_missing": u.missing_dimensions()}


def montar_linhas(man: dict, hist: dict, cfg: dict, tap: dict) -> list:
    kws = sa._keywords_por_evento(cfg)
    al = sa._aliases_map(cfg)
    linhas = []
    for it in man["itens"]:
        rec = (hist.get("articles") or {}).get(it["url"]) or {}
        tr = tap.get((it["url"], it.get("empresa")))
        base = rec or {"title": (tr or {}).get("titulo", ""),
                       "summary": (tr or {}).get("resumo", ""),
                       "pub_iso": (tr or {}).get("pub_iso", ""),
                       "domain": (tr or {}).get("dominio", "")}
        v0 = pi.montar_v0(base)
        v1 = pi.montar_v1(base, it["url"]) if it.get("v1_disponivel") else None
        d = leitura_deterministica(it, hist, cfg, kws, al)
        linhas.append({
            "ident": it["ident"], "estrato": it["estrato"],
            "procedencia": it["procedencia"],
            "empresa": it.get("empresa", ""), "evento": it.get("evento", ""),
            "titulo": (base.get("title") or "")[:200],
            "url": it["url"],
            "texto_v0": v0["texto"][:600],
            "v0_chars_uteis": v0["chars_uteis"],
            "v0_suficiente": v0["suficiente"],
            "v1_disponivel": bool(it.get("v1_disponivel")),
            "v1_chars_uteis": (v1 or {}).get("chars_uteis", ""),
            "v1_suficiente": (v1 or {}).get("suficiente", ""),
            **{k: v for k, v in d.items() if k != "d_origem"},
            **{f"d_origem_{dim}": (d.get("d_origem") or {}).get(dim, "")
               for dim in DIMENSOES_CALIBRAVEIS},
            "llm_status": "NAO_EXECUTADO",
            **{c: (UNREVIEWED if c == "reviewer" else "") for c in COLUNAS_VERDADE},
        })
    return linhas


def denominadores(linhas: list) -> dict:
    """Os denominadores que o pilot prepara. Numeradores só existem depois da
    adjudicação — por isso não há percentual nenhum aqui."""
    cal = {}
    for d in DIMENSOES_CALIBRAVEIS:
        col = f"d_origem_{d}"
        c = collections.Counter(l.get(col) for l in linhas if l.get("d_disponivel"))
        cal[d] = {"D_DEFAULT": c.get("D-DEFAULT", 0), "D_RULE": c.get("D-RULE", 0),
                  "numerador": "PENDENTE_ADJUDICACAO"}
    est = collections.Counter(l["estrato"] for l in linhas)
    return {
        "itens": len(linhas),
        "por_estrato": dict(est),
        "com_leitura_deterministica": sum(1 for l in linhas if l.get("d_disponivel")),
        "calibracao_de_defaults": cal,
        "ow1_denominador": sum(1 for l in linhas if l.get("evento")),
        "ow2_denominador": est.get("S6", 0),
        "ow3_denominador": est.get("S7", 0),
        "procedencia": dict(collections.Counter(l["procedencia"] for l in linhas)),
        "development_control_nao_e_oos": True,
        "revisados_humanos": sum(1 for l in linhas
                                 if l.get("reviewer") not in ("", UNREVIEWED)),
        "unreviewed": sum(1 for l in linhas
                          if l.get("reviewer") in ("", UNREVIEWED)),
    }


GUIA = f"""GUIA DE ADJUDICAÇÃO — R7b-A ({GUIA_VERSION})

Objetivo: dizer o que o TEXTO sustenta, não o que o sistema concluiu. Não olhe
a coluna do motor antes de decidir; ela está na planilha para comparação
posterior, não para orientar o julgamento.

Preencha SOMENTE as colunas h_*. Deixe em branco o que não conseguir decidir —
"não consegui decidir" é um resultado, e a taxa de indecisão é uma das
métricas do piloto (se passar de 20%, o guia é que está ruim, não você).
Ao final, escreva seu nome em `reviewer`.

h_event_asserted — o texto AFIRMA que o evento ocorreu para esta empresa?
  ASSERTED        o texto afirma o evento.
  MENTIONED_ONLY  a palavra aparece, mas o evento não é afirmado para ela.
                  Ex.: nome de processo ("Bankruptcy Court") sem insolvência.
  DENIED          o texto nega.
  UNCLEAR         não dá para decidir.

h_subject — de QUEM é o evento. Escreva o nome como aparece no texto.
  Se for a própria empresa monitorada, repita o nome dela.
  Cuidado com aposto: "Fornecedor, contratado da Vale, foi condenado" → o
  sujeito é o Fornecedor.

h_company_role — papel DA EMPRESA MONITORADA.
  SUBJECT, BUYER, SELLER, TARGET, INVESTOR, CREDITOR, DEBTOR, VICTIM,
  PERPETRATOR, MENTIONED (citada sem participar), UNRELATED, UNKNOWN.

h_currentness — o evento é atual ou referência a período anterior?
  CURRENT / HISTORICAL / UNDATABLE / CONFLICTING.
  Regra prática: a data de publicação NÃO decide. "X relembra a falência de
  2019" é HISTORICAL mesmo publicado hoje.

h_centrality — MAIN (é o assunto), MATERIAL_SECONDARY (relevante, não
  principal), BACKGROUND (pano de fundo), INCIDENTAL (menção de passagem).

h_scoreable — na sua opinião de risco, este evento DEVERIA pontuar para esta
  empresa? SIM / NAO / DUVIDA.

h_novel_valido — só para os estratos S6 e S7 (sem evento da taxonomia):
  o artigo descreve um acontecimento materialmente relevante para o risco
  desta empresa? SIM / NAO / DUVIDA. Se SIM, descreva em h_observacao em
  poucas palavras.

ATENÇÃO — estes itens são CONTROLE DE DESENVOLVIMENTO (procedencia =
DEVELOPMENT_CONTROL): moldaram as regras atuais. Servem para detectar
regressão, NUNCA para medir desempenho fora de amostra. Adjudique normalmente;
a separação é feita no relatório.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=str(OUTDIR))
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)

    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    man = json.load(io.open(out / "sample_manifest.json", encoding="utf-8"))
    tap = {}
    tf = out / "tap_pre_filtro.json"
    if tf.exists():
        for it in (json.load(io.open(tf, encoding="utf-8")).get("itens") or []):
            tap[(it.get("url"), it.get("empresa"))] = it

    linhas = montar_linhas(man, hist, cfg, tap)
    den = denominadores(linhas)

    csvp = out / "human_review_template.csv"
    cols = list(linhas[0].keys())
    with io.open(csvp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";")
        w.writeheader()
        for l in linhas:
            w.writerow(l)
    io.open(out / "adjudication_guide.md", "w", encoding="utf-8").write(GUIA)
    io.open(out / "denominators.json", "w", encoding="utf-8").write(
        json.dumps(den, ensure_ascii=False, indent=2, sort_keys=True))

    print("=" * 96)
    print("MATERIAL DE ADJUDICAÇÃO R7b-A")
    print("=" * 96)
    print(f"  itens                       : {den['itens']}")
    print(f"  por estrato                 : {den['por_estrato']}")
    print(f"  com leitura determinística  : {den['com_leitura_deterministica']}")
    print(f"  procedência                 : {den['procedencia']}")
    print()
    print("  DENOMINADORES DE CALIBRAÇÃO (numerador = PENDENTE_ADJUDICACAO)")
    for d, v in den["calibracao_de_defaults"].items():
        print(f"    {d:20s} D-DEFAULT {v['D_DEFAULT']:3d} · D-RULE {v['D_RULE']:3d}")
    print()
    print(f"  OW-1 denominador (itens com evento) : {den['ow1_denominador']}")
    print(f"  OW-2 denominador (S6, eventless)    : {den['ow2_denominador']}")
    print(f"  OW-3 denominador (S7, tap)          : {den['ow3_denominador']}")
    print()
    print(f"  revisados por humano        : {den['revisados_humanos']}")
    print(f"  UNREVIEWED                  : {den['unreviewed']}")
    print()
    print(f"  → {csvp}")
    print(f"  → {out / 'adjudication_guide.md'}")
    print(f"  → {out / 'denominators.json'}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
