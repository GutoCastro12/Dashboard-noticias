#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_human_review_writer.py — o caminho oficial da verdade humana.

POR QUE ESTE ARQUIVO EXISTE

Os dois primeiros reviews prospectivos foram gravados por scripts avulsos.
Cada um repetiu à mão as mesmas proteções — identidade, não-sobrescrita, hash
dos imutáveis. Isso escala mal: a décima adjudicação é onde alguém esquece uma
delas, e o holdout morre em silêncio, sem teste que perceba.

O que este arquivo protege não é o writer em si, é o holdout: se qualquer um
destes portões cair, uma adjudicação futura pode apagar uma observação
prospectiva e ninguém saberá.

REGRA DE OURO DESTE ARQUIVO

Nenhum teste escreve no sidecar real. Tudo roda em cópia temporária. Os cases
#1 e #2 são tocados apenas em DRY-RUN, e um bloco no fim confere que o arquivo
real terminou byte a byte como começou.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import semantic_v2_shadow as sh
import reliability_human_review_writer as w

PASS = FAIL = 0
REAL = Path("risk_semantic_v2_shadow.json")
SHA_REAL_INICIAL = hashlib.sha256(REAL.read_bytes()).hexdigest()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def recusa(fn, marca, label):
    """Confere que a operação foi recusada PELO MOTIVO CERTO — não por um erro
    qualquer que por acaso também impediria a escrita."""
    try:
        fn()
    except w.RevisaoRecusada as exc:
        check(marca in str(exc), f"{label} (recusa: {str(exc)[:60]})")
        return
    except Exception as exc:                       # noqa: BLE001
        check(False, f"{label} — erro inesperado {type(exc).__name__}: {exc}")
        return
    check(False, f"{label} — NÃO recusou")


def sidecar_temp(dados: dict) -> Path:
    p = Path(tempfile.mkdtemp(prefix="hrw_")) / "shadow.json"
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True))
    return p


REVIEW_OK = {
    "verdict": "TRUE_NEW_ANNOUNCEMENT",
    "reviewer": "Gustavo",
    "reviewer_type": "human",
    "dimensoes_adjudicadas": {"event_asserted": "ASSERTED",
                              "occurrence_novelty": "NEW_OCCURRENCE",
                              "phase": "ANNOUNCED", "centrality": "MAIN"},
}


def caso_sintetico(n_modelos=2, empresa="Vale", evento="ma",
                   com_review=False) -> dict:
    """Caso ainda não adjudicado, com N registros de modelo."""
    base = {"_meta": {"shadow_version": sh.SHADOW_VERSION}, "observacoes": {}}
    aid = sh.id_artigo("https://exemplo.com/materia-sintetica")
    for i in range(n_modelos):
        modelo = f"modelo-{i+1}"
        base["observacoes"][sh.chave(aid, empresa, evento, "v2", "r7ba.p2",
                                     modelo)] = {
            "article_id": aid, "company": empresa, "candidate_event": evento,
            "url": "https://exemplo.com/materia-sintetica",
            "title": "Materia sintetica para teste do writer",
            "first_seen_iso": "2026-08-15 00:00", "contract_version": "v2",
            "schema_version": "r7ba.s2", "prompt_version": "r7ba.p2",
            "actual_model": modelo, "estado": "OK",
            "deterministic": {"scoreable": True},
            "saida": {"events": [{"event_id": evento, "subject": empresa}]},
            "evidencia": {"aceitos": 1},
            "human_review": dict(REVIEW_OK) if com_review else None,
        }
    return base


print("=" * 98)
print("BLOCO A — DRY-RUN É O PADRÃO; NADA É ESCRITO SEM APPLY")
print("=" * 98)
_p = sidecar_temp(caso_sintetico())
_antes = _p.read_bytes()
_r = w.registrar_human_review(empresa="Vale", evento="ma", review=REVIEW_OK,
                              caminho=_p)
