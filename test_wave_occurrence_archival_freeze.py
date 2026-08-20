#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_archival_freeze.py — acervo que cresce, experimento que não.

O congelamento da V1 fixou o hash de um manifesto derivado do acervo humano
INTEIRO. Como o acervo sempre foi feito para crescer, adjudicar Petrobras
fazia V1, V2 e V3 falharem a própria verificação — sem que nenhum deles tivesse
mudado. Erro meu, no desenho da V1.

Esta suíte prova as quatro coisas que tornam a correção legítima:

1. O experimento não derivou. Com o acervo em 7/17/1 e em 10/21/4, a população
   congelada tem os MESMOS 17 alvos e os MESMOS `article_ref`. Sem isso, o
   problema seria drift real e a resposta certa seria outra.

2. O recomputo direto DERIVA e a verificação arquival NÃO. Se as duas
   coincidissem, a camada arquival não estaria fazendo nada.

3. Mutar o snapshot REPROVA. Um verificador que aceita qualquer entrada
   histórica não verifica nada — inclusive plantar Petrobras no snapshot tem
   de falhar.

4. Continua havendo UM acervo canônico. A correção não pode ser "criar um
   segundo lugar de verdade", que era a alternativa mais fácil e a pior.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile

import reliability_occurrence_archival_source as arq
import reliability_occurrence_archival_verifier as av
import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_truth as ot

