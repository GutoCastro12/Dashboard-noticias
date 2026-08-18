#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_archival_verifier.py — o experimento é de quando foi feito.

O DEFEITO QUE ISTO CORRIGE

`manifesto_desenvolvimento()` deriva o manifesto de desenvolvimento do acervo
humano INTEIRO — todas as ocorrências, todas as pertinências, todas as
relações. Ao congelar o hash desse manifesto em V1, V2 e V3, fixei o valor de
algo que sempre foi feito para crescer. Consequência: adjudicar Petrobras faz
`dev_manifest_hash` sair de `82cda660cdece064`, e os três experimentos passam a
falhar a própria verificação — sem que nenhum deles tenha mudado.

E não mudaram mesmo. Medido: com o acervo em 7/17/1 e em 10/21/4, a população
congelada continua com os MESMOS 17 alvos e os MESMOS `article_ref`. O que
mudou foi só a largura do manifesto.

Erro meu, no congelamento da V1.

A CORREÇÃO, E O QUE ELA DELIBERADAMENTE NÃO FAZ

Não re-fixa hash nenhum. Não edita um byte de V1, V2 ou V3. Não cria um segundo
acervo canônico — `occurrence_truth` continua sendo a única verdade viva, e ela
cresce.

O que faz é separar duas perguntas que estavam confundidas:

  "o que a verdade humana diz HOJE?"       -> acervo vivo, cumulativo
  "contra o que o experimento foi medido?" -> snapshot imutável do congelamento

O verificador carrega o snapshot histórico e roda contra ele a MESMA geração de
manifesto congelada, sem alterá-la. O arquivo vivo nunca é tocado durante a
verificação.

O SEGUNDO DEFEITO, ENCONTRADO DEPOIS

A primeira versão desta camada consertou metade do problema e disse, em
comentário, que a outra metade não existia: "os demais hashes do experimento
vêm do acervo vivo, porque não dependem dele". Isso era falso.
`exemplos_congelados()` reconstrói o payload dos exemplos a partir de
`risk_history.json` — o acervo de ARTIGOS, que também cresce e também é
corrigido.

O que expôs isso foi uma correção de produção legítima: o artigo da BRF sobre
o adiamento da assembleia estava gravado com a data do feed (2026-05-28)
quando a própria página declara 2025-06-17. Reparar a data (`f62199e`) mudou o
payload do exemplo da BRF, e com ele `example_set_hash` e
`freeze_manifest_hash` — sem que V1, V2 ou V3 tivessem mudado um byte.

Mesmo erro da primeira vez, em outro eixo: congelar um hash derivado de algo
feito para mudar. Por isso há dois snapshots, não um:

  verdade humana consumida  -> occurrence_auditor_dev_truth_snapshot_v1
  artigos consumidos        -> occurrence_auditor_freeze_history_snapshot_v1

Nenhum hash histórico foi reescrito. `example_set_hash` continua sendo
`6ea9a6519b3066bb`, e agora volta a bater porque a entrada voltou a ser a
histórica. A correção da BRF em produção permanece de pé: o exemplo congelado
guarda o que a V3 VIU, o histórico vivo guarda o que é VERDADE hoje. As duas
coisas convivem, e é isso que o arquivo tinha de garantir desde o começo.

POR QUE O SNAPSHOT É HONESTO SOBRE SI MESMO

Ele foi reconstruído agora, não commitado antes da V1. Diz isso no próprio
`_meta`. A reconstrução é verificável: o estado pré-Petrobras está em
`363d9c8`, e o manifesto derivado dele bate com o hash publicado. Fingir que o
snapshot é anterior seria inventar proveniência.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json

import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3

SNAPSHOT = "occurrence_auditor_dev_truth_snapshot_v1.json"
SNAPSHOT_SHA256 = "622082c16255ce7a"
MANIFESTO_HISTORICO = "82cda660cdece064"
# Segundo snapshot imutável: os ARTIGOS que os experimentos consumiram. O
# primeiro desacoplou a verdade humana; este desacopla a entrada de artigos.
# Ver `SNAPSHOT_HISTORICO` abaixo e o cabeçalho "O SEGUNDO DEFEITO".
SNAPSHOT_HISTORICO = "occurrence_auditor_freeze_history_snapshot_v1.json"
SNAPSHOT_HISTORICO_SHA256 = "430da3e4973b227e"
EXAMPLE_SET_HISTORICO = "6ea9a6519b3066bb"
VERIFIER_VERSION = "occurrence.auditor.archival.v2"

# Cada experimento e o hash de manifesto que ele realmente carregou ao ser
# publicado. Literais, escritos à mão: `esperado = calcular()` passaria por
# construção e não provaria nada.
EXPERIMENTOS = {
    "V1": (v1, "82cda660cdece064"),
    "V2": (v2, "82cda660cdece064"),
    "V3": (v3, "82cda660cdece064"),
}


