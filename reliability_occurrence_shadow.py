#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_shadow.py — OCCURRENCE SHADOW V1 (opcao C, em sombra).

A onda de desenho (`1c2b43e`) recomendou a opcao C: uma fusao CONSCIENTE DE
OCORRENCIA, que produz ocorrencia com MEMBROS e FASES em vez de um sobrevivente
com corroboracao. Este modulo a implementa — e SOMENTE em sombra.

AUTORIDADE: NENHUMA. Nao pontua producao, nao decide ocorrencia de producao,
nao escreve em lugar nenhum, e nenhum caminho de producao o importa.

POR QUE O ID NAO PODE SER `company|family|objeto`
-------------------------------------------------
O documento de desenho propos identidade estavel a partir de empresa + familia +
objeto canonico. Isso NAO basta, e a propria verdade humana prova: a Hapvida tem
DUAS trocas de CEO reais em quatro meses (`troca_ceo:hapvida:3b55e5fc412d` x
`troca_ceo:hapvida:dc829e29aab1`, relacao DISTINCT_OCCURRENCE adjudicada). Mesma
empresa, mesma familia, mesmo tipo de objeto — dois eventos economicos distintos.

Por isso a identidade tem DUAS camadas separadas:

    IDENTIDADE DE OBJETO      que ativo/entidade/negocio esta envolvido
    INSTANCIA DE OCORRENCIA   QUAL evento economico sobre aquele objeto e este

A instancia deriva de features do ADAPTADOR DE FAMILIA (pessoa que entra/sai,
serie da emissao, alvo e valor da transacao). Onde as features sao fracas, ela
cai numa ancora de data de INICIACAO — e o registro marca
`id_stability: DATE_ANCHORED`, para que o checkpoint diga QUAIS ids sao estaveis
por conteudo e quais nao sao. Nunca se usa artigo mais recente, representante
mutavel, contagem de fontes nem indice de cluster.

O QUE ESTE MODULO NAO FAZ
-------------------------
Nao infere alias por similaridade de nome. `clark|kimberly` e `arbex|suzb3` sao
DISJUNTOS mesmo sem ruido: a Suzano so esta correta hoje porque a sobre-fusao a
uniu por acidente. Alias aqui e DECLARADO, com fonte, em
`occurrence_alias_shadow.json`.

LIMITACAO DECLARADA
-------------------
`build_evolution` recalcula a data efetiva de grupos de republicacao opt-in
(`canonical_pub_ts_by_key`) num escopo local nao reutilizavel de fora. A sombra
usa a data do proprio registro e MEDE a divergencia contra o representante da
producao em vez de fingir cobertura total (ver `fidelidade()`).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

import reliability_occurrence_reproducer as rp
import risk_dashboard as rd
import semantic_audit as sa
import semantic_v2_shadow as sh

SHADOW_VERSION = "occurrence.shadow.v1"
AUTORIDADE = {
    "production_score_authority": "NONE",
    "production_occurrence_authority": "NONE",
    "semantic_authority": "NONE",
    "write_authority": "NONE",
    "output_label": "SHADOW / SIMULATED",
}
ARQUIVO_ALIAS = "occurrence_alias_shadow.json"

# ── §7 · quatro estados de fase, mais UNKNOWN ────────────────────────────────
INICIACAO = "INICIACAO"
ETAPA = "ETAPA"
MATERIAL = "MATERIAL"
ACOMPANHAMENTO = "ACOMPANHAMENTO"
UNKNOWN = "UNKNOWN"
FASES = (INICIACAO, ETAPA, MATERIAL, ACOMPANHAMENTO, UNKNOWN)

# ── §13 · papel do marcador ──────────────────────────────────────────────────
# Nao e blacklist de empresa. Um marcador de REGULADOR continua util como
# CONTEXTO — ele so nao pode CARREGAR identidade de ocorrencia, porque aparece
# em transacoes distintas. Foi `cade` que uniu EMAE e Sanessol na Sabesp.
OBJECT_MARKER = "OBJECT_MARKER"
CONTEXT_MARKER = "CONTEXT_MARKER"
REGULATOR_MARKER = "REGULATOR_MARKER"
GENERIC_MARKER = "GENERIC_MARKER"

_REGULADORES = frozenset({
    "cade", "cvm", "anp", "aneel", "bacen", "susep", "antt", "antaq", "ans",
    "anatel", "accc", "sec", "ftc", "doj", "cofece", "sunass", "osinergmin",
    "indecopi", "superintendencia", "tribunal", "conselho", "justica",
    "supremo", "stf", "stj", "comissao", "reguladora", "agencia", "procon",
    "receita", "conar", "arsesp",
})
# Palavras que aparecem em QUALQUER noticia da familia e por isso nao
# identificam objeto nenhum.
_GENERICOS = frozenset({
    "apos", "ainda", "antes", "durante", "sobre", "contra", "entre", "para",
    "novo", "nova", "novos", "novas", "material", "relevante", "mercado",
    "brasil", "brasileira", "brasileiro", "milhoes", "milhao", "bilhoes",
    "bilhao", "participacao", "acoes", "reais", "dolares", "total", "parcial",
    "primeira", "segunda", "terceira", "maior", "menor", "melhor",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
    "exclusivo", "analise", "opiniao", "coluna", "entrevista", "veja",
    "confira", "saiba", "entenda", "balanco", "trimestre", "semestre",
    # palavras funcionais que so aparecem capitalizadas por abrirem a manchete
    # ("Como a Rumo...", "Quem assume...") — nunca identificam objeto
    "como", "quem", "onde", "quando", "porque", "depois", "desde", "apenas",
    "mesmo", "assim", "entao", "tambem", "enquanto", "embora", "sera",
})
# Termos de moldura noticiosa/analitica: contexto real, mas nao objeto.
_CONTEXTUAIS = frozenset({
    "reuters", "bloomberg", "valor", "estadao", "folha", "infomoney",
    "exame", "neofeed", "brazil", "journal", "times", "relatorio",
    "analistas", "analista", "investidores", "acionistas", "governo",
    "uniao", "europeia", "federal", "estadual", "municipal",
})


def papel_marcador(token: str) -> str:
    """Classifica o PAPEL de um marcador. So OBJECT_MARKER carrega identidade."""
    t = (token or "").strip().lower()
    if not t:
        return GENERIC_MARKER
    if t in _REGULADORES:
        return REGULATOR_MARKER
    if t in _GENERICOS or t.isdigit() or len(t) < 3:
        return GENERIC_MARKER
    if t in _CONTEXTUAIS:
        return CONTEXT_MARKER
    return OBJECT_MARKER


# `_marcadores_operacao` da producao so aceita nome proprio com 4+ caracteres, e
# por isso perde "Oma" — o objeto de uma das tres transacoes de M&A da JBS.
# Aqui se recupera o nome proprio de EXATAMENTE 3 letras, que na pratica e
# geografia (Oma, EUA sai por generico) ou sigla de ativo. Nada mais curto:
# duas letras seriam ruido garantido.
_RX_PROPRIO_CURTO = re.compile(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zà-ÿ]{2})\b")


def marcadores_por_papel(titulo: str, event_id: str, empresa: str,
                         aliases=None) -> dict:
    ident = rd.occurrence_identity(titulo or "", event_id, empresa, aliases)
    saida = {OBJECT_MARKER: set(), CONTEXT_MARKER: set(),
             REGULATOR_MARKER: set(), GENERIC_MARKER: set()}
    brutos = [m for m in (ident.get("marcadores") or "").split("|") if m]
    ali = {rd.normalize(a) for a in (list(aliases or []) + [empresa]) if a}
    for m in _RX_PROPRIO_CURTO.findall(titulo or ""):
        n = rd.normalize(m)
        if n not in ali and n not in rd._STOP_MARCADORES:
            brutos.append(n)
    for m in brutos:
        saida[papel_marcador(m)].add(m)
    return saida


# ── §7 · evidencia de fase ───────────────────────────────────────────────────
# Mapeia CONSERVADORAMENTE a evidencia bruta de `_fase_do_evento` mais sinais
# proprios. A evidencia bruta NUNCA e descartada: viaja em `fase_bruta`.
_RX_MATERIAL = re.compile(
    r"\bconclu[ií]\w*|\bconclus\w+|\bfinaliz\w*|\bencerr\w*|"
    r"\bcomplet(?:a|ou|es|ed)\b|\bcompletion\b|\bfecha(?:\b|mento)|"
    r"\bliquida\w*|\bassume\s+o\s+controle\b|\bwraps?\s+up\b|\bcloses?\b|"
    r"\bclosing\b|\bcierra\w*|\bculmina\w*", re.I)
_RX_ETAPA = re.compile(
    r"\baprova\w*|\bapprove\w*|\bapproval\b|\bautoriza\w*|\bhomologa\w*|"
    r"\bassembleia\b|\bliminar\b|\bcautelar\b|\brecurso\b|\bveto\w*|"
    r"\bscrutiny\b|\bprecifica\w*|\bbookbuilding\b|\bsem\s+restric\w+|"
    r"\bcom\s+restric\w+|\baceita\b|\bdefere\w*|\bacolhe\w*", re.I)
_RX_INICIACAO = re.compile(
    r"\banunci\w*|\bannounce\w*|\bassina\w*|\bfirma\w*|\bacord\w*|"
    r"\bprotocol\w*|\bpede\s+registro\b|\bsolicita\w*|\bnegocia\w*|"
    r"\bavalia\w*|\bestuda\w*|\blan[cç]\w*|\bprop[oõ]\w*|\boferta\b|"
    # `adquire` deixava de fora `adquirir`/`adquiriu`, e era justamente a forma
    # do anuncio do compromisso vinculante da Natura
    r"\bcompra\w*|\badquir\w*|\bagrees?\s+to\b|\bto\s+acquire\b|\bdeal\b",
    re.I)
_RX_ACOMPANHAMENTO = re.compile(
    r"\bcoment\w*|\bcomment\w*|\bopini\w*|\bapos\s+a?\s*(?:conclus|aquisic|fusao)|"
    r"\bimpacto\s+d[ao]\b|\breflete\w*|\bo\s+que\s+muda\b|\brelembr\w*|"
    r"\bretrospect\w*|\brecap\w*|\bexplica\w*|\bentenda\b|\banalistas?\s+v[eê]\w*|"
    r"\bwhat\s+it\s+means\b|\bconsequenci\w*|\bclientes?\s+(?:reclam|relat)\w*|"
    # fala ATRIBUIDA a porta-voz: o artigo relata o que alguem disse SOBRE o
    # evento, nao um novo estagio dele (ISA: "diz CFO da Isa Energia")
    r"\b(?:diz|afirma|declara|comenta|segundo)\s+(?:o\s+|a\s+)?"
    r"(?:ceo|cfo|coo|cro|diretor\w*|president\w*|analist\w*|banco\b)|"
    # consequencia para terceiro/consumidor, nao estagio do processo
    r"\bafeta\s+(?:quem|os?\s|as?\s)|\bquem\s+est[aá]\s+esperando\b", re.I)


# Familias cujo evento e PONTUAL: a propria assercao do fato e a iniciacao dele
# ("Moody's rebaixa o rating da X" nao tem verbo de fase, e nao precisa ter).
# Familias de PROCESSO (ma, follow_on, emissao, RJ) ficam de fora de proposito:
# ali a ausencia de evidencia de fase e informacao real, e vira UNKNOWN.
_FAMILIA_PONTUAL = frozenset({
    "rebaixamento_rating", "recomendacao_negativa", "investigacao_regulatoria",
    "incidente_operacional", "incidente_operacional_grave",
    "disrupcao_operacional", "guidance_negativo", "troca_ceo",
})


# §15/§17 — RECAPITULACAO COORDENADA, calibrada pela revisao humana de
# 2026-08-20 (Engie). "Engie Brasil lucra R$ 694 mi no 2o tri E CONCLUI
# follow-on" nao e um fechamento: a assercao PRIMARIA e o resultado
# trimestral, e a conclusao vem em oracao coordenada, referindo fato que JA
# ocorreu. A palavra `conclui` sozinha nao pode decidir fase — foi exatamente
# esse atalho que a revisao humana derrubou.
#
# O guarda nao e lexical sobre `conclui`: exige (a) assercao de OUTRO evento na
# oracao principal e (b) o verbo material depois da coordenacao.
_ASSERCAO_PRIMARIA_OUTRA = (
    r"lucr\w*|preju[ií]z\w*|resultad\w*|receita\w*|ebitda|balanc\w*|"
    r"fatur\w*|margem|dividend\w*|jcp\b|earnings|revenue|profit\w*|"
    r"posts?\b|reports?\b")
_RX_RECAP_COORDENADO = re.compile(
    r"\b(?:" + _ASSERCAO_PRIMARIA_OUTRA + r")\b[^,;]{0,80}?"
    r"\s+(?:e|and)\s+(?:tamb[eé]m\s+)?"
    r"(?:conclui\w*|finaliz\w*|encerr\w*|complet\w*|fecha\b)", re.I)
