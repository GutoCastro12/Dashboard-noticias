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

import contextlib
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

# ── 4I.2 P0: PERFIL/VERBETE DE COMPANHIA ────────────────────────────────────
# Páginas enciclopédicas e perfis corporativos listam a TRAJETÓRIA da empresa
# como TÓPICOS, não como fatos do dia: "General Motors (GM) | History, Growth,
# Bankruptcy, & Recovery". A falência ali é item de sumário, não ocorrência.
#
# Sinal estrutural (não lexical solto): o texto enumera ≥2 tópicos de
# trajetória em lista separada por vírgula/&/pipe. `history` sozinho num
# título jamais basta — "NextEra's acquisition would bring history of
# political fights" tem `history` e NÃO é perfil.
_PERFIL_TOPICOS = (r"hist[óo]r(?:y|ia|ical)|growth|recovery|overview|profile|timeline|"
                   r"perfil|trajet[óo]ria|linha do tempo|crescimento")
_PERFIL_LISTA = re.compile(
    r"(?:" + _PERFIL_TOPICOS + r")\b[^|]{0,40}?[,&|][^|]{0,40}?\b(?:" + _PERFIL_TOPICOS + r")\b",
    re.I)
# Verbo/locução de OCORRÊNCIA ATUAL — vence o marcador de perfil (§7). Sem
# isto, um perfil que noticiasse um pedido de falência seria apagado.
_OCORRENCIA_ATUAL = re.compile(
    r"\b(files?|filed|filing|seeks?|sought|enters?|entered|declares?|declared|"
    r"approv\w+|orders?|ordered|wins?|won|exits?|exited|emerges?|emerged|"
    r"pede|pediu|entra|entrou|decreta|decretou|homologa|homologou|aprova|aprovou|"
    r"protocola|protocolou|solicita|solicitou)\b", re.I)


def detect_company_profile(text: str) -> str:
    """Verbete/perfil de companhia: a trajetória é o ASSUNTO, não o fato.

    Devolve a evidência ou "". Exige lista de tópicos de trajetória E ausência
    de verbo de ocorrência atual — keyword cria candidato, não prova evento.
    """
    t = _n(text)
    if _OCORRENCIA_ATUAL.search(t):
        return ""
    m = _PERFIL_LISTA.search(t)
    return m.group(0).strip()[:60] if m else ""


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
    # 4I.2 P0: verbete/perfil de companhia é referência histórica por natureza —
    # reusa o mesmo bucket e a mesma regra `R_HISTORICO` já validados no caso
    # WSAW/2009, em vez de criar mecanismo paralelo.
    if not hist:
        _perfil = detect_company_profile(text)
        if _perfil:
            hist = True
            marcador = f"perfil_de_companhia:{_perfil}"
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
    # A companhia comprando as próprias ações não adquire ninguém. Esta lista
    # já dizia isso — e `ma_is_legitimate` já devolvia
    # `recompra_de_acoes_proprias_nao_e_ma` — mas o vocabulário não alcançava a
    # construção do Fato Relevante da CVM, "Aquisição de Ações de Emissão da
    # Própria Companhia". Seis recompras (Porto, Embraer, Gerdau, Ultrapar,
    # Eneva, Vale) pontuavam como M&A; na Vale era 53% do score e definia o
    # pior evento. Duas do MESMO formulário eram barradas por acidente, porque
    # traziam "Cancelamento de Ações" no resumo e casavam por outro padrão.
    #
    # O que distingue recompra de aquisição real é a AUTORREFERÊNCIA — própria
    # companhia, sua própria emissão, own shares. "Ações de emissão da
    # Companhia Beta" continua sendo M&A legítimo e não pode casar aqui.
    # `cancelamento de ações` solto NÃO entra: era o único padrão da família que
    # não exigia autorreferência, e ações também são canceladas em fusão,
    # incorporação e redução de capital. Ele suprimia a fusão BRF/Marfrig como
    # se fosse recompra — o cancelamento ali é etapa de implementação da
    # combinação, não compra das próprias ações. Falso negativo de M&A, e falso
    # negativo não aparece no painel.
    #
    # Medido antes de trocar: no corpus inteiro o padrão amplo era evidência
    # ÚNICA em um único artigo (justamente o falso negativo). Nos casos de
    # recompra verdadeira ele era redundante — havia sempre outra evidência
    # autorreferente no mesmo texto. Estreitá-lo remove só o dano.
    "acoes_proprias": [r"recompra\s+de\s+a[çc][õo]es", r"buyback", r"share\s+repurchase",
                       r"treasury\s+shares?", r"a[çc][õo]es\s+pr[óo]prias",
                       r"cancelamento\s+d[ae]s?\s+a[çc][õo]es\s+"
                       r"(?:pr[óo]prias|em\s+tesouraria|mantidas\s+em\s+tesouraria|"
                       r"de\s+(?:sua\s+)?pr[óo]pria\s+emiss[ãa]o|"
                       r"de\s+emiss[ãa]o\s+pr[óo]pria|"
                       r"de\s+emiss[ãa]o\s+d[ao]\s+pr[óo]pri[ao]\s+"
                       r"(?:companhia|empresa|sociedade|emissora))",
                       r"cancellation\s+of\s+(?:its\s+)?(?:own|treasury)\s+shares?",
                       r"a[çc][õo]es\s+em\s+tesouraria",
                       r"recompra\s+de\s+d[íi]vida", r"deb[êe]ntures\s+pr[óo]prias",
                       r"cotas\s+do\s+pr[óo]prio\s+fundo",
                       # emissão autorreferente: o possessivo é obrigatório
                       r"a[çc][õo]es\s+de\s+emiss[ãa]o\s+d[ao]\s+pr[óo]pri[ao]\s+"
                       r"(?:companhia|empresa|sociedade|emissora)",
                       r"a[çc][õo]es\s+de\s+(?:sua\s+)?pr[óo]pria\s+emiss[ãa]o",
                       r"a[çc][õo]es\s+de\s+emiss[ãa]o\s+pr[óo]pria",
                       r"a[çc][õo]es\s+de\s+sua\s+emiss[ãa]o",
                       # inglês e espanhol, mesma exigência de autorreferência
                       r"(?:its|their)\s+own\s+(?:shares?|stock)",
                       r"repurchase\s+of\s+(?:its\s+)?own\s+shares?",
                       r"shares?\s+of\s+its\s+own\s+issuance",
                       r"acciones\s+propias",
                       r"acciones\s+de\s+(?:su\s+)?propia\s+emisi[óo]n"],
    # 4I.2 Wave C2: `kc[- ]390` é designação de MODELO, mesma natureza dos
    # `e190`/`e195` já presentes — "aquisição dos três Embraer KC-390" é compra
    # de N unidades de um produto, não da companhia.
    "aeronaves": [r"aeronaves?", r"aircraft", r"avi[õo]es", r"jatos?", r"e190", r"e195",
                  r"kc[- ]?390", r"motores?\s+ge", r"engines?"],
    # 4I.2 Wave C2: CARTEIRA DE PEDIDOS (backlog). "Aquisição da Grécia pode
    # adicionar US$ 690 milhões à carteira da Embraer" é RECEITA COMERCIAL do
    # fabricante, não aquisição empresarial. Restrito à construção observada
    # (algo entra na carteira de alguém); não colide com `carteira de crédito`,
    # que já é tratada como objeto financeiro.
    # Escopo PT: os dois casos reais são PT. Sem formas EN — quando houver caso
    # observado em inglês, elas entram junto com a evidência.
    "pedido_comercial": [r"[àa]\s+carteira\s+d[ao]\s+", r"carteira\s+de\s+pedidos"],
    "equipamento": [r"equipamentos?", r"maquin[áa]rio", r"m[áa]quinas?", r"machinery",
                    r"frota de caminh"],
    "imovel": [r"im[óo]ve(?:l|is)", r"terreno", r"real\s+estate", r"galp[ãa]o"],
    "capex_ativo": [r"capex", r"renova[çc][ãa]o\s+de\s+frota", r"usina", r"planta industrial"],
    # ── 4I.2 R7b-S2: O QUE FOI COMPRADO, NÃO SÓ QUEM COMPROU ────────────────
    # Ground truth humano (2026-08-12): BTG adquirindo uma FAZENDA e Petrobras
    # adquirindo um BLOCO EXPLORATÓRIO são adquirentes reais — o papel está
    # certo — mas o objeto não é societário, e por isso não são `ma`. O mapa
    # de objetos já existia e já governava a rejeição; faltava-lhe o léxico de
    # PROPRIEDADE RURAL e de DIREITO/CONCESSÃO. Nada aqui decide materialidade:
    # a informação continua registrada como evento direto não pontuável.
    "imovel_rural": [r"fazendas?", r"propriedades?\s+rura(?:l|is)",
                     r"im[óo]ve(?:l|is)\s+rura(?:l|is)", r"terras?\s+(?:agr[íi]cola|rura)\w*",
                     r"[áa]reas?\s+rura(?:l|is)", r"hectares", r"\bch[áa]cara",
                     r"farmland", r"\bfarms?\b"],
    "direito_exploratorio": [
        r"blocos?\s+explorat[óo]rios?", r"[áa]reas?\s+explorat[óo]rias?",
        r"lotes?\s+explorat[óo]rios?", r"direitos?\s+de\s+explora[çc][ãa]o",
        r"direitos?\s+miner[áa]rios?", r"campos?\s+(?:de\s+petr[óo]leo|petrol[íi]feros?)",
        r"exploration\s+(?:block|rights?|acreage)", r"mining\s+rights?",
        # concessões de infraestrutura: o objeto é o DIREITO de operar, não a
        # sociedade concessionária (`concession[áa]ria` não casa aqui, de
        # propósito — comprar a concessionária é aquisição societária).
        r"concess(?:[ãa]o|[õo]es)\s+(?:rodovi\w+|ferrovi\w+|aeroportu\w+|portu\w+)",
        r"concess(?:[ãa]o|[õo]es)\s+d[eo]\s+(?:rodovia|ferrovia|aeroporto|porto|saneamento|"
        r"[áa]gua|esgoto|energia|transmiss[ãa]o|distribui[çc][ãa]o|explora[çc][ãa]o|lote)",
    ],
    "carteira_de_ativos": [r"carteiras?\s+de\s+ativos", r"portf[óo]lios?\s+de\s+ativos",
                           r"asset\s+portfolio"],
}
# Evidência POSITIVA de objeto societário. Existe para dois fins: (a) provar
# `empresa` em vez de deduzi-la por ausência de blocker, e (b) VENCER o léxico
# de ativo quando os dois aparecem — "aquisição de participação na
# concessionária Alfa" é societário mesmo citando concessão. Exige SUBSTANTIVO
# societário: percentual sozinho não basta ("30% de uma fazenda" continua ativo).
OBJ_SOCIETARIO_FORTE = [
    r"participa[çc][ãa]o\s+(?:acion[áa]ria|societ[áa]ria)", r"participa[çc][ãa]o\s+d[aeo]\s",
    r"participa[çc][ãa]o\s+n[ao]\s", r"participa[çc][ãa]o\s+em\s",
    r"controle\s+acion[áa]rio", r"capital\s+social", r"joint\s*ventures?",
    r"a[çc][õo]es\s+d[aeo]\s", r"quotas?\s+d[aeo]\s", r"\bstakes?\s+in\b",
    r"\bequity\s+(?:stake|interest)",
]
# NÃO entram aqui `subsidi[áa]ria`/`controlada d...`: nomeiam uma RELAÇÃO entre
# empresas, não o objeto da compra, e no corpus real aparecem descrevendo o
# COMPRADOR com a mesma frequência ("Controlada da Cemig conclui aquisição de
# usinas fotovoltaicas"). Quando o alvo é mesmo uma controlada nomeada, o ramo
# de entidade nomeada de `ma_is_legitimate` já aceita — com objeto `indefinido`,
# que é a resposta honesta: ninguém provou o tipo do objeto.
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


