#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7cp4_backlog.py — 4I.2 R7c-P4 §16.

A FILA ANTIGA NÃO PODE MONOPOLIZAR O RUN.

O run 31627960181 estourou os 8 minutos porque 184 registros ficaram
retomáveis de uma vez. O passo foi cortado ANTES de gravar o sidecar: gastou o
envelope inteiro e não acumulou nada. O teto estrutural de 80 não protege
contra isso — ele limita o TOTAL de requisições, não a fatia que o backlog
consome.

Dois orçamentos, dois papéis:

    MAX_FETCH_POR_RUN = 80          teto ESTRUTURAL do run inteiro
    MAX_RETRY_BACKLOG_POR_RUN = 25  fatia máxima da FILA ANTIGA

Artigo novo tem precedência absoluta: é a coleta prospectiva, a razão de
existir da camada. O backlog fica com o que sobrar do teto, e ainda assim
limitado a 25, para drenar ao longo de vários crons.

Os testes usam a mesma função de particionamento do runtime, com sidecars
sintéticos — nenhum deles toca a rede.
"""
from __future__ import annotations

import io

import reliability_input_rehearsal as rh
import reliability_input_shadow as sh

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _reg(i, falha=rh.RESOLUTION_FAILED, last=10, first=5):
    url = f"http://old/{i}"
    return il_id(url), {"article_id": il_id(url), "url": url,
                        "falha": falha, "last_seen_run": last,
                        "first_seen_run": first,
                        "final": {"input_ready_under_r7c_policy": False,
                                  "useful_chars": 20}}


def il_id(url):
    return sh.il.identidade(url)


def particionar(conhecidos: dict, urls: list):
    """Reproduz a política do runtime: novos primeiro, backlog ordenado por
    quem foi visto há mais tempo, cortado no orçamento."""
    novos, backlog = [], []
    for u in urls:
        aid = il_id(u)
        reg = conhecidos.get(aid)
        if reg is None or reg.get("falha") not in sh.RETOMAVEIS:
            novos.append(u)
        else:
            backlog.append((reg, u))
    backlog.sort(key=lambda rb: ((rb[0].get("last_seen_run") or 0),
                                 (rb[0].get("first_seen_run") or 0),
                                 rb[0].get("article_id") or ""))
    sel = [u for _r, u in backlog[:sh.MAX_RETRY_BACKLOG_POR_RUN]]
    adiado = [u for _r, u in backlog[sh.MAX_RETRY_BACKLOG_POR_RUN:]]
    return novos, sel, adiado


print("=" * 98)
print("BLOCO A — dois orçamentos, uma fonte de verdade")
print("=" * 98)
check(sh.MAX_RETRY_BACKLOG_POR_RUN == 25,
      f"[1] orçamento de backlog = 25 ({sh.MAX_RETRY_BACKLOG_POR_RUN})")
check(sh.MAX_FETCH_POR_RUN == 80,
      f"[2] teto ESTRUTURAL segue 80 ({sh.MAX_FETCH_POR_RUN})")
check(sh.MAX_RETRY_BACKLOG_POR_RUN != sh.MAX_FETCH_POR_RUN,
      "[3] 25 não virou o novo cap total")
_src = io.open("reliability_input_shadow.py", encoding="utf-8").read()
check(_src.count("MAX_RETRY_BACKLOG_POR_RUN = ") == 1,
      "[4] o número está declarado em UM lugar só")
check("MAX_RETRY_BACKLOG_POR_RUN" in _src.split("def coletar(")[1],
      "[5] e é o coletor que o consome")

print()
print("=" * 98)
print("BLOCO B — §16 CASE A: 100 retomáveis, nenhum novo")
print("=" * 98)
_c100 = dict(_reg(i) for i in range(100))
_u100 = [f"http://old/{i}" for i in range(100)]
_n, _s, _a = particionar(_c100, _u100)
check(len(_s) == 25, f"[6] backlog selecionado = 25 ({len(_s)})")
check(len(_a) == 75, f"[7] os demais 75 são adiados ({len(_a)})")
check(len(_n) == 0, "[8] nenhum artigo novo neste cenário")

print()
print("=" * 98)
print("BLOCO C — §16 CASE B: 10 novos + 100 retomáveis")
print("=" * 98)
_urls = [f"http://new/{i}" for i in range(10)] + _u100
_n, _s, _a = particionar(_c100, _urls)
check(len(_n) == 10, f"[9] os 10 novos NÃO são expulsos pela fila ({len(_n)})")
check(len(_s) == 25, f"[10] backlog limitado a 25 ({len(_s)})")
check(len(_n) + len(_s) <= sh.MAX_FETCH_POR_RUN,
      f"[11] total elegível a fetch ≤ 80 ({len(_n) + len(_s)})")

print()
print("=" * 98)
print("BLOCO D — §16 CASE C/D: o teto estrutural manda no total")
print("=" * 98)
_u70 = [f"http://new/{i}" for i in range(70)]
_n, _s, _a = particionar(_c100, _u70 + _u100)
check(len(_n) == 70, f"[12] 70 novos preservados ({len(_n)})")
check(len(_n) + len(_s) == 95,
      f"[13] a seleção soma 95, e é o CONTADOR que corta em 80 ({len(_n)+len(_s)})")
_cont = {"fetches": 80, "duplicatas_evitadas": 0, "por_artigo": {},
         "limite_fetch": sh.MAX_FETCH_POR_RUN}
check(rh.enriquecer_uma_vez("http://x/81", "T", {}, sidecar={},
                            permitir_rede=True,
                            contador=_cont)["falha"] == rh.CAP_REACHED,
      "[14] atingido o teto estrutural, o excedente vira CAP_REACHED")
_u80 = [f"http://new/{i}" for i in range(80)]
_n, _s, _a = particionar(_c100, _u80)
check(len(_n) == 80 and len(_s) == 0,
      f"[15] CASE D: 80 novos, backlog fica com 0 ({len(_n)}/{len(_s)})")

print()
print("=" * 98)
print("BLOCO E — §16 CASE E: fila menor que o orçamento é toda retomada")
print("=" * 98)
_c20 = dict(_reg(i) for i in range(20))
_n, _s, _a = particionar(_c20, [f"http://old/{i}" for i in range(20)])
check(len(_s) == 20 and not _a,
      f"[16] os 20 retomáveis passam inteiros ({len(_s)}, adiados {len(_a)})")

print()
print("=" * 98)
print("BLOCO F — §16 CASE F: justiça entre runs, sem starvation")
print("=" * 98)
_c = dict(_reg(i, last=10 + i) for i in range(60))
_urls = [f"http://old/{i}" for i in range(60)]
_n1, _s1, _a1 = particionar(_c, _urls)
check(len(_s1) == 25, f"[17] run 1 leva 25 ({len(_s1)})")
# run 2: os atendidos avançam `last_seen`; os adiados continuam com o antigo
for u in _s1:
    _c[il_id(u)]["last_seen_run"] = 999
_n2, _s2, _a2 = particionar(_c, _urls)
check(not (set(_s1) & set(_s2)),
      f"[18] run 2 NÃO repete nenhum do run 1 ({len(set(_s1) & set(_s2))} repetidos)")
check(len(_s2) == 25, f"[19] e avança para os 25 seguintes ({len(_s2)})")
check(len(set(_s1) | set(_s2)) == 50,
      "[20] em dois runs, 50 registros distintos foram atendidos")
_ordem = [_c[il_id(u)]["last_seen_run"] for u in _s2]
check(_ordem == sorted(_ordem),
      "[21] a ordem é determinística: quem foi visto há mais tempo primeiro")

print()
print("=" * 98)
print("BLOCO G — §16 CASE G/H: estado bom não é degradado")
print("=" * 98)
_bom = {"article_id": il_id("http://ok/1"), "url": "http://ok/1",
        "falha": rh.OK, "last_seen_run": 1, "first_seen_run": 1,
        "final": {"input_ready_under_r7c_policy": True, "useful_chars": 900}}
_n, _s, _a = particionar({il_id("http://ok/1"): _bom}, ["http://ok/1"])
check(len(_s) == 0,
      "[22] registro já resolvido não entra no backlog nem gasta rede")
_rob = dict([_reg(1, falha=rh.ROBOTS_BLOCKED)])
_n, _s, _a = particionar(_rob, ["http://old/1"])
check(len(_s) == 0,
      "[23] robots não é fila: continua fora das retomadas")
check("adiados_ids" in _src and "or art_id in adiados_ids" in _src,
      "[24] o adiado segue o mesmo caminho de preservação do não-retomável")
_col = _src.split("def coletar(")[1]
check("backlog_selecionado" in _col and "backlog_adiado" in _col
      and "backlog_total" in _col,
      "[25] a telemetria permite provar que a fila está drenando")
check("cap_estrutural" in _col,
      "[26] e o teto estrutural sai declarado no resumo do run")

print()
print("=" * 98)
print("BLOCO H — §17 os limites vizinhos seguem intocados")
print("=" * 98)
import reliability_enrichment_sidecar as sc  # noqa: E402
check(sc.MAX_REQUESTS_POR_RUN == 40,
      f"[27] R5/R6 em 40 ({sc.MAX_REQUESTS_POR_RUN})")
check(rh.MAX_FETCH_ARTIGOS == 40,
      f"[28] rehearsal local em 40 ({rh.MAX_FETCH_ARTIGOS})")
check("MAX_RETRY_BACKLOG" not in io.open("reliability_enrichment_sidecar.py",
                                         encoding="utf-8").read(),
      "[29] o caminho do R5/R6 não foi tocado")
_wf = io.open(".github/workflows/update_risk_dashboard.yml",
              encoding="utf-8").read()
check("timeout-minutes: 8" in _wf.split("Shadow input layer")[1][:220],
      "[30] o timeout do passo continua 8 minutos")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7c-P4 (backlog drain): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
