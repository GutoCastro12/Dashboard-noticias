#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_model_benchmark.py — benchmark dos auditores semânticos contra
verdade humana, e a preparação da avaliação DIRECIONAL.

AUTORIDADE: NENHUMA. Somente leitura. Nenhum caminho de produção o importa,
nenhum resultado deste módulo toca score, status ou ocorrência.

O QUE ESTA ONDA PODE E O QUE NÃO PODE MEDIR
-------------------------------------------
**Camada A — validação semântica: MEDÍVEL, e medida aqui.**
`risk_semantic_v2_shadow.json` guarda 48 observações reais dos dois modelos
sobre 24 artigos, no contrato `v2` e no prompt `r7ba.p2`, congeladas. Onze
desses artigos têm adjudicação humana — 22 observações. É contra elas que os
modelos são medidos, sem uma única chamada de rede.

**Camada B — direcionalidade: NÃO MEDÍVEL nesta execução.** Duas razões, e
nenhuma delas é resultado ruim:

  1. o contrato `v2` NÃO emite campo direcional. Os campos são `event_asserted`,
     `company_role`, `occurrence_novelty`, `phase`, `currentness`, `centrality`
     e as citações — direção não está entre eles. Medir direção exigiria
     chamadas novas sob um contrato novo;
  2. não há credencial de modelo neste ambiente — nem em `GEMINI_API_KEY` nem
     em `config_risco.yaml`. Zero chamadas foram feitas.

Então este módulo faz o que é honesto: mede a Camada A por inteiro, MONTA o
manifesto direcional (tiers, controles, casos JBS) para que a próxima execução
com credencial só precise rodar, e reporta a Camada B como NÃO EXECUTADA.

DOIS CONJUNTOS QUE NÃO SE MISTURAM
-----------------------------------
Batch V1 (24 casos / 27 memberships em `risk_human_supervision.json`) e o
Contrato V2 observado pelos modelos são **disjuntos**: nenhum artigo em comum.
Batch V1 é DESENVOLVIMENTO — foi usado para desenhar guardas determinísticas.
O Contrato V2 revisado é VALIDAÇÃO prospectiva. Reportá-los juntos apagaria
essa distinção, e é ela que dá valor ao número de validação.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import statistics
from collections import Counter, defaultdict

import occurrence_engine as oe
import reliability_human_supervision as hs
import risk_dashboard as rd

BENCH_VERSION = "model.benchmark.v2"
AUTORIDADE = {
    "production_score_authority": "NONE",
    "production_occurrence_authority": "NONE",
    "semantic_authority": "NONE",
    "write_authority": "NONE",
    "output_label": "EVALUATION ONLY",
}
SHADOW = "risk_semantic_v2_shadow.json"

# Dimensões que a adjudicação humana do Contrato V2 cobre. `phase`,
# `centrality` e `transaction_object` só existem em parte dos casos — a
# cobertura é reportada, nunca preenchida por inferência.
DIMENSOES = ("event_asserted", "company_role", "subject", "occurrence_novelty",
             "currentness", "phase", "centrality", "transaction_object",
             "relation", "related_entity")

NAO_EMITIDO = "NOT_EMITTED"


def _sh(caminho: str = SHADOW) -> dict:
    return json.load(io.open(caminho, encoding="utf-8"))


