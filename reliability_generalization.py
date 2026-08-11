#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_generalization.py — 4I.2 Reliability Learning Loop (R1).

Mede se o runtime APRENDEU A CLASSE do erro, não apenas o caso.

Contrato (§4/§6 do brief R1):
  EXACT REGRESSION  != GENERALIZATION
  Passar só nas exact regressions é memorização.

R3b acrescenta a distinção que faltava:

  BEHAVIORAL PASS     — o pipeline final produziu o output esperado.
  FAMILY-SPECIFIC PASS— a família SOB TESTE foi de fato a responsável.

Um sibling da F4 eliminado por `R_CREDOR_NAO_HERDA_EVENTO_DO_DEVEDOR` tem
output correto e NÃO testa F4. Sem essa separação, uma família podia receber
`GENERALIZED_ON_TEST_SET` por acidente, porque outra regra resolveu os
exemplos antes — exatamente o que o objetivo do framework proíbe.

A proveniência NÃO reimplementa semântica: lê `semantic_discards` e
`event_assessments`, que o próprio pipeline já grava, onde `regra` é o
`attribution_rule` da decisão vencedora para aquele par (empresa, evento).

Cada fixture passa pelo MESMO pipeline semântico de produção
(`_reclassify_only_pass`) — não há segunda implementação da semântica aqui.

Uso:
    python reliability_generalization.py            # relatório
    python reliability_generalization.py --json     # saída legível por máquina
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import risk_dashboard as rd

FAMILIES = Path("test_fixtures_reliability/error_families.json")
OUTDIR = Path("out_reliability")

# Estados possíveis de uma família.
GENERALIZED = "GENERALIZED_ON_TEST_SET"
COVERED = "BEHAVIORALLY_COVERED_ON_TEST_SET"   # R3b §7
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED_BY_INPUT"
UNRESOLVED = "UNRESOLVED"

# Resultado de UM caso, já com proveniência (R3b §6).
EXERCISED_PASS = "EXERCISED_PASS"                       # a família disparou e acertou
PASS_OTHER_RULE = "BEHAVIORAL_PASS_OTHER_RULE"          # acertou, mas quem resolveu foi outra
CASE_FAIL = "FAIL"                                      # output errado
CASE_BLOCKED = "BLOCKED_BY_INPUT"                       # verdade conhecida, sinal ausente
NOT_APPLICABLE = "NOT_APPLICABLE"                       # negativo: não se espera disparo

# Uma exact + UM sibling exercitado é memorização com testemunha. Exigimos
# pelo menos DOIS siblings que realmente exercitem a regra — é o mínimo que
# distingue "aprendeu a classe" de "aprendeu uma frase". Não exigimos que
# TODOS os siblings exercitem: um exemplo redundante coberto por outra regra
# válida não pode impedir a generalização (§7).
MIN_EXERCISED_SIBLINGS = 2
SEM_REGISTRO = "SEM_REGISTRO_SEMANTICO"


def _pontua(cfg: dict, title: str, company: str) -> tuple[set, dict]:
    """Roda o pipeline semântico REAL sobre um título e devolve os eventos que
    ficariam scoreable para a empresa, mais o registro completo — de onde sai
    a proveniência, sem duplicar a semântica."""
    hist = {"articles": {"u1": {
        "title": title, "summary": "", "source": "reliability-fixture",
        "domain": "exemplo.com", "pub_ts": 1786000000,
        "pub_iso": "2026-08-07 04:00", "companies": [company]}},
        "run_count": 1}
    rd._reclassify_only_pass(hist, cfg)
    rec = hist["articles"]["u1"]
    return set(rd.event_ids_for(rec, company) or []), rec


def _proveniencia(rec: dict, company: str, event_id: str) -> dict:
    """Quem decidiu este par (empresa, evento), segundo o próprio pipeline.

    `semantic_discards` guarda a regra vencedora de cada evento REMOVIDO;
    `event_assessments` guarda o laudo completo. Nada é inferido aqui.
    """
    p = {"final_rule": "", "motivo": "", "subject_company": "",
         "relation_type": "", "event_scope": "", "event_id_corrigido": ""}
    for d in (rec.get("semantic_discards") or []):
        if d.get("empresa") == company and d.get("event_id") == event_id:
            p.update(final_rule=d.get("regra") or "",
                     motivo=(d.get("motivo") or "")[:140],
                     subject_company=d.get("subject_company") or "",
                     event_id_corrigido=d.get("event_id_corrigido") or "")
    for a in (rec.get("event_assessments") or []):
        if a.get("company") == company and a.get("event_id") == event_id:
            p.setdefault("relation_type", "")
            p["relation_type"] = a.get("relation_type") or p["relation_type"]
            p["event_scope"] = a.get("event_scope") or p["event_scope"]
            if not p["final_rule"]:
                p["final_rule"] = a.get("attribution_rule") or ""
    return p


