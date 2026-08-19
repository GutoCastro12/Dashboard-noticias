#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_taxonomy_inventory.py — 4I.2 R7a.

O QUE O SISTEMA SABE INTERPRETAR, POR EVENTO, COM EVIDÊNCIA.

Fraude chegou em R6f com uma cadeia de sujeito, papel, agência, liability,
fase, evidência e atribuição de terceiro. A maior parte da taxonomia ainda
decide por `keyword → candidato → ausência de blocker → pontua`. Este módulo
não corrige essa desigualdade: ele a MEDE, para que a correção seja dirigida
por evidência e não por impressão.

Três coisas são derivadas do código e do corpus, nunca deste arquivo:

1. a taxonomia real — lida de `config_risco.yaml`, não de uma lista fixa;
2. o escopo de cada regra — os mesmos conjuntos que `resolve_article_semantics`
   consulta (`EVENTOS_SUJEITO_ESTRITO`, `EVENTOS_MA`, ...), importados de
   `semantic_audit`, de modo que renomear um evento no config reaparece aqui;
3. o exercício de cada regra — contado em `risk_history.json`
   (`attribution_rule` real) e nos fixtures.

O único julgamento humano codificado é o MAPA DIMENSIONAL: qual pergunta do
contrato universal cada regra responde. Ele está declarado abaixo, uma linha
por regra, e `test_wave_r7a_taxonomy_contract.py` falha se uma regra existir
no runtime e não estiver aqui — o inventário não pode silenciosamente ficar
para trás do código.

NADA AQUI ALTERA SCORING. É leitura.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
from pathlib import Path

import semantic_audit as sa

INVENTARIO_VERSION = "r7a.1"

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
CONFIG = Path(os.environ.get("RELIABILITY_CONFIG", "config_risco.yaml"))
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_taxonomy_inventory"))

# ── as onze perguntas do contrato universal, na ordem do brief ──────────────
DIMENSOES = (
    "event_evidence",     # 1. o evento existe no texto?
    "subject",            # 2. quem é o sujeito?
    "role",               # 3. qual o papel da monitorada?
    "relation",           # 4. existe outra entidade central, e em que relação?
    "currentness",        # 5. o evento é atual?
    "phase",              # 6. qual a fase / nível de confirmação?
    "centrality",         # 7. é evento central ou contexto?
    "positive_evidence",  # 8. existe evidência positiva?
    "negative_evidence",  # 9. existe evidência de descarte?
    "entity_attribution", # 10./11. a qual entidade o trecho pertence
    # 12. R7b-S2: O QUE foi transacionado. Só se aplica à família M&A, e existe
    # porque papel e objeto são perguntas distintas: BTG e Petrobras são
    # adquirentes de verdade (role comprovado) e ainda assim não fazem M&A
    # societário (object é fazenda / bloco exploratório). Sem esta dimensão a
    # família `ma` aparentava completude sem nunca ter provado o objeto.
    "transaction_object",
)

STRONG, PARTIAL, INDIRECT, NONE = "STRONG", "PARTIAL", "INDIRECT", "NONE"

# ── escopos: os MESMOS objetos que o runtime consulta ───────────────────────
# Importados, não copiados. Se `EVENTOS_MA` mudar em semantic_audit.py, a
# matriz muda junto — foi exatamente por copiar listas que relatórios antigos
# passaram a descrever um sistema que não existia mais.
_ESTRITO = "EVENTOS_SUJEITO_ESTRITO"
_MA = "EVENTOS_MA"
_FRAUDE = "EVENTOS_FRAUDE"
_CREDITO = "EVENTOS_CREDITO_EXIGEM_FATO"
_INVEST = "EVENTOS_INVESTIGACAO_E_O_PROPRIO_EVENTO"
_RATING = "RATING_FAMILY"
_GLOBAL = "__global__"

_CONJUNTOS = {
    _ESTRITO: lambda: set(sa.EVENTOS_SUJEITO_ESTRITO),
    _MA: lambda: set(sa.EVENTOS_MA),
    _FRAUDE: lambda: set(sa.EVENTOS_FRAUDE),
    _CREDITO: lambda: set(sa.EVENTOS_CREDITO_EXIGEM_FATO),
    _INVEST: lambda: set(sa.EVENTOS_INVESTIGACAO_E_O_PROPRIO_EVENTO),
    _RATING: lambda: set(sa.RATING_FAMILY),
}


