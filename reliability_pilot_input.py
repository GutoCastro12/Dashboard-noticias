#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_input.py — 4I.2 R7b-A.

ESCADA DE INPUT EXPERIMENTAL: V0 (texto armazenado) e V1 (enriquecido).

R7a mediu o que nenhuma camada semântica supera: 749 dos 751 registros têm
menos de 500 caracteres, porque `translation.max_chars` corta em 400. Um
extrator — determinístico ou LLM — que recebe manchete não pode ser cobrado
por não enxergar sujeito, papel ou atualidade. Este módulo separa as duas
perguntas: quanto o instrumento erra, e quanto o instrumento simplesmente não
tinha o que ler.

A SUFICIÊNCIA É DETERMINÍSTICA E VERSIONADA (`INPUT_POLICY_VERSION`). Não se
usa LLM para decidir se vale chamar o LLM — isso trocaria um custo conhecido
por um viés desconhecido, e tornaria o denominador dependente do próprio
objeto de estudo.

V1 usa rede, e SÓ em subconjunto: reaproveita o `reliability_enrichment_sidecar`
das waves R5/R6 (mesma escada Tier 0/1/2, mesmo contrato "sem contexto é
melhor que contexto sujo"). Grava exclusivamente em diretório experimental —
nunca no sidecar de produção.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from reliability_pilot_contract import (INPUT_POLICY_VERSION, normalizar,
                                        genero_da_fonte)

# ── política de suficiência ─────────────────────────────────────────────────
MIN_CHARS_UTEIS = 600      # abaixo disso, praticamente só manchete
MIN_FRASES = 3
MAX_CHARS_ENVIADOS = 5000  # truncamento neutro (lede), nunca por keyword

_BOILERPLATE = re.compile(
    r"(all rights reserved|todos os direitos reservados|leia mais|read more|"
    r"assine|subscribe|cookies?|aceit[ae]r? cookies|continue lendo|"
    r"clique aqui|sign up|newsletter)", re.I)

INSUFICIENTE = "INSUFFICIENT_INPUT"


def _frases(t: str) -> int:
    return len([s for s in re.split(r"[.!?]+\s", t or "") if len(s.strip()) > 25])


def chars_uteis(texto: str, titulo: str = "") -> int:
    """Conta caracteres que acrescentam informação ao título.

    Um `summary` que repete o título não é input: no corpus atual isso é
    comum, e contá-lo inflaria a suficiência exatamente nos casos mais pobres."""
    t = normalizar(texto)
    tit = normalizar(titulo)
    if tit and tit.lower() in t.lower():
        t = re.sub(re.escape(tit), " ", t, flags=re.I)
    t = _BOILERPLATE.sub(" ", t)
    return len(re.sub(r"\s+", " ", t).strip())


def suficiente(texto: str, titulo: str = "") -> dict:
    n = chars_uteis(texto, titulo)
    f = _frases(texto)
    ok = n >= MIN_CHARS_UTEIS and f >= MIN_FRASES
    return {"suficiente": ok, "chars_uteis": n, "frases": f,
            "motivo": "" if ok else
                      (f"chars_uteis={n}<{MIN_CHARS_UTEIS}" if n < MIN_CHARS_UTEIS
                       else f"frases={f}<{MIN_FRASES}"),
            "input_policy_version": INPUT_POLICY_VERSION}


def truncar_neutro(texto: str, limite: int = MAX_CHARS_ENVIADOS) -> str:
    """Trunca pelo início (lede). NUNCA por janela de keyword: a discovery
    cega existe para achar a frase inesperada, e recortar em volta do
    vocabulário de risco eliminaria justamente o que se quer descobrir."""
    t = normalizar(texto)
    if len(t) <= limite:
        return t
    corte = t[:limite]
    ult = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
    return corte[:ult + 1] if ult > limite * 0.6 else corte


# ── V0: só o que já está no history ─────────────────────────────────────────
def montar_v0(rec: dict) -> dict:
    titulo = rec.get("title") or ""
    resumo = rec.get("summary") or ""
    texto = truncar_neutro(f"{titulo}. {resumo}".strip() if resumo else titulo)
    suf = suficiente(texto, titulo)
    return {"variant": "V0", "texto": texto, "titulo": titulo,
            "pub_iso": rec.get("pub_iso") or "",
            "genero": genero_da_fonte(rec.get("domain") or ""),
            "origem": "history", **suf}


# ── V1: escada com rede, em subconjunto ─────────────────────────────────────
SIDECAR = Path("risk_enrichment_shadow.json")


def _fragmentos_uteis(reg: dict, titulo: str) -> tuple[str, str]:
    """Aplica o MESMO filtro de qualidade das waves R5/R6 aos fragmentos já
    coletados. O contrato é o de R5b — sem contexto é melhor que contexto
    sujo: boilerplate de portal ("Últimas noticias de …") tem `sentence_like`
    falso e `malformed_text`, e é descartado mesmo sendo o maior fragmento."""
    try:
        import reliability_enrichment_sidecar as sc
    except Exception:
        return "", ""
    bons = [f for f in (reg.get("fragments") or []) if sc.suficiente(f)]
    if not bons:
        return "", ""
    sel, _motivo = sc.selecionar(bons)
    metodo = (sel or bons[0]).get("method", "?")
    texto = " ".join(f.get("text_excerpt") or "" for f in bons)
    return texto, metodo


def montar_v1(rec: dict, url: str, *, permitir_rede: bool = False,
              sidecar: dict | None = None) -> dict:
    """R0 (V0) → R1/R2 via enrichment → INSUFICIENTE.

    Duas origens, nesta ordem: (1) o sidecar de produção JÁ coletado, que é
    leitura pura — 17 artigos do corpus têm fragmento útil e servem de
    subconjunto V0/V1 sem gastar uma requisição; (2) rede, só se autorizada
    explicitamente. Nunca inventa texto: sem fragmento, devolve V0 marcado."""
    v0 = montar_v0(rec)
    base = {"variant": "V1", "titulo": v0["titulo"], "pub_iso": v0["pub_iso"],
            "genero": v0["genero"]}

    side = sidecar
    if side is None and SIDECAR.exists():
        try:
            side = json.load(io.open(SIDECAR, encoding="utf-8"))
        except Exception:
            side = {}
    reg = ((side or {}).get("articles") or {}).get(url) or {}

    frag, metodo, rede = "", "", False
    if reg:
        frag, metodo = _fragmentos_uteis(reg, v0["titulo"])
    if not frag and permitir_rede:
        try:
            import reliability_enrichment_sidecar as sc
            novo = sc.enriquecer_url(url, v0["titulo"], rec)
            rede = True
            frag, metodo = _fragmentos_uteis(novo, v0["titulo"])
        except Exception as exc:
            return {**v0, **base, "origem": "v0_fallback", "rede": True,
                    "nota": f"falha no enrichment: {type(exc).__name__}"}

    if not frag:
        motivo = (reg.get("status") or "nao_tentado") if reg else "nao_tentado"
        return {**v0, **base, "origem": "v0_fallback", "rede": rede,
                "nota": f"sem fragmento útil ({motivo})"}

    texto = truncar_neutro(f"{v0['titulo']}. {frag}")
    suf = suficiente(texto, v0["titulo"])
    return {**base, "texto": texto, "origem": f"enrichment:{metodo}",
            "rede": rede, "nota": "", **suf}


def inventario_v1(hist: dict) -> dict:
    """Quantos artigos do corpus têm V1 REAL disponível sem gastar rede."""
    if not SIDECAR.exists():
        return {"disponiveis": 0, "tentados": 0, "urls": []}
    side = json.load(io.open(SIDECAR, encoding="utf-8"))
    arts = (hist.get("articles") or {})
    urls = []
    for u, reg in (side.get("articles") or {}).items():
        if u in arts and _fragmentos_uteis(reg, arts[u].get("title") or "")[0]:
            urls.append(u)
    return {"disponiveis": len(urls), "tentados": len(side.get("articles") or {}),
            "urls": urls}


def censo_de_suficiencia(hist: dict, limite: int | None = None) -> dict:
    """Perfil de input do corpus — o número que diz se vale gastar V1."""
    itens = list((hist.get("articles") or {}).items())
    if limite:
        itens = itens[:limite]
    tot = suf = 0
    faixas = {"<200": 0, "200-599": 0, "600-1499": 0, ">=1500": 0}
    for _u, rec in itens:
        v0 = montar_v0(rec)
        tot += 1
        suf += 1 if v0["suficiente"] else 0
        n = v0["chars_uteis"]
        faixas["<200" if n < 200 else "200-599" if n < 600
               else "600-1499" if n < 1500 else ">=1500"] += 1
    return {"total": tot, "suficientes_v0": suf,
            "insuficientes_v0": tot - suf, "faixas": faixas,
            "input_policy_version": INPUT_POLICY_VERSION}


if __name__ == "__main__":
    h = json.load(io.open(Path("risk_history.json"), encoding="utf-8"))
    c = censo_de_suficiencia(h)
    print(json.dumps(c, ensure_ascii=False, indent=2))
