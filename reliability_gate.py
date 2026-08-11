#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_gate.py — 4I.2 Reliability Learning Loop (R1b).

Resumo único de confiabilidade, para rodar DEPOIS de cada workflow de produção.

Reúne, sem duplicar lógica:
  1. Gold                     (test_gold_4i.py)
  2. Attribution Gold         (test_attribution_gold_4i.py)
  3. Generalização por família (reliability_generalization)
  4. Live critical/high scan  (reliability_live_audit)
  5. Novelty diff             (idem)
  6. Holdout                  (idem)

Não roda a suíte pytest inteira — isso é papel do CI e duplicaria trabalho.

Contrato de saída (R1c §18) — a distinção importa:

  EXIT 1 · BLOCKING — regressão estrutural, algo que ANTES funcionava:
     gold, attribution, exact/sibling/negative de família GENERALIZED,
     fixture inválida, contaminação pelo fallback F1, schema/crash.

  EXIT 0 · REVIEW REQUIRED — precisa de olho humano, não é regressão:
     crítico/alto NOVO ou CHANGED, item UNREVIEWED, holdout não revisado,
     família PARTIAL ou BLOCKED_BY_INPUT, review AMBIGUOUS.

Uma notícia crítica legítima recém-coletada precisa SALTAR AOS OLHOS sem
quebrar o build — senão o gate vira ruído e passa a ser ignorado, que é
exatamente como a Britannica entrou em produção.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import reliability_generalization as rg
import reliability_live_audit as la
import reliability_metrics as rm

PY = sys.executable
OUTDIR = Path("out_reliability")