def _escopo(spec) -> set | str:
    """`spec` é _GLOBAL, um nome de conjunto do runtime, ou uma tupla literal
    de event ids (usada quando o próprio código escreve `ev in (...)`)."""
    if spec == _GLOBAL:
        return _GLOBAL
    if isinstance(spec, str):
        return _CONJUNTOS[spec]()
    out = set()
    for s in spec:
        out |= _escopo(s) if not isinstance(s, str) or s in _CONJUNTOS else {s}
    return out


# ── MAPA DIMENSIONAL ────────────────────────────────────────────────────────
# regra -> (dimensões respondidas, escopo, shadow_only)
# Ordem espelha `resolve_article_semantics`, para conferência lado a lado.
REGRAS = {
    "R_RATING_FAMILIA": (("centrality",), _RATING, False),
    "R_HISTORICO": (("currentness",), _GLOBAL, False),
    "R_NEGACAO_EXPLICITA": (("event_evidence", "negative_evidence"), _GLOBAL, False),
    "R_RESOLUCAO_EVENTO_ANTERIOR": (("currentness", "phase", "negative_evidence"),
                                    _GLOBAL, False),
    "R_DEFAULT_NOMENCLATURA_RATING": (("event_evidence", "negative_evidence"),
                                      ("default", "default_cri"), False),
    "R_CREDITO_EXIGE_FATO_CONSUMADO": (("phase",), _CREDITO, False),
    "R_INVESTIGACAO_SEM_ATO_FORMAL": (("phase",), _INVEST, False),
    "R_POSSESSIVO_MESMA_ORACAO": (("subject", "entity_attribution"),
                                  (_ESTRITO, _FRAUDE), False),
    "R_CAUSACAO_TERCEIRO": (("subject", "role", "relation"), _ESTRITO, False),
    "R_COMPRADOR_NAO_SOFRE_RJ": (("role", "relation"), _ESTRITO, False),
    "R_COMUNICADO_SOBRE_TERCEIRO": (("subject", "centrality"), _ESTRITO, False),
    "R_MA_PAPEL_VENDEDOR": (("role", "relation"), (_MA, "follow_on"), False),
    "R_MA_OBJETO_ESCOPO": (("event_evidence", "centrality", "transaction_object"),
                           _MA, False),
    "R_MA_LEGITIMO": (("positive_evidence",), _MA, False),
    "R_LIABILITY_DE_TERCEIRO": (("subject", "entity_attribution"), _FRAUDE, True),
    "R_LIABILITY_VENCE_RESOLUCAO": (("phase", "positive_evidence"), _FRAUDE, False),
    "R_FASE_JURIDICA_MITIGADORA": (("phase",), _FRAUDE, False),
    "R_FRAUDE_NAO_CONFIRMADA": (("phase",), _FRAUDE, False),
    "R_EVENTO_CITADO_COMO_PASSADO": (("currentness",), _GLOBAL, False),
    "R_FOLLOW_ON_DE_TERCEIRO": (("subject",), ("follow_on",), False),
    "R_TROCA_CEO_DE_TERCEIRO": (("subject",), ("troca_ceo",), False),
    # 4I.2 R7k: irmã da anterior, num eixo ANTERIOR a ela. A regra de terceiro
    # responde "de quem é a troca"; esta responde "houve troca?". Dimensão
    # `event_evidence` porque a keyword `novo CEO` é adjetivo de status e não
    # prova evento — supervisão humana, lote V1, invariante H4.
    "R_TROCA_CEO_SEM_ASSERCAO": (("event_evidence", "centrality"),
                                 ("troca_ceo",), False),
    "R_EVENTO_DE_SUBSIDIARIA_NOMEADA": (("subject", "relation"), _GLOBAL, False),
    "R_EVENTO_NAO_CONSUMADO_OU_DE_CARTEIRA": (("phase", "event_evidence"),
                                              (_ESTRITO, _CREDITO), False),
    "R_SOBERANO_NAO_E_EMISSOR_CORPORATIVO": (("subject",), (_ESTRITO, _CREDITO), False),
    "R_INSOLVENCIA_SETORIAL_OU_DE_TERCEIRO": (("subject", "relation",
                                               "entity_attribution"),
                                              _ESTRITO, False),
    "R_MONITORADA_E_CREDORA_LESADA": (("role", "relation"), (_ESTRITO, _CREDITO), False),
    "R_CREDOR_NAO_HERDA_EVENTO_DO_DEVEDOR": (("subject", "role", "relation"),
                                             (_ESTRITO, _CREDITO), False),
    # Irmã da anterior, para quando o devedor NÃO é nomeado: o polo ativo é
    # inequívoco pelo verbo ("X requer a falência"), mesmo sem saber quem é o
    # requerido. Adjudicada em Santander/Minera Cobre Verde (2026-08-12).
    "R_REQUERENTE_DE_FALENCIA_NAO_E_O_FALIDO": (("subject", "role", "relation"),
                                                (_ESTRITO, _CREDITO), False),
    "R_MONITORADA_E_FINANCIADORA": (("role", "relation"), ("emissao_divida",), False),
    "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE": (("role", "relation"), _FRAUDE, False),
    # Irmã da anterior para a construção "<Empresa> fraud case": a empresa dá
    # NOME ao processo, mas quem responde é terceiro. Exige duas evidências —
    # o caso nomeado e um ator terceiro responsabilizado (ou comentário sobre
    # o caso) —, porque o nome sozinho também aparece quando a empresa é ré.
    # Adjudicada em Duke Energy (2026-08-14).
    "R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA": (("subject", "role", "relation"),
                                          _FRAUDE, False),
    "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA": (("event_evidence", "subject"),
                                              ("falencia", "recuperacao_judicial"), False),
    "R_AFILIACAO_INDIVIDUAL": (("subject", "role"),
                               ("falencia", "default", "investigacao_regulatoria"), False),
    "R_PAPEL_NAO_SUJEITO": (("role",), _GLOBAL, False),
    "R_EVENTO_DE_OUTRO_ITEM_DO_DIGEST": (("subject", "centrality",
                                          "entity_attribution"), _GLOBAL, False),
    "R_FRAUDE_VITIMA_DETECTORA": (("role", "relation", "positive_evidence"),
                                  _FRAUDE, True),
    "R_FRAUDE_ATOR_EXTERNO": (("role", "relation", "positive_evidence"), _FRAUDE, True),
    "R_FRAUDE_PREJUIZO_DE_TERCEIRO": (("role", "relation", "positive_evidence"),
                                      _FRAUDE, True),
}

