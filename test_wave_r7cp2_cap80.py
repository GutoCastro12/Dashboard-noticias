#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp2_cap80.py — 4I.2 R7c-P2 §19.

O TETO É 80 ARTIGOS ÚNICOS, E É O CONTADOR QUE MANDA.

Antes desta wave o `max_fetch` do coletor prospectivo era decorativo:
`coletar()` recebia o parâmetro e nunca o repassava, então quem governava era
a constante do módulo de rehearsal. Trocar o número no lugar errado não teria
efeito nenhum — e o run seguinte pareceria confirmar um teto que nunca foi
aplicado. Os testes abaixo fixam de onde o limite vem.

A unidade continua sendo o ARTIGO: um artigo citado por três emissores é uma
requisição, não três. E `CAP_REACHED` continua distinto de `EMPTY` — não ter
pedido não é o publisher não ter entregue.

O teto do R5/R6 (`MAX_REQUESTS_POR_RUN`) fica onde está: é outro caminho, com
outra política, e mexer nele seria alterar produção.
"""
from __future__ import annotations

import io

import reliability_enrichment_sidecar as sc
import reliability_input_rehearsal as rh
import reliability_input_shadow as sh

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _cont(fetches=0, limite=None):
    c = {"fetches": fetches, "duplicatas_evitadas": 0, "por_artigo": {}}
    if limite is not None:
        c["limite_fetch"] = limite
    return c


print("=" * 96)
print("BLOCO A — o teto é 80 e vem do contador")
print("=" * 96)
check(sh.MAX_FETCH_POR_RUN == 80,
      f"[1] o coletor prospectivo declara 80 ({sh.MAX_FETCH_POR_RUN})")
check(rh.enriquecer_uma_vez("http://a/81", "T", {}, sidecar={},
                            permitir_rede=True,
                            contador=_cont(80, 80))["falha"] == rh.CAP_REACHED,
      "[2] o 81º artigo com teto 80 é CAP_REACHED")
_c = _cont(79, 80)
_r = rh.enriquecer_uma_vez("http://inexistente-zzz.test/a", "T", {}, sidecar={},
                           permitir_rede=False, contador=_c)
check(_r["falha"] != rh.CAP_REACHED,
      f"[3] o 80º ainda pode ser tentado ({_r['falha']})")
check(rh.enriquecer_uma_vez("http://a/41", "T", {}, sidecar={},
                            permitir_rede=True,
                            contador=_cont(40, 80))["falha"] != rh.CAP_REACHED,
      "[4] com teto 80, o 41º NÃO é mais barrado")
check(rh.enriquecer_uma_vez("http://a/41b", "T", {}, sidecar={},
                            permitir_rede=True,
                            contador=_cont(40))["falha"] == rh.CAP_REACHED,
      "[5] sem `limite_fetch`, cai no default do módulo (40)")
_src = io.open("reliability_input_rehearsal.py", encoding="utf-8").read()
_corpo = _src.split("def enriquecer_uma_vez")[1].split("\ndef ")[0]
check('contador.get("limite_fetch"' in _corpo,
      "[6] o limite é lido do contador, não de constante fixa")

print()
print("=" * 96)
print("BLOCO B — o coletor prospectivo realmente propaga o teto")
print("=" * 96)
_sh = io.open("reliability_input_shadow.py", encoding="utf-8").read()
check('"limite_fetch": max_fetch' in _sh,
      "[7] `coletar()` põe o teto no contador")
_col = _sh.split("def coletar(")[1].split("\ndef ")[0]
check("max_fetch" in _col and "limite_fetch" in _col,
      "[8] o parâmetro deixou de ser decorativo")
check("--max-fetch" in _sh and "max_fetch=a.max_fetch" in _sh,
      "[9] a CLI continua podendo sobrescrever o teto")

print()
print("=" * 96)
print("BLOCO C — a unidade é o ARTIGO, não o par empresa×artigo")
print("=" * 96)
_c2 = _cont(0, 80)
for emp in ("A", "B", "C"):
    rh.processar_artigo(url="http://a/multi", titulo="T pobre",
                        resumo="T pobre - Fonte", dominio="n.com",
                        pub_iso="", empresas={emp: []}, ricos=None, rec={},
                        sidecar={}, permitir_rede=False, contador=_c2)
check(_c2["duplicatas_evitadas"] == 2,
      f"[10] três empresas consomem UMA unidade ({_c2['duplicatas_evitadas']} evitadas)")
check(len(_c2["por_artigo"]) == 1,
      "[11] o cache é por identidade de artigo")
check(_c2["fetches"] == 0,
      "[12] e sem rede autorizada, nenhuma requisição é gasta")

print()
print("=" * 96)
print("BLOCO D — CAP_REACHED ≠ EMPTY, e a escada continua economizando")
print("=" * 96)
check(rh.CAP_REACHED != rh.EMPTY and rh.CAP_REACHED in rh.FALHAS,
      "[13] são estados distintos")
_c3 = _cont(0, 80)
_CORPO = (
    "A companhia comunicou nesta terca-feira que assinou contrato vinculante "
    "para adquirir a totalidade das acoes ordinarias da subsidiaria brasileira "
    "por dois bilhoes e quinhentos milhoes de reais. A operacao depende ainda "
    "de aprovacao pelo conselho administrativo de defesa economica e pelos "
    "acionistas minoritarios reunidos em assembleia extraordinaria. Segundo o "
    "fato relevante enviado ao mercado, o pagamento sera feito em tres parcelas "
    "semestrais corrigidas pela taxa basica de juros. Analistas consultados "
    "avaliam que a compra amplia significativamente a presenca da empresa no "
    "segmento de distribuicao e logistica no nordeste do pais.")
_r3 = rh.processar_artigo(
    url="http://a/rico", titulo="Titulo do artigo", resumo=_CORPO,
    dominio="n.com", pub_iso="", empresas={"A": ["ma"]}, ricos=None, rec={},
    sidecar={}, permitir_rede=True, contador=_c3)
check(not _r3["r1_tentado"] and _c3["fetches"] == 0,
      "[14] R0 suficiente não gasta requisição, mesmo com teto alto")
_reuso = {"articles": {"http://a/cache": {"status": "OK", "fragments": [
    {"method": "jsonld:articleBody", "tier": 1, "kind": "structured",
     "text_excerpt": "Uma frase longa e narrativa de verdade sobre o evento. " * 8,
     "quality_flags": [], "sentence_like": True, "effective_new_tokens": 40,
     "effective_new_chars": 300, "length": 400, "containment": 0.1,
     "content_hash": "h"}]}}}
_c4 = _cont(0, 80)
rh.enriquecer_uma_vez("http://a/cache", "T", {}, sidecar=_reuso,
                      permitir_rede=True, contador=_c4)
check(_c4["fetches"] == 0,
      "[15] reuso de sidecar não gera requisição nova")

print()
print("=" * 96)
print("BLOCO E — politeness por host")
print("=" * 96)
check(rh.PAUSA_POR_HOST > 0,
      f"[16] há espaçamento entre requisições ao mesmo host ({rh.PAUSA_POR_HOST}s)")
check("por_host" in _corpo and "PAUSA_POR_HOST" in _corpo,
      "[17] a contagem é por HOST, aplicada a partir da segunda requisição")
_f = rh.funil([], _cont(0, 80))
check("max_requests_um_host" in _f and "requests_por_host" in _f,
      "[18] o funil reporta concentração por domínio")
check(_f.get("limite_fetch") == 80,
      f"[19] o funil declara o teto vigente ({_f.get('limite_fetch')})")

print()
print("=" * 96)
print("BLOCO F — o teto do R5/R6 e o do rehearsal local ficam onde estão")
print("=" * 96)
check(sc.MAX_REQUESTS_POR_RUN == 40,
      f"[20] R5/R6 intocado em 40 ({sc.MAX_REQUESTS_POR_RUN})")
check(rh.MAX_FETCH_ARTIGOS == 40,
      f"[21] o rehearsal LOCAL segue 40 ({rh.MAX_FETCH_ARTIGOS})")
_sc = io.open("reliability_enrichment_sidecar.py", encoding="utf-8").read()
check("limite_fetch" not in _sc,
      "[22] o caminho do R5/R6 não foi tocado por esta mudança")

print()
print("=" * 96)
print("BLOCO G — shadow segue isolado e fail-open")
print("=" * 96)
check("risk_input_shadow" not in io.open("risk_dashboard.py", encoding="utf-8").read(),
      "[23] o sidecar continua fora de qualquer caminho de produção")
check("traceback.print_exc" in _sh,
      "[24] a falha do shadow continua visível no log")
check(all(x not in _sh for x in ("genai", "gemini", "GEMINI_API_KEY")),
      "[25] nenhuma dependência de LLM")
_wf = io.open(".github/workflows/update_risk_dashboard.yml",
              encoding="utf-8").read()
check("continue-on-error: true" in _wf.split("Shadow input layer")[1][:200],
      "[26] o passo segue com continue-on-error")
check("timeout-minutes: 8" in _wf.split("Shadow input layer")[1][:200],
      "[27] timeout mantido em 8 minutos")

print()
print("=" * 96)
print(f"RESULTADO WAVE R7c-P2 (cap 80): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
