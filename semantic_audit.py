#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
semantic_audit.py — Auditoria semântica bloqueante.

Generaliza a lógica que funcionou em Vale/Samarco: identificar o SUJEITO
VERDADEIRO do evento, preservar o evento como CONTEXTO quando for relevante
para o emissor monitorado, e impedir que ele pontue para a empresa errada.

Princípio: uma palavra-chave cria apenas um CANDIDATO. Ela não determina
sozinha quem sofreu o evento, quem agiu, se é atual ou histórico, se é
confirmado, nem se deve pontuar.

Resolução por:
    empresa monitorada × evento × sujeito × papel × evidência × fase × tempo
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

# ─────────────────────────── utilitários ───────────────────────────
def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


_POSS = r"(?:d[aeo]s?|of|del|de\s+l[ao]s?)"


def split_clauses(text: str) -> list[tuple[int, int, str]]:
    """Divide em orações preservando offsets. Conectivos e travessões contam
    como fronteira: a janela textual NUNCA pode atravessar oração."""
    out, start = [], 0
    padrao = (r"[.;!?]"
              r"|\s+—\s+|\s+-\s+"
              r"|\s+(?:e|and|y)\s+(?:atualiza\w*\s+sobre|informa\w*\s+sobre)\s+"
              r"|\s*,\s*(?:enquanto|ao passo que|while)\s+"
              r"|\s+(?:em meio a|em meio à|amid|apos|após|following|desde)\s+")
    for m in re.finditer(padrao, text, re.I):
        end = m.start()
        if end > start:
            out.append((start, end, text[start:end]))
        start = m.end()
    if start < len(text):
        out.append((start, len(text), text[start:]))
    return out or [(0, len(text), text)]


# ─────────────── 12. referência histórica / temporal ───────────────
HISTORICAL_MARKERS = [
    r"this day in history", r"on this day", r"years ago", r"h[áa]\s+\d+\s+anos",
    r"relembre", r"retrospectiva", r"anivers[áa]rio de", r"from the archives",
    r"arquivo hist[óo]rico", r"naquele ano", r"em \d{4},", r"h[áa] uma d[ée]cada",
    r"hace \d+ a[ñn]os", r"efem[ée]ride",
]


def detect_historical_reference(text: str, article_year: int | None = None) -> dict:
    """Detecta se o evento econômico é histórico, não atual.

    A data de PUBLICAÇÃO não substitui a data ECONÔMICA do evento."""
    t = _n(text)
    marcador = next((m for m in HISTORICAL_MARKERS if re.search(m, t)), "")
    # data explícita no texto (ex.: "June 1, 2009", "2009")
    anos = [int(a) for a in re.findall(r"\b(19\d{2}|20[0-2]\d)\b", text)]
    ano_evento = None
    if anos:
        ano_evento = min(anos)
    hist = bool(marcador)
    if not hist and ano_evento and article_year and (article_year - ano_evento) >= 2:
        # ano antigo citado explicitamente junto de verbo no passado
        if re.search(r"\b(files?|filed|pediu|entrou|decretou|ocorreu|announced)\b", t):
            hist = True
            marcador = f"ano_antigo_citado:{ano_evento}"
    return {
        "historical_reference": hist,
        "event_year": ano_evento if hist else None,
        "temporal_evidence": marcador,
        "new_occurrence": not hist,
        "current_event": not hist,
    }


# ─────────────── 4/11/13. objeto e escopo da transação ───────────────
OBJ_NAO_EMPRESA = {
    "acoes_proprias": [r"recompra\s+de\s+a[çc][õo]es", r"buyback", r"share\s+repurchase",
                       r"treasury\s+shares?", r"a[çc][õo]es\s+pr[óo]prias",
                       r"cancelamento\s+de\s+a[çc][õo]es", r"a[çc][õo]es\s+em\s+tesouraria",
                       r"recompra\s+de\s+d[íi]vida", r"deb[êe]ntures\s+pr[óo]prias",
                       r"cotas\s+do\s+pr[óo]prio\s+fundo"],
    "aeronaves": [r"aeronaves?", r"aircraft", r"avi[õo]es", r"jatos?", r"e190", r"e195",
                  r"motores?\s+ge", r"engines?"],
    "equipamento": [r"equipamentos?", r"maquin[áa]rio", r"machinery", r"frota de caminh"],
    "imovel": [r"im[óo]ve(?:l|is)", r"terreno", r"real\s+estate", r"galp[ãa]o"],
    "capex_ativo": [r"capex", r"renova[çc][ãa]o\s+de\s+frota", r"usina", r"planta industrial"],
}
OBJ_EMPRESA = [
    r"aquisi[çc][ãa]o\s+d[ao]s?\s+(?:empresa|companhia|banco|grupo|controlad|participa[çc])",
    r"compra\s+d[ao]s?\s+(?:empresa|companhia|banco|grupo|controlad|participa[çc])",
    r"controle\s+acion[áa]rio", r"fus[ãa]o", r"merger", r"business\s+combination",
    r"participa[çc][ãa]o\s+(?:acion[áa]ria|societ[áa]ria)", r"stake\s+in",
    r"unidade\s+de\s+neg[óo]cio", r"incorpora[çc][ãa]o\s+d[ao]",
]
NEGACAO_MA = [
    r"n[ãa]o\s+(?:avalia|planeja|pretende|considera)\s+.{0,30}aquisi[çc]",
    r"no\s+plans?\s+(?:for|to)\s+.{0,25}acquisit",
    r"descarta\s+.{0,25}aquisi[çc]", r"sem\s+planos\s+de\s+aquisi[çc]",
    r"not?\s+(?:considering|evaluating)\s+.{0,25}acquisit",
]
# ── 4I.2 Wave A2: negação ESCOPADA AO EVENTO ────────────────────────────────
# `NEGACAO_MA` acima só protege M&A. A auditoria 4I encontrou negação também
# em outras famílias (Citigroup "banco nega planos", Hapvida "rescisão",
# Klabin "não há conversas"). A regra abaixo é geral, mas NUNCA global: a
# negação precisa estar na MESMA proposição que a menção do evento, senão
# "nega conversas de aquisição, mas confirma emissão de dívida" apagaria as
# duas coisas (§12).
NEGACAO_TRIGGERS = [
    # pt
    r"n[ãa]o\s+h[áa]\b", r"n[ãa]o\s+existe\w*", r"n[ãa]o\s+est[áa]\b",
    r"n[ãa]o\s+(?:avalia|planeja|pretende|considera|negocia|prev[êe]|vai|ir[áa]|houve|ocorreu)\b",
    r"\bneg(?:a|ou|am|ar|aram|ando)\b", r"desmente\w*", r"descarta\w*",
    r"sem\s+planos\s+de", r"afasta\s+(?:a\s+)?(?:possibilidade|hip[óo]tese)",
    r"rescis[ãa]o\b", r"rescind\w*", r"n[ãa]o\s+pode\s+ser\s+exigid\w*",
    # en
    r"\bdenies?\b", r"\bdenied\b", r"\bdenying\b", r"no\s+plans?\s+(?:to|for)\b",
    r"\bnot\s+(?:considering|evaluating|in\s+talks|planning|filing|pursuing)\b",
    r"no\s+(?:discussions?|talks?|negotiations?)\b", r"has\s+not\s+filed\b",
    r"\bruled?\s+out\b", r"\bterminat(?:es|ed|ion)\b", r"\bcall(?:s|ed)\s+off\b",
    r"\bscrapp?(?:s|ed)\b", r"\bwalks?\s+away\s+from\b",
    # es
    r"\bniega\b", r"\bniegan\b", r"no\s+hay\b", r"no\s+existen?\b",
    r"\bdescarta\w*", r"sin\s+planes\s+de", r"no\s+est[áa]\s+negociando",
    r"\brescisi[óo]n\b",
]
# adversativas separam proposições: "nega X, mas confirma Y" são 2 proposições
_ADVERSATIVA = (r"[.;!?]|\s+—\s+|\s+-\s+"
                r"|\s*,?\s*\b(?:mas|por[ée]m|contudo|entretanto|todavia|embora"
                r"|but|however|although|though|while|yet"
                r"|pero|sin\s+embargo|aunque)\b\s*")


def _proposicoes(text: str) -> list[str]:
    """Fatia o texto em proposições independentes. Mais fino que
    `split_clauses` (que é compartilhado por outras regras e não trata
    adversativas), de propósito: negação precisa desse corte."""
    return [p for p in re.split(_ADVERSATIVA, text, flags=re.I) if p and p.strip()]


def detect_event_negation(text: str, event_keywords: list[str]) -> dict:
    """O evento está explicitamente NEGADO neste texto?

    Verdadeiro só quando TODA menção do evento aparece em proposição que
    também carrega negação — assim "nega aquisição, mas confirma emissão"
    nega apenas o M&A. Sem menção do evento, devolve False (nada a negar):
    a negação nunca se aplica a um evento que o texto não discute."""
    t = _n(text)
    # Casamento por LIMITE DE PALAVRA, não substring: preserva keywords curtas
    # e legítimas da taxonomia ("OPA", "RJ", "M&A") sem que "OPA" case dentro
    # de "opaco" nem "RJ" dentro de outra sigla.
    kws = [re.escape(_n(k)) for k in (event_keywords or []) if k and len(_n(k)) >= 2]
    if not kws:
        return {"negated": False, "evidence": "", "mentions": 0}
    kw_rx = re.compile(r"(?<!\w)(?:" + "|".join(kws) + r")(?!\w)")
    mencoes = negadas = 0
    evidencia = ""
    for prop in _proposicoes(t):
        if not kw_rx.search(prop):
            continue
        mencoes += 1
        gat = next((p for p in NEGACAO_TRIGGERS if re.search(p, prop, re.I)), "")
        if gat:
            negadas += 1
            evidencia = evidencia or prop.strip()[:120]
    return {"negated": bool(mencoes) and negadas == mencoes,
            "evidence": evidencia, "mentions": mencoes}


# ── 4I.2 Wave A3: RESOLUÇÃO de evento negativo anterior ──────────────────────
# Distinta de negação: aqui o evento negativo ACONTECEU de verdade, e a
# notícia atual informa encerramento/saída/cura/quitação. "Samarco informa
# encerramento da RJ" não é uma nova RJ; "Aeroméxico salió de la quiebra"
# não é nova falência; "resgate antecipado integral" não é nova emissão.
#
# Os gatilhos são deliberadamente ESPECÍFICOS (verbo + objeto do evento), não
# verbos soltos: "conclui"/"encerra" isolados derrubariam positivos legítimos
# do gold como "BTG conclui aquisição do HSBC Uruguai".
RESOLUCAO_TRIGGERS = [
    # pt — insolvência/litígio
    r"encerramento\s+d[ao]\s+(?:recupera|fal|process|litig)",
    r"encerra\s+(?:a\s+|o\s+)?(?:recupera|fal|process)",
    r"sa[ií](?:u|da|r)\s+d[ao]\s+(?:recupera|fal)",
    r"conclus[ãa]o\s+d[ao]\s+(?:recupera|fal|process)",
    r"conclui\s+(?:a\s+|o\s+)?(?:recupera|process)",
    r"supera(?:ndo|do|r)?\s+(?:a\s+)?(?:recupera|fal|crise)",
    r"emerge\s+d[ao]\s+(?:recupera|fal)",
    # pt — dívida
    r"resgate\s+antecipado", r"quita[çc][ãa]o\s+(?:antecipada\s+)?d[ao]",
    r"quita\s+(?:a\s+|o\s+)?d[íi]vida", r"amortiza[çc][ãa]o\s+integral",
    r"liquida[çc][ãa]o\s+antecipada",
    # pt — rating
    r"(?:mant[ée]m|manteve|mantido|reafirma|reafirmou)\s+(?:o\s+|a\s+)?(?:rating|nota|classifica)",
    r"rating\s+(?:mantido|reafirmado)",
    # en
    r"emerge[sd]?\s+from\s+(?:bankruptcy|chapter\s*11|restructuring)",
    r"exit(?:s|ed|ing)?\s+(?:from\s+)?(?:bankruptcy|chapter\s*11)",
    r"emerged\s+from", r"early\s+redemption", r"redeem(?:s|ed)\s+(?:the\s+)?notes",
    r"repa(?:ys|id)\s+(?:the\s+)?(?:debt|notes|bonds)",
    r"affirm(?:s|ed)\s+(?:the\s+)?(?:rating|idr)", r"rating\s+affirmed",
    r"cured?\s+(?:the\s+)?(?:default|breach|covenant)",
    # es
    r"sal(?:e|i[óo]|ir)\s+d[e]?\s*l?[ao]?\s*(?:quiebra|concurso)",
    r"super[óa]\s+(?:la\s+)?(?:quiebra|crisis)",
    r"rescate\s+anticipado", r"mantiene\s+(?:la\s+)?(?:calificaci[óo]n|nota)",
]


# Ação negativa EXPLÍCITA e simultânea. Encontrada na 4I.2 Wave A3 ao
# derrubar um positivo legítimo do gold: "Fitch revisa perspectiva para
# negativa … E MANTÉM rating 'BB'" é um outlook negativo REAL — a afirmação
# de uma dimensão (rating mantido) não neutraliza a deterioração de outra
# (perspectiva revisada). Quando as duas coisas estão na mesma proposição,
# a ação negativa vence e a resolução NÃO se aplica.
ACAO_NEGATIVA_EXPLICITA = [
    r"revis[ãa]o?\s+(?:a\s+)?perspectiva\s+para\s+negativ", r"revisa\s+.{0,20}negativ",
    r"perspectiva\s+negativ", r"outlook\s+(?:to\s+)?negative", r"negative\s+outlook",
    r"rebaix\w*", r"corta\s+(?:o\s+)?(?:rating|nota)", r"downgrad\w*",
    r"lower(?:s|ed)\s+(?:the\s+)?(?:rating|idr)", r"cut(?:s)?\s+(?:the\s+)?rating",
    r"rebaja\w*", r"pede\s+(?:a\s+)?(?:fal[êe]nci|recupera)",
    r"entra\s+(?:em|com)\s+(?:recupera|fal)", r"files?\s+for\s+bankruptcy",
    r"creditwatch\s+negativ", r"em\s+observa[çc][ãa]o\s+negativ",
]