check(_r["aplicado"] is False, "[1] sem apply, a operação não aplica")
check(_p.read_bytes() == _antes, "[2] e o arquivo não é tocado byte a byte")
check(len(_r["chaves"]) == 2 and _r["caso"]["empresa"] == "Vale",
      f"[3] o dry-run já resolve o caso e mostra os registros ({len(_r['chaves'])})")
check("hashes_antes" in _r and len(_r["hashes_antes"]) == 2,
      "[4] e exibe o hash dos imutáveis antes de qualquer escrita")

print()
print("=" * 98)
print("BLOCO B — APPLY EXPLÍCITO GRAVA EM TODOS OS MODELOS DO CASO")
print("=" * 98)
_r = w.registrar_human_review(empresa="Vale", evento="ma", review=REVIEW_OK,
                              caminho=_p, aplicar=True)
_d = sh.carregar(_p)
check(_r["aplicado"] and _r["escreveu"], "[5] com apply, escreve")
check(all(o.get("human_review") for o in _d["observacoes"].values()),
      "[6] TODOS os registros do caso recebem a verdade — não só o primeiro")
_verdades = {json.dumps(o["human_review"], sort_keys=True)
             for o in _d["observacoes"].values()}
check(len(_verdades) == 1,
      "[7] e a verdade é IDÊNTICA entre os modelos — verdade é do caso, "
      "não do modelo")
check(all(o["human_review"]["writer_version"] == w.WRITER_VERSION
          for o in _d["observacoes"].values()),
      f"[8] com a versão do writer registrada ({w.WRITER_VERSION})")
check(w.casos_revisados(_d) == (1, 1),
      f"[9] dois registros de modelo contam como UM caso revisado "
      f"({w.casos_revisados(_d)})")

print()
print("=" * 98)
print("BLOCO C — IMUTÁVEIS INTACTOS")
print("=" * 98)
check(_r["hashes_antes"] == _r["hashes_depois"],
      "[10] o hash dos campos imutáveis é igual antes e depois")
_o = next(iter(_d["observacoes"].values()))
check(_o["saida"]["events"][0]["subject"] == "Vale"
      and _o["evidencia"]["aceitos"] == 1 and _o["deterministic"]["scoreable"],
      "[11] saída, evidência e snapshot determinístico seguem lá")
check("human_review" not in w.CAMPOS_IMUTAVEIS,
      "[12] e `human_review` é o único campo fora da lista de imutáveis")

print()
print("=" * 98)
print("BLOCO D — SOBRESCRITA SILENCIOSA É IMPOSSÍVEL")
print("=" * 98)
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        review=REVIEW_OK, caminho=_p,
                                        aplicar=True),
       "HUMAN_REVIEW_ALREADY_EXISTS",
       "[13] segunda escrita normal sobre caso já revisado é bloqueada")
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        review=REVIEW_OK, caminho=_p,
                                        aplicar=True, override=True),
       "OVERRIDE_SEM_MOTIVO",
       "[14] override sem motivo é bloqueado")

print()
print("=" * 98)
print("BLOCO E — OVERRIDE VERSIONADO PRESERVA A VERDADE ANTERIOR")
print("=" * 98)
_novo = dict(REVIEW_OK, verdict="FALSE_DESCRIPTOR")
_r2 = w.registrar_human_review(empresa="Vale", evento="ma", review=_novo,
                               caminho=_p, aplicar=True, override=True,
                               motivo="reavaliação com corpo do artigo")
_d2 = sh.carregar(_p)
_hr = next(iter(_d2["observacoes"].values()))["human_review"]
check(_hr["verdict"] == "FALSE_DESCRIPTOR", "[15] o override grava a nova verdade")
check((_hr.get("override_de") or {}).get("verdict") == "TRUE_NEW_ANNOUNCEMENT",
      "[16] e a verdade ANTERIOR fica preservada em `override_de`")
check(_hr.get("override_motivo") == "reavaliação com corpo do artigo",
      "[17] com o motivo registrado junto")
check(_r2["hashes_antes"] == _r2["hashes_depois"],
      "[18] o override também não mexe em campo imutável")

