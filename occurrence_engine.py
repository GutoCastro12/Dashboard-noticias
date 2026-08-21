#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
occurrence_engine.py — identidade de ocorrência e autoridade de score.

Motor de PRODUÇÃO. Consolida duas coisas que o pipeline tratava coladas:

    QUE EVENTOS ECONÔMICOS EXISTEM        (identidade de ocorrência)
    QUAIS DELES SOMAM RISCO               (autoridade de score)

A primeira vem da arquitetura validada em sombra (`reliability_occurrence_
shadow.py`, ondas R7q–R7s); a segunda, da decisão humana de 2026-08-21.

POR QUE AS DUAS JUNTAS
----------------------
O total do emissor é a soma de UMA contribuição por `_occ_key`. Mudar a
identidade de ocorrência muda o score por construção. Separar as promoções
exigiria manter duas chaves de ocorrência vivas ao mesmo tempo — uma para
exibir e outra para pontuar —, que é justamente a verdade dupla que o projeto
recusou.

A DECISÃO HUMANA
----------------
Um evento de família declarada `direction: neutra` continua **material,
visível, com membros, fase, âncora e representante** — mas NÃO soma pontos de
risco pelo simples fato de existir, e NÃO conta como tipo negativo. Famílias
`positiva`/`mitigadora` idem, e nunca subtraem: um evento favorável não abate
um default. Famílias `negativa` preservam a mecânica atual, intacta.

UMA ÚNICA FONTE DE VERDADE
--------------------------
`tem_autoridade_adversa()` é consultada pela contribuição, pela contagem de
tipos negativos e pelo gatilho de evento crítico. Nada disso infere autoridade
de "score > 0" — arredondamento e decaimento tornariam a regra circular.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from collections import defaultdict

import risk_dashboard as _rd

ENGINE_VERSION = "occurrence.engine.v1"
POLITICA_HUMANA = "HUMAN_SCORE_POLICY_2026_08_21"

# ── autoridade de score ─────────────────────────────────────────────────────
ADVERSA = "ADVERSE"
CONTEXTUAL = "CONTEXT_DEPENDENT"
FAVORAVEL = "FAVORABLE"
DESCONHECIDA = "UNKNOWN"


def direcao_de(ev: dict) -> str:
    """Classificação canônica, tirada do campo `direction` que a taxonomia já
    declara. Nenhuma lista paralela de famílias é mantida em código."""
    d = (ev.get("direction") or "").strip().lower()
    if d == "negativa":
        return ADVERSA
    if d == "neutra":
        return CONTEXTUAL
    if d in ("positiva", "mitigadora"):
        return FAVORAVEL
    return DESCONHECIDA


def tem_autoridade_adversa(ev: dict) -> bool:
    """FONTE ÚNICA de autoridade de score adverso.

    Só família declarada `negativa` soma risco. `neutra` é material sem ser
    adversa; `positiva`/`mitigadora` são favoráveis e nunca subtraem; ausência
    de declaração não fabrica sentido adverso a partir de materialidade."""
    return direcao_de(ev) == ADVERSA


# ── papel do marcador ───────────────────────────────────────────────────────
# Um marcador de regulador segue útil como CONTEXTO; ele só não pode CARREGAR
# identidade, porque aparece em transações distintas — foi `cade` que uniu a
# EMAE à Sanessol na Sabesp.
OBJETO, CONTEXTO, REGULADOR, GENERICO = (
    "OBJECT_MARKER", "CONTEXT_MARKER", "REGULATOR_MARKER", "GENERIC_MARKER")

_REGULADORES = frozenset({
    "cade", "cvm", "anp", "aneel", "bacen", "susep", "antt", "antaq", "ans",
    "anatel", "accc", "sec", "ftc", "doj", "cofece", "sunass", "osinergmin",
    "indecopi", "superintendencia", "tribunal", "conselho", "justica",
    "supremo", "stf", "stj", "comissao", "reguladora", "agencia", "procon",
    "receita", "conar", "arsesp",
})
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
    "como", "quem", "onde", "quando", "porque", "depois", "desde", "apenas",
    "mesmo", "assim", "entao", "tambem", "enquanto", "embora", "sera",
})
_CONTEXTUAIS = frozenset({
    "reuters", "bloomberg", "valor", "estadao", "folha", "infomoney",
    "exame", "neofeed", "brazil", "journal", "times", "relatorio",
    "analistas", "analista", "investidores", "acionistas", "governo",
    "uniao", "europeia", "federal", "estadual", "municipal",
})


def papel_marcador(token: str) -> str:
    t = (token or "").strip().lower()
    if not t:
        return GENERICO
    if t in _REGULADORES:
        return REGULADOR
    if t in _GENERICOS or t.isdigit() or len(t) < 3:
        return GENERICO
    if t in _CONTEXTUAIS:
        return CONTEXTO
    return OBJETO