def _societario_apos_verbo(t: str) -> bool:
    """Evidência societária no OBJETO da aquisição, não no sujeito dela.

    O blast da R7b-S2 mostrou por que a janela importa: "Controlada da Cemig
    conclui aquisição de usinas fotovoltaicas" tem `controlada d...` no texto,
    mas isso descreve QUEM COMPRA — o que se compra é uma usina. Só conta o que
    vem depois do verbo de aquisição. É a própria distinção que a wave existe
    para fazer: papel não é objeto.
    """
    m = _MA_VERBO_RX.search(t)
    if not m:
        return False
    janela = t[m.end():m.end() + 140]
    return any(re.search(p, janela) for p in OBJ_SOCIETARIO_FORTE)


def detect_transaction(text: str) -> dict:
    """Resolve objeto, escopo e fase da transação (itens 4, 7, 8, 11, 13)."""
    t = _n(text)
    obj, escopo = "", ""
    # `acoes_proprias` primeiro: recompra é rejeição própria e não pode ser
    # sobreposta pela evidência societária ("recompra de ações DA companhia").
    if any(re.search(p, t) for p in OBJ_NAO_EMPRESA["acoes_proprias"]):
        obj, escopo = "acoes_proprias", "capital_proprio"
    elif _societario_apos_verbo(t):
        obj, escopo = "empresa", "externo"
    else:
        for k, pats in OBJ_NAO_EMPRESA.items():
            if k == "acoes_proprias":
                continue
            if any(re.search(p, t) for p in pats):
                obj, escopo = k, "capex"
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


_OBJ_NAO_EMPRESARIAL_IDS = frozenset(k for k in OBJ_NAO_EMPRESA if k != "acoes_proprias")
_MA_VERBO_RX = re.compile(
    r"(aquisi[çc][ãa]o|compra|adquir\w+|fus[ãa]o|incorpora[çc][ãa]o|merger|"
    r"acquisition|acquires?|takeover|oferta p[úu]blica de aquisi|"
    # R7b-S2: assumir o controle é aquisição societária sem a palavra "compra"
    r"assum\w+\s+(?:o\s+)?controle|takes?\s+control)", re.I)
_MA_PARTICIPACAO_RX = re.compile(
    r"(\d{1,3}(?:[.,]\d+)?\s*%|participa[çc][ãa]o|stake|controle|sociedade|"
    r"joint\s*venture|capital\s+social)", re.I)
# objetos que, mesmo com verbo de aquisição, NÃO são M&A empresarial
_MA_OBJ_FINANCEIRO_RX = re.compile(
    r"(deb[êe]ntures|b[ôo]nus|bonds?|notas?\s+comerciais|cotas?|t[íi]tulos?|"
    r"a[çc][õo]es\s+pr[óo]prias|carteira\s+de\s+cr[ée]dito)", re.I)


# ── 4I.2 Wave C1: PAPEL TRANSACIONAL DE VENDEDOR ────────────────────────────
# Quem VENDE um ativo não faz uma aquisição. A taxonomia atual não tem evento
# próprio para desinvestimento (lacuna DESINVESTIMENTO_SEM_EVENTO_PROPRIO), e
# até que exista, `ma`/`follow_on` simplesmente não pertencem à vendedora.
#
# Exige a cadeia COMPLETA (C1b): OUTRO comprador nomeado + verbo de aquisição
# + objeto + preposição de origem governando a monitorada. `da <monitorada>`
# sozinho NUNCA basta — é ambíguo ("aquisição da Aegea" é aquisição FEITA
# pela Aegea, um TRUE do gold.)
_SELLER_S3_S5 = [
    # PT (S3): "Âmbar Energia conclui a aquisição de 4 hidrelétricas DA CEMIG"
    r"\w[\w\s&.\-]{{2,40}}?\s+(?:conclui|aprova|anuncia|assina)?\s*(?:a\s+)?"
    r"(?:aquisi[çc][ãa]o|compra)\s+d[aeo]s?\s+(?P<obj>[\w\s\-]{{2,40}}?)\s+"
    r"d[aeo]\s+{m}\b",
    # EN (S5): "Spire completes acquisition of <business> FROM DUKE ENERGY"
    r"\w[\w\s&.\-]{{2,40}}?\s+(?:completes?|announces?|closes?)?\s*(?:the\s+)?"
    r"(?:acquisition|purchase)\s+of\s+(?P<obj>[\w\s\-]{{2,50}}?)\s+from\s+{m}\b",
    r"\w[\w\s&.\-]{{2,40}}?\s+(?:acquires?|buys?|purchases?)\s+"
    r"(?P<obj>[\w\s\-]{{2,50}}?)\s+from\s+{m}\b",
]
# H1B4 — OBJETO SOCIETÁRIO: se o que se compra são AÇÕES/PARTICIPAÇÃO da
# monitorada, ela é o ALVO cujo capital muda de mãos, não a vendedora.
# "Ternium conclui aquisição de ações da Usiminas" → Usiminas é target (C3).
_SELLER_OBJ_SOCIETARIO = re.compile(
    r"\b(a[çc][õo]es|a[çc][ãa]o|participa[çc][ãa]o|fatia|stake|shares?|quotas?|"
    r"capital)\b", re.I)
# H1B6 — MARCADOR DE COMPRADOR `por`: "aquisição de fazenda por banco DO BTG"
# — aqui `do BTG` é parte do NOME do comprador, não preposição de origem.
_SELLER_MARCADOR_COMPRADOR = re.compile(r"\bpor\b", re.I)


def detect_transaction_seller_role(text: str, monitored: str,
                                   aliases: list[str] | None = None) -> str:
    """A monitorada é a VENDEDORA da transação? Devolve evidência ou "".

    Papel TRANSACIONAL — o artigo continua diretamente relacionado à empresa;
    o que está errado é o event_id, não a relevância (§20 do brief C1a).
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    for p in _SELLER_S3_S5:
        m = re.search(p.format(m=alt), t, re.I)
        if not m:
            continue
        obj = m.group("obj") or ""
        if _SELLER_OBJ_SOCIETARIO.search(obj):      # H1B4 → é target, não seller
            return ""
        trecho = t[m.start():m.end()]
        if _SELLER_MARCADOR_COMPRADOR.search(trecho):   # H1B6 → `do X` é o comprador
            return ""
        return trecho.strip()[:80]
    return ""


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
    # derivado do mapa (R7b-S2): qualquer objeto não-empresarial catalogado
    # rejeita, sem precisar repetir a lista aqui e deixá-la envelhecer.
    if d["transaction_object"] in _OBJ_NAO_EMPRESARIAL_IDS:
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


# ── credor REQUERENTE: quem PEDE a falência não é quem vai à falência ───────
# Lacuna medida no caso Santander/Minera Cobre Verde (2026-08-12): a família
# credor≠devedor cobria duas construções — devedor nomeado por possessivo
# ("falência DA Oi") e monitorada como FINANCIADORA ("junto ao banco") — e
# nenhuma alcança "Santander entra na Justiça para solicitar falência". Ali o
# devedor NÃO é nomeado e o banco não financia nada: ele REQUER. Sem sujeito
# possessivo para extrair, `detect_debtor_subject` devolvia vazio e o evento
# seguia atribuído ao próprio requerente.
#
# A estrutura que decide é verbal, não lexical: SUJEITO + VERBO DE
# REQUERIMENTO + falência. Quem requer está no polo ativo; o falido é o
# requerido. Vale mesmo quando o devedor não é nomeado no texto local — e é
# justamente aí que precisa funcionar, porque muitas vezes só há a manchete.
_VERBOS_REQUERIMENTO = (
    r"(?:pede|pediu|pedir|solicita|solicitou|solicitar|requer|requereu|requerer|"
    r"ajuiza|ajuizou|ajuizar|protocola|protocolou|move|moveu|impetra|impetrou|"
    r"entra\s+na\s+justica|entrou\s+na\s+justica|aciona\s+a\s+justica|"
    r"seeks|files|filed|petitions|petitioned|"
    r"pide|pidio|solicito|demanda|demando)"
)
# "entra na Justiça PARA SOLICITAR falência": ponte entre o verbo de ação e o
# substantivo de insolvência.
_PONTE_REQUERIMENTO = (
    r"(?:\s+(?:para|a\s+fim\s+de|com\s+o\s+objetivo\s+de|buscando|to)"
    r"(?:\s+\w+){0,2})?"
)
# SÓ falência/quiebra/bankruptcy. `recuperacao judicial` fica de fora de
# propósito: "X pede recuperação judicial" é, na esmagadora maioria, o pedido
# da PRÓPRIA empresa — incluí-la apagaria evento legítimo do emissor.
_INSOLVENCIA_REQUERIDA = r"(?:falencia|quiebra|bankruptcy)"
# Marcadores de que o pedido é sobre si mesma — aí o evento É da monitorada.
_AUTOFALENCIA = r"(?:propri[ao]|autofalencia|its\s+own|own\s+bankruptcy)"


def is_monitored_requerente_insolvencia(
        text: str, monitored: str, aliases: list[str] | None = None) -> str:
    """Evidência de que a monitorada REQUER a falência de outrem, ou "".

    Devolve o trecho que sustenta a decisão — nunca um booleano nu — porque a
    rejeição precisa citar o que a motivou. Não adivinha o nome do devedor: a
    conclusão "a monitorada é a requerente, logo não é a falida" independe de
    o requerido estar nomeado no texto disponível.
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"

    # a falência é explicitamente DA monitorada → evento dela, não bloqueia
    if re.search(_INSOLVENCIA_REQUERIDA + r"\s+" + _POSS + r"\s+" + alt, t):
        return ""

    # (a) verbal: "<monitorada> … solicita/entra na Justiça para solicitar falência"
    rx = re.compile(
        alt + r"\b[^.;!?]{0,90}?\b" + _VERBOS_REQUERIMENTO
        + _PONTE_REQUERIMENTO
        + r"\s+(?:a|o|the|la|el|de)?\s*" + _INSOLVENCIA_REQUERIDA)
    m = rx.search(t)
    if not m:
        # (b) nominal: "<monitorada> … pedido de falência CONTRA <alguém>".
        # A preposição adversativa é obrigatória aqui. Sem ela, "entra com
        # pedido de falência" é ambíguo — pode ser autofalência —, e bloquear
        # apagaria evento legítimo do próprio emissor.
        rx_nom = re.compile(
            alt + r"\b[^.;!?]{0,90}?\b"
            r"(?:pedido|requerimento|solicitacao|acao|peticao|petition)"
            r"\s+de\s+" + _INSOLVENCIA_REQUERIDA
            + r"\s+(?:contra|against|em\s+face\s+de|frente\s+a)")
        m = rx_nom.search(t)
    if not m:
        # (c) inglês: "files/seeks (for) bankruptcy AGAINST <alguém>".
        # O `against` é obrigatório porque, em inglês, "X files for bankruptcy"
        # sem preposição adversativa é justamente a autofalência — bloquear
        # ali apagaria o evento próprio do emissor.
        rx_en = re.compile(
            alt + r"\b[^.;!?]{0,90}?\b"
            r"(?:files?|filed|filing|seeks?|sought|petitions?|petitioned)"
            r"(?:\s+for)?\s+" + _INSOLVENCIA_REQUERIDA
            + r"\s+(?:against|of)\b")
        m = rx_en.search(t)
    if not m:
        return ""
    # "pediu a PRÓPRIA falência" continua sendo evento da monitorada
    if re.search(_AUTOFALENCIA, t[m.start():m.end() + 40]):
        return ""
    return re.sub(r"\s+", " ", m.group(0))[:140]


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
    # 4I.2 Wave B7b-5b — ANALISTA/RECOMENDADOR: a monitorada é a FONTE da
    # recomendação sobre o ativo de um TERCEIRO, não sujeito do evento.
    # Vocabulário restrito à construção COMPROVADA no registro real (§4):
    # nome + verbo de recomendação + OBJETO FINANCEIRO (§12). O objeto é
    # exigido de propósito — "{m} recommends" sozinho é ambíguo demais.
    # NÃO inclui `downgrade`/`upgrade`/`rates`/`price target`/`initiates
    # coverage` nem formas PT/ES: nenhum caso real os justifica, e
    # `downgrade` colide com rebaixamento de rating de crédito (§13).
    "analista": [r"{m}\s+recommends?\s+(?:buying|selling)\s+(?:the\s+)?"
                 r"(?:stock|shares|share)\b"],
    # 4I.2 Wave B8 — INDIVIDUAL SUBJECT: o alvo formal do ato é uma PESSOA
    # FÍSICA e a monitorada aparece só como VÍNCULO PROFISSIONAL dela
    # ("processo contra o ex-presidente do conselho DA Vale"). Cargo sozinho
    # nunca basta: exige ATO FORMAL + preposição de ALVO + CARGO + vínculo.
    # Escopo linguístico: PT apenas — única evidência real observada (B8a,
    # N=1). EN/ES ficam para quando houver caso.
    "individual_subject": [
        r"(?:processo|procedimento|inqu[ée]rito|investiga\w*|apura\w*|a[çc][ãa]o)"
        r"(?:\s+\w+){{0,2}}\s+contra\s+"        # ato formal + preposição de alvo
        r"(?:[^,]{{0,40}},\s*)?"                # nome próprio em aposto, opcional
        r"(?:o\s+|a\s+)?ex[- ]?"                # cargo de EX-ocupante
        r"(?:presidente|diretor\w*|conselheir[oa]|executiv[oa]|ceo|chairman|"
        r"gerente|administrador|s[óo]ci[oa]|superintendente)"
        r"(?:\s+\w+){{0,3}}\s+d[aeo]\s+{m}\b",  # vínculo: "… da <monitorada>"
    ],
}