def detect_event_resolution(text: str, event_keywords: list[str]) -> dict:
    """O texto informa a RESOLUÇÃO de um evento negativo anterior?

    Mesma disciplina de escopo da negação (Wave A2): a marca de resolução
    precisa estar na MESMA proposição que a menção do evento — senão uma
    notícia que fala da saída da RJ de um terceiro apagaria o evento direto
    da monitorada."""
    t = _n(text)
    kws = [re.escape(_n(k)) for k in (event_keywords or []) if k and len(_n(k)) >= 2]
    if not kws:
        return {"resolved": False, "evidence": ""}
    kw_rx = re.compile(r"(?<!\w)(?:" + "|".join(kws) + r")(?!\w)")
    mencoes = resolvidas = 0
    evidencia = ""
    for prop in _proposicoes(t):
        tem_kw = bool(kw_rx.search(prop))
        # ação negativa explícita na MESMA proposição vence a marca de
        # resolução (ver ACAO_NEGATIVA_EXPLICITA)
        if any(re.search(p, prop, re.I) for p in ACAO_NEGATIVA_EXPLICITA):
            if tem_kw:
                mencoes += 1
            continue
        gat = next((p for p in RESOLUCAO_TRIGGERS if re.search(p, prop, re.I)), "")
        # o gatilho de dívida/rating pode aparecer sem a keyword do evento na
        # mesma proposição (ex.: "resgate antecipado integral da 21ª emissão")
        if not tem_kw and not gat:
            continue
        if tem_kw:
            mencoes += 1
            if gat:
                resolvidas += 1
                evidencia = evidencia or prop.strip()[:120]
        elif gat:
            evidencia = evidencia or prop.strip()[:120]
    return {"resolved": bool(mencoes) and resolvidas == mencoes,
            "evidence": evidencia}


POS_TRANSACAO = [
    r"ap[óo]s\s+(?:a\s+)?aquisi[çc][ãa]o", r"depois\s+d[ao]\s+compra",
    r"desde\s+a\s+aquisi[çc][ãa]o", r"aquisi[çc][ãa]o\s+conclu[íi]da",
    r"sinergias\s+d[ao]\s+aquisi[çc]", r"integra[çc][ãa]o\s+p[óo]s",
    r"post[- ]acquisition", r"following\s+the\s+acquisition", r"after\s+the\s+acquisition",
]
RUMOR = [
    r"mira\s+aquisi[çc]", r"estuda\s+(?:a\s+)?compra", r"negocia[çc][õo]es\s+(?:para|com)",
    r"suspeita\s+oferta", r"poss[íi]vel\s+comprador", r"em\s+conversas",
    r"rumor", r"pode\s+adquirir", r"considera\s+comprar", r"oferta\s+n[ãa]o\s+confirmada",
    r"interesse\s+em\s+adquirir", r"explor(?:a|ando)\s+aquisi[çc]",
]
INTRAGRUPO = [
    r"subsidi[áa]ria\s+integral", r"controlada\s+integral", r"100%\s+detida",
    r"wholly[- ]owned", r"incorpora[çc][ãa]o\s+d[ae]\s+(?:sua\s+)?(?:subsidi[áa]ria|controlada)",
    r"reorganiza[çc][ãa]o\s+societ[áa]ria", r"simplifica[çc][ãa]o\s+societ[áa]ria",
    r"sob\s+controle\s+comum",
]
# item 3 da correção 2026-07-31: quando um M&A é rejeitado por ser apenas
# "contexto pós-aquisição" MAS o texto também descreve um resultado econômico
# acima do esperado, o EVENTO PRINCIPAL é o resultado, não a integração —
# a aquisição vira só `secondary_context`. Taxonomia real (config_risco.yaml)
# NÃO tem hoje `resultado_acima_expectativas`/`outlook_positivo` para lucro
# (outlook_positivo é sobre rating de crédito, não resultado operacional) —
# usamos este id como rótulo informativo (scoreable=False, não participa de
# `event_ids_for`/taxonomy lookup) até uma decisão formal de cadastrá-lo.
EARNINGS_BEAT_MARKERS = [
    r"supera\s+(?:as\s+)?estimativas", r"supera\s+(?:as\s+)?expectativas",
    r"acima\s+d[ao]s?\s+expectativas", r"acima\s+d[ao]s?\s+estimativas",
    r"resultado\s+acima\s+d[ao]\s+esperado", r"lucro\s+acima\s+d[ao]s?\s+expectativas",
    r"beats?\s+estimates", r"better[- ]than[- ]expected", r"surpasses?\s+expectations",
    r"supera\s+las\s+expectativas", r"supera\s+las\s+estimaciones",
]


def detect_earnings_beat(text: str) -> bool:
    t = _n(text)
    return any(re.search(p, t) for p in EARNINGS_BEAT_MARKERS)


def detect_transaction(text: str) -> dict:
    """Resolve objeto, escopo e fase da transação (itens 4, 7, 8, 11, 13)."""
    t = _n(text)
    obj, escopo = "", ""
    for k, pats in OBJ_NAO_EMPRESA.items():
        if any(re.search(p, t) for p in pats):
            obj = k
            escopo = ("capital_proprio" if k == "acoes_proprias" else "capex")
            break
    if not obj and any(re.search(p, t) for p in OBJ_EMPRESA):
        obj, escopo = "empresa", "externo"
    neg = any(re.search(p, t) for p in NEGACAO_MA)
    pos = any(re.search(p, t) for p in POS_TRANSACAO)
    rumor = any(re.search(p, t) for p in RUMOR)
    intra = any(re.search(p, t) for p in INTRAGRUPO)
    if intra:
        escopo = "intragrupo"
    elif pos:
        # Referência pós-transação (ex.: "resultado após aquisição
        # concluída") não é uma operação externa em andamento — mesmo quando
        # nenhuma entidade nomeada aparece no texto (o objeto/escopo bruto
        # ficaria "indefinido"), o escopo real é histórico/pós-aquisição.
        escopo = "historico_pos_aquisicao"
    fase = ("integracao" if pos else
            ("rumor" if rumor else
             ("intragrupo" if intra else ("anuncio" if obj == "empresa" else ""))))
    return {
        "transaction_object": obj or "indefinido",
        "transaction_scope": escopo or "indefinido",
        "transaction_phase": fase,
        "negation_detected": neg,
        "post_transaction_context": pos,
        "rumor_detected": rumor,
        "intragroup_detected": intra,
        "change_of_control": bool(obj == "empresa" and not intra and not pos and not rumor),
        "current_transaction": bool(obj == "empresa" and not pos and not neg),
    }


_MA_VERBO_RX = re.compile(
    r"(aquisi[çc][ãa]o|compra|adquir\w+|fus[ãa]o|incorpora[çc][ãa]o|merger|"
    r"acquisition|acquires?|takeover|oferta p[úu]blica de aquisi)", re.I)
_MA_PARTICIPACAO_RX = re.compile(
    r"(\d{1,3}(?:[.,]\d+)?\s*%|participa[çc][ãa]o|stake|controle|sociedade|"
    r"joint\s*venture|capital\s+social)", re.I)
# objetos que, mesmo com verbo de aquisição, NÃO são M&A empresarial
_MA_OBJ_FINANCEIRO_RX = re.compile(
    r"(deb[êe]ntures|b[ôo]nus|bonds?|notas?\s+comerciais|cotas?|t[íi]tulos?|"
    r"a[çc][õo]es\s+pr[óo]prias|carteira\s+de\s+cr[ée]dito)", re.I)


def ma_is_legitimate(text: str, papeis: dict | None = None) -> tuple[bool, str]:
    """M&A legítimo? (item 13). Devolve (ok, motivo_da_rejeicao).

    Default correto: 'aquisição/compra de <ENTIDADE NOMEADA>' ou de
    participação/percentual É aquisição empresarial. Só é rejeitado quando o
    objeto é comprovadamente outro (ativo, título, ações próprias), quando há
    negação, reorganização intragrupo, contexto pós-transação ou rumor.

    A versão anterior exigia a palavra 'empresa/companhia' explícita e rejeitava
    M&A real ('aquisição da Viterra', '51% da sociedade de tissue'). Regra ampla
    demais remove evento legítimo — por isso o default foi invertido."""
    d = detect_transaction(text)
    papeis = papeis or {}
    if d["negation_detected"]:
        return False, "negacao_explicita_de_nova_aquisicao"
    if d["transaction_object"] == "acoes_proprias":
        return False, "recompra_de_acoes_proprias_nao_e_ma"
    if d["transaction_object"] in ("aeronaves", "equipamento", "imovel", "capex_ativo"):
        return False, f"objeto_nao_empresarial:{d['transaction_object']}"
    if d["intragroup_detected"]:
        return False, "reorganizacao_intragrupo_sob_controle_comum"
    if d["post_transaction_context"]:
        return False, "contexto_pos_aquisicao_nao_e_nova_ocorrencia"
    if d["rumor_detected"]:
        return False, "rumor_ou_oferta_nao_confirmada"

    m = _MA_VERBO_RX.search(text)
    if not m:
        return False, "sem_verbo_de_aquisicao_no_texto"
    depois = text[m.end():m.end() + 140]
    antes = text[max(0, m.start() - 90):m.start()]   # inglês: "<Entidade> Acquisition"
    if _MA_OBJ_FINANCEIRO_RX.search(depois) and not _MA_PARTICIPACAO_RX.search(depois):
        return False, "objeto_financeiro_nao_empresarial"
    # entidade nomeada logo após o verbo → aquisição de empresa
    if _ENT_RX.search(depois) or re.search(r"\b[A-ZÁÂÃÉÊÍÓÔÕÚÇ][\w&.\-]{2,}", depois):
        return True, ""
    if _MA_PARTICIPACAO_RX.search(depois):
        return True, ""
    # construção anglófona: entidade nomeada imediatamente antes de "Acquisition"
    if re.search(r"\b[A-Z][\w&.\-]{2,}(?:\s+[A-Z][\w&.\-]{2,}){0,3}\s*$", antes.strip()):
        return True, ""
    if d["transaction_object"] == "empresa":
        return True, ""
    if papeis.get("transaction_role") == "compradora" and papeis.get("target_company"):
        return True, ""
    return False, "objeto_da_transacao_nao_identificado"


# ─────────────── 6/15. fases jurídicas ───────────────
# 4I.2 Wave A1 — a lista original cobria bem português e quase nada de
# inglês/espanhol, e por isso NÃO disparava nos falsos positivos críticos da
# auditoria 4I: JPMorgan ("fraud-claim scrutiny"), CVS ("sue … alleging"),
# BAT ("probes … allegations"), Prudential ("suspected of fraud"), Nutresa
# ("presunta explotación"). As fases são as MESMAS de antes — nenhuma
# taxonomia nova (§7) — só ganharam cobertura pt/en/es equivalente, mais a
# fase civil `acusacao_civil`, que a lista antiga não distinguia de fraude
# consumada.
FASES_JURIDICAS = [
    ("encerramento", [r"encerra\w*\s+(?:a\s+)?a[çc][ãa]o", r"encerramento\s+d[ao]",
                      r"p[õo]e\s+fim", r"quita[çc][ãa]o", r"extin[çc][ãa]o\s+d[ao]\s+processo",
                      r"encerra\s+processo", r"settles?\b", r"settlement",
                      r"\bdefeats?\b", r"\bwins?\s+(?:dismissal|case|suit)",
                      r"\bcleared\s+of\b", r"\bdrops?\s+(?:the\s+)?(?:case|suit|claims?)\b",
                      r"concluye\s+(?:el\s+)?proceso", r"pone\s+fin"], "mitigadora"),
    ("acordo", [r"acordo\s+(?:judicial|com\s+o\s+minist[ée]rio|de\s+leni[êe]ncia)",
                r"faz\s+acordo", r"celebra\s+acordo", r"plea\s+agreement",
                r"\bsettles?\s+with\b", r"reaches?\s+(?:a\s+)?settlement",
                r"acuerdo\s+(?:judicial|extrajudicial|de\s+leniencia)"], "mitigadora"),
    ("pagamento", [r"paga\s+r\$", r"pagamento\s+de\s+r\$", r"pays?\s+\$",
                   r"desembolsa", r"paga\s+(?:una\s+)?multa"], "mitigadora"),
    ("absolvicao", [r"absolvi\w*", r"acquitted", r"inocentad", r"absuelt\w*"], "mitigadora"),
    ("arquivamento", [r"arquiva\w*", r"dismissed", r"shelved",
                      r"\bthrows?\s+out\b", r"archiv[oa]\s+d?el?\s+caso"], "mitigadora"),
    ("prescricao", [r"prescri[çc][ãa]o", r"prescrito", r"prescripci[óo]n"], "mitigadora"),
    ("anulacao", [r"anula\w*", r"overturn\w*", r"revers[ãa]o\s+d[ae]\s+condena"], "mitigadora"),
    ("condenacao", [r"condena\w*", r"convicted", r"conviction", r"sentenc[ei]ad",
                    r"pleaded\s+guilty", r"guilty\s+plea", r"found\s+liable",
                    r"admits?\s+(?:to\s+)?(?:fraud|wrongdoing)",
                    r"declarad[oa]\s+culpable"], "negativa"),
    ("acusacao_formal", [r"den[úu]ncia\s+(?:formal|do\s+mp|oferecida)",
                         r"indict\w*", r"formalmente\s+acusad", r"a[çc][ãa]o\s+penal",
                         r"\bcharged\s+with\b", r"\bfiles?\s+charges\b",
                         r"criminal\s+charges", r"imputad[oa]\s+formalmente"], "negativa"),
    ("investigacao", [r"investiga[çc][ãa]o", r"apura[çc][ãa]o", r"opera[çc][ãa]o\s+d[ao]\s+pf",
                      r"investigation", r"investigat\w*", r"probe[sd]?\b", r"inqu[ée]rito",
                      r"\bscrutiny\b", r"\bunder\s+review\b", r"whistleblow\w*",
                      r"investigaci[óo]n"], "negativa"),
    # NOVA (4I.2 Wave A1): litígio CIVIL — lawsuit/claim/processo. Antes caía
    # em "alegacao" (ou em nada) e o evento pontuava como fraude consumada:
    # é exatamente o par CVS Health / JPMorgan Chase da auditoria.
    ("acusacao_civil", [r"\blawsuits?\b", r"\bsues?\b", r"\bsuing\b", r"\bsued\b",
                        r"\bfraud[- ]claims?\b", r"\bclaims?\s+(?:of|that)\b",
                        r"\bcivil\s+(?:suit|action|complaint)\b",
                        r"\bcomplaint\s+(?:against|filed)",
                        r"processa\w*\s+(?:a|o|na|no)\b",
                        r"a[çc][ãa]o\s+(?:c[íi]vel|judicial)\b", r"move[m]?\s+a[çc][ãa]o",
                        r"demanda\s+judicial", r"querella"], "negativa"),
    ("alegacao", [r"den[úu]ncia\s+exclusiva", r"acusa\w*", r"suspeita",
                  r"alleg\w*", r"alega\w*", r"teria\s+", r"segundo\s+denuncia",
                  r"\bsuspected\b", r"\bpurported\w*", r"presunt\w*",
                  r"sob\s+suspeita", r"denunciad[oa]\b", r"acusaci[óo]n"], "negativa"),
]

