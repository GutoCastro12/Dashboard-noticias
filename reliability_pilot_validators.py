#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_validators.py — 4I.2 R7b-A.

O QUE TRANSFORMA ALUCINAÇÃO EM MÉTRICA.

Sem validação determinística, "o modelo às vezes inventa" é uma impressão. Com
ela, vira uma contagem com denominador. As três checagens são independentes do
provider e rodam sem rede:

H1  quote que não existe literalmente no input  → campo UNSUPPORTED
H3  organização que não aparece no texto        → registro descartado
H5  violação de schema/enum                     → retry, depois descarte

H2 (quote existe mas não sustenta o campo) e H6 (materialidade inflada) são
julgamento humano — ficam marcados para adjudicação, nunca auto-resolvidos.

O contrato do quote é LITERAL. Match semântico frouxo derrotaria o propósito:
o objetivo é justamente impedir que o modelo produza conclusão sem apoio no
texto, e um matcher tolerante aceitaria paráfrase — que é exatamente a forma
mais comum de alucinação plausível.
"""
from __future__ import annotations

import re

from reliability_pilot_contract import (SCHEMA_AUDIT, SCHEMA_DISCOVERY,
                                        SCHEMA_COMBINED, normalizar,
                                        normalizar_para_comparacao)

H1_QUOTE_INEXISTENTE = "H1_QUOTE_INEXISTENTE"
H3_ENTIDADE_AUSENTE = "H3_ENTIDADE_AUSENTE"
H4_NOVEL_SEM_QUOTE = "H4_NOVEL_SEM_QUOTE"
H5_SCHEMA_INVALIDO = "H5_SCHEMA_INVALIDO"

CAMPOS_QUOTE = ("event_quote", "subject_quote", "role_quote", "relation_quote",
                "currentness_quote", "phase_quote", "evidence_quote",
                "magnitude_quote")


# ── H5: schema e enums ──────────────────────────────────────────────────────
def _tipo_ok(valor, tipos) -> bool:
    m = {"string": str, "object": dict, "array": list,
         "number": (int, float), "boolean": bool}
    if isinstance(tipos, str):
        tipos = [tipos]
    for t in tipos:
        if t == "null" and valor is None:
            return True
        if t in m and isinstance(valor, m[t]):
            return True
    return False


def validar_schema(obj, schema: dict, caminho: str = "$") -> list:
    """Validador mínimo suficiente para os schemas deste piloto. Não uso
    jsonschema para não introduzir dependência nova numa wave experimental."""
    erros = []
    if "enum" in schema:
        if obj not in schema["enum"]:
            erros.append(f"{caminho}: {obj!r} fora do enum")
        return erros
    tipo = schema.get("type")
    if tipo and not _tipo_ok(obj, tipo):
        erros.append(f"{caminho}: tipo {type(obj).__name__} ≠ {tipo}")
        return erros
    if isinstance(obj, dict):
        for req in schema.get("required", []):
            if req not in obj:
                erros.append(f"{caminho}.{req}: ausente (obrigatório)")
        for k, v in obj.items():
            sub = (schema.get("properties") or {}).get(k)
            if sub:
                erros.extend(validar_schema(v, sub, f"{caminho}.{k}"))
    elif isinstance(obj, list):
        item = (schema.get("items") or {})
        for i, v in enumerate(obj):
            erros.extend(validar_schema(v, item, f"{caminho}[{i}]"))
    return erros


SCHEMAS = {"AUDIT": SCHEMA_AUDIT, "DISCOVERY": SCHEMA_DISCOVERY,
           "COMBINED": SCHEMA_COMBINED}


# ── H1: quote literal ───────────────────────────────────────────────────────
# O piso de 4 caracteres da v1 existia para barrar citação trivial — "de", "of",
# "a" —, que aparece em qualquer texto e não sustenta campo nenhum. O efeito
# colateral só apareceu com dado real: no case #2 prospectivo, ambos os modelos
# citaram `subject_quote = "JBS"`, que está LITERALMENTE na manchete, e os dois
# foram marcados H1_QUOTE_INEXISTENTE. Isso não é alucinação — é falso negativo
# do validador, e atinge toda empresa de nome curto (B3, BRF, JBS, WEG, ...).
#
# A v2 separa as duas coisas que o piso confundia: COMPRIMENTO e TRIVIALIDADE.
# Quote longa continua idêntica à v1 — substring na forma normalizada. Quote
# curta passa a ser aceita quando é um TOKEN COMPLETO e não é palavra funcional.
# Assim "JBS" em "...da JBS a partir..." vale, "de" em "modelo de negócio" não,
# e "b3" dentro de "b3sa3" não — porque não fecha token.
#
# Nenhum nome de empresa é citado aqui: a regra é sobre a natureza do token.
QUOTE_VALIDATOR_VERSION = "r7ba.q2"

# Palavras funcionais de 1–3 caracteres em pt/en/es. Só entram tokens que não
# sustentam campo algum sozinhos; nenhum alias real de emissor é palavra
# funcional, então esta lista não pode barrar entidade legítima.
# Só tokens de 1–3 caracteres: acima disso a regra nem consulta esta lista, e
# entrada mais longa seria peso morto que dá falsa impressão de cobertura.
_QUOTE_TRIVIAL = frozenset("""
a à ao aos as às o os um uns e é ou se de do da dos das em no na nos nas por
que com sem sua seu sob ate até já há ha nao não mas foi ser tem lhe me te
vos ela ele eu tu
an and the of to in is it on at as by or be he we do if no so up us are was
has its not but all can for her his had may new one two off out per
el la los las del un una y con su sus mas más ese esa
""".split())

# Casa TOKEN COMPLETO: nem `\b` (que falha quando a citação termina em símbolo,
# como "r$"), nem substring solta (que aceitaria "b3" dentro de "b3sa3").
def _token_completo(q: str, t: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(q) + r"(?!\w)", t) is not None


def quote_valida_v1(quote: str, texto: str) -> bool:
    """Comportamento ORIGINAL, preservado para reavaliar o que foi observado.

    Não é código morto: sem ele não dá para dizer "na época o validador
    respondeu X, sob a regra corrigida responderia Y" sem reescrever a
    telemetria histórica — que é justamente o que não se pode fazer.
    """
    if quote is None or quote == "":
        return True          # ausência declarada não é alucinação
    q = normalizar_para_comparacao(quote)
    if len(q) < 4:
        return False
    return q in normalizar_para_comparacao(texto)


def quote_valida(quote: str, texto: str) -> bool:
    if quote is None or quote == "":
        return True          # ausência declarada não é alucinação
    q = normalizar_para_comparacao(quote)
    if not q:
        return False
    t = normalizar_para_comparacao(texto)
    if len(q) >= 4:
        return q in t        # idêntico à v1 — nada muda para quote longa
    if q in _QUOTE_TRIVIAL:
        return False         # literal, mas não sustenta campo algum
    return _token_completo(q, t)


def validar_quotes(evento: dict, texto: str) -> dict:
    invalidas, checadas = [], []
    for c in CAMPOS_QUOTE:
        if c not in evento:
            continue
        v = evento.get(c)
        if v in (None, ""):
            continue
        checadas.append(c)
        if not quote_valida(v, texto):
            invalidas.append(c)
    return {"checadas": checadas, "invalidas": invalidas,
            "valida": not invalidas}


# ── H3: entidade presente no texto ──────────────────────────────────────────
_PALAVRA = re.compile(r"[0-9A-Za-zÁÂÃÀÉÊÍÓÔÕÚÜÇáâãàéêíóôõúüç&.\-]+")


def entidade_no_texto(nome: str, texto: str, aliases=None) -> bool:
    """Aceita a entidade se ela (ou um alias conhecido) aparece no texto.

    Casamento por TOKEN, não por substring: exigir a string inteira rejeitaria
    "Duke Energy Corp" quando o texto diz "Duke Energy", e aceitar substring
    solta faria "Vale" casar dentro de "Valeu". Exige-se que os tokens
    significativos do nome apareçam."""
    if not nome:
        return False
    alvo = normalizar_para_comparacao(texto)
    cands = [nome] + list(aliases or [])
    for c in cands:
        toks = [t for t in _PALAVRA.findall(normalizar_para_comparacao(c))
                if len(t) >= 3]
        if not toks:
            continue
        if all(re.search(r"\b" + re.escape(t) + r"\b", alvo) for t in toks):
            return True
    return False


# ── orquestração ────────────────────────────────────────────────────────────
def validar_audit(saida: dict, *, texto: str, organizacao: str,
                  aliases=None, event_ids=None) -> dict:
    erros = validar_schema(saida, SCHEMA_AUDIT)
    if erros:
        return {"ok": False, "falha": H5_SCHEMA_INVALIDO, "erros": erros,
                "eventos": []}
    permitidos = set(event_ids or [])
    out, marcas = [], []
    for ev in (saida.get("events") or []):
        m = []
        if permitidos and ev.get("event_id") not in permitidos:
            m.append("EVENT_ID_FORA_DOS_CANDIDATOS")
        q = validar_quotes(ev, texto)
        if not q["valida"]:
            m.append(H1_QUOTE_INEXISTENTE)
        rel = ev.get("related_entity")
        if rel and not entidade_no_texto(rel, texto):
            m.append(H3_ENTIDADE_AUSENTE)
        subj = ev.get("subject")
        if subj and not entidade_no_texto(subj, texto, aliases):
            m.append(H3_ENTIDADE_AUSENTE)
        out.append({**ev, "_validacao": {"quotes": q, "marcas": m,
                                         "aceito": not m}})
        marcas.extend(m)
    return {"ok": True, "falha": "", "erros": [], "eventos": out,
            "marcas": sorted(set(marcas)),
            "aceitos": sum(1 for e in out if e["_validacao"]["aceito"]),
            "total": len(out)}


def validar_discovery(saida: dict, *, texto: str) -> dict:
    erros = validar_schema(saida, SCHEMA_DISCOVERY)
    if erros:
        return {"ok": False, "falha": H5_SCHEMA_INVALIDO, "erros": erros,
                "eventos": []}
    out, marcas = [], []
    for ev in (saida.get("events") or []):
        m = []
        if not (ev.get("evidence_quote") or "").strip():
            m.append(H4_NOVEL_SEM_QUOTE)
        q = validar_quotes(ev, texto)
        if not q["valida"]:
            m.append(H1_QUOTE_INEXISTENTE)
        if not entidade_no_texto(ev.get("organization") or "", texto):
            m.append(H3_ENTIDADE_AUSENTE)
        out.append({**ev, "_validacao": {"quotes": q, "marcas": m,
                                         "aceito": not m}})
        marcas.extend(m)
    return {"ok": True, "falha": "", "erros": [], "eventos": out,
            "marcas": sorted(set(marcas)),
            "aceitos": sum(1 for e in out if e["_validacao"]["aceito"]),
            "total": len(out)}


def validar_combined(saida: dict, *, texto: str, organizacao: str,
                     aliases=None, event_ids=None) -> dict:
    erros = validar_schema(saida, SCHEMA_COMBINED)
    if erros:
        return {"ok": False, "falha": H5_SCHEMA_INVALIDO, "erros": erros,
                "audit": {"eventos": []}, "discovery": {"eventos": []}}
    a = validar_audit({"events": saida.get("events") or []}, texto=texto,
                      organizacao=organizacao, aliases=aliases,
                      event_ids=event_ids)
    d = validar_discovery({"events": saida.get("novel_events") or []},
                          texto=texto)
    return {"ok": True, "falha": "", "erros": [], "audit": a, "discovery": d}


def casar_entidade_local(nome: str, aliases_por_empresa: dict) -> str:
    """Entity match LOCAL e determinístico da discovery cega.

    A call de discovery não sabe quais empresas monitoramos; é AQUI, offline,
    que a organização citada vira (ou não) uma monitorada. Se essa etapa
    vivesse no prompt, a cegueira seria fingida."""
    if not nome:
        return ""
    alvo = normalizar_para_comparacao(nome)
    melhor, melhor_n = "", 0
    for emp, als in (aliases_por_empresa or {}).items():
        for c in [emp] + list(als or []):
            toks = [t for t in _PALAVRA.findall(normalizar_para_comparacao(c))
                    if len(t) >= 3]
            if not toks:
                continue
            if all(re.search(r"\b" + re.escape(t) + r"\b", alvo) for t in toks):
                if len(toks) > melhor_n:
                    melhor, melhor_n = emp, len(toks)
    return melhor