# A monitorada aparece ELA PRÓPRIA como alvo do ato formal. Tem PRECEDÊNCIA
# sobre `individual_subject` (§9): se a companhia é parte, o evento é dela,
# mesmo que um executivo também seja citado. Complementa
# `_INVESTIGACAO_PROPRIA`, que cobre as formas EN/ES e "investiga a X".
_ALVO_E_A_PROPRIA_EMPRESA = [
    r"contra\s+(?:a\s+|o\s+)?{m}\b",
    r"{m}\s+(?:e\s+(?:seus?|suas?)\s+\w+\s+)?(?:s[ãa]o|[ée])\s+(?:alvo|investigad)",
    r"{m}\s+(?:e\s+\w+){{0,3}}\s+s[ãa]o\s+alvo",
]

# Investigação dirigida à PRÓPRIA monitorada. Tem PRECEDÊNCIA sobre o papel
# de analista (§6): sujeito formal explícito vence papel lateral, mesmo que
# a mesma matéria traga uma recomendação de mercado.
_INVESTIGACAO_PROPRIA = [
    r"(?:investigation|probe|inquiry)\s+(?:into|against|of|on)\s+(?:the\s+)?{m}",
    r"{m}\s+(?:faces?|is\s+facing|under)\s+(?:an?\s+)?(?:\w+\s+){{0,2}}"
    r"(?:probe|investigation|inquiry)",
    r"(?:investiga(?:ci[óo]n|[çc][ãa]o))\s+(?:\w+\s+){{0,2}}?(?:contra|sobre|a)\s+{m}",
    r"investiga\s+(?:a\s+|o\s+)?{m}\b",
]


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
    # Padrão de produção, inalterado desde a Wave B4. A R6b mostrou que ele
    # não distingue direção — ver `FRAUDE_VITIMA_SHADOW` logo abaixo —, mas a
    # correção só vale no caminho SHADOW até ser autorizada em produção.
    r"(?:cliente|customer|funcion[áa]rio|employee|ex-funcion[áa]rio)\s+.{{0,60}}?"
    r"(?:fraud\w*|golpe|estafa|lesou|desviou)\s*.{{0,20}}?{a}{m}",
    # ── 4I.2 R2/F3: PAPEL DE PROTETORA ──────────────────────────────────
    # A monitorada é SUJEITO de verbo de combate/prevenção à fraude:
    # "Duke Energy leverages artificial intelligence to COMBAT FRAUD…".
    # Quem combate a fraude não a cometeu. É evidência POSITIVA de papel —
    # nunca ausência de acusação (§12).
    r"{m}{q}(?:\s+\w+){{0,6}}\s+(?:to\s+|para\s+)?(?:combats?|fights?|prevents?|"
    r"tackles?|blocks?|combater|combate|prevenir|previne|proteger|protege|"
    r"coibir|co[íi]be)\s+(?:\w+\s+){{0,2}}(?:fraud|fraude|scam|golpe|estafa|phishing)",
    # ── 4I.2 R2/F3: GOLPE CONTRA A BASE DE CLIENTES ─────────────────────
    # "fraude contra clientes do <X>" / "scam targeting customers of <X>".
    # O alvo econômico é a carteira de clientes, não a monitorada como autora.
    r"(?:fraud|fraude|scam|golpe|estafa|phishing)\s+(?:\w+\s+){{0,2}}?"
    r"(?:against|contra|targeting|dirigid[oa]s?\s+a|voltad[oa]s?\s+contra)\s+"
    r"(?:os\s+|as\s+|the\s+)?(?:customers?|clients?|clientes|usu[áa]rios|"
    r"consumidores|correntistas|assinantes)\s+(?:of\s+|d[oae]s?\s+)?{m}",
]

