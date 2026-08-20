#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_reproducer.py — o que a produção DECIDE sobre ocorrência.

POR QUE INSTRUMENTAR E NÃO REIMPLEMENTAR

A pergunta desta onda é "o que a produção faz hoje", não "o que deveria fazer".
Reimplementar o agrupamento produziria um segundo algoritmo que diverge em
silêncio — e um reprodutor que discorda da produção não mede nada, só cria uma
terceira opinião.

Então este módulo **envolve** `assign_occurrence_clusters`, deixa a função real
decidir, e apenas ANOTA os grupos que ela formou. Depois roda a
`build_evolution` real e junta as duas coisas. A equivalência não é perseguida:
ela é estrutural, porque quem decide continua sendo a produção.

O QUE ELE RESPONDE

Por ocorrência (`_occ_key`), para cada empresa × família:

  - quais artigos são membros, com data e título;
  - qual artigo virou REPRESENTANTE;
  - qual data ancora a ocorrência;
  - qual artigo sustenta a recência que o decaimento usa;
  - quanto a ocorrência contribui de score;
  - quantas fontes corroboram.

CINCO CONCEITOS QUE NÃO PODEM SER COLAPSADOS

A lição do lote V1 de supervisão humana é que estas perguntas são distintas, e
tratá-las como uma só foi a origem dos erros:

  A  ASSERÇÃO      o artigo afirma um evento?
  B  IDENTIDADE    é a mesma ocorrência econômica?
  C  FASE          anúncio / etapa processual / fechamento / acompanhamento?
  D  RENOVAÇÃO     esta fase renova a relevância (decaimento)?
  E  REPRESENTANTE qual artigo deve representar a ocorrência?

A produção hoje decide (B) por `assign_occurrence_clusters` e decide (D) e (E)
JUNTAS, por um único critério: dentro do `_occ_key`, vence o membro de maior
`contrib = score × decaimento × confiança + bônus de corroboração`. Como o
decaimento domina, o artigo mais RECENTE tende a vencer — e é por isso que um
acompanhamento pode virar representante e renovar a recência de um fato antigo.

Este módulo não corrige nada disso. Ele mede.

AUTORIDADE

  production_score_authority: NENHUMA
  semantic_authority:         NENHUMA

