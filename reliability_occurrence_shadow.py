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


def marcadores_por_papel(titulo: str, event_id: str, empresa: str,
                         aliases=None) -> dict:
    ident = rd.occurrence_identity(titulo or "", event_id, empresa, aliases)
    saida = {OBJECT_MARKER: set(), CONTEXT_MARKER: set(),
             REGULATOR_MARKER: set(), GENERIC_MARKER: set()}
    for m in (ident.get("marcadores") or "").split("|"):
        if m:
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
    r"\bcompra\w*|\badquire\w*|\bagrees?\s+to\b|\bto\s+acquire\b|\bdeal\b",
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


def fase_de(titulo: str, family: str = "") -> dict:
    """Quatro estados + UNKNOWN. Nao forca classificacao sem evidencia."""
    t = titulo or ""
    bruta = rd._fase_do_evento(t)
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
    """§17 — acao de rating: agencia + direcao + episodio."""
    forte = bool(ctx["agencia"]) and bool(ctx["direcao_rating"])
    return {"features": {"agencia": ctx["agencia"], "direcao": ctx["direcao_rating"]},
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


def _ad_fallback(ctx: dict) -> dict:
    """§4 — o fallback e CONSERVADOR, nao 'funde tudo'."""
    return {"features": {"objeto": "|".join(sorted(ctx["objeto_tokens"]))},
            "confidence": "UNKNOWN", "gap_days": 45}


ADAPTADORES = {
    "ma": _ad_ma,
    "emissao_divida": _ad_emissao,
    "emissao_cotas": _ad_emissao,
    "follow_on": _ad_follow_on,
    "troca_ceo": _ad_troca_ceo,
    "rebaixamento_rating": _ad_rating,
    "recomendacao_negativa": _ad_recomendacao,
    "recuperacao_judicial": _ad_recuperacao_judicial,
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
                brutos = set(papeis[OBJECT_MARKER])
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
                    "locais": sorted(rd._marcadores_locais_operacionais(
                        titulo, company, ali_por_emp.get(company))),
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


def _instancias(grupo: list, family: str) -> list:
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

    # gap de INICIACAO: duas aberturas distantes sao dois eventos economicos
    final = []
    for g in inst:
        g = sorted(g, key=lambda x: x["pub_ts"])
        atual = [g[0]]
        for ant, cur in zip(g, g[1:]):
            if (cur["fase"] == INICIACAO and ant["fase"] == INICIACAO
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


def escolher_representante(membros: list) -> dict:
    """Politica deterministica: assercao primaria de evento (fase que EXPLICA o
    fato) > qualidade da fonte > completude do titulo > data mais antiga como
    ultimo desempate. Nunca 'o mais novo' nem 'o mais velho' por si so."""
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
        por_par[(m["company"], m["family"])].append(m)

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
            for inst in _instancias(grupo, fam):
                inst.sort(key=lambda m: (m["pub_ts"], m["article_id"]))
                if fam in _FAMILIA_SEM_OBJETO_EXTERNO:
                    # rotular 'mobly' como objeto da RJ da Tok&Stok seria
                    # mentira de identidade: aqui o objeto e o proprio emissor
                    objeto, obj_conf = "", "SELF"
                else:
                    objeto, obj_conf = _objeto_canonico(inst)
                disc = next((_discriminante(m, fam)[0] for m in inst
                             if _discriminante(m, fam)[0]), "")
                if disc:
                    assinatura, estabilidade = "disc:" + disc, "CONTENT_STABLE"
                else:
                    assinatura = "epoch:" + _membro_de_abertura(inst)["article_date"]
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
                rep = escolher_representante(inst)
                base = max(m["score_base"] or 0 for m in inst)
                contrib = base * (membro_ancora.get("trust_w") or 1.0) * decay(
                    membro_ancora["pub_ts"])
                dominios = {m["domain"] for m in inst if m.get("domain")}
                ocorrencias.append({
                    "occurrence_id": oid,
                    "company": emp,
                    "family": fam,
                    "canonical_object": objeto,
                    "object_confidence": obj_conf,
                    "occurrence_instance_signature": assinatura,
                    "id_stability": estabilidade,
                    "aliases": sorted({a for m in inst for a in m["alias_ids"]}),
                    "n_membros": len(inst),
                    "membros": [{
                        "article_id": m["article_id"],
                        "article_date": m["article_date"], "pub_ts": m["pub_ts"],
                        # §20 — sem inferencia silenciosa: a data efetiva cai na
                        # data do artigo e o rotulo diz que foi fallback
                        "effective_event_date": m["article_date"],
                        "effective_event_date_source": "ARTICLE_DATE_FALLBACK",
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


def matriz_humana(S: dict, caminho_humano: str | None = None,
                  historico="risk_history.json") -> dict:
    import reliability_human_supervision as hs
    MS = hs.carregar(caminho_humano)["memberships"]
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)
    por_artigo = {}
    for o in S["ocorrencias"]:
        for m in o["membros"]:
            por_artigo[(m["article_id"], o["company"], o["family"])] = (o, m)

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
        janela = None
        if rel_h == "SAME_OCCURRENCE" and rel_s == "NEW_OCCURRENCE":
            janela = _antecessor_fora_da_janela(
                h["company"], h["family"], set(m["objeto_tokens"]),
                m["pub_ts"], H, S["window_days"])
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
                              or janela else rel_h == rel_s),
            "identidade_limitada_pela_janela": janela,
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
            por_artigo[(m["article_id"], o["company"], o["event_id"]
                        if "event_id" in o else o["family"])] = (o, m)
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
def _humano_confirmado(M: dict, company: str, family: str) -> bool:
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
        status_delta.append({
            "company": emp, "producao": p["status"],
            "sombra_simulado": v["simulated_status"],
            "delta_score": d,
            "explicado": bool(causa) or any(
                l.get("avaliavel") and l["company"] == emp for l in M["linhas"]),
            "causa": [f"{x['family']}: {x['producao_ocorrencias']}->"
                      f"{x['sombra_ocorrencias']}" for x in causa]})
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
