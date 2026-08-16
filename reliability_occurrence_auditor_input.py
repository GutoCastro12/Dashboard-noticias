#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_occurrence_auditor_input.py — o que o auditor de novidade poderá ver.

POR QUE ESTE MÓDULO EXISTE

A pergunta útil não é "este artigo fala de troca de CEO?". É: dado este artigo
e as ocorrências que o sistema já montou para a mesma empresa e família, ele é
um fato NOVO, uma FASE de um fato existente, ou comentário sobre algo já
conhecido? Nenhum classificador de artigo isolado responde isso — falta o
contexto do que já existe.

Este módulo monta esse contexto. Não chama modelo nenhum.

A INVARIANTE QUE JUSTIFICA O MÓDULO EXISTIR SEPARADO

O pacote é CEGO. Ele se constrói só do que o runtime observa: histórico,
classificação determinística, ocorrências provisórias do agrupador, extração de
pessoa, marca de acompanhamento. A verdade humana de ocorrência não entra —
nem o id, nem a novidade, nem a fase, nem a âncora, nem a relação.

Isso não é escrúpulo: em produção a verdade humana não existe no momento da
inferência. Um construtor que a use funciona no teste e falha no dia seguinte,
e a medição feita com ela é ficção. Por isso `prompt_payload` e
`evaluation_metadata` são campos separados, e há teste que varre o payload
procurando vazamento.

REFERÊNCIA PROVISÓRIA NÃO É VERDADE

O pacote usa `_occ_key` como `provisional_occurrence_id`. Isso é legítimo e é o
oposto do que a verdade humana faz: aqui ele é entrada do algoritmo, descartável,
e serve só para o modelo poder apontar "candidato 1" ou "candidato 2". A verdade
humana nunca o usa, porque ele muda quando o agrupador melhora.

AVALIAÇÃO COMPARA PERTINÊNCIA, NUNCA ID

Ocorrência provisória e ocorrência humana vivem em espaços diferentes. Comparar
`ma#0` com `ma:smart-fit:1aa8...` não significaria nada. O avaliador pergunta
outra coisa: os artigos que o auditor juntou são os mesmos que o humano juntou?
É por isso que Santander pode ter duas ocorrências provisórias e uma humana sem
que nada esteja inconsistente — é justamente o defeito que se quer medir.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time

import reliability_ceo_duplicate_detector as ceo
import reliability_occurrence_truth as ot
import risk_dashboard as rd
import semantic_audit as sa

INPUT_CONTRACT = "occurrence.auditor.input.v1"
OUTPUT_CONTRACT = "occurrence.auditor.output.v1"

# §11 — o auditor escolhe entre candidatos ou diz que nenhum serve. O enum de
# novidade é o do Contract V2, importado, nunca copiado.
OUTPUT_NOVELTY = ot.OCCURRENCE_NOVELTY
OUTPUT_CONFIANCA = ("HIGH", "MEDIUM", "LOW", "UNDETERMINED")
OUTPUT_EVIDENCE_SOURCE = ("TARGET_TITLE", "TARGET_SNIPPET", "CANDIDATE_TITLE")
TEXT_EVIDENCE = ("TITLE_ONLY", "TITLE_PLUS_REDUNDANT_SNIPPET", "RICHER_LOCAL_TEXT")

# Campos que jamais podem aparecer no payload. A varredura é por SUBSTRING no
# JSON serializado — mais grosseira que checar chave por chave, e é isso que a
# torna útil: pega o vazamento que entrou dentro de um texto livre.
PROIBIDOS_NO_PAYLOAD = (
    "occurrence_truth_id", "occurrence_truth", "adjudicated_by",
    "adjudicator_type", "adjudicated_at_iso", "should_refresh_anchor",
    "DISTINCT_OCCURRENCE", "membership_id", "superseded_by", "supersedes",
    "material_phase", "human_review", "dimensoes_adjudicadas",
)


def _limpo(s):
    return re.sub(r"\s+", " ", re.sub(r"&nbsp;|&amp;", " ", s or "")).strip()