Nenhum caminho de produção importa este arquivo. Ele lê `risk_history.json` e
não escreve em lugar nenhum.
"""
from __future__ import annotations

import argparse
import copy
import io
import json

import risk_dashboard as rd
import semantic_v2_shadow as sh

REPRODUCER_VERSION = "occurrence.reproducer.v1"
AUTORIDADE = {"production_score_authority": "NONE", "semantic_authority": "NONE"}


def _aid(url: str, rec: dict) -> str:
    return sh.id_artigo(rec.get("url") or url, rec.get("title") or "")


def reproduzir(historico="risk_history.json", config="config_risco.yaml") -> dict:
    """Roda a produção REAL e anota as decisões de ocorrência que ela tomou.

    `historico` aceita caminho ou dict já carregado — é assim que o teste
    injeta um acervo simulado sem tocar em produção."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else copy.deepcopy(historico))

    # url -> article_id, para resolver os ABSORVIDOS: a fusao de gemeos guarda
    # deles apenas dominio/url, e sem este indice eles ficariam invisiveis como
    # membros — foi assim que a Sabesp pareceu ter "ocorrencia propria" quando
    # na verdade o artigo estava absorvido dentro da ocorrencia certa.
    por_url = {}
    for u, r in H["articles"].items():
        a = _aid(u, r)
        por_url[u] = a
        if r.get("url"):
            por_url[r["url"]] = a

    capturado: list[list[dict]] = []
    real = rd.assign_occurrence_clusters

    def espiao(occurrences, *a, **k):
        # a função REAL decide; nós só olhamos o resultado
        out = real(occurrences, *a, **k)
        capturado.append([{
            # MESMA formula do representante — casar por chave do dicionario de
            # historico nao funciona: a ocorrencia carrega o campo `url`, que
            # pode diferir da chave (Google News x URL resolvida).
            "article_id": sh.id_artigo(o.get("url", ""), o.get("title", "")),
            "url": o.get("url", ""),
            "pub_ts": o.get("pub_ts"),
            "event_id": o.get("event_id"),
            "occ_key": o.get("_occ_key"),
            "title": o.get("title", ""),
            "score": o.get("score"),
            "trust_w": o.get("trust_w"),
            "n_corrob": len(o.get("corrob") or []),
            # os artigos ABSORVIDOS na fusao de gemeos sao membros reais da
            # ocorrencia; eles somem da lista clusterizada e viram fonte
            # corroborante. Ignora-los subestimaria o tamanho da ocorrencia.
            "corrob": [{"domain": c.get("domain", ""), "url": c.get("url", ""),
                        "quando": c.get("quando", ""),
                        "article_id": por_url.get(c.get("url", ""), "")}
                       for c in (o.get("corrob") or [])],
            "fase": ((o.get("_ident") or {}).get("fase") or ""),
            "marcadores": ((o.get("_ident") or {}).get("marcadores") or ""),
        } for o in occurrences])
        return out

    rd.assign_occurrence_clusters = espiao
    try:
        evo = rd.build_evolution(copy.deepcopy(H), cfg)
    finally:
        rd.assign_occurrence_clusters = real

    # membros por occ_key, na ordem em que a produção os viu
    membros: dict[str, list[dict]] = {}
    for grupo in capturado:
        for o in grupo:
            if not o["occ_key"]:
                continue
            membros.setdefault(o["occ_key"], []).append(dict(o))
    for v in membros.values():
        v.sort(key=lambda x: (x["pub_ts"] or 0))

    ocorrencias = []
    for linha in evo:
        emp = linha["company"]
        for ev in (linha.get("events") or []):
            k = ev.get("_occ_key") or ev.get("event_id")
            mem = membros.get(k, [])
            rep_ts = ev.get("pub_ts")
            datas = [m["pub_ts"] for m in mem if m["pub_ts"]]
            ocorrencias.append({
                "company": emp,
                "family": ev.get("event_id"),
                "occ_key": k,
                "n_membros": len(mem),
                "membros": [{"article_id": m["article_id"], "date": _iso(m["pub_ts"]),
                             "title": m["title"][:120], "fase": m["fase"],
                             "marcadores": m["marcadores"],
                             "n_absorvidos": len(m.get("corrob") or [])}
                            for m in mem],
                "n_absorvidos": sum(len(m.get("corrob") or []) for m in mem),
                # membros ABSORVIDOS: participam da ocorrencia, nao competem
                # por representante, e nao renovam o decaimento — sao fonte.
                "absorvidos": [{"article_id": c["article_id"], "domain": c["domain"]}
                               for m in mem for c in (m.get("corrob") or [])],
                "todos_article_ids": sorted(
                    {m["article_id"] for m in mem}
                    | {c["article_id"] for m in mem
                       for c in (m.get("corrob") or []) if c["article_id"]}),
                # REPRESENTANTE = o que a produção escolheu (maior contrib)
                "representante_article_id": _aid(ev.get("url", ""), ev),
                "representante_title": (ev.get("title") or "")[:120],
                "representante_date": ev.get("date"),
                "representante_ts": rep_ts,
                # ÂNCORA = a data mais antiga do grupo; é o fato, não a cobertura
                "ancora_date": _iso(min(datas)) if datas else None,
                "ultima_date": _iso(max(datas)) if datas else None,
                "span_dias": (int((max(datas) - min(datas)) / 86400)
                              if len(datas) > 1 else 0),
                # RECÊNCIA: quem sustenta o decaimento é o representante
                "recencia_article_id": _aid(ev.get("url", ""), ev),
                "representante_e_o_mais_recente": (
                    bool(datas) and rep_ts == max(datas)),
                "representante_e_o_mais_antigo": (
                    bool(datas) and rep_ts == min(datas)),
                "score_base": ev.get("score"),
                "trust_w": ev.get("trust_w"),
                "n_fontes": ev.get("sources"),
                "n_corrob": len(ev.get("corrob") or []),
                "severity": ev.get("severity"),
                "fase_representante": ev.get("event_phase") or "",
            })

    empresas = {l["company"]: {"total_score": l["total_score"],
                               "status": l["status"],
                               "n_eventos": len(l.get("events") or []),
                               "familias": [e["event_id"] for e in (l.get("events") or [])]}
                for l in evo}
    return {"_meta": {"reproducer_version": REPRODUCER_VERSION, **AUTORIDADE,
                      "modo": "INSTRUMENTED_PRODUCTION",
                      "nao_e": ("reimplementacao do agrupamento; a decisao continua "
                                "sendo da producao — este modulo apenas anota")},
            "corpus": len(H["articles"]),
            "ocorrencias": ocorrencias,
            "empresas": empresas,
            "evolucao": evo}


