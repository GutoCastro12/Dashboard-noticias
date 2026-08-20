#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_blast.py — quanto custa cada defeito de ocorrência.

ONDA DE DESENHO. Nada aqui altera produção: o módulo mede o acervo atual para
que a arquitetura da próxima onda seja escolhida por número, não por impressão.

O QUE FOI MEDIDO E CONTRARIA O QUE SE SUPUNHA

1. A produção NÃO infla recência. Em nenhuma ocorrência multi-artigo o
   representante é o mais recente — a fusão de gêmeos absorve o artigo
   posterior como corroboração ANTES do clustering, então ele nunca compete.
   O defeito é o oposto: não existe como um fechamento material reancorar.

2. A sobre-fusão tem uma causa concreta e pequena. O conjunto de marcadores de
   `occurrence_identity` inclui o nome do REGULADOR. Na Sabesp:

       'cade|emae|tribunal'   (EMAE)
       'cade|sanessol'        (Sanessol)

   `cade` é a ponte que une duas transações distintas. Removido o ruído, os
   objetos separam sozinhos: emae / sanessol / castilho.

3. O alias da Suzano NÃO é inferível de marcador: 'clark|kimberly' e
   'arbex|suzb3' são disjuntos mesmo sem ruído. Ela está correta hoje por
   ACIDENTE — a mesma sobre-fusão que erra na Sabesp acerta aqui. Um mecanismo
   de alias precisa ser declarado, não adivinhado.

RUÍDO DE MARCADOR

`_MARCADOR_RUIDO` não é lista de empresas: são reguladores, órgãos e palavras
de ligação que aparecem em QUALQUER transação e por isso não identificam
objeto nenhum. Ela existe para medir, não para corrigir — a correção é da onda
de implementação, e tem de decidir se filtra na origem ou numa camada nova.

