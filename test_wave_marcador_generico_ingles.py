#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_marcador_generico_ingles.py — `acquisition` não identifica a operação.

O CASO

"Could AES Acquisition Change EQT Corporation's Future Prospects?" (fev/2026) e
"EQT Corporation: Another Acquisition" (jul/2026) são dois negócios distintos —
a segunda manchete diz literalmente *Another*. Estavam na MESMA ocorrência,
unidos acima do gap de 45 dias por 160 dias de distância, apoiados numa única
palavra compartilhada: `acquisition`.

A CAUSA

`_STOP_MARCADORES` nasceu num corpus só em português. `aquisicao` está lá desde
sempre; o equivalente inglês nunca esteve. Como a extração pega palavras
capitalizadas, "Acquisition" de manchete inglesa virava marcador de identidade
de transação — a mesma função que `Jirau` ou `Marfrig` exercem legitimamente.

POR QUE SÓ UM TOKEN

O corpus tem 22 genéricos ingleses de transação/valor entre os marcadores. A
ablação token a token — cada um sozinho — mostrou que **apenas `acquisition`**
altera qualquer ocorrência, score, status ou família. Filtrar os 22 dá
exatamente o mesmo resultado que filtrar só este. Os outros 21 são dormentes:
adicioná-los mudaria o comportamento futuro sem nenhum caso atual que valide a
mudança. Ficam auditados e fora de produção — é escolha, não esquecimento.

`with` também não entrou, embora sustente a última união fraca acima do gap
(Halliburton). Ele não corrige nada hoje e é palavra gramatical, cuja interação
com a extração atual não foi medida.

O QUE ESTA ONDA NÃO FAZ

Não corrige o over-merge por encadeamento temporal. Capital One segue misturando
Brex e Discover; Janus segue com Deal A e Deal B juntos; Petrobras em 1 para 3;
Sabesp em 2 para 3; Halliburton em 2 para 4. Este arquivo grava verdade fatual e
contagem do resolvedor em colunas separadas, e falha se alguém as igualar.
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
H = json.load(io.open("risk_history.json", encoding="utf-8"))
SRC = io.open("risk_dashboard.py", encoding="utf-8").read()
GAP = 45 * 86400


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def marc(t, e):
    return {m for m in rd._marcadores_operacao(t, e, AL.get(e)).split("|") if m}


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


def composicao(g):
    """Composição por DATA, não por id de ocorrência — o id é derivado de ordem."""
    return sorted(tuple(sorted(time.strftime("%Y-%m-%d", time.gmtime(o["pub_ts"]))
                               for o in v)) for v in g.values())


_ORIG_STOP = set(rd._STOP_MARCADORES)


def sem_o_filtro(fn):
    """Executa `fn` com o comportamento ANTERIOR (sem `acquisition` filtrado)."""
    rd._STOP_MARCADORES = _ORIG_STOP - {"acquisition"}
    try:
        return fn()
    finally:
        rd._STOP_MARCADORES = set(_ORIG_STOP)


print("=" * 98)
print("§23/§24 A MUDANÇA É EXATAMENTE UM TOKEN")
print("=" * 98)
check(len(rd._STOP_MARCADORES) == 30,
      f"[1] `_STOP_MARCADORES` tem 30 entradas ({len(rd._STOP_MARCADORES)})")
check("acquisition" in rd._STOP_MARCADORES, "[2] e `acquisition` está entre elas")
check(_ORIG_STOP - {"acquisition"} == {
    "fato", "relevante", "comunicado", "mercado", "acoes", "acao", "oferta",
    "emissao", "debentures", "aquisicao", "venda", "compra", "empresa", "grupo",
    "banco", "companhia", "sa", "reais", "bilhoes", "milhoes", "follow", "on",
    "capital", "social", "conselho", "diretoria", "resultado", "lucro", "receita"},
      "[3] as 29 entradas originais estão todas preservadas, sem reordenação")
_dormentes = {"merger", "billion", "million", "strategic", "investment",
              "announces", "announcement", "completes", "equity", "offers",
              "stock", "shares", "stakes", "deals", "revised", "amended",
              "close", "boosts", "earnings", "profit", "outlook"}
_intrusos = sorted(_dormentes & rd._STOP_MARCADORES)
check(not _intrusos,
      f"[4] §9/§26 nenhum dos 21 genéricos dormentes entrou ({_intrusos or 'nenhum'})")
check("with" not in rd._STOP_MARCADORES, "[5] §25 `with` NÃO foi adicionado")
check("nasdaq" not in rd._STOP_MARCADORES and "cade" not in rd._STOP_MARCADORES,
      "[6] nenhum token de fonte/regulador foi adicionado de carona")

