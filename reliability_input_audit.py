#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_audit.py — 4I.2 R5a.

Mede QUANTO TEXTO o classificador realmente recebe, por registro e por fonte.

A pergunta que motiva o módulo: os três Duke bloqueados por falta de sinal são
uma exceção ou o sintoma de um pipeline que persiste quase só o título? Sem
essa medição, "enriquecer o input" é palpite.

Nada aqui altera `risk_history.json`, semântica ou score. É leitura pura.

Métrica central — GANHO EFETIVO. Um campo não conta como enriquecimento só
por estar preenchido: `summary` do Google News costuma ser o próprio título
mais o nome da fonte. Medimos os tokens do candidato que NÃO aparecem no
título, o que é determinístico e não precisa de modelo nenhum.

Uso:
    python reliability_input_audit.py            # relatório
    python reliability_input_audit.py --json     # saída legível por máquina
"""
from __future__ import annotations

import io
import json
import os
import re
import statistics
import sys
import unicodedata
from pathlib import Path

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY") or "risk_history.json")
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR") or "out_reliability")

# Campos textuais que o registro pode carregar. `title` é a referência.
CAMPOS_TEXTO = ("title", "summary", "description", "content", "body",
                "article_body", "excerpt", "snippet", "resumo", "texto")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


def _tokens(s: str) -> set:
    """Tokens de conteúdo: descarta pontuação e palavras de 1-2 letras, que
    não carregam papel semântico e inflariam o ganho artificialmente."""
    return {t for t in re.findall(r"[a-z0-9]+", _norm(s)) if len(t) > 2}


def ganho_efetivo(base: str, candidato: str) -> dict:
    """Quanto do candidato é conteúdo NOVO em relação à base.

    `containment` = fração dos tokens do candidato já presentes na base. 1.0
    significa repetição pura; é o caso do `summary` do Google News.
    """
    tb, tc = _tokens(base), _tokens(candidato)
    if not tc:
        return {"tokens_novos": 0, "containment": 1.0, "chars_novos": 0,
                "duplicado": True}
    novos = tc - tb
    # chars novos: aproximação honesta pelo comprimento dos tokens inéditos
    chars = sum(len(t) for t in novos)
    return {"tokens_novos": len(novos),
            "containment": round(1 - len(novos) / len(tc), 3),
            "chars_novos": chars,
            "duplicado": not novos}


def _p(vals, q):
    if not vals:
        return 0
    vals = sorted(vals)
    i = min(len(vals) - 1, int(round(q * (len(vals) - 1))))
    return vals[i]


def auditar() -> dict:
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    arts = hist["articles"]
    campos = {c: {"presentes": 0, "nao_vazios": 0, "tamanhos": [],
                  "dup_title": 0, "com_ganho": 0, "tokens_novos": []}
              for c in CAMPOS_TEXTO}
    por_fonte, linhas = {}, []

    for url, rec in arts.items():
        titulo = rec.get("title") or ""
        for c in CAMPOS_TEXTO:
            if c not in rec:
                continue
            campos[c]["presentes"] += 1
            v = rec.get(c) or ""
            if not isinstance(v, str) or not v.strip():
                continue
            campos[c]["nao_vazios"] += 1
            campos[c]["tamanhos"].append(len(v))
            if c == "title":
                continue
            g = ganho_efetivo(titulo, v)
            if g["duplicado"]:
                campos[c]["dup_title"] += 1
            else:
                campos[c]["com_ganho"] += 1
                campos[c]["tokens_novos"].append(g["tokens_novos"])

        resumo = rec.get("summary") or ""
        g = ganho_efetivo(titulo, resumo)
        # classe do input efetivo deste registro
        if not resumo.strip():
            classe = "SO_TITULO"
        elif g["duplicado"]:
            classe = "TITULO_MAIS_SUMARIO_DUPLICADO"
        elif g["tokens_novos"] < 5:
            classe = "GANHO_MARGINAL"
        else:
            classe = "SUMARIO_COM_CONTEUDO"
        fonte = rec.get("source") or "(sem fonte)"
        d = por_fonte.setdefault(fonte, {"records": 0, "SO_TITULO": 0,
                                         "TITULO_MAIS_SUMARIO_DUPLICADO": 0,
                                         "GANHO_MARGINAL": 0,
                                         "SUMARIO_COM_CONTEUDO": 0})
        d["records"] += 1
        d[classe] += 1
        linhas.append({"url": url, "title": titulo, "source": fonte,
                       "classe": classe, "tokens_novos": g["tokens_novos"],
                       "chars_title": len(titulo), "chars_summary": len(resumo)})

    resumo_campos = {}
    for c, d in campos.items():
        if not d["presentes"]:
            continue
        resumo_campos[c] = {
            "presentes": d["presentes"], "nao_vazios": d["nao_vazios"],
            "cobertura": round(d["nao_vazios"] / len(arts) * 100, 1),
            "mediana_chars": int(statistics.median(d["tamanhos"])) if d["tamanhos"] else 0,
            "p90_chars": _p(d["tamanhos"], 0.9),
            "duplicado_com_title": d["dup_title"],
            "com_ganho_sobre_title": d["com_ganho"],
            "mediana_tokens_novos": (int(statistics.median(d["tokens_novos"]))
                                     if d["tokens_novos"] else 0),
        }

    classes = {k: sum(1 for l in linhas if l["classe"] == k)
               for k in ("SO_TITULO", "TITULO_MAIS_SUMARIO_DUPLICADO",
                         "GANHO_MARGINAL", "SUMARIO_COM_CONTEUDO")}
    return {"records": len(arts), "campos": resumo_campos, "classes": classes,
            "por_fonte": por_fonte, "linhas": linhas}


def imprimir(r: dict) -> int:
    print("=" * 96)
    print("INPUT AUDIT — quanto texto o classificador realmente recebe")
    print("=" * 96)
    print(f"  registros: {r['records']}")
    print("\nCAMPOS TEXTUAIS PRESENTES NO HISTORY")
    print(f"  {'campo':14s} {'não-vazios':>11s} {'cobertura':>10s} "
          f"{'mediana':>8s} {'p90':>6s} {'dup c/ title':>13s} {'c/ ganho':>9s}")
    for c, d in r["campos"].items():
        print(f"  {c:14s} {d['nao_vazios']:>11d} {d['cobertura']:>9.1f}% "
              f"{d['mediana_chars']:>8d} {d['p90_chars']:>6d} "
              f"{d['duplicado_com_title']:>13d} {d['com_ganho_sobre_title']:>9d}")

    print("\nCLASSE DO INPUT EFETIVO, POR REGISTRO")
    tot = r["records"]
    for k, v in r["classes"].items():
        print(f"  {k:32s} {v:>5d}  {v / tot * 100:>5.1f}%")

    print("\nTOP FONTES POR VOLUME (classe do input)")
    top = sorted(r["por_fonte"].items(), key=lambda kv: -kv[1]["records"])[:12]
    print(f"  {'fonte':34s} {'recs':>5s} {'só tít':>7s} {'dup':>5s} "
          f"{'marg':>5s} {'conteúdo':>9s}")
    for f, d in top:
        print(f"  {f[:34]:34s} {d['records']:>5d} {d['SO_TITULO']:>7d} "
              f"{d['TITULO_MAIS_SUMARIO_DUPLICADO']:>5d} {d['GANHO_MARGINAL']:>5d} "
              f"{d['SUMARIO_COM_CONTEUDO']:>9d}")
    print("=" * 96)
    return 0


def main() -> int:
    r = auditar()
    OUTDIR.mkdir(exist_ok=True)
    (OUTDIR / "input_audit.json").write_text(
        json.dumps({k: v for k, v in r.items() if k != "linhas"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    return imprimir(r)


if __name__ == "__main__":
    raise SystemExit(main())