AUTORIDADE: NENHUMA. Somente leitura.
"""
from __future__ import annotations

import argparse
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

import reliability_occurrence_reproducer as rp
import risk_dashboard as rd
import semantic_v2_shadow as sh

BLAST_VERSION = "occurrence.blast.v1"
AUTORIDADE = {"production_score_authority": "NONE", "semantic_authority": "NONE"}

# Reguladores/órgãos e conectivos: aparecem em transações distintas e por isso
# NÃO identificam objeto. É esta ponte que funde EMAE com Sanessol.
_MARCADOR_RUIDO = frozenset({
    "cade", "anp", "aneel", "cvm", "bacen", "susep", "antt", "accc", "sec",
    "tribunal", "superintendencia", "geral", "uniao", "europeia", "comissao",
    "justica", "conselho", "assembleia", "reguladora", "agencia",
    "apos", "ainda", "novo", "nova", "material", "mercado", "brasil",
    "milhoes", "bilhoes", "bilhao", "milhao", "participacao", "acoes",
})

_RX_FECHAMENTO = re.compile(
    r"\bconclu[ií]\w*|\bfinaliz\w*|\bencerr\w*|\bcompletes?\b|\bcompleted\b|"
    r"\bwraps up\b|\bfecha\b|\bconcluded\b", re.I)
_RX_ETAPA = re.compile(
    r"\baprova\w*|\bapproval\b|\bapproves?\b|\bassembleia\b|\bliminar\b|"
    r"\brecurso\b|\bcautelar\b|\bautoriza\w*|\bscrutiny\b", re.I)
# Balde de mercado: não é emissor, agrega notícia de terceiros. Medir
# sobre-fusão nele é medir o balde, não a arquitetura.
_NAO_EMISSOR = frozenset({"Mercado (geral)"})


def marcadores_limpos(titulo: str, event_id: str, empresa: str) -> set:
    ident = rd.occurrence_identity(titulo or "", event_id, empresa, None)
    brutos = {m for m in (ident.get("marcadores") or "").split("|") if m}
    return brutos - _MARCADOR_RUIDO


def _grupos_disjuntos(conjuntos: list) -> list:
    """União por interseção: quantos objetos DISTINTOS o grupo contém."""
    grupos: list[set] = []
    for m in [c for c in conjuntos if c]:
        alvo = next((g for g in grupos if g & m), None)
        if alvo is None:
            grupos.append(set(m))
        else:
            alvo |= m
    mudou = True
    while mudou:
        mudou = False
        for a in range(len(grupos)):
            for b in range(a + 1, len(grupos)):
                if grupos[a] & grupos[b]:
                    grupos[a] |= grupos[b]
                    grupos.pop(b)
                    mudou = True
                    break
            if mudou:
                break
    return grupos


def _decay_fn(cfg):
    ev = cfg.get("evolution", {})
    d = ev.get("decay", {})
    hl = max(1, d.get("half_life_days", 30))
    on = d.get("enabled", True)
    agora = int(datetime.now(timezone.utc).timestamp())

    def f(ts):
        return 1.0 if not on else 0.5 ** (((agora - ts) / 86400.0) / hl)
    return f


def medir(historico="risk_history.json", config="config_risco.yaml") -> dict:
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    R = rp.reproduzir(historico, config)
    decay = _decay_fn(cfg)

    pares = defaultdict(list)
    for u, r in H["articles"].items():
        aid = sh.id_artigo(r.get("url") or u, r.get("title") or "")
        for emp in (r.get("companies") or []):
            for evid in (rd.event_ids_for(r, emp) or []):
                t = r.get("title") or ""
                pares[(emp, evid)].append({
                    "article_id": aid, "ts": r.get("pub_ts") or 0,
                    "date": (r.get("pub_iso") or "")[:10], "title": t,
                    "objeto": marcadores_limpos(t, evid, emp),
                    "fase": rd.occurrence_identity(t, evid, emp, None).get("fase") or "",
                    "fechamento": bool(_RX_FECHAMENTO.search(t)),
                    "etapa": bool(_RX_ETAPA.search(t))})

    sobre, fechamentos, etapas = [], [], []
    for (emp, fam), itens in pares.items():
        if emp in _NAO_EMISSOR or len(itens) < 2:
            continue
        gs = _grupos_disjuntos([i["objeto"] for i in itens])
        span = int((max(i["ts"] for i in itens) - min(i["ts"] for i in itens)) / 86400)
        if len(gs) > 1:
            sobre.append({"company": emp, "family": fam, "n_objetos": len(gs),
                          "n_artigos": len(itens), "span_dias": span,
                          "objetos": [sorted(g)[:5] for g in gs]})
        it = sorted(itens, key=lambda i: i["ts"])
        fech = [i for i in it if i["fechamento"]]
        if fech and fech[-1]["ts"] > it[0]["ts"]:
            o = next((x for x in R["ocorrencias"]
                      if x["company"] == emp and x["family"] == fam), None)
            if o:
                base = (o["score_base"] or 0) * (o["trust_w"] or 1.0)
                atual = base * decay(o["representante_ts"] or 0)
                novo = base * decay(fech[-1]["ts"])
                fechamentos.append({
                    "company": emp, "family": fam,
                    "ancora_atual": o["representante_date"],
                    "ancora_fechamento": fech[-1]["date"],
                    "contrib_atual": round(atual, 1),
                    "contrib_se_renovasse": round(novo, 1),
                    "delta": round(novo - atual, 1),
                    "ja_ancorado_no_fechamento": (o["representante_ts"] == fech[-1]["ts"]),
                    "fechamento_title": fech[-1]["title"][:110]})
        if any(i["etapa"] for i in it):
            etapas.append({"company": emp, "family": fam,
                           "n_etapas": sum(1 for i in it if i["etapa"])})

    absorv_tot = sum(o["n_absorvidos"] for o in R["ocorrencias"])
    absorv_res = sum(1 for o in R["ocorrencias"] for a in o["absorvidos"]
                     if a["article_id"])
    return {
        "_meta": {"blast_version": BLAST_VERSION, **AUTORIDADE},
        "corpus": R["corpus"],
        "pares_pontuaveis": len(pares),
        "ocorrencias_no_painel": len(R["ocorrencias"]),
        "sobre_fusao": sorted(sobre, key=lambda x: (-x["n_objetos"], -x["n_artigos"])),
        "fechamentos": sorted(fechamentos, key=lambda x: -x["delta"]),
        "etapas_regulatorias": etapas,
        "absorvidos": {"total": absorv_tot, "resolviveis": absorv_res,
                       "nao_resolviveis": absorv_tot - absorv_res,
                       "ocorrencias_afetadas": sum(
                           1 for o in R["ocorrencias"]
                           if any(not a["article_id"] for a in o["absorvidos"]))},
        "uma_ocorrencia_por_par": all(
            sum(1 for o in R["ocorrencias"]
                if (o["company"], o["family"]) == k) <= 1
            for k in {(o["company"], o["family"]) for o in R["ocorrencias"]}),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Blast de ocorrencia (somente leitura).")
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args(argv)
    m = medir(a.historico)
    print(json.dumps({k: v for k, v in m.items()
                      if k not in ("sobre_fusao", "fechamentos", "etapas_regulatorias")},
                     ensure_ascii=False, indent=1))
    print(f"\nSOBRE-FUSAO (emissores, ruido de regulador removido): "
          f"{len(m['sobre_fusao'])}")
    for x in m["sobre_fusao"][:a.top]:
        print(f"  {x['n_objetos']} objetos | {x['n_artigos']} art | {x['span_dias']}d "
              f"| {x['company']} / {x['family']}  {x['objetos']}")
    print(f"\nFECHAMENTOS apos anuncio: {len(m['fechamentos'])}")
    for x in m["fechamentos"][:a.top]:
        marca = "JA ANCORADO" if x["ja_ancorado_no_fechamento"] else f"delta {x['delta']:+}"
        print(f"  {x['company']:<24} {x['family']:<10} ancora={x['ancora_atual']} "
              f"fecho={x['ancora_fechamento']} {marca}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
