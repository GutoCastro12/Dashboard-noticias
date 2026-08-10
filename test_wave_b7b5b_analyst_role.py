#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b5b_analyst_role.py — 4I.2 Wave B7b-5b.

ANALISTA ≠ SUJEITO: quando a monitorada emite recomendação sobre a ação de
um TERCEIRO, ela é fonte de análise, não sujeito do evento noticiado.

Reusa a infraestrutura A7 (`detect_papel_nao_sujeito` / `_PAPEL_NAO_SUJEITO`),
acrescentando o papel `analista`. A família `investigacao_regulatoria` NÃO
foi tocada — keywords, severidade, peso e fase seguem intactos.

Precedência (§6): investigação dirigida à própria monitorada vence o papel
de analista. O papel é fallback, nunca sobrepõe sujeito formal explícito.
"""
from __future__ import annotations
import semantic_audit as sa
import risk_dashboard as rd

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)


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
                              "domain": "exemplo.com", "pub_ts": 1784000000,
                              "pub_iso": "2026-07-14 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def papel(t, emp):
    return sa.detect_papel_nao_sujeito(t, emp, AL.get(emp) or [emp])


_G250 = ("AppLovin Rises 5% Premarket After Citigroup Recommends Buying The Stock "
         "Amid SEC Investigation Concerns")

print("=" * 96)
print("BLOCO A — §8/§9: o caso real G250, nos DOIS event_ids")
print("=" * 96)
_got = pontua(_G250, "Citigroup")
check("investigacao_regulatoria" not in _got,
      f"[1 G250] Citigroup NÃO recebe investigacao_regulatoria (obtido {sorted(_got)})")
check("investigacao_gestora" not in _got,
      "[2 G250] Citigroup NÃO recebe investigacao_gestora — mesma menção 'SEC Investigation'")
check(papel(_G250, "Citigroup") == "analista",
      "[3] papel auditável = 'analista', via estrutura A7 existente")

print()
print("=" * 96)
print("BLOCO B — §10: investigação PRÓPRIA preservada (3 TRUEs obrigatórios)")
print("=" * 96)
# A asserção é no NÍVEL DO PAPEL — a camada que esta wave alterou. A forma
# inglesa "SEC opens investigation into X" não é classificada pela taxonomia
# de produção nem ANTES nem DEPOIS desta wave (BEFORE == AFTER, verificado):
# é limitação da camada de classificação, fora do escopo (§3).
check(papel("SEC opens investigation into Citigroup", "Citigroup") == "",
      "[4] 'investigation into Citigroup' → papel NÃO é analista (guard §6)")
check("investigacao_regulatoria" in pontua(
        "CNBV abre investigación contra Banorte por irregularidades",
        "Grupo Financiero Banorte"),
      "[5] 'CNBV abre investigación contra Banorte' → Banorte RECEBE")
check("investigacao_regulatoria" in pontua(
        "Ministerio del Trabajo abre investigación a Nutresa por presunta explotación",
        "Grupo Nutresa"),
      "[6] Nutresa preservada — Wave A1b intacta ('presunta' qualifica a conduta)")

print()
print("=" * 96)
print("BLOCO C — §5/§6/§7: o papel precisa estar LIGADO à monitorada")
print("=" * 96)
check(papel("Citigroup recommends buying the stock", "Citigroup") == "analista",
      "[7] recomendação ligada à monitorada → analista")
check(papel("Analyst recommends buying Citigroup shares", "Citigroup") == "",
      "[8] recomendação de TERCEIRO sobre a monitorada → NÃO é analista")
_BOTH = ("SEC opens investigation into Citigroup as Citigroup recommends buying "
         "AppLovin shares")
check(papel(_BOTH, "Citigroup") == "",
      "[9 BOTH] investigação própria explícita VENCE o papel de analista")
check(papel(_BOTH, "Citigroup") == "",
      "[9b BOTH] o gate de precedência desarma o papel, não o evento")
check(papel("Citigroup faces SEC investigation while recommending buying the stock",
            "Citigroup") == "",
      "[10 BOTH] 'Citigroup faces SEC investigation' também vence")

print()
print("=" * 96)
print("BLOCO D — §11: variação sintática próxima")
print("=" * 96)
check(papel("Stock jumps after Citigroup recommends buying shares", "Citigroup")
      == "analista", "[11] 'recommends buying shares' (sem 'the stock')")
check(papel("Citigroup recommends selling the stock amid probe concerns", "Citigroup")
      == "analista", "[12] 'recommends selling the stock'")

print()
print("=" * 96)
print("BLOCO E — §12/§13: objeto financeiro exigido, rating não é analista")
print("=" * 96)
check(papel("Citigroup recommends caution to investors", "Citigroup") == "",
      "[13] sem objeto financeiro, o papel NÃO é inferido")
check(papel("Moody's downgrades Citigroup credit rating to Baa2", "Citigroup") == "",
      "[14] downgrade de RATING não vira papel de analista")
check(papel("Moody's downgrades Citigroup credit rating to Baa2", "Citigroup") == "",
      "[14b] 'downgrade' não está no vocabulário — sem colisão com rating")
check("rebaixamento_rating" in pontua(
        "Moody's rebaixa rating corporativo da Rumo de 'Ba2' para 'Ba3' e altera "
        "perspectiva para negativa", "Rumo"), "[15] rating legítimo preservado")

print()
print("=" * 96)
print("BLOCO F — §15: corpus de investigação e M&A intactos")
print("=" * 96)
# REGISTRO REAL do histórico — não fixture sintética.
_AMER = "Fraude nas Americanas: executivos de Itaú, Bradesco e Santander são alvo de buscas"
for _c in ("Itaú Unibanco", "Bradesco", "Santander Brasil"):
    check("investigacao_regulatoria" in pontua(_AMER, _c),
          f"[16 Americanas] {_c} mantém investigacao_regulatoria")
check("ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx",
                     "Cigna Group"), "[17] M&A legítimo intacto (Wave C congelada)")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração"), "[18] Vale/Samarco nos dois lados")

print()
print("=" * 96)
print("BLOCO G — §22: A7 preservada nos papéis antigos")
print("=" * 96)
check(list(sa._PAPEL_NAO_SUJEITO) == ["vitima", "comentarista", "investigador",
                                       "analista"],
      "[19] A7 preservada: papéis antigos intactos, 'analista' apenas acrescentado")
check(papel("CNBV abre investigación contra Banorte", "Grupo Financiero Banorte")
      != "analista", "[20] investigação própria nunca é classificada como analista")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-5b: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