# `_marcadores_operacao` exige nome próprio com 4+ caracteres e por isso perde
# "Omã", que é o objeto de uma das transações de M&A da JBS.
_RX_PROPRIO_CURTO = re.compile(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zà-ÿ]{2})\b")


def marcadores_por_papel(titulo: str, event_id: str, empresa: str,
                         aliases=None) -> dict:
    ident = _rd.occurrence_identity(titulo or "", event_id, empresa, aliases)
    saida = {OBJETO: set(), CONTEXTO: set(), REGULADOR: set(), GENERICO: set()}
    brutos = [m for m in (ident.get("marcadores") or "").split("|") if m]
    ali = {_rd.normalize(a) for a in (list(aliases or []) + [empresa]) if a}
    for m in _RX_PROPRIO_CURTO.findall(titulo or ""):
        n = _rd.normalize(m)
        if n not in ali and n not in _rd._STOP_MARCADORES:
            brutos.append(n)
    for m in brutos:
        saida[papel_marcador(m)].add(m)
    return saida


# Em M&A o objeto é o ALVO, não qualquer parte nomeada: "JBS propõe aquisição
# da Pilgrim's" e "Pilgrim's anuncia aquisição da Walkers" citam ambos a
# Pilgrim's, mas numa ela é alvo e na outra compradora. A pista só vale seguida
# de conectivo de complemento — senão "aquisição DECORRE do Compromisso"
# tomaria o compromisso como alvo.
_RX_CUE_AQUISICAO = re.compile(
    r"\b(?:aquisi[cç][aã]o|aquisi[cç][oõ]es|compra|adquir\w*|"
    r"incorpora[cç][aã]o|takeover|acquisition|acquires?|to\s+acquire|"
    r"stake)\s+(?:d[aeo]s?|pel[ao]s?|em|of|in|no|na|nos|nas)\b", re.I)


def alvo_da_transacao(titulo: str, event_id: str, empresa: str,
                      aliases=None) -> set:
    m = _RX_CUE_AQUISICAO.search(titulo or "")
    if not m:
        return set()
    depois = (titulo or "")[m.end():]
    if not depois.strip():
        return set()
    return marcadores_por_papel(depois, event_id, empresa, aliases)[OBJETO]


# ── fases ───────────────────────────────────────────────────────────────────
INICIACAO, ETAPA, MATERIAL, ACOMPANHAMENTO, DESCONHECIDO = (
    "INICIACAO", "ETAPA", "MATERIAL", "ACOMPANHAMENTO", "UNKNOWN")
FASES = (INICIACAO, ETAPA, MATERIAL, ACOMPANHAMENTO, DESCONHECIDO)

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
    r"\bcompra\w*|\badquir\w*|\bagrees?\s+to\b|\bto\s+acquire\b|\bdeal\b",
    re.I)
_RX_ACOMPANHAMENTO = re.compile(
    r"\bcoment\w*|\bcomment\w*|\bopini\w*|\bapos\s+a?\s*(?:conclus|aquisic|fusao)|"
    r"\bimpacto\s+d[ao]\b|\breflete\w*|\bo\s+que\s+muda\b|\brelembr\w*|"
    r"\bretrospect\w*|\brecap\w*|\bexplica\w*|\bentenda\b|\banalistas?\s+v[eê]\w*|"
    r"\bwhat\s+it\s+means\b|\bconsequenci\w*|\bclientes?\s+(?:reclam|relat)\w*|"
    r"\b(?:diz|afirma|declara|comenta|segundo)\s+(?:o\s+|a\s+)?"
    r"(?:ceo|cfo|coo|cro|diretor\w*|president\w*|analist\w*|banco\b)|"
    r"\bafeta\s+(?:quem|os?\s|as?\s)|\bquem\s+est[aá]\s+esperando\b", re.I)
# "lucra R$ 694 mi no 2o tri E CONCLUI follow-on" não é fechamento: a asserção
# primária é o resultado, e a conclusão vem em oração coordenada, referindo
# fato que já ocorreu. A palavra `conclui` sozinha não pode decidir fase.
_ASSERCAO_PRIMARIA_OUTRA = (
    r"lucr\w*|preju[ií]z\w*|resultad\w*|receita\w*|ebitda|balanc\w*|"
    r"fatur\w*|margem|dividend\w*|jcp\b|earnings|revenue|profit\w*|"
    r"posts?\b|reports?\b")
_RX_RECAP_COORDENADO = re.compile(
    r"\b(?:" + _ASSERCAO_PRIMARIA_OUTRA + r")\b[^,;]{0,80}?"
    r"\s+(?:e|and)\s+(?:tamb[eé]m\s+)?"
    r"(?:conclui\w*|finaliz\w*|encerr\w*|complet\w*|fecha\b)", re.I)