def qualidade_do_texto(artigo: dict) -> str:
    """§68 — quanto texto local existe de verdade.

    Medido no corpus: 94% dos resumos são o título repetido mais o nome da
    fonte. Um auditor que suponha corpo de matéria vai alucinar detalhe; dizer
    a ele que só há manchete é o que permite abster-se com honestidade."""
    t, s = _limpo(artigo.get("title")).lower(), _limpo(artigo.get("summary")).lower()
    if not s:
        return "TITLE_ONLY"
    if s.startswith(t[:max(20, int(len(t) * 0.8))]) and len(s) < len(t) + 60:
        return "TITLE_PLUS_REDUNDANT_SNIPPET"
    return "RICHER_LOCAL_TEXT"


def _identidade_familia(titulo: str, empresa: str, familia: str, aliases_glob) -> dict:
    """§10 — só o que o sistema DETERMINÍSTICO já extrai hoje. Nada de
    reconhecedor de entidade novo inventado para a ocasião."""
    if familia == "troca_ceo":
        p = ceo.extrai_pessoas(titulo, aliases_glob)
        return {"incoming_person": p["incoming_person"] or None,
                "outgoing_person": p["outgoing_person"] or None,
                "follow_up_language": ceo.eh_seguimento(titulo),
                "extraction_evidence": p["evidencia"]}
    return {}


def _artigo_resumido(u, a, empresa, familia, aliases_glob, com_texto=True):
    d = {
        "article_ref": ot.article_ref(a.get("url") or u, a.get("title") or ""),
        "publication_date": time.strftime("%Y-%m-%d", time.gmtime(a.get("pub_ts") or 0)),
        "source": a.get("source") or "",
        "title": a.get("title") or "",
        "language": (a.get("language") or "") or None,
    }
    if com_texto:
        q = qualidade_do_texto(a)
        d["text_evidence_quality"] = q
        d["local_snippet"] = (_limpo(a.get("summary"))
                              if q == "RICHER_LOCAL_TEXT" else None)
        d["family_identity_extracted"] = _identidade_familia(
            a.get("title") or "", empresa, familia, aliases_glob)
    return d


def _ocorrencias_provisorias(H, cfg, empresa, familia):
    """Estado do agrupador AGORA. É o que existirá em produção no momento da
    inferência; a verdade humana, não."""
    AL = sa._aliases_map(cfg)
    its = []
    for u, a in (H.get("articles") or {}).items():
        if familia not in ((a.get("events_by_company") or {}).get(empresa) or []):
            continue
        t = a.get("title") or ""
        its.append({"u": u, "a": a, "event_id": familia, "pub_ts": a.get("pub_ts") or 0,
                    "title": t,
                    "_ident": rd.occurrence_identity(t, familia, empresa, AL.get(empresa))})
    its.sort(key=lambda x: (x["pub_ts"], x["u"]))
    rd.assign_occurrence_clusters(its, 45, None, AL)
    g = {}
    for o in its:
        g.setdefault(o["_occ_key"], []).append(o)
    return [g[k] for k in sorted(g, key=lambda k: (min(o["pub_ts"] for o in g[k]), k))]


def _representantes(membros, teto):
    """§18 — quais manchetes representam a ocorrência quando ela é grande.

    Primeiro e último dão o alcance temporal; os que têm identidade explícita
    dizem de QUE fato se trata. Cortar pelo meio preserva as duas coisas, que é
    o que o modelo precisa para reconhecer a mesma transação em fases distintas.
    """
    if len(membros) <= teto:
        return list(membros)
    com_id = [o for o in membros[1:-1] if o.get("_tem_id")]
    escolhidos = [membros[0]] + com_id[:max(0, teto - 2)] + [membros[-1]]
    vistos, out = set(), []
    for o in escolhidos:
        if o["u"] not in vistos:
            vistos.add(o["u"])
            out.append(o)
    return out[:teto]


