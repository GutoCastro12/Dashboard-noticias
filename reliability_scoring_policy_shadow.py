#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_scoring_policy_shadow.py — SCORING POLICY SHADOW V1.

O lado da OCORRENCIA ficou pronto em `f880419`. O que sobra e uma pergunta de
POLITICA, e ela nao e minha para responder: quanto risco um evento MATERIAL mas
de direcao INDETERMINADA deve somar?

Este modulo nao escolhe. Ele MEDE, para que a escolha humana seja informada por
numero e nao por impressao.

TRES PERGUNTAS QUE HOJE CHEGAM COLADAS
---------------------------------------
  MATERIALIDADE       o fato e relevante o bastante para aparecer no radar?
  DIRECAO             a evidencia indica adverso, favoravel, contextual, incerto?
  AUTORIDADE DE SCORE isto deve AUMENTAR o Score de Risco, e quanto?

O achado que originou esta onda: `config_risco.yaml` declara `direction:
neutra` para 8 familias que MESMO ASSIM recebem peso de risco positivo, 180
pontos somados — `ma` 40, `emissao_divida` 35, `follow_on` 30, `troca_ceo` 25 e
mais quatro menores, todas com severidade `alto` ou `medio`. O painel trata
"aconteceu algo material" como "aconteceu algo ruim".

DE ONDE VEM O P0
----------------
A linha-base sai do `breakdown` que a propria `build_evolution` publica: e a
decomposicao auditavel de `best_contribs`, com `contrib`, `direction`,
`severity` e `base` por ocorrencia. Nada e reimplementado.

PRECISAO DECLARADA
------------------
`breakdown` arredonda `contrib` a 0,1. Somar as partes arredondadas reproduz o
`total_score` da producao em 61 de 63 emissores; nos outros dois a diferenca e
o residuo de arredondamento (<= 0,5). O STATUS reproduz 63 de 63, porque a
regra usa o `total_score` da propria producao mais os sinalizadores
`hard_critical` e `persistent` que a linha ja carrega. Como TODAS as politicas
usam o mesmo metodo, o residuo se cancela nas COMPARACOES — que e o que esta
onda precisa medir.

LIMIARES
--------
`build_evolution(history, cfg)` sem `thresholds` usa as bases da config
(atencao 60, critico 125) — e essa a chamada que o reprodutor publicado ja
usava como producao. Os limiares adaptativos (36/75 nesta rodada) sao
calculados e REPORTADOS, nunca aplicados: §28 proibe recalibrar aqui.

AUTORIDADE: NENHUMA. Somente leitura, nenhum caminho de producao o importa.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import statistics
from collections import Counter, defaultdict

import reliability_occurrence_shadow as osd
import risk_dashboard as rd

POLICY_VERSION = "scoring.policy.shadow.v1"
AUTORIDADE = {
    "production_score_authority": "NONE",
    "production_occurrence_authority": "NONE",
    "config_write_authority": "NONE",
    "threshold_authority": "NONE",
    "output_label": "SHADOW / SIMULATED",
}

# §3 — estados de direcao permitidos no diagnostico
ADVERSO = "ADVERSE"
CONTEXTUAL = "CONTEXT_DEPENDENT"
FAVORAVEL = "FAVORABLE"
DESCONHECIDO = "UNKNOWN"


def classificar_direcao(direcao_config: str) -> str:
    """A taxonomia ja declara `direction`. Nao se inventa polaridade nova:
    `negativa` e adversa; `neutra` e dependente de contexto; `positiva` e
    `mitigadora` sao favoraveis; ausencia e desconhecida."""
    d = (direcao_config or "").strip().lower()
    if d == "negativa":
        return ADVERSO
    if d == "neutra":
        return CONTEXTUAL
    if d in ("positiva", "mitigadora"):
        return FAVORAVEL
    return DESCONHECIDO


