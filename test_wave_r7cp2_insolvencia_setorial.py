#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp2_insolvencia_setorial.py — 4I.2 R7c-P2.

UMA EMPRESA CITADA NUMA NOTÍCIA SOBRE RECUPERAÇÕES JUDICIAIS ALHEIAS NÃO ESTÁ
EM RECUPERAÇÃO JUDICIAL.

Caso adjudicado por humano (FALSE_POSITIVE): "Banco do Brasil (BBAS3) em
alerta: Pedidos de recuperação judicial no agronegócio saltam 22%, aponta
Serasa" pontuava `recuperacao_judicial` para o banco. A recuperação é do setor;
o banco é o credor exposto.

O defeito não era do título. Era o padrão que a R7a nomeou: a palavra vira
candidato e o evento pontua por AUSÊNCIA de blocker. `detect_debtor_subject`
devolvia vazio tanto aqui quanto em "X pede recuperação judicial" — nada
distinguia os dois. A correção exige EVIDÊNCIA POSITIVA de que a monitorada é
a devedora.

Duas fronteiras que o blast obrigou a fixar:

1. PRESERVAR A FAMÍLIA. Cinco construções positivas continuam pontuando; o
   objetivo é exigir sujeito, não apagar insolvência.
2. O BALDE DE MERCADO NÃO É EMISSOR. Na primeira versão, 24 dos 25 pares
   alterados eram "Mercado (geral)" — inclusive "Hughes pede recuperação
   judicial", evento real de terceiro que é exatamente o que aquele
   agrupamento existe para mostrar. Aplicar ali esvaziaria o feed.