def construir_pacote(empresa: str, familia: str, article_ref_alvo: str,
                     historico: str = "risk_history.json",
                     config: str = "config_risco.yaml",
                     teto_ocorrencias: int = 5,
                     teto_artigos_por_ocorrencia: int = 4,
                     detector_reasons=None) -> dict:
    """Monta o pacote CEGO de um artigo-alvo.

    O alvo é excluído das ocorrências candidatas: perguntar "a que ocorrência
    isto pertence" oferecendo a ocorrência que já o contém seria responder a
    própria pergunta."""
    cfg = rd.load_config(config)
    H = json.load(io.open(historico, encoding="utf-8"))
    aliases_glob = ceo._aliases_conhecidos(cfg)
    grupos = _ocorrencias_provisorias(H, cfg, empresa, familia)

    alvo_obj = None
    for g in grupos:
        for o in g:
            if ot.article_ref(o["a"].get("url") or o["u"], o["title"]) == article_ref_alvo:
                alvo_obj = o
    if alvo_obj is None:
        raise ValueError(f"ARTIGO_NAO_ENCONTRADO: {article_ref_alvo}")

    candidatos = []
    for i, g in enumerate(grupos):
        restantes = [o for o in g if o is not alvo_obj]
        if not restantes:
            continue
        for o in restantes:
            fi = _identidade_familia(o["title"], empresa, familia, aliases_glob)
            o["_tem_id"] = bool(fi.get("incoming_person") or fi.get("outgoing_person"))
        reps = _representantes(restantes, teto_artigos_por_ocorrencia)
        ident = {}
        for o in restantes:
            fi = _identidade_familia(o["title"], empresa, familia, aliases_glob)
            for k, v in fi.items():
                if v and k in ("incoming_person", "outgoing_person"):
                    ident.setdefault(k, set()).add(v)
        candidatos.append({
            "candidate_label": f"CANDIDATE_{len(candidatos) + 1}",
            "provisional_occurrence_id": restantes[0]["_occ_key"],
            "company": empresa,
            "event_id": familia,
            "n_articles": len(restantes),
            "first_date": time.strftime("%Y-%m-%d", time.gmtime(min(o["pub_ts"] for o in restantes))),
            "last_date": time.strftime("%Y-%m-%d", time.gmtime(max(o["pub_ts"] for o in restantes))),
            "family_identity_extracted": {k: sorted(v) for k, v in ident.items()},
            "operation_markers": sorted({m for o in restantes
                                         for m in (o["_ident"].get("marcadores") or "").split("|")
                                         if m}),
            "representative_articles": [
                _artigo_resumido(o["u"], o["a"], empresa, familia, aliases_glob,
                                 com_texto=False) for o in reps],
            "articles_omitted": len(restantes) - len(reps),
        })
    candidatos = candidatos[-teto_ocorrencias:]
    for n, c in enumerate(candidatos, 1):
        c["candidate_label"] = f"CANDIDATE_{n}"

    return {
        "input_contract": INPUT_CONTRACT,
        "prompt_payload": {
            "company": empresa,
            "event_id": familia,
            "target_article": _artigo_resumido(alvo_obj["u"], alvo_obj["a"], empresa,
                                               familia, aliases_glob),
            "candidate_occurrences": candidatos,
            "detector_reasons": list(detector_reasons or []),
            "answer_space": {
                "occurrence_novelty": list(OUTPUT_NOVELTY),
                "selected_candidate": [c["candidate_label"] for c in candidatos] + [None],
                "confidence": list(OUTPUT_CONFIANCA),
            },
        },
        "evaluation_metadata": {
            "target_article_ref": article_ref_alvo,
            "built_from": "runtime_observable_state_only",
        },
    }


def vazamentos(pacote: dict) -> list:
    """§54 — varre o payload atrás de qualquer coisa que só a verdade humana
    saberia. Devolve os termos encontrados."""
    bruto = json.dumps(pacote.get("prompt_payload") or {}, ensure_ascii=False)
    return sorted({t for t in PROIBIDOS_NO_PAYLOAD if t in bruto})


# ── avaliação offline (§21/§22/§60) ─────────────────────────────────────────
def _verdade_por_artigo(dados: dict, empresa: str, familia: str) -> dict:
    return {m["article_ref"]: m["occurrence_truth_id"]
            for m in ot.memberships_ativas(dados)
            if m["company"] == empresa and m["event_id"] == familia}


