#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7c_input_layer.py — 4I.2 R7c.

A CAMADA DE INPUT NÃO PODE MUDAR O QUE A PRODUÇÃO LÊ.

Ela existe para MEDIR e, no futuro, MELHORAR o texto. Enquanto estiver
desligada, `_parse_rss`, `build_feed`, classificação, atribuição e score
precisam se comportar exatamente como antes. Os testes abaixo fixam três
contratos:

1. UNIDADE = ARTIGO. Uma empresa atribuída sem nenhum candidato é um fato
   registrado, não ausência de registro — é o caso que hoje desaparece antes
   do history e sem o qual descoberta aberta não existe.

2. SEM CONTEXTO É MELHOR QUE CONTEXTO SUJO. Herdado de R5b, onde boilerplate
   de portal reintroduziu um falso positivo já corrigido. Fragmento que não
   passa no filtro de qualidade não vira "melhor esforço".

3. DESLIGADA POR PADRÃO E SEM ESCRITA EM PRODUÇÃO. O segundo parser de RSS é
   deliberado: mexer no de produção mudaria o texto que alimenta classificação
   e atribuição, e esta wave tem contrato de equivalência.

Zero chamadas de LLM nesta wave — verificado no código, não prometido.
"""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import risk_dashboard as rd
import reliability_input_layer as il
import reliability_input_capture as ic
import reliability_pilot_input as pi

PASS = FAIL = 0
OUT = Path("out_reliability/r7c")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


print("=" * 98)
print("BLOCO A — a unidade é o artigo, e empresa sem candidato é um fato")
print("=" * 98)
_ai = il.montar_article_input(
    url="http://x/1", titulo="Empresa A compra a B",
    resumo="Empresa A compra a B &nbsp;&nbsp; Fonte", dominio="news.com",
    empresas={"Empresa A": ["ma"], "Empresa B": [], "Empresa C": ["falencia"]})
check(_ai["n_empresas"] == 3, "[1] três empresas no mesmo artigo, um só texto")
check(_ai["n_empresas_sem_candidato"] == 1,
      "[2] a empresa sem candidato é contada, não omitida")
_b = [e for e in _ai["empresas"] if e["empresa"] == "Empresa B"][0]
check(_b["tem_candidato"] is False and _b["candidatos"] == [],
      "[3] lista vazia é informação declarada, não ausência de registro")
check(_ai["article_id"] == il.identidade("http://x/1"),
      "[4] identidade estável derivada da URL")
check(_ai["melhor_texto"] and _ai["texto_base"],
      "[5] o texto é do ARTIGO, não por empresa")
check([e["empresa"] for e in _ai["empresas"]] ==
      sorted(e["empresa"] for e in _ai["empresas"]),
      "[6] empresas em ordem determinística")

print()
print("=" * 98)
print("BLOCO B — qualidade mede o que o corpus realmente tem")
print("=" * 98)
_q = il.qualidade_do_texto("Empresa X compra Y",
                           "Empresa X compra Y &nbsp;&nbsp; InfoMoney")
check(_q["chars_uteis"] < 40,
      f"[7] resumo que repete o título quase não acrescenta ({_q['chars_uteis']})")
check(_q["ruido_rss"], "[8] o padrão &nbsp; do Google News é detectado")
check(_q["duplicacao_titulo"] > 0.4,
      f"[9] duplicação título↔resumo é medida ({_q['duplicacao_titulo']})")
check(not _q["suficiente"], "[10] e portanto o input é insuficiente")
_rico = il.qualidade_do_texto("T", "Uma frase longa com conteudo real. " * 40)
check(_rico["suficiente"] and _rico["frases"] >= 3,
      "[11] texto real passa na suficiência")
check(il.qualidade_do_texto("", "")["chars_uteis"] == 0,
      "[12] texto vazio não quebra o medidor")

print()
print("=" * 98)
print("BLOCO C — a escada e o contrato 'sem contexto é melhor que sujo'")
print("=" * 98)
_pobre = il.montar_article_input(url="http://x/2", titulo="T curto",
                                 resumo="T curto &nbsp; Fonte")
_sujo = {"articles": {"http://x/2": {"status": "OK", "fragments": [
    {"method": "jsonld:description", "tier": 1, "kind": "structured",
     "text_excerpt": "Últimas noticias de Argentina y el mundo – LA NACION",
     "quality_flags": ["malformed_text"], "sentence_like": False,
     "effective_new_tokens": 4, "effective_new_chars": 29, "length": 52,
     "containment": 0.2, "content_hash": "x"}]}}}
_r = il.subir_escada(json.loads(json.dumps(_pobre)), sidecar=_sujo)
check(_r["melhor_origem"] == il.R0_ARMAZENADO,
      f"[13] boilerplate de portal NÃO é selecionado ({_r['melhor_origem']})")
check(_r["escada"]["estado"] in (il.INSUFICIENTE, il.BLOQUEADO),
      "[14] e a escada reporta insuficiência em vez de fingir sucesso")
_bloq = {"articles": {"http://x/2": {"status": "BLOCKED_BY_ROBOTS",
                                     "fragments": []}}}
_r2 = il.subir_escada(json.loads(json.dumps(_pobre)), sidecar=_bloq)
check(_r2["escada"]["motivo"] == "robots",
      f"[15] robots é motivo próprio, não 'não deu certo' ({_r2['escada']['motivo']})")
check(set(il.MOTIVOS) >= {"robots", "fetch_falhou", "sem_fragmento_util"},
      "[16] motivos de fracasso são distinguidos")
_r3 = il.subir_escada(json.loads(json.dumps(_pobre)), sidecar={})
check(not any(t.get("rede") for t in _r3["escada"]["tentativas"]),
      "[17] sem autorização explícita, a escada não usa rede")
_bom = il.montar_article_input(
    url="http://x/3", titulo="Titulo do artigo",
    resumo="Uma frase suficientemente longa com conteudo real. " * 30)
_r4 = il.subir_escada(json.loads(json.dumps(_bom)))
check(_r4["escada"]["estado"] == il.R0_ARMAZENADO
      and len(_r4["escada"]["tentativas"]) == 1,
      "[18] R0 suficiente encerra a escada sem tentar mais nada")

print()
print("=" * 98)
print("BLOCO D — content:encoded: o texto descartado no parse")
print("=" * 98)
_xml = """<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><item><title>T</title><link>http://a/1</link>
<description>&lt;p&gt;resumo curto&lt;/p&gt;</description>
<content:encoded>&lt;p&gt;corpo inteiro do artigo com muito mais texto aqui dentro&lt;/p&gt;</content:encoded>
</item></channel></rss>"""
_it = list(ET.fromstring(_xml).iter("item"))[0]
_e = ic.extrair_do_item(_it)
check("corpo inteiro" in _e["content_encoded"],
      "[19] content:encoded é lido")
check(_e["melhor"] == _e["content_encoded"] and _e["ganho_chars"] > 0,
      f"[20] o corpo vence a description quando é maior ({_e['ganho_chars']} chars)")
check("<p>" not in _e["melhor"] and "&lt;" not in _e["melhor"],
      "[21] HTML e entidades são limpos")
_sem_ns = """<rss><channel><item><title>T</title>
<description>d</description><encoded>corpo sem namespace declarado</encoded>
</item></channel></rss>"""
_e2 = ic.extrair_do_item(list(ET.fromstring(_sem_ns).iter("item"))[0])
check("sem namespace" in _e2["content_encoded"],
      "[22] feed que erra o namespace ainda é aproveitado")
_so_desc = """<rss><channel><item><title>T</title>
<description>so descricao</description></item></channel></rss>"""
_e3 = ic.extrair_do_item(list(ET.fromstring(_so_desc).iter("item"))[0])
check(_e3["ganho_chars"] == 0 and _e3["melhor"] == "so descricao",
      "[23] sem corpo, a description continua valendo")
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
check("content" not in _src.split("def _parse_rss")[1].split("return articles")[0],
      "[24] o parser de PRODUÇÃO segue sem ler content:encoded — não foi tocado")

print()
print("=" * 98)
print("BLOCO E — captura pré-descarte")
print("=" * 98)
check(ic.capturar_pre_descarte({}, permitir_rede=False).get("erro"),
      "[25] sem autorização de rede, a captura não roda")
check(ic.auditar_feeds({}, permitir_rede=False).get("erro"),
      "[26] idem para a auditoria de feeds")
_cap = OUT / "pre_discard_capture.json"
if _cap.exists():
    c = json.load(io.open(_cap, encoding="utf-8"))
    check(c["telemetria"].get("sem_candidato", 0) > 0,
          f"[27] a captura encontra artigos sem candidato "
          f"({c['telemetria'].get('sem_candidato')})")
    check(c["taxa_descarte"] > 0.5,
          f"[28] a maior parte dos atribuídos é descartada ({c['taxa_descarte']})")
    check(all("article_id" in a and "empresas" in a for a in c["artigos"][:5]),
          "[29] artigo descartado tem a MESMA forma do artigo com candidato")
    check(all(a["descartado_pelo_filtro"] for a in c["artigos"]
              if not any(e["tem_candidato"] for e in a["empresas"])),
          "[30] o motivo do descarte é declarado por artigo")
else:
    for i in (27, 28, 29, 30):
        check(False, f"[{i}] captura ausente")

print()
print("=" * 98)
print("BLOCO F — desligada por padrão, sem escrita em produção, sem LLM")
print("=" * 98)
check(il.ATIVO is False, "[31] a camada nasce desligada")
_srcs = {"layer": io.open("reliability_input_layer.py", encoding="utf-8").read(),
         "capture": io.open("reliability_input_capture.py", encoding="utf-8").read()}
for nome, s in _srcs.items():
    check(all(x not in s for x in ("save_history", "merge_into_history",
                                   "--apply", "--backfill", "--reclassify")),
          f"[32..33] {nome} não escreve em history")
    check(all(x not in s for x in ("genai", "gemini", "GEMINI_API_KEY",
                                   "generate_content")),
          f"[34..35] {nome} não faz nenhuma chamada de LLM")
for alvo in ("risk_history.json", "config_risco.yaml",
             "risk_enrichment_shadow.json"):
    try:
        ic._guardar(Path(alvo))
        check(False, f"[36..38] deveria recusar escrever em {alvo}")
    except PermissionError:
        check(True, f"[36..38] recusa escrever em {alvo}")
check(ic._guardar(ic.OUTDIR / "x.json") is None,
      "[39] aceita escrever no diretório experimental")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("input_layer" not in _wf and "input_capture" not in _wf,
      "[40] nada disso está no workflow")

print()
print("=" * 98)
print("BLOCO G — versionamento e determinismo")
print("=" * 98)
_t = il.telemetria([_ai])
for k in ("input_layer_version", "ladder_version", "input_policy_version"):
    check(_t.get(k), f"[41..43] a telemetria carrega {k}")
check(_t["ativo"] is False, "[44] a telemetria declara o estado do interruptor")
_h = json.load(io.open("risk_history.json", encoding="utf-8"))
_a1 = il.telemetria(il.varrer(_h, limite=60))
_a2 = il.telemetria(il.varrer(_h, limite=60))
check(_a1 == _a2, "[45] duas varreduras seguidas dão o mesmo resultado")
check(_a1["pares_empresa_artigo"] >= _a1["artigos"],
      "[46] há ao menos um par empresa×artigo por artigo")
_antes = io.open("risk_history.json", "rb").read()
il.varrer(_h, limite=40)
check(io.open("risk_history.json", "rb").read() == _antes,
      "[47] varrer o corpus não toca risk_history.json")

print()
print("=" * 98)
print("BLOCO H — a camada não altera o que a produção lê")
print("=" * 98)
_cfg = rd.load_config("config_risco.yaml")
_amostra = list((_h.get("articles") or {}).items())[:30]
_iguais = 0
for url, rec in _amostra:
    ai = il.do_registro(url, rec)
    if ai["titulo"] == (rec.get("title") or ""):
        _iguais += 1
check(_iguais == len(_amostra),
      "[48] a camada lê o registro sem reescrever título")
for url, rec in _amostra[:10]:
    ai = il.do_registro(url, rec)
    emps = {e["empresa"] for e in ai["empresas"]}
    esperado = set(rec.get("companies") or []) | set(rec.get("events_by_company") or {})
    if emps != esperado:
        check(False, f"[49] empresas divergem em {url[:40]}")
        break
else:
    check(True, "[49] as empresas do artigo vêm do registro, sem invenção")
check(il.do_registro("u", {"title": "T"})["n_empresas"] == 0,
      "[50] registro sem empresa não inventa atribuição")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c (camada de input): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
