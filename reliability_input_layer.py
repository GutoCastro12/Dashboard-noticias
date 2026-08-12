#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_layer.py — 4I.2 R7c.

A CAMADA DE INPUT, COM O ARTIGO COMO UNIDADE.

R7b-A mediu o teto de tudo o que veio antes: 0 de 751 registros do corpus
passam na suficiência de input, 750 têm menos de 200 caracteres úteis, e o
`summary` guardado é o título repetido seguido de " &nbsp;&nbsp; Fonte" — o
formato do RSS do Google News. Seis waves de semântica foram gastas ensinando
o sistema a ler melhor um texto que, na prática, é uma manchete.

Esta camada existe para mudar o texto, não a interpretação. Ela não classifica,
não atribui, não pontua e não decide nada — produz o MELHOR TEXTO LIMPO
DISPONÍVEL por artigo, com procedência e qualidade declaradas, e mede o que
não conseguiu.

POR QUE O ARTIGO, E NÃO `article × company` OU `article × event`:

    artigo
      ├── empresa A → candidatos [ma]
      ├── empresa B → candidatos []        ← existe, e hoje some
      └── empresa C → candidatos [falencia]

O texto é UM só. Enriquecer por par empresa×evento faria a mesma página ser
buscada três vezes e, pior, amarraria a elegibilidade do input à existência de
candidato — que é exatamente o viés que impede descoberta aberta. A empresa B
do desenho é o caso que o pipeline hoje descarta antes do history: 78% dos
artigos atribuídos, medidos pelo tap da R7b-A.

DESLIGADA POR PADRÃO. `ATIVO = False`, nenhuma escrita em produção, nenhuma
chamada de LLM, nenhum efeito sobre score. A ativação é decisão de outra wave.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import time
from pathlib import Path

from reliability_pilot_contract import genero_da_fonte, normalizar
import reliability_pilot_input as pi

INPUT_LAYER_VERSION = "r7c.1"
LADDER_VERSION = "r7c.ladder1"

# Interruptor único. Como em `shadow_fraud_roles`, o default é OFF: um flag que
# precisa ser ligado nunca chega à produção por esquecimento.
ATIVO = False

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7c"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
SIDECAR_PROD = Path("risk_enrichment_shadow.json")

# Estados da escada — nomeados para telemetria, não para lógica de negócio.
R0_ARMAZENADO = "R0_ARMAZENADO"
R1_ESTRUTURADO = "R1_ESTRUTURADO"
R2_CORPO = "R2_CORPO"
INSUFICIENTE = "INSUFICIENTE"
BLOQUEADO = "BLOQUEADO"
NAO_TENTADO = "NAO_TENTADO"

# Motivos de fracasso, separados porque exigem respostas diferentes: robots é
# política do site (não adianta insistir), fetch falho é transitório (vale
# reter), e "sem fragmento útil" é qualidade (o contrato R5b rejeitou o que
# veio). Misturá-los produziria a métrica inútil "não deu certo".
MOTIVOS = ("robots", "fetch_falhou", "sem_fragmento_util", "sem_url_resolvida",
           "nao_aplicavel", "ok")

_RUIDO_RSS = re.compile(r"&nbsp;|\s+—\s+[\w\s.]{2,40}$|\s{2,}[A-Z][\w\s.]{2,30}$")