# Fases em que o fato NÃO está juridicamente consumado. Palavra forte em
# manchete não pode pular fase (invariante 7 do CLAUDE.md): a taxonomia NÃO
# ganha evento novo — o evento apenas deixa de ser pontuável e vai para o
# bucket informativo já existente (4I.2 §7).
FASES_NAO_CONSUMADAS = frozenset({"alegacao", "acusacao_civil", "investigacao"})


def detect_juridical_phase(text: str) -> dict:
    """Fase jurídica e direção (itens 6 e 15). Mitigadores vencem quando o
    texto descreve desfecho, não nova acusação."""
    t = _n(text)
    achados = []
    for fase, pats, direcao in FASES_JURIDICAS:
        if any(re.search(p, t) for p in pats):
            achados.append((fase, direcao))
    if not achados:
        return {"event_phase": "", "direction": "", "confirmation_level": "indefinido",
                "phase_evidence": ""}
    mitig = [a for a in achados if a[1] == "mitigadora"]
    neg = [a for a in achados if a[1] == "negativa"]
    # desfecho (encerramento/acordo/pagamento/absolvição) domina a acusação citada
    # apenas como ASSUNTO da ação
    if mitig and not any(f in ("condenacao", "acusacao_formal") for f, _ in neg):
        fase, direcao = mitig[0]
        conf = "desfecho_confirmado"
    elif neg:
        fase, direcao = neg[0]
        conf = ("confirmado" if fase == "condenacao" else
                ("formal" if fase == "acusacao_formal" else "nao_confirmado"))
    else:
        fase, direcao = achados[0]
        conf = "indefinido"
    return {"event_phase": fase, "direction": direcao,
            "confirmation_level": conf,
            "phase_evidence": ";".join(f for f, _ in achados)}


# ─────────────── 5/9/10/14. ator × afetado (causação e papéis) ───────────────
CAUSA_TERCEIRO = [
    r"leva\s+(?:as\s+|os\s+)?(.{2,40}?)\s+[àa]\s+(?:beira\s+d[ao]\s+)?fal[êe]nci",
    r"leva\s+(?:as\s+|os\s+)?(.{2,40}?)\s+[àa]\s+recupera[çc][ãa]o",
    r"causa\s+(?:a\s+)?fal[êe]nci\w*\s+d[aeo]s?\s+(.{2,40})",
    r"deixa\s+(.{2,40}?)\s+[àa]\s+beira",
    r"drives?\s+(.{2,40}?)\s+to\s+bankrupt",
]
PAPEL_COMPRADOR = [r"(.{2,40}?)\s+compra\b", r"(.{2,40}?)\s+adquire\b",
                   r"(.{2,40}?)\s+mira\s+aquisi", r"(.{2,40}?)\s+acquires?\b",
                   r"aquisi[çc][ãa]o\s+pel[ao]\s+(.{2,40})"]
PAPEL_ALVO = [r"compra\s+(?:a\s+|o\s+)?(.{2,40}?)(?:\s+em\s+|\s+por\s+|$)",
              r"adquire\s+(?:a\s+|o\s+)?(.{2,40}?)(?:\s+em\s+|\s+por\s+|$)",
              r"aquisi[çc][ãa]o\s+d[ao]\s+(.{2,40}?)(?:\s+em\s+|\s+por\s+|$)",
              r"acquisition\s+of\s+(.{2,40}?)(?:\s+for|\s+in|$)"]
COMUNICADO_TERCEIRO = [
    r"informa\s+sobre", r"comunica\s+(?:sobre|que)", r"esclarece\s+sobre",
    r"presta\s+esclarecimentos", r"informa\s+(?:o\s+)?mercado\s+sobre",
    r"announces?\s+(?:that|regarding)", r"provides?\s+update\s+on",
]


def detect_roles(text: str, monitored: str, aliases_por_empresa: dict) -> dict:
    """Resolve actor/affected/buyer/target e detecta comunicado sobre terceiro."""
    t = text
    tn = _n(text)
    res = {"actor_company": "", "affected_company": "", "buyer_company": "",
           "seller_company": "", "target_company": "", "transaction_role": "",
           "third_party_statement": False, "role_evidence": ""}

    def _quem(trecho: str) -> str:
        tr = _n(trecho)
        for emp, als in aliases_por_empresa.items():
            for a in als:
                if re.search(rf"\b{re.escape(_n(a))}\b", tr):
                    return emp
        return trecho.strip()[:60]

    for p in CAUSA_TERCEIRO:
        m = re.search(p, tn)
        if m:
            res["actor_company"] = monitored
            res["affected_company"] = _quem(m.group(1))
            res["role_evidence"] = m.group(0)[:90]
            break
    for p in PAPEL_COMPRADOR:
        m = re.search(p, tn)
        if m:
            comprador = _quem(m.group(1))
            res["buyer_company"] = comprador
            if comprador == monitored:
                res["transaction_role"] = "compradora"
            break
    for p in PAPEL_ALVO:
        m = re.search(p, tn)
        if m:
            alvo = _quem(m.group(1))
            if alvo != res.get("buyer_company"):
                res["target_company"] = alvo
                if alvo == monitored:
                    res["transaction_role"] = "alvo"
            break
    # "comunicado sobre terceiro" só vale se a MONITORADA for quem comunica.
    # Em "Vale informa sobre RJ da Samarco", quem informa é a Vale — a regra
    # não pode disparar quando a monitorada é a Samarco.
    for p in COMUNICADO_TERCEIRO:
        m = re.search(p, tn)
        if not m:
            continue
        antes = tn[:m.start()]
        als_mon = aliases_por_empresa.get(monitored, [monitored])
        if any(re.search(rf"\b{re.escape(_n(a))}\b", antes) for a in als_mon):
            res["third_party_statement"] = True
            res["role_evidence"] = res["role_evidence"] or m.group(0)[:60]
        break
    return res


# ── entidade nomeada por possessivo, ESCOPADA POR ORAÇÃO ──
# Necessária porque o sujeito verdadeiro frequentemente NÃO está na watchlist
# (Samarco, Banco Digimais, St. Marche, transportadoras).
_STOP_ENT = {"a", "o", "as", "os", "da", "do", "das", "dos", "de", "e", "em", "que",
             "the", "of", "and", "para", "com", "por", "sua", "seu", "plano",
             "recuperacao", "judicial", "falencia", "processo", "acao", "grupo",
             # cargos nunca sao entidade: "renuncia DO CEO" capturava "CEO"
             # como sujeito (4I.2 B7b-1)
             "ceo", "presidente", "diretor", "diretor-presidente", "gerente",
             "gerente general", "conselho", "sucessao", "comando",
             # moedas e magnitudes — evitam capturar "R$ 1,1 bilhão" como entidade
             "r", "rs", "r$", "us", "us$", "usd", "brl", "eur", "milhoes", "milhao",
             "bilhoes", "bilhao", "mil", "reais", "dolares",
             # países/jurisdições não são sujeito de evento de crédito
             "eeuu", "eua", "brasil", "argentina", "chile", "mexico", "colombia",
             "estados unidos", "justica", "cvm", "sec", "bacen", "cade",
             # estados/províncias/cidades não são sujeito de evento de crédito
             "michigan", "california", "texas", "florida", "nova york", "new york",
             "sao paulo", "rio de janeiro", "minas gerais", "bahia", "parana",
             "mato grosso", "santa catarina", "goias", "ceara", "pernambuco",
             "delaware", "ohio", "illinois", "mt", "sc", "sp", "rj", "mg"}
# aceita prefixo organizacional minúsculo: "do banco Digimais", "da empresa X"
_ENT_RX = re.compile(
    r"\b(?:d[aeo]s?|of)\s+(?:(?:banco|empresa|companhia|grupo|firma|sociedade|"
    r"holding|construtora|varejista|petroleira|operadora|bank|company)\s+)?"
    r"((?:[A-ZÁÂÃÉÊÍÓÔÕÚÇ][\w&.\-]*)(?:\s+(?:de|da|do|dos|das|e|&)?\s*"
    r"[A-ZÁÂÃÉÊÍÓÔÕÚÇ][\w&.\-]*){0,3})")
# entidade precisa parecer organização: ≥3 chars e não ser só valor monetário
_ENT_VALIDA_RX = re.compile(r"[A-Za-zÁÂÃÉÊÍÓÔÕÚÇáâãéêíóôõúç]{3,}")

EVENT_TERM_RX = {
    "recuperacao_judicial": r"recupera[çc][ãa]o\s+judicial|chapter\s*11|\brj\b",
    "falencia": r"fal[êe]nci\w*|bankrupt\w*|liquida[çc][ãa]o",
    "default": r"\bdefault\b|inadimpl\w*|calote",
    "inadimplencia": r"inadimpl\w*|\bdefault\b",
    "fraude": r"fraude|fraudulent\w*|fraud\b",
    # 4I.2 Wave B7b-1: "novo CEO DA Vale", "renúncia do CEO DA Organon" é a
    # MESMA relação possessiva que "recuperação judicial DA Samarco" — o
    # detector `subject_by_possessive` já era genérico (dirigido por este
    # mapa); só faltava o termo do evento. Nenhum detector paralelo criado.
    # Só construções que LIGAM a troca a um possessivo imediato. O termo solto
    # `\bceo\b` foi removido: fazia o scan possessivo achar qualquer "de X"
    # adiante e derrubou 3 positivos legítimos (Santander, Smart Fit, Tupy),
    # em que a própria monitorada é o sujeito.
    "troca_ceo": (r"(?:novo|nova|new)\s+(?:ceo|presidente|president|"
                  r"diretor[- ]presidente|gerente\s+general|chief\s+executive)"
                  r"|(?:ren[úu]ncia|sa[íi]da)\s+d[oe]\s+(?:ceo|presidente)"),
}


def subject_by_possessive(text: str, event_id: str, monitored: str,
                          aliases_por_empresa: dict) -> tuple[str, str]:
    """Acha 'd[ao] <ENTIDADE>' na MESMA oração do termo do evento.

    Ex.: 'Plano de Recuperação Judicial da Samarco'      → Samarco
         'A falência fraudulenta do Banco Digimais ...'  → Banco Digimais

    Regras de segurança (evitam remover evento legítimo):
      - se a MONITORADA aparece como dona do evento em qualquer oração com o
        termo, NÃO reatribui (ela é o sujeito verdadeiro);
      - descarta valores monetários, magnitudes, países e órgãos públicos;
      - a janela nunca atravessa fronteira de oração."""
    termo = EVENT_TERM_RX.get(event_id)
    if not termo:
        return "", ""
    alias_mon = {_n(a) for a in aliases_por_empresa.get(monitored, [monitored])}
    candidatos = []
    for (s0, s1, frase) in split_clauses(text):
        m = re.search(termo, frase, re.I)
        if not m:
            continue
        # janela POSTERIOR e ANTERIOR, ambas dentro da MESMA oração
        # ("recuperação da Oi em falência" tem o dono ANTES do termo)
        janelas = [frase[m.end():], frase[:m.start()]]
        for _jan in janelas:
          for cand in _ENT_RX.finditer(_jan):
            ent = re.sub(r"\s+", " ", cand.group(1)).strip(" .,;:")
            en = _n(ent)
            _conhecida = any(_n(a) == en for als in aliases_por_empresa.values()
                             for a in als)
            if not ent or en in _STOP_ENT:
                continue
            # entidade curta ("Oi") só vale se for alias conhecido da watchlist
            if not _ENT_VALIDA_RX.search(ent) and not _conhecida:
                continue
            if any(tok in _STOP_ENT for tok in en.split()[:1]):
                continue
            candidatos.append((ent, cand.group(0)[:80]))
    if not candidatos:
        return "", ""
    # A comparação é ANCORADA no início da entidade: a captura multi-token pode
    # arrastar palavras da frase seguinte ("Samarco Vale informa..."), e casar
    # por substring devolveria o sujeito errado.
    def _cabeca(e: str) -> str:
        return _n(e).split()[0] if _n(e).split() else ""

    # a monitorada é dona do evento em alguma oração → mantém como sujeito
    for ent, _ev in candidatos:
        en, cab = _n(ent), _cabeca(ent)
        if (en in alias_mon or cab in {a.split()[0] for a in alias_mon if a}
                or en.startswith(_n(monitored)) or _n(monitored).startswith(en)):
            return "", ""
    ent, evid = candidatos[0]
    cab = _cabeca(ent)
    for emp, als in aliases_por_empresa.items():
        for a in als:
            na = _n(a)
            if na == _n(ent) or _n(ent).startswith(na) or na.split()[0] == cab:
                return emp, evid
    return ent, evid


