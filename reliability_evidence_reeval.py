#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_evidence_reeval.py — reavaliação DERIVADA de validade de evidência.

POR QUE ESTE MÓDULO EXISTE

O validador de quote tinha um piso de 4 caracteres que reprovava citações
literalmente presentes no input sempre que a entidade tinha nome curto. O case
#2 prospectivo (JBS/`troca_ceo`) expôs isso: os dois modelos citaram
`subject_quote = "JBS"`, que está na manchete, e ambos saíram
`H1_QUOTE_INEXISTENTE`.

Corrigir o validador é fácil. O que não se pode fazer é reescrever o passado:
no momento da inferência o validador respondeu INVÁLIDO, e esse fato é parte da
telemetria. Apagá-lo transformaria o histórico em algo que não aconteceu.

Então a reavaliação é DERIVADA e não destrutiva. Este módulo lê os registros
persistidos, reconstrói exatamente o texto que o validador viu e recalcula a
validade sob as duas regras, lado a lado:

    COMO OBSERVADO   (v1, com o piso)      → o que a telemetria registrou
    REAVALIADO       (v2, token completo)  → o que a regra corrigida diria

Nada aqui escreve no shadow, no histórico ou no dashboard. É infraestrutura de
avaliação: não tem autoridade sobre score, semântica de produção ou verdade
humana.

RECONSTRUÇÃO DO TEXTO

`semantic_v2_shadow_run` passa ao validador `(summary or title)[:4000]` do
artigo. É determinístico e reproduzível a partir de `risk_history.json`, então
o texto não precisa estar duplicado no registro — mas precisa ser reconstruído
pela MESMA expressão, ou a comparação mediria outra coisa.
"""
from __future__ import annotations

import io
import json

import reliability_pilot_validators as pv

REEVAL_VERSION = "r7ba.q2.reeval.v1"
LIMITE_TEXTO = 4000


def texto_do_artigo(artigo: dict) -> str:
    """Mesma expressão de `semantic_v2_shadow_run`. Se aquela mudar, esta
    precisa mudar junto — por isso o teste ancora as duas."""
    return (artigo.get("summary") or artigo.get("title") or "")[:LIMITE_TEXTO]


def _quotes(evento: dict) -> list:
    return [(c, evento.get(c)) for c in pv.CAMPOS_QUOTE
            if evento.get(c) not in (None, "")]


def reavaliar_evento(evento: dict, texto: str) -> dict:
    """Compara v1 e v2 campo a campo para UM evento de saída do modelo."""
    campos = []
    for campo, quote in _quotes(evento):
        v1 = pv.quote_valida_v1(quote, texto)
        v2 = pv.quote_valida(quote, texto)
        campos.append({"campo": campo, "quote": quote, "v1": v1, "v2": v2,
                       "mudou": v1 != v2})
    return {
        "campos": campos,
        "valido_v1": all(c["v1"] for c in campos),
        "valido_v2": all(c["v2"] for c in campos),
        "mudancas": [c for c in campos if c["mudou"]],
    }


def reavaliar_shadow(shadow: dict, historico: dict) -> dict:
    """Percorre todas as observações persistidas do shadow V2."""
    artigos = historico.get("articles") or {}
    registros, sem_texto = [], []
    for chave, obs in sorted(shadow.get("observacoes", {}).items()):
        art = artigos.get(obs.get("url") or "")
        if art is None:
            sem_texto.append(chave)
            continue
        texto = texto_do_artigo(art)
        for i, ev in enumerate((obs.get("saida") or {}).get("events") or []):
            r = reavaliar_evento(ev, texto)
            registros.append({
                "chave": chave, "empresa": obs.get("company"),
                "evento": obs.get("candidate_event"),
                "modelo": obs.get("actual_model"), "indice": i,
                "titulo": obs.get("title"), "texto_excerto": texto[:160],
                **r,
            })
    mud = [c for r in registros for c in r["mudancas"]]
    return {
        "reeval_version": REEVAL_VERSION,
        "validator_version": pv.QUOTE_VALIDATOR_VERSION,
        "registros": registros,
        "sem_texto_reconstruivel": sem_texto,
        "total_quotes": sum(len(r["campos"]) for r in registros),
        "invalido_para_valido": sum(1 for c in mud if not c["v1"] and c["v2"]),
        "valido_para_invalido": sum(1 for c in mud if c["v1"] and not c["v2"]),
        "eventos_validos_v1": sum(1 for r in registros if r["valido_v1"]),
        "eventos_validos_v2": sum(1 for r in registros if r["valido_v2"]),
        "total_eventos": len(registros),
    }


def carregar_e_reavaliar(shadow_path="risk_semantic_v2_shadow.json",
                         historico_path="risk_history.json") -> dict:
    sh = json.load(io.open(shadow_path, encoding="utf-8"))
    hi = json.load(io.open(historico_path, encoding="utf-8"))
    return reavaliar_shadow(sh, hi)


def relatorio(res: dict) -> str:
    L = [f"reavaliação de evidência — {res['reeval_version']} "
         f"(validador {res['validator_version']})",
         f"  eventos reavaliados : {res['total_eventos']}",
         f"  quotes avaliadas    : {res['total_quotes']}",
         f"  INVÁLIDO → VÁLIDO   : {res['invalido_para_valido']}",
         f"  VÁLIDO → INVÁLIDO   : {res['valido_para_invalido']}",
         f"  eventos válidos v1  : {res['eventos_validos_v1']}"
         f"/{res['total_eventos']}",
         f"  eventos válidos v2  : {res['eventos_validos_v2']}"
         f"/{res['total_eventos']}"]
    if res["sem_texto_reconstruivel"]:
        L.append(f"  SEM TEXTO RECONSTRUÍVEL: {res['sem_texto_reconstruivel']}")
    for r in res["registros"]:
        for c in r["mudancas"]:
            L.append(f"  · {r['empresa']}/{r['evento']} [{r['modelo']}] "
                     f"{c['campo']}={c['quote']!r}: "
                     f"v1={'VÁLIDA' if c['v1'] else 'INVÁLIDA'} → "
                     f"v2={'VÁLIDA' if c['v2'] else 'INVÁLIDA'}")
    return "\n".join(L)


if __name__ == "__main__":
    print(relatorio(carregar_e_reavaliar()))