def _iso(ts) -> str:
    from datetime import datetime, timezone
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def equivalencia(rep: dict) -> dict:
    """A anotação bate com a produção? Estrutural, mas afirmada mesmo assim.

    Um reprodutor que não verifica a própria fidelidade é só mais uma opinião."""
    evo = rep["evolucao"]
    prob = []
    chaves_evo = {(l["company"], e.get("_occ_key") or e.get("event_id"))
                  for l in evo for e in (l.get("events") or [])}
    chaves_rep = {(o["company"], o["occ_key"]) for o in rep["ocorrencias"]}
    if chaves_evo != chaves_rep:
        prob.append(f"chaves divergem: {len(chaves_evo ^ chaves_rep)} diferenca(s)")
    for o in rep["ocorrencias"]:
        if o["n_membros"] == 0:
            prob.append(f"sem membros capturados: {o['occ_key']}")
            continue
        ids_mem = {m["article_id"] for m in o["membros"]}
        if o["representante_article_id"] not in ids_mem:
            prob.append(f"representante fora dos membros: {o['occ_key']}")
    soma_evo = sum(l["total_score"] for l in evo)
    return {"ok": not prob, "problemas": prob,
            "ocorrencias": len(rep["ocorrencias"]),
            "empresas": len(rep["empresas"]),
            "score_total_producao": soma_evo}


def inventario(rep: dict) -> dict:
    o = rep["ocorrencias"]
    return {
        "ocorrencias": len(o),
        "multi_artigo": sum(1 for x in o if x["n_membros"] > 1),
        "multi_fonte": sum(1 for x in o if (x["n_fontes"] or 1) > 1),
        "span_gt_7d": sum(1 for x in o if x["span_dias"] > 7),
        "span_gt_30d": sum(1 for x in o if x["span_dias"] > 30),
        "span_gt_90d": sum(1 for x in o if x["span_dias"] > 90),
        "representante_e_o_mais_recente": sum(
            1 for x in o if x["n_membros"] > 1 and x["representante_e_o_mais_recente"]),
        "representante_nao_e_o_mais_antigo": sum(
            1 for x in o if x["n_membros"] > 1 and not x["representante_e_o_mais_antigo"]),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Reprodutor DIAGNOSTICO de ocorrencia (somente leitura).")
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--json", action="store_true")
    p.add_argument("--top-span", type=int, default=0)
    a = p.parse_args(argv)
    rep = reproduzir(a.historico)
    eq = equivalencia(rep)
    if a.json:
        print(json.dumps({"equivalencia": eq, "inventario": inventario(rep),
                          "ocorrencias": rep["ocorrencias"]},
                         ensure_ascii=False, indent=1))
        return 0 if eq["ok"] else 1
    print(json.dumps({"equivalencia": eq, "inventario": inventario(rep)},
                     ensure_ascii=False, indent=1))
    if a.top_span:
        print("\nMAIOR SPAN ENTRE PRIMEIRO E ULTIMO ARTIGO:")
        for x in sorted(rep["ocorrencias"], key=lambda z: -z["span_dias"])[:a.top_span]:
            print(f"  {x['span_dias']:>4}d {x['company']:<26} {x['family']:<22} "
                  f"membros={x['n_membros']} rep={x['representante_date']} "
                  f"ancora={x['ancora_date']}")
            print(f"        rep: {x['representante_title'][:90]}")
    return 0 if eq["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