PASS = FAIL = 0
VIVO = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
SNAP = av.carregar_snapshot()
HIST = "82cda660cdece064"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _h(o):
    return hashlib.sha256(
        json.dumps(o, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _com_petrobras(base):
    """Acervo temporário com a adjudicação da Petrobras, em memória."""
    W = copy.deepcopy(base)
    P = [ot.criar_ocorrencia(W, company="Petrobras", event_id="ma",
                             material_event_date=None, family_identity={"t": i},
                             adjudicated_by="teste",
                             adjudicated_at_iso="2026-08-16T00:00:00Z")
         for i in range(3)]
    for ref, o in [("tB", P[0]), ("tD", P[0]), ("tA", P[1]), ("tC", P[2])]:
        ot.adicionar_membership(W, occurrence_truth_id=o, article_ref_=ref,
                                company="Petrobras", event_id="ma",
                                occurrence_novelty="UNDETERMINED",
                                adjudicated_by="teste",
                                adjudicated_at_iso="2026-08-16T00:00:00Z")
    return W


n = 1
print("=" * 98)
print("1. O EXPERIMENTO NÃO DERIVOU — SÓ O MANIFESTO É LARGO DEMAIS")
print("=" * 98)
_a = v3.alvos_com_verdade(SNAP, historico=arq.HISTORICO)
_b = v3.alvos_com_verdade(VIVO, historico=arq.HISTORICO)
check(len(_a) == 17 and len(_b) == 17,
      f"[{n}] 17 alvos pela verdade congelada e 17 pela verdade viva "
      f"({len(_a)}/{len(_b)})"); n += 1
check([x["article_ref"] for x in _a] == [x["article_ref"] for x in _b],
      f"[{n}] e são os MESMOS `article_ref` — a população congelada é idêntica, "
      "logo isto é largura de manifesto e não drift de experimento"); n += 1
check([sorted(x["linkage_aceitaveis"]) for x in _a]
      == [sorted(x["linkage_aceitaveis"]) for x in _b],
      f"[{n}] e a verdade de pertinência de cada alvo também não mudou"); n += 1

print()
print("=" * 98)
print("2. O RECOMPUTO DIRETO DERIVA; A VERIFICAÇÃO ARQUIVAL NÃO")
print("=" * 98)
check(_h(v1.manifesto_desenvolvimento(SNAP)) == HIST,
      f"[{n}] snapshot → `{HIST}`"); n += 1
_vivo_direto = _h(v1.manifesto_desenvolvimento(VIVO))
check(_vivo_direto != HIST,
      f"[{n}] acervo vivo (10/21/4) → `{_vivo_direto}` — DERIVA, como esperado"); n += 1
_rel = av.verificar_historico()
check(_rel["dev_manifest_hash"] == HIST and _rel["ok"],
      f"[{n}] verificação arquival → `{_rel['dev_manifest_hash']}` — ESTÁVEL. "
      "Se coincidisse com o recomputo, a camada não faria nada"); n += 1
for nome in ("V1", "V2", "V3"):
    check(_rel["por_experimento"][nome]["ok"],
          f"[{n}] {nome} verifica contra o snapshot com o hash publicado"); n += 1

print()
print("=" * 98)
print("3. CRESCIMENTO FUTURO NÃO QUEBRA A VERIFICAÇÃO HISTÓRICA")
print("=" * 98)
_maior = _com_petrobras(VIVO)
check(len(ot.ocorrencias(_maior)) == 13,
      f"[{n}] acervo temporário ainda maior "
      f"({len(ot.ocorrencias(_maior))} ocorrências)"); n += 1
check(_h(v1.manifesto_desenvolvimento(_maior)) != HIST,
      f"[{n}] recomputo direto deriva de novo"); n += 1
check(av.verificar_historico()["dev_manifest_hash"] == HIST,
      f"[{n}] e a verificação arquival segue em `{HIST}` — o acervo pode "
      "crescer indefinidamente"); n += 1

print()
print("=" * 98)
print("4. MUTAR O SNAPSHOT REPROVA")
print("=" * 98)
_tmp = tempfile.mkdtemp(prefix="snapmut")
for nome, mut in [
        ("remover uma ocorrência",
         lambda s: s["occurrence_truth"]["occurrences"].pop(
             sorted(s["occurrence_truth"]["occurrences"])[0])),
        ("remover uma pertinência",
         lambda s: s["occurrence_truth"]["memberships"].pop()),
        ("remover a relação",
         lambda s: s["occurrence_truth"]["relations"].pop()),
        ("plantar Petrobras no histórico",
         lambda s: s["occurrence_truth"]["occurrences"].update(
             {"ma:petrobras:deadbeef0000":
              {"occurrence_truth_id": "ma:petrobras:deadbeef0000",
               "company": "Petrobras", "event_id": "ma",
               "material_event_date": None, "family_identity": {},
               "adjudicated_by": "x", "adjudicated_at_iso": "x", "nota": ""}})),
]:
    s = copy.deepcopy(SNAP)
    mut(s)
    p = os.path.join(_tmp, f"{abs(hash(nome))}.json")
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(s, ensure_ascii=False, indent=1, sort_keys=True))
    r = av.verificar_historico(p)
    check(not r["ok"], f"[{n}] {nome} → REPROVA ({len(r['problemas'])} problemas)")
    n += 1
check(av.verificar_historico()["ok"],
      f"[{n}] e o snapshot real segue passando depois das mutações"); n += 1
check(av.SNAPSHOT_SHA256 == av.checksum_snapshot(),
      f"[{n}] checksum do snapshot fixado à mão bate "
      f"(`{av.SNAPSHOT_SHA256}`)"); n += 1
_o = av.SNAPSHOT_SHA256
try:
    av.SNAPSHOT_SHA256 = "0" * 16
    check(not av.verificar_historico()["ok"],
          f"[{n}] e mutar o checksum fixado REPROVA — a fixação tem efeito")
    n += 1
finally:
    av.SNAPSHOT_SHA256 = _o

print()
print("=" * 98)
print("5. UM ÚNICO ACERVO CANÔNICO, E ELE CRESCEU")
print("=" * 98)
_ns = [k for k in VIVO if "occurrence_truth" in k]
check(_ns == ["occurrence_truth"],
      f"[{n}] só um namespace de verdade viva ({_ns}) — nenhum "
      "`occurrence_truth_devset_v2` foi criado"); n += 1
check(SNAP["_meta"]["role"] == "HISTORICAL_FREEZE_INPUT"
      and SNAP["_meta"]["current_authority"] == "NONE",
      f"[{n}] o snapshot se declara entrada histórica sem autoridade atual"); n += 1
check("RECONSTRUIDO" in SNAP["_meta"]["provenance"],
      f"[{n}] e admite ter sido reconstruído agora, não commitado antes da V1 "
      "— proveniência inventada seria pior que nenhuma"); n += 1

print()
print("=" * 98)
print("6. A VERDADE DA PETROBRAS ESTÁ EXATA")
print("=" * 98)
_p = [m for m in ot.memberships_ativas(VIVO) if m["company"] == "Petrobras"]
_ids = {m["occurrence_truth_id"] for m in _p}
check(len(ot.ocorrencias(VIVO)) == 10 and len(ot.memberships(VIVO)) == 21
      and len(ot.relacoes(VIVO)) == 4,
      f"[{n}] acervo vivo em 10/21/4"); n += 1
check(len(_p) == 4 and len(_ids) == 3,
      f"[{n}] 4 pertinências em 3 ocorrências ({len(_p)}/{len(_ids)})"); n += 1
_arg = [m for m in _p if "argonauta" in m["article_ref"].lower()
        or "rad.cvm.gov.br" in m["article_ref"]]
check(len(_arg) == 2 and len({m["occurrence_truth_id"] for m in _arg}) == 1,
      f"[{n}] B e D compartilham UMA ocorrência — a igualdade está no ID, sem "
      "relação SAME redundante"); n += 1
_ls = next(m for m in _p if "lightsource" in m["article_ref"])
_ws = next(m for m in _p if "wilson" in m["article_ref"])
check(_ls["occurrence_truth_id"] != _arg[0]["occurrence_truth_id"]
      and _ws["occurrence_truth_id"] != _arg[0]["occurrence_truth_id"]
      and _ls["occurrence_truth_id"] != _ws["occurrence_truth_id"],
      f"[{n}] Lightsource, Wilson Sons e Argonauta são três ocorrências"); n += 1
_rel_p = [r for r in ot.relacoes(VIVO)
          if r["occurrence_a"] in _ids and r["occurrence_b"] in _ids]
check(len(_rel_p) == 3
      and all(r["relation"] == "DISTINCT_OCCURRENCE" for r in _rel_p),
      f"[{n}] exatamente 3 relações DISTINCT entre elas ({len(_rel_p)})"); n += 1
check(all(m["occurrence_novelty"] == "UNDETERMINED" for m in _p),
      f"[{n}] novidade `UNDETERMINED` nas quatro — a adjudicação foi de "
      "identidade, não de papel cronológico"); n += 1
_fases = {m["article_ref"].split("/")[-1][:12] or m["article_ref"][-12:]:
          m["material_phase"] for m in _p}
check(sorted(set(_fases.values())) == ["COMPLETION", "REGULATORY_APPROVAL",
                                       "UNKNOWN"],
      f"[{n}] fase só onde o título a enuncia literalmente: {sorted(set(_fases.values()))}"); n += 1
check("africa" not in json.dumps(ot.memberships(VIVO), ensure_ascii=False).lower(),
      f"[{n}] §25 o artigo da África NÃO entrou — decisão adiada"); n += 1

print()
print("=" * 98)
print("7. A VERDADE ANTERIOR CONTINUA INTACTA")
print("=" * 98)
_antes = ot.ocorrencias(SNAP)
check(set(_antes) <= set(ot.ocorrencias(VIVO)),
      f"[{n}] as 7 ocorrências anteriores preservadas"); n += 1
_ma = {m["article_ref"] for m in ot.memberships(SNAP)}
check(_ma <= {m["article_ref"] for m in ot.memberships(VIVO)},
      f"[{n}] as 17 pertinências anteriores preservadas"); n += 1
check(not ot.validar(VIVO), f"[{n}] o acervo valida ({ot.validar(VIVO) or 'OK'})"); n += 1
check(json.loads(json.dumps(VIVO)) == VIVO,
      f"[{n}] ida e volta de serialização preserva o acervo"); n += 1

print()
print("=" * 98)
print("8. NENHUM BYTE CONGELADO FOI TOCADO")
print("=" * 98)
for nome, mod, esperado in (("V1", v1, "cfb16c04bddd7e5d"),
                            ("V2", v2, "62f037f52dbbcf65"),
                            ("V3", v3, "d5c31ee1810a770b")):
    pin = dict(getattr(mod, f"HASHES_V{nome[-1]}"))
    check(pin["freeze_manifest_hash"] == esperado
          and pin["dev_manifest_hash"] == HIST,
          f"[{n}] {nome}: hashes publicados inalterados "
          f"(freeze `{pin['freeze_manifest_hash']}`, dev `{HIST}`)"); n += 1
check(len(v3.alvos_com_verdade(VIVO, historico=arq.HISTORICO)) == 17,
      f"[{n}] §29 o devset congelado da V3 segue com 17 alvos — o acervo "
      "vivo crescer OU encolher não move o experimento"); n += 1

print()
print("=" * 98)
print(f"RESULTADO ARQUIVAL + PETROBRAS: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
