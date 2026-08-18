#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_date_provenance.py — a correção sobrevive ao artigo.

O QUE ACONTECEU

O artigo do Estadão sobre o adiamento da assembleia Marfrig/BRF entrou com a
data do feed (2026-05-28) quando a própria página declara 2025-06-17. O reparo
canônico corrigiu a data — e, exatamente por corrigi-la, o artigo passou a ter
427 dias. `history_keep_days` é 400. No cron seguinte, `merge_into_history`
podou o registro de `risk_history.json`.

O resultado é perverso: quanto MAIS antiga a data verdadeira, mais rápido o
próprio reparo desaparece. Some o registro, some o conflito, some a evidência
de que a data do feed estava errada — e some a única prova de que a correção
foi legítima.

A DECISÃO

`risk_history.json` continua sendo HISTÓRICO OPERACIONAL, com retenção normal.
Não vira arquivo permanente. Nenhum artigo ganha isenção de retenção por ter
`pub_date_origin=pagina`, e `history_keep_days` não muda.

O que passa a existir é a separação que faltava:

  histórico operacional de notícias  !=  trilha permanente de proveniência

Este side-car guarda só a segunda coisa. Ele responde "de onde veio esta data,
e o que ela contradisse", e nada mais — não guarda corpo de artigo, não guarda
score, não decide semântica e não ressuscita registro nenhum.

O QUE ELE NÃO É

Não é autoridade de score: `build_evolution` nunca o lê. Não é autoridade
semântica: nenhuma classificação depende dele. Não é verdade humana: essa vive
em `risk_semantic_v2_shadow.json` e nos fixtures de confiabilidade. E não é uma
segunda base de notícias: guarda ~15 campos de data por artigo, e só para
artigos em que a página estabeleceu alguma coisa além do feed.

REVISÃO

A primeira decisão canônica é preservada. Se a página passar a declarar outra
data forte, a entrada não é sobrescrita em silêncio: a decisão anterior vai
para `revisions` e a corrente reflete a evidência nova. Sobrescrever sem rastro
seria repetir, num arquivo de auditoria, o defeito que ele existe para impedir.

IDEMPOTÊNCIA