# ── manifesto congelado ─────────────────────────────────────────────────────
def manifesto(caminho: str = SHADOW) -> dict:
    """Manifesto DETERMINÍSTICO da avaliação, com hash.

    Separa o que é verdade acionável do que o humano deliberadamente deixou em
    aberto — `UNDETERMINED` e `POLICY_PENDING` NÃO são erro de modelo, e
    contá-los como tal fabricaria uma métrica."""
    S = _sh(caminho)
    obs = S["observacoes"]
    MS = hs.carregar()["memberships"]

    v1 = {"memberships": len(MS),
          "casos": len({m["case_id"] for m in MS.values()}),
          "por_status": dict(Counter(m.get("status", "") for m in MS.values())),
          "artigos": sorted({m["article_id"] for m in MS.values()}),
          "papel": "DEVELOPMENT_SET"}
    v1["CLEAR"] = v1["por_status"].get("CLEAR", 0)
    v1["UNDETERMINED"] = v1["por_status"].get("UNDETERMINED", 0)
    v1["POLICY_PENDING"] = v1["por_status"].get("POLICY_PENDING", 0)

    revisadas = {k: v for k, v in obs.items() if v.get("human_review")}
    v2 = {"observacoes": len(obs),
          "artigos_observados": sorted({k.split("|")[0] for k in obs}),
          "observacoes_revisadas": len(revisadas),
          "artigos_revisados": sorted({k.split("|")[0] for k in revisadas}),
          "modelos": sorted({v["actual_model"] for v in obs.values()}),
          "prompts": sorted({v["prompt_version"] for v in obs.values()}),
          "contratos": sorted({v["contract_version"] for v in obs.values()}),
          "papel": "PROSPECTIVE_VALIDATION_SET"}
    v2["artigos_nao_revisados"] = sorted(
        set(v2["artigos_observados"]) - set(v2["artigos_revisados"]))

    sobreposicao = sorted(set(v1["artigos"]) & set(v2["artigos_observados"]))
    corpo = json.dumps({"v1": v1, "v2": v2, "sobreposicao": sobreposicao},
                       sort_keys=True, ensure_ascii=False)
    return {"_meta": {"bench_version": BENCH_VERSION, **AUTORIDADE},
            "batch_v1": v1, "contrato_v2": v2,
            "sobreposicao_v1_v2": sobreposicao,
            "higiene": ("Batch V1 e Contrato V2 sao DISJUNTOS. V1 e conjunto de "
                        "DESENVOLVIMENTO (desenhou guardas deterministicas); o "
                        "V2 revisado e VALIDACAO prospectiva. Reportar os dois "
                        "juntos apagaria a distincao."),
            "freeze": S["_meta"],
            "manifesto_hash": hashlib.sha256(corpo.encode("utf-8")).hexdigest()[:16]}


# ── Camada A · validação semântica ──────────────────────────────────────────
def _eventos_do_modelo(o: dict) -> list:
    return [e for e in ((o.get("evidencia") or {}).get("eventos") or [])]


def _melhor_evento(o: dict, event_id: str) -> dict | None:
    """O evento que o modelo emitiu para a família candidata; se não emitiu
    nenhum para ela, o primeiro — e a divergência aparece em `event_id`."""
    evs = _eventos_do_modelo(o)
    if not evs:
        return None
    return next((e for e in evs if e.get("event_id") == event_id), evs[0])


def _det_para_dimensao(det: dict, dim: str):
    """Traduz o snapshot determinístico para o vocabulário das dimensões.

    O determinístico não emite a maioria delas — e é isso que se reporta, em
    vez de inferir um valor que ele nunca decidiu."""
    if dim == "event_asserted":
        return "ASSERTED" if det.get("atribuida") else NAO_EMITIDO
    if dim == "company_role":
        if det.get("relation_type") == "direto" and not det.get("subject_company"):
            return "SUBJECT"
        return NAO_EMITIDO
    if dim == "phase":
        return det.get("event_phase") or NAO_EMITIDO
    return NAO_EMITIDO


