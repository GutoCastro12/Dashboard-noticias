#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reliability_gate_contract.py — 4I.2 R1c.

O gate precisa distinguir REGRESSÃO de NOVIDADE.

  EXIT 1  só para regressão estrutural — algo que antes funcionava e caiu.
  EXIT 0  para tudo que pede olho humano: crítico novo, item unreviewed,
          holdout pendente, família parcial, review ambíguo.

Se um crítico legítimo recém-coletado quebrasse o build, o gate viraria ruído
e passaria a ser ignorado — que é exatamente como a Britannica chegou à
produção sem ninguém ver.

Testa também que review humano SOBREVIVE à regravação do baseline (§14).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import reliability_live_audit as la

PASS = FAIL = 0
BASE = Path("out_reliability/live_baseline.json")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def rodar_gate() -> tuple[int, str]:
    p = subprocess.run([sys.executable, "reliability_gate.py"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


print("=" * 96)
print("BLOCO A — §19/§22/§23: novidade e review pendente NÃO bloqueiam")
print("=" * 96)
_bkp = BASE.read_text(encoding="utf-8") if BASE.exists() else None
try:
    # simula um crítico NOVO removendo um item do baseline
    if _bkp:
        d = json.loads(_bkp)
        alvo = next((k for k in d["itens"] if "General Motors" in k), list(d["itens"])[0])
        d["itens"].pop(alvo)
        BASE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    code, out = rodar_gate()
    check(code == 0, f"[§19] crítico NOVO não bloqueia (exit={code})")
    check("REVIEW REQUIRED" in out, "[§19b] aparece como REVIEW REQUIRED, com destaque")
    check("NOVO" in out, "[§19c] o item novo é nomeado no aviso")
finally:
    if _bkp:
        BASE.write_text(_bkp, encoding="utf-8")

code, out = rodar_gate()
check(code == 0, f"[§22/§23] famílias PARTIAL e holdout pendente não bloqueiam (exit={code})")
check("REVIEW REQUIRED" in out, "[§22b] pendências ficam visíveis como aviso")

print()
print("=" * 96)
print("BLOCO B — §20/§21: regressão estrutural BLOQUEIA")
print("=" * 96)
_fam = Path("test_fixtures_reliability/error_families.json")
_orig = _fam.read_text(encoding="utf-8")
try:
    # quebra um sibling de família GENERALIZED (F5) com um caso impossível
    d = json.loads(_orig)
    for f in d["families"]:
        if f["family_id"] == "F5":
            f["semantic_siblings"].append({
                "title": "BTG Pactual conclui aquisicao do banco Alfa",
                "company": "BTG Pactual", "forbidden": "ma"})
    _fam.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    code, out = rodar_gate()
    check(code == 1, f"[§21] sibling quebrado em família GENERALIZED bloqueia (exit={code})")
    check("BLOCKING" in out, "[§21b] o motivo aparece rotulado como BLOCKING")
finally:
    _fam.write_text(_orig, encoding="utf-8")

code, _ = rodar_gate()
check(code == 0, f"[§21c] restaurada a fixture, o gate volta a passar (exit={code})")

print()
print("=" * 96)
print("BLOCO C — §14: review humano sobrevive à regravação do baseline")
print("=" * 96)
antes = {l["review_status"] for l in la.coletar()["linhas"] if l["severity"] == "critico"}
subprocess.run([sys.executable, "reliability_live_audit.py", "--set-baseline"],
               capture_output=True, timeout=900)
depois = la.coletar()
crit = [l for l in depois["linhas"] if l["severity"] == "critico"]
check({l["review_status"] for l in crit} == antes,
      "[§14] regravar o snapshot NÃO apaga o review humano")
check(all(l["review_status"] != "UNREVIEWED" for l in crit),
      f"[§16] cobertura total dos críticos: {sum(1 for l in crit if l['review_status'] != 'UNREVIEWED')}/{len(crit)}")

print()
print("=" * 96)
print("BLOCO D — §12/§15: contratos da métrica de review")
print("=" * 96)
r = la.resumo(depois, la.novidade(depois["linhas"]))
rev = r["review"]
check(rev["AMBIGUOUS"] > 0 and str(rev["TRUE"] + rev["FALSE_POSITIVE"]) in rev["precision"],
      f"[§15] AMBIGUOUS fica FORA do denominador — precision {rev['precision']}")
check(rev["total"] == rev["TRUE"] + rev["FALSE_POSITIVE"] + rev["AMBIGUOUS"] + rev["UNREVIEWED"],
      "[§15b] a soma dos estados fecha com o total de críticos")
check(r["review_high"]["reviewed"] < r["review_high"]["total"],
      "[§17] altos permanecem majoritariamente não revisados — não extrapolamos")

print()
print("=" * 96)
print(f"RESULTADO GATE CONTRACT: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    sys.exit(1)
