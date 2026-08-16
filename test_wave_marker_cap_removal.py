#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_marker_cap_removal.py — o marcador da operação não é mais truncado.

O CASO

"Smart Fit Avança no Centro-Oeste Com Aquisição da Evolve" produzia o marcador
`avanca|centro`. `evolve` — o único token que diz QUAL operação é — era
descartado. Sem ele, o artigo de dezembro não se reunia aos de julho e agosto
sobre a mesma aquisição, e a Smart Fit aparecia com DUAS aquisições da Evolve.

A CAUSA

`_marcadores_operacao` terminava em `"|".join(sorted(set(out))[:2])`. O `[:2]`
guardava as duas primeiras em ordem ALFABÉTICA, que não é ordem de relevância:
`avanca` e `centro` vencem `evolve` por acaso do alfabeto. Em manchete inglesa o
efeito é sistemático, porque `Acquisition` e `Billion` ordenam antes do nome da
contraparte — `chart`, `brex`, `discover` eram perdidos do mesmo jeito.

O QUE ESTA ONDA FAZ, E O QUE NÃO FAZ

Remove só a truncagem. Os filtros (stopwords, aliases, fase), a deduplicação e
a ordenação determinística ficam idênticos. O consumidor
(`assign_occurrence_clusters`, passo 3) já fazia `split("|")` para um set e
sempre aceitou qualquer cardinalidade — o cap nunca foi requisito de interface.

NÃO resolve o over-merge por encadeamento temporal: Petrobras segue em 1
ocorrência para 3 negócios, Sabesp em 2 para 3. Este arquivo registra os dois
números lado a lado — verdade fatual e comportamento do resolvedor — justamente
para que ninguém leia esta onda como se ela tivesse resolvido aquilo.

