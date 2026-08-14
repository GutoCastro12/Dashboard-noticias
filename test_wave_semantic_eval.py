#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_semantic_eval.py — a projeção semântica é geral, não ajustada.

O QUE ESTE TESTE PROTEGE

1. Que a projeção seja CONTRACT-NATIVE e GERAL: nenhuma regra por empresa,
   por sample_id ou por caso. Uma régua ajustada aos 8 controles mediria a si
   mesma.

2. Que ela combine as dimensões em vez de reduzir tudo a "o sujeito casa com a
   empresa" — o defeito que fez o run 31758509054 reportar 2/8 enquanto os
   modelos devolviam `SELLER` e `related_entity` corretos.

3. Que `SELLER → não pontua` NÃO seja a regra inteira: é um componente,
   escopado à família M&A, e vindo da adjudicação S3-A já registrada.

4. Que aplicabilidade seja explícita: dimensão que a verdade não fixa não
   entra no denominador. Penalizar por dimensão inaplicável inventa erro.

Os casos deste arquivo são SINTÉTICOS e contratuais — existem para fixar a
regra antes de ela tocar o artefato.

NENHUMA CHAMADA A PROVIDER.
"""
from __future__ import annotations

import inspect
import io
import re

import bench_semantic_eval as ev

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def E(**kw):
    """Evento contratual completo, com os defaults 'tudo passa'."""
    base = {"event_id": "ma", "event_asserted": "ASSERTED", "subject": "Acme",
            "company_role": "SUBJECT", "currentness": "CURRENT",
            "phase": "CONCLUDED", "centrality": "MAIN",
            "related_entity": None, "field_support": "SUPPORTED"}
    base.update(kw)
    return base


print("=" * 98)
print("BLOCO A — A REGRA É GERAL: nada de empresa ou caso no código")
print("=" * 98)
_src = io.open("bench_semantic_eval.py", encoding="utf-8").read()
_fonte_proj = inspect.getsource(ev.projetar_pontuavel)
_codigo = "\n".join(l.split("#")[0] for l in _fonte_proj.splitlines())
_codigo = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", _codigo)
for _nome in ("Cemig", "Sabesp", "BTG", "YPF", "PRIO", "TIM", "Security", "B3"):
    check(_nome not in _codigo, f"[1..8] a projeção não menciona {_nome}")
check(not re.search(r"P1-\d", _codigo),
      "[9] e não menciona sample_id algum")
check("VERDADE" not in _codigo,
      "[10] a projeção não lê a tabela de verdade — ela não sabe a resposta")

print()
print("=" * 98)
print("BLOCO B — MATRIZ CONTRATUAL (sintética, pré-registrada)")
print("=" * 98)
# A: empresa é sujeito, atual, central, M&A societário válido → pontua
_a = ev.projetar_pontuavel(E(), "Acme", [], "ma")
check(_a["pontuavel"] is True and _a["porta"] == "NENHUMA",
      "[11] A: sujeito + atual + central + afirmado → pontua")

# B: sujeito é terceiro → não pontua
_b = ev.projetar_pontuavel(E(subject="Outra Cia"), "Acme", [], "ma")
check(_b["pontuavel"] is False and _b["porta"] == "SUJEITO",
      "[12] B: sujeito é terceiro → não pontua (porta SUJEITO)")

# C: histórico/follow-up → não é nova ocorrência
_c = ev.projetar_pontuavel(E(currentness="HISTORICAL"), "Acme", [], "ma")
check(_c["pontuavel"] is False and _c["porta"] == "VIGENCIA",
      "[13] C: vigência histórica → não cria nova ocorrência")

# D: objeto de ativo — o contrato NÃO representa isso
check(all(d not in E() for d in ev.DIMENSOES_AUSENTES_DO_CONTRATO),
      "[14] D: transaction_object/scope NÃO existem no contrato — o caso de "
      "fazenda vs. controle societário não é decidível pelos campos")

# E: empresa vendedora em M&A → o sujeito da aquisição é a contraparte
_e = ev.projetar_pontuavel(E(company_role="SELLER", subject="Acme",
                             related_entity="Compradora SA"), "Acme", [], "ma")
check(_e["pontuavel"] is False and _e["porta"] == "SUJEITO_E_PAPEL",
      "[15] E: papel SELLER em M&A põe o sujeito na contraparte")
check("Compradora SA" in _e["motivo"],
      "[16] e o motivo nomeia a contraparte que o modelo informou")

# F: M&A citado como contexto causal num artigo de rating → não pontua
_f = ev.projetar_pontuavel(E(centrality="BACKGROUND"), "Acme", [], "ma")
check(_f["pontuavel"] is False and _f["porta"] == "CENTRALIDADE",
      "[17] F: evento de fundo é contexto, não ocorrência")

print()
print("=" * 98)
print("BLOCO C — ROLE-SENSITIVE NÃO É ROLE-ONLY")
print("=" * 98)
_g = ev.projetar_pontuavel(E(company_role="SELLER"), "Acme", [], "falencia")
check(_g["pontuavel"] is True,
      "[18] SELLER FORA da família M&A não fecha porta alguma — a regra é "
      "escopada, não um veto universal ao vendedor")
check(ev.PAPEL_MA_CONTRAPARTE == frozenset({"SELLER"}),
      "[19] o veto de papel existe SÓ para SELLER e SÓ em M&A")
_h = ev.projetar_pontuavel(E(company_role="BUYER"), "Acme", [], "ma")
check(_h["pontuavel"] is True,
      "[20] BUYER não é automaticamente pontuável por ser BUYER — passou "
      "porque TODAS as outras portas também passaram")
_i = ev.projetar_pontuavel(E(company_role="BUYER", currentness="HISTORICAL"),
                           "Acme", [], "ma")
check(_i["pontuavel"] is False and _i["porta"] == "VIGENCIA",
      "[21] e BUYER histórico não pontua — papel sozinho não decide")
_j = ev.projetar_pontuavel(E(company_role="TARGET"), "Acme", [], "ma")
check(_j["pontuavel"] is True,
      "[22] TARGET é atributivo: ser adquirido é material para o emissor")

print()
print("=" * 98)
print("BLOCO D — DEMAIS PORTAS")
print("=" * 98)
for _est, _porta, _n in (("MENTIONED_ONLY", "ASSERCAO", 23),
                         ("DENIED", "ASSERCAO", 24),
                         ("UNCLEAR", "ASSERCAO", 25)):
    _r = ev.projetar_pontuavel(E(event_asserted=_est), "Acme", [], "ma")
    check(_r["pontuavel"] is False and _r["porta"] == _porta,
          f"[{_n}] {_est} → não afirmado, não pontua")
_k = ev.projetar_pontuavel(E(phase="RUMOR"), "Acme", [], "ma")
check(_k["pontuavel"] is False and _k["porta"] == "FASE",
      "[26] rumor não consuma ocorrência")
for _papel, _n in (("MENTIONED", 27), ("UNRELATED", 28), ("UNKNOWN", 29)):
    _r = ev.projetar_pontuavel(E(company_role=_papel), "Acme", [], "falencia")
    check(_r["pontuavel"] is False and _r["porta"] == "PAPEL",
          f"[{_n}] papel {_papel} não atribui a ocorrência à empresa")
_l = ev.projetar_pontuavel(None, "Acme", [], "ma")
check(_l["pontuavel"] is None,
      "[30] evento ausente é indecidível, não 'não pontua'")

print()
print("=" * 98)
print("BLOCO E — ORDEM E AUDITABILIDADE DAS PORTAS")
print("=" * 98)
_m = ev.projetar_pontuavel(E(event_asserted="DENIED", currentness="HISTORICAL",
                             centrality="BACKGROUND"), "Acme", [], "ma")
check(_m["porta"] == "ASSERCAO",
      "[31] a porta mais forte fecha primeiro — a decisão é rastreável")
check(all("porta" in ev.projetar_pontuavel(E(**k), "Acme", [], "ma")
          for k in ({}, {"currentness": "UNDATABLE"}, {"phase": "RUMOR"})),
      "[32] toda decisão declara por qual porta passou")

print()
print("=" * 98)
print("BLOCO F — APLICABILIDADE: dimensão sem verdade não entra no denominador")
print("=" * 98)
_v = {"sujeito": ["Acme"], "papel": ["BUYER"], "terceiro": None,
      "vigencia": ["CURRENT"], "fase": None, "centralidade": ["MAIN"]}
_d = ev.avaliar_dimensoes(E(company_role="BUYER"), _v)
check(_d["terceiro"]["aplicavel"] is False and _d["fase"]["aplicavel"] is False,
      "[33] dimensões sem verdade declarada são INAPLICÁVEIS")
check(_d["sujeito"]["correto"] and _d["papel"]["correto"]
      and _d["vigencia"]["correto"] and _d["centralidade"]["correto"],
      "[34] e as aplicáveis são avaliadas normalmente")
_d2 = ev.avaliar_dimensoes(E(company_role="SELLER"), _v)
check(_d2["papel"]["correto"] is False
      and _d2["papel"]["devolvido"] == "SELLER",
      "[35] uma dimensão errada é marcada com o valor devolvido, para auditoria")
_d3 = ev.avaliar_dimensoes(None, _v)
check(all(not _d3[k]["correto"] for k in _d3),
      "[36] sem evento, nenhuma dimensão é dada como correta")

print()
print("=" * 98)
print("BLOCO G — VERDADE DIMENSIONAL VEM DE ADJUDICAÇÃO, NÃO DE OUTPUT")
print("=" * 98)
check(set(ev.VERDADE) == {"P1-002", "P1-003", "P1-004", "P1-005",
                          "P1-007", "P1-008", "P1-009", "P1-010"},
      "[37] os 8 controles humanos têm verdade dimensional declarada")
check(ev.VERDADE["P1-002"]["papel"] == ["SELLER"],
      "[38] Cemig: papel adjudicado é SELLER (família S3-A)")
check("Aconcagua" in ev.VERDADE["P1-008"]["sujeito"],
      "[39] YPF: o sujeito da falência é o terceiro")
check(ev.VERDADE["P1-010"]["vigencia"] == ["HISTORICAL"],
      "[40] TIM: a ocorrência é anterior; o artigo é follow-up")
check(ev.VERDADE["P1-005"]["evento_existe"] is False,
      "[41] B3: não há nova troca de CEO — só o descritor")
check("não controle societário" in (ev.VERDADE["P1-004"]["objeto"] or ""),
      "[42] BTG: a verdade é de OBJETO, dimensão que o contrato não representa")
check(ev.CRITICAS == ("sujeito", "papel", "terceiro", "vigencia"),
      "[43] as famílias críticas são sujeito, papel, terceiro e vigência")

print()
print("=" * 98)
print("BLOCO H — ATRIBUIÇÃO DE ERRO")
print("=" * 98)
_dim_ok = {d: {"aplicavel": True, "correto": True, "devolvido": "x"}
           for d in ev.DIMENSOES}
_dim_ruim = dict(_dim_ok)
_dim_ruim["papel"] = {"aplicavel": True, "correto": False, "devolvido": "BUYER"}
check(ev.classificar_erro("P1-003", _dim_ok, True,
                          {"pontuavel": False}, False) is None,
      "[44] acerto não gera atribuição de erro")
check(ev.classificar_erro("P1-003", _dim_ok, True,
                          {"pontuavel": True}, False) == ev.PROJECTION_ERROR,
      "[45] dimensões certas + veredito errado ⇒ erro da RÉGUA")
check(ev.classificar_erro("P1-003", _dim_ruim, True,
                          {"pontuavel": True}, False) == ev.MODEL_SEMANTIC_ERROR,
      "[46] dimensão crítica errada ⇒ erro do MODELO")
check(ev.classificar_erro("P1-003", _dim_ok, False,
                          {"pontuavel": True}, False) == ev.MODEL_EXTRACTION_ERROR,
      "[47] evidência inválida ⇒ erro de EXTRAÇÃO")
check(ev.classificar_erro("P1-004", _dim_ok, True,
                          {"pontuavel": True}, False) == ev.CONTRACT_GAP,
      "[48] caso que depende de objeto/escopo ⇒ LACUNA DE CONTRATO, não culpa "
      "do modelo")

print()
print("=" * 98)
print(f"RESULTADO PROJEÇÃO SEMÂNTICA: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
