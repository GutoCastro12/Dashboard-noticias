#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_prospective_report.py — o holdout prospectivo, contado uma vez só.

POR QUE ESTE MÓDULO EXISTE

As métricas do experimento prospectivo vinham sendo recontadas à mão a cada
wave, em scripts avulsos. Isso já produziu um erro real e caro: uma contagem
chamou `projetar_pontuavel_v2` SEM `event_id`, o que desativa em silêncio a
porta OBJETO — exatamente a dimensão que o Contract V2 foi criado para
acrescentar. O número saiu plausível, foi apresentado como correção de um
número que estava certo, e virou baseline oficial por três waves.

Conta feita à mão não tem denominador declarado nem teste. Este módulo tem os
dois, e é a única fonte das métricas daqui em diante.

O QUE ELE DERIVA, E DE ONDE

Tudo sai dos artefatos oficiais — sidecar do shadow, verdade humana gravada
pelo writer, snapshot determinístico contemporâneo, saídas persistidas dos
modelos e a reavaliação de evidência sob o validador vigente. Nada é fixado
no código: nem N, nem nomes de empresa, nem lista de modelos, nem acurácia.
Os valores de hoje são asserção de teste, não constante de lógica.

DENOMINADORES SÃO O PONTO INTEIRO

Cada métrica declara o seu. Uma dimensão só entra no denominador quando a
verdade humana ADJUDICOU aquela dimensão — ausência não é erro do modelo, é
ausência de verdade. Contar ausência como erro inventaria acurácia para baixo
tão levianamente quanto ignorar erro a inventaria para cima.

O QUE ELE NÃO É

Derivado e somente-leitura. Não escreve verdade humana, não toca o sidecar,
não altera histórico, score, dashboard ou contrato, e não chama modelo.
Produção não o importa — a direção da dependência é só esta:
relatório lê avaliação.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import bench_semantic_eval as be
import reliability_evidence_reeval as rr
import reliability_human_review_writer as hrw
import reliability_pilot_validators as pv
import semantic_v2_shadow as sh

REPORT_VERSION = "r7ba.report.v1"

# Critério formal de decisão. Enquanto não for atingido, o relatório devolve
# vencedor NENHUM — por mais tentadora que a diferença pareça com N pequeno.
N_MINIMO_PARA_DECISAO = 25
N_PREFERIDO = (40, 50)

DIMENSOES = ("event_asserted", "subject", "company_role", "relation",
             "currentness", "occurrence_novelty", "phase", "centrality",
             "transaction_object")

AVALIADOR_DETERMINISTICO = "deterministico"


# ── leitura da verdade humana ───────────────────────────────────────────────
def review_efetiva(obs: dict) -> dict:
    """A adjudicação em vigor. Override é versionado: a atual é a que vale, e
    a anterior fica em `override_de` para auditoria — nunca some."""
    return obs.get("human_review") or {}


def humano_pontuavel(review: dict):
    """Pontuabilidade semântica segundo o humano.

    As duas primeiras adjudicações gravaram isso com nomes diferentes
    (`scoreable_as_ma`, `scoreable_as_troca_ceo`), antes de existir writer
    oficial. Ler por prefixo aceita as duas sem reescrever verdade humana
    já registrada — e `scoreable` cobre o formato canônico.
    """
    if "scoreable" in review:
        return review["scoreable"]
    for k, v in review.items():
        if k.startswith("scoreable_as_"):
            return v
    return None


def dimensoes_humanas(review: dict) -> dict:
    return {k: v for k, v in (review.get("dimensoes_adjudicadas") or {}).items()
            if v not in (None, "")}


