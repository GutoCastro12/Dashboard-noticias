#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7g_proveniencia_retencao.py — R7g.

A CORRECAO TEM DE SOBREVIVER AO ARTIGO.

Corrigir a data do artigo da BRF para 2025-06-17 deu a ele 427 dias.
`history_keep_days` e 400. No cron seguinte `merge_into_history` podou o
registro de `risk_history.json` — some o registro, some o conflito, some a
prova de que a data do feed estava errada. Quanto mais antiga a data
verdadeira, mais rapido o proprio reparo desaparece.

A decisao humana: NAO transformar `risk_history.json` em arquivo permanente.
Retencao continua igual, `history_keep_days` nao muda, nenhum artigo ganha
isencao por ter `pub_date_origin=pagina`. O que muda e a separacao:

  historico operacional de noticias  !=  trilha permanente de proveniencia

O teste central e o BLOCO E: o artigo sai do historico por retencao e a
proveniencia continua consultavel.
"""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import tempfile

import reliability_date_provenance as dp
import reliability_date_repair as rep
import reliability_page_date as pd

PASS = FAIL = 0
BRF_ID = "972a2d5f184545235f9d"
FEED_TS = 1786000000
PAG_TS = FEED_TS - 300 * 86400


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


def _html(iso: str) -> str:
    return ('<html><head><script type="application/ld+json">'
            '{"@type":"NewsArticle","headline":"Titulo de teste",'
            f'"datePublished":"{iso}","dateModified":"{iso}"}}'
            '</script></head><body>corpo</body></html>')


def _iso_de(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")


def _rec(pub_ts: int, **extra) -> dict:
    r = {"title": "Titulo de teste", "url": "https://exemplo.invalido/artigo",
         "canonical_url": "https://exemplo.invalido/artigo",
         "source": "Fonte de Teste", "domain": "exemplo.invalido",
         "summary": "corpo do artigo", "pub_ts": pub_ts,
         "pub_iso": "2026-08-01 12:00", "companies": ["Alfa"],
         "event_ids": ["ma"], "events_by_company": {"Alfa": ["ma"]}}
    r.update(extra)
    return r


def _tmp(nome: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="r7g_"), nome)


print("=" * 98)
print("BLOCO A - o artefato se declara e nao tem autoridade")
print("=" * 98)
_v = dp.carregar(_tmp("vazio.json"))
_m = _v["_meta"]
check(_m["schema_version"] == "pubdate.audit.v1",
      f"[1] schema versionado ({_m['schema_version']})")
check(_m["role"] == "PUBLICATION_DATE_PROVENANCE_AUDIT", "[2] papel declarado")
check(_m["production_score_authority"] == "NONE", "[3] autoridade de score NENHUMA")
check(_m["semantic_authority"] == "NONE", "[4] autoridade semantica NENHUMA")
check(_m["human_truth_authority"] == "NONE", "[5] autoridade de verdade humana NENHUMA")
check(_v["articles"] == {}, "[6] comeca vazio, sem inventar entrada")
check("article_id" in _m["chave"], "[7] chave estavel declarada")

print()
print("=" * 98)
print("BLOCO B - so registra quando a PAGINA estabeleceu algo alem do feed")
print("=" * 98)
check(not dp.deve_registrar(_rec(FEED_TS)),
      "[8] registro so com data de feed NAO entra — nao e copia do historico")
check(dp.deve_registrar(_rec(FEED_TS, page_pub_ts=PAG_TS)),
      "[9] registro com data de pagina entra")
check(not dp.deve_registrar(_rec(FEED_TS, page_pub_ts=0)),
      "[10] page_pub_ts zerado nao conta")

print()
print("=" * 98)
print("BLOCO C - upsert idempotente e revisao rastreada")
print("=" * 98)
CAM = _tmp("prov.json")
r1 = _rec(FEED_TS)
h1 = {"articles": {r1["url"]: copy.deepcopy(r1)}}
p1 = rep.preparar(h1, r1["url"], _html(_iso_de(PAG_TS)))
rec_reparado = {**r1, **p1["campos"]}
res1 = dp.registrar_muitos([(r1["url"], rec_reparado)], caminho=CAM,
                           origem="teste", aplicar=True)
check(len(res1["novos"]) == 1 and res1["total"] == 1,
      f"[11] primeira evidencia cria a entrada ({res1['novos']})")
res2 = dp.registrar_muitos([(r1["url"], rec_reparado)], caminho=CAM,
                           origem="teste", aplicar=True)
check(res2["novos"] == [] and res2["revisados"] == [],
      f"[12] evidencia IDENTICA nao escreve nada ({res2})")
_antes = io.open(CAM, encoding="utf-8").read()
dp.registrar_muitos([(r1["url"], rec_reparado)], caminho=CAM, aplicar=True)
check(io.open(CAM, encoding="utf-8").read() == _antes,
      "[13] e o arquivo fica byte-identico — sem commit de ruido")
_aid = dp.id_do_registro(rec_reparado)
_e = dp.consultar(_aid, CAM)
check(_e["provenance"]["pub_date_origin"] == "pagina",
      "[14] a origem `pagina` fica gravada")
check(_e["provenance"]["feed_pub_ts"] == FEED_TS,
      "[15] o feed ORIGINAL fica gravado")
check(_e["provenance"]["pub_date_conflict_s"] == abs(FEED_TS - PAG_TS),
      "[16] o conflito fica gravado")
check("CONFLICT" in _e["provenance"]["pub_date_note"],
      "[17] a nota de auditoria fica gravada")
check(_e["revisions"] == [], "[18] sem revisao espuria")

print()
print("=" * 98)
print("BLOCO D - evidencia NOVA nao sobrescreve em silencio")
print("=" * 98)
NOVA = PAG_TS - 500 * 86400
h2 = {"articles": {r1["url"]: copy.deepcopy(rec_reparado)}}
p2 = rep.preparar(h2, r1["url"], _html(_iso_de(NOVA)))
rec2 = {**rec_reparado, **p2["campos"]}
res3 = dp.registrar_muitos([(r1["url"], rec2)], caminho=CAM, origem="teste2",
                           aplicar=True)
check(res3["revisados"] == [_aid], f"[19] evidencia diferente vira REVISAO ({res3})")
_e2 = dp.consultar(_aid, CAM)
check(len(_e2["revisions"]) == 1, "[20] a decisao anterior foi PRESERVADA")
check(_e2["revisions"][0]["provenance"]["page_pub_ts"] == PAG_TS,
      "[21] com a evidencia antiga intacta")
check(_e2["provenance"]["page_pub_ts"] == NOVA,
      "[22] e a corrente refletindo a evidencia nova")
check(_e2["provenance"]["feed_pub_ts"] == FEED_TS,
      "[23] o feed original continua sendo o feed original")

print()
print("=" * 98)
print("BLOCO E - CENTRAL: retencao tira o artigo, a proveniencia fica")
print("=" * 98)
import risk_dashboard as rd

_cfg = rd.load_config("config_risco.yaml")
_keep = _cfg["dashboard"].get("history_keep_days", 120)
check(_keep == 400, f"[24] `history_keep_days` INALTERADO ({_keep})")
# artigo antigo o bastante para a retencao levar
_velho = _rec(int(__import__("time").time()) - (_keep + 30) * 86400,
              url="https://exemplo.invalido/antigo",
              canonical_url="https://exemplo.invalido/antigo")
_velho.update({"page_pub_ts": _velho["pub_ts"], "page_pub_iso": "x",
               "feed_pub_ts": _velho["pub_ts"] + 300 * 86400,
               "feed_pub_iso": "y", "pub_date_origin": "pagina",
               "pub_date_verification": "verificado_pagina",
               "pub_date_conflict_s": 300 * 86400,
               "pub_date_policy": "pubdate.p1", "pub_date_note": "CONFLICT teste"})
CAM2 = _tmp("prov2.json")
dp.registrar_muitos([(_velho["url"], _velho)], caminho=CAM2, origem="teste",
                    aplicar=True)
_aid_v = dp.id_do_registro(_velho)
_hist = {"articles": {_velho["url"]: copy.deepcopy(_velho)}}
rd.merge_into_history(_hist, [], keep_days=_keep)
check(_velho["url"] not in _hist["articles"],
      "[25] a retencao REMOVEU o artigo do historico operacional")
_sobrevive = dp.consultar(_aid_v, CAM2)
check(_sobrevive is not None,
      "[26] e a proveniencia CONTINUA consultavel depois da poda")
check(_sobrevive["provenance"]["pub_date_origin"] == "pagina" and
      _sobrevive["provenance"]["pub_date_conflict_s"] == 300 * 86400,
      "[27] com origem e conflito reconstrutiveis")
check(_velho["url"] not in _hist["articles"],
      "[28] e o artigo NAO foi ressuscitado para o historico")

print()
print("=" * 98)
print("BLOCO F - CANARIO REAL: a BRF, ja podada de producao")
print("=" * 98)
_H = json.load(io.open("risk_history.json", encoding="utf-8"))
check(not any("o-que-fez-cvm-adiar" in u for u in _H["articles"]),
      "[29] o artigo da BRF NAO esta no historico vivo (podado pela retencao)")
_brf = dp.consultar(BRF_ID)
check(_brf is not None, "[30] mas a proveniencia da BRF esta no side-car")
if _brf:
    pv = _brf["provenance"]
    check(pv["feed_pub_iso"] == "2026-05-28 09:24",
          f"[31] feed original 2026-05-28 preservado ({pv['feed_pub_iso']})")
    check(pv["page_pub_iso"].startswith("2025-06-17"),
          f"[32] data da pagina 2025-06-17 preservada ({pv['page_pub_iso']})")
    check(pv["pub_iso"] == "2025-06-17 13:03", "[33] data efetiva preservada")
    check(pv["pub_date_conflict_s"] == 29794864,
          f"[34] conflito de 344 dias reconstrutivel ({pv['pub_date_conflict_s']})")
    check(pv["pub_date_origin"] == "pagina" and
          pv["pub_date_verification"] == "verificado_pagina",
          "[35] origem e verificacao preservadas")
    check(pv["page_date_source"] == "jsonld", "[36] fonte da data preservada")
    check("02f4080" in (_brf.get("first_seen_via") or ""),
          f"[37] a proveniencia da SEMEADURA nomeia o commit imutavel de origem "
          f"({_brf.get('first_seen_via')})")
    check(_brf.get("companies") == ["BRF"] and _brf.get("event_ids") == ["ma"],
          "[38] identificacao compacta suficiente para auditoria futura")

print()
print("=" * 98)
print("BLOCO G - a semeadura veio de estado publicado, nao de digitacao")
print("=" * 98)
_bruto = subprocess.run(["git", "show", "02f4080:risk_history.json"],
                        capture_output=True).stdout
_Hp = json.loads(_bruto.decode("utf-8"))
_orig = [r for u, r in _Hp["articles"].items() if "o-que-fez-cvm-adiar" in u]
check(len(_orig) == 1, "[39] o registro reparado existe em 02f4080")
if _orig and _brf:
    _esperado = {k: _orig[0][k] for k in dp.CAMPOS if _orig[0].get(k) not in (None, "")}
    check(_brf["provenance"] == _esperado,
          "[40] e a entrada semeada bate campo a campo com ele")

print()
print("=" * 98)
print("BLOCO H - NENHUMA autoridade de score ou semantica")
print("=" * 98)
_fontes = {f: io.open(f, encoding="utf-8").read()
           for f in ("risk_dashboard.py", "semantic_audit.py")}
_codigo = {f: "\n".join(l.split("#")[0] for l in s.splitlines())
           for f, s in _fontes.items()}
check("reliability_date_provenance" not in _codigo["semantic_audit.py"],
      "[41] `semantic_audit` nao importa o side-car")
import inspect
check("reliability_date_provenance" not in inspect.getsource(rd.build_evolution),
      "[42] `build_evolution` nao le o side-car")
check("reliability_date_provenance" not in inspect.getsource(rd.merge_into_history),
      "[43] `merge_into_history` nao foi acoplado a ele")
check("reliability_date_provenance" in _codigo["risk_dashboard.py"],
      "[44] so a camada de verificacao de data o alimenta")

print()
print("=" * 98)
print("BLOCO I - a camada de INGESTAO entrega a trilha")
print("=" * 98)
_src = inspect.getsource(rd.verify_publication_dates)
check("_auditar" in _src and "registrar_muitos" in _src,
      "[45] `verify_publication_dates` persiste a proveniencia")
check("page_pub_ts" in _src,
      "[46] so quando a pagina estabeleceu data — sem inflar o arquivo")
check("except Exception" in _src,
      "[47] fail-open: perder a trilha nao derruba a coleta")

print()
print("=" * 98)
print("BLOCO J - o reparo tambem entrega, e locks continuam vencendo")
print("=" * 98)
_hist_tmp = _tmp("hist.json")
io.open(_hist_tmp, "w", encoding="utf-8").write(
    json.dumps({"articles": {r1["url"]: _rec(FEED_TS)}}, ensure_ascii=False))
_cam_j = _tmp("prov3.json")
# Injecao explicita, nao monkeypatch de `dp.CAMINHO`: o parametro e o contrato,
# e teste que depende de reatribuir global volta a vazar para producao no dia
# em que alguem esquecer o `finally`.
_pl = rep.aplicar(_hist_tmp, r1["url"], _html(_iso_de(PAG_TS)),
                  aplicar_de_fato=True, caminho_proveniencia=_cam_j)
check(_pl.get("auditoria_proveniencia", {}).get("novos"),
      f"[48] `--apply` grava a trilha ({_pl.get('auditoria_proveniencia')})")
check(dp.consultar(dp.id_do_registro(_rec(FEED_TS)), _cam_j) is not None,
      "[49] e a entrada e consultavel logo apos o reparo")
_travado = _rec(FEED_TS, manual_correction={"locked_fields": ["pub_ts", "pub_iso"]})
_ct = pd.verificar_registro(_travado, _html(_iso_de(PAG_TS)))
check(_ct.get("pub_date_verification") == "ignorado_correcao_manual",
      "[50] correcao manual travada continua vencendo o verificador")
check(not dp.deve_registrar({**_travado, **_ct}),
      "[51] e registro travado nao entra na trilha por acidente")

print()
print("=" * 98)
print("BLOCO K - o cron persiste o artefato")
print("=" * 98)
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("risk_date_provenance.json" in _wf,
      "[52] o workflow adiciona o artefato ao commit de dados")
check("git add risk_history.json" in _wf,
      "[53] sem mexer na lista principal de dados")
_cfg_txt = io.open("config_risco.yaml", encoding="utf-8").read()
check("history_keep_days: 400" in _cfg_txt,
      "[54] `history_keep_days` continua 400 no config")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7g (proveniencia de data resistente a retencao): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
