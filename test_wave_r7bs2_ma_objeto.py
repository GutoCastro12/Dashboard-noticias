#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7bs2_ma_objeto.py — 4I.2 R7b-S2.

AQUISIÇÃO NÃO É AUTOMATICAMENTE M&A.

Gustavo adjudicou três controles S2 em 2026-08-12. Os três têm o MESMO papel
transacional — a monitorada é a adquirente, e nisso o runtime já acertava. O
que separa um do outro é o OBJETO:

    Suzano   → participação societária em joint venture (51%)  → `ma` TRUE
    BTG      → fazenda                                         → `ma` FALSE
    Petrobras→ bloco exploratório                              → `ma` FALSE

Por isso a wave é G3 e não G1/G2: o mapa de objetos (`OBJ_NAO_EMPRESA`) e a
rota de rejeição (`R_MA_OBJETO_ESCOPO` → `aquisicao_capex`, direto e não
pontuável) JÁ EXISTIAM e já governavam aeronaves, imóvel e equipamento.
Faltava-lhes o léxico de PROPRIEDADE RURAL e de DIREITO/CONCESSÃO — e faltava
provar o objeto societário em vez de deduzi-lo de uma maiúscula qualquer depois
do verbo, que era o que fazia "São Tomé e Príncipe" valer como nome de empresa.

O QUE ESTA REGRA NÃO É: uma regra que rejeita aquisição parcial. 51% continua
M&A, e os testes [22..28] fixam a escala inteira de 10% a 100%.