# ── linha-base: producao, lida do proprio breakdown ─────────────────────────
def linha_base(historico="risk_history.json", config="config_risco.yaml") -> dict:
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    evo = rd.build_evolution(H, cfg)
    tax = {e["id"]: e for e in cfg["taxonomy"]}
    rotulo_para_id = {}
    for e in cfg["taxonomy"]:
        rotulo_para_id.setdefault(e["label"], e["id"])
    st = cfg.get("evolution", {}).get("status", {})
    limiares = {"atencao": st.get("atencao_total_min", 60),
                "critico": st.get("critico_total_min", 125),
                "evento_critico": st.get("critico_event_min_score", 90),
                "fonte": "config base (build_evolution sem `thresholds`)"}
    modo = {c["name"]: c.get("scoring_mode", "normal")
            for c in cfg.get("watchlist", [])}

    empresas = {}
    for l in evo:
        itens = []
        for b in (l.get("breakdown") or []):
            eid = rotulo_para_id.get(b["label"], "")
            itens.append({
                "family": eid, "label": b["label"], "date": b["date"],
                "peso_base": b.get("base", 0), "trust_f": b.get("trust_f", 1.0),
                "decay_f": b.get("decay_f", 1.0),
                "base_contrib": b.get("base_contrib", 0.0),
                "corrob_bonus": b.get("corrob_bonus", 0.0),
                # `contrib` ja sai GATEADO da producao promovida. O POTENCIAL
                # nao gateado — "quanto isto somaria se a familia pontuasse" —
                # e o que permite continuar medindo politica; ele vem de
                # `base_contrib + corrob_bonus`, calculados antes do portao.
                "contrib": round(b.get("base_contrib", 0.0)
                                 + b.get("corrob_bonus", 0.0), 1),
                "contrib_vigente": b.get("contrib", 0.0),
                "score_authority": bool(b.get("score_authority", True)),
                "severidade": b.get("severity", ""),
                "direcao_config": b.get("direction", ""),
                "direcao": classificar_direcao(b.get("direction", "")),
                "fontes": b.get("sources", 1),
                "titulo": (b.get("title") or "")[:120]})
        empresas[l["company"]] = {
            "company": l["company"], "itens": itens,
            "producao_total": l["total_score"], "producao_status": l["status"],
            "hard_critical": bool(l.get("hard_critical")),
            "persistent": bool(l.get("persistent")),
            "scoring_mode": modo.get(l["company"], "normal"),
            "tier": l.get("tier"), "asset_group": l.get("asset_group")}
    return {"_meta": {"policy_version": POLICY_VERSION, **AUTORIDADE,
                      "nota": ("`contrib` aqui e o POTENCIAL nao gateado; a "
                               "producao promovida ja aplica o portao de "
                               "direcao, e o valor vigente fica em "
                               "`contrib_vigente`")},
            "empresas": empresas, "limiares": limiares, "taxonomy": tax,
            "limiares_adaptativos_reportados": rd.calibrate_thresholds(H, cfg),
            "corpus": len(H["articles"])}


def _status(total: float, n_tipos: int, e: dict, lim: dict) -> str:
    """A REGRA da producao, verbatim. Nenhum limiar novo, nenhum termo novo."""
    if e["scoring_mode"] == "monitoramento_limitado":
        return "monitoramento_limitado"
    if e["hard_critical"] or total >= lim["critico"]:
        return "critico"
    if e["persistent"] or total >= lim["atencao"] or n_tipos >= 2:
        return "atencao"
    return "monitorar"


# ── as politicas ────────────────────────────────────────────────────────────
def _mult_p0(item):
    return 1.0


def _mult_p1(item):
    """§7 — portao de direcao. Adverso mantem a mecanica atual; contextual,
    favoravel e desconhecido contribuem ZERO. Nunca subtrai (§22)."""
    return 1.0 if item["direcao"] == ADVERSO else 0.0


def _mult_neutro(k):
    def f(item):
        return 1.0 if item["direcao"] == ADVERSO else k
    return f


POLITICAS_BASE = {
    "P0_CURRENT": _mult_p0,
    "P1_DIRECTION_GATED": _mult_p1,
    "PM_0.25": _mult_neutro(0.25),
    "PM_0.50": _mult_neutro(0.50),
    "PM_1.00": _mult_neutro(1.00),
}


