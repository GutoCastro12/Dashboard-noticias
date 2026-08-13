#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot1_sample.py — R7b, primeiro piloto Gemini: CONGELAR A AMOSTRA.

Este módulo não fala com provider nenhum. Ele responde, de forma reproduzível,
a uma pergunta só: O QUE EXATAMENTE SERÁ ENVIADO ao modelo quando a execução
for autorizada.

DUAS TRILHAS, MEDIDAS SEPARADAMENTE (decisão humana da wave R7b-S3):

  RICH     — S1/S2/S4/S5/S6/S8, com corpo de verdade. É daqui que sai o número
             de acurácia do piloto.
  DEGRADED — S3, onde só existe título + `og:`/`meta:description` porque os
             publishers bloqueiam. Entra no piloto porque são os quatro casos
             melhor adjudicados que temos e testam atribuição, papel do artigo,
             atualidade e centralidade. NUNCA soma na métrica rich.

REPRODUTIBILIDADE: nada de amostra aleatória. A seleção é ordenada por
`article_id` e o manifesto grava os SHAs do corpus de origem. Rodar duas vezes
sobre o mesmo corpus dá o mesmo manifesto — `--verificar` prova isso.

VERDADE HUMANA NUNCA ENTRA NO PAYLOAD. Ela é anexada ao manifesto do lado da
AVALIAÇÃO, num bloco separado, e os testes de vazamento falham se um rótulo,
nota de revisor ou veredito determinístico aparecer no que vai ao modelo.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

import reliability_pilot_contract as pc
import risk_dashboard as rd
import semantic_audit as sa

SAMPLE_VERSION = "r7b.pilot1.sample.v1"
INPUT_VERSION = "r7b.pilot1.input.v1"

OUTDIR = Path(os.environ.get("R7B_PILOT1_OUT", "out_reliability/r7b_pilot1"))
MANIFESTO = OUTDIR / "pilot1_sample_manifest.json"

HISTORY = Path("risk_history.json")
SHADOW = Path("risk_input_shadow.json")
ENRICH = Path("risk_enrichment_shadow.json")
S3EXP = Path("out_reliability/r7b_s3_experimental_inputs.json")

DEV_CONTROL = "DEV_CONTROL"
HOLDOUT = "HOLDOUT"
RICH = "rich"
DEGRADED = "degraded"

# Limiar de corpo "rico". É o mesmo espírito da r7c.policy2, mas vive AQUI e
# não a altera: política de produção decide o que alimenta o shadow; isto
# decide em que trilha o item é REPORTADO.
RICH_MIN_CHARS = 600
RICH_MIN_SENT = 3


def _sha(p: Path) -> str:
    if not p.exists():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _sent(t: str) -> int:
    return len([x for x in re.split(r"[.!?]\s", t or "") if len(x) > 25])


def _toks(t: str) -> int:
    return len({w.lower() for w in re.findall(r"\w{4,}", t or "")})


# ── resolução de input: melhor texto ARMAZENADO, sem rede ───────────────────
def montar_fontes() -> dict:
    """Índice url -> (texto, tier, origem). Nenhuma requisição."""
    fontes = {}

    if ENRICH.exists():
        for k, v in (json.load(io.open(ENRICH, encoding="utf-8"))
                     .get("articles") or {}).items():
            txt = " ".join(f.get("text_excerpt") or ""
                           for f in (v.get("fragments") or [])
                           if isinstance(f, dict))
            if txt.strip():
                for u in (v.get("canonical_url"), k):
                    if u:
                        fontes.setdefault(u, (txt, "R1_SIDECAR", "enrichment_shadow"))

    if S3EXP.exists():
        for r in (json.load(io.open(S3EXP, encoding="utf-8"))
                  .get("registros") or []):
            txt = r.get("evidence_text") or ""
            if txt.strip():
                for u in (r.get("url"), r.get("canonical_url")):
                    if u:
                        fontes[u] = (txt, r.get("fetch", {}).get("tier") or "THIN",
                                     "s3_experimental")
    return fontes