# ─────────────── 3. famílias de ação de rating ───────────────
RATING_FAMILY = {
    "rebaixamento_rating": "acao_rating_negativa",
    "outlook_negativo": "acao_rating_negativa",
    "creditwatch_negativo": "acao_rating_negativa",
    "revisao_para_rebaixamento": "acao_rating_negativa",
    "perspectiva_negativa": "acao_rating_negativa",
    "rating_elevado": "acao_rating_positiva",
    "outlook_positivo": "acao_rating_positiva",
    "perspectiva_positiva": "acao_rating_positiva",
}
RATING_PRINCIPAL = {
    "acao_rating_negativa": ["rebaixamento_rating", "creditwatch_negativo",
                             "revisao_para_rebaixamento", "outlook_negativo",
                             "perspectiva_negativa"],
    "acao_rating_positiva": ["rating_elevado", "outlook_positivo", "perspectiva_positiva"],
}


def collapse_rating_actions(event_ids: list[str]) -> dict:
    """Uma ação de rating não pode pontuar duas vezes na mesma família (item 3).

    downgrade efetivo + outlook negativo → só downgrade.
    rating mantido + outlook alterado    → só outlook."""
    manter, removidos = list(event_ids), []
    for familia, ordem in RATING_PRINCIPAL.items():
        presentes = [e for e in event_ids if RATING_FAMILY.get(e) == familia]
        if len(presentes) <= 1:
            continue
        principal = next((e for e in ordem if e in presentes), presentes[0])
        for e in presentes:
            if e != principal:
                manter.remove(e)
                removidos.append({"event_id": e, "absorvido_por": principal,
                                  "familia": familia,
                                  "motivo": "mesma ação da agência — atributo secundário"})
    return {"event_ids": manter, "removidos": removidos}


# ─────────────── orquestração: resolve um artigo ───────────────
EVENTOS_SUJEITO_ESTRITO = {
    "recuperacao_judicial", "falencia", "default", "inadimplencia",
    "liquidacao", "chapter11", "intervencao",
}
EVENTOS_MA = {"ma", "fusao_aquisicao", "m&a"}
EVENTOS_FRAUDE = {"fraude", "fraude_investigacao", "corrupcao", "lavagem"}

# ── 4I.2 Wave A1b: fase jurídica FORA da família fraude ─────────────────────
# A regra da A1 não pode ser reaproveitada cegamente: o mínimo de fase que
# torna um evento pontuável depende do que o event_id SIGNIFICA.
#
# `investigacao_regulatoria` (score 30) tem como keywords atos FORMAIS ("CVM
# abre processo", "busca e apreensão", "expediente sancionador", "abre
# investigación"): o próprio evento É a investigação. Exigir condenação aqui
# mudaria o significado da família (§9/§20). Só alegação/rumor sem ato formal
# não basta.
#
# `default_cri` (score 80, "Default de CRI na carteira") representa um fato
# ECONÔMICO consumado — CRI efetivamente inadimplente. Denúncia, processo ou
# investigação sobre suposta inadimplência não provam o default (§10/§21).
EVENTOS_INVESTIGACAO_E_O_PROPRIO_EVENTO = {"investigacao_regulatoria"}

# ── 4I.2 Wave A4: "Issuer Default Rating" é NOME DE MÉTRICA, não evento ─────
# "Fitch Ratings Upgrades Term Issuer Default Rating on BAT" virava `default`
# crítico (peso 80) — sendo que é um UPGRADE. A palavra "default" aqui compõe
# a nomenclatura da agência (IDR), não descreve inadimplemento.
NOMENCLATURA_RATING = [
    r"issuer\s+default\s+rating", r"\bidr\b", r"long[- ]term\s+issuer\s+default",
    r"short[- ]term\s+issuer\s+default", r"foreign\s+currency\s+issuer\s+default",
    r"local\s+currency\s+issuer\s+default", r"default\s+rating",
    r"calificaci[óo]n\s+de\s+incumplimiento\s+del\s+emisor",
    r"rating\s+de\s+inadimpl[êe]ncia\s+do\s+emissor",
    # cláusula contratual citada como texto jurídico, sem acionamento
    r"default\s+provisions?", r"cl[áa]usulas?\s+de\s+default",
]
# Default ECONÔMICO de verdade — o que a nomenclatura acima não prova.
DEFAULT_ECONOMICO_REAL = [
    r"payment\s+default", r"missed\s+payment", r"failure\s+to\s+pay",
    r"event\s+of\s+default\s+(?:was\s+)?(?:triggered|declared|occurred)",
    r"defaults?\s+on\s+(?:its\s+|the\s+)?(?:debt|notes|bonds|loan|payment)",
    r"deixou?\s+de\s+pagar", r"n[ãa]o\s+pagou", r"calote",
    r"inadimpl[êe]ncia\s+d[ao]\s+d[íi]vida", r"atraso\s+no\s+pagamento",
    r"impago", r"incumplimiento\s+de\s+pago", r"cesaci[óo]n\s+de\s+pagos",
    r"acionou?\s+(?:o\s+)?evento\s+de\s+inadimpl",
]


# ── 4I.2 Wave A5: referência a evento PASSADO detectável pelo texto ─────────
# Construção "após/tras/after + <evento>": o evento é citado como pano de
# fundo, não anunciado. Exige ADJACÊNCIA (marcador colado ao termo do evento)
# para não capturar "Justiça aceita RJ após pedido da empresa", que é atual.
POSTERIORIDADE = (r"(?:tras|ap[óo]s|depois\s+d[aeo]s?|after|following|since|desde)\s+"
                  r"(?:the\s+|a\s+|o\s+|as\s+|os\s+|la\s+|el\s+|its\s+|su\s+|sua\s+|seu\s+)?")


def detect_evento_passado(text: str, event_keywords: list[str]) -> dict:
    """Evento citado como ANTERIOR ("tras la quiebra", "after the acquisition")."""
    t = _n(text)
    kws = [re.escape(_n(k)) for k in (event_keywords or []) if k and len(_n(k)) >= 4]
    if not kws:
        return {"passado": False, "evidence": ""}
    rx = re.compile(POSTERIORIDADE + r"(?:" + "|".join(kws) + r")(?!\w)", re.I)
    mencoes = passadas = 0
    ev = ""
    kw_rx = re.compile(r"(?<!\w)(?:" + "|".join(kws) + r")(?!\w)")
    for prop in _proposicoes(t):
        if not kw_rx.search(prop):
            continue
        mencoes += 1
        m = rx.search(prop)
        if m:
            passadas += 1
            ev = ev or m.group(0)[:80]
    return {"passado": bool(mencoes) and passadas == mencoes, "evidence": ev}


# ── 4I.2 Wave A6: credor ≠ devedor ──────────────────────────────────────────
# O evento de crédito pertence a QUEM DEVE. A regra é sobre papel econômico,
# nunca sobre tipo de empresa: um banco continua podendo sofrer evento próprio
# (§31). Dois sinais complementares, ambos textuais e explícitos.
_INSOLVENCIA_NOUN = (r"(?:impago|default|calote|inadimpl[êe]nci\w*|falenci\w*|fal[êe]nci\w*|"
                     r"quiebra|recupera[çc][ãa]o(?:\s+judicial)?|concurso\s+de\s+acreedores|"
                     r"bankruptcy|insolvenc\w*)")
# 4I.2 B3: o preenchimento entre o substantivo de insolvência e o possessivo
# NÃO pode atravessar outro conectivo possessivo, senão a captura escorrega
# para a frase seguinte (foi o que zerou o caso Grupo México: "impago de Pemex
# Detuvo Grupo México" era capturado inteiro).
_DEVEDOR_POSSESSIVO = re.compile(
    _INSOLVENCIA_NOUN + r"(?:\s+(?!de\b|da\b|do\b|dos\b|das\b|of\b|del\b)\w+){0,3}?\s+"
    r"(?:de|da|do|dos|das|of|del|de\s+la)\s+"
    r"((?:[a-z0-9&.\-]+\s*){1,4})", re.I)
# "calote de R$ 3,6 bi DO Banco do Brasil": quando há um VALOR entre o evento e
# o possessivo, a quantia é DEVIDA A quem o possessivo nomeia — esse nome é o
# CREDOR, não o devedor. É o que separa o caso Banco do Brasil do caso Pemex.
_VALOR = r"(?:r\$|us\$|eur|\$)?\s*[\d][\d.,]*\s*(?:bi|bilh\w*|mi|milh\w*|mil|bn)?"
_CREDOR_POR_VALOR = re.compile(
    _INSOLVENCIA_NOUN + r"\s+de\s+" + _VALOR + r"\s+(?:do|da|dos|das|de)\s+"
    r"((?:[a-z0-9&.\-]+\s*){1,4})", re.I)
# monitorada aparece como FINANCIADORA da operação de outro
_CREDOR_CUES = [
    r"junto\s+a[oó]?\s+{m}", r"junto\s+[àa]\s+{m}", r"com\s+o\s+{m}\b",
    r"{m}\s+(?:financia|financiou|empresta|emprestou|concede\s+cr[ée]dito)",
    r"financiad[oa]\s+pel[oa]\s+{m}", r"{m}\s+as\s+(?:lender|creditor)",
]


def detect_debtor_subject(text: str, monitored: str, aliases: list[str] | None = None) -> str:
    """Entidade a quem o evento de crédito/insolvência realmente pertence,
    quando o texto a nomeia por possessivo ("impago de Pemex", "recuperação
    da Oi"). Devolve "" quando o devedor é a própria monitorada ou quando
    não há nome — nunca adivinha."""
    t = _n(text)
    meus = {_n(a) for a in (aliases or [])} | {_n(monitored)}

    def _limpa(bruto):
        cand = re.sub(r"\s+", " ", bruto).strip(" .,;:")
        # nunca deixar a entidade atravessar conectivo ou início de nova frase
        cand = re.split(r"\b(?:em|no|na|e|and|y|que|com|detuvo|esta|está|apos|após|"
                        # verbos/particípios encerram o nome da entidade
                        r"foi|foram|sera|será|teve|pediu|entrou|sofreu|registrou|"
                        r"decretad\w*|citad\w*|aprovad\w*|convertid\w*|was|were)\b",
                        cand)[0].strip()
        return cand

    # (a) monitorada nomeada como CREDORA por construção de valor → não é devedora
    for m in _CREDOR_POR_VALOR.finditer(t):
        cred = _limpa(m.group(1))
        if cred and any(cred in a or a in cred for a in meus if a):
            return "__monitorada_e_credora__"
    # (b) devedor nomeado por possessivo
    for m in _DEVEDOR_POSSESSIVO.finditer(t):
        cand = _limpa(m.group(1))
        if not cand or len(cand) < 2:
            continue
        if any(cand in a or a in cand for a in meus if a):
            continue           # este possessivo é a própria monitorada; segue procurando
        # Valor monetário nunca é entidade. `fullmatch` não bastava: "r$ 1,1
        # bilhao" tem token alfabético e escapava, virando "entidade" — foi a
        # regressão que a suíte histórica (test_semantica [13]) pegou nesta
        # wave. Agora remove moeda/número/magnitude e exige que sobre nome.
        _resto = re.sub(r"\b(?:r\$|rs|us\$|usd|brl|eur|bi|bn|bilh\w*|milh\w*|mi|mil|"
                        r"reais|dolares|d[óo]lares|euros?)\b|[\d.,$]+", " ", cand).strip()
        if len(_resto) < 2:
            continue
        if cand in _STOP_ENT or _resto in _STOP_ENT:
            continue
        # Substantivo comum do próprio domínio financeiro nunca é entidade:
        # "inadimplência DA DÍVIDA" extraía "divida" como se fosse empresa
        # (encontrado pelos testes da B5).
        # Verificação por TOKEN: se todas as palavras restantes forem
        # substantivos/adjetivos comuns do domínio, não é entidade. O
        # `fullmatch` de uma palavra só falhava em "dívida própria" e
        # bloqueava default legítimo do emissor (§8).
        _COMUM = re.compile(
            r"(?:d[íi]vida|deuda|debt|d[ée]bito|obriga[çc][ãa]o|obligaci[óo]n|"
            r"pagamento|pago|parcela|juros|intereses|interest|deb[êe]nture|"
            r"bond|note|t[íi]tulo|cri|cra|empr[ée]stimo|loan|presta[çc][ãa]o|"
            r"contrato|opera[çc][ãa]o|companhia|empresa|sociedade|grupo|banco|"
            r"governo|gobierno|estado|pa[íi]s|carteira|cartera|portfolio|cr[ée]dito|"
            r"pr[óo]pri[oa]|own|total|l[íi]quid[oa]|financeir[oa]|banc[áa]ri[oa])s?",
            re.I)
        _toks = [w for w in re.split(r"\s+", _resto) if w]
        if _toks and all(_COMUM.fullmatch(w) for w in _toks):
            continue
        return cand
    return ""


def is_monitored_credor(text: str, monitored: str, aliases: list[str] | None = None) -> bool:
    """A monitorada aparece explicitamente como financiadora da operação."""
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return False
    alt = "(?:" + "|".join(nomes) + ")"
    return any(re.search(p.format(m=alt), t, re.I) for p in _CREDOR_CUES)


# ── 4I.2 Wave A7: vítima / comentarista / investigador ──────────────────────
# A monitorada aparece, mas não é sujeito do evento: ela alerta, comenta,
# investiga ou sofre. Cues textuais explícitos, sem tocar entity resolution.
_PAPEL_NAO_SUJEITO = {
    "vitima": [r"{m}\s+(?:warns?|alerta|alertou)\s+(?:customers?|clientes?|sobre|about)",
                r"golpe\s+(?:que\s+)?se\s+passa\s+por\s+{m}", r"impersonat\w*\s+{m}",
                r"charged\s+in\s+{m}\s+(?:fraud|scam)",
                r"(?:v[íi]tima|victim)\s+(?:de|of)\s+", r"scam\s+(?:targeting|involving)\s+{m}"],
    # 4I.2 B4: os cues de fraude propriamente ditos vivem em FRAUDE_VITIMA/
    # FRAUDE_AGENTE abaixo, porque papel e fase jurídica são eixos distintos.
    "comentarista": [r"{m}\s+(?:alerta\s+para|comenta|avalia|v[êe]|diz\s+que)",
                      r"(?:diz|afirma|segundo|responde)\s+(?:o\s+)?(?:gestor|diretor|presidente|"
                      r"economista)\s+d[ao]\s+{m}", r"gestor\s+d[ao]\s+{m}\s+responde"],
    "investigador": [r"{m}\s+(?:abre|abri[óu])\s+(?:una?\s+)?investigaci[óo]n",
                      r"{m}\s+(?:abre|instaura)\s+(?:uma?\s+)?(?:investiga[çc][ãa]o|sindic[âa]ncia)",
                      r"{m}\s+opens?\s+(?:an?\s+)?(?:probe|investigation)"],
}


