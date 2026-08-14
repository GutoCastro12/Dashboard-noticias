#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_llm_routing.py — roteamento LLM por tarefa e vencedor de tradução.

O QUE ESTE TESTE PROTEGE

1. Que TRADUÇÃO e SEMÂNTICA sejam tarefas separadas. Havia uma única variável
   global: `llm.model` valia para as duas. Como a cota do Gemini é contada por
   projeto+modelo, "um modelo para tudo" também significa "uma cota para
   tudo", e a tradução consumindo o dia deixava a consolidação sem nada.

2. Que o vencedor de tradução medido no run 31754386165
   (`gemini-3.5-flash-lite`) seja o default — e que `gemini-3.6-flash`, cuja
   cota diária estava esgotada antes do cron 31738417162, NÃO volte por baixo
   como fallback.

3. Que a SEMÂNTICA continue exatamente como estava. O benchmark semântico não
   mediu nada: as 22 chamadas morreram no cliente, antes da rede. Trocar o
   modelo semântico sem dado seria palpite.

4. Que exista rollback por variável de ambiente, sem editar `config_risco.yaml`.

NENHUMA CHAMADA A PROVIDER.
"""
from __future__ import annotations

import io
import os

for _v in ("RISK_TRANSLATION_LLM_MODEL", "RISK_SEMANTIC_LLM_MODEL",
           "RISK_TRANSLATION_LLM_PROVIDER", "RISK_SEMANTIC_LLM_PROVIDER"):
    os.environ.pop(_v, None)

import llm_router as router
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


CFG = rd.load_config("config_risco.yaml")
SEM_CONFIG = [CFG["llm"]["model"]] + list(CFG["llm"]["model_fallbacks"])

print("=" * 98)
print("BLOCO A — VENCEDOR DE TRADUÇÃO (medido no run 31754386165)")
print("=" * 98)
_t = router.modelos_de(router.TASK_TRANSLATION, CFG)
check(_t[0] == "gemini-3.5-flash-lite",
      f"[1] primário da tradução é o vencedor medido: {_t[0]}")
check("gemini-3.6-flash" not in _t,
      f"[2] gemini-3.6-flash NÃO está no caminho de tradução — nem como "
      f"fallback ({_t})")
check("gemini-3.1-flash-lite" in _t,
      "[3] o outro Lite fica como degradação conhecida, não o 3.6")
check(len(_t) == len(set(_t)), "[4] sem modelo duplicado na lista")
check(router.rota(router.TASK_TRANSLATION, CFG)["model_origem"]
      == "default_de_codigo",
      "[5] a telemetria diz que a origem é decisão de código, não config")

print()
print("=" * 98)
print("BLOCO B — SEMÂNTICA INTOCADA: sem dado, sem troca")
print("=" * 98)
check(router.MODELO_PADRAO[router.TASK_SEMANTIC] == "",
      "[6] não há vencedor semântico registrado — o benchmark não mediu nada")
check(router.modelos_de(router.TASK_SEMANTIC, CFG) == SEM_CONFIG,
      f"[7] a semântica resolve para a lista do config, byte a byte "
      f"({SEM_CONFIG})")
check(router.rota(router.TASK_SEMANTIC, CFG)["model_origem"] == "config",
      "[8] e a origem declarada é 'config'")
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
_codigo = "\n".join(l.split("#")[0] for l in _src.splitlines())
check("_router.modelos_de(_router.TASK_TRANSLATION" in _codigo,
      "[9] a tradução passa pelo roteador")
check("_router.modelos_de(_router.TASK_SEMANTIC" not in _codigo,
      "[10] a consolidação semântica NÃO foi reescrita — segue o caminho antigo")

print()
print("=" * 98)
print("BLOCO C — TAREFAS ISOLADAS E ROLLBACK SEM EDITAR CONFIG")
print("=" * 98)
check(len(set(router.ENV_MODELO.values())) == len(router.ENV_MODELO),
      "[11] uma env DISTINTA por tarefa — não uma env global para tudo")
check("RISK_LLM_MODEL" not in router.ENV_MODELO.values(),
      "[12] nenhuma env global apaga a separação entre as tarefas")

os.environ["RISK_TRANSLATION_LLM_MODEL"] = "gemini-3.1-flash-lite"
try:
    _r = router.modelos_de(router.TASK_TRANSLATION, CFG)
    check(_r[0] == "gemini-3.1-flash-lite",
          f"[13] rollback da tradução por env, sem tocar o config ({_r[0]})")
    check(router.modelos_de(router.TASK_SEMANTIC, CFG) == SEM_CONFIG,
          "[14] e a semântica continua intocada — tarefas de fato isoladas")
    check(router.rota(router.TASK_TRANSLATION, CFG)["model_origem"] == "env",
          "[15] a origem passa a ser 'env'")
finally:
    os.environ.pop("RISK_TRANSLATION_LLM_MODEL", None)

os.environ["RISK_SEMANTIC_LLM_MODEL"] = "gemini-3.5-flash-lite"
try:
    check(router.modelo_de(router.TASK_SEMANTIC, CFG) == "gemini-3.5-flash-lite",
          "[16] a semântica também é sobrescrevível por env, se um dia medirmos")
    check(router.modelos_de(router.TASK_TRANSLATION, CFG)[0]
          == "gemini-3.5-flash-lite",
          "[17] e isso não altera o default da tradução")
finally:
    os.environ.pop("RISK_SEMANTIC_LLM_MODEL", None)

print()
print("=" * 98)
print("BLOCO D — CONFIG DE PRODUÇÃO INTOCADO")
print("=" * 98)
check(CFG["llm"]["model"] == "gemini-3.6-flash",
      "[18] config_risco.yaml não foi editado: llm.model segue gemini-3.6-flash")
check(list(CFG["llm"]["model_fallbacks"])
      == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
      "[19] e os fallbacks do config seguem os mesmos")
check(router.provider_de(router.TASK_TRANSLATION, CFG) == "gemini"
      and router.provider_de(router.TASK_SEMANTIC, CFG) == "gemini",
      "[20] provider de ambas as tarefas continua gemini — Groq não é default")

print()
print("=" * 98)
print(f"RESULTADO ROTEAMENTO LLM: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
