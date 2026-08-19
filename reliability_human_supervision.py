#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_human_supervision.py — supervisão humana que o Contract V2 não comporta.

POR QUE UM ARTEFATO NOVO

`risk_semantic_v2_shadow.json` é indexado por SAÍDA DE MODELO: a chave é
`article_id|empresa|evento|contrato|prompt|modelo`, e todo `human_review` fica
pendurado num registro que carrega `saida`, `deterministic`, `latencia_s` e
`usage`. Ele responde "o modelo acertou?".

O lote V1 de supervisão manual não tem saída de modelo nenhuma — os 24 casos
foram escolhidos exatamente porque ninguém além do determinístico opinou sobre
eles. Pendurá-los lá exigiria fabricar registros de modelo, e um artefato de
avaliação contaminado por registro fabricado deixa de avaliar.

O segundo motivo é semântico. As dimensões do V2 (`event_asserted`,
`company_role`, `occurrence_novelty`, `currentness`, `phase`, `centrality`)
não expressam o que este lote descobriu:

  - mesma ocorrência PODE renovar score (fechamento material) e PODE NÃO poder
    (etapa processual) — `occurrence_novelty` não distingue as duas;
  - o evento existe mas a FAMÍLIA está errada (PRIO, Bradesco, Aegea);
  - o evento é relevante e NÃO cabe na taxonomia atual;
  - a evidência local não basta e a fonte está quebrada;
  - o artigo derivado existe e o anúncio original está ausente do radar.

Forçar isso nos enums do V2 apagaria justamente a informação nova.

O QUE ESTE ARQUIVO NÃO É

Não é autoridade de score: `build_evolution` nunca o lê. Não é autoridade
semântica: nenhuma regra de produção depende dele. Não substitui o shadow V2 —
os dois convivem, e nenhum `human_review` do V2 é tocado aqui.

CHAVE

`article_id|company|family`. Um artigo pode ter julgamento por empresa (o caso
dos três bancos) e por família (o caso da Rumo, que tem recomendação e troca de
CEO no mesmo texto). São registros de FILIAÇÃO distintos de um mesmo CASO de
revisão — por isso o número de filiações é maior que o número de casos, e as
duas contagens são reportadas separadamente.

RÓTULOS DIAGNÓSTICOS

`article_role`, `taxonomy_fit` e as filas (`recall`, `source`, `taxonomy`) são
texto livre DE PROPÓSITO. São mais ricos que os enums de produção e mudam mais
rápido que eles; congelá-los em enum agora seria decidir a taxonomia antes de
ter evidência. O que é enum aqui é só o que a máquina precisa comparar:
`status`, `event_asserted`, `scoreable`, `occurrence_relation`, `score_refresh`.

IDEMPOTÊNCIA

