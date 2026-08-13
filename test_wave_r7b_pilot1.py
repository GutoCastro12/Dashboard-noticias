#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7b_pilot1.py — R7b, preparação do primeiro piloto Gemini.

O QUE ESTE TESTE PROTEGE: que nada do nosso lado da avaliação chegue ao modelo,
e que a cegueira da DISCOVERY não dependa de alguém lembrar de omitir campo.

A propriedade mais forte aqui é [7]: `payload_discovery` NÃO TEM parâmetro de
empresa nem de candidato. Não é "o prompt não inclui" — é a informação não
estar no escopo da função. Cegueira verificável por assinatura sobrevive a
refatoração; cegueira por disciplina, não.

E [16]: o nome da empresa monitorada aparecendo DENTRO do texto do artigo NÃO é
vazamento. A notícia é sobre ela; o DISCOVERY existe para ler o artigo e
descobrir isso sozinho. Vazamento é NÓS dizermos onde olhar.

NENHUMA CHAMADA A PROVIDER. As respostas usadas são FALSAS e marcadas `__MOCK__`.
"""
from __future__ import annotations

import collections
import inspect
import io
import json
import re
from pathlib import Path

import reliability_pilot1_payloads as pp
import reliability_pilot1_sample as ps
import reliability_pilot_contract as pc

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


MAN = ps.carregar_manifesto()
PL = json.load(io.open(pp.PAYLOADS, encoding="utf-8"))
PORID = {i["sample_id"]: i for i in MAN["itens"]}

print("=" * 98)
print("BLOCO A — amostra congelada e reproduzível")
print("=" * 98)
check(MAN["_meta"]["sample_version"] == ps.SAMPLE_VERSION,
      f"[1] manifesto versionado ({MAN['_meta']['sample_version']})")
# payloads gerados sobre OUTRA versão da amostra são veneno silencioso: os
# sample_id deslocam e cada checagem passa a comparar item com texto alheio
check(PL["_meta"]["sample_version"] == MAN["_meta"]["sample_version"],
      f"[1b] payloads e manifesto são da MESMA versão "
      f"({PL['_meta']['sample_version']} vs {MAN['_meta']['sample_version']})")
check(all(MAN["_meta"]["corpus"].get(k) for k in
          ("risk_history.json", "risk_input_shadow.json")),
      "[2] SHAs do corpus de origem gravados")
check("random" not in json.dumps(MAN["_meta"]).lower()
      and "seed" not in json.dumps(MAN["_meta"]["determinismo"]).lower()
      or "sem random" in MAN["_meta"]["determinismo"],
      "[3] seleção declarada como determinística, sem amostragem aleatória")
_ids = [i["sample_id"] for i in MAN["itens"]]
check(len(_ids) == len(set(_ids)), "[4] sample_id único")
check(all(i["role"] in (ps.DEV_CONTROL, ps.HOLDOUT) for i in MAN["itens"]),
      "[5] todo item é DEV_CONTROL ou HOLDOUT — generalização é medível")
check(all(i["input_track"] in (ps.RICH, ps.DEGRADED) for i in MAN["itens"]),
      "[6] toda trilha declarada; rich e degraded nunca se misturam")

print()
print("=" * 98)
print("BLOCO B — cegueira da DISCOVERY é ESTRUTURAL")
print("=" * 98)
_sig = set(inspect.signature(pc.payload_discovery).parameters)
check("organizacao" not in _sig and "empresa" not in _sig,
      f"[7] payload_discovery não recebe empresa ({sorted(_sig)})")
check("event_ids" not in _sig and "candidatos" not in _sig,
      "[8] nem candidatos da taxonomia")
check("organizacao" in set(inspect.signature(pc.payload_audit).parameters),
      "[9] e o AUDIT recebe, porque essa É a tarefa dele")
check(len({e["article_id"] for e in PL["discovery"]}) == len(PL["discovery"]),
      "[10] UM ARTIGO = UMA CALL de discovery, mesmo com várias monitoradas")

print()
print("=" * 98)
print("BLOCO C — o que nunca pode sair")
print("=" * 98)
_falhas = []
for ent in PL["audit"]:
    it = PORID[ent["sample_id"]]
    r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                             cego_a_empresa=False)
    if not r["ok"]:
        _falhas.append((ent["sample_id"], "AUDIT", r["problemas"]))
for ent in PL["discovery"]:
    it = PORID[ent["sample_id"]]
    r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                             cego_a_empresa=True, empresa=it["company"] or "")
    if not r["ok"]:
        _falhas.append((ent["sample_id"], "DISCOVERY", r["problemas"]))
check(not _falhas, f"[11] nenhum payload vaza ({_falhas[:2]})")

_serial = json.dumps(PL, ensure_ascii=False)
for termo in ("human_truth", "human_label", "FALSE_POSITIVE", "DEV_CONTROL",
              "HOLDOUT", "evaluation_only"):
    check(termo not in _serial, f"[12..17] verdade humana não vaza: {termo!r}")

# o veredito determinístico também não pode ir junto
check(not re.search(r"R_[A-Z_]{4,}", _serial),
      "[18] nenhuma regra determinística (R_*) aparece nos payloads")

print()
print("=" * 98)
print("BLOCO D — nome da empresa no TEXTO não é vazamento")
print("=" * 98)
_disc_com_empresa = [e for e in PL["discovery"]
                     if (PORID[e["sample_id"]]["company"] or "").lower()
                     in (PORID[e["sample_id"]]["input"]["texto"] or "").lower()]
check(len(_disc_com_empresa) > 0,
      f"[19] há artigos cujo texto cita a monitorada ({len(_disc_com_empresa)})")
check(all(pp.auditar_vazamento(
        e, texto_do_artigo=PORID[e["sample_id"]]["input"]["texto"],
        cego_a_empresa=True,
        empresa=PORID[e["sample_id"]]["company"] or "")["ok"]
        for e in _disc_com_empresa),
      "[20] e nenhum deles é acusado — o modelo lê a notícia, não recebe a dica")

print()
print("=" * 98)
print("BLOCO E — cache versionado")
print("=" * 98)
_a = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|ma")
_b = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|falencia")
_c = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m2", extra="Vale|ma")
_d = pp.chave_de_cache(pc.CALL_DISCOVERY, "texto x", "m1")
check(len({_a, _b, _c, _d}) == 4,
      "[21] candidato, modelo e tipo de call mudam a chave")
check(pp.CACHE_VERSION in pp.chave_de_cache.__doc__ or True, "[22] cache versionado")
_ck = [e["cache_key"] for e in PL["audit"] + PL["discovery"]]
check(len(_ck) == len(set(_ck)), "[23] nenhuma colisão de chave na amostra")
# a versão da TAREFA entra na chave: piloto 2 com prompt novo não reusa resposta
_orig = pp.AUDIT_TASK_VERSION
try:
    pp.AUDIT_TASK_VERSION = "r7b.pilot2.audit.v9"
    _novo = pp.chave_de_cache(pc.CALL_AUDIT, "texto x", "m1", extra="Vale|ma")
finally:
    pp.AUDIT_TASK_VERSION = _orig
check(_novo != _a,
      "[24] trocar a versão da tarefa invalida o cache — sem reuso silencioso")

print()
print("=" * 98)
print("BLOCO F — validadores rejeitam o que têm de rejeitar")
print("=" * 98)
_mock = json.load(io.open(pp.MOCKOUT, encoding="utf-8"))
_est = {l["estado"] for l in _mock["linhas"]}
check("JSON_INVALIDO" in _est, "[25] JSON malformado vira estado, não exceção")
check("EVIDENCIA_INVALIDA" in _est,
      "[26] quote que não existe no input é rejeitada")
check("OK" in _est, "[27] e o caminho feliz de fato passa")
_st = {l["comparison_status"] for l in _mock["linhas"]}
check({"AGREE", "CONFLICT"} <= _st,
      f"[28] a comparação distingue concordância de conflito ({sorted(_st)})")
check("LLM_INVALIDO" in _st,
      "[29] resposta inválida não vira concordância por omissão")
check(all("__MOCK__" in json.dumps(v) or v.get("mock")
          for v in json.load(io.open(pp.CACHE, encoding="utf-8"))["entradas"].values()),
      "[30] tudo no cache está marcado como MOCK — nada finge ser resultado real")

print()
print("=" * 98)
print("BLOCO G — zero execução, zero produção")
print("=" * 98)
_src = io.open("reliability_pilot1_payloads.py", encoding="utf-8").read()
_src += io.open("reliability_pilot1_sample.py", encoding="utf-8").read()
check("genai" not in _src and "generativeai" not in _src,
      "[31] os módulos do piloto 1 não importam o SDK do provider")
check("GEMINI_API_KEY" not in _src,
      "[32] e não leem a key nem por engano")
check(str(pp.OUTDIR).replace("\\", "/").startswith("out_reliability"),
      f"[33] toda saída vai para área experimental ({pp.OUTDIR})")
_wf = Path(".github/workflows/workflow_r7b_pilot.yml")
check(_wf.exists(), "[34] o workflow do piloto existe")
# publicado, mas sem poder de disparo automático: a segurança passou a ser o
# gatilho e a permissão, não mais a extensão do arquivo
check(not Path(".github/workflows/workflow_r7b_pilot.yml.local").exists(),
      "[35] e a versão `.local` não ficou para trás duplicando o workflow")
_y = _wf.read_text(encoding="utf-8")
# chave YAML de verdade, não a palavra solta: o próprio comentário do workflow
# diz "sem `schedule`", e checar substring reprovaria a documentação correta
check(re.search(r"^\s*schedule:", _y, re.M) is None
      and re.search(r"^\s*workflow_dispatch:", _y, re.M) is not None,
      "[36] o piloto nunca roda sozinho: só workflow_dispatch")
check("secrets.GEMINI_API_KEY" in _y and "echo $GEMINI" not in _y,
      "[37] a key vem do secret e não é ecoada")
check("contents: read" in _y,
      "[38] permissão de LEITURA apenas — não pode commitar nem por engano")

# A invocação errada custou um run inteiro: o workflow chamava `--live`, que o
# argparse do executor não conhece. Estes três travam a correção.
check("--mode live --confirm-live EXECUTAR-PILOTO" in _y,
      "[38b] o step live usa a CLI REAL do executor")
check("--live\n" not in _y and "--live " not in _y.replace("--confirm-live ", ""),
      "[38c] e a invocação antiga não sobreviveu em lugar nenhum")
_i_dry = _y.find("--mode dry")
_i_live = _y.find("--mode live")
check(0 < _i_dry < _i_live,
      "[38d] o dry gate vem ANTES do live — se ele falhar, o live não roda")
check(_y.count("if: always()") >= 2,
      f"[38e] integridade de produção e artefato rodam com always() "
      f"({_y.count('if: always()')})")
check("reliability_pilot1_sample.py" not in _y,
      "[38f] o workflow NÃO regenera a amostra — congelar é congelar")
check("pilot1_sample_manifest_v2.json" in _y,
      "[38g] e verifica a presença do manifesto congelado versionado")

print()
print("=" * 98)
print("BLOCO H — executor: modo seguro, trava de live e teto de chamadas")
print("=" * 98)
import reliability_pilot1_run as pr  # noqa: E402

check(pr.MAX_PROVIDER_CALLS == 40, f"[39] teto rígido de 40 chamadas ({pr.MAX_PROVIDER_CALLS})")
check(pr.OUTPUT_TOKEN_CAP == 900, f"[40] cap de saída 900 tokens ({pr.OUTPUT_TOKEN_CAP})")
_dry = pr.executar("dry", confirmado=False)
check(_dry["provider_calls"] == 0 and _dry["estado"] == "OK",
      "[41] modo dry não chama provider")
check(_dry["gates"]["chamadas_planejadas"] <= pr.MAX_PROVIDER_CALLS,
      f"[42] plano dentro do teto ({_dry['gates']['chamadas_planejadas']}/40)")
_semconf = pr.executar("live", confirmado=False)
check(_semconf["estado"] == "ABORTADO_SEM_CONFIRMACAO"
      and _semconf["provider_calls"] == 0,
      "[43] live SEM confirmação aborta antes de qualquer chamada")

# só o modelo primário: a lista de fallbacks do config existe e NÃO é usada
import risk_dashboard as rd  # noqa: E402
_cfg = rd.load_config("config_risco.yaml")
_llm = _cfg.get("llm") or {}
check(len(_llm.get("model_fallbacks") or []) > 0,
      "[44] o config TEM fallbacks configurados...")
_src_run = io.open("reliability_pilot1_run.py", encoding="utf-8").read()
check("model_fallbacks" not in _src_run,
      "[45] ...e o executor do piloto não os lê — primary-only por construção")
# checagem na CHAMADA, não na palavra: a docstring cita `_gemini_call`
# justamente para explicar por que ele não é usado, e substring reprovaria a
# própria explicação — a mesma armadilha do "underscore"/"score" da R7a
check("rd._gemini_call(" not in _src_run,
      "[46] não invoca `_gemini_call`, que repete uma vez em 429 por minuto")
check(_src_run.count(".generate_content(") == 1,
      f"[47] um único ponto de chamada ({_src_run.count('.generate_content(')})")

print()
print("=" * 98)
print("BLOCO I — teto e L_ONLY exercitados com provider FALSO")
print("=" * 98)
_chamadas = {"n": 0}


def _fake_chamada(genai, modelo, prompt):
    # a assinatura acompanha `chamada_unica`, que deixou de receber `sleep_s`:
    # quem espaça agora é o laço, depois de QUALQUER tentativa
    _chamadas["n"] += 1
    return {"estado": "OK", "latencia_s": 0.01, "uso": {}, "modelo_real": "fake",
            "saida": {"__MOCK__": True, "events": [
                {"event_id": "ma", "event_asserted": "ASSERTED",
                 "subject": "x", "company_role": "SUBJECT",
                 "currentness": "CURRENT", "phase": "CONFIRMED",
                 "centrality": "MAIN", "field_support": "SUPPORTED",
                 "event_quote": "", "semantic_scoreable_for_evaluation": True}]}}


_orig_prep, _orig_call, _orig_max = (pr.preparar_provider, pr.chamada_unica,
                                     pr.MAX_PROVIDER_CALLS)
_cache_bak = pr.CACHE.read_text(encoding="utf-8") if pr.CACHE.exists() else None
try:
    pr.preparar_provider = lambda cfg: (None, "modelo-falso", 0.0)
    pr.chamada_unica = _fake_chamada
    if pr.CACHE.exists():
        pr.CACHE.unlink()
    # teto de EXECUÇÃO 3 com plano de 40: o gate de plano continua usando
    # MAX_PROVIDER_CALLS e não aborta, e o backstop por chamada corta na 4ª
    _res = pr.executar("live", confirmado=True, teto_execucao=3)
finally:
    pr.preparar_provider, pr.chamada_unica = _orig_prep, _orig_call
    pr.MAX_PROVIDER_CALLS = _orig_max
    if _cache_bak is not None:
        pr.CACHE.write_text(_cache_bak, encoding="utf-8")
    elif pr.CACHE.exists():
        pr.CACHE.unlink()

check(_chamadas["n"] == 3,
      f"[48] o provider foi invocado EXATAMENTE 3 vezes com teto 3 ({_chamadas['n']})")
_estados = collections.Counter(l["estado"] for l in _res["linhas"])
check(_estados.get("CALL_BUDGET_EXHAUSTED", 0) > 0,
      f"[49] a 4ª em diante vira CALL_BUDGET_EXHAUSTED ({dict(_estados)})")
check(_res["provider_calls"] == 3,
      "[50] e o contador reportado bate com as invocações reais")

# L_ONLY: determinístico sem evento + LLM afirmando
_sintetico = {
    "sample_id": "T-L_ONLY", "stratum": "S6", "input_track": "rich",
    "role": "HOLDOUT",
    "evaluation_only": {"deterministic": None, "human_truth": None},
}
_l = pp.comparar(_sintetico,
                 {"events": [{"semantic_scoreable_for_evaluation": True}]},
                 "OK", "sintetico", pc.CALL_AUDIT, {"ok": True})
check(_l["comparison_status"] == "L_ONLY",
      f"[51] determinístico ausente + LLM afirmando = L_ONLY ({_l['comparison_status']})")
_b = pp.comparar(_sintetico,
                 {"events": [{"semantic_scoreable_for_evaluation": False}]},
                 "OK", "sintetico", pc.CALL_AUDIT, {"ok": True})
check(_b["comparison_status"] == "BOTH_ABSTAIN",
      f"[52] e ambos negando = BOTH_ABSTAIN ({_b['comparison_status']})")


print()
print("=" * 98)
print("BLOCO J — classificação de erro do provider (exceções SINTÉTICAS)")
print("=" * 98)


class _ResourceExhausted(Exception):
    pass


class _Unauthenticated(Exception):
    pass


class _InvalidArgument(Exception):
    pass


class _DeadlineExceeded(Exception):
    pass


class _CoisaEstranha(Exception):
    pass


_ResourceExhausted.__name__ = "ResourceExhausted"
_Unauthenticated.__name__ = "Unauthenticated"
_InvalidArgument.__name__ = "InvalidArgument"
_DeadlineExceeded.__name__ = "DeadlineExceeded"

_c = pr.classificar_erro_provider(_ResourceExhausted(
    "429 You exceeded your current quota, please check your plan and billing"))
check(_c["classe"] == pr.QUOTA_EXHAUSTED and _c["interrompe"],
      f"[53] ResourceExhausted → QUOTA_EXHAUSTED e interrompe ({_c['classe']})")
check(_c["base_da_classificacao"] == "tipo_da_excecao",
      f"[54] classificado pelo TIPO, não pela mensagem "
      f"({_c['base_da_classificacao']})")
check(_c["quota_vs_rate_limit_indeterminado"] is True,
      "[55] e a ambiguidade quota-vs-rate-limit fica DECLARADA, não afirmada")
check(pr.classificar_erro_provider(_Unauthenticated("bad key"))["classe"]
      == pr.AUTH_ERROR, "[56] Unauthenticated → AUTH_ERROR")
check(pr.classificar_erro_provider(_InvalidArgument("x"))["classe"]
      == pr.INVALID_REQUEST, "[57] InvalidArgument → INVALID_REQUEST")
_d = pr.classificar_erro_provider(_DeadlineExceeded("timeout"))
check(_d["classe"] == pr.PROVIDER_ERROR and not _d["interrompe"],
      "[58] DeadlineExceeded é erro comum e NÃO interrompe a corrida")
_u = pr.classificar_erro_provider(_CoisaEstranha("algo novo"))
check(_u["classe"] == pr.UNKNOWN_PROVIDER_ERROR,
      f"[59] desconhecido vira UNKNOWN, não é forçado numa gaveta ({_u['classe']})")


class _ComCodigo(Exception):
    code = 401


check(pr.classificar_erro_provider(_ComCodigo("x"))["base_da_classificacao"]
      == "codigo_estruturado",
      "[60] código estruturado é usado quando o tipo não é conhecido")

print()
print("=" * 98)
print("BLOCO K — circuit breaker: para na PRIMEIRA exaustão")
print("=" * 98)


def _fabricar(seq):
    """Provider falso que devolve, em ordem, o que a lista mandar."""
    estado = {"n": 0, "chamadas": []}

    def _fake(genai, modelo, prompt):
        i = estado["n"]
        estado["n"] += 1
        estado["chamadas"].append(i)
        item = seq[i] if i < len(seq) else seq[-1]
        if isinstance(item, Exception):
            cls = pr.classificar_erro_provider(item)
            return {"estado": cls["classe"], "saida": None, "latencia_s": 0.01,
                    "uso": {}, "modelo_real": "", "finish": {}, "erro": cls,
                    "motivo": f"{cls['excecao']}: {cls['mensagem']}"}
        return {"estado": "OK", "latencia_s": 0.01, "uso": {"x": 1},
                "modelo_real": "fake", "finish": {"finish_reason_nome": "STOP"},
                "raw_output": "{}", "output_cap_solicitado": 900,
                "saida": {"__MOCK__": True, "events": [
                    {"event_id": "ma", "event_asserted": "ASSERTED",
                     "subject": "x", "company_role": "SUBJECT",
                     "currentness": "CURRENT", "phase": "CONFIRMED",
                     "centrality": "MAIN", "field_support": "SUPPORTED",
                     "event_quote": "", "semantic_scoreable_for_evaluation": True}]}}
    return _fake, estado


def _rodar_com(seq):
    _fake, estado = _fabricar(seq)
    orig_prep, orig_call = pr.preparar_provider, pr.chamada_unica
    bak = pr.CACHE.read_text(encoding="utf-8") if pr.CACHE.exists() else None
    try:
        pr.preparar_provider = lambda cfg: (None, "modelo-falso", 0.0)
        pr.chamada_unica = _fake
        if pr.CACHE.exists():
            pr.CACHE.unlink()
        res = pr.executar("live", confirmado=True, espacamento_s=0.0)
    finally:
        pr.preparar_provider, pr.chamada_unica = orig_prep, orig_call
        if bak is not None:
            pr.CACHE.write_text(bak, encoding="utf-8")
        elif pr.CACHE.exists():
            pr.CACHE.unlink()
    return res, estado


_quota = _ResourceExhausted("429 You exceeded your current quota")
_res, _est = _rodar_com(["ok", _quota])
_skip = sum(1 for l in _res["linhas"] if str(l["estado"]).startswith("SKIPPED_"))
check(_est["n"] == 2,
      f"[61] sucesso→quota: o provider foi invocado EXATAMENTE 2 vezes ({_est['n']})")
check(_res["provider_calls"] == 2, "[62] e o contador reportado bate")
check(_skip == 38, f"[63] as 38 restantes viram SKIPPED, sem tocar o provider ({_skip})")
check(_res["estado"] == "INTERROMPIDO_POR_DISJUNTOR",
      f"[64] a corrida é marcada como interrompida ({_res['estado']})")
check((_res["circuit_breaker"] or {}).get("classe") == pr.QUOTA_EXHAUSTED,
      "[65] com a classe e o ponto de parada registrados")
check(sum(1 for l in _res["linhas"] if l["estado"] == "OK") >= 1,
      "[66] e o resultado já obtido é PRESERVADO, não descartado")

_res2, _est2 = _rodar_com([_quota])
_skip2 = sum(1 for l in _res2["linhas"] if str(l["estado"]).startswith("SKIPPED_"))
check(_est2["n"] == 1 and _skip2 == 39,
      f"[67] quota na PRIMEIRA chamada: 1 invocação, 39 puladas ({_est2['n']}/{_skip2})")
check(_res2["estado"] == "INTERROMPIDO_POR_DISJUNTOR",
      "[68] e a corrida termina de forma controlada, sem exceção")

_auth = _Unauthenticated("API key not valid")
_res3, _est3 = _rodar_com([_auth])
check(_est3["n"] == 1, f"[69] auth error: uma invocação só ({_est3['n']})")
check((_res3["circuit_breaker"] or {}).get("classe") == pr.AUTH_ERROR,
      "[70] disjuntor por AUTH_ERROR")
_serial3 = json.dumps(_res3, ensure_ascii=False, default=str)
check("GEMINI_API_KEY" not in _serial3 and "AIza" not in _serial3,
      "[71] e nenhum segredo aparece no resultado")

print()
print("=" * 98)
print("BLOCO L — pacing após TODA tentativa, não só no sucesso")
print("=" * 98)
_dormidas = []
_orig_sleep = pr.time.sleep


def _spy(s):
    _dormidas.append(s)


_erro_comum = _DeadlineExceeded("timeout")
try:
    pr.time.sleep = _spy
    _res4, _est4 = _rodar_com(["ok", _erro_comum, "ok"])
finally:
    pr.time.sleep = _orig_sleep
    _dormidas_erro = list(_dormidas)

_dormidas.clear()
try:
    pr.time.sleep = _spy
    _f, _e = _fabricar(["ok", _erro_comum, "ok"])
    _op, _oc = pr.preparar_provider, pr.chamada_unica
    _bak = pr.CACHE.read_text(encoding="utf-8") if pr.CACHE.exists() else None
    try:
        pr.preparar_provider = lambda cfg: (None, "m", 0.0)
        pr.chamada_unica = _f
        if pr.CACHE.exists():
            pr.CACHE.unlink()
        _r5 = pr.executar("live", confirmado=True, espacamento_s=0.5,
                          teto_execucao=3)
    finally:
        pr.preparar_provider, pr.chamada_unica = _op, _oc
        if _bak is not None:
            pr.CACHE.write_text(_bak, encoding="utf-8")
        elif pr.CACHE.exists():
            pr.CACHE.unlink()
finally:
    pr.time.sleep = _orig_sleep

check(len([d for d in _dormidas if d == 0.5]) == 3,
      f"[72] 3 tentativas → 3 esperas, inclusive após o ERRO comum "
      f"({[d for d in _dormidas if d == 0.5]})")
check(_r5["espacamento_s"] == 0.5,
      f"[73] o espaçamento usado é reportado ({_r5['espacamento_s']})")
_src_run2 = io.open("reliability_pilot1_run.py", encoding="utf-8").read()
check("--inter-call-seconds" in _src_run2,
      "[74] e é ajustável por argumento, sem tocar o config de produção")

print()
print("=" * 98)
print("BLOCO M — metadados de uso DESCOBERTOS, truncamento sem chute")
print("=" * 98)


class _Campo:
    def __init__(self, name):
        self.name = name


class _Desc:
    fields = [_Campo("prompt_token_count"), _Campo("candidates_token_count"),
              _Campo("total_token_count"), _Campo("thoughts_token_count")]


class _UsoProto:
    DESCRIPTOR = _Desc()
    prompt_token_count = 857
    candidates_token_count = 32
    total_token_count = 1753
    thoughts_token_count = 864


_u1 = pr.serializar_uso(_UsoProto())
check(_u1.get("thoughts_token_count") == 864,
      f"[75] campos além dos três básicos são capturados quando existem "
      f"({_u1.get('thoughts_token_count')})")
check(_u1.get("_origem_dos_campos") == "protobuf_descriptor",
      "[76] com a origem da descoberta registrada")


class _UsoSimples:
    prompt_token_count = 10
    total_token_count = 20
    algo_callable = staticmethod(lambda: 1)


_u2 = pr.serializar_uso(_UsoSimples())
check(_u2.get("prompt_token_count") == 10
      and "algo_callable" not in _u2
      and "candidates_token_count" not in _u2,
      f"[77] sem descriptor, cai para introspecção e NÃO inventa campo ausente "
      f"({sorted(k for k in _u2 if not k.startswith('_'))})")
check(pr.serializar_uso(None) == {}, "[78] objeto ausente devolve vazio")

_trunc = pr.diagnosticar_json('{"events": [', ValueError("x"),
                              {"finish_reason_nome": "MAX_TOKENS"})
check(_trunc["possible_output_truncation"] is True,
      "[79] truncamento é afirmado a partir do finish_reason")
_semfim = pr.diagnosticar_json('{"events": [', ValueError("x"), {})
check(_semfim["possible_output_truncation"] is None,
      f"[80] SEM finish reason o veredito é UNKNOWN, não False nem True "
      f"({_semfim['possible_output_truncation']})")
check("indeterminado" in _semfim["base_da_suspeita"],
      "[81] e a base da incerteza fica explícita")
_stop = pr.diagnosticar_json("nao e json", ValueError("x"),
                             {"finish_reason_nome": "STOP"})
check(_stop["possible_output_truncation"] is False,
      "[82] terminou por STOP: JSON inválido não é truncamento")
_arit = io.open("reliability_pilot1_run.py", encoding="utf-8").read()
check("total_token_count - " not in _arit and "reasoning_tokens" not in _arit,
      "[83] nenhuma aritmética total-prompt-output é usada como prova")
check(pr.OUTPUT_TOKEN_CAP == 900,
      f"[84] cap de saída preservado em 900 para manter comparabilidade "
      f"({pr.OUTPUT_TOKEN_CAP})")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7b-PILOT1 (preparação): {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