def camada_a(caminho: str = SHADOW) -> dict:
    """Modelo × humano, dimensão por dimensão, só nos casos REVISADOS."""
    S = _sh(caminho)
    obs = S["observacoes"]
    linhas, por_modelo = [], defaultdict(lambda: defaultdict(Counter))
    # O snapshot deterministico e o MESMO nas duas observacoes do artigo (uma
    # por modelo). Conta-lo em cada uma dobraria o denominador e faria o
    # deterministico parecer avaliado no dobro dos casos.
    det_vistos: set = set()
    for k, o in sorted(obs.items()):
        hr = o.get("human_review")
        if not hr:
            continue
        adj = hr.get("dimensoes_adjudicadas") or {}
        ev = _melhor_evento(o, o["candidate_event"])
        modelo = o["actual_model"]
        linha = {"chave": k, "article_id": o["article_id"],
                 "company": o["company"], "family": o["candidate_event"],
                 "modelo": modelo, "titulo": (o.get("title") or "")[:110],
                 "verdict_humano": hr.get("verdict"),
                 "scoreable_humano": hr.get("scoreable"),
                 "scoreable_det": (o.get("deterministic") or {}).get("scoreable"),
                 "classe_de_falha": hr.get("classe_de_falha"),
                 "latencia_s": o.get("latencia_s"),
                 "usage": o.get("usage"), "estado": o.get("estado"),
                 "dimensoes": {}}
        for dim in DIMENSOES:
            if dim not in adj:
                continue
            hum = adj[dim]
            mod = (ev or {}).get(dim, NAO_EMITIDO)
            det = _det_para_dimensao(o.get("deterministic") or {}, dim)
            # `subject` é texto livre na adjudicação humana: compara-se por
            # continência, não por igualdade — exigir string idêntica mediria
            # redação, não acerto.
            if dim == "subject":
                ok = bool(mod) and (rd.normalize(str(mod))
                                    in rd.normalize(str(hum))
                                    or rd.normalize(str(hum)).startswith(
                                        rd.normalize(str(mod))))
            else:
                ok = (str(mod).upper() == str(hum).upper())
            linha["dimensoes"][dim] = {"humano": hum, "modelo": mod,
                                       "deterministico": det, "acerto": ok}
            por_modelo[modelo][dim]["avaliaveis"] += 1
            por_modelo[modelo][dim]["acertos" if ok else "erros"] += 1
            if det != NAO_EMITIDO and (o["article_id"], dim) not in det_vistos:
                det_vistos.add((o["article_id"], dim))
                por_modelo["DETERMINISTICO"][dim]["avaliaveis"] += 1
                por_modelo["DETERMINISTICO"][dim][
                    "acertos" if str(det).upper() == str(hum).upper()
                    else "erros"] += 1
        # scoreability é a dimensão de decisão: o humano a adjudica
        # explicitamente em parte dos casos
        if hr.get("scoreable") is not None:
            det_sc = (o.get("deterministic") or {}).get("scoreable")
            if (o["article_id"], "scoreable") not in det_vistos:
                det_vistos.add((o["article_id"], "scoreable"))
                por_modelo["DETERMINISTICO"]["scoreable"]["avaliaveis"] += 1
                por_modelo["DETERMINISTICO"]["scoreable"][
                    "acertos" if det_sc == hr["scoreable"] else "erros"] += 1
            mod_sc = _scoreable_do_modelo(ev)
            linha["scoreable_modelo"] = mod_sc
            if mod_sc is not None:
                por_modelo[modelo]["scoreable"]["avaliaveis"] += 1
                por_modelo[modelo]["scoreable"][
                    "acertos" if mod_sc == hr["scoreable"] else "erros"] += 1
        linhas.append(linha)
    return {"linhas": linhas,
            "por_modelo": {m: {d: dict(c) for d, c in dd.items()}
                           for m, dd in por_modelo.items()},
            "observacoes_avaliadas": len(linhas),
            "authority": "EVALUATION ONLY"}


def _scoreable_do_modelo(ev: dict | None):
    """Pontuável, na leitura do modelo: evento AFIRMADO, empresa SUJEITO e
    fato CORRENTE. Derivada, e declarada como derivada — o contrato `v2` não
    tem campo `scoreable`."""
    if not ev:
        return None
    if ev.get("event_asserted") != "ASSERTED":
        return False
    if ev.get("currentness") not in ("CURRENT", None, ""):
        return False
    return ev.get("company_role") == "SUBJECT"


# ── evidência ───────────────────────────────────────────────────────────────
def evidencia(caminho: str = SHADOW) -> dict:
    """§17 — a citação sustenta o que foi emitido? O validador do próprio
    contrato já checa as citações; aqui se agrega o resultado dele, sem
    reinterpretar."""
    S = _sh(caminho)
    por_modelo = defaultdict(Counter)
    for k, o in S["observacoes"].items():
        m = o["actual_model"]
        for ev in _eventos_do_modelo(o):
            val = ev.get("_validacao") or {}
            q = val.get("quotes") or {}
            invalidas = q.get("invalidas") or []
            checadas = q.get("checadas") or []
            if not checadas:
                por_modelo[m]["SEM_CITACAO"] += 1
            elif not invalidas:
                por_modelo[m]["GROUNDED"] += 1
            elif len(invalidas) < len(checadas):
                por_modelo[m]["PARTIALLY_GROUNDED"] += 1
            else:
                por_modelo[m]["UNSUPPORTED"] += 1
            if ev.get("field_support") and ev["field_support"] != "SUPPORTED":
                por_modelo[m]["FIELD_SUPPORT_" + ev["field_support"]] += 1
    return {"por_modelo": {m: dict(c) for m, c in por_modelo.items()},
            "authority": "EVALUATION ONLY"}


