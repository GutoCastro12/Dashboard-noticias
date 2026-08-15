#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_manual_correction.py — correção versionada de UM registro.

POR QUE ESTA FERRAMENTA EXISTE

A reclassificação oficial (`--reclassify-only --apply`) tem um portão
deliberado: `G5 added == 0`. Ela pode remover ou mover eventos, nunca
acrescentar. É o que impede uma varredura ampla de inventar eventos no
histórico, e não deve ser afrouxada.

Mas existe uma classe legítima de correção que precisa devolver um evento: um
registro gravado sob uma regra ERRADA, depois de a regra ter sido corrigida. Foi
o caso do artigo da fusão BRF/Marfrig, suprimido como recompra de ações e
preso nesse estado mesmo após o vocabulário ser corrigido.

O projeto já tratava exceções assim por edição controlada com metadados —
B3/Braskem e GM/Law.com. Esta ferramenta é essa convenção, executável e com
portões, em vez de edição manual de JSON.

O QUE ELA FAZ

Traz UM registro para o estado que o runtime ATUAL calcula, e registra
proveniência versionada: o que mudou, por quê, quando, o estado anterior
íntegro. Dry-run é o padrão.

SOBRE `locked_fields`

O lock do projeto PRESERVA o valor gravado contra recálculo — ele não escreve
valor novo. Serve para quando a decisão humana diverge da regra. Aqui a regra já
concorda com o resultado desejado: o que faltava era apenas gravar. Travar
campos neste caso congelaria o registro contra melhorias futuras legítimas —
exatamente o problema que o comentário do próprio mecanismo diz ter havido com
a proteção antiga, ampla demais.

Por isso o default é lock vazio, e quem quiser travar precisa pedir
explicitamente. A proteção contra regressão fica no teste, que falha alto, em
vez de num lock que congela em silêncio.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

CORRECTION_TOOL_VERSION = "manualfix.v1"

# Campos semânticos que uma correção pode tocar. Nada fora desta lista é
# alterado: título, URL, fonte, data e evidência ficam sempre intactos.
CAMPOS_SEMANTICOS = ("events_by_company", "context_events_by_company",
                     "informational_events_by_company", "companies_attributed",
                     "context_companies", "semantic_discards",
                     "event_assessments", "mention_roles", "event_ids")

# Tudo que a correção jamais pode tocar — conferido por hash antes e depois.
CAMPOS_INTOCAVEIS = ("title", "url", "canonical_url", "source", "domain",
                     "summary", "pub_ts", "pub_iso", "companies",
                     "feed_pub_ts", "feed_pub_iso", "page_pub_ts",
                     "pub_date_policy", "corroborations", "corrob_sources")


class CorrecaoRecusada(Exception):
    """Erro de operação — nunca deixa o histórico pela metade."""


