#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_human_review_writer.py — caminho oficial para gravar verdade humana.

POR QUE FORMALIZAR AGORA

Os dois primeiros reviews prospectivos foram gravados com scripts avulsos que
chamavam `semantic_v2_shadow.carregar`/`gravar` direto. Funcionou, e cada script
repetiu à mão as mesmas proteções: conferir identidade, recusar sobrescrita,
comparar hash dos campos imutáveis. Repetir proteção à mão escala mal — a
décima adjudicação é onde alguém esquece uma delas, e o holdout é destruído
silenciosamente por uma linha de JSON.

Este módulo é essa operação, uma vez, com portões explícitos.

O QUE ELE GARANTE

  · o caso é resolvido por identidade estável e EXATAMENTE UM caso casa;
  · a verdade é do CASO, não do modelo — todos os registros do caso recebem a
    mesma, e é impossível G1 e G2 divergirem;
  · dry-run é o padrão; escrever exige `aplicar=True`;
  · review existente NUNCA é sobrescrito em silêncio — override é explícito,
    exige motivo e preserva a verdade anterior com proveniência;
  · só `human_review` muda: o hash dos campos imutáveis é conferido antes e
    depois de cada escrita, e divergência aborta;
  · a escrita convive com o cron: funde com o estado mais fresco do disco
    imediatamente antes de gravar, preservando casos que chegaram no meio.

O QUE ELE NÃO É

Não tem autoridade sobre score. `risk_dashboard.py` e `semantic_audit.py` não
importam este módulo, e o teste desta wave prova isso. Verdade humana é
avaliação — a autoridade de produção é a regra determinística.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time

import semantic_v2_shadow as sh
import reliability_pilot_contract as v1
import reliability_pilot_contract_v2 as v2

WRITER_VERSION = "r7ba.hrw.v1"

# Campos que a adjudicação jamais pode tocar. Tudo que o cron observou entra
# aqui; `human_review` é deliberadamente o único ausente.
CAMPOS_IMUTAVEIS = ("article_id", "article_pub_iso", "candidate_event",
                    "case_id", "company", "contract_version", "created_at",
                    "deterministic", "erro", "estado", "evidencia", "finish",
                    "first_seen_iso", "latencia_s", "prompt_version",
                    "requested_model", "actual_model", "saida",
                    "schema_version", "shadow_version", "source", "title",
                    "url", "usage")

# Metadados que TODOS os registros de um mesmo caso têm de compartilhar. Se
# divergirem, os registros não descrevem o mesmo caso e a escrita para.
CAMPOS_COMUNS_DO_CASO = ("article_id", "company", "candidate_event", "url",
                         "title", "first_seen_iso", "contract_version",
                         "schema_version", "prompt_version")

# Enums vêm do contrato — nunca redefinidos aqui, ou sairiam do sincronismo.
ENUMS = {
    "event_asserted": ("ASSERTED", "MENTIONED_ONLY", "DENIED", "UNCLEAR"),
    "company_role": v1.COMPANY_ROLE,
    "currentness": v1.CURRENTNESS,
    "phase": v1.PHASE,
    "centrality": v1.CENTRALITY,
    "occurrence_novelty": v2.OCCURRENCE_NOVELTY,
    "transaction_object": v2.TRANSACTION_OBJECT,
}

TIPOS_DE_REVISOR = ("human",)


class RevisaoRecusada(Exception):
    """Erro de operação — nunca deixa o arquivo pela metade."""