def melhor_input(url: str, rec: dict, fontes: dict) -> dict:
    """Escada: título → summary do history → fragmento armazenado.

    Sem rede, sem inventar. O que não existe fica registrado como ausente —
    input pobre não pode ser mascarado, porque é ele que decide a trilha.
    """
    titulo = rec.get("title") or ""
    resumo = rec.get("summary") or ""
    corpo, tier, origem = fontes.get(url, ("", "", ""))
    if not corpo and resumo.strip() and resumo.strip() != titulo.strip():
        corpo, tier, origem = resumo, "R0_SUMMARY", "history_summary"

    partes, prov = [], []
    t = pc.normalizar(titulo)
    if t:
        partes.append(t)
        prov.append({"metodo": "title", "chars": len(t)})
    c = pc.normalizar(corpo)
    # o summary do coletor repete o título com frequência; contar isso como
    # ganho de input seria mentir para o próprio manifesto
    if c and c.lower() != t.lower():
        if t and c.lower().startswith(t.lower()):
            c = c[len(t):].lstrip(" .-–—:")
        if c:
            partes.append(c)
            prov.append({"metodo": origem or "body", "chars": len(c)})

    texto = pc.normalizar(" ".join(partes))
    ganho = len(texto) - len(t)
    return {
        "texto": texto,
        "tier": tier or ("TITLE_ONLY" if not corpo else "THIN"),
        "origem_do_corpo": origem or "nenhuma",
        "chars": len(texto),
        "chars_alem_do_titulo": max(0, ganho),
        "sentence_like_count": _sent(texto),
        "unique_meaningful_tokens": _toks(texto),
        "provenance": prov,
        "content_hash": pc.hash_input(texto),
        "input_version": INPUT_VERSION,
        "normalization_version": pc.NORMALIZATION_VERSION,
    }


def trilha(inp: dict) -> str:
    return (RICH if inp["chars_alem_do_titulo"] >= RICH_MIN_CHARS
            and inp["sentence_like_count"] >= RICH_MIN_SENT else DEGRADED)


# ── controles com verdade humana (lado da AVALIAÇÃO, nunca do payload) ──────
def carregar_verdade() -> dict:
    """url||company||event -> verdade humana. Fica FORA do payload."""
    out = {}
    p1 = Path("test_fixtures_reliability/ma_transaction_reviews.json")
    p2 = Path("test_fixtures_reliability/occurrence_currentness_reviews.json")
    for p, campo in ((p1, "scoreable_as_ma"), (p2, "human_scoreable")):
        if not p.exists():
            continue
        for k, v in json.load(io.open(p, encoding="utf-8")).items():
            if k == "_meta":
                continue
            out[k] = {
                "fonte": p.name,
                "company": v.get("company"),
                "title": v.get("title") or "",
                "event_id": v.get("event_id"),
                "stratum": v.get("stratum") or "S2",
                "human_scoreable": v.get(campo),
                "human_label": v.get("human_label"),
                "failure_dimension": v.get("failure_dimension"),
                "s3_family": v.get("s3_family"),
                "reviewer_type": v.get("reviewer_type"),
            }
    return out


def _achar(arts: dict, url: str, titulo: str = ""):
    """Localiza o artigo do controle. Casa por URL e, em último caso, pelo
    TÍTULO EXATO da fixture.

    A primeira versão caía para "primeiro artigo cujo título cita a empresa" e
    isso trocou silenciosamente dois controles S2 por matérias diferentes da
    mesma empresa — um controle adjudicado apontando para outro artigo é pior
    do que controle nenhum. Não achou: devolve vazio e o item é registrado
    como ausente do corpus.
    """
    if url in arts:
        return url, arts[url]
    alvo = pc.normalizar(titulo).lower()
    if len(alvo) >= 25:
        for u, r in sorted(arts.items()):
            if pc.normalizar(r.get("title") or "").lower() == alvo:
                return u, r
    return None, None


# ── composição da amostra ───────────────────────────────────────────────────
# Alvos por estrato. Pequenos de propósito: primeiro piloto mede sinal, não
# constrói benchmark. Os controles humanos são OBRIGATÓRIOS; o resto preenche
# diversidade e é HOLDOUT — nenhuma regra foi construída a partir deles.
ALVO_HOLDOUT = {"S6": 6, "S1": 3, "S4": 4, "S5": 2}


def _decisao(rec, emp, ev, cfg, kws, al):
    d = sa.resolve_article_semantics(
        rec.get("title") or "", rec.get("summary") or "", emp, [ev], al,
        article_year=sa._ano_do_registro(rec),
        source_domain=rec.get("domain") or "",
        keywords_por_evento=kws, country=rec.get("country") or "")["decisoes"][0]
    return d