print()
print("=" * 98)
print("§24 NORMALIZAÇÃO — UMA ENTRADA MINÚSCULA COBRE A MANCHETE CAPITALIZADA")
print("=" * 98)
check(rd.normalize("Acquisition") == "acquisition",
      "[7] `normalize` rebaixa a caixa antes da comparação")
check("acquisition" not in marc("EQT Corporation: Another Acquisition", "EQT Corporation"),
      "[8] logo 'Acquisition' capitalizado é filtrado sem entrada duplicada")
check("Acquisition" not in rd._STOP_MARCADORES,
      "[9] e nenhuma variante capitalizada foi adicionada")

print()
print("=" * 98)
print("§18/§19/§20/§21 O QUE NÃO MUDOU")
print("=" * 98)
_corpo = SRC[SRC.index("def _marcadores_operacao"):]
_corpo = _corpo[:_corpo.index("def occurrence_identity")]
_codigo = "\n".join(l for l in _corpo.splitlines() if not l.lstrip().startswith("#"))
check("[:2]" not in _codigo, "[10] a remoção do cap continua valendo")
check('"|".join(sorted(set(out)))' in _codigo, "[11] a seleção final é a mesma")
_clu = SRC[SRC.index("def assign_occurrence_clusters"):]
_clu = _clu[:_clu.index("_DIRECTION_LABELS")]
check("gap_days: int = 45" in _clu, "[12] o gap continua 45 dias")
check("novo = anterior is None or (ts - anterior) > limite" in _clu,
      "[13] o passo temporal é o mesmo")
check(_clu.count("marcas[a] and marcas[b] and (marcas[a] & marcas[b])") == 1,
      "[14] a união por marcador acima do gap é a mesma")
check("eh_forte" not in SRC and "MARCADOR_FRACO" not in SRC
      and "marcador_forte" not in SRC,
      "[15] §37 nenhuma arquitetura de marcador forte/fraco entrou em produção")
_nomes = ("EQT", "Chart", "Brex", "Discover", "Evolve", "Marfrig", "Jirau")
_linhas_cod = [l for l in SRC.splitlines()
               if not l.lstrip().startswith("#") and '"""' not in l]
check(not any(n in l for l in _linhas_cod for n in ("Brex", "Evolve", "Discover")),
      "[16] §22 nenhum nome de empresa/negócio no código — só em comentário")

print()
print("=" * 98)
print("§13/§28 EQT — O CONTROLE POSITIVO")
print("=" * 98)
_eqt = ocorrencias("EQT Corporation")
_arts = sorted((o for v in _eqt.values() for o in v), key=lambda z: z["pub_ts"])
check(len(_arts) == 2, f"[17] a EQT tem 2 artigos de `ma` ({len(_arts)})")
check("AES" in _arts[0]["title"] and "Another" in _arts[1]["title"],
      "[18] e são 'Could AES Acquisition…' e '…Another Acquisition' — "
      "duas transações distintas, verdade fatual = 2")
check((_arts[1]["pub_ts"] - _arts[0]["pub_ts"]) > GAP,
      f"[19] separados por {int((_arts[1]['pub_ts']-_arts[0]['pub_ts'])/86400)} dias, "
      "muito acima do gap — só um marcador comum poderia uni-los")
_antes_eqt = sem_o_filtro(lambda: composicao(ocorrencias("EQT Corporation")))
check(len(_antes_eqt) == 1,
      f"[20] sem o filtro eram UMA ocorrência ({len(_antes_eqt)}) — a checagem "
      "seguinte não é vácua")
check(len(_eqt) == 2, f"[21] com o filtro são DUAS ({len(_eqt)})")
check(composicao(_eqt) == [("2026-02-13",), ("2026-07-24",)],
      f"[22] e cada transação fica sozinha ({composicao(_eqt)})")
_m0 = sem_o_filtro(lambda: marc(_arts[0]["title"], "EQT Corporation"))
_m1 = sem_o_filtro(lambda: marc(_arts[1]["title"], "EQT Corporation"))
check(_m0 & _m1 == {"acquisition"},
      f"[23] antes o ÚNICO marcador comum era `acquisition` ({sorted(_m0 & _m1)})")
check(not (marc(_arts[0]["title"], "EQT Corporation")
           & marc(_arts[1]["title"], "EQT Corporation")),
      "[24] depois não sobra nenhum marcador comum entre os dois artigos")

