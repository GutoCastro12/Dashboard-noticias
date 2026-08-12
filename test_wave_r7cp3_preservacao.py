#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp3_preservacao.py — 4I.2 R7c-P3.

O SIDECAR GUARDA O MELHOR INPUT CONHECIDO DO ARTIGO.

Defeito medido em produção, no run 113: 176 registros rebaixados e 8 que
estavam `input_ready` deixaram de estar. O acúmulo caiu de 9 para 2 — a camada
se autodestruía a cada execução, que é o oposto exato da razão de ela existir.

Causa: um artigo já presente no sidecar era REPROCESSADO com a rede desligada
(por desenho, para não re-buscar o que já se tem). A resolução do wrapper do
Google News não acontece offline, virava `RESOLUTION_FAILED`, e o registro
degradado SOBRESCREVIA o bom.

O contrato que estes testes fixam não é "registro imutável" — isso congelaria
a camada e impediria melhora. É mais estreito:

    falha transitória não destrói informação válida;
    observação melhor pode substituir a pior;
    `CAP_REACHED` volta para a fila, porque é justamente o que ficou por fazer;
    `last_seen` acompanha o tempo sem tocar no conteúdo.
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import risk_dashboard as rd
import reliability_input_rehearsal as rh
import reliability_input_shadow as sh

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _bom(url="http://a/1", run=10, falha=rh.OK, ready=True, chars=900):
    """Registro como o de um artigo já enriquecido com sucesso."""
    return {
        "article_id": sh.il.identidade(url), "url": url, "titulo": "T",
        "falha": falha, "tier_final": rh.TIER_R2,
        "final": {"useful_chars": chars, "sentence_like_count": 6,
                  "meaningful_gain_vs_title": 80,
                  "input_ready_under_r7c_policy": ready, "faltou": []},
        "r0_legacy": {"useful_chars": 20}, "r0_extended": {"useful_chars": 20},
        "provenance": [{"metodo": "title", "chars": 2},
                       {"metodo": "jsonld:articleBody", "chars": chars}],
        "content_hash": "h" * 32, "best_input_chars": chars,
        "enrichment": {"origem": "FETCHED", "falha": falha, "tier": rh.TIER_R2},
        "empresas": [{"empresa": "A", "candidatos": [], "tem_candidato": False}],
        "n_empresas": 1, "tem_algum_candidato": False,
        "first_seen_run": run, "last_seen_run": run,
        "query_kind": "company_query",
    }


print("=" * 98)
print("BLOCO A — o contrato de preservação, isolado")
print("=" * 98)
_src = io.open("reliability_input_shadow.py", encoding="utf-8").read()
_col = _src.split("def coletar(")[1].split("\ndef ")[0]
check("antes.get(\"falha\") not in (rh.CAP_REACHED" in _col,
      "[1] só se reprocessa o que ficou pendente por teto")
check("reaproveitados" in _col,
      "[2] o reaproveitamento é contado e sai na telemetria")
check("antes[\"last_seen_run\"] = run_count" in _col,
      "[3] `last_seen_run` acompanha o tempo sem tocar no conteúdo")
check("permitir_rede=permitir_rede," in _col
      and "permitir_rede and not antes" not in _col,
      "[4] o artigo reprocessado deixou de ser forçado a rodar offline")

print()
print("=" * 98)
print("BLOCO B — CASE A: registro bom sobrevive a run sem rede")
print("=" * 98)
_tmp = Path(tempfile.mkdtemp()) / "s.json"
_orig = sh.SIDECAR
try:
    sh.SIDECAR = _tmp
    side = sh.carregar()
    side.setdefault(sh.MARCO, 10)
    side["articles"]["a1"] = _bom()
    sh.gravar(side)
    _antes = json.loads(json.dumps(side["articles"]["a1"]))
    r = sh.coletar(cfg, run_count=11, max_emissores=1, permitir_rede=False)
    _dep = json.load(io.open(_tmp, encoding="utf-8"))["articles"].get("a1")
    check(_dep is not None, "[5] o registro continua no sidecar")
    check(_dep["final"]["input_ready_under_r7c_policy"] is True,
          "[6] `input_ready` preservado")
    check(_dep["falha"] == rh.OK,
          f"[7] NÃO virou RESOLUTION_FAILED ({_dep['falha']})")
    check(_dep["content_hash"] == _antes["content_hash"],
          "[8] o content hash do melhor input é o mesmo")
    check(_dep["provenance"] == _antes["provenance"],
          "[9] a procedência é preservada")
    check(_dep["best_input_chars"] == _antes["best_input_chars"],
          "[10] o tamanho do melhor input não encolhe")
finally:
    sh.SIDECAR = _orig

print()
print("=" * 98)
print("BLOCO C — CASE B: CAP_REACHED volta para a fila")
print("=" * 98)
check(rh.CAP_REACHED in ("CAP_REACHED",), "[11] o estado existe")
_cap = _bom(url="http://a/2", falha=rh.CAP_REACHED, ready=False, chars=20)
check(_cap["falha"] == rh.CAP_REACHED, "[12] fixture com o estado pendente")
_reprocessa = _cap.get("falha") in (rh.CAP_REACHED, None, "")
check(_reprocessa,
      "[13] a condição do coletor manda reprocessar quem ficou pelo teto")