# §14 — vinculo economico EXPLICITO com um compromisso anterior. Nao e
# similaridade de nome: e o texto dizendo que este ato DECORRE daquele.
_RX_DECORRE_COMPROMISSO = re.compile(
    r"decorre\s+d[oa]s?\s+(?:compromisso|acordo|contrato)\w*|"
    r"em\s+cumprimento\s+a[o]?\s+(?:compromisso|acordo)|"
    r"nos\s+termos\s+d[oa]\s+(?:compromisso|acordo)|"
    r"pursuant\s+to\s+the\s+(?:binding\s+)?(?:commitment|agreement)|"
    r"under\s+the\s+(?:binding\s+)?commitment|"
    # §11 — atingir o piso COMPROMETIDO e cumprimento do compromisso, nao
    # etapa societaria. E a razao que a propria revisao humana deu para o
    # marco de 31/07 da Natura ser material, e nao a palavra "assembleia"
    # que aparece na mesma manchete.
    r"(?:m[ií]nimo|patamar|limiar|piso)\s+(?:estabelecid\w+\s+)?"
    r"d[oa]\s+(?:compromisso|acordo)|"
    r"atinge\s+o\s+(?:m[ií]nimo|patamar|limiar)\s+"
    r"(?:comprometid\w+|estabelecid\w+)", re.I)
# §23 — abertura de processo e identificador de processo
_RX_ABRE_PROCESSO = re.compile(
    r"\babre\s+(?:um\s+)?(?:processo|inqu[eé]rito|investiga\w+)|"
    r"\binstaura\w*\s+(?:processo|inqu[eé]rito)|"
    r"\bopens?\s+(?:an?\s+)?(?:proceeding|investigation|probe)", re.I)
_RX_PROCESSO_ID = re.compile(
    r"\b(?:processo|proc\.?|inqu[eé]rito|pas)\s*(?:administrativo\s*)?"
    r"(?:sancionador\s*)?n?[.ºo°]*\s*"
    r"(\d{2,5}[./]\d{3,6}[./-]?\d{0,6}(?:[-/]\d{1,4})?)", re.I)


# Familias em que a acao do ANALISTA e o proprio evento. Fora delas, uma casa
# reiterando recomendacao ou mexendo em preco-alvo esta COMENTANDO um fato, nao
# praticando-o: "UBS reitera recomendacao de compra para JBS apos proposta de
# aquisicao da PPC" nao e uma terceira transacao de M&A.
_FAMILIA_DE_ANALISTA = frozenset({"recomendacao_negativa", "rebaixamento_rating"})
_RX_ANALISTA_PRIMARIO = re.compile(
    r"^[^,;:]{0,60}?\b(?:reitera\w*|mant[eé]m|eleva\w*|reduz\w*|corta\b|"
    r"rebaix\w*|inicia\s+cobertura|reafirm\w*)\s+"
    r"(?:a\s+|o\s+|sua\s+)?(?:recomenda\w+|pre[cç]o[- ]alvo|rating|"
    r"cobertura|classifica\w+|target)", re.I)


def fase_de(titulo: str, family: str = "") -> dict:
    """Quatro estados + UNKNOWN. Nao forca classificacao sem evidencia."""
    t = titulo or ""
    bruta = rd._fase_do_evento(t)
    if family not in _FAMILIA_DE_ANALISTA:
        _an = _RX_ANALISTA_PRIMARIO.search(t)
        if _an:
            return {"fase": ACOMPANHAMENTO,
                    "fase_evidencia": "analista_como_assercao_primaria:"
                                      + _an.group(0)[:44],
                    "fase_bruta": bruta}
    if family == "troca_ceo":
        # reusa o guarda JA PUBLICADO em producao
        # (`R_TROCA_CEO_SEM_ASSERCAO`), em vez de reimplementar a deteccao de
        # descritor de cargo. Se a producao mudar de opiniao, a sombra muda
        # junto — que e o comportamento certo para uma sombra.
        _dc = sa.detect_troca_ceo_sem_assercao(t, t)
        if _dc:
            return {"fase": ACOMPANHAMENTO,
                    "fase_evidencia": "descritor_sem_assercao:" + _dc[:44],
                    "fase_bruta": bruta}
    _rc = _RX_RECAP_COORDENADO.search(t)
    if _rc:
        return {"fase": ACOMPANHAMENTO,
                "fase_evidencia": "recap_coordenado:" + _rc.group(0)[:52],
                "fase_bruta": bruta}
    # §11/§14 — ato que DECORRE de compromisso anterior e marco MATERIAL da
    # mesma transacao, nunca uma ocorrencia nova
    _dc = _RX_DECORRE_COMPROMISSO.search(t)
    if _dc:
        return {"fase": MATERIAL,
                "fase_evidencia": "decorre_de_compromisso:" + _dc.group(0)[:52],
                "fase_bruta": bruta}
    for rx, fase in ((_RX_MATERIAL, MATERIAL), (_RX_ACOMPANHAMENTO, ACOMPANHAMENTO),
                     (_RX_ETAPA, ETAPA), (_RX_INICIACAO, INICIACAO)):
        m = rx.search(t)
        if m:
            return {"fase": fase, "fase_evidencia": m.group(0), "fase_bruta": bruta}
    # a evidencia bruta da producao ainda pode salvar o caso
    _map = {"encerramento": MATERIAL, "aprovacao": ETAPA, "precificacao": ETAPA,
            "anuncio": INICIACAO}
    if bruta in _map:
        return {"fase": _map[bruta], "fase_evidencia": "bruta:" + bruta,
                "fase_bruta": bruta}
    if family in _FAMILIA_PONTUAL:
        return {"fase": INICIACAO, "fase_evidencia": "familia_pontual",
                "fase_bruta": bruta}
    return {"fase": UNKNOWN, "fase_evidencia": "", "fase_bruta": bruta}


# ── §24 · em M&A o objeto e o ALVO, nao qualquer parte nomeada ──────────────
# "JBS propoe aquisicao dos 18% restantes da Pilgrim's Pride" e "Pilgrim's
# Pride anuncia aquisicao da Walkers Deli" citam ambos a Pilgrim's — mas numa
# ela e ALVO e na outra e COMPRADORA. Somar os nomes proprios do titulo inteiro
# funde duas transacoes distintas; foi o que aconteceu no controle sintetico da
# JBS. O alvo e o que vem DEPOIS do verbo/substantivo de aquisicao.
#
# A pista so vale se for SEGUIDA de conectivo de complemento ("aquisição DA
# Walkers", "aquisição EM Omã"). Sem essa exigencia, "aquisição DECORRE do
# Compromisso Vinculante" tomaria "Compromisso Vinculante" como alvo — e foi
# assim que a primeira versao trocou o occurrence_id da Natura.
_RX_CUE_AQUISICAO = re.compile(
    r"\b(?:aquisi[cç][aã]o|aquisi[cç][oõ]es|compra|adquir\w*|"
    r"incorpora[cç][aã]o|takeover|acquisition|acquires?|to\s+acquire|"
    r"stake)\s+(?:d[aeo]s?|pel[ao]s?|em|of|in|no|na|nos|nas)\b", re.I)


def alvo_da_transacao(titulo: str, event_id: str, empresa: str,
                      aliases=None) -> set:
    """Marcadores de objeto que aparecem DEPOIS da pista de aquisicao. Conjunto
    vazio significa 'nao identificavel' — e o chamador cai no comportamento
    anterior em vez de inventar um alvo."""
    m = _RX_CUE_AQUISICAO.search(titulo or "")
    if not m:
        return set()
    depois = (titulo or "")[m.end():]
    if not depois.strip():
        return set()
    return marcadores_por_papel(depois, event_id, empresa,
                                aliases)[OBJECT_MARKER]


# ── §14 · alias DECLARADO ────────────────────────────────────────────────────
def carregar_aliases(caminho: str = ARQUIVO_ALIAS) -> dict:
    try:
        d = json.load(io.open(caminho, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fora: dict = {}
    for a in d.get("aliases", []):
        # sem fonte declarada nao entra: alias exige proveniencia auditavel
        if a.get("claim") != "SAME_OBJECT" or not a.get("source"):
            continue
        fora.setdefault((a.get("company"), a.get("family")), []).append({
            "alias_id": a["alias_id"], "canonical": a["canonical"],
            "members": {m.lower() for m in a.get("members", [])},
            "source": a["source"], "evidence": a.get("evidence", "")})
    return fora


def canonicalizar(tokens: set, company: str, family: str, aliases: dict) -> tuple:
    """Troca tokens de alias pelo nome canonico do GRUPO. Sem similaridade
    difusa: so casa quem foi DECLARADO."""
    regras = aliases.get((company, family), [])
    fora, usados = set(), []
    for t in tokens:
        alvo = next((r for r in regras if t in r["members"]), None)
        if alvo is None:
            fora.add(t)
            continue
        fora.add(alvo["canonical"])
        if alvo["alias_id"] not in usados:
            usados.append(alvo["alias_id"])
    return fora, usados


# ── §4/§16/§17 · adaptadores de identidade por familia ───────────────────────
# Cada adaptador devolve features ESTAVEIS da instancia. Onde a evidencia local
# e fraca, devolve confianca WEAK e a instancia cai na ancora de data.
_RX_PESSOA = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]"
    r"[a-zà-ÿ]{2,})\s+"
    r"((?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ]"
    r"[a-zà-ÿ]{2,})"
    r"(?:\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ]"
    r"[a-zà-ÿ]{2,}))?")
_RX_AGENCIA = re.compile(
    r"\b(moody\w*|fitch|s&p|standard\s*&\s*poor\w*|austin\s+rating|dbrs|scope)\b",
    re.I)
_RX_DIRECAO_RATING = re.compile(
    r"\b(rebaix\w*|downgrade\w*|corta\b|eleva\w*|upgrade\w*|reafirm\w*|"
    r"mant[eê]m\b|revis\w+|perspectiva\s+negativa|outlook\s+negative)\b", re.I)
# §4/§22 — o NIVEL atribuido e a evidencia local que separa "a mesma acao
# noticiada duas vezes" de "outra acao da mesma agencia". Escalas Moody's
# (Baa1, B1, Caa2) e S&P/Fitch (BB-, B+, AAA).
_RX_NIVEL_RATING = re.compile(
    r"\b(?:para|to|em|for)\s+[‘'\"]?"
    r"((?:aaa|aa|a|bbb|bb|b|ccc|cc|c|d)[+-]?|"
    r"(?:aaa|aa|a|baa|ba|b|caa|ca|c)[123])"
    r"[’'\"]?(?:\s|,|$|\.)", re.I)


def _pessoas(titulo: str, empresa: str, aliases_emp) -> list:
    """Nomes proprios de PESSOA no titulo, excluindo aliases do emissor."""
    ali = {rd.normalize(a) for a in (list(aliases_emp or []) + [empresa]) if a}
    fora = []
    for m in _RX_PESSOA.finditer(titulo or ""):
        nome = rd.normalize(" ".join(p for p in m.groups() if p))
        if any(a and (a in nome or nome in a) for a in ali if a):
            continue
        if any(papel_marcador(p) != OBJECT_MARKER for p in nome.split()):
            continue
        fora.append(nome)
    return sorted(set(fora))


def _ad_ma(ctx: dict) -> dict:
    """§16 — M&A: alvo/objeto normalizado, valor, tipo de transacao. A data de
    publicacao NUNCA e o id; ela so desempata quando as features nao bastam."""
    forte = bool(ctx["objeto_tokens"]) and bool(ctx["valor"])
    return {"features": {"target": "|".join(sorted(ctx["objeto_tokens"])),
                         "valor": ctx["valor"]},
            "confidence": "STRONG" if forte else "WEAK", "gap_days": 240}


def _ad_emissao(ctx: dict) -> dict:
    """§17 — emissao: a SERIE separa sempre (16a x 17a emissao); valor apoia."""
    return {"features": {"serie": ctx["serie"], "valor": ctx["valor"],
                         "instrumento": "|".join(sorted(ctx["objeto_tokens"]))},
            "confidence": "STRONG" if ctx["serie"] else "WEAK", "gap_days": 120}


def _ad_follow_on(ctx: dict) -> dict:
    return {"features": {"serie": ctx["serie"], "valor": ctx["valor"]},
            "confidence": "STRONG" if ctx["serie"] else "WEAK", "gap_days": 120}


def _ad_troca_ceo(ctx: dict) -> dict:
    """§17 — sucessao: o episodio e identificado pelas PESSOAS. E a prova de que
    o id nao pode ser company|family|objeto — a Hapvida tem duas sucessoes reais
    adjudicadas como DISTINCT_OCCURRENCE."""
    return {"features": {"pessoas": "|".join(ctx["pessoas"])},
            "confidence": "STRONG" if ctx["pessoas"] else "WEAK", "gap_days": 90}


def _ad_rating(ctx: dict) -> dict:
    """§4/§22 — acao de rating: AGENCIA + direcao + nivel + episodio.

    Calibrado por revisao humana de 2026-08-20 (Cosan): duas acoes de AGENCIAS
    DIFERENTES sao ocorrencias distintas, e nao se fundem por empresa, familia,
    direcao ou proximidade de data. O inverso tambem nao vale — mesma agencia
    NAO implica mesma ocorrencia: a Moody's rebaixou a Cosan em 16/07 (para B1)
    e de novo em 10/08. Quem separa e o NIVEL atribuido mais a janela de
    corroboracao; ver `_mesmo_episodio_rating`."""
    forte = bool(ctx["agencia"]) and bool(ctx["direcao_rating"])
    return {"features": {"agencia": ctx["agencia"], "direcao": ctx["direcao_rating"],
                         "nivel": ctx["nivel_rating"]},
            "confidence": "STRONG" if forte else "WEAK", "gap_days": 60}