# ── 4I.2 R6b/R6c: MESMA LISTA, COM DIREÇÃO OBRIGATÓRIA — SÓ NO SHADOW ───────
# O padrão de produção acima aceita "employee committed fraud FOR Company" e
# "employee defrauded Company" como se fossem a mesma coisa: exige apenas um
# funcionário e um termo de fraude perto do nome da empresa. É a PREPOSIÇÃO
# que separa fraude EM NOME da companhia de fraude CONTRA ela.
#
# A correção existe e está testada, mas ativá-la mudaria a classificação de
# todo artigo futuro — e isso ainda não foi autorizado. Por isso ela vive
# nesta lista paralela, usada apenas quando o chamador pede explicitamente o
# caminho shadow. Produção segue byte a byte com o comportamento anterior.
FRAUDE_VITIMA_SHADOW = [
    p for p in FRAUDE_VITIMA
    if not p.startswith(r"(?:cliente|customer|funcion[áa]rio|employee")
] + [
    r"(?:cliente|customer|funcion[áa]rio|employee|ex-funcion[áa]rio)\s+.{{0,60}}?"
    r"(?:fraud\w*|golpe|estafa|lesou|desviou|stole|roubou|embezzl\w*)"
    r"\s*.{{0,20}}?(?:against|from|contra|de|d[ao]s?)\s*{a}{m}",
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
    # 4I.2 R7c-P8 — PROXIMIDADE e HIPOTESE. Caso adjudicado por humano como
    # FALSE_POSITIVE: "Cosan (CSAN3) a beira da falencia? Sera que conseguira
    # se recuperar?" pontuava `falencia` critica para a Cosan. Estar A BEIRA de
    # algo e justamente NAO ter chegado la — mesma familia semantica de "risco
    # de", que este helper ja reconhece. Nao e regra de ponto de interrogacao:
    # o que bloqueia e o modalizador adjacente ao termo do evento, e por isso
    # "X pediu falencia? Documento confirma protocolo" continua pontuando.
    r"[àa]\s+beira\s+d", r"[àa]s\s+portas\s+d", r"pr[óo]xim[ao]\s+d[ao]",
    r"rumo\s+[àa]", r"caminha\s+para", r"beira\s+d[ao]",
    r"eventual\s+", r"eventuais\s+", r"hipot[ée]tic\w*\s+", r"suposta?\s+",
    r"al\s+borde\s+de", r"on\s+the\s+brink\s+of", r"on\s+the\s+verge\s+of",
    r"discute\s+", r"debate\s+",
]
# Mesma ideia, marcador DEPOIS do termo: "falencia a vista", "falencia
# iminente". A ordem inversa e comum em manchete e o padrao prefixado nao a
# alcanca.
MODALIZADOR_PROSPECTIVO_POSFIXO = [
    r"[àa]\s+vista", r"iminente", r"inminente", r"looming",
    r"no\s+horizonte", r"[àa]\s+espreita", r"em\s+discuss[ãa]o",
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
    rx_prosp = re.compile(r"(?:" + "|".join(MODALIZADOR_PROSPECTIVO) + r")\s*(?:\w+\s+){0,2}?(?:"
                          + kw_alt + r")(?!\w)", re.I)
    # e o caso simetrico, com o marcador seguindo o termo ("falencia iminente")
    rx_prosp_pos = re.compile(r"(?<!\w)(?:" + kw_alt + r")(?!\w)\s+(?:\w+\s+){0,1}?(?:"
                              + "|".join(MODALIZADOR_PROSPECTIVO_POSFIXO) + r")", re.I)
    kw_rx = re.compile(r"(?<!\w)(?:" + kw_alt + r")(?!\w)")
    mencoes = neutras = 0
    motivo = ev = ""
    for prop in _proposicoes(t):
        if not kw_rx.search(prop):
            continue
        mencoes += 1
        m = rx_prosp.search(prop) or rx_prosp_pos.search(prop)
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

# ── 4I.2 R4b/B2: CAPITALIZAÇÃO NÃO É PROVA DE ENTIDADE ──────────────────────
# Em manchete, TUDO vem capitalizado. `Vale's Chapter 11`, `Capital One's
# Acquisition Of Discover`, `NextEra Energy's Dominion Acquisition. Virginia
# Governor` — nenhum desses complementos possessivos é uma subsidiária; são
# termo jurídico, substantivo de transação e travessia de fim de frase.
#
# A correção é estrutural e vale para as três construções: a sequência
# capturada é TRUNCADA no primeiro token que pertence a uma classe de
# substantivo comum / verbo — não é uma lista de exceções por caso. Sobrando
# nome, ele é a entidade ("Cigna's Evernorth Completes Acquisition…" →
# `Evernorth`); não sobrando nada, não há subsidiária.
_NAO_ENTIDADE_TOKENS = {
    # termo jurídico / insolvência
    "chapter", "section", "bankruptcy", "insolvency", "restructuring",
    "reorganization", "reorganisation", "filing", "filings", "plan", "petition",
    "case", "lawsuit", "suit", "claim", "ruling", "settlement", "court",
    "recuperacao", "falencia", "concordata", "processo", "acao",
    # papel corporativo
    "ceo", "cfo", "coo", "cto", "chairman", "chairwoman", "board", "president",
    "director", "directors", "management", "shareholder", "shareholders",
    "conselho", "diretoria", "presidente", "acionistas",
    # atributo financeiro
    "rating", "ratings", "debt", "bond", "bonds", "earnings", "results",
    "revenue", "revenues", "profit", "loss", "losses", "share", "shares",
    "stock", "stocks", "outlook", "guidance", "dividend", "credit", "cash",
    "divida", "resultado", "resultados", "lucro", "prejuizo", "acoes",
    # transação / evento
    "acquisition", "merger", "deal", "purchase", "sale", "takeover", "spinoff",
    "ipo", "offering", "migration", "investment", "expansion", "buyback",
    "aquisicao", "fusao", "venda", "compra", "oferta",
    # verbos e particípios que a captura pode engolir
    "completes", "completed", "suspected", "reshapes", "won", "files", "filed",
    "plans", "seeks", "says", "reports", "faces", "wins", "loses", "announces",
}


def _nome_entidade_limpo(cand: str) -> str:
    """Trunca a sequência capturada no primeiro token que não é nome próprio.

    Devolve "" quando nada resta — sinal de que o complemento possessivo era
    um substantivo comum, não uma entidade.
    """
    out = []
    for tok in re.sub(r"\s+", " ", cand or "").strip().split(" "):
        limpo = _n(tok).strip(".,;:'’`")
        if limpo in _NAO_ENTIDADE_TOKENS:
            break
        # citação legal: nome capitalizado seguido de número ("Chapter 11",
        # "Section 363") nunca é razão social.
        if limpo.isdigit():
            break
        out.append(tok)
        if tok.endswith(".") and len(tok.strip(".")) > 2:
            break                      # fim de frase, não abreviação ("St.")
    return re.sub(r"\s+", " ", " ".join(out)).strip(" .,;:")


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


# ── B7b-4b: DIGEST MULTIEMPRESA — ATRIBUIÇÃO LOCAL POR SEGMENTO (4I.2) ──
# Helper NOVO e estreito: `split_clauses` continua intocada porque serve
# `subject_by_possessive` e as Waves A/B com outro contrato (§5).
#
# Delimitador: SOMENTE ';' (§2). ':' NÃO divide — "Vale: companhia anuncia
# novo CEO" é um item só, e o diagnóstico da B7b-4a mediu que ';' sozinho
# resolve 9/9 casos. '|' não foi adicionado porque nenhum caso real
# observado depende dele (§3, sem expansão preventiva).
#
# Escopo do texto: apenas o TÍTULO (§15). O summary do corpus é
# majoritariamente o título reduplicado com o veículo anexado; segmentar a
# concatenação criaria itens sintéticos e falsos negativos. Decisão
# documentada e deliberadamente conservadora.
_DIGEST_DELIM = re.compile(r";")


def split_digest_segments(title: str) -> list[str]:
    """Itens de um digest de mercado, separados por ';'."""
    return [s.strip() for s in _DIGEST_DELIM.split(title or "") if s.strip()]


def _seg_tem_evento(seg_norm: str, kws_norm: list) -> bool:
    return any(k in seg_norm for k in kws_norm)


def _seg_tem_empresa(seg_norm: str, aliases: list) -> bool:
    return any(re.search(r"\b" + re.escape(_n(a)) + r"\b", seg_norm)
               for a in aliases if _n(a))


def detect_evento_de_outro_item(title: str, monitored: str, kws: list,
                                aliases_por_empresa: dict) -> str:
    """Digest: o evento vive num item de OUTRA empresa (Wave B7b-4b).

    Gate de ATRIBUIÇÃO, não classificador (§8): consome os event IDs que o
    pipeline já detectou e o vocabulário da própria taxonomia. Devolve a
    empresa dona do item onde o evento realmente está, ou "".

    Exige TODAS as condições do §7: delimitador presente, ≥2 itens, a
    monitorada identificada num item, o evento AUSENTE de todo item dela, e
    presente num item de outra monitorada.
    """
    segs = split_digest_segments(title)
    if len(segs) < 2:
        return ""
    kws_norm = [_n(k) for k in kws if len(_n(k)) >= 4]
    if not kws_norm:
        return ""
    segn = [_n(s) for s in segs]
    al_m = list(aliases_por_empresa.get(monitored) or [monitored])
    idx_m = [i for i, s in enumerate(segn) if _seg_tem_empresa(s, al_m)]
    if not idx_m:
        return ""
    # Condição 4: se o evento aparece em QUALQUER item da monitorada, é dela.
    if any(_seg_tem_evento(segn[i], kws_norm) for i in idx_m):
        return ""
    # Condição 5: outra monitorada é dona de um item que contém o evento.
    for i, s in enumerate(segn):
        if i in idx_m or not _seg_tem_evento(s, kws_norm):
            continue
        for outra, als in (aliases_por_empresa or {}).items():
            if outra != monitored and _seg_tem_empresa(s, list(als or [outra])):
                return outra
    return ""


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
        # capitalização sozinha não prova entidade (R4b): trunca no primeiro
        # substantivo comum/verbo antes de qualquer outra checagem.
        cand = _nome_entidade_limpo(m.group(1))
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


# ── 4I.2 R6a/F3: PAPEL EXPLÍCITO DE FRAUDE SUPERA FASE ──────────────────────
# Auditado em R5c: os artigos do caso Duke deixavam de pontuar apenas porque
# `allegedly`/`investigation` marcavam a fase como não consumada — o runtime
# seguia com `subject_company = Duke Energy`, ou seja, continuava achando que
# a fraude era DELA. Resultado certo, motivo frágil: bastaria a fraude ser
# confirmada para o evento voltar a pontuar contra a VÍTIMA.
#
# PAPEL e FASE são dimensões diferentes. Quem sofreu a fraude não vira autora
# quando a fraude é comprovada. Por isso o papel explícito passa a ser
# avaliado ANTES da fase, e só com evidência POSITIVA — nunca por ausência de
# acusação.
_FRAUDE_ATOR_EXTERNO = (
    r"(?:suspects?|man|woman|person|people|individuals?|scammers?|fraudsters?|"
    r"hackers?|criminals?|defendants?|golpistas?|estelionat[áa]rios?|"
    r"suspeitos?|acusados?|homem|mulher|quadrilha|terceiros?)")
# Se o ator é gente da própria monitorada, não há vítima: pessoal interno é
# agência da empresa, não terceiro.
_FRAUDE_AGENCIA_PROPRIA = (
    r"(?:executives?|executivos?|employees?|funcion[áa]rios?|staff|agents?|"
    r"agentes?|diretor\w*|director\w*|managers?|gerentes?|s[óo]cios?|"
    r"vendedores?|salespe\w+|officers?)")
_FRAUDE_OBJETO = r"(?:fraud\w*|fraude\w*|scam\w*|golpes?|estafas?|phishing)"

# V1 — a monitorada DESCOBRE / DETECTA / REPORTA a fraude alheia.
_FRAUDE_V1_DESCOBRE = [
    r"{m}{q}(?:\s+" + _FRAUDE_AGENCIA_PROPRIA + r")?\s+"
    r"(?:discovered|detected|identified|uncovered|flagged|reported|"
    r"descobriu|detectou|identificou|constatou|apurou|reportou)"
    r"(?:\s+\w+){{0,8}}\s+" + _FRAUDE_OBJETO,
    r"{m}{q}(?:\s+" + _FRAUDE_AGENCIA_PROPRIA + r")?\s+"
    r"(?:discovered|detected|identified|uncovered|descobriu|detectou|"
    r"identificou|constatou)(?:\s+\w+){{0,6}}\s+"
    r"(?:accounts?|contas?|transactions?|transa[çc][õo]es)"
    r"(?:\s+\w+){{0,8}}?\s*(?:were\s+opened|opened|abertas?|criadas?)",
]
# V2 — prejuízo causado à monitorada por esquema de terceiro.
_FRAUDE_V2_PREJUIZO = [
    r"costing\s+(?:the\s+(?:company|utility|bank|firm)|{m})"
    r"(?:\s+\S+){{0,6}}\s+in\s+(?:losses|damages)",
    r"(?:causou|gerou|provocou)(?:\s+\w+){{0,4}}\s+"
    r"(?:preju[íi]zo|perdas?|dano)(?:\s+\w+){{0,4}}\s+(?:a|à|para|ao)\s+{m}",
]
# P1 — ator externo explicitamente responsável, e o objeto fraudado é da
# monitorada. Nenhuma das metades basta sozinha (§6/§11).
_FRAUDE_P1_ATOR_EXTERNO = [
    _FRAUDE_ATOR_EXTERNO + r"(?:\s+\w+){{0,10}}\s+"
    r"(?:responsible\s+for|charged\s+with|convicted\s+of|arrested\s+for|"
    r"accused\s+of|indicted\s+for|sentenced\s+for|acusad[oa]s?\s+de|"
    r"condenad[oa]s?\s+por)(?:\s+\w+){{0,10}}\s+"
    r"(?:fraudulent\s+{m}|identity\s+theft|stealing|roubo\s+de\s+identidade)",
    r"(?:creating|created|opening|opened|criar|criaram|abrir|abriram)"
    r"(?:\s+\w+){{0,3}}\s+fraudulent\s+{m}\b",
    r"(?:impersonat\w*|posing\s+as|se\s+passa\w*\s+por)(?:\s+\w+){{0,3}}\s*{m}\b",
]
# Guards: a fraude é da CASA. Qualquer um anula o papel de vítima.
_FRAUDE_CASA_PROPRIA = [
    r"{m}{q}\s+(?:own|pr[óo]pri[oa])(?:\s+\w+){{0,3}}\s*" + _FRAUDE_OBJETO,
    r"{m}{q}(?:\s+\w+){{0,3}}\s+" + _FRAUDE_AGENCIA_PROPRIA +
    r"(?:\s+\w+){{0,6}}\s+(?:committed|orchestrat\w*|perpetrat\w*|ran\b|"
    r"cometeram|orquestraram|praticaram|montaram)",
    r"(?:its|seus?|suas?)\s+" + _FRAUDE_AGENCIA_PROPRIA +
    r"(?:\s+\w+){{0,6}}\s+(?:had\s+)?(?:committed|orchestrat\w*|perpetrat\w*|"
    r"cometeram|praticaram)",
    _FRAUDE_AGENCIA_PROPRIA + r"\s+(?:of|d[aeo]s?)\s+{m}"
    r"(?:\s+\w+){{0,6}}\s+(?:committed|orchestrat\w*|perpetrat\w*|cometeu|"
    r"cometeram|praticou|praticaram)",
    r"{m}{q}(?:\s+\w+){{0,4}}\s+(?:participat\w*|particip\w*)"
    r"(?:\s+\w+){{0,4}}\s+" + _FRAUDE_OBJETO,
    # "own fraudulent scheme was uncovered" — o esquema é da própria empresa
    r"(?:its|seus?|suas?)\s+own\s+" + _FRAUDE_OBJETO,
    r"own\s+fraudulent\s+scheme",
]


# ── 4I.2 R6c: SHADOW ≠ PRODUÇÃO ─────────────────────────────────────────────
# A semântica de papel da R6a/R6b está validada, mas ligá-la por padrão faria
# dela o classificador de produção para TODO artigo futuro — e essa é uma
# decisão separada, que ainda não foi tomada. Enquanto isso, ela fica atrás
# de um interruptor explícito: desligado, o pipeline se comporta exatamente
# como antes; ligado, apenas dentro do bloco `with`, os avaliadores shadow
# enxergam a nova semântica.
#
# O padrão é DESLIGADO de propósito. Um flag que precisa ser ligado nunca
# entra em produção por esquecimento; um que precisa ser desligado, sim.
_SHADOW_FRAUD_ROLES = False


def shadow_fraud_roles_ativo() -> bool:
    return _SHADOW_FRAUD_ROLES


@contextlib.contextmanager
def shadow_fraud_roles():
    """Habilita a semântica de papel de fraude SÓ dentro deste bloco."""
    global _SHADOW_FRAUD_ROLES
    anterior = _SHADOW_FRAUD_ROLES
    _SHADOW_FRAUD_ROLES = True
    try:
        yield
    finally:
        _SHADOW_FRAUD_ROLES = anterior


# ── 4I.2 R6d: RESPONSABILIZAÇÃO ADJUDICADA vs RESOLUÇÃO POSTERIOR ───────────
# Um acordo firmado DEPOIS de a empresa ser responsabilizada é o desfecho de
# um fato provado, não a ausência dele. `detect_juridical_phase` já cobre a
# maior parte: achando `condenacao` junto de `encerramento`, a fase mitigadora
# não vence. Medido nesta wave, o contrato falha em dois pontos:
#
#   L4  "admitted fraud and agreed to settle" — o cue de condenação exige
#       `admits?`, que não casa `admitted`. A confissão passa despercebida e
#       o acordo apaga o evento.
#   L8  "Supplier was found liable; Company agreed separately to settle" — a
#       responsabilização é de TERCEIRO e a monitorada herda o evento. É
#       problema de sujeito, não de fase.
#
# As duas correções vivem no caminho SHADOW; produção segue idêntica.
_LIABILITY_ADJUDICADA = (
    r"(?:found\s+liable|held\s+liable|was\s+liable|convicted|conviction|"
    r"pleaded\s+guilty|guilty\s+plea|admitt?(?:ed|s|ing)\s+(?:to\s+)?"
    r"(?:the\s+)?(?:fraud|wrongdoing|guilt)|condenad\w*|"
    r"declarad[oa]\s+culpable|assumiu\s+(?:a\s+)?(?:fraude|culpa))")


def detect_liability_adjudicada(text: str, monitored: str,
                                aliases: list[str] | None = None) -> dict:
    """Responsabilização explícita LIGADA À MONITORADA.

    Devolve `{"cue", "evidence"}` ou `{}`. O vínculo é obrigatório: uma
    condenação de fornecedor citada no mesmo texto não responsabiliza a
    monitorada — é exatamente o caso L8.
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return {}
    alt = "(?:" + "|".join(nomes) + ")"
    ligacoes = [
        # "X was found liable" — sem vírgula entre o nome e o cue, senão o
        # aposto de terceiro ("Supplier, a contractor of X, was found liable")
        # faria X parecer o responsabilizado.
        rf"{alt}\s+(?:\w+\s+){{0,3}}?" + _LIABILITY_ADJUDICADA,
        # "X and Y were found liable" — enumeração conjunta, sem atravessar
        # fim de frase: "X and Y were named. They were found liable" é anáfora
        # e tem tratamento próprio abaixo.
        rf"{alt}\s*,?\s+and\s+[\w\s&\-]{{2,40}}?\s+(?:\w+\s+){{0,3}}?"
        + _LIABILITY_ADJUDICADA,
        # "... and X were found liable"
        rf"\band\s+{alt}\s+(?:\w+\s+){{0,3}}?" + _LIABILITY_ADJUDICADA,
        # "fraud committed by X" / "condenação da X"
        _LIABILITY_ADJUDICADA + rf"(?:\s+\w+){{0,6}}\s+(?:by|d[aeo]s?)\s+{alt}\b",
    ]
    for p in ligacoes:
        m = re.search(p, t)
        if m:
            cue = re.search(_LIABILITY_ADJUDICADA, m.group(0))
            return {"cue": cue.group(0) if cue else "", "evidence": m.group(0)[:140]}
    # Anáfora: "Omnicare and its parent company, CVS Health, … in a case in
    # which THEY were found liable" — o pronome retoma a enumeração anterior.
    # Só vale quando a monitorada aparece ANTES da anáfora e nenhum outro
    # sujeito é responsabilizado entre as duas; é isso que impede o caso do
    # fornecedor condenado de contaminar a empresa que apenas fez acordo.
    ana = re.search(r"\b(?:they|eles|elas|ambas|ambos)\s+(?:\w+\s+){0,2}?"
                    + _LIABILITY_ADJUDICADA, t)
    if ana:
        antes = t[:ana.start()]
        mon = list(re.finditer(alt, antes))
        if mon and not re.search(_LIABILITY_ADJUDICADA, antes[mon[-1].end():]):
            cue = re.search(_LIABILITY_ADJUDICADA, ana.group(0))
            return {"cue": cue.group(0) if cue else "",
                    "evidence": ana.group(0)[:140], "anafora": True}
    return {}


# ── 4I.2 R6e: RESPONSABILIZAÇÃO DE TERCEIRO NÃO TRANSFERE ───────────────────
# O trace do L8 mostrou o problema exato: em "Supplier Alfa was found liable
# for fraud; Vale agreed separately to settle a contract dispute" o pipeline
# reconhece a condenação (EVENT EXISTS) mas nunca decide de QUEM ela é — o
# laço termina com `subject = Vale` só porque a Vale é a única monitorada
# citada, e o evento passa sem regra alguma.
#
# A correção não pode ser negação cega ("a monitorada não está ligada, então
# descarta"): isso apagaria eventos legítimos onde o vínculo simplesmente não
# tem construção reconhecível. Exigimos evidência POSITIVA de que existe OUTRO
# responsabilizado — um sujeito nomeado ou um papel de terceiro imediatamente
# antes do cue de responsabilização.
_TERCEIRO_SUJEITO = (
    r"(?:suppliers?|fornecedor\w*|vendors?|contractors?|distribuidor\w*|"
    r"parceir[oa]s?|partners?|clientes?|customers?|terceir[oa]s?|"
    r"executives?|executivos?|employees?|funcion[áa]rios?|indiv[íi]duos?|"
    r"suspects?|defendants?|r[ée]us?)")


def detect_liability_de_terceiro(text: str, monitored: str,
                                 aliases: list[str] | None = None) -> dict:
    """Existe responsabilização explícita, e o sujeito dela NÃO é a monitorada.

    Devolve `{"terceiro", "cue", "evidence"}` ou `{}`. Só atua quando há um
    responsabilizado IDENTIFICÁVEL diferente da monitorada — ausência de
    vínculo, sozinha, nunca basta para apagar um evento.
    """
    if detect_liability_adjudicada(text, monitored, aliases):
        return {}                       # a monitorada está ligada: não é caso
    tc = _norm_caixa(text)
    t = tc.lower()
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return {}
    alt = "(?:" + "|".join(nomes) + ")"
    for m in re.finditer(_LIABILITY_ADJUDICADA, t):
        antes = t[max(0, m.start() - 90):m.start()]
        # o sujeito é o núcleo nominal imediatamente anterior ao cue
        s = re.search(r"(?:^|[.;:]\s*)([^.;:]{2,80}?)\s*$", antes)
        trecho = (s.group(1) if s else antes).strip()
        # O aposto não muda o sujeito: em "Supplier, a contractor of X, was
        # found liable" quem responde é o Supplier, não X. O núcleo é o que
        # vem antes da primeira vírgula.
        nucleo = trecho.split(",")[0].strip() if "," in trecho else trecho
        if re.search(alt, nucleo):
            continue                    # a monitorada É o sujeito do cue
        if re.search(alt, trecho) and not re.search(_TERCEIRO_SUJEITO, nucleo) \
                and not re.search(r"(?-i:[A-Z])", _norm_caixa(nucleo)):
            continue                    # nome só aparece e não há outro núcleo
        trecho = nucleo
        # o nome do responsabilizado sai do NÚCLEO, nunca da janela bruta —
        # senão o aposto devolveria justamente a monitorada como "terceiro".
        janela_tc = tc[max(0, m.start() - 90):m.start()]
        pos = janela_tc.lower().find(trecho)
        nucleo_tc = janela_tc[pos:pos + len(trecho)] if pos >= 0 else ""
        nome = re.search(r"(?-i:[A-Z][\w&.\-]{2,})(?:\s+(?-i:[A-Z][\w&.\-]{2,}))?",
                         nucleo_tc)
        papel = re.search(_TERCEIRO_SUJEITO, trecho)
        if nome or papel:
            return {"terceiro": (nome.group(0) if nome else papel.group(0))[:60],
                    "cue": m.group(0),
                    "evidence": (trecho + " " + m.group(0))[:140]}
    return {}


def detect_fraud_victim_evidence(text: str, monitored: str,
                                 aliases: list[str] | None = None) -> dict:
    """Evidência POSITIVA de que a monitorada sofreu — não cometeu — a fraude.

    Devolve `{"role", "rule", "evidence"}` ou `{}`. Responsabilização
    explícita vence tudo: companhia condenada não vira vítima por citar
    clientes ou prejuízos. E qualquer marca de fraude praticada por gente da
    própria casa anula o papel.
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return {}
    alt = "(?:" + "|".join(nomes) + ")"
    fmt = dict(m=alt, q=_QUALIF, a=_ART)
    if any(re.search(p.format(**fmt), t, re.I) for p in FRAUDE_AGENTE):
        return {}
    if any(re.search(p.format(**fmt), t, re.I) for p in _FRAUDE_CASA_PROPRIA):
        return {}
    if detect_agencia_em_nome_da_empresa(text, monitored, aliases):
        return {}                     # R6b: agiu PELA empresa, não CONTRA ela
    for regra, padroes in (("R_FRAUDE_VITIMA_DETECTORA", _FRAUDE_V1_DESCOBRE),
                           ("R_FRAUDE_ATOR_EXTERNO", _FRAUDE_P1_ATOR_EXTERNO),
                           ("R_FRAUDE_PREJUIZO_DE_TERCEIRO", _FRAUDE_V2_PREJUIZO)):
        for p in padroes:
            m = re.search(p.format(**fmt), t, re.I)
            if m:
                return {"role": "vitima", "rule": regra,
                        "evidence": m.group(0)[:120]}
    return {}


# ── 4I.2 R6b: AGÊNCIA — FRAUDE *PARA* A EMPRESA NÃO É FRAUDE *CONTRA* ELA ───
# Funcionário, executivo ou preposto são agência da companhia. Quando o texto
# diz que agiram EM NOME dela, a companhia não é a parte lesada — mesmo que a
# frase mencione fraude e o nome dela na mesma oração. É a preposição que
# separa "employee defrauded Company" de "employee committed fraud for
# Company", e o detector precisa enxergá-la.
_AGENTE_DA_CASA = (r"(?:employees?|funcion[áa]rios?|ex-funcion[áa]rios?|"
                   r"executives?|executivos?|officers?|agents?|agentes?|"
                   r"diretor\w*|director\w*|managers?|gerentes?|prepostos?|"
                   r"representantes?|contractors?|s[óo]cios?)")
_AGENCIA_EM_NOME_DA_EMPRESA = [
    # "<agente> committed fraud FOR / ON BEHALF OF <empresa|the firm>"
    _AGENTE_DA_CASA + r"(?:\s+\w+){{0,8}}?\s+"
    r"(?:committed|orchestrat\w*|perpetrat\w*|carried\s+out|ran\b|"
    r"cometeu|cometeram|praticou|praticaram|orquestrou|orquestraram)"
    r"(?:\s+\w+){{0,6}}?\s+(?:for|on\s+behalf\s+of|em\s+nome\s+d[aeo]s?|para)\s+"
    r"(?:the\s+(?:company|firm|group)|a\s+empresa|{m})",
    # "<agente> acting on <empresa> instructions / a mando da <empresa>"
    _AGENTE_DA_CASA + r"(?:\s+\w+){{0,4}}?\s+(?:acting|agindo)"
    r"(?:\s+\w+){{0,4}}?\s+(?:on|sob|a\s+mando\s+d[aeo]s?|por\s+ordem\s+d[aeo]s?)"
    r"\s*(?:the\s+)?{m}",
]


def detect_agencia_em_nome_da_empresa(text: str, monitored: str,
                                      aliases: list[str] | None = None) -> str:
    """O ato foi praticado POR gente da casa e EM NOME da monitorada."""
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    for p in _AGENCIA_EM_NOME_DA_EMPRESA:
        m = re.search(p.format(m=alt, q=_QUALIF, a=_ART), t, re.I)
        if m:
            return m.group(0)[:120]
    return ""


# ── caso NOMEADO pela empresa, autor terceiro ───────────────────────────────
# Lacuna medida no diagnóstico da Duke Energy: "<Empresa> fraud case" é o NOME
# do processo — a empresa qualifica o caso, não o comete. A lista de vítima já
# tinha um padrão para isso, `charged in {m} fraud`, mas ele exige "charged in"
# colado ao nome e escapava de três construções reais:
#   • "charged in THE ONGOING Duke Energy fraud case"  (palavras intercaladas)
#   • "ARRESTED IN Duke Energy fraud case"             (outro verbo)
#   • "WEIGHS IN ON Duke Energy Fraud Case"            (comentário)
#
# A regra exige DUAS evidências, nunca só o nome do caso:
#   (a) a construção atributiva "<Empresa> fraud case/investigation/scheme";
#   (b) um ator TERCEIRO agindo (preso, indiciado, condenado) OU comentário
#       sobre o caso.
#
# Só o nome não basta — seria amplo demais e apagaria fraude real. E é por isso
# que TIM e Citigroup ficam de fora naturalmente: "condena executivos DA TIM"
# nomeia insiders da própria companhia, sem a construção de caso nomeado, e
# "Fraud Showdown Against Citigroup" não tem ator terceiro nem caso nomeado.
_CASO_NOMEADO = (r"{m}{q}\s+(?:fraud|fraude)\s+"
                 r"(?:case|investigation|scheme|probe|caso|investiga[çc][ãa]o)")
# Ator de terceiro: substantivo genérico de pessoa + verbo de responsabilização.
# `suspeito`, `terceiro` e afins nunca designam a própria companhia.
_ATOR_TERCEIRO = (
    r"\b(?:suspects?|suspeit[oa]s?|persons?|man|woman|men|women|individuals?|"
    r"pessoas?|indiv[íi]duos?|customers?|clientes?|employees?|ex-?employees?|"
    r"funcion[áa]ri[oa]s?|ex-?funcion[áa]ri[oa]s?|third\s+part(?:y|ies))\b")
_VERBO_RESPONSABILIZACAO = (
    r"\b(?:arrested|charged|indicted|detained|convicted|sentenced|pleaded|"
    r"pled|apprehended|pres[oa]s?|detid[oa]s?|indiciad[oa]s?|denunciad[oa]s?)\b")
# Comentário/análise sobre o caso: quem comenta não cometeu.
_COMENTARIO_SOBRE_CASO = (
    r"\b(?:weighs?\s+in|weighed\s+in|comments?\s+on|commented\s+on|discusses|"
    r"discussed|analysis\s+of|analyzes|explains|explained|opinion\s+on|"
    r"comenta|analisa|discute|opina)\b")


def is_caso_nomeado_com_autor_terceiro(
        text: str, monitored: str, aliases: list[str] | None = None) -> str:
    """Evidência de que o texto NOMEIA o caso pela empresa e o autor é outro.

    Devolve o trecho que sustenta a decisão, ou "". Exige as duas evidências:
    a construção de caso nomeado E (ator terceiro OU comentário). Só a
    expressão "<Empresa> fraud case" jamais basta.
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"

    m = re.search(_CASO_NOMEADO.format(m=alt, q=_QUALIF), t)
    if not m:
        return ""

    # (b1) ator terceiro responsabilizado, em qualquer ordem na mesma frase
    tem_ator = (re.search(_ATOR_TERCEIRO, t)
                and re.search(_VERBO_RESPONSABILIZACAO, t))
    # (b2) comentário/análise sobre o caso
    tem_comentario = re.search(_COMENTARIO_SOBRE_CASO, t)
    if not (tem_ator or tem_comentario):
        return ""

    # Guarda: se o ator descrito PERTENCE à companhia ("executivos da <M>",
    # "<M> executives"), a responsabilização alcança a casa e não é terceiro.
    if re.search(r"(?:executiv|diretor|director|officer|gerente|manager|"
                 r"funcion[áa]ri)\w*\s+(?:e\s+\w+\s+)?(?:d[aeo]s?|of)\s+" + alt, t):
        return ""
    if re.search(alt + r"\s+(?:executives?|directors?|officers?|managers?)", t):
        return ""

    return re.sub(r"\s+", " ", m.group(0))[:120]


def detect_fraud_role(text: str, monitored: str,
                      aliases: list[str] | None = None) -> str:
    """Papel da monitorada num evento de fraude: "agente", "vitima" ou "".

    O papel de AGENTE vence sempre: um cue incidental de vítima noutra oração
    não pode apagar fraude realmente cometida/condenada pela companhia (§10).
    E fraude praticada por agência da própria casa EM NOME dela nunca produz
    papel de vítima (R6b) — quem agiu pela empresa não a lesou.
    """
    t = _n(text)
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    if any(re.search(p.format(m=alt, q=_QUALIF, a=_ART), t, re.I) for p in FRAUDE_AGENTE):
        return "agente"
    # R6c: guard de agência e lista com direção obrigatória são caminho
    # SHADOW. Sem o interruptor, vale exatamente a lista de produção.
    if _SHADOW_FRAUD_ROLES:
        if detect_agencia_em_nome_da_empresa(text, monitored, aliases):
            return ""
        vitima = FRAUDE_VITIMA_SHADOW
    else:
        vitima = FRAUDE_VITIMA
    if any(re.search(p.format(m=alt, q=_QUALIF, a=_ART), t, re.I) for p in vitima):
        return "vitima"
    # Mesma família — a companhia não é a autora —, mas por outra evidência: o
    # caso leva o nome dela e quem responde é um terceiro. Vem DEPOIS da lista
    # de vítima e DEPOIS do agente, de modo que nada que já decidia muda.
    if is_caso_nomeado_com_autor_terceiro(text, monitored, aliases):
        return "caso_nomeado"
    return ""


# ── 4I.2 R4/F2: FORO JUDICIAL ≠ INSOLVÊNCIA DA MONITORADA ───────────────────
# `Bankruptcy Court` é o NOME DE UM TRIBUNAL. A palavra `bankruptcy` ali
# designa a competência da corte, não um fato da empresa citada — o litígio
# pode ser de privacidade de dados, contrato ou descoberta.
#
# A regra proibida seria `bankruptcy court ⇒ descarta falência`: ela apagaria
# "Bankruptcy Court approves Company's Chapter 11 plan", onde o foro é o mesmo
# mas a insolvência é real. Por isso, duas condições CUMULATIVAS:
#
#   1) TODA ocorrência de termo do evento está DENTRO do nome da instituição;
#   2) nada mais no texto liga insolvência à monitorada.
#
# Basta um termo de insolvência fora do nome do tribunal — ou uma construção
# possessiva/verbal ligando a empresa ao processo — para a regra não atuar.
#
# Escopo lexical DELIBERADAMENTE mínimo: só as construções realmente
# observadas no corpus — `Bankruptcy Court` e `Bankruptcy judge`. Não há
# nenhum caso em PT/ES no histórico ("vara de falências", "tribunal
# concursal"), e naqueles títulos o pipeline sequer chega a criar candidato
# de falência. Expandir sem caso observado é o erro que já custou uma wave.
_FORO_INSOLVENCIA = re.compile(
    r"(?:u\.?s\.?\s+|federal\s+|the\s+)?bankruptcy\s+(?:court|judge)")
# Vocabulário de insolvência que NÃO é keyword de evento mas prova vínculo:
# aparece nos TRUE controls ("Chapter 11 plan", "restructuring plan", "debtor").
_PROVA_INSOLVENCIA_PROPRIA = [
    # o possessivo inglês pode vir como `X's`, `Xs'` ou só `X'` (nome plural)
    r"{m}(?:['’]s|s['’]|['’])?\s+(?:chapter\s+(?:\d+|eleven)|restructuring\s+plan|"
    r"reorganization\s+plan|reorganisation\s+plan|bankruptcy\s+(?:filing|petition|plan)|"
    r"insolvency|plano\s+de\s+recupera[çc][ãa]o)",
    r"(?:chapter\s+(?:\d+|eleven)|restructuring\s+plan|reorganization\s+plan|"
    r"bankruptcy\s+(?:filing|petition|plan)|plano\s+de\s+recupera[çc][ãa]o)\s+"
    r"(?:of|for|d[aeo]s?\s+)\s*{m}",
    r"{m}\s+(?:files?|filed|filing|petitions?|petitioned)\s+for\b",
    # "Vale files Chapter 11 petition in bankruptcy court": o verbo de
    # protocolo liga a empresa ao processo mesmo sem a preposição `for`.
    r"{m}\s+(?:files?|filed|filing|petitions?|petitioned|submits?|submitted)\s+"
    r"(?:\w+\s+){{0,3}}(?:chapter\s+(?:\d+|eleven)|bankruptcy|insolvency|petition)",
    r"{m}\s+(?:enters?|entered|exits?|exited|emerges?|emerged)\s+"
    r"(?:from\s+)?(?:chapter|bankruptcy|insolvency|reorganization)",
    r"\b(?:debtor|devedora)\s+{m}\b",
    r"{m}\s*,?\s+(?:the\s+|as\s+)?debtor\b",
    r"declare?s?\s+{m}\s+(?:bankrupt|insolvent)",
    r"{m}\s+(?:pede|pediu|entra|entrou|obt[êe]m|obteve|protocola|protocolou)\s+"
    r"(?:\w+\s+){{0,2}}(?:recupera[çc][ãa]o|fal[êe]ncia|concordata)",
    r"(?:recupera[çc][ãa]o\s+judicial|fal[êe]ncia|quiebra|concurso\s+de\s+acreedores)"
    r"\s+d[aeo]s?\s+{m}\b",
]


def detect_foro_judicial_sem_insolvencia(text: str, monitored: str,
                                         aliases: list[str] | None = None,
                                         event_kws: list[str] | None = None) -> str:
    """O termo de insolvência só nomeia o TRIBUNAL — não a empresa monitorada.

    Devolve o nome do foro encontrado, ou "". Retorna "" (isto é, mantém o
    evento) assim que houver qualquer termo do evento fora do nome da
    instituição, ou qualquer prova positiva ligando a insolvência à empresa.
    """
    if not event_kws:
        return ""
    t = _n(text)
    foros = [m.span() for m in _FORO_INSOLVENCIA.finditer(t)]
    if not foros:
        return ""
    # (1) nenhum termo do evento pode ocorrer FORA do nome do tribunal
    achou_kw = False
    for kw in {_n(k) for k in event_kws if len(_n(k)) >= 4}:
        for mk in re.finditer(re.escape(kw), t):
            achou_kw = True
            if not any(a <= mk.start() and mk.end() <= b for a, b in foros):
                return ""
    if not achou_kw:
        return ""
    # (2) nenhuma prova positiva de insolvência DA monitorada
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    for p in _PROVA_INSOLVENCIA_PROPRIA:
        if re.search(p.format(m=alt), t):
            return ""
    a, b = foros[0]
    return t[a:b]


# ── 4I.2 R3/F4: AFILIAÇÃO INDIVIDUAL COM OUTRO SUJEITO EXPLÍCITO ────────────
# `ex-CEO de X` identifica uma PESSOA; não torna X sujeito do evento. Mas a
# afiliação sozinha NUNCA basta — "Ex-CEO da X afirma que X entrou em default"
# continua sendo evento de X. Por isso duas condições CUMULATIVAS:
#
#   1) a monitorada aparece EXCLUSIVAMENTE dentro da construção de afiliação;
#   2) o termo do evento está preso a OUTRO sintagma nominal.
#
# É a condição (1) que protege os contraexemplos: se a empresa reaparece fora
# do aposto, ela volta a ser candidata a sujeito e o gate não atua.
_AFILIACAO_INDIVIDUAL = (
    r"(?:ex[-\s]?|former\s+|antigo\s+)"
    r"(?:ceo|presidente|president|chairman|diretor\w*|director\w*|executiv[oa]s?|"
    r"executive|conselheir[oa]|gerente|superintendente)\s*"
    r"(?:d[aeo]s?\s+|of\s+|del\s+)")
# Substantivos de COMPANHIA genérica — reconhecem o terceiro sujeito mesmo
# quando ele não é entidade nomeada nem está na watchlist ("una petrolera").
_SUJEITO_GENERICO = (r"petrolera|petroleira|empresa|compa[ñn]ia|companhia|firma|"
                     r"grupo|fabricante|varejista|banco|operadora|construtora|"
                     r"mineradora|siderurgica|company|firm")
# Só determinante INDEFINIDO conta. "una petrolera en default" INTRODUZ um
# referente novo; "the company may default" é anáfora — retoma a própria
# monitorada e não prova sujeito distinto.
_DET_INDEFINIDO = r"(?:un|uno|una|um|uma|uns|umas|unos|unas|an?|another|outr[oa]|otr[oa])\s+"
# Classificador comum antes de nome próprio — "supplier Beta", "fornecedora
# Alfa". Assim como o determinante indefinido, INTRODUZ um referente novo; um
# aposto de afiliação ("ex-CEO da Vale Fulano") nunca tem classificador.
_CLASSIFICADOR = (_SUJEITO_GENERICO +
                  r"|supplier|fornecedor[ae]?|distribuidora|transportadora|"
                  r"concorrente|rival|parceira|cliente|subsidiaria|controlada")
# Aposto ("ex-CEO da YPF Fulano de Tal") nunca atravessa pontuação forte; a
# vírgula fica de fora justamente porque aposto usa vírgula.
_FRONTEIRA_FORTE = re.compile(r"[.;:!?\"“”«»]|\s[-–—]\s")


def _norm_caixa(s: str) -> str:
    """Mesma normalização de `_n`, preservando a caixa — os offsets coincidem
    com os de `_n`, o que permite ler maiúsculas sem desalinhar índices."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def detect_individual_affiliation_role(text: str, monitored: str,
                                       aliases: list[str] | None = None,
                                       event_kws: list[str] | None = None) -> str:
    """A monitorada só nomeia a AFILIAÇÃO de uma pessoa e o evento é de outro.

    Devolve a evidência do outro sujeito, ou "". Não decide por distância
    (§21): o sujeito é o núcleo nominal que ANTECEDE o termo do evento, e ele
    só conta como prova se for (a) um nome próprio separado da monitorada por
    pontuação forte, ou (b) um substantivo de companhia com determinante
    indefinido. Qualquer outra coisa devolve "" e o evento permanece.
    """
    if not event_kws:
        return ""
    tc = _norm_caixa(text)
    t = tc.lower()
    nomes = [re.escape(_n(a)) for a in ((aliases or []) + [monitored]) if a]
    if not nomes:
        return ""
    alt = "(?:" + "|".join(nomes) + ")"
    ocorr = list(re.finditer(alt, t))
    if not ocorr:
        return ""
    # (1) toda ocorrência da monitorada tem de vir logo após a afiliação
    for m in ocorr:
        if not re.search(_AFILIACAO_INDIVIDUAL + r"$", t[max(0, m.start() - 60):m.start()]):
            return ""
    # (2) o termo do evento tem de estar preso a OUTRO núcleo nominal
    nucleo = re.compile(r"(?:(?i:" + _DET_INDEFINIDO + r")(?P<gen>(?i:" + _SUJEITO_GENERICO + r"))\b"
                        r"|(?P<nome>[A-Z][\w\-]{2,}))"
                        r"(?:\s+\w+){0,4}[\s\"“”]*$")
    classif = re.compile(r"(?i:" + _CLASSIFICADOR + r")\s+$")
    for kw in sorted({_n(k) for k in event_kws if len(_n(k)) >= 4}):
        for mk in re.finditer(re.escape(kw), t):
            trecho = tc[:mk.start()]
            cand = None
            for i in range(len(trecho)):         # o núcleo MAIS PRÓXIMO vence
                m2 = nucleo.match(trecho, i)
                if m2:
                    cand = m2
            if not cand:
                continue
            if cand.group("gen"):
                return cand.group("gen")
            nome = cand.group("nome")
            if re.fullmatch(alt, nome.lower()):
                continue                        # o núcleo é a própria monitorada
            pos = cand.start("nome")
            # classificador antes do nome já basta: "supplier Beta" introduz
            # referente novo e nenhum aposto de afiliação tem classificador.
            mc = classif.search(trecho[:pos])
            if mc:
                return f"{mc.group(0).strip()} {nome}"
            # nome próprio sem classificador só prova sujeito distinto se não
            # for aposto da afiliação: exige pontuação forte entre ele e toda
            # menção à monitorada.
            if all(_FRONTEIRA_FORTE.search(tc[min(pos, m.start()):max(pos, m.start())])
                   for m in ocorr):
                return nome
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
        if not any(re.search(p.format(m=alt), t, re.I) for p in pats):
            continue
        # 4I.2 Wave B7b-5b §6: o papel de analista é FALLBACK. Se o texto diz
        # explicitamente que a monitorada é a investigada, o sujeito formal
        # vence e o evento continua dela.
        if papel == "analista" and any(
                re.search(p.format(m=alt), t, re.I) for p in _INVESTIGACAO_PROPRIA):
            continue
        # 4I.2 Wave B8 §9: mesma precedência para o alvo individual — se a
        # companhia também é alvo formal, o evento continua dela.
        if papel == "individual_subject" and any(
                re.search(p.format(m=alt), t, re.I)
                for p in _INVESTIGACAO_PROPRIA + _ALVO_E_A_PROPRIA_EMPRESA):
            continue
        return papel
    return ""


