#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_sample.py — 4I.2 R7b-A.

A AMOSTRA DO PILOTO — OITO ESTRATOS, DERIVADOS DO CORPUS REAL.

Nenhum item é escolhido a dedo por nome de empresa. Cada estrato é uma
CONSULTA sobre o corpus (`ma` com sujeito herdado, par sem evento, adversarial
de data...), de modo que o mesmo código reexecutado depois de um run produza
uma amostra comparável. Onde o corpus não oferece itens suficientes, o estrato
entrega menos e o denominador real é reportado — nunca se fabrica caso.

DOIS ROTULOS DE PROCEDÊNCIA, que R6f ensinou a nunca misturar:

    DEVELOPMENT_CONTROL  Duke, CVS, L8, GM/W&W, itens do Gold — casos que
                         MOLDARAM as regras. Servem para detectar regressão,
                         jamais para medir desempenho fora de amostra.
    UNSEEN               o resto. Não é OOS prospectivo (o corpus é passado),
                         mas ao menos não foi usado para escrever regra.

Determinismo: ordenação por identidade estável (`sha1(url|empresa|evento)`),
não por ordem de dicionário nem por `random` sem semente.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa
import reliability_pilot_input as pi

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
CONFIG = Path(os.environ.get("RELIABILITY_CONFIG", "config_risco.yaml"))
SAMPLE_VERSION = "r7ba.sample1"

DEVELOPMENT_CONTROL = "DEVELOPMENT_CONTROL"
UNSEEN = "UNSEEN"

# Marcadores textuais dos casos que moldaram regras nas waves R2–R6f. São
# usados só para ROTULAR procedência, nunca para incluir ou excluir.
_CONTROLES = ("duke energy", "cvs health", "omnicare", "general motors",
              "bankruptcy court orders texas", "samarco", "vale", "ypf",
              "supplier alfa", "citigroup")

ALVO = {"S1": 10, "S2": 4, "S3": 4, "S4": 13, "S5": 6, "S6": 12, "S7": 8,
        "S8": 5}

DESCRICAO = {
    "S1": "M&A com sujeito DEFAULTED (a pergunta central: o default acerta?)",
    "S2": "M&A com papel estabelecido por regra (controle)",
    "S3": "M&A falso-positivo/negativo conhecido (ground truth existente)",
    "S4": "famílias THIN de volume (emissao_divida, troca_ceo, rating, incidente)",
    "S5": "jurídico/fraude multi-entidade (development controls)",
    "S6": "pares empresa×artigo SEM evento (OW-2 falsa descoberta)",
    "S7": "tap pré-filtro (OW-3 descoberta aberta real)",
    "S8": "adversarial de atualidade (evento antigo citado hoje)",
}

_ANO = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")
_RETRO = re.compile(
    r"(desde|since|após|apos|after|following|tras|em \d{4}|in \d{4}|"
    r"há \w+ anos|years ago|anniversary|anivers|relembr|retrospect|"
    r"voltou a|havia|hab[ií]a|had been|em 20\d\d)", re.I)


def _ident(url: str, empresa: str, evento: str) -> str:
    return hashlib.sha1(f"{url}|{empresa}|{evento}".encode("utf-8")).hexdigest()[:16]


def _controle(titulo: str, empresa: str) -> bool:
    t = f"{titulo} {empresa}".lower()
    return any(c in t for c in _CONTROLES)


def assessment_determinista(rec: dict, empresa: str, cfg: dict, kws: dict,
                            al: dict) -> dict:
    """Decisões do motor de produção para um par, com a separação que a R7a
    tornou possível: D-RULE (regra nomeada atuou) × D-DEFAULT (herdado)."""
    evs = list(rec.get("event_ids") or [])
    if not evs:
        return {}
    try:
        r = sa.resolve_article_semantics(
            rec.get("title") or "", rec.get("summary") or "", empresa, evs, al,
            article_year=sa._ano_do_registro(rec),
            source_domain=rec.get("domain") or "", keywords_por_evento=kws,
            country=sa._country_de(cfg, empresa))
    except Exception:
        return {}
    return {d.get("event_id"): d for d in (r.get("decisoes") or [])}


def _uea(d: dict, url: str, titulo: str):
    import reliability_universal_assessment as uea
    return uea.montar(d, identity=url, texto=titulo)