def _hash(obj) -> str:
    bruto = (obj if isinstance(obj, str)
             else json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def carregar_snapshot(caminho: str = SNAPSHOT) -> dict:
    """Lê o snapshot histórico. Somente leitura — o acervo vivo não entra aqui."""
    return json.load(io.open(caminho, encoding="utf-8"))


def checksum_snapshot(caminho: str = SNAPSHOT) -> str:
    return hashlib.sha256(
        io.open(caminho, "rb").read()).hexdigest()[:16]


def manifesto_historico(snap: dict) -> str:
    """Roda a geração de manifesto CONGELADA sobre o snapshot.

    A função vem de `reliability_occurrence_auditor_freeze` sem alteração: o
    ponto é provar que a semântica congelada, alimentada com a entrada
    histórica, ainda produz o hash histórico. Reimplementá-la aqui só provaria
    que sei copiar código."""
    return _hash(v1.manifesto_desenvolvimento(snap))


def verificar_historico(caminho: str = SNAPSHOT,
                        historico: str = SNAPSHOT_HISTORICO) -> dict:
    """Verifica os três experimentos contra o snapshot, e o snapshot contra si.

    Devolve um relatório em vez de levantar exceção: quem chama precisa poder
    exibir QUAL parte divergiu, não só que algo divergiu."""
    problemas = []
    cs = checksum_snapshot(caminho)
    if cs != SNAPSHOT_SHA256:
        problemas.append(("snapshot_checksum", SNAPSHOT_SHA256, cs))
    snap = carregar_snapshot(caminho)
    meta = snap.get("_meta", {})
    if meta.get("role") != "HISTORICAL_FREEZE_INPUT":
        problemas.append(("snapshot_role", "HISTORICAL_FREEZE_INPUT",
                          meta.get("role")))
    if meta.get("current_authority") != "NONE":
        problemas.append(("snapshot_authority", "NONE",
                          meta.get("current_authority")))
    obtido = manifesto_historico(snap)
    if obtido != MANIFESTO_HISTORICO:
        problemas.append(("dev_manifest_hash", MANIFESTO_HISTORICO, obtido))
    cs_hist = checksum_snapshot(historico)
    if cs_hist != SNAPSHOT_HISTORICO_SHA256:
        problemas.append(("snapshot_historico_checksum",
                          SNAPSHOT_HISTORICO_SHA256, cs_hist))
    meta_h = carregar_snapshot(historico).get("_meta", {})
    if meta_h.get("role") != "HISTORICAL_FREEZE_INPUT":
        problemas.append(("snapshot_historico_role", "HISTORICAL_FREEZE_INPUT",
                          meta_h.get("role")))
    if meta_h.get("current_authority") != "NONE":
        problemas.append(("snapshot_historico_authority", "NONE",
                          meta_h.get("current_authority")))
    por_experimento = {}
    for nome, (mod, esperado) in EXPERIMENTOS.items():
        # AMBAS as entradas vêm de snapshot. A versão anterior deixava a
        # entrada de ARTIGOS no acervo vivo, "porque não depende da verdade" —
        # e isso era falso: `exemplos_congelados` reconstrói o payload dos
        # exemplos a partir de `risk_history.json`. Bastou a correção legítima
        # da data de publicação do artigo da BRF (f62199e) para `example_set_
        # hash` e `freeze_manifest_hash` mudarem sem que experimento nenhum
        # tivesse mudado. O acervo vivo não é entrada de arquivo.
        div = [d for d in mod.verificar_congelamento(
            json.loads(json.dumps(snap)), historico=historico)]
        por_experimento[nome] = {
            "dev_manifest_esperado": esperado,
            "dev_manifest_obtido": obtido,
            "ok": obtido == esperado and not div,
            "divergencias": div,
        }
        if not por_experimento[nome]["ok"]:
            problemas.append((f"experimento_{nome}", esperado, div or obtido))
    return {"verifier_version": VERIFIER_VERSION,
            "snapshot": caminho, "snapshot_checksum": cs,
            "snapshot_historico": historico,
            "snapshot_historico_checksum": cs_hist,
            "snapshot_counts": meta.get("source_truth_counts"),
            "dev_manifest_hash": obtido,
            "por_experimento": por_experimento,
            "ok": not problemas, "problemas": problemas}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Verifica V1/V2/V3 contra o snapshot histórico (só leitura).")
    p.add_argument("--snapshot", default=SNAPSHOT)
    p.add_argument("--snapshot-historico", default=SNAPSHOT_HISTORICO)
    p.add_argument("--json-out", default=None)
    a = p.parse_args(argv)
    rel = verificar_historico(a.snapshot, a.snapshot_historico)
    saida = json.dumps(rel, ensure_ascii=False, indent=1, sort_keys=True)
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(saida)
    else:
        print(saida)
    return 0 if rel["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
