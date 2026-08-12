#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_universal_assessment.py — 4I.2 R7a.

UM FORMATO ÚNICO DE RESPOSTA PARA AS ONZE PERGUNTAS, EM TODA A TAXONOMIA.

`resolve_article_semantics` já devolve muita coisa por `company × event`, mas
devolve tudo no mesmo plano e com o MESMO grau de crença: `subject_company`
começa valendo a monitorada e só muda se alguma regra a mover. Quem lê o
registro não distingue

    "o sujeito é a monitorada PORQUE o texto diz"

de

    "o sujeito é a monitorada PORQUE ninguém disse o contrário".

Foi exatamente essa indistinção que deixou o L8 passar: o evento existia, a
condenação existia, e o sujeito ficou sendo a única monitorada citada.

`UniversalEventAssessment` separa essas duas coisas em toda dimensão. Cada uma
carrega um `status`:

    ESTABLISHED  — há regra/trecho que fixa o valor
    DEFAULTED    — o valor é o default do runtime, ninguém o comprovou
    CONTRADICTED — há evidência de que o valor corrente está errado
    UNKNOWN      — a dimensão não é sequer consultada para esta família

`semantic_completeness.missing_dimensions` lista o que está DEFAULTED ou
UNKNOWN. É a métrica que R7a existe para produzir.

SHADOW PURO. Não escreve em history, não altera scoring, não é chamado pelo
workflow. Roda offline sobre o corpus — zero rede, zero LLM.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import semantic_audit as sa

UEA_VERSION = "r7a.1"

ESTABLISHED = "ESTABLISHED"
DEFAULTED = "DEFAULTED"
CONTRADICTED = "CONTRADICTED"
UNKNOWN = "UNKNOWN"

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
CONFIG = Path(os.environ.get("RELIABILITY_CONFIG", "config_risco.yaml"))
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_taxonomy_inventory"))

# Níveis que de fato dizem algo sobre confirmação. "indefinido" é ausência de
# conclusão, não conclusão — tratá-lo como valor inflava a cobertura de fase.
CONFIRMACAO_REAL = frozenset({"confirmado", "nao_confirmado", "nao_confirmada",
                              "desfecho_confirmado"})


@dataclass
class Dim:
    """Uma dimensão do contrato. `value` é o que o runtime concluiu; `status`
    diz se isso foi comprovado ou herdado do default."""
    value: object = ""
    status: str = UNKNOWN
    rule: str = ""
    evidence: str = ""

    def est(self, value, rule="", evidence=""):
        self.value, self.status = value, ESTABLISHED
        self.rule, self.evidence = rule or self.rule, evidence or self.evidence
        return self

    def dft(self, value, rule="", evidence=""):
        self.value, self.status = value, DEFAULTED
        self.rule, self.evidence = rule or self.rule, evidence or self.evidence
        return self


@dataclass
class UniversalEventAssessment:
    article_identity: str = ""
    company: str = ""
    event: str = ""

    candidate: Dim = field(default_factory=Dim)
    event_occurrence: Dim = field(default_factory=Dim)
    subject: Dim = field(default_factory=Dim)
    company_role: Dim = field(default_factory=Dim)
    relation: Dim = field(default_factory=Dim)
    currentness: Dim = field(default_factory=Dim)
    phase: Dim = field(default_factory=Dim)
    centrality: Dim = field(default_factory=Dim)
    positive_evidence: Dim = field(default_factory=Dim)
    negative_evidence: Dim = field(default_factory=Dim)
    entity_attribution: Dim = field(default_factory=Dim)

    scoreable: bool = False
    decision_rule: str = ""
    rules_exercised: list = field(default_factory=list)

    DIMS = ("candidate", "event_occurrence", "subject", "company_role", "relation",
            "currentness", "phase", "centrality", "positive_evidence",
            "negative_evidence", "entity_attribution")

    def dims(self) -> dict:
        return {d: getattr(self, d) for d in self.DIMS}

    def missing_dimensions(self) -> list:
        return [d for d, v in self.dims().items()
                if v.status in (DEFAULTED, UNKNOWN)]

    def established(self) -> list:
        return [d for d, v in self.dims().items() if v.status == ESTABLISHED]

    def completeness(self) -> float:
        return len(self.established()) / len(self.DIMS)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["semantic_completeness"] = {
            "missing_dimensions": self.missing_dimensions(),
            "established": self.established(),
            "ratio": round(self.completeness(), 4),
        }
        d["current_decision"] = {"scoreable": self.scoreable, "rule": self.decision_rule}
        return d