print()
print("=" * 98)
print("BLOCO F — VALIDAÇÃO DE ENTRADA")
print("=" * 98)
_p2 = sidecar_temp(caso_sintetico())
recusa(lambda: w.registrar_human_review(
    empresa="Vale", evento="ma", caminho=_p2, aplicar=True,
    review=dict(REVIEW_OK, dimensoes_adjudicadas={"occurrence_novelty": "BANANA"})),
    "ENUM_INVALIDO", "[19] enum inventado é rejeitado")
recusa(lambda: w.registrar_human_review(
    empresa="Vale", evento="ma", caminho=_p2, aplicar=True,
    review=dict(REVIEW_OK, dimensoes_adjudicadas={"phase": "POSSE"})),
    "ENUM_INVALIDO", "[20] valor plausível fora do enum também é rejeitado")
recusa(lambda: w.registrar_human_review(
    empresa="Vale", evento="ma", caminho=_p2, aplicar=True,
    review=dict(REVIEW_OK, reviewer="")),
    "REVISOR_VAZIO", "[21] revisor vazio é rejeitado")
recusa(lambda: w.registrar_human_review(
    empresa="Vale", evento="ma", caminho=_p2, aplicar=True,
    review=dict(REVIEW_OK, reviewer_type="model")),
    "REVISOR_INVALIDO", "[22] reviewer_type=model é rejeitado — modelo não é verdade")
recusa(lambda: w.registrar_human_review(
    empresa="Vale", evento="ma", caminho=_p2, aplicar=True,
    review=dict(REVIEW_OK, verdict="")),
    "VERDICT_VAZIO", "[23] veredito vazio é rejeitado")
check(sh.carregar(_p2)["observacoes"] and all(
    o.get("human_review") is None
    for o in sh.carregar(_p2)["observacoes"].values()),
    "[24] e nenhuma dessas tentativas escreveu coisa alguma")

print()
print("=" * 98)
print("BLOCO G — REVISÃO DIMENSIONAL PARCIAL É LEGÍTIMA")
print("=" * 98)
_p3 = sidecar_temp(caso_sintetico())
_parcial = {"verdict": "AMBIGUOUS", "reviewer": "Gustavo",
            "reviewer_type": "human",
            "dimensoes_adjudicadas": {"occurrence_novelty": "UNDETERMINED",
                                      "phase": None, "centrality": ""}}
_rp = w.registrar_human_review(empresa="Vale", evento="ma", review=_parcial,
                               caminho=_p3, aplicar=True)
check(_rp["aplicado"],
      "[25] dimensão ausente/vazia não bloqueia — não se inventa verdade "
      "para preencher tabela")
_hrp = next(iter(sh.carregar(_p3)["observacoes"].values()))["human_review"]
check(_hrp["dimensoes_adjudicadas"]["phase"] is None,
      "[26] e a ausência é preservada como ausência")

print()
print("=" * 98)
print("BLOCO H — IDENTIDADE: EXATAMENTE UM CASO OU NADA")
print("=" * 98)
_p4 = sidecar_temp(caso_sintetico())
recusa(lambda: w.registrar_human_review(empresa="Petrobras", evento="ma",
                                        review=REVIEW_OK, caminho=_p4),
       "NENHUM_CASO", "[27] empresa que não existe: nenhum caso")
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="falencia",
                                        review=REVIEW_OK, caminho=_p4),
       "NENHUM_CASO", "[28] candidato errado: nenhum caso")
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        url="https://exemplo.com/outra",
                                        review=REVIEW_OK, caminho=_p4),
       "NENHUM_CASO", "[29] URL errada: nenhum caso")
_dois = caso_sintetico()
_outro = caso_sintetico()
_aid2 = sh.id_artigo("https://exemplo.com/segunda-materia")
for k, o in list(_outro["observacoes"].items()):
    o = dict(o, article_id=_aid2, url="https://exemplo.com/segunda-materia")
    _dois["observacoes"][k + "|2"] = o
_p5 = sidecar_temp(_dois)
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        review=REVIEW_OK, caminho=_p5),
       "CASO_AMBIGUO", "[30] dois artigos da mesma empresa×evento: aborta ambíguo")