# Vínculo econômico EXPLÍCITO com um compromisso anterior — não similaridade de
# nome: é o texto dizendo que este ato DECORRE daquele.
_RX_DECORRE_COMPROMISSO = re.compile(
    r"decorre\s+d[oa]s?\s+(?:compromisso|acordo|contrato)\w*|"
    r"em\s+cumprimento\s+a[o]?\s+(?:compromisso|acordo)|"
    r"nos\s+termos\s+d[oa]\s+(?:compromisso|acordo)|"
    r"pursuant\s+to\s+the\s+(?:binding\s+)?(?:commitment|agreement)|"
    r"under\s+the\s+(?:binding\s+)?commitment|"
    r"(?:m[ií]nimo|patamar|limiar|piso)\s+(?:estabelecid\w+\s+)?"
    r"d[oa]\s+(?:compromisso|acordo)|"
    r"atinge\s+o\s+(?:m[ií]nimo|patamar|limiar)\s+"
    r"(?:comprometid\w+|estabelecid\w+)", re.I)
# Fora das famílias em que a ação da casa É o evento, uma casa reiterando
# recomendação está COMENTANDO um fato, não praticando-o.
_FAMILIA_DE_ANALISTA = frozenset({"recomendacao_negativa", "rebaixamento_rating"})
_RX_ANALISTA_PRIMARIO = re.compile(
    r"^[^,;:]{0,60}?\b(?:reitera\w*|mant[eé]m|eleva\w*|reduz\w*|corta\b|"
    r"rebaix\w*|inicia\s+cobertura|reafirm\w*)\s+"
    r"(?:a\s+|o\s+|sua\s+)?(?:recomenda\w+|pre[cç]o[- ]alvo|rating|"
    r"cobertura|classifica\w+|target)", re.I)
_RX_ABRE_PROCESSO = re.compile(
    r"\babre\s+(?:um\s+)?(?:processo|inqu[eé]rito|investiga\w+)|"
    r"\binstaura\w*\s+(?:processo|inqu[eé]rito)|"
    r"\bopens?\s+(?:an?\s+)?(?:proceeding|investigation|probe)", re.I)
_RX_PROCESSO_ID = re.compile(
    r"\b(?:processo|proc\.?|inqu[eé]rito|pas)\s*(?:administrativo\s*)?"
    r"(?:sancionador\s*)?n?[.ºo°]*\s*"
    r"(\d{2,5}[./]\d{3,6}[./-]?\d{0,6}(?:[-/]\d{1,4})?)", re.I)
_RX_NIVEL_RATING = re.compile(
    r"\b(?:para|to|em|for)\s+[‘'\"]?"
    r"((?:aaa|aa|a|bbb|bb|b|ccc|cc|c|d)[+-]?|"
    r"(?:aaa|aa|a|baa|ba|b|caa|ca|c)[123])"
    r"[’'\"]?(?:\s|,|$|\.)", re.I)
_RX_AGENCIA = re.compile(
    r"\b(moody\w*|fitch|s&p|standard\s*&\s*poor\w*|austin\s+rating|dbrs|scope)\b",
    re.I)
_RX_DIRECAO_RATING = re.compile(
    r"\b(rebaix\w*|downgrade\w*|corta\b|eleva\w*|upgrade\w*|reafirm\w*|"
    r"mant[eê]m\b|revis\w+|perspectiva\s+negativa|outlook\s+negative)\b", re.I)

# Famílias cujo evento é PONTUAL: a própria asserção do fato é a iniciação
# ("Moody's rebaixa o rating da X" não tem verbo de fase, e não precisa ter).
_FAMILIA_PONTUAL = frozenset({
    "rebaixamento_rating", "recomendacao_negativa", "investigacao_regulatoria",
    "incidente_operacional", "incidente_operacional_grave",
    "disrupcao_operacional", "guidance_negativo", "troca_ceo",
})


def fase_de(titulo: str, family: str = "") -> dict:
    """Quatro estados + UNKNOWN. Não força classificação sem evidência."""
    t = titulo or ""
    bruta = _rd._fase_do_evento(t)
    if family not in _FAMILIA_DE_ANALISTA:
        m = _RX_ANALISTA_PRIMARIO.search(t)
        if m:
            return {"fase": ACOMPANHAMENTO, "fase_bruta": bruta,
                    "fase_evidencia": "analista_como_assercao_primaria:"
                                      + m.group(0)[:44]}
    if family == "troca_ceo":
        import semantic_audit as _sa
        d = _sa.detect_troca_ceo_sem_assercao(t, t)
        if d:
            return {"fase": ACOMPANHAMENTO, "fase_bruta": bruta,
                    "fase_evidencia": "descritor_sem_assercao:" + d[:44]}
    m = _RX_RECAP_COORDENADO.search(t)
    if m:
        return {"fase": ACOMPANHAMENTO, "fase_bruta": bruta,
                "fase_evidencia": "recap_coordenado:" + m.group(0)[:52]}
    m = _RX_DECORRE_COMPROMISSO.search(t)
    if m:
        return {"fase": MATERIAL, "fase_bruta": bruta,
                "fase_evidencia": "decorre_de_compromisso:" + m.group(0)[:52]}
    for rx, fase in ((_RX_MATERIAL, MATERIAL), (_RX_ACOMPANHAMENTO, ACOMPANHAMENTO),
                     (_RX_ETAPA, ETAPA), (_RX_INICIACAO, INICIACAO)):
        m = rx.search(t)
        if m:
            return {"fase": fase, "fase_evidencia": m.group(0), "fase_bruta": bruta}
    _map = {"encerramento": MATERIAL, "aprovacao": ETAPA, "precificacao": ETAPA,
            "anuncio": INICIACAO}
    if bruta in _map:
        return {"fase": _map[bruta], "fase_evidencia": "bruta:" + bruta,
                "fase_bruta": bruta}
    if family in _FAMILIA_PONTUAL:
        return {"fase": INICIACAO, "fase_evidencia": "familia_pontual",
                "fase_bruta": bruta}
    return {"fase": DESCONHECIDO, "fase_evidencia": "", "fase_bruta": bruta}


