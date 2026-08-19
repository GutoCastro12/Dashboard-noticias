#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_archival_source.py — o acervo vivo não é entrada de arquivo.

O DEFEITO

A R7e estabeleceu que os exemplos congelados do auditor de ocorrência precisam
ser reconstruídos a partir de um snapshot imutável, e o
`reliability_occurrence_archival_verifier` já fazia isso, passando o snapshot
explicitamente. O que ficou para trás foi o DEFAULT: todas as funções de
congelamento têm `historico="risk_history.json"`, e qualquer chamada que
omitisse o parâmetro voltava, em silêncio, ao acervo vivo.

A conta chegou na tentativa de alinhar a produção de `troca_ceo`. Retirar a
família dos artigos do Santander e da Tupy — correção legítima, adjudicada por
humano — fez os exemplos congelados pararem de reconstruir:

    ValueError: ARTIGO_NAO_ENCONTRADO: cad44d85917e8bb50e46

O artigo continua no snapshot, com `troca_ceo` intacto. Ele deixou de ser membro
apenas NO VIVO. Oito testes caíram e o alinhamento teve de ser revertido.

POR QUE UM MÓDULO, E NÃO UM DEFAULT NOVO NOS EXECUTORES

A correção óbvia seria trocar o default dentro de
`reliability_occurrence_auditor_freeze*.py`. Ela está PROIBIDA: os bytes desses
executores são congelados e verificados por hash em
`test_wave_r7e_arquival_exemplos.py` (BLOCO G). Um experimento cujo executor
muda deixa de poder atribuir seus resultados congelados ao mesmo código — é o
ponto inteiro do congelamento.

Então a autoridade arquival vira um módulo próprio, e quem chama passa a fonte
explicitamente. Executor intocado, ambiguidade removida no ponto de chamada.

COMO USAR

    import reliability_occurrence_archival_source as arq
    fz.exemplos_congelados(D, historico=arq.HISTORICO)

`resolver()` existe para quem recebe o parâmetro de fora e quer o mesmo default:

    historico = arq.resolver(historico)

DUAS AUTORIDADES, NUNCA MISTURADAS

    histórico/congelado  →  este módulo (snapshot imutável)
    produção corrente    →  risk_history.json

`build_evolution`, `_reclassify_only_pass` e `reliability_live_audit` continuam
lendo o acervo vivo, como devem. Nenhum deles passa por aqui.
"""
from __future__ import annotations

import hashlib
import io
import json
import os

# Mesma constante que o verificador arquival já publica; declarada aqui para
# que os consumidores tenham UM lugar para importar, em vez de repetir o nome
# do arquivo em cada teste.
HISTORICO = "occurrence_auditor_freeze_history_snapshot_v1.json"
HISTORICO_SHA256 = "430da3e4973b227e"

DEV_TRUTH = "occurrence_auditor_dev_truth_snapshot_v1.json"
DEV_TRUTH_SHA256 = "622082c16255ce7a"


def resolver(historico=None):
    """Resolve a fonte histórica NA CHAMADA, nunca no `def`.

    Um literal como default de parâmetro congela no import e reintroduz, por
    outra porta, o defeito que já custou uma fixture gravada dentro de artefato
    de produção. `None` significa "use a autoridade arquival"; caminho ou
    objeto explícito vence — é assim que um teste injeta acervo simulado sem
    tocar em nada real."""
    return HISTORICO if historico is None else historico


def _sha(caminho: str) -> str:
    return hashlib.sha256(io.open(caminho, "rb").read()).hexdigest()[:16]


def integro() -> dict:
    """Os dois snapshots seguem com o conteúdo congelado?

    Serve de porta para quem for medir contra o arquivo: se a entrada mudou, o
    número que sair depois não descreve o experimento que foi congelado."""
    out = {}
    for nome, caminho, esperado in (
            ("historico", HISTORICO, HISTORICO_SHA256),
            ("dev_truth", DEV_TRUTH, DEV_TRUTH_SHA256)):
        existe = os.path.exists(caminho)
        obtido = _sha(caminho) if existe else ""
        out[nome] = {"caminho": caminho, "existe": existe,
                     "esperado": esperado, "obtido": obtido,
                     "ok": existe and obtido == esperado}
    out["ok"] = all(v["ok"] for k, v in out.items() if k != "ok")
    return out


def carregar(historico=None) -> dict:
    """O acervo histórico como objeto. Aceita caminho ou dict já carregado."""
    h = resolver(historico)
    if isinstance(h, dict):
        return h
    return json.load(io.open(h, encoding="utf-8"))


def main(argv=None) -> int:
    print(json.dumps(integro(), ensure_ascii=False, indent=1))
    return 0 if integro()["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