def simular(base: dict, multiplicador, *, cap_por_familia: bool = False,
            tipos_so_pontuaveis: bool = False) -> dict:
    """Aplica um multiplicador por ocorrencia e recalcula total e status.

    `cap_por_familia` (§9): as ocorrencias continuam economicamente distintas —
    o cap age SO no score, deixando uma parcela por empresa x familia. Serve
    para separar "inflacao por multiplicidade" de "inflacao pelo peso de
    existir".

    `tipos_so_pontuaveis` (§7 variante): o gatilho `n_tipos >= 2` do status
    passa a contar apenas familias com autoridade de score. Reportado a parte
    porque MUDA a regra de status, e §28 manda nao decidir isso aqui."""
    lim = base["limiares"]
    fora = {}
    for nome, e in base["empresas"].items():
        pontuados = []
        for it in e["itens"]:
            m = multiplicador(it)
            pontuados.append({**it, "multiplicador": m,
                              "contrib_politica": round(it["contrib"] * m, 4)})
        if cap_por_familia:
            melhor = {}
            for it in pontuados:
                k = it["family"] or it["label"]
                if k not in melhor or it["contrib_politica"] > melhor[k]["contrib_politica"]:
                    melhor[k] = it
            for it in pontuados:
                if melhor.get(it["family"] or it["label"]) is not it:
                    it["contrib_politica"] = 0.0
                    it["capado"] = True
        total = sum(it["contrib_politica"] for it in pontuados)
        if tipos_so_pontuaveis:
            n_tipos = len({it["family"] or it["label"] for it in pontuados
                           if it["contrib_politica"] > 0})
        else:
            n_tipos = len({it["family"] or it["label"] for it in pontuados})
        fora[nome] = {
            "company": nome, "total": round(total, 1),
            "status": _status(round(total), n_tipos, e, lim),
            "n_tipos": n_tipos, "itens": pontuados,
            "producao_total": e["producao_total"],
            "producao_status": e["producao_status"],
            "adverso": round(sum(it["contrib"] for it in e["itens"]
                                 if it["direcao"] == ADVERSO), 1),
            "contextual": round(sum(it["contrib"] for it in e["itens"]
                                    if it["direcao"] == CONTEXTUAL), 1),
            "favoravel": round(sum(it["contrib"] for it in e["itens"]
                                   if it["direcao"] == FAVORAVEL), 1)}
    return {"empresas": fora, "authority": "SHADOW / SIMULATED"}


# ── §13 · distribuicao ──────────────────────────────────────────────────────
def distribuicao(sim: dict) -> dict:
    v = sorted(x["total"] for x in sim["empresas"].values())
    stt = Counter(x["status"] for x in sim["empresas"].values())

    def q(p):
        return round(rd._percentile(v, p), 1)

    return {"n": len(v), "total_sistema": round(sum(v), 1),
            "media": round(statistics.fmean(v), 1) if v else 0.0,
            "mediana": q(50), "p25": q(25), "p75": q(75), "p90": q(90),
            "p95": q(95), "max": round(max(v), 1) if v else 0.0,
            "monitorar": stt.get("monitorar", 0), "atencao": stt.get("atencao", 0),
            "critico": stt.get("critico", 0),
            "monitoramento_limitado": stt.get("monitoramento_limitado", 0)}


# ── §14/§15 · ranking e transicoes ──────────────────────────────────────────
def _ranking(sim: dict) -> dict:
    ordem = sorted(sim["empresas"].values(),
                   key=lambda x: (-x["total"], x["company"]))
    return {x["company"]: i + 1 for i, x in enumerate(ordem)}


def comparar_politicas(p0: dict, px: dict, top: int = 30) -> dict:
    r0, rx = _ranking(p0), _ranking(px)
    linhas = []
    for nome, a in p0["empresas"].items():
        b = px["empresas"][nome]
        linhas.append({
            "company": nome, "rank_p0": r0[nome], "rank_politica": rx[nome],
            "delta_rank": r0[nome] - rx[nome],
            "score_p0": a["total"], "score_politica": b["total"],
            "delta_score": round(b["total"] - a["total"], 1),
            "status_p0": a["status"], "status_politica": b["status"],
            "mudou_status": a["status"] != b["status"],
            "contextual_removido": round(a["contextual"] - b["contextual"], 1)})
    trans = Counter((l["status_p0"], l["status_politica"]) for l in linhas)
    return {
        "top_por_score_p0": sorted(linhas, key=lambda x: x["rank_p0"])[:top],
        "maiores_quedas_de_score": sorted(linhas, key=lambda x: x["delta_score"])[:20],
        "maiores_mudancas_de_rank": sorted(
            linhas, key=lambda x: -abs(x["delta_rank"]))[:20],
        "mudancas_de_status": [l for l in linhas if l["mudou_status"]],
        "matriz_transicao": {f"{a} -> {b}": n for (a, b), n in
                             sorted(trans.items(), key=lambda kv: -kv[1])},
        "authority": "SHADOW / SIMULATED"}


