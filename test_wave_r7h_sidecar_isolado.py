#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7h_sidecar_isolado.py — R7h.

TESTE NENHUM ESCREVE NO SIDE-CAR DE PRODUCAO.

`rep.aplicar(..., aplicar_de_fato=True)` passou a persistir a trilha de
proveniencia. Dois testes que aplicavam reparo de verdade nao isolavam o
destino, entao gravaram no artefato de PRODUCAO — e isso foi publicado em
`c0430a7`:

  be7c8a47831d5b8a337f  https://exemplo.invalido/artigo   (fixture sintetica)
  21d8d04410e46b6127ed  URL da Vale/Samarco               (fixture com URL real)

A entrada da Vale e a mais traicoeira: URL real, valores de proveniencia
plausiveis, indistinguivel de registro legitimo numa leitura rapida. Um
arquivo de auditoria contaminado por fixture nao serve de auditoria.

A correcao estrutural e injecao de dependencia: `caminho_proveniencia`. O
default e `None` e resolve na chamada — `=dp.CAMINHO` reintroduziria o mesmo
defeito de captura no `def` que ja consertamos dentro do modulo.

O teste central e o BLOCO C: reproduz a sequencia exata que causou a poluicao
e prova que o artefato de producao fica byte-identico.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

import reliability_date_provenance as dp
import reliability_date_repair as rep

PASS = FAIL = 0
POLUIDOS = ("be7c8a47831d5b8a337f", "21d8d04410e46b6127ed")
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


def _rec(pub_ts: int) -> dict:
    return {"title": "Titulo de teste", "url": "https://exemplo.invalido/artigo",
            "canonical_url": "https://exemplo.invalido/artigo",
            "source": "Fonte de Teste", "domain": "exemplo.invalido",
            "summary": "", "pub_ts": pub_ts, "pub_iso": "2026-08-01 12:00",
            "companies": ["Alfa"], "event_ids": ["ma"],
            "events_by_company": {"Alfa": ["ma"]}}


def _tmpdir() -> str:
    return tempfile.mkdtemp(prefix="r7h_")


def _sha_producao() -> str:
    if not os.path.exists(dp.CAMINHO):
        return "AUSENTE"
    return hashlib.sha256(io.open(dp.CAMINHO, "rb").read()).hexdigest()


print("=" * 98)
print("BLOCO A - a API permite injetar o destino")
print("=" * 98)
import inspect

_sig = inspect.signature(rep.aplicar)
check("caminho_proveniencia" in _sig.parameters,
      "[1] `aplicar` aceita `caminho_proveniencia`")
_default = _sig.parameters["caminho_proveniencia"].default
check(_default is None,
      f"[2] o default e None, nao um caminho congelado no `def` ({_default!r})")
_src = inspect.getsource(rep.aplicar)
check("caminho=caminho_proveniencia" in _src,
      "[3] e o parametro chega ao registrador")
check(dp._caminho(None) == dp.CAMINHO,
      "[4] None resolve para o canonico — producao inalterada")
check(dp._caminho("/tmp/x.json") == "/tmp/x.json",
      "[5] e um caminho explicito vence")

print()
print("=" * 98)
print("BLOCO B - injecao funciona: temp recebe, producao nao")
print("=" * 98)
_d = _tmpdir()
_hist = os.path.join(_d, "hist.json")
_prov = os.path.join(_d, "prov.json")
io.open(_hist, "w", encoding="utf-8").write(
    json.dumps({"articles": {"https://exemplo.invalido/artigo": _rec(FEED_TS)}},
               ensure_ascii=False))
_antes = _sha_producao()
_pl = rep.aplicar(_hist, "https://exemplo.invalido/artigo",
                  _html(_iso_de(PAG_TS)), aplicar_de_fato=True,
                  caminho_proveniencia=_prov)
check(_pl["auditoria_proveniencia"]["novos"],
      f"[6] o side-car TEMP recebeu a trilha ({_pl['auditoria_proveniencia']['novos']})")
check(os.path.exists(_prov), "[7] e o arquivo temp foi criado")
check(_sha_producao() == _antes,
      "[8] o side-car de PRODUCAO ficou byte-identico")