_r5 = w.registrar_human_review(empresa="Vale", evento="ma",
                               url="https://exemplo.com/segunda-materia",
                               review=REVIEW_OK, caminho=_p5)
check(len(_r5["chaves"]) == 2 and _r5["caso"]["artigo_id"] == _aid2,
      "[31] e a URL desambigua corretamente")

print()
print("=" * 98)
print("BLOCO I — REGISTROS INCOERENTES NÃO SÃO ADJUDICADOS JUNTOS")
print("=" * 98)
_inc = caso_sintetico()
_k = sorted(_inc["observacoes"])[0]
_inc["observacoes"][_k]["prompt_version"] = "r7ba.p9"
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        review=REVIEW_OK,
                                        caminho=sidecar_temp(_inc)),
       "CASO_INCOERENTE", "[32] versão de prompt divergente entre registros aborta")
_inc2 = caso_sintetico()
_inc2["observacoes"][sorted(_inc2["observacoes"])[0]]["deterministic"] = {
    "scoreable": False}
recusa(lambda: w.registrar_human_review(empresa="Vale", evento="ma",
                                        review=REVIEW_OK,
                                        caminho=sidecar_temp(_inc2)),
       "CASO_INCOERENTE", "[33] snapshot determinístico divergente aborta")

print()
print("=" * 98)
print("BLOCO J — CRON CONCORRENTE: CASO NOVO NÃO É APAGADO")
print("=" * 98)
# Reproduz a corrida real: o operador carrega o estado, o cron acrescenta um
# caso #3 no disco, e só então o apply acontece.
_base = caso_sintetico()
_p6 = sidecar_temp(_base)
_dados_do_operador = sh.carregar(_p6)          # foto ANTES do cron
_com_c3 = copy.deepcopy(_base)
_aid3 = sh.id_artigo("https://exemplo.com/caso-3-do-cron")
_com_c3["observacoes"][sh.chave(_aid3, "Cemig", "troca_ceo", "v2", "r7ba.p2",
                                "modelo-1")] = {
    "article_id": _aid3, "company": "Cemig", "candidate_event": "troca_ceo",
    "url": "https://exemplo.com/caso-3-do-cron", "title": "Caso 3 do cron",
    "first_seen_iso": "2026-08-15 01:00", "contract_version": "v2",
    "schema_version": "r7ba.s2", "prompt_version": "r7ba.p2",
    "actual_model": "modelo-1", "estado": "OK",
    "deterministic": {"scoreable": True},
    "saida": {"events": []}, "evidencia": {"aceitos": 0},
    "human_review": None}
sh.gravar(_com_c3, _p6)                        # o cron escreveu no meio
_r6 = w.registrar_human_review(empresa="Vale", evento="ma", review=REVIEW_OK,
                               caminho=_p6, aplicar=True)
_d6 = sh.carregar(_p6)
check(any(o["company"] == "Cemig" for o in _d6["observacoes"].values()),
      "[34] o caso que o cron criou durante a operação SOBREVIVE")
check(sum(1 for o in _d6["observacoes"].values()
          if o["company"] == "Vale" and o.get("human_review")) == 2,
      "[35] e a adjudicação do caso alvo foi gravada mesmo assim")
check(next(o for o in _d6["observacoes"].values()
           if o["company"] == "Cemig").get("human_review") is None,
      "[36] o caso novo NÃO recebe a verdade de outro caso")
check(w.casos_revisados(_d6) == (2, 1),
      f"[37] contagem: 2 observados, 1 revisado ({w.casos_revisados(_d6)})")