E NÃO mexe em `_STOP_MARCADORES`: a ausência de vocabulário genérico inglês é um
defeito confirmado, mas removê-lo quebrou a coesão de Baker Hughes no protótipo
B. Fica para uma onda própria, com medição própria.
"""
from __future__ import annotations

import io
import json
import re
import time

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
SRC = io.open("risk_dashboard.py", encoding="utf-8").read()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def marc(titulo, emissor):
    return {m for m in rd._marcadores_operacao(
        titulo, emissor, AL.get(emissor)).split("|") if m}


_STOP_ATUAL = set(rd._STOP_MARCADORES)


def truncado(titulo, emissor):
    """Reconstrução do comportamento HISTÓRICO, anterior a esta onda.

    Precisa desfazer duas coisas, não uma: o `[:2]` (esta onda) e o filtro de
    `acquisition` (onda seguinte, que passou a filtrá-lo). Reconstruir só o
    `[:2]` sobre o vocabulário de hoje descreveria um mundo que nunca existiu.
    """
    rd._STOP_MARCADORES = _STOP_ATUAL - {"acquisition"}
    try:
        return set(sorted(marc(titulo, emissor))[:2])
    finally:
        rd._STOP_MARCADORES = set(_STOP_ATUAL)


def com_cap(fn):
    """Executa `fn` com o `[:2]` de volta, mantendo o vocabulário de HOJE.
    Mede o que a remoção do cap sustenta agora, não o que sustentava então."""
    _orig = rd._marcadores_operacao
    rd._marcadores_operacao = lambda t, e, a: "|".join(
        sorted({m for m in _orig(t, e, a).split("|") if m})[:2])
    try:
        return fn()
    finally:
        rd._marcadores_operacao = _orig


print("=" * 98)
print("§4/§5 A MUDANÇA É EXATAMENTE A REMOÇÃO DO CAP")
print("=" * 98)
_corpo = SRC[SRC.index("def _marcadores_operacao"):]
_corpo = _corpo[:_corpo.index("def occurrence_identity")]
# só as linhas de CÓDIGO — o comentário do fix cita o `[:2]` para explicá-lo
_codigo = "\n".join(l for l in _corpo.splitlines() if not l.lstrip().startswith("#"))
check("[:2]" not in _codigo, "[1] o `[:2]` não existe mais no CÓDIGO de `_marcadores_operacao`")
check('"|".join(sorted(set(out)))' in _corpo,
      "[2] a seleção final é `\"|\".join(sorted(set(out)))`")
check("sorted(" in _corpo, "[3] a ordenação foi preservada (§5)")
check("set(out)" in _corpo, "[4] a deduplicação foi preservada (§11)")
check("_STOP_MARCADORES" in _corpo and "_FASE_HINTS" in _corpo,
      "[5] os filtros de stopword e de fase continuam aplicados")
check("normalize(a) for a in (list(aliases or []) + [emissor])" in _corpo,
      "[6] o filtro de alias do próprio emissor continua aplicado")
check(r're.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]{3,}", t)' in _corpo,
      "[7] a regex de extração é a mesma")

print()
print("=" * 98)
print("§6 ESTA ONDA NÃO MEXEU EM `_STOP_MARCADORES`")
print("=" * 98)
# Atualizado: uma onda posterior adicionou `acquisition` (e SÓ ele), com suíte
# própria — `test_wave_marcador_generico_ingles.py`. As checagens abaixo seguem
# provando que a remoção do cap não veio acompanhada de limpeza de vocabulário.
check(len(rd._STOP_MARCADORES) == 30,
      f"[8] a lista tem 30 entradas: as 29 originais + `acquisition` "
      f"({len(rd._STOP_MARCADORES)})")
_dormentes = {"merger", "agreement", "billion", "million", "announces",
              "boosts", "revised", "amended", "closing", "deal",
              "transaction", "stake", "shares", "equity", "strategic",
              "investment", "completes", "with"}
_intruso = sorted(_dormentes & rd._STOP_MARCADORES)
check(not _intruso,
      f"[9] e nenhum outro genérico entrou de carona ({_intruso or 'nenhum'})")
check("billion" in marc("Baker Hughes Company Issues $9.5 Billion in Senior "
                        "Unsecured Notes to Fund Acquisition of Chart Industries",
                        "Baker Hughes"),
      "[10] `billion` CONTINUA aparecendo — nenhuma onda até aqui o removeu, e "
      "um marcador feio mas inofensivo não é motivo para mudança semântica")

print()
print("=" * 98)
print("§7 CLUSTERING INTOCADO — SEM GUARDA DE INTERSEÇÃO, GAP INALTERADO")
print("=" * 98)
_clu = SRC[SRC.index("def assign_occurrence_clusters"):]
_clu = _clu[:_clu.index("_DIRECTION_LABELS")]
check("gap_days: int = 45" in _clu, "[11] o gap padrão continua 45 dias")
check("novo = anterior is None or (ts - anterior) > limite" in _clu,
      "[12] o passo temporal continua medindo o gap entre artigos CONSECUTIVOS")
check(_clu.count("marcas[a] and marcas[b] and (marcas[a] & marcas[b])") == 1,
      "[13] a união por marcador acima do gap (passo 3) é a mesma, e é única")
check("cluster_marcas" in _clu and "is_family_group and marcas_o" in _clu,
      "[14] o gate de marcador de LOCAL segue restrito a grupos de família")
check("inter" not in _clu.replace("interse", "").replace("_int", ""),
      "[15] nenhuma guarda de interseção foi introduzida no clusterizador")

print()
print("=" * 98)
print("§3 O CONSUMIDOR ACEITA N MARCADORES — NENHUMA DEPENDÊNCIA DE CARDINALIDADE")
print("=" * 98)
check('.split("|") if m}' in _clu,
      "[16] o consumidor converte a string em SET via split, sem supor tamanho")
check(not re.search(r'marcadores["\']?\]?\s*\[\s*[01]\s*\]', SRC),
      "[17] nenhum consumidor indexa `marcadores[0]`/`[1]`")
check(not re.search(r"\w+\s*,\s*\w+\s*=\s*.*marcadores", SRC),
      "[18] nenhum consumidor faz unpack de dois marcadores")
_ch = SRC[SRC.index("def _chave_forte"):]
check(SRC.count("_chave_forte") == 1,
      "[19] `_chave_forte` interpola a string inteira e segue sem chamador — "
      "a mudança não altera nenhuma chave viva")

print()
print("=" * 98)
print("§9 REGRESSÕES DIRETAS — TODO TOKEN QUE JÁ PASSAVA NOS FILTROS SOBREVIVE")
print("=" * 98)
BH = ("Baker Hughes Company Issues $9.5 Billion in Senior Unsecured Notes to "
      "Fund Acquisition of Chart Industries")
_bh = marc(BH, "Baker Hughes")
check("chart" in _bh, "[20] Baker Hughes: `chart` sobrevive")
check("industries" in _bh, "[21] Baker Hughes: `industries` sobrevive")
check(truncado(BH, "Baker Hughes") == {"acquisition", "billion"},
      f"[22] e ambos eram descartados pelo top-2, que ficava com "
      f"`acquisition|billion` ({sorted(truncado(BH, chr(66)+chr(97)+chr(107)+chr(101)+chr(114)+chr(32)+chr(72)+chr(117)+chr(103)+chr(104)+chr(101)+chr(115)))})")

CO_BREX = "Capital One's $5.15B Brex Acquisition Boosts Profit Outlook"
check("brex" in marc(CO_BREX, "Capital One Financial"),
      "[23] Capital One: `brex` sobrevive")
check(truncado(CO_BREX, "Capital One Financial") == {"acquisition", "boosts"},
      "[24] e era descartado antes, quando a identidade era `acquisition|boosts`")

CO_DISC = ("Capital One Faces Earnings Test After High-Stakes Discover "
           "Acquisition Bet")
check("discover" in marc(CO_DISC, "Capital One Financial"),
      "[25] Capital One: `discover` sobrevive")
check(truncado(CO_DISC, "Capital One Financial") == {"acquisition", "after"},
      "[26] e era descartado antes, quando a identidade era `acquisition|after`")

SF = "Smart Fit Avança no Centro-Oeste Com Aquisição da Evolve"
check("evolve" in marc(SF, "Smart Fit"), "[27] Smart Fit: `evolve` sobrevive")
check("evolve" not in truncado(SF, "Smart Fit"),
      "[28] e era descartado antes (`avanca|centro`) — a causa do falso split")

RUMO = "Como ficará a Ultrapar (UGPA3) com uma aquisição da Rumo (RAIL3)?"
_ru = marc(RUMO, "Rumo")
check("ultrapar" in _ru, "[29] Rumo: `ultrapar` sobrevive")
check(truncado(RUMO, "Rumo") == {"como", "ugpa3"},
      "[30] antes a identidade era `como|ugpa3` — uma palavra capitalizada só "
      "por iniciar a frase, mais o ticker")
check(_ru > truncado(RUMO, "Rumo"),
      "[31] e o conjunto novo é estritamente maior, nunca menor")

print()
print("=" * 98)
print("§10/§11/§12/§13 PROPRIEDADES DA FUNÇÃO")
print("=" * 98)
_rep = {rd._marcadores_operacao(BH, "Baker Hughes", AL.get("Baker Hughes"))
        for _ in range(50)}
check(len(_rep) == 1, "[32] determinismo: 50 execuções, uma única saída (§10)")
check(rd._marcadores_operacao(BH, "Baker Hughes", AL.get("Baker Hughes"))
      == "|".join(sorted(_bh)),
      "[33] a saída é a ordenação alfabética dos marcadores (§5)")
_dup = "Chart Industries Confirma Chart Industries Como Alvo da Chart Industries"
_md = rd._marcadores_operacao(_dup, "Baker Hughes", AL.get("Baker Hughes"))
check(_md.split("|").count("chart") == 1,
      "[34] deduplicação: token repetido aparece uma vez só (§11)")
check(len(_md.split("|")) == len(set(_md.split("|"))),
      "[35] nenhuma duplicata na serialização")
check(rd._marcadores_operacao("banco vende ações no mercado", "Itaú Unibanco",
                              AL.get("Itaú Unibanco")) == "",
      "[36] resultado vazio continua vazio — nenhum token inventado (§12)")
check(rd._marcadores_operacao("", "Petrobras", AL.get("Petrobras")) == "",
      "[37] título vazio continua devolvendo string vazia")
_multi = marc("Ternium conclui aquisição de ações da Nippon Steel e Mitsubishi "
              "Corporation na Usiminas segundo Bradesco BBI", "Usiminas")
check(len(_multi) >= 4,
      f"[38] caso com ≥4 marcadores sobreviventes devolve todos ({len(_multi)}) (§13)")
check(len(marc(("Capital One’s Discover Card Migration Reshapes Payments "
                "Economics And Merger Story"), "Capital One Financial")) > 2,
      "[39] regressão direta contra reintroduzir qualquer cap fixo")

print()
print("=" * 98)
print("§28/§29/§30 CORPUS INTEIRO — SÓ ADIÇÃO, NUNCA REMOÇÃO")
print("=" * 98)
H = json.load(io.open("risk_history.json", encoding="utf-8"))
pares = [(u, a.get("title") or "", emp)
         for u, a in H["articles"].items()
         for emp, evs in (a.get("events_by_company") or {}).items()
         if "ma" in (evs or [])]
check(len(pares) == 128, f"[40] 128 pares (artigo, empresa) com `ma` ({len(pares)})")
# A propriedade "só adiciona" é sobre O CAP, então o contrafactual tem de variar
# APENAS o cap. Medi-la contra o comportamento histórico misturaria esta onda com
# a remoção de `acquisition`, que legitimamente RETIRA um marcador — e a
# propriedade pareceria violada por um mérito de outra correção.
_so_cap = com_cap(lambda: {(u, e): marc(t, e) for u, t, e in pares})
_rem = [(e, t) for u, t, e in pares if _so_cap[(u, e)] - marc(t, e)]
check(not _rem, f"[41] REMOVED MARKERS = 0 no corpus inteiro ({len(_rem)})")
check(all(_so_cap[(u, e)] <= marc(t, e) for u, t, e in pares),
      "[42] AFTER ⊇ BEFORE em todos os pares — a propriedade que define a onda")
_add = [(e, t) for u, t, e in pares if marc(t, e) - _so_cap[(u, e)]]
check(len(_add) == 49, f"[43] 49 pares ganham marcador com o cap removido ({len(_add)})")
_card = [len(marc(t, e)) for _u, t, e in pares]
check(max(_card) == 10, f"[44] cardinalidade máxima observada = 10 ({max(_card)}) — "
                        "registrada, não limitada")
check(sum(1 for c in _card if c == 0) == 13,
      "[45] artigos sem marcador algum continuam 13 — a mudança não cria nem "
      "elimina marcador do nada")

print()
print("=" * 98)
print("§14/§15 SMART FIT — A REGRESSÃO DE PRODUÇÃO PRINCIPAL")
print("=" * 98)


def ocorrencias(empresa, familia="ma"):
    its = []
    for u, a in H["articles"].items():
        if familia in ((a.get("events_by_company") or {}).get(empresa) or []):
            its.append({"u": u, "event_id": familia, "pub_ts": a.get("pub_ts"),
                        "title": a.get("title") or "",
                        "_ident": rd.occurrence_identity(
                            a.get("title") or "", familia, empresa, AL.get(empresa))})
    its.sort(key=lambda x: x["pub_ts"])
    rd.assign_occurrence_clusters(its, 45, None, AL)
    g = {}
    for o in its:
        g.setdefault(o["_occ_key"], []).append(o)
    return g


_sf = ocorrencias("Smart Fit")
_arts = sorted((o for cl in _sf.values() for o in cl), key=lambda z: z["pub_ts"])
check(len(_arts) == 3, f"[46] a Smart Fit tem 3 artigos de `ma` ({len(_arts)})")
check(all("evolve" in (o["title"] or "").lower().replace("smartfit", "")
          for o in _arts),
      "[47] e os três são a MESMA aquisição, da Evolve — verdade fatual = 1 negócio")
check(len(_sf) == 1, f"[48] o resolvedor de PRODUÇÃO devolve 1 ocorrência ({len(_sf)})")
_ts = [o["pub_ts"] for o in _arts]
check((_ts[1] - _ts[0]) > 45 * 86400,
      "[49] e os dois primeiros estão a mais de 45 dias — quem os une é o "
      "marcador `evolve`, no passo 3, não o gap")
_orig = rd._marcadores_operacao
try:
    rd._marcadores_operacao = lambda t, e, a: "|".join(
        sorted({m for m in _orig(t, e, a).split("|") if m})[:2])
    _sf_antes = ocorrencias("Smart Fit")
finally:
    rd._marcadores_operacao = _orig
check(len(_sf_antes) == 2,
      f"[50] com o cap restaurado voltam 2 ocorrências ({len(_sf_antes)}) — "
      "a checagem [48] não é vácua")

# O score decai com o tempo; travar valor absoluto faz a suíte apodrecer sozinha
# (aconteceu: 36 virou 35 sem nenhuma mudança de código). O que a onda precisa
# provar é INVARIÂNCIA — a correção de ocorrência não move score.
_rot = 51
for w in (30, 90, 365):
    d = [x for x in rd.build_evolution(H, cfg, w) if x["company"] == "Smart Fit"]
    a = com_cap(lambda w=w: [x for x in rd.build_evolution(H, cfg, w)
                             if x["company"] == "Smart Fit"])
    _dep = (d[0]["total_score"], d[0]["status"]) if d else None
    _ant = (a[0]["total_score"], a[0]["status"]) if a else None
    check(_dep == _ant,
          f"[{_rot}] Smart Fit {w}d: score/status idênticos com e sem o cap "
          f"({_ant} -> {_dep})")
    _rot += 1
_ma365 = [b["contrib"] for b in
          [x for x in rd.build_evolution(H, cfg, 365) if x["company"] == "Smart Fit"][0]
          ["breakdown"] if b["label"] == "M&A"]
_ma365_antes = com_cap(lambda: [b["contrib"] for b in
                                [x for x in rd.build_evolution(H, cfg, 365)
                                 if x["company"] == "Smart Fit"][0]["breakdown"]
                                if b["label"] == "M&A"])
check(len(_ma365) == 1 and len(_ma365_antes) == 2,
      f"[54] e o painel mostra UMA linha de M&A, não duas "
      f"({_ma365_antes} -> {_ma365})")

print()
print("=" * 98)
print("§16-§27 DEZ CONTROLES DE IDENTIDADE")
print("=" * 98)
# (verdade fatual adjudicada, contagem esperada do resolvedor DEPOIS desta onda)
# As duas colunas são deliberadamente diferentes onde o over-merge temporal
# persiste. Igualar as duas seria afirmar que esta onda resolveu aquilo.
CONTROLES = {
    "Baker Hughes":          (1, 1),
    "Rumo":                  (1, 1),
    "Usiminas":              (1, 1),
    "BRF":                   (1, 1),
    "Eneva":                 (1, 1),
    "Engie Brasil":          (1, 1),
    "Smart Fit":             (1, 1),
    "Capital One Financial": (2, 2),
    "Janus":                 (2, 1),
    "Petrobras":             (3, 1),
    "Sabesp":                (3, 2),
    "NextEra Energy":        (None, 1),
    "BTG Pactual":           (None, 2),
}
_n = 55
for emp, (fatual, esperado) in CONTROLES.items():
    obtido = len(ocorrencias(emp))
    nota = "" if fatual in (None, esperado) else f"  [over-merge temporal aberto: fatual={fatual}]"
    check(obtido == esperado, f"[{_n}] {emp}: resolvedor = {esperado} ({obtido}){nota}")
    _n += 1
check(CONTROLES["Petrobras"][0] != CONTROLES["Petrobras"][1]
      and CONTROLES["Sabesp"][0] != CONTROLES["Sabesp"][1]
      and CONTROLES["Janus"][0] != CONTROLES["Janus"][1],
      f"[{_n}] Petrobras, Sabesp e Janus estão registrados com verdade fatual "
      "DIFERENTE do resolvedor — esta onda não resolve encadeamento temporal (§33)")
_n += 1

print()
print("=" * 98)
print("§32/§34/§35/§36 BLAST — OCORRÊNCIAS, SCORE, CRÍTICOS, FAMÍLIA CRUZADA")
print("=" * 98)
_emps = sorted({e for _u, _t, e in pares})
_tot = sum(len(ocorrencias(e)) for e in _emps)
check(_tot == 64, f"[{_n}] total de ocorrências M&A no corpus = 64 ({_tot})")
_n += 1
# Contrafactual medido sobre o vocabulário de HOJE: quantos falsos splits a
# ausência do cap impede agora. É afirmação mais forte que a original (uma
# empresa) e continua válida conforme o vocabulário evolui.
_antes_por_emp = com_cap(lambda: {e: len(ocorrencias(e)) for e in _emps})
_tot_antes = sum(_antes_por_emp.values())
_depois_por_emp = {e: len(ocorrencias(e)) for e in _emps}
_diff = {e: (_antes_por_emp[e], _depois_por_emp[e]) for e in _emps
         if _antes_por_emp[e] != _depois_por_emp[e]}
check(_tot_antes == 67, f"[{_n}] com o cap de volta seriam 67 ({_tot_antes})")
_n += 1
check(set(_diff) == {"Smart Fit", "British American Tobacco", "Halliburton"}
      and _diff["Smart Fit"] == (2, 1),
      f"[{_n}] o cap causaria 3 falsos splits hoje, entre eles o da Smart Fit "
      f"({_diff})")
_n += 1

_ev = {x["company"]: x for x in rd.build_evolution(H, cfg, 365)}
_crit = sorted(c for c, v in _ev.items() if v.get("status") == "critico")
check(len(_crit) == 5, f"[{_n}] inventário de críticos = 5 ({len(_crit)})")
_n += 1
check(_crit == ["CIBanco", "Citigroup", "Pemex (Petróleos Mexicanos)",
                "TIM Brasil", "Tok&Stok"],
      f"[{_n}] e são os mesmos cinco de sempre ({_crit})")
_n += 1
_tk = ocorrencias("Tok&Stok", "recuperacao_judicial")
check(len(_tk) == 1,
      f"[{_n}] Tok&Stok: os 2 artigos de RJ seguem em UMA ocorrência ({len(_tk)}) — "
      "o dano que a guarda de interseção causaria não foi introduzido (§8/§36)")
_n += 1
_ev_antes = com_cap(lambda: {x["company"]: x
                             for x in rd.build_evolution(H, cfg, 365)})
check((_ev["Tok&Stok"]["total_score"], _ev["Tok&Stok"]["status"])
      == (_ev_antes["Tok&Stok"]["total_score"], _ev_antes["Tok&Stok"]["status"])
      and _ev["Tok&Stok"]["status"] == "critico",
      f"[{_n}] Tok&Stok: score e status idênticos com e sem o cap, e segue "
      f"crítico ({_ev_antes['Tok&Stok']['total_score']} -> "
      f"{_ev['Tok&Stok']['total_score']})")
_n += 1
for emp in ("BRF", "Samarco Mineração"):
    check((_ev[emp]["total_score"], _ev[emp]["status"])
          == (_ev_antes[emp]["total_score"], _ev_antes[emp]["status"])
          and _ev[emp]["status"] == "monitorar",
          f"[{_n}] {emp} preservado, e inalterado pelo cap "
          f"({_ev_antes[emp]['total_score']} -> {_ev[emp]['total_score']}, "
          f"{_ev[emp]['status']})")
    _n += 1

print()
print("=" * 98)
print("§37 NENHUMA MIGRAÇÃO DE HISTÓRICO — O MARCADOR É DERIVADO")
print("=" * 98)
_amostra = list(H["articles"].values())[:400]
check(not any("marcadores" in str(a) for a in _amostra),
      f"[{_n}] `marcadores` não é persistido em `risk_history.json`")
_n += 1
check(not any("_occ_key" in str(a) for a in _amostra),
      f"[{_n}] `_occ_key` não é persistido — a ocorrência é recalculada a cada build")
_n += 1
check("_ident" not in set(k for a in _amostra for k in a.keys()),
      f"[{_n}] e `_ident` também não — nenhuma migração de dado é necessária")
_n += 1

print()
print("=" * 98)
print(f"RESULTADO MARKER CAP REMOVAL: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
