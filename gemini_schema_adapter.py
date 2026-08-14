#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tradução do schema CANÔNICO para a representação que o SDK Gemini aceita.

O PROBLEMA MEDIDO

No run 31754386165 as 22 chamadas semânticas morreram no CLIENTE, antes de
qualquer byte sair pela rede:

    TypeError: unhashable type: 'list'

Causa isolada: o `google-generativeai` 0.8.6 hasheia o valor de `type` ao
converter o schema, e o nosso contrato declara 9 campos anuláveis na forma
JSON-Schema padrão, `{"type": ["string", "null"]}`. Uma lista não é hasheável.
Não foi falha de modelo, não foi cota, não foi qualidade — foi representação.

A SEPARAÇÃO QUE ESTE MÓDULO IMPÕE

    CONTRATO CANÔNICO      — continua aceitando null. Não muda.
    REPRESENTAÇÃO DO PROVIDER — o que cabe no conversor de um SDK específico.

Rebaixar a ontologia inteira porque um SDK tem limitação seria deixar a cauda
abanar o cachorro: o contrato descreve o problema de risco, não as restrições
de serialização de uma biblioteca.

O MAPEAMENTO

Um campo anulável vira, para o provider:

    type: <o tipo não-nulo>      e      FORA de `required`

porque "ausente" é a única forma de dizer "nulo" que o conversor aceita. Na
volta, `normalizar_saida()` devolve o campo ausente para `None`, restaurando o
contrato canônico. Ausência do provider ⇒ null canônico.

O QUE NÃO ACONTECE

Campos obrigatórios e NÃO anuláveis continuam obrigatórios. Compatibilidade
não se resolve esvaziando `required` — isso trocaria um erro de serialização
por um buraco semântico, que é bem pior: o modelo passaria a poder omitir
`company_role` e ninguém notaria.

E ausência nunca vira `""`. Um campo faltando significa "o modelo não afirmou
isso"; string vazia significa "o modelo afirmou o vazio". Confundir os dois
transformaria silêncio em asserção.
"""
from __future__ import annotations

import copy

NULO = "null"


def _tipo_nao_nulo(tipos: list) -> str:
    for t in tipos:
        if t != NULO:
            return t
    return "string"


def eh_anulavel(prop: dict) -> bool:
    """Anulável = o tipo canônico admite `null` explicitamente."""
    t = (prop or {}).get("type")
    return isinstance(t, list) and NULO in t


def campos_anulaveis(schema: dict) -> list[str]:
    """Nomes dos campos anuláveis, em qualquer profundidade, ordenados."""
    achados: set[str] = set()

    def anda(no):
        if isinstance(no, dict):
            for nome, prop in (no.get("properties") or {}).items():
                if isinstance(prop, dict) and eh_anulavel(prop):
                    achados.add(nome)
            for v in no.values():
                anda(v)
        elif isinstance(no, list):
            for v in no:
                anda(v)

    anda(schema)
    return sorted(achados)


def adaptar_schema(schema: dict) -> dict:
    """Devolve uma CÓPIA na representação do provider. Não muta o canônico."""
    def converte(no):
        if isinstance(no, list):
            return [converte(v) for v in no]
        if not isinstance(no, dict):
            return no

        novo = {}
        for chave, valor in no.items():
            if chave == "properties" and isinstance(valor, dict):
                novo[chave] = {k: converte(v) for k, v in valor.items()}
            else:
                novo[chave] = converte(valor)

        props = novo.get("properties")
        if isinstance(props, dict):
            anulaveis = {k for k, v in props.items()
                         if isinstance(v, dict) and eh_anulavel(v)}
            for k in anulaveis:
                props[k] = dict(props[k])
                props[k]["type"] = _tipo_nao_nulo(props[k]["type"])
            if anulaveis and isinstance(novo.get("required"), list):
                # anulável sai de `required`: ausência é como o provider
                # expressa nulo. Os demais obrigatórios continuam obrigatórios.
                novo["required"] = [r for r in novo["required"]
                                    if r not in anulaveis]
        return novo

    return converte(copy.deepcopy(schema))


def normalizar_saida(saida, schema: dict):
    """Restaura o contrato canônico: campo anulável ausente volta a `None`.

    Só toca campos declarados anuláveis no schema canônico. Campos que o
    contrato não prevê ficam como vieram — inventar chave seria tão errado
    quanto perder uma.
    """
    def anda(valor, no):
        if isinstance(no, dict) and no.get("type") == "array":
            itens = no.get("items") or {}
            if isinstance(valor, list):
                return [anda(v, itens) for v in valor]
            return valor
        if not isinstance(no, dict) or not isinstance(no.get("properties"), dict):
            return valor
        if not isinstance(valor, dict):
            return valor
        saida_ = dict(valor)
        for nome, prop in no["properties"].items():
            if not isinstance(prop, dict):
                continue
            if nome in saida_:
                saida_[nome] = anda(saida_[nome], prop)
            elif eh_anulavel(prop):
                saida_[nome] = None        # ausência do provider ⇒ null canônico
        return saida_

    return anda(saida, schema)


def descrever(schema: dict) -> dict:
    """Resumo auditável do que a adaptação fez — para o artefato."""
    adaptado = adaptar_schema(schema)
    anulaveis = campos_anulaveis(schema)

    def requireds(no, acc):
        if isinstance(no, dict):
            if isinstance(no.get("required"), list):
                acc.append(list(no["required"]))
            for v in no.values():
                requireds(v, acc)
        elif isinstance(no, list):
            for v in no:
                requireds(v, acc)
        return acc

    req_antes = requireds(schema, [])
    req_depois = requireds(adaptado, [])
    return {
        "campos_anulaveis": anulaveis,
        "n_anulaveis": len(anulaveis),
        "required_antes": req_antes,
        "required_depois": req_depois,
        "removidos_de_required": sorted(
            {r for grupo in req_antes for r in grupo}
            - {r for grupo in req_depois for r in grupo}),
        "tipos_em_lista_restantes": campos_anulaveis(adaptado),
    }