# ── 4I.2 R7c-P2 — insolvência setorial ou de terceiro ───────────────────────
# Caso adjudicado por humano: "Banco do Brasil (BBAS3) em alerta: Pedidos de
# recuperação judicial no agronegócio saltam 22%, aponta Serasa" pontuava
# `recuperacao_judicial` PARA O BANCO. A recuperação é do setor; o banco é o
# credor exposto, citado no contexto.
#
# O defeito não está no título: é o de sempre — a palavra vira candidato e o
# evento pontua por AUSÊNCIA de blocker. `detect_debtor_subject` devolve vazio
# tanto aqui quanto em "X pede recuperação judicial", então nada distinguia os
# dois. A correção exige EVIDÊNCIA POSITIVA de que a monitorada é a devedora.
#
# Generaliza para insolvência de escopo setorial/coletivo — não conhece banco,
# agronegócio, veículo nem ticker.
# Singular E plural: "recuperações judiciais no varejo" é justamente a forma
# em que a notícia setorial aparece, e a versão só-singular deixava passar.
# Baldes que nao sao emissores: agregam noticia de mercado, nunca sao sujeito.
_NAO_EMISSOR = frozenset({"mercado (geral)", "mercado geral", "mercado"})

_INSOLV_ESTRITA = (r"(?:recupera[çc](?:[ãa]o|[õo]es)\s+judicia(?:l|is)|"
                   r"fal[êe]nci\w*|concurso\s+de\s+acreedores|quiebra|"
                   r"chapter\s*11|insolvenc\w*|bankruptc\w*)")