# ── alias DECLARADO ─────────────────────────────────────────────────────────
ARQUIVO_ALIAS = "occurrence_alias_shadow.json"
_ALIAS_CACHE: dict = {}


def carregar_aliases(caminho: str = ARQUIVO_ALIAS) -> dict:
    """Alias de objeto econômico, com fonte. Nunca inferido por similaridade de
    nome: `clark|kimberly` e `arbex|suzb3` são disjuntos, e só uma declaração
    humana os une."""
    if caminho in _ALIAS_CACHE:
        return _ALIAS_CACHE[caminho]
    try:
        d = json.load(io.open(caminho, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"aliases": []}
    fora: dict = {}
    for a in d.get("aliases", []):
        if a.get("claim") != "SAME_OBJECT" or not a.get("source"):
            continue
        fora.setdefault((a.get("company"), a.get("family")), []).append({
            "alias_id": a["alias_id"], "canonical": a["canonical"],
            "members": {m.lower() for m in a.get("members", [])}})
    _ALIAS_CACHE[caminho] = fora
    return fora


def canonicalizar(tokens: set, company: str, family: str, aliases: dict) -> tuple:
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


# ── discriminantes de instância por família ─────────────────────────────────
# A identidade NÃO pode ser `empresa|família|objeto`: a Hapvida tem duas trocas
# de CEO reais em quatro meses, e 16ª × 17ª emissão são dois eventos sobre o
# mesmo instrumento. Camada de INSTÂNCIA separada.
_DISCRIMINANTE = {
    "ma": ("valor",), "emissao_divida": ("serie",), "emissao_cotas": ("serie",),
    "follow_on": ("serie",), "troca_ceo": ("pessoas",),
    "rebaixamento_rating": ("agencia", "direcao"),
    "recomendacao_negativa": ("instituicao", "direcao"),
    "incidente_operacional_grave": ("local",), "disrupcao_operacional": ("local",),
    "investigacao_regulatoria": ("regulador",),
}
# Famílias cujo evento é do PRÓPRIO emissor: a oferta, a emissão, a recuperação
# judicial, a ação de rating não têm "objeto externo". Um nome próprio ali é o
# destino dos recursos ou um terceiro citado de passagem.
_FAMILIA_SEM_OBJETO_EXTERNO = frozenset({
    "follow_on", "emissao_divida", "emissao_cotas", "recuperacao_judicial",
    "rebaixamento_rating", "recomendacao_negativa",
})
_GAP_FAMILIA = {"ma": 240, "emissao_divida": 120, "emissao_cotas": 120,
                "follow_on": 120, "troca_ceo": 90, "rebaixamento_rating": 60,
                "recomendacao_negativa": 45, "recuperacao_judicial": 400,
                "incidente_operacional_grave": 30, "disrupcao_operacional": 30,
                "investigacao_regulatoria": 180}


def _features(m: dict, family: str) -> tuple:
    """(chave discriminante, forte?). Vazia significa 'não identificável' — e o
    chamador cai na âncora de data, marcando `DATE_ANCHORED`."""
    if family in ("ma",):
        forte = bool(m["objeto_tokens"]) and bool(m["valor"])
        vals = {"valor": m["valor"]}
    elif family in ("emissao_divida", "emissao_cotas", "follow_on"):
        forte, vals = bool(m["serie"]), {"serie": m["serie"]}
    elif family == "troca_ceo":
        forte, vals = bool(m["pessoas"]), {"pessoas": "|".join(m["pessoas"])}
    elif family == "rebaixamento_rating":
        forte = bool(m["agencia"]) and bool(m["direcao_rating"])
        vals = {"agencia": m["agencia"], "direcao": m["direcao_rating"]}
    elif family == "recomendacao_negativa":
        forte = False
        vals = {"instituicao": m["agencia"], "direcao": m["direcao_rating"]}
    elif family == "investigacao_regulatoria":
        forte, vals = bool(m["processo_id"]), {"regulador": m["regulador"]}
    elif family in ("incidente_operacional_grave", "disrupcao_operacional"):
        forte, vals = bool(m["locais"]), {"local": "|".join(sorted(m["locais"]))}
    else:
        return "", False
    if not forte:
        return "", False
    campos = _DISCRIMINANTE.get(family, ())
    v = [str(vals.get(c, "")) for c in campos]
    return ("::".join(v) if all(v) else ""), True


def _mesmo_episodio_rating(a, b, janela_dias):
    if abs(a["pub_ts"] - b["pub_ts"]) <= janela_dias * 86400:
        return True
    na, nb = a.get("nivel_rating", ""), b.get("nivel_rating", "")
    if na and nb:
        return na == nb
    # Sem nível comparável e fora da janela, cada publicação vale como ação:
    # a Moody's rebaixou a Cosan em 16/07 (para B1) e de novo em 10/08.
    return False


def _mesmo_episodio_investigacao(a, b, janela_dias):
    pa, pb = a.get("processo_id", ""), b.get("processo_id", "")
    if pa and pb:
        return pa == pb
    if a.get("abre_processo") and b.get("abre_processo"):
        return bool(a["objeto_tokens"] & b["objeto_tokens"])
    if b.get("abre_processo") and not a.get("abre_processo"):
        # Abrir processo é asserção de procedimento NOVO: sem identificador que
        # ligue os dois, não é corroboração de artigo anterior.
        return False
    if abs(a["pub_ts"] - b["pub_ts"]) <= janela_dias * 86400:
        return True
    return bool(a["objeto_tokens"] & b["objeto_tokens"])


_MESMO_EPISODIO = {
    "rebaixamento_rating": _mesmo_episodio_rating,
    "recomendacao_negativa": _mesmo_episodio_rating,
    "investigacao_regulatoria": _mesmo_episodio_investigacao,
}
_CHAVE_EPISODIO = {
    "rebaixamento_rating": lambda m: m.get("nivel_rating", ""),
    "recomendacao_negativa": lambda m: m.get("nivel_rating", ""),
    "investigacao_regulatoria": lambda m: m.get("processo_id", ""),
}


# ── identidade de ocorrência ────────────────────────────────────────────────
def _traços(cand: dict, family: str, company: str, aliases_emp,
            aliases_decl: dict, opt_in: bool) -> dict:
    """Extrai do candidato de produção tudo que a identidade precisa. Não muda
    o candidato: devolve um envelope."""
    titulo = cand.get("title") or ""
    papeis = marcadores_por_papel(titulo, family, company, aliases_emp)
    locais = sorted(_rd._marcadores_locais_operacionais(titulo, company, aliases_emp))
    if opt_in:
        # Família opt-in: quem identifica o fato é a INSTALAÇÃO, e é esse mesmo
        # marcador que o gate legado já usava.
        brutos = set(locais)
    elif family in _FAMILIA_SEM_OBJETO_EXTERNO:
        brutos = set()
    else:
        brutos = set(papeis[OBJETO])
        alvo = alvo_da_transacao(titulo, family, company, aliases_emp)
        if alvo:
            brutos = set(alvo)
    obj_expl = _rd.normalize(cand.get("subject_company") or "")
    if obj_expl:
        brutos |= set(obj_expl.split())
    brutos = {t for t in brutos if papel_marcador(t) == OBJETO}
    tokens, alias_ids = canonicalizar(brutos, company, family, aliases_decl)
    f = fase_de(titulo, family)
    _ag = _RX_AGENCIA.search(titulo)
    _dr = _RX_DIRECAO_RATING.search(titulo)
    _nr = _RX_NIVEL_RATING.search(titulo)
    _pi = _RX_PROCESSO_ID.search(titulo)
    return {
        "cand": cand, "family": family, "company": company,
        "pub_ts": cand.get("pub_ts") or 0,
        "article_id": cand.get("article_id") or "",
        "objeto_tokens": tokens, "alias_ids": alias_ids,
        "marcadores_regulador": sorted(papeis[REGULADOR]),
        "locais": locais, "marcador_nao_contradiz": opt_in,
        "serie": _rd._serie_da_operacao(titulo),
        "valor": _rd._valor_da_operacao(titulo),
        "pessoas": _pessoas(titulo, company, aliases_emp),
        "agencia": _ag.group(1).lower() if _ag else "",
        "direcao_rating": _dr.group(1).lower() if _dr else "",
        "nivel_rating": _nr.group(1).lower() if _nr else "",
        "regulador": "|".join(sorted(papeis[REGULADOR])),
        "processo_id": _pi.group(1) if _pi else "",
        "abre_processo": bool(_RX_ABRE_PROCESSO.search(titulo)),
        **f,
    }


_RX_PESSOA = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zà-ÿ]{2,})\s+"
    r"((?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zà-ÿ]{2,})"
    r"(?:\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zà-ÿ]{2,}))?")