# ── agrupamento em casos ────────────────────────────────────────────────────
def agrupar_casos(dados: dict) -> tuple:
    """Casos artigo×empresa e erros de integridade encontrados no caminho."""
    casos, erros = {}, []
    for chave, o in sorted((dados.get("observacoes") or {}).items()):
        ident = (o.get("article_id"), o.get("company"), o.get("candidate_event"))
        casos.setdefault(ident, []).append((chave, o))
    for ident, regs in casos.items():
        modelos = [o.get("actual_model") for _, o in regs]
        dup = {m for m in modelos if modelos.count(m) > 1}
        if dup:
            erros.append({"tipo": "REGISTRO_DUPLICADO", "caso": list(ident),
                          "modelos": sorted(dup)})
        verdades = {json.dumps(review_efetiva(o), sort_keys=True,
                               ensure_ascii=False) for _, o in regs}
        if len(verdades) > 1:
            erros.append({"tipo": "VERDADE_DIVERGENTE_ENTRE_MODELOS",
                          "caso": list(ident)})
        for _, o in regs:
            for campo, valor in dimensoes_humanas(review_efetiva(o)).items():
                if campo in hrw.ENUMS and valor not in hrw.ENUMS[campo]:
                    erros.append({"tipo": "ENUM_HUMANO_INVALIDO",
                                  "caso": list(ident),
                                  "campo": campo, "valor": valor})
    return casos, erros


def eleg_prospectiva(obs: dict, historico: dict, desenv: set) -> tuple:
    """Prospectivo = capturado pós-freeze e fora do corpus de desenvolvimento.
    Delegado a `semantic_v2_shadow.elegivel`, que é a autoridade."""
    art = (historico.get("articles") or {}).get(obs.get("url") or "")
    if art is None:
        return False, "artigo ausente do histórico — não dá para provar"
    return sh.elegivel(art, desenv)


# ── projeções ───────────────────────────────────────────────────────────────
def projecao_do_modelo(obs: dict):
    """Projeção final do modelo, invocada COMO FOI DESENHADA.

    `event_id` não é opcional na prática: a porta OBJETO de `projetar_pontuavel_v2`
    só é alcançável quando ele é passado, e é ela que carrega metade do que o
    Contract V2 acrescentou. Omitir o argumento não produz uma medição mais
    conservadora — produz uma medição de outra coisa. Todos os callers do
    projeto passam; este também.
    """
    eventos = (obs.get("saida") or {}).get("events") or []
    if not eventos:
        return None, {"pontuavel": None, "porta": "SEM_EVENTO"}
    ev = eventos[0]
    proj = be.projetar_pontuavel_v2(ev, obs.get("company") or "", None,
                                    obs.get("candidate_event") or "")
    return ev, proj