def construir() -> dict:
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    arts = hist["articles"]
    cfg = rd.load_config("config_risco.yaml")
    kws = sa._keywords_por_evento(cfg)
    al = sa._aliases_map(cfg)
    fontes = montar_fontes()
    verdade = carregar_verdade()

    itens, vistos = [], set()

    def add(url, rec, empresa, evento, stratum, papel, origem_selecao):
        ident = f"{url}||{empresa}||{evento}"
        if ident in vistos:
            return
        vistos.add(ident)
        inp = melhor_input(url, rec, fontes)
        d = _decisao(rec, empresa, evento, cfg, kws, al) if evento else {}
        gt = verdade.get(ident)
        itens.append({
            "sample_id": f"P1-{len(itens) + 1:03d}",
            "article_id": hashlib.sha256(url.encode()).hexdigest()[:16],
            "url": url,
            "title": rec.get("title") or "",
            "source": rec.get("source") or "",
            "domain": rec.get("domain") or "",
            "pub_iso": rec.get("pub_iso") or rec.get("date") or "",
            "company": empresa,
            "candidate_events": [evento] if evento else [],
            "stratum": stratum,
            "role": papel,
            "input_track": trilha(inp),
            "input": inp,
            "selection_reason": origem_selecao,
            # LADO DA AVALIAÇÃO — nunca entra em payload
            "evaluation_only": {
                "human_truth": gt,
                "deterministic": {
                    "scoreable": bool(d.get("scoreable")) if d else None,
                    "rule": d.get("attribution_rule") or "" if d else "",
                    "reason": (d.get("rejection_reason") or "")[:160] if d else "",
                } if evento else None,
            },
        })

    # 1) CONTROLES HUMANOS — obrigatórios, DEV_CONTROL
    for chave, gt in sorted(verdade.items()):
        url = chave.split("||")[0]
        u, rec = _achar(arts, url, gt.get("title") or "")
        if not rec:
            # o controle existe como verdade mas o artigo não está no corpus
            # de produção (S2 prospectivo). Registrado, não silenciado.
            itens.append({
                "sample_id": f"P1-X{len(itens) + 1:03d}",
                "article_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "url": url, "title": "", "company": gt.get("company"),
                "candidate_events": [gt.get("event_id")],
                "stratum": gt.get("stratum"), "role": DEV_CONTROL,
                "input_track": DEGRADED,
                "input": {"texto": "", "chars": 0, "tier": "AUSENTE_DO_CORPUS",
                          "chars_alem_do_titulo": 0, "sentence_like_count": 0,
                          "unique_meaningful_tokens": 0, "provenance": [],
                          "content_hash": "", "input_version": INPUT_VERSION,
                          "origem_do_corpo": "nenhuma",
                          "normalization_version": pc.NORMALIZATION_VERSION},
                "selection_reason": "controle humano sem artigo no history",
                "evaluation_only": {"human_truth": gt, "deterministic": None},
            })
            continue
        add(u, rec, gt.get("company"), gt.get("event_id"),
            gt.get("stratum"), DEV_CONTROL, "controle com verdade humana")

    # 2) HOLDOUT — diversidade, sem verdade humana, nenhuma regra derivada daí
    pool = collections.defaultdict(list)
    for url in sorted(arts):
        rec = arts[url]
        comps = list(rec.get("companies") or [])
        evs = list(rec.get("event_ids") or [])
        for emp in comps:
            if not evs:
                pool["S6"].append((url, rec, emp, ""))
                continue
            for ev in evs:
                if ev in sa.EVENTOS_MA:
                    pool["S1"].append((url, rec, emp, ev))
                elif ev in ("emissao_divida", "troca_ceo", "rebaixamento_rating",
                            "incidente_operacional"):
                    pool["S4"].append((url, rec, emp, ev))
                elif ev in sa.EVENTOS_FRAUDE or ev in (
                        "investigacao_regulatoria", "investigacao_gestora",
                        "falencia", "recuperacao_judicial"):
                    if len(comps) > 1:
                        pool["S5"].append((url, rec, emp, ev))

    for est, alvo in ALVO_HOLDOUT.items():
        cands = []
        for url, rec, emp, ev in pool.get(est, []):
            if f"{url}||{emp}||{ev}" in vistos:
                continue
            inp = melhor_input(url, rec, fontes)
            cands.append((inp["chars_alem_do_titulo"], url, rec, emp, ev, inp))
        # prioriza input real; empate resolvido por article_id -> determinístico
        cands.sort(key=lambda x: (-x[0], hashlib.sha256(x[1].encode()).hexdigest()))
        dominios, empresas, n = set(), set(), 0
        for ganho, url, rec, emp, ev, inp in cands:
            if n >= alvo:
                break
            dom = rec.get("domain") or "?"
            # diversidade: no máximo 1 por domínio e 1 por empresa dentro do
            # estrato, senão a amostra vira um retrato de um publisher só
            if dom in dominios or emp in empresas:
                continue
            dominios.add(dom)
            empresas.add(emp)
            n += 1
            add(url, rec, emp, ev, est, HOLDOUT,
                f"holdout {est}: maior input disponível, 1 por domínio/empresa")

    return {
        "_meta": {
            "sample_version": SAMPLE_VERSION,
            "input_version": INPUT_VERSION,
            "normalization_version": pc.NORMALIZATION_VERSION,
            "prompt_version": pc.PROMPT_VERSION,
            "schema_version": pc.SCHEMA_VERSION,
            "input_policy_version": pc.INPUT_POLICY_VERSION,
            "arquitetura": pc.ARCH_A,
            "corpus": {
                "risk_history.json": _sha(HISTORY),
                "risk_input_shadow.json": _sha(SHADOW),
                "risk_enrichment_shadow.json": _sha(ENRICH),
                "s3_experimental": _sha(S3EXP),
                "history_run_count": hist.get("run_count"),
                "history_articles": len(arts),
            },
            "determinismo": "ordenação por article_id/sha; sem random, sem seed",
            "trilhas": {"rich": f"ganho>={RICH_MIN_CHARS} e frases>={RICH_MIN_SENT}",
                        "degraded": "o resto"},
            "aviso": "evaluation_only NUNCA entra em payload enviado ao modelo",
        },
        "itens": itens,
    }


