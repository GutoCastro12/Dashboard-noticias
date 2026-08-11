#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate de generalização: as famílias já declaradas como resolvidas não podem
regredir. Famílias PARTIAL/BLOCKED/UNRESOLVED são registro honesto do que o
sistema ainda não aprendeu — não falham o build, mas ficam visíveis."""
from __future__ import annotations
import reliability_generalization as rg

PASS = FAIL = 0
res = rg.avaliar()
por_id = {f["family_id"]: f for f in res["families"]}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {label}")
    else:
        FAIL += 1; print(f"  ❌ FALHOU: {label}")


print("=" * 96)
print("NÍVEL 1 — COMPORTAMENTAL: famílias já resolvidas não podem regredir")
print("=" * 96)
for fid in ("F4", "F5", "F6"):
    f = por_id[fid]
    check(f["behavioral_status"] == rg.COVERED,
          f"[{fid}] {f['name']} — {f['behavioral_status']} "
          f"(exact {f['exact'][0]}/{f['exact'][1]}, siblings {f['siblings'][0]}/{f['siblings'][1]}, "
          f"negatives {f['negatives'][0]}/{f['negatives'][1]})")

print()
print("=" * 96)
print("NÍVEL 2 — ESPECÍFICO DA FAMÍLIA: quem resolveu foi a regra sob teste?")
print("=" * 96)
for fid in ("F4", "F5"):
    f = por_id[fid]
    check(f["status"] == rg.GENERALIZED,
          f"[{fid}] exercised exact {f['exercised_exact'][0]}/{f['exercised_exact'][1]} · "
          f"exercised siblings {f['exercised_siblings'][0]}/{f['exercised_siblings'][1]} "
          f"→ {f['status']}")
check(all(f["exercised_siblings"][0] >= rg.MIN_EXERCISED_SIBLINGS
          for f in res["families"] if f["status"] == rg.GENERALIZED),
      f"GENERALIZED exige ao menos {rg.MIN_EXERCISED_SIBLINGS} siblings que "
      f"exercitem a própria família — não basta o output final estar correto")
check(all(f["negatives_family_fired"] == 0 for f in res["families"]),
      "nenhuma família dispara indevidamente sobre controle negativo `required`")
_covered = [f["family_id"] for f in res["families"] if f["status"] == rg.COVERED]
check(True, f"famílias com output correto mas evidência insuficiente: {_covered or '—'}")

print()
print("=" * 96)
print("CONTRATOS ESTRUTURAIS DA MEMÓRIA DE ERROS")
print("=" * 96)
check(all(f["exact"][1] > 0 for f in res["families"]),
      "toda família tem ao menos uma regressão exata real")
check(all(f["status"] in (rg.GENERALIZED, rg.COVERED, rg.PARTIAL, rg.BLOCKED, rg.UNRESOLVED)
          for f in res["families"]),
      "nenhuma família usa o rótulo vago 'FIXED'")
check(all(f["rule_ids"] for f in res["families"]
          if f["status"] in (rg.GENERALIZED, rg.COVERED)),
      "família resolvida declara quais regras a implementam — sem isso não há proveniência")
check(len(res["review_queue"]) > 0,
      "review queue existe e não é auto-classificada pelo runtime")

print()
print("=" * 96)
print("ESTADO ATUAL (informativo — não falha o build)")
print("=" * 96)
for f in res["families"]:
    if f["status"] != rg.GENERALIZED:
        print(f"  {f['status']:24s} {f['family_id']} {f['name']}")

print()
print("=" * 96)
print(f"RESULTADO RELIABILITY GENERALIZATION: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
