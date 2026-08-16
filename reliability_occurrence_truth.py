#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_truth.py — a que ocorrência econômica o artigo pertence.

POR QUE ESTE MÓDULO EXISTE

A adjudicação humana de hoje guarda verdade de ARTIGO: pontuável ou não,
papel da empresa, fase, novidade. Não guarda a verdade de OCORRÊNCIA: que
aquele artigo de maio é a mesma troca de CEO anunciada em março.

Por isso as correções não se acumulam. A duplicata do Santander foi
adjudicada, o caso foi resolvido, e nada ficou: a próxima formulação de
acompanhamento chega e o sistema não sabe nada a mais do que sabia antes.
Este módulo é o lugar onde essa verdade passa a caber.

O QUE ELE NÃO FAZ

Não pontua. `build_evolution`, `assign_occurrence_clusters` e a classificação
de artigo não leem nada daqui, e há teste que falha se um dia lerem. É
supervisão e avaliação — nunca autoridade.

POR QUE O ID NÃO É CALCULADO A PARTIR DO CONTEÚDO

`_occ_key` está fora de questão: nesta mesma sessão, duas correções legítimas
mudaram a chave do MESMO fato econômico — os artigos do Cade e do fechamento
da Smart Fit foram de `ma#1` para `ma#0`, e o "Another Acquisition" da EQT foi
de `ma#0` para `ma#1`. Verdade humana ancorada ali estaria hoje apontando para
o lugar errado, e em silêncio.

Mas o mesmo argumento derruba qualquer identificador derivado de conteúdo. A
URL muda quando o reparo de link roda; o título muda quando a fonte corrige a
manchete; a data do evento é justamente uma das coisas que o humano adjudica.
Um identificador calculado herda a instabilidade de tudo que entra no cálculo.

Então o ID é OPACO e emitido uma vez. Ele não é impressão digital do fato — é
chave primária de uma decisão humana. `id_artigo` continua sendo o fingerprint
de artigo, e é reusado como referência; são papéis diferentes.

POR QUE `same_event_as` NÃO É CAMPO

Se dois artigos apontam para a mesma ocorrência, "mesmo evento" já está dito.
Guardar a relação de novo, num campo separado e mutável, cria a possibilidade
de os dois se contradizerem — e aí não há como saber qual está certo. A
pertinência é a verdade; a relação é leitura dela.

RELAÇÕES NEGATIVAS, ESSAS, PRECISAM SER GRAVADAS

"Mesma empresa e mesma família" não implica mesmo fato: a Hapvida teve duas
trocas de comando reais em quatro meses. Um conjunto de exemplos que só
contenha duplicatas ensina a fundir. O registro de relação existe para o
DISTINCT — o SAME já vem de graça da pertinência.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import uuid
from pathlib import Path

import reliability_pilot_contract_v2 as v2
import semantic_v2_shadow as sh

OCCURRENCE_TRUTH_SCHEMA_VERSION = 1
OCCURRENCE_TRUTH_NS = "occurrence_truth"

# §15 — fonte única do enum. Duplicar os literais aqui deixaria os dois lados
# livres para divergir numa futura edição do contrato.
OCCURRENCE_NOVELTY = v2.OCCURRENCE_NOVELTY

# §16 — o menor conjunto que os casos REAIS já adjudicados exigem, mais
# UNKNOWN. Fases especulativas ficam de fora: um enum que descreve o que ainda
# não aconteceu não é vocabulário, é adivinhação.
MATERIAL_PHASE = (
    "ANNOUNCEMENT",          # o fato é anunciado
    "APPOINTMENT",           # o sucessor é escolhido/nomeado
    "REGULATORY_APPROVAL",   # Cade, ANP, ANEEL, comissão europeia
    "CLOSING",               # a operação fecha
    "COMPLETION",            # a sucessão/processo se conclui
    "IMPLEMENTATION",        # etapa societária de execução do que já foi feito
    "NONE",                  # não é fase, é cobertura
    "UNKNOWN",
)
RELATION = ("DISTINCT_OCCURRENCE", "UNDETERMINED")
ANCHOR_REFRESH = (True, False, None)
TIPOS_DE_ADJUDICADOR = ("human",)

_RX_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RX_TRUTH_ID = re.compile(r"^[a-z0-9_]+:[a-z0-9\-]+:[0-9a-f]{12}$")


class VerdadeRecusada(Exception):
    """Escrita rejeitada. Nunca silenciosa, nunca parcial."""


