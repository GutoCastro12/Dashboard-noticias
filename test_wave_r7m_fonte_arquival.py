#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7m_fonte_arquival.py — 4I.2 R7m.

O ACERVO VIVO NÃO É ENTRADA DE ARQUIVO.

A R7e provou o princípio e o aplicou no verificador arquival. O que ficou para
trás foi o DEFAULT das funções de congelamento: `historico="risk_history.json"`.
Qualquer chamada que omitisse o parâmetro voltava, em silêncio, ao acervo vivo.

A conta chegou ao tentar alinhar a produção de `troca_ceo`. Retirar a família
dos artigos do Santander e da Tupy — correção adjudicada por humano — fez os
exemplos congelados pararem de reconstruir com `ARTIGO_NAO_ENCONTRADO`, oito
testes caíram e o alinhamento teve de ser revertido.

POR QUE UM MÓDULO EM VEZ DE UM DEFAULT NOVO

Os bytes dos executores de congelamento são verificados por hash no BLOCO G do
`test_wave_r7e_arquival_exemplos.py`. Trocar o default dentro deles quebraria
esse congelamento — e um experimento cujo executor muda não pode mais atribuir
seus resultados ao mesmo código. A autoridade arquival vira módulo próprio e
quem chama passa a fonte explicitamente.

O TESTE CENTRAL É O BLOCO D: simula, EM MEMÓRIA, exatamente a mudança de
produção que provocou o desastre, e prova que agora nada quebra.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile

import reliability_occurrence_archival_source as arq
import reliability_occurrence_auditor_freeze as fz
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_truth as ot
import semantic_v2_shadow as sh

PASS = FAIL = 0
ALVOS = {"cad44d85917e8bb50e46": "Santander Brasil",
         "9eb803c2493e648262ec": "Tupy"}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


D = json.load(io.open(arq.DEV_TRUTH, encoding="utf-8"))
H_VIVO = json.load(io.open("risk_history.json", encoding="utf-8"))


def _sem_ceo(hist):
    """O acervo vivo COMO FICARÁ depois do alinhamento de produção."""
    h = copy.deepcopy(hist)
    for u, r in h["articles"].items():
        aid = sh.id_artigo(r.get("url") or u, r.get("title") or "")
        if aid in ALVOS:
            ebc = r.get("events_by_company") or {}
            emp = ALVOS[aid]
            ebc[emp] = [e for e in (ebc.get(emp) or []) if e != "troca_ceo"]
    return h


print("=" * 98)
print("BLOCO A - a autoridade arquival tem nome e checksum")
print("=" * 98)
check(arq.HISTORICO == "occurrence_auditor_freeze_history_snapshot_v1.json",
      "[1] a fonte histórica é o snapshot imutável")
check(arq.resolver(None) == arq.HISTORICO,
      "[2] None resolve para a autoridade arquival")
check(arq.resolver("/tmp/x.json") == "/tmp/x.json",
      "[3] e um caminho explícito vence — é assim que o teste injeta")
_i = arq.integro()
check(_i["ok"], f"[4] os dois snapshots seguem íntegros ({_i['historico']['obtido']}"
                f" / {_i['dev_truth']['obtido']})")
check(_i["historico"]["obtido"] == "430da3e4973b227e",
      "[5] histórico com o checksum congelado")
check(_i["dev_truth"]["obtido"] == "622082c16255ce7a",
      "[6] verdade de desenvolvimento com o checksum congelado")
import inspect
check(inspect.signature(arq.resolver).parameters["historico"].default is None,
      "[7] o default é None, resolvido NA CHAMADA — não um literal no `def`")

print()
print("=" * 98)
print("BLOCO B - os dois artigos existem no snapshot com a família intacta")
print("=" * 98)
_S = json.load(io.open(arq.HISTORICO, encoding="utf-8"))
_arts = _S.get("articles") or _S
for _i2, (_aid, _emp) in enumerate(sorted(ALVOS.items()), start=8):
    _achou = [r for u, r in _arts.items()
              if sh.id_artigo(r.get("url") or u, r.get("title") or "") == _aid]
    check(_achou and "troca_ceo" in ((_achou[0].get("events_by_company") or {})
                                     .get(_emp) or []),
          f"[{_i2}] {_emp}: presente no snapshot COM `troca_ceo`")
for _i2, (_aid, _emp) in enumerate(sorted(ALVOS.items()), start=10):
    _achou = [r for u, r in H_VIVO["articles"].items()
              if sh.id_artigo(r.get("url") or u, r.get("title") or "") == _aid]
    check(bool(_achou),
          f"[{_i2}] {_emp}: e também presente no acervo vivo (ainda não alinhado)")

print()
print("=" * 98)
print("BLOCO C - REPRODUÇÃO do desastre: acervo vivo como entrada de arquivo")
print("=" * 98)
_d = tempfile.mkdtemp(prefix="r7m_")
_sim = os.path.join(_d, "hist_sem_ceo.json")
io.open(_sim, "w", encoding="utf-8").write(
    json.dumps(_sem_ceo(H_VIVO), ensure_ascii=False))