def _check(cfg: dict, caso: dict, regras_familia: set, declarado: str) -> dict:
    """Avalia UM caso em dois níveis: comportamental e específico da família."""
    got, rec = _pontua(cfg, caso["title"], caso["company"])
    proibido = caso.get("forbidden")
    exigido = caso.get("required")
    alvo = proibido if (proibido and proibido != "__none__") else exigido
    r = {"title": caso["title"], "company": caso["company"], "alvo": alvo or "",
         "got": sorted(got), "esperado": "ausente" if proibido else "presente",
         "family_fired": False, "outcome": NOT_APPLICABLE,
         "final_rule": "", "motivo": "", "subject_company": "",
         "relation_type": "", "event_scope": ""}

    if not alvo:                                     # `forbidden: __none__`
        r["passou"] = True
        r["detalhe"] = "sem asserção de evento"
        return r

    prov = _proveniencia(rec, caso["company"], alvo)
    r.update({k: prov[k] for k in ("final_rule", "motivo", "subject_company",
                                   "relation_type", "event_scope")})
    r["family_fired"] = bool(prov["final_rule"] and prov["final_rule"] in regras_familia)

    if proibido and proibido != "__none__":
        r["passou"] = proibido not in got
        r["detalhe"] = f"proibido={proibido} obtido={sorted(got)}"
        if not r["passou"]:
            r["outcome"] = CASE_BLOCKED if declarado in (BLOCKED, UNRESOLVED) else CASE_FAIL
        elif r["family_fired"]:
            r["outcome"] = EXERCISED_PASS
        else:
            r["outcome"] = PASS_OTHER_RULE
            if not r["final_rule"]:
                # o evento não chegou à semântica: sumiu antes (classificação,
                # detect_companies, mention_role). Gap explícito, não inferência.
                r["final_rule"] = SEM_REGISTRO
    else:
        r["passou"] = exigido in got
        r["detalhe"] = f"exigido={exigido} obtido={sorted(got)}"
        # em controle negativo o esperado é a família NÃO disparar (§10)
        r["outcome"] = NOT_APPLICABLE if r["passou"] else CASE_FAIL
    return r


def _validar_fixtures(cfg: dict, dados: dict) -> list:
    """Empresa fora da watchlist contamina a medição: `event_ids_for` cai no
    fallback legado F1 e devolve os `event_ids` globais. Falha alto em vez de
    produzir um número sem significado."""
    nomes = {w["name"] for w in (cfg.get("watchlist") or [])}
    ruins = []
    for fam in dados["families"]:
        for b in ("exact_regressions", "semantic_siblings", "negative_controls"):
            for c in (fam.get(b) or []):
                if c["company"] not in nomes:
                    ruins.append(f"{fam['family_id']}/{b}: {c['company']!r}")
    return ruins


def avaliar(cfg: dict | None = None) -> dict:
    cfg = cfg or rd.load_config("config_risco.yaml")
    dados = json.loads(FAMILIES.read_text(encoding="utf-8"))
    ruins = _validar_fixtures(cfg, dados)
    if ruins:
        raise SystemExit("FIXTURES INVÁLIDAS — empresa fora da watchlist: "
                         + "; ".join(ruins))
    resultado = {"families": [], "review_queue": dados.get("review_queue", []),
                 "holdout_start_ts": dados["_meta"]["holdout_start_ts"]}

    for fam in dados["families"]:
        declarado = fam.get("status", "")
        regras = set(fam.get("rule_ids") or [])
        linhas = {}
        for bucket in ("exact_regressions", "semantic_siblings", "negative_controls"):
            linhas[bucket] = [_check(cfg, c, regras, declarado)
                              for c in (fam.get(bucket) or [])]

        def taxa(b):
            v = linhas[b]
            return (sum(1 for x in v if x["passou"]), len(v))

        def exercitados(b):
            return sum(1 for x in linhas[b] if x["outcome"] == EXERCISED_PASS)

        ex, exn = taxa("exact_regressions")
        sb, sbn = taxa("semantic_siblings")
        ng, ngn = taxa("negative_controls")
        ex_f, sb_f = exercitados("exact_regressions"), exercitados("semantic_siblings")
        # Disparo INDEVIDO só existe em negativo do tipo `required`: ali o
        # evento tem de permanecer, e a família ter disparado significa que
        # ela o removeu. Em negativo `forbidden` ("não pode voltar") o
        # disparo da família é justamente o comportamento correto.
        neg_fired = sum(1 for x, c in zip(linhas["negative_controls"],
                                          fam.get("negative_controls") or [])
                        if x["family_fired"] and c.get("required"))

        # NÍVEL 1 — comportamental: o output final está correto?
        if declarado in (BLOCKED, UNRESOLVED):
            comportamental = declarado
        elif exn and ex == exn and (sbn == 0 or sb == sbn) and (ngn == 0 or ng == ngn):
            comportamental = COVERED if sbn else PARTIAL
        else:
            comportamental = PARTIAL

        # NÍVEL 2 — específico da família: quem resolveu foi a regra sob teste?
        # `GENERALIZED_ON_TEST_SET` passa a exigir EVIDÊNCIA de que a própria
        # família foi exercitada, não apenas que o output final está certo.
        if comportamental == COVERED and ex_f >= 1 and sb_f >= MIN_EXERCISED_SIBLINGS \
                and neg_fired == 0:
            status = GENERALIZED
        else:
            status = comportamental

        resultado["families"].append({
            "family_id": fam["family_id"], "name": fam["name"],
            "invariant": fam["invariant"], "status": status,
            "behavioral_status": comportamental,
            "exact": [ex, exn], "siblings": [sb, sbn], "negatives": [ng, ngn],
            "exercised_exact": [ex_f, exn], "exercised_siblings": [sb_f, sbn],
            "negatives_family_fired": neg_fired,
            "rule_ids": sorted(regras),
            "detalhes": linhas, "notes": fam.get("notes", ""),
        })
    return resultado