Evidência idêntica reaplicada não escreve nada — nem carimbo de "última
verificação". Campo mutável a cada cron só produziria commit de ruído e
esconderia a mudança que importa no meio dele.
"""
from __future__ import annotations

import argparse
import io
import json
import os

import semantic_v2_shadow as sh

CAMINHO = "risk_date_provenance.json"
SCHEMA_VERSION = "pubdate.audit.v1"
STORE_VERSION = "pubdate.audit.store.v1"

# Só o que reconstrói a decisão de data. Nada de corpo, score ou semântica.
CAMPOS = ("feed_pub_ts", "feed_pub_iso", "page_pub_ts", "page_pub_iso",
          "page_date_source", "page_date_modified", "pub_ts", "pub_iso",
          "pub_date_origin", "pub_date_policy", "pub_date_verification",
          "pub_date_conflict_s", "pub_date_note")
# Identificação compacta: o suficiente para saber DE QUE artigo se fala depois
# que ele sai do histórico. `companies`/`event_ids` entram porque sem eles a
# entrada vira um id órfão numa auditoria futura.
CAMPOS_IDENTIDADE = ("url", "title", "source", "companies", "event_ids")


def _vazio() -> dict:
    return {"_meta": {
        "artifact": "risk_date_provenance",
        "schema_version": SCHEMA_VERSION,
        "store_version": STORE_VERSION,
        "role": "PUBLICATION_DATE_PROVENANCE_AUDIT",
        "production_score_authority": "NONE",
        "semantic_authority": "NONE",
        "human_truth_authority": "NONE",
        "purpose": (
            "reconstrucao forense da decisao de data de publicacao. Sobrevive a "
            "retencao de risk_history.json de proposito: corrigir uma data para "
            "o passado distante pode empurrar o artigo alem de "
            "history_keep_days, e a evidencia da correcao nao pode morrer junto "
            "com o registro operacional."),
        "nao_e": ("segunda base de noticias; nao guarda corpo, score nem "
                  "semantica; nenhum caminho de producao le este arquivo para "
                  "pontuar ou classificar"),
        "chave": "article_id (semantic_v2_shadow.id_artigo)",
    }, "articles": {}}


def _caminho(caminho: str | None = None) -> str:
    """Resolve o destino NA CHAMADA, nao no `def`.

    Com `caminho=CAMINHO` como default, o valor congela no import e nem
    reatribuir `dp.CAMINHO` redireciona a escrita — foi assim que um teste
    gravou `exemplo.invalido` dentro do artefato de producao. Um arquivo de
    auditoria que nao consegue ser isolado em teste nao serve como auditoria."""
    return caminho or CAMINHO


def carregar(caminho: str | None = None) -> dict:
    caminho = _caminho(caminho)
    if not os.path.exists(caminho):
        return _vazio()
    d = json.load(io.open(caminho, encoding="utf-8"))
    d.setdefault("articles", {})
    d.setdefault("_meta", _vazio()["_meta"])
    return d


def salvar(dados: dict, caminho: str | None = None) -> None:
    io.open(_caminho(caminho), "w", encoding="utf-8", newline="").write(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def id_do_registro(rec: dict, url: str = "") -> str:
    return sh.id_artigo(rec.get("url") or url, rec.get("title") or "")


def _instantaneo(rec: dict) -> dict:
    """Só os campos de data/proveniência presentes, sem inventar ausência."""
    return {k: rec[k] for k in CAMPOS if rec.get(k) not in (None, "")}


def _identidade(rec: dict, url: str) -> dict:
    out = {"url": rec.get("url") or url}
    for k in CAMPOS_IDENTIDADE:
        if k == "url":
            continue
        v = rec.get(k)
        if v not in (None, "", [], {}):
            out[k] = v
    return out


def deve_registrar(rec: dict) -> bool:
    """Registra quando a PÁGINA estabeleceu algo além do feed.

    Sem `page_pub_ts` não há proveniência a auditar — foi o feed e ponto, e
    encher o arquivo com isso só o transformaria em cópia do histórico."""
    try:
        return int(rec.get("page_pub_ts") or 0) > 0
    except (TypeError, ValueError):
        return False


def upsert(dados: dict, rec: dict, *, url: str = "", origem: str = "",
           quando: str = "") -> tuple[str, bool]:
    """Insere ou revisa UMA entrada. Devolve (article_id, mudou).

    Evidência idêntica não escreve nada. Evidência diferente NÃO sobrescreve em
    silêncio: a decisão anterior vai para `revisions`."""
    aid = id_do_registro(rec, url)
    novo = _instantaneo(rec)
    atual = dados["articles"].get(aid)
    if atual is None:
        entrada = {"article_id": aid, **_identidade(rec, url),
                   "provenance": novo, "revisions": []}
        if origem:
            entrada["first_seen_via"] = origem
        if quando:
            entrada["first_seen_at"] = quando
        dados["articles"][aid] = entrada
        return aid, True
    if atual.get("provenance") == novo:
        return aid, False            # idempotente: nem toca no arquivo
    atual.setdefault("revisions", []).append(
        {"provenance": atual.get("provenance") or {},
         **({"via": origem} if origem else {}),
         **({"em": quando} if quando else {})})
    atual["provenance"] = novo
    atual.update({k: v for k, v in _identidade(rec, url).items() if v})
    return aid, True


def registrar_muitos(registros, *, caminho: str | None = None, origem: str = "",
                     quando: str = "", aplicar: bool = False) -> dict:
    """`registros` é um iterável de (url, rec). Dry-run por padrão."""
    caminho = _caminho(caminho)
    dados = carregar(caminho)
    novos, revisados, ignorados = [], [], 0
    for url, rec in registros:
        if not deve_registrar(rec):
            ignorados += 1
            continue
        antes = set(dados["articles"])
        aid, mudou = upsert(dados, rec, url=url, origem=origem, quando=quando)
        if not mudou:
            continue
        (novos if aid not in antes else revisados).append(aid)
    if aplicar and (novos or revisados):
        salvar(dados, caminho)
    return {"novos": novos, "revisados": revisados,
            "sem_proveniencia": ignorados, "aplicado": bool(aplicar),
            "total": len(dados["articles"])}


def consultar(article_id: str, caminho: str | None = None) -> dict | None:
    return carregar(caminho)["articles"].get(article_id)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Consulta a trilha de proveniência de data (só leitura).")
    p.add_argument("--article-id")
    p.add_argument("--caminho", default=None)
    a = p.parse_args(argv)
    dados = carregar(a.caminho)
    if a.article_id:
        e = dados["articles"].get(a.article_id)
        print(json.dumps(e, ensure_ascii=False, indent=1) if e
              else f"SEM_REGISTRO: {a.article_id}")
        return 0 if e else 1
    print(json.dumps({"schema": dados["_meta"].get("schema_version"),
                      "entradas": len(dados["articles"]),
                      "ids": sorted(dados["articles"])}, ensure_ascii=False,
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