def _ad_recomendacao(ctx: dict) -> dict:
    return {"features": {"instituicao": ctx["agencia"]
                         or "|".join(sorted(ctx["objeto_tokens"])),
                         "direcao": ctx["direcao_rating"]},
            "confidence": "WEAK", "gap_days": 45}


def _ad_recuperacao_judicial(ctx: dict) -> dict:
    """Processo unico e longo por emissor: features vazias, gap largo."""
    return {"features": {}, "confidence": "WEAK", "gap_days": 400}


def _ad_incidente(ctx: dict) -> dict:
    """Incidente operacional: o LOCAL e o objeto (Yobel/Los Olivos)."""
    return {"features": {"local": "|".join(sorted(ctx["locais"]
                                                  or ctx["objeto_tokens"]))},
            "confidence": "STRONG" if ctx["locais"] else "WEAK", "gap_days": 30}


def _ad_investigacao(ctx: dict) -> dict:
    """§23 — processo regulatorio: REGULADOR SOZINHO NAO BASTA.

    Calibrado por revisao humana de 2026-08-20 (Vale). O identificador do
    processo, quando existe, e evidencia forte de mesma ocorrencia; na falta
    dele vale o ASSUNTO investigado. Dois artigos que ambos dizem "abre
    processo" sobre assuntos diferentes sao dois processos, nao um."""
    return {"features": {"regulador": ctx["regulador"],
                         "processo_id": ctx["processo_id"],
                         "assunto": "|".join(sorted(ctx["objeto_tokens"]))},
            "confidence": "STRONG" if ctx["processo_id"] else "WEAK",
            "gap_days": 180}


def _ad_fallback(ctx: dict) -> dict:
    """§4 — o fallback e CONSERVADOR, nao 'funde tudo'."""
    return {"features": {"objeto": "|".join(sorted(ctx["objeto_tokens"]))},
            "confidence": "UNKNOWN", "gap_days": 45}


# ── §22/§23 · quando dois membros da MESMA chave sao o MESMO episodio ────────
def _mesmo_episodio_rating(a: dict, b: dict, janela_dias: int) -> tuple:
    """Duas acoes da mesma agencia e mesma direcao. Sao a MESMA acao noticiada
    duas vezes, ou duas acoes?"""
    if abs(a["pub_ts"] - b["pub_ts"]) <= janela_dias * 86400:
        return True, "janela de corroboracao"
    na, nb = a.get("nivel_rating", ""), b.get("nivel_rating", "")
    if na and nb:
        return (na == nb), ("mesmo nivel " + na if na == nb
                            else "niveis diferentes " + na + " x " + nb)
    # sem nivel dos dois lados e fora da janela: cada publicacao de acao de
    # rating vale como acao. Conservador na direcao de NAO fundir.
    return False, "sem nivel comparavel fora da janela"


def _mesmo_episodio_investigacao(a: dict, b: dict, janela_dias: int) -> tuple:
    pa, pb = a.get("processo_id", ""), b.get("processo_id", "")
    if pa and pb:
        return (pa == pb), ("mesmo processo " + pa if pa == pb
                            else "processos " + pa + " x " + pb)
    if a.get("abre_processo") and b.get("abre_processo"):
        comum = a["objeto_tokens"] & b["objeto_tokens"]
        if not comum:
            return False, "duas aberturas de processo com assuntos disjuntos"
        return True, "duas aberturas com assunto comum " + "|".join(sorted(comum))
    if b.get("abre_processo") and not a.get("abre_processo"):
        # abrir um processo e ASSERCAO de procedimento novo. Sem identificador
        # que ligue os dois, ele nao pode ser absorvido como corroboracao de um
        # artigo anterior que nao abriu processo nenhum — foi este atalho, pela
        # janela de 10 dias, que fundiu a abertura de 23/07 na Vale com a
        # eleicao de presidente interino de 15/07.
        return False, "abertura de processo nao corrobora artigo anterior"
    if abs(a["pub_ts"] - b["pub_ts"]) <= janela_dias * 86400:
        return True, "janela de corroboracao"
    comum = a["objeto_tokens"] & b["objeto_tokens"]
    if comum:
        return True, "assunto comum " + "|".join(sorted(comum))
    return False, "sem identificador de processo nem assunto comum"


_MESMO_EPISODIO = {
    "rebaixamento_rating": _mesmo_episodio_rating,
    "recomendacao_negativa": _mesmo_episodio_rating,
    "investigacao_regulatoria": _mesmo_episodio_investigacao,
}


def mesmo_episodio(family: str, a: dict, b: dict, janela_dias: int) -> tuple:
    fn = _MESMO_EPISODIO.get(family)
    return fn(a, b, janela_dias) if fn else (True, "")


# Quando o corte por episodio divide uma chave discriminante, a assinatura da
# instancia PRECISA carregar o que os separou — senao duas ocorrencias
# legitimamente distintas nascem com o mesmo occurrence_id. Foi o que o
# detector de colisao pegou nos dois rebaixamentos da Moody's na Cosan.
_CHAVE_EPISODIO = {
    "rebaixamento_rating": lambda m: m.get("nivel_rating", ""),
    "recomendacao_negativa": lambda m: m.get("nivel_rating", ""),
    "investigacao_regulatoria": lambda m: m.get("processo_id", ""),
}


def chave_episodio(family: str, abertura: dict) -> tuple:
    """(chave, estavel_por_conteudo). Sem evidencia propria, cai na data de
    abertura — e o registro sai marcado DATE_ANCHORED."""
    fn = _CHAVE_EPISODIO.get(family)
    if fn is None:
        return "", True
    v = fn(abertura)
    return (v, True) if v else (abertura["article_date"], False)


ADAPTADORES = {
    "ma": _ad_ma,
    "emissao_divida": _ad_emissao,
    "emissao_cotas": _ad_emissao,
    "follow_on": _ad_follow_on,
    "troca_ceo": _ad_troca_ceo,
    "rebaixamento_rating": _ad_rating,
    "recomendacao_negativa": _ad_recomendacao,
    "recuperacao_judicial": _ad_recuperacao_judicial,
    "investigacao_regulatoria": _ad_investigacao,
    "incidente_operacional_grave": _ad_incidente,
    "disrupcao_operacional": _ad_incidente,
}


def adaptador_de(family: str):
    return ADAPTADORES.get(family, _ad_fallback)


# ── §9 · a regra de ancora, isolada e pura ───────────────────────────────────
def ancora_efetiva(ancora_atual: str, candidata: str) -> str:
    """§9/§30 — a ancora NUNCA anda para tras. Uma fase material mais ANTIGA que
    uma ancora ja mais nova nao move nada. Exigida pelo achado do Citigroup:
    renovar as cegas custaria -28,4 pontos."""
    if not candidata:
        return ancora_atual
    if not ancora_atual:
        return candidata
    return max(ancora_atual, candidata)