def _pessoas(titulo: str, empresa: str, aliases_emp) -> list:
    ali = {_rd.normalize(a) for a in (list(aliases_emp or []) + [empresa]) if a}
    fora = []
    for m in _RX_PESSOA.finditer(titulo or ""):
        nome = _rd.normalize(" ".join(p for p in m.groups() if p))
        if any(a and (a in nome or nome in a) for a in ali if a):
            continue
        if any(papel_marcador(p) != OBJETO for p in nome.split()):
            continue
        fora.append(nome)
    return sorted(set(fora))


def _uniao_por_intersecao(itens: list) -> list:
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


def _abertura(inst: list) -> dict:
    """O artigo que ABRE a ocorrência: a INICIAÇÃO mais antiga, ou o membro
    mais antigo. Não é o representante (que pode mudar), nem o mais recente,
    nem índice de cluster."""
    abre = [m for m in inst if m["fase"] == INICIACAO]
    return min(abre or inst, key=lambda m: (m["pub_ts"], m["article_id"]))


def _objeto_canonico(inst: list) -> tuple:
    """Sai do MEMBRO DE ABERTURA. Token mais frequente ou núcleo comum QUEBRAM
    a estabilidade do id: frequência cresce e núcleo encolhe quando um membro
    entra. A abertura é imutável sob acréscimo posterior."""
    abre = _abertura(inst)
    toks = sorted(abre["objeto_tokens"])
    if not toks:
        return "", "UNKNOWN"
    nucleo = set.intersection(*[m["objeto_tokens"] for m in inst])
    return "|".join(toks), ("STRONG" if nucleo else "WEAK")