print()
print("=" * 98)
print("BLOCO K — MODELO NOVO DO MESMO CASO CHEGANDO NO MEIO")
print("=" * 98)
# O caso mais traiçoeiro: o cron acrescenta um TERCEIRO modelo do MESMO caso
# DEPOIS que o writer já leu o estado. Se ele aplicasse só sobre o que leu, o
# registro novo ficaria sem verdade humana e o caso viraria meio-revisado.
#
# Gravar o G3 no disco antes de chamar o writer NÃO testa isso — o writer
# recarrega no início e simplesmente o veria. A corrida real acontece ENTRE a
# leitura inicial e a fusão de escrita, então é ali que ele precisa ser
# injetado. Por isso `carregar` é instrumentado: na primeira chamada devolve a
# foto antiga e deixa o "cron" gravar; na segunda, lê o disco já com o G3.
_p7 = sidecar_temp(caso_sintetico(n_modelos=2))
_foto = sh.carregar(_p7)
_aid = next(iter(_foto["observacoes"].values()))["article_id"]
_g3 = copy.deepcopy(_foto)
_g3["observacoes"][sh.chave(_aid, "Vale", "ma", "v2", "r7ba.p2", "modelo-3")] = {
    **copy.deepcopy(next(iter(_foto["observacoes"].values()))),
    "actual_model": "modelo-3", "human_review": None}

_carregar_real = sh.carregar
_chamadas = {"n": 0}


def _carregar_com_cron_no_meio(caminho=None):
    _chamadas["n"] += 1
    if _chamadas["n"] == 1:
        _carregar_real(caminho)          # o operador lê o estado…
        sh.gravar(_g3, _p7)              # …e o cron grava logo em seguida
        return copy.deepcopy(_foto)      # a foto do operador é a ANTIGA
    return _carregar_real(caminho)


sh.carregar = _carregar_com_cron_no_meio
try:
    _r7 = w.registrar_human_review(empresa="Vale", evento="ma",
                                   review=REVIEW_OK, caminho=_p7, aplicar=True)
finally:
    sh.carregar = _carregar_real
check(_chamadas["n"] >= 2,
      f"[37b] o writer releu o disco antes de gravar ({_chamadas['n']} leituras)")
_d7 = sh.carregar(_p7)
check(len(_d7["observacoes"]) == 3, "[38] os três registros continuam no arquivo")
check(all(o.get("human_review") for o in _d7["observacoes"].values()),
      "[39] e os TRÊS recebem a verdade — inclusive o que chegou no meio")
check(_r7.get("chaves_novas_no_merge"),
      f"[40] o writer registra que houve registro novo na fusão "
      f"({_r7.get('chaves_novas_no_merge')})")
check(w.casos_revisados(_d7) == (1, 1),
      "[41] e continua sendo UM caso revisado, não três")

print()
print("=" * 98)
print("BLOCO L — N MODELOS: NADA ASSUME EXATAMENTE DOIS")
print("=" * 98)
for _n, _qtd in ((42, 1), (43, 3), (44, 5)):
    _pn = sidecar_temp(caso_sintetico(n_modelos=_qtd))
    _rn = w.registrar_human_review(empresa="Vale", evento="ma",
                                   review=REVIEW_OK, caminho=_pn, aplicar=True)
    _dn = sh.carregar(_pn)
    check(len(_rn["chaves"]) == _qtd
          and all(o.get("human_review") for o in _dn["observacoes"].values())
          and w.casos_revisados(_dn) == (1, 1),
          f"[{_n}] caso com {_qtd} modelo(s): todos adjudicados, 1 caso revisado")

print()
print("=" * 98)
print("BLOCO M — DRY-RUN CONTRA OS CASES REAIS #1 E #2")
print("=" * 98)
recusa(lambda: w.registrar_human_review(empresa="Eneva", evento="ma",
                                        review=REVIEW_OK, aplicar=True),
       "HUMAN_REVIEW_ALREADY_EXISTS",
       "[45] case #1 real (Eneva/ma): já revisado, escrita bloqueada")
# A JBS tem DOIS artigos de `troca_ceo` no acervo desde 2026-08-17. Buscar só
# por empresa+família virou ambíguo, e a recusa mudou de motivo: já não é
# "existe revisão" e sim "não sei de qual artigo você fala". A proteção ficou
# MAIS forte, não mais fraca — antes, com um artigo só, a ambiguidade não podia
# nem ser detectada.
recusa(lambda: w.registrar_human_review(empresa="JBS", evento="troca_ceo",
                                        review=REVIEW_OK, aplicar=True),
       "CASO_AMBIGUO",
       "[46] JBS/troca_ceo sem `artigo_id`: escrita bloqueada por ambiguidade")