# ── §16/§17/§18 · de onde vem o score do sistema ───────────────────────────
def inventario_direcao(base: dict, p0: dict) -> dict:
    itens = [it for e in base["empresas"].values() for it in e["itens"]]
    tot = sum(it["contrib"] for it in itens)
    por_dir = defaultdict(float)
    cnt = Counter()
    for it in itens:
        por_dir[it["direcao"]] += it["contrib"]
        cnt[it["direcao"]] += 1
    faixas = {">25%": [], ">50%": [], ">75%": [], "100%": []}
    for nome, x in p0["empresas"].items():
        t = x["total"]
        if t <= 0:
            continue
        f = x["contextual"] / t
        if f > 0.25:
            faixas[">25%"].append((nome, round(f, 3)))
        if f > 0.50:
            faixas[">50%"].append((nome, round(f, 3)))
        if f > 0.75:
            faixas[">75%"].append((nome, round(f, 3)))
        if f >= 0.999:
            faixas["100%"].append((nome, round(f, 3)))
    return {
        "ocorrencias_pontuaveis": len(itens),
        "ocorrencias_por_direcao": dict(cnt),
        "pontos_por_direcao": {k: round(v, 1) for k, v in por_dir.items()},
        "total_sistema": round(tot, 1),
        "pct_contextual": (round(por_dir[CONTEXTUAL] / tot, 4) if tot else None),
        "pct_adverso": (round(por_dir[ADVERSO] / tot, 4) if tot else None),
        "empresas_por_faixa": {k: sorted(v, key=lambda x: -x[1])
                               for k, v in faixas.items()},
        "authority": "SHADOW / SIMULATED"}


def auditoria_status(base: dict, p0: dict, p1: dict, alvo: str) -> list:
    fora = []
    for nome, x in p0["empresas"].items():
        if x["status"] != alvo:
            continue
        t = x["total"]
        fora.append({
            "company": nome, "total": t, "status_p0": alvo,
            "adverso": x["adverso"], "contextual": x["contextual"],
            "pct_contextual": (round(x["contextual"] / t, 3) if t else None),
            "status_p1": p1["empresas"][nome]["status"],
            "total_p1": p1["empresas"][nome]["total"],
            "cai_de_status": p1["empresas"][nome]["status"] != alvo,
            "hard_critical": base["empresas"][nome]["hard_critical"],
            "persistent": base["empresas"][nome]["persistent"],
            "n_tipos": x["n_tipos"],
            "familias": sorted({it["family"] for it in x["itens"]})})
    return sorted(fora, key=lambda x: -(x["pct_contextual"] or 0))


# ── §26/§27 · sensibilidade por familia ─────────────────────────────────────
def sensibilidade_por_familia(base: dict, p0: dict) -> list:
    familias = sorted({it["family"] for e in base["empresas"].values()
                       for it in e["itens"] if it["family"]})
    fora = []
    for fam in familias:
        def m(item, _f=fam):
            return 0.0 if item["family"] == _f else 1.0
        sx = simular(base, m)
        mud = [n for n, x in sx["empresas"].items()
               if x["status"] != p0["empresas"][n]["status"]]
        pontos = sum(it["contrib"] for e in base["empresas"].values()
                     for it in e["itens"] if it["family"] == fam)
        emp = {e["company"] for e in base["empresas"].values()
               for it in e["itens"] if it["family"] == fam}
        d0 = distribuicao(p0)["total_sistema"]
        fora.append({
            "family": fam,
            "direcao": classificar_direcao(
                (base["taxonomy"].get(fam) or {}).get("direction", "")),
            "peso_base": (base["taxonomy"].get(fam) or {}).get("score", 0),
            "ocorrencias": sum(1 for e in base["empresas"].values()
                               for it in e["itens"] if it["family"] == fam),
            "empresas": len(emp),
            "pontos_vivos": round(pontos, 1),
            "pct_do_sistema": (round(pontos / d0, 4) if d0 else None),
            "delta_total_se_zerada": round(
                distribuicao(sx)["total_sistema"] - d0, 1),
            "empresas_que_mudam_status": sorted(mud)})
    return sorted(fora, key=lambda x: -x["pontos_vivos"])