def _rodar(script: str) -> str:
    try:
        p = subprocess.run([PY, script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"ERRO ao executar {script}: {exc}"


def _num(padrao: str, texto: str, default: str = "?") -> str:
    m = re.search(padrao, texto)
    return m.group(1) if m else default


def main() -> int:
    OUTDIR.mkdir(exist_ok=True)
    falhas = []

    gold_txt = _rodar("test_gold_4i.py")
    pos = _num(r"POSITIVOS.*?(\d+/\d+)", gold_txt)
    neg = _num(r"NEGATIVOS.*?(\d+/\d+)", gold_txt)
    tot = _num(r"RESULTADO GOLD 4I:\s*(\d+/\d+)", gold_txt)

    attr_txt = _rodar("test_attribution_gold_4i.py")
    attr = _num(r"RESULTADO ATTRIBUTION GOLD:\s*(\d+/\d+)", attr_txt)

    gen = rg.avaliar()
    g = {"exact": [0, 0], "siblings": [0, 0], "negatives": [0, 0],
         "exercised_exact": [0, 0], "exercised_siblings": [0, 0]}
    st, stb = {}, {}
    for f in gen["families"]:
        for k in g:
            g[k][0] += f[k][0]
            g[k][1] += f[k][1]
        st[f["status"]] = st.get(f["status"], 0) + 1
        stb[f["behavioral_status"]] = stb.get(f["behavioral_status"], 0) + 1
    # Regressão de família já resolvida é falha dura. O critério é o
    # COMPORTAMENTAL — é ele que significa "algo que antes funcionava caiu".
    # O nível família-específico (R3b) é evidência de aprendizado, não de
    # regressão: perder `GENERALIZED` por falta de sibling exercitado é
    # lacuna de cobertura de teste, e vira aviso, não bloqueio (§24).
    regrediu = [f["family_id"] for f in gen["families"]
                if f["family_id"] in ("F5", "F6") and f["behavioral_status"] != rg.COVERED]
    if regrediu:
        falhas.append(f"REGRESSÃO: famílias generalizadas caíram: {', '.join(regrediu)}")
    if "?" in (pos, neg, tot, attr):
        falhas.append("REGRESSÃO/ESTRUTURAL: gold ou attribution não produziram resultado")
    elif pos.split("/")[0] != pos.split("/")[1]:
        falhas.append(f"REGRESSÃO: gold positives {pos} — positivos não podem cair")

    res = la.coletar()
    nov = la.novidade(res["linhas"])
    la.gravar(res)
    r = la.resumo(res, nov)

    print("=" * 96)
    print("RELIABILITY GATE")
    print("=" * 96)
    print("\nGOLD")
    print(f"  positives            {pos}")
    print(f"  negatives            {neg}")
    print(f"  total                {tot}")
    print(f"  attribution          {attr}")
    print("\nBEHAVIORAL GENERALIZATION   (o output final está correto?)")
    print(f"  exact                {g['exact'][0]}/{g['exact'][1]}")
    print(f"  siblings             {g['siblings'][0]}/{g['siblings'][1]}")
    print(f"  negative controls    {g['negatives'][0]}/{g['negatives'][1]}")
    print(f"  behaviorally covered {stb.get(rg.COVERED, 0)}")
    print("\nFAMILY-SPECIFIC GENERALIZATION   (a família sob teste foi exercitada?)")
    print(f"  exercised exact      {g['exercised_exact'][0]}/{g['exercised_exact'][1]}")
    print(f"  exercised siblings   {g['exercised_siblings'][0]}/{g['exercised_siblings'][1]}")
    print(f"  generalized          {st.get(rg.GENERALIZED, 0)}")
    print(f"  covered only         {st.get(rg.COVERED, 0)}")
    print(f"  partial              {st.get(rg.PARTIAL, 0)}")
    print(f"  blocked/unresolved   {st.get(rg.BLOCKED, 0) + st.get(rg.UNRESOLVED, 0)}")
    print("\nLIVE")
    print(f"  records              {r['records']}")
    print(f"  critical             {r['critical']}")
    print(f"  high                 {r['high']}")
    if r["sem_baseline"]:
        print("  novelty              SEM BASELINE (rode reliability_live_audit.py --set-baseline)")
    else:
        print(f"  new critical         {r['new_critical']}")
        print(f"  new high             {r['new_high']}")
        print(f"  changed              {r['changed']}")
    print(f"  critical unreviewed  {r['unreviewed_critical']}")
    print("\nHOLDOUT")
    print(f"  candidates           {r['holdout_total']}")
    print(f"  reviewed             {r['holdout_reviewed']}")
    print(f"  unreviewed           {r['holdout_unreviewed']}")
    if r["holdout_reviewed"] == 0:
        print("  precision            NÃO CALCULÁVEL — sem labels humanos")
    rev, hr = r["review"], r["review_high"]
    print("\nCRITICAL REVIEW")
    print(f"  total critical       {rev['total']}")
    print(f"  reviewed             {rev['reviewed']}  (coverage {rev['coverage']})")
    print(f"  true                 {rev['TRUE']}")
    print(f"  false_positive       {rev['FALSE_POSITIVE']}")
    print(f"  ambiguous            {rev['AMBIGUOUS']}")
    print(f"  unreviewed           {rev['UNREVIEWED']}")
    print(f"  precision reviewed   {rev['precision']}")
    print("     ^ PRECISION ON CURRENT REVIEWED CRITICAL SET — não é precisão global")
    print(f"  reviewer provenance  {r.get('reviewer_types') or '—'}")
    print("     ^ rótulo do assistente NÃO equivale a confirmação manual do usuário")
    print("\nHIGH REVIEW")
    print(f"  total high           {hr['total']}")
    print(f"  reviewed             {hr['reviewed']}  (coverage {hr['coverage']})")
    print(f"  unreviewed           {hr['UNREVIEWED']}")
    print("     ^ a precisão dos críticos NÃO se extrapola para os altos")
    print("\nREVIEW QUEUE")
    print(f"  aguardando adjudicação humana  {len(gen['review_queue'])}")

    # R3b §24 — as duas métricas, informativas, sem threshold de bloqueio.
    met = None
    try:
        met = rm.relatorio()
    except SystemExit as exc:              # baseline ainda não congelado
        print(f"\nMETRICS\n  indisponível: {exc}")
    if met:
        print(f"\nMETRICS  ·  baseline `{met['baseline_id']}`  "
              f"denominador fixo {met['counts']['denominador_fixo']}")
        for u, rot in (("stored", "STORED PRODUCTION   "),
                       ("candidate", "CANDIDATE RUNTIME   ")):
            print(f"  {rot} A fixed accuracy {met[u]['a']['texto']:>18}"
                  f"   ·   B live precision {met[u]['b']['texto']:>18}")
        print("     ^ A tem denominador FIXO (mede evolução); B tem denominador"
              " VARIÁVEL (descreve a tela). Não são a mesma conta.")
        print("     ^ CANDIDATE é projeção em memória — NÃO está em produção.")

    # WARNINGS (§18) — exigem olho humano, mas não são regressão.
    avisos = []
    if not r["sem_baseline"]:
        if r["new_critical"]:
            avisos.append(f"{r['new_critical']} crítico(s) NOVO(S) — adjudicar")
        if r["new_high"]:
            avisos.append(f"{r['new_high']} alto(s) NOVO(S)")
        if r["changed"]:
            avisos.append(f"{r['changed']} item(ns) CHANGED")
    if rev["UNREVIEWED"]:
        avisos.append(f"{rev['UNREVIEWED']} crítico(s) UNREVIEWED")
    if rev["AMBIGUOUS"]:
        avisos.append(f"{rev['AMBIGUOUS']} review(s) AMBIGUOUS")
    if r["holdout_unreviewed"]:
        avisos.append(f"{r['holdout_unreviewed']} candidato(s) de holdout não revisado(s)")
    parciais = st.get(rg.PARTIAL, 0) + st.get(rg.BLOCKED, 0) + st.get(rg.UNRESOLVED, 0)
    if parciais:
        avisos.append(f"{parciais} família(s) PARTIAL/BLOCKED/UNRESOLVED")
    if st.get(rg.COVERED, 0):
        avisos.append(f"{st[rg.COVERED]} família(s) com output correto mas SEM "
                      f"evidência suficiente de que a própria regra foi exercitada")

    print()
    print("=" * 96)
    if avisos:
        print("  ⚠️  REVIEW REQUIRED — não bloqueia, mas exige olho humano:")
        for a in avisos:
            print(f"       · {a}")
    if falhas:
        print("  ❌ BLOCKING:")
        for f in falhas:
            print(f"       · {f}")
        print("=" * 96)
        return 1
    if not avisos:
        print("  ✅ nada exigindo ação imediata")
    print("=" * 96)
    (OUTDIR / "gate_summary.json").write_text(json.dumps(
        {"gold": {"positives": pos, "negatives": neg, "total": tot, "attribution": attr},
         "generalization": {"exact": g["exact"], "siblings": g["siblings"],
                            "negatives": g["negatives"],
                            "exercised_exact": g["exercised_exact"],
                            "exercised_siblings": g["exercised_siblings"],
                            "status": st, "behavioral_status": stb},
         "metrics": ({"baseline_id": met["baseline_id"], "counts": met["counts"],
                      "stored": {"a": met["stored"]["a"]["texto"],
                                 "b": met["stored"]["b"]["texto"]},
                      "candidate": {"a": met["candidate"]["a"]["texto"],
                                    "b": met["candidate"]["b"]["texto"]}}
                     if met else None),
         "live": r, "review_queue": len(gen["review_queue"])},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
