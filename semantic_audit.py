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
FASES_JURIDICAS = [
    ("encerramento", [r"encerra\w*\s+(?:a\s+)?a[çc][ãa]o", r"encerramento\s+d[ao]",
                      r"p[õo]e\s+fim", r"quita[çc][ãa]o", r"extin[çc][ãa]o\s+d[ao]\s+processo",
                      r"encerra\s+processo", r"settles?\b", r"settlement"], "mitigadora"),
    ("acordo", [r"acordo\s+(?:judicial|com\s+o\s+minist[ée]rio|de\s+leni[êe]ncia)",
                r"faz\s+acordo", r"celebra\s+acordo", r"plea\s+agreement"], "mitigadora"),
    ("pagamento", [r"paga\s+r\$", r"pagamento\s+de\s+r\$", r"pays?\s+\$",
                   r"desembolsa"], "mitigadora"),
    ("absolvicao", [r"absolvi\w*", r"acquitted", r"inocentad"], "mitigadora"),
    ("arquivamento", [r"arquiva\w*", r"dismissed", r"shelved"], "mitigadora"),
    ("prescricao", [r"prescri[çc][ãa]o", r"prescrito"], "mitigadora"),
    ("anulacao", [r"anula\w*", r"overturn\w*", r"revers[ãa]o\s+d[ae]\s+condena"], "mitigadora"),
    ("condenacao", [r"condena\w*", r"convicted", r"sentenc[ei]ad"], "negativa"),
    ("acusacao_formal", [r"den[úu]ncia\s+(?:formal|do\s+mp|oferecida)",
                         r"indict\w*", r"formalmente\s+acusad", r"a[çc][ãa]o\s+penal"], "negativa"),
    ("investigacao", [r"investiga[çc][ãa]o", r"apura[çc][ãa]o", r"opera[çc][ãa]o\s+d[ao]\s+pf",
                      r"investigation", r"probe", r"inqu[ée]rito"], "negativa"),
    ("alegacao", [r"den[úu]ncia\s+exclusiva", r"acusa\w*", r"suspeita",
                  r"alleged", r"teria\s+", r"segundo\s+denuncia"], "negativa"),
]


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


def resolve_article_semantics(title: str, summary: str, monitored: str,
                              event_ids: list[str], aliases_por_empresa: dict,
                              *, article_year: int | None = None,
                              source_domain: str = "") -> dict:
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
                d.update(scoreable=False, attribution_rule="R_MA_OBJETO_ESCOPO",
                         rejection_reason=motivo,
                         event_id_corrigido=novo or "",
                         direction=("positiva" if novo in ("recompra_acoes",
                                                           "integracao_pos_aquisicao")
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
                d.update(attribution_confidence="baixa",
                         attribution_rule="R_FRAUDE_NAO_CONFIRMADA",
                         rejection_reason="alegação/denúncia sem confirmação formal")
        decisoes.append(d)
    return {"decisoes": decisoes, "historico": hist, "transacao": trans,
            "fase": fase, "papeis": papeis, "rating_colapso": colapso}


# ═══════════════════════════════════════════════════════════════════════
# INTEGRAÇÃO COM O PIPELINE DE PRODUÇÃO
# ═══════════════════════════════════════════════════════════════════════
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
                                      source_domain=rec.get("domain", "") or "")
        manter = []
        for d in r["decisoes"]:
            todas.append(d)
            ev = d["event_id"]
            # idempotência: substitui a avaliação anterior da mesma
            # (empresa, evento) em vez de acumular duplicatas a cada execução
            assessments[:] = [x for x in assessments
                              if not (x.get("company") == empresa
                                      and x.get("event_id") == ev
                                      and x.get("assessed_by") == "semantic_audit")]
            assessments.append({
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
            })
            if d["scoreable"]:
                manter.append(ev)
                continue
            mudou = True
            corrigido = d.get("event_id_corrigido") or ""
            # evento de TERCEIRO ou reclassificação informativa → contexto
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
