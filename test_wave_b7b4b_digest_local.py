#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b7b4b_digest_local.py — 4I.2 Wave B7b-4b.

DIGEST MULTIEMPRESA: num digest de mercado, o evento pertence ao ITEM em
que aparece. Se a monitorada está em outro item e o evento não aparece em
NENHUM item dela, ela não herda o evento.

Delimitador: SOMENTE ';'. ':' NÃO divide (fixado pelo BLOCO D — protege
contra generalização ingênua futura). '|' não foi adicionado: nenhum caso
real observado depende dele.

Gate de ATRIBUIÇÃO, precedência mínima: roda depois de todas as regras
semânticas, então B7b-1/B7b-2/B2/B4/B5 continuam decidindo antes.
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
                              "domain": "exemplo.com", "pub_ts": 1785000000,
                              "pub_iso": "2026-07-20 10:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def dono(title, emp, ev="troca_ceo"):
    return sa.detect_evento_de_outro_item(title, emp, KW.get(ev) or [], AL)


_FP = ("Ibovespa hoje: Usiminas (USIM5) lidera altas; B3 (B3SA3) cai quase 5% após "
       "anúncio de novo CEO")

print("=" * 96)
print("BLOCO A — §9: o FP real, nos dois lados")
print("=" * 96)
check("troca_ceo" not in pontua(_FP, "Usiminas"),
      "[1 FP real] Usiminas NÃO recebe troca_ceo — o evento é do item da B3")
check(dono(_FP, "Usiminas") == "B3",
      f"[1b] dono do item identificado = B3 (obtido {dono(_FP, 'Usiminas')!r})")
check("troca_ceo" in pontua(_FP, "B3"),
      "[2] B3 — dona do item — MANTÉM troca_ceo no mesmo artigo")
check(dono(_FP, "B3") == "",
      "[2b] para a B3 o gate não dispara: o evento está no item dela")

print()
print("=" * 96)
print("BLOCO B — §10/§11/§12: TRUEs obrigatórios")
print("=" * 96)
check("troca_ceo" in pontua("Usiminas anuncia novo CEO e ações lideram altas", "Usiminas"),
      "[3 mesmo item] sem ';', comportamento anterior: Usiminas recebe")
_DOIS = "Usiminas anuncia novo CEO; B3 também anuncia novo CEO"
check("troca_ceo" in pontua(_DOIS, "Usiminas"),
      "[4 dois CEOs] Usiminas recebe — cada item tem o seu")
check("troca_ceo" in pontua(_DOIS, "B3"),
      "[4b dois CEOs] B3 também recebe — a regra não elege uma empresa por artigo")
_DIF = "Usiminas divulga resultado; B3 cai após anúncio de novo CEO"
check("troca_ceo" not in pontua(_DIF, "Usiminas"),
      "[5 eventos diferentes] troca_ceo não vaza para a Usiminas")
check("troca_ceo" in pontua(_DIF, "B3"),
      "[5b eventos diferentes] B3 mantém troca_ceo")

print()
print("=" * 96)
print("BLOCO C — §14: header editorial não atrapalha o ';'")
print("=" * 96)
_HDR = "Ibovespa hoje: Usiminas sobe; B3 cai após anúncio de novo CEO"
check("troca_ceo" not in pontua(_HDR, "Usiminas"),
      "[6] 'Ibovespa hoje:' não impede o ';' de separar os itens")
check("troca_ceo" in pontua(_HDR, "B3"), "[6b] B3 preservada no mesmo título")

print()
print("=" * 96)
print("BLOCO D — §4/§13: ':' NÃO é splitter (proteção permanente)")
print("=" * 96)
check("troca_ceo" in pontua("Vale: companhia anuncia novo CEO", "Vale"),
      "[7] 'Vale: companhia anuncia novo CEO' → Vale RECEBE")
check("troca_ceo" in pontua("B3: companhia anuncia troca de CEO", "B3"),
      "[8] 'B3: companhia anuncia troca de CEO' → B3 RECEBE")
check(sa.split_digest_segments("Vale: companhia anuncia novo CEO") ==
      ["Vale: companhia anuncia novo CEO"],
      "[8b] segmentador devolve UM item — ':' não divide")
check(len(sa.split_digest_segments(_FP)) == 2,
      "[8c] o mesmo segmentador devolve 2 itens quando há ';'")

print()
print("=" * 96)
print("BLOCO E — §18: os dois digests já corrigidos pela B7b-1")
print("=" * 96)
_VALE = ("Novo CEO da Vale (VALE3): o que Embraer e Klabin dizem sobre participação "
         "de diretores na concorrente")
check("troca_ceo" not in pontua(_VALE, "Embraer"), "[9] Embraer/Vale continua corrigido")
check("troca_ceo" not in pontua(_VALE, "Klabin"), "[9b] Klabin/Vale continua corrigido")
check("troca_ceo" in pontua(_VALE, "Vale"), "[9c] Vale mantém o seu")
check(dono(_VALE, "Embraer") == "",
      "[9d] o gate novo NÃO é necessário aqui — sem ';', B7b-1 decide sozinha")
_COSAN = "Como a Cosan prepara a venda da Rumo: expansão e novo CEO"
check("troca_ceo" not in pontua(_COSAN, "Cosan"), "[10] Cosan/Rumo continua corrigido")
check(dono(_COSAN, "Cosan") == "",
      "[10b] idem: ':' não divide, o gate novo não atua, B7b-1 mantém a correção")

print()
print("=" * 96)
print("BLOCO F — §19.9/§19.10: comportamento anterior preservado")
print("=" * 96)
check("troca_ceo" in pontua("Petrobras anuncia novo CEO", "Petrobras"),
      "[11 sem delimitador] artigo simples inalterado")
check(dono("Usiminas anuncia novo CEO; ações sobem", "Usiminas") == "",
      "[12 uma empresa só] sem outra monitorada dona do item, o gate não atua")
check(dono("Usiminas sobe; petróleo cai após anúncio de novo CEO", "Usiminas") == "",
      "[12b] item sem empresa monitorada não vira 'dono' — nada é desarmado")

print()
print("=" * 96)
print("BLOCO G — §26: waves anteriores intactas")
print("=" * 96)
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração"), "[13] Vale/Samarco nos dois lados")
check("follow_on" not in pontua(
        "Aegea aprova aumento de capital e Itaúsa (ITSA4) pode aportar até "
        "R$ 1,5 bilhão", "Itaúsa"), "[14] B7b-2 preservado")
check("fraude" not in pontua(
        "Truist Bank warns customers about phishing, check fraud and text scams",
        "Truist Financial"), "[15] B4 Truist preservado")
check("ma" in pontua("Cigna’s Evernorth Completes Acquisition of CarepathRx",
                     "Cigna Group"), "[16] M&A legítimo preservado (Wave C congelada)")

print()
print("=" * 96)
print(f"RESULTADO WAVE B7b-4b: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
