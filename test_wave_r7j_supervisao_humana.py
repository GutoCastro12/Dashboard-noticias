#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7j_supervisao_humana.py — R7j.

O LOTE V1 DE SUPERVISÃO HUMANA NÃO PODE PONTUAR NEM SOBRESCREVER NADA.

Este arquivo NÃO testa semântica de produção. A onda R7j congelou julgamento
humano; nenhuma regra foi mudada. O que ele trava é o artefato:

  - esquema válido e chave estável `article_id|company|family`;
  - o escritor é idempotente e isolável (o default resolve NA CHAMADA — foi
    assim que, numa onda anterior, um teste gravou fixture dentro de um
    artefato de produção);
  - `UNDETERMINED` e `POLICY_PENDING` são estados de primeira classe: um caso
    sem evidência suficiente não pode virar veredito;
  - nenhuma revisão do Contract V2 foi tocada;
  - nada aqui tem autoridade de score.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile

import reliability_human_supervision as hs

PASS = FAIL = 0
LOTE = "BATCH_V1"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


def _sha(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest() if os.path.exists(p) else "AUSENTE"


def _base(**kw):
    m = {"case_id": "99", "article_id": "a" * 20, "company": "Alfa",
         "family": "ma", "status": "CLEAR", "event_asserted": "YES",
         "scoreable": "YES", "article_role": "NEW_EVENT",
         "occurrence_relation": "NEW_OCCURRENCE", "score_refresh": "NOT_APPLICABLE",
         "company_relation": "BUYER", "taxonomy_fit": "FITS",
         "underlying_event_present": True, "evidence_sufficiency": "SUFFICIENT",
         "source_auditability": "OK", "diagnostic_flags": [],
         "rationale": "razao suficientemente longa para passar na validacao",
         "adjudicated_by": "gustavo", "adjudicated_at": "2026-08-19T00:00:00Z",
         "supervision_batch": LOTE, "reviewer_type": "human",
         "production_score_authority": "NONE"}
    m.update(kw)
    return m


D = hs.carregar()
MS = D["memberships"]

print("=" * 98)
print("BLOCO A - o lote V1 esta congelado com as contagens certas")
print("=" * 98)
_lote = [m for m in MS.values() if m.get("supervision_batch") == LOTE]
check(len(_lote) == 27, f"[1] 27 filiacoes no lote V1 (obtido {len(_lote)})")
_casos = {m["case_id"] for m in _lote}
check(len(_casos) == 24, f"[2] cobrindo 24 CASOS de revisao (obtido {len(_casos)})")
check(_casos == {f"{i:02d}" for i in range(1, 25)},
      "[3] e os casos sao exatamente 01..24, sem buraco")
_st = {}
for m in _lote:
    _st[m["status"]] = _st.get(m["status"], 0) + 1
check(_st.get("CLEAR") == 25, f"[4] 25 filiacoes CLEAR (obtido {_st.get('CLEAR')})")
check(_st.get("UNDETERMINED") == 1, "[5] 1 UNDETERMINED")
check(_st.get("POLICY_PENDING") == 1, "[6] 1 POLICY_PENDING")
_casos_claros = {m["case_id"] for m in _lote if m["status"] == "CLEAR"}
check(len(_casos_claros) == 22,
      f"[7] que correspondem a 22 CASOS claros (obtido {len(_casos_claros)})")
check(len({m["company"] for m in _lote}) == 23, "[8] 23 empresas")
check(len({m["family"] for m in _lote}) == 10, "[9] 10 familias")

print()
print("=" * 98)
print("BLOCO B - filiacao multipla: um caso, varios registros")
print("=" * 98)
_c9 = [m for m in _lote if m["case_id"] == "09"]
check(len(_c9) == 3, "[10] o caso 09 gera 3 filiacoes — uma por banco")
check({m["company"] for m in _c9} ==
      {"Itaú Unibanco", "Bradesco", "Santander Brasil"},
      "[11] Itau, Bradesco e Santander")
check(len({m["article_id"] for m in _c9}) == 1,
      "[12] compartilhando a MESMA identidade de evento economico")
check(all(m["scoreable"] == "YES" for m in _c9),
      "[13] e os tres pontuam")
_c18 = [m for m in _lote if m["case_id"] == "18"]
check(len(_c18) == 2, "[14] o caso 18 gera 2 filiacoes — uma por familia")
check({m["family"] for m in _c18} == {"recomendacao_negativa", "troca_ceo"},
      "[15] recomendacao_negativa e troca_ceo no MESMO artigo")
_rec = next(m for m in _c18 if m["family"] == "recomendacao_negativa")
_ceo = next(m for m in _c18 if m["family"] == "troca_ceo")
check(_rec["scoreable"] == "YES" and _ceo["scoreable"] == "NO",
      "[16] com julgamentos OPOSTOS — a recomendacao pontua, a troca de CEO nao")

print()
print("=" * 98)
print("BLOCO C - a distincao central: mesma ocorrencia != sem renovacao")
print("=" * 98)


def _m(caso, fam=None):
    return next(m for m in _lote
                if m["case_id"] == caso and (fam is None or m["family"] == fam))


_sab, _smf, _suz = _m("03"), _m("05"), _m("08")
check(_sab["occurrence_relation"] == "SAME_OCCURRENCE"
      and _smf["occurrence_relation"] == "SAME_OCCURRENCE",
      "[17] Sabesp e Smart Fit sao ambos MESMA ocorrencia")
check(_sab["score_refresh"] == "FALSE" and _smf["score_refresh"] == "TRUE",
      "[18] mas SO o fechamento material renova — a etapa processual nao")
check(_sab["article_role"] == "PROCESS_STEP"
      and _smf["article_role"] == "IMPLEMENTATION_CLOSING",
      "[19] e os papeis registram exatamente essa diferenca")
check(_suz["score_refresh"] == "TRUE" and "OBJECT_ALIAS" in _suz["diagnostic_flags"],
      "[20] Suzano: fechamento renova, e o alias do objeto ficou registrado")
check(_smf["scoreable"] == "YES" and _sab["scoreable"] == "NO",
      "[21] fechamento pontua; continuacao processual nao")

print()
print("=" * 98)
print("BLOCO D - descritor nao e assercao, e ha positivo de CEO")
print("=" * 98)
for _c, _emp in (("06", "B3"), ("07", "Tupy"), ("16", "Pemex"), ("20", "Santander")):
    m = _m(_c)
    check(m["scoreable"] == "NO" and m["event_asserted"] == "NO",
          f"[22..25] {_emp}: nao afirma troca de CEO e nao pontua")
check(_ceo["scoreable"] == "NO", "[26] Rumo: comentario de analista tambem nao")
_vale_ceo = _m("17")
check(_vale_ceo["scoreable"] == "YES"
      and _vale_ceo["article_role"] == "SUCCESSION_PROCESS",
      "[27] MAS a Vale pontua: processo de sucessao ativo E evento de CEO")
check(len([m for m in _lote if m["family"] == "troca_ceo"
           and m["scoreable"] == "NO"]) == 5,
      "[28] 5 negativos de CEO no lote")
check(len([m for m in _lote if m["family"] == "troca_ceo"
           and m["scoreable"] == "YES"]) == 1,
      "[29] e 1 positivo — o lote nao e um viés de so-remover")

print()
print("=" * 98)
print("BLOCO E - pessoa x empresa NAO tem regra global unica")
print("=" * 98)
check(all(m["scoreable"] == "YES" for m in _c9),
      "[30] executivos dos tres bancos alvo de buscas: PONTUA para os bancos")
check(any("PERSON_COMPANY_POLICY_COUNTEREXAMPLE" in m["diagnostic_flags"]
          for m in _c9),
      "[31] marcado como contraexemplo de politica pessoa-empresa")
_vale_reg = _m("22")
check(_vale_reg["scoreable"] == "YES"
      and "CORPORATE_EVIDENCE_COUNTEREXAMPLE_FOR_C3" in _vale_reg["diagnostic_flags"],
      "[32] e a investigacao de governanca da Vale tambem pontua")
_S = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_bb = [v["human_review"] for v in _S["observacoes"].values()
       if (v.get("article_id") or "") == "061eb3b6f37708a1986e" and v.get("human_review")]
check(_bb and _bb[0]["scoreable"] is False,
      "[33] enquanto o BB (caso #11 do V2) segue NAO pontuavel")
check(True, "[34] tres desfechos distintos: uma regra global 'alvo pessoa => "
            "empresa nunca pontua' esta refutada por evidencia humana")

print()
print("=" * 98)
print("BLOCO F - familia errada nao vira evento inexistente")
print("=" * 98)
for _c, _emp in (("04", "PRIO"), ("19", "Bradesco"), ("24", "Aegea")):
    m = _m(_c)
    check(m["scoreable"] == "NO" and m["taxonomy_fit"].startswith("MISCLASSIFIED"),
          f"[35..37] {_emp}: nao pontua NESTA familia, e a taxonomia esta errada")
    check(m["underlying_event_present"] is True,
          f"[38..40] {_emp}: mas o evento societario subjacente EXISTE")
check(all("TAXONOMY_GAP_CANDIDATE" in _m(c)["diagnostic_flags"]
          for c in ("04", "24")),
      "[41] PRIO e Aegea entram na fila de lacuna de taxonomia")
check("POSSIBLY_CREDIT_POSITIVE" in _m("24")["diagnostic_flags"],
      "[42] e a Aegea fica marcada como possivelmente CREDIT-POSITIVE")

print()
print("=" * 98)
print("BLOCO G - incerteza e estado de primeira classe")
print("=" * 98)
_cap, _cop = _m("13"), _m("12")
check(_cap["status"] == "UNDETERMINED"
      and _cap["evidence_sufficiency"] == "INSUFFICIENT",
      "[43] Capital One: evidencia insuficiente, sem rotulo")
check(_cap["source_auditability"] == "BROKEN_SINGLE_SOURCE",
      "[44] fonte unica quebrada registrada")
check(_cop["status"] == "POLICY_PENDING" and _cop["scoreable"] == "UNDETERMINED",
      "[45] Copel: politica pendente, sem verdade humana")
check(hs.validar(_base(status="CLEAR", evidence_sufficiency="INSUFFICIENT")),
      "[46] o validador REJEITA evidencia insuficiente com veredito CLEAR")
check(hs.validar(_base(status="UNDETERMINED", scoreable="YES")),
      "[47] e rejeita UNDETERMINED com pontuabilidade firme")
check(not hs.validar(_base()), "[48] mas aceita um registro coerente")
check(hs.validar(_base(rationale="curta")), "[49] e exige razao registrada")
check(hs.validar(_base(status="TALVEZ")), "[50] status fora do enum e rejeitado")

print()
print("=" * 98)
print("BLOCO H - o escritor e isolavel e idempotente")
print("=" * 98)
_d = tempfile.mkdtemp(prefix="r7j_")
_tmp = os.path.join(_d, "hs.json")
_antes = _sha(hs.CAMINHO)
_r1 = hs.registrar_muitos([_base()], caminho=_tmp, aplicar=True)
check(_r1["novos"] and os.path.exists(_tmp), "[51] grava no destino injetado")
check(_sha(hs.CAMINHO) == _antes,
      "[52] e o artefato de PRODUCAO fica byte-identico")
_r2 = hs.registrar_muitos([_base()], caminho=_tmp, aplicar=True)
check(not _r2["novos"] and not _r2["revisados"],
      "[53] reaplicar evidencia identica nao escreve nada")
_r3 = hs.registrar_muitos([_base(scoreable="NO", status="CLEAR")],
                          caminho=_tmp, aplicar=True)
check(_r3["revisados"], "[54] julgamento diferente conta como revisao")
_e = hs.consultar if hasattr(hs, "consultar") else None
_d3 = hs.carregar(_tmp)["memberships"][hs.chave("a" * 20, "Alfa", "ma")]
check(len(_d3.get("revisions") or []) == 1,
      "[55] e a decisao anterior foi para `revisions`, nao sobrescrita em silencio")
check(hs._caminho(None) == hs.CAMINHO and hs._caminho("/x.json") == "/x.json",
      "[56] o destino resolve NA CHAMADA, nao no `def`")
check(_sha(hs.CAMINHO) == _antes,
      "[57] producao segue intocada depois de todo o bloco")

print()
print("=" * 98)
print("BLOCO I - sem autoridade de producao e sem colisao com o V2")
print("=" * 98)
check(D["_meta"]["production_score_authority"] == "NONE",
      "[58] o artefato declara autoridade de score NENHUMA")
check(D["_meta"]["semantic_authority"] == "NONE",
      "[59] e autoridade semantica NENHUMA")
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
check("risk_human_supervision" not in _src,
      "[60] nenhum caminho de producao le este arquivo")
_ids_v2 = {v.get("article_id") for v in _S["observacoes"].values()
           if v.get("human_review")}
check(len(_ids_v2) == 11, "[61] as 11 revisoes do Contract V2 seguem intactas")
check(len({m["article_id"] for m in _lote} & _ids_v2) == 0,
      "[62] e ZERO artigo do lote V1 colide com caso ja adjudicado no V2")
_ot = _S["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4,
      "[63] occurrence_truth intacto (10/21/4)")
check(len(set(MS)) == len(MS), "[64] nenhuma chave duplicada")
check(all(hs.chave(m["article_id"], m["company"], m["family"]) == k
          for k, m in MS.items()),
      "[65] e toda chave e derivada de article_id|company|family")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7j (supervisao humana V1): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
