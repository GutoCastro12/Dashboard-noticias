#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7f_data_idempotencia.py — R7f.

DATA EM VIGOR NAO E DATA DO FEED.

Depois de uma correcao baseada em pagina, `pub_ts` passa a ser a data da
PAGINA. As duas portas de decisao — `reliability_date_repair.preparar` e
`reliability_page_date.verificar_registro` — reliam `pub_ts` como se fosse a
data do feed. Na SEGUNDA passada isso compara pagina com pagina: delta 0,
conflito falso-negativo, e a proveniencia do conflito real seria apagada:

  pub_date_origin       : pagina            -> feed
  pub_date_verification : verificado_pagina -> verificado_sem_conflito
  pub_date_conflict_s   : 29794864          -> 0
  pub_date_note         : conflito          -> "dentro da tolerancia"

A data em vigor nao se movia — por isso o defeito passou. O que se perdia era
a TRILHA: o registro passaria a alegar que a data veio do feed sem conflito,
que e falso. Exposto no caso da BRF (feed 2026-05-28 x pagina 2025-06-17,
344 dias).

A correcao e um seletor unico, `feed_original_ts`, com a MESMA precedencia que
`campos_de_proveniencia` ja usava para gravar: `feed_pub_ts` quando existe,
`pub_ts` na primeira passada. Registro novo se comporta exatamente como antes.