print()
print("=" * 98)
print("§12/§29 BAKER HUGHES — O CONTROLE DURO")
print("=" * 98)
_bh = ocorrencias("Baker Hughes")
_bha = sorted((o for v in _bh.values() for o in v), key=lambda z: z["pub_ts"])
check(len(_bha) == 7, f"[25] os 7 artigos de Chart Industries seguem presentes ({len(_bha)})")
check(len(_bh) == 1, f"[26] e continuam em UMA ocorrência ({len(_bh)})")
check(composicao(_bh) == [tuple(sorted(time.strftime("%Y-%m-%d", time.gmtime(o["pub_ts"]))
                                       for o in _bha))],
      "[27] nenhum artigo se perdeu ou se separou")
_sem_ent = [o for o in _bha if not ({"chart", "industries"} & marc(o["title"], "Baker Hughes"))]
check(len(_sem_ent) == 2,
      f"[28] DOIS artigos nunca nomeiam a contraparte ({len(_sem_ent)}) — "
      "'Timing An Acquisition Close Perfectly' e 'EU Scrutiny Puts…'. Eram eles "
      "que dependiam de `acquisition` para se ligar ao resto")
check(all(any((_bha[i + 1]["pub_ts"] - _bha[i]["pub_ts"]) <= GAP
              for i in range(len(_bha) - 1)) for _ in [0]),
      "[29] e o que os mantém juntos é o encadeamento temporal + `chart`, "
      "não `acquisition` — por isso o filtro não quebra o negócio")

print()
print("=" * 98)
print("§30 CONTROLES DE IDENTIDADE — FATUAL × RESOLVEDOR")
print("=" * 98)
# (verdade fatual adjudicada, contagem esperada do resolvedor DEPOIS desta onda)
CONTROLES = {
    "Baker Hughes":          (1, 1),
    "Smart Fit":             (1, 1),
    "BRF":                   (1, 1),
    "Eneva":                 (1, 1),
    "Engie Brasil":          (1, 1),
    "Rumo":                  (1, 1),
    "Usiminas":              (1, 1),
    "EQT Corporation":       (2, 2),
    "Capital One Financial": (2, 2),
    "Janus":                 (2, 1),
    "Petrobras":             (3, 1),
    "Sabesp":                (3, 2),
    "Halliburton":           (4, 2),
    "JPMorgan Chase":        (None, 2),
}
_n = 30
for emp, (fatual, esperado) in CONTROLES.items():
    obtido = len(ocorrencias(emp))
    nota = "" if fatual in (None, esperado) else f"  [encadeamento temporal aberto: fatual={fatual}]"
    check(obtido == esperado, f"[{_n}] {emp}: resolvedor = {esperado} ({obtido}){nota}")
    _n += 1
_divergentes = {e for e, (f, x) in CONTROLES.items() if f is not None and f != x}
check(_divergentes == {"Janus", "Petrobras", "Sabesp", "Halliburton"},
      f"[{_n}] §20 quatro controles têm verdade fatual DIFERENTE do resolvedor "
      f"({sorted(_divergentes)}) — esta onda não resolve encadeamento temporal, e "
      "igualar as colunas seria gravar o defeito como se fosse correção")
_n += 1

print()
print("=" * 98)
print("§15/§16/§21 NÃO PIORADOS — COMPOSIÇÃO IDÊNTICA À DE ANTES DO FILTRO")
print("=" * 98)
for emp in ("Capital One Financial", "Janus", "Petrobras", "Sabesp",
            "Halliburton", "JPMorgan Chase", "Smart Fit", "BRF", "Eneva",
            "Engie Brasil", "Rumo", "Usiminas", "Baker Hughes"):
    antes = sem_o_filtro(lambda e=emp: composicao(ocorrencias(e)))
    check(antes == composicao(ocorrencias(emp)),
          f"[{_n}] {emp}: composição inalterada pelo filtro")
    _n += 1

print()
print("=" * 98)
print("§31/§32/§33/§34/§35 BLAST")
print("=" * 98)
_emps = sorted({e for a in H["articles"].values()
                for e, evs in (a.get("events_by_company") or {}).items()
                if "ma" in (evs or [])})
_tot = sum(len(ocorrencias(e)) for e in _emps)
_tot_antes = sem_o_filtro(lambda: sum(len(ocorrencias(e)) for e in _emps))
check(_tot_antes == 63, f"[{_n}] eram 63 ocorrências M&A ({_tot_antes})")
_n += 1
check(_tot == 64, f"[{_n}] agora são 64 ({_tot})")
_n += 1
_mud = sem_o_filtro(lambda: {e: composicao(ocorrencias(e)) for e in _emps})
_dep = {e: composicao(ocorrencias(e)) for e in _emps}
_diff = sorted(e for e in _emps if _mud[e] != _dep[e])
check(_diff == ["EQT Corporation"],
      f"[{_n}] EXATAMENTE uma empresa mudou, e é a EQT ({_diff})")
_n += 1