# A monitorada É a devedora: possessivo, verbo próprio ou pedido nomeado.
_INSOLV_SUJEITO_PROPRIO = [
    _INSOLV_ESTRITA + r"\s+d[aeo]s?\s+(?:{m})\b",
    r"(?:{m})\s+(?:\w+\s+){{0,2}}?(?:pede|pediu|pedir[áa]|entra|entrou|"
    r"solicita|solicitou|requer|requereu|ajuiza|ajuizou|protocola|protocolou)"
    r"\s+(?:\w+\s+){{0,3}}?" + _INSOLV_ESTRITA,
    r"(?:{m})\s+(?:est[áa]|entrou|permanece|segue|encontra-se)\s+"
    r"(?:\w+\s+){{0,2}}?em\s+" + _INSOLV_ESTRITA,
    r"(?:pedido|plano|processo)\s+de\s+" + _INSOLV_ESTRITA +
    r"\s+d[aeo]s?\s+(?:{m})\b",
    r"(?:{m})\s+(?:\w+\s+){{0,3}}?(?:decretad\w*|declarad\w*)\s+"
    r"(?:\w+\s+){{0,2}}?" + _INSOLV_ESTRITA,
]

# O sujeito é COLETIVO ou terceiro: setor, classe de empresas, clientes,
# fornecedores, produtores. "Pedidos de RJ no agronegócio", "RJ de clientes".
_INSOLV_SUJEITO_COLETIVO = [
    r"(?:pedidos?|processos?|casos?|n[úu]mero)\s+de\s+" + _INSOLV_ESTRITA,
    _INSOLV_ESTRITA + r"\s+(?:no|na|nos|nas|em|do|da|dos|das)\s+"
    r"(?:setor|segmento|mercado|ind[úu]stria|agroneg[óo]cio|varejo|"
    r"com[ée]rcio|constru[çc][ãa]o|pa[íi]s|regi[ãa]o|estado)",
    _INSOLV_ESTRITA + r"\s+d[aeo]s?\s+(?:clientes?|fornecedores?|produtores?|"
    r"parceir\w+|devedores?|tomadores?|contrapartes?|empresas?|companhias?|"
    r"varejistas?|construtoras?|usinas?)",
    r"(?:empresas?|companhias?|clientes?|produtores?|fornecedores?)\s+"
    r"(?:\w+\s+){0,2}?em\s+" + _INSOLV_ESTRITA,
    r"(?:aumento|alta|salto|crescimento|avan[çc]o|onda|disparam?|saltam?)"
    r"(?:\s+\w+){0,4}?\s+" + _INSOLV_ESTRITA,
    # Plural nu, sem nenhuma entidade ligada: "recuperações judiciais crescem",
    # "as falências no país". O plural já indica classe, não uma empresa.
    r"(?:recupera[çc][õo]es\s+judiciais|fal[êe]ncias)",
]

