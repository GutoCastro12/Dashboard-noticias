#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6f_shadow_v2.py — 4I.2 R6f.

O perfil shadow V2 reúne, cumulativamente, R6b (papel), R6d (responsabilização
vence resolução) e R6e (sujeito da responsabilização). Ele existe atrás do
mesmo interruptor da R6c — não há segundo mecanismo — e nada disso pode tocar
o classificador de produção.

Dois contratos que este arquivo protege:

  · MODO P É A PRODUÇÃO PUBLICADA. Com o interruptor desligado, R6d e R6e são
    inertes: L8 volta a herdar, N4 volta a ser lido como vítima. Isso é
    proposital nesta fase.

  · CONTROLE HISTÓRICO NÃO É EVIDÊNCIA. Duke, CVS, N4 e as fixtures foram
    usados para construir as regras; contá-los como amostra out-of-sample
    faria a decisão de ativação se apoiar no próprio treino.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pont(t, emp, shadow=False, resumo=""):
    h = {"articles": {"u1": {"title": t, "summary": resumo, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": [emp]}}, "run_count": 1}
    if shadow:
        with sa.shadow_fraud_roles():
            rd._reclassify_only_pass(h, cfg)
    else:
        rd._reclassify_only_pass(h, cfg)
    r = h["articles"]["u1"]
    return (set((r.get("events_by_company") or {}).get(emp) or []),
            {d.get("event_id"): d.get("regra")
             for d in (r.get("semantic_discards") or []) if d.get("empresa") == emp})


# a variante com ponto-e-virgula e a que reproduz o gap: com ponto final
# a segmentacao ja separa as duas oracoes e a atribuicao nem ocorre.
L8 = ("Supplier Alfa was found liable for fraud; Vale agreed separately to "
      "settle a contract dispute")
N4 = "A Vale employee committed fraud for Vale"
D2 = ("Dazmyn Person: Another person was arrested, charged in the ongoing Duke Energy "
      "fraud case")
D2E = ("WCSO identified a woman allegedly responsible for stealing victims' identities "
       "to assist in creating fraudulent Duke Energy accounts.")
D3 = "Third suspect arrested in Duke Energy fraud case"
D3E = ("On Tuesday deputies arrested a suspect after Duke Energy representatives "
       "discovered numerous accounts were opened in the name of customers without their "
       "knowledge, costing the company 495904 in losses in the fraud.")
CVS = "Omnicare, CVS Health reach $440 million settlement in fraud case"
CVSE = ("Omnicare and its parent company, CVS Health, have agreed to pay at least $440 "
        "million to satisfy a judgment in a case in which they were found liable for "
        "fraudulently dispensing drugs")

print("=" * 96)
print("BLOCO A — MODO P é a produção publicada: R6d e R6e são inertes")
print("=" * 96)
check(sa.shadow_fraud_roles_ativo() is False, "[1] interruptor desligado por padrão")
_g, _ = pont(L8, "Vale")
check("fraude" in _g, "[2] P: L8 ainda herda liability de terceiro — comportamento antigo")
check(sa.detect_fraud_role(N4, "Vale", AL.get("Vale")) == "vitima",
      "[3] P: N4 ainda é lido como vítima — comportamento antigo")
_g, _r = pont("Vale admitted fraud and agreed to settle", "Vale")
check(_r.get("fraude") == "R_FASE_JURIDICA_MITIGADORA",
      "[4] P: acordo ainda apaga a confissão — comportamento antigo")

print()
print("=" * 96)
print("BLOCO B — perfil V2 é cumulativo dentro do mesmo interruptor")
print("=" * 96)
with sa.shadow_fraud_roles():
    _g, _r = pont(D2, "Duke Energy", True, D2E)
    check(_r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO", "[5] R6b ativa (papel)")
    _g, _ = pont("Vale admitted fraud and agreed to settle", "Vale", True)
    check("fraude" in _g, "[6] R6d ativa (liability vence resolução)")
    _g, _r = pont(L8, "Vale", True)
    check(_r.get("fraude") == "R_LIABILITY_DE_TERCEIRO", "[7] R6e ativa (sujeito)")
check(sa.shadow_fraud_roles_ativo() is False, "[8] e o estado volta ao sair")

print()
print("=" * 96)
print("BLOCO C — regressões obrigatórias do shadow")
print("=" * 96)
_g, _r = pont(D2, "Duke Energy", True, D2E)
check("fraude" not in _g and _r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO",
      "[9] Duke #2 SC-R1")
_g, _r = pont(D3, "Duke Energy", True, D3E)
check("fraude" not in _g and _r.get("fraude") == "R_FRAUDE_PREJUIZO_DE_TERCEIRO",
      "[10] Duke #3 SC-R1 com supporting")
_g, _ = pont(CVS, "CVS Health", True, CVSE)
check("fraude" in _g, "[11] CVS scoreable TRUE")
_g, _ = pont(N4, "Vale", True)
check("fraude" in _g, "[12] N4: empresa não vira vítima")
_g, _ = pont(L8, "Vale", True)
check("fraude" not in _g, "[13] L8: não herda liability de terceiro")
_g, _r = pont("Bankruptcy Court Orders Texas to Strike Allegations In State Data "
              "Privacy Suit Against General Motors", "General Motors", True)
check("falencia" not in _g and _r.get("falencia") == "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA",
      "[14] GM/W&W: falência não reintroduzida")
_g, _ = pont(D3, "Duke Energy", True)
check("fraude" in _g,
      "[15] Duke #1/#3 sem enrichment seguem BLOCKED_BY_INPUT — sem heurística")

print()
print("=" * 96)
print("BLOCO D — P / I / IS: três modos, nunca somados")
print("=" * 96)
import reliability_shadow_diff as sd  # noqa: E402

check(sd.EVENTO_ESCOPO == "fraude", f"[16] escopo é fraude ({sd.EVENTO_ESCOPO})")
_p = ("scoreable", "scoreable", "scoreable")
check(sd.fonte_do_delta(True, True, True) == "NO_CHANGE", "[17] fonte NO_CHANGE")
check(sd.fonte_do_delta(True, False, False) == "INPUT_ONLY", "[18] fonte INPUT_ONLY")
check(sd.fonte_do_delta(True, True, False) == "SEMANTICS_ONLY", "[19] fonte SEMANTICS_ONLY")
check(sd.fonte_do_delta(True, False, True) == "INPUT_AND_SEMANTICS",
      "[20] fonte INPUT_AND_SEMANTICS")
check(sd.classificar_delta({"C": ["fraude"]}, {}, {"C": []}, {}, "C", "fraude")
      == "EVENT_REMOVED", "[21] delta EVENT_REMOVED")
check(sd.classificar_delta({"C": []}, {}, {"C": ["fraude"]}, {}, "C", "fraude")
      == "EVENT_INTRODUCED", "[22] delta EVENT_INTRODUCED")
check(sd.classificar_delta({"C": []}, {("C", "fraude"): ("R_A", "")},
                           {"C": []}, {("C", "fraude"): ("R_B", "")}, "C", "fraude")
      == "CAUSALITY_CHANGED", "[23] delta CAUSALITY_CHANGED")
check(sd.classificar_delta({"C": []}, {("C", "fraude"): ("R_A", "X")},
                           {"C": []}, {("C", "fraude"): ("R_A", "Y")}, "C", "fraude")
      == "SUBJECT_CHANGED", "[24] delta SUBJECT_CHANGED")

print()
print("=" * 96)
print("BLOCO E — qualidade causal só onde há review")
print("=" * 96)
check(sd.qualidade_causal("UNREVIEWED", True, "R_FRAUDE_ATOR_EXTERNO") == "",
      "[25] sem review não há CQ — nada de ground truth inventado")
check(sd.qualidade_causal("FALSE_POSITIVE", True, "R_FRAUDE_ATOR_EXTERNO") == "CQ-2",
      "[26] razão de papel consumida ⇒ CQ-2")
check(sd.qualidade_causal("FALSE_POSITIVE", True, "R_FRAUDE_NAO_CONFIRMADA") == "CQ-1",
      "[27] output certo por causa frágil ⇒ CQ-1")
check(sd.qualidade_causal("TRUE", False, "R_FRAUDE_ATOR_EXTERNO") == "",
      "[28] output errado não recebe CQ")

print()
print("=" * 96)
print("BLOCO F — histórico vs prospectivo e amostra insuficiente")
print("=" * 96)
check("INSUFFICIENT_SAMPLE" in sd._prec([], "P"),
      "[29] denominador zero é marcado como amostra insuficiente")
_amostra = [{"P": "scoreable", "ground_truth": "TRUE"},
            {"P": "scoreable", "ground_truth": "FALSE_POSITIVE"}]
check("1/2 = 50.0%" in sd._prec(_amostra, "P") and "INSUFFICIENT" in sd._prec(_amostra, "P"),
      f"[30] precisão sempre com numerador/denominador: {sd._prec(_amostra, 'P')}")
_dados = json.load(io.open(sd.PROSPECTIVO, encoding="utf-8")) \
    if sd.PROSPECTIVO.exists() else {"observacoes": []}
_hist = [o for o in _dados["observacoes"] if o["classe"] == "HISTORICAL_CONTROL"]
check(all(o["classe"] in ("HISTORICAL_CONTROL", "PROSPECTIVE")
          for o in _dados["observacoes"]),
      "[31] toda observação é classificada em histórica ou prospectiva")
check(any("Duke" in o["title"] or "CVS" in o["title"] for o in _hist),
      "[32] Duke e CVS estão marcados como CONTROLE, não como evidência")
check(all(o["classe"] == "HISTORICAL_CONTROL"
          for o in _dados["observacoes"] if "Duke Energy" in o["company"]),
      "[33] nenhum controle Duke é contado como out-of-sample")

print()
print("=" * 96)
print("BLOCO G — isolamento: nada ativa o shadow implicitamente")
print("=" * 96)
import subprocess  # noqa: E402
import sys  # noqa: E402

_env = {**os.environ, "SHADOW_FRAUD_ROLES": "1", "SHADOW_V2": "on",
        "RELIABILITY_SHADOW": "true"}
_r = subprocess.run([sys.executable, "-c",
                     "import semantic_audit as sa;"
                     "print('ON' if sa.shadow_fraud_roles_ativo() else 'OFF')"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=_env, timeout=300)
check(_r.stdout.strip() == "OFF", f"[34] nenhuma env var ativa ({_r.stdout.strip()})")
for f in ("risk_dashboard.py",):
    check("shadow_fraud_roles" not in io.open(f, encoding="utf-8").read(),
          f"[35] {f} não conhece o interruptor")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("shadow_fraud_roles" not in _wf, "[36] o workflow não ativa a semântica")
check(_wf.index("Run pipeline") < _wf.index("Shadow enrichment")
      < _wf.index("Commit history + dashboard"),
      "[37] ordem: produção → shadow → commit")
check("continue-on-error: true" in _wf.split("Shadow enrichment")[1][:200],
      "[38] o passo shadow é fail-open")

print()
print("=" * 96)
print("BLOCO H — side-car: compatibilidade e ausência de reescrita retroativa")
print("=" * 96)
import reliability_enrichment_sidecar as sc  # noqa: E402

check("1.0" in sc.SCHEMA_COMPATIVEIS and sc.SCHEMA_VERSION == "1.1",
      f"[39] 1.0 legível, 1.1 corrente ({sc.SCHEMA_COMPATIVEIS})")
_side = json.load(io.open("risk_enrichment_shadow.json", encoding="utf-8"))
_vers = {r.get("extractor_version") for r in _side["articles"].values()}
check("r6b.1" not in _vers,
      f"[40] nenhum registro antigo reescrito com o extractor novo: {sorted(_vers)}")
check("r6f_publicado_no_run" in io.open("reliability_enrichment_sidecar.py",
                                        encoding="utf-8").read(),
      "[41] o marco prospectivo é carimbado no side-car")
_t = Path(tempfile.mkdtemp(prefix="r6f_"))
_p = _t / "s.json"
_p.write_text(json.dumps({"schema_version": "1.0", "articles": {}}), encoding="utf-8")
os.environ["RELIABILITY_SIDECAR"] = str(_p)
import importlib  # noqa: E402
importlib.reload(sc)
sc.carregar_sidecar()
check(json.loads(_p.read_text(encoding="utf-8"))["schema_version"] == "1.0",
      "[42] ler um side-car 1.0 não o migra silenciosamente")
os.environ.pop("RELIABILITY_SIDECAR", None)
importlib.reload(sc)

print()
print("=" * 96)
print(f"RESULTADO WAVE R6f (shadow V2): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