# ── telemetria ──────────────────────────────────────────────────────────────
def telemetria(caminho: str = SHADOW) -> dict:
    """§46 — o que a observação congelada registrou. O que ela não registrou é
    dito `indisponivel`, não estimado."""
    S = _sh(caminho)
    por_modelo = defaultdict(lambda: {"chamadas": 0, "ok": 0, "falhas": 0,
                                      "latencias": [], "tok_in": 0,
                                      "tok_out": 0, "tok_total": 0,
                                      "sem_usage": 0})
    for o in S["observacoes"].values():
        m = por_modelo[o["actual_model"]]
        m["chamadas"] += 1
        m["ok" if o.get("estado") == "OK" else "falhas"] += 1
        if o.get("latencia_s") is not None:
            m["latencias"].append(float(o["latencia_s"]))
        u = o.get("usage") or {}
        if not u:
            m["sem_usage"] += 1
        m["tok_in"] += u.get("prompt_token_count") or u.get("input_tokens") or 0
        m["tok_out"] += (u.get("candidates_token_count")
                         or u.get("output_tokens") or 0)
        m["tok_total"] += u.get("total_token_count") or u.get("total_tokens") or 0
    fora = {}
    for k, v in por_modelo.items():
        L = sorted(v["latencias"])
        fora[k] = {
            "chamadas": v["chamadas"], "ok": v["ok"], "falhas": v["falhas"],
            "tokens_entrada": v["tok_in"] or "indisponivel",
            "tokens_saida": v["tok_out"] or "indisponivel",
            "tokens_total": v["tok_total"] or "indisponivel",
            "observacoes_sem_usage": v["sem_usage"],
            "latencia_media_s": round(statistics.fmean(L), 2) if L else "indisponivel",
            "latencia_mediana_s": round(statistics.median(L), 2) if L else "indisponivel",
            "latencia_p95_s": (round(rd._percentile(L, 95), 2) if L
                               else "indisponivel"),
            "custo": "indisponivel — a observacao congelada nao registra preco",
        }
    return {"por_modelo": fora,
            "chamadas_nesta_execucao": 0,
            "nota": ("Nenhuma chamada foi feita nesta execucao: nao ha "
                     "credencial de modelo neste ambiente. Os numeros vem das "
                     "observacoes CONGELADAS."),
            "authority": "EVALUATION ONLY"}


# ── §42 · matriz humana completa, caso a caso ──────────────────────────────
def matriz_humana(cam_a: dict) -> dict:
    """Toda linha revisada, com humano / determinístico / cada modelo lado a
    lado. É o artefato de auditoria: nada aqui é agregado."""
    porart = defaultdict(dict)
    for l in cam_a["linhas"]:
        porart[l["article_id"]][l["modelo"]] = l
    linhas = []
    for aid, d in sorted(porart.items()):
        base = next(iter(d.values()))
        celulas = {}
        for dim in DIMENSOES:
            x = base["dimensoes"].get(dim)
            if not x:
                continue
            celulas[dim] = {
                "humano": x["humano"],
                "deterministico": x.get("deterministico", NAO_EMITIDO),
                **{m: {"valor": l["dimensoes"][dim]["modelo"],
                       "acerto": l["dimensoes"][dim]["acerto"]}
                   for m, l in d.items() if dim in l["dimensoes"]}}
        linhas.append({
            "article_id": aid, "company": base["company"],
            "family": base["family"], "titulo": base["titulo"],
            "verdict_humano": base["verdict_humano"],
            "scoreable": {"humano": base.get("scoreable_humano"),
                          "deterministico": base.get("scoreable_det"),
                          **{m: l.get("scoreable_modelo")
                             for m, l in d.items()}},
            "dimensoes": celulas})
    return {"linhas": linhas, "n": len(linhas), "authority": "EVALUATION ONLY"}