def identidade(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


# ── qualidade do texto ──────────────────────────────────────────────────────
def qualidade_do_texto(titulo: str, texto: str) -> dict:
    """Perfil objetivo do input. `duplicacao_titulo` é o diagnóstico central
    do corpus atual: o resumo do Google News repete a manchete, então medir
    `len(summary)` sem descontar o título superestima o input em ~5x."""
    t = normalizar(texto or "")
    tit = normalizar(titulo or "")
    uteis = pi.chars_uteis(t, tit)
    dup = 0.0
    if tit and t:
        dup = round(min(1.0, len(tit) / max(1, len(t))), 3) if tit.lower() in t.lower() else 0.0
    return {
        "chars_totais": len(t),
        "chars_uteis": uteis,
        "frases": len([s for s in re.split(r"[.!?]+\s", t) if len(s.strip()) > 25]),
        "duplicacao_titulo": dup,
        "ruido_rss": bool(_RUIDO_RSS.search(texto or "")),
        "suficiente": pi.suficiente(t, tit)["suficiente"],
    }


# ── o registro por artigo ───────────────────────────────────────────────────
def montar_article_input(*, url: str, titulo: str, resumo: str = "",
                         dominio: str = "", pub_iso: str = "",
                         empresas: dict | None = None,
                         run: int | None = None) -> dict:
    """Um artigo, todas as empresas atribuídas, com e sem candidato.

    `empresas` é {nome: [event_ids]}. Uma lista VAZIA é informação, não
    ausência de informação: é a empresa que o pipeline atribuiu e para a qual
    nenhum evento da taxonomia se aplicou."""
    emps = empresas or {}
    base = f"{titulo}. {resumo}".strip() if resumo else (titulo or "")
    q = qualidade_do_texto(titulo, base)
    return {
        "article_id": identidade(url),
        "url": url,
        "titulo": titulo or "",
        "pub_iso": pub_iso or "",
        "dominio": dominio or "",
        "source_genre": genero_da_fonte(dominio),
        "empresas": [{"empresa": e, "candidatos": sorted(evs or []),
                      "tem_candidato": bool(evs)} for e, evs in sorted(emps.items())],
        "n_empresas": len(emps),
        "n_empresas_sem_candidato": sum(1 for evs in emps.values() if not evs),
        "texto_base": pi.truncar_neutro(base),
        "qualidade_r0": q,
        "escada": {"estado": R0_ARMAZENADO if q["suficiente"] else NAO_TENTADO,
                   "motivo": "ok" if q["suficiente"] else "nao_aplicavel",
                   "tentativas": []},
        "melhor_texto": pi.truncar_neutro(base),
        "melhor_origem": R0_ARMAZENADO,
        "input_layer_version": INPUT_LAYER_VERSION,
        "ladder_version": LADDER_VERSION,
        "input_policy_version": pi.INPUT_POLICY_VERSION,
        "capturado_em": int(time.time()),
        "first_seen_run": run,
    }


def _do_sidecar(url: str, titulo: str, side: dict) -> tuple:
    """Reaproveita o que a produção já buscou. Sem rede e sem custo: 52 URLs
    foram tentadas nas waves R5c/R6f e 17 têm fragmento que sobrevive ao
    contrato R5b."""
    reg = ((side or {}).get("articles") or {}).get(url) or {}
    if not reg:
        return "", "", NAO_TENTADO, "nao_tentado"
    texto, metodo = pi._fragmentos_uteis(reg, titulo)
    if texto:
        tier = R2_CORPO if "paragrafo" in (metodo or "") else R1_ESTRUTURADO
        return texto, metodo, tier, "ok"
    st = reg.get("status") or ""
    motivo = ("robots" if "ROBOTS" in st.upper()
              else "fetch_falhou" if "FETCH" in st.upper() or "ERRO" in st.upper()
              else "sem_fragmento_util")
    return "", "", BLOQUEADO if motivo == "robots" else INSUFICIENTE, motivo


def subir_escada(ai: dict, *, permitir_rede: bool = False,
                 sidecar: dict | None = None, rec: dict | None = None) -> dict:
    """R0 → R1/R2 → INSUFICIENTE, parando assim que bastar.

    Contrato herdado de R5b e mantido: SEM CONTEXTO É MELHOR QUE CONTEXTO
    SUJO. Um fragmento que não passa no filtro de qualidade não é usado nem
    como "melhor esforço" — boilerplate de portal já reintroduziu um falso
    positivo corrigido numa wave anterior."""
    if ai["qualidade_r0"]["suficiente"]:
        ai["escada"]["estado"] = R0_ARMAZENADO
        ai["escada"]["motivo"] = "ok"
        ai["escada"]["tentativas"].append({"tier": "R0", "resultado": "ok",
                                           "rede": False})
        return ai

    side = sidecar
    if side is None and SIDECAR_PROD.exists():
        try:
            side = json.load(io.open(SIDECAR_PROD, encoding="utf-8"))
        except Exception:
            side = {}
    texto, metodo, estado, motivo = _do_sidecar(ai["url"], ai["titulo"], side or {})
    ai["escada"]["tentativas"].append(
        {"tier": "R1/R2 (sidecar)", "resultado": motivo, "rede": False,
         "metodo": metodo})

    if not texto and permitir_rede:
        try:
            import reliability_enrichment_sidecar as sc
            novo = sc.enriquecer_url(ai["url"], ai["titulo"], rec or {})
            texto, metodo = pi._fragmentos_uteis(novo, ai["titulo"])
            st = novo.get("status") or ""
            motivo = ("ok" if texto else
                      "robots" if "ROBOTS" in st.upper() else "sem_fragmento_util")
            estado = (R2_CORPO if texto and "paragrafo" in (metodo or "")
                      else R1_ESTRUTURADO if texto
                      else BLOQUEADO if motivo == "robots" else INSUFICIENTE)
            ai["escada"]["tentativas"].append(
                {"tier": "R1/R2 (rede)", "resultado": motivo, "rede": True,
                 "metodo": metodo})
        except Exception as exc:
            ai["escada"]["tentativas"].append(
                {"tier": "R1/R2 (rede)", "resultado": f"erro:{type(exc).__name__}",
                 "rede": True})

    if texto:
        combinado = pi.truncar_neutro(f"{ai['titulo']}. {texto}")
        ai["melhor_texto"] = combinado
        ai["melhor_origem"] = f"{estado}:{metodo}"
        ai["qualidade_r1"] = qualidade_do_texto(ai["titulo"], combinado)
        ai["escada"]["estado"] = estado
        ai["escada"]["motivo"] = "ok"
    else:
        ai["escada"]["estado"] = estado if estado != NAO_TENTADO else INSUFICIENTE
        ai["escada"]["motivo"] = motivo
    return ai


# ── varredura do corpus ─────────────────────────────────────────────────────
def do_registro(url: str, rec: dict, run: int | None = None) -> dict:
    ebc = rec.get("events_by_company") or {}
    emps = {}
    for c in (rec.get("companies") or []):
        emps[c] = list(ebc.get(c) or [])
    for c, evs in ebc.items():
        emps.setdefault(c, list(evs or []))
    return montar_article_input(
        url=url, titulo=rec.get("title") or "", resumo=rec.get("summary") or "",
        dominio=rec.get("domain") or "", pub_iso=rec.get("pub_iso") or "",
        empresas=emps, run=run)


def varrer(hist: dict, *, limite: int | None = None,
           permitir_rede: bool = False) -> list:
    side = {}
    if SIDECAR_PROD.exists():
        try:
            side = json.load(io.open(SIDECAR_PROD, encoding="utf-8"))
        except Exception:
            side = {}
    itens = list((hist.get("articles") or {}).items())
    if limite:
        itens = itens[:limite]
    out = []
    for url, rec in itens:
        ai = do_registro(url, rec)
        out.append(subir_escada(ai, permitir_rede=permitir_rede, sidecar=side,
                                rec=rec))
    return out


def telemetria(ais: list) -> dict:
    """O que a camada mede. Nenhum destes números altera decisão de produção —
    eles dizem o que a produção NÃO está conseguindo ler."""
    est = collections.Counter(a["escada"]["estado"] for a in ais)
    mot = collections.Counter(a["escada"]["motivo"] for a in ais)
    suf0 = sum(1 for a in ais if a["qualidade_r0"]["suficiente"])
    suf1 = sum(1 for a in ais if (a.get("qualidade_r1") or {}).get("suficiente"))
    ganho = [(a.get("qualidade_r1") or {}).get("chars_uteis", 0)
             - a["qualidade_r0"]["chars_uteis"]
             for a in ais if a.get("qualidade_r1")]
    dup = [a["qualidade_r0"]["duplicacao_titulo"] for a in ais]
    sem_cand = sum(a["n_empresas_sem_candidato"] for a in ais)
    pares = sum(a["n_empresas"] for a in ais)
    return {
        "artigos": len(ais),
        "pares_empresa_artigo": pares,
        "pares_sem_candidato": sem_cand,
        "artigos_multiempresa": sum(1 for a in ais if a["n_empresas"] > 1),
        "suficientes_R0": suf0,
        "suficientes_apos_escada": suf0 + suf1,
        "estados": dict(est),
        "motivos": dict(mot),
        "ruido_rss": sum(1 for a in ais if a["qualidade_r0"]["ruido_rss"]),
        "duplicacao_titulo_media": round(sum(dup) / max(1, len(dup)), 3),
        "ganho_mediano_chars": (sorted(ganho)[len(ganho) // 2] if ganho else 0),
        "genero": dict(collections.Counter(a["source_genre"] for a in ais)),
        "input_layer_version": INPUT_LAYER_VERSION,
        "ladder_version": LADDER_VERSION,
        "input_policy_version": pi.INPUT_POLICY_VERSION,
        "ativo": ATIVO,
    }


def imprimir(t: dict):
    print("=" * 96)
    print(f"CAMADA DE INPUT — {t['input_layer_version']} "
          f"(ativo={t['ativo']}, escada={t['ladder_version']})")
    print("=" * 96)
    print(f"  artigos                      : {t['artigos']}")
    print(f"  pares empresa × artigo       : {t['pares_empresa_artigo']}")
    print(f"    sem candidato de taxonomia : {t['pares_sem_candidato']}")
    print(f"  artigos multi-empresa        : {t['artigos_multiempresa']}")
    print()
    print(f"  suficientes em R0            : {t['suficientes_R0']}/{t['artigos']}")
    print(f"  suficientes após a escada    : {t['suficientes_apos_escada']}/{t['artigos']}")
    print(f"  ganho mediano quando enriquece: {t['ganho_mediano_chars']} chars úteis")
    print()
    print(f"  estados : {t['estados']}")
    print(f"  motivos : {t['motivos']}")
    print()
    print(f"  ruído de RSS no resumo       : {t['ruido_rss']}/{t['artigos']}")
    print(f"  duplicação título↔resumo méd.: {t['duplicacao_titulo_media']}")
    print(f"  gênero da fonte              : {t['genero']}")
    print("=" * 96)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--permitir-rede", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    ais = varrer(hist, limite=a.limite, permitir_rede=a.permitir_rede)
    t = telemetria(ais)
    imprimir(t)
    if a.json:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        io.open(OUTDIR / "input_quality.json", "w", encoding="utf-8").write(
            json.dumps({"telemetria": t, "amostra": ais[:40]},
                       ensure_ascii=False, indent=2, sort_keys=True, default=str))
        print(f"  → {OUTDIR / 'input_quality.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