# ── §5 · candidatos ANTES da fusao destrutiva ────────────────────────────────
def candidatos(historico="risk_history.json", config="config_risco.yaml") -> dict:
    """Reconstroi o MESMO estagio de candidatos que `build_evolution` monta
    antes da fusao de gemeos (mesmos helpers de producao: `event_ids_for`,
    `event_applies_to`, `trust_of_rec`, `occurrence_identity`) — mas PRESERVA o
    `article_id` canonico de cada candidato, que e exatamente o que a fusao
    perde nos 106 absorvidos."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    # o grupo de ativo vem do MESMO resolvedor da producao — ler a chave crua do
    # cadastro daria "a_revisar" para todo mundo e zeraria os candidatos
    meta = {c["name"]: {"asset_group": rd.asset_group_of_company(c)}
            for c in cfg.get("watchlist", [])}
    ali_por_emp = {c["name"]: (c.get("aliases") or [c["name"]])
                   for c in cfg.get("watchlist", [])}
    # §27 — a producao agrupa em UMA ocorrencia economica os event_ids de uma
    # familia opt-in (`merge_occurrences_across_articles`). Sem isto a sombra
    # partia o incendio da Yobel em `incidente_operacional`,
    # `incidente_operacional_grave` e `paralisacao_operacional`, triplicando o
    # score — regressao contra um comportamento que ja estava CERTO.
    fam_map = rd.cross_article_family_map(cfg)
    ev_cfg = cfg.get("evolution", {})
    window_days = ev_cfg.get("window_days", 90)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff = now_ts - window_days * 86400
    aliases_decl = carregar_aliases()

    fora = []
    for url, rec in H["articles"].items():
        pub_ts = rec.get("pub_ts") or 0
        if pub_ts < cutoff:
            continue
        aid = sh.id_artigo(rec.get("url") or url, rec.get("title") or "")
        titulo = rec.get("title") or ""
        for company in rec.get("companies", []):
            if company == rd.MARKET_LABEL:
                continue
            grp = meta.get(company, {}).get("asset_group", "a_revisar")
            for eid in rd.event_ids_for(rec, company):
                ev = taxonomy.get(eid)
                if not ev or not rd.event_applies_to(ev, grp):
                    continue
                _, t_w, t_label = rd.trust_of_rec(rec, cfg)
                papeis = marcadores_por_papel(titulo, eid, company,
                                              ali_por_emp.get(company))
                objeto_expl = rd.normalize(
                    (rec.get("mention_roles") or {}).get(company, {})
                    .get("subject_company", ""))
                locais = sorted(rd._marcadores_locais_operacionais(
                    titulo, company, ali_por_emp.get(company)))
                if fam_map.get(eid):
                    # familia opt-in: quem identifica o fato e a INSTALACAO, e
                    # e esse mesmo marcador que a producao usa no seu gate.
                    # Usar os marcadores genericos aqui partia o incendio da
                    # Yobel em tres.
                    brutos = set(locais)
                else:
                    brutos = set(papeis[OBJECT_MARKER])
                    # §24 — quando o titulo diz claramente o que foi comprado,
                    # o objeto e o ALVO. Sem pista de aquisicao, mantem-se o
                    # comportamento anterior: nada e inventado.
                    alvo = alvo_da_transacao(titulo, eid,
                                             company, ali_por_emp.get(company))
                    if alvo:
                        brutos = set(alvo)
                if objeto_expl:
                    brutos |= set(objeto_expl.split())
                brutos = {t for t in brutos if papel_marcador(t) == OBJECT_MARKER}
                tokens, alias_ids = canonicalizar(brutos, company, eid, aliases_decl)
                f = fase_de(titulo, eid)
                fora.append({
                    # §5 — proveniencia preservada
                    "article_id": aid,
                    "url": rec.get("url") or url,
                    "canonical_url": rec.get("canonical_url") or rec.get("resolved_url"),
                    "title": titulo,
                    "company": company,
                    "family": eid,
                    "family_key": fam_map.get(eid, eid),
                    "article_date": (rec.get("pub_iso") or "")[:10],
                    "pub_ts": pub_ts,
                    "source": rec.get("source", ""),
                    "domain": rec.get("domain", ""),
                    "trust_w": t_w,
                    "trust_label": t_label,
                    "score_base": ev.get("score", 0),
                    "direction": ev.get("direction", "negativa"),
                    "assertion_state": next(
                        (a.get("confirmation_status", "") for a in
                         (rec.get("event_assessments") or [])
                         if a.get("company") == company and a.get("event_id") == eid),
                        ""),
                    "relation_type": (rec.get("mention_roles") or {})
                        .get(company, {}).get("relation_type", "direto"),
                    "subject_company": (rec.get("mention_roles") or {})
                        .get(company, {}).get("subject_company", ""),
                    # identidade
                    "objeto_tokens": tokens,
                    "alias_ids": alias_ids,
                    "marcadores_regulador": sorted(papeis[REGULATOR_MARKER]),
                    "marcadores_contexto": sorted(papeis[CONTEXT_MARKER]),
                    "marcadores_genericos": sorted(papeis[GENERIC_MARKER]),
                    "serie": rd._serie_da_operacao(titulo),
                    "valor": rd._valor_da_operacao(titulo),
                    "pessoas": _pessoas(titulo, company, ali_por_emp.get(company)),
                    "agencia": (_RX_AGENCIA.search(titulo).group(1).lower()
                                if _RX_AGENCIA.search(titulo) else ""),
                    "direcao_rating": (_RX_DIRECAO_RATING.search(titulo).group(1).lower()
                                       if _RX_DIRECAO_RATING.search(titulo) else ""),
                    # §22 — o nivel atribuido separa "mesma acao renoticiada"
                    # de "outra acao da mesma agencia"
                    "nivel_rating": (_RX_NIVEL_RATING.search(titulo).group(1).lower()
                                     if _RX_NIVEL_RATING.search(titulo) else ""),
                    # §23 — identidade do processo regulatorio
                    "regulador": "|".join(sorted(papeis[REGULATOR_MARKER])),
                    "processo_id": (_RX_PROCESSO_ID.search(titulo).group(1)
                                    if _RX_PROCESSO_ID.search(titulo) else ""),
                    "abre_processo": bool(_RX_ABRE_PROCESSO.search(titulo)),
                    # §14 — vinculo explicito com compromisso anterior
                    "decorre_de_compromisso": bool(
                        _RX_DECORRE_COMPROMISSO.search(titulo)),
                    "locais": locais,
                    # a ausencia de marcador de local NAO contradiz um local
                    # conhecido — e o mesmo criterio do gate da producao
                    "marcador_nao_contradiz": bool(fam_map.get(eid)),
                    **f,
                })
    return {"_meta": {"shadow_version": SHADOW_VERSION, **AUTORIDADE},
            "membros": fora, "corpus": len(H["articles"]),
            "window_days": window_days, "now_ts": now_ts, "cfg": cfg}


# ── §3 · identidade de objeto x instancia de ocorrencia ──────────────────────
def _uniao_por_intersecao(itens: list) -> list:
    """Agrupa candidatos cujos conjuntos de OBJECT_MARKER se tocam."""
    grupos: list = []
    for m in itens:
        toca = [g for g in grupos
                if any(m["objeto_tokens"] & x["objeto_tokens"] for x in g)]
        if not toca:
            grupos.append([m])
            continue
        alvo = toca[0]
        alvo.append(m)
        for outro in toca[1:]:
            alvo.extend(outro)
            grupos.remove(outro)
    return grupos


def _membro_de_abertura(inst: list) -> dict:
    """O artigo que ABRE a ocorrencia: a INICIACAO mais antiga, ou, na falta
    dela, o membro mais antigo. Nao e o representante (que pode mudar), nem o
    mais recente, nem indice de cluster nenhum."""
    abre = [m for m in inst if m["fase"] == INICIACAO]
    return min(abre or inst, key=lambda m: (m["pub_ts"], m["article_id"]))


def _objeto_canonico(inst: list) -> tuple:
    """Nome canonico do objeto, tirado do MEMBRO DE ABERTURA.

    A tentativa obvia — token mais frequente, ou nucleo comum a todos — QUEBRA
    a invariante §32: as duas mudam quando um membro entra, porque frequencia
    cresce e nucleo encolhe. Foi assim que a primeira versao trocou o id ao
    receber a etapa regulatoria. A abertura, ao contrario, e imutavel sob
    acrescimo POSTERIOR — e a assinatura do evento inicial de que fala §16.

    Limite honesto: uma fonte publicada ANTES da abertura conhecida desloca a
    abertura. Por isso o campo `id_stability` acompanha cada ocorrencia."""
    abre = _membro_de_abertura(inst)
    toks = sorted(abre["objeto_tokens"])
    if not toks:
        return "", "UNKNOWN"
    nucleo = set.intersection(*[m["objeto_tokens"] for m in inst])
    return "|".join(toks), ("STRONG" if nucleo else "WEAK")


# §15 — a feature DISCRIMINANTE responde "qual evento economico e este", nunca
# "que objeto e este". Alias resolve objeto; isto resolve instancia.
_DISCRIMINANTE = {
    "ma": ("valor",), "emissao_divida": ("serie",), "emissao_cotas": ("serie",),
    "follow_on": ("serie",), "troca_ceo": ("pessoas",),
    "rebaixamento_rating": ("agencia", "direcao"),
    "recomendacao_negativa": ("instituicao", "direcao"),
    "incidente_operacional_grave": ("local",), "disrupcao_operacional": ("local",),
    "investigacao_regulatoria": ("regulador",),
}


# Familias cujo evento e do PROPRIO emissor: a oferta, a emissao, a recuperacao
# judicial nao tem "objeto externo". Ali um nome proprio no titulo e o destino
# dos recursos (Engie/Jirau) ou um terceiro citado de passagem (Tok&Stok/Mobly)
# — nao a identidade do fato. Deixar esses tokens fatiarem a ocorrencia
# produziria over-split pior que a producao. Quem separa aqui e a feature
# discriminante da familia (serie/valor), conforme §17.
#
# Acao de rating entra aqui pelo mesmo motivo, e por uma falha REAL que o teste
# de unicidade de id pegou: dois artigos sobre o MESMO rebaixamento da S&P na
# Cosan, a 28 dias um do outro, caiam em baldes anonimos distintos (a janela de
# 10 dias) e produziam o MESMO occurrence_id — colisao. O objeto de uma acao de
# rating e o proprio emissor; quem separa e a agencia + a direcao.
_FAMILIA_SEM_OBJETO_EXTERNO = frozenset({
    "follow_on", "emissao_divida", "emissao_cotas", "recuperacao_judicial",
    "rebaixamento_rating", "recomendacao_negativa",
})


def _discriminante(m: dict, family: str) -> tuple:
    ad = adaptador_de(family)(m)
    if ad["confidence"] != "STRONG":
        return "", ad
    campos = _DISCRIMINANTE.get(family, ())
    vals = [str(ad["features"].get(c, "")) for c in campos]
    return ("::".join(vals) if all(vals) else ""), ad


def _instancias(grupo: list, family: str, janela_corrob: int = 10) -> list:
    # Familia opt-in: a identidade E o marcador de instalacao, e ela nao expira
    # com o tempo — e exatamente a semantica da producao ("mantem unida a mesma
    # operacao mesmo quando as etapas passam de 45 dias"). Aplicar aqui
    # discriminante ou corte por gap re-partiria o que o agrupamento por objeto
    # ja uniu, que foi o que triplicou o incendio da Yobel.
    if any(m.get("marcador_nao_contradiz") for m in grupo):
        return [sorted(grupo, key=lambda x: (x["pub_ts"], x["article_id"]))]
    """Divide um grupo de OBJETO em INSTANCIAS economicas distintas.

    Divide-se apenas com evidencia POSITIVA de distincao: ou duas features
    discriminantes fortes e diferentes, ou duas INICIACOES separadas por mais
    que o gap da familia sem nenhum membro entre elas. Membro sem feature forte
    NAO cria instancia — ele se ancora na instancia mais proxima no tempo."""
    ad0 = adaptador_de(family)(grupo[0])
    gap = ad0["gap_days"] * 86400
    discs: dict = defaultdict(list)
    sem_disc = []
    for m in grupo:
        d, ad = _discriminante(m, family)
        m["_adapter_features"] = ad["features"]
        m["_adapter_confidence"] = ad["confidence"]
        (discs[d] if d else sem_disc).append(m)

    if discs:
        inst = [sorted(v, key=lambda x: x["pub_ts"]) for v in discs.values()]
    else:
        inst = []
    for m in sem_disc:
        if not inst:
            inst.append([m])
            continue
        perto = min(inst, key=lambda g: min(abs(m["pub_ts"] - x["pub_ts"]) for x in g))
        if min(abs(m["pub_ts"] - x["pub_ts"]) for x in perto) <= gap:
            perto.append(m)
            perto.sort(key=lambda x: x["pub_ts"])
        else:
            inst.append([m])

    # dois cortes, nesta ordem:
    #   (a) EPISODIO — regra calibrada por familia (agencia+nivel de rating,
    #       identificador/assunto de processo). Vale mesmo dentro da mesma
    #       chave discriminante: foi o que a revisao humana da Cosan e da Vale
    #       exigiu, porque `moody::rebaixa` casa DUAS acoes distintas.
    #   (b) gap de INICIACAO — duas aberturas distantes sao dois eventos.
    final = []
    for g in inst:
        g = sorted(g, key=lambda x: (x["pub_ts"], x["article_id"]))
        atual = [g[0]]
        for ant, cur in zip(g, g[1:]):
            junto, razao = mesmo_episodio(family, ant, cur, janela_corrob)
            cur["_episodio_razao"] = razao
            if not junto:
                final.append(atual)
                atual = [cur]
            elif (cur["fase"] == INICIACAO and ant["fase"] == INICIACAO
                  and cur["pub_ts"] - ant["pub_ts"] > gap):
                final.append(atual)
                atual = [cur]
            else:
                atual.append(cur)
        final.append(atual)
    return final


def _slug(nome: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", rd.normalize(nome or "")).strip("-")


def occurrence_id(company: str, family: str, objeto: str, assinatura: str) -> str:
    base = f"{company}|{family}|{objeto}|{assinatura}"
    return (f"{family}:{_slug(company)}:"
            f"{hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]}")


# ── §19 · representante de exibicao (nem o mais novo, nem o mais velho) ──────
_PESO_FASE_REPRESENTANTE = {INICIACAO: 3, MATERIAL: 3, ETAPA: 2,
                            ACOMPANHAMENTO: 1, UNKNOWN: 1}


# §25 — familias de ESTADO CONTINUO DE RISCO. Uma investigacao regulatoria ou
# uma recuperacao judicial nao "acontecem" numa data: elas seguem correndo, e o
# que o leitor precisa ver e o desenvolvimento substantivo mais RECENTE, nao a
# primeira noticia. A revisao humana de 2026-08-20 (Vale) trouxe exatamente
# esse contraexemplo a regra "a iniciacao e sempre o melhor representante".
#
# Isto muda o REPRESENTANTE DE EXIBICAO e nada mais. A ancora de score continua
# governada por `refresh_eligible`/`refresh_effective` — o proprio prompt
# humano avisou para nao equiparar as duas coisas, e §7 deixou a renovacao de
# investigacao explicitamente EM ABERTO.
_FAMILIA_ESTADO_CONTINUO = frozenset({
    "investigacao_regulatoria", "recuperacao_judicial",
})
_FASES_SUBSTANTIVAS = (INICIACAO, ETAPA, MATERIAL)


def escolher_representante(membros: list, family: str = "") -> dict:
    """Politica deterministica.

    Familia de TRANSACAO: assercao primaria de evento (fase que EXPLICA o fato)
    > qualidade da fonte > completude do titulo > data mais antiga como ultimo
    desempate. Familia de ESTADO CONTINUO: desenvolvimento substantivo mais
    RECENTE. Em nenhum dos dois casos a regra e "o mais novo" ou "o mais velho"
    sozinha."""
    if family in _FAMILIA_ESTADO_CONTINUO:
        subst = [m for m in membros if m["fase"] in _FASES_SUBSTANTIVAS]
        return max(subst or membros,
                   key=lambda m: (m["pub_ts"], m.get("trust_w") or 0.0,
                                  len(m.get("title") or "")))
    return max(membros, key=lambda m: (
        _PESO_FASE_REPRESENTANTE.get(m["fase"], 1),
        m.get("trust_w") or 0.0,
        1 if m.get("assertion_state") == "confirmado" else 0,
        len(m.get("title") or ""),
        -m["pub_ts"]))


# ── §8/§9 · fase != autoridade de renovacao ─────────────────────────────────
# Politica por familia com PROVENIENCIA. So `ma` tem confirmacao humana
# (Smart Fit e Suzano, lote V1). As demais ficam eleveis porem UNREVIEWED —
# reportadas, nunca promovidas a verdade.
POLITICA_REFRESH = {
    "ma": {"material_pode_renovar": True, "status": "HUMAN_CONFIRMED",
           "source": "human_supervision:BATCH_V1:case_05,case_08"},
}
_POLITICA_PADRAO = {"material_pode_renovar": True, "status": "UNREVIEWED",
                    "source": ""}


def politica_refresh(family: str) -> dict:
    return POLITICA_REFRESH.get(family, _POLITICA_PADRAO)


# §35 — o que a calibracao de 2026-08-20 estabeleceu e o que NAO estabeleceu.
# Publicado inteiro para que a incerteza restante nao fique escondida.
POLITICA_STATUS_FAMILIA = {
    "ma": {
        "status": "HUMAN_CONFIRMED",
        "estabelecido": "fechamento material renova a ancora para frente",
        "fonte": "human_supervision:BATCH_V1:case_05,case_08",
        "aberto": "",
    },
    "follow_on": {
        "status": "PARTIALLY_ESTABLISHED",
        "estabelecido": "recapitulacao retrospectiva NAO renova "
                        "(HUMAN_REVIEW_2026_08_20, Engie)",
        "fonte": "human_supervision:BATCH_V1:case_11 + HUMAN_REVIEW_2026_08_20",
        "aberto": "se uma conclusao CORRENTE e genuina deve renovar segue "
                  "sem decisao humana",
    },
    "rebaixamento_rating": {
        "status": "IDENTITY_ONLY",
        "estabelecido": "agencias diferentes sao ocorrencias distintas "
                        "(HUMAN_REVIEW_2026_08_20, Cosan)",
        "fonte": "HUMAN_REVIEW_2026_08_20",
        "aberto": "isto e IDENTIDADE, nao politica de renovacao — nenhuma "
                  "decisao humana sobre renovacao de rating existe",
    },
    "emissao_divida": {
        "status": "UNREVIEWED",
        "estabelecido": "",
        "fonte": "",
        "aberto": "nenhuma verdade humana sobre renovacao",
    },
    "investigacao_regulatoria": {
        "status": "REPRESENTATIVE_ONLY",
        "estabelecido": "o desenvolvimento substantivo mais recente e o "
                        "REPRESENTANTE (HUMAN_REVIEW_2026_08_20, Vale)",
        "fonte": "HUMAN_REVIEW_2026_08_20",
        "aberto": "se um desenvolvimento novo RENOVA o score segue sem "
                  "decisao humana — deliberadamente nao inventado",
    },
}


def politica_familias() -> dict:
    return {"politicas": POLITICA_STATUS_FAMILIA,
            "calibracao": "HUMAN_REVIEW_2026_08_20",
            "authority": "SHADOW / SIMULATED"}


# ── §9/§10/§14/§15 · MATERIALIDADE nao e DIRECAO nao e AUTORIDADE DE SCORE ──
#
# Tres perguntas distintas que hoje chegam coladas no painel:
#
#   MATERIALIDADE   aconteceu algo economicamente relevante?
#   DIRECAO         a evidencia indica adverso, favoravel, neutro ou incerto?
#   AUTORIDADE      isto deve somar pontos de risco, e quantos?
#
# Este bloco NAO decide nenhuma delas para a producao. Ele MEDE onde as tres
# estao conflacionadas, usando o campo `direction` que a PROPRIA taxonomia ja
# declara — nenhum motor de polaridade novo e criado (§10).
ADVERSO = "INHERENTLY_ADVERSE"
CONTEXTUAL = "CONTEXT_DEPENDENT"
PENDENTE = "POLICY_PENDING"
DIRECAO_INDETERMINADA = "DIRECTION_UNDETERMINED"


def classificar_direcao_familia(ev: dict) -> str:
    """A taxonomia ja carrega `direction`. Familia declarada `negativa` e
    adversa pelo proprio cadastro; `neutra` e dependente de contexto — e e ali
    que pontuar pela MERA EXISTENCIA do evento confunde fato material com fato
    ruim."""
    d = (ev.get("direction") or "").lower()
    if d == "negativa":
        return ADVERSO
    if d in ("neutra", ""):
        return CONTEXTUAL
    return PENDENTE          # positiva / mitigadora: politica em aberto


def matriz_materialidade(config="config_risco.yaml") -> dict:
    """§15 — tabela diagnostica familia a familia. Nenhum peso alterado."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    linhas = []
    for ev in cfg["taxonomy"]:
        cls = classificar_direcao_familia(ev)
        linhas.append({
            "family": ev["id"],
            "evento_material": True,       # estar na taxonomia ja afirma isso
            "direcao_declarada": ev.get("direction", ""),
            "classificacao": cls,
            "peso_base": ev.get("score", 0),
            "severidade": ev.get("severity", ""),
            "pontua_por_existir": cls == CONTEXTUAL and (ev.get("score", 0) > 0),
            "status_politica": (PENDENTE if cls == CONTEXTUAL
                                else "TAXONOMY_DECLARED")})
    conflito = [x for x in linhas if x["pontua_por_existir"]]
    return {"linhas": sorted(linhas, key=lambda x: -x["peso_base"]),
            "familias": len(linhas),
            "conflacao_materialidade_x_adversidade": sorted(
                conflito, key=lambda x: -x["peso_base"]),
            "peso_somado_das_neutras_que_pontuam": sum(
                x["peso_base"] for x in conflito),
            "authority": "SHADOW / SIMULATED · SOMENTE LEITURA"}