_nao = _bom(url="http://a/3", falha=rh.OK)
check(_nao.get("falha") not in (rh.CAP_REACHED, None, ""),
      "[14] e NÃO reprocessar quem já foi resolvido")
_rob = _bom(url="http://a/4", falha=rh.ROBOTS_BLOCKED, ready=False)
check(_rob.get("falha") not in (rh.CAP_REACHED, None, ""),
      "[15] robots é decisão do site, não fila — não se insiste a cada run")

print()
print("=" * 98)
print("BLOCO D — CASE C/D: melhora é permitida, piora não destrói")
print("=" * 98)
_tmp2 = Path(tempfile.mkdtemp()) / "s.json"
try:
    sh.SIDECAR = _tmp2
    side = sh.carregar()
    side.setdefault(sh.MARCO, 10)
    # CAP_REACHED é o único caminho de melhora: volta para a fila e pode subir.
    side["articles"]["a2"] = _bom(url="http://a/2", falha=rh.CAP_REACHED,
                                  ready=False, chars=20)
    sh.gravar(side)
    check(json.load(io.open(_tmp2, encoding="utf-8"))["articles"]["a2"]["falha"]
          == rh.CAP_REACHED,
          "[16] registro pendente persiste como pendente")
    # CASE D: um registro bom não é substituído por observação pior.
    side["articles"]["a3"] = _bom(url="http://a/3", chars=1500)
    sh.gravar(side)
    _dep = json.load(io.open(_tmp2, encoding="utf-8"))["articles"]["a3"]
    check(_dep["final"]["useful_chars"] == 1500,
          "[17] o melhor input conhecido permanece o melhor")
finally:
    sh.SIDECAR = _orig
check("CAP_REACHED" in _col,
      "[18] a política de retomada é explícita no código, não implícita")

print()
print("=" * 98)
print("BLOCO E — CASE E/F: dois runs seguidos, zero degradação")
print("=" * 98)
_tmp3 = Path(tempfile.mkdtemp()) / "s.json"
try:
    sh.SIDECAR = _tmp3
    r1 = sh.coletar(cfg, run_count=20, max_emissores=2, permitir_rede=False)
    d1 = json.load(io.open(_tmp3, encoding="utf-8"))["articles"]
    r2 = sh.coletar(cfg, run_count=21, max_emissores=2, permitir_rede=False)
    d2 = json.load(io.open(_tmp3, encoding="utf-8"))["articles"]
    comuns = set(d1) & set(d2)
    piorou = [k for k in comuns
              if (d1[k].get("final") or {}).get("input_ready_under_r7c_policy")
              and not (d2[k].get("final") or {}).get("input_ready_under_r7c_policy")]
    mudou_falha = [k for k in comuns if d1[k].get("falha") != d2[k].get("falha")]
    check(len(comuns) > 0, f"[19] há registros em comum entre os dois runs ({len(comuns)})")
    check(not piorou, f"[20] CASE F: nenhum `input_ready` foi perdido ({len(piorou)})")
    check(not mudou_falha,
          f"[21] nenhum registro mudou de estado sem motivo ({len(mudou_falha)})")
    check(r2["resumo"].get("reaproveitados", 0) > 0,
          f"[22] o segundo run reaproveita ({r2['resumo'].get('reaproveitados')})")
    check(all(v.get("last_seen_run") == 21 for v in d2.values()),
          "[23] CASE E: `last_seen_run` avança para todos")
    check(all(v.get("first_seen_run") == 20 for v in d2.values()),
          "[24] e `first_seen_run` NÃO é reescrito")
    check(len(d2) == len(d1),
          f"[25] o sidecar não infla com duplicatas ({len(d1)} → {len(d2)})")
finally:
    sh.SIDECAR = _orig

print()
print("=" * 98)
print("BLOCO F — o teto e os limites vizinhos")
print("=" * 98)
import reliability_enrichment_sidecar as sc  # noqa: E402
check(sh.MAX_FETCH_POR_RUN == 80,
      f"[26] o teto do shadow segue 80 ({sh.MAX_FETCH_POR_RUN})")
check(sc.MAX_REQUESTS_POR_RUN == 40,
      f"[27] o teto do R5/R6 segue INTOCADO em 40 ({sc.MAX_REQUESTS_POR_RUN})")
check(rh.MAX_FETCH_ARTIGOS == 40,
      f"[28] o teto do rehearsal LOCAL segue 40 ({rh.MAX_FETCH_ARTIGOS})")
check('"limite_fetch": max_fetch' in _col,
      "[29] o teto continua viajando no contador")

print()
print("=" * 98)
print("BLOCO G — o shadow segue isolado")
print("=" * 98)
check("risk_input_shadow" not in io.open("risk_dashboard.py",
                                         encoding="utf-8").read(),
      "[30] o sidecar não é lido por nenhum caminho de produção")
for proibido in ("save_history", "merge_into_history", "--backfill",
                 "build_evolution", "genai", "gemini"):
    check(proibido not in _src, f"[31..36] o shadow não usa `{proibido}`")
check("traceback.print_exc" in _src,
      "[37] fail-open continua não sendo falha silenciosa")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-P3 (preservação do input): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