# ── relatório ───────────────────────────────────────────────────────────────
def gerar(sidecar: Path | None = None,
          historico_path: str = "risk_history.json") -> dict:
    caminho = sidecar or sh.CAMINHO
    dados = sh.carregar(caminho)
    historico = json.load(io.open(historico_path, encoding="utf-8"))
    desenv = sh.corpus_de_desenvolvimento()
    casos, integridade = agrupar_casos(dados)
    reeval = rr.reavaliar_shadow(dados, historico)
    reeval_por_chave = {}
    for r in reeval["registros"]:
        reeval_por_chave.setdefault(r["chave"], r)

    modelos = sorted({o.get("actual_model") for o in
                      (dados.get("observacoes") or {}).values()
                      if o.get("actual_model")})
    avaliadores = [AVALIADOR_DETERMINISTICO] + modelos

    detalhes, excluidos = [], []
    fila_revisao, fila_divergencia = [], []
    final = {a: {"acertos": 0, "denominador": 0} for a in avaliadores}
    dim_metric = {m: {d: {"acertos": 0, "denominador": 0} for d in DIMENSOES}
                  for m in modelos}
    ev_raw = {"validos": 0, "total": 0}
    ev_q2 = {"validos": 0, "total": 0}

    for ident, regs in sorted(casos.items(), key=lambda x: str(x[0])):
        artigo_id, empresa, candidato = ident
        primeiro = regs[0][1]
        ok_prosp, motivo_prosp = eleg_prospectiva(primeiro, historico, desenv)
        review = review_efetiva(primeiro)
        hum_pont = humano_pontuavel(review)
        dims_h = dimensoes_humanas(review)
        revisado = bool(review)

        modelos_do_caso = {}
        for chave, o in regs:
            ev, proj = projecao_do_modelo(o)
            reg_reeval = reeval_por_chave.get(chave) or {}
            bruto = (o.get("evidencia") or {})
            valido_bruto = bool(bruto.get("aceitos"))
            valido_q2 = bool(reg_reeval.get("valido_v2"))
            ev_raw["total"] += 1
            ev_q2["total"] += 1
            ev_raw["validos"] += int(valido_bruto)
            ev_q2["validos"] += int(valido_q2)
            modelos_do_caso[o.get("actual_model")] = {
                "projecao_final": proj.get("pontuavel"),
                "porta": proj.get("porta"),
                "dimensoes": {d: (ev or {}).get(d) for d in DIMENSOES},
                "evidencia_bruta": "VALIDA" if valido_bruto else "INVALIDA",
                "marcas_brutas": sorted({
                    m for e in (bruto.get("eventos") or [])
                    for m in ((e.get("_validacao") or {}).get("marcas") or [])}),
                "evidencia_q2": "VALIDA" if valido_q2 else "INVALIDA",
                "reavaliada": bool(reg_reeval.get("mudancas")),
            }

        det_pont = (primeiro.get("deterministic") or {}).get("scoreable")

        if not ok_prosp:
            excluidos.append({"empresa": empresa, "candidato": candidato,
                              "motivo": motivo_prosp,
                              "titulo": primeiro.get("title")})
        elif revisado and hum_pont is not None:
            final[AVALIADOR_DETERMINISTICO]["denominador"] += 1
            final[AVALIADOR_DETERMINISTICO]["acertos"] += int(det_pont == hum_pont)
            for m, r in modelos_do_caso.items():
                final[m]["denominador"] += 1
                final[m]["acertos"] += int(r["projecao_final"] == hum_pont)
            for m, r in modelos_do_caso.items():
                for d, verdade in dims_h.items():
                    if d not in DIMENSOES:
                        continue
                    dim_metric[m][d]["denominador"] += 1
                    dim_metric[m][d]["acertos"] += int(r["dimensoes"].get(d)
                                                       == verdade)

        if not revisado:
            fila_revisao.append({"empresa": empresa, "candidato": candidato,
                                 "titulo": primeiro.get("title"),
                                 "prospectivo": ok_prosp})
        finais = {m: r["projecao_final"] for m, r in modelos_do_caso.items()}
        motivos = []
        if len(set(finais.values())) > 1:
            motivos.append("modelos discordam entre si")
        if any(v != det_pont for v in finais.values()):
            motivos.append("modelo discorda do determinístico")
        novidades = {r["dimensoes"].get("occurrence_novelty")
                     for r in modelos_do_caso.values()}
        if len(novidades) > 1:
            motivos.append("discordância em occurrence_novelty")
        objetos = {r["dimensoes"].get("transaction_object")
                   for r in modelos_do_caso.values()}
        if len(objetos) > 1:
            motivos.append("discordância em transaction_object")
        if not revisado:
            motivos.append("sem verdade humana")
        if motivos:
            fila_divergencia.append({
                "empresa": empresa, "candidato": candidato,
                "titulo": primeiro.get("title"), "motivos": motivos,
                "prioridade": (0 if not revisado else 1)})

        detalhes.append({
            "artigo_id": artigo_id, "empresa": empresa,
            "candidato": candidato, "titulo": primeiro.get("title"),
            "fonte": primeiro.get("source"),
            "capturado_em": primeiro.get("first_seen_iso"),
            "publicado_em": primeiro.get("article_pub_iso"),
            "contract_version": primeiro.get("contract_version"),
            "prompt_version": primeiro.get("prompt_version"),
            "prospectivo": ok_prosp, "motivo_prospectivo": motivo_prosp,
            "revisado": revisado,
            "veredito_humano": review.get("verdict"),
            "humano_pontuavel": hum_pont,
            "dimensoes_humanas": dims_h,
            "override": bool(review.get("override_de")),
            "deterministico_pontuavel": det_pont,
            "deterministico_correto": (None if not (revisado and hum_pont is not None)
                                       else det_pont == hum_pont),
            "modelos": modelos_do_caso,
            "modelos_ausentes": sorted(set(modelos) - set(modelos_do_caso)),
        })

    fila_divergencia.sort(key=lambda x: (x["prioridade"], x["empresa"]))
    revisados = sum(1 for d in detalhes if d["revisado"])
    overrides = sum(1 for d in detalhes if d["override"])

    def cobertura(campo):
        c = {}
        for d in detalhes:
            c.setdefault(d.get(campo) or "(sem)", {"observados": 0,
                                                   "revisados": 0})
            c[d.get(campo) or "(sem)"]["observados"] += 1
            c[d.get(campo) or "(sem)"]["revisados"] += int(d["revisado"])
        return c

    severidades = {}
    for _, regs in casos.items():
        s = (regs[0][1].get("deterministic") or {}).get("severity")
        if s is not None:
            severidades[s] = severidades.get(s, 0) + 1

    def taxa(d):
        return (round(d["acertos"] / d["denominador"], 4)
                if d["denominador"] else None)

    bruto_sidecar = Path(caminho).read_bytes()
    return {
        "report_version": REPORT_VERSION,
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fonte": {
            "sidecar": str(caminho),
            "sidecar_sha256": hashlib.sha256(bruto_sidecar).hexdigest(),
            "shadow_version": (dados.get("_meta") or {}).get("shadow_version"),
            "freeze_iso": sh.CONTRACT_FREEZE_ISO,
            "freeze_epoch": sh.CONTRACT_FREEZE_TS,
            "validador_evidencia": pv.QUOTE_VALIDATOR_VERSION,
            "reeval_version": rr.REEVAL_VERSION,
            "eval_version": be.EVAL_VERSION,
            "writer_version": hrw.WRITER_VERSION,
        },
        "escopo_do_contrato": sorted({
            (d["contract_version"], d["prompt_version"]) for d in detalhes
        }) and [{"contract_version": c, "prompt_version": p} for c, p in
                sorted({(d["contract_version"], d["prompt_version"])
                        for d in detalhes})],
        "contagens": {
            "casos_observados": len(casos),
            "casos_revisados": revisados,
            "registros_de_modelo": len(dados.get("observacoes") or {}),
            "casos_excluidos_nao_prospectivos": len(excluidos),
            "overrides": overrides,
            "modelos": modelos,
        },
        "pontuabilidade_final": {a: {**final[a], "acuracia": taxa(final[a])}
                                 for a in avaliadores},
        "dimensoes": {m: {d: {**dim_metric[m][d],
                              "acuracia": taxa(dim_metric[m][d])}
                          for d in DIMENSOES} for m in modelos},
        "evidencia": {
            "bruta": {**ev_raw, "acuracia": taxa({"acertos": ev_raw["validos"],
                                                  "denominador": ev_raw["total"]})},
            "reavaliada_q2": {**ev_q2,
                              "acuracia": taxa({"acertos": ev_q2["validos"],
                                                "denominador": ev_q2["total"]})},
            "invalido_para_valido": reeval["invalido_para_valido"],
            "valido_para_invalido": reeval["valido_para_invalido"],
        },
        "cobertura": {
            "familia_de_evento": cobertura("candidato"),
            "empresa": cobertura("empresa"),
            "fonte": cobertura("fonte"),
            "severidade": severidades or None,
        },
        "fila_de_revisao": fila_revisao,
        "fila_de_divergencia": fila_divergencia,
        "excluidos": excluidos,
        "integridade": integridade,
        "decisao": {
            "vencedor": None,
            "motivo": (f"N revisado ({revisados}) abaixo do mínimo formal "
                       f"({N_MINIMO_PARA_DECISAO})"
                       if revisados < N_MINIMO_PARA_DECISAO
                       else "critério formal atingido; decisão é humana"),
            "amostra_pequena": revisados < N_MINIMO_PARA_DECISAO,
            "n_minimo": N_MINIMO_PARA_DECISAO,
            "n_preferido": list(N_PREFERIDO),
            "progresso": f"{revisados}/{N_MINIMO_PARA_DECISAO}",
        },
        "casos": detalhes,
    }