def _instancias(grupo: list, family: str, janela: int) -> list:
    if any(m.get("marcador_nao_contradiz") for m in grupo):
        # Família opt-in: a identidade É o marcador de instalação e não expira
        # com o tempo. Aplicar discriminante ou corte por gap re-partiria o que
        # o agrupamento por objeto já uniu.
        return [sorted(grupo, key=lambda x: (x["pub_ts"], x["article_id"]))]
    gap = _GAP_FAMILIA.get(family, 45) * 86400
    discs, sem = defaultdict(list), []
    for m in grupo:
        d, _ = _features(m, family)
        (discs[d] if d else sem).append(m)
    inst = [sorted(v, key=lambda x: x["pub_ts"]) for v in discs.values()]
    for m in sem:
        if not inst:
            inst.append([m])
            continue
        perto = min(inst, key=lambda g: min(abs(m["pub_ts"] - x["pub_ts"]) for x in g))
        if min(abs(m["pub_ts"] - x["pub_ts"]) for x in perto) <= gap:
            perto.append(m)
            perto.sort(key=lambda x: x["pub_ts"])
        else:
            inst.append([m])
    fn = _MESMO_EPISODIO.get(family)
    final = []
    for g in inst:
        g = sorted(g, key=lambda x: (x["pub_ts"], x["article_id"]))
        atual = [g[0]]
        for ant, cur in zip(g, g[1:]):
            junto = fn(ant, cur, janela) if fn else True
            if not junto or (cur["fase"] == INICIACAO and ant["fase"] == INICIACAO
                             and cur["pub_ts"] - ant["pub_ts"] > gap):
                final.append(atual)
                atual = [cur]
            else:
                atual.append(cur)
        final.append(atual)
    return final


def _slug(nome: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _rd.normalize(nome or "")).strip("-")


def occurrence_id(company: str, family: str, objeto: str, assinatura: str) -> str:
    base = company + "|" + family + "|" + objeto + "|" + assinatura
    return (family + ":" + _slug(company) + ":"
            + hashlib.sha1(base.encode("utf-8")).hexdigest()[:12])


def ancora_efetiva(atual: str, candidata: str) -> str:
    """A âncora NUNCA anda para trás. Uma fase material mais ANTIGA que uma
    âncora já mais nova não move nada — renovar às cegas custaria 28 pontos no
    caso Citigroup."""
    if not candidata:
        return atual
    if not atual:
        return candidata
    return max(atual, candidata)


# Famílias de ESTADO CONTÍNUO DE RISCO: uma investigação ou uma recuperação
# judicial não "acontecem" numa data — o leitor precisa do desenvolvimento
# substantivo mais recente, não da primeira notícia.
_FAMILIA_ESTADO_CONTINUO = frozenset({"investigacao_regulatoria",
                                      "recuperacao_judicial"})
_FASES_SUBSTANTIVAS = (INICIACAO, ETAPA, MATERIAL)
_PESO_FASE_REP = {INICIACAO: 3, MATERIAL: 3, ETAPA: 2,
                  ACOMPANHAMENTO: 1, DESCONHECIDO: 1}