# ── 4I.2 Wave B4: VÍTIMA ≠ AUTORA da fraude ─────────────────────────────────
# Papel e fase jurídica são EIXOS DISTINTOS (§5): "Truist alleges customer
# committed fraud against the bank" é allegation NA FASE e vítima NO PAPEL —
# e mesmo que a fraude fosse comprovada, continuaria não sendo fraude
# COMETIDA pela Truist.
#
# `{m}` é o alias da monitorada. `{q}` absorve qualificadores corporativos
# entre o nome e o verbo ("Truist BANK warns…", "Vale S.A. informou…"), que
# foi exatamente a causa do falso positivo Truist: o alias cadastrado existe
# e a atribuição funciona — o cue é que exigia adjacência estrita.
_QUALIF = r"(?:\s+(?:bank|banco|financial|holdings?|group|grupo|s\.?a\.?|inc\.?|corp\.?|"
_QUALIF += r"co\.?|plc|ltda?\.?|n\.?a\.?)){0,3}"
# conector tolerante a artigo/preposicao: "defraudaron A Cemex", "contra A
# Petrobras", "fraudou O Itau" — sem isso o cue so casava sem artigo.
_ART = r"\s+(?:a|o|as|os|the|el|la|los|las|ao|aos|à|às)?\s*"
FRAUDE_VITIMA = [
    # a monitorada alerta/sofre/é alvo
    r"{m}{q}\s+(?:warns?|warned|alerta|alertou|adverte|advirti[óo])",
    r"{m}{q}\s+(?:sofreu|sofre|foi\s+alvo|foi\s+v[íi]tima|perdeu)",
    r"(?:v[íi]tima|victim|v[íi]ctima)\s+(?:de|of)\s+{m}",
    r"{m}{q}\s+(?:é|e|foi)\s+(?:a\s+)?(?:v[íi]tima|lesad[oa])",
    # terceiro age CONTRA a monitorada
    r"(?:fraud|fraude|estafa|golpe|scam|scheme|esquema)\s+(?:against|contra){a}{m}",
    r"(?:defraud\w*|fraud(?:ou|aram)|scamm\w*|estaf[óo]|enganou|lesou|roubou|"
    r"stole\s+from){a}{m}",
    r"(?:stole|roubad\w*|desviad\w*|subtra[íi]d\w*)\s+.{{0,40}}?(?:from|de|da|do)\s+{m}",
    r"(?:targeting|dirigid[oa]\s+a|voltado\s+contra){a}{m}",
    r"(?:se\s+passa(?:m|ndo)?\s+por|impersonat\w*|posing\s+as)\s+{m}",
    r"charged\s+in\s+{m}{q}\s+(?:fraud|scam)",
    r"(?:cliente|customer|funcion[áa]rio|employee|ex-funcion[áa]rio)\s+.{{0,60}}?"
    r"(?:fraud\w*|golpe|estafa|lesou|desviou)\s*.{{0,20}}?{a}{m}",
]
FRAUDE_AGENTE = [
    r"{m}{q}\s+(?:commit\w*|comete\w*|praticou|perpetr\w*|orquestr\w*)",
    r"{m}{q}\s+(?:admit\w*|confess\w*|assumiu)",
    r"{m}{q}\s+.{{0,30}}?(?:condenad|convicted|found\s+liable|declarad[oa]\s+culpable)",
    r"(?:condena\w*|convicts?)\s+.{{0,40}}?{m}",
    r"{m}{q}\s+(?:is\s+)?(?:accused|acusad[oa]|indicted|charged\s+with)",
    r"(?:fraud|fraude)\s+(?:by|d[ao])\s+{m}",
    r"{m}{q}\s+(?:stole|roubou|desviou|manipul\w*|falsific\w*|forjou)",
    r"{m}{q}\s+.{{0,30}}?(?:esquema|scheme)\s+(?:fraudulent\w*|de\s+fraude)",
]


# ── 4I.2 Wave B5: SOBERANO ≠ EMISSOR CORPORATIVO ────────────────────────────
# YPF recebia `default` crítico de "default ARGENTINO": o devedor está nomeado
# por DEMÔNIMO ADJETIVO, construção que nenhum detector de sujeito/possessivo
# reconhecia — sem regra, o evento caía no fim do laço com subject=YPF.
#
# Nenhuma base geográfica nova (§10): reaproveito (a) o princípio já codificado
# em `_STOP_ENT` ("países/jurisdições não são sujeito de evento de crédito") e
# (b) o campo `country` que o cadastro já mantém. `country` serve só para
# RECONHECER o soberano — nunca como fallback de atribuição (§11) — e o efeito
# é sempre REMOVER atribuição indevida, jamais criá-la.
SOBERANO_TERMS = [
    r"soberan\w*", r"sovereign", r"d[íi]vida\s+p[úu]blica", r"deuda\s+p[úu]blica",
    r"d[oe]\s+governo", r"del\s+gobierno", r"government\s+(?:debt|bonds?|default)",
    r"tesouro\s+nacional", r"tesoro\s+(?:nacional|p[úu]blico)", r"treasury",
    r"\bfmi\b", r"\bimf\b", r"paris\s+club", r"clube\s+de\s+paris",
    r"t[íi]tulos\s+p[úu]blicos",
]
# a monitorada é o devedor de forma EXPLÍCITA — vence qualquer marca soberana
DEVEDOR_CORPORATIVO_EXPLICITO = [
    r"{m}{q}\s+(?:deixa|deixou)\s+de\s+pagar", r"{m}{q}\s+n[ãa]o\s+pag(?:a|ou)",
    r"{m}{q}\s+(?:default(?:s|ed)?|entra\s+em\s+default|declara\s+default)",
    r"{m}{q}\s+(?:incumple|incumpli[óo]|deja\s+de\s+pagar)",
    r"{m}{q}\s+(?:misses?|missed)\s+(?:a\s+|the\s+)?(?:bond\s+)?payment",
    r"{m}{q}\s+(?:calote|inadimpl\w*)", r"default\s+d[ao]\s+{m}",
    r"{m}{q}\s+(?:on\s+its|em\s+sua|de\s+sua)\s+(?:corporate\s+)?(?:debt|d[íi]vida)",
]


# ── 4I.2 Wave B6: risco PROSPECTIVO e objeto CARTEIRA ≠ default do emissor ──
# Banorte recebia `default` crítico (peso 100) de "créditos con mayor RIESGO DE
# impago": não há default nenhum — há concessão de crédito com risco maior. A
# causa é de CLASSIFICAÇÃO (construção lexical que não representa o evento
# econômico), não de entity resolution. Duas dimensões, ambas pequenas e
# gerais, na mesma família da distinção fato/fase das Waves A1/A1b.
MODALIZADOR_PROSPECTIVO = [
    r"risco\s+de", r"riesgo\s+de", r"risk\s+of", r"em\s+risco\s+de",
    r"amea[çc]a\s+de", r"amenaza\s+de", r"threat\s+of", r"perto\s+d[eo]",
    r"pode\s+(?:entrar\s+em|dar|sofrer)", r"podr[íi]a", r"puede\s+(?:entrar|caer)",
    r"could\s+(?:default|face)", r"may\s+default", r"expectativa\s+de",
    r"proje[çc][ãa]o\s+de", r"alerta\s+(?:para|sobre)", r"warns?\s+of",
    r"prev[êe]\s+", r"temor\s+de", r"receio\s+de", r"potencial\s+",
]
# o evento recai sobre CARTEIRA/PRODUTO/INSTRUMENTO, não sobre obrigação própria
OBJETO_CARTEIRA = [
    r"carteira\s+de\s+cr[ée]dito", r"cartera\s+(?:de\s+cr[ée]dito|vencida)",
    r"loan\s+portfolio", r"cr[ée]dito?s\s+(?:concedidos?|outorgados?|con\b)",
    r"morosidad", r"inadimpl[êe]ncia\s+d[ao]s?\s+(?:carteira|clientes?|tomadores?)",
    r"\bnpl\b", r"non[- ]performing", r"default\s+rate", r"taxa\s+de\s+(?:default|inadimpl)",
    r"cr[ée]ditos?\s+(?:problem[áa]ticos?|en\s+incumplimiento|vencidos?)",
    r"borrowers?\s+in\s+default", r"clientes?\s+em\s+default",
    r"empr[ée]stimos?\s+(?:concedidos?|outorgados?)",
    r"(?:clientes?|tomadores?|devedores?|borrowers?|deudores?)\s+d[oae]s?\s+\w+\s+"
    r"(?:entram?|entra|caen?|cai)\s+em\s+default",
    r"provis[õo]es\s+para\s+(?:perdas|devedores)", r"exposi[çc][ãa]o\s+a\b",
]