# ── regras que, quando disparam, COMPROVAM cada dimensão ────────────────────
# Reutiliza o mapa dimensional do inventário: uma regra só pode estabelecer o
# que ela de fato responde. Manter os dois arquivos em sincronia é verificado
# por teste, não por disciplina.
def _mapa_dimensional() -> dict:
    import reliability_taxonomy_inventory as inv
    traducao = {"event_evidence": "event_occurrence", "role": "company_role",
                "subject": "subject", "relation": "relation",
                "currentness": "currentness", "phase": "phase",
                "centrality": "centrality", "positive_evidence": "positive_evidence",
                "negative_evidence": "negative_evidence",
                "entity_attribution": "entity_attribution"}
    return {r: tuple(traducao[d] for d in dims)
            for r, (dims, _e, _s) in inv.REGRAS.items()}


MAPA = None


def _mapa():
    global MAPA
    if MAPA is None:
        MAPA = _mapa_dimensional()
    return MAPA


def _trecho(d: dict) -> str:
    for k in ("rejection_reason", "temporal_evidence", "evidence"):
        if d.get(k):
            return str(d[k])[:220]
    return ""


def montar(d: dict, *, identity: str, texto: str = "") -> UniversalEventAssessment:
    """Traduz UMA decisão de `resolve_article_semantics` para o contrato.

    A tradução é conservadora por desenho: só marca ESTABLISHED quando existe
    regra nomeada respondendo aquela dimensão, ou quando o próprio runtime
    preencheu um campo que não é default (papel de transação, entidade
    terceira, fase jurídica reconhecida). Tudo o mais fica DEFAULTED — e é
    isso que se quer enxergar."""
    m = _mapa()
    u = UniversalEventAssessment(
        article_identity=identity,
        company=d.get("monitored_company") or "",
        event=d.get("event_id") or "",
    )
    regra = d.get("attribution_rule") or ""
    u.scoreable = bool(d.get("scoreable"))
    u.decision_rule = regra
    u.rules_exercised = [regra] if regra else []
    dimensoes_da_regra = set(m.get(regra, ()))

    # candidato: o evento foi reconhecido — isso é sempre um fato observado.
    u.candidate.est(True, rule="classify_article",
                    evidence=(texto or "")[:160])

    # ocorrência: só está comprovada se alguma regra tratou de event_evidence.
    if "event_occurrence" in dimensoes_da_regra:
        u.event_occurrence.est(bool(d.get("scoreable")), regra, _trecho(d))
    else:
        u.event_occurrence.dft(True, evidence="keyword presente; ocorrência não verificada")

    # sujeito: o campo NASCE valendo a monitorada. Só é ESTABLISHED se uma
    # regra de sujeito atuou; caso contrário é herança, não conclusão.
    subj = d.get("subject_company") or ""
    if "subject" in dimensoes_da_regra:
        u.subject.est(subj, regra, _trecho(d))
    elif subj and subj != d.get("monitored_company"):
        u.subject.est(subj, regra or "runtime", _trecho(d))
    else:
        u.subject.dft(subj, evidence="sujeito = monitorada por default do runtime")

    # papel da monitorada
    papel = d.get("transaction_role") or ""
    if "company_role" in dimensoes_da_regra:
        u.company_role.est(papel or "nao_sujeito", regra, _trecho(d))
    elif papel:
        u.company_role.est(papel, regra or "detect_transaction", "")
    else:
        u.company_role.dft("sujeito_presumido",
                           evidence="nenhuma regra de papel atuou")

    # relação com outra entidade
    rel = d.get("relation_type") or ""
    outra = (d.get("target_company") or d.get("seller_company")
             or d.get("buyer_company") or d.get("affected_company")
             or d.get("actor_company") or "")
    if rel or ("relation" in dimensoes_da_regra and outra):
        u.relation.est({"type": rel, "related_entity": outra}, regra, _trecho(d))
    elif outra:
        u.relation.est({"type": "", "related_entity": outra}, "detect_roles", "")
    else:
        u.relation.dft({"type": "", "related_entity": ""},
                       evidence="nenhuma outra entidade central identificada")

    # atualidade
    hist = bool(d.get("historical_reference"))
    if "currentness" in dimensoes_da_regra or d.get("temporal_evidence"):
        u.currentness.est("historico" if hist else "atual", regra,
                          d.get("temporal_evidence") or _trecho(d))
    else:
        u.currentness.dft("atual", evidence="ausência de marcador histórico "
                                            "não é prova de atualidade")

    # fase / confirmação
    # `confirmation_level` NASCE valendo "indefinido" — sentinela de "não sei",
    # presente em 82% dos pares do corpus. Contá-la como valor fazia a
    # dimensão marcar 100% ESTABLISHED, que é precisamente a falsa cobertura
    # que esta wave existe para expor. Só nível REAL de confirmação conta.
    fase = d.get("event_phase") or ""
    conf = d.get("confirmation_level") or ""
    if fase or conf in CONFIRMACAO_REAL or "phase" in dimensoes_da_regra:
        u.phase.est({"phase": fase, "confirmation": conf}, regra, _trecho(d))
    else:
        u.phase.dft({"phase": fase, "confirmation": conf},
                    evidence="fase não reconhecida; confirmação indefinida")

    # centralidade
    escopo = d.get("event_scope") or ""
    if "centrality" in dimensoes_da_regra or escopo in ("absorvido", "indireto"):
        u.centrality.est(escopo or "direto", regra, _trecho(d))
    else:
        u.centrality.dft(escopo or "direto",
                         evidence="centralidade não avaliada para esta família")

    # evidência positiva
    if "positive_evidence" in dimensoes_da_regra:
        u.positive_evidence.est(True, regra, _trecho(d))
    else:
        u.positive_evidence.dft(False,
                                evidence="pontuação sem evidência positiva exigida")

    # evidência negativa / descarte
    if not d.get("scoreable") and regra:
        u.negative_evidence.est({"reasons": [regra]}, regra, _trecho(d))
    elif "negative_evidence" in dimensoes_da_regra:
        u.negative_evidence.est({"reasons": [regra]}, regra, _trecho(d))
    else:
        u.negative_evidence.dft({"reasons": []},
                                evidence="nenhum descarte aplicável")

    # atribuição de entidade
    if "entity_attribution" in dimensoes_da_regra:
        u.entity_attribution.est(subj or outra, regra, _trecho(d))
    else:
        u.entity_attribution.dft("", evidence="trecho não atribuído a entidade")

    return u