def _representante(inst: list, family: str) -> dict:
    """Família de TRANSAÇÃO: a asserção primária que EXPLICA o fato. Família de
    ESTADO CONTÍNUO: o desenvolvimento substantivo mais recente. Nunca "o mais
    novo" nem "o mais velho" sozinhos."""
    if family in _FAMILIA_ESTADO_CONTINUO:
        subst = [m for m in inst if m["fase"] in _FASES_SUBSTANTIVAS]
        return max(subst or inst,
                   key=lambda m: (m["pub_ts"], m["cand"].get("trust_w") or 0.0,
                                  len(m["cand"].get("title") or "")))
    return max(inst, key=lambda m: (
        _PESO_FASE_REP.get(m["fase"], 1),
        m["cand"].get("trust_w") or 0.0,
        1 if m["cand"].get("confirmation_status") == "confirmado" else 0,
        len(m["cand"].get("title") or ""), -m["pub_ts"]))


# ── ponto de entrada da produção ────────────────────────────────────────────
def _fonte_de(cand: dict, quando: str = "") -> dict:
    """Descreve um membro como fonte corroborante, preservando os campos de
    link que o reparo já resolveu."""
    return {"source": cand.get("source", ""), "domain": cand.get("domain", ""),
            "url": cand.get("url", ""), "when": quando,
            "article_id": cand.get("article_id", ""),
            **{k: cand.get(k) for k in
               ("display_url", "canonical_url", "resolved_url", "link_health",
                "link_render_anchor", "link_label") if cand.get(k) is not None}}


