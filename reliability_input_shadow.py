#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_input_shadow.py — 4I.2 R7c-P.

COLETA PROSPECTIVA DO INPUT, EM PRODUÇÃO, SEM TOCAR EM NADA DE PRODUÇÃO.

A R7c mediu localmente o que a camada de input consegue. Esta é a publicação:
a partir daqui, cada run real acumula, num sidecar separado, o artigo com o
melhor texto disponível — inclusive os artigos ATRIBUÍDOS SEM CANDIDATO, que
hoje o pipeline descarta antes do history (74,7% dos atribuídos, medido em dois
runs independentes). Sem esse acúmulo, qualquer avaliação futura continuaria
limitada a manchetes e à parte do mundo que a taxonomia já sabe nomear.

O QUE ESTE MÓDULO NÃO FAZ, e é verificado por teste, não prometido: não
classifica, não atribui evento, não pontua, não escreve em `risk_history.json`,
não é lido por `risk_dashboard.py`, não chama LLM. O sidecar
`risk_input_shadow.json` não aparece em nenhum caminho de leitura de produção.

PROSPECTIVO DE VERDADE. A R6f custou uma correção pública por um off-by-one
exatamente aqui: o marco é gravado no MESMO run em que a primeira coleta
acontece, então `run >= marco` é prospectivo e `run < marco` é estoque. E a
ausência de carimbo significa ESTOQUE, nunca novidade — foi assim que 18
candidatos antigos apareceram como out-of-sample.

FAIL-OPEN, MAS NÃO SILENCIOSO. Qualquer falha aqui é registrada e devolve
código 0: o dashboard já foi publicado quando este passo roda. Esconder o erro
seria pior que falhar.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import time
import traceback
from pathlib import Path

import risk_dashboard as rd
import reliability_input_layer as il
import reliability_input_rehearsal as rh

SHADOW_VERSION = "r7cp.1"
SCHEMA_VERSION = "r7cp.s1"