def digest_imutavel(obs: dict) -> str:
    nucleo = {k: obs.get(k) for k in CAMPOS_IMUTAVEIS}
    return hashlib.sha256(
        json.dumps(nucleo, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ── resolução do caso ───────────────────────────────────────────────────────
def resolver_caso(dados: dict, *, empresa: str, evento: str,
                  url: str = "", artigo_id: str = "") -> list:
    """Chaves dos registros do ÚNICO caso artigo×empresa que casa.

    Identidade estável = (article_id, company, candidate_event). A URL, quando
    dada, é traduzida para `article_id` pela mesma função que o cron usa — não
    por comparação de string, que quebraria com querystring ou barra final.
    """
    alvo = artigo_id or (sh.id_artigo(url) if url else "")
    achados = {}
    for chave, o in (dados.get("observacoes") or {}).items():
        if o.get("company") != empresa or o.get("candidate_event") != evento:
            continue
        if alvo and o.get("article_id") != alvo:
            continue
        achados.setdefault(o.get("article_id"), []).append(chave)
    if not achados:
        raise RevisaoRecusada(
            f"NENHUM_CASO: nada casa com empresa={empresa!r} evento={evento!r}"
            + (f" artigo={alvo!r}" if alvo else ""))
    if len(achados) > 1:
        raise RevisaoRecusada(
            f"CASO_AMBIGUO: {len(achados)} artigos distintos casam "
            f"({sorted(achados)}); informe url ou artigo_id")
    return sorted(next(iter(achados.values())))


def conferir_coerencia(dados: dict, chaves: list) -> None:
    """Todos os registros do caso descrevem o mesmo artigo×empresa×candidato."""
    obs = dados["observacoes"]
    for campo in CAMPOS_COMUNS_DO_CASO:
        valores = {json.dumps(obs[k].get(campo), ensure_ascii=False)
                   for k in chaves}
        if len(valores) > 1:
            raise RevisaoRecusada(
                f"CASO_INCOERENTE: registros divergem em {campo!r}: {valores}")
    det = {json.dumps(obs[k].get("deterministic"), ensure_ascii=False,
                      sort_keys=True) for k in chaves}
    if len(det) > 1:
        raise RevisaoRecusada(
            "CASO_INCOERENTE: snapshot determinístico difere entre registros")


# ── validação da revisão ────────────────────────────────────────────────────
def validar_review(review: dict) -> None:
    if not isinstance(review, dict):
        raise RevisaoRecusada("REVIEW_INVALIDA: esperado objeto")
    if not str(review.get("verdict") or "").strip():
        raise RevisaoRecusada("VERDICT_VAZIO: toda adjudicação precisa de veredito")
    if not str(review.get("reviewer") or "").strip():
        raise RevisaoRecusada("REVISOR_VAZIO: adjudicação anônima não é rastreável")
    if review.get("reviewer_type") not in TIPOS_DE_REVISOR:
        raise RevisaoRecusada(
            f"REVISOR_INVALIDO: reviewer_type={review.get('reviewer_type')!r} "
            f"— só {TIPOS_DE_REVISOR} adjudica; modelo não é verdade")
    dims = review.get("dimensoes_adjudicadas") or {}
    if not isinstance(dims, dict):
        raise RevisaoRecusada("DIMENSOES_INVALIDAS: esperado objeto")
    for campo, valor in dims.items():
        # Ausência é legítima: nem todo caso tem verdade humana para toda
        # dimensão, e inventar valor para preencher tabela é pior que o vazio.
        if campo not in ENUMS or valor in (None, ""):
            continue
        if valor not in ENUMS[campo]:
            raise RevisaoRecusada(
                f"ENUM_INVALIDO: {campo}={valor!r} não pertence a "
                f"{tuple(ENUMS[campo])}")


# ── operação ────────────────────────────────────────────────────────────────
def registrar_human_review(*, empresa: str, evento: str, review: dict,
                           url: str = "", artigo_id: str = "",
                           caminho=None, aplicar: bool = False,
                           override: bool = False, motivo: str = "",
                           agora=None) -> dict:
    """Dry-run por padrão. Só grava com `aplicar=True`."""
    validar_review(review)
    if override and not str(motivo or "").strip():
        raise RevisaoRecusada(
            "OVERRIDE_SEM_MOTIVO: corrigir verdade humana exige justificativa "
            "registrada — override silencioso é indistinguível de erro")

    dados = sh.carregar(caminho)
    chaves = resolver_caso(dados, empresa=empresa, evento=evento,
                           url=url, artigo_id=artigo_id)
    conferir_coerencia(dados, chaves)
    obs = dados["observacoes"]
    anteriores = {k: obs[k].get("human_review") for k in chaves}
    ja_revisado = [k for k, v in anteriores.items() if v]

    if ja_revisado and not override:
        raise RevisaoRecusada(
            f"HUMAN_REVIEW_ALREADY_EXISTS: {len(ja_revisado)} de {len(chaves)} "
            f"registro(s) já têm adjudicação. Use override com motivo para "
            f"corrigir de forma versionada — sobrescrever apaga o holdout.")

    novo = dict(review)
    novo.setdefault("reviewed_at", int(agora if agora is not None else time.time()))
    novo.setdefault("reviewed_at_iso", time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(novo["reviewed_at"])))
    novo["writer_version"] = WRITER_VERSION

    resultado = {
        "caso": {"empresa": empresa, "evento": evento,
                 "artigo_id": obs[chaves[0]].get("article_id"),
                 "titulo": obs[chaves[0]].get("title"),
                 "url": obs[chaves[0]].get("url"),
                 "modelos": [obs[k].get("actual_model") for k in chaves]},
        "chaves": chaves,
        "review_anterior": anteriores,
        "review_proposta": novo,
        "override": bool(override),
        "aplicado": False,
        "hashes_antes": {k: digest_imutavel(obs[k]) for k in chaves},
    }
    if not aplicar:
        resultado["motivo_nao_aplicado"] = "dry-run: use aplicar=True"
        return resultado

    # Funde com o estado MAIS FRESCO do disco antes de decidir o que escrever.
    # O cron pode ter acrescentado casos — ou até um modelo novo DESTE caso —
    # enquanto a adjudicação era preparada. `fundir` preserva o que já existia
    # e traz o que é novo; a revisão é aplicada DEPOIS, sobre o estado fundido,
    # para que nenhum registro do caso fique sem a verdade humana.
    fundido = sh.fundir(dados, sh.carregar(caminho))
    chaves_finais = resolver_caso(fundido, empresa=empresa, evento=evento,
                                  url=url, artigo_id=artigo_id)
    conferir_coerencia(fundido, chaves_finais)
    novas = [k for k in chaves_finais if k not in chaves]
    if novas and not override:
        conflito = [k for k in novas if (fundido["observacoes"][k]
                                         .get("human_review"))]
        if conflito:
            raise RevisaoRecusada(
                f"HUMAN_REVIEW_ALREADY_EXISTS: registro(s) {conflito} "
                f"chegaram com adjudicação durante a operação")

    antes = {k: digest_imutavel(fundido["observacoes"][k])
             for k in chaves_finais}
    for k in chaves_finais:
        o = fundido["observacoes"][k]
        anterior = o.get("human_review")
        atual = dict(novo)
        if override:
            atual["override_de"] = anterior
            atual["override_motivo"] = motivo
        o["human_review"] = atual
    depois = {k: digest_imutavel(fundido["observacoes"][k])
              for k in chaves_finais}
    divergentes = [k for k in chaves_finais if antes[k] != depois[k]]
    if divergentes:
        raise RevisaoRecusada(
            f"IMUTAVEIS_ALTERADOS: {divergentes} — nada foi gravado")

    escreveu = sh.gravar(fundido, caminho)
    resultado.update(aplicado=True, escreveu=escreveu,
                     chaves=chaves_finais, hashes_antes=antes,
                     hashes_depois=depois,
                     chaves_novas_no_merge=novas,
                     casos_preservados=len({
                         (o.get("article_id"), o.get("company"),
                          o.get("candidate_event"))
                         for o in fundido["observacoes"].values()}))
    return resultado


def casos_revisados(dados: dict) -> tuple:
    """(observados, revisados) contados por CASO artigo×empresa, não por
    registro de modelo: G1 e G2 do mesmo caso são UM caso revisado."""
    casos = {}
    for o in (dados.get("observacoes") or {}).values():
        casos.setdefault((o.get("article_id"), o.get("company"),
                          o.get("candidate_event")), []).append(o)
    revisados = sum(1 for v in casos.values()
                    if v and all(x.get("human_review") for x in v))
    return len(casos), revisados


def relatorio(res: dict) -> str:
    c = res["caso"]
    rev = res["review_proposta"]
    dims = rev.get("dimensoes_adjudicadas") or {}
    L = ["CASO ENCONTRADO",
         f"  empresa   : {c['empresa']}",
         f"  candidato : {c['evento']}",
         f"  manchete  : {(c['titulo'] or '')[:78]}",
         f"  modelos   : {', '.join(str(m) for m in c['modelos'])}",
         f"  revisão atual: " + ("JÁ EXISTE" if any(res["review_anterior"].values())
                                 else "nenhuma"),
         "",
         "VERDADE HUMANA PROPOSTA",
         f"  veredito  : {rev.get('verdict')}",
         f"  revisor   : {rev.get('reviewer')} ({rev.get('reviewer_type')})"]
    for k in sorted(dims):
        L.append(f"  {k:20s}: {dims[k]}")
    if res.get("override"):
        L += ["", f"OVERRIDE: {rev.get('override_motivo') or '(motivo no payload)'}"]
    L += ["", "CAMPOS IMUTÁVEIS",
          "  " + ("OK — inalterados" if res.get("aplicado")
                  else "conferidos na escrita")]
    if res.get("aplicado"):
        L += ["", "VERDADE HUMANA REGISTRADA",
              f"  registros atualizados : {len(res['chaves'])}",
              f"  arquivo alterado      : {res.get('escreveu')}",
              f"  casos preservados     : {res.get('casos_preservados')}"]
        if res.get("chaves_novas_no_merge"):
            L.append(f"  registros que chegaram durante a operação: "
                     f"{res['chaves_novas_no_merge']}")
    else:
        L += ["", "ESCRITA", f"  NÃO APLICADA — {res.get('motivo_nao_aplicado')}"]
    return "\n".join(L)


# ── verdade de OCORRÊNCIA ───────────────────────────────────────────────────
# Unidade diferente da revisão de artigo, então porta de entrada diferente —
# mas o mesmo arquivo e o mesmo contrato de segurança: dry-run por padrão,
# recusa explícita em vez de escrita parcial, nada de sobrescrever em silêncio.
# Uma função única que fizesse as duas coisas ficaria ambígua sobre o que
# exatamente está sendo afirmado.
#
# O roteamento acontece ANTES do parser existente, e não como subcomando, para
# que nenhuma invocação atual mude de forma: `--empresa` e `--evento` seguem
# obrigatórios exatamente onde já eram.
ACOES_OCORRENCIA = ("occurrence-create", "occurrence-member",
                    "occurrence-relation", "occurrence-report")


def _main_ocorrencia(args: list) -> int:
    import reliability_occurrence_truth as ot
    from pathlib import Path
    acao, resto = args[0], args[1:]
    p = argparse.ArgumentParser(prog=f"writer {acao}",
                                description="Verdade humana de OCORRÊNCIA "
                                            "(dry-run por padrão).")
    p.add_argument("--sidecar", default=None)
    p.add_argument("--apply", action="store_true",
                   help="sem esta flag, nada é escrito")
    p.add_argument("--empresa", default="")
    p.add_argument("--evento", default="")
    p.add_argument("--occurrence-id", default="")
    p.add_argument("--occurrence-b", default="")
    p.add_argument("--url", default="")
    p.add_argument("--artigo-id", default="")
    p.add_argument("--data-do-fato", default=None,
                   help="AAAA-MM-DD; ausente quando desconhecida")
    p.add_argument("--novidade", default="")
    p.add_argument("--fase", default="UNKNOWN")
    p.add_argument("--ancora", default="unknown",
                   choices=("true", "false", "unknown"))
    p.add_argument("--relacao", default="")
    p.add_argument("--identidade", default="",
                   help="JSON com a identidade específica da família")
    p.add_argument("--evidencia", default="")
    p.add_argument("--supersedes", default="")
    p.add_argument("--revisor", default="")
    p.add_argument("--quando", default="")
    a = p.parse_args(resto)
    caminho = Path(a.sidecar) if a.sidecar else None
    dados = sh.carregar(caminho)
    antes = json.dumps(dados, ensure_ascii=False, sort_keys=True)
    try:
        if acao == "occurrence-report":
            print(ot.relatorio(dados))
            return 0
        if not a.revisor or not a.quando:
            raise ot.VerdadeRecusada("SEM_PROVENIENCIA: --revisor e --quando "
                                     "são obrigatórios")
        if acao == "occurrence-create":
            oid = ot.criar_ocorrencia(
                dados, company=a.empresa, event_id=a.evento,
                material_event_date=a.data_do_fato,
                family_identity=(json.loads(a.identidade) if a.identidade else None),
                adjudicated_by=a.revisor, adjudicated_at_iso=a.quando)
            print(f"ocorrência criada: {oid}")
        elif acao == "occurrence-member":
            ancora = {"true": True, "false": False, "unknown": None}[a.ancora]
            mid = ot.adicionar_membership(
                dados, occurrence_truth_id=a.occurrence_id,
                article_ref_=(a.artigo_id or ot.article_ref(a.url)),
                company=a.empresa, event_id=a.evento,
                occurrence_novelty=a.novidade, material_phase=a.fase,
                should_refresh_anchor=ancora, evidence=a.evidencia,
                adjudicated_by=a.revisor, adjudicated_at_iso=a.quando,
                supersedes=a.supersedes)
            print(f"pertinência criada: {mid}")
        elif acao == "occurrence-relation":
            ot.adicionar_relacao(
                dados, occurrence_a=a.occurrence_id, occurrence_b=a.occurrence_b,
                relation=a.relacao, evidence=a.evidencia,
                adjudicated_by=a.revisor, adjudicated_at_iso=a.quando)
            print(f"relação {a.relacao} registrada")
    except ot.VerdadeRecusada as exc:
        print(f"RECUSADO: {exc}")
        return 2
    probs = ot.validar(dados)
    if probs:
        print(f"RECUSADO: STORE_INCONSISTENTE após a operação: {probs}")
        return 2
    if not a.apply:
        print("DRY-RUN — nada foi escrito. Use --apply para persistir.")
        return 0
    if json.dumps(dados, ensure_ascii=False, sort_keys=True) == antes:
        print("nada mudou")
        return 0
    sh.gravar(dados, caminho)
    print("aplicado.")
    return 0


def main(argv=None) -> int:
    _args = list(__import__("sys").argv[1:] if argv is None else argv)
    if _args and _args[0] in ACOES_OCORRENCIA:
        return _main_ocorrencia(_args)
    p = argparse.ArgumentParser(
        description="Grava verdade humana no shadow prospectivo (dry-run por "
                    "padrão).")
    p.add_argument("--empresa", required=True)
    p.add_argument("--evento", required=True, help="candidate_event, ex.: ma")
    p.add_argument("--url", default="", help="desambigua quando há vários artigos")
    p.add_argument("--artigo-id", default="")
    p.add_argument("--review", required=True,
                   help="arquivo JSON com a adjudicação")
    p.add_argument("--sidecar", default=None, help="caminho do shadow")
    p.add_argument("--apply", action="store_true",
                   help="sem esta flag, nada é escrito")
    p.add_argument("--override", action="store_true",
                   help="corrige adjudicação existente; exige --motivo")
    p.add_argument("--motivo", default="")
    a = p.parse_args(argv)
    review = json.load(io.open(a.review, encoding="utf-8"))
    try:
        res = registrar_human_review(
            empresa=a.empresa, evento=a.evento, review=review, url=a.url,
            artigo_id=a.artigo_id,
            caminho=(__import__("pathlib").Path(a.sidecar) if a.sidecar else None),
            aplicar=a.apply, override=a.override, motivo=a.motivo)
    except RevisaoRecusada as exc:
        print(f"RECUSADO: {exc}")
        return 2
    print(relatorio(res))
    if res.get("aplicado"):
        obs, rev = casos_revisados(sh.carregar(
            __import__("pathlib").Path(a.sidecar) if a.sidecar else None))
        print(f"\nOBSERVED N = {obs}   REVIEWED N = {rev}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