recusa(lambda: w.registrar_human_review(empresa="JBS", evento="troca_ceo",
                                        artigo_id="5d05e84444486491a30b",
                                        review=REVIEW_OK, aplicar=True),
       "HUMAN_REVIEW_ALREADY_EXISTS",
       "[46b] e COM o `artigo_id` exato: bloqueada por já estar revisada — a "
       "desambiguação resolve o artigo certo, não contorna a proteção")
_real = sh.carregar()
# Observados crescem com o cron; revisados só crescem por decisão humana.
# Fixar o par era voltar a acoplar o teste à cardinalidade viva.
_obs, _rev = w.casos_revisados(_real)
check(_rev >= 7 and _obs >= _rev,
      f"[47] o sidecar real tem {_obs} observados e {_rev} revisados — nunca "
      "menos que os sete adjudicados, e observados nunca abaixo de revisados")

print()
print("=" * 98)
print("BLOCO N — SEM AUTORIDADE SOBRE SCORE")
print("=" * 98)
for _n, _mod in ((48, "risk_dashboard.py"), (49, "semantic_audit.py")):
    _src = io.open(_mod, encoding="utf-8").read()
    check("human_review_writer" not in _src and "human_review" not in _src,
          f"[{_n}] {_mod} não conhece o writer nem verdade humana")
_w = io.open("reliability_human_review_writer.py", encoding="utf-8").read()
check("risk_history" not in _w and "build_evolution" not in _w,
      "[50] o writer não toca histórico nem cálculo de score")
check("events_by_company" not in _w,
      "[51] e não escreve em `events_by_company`")

print()
print("=" * 98)
print("BLOCO O — O SIDECAR REAL TERMINOU COMO COMEÇOU")
print("=" * 98)
check(hashlib.sha256(REAL.read_bytes()).hexdigest() == SHA_REAL_INICIAL,
      "[52] risk_semantic_v2_shadow.json byte a byte idêntico ao início")
_rr = sh.carregar()
_casos = {}
for _o in _rr["observacoes"].values():
    _casos.setdefault((_o["company"], _o["candidate_event"]), []).append(_o)
check(_casos[("Eneva", "ma")][0]["human_review"]["verdict"] == "FALSE_SCOPE",
      "[53] case #1 intacto: FALSE_SCOPE")
# Com dois artigos JBS/troca_ceo, indexar por [0] passou a ser sorteio. O que
# importa é que CADA um manteve o seu veredito — o anúncio e a menção lateral
# são leituras diferentes do mesmo assunto e nenhuma sobrescreveu a outra.
_jbs = {o["article_id"]: o["human_review"]["verdict"]
        for o in _rr["observacoes"].values()
        if (o["company"], o["candidate_event"]) == ("JBS", "troca_ceo")}
check(_jbs.get("5d05e84444486491a30b") == "TRUE_NEW_ANNOUNCEMENT"
      and _jbs.get("201b91aa6b3c1d9e780c") == "MENCIONA_SEM_ANUNCIAR",
      f"[54] os dois casos JBS intactos e distintos ({_jbs})")
# Existe UM override real: o Orizon foi reescrito para acrescentar
# `scoreable_as_ma` no topo, que é onde o relatório lê pontuabilidade. O que
# precisa ser invariante não é "zero overrides" — é que todo override registre
# motivo. Override silencioso é indistinguível de erro; foi por isso que o
# writer passou a exigir justificativa.
_ovr = [o for o in _rr["observacoes"].values()
        if (o.get("human_review") or {}).get("override_de") is not None]
check(all((o["human_review"].get("override_motivo") or "").strip()
          for o in _ovr),
      f"[55] todo override nos cases reais tem motivo registrado "
      f"({len(_ovr)} override(s))")

print()
print("=" * 98)
print(f"RESULTADO WRITER DE VERDADE HUMANA: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
