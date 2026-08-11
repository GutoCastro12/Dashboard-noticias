#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r6b_agency_evidence.py — 4I.2 R6b.

Dois defeitos que a R6a expôs e esta wave fecha.

AGÊNCIA. `Company employee committed fraud FOR Company` era lido como se a
companhia fosse vítima, igual a `employee defrauded Company`. O padrão antigo
exigia apenas um funcionário e um termo de fraude perto do nome da empresa —
a preposição, que é justamente o que separa as duas leituras, não era olhada.
Funcionário, executivo e preposto são agência da companhia: quando agem em
nome dela, ela não é a parte lesada.

RETENÇÃO DE EVIDÊNCIA. O early stop parava no primeiro fragmento tecnicamente
suficiente. Num caso real a metadata dizia só que houve uma prisão, enquanto o
corpo dizia quem descobriu a fraude e quem sofreu o prejuízo — e o corpo era
descartado. Suficiência técnica não é completude de evidência: enquanto o
PAPEL do evento seguir indefinido, vale procurar um único apoio limpo.

O que continua valendo: contexto sujo é pior que nenhum contexto.
"""
from __future__ import annotations

import io
import json

import reliability_enrichment_sidecar as sc
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


def papel(t, emp):
    # R6c: estes testes avaliam a semântica SHADOW, nunca a de produção.
    with sa.shadow_fraud_roles():
        return sa.detect_fraud_role(t, emp, AL.get(emp) or [emp])


def pontua(t, emp, resumo=""):
    h = {"articles": {"u1": {"title": t, "summary": resumo, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00",
                             "companies": [emp]}}, "run_count": 1}
    with sa.shadow_fraud_roles():
        rd._reclassify_only_pass(h, cfg)
    r = h["articles"]["u1"]
    return (set((r.get("events_by_company") or {}).get(emp) or []),
            {d.get("event_id"): d.get("regra")
             for d in (r.get("semantic_discards") or []) if d.get("empresa") == emp})


print("=" * 96)
print("BLOCO A — AGÊNCIA: fraude PARA a empresa não é fraude CONTRA ela")
print("=" * 96)
_A = [
    ("A1", "Employee of Vale committed fraud for Vale", "Vale", False),
    ("A2", "Vale executive orchestrated fraud on behalf of Vale", "Vale", False),
    ("A8", "Vale discovered that its executives had committed fraud for the firm",
     "Vale", False),
    ("A10", "Vale agent acting on Vale instructions committed fraud", "Vale", False),
]
for tag, t, emp, espera_vitima in _A:
    check(papel(t, emp) != "vitima",
          f"[1..4] {tag}: agiu PELA empresa ⇒ papel != vítima (obtido {papel(t, emp)!r})")
    g, _ = pontua(t, emp)
    check("fraude" in g, f"[5..8] {tag}: e o evento continua atribuído à empresa")

print()
print("=" * 96)
print("BLOCO B — AGÊNCIA: fraude CONTRA a empresa continua provando vítima")
print("=" * 96)
for tag, t, emp in (
    ("A3", "Former employee defrauded Vale in a fraud scheme", "Vale"),
    ("A4", "Employee stole 10 million from Vale in a fraud", "Vale"),
    ("A7", "Vale discovered that an employee had stolen money from Vale in a fraud", "Vale"),
):
    check(papel(t, emp) == "vitima", f"[9..11] {tag}: fraude contra a empresa ⇒ vítima")
    g, r = pontua(t, emp)
    check("fraude" not in g, f"[12..14] {tag}: e o evento não pontua contra a vítima")

print()
print("=" * 96)
print("BLOCO C — responsabilidade e contorno externo")
print("=" * 96)
_g, _ = pontua("Vale was found liable for fraud committed by employees", "Vale")
check("fraude" in _g, "[15] A6: responsabilização por fraude de funcionários continua pontuando")
check(papel("Vale was found liable for fraud committed by employees", "Vale") == "agente",
      "[16] A6: papel é AGENTE, não vítima")
check(papel("Employee defrauded Vale customers in a fraud", "Vale") != "agente",
      "[17] A5: fraude contra clientes não torna a empresa autora")
_p = sa.detect_fraud_victim_evidence(
    "Independent contractor impersonated Vale and defrauded customers in a fraud",
    "Vale", AL.get("Vale"))
check(_p.get("role") == "vitima",
      f"[18] A9: impersonação por terceiro ⇒ a empresa é alvo ({_p.get('rule')})")
check(sa.detect_agencia_em_nome_da_empresa(
    "Employee of Vale committed fraud for Vale", "Vale", AL.get("Vale")),
    "[19] o detector de agência devolve a evidência literal")
check(not sa.detect_agencia_em_nome_da_empresa(
    "Former employee defrauded Vale", "Vale", AL.get("Vale")),
    "[20] e não dispara quando a fraude é contra a empresa")

print()
print("=" * 96)
print("BLOCO D — RETENÇÃO DE EVIDÊNCIA: apoio só quando o papel falta")
print("=" * 96)
_BASE = "Third suspect arrested in Company fraud case."
_PRIM = {"method": "jsonld:description", "tier": 1, "content_hash": "p1",
         "text_excerpt": ("Sheriff said a suspect was arrested after deputies said she was "
                          "connected to an ongoing Company fraud investigation."),
         **sc.qualidade("Sheriff said a suspect was arrested after deputies said she was "
                        "connected to an ongoing Company fraud investigation.", _BASE)}
# Reproduz a ESTRUTURA do corpo real: a evidência de papel aparece longe da
# primeira menção ao termo do evento, que é o que a janela precisa alcançar.
_BODY_TXT = ("Deputies opened an ongoing fraud investigation last year. "
             "Deputies arrested a suspect after Vale representatives discovered numerous "
             "accounts were opened in the name of customers without their knowledge, "
             "costing the company 495904 in losses in the fraud. The suspect faces "
             "identity theft charges.")
_SUP = {"method": "html:paragrafos", "tier": 2, "content_hash": "s1",
        "text_excerpt": _BODY_TXT, **sc.qualidade(_BODY_TXT, _BASE)}
_sel, _mot = sc.selecionar_evidencias([_PRIM, _SUP], f"Vale fraud case. ", "Vale",
                                      "fraude", AL.get("Vale"))
check(len(_sel) == 2 and _sel[1]["method"] == "html:paragrafos",
      f"[21] papel indefinido no primary ⇒ um apoio é retido ({[f['method'] for f in _sel]})")
check(_sel[1].get("window_of_event"),
      "[22] o apoio é uma JANELA do evento, não o corpo inteiro")
check(len(_sel[1]["text_excerpt"]) <= len(_BODY_TXT),
      f"[23] a janela nunca excede o corpo ({len(_sel[1]['text_excerpt'])} ≤ {len(_BODY_TXT)})")
# num corpo longo a janela precisa CORTAR de verdade, não copiar tudo
_LONGO = ("Lorem ipsum navegação irrelevante. " * 40) + _BODY_TXT + (" Rodapé. " * 40)
_jan = [j for j in sc.janela_de_evento(_LONGO, ["fraud"]) if j]
check(_jan and max(len(j) for j in _jan) < len(_LONGO),
      f"[23b] em corpo longo a janela recorta ({max(len(j) for j in _jan)} < {len(_LONGO)})")
check(len(_sel) <= sc.MAX_EVIDENCIAS,
      f"[24] no máximo {sc.MAX_EVIDENCIAS} evidências por artigo")

_PRIM_ROLE_TXT = ("A woman was responsible for stealing identities to create fraudulent "
                  "Vale accounts, investigators said.")
_PRIM_ROLE = {"method": "meta:og:description", "tier": 1, "content_hash": "p2",
              "text_excerpt": _PRIM_ROLE_TXT,
              **sc.qualidade(_PRIM_ROLE_TXT, _BASE)}
_sel2, _m2 = sc.selecionar_evidencias([_PRIM_ROLE, _SUP], "Vale fraud case. ", "Vale",
                                      "fraude", AL.get("Vale"))
check(len(_sel2) == 1 and _sel2[0]["tier"] == 1,
      f"[25] papel explícito no primary ⇒ NENHUM apoio é buscado ({len(_sel2)})")

print()
print("=" * 96)
print("BLOCO E — contexto sujo continua rejeitado; duplicata não vira apoio")
print("=" * 96)
_SUJO_TXT = ("Menu About Contributors Blogs Podcasts Search Restructuring Perspectives "
             "Categories: Bankruptcy Sales , Chapter 11. Assine a newsletter.")
_SUJO = {"method": "html:paragrafos", "tier": 2, "content_hash": "d1",
         "text_excerpt": _SUJO_TXT, **sc.qualidade(_SUJO_TXT, _BASE)}
_sel3, _m3 = sc.selecionar_evidencias([_PRIM, _SUJO], "Vale fraud case. ", "Vale",
                                      "fraude", AL.get("Vale"))
check(len(_sel3) == 1,
      f"[26] apoio sujo NÃO é retido, mesmo com o papel indefinido ({_m3[:44]})")
check(sc.selecionar([_SUJO])[0] is None,
      "[27] e ele também não seria escolhido como primary")
_DUP = dict(_PRIM, method="meta:description", content_hash="dup")
_sel4, _ = sc.selecionar_evidencias([_PRIM, _DUP], "Vale fraud case. ", "Vale",
                                    "fraude", AL.get("Vale"))
check(len(_sel4) == 1, "[28] apoio que só repete o primary é descartado")

print()
print("=" * 96)
print("BLOCO F — schema, compatibilidade e determinismo")
print("=" * 96)
check(sc.SCHEMA_VERSION == "1.1" and sc.EXTRACTOR_VERSION.startswith("r6b"),
      f"[29] schema/extractor versionados: {sc.SCHEMA_VERSION}/{sc.EXTRACTOR_VERSION}")
check("1.0" in sc.SCHEMA_COMPATIVEIS,
      "[30] o side-car antigo continua legível")
_antigo = {"schema_version": "1.0", "extractor_version": "r5b.1",
           "policy_version": "r5b.1", "status": "OK"}
check(not sc._reaproveitavel(_antigo),
      "[31] registro de versão antiga não é reaproveitado silenciosamente")
check(sc.selecionar_evidencias([_PRIM, _SUP], "Vale fraud case. ", "Vale", "fraude",
                               AL.get("Vale"))[0]
      == sc.selecionar_evidencias([_PRIM, _SUP], "Vale fraud case. ", "Vale", "fraude",
                                  AL.get("Vale"))[0],
      "[32] mesma entrada produz exatamente a mesma seleção")
check(not sc.papel_do_evento_indefinido("qualquer texto", "Vale", "ma", AL.get("Vale")),
      "[33] a completude de evidência só se aplica a fraude — não vira regra global")

_src = io.open("reliability_enrichment_sidecar.py", encoding="utf-8").read()
for termo in ("Duke", "Dazmyn", "WRAL", "whiteandwilliams", "CVS"):
    check(termo not in _src, f"[34..38] nenhum hard-code de '{termo}' na arquitetura")
check(all(x not in _src for x in ("save_history", "merge_into_history", "--apply")),
      "[39] nenhum caminho de escrita em history")

print()
print("=" * 96)
print(f"RESULTADO WAVE R6b (agência + evidência): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
