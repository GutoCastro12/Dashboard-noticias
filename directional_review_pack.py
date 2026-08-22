# -*- coding: utf-8 -*-
"""DIRECTIONAL HUMAN LABELING PACK — BATCH D1.

Monta um pacote pequeno e estratificado para que o humano crie VERDADE
DIRECIONAL real antes de qualquer nova chamada de modelo.

O que este modulo NAO faz, por contrato:

  * nao chama modelo, nao acessa rede, nao faz backfill;
  * nao escreve em `risk_history.json`, `config_risco.yaml`,
    `risk_human_supervision.json` nem em `occurrence_truth`;
  * nao emite rotulo humano nenhum — as celulas de rotulo saem VAZIAS;
  * nao mostra previsao de modelo, para nao contaminar o julgamento (S15).

A unidade e a OCORRENCIA. Duas fontes corroborando o mesmo fato nao viram
dois votos direcionais.

DEFEITO CORRIGIDO AQUI (S2 re-medicao): o manifesto direcional de
`reliability_model_benchmark` era montado de `linha["events"]`, que guarda um
evento por FAMILIA por empresa (o `event_id` e o proprio nome da familia).
Isso colapsava ocorrencias distintas da mesma familia — a JBS perdia a
aquisicao dos 18% da Pilgrim's, que sozinha responde por ~40% do score dela.
A superficie correta e o `breakdown` (o que de fato pontua), religado a
ocorrencia pelo agrupador. Producao sempre pontuou as duas corretamente;
quem amostrava errado era o benchmark.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict

import occurrence_engine as oe
import risk_dashboard as rd
import semantic_v2_shadow as _sv2

PACK_VERSION = "directional.pack.d1"

AUTORIDADE = {
    "production_score_authority": "NONE",
    "production_occurrence_authority": "NONE",
    "semantic_authority": "NONE",
    "human_truth_write_authority": "NONE",
    "model_predictions_included": False,
    "human_labels_prefilled": False,
    "output_label": "HUMAN REVIEW PACK — LABELS PENDING",
}

ROTULOS = ("ADVERSE", "FAVORABLE", "NEUTRAL", "MIXED", "UNCERTAIN")

EVIDENCIA_INSUFICIENTE = "LOCAL_EVIDENCE_INSUFFICIENT"
NAO_ELEGIVEL = "NOT_ELIGIBLE_FOR_DIRECTIONAL_TRUTH"

# S25 — casos cuja incerteza humana subjacente ainda importa. Ficam fora do
# D1: rotular direcao sobre eles misturaria duas perguntas diferentes.
EXCLUIR_D1 = {"Capital One": "UNDETERMINED em Batch V1",
              "Copel": "POLICY_PENDING em Batch V1"}

# S17 — familias contextuais que o pacote precisa cobrir para que o proximo
# benchmark consiga distinguir MATERIALIDADE de ADVERSIDADE.
FAM_CONTEXTUAIS = ("ma", "troca_ceo", "emissao_divida", "follow_on")

# S6/S8/S9/S10/S11/S12 — controles que a onda nomeia explicitamente. Entram
# ANTES de qualquer criterio de contribuicao: se o corte por peso os deixasse
# de fora, o pacote perderia justamente os casos que o humano pediu. O campo
# `n` diz quantas ocorrencias daquela (empresa, familia) levar; None = todas.
OBRIGATORIOS = (
    # controles adversos obvios (S6)
    ("Tok&Stok", "recuperacao_judicial", None, "S6 controle adverso"),
    ("Cosan", "rebaixamento_rating", 2, "S6 controle adverso"),
    ("B3", "suspensao_negociacao", None, "S6 controle adverso"),
    ("Rumo", "rebaixamento_rating", None, "S6 controle adverso"),
    ("JBS", "recomendacao_negativa", None, "S6/S8 controle adverso"),
    ("Lojas Renner", "recomendacao_negativa", None, "S6 controle adverso"),
    # pacote JBS obrigatorio (S8)
    ("JBS", "ma", None, "S8 pacote JBS"),
    ("JBS", "troca_ceo", None, "S8 pacote JBS"),
    ("JBS", "emissao_divida", None, "S8 pacote JBS"),
    # diversidade de M&A (S9)
    ("Suzano", "ma", None, "S9 diversidade M&A"),
    ("Natura &Co", "ma", 2, "S9 diversidade M&A"),
    ("Sabesp", "ma", None, "S9 diversidade M&A"),
    # diversidade de CEO (S10)
    ("CPFL Energia", "troca_ceo", None, "S10 CEO sem contexto adverso obvio"),
    ("Rumo", "troca_ceo", 1, "S10 CEO com contexto potencialmente adverso"),
    ("Vale", "troca_ceo", None, "S10 CEO sob investigacao em curso"),
    # divida e follow-on (S11/S12)
    ("Engie Brasil", "emissao_divida", None, "S11 emissao representativa"),
    ("Axia Energia", "emissao_divida", 1, "S11 emissao representativa"),
    ("Engie Brasil", "follow_on", 1, "S12 follow-on verdadeiro"),
    ("PRIO", "follow_on", None, "S12 follow-on verdadeiro"),
)

# S6/S9 — nomeados pela onda que NAO possuem ocorrencia pontuavel hoje.
# Reportados como ausentes; nao se fabrica linha para eles.
NOMEADOS = ("WEG", "Smart Fit")

# S1 — a regra e nao pedir ao humano que decida atribuicao. A excecao e a
# ocorrencia JA marcada como incerta no backlog: ali a duvida e conhecida e
# esconde-la seria pior. A linha entra, com o aviso visivel.
ATRIBUICAO_EM_ABERTO = {
    ("B3", "suspensao_negociacao"):
        "ATTRIBUTION_REVIEW_CANDIDATE — a suspensao e das acoes da Refit, "
        "terceiro; a B3 e a bolsa que suspende. Ver OCCURRENCE_ARCHITECTURE "
        "L578. Se a atribuicao lhe parecer errada, responda UNCERTAIN e "
        "diga isso na observacao.",
    ("B3", "troca_ceo"):
        "ATTRIBUTION_REVIEW_CANDIDATE — o CEO novo e da Gol, nao da B3. "
        "Ver OCCURRENCE_ARCHITECTURE L578.",
}


# ── evidencia local ────────────────────────────────────────────────────────
def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _limpar(txt: str) -> str:
    """Tira entidade HTML e espaco duplicado do resumo armazenado."""
    t = re.sub(r"&nbsp;?", " ", txt or "")
    t = re.sub(r"&amp;", "&", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def evidencia_do_artigo(rec: dict, limite: int = 300) -> dict:
    """Evidencia literal e curta (S14). Sem pesquisa externa, sem parafrase
    que acrescente fato — so o que esta armazenado localmente.

    FATO MEDIDO, nao suposto: o radar NAO guarda corpo de artigo. O campo
    `summary` do historico e, em 843 de 844 casos, o proprio titulo seguido do
    nome do veiculo; apenas 37 artigos trazem mais de 40 caracteres alem do
    titulo. A evidencia local real e, portanto, o TITULO — que e exatamente a
    entrada que o contrato do modelo recebe (`title` + `source` + `url` +
    `candidate_event`, sem corpo). Rotular a partir de titulo nao e um
    atalho: e parear a verdade humana com a entrada que o benchmark futuro
    vai de fato ver."""
    titulo = _limpar(rec.get("title") or "")
    resumo = _limpar(rec.get("summary") or "")
    # descarta o prefixo-eco e a assinatura do veiculo, e fica com o resto
    n_t, n_r = _norm(titulo), _norm(resumo)
    if titulo and n_r.startswith(n_t[:60]):
        resto = resumo[len(titulo):] if len(resumo) > len(titulo) else ""
    else:
        resto = resumo
    resto = re.sub(r"^[\s\-–—|:·]+", "", resto).strip()
    # o resumo termina com a assinatura do veiculo ("… Investing.com Brasil -
    # Financas, Cambio e Investimentos"). Isso e rodape, nao evidencia: se o
    # que sobrou do resumo E o proprio nome da fonte, nao sobrou nada.
    fonte = _norm(rd_fonte := (rec.get("source") or ""))
    if fonte and _norm(resto) and (_norm(resto) in fonte
                                   or fonte in _norm(resto)):
        resto = ""
    elif fonte:
        resto = re.sub(re.escape(rd_fonte) + r"\s*$", "", resto).strip()
    corpo = resto if len(resto) > 40 else ""
    if len(corpo) > limite:
        corte = corpo[:limite]
        corpo = (corte[:corte.rfind(" ")] if " " in corte else corte) + "…"
    return {"titulo": titulo, "fonte": rec.get("source") or "",
            "data": (rec.get("pub_iso") or "")[:10],
            "url": rec.get("display_url") or rec.get("url") or "",
            "corpo_local": corpo}


# ── ocorrencias que de fato pontuam ────────────────────────────────────────
def coletar(historico="risk_history.json", config="config_risco.yaml") -> dict:
    """Roda `build_evolution` instrumentando o agrupador, para recuperar TODAS
    as ocorrencias por empresa — inclusive as que `linha["events"]` perde por
    deduplicar em cima do nome da familia."""
    cfg = rd.load_config(config) if isinstance(config, str) else config
    H = (json.load(io.open(historico, encoding="utf-8"))
         if isinstance(historico, str) else historico)

    capturado: dict = defaultdict(list)
    original = rd._oe.agrupar_ocorrencias

    def espiao(occs, company, _cfg, fam_map, collapse_days, quando_fn=None):
        saida = original(occs, company, _cfg, fam_map, collapse_days,
                         quando_fn=quando_fn)
        for o in saida:
            oc = o.get("_ocorrencia")
            if oc:
                capturado[company].append(oc)
        return saida

    rd._oe.agrupar_ocorrencias = espiao
    try:
        evo = rd.build_evolution(H, cfg)
    finally:
        rd._oe.agrupar_ocorrencias = original

    # `risk_history["articles"]` e indexado por URL; o `article_id` que a
    # ocorrencia carrega vem de `semantic_v2_shadow.id_artigo(url, titulo)`.
    # Buscar registro por article_id direto no dicionario falha para TODOS os
    # artigos, em silencio — o que faria o pacote sair sem evidencia nenhuma.
    arts = H.get("articles", {})
    por_id = {}
    for url, rec in arts.items():
        try:
            por_id[_sv2.id_artigo(url, rec.get("title") or "")] = rec
        except Exception:                                    # noqa: BLE001
            continue
    return {"evo": evo, "ocorrencias": dict(capturado), "cfg": cfg,
            "artigos": arts, "por_id": por_id}


def _ligar(linha: dict, ocs: list) -> list:
    """Liga cada contribuicao do score (o que pontua) a sua ocorrencia.

    A ligacao e por URL do membro, com titulo como reserva. Uma contribuicao
    sem ocorrencia correspondente NAO e descartada em silencio: ela volta com
    `occurrence_id: None` e entra no relatorio de cobertura."""
    # A chave e o `article_id` canonico, nao a URL nem o titulo: os membros da
    # ocorrencia chegam com `url` vazia, e o titulo colide (a Rumo tem a mesma
    # materia do Citi capturada sob duas URLs do Estadao, que a producao trata
    # como duas ocorrencias distintas). Casar por titulo as fundiria.
    por_id, por_tit, ambiguo = {}, {}, set()
    for oc in ocs:
        for m in oc["members"]:
            if m.get("article_id"):
                por_id.setdefault(m["article_id"], oc)
            t = _norm(m.get("title") or "")
            if t in por_tit and por_tit[t]["occurrence_id"] != oc["occurrence_id"]:
                ambiguo.add(t)
            por_tit.setdefault(t, oc)
    fora = []
    for b in (linha.get("breakdown") or []):
        aid = _sv2.id_artigo(b.get("url") or "", b.get("title") or "")
        oc = por_id.get(aid)
        if oc is None:
            t = _norm(b.get("title") or "")
            oc = None if t in ambiguo else por_tit.get(t)
        fora.append({"contrib": b, "ocorrencia": oc, "article_id": aid,
                     "occurrence_id": oc["occurrence_id"] if oc else None})
    return fora


def universo(dados: dict) -> dict:
    """Toda ocorrencia que PONTUA hoje, com sua contribuicao e o contexto da
    empresa. E esta a superficie que uma modulacao direcional atingiria."""
    tax = {e["id"]: e for e in dados["cfg"]["taxonomy"]}
    linhas, sem_ligacao = [], []
    for l in dados["evo"]:
        ocs = dados["ocorrencias"].get(l["company"], [])
        for lig in _ligar(l, ocs):
            b, oc = lig["contrib"], lig["ocorrencia"]
            if not oc:
                sem_ligacao.append({"company": l["company"],
                                    "titulo": b.get("title"),
                                    "contrib": b.get("canonical_contrib")})
                continue
            fam = oc["family"]
            ev = tax.get(fam, {})
            linhas.append({
                "occurrence_id": oc["occurrence_id"],
                "company": l["company"], "family": fam,
                "family_label": b.get("label") or fam,
                "direcao_configurada": ev.get("direction"),
                "classe_de_sinal": oe.classe_de_sinal(ev),
                "contribution_class": b.get("contribution_class"),
                "canonical_contrib": round(b.get("canonical_contrib") or 0, 3),
                "phase": b.get("event_phase"),
                "phase_por_membro": [m.get("phase") for m in oc["members"]],
                "anchor_date": oc.get("anchor_date"),
                "data_efetiva": b.get("date"),
                "representante": oc.get("display_representative"),
                "membros": [m["article_id"] for m in oc["members"]],
                "n_membros": len(oc["members"]),
                "titulo": b.get("title"),
                "fonte": b.get("source"),
                "url": b.get("url"),
                "empresa_total": l.get("total_score"),
                "empresa_adverso": l.get("adverse_score"),
                "empresa_contextual": l.get("contextual_score"),
                "empresa_status": l.get("status"),
                "empresa_persistent": l.get("persistent"),
            })
    # ── uma linha por OCORRENCIA, nao por contribuicao ────────────────────
    # A Engie Brasil tem UMA ocorrencia de follow-on com 9 membros, e o score
    # conta DOIS desses membros em separado (28.6 + 19.06). Emitir duas linhas
    # pediria ao humano dois votos direcionais sobre o mesmo fato economico —
    # exatamente o que a unidade OCORRENCIA existe para impedir. Agrega-se, e
    # o fato de haver mais de uma contribuicao fica registrado.
    juntas: dict = {}
    for r in linhas:
        k = r["occurrence_id"]
        if k not in juntas:
            juntas[k] = {**r, "n_contribuicoes": 1,
                         "contrib_total": r["canonical_contrib"]}
            continue
        j = juntas[k]
        j["n_contribuicoes"] += 1
        j["contrib_total"] = round(j["contrib_total"]
                                   + r["canonical_contrib"], 3)
        if r["canonical_contrib"] > j["canonical_contrib"]:
            j.update({k2: r[k2] for k2 in
                      ("canonical_contrib", "titulo", "fonte", "url",
                       "data_efetiva", "phase")})
    saida = sorted(juntas.values(), key=lambda r: (r["company"],
                                                   r["occurrence_id"]))
    multiplas = [{"company": r["company"], "family": r["family"],
                  "occurrence_id": r["occurrence_id"],
                  "n_contribuicoes": r["n_contribuicoes"],
                  "contrib_total": r["contrib_total"]}
                 for r in saida if r["n_contribuicoes"] > 1]
    return {"linhas": saida, "n": len(saida),
            "contribuicoes_sem_ocorrencia": sem_ligacao,
            "ocorrencias_com_multiplas_contribuicoes": multiplas,
            "n_contribuicoes_ligadas": len(linhas),
            "n_breakdown": sum(len(l.get("breakdown") or [])
                               for l in dados["evo"])}


# ── selecao do lote D1 ─────────────────────────────────────────────────────
def selecionar(uni: dict, alvo_t1=(8, 12), alvo_t2=(12, 18)) -> dict:
    """Lote estratificado (S3/S17/S18): controles adversos obvios primeiro,
    depois o nucleo contextual, priorizando contribuicao alta, emissor em
    `atencao` e diversidade de familia — nunca recencia pura."""
    elegiveis, excluidos = [], []
    for r in uni["linhas"]:
        if r["company"] in EXCLUIR_D1:
            excluidos.append({**r, "motivo": NAO_ELEGIVEL + ": "
                              + EXCLUIR_D1[r["company"]]})
        else:
            elegiveis.append(r)

    def peso(r):
        return (2 if r["empresa_status"] == "critico" else
                1 if r["empresa_status"] == "atencao" else 0,
                r["canonical_contrib"])

    # ── passo 1: os controles nomeados entram antes do corte por peso ──────
    escolhidos, motivos, ausentes = {}, {}, []
    for empresa, familia, quantos, porque in OBRIGATORIOS:
        cand = sorted([r for r in elegiveis
                       if r["company"] == empresa and r["family"] == familia],
                      key=lambda r: -r["canonical_contrib"])
        if not cand:
            ausentes.append({"company": empresa, "family": familia,
                             "motivo_pedido": porque,
                             "estado": "SEM_OCORRENCIA_PONTUAVEL"})
            continue
        for r in cand[:(quantos or len(cand))]:
            escolhidos[r["occurrence_id"]] = r
            motivos[r["occurrence_id"]] = porque
    for nome in NOMEADOS:
        if not any(r["company"].lower().startswith(nome.lower())
                   for r in uni["linhas"]):
            ausentes.append({"company": nome, "family": "*",
                             "motivo_pedido": "S6/S9 nomeado pela onda",
                             "estado": "SEM_OCORRENCIA_PONTUAVEL"})

    t1 = [r for r in escolhidos.values()
          if r["classe_de_sinal"] == oe.SINAL_ADVERSO]
    t2 = [r for r in escolhidos.values()
          if r["classe_de_sinal"] != oe.SINAL_ADVERSO]
    por_fam = Counter(r["family"] for r in t2)
    por_emp = Counter(r["company"] for r in t2)

    # ── passo 2: completa por peso, respeitando teto de familia e empresa ──
    adv = [r for r in elegiveis if r["classe_de_sinal"] == oe.SINAL_ADVERSO
           and r["occurrence_id"] not in escolhidos]
    ctx = [r for r in elegiveis if r["classe_de_sinal"] != oe.SINAL_ADVERSO
           and r["occurrence_id"] not in escolhidos]

    vistos = Counter(r["company"] for r in t1)
    for r in sorted(adv, key=peso, reverse=True):
        if len(t1) >= alvo_t1[1]:
            break
        if vistos[r["company"]] >= 2:
            continue
        vistos[r["company"]] += 1
        t1.append(r)
        motivos[r["occurrence_id"]] = "S18 contribuicao alta"

    teto_fam, teto_emp = 7, 5
    for r in sorted(ctx, key=peso, reverse=True):
        if len(t2) >= alvo_t2[1]:
            break
        if por_fam[r["family"]] >= teto_fam or por_emp[r["company"]] >= teto_emp:
            continue
        t2.append(r)
        motivos[r["occurrence_id"]] = "S18 contribuicao alta"
        por_fam[r["family"]] += 1
        por_emp[r["company"]] += 1
    return {"tier1": t1, "tier2": t2, "excluidos": excluidos,
            "motivos": motivos, "nomeados_ausentes": ausentes,
            "cobertura_familia": dict(por_fam)}


# ── montagem do pacote ─────────────────────────────────────────────────────
def montar(dados: dict, sel: dict) -> dict:
    arts = dados["por_id"]
    linhas = []
    ordem = sorted(sel["tier1"], key=lambda r: (-r["canonical_contrib"],
                                                r["occurrence_id"])) \
        + sorted(sel["tier2"], key=lambda r: (-r["canonical_contrib"],
                                              r["occurrence_id"]))
    for i, r in enumerate(ordem, 1):
        rep = arts.get(r["representante"]) or {}
        ev_rep = evidencia_do_artigo(rep)
        # S13/S14 — a evidencia e o conjunto de titulos DISTINTOS da
        # ocorrencia. Titulos de veiculos diferentes sobre o mesmo fato
        # costumam trazer angulos diferentes; titulo repetido nao acrescenta.
        outros, vistos_t = [], {_norm(ev_rep["titulo"])}
        for aid in r["membros"]:
            if aid == r["representante"]:
                continue
            e = evidencia_do_artigo(arts.get(aid) or {}, limite=200)
            n = _norm(e["titulo"])
            if e["titulo"] and n not in vistos_t:
                vistos_t.add(n)
                outros.append({"article_id": aid, **e})
        pontos = []
        if ev_rep["corpo_local"]:
            pontos.append(ev_rep["corpo_local"])
        for o in outros[:2]:
            pontos.append("%s — %s%s" % (o["titulo"], o["fonte"] or "fonte n/d",
                                         (" · " + o["corpo_local"])
                                         if o["corpo_local"] else ""))
        if ev_rep["corpo_local"]:
            estado = "CORPO_LOCAL"
        elif outros:
            estado = "TITULOS_MULTIPLOS"
        else:
            estado = EVIDENCIA_INSUFICIENTE
        linhas.append({
            "review_id": "D%02d" % i,
            "motivo_selecao": sel["motivos"].get(r["occurrence_id"], ""),
            "occurrence_id": r["occurrence_id"],
            "company": r["company"], "family": r["family"],
            "family_label": r["family_label"],
            "direcao_configurada": r["direcao_configurada"],
            "classe_de_sinal": r["classe_de_sinal"],
            "tier": ("TIER1_ADVERSE_CONTROL"
                     if r["classe_de_sinal"] == oe.SINAL_ADVERSO
                     else "TIER2_CONTEXTUAL"),
            "expected_control": ("ADVERSE"
                                 if r["classe_de_sinal"] == oe.SINAL_ADVERSO
                                 else None),
            "contribution_class": r["contribution_class"],
            "canonical_contrib": r["canonical_contrib"],
            "phase": r["phase"], "phase_por_membro": r["phase_por_membro"],
            "anchor_date": r["anchor_date"], "data_efetiva": r["data_efetiva"],
            "representante": r["representante"], "membros": r["membros"],
            "n_membros": r["n_membros"],
            "titulo": ev_rep["titulo"] or r["titulo"],
            "fonte": ev_rep["fonte"] or r["fonte"],
            "url": r["url"],
            "evidencia": pontos,
            "evidencia_membros": outros[:2],
            "estado_evidencia": estado,
            "aviso_atribuicao": ATRIBUICAO_EM_ABERTO.get(
                (r["company"], r["family"])),
            "empresa": {"total": r["empresa_total"],
                        "adverso": r["empresa_adverso"],
                        "contextual": r["empresa_contextual"],
                        "status": r["empresa_status"],
                        "persistent": r["empresa_persistent"]},
            # S20 — sai VAZIO. O humano preenche; o gerador nunca.
            "human_label": None,
            "human_note": None,
        })
    return {"_meta": {"pack_version": PACK_VERSION, **AUTORIDADE},
            "rotulos_permitidos": list(ROTULOS),
            "unidade": "OCCURRENCE",
            "linhas": linhas, "n": len(linhas),
            "n_tier1": sum(1 for l in linhas
                           if l["tier"] == "TIER1_ADVERSE_CONTROL"),
            "n_tier2": sum(1 for l in linhas if l["tier"] == "TIER2_CONTEXTUAL"),
            "excluidos": sel["excluidos"],
            "nomeados_ausentes": sel["nomeados_ausentes"]}


def manifesto(pack: dict) -> dict:
    """S22 — manifesto estavel. O hash cobre id, empresa, familia, artigos e a
    evidencia mostrada; muda se, e so se, o que o humano leu mudar."""
    linhas = []
    for l in pack["linhas"]:
        h = hashlib.sha256(
            json.dumps([l["occurrence_id"], l["company"], l["family"],
                        sorted(l["membros"]), l["titulo"], l["evidencia"]],
                       sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        linhas.append({"review_id": l["review_id"],
                       "occurrence_id": l["occurrence_id"],
                       "company": l["company"], "family": l["family"],
                       "article_ids": sorted(l["membros"]),
                       "evidence_hash": h})
    total = hashlib.sha256(
        json.dumps(linhas, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return {"linhas": linhas, "manifesto_hash": total, "n": len(linhas),
            "ordem_deterministica": True}


# ── S23 — proveniencia da direcao humana ja existente ──────────────────────
def proveniencia_humana(caminho="risk_human_supervision.json") -> dict:
    """Audita o que a supervisao humana JA diz sobre direcao, sem converter
    nada em rotulo. Distingue afirmacao direcional explicita de afirmacao
    apenas LIMITADA ('nao e automaticamente adverso')."""
    try:
        S = json.load(io.open(caminho, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        return {"erro": str(e), "explicitos": [], "limitados": []}
    EXPL = re.compile(r"advers|negativ|deteriora|favorav|positiv|mitiga",
                      re.I)
    LIM = re.compile(r"nao\s+(e|sao)\s+(necessariamente|automaticamente)|"
                     r"not\s+necessarily|not\s+automatically|"
                     r"nao\s+pontua|indetermin", re.I)
    expl, lim = [], []
    def varrer(no, caminho_no=""):
        if isinstance(no, dict):
            for k, v in no.items():
                varrer(v, caminho_no + "/" + str(k))
        elif isinstance(no, list):
            for i, v in enumerate(no):
                varrer(v, caminho_no + "[%d]" % i)
        elif isinstance(no, str) and len(no) > 12:
            t = _norm(no)
            if LIM.search(t):
                lim.append({"onde": caminho_no, "texto": no[:180]})
            elif EXPL.search(t):
                expl.append({"onde": caminho_no, "texto": no[:180]})
    varrer(S)
    return {"explicitos": expl, "limitados": lim,
            "n_explicitos": len(expl), "n_limitados": len(lim),
            "nota": "proveniencia apenas — nada aqui virou rotulo"}


# ── S24 — controle da lacuna de coleta ─────────────────────────────────────
def lacuna_walkers(dados: dict) -> dict:
    """Walkers/Pilgrim's existe nas observacoes do contrato V2 mas nao em
    `risk_history`. NAO pode ser fabricado como ocorrencia de producao."""
    arts = dados["por_id"]
    alvo = "61166442c0f897153eec"
    return {"article_id": alvo,
            "em_risk_history": alvo in arts,
            "classificacao": "COLLECTION_GAP_CONTROL",
            "elegivel_para_linha_direcional": False,
            "nota": "util para trabalho futuro de recall/taxonomia; nao e "
                    "ocorrencia de producao e nao entra no D1"}


# ── S26/S27 — cobertura futura do benchmark ────────────────────────────────
def cobertura_futura(pack: dict) -> dict:
    """Se o humano rotular TODAS as linhas do D1, quantos casos de benchmark
    passam a existir, por familia. Nao se prediz distribuicao de rotulo."""
    fam = Counter(l["family"] for l in pack["linhas"])
    t1 = Counter(l["family"] for l in pack["linhas"]
                 if l["tier"] == "TIER1_ADVERSE_CONTROL")
    contextuais = {f: fam.get(f, 0) for f in FAM_CONTEXTUAIS}
    faltando = [f for f, n in contextuais.items() if n < 2]
    suficiente = (pack["n_tier1"] >= 3 and pack["n_tier2"] >= 10
                  and not faltando)
    return {"casos_por_familia": dict(fam),
            "controles_adversos_por_familia": dict(t1),
            "familias_contextuais_maiores": contextuais,
            "familias_com_menos_de_2": faltando,
            "d1_suficiente_para_primeiro_benchmark": suficiente,
            "d2_necessario": not suficiente,
            "o_que_d2_precisaria": ([] if suficiente else
                                    ["ao menos 2 ocorrencias por familia "
                                     "contextual maior: " + ", ".join(faltando)]),
            "nota": "contagem de casos e cobertura de familia apenas — "
                    "nenhuma distribuicao de rotulo foi prevista"}


# ── saida ──────────────────────────────────────────────────────────────────
def markdown(pack: dict, man: dict) -> str:
    L = []
    a = L.append
    a("# DIRECTIONAL HUMAN REVIEW — BATCH D1\n")
    a("Pacote de revisão humana. **Nenhuma previsão de modelo** e **nenhum "
      "rótulo pré-preenchido**: as células de rótulo saem vazias de "
      "propósito.\n")
    a("- unidade: **OCORRÊNCIA**")
    a("- rótulos: `ADVERSE` · `FAVORABLE` · `NEUTRAL` · `MIXED` · `UNCERTAIN`")
    a("- manifesto: `%s`" % man["manifesto_hash"])
    a("- linhas: **%d** (Tier 1 adverso %d · Tier 2 contextual %d)\n"
      % (pack["n"], pack["n_tier1"], pack["n_tier2"]))
    a("A pergunta **não** é \"esse tipo de evento poderia ser ruim?\". "
      "É: **a evidência local desta ocorrência sustenta que ela é ruim?**\n")
    a("Responda em bloco, no chat: `D01 ADVERSE`, `D02 UNCERTAIN`, …\n")
    a("---\n")
    for l in pack["linhas"]:
        e = l["empresa"]
        a("## %s — %s / %s" % (l["review_id"], l["company"], l["family_label"]))
        a("")
        a("**%s**  " % l["titulo"])
        a("%s · %s · fase `%s` · %d artigo(s) na ocorrência  "
          % (l["fonte"] or "fonte não registrada", l["data_efetiva"] or "—",
             l["phase"] or "UNKNOWN", l["n_membros"]))
        a("Contribui **%.1f** (`%s`) para %s, que hoje soma **%s** = %s adverso "
          "+ %s contextual · status `%s`"
          % (l["canonical_contrib"], l["contribution_class"], l["company"],
             e["total"], e["adverso"], e["contextual"], e["status"]))
        a("")
        if l["estado_evidencia"] == EVIDENCIA_INSUFICIENTE:
            a("> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima "
              "está armazenado localmente: um único artigo, sem corpo. "
              "É também tudo o que o contrato do modelo receberia.")
        else:
            for p in l["evidencia"]:
                a("> %s" % p)
        a("")
        if l["aviso_atribuicao"]:
            a("⚠️ %s" % l["aviso_atribuicao"])
            a("")
        if l["expected_control"]:
            a("*(controle esperado: `ADVERSE` — não vincula seu julgamento)*")
            a("")
        a("```")
        a("%s — %s / %s" % (l["review_id"], l["company"], l["family_label"]))
        a("Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]")
        a("Observação opcional:")
        a("```")
        a("")
    return "\n".join(L)


def rodar(historico="risk_history.json", config="config_risco.yaml") -> dict:
    dados = coletar(historico, config)
    uni = universo(dados)
    sel = selecionar(uni)
    pack = montar(dados, sel)
    man = manifesto(pack)
    return {"_meta": {"pack_version": PACK_VERSION, **AUTORIDADE},
            "universo": {"n_ocorrencias_pontuaveis": uni["n"],
                         "n_linhas_breakdown": uni["n_breakdown"],
                         "contribuicoes_sem_ocorrencia":
                             uni["contribuicoes_sem_ocorrencia"]},
            "pack": pack, "manifesto": man,
            "proveniencia_humana": proveniencia_humana(),
            "lacuna_walkers": lacuna_walkers(dados),
            "cobertura_futura": cobertura_futura(pack)}


def main() -> int:
    import sys
    R = rodar()
    escrever = "--escrever" in sys.argv
    if escrever:
        io.open("directional_human_review_pack_d1.json", "w",
                encoding="utf-8").write(
            json.dumps(R, ensure_ascii=False, indent=1, sort_keys=True))
        io.open("docs/DIRECTIONAL_HUMAN_REVIEW_D1.md", "w",
                encoding="utf-8").write(markdown(R["pack"], R["manifesto"]))
        print("artefatos escritos")
    print("ocorrencias pontuaveis: %d | breakdown: %d | pack: %d "
          "(T1 %d / T2 %d) | manifesto %s"
          % (R["universo"]["n_ocorrencias_pontuaveis"],
             R["universo"]["n_linhas_breakdown"], R["pack"]["n"],
             R["pack"]["n_tier1"], R["pack"]["n_tier2"],
             R["manifesto"]["manifesto_hash"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
