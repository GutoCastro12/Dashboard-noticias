#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reliability_enrichment.py — 4I.2 R5a §26.

Testes DETERMINÍSTICOS do enrichment: fixtures HTML locais, zero rede. As
fixtures reproduzem as ESTRUTURAS observadas nos artigos auditados (JSON-LD,
og:description, `<p>` contaminado por script inline, boilerplate), escritas à
mão — nenhum conteúdo de terceiro é copiado para o repositório.

O que estes testes protegem:
  · extração não inventa texto e não engole script como se fosse artigo;
  · `ganho_efetivo` mede conteúdo NOVO, não mero preenchimento — é o que
    impede chamar de enrichment o `summary` do Google News, que é o próprio
    título mais o nome do veículo;
  · robots é consultado ANTES do fetch, e `disallow` não é contornado.
"""
from __future__ import annotations

import io
from pathlib import Path

import reliability_enrichment as enr
import reliability_input_audit as ia

PASS = FAIL = 0
FIX = Path("test_fixtures_reliability/enrichment")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _html(nome):
    return io.open(FIX / nome, encoding="utf-8").read()


print("=" * 96)
print("BLOCO A — extração determinística por método")
print("=" * 96)
_ex = dict(enr.extrair(_html("jsonld.html")))
check("jsonld:description" in _ex, f"[1] JSON-LD description extraído ({len(_ex)} métodos)")
check("jsonld:articleBody" in _ex, "[2] JSON-LD articleBody extraído")
check("meta:og:description" in _ex, "[3] og:description extraído")
check("meta:description" in _ex, "[4] meta description extraído")
check("html:paragrafos" in _ex, "[5] excerto de parágrafos extraído")
check("costing the utility money" in _ex["jsonld:description"],
      "[6] o texto vem literal da fonte, sem reescrita")
check(enr.extrair(_html("vazio.html")) == [],
      "[7] página sem campo algum devolve lista vazia, não texto inventado")

print()
print("=" * 96)
print("BLOCO B — GANHO EFETIVO: preenchido não é o mesmo que informativo")
print("=" * 96)
_t = "Capital One has more room to run after its Discover acquisition, Jim Cramer says"
_s = f"{_t} &nbsp;&nbsp; CNBC"
_g = ia.ganho_efetivo(_t, _s)
_novos = ia._tokens(_s) - ia._tokens(_t)
# O "ganho" do Google News é a entidade HTML do separador e o nome do veículo.
# Asserir a NATUREZA dos tokens é mais forte que escolher um limiar.
check(_novos <= {"nbsp", "cnbc"},
      f"[8] o summary do Google News só acrescenta separador e veículo: {sorted(_novos)}")
_g2 = ia.ganho_efetivo(_t, "Deputies said representatives discovered fraudulent accounts")
check(_g2["tokens_novos"] >= 5 and _g2["containment"] < 0.3,
      f"[9] texto realmente novo é reconhecido como ganho ({_g2['tokens_novos']} tokens)")
check(ia.ganho_efetivo("abc def", "abc def")["duplicado"],
      "[10] repetição pura é marcada como duplicada")
check(ia.ganho_efetivo("abc", "")["duplicado"],
      "[11] texto vazio nunca conta como enriquecimento")

print()
print("=" * 96)
print("BLOCO C — contaminação: script inline não é artigo")
print("=" * 96)
_ex2 = dict(enr.extrair(_html("script_em_paragrafo.html")))
check("codigo_javascript" in enr._boilerplate(_ex2["html:paragrafos"]),
      "[12] `<p>` com script é marcado como codigo_javascript")
check(not enr._boilerplate(_ex2["meta:og:description"]),
      "[13] a descrição limpa não é marcada como boilerplate")
_reg = {"enrichment": [
    {"extraction_method": "html:paragrafos", "text": _ex2["html:paragrafos"],
     "effective_new_tokens": 99, "duplicated_title": False,
     "boilerplate": enr._boilerplate(_ex2["html:paragrafos"])},
    {"extraction_method": "meta:og:description", "text": _ex2["meta:og:description"],
     "effective_new_tokens": 5, "duplicated_title": False, "boilerplate": []},
]}
check(enr.melhor(_reg)["extraction_method"] == "meta:og:description",
      "[14] a escolha prefere texto LIMPO a texto VOLUMOSO — volume não vence sujeira")
_bo = enr._boilerplate(dict(enr.extrair(_html("boilerplate.html")))["html:paragrafos"])
check("cookie_consent" in _bo and "newsletter" in _bo,
      f"[15] boilerplate clássico é detectado: {_bo}")

print()
print("=" * 96)
print("BLOCO D — robots é gate de entrada, não sugestão")
print("=" * 96)
_src = io.open("reliability_enrichment.py", encoding="utf-8").read()
_corpo = _src.split("def enriquecer(")[1].split("\ndef ")[0]
check(_corpo.index("_robots_permite") < _corpo.index("requests.get"),
      "[16] robots é consultado ANTES de qualquer requisição")
check("BLOCKED_BY_ROBOTS" in _corpo and "return reg" in _corpo,
      "[17] disallow encerra o processamento do artigo, sem fetch")
import ast  # noqa: E402  (usado só nas asserções de escopo)

# olha o CÓDIGO, não a prosa: a docstring cita "LLM/embedding" justamente
# para dizer que não os usa.
_arvore = ast.parse(_src)
_imports = {n.module.split(".")[0] for n in ast.walk(_arvore)
            if isinstance(n, ast.ImportFrom) and n.module}
_imports |= {a.name.split(".")[0] for n in ast.walk(_arvore)
             if isinstance(n, ast.Import) for a in n.names}
for termo in ("openai", "anthropic", "spacy", "transformers", "nltk",
              "sentence_transformers", "torch", "sklearn"):
    check(termo not in _imports, f"[18..25] sem LLM/NER/embedding importado ('{termo}')")
check(enr.melhor({"enrichment": []}) is None,
      "[26] sem extração não há 'melhor' — nada é inventado")

print()
print("=" * 96)
print("BLOCO E — o shadow nunca toca produção")
print("=" * 96)
_aberturas = [n for n in ast.walk(_arvore) if isinstance(n, ast.Call)
              and getattr(getattr(n, "func", None), "attr", "") == "open"]
_modos = {(a.value if isinstance(a, ast.Constant) else "?")
          for c in _aberturas for a in c.args[1:2]}
check(all(m in ("?", "r", "rb") for m in _modos),
      f"[27] toda abertura de arquivo do módulo é de leitura: {sorted(_modos) or ['default r']}")
check(all(x not in _src for x in ("save_history", "--apply", "merge_into_history")),
      "[28] não há caminho de escrita em history no módulo de enrichment")
check(str(enr.SHADOW).endswith("enrichment_shadow.json")
      and "out_reliability" in str(enr.SHADOW),
      f"[29] o resultado vai para artefato separado: {enr.SHADOW}")
check(enr.HISTORY.name == "risk_history.json" and "write" not in _src.split("HISTORY")[1][:200],
      "[30] o history é referenciado só como origem de leitura")

print()
print("=" * 96)
print(f"RESULTADO ENRICHMENT: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