def agrupar_ocorrencias(candidatos: list, company: str, cfg: dict,
                        fam_map: dict, janela_corrob: int,
                        quando_fn=None) -> list:
    """Agrupa os candidatos de UM emissor em ocorrências econômicas.

    Devolve uma lista de dicionários no MESMO contrato que o pipeline já
    consome — um por ocorrência, com `_occ_key`, `corrob` e os campos de
    exibição — acrescido do que a arquitetura nova exige:

        `_ocorrencia`        identidade, fase por membro, âncora, membros
        `_anchor_ts`         data que governa o decaimento (≠ exibição)
        `_score_authority`   se esta ocorrência pode somar risco adverso

    O representante de EXIBIÇÃO fica nos campos visíveis; a ÂNCORA fica em
    `_anchor_ts`. São perguntas diferentes e por isso campos diferentes.
    """
    taxonomy = {e["id"]: e for e in cfg["taxonomy"]}
    aliases_emp = next((c.get("aliases") or [c["name"]]
                        for c in cfg.get("watchlist", [])
                        if c.get("name") == company), None)
    aliases_decl = carregar_aliases()

    por_par: dict = defaultdict(list)
    for cand in candidatos:
        eid = cand["event_id"]
        env = _traços(cand, eid, company, aliases_emp, aliases_decl,
                      bool(fam_map.get(eid)))
        por_par[fam_map.get(eid, eid)].append(env)

    saida = []
    for fam, itens in sorted(por_par.items()):
        if fam in _FAMILIA_SEM_OBJETO_EXTERNO:
            grupos = [list(itens)]
        else:
            nomeados = [m for m in itens if m["objeto_tokens"]]
            anon = [m for m in itens if not m["objeto_tokens"]]
            grupos = _uniao_por_intersecao(nomeados)
            if len(grupos) == 1 and anon and all(
                    m.get("marcador_nao_contradiz") for m in anon):
                grupos[0].extend(anon)
                anon = []
            if grupos:
                resto = []
                for m in anon:
                    # Um ACOMPANHAMENTO se refere a um fato que já existe: sem
                    # objeto próprio ele não pode ABRIR ocorrência, senão um
                    # comentário viraria evento econômico.
                    if m["fase"] != ACOMPANHAMENTO:
                        resto.append(m)
                        continue
                    perto = min(grupos, key=lambda g: min(
                        abs(m["pub_ts"] - x["pub_ts"]) for x in g))
                    perto.append(m)
                anon = resto
            anon.sort(key=lambda m: m["pub_ts"])
            atual: list = []
            for m in anon:
                if atual and m["pub_ts"] - atual[-1]["pub_ts"] <= janela_corrob * 86400:
                    atual.append(m)
                else:
                    if atual:
                        grupos.append(atual)
                    atual = [m]
            if atual:
                grupos.append(atual)

        for grupo in grupos:
            for inst in _instancias(grupo, fam, janela_corrob):
                inst.sort(key=lambda m: (m["pub_ts"], m["article_id"]))
                if fam in _FAMILIA_SEM_OBJETO_EXTERNO:
                    objeto, obj_conf = "", "SELF"
                else:
                    objeto, obj_conf = _objeto_canonico(inst)
                disc = next((_features(m, fam)[0] for m in inst
                             if _features(m, fam)[0]), "")
                abre = _abertura(inst)
                fn_ep = _CHAVE_EPISODIO.get(fam)
                ep = fn_ep(abre) if fn_ep else ""
                if disc:
                    assinatura = "disc:" + disc + (("|ep:" + ep) if ep else "")
                    estabilidade = "CONTENT_STABLE" if (not fn_ep or ep) else "DATE_ANCHORED"
                elif fn_ep and not ep:
                    assinatura, estabilidade = ("epoch:" + abre["cand"]["date"],
                                                "DATE_ANCHORED")
                else:
                    assinatura, estabilidade = ("epoch:" + abre["cand"]["date"],
                                                "DATE_ANCHORED")
                oid = occurrence_id(company, fam, objeto, assinatura)

                ev = taxonomy.get(inst[0]["family"], {})
                autoridade = tem_autoridade_adversa(
                    taxonomy.get(max(inst, key=lambda m: m["cand"]["score"])
                                 ["family"], ev))
                # peso-base do estágio mais grave da família (promoção de
                # estágio já era o comportamento legado)
                pior = max(inst, key=lambda m: m["cand"]["score"])
                rep = _representante(inst, fam)
                if fam in _FAMILIA_ESTADO_CONTINUO:
                    # Risco de ESTADO CONTÍNUO não "aconteceu" numa data: uma
                    # recuperação judicial em curso com notícia fresca é risco
                    # fresco. Ancorar na primeira notícia dividiria por quatro
                    # a recência da RJ da Tok&Stok — que é exatamente o que a
                    # promoção não pode fazer com um adverso.
                    subst = [m for m in inst if m["fase"] in _FASES_SUBSTANTIVAS]
                    ancora_m = max(subst or inst,
                                   key=lambda m: (m["pub_ts"], m["article_id"]))
                    ancora_data = ancora_m["cand"]["date"]
                    motivo = "CONTINUING_STATE::latest_substantive"
                else:
                    ancora_data, ancora_m = inst[0]["cand"]["date"], inst[0]
                    motivo = "INITIAL_MEMBER"
                    for m in inst:
                        if m["fase"] != MATERIAL:
                            continue
                        nova = ancora_efetiva(ancora_data, m["cand"]["date"])
                        if nova != ancora_data:
                            ancora_data, ancora_m = nova, m
                            motivo = "MATERIAL_PHASE::" + (m["fase_evidencia"] or "?")

                base = dict(pior["cand"])
                # exibição vem do representante; peso-base do estágio mais grave
                for k in ("title", "url", "source", "domain", "trust_w",
                          "trust_label", "display_url", "canonical_url",
                          "resolved_url", "link_health", "link_render_anchor",
                          "link_label", "article_id"):
                    if k in rep["cand"]:
                        base[k] = rep["cand"][k]
                corrob = list(base.get("persisted_corrob") or [])
                vistos = {base.get("domain", "")} | {c.get("domain")
                                                     for c in corrob}
                for m in inst:
                    if m is rep:
                        continue
                    d = m["cand"].get("domain", "")
                    if d and d not in vistos:
                        corrob.append(_fonte_de(m["cand"],
                                                quando_fn(m["cand"]) if quando_fn else ""))
                        vistos.add(d)
                    for c in (m["cand"].get("persisted_corrob") or []):
                        if c.get("domain") and c["domain"] not in vistos:
                            corrob.append(c)
                            vistos.add(c["domain"])
                base["corrob"] = corrob
                base["_occ_key"] = oid
                base["_anchor_ts"] = ancora_m["pub_ts"]
                base["_anchor_date"] = ancora_data
                base["_score_authority"] = autoridade
                base["_ocorrencia"] = {
                    "occurrence_id": oid, "company": company, "family": fam,
                    "canonical_object": objeto, "object_confidence": obj_conf,
                    "instance_signature": assinatura, "id_stability": estabilidade,
                    "aliases": sorted({a for m in inst for a in m["alias_ids"]}),
                    "engine_version": ENGINE_VERSION,
                    "score_authority": autoridade,
                    "direction": direcao_de(ev),
                    "initial_date": inst[0]["cand"]["date"],
                    "anchor_date": ancora_data,
                    "anchor_member": ancora_m["article_id"],
                    "refresh_reason": motivo,
                    "display_representative": rep["article_id"],
                    "display_representative_date": rep["cand"]["date"],
                    "families": sorted({m["family"] for m in inst}),
                    "members": [{
                        "article_id": m["article_id"],
                        "article_date": m["cand"]["date"],
                        "phase": m["fase"], "phase_evidence": m["fase_evidencia"],
                        "family": m["family"], "domain": m["cand"].get("domain", ""),
                        "source": m["cand"].get("source", ""),
                        "title": (m["cand"].get("title") or "")[:150],
                        # Um ACOMPANHAMENTO refere fato que já ocorreu: a data
                        # do artigo NÃO é a data do evento, e o acervo não diz
                        # qual é. Declara-se a lacuna.
                        "effective_event_date": (None if m["fase"] == ACOMPANHAMENTO
                                                 else m["cand"]["date"]),
                        "role": ("ANCHOR" if m is ancora_m else
                                 "REPRESENTATIVE" if m is rep else "CORROBORATION"),
                    } for m in inst]}
                saida.append(base)
    return saida