Nenhum nome próprio do caso é conhecido pela regra: nem banco, nem
agronegócio, nem veículo, nem ticker.
"""
from __future__ import annotations

import io

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
KWS = sa._keywords_por_evento(cfg)
AL = sa._aliases_map(cfg)
REGRA = "R_INSOLVENCIA_SETORIAL_OU_DE_TERCEIRO"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def dec(t, emp, ev="recuperacao_judicial", resumo=""):
    r = sa.resolve_article_semantics(t, resumo, emp, [ev], AL,
                                     article_year=2026, source_domain="ex.com",
                                     keywords_por_evento=KWS, country="")
    d = r["decisoes"][0]
    return bool(d["scoreable"]), (d.get("attribution_rule") or ""), d


EXATO = ("Banco do Brasil (BBAS3) em alerta: Pedidos de recuperação judicial "
         "no agronegócio saltam 22%, aponta Serasa")

print("=" * 98)
print("BLOCO A — regressão exata do caso adjudicado")
print("=" * 98)
_s, _r, _d = dec(EXATO, "Banco do Brasil")
check(_s is False, f"[1] o evento deixa de pontuar para o Banco do Brasil ({_s})")
check(_r == REGRA, f"[2] pela regra de insolvência setorial ({_r})")
check(_d.get("event_scope") == "indireto",
      "[3] e é marcado como evento indireto")
check(_d.get("relation_type") == "setorial_ou_terceiro",
      f"[4] com relação declarada ({_d.get('relation_type')})")
check(_d.get("rejection_reason") and "exposta" in _d["rejection_reason"],
      "[5] o motivo da rejeição nomeia o papel da monitorada")
check(_d.get("subject_evidence"),
      f"[6] a evidência do sujeito coletivo é registrada "
      f"({str(_d.get('subject_evidence'))[:40]!r})")

print()
print("=" * 98)
print("BLOCO B — siblings negativos: sujeito setorial ou de terceiro")
print("=" * 98)
_NEG = [
    ("N1", "Banco do Brasil em alerta: pedidos de recuperação judicial no "
           "setor agrícola disparam", "Banco do Brasil", "recuperacao_judicial"),
    ("N2", "Recuperações judiciais no varejo crescem e preocupam Itaú Unibanco",
     "Itaú Unibanco", "recuperacao_judicial"),
    ("N3", "Banco do Brasil aumenta provisões após recuperação judicial de "
           "clientes", "Banco do Brasil", "recuperacao_judicial"),
    ("N4", "Vale sofre impacto da recuperação judicial de fornecedor Alfa",
     "Vale", "recuperacao_judicial"),
    ("N5", "Pedidos de recuperação judicial de produtores aumentam 20%, "
           "aponta Serasa e afeta Banco do Brasil", "Banco do Brasil",
     "recuperacao_judicial"),
    ("N6", "Falências no comércio crescem 15%, diz Serasa; Itaú Unibanco "
           "monitora", "Itaú Unibanco", "falencia"),
]
for tag, t, emp, ev in _NEG:
    s, r, _ = dec(t, emp, ev)
    check(s is False, f"[7..12] {tag}: {emp} não está em insolvência ({r or '-'})")

print()
print("=" * 98)
print("BLOCO C — positivos preservados: a monitorada É a devedora")
print("=" * 98)
_POS = [
    ("P1", "Banco do Brasil pede recuperação judicial", "Banco do Brasil",
     "recuperacao_judicial"),
    ("P2", "Justiça aceita pedido de recuperação judicial da Vale", "Vale",
     "recuperacao_judicial"),
    ("P3", "Vale entra em recuperação judicial", "Vale", "recuperacao_judicial"),
    ("P4", "Plano de recuperação judicial da Vale é aprovado", "Vale",
     "recuperacao_judicial"),
    ("P5", "Credores aprovam plano de recuperação judicial da Vale", "Vale",
     "recuperacao_judicial"),
    ("P6", "Americanas pede falência após rombo bilionário", "Americanas",
     "falencia"),
]
for tag, t, emp, ev in _POS:
    s, r, _ = dec(t, emp, ev)
    check(s is True, f"[13..18] {tag}: continua pontuando ({r or 'sem blocker'})")

print()
print("=" * 98)
print("BLOCO D — near-negatives: keyword não decide sujeito")
print("=" * 98)
_AMB = [
    ("A1", "Vale e recuperação judicial no setor de mineração"),
    ("A2", "Vale analisa recuperações judiciais"),
    ("A3", "Vale negocia créditos contra companhia em recuperação judicial"),
    ("A4", "Vale compra ativos de companhia em recuperação judicial"),
]
for tag, t in _AMB:
    s, r, _ = dec(t, "Vale")
    check(s is False, f"[19..22] {tag}: sem evidência de sujeito, não pontua")

print()
print("=" * 98)
print("BLOCO E — nada do caso concreto está embutido na regra")
print("=" * 98)
_src = io.open("semantic_audit.py", encoding="utf-8").read()
_bloco = _src.split("def detect_insolvencia_setorial")[1].split("\ndef ")[0]
_codigo = "\n".join(l.split("#")[0] for l in _bloco.splitlines())
for termo in ("Banco do Brasil", "BBAS", "Serasa", "Money Times", "Itaú"):
    check(termo not in _codigo and termo.lower() not in _codigo.lower(),
          f"[23..27] nenhum hard-code de {termo!r}")
_pats = _src.split("_INSOLV_SUJEITO_COLETIVO = [")[1].split(
    "_INSOLV_PAPEL_EXPOSTO")[0]
check("agroneg" in _pats,
      "[28] 'agronegócio' aparece só como UM setor entre vários, não como o caso")
check(all(x in _pats for x in ("varejo", "constru", "ind[úu]stria")),
      "[29] a lista de setores é genérica")

print()
print("=" * 98)
print("BLOCO F — o balde de mercado não é emissor")
print("=" * 98)
check(rd.MARKET_LABEL.lower() in sa._NAO_EMISSOR,
      f"[30] {rd.MARKET_LABEL!r} está fora do escopo da regra")
_s2, _r2, _ = dec("Hughes pede recuperação judicial nos Estados Unidos",
                  rd.MARKET_LABEL)
check(_r2 != REGRA,
      f"[31] evento real de terceiro segue no feed de mercado ({_r2 or '-'})")
_s3, _r3, _ = dec("Pedidos de recuperação judicial batem recorde no trimestre",
                  rd.MARKET_LABEL)
check(_r3 != REGRA, "[32] notícia setorial de mercado não é bloqueada por esta regra")

print()
print("=" * 98)
print("BLOCO G — o detector isolado")
print("=" * 98)
check(sa.detect_insolvencia_setorial(EXATO, "Banco do Brasil",
                                     ["Banco do Brasil", "BBAS3"]),
      "[33] detecta o caso adjudicado")
check(not sa.detect_insolvencia_setorial("Vale pede recuperação judicial",
                                         "Vale", ["Vale"]),
      "[34] não atua quando a monitorada é a devedora")
check(not sa.detect_insolvencia_setorial(
    "Vale anuncia aquisição da Alfa", "Vale", ["Vale"]),
    "[35] não atua sem insolvência no texto")
_r5 = sa.detect_insolvencia_setorial(EXATO, "Banco do Brasil", ["Banco do Brasil"])
check(_r5.get("papel_monitorada"),
      f"[36] o papel da monitorada é registrado ({_r5.get('papel_monitorada')!r})")
check("recupera" in (_r5.get("sujeito_coletivo") or "").lower(),
      f"[37] o sujeito coletivo é nomeado ({_r5.get('sujeito_coletivo')!r})")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-P2 (insolvência setorial): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