def markdown(r: dict) -> str:
    c, f = r["contagens"], r["fonte"]
    L = ["# Validação prospectiva do Contract V2", "",
         f"- Gerado em: {r['gerado_em']}",
         f"- Freeze: {f['freeze_iso']} (epoch {f['freeze_epoch']})",
         f"- Contrato: " + ", ".join(f"{e['contract_version']} / "
                                     f"{e['prompt_version']}"
                                     for e in r["escopo_do_contrato"]),
         f"- Sidecar SHA-256: `{f['sidecar_sha256'][:32]}…`",
         f"- Validador de evidência: `{f['validador_evidencia']}`", "",
         f"**Casos observados: {c['casos_observados']}** · "
         f"**revisados: {c['casos_revisados']}** · "
         f"**registros de modelo: {c['registros_de_modelo']}**", ""]
    if r["decisao"]["amostra_pequena"]:
        L += [f"> **AMOSTRA PEQUENA DEMAIS PARA DECISÃO DE ROTEAMENTO** — "
              f"{r['decisao']['progresso']} casos revisados; alvo mínimo "
              f"{r['decisao']['n_minimo']}, preferido "
              f"{r['decisao']['n_preferido'][0]}–{r['decisao']['n_preferido'][1]}. "
              f"Vencedor: **NENHUM**.", ""]
    L += ["## Pontuabilidade semântica final", "",
          "| avaliador | acertos | denominador | acurácia |",
          "|---|---:|---:|---:|"]
    for a, d in r["pontuabilidade_final"].items():
        acc = "—" if d["acuracia"] is None else f"{d['acuracia']:.0%}"
        L.append(f"| {a} | {d['acertos']} | {d['denominador']} | {acc} |")
    L += ["", "*Denominador: casos prospectivos com pontuabilidade humana "
              "adjudicada.*", "", "## Dimensões", "",
          "| modelo | dimensão | acertos | denominador | acurácia |",
          "|---|---|---:|---:|---:|"]
    for m, dims in r["dimensoes"].items():
        for d, v in dims.items():
            if not v["denominador"]:
                continue
            L.append(f"| {m} | {d} | {v['acertos']} | {v['denominador']} | "
                     f"{v['acuracia']:.0%} |")
    L += ["", "*Cada dimensão só conta os casos em que o humano a adjudicou. "
              "Ausência de verdade não é erro do modelo.*", "",
          "## Validade de evidência", "",
          "| leitura | válidas | total |", "|---|---:|---:|",
          f"| como observado (telemetria original) | "
          f"{r['evidencia']['bruta']['validos']} | "
          f"{r['evidencia']['bruta']['total']} |",
          f"| reavaliada sob `{f['validador_evidencia']}` | "
          f"{r['evidencia']['reavaliada_q2']['validos']} | "
          f"{r['evidencia']['reavaliada_q2']['total']} |", "",
          f"Reavaliação: {r['evidencia']['invalido_para_valido']} "
          f"INVÁLIDA→VÁLIDA, {r['evidencia']['valido_para_invalido']} "
          f"VÁLIDA→INVÁLIDA. As duas leituras coexistem; nenhuma sobrescreve "
          f"a outra.", "", "## Casos", ""]
    for d in r["casos"]:
        L += [f"### {d['empresa']} / `{d['candidato']}`", "",
              f"- {d['titulo']}",
              f"- {d['fonte']} · publicado {d['publicado_em']} · capturado "
              f"{d['capturado_em']}",
              f"- Prospectivo: {'sim' if d['prospectivo'] else 'NÃO — ' + d['motivo_prospectivo']}"]
        if d["revisado"]:
            L.append(f"- Humano: **{d['veredito_humano']}** "
                     f"(pontuável={d['humano_pontuavel']})")
            if d["dimensoes_humanas"]:
                L.append("- Dimensões adjudicadas: "
                         + ", ".join(f"`{k}`={v}" for k, v in
                                     sorted(d["dimensoes_humanas"].items())))
        else:
            L.append("- Humano: **sem adjudicação**")
        L += ["", "| avaliador | final | porta | novidade | objeto | "
                  "evidência bruta | evidência q2 |",
              "|---|---|---|---|---|---|---|",
              f"| determinístico | {d['deterministico_pontuavel']} | — | — | "
              f"— | — | — |"]
        for m, v in sorted(d["modelos"].items()):
            L.append(f"| {m} | {v['projecao_final']} | {v['porta']} | "
                     f"{v['dimensoes'].get('occurrence_novelty')} | "
                     f"{v['dimensoes'].get('transaction_object')} | "
                     f"{v['evidencia_bruta']} | {v['evidencia_q2']} |")
        if d["modelos_ausentes"]:
            L.append(f"\nSem resposta de: {', '.join(d['modelos_ausentes'])}")
        L.append("")
    if r["fila_de_revisao"]:
        L += ["## Fila de revisão", ""]
        L += [f"- {q['empresa']} / `{q['candidato']}` — {q['titulo']}"
              for q in r["fila_de_revisao"]] + [""]
    if r["fila_de_divergencia"]:
        L += ["## Fila de divergência", ""]
        L += [f"- {q['empresa']} / `{q['candidato']}`: "
              f"{'; '.join(q['motivos'])}" for q in r["fila_de_divergencia"]]
        L.append("")
    if r["excluidos"]:
        L += ["## Excluídos (não prospectivos)", ""]
        L += [f"- {e['empresa']} / `{e['candidato']}` — {e['motivo']}"
              for e in r["excluidos"]] + [""]
    if r["integridade"]:
        L += ["## Erros de integridade", ""]
        L += [f"- {e}" for e in r["integridade"]] + [""]
    L += ["## Cobertura", "",
          "| família | observados | revisados |", "|---|---:|---:|"]
    for k, v in sorted(r["cobertura"]["familia_de_evento"].items()):
        L.append(f"| `{k}` | {v['observados']} | {v['revisados']} |")
    L += ["", "| empresa | observados | revisados |", "|---|---:|---:|"]
    for k, v in sorted(r["cobertura"]["empresa"].items()):
        L.append(f"| {k} | {v['observados']} | {v['revisados']} |")
    L += ["", "| fonte | observados | revisados |", "|---|---:|---:|"]
    for k, v in sorted(r["cobertura"]["fonte"].items()):
        L.append(f"| {k} | {v['observados']} | {v['revisados']} |")
    if not r["cobertura"]["severidade"]:
        L += ["", "*Severidade não consta no snapshot determinístico; não é "
                  "inferida.*"]
    L += ["", f"**Vencedor: NENHUM** — {r['decisao']['motivo']}.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Relatório derivado do holdout prospectivo (somente leitura).")
    p.add_argument("--sidecar", default=None)
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--json-out", default=None)
    p.add_argument("--md-out", default=None)
    a = p.parse_args(argv)
    r = gerar(Path(a.sidecar) if a.sidecar else None, a.historico)
    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"JSON  -> {a.json_out}")
    if a.md_out:
        Path(a.md_out).parent.mkdir(parents=True, exist_ok=True)
        io.open(a.md_out, "w", encoding="utf-8").write(markdown(r))
        print(f"MD    -> {a.md_out}")
    if not (a.json_out or a.md_out):
        print(markdown(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