def direcao_de_ocorrencia(o: dict, tax: dict) -> dict:
    """§10 — classifica SO o que a evidencia sustenta. Nada e rotulado
    automaticamente como positivo; na duvida, DIRECTION_UNDETERMINED."""
    ev = tax.get(o["membros"][0]["family"], {})
    cls = classificar_direcao_familia(ev)
    if cls == ADVERSO:
        return {"direcao": "ADVERSE", "base": "taxonomia declara `negativa`"}
    return {"direcao": DIRECAO_INDETERMINADA,
            "base": "taxonomia declara `" + (ev.get("direction") or "?")
                    + "`; nenhuma evidencia local de deterioracao"}


def decomposicao(company: str, S: dict, prod: dict, sim: dict,
                 config="config_risco.yaml") -> dict:
    """§11 — de onde vem cada ponto de um emissor."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    tax = {e["id"]: e for e in cfg["taxonomy"]}
    linhas = []
    for o in S["ocorrencias"]:
        if o["company"] != company:
            continue
        d = direcao_de_ocorrencia(o, tax)
        linhas.append({
            "occurrence_id": o["occurrence_id"], "family": o["family"],
            "canonical_object": o["canonical_object"],
            "peso_base": o["score_base"],
            "trust_w": o["membros"][0]["trust_w"],
            "anchor_date": o["anchor_date"],
            "contribuicao_simulada": o["simulated_contribution"],
            "direcao": d["direcao"], "base_da_direcao": d["base"],
            "representante": o["display_representative_title"][:100],
            "n_membros": o["n_membros"]})
    adv = sum(x["contribuicao_simulada"] for x in linhas
              if x["direcao"] == "ADVERSE")
    ind = sum(x["contribuicao_simulada"] for x in linhas
              if x["direcao"] == DIRECAO_INDETERMINADA)
    return {"company": company,
            "producao": prod["empresas"].get(company),
            "sombra": sim["empresas"].get(company),
            "ocorrencias": sorted(linhas, key=lambda x: -x["contribuicao_simulada"]),
            "contribuicao_de_familias_adversas": round(adv, 1),
            "contribuicao_de_direcao_indeterminada": round(ind, 1),
            "fracao_indeterminada": (round(ind / (adv + ind), 3)
                                     if (adv + ind) else None),
            "authority": "SHADOW / SIMULATED"}


# ── §16/§17 · o score consome a CONTAGEM de ocorrencia? ─────────────────────
def acoplamento_score_ocorrencia(caminho="risk_dashboard.py") -> dict:
    """Le a producao para responder se promover a estrutura de ocorrencia SEM
    mexer no score e tecnicamente possivel. Nao adivinha: procura a evidencia
    no proprio codigo."""
    src = io.open(caminho, encoding="utf-8").read()
    chaveia = 'k = o.get("_occ_key") or o["event_id"]' in src
    soma = ('return sum(b["contrib"] for b in '
            'best_contribs(negatives, as_of_ts).values())' in src)
    return {"best_contribs_chaveia_por_occ_key": chaveia,
            "total_e_soma_por_chave": soma,
            "score_consome_contagem_de_ocorrencia": chaveia and soma,
            "conclusao": (
                "o total do emissor e literalmente UMA contribuicao por "
                "`_occ_key` somada: dividir uma ocorrencia em duas ACRESCENTA "
                "uma parcela. Promover a estrutura sem mudar o score exigiria "
                "manter duas chaves de ocorrencia vivas ao mesmo tempo — uma "
                "para exibir e outra para pontuar — que e a verdade dupla "
                "inconsistente que §16 proibe."
                if chaveia and soma
                else "acoplamento nao confirmado por leitura de codigo"),
            "promocao_em_dois_estagios_segura": not (chaveia and soma),
            "authority": "SHADOW / SIMULATED · SOMENTE LEITURA"}


# ── §21/§23 · prontidao de OCORRENCIA e prontidao de SCORE, separadas ──────
ERRO_OCORRENCIA = "OCCURRENCE_ERROR"
LACUNA_POLITICA = "HUMAN_CONFIRMED_CORRECT_OCCURRENCE_BUT_SCORING_POLICY_GAP"
NAO_REVISADO = "UNREVIEWED"
EXPLICADO_OUTRO = "EXPLAINED_OTHER"


def classificar_status_deltas(B: dict, S: dict, M: dict, prod: dict, sim: dict,
                              config="config_risco.yaml") -> list:
    """§23 — uma mudanca de status pode vir de identidade ERRADA ou de
    identidade CERTA com politica de score em aberto. Sao coisas diferentes e
    nao podem ser contadas juntas."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    fora = []
    for x in B["delta_status"]:
        dec = decomposicao(x["company"], S, prod, sim, cfg)
        erro_humano = any(l.get("avaliavel") and l["company"] == x["company"]
                          and l["identidade_ok"] is False for l in M["linhas"])
        confirmado = any(l.get("avaliavel") and l["company"] == x["company"]
                         and l["identidade_ok"] is True for l in M["linhas"])
        if erro_humano:
            cat = ERRO_OCORRENCIA
        elif confirmado and dec["fracao_indeterminada"] is not None \
                and dec["fracao_indeterminada"] >= 0.5:
            cat = LACUNA_POLITICA
        elif not x["explicado"]:
            cat = NAO_REVISADO
        else:
            cat = EXPLICADO_OUTRO
        fora.append({**x, "categoria": cat,
                     "fracao_indeterminada": dec["fracao_indeterminada"],
                     "contribuicao_adversa": dec["contribuicao_de_familias_adversas"],
                     "contribuicao_indeterminada":
                         dec["contribuicao_de_direcao_indeterminada"]})
    return fora


def prontidao(S, prod, M, T, B, F, sim, config="config_risco.yaml") -> dict:
    """§21 — DOIS veredictos, nunca um booleano so ate o fim."""
    cats = classificar_status_deltas(B, S, M, prod, sim, config)
    col = colisoes_de_id(S)
    amb = [x for x in B["sobre_fusao_divisoes"] if x["confianca"] == "AMBIGUOUS"]
    bloq_occ = []
    if M["identidade"]["erros"]:
        bloq_occ.append("identidade humana: " + str(M["identidade"]["erros"]))
    if M["fase"]["erros"]:
        bloq_occ.append("fase humana: " + str(M["fase"]["erros"]))
    if M["refresh"]["erros"]:
        bloq_occ.append("renovacao humana: " + str(M["refresh"]["erros"]))
    if M["representante"]["erros"]:
        bloq_occ.append("representante humano: " + str(M["representante"]["erros"]))
    if col:
        bloq_occ.append("colisao de id: " + str(len(col)))
    if F["membros_sem_article_id"]:
        bloq_occ.append("proveniencia perdida")
    if B["sub_fusao_fusoes"]:
        bloq_occ.append("fusao inexplicada: " + str(len(B["sub_fusao_fusoes"])))
    if [x for x in cats if x["categoria"] == ERRO_OCORRENCIA]:
        bloq_occ.append("status muda por identidade errada")

    bloq_score = []
    if [x for x in cats if x["categoria"] == LACUNA_POLITICA]:
        bloq_score.append(
            "status muda com identidade CERTA e direcao indeterminada: "
            + str([x["company"] for x in cats if x["categoria"] == LACUNA_POLITICA]))
    if [x for x in cats if x["categoria"] == NAO_REVISADO]:
        bloq_score.append("status muda sem explicacao")
    aberto = [f for f, v in POLITICA_STATUS_FAMILIA.items()
              if v["status"] != "HUMAN_CONFIRMED"]
    if aberto:
        bloq_score.append("politica de renovacao em aberto: " + str(sorted(aberto)))
    mat = matriz_materialidade(config)
    if mat["conflacao_materialidade_x_adversidade"]:
        bloq_score.append(
            "familias `neutra` que pontuam por existir: "
            + str(len(mat["conflacao_materialidade_x_adversidade"]))
            + " (peso somado " + str(mat["peso_somado_das_neutras_que_pontuam"]) + ")")
    return {
        "ocorrencia": {
            "identidade": M["identidade"], "fase": M["fase"],
            "renovacao": M["refresh"], "representante": M["representante"],
            "data_efetiva": M["data_efetiva"],
            "ids_estaveis": not col,
            "proveniencia": (str(F["membros_com_article_id"]) + "/"
                             + str(F["membros_sombra"])),
            "fusoes_inexplicadas": len(B["sub_fusao_fusoes"]),
            "divisoes_ambiguas": len(amb),
            "bloqueadores": bloq_occ,
            "pronta": not bloq_occ},
        "score": {
            "delta_total": B["delta_score_total"],
            "empresas_com_delta": len(B["delta_score_por_empresa"]),
            "status_deltas": cats,
            "bloqueadores": bloq_score,
            "pronta": not bloq_score},
        "acoplamento": acoplamento_score_ocorrencia(),
        "authority": "SHADOW / SIMULATED"}