# Papel da monitorada quando o sujeito é coletivo: exposta, credora, provisiona,
# acompanha, é fonte do dado. Nenhum deles é "estar em recuperação judicial".
_INSOLV_PAPEL_EXPOSTO = [
    r"(?:{m})\s*(?:[\w()\[\]:,.-]+\s+){{0,3}}?(?:em\s+alerta|em\s+aten[çc][ãa]o|"
    r"monitora|acompanha|avalia|analisa|projeta|estima|aponta|revela|"
    r"calcula|divulga|informa)",
    r"(?:{m})\s*(?:[\w()\[\]:,.-]+\s+){{0,3}}?(?:provis\w+|provisiona|reserva|"
    r"exposi[çc][ãa]o|expost\w+|credor\w*|carteira)",
    r"(?:segundo|conforme|de\s+acordo\s+com|aponta|diz|informa)\s+"
    r"(?:a\s+|o\s+)?(?:{m})\b",
]


def detect_insolvencia_setorial(text: str, monitored: str,
                                aliases: list | None = None) -> dict:
    """A insolvência é do SETOR/de terceiros, não da monitorada.

    Só bloqueia quando NÃO há evidência positiva de que a monitorada é a
    devedora. Assim "X pede recuperação judicial", "recuperação judicial da X",
    "plano de recuperação judicial da X" e "X está em recuperação judicial"
    seguem pontuando — exige-se sujeito, não se apaga a família."""
    # O balde de notícia de mercado NÃO é um emissor: ali o sujeito nunca é a
    # "empresa monitorada", e aplicar a regra apagaria o feed inteiro. Medido
    # no blast: 24 dos 25 pares alterados eram desse balde, incluindo
    # "Hughes pede recuperação judicial" — evento real de terceiro, que é
    # justamente o que esse agrupamento existe para mostrar.
    if _n(monitored) in _NAO_EMISSOR:
        return {}
    t = _n(text)
    alt = "(?:" + "|".join(re.escape(_n(a)) for a in
                           ((aliases or []) + [monitored]) if a) + ")"
    for p in _INSOLV_SUJEITO_PROPRIO:
        if re.search(p.format(m=alt), t, re.I):
            return {}
    coletivo = ""
    for p in _INSOLV_SUJEITO_COLETIVO:
        m = re.search(p, t, re.I)
        if m:
            coletivo = m.group(0).strip()
            break
    if not coletivo:
        return {}
    papel = ""
    for p in _INSOLV_PAPEL_EXPOSTO:
        m = re.search(p.format(m=alt), t, re.I)
        if m:
            papel = m.group(0).strip()
            break
    return {"sujeito_coletivo": coletivo[:90], "papel_monitorada": papel[:90],
            "evidence": coletivo[:160]}


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
            # 4I.2 R7c-P2 — insolvência SETORIAL/de terceiro. Precedência
            # DELIBERADAMENTE MÍNIMA: só atua quando nenhuma regra de terceiro
            # NOMEADO atuou. Rodando antes, roubava o caso do comunicado sobre
            # terceiro — que dá um sujeito melhor, com entidade nomeada, e o
            # `test_semantica` acusou na hora. Aqui o sujeito é um COLETIVO
            # ("pedidos de RJ no agronegócio"), que nenhuma das regras acima
            # procura, e por isso o evento pontuava por ausência de blocker.
            if not terceiro:
                _set = detect_insolvencia_setorial(
                    texto, monitored,
                    aliases_por_empresa.get(monitored) or [monitored])
                if _set:
                    d.update(subject_company=_set["sujeito_coletivo"],
                             scoreable=False, event_scope="indireto",
                             relation_type="setorial_ou_terceiro",
                             subject_evidence=_set["evidence"],
                             direction="neutra",
                             attribution_rule="R_INSOLVENCIA_SETORIAL_OU_DE_TERCEIRO",
                             rejection_reason=(
                                 f"a insolvência é de "
                                 f"{_set['sujeito_coletivo']!r}; {monitored} "
                                 f"aparece como parte exposta/citada"
                                 + (f" ({_set['papel_monitorada']})"
                                    if _set["papel_monitorada"] else "")),
                             attribution_confidence="alta")
                    decisoes.append(d)
                    continue
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
        # 3b) VENDEDORA DA TRANSAÇÃO (4I.2 Wave C1)
        # Roda ANTES do bloco de M&A: se a monitorada é quem vende, o evento
        # não é dela, qualquer que seja o objeto. Cobre `ma` e `follow_on` —
        # G245 provou que a mesma estrutura S3 aparece nas duas famílias.
        # NÃO cria event_id novo: a lacuna de desinvestimento segue aberta.
        if ev in EVENTOS_MA or ev == "follow_on":
            _vend = detect_transaction_seller_role(
                texto, monitored, aliases_por_empresa.get(monitored) or [monitored])
            if _vend:
                d.update(scoreable=False, event_scope="direto",
                         relation_type="vendedora",
                         attribution_rule="R_MA_PAPEL_VENDEDOR",
                         rejection_reason=(f"{monitored} é a VENDEDORA nesta transação; "
                                            f"{ev} pertence ao comprador/emissor "
                                            f"(\"{_vend}\")"))
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
            # 4I.2 R6a/F3 — PAPEL EXPLÍCITO ANTES DA FASE.
            # Se há evidência positiva de que a monitorada SOFREU a fraude, o
            # evento não é dela — e isso não pode depender de `allegedly` nem
            # de a investigação estar em curso. Fase decide se um fato está
            # confirmado; papel decide de QUEM é o fato.
            # R6c: caminho SHADOW. Em produção este gate não roda, e o bloco
            # de fraude decide como antes, pela fase.
            _vit = (detect_fraud_victim_evidence(
                texto, monitored, aliases_por_empresa.get(monitored) or [monitored])
                if _SHADOW_FRAUD_ROLES else {})
            if _vit:
                d.update(scoreable=False, event_scope="direto",
                         relation_type="vitima_de_fraude",
                         subject_company=monitored,
                         subject_evidence=_vit["evidence"],
                         attribution_rule=_vit["rule"],
                         attribution_confidence="alta",
                         rejection_reason=(
                             f"{monitored} é a parte LESADA, não a autora — "
                             f"evidência: “{_vit['evidence']}”"))
                decisoes.append(d)
                continue
            # 4I.2 R6d — RESPONSABILIZAÇÃO ADJUDICADA VENCE RESOLUÇÃO POSTERIOR.
            # Caminho SHADOW. Um acordo posterior é o desfecho de um fato
            # provado, não a ausência dele — mas só quando a responsabilização
            # está ligada À MONITORADA. Liability de terceiro citada no mesmo
            # texto não transfere o evento.
            _al_f = aliases_por_empresa.get(monitored) or [monitored]
            # 4I.2 R6e — responsabilização de TERCEIRO não transfere.
            # Caminho SHADOW. Sem evidência de outro responsabilizado
            # identificável, nada é descartado: ausência de vínculo sozinha
            # nunca apaga evento.
            _terc = (detect_liability_de_terceiro(texto, monitored, _al_f)
                     if _SHADOW_FRAUD_ROLES else {})
            if _terc:
                d.update(scoreable=False, event_scope="indireto",
                         relation_type="terceiro_responsabilizado",
                         subject_company=_terc["terceiro"],
                         subject_evidence=_terc["evidence"],
                         attribution_rule="R_LIABILITY_DE_TERCEIRO",
                         attribution_confidence="alta",
                         rejection_reason=(
                             f"quem foi responsabilizado é '{_terc['terceiro']}' "
                             f"(“{_terc['cue']}”); {monitored} não herda a fraude"))
                decisoes.append(d)
                continue
            _liab = (detect_liability_adjudicada(texto, monitored, _al_f)
                     if _SHADOW_FRAUD_ROLES else {})
            if _liab and fase["direction"] == "mitigadora":
                d.update(scoreable=True, event_scope="direto",
                         subject_company=monitored,
                         subject_evidence=_liab["evidence"],
                         attribution_rule="R_LIABILITY_VENCE_RESOLUCAO",
                         attribution_confidence="alta",
                         confirmation_level="confirmado",
                         rejection_reason=(
                             f"responsabilização explícita (“{_liab['cue']}”) precede a "
                             f"resolução {fase['event_phase']}; o acordo encerra um fato "
                             f"provado, não o apaga"))
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
            # A monitorada REQUER a falência de outrem. Vem depois do detector
            # possessivo de propósito: quando o devedor está nomeado, aquele
            # bloco já resolve e diz QUEM é. Esta regra cobre o caso em que o
            # requerido não aparece no texto — e mesmo assim o polo ativo é
            # inequívoco. `subject_company` fica vazio porque não sabemos o
            # nome do falido, e inventá-lo seria pior do que admitir a lacuna.
            _req = is_monitored_requerente_insolvencia(texto, monitored, _al)
            if _req:
                d.update(scoreable=False, event_scope="indireto",
                         # `subject_company` volta a VAZIO: o padrão do bloco é
                         # a própria monitorada, e deixá-lo assim registraria
                         # que ela é o sujeito da falência — exatamente a
                         # atribuição que esta regra existe para negar. Vazio
                         # diz a verdade: sabemos que não é ela, não sabemos
                         # quem é.
                         subject_company="",
                         relation_type="credor_requerente",
                         attribution_rule="R_REQUERENTE_DE_FALENCIA_NAO_E_O_FALIDO",
                         rejection_reason=(f"{monitored} REQUER a falência "
                                           f"(\"{_req}\"); o falido é o requerido, "
                                           f"não o requerente"))
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
            if _fr == "caso_nomeado":
                _ev = is_caso_nomeado_com_autor_terceiro(texto, monitored, _al)
                d.update(scoreable=False, event_scope="indireto",
                         # `subject_company` vazio: sabemos que NÃO é a
                         # monitorada; quem é o autor o texto não nomeia.
                         subject_company="",
                         relation_type="entidade_que_nomeia_o_caso",
                         attribution_rule="R_CASO_NOMEADO_NAO_IMPUTA_AUTORIA",
                         rejection_reason=(f"\"{_ev}\" nomeia o processo; quem responde "
                                           f"é terceiro, não {monitored}"))
                decisoes.append(d)
                continue
        # 2h) PAPEL NÃO-SUJEITO: vítima / comentarista / investigador (Wave A7)
        _papel = detect_papel_nao_sujeito(texto, monitored, _al)
        # 4I.2 Wave B8 §8: o papel `individual_subject` só vale para a família
        # onde o caso foi observado. Fraude, condenação, prisão e M&A têm
        # semântica de responsabilização corporativa própria e ficam de fora
        # até haver evidência real — menor blast radius vence.
        if _papel == "individual_subject" and ev != "investigacao_regulatoria":
            _papel = ""
        # 4I.2 R4/F2: o termo de insolvência só nomeia o TRIBUNAL.
        # Escopo restrito a `falencia`/`recuperacao_judicial` — são os únicos
        # eventos cuja keyword aparece em nome de instituição judicial (§11:
        # nada de reescrever o contrato de insolvência inteiro nesta wave).
        if not _papel and ev in ("falencia", "recuperacao_judicial"):
            _foro = detect_foro_judicial_sem_insolvencia(
                texto, monitored, _al, (keywords_por_evento or {}).get(ev) or [])
            if _foro:
                d.update(scoreable=False, event_scope="direto",
                         relation_type="foro_judicial",
                         subject_company=monitored,
                         subject_evidence=_foro,
                         attribution_rule="R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA",
                         rejection_reason=(
                             f"'{_foro}' é o FORO; nada no texto liga insolvência "
                             f"a {monitored}"))
                decisoes.append(d)
                continue
        # 4I.2 R3/F4: afiliação individual + OUTRO sujeito explícito do evento.
        # Escopo restrito às famílias com caso real observado (§24); as demais
        # ficam de fora até haver evidência.
        if not _papel and ev in ("falencia", "default", "investigacao_regulatoria"):
            _outro = detect_individual_affiliation_role(
                texto, monitored, _al, (keywords_por_evento or {}).get(ev) or [])
            if _outro:
                d.update(scoreable=False, event_scope="indireto",
                         relation_type="afiliacao_individual",
                         subject_company=_outro,
                         attribution_rule="R_AFILIACAO_INDIVIDUAL",
                         rejection_reason=(
                             f"{monitored} aparece apenas como afiliação de uma pessoa; "
                             f"o {ev} é de '{_outro}'"))
                decisoes.append(d)
                continue
        if _papel:
            d.update(scoreable=False, event_scope="indireto",
                     relation_type=f"papel_{_papel}",
                     attribution_rule="R_PAPEL_NAO_SUJEITO",
                     rejection_reason=(f"{monitored} atua como {_papel} neste texto, "
                                        f"não como sujeito do evento"))
            decisoes.append(d)
            continue
        # B7b-4b) DIGEST MULTIEMPRESA: O EVENTO É DE OUTRO ITEM (4I.2)
        # Precedência DELIBERADAMENTE MÍNIMA (§6): roda por último, depois de
        # todas as regras semânticas. Qualquer regra que já tenha provado a
        # relação (possessivo, sujeito estrito, M&A, papel) sai antes com
        # `continue` e nunca é atropelada por localidade textual.
        # Opera por EMPRESA × EVENTO: desarma só este evento para esta
        # monitorada — a dona do item continua recebendo o seu.
        if _kws:
            _dono = detect_evento_de_outro_item(title, monitored, _kws,
                                                aliases_por_empresa)
            if _dono:
                d.update(subject_company=_dono, scoreable=False,
                         event_scope="indireto", relation_type="outro_item_do_digest",
                         attribution_rule="R_EVENTO_DE_OUTRO_ITEM_DO_DIGEST",
                         rejection_reason=(f"o evento pertence ao item da '{_dono}'; "
                                            f"{monitored} aparece em outro item do digest"))
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
