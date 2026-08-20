#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7o_reprodutor_ocorrencia.py — 4I.2 R7o.

O REPRODUTOR TEM DE CONCORDAR COM A PRODUÇÃO, NÃO COM O QUE ELA DEVERIA FAZER.

Esta onda é MEDIÇÃO. O reprodutor instrumenta `assign_occurrence_clusters` e
roda a `build_evolution` REAL — quem decide continua sendo a produção. Por isso
a equivalência aqui não é perseguida por ajuste: ela é estrutural, e o teste a
AFIRMA para que uma reimplementação futura não entre por acidente.

O QUE ESTE ARQUIVO DELIBERADAMENTE NÃO FAZ

Não codifica a preferência humana como expectativa de produção. Onde humano e
produção divergem, o teste registra a DIVERGÊNCIA como fato medido. Um teste
que exigisse o comportamento humano transformaria medição em implementação
disfarçada, e a próxima onda perderia a linha de base.

ESTABILIDADE TEMPORAL

O cron acrescenta artigos. Contagens absolutas de ocorrência não são invariante
— seriam um calendário. O que se afirma é igualdade contra a produção sobre o
MESMO insumo, e propriedades estruturais que não dependem do tamanho do acervo.
"""
from __future__ import annotations

import copy
import io
import json

import reliability_human_supervision as hs
import reliability_occurrence_reproducer as rp
import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


cfg = rd.load_config("config_risco.yaml")
H = json.load(io.open("risk_history.json", encoding="utf-8"))
R = rp.reproduzir()
EQ = rp.equivalencia(R)
INV = rp.inventario(R)
OCC = {(o["company"], o["family"]): o for o in R["ocorrencias"]}

print("=" * 98)
print("BLOCO A - equivalencia com a producao")
print("=" * 98)
check(EQ["ok"], f"[1] sem divergencia estrutural ({EQ['problemas']})")
_evo = rd.build_evolution(copy.deepcopy(H), cfg)
_chaves_prod = {(l["company"], e.get("_occ_key") or e.get("event_id"))
                for l in _evo for e in (l.get("events") or [])}
_chaves_rep = {(o["company"], o["occ_key"]) for o in R["ocorrencias"]}
check(_chaves_prod == _chaves_rep,
      f"[2] mesmo conjunto de ocorrencias ({len(_chaves_prod)})")
_score_prod = {l["company"]: l["total_score"] for l in _evo}
check(_score_prod == {k: v["total_score"] for k, v in R["empresas"].items()},
      "[3] score por empresa identico ao da producao")
check({l["company"]: l["status"] for l in _evo}
      == {k: v["status"] for k, v in R["empresas"].items()},
      "[4] status por empresa identico")
check(sum(_score_prod.values()) == EQ["score_total_producao"],
      f"[5] score total identico ({EQ['score_total_producao']})")
for _i, o in enumerate(R["ocorrencias"][:0] or [], start=6):
    pass
check(all(o["representante_article_id"] in set(o["todos_article_ids"])
          for o in R["ocorrencias"] if o["todos_article_ids"]),
      "[6] o representante sempre pertence a propria ocorrencia")
check(all(o["n_membros"] >= 1 for o in R["ocorrencias"]),
      "[7] toda ocorrencia tem ao menos um membro clusterizado")

print()
print("=" * 98)
print("BLOCO B - o reprodutor nao tem autoridade")
print("=" * 98)
check(R["_meta"]["production_score_authority"] == "NONE",
      "[8] autoridade de score NENHUMA")
check(R["_meta"]["semantic_authority"] == "NONE", "[9] autoridade semantica NENHUMA")
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
check("reliability_occurrence_reproducer" not in _src,
      "[10] nenhum caminho de producao importa o reprodutor")
_sem = io.open("semantic_audit.py", encoding="utf-8").read()
check("reliability_occurrence_reproducer" not in _sem,
      "[11] nem o motor semantico")
_rsrc = io.open("reliability_occurrence_reproducer.py", encoding="utf-8").read()
check('io.open(' in _rsrc and '"w"' not in _rsrc.split('"""', 2)[2],
      "[12] o modulo nao abre nada para escrita")
check("assign_occurrence_clusters" in _rsrc and "def assign_occurrence" not in _rsrc,
      "[13] ele INSTRUMENTA a funcao real — nao reimplementa o agrupamento")

print()
print("=" * 98)
print("BLOCO C - a arquitetura atual, medida (linha de base)")
print("=" * 98)
_multi = [o for o in R["ocorrencias"] if o["n_membros"] > 1]
check(INV["ocorrencias"] == len(R["ocorrencias"]),
      f"[14] {INV['ocorrencias']} ocorrencias no acervo atual")
check(INV["representante_e_o_mais_recente"] == 0,
      f"[15] em NENHUMA ocorrencia multi-artigo o representante e o mais recente "
      f"({INV['representante_e_o_mais_recente']}/{len(_multi)}) — a producao "
      f"ancora no PRIMEIRO artigo do grupo")