# ── §6 · a estrutura de ocorrencia da sombra ─────────────────────────────────
def construir(historico="risk_history.json", config="config_risco.yaml") -> dict:
    C = candidatos(historico, config)
    cfg = C["cfg"]
    now_ts = C["now_ts"]
    d = cfg.get("evolution", {}).get("decay", {})
    half = max(1, d.get("half_life_days", 30))
    decay_on = d.get("enabled", True)
    collapse = cfg.get("evolution", {}).get("same_event_window_days", 10)

    def decay(ts):
        return 1.0 if not decay_on else 0.5 ** (((now_ts - ts) / 86400.0) / half)

    por_par: dict = defaultdict(list)
    for m in C["membros"]:
        por_par[(m["company"], m["family_key"])].append(m)

    ocorrencias = []
    for (emp, fam), itens in sorted(por_par.items()):
        if fam in _FAMILIA_SEM_OBJETO_EXTERNO:
            nomeados, anonimos, grupos = [], [], ([list(itens)] if itens else [])
        else:
            nomeados = [m for m in itens if m["objeto_tokens"]]
            anonimos = [m for m in itens if not m["objeto_tokens"]]
            grupos = _uniao_por_intersecao(nomeados)
        # §4 — sem objeto identificavel o fallback e CONSERVADOR: os anonimos
        # NAO entram num grupo nomeado por conveniencia. Eles se agrupam entre
        # si pela mesma janela curta que a producao ja usa, e o registro sai
        # marcado UNKNOWN para que nada finja identidade que nao tem.
        if len(grupos) == 1 and anonimos and all(
                m.get("marcador_nao_contradiz") for m in anonimos):
            grupos[0].extend(anonimos)
            anonimos = []
        # Um ACOMPANHAMENTO, por definicao, se refere a um fato que ja existe.
        # Sem objeto proprio ele nao pode ABRIR ocorrencia: cria-la seria
        # inventar um evento economico a partir de um comentario. Ancora-se na
        # ocorrencia mais proxima no tempo. Nada disso depende de score — e a
        # mesma regra vale se o peso da familia for zero.
        if grupos:
            resto = []
            for m in anonimos:
                if m["fase"] != ACOMPANHAMENTO:
                    resto.append(m)
                    continue
                perto = min(grupos, key=lambda g: min(
                    abs(m["pub_ts"] - x["pub_ts"]) for x in g))
                perto.append(m)
                m["_ancorado_por_acompanhamento"] = True
            anonimos = resto
        anonimos.sort(key=lambda m: m["pub_ts"])
        atual: list = []
        for m in anonimos:
            if atual and m["pub_ts"] - atual[-1]["pub_ts"] <= collapse * 86400:
                atual.append(m)
            else:
                if atual:
                    grupos.append(atual)
                atual = [m]
        if atual:
            grupos.append(atual)

        for grupo in grupos:
            for inst in _instancias(grupo, fam, collapse):
                inst.sort(key=lambda m: (m["pub_ts"], m["article_id"]))
                if fam in _FAMILIA_SEM_OBJETO_EXTERNO:
                    # rotular 'mobly' como objeto da RJ da Tok&Stok seria
                    # mentira de identidade: aqui o objeto e o proprio emissor
                    objeto, obj_conf = "", "SELF"
                else:
                    objeto, obj_conf = _objeto_canonico(inst)
                disc = next((_discriminante(m, fam)[0] for m in inst
                             if _discriminante(m, fam)[0]), "")
                abertura = _membro_de_abertura(inst)
                ep, ep_estavel = chave_episodio(fam, abertura)
                if disc:
                    assinatura = "disc:" + disc + (("|ep:" + ep) if ep else "")
                    estabilidade = ("CONTENT_STABLE" if ep_estavel
                                    else "DATE_ANCHORED")
                else:
                    assinatura = "epoch:" + abertura["article_date"]
                    estabilidade = "DATE_ANCHORED"
                oid = occurrence_id(emp, fam, objeto, assinatura)

                inicial = inst[0]["article_date"]
                pol = politica_refresh(fam)
                ancora, motivo, membro_ancora = inicial, "INITIAL_MEMBER", inst[0]
                for m in inst:
                    eleg = (m["fase"] == MATERIAL and pol["material_pode_renovar"])
                    m["refresh_eligible"] = eleg
                    nova = ancora_efetiva(ancora, m["article_date"]) if eleg else ancora
                    efetivo = bool(eleg and nova != ancora)
                    m["refresh_effective"] = efetivo
                    m["refresh_policy_status"] = pol["status"] if eleg else "NOT_ELIGIBLE"
                    if efetivo:
                        ancora, membro_ancora = nova, m
                        motivo = "MATERIAL_PHASE::" + (m["fase_evidencia"] or "?")
                    elif eleg and motivo == "INITIAL_MEMBER":
                        motivo = "MATERIAL_NOT_NEWER_THAN_ANCHOR"
                rep = escolher_representante(inst, fam)
                base = max(m["score_base"] or 0 for m in inst)
                contrib = base * (membro_ancora.get("trust_w") or 1.0) * decay(
                    membro_ancora["pub_ts"])
                dominios = {m["domain"] for m in inst if m.get("domain")}
                ocorrencias.append({
                    "occurrence_id": oid,
                    "company": emp,
                    "family": fam,
                    "familias_membros": sorted({m["family"] for m in inst}),
                    "canonical_object": objeto,
                    "object_confidence": obj_conf,
                    "occurrence_instance_signature": assinatura,
                    "id_stability": estabilidade,
                    "aliases": sorted({a for m in inst for a in m["alias_ids"]}),
                    "n_membros": len(inst),
                    "membros": [{
                        "article_id": m["article_id"], "family": m["family"],
                        "article_date": m["article_date"], "pub_ts": m["pub_ts"],
                        # §20 — sem inferencia silenciosa: a data efetiva cai na
                        # data do artigo e o rotulo diz que foi fallback
                        # §16/§20 — um ACOMPANHAMENTO refere fato que JA
                        # ocorreu: a data do artigo NAO e a data do evento, e o
                        # acervo local nao diz qual e. Declara-se a lacuna em
                        # vez de carimbar a data da publicacao como se fosse a
                        # do fato — foi esse carimbo que a revisao humana da
                        # Engie derrubou.
                        "effective_event_date": (
                            None if m["fase"] == ACOMPANHAMENTO
                            else m["article_date"]),
                        "effective_event_date_source": (
                            "RETROSPECTIVE_REFERENCE_DATE_UNKNOWN"
                            if m["fase"] == ACOMPANHAMENTO
                            else "ARTICLE_DATE_FALLBACK"),
                        "phase": m["fase"], "phase_evidence": m["fase_evidencia"],
                        "phase_raw": m["fase_bruta"],
                        "article_role": m["relation_type"],
                        "assertion_state": m["assertion_state"],
                        "corroboration_role": ("ANCHOR" if m is membro_ancora else
                                               "REPRESENTATIVE" if m is rep
                                               else "CORROBORATION"),
                        "refresh_eligible": m["refresh_eligible"],
                        "refresh_effective": m["refresh_effective"],
                        "refresh_policy_status": m["refresh_policy_status"],
                        "source": m["source"], "domain": m["domain"],
                        "trust_w": m["trust_w"], "trust_label": m["trust_label"],
                        "title": (m["title"] or "")[:150], "url": m["url"],
                        "objeto_tokens": sorted(m["objeto_tokens"]),
                        "marcadores_regulador": m["marcadores_regulador"],
                        "adapter_features": m.get("_adapter_features", {}),
                        "adapter_confidence": m.get("_adapter_confidence", "UNKNOWN"),
                    } for m in inst],
                    "initial_date": inicial,
                    "anchor_date": ancora,
                    "anchor_member": membro_ancora["article_id"],
                    "refresh_reason": motivo,
                    "refresh_policy_status": pol["status"],
                    "display_representative": rep["article_id"],
                    "display_representative_date": rep["article_date"],
                    "display_representative_title": (rep["title"] or "")[:150],
                    "score_base": base,
                    "n_dominios": len(dominios),
                    "simulated_contribution": round(contrib, 1),
                    "authority": "SHADOW / SIMULATED",
                })
    return {"_meta": {"shadow_version": SHADOW_VERSION, **AUTORIDADE},
            "corpus": C["corpus"], "window_days": C["window_days"],
            "candidatos": len(C["membros"]), "ocorrencias": ocorrencias,
            "now_ts": now_ts}


# ── §38 · simulacao de score/status, sem autoridade nenhuma ──────────────────
def simular(S: dict, prod: dict) -> dict:
    """Status derivado dos limiares EFETIVOS que a producao usou nesta rodada
    (os limiares adaptativos vivem dentro de `build_evolution` e nao sao
    reutilizaveis de fora). Rotulado, nunca autoritativo."""
    porc: dict = defaultdict(float)
    base_max: dict = defaultdict(int)
    for o in S["ocorrencias"]:
        porc[o["company"]] += o["simulated_contribution"]
        base_max[o["company"]] = max(base_max[o["company"]], o["score_base"] or 0)

    emp = prod["empresas"]
    pbase: dict = defaultdict(int)
    for o in prod["ocorrencias"]:
        pbase[o["company"]] = max(pbase[o["company"]], o["score_base"] or 0)
    # o rotulo de status nao e funcao so do total: a producao promove a critico
    # por evento unico grave (`critico_event_min_score`). Sem isso a derivacao
    # colocaria a Tok&Stok (total 42, critico) como piso de todo mundo.
    lim_ev = 90
    # os limiares de `atencao`/`critico` sao ADAPTATIVOS (percentis calculados
    # dentro de `build_evolution`) e nao sao legiveis de fora. Em vez de chutar
    # um piso, ajusta-se o par de limiares que melhor REPRODUZ o rotulo da
    # producao sobre os numeros da producao — e a fidelidade desse ajuste e
    # publicada junto. Comparar status sem isso compararia vocabulario.
    cand = sorted({v["total_score"] for v in emp.values()} | {0, 60, 125})

    def _erros(la, lc):
        return [k for k, v in emp.items()
                if ("critico" if (pbase.get(k, 0) >= lim_ev
                                  or v["total_score"] >= lc)
                    else "atencao" if v["total_score"] >= la
                    else "monitorar") != v["status"]]

    lim_at, lim_cr, erros = 60, 125, None
    for lc in cand:
        for la in cand:
            if la > lc:
                continue
            e = _erros(la, lc)
            if erros is None or len(e) < len(erros):
                lim_at, lim_cr, erros = la, lc, e

    def status(total, maior_evento):
        if maior_evento >= lim_ev or total >= lim_cr:
            return "critico"
        return "atencao" if total >= lim_at else "monitorar"
    return {"limiares_derivados": {"atencao_min": lim_at, "critico_min": lim_cr,
                                   "evento_critico_min": lim_ev,
                                   "fonte": "derivado_da_producao"},
            "modelo_status_fidelidade": {
                "empresas": len(emp), "reproduzidas": len(emp) - len(erros),
                "divergentes": sorted(erros)},
            "empresas": {k: {"simulated_total_score": round(v, 1),
                             "simulated_status": status(v, base_max[k])}
                         for k, v in sorted(porc.items())},
            "authority": "SHADOW / SIMULATED"}


# ── §5/§31 · fidelidade e proveniencia ───────────────────────────────────────
def fidelidade(S: dict, prod: dict) -> dict:
    """A sombra cobre os artigos que a producao pontua? E preserva o id que a
    fusao destrutiva perde?"""
    ids_sombra = {m["article_id"] for o in S["ocorrencias"] for m in o["membros"]}
    reps = {o["representante_article_id"] for o in prod["ocorrencias"]}
    abs_tot = sum(o["n_absorvidos"] for o in prod["ocorrencias"])
    abs_res = sum(1 for o in prod["ocorrencias"] for a in o["absorvidos"]
                  if a["article_id"])
    return {
        "membros_sombra": sum(o["n_membros"] for o in S["ocorrencias"]),
        "membros_com_article_id": sum(
            1 for o in S["ocorrencias"] for m in o["membros"] if m["article_id"]),
        "membros_sem_article_id": sum(
            1 for o in S["ocorrencias"] for m in o["membros"] if not m["article_id"]),
        "producao_absorvidos_total": abs_tot,
        "producao_absorvidos_resolviveis": abs_res,
        "producao_absorvidos_nao_resolviveis": abs_tot - abs_res,
        "representantes_producao": len(reps),
        "representantes_cobertos_pela_sombra": len(reps & ids_sombra),
        "representantes_nao_cobertos": sorted(reps - ids_sombra)[:12],
    }


# ── §22 · producao x sombra, por empresa x familia ───────────────────────────
def comparar(S: dict, prod: dict) -> list:
    ps: dict = defaultdict(list)
    for o in prod["ocorrencias"]:
        ps[(o["company"], o["family"])].append(o)
    ss: dict = defaultdict(list)
    for o in S["ocorrencias"]:
        ss[(o["company"], o["family"])].append(o)
    linhas = []
    for k in sorted(set(ps) | set(ss)):
        p, s = ps.get(k, []), ss.get(k, [])
        linhas.append({
            "company": k[0], "family": k[1],
            "producao_ocorrencias": len(p), "sombra_ocorrencias": len(s),
            "producao_ancora": p[0]["ancora_date"] if p else None,
            "sombra_ancoras": sorted({x["anchor_date"] for x in s}),
            "producao_representante": p[0]["representante_article_id"] if p else None,
            "sombra_representantes": sorted({x["display_representative"] for x in s}),
            "sombra_score_simulado": round(
                sum(x["simulated_contribution"] for x in s), 1),
            "objetos_sombra": sorted({x["canonical_object"] for x in s
                                      if x["canonical_object"]}),
        })
    return linhas


# ── §23/§25 · matriz humana, por DIMENSAO (nunca uma acuracia agregada) ──────
# Traducao entre o vocabulario humano de artigo e o modelo de fase da sombra.
_ROLE_PARA_FASE = {
    "PROCESS_STEP": ETAPA,
    "IMPLEMENTATION_CLOSING": MATERIAL,
    "FOLLOW_UP": ACOMPANHAMENTO,
    "RETROSPECTIVE_RECAP": ACOMPANHAMENTO,
    "STRATEGIC_COMMENTARY": ACOMPANHAMENTO,
    "THIRD_PARTY_COMMENTARY": ACOMPANHAMENTO,
    "ANALYST_COMMENTARY_CORROBORATION": ACOMPANHAMENTO,
    "DESCRIPTOR_BACKGROUND": ACOMPANHAMENTO,
    "NEW_EVENT": INICIACAO,
    "NEW_NEGATIVE_RATING_EVENT": INICIACAO,
    "NEW_NEGATIVE_ASSESSMENT": INICIACAO,
    "REAFFIRMATION": INICIACAO,
    "SUCCESSION_PROCESS": INICIACAO,
    "RELEVANT_STAKE_ACQUISITION": INICIACAO,
}