# ── varredura offline do corpus ─────────────────────────────────────────────
def avaliar_corpus(hist: dict, cfg: dict, *, limite: int | None = None,
                   shadow: bool = False) -> list:
    """Recomputa a semântica de TODO o corpus e devolve os assessments.

    Necessário porque `event_assessments` só está persistido em 111 dos 751
    registros: medir cobertura só sobre eles compararia candidatos de 15% do
    corpus com pontuações de 100%. Aqui o denominador é o mesmo para tudo."""
    kws = sa._keywords_por_evento(cfg)
    al = sa._aliases_map(cfg)
    out = []
    itens = list((hist.get("articles") or {}).items())
    if limite:
        itens = itens[:limite]

    def _um():
        for url, rec in itens:
            evs = list(rec.get("event_ids") or [])
            emps = list(rec.get("companies") or [])
            if not evs or not emps:
                continue
            titulo = rec.get("title") or ""
            resumo = rec.get("summary") or ""
            ano = sa._ano_do_registro(rec)
            for emp in emps:
                try:
                    r = sa.resolve_article_semantics(
                        titulo, resumo, emp, evs, al, article_year=ano,
                        source_domain=rec.get("domain") or "",
                        keywords_por_evento=kws,
                        country=sa._country_de(cfg, emp))
                except Exception:
                    continue
                for d in r.get("decisoes") or []:
                    out.append(montar(d, identity=url, texto=titulo))

    if shadow:
        with sa.shadow_fraud_roles():
            _um()
    else:
        _um()
    return out