# ── §31/§34 · valor do modelo sobre o determinístico ────────────────────────
def valor_sobre_deterministico(cam_a: dict) -> dict:
    """RESGATE = determinístico errou e o modelo acertou. REGRESSÃO = o inverso.

    Medido em `scoreable`, que é a dimensão de DECISÃO — a única em que o
    determinístico realmente emite um veredito comparável."""
    porart = defaultdict(dict)
    for l in cam_a["linhas"]:
        porart[l["article_id"]][l["modelo"]] = l
    fora = defaultdict(lambda: {"resgates": [], "regressoes": [], "ambos_erram": []})
    disc, so_g1, so_g2 = [], [], []
    for aid, d in porart.items():
        base = next(iter(d.values()))
        hum = base.get("scoreable_humano")
        if hum is None:
            continue
        det = base.get("scoreable_det")
        det_ok = (det == hum)
        certos = {}
        for modelo, l in d.items():
            mod = l.get("scoreable_modelo")
            if mod is None:
                continue
            ok = (mod == hum)
            certos[modelo] = ok
            rot = base["company"] + "/" + base["family"]
            if ok and not det_ok:
                fora[modelo]["resgates"].append(rot)
            elif det_ok and not ok:
                fora[modelo]["regressoes"].append(rot)
            elif not ok and not det_ok:
                fora[modelo]["ambos_erram"].append(rot)
        if len(certos) == 2:
            v = list(certos.values())
            k = list(certos)
            if v[0] != v[1]:
                disc.append({"company": base["company"], "family": base["family"],
                             "acertou": k[v.index(True)]})
                (so_g1 if k[v.index(True)] == "gemini-3.1-flash-lite"
                 else so_g2).append(base["company"])
    return {"por_modelo": {m: {k: sorted(v) for k, v in d.items()}
                           for m, d in fora.items()},
            "discordancias_g1_g2": disc,
            "resgates_unicos_g1": sorted(set(so_g1)),
            "resgates_unicos_g2": sorted(set(so_g2)),
            "valor_incremental_do_segundo_modelo": (
                "nenhum" if not so_g2 else "medido: " + str(sorted(set(so_g2)))),
            "authority": "EVALUATION ONLY"}


# ── §32 · o papel mais seguro: gatilho de revisão ──────────────────────────
def gatilho_de_revisao(cam_a: dict) -> dict:
    """O modelo NÃO decide; ele só levanta a mão quando discorda do
    determinístico. Precisão = das vezes que levantou, quantas apontavam um
    erro REAL do determinístico."""
    porart = defaultdict(dict)
    for l in cam_a["linhas"]:
        porart[l["article_id"]][l["modelo"]] = l
    fora = {}
    for modelo in ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite"):
        levantou, certos, falsos = [], [], []
        for aid, d in porart.items():
            l = d.get(modelo)
            if not l or l.get("scoreable_modelo") is None:
                continue
            hum = l.get("scoreable_humano")
            if hum is None:
                continue
            if l["scoreable_modelo"] != l.get("scoreable_det"):
                rot = l["company"] + "/" + l["family"]
                levantou.append(rot)
                (certos if l["scoreable_modelo"] == hum else falsos).append(rot)
        fora[modelo] = {
            "disagreements_surfaced": len(levantou),
            "erros_reais_do_deterministico_encontrados": sorted(certos),
            "falsos_alarmes": sorted(falsos),
            "precisao": (round(len(certos) / len(levantou), 4)
                         if levantou else None)}
    return {"por_modelo": fora, "authority": "EVALUATION ONLY"}


# ── §36/§37/§43 · gaps de prompt, só com evidência repetida ────────────────
def gaps(cam_a: dict) -> dict:
    """Um gap só é aberto com padrão causal REPETIDO. Um exemplo é anedota."""
    porart = defaultdict(dict)
    for l in cam_a["linhas"]:
        porart[l["article_id"]][l["modelo"]] = l

    def casos(pred):
        return [ {"company": b["company"], "family": b["family"],
                  "titulo": b["titulo"][:80],
                  "modelos_que_erram": sorted(m for m, l in d.items() if pred(l))}
                 for aid, d in porart.items()
                 for b in [next(iter(d.values()))]
                 if any(pred(l) for l in d.values()) ]

    def erra(l, dim):
        x = l["dimensoes"].get(dim)
        return bool(x) and not x["acerto"]

    pessoa = [c for c in casos(lambda l: erra(l, "company_role")
                               and (l["dimensoes"]["company_role"]["humano"]
                                    in ("MENTIONED", "UNRELATED")))]
    ceo = [c for c in casos(lambda l: l["family"] == "troca_ceo"
                            and erra(l, "event_asserted"))]
    return {
        "MODEL_PROMPT_PERSON_COMPANY_GAP": {
            "casos": pessoa, "n": len(pessoa),
            "estado": ("CONFIRMED" if len(pessoa) >= 2 else
                       "SINGLE_EXAMPLE_NOT_ENOUGH" if pessoa else "NOT_OBSERVED"),
            "regra": "§36 exige ao menos 2 exemplos humanos com o MESMO padrao "
                     "causal; um exemplo e anedota"},
        "MODEL_PROMPT_CEO_ASSERTION_GAP": {
            "casos": ceo, "n": len(ceo),
            "estado": ("CONFIRMED" if len(ceo) >= 2 else
                       "SINGLE_EXAMPLE_NOT_ENOUGH" if ceo else "NOT_OBSERVED")},
        "MODEL_PROMPT_MATERIALITY_DIRECTION_GAP": {
            "casos": [], "n": 0,
            "estado": "NOT_MEASURABLE",
            "motivo": "o contrato `v2` nao emite direcao; medir exigiria "
                      "chamadas novas sob contrato novo"},
        "authority": "EVALUATION ONLY"}


