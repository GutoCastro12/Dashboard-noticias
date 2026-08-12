#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp8_falencia_especulativa.py — 4I.2 R7c-P8.

ESTAR À BEIRA DA FALÊNCIA É, LITERALMENTE, NÃO TER FALIDO.

Caso adjudicado por humano como FALSE_POSITIVE: "Cosan (CSAN3) à beira da
falência? Será que conseguirá se recuperar?" pontuava `falencia` CRÍTICA para a
Cosan. Nenhuma regra semântica atuou — o evento passou por ausência de blocker,
que é o padrão que a R7a nomeou: `keyword + no blocker ≠ confirmed event`.

A correção NÃO é uma regra nova. O helper certo já existia:
`detect_evento_nao_consumado`, criado na Wave B6 para "riesgo de impago", já
tem exatamente a forma necessária — está escopado a insolvência, roda como
último recurso e CEDE diante de devedor corporativo explícito, que é o gate de
evidência positiva. Faltava-lhe apenas o vocabulário de PROXIMIDADE e HIPÓTESE.
Por isso esta wave é G3, não G1/G2: a família já era coberta, o léxico é que
estava incompleto.

O QUE ESTA REGRA NÃO É: uma regra de ponto de interrogação. O que bloqueia é o
MODALIZADOR ADJACENTE ao termo do evento. Por isso "X pediu falência? Documento
judicial confirma protocolo" continua pontuando — teste [17] fixa isso, e é a
diferença entre ler a manchete e ler a pontuação dela.
"""
from __future__ import annotations

import io

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
KWS = sa._keywords_por_evento(cfg)
AL = sa._aliases_map(cfg)
REGRA = "R_EVENTO_NAO_CONSUMADO_OU_DE_CARTEIRA"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def dec(t, emp, ev="falencia", resumo=""):
    r = sa.resolve_article_semantics(t, resumo, emp, [ev], AL, article_year=2026,
                                     source_domain="ex.com",
                                     keywords_por_evento=KWS, country="")
    d = r["decisoes"][0]
    return bool(d["scoreable"]), (d.get("attribution_rule") or ""), d


EXATO = "Cosan (CSAN3) à beira da falência? Será que conseguirá se recuperar?"

print("=" * 98)
print("BLOCO A — regressão exata do caso adjudicado")
print("=" * 98)
_s, _r, _d = dec(EXATO, "Cosan")
check(_s is False, f"[1] `falencia` deixa de pontuar para a Cosan ({_s})")
check(_r == REGRA, f"[2] pelo helper de evento não consumado ({_r})")
check(_d.get("event_scope") == "indireto", "[3] marcado como indireto")
check(_d.get("relation_type") == "risco_prospectivo",
      f"[4] com relação declarada ({_d.get('relation_type')})")
check("beira" in (_d.get("rejection_reason") or "").lower(),
      f"[5] a evidência textual é registrada no motivo")

print()
print("=" * 98)
print("BLOCO B — siblings de especulação (§15)")
print("=" * 98)
_NEG = [
    ("N1", "Vale está à beira da falência?", "Vale", "falencia"),
    ("N3", "Será o fim da Vale? Mercado discute risco de falência", "Vale",
     "falencia"),
    ("N4", "Vale corre risco de falência, dizem analistas", "Vale", "falencia"),
    ("N5", "Falência à vista para a Vale", "Vale", "falencia"),
    ("N6", "Vale conseguiria sobreviver a uma eventual falência?", "Vale",
     "falencia"),
    ("N10", "Petrobras às portas da falência, segundo relatório", "Petrobras",
     "falencia"),
    ("N11", "Vale caminha para a falência, alertam credores", "Vale", "falencia"),
    ("N12", "Falência iminente da Vale preocupa o mercado", "Vale", "falencia"),
]
for tag, t, emp, ev in _NEG:
    s, r, _ = dec(t, emp, ev)
    check(s is False, f"[6..13] {tag}: especulação não confirma o evento ({r or '-'})")

print()
print("=" * 98)
print("BLOCO C — positivos preservados (§17)")
print("=" * 98)
_POS = [
    ("P1", "Vale pede falência"),
    ("P2", "Justiça decreta falência da Vale"),
    ("P3", "Vale tem falência decretada"),
    ("P4", "Pedido de falência da Vale é protocolado"),
    ("P5", "Tribunal confirma falência da Vale"),
    ("P6", "Vale pediu falência? Documento judicial confirma protocolo"),
]
for tag, t in _POS:
    s, r, _ = dec(t, "Vale")
    check(s is True, f"[14..19] {tag}: continua pontuando ({r or 'sem blocker'})")

print()
print("=" * 98)
print("BLOCO D — NÃO é regra de ponto de interrogação (§9)")
print("=" * 98)
check(dec("Vale pediu falência? Documento judicial confirma protocolo",
          "Vale")[0] is True,
      "[20] manchete interrogativa COM evidência positiva continua pontuando")
check(dec("Vale à beira da falência", "Vale")[0] is False,
      "[21] e sem `?` a especulação continua bloqueada — o gatilho é o modalizador")
_src = io.open("semantic_audit.py", encoding="utf-8").read()
_bloco = _src.split("MODALIZADOR_PROSPECTIVO = [")[1].split("]")[0]
check("?" not in _bloco and r"\?" not in _bloco,
      "[22] nenhum padrão do modalizador depende de interrogação")

print()
print("=" * 98)
print("BLOCO E — terceiros e setor seguem governados pelas regras próprias (§16)")
print("=" * 98)
_TER = [
    ("N7", "Falência de clientes pode pressionar a Vale", "Vale", "falencia"),
    ("N8", "Vale analisa risco de falência no setor", "Vale", "falencia"),
    ("N9", "Mercado teme falências entre fornecedores da Vale", "Vale",
     "falencia"),
    ("RJ1", "Banco do Brasil (BBAS3) em alerta: Pedidos de recuperação "
            "judicial no agronegócio saltam 22%", "Banco do Brasil",
     "recuperacao_judicial"),
]
for tag, t, emp, ev in _TER:
    s, r, _ = dec(t, emp, ev)
    check(s is False, f"[23..26] {tag}: não herda o evento ({r or '-'})")
check(dec("Vale pede recuperação judicial", "Vale",
          "recuperacao_judicial")[0] is True,
      "[27] e a RJ verdadeira da própria empresa segue pontuando")

print()
print("=" * 98)
print("BLOCO F — negação e precedência (§18/§21)")
print("=" * 98)
check(dec("Vale nega risco de falência", "Vale")[1] == "R_NEGACAO_EXPLICITA",
      "[28] negação explícita mantém sua precedência")
check(dec("Analistas descartam falência da Vale", "Vale")[1]
      == "R_NEGACAO_EXPLICITA",
      "[29] idem para descarte")
check(dec("Vale estaria próxima da falência, segundo fonte não confirmada",
          "Vale")[0] is False,
      "[30] fonte não confirmada + proximidade não pontua")

print()
print("=" * 98)
print("BLOCO G — nada do caso concreto está embutido (§8)")
print("=" * 98)
_mod = _bloco + _src.split("MODALIZADOR_PROSPECTIVO_POSFIXO = [")[1].split("]")[0]
_codigo = "\n".join(l.split("#")[0] for l in _mod.splitlines())
for termo in ("Cosan", "CSAN3", "investing", "analysis", "2026"):
    check(termo.lower() not in _codigo.lower(),
          f"[31..35] nenhum hard-code de {termo!r}")

print()
print("=" * 98)
print("BLOCO H — o helper reusado, não uma regra nova (§25 G3)")
print("=" * 98)
check(hasattr(sa, "detect_evento_nao_consumado"),
      "[36] o helper já existia — a wave estendeu vocabulário")
check(hasattr(sa, "MODALIZADOR_PROSPECTIVO_POSFIXO"),
      "[37] marcador posposto tem lista própria")
check(any("beira" in p for p in sa.MODALIZADOR_PROSPECTIVO),
      "[38] proximidade entrou no léxico prospectivo")
_nc = sa.detect_evento_nao_consumado(EXATO, KWS.get("falencia", []), "Cosan",
                                     ["Cosan"])
check(_nc["nao_consumado"] and _nc["motivo"] == "risco_prospectivo",
      f"[39] o helper isolado reconhece o caso ({_nc.get('motivo')})")
check(not sa.detect_evento_nao_consumado(
    "Vale pede falência", KWS.get("falencia", []), "Vale",
    ["Vale"])["nao_consumado"],
    "[40] e não atua quando o evento é afirmado")
import reliability_taxonomy_inventory as inv  # noqa: E402
check(REGRA in inv.REGRAS,
      "[41] a regra já consta do mapa dimensional da R7a — nada a registrar")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-P8 (falência especulativa): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
