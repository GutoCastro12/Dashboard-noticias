#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6e_subject_attribution.py — 4I.2 R6e.

QUEM FOI RESPONSABILIZADO NÃO É QUEM APARECE NA NOTÍCIA.

O trace do L8 expôs o mecanismo exato: em "Supplier was found liable for
fraud; Company settled separately" o pipeline reconhece a condenação
(EVENT EXISTS) mas nunca decide de QUEM ela é — o laço termina com
`subject = Company` só porque é a única monitorada citada, e o evento passa
sem regra alguma.

A correção não é negação cega. Apagar o evento sempre que a monitorada não
estiver ligada removeria casos legítimos em que o vínculo simplesmente não
tem construção reconhecível. Exige-se evidência POSITIVA de que existe OUTRO
responsabilizado identificável.

E o aposto não muda o sujeito: em "Supplier, a contractor of Company, was
found liable", quem responde é o Supplier.

Tudo shadow-only. Produção segue idêntica ao publicado.
"""
from __future__ import annotations

import io

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


def terc(t, emp):
    return sa.detect_liability_de_terceiro(t, emp, AL.get(emp) or [emp])


def lia(t, emp):
    return sa.detect_liability_adjudicada(t, emp, AL.get(emp) or [emp])


print("=" * 96)
print("BLOCO A — S1..S14: de quem é a responsabilização")
print("=" * 96)
_S = [
    ("S1", "Supplier Alfa was found liable for fraud. Vale settled a separate contract dispute.", "Vale", False),
    ("S2", "Supplier Alfa was found liable for fraud involving Vale products.", "Vale", False),
    ("S3", "Vale was found liable for fraud committed with Supplier Alfa.", "Vale", True),
    ("S4", "Vale and Supplier Alfa were found jointly liable for fraud.", "Vale", True),
    ("S5", "Supplier Alfa, a contractor of Vale, was found liable for fraud.", "Vale", False),
    ("S7", "Vale and its subsidiary Alpha were found liable for fraud.", "Vale", True),
    ("S8", "Supplier Alfa was found liable for defrauding Vale.", "Vale", False),
    ("S9", "Vale sued Supplier Alfa after Supplier Alfa was found liable for fraud.", "Vale", False),
    ("S11", "Omnicare and its parent company, CVS Health, were found liable for fraud.", "CVS Health", True),
    ("S12", "Vale and Alpha were named. They were found liable for fraud.", "Vale", True),
    ("S13", "Supplier Alfa was found liable for fraud. They later reached an agreement with Vale.", "Vale", False),
    ("S14", "Supplier Alfa and Beta Corp were named. They were found liable for fraud. Vale commented.", "Vale", False),
]
for tag, t, emp, espera in _S:
    g, _ = pont(t, emp, shadow=True)
    check(("fraude" in g) is espera,
          f"[1..12] {tag}: {'monitorada responde' if espera else 'não herda'} ({sorted(g)})")

print()
print("=" * 96)
print("BLOCO B — o aposto não transfere o sujeito")
print("=" * 96)
_T5 = "Supplier Alfa, a contractor of Vale, was found liable for fraud."
check(lia(_T5, "Vale") == {},
      "[13] a monitorada citada em aposto NÃO é a responsabilizada")
check(terc(_T5, "Vale").get("terceiro", "").lower().startswith("supplier"),
      f"[14] o terceiro é identificado: {terc(_T5, 'Vale').get('terceiro')!r}")
check(lia("Vale was found liable for fraud", "Vale").get("cue") == "found liable",
      "[15] sujeito imediato continua sendo reconhecido")
check(lia("Vale and Alpha were found liable for fraud", "Vale").get("cue"),
      "[16] enumeração conjunta continua ligando a monitorada")

print()
print("=" * 96)
print("BLOCO C — evidência positiva obrigatória; ausência de vínculo não apaga")
print("=" * 96)
_SEM = "Fraud was confirmed in the sector last year and Vale published a statement."
check(terc(_SEM, "Vale") == {},
      "[17] sem responsabilizado identificável, nada é descartado")
check(terc("Supplier Alfa was found liable for fraud", "Vale").get("terceiro"),
      "[18] com terceiro nomeado, o descarte é justificado")
check(terc("Vale was found liable for fraud", "Vale") == {},
      "[19] quando a própria monitorada responde, a regra não atua")

print()
print("=" * 96)
print("BLOCO D — anáfora conservadora")
print("=" * 96)
check(lia("Vale and Alpha were named. They were found liable for fraud.", "Vale").get("anafora"),
      "[20] `they` retoma a enumeração que inclui a monitorada")
check(lia("Supplier Alfa was found liable. They later reached an agreement with Vale.",
          "Vale") == {},
      "[21] `they` depois de outro responsabilizado NÃO vira a monitorada")
check(lia("Supplier Alfa and Beta Corp were named. They were found liable for fraud. "
          "Vale commented.", "Vale") == {},
      "[22] com outro sujeito responsabilizado no meio, a anáfora não vale")

print()
print("=" * 96)
print("BLOCO E — controles preservados")
print("=" * 96)
_D2E = ("WCSO identified a woman allegedly responsible for stealing victims' identities "
        "to assist in creating fraudulent Duke Energy accounts.")
_g, _r = pont("Dazmyn Person: Another person was arrested, charged in the ongoing "
              "Duke Energy fraud case", "Duke Energy", shadow=True, resumo=_D2E)
check(_r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO",
      f"[23] Duke #2 segue SC-R1 ({_r.get('fraude')})")
_D3E = ("On Tuesday deputies arrested a suspect after Duke Energy representatives "
        "discovered numerous accounts were opened in the name of customers without "
        "their knowledge, costing the company 495904 in losses in the fraud.")
_g, _r = pont("Third suspect arrested in Duke Energy fraud case", "Duke Energy",
              shadow=True, resumo=_D3E)
check(_r.get("fraude") == "R_FRAUDE_PREJUIZO_DE_TERCEIRO",
      f"[24] Duke #3 segue SC-R1 ({_r.get('fraude')})")
_g, _ = pont("A Vale employee committed fraud for Vale", "Vale", shadow=True)
check("fraude" in _g, "[25] N4 preservado")
_CVS_E = ("Omnicare and its parent company, CVS Health, have agreed to pay at least "
          "$440 million to satisfy a judgment in a case in which they were found liable "
          "for fraudulently dispensing drugs")
_g, _ = pont("Omnicare, CVS Health reach $440 million settlement in fraud case",
             "CVS Health", shadow=True, resumo=_CVS_E)
check("fraude" in _g, "[26] CVS preservado como scoreable")
_g, _r = pont("Bankruptcy Court Orders Texas to Strike Allegations In State Data "
              "Privacy Suit Against General Motors", "General Motors", shadow=True)
check("falencia" not in _g, "[27] GM/W&W intocado — F2 preservada")
_g, _ = pont("Vale was found liable for fraud and later agreed to settle", "Vale",
             shadow=True)
check("fraude" in _g, "[28] R6d preservada: liability vence resolução")

print()
print("=" * 96)
print("BLOCO F — shadow-only e ausência de hard-code")
print("=" * 96)
check(sa.shadow_fraud_roles_ativo() is False, "[29] interruptor desligado por padrão")
_gp, _ = pont("Supplier Alfa was found liable for fraud involving Vale products.", "Vale")
check("fraude" in _gp,
      "[30] em produção o comportamento antigo é preservado — nada foi ativado")
_src = io.open("semantic_audit.py", encoding="utf-8").read()
_bloco = _src.split("# ── 4I.2 R6e")[1].split("def detect_fraud_victim_evidence")[0]
_codigo = "\n".join(l for l in _bloco.splitlines() if not l.strip().startswith("#"))
for termo in ("Vale", "CVS", "Duke", "Alfa", "Omnicare", "Supplier Alfa"):
    check(termo not in _codigo, f"[31..36] nenhum hard-code de '{termo}'")
check("_SHADOW_FRAUD_ROLES else {}" in _src,
      "[37] o gate de terceiro é condicionado ao interruptor shadow")

print()
print("=" * 96)
print("BLOCO G — fail-closed do ensaio de ativação")
print("=" * 96)
import reliability_fraud_activation_rehearsal as reh  # noqa: E402

check(reh.EVENTO == "fraude",
      f"[38] o ensaio é limitado a fraude ({reh.EVENTO})")
_rsrc = io.open("reliability_fraud_activation_rehearsal.py", encoding="utf-8").read()
check("FAIL-CLOSED" in _rsrc and "fallback_P" in _rsrc,
      "[39] sem enrichment limpo, o candidato é a produção atual")
check(all(x not in _rsrc for x in ("save_history", "merge_into_history", "--apply")),
      "[40] o ensaio não escreve em history")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("activation_rehearsal" not in _wf,
      "[41] o ensaio NÃO é chamado pelo workflow")

print()
print("=" * 96)
print(f"RESULTADO WAVE R6e (subject attribution): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