def resumo(ueas: list) -> dict:
    por_ev = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    cand = collections.Counter()
    pont = collections.Counter()
    comp = collections.defaultdict(list)
    for u in ueas:
        cand[u.event] += 1
        if u.scoreable:
            pont[u.event] += 1
        comp[u.event].append(u.completeness())
        for d, v in u.dims().items():
            por_ev[u.event][d][v.status] += 1
    return {"candidatos": cand, "pontuaveis": pont,
            "status": {e: {d: dict(c) for d, c in dd.items()} for e, dd in por_ev.items()},
            "completude": {e: sum(v) / len(v) for e, v in comp.items()}}


def imprimir(res: dict, top: int = 20):
    print("=" * 112)
    print(f"UNIVERSAL EVENT ASSESSMENT — varredura offline do corpus — {UEA_VERSION}")
    print("=" * 112)
    tot = sum(res["candidatos"].values())
    print(f"  pares empresa × evento avaliados : {tot}")
    print(f"  eventos distintos                 : {len(res['candidatos'])}")
    print()
    print(f"  {'evento':28s} {'cand':>5s} {'pont':>5s} {'compl':>6s}   "
          f"dimensões DEFAULTED/UNKNOWN mais frequentes")
    print("  " + "-" * 108)
    for ev, n in res["candidatos"].most_common(top):
        falt = collections.Counter()
        for d, c in res["status"][ev].items():
            falt[d] = c.get(DEFAULTED, 0) + c.get(UNKNOWN, 0)
        piores = [d for d, v in falt.most_common() if v >= n * 0.9][:5]
        print(f"  {ev[:28]:28s} {n:5d} {res['pontuaveis'].get(ev, 0):5d} "
              f"{res['completude'][ev]*100:5.1f}%   {', '.join(piores)}")
    print()
    glob = collections.Counter()
    for ev, dd in res["status"].items():
        for d, c in dd.items():
            glob[d] += c.get(ESTABLISHED, 0)
    tot_dim = collections.Counter()
    for ev, dd in res["status"].items():
        for d, c in dd.items():
            tot_dim[d] += sum(c.values())
    print("  dimensão comprovada (ESTABLISHED) sobre todos os pares:")
    for d in UniversalEventAssessment.DIMS:
        t = tot_dim[d] or 1
        print(f"    {d:20s} {glob[d]:5d}/{tot_dim[d]:5d} = {glob[d]/t*100:5.1f}%")
    print("=" * 112)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--shadow", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import risk_dashboard as rd
    cfg = rd.load_config(str(CONFIG))
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    ueas = avaliar_corpus(hist, cfg, limite=a.limite, shadow=a.shadow)
    res = resumo(ueas)
    imprimir(res, top=a.top)
    if a.json:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        p = OUTDIR / ("universal_assessment_shadow.json" if a.shadow
                      else "universal_assessment.json")
        io.open(p, "w", encoding="utf-8").write(json.dumps(
            {"version": UEA_VERSION, "shadow": a.shadow,
             "resumo": {"candidatos": dict(res["candidatos"]),
                        "pontuaveis": dict(res["pontuaveis"]),
                        "status": res["status"],
                        "completude": res["completude"]},
             "amostra": [u.to_dict() for u in ueas[:40]]},
            ensure_ascii=False, indent=2, sort_keys=True, default=str))
        print(f"  json → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