# ── Camada B · direcionalidade ──────────────────────────────────────────────
# Rótulos permitidos. Nenhum multiplicador numérico é emitido: §18 proíbe.
ADVERSE, FAVORABLE, NEUTRAL, MIXED, UNCERTAIN = (
    "ADVERSE", "FAVORABLE", "NEUTRAL", "MIXED", "UNCERTAIN")

TIER1 = "TIER1_EXPLICIT_DIRECTIONAL_TRUTH"
TIER2 = "TIER2_HUMAN_BOUNDED_TRUTH"


def manifesto_direcional(historico="risk_history.json",
                         config="config_risco.yaml") -> dict:
    """§21 — os dois tiers de verdade direcional, montados da OCORRÊNCIA.

    A supervisão existente não foi construída como dataset de polaridade. Em
    vez de inventar um rótulo por caso, separam-se dois regimes:

      TIER 1  a direção está estabelecida — a família é declarada `negativa`
              na config, e o esperado é ADVERSE;
      TIER 2  o humano estabeleceu apenas que NÃO é automaticamente adverso.
              Aqui não há rótulo único: avalia-se o EXCESSO — ADVERSE sem
              evidência, FAVORABLE sem evidência — e a abstenção apropriada.

    A unidade é a OCORRÊNCIA, não o artigo (§19): duas fontes corroborando o
    mesmo fato não podem virar dois votos direcionais.
    """
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    tax = {e["id"]: e for e in cfg["taxonomy"]}
    evo = rd.build_evolution(H, cfg)
    casos = []
    for l in evo:
        for o in (l.get("events") or []):
            oc = o.get("_ocorrencia")
            if not oc:
                continue
            ev = tax.get(oc["members"][0]["family"], {})
            classe = oe.classe_de_sinal(ev)
            if classe == oe.SINAL_NAO_RISCO:
                tier, esperado = TIER1, FAVORABLE
            elif classe == oe.SINAL_ADVERSO:
                tier, esperado = TIER1, ADVERSE
            else:
                tier, esperado = TIER2, None
            casos.append({
                "occurrence_id": oc["occurrence_id"],
                "company": oc["company"], "family": oc["family"],
                "canonical_object": oc["canonical_object"],
                "classe_de_sinal": classe, "tier": tier,
                "direcao_esperada": esperado,
                "regra_tier2": (None if esperado else
                                "sem rotulo unico: mede-se ADVERSE sem "
                                "evidencia, FAVORABLE sem evidencia e "
                                "abstencao apropriada"),
                "anchor_date": oc["anchor_date"],
                "phase_por_membro": [m["phase"] for m in oc["members"]],
                "n_membros": len(oc["members"]),
                # §19 — a entrada do modelo é a OCORRÊNCIA: representante mais
                # membros, não um artigo solto
                "representante": oc["display_representative"],
                "membros": [m["article_id"] for m in oc["members"]],
                "titulo_representante": next(
                    (m["title"] for m in oc["members"]
                     if m["article_id"] == oc["display_representative"]),
                    oc["members"][0]["title"]),
            })
    t1 = [c for c in casos if c["tier"] == TIER1]
    t2 = [c for c in casos if c["tier"] == TIER2]
    return {"_meta": {"bench_version": BENCH_VERSION, **AUTORIDADE},
            "casos": sorted(casos, key=lambda c: c["occurrence_id"]),
            "tier1": len(t1), "tier2": len(t2),
            "tier1_adverso_esperado": sum(1 for c in t1
                                          if c["direcao_esperada"] == ADVERSE),
            "tier1_favoravel_esperado": sum(1 for c in t1
                                            if c["direcao_esperada"] == FAVORABLE),
            "rotulos_permitidos": [ADVERSE, FAVORABLE, NEUTRAL, MIXED, UNCERTAIN],
            "unidade": "OCCURRENCE",
            "sem_multiplicador_numerico": True,
            "authority": "EVALUATION ONLY"}


