#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_b8_individual_subject.py — 4I.2 Wave B8.

INDIVÍDUO ≠ EMISSOR: quando o alvo formal de um ato regulatório é uma PESSOA
FÍSICA e a monitorada aparece apenas como vínculo profissional dela
("processo contra o ex-presidente do conselho DA Vale"), a companhia não
recebe `investigacao_regulatoria`.

Reusa a infraestrutura A7 (`detect_papel_nao_sujeito` / `_PAPEL_NAO_SUJEITO`),
mesmo padrão da B7b-5b, acrescentando o papel `individual_subject`.

Escopo deliberado:
  - família: SÓ `investigacao_regulatoria` (fraude/condenação/prisão têm
    semântica de responsabilização corporativa própria);
  - idioma: SÓ PT (única evidência real observada — B8a, N=1).

Precedência: se a companhia também é alvo formal, o evento continua dela.
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
                              "domain": "exemplo.com", "pub_ts": 1783483200,
                              "pub_iso": "2026-07-08 04:00", "companies": [company]}},
         "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return set(rd.event_ids_for(h["articles"]["u1"], company) or [])


def papel(t, emp):
    return sa.detect_papel_nao_sujeito(t, emp, AL.get(emp) or [emp])


_REAL = "CVM abre processo administrativo contra ex-presidente do conselho da Vale, diz jornal"

print("=" * 96)
print("BLOCO A — §17: o caso real Vale/Stieler")
print("=" * 96)
_got = pontua(_REAL, "Vale")
check("investigacao_regulatoria" not in _got,
      f"[1 caso real] Vale NÃO recebe investigacao_regulatoria (obtido {sorted(_got)})")
check(papel(_REAL, "Vale") == "individual_subject",
      "[2] papel auditável = 'individual_subject', via estrutura A7 existente")
check("Vale" in rd.detect_companies({"title": _REAL, "summary": ""}, cfg["watchlist"]),
      "[3 §17] Vale CONTINUA detectada como empresa mencionada — não apagamos o vínculo")

print()
print("=" * 96)
print("BLOCO B — §12: variações FALSE (indivíduo é o único alvo)")
print("=" * 96)
for _t in ("CVM abre investigação contra ex-diretor da Vale",
           "Processo é aberto contra ex-conselheiro da Vale",
           "CVM abre processo contra Daniel Stieler, ex-presidente do conselho da Vale",
           "Inquérito contra o ex-executivo da Vale avança na autarquia"):
    check("investigacao_regulatoria" not in pontua(_t, "Vale"),
          f"[FALSE] {_t[:62]}")

print()
print("=" * 96)
print("BLOCO C — §13/§5: TRUE controls obrigatórios (empresa é alvo)")
print("=" * 96)
_B = "CVM abre processo contra a Vale e seu ex-presidente"
check("investigacao_regulatoria" in pontua(_B, "Vale"),
      "[B] 'contra a Vale e seu ex-presidente' → Vale CONTINUA scoreable")
check(papel(_B, "Vale") == "", "[B2] guard de alvo próprio desarma o papel individual")
# Asserção no NÍVEL DO PAPEL — a camada que esta wave altera. A frase não é
# classificada como `investigacao_regulatoria` pela taxonomia de produção nem
# ANTES nem DEPOIS (BEFORE == AFTER, verificado); é limitação da camada de
# classificação, fora do escopo. O que importa aqui: o papel individual NÃO
# dispara, então nada seria desarmado.
_C = "CVM investiga a Vale por atos praticados por seu ex-diretor"
check(papel(_C, "Vale") == "",
      "[C] 'investiga a Vale por atos do ex-diretor' → papel individual NÃO dispara")
_D = "Vale é alvo de investigação da CVM; ex-presidente também é investigado"
check("investigacao_regulatoria" in pontua(_D, "Vale"),
      "[D] 'Vale é alvo de investigação' → Vale CONTINUA scoreable")
check(papel("Ex-presidente da Vale participa de seminário sobre mineração", "Vale") == "",
      "[E §13.4] menção a ex-presidente SEM ato formal não cria papel espúrio")
check(papel("Vale nomeia ex-diretor da Petrobras para o conselho", "Vale") == "",
      "[E2] cargo + vínculo sem ato formal contra ele → sem papel")

print()
print("=" * 96)
print("BLOCO D — §14: TRUE controls do gold (G109, G246, G262, G267)")
print("=" * 96)
_TIM = "Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC"
check("fraude" in pontua(_TIM, "TIM Brasil"),
      "[G109] TIM mantém `fraude` — condenação alcança a companhia")
_AMER = ("Fraude nas Americanas: executivos de Itaú, Bradesco e Santander são "
         "alvo de buscas")
for _c in ("Itaú Unibanco", "Bradesco", "Santander Brasil"):
    check("investigacao_regulatoria" in pontua(_AMER, _c),
          f"[G246/G262/G267] {_c} mantém investigacao_regulatoria")

print()
print("=" * 96)
print("BLOCO E — §8: escopo restrito a investigacao_regulatoria")
print("=" * 96)
# O escopo é restrito por DOIS mecanismos independentes:
# (1) estrutural — o pattern exige ato formal + "contra"; construções de
#     condenação/prisão simplesmente não casam;
# (2) explícito — o call site só honra o papel em `investigacao_regulatoria`.
check(papel("Justiça condena o ex-diretor da Vale por fraude", "Vale") == "",
      "[F] condenação (sem ato formal + 'contra') NÃO dispara o papel — escopo estrutural")
check(papel("Ex-diretor da Vale é preso em operação da PF", "Vale") == "",
      "[F2] prisão de ex-executivo também não dispara")
import inspect as _i
_src = _i.getsource(sa.resolve_article_semantics)
check('_papel == "individual_subject" and ev != "investigacao_regulatoria"' in _src,
      "[F3] call site restringe o papel a `investigacao_regulatoria` (§8)")

print()
print("=" * 96)
print("BLOCO F — regressões das waves anteriores")
print("=" * 96)
check("investigacao_regulatoria" in pontua(
        "CNBV abre investigación contra Banorte por irregularidades",
        "Grupo Financiero Banorte"), "[R1] B7b-3b/Banorte preservado")
check("investigacao_regulatoria" in pontua(
        "Ministerio del Trabajo abre investigación a Nutresa por presunta explotación",
        "Grupo Nutresa"), "[R2] Nutresa (A1b) preservada")
_G250 = ("AppLovin Rises 5% Premarket After Citigroup Recommends Buying The Stock "
         "Amid SEC Investigation Concerns")
check("investigacao_regulatoria" not in pontua(_G250, "Citigroup"),
      "[R3] B7b-5b/Citigroup preservado")
check(papel(_G250, "Citigroup") == "analista",
      "[R4] papel `analista` não foi capturado pelo novo padrão")
check("recuperacao_judicial" not in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale")
      and "recuperacao_judicial" in pontua(
        "Vale informa sobre Plano de Recuperação Judicial da Samarco",
        "Samarco Mineração"), "[R5] Vale/Samarco nos dois lados")
check(list(sa._PAPEL_NAO_SUJEITO) == ["vitima", "comentarista", "investigador",
                                       "analista", "individual_subject"],
      "[R6] A7 preservada: papéis antigos intactos, um único acrescentado")

print()
print("=" * 96)
print(f"RESULTADO WAVE B8: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    import sys
    sys.exit(1)