check(all(o["representante_e_o_mais_antigo"] for o in _multi),
      "[16] o representante e sempre o membro mais antigo")
check(all(o["ancora_date"] == o["representante_date"] for o in _multi),
      "[17] logo a ancora de decaimento e a data do PRIMEIRO artigo — cobertura "
      "posterior NAO renova recencia na arquitetura atual")
from collections import Counter
_c = Counter((o["company"], o["family"]) for o in R["ocorrencias"])
check(all(v == 1 for v in _c.values()),
      f"[18] cada empresa x familia tem UMA ocorrencia pontuavel "
      f"({sum(1 for v in _c.values() if v > 1)} com mais de uma)")
check(INV["span_gt_90d"] == 0,
      f"[19] nenhuma ocorrencia excede a janela de 90 dias ({INV['span_gt_90d']})")

print()
print("=" * 98)
print("BLOCO D - ancoras humanas: DIVERGENCIA REGISTRADA, NAO CORRIGIDA")
print("=" * 98)
_MS = hs.carregar()["memberships"]
_ANC = [("01", "d481295fca29979d04cb", "Tok&Stok", "recuperacao_judicial", "FALSE"),
        ("03", "b1fc685f3f7d47d63a2c", "Sabesp", "ma", "FALSE"),
        ("05", "601562a812028d796edb", "Smart Fit", "ma", "TRUE"),
        ("08", "6f4ae9a2a77fb2678d11", "Suzano", "ma", "TRUE"),
        ("11", "a3de08f211694408beb1", "Engie Brasil", "follow_on", "FALSE"),
        ("21", "2d16863bb425f80dc9c3", "ISA Energia Brasil", "follow_on", "FALSE")]
_div_ref = []
for _caso, _aid, _emp, _fam, _ref_h in _ANC:
    _m = _MS.get(f"{_aid}|{_emp}|{_fam}")
    _o = OCC.get((_emp, _fam))
    if _o is None:
        continue
    _prod_ref = "FALSE" if _o["representante_e_o_mais_antigo"] else "TRUE"
    if _prod_ref != _ref_h:
        _div_ref.append((_caso, _emp, _ref_h, _prod_ref))
check(len(_div_ref) == 2,
      f"[20] exatamente 2 divergencias de RENOVACAO medidas ({_div_ref})")
check({d[1] for d in _div_ref} == {"Smart Fit", "Suzano"},
      "[21] e sao os dois FECHAMENTOS MATERIAIS — o humano quer renovacao, a "
      "arquitetura atual nao sabe renovar")
check(OCC[("Tok&Stok", "recuperacao_judicial")]["representante_title"]
      .startswith("Tok&Stok: Justiça aceita"),
      "[22] Tok&Stok: a producao JA representa pela aceitacao da RJ, nao pela "
      "materia de consequencia — concorda com o humano")
check(OCC[("Engie Brasil", "follow_on")]["representante_date"] == "2026-06-11",
      "[23] Engie: ancorada no anuncio de 11/06, nao na recapitulacao — concorda")
check(OCC[("ISA Energia Brasil", "follow_on")]["representante_title"]
      .lower().startswith("isa energia lança oferta"),
      "[24] ISA: o anuncio ORIGINAL existe no acervo e e o representante — a "
      "suspeita de recall nao se confirma para este caso")
_sant = OCC.get(("Santander Brasil", "troca_ceo"))
check(_sant is None,
      "[25] Santander: sem ocorrencia de CEO — o alinhamento publicado removeu "
      "a filiacao viva, e o controle se confirma")

print()
print("=" * 98)
print("BLOCO E - limitacao HONESTA do reprodutor")
print("=" * 98)
_abs_total = sum(o["n_absorvidos"] for o in R["ocorrencias"])
_abs_sem_id = sum(1 for o in R["ocorrencias"] for a in o["absorvidos"]
                  if not a["article_id"])
check(_abs_total > 0, f"[26] a producao absorve artigos como corroboracao "
                      f"({_abs_total} no acervo atual)")
check(_abs_sem_id > 0,
      f"[27] e parte deles NAO e resolvivel por article_id ({_abs_sem_id}) — a "
      "fusao guarda so dominio e URL de redirect, entao o reprodutor declara a "
      "lacuna em vez de fingir cobertura total")
check(all("article_id" in a and "domain" in a
          for o in R["ocorrencias"] for a in o["absorvidos"]),
      "[28] mas todo absorvido aparece, com o que existe dele")

print()
print("=" * 98)
print("BLOCO F - o que esta onda NAO pode ter tocado")
print("=" * 98)
_S = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ot = _S["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[29] occurrence_truth intacto (10/21/4)")
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[30] supervisao humana intacta (27/24)")
check("R_TROCA_CEO_SEM_ASSERCAO" in _sem,
      "[31] a guarda de CEO segue publicada e intocada")
check("def assign_occurrence_clusters" in _src and "def build_evolution" in _src,
      "[32] clustering e build_evolution seguem no lugar, sem reescrita")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7o (reprodutor de ocorrencia): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