def avaliar(pacote: dict, saida: dict, dados_verdade: dict) -> dict:
    """Compara uma saída HIPOTÉTICA do auditor com a verdade humana.

    A comparação é por PERTINÊNCIA de artigo, nunca por id: o candidato
    provisório e a ocorrência humana vivem em espaços diferentes, e igualar
    `ma#0` a `ma:smart-fit:1aa8...` não significaria nada."""
    pp = pacote["prompt_payload"]
    emp, fam = pp["company"], pp["event_id"]
    alvo = pacote["evaluation_metadata"]["target_article_ref"]
    verdade = _verdade_por_artigo(dados_verdade, emp, fam)
    verdade_alvo = verdade.get(alvo)
    escolhido = saida.get("selected_candidate")
    cand = {c["candidate_label"]: c for c in pp["candidate_occurrences"]}
    refs_do_candidato = set()
    if escolhido in cand:
        refs_do_candidato = {a["article_ref"]
                             for a in cand[escolhido]["representative_articles"]}
    verdades_do_candidato = {verdade.get(r) for r in refs_do_candidato} - {None}

    r = {"target_article_ref": alvo, "human_occurrence": verdade_alvo,
         "selected_candidate": escolhido,
         "novelty_predita": saida.get("occurrence_novelty"),
         "false_merge": False, "false_split": False,
         "linkage_correct": None, "novelty_correct": None,
         "abstained": saida.get("occurrence_novelty") == "UNDETERMINED"
                      or saida.get("confidence") == "UNDETERMINED"}
    if verdade_alvo is None:
        r["avaliavel"] = False
        return r
    r["avaliavel"] = True
    if escolhido is None:
        # disse NOVA. É falso split se o humano diz que pertence a algo que já
        # está entre os candidatos.
        outras = {v for refs in
                  [{a["article_ref"] for a in c["representative_articles"]}
                   for c in pp["candidate_occurrences"]] for v in
                  {verdade.get(x) for x in refs} - {None}}
        r["false_split"] = verdade_alvo in outras
        r["linkage_correct"] = verdade_alvo not in outras
    else:
        acertou = verdade_alvo in verdades_do_candidato
        r["linkage_correct"] = acertou
        r["false_merge"] = bool(verdades_do_candidato) and not acertou
    return r


def relatorio(pacote: dict) -> str:
    pp = pacote["prompt_payload"]
    t = pp["target_article"]
    L = [f"## Pacote de auditoria — {pp['company']} · `{pp['event_id']}`", ""]
    L.append(f"*{pacote['input_contract']} · cego: construído só do estado "
             f"observável em runtime.*")
    L.append("")
    L.append(f"**Alvo** {t['publication_date']} · {t['source']}")
    L.append(f"> {t['title']}")
    L.append(f"- evidência textual: {t['text_evidence_quality']}")
    if t.get("family_identity_extracted"):
        fi = {k: v for k, v in t["family_identity_extracted"].items()
              if v and k != "extraction_evidence"}
        L.append(f"- extraído: {fi or 'nada'}")
    L.append("")
    L.append(f"**{len(pp['candidate_occurrences'])} ocorrência(s) candidata(s)**")
    for c in pp["candidate_occurrences"]:
        L.append(f"- **{c['candidate_label']}** {c['first_date']} → {c['last_date']} "
                 f"({c['n_articles']} artigo(s))"
                 + (f", {c['articles_omitted']} omitido(s)" if c["articles_omitted"] else ""))
        if c["family_identity_extracted"]:
            L.append(f"  - identidade: {c['family_identity_extracted']}")
        for a in c["representative_articles"]:
            L.append(f"  - {a['publication_date']} — {a['title'][:80]}")
    vaz = vazamentos(pacote)
    L.append("")
    L.append(f"**Vazamento de verdade humana:** {'nenhum' if not vaz else vaz}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Monta o pacote CEGO do auditor de novidade de ocorrência "
                    "(somente leitura; não chama modelo).")
    p.add_argument("--empresa", required=True)
    p.add_argument("--evento", required=True)
    p.add_argument("--artigo-ref", required=True)
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--config", default="config_risco.yaml")
    p.add_argument("--json-out", default=None)
    a = p.parse_args(argv)
    pac = construir_pacote(a.empresa, a.evento, a.artigo_ref, a.historico, a.config)
    if a.json_out:
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(pac, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"JSON -> {a.json_out}")
    else:
        print(relatorio(pac))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
