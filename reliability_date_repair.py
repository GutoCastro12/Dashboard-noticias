#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_date_repair.py — conserta UMA data já gravada, com portão.

POR QUE SEPARADO DA CAMADA DE VERIFICAÇÃO

`reliability_page_date` protege o que vem daqui para frente. Não desfaz o que
já está no histórico: a matéria da Vale de 2023 entrou como 2026 e continua
sustentando a Samarco como CRÍTICA no painel publicado.

Reprocessar o histórico inteiro para consertar isso seria backfill — centenas
de requisições e uma superfície de mudança que ninguém consegue revisar. O que
se quer é o oposto: uma correção cirúrgica, provada artigo a artigo.

O QUE ESTA FERRAMENTA GARANTE

  · identidade resolvida por URL canônica, com EXATAMENTE UM registro casando —
    0 ou mais de 1 aborta;
  · a data nova vem da própria página, pela mesma política da camada de
    verificação (`pubdate.p1`) — nunca de um valor digitado à mão;
  · correção manual travada vence a ferramenta, sempre;
  · dry-run é o padrão; escrever exige `--apply`;
  · só campos de data e proveniência mudam — o resto do registro é conferido
    por hash antes e depois, e divergência aborta sem gravar;
  · o valor do feed é preservado, para que a decisão continue auditável.

Não é backfill. Uma execução conserta um artigo.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path

import reliability_page_date as pd

REPAIR_VERSION = "pubdate.repair.v1"

# Tudo que a correção de data NÃO pode encostar. Se algum destes mudar, a
# operação aborta: seria classificação semântica se movendo por baixo de uma
# correção que deveria ser só temporal.
CAMPOS_INTOCAVEIS = ("title", "url", "canonical_url", "source", "domain",
                     "summary", "companies", "event_ids", "events_by_company",
                     "context_events_by_company",
                     "informational_events_by_company", "mention_roles",
                     "semantic_discards", "companies_attributed",
                     "context_companies", "manual_correction")

CAMPOS_DE_DATA = ("pub_ts", "pub_iso", "feed_pub_ts", "feed_pub_iso",
                  "page_pub_ts", "page_pub_iso", "page_date_source",
                  "page_date_modified", "pub_date_verification",
                  "pub_date_origin", "pub_date_conflict_s", "pub_date_policy",
                  "pub_date_note")


class ReparoRecusado(Exception):
    """Erro de operação — nunca deixa o histórico pela metade."""


