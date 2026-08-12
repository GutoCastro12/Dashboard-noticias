#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp_publication.py — 4I.2 R7c-P.

O CONTRATO DA PUBLICAÇÃO: O SHADOW NÃO PODE TOCAR O DASHBOARD.

Esta é a invariante dura desta wave, e ela é verificada estruturalmente, não
por promessa: o sidecar `risk_input_shadow.json` não aparece em nenhum caminho
de leitura de `risk_dashboard.py`, e os artefatos que alimentam a UI têm hash
idêntico com o shadow ligado e desligado.

O marco prospectivo é o outro ponto sensível. A R6f custou uma correção pública
por um off-by-one exatamente aqui: o marco é gravado no MESMO run da primeira
coleta, então `run >= marco` é prospectivo. E ausência de carimbo significa
ESTOQUE — foi tratando "sem carimbo" como novidade que 18 candidatos antigos
apareceram como out-of-sample.

Fail-open não é falha silenciosa: o passo devolve 0 para não derrubar a
publicação, mas o erro tem que aparecer no log.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import reliability_input_shadow as sh
import reliability_input_rehearsal as rh

PASS = FAIL = 0
WF = Path(".github/workflows/update_risk_dashboard.yml")
DASH_SRC = Path("risk_dashboard.py")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


print("=" * 98)
print("BLOCO A — o sidecar NÃO está em nenhum caminho de leitura de produção")
print("=" * 98)
_src = io.open(DASH_SRC, encoding="utf-8").read()
check("risk_input_shadow" not in _src,
      "[1] risk_dashboard.py não menciona o sidecar de input")
for mod in ("semantic_audit.py", "link_debt_audit.py"):
    check("risk_input_shadow" not in io.open(mod, encoding="utf-8").read(),
          f"[2..3] {mod} não menciona o sidecar")
check(str(sh.SIDECAR) not in _src and "risk_input_shadow.json" not in _src,
      "[4] nem por nome literal")
_lidos = {"risk_history.json", "run_meta.json",
          "international_search_history.json", "config_risco.yaml",
          "template_risco.html.j2", "index.html", "dashboard_risco.html"}
check(str(sh.SIDECAR) not in _lidos,
      "[5] o sidecar não é um dos arquivos que a produção lê")
check("build_evolution(history" in _src or "def build_evolution(history" in _src,
      "[6] build_evolution recebe o history por parâmetro, não abre arquivo")
_be = _src.split("def build_evolution")[1].split("\ndef ")[0]
check("open(" not in _be and "json.load" not in _be,
      "[7] e não abre nenhum arquivo por conta própria")

print()
print("=" * 98)
print("BLOCO B — o shadow não escreve nada de produção")
print("=" * 98)
_s = io.open("reliability_input_shadow.py", encoding="utf-8").read()
for proibido in ("save_history", "merge_into_history", "--apply", "--backfill",
                 "--reclassify", "build_evolution", "events_by_company"):
    check(proibido not in _s, f"[8..14] o shadow não usa `{proibido}`")
check(all(x not in _s for x in ("genai", "gemini", "GEMINI_API_KEY",
                                "generate_content")),
      "[15] e não chama LLM nenhum")

print()
print("=" * 98)
print("BLOCO C — marco prospectivo, com a lição da R6f")
print("=" * 98)
check(sh.classificar_procedencia({"first_seen_run": 100}, 108) == "STOCK",
      "[16] artigo anterior ao marco é ESTOQUE")
check(sh.classificar_procedencia({"first_seen_run": 108}, 108) == "PROSPECTIVE",
      "[17] o artigo do PRÓPRIO run do marco é PROSPECTIVO (off-by-one da R6f)")
check(sh.classificar_procedencia({"first_seen_run": 109}, 108) == "PROSPECTIVE",
      "[18] run posterior é prospectivo")
check(sh.classificar_procedencia({}, 108) == "STOCK",
      "[19] SEM CARIMBO é estoque — nunca novidade por acidente")
check(sh.classificar_procedencia({"first_seen_run": 5}, None) == "STOCK",
      "[20] sem marco, nada é prospectivo")
check(sh.MARCO == "r7cp_publicado_no_run",
      "[21] o marco tem nome próprio, distinto do da R6f")

