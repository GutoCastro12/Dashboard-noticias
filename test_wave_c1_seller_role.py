#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_c1_seller_role.py — 4I.2 Wave C1.

VENDEDORA ≠ ADQUIRENTE. Quem vende um ativo não faz uma aquisição; `ma` e
`follow_on` pertencem ao comprador/emissor. Guard transacional dedicado
(`detect_transaction_seller_role`), regra `R_MA_PAPEL_VENDEDOR`.

Exige a cadeia COMPLETA: outro comprador nomeado + verbo + objeto +
preposição de origem sobre a monitorada. `da <monitorada>` sozinho nunca
basta — "aquisição da Aegea" é aquisição FEITA pela Aegea (TRUE do gold).

Dois refinamentos, cada um ancorado num caso real (C1b):
  H1B4  objeto societário  → "aquisição de ações da Usiminas" = target (C3)
  H1B6  marcador `por`     → "por banco do BTG" = nome do comprador (G129)

Escopo: PT + EN. Famílias: `ma` e `follow_on` apenas.
NÃO cria event_id de desinvestimento — a lacuna segue aberta.
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
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-30 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def vend(t, emp):
    return sa.detect_transaction_seller_role(t, emp, AL.get(emp) or [emp])


_G141 = "Âmbar Energia conclui a aquisição de 4 hidrelétricas da Cemig em MG"
_G146 = "Mattos Filho assessora Energo-Pro na aquisição do CEBI da Copel"
_G147 = ("Spire completes acquisition of Tennessee Piedmont Natural Gas business "
         "from Duke Energy")
_G154 = "Taesa aprova aquisição de cinco transmissoras da Energisa por R$ 1,5 bi"
_G245 = "Eneva (ENEV3) anuncia compra de térmicas do BTG e follow on de até R$ 4,2 bilhões"
_G155 = "Transmissora Aliança de Energia Elétrica anuncia aquisição de ativos da Energisa"

print("=" * 96)
print("BLOCO A — §17: os 5 sellers conhecidos")
print("=" * 96)
for _t, _c, _ev in ((_G141, "Cemig", "ma"), (_G146, "Copel", "ma"),
                    (_G147, "Duke Energy", "ma"), (_G154, "Energisa", "ma"),
                    (_G245, "BTG Pactual", "follow_on")):
    check(_ev not in pontua(_t, _c), f"[seller] {_c} NÃO recebe `{_ev}`")
    check(vend(_t, _c) != "", f"[seller-evid] papel de vendedora detectado: {_c}")
check("Cemig" in rd.detect_companies({"title": _G141, "summary": ""}, cfg["watchlist"]),
      "[§17] a monitorada CONTINUA em companies — a notícia é dela")

print()
print("=" * 96)
print("BLOCO B — §18: G155 ×2 (seller correto, dedup segue para a Wave D)")
print("=" * 96)
check("ma" not in pontua(_G155, "Energisa"), "[G155] Energisa NÃO recebe `ma`")
check(vend(_G155, "Energisa") != "",
      "[G155b] papel de vendedora correto — role corrigida ≠ ocorrência deduplicada")

print()
print("=" * 96)
print("BLOCO C — §13/§14/§15: os três controles que NÃO podem disparar")
print("=" * 96)
_USI = "Ternium conclui aquisição de ações da Usiminas por US$ 315,2 milhões"
check(vend(_USI, "Usiminas") == "",
      "[§13 C3] 'ações da Usiminas' → objeto societário, é TARGET, não vendedora")
_BTG = "Justiça do Mato Grosso mantém aquisição de fazenda bilionária por banco do BTG"
check(vend(_BTG, "BTG Pactual") == "",
      "[§14 G129] 'por banco do BTG' → afiliação do COMPRADOR, não vendedora")
_AEG = ("Ouro urbano? Os planos por trás da aquisição bilionária da Aegea em "
        "resíduos sólidos")
check(vend(_AEG, "Aegea Saneamento") == "",
      "[§15 G124] 'aquisição da Aegea' = aquisição FEITA pela Aegea → não vendedora")

print()
print("=" * 96)
print("BLOCO D — §16: residuais S7 deliberadamente NÃO corrigidos (scope lock)")
print("=" * 96)
_G135 = "CCP approves Systems Limited’s acquisition of BAT SAA Services"
check(vend(_G135, "British American Tobacco") == "",
      "[G135 S7-A] nome da vendedora dentro do nome do ativo → fora do C1c, por decisão")
_G205 = "Mattos Filho e Demarest atuam em aquisição da Vale pela GIP na Aliança Energia"
check(vend(_G205, "Vale") == "",
      "[G205 S7-B] 'aquisição da Vale pela GIP' → fora do C1c, por decisão")

print()
print("=" * 96)
print("BLOCO E — §20/§21/§22/§23: sintéticos estruturais")
print("=" * 96)
check(vend("Empresa A conclui aquisição de usinas da Empresa B", "Empresa B") != "",
      "[PT] 'A adquire usinas da B' → B é vendedora")
check(vend("Company A completes acquisition of assets from Company B", "Company B") != "",
      "[EN] 'A acquires assets from B' → B é vendedora")
check(vend("Empresa A conclui aquisição de participação na Empresa B", "Empresa B") == "",
      "[§22 H1B4] objeto societário → não vendedora")
check(vend("Company A completes acquisition of shares from Company B", "Company B") == "",
      "[§22 H1B4 EN] 'shares' → não vendedora")
check(vend("Aquisição de ativo por banco da Empresa B", "Empresa B") == "",
      "[§23 H1B6] marcador `por` → não vendedora")
check(vend("Aegea anuncia aquisição de operação de resíduos", "Aegea Saneamento") == "",
      "[§11] sem OUTRO comprador nomeado + origem, o guard não atua")

print()
print("=" * 96)
print("BLOCO F — §21/§24: M&A legítimo do comprador sobrevive")
print("=" * 96)
check("ma" in pontua("BTG conclui aquisição do HSBC Uruguai e inicia operações no país",
                     "BTG Pactual"), "[TRUE] comprador mantém `ma`")
check("ma" in pontua("Cade aprova fusão entre Marfrig e BRF que cria gigante do setor",
                     "BRF"), "[TRUE] fusão mantém `ma`")
check("follow_on" in pontua("Itaúsa anuncia follow-on de R$ 5 bilhões", "Itaúsa"),
      "[TRUE] emissor mantém `follow_on`")

print()
print("=" * 96)
print("BLOCO G — regressões das waves anteriores")
print("=" * 96)
check("ma" not in pontua(
        "Grécia investirá 600 milhões de euros na aquisição dos três Embraer "
        "KC-390 Millennium", "Embraer"), "[C2] Embraer/KC-390 preservado")
check("investigacao_regulatoria" not in pontua(
        "CVM abre processo administrativo contra ex-presidente do conselho da Vale, "
        "diz jornal", "Vale"), "[B8] Vale/Stieler preservado")
check("follow_on" not in pontua(
        "Aegea aprova aumento de capital e Itaúsa (ITSA4) pode aportar até "
        "R$ 1,5 bilhão", "Itaúsa"), "[B7b-2] Itaúsa/Aegea preservado")

print()
print("=" * 96)
print(f"RESULTADO WAVE C1: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
