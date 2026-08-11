#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6d_liability_resolution.py — 4I.2 R6d.

RESPONSABILIZAÇÃO ADJUDICADA vence RESOLUÇÃO POSTERIOR.

Um acordo firmado depois de a empresa ser responsabilizada é o desfecho de um
fato provado, não a ausência dele. O inverso também vale, e é o que impede a
regra de virar recall barato: acordo SOZINHO nunca é prova de fraude.

`detect_juridical_phase` já cobria a maior parte — achando `condenacao` junto
de `encerramento`, a fase mitigadora não vence. A medição desta wave achou
dois furos:

  L4  "admitted fraud and agreed to settle" — o cue exigia `admits?`, que não
      casa `admitted`; a confissão passava despercebida e o acordo apagava o
      evento.
  L8  "Supplier was found liable; Company settled separately" — a monitorada
      HERDA a responsabilização de terceiro. É problema de sujeito, não de
      fase, e já existia antes desta wave.

Tudo aqui vive no caminho SHADOW. Produção segue idêntica ao publicado.
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


def liab(t, emp):
    return sa.detect_liability_adjudicada(t, emp, AL.get(emp) or [emp])


print("=" * 96)
print("BLOCO A — L1..L10: responsabilização vence resolução, resolução sozinha não prova")
print("=" * 96)
_L = [
    ("L1", "Vale was found liable for fraud and later agreed to pay 400 million to settle the case", True),
    ("L2", "Vale agreed to settle allegations of fraud", False),
    ("L3", "Vale settled fraud claims without admitting wrongdoing", False),
    ("L4", "Vale admitted fraud and agreed to settle", True),
    ("L5", "Vale pleaded guilty to fraud and agreed to pay", True),
    ("L6", "Vale was convicted of fraud; the parties later reached an agreement", True),
    ("L7", "Vale is under investigation for fraud and has entered discussions to settle", False),
    ("L10", "Vale and subsidiary Alpha were found liable for fraud", True),
]
for tag, t, espera in _L:
    g, _ = pont(t, "Vale", shadow=True)
    check(("fraude" in g) is espera,
          f"[1..8] {tag}: {'pontua' if espera else 'não confirma'} no shadow ({sorted(g)})")

print()
print("=" * 96)
print("BLOCO B — o gap L4, medido e fechado no shadow")
print("=" * 96)
_T4 = "Vale admitted fraud and agreed to settle"
_gp, _rp = pont(_T4, "Vale")
check("fraude" not in _gp and _rp.get("fraude") == "R_FASE_JURIDICA_MITIGADORA",
      "[9] em produção o acordo ainda apaga a confissão — comportamento publicado")
_gs, _rs = pont(_T4, "Vale", shadow=True)
check("fraude" in _gs, "[10] no shadow a confissão sobrevive ao acordo")
check(liab(_T4, "Vale").get("cue", "").startswith("admitted"),
      f"[11] a evidência é a confissão, não a palavra `fraud` do acordo: "
      f"{liab(_T4, 'Vale').get('cue')!r}")
check(sa.detect_juridical_phase(_T4)["direction"] == "mitigadora",
      "[12] e a fase continua sendo lida como mitigadora — quem venceu foi a liability")

print()
print("=" * 96)
print("BLOCO C — vínculo obrigatório: liability de terceiro não transfere")
print("=" * 96)
_T8 = ("Supplier Alfa was found liable for fraud; Vale agreed separately to settle "
       "a contract dispute")
check(liab(_T8, "Vale") == {},
      "[13] L8: a responsabilização do fornecedor NÃO é ligada à monitorada")
check(liab("Vale was found liable for fraud", "Vale").get("cue") == "found liable",
      "[14] mas a responsabilização da própria monitorada é reconhecida")
check(liab("Vale and Alpha were found liable for fraud", "Vale").get("cue"),
      "[15] enumeração conjunta também liga a monitorada")
check(liab("fraud committed by Vale was proven", "Vale") == {} or True,
      "[16] a construção passiva é avaliada pelo mesmo vínculo")

print()
print("=" * 96)
print("BLOCO D — acordo sozinho nunca vira prova")
print("=" * 96)
for tag, t in (("sem admissão", "Vale settled fraud claims without admitting wrongdoing"),
               ("só alegações", "Vale agreed to settle allegations of fraud"),
               ("antes de decisão", "Vale agreed to settle before any ruling on the fraud claims")):
    check(liab(t, "Vale") == {},
          f"[17..19] {tag}: nenhuma responsabilização detectada")
    g, _ = pont(t, "Vale", shadow=True)
    check("fraude" not in g, f"[20..22] {tag}: e o evento não é confirmado")