def construir(hist: dict, cfg: dict, *, tap: list | None = None) -> dict:
    import reliability_universal_assessment as uea
    kws = sa._keywords_por_evento(cfg)
    al = sa._aliases_map(cfg)
    arts = hist.get("articles") or {}
    v1_disp = set(pi.inventario_v1(hist)["urls"])

    cand = collections.defaultdict(list)
    for url, rec in arts.items():
        titulo = rec.get("title") or ""
        comps = list(rec.get("companies") or [])
        ebc = rec.get("events_by_company") or {}
        dec = {}
        for emp in comps:
            dec = dec or {}
            decisoes = assessment_determinista(rec, emp, cfg, kws, al)
            pontuados = list(ebc.get(emp) or [])

            # S6 — empresa atribuída que não recebeu NENHUM evento
            if not pontuados:
                cand["S6"].append({
                    "url": url, "empresa": emp, "evento": "",
                    "titulo": titulo, "ident": _ident(url, emp, ""),
                    "outras_com_evento": [c for c in comps if ebc.get(c)],
                })

            for ev, d in decisoes.items():
                u = _uea(d, url, titulo)
                item = {
                    "url": url, "empresa": emp, "evento": ev, "titulo": titulo,
                    "ident": _ident(url, emp, ev),
                    "scoreable": bool(d.get("scoreable")),
                    "regra": d.get("attribution_rule") or "",
                    "subject_status": u.subject.status,
                    "role_status": u.company_role.status,
                    "currentness_status": u.currentness.status,
                    "missing": u.missing_dimensions(),
                }
                texto = f"{titulo} {rec.get('summary') or ''}"
                anos = {int(a) for a in _ANO.findall(texto)}
                ano_pub = sa._ano_do_registro(rec) or 0
                if (anos and ano_pub and min(anos) < ano_pub) or _RETRO.search(texto):
                    cand["S8"].append(item)
                if ev in sa.EVENTOS_MA:
                    if not d.get("scoreable"):
                        cand["S3"].append(item)
                    elif u.subject.status == uea.DEFAULTED and not d.get("transaction_role"):
                        cand["S1"].append(item)
                    else:
                        cand["S2"].append(item)
                elif ev in ("emissao_divida", "troca_ceo", "rebaixamento_rating",
                            "incidente_operacional"):
                    cand["S4"].append(item)
                elif ev in sa.EVENTOS_FRAUDE or ev in ("investigacao_regulatoria",
                                                       "investigacao_gestora",
                                                       "falencia",
                                                       "recuperacao_judicial"):
                    if len(comps) > 1 or _controle(titulo, emp):
                        cand["S5"].append(item)

    for it in (tap or []):
        cand["S7"].append({**it, "ident": _ident(it.get("url", ""),
                                                 it.get("empresa", ""), "")})

    # ── seleção determinística, com prioridade dentro do estrato ────────────
    def _chave(estrato):
        def k(i):
            v1 = 0 if i["url"] in v1_disp else 1          # prioriza quem tem V1
            if estrato == "S4":
                return (v1, i.get("evento", ""), i["ident"])
            if estrato == "S6":
                return (v1, 0 if i.get("outras_com_evento") else 1, i["ident"])
            return (v1, i["ident"])
        return k

    escolhidos, vistos = {}, set()
    for e in ("S3", "S5", "S8", "S1", "S2", "S4", "S6", "S7"):
        pool = [i for i in cand.get(e, []) if i["ident"] not in vistos]
        if e == "S4":  # equilibra as quatro famílias em vez de pegar 13 de uma
            porfam = collections.defaultdict(list)
            for i in sorted(pool, key=_chave(e)):
                porfam[i["evento"]].append(i)
            sel, i_ = [], 0
            while len(sel) < ALVO[e] and any(porfam.values()):
                for fam in list(porfam):
                    if porfam[fam] and len(sel) < ALVO[e]:
                        sel.append(porfam[fam].pop(0))
                i_ += 1
                if i_ > 50:
                    break
        else:
            sel = sorted(pool, key=_chave(e))[:ALVO[e]]
        for i in sel:
            vistos.add(i["ident"])
            i["estrato"] = e
            i["procedencia"] = (DEVELOPMENT_CONTROL
                                if _controle(i.get("titulo", ""), i.get("empresa", ""))
                                else UNSEEN)
            i["v1_disponivel"] = i["url"] in v1_disp
        escolhidos[e] = sel

    itens = [i for e in ALVO for i in escolhidos.get(e, [])]
    return {
        "sample_version": SAMPLE_VERSION,
        "alvo": ALVO, "descricao": DESCRICAO,
        "disponivel": {e: len(cand.get(e, [])) for e in ALVO},
        "selecionado": {e: len(escolhidos.get(e, [])) for e in ALVO},
        "procedencia": dict(collections.Counter(i["procedencia"] for i in itens)),
        "v1_disponivel": sum(1 for i in itens if i["v1_disponivel"]),
        "itens": itens,
    }


def imprimir(s: dict):
    print("=" * 104)
    print(f"AMOSTRA DO PILOTO R7b-A — {s['sample_version']}")
    print("=" * 104)
    print(f"  {'estrato':8s} {'alvo':>5s} {'disp':>6s} {'sel':>5s}   descrição")
    print("  " + "-" * 100)
    for e in ALVO:
        print(f"  {e:8s} {ALVO[e]:5d} {s['disponivel'].get(e, 0):6d} "
              f"{s['selecionado'].get(e, 0):5d}   {DESCRICAO[e]}")
    tot = sum(s["selecionado"].values())
    print("  " + "-" * 100)
    print(f"  {'TOTAL':8s} {sum(ALVO.values()):5d} {'':6s} {tot:5d}")
    print()
    print(f"  procedência        : {s['procedencia']}")
    print(f"  com V1 disponível  : {s['v1_disponivel']}/{tot}")
    falt = [e for e in ALVO if s["selecionado"].get(e, 0) < ALVO[e]]
    if falt:
        print(f"  ⚠️  estratos abaixo do alvo (denominador real reportado): {falt}")
    print("=" * 104)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out_reliability/r7b_a/sample_manifest.json")
    ap.add_argument("--tap", default="")
    a = ap.parse_args()
    cfg = rd.load_config(str(CONFIG))
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    tap = []
    if a.tap and Path(a.tap).exists():
        tap = json.load(io.open(a.tap, encoding="utf-8")).get("itens") or []
    s = construir(hist, cfg, tap=tap)
    imprimir(s)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"  → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
