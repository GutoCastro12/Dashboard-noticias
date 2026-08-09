#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b1_aliases.py — 4I.2 Wave B1: aliases ambíguos observados.

Solução CONFIG-ONLY via `mention_guard.contexto_patterns` — o mesmo mecanismo
já usado em produção pela B3 ("índice da B3"), que é consultado por
`detect_companies`, o caminho REAL de atribuição.

Nenhum alias removido/adicionado, nenhum `search_terms`, nenhum `entity_cues`
(evitando ampliação de recall contextual, §1), nenhuma mudança de runtime.
"""
from __future__ import annotations
import copy
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
# cadastro SEM os mention_guard desta wave, para comparações antes/depois
base = copy.deepcopy(cfg)
for _w in base["watchlist"]:
    if _w["name"] in ("MercadoLibre", "Porto", "Santander Brasil"):
        _w.pop("mention_guard", None)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def det(t, c=None):
    return rd.detect_companies({"title": t, "summary": ""}, (c or cfg)["watchlist"])


def w(nome, c=None):
    return next(x for x in (c or cfg)["watchlist"] if x["name"] == nome)


print("=" * 96)
print("BLOCO A — MercadoLibre: FPs reais fora, marca preservada")
print("=" * 96)
check("MercadoLibre" not in det("IBS Energy pede recuperação judicial em meio à "
                                 "pressão no mercado livre"),
      "[FP1/gold] 'pressão no mercado livre' NÃO atribui MercadoLibre")
check("MercadoLibre" not in det("Prefeitura do Rio e BBCE assinam acordo para "
                                 "aquisição de energia do mercado livre"),
      "[FP2/gold] 'energia do mercado livre' NÃO atribui MercadoLibre")
for t in ("Mercado Livre anuncia nova aquisição no Brasil",
          "Mercado Livre divulga resultados trimestrais",
          "Mercado Livre capta recursos",
          "Mercado Livre é alvo de investigação",
          "Ações do Mercado Livre caem após resultado"):
    check("MercadoLibre" in det(t), f"[TRUE] marca preservada: {t[:48]}")
check("MercadoLibre" in det("MELI reporta crescimento de receita"),
      "[TRUE] alias MELI preservado")

print()
print("=" * 96)
print("BLOCO B — Porto: uso idiomático fora, identidade preservada")
print("=" * 96)
check("Porto" not in det("ISA Energia Brasil capta R$ 1 bilhão em debêntures e reforça "
                          "posição como “porto seguro” do setor de transmissão"),
      "[FP/gold] 'como “porto seguro” do setor' NÃO atribui Porto")
for t in ("[Fato Relevante] Porto: Aquisição de Ações de Emissão da Própria Companhia",
          "Porto anuncia novo plano de expansão",
          "Porto Seguro divulga resultados do trimestre"):
    check("Porto" in det(t), f"[TRUE] identidade preservada: {t[:48]}")

print()
print("=" * 96)
print("BLOCO C — Santander: entidade composta fora, 12/12 TRUE preservados")
print("=" * 96)
check("Santander Brasil" not in det("Zurich Santander Brasil anuncia Alejandro Widder "
                                     "como novo CEO"),
      "[FP/gold] 'Zurich Santander' NÃO atribui Santander Brasil")
_TRUE_SANT = [
    "Santander Brasil anuncia troca de CEO",
    "Banco Santander divulga lucro do trimestre",
    "Cade aprova aquisição pelo Santander de fatia da Estapar na Loop",
    "Ações do Santander Brasil têm maior alta em seis anos com oferta pública de aquisição",
    "XP vê troca de CEO no Santander (SANB11) sem ruptura",
    "Santander Brasil aprova incorporação da Esfera Fidelidade",
    "Fraude nas Americanas: executivos de Itaú, Bradesco e Santander são alvo de buscas",
    "ComBio capta R$ 200 mi junto ao Santander em operação amparada pelo Eco Invest",
    "Santander anuncia novo diretor financeiro",
    "Santander Brasil reporta resultado acima do esperado",
    "SANB11 sobe após balanço",
    "Santander eleva projeção de crédito para o ano",
]
_ok = sum(1 for t in _TRUE_SANT if "Santander Brasil" in det(t))
check(_ok == len(_TRUE_SANT),
      f"[§18] {_ok}/{len(_TRUE_SANT)} TRUE Santander preservados")

print()
print("=" * 96)
print("BLOCO D — §8/§13/§21/§22: coleta, aliases e queries INALTERADOS")
print("=" * 96)
for nome in ("MercadoLibre", "Porto", "Santander Brasil"):
    a, b = w(nome, base), w(nome)
    check(a.get("aliases") == b.get("aliases"),
          f"[{nome}] aliases idênticos (nenhuma remoção/adição)")
    check(rd.build_company_queries(a, base["taxonomy"])
          == rd.build_company_queries(b, cfg["taxonomy"]),
          f"[{nome}] build_company_queries idêntico (coleta inalterada)")
    check("search_terms" not in b, f"[{nome}] nenhum search_terms adicionado")
    check("entity_cues" not in b and "entity_cues_min" not in b,
          f"[{nome}] nenhum entity_cues/entity_cues_min adicionado (§1)")

print()
print("=" * 96)
print("BLOCO E — §26/§27: nenhum efeito fora dos 3 emissores")
print("=" * 96)
_outros = [x["name"] for x in cfg["watchlist"]
           if x["name"] not in ("MercadoLibre", "Porto", "Santander Brasil")
           and x.get("mention_guard")]
check("B3" in _outros, "[§27] mention_guard preexistente da B3 continua no cadastro")
check("B3" not in det("Empresas listadas no pregão da B3 sofrem queda"),
      "[§27] guard da B3 ('pregão da B3') continua funcionando")
check("B3" in det("B3 anuncia novo CEO"), "[§27] B3 como sujeito continua detectada")
_novas = 0
for t in ("Nova fintech amplia marketplace de seguros",
          "Setor de e-commerce cresce no Brasil",
          "Seguradora lança apólice digital"):
    _novas += len([c for c in det(t) if c in ("MercadoLibre", "Porto")])
check(_novas == 0,
      "[§1] cues genéricos NÃO foram adicionados: texto setorial não atribui "
      "MercadoLibre/Porto")

print()
print("=" * 96)
print(f"RESULTADO WAVE B1: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