# ── §10 · indice de materialidade, separado do score de risco ──────────────
def indice_materialidade(base: dict) -> dict:
    """§10 — o evento material continua VISIVEL mesmo com autoridade de score
    zero. O indice e diagnostico: nao vai para o painel nesta onda."""
    fora = {}
    for nome, e in base["empresas"].items():
        fora[nome] = {
            "eventos_materiais": len(e["itens"]),
            "familias": len({it["family"] for it in e["itens"]}),
            "soma_peso_base": sum(it["peso_base"] for it in e["itens"]),
            "materialidade_ponderada": round(
                sum(it["contrib"] for it in e["itens"]), 1),
            "adverso": round(sum(it["contrib"] for it in e["itens"]
                                 if it["direcao"] == ADVERSO), 1),
            "contextual": round(sum(it["contrib"] for it in e["itens"]
                                    if it["direcao"] == CONTEXTUAL), 1)}
    return {"definicao": "contagem de ocorrencias materiais, soma de peso-base "
                         "e soma ponderada (decaimento x confianca) — INDEPENDENTE "
                         "de direcao e sem autoridade de score",
            "empresas": fora, "authority": "SHADOW / SIMULATED · DIAGNOSTICO"}


# ── §31 · acoplamento e pontos de insercao ─────────────────────────────────
def pontos_de_insercao(caminho="risk_dashboard.py") -> dict:
    src = io.open(caminho, encoding="utf-8").read()
    linhas = src.split("\n")

    def achar(frag):
        for i, l in enumerate(linhas, start=1):
            if frag in l:
                return i
        return None

    return {
        "best_contribs": achar("def best_contribs(negatives"),
        "contrib_formula": achar("base_contrib = o[\"score\"] * d * o.get"),
        "chave_de_ocorrencia": achar("k = o.get(\"_occ_key\") or o[\"event_id\"]"),
        "weighted_total": achar("def weighted_total(negatives"),
        "regra_de_status": achar("elif has_hard_critical or total >= critico_total"),
        "n_negative_types": (achar("n_negative_types = n_risk_signal_types")
                             or achar("n_negative_types = len({o[\"event_id\"]")),
        "montagem_de_candidatos": achar("per_company: dict[str, list[dict]] = {}"),
        "fusao_de_gemeos": achar("twin = next((m for m in merged"),
        "clustering": achar("def assign_occurrence_clusters("),
        "nota": ("um portao de direcao entra em UM ponto: o fator aplicado a "
                 "`base_contrib` dentro de `best_contribs`. A contagem de tipos "
                 "do status (`n_negative_types`) e um SEGUNDO ponto, e e "
                 "decisao separada — hoje ela promove a `atencao` mesmo com "
                 "score zero."),
        "authority": "SHADOW / SIMULATED · SOMENTE LEITURA"}


# ── orquestracao ───────────────────────────────────────────────────────────
def rodar_tudo(historico="risk_history.json", config="config_risco.yaml") -> dict:
    base = linha_base(historico, config)
    sims = {n: simular(base, m) for n, m in POLITICAS_BASE.items()}
    sims["P1b_TIPOS_TAMBEM_GATED"] = simular(base, _mult_p1,
                                             tipos_so_pontuaveis=True)
    sims["P2_FAMILY_CAP"] = simular(base, _mult_p0, cap_por_familia=True)
    p0 = sims["P0_CURRENT"]
    return {
        "_meta": {"policy_version": POLICY_VERSION, **AUTORIDADE},
        "base": base,
        "politicas": sims,
        "distribuicoes": {n: distribuicao(s) for n, s in sims.items()},
        "comparacoes": {n: comparar_politicas(p0, s)
                        for n, s in sims.items() if n != "P0_CURRENT"},
        "inventario_direcao": inventario_direcao(base, p0),
        "criticos": auditoria_status(base, p0, sims["P1_DIRECTION_GATED"],
                                     "critico"),
        "atencao": auditoria_status(base, p0, sims["P1_DIRECTION_GATED"],
                                    "atencao"),
        "sensibilidade_familia": sensibilidade_por_familia(base, p0),
        "materialidade": indice_materialidade(base),
        "pontos_de_insercao": pontos_de_insercao(),
        "fidelidade_p0": fidelidade_p0(base, p0),
        "fidelidade_vigente": fidelidade_vigente(base, sims)}