_aid = dp.id_do_registro(_rec(FEED_TS))
check(dp.consultar(_aid, _prov) is not None, "[9] a entrada esta no temp")
check(dp.consultar(_aid) is None,
      "[10] e NAO esta em producao")

print()
print("=" * 98)
print("BLOCO C - CENTRAL: reproduz a sequencia que poluiu, sem poluir")
print("=" * 98)
_antes = _sha_producao()
_prod_antes = json.load(io.open(dp.CAMINHO, encoding="utf-8"))["articles"] \
    if os.path.exists(dp.CAMINHO) else {}
_venv = sys.executable
for _suite in ("test_wave_page_date.py", "test_wave_r7f_data_idempotencia.py"):
    _r = subprocess.run([_venv, _suite], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=900)
    check(_r.returncode == 0, f"[11..12] {_suite} verde (exit={_r.returncode})")
check(_sha_producao() == _antes,
      "[13] depois das DUAS suites, o side-car de producao segue byte-identico")
_prod_depois = json.load(io.open(dp.CAMINHO, encoding="utf-8"))["articles"] \
    if os.path.exists(dp.CAMINHO) else {}
check(set(_prod_depois) == set(_prod_antes),
      "[14] nenhum article_id novo apareceu em producao")
for _bad in POLUIDOS:
    check(_bad not in _prod_depois,
          f"[15..16] o id poluido {_bad} NAO foi recriado")

print()
print("=" * 98)
print("BLOCO D - a limpeza: os dois ids poluidos sumiram de producao")
print("=" * 98)
_prod = json.load(io.open(dp.CAMINHO, encoding="utf-8"))
for _bad in POLUIDOS:
    check(_bad not in _prod["articles"],
          f"[17..18] {_bad} ausente do side-car publicado")
check(not any("exemplo.invalido" in (v.get("url") or "")
              for v in _prod["articles"].values()),
      "[19] nenhuma URL sintetica restou")
check(not any((v.get("first_seen_via") or "") == "date_repair"
              and "vale.com" in (v.get("url") or "")
              for v in _prod["articles"].values()),
      "[20] nem a entrada da Vale escrita por teste")

print()
print("=" * 98)
print("BLOCO E - a BRF continua intacta")
print("=" * 98)
_brf = _prod["articles"].get("972a2d5f184545235f9d")
check(_brf is not None, "[21] a entrada da BRF sobreviveu a limpeza")
if _brf:
    pv = _brf["provenance"]
    check(pv["feed_pub_iso"] == "2026-05-28 09:24", "[22] feed original preservado")
    check(pv["page_pub_iso"].startswith("2025-06-17"), "[23] data da pagina preservada")
    check(pv["pub_iso"] == "2025-06-17 13:03", "[24] data efetiva preservada")
    check(pv["pub_date_origin"] == "pagina", "[25] origem preservada")
    check(pv["pub_date_verification"] == "verificado_pagina",
          "[26] verificacao preservada")
    check(pv["pub_date_conflict_s"] == 29794864, "[27] conflito preservado")
    check("CONFLICT" in pv["pub_date_note"], "[28] nota preservada")
    check("02f4080" in (_brf.get("first_seen_via") or ""),
          "[29] proveniencia da semeadura preservada")
    check(_brf.get("revisions") == [], "[30] historico de revisao preservado")
_H = json.load(io.open("risk_history.json", encoding="utf-8"))
check(not any("o-que-fez-cvm-adiar" in u for u in _H["articles"]),
      "[31] e o artigo da BRF NAO foi ressuscitado no historico operacional")

print()
print("=" * 98)
print("BLOCO F - a producao legitima do cron foi preservada")
print("=" * 98)
_ing = [v for v in _prod["articles"].values()
        if (v.get("first_seen_via") or "") == "ingestao"]
check(len(_ing) > 0,
      f"[32] o cron ja gravou entradas legitimas pela ingestao ({len(_ing)})")
check(all("exemplo" not in (v.get("url") or "") for v in _ing),
      "[33] nenhuma delas e sintetica")
_conf = [v for v in _ing if v["provenance"].get("pub_date_origin") == "pagina"]
check(len(_conf) > 0,
      f"[34] inclusive conflitos reais detectados em producao ({len(_conf)})")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7h (side-car isolado de teste): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
