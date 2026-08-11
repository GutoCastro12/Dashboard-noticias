#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment_policy.py — 4I.2 R5b.

Decide QUAIS artigos merecem uma requisição extra. Enriquecer tudo custaria
centenas de requests por ciclo para mudar quase nada: a R5a mostrou que o
enrichment só é material quando a classificação depende de contexto que o
título não carrega.

O critério não pode olhar ground truth humano nem nome de empresa — ele roda
no momento da coleta, quando ninguém adjudicou nada ainda. Usa apenas o que o
pipeline já tem em mãos: severidade do evento candidato, quanto conteúdo o
`summary` acrescenta ao título, e marcas textuais de que o SUJEITO do evento
está em disputa (nome de caso, papel processual, múltiplas entidades).

Nada aqui altera scoring: é elegibilidade de coleta, não decisão semântica.
"""
from __future__ import annotations

import re

import reliability_input_audit as ia

POLICY_VERSION = "r5b.1"

# Um `summary` que não acrescenta nada além do veículo deixa a decisão inteira
# nas costas do título. Abaixo deste limiar o input é considerado marginal.
MIN_TOKENS_NOVOS = 5

# Construções em que o nome da empresa aparece SEM que o papel dela esteja
# dito. São exatamente os padrões que já custaram falsos positivos: nome de
# caso ("<Empresa> fraud case"), foro, e menções processuais em que a
# monitorada pode ser autora, ré, vítima ou apenas rótulo do processo.
_AMBIGUIDADE_DE_PAPEL = [
    (r"\b(?:fraud|fraude|scam|golpe)\s+(?:case|investigation|probe|scheme|"
     r"caso|investiga\w+|esquema)\b", "nome_de_caso"),
    (r"\b(?:case|caso|investigation|investiga\w+|probe|inqu[ée]rito|lawsuit|"
     r"a[çc][ãa]o|processo|suit)\b", "termo_processual"),
    (r"\b(?:court|tribunal|juizo|ju[íi]zo|vara|judge|juiz|deputies|"
     r"sheriff|pol[íi]cia|police|promotor|prosecutor)\b", "ator_judicial"),
    (r"\b(?:arrested|charged|sentenced|indicted|preso|detido|denunciado|"
     r"condenado|acusado|suspect|suspeito)\b", "individuo_no_titulo"),
    (r"\b(?:ex[- ]?ceo|ex[- ]?presidente|former\s+(?:ceo|chairman|president|"
     r"director)|antigo\s+diretor)\b", "afiliacao_individual"),
]

# Famílias de evento cuja atribuição depende de SUJEITO/PAPEL, medidas nas
# waves anteriores: insolvência, fraude, investigação, default e M&A. Não é
# lista de casos conhecidos — é o conjunto de famílias em que erramos por
# falta de contexto, e todas continuam valendo para notícias novas.
FAMILIAS_SENSIVEIS = {
    "falencia", "recuperacao_judicial", "default", "fraude",
    "investigacao_regulatoria", "investigacao_gestora", "ma", "liquidacao",
}


def sinais(rec: dict, cfg: dict) -> dict:
    """Sinais observáveis no momento da coleta. Zero ground truth."""
    sev = {e["id"]: e.get("severity") for e in (cfg.get("taxonomy") or [])}
    titulo = rec.get("title") or ""
    resumo = rec.get("summary") or ""
    # O candidato bruto é `event_ids`, produzido pela classificação ANTES de a
    # semântica decidir sujeito. É esse o estado em que a coleta decidiria
    # enriquecer — medir por `events_by_company` olharia o resultado depois da
    # correção e esconderia justamente os casos que queremos alcançar.
    eventos = sorted(set(rec.get("event_ids") or [])
                     or {e for evs in (rec.get("events_by_company") or {}).values()
                         for e in (evs or [])})
    severidades = {sev.get(e, "?") for e in eventos}
    g = ia.ganho_efetivo(titulo, resumo)
    texto = f"{titulo}. {resumo}"
    marcas = sorted({tag for pat, tag in _AMBIGUIDADE_DE_PAPEL
                     if re.search(pat, ia._norm(texto))})
    empresas = [c for c, v in (rec.get("events_by_company") or {}).items() if v]
    return {
        "eventos": eventos,
        "severidade_max": ("critico" if "critico" in severidades
                           else "alto" if "alto" in severidades else "outro"),
        "tokens_novos": g["tokens_novos"],
        "input_marginal": g["tokens_novos"] < MIN_TOKENS_NOVOS,
        "marcas_ambiguidade": marcas,
        "familia_sensivel": bool(set(eventos) & FAMILIAS_SENSIVEIS),
        "multiplas_empresas": len(empresas) > 1,
    }


# ── políticas candidatas (§7) — só a escolhida vira `should_enrich` ─────────
def policy_a(s: dict) -> bool:
    """Todo candidato crítico ou alto."""
    return s["severidade_max"] in ("critico", "alto")


def policy_b(s: dict) -> bool:
    """Crítico/alto cujo input é marginal."""
    return policy_a(s) and s["input_marginal"]


def policy_c(s: dict) -> bool:
    """Crítico/alto, input marginal E alguma marca de papel em disputa."""
    return policy_b(s) and bool(s["marcas_ambiguidade"])


def policy_d(s: dict) -> bool:
    """Só famílias empiricamente sensíveis a sujeito/papel, com input marginal."""
    return (s["familia_sensivel"] and s["input_marginal"]
            and s["severidade_max"] in ("critico", "alto"))


def policy_e(s: dict) -> bool:
    """Crítico/alto com marca de papel em disputa — sem exigir input marginal.

    A conjunção da POLICY-C (marginal E ambíguo) barrou um caso real de F2 por
    um único token acima do limiar, embora as marcas de ambiguidade estivessem
    lá. Quando o papel está em disputa, a quantidade de texto é irrelevante:
    é a natureza do texto que pede contexto.
    """
    return (s["severidade_max"] in ("critico", "alto")
            and bool(s["marcas_ambiguidade"]))


POLICIES = {"A": policy_a, "B": policy_b, "C": policy_c, "D": policy_d,
            "E": policy_e}

# Escolhida em R5b após medir cobertura, custo e recall (ver checkpoint).
POLICY_ESCOLHIDA = "E"


def should_enrich(rec: dict, cfg: dict) -> tuple[bool, dict]:
    """Elegibilidade de COLETA. Devolve (decisão, sinais que a justificam)."""
    s = sinais(rec, cfg)
    s["policy"] = POLICY_ESCOLHIDA
    s["policy_version"] = POLICY_VERSION
    return POLICIES[POLICY_ESCOLHIDA](s), s