def detect_evento_nao_consumado(text: str, event_keywords: list[str], monitored: str,
                                aliases: list[str] | None = None) -> dict:
    """O termo do evento aparece apenas como RISCO PROSPECTIVO ou recai sobre
    CARTEIRA/produto — e não como obrigação própria inadimplida?

    Cede sempre que houver devedor corporativo explícito (§8): banco que deixa
    de pagar obrigação própria continua pontuando. Nunca fabrica sujeito (§10).
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    alt = "(?:" + "|".join(nomes) + ")" if nomes else ""
    if alt and any(re.search(p.format(m=alt, q=_QUALIF), t, re.I)
                   for p in DEVEDOR_CORPORATIVO_EXPLICITO):
        return {"nao_consumado": False, "motivo": "", "evidence": ""}
    kws = [re.escape(_n(k)) for k in (event_keywords or []) if k and len(_n(k)) >= 2]
    if not kws:
        return {"nao_consumado": False, "motivo": "", "evidence": ""}
    kw_alt = "|".join(kws)
    # modalizador COLADO ao termo do evento ("riesgo de impago")
    rx_prosp = re.compile(r"(?:" + "|".join(MODALIZADOR_PROSPECTIVO) + r")\s+(?:\w+\s+){0,2}?(?:"
                          + kw_alt + r")(?!\w)", re.I)
    kw_rx = re.compile(r"(?<!\w)(?:" + kw_alt + r")(?!\w)")
    mencoes = neutras = 0
    motivo = ev = ""
    for prop in _proposicoes(t):
        if not kw_rx.search(prop):
            continue
        mencoes += 1
        m = rx_prosp.search(prop)
        if m:
            neutras += 1
            motivo = motivo or "risco_prospectivo"
            ev = ev or m.group(0)[:70]
            continue
        c = next((re.search(p, prop, re.I) for p in OBJETO_CARTEIRA
                  if re.search(p, prop, re.I)), None)
        if c:
            neutras += 1
            motivo = motivo or "objeto_carteira"
            ev = ev or c.group(0)[:70]
    return {"nao_consumado": bool(mencoes) and neutras == mencoes,
            "motivo": motivo, "evidence": ev}


# ── 4I.2 Wave B2: SUBSIDIÁRIA nomeada ao lado da controladora ───────────────
# "CVS Health's Omnicare files for Chapter 11" e "Bankruptcy judge approves
# sale of CVS Health SUBSIDIARY Omnicare": o sujeito é a Omnicare, mas as duas
# construções (possessivo saxônico e aposto com palavra de relação) não são
# cobertas por `subject_by_possessive`, que espera "falência DA X" — então o
# evento caía no fallback subject = monitorada.
#
# Política aplicada é a JÁ EXISTENTE no projeto (roteamento de não-pontuáveis
# em `apply_semantics_to_record`): subject_company != monitorada → contexto de
# terceiro, a mesma que já trata Vale/Samarco, Gerdau/transportadoras,
# Cencosud/St. Marche e BTG/Digimais. NENHUMA metodologia de risco consolidado
# é criada: esta wave corrige ATRIBUIÇÃO DE SUJEITO, não decide se risco de
# controlada deve afetar o score da controladora.
_REL_SUBSIDIARIA = (r"(?:subsidiary|subsidiaria|subsidi[áa]ria|unit|division|"
                    r"controlada|coligada|afiliada|affiliate|arm)")
# O nome da entidade é uma sequência de tokens CAPITALIZADOS. O grupo abaixo
# usa flag local `(?-i:…)` porque as buscas rodam com `re.I` — e sob `re.I` o
# `[A-Z]` deixa de exigir inicial maiúscula, fazendo a captura engolir
# conectivos e verbos minúsculos ("Omnicare TO GenieRX", "Omnicare FILES for
# Chapter"). Com a flag local, o limite sintático é o próprio fim da sequência
# capitalizada: nomes multipalavra legítimos ("Banco Digimais", "St. Marche",
# "Zurich Santander Brasil Seguros") continuam inteiros, e minúsculas param a
# captura naturalmente — sem lista de palavras de corte.
_ENT_NOME_B2 = (r"((?-i:[A-Z][\w&.\-]*"
                # conectivo MINUSCULO so continua o nome se vier outro token
                # capitalizado depois ("Banco de Brasilia", "Bank of America") —
                # assim "Omnicare to GenieRX" ainda para em "Omnicare", porque
                # "to" nao e conectivo nominal.
                r"(?:\s+(?:de|da|do|dos|das|of|del|y|e|&)\s+[A-Z][\w&.\-]*"
                r"|\s+[A-Z][\w&.\-]*){0,3}))")


# ── 4I.2 Wave B7b-2: follow-on de TERCEIRO, monitorada é INVESTIDORA ────────
# "Itaúsa aporta no aumento de capital DA AEGEA" não é follow-on da Itaúsa.
# A regra A6 (`is_monitored_credor`) cobre só `emissao_divida` e usa cues de
# EMPRÉSTIMO ("junto ao", "financia") — semântica diferente de aporte em
# equity. Detector análogo, não reuso forçado.
_EMISSOR_TERCEIRO = [
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+(?:aprova|anuncia|realiza|"
    r"lan[çc]a|conclui|precifica)\s+(?:o\s+|um\s+|a\s+)?"
    r"(?:aumento\s+de\s+capital|follow[- ]on|oferta\s+(?:p[úu]blica\s+)?de\s+a[çc][õo]es)",
    r"(?:aumento\s+de\s+capital|follow[- ]on|oferta\s+de\s+a[çc][õo]es)\s+d[ao]\s+"
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})",
    r"(?:fatia|participa[çc][ãa]o|stake)\s+n[ao]\s+"
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})",
    r"\bD[ao]\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})\s+[Aa]p[óo]s\s+"
    r"[Hh]omologa[çc][ãa]o\s+[Dd]e\s+[Aa]umento\s+[Dd]e\s+[Cc]apital",
]
# `{t}` absorve o ticker entre parenteses ("Itausa (ITSA4) pode aportar"),
# mesma forma do qualificador corporativo que a B4 tratou em "Truist BANK".
_TICKER_PAREN = r"(?:\s*\([A-Z0-9]{3,7}\))?"
_PAPEL_INVESTIDORA = [
    r"{m}{q}{t}\s+(?:pode\s+)?(?:aporta\w*|subscreve\w*|participa\w*|investe|investir)",
    r"{m}{q}{t}\s+planeja\s+(?:ampliar|aumentar)\s+(?:sua\s+)?(?:fatia|participa[çc][ãa]o)",
    r"{m}{q}{t}\s+(?:passa\s+a\s+deter|amplia\s+(?:sua\s+)?(?:fatia|participa[çc][ãa]o))",
    r"{m}{q}{t}\s+(?:acompanha|exerce\s+direito)",
]


def detect_follow_on_de_terceiro(text: str, monitored: str,
                                 aliases: list[str] | None = None) -> str:
    """Emissor TERCEIRO do follow-on quando a monitorada é apenas a
    INVESTIDORA/aportante. Devolve "" quando o emissor é a própria monitorada
    ou quando ela não está em papel de investidora — nunca infere."""
    if not text:
        return ""
    meus = {_n(a) for a in ((aliases or []) + [monitored]) if a}
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    tn = _n(text)
    if not any(re.search(p.format(m=alt, q=_QUALIF, t=_TICKER_PAREN), tn, re.I)
               for p in _PAPEL_INVESTIDORA):
        return ""                      # monitorada não está em papel de investidora
    for p in _EMISSOR_TERCEIRO:
        for m in re.finditer(p, text):
            cand = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
            cn = _n(cand)
            if not cn or len(cn) < 3 or cn in _STOP_ENT:
                continue
            if any(cn in a or a in cn for a in meus if a):
                continue               # o emissor é a própria monitorada
            return cand
    return ""


def detect_subsidiary_subject(text: str, monitored: str,
                              aliases: list[str] | None = None) -> str:
    """Subsidiária nomeada IMEDIATAMENTE ao lado da controladora monitorada.

    Reconhece só as duas construções explícitas — possessivo saxônico
    ("CVS Health's Omnicare") e aposto com palavra de relação ("CVS Health
    subsidiary Omnicare"). Devolve "" quando o nome capturado é a própria
    monitorada ou quando não há nome: nunca infere relação societária."""
    if not text:
        return ""
    meus = {_n(a) for a in ((aliases or []) + [monitored]) if a}
    nomes = [re.escape(a) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    padroes = [
        rf"{alt}\s*[’'`]s\s+{_ENT_NOME_B2}",              # CVS Health's Omnicare
        rf"{alt}\s+{_REL_SUBSIDIARIA}\s+{_ENT_NOME_B2}",  # CVS Health subsidiary Omnicare
        rf"{_REL_SUBSIDIARIA}\s+d[aeo]s?\s+{alt}[,\s]+{_ENT_NOME_B2}",
    ]
    for p in padroes:
        m = re.search(p, text, re.I)
        if not m:
            continue
        cand = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
        cn = _n(cand)
        if not cn or len(cn) < 3:
            continue
        if any(cn in a or a in cn for a in meus if a):
            continue                       # capturou a própria monitorada
        if cn in _STOP_ENT:
            continue
        return cand
    return ""


def _stem_pais(nome: str) -> str:
    """Radical do nome do país, para casar o demônimo adjetivo derivado dele
    ("Argentina"→argentin→"argentino"; "México"→mexic→"mexicano"). Derivação
    morfológica simples sobre metadado existente — não é tabela nova."""
    n = _n(nome or "").strip()
    n = re.sub(r"[^a-z\s]", "", n)
    n = re.sub(r"[aeiou]+$", "", n)
    return n if len(n) >= 4 else ""


def detect_sovereign_subject(text: str, event_keywords: list[str], monitored: str,
                             aliases: list[str] | None = None,
                             country: str = "") -> dict:
    """O evento de crédito pertence ao SOBERANO (país/governo/tesouro), e não à
    monitorada? Verdadeiro só quando (a) há marca soberana na MESMA proposição
    da menção do evento e (b) NÃO há evidência explícita de que a monitorada é
    a devedora — havendo, o sujeito corporativo vence (§6/§12/§16)."""
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    alt = "(?:" + "|".join(nomes) + ")" if nomes else ""
    if alt and any(re.search(p.format(m=alt, q=_QUALIF), t, re.I)
                   for p in DEVEDOR_CORPORATIVO_EXPLICITO):
        return {"soberano": False, "evidence": "", "motivo": "devedor corporativo explícito"}
    kws = [re.escape(_n(k)) for k in (event_keywords or []) if k and len(_n(k)) >= 2]
    if not kws:
        return {"soberano": False, "evidence": ""}
    kw_rx = re.compile(r"(?<!\w)(?:" + "|".join(kws) + r")(?!\w)")
    marcas = list(SOBERANO_TERMS)
    stem = _stem_pais(country)
    if stem:
        # O demônimo só vale em POSIÇÃO ADJETIVA colada ao evento ("default
        # argentino", "deuda argentina"). Sem essa restrição o radical casa
        # dentro do NOME da empresa — "Grupo México" derrubava o default
        # legítimo da Pemex, que é exatamente a nacionalidade-como-regra
        # proibida pelo §7.
        _kw_alt = "|".join(kws)
        marcas.append(rf"(?:{_kw_alt})\s+{stem}\w*\b")
        marcas.append(rf"\b{stem}\w*\s+(?:{_kw_alt})")
    mencoes = soberanas = 0
    ev = ""
    for prop in _proposicoes(t):
        if not kw_rx.search(prop):
            continue
        mencoes += 1
        achou = next((re.search(p, prop, re.I) for p in marcas
                      if re.search(p, prop, re.I)), None)
        if achou:
            soberanas += 1
            ev = ev or achou.group(0)[:60]
    return {"soberano": bool(mencoes) and soberanas == mencoes, "evidence": ev}


def detect_fraud_role(text: str, monitored: str,
                      aliases: list[str] | None = None) -> str:
    """Papel da monitorada num evento de fraude: "agente", "vitima" ou "".

    O papel de AGENTE vence sempre: um cue incidental de vítima noutra oração
    não pode apagar fraude realmente cometida/condenada pela companhia (§10).
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    if any(re.search(p.format(m=alt, q=_QUALIF, a=_ART), t, re.I) for p in FRAUDE_AGENTE):
        return "agente"
    if any(re.search(p.format(m=alt, q=_QUALIF, a=_ART), t, re.I) for p in FRAUDE_VITIMA):
        return "vitima"
    return ""


def detect_papel_nao_sujeito(text: str, monitored: str,
                             aliases: list[str] | None = None) -> str:
    """Papel da monitorada quando ela NÃO é o sujeito do evento. "" se nenhum."""
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    for papel, pats in _PAPEL_NAO_SUJEITO.items():
        if any(re.search(p.format(m=alt), t, re.I) for p in pats):
            return papel
    return ""


def is_default_nomenclatura_de_rating(text: str) -> bool:
    """O texto usa "default" apenas como NOME de métrica de rating (IDR) ou
    como cláusula contratual citada, sem qualquer default econômico real?"""
    t = _n(text)
    if not any(re.search(p, t, re.I) for p in NOMENCLATURA_RATING):
        return False
    return not any(re.search(p, t, re.I) for p in DEFAULT_ECONOMICO_REAL)
EVENTOS_CREDITO_EXIGEM_FATO = {"default", "default_cri", "covenant_breach",
                                "inadimplencia", "cross_default"}


def resolve_article_semantics(title: str, summary: str, monitored: str,
                              event_ids: list[str], aliases_por_empresa: dict,
                              *, article_year: int | None = None,
                              source_domain: str = "",
                              keywords_por_evento: dict | None = None,
                              country: str = "") -> dict:
    """Resolve TODOS os eventos candidatos de um artigo para UMA empresa
    monitorada. Devolve decisões por evento com evidência e regra aplicada."""
    texto = f"{title} {summary}".strip()
    hist = detect_historical_reference(texto, article_year)
    trans = detect_transaction(texto)
    fase = detect_juridical_phase(texto)
    papeis = detect_roles(texto, monitored, aliases_por_empresa)
    colapso = collapse_rating_actions(event_ids)

    decisoes = []
    for ev in event_ids:
        rej = ""
        d = {
            "monitored_company": monitored, "event_id": ev,
            "subject_company": monitored, "actor_company": papeis["actor_company"],
            "affected_company": papeis["affected_company"],
            "buyer_company": papeis["buyer_company"],
            "seller_company": papeis["seller_company"],
            "target_company": papeis["target_company"],
            "transaction_role": papeis["transaction_role"],
            "transaction_object": trans["transaction_object"],
            "transaction_scope": trans["transaction_scope"],
            "event_phase": fase["event_phase"] or trans["transaction_phase"],
            "event_scope": "direto", "relation_type": "",
            "direction": fase["direction"],
            "historical_reference": hist["historical_reference"],
            "new_occurrence": hist["new_occurrence"],
            "confirmation_level": fase["confirmation_level"],
            "temporal_evidence": hist["temporal_evidence"],
            "negation_detected": trans["negation_detected"],
            "scoreable": True, "attribution_rule": "", "rejection_reason": "",
            "attribution_confidence": "media",
        }
        # 1) colapso de ação de rating
        if ev not in colapso["event_ids"]:
            info = next(r for r in colapso["removidos"] if r["event_id"] == ev)
            d.update(scoreable=False, event_scope="absorvido",
                     attribution_rule="R_RATING_FAMILIA",
                     rejection_reason=f"absorvido por {info['absorvido_por']} "
                                      f"({info['motivo']})")
            decisoes.append(d)
            continue
        # 2) referência histórica
        if hist["historical_reference"]:
            d.update(scoreable=False, new_occurrence=False,
                     attribution_rule="R_HISTORICO",
                     rejection_reason=f"referência histórica "
                                      f"({hist['temporal_evidence']}); evento econômico "
                                      f"de {hist['event_year']}")
            decisoes.append(d)
            continue
        # 2b) NEGAÇÃO EXPLÍCITA DO EVENTO (4I.2 Wave A2)
        # Escopada: só nega o evento cujas menções estão todas em proposição
        # com negação. "Nega conversas de aquisição, mas confirma emissão de
        # dívida" derruba apenas o M&A (§12). Sem `keywords_por_evento` (uso
        # legado da função) o gate simplesmente não atua — nunca adivinha.
        _kws = (keywords_por_evento or {}).get(ev) or []
        if _kws:
            _neg = detect_event_negation(texto, _kws)
            if _neg["negated"]:
                d.update(scoreable=False, new_occurrence=False,
                         negation_detected=True,
                         event_scope="direto",
                         direction="mitigadora",
                         attribution_rule="R_NEGACAO_EXPLICITA",
                         rejection_reason=(f"texto nega explicitamente o evento "
                                            f"(\"{_neg['evidence'][:70]}\")"))
                decisoes.append(d)
                continue
        # 2c) RESOLUÇÃO de evento negativo anterior (4I.2 Wave A3)
        # Não é negação: o evento aconteceu, e a notícia informa o desfecho.
        # Vai para o bucket informativo já existente — o fato é preservado,
        # apenas deixa de contar como NOVO evento negativo (§4/§17).
        if _kws:
            _res = detect_event_resolution(texto, _kws)
            if _res["resolved"]:
                d.update(scoreable=False, new_occurrence=False,
                         event_scope="direto", direction="mitigadora",
                         attribution_rule="R_RESOLUCAO_EVENTO_ANTERIOR",
                         rejection_reason=(f"desfecho/resolução de evento anterior, não "
                                            f"novo evento (\"{_res['evidence'][:70]}\")"))
                decisoes.append(d)
                continue
        # 2e) "default" como NOMENCLATURA DE RATING (4I.2 Wave A4)
        # Só desarma o evento `default`: a ação de rating em si (upgrade ou
        # downgrade) segue o caminho normal da taxonomia de rating (§13/§25).
        if ev in ("default", "default_cri") and is_default_nomenclatura_de_rating(texto):
            d.update(scoreable=False, event_scope="direto",
                     attribution_rule="R_DEFAULT_NOMENCLATURA_RATING",
                     rejection_reason=("'default' aqui é nome de métrica de rating "
                                        "(Issuer Default Rating) ou cláusula contratual "
                                        "citada — não há inadimplemento econômico"))
            decisoes.append(d)
            continue
        # 2d) FASE JURÍDICA fora da família fraude (4I.2 Wave A1b)
        # Semântica por família, nunca um gate único (§8):
        #   crédito  → alegação/processo/investigação NÃO provam o fato
        #              econômico; só o fato consumado pontua;
        #   investigação regulatória → o evento É a investigação, então ato
        #              formal do regulador pontua; só rumor/alegação privada
        #              sem ato formal não basta.
        if ev in EVENTOS_CREDITO_EXIGEM_FATO and fase["event_phase"] in FASES_NAO_CONSUMADAS:
            d.update(scoreable=False, event_scope="direto",
                     attribution_confidence="baixa",
                     attribution_rule="R_CREDITO_EXIGE_FATO_CONSUMADO",
                     rejection_reason=(f"fase {fase['event_phase']}: denúncia/processo/"
                                        f"investigação não provam inadimplemento econômico"))
            decisoes.append(d)
            continue
        if (ev in EVENTOS_INVESTIGACAO_E_O_PROPRIO_EVENTO
                and fase["event_phase"] in ("alegacao", "acusacao_civil")
                and fase["event_phase"] != "investigacao"):
            # há alegação, mas nenhum ato formal de investigação no texto
            if not re.search(r"abre\s+(?:processo|investiga|inqu[ée]rito|expediente)"
                             r"|instaura\w*|busca\s+e\s+apreens|expediente\s+sancionador"
                             r"|opens?\s+(?:an?\s+)?(?:probe|investigation|inquiry)"
                             r"|formal\s+(?:probe|investigation)", _n(texto), re.I):
                d.update(scoreable=False, event_scope="direto",
                         attribution_confidence="baixa",
                         attribution_rule="R_INVESTIGACAO_SEM_ATO_FORMAL",
                         rejection_reason=("alegação/rumor sem ato formal de investigação "
                                            "por autoridade"))
                decisoes.append(d)
                continue
        # 3) sujeito estrito (RJ/falência/default)
        if ev in EVENTOS_SUJEITO_ESTRITO:
            terceiro = ""
            regra = ""
            _ent, _evid = subject_by_possessive(texto, ev, monitored, aliases_por_empresa)
            if _ent:
                terceiro, regra = _ent, "R_POSSESSIVO_MESMA_ORACAO"
            elif papeis["affected_company"] and papeis["affected_company"] != monitored:
                terceiro, regra = papeis["affected_company"], "R_CAUSACAO_TERCEIRO"
            elif papeis["transaction_role"] == "compradora" and papeis["target_company"]:
                terceiro, regra = papeis["target_company"], "R_COMPRADOR_NAO_SOFRE_RJ"
            elif papeis["third_party_statement"]:
                terceiro, regra = (papeis["target_company"] or "terceiro citado"), \
                                  "R_COMUNICADO_SOBRE_TERCEIRO"
            if terceiro:
                d.update(subject_company=terceiro, scoreable=False,
                         event_scope="indireto",
                         relation_type=("alvo_aquisicao" if regra == "R_COMPRADOR_NAO_SOFRE_RJ"
                                        else ("terceiro_afetado" if regra == "R_CAUSACAO_TERCEIRO"
                                              else "investida_jv")),
                         subject_evidence=_evid,
                         direction="mitigadora" if regra == "R_COMPRADOR_NAO_SOFRE_RJ" else "neutra",
                         attribution_rule=regra,
                         rejection_reason=f"sujeito verdadeiro é {terceiro}; "
                                          f"não representa {ev} de {monitored}",
                         attribution_confidence="alta")
                decisoes.append(d)
                continue
        # 4) M&A
        if ev in EVENTOS_MA:
            ok, motivo = ma_is_legitimate(texto, papeis)
            if not ok:
                novo = {"recompra_de_acoes_proprias_nao_e_ma": "recompra_acoes",
                        "reorganizacao_intragrupo_sob_controle_comum":
                            "reorganizacao_societaria_interna",
                        "contexto_pos_aquisicao_nao_e_nova_ocorrencia":
                            "integracao_pos_aquisicao",
                        "rumor_ou_oferta_nao_confirmada": "rumor_ma",
                        "negacao_explicita_de_nova_aquisicao": None,
                        }.get(motivo)
                if motivo.startswith("objeto_nao_empresarial"):
                    novo = "aquisicao_capex"
                secondary_context_id = ""
                # o CONTEÚDO PRINCIPAL da notícia pode ser um resultado econômico
                # (lucro/rating acima do esperado), não a integração em si — a
                # aquisição então vira só referência secundária/histórica.
                if novo == "integracao_pos_aquisicao" and detect_earnings_beat(texto):
                    secondary_context_id = "integracao_pos_aquisicao"
                    novo = "resultado_acima_expectativas"
                d.update(scoreable=False, attribution_rule="R_MA_OBJETO_ESCOPO",
                         rejection_reason=motivo,
                         event_id_corrigido=novo or "",
                         secondary_context_id=secondary_context_id,
                         direction=("positiva" if novo in ("recompra_acoes",
                                                           "integracao_pos_aquisicao",
                                                           "resultado_acima_expectativas")
                                    else "neutra"),
                         confirmation_level=("nao_confirmada" if novo == "rumor_ma"
                                             else d["confirmation_level"]))
                decisoes.append(d)
                continue
            d.update(attribution_rule="R_MA_LEGITIMO", event_scope="direto")
        # 5) fraude e eventos jurídicos
        if ev in EVENTOS_FRAUDE:
            _entf, _evidf = subject_by_possessive(texto, ev, monitored, aliases_por_empresa)
            if _entf:
                d.update(subject_company=_entf, scoreable=False, event_scope="indireto",
                         relation_type="terceiro_citado", subject_evidence=_evidf,
                         attribution_rule="R_POSSESSIVO_MESMA_ORACAO",
                         rejection_reason=f"sujeito verdadeiro é {_entf}; "
                                          f"não representa fraude de {monitored}",
                         attribution_confidence="alta")
                decisoes.append(d)
                continue
            if fase["direction"] == "mitigadora":
                d.update(scoreable=False, attribution_rule="R_FASE_JURIDICA_MITIGADORA",
                         rejection_reason=f"fase {fase['event_phase']} "
                                          f"(desfecho, não nova acusação)",
                         event_id_corrigido={"encerramento": "encerramento_litigio",
                                             "acordo": "acordo_judicial",
                                             "pagamento": "pagamento_contingencia",
                                             "absolvicao": "absolvicao",
                                             "arquivamento": "arquivamento_processo",
                                             }.get(fase["event_phase"], "desfecho_juridico"))
                decisoes.append(d)
                continue
            if fase["confirmation_level"] == "nao_confirmado":
                # 4I.2 Wave A1 — SEGUNDA falha independente encontrada na
                # auditoria: antes esta regra só reduzia `attribution_confidence`
                # e o evento continuava PONTUANDO com peso integral. O caso
                # CVS Health é a prova: o registro carregava
                # `legal_status="allegation/lawsuit"` e mesmo assim pontuava
                # fraude 90/crítico. Agora a fase não consumada realmente trava
                # o scoring — sem mexer em peso, threshold ou taxonomia: o
                # evento vai para o bucket informativo já existente, que é o
                # destino correto de "alegação ainda não comprovada".
                d.update(scoreable=False,
                         attribution_confidence="baixa",
                         attribution_rule="R_FRAUDE_NAO_CONFIRMADA",
                         event_scope="direto",
                         rejection_reason=(
                             f"fase {fase['event_phase']} — alegação/processo sem "
                             f"confirmação formal; não prova fraude consumada"))
        # ── ÚLTIMO RECURSO (4I.2 Waves A5/A6/A7) ──
        # Rodam DEPOIS de todas as regras estabelecidas (sujeito
        # estrito, M&A, fraude): elas resolvem sujeito/relação com
        # semântica já validada (Vale/Samarco, B3/Braskem) e não
        # podem ser atropeladas por estas. Só tratam o que sobrou.
        # A5) EVENTO CITADO COMO PASSADO (4I.2 Wave A5)
        if _kws:
            _pas = detect_evento_passado(texto, _kws)
            if _pas["passado"]:
                d.update(scoreable=False, new_occurrence=False,
                         event_scope="direto", direction="neutra",
                         attribution_rule="R_EVENTO_CITADO_COMO_PASSADO",
                         rejection_reason=(f"evento citado como anterior "
                                            f"(\"{_pas['evidence']}\"), não anunciado agora"))
                decisoes.append(d)
                continue
        # 2g) CREDOR ≠ DEVEDOR (4I.2 Wave A6) — papel econômico, nunca tipo de
        # empresa: banco continua podendo sofrer evento próprio (§31).
        # B7b-2) FOLLOW-ON DE TERCEIRO, MONITORADA É INVESTIDORA (4I.2)
        # Restrito a `follow_on`, por EMPRESA × EVENTO: só desarma o evento
        # para a investidora — o EMISSOR real continua recebendo o seu.
        if ev == "follow_on":
            _emissor = detect_follow_on_de_terceiro(
                texto, monitored, aliases_por_empresa.get(monitored) or [monitored])
            if _emissor:
                d.update(subject_company=_emissor, scoreable=False,
                         event_scope="indireto", relation_type="investidora",
                         attribution_rule="R_FOLLOW_ON_DE_TERCEIRO",
                         rejection_reason=(f"o follow-on/aumento de capital é da "
                                            f"'{_emissor}'; {monitored} apenas aporta"))
                decisoes.append(d)
                continue
        # B7b-1) TROCA DE CEO DE TERCEIRO, POR POSSESSIVO (4I.2)
        # Restrito a `troca_ceo` (§5). Opera por EMPRESA × EVENTO (§4): só
        # desarma este evento PARA ESTA monitorada — a empresa que é de fato
        # sujeito continua recebendo o seu, porque o laço roda por empresa e
        # `subject_by_possessive` já devolve "" quando a monitorada é a dona.
        if ev == "troca_ceo":
            _ceo_sub, _ceo_ev = subject_by_possessive(texto, ev, monitored,
                                                      aliases_por_empresa)
            if _ceo_sub:
                d.update(subject_company=_ceo_sub, scoreable=False,
                         event_scope="indireto", relation_type="terceiro_citado",
                         subject_evidence=_ceo_ev,
                         attribution_rule="R_TROCA_CEO_DE_TERCEIRO",
                         rejection_reason=(f"a troca de CEO é da '{_ceo_sub}'; "
                                            f"{monitored} aparece lateralmente"))
                decisoes.append(d)
                continue
        # B2) SUBSIDIÁRIA NOMEADA AO LADO DA CONTROLADORA (4I.2 Wave B2)
        # Corrige apenas o SUJEITO. O destino segue a política já existente
        # (subject != monitorada → contexto de terceiro), sem criar bucket
        # novo nem metodologia de risco consolidado.
        # Restrito a EVENTOS_SUJEITO_ESTRITO: são os eventos que a entidade
        # SOFRE (falência/RJ/default/liquidação), onde "quem sofreu" tem de ser
        # exato. Eventos que a subsidiária PRATICA (M&A como compradora) são
        # economicamente do grupo — "Cigna's Evernorth Completes Acquisition"
        # é aquisição correta da Cigna, e o gold confirma.
        _sub = (detect_subsidiary_subject(texto, monitored,
                                          aliases_por_empresa.get(monitored) or [monitored])
                if ev in EVENTOS_SUJEITO_ESTRITO else "")
        if _sub:
            d.update(subject_company=_sub, scoreable=False,
                     event_scope="indireto", relation_type="subsidiaria",
                     attribution_rule="R_EVENTO_DE_SUBSIDIARIA_NOMEADA",
                     rejection_reason=(f"o evento é da subsidiária '{_sub}'; "
                                        f"{monitored} é a controladora, não o sujeito"))
            decisoes.append(d)
            continue
        # B6) RISCO PROSPECTIVO / OBJETO CARTEIRA ≠ default do emissor
        # Posição: bloco de último recurso, junto das demais regras de papel —
        # só atua quando nenhuma evidência mais forte já identificou a
        # monitorada como sujeito direto (§14). Cede a devedor corporativo
        # explícito, então não vira blindagem de banco (§8).
        _al_b6 = aliases_por_empresa.get(monitored) or [monitored]
        if (ev in EVENTOS_SUJEITO_ESTRITO or ev in EVENTOS_CREDITO_EXIGEM_FATO) and _kws:
            _nc = detect_evento_nao_consumado(texto, _kws, monitored, _al_b6)
            if _nc["nao_consumado"]:
                d.update(scoreable=False, event_scope="indireto",
                         relation_type=("exposicao_carteira"
                                        if _nc["motivo"] == "objeto_carteira"
                                        else "risco_prospectivo"),
                         attribution_rule="R_EVENTO_NAO_CONSUMADO_OU_DE_CARTEIRA",
                         rejection_reason=(f"{_nc['motivo']}: \"{_nc['evidence']}\" — não há "
                                            f"inadimplemento consumado de obrigação própria"))
                decisoes.append(d)
                continue
        # B5) SOBERANO ≠ EMISSOR CORPORATIVO (4I.2 Wave B5)
        # Roda no bloco de último recurso, junto das demais regras de papel:
        # sujeito estrito, M&A, fraude e o próprio detector de devedor já
        # decidiram antes. Dentro do detector, devedor corporativo explícito
        # vence a marca soberana — logo default próprio da estatal sobrevive.
        _al_b5 = aliases_por_empresa.get(monitored) or [monitored]
        if (ev in EVENTOS_SUJEITO_ESTRITO or ev in EVENTOS_CREDITO_EXIGEM_FATO) and _kws:
            _sob = detect_sovereign_subject(texto, _kws, monitored, _al_b5, country)
            if _sob["soberano"]:
                d.update(scoreable=False, event_scope="indireto",
                         relation_type="evento_soberano",
                         attribution_rule="R_SOBERANO_NAO_E_EMISSOR_CORPORATIVO",
                         rejection_reason=(f"o evento de crédito é do soberano "
                                            f"(\"{_sob['evidence']}\"), não de {monitored}"))
                decisoes.append(d)
                continue
        _al = aliases_por_empresa.get(monitored) or [monitored]
        if ev in EVENTOS_SUJEITO_ESTRITO or ev in EVENTOS_CREDITO_EXIGEM_FATO:
            _dev = detect_debtor_subject(texto, monitored, _al)
            if _dev == "__monitorada_e_credora__":
                # a quantia inadimplida é DEVIDA À monitorada (construção
                # "calote de R$ X do <monitorada>") — ela é a credora lesada.
                d.update(scoreable=False, event_scope="indireto",
                         relation_type="credor_lesado",
                         attribution_rule="R_MONITORADA_E_CREDORA_LESADA",
                         rejection_reason=(f"o valor inadimplido é devido a {monitored}; "
                                            f"a monitorada é a credora, não a devedora"))
                decisoes.append(d)
                continue
            if _dev:
                d.update(subject_company=_dev, scoreable=False,
                         event_scope="indireto", relation_type="terceiro_devedor",
                         attribution_rule="R_CREDOR_NAO_HERDA_EVENTO_DO_DEVEDOR",
                         rejection_reason=(f"o evento de crédito pertence a '{_dev}'; "
                                            f"{monitored} não é o devedor"))
                decisoes.append(d)
                continue
        if ev in ("emissao_divida",) and is_monitored_credor(texto, monitored, _al):
            d.update(scoreable=False, event_scope="indireto",
                     relation_type="credor_financiador",
                     attribution_rule="R_MONITORADA_E_FINANCIADORA",
                     rejection_reason=(f"{monitored} aparece como financiadora da "
                                        f"operação de terceiro, não como emissora"))
            decisoes.append(d)
            continue
        # B4) FRAUDE: VÍTIMA ≠ AUTORA (4I.2 Wave B4)
        # Posição: bloco de último recurso, depois de sujeito estrito, M&A e
        # do bloco de fraude — logo, fraude com sujeito de terceiro, desfecho
        # mitigador ou fase não confirmada já saiu antes. Aqui só chega fraude
        # que seguiria pontuando como DIRETA da monitorada; o papel de agente
        # vence o de vítima dentro do próprio detector.
        if ev in EVENTOS_FRAUDE:
            _fr = detect_fraud_role(texto, monitored, _al)
            if _fr == "vitima":
                d.update(scoreable=False, event_scope="indireto",
                         relation_type="vitima_do_evento",
                         attribution_rule="R_VITIMA_NAO_E_AUTORA_DA_FRAUDE",
                         rejection_reason=(f"{monitored} é vítima/alvo da fraude neste "
                                            f"texto, não quem a praticou"))
                decisoes.append(d)
                continue
        # 2h) PAPEL NÃO-SUJEITO: vítima / comentarista / investigador (Wave A7)
        _papel = detect_papel_nao_sujeito(texto, monitored, _al)
        if _papel:
            d.update(scoreable=False, event_scope="indireto",
                     relation_type=f"papel_{_papel}",
                     attribution_rule="R_PAPEL_NAO_SUJEITO",
                     rejection_reason=(f"{monitored} atua como {_papel} neste texto, "
                                        f"não como sujeito do evento"))
            decisoes.append(d)
            continue
        decisoes.append(d)
    return {"decisoes": decisoes, "historico": hist, "transacao": trans,
            "fase": fase, "papeis": papeis, "rating_colapso": colapso}


# ═══════════════════════════════════════════════════════════════════════
# INTEGRAÇÃO COM O PIPELINE DE PRODUÇÃO
# ═══════════════════════════════════════════════════════════════════════
def _country_de(cfg: dict, empresa: str) -> str:
    """País de domicílio do cadastro — usado APENAS para reconhecer o soberano
    (Wave B5), nunca como fallback de atribuição de sujeito (§11)."""
    c = next((x for x in (cfg.get("watchlist") or []) if x.get("name") == empresa), None)
    return (c or {}).get("country", "") or ""


def _keywords_por_evento(cfg: dict) -> dict:
    """Vocabulário de cada evento vindo da PRÓPRIA taxonomia de produção —
    nunca uma lista paralela hardcoded. Assim a negação escopada (Wave A2)
    acompanha automaticamente qualquer mudança futura da taxonomia, sem
    precisar ser reeditada."""
    return {e["id"]: list(e.get("keywords") or []) for e in (cfg.get("taxonomy") or [])}


def _aliases_map(cfg: dict) -> dict:
    return {c["name"]: (c.get("aliases") or [c["name"]])
            for c in (cfg.get("watchlist") or [])}


def _ano_do_registro(rec: dict) -> int:
    ts = rec.get("pub_ts") or rec.get("captured_ts") or 0
    if not ts:
        return 2026
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, timezone.utc).year
    except Exception:
        return 2026


