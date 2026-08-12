#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7c_rehearsal.py — 4I.2 R7c §10–§47.

AS REGRESSÕES OBRIGATÓRIAS DO §44, MAIS O QUE O REHEARSAL ENSINOU.

Três defeitos do próprio instrumento foram encontrados executando esta wave, e
cada um virou teste aqui:

1. NÃO TENTADO NÃO É VAZIO. Ao estourar o teto de fetches, 143 artigos foram
   rotulados EMPTY — como se o publisher não tivesse entregue nada. `EMPTY`
   passou a significar "pediram e não veio"; o resto tem rótulo próprio.

2. RESOLVER ANTES DE BUSCAR. O tap entrega wrapper do Google News. Buscar
   `news.google.com` bate em robots 100% das vezes, e a perda parecia do
   publisher. O corpus histórico não mostrava isso porque a produção já
   resolveu aquelas URLs mais tarde. Resolução virou etapa própria do funil.

3. SENSIBILIDADE DE POLÍTICA PRECISA DE DENOMINADOR ÚNICO. Reexecutar a escada
   por política media disponibilidade de rede, não rigor de política: as três
   colunas davam zero. Agora as três reavaliam os MESMOS componentes.

Zero LLM, zero escrita em produção, zero mudança de parser de produção.
"""
from __future__ import annotations

import io
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import reliability_input_layer as il
import reliability_input_capture as ic
import reliability_input_rehearsal as rh
import reliability_pilot_contract as ct
import reliability_pilot_validators as vl

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


TIT = "Empresa Alfa anuncia aquisicao da Beta"
CORPO = ("A Empresa Alfa comunicou nesta terca-feira que assinou contrato para "
         "adquirir a totalidade das acoes da Beta Participacoes por dois bilhoes "
         "de reais. A operacao ainda depende de aprovacao do conselho "
         "administrativo de defesa economica e deve ser concluida no proximo "
         "semestre, segundo comunicado enviado ao mercado. Analistas avaliam que "
         "a compra amplia a presenca da companhia no segmento de distribuicao.")

print("=" * 98)
print("BLOCO A — §44 duplicação do Google News não é conteúdo novo")
print("=" * 98)
_dup = rh.componentes(TIT, f"{TIT} &nbsp;&nbsp; InfoMoney")
check(_dup["meaningful_gain_vs_title"] <= 2,
      f"[1] resumo que repete o título não traz tokens novos "
      f"({_dup['meaningful_gain_vs_title']})")
check(not rh.pronto(_dup)["input_ready_under_r7c_policy"],
      "[2] e portanto não fica input-ready")
_real = rh.componentes(TIT, CORPO)
check(_real["meaningful_gain_vs_title"] > 30,
      f"[3] corpo real traz tokens novos ({_real['meaningful_gain_vs_title']})")
check(rh.pronto(_real)["input_ready_under_r7c_policy"],
      "[4] e fica input-ready sob a política SELECTED")
check(_real["sentence_like_count"] >= 3,
      f"[5] frases reconhecíveis são contadas ({_real['sentence_like_count']})")

print()
print("=" * 98)
print("BLOCO B — §44 content:encoded rico vence description pobre")
print("=" * 98)
_xml = f"""<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><item><title>{TIT}</title><link>http://a/1</link>
<description>{TIT} - Fonte</description>
<content:encoded>&lt;p&gt;{CORPO}&lt;/p&gt;</content:encoded>
</item></channel></rss>"""
_ricos = rh.campos_ricos(list(ET.fromstring(_xml).iter("item"))[0])
check(set(_ricos) >= {"rss:description", "content:encoded"},
      f"[6] os dois campos são extraídos ({sorted(_ricos)})")
_txt, _met, _c = rh.r0_extended(TIT, _ricos)
check(_met == "content:encoded",
      f"[7] R0-EXTENDED escolhe o campo mais rico ({_met})")
check(rh.pronto(_c)["input_ready_under_r7c_policy"],
      "[8] e isso basta para ficar pronto SEM requisição extra")
_so_pobre = {"rss:description": f"{TIT} - Fonte"}
check(rh.r0_extended(TIT, _so_pobre)[1] == "rss:description",
      "[9] sem campo rico, a description continua sendo o melhor disponível")
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
_pr = _src.split("def _parse_rss")[1].split("return articles")[0]
check("content" not in _pr and "encoded" not in _pr,
      "[10] o parser de PRODUÇÃO segue intocado")

print()
print("=" * 98)
print("BLOCO C — §10 a escada para cedo e §21 um fetch por artigo")
print("=" * 98)
_cont = {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {}}
_r = rh.processar_artigo(url="http://a/1", titulo=TIT, resumo=CORPO,
                         dominio="news.com", pub_iso="", empresas={"Alfa": ["ma"]},
                         ricos={}, rec={}, sidecar={}, permitir_rede=False,
                         contador=_cont)
check(not _r["r1_tentado"],
      "[11] R0 suficiente não dispara R1")
check(_cont["fetches"] == 0, "[12] e não gasta requisição")
_cont2 = {"fetches": 0, "duplicatas_evitadas": 0, "por_artigo": {}}
for emp in ("Alfa", "Beta", "Gama"):
    rh.processar_artigo(url="http://a/2", titulo="T pobre",
                        resumo="T pobre - Fonte", dominio="news.com",
                        pub_iso="", empresas={emp: []}, ricos={}, rec={},
                        sidecar={}, permitir_rede=False, contador=_cont2)
check(_cont2["duplicatas_evitadas"] == 2,
      f"[13] três empresas no mesmo artigo = UM enrichment "
      f"({_cont2['duplicatas_evitadas']} duplicatas evitadas)")
check(len(_cont2["por_artigo"]) == 1,
      "[14] o cache é por identidade de ARTIGO, não por empresa")

print()
print("=" * 98)
print("BLOCO D — §13 taxonomia de falha: nossa culpa × do publisher")
print("=" * 98)
check(rh.CAP_REACHED in rh.FALHAS and rh.RESOLUTION_FAILED in rh.FALHAS,
      "[15] teto e falha de resolução são estados próprios")
_capc = {"fetches": rh.MAX_FETCH_ARTIGOS, "duplicatas_evitadas": 0,
         "por_artigo": {}}
_rc = rh.enriquecer_uma_vez("http://a/9", "T", {}, sidecar={},
                            permitir_rede=True, contador=_capc)
check(_rc["falha"] == rh.CAP_REACHED,
      f"[16] estourado o teto, o artigo é CAP_REACHED — nunca EMPTY ({_rc['falha']})")
check(_capc["fetches"] == rh.MAX_FETCH_ARTIGOS,
      "[17] e nenhuma requisição extra é feita")
check(rh.classificar_falha({"status": "BLOCKED_BY_ROBOTS"}) == rh.ROBOTS_BLOCKED,
      "[18] robots é distinguido")
check(rh.classificar_falha({"status": "OK", "fragments": []}) == rh.EMPTY,
      "[19] pedimos e não veio nada = EMPTY")
check(rh.classificar_falha({"status": "OK", "fragments": [1]},
                           texto_sujo=True) == rh.DIRTY_ONLY,
      "[20] veio só sujeira = DIRTY_ONLY")
_res = {"por_artigo": {}, "fetches": 0, "duplicatas_evitadas": 0}
_u, _m, _f = rh.resolver_url("http://exemplo.com/noticia", permitir_rede=False,
                             contador=_res)
check(_m == "direto" and not _f,
      "[21] URL de publisher não precisa de resolução")
_u2, _m2, _f2 = rh.resolver_url(
    "https://news.google.com/rss/articles/CBMiabc", permitir_rede=False,
    contador=_res)
check(_f2 == rh.RESOLUTION_FAILED,
      "[22] wrapper sem rede é RESOLUTION_FAILED, não robots do publisher")

print()
print("=" * 98)
print("BLOCO E — §12/§44 contexto sujo nunca entra")
print("=" * 98)
_menu = ("Home Início Contato Sobre nós Termos de uso Política de privacidade "
         "Bankruptcy Sales, Chapter 11")
_cm = rh.componentes("T", _menu)
check(rh._sujo(_cm), "[23] menu de site é detectado como sujo")
check("dirty" in rh.pronto(_cm)["faltou"],
      "[24] e reprovado pela política, qualquer que seja o tamanho")
_cook = rh.componentes("T", "Aceitar todos os cookies. " + CORPO)
check("dirty" in rh.pronto(_cook)["faltou"],
      "[25] banner de cookie contamina o fragmento inteiro (W&W)")
_pay = rh.componentes("T", "Assine para continuar lendo. " + CORPO)
check(_pay["paywall_flag"], "[26] paywall é sinalizado")

print()
print("=" * 98)
print("BLOCO F — §17/§19/§30 best_input neutro, com cap e hash")
print("=" * 98)
_bi = rh.montar_best_input(TIT, [("content:encoded", CORPO)])
check(_bi["best_input"].startswith(TIT),
      "[27] o título é preservado no início")
check(CORPO[:40] in _bi["best_input"], "[28] o corpo entra na íntegra")
check(len(_bi["provenance"]) == 2 and _bi["provenance"][0]["metodo"] == "title",
      "[29] procedência por fragmento é registrada")
_dupf = rh.montar_best_input(TIT, [("a", TIT), ("b", TIT), ("c", CORPO)])
check(_dupf["best_input"].count(TIT) == 1,
      "[30] duplicação óbvia é removida")
_longo = rh.montar_best_input(TIT, [("x", "Frase de teste bem longa. " * 800)])
check(_longo["truncado"] and len(_longo["best_input"]) <= rh.BEST_INPUT_CAP,
      f"[31] o cap de {rh.BEST_INPUT_CAP} chars é respeitado")
check(_longo["chars_antes_do_cap"] > rh.BEST_INPUT_CAP,
      "[32] e o tamanho ANTES do cap é reportado")
check(rh.content_hash("a b  c") == rh.content_hash("a  b c "),
      "[33] espaço em branco não muda o content hash")
check(rh.montar_best_input(TIT, [("m1", CORPO)])["content_hash"]
      == rh.montar_best_input(TIT, [("m2", CORPO)])["content_hash"],
      "[34] mudar só a procedência não muda o hash")
check(rh.content_hash(CORPO) != rh.content_hash(CORPO + " Novo fato material."),
      "[35] fragmento novo muda o hash")
_neutro = io.open("reliability_input_rehearsal.py", encoding="utf-8").read()
_corpo_bi = _neutro.split("def montar_best_input")[1].split("def content_hash")[0]
_sem_doc = _corpo_bi.split('"""')[2] if _corpo_bi.count('"""') >= 2 else _corpo_bi
_codigo_bi = " ".join(l.split("#")[0] for l in _sem_doc.splitlines())
check(not any(k in _codigo_bi.lower() for k in ("falenc", "fraude", "aquisic",
                                                "default", "keyword", "termo")),
      "[36] §19 — o CÓDIGO do best_input não cita nenhuma keyword da taxonomia")

print()
print("=" * 98)
print("BLOCO G — §41/§42 compatibilidade com os payloads da R7b-A")
print("=" * 98)
_texto = rh.montar_best_input(TIT, [("content:encoded", CORPO)])["best_input"]
_pa = ct.payload_audit(texto=_texto, organizacao="Empresa Alfa",
                       aliases=["Alfa"], event_ids=["ma"], pub_iso="2026-08-12")
check(_pa["call_type"] == ct.CALL_AUDIT and "Empresa Alfa" in _pa["prompt"],
      "[37] o AUDIT monta com o best_input novo")
check(not vl.validar_schema({"events": []}, ct.SCHEMA_AUDIT),
      "[38] o schema do AUDIT continua válido")
_pd = ct.payload_discovery(texto=_texto, pub_iso="2026-08-12")
_fora = _pd["prompt"].replace(_pd["texto"], "")
check("Empresa Alfa" not in _fora,
      "[39] §42 — a DISCOVERY segue cega à empresa monitorada")
check("ma" not in _fora.split() and "event_id" not in _fora,
      "[40] e cega aos candidatos da taxonomia")
for nome, p in (("audit", _pa), ("discovery", _pd)):
    check(not ct.checar_payload({k: v for k, v in p.items() if k != "schema"},
                                texto_do_artigo=p["texto"]),
          f"[41..42] {nome} sem score/peso/tier/threshold")
check(vl.quote_valida(CORPO[:60], _pa["texto"]),
      "[43] quotes do corpo enriquecido validam contra o input")

print()
print("=" * 98)
print("BLOCO H — §43 sem dependência de LLM e §33 escrita atômica")
print("=" * 98)
for nome, arq in (("rehearsal", "reliability_input_rehearsal.py"),
                  ("runner", "reliability_input_rehearsal_run.py")):
    s = io.open(arq, encoding="utf-8").read()
    check(all(x not in s for x in ("genai", "gemini", "GEMINI_API_KEY",
                                   "generate_content")),
          f"[44..45] {nome} não importa nem chama provider de LLM")
_amb = dict(os.environ)
os.environ.pop("GEMINI_API_KEY", None)
try:
    import importlib
    importlib.reload(rh)
    check(rh.componentes("T", CORPO)["useful_chars"] > 0,
          "[46] a camada funciona sem chave e sem SDK instalado")
finally:
    os.environ.update(_amb)
import reliability_input_rehearsal_run as rr  # noqa: E402
check("os.replace" in io.open("reliability_input_rehearsal_run.py",
                              encoding="utf-8").read(),
      "[47] a gravação do store experimental é atômica")
try:
    rr._gravar_teste = None
    ic._guardar(Path("risk_history.json"))
    check(False, "[48] deveria recusar escrever em history")
except PermissionError:
    check(True, "[48] o store experimental recusa escrever em produção")

print()
print("=" * 98)
print("BLOCO I — §15 sensibilidade com denominador único")
print("=" * 98)
_regs = []
for txt in (f"{TIT} - Fonte", CORPO[:300], CORPO, CORPO * 3):
    c = rh.componentes(TIT, txt)
    _regs.append({"r0_legacy": c, "r0_extended": c, "final": c,
                  "n_empresas": 1, "falha": rh.OK, "tier_final": rh.TIER_R0_LEGACY,
                  "truncado": False, "r1_tentado": False, "r2_tentado": False,
                  "tem_algum_candidato": True, "enrichment": {}})
_s = rh.sensibilidade(_regs)
check(set(_s) == set(rh.POLITICAS), "[49] as três políticas são reportadas")
check(_s["PERMISSIVE"]["final_ready"] >= _s["SELECTED"]["final_ready"]
      >= _s["CONSERVATIVE"]["final_ready"],
      f"[50] mais permissivo aceita mais "
      f"({_s['PERMISSIVE']['final_ready']} ≥ {_s['SELECTED']['final_ready']} "
      f"≥ {_s['CONSERVATIVE']['final_ready']})")
check(len({_s[p]["final_ready"] + _s[p]["final_insufficient"]
           for p in rh.POLITICAS}) == 1,
      "[51] o denominador é IDÊNTICO nas três colunas")
check(_s["CONSERVATIVE"]["mediana_chars_aceitos"] >=
      _s["PERMISSIVE"]["mediana_chars_aceitos"] or
      _s["CONSERVATIVE"]["final_ready"] == 0,
      "[52] política mais dura aceita textos maiores")

print()
print("=" * 98)
print("BLOCO J — §20 provas nos dados reais capturados")
print("=" * 98)
_cap = OUT / "pre_discard_capture.json"
if _cap.exists():
    c = json.load(io.open(_cap, encoding="utf-8"))
    arts = c["artigos"]
    _a = [a for a in arts if any(e["tem_candidato"] for e in a["empresas"])]
    _b = [a for a in arts if not any(e["tem_candidato"] for e in a["empresas"])]
    _cc = [a for a in arts if len(a["empresas"]) > 1
           and any(e["tem_candidato"] for e in a["empresas"])
           and any(not e["tem_candidato"] for e in a["empresas"])]
    check(_a, f"[53] CASE A — empresa com candidato ({len(_a)})")
    check(_b, f"[54] CASE B — empresa sem candidato ({len(_b)})")
    check(all(e["candidatos"] == [] for a in _b for e in a["empresas"]),
          "[55] ausência é `[]`, nunca null")
    check(len({a["article_id"] for a in arts}) == len(arts),
          "[56] um único registro por artigo")
    if _cc:
        check(True, f"[57] CASE C — artigo multi-empresa misto ({len(_cc)})")
    else:
        check(all(len(a["empresas"]) >= 1 for a in arts),
              "[57] CASE C não ocorreu nesta captura; mapa por empresa preservado")
else:
    for i in range(53, 58):
        check(False, f"[{i}] captura pré-descarte ausente")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-rehearsal: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