O teste central e metamorfico (BLOCO D): passada 1 e passada 2 tem de produzir
o mesmo estado, em todos os cenarios de politica.
"""
from __future__ import annotations

import copy
import io
import json
import os
import tempfile

import reliability_date_repair as rep
import reliability_page_date as pd

PASS = FAIL = 0

# ── fixtures sinteticas: sem rede, sem nomes reais na logica ────────────────
FEED_TS = 1786000000          # feed "atual"
PAG_TS = FEED_TS - 300 * 86400  # pagina bem mais antiga -> conflito material


def _html(iso: str) -> str:
    """Pagina minima com JSON-LD declarando a data. Nenhuma requisicao."""
    return ('<html><head><script type="application/ld+json">'
            '{"@type":"NewsArticle","headline":"Titulo de teste",'
            f'"datePublished":"{iso}","dateModified":"{iso}"}}'
            '</script></head><body>corpo</body></html>')


def _iso_de(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")


def _registro(pub_ts: int, **extra) -> dict:
    r = {"title": "Titulo de teste", "url": "https://exemplo.invalido/artigo",
         "canonical_url": "https://exemplo.invalido/artigo",
         "source": "Fonte de Teste", "domain": "exemplo.invalido",
         "summary": "", "pub_ts": pub_ts,
         "pub_iso": "2026-08-01 12:00", "companies": ["Alfa"],
         "event_ids": ["ma"], "events_by_company": {"Alfa": ["ma"]}}
    r.update(extra)
    return r


def _hist(rec: dict) -> dict:
    return {"articles": {rec["url"]: copy.deepcopy(rec)}}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


def _passada(hist: dict, html: str) -> dict:
    return rep.preparar(hist, list(hist["articles"])[0], html)


def _aplicado(hist: dict, campos: dict) -> dict:
    """Estado do registro depois de gravar os campos propostos."""
    h = copy.deepcopy(hist)
    u = list(h["articles"])[0]
    h["articles"][u].update(campos)
    return h


CAMPOS_DATA = ("pub_ts", "pub_iso", "feed_pub_ts", "feed_pub_iso",
               "page_pub_ts", "page_pub_iso", "page_date_source",
               "page_date_modified", "pub_date_origin", "pub_date_policy",
               "pub_date_verification", "pub_date_conflict_s", "pub_date_note")


def _foto(hist: dict) -> dict:
    r = hist["articles"][list(hist["articles"])[0]]
    return {k: r.get(k) for k in CAMPOS_DATA}


print("=" * 98)
print("BLOCO A - o seletor de feed original")
print("=" * 98)
check(pd.feed_original_ts({"pub_ts": 111}) == 111,
      "[1] sem proveniencia, o feed original e o proprio pub_ts (1a passada)")
check(pd.feed_original_ts({"pub_ts": 222, "feed_pub_ts": 111}) == 111,
      "[2] com proveniencia, vence feed_pub_ts — nao a data em vigor")
check(pd.feed_original_ts({}) == 0, "[3] registro vazio devolve 0")
check(pd.feed_original_ts({"pub_ts": "nao-numero"}) == 0,
      "[4] valor invalido nao explode")
check(pd.feed_original_ts({"pub_ts": 222, "feed_pub_ts": 0}) == 222,
      "[5] feed_pub_ts zerado cai para pub_ts")

print()
print("=" * 98)
print("BLOCO B - PAGINA VENCE: primeira passada preservada")
print("=" * 98)
H1 = _hist(_registro(FEED_TS))
P1 = _passada(H1, _html(_iso_de(PAG_TS)))
check(P1["decisao"]["origem"] == "pagina", "[6] a pagina vence o conflito material")
check(P1["decisao"]["conflito"] is True, "[7] o conflito e registrado")
check(P1["decisao"]["delta_s"] == abs(FEED_TS - PAG_TS),
      f"[8] o delta e feed x pagina ({P1['decisao']['delta_s']}s)")
check(P1["campos"]["feed_pub_ts"] == FEED_TS,
      "[9] o feed original e preservado")
check(P1["campos"]["pub_ts"] == PAG_TS, "[10] a data em vigor passa a ser a da pagina")
check(P1["campos"]["pub_date_verification"] == "verificado_pagina",
      "[11] verificacao = verificado_pagina")

print()
print("=" * 98)
print("BLOCO C - PAGINA VENCE: SEGUNDA passada e no-op")
print("=" * 98)
H1b = _aplicado(H1, P1["campos"])
P1b = _passada(H1b, _html(_iso_de(PAG_TS)))
check(P1b["mudancas"] == {},
      f"[12] a segunda passada NAO propoe mudanca nenhuma ({P1b['mudancas']})")
check(P1b["decisao"]["origem"] == "pagina",
      "[13] origem continua `pagina` — nao volta para `feed`")
check(P1b["decisao"]["verificacao"] == "verificado_pagina",
      "[14] verificacao continua `verificado_pagina`")
check(P1b["decisao"]["delta_s"] == abs(FEED_TS - PAG_TS),
      "[15] o delta do conflito NAO vira 0")
check(P1b["decisao"]["conflito"] is True, "[16] o conflito continua registrado")
check("CONFLICT" in P1b["decisao"]["motivo"],
      "[17] a nota de auditoria continua descrevendo o conflito")
check(P1b["feed_iso"] == P1["campos"]["feed_pub_iso"],
      f"[18] o relatorio exibe o feed ORIGINAL ({P1b['feed_iso']}), nao a data em vigor")

print()
print("=" * 98)
print("BLOCO D - METAMORFICO: passada 1 == passada 2, em toda politica")
print("=" * 98)
CENARIOS = [
    ("pagina vence (conflito material)", FEED_TS, PAG_TS),
    ("sem conflito (dentro da tolerancia)", FEED_TS, FEED_TS - 60),
    ("feed vence (pagina identica)", FEED_TS, FEED_TS),
    # ambos no passado: data futura e recusada pelo extrator, e com razao
    ("pagina mais NOVA que o feed", FEED_TS - 400 * 86400, FEED_TS - 100 * 86400),
]
for rot, fts, pts in CENARIOS:
    h = _hist(_registro(fts))
    html = _html(_iso_de(pts))
    p1 = _passada(h, html)
    h2 = _aplicado(h, p1["campos"])
    p2 = _passada(h2, html)
    h3 = _aplicado(h2, p2["campos"])
    check(_foto(h2) == _foto(h3) and p2["mudancas"] == {},
          f"[19..22] {rot}: passada 2 identica a passada 1 "
          f"(origem={p1['decisao']['origem']}, mudancas={p2['mudancas']})")

print()
print("=" * 98)
print("BLOCO E - SEM CONFLITO e FEED VENCE continuam se comportando como antes")
print("=" * 98)
Hs = _hist(_registro(FEED_TS))
Ps = _passada(Hs, _html(_iso_de(FEED_TS - 60)))
check(Ps["decisao"]["origem"] == "feed",
      "[23] divergencia dentro da tolerancia mantem o feed")
check(Ps["decisao"]["verificacao"] == "verificado_sem_conflito",
      "[24] e marca verificado_sem_conflito")
check("pub_ts" not in Ps["campos"],
      "[25] sem conflito, a data em vigor nem e reescrita")
check(Ps["campos"]["page_pub_ts"] == FEED_TS - 60,
      "[26] mas a evidencia da pagina fica gravada")
Hs2 = _aplicado(Hs, Ps["campos"])
Ps2 = _passada(Hs2, _html(_iso_de(FEED_TS - 60)))
check(Ps2["mudancas"] == {}, f"[27] e a segunda passada e no-op ({Ps2['mudancas']})")
check(Ps2["decisao"]["origem"] == "feed", "[28] sem oscilacao de proveniencia")

print()
print("=" * 98)
print("BLOCO F - NOVA EVIDENCIA DE PAGINA ainda muda as coisas")
print("=" * 98)
# depois de reparado, uma pagina que passa a declarar OUTRA data forte tem de
# voltar a propor correcao — idempotencia nao pode virar congelamento eterno.
H3 = _aplicado(H1, P1["campos"])
NOVA = PAG_TS - 500 * 86400
P3 = _passada(H3, _html(_iso_de(NOVA)))
check(P3["mudancas"] != {},
      "[29] evidencia de pagina NOVA volta a propor correcao")
check(P3["campos"]["pub_ts"] == NOVA,
      "[30] a data em vigor acompanha a nova evidencia")
check(P3["campos"]["feed_pub_ts"] == FEED_TS,
      "[31] e o feed ORIGINAL continua sendo o feed original")
check(P3["decisao"]["delta_s"] == abs(FEED_TS - NOVA),
      "[32] o delta volta a ser medido contra o feed original")

print()
print("=" * 98)
print("BLOCO G - CORRECAO MANUAL TRAVADA vence o reparador")
print("=" * 98)
Ht = _hist(_registro(FEED_TS, manual_correction={
    "locked_fields": ["pub_ts", "pub_iso"], "correction_id": "teste_lock",
    "reason": "auditoria humana"}))
try:
    _passada(Ht, _html(_iso_de(PAG_TS)))
    check(False, "[33] registro travado deveria ser recusado")
except rep.ReparoRecusado as e:
    check("TRAVADA" in str(e), f"[33] registro travado e recusado ({str(e)[:46]})")
_ct = pd.verificar_registro(Ht["articles"][list(Ht["articles"])[0]],
                            _html(_iso_de(PAG_TS)))
check(_ct.get("pub_date_verification") == "ignorado_correcao_manual",
      "[34] e a camada de ingestao tambem respeita o lock")
check("pub_ts" not in _ct, "[35] nenhuma data e proposta sob lock")

print()
print("=" * 98)
print("BLOCO H - a camada de INGESTAO usa o mesmo seletor")
print("=" * 98)
_novo = _registro(FEED_TS)
_c1 = pd.verificar_registro(dict(_novo), _html(_iso_de(PAG_TS)))
check(_c1["pub_date_origin"] == "pagina" and _c1["feed_pub_ts"] == FEED_TS,
      "[36] registro novo: comportamento de primeira passada inalterado")
_rep_ing = dict(_novo)
_rep_ing.update(_c1)
_c2 = pd.verificar_registro(_rep_ing, _html(_iso_de(PAG_TS)))
check(all(_rep_ing.get(k) == v for k, v in _c2.items()),
      f"[37] registro ja reparado: segunda verificacao e no-op "
      f"({[k for k, v in _c2.items() if _rep_ing.get(k) != v]})")
check(_c2["pub_date_origin"] == "pagina" and _c2["pub_date_conflict_s"] ==
      abs(FEED_TS - PAG_TS),
      "[38] sem oscilacao de origem nem zeragem do conflito na ingestao")

print()
print("=" * 98)
print("BLOCO I - DRY-RUN e APPLY coincidem")
print("=" * 98)
_tmp = os.path.join(tempfile.mkdtemp(prefix="r7f_"), "hist.json")
io.open(_tmp, "w", encoding="utf-8").write(
    json.dumps(_hist(_registro(FEED_TS)), ensure_ascii=False))
_html_p = _html(_iso_de(PAG_TS))
_prev = rep.preparar(json.load(io.open(_tmp, encoding="utf-8")),
                     "https://exemplo.invalido/artigo", _html_p)
_prov_tmp = os.path.join(os.path.dirname(_tmp), "prov.json")
rep.aplicar(_tmp, "https://exemplo.invalido/artigo", _html_p,
            aplicar_de_fato=True, caminho_proveniencia=_prov_tmp)
_depois = json.load(io.open(_tmp, encoding="utf-8"))
_rec = _depois["articles"]["https://exemplo.invalido/artigo"]
check(all(_rec.get(k) == v for k, v in _prev["campos"].items()),
      "[39] o que o dry-run previu foi exatamente o que o apply gravou")
_seg = rep.preparar(_depois, "https://exemplo.invalido/artigo", _html_p)
check(_seg["mudancas"] == {},
      f"[40] e o dry-run seguinte ao apply e zero ({_seg['mudancas']})")

print()
print("=" * 98)
print("BLOCO J - regressao EXATA da BRF (fixture derivada do registro reparado)")
print("=" * 98)
# O registro real saiu de `risk_history.json`: com a data corrigida para
# 2025-06-17 ele passou dos 400 dias de `history_keep_days` e o cron o podou.
# A fixture preserva o estado reparado publicado em `02f4080`.
BRF = {
    "title": "O que fez a CVM adiar a assembleia de fusão entre Marfrig e BRF?",
    "url": ("https://www.estadao.com.br/web-stories/einvestidor/"
            "o-que-fez-cvm-adiar-assembleia-fusao-marfrig-e-brf/"),
    "canonical_url": ("https://www.estadao.com.br/web-stories/einvestidor/"
                      "o-que-fez-cvm-adiar-assembleia-fusao-marfrig-e-brf/"),
    "source": "Estadão", "domain": "estadao.com.br", "summary": "",
    "pub_ts": 1750176193, "pub_iso": "2025-06-17 13:03",
    "feed_pub_ts": 1779971057, "feed_pub_iso": "2026-05-28 09:24",
    "page_pub_ts": 1750176193, "page_pub_iso": "2025-06-17 16:03",
    "page_date_source": "jsonld",
    "page_date_modified": "2025-06-17T13:03:13-03:00",
    "pub_date_origin": "pagina", "pub_date_policy": "pubdate.p1",
    "pub_date_verification": "verificado_pagina",
    "pub_date_conflict_s": 29794864,
    "pub_date_note": ("FEED_PAGE_DATE_CONFLICT: feed e página divergem "
                      "344 dia(s); a página é a autoridade"),
}
HB = {"articles": {BRF["url"]: copy.deepcopy(BRF)}}
PB = _passada(HB, _html("2025-06-17T13:03:13-03:00"))
check(PB["mudancas"] == {},
      f"[41] segunda passada na BRF nao propoe NADA ({PB['mudancas']})")
check(PB["decisao"]["origem"] == "pagina", "[42] origem continua `pagina`")
check(PB["decisao"]["verificacao"] == "verificado_pagina",
      "[43] verificacao continua `verificado_pagina`")
check(PB["decisao"]["delta_s"] == 29794864,
      f"[44] o delta de conflito continua 29794864 ({PB['decisao']['delta_s']})")
check(PB["campos"]["pub_ts"] == 1750176193 and
      PB["campos"]["pub_iso"] == "2025-06-17 13:03",
      "[45] a data corrigida NAO se move")
check(PB["campos"]["feed_pub_iso"] == "2026-05-28 09:24",
      "[46] o feed original continua preservado")
check(PB["campos"]["pub_date_note"] == BRF["pub_date_note"],
      "[47] a nota de auditoria e byte a byte a mesma")
check(_foto(HB) == _foto(_aplicado(HB, PB["campos"])),
      "[48] aplicar a segunda passada deixaria o registro identico")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7f (idempotencia de proveniencia de data): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