def _antecessor_fora_da_janela(company, family, tokens, pub_ts, historico, window_days):
    """O artigo que ABRE a ocorrencia pode estar fora da janela de score de 90
    dias — nesse caso a sombra chama de NEW o que o humano chama de SAME, e o
    erro e da JANELA, nao da identidade. Isto detecta o caso em vez de deixar a
    metrica mentir."""
    corte = pub_ts - window_days * 86400
    for u, r in historico["articles"].items():
        rp_ts = r.get("pub_ts") or 0
        if rp_ts >= pub_ts - 0 or rp_ts < corte - 365 * 86400:
            continue
        if company not in (r.get("companies") or []):
            continue
        if family not in (rd.event_ids_for(r, company) or []):
            continue
        if not tokens:
            return {"article_date": (r.get("pub_iso") or "")[:10],
                    "title": (r.get("title") or "")[:110]}
        marc = marcadores_por_papel(r.get("title") or "", family, company)
        # o alias DECLARADO tambem vale para tras: sem canonicalizar aqui, o
        # anuncio da Kimberly-Clark nao casaria com o fechamento da Arbex
        antes, _ = canonicalizar(marc[OBJECT_MARKER], company, family,
                                 carregar_aliases())
        if tokens & antes:
            return {"article_date": (r.get("pub_iso") or "")[:10],
                    "title": (r.get("title") or "")[:110]}
    return None


def _objeto_existe_no_corpus(company, family, tokens, exceto_ts, historico) -> bool:
    """O objeto desta ocorrencia aparece em ALGUM outro artigo do acervo — em
    qualquer familia, dentro ou fora da janela?

    Serve para separar duas coisas que nao podem ser contadas juntas: um erro
    de IDENTIDADE (a peca existe e a sombra nao a ligou) e uma lacuna de
    COLETA (a peca nunca foi capturada). No caso Natura/Advent o anuncio de
    30/03 simplesmente nao esta em `risk_history.json` — nenhuma regra de
    ocorrencia poderia liga-lo."""
    if not tokens:
        return False
    for u, r in historico["articles"].items():
        if (r.get("pub_ts") or 0) == exceto_ts:
            continue
        if company not in (r.get("companies") or []):
            continue
        marc = marcadores_por_papel(r.get("title") or "", family, company)
        antes, _ = canonicalizar(marc[OBJECT_MARKER], company, family,
                                 carregar_aliases())
        if tokens & antes:
            return True
    return False


# Papeis humanos que descrevem um artigo que NAO assere o evento na propria
# data de publicacao.
_PAPEL_RETROSPECTIVO = frozenset({
    "RETROSPECTIVE_RECAP", "FOLLOW_UP", "STRATEGIC_COMMENTARY",
    "THIRD_PARTY_COMMENTARY", "ANALYST_COMMENTARY_CORROBORATION",
    "DESCRIPTOR_BACKGROUND",
})
# Papeis que NUNCA deveriam virar o representante principal.
_PAPEL_NAO_PRINCIPAL = frozenset({
    "RETROSPECTIVE_RECAP", "STRATEGIC_COMMENTARY", "THIRD_PARTY_COMMENTARY",
    "ANALYST_COMMENTARY_CORROBORATION", "DESCRIPTOR_BACKGROUND", "FOLLOW_UP",
})


def _representante_esperado(h: dict, o: dict, m: dict):
    papel = h.get("article_role") or ""
    e_rep = o["display_representative"] == m["article_id"]
    if papel in _PAPEL_NAO_PRINCIPAL:
        return not e_rep
    if o["family"] in _FAMILIA_ESTADO_CONTINUO and o["n_membros"] > 1:
        # §6 — o desenvolvimento substantivo mais recente e o principal
        mais_novo = max(o["membros"], key=lambda x: x["article_date"])
        return e_rep == (m["article_id"] == mais_novo["article_id"])
    return None


def matriz_humana(S: dict, caminho_humano: str | None = None,
                  historico="risk_history.json") -> dict:
    import reliability_human_supervision as hs
    MS = hs.carregar(caminho_humano)["memberships"]
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    por_artigo = {}
    for o in S["ocorrencias"]:
        for m in o["membros"]:
            por_artigo[(m["article_id"], o["company"], m["family"])] = (o, m)

    linhas = []
    for _k, h in sorted(MS.items(), key=lambda x: (x[1]["case_id"], x[1]["company"])):
        par = por_artigo.get((h["article_id"], h["company"], h["family"]))
        if par is None:
            linhas.append({"case_id": h["case_id"], "company": h["company"],
                           "family": h["family"], "avaliavel": False,
                           "motivo": "artigo nao pontua na janela atual da sombra"})
            continue
        o, m = par
        fase_h = _ROLE_PARA_FASE.get(h.get("article_role") or "")
        # §3 — identidade em termos humanos: este artigo ABRE a ocorrencia
        # (NEW_OCCURRENCE) ou se JUNTA a uma que ja existia (SAME_OCCURRENCE)?
        abre = (m["article_date"] == o["initial_date"])
        rel_h = h.get("occurrence_relation")
        rel_s = "NEW_OCCURRENCE" if abre else "SAME_OCCURRENCE"
        janela = corpus = None
        if rel_h == "SAME_OCCURRENCE" and rel_s == "NEW_OCCURRENCE":
            janela = _antecessor_fora_da_janela(
                h["company"], h["family"], set(m["objeto_tokens"]),
                m["pub_ts"], H, S["window_days"])
            if janela is None and not _objeto_existe_no_corpus(
                    h["company"], h["family"], set(m["objeto_tokens"]),
                    m["pub_ts"], H):
                corpus = ("o artigo de abertura nao existe no acervo — lacuna "
                          "de COLETA, nao de identidade")
        # §8 — fase NAO e autoridade de renovacao: a verdade humana
        # `score_refresh` responde "esta fase PODE renovar", que e
        # ELEGIBILIDADE. Se a fase material ja E a ancora, `refresh_effective`
        # e falso sem que a elegibilidade esteja errada (caso Suzano).
        ref_h = h.get("score_refresh")
        ref_s = "TRUE" if m["refresh_eligible"] else "FALSE"
        linhas.append({
            "case_id": h["case_id"], "company": h["company"], "family": h["family"],
            "avaliavel": True,
            "identidade_humana": rel_h, "identidade_sombra": rel_s,
            "identidade_ok": (None if rel_h in ("NOT_APPLICABLE", "UNDETERMINED")
                              or janela or corpus else rel_h == rel_s),
            "identidade_limitada_pela_janela": janela,
            "identidade_limitada_pelo_corpus": corpus,
            "fase_humana": fase_h or "SEM_MAPA", "fase_sombra": m["phase"],
            "fase_ok": (None if not fase_h or m["phase"] == UNKNOWN
                        else fase_h == m["phase"]),
            "fase_indefinida": m["phase"] == UNKNOWN and bool(fase_h),
            "refresh_humano": ref_h, "refresh_elegivel_sombra": ref_s,
            "refresh_efetivo_sombra": m["refresh_effective"],
            "refresh_ok": (None if ref_h in ("NOT_APPLICABLE", "UNDETERMINED")
                           else ref_h == ref_s),
            "representante_sombra_e_este": (
                o["display_representative"] == m["article_id"]),
            # §26 — o humano so opina sobre representante quando o papel do
            # artigo o define: quem RECAPITULA ou COMENTA nunca deveria ser o
            # principal; quem e o desenvolvimento mais recente de um estado
            # continuo deveria.
            "representante_ok": _representante_esperado(h, o, m),
            # §26/§16 — a data efetiva so e avaliavel quando o papel humano diz
            # que o artigo NAO assere o evento na propria data
            "data_efetiva_ok": (
                None if h.get("article_role") not in _PAPEL_RETROSPECTIVO
                else m["effective_event_date"] != m["article_date"]),
            "data_efetiva_sombra": m["effective_event_date"],
            "data_efetiva_fonte": m["effective_event_date_source"],
            "occurrence_id": o["occurrence_id"], "anchor_date": o["anchor_date"],
            "article_role_humano": h.get("article_role"),
            "titulo": m["title"][:110],
        })

    def taxa(campo):
        vals = [l[campo] for l in linhas
                if l.get("avaliavel") and l.get(campo) is not None]
        return {"avaliaveis": len(vals), "acertos": sum(1 for v in vals if v),
                "erros": sum(1 for v in vals if not v)}

    return {"linhas": linhas, "avaliaveis": sum(1 for l in linhas if l["avaliavel"]),
            "nao_avaliaveis": sum(1 for l in linhas if not l["avaliavel"]),
            "identidade": taxa("identidade_ok"), "fase": taxa("fase_ok"),
            "refresh": taxa("refresh_ok"),
            "fase_indefinida": sum(1 for l in linhas if l.get("fase_indefinida")),
            "identidade_limitada_pela_janela": sum(
                1 for l in linhas if l.get("identidade_limitada_pela_janela")),
            "identidade_limitada_pelo_corpus": sum(
                1 for l in linhas if l.get("identidade_limitada_pelo_corpus")),
            "representante": taxa("representante_ok"),
            "data_efetiva": taxa("data_efetiva_ok"),
            "authority": "SHADOW / SIMULATED"}


# ── §24 · avaliacao contra occurrence_truth (somente leitura) ────────────────
_PHASE_TRUTH = {"COMPLETION": MATERIAL, "CLOSING": MATERIAL,
                "IMPLEMENTATION": MATERIAL, "REGULATORY_APPROVAL": ETAPA,
                "ANNOUNCEMENT": INICIACAO, "APPOINTMENT": INICIACAO,
                "NONE": ACOMPANHAMENTO}


def avaliar_occurrence_truth(S: dict, caminho="risk_semantic_v2_shadow.json") -> dict:
    ot = json.load(io.open(caminho, encoding="utf-8"))["occurrence_truth"]
    por_artigo = {}
    for o in S["ocorrencias"]:
        for m in o["membros"]:
            por_artigo[(m["article_id"], o["company"], m["family"])] = (o, m)
    linhas, mesma_occ = [], {}
    for mb in ot["memberships"]:
        par = por_artigo.get((mb["article_ref"], mb["company"], mb["event_id"]))
        if par is None:
            linhas.append({"membership_id": mb["membership_id"], "avaliavel": False,
                           "motivo": "artigo fora da janela de score da sombra"})
            continue
        o, m = par
        fase_v = _PHASE_TRUTH.get(mb["material_phase"])
        mesma_occ.setdefault(mb["occurrence_truth_id"], set()).add(o["occurrence_id"])
        linhas.append({
            "membership_id": mb["membership_id"], "avaliavel": True,
            "company": mb["company"], "family": mb["event_id"],
            "material_phase_verdade": mb["material_phase"],
            "fase_esperada": fase_v or "SEM_MAPA", "fase_sombra": m["phase"],
            "fase_ok": (None if not fase_v else fase_v == m["phase"]),
            "novelty_verdade": mb["occurrence_novelty"],
            "should_refresh_verdade": mb["should_refresh_anchor"],
            "refresh_sombra": m["refresh_effective"],
            "refresh_ok": (None if mb["should_refresh_anchor"] is None
                           else bool(mb["should_refresh_anchor"]) == m["refresh_effective"]),
            "occurrence_truth_id": mb["occurrence_truth_id"],
            "occurrence_id_sombra": o["occurrence_id"]})
    # relacoes DISTINCT_OCCURRENCE: a sombra as mantem distintas?
    rel = []
    for r in ot["relations"]:
        a = mesma_occ.get(r["occurrence_a"], set())
        b = mesma_occ.get(r["occurrence_b"], set())
        if not a or not b:
            rel.append({"relation": r["relation"], "avaliavel": False,
                        "occurrence_a": r["occurrence_a"], "occurrence_b": r["occurrence_b"]})
            continue
        rel.append({"relation": r["relation"], "avaliavel": True,
                    "occurrence_a": r["occurrence_a"], "occurrence_b": r["occurrence_b"],
                    "sombra_mantem_distintas": not (a & b)})

    def taxa(campo):
        vals = [l[campo] for l in linhas if l.get("avaliavel") and l.get(campo) is not None]
        return {"avaliaveis": len(vals), "acertos": sum(1 for v in vals if v),
                "erros": sum(1 for v in vals if not v)}

    # ocorrencias-verdade quebradas em mais de uma ocorrencia-sombra
    fragmentadas = {k: sorted(v) for k, v in mesma_occ.items() if len(v) > 1}
    return {"linhas": linhas, "relacoes": rel,
            "memberships_totais": len(ot["memberships"]),
            "memberships_avaliaveis": sum(1 for l in linhas if l["avaliavel"]),
            "fase": taxa("fase_ok"), "refresh": taxa("refresh_ok"),
            "verdades_fragmentadas_pela_sombra": fragmentadas,
            "authority": "SHADOW / SIMULATED"}


# ── §26/§27/§28/§29/§30 · os blasts ─────────────────────────────────────────
ARQUIVO_CALIBRACAO = "occurrence_calibration_shadow.json"