# Reclassificações informativas: evento sai do pontuável e vira contexto.
EVENTO_INFORMATIVO = {
    "recompra_acoes", "reorganizacao_societaria_interna", "integracao_pos_aquisicao",
    "aquisicao_capex", "encerramento_litigio", "acordo_judicial",
    "pagamento_contingencia", "rumor_ma", "evento_historico", "absolvicao",
    "arquivamento_processo", "desfecho_juridico",
}


def apply_semantics_to_record(rec: dict, cfg: dict, *, aliases: dict | None = None) -> dict:
    """Aplica a resolução semântica a UM registro do histórico, IN-PLACE.

    É o ponto único de integração: usada tanto pelo pipeline normal
    (`classify_and_attribute`) quanto pela reclassificação offline.

    Move o evento de `events_by_company` para `context_events_by_company`
    quando o sujeito verdadeiro é outra entidade, quando a referência é
    histórica, quando o M&A não é empresarial, quando a fase jurídica é de
    desfecho, ou quando a ação de rating já foi contabilizada pela família.

    Devolve um resumo do que mudou (para auditoria)."""
    aliases = aliases or _aliases_map(cfg)
    ebc = rec.get("events_by_company")
    if not isinstance(ebc, dict) or not any(ebc.values()):
        return {"mudou": False, "decisoes": []}
    titulo = rec.get("title", "") or ""
    resumo = rec.get("summary", "") or ""
    ano = _ano_do_registro(rec)
    ctx = rec.get("context_events_by_company") or {}
    info = rec.get("informational_events_by_company") or {}
    descartes = rec.get("semantic_discards") or []
    # `event_assessments` é uma LISTA de dicts com chaves company/event_id —
    # formato consumido por build_evolution. Preservar exatamente.
    _ass_raw = rec.get("event_assessments")
    assessments = _ass_raw if isinstance(_ass_raw, list) else []
    mudou, todas = False, []

    for empresa in list(ebc.keys()):
        eventos = list(ebc.get(empresa) or [])
        if not eventos:
            continue
        r = resolve_article_semantics(titulo, resumo, empresa, eventos, aliases,
                                      article_year=ano,
                                      source_domain=rec.get("domain", "") or "",
                                      keywords_por_evento=_keywords_por_evento(cfg),
                                      country=_country_de(cfg, empresa))
        manter = []
        # Eventos que PERMANECEM pontuáveis nesta empresa/artigo — qualquer
        # outro evento descartado da MESMA empresa neste MESMO artigo, cujo
        # sujeito também seja a própria empresa, é um COMPONENTE SECUNDÁRIO
        # daquela ocorrência (ex.: outlook absorvido pelo downgrade da mesma
        # ação; "ma"→integração descartado no mesmo artigo em que o rating
        # já pontua para a mesma empresa, caso PRIO/S&P) — não é um sinal
        # autônomo e NÃO deve ganhar card próprio em nenhum bucket de
        # exibição (nem contexto, nem informativo).
        scoreable_ids_empresa = {dd["event_id"] for dd in r["decisoes"] if dd["scoreable"]}
        for d in r["decisoes"]:
            todas.append(d)
            ev = d["event_id"]
            # idempotência: substitui a avaliação anterior da mesma
            # (empresa, evento) NA MESMA POSIÇÃO em vez de acumular
            # duplicatas ou reordenar a lista a cada execução (uma segunda
            # aplicação em que só um SUBCONJUNTO de eventos é reprocessado —
            # porque os demais já saíram de `events_by_company` — não pode
            # mudar a ordem dos que ficaram parados).
            _idx_existente = next((i for i, x in enumerate(assessments)
                                   if x.get("company") == empresa and x.get("event_id") == ev
                                   and x.get("assessed_by") == "semantic_audit"), None)
            _nova_assessment = {
                "company": empresa, "event_id": ev,
                "assessed_by": "semantic_audit",
                "subject_company": d["subject_company"],
                "actor_company": d["actor_company"],
                "affected_company": d["affected_company"],
                "transaction_object": d["transaction_object"],
                "transaction_scope": d["transaction_scope"],
                "transaction_role": d["transaction_role"],
                "event_phase": d["event_phase"],
                "event_scope": d["event_scope"],
                "direction": d["direction"],
                "historical_reference": d["historical_reference"],
                "new_occurrence": d["new_occurrence"],
                "confirmation_level": d["confirmation_level"],
                "attribution_rule": d["attribution_rule"],
                "attribution_confidence": d["attribution_confidence"],
                "scoreable": d["scoreable"],
                "rejection_reason": d["rejection_reason"],
                "legal_status": d["event_phase"] or "",
                "confirmation_status": d["confirmation_level"] or "",
            }
            if _idx_existente is not None:
                assessments[_idx_existente] = _nova_assessment
                _assess_idx = _idx_existente
            else:
                assessments.append(_nova_assessment)
                _assess_idx = len(assessments) - 1
            if d["scoreable"]:
                manter.append(ev)
                continue
            mudou = True
            corrigido = d.get("event_id_corrigido") or ""
            # Destino do evento NÃO PONTUÁVEL depende do SUJEITO REAL:
            #   subject_company != empresa monitorada → contexto de TERCEIRO
            #     real (Vale/Samarco, Gerdau/transportadoras, Cencosud/St.
            #     Marche, BTG/Digimais) — vai para `context_events_by_company`.
            #   subject_company == empresa monitorada  → evento DIRETO do
            #     próprio emissor, apenas não pontuável (positivo, neutro,
            #     informativo, ou absorvido pela família de rating) — vai
            #     para `informational_events_by_company`. NUNCA tratar a
            #     própria empresa como "entidade relacionada" a si mesma.
            is_direct = _n(d["subject_company"]) == _n(empresa)
            if is_direct and scoreable_ids_empresa:
                # componente SECUNDÁRIO de uma ocorrência já pontuável da
                # MESMA empresa neste artigo (família de rating, ou evento
                # co-detectado no mesmo texto) — fica só como metadado do
                # evento principal em `event_assessments`/`semantic_discards`,
                # NUNCA como card independente em informational/context.
                assessments[_assess_idx]["family_secondary"] = True
                assessments[_assess_idx]["primary_event_id"] = sorted(scoreable_ids_empresa)[0]
                descartes.append({
                    "empresa": empresa, "event_id": ev,
                    "event_id_corrigido": corrigido,
                    "regra": d["attribution_rule"],
                    "motivo": d["rejection_reason"][:220],
                    "subject_company": d["subject_company"],
                    "family_secondary": True,
                    "primary_event_id": sorted(scoreable_ids_empresa)[0],
                })
                continue
            if not is_direct:
                ctx.setdefault(empresa, [])
                if not any(c.get("event_id") == ev for c in ctx[empresa]):
                    ctx[empresa].append({
                        "event_id": ev,
                        "event_label": (corrigido or ev).replace("_", " "),
                        "subject_company": d["subject_company"],
                        "relation_type": d["relation_type"] or "evento_reclassificado",
                        "impact_type": ("indireto_material" if d["event_scope"] == "indireto"
                                        else "informativo"),
                        "event_scope": d["event_scope"] or "informativo",
                        "event_phase": d["event_phase"],
                        "direction": d["direction"] or "neutra",
                        "scoreable": False,
                        "event_id_corrigido": corrigido,
                        "attribution_rule": d["attribution_rule"],
                        "attribution_confidence": d["attribution_confidence"],
                        "attribution_evidence": (d.get("subject_evidence")
                                                 or d.get("temporal_evidence") or "")[:160],
                        "nota": d["rejection_reason"][:220],
                    })
            else:
                final_id = corrigido or ev
                secondary_ctx_id = d.get("secondary_context_id") or ""
                direction_final = d["direction"] or "neutra"
                display_category = "positivo" if direction_final == "positiva" else "informativo"
                pos_aquisicao = (final_id == "integracao_pos_aquisicao"
                                  or secondary_ctx_id == "integracao_pos_aquisicao"
                                  or "pos_aquisicao" in (d["rejection_reason"] or ""))
                hist_ref = bool(d["historical_reference"]) or pos_aquisicao
                info.setdefault(empresa, [])
                if not any(c.get("event_id") == final_id and c.get("source_record_id") == rec.get("url", "")
                           for c in info[empresa]):
                    info[empresa].append({
                        "company": empresa,
                        "event_id": final_id,
                        "event_label": (corrigido or ev).replace("_", " "),
                        "subject_company": empresa,
                        "monitored_company": empresa,
                        "relation_type": "direto",
                        "event_scope": "direto",
                        "direction": direction_final,
                        "scoreable": False,
                        "display_category": display_category,
                        "new_ma_occurrence": False,
                        "change_of_control": False,
                        "external_ma": False,
                        "historical_transaction_reference": hist_ref,
                        "secondary_context": secondary_ctx_id or corrigido or "",
                        "transaction_scope": d.get("transaction_scope") or "",
                        "confirmation_status": ("confirmado" if hist_ref
                                                 else (d.get("confirmation_level") or "indefinido")),
                        "attribution_rule": d["attribution_rule"],
                        "attribution_confidence": d["attribution_confidence"],
                        "title": titulo,
                        "url": rec.get("url", ""),
                        "pub_ts": rec.get("pub_ts"),
                        "observation": d["rejection_reason"][:220],
                        "source_record_id": rec.get("url", ""),
                    })
            descartes.append({
                "empresa": empresa, "event_id": ev,
                "event_id_corrigido": corrigido,
                "regra": d["attribution_rule"],
                "motivo": d["rejection_reason"][:220],
                "subject_company": d["subject_company"],
            })
        ebc[empresa] = manter

    if mudou:
        rec["context_events_by_company"] = ctx
        rec["informational_events_by_company"] = info
        rec["semantic_discards"] = descartes
        rec["event_assessments"] = assessments
        rec["companies_attributed"] = [c for c, v in ebc.items() if v]
        ctx_comps = [c for c, v in ctx.items() if v]
        rec["context_companies"] = sorted(set((rec.get("context_companies") or []) + ctx_comps))
        # event_ids global (legado) precisa refletir o que sobrou
        rec["event_ids"] = sorted({e for v in ebc.values() for e in (v or [])})
        rec["semantic_version"] = SEMANTIC_VERSION
    return {"mudou": mudou, "decisoes": todas}


SEMANTIC_VERSION = "1.0"
