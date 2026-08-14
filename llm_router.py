#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Roteamento LLM por TAREFA — separa `task`, `provider` e `model`.

POR QUE ISTO EXISTE

Até aqui havia uma única variável global de modelo: `llm.model` do
`config_risco.yaml` valia para tradução, para consolidação semântica e para o
piloto ao mesmo tempo. Isso amarra três tarefas com exigências diferentes ao
mesmo provider e ao mesmo balde de cota — a cota do Gemini é contada por
PROJETO + MODELO, então "um modelo para tudo" também significa "uma cota para
tudo", e a tradução consumindo o dia inteiro deixa a consolidação sem nada.

Tradução quer fidelidade literal e preservação de entidades. Consolidação
semântica quer julgamento de sujeito, papel e vigência. Não há razão para que o
melhor modelo gratuito para uma seja o melhor para a outra, e o benchmark pode
perfeitamente eleger vencedores diferentes por tarefa.

O QUE ESTE MÓDULO NÃO FAZ

Não muda o comportamento de produção por si só. Enquanto não houver vencedor
medido, o default de TODA tarefa continua sendo exatamente o que o
`config_risco.yaml` já dizia — mesmo modelo primário, mesma lista de fallbacks,
mesma ordem. Trocar de modelo passa a ser possível por variável de ambiente,
sem editar o config de produção.

PRECEDÊNCIA

    env var da tarefa  >  default de código da tarefa  >  config (llm.model)

O default de código nasce vazio de propósito: preenchê-lo é uma decisão de
benchmark, não de conveniência.
"""
from __future__ import annotations

import os

# ── tarefas ─────────────────────────────────────────────────────────────────
TASK_TRANSLATION = "translation"
TASK_SEMANTIC = "semantic"
TASK_PILOT = "pilot"
TASKS = (TASK_TRANSLATION, TASK_SEMANTIC, TASK_PILOT)

# ── providers ───────────────────────────────────────────────────────────────
PROVIDER_GEMINI = "gemini"
PROVIDER_GROQ = "groq"

# Uma variável POR TAREFA. Uma única env para tudo reintroduziria o acoplamento
# que este módulo existe para desfazer.
ENV_MODELO = {
    TASK_TRANSLATION: "RISK_TRANSLATION_LLM_MODEL",
    TASK_SEMANTIC: "RISK_SEMANTIC_LLM_MODEL",
    TASK_PILOT: "RISK_PILOT_LLM_MODEL",
}
ENV_PROVIDER = {
    TASK_TRANSLATION: "RISK_TRANSLATION_LLM_PROVIDER",
    TASK_SEMANTIC: "RISK_SEMANTIC_LLM_PROVIDER",
    TASK_PILOT: "RISK_PILOT_LLM_PROVIDER",
}

# VENCEDORES DO BENCHMARK — vazios enquanto não houver medição real.
#
# TRADUÇÃO — `gemini-3.5-flash-lite`, medido no run 31754386165.
#   Fidelidade EMPATADA com o 3.1-flash-lite: nos mesmos 2 lotes (5 títulos em
#   espanhol, 3 em inglês) ambos devolveram mapeamento completo, preservaram as
#   14 entidades verificadas e o valor monetário, e traduziram os 8 títulos.
#   Zero perdas dos dois lados. O desempate é operacional: 3,93 s contra 7,28 s
#   e 1335 contra 1492 tokens de saída. Amostra pequena e declarada — 8 itens,
#   2 idiomas —, mas a dimensão que poderia doer (corromper entidade ou número)
#   não teve variação alguma a medir.
#
#   O ganho maior não é velocidade: é sair do `gemini-3.6-flash`, cuja cota
#   diária já estava esgotada quando o cron 31738417162 começou. A tradução
#   passa a sacar de outro balde de cota.
#
# SEMÂNTICA — vazio, continua herdando o config. O benchmark semântico não
#   produziu dado algum: as 22 chamadas de audit/discovery morreram no CLIENTE,
#   antes da rede, porque o SDK 0.8.6 não converte os 9 campos anuláveis do
#   nosso schema (`{"type": ["string","null"]}` → `unhashable type: 'list'`).
#   Nenhum dos dois modelos foi medido. Preencher isto seria palpite.
MODELO_PADRAO: dict[str, str] = {
    TASK_TRANSLATION: "gemini-3.5-flash-lite",
    TASK_SEMANTIC: "",
    TASK_PILOT: "",
}

PROVIDER_PADRAO = {
    TASK_TRANSLATION: PROVIDER_GEMINI,
    TASK_SEMANTIC: PROVIDER_GEMINI,
    TASK_PILOT: PROVIDER_GEMINI,
}


def _cfg_llm(cfg: dict | None) -> dict:
    return (cfg or {}).get("llm") or {}


def provider_de(tarefa: str, cfg: dict | None = None) -> str:
    """Provider da tarefa. Groq nunca vira default sozinho — só por env."""
    if tarefa not in TASKS:
        raise ValueError(f"tarefa desconhecida: {tarefa!r}")
    return (os.environ.get(ENV_PROVIDER[tarefa], "").strip()
            or PROVIDER_PADRAO[tarefa])


def modelo_de(tarefa: str, cfg: dict | None = None) -> str:
    """Modelo PRIMÁRIO da tarefa, seguindo a precedência documentada."""
    if tarefa not in TASKS:
        raise ValueError(f"tarefa desconhecida: {tarefa!r}")
    return (os.environ.get(ENV_MODELO[tarefa], "").strip()
            or MODELO_PADRAO.get(tarefa, "")
            or _cfg_llm(cfg).get("model", ""))


def modelos_de(tarefa: str, cfg: dict | None = None) -> list[str]:
    """Primário + fallbacks, sem repetição e preservando a ordem.

    Quando a tarefa tem modelo próprio, ele entra na FRENTE dos fallbacks do
    config em vez de substituí-los: se o escolhido sumir da conta, a degradação
    continua sendo a que a produção já conhece, não um beco sem saída.
    """
    primario = modelo_de(tarefa, cfg)
    saida: list[str] = []
    for m in [primario] + list(_cfg_llm(cfg).get("model_fallbacks") or []):
        if m and m not in saida:
            saida.append(m)
    return saida


def rota(tarefa: str, cfg: dict | None = None) -> dict:
    """Rota resolvida + a ORIGEM de cada decisão, para telemetria honesta."""
    env_m = os.environ.get(ENV_MODELO.get(tarefa, ""), "").strip()
    env_p = os.environ.get(ENV_PROVIDER.get(tarefa, ""), "").strip()
    if env_m:
        origem = "env"
    elif MODELO_PADRAO.get(tarefa):
        origem = "default_de_codigo"
    else:
        origem = "config"
    return {
        "task": tarefa,
        "provider": provider_de(tarefa, cfg),
        "provider_origem": "env" if env_p else "default_de_codigo",
        "model": modelo_de(tarefa, cfg),
        "model_origem": origem,
        "fallbacks": modelos_de(tarefa, cfg)[1:],
    }
