#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r3_individual_affiliation.py — 4I.2 R3 / família F4.

AFILIAÇÃO INDIVIDUAL ≠ SUJEITO DO EVENTO. "ex-CEO de X" identifica uma
PESSOA; não faz de X o sujeito da falência/default alheios.

A regra proibida — `ex-CEO de X` ⇒ remove evento de X — não foi implementada.
`R_AFILIACAO_INDIVIDUAL` exige DUAS condições cumulativas:

  1. a monitorada aparece EXCLUSIVAMENTE dentro da construção de afiliação;
  2. o termo do evento tem OUTRO núcleo nominal, provado por uma destas três
     formas — nome próprio separado por pontuação forte, nome próprio com
     classificador comum ("supplier Beta"), ou substantivo de companhia com
     determinante INDEFINIDO ("una petrolera").

Anáfora definida ("the company may default") NÃO conta: retoma a própria
monitorada. Aposto de nome de pessoa ("ex-CEO da Vale Fabio Schvartsman")
também não — não há classificador nem pontuação entre eles.

Escopo deliberado: `falencia`, `default`, `investigacao_regulatoria` — as
famílias com caso real observado. Menor blast radius vence.
"""
from __future__ import annotations

import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
KW = sa._keywords_por_evento(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pontua(title, company, summary=""):
    h = {"articles": {"u1": {"title": title, "summary": summary, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1786000000,
                             "pub_iso": "2026-08-07 04:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    rec = h["articles"]["u1"]
    regras = {d.get("regra") for d in (rec.get("semantic_discards") or [])}
    return set(rec.get("events_by_company", {}).get(company) or []), regras


def afiliacao(t, emp, ev):
    return sa.detect_individual_affiliation_role(t, emp, AL.get(emp) or [emp],
                                                 KW.get(ev) or [])


# Os dois artigos reais, literalmente como estão persistidos (só título e
# sumário duplicado — nenhum dos dois tem corpo).
_R1 = ("Vista se uniría a un exCEO de YPF y a la dueña de Puma Energy "
       "para rescatar a una petrolera en default")
_R2 = ("“Sin reestructuración, Aconcagua irá a la quiebra”: el duro pronóstico "
       "del exCEO de YPF")

print("=" * 96)
print("BLOCO A — os dois casos reais YPF")
print("=" * 96)
_g, _r = pontua(_R1, "YPF")
check("default" not in _g, f"[1] YPF NÃO recebe default (obtido {sorted(_g)})")
check("R_AFILIACAO_INDIVIDUAL" in _r, "[2] a remoção é atribuída a R_AFILIACAO_INDIVIDUAL")
_g, _r = pontua(_R2, "YPF")
check("falencia" not in _g, f"[3] YPF NÃO recebe falencia (obtido {sorted(_g)})")
check("R_AFILIACAO_INDIVIDUAL" in _r, "[4] a remoção é atribuída a R_AFILIACAO_INDIVIDUAL")
check(afiliacao(_R1, "YPF", "default") == "petrolera",
      "[5] o sujeito devolvido é o substantivo indefinido 'petrolera'")
check(afiliacao(_R2, "YPF", "falencia") == "Aconcagua",
      "[6] o sujeito devolvido é a entidade nomeada 'Aconcagua'")

print()
print("=" * 96)
print("BLOCO B — as três formas de provar OUTRO sujeito")
print("=" * 96)
check(afiliacao("Former CEO of Duke Energy says supplier Beta will default on its notes",
                "Duke Energy", "default").endswith("Beta"),
      "[7] classificador comum antes do nome próprio ('supplier Beta') prova sujeito")
check(afiliacao("El expresidente de YPF advirtió que una empresa entró en default",
                "YPF", "default") == "empresa",
      "[8] substantivo de companhia com determinante indefinido prova sujeito")
check(afiliacao(_R2, "YPF", "falencia") == "Aconcagua",
      "[9] pontuação forte entre o nome e a monitorada prova sujeito")

print()
print("=" * 96)
print("BLOCO C — a regra PROIBIDA não foi implementada")
print("=" * 96)
_casos_sem_outro_sujeito = [
    ("Former CEO of Petrobras discusses bankruptcy", "Petrobras", "falencia"),
    ("Former CEO of Petrobras warns the company may default", "Petrobras", "default"),
    ("Ex-CEO da Vale Fabio Schvartsman e citado em processo sobre falencia",
     "Vale", "falencia"),
]
for t, emp, ev in _casos_sem_outro_sujeito:
    check(afiliacao(t, emp, ev) == "",
          f"[10..12] afiliação sem outro sujeito NÃO produz evidência :: {t[:52]}")
    _g, _ = pontua(t, emp)
    check(ev in _g, f"[13..15] e o evento {ev} PERMANECE em {emp}")

print()
print("=" * 96)
print("BLOCO D — condição (1): monitorada fora da afiliação volta a ser sujeito")
print("=" * 96)
for t, emp, ev in [
    ("Former CEO of Petrobras says Petrobras may default on its bonds",
     "Petrobras", "default"),
    ("Ex-CEO da Vale assume cargo enquanto a Vale entra em default", "Vale", "default"),
    ("Ex-presidente da Vale nao evitou o pedido de falencia da Vale", "Vale", "falencia"),
]:
    check(afiliacao(t, emp, ev) == "",
          f"[16..18] segunda menção fora do aposto neutraliza a regra :: {t[:52]}")
    _g, _ = pontua(t, emp)
    check(ev in _g, f"[19..21] e o evento {ev} PERMANECE em {emp}")

print()
print("=" * 96)
print("BLOCO E — eventos próprios e sem afiliação alguma ficam intactos")
print("=" * 96)
for t, emp, ev in [
    ("Petrobras pede recuperacao judicial", "Petrobras", "recuperacao_judicial"),
    ("Vale entra em default, segundo comunicado ao mercado", "Vale", "default"),
    ("CEO da Vale confirma que a companhia entrou em default", "Vale", "default"),
    ("CVM abre processo administrativo sancionador contra a Petrobras",
     "Petrobras", "investigacao_regulatoria"),
]:
    _g, _ = pontua(t, emp)
    check(ev in _g, f"[22..25] {ev} preservado em {emp} :: {t[:52]}")

print()
print("=" * 96)
print("BLOCO F — escopo: só as famílias com caso real observado")
print("=" * 96)
import re  # noqa: E402  (usado só na asserção de escopo)

_src = open("semantic_audit.py", encoding="utf-8").read()
# ancora no PRÓPRIO gate F4: outras famílias também usam `if not _papel and
# ev in (...)`, então casar pelo primeiro match pega a regra errada.
_m = re.search(r'if not _papel and ev in \(([^)]*)\):'
               r'(?:(?!if not _papel).)*?detect_individual_affiliation_role',
               _src, re.S)
check(_m is not None, "[26] o gate F4 está condicionado a uma lista explícita de eventos")
if _m:
    _escopo = {x.strip().strip("\"'") for x in _m.group(1).split(",") if x.strip()}
    check(_escopo == {"falencia", "default", "investigacao_regulatoria"},
          f"[27] escopo exatamente falencia/default/investigacao_regulatoria: {sorted(_escopo)}")
_g, _r = pontua("Former CEO of Duke Energy charged in fraud at supplier Beta", "Duke Energy")
check("fraude" in _g and "R_AFILIACAO_INDIVIDUAL" not in _r,
      f"[28] `fraude` fica FORA do escopo — o evento permanece (obtido {sorted(_g)})")

print()
print("=" * 96)
print("BLOCO G — sem rede, sem NER, sem LLM, sem hard-code")
print("=" * 96)
_bloco = _src.split("# ── 4I.2 R3/F4")[1].split("def detect_papel_nao_sujeito")[0]
# comentários explicam o caso observado; o que não pode existir é o nome
# dentro do CÓDIGO.
_codigo = "\n".join(l for l in _bloco.splitlines() if not l.strip().startswith("#"))
for termo in ("YPF", "Aconcagua", "Clarin", "Puma Energy", "Diario", "Duke", "Beta"):
    check(termo not in _codigo, f"[29..35] nenhum hard-code de '{termo}' na regra")
for termo in ("requests", "urlopen", "openai", "anthropic", "spacy", "nltk"):
    check(termo not in _codigo, f"[36..41] a regra não usa rede/NER/LLM ('{termo}')")

print()
print("=" * 96)
print(f"RESULTADO WAVE R3 (afiliação individual): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