def pacote_jbs(historico="risk_history.json", config="config_risco.yaml") -> dict:
    """§20 — o pacote direcional da JBS, caso-produto desta frente.

    A verdade humana disponível para as quatro contextuais NÃO é "positivo".
    É: **não são necessariamente ruins**, e a evidência determinística sozinha
    não sustenta deterioração confirmada. Por isso `UNCERTAIN`, `NEUTRAL` e
    `MIXED` são todos defensáveis ali — e o erro a detectar é o oposto:
    `ADVERSE` com confiança e sem evidência."""
    M = manifesto_direcional(historico, config)
    jbs = [c for c in M["casos"] if c["company"] == "JBS"]
    for c in jbs:
        c["verdade_humana"] = (
            "ADVERSE esperado (familia declarada `negativa`)"
            if c["tier"] == TIER1 else
            "NAO NECESSARIAMENTE ADVERSO — UNCERTAIN/NEUTRAL/MIXED defensaveis; "
            "o erro a detectar e ADVERSE confiante sem evidencia")
    return {"ocorrencias": jbs, "n": len(jbs),
            "tier1": sum(1 for c in jbs if c["tier"] == TIER1),
            "tier2": sum(1 for c in jbs if c["tier"] == TIER2),
            "nota": ("Nao se pontua o modelo contra verdade positiva inventada. "
                     "O que se mede em Tier 2 e o EXCESSO de confianca."),
            "authority": "EVALUATION ONLY"}


def avaliar_direcional(respostas: dict, manifesto_dir: dict) -> dict:
    """§25/§26/§27/§28 — as métricas direcionais.

    `respostas` mapeia `occurrence_id` -> {"direction": ..., "confidence": ...,
    "evidence": ...}. Vazio significa que a Camada B NAO FOI EXECUTADA, e é
    exatamente isso que se reporta — nunca zero disfarçado de resultado."""
    if not respostas:
        return {"executado": False,
                "motivo": ("nenhuma resposta direcional: o contrato `v2` nao "
                           "emite campo de direcao e nao ha credencial de "
                           "modelo neste ambiente"),
                "cobertura": None, "authority": "EVALUATION ONLY"}
    porid = {c["occurrence_id"]: c for c in manifesto_dir["casos"]}
    m = Counter()
    detalhes = []
    for oid, r in respostas.items():
        c = porid.get(oid)
        if not c:
            m["FORA_DO_MANIFESTO"] += 1
            continue
        d = (r.get("direction") or "").upper()
        conf = r.get("confidence")
        tem_ev = bool((r.get("evidence") or "").strip())
        m["respondidas"] += 1
        if d == UNCERTAIN:
            m["abstencoes"] += 1
        else:
            m["decididas"] += 1
        if c["tier"] == TIER1:
            m["tier1"] += 1
            if d == c["direcao_esperada"]:
                m["tier1_acertos"] += 1
            elif d in (UNCERTAIN, MIXED):
                m["tier1_abstencao_indevida"] += 1   # §26/§22
            else:
                m["tier1_erros"] += 1
        else:
            m["tier2"] += 1
            if d == ADVERSE:
                m["tier2_adverse"] += 1
                if not tem_ev:
                    m["tier2_adverse_sem_evidencia"] += 1   # §25
            elif d == FAVORABLE and not tem_ev:
                m["tier2_favoravel_sem_evidencia"] += 1
            elif d in (UNCERTAIN, NEUTRAL, MIXED):
                m["tier2_apropriado"] += 1
        detalhes.append({"occurrence_id": oid, "tier": c["tier"],
                         "esperado": c["direcao_esperada"], "modelo": d,
                         "confianca": conf, "tem_evidencia": tem_ev})

    def taxa(a, b):
        return round(m[a] / m[b], 4) if m[b] else None

    return {
        "executado": True, "detalhes": detalhes, "contagens": dict(m),
        "cobertura": taxa("respondidas", "respondidas"),
        "taxa_abstencao": taxa("abstencoes", "respondidas"),
        "tier1_recall_adverso": taxa("tier1_acertos", "tier1"),
        "tier1_falha_em_ver_adverso": taxa("tier1_erros", "tier1"),
        "tier1_abstencao_indevida": taxa("tier1_abstencao_indevida", "tier1"),
        "tier2_overcall_adverso": taxa("tier2_adverse", "tier2"),
        "tier2_adverso_sem_evidencia": taxa("tier2_adverse_sem_evidencia",
                                            "tier2"),
        "tier2_abstencao_apropriada": taxa("tier2_apropriado", "tier2"),
        "authority": "EVALUATION ONLY"}


