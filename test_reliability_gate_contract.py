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

ISOLAMENTO (R5a §0.2) — este arquivo é DESTRUTIVO por natureza: quebra
fixtures de propósito para provar que o gate reage. Ele trabalha numa CÓPIA
temporária de tudo que muta (`error_families.json`, o baseline de novidade e
o outdir), via as variáveis `RELIABILITY_*`. A fixture versionada nunca é
tocada, e nenhuma execução deixa estado para a próxima.

Isso corrige uma flakiness real, reproduzida: o BLOCO A simulava um crítico
NOVO removendo do snapshot uma chave escolhida por nome de empresa fixo. Se
essa chave já não estivesse no inventário vivo — o que passou a acontecer
quando a GM deixou de pontuar —, apagá-la não gerava novidade nenhuma e a
asserção caía. O teste então se auto-curava, porque o BLOCO C regravava o
snapshot: falhava UMA vez e passava sempre depois. O alvo agora é derivado do
inventário vivo, não de um nome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="gate_contract_"))
_FAM_ORIG = Path("test_fixtures_reliability/error_families.json")
_FAM_TMP = _TMP / "error_families.json"
shutil.copy2(_FAM_ORIG, _FAM_TMP)
os.environ["RELIABILITY_FAMILIES"] = str(_FAM_TMP)
os.environ["RELIABILITY_OUTDIR"] = str(_TMP / "out")

import reliability_live_audit as la  # noqa: E402  (depende do env acima)

PASS = FAIL = 0
BASE = la.BASELINE
_fam = _FAM_TMP


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
                       text=True, encoding="utf-8", errors="replace", timeout=900,
                       env={**os.environ})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# o snapshot de novidade parte do estado atual, dentro do workspace temporário
la.set_baseline(la.coletar())


print("=" * 96)
print("BLOCO A — §19/§22/§23: novidade e review pendente NÃO bloqueiam")
print("=" * 96)
_bkp = BASE.read_text(encoding="utf-8")
try:
    # Simula um crítico NOVO removendo do snapshot um item que ESTÁ no
    # inventário vivo — derivado da medição, nunca de um nome de empresa
    # fixo, que envelhece assim que aquele item deixa de pontuar.
    _vivos = {la._chave(l["url"], l["company"], l["event_id"])
              for l in la.coletar()["linhas"] if l["severity"] == "critico"}
    d = json.loads(_bkp)
    alvo = next(k for k in d["itens"] if k in _vivos)
    d["itens"].pop(alvo)
    BASE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    code, out = rodar_gate()
    check(code == 0, f"[§19] crítico NOVO não bloqueia (exit={code})")
    check("REVIEW REQUIRED" in out, "[§19b] aparece como REVIEW REQUIRED, com destaque")
    check("NOVO" in out, "[§19c] o item novo é nomeado no aviso")
finally:
    BASE.write_text(_bkp, encoding="utf-8")

code, out = rodar_gate()
check(code == 0, f"[§22/§23] famílias PARTIAL e holdout pendente não bloqueiam (exit={code})")
check("REVIEW REQUIRED" in out, "[§22b] pendências ficam visíveis como aviso")

print()
print("=" * 96)
print("BLOCO B — §20/§21: regressão estrutural BLOQUEIA")
print("=" * 96)
_orig = _fam.read_text(encoding="utf-8")   # cópia temporária, não a versionada
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
               capture_output=True, timeout=900, env={**os.environ})
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
print("BLOCO E — R5a §0.2: o teste destrutivo não contamina a fixture versionada")
print("=" * 96)
check(_FAM_ORIG.read_text(encoding="utf-8") == _orig,
      "[§0.2] error_families.json versionado ficou byte a byte intacto")
check(str(la.OUTDIR).startswith(str(_TMP)),
      f"[§0.2b] o outdir usado é temporário: {la.OUTDIR}")
check(not Path("out_reliability/live_baseline.json").samefile(BASE)
      if Path("out_reliability/live_baseline.json").exists() else True,
      "[§0.2c] o baseline de novidade compartilhado não foi tocado")

shutil.rmtree(_TMP, ignore_errors=True)

print()
print("=" * 96)
print(f"RESULTADO GATE CONTRACT: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    sys.exit(1)