_MARCA = {EXERCISED_PASS: "✔", PASS_OTHER_RULE: "~", CASE_FAIL: "✗",
          CASE_BLOCKED: "⛔", NOT_APPLICABLE: "·"}


def imprimir(res: dict) -> int:
    print("=" * 96)
    print("RELIABILITY GENERALIZATION — o sistema aprendeu a CLASSE ou só o CASO?")
    print("=" * 96)
    tot = {"exact": [0, 0], "siblings": [0, 0], "negatives": [0, 0],
           "exercised_exact": [0, 0], "exercised_siblings": [0, 0]}
    por_status, por_comport = {}, {}
    for f in res["families"]:
        for k in tot:
            tot[k][0] += f[k][0]
            tot[k][1] += f[k][1]
        por_status[f["status"]] = por_status.get(f["status"], 0) + 1
        por_comport[f["behavioral_status"]] = por_comport.get(f["behavioral_status"], 0) + 1
        marca = {GENERALIZED: "✅", COVERED: "🟡", PARTIAL: "⚠️ ",
                 BLOCKED: "⛔", UNRESOLVED: "⛔"}.get(f["status"], "  ")
        print(f"\n{marca} {f['family_id']} — {f['name']}   [{f['status']}]")
        print(f"     invariante: {f['invariant'][:88]}")
        print(f"     comportamental : exact {f['exact'][0]}/{f['exact'][1]} · "
              f"siblings {f['siblings'][0]}/{f['siblings'][1]} · "
              f"negatives {f['negatives'][0]}/{f['negatives'][1]}  [{f['behavioral_status']}]")
        print(f"     família        : exact {f['exercised_exact'][0]}/{f['exercised_exact'][1]} · "
              f"siblings {f['exercised_siblings'][0]}/{f['exercised_siblings'][1]} · "
              f"disparos indevidos em negativos {f['negatives_family_fired']}"
              f"   regras={','.join(f['rule_ids']) or '—'}")
        for bucket, rotulo in (("exact_regressions", "exact"),
                               ("semantic_siblings", "sibling"),
                               ("negative_controls", "negativo")):
            for x in f["detalhes"][bucket]:
                if x["outcome"] in (NOT_APPLICABLE, EXERCISED_PASS):
                    continue                     # o esperado; não polui o log
                print(f"       {_MARCA.get(x['outcome'], '?')} {rotulo}: {x['title'][:62]}")
                print(f"          {x['outcome']} · regra={x['final_rule'] or '—'} · {x['detalhe']}")
    print()
    print("=" * 96)
    print("  BEHAVIORAL GENERALIZATION — o output final está correto?")
    print(f"    EXACT REGRESSION RATE     : {tot['exact'][0]}/{tot['exact'][1]}")
    print(f"    SEMANTIC SIBLING RATE     : {tot['siblings'][0]}/{tot['siblings'][1]}")
    print(f"    NEGATIVE CONTROL PRESERV. : {tot['negatives'][0]}/{tot['negatives'][1]}")
    print(f"    famílias BEHAVIORALLY_COVERED : {por_comport.get(COVERED, 0)}")
    print()
    print("  FAMILY-SPECIFIC GENERALIZATION — a família sob teste foi exercitada?")
    print(f"    EXERCISED EXACT           : {tot['exercised_exact'][0]}/{tot['exercised_exact'][1]}")
    print(f"    EXERCISED SIBLINGS        : {tot['exercised_siblings'][0]}/{tot['exercised_siblings'][1]}")
    print(f"    famílias GENERALIZED      : {por_status.get(GENERALIZED, 0)}"
          f"   (mínimo exigido: 1 exact + {MIN_EXERCISED_SIBLINGS} siblings exercitados)")
    print(f"    famílias só COVERED       : {por_status.get(COVERED, 0)}")
    print(f"    famílias parciais         : {por_status.get(PARTIAL, 0)}")
    print(f"    famílias bloqueadas/abertas: "
          f"{por_status.get(BLOCKED, 0) + por_status.get(UNRESOLVED, 0)}")
    print(f"  review queue (não classificado automaticamente): {len(res['review_queue'])}")
    print("=" * 96)
    return 0


def main() -> int:
    res = avaliar()
    OUTDIR.mkdir(exist_ok=True)
    (OUTDIR / "generalization_report.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    return imprimir(res)


if __name__ == "__main__":
    raise SystemExit(main())