def digest_intocavel(rec: dict) -> str:
    nucleo = {k: rec.get(k) for k in CAMPOS_INTOCAVEIS}
    return hashlib.sha256(
        json.dumps(nucleo, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def resolver(historico: dict, url: str) -> str:
    """URL do único registro que casa. Compara pela forma canônica."""
    import link_debt_audit as lk
    alvo = (lk.canonicalize(url) or url).rstrip("/").lower()
    achados = []
    for u, a in (historico.get("articles") or {}).items():
        for cand in (a.get("canonical_url"), a.get("resolved_url"), u):
            if cand and (lk.canonicalize(cand) or cand).rstrip("/").lower() == alvo:
                achados.append(u)
                break
    achados = sorted(set(achados))
    if not achados:
        raise CorrecaoRecusada(f"NENHUM_REGISTRO: nada casa com {url!r}")
    if len(achados) > 1:
        raise CorrecaoRecusada(f"REGISTRO_AMBIGUO: {achados}")
    return achados[0]


def estado_do_runtime(historico: dict, url: str, cfg: dict) -> dict:
    """O que o runtime ATUAL calcula para este registro, pelo caminho oficial."""
    import risk_dashboard as rd
    copia = copy.deepcopy(historico)
    rd._reclassify_only_pass(copia, cfg)
    return copia["articles"][url]


def preparar(historico: dict, url: str, cfg: dict, *, correction_id: str,
             motivo: str, revisor: str, locked_fields=None) -> dict:
    alvo = resolver(historico, url)
    atual = historico["articles"][alvo]
    novo = estado_do_runtime(historico, alvo, cfg)
    mudancas = {k: (atual.get(k), novo.get(k)) for k in CAMPOS_SEMANTICOS
                if atual.get(k) != novo.get(k)}
    if not mudancas:
        raise CorrecaoRecusada(
            "SEM_DIVERGENCIA: o registro já está no estado que o runtime "
            "calcula — não há o que corrigir")
    fora = [k for k in (set(atual) | set(novo))
            if atual.get(k) != novo.get(k) and k not in CAMPOS_SEMANTICOS]
    if fora:
        raise CorrecaoRecusada(
            f"DIVERGENCIA_FORA_DO_ESCOPO: {fora} — a correção só toca campos "
            f"semânticos; nada foi gravado")
    lf = list(locked_fields or [])
    desconhecidos = [f for f in lf if f not in CAMPOS_SEMANTICOS]
    if desconhecidos:
        raise CorrecaoRecusada(f"LOCK_FORA_DO_ESCOPO: {desconhecidos}")
    return {
        "url": alvo, "titulo": atual.get("title"), "fonte": atual.get("source"),
        "mudancas": mudancas, "locked_fields": lf,
        "correction_id": correction_id, "motivo": motivo, "revisor": revisor,
        "hash_intocavel_antes": digest_intocavel(atual),
        "tool_version": CORRECTION_TOOL_VERSION,
        "aplicado": False,
    }


def aplicar(caminho: str, url: str, cfg: dict, *, correction_id: str,
            motivo: str, revisor: str, locked_fields=None,
            aplicar_de_fato: bool = False, agora=None) -> dict:
    historico = json.load(io.open(caminho, encoding="utf-8"))
    plano = preparar(historico, url, cfg, correction_id=correction_id,
                     motivo=motivo, revisor=revisor, locked_fields=locked_fields)
    if not aplicar_de_fato:
        plano["motivo_nao_aplicado"] = "dry-run: use aplicar_de_fato=True"
        return plano

    novo_hist = copy.deepcopy(historico)
    rec = novo_hist["articles"][plano["url"]]
    antes_hash = digest_intocavel(rec)
    anterior = {k: copy.deepcopy(rec.get(k)) for k in plano["mudancas"]}
    for k, (_v0, v1) in plano["mudancas"].items():
        if v1 is None:
            rec.pop(k, None)
        else:
            rec[k] = v1
    rec["manual_correction"] = {
        "correction_id": correction_id,
        "corrected_at": (agora or datetime.now(timezone.utc)
                         .astimezone()).isoformat(timespec="seconds"),
        "corrected_by": revisor,
        "tool": CORRECTION_TOOL_VERSION,
        "locked_fields": plano["locked_fields"],
        "reason": motivo,
        "previous_state": anterior,
    }
    if digest_intocavel(rec) != antes_hash:
        raise CorrecaoRecusada("CAMPOS_INTOCAVEIS_ALTERADOS: nada foi gravado")
    outros = [u for u in novo_hist["articles"]
              if u != plano["url"]
              and novo_hist["articles"][u] != historico["articles"][u]]
    if outros:
        raise CorrecaoRecusada(f"MUTACAO_COLATERAL: {outros[:4]}")

    p = Path(caminho)
    bruto = p.read_bytes()
    (p.with_suffix(p.suffix + f".backup_{correction_id}")).write_bytes(bruto)
    tmp = p.with_suffix(p.suffix + ".tmp")
    # mesma serialização do pipeline (`risk_dashboard.py:4211`): sem sort_keys,
    # para que o diff mostre a correção e não o arquivo inteiro reordenado.
    io.open(tmp, "w", encoding="utf-8").write(
        json.dumps(novo_hist, ensure_ascii=False, indent=1))
    tmp.replace(p)
    plano.update(aplicado=True, registros_alterados=1,
                 sha256_antes=hashlib.sha256(bruto).hexdigest(),
                 sha256_depois=hashlib.sha256(p.read_bytes()).hexdigest())
    return plano


def relatorio(p: dict) -> str:
    L = ["REGISTRO ALVO",
         f"  título : {(p['titulo'] or '')[:76]}",
         f"  fonte  : {p['fonte']}",
         f"  url    : {p['url'][:88]}",
         "",
         f"CORREÇÃO  id={p['correction_id']}  revisor={p['revisor']}  "
         f"ferramenta={p['tool_version']}",
         f"  {p['motivo'][:150]}",
         "",
         f"CAMPOS SEMÂNTICOS QUE MUDAM ({len(p['mudancas'])})"]
    for k, (a, b) in sorted(p["mudancas"].items()):
        L.append(f"  {k}")
        L.append(f"     de : {json.dumps(a, ensure_ascii=False)[:110]}")
        L.append(f"     p/ : {json.dumps(b, ensure_ascii=False)[:110]}")
    _lock = (", ".join(p["locked_fields"]) if p["locked_fields"]
             else "(nenhum — o runtime já calcula este estado)")
    L += ["", f"LOCKED_FIELDS: {_lock}",
          "", "CAMPOS INTOCÁVEIS",
          "  " + ("OK — inalterados" if p.get("aplicado")
                  else "conferidos na escrita")]
    if p.get("aplicado"):
        L += ["", "APLICADO",
              f"  registros alterados : {p['registros_alterados']}",
              f"  sha256 antes/depois : {p['sha256_antes'][:16]} → "
              f"{p['sha256_depois'][:16]}"]
    else:
        L += ["", f"ESCRITA  NÃO APLICADA — {p.get('motivo_nao_aplicado','')}"]
    return "\n".join(L)


def main(argv=None) -> int:
    import risk_dashboard as rd
    ap = argparse.ArgumentParser(
        description="Correção versionada de UM registro (dry-run por padrão).")
    ap.add_argument("--url", required=True)
    ap.add_argument("--correction-id", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--reviewer", default="Gustavo")
    ap.add_argument("--lock", default="",
                    help="campos a travar, separados por vírgula (default: nenhum)")
    ap.add_argument("--history", default="risk_history.json")
    ap.add_argument("--config", default="config_risco.yaml")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    cfg = rd.load_config(a.config)
    try:
        p = aplicar(a.history, a.url, cfg, correction_id=a.correction_id,
                    motivo=a.reason, revisor=a.reviewer,
                    locked_fields=[f for f in a.lock.split(",") if f],
                    aplicar_de_fato=a.apply)
    except CorrecaoRecusada as exc:
        print(f"RECUSADO: {exc}")
        return 2
    print(relatorio(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