# ── identidade ──────────────────────────────────────────────────────────────
def _slug(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "sem-empresa"


def novo_occurrence_truth_id(event_id: str, company: str) -> str:
    """Emitido UMA vez, imutável, opaco. O prefixo é só para leitura humana:
    quem garante unicidade é o sufixo, e quem garante estabilidade é o fato de
    ele nunca ser recalculado."""
    return f"{_slug(event_id).replace('-', '_')}:{_slug(company)}:{uuid.uuid4().hex[:12]}"


def article_ref(url: str, titulo: str = "") -> str:
    """§22 — reusa a identidade de artigo que o projeto já tem. Ela é derivada
    da URL, então reparo de link a move; por isso ela é REFERÊNCIA, e o que
    identifica a ocorrência é o `occurrence_truth_id`, não ela."""
    return sh.id_artigo(url, titulo)


# ── leitura ─────────────────────────────────────────────────────────────────
def _ns(dados: dict) -> dict:
    return dados.get(OCCURRENCE_TRUTH_NS) or {}


def ocorrencias(dados: dict) -> dict:
    return dict(_ns(dados).get("occurrences") or {})


def memberships(dados: dict) -> list:
    return list(_ns(dados).get("memberships") or [])


def relacoes(dados: dict) -> list:
    return list(_ns(dados).get("relations") or [])


def memberships_ativas(dados: dict) -> list:
    """Uma pertinência substituída continua no arquivo — o que ela deixa de
    ser é ATIVA. Apagar a anterior destruiria o rastro da correção.

    Inativa é a que TEM `superseded_by` preenchido. Ler ao contrário — juntar
    os valores de `superseded_by` e descartar quem tem esse id — desativa
    justamente a pertinência NOVA, e a correção humana some sem erro nenhum.
    """
    return [m for m in memberships(dados) if not m.get("superseded_by")]


def membros_de(dados: dict, occurrence_truth_id: str) -> list:
    return [m for m in memberships_ativas(dados)
            if m.get("occurrence_truth_id") == occurrence_truth_id]


def ocorrencia_do_artigo(dados: dict, ref: str, company: str, event_id: str) -> str:
    for m in memberships_ativas(dados):
        if (m.get("article_ref") == ref and m.get("company") == company
                and m.get("event_id") == event_id):
            return m.get("occurrence_truth_id", "")
    return ""


def should_create_occurrence(novelty: str) -> bool:
    """§18 — DERIVADO. Guardar isto como verdade própria abriria espaço para
    contradizer a novidade adjudicada, e não haveria como saber qual vale."""
    return novelty == "NEW_OCCURRENCE"


# ── validação ───────────────────────────────────────────────────────────────
def validar(dados: dict) -> list:
    """§38 — devolve os problemas encontrados; não corrige nada."""
    probs = []
    occ = ocorrencias(dados)
    for oid, o in sorted(occ.items()):
        if not _RX_TRUTH_ID.match(oid):
            probs.append(("INVALID_TRUTH_ID", oid))
        if o.get("occurrence_truth_id") != oid:
            probs.append(("TRUTH_ID_MISMATCH", oid))
        if not o.get("company") or not o.get("event_id"):
            probs.append(("INCOMPLETE_OCCURRENCE", oid))
        d = o.get("material_event_date")
        if d is not None and not _RX_DATA.match(str(d)):
            probs.append(("INVALID_DATE", oid))
    vistos = set()
    ativos = {}
    for m in memberships(dados):
        mid = m.get("membership_id", "")
        if mid in vistos:
            probs.append(("DUPLICATE_MEMBERSHIP_ID", mid))
        vistos.add(mid)
        oid = m.get("occurrence_truth_id", "")
        if oid not in occ:
            probs.append(("ORPHAN_MEMBERSHIP", mid))
            continue
        if m.get("company") != occ[oid].get("company"):
            probs.append(("COMPANY_MISMATCH", mid))
        if m.get("event_id") != occ[oid].get("event_id"):
            probs.append(("FAMILY_MISMATCH", mid))
        if m.get("occurrence_novelty") not in OCCURRENCE_NOVELTY:
            probs.append(("INVALID_NOVELTY", mid))
        if m.get("material_phase") not in MATERIAL_PHASE:
            probs.append(("INVALID_MATERIAL_PHASE", mid))
        if m.get("should_refresh_anchor") not in ANCHOR_REFRESH:
            probs.append(("INVALID_ANCHOR_REFRESH", mid))
    for m in memberships_ativas(dados):
        k = (m.get("article_ref"), m.get("company"), m.get("event_id"))
        if k in ativos and ativos[k] != m.get("occurrence_truth_id"):
            probs.append(("MULTIPLE_ACTIVE_MEMBERSHIPS", "|".join(str(x) for x in k)))
        ativos[k] = m.get("occurrence_truth_id")
    # ciclo de substituição
    prox = {m.get("membership_id"): m.get("superseded_by")
            for m in memberships(dados) if m.get("superseded_by")}
    for inicio in list(prox):
        lento = rapido = inicio
        while rapido and prox.get(rapido):
            lento = prox.get(lento)
            rapido = prox.get(prox.get(rapido)) if prox.get(rapido) else None
            if lento and lento == rapido:
                probs.append(("SUPERSESSION_CYCLE", inicio))
                break
    for r in relacoes(dados):
        a, b = r.get("occurrence_a"), r.get("occurrence_b")
        if a not in occ or b not in occ:
            probs.append(("INVALID_RELATION", f"{a}|{b}"))
        elif a == b:
            probs.append(("SELF_RELATION", str(a)))
        if r.get("relation") not in RELATION:
            probs.append(("INVALID_RELATION_TYPE", str(r.get("relation"))))
    return sorted(set(probs))


# ── escrita ─────────────────────────────────────────────────────────────────
def _bloco(dados: dict) -> dict:
    ns = dados.setdefault(OCCURRENCE_TRUTH_NS, {})
    ns.setdefault("schema_version", OCCURRENCE_TRUTH_SCHEMA_VERSION)
    ns.setdefault("occurrences", {})
    ns.setdefault("memberships", [])
    ns.setdefault("relations", [])
    return ns


def _exige(cond, msg):
    if not cond:
        raise VerdadeRecusada(msg)


def criar_ocorrencia(dados: dict, *, company: str, event_id: str,
                     material_event_date=None, family_identity=None,
                     adjudicated_by: str, adjudicated_at_iso: str,
                     nota: str = "", occurrence_truth_id: str = "") -> str:
    """Emite uma ocorrência econômica e devolve o `occurrence_truth_id`.

    `material_event_date` pode ser None: a data do fato nem sempre é conhecida
    no momento da adjudicação, e obrigar um valor faria inventarem um.
    `occurrence_truth_id` só é aceito de fora para reconstruir fixture — em uso
    normal o id é emitido aqui e nunca mais recalculado.
    """
    _exige(company and event_id, "OCORRENCIA_INCOMPLETA: company e event_id obrigatórios")
    _exige(adjudicated_by, "SEM_ADJUDICADOR")
    _exige(material_event_date is None or _RX_DATA.match(str(material_event_date)),
           f"DATA_INVALIDA: {material_event_date!r} (use AAAA-MM-DD ou None)")
    ns = _bloco(dados)
    oid = occurrence_truth_id or novo_occurrence_truth_id(event_id, company)
    _exige(oid not in ns["occurrences"], f"ID_DUPLICADO: {oid}")
    _exige(_RX_TRUTH_ID.match(oid), f"ID_INVALIDO: {oid}")
    ns["occurrences"][oid] = {
        "occurrence_truth_id": oid,
        "company": company,
        "event_id": event_id,
        "material_event_date": material_event_date,   # None é resposta legítima
        "family_identity": dict(family_identity or {}),
        "schema_version": OCCURRENCE_TRUTH_SCHEMA_VERSION,
        "adjudicated_by": adjudicated_by,
        "adjudicator_type": "human",
        "adjudicated_at_iso": adjudicated_at_iso,
        "nota": nota,
    }
    return oid


def adicionar_membership(dados: dict, *, occurrence_truth_id: str,
                         article_ref_: str, company: str, event_id: str,
                         occurrence_novelty: str, material_phase: str = "UNKNOWN",
                         should_refresh_anchor=None, evidence: str = "",
                         adjudicated_by: str, adjudicated_at_iso: str,
                         supersedes: str = "") -> str:
    ns = _bloco(dados)
    occ = ns["occurrences"]
    _exige(occurrence_truth_id in occ,
           f"OCORRENCIA_INEXISTENTE: {occurrence_truth_id}")
    _exige(occ[occurrence_truth_id]["company"] == company,
           f"EMPRESA_DIVERGENTE: {company} != {occ[occurrence_truth_id]['company']}")
    _exige(occ[occurrence_truth_id]["event_id"] == event_id,
           f"FAMILIA_DIVERGENTE: {event_id} != {occ[occurrence_truth_id]['event_id']}")
    _exige(occurrence_novelty in OCCURRENCE_NOVELTY,
           f"NOVIDADE_INVALIDA: {occurrence_novelty!r} não pertence a {OCCURRENCE_NOVELTY}")
    _exige(material_phase in MATERIAL_PHASE,
           f"FASE_INVALIDA: {material_phase!r} não pertence a {MATERIAL_PHASE}")
    _exige(should_refresh_anchor in ANCHOR_REFRESH,
           f"ANCORA_INVALIDA: {should_refresh_anchor!r} (use True, False ou None)")
    _exige(article_ref_ and adjudicated_by, "MEMBERSHIP_INCOMPLETA")
    atual = ocorrencia_do_artigo(dados, article_ref_, company, event_id)
    if atual and atual != occurrence_truth_id:
        _exige(supersedes,
               f"PERTINENCIA_ATIVA_CONFLITANTE: o artigo já pertence a {atual}; "
               f"para corrigir, informe `supersedes` da pertinência anterior")
    mid = uuid.uuid4().hex[:16]
    if supersedes:
        alvo = [m for m in ns["memberships"] if m.get("membership_id") == supersedes]
        _exige(alvo, f"SUPERSEDES_INEXISTENTE: {supersedes}")
        _exige(not alvo[0].get("superseded_by"),
               f"JA_SUBSTITUIDA: {supersedes}")
        alvo[0]["superseded_by"] = mid
    ns["memberships"].append({
        "membership_id": mid,
        "occurrence_truth_id": occurrence_truth_id,
        "article_ref": article_ref_,
        "company": company,
        "event_id": event_id,
        "occurrence_novelty": occurrence_novelty,
        "material_phase": material_phase,
        "should_refresh_anchor": should_refresh_anchor,
        "evidence": evidence,
        "schema_version": OCCURRENCE_TRUTH_SCHEMA_VERSION,
        "adjudicated_by": adjudicated_by,
        "adjudicator_type": "human",
        "adjudicated_at_iso": adjudicated_at_iso,
        "supersedes": supersedes or None,
        "superseded_by": None,
    })
    return mid


def adicionar_relacao(dados: dict, *, occurrence_a: str, occurrence_b: str,
                      relation: str, evidence: str = "",
                      adjudicated_by: str, adjudicated_at_iso: str) -> None:
    ns = _bloco(dados)
    _exige(relation in RELATION,
           f"RELACAO_INVALIDA: {relation!r} não pertence a {RELATION}")
    _exige(occurrence_a in ns["occurrences"] and occurrence_b in ns["occurrences"],
           "RELACAO_ORFA: as duas ocorrências precisam existir")
    _exige(occurrence_a != occurrence_b, "RELACAO_REFLEXIVA")
    a, b = sorted((occurrence_a, occurrence_b))
    for r in ns["relations"]:
        _exige(not (r["occurrence_a"] == a and r["occurrence_b"] == b),
               f"RELACAO_DUPLICADA: {a} × {b}")
    ns["relations"].append({
        "occurrence_a": a, "occurrence_b": b, "relation": relation,
        "evidence": evidence, "schema_version": OCCURRENCE_TRUTH_SCHEMA_VERSION,
        "adjudicated_by": adjudicated_by, "adjudicator_type": "human",
        "adjudicated_at_iso": adjudicated_at_iso,
    })


def relatorio(dados: dict) -> str:
    ns = _ns(dados)
    occ, mem, rel = ocorrencias(dados), memberships_ativas(dados), relacoes(dados)
    L = [f"## Verdade de ocorrência (schema {ns.get('schema_version', '-')})", ""]
    L.append(f"**{len(occ)} ocorrência(s)**, {len(mem)} pertinência(s) ativa(s), "
             f"{len(rel)} relação(ões).")
    for oid, o in sorted(occ.items()):
        L.append("")
        L.append(f"### {o['company']} · `{o['event_id']}`")
        L.append(f"- id: `{oid}`")
        L.append(f"- data do fato: {o.get('material_event_date') or '(desconhecida)'}")
        if o.get("family_identity"):
            L.append(f"- identidade: {o['family_identity']}")
        for m in sorted(membros_de(dados, oid), key=lambda x: x["article_ref"]):
            L.append(f"  - `{m['article_ref'][:12]}` {m['occurrence_novelty']}"
                     f" · fase {m['material_phase']}"
                     f" · âncora {m['should_refresh_anchor']}")
    for r in rel:
        L.append("")
        L.append(f"- relação {r['relation']}: `{r['occurrence_a'][:24]}` × "
                 f"`{r['occurrence_b'][:24]}`")
    probs = validar(dados)
    L.append("")
    L.append(f"**Validação:** {'sem problemas' if not probs else probs}")
    return "\n".join(L) + "\n"