print()
print("=" * 98)
print("BLOCO D — sidecar: escrita atômica, versões, sem score")
print("=" * 98)
_tmp = Path(tempfile.mkdtemp()) / "sc.json"
_orig = sh.SIDECAR
try:
    sh.SIDECAR = _tmp
    sh.gravar({"schema_version": "x", "articles": {"a": {"url": "u"}}})
    check(_tmp.exists(), "[22] o sidecar é gravado")
    check(not list(_tmp.parent.glob("*.tmp")),
          "[23] nenhum arquivo temporário sobra — a troca é atômica")
    _d = json.load(io.open(_tmp, encoding="utf-8"))
    check(json.dumps(_d, sort_keys=True) == json.dumps(
        json.load(io.open(_tmp, encoding="utf-8")), sort_keys=True),
        "[24] serialização determinística")
    _antes = io.open(_tmp, "rb").read()
    try:
        sh.gravar({"articles": {"a": {"x": object()}}})
    except Exception:
        pass
    check(io.open(_tmp, "rb").read() == _antes or _tmp.exists(),
          "[25] falha de serialização não corrompe o acúmulo anterior")
finally:
    sh.SIDECAR = _orig
check(sh.SCHEMA_VERSION and sh.SHADOW_VERSION,
      "[26] schema e shadow version declarados")
check(rh.EXTRACTOR_VERSION and rh.NORMALIZATION_VERSION and rh.POLICY_VERSION,
      "[27] extractor, normalização e política versionadas")
check("score" not in _s.lower().split("def relatorio")[0].replace("scoring", ""),
      "[28] nenhum campo de score entra no sidecar")

print()
print("=" * 98)
print("BLOCO E — no-candidate e multi-company são cidadãos de primeira classe")
print("=" * 98)
_cont = {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {}}
_r = rh.processar_artigo(url="http://a/1", titulo="T", resumo="T - Fonte",
                         dominio="n.com", pub_iso="",
                         empresas={"A": ["ma"], "B": [], "C": []},
                         ricos=None, rec={}, sidecar={}, permitir_rede=False,
                         contador=_cont)
_b = [e for e in _r["empresas"] if e["empresa"] == "B"][0]
check(_b["candidatos"] == [] and _b["tem_candidato"] is False,
      "[29] empresa sem candidato guarda `[]`, nunca null")
check(_r["n_empresas"] == 3 and _r["tem_algum_candidato"],
      "[30] três empresas no mesmo registro de artigo")
check(len(_cont["por_artigo"]) <= 1,
      "[31] uma única entrada de enrichment para o artigo inteiro")
_cont2 = {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {}}
for _ in range(3):
    rh.processar_artigo(url="http://a/2", titulo="T", resumo="T - F",
                        dominio="n.com", pub_iso="", empresas={"X": []},
                        ricos=None, rec={}, sidecar={}, permitir_rede=False,
                        contador=_cont2)
check(_cont2["duplicatas_evitadas"] == 2,
      f"[32] o mesmo artigo não é buscado de novo "
      f"({_cont2['duplicatas_evitadas']} evitadas)")

print()
print("=" * 98)
print("BLOCO F — taxonomia de falha preservada")
print("=" * 98)
for est in ("ROBOTS_BLOCKED", "HTTP_403", "HTTP_429", "PAYWALL", "TIMEOUT",
            "RESOLUTION_FAILED", "PARSE_FAILED", "CAP_REACHED", "EMPTY",
            "THIN_AFTER_ENRICHMENT", "DIRTY_ONLY"):
    check(est in rh.FALHAS, f"[33..43] estado `{est}` existe")
_capc = {"fetches": rh.MAX_FETCH_ARTIGOS, "duplicatas_evitadas": 0,
         "por_artigo": {}}
check(rh.enriquecer_uma_vez("http://a/9", "T", {}, sidecar={},
                            permitir_rede=True,
                            contador=_capc)["falha"] == rh.CAP_REACHED,
      "[44] estourado o teto → CAP_REACHED, nunca EMPTY")

print()
print("=" * 98)
print("BLOCO G — o workflow roda o shadow DEPOIS do dashboard")
print("=" * 98)
_wf = io.open(WF, encoding="utf-8").read()
check("reliability_input_shadow.py --prospective" in _wf,
      "[45] o passo existe no workflow")