# Regras GLOBAIS que não são específicas de família: contam como INDIRECT para
# a família, nunca como STRONG. `R_PAPEL_NAO_SUJEITO` é o último recurso do
# laço e `R_HISTORICO` roda antes de qualquer decisão por evento — nenhuma das
# duas prova que a família tem tratamento próprio da dimensão.
GENERICAS = {"R_HISTORICO", "R_NEGACAO_EXPLICITA", "R_RESOLUCAO_EVENTO_ANTERIOR",
             "R_EVENTO_CITADO_COMO_PASSADO", "R_PAPEL_NAO_SUJEITO",
             "R_EVENTO_DE_SUBSIDIARIA_NOMEADA", "R_EVENTO_DE_OUTRO_ITEM_DO_DIGEST"}


# ── 1. taxonomia real, derivada do config ───────────────────────────────────
def taxonomia(cfg: dict) -> list[dict]:
    out = []
    for e in (cfg.get("taxonomy") or []):
        if not isinstance(e, dict) or not e.get("id"):
            continue
        out.append({
            "event_id": e["id"],
            "label": e.get("label") or "",
            "severity": e.get("severity") or "",
            "direction": e.get("direction") or "",
            "score": e.get("score"),
            "dimensions": list(e.get("dimensions") or []),
            "applies_to": list(e.get("applies_to") or []),
            "keywords": list(e.get("keywords") or []),
        })
    return out


