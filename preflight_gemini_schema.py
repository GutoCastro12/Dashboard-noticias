#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preflight: o SDK Gemini consegue converter os nossos schemas? ZERO rede.

POR QUE ISTO EXISTE

O primeiro benchmark real (run 31754386165) gastou o dispatch inteiro para
descobrir, 22 vezes seguidas, que o `google-generativeai` 0.8.6 não converte
`{"type": ["string","null"]}`. A falha era determinística e local: dava para
saber sem chamar ninguém.

Este preflight roda a MESMA construção de `GenerationConfig` que o benchmark
faz, com o MESMO SDK instalado no workflow, sobre TODOS os schemas do plano.
Se a conversão falhar, o job para aqui — antes de qualquer chamada.

Nenhuma requisição é feita: `GenerativeModel(...)` e `GenerationConfig(...)`
são construção local. Não há `generate_content` neste arquivo, e a ausência é
verificada por teste.
"""
from __future__ import annotations

import io
import json
import sys

import gemini_schema_adapter as ga
import bench_free_llm as bench
import reliability_pilot1_sample as ps
import risk_dashboard as rd

FALHAS = []


def _ok(msg):
    print(f"  ✅ {msg}")


def _falhou(msg):
    FALHAS.append(msg)
    print(f"  ❌ {msg}")


def main() -> int:
    print("=" * 96)
    print("PREFLIGHT — compatibilidade de schema com o SDK Gemini (zero rede)")
    print("=" * 96)

    try:
        import google.generativeai as genai
    except Exception as exc:
        _falhou(f"SDK google-generativeai indisponível: {exc}")
        return 1
    versao = getattr(genai, "__version__", "desconhecida")
    _ok(f"SDK importado (versão {versao})")

    cfg = rd.load_config("config_risco.yaml")
    man = ps.carregar_manifesto()
    entradas, ausentes = bench.montar_plano(man, cfg,
                                            bench.CONTRATO_PADRAO)
    if ausentes:
        _falhou(f"itens planejados ausentes do manifesto: {ausentes}")
    _ok(f"{len(entradas)} entradas no plano "
        f"(contrato {bench.CONTRATO_PADRAO})")

    # 1) o canônico REALMENTE tem o padrão que quebrava — se não tiver mais,
    #    este preflight virou vazio e alguém precisa saber.
    canonicos = [(e["sample_id"], e["call_type"],
                  (e.get("payload") or {}).get("schema") or {})
                 for e in entradas]
    total_anulaveis = sum(len(ga.campos_anulaveis(s)) for _, _, s in canonicos)
    if total_anulaveis == 0:
        _falhou("nenhum campo anulável encontrado — o preflight não está "
                "exercitando o caso que quebrou")
    else:
        _ok(f"{total_anulaveis} campo(s) anulável(is) no plano canônico")

    # 2) a adaptação não pode deixar tipo em lista para trás
    for sid, tipo, esq in canonicos:
        restantes = ga.campos_anulaveis(ga.adaptar_schema(esq))
        if restantes:
            _falhou(f"{sid}/{tipo}: tipo em lista sobrevive à adaptação: "
                    f"{restantes}")
    if not FALHAS:
        _ok("nenhum tipo em lista sobrevive à adaptação")

    # 3) a prova que importa: o conversor REAL do SDK aceita?
    convertidos = 0
    for sid, tipo, esq in canonicos:
        try:
            genai.types.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
                max_output_tokens=bench.OUTPUT_TOKEN_CAP,
                response_schema=ga.adaptar_schema(esq))
            convertidos += 1
        except Exception as exc:
            _falhou(f"{sid}/{tipo}: {type(exc).__name__}: {str(exc)[:160]}")
    if convertidos == len(canonicos):
        _ok(f"o SDK converteu os {convertidos} schemas do plano")

    # 4) contraprova: o canônico CRU ainda deve falhar. Se passar, o defeito
    #    sumiu por outro motivo e a adaptação virou ruído — melhor saber.
    esq0 = canonicos[0][2]
    try:
        genai.types.GenerationConfig(
            temperature=0.0, response_mime_type="application/json",
            max_output_tokens=bench.OUTPUT_TOKEN_CAP, response_schema=esq0)
        print("  ⚠️  o schema CRU passou no conversor — o SDK pode ter mudado; "
              "a adaptação segue correta mas deixou de ser necessária")
    except Exception as exc:
        _ok(f"contraprova: o schema CRU ainda falha ({type(exc).__name__}) — "
            f"é a adaptação que está resolvendo")

    resumo = {"sdk_versao": versao, "entradas": len(entradas),
              "schemas_convertidos": convertidos,
              "campos_anulaveis": total_anulaveis,
              "falhas": FALHAS, "requests_ao_provider": 0}
    io.open("out_bench_freellm_preflight.json", "w", encoding="utf-8").write(
        json.dumps(resumo, ensure_ascii=False, indent=1))

    print("=" * 96)
    if FALHAS:
        print(f"PREFLIGHT REPROVADO — {len(FALHAS)} falha(s). "
              f"ZERO chamadas ao provider.")
        return 1
    print("PREFLIGHT OK — o live pode prosseguir. ZERO chamadas ao provider.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