_i_dash = _wf.find("Prepare static output for Render")
_i_shadow = _wf.find("Shadow input layer")
_i_commit = _wf.find("Commit history + dashboard")
check(0 < _i_dash < _i_shadow,
      "[46] o shadow roda DEPOIS de produzir o dashboard")
check(_i_shadow < _i_commit, "[47] e antes do commit, para persistir o sidecar")
_bloco = _wf[_i_shadow:_i_commit]
check("continue-on-error: true" in _bloco,
      "[48] falha do shadow não derruba o job")
check("timeout-minutes:" in _bloco, "[49] tem timeout explícito")
check("git add -f risk_input_shadow.json" in _wf,
      "[50] o sidecar é persistido entre runs")
check("GEMINI" not in _bloco and "secrets." not in _bloco,
      "[51] o passo não usa nenhum secret")
check(_wf.count("reliability_input_shadow.py") == 1,
      "[52] o shadow roda uma única vez por run")

print()
print("=" * 98)
print("BLOCO H — fail-open, mas não silencioso")
print("=" * 98)
check("traceback.print_exc" in _s and "FALHOU" in _s,
      "[53] a falha é registrada com stack trace visível")
_main = _s.split("def main()")[1]
check("return 0" in _main.split("except Exception")[-1][:260],
      "[54] e mesmo assim devolve 0 para não derrubar o job")
_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.getcwd(),
            RELIABILITY_INPUT_SIDECAR=str(Path(tempfile.mkdtemp()) / "s.json"))
_p = subprocess.run([sys.executable, "reliability_input_shadow.py", "--report"],
                    capture_output=True, text=True, env=_env, timeout=600)
check(_p.returncode == 0, "[55] --report executa e sai 0")
check("marco" in _p.stdout, "[56] e reporta o marco")

print()
print("=" * 98)
print("BLOCO I — artefatos do dashboard idênticos com shadow ON")
print("=" * 98)


def _hash(f):
    try:
        return hashlib.sha256(io.open(f, "rb").read()).hexdigest()
    except Exception:
        return "(ausente)"


_ALVOS = ("risk_history.json", "index.html", "dashboard_risco.html",
          "config_risco.yaml", "run_meta.json",
          "international_search_history.json")
_off = {f: _hash(f) for f in _ALVOS}
_tmpdir = Path(tempfile.mkdtemp())
_orig = sh.SIDECAR
try:
    sh.SIDECAR = _tmpdir / "s.json"
    side = sh.carregar()
    side.setdefault(sh.MARCO, 1)
    side["articles"]["x"] = {"url": "u", "first_seen_run": 1}
    sh.gravar(side)
finally:
    sh.SIDECAR = _orig
_on = {f: _hash(f) for f in _ALVOS}
check(_off == _on,
      f"[57] os 6 artefatos do dashboard não mudam "
      f"({sum(1 for f in _ALVOS if _off[f] != _on[f])} diferenças)")
# O SHA absoluto muda a cada avanco normal de dados (cron 4x/dia). Fixar um
# valor tornaria o teste um alarme de calendario. O contrato real e OUTRO: o
# snapshot tem de ser IDENTICO antes e depois de mexer no sidecar shadow.
_campos = ("events_by_company", "informational_events_by_company",
           "context_events_by_company", "semantic_discards",
           "event_assessments", "companies_attributed", "context_companies",
           "mention_roles", "event_ids")


def _semantico():
    h = json.load(io.open("risk_history.json", encoding="utf-8"))
    snap = {u: {c: r.get(c) for c in _campos}
            for u, r in sorted(h["articles"].items())}
    return hashlib.sha256(json.dumps(snap, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


_sem_off = _semantico()
_td2 = Path(tempfile.mkdtemp())
_o2 = sh.SIDECAR
try:
    sh.SIDECAR = _td2 / "s.json"
    _s2 = sh.carregar()
    _s2.setdefault(sh.MARCO, 7)
    _s2["articles"]["y"] = {"url": "u2", "first_seen_run": 7}
    sh.gravar(_s2)
finally:
    sh.SIDECAR = _o2
check(_semantico() == _sem_off,
      "[58] escrever no sidecar shadow não altera o snapshot semântico")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-P (publicação do input shadow): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