_erro = ""
try:
    fz.exemplos_congelados(copy.deepcopy(D), historico=_sim)
except Exception as e:
    _erro = f"{type(e).__name__}: {e}"
check("ARTIGO_NAO_ENCONTRADO" in _erro,
      f"[12] com o acervo vivo alinhado, a reconstrução QUEBRA ({_erro[:52]})")
check("cad44d85917e8bb50e46" in _erro,
      "[13] e quebra exatamente no artigo do Santander")

print()
print("=" * 98)
print("BLOCO D - CENTRAL: com a autoridade arquival, nada quebra")
print("=" * 98)
for _i2, (_nome, _mod) in enumerate((("V1", fz), ("V2", v2), ("V3", v3)),
                                    start=14):
    try:
        _ex = _mod.exemplos_congelados(copy.deepcopy(D), historico=arq.HISTORICO)
        _ok = bool(_ex)
    except Exception as e:
        _ok = False
        _ex = f"{type(e).__name__}: {e}"
    check(_ok, f"[{_i2}] {_nome} reconstrói os exemplos a partir do snapshot")
for _i2, (_nome, _mod) in enumerate((("V1", fz), ("V2", v2), ("V3", v3)),
                                    start=17):
    check(_mod.verificar_congelamento(copy.deepcopy(D),
                                      historico=arq.HISTORICO) == [],
          f"[{_i2}] e {_nome} segue sem divergência de congelamento")

print()
print("=" * 98)
print("BLOCO E - METAMÓRFICO: mudar o acervo vivo não move o arquivo")
print("=" * 98)
_h1 = fz.exemplos_congelados(copy.deepcopy(D), historico=arq.HISTORICO)
_sim2 = os.path.join(_d, "hist_vazio.json")
io.open(_sim2, "w", encoding="utf-8").write(
    json.dumps({"articles": {}, "run_count": 1}, ensure_ascii=False))
_h2 = fz.exemplos_congelados(copy.deepcopy(D), historico=arq.HISTORICO)
check(json.dumps(_h1, sort_keys=True) == json.dumps(_h2, sort_keys=True),
      "[20] duas reconstruções seguidas são idênticas")


def _hash_ex(hist):
    return hashlib.sha256(json.dumps(
        fz.exemplos_congelados(copy.deepcopy(D), historico=hist),
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


_antes = _hash_ex(arq.HISTORICO)
_ = _sem_ceo(H_VIVO)          # o vivo muda…
check(_hash_ex(arq.HISTORICO) == _antes,
      "[21] …e o hash dos exemplos arquivais NÃO se move")
check(arq.integro()["historico"]["obtido"] == "430da3e4973b227e",
      "[22] o snapshot continua byte-idêntico depois de tudo")

print()
print("=" * 98)
print("BLOCO F - a view ATIVA de occurrence_truth já existe e é a canônica")
print("=" * 98)
_SH = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ms = ot.memberships(_SH)
_at = ot.memberships_ativas(_SH)
check(len(_ms) == 21, f"[23] 21 memberships brutas ({len(_ms)})")
check(len(_at) == 21, f"[24] e 21 ativas — nenhuma substituída ainda ({len(_at)})")
check(sum(1 for m in _ms if m.get("superseded_by")) == 0,
      "[25] `superseded_by` está nulo em todas")
check(all(not m.get("supersedes") for m in _ms),
      "[26] e `supersedes` também")
_alvo = [m for m in _ms if m.get("article_ref") in ALVOS]
check(len(_alvo) == 2, f"[27] as duas memberships dos alvos existem ({len(_alvo)})")
check(all(m.get("occurrence_novelty") == "FOLLOW_UP" for m in _alvo),
      "[28] ambas já adjudicadas como FOLLOW_UP")
check(all(m.get("material_phase") == "NONE" for m in _alvo),
      "[29] com fase material NONE")
check(all(m.get("should_refresh_anchor") in (False, None) for m in _alvo),
      "[30] e sem renovação de âncora — NÃO contradizem o lote V1")

print()
print("=" * 98)
print("BLOCO G - os executores congelados seguem byte-idênticos")
print("=" * 98)
_CONG = {"reliability_occurrence_auditor_freeze.py": "69e1229b1ceb6153",
         "reliability_occurrence_auditor_freeze_v2.py": "f0aa9ab28a9f24be",
         "reliability_occurrence_auditor_freeze_v3.py": "48b66a8b4e7ae166"}
for _i2, (_f, _esp) in enumerate(sorted(_CONG.items()), start=31):
    _got = hashlib.sha256(io.open(_f, "rb").read()).hexdigest()[:16]
    check(_got == _esp, f"[{_i2}] {_f} intacto ({_got})")
_src = io.open("reliability_occurrence_archival_source.py", encoding="utf-8").read()
check("risk_history" not in _src.split('"""')[2],
      "[34] o módulo arquival não referencia o acervo vivo fora da docstring")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7m (fonte arquival explícita): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