# ── 2. censo do corpus ──────────────────────────────────────────────────────
def censo(hist: dict) -> dict:
    """Candidatos, pontuáveis, informativos e contexto por evento, mais as
    regras realmente exercitadas. Candidato = evento reconhecido para ALGUMA
    empresa do registro, contado por par empresa × evento — a mesma unidade
    que o contrato universal usa."""
    cand = collections.Counter()
    score = collections.Counter()
    info = collections.Counter()
    ctx = collections.Counter()
    regras = collections.Counter()
    regra_por_evento = collections.defaultdict(collections.Counter)
    empresas = collections.defaultdict(set)

    # Os três campos NÃO têm o mesmo formato: `events_by_company` guarda o
    # event id cru, enquanto informativo e contexto guardam o registro inteiro
    # (com `subject_company`, `relation_type`, etc.). Ler os três como lista de
    # strings estourava em `unhashable type: dict`.
    def _ids(v):
        for x in v or []:
            yield x.get("event_id") if isinstance(x, dict) else x

    for rec in (hist.get("articles") or {}).values():
        for emp, evs in (rec.get("events_by_company") or {}).items():
            for ev in _ids(evs):
                if ev:
                    score[ev] += 1
                    empresas[ev].add(emp)
        for emp, evs in (rec.get("informational_events_by_company") or {}).items():
            for ev in _ids(evs):
                if ev:
                    info[ev] += 1
        for emp, evs in (rec.get("context_events_by_company") or {}).items():
            for ev in _ids(evs):
                if ev:
                    ctx[ev] += 1
        vistos = set()
        for d in (rec.get("event_assessments") or []):
            ev, emp = d.get("event_id"), d.get("monitored_company")
            if not ev:
                continue
            if (emp, ev) not in vistos:
                cand[ev] += 1
                vistos.add((emp, ev))
            r = d.get("attribution_rule") or ""
            if r:
                regras[r] += 1
                regra_por_evento[ev][r] += 1
        for d in (rec.get("semantic_discards") or []):
            r = d.get("regra") or ""
            ev = d.get("event_id") or ""
            if r:
                regras[r] += 1
                if ev:
                    regra_por_evento[ev][r] += 1

    return {"candidatos": cand, "pontuaveis": score, "informativos": info,
            "contexto": ctx, "regras_exercitadas": regras,
            "regra_por_evento": {k: dict(v) for k, v in regra_por_evento.items()},
            "empresas": {k: len(v) for k, v in empresas.items()}}


# ── 3. matriz de cobertura ──────────────────────────────────────────────────
def regras_do_evento(ev: str) -> list[str]:
    out = []
    for nome, (_dims, spec, _sh) in REGRAS.items():
        esc = _escopo(spec)
        if esc == _GLOBAL or ev in esc:
            out.append(nome)
    return out


def matriz(eventos: list[dict], cens: dict) -> dict:
    """STRONG exige regra ESPECÍFICA da família E exercício real no corpus.
    PARTIAL = regra específica que nunca disparou. INDIRECT = só regra
    genérica alcança a dimensão. NONE = nada responde a pergunta.

    A distinção existe porque `ter regex ≠ ter cobertura`: uma regra de
    `historical reference` global não prova que currentness esteja resolvido
    para uma família em particular — ela prova que existe um mecanismo capaz
    de, em princípio, ser acionado."""
    exerc = cens["regras_exercitadas"]
    por_ev = cens["regra_por_evento"]
    out = {}
    for e in eventos:
        ev = e["event_id"]
        aplicaveis = regras_do_evento(ev)
        linha = {}
        for dim in DIMENSOES:
            especificas = [r for r in aplicaveis
                           if dim in REGRAS[r][0] and r not in GENERICAS]
            genericas = [r for r in aplicaveis
                         if dim in REGRAS[r][0] and r in GENERICAS]
            ex_esp = [r for r in especificas
                      if por_ev.get(ev, {}).get(r) or exerc.get(r)]
            ex_ev = [r for r in especificas if por_ev.get(ev, {}).get(r)]
            if ex_ev:
                nivel, prova = STRONG, ex_ev
            elif ex_esp:
                nivel, prova = PARTIAL, ex_esp
            elif especificas:
                nivel, prova = PARTIAL, especificas
            elif genericas:
                nivel, prova = INDIRECT, genericas
            else:
                nivel, prova = NONE, []
            linha[dim] = {"nivel": nivel, "regras": sorted(prova),
                          "shadow_only": bool(prova) and all(REGRAS[r][2] for r in prova)}
        out[ev] = linha
    return out