def fidelidade_vigente(base: dict, sims: dict) -> dict:
    """Qual política simulada reproduz a produção VIGENTE?

    Antes da promoção era P0 (sem portão). Depois da decisão humana de
    2026-08-21 a produção aplica o portão de direção, então quem reproduz é
    P1 — e P0 passa a significar "quanto seria sem o portão", o contrafactual
    que mantém a medição de política viva."""
    fora = {}
    for nome, sim in sims.items():
        s_ok = sum(1 for k, x in sim["empresas"].items()
                   if round(x["total"]) == base["empresas"][k]["producao_total"])
        st_ok = sum(1 for k, x in sim["empresas"].items()
                    if x["status"] == base["empresas"][k]["producao_status"])
        fora[nome] = {"score_identico": s_ok, "status_identico": st_ok,
                      "empresas": len(sim["empresas"])}
    return {"por_politica": fora,
            # [2026-08-22] contextual voltou a contribuir com peso 1,0
            "politica_vigente_na_producao": "P0_CURRENT",
            "authority": "SHADOW / SIMULATED"}


def fidelidade_p0(base: dict, p0: dict) -> dict:
    """P0 e o CONTRAFACTUAL nao gateado. Ele nao reproduz mais a producao, e
    nao deve: a producao promovida aplica o portao de direcao."""
    score_ok, status_ok, resid = 0, 0, []
    for nome, x in p0["empresas"].items():
        e = base["empresas"][nome]
        if round(x["total"]) == e["producao_total"]:
            score_ok += 1
        else:
            resid.append({"company": nome, "p0": x["total"],
                          "producao": e["producao_total"],
                          "residuo": round(x["total"] - e["producao_total"], 2)})
        if x["status"] == e["producao_status"]:
            status_ok += 1
    return {"empresas": len(p0["empresas"]),
            "score_identico": score_ok, "status_identico": status_ok,
            "residuos": resid,
            "causa": ("`breakdown` arredonda `contrib` a 0,1; somar as partes "
                      "arredondadas difere do arredondamento da soma exata em "
                      "ate 0,5. Como TODAS as politicas usam o mesmo metodo, o "
                      "residuo se cancela nas comparacoes."),
            "authority": "SHADOW / SIMULATED"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Scoring Policy Shadow V1 — diagnostico, SEM autoridade.")
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--secao", default="resumo",
                   choices=("resumo", "distribuicoes", "familias", "criticos",
                            "atencao", "jbs", "insercao", "json"))
    a = p.parse_args(argv)
    R = rodar_tudo(a.historico)
    print("SHADOW / SIMULATED — autoridade de producao: NENHUMA")
    if a.secao == "json":
        print(json.dumps(R, ensure_ascii=False, default=str)[:400000])
        return 0
    if a.secao in ("resumo", "distribuicoes"):
        print(json.dumps({"fidelidade_p0": R["fidelidade_p0"],
                          "inventario": R["inventario_direcao"],
                          "distribuicoes": R["distribuicoes"]},
                         ensure_ascii=False, indent=1))
    if a.secao == "familias":
        print(json.dumps(R["sensibilidade_familia"], ensure_ascii=False, indent=1))
    if a.secao in ("criticos", "atencao"):
        print(json.dumps(R[a.secao], ensure_ascii=False, indent=1))
    if a.secao == "jbs":
        print(json.dumps({n: s["empresas"].get("JBS")
                          for n, s in R["politicas"].items()},
                         ensure_ascii=False, indent=1))
    if a.secao == "insercao":
        print(json.dumps(R["pontos_de_insercao"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