Evidência idêntica reaplicada não escreve nada — nem carimbo de "última
verificação". Julgamento diferente NÃO sobrescreve em silêncio: a decisão
anterior vai para `revisions`.
"""
from __future__ import annotations

import argparse
import io
import json
import os

CAMINHO = "risk_human_supervision.json"
SCHEMA_VERSION = "human.supervision.v1"
STORE_VERSION = "human.supervision.store.v1"

# Enums MÍNIMOS — só o que precisa ser comparável por máquina.
STATUS = ("CLEAR", "UNDETERMINED", "POLICY_PENDING")
ASSERTED = ("YES", "NO", "UNDETERMINED")
SCOREABLE = ("YES", "NO", "UNDETERMINED")
OCC_RELATION = ("NEW_OCCURRENCE", "SAME_OCCURRENCE", "UNDETERMINED",
                "NOT_APPLICABLE")
REFRESH = ("TRUE", "FALSE", "UNDETERMINED", "NOT_APPLICABLE")
EVIDENCIA = ("SUFFICIENT", "INSUFFICIENT")
AUDITABILIDADE = ("OK", "BROKEN_SINGLE_SOURCE", "LINK_IN_VERIFICATION",
                  "TITLE_INSUFFICIENT_BODY_CONFIRMS")


def _vazio() -> dict:
    return {"_meta": {
        "artifact": "risk_human_supervision",
        "schema_version": SCHEMA_VERSION,
        "store_version": STORE_VERSION,
        "role": "HUMAN_SUPERVISION_DATASET",
        "production_score_authority": "NONE",
        "semantic_authority": "NONE",
        "purpose": (
            "julgamento humano sobre artigos do radar, mais rico do que o "
            "contrato V2 comporta: distingue etapa processual de fechamento "
            "material, descritor de assercao, familia errada de evento "
            "inexistente, e registra insuficiencia de evidencia em vez de "
            "forcar rotulo."),
        "nao_e": ("substituto do risk_semantic_v2_shadow.json, que mede modelo "
                  "contra humano; aqui nao ha saida de modelo. Nenhum caminho "
                  "de producao le este arquivo para pontuar ou classificar."),
        "chave": "article_id|company|family",
        "contagens": ("`casos` conta CASOS de revisao; `filiacoes` conta "
                      "registros article x company x family. Um caso com tres "
                      "empresas ou duas familias gera mais de uma filiacao."),
    }, "memberships": {}}


def _caminho(caminho: str | None = None) -> str:
    """Resolve o destino NA CHAMADA, nao no `def`.

    Com o caminho como default do parametro, o valor congela no import e nem
    reatribuir o modulo redireciona a escrita — foi assim que um teste gravou
    fixture dentro de um artefato de producao nesta base."""
    return caminho or CAMINHO


def carregar(caminho: str | None = None) -> dict:
    caminho = _caminho(caminho)
    if not os.path.exists(caminho):
        return _vazio()
    d = json.load(io.open(caminho, encoding="utf-8"))
    d.setdefault("memberships", {})
    d.setdefault("_meta", _vazio()["_meta"])
    return d


def salvar(dados: dict, caminho: str | None = None) -> None:
    io.open(_caminho(caminho), "w", encoding="utf-8", newline="").write(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def chave(article_id: str, company: str, family: str) -> str:
    return f"{article_id}|{company}|{family}"


def validar(m: dict) -> list[str]:
    """Devolve a lista de problemas. Lista vazia = registro valido."""
    p = []
    for campo in ("article_id", "company", "family", "status", "case_id",
                  "supervision_batch", "adjudicated_by", "adjudicated_at"):
        if not str(m.get(campo) or "").strip():
            p.append(f"campo obrigatorio ausente: {campo}")
    if m.get("status") not in STATUS:
        p.append(f"status invalido: {m.get('status')!r}")
    if m.get("event_asserted") not in ASSERTED:
        p.append(f"event_asserted invalido: {m.get('event_asserted')!r}")
    if m.get("scoreable") not in SCOREABLE:
        p.append(f"scoreable invalido: {m.get('scoreable')!r}")
    if m.get("occurrence_relation") not in OCC_RELATION:
        p.append(f"occurrence_relation invalido: {m.get('occurrence_relation')!r}")
    if m.get("score_refresh") not in REFRESH:
        p.append(f"score_refresh invalido: {m.get('score_refresh')!r}")
    if m.get("evidence_sufficiency") not in EVIDENCIA:
        p.append(f"evidence_sufficiency invalido: {m.get('evidence_sufficiency')!r}")
    if m.get("source_auditability") not in AUDITABILIDADE:
        p.append(f"source_auditability invalido: {m.get('source_auditability')!r}")
    # Coerencia: um caso sem evidencia suficiente nao pode ter veredito firme.
    if m.get("evidence_sufficiency") == "INSUFFICIENT" and m.get("status") == "CLEAR":
        p.append("evidencia INSUFFICIENT nao pode produzir status CLEAR")
    if m.get("status") == "UNDETERMINED" and m.get("scoreable") != "UNDETERMINED":
        p.append("status UNDETERMINED exige scoreable UNDETERMINED")
    if m.get("status") == "POLICY_PENDING" and m.get("scoreable") != "UNDETERMINED":
        p.append("status POLICY_PENDING exige scoreable UNDETERMINED")
    # `rationale` e o que torna a supervisao auditavel depois.
    if len(str(m.get("rationale") or "")) < 20:
        p.append("rationale ausente ou curta demais")
    return p


def upsert(dados: dict, m: dict) -> tuple[str, bool]:
    """Insere ou revisa UMA filiacao. Devolve (chave, mudou).

    Julgamento identico nao escreve nada. Julgamento diferente NAO sobrescreve
    em silencio: a decisao anterior vai para `revisions`."""
    k = chave(m["article_id"], m["company"], m["family"])
    novo = {x: y for x, y in m.items() if x != "revisions"}
    atual = dados["memberships"].get(k)
    if atual is None:
        dados["memberships"][k] = {**novo, "revisions": []}
        return k, True
    anterior = {x: y for x, y in atual.items() if x != "revisions"}
    if anterior == novo:
        return k, False
    atual.setdefault("revisions", []).append(anterior)
    atual.update(novo)
    return k, True


def registrar_muitos(registros, *, caminho: str | None = None,
                     aplicar: bool = False) -> dict:
    """Dry-run por padrao. Nada e gravado sem `aplicar=True`."""
    caminho = _caminho(caminho)
    dados = carregar(caminho)
    novos, revisados, problemas = [], [], []
    for m in registros:
        erros = validar(m)
        if erros:
            problemas.append({"chave": chave(m.get("article_id", "?"),
                                             m.get("company", "?"),
                                             m.get("family", "?")),
                              "erros": erros})
            continue
        antes = set(dados["memberships"])
        k, mudou = upsert(dados, m)
        if not mudou:
            continue
        (novos if k not in antes else revisados).append(k)
    if problemas:
        return {"novos": [], "revisados": [], "problemas": problemas,
                "aplicado": False, "total": len(dados["memberships"])}
    if aplicar and (novos or revisados):
        salvar(dados, caminho)
    return {"novos": novos, "revisados": revisados, "problemas": [],
            "aplicado": bool(aplicar and (novos or revisados)),
            "total": len(dados["memberships"]),
            "casos": len({v.get("case_id") for v in dados["memberships"].values()})}


def resumo(caminho: str | None = None) -> dict:
    d = carregar(caminho)
    ms = list(d["memberships"].values())
    por_status = {}
    for m in ms:
        por_status[m.get("status")] = por_status.get(m.get("status"), 0) + 1
    return {"schema": d["_meta"].get("schema_version"),
            "filiacoes": len(ms),
            "casos": len({m.get("case_id") for m in ms}),
            "empresas": len({m.get("company") for m in ms}),
            "familias": len({m.get("family") for m in ms}),
            "por_status": por_status,
            "lotes": sorted({m.get("supervision_batch") for m in ms})}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Consulta a supervisao humana (somente leitura).")
    p.add_argument("--chave")
    p.add_argument("--caso")
    p.add_argument("--caminho", default=None)
    a = p.parse_args(argv)
    d = carregar(a.caminho)
    if a.chave:
        e = d["memberships"].get(a.chave)
        print(json.dumps(e, ensure_ascii=False, indent=1) if e
              else f"SEM_REGISTRO: {a.chave}")
        return 0 if e else 1
    if a.caso:
        e = [v for v in d["memberships"].values() if v.get("case_id") == a.caso]
        print(json.dumps(e, ensure_ascii=False, indent=1) if e
              else f"SEM_REGISTRO: caso {a.caso}")
        return 0 if e else 1
    print(json.dumps(resumo(a.caminho), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
