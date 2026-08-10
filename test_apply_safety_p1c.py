#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_apply_safety_p1c.py — P1c: segurança de escrita do history.

Cobre APENAS o mecanismo de persistência e os gates de `--reclassify-only
--apply`. Nenhuma asserção semântica — hardening não pode mexer em
classificação, atribuição, pesos ou taxonomia.

Todos os testes operam em diretórios temporários. Nada toca
`risk_history.json`, `index.html` ou qualquer arquivo do repo.
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile
from pathlib import Path

import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


HIST = {"articles": {"u1": {"title": "a", "companies": ["X"]},
                     "u2": {"title": "b", "companies": ["Y"]}},
        "run_count": 3}


def _tmpdir():
    return Path(tempfile.mkdtemp(prefix="p1c_"))


print("=" * 96)
print("BLOCO A — §2/§16.A: escrita atômica normal")
print("=" * 96)
d = _tmpdir()
p = d / "h.json"
rd.save_history(p, HIST)
check(p.exists() and json.loads(p.read_text(encoding="utf-8")) == HIST,
      "[A] write atômico produz JSON correto")
rd.save_history(p, {"articles": {"u3": {}}, "run_count": 4})
check(json.loads(p.read_text(encoding="utf-8"))["run_count"] == 4,
      "[A2] segunda escrita substitui o conteúdo")
check(not list(d.glob("*.tmp*")), "[§4] nenhum arquivo .tmp remanescente")

print()
print("=" * 96)
print("BLOCO B — §5: contrato de serialização preservado")
print("=" * 96)
d = _tmpdir()
p2 = d / "h2.json"
rd.save_history(p2, HIST)
legado = json.dumps(HIST, ensure_ascii=False, indent=1)
check(p2.read_text(encoding="utf-8") == legado,
      "[B] bytes idênticos ao open(w)+json.dump legado (encoding/indent/ordem)")
_ACC = {"articles": {}, "nota": "Grupo Financiero Banorte — investigación"}
p3 = d / "h3.json"
rd.save_history(p3, _ACC)
check("investigación" in p3.read_text(encoding="utf-8"),
      "[B2] ensure_ascii=False preservado (acentos literais)")

print()
print("=" * 96)
print("BLOCO C — §3/§16.B/§16.C/§17: interrupção deixa o original intacto")
print("=" * 96)


class _Explode:
    """Serializa parcialmente e então falha — simula queda no meio do dump."""
    def __init__(self, exc):
        self.exc = exc

    def __repr__(self):
        raise self.exc


d = _tmpdir()
p4 = d / "orig.json"
rd.save_history(p4, HIST)
sha_antes = rd.sha256_file(p4)

for _label, _exc in (("[B] exceção durante serialização", ValueError("boom")),
                     ("[§17] KeyboardInterrupt antes do replace", KeyboardInterrupt())):
    try:
        rd.save_history(p4, {"articles": {"u9": _Explode(_exc)}})
    except BaseException:
        pass
    check(rd.sha256_file(p4) == sha_antes,
          f"{_label} → original byte-for-byte intacto")
    check(not list(d.glob("*.tmp*")), f"{_label} → temp removido")

_orig_replace = os.replace
try:
    os.replace = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    try:
        rd.save_history(p4, {"articles": {"u8": {}}})
    except BaseException:
        pass
finally:
    os.replace = _orig_replace
check(rd.sha256_file(p4) == sha_antes,
      "[C] interrupção NO os.replace → original intacto")

print()
print("=" * 96)
print("BLOCO D — §6/§12: SHA-256 de arquivo")
print("=" * 96)
d = _tmpdir()
a, b = d / "a.json", d / "b.json"
rd.save_history(a, HIST)
shutil.copy2(a, b)
check(rd.sha256_file(a) == rd.sha256_file(b), "[D] cópia fiel → hashes iguais")
rd.save_history(b, {"articles": {}, "run_count": 99})
check(rd.sha256_file(a) != rd.sha256_file(b), "[E] conteúdo diferente → hashes diferentes")
check(len(rd.sha256_file(a)) == 64, "[D2] hash hex de 64 caracteres")

print()
print("=" * 96)
print("BLOCO E — §13/§15: apply NÃO escreve index.html por default")
print("=" * 96)
import inspect
_src = inspect.getsource(rd.run_reclassify_only)
check('args.output_html or "index.html"' not in _src,
      "[§13] default 'index.html' removido do caminho de apply")
check("outdir / \"preview_reclassify_only.html\"" in _src,
      "[§15] preview default vai para o --audit-outdir")
check("--yes-really" not in _src, "[§13] nenhuma flag assustadora introduzida")

print()
print("=" * 96)
print("BLOCO F — §8/§11: gates declarados no caminho de apply")
print("=" * 96)
for _g in ("G1 primeira passada sem errors", "G2 idempotência", "G3 mesmo número",
           "G4 mesmo conjunto", "G5 added == 0", "G6 duplicates_collapsed == 0",
           "G7 backup hash"):
    check(_g in _src, f"[§8] gate pré-write presente: {_g}")
for _p in ("P2 contagem", "P3 conjunto de identidades", "P4 stored != candidate",
           "P5 pós-apply não idempotente"):
    check(_p in _src, f"[§11] gate pós-write presente: {_p}")
check("ROLLBACK NÃO CONFIRMADO" in _src, "[§12] rollback verificado por hash")
check("except BaseException as exc" in _src,
      "[§12b] rollback cobre BaseException, não só Exception")
check("gates_ok" not in _src, "[§8] variável morta `gates_ok` eliminada")
check(_src.index("_gates") < _src.index("shutil.copy2"),
      "[§8] gates pré-write são avaliados ANTES de qualquer cópia/escrita")

print()
print("=" * 96)
print("BLOCO G — §1: contrato de save_history inalterado para os outros callers")
print("=" * 96)
_sig = inspect.signature(rd.save_history)
check(list(_sig.parameters) == ["path", "history"], "[G] assinatura preservada")
check(_sig.return_annotation is None, "[G2] retorno None preservado")
d = _tmpdir()
_s = d / "sub"
_s.mkdir()
rd.save_history(_s / "n.json", HIST)
check((_s / "n.json").exists(), "[G3] funciona em subdiretório (mesmo filesystem)")

print()
print("=" * 96)
print(f"RESULTADO P1c APPLY SAFETY: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