def digest_intocavel(rec: dict) -> str:
    nucleo = {k: rec.get(k) for k in CAMPOS_INTOCAVEIS}
    return hashlib.sha256(
        json.dumps(nucleo, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def resolver_registro(historico: dict, alvo: str) -> str:
    """URL do único registro que casa. Compara por forma canônica, não por
    string crua: querystring e barra final não podem decidir identidade."""
    import link_debt_audit as lk
    can_alvo = (lk.canonicalize(alvo) or alvo).rstrip("/").lower()
    achados = []
    for u, a in (historico.get("articles") or {}).items():
        for cand in (a.get("canonical_url"), a.get("resolved_url"), u):
            if not cand:
                continue
            if (lk.canonicalize(cand) or cand).rstrip("/").lower() == can_alvo:
                achados.append(u)
                break
    if not achados:
        raise ReparoRecusado(f"NENHUM_REGISTRO: nada casa com {alvo!r}")
    if len(set(achados)) > 1:
        raise ReparoRecusado(
            f"REGISTRO_AMBIGUO: {len(set(achados))} registros casam com "
            f"{alvo!r}: {sorted(set(achados))}")
    return achados[0]


def preparar(historico: dict, alvo: str, html: str, *,
             agora: int | None = None,
             tolerancia: int = pd.TOLERANCIA_S) -> dict:
    """Calcula a correção sem tocar em nada. Base do dry-run e do apply."""
    url = resolver_registro(historico, alvo)
    rec = (historico.get("articles") or {})[url]
    travados = pd.campos_travados(rec)
    if travados & {"pub_ts", "pub_iso"}:
        raise ReparoRecusado(
            f"CORRECAO_MANUAL_TRAVADA: {sorted(travados & {'pub_ts','pub_iso'})} "
            f"sob lock manual — auditoria humana vence o verificador")
    pagina = pd.extrair_data_da_pagina(
        html, url=rec.get("canonical_url") or url,
        headline=rec.get("title") or "", agora=agora)
    if not pagina.get("published_ts"):
        raise ReparoRecusado(
            f"SEM_DATA_NA_PAGINA: {pagina.get('motivo') or 'não declarada'} — "
            f"sem data forte não se corrige nada")
    decisao = pd.decidir_data_efetiva(int(rec.get("pub_ts") or 0), pagina,
                                      tolerancia=tolerancia)
    campos = pd.campos_de_proveniencia(rec, pagina, decisao)
    mudancas = {k: (rec.get(k), v) for k, v in campos.items()
                if rec.get(k) != v}
    return {
        "url": url, "titulo": rec.get("title"), "fonte": rec.get("source"),
        "feed_iso": rec.get("feed_pub_iso") or rec.get("pub_iso"),
        "pagina": pagina, "decisao": decisao, "campos": campos,
        "mudancas": mudancas,
        "conflito": decisao["conflito"],
        "hash_intocavel_antes": digest_intocavel(rec),
        "repair_version": REPAIR_VERSION,
    }


def aplicar(caminho_historico: str, alvo: str, html: str, *,
            agora: int | None = None, tolerancia: int = pd.TOLERANCIA_S,
            aplicar_de_fato: bool = False) -> dict:
    """Dry-run por padrão. Só grava com `aplicar_de_fato=True`."""
    historico = json.load(io.open(caminho_historico, encoding="utf-8"))
    plano = preparar(historico, alvo, html, agora=agora, tolerancia=tolerancia)
    plano["aplicado"] = False
    if not aplicar_de_fato:
        plano["motivo_nao_aplicado"] = "dry-run: use aplicar_de_fato=True"
        return plano
    if not plano["conflito"]:
        raise ReparoRecusado(
            "SEM_CONFLITO_MATERIAL: a data do feed está dentro da tolerância; "
            "não há o que corrigir")

    novo = copy.deepcopy(historico)
    rec = novo["articles"][plano["url"]]
    antes = digest_intocavel(rec)
    rec.update(plano["campos"])
    depois = digest_intocavel(rec)
    if antes != depois:
        raise ReparoRecusado(
            "CAMPOS_INTOCAVEIS_ALTERADOS: nada foi gravado")
    fora = [k for k in plano["campos"] if k not in CAMPOS_DE_DATA]
    if fora:
        raise ReparoRecusado(f"CAMPO_FORA_DO_ESCOPO: {fora} — nada foi gravado")
    outros = [u for u in novo["articles"]
              if u != plano["url"]
              and json.dumps(novo["articles"][u], sort_keys=True,
                             ensure_ascii=False)
              != json.dumps(historico["articles"][u], sort_keys=True,
                            ensure_ascii=False)]
    if outros:
        raise ReparoRecusado(f"MUTACAO_COLATERAL: {outros[:5]} — nada foi gravado")

    p = Path(caminho_historico)
    bruto = p.read_bytes()
    backup = p.with_suffix(p.suffix + f".backup_{REPAIR_VERSION}")
    backup.write_bytes(bruto)
    tmp = p.with_suffix(p.suffix + ".tmp")
    io.open(tmp, "w", encoding="utf-8").write(
        json.dumps(novo, ensure_ascii=False, indent=1, sort_keys=True))
    tmp.replace(p)
    plano.update(aplicado=True,
                 backup=str(backup),
                 sha256_antes=hashlib.sha256(bruto).hexdigest(),
                 sha256_depois=hashlib.sha256(p.read_bytes()).hexdigest(),
                 registros_alterados=1)
    return plano


def relatorio(p: dict) -> str:
    d, pg = p["decisao"], p["pagina"]
    L = ["REGISTRO ENCONTRADO",
         f"  título   : {(p['titulo'] or '')[:76]}",
         f"  fonte    : {p['fonte']}",
         f"  url      : {p['url'][:88]}",
         "",
         "DATAS",
         f"  feed     : {p['feed_iso']}",
         f"  página   : {pg['published_iso']}  (via {pg['fonte']}, "
         f"{pg.get('candidatos')} candidato(s))",
         f"  modified : {pg.get('modified_iso') or '—'}  (diagnóstico apenas; "
         f"nunca usado como publicação)",
         f"  efetiva  : {p['campos'].get('pub_iso', p['feed_iso'])}",
         "",
         f"DECISÃO  origem={d['origem']}  conflito={d['conflito']}  "
         f"delta={d['delta_s'] // 86400} dia(s)  política={d['policy']}",
         f"  {d['motivo']}",
         "",
         "CAMPOS QUE MUDAM"]
    for k, (antes, depois) in sorted(p["mudancas"].items()):
        L.append(f"  {k:24s} {str(antes)[:26]!r:28s} → {str(depois)[:26]!r}")
    if not p["mudancas"]:
        L.append("  (nenhum)")
    L += ["", "CAMPOS INTOCÁVEIS", "  " + ("OK — inalterados" if p.get("aplicado")
                                           else "conferidos na escrita")]
    if p.get("aplicado"):
        L += ["", "APLICADO",
              f"  registros alterados : {p['registros_alterados']}",
              f"  backup              : {p['backup']}",
              f"  sha256 antes/depois : {p['sha256_antes'][:16]} → "
              f"{p['sha256_depois'][:16]}"]
    else:
        L += ["", f"ESCRITA  NÃO APLICADA — {p.get('motivo_nao_aplicado', '')}"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Corrige a data de UM artigo já gravado (dry-run por padrão).")
    ap.add_argument("--url", required=True, help="URL do artigo no histórico")
    ap.add_argument("--history", default="risk_history.json")
    ap.add_argument("--html", default=None,
                    help="arquivo com o HTML da página; sem ele, busca na rede")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--timeout", type=int, default=20)
    a = ap.parse_args(argv)
    if a.html:
        html = io.open(a.html, encoding="utf-8", errors="replace").read()
    else:
        import requests
        r = requests.get(a.url, timeout=a.timeout,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; "
                                                "RadarRisco/1.0)"})
        if r.status_code != 200:
            print(f"RECUSADO: PAGINA_INDISPONIVEL: HTTP {r.status_code}")
            return 2
        html = r.text
    try:
        p = aplicar(a.history, a.url, html, aplicar_de_fato=a.apply)
    except ReparoRecusado as exc:
        print(f"RECUSADO: {exc}")
        return 2
    print(relatorio(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