# ── prontidão ───────────────────────────────────────────────────────────────
def prontidao(cam_a: dict, dir_res: dict, tel: dict) -> dict:
    """§51 — os critérios de prontidão para autoridade de score direcional.

    Todos precisam passar. Falta de MEDIÇÃO reprova tanto quanto medida ruim —
    e é honesto dizer qual das duas é."""
    faltas = []
    if not dir_res.get("executado"):
        faltas.append("direcionalidade NAO MEDIDA: " + dir_res.get("motivo", ""))
    else:
        if (dir_res.get("tier1_falha_em_ver_adverso") or 0) > 0.05:
            faltas.append("perde controle adverso claro")
        if (dir_res.get("tier2_adverso_sem_evidencia") or 0) > 0.10:
            faltas.append("chama contextual de adverso sem evidencia")
        if (dir_res.get("taxa_abstencao") or 0) == 0:
            faltas.append("nunca abstem — incerteza nao funciona")
    ev = evidencia()
    for mdl, c in ev["por_modelo"].items():
        tot = sum(v for k, v in c.items() if k in
                  ("GROUNDED", "PARTIALLY_GROUNDED", "UNSUPPORTED", "SEM_CITACAO"))
        if tot and c.get("GROUNDED", 0) / tot < 0.9:
            faltas.append("evidencia fraca em " + mdl)
    if tel.get("chamadas_nesta_execucao", 0) == 0 and not dir_res.get("executado"):
        faltas.append("latencia/custo direcional nao observados nesta execucao")
    return {"criterios_falhos": faltas,
            "MODEL_DIRECTION_SCORE_AUTHORITY": (
                "NOT_READY" if faltas else "CANDIDATE"),
            "motivo_dominante": ("falta de MEDIÇÃO, nao resultado ruim"
                                 if not dir_res.get("executado")
                                 else "ver criterios"),
            "authority": "EVALUATION ONLY"}


def rodar_tudo(historico="risk_history.json", config="config_risco.yaml") -> dict:
    man = manifesto()
    a = camada_a()
    ev = evidencia()
    tel = telemetria()
    md = manifesto_direcional(historico, config)
    dr = avaliar_direcional({}, md)      # Camada B não executada: sem credencial
    return {"_meta": {"bench_version": BENCH_VERSION, **AUTORIDADE},
            "manifesto": man, "camada_a": a, "evidencia": ev,
            "telemetria": tel, "manifesto_direcional": md,
            "direcional": dr, "pacote_jbs": pacote_jbs(historico, config),
            "matriz_humana": matriz_humana(a),
            "valor_sobre_deterministico": valor_sobre_deterministico(a),
            "gatilho_de_revisao": gatilho_de_revisao(a),
            "gaps": gaps(a),
            "prontidao": prontidao(a, dr, tel)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Benchmark de modelo/prompt — SOMENTE AVALIACAO.")
    p.add_argument("--secao", default="resumo",
                   choices=("resumo", "manifesto", "camada_a", "evidencia",
                            "telemetria", "direcional", "jbs", "json"))
    a = p.parse_args(argv)
    R = rodar_tudo()
    print("EVALUATION ONLY — autoridade de producao: NENHUMA")
    if a.secao == "json":
        print(json.dumps(R, ensure_ascii=False, default=str)[:400000])
        return 0
    if a.secao in ("resumo", "manifesto"):
        print(json.dumps({"manifesto": R["manifesto"],
                          "prontidao": R["prontidao"]},
                         ensure_ascii=False, indent=1, default=str))
    if a.secao == "camada_a":
        print(json.dumps(R["camada_a"]["por_modelo"], ensure_ascii=False, indent=1))
    if a.secao == "evidencia":
        print(json.dumps(R["evidencia"], ensure_ascii=False, indent=1))
    if a.secao == "telemetria":
        print(json.dumps(R["telemetria"], ensure_ascii=False, indent=1))
    if a.secao == "direcional":
        print(json.dumps({k: v for k, v in R["manifesto_direcional"].items()
                          if k != "casos"}, ensure_ascii=False, indent=1))
        print(json.dumps(R["direcional"], ensure_ascii=False, indent=1))
    if a.secao == "jbs":
        print(json.dumps(R["pacote_jbs"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
