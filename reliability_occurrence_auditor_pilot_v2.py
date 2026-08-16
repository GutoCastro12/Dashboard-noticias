#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_pilot_v2.py — o mesmo experimento, outro contrato.

POR QUE ADAPTADOR E NÃO CÓPIA

Copiar o executor da V1 e editar as três linhas do contrato produziria dois
arquivos quase idênticos que envelheceriam separados. O disjuntor, o ritmo, a
escrita incremental, a contabilidade de chamadas e a proibição de retry são a
parte que mais importa e a que menos muda — duplicá-la é convidar a divergência
silenciosa entre "o que a V1 fez" e "o que a V2 fez".

Então este módulo empresta o executor da V1 e troca DEBAIXO dele o módulo de
congelamento, por um objeto que expõe a API da V2 sob os nomes que o executor
já chama. A mecânica é bit a bit a mesma; só o contrato mudou. É justamente a
invariância da mecânica que torna V1 e V2 comparáveis.

A troca é explícita e local a este arquivo. Importar o executor da V1 em
qualquer outro lugar continua rodando a V1.
"""
from __future__ import annotations

import argparse
import json
import types

import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_pilot as base

G1 = base.G1
G2 = base.G2
MODELOS = base.MODELOS
MAX_CHAMADAS = base.MAX_CHAMADAS          # 17 × 2, o mesmo teto duro de 34
DISJUNTOR_FALHAS_CONSECUTIVAS = base.DISJUNTOR_FALHAS_CONSECUTIVAS
NAO_CHAMADO = base.NAO_CHAMADO
SCHEMA_SAIDA = v2.SCHEMA_SAIDA

# O executor da V1 chama `fz.HASHES_V1`, `fz.avaliar_v1`, `fz.validar_saida`,
# `fz.folds`, `fz.exemplos_congelados`, `fz.montar_prompt`, `fz.agregar`,
# `fz.verificar_congelamento` e `fz.FREEZE_VERSION`. O adaptador responde a
# todos com a implementação V2 — inclusive `HASHES_V1`, que aqui carrega os
# hashes da V2 para que cada resposta gravada fique amarrada ao congelamento
# que realmente a produziu.
_SHIM = types.SimpleNamespace(
    FREEZE_VERSION=v2.FREEZE_VERSION,
    HASHES_V1=v2.HASHES_V2,
    avaliar_v1=v2.avaliar_v2,
    validar_saida=v2.validar_saida,
    folds=v2.folds,
    exemplos_congelados=v2.exemplos_congelados,
    montar_prompt=v2.montar_prompt,
    agregar=v2.agregar,
    verificar_congelamento=v2.verificar_congelamento,
    OUT_NOVELTY=v2.OUT_NOVELTY, OUT_PHASE=v2.OUT_PHASE,
    OUT_ANCHOR=v2.OUT_ANCHOR, OUT_CONFIDENCE=v2.OUT_CONFIDENCE,
    OUT_EVIDENCE_ORIGIN=v2.OUT_EVIDENCE_ORIGIN,
    SEM_AVALIACAO=v2.SEM_AVALIACAO, ABSTENCAO=v2.ABSTENCAO,
    DEFAULT_CURATED_SET=v2.DEFAULT_CURATED_SET,
    MODO_DE_OPERACAO=v2.MODO_DE_OPERACAO,
)


def _instalar():
    """Troca o congelamento sob o executor. Idempotente."""
    base.fz = _SHIM
    base.SCHEMA_SAIDA = v2.SCHEMA_SAIDA


_instalar()

alvos_congelados = base.alvos_congelados
executar = base.executar
pontuar = base.pontuar
sanidade = base.sanidade
config_efetiva = base.config_efetiva


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Benchmark de desenvolvimento do auditor — congelamento V2.")
    p.add_argument("--shadow", default="risk_semantic_v2_shadow.json")
    p.add_argument("--out", default="out_auditor_pilot_v2/dev_results.jsonl")
    p.add_argument("--manifest-out",
                   default="out_auditor_pilot_v2/execution_manifest.json")
    p.add_argument("--score-out", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    _instalar()
    return base.main(["--shadow", a.shadow, "--out", a.out,
                      "--manifest-out", a.manifest_out]
                     + (["--score-out", a.score_out] if a.score_out else [])
                     + (["--dry-run"] if a.dry_run else []))


if __name__ == "__main__":
    raise SystemExit(main())