def maturidade(linha: dict) -> str:
    """Rótulo por evento, derivado da própria linha — sem impressão."""
    n = collections.Counter(v["nivel"] for v in linha.values())
    if n[NONE] >= len(DIMENSOES) - 2:
        return "KEYWORD_ONLY"
    if n[STRONG] >= 5:
        return "CONTRACT_RICH"
    if n[STRONG] >= 2:
        return "CONTRACT_PARTIAL"
    if n[STRONG] or n[PARTIAL]:
        return "CONTRACT_THIN"
    return "KEYWORD_ONLY"


# ── relatório ───────────────────────────────────────────────────────────────
_SIG = {STRONG: "██", PARTIAL: "▓▓", INDIRECT: "░░", NONE: "  "}


def imprimir(eventos, cens, mat, top=None):
    print("=" * 118)
    print(f"INVENTÁRIO SEMÂNTICO DA TAXONOMIA — {INVENTARIO_VERSION}")
    print("=" * 118)
    ativos = [e for e in eventos if cens["candidatos"].get(e["event_id"])]
    print(f"  eventos na taxonomia          : {len(eventos)}")
    print(f"  eventos com candidato no corpus: {len(ativos)}")
    print(f"  eventos sem nenhum candidato   : {len(eventos) - len(ativos)}")
    print(f"  regras mapeadas                : {len(REGRAS)}")
    print(f"  regras exercitadas no corpus   : "
          f"{sum(1 for r in REGRAS if cens['regras_exercitadas'].get(r))}")
    print()
    cab = "  ".join(d[:4] for d in DIMENSOES)
    print(f"  {'evento':30s} {'sev':9s} {'cand':>5s} {'pont':>5s} {'info':>5s} "
          f"{'ctx':>4s}  {cab}   maturidade")
    print("  " + "-" * 114)
    ordem = sorted(eventos, key=lambda e: (-cens["candidatos"].get(e["event_id"], 0),
                                           e["event_id"]))
    if top:
        ordem = ordem[:top]
    for e in ordem:
        ev = e["event_id"]
        cel = "    ".join(_SIG[mat[ev][d]["nivel"]] for d in DIMENSOES)
        print(f"  {ev[:30]:30s} {e['severity'][:9]:9s} "
              f"{cens['candidatos'].get(ev, 0):5d} {cens['pontuaveis'].get(ev, 0):5d} "
              f"{cens['informativos'].get(ev, 0):5d} {cens['contexto'].get(ev, 0):4d}  "
              f"{cel}   {maturidade(mat[ev])}")
    print()
    print("  legenda: ██ STRONG (regra própria da família, exercitada no corpus)")
    print("           ▓▓ PARTIAL (regra própria, sem exercício registrado)")
    print("           ░░ INDIRECT (só regra genérica alcança a dimensão)")
    print("              NONE (nada responde a pergunta)")
    print()
    dist = collections.Counter(maturidade(mat[e["event_id"]]) for e in ativos)
    print("  maturidade dos eventos COM candidato no corpus:")
    for k in ("CONTRACT_RICH", "CONTRACT_PARTIAL", "CONTRACT_THIN", "KEYWORD_ONLY"):
        print(f"    {k:18s} {dist.get(k, 0):3d}")
    print()
    print("  dimensão mais frágil entre eventos ativos:")
    for d in DIMENSOES:
        c = collections.Counter(mat[e["event_id"]][d]["nivel"] for e in ativos)
        print(f"    {d:20s} STRONG {c[STRONG]:3d} · PARTIAL {c[PARTIAL]:3d} · "
              f"INDIRECT {c[INDIRECT]:3d} · NONE {c[NONE]:3d}")
    print("=" * 118)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import risk_dashboard as rd
    cfg = rd.load_config(str(CONFIG))
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    ev = taxonomia(cfg)
    cens = censo(hist)
    mat = matriz(ev, cens)
    imprimir(ev, cens, mat, top=a.top)
    if a.json:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        p = OUTDIR / "taxonomy_semantic_matrix.json"
        io.open(p, "w", encoding="utf-8").write(json.dumps(
            {"version": INVENTARIO_VERSION, "dimensoes": list(DIMENSOES),
             "eventos": ev, "censo": {k: dict(v) if isinstance(v, collections.Counter)
                                      else v for k, v in cens.items()},
             "matriz": mat,
             "maturidade": {e["event_id"]: maturidade(mat[e["event_id"]]) for e in ev}},
            ensure_ascii=False, indent=2, sort_keys=True))
        print(f"  json → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