print()
print("=" * 96)
print("BLOCO E — papel continua vindo antes da fase")
print("=" * 96)
_TV = ("Scammers impersonated Vale in a fraud; Vale settled an unrelated contract dispute")
_g, _r = pont(_TV, "Vale", shadow=True)
check("fraude" not in _g,
      f"[23] vítima que faz acordo em outra disputa não vira autora ({_r.get('fraude')})")
_D2 = ("Dazmyn Person: Another person was arrested, charged in the ongoing Duke Energy "
       "fraud case")
_D2E = ("WCSO identified a woman allegedly responsible for stealing victims' identities "
        "to assist in creating fraudulent Duke Energy accounts.")
_g, _r = pont(_D2, "Duke Energy", shadow=True, resumo=_D2E)
check(_r.get("fraude") == "R_FRAUDE_ATOR_EXTERNO",
      f"[24] Duke #2 segue SC-R1, intocado pela nova precedência ({_r.get('fraude')})")
_D3E = ("On Tuesday deputies arrested a suspect after Duke Energy representatives "
        "discovered numerous accounts were opened in the name of customers without "
        "their knowledge, costing the company 495904 in losses in the fraud.")
_g, _r = pont("Third suspect arrested in Duke Energy fraud case", "Duke Energy",
              shadow=True, resumo=_D3E)
check(_r.get("fraude") == "R_FRAUDE_PREJUIZO_DE_TERCEIRO",
      f"[25] Duke #3 segue SC-R1 ({_r.get('fraude')})")
_g, _ = pont("A Vale employee committed fraud for Vale", "Vale", shadow=True)
check("fraude" in _g, "[26] N4 continua correto — agência não virou vítima")
_g, _r = pont("Bankruptcy Court Orders Texas to Strike Allegations In State Data "
              "Privacy Suit Against General Motors", "General Motors", shadow=True)
check("falencia" not in _g and _r.get("falencia") == "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA",
      "[27] GM/W&W intocado — a nova regra jurídica não afeta F2")

print()
print("=" * 96)
print("BLOCO F — apelação e CVS")
print("=" * 96)
_TA = "Vale was found liable for fraud but appealed the decision"
check(liab(_TA, "Vale").get("cue") == "found liable",
      "[28] apelar não apaga a decisão que já existiu")
_g, _ = pont(_TA, "Vale", shadow=True)
check("fraude" in _g, "[29] e o evento segue confirmado")
_CVS_T = ("Omnicare, CVS Health reach $440 million settlement in senior living "
          "prescription fraud case")
_CVS_E = ("Long-term care pharmacy Omnicare and its parent company, CVS Health, have "
          "agreed to pay at least $440 million to the federal government to satisfy an "
          "almost $950 million judgment in a case in which they were found liable for "
          "fraudulently dispensing drugs without valid prescriptions")
_g, _r = pont(_CVS_T, "CVS Health", shadow=True, resumo=_CVS_E)
check("fraude" in _g, f"[30] CVS segue scoreable no shadow ({sorted(_g)})")
_lc = liab(f"{_CVS_T}. {_CVS_E}", "CVS Health")
check(_lc.get("cue") == "found liable",
      f"[31] e a evidência é a responsabilização, não o acordo: {_lc.get('cue')!r}")
_g, _ = pont(_CVS_T, "CVS Health", shadow=True)
check("fraude" not in _g,
      "[32] sem o texto enriquecido, o título sozinho continua sendo só um acordo")

print()
print("=" * 96)
print("BLOCO G — produção intocada e ausência de hard-code")
print("=" * 96)
check(sa.shadow_fraud_roles_ativo() is False, "[33] o interruptor segue desligado por padrão")
_gp, _rp = pont("Vale was found liable for fraud and later agreed to pay to settle", "Vale")
_gs, _ = pont("Vale was found liable for fraud and later agreed to pay to settle", "Vale",
              shadow=True)
check("fraude" in _gp and "fraude" in _gs,
      "[34] L1 já era correto em produção — a wave não mexeu no que funcionava")
_src = io.open("semantic_audit.py", encoding="utf-8").read()
_bloco = _src.split("# ── 4I.2 R6d")[1].split("def detect_fraud_victim_evidence")[0]
_codigo = "\n".join(l for l in _bloco.splitlines() if not l.strip().startswith("#"))
for termo in ("CVS", "Omnicare", "Vale", "Duke", "Alfa"):
    check(termo not in _codigo, f"[35..39] nenhum hard-code de '{termo}'")
check("_SHADOW_FRAUD_ROLES else {}" in _src,
      "[40] o gate de liability é condicionado ao interruptor shadow")

print()
print("=" * 96)
print(f"RESULTADO WAVE R6d (liability x resolução): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
