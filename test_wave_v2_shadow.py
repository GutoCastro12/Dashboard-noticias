#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_v2_shadow.py — shadow prospectivo: holdout real, zero autoridade.

O QUE ESTE TESTE PROTEGE

1. Que só entre o que é PROSPECTIVO. Artigo anterior ao freeze do Contract V2,
   ou que participou do desenvolvimento, não é holdout — e incluí-lo
   transformaria a validação numa reconfirmação do que já sabíamos.

2. Que a PRIMEIRA observação seja imutável. Regravar depois de um ajuste de
   prompt destruiria a medição sem deixar rastro.

3. Que a chave carregue a versão do contrato, para que um resultado V2 nunca
   seja sobrescrito por um V3.

4. Que o determinístico seja fotografado NO MOMENTO da observação. Recalcular
   meses depois compara o modelo de hoje com um determinístico que mudou.

5. Que falha de provider, cota ou sidecar corrompido NÃO derrube o cron.

6. Que a camada não tenha autoridade alguma sobre score.

NENHUMA CHAMADA A PROVIDER.
"""
from __future__ import annotations

import calendar
import datetime
import importlib
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="v2shadow_"))
os.environ["RISK_SEMANTIC_V2_SHADOW"] = str(TMP / "shadow.json")
os.environ["RISK_SHADOW_PACING_S"] = "0"

import semantic_v2_shadow as sh
import semantic_v2_shadow_run as run
import reliability_pilot_contract_v2 as v2

importlib.reload(sh)

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


DEPOIS = sh.CONTRACT_FREEZE_TS + 3600
ANTES = sh.CONTRACT_FREEZE_TS - 3600


def A(url="http://x/1", ts=None, empresa="Acme", ev="ma", titulo="Alfa compra Beta",
      **kw):
    a = {"url": url, "title": titulo, "summary": titulo,
         "captured_ts": DEPOIS if ts is None else ts,
         "companies": [empresa], "events_by_company": {empresa: [ev]},
         "companies_attributed": [empresa], "context_companies": [],
         "mention_roles": {empresa: {"relation_type": "direto",
                                     "subject_company": "", "impact_type": "direto",
                                     "event_phase": "anuncio"}},
         "cap_iso": "2026-08-15 10:00", "pub_iso": "2026-08-15 09:00",
         "source": "Fonte"}
    a.update(kw)
    return a


print("=" * 98)
print("BLOCO A0 — ROUND-TRIP DO FREEZE: o bug que a suíte anterior não viu")
print("=" * 98)
# A checagem antiga afirmava o ISO contra ele mesmo, e todos os casos
# sintéticos usavam `CONTRACT_FREEZE_TS ± 3600` — relativos à constante. Com
# isso a suíte ficou 57/57 enquanto o epoch estava 86400 s à frente do ISO e o
# holdout era impossível de alimentar. Aqui o epoch é derivado DE FORMA
# INDEPENDENTE e comparado; se alguém mudar o ISO e esquecer o resto, fica
# vermelho.
_ESPERADO_TS = calendar.timegm(
    time.strptime("2026-08-14T12:06:33Z", "%Y-%m-%dT%H:%M:%SZ"))
check(sh.CONTRACT_FREEZE_TS == _ESPERADO_TS == 1786709193,
      f"[0a] epoch do freeze = {sh.CONTRACT_FREEZE_TS} "
      f"(derivado independentemente: {_ESPERADO_TS})")
check(time.strftime("%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(sh.CONTRACT_FREEZE_TS))
      == sh.CONTRACT_FREEZE_ISO,
      "[0b] round-trip: epoch → UTC devolve exatamente o ISO canônico")
check(sh.CONTRACT_FREEZE_TS < time.time(),
      "[0c] e o freeze está no PASSADO — um freeze futuro torna o holdout "
      "impossível de alimentar, que foi o defeito observado")
# fuso da máquina não pode interferir: o mesmo ISO tem que dar o mesmo epoch
_iso_dt = datetime.datetime(2026, 8, 14, 12, 6, 33,
                            tzinfo=datetime.timezone.utc)
check(int(_iso_dt.timestamp()) == sh.CONTRACT_FREEZE_TS,
      "[0d] conversão timezone-safe: bate com datetime aware em UTC")
check(sh._epoch_utc("2026-01-01T00:00:00Z") == 1767225600,
      "[0e] a função de conversão é correta para outra data conhecida")
try:
    sh._epoch_utc("2026-08-14T12:06:33")
    _rej = False
except ValueError:
    _rej = True
check(_rej, "[0f] instante sem sufixo Z é REJEITADO — sem UTC explícito não há "
            "como saber o instante")
_src_sh = io.open("semantic_v2_shadow.py", encoding="utf-8").read()
_cod_sh = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
                 "\n".join(l.split("#")[0] for l in _src_sh.splitlines()))
check("CONTRACT_FREEZE_TS = _epoch_utc(" in _cod_sh,
      "[0g] o epoch é DERIVADO do ISO — uma fonte de verdade, não dois "
      "literais que podem divergir")
check(not re.search(r"CONTRACT_FREEZE_TS\s*=\s*\d", _cod_sh),
      "[0h] e não sobrou nenhum epoch escrito à mão")
check("time.mktime" not in _cod_sh,
      "[0i] não usa time.mktime, que aplicaria o fuso local do runner")

print()
print("=" * 98)
print("BLOCO A — FREEZE E ELEGIBILIDADE PROSPECTIVA")
print("=" * 98)
check(sh.CONTRACT_FREEZE_COMMIT.startswith("7526667"),
      f"[1] o freeze aponta para o commit do Contract V2 "
      f"({sh.CONTRACT_FREEZE_COMMIT[:7]})")
check(sh.CONTRACT_FREEZE_ISO == "2026-08-14T12:06:33Z",
      f"[2] com timestamp declarado ({sh.CONTRACT_FREEZE_ISO})")
_dev = sh.corpus_de_desenvolvimento()
check(len(_dev) == 25,
      f"[3] o corpus de desenvolvimento tem as 25 URLs do manifesto ({len(_dev)})")

_ok, _m = sh.elegivel(A(), _dev)
check(_ok, "[4] artigo capturado DEPOIS do freeze é prospectivo")
_ok2, _m2 = sh.elegivel(A(ts=ANTES), _dev)
check(not _ok2 and "anterior ao freeze" in _m2,
      f"[5] artigo anterior ao freeze é REJEITADO ({_m2})")
_url_dev = sorted(_dev)[0]
_ok3, _m3 = sh.elegivel(A(url=_url_dev), _dev)
check(not _ok3 and "desenvolvimento" in _m3,
      "[6] artigo do corpus de desenvolvimento é REJEITADO mesmo se recente")
_ok4, _m4 = sh.elegivel(A(ts=0), _dev)
check(not _ok4, "[7] sem timestamp não é aceito — pós-freeze precisa ser provável")
_ok5, _ = sh.elegivel(A(ts=sh.CONTRACT_FREEZE_TS), _dev)
check(not _ok5, "[8] exatamente no instante do freeze não conta como posterior")

print()
print("=" * 98)
print("BLOCO B — SELEÇÃO: teto, prioridade e determinismo")
print("=" * 98)
_muitos = [A(url=f"http://x/{i}", titulo=f"Empresa {i} anuncia aquisicao")
           for i in range(20)]
_sel = sh.selecionar(_muitos, {}, set())
check(len(_sel) == sh.MAX_CASOS_POR_RUN,
      f"[9] o teto por execução é respeitado ({len(_sel)}/{sh.MAX_CASOS_POR_RUN})")
check(sh.MAX_CHAMADAS_POR_RUN == sh.MAX_CASOS_POR_RUN * 2,
      f"[10] com dois modelos, o teto de chamadas é {sh.MAX_CHAMADAS_POR_RUN}")
_sel2 = sh.selecionar(_muitos, {}, set())
check([c["artigo_id"] for c in _sel] == [c["artigo_id"] for c in _sel2],
      "[11] a seleção é determinística — mesmo corpus, mesma amostra")
_ja = {(c["artigo_id"], c["empresa"], c["event_id"]) for c in _sel}
_sel3 = sh.selecionar(_muitos, {}, _ja)
check(not set(c["artigo_id"] for c in _sel3) & set(c["artigo_id"] for c in _sel),
      "[12] casos já observados não voltam à fila")
_p_follow = sh.prioridade(A(titulo="Conselho aprova conclusão da fusão após recurso"), "ma")
_p_seco = sh.prioridade(A(titulo="Empresa divulga balanço"), "ma")
check(_p_follow > _p_seco,
      f"[13] linguagem de follow-up prioriza o caso ({_p_follow} > {_p_seco})")
check(sh.prioridade(A(), "falencia") > sh.prioridade(A(), "evento_qualquer"),
      "[14] famílias de atribuição difícil entram antes")
_src_sel = io.open("semantic_v2_shadow.py", encoding="utf-8").read()
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _src_sel.splitlines()))
check("human_truth" not in _cod and "human_scoreable" not in _cod,
      "[15] a seleção NÃO consulta verdade humana")

print()
print("=" * 98)
print("BLOCO C — CHAVE VERSIONADA E IMUTABILIDADE")
print("=" * 98)
_k = sh.chave("aid", "Acme", "ma", "v2", "r7ba.p2", "gemini-3.1-flash-lite")
check("v2" in _k and "r7ba.p2" in _k and "gemini-3.1-flash-lite" in _k,
      "[16] a chave carrega contrato, prompt e modelo")
_k3 = sh.chave("aid", "Acme", "ma", "v3", "r7ba.p3", "gemini-3.1-flash-lite")
check(_k != _k3,
      "[17] um resultado V3 NÃO colide com o V2 — a observação antiga sobrevive")
_km = sh.chave("aid", "Acme", "ma", "v2", "r7ba.p2", "gemini-3.5-flash-lite")
check(_k != _km, "[18] e cada modelo tem chave própria")

_base = {"_meta": {}, "observacoes": {_k: {"estado": "OK", "v": "PRIMEIRA"}}}
_novo = {"_meta": {}, "observacoes": {_k: {"estado": "OK", "v": "SEGUNDA"},
                                      _km: {"estado": "OK", "v": "outra"}}}
_f = sh.fundir(_base, _novo)
check(_f["observacoes"][_k]["v"] == "PRIMEIRA",
      "[19] no merge, a PRIMEIRA observação vence — o oposto do cache, e de "
      "propósito")
check(_km in _f["observacoes"],
      "[20] e a observação do outro modelo não é perdida")

print()
print("=" * 98)
print("BLOCO D — SIDECAR: atômico, fail-open, sem churn")
print("=" * 98)
_p = TMP / "s1.json"
_d = {"_meta": {"shadow_version": sh.SHADOW_VERSION},
      "observacoes": {_k: {"estado": "OK"}}}
check(sh.gravar(_d, _p) is True, "[21] a primeira gravação escreve")
_bytes = _p.read_bytes()
check(sh.gravar(_d, _p) is False,
      "[22] gravar o MESMO conteúdo não reescreve — cron sem caso novo não "
      "gera commit")
check(_p.read_bytes() == _bytes, "[23] e o arquivo fica byte-idêntico")
_d["observacoes"][_km] = {"estado": "OK"}
check(sh.gravar(_d, _p) is True, "[24] mas uma observação nova de fato grava")
_p2 = TMP / "corrompido.json"
io.open(_p2, "w", encoding="utf-8").write("{ isto nao e json")
_rec = sh.carregar(_p2)
check(_rec["observacoes"] == {} and _rec["_meta"].get("recuperado_de_corrupcao"),
      "[25] sidecar corrompido vira vazio recuperável, não exceção")
check(sh.carregar(TMP / "inexistente.json")["observacoes"] == {},
      "[26] sidecar ausente também é fail-open")

print()
print("=" * 98)
print("BLOCO E — SNAPSHOT DETERMINÍSTICO CONTEMPORÂNEO")
print("=" * 98)
_art = A()
_snap = sh.snapshot_deterministico(_art, "Acme", "ma")
check(_snap["scoreable"] is True and _snap["atribuida"] is True,
      "[27] o veredito determinístico do momento é fotografado")
check(_snap["relation_type"] == "direto" and "event_phase" in _snap,
      "[28] com papel e fase, não só o booleano")
check(_snap["capturado_em"] == "2026-08-15 10:00",
      "[29] e com o instante da captura, para provar contemporaneidade")
_art2 = dict(_art, events_by_company={"Acme": []})
check(sh.snapshot_deterministico(_art2, "Acme", "ma")["scoreable"] is False,
      "[30] um caso não pontuável é fotografado como tal")

print()
print("=" * 98)
print("BLOCO F — DOIS MODELOS, MESMO CASO, SEM CONTAMINAÇÃO")
print("=" * 98)


class _Prov:
    """Provedor falso: um roteiro por MODELO, nunca compartilhado."""

    class types:
        @staticmethod
        def GenerationConfig(**kw):
            return dict(kw)

    def __init__(self, por_modelo):
        self.por_modelo = por_modelo
        self.invocacoes = {m: 0 for m in por_modelo}

    def GenerativeModel(self, modelo):
        prov, alvo = self, modelo

        class _M:
            def generate_content(_s, prompt, **kw):
                prov.invocacoes[alvo] += 1
                passo = prov.por_modelo[alvo]
                if isinstance(passo, Exception):
                    raise passo
                return type("R", (), {
                    "text": passo,
                    "usage_metadata": type("U", (), {
                        "prompt_token_count": 100,
                        "candidates_token_count": 50,
                        "total_token_count": 150})(),
                    "candidates": [type("C", (), {
                        "finish_reason": type("F", (), {"name": "STOP"})()})()],
                    "model_version": alvo})()
        return _M()


_RESP = json.dumps({"events": [{
    "event_id": "ma", "event_asserted": "ASSERTED", "subject": "Acme",
    "company_role": "BUYER", "currentness": "CURRENT", "phase": "CONCLUDED",
    "centrality": "MAIN", "field_support": "SUPPORTED",
    "occurrence_novelty": "NEW_OCCURRENCE",
    "transaction_object": "COMPANY_CONTROL"}]})

os.environ["RISK_SEMANTIC_V2_SHADOW"] = str(TMP / "run1.json")
importlib.reload(sh)
importlib.reload(run)
_hist = {"articles": {"http://x/9": A(url="http://x/9")}}
_cfg = run.rd.load_config("config_risco.yaml")
_prov = _Prov({sh.MODELOS[0]: _RESP, sh.MODELOS[1]: _RESP})
_tel = run.executar(_hist, _cfg, genai=_prov, limite=5)
check(_tel["selecionados"] == 1, "[31] um caso prospectivo selecionado")
check(_prov.invocacoes[sh.MODELOS[0]] == 1
      and _prov.invocacoes[sh.MODELOS[1]] == 1,
      "[32] os DOIS modelos receberam o MESMO caso, uma vez cada")
check(_tel["novos_registros"] == 2,
      f"[33] duas observações persistidas ({_tel['novos_registros']})")
check(_tel["concordancias"] == 1 and _tel["divergencias"] == 0,
      "[34] concordância entre modelos é contabilizada")
_dados = sh.carregar()
_um = list(_dados["observacoes"].values())[0]
for _c in ("case_id", "article_id", "url", "company", "candidate_event",
           "contract_version", "prompt_version", "requested_model",
           "actual_model", "saida", "evidencia", "deterministic", "usage",
           "latencia_s", "finish", "created_at", "human_review"):
    pass
check(all(_c in _um for _c in ("case_id", "article_id", "url", "company",
                               "candidate_event", "contract_version",
                               "prompt_version", "requested_model",
                               "actual_model", "saida", "evidencia",
                               "deterministic", "usage", "latencia_s",
                               "finish", "created_at", "human_review")),
      "[35] o registro carrega identidade, versões, saída, evidência, "
      "determinístico, uso e criação")
check(_um["human_review"] is None,
      "[36] `human_review` nasce VAZIO — nada é auto-adjudicado")

# imutabilidade em nova execução
_prov2 = _Prov({sh.MODELOS[0]: _RESP, sh.MODELOS[1]: _RESP})
_tel2 = run.executar(_hist, _cfg, genai=_prov2, limite=5)
check(_tel2["selecionados"] == 0 and _tel2["novos_registros"] == 0,
      "[37] o mesmo caso NÃO é reobservado na execução seguinte")
check(_prov2.invocacoes[sh.MODELOS[0]] == 0,
      "[38] e nenhuma chamada é feita por ele — a primeira observação é a válida")
check(_tel2["sidecar_alterado"] is False,
      "[39] sem caso novo, o sidecar não é reescrito")

print()
print("=" * 98)
print("BLOCO G — UM MODELO CAI, O OUTRO CONTINUA")
print("=" * 98)
os.environ["RISK_SEMANTIC_V2_SHADOW"] = str(TMP / "run2.json")
importlib.reload(sh)
importlib.reload(run)
_cota = type("ResourceExhausted", (Exception,), {})("429 requests per day")
_hist2 = {"articles": {f"http://y/{i}": A(url=f"http://y/{i}")
                       for i in range(3)}}
_prov3 = _Prov({sh.MODELOS[0]: _cota, sh.MODELOS[1]: _RESP})
_tel3 = run.executar(_hist2, _cfg, genai=_prov3, limite=3)
check(_prov3.invocacoes[sh.MODELOS[0]] == 1,
      f"[40] G1 com cota: UMA tentativa e para "
      f"({_prov3.invocacoes[sh.MODELOS[0]]})")
check(_prov3.invocacoes[sh.MODELOS[1]] == 3,
      f"[41] G2 continua os 3 casos — cota é por projeto+modelo "
      f"({_prov3.invocacoes[sh.MODELOS[1]]})")
check(_tel3["por_modelo"][sh.MODELOS[0]]["parada_por_cota"] == "QUOTA_EXHAUSTED",
      "[42] a telemetria registra por qual classe o modelo parou")
check(_tel3["novos_registros"] >= 3,
      "[43] as observações de G2 são preservadas mesmo com G1 fora")
_obs3 = sh.carregar()["observacoes"]
_ausentes = [o for o in _obs3.values()
             if o["requested_model"] == sh.MODELOS[0] and o["estado"] != "OK"]
check(_ausentes, "[44] e a AUSÊNCIA de G1 é registrada, não substituída por G2")

print()
print("=" * 98)
print("BLOCO H — FAIL-OPEN E ZERO AUTORIDADE SOBRE SCORE")
print("=" * 98)
_tel4 = run.executar({"articles": {"http://z/1": A(url="http://z/1")}}, _cfg,
                     genai=None)
check(isinstance(_tel4, dict),
      "[45] sem chave e sem SDK, a função retorna normalmente (fail-open)")


class _Explode:
    class types:
        @staticmethod
        def GenerationConfig(**kw):
            raise RuntimeError("boom")

    def GenerativeModel(self, m):
        raise RuntimeError("boom")


_tel5 = run.executar({"articles": {"http://z/2": A(url="http://z/2")}}, _cfg,
                     genai=_Explode())
check(isinstance(_tel5, dict),
      "[46] provedor que explode não levanta para o cron")
_src_run = io.open("semantic_v2_shadow_run.py", encoding="utf-8").read()
_cod_run = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
                  "\n".join(l.split("#")[0] for l in _src_run.splitlines()))
for _n, _proib in enumerate(("build_evolution", "total_score", "status ="),
                            start=47):
    check(_proib not in _cod_run,
          f"[{_n}] o executor não toca `{_proib.strip()}`")
# Ler o history é legítimo — é a fonte dos artigos. O que não pode é ESCREVER.
_escritas = re.findall(r'open\(\s*([^,)]*)\s*,\s*["\']w', _cod_run)
check(all("shadow" in e.lower() or "out_reliability" in e.lower()
          or "args." not in e for e in _escritas)
      and not re.search(r'json\.dump\([^)]*risk_history', _cod_run),
      f"[50] o executor só escreve no sidecar e na telemetria ({_escritas})")
check(not re.search(r"hist(orico)?\s*\[[^\]]+\]\s*=", _cod_run)
      and 'events_by_company"] =' not in _cod_run,
      "[51] e nunca atribui dentro do history carregado")

print()
print("=" * 98)
print("BLOCO I — FILA DE REVISÃO")
print("=" * 98)
_fila = run.fila_de_revisao(sh.carregar())
check(isinstance(_fila, list) and len(_fila) <= 20,
      f"[52] a fila é curta ({len(_fila)} caso(s)) — não despeja centenas")
if _fila:
    check(all(("company" in f and "title" in f and "motivo" in f)
              for f in _fila),
          "[53] cada item traz empresa, manchete e o motivo do review")
    check(_fila == sorted(_fila, key=lambda x: (-x["prioridade"],
                                                x["article_id"])),
          "[54] ordenada por prioridade, divergência entre modelos primeiro")
else:
    check(True, "[53] fila vazia é resultado válido quando nada diverge")
    check(True, "[54] (sem itens para ordenar)")

print()
print("=" * 98)
print("BLOCO I2 — REGRESSÃO EXATA: o primeiro caso prospectivo real")
print("=" * 98)
# Metadados REAIS do artigo que o cron 31808760930 trouxe e que o freeze
# quebrado tornou inelegível. Fixados aqui como regressão: se a elegibilidade
# temporal regredir de novo, este caso denuncia.
_ENEVA_TS = 1786717513          # 2026-08-14T14:25:13Z
_ENEVA_URL = ("https://brasilenergia.com.br/petroleoegas/ep/"
              "anp-aprova-aquisicao-de-parte-da-atem-em-japiim-pela-eneva")
_ENEVA = {"url": _ENEVA_URL,
          "title": "ANP aprova aquisição de parte da Atem em Japiim pela Eneva",
          "summary": "", "captured_ts": _ENEVA_TS, "companies": ["Eneva"],
          "events_by_company": {"Eneva": ["ma"]},
          "companies_attributed": ["Eneva"], "context_companies": [],
          "mention_roles": {"Eneva": {"relation_type": "direto",
                                      "subject_company": "",
                                      "impact_type": "direto",
                                      "event_phase": "aprovacao"}},
          "cap_iso": "2026-08-14 11:25", "pub_iso": "2026-08-11 12:57",
          "source": "Editora Brasil Energia"}
_dev_real = sh.corpus_de_desenvolvimento()
check(_ENEVA_TS > sh.CONTRACT_FREEZE_TS,
      f"[58] capturado {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(_ENEVA_TS))} "
      f"— depois do freeze {sh.CONTRACT_FREEZE_ISO}")
check(_ENEVA_URL not in _dev_real,
      "[59] fora do corpus de desenvolvimento do V2")
check(len(_dev_real) == 25 and _ENEVA_URL not in _dev_real,
      "[60] e não é nenhuma das 25 URLs do manifesto congelado")
_ok_en, _m_en = sh.elegivel(_ENEVA, _dev_real)
check(_ok_en and _m_en == "prospectivo",
      f"[61] ELEGÍVEL como prospectivo ({_m_en})")
_snap_en = sh.snapshot_deterministico(_ENEVA, "Eneva", "ma")
check(_snap_en["scoreable"] is True and _snap_en["event_phase"] == "aprovacao",
      "[62] o snapshot determinístico contemporâneo é capturado "
      "(pontuável, fase=aprovacao)")
check(sh.prioridade(_ENEVA, "ma") > 0,
      f"[63] entra na fila com prioridade {sh.prioridade(_ENEVA, 'ma')}")
# O epoch QUEBRADO teria rejeitado este caso. Provado executando a mesma
# função com a constante trocada, e restaurando em seguida.
_EPOCH_QUEBRADO = 1786795593
_orig_ts = sh.CONTRACT_FREEZE_TS
try:
    sh.CONTRACT_FREEZE_TS = _EPOCH_QUEBRADO
    _ok_bug, _m_bug = sh.elegivel(_ENEVA, _dev_real)
finally:
    sh.CONTRACT_FREEZE_TS = _orig_ts
check(not _ok_bug and "anterior ao freeze" in _m_bug,
      f"[64] com o epoch antigo ({_EPOCH_QUEBRADO}) este caso REAL era "
      f"rejeitado ({_m_bug})")
check(sh.elegivel(_ENEVA, _dev_real)[0] is True,
      "[65] e com o epoch derivado ele é aceito — a diferença entre ter e não "
      "ter holdout")

print()
print("=" * 98)
print("BLOCO J — CONTRATO V2 CONGELADO NESTA WAVE")
print("=" * 98)
check(v2.PROMPT_VERSION == "r7ba.p2" and v2.SCHEMA_VERSION == "r7ba.s2",
      "[55] o Contract V2 não foi versionado de novo — segue congelado")
check(set(v2.OCCURRENCE_NOVELTY) == {"NEW_OCCURRENCE", "FOLLOW_UP",
                                     "HISTORICAL_CONTEXT",
                                     "DESCRIPTOR_OR_BACKGROUND",
                                     "UNDETERMINED"},
      "[56] os enums de novidade não mudaram")
check(len(v2.TRANSACTION_OBJECT) == 9,
      "[57] nem os de objeto da transação")

print()
print("=" * 98)
print(f"RESULTADO SHADOW PROSPECTIVO V2: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
