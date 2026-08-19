#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7i_dirigente_em_exercicio.py — 4I.2 R7i.

DIRIGENTE EM EXERCÍCIO ≠ EMISSOR.

A regra B8 (`individual_subject` / `R_PAPEL_NAO_SUJEITO`) já dizia que
"processo contra o ex-presidente do conselho DA Vale" não é evento da Vale.
Mas o padrão exigia `ex[- ]?`, e por isso

    "CVM abre processo contra presidente do Banco do Brasil
     após declaração sobre ações"

caía no default `direto` e PONTUAVA para o banco. O holdout adjudicou o caso
(#11) como NÃO pontuável: o alvo formal é a pessoa; o banco é o genitivo do
cargo.

O prefixo nunca foi a invariante. Ele era o formato léxico do único exemplo
que criou a regra (B8a, N=1) — o caso-semente da Vale é estruturalmente
idêntico ao do BB exceto por ele. A invariante real não distingue ocupante
atual de ex-ocupante:

    ato formal adverso CONTRA pessoa nomeada
    + monitorada só dentro do sintagma de afiliação
    ⇒ a companhia é contexto, não sujeito

CONTUDO — e este é o ponto que o arquivo inteiro existe para travar — tornar
o prefixo opcional E PARAR AÍ foi MEDIDO E REPROVADO. Sozinha, a mudança
suprimia quatro controles positivos legítimos: ações do próprio emissor, ato
em nome da companhia, divulgação corporativa oficial e "a companhia também é
investigada". Por isso a generalização vem acompanhada da promoção por
EVIDÊNCIA CORPORATIVA POSITIVA (`_EVIDENCIA_CORPORATIVA_PROMOVE` + menção da
monitorada fora do aposto de cargo).

Escopo de família INALTERADO: só `investigacao_regulatoria`. A auditoria
mostrou que 6 dos 11 candidatos de dirigente no corpus são `troca_ceo` —
família em que o evento É corporativo (precedente humano JBS/Wesley,
`company_role=SUBJECT`). Uma guarda global apagaria `troca_ceo` e `fraude`.

TICKER SOZINHO NÃO PROMOVE. "(BBAS3)" é aposto identificador da matéria, não
prova de que o emissor é alvo do ato.

Este arquivo NÃO escreve em nada: nem histórico, nem shadow, nem side-car.
"""
from __future__ import annotations

import copy
import io
import json
import re

import risk_dashboard as rd
import semantic_audit as sa
import semantic_v2_shadow as sh

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)

BB_ID = "061eb3b6f37708a1986e"
BB_TITULO = ("CVM abre processo contra presidente do Banco do Brasil "
             "após declaração sobre ações")
BB_EX = ("CVM abre processo contra ex-presidente do Banco do Brasil "
         "após declaração sobre ações")
B8_SEMENTE = ("CVM abre processo administrativo contra ex-presidente do "
              "conselho da Vale, diz jornal")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


def pontua(title, company, summary=None):
    """True se o evento continua pontuável para a empresa."""
    h = {"articles": {"u1": {"title": title, "summary": summary or title,
                             "source": "s", "domain": "exemplo.com",
                             "pub_ts": 1787076105, "pub_iso": "2026-08-18 15:01",
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return bool(rd.event_ids_for(h["articles"]["u1"], company))


def regras(title, company):
    h = {"articles": {"u1": {"title": title, "summary": title, "source": "s",
                             "domain": "exemplo.com", "pub_ts": 1787076105,
                             "pub_iso": "2026-08-18 15:01",
                             "companies": [company]}}, "run_count": 1}
    rd._reclassify_only_pass(h, cfg)
    return {d.get("regra")
            for d in (h["articles"]["u1"].get("semantic_discards") or [])}


def papel(title, company):
    return sa.detect_papel_nao_sujeito(title, company,
                                       AL.get(company) or [company])


print("=" * 98)
print("BLOCO A - a ANCORA: o caso #11 do holdout")
print("=" * 98)
check(not pontua(BB_TITULO, "Banco do Brasil"),
      "[1] o artigo do BB NAO pontua mais para o Banco do Brasil")
check(papel(BB_TITULO, "Banco do Brasil") == "individual_subject",
      "[2] e o papel reconhecido e `individual_subject`")
check("R_PAPEL_NAO_SUJEITO" in regras(BB_TITULO, "Banco do Brasil"),
      "[3] atribuido a R_PAPEL_NAO_SUJEITO — nenhum ID de regra novo")
check("R_AFILIACAO_INDIVIDUAL" not in regras(BB_TITULO, "Banco do Brasil"),
      "[4] e NAO a R_AFILIACAO_INDIVIDUAL, que nunca governou este caso")
_kw = sa._keywords_por_evento(cfg).get("investigacao_regulatoria") or []
check("CVM abre processo" in _kw,
      "[5] a familia continua sendo disparada pela keyword `CVM abre processo`")
check(sa.detect_individual_affiliation_role(
          BB_TITULO, "Banco do Brasil", AL["Banco do Brasil"], _kw) == "",
      "[6] `R_AFILIACAO_INDIVIDUAL` segue inerte aqui — nao foi tocada")

print()
print("=" * 98)
print("BLOCO B - o ex-dirigente continua contido (regressao B8)")
print("=" * 98)
check(not pontua(B8_SEMENTE, "Vale"),
      "[7] a semente B8 (ex-presidente do conselho da Vale) segue contida")
check(papel(B8_SEMENTE, "Vale") == "individual_subject",
      "[8] pelo mesmo papel de sempre")
check(not pontua(BB_EX, "Banco do Brasil"),
      "[9] e o BB com `ex-presidente` tambem")
check(pontua(BB_TITULO, "Banco do Brasil") == pontua(BB_EX, "Banco do Brasil"),
      "[10] METAMORFICA: `presidente` e `ex-presidente` dao o MESMO resultado "
      "— a idade do mandato deixou de ser a fronteira semantica")

print()
print("=" * 98)
print("BLOCO C - CONTROLES POSITIVOS: risco corporativo legitimo sobrevive")
print("=" * 98)
POSITIVOS = [
    # REAIS — extraidos do corpus retido
    ("Justiça condena executivos e ex-diretores da TIM por fraude fiscal em SC",
     "TIM Brasil", "REAL fraude: escopo de familia protege"),
    ("Christian Egan é escolhido como novo CEO da B3",
     "B3", "REAL troca_ceo: nomeacao"),
    ("Gonzalo Rueda Castillo es el nuevo gerente general de Cemento Yura",
     "Yura", "REAL troca_ceo: nomeacao ES"),
    ("Follow-on foi oportunidade, não necessidade, diz CFO da Isa Energia",
     "ISA Energia Brasil", "REAL follow_on: o CFO e comentarista"),
    ("CVM abre processo para apurar destituição de conselheiro da Vale",
     "Vale", "REAL: o objeto e um ATO SOCIETARIO da propria Vale"),
    ("Fraude nas Americanas: executivos de Itaú, Bradesco e Santander "
     "são alvo de buscas",
     "Itaú Unibanco", "REAL: nao ha `contra` — a guarda nao alcanca"),
    # SINTETICOS — buracos estruturais sem caso real no corpus
    ("CVM abre processo contra o Banco do Brasil e seu presidente após "
     "declaração sobre ações",
     "Banco do Brasil", "SINT co-alvo explicito"),
    ("CVM abre processo contra presidente do Banco do Brasil por manipulação "
     "das ações do Banco do Brasil",
     "Banco do Brasil", "SINT valores mobiliarios do proprio emissor"),
    ("CVM abre processo contra diretor da Vale por informação em fato "
     "relevante da Vale",
     "Vale", "SINT divulgacao corporativa oficial"),
    ("CVM abre processo contra presidente da Vale por declaração feita em "
     "nome da Vale",
     "Vale", "SINT capacidade institucional declarada"),
    ("CVM abre processo contra diretor da Vale e aponta responsabilidade "
     "da Vale",
     "Vale", "SINT responsabilidade corporativa afirmada"),
    ("CVM abre processo contra presidente do Santander Brasil; o Santander "
     "Brasil também é investigado",
     "Santander Brasil", "SINT mencao independente fora do aposto"),
    ("CVM abre processo contra a Vale e contra diretor da Vale",
     "Vale", "SINT empresa alvo + executivo"),
]
for i, (t, emp, nota) in enumerate(POSITIVOS, start=11):
    check(pontua(t, emp), f"[{i}] POSITIVO preservado — {nota}")

print()
print("=" * 98)
print("BLOCO D - CONTROLES NEGATIVOS: alvo-pessoa fica contido")
print("=" * 98)
NEGATIVOS = [
    (BB_TITULO, "Banco do Brasil", "REAL ancora BB (verdade humana)"),
    (B8_SEMENTE, "Vale", "REAL semente B8"),
    ("CVM abre processo contra CEO da Vale", "Vale", "CEO em exercicio"),
    ("CVM abre processo contra diretor da Vale por conduta pessoal",
     "Vale", "diretor atual, conduta pessoal"),
    ("CVM abre processo contra chairman do Santander Brasil",
     "Santander Brasil", "chairman atual"),
    ("CVM abre processo contra conselheiro da Vale por conduta pessoal",
     "Vale", "conselheiro atual"),
    ("CVM abre processo contra Fulano de Tal, presidente do Banco do Brasil",
     "Banco do Brasil", "nome proprio em aposto"),
]
for i, (t, emp, nota) in enumerate(NEGATIVOS, start=24):
    check(not pontua(t, emp), f"[{i}] NEGATIVO contido — {nota}")

print()
print("=" * 98)
print("BLOCO E - POLITICA DE TICKER: sozinho NUNCA promove")
print("=" * 98)
check(not pontua("CVM abre processo contra presidente do Banco do Brasil "
                 "(BBAS3)", "Banco do Brasil"),
      "[31] ticker entre parenteses nao promove o evento de volta")
check(not pontua("CVM abre processo contra ex-presidente do Banco do Brasil "
                 "(BBAS3)", "Banco do Brasil"),
      "[32] nem com ex-dirigente")
check(not pontua("CVM abre processo contra presidente da Vale (VALE3)", "Vale"),
      "[33] idem para outro emissor/ticker")
check(pontua("CVM abre processo contra presidente da Vale por manipulação "
             "das ações da Vale", "Vale"),
      "[34] mas o mesmo emissor DENTRO da construcao de objeto promove")
check(sa._e_ticker("BBAS3") and sa._e_ticker("SANB11") and sa._e_ticker("VALE3"),
      "[35] o reconhecedor de ticker e estrutural (maiusculas + digito)")
check(not sa._e_ticker("Vale") and not sa._e_ticker("Banco do Brasil"),
      "[36] e nao confunde razao social com ticker")

print()
print("=" * 98)
print("BLOCO F - PARES MINIMOS: so o membro positivo promove")
print("=" * 98)
PARES = [
    ("Par1", BB_TITULO, "Banco do Brasil",
     "CVM abre processo contra Banco do Brasil e seu presidente após "
     "declaração sobre ações", "Banco do Brasil"),
    ("Par2", "CVM abre processo contra CEO da Vale", "Vale",
     "CVM abre processo contra CEO da Vale por manipulação das ações da Vale",
     "Vale"),
    ("Par3", "CVM abre processo contra diretor do Santander Brasil",
     "Santander Brasil",
     "CVM abre processo contra diretor do Santander Brasil; o Santander "
     "Brasil também é investigado", "Santander Brasil"),
    ("Par4", "CVM abre processo contra presidente da Vale", "Vale",
     "CVM abre processo contra presidente da Vale por dado em comunicado "
     "da Vale", "Vale"),
]
for i, (rot, tn, en, tp, ep) in enumerate(PARES, start=37):
    check(not pontua(tn, en) and pontua(tp, ep),
          f"[{i}] {rot}: o negativo fica contido e SO o positivo promove")

print()
print("=" * 98)
print("BLOCO G - METAMORFICAS")
print("=" * 98)
check(pontua("CVM abre processo contra presidente da Vale", "Vale") ==
      pontua("CVM abre processo contra ex-presidente da Vale", "Vale") is False,
      "[41] atual <-> ex nao altera a nao-pontuabilidade de afiliacao pura")
check(pontua("CVM abre processo contra Vale e seu presidente", "Vale"),
      "[42] acrescentar co-alvo explicito promove")
check(not pontua("CVM abre processo contra presidente do Santander Brasil",
                 "Santander Brasil") and
      not pontua("CVM abre processo contra diretor do Santander Brasil",
                 "Santander Brasil"),
      "[43] trocar a identidade do dirigente preserva o resultado")
check(not rd.detect_companies(
          {"title": "CVM abre processo contra presidente do conselho",
           "summary": ""}, cfg["watchlist"]),
      "[44] sem a empresa no texto nao se cria atribuicao alguma")
check(pontua("Christian Egan é escolhido como novo CEO da B3", "B3"),
      "[45] trocar a familia para troca_ceo nao invoca a guarda")
check(pontua("Justiça condena executivos e ex-diretores da TIM por fraude "
             "fiscal em SC", "TIM Brasil"),
      "[46] nem para fraude")

print()
print("=" * 98)
print("BLOCO H - ESCOPO DE FAMILIA permanece `investigacao_regulatoria`")
print("=" * 98)
_alvo = ("Wesley Batista Filho será o novo CEO Global da JBS a partir de "
         "janeiro de 2027")
check(pontua(_alvo, "JBS"),
      "[47] o precedente humano JBS/Wesley (company_role=SUBJECT) segue "
      "pontuando")
check(papel("CVM abre processo contra presidente da Vale", "Vale") ==
      "individual_subject",
      "[48] a guarda de fato dispara na familia regulatoria")
_fora = [("Justiça condena diretor da Vale por fraude", "Vale"),
         ("Novo CEO da Vale assume em janeiro", "Vale")]
for i, (t, emp) in enumerate(_fora, start=49):
    check("R_PAPEL_NAO_SUJEITO" not in regras(t, emp) or pontua(t, emp),
          f"[{i}] a guarda nao suprime fora da familia regulatoria: {t[:44]!r}")

print()
print("=" * 98)
print("BLOCO I - YPF: a REGRA QUE DE FATO GOVERNA (premissa corrigida)")
print("=" * 98)
# A auditoria corrigiu a afirmacao anterior de que o artigo do Aconcagua seria
# suprimido por `R_AFILIACAO_INDIVIDUAL`. Cada artigo e testado contra a regra
# que REALMENTE atua nele — YPF e regressao ampla, nao prova do C3.
YPF = [
    ("Vista se uniría a un exCEO de YPF y a la dueña de Puma Energy para "
     "rescatar a una petrolera en default", "R_AFILIACAO_INDIVIDUAL"),
    ("“Sin reestructuración, Aconcagua irá a la quiebra”: el duro pronóstico "
     "del exCEO de YPF", "R_AFILIACAO_INDIVIDUAL"),
    ("El exCEO de YPF que busca recuperar Aconcagua tras la quiebra",
     "R_EVENTO_CITADO_COMO_PASSADO"),
]
for i, (t, esperada) in enumerate(YPF, start=51):
    r = regras(t, "YPF")
    check(not pontua(t, "YPF") and esperada in r,
          f"[{i}] YPF contido por {esperada} (obtido: {sorted(r)})")

print()
print("=" * 98)
print("BLOCO J - BLAST no corpus retido: EXATAMENTE 1 par muda")
print("=" * 98)
_H = json.load(io.open("risk_history.json", encoding="utf-8"))
_C3_PAT = copy.deepcopy(sa._PAPEL_NAO_SUJEITO["individual_subject"])
_C3_EV = copy.deepcopy(sa._EVIDENCIA_CORPORATIVA_PROMOVE)
_C3_FN = sa._mencao_independente_da_afiliacao
_C0_PAT = [_C3_PAT[0].replace(r"(?:o\s+|a\s+)?(?:ex[- ]?)?",
                              r"(?:o\s+|a\s+)?ex[- ]?")]


def _mapa(pre_edicao: bool) -> dict:
    if pre_edicao:
        sa._PAPEL_NAO_SUJEITO["individual_subject"] = _C0_PAT
        sa._EVIDENCIA_CORPORATIVA_PROMOVE = []
        sa._mencao_independente_da_afiliacao = lambda *a, **k: ""
    else:
        sa._PAPEL_NAO_SUJEITO["individual_subject"] = _C3_PAT
        sa._EVIDENCIA_CORPORATIVA_PROMOVE = _C3_EV
        sa._mencao_independente_da_afiliacao = _C3_FN
    h = copy.deepcopy(_H)
    rd._reclassify_only_pass(h, cfg)
    out = {}
    for url, rec in h["articles"].items():
        for emp in (rec.get("companies") or []):
            out[(url, emp)] = tuple(sorted(rd.event_ids_for(rec, emp) or []))
    return out


check(_C0_PAT != _C3_PAT, "[54] a simulacao do estado pre-edicao e efetiva")
_antes = _mapa(True)
_depois = _mapa(False)
sa._PAPEL_NAO_SUJEITO["individual_subject"] = _C3_PAT
sa._EVIDENCIA_CORPORATIVA_PROMOVE = _C3_EV
sa._mencao_independente_da_afiliacao = _C3_FN
_mud = [(k, _antes[k], _depois[k]) for k in _antes if _antes[k] != _depois.get(k)]
check(len(_mud) == 1, f"[55] exatamente 1 par artigo x empresa muda "
                      f"(obtido: {len(_mud)})")
if _mud:
    (_u, _emp), _a, _d = _mud[0]
    _rec = _H["articles"][_u]
    _aid = sh.id_artigo(_rec.get("url") or _u, _rec.get("title") or "")
    check(_aid == BB_ID, f"[56] e o par e exatamente a ancora BB ({_aid})")
    check(_emp == "Banco do Brasil", "[57] para o Banco do Brasil")
    check(list(_a) == ["investigacao_regulatoria"] and list(_d) == [],
          "[58] investigacao_regulatoria -> nenhum evento pontuavel")
for i, _fam in enumerate(("troca_ceo", "fraude", "follow_on", "ma",
                          "recomendacao_negativa"), start=59):
    _dif = [k for k in _antes if (_fam in _antes[k]) != (_fam in _depois.get(k, ()))]
    check(not _dif, f"[{i}] ZERO vazamento para a familia `{_fam}`")

print()
print("=" * 98)
print("BLOCO K - supervisao humana: o codigo passa a CONCORDAR com o holdout")
print("=" * 98)
_S = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_obs = _S["observacoes"]
_bb = [v for v in _obs.values() if (v.get("article_id") or "") == BB_ID]
check(_bb, "[64] o caso #11 continua no shadow")
if _bb:
    _hr = _bb[0]["human_review"]
    check(_hr["scoreable"] is False,
          "[65] a verdade humana segue `scoreable = False`")
    check(_hr["dimensoes_adjudicadas"]["company_role"] == "MENTIONED",
          "[66] e `company_role = MENTIONED` — INALTERADA por esta onda")
    check(not pontua(BB_TITULO, "Banco do Brasil"),
          "[67] e o replay do codigo atual CONCORDA: sem autoridade de score "
          "para o Banco do Brasil")
# O cron acrescenta observacoes prospectivas novas, e elas nascem SEM revisao —
# esse e o funcionamento correto da fila. Travar "exatamente 11 casos" media o
# calendario, nao a invariante. O que importa e que as 11 adjudicacoes
# sobrevivam a cada avanco de dado e que caso novo do cron NAO entre revisado
# por acidente.
_revisados = {v.get("article_id") for v in _obs.values() if v.get("human_review")}
check(len(_revisados) >= 11,
      f"[68] as adjudicacoes do holdout sobrevivem ao cron ({len(_revisados)})")
check(BB_ID in _revisados,
      "[69] inclusive a do caso #11, que esta onda tinha de preservar")
_ot = _S["occurrence_truth"]
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4,
      "[70] occurrence_truth intacto (10/21/4)")

print()
print("=" * 98)
print("BLOCO L - o que esta onda NAO pode ter tocado")
print("=" * 98)
import reliability_pilot_contract as _pc
check(list(_pc.COMPANY_ROLE) == ["SUBJECT", "BUYER", "SELLER", "TARGET",
                                 "INVESTOR", "CREDITOR", "DEBTOR", "VICTIM",
                                 "PERPETRATOR", "MENTIONED", "UNRELATED",
                                 "UNKNOWN"],
      "[71] o enum company_role permanece IDENTICO — o gap segue aberto")
check("pessoa" not in _pc.PROMPT_AUDIT.lower()
      and "dirigente" not in _pc.PROMPT_AUDIT.lower(),
      "[72] o prompt do Contract V2 NAO foi alterado nesta onda")
# A versao original desta checagem afirmava que o historico PERSISTIDO ainda
# continha o registro antigo — era a prova de que a onda de CODIGO nao havia
# tocado em dado. Aquele estado era transitorio de proposito: a onda seguinte,
# autorizada, alinhou o registro pelo caminho canonico `--reclassify-only`.
# Manter a assercao anterior seria travar o repositorio no meio do caminho, e
# apaga-la perderia cobertura. Ela vira, entao, a assercao mais forte: as TRES
# autoridades concordam na pontuabilidade.
_hist_bb = [r for u, r in _H["articles"].items()
            if sh.id_artigo(r.get("url") or u, r.get("title") or "") == BB_ID]
check(_hist_bb, "[73a] o artigo permanece no historico — corrigir atribuicao "
                "nao e apagar historia")
check(_hist_bb and not rd.event_ids_for(_hist_bb[0], "Banco do Brasil"),
      "[73b] e o PERSISTIDO nao concede mais autoridade de score ao BB: "
      "verdade humana == codigo atual == producao")
check("R_PAPEL_NAO_SUJEITO" in io.open("reliability_taxonomy_inventory.py",
                                       encoding="utf-8").read(),
      "[74] o ID de regra continua registrado no inventario de taxonomia")
_inv = io.open("reliability_taxonomy_inventory.py", encoding="utf-8").read()
check("R_DIRIGENTE" not in _inv and "R_OFFICER" not in _inv,
      "[75] nenhum ID de regra de producao novo foi criado")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7i (dirigente em exercicio): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