# ── contagem de chamadas e volume ───────────────────────────────────────────
def plano_de_chamadas(man: dict) -> dict:
    """AUDIT é por empresa×artigo; DISCOVERY é por ARTIGO — um artigo com três
    empresas monitoradas continua sendo UMA descoberta, e o casamento com a
    watchlist é local."""
    itens = man["itens"]
    audit = [i for i in itens if i["company"] and i["candidate_events"]
             and i["input"]["chars"] > 0]
    artigos = {}
    for i in itens:
        if i["input"]["chars"] > 0:
            artigos.setdefault(i["article_id"], i)
    disc = list(artigos.values())

    def _vol(lst):
        c = sum(i["input"]["chars"] for i in lst)
        return {"chars": c, "tokens_estimados": round(c / 4)}

    por_trilha = collections.Counter(i["input_track"] for i in itens)
    return {
        "audit_pairs": len(audit),
        "discovery_articles": len(disc),
        "total_calls": len(audit) + len(disc),
        "calls_rich": sum(1 for i in audit if i["input_track"] == RICH)
                      + sum(1 for i in disc if i["input_track"] == RICH),
        "calls_degraded": sum(1 for i in audit if i["input_track"] == DEGRADED)
                          + sum(1 for i in disc if i["input_track"] == DEGRADED),
        "itens_por_trilha": dict(por_trilha),
        "itens_sem_input": sum(1 for i in itens if i["input"]["chars"] == 0),
        "volume_audit": _vol(audit),
        "volume_discovery": _vol(disc),
        "aproximacao_de_token": "chars/4 — heurística explícita; nenhum "
                                "tokenizer do provider foi consultado e nenhum "
                                "pacote foi instalado para isto",
        "output_token_cap_sugerido": 900,
    }


def main() -> int:
    man = construir()
    plano = plano_de_chamadas(man)
    man["_meta"]["plano_de_chamadas"] = plano

    if "--verificar" in sys.argv:
        if not MANIFESTO.exists():
            print("manifesto ausente — rode sem --verificar primeiro")
            return 1
        antigo = json.load(io.open(MANIFESTO, encoding="utf-8"))
        a = json.dumps([i["sample_id"] + i["url"] + i["input"]["content_hash"]
                        for i in antigo["itens"]], ensure_ascii=False)
        b = json.dumps([i["sample_id"] + i["url"] + i["input"]["content_hash"]
                        for i in man["itens"]], ensure_ascii=False)
        igual = a == b
        print(f"REPRODUTIBILIDADE: {'IDENTICA' if igual else 'DIVERGENTE'}")
        print(f"  itens antes/depois: {len(antigo['itens'])}/{len(man['itens'])}")
        return 0 if igual else 1

    OUTDIR.mkdir(parents=True, exist_ok=True)
    json.dump(man, io.open(MANIFESTO, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    est = collections.Counter(i["stratum"] for i in man["itens"])
    papel = collections.Counter(i["role"] for i in man["itens"])
    print("=" * 96)
    print(f"PILOT-1 SAMPLE — {SAMPLE_VERSION}")
    print("=" * 96)
    print(f"  itens: {len(man['itens'])} | artigos únicos: "
          f"{len({i['article_id'] for i in man['itens']})}")
    print(f"  estratos : {dict(sorted(est.items()))}")
    print(f"  papel    : {dict(papel)}")
    print(f"  trilhas  : {plano['itens_por_trilha']} | sem input: "
          f"{plano['itens_sem_input']}")
    print(f"  AUDIT pairs         : {plano['audit_pairs']}")
    print(f"  DISCOVERY articles  : {plano['discovery_articles']}")
    print(f"  TOTAL calls         : {plano['total_calls']} "
          f"(rich {plano['calls_rich']} · degraded {plano['calls_degraded']})")
    print(f"  volume AUDIT        : {plano['volume_audit']}")
    print(f"  volume DISCOVERY    : {plano['volume_discovery']}")
    print(f"  manifesto → {MANIFESTO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