def carregar_calibracao(caminho: str = ARQUIVO_CALIBRACAO) -> dict:
    """Confirmacoes humanas de NIVEL DE PAR (emissor x familia).

    `risk_human_supervision.json` e indexado por `article_id|company|family` e
    ja esta congelado; parte das decisoes desta calibracao se refere a artigos
    que nem existem no acervo (a transacao Walkers). Registrar aqui evita bump
    de schema e evita reescrever verdade humana anterior."""
    try:
        d = json.load(io.open(caminho, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {(c["company"], c["family"]): c for c in d.get("confirmacoes", [])
            if c.get("company") and c.get("family")}


def _humano_confirmado(M: dict, company: str, family: str) -> bool:
    if (company, family) in carregar_calibracao():
        return True
    return any(l.get("avaliavel") and l["company"] == company
               and l["family"] == family for l in M["linhas"])


def blast(S: dict, prod: dict, M: dict, sim: dict) -> dict:
    ps: dict = defaultdict(list)
    for o in prod["ocorrencias"]:
        ps[(o["company"], o["family"])].append(o)
    ss: dict = defaultdict(list)
    for o in S["ocorrencias"]:
        ss[(o["company"], o["family"])].append(o)

    # §26 — sobre-fusao: a producao tinha 1, a sombra separa em N
    divisoes, fusoes = [], []
    for k in sorted(set(ps) | set(ss)):
        p, s = ps.get(k, []), ss.get(k, [])
        if not p or not s:
            continue
        conf = ("HUMAN_CONFIRMED" if _humano_confirmado(M, *k) else
                "HIGH_CONFIDENCE" if all(x["object_confidence"] == "STRONG"
                                         and x["id_stability"] == "CONTENT_STABLE"
                                         for x in s)
                else "AMBIGUOUS")
        delta = round(sum(x["simulated_contribution"] for x in s)
                      - sum((x["score_base"] or 0) * (x["trust_w"] or 1.0)
                            for x in p), 1)
        reg = sorted({r for x in s for m in x["membros"]
                      for r in m["marcadores_regulador"]})
        registro = {"company": k[0], "family": k[1],
                    "producao_ocorrencias": len(p), "sombra_ocorrencias": len(s),
                    "objetos": sorted({x["canonical_object"] for x in s
                                       if x["canonical_object"]}),
                    "marcadores_regulador_presentes": reg,
                    "delta_score_simulado": delta, "confianca": conf}
        if len(s) > len(p):
            divisoes.append(registro)
        elif len(s) < len(p):
            fusoes.append(registro)

    # §28 — renovacao: membro MATERIAL posterior a ancora inicial
    renovacoes, negativos, retro = [], [], []
    for o in S["ocorrencias"]:
        mats = [m for m in o["membros"] if m["phase"] == MATERIAL]
        for m in mats:
            reg = {"company": o["company"], "family": o["family"],
                   "occurrence_id": o["occurrence_id"],
                   "membro_material": m["article_id"],
                   "membro_material_date": m["article_date"],
                   "ancora_anterior": o["initial_date"],
                   "ancora_efetiva": o["anchor_date"],
                   "politica": o["refresh_policy_status"],
                   "titulo": m["title"][:110],
                   "classificacao": ("HUMAN_CONFIRMED"
                                     if _humano_confirmado(M, o["company"], o["family"])
                                     else "UNREVIEWED")}
            if m["refresh_effective"]:
                renovacoes.append(reg)
            elif m["article_date"] < o["initial_date"]:
                retro.append(reg)          # §30 — nunca move para tras
            else:
                reg["motivo"] = "material ja E a ancora"
                retro.append(reg)
        # §29 — controle negativo: ETAPA/ACOMPANHAMENTO posteriores a ancora
        for m in o["membros"]:
            if (m["phase"] in (ETAPA, ACOMPANHAMENTO)
                    and m["article_date"] > o["anchor_date"]):
                negativos.append({
                    "company": o["company"], "family": o["family"],
                    "occurrence_id": o["occurrence_id"], "fase": m["phase"],
                    "membro": m["article_id"], "membro_date": m["article_date"],
                    "ancora": o["anchor_date"],
                    "renovou": m["refresh_effective"],
                    "titulo": m["title"][:110]})

    delta_emp, status_delta = [], []
    # onde o modelo de status derivado nao reproduz nem a propria producao, um
    # "delta de status" seria artefato de medicao, nao achado de arquitetura
    infiel = set(sim["modelo_status_fidelidade"]["divergentes"])
    for emp, v in sim["empresas"].items():
        p = prod["empresas"].get(emp)
        if not p:
            continue
        d = round(v["simulated_total_score"] - p["total_score"], 1)
        if abs(d) >= 0.05:
            delta_emp.append({"company": emp, "producao": p["total_score"],
                              "sombra_simulado": v["simulated_total_score"],
                              "delta": d})
        if emp in infiel or v["simulated_status"] == p["status"]:
            continue
        causa = [x for x in divisoes + fusoes if x["company"] == emp]
        # §33 — nomear a causa NAO e o mesmo que ter respaldo humano para ela.
        # Uma mudanca de status so deixa de bloquear quando a divisao que a
        # produziu foi confirmada por humano; estar no lote de supervisao por
        # outro motivo nao serve de aval.
        status_delta.append({
            "company": emp, "producao": p["status"],
            "sombra_simulado": v["simulated_status"],
            "delta_score": d,
            "explicado": bool(causa),
            "suportado_por_humano": bool(causa) and all(
                x["confianca"] == "HUMAN_CONFIRMED" for x in causa),
            "causa": [f"{x['family']}: {x['producao_ocorrencias']}->"
                      f"{x['sombra_ocorrencias']} ({x['confianca']})"
                      for x in causa]})
    return {
        "sobre_fusao_divisoes": sorted(divisoes,
                                       key=lambda x: -abs(x["delta_score_simulado"])),
        "sub_fusao_fusoes": fusoes,
        "renovacoes": sorted(renovacoes, key=lambda x: x["membro_material_date"]),
        "renovacoes_bloqueadas_ou_ja_ancoradas": retro,
        "controle_negativo_sem_renovacao": negativos,
        "delta_score_por_empresa": sorted(delta_emp, key=lambda x: -abs(x["delta"])),
        "delta_status": status_delta,
        "delta_score_total": round(sum(x["delta"] for x in delta_emp), 1),
        "authority": "SHADOW / SIMULATED"}


# ── §41 · fila de revisao, no maximo 10, priorizada ─────────────────────────
def fila_revisao(B: dict, M: dict, limite: int = 10) -> list:
    fila = []
    for x in B["delta_status"]:
        fila.append({"prioridade": 1, "tipo": "STATUS_IMPACT",
                     "company": x["company"], "detalhe": x,
                     "veredito": "REVIEW_CANDIDATE"})
    for l in M["linhas"]:
        if l.get("avaliavel") and (l["identidade_ok"] is False
                                  or l["fase_ok"] is False
                                  or l["refresh_ok"] is False):
            fila.append({"prioridade": 1, "tipo": "HUMAN_DISAGREEMENT",
                         "company": l["company"], "case_id": l["case_id"],
                         "detalhe": {"family": l["family"], "titulo": l["titulo"],
                                     "identidade": (l["identidade_humana"],
                                                    l["identidade_sombra"]),
                                     "fase": (l["fase_humana"], l["fase_sombra"]),
                                     "refresh": (l["refresh_humano"],
                                                 l["refresh_elegivel_sombra"])},
                         "veredito": "REVIEW_CANDIDATE"})
    for x in B["sobre_fusao_divisoes"]:
        if x["confianca"] == "HUMAN_CONFIRMED":
            continue
        fila.append({"prioridade": 2 if abs(x["delta_score_simulado"]) >= 10 else 3,
                     "tipo": "SPLIT_" + x["confianca"], "company": x["company"],
                     "detalhe": x, "veredito": "REVIEW_CANDIDATE"})
    for x in B["renovacoes"]:
        if x["classificacao"] == "UNREVIEWED":
            fila.append({"prioridade": 2, "tipo": "REFRESH_UNREVIEWED",
                         "company": x["company"], "detalhe": x,
                         "veredito": "REVIEW_CANDIDATE"})
    fila.sort(key=lambda x: (x["prioridade"], x["company"]))
    return fila[:limite]


# ── §42 · metricas de promocao e bloqueadores ───────────────────────────────
def colisoes_de_id(S: dict) -> list:
    """Dois eventos economicos distintos com o MESMO occurrence_id sao um
    defeito de identidade, nao um detalhe. Detectado e reportado em vez de
    remendado com sufixo — sufixo por ordem seria o indice de cluster de volta,
    que §3 proibe."""
    por_id: dict = defaultdict(list)
    for o in S["ocorrencias"]:
        por_id[o["occurrence_id"]].append(o)
    return [{"occurrence_id": k, "n": len(v), "company": v[0]["company"],
             "family": v[0]["family"],
             "aberturas": [x["initial_date"] for x in v]}
            for k, v in sorted(por_id.items()) if len(v) > 1]


def promocao(S, prod, M, T, B, F) -> dict:
    bloqueios = []
    col = colisoes_de_id(S)
    if col:
        bloqueios.append(f"colisao de occurrence_id: {len(col)}")
    if M["refresh"]["erros"]:
        bloqueios.append("ancora confirmada por humano falha: "
                         f"{M['refresh']['erros']} caso(s)")
    if M["identidade"]["erros"]:
        bloqueios.append(f"identidade humana falha: {M['identidade']['erros']} caso(s)")
    if M["fase"]["erros"]:
        bloqueios.append(f"fase humana falha: {M['fase']['erros']} caso(s)")
    if T["fase"]["erros"] or T["refresh"]["erros"]:
        bloqueios.append("occurrence_truth diverge")
    naoexp = [x for x in B["delta_status"] if not x["explicado"]]
    if naoexp:
        bloqueios.append(f"status muda sem explicacao: {len(naoexp)} empresa(s)")
    semaval = [x for x in B["delta_status"]
               if x["explicado"] and not x["suportado_por_humano"]]
    if semaval:
        bloqueios.append("status muda por divisao ainda NAO confirmada por "
                         f"humano: {[x['company'] for x in semaval]}")
    if F["membros_sem_article_id"]:
        bloqueios.append("proveniencia perdida na sombra")
    amb = [x for x in B["sobre_fusao_divisoes"] if x["confianca"] == "AMBIGUOUS"]
    return {
        "acordo_identidade_humana": M["identidade"],
        "acordo_fase_humana": M["fase"],
        "acordo_renovacao_humana": M["refresh"],
        "acordo_occurrence_truth": {"fase": T["fase"], "refresh": T["refresh"],
                                    "avaliaveis": T["memberships_avaliaveis"]},
        "colisoes_de_id": col,
        "divisoes_inexplicadas": len(amb),
        "fusoes_inexplicadas": len(B["sub_fusao_fusoes"]),
        "blast_score_total": B["delta_score_total"],
        "blast_status": len(B["delta_status"]),
        "retencao_de_proveniencia": (
            f"{F['membros_com_article_id']}/{F['membros_sombra']}"),
        "bloqueadores": bloqueios,
        "pronto_para_promover": not bloqueios,
        "authority": "SHADOW / SIMULATED"}


def rodar_tudo(historico="risk_history.json", config="config_risco.yaml") -> dict:
    S = construir(historico, config)
    prod = rp.reproduzir(historico, config)
    sim = simular(S, prod)
    M = matriz_humana(S, None, historico)
    T = avaliar_occurrence_truth(S)
    F = fidelidade(S, prod)
    B = blast(S, prod, M, sim)
    return {"_meta": {"shadow_version": SHADOW_VERSION, **AUTORIDADE},
            "sombra": S, "producao": prod, "simulacao": sim, "matriz_humana": M,
            "occurrence_truth": T, "fidelidade": F, "blast": B,
            "fila_revisao": fila_revisao(B, M),
            "politica_familias": politica_familias(),
            "colisoes_de_id": colisoes_de_id(S),
            "matriz_materialidade": matriz_materialidade(config),
            "prontidao": prontidao(S, prod, M, T, B, F, sim, config),
            "promocao": promocao(S, prod, M, T, B, F),
            "equivalencia_producao": rp.equivalencia(prod)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Occurrence Shadow V1 — diagnostico, SEM autoridade.")
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--secao", default="resumo",
                   choices=("resumo", "matriz", "blast", "fila", "promocao", "json"))
    a = p.parse_args(argv)
    R = rodar_tudo(a.historico)
    if a.secao == "json":
        print(json.dumps(R, ensure_ascii=False, default=str)[:400000])
        return 0
    if a.secao in ("resumo", "promocao"):
        print("SHADOW / SIMULATED — autoridade de producao: NENHUMA")
        print(json.dumps({"corpus": R["sombra"]["corpus"],
                          "candidatos": R["sombra"]["candidatos"],
                          "ocorrencias_sombra": len(R["sombra"]["ocorrencias"]),
                          "ocorrencias_producao": len(R["producao"]["ocorrencias"]),
                          "fidelidade": R["fidelidade"],
                          "promocao": R["promocao"]}, ensure_ascii=False, indent=1))
    if a.secao == "matriz":
        for l in R["matriz_humana"]["linhas"]:
            print(json.dumps(l, ensure_ascii=False))
    if a.secao == "blast":
        print(json.dumps(R["blast"], ensure_ascii=False, indent=1))
    if a.secao == "fila":
        print(json.dumps(R["fila_revisao"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
