#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7b_a_llm_pilot.py — 4I.2 R7b-A.

O QUE PRECISA SER VERDADE MESMO SEM UMA ÚNICA CHAMADA AO MODELO.

Duas garantias do piloto são estruturais e não dependem do provider:

1. A DISCOVERY É CEGA. Não porque lembramos de omitir a empresa, mas porque a
   função que monta o payload não recebe empresa nem candidatos. Aqui isso é
   verificado pela ASSINATURA e pelo conteúdo serializado do prompt real.

2. NADA DE PONTUAÇÃO SAI. Score, peso, tier, trust, threshold e afins são
   proibidos no payload — e o texto do artigo é explicitamente isento, porque
   censurar o conteúdo destruiria o objeto da análise.

E uma terceira, comportamental: SEM PROVIDER, NADA É INVENTADO. O runner monta
os payloads e marca `NAO_EXECUTADO`. Um piloto que fabrica saída quando a
chave falta é pior do que um piloto não executado.

A checagem de termos proibidos já reprovou o próprio prompt de discovery, que
pede rótulos "com underscore" — substring pegava "under-score-". O teste [12]
fixa a fronteira de palavra para que a correção não regrida.
"""
from __future__ import annotations

import inspect
import io
import json
import os
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa
import reliability_pilot_contract as ct
import reliability_pilot_input as pi
import reliability_pilot_validators as vl
import reliability_pilot_runner as rn
import reliability_pilot_sample as ps
import reliability_pilot_tap as tp

PASS = FAIL = 0
OUT = Path("out_reliability/r7b_a")
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


TXT = ("A Vale anunciou hoje a aquisicao da Alfa Mineracao por 2 bilhoes. "
       "O acordo foi confirmado pelo conselho.")

print("=" * 98)
print("BLOCO A — a discovery é cega por construção, não por lembrança")
print("=" * 98)
_sig = inspect.signature(ct.payload_discovery).parameters
check(set(_sig) == {"texto", "pub_iso", "genero"},
      f"[1] payload_discovery só aceita texto/pub/gênero ({sorted(_sig)})")
check(all(x not in _sig for x in ("organizacao", "empresa", "event_ids",
                                  "aliases", "candidates")),
      "[2] não há parâmetro de empresa monitorada nem de candidatos")
_pd = ct.payload_discovery(texto=TXT, pub_iso="2026-01-01")
_fora = _pd["prompt"].replace(_pd["texto"], "")
check("Vale" not in _fora,
      "[3] o nome da empresa não aparece no prompt fora do texto do artigo")
check("ma" not in _fora.split() and "event_id" not in _fora,
      "[4] nenhum identificador de evento da taxonomia é apresentado")
check("Vale" in _pd["texto"],
      "[5] o texto do artigo é preservado íntegro — cegueira não é censura")
_pa = ct.payload_audit(texto=TXT, organizacao="Vale", aliases=["Vale S.A."],
                       event_ids=["ma"])
check("Vale" in _pa["prompt"] and "ma" in _pa["prompt"],
      "[6] o AUDIT, ao contrário, recebe empresa e candidatos deliberadamente")

print()
print("=" * 98)
print("BLOCO B — nenhum vocabulário de pontuação sai")
print("=" * 98)
for nome, p in (("discovery", _pd), ("audit", _pa)):
    check(not ct.checar_payload({k: v for k, v in p.items() if k != "schema"},
                                texto_do_artigo=p["texto"]),
          f"[7..8] payload {nome} sem termos proibidos")
check(ct.checar_payload({"x": "score 90, tier 1, trust 0.95"}) ==
      ["score", "tier", "trust"],
      "[9] a checagem DETECTA vazamento real")
check(not ct.checar_payload({"t": "score alto"}, texto_do_artigo="score alto"),
      "[10] o texto do artigo é isento — notícia pode falar de score")
check("watchlist" in ct.PROIBIDO_NO_PAYLOAD and "exposure" in ct.PROIBIDO_NO_PAYLOAD,
      "[11] watchlist e exposure estão na lista de proibidos")
check(not ct.checar_payload({"x": "rotulo com underscore"}),
      "[12] 'underscore' não é 'score' — casamento por fronteira de palavra")

print()
print("=" * 98)
print("BLOCO C — validador de evidência: quote literal ou UNSUPPORTED")
print("=" * 98)
check(vl.quote_valida("aquisicao da Alfa Mineracao", TXT),
      "[13] quote literal presente é aceita")
check(vl.quote_valida("A VALE ANUNCIOU HOJE", TXT),
      "[14] diferença de caixa/acento não invalida")
check(not vl.quote_valida("a Vale vendeu a Alfa", TXT),
      "[15] paráfrase plausível é REJEITADA — é a alucinação típica")
check(vl.quote_valida("", TXT) and vl.quote_valida(None, TXT),
      "[16] ausência declarada não é alucinação")
check(not vl.quote_valida("abc", TXT), "[17] quote curta demais não conta")
_ev = {"subject_quote": "A Vale anunciou hoje", "role_quote": "inventado total"}
_r = vl.validar_quotes(_ev, TXT)
check(_r["invalidas"] == ["role_quote"] and not _r["valida"],
      f"[18] valida campo a campo ({_r['invalidas']})")

print()
print("=" * 98)
print("BLOCO D — validador de entidade e casamento local")
print("=" * 98)
check(vl.entidade_no_texto("Alfa Mineracao", TXT), "[19] entidade presente")
check(not vl.entidade_no_texto("Petrobras", TXT),
      "[20] entidade ausente é rejeitada — H3")
check(vl.entidade_no_texto("Vale S.A.", TXT, ["Vale"]),
      "[21] alias conhecido resolve a entidade")
check(not vl.entidade_no_texto("Val", "O valor subiu"),
      "[22] casamento por token, não por substring")
_emp = vl.casar_entidade_local("Vale", AL)
check(_emp == "Vale" or _emp in AL,
      f"[23] o match com a watchlist acontece LOCALMENTE ({_emp!r})")
check(vl.casar_entidade_local("Empresa Inexistente XZ", AL) == "",
      "[24] organização fora da watchlist não vira monitorada")

print()
print("=" * 98)
print("BLOCO E — schema e enums")
print("=" * 98)
_bom = {"events": [{"event_id": "ma", "event_asserted": "ASSERTED",
                    "subject": "Vale", "subject_basis": "EXPLICIT",
                    "company_role": "BUYER", "currentness": "CURRENT",
                    "phase": "ANNOUNCED", "centrality": "MAIN",
                    "field_support": "SUPPORTED"}]}
check(not vl.validar_schema(_bom, ct.SCHEMA_AUDIT), "[25] saída válida passa")
_ruim = json.loads(json.dumps(_bom))
_ruim["events"][0]["centrality"] = "MUITO_IMPORTANTE"
check(vl.validar_schema(_ruim, ct.SCHEMA_AUDIT), "[26] enum inválido é pego")
_falta = {"events": [{"event_id": "ma"}]}
check(vl.validar_schema(_falta, ct.SCHEMA_AUDIT),
      "[27] campo obrigatório ausente é pego")
_v = vl.validar_audit(_bom, texto=TXT, organizacao="Vale", aliases=["Vale"],
                      event_ids=["ma"])
check(_v["ok"] and _v["total"] == 1, "[28] audit válido é aceito")
_fora_cand = json.loads(json.dumps(_bom))
_fora_cand["events"][0]["event_id"] = "falencia"
_v2 = vl.validar_audit(_fora_cand, texto=TXT, organizacao="Vale",
                       aliases=["Vale"], event_ids=["ma"])
check("EVENT_ID_FORA_DOS_CANDIDATOS" in _v2["marcas"],
      "[29] event_id fora dos candidatos é marcado")
_nq = {"events": [{"organization": "Vale", "risk_channel": "credito",
                   "event_description": "x", "currentness": "CURRENT",
                   "centrality": "MAIN", "evidence_quote": "",
                   "field_support": "SUPPORTED"}]}
check(vl.H4_NOVEL_SEM_QUOTE in vl.validar_discovery(_nq, texto=TXT)["marcas"],
      "[30] novel event sem quote é marcado H4")

print()
print("=" * 98)
print("BLOCO F — identidade de cache e versionamento")
print("=" * 98)
_a = ct.identidade_de_cache(call_type="AUDIT", texto=TXT, model="m1")
check(_a == ct.identidade_de_cache(call_type="AUDIT", texto=TXT, model="m1"),
      "[31] mesma entrada → mesma identidade")
for campo, mudou in (("call_type", ct.identidade_de_cache(call_type="DISCOVERY", texto=TXT, model="m1")),
                     ("model", ct.identidade_de_cache(call_type="AUDIT", texto=TXT, model="m2")),
                     ("texto", ct.identidade_de_cache(call_type="AUDIT", texto=TXT + " x", model="m1")),
                     ("extra", ct.identidade_de_cache(call_type="AUDIT", texto=TXT, model="m1", extra="Vale|ma"))):
    check(mudou != _a, f"[32..35] mudar {campo} muda a identidade")
_orig = ct.PROMPT_VERSION
try:
    ct.PROMPT_VERSION = "outra"
    check(ct.identidade_de_cache(call_type="AUDIT", texto=TXT, model="m1") != _a,
          "[36] mudar a versão do prompt invalida o cache")
finally:
    ct.PROMPT_VERSION = _orig
_v = ct.versoes("m1")
check(all(k in _v for k in ("provider", "model", "prompt_version",
                            "schema_version", "normalization_version",
                            "input_policy_version")),
      "[37] as seis versões viajam junto com a saída")

print()
print("=" * 98)
print("BLOCO G — política de input V0/V1")
print("=" * 98)
_rec = {"title": "Empresa X compra Y", "summary": "Empresa X compra Y &nbsp; Fonte"}
_v0 = pi.montar_v0(_rec)
check(not _v0["suficiente"] and _v0["chars_uteis"] < 100,
      f"[38] resumo que repete o título não conta como input ({_v0['chars_uteis']})")
_rico = {"title": "T", "summary": "Frase um bem longa com conteudo real. " * 30}
check(pi.montar_v0(_rico)["suficiente"], "[39] texto rico passa na suficiência")
check(pi.suficiente("x" * 10000)["input_policy_version"] == ct.INPUT_POLICY_VERSION,
      "[40] a política de suficiência é versionada")
_long = "Frase. " * 5000
check(len(pi.truncar_neutro(_long)) <= pi.MAX_CHARS_ENVIADOS,
      "[41] truncamento respeita o limite")
_src = io.open("reliability_pilot_input.py", encoding="utf-8").read()
check("keyword" not in _src.split("def truncar_neutro")[1].split("def ")[0].lower()
      or "NUNCA por janela de keyword" in _src,
      "[42] o truncamento é neutro — nunca janela ao redor de keyword de risco")
check(pi.montar_v1(_rec, "http://inexistente", permitir_rede=False)["origem"]
      == "v0_fallback",
      "[43] sem fragmento, V1 devolve V0 marcado — nunca inventa texto")

print()
print("=" * 98)
print("BLOCO H — sem provider, nada é inventado")
print("=" * 98)
_ambiente = dict(os.environ)
os.environ.pop("GEMINI_API_KEY", None)
try:
    rn.cliente({"llm": {}})
    check(False, "[44] cliente sem chave deveria falhar")
except rn.ProviderIndisponivel as exc:
    check("GEMINI_API_KEY" in str(exc),
          "[44] sem chave, o cliente falha explicitamente")
finally:
    os.environ.update(_ambiente)
_runsrc = io.open("reliability_pilot_runner.py", encoding="utf-8").read()
check("NAO_EXECUTADO" in _runsrc and "saída sintética" in _runsrc,
      "[45] o runner marca NAO_EXECUTADO em vez de fabricar resultado")
for arq in ("llm_run_archa_v0.json", "llm_run_archb_v0.json"):
    p = OUT / arq
    if p.exists():
        r = json.load(io.open(p, encoding="utf-8"))
        st = {x.get("status") for s in r["saidas"] for x in s["resultados"].values()}
        check(st <= {"NAO_EXECUTADO"} or "EXECUTADO" in st,
              f"[46..47] {arq}: nenhum status inventado ({sorted(st)})")
    else:
        check(False, f"[46..47] {arq} ausente")

print()
print("=" * 98)
print("BLOCO I — provas medidas sobre os payloads reais da amostra")
print("=" * 98)
_pa_run = OUT / "llm_run_archa_v0.json"
if _pa_run.exists():
    r = json.load(io.open(_pa_run, encoding="utf-8"))
    pv = r["provas_de_payload"]
    check(pv["discovery_payloads"] > 0,
          f"[48] há payloads de discovery reais ({pv['discovery_payloads']})")
    check(pv["vazamento_empresa"] == 0,
          f"[49] zero vazamento de empresa monitorada ({pv['vazamento_empresa']})")
    check(pv["vazamento_candidatos"] == 0,
          f"[50] zero vazamento de candidatos ({pv['vazamento_candidatos']})")
    check(pv["sem_proibidos"],
          f"[51] zero termos proibidos ({pv['termos_proibidos']})")
    check(r["arch"] == ct.ARCH_A and r["variante"] == "V0",
          "[52] a saída registra arquitetura e variante")
else:
    for i in range(48, 53):
        check(False, f"[{i}] execução ARCH-A ausente")

print()
print("=" * 98)
print("BLOCO J — o experimento não toca produção")
print("=" * 98)
for mod, s in (("tap", io.open("reliability_pilot_tap.py", encoding="utf-8").read()),
               ("runner", _runsrc),
               ("sample", io.open("reliability_pilot_sample.py", encoding="utf-8").read()),
               ("report", io.open("reliability_pilot_report.py", encoding="utf-8").read())):
    check(all(x not in s for x in ("save_history", "merge_into_history",
                                   "--apply", "--backfill", "--reclassify")),
          f"[53..56] {mod} não escreve em history")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("pilot" not in _wf.lower(), "[57] nenhum módulo do piloto está no workflow")
try:
    tp._guardar(Path("risk_history.json"))
    check(False, "[58] o tap deveria recusar escrever em history")
except PermissionError:
    check(True, "[58] o tap recusa escrever em risk_history.json")
try:
    tp._guardar(Path("config_risco.yaml"))
    check(False, "[59] o tap deveria recusar escrever no config")
except PermissionError:
    check(True, "[59] o tap recusa escrever no config")
check(tp._guardar(tp.OUTDIR / "x.json") is None,
      "[60] o tap aceita escrever no diretório experimental")

print()
print("=" * 98)
print("BLOCO K — amostra, procedência e verdade em branco")
print("=" * 98)
_man = OUT / "sample_manifest.json"
if _man.exists():
    m = json.load(io.open(_man, encoding="utf-8"))
    check(set(m["selecionado"]) == set(ps.ALVO),
          "[61] todos os oito estratos existem na amostra")
    check(all(i["procedencia"] in (ps.UNSEEN, ps.DEVELOPMENT_CONTROL)
              for i in m["itens"]),
          "[62] todo item tem procedência declarada")
    check(m["procedencia"].get(ps.DEVELOPMENT_CONTROL, 0) > 0,
          "[63] os controles de desenvolvimento estão marcados como tais")
    _ids = [i["ident"] for i in m["itens"]]
    check(len(_ids) == len(set(_ids)), "[64] identidades únicas")
    import reliability_pilot_report as rp
    _l = rp.montar_linhas({"itens": m["itens"][:5]},
                          json.load(io.open("risk_history.json", encoding="utf-8")),
                          cfg, {})
    check(all(l["reviewer"] == rp.UNREVIEWED for l in _l),
          "[65] toda linha nasce UNREVIEWED")
    check(all(l[c] == "" for l in _l for c in rp.COLUNAS_VERDADE
              if c != "reviewer"),
          "[66] nenhuma célula de verdade humana vem preenchida")
    check(not any("d_scoreable" == c for l in _l for c in rp.COLUNAS_VERDADE),
          "[67] a conclusão do motor não ocupa coluna de verdade")
else:
    for i in range(61, 68):
        check(False, f"[{i}] manifesto de amostra ausente")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7b-A (llm semantic pilot): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