_ev = {x["company"]: x for x in rd.build_evolution(H, cfg, 365)}
_ev_antes = sem_o_filtro(lambda: {x["company"]: x
                                  for x in rd.build_evolution(H, cfg, 365)})
_sc = sorted(c for c in _ev if c in _ev_antes
             and _ev[c]["total_score"] != _ev_antes[c]["total_score"])
check(_sc == ["EQT Corporation"],
      f"[{_n}] só a EQT muda de score ({_sc})")
_n += 1
check((_ev_antes["EQT Corporation"]["total_score"],
       _ev["EQT Corporation"]["total_score"]) == (14, 15),
      f"[{_n}] EQT 14 → 15 — aumento correto por restaurar transação oculta "
      f"({_ev_antes['EQT Corporation']['total_score']} → "
      f"{_ev['EQT Corporation']['total_score']})")
_n += 1
check(_ev["EQT Corporation"]["status"] == _ev_antes["EQT Corporation"]["status"] == "monitorar",
      f"[{_n}] e o status não muda (monitorar)")
_n += 1
_st = [c for c in _ev if c in _ev_antes and _ev[c]["status"] != _ev_antes[c]["status"]]
check(not _st, f"[{_n}] nenhuma empresa muda de status ({_st or 'nenhuma'})")
_n += 1
_crit = sorted(c for c, v in _ev.items() if v.get("status") == "critico")
check(_crit == ["CIBanco", "Citigroup", "Pemex (Petróleos Mexicanos)",
                "TIM Brasil", "Tok&Stok"],
      f"[{_n}] inventário de críticos inalterado, 5 e os mesmos ({len(_crit)})")
_n += 1

print()
print("=" * 98)
print("§34/§35 FAMÍLIAS NÃO-M&A — DANO ZERO")
print("=" * 98)
_fams = set()
for a in H["articles"].values():
    for evs in (a.get("events_by_company") or {}).values():
        _fams |= set(evs or [])
_alvos = []
for f in sorted(_fams - {"ma"}):
    for e in sorted({x for a in H["articles"].values()
                     for x, ee in (a.get("events_by_company") or {}).items()
                     if f in (ee or [])}):
        _alvos.append((f, e))
_dano = [(f, e) for f, e in _alvos
         if sem_o_filtro(lambda f=f, e=e: composicao(ocorrencias(e, f)))
         != composicao(ocorrencias(e, f))]
check(len(_alvos) == 84,
      f"[{_n}] a varredura cobre 84 pares (família, empresa) fora de M&A ({len(_alvos)})")
_n += 1
check(not _dano, f"[{_n}] ZERO composição alterada fora de M&A ({_dano or 'nenhuma'})")
_n += 1
_tk = ocorrencias("Tok&Stok", "recuperacao_judicial")
check(len(_tk) == 1, f"[{_n}] Tok&Stok: os 2 artigos de RJ seguem em uma ocorrência ({len(_tk)})")
_n += 1
check((_ev["Tok&Stok"]["total_score"], _ev["Tok&Stok"]["status"])
      == (_ev_antes["Tok&Stok"]["total_score"], _ev_antes["Tok&Stok"]["status"])
      and _ev["Tok&Stok"]["status"] == "critico",
      f"[{_n}] Tok&Stok: score e status idênticos com e sem o filtro, e segue "
      f"crítico ({_ev_antes['Tok&Stok']['total_score']} -> "
      f"{_ev['Tok&Stok']['total_score']})")
_n += 1

print()
print("=" * 98)
print("§39 SEM MIGRAÇÃO DE HISTÓRICO E DEMAIS PRESERVAÇÕES")
print("=" * 98)
_amostra = list(H["articles"].values())[:400]
check(not any("marcadores" in str(a) for a in _amostra),
      f"[{_n}] o marcador continua derivado, não persistido")
_n += 1
check('diag1["added"] == 0' in SRC, f"[{_n}] G5 intacto")
_n += 1
# Score absoluto decai com o tempo — travá-lo faz a suíte apodrecer sozinha.
# O que esta onda precisa provar é INVARIÂNCIA sob o filtro.
for emp, st in (("BRF", "monitorar"), ("Smart Fit", "atencao"),
                ("Vale", "atencao"), ("Samarco Mineração", "monitorar")):
    check((_ev[emp]["total_score"], _ev[emp]["status"])
          == (_ev_antes[emp]["total_score"], _ev_antes[emp]["status"])
          and _ev[emp]["status"] == st,
          f"[{_n}] {emp} inalterado pelo filtro e ainda em `{st}` "
          f"({_ev_antes[emp]['total_score']} -> {_ev[emp]['total_score']})")
    _n += 1

print()
print("=" * 98)
print(f"RESULTADO MARCADOR GENÉRICO INGLÊS: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