SIDECAR = Path(os.environ.get("RELIABILITY_INPUT_SIDECAR",
                              "risk_input_shadow.json"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7c"))

MAX_FETCH_POR_RUN = 80          # R7c-P2 — elevado de 40 apos autorizacao explicita
MAX_EMISSORES_POR_RUN = 14
PAUSA_ENTRE_QUERIES = 1.0
MAX_ARTIGOS_PERSISTIDOS = 2000  # §9 — teto medido: ~5,5 MB no estado estacionario

# Orcamento SEPARADO do teto estrutural. 80 limita o TOTAL de requisicoes do
# run; este limita quanto da FILA ANTIGA pode ser retomado numa unica execucao.
# Existe porque uma fila de 184 retomaveis consumiu os 8 minutos inteiros do
# passo e ele foi cortado antes de gravar o sidecar — gastou o envelope e nao
# acumulou nada. Nao e um novo cap total: e a fatia maxima do backlog.
MAX_RETRY_BACKLOG_POR_RUN = 25

MARCO = "r7cp_publicado_no_run"

# ESTADOS RETOMÁVEIS. A distinção não é cosmética: define o que um run seguinte
# tenta de novo e o que aceita como resposta final.
#   CAP_REACHED       nem chegou a ser tentado — é fila, não resultado.
#   RESOLUTION_FAILED a resolução do wrapper depende de rede; falhou uma vez,
#                     pode funcionar depois. Foi este o estado em que o bug de
#                     degradação deixou 183 registros: sem retomada, ficariam
#                     presos para sempre num erro que era nosso, não do site.
#   TIMEOUT / 429     transitórios por definição.
# ROBOTS_BLOCKED e HTTP_403 NÃO entram: são decisão do publisher, e insistir a
# cada run seria pouco educado sem chance de resultado diferente.
# EMPTY / THIN / DIRTY também não: o site entregou o que tinha.
RETOMAVEIS = frozenset({rh.CAP_REACHED, rh.RESOLUTION_FAILED, rh.TIMEOUT,
                        rh.HTTP_429, None, ""})


def carregar() -> dict:
    if SIDECAR.exists():
        try:
            d = json.load(io.open(SIDECAR, encoding="utf-8"))
            if isinstance(d, dict) and "articles" in d:
                return d
        except Exception:
            pass
    return {"schema_version": SCHEMA_VERSION, "shadow_version": SHADOW_VERSION,
            "articles": {}, "runs": []}


def gravar(side: dict) -> None:
    """Escrita atômica: uma falha no meio não pode corromper o acúmulo de
    runs anteriores — o sidecar é a única memória entre runners efêmeros."""
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIDECAR.with_suffix(SIDECAR.suffix + ".tmp")
    io.open(tmp, "w", encoding="utf-8").write(
        json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True,
                   default=str))
    os.replace(tmp, SIDECAR)


# Componentes que ficam INTEIROS no estágio final — é sobre ele que qualquer
# análise futura decide. Dos estágios anteriores guarda-se só o suficiente para
# medir o ganho marginal de cada degrau da escada.
_RESUMO_ESTAGIO = ("useful_chars", "sentence_like_count",
                   "meaningful_gain_vs_title", "input_ready_under_r7c_policy")


def enxugar(reg: dict) -> dict:
    """Registro enxuto — §9.

    Medido antes de publicar: 3,7 KB por artigo dava 14,8 MB no teto, e o
    arquivo inteiro é reescrito e commitado quatro vezes por dia. Três blocos
    de componentes quase idênticos (`r0_legacy`, `r0_extended`, `final`)
    respondiam por 1,4 KB disso. Guardar o estágio final completo e um resumo
    dos anteriores preserva tudo o que o §11 exige — first_seen, empresas, mapa
    de candidatos, procedência, qualidade, enrichment, versões — e o ganho
    marginal por degrau continua calculável."""
    out = dict(reg)
    for estagio in ("r0_legacy", "r0_extended"):
        c = out.get(estagio) or {}
        out[estagio] = {k: c[k] for k in _RESUMO_ESTAGIO if k in c}
        if estagio == "r0_extended" and c.get("metodo"):
            out[estagio]["metodo"] = c["metodo"]
    f = out.get("final") or {}
    out["final"] = {k: v for k, v in f.items() if k != "faltou"} or f
    out["final"]["faltou"] = (f.get("faltou") or [])[:3]
    enr = out.get("enrichment") or {}
    out["enrichment"] = {k: enr[k] for k in
                         ("origem", "falha", "tier", "metodo",
                          "metodo_resolucao") if k in enr}
    return out


def classificar_procedencia(reg: dict, marco: int | None) -> str:
    """ESTOQUE × PROSPECTIVO, com a lição de R6f aplicada.

    Sem carimbo → ESTOQUE. O artigo que não passou por aqui não pode ser
    tratado como observação nova só porque não sabemos quando entrou."""
    run = reg.get("first_seen_run")
    if marco is None or run is None:
        return "STOCK"
    return "PROSPECTIVE" if run >= marco else "STOCK"


def coletar(cfg: dict, *, run_count: int, max_emissores: int = MAX_EMISSORES_POR_RUN,
            max_fetch: int = MAX_FETCH_POR_RUN,
            permitir_rede: bool = True) -> dict:
    """Um ciclo prospectivo: coleta, atribui, mede input, enriquece uma vez por
    artigo, persiste. Reusa coleta e atribuição de produção — não reimplementa."""
    import requests

    side = carregar()
    side.setdefault(MARCO, run_count)      # marco gravado no PRIMEIRO run
    marco = side[MARCO]
    hist = {}
    if HISTORY.exists():
        try:
            hist = json.load(io.open(HISTORY, encoding="utf-8"))
        except Exception:
            hist = {}
    watch = cfg.get("watchlist", [])
    tax = cfg.get("taxonomy", [])
    S = requests.Session()
    # O teto viaja no contador — e o unico lugar em que ele e lido. Ate a
    # R7c-P este parametro nao chegava a lugar nenhum e o valor efetivo era a
    # constante do modulo de rehearsal.
    contador = {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {},
                "resolucoes": 0, "limite_fetch": max_fetch}
    tel = collections.Counter()
    vistos = set()
    brutos = []

    for comp in watch[:max_emissores]:
        try:
            queries = rd.build_company_queries(comp, cfg)
        except Exception:
            queries = [comp.get("name") or ""]
        for q in (queries or [])[:1]:
            try:
                arts = rd.fetch_query(q, cfg, S)
            except Exception as exc:
                tel[f"erro_query:{type(exc).__name__}"] += 1
                continue
            tel["bruto"] += len(arts or [])
            for art in (arts or []):
                url = art.get("url") or ""
                if not url or url in vistos:
                    continue
                vistos.add(url)
                try:
                    comps = rd.detect_companies(art, watch)
                except Exception:
                    comps = []
                if not comps:
                    tel["sem_empresa"] += 1
                    continue
                tel["atribuido"] += 1
                try:
                    evs = [e.get("id") for e in (rd.classify_article(art, tax) or [])]
                except Exception:
                    evs = []
                tel["com_candidato" if evs else "sem_candidato"] += 1
                brutos.append({"art": art, "comps": comps, "evs": evs,
                               "query_kind": "company_query"})
            time.sleep(PAUSA_ENTRE_QUERIES)

    # §15 — prioridade simples, determinística e taxonomy-neutral: ordem de
    # primeira aparição, alternando entre com e sem candidato para que o teto
    # não consuma o orçamento só nos artigos que a taxonomia já reconhece.
    com = [b for b in brutos if b["evs"]]
    sem = [b for b in brutos if not b["evs"]]
    ordem = []
    while com or sem:
        if sem:
            ordem.append(sem.pop(0))
        if com:
            ordem.append(com.pop(0))

    # ── R7c-P4: o backlog não pode monopolizar o run ────────────────────────
    # O run 31627960181 estourou os 8 minutos porque 184 registros ficaram
    # retomáveis de uma vez e o passo foi cortado ANTES de gravar o sidecar —
    # ou seja, gastou o envelope inteiro e não acumulou nada. O teto estrutural
    # de 80 requisições não protege contra isso: ele limita o TOTAL, não a
    # fatia que a fila antiga consome.
    #
    # Duas listas, dois orçamentos. Artigo novo deste run tem precedência
    # absoluta — é a coleta prospectiva, a razão de existir da camada. O que
    # sobra do teto vai para a fila antiga, e ainda assim no máximo
    # MAX_RETRY_BACKLOG_POR_RUN por execução, para a fila drenar ao longo de
    # vários crons em vez de tomar um run inteiro.
    conhecidos = side["articles"] or {}
    novos_b, backlog_b = [], []
    for b in ordem:
        art_id = il.identidade(b["art"].get("url") or "")
        reg = conhecidos.get(art_id)
        if reg is None:
            novos_b.append(b)
        elif reg.get("falha") in RETOMAVEIS:
            backlog_b.append((reg, b))
        else:
            novos_b.append(b)          # não retomável: só reaproveita, sem rede

    # Ordem justa e determinística: primeiro quem foi visto há mais tempo.
    # Sem isso o mesmo lote seria retomado a cada run e o resto passaria fome.
    backlog_b.sort(key=lambda rb: ((rb[0].get("last_seen_run") or 0),
                                   (rb[0].get("first_seen_run") or 0),
                                   rb[0].get("article_id") or ""))
    backlog_total = len(backlog_b)
    backlog_sel = [b for _r, b in backlog_b[:MAX_RETRY_BACKLOG_POR_RUN]]
    backlog_adiado = [b for _r, b in backlog_b[MAX_RETRY_BACKLOG_POR_RUN:]]
    # Os adiados entram na lista sem direito a rede: preservam o registro e
    # atualizam `last_seen`, exatamente como qualquer não-retomável.
    ordem = novos_b + backlog_sel + backlog_adiado
    adiados_ids = {il.identidade(b["art"].get("url") or "") for b in backlog_adiado}

    novos = prospectivos = reaproveitados = 0
    regs = []
    for b in ordem:
        art, comps, evs = b["art"], b["comps"], b["evs"]
        url = art.get("url") or ""
        art_id = il.identidade(url)
        antes = (side["articles"] or {}).get(art_id)
        primeiro = antes.get("first_seen_run") if antes else run_count
        if not antes:
            novos += 1
        # O QUE JÁ FOI RESOLVIDO NÃO SE REDERIVA. Um artigo já presente no
        # sidecar era reprocessado com a rede desligada — e o resultado
        # degradado SOBRESCREVIA o bom. Medido no run 113: 176 registros
        # rebaixados e 8 que estavam `input_ready` deixaram de estar, porque a
        # resolução do wrapper do Google News não acontece offline e virava
        # RESOLUTION_FAILED. O acúmulo, que é a razão de existir desta camada,
        # se destruía a cada run.
        #
        # Reprocessa-se apenas o que está em estado RETOMÁVEL (ver acima) E
        # coube no orçamento de backlog deste run. Resultado já obtido — bom ou
        # ruim — não é refeito; fila antiga adiada é preservada intacta.
        if antes and (antes.get("falha") not in RETOMAVEIS
                      or art_id in adiados_ids):
            reaproveitados += 1
            antes["last_seen_run"] = run_count
            antes["procedencia"] = classificar_procedencia(antes, marco)
            side["articles"][art_id] = antes
            regs.append(antes)
            continue

        reg = rh.processar_artigo(
            url=url, titulo=art.get("title") or "",
            resumo=art.get("summary") or "", dominio=art.get("domain") or "",
            pub_iso=art.get("pub_iso") or "",
            empresas={c: list(evs) for c in comps}, ricos=None, rec=art,
            sidecar={}, permitir_rede=permitir_rede,
            contador=contador, politica="SELECTED",
            query_kind=b["query_kind"], fonte="company_query")
        reg.pop("_best_input", None)
        reg = enxugar(reg)
        reg.update({
            "first_seen_run": primeiro, "last_seen_run": run_count,
            "shadow_version": SHADOW_VERSION,
            "schema_version": SCHEMA_VERSION,
            "url_resolvida": (reg.get("enrichment") or {}).get("url_resolvida", ""),
        })
        reg["procedencia"] = classificar_procedencia(reg, marco)
        prospectivos += 1 if reg["procedencia"] == "PROSPECTIVE" else 0
        side["articles"][art_id] = reg
        regs.append(reg)

    # §9 — teto de crescimento: mantém os mais recentes.
    if len(side["articles"]) > MAX_ARTIGOS_PERSISTIDOS:
        mantidos = sorted(side["articles"].items(),
                          key=lambda kv: (kv[1].get("last_seen_run") or 0,
                                          kv[1].get("first_seen_run") or 0),
                          reverse=True)[:MAX_ARTIGOS_PERSISTIDOS]
        side["articles"] = dict(mantidos)

    funil = rh.funil(regs, contador) if regs else {}
    resumo = {"run_count": run_count, "marco": marco,
              "gerado_em": int(time.time()),
              "telemetria_coleta": dict(tel),
              "artigos_no_run": len(regs), "novos": novos,
              "reaproveitados": reaproveitados,
              "backlog_total": backlog_total,
              "backlog_selecionado": len(backlog_sel),
              "backlog_adiado": len(backlog_adiado),
              "backlog_budget": MAX_RETRY_BACKLOG_POR_RUN,
              "cap_estrutural": max_fetch,
              "prospectivos": prospectivos,
              "funil": funil}
    side["runs"] = (side.get("runs") or [])[-19:] + [resumo]
    side["shadow_version"] = SHADOW_VERSION
    side["schema_version"] = SCHEMA_VERSION
    gravar(side)
    return {"resumo": resumo, "registros": regs, "sidecar": str(SIDECAR),
            "total_persistido": len(side["articles"])}


def relatorio(side: dict | None = None) -> dict:
    """§31 — observabilidade do acúmulo. CLI, nunca UI."""
    side = side or carregar()
    arts = list((side.get("articles") or {}).values())
    marco = side.get(MARCO)
    proc = collections.Counter(classificar_procedencia(a, marco) for a in arts)
    pron = sum(1 for a in arts
               if (a.get("final") or {}).get("input_ready_under_r7c_policy"))
    com = sum(1 for a in arts if a.get("tem_algum_candidato"))
    return {
        "sidecar": str(SIDECAR), "marco": marco,
        "artigos": len(arts), "procedencia": dict(proc),
        "input_ready": pron,
        "com_candidato": com, "sem_candidato": len(arts) - com,
        "falhas": dict(collections.Counter(a.get("falha") for a in arts)),
        "dominios": len({a.get("dominio") for a in arts}),
        "query_kinds": dict(collections.Counter(a.get("query_kind") for a in arts)),
        "runs_registrados": len(side.get("runs") or []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prospective", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--max-emissores", type=int, default=MAX_EMISSORES_POR_RUN)
    ap.add_argument("--max-fetch", type=int, default=MAX_FETCH_POR_RUN)
    ap.add_argument("--sem-rede", action="store_true")
    a = ap.parse_args()

    if a.report:
        print(json.dumps(relatorio(), ensure_ascii=False, indent=2))
        return 0
    if not a.prospective:
        print("nada a fazer: use --prospective ou --report")
        return 0

    # FAIL-OPEN, NÃO SILENCIOSO. O dashboard já foi publicado quando este passo
    # roda; uma falha aqui não pode derrubar a execução, mas tem que aparecer.
    try:
        cfg = rd.load_config("config_risco.yaml")
        run_count = 0
        try:
            run_count = int(json.load(io.open("run_meta.json",
                                              encoding="utf-8")).get("run_count") or 0)
        except Exception:
            pass
        r = coletar(cfg, run_count=run_count, max_emissores=a.max_emissores,
                    max_fetch=a.max_fetch, permitir_rede=not a.sem_rede)
        res, f = r["resumo"], r["resumo"]["funil"]
        print(f"   📥 input shadow · run {run_count} · marco {res['marco']}")
        print(f"      coleta: {res['telemetria_coleta']}")
        print(f"      backlog {res.get('backlog_selecionado', 0)}"
              f"/{res.get('backlog_total', 0)} "
              f"(budget {res.get('backlog_budget')}, adiados "
              f"{res.get('backlog_adiado', 0)}) · cap {res.get('cap_estrutural')}")
        print(f"      artigos {res['artigos_no_run']} · novos {res['novos']} "
              f"· reaproveitados {res.get('reaproveitados', 0)} "
              f"· prospectivos {res['prospectivos']} "
              f"· persistidos {r['total_persistido']}")
        if f:
            print(f"      ready {f.get('final_ready', 0)}/{f.get('artigos_unicos', 0)} "
                  f"· fetches {f.get('network_fetches', 0)} "
                  f"· evitadas {f.get('requests_evitadas_por_dedup', 0)} "
                  f"· resolucoes {f.get('resolucoes', 0)}")
            print(f"      falhas: {f.get('falhas', {})}")
        OUTDIR.mkdir(parents=True, exist_ok=True)
        io.open(OUTDIR / "input_shadow_last_run.json", "w",
                encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True,
                       default=str))
    except Exception:
        print("   ⚠️  input shadow FALHOU — produção não é afetada:")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