COLISÃO REGISTRADA, NÃO RESOLVIDA: o gold da auditoria 4I marca
"Petrobras … aquisição de bloco exploratório na África" como positivo `ma` que
não pode sumir. A verdade humana desta wave diz o contrário sobre a mesma
classe semântica. O teste [41] documenta a colisão em vez de escondê-la;
nenhuma label do gold foi alterada aqui.
"""
from __future__ import annotations

import io
import json

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
KWS = sa._keywords_por_evento(cfg)
AL = sa._aliases_map(cfg)


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def dec(t, emp, ev="ma"):
    r = sa.resolve_article_semantics(t, "", emp, [ev], AL, article_year=2026,
                                     source_domain="ex.com",
                                     keywords_por_evento=KWS, country="")
    d = r["decisoes"][0]
    return bool(d["scoreable"]), d


def obj(t):
    return sa.detect_transaction(t)["transaction_object"]


E1 = "Justiça do Mato Grosso mantém aquisição de fazenda bilionária por banco do BTG"
E2 = "Suzano conclui aquisição de joint venture da Kimberly-Clark por US$ 1,3 bi"
E3 = "Petrobras conclui aquisição de bloco exploratório em São Tomé e Príncipe"

print("=" * 98)
print("BLOCO A — regressões exatas dos três controles adjudicados")
print("=" * 98)
_s1, _d1 = dec(E1, "BTG Pactual")
check(_s1 is False, f"[1] E1 BTG: fazenda não pontua como `ma` ({_s1})")
check(_d1.get("attribution_rule") == "R_MA_OBJETO_ESCOPO",
      f"[2] E1 pela rota de objeto ({_d1.get('attribution_rule')})")
check(obj(E1) == "imovel_rural", f"[3] E1 objeto é imóvel rural ({obj(E1)})")
check(_d1.get("event_id_corrigido") == "aquisicao_capex",
      f"[4] E1 informação preservada como aquisicao_capex "
      f"({_d1.get('event_id_corrigido')})")
check(_d1.get("event_scope") != "indireto",
      "[5] E1 continua evento DIRETO da monitorada — não vira contexto de terceiro")

_s2, _d2 = dec(E2, "Suzano")
check(_s2 is True, f"[6] E2 Suzano: participação societária pontua ({_s2})")
check(obj(E2) == "empresa", f"[7] E2 objeto societário PROVADO ({obj(E2)})")
check(_d2.get("attribution_rule") == "R_MA_LEGITIMO",
      f"[8] E2 por M&A legítimo ({_d2.get('attribution_rule')})")

_s3, _d3 = dec(E3, "Petrobras")
check(_s3 is False, f"[9] E3 Petrobras: bloco exploratório não pontua ({_s3})")
check(obj(E3) == "direito_exploratorio",
      f"[10] E3 objeto é direito exploratório ({obj(E3)})")
check(_d3.get("event_id_corrigido") == "aquisicao_capex",
      "[11] E3 informação preservada, não apagada")

print()
print("=" * 98)
print("BLOCO B — positivos: M&A societário não pode ser bloqueado (§16)")
print("=" * 98)
_POS = [
    ("P1", "Vale adquire 100% da Empresa Alfa"),
    ("P2", "Vale compra 51% da Empresa Alfa"),
    ("P3", "Vale adquire participação de 30% na Empresa Alfa"),
    ("P4", "Vale assume controle da Empresa Alfa"),
    ("P5", "Vale conclui aquisição de participação em joint venture"),
    ("P6", "Vale compra participação societária da Empresa Alfa"),
]
for tag, t in _POS:
    s, d = dec(t, "Vale")
    check(s is True, f"[12..17] {tag}: continua pontuando "
                     f"({d.get('attribution_rule') or d.get('rejection_reason')})")

print()
print("=" * 98)
print("BLOCO C — negativos: aquisição de ativo/direito (§17)")
print("=" * 98)
_NEG = [
    ("N1", "Vale adquire uma fazenda", "imovel_rural"),
    ("N2", "Vale compra imóvel comercial", "imovel"),
    ("N3", "Vale adquire bloco exploratório", "direito_exploratorio"),
    ("N4", "Vale compra concessão rodoviária", "direito_exploratorio"),
    ("N5", "Vale compra máquinas da Empresa Alfa", "equipamento"),
    ("N6", "Vale adquire terreno para nova fábrica", "imovel"),
    ("N7", "Vale adquire direitos de exploração", "direito_exploratorio"),
    ("N8", "Vale compra carteira de créditos", None),
]
for tag, t, esperado in _NEG:
    s, d = dec(t, "Vale")
    check(s is False, f"[18..25] {tag}: não é M&A societário ({obj(t)})")
    if esperado:
        check(obj(t) == esperado,
              f"[18..25] {tag}: objeto identificado como {esperado} ({obj(t)})")

print()
print("=" * 98)
print("BLOCO D — participação parcial continua M&A (§20)")
print("=" * 98)
for pct in (10, 20, 30, 49, 51, 80, 100):
    t = f"Vale adquire participação de {pct}% na Empresa Alfa"
    s, _ = dec(t, "Vale")
    check(s is True, f"[26..32] {pct}%: participação parcial não deixa de ser M&A")

print()
print("=" * 98)
print("BLOCO E — papel ≠ objeto; vendedor/alvo preservados (§6/§19)")
print("=" * 98)
check(dec("Empresa Alfa vende ativo para a Vale", "Empresa Alfa")[0] is False,
      "[33] a vendedora não faz aquisição")
check(dec("Ternium conclui aquisição de ações da Usiminas", "Usiminas")[0] is True,
      "[34] alvo cujo capital muda de mãos continua reconhecido")
# o defeito que o blast pegou: `controlada de X` descreve QUEM COMPRA
_cemig = ("Controlada da Cemig (CMIG4) conclui aquisição de usinas fotovoltaicas "
          "por R$ 52,8 mi")
check(dec(_cemig, "Cemig")[0] is False,
      f"[35] `controlada de X` é o comprador, não o objeto ({obj(_cemig)})")
check(obj(_cemig) == "capex_ativo",
      f"[36] e o objeto real da compra é a usina ({obj(_cemig)})")

print()
print("=" * 98)
print("BLOCO F — nada do caso concreto está embutido (§19/§20/§21 do brief)")
print("=" * 98)
_src = io.open("semantic_audit.py", encoding="utf-8").read()
_bloco = (_src.split("OBJ_NAO_EMPRESA = {")[1].split("\nOBJ_SOCIETARIO_FORTE")[0]
          + _src.split("OBJ_SOCIETARIO_FORTE = [")[1].split("]")[0])
_codigo = "\n".join(l.split("#")[0] for l in _bloco.splitlines())
for termo in ("BTG", "Suzano", "Petrobras", "Kimberly", "Mato Grosso",
              "agfeed", "Tomé", "Cemig"):
    check(termo.lower() not in _codigo.lower(),
          f"[37..44] nenhum hard-code de {termo!r}")

print()
print("=" * 98)
print("BLOCO G — helper reusado e dimensão instrumentada (§5/§27)")
print("=" * 98)
check(hasattr(sa, "detect_transaction") and hasattr(sa, "ma_is_legitimate"),
      "[45] os helpers de objeto já existiam — a wave estendeu vocabulário")
check(hasattr(sa, "OBJ_SOCIETARIO_FORTE"),
      "[46] evidência societária positiva tem lista própria")
check(hasattr(sa, "_societario_apos_verbo"),
      "[47] a evidência societária é lida DEPOIS do verbo — objeto, não sujeito")
check("subsidi" not in " ".join(sa.OBJ_SOCIETARIO_FORTE),
      "[48] relação entre empresas (subsidiária/controlada) não vale como objeto")
import reliability_taxonomy_inventory as inv  # noqa: E402
import reliability_universal_assessment as uea  # noqa: E402
check("transaction_object" in inv.DIMENSOES,
      "[49] R7a reconhece a dimensão do objeto da transação")
check("transaction_object" in inv.REGRAS["R_MA_OBJETO_ESCOPO"][0],
      "[50] e a regra de objeto declara que a estabelece")
_u_ma = uea.montar(dec(E2, "Suzano")[1], identity="x", texto=E2)
_u_rat = uea.montar(
    sa.resolve_article_semantics("Fitch rebaixa rating da Vale", "", "Vale",
                                 ["rebaixamento_rating"], AL, article_year=2026,
                                 source_domain="e.com", keywords_por_evento=KWS,
                                 country="")["decisoes"][0],
    identity="y", texto="Fitch rebaixa rating da Vale")
check("transaction_object" in _u_ma.dims(),
      "[51] a dimensão vale para a família M&A")
check("transaction_object" not in _u_rat.dims(),
      "[52] e NÃO para quem não compra nada — rebaixamento não tem objeto")
_u_amb = uea.montar(dec("Vale adquire unidade da Empresa Alfa", "Vale")[1],
                    identity="z", texto="Vale adquire unidade da Empresa Alfa")
check(_u_amb.transaction_object.status == uea.DEFAULTED,
      f"[53] caso ambíguo pontua com objeto NÃO PROVADO — a falsa completude "
      f"da família `ma` deixa de ser invisível "
      f"({_u_amb.transaction_object.status})")

print()
print("=" * 98)
print("BLOCO H — verdade humana registrada e colisão declarada (§10/§24)")
print("=" * 98)
_gt = json.load(io.open("test_fixtures_reliability/ma_transaction_reviews.json",
                        encoding="utf-8"))
_itens = {k: v for k, v in _gt.items() if k != "_meta"}
check(len(_itens) == 3, f"[54] os três ground truths S2 estão registrados ({len(_itens)})")
check(all(v["reviewer_type"] == "human" for v in _itens.values()),
      "[55] todos marcados como adjudicação humana")
check({v["company"]: v["scoreable_as_ma"] for v in _itens.values()}
      == {"BTG Pactual": False, "Suzano": True, "Petrobras": False},
      "[56] e os vereditos batem com o brief")
check(all(v.get("transaction_role") == "adquirente" for v in _itens.values()),
      "[57] nos três o PAPEL é o mesmo — o que difere é o objeto")
check("nota_de_colisao" in _gt["_meta"],
      "[58] a colisão com o gold 4I está declarada, não escondida")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7b-S2 (objeto da transação em M&A): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
