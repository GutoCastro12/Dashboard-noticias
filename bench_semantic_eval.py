#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reavaliação semântica OFFLINE do run 31758509054. ZERO chamadas.

POR QUE ESTE MÓDULO EXISTE

O benchmark reportou 2/8 para os dois modelos e o determinístico em 6/8. Antes
de concluir que os modelos são incapazes, é preciso descartar a hipótese mais
barata: a régua. A projeção anterior reduzia um contrato de 11 dimensões a

    pontuável ≈ (o sujeito casa com a empresa)

e, no caso Cemig, os dois modelos devolveram `company_role: SELLER` e
`related_entity: Âmbar Energia` — a informação certa estava lá e a régua a
jogou fora. Ela também lia `transaction_object`, um campo que NÃO EXISTE no
contrato: sempre `None`, nunca decidiu nada.

TRÊS NÍVEIS, SEPARADOS

    NÍVEL 1  EXTRAÇÃO      — os fatos vieram certos? (citações literais válidas)
    NÍVEL 2  INTERPRETAÇÃO — os campos representam papel/vigência/centralidade
                             corretamente?
    NÍVEL 3  PROJEÇÃO      — a função que vira pontuável/não-pontuável acertou?

O benchmark anterior misturou 2 e 3. Um erro de nível 3 é meu; um erro de
nível 2 é do modelo. A diferença decide se a próxima wave é de contrato ou de
provider.

A PROJEÇÃO É PRÉ-REGISTRADA

Cada porta abaixo vem de uma adjudicação que JÁ EXISTIA antes deste run — não
de tentativa e erro sobre estes 8 casos. Nenhuma regra menciona empresa ou
sample_id. Se as portas não bastarem para acertar um caso, o caso fica errado
e o erro é atribuído a quem o cometeu.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

ARTEFATO = Path("test_fixtures_reliability/bench_run_31758509054.json")
EVAL_VERSION = "bench.semantic.eval.v2"

# ── famílias de evento ──────────────────────────────────────────────────────
# `ma` é a única família com regra de papel específica, porque é a única para a
# qual existe adjudicação humana registrada sobre papel (família S3-A).
FAMILIA_MA = "ma"

# Papéis que NÃO atribuem a ocorrência à empresa monitorada, em qualquer
# família. Vêm da invariante de atribuição: evento de terceiro não pontua.
PAPEIS_NAO_ATRIBUTIVOS = frozenset({"MENTIONED", "UNRELATED", "UNKNOWN"})

# Papel que, na família M&A, indica que o SUJEITO da aquisição é a contraparte.
# Registrado na adjudicação S3-A_seller_role (Cemig/Âmbar). NÃO é uma afirmação
# de que desinvestimento é imaterial — é o alcance da taxonomia `ma` vigente.
PAPEL_MA_CONTRAPARTE = frozenset({"SELLER"})

CURRENTNESS_QUE_PONTUA = frozenset({"CURRENT"})
CENTRALIDADE_CONTEXTUAL = frozenset({"BACKGROUND", "INCIDENTAL"})
FASES_SEM_FATO = frozenset({"RUMOR"})

# Dimensões que o contrato NÃO representa. Um caso que dependa delas não é
# decidível pelos campos devolvidos, e isso é achado, não erro do modelo.
DIMENSOES_AUSENTES_DO_CONTRATO = ("transaction_object", "scope")


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def mesmo_ente(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


# ── PROJEÇÃO PRÉ-REGISTRADA (congelada antes de tocar o artefato) ───────────
def projetar_pontuavel(ev: dict, empresa: str, aliases=None,
                       event_id: str = "") -> dict:
    """Contrato → pontuável/não pontuável, combinando as dimensões.

    Devolve `{"pontuavel": bool|None, "porta": str, "motivo": str}` para que
    cada decisão seja auditável: saber QUAL porta fechou importa tanto quanto
    o booleano.
    """
    if not ev:
        return {"pontuavel": None, "porta": "SEM_EVENTO",
                "motivo": "o modelo não devolveu evento para este candidato"}

    nomes = [empresa] + list(aliases or [])

    # 1. ASSERÇÃO — palavra-chave cria candidato, nunca prova o fato.
    if ev.get("event_asserted") != "ASSERTED":
        return {"pontuavel": False, "porta": "ASSERCAO",
                "motivo": f"evento não afirmado ({ev.get('event_asserted')})"}

    # 2. FASE — rumor não é fato consumado.
    if ev.get("phase") in FASES_SEM_FATO:
        return {"pontuavel": False, "porta": "FASE",
                "motivo": f"fase {ev.get('phase')} não consuma ocorrência"}

    # 3. VIGÊNCIA — histórico/follow-up não cria NOVA ocorrência.
    if ev.get("currentness") not in CURRENTNESS_QUE_PONTUA:
        return {"pontuavel": False, "porta": "VIGENCIA",
                "motivo": f"vigência {ev.get('currentness')} não é nova ocorrência"}

    # 4. CENTRALIDADE — evento meramente contextual não vira ocorrência.
    if ev.get("centrality") in CENTRALIDADE_CONTEXTUAL:
        return {"pontuavel": False, "porta": "CENTRALIDADE",
                "motivo": f"centralidade {ev.get('centrality')} é contexto"}

    # 5. PAPEL NÃO-ATRIBUTIVO — em qualquer família.
    if ev.get("company_role") in PAPEIS_NAO_ATRIBUTIVOS:
        return {"pontuavel": False, "porta": "PAPEL",
                "motivo": f"papel {ev.get('company_role')} não atribui à empresa"}

    # 6. ATRIBUIÇÃO — o sujeito precisa ser a empresa monitorada.
    #    Quando o papel já diz que a contraparte é o sujeito (M&A/SELLER), o
    #    `subject` textual é irrelevante: o papel é a informação mais forte.
    papel = ev.get("company_role")
    if event_id == FAMILIA_MA and papel in PAPEL_MA_CONTRAPARTE:
        alvo = ev.get("related_entity") or "contraparte"
        return {"pontuavel": False, "porta": "SUJEITO_E_PAPEL",
                "motivo": f"em M&A o papel {papel} põe o sujeito da aquisição "
                          f"na contraparte ({alvo})"}

    sujeito = ev.get("subject") or ""
    if sujeito and not any(mesmo_ente(sujeito, n) for n in nomes):
        return {"pontuavel": False, "porta": "SUJEITO",
                "motivo": f"o sujeito ({sujeito}) não é a empresa monitorada"}

    return {"pontuavel": True, "porta": "NENHUMA",
            "motivo": "todas as portas passaram"}


# ── VERDADE DIMENSIONAL ─────────────────────────────────────────────────────
# Vem das adjudicações humanas já registradas, não dos outputs. Onde a verdade
# admite mais de um valor aceitável, todos são listados; onde ela não se
# pronuncia, o campo é None e a dimensão não é aplicável àquele caso.
VERDADE = {
    "P1-002": {  # Cemig — Âmbar conclui a aquisição de hidrelétricas da Cemig
        "evento_existe": True,
        "sujeito": ["Âmbar", "Ambar", "Âmbar Energia"],
        "papel": ["SELLER"],
        "terceiro": ["Âmbar", "Ambar", "Âmbar Energia"],
        "vigencia": ["CURRENT"],
        "fase": ["CONCLUDED", "COMPLETED", "RESOLVED"],
        "centralidade": ["MAIN"],
        "objeto": "ativo (hidrelétricas), não controle societário",
    },
    "P1-003": {  # Sabesp/Emae — aquisição real, artigo é follow-up regulatório
        "evento_existe": True,
        "sujeito": ["Sabesp"],
        "papel": ["BUYER", "SUBJECT"],
        "terceiro": ["Emae"],
        "vigencia": ["HISTORICAL"],
        "fase": None,
        "centralidade": ["MAIN"],
        "objeto": None,
    },
    "P1-004": {  # BTG — aquisição de fazenda, não M&A societário
        "evento_existe": True,
        "sujeito": ["BTG"],
        "papel": ["BUYER", "SUBJECT"],
        "terceiro": None,
        "vigencia": ["CURRENT"],
        "fase": None,
        "centralidade": ["MAIN"],
        "objeto": "imóvel rural, não controle societário",
    },
    "P1-005": {  # B3 — "novo CEO" é descritor, não nova troca
        "evento_existe": False,
        "sujeito": None,
        "papel": None,
        "terceiro": None,
        "vigencia": ["HISTORICAL"],
        "fase": None,
        "centralidade": ["BACKGROUND", "INCIDENTAL"],
        "objeto": None,
    },
    "P1-007": {  # Grupo Security — fusão concluída, artigo é naming posterior
        "evento_existe": True,
        "sujeito": None,
        "papel": ["TARGET", "SUBJECT"],
        "terceiro": ["Bicecorp"],
        "vigencia": ["HISTORICAL"],
        "fase": ["CONCLUDED", "COMPLETED", "RESOLVED"],
        "centralidade": ["MAIN"],
        "objeto": None,
    },
    "P1-008": {  # YPF — a falência é da Aconcagua, terceiro
        "evento_existe": True,
        "sujeito": ["Aconcagua"],
        "papel": ["UNRELATED", "MENTIONED"],
        "terceiro": ["Aconcagua"],
        "vigencia": ["HISTORICAL"],
        "fase": None,
        "centralidade": ["BACKGROUND", "INCIDENTAL"],
        "objeto": None,
    },
    "P1-009": {  # PRIO — o evento central é rating; M&A é contexto causal
        "evento_existe": True,
        "sujeito": ["PRIO", "Prio"],
        "papel": ["BUYER", "SUBJECT"],
        "terceiro": None,
        "vigencia": ["HISTORICAL"],
        "fase": None,
        "centralidade": ["BACKGROUND", "INCIDENTAL"],
        "objeto": None,
    },
    "P1-010": {  # TIM — aquisição real anterior; artigo é follow-up estratégico
        "evento_existe": True,
        "sujeito": ["TIM"],
        "papel": ["BUYER", "SUBJECT", "TARGET"],
        "terceiro": None,
        "vigencia": ["HISTORICAL"],
        "fase": None,
        "centralidade": ["BACKGROUND", "INCIDENTAL"],
        "objeto": None,
    },
}

DIMENSOES = ("sujeito", "papel", "terceiro", "vigencia", "fase",
             "centralidade")
CRITICAS = ("sujeito", "papel", "terceiro", "vigencia")


def _bate(valor, aceitos) -> bool:
    if not aceitos:
        return False
    v = str(valor or "")
    return any(mesmo_ente(v, a) or v == a for a in aceitos)


def avaliar_dimensoes(ev: dict, verdade: dict) -> dict:
    """Cada dimensão separada, com aplicabilidade explícita.

    Denominador NUNCA é 8 para tudo: uma dimensão que a verdade não fixa não
    é aplicável, e penalizar o modelo por ela seria inventar erro.
    """
    out = {}
    if not ev:
        return {d: {"aplicavel": bool(verdade.get(d)), "correto": False,
                    "devolvido": None} for d in DIMENSOES}
    campo = {"sujeito": "subject", "papel": "company_role",
             "terceiro": "related_entity", "vigencia": "currentness",
             "fase": "phase", "centralidade": "centrality"}
    for d in DIMENSOES:
        aceitos = verdade.get(d)
        devolvido = ev.get(campo[d])
        out[d] = {"aplicavel": bool(aceitos),
                  "correto": bool(aceitos) and _bate(devolvido, aceitos),
                  "devolvido": devolvido}
    return out


CONTRACT_GAP = "CONTRACT_GAP"
MODEL_EXTRACTION_ERROR = "MODEL_EXTRACTION_ERROR"
MODEL_SEMANTIC_ERROR = "MODEL_SEMANTIC_ERROR"
PROJECTION_ERROR = "PROJECTION_ERROR"


def classificar_erro(sample_id: str, dims: dict, evidencia_ok: bool,
                     projecao: dict, humano: bool) -> str | None:
    """A quem pertence o erro final. Distinção que decide a próxima wave."""
    if projecao["pontuavel"] == humano:
        return None
    verdade = VERDADE.get(sample_id) or {}
    if verdade.get("objeto") and "não controle societário" in verdade["objeto"]:
        # a decisão depende de objeto/escopo, que o contrato não representa
        return CONTRACT_GAP
    if not evidencia_ok:
        return MODEL_EXTRACTION_ERROR
    criticas_erradas = [d for d in CRITICAS
                        if dims[d]["aplicavel"] and not dims[d]["correto"]]
    if criticas_erradas:
        return MODEL_SEMANTIC_ERROR
    return PROJECTION_ERROR


# ── aplicação ao artefato congelado ─────────────────────────────────────────
def carregar_artefato(caminho: Path = ARTEFATO) -> dict:
    return json.load(io.open(caminho, encoding="utf-8"))


def evento_do_caso(linha: dict) -> dict | None:
    eventos = ((linha.get("validacao") or {}).get("eventos") or [])
    alvo = linha.get("event_id")
    for ev in eventos:
        if ev.get("event_id") == alvo:
            return ev
    return eventos[0] if eventos else None


def reavaliar(artefato: dict) -> dict:
    saida = {"eval_version": EVAL_VERSION, "por_modelo": {},
             "provider_calls": 0}
    for modelo, dados in artefato["por_modelo"].items():
        casos = []
        for linha in dados["linhas"]:
            if linha["call_type"] != "AUDIT":
                continue
            sid = linha["sample_id"]
            verdade = VERDADE.get(sid) or {}
            ev = evento_do_caso(linha)
            dims = avaliar_dimensoes(ev, verdade)
            evid = bool((linha.get("validacao") or {}).get("ok"))
            proj = projetar_pontuavel(ev, linha.get("company") or "",
                                      [], linha.get("event_id") or "")
            humano = linha["human_scoreable"]
            casos.append({
                "sample_id": sid, "company": linha["company"],
                "event_id": linha["event_id"],
                "human_scoreable": humano,
                "deterministic_scoreable": linha["deterministic_scoreable"],
                "projecao_antiga": linha["llm_scoreable"],
                "projecao_nova": proj["pontuavel"],
                "porta": proj["porta"], "motivo": proj["motivo"],
                "acertou_antiga": linha["llm_scoreable"] == humano,
                "acertou_nova": proj["pontuavel"] == humano,
                "evidencia_ok": evid,
                "dimensoes": dims,
                "erro": classificar_erro(sid, dims, evid, proj, humano),
            })
        agreg = {}
        for d in DIMENSOES:
            apl = [c for c in casos if c["dimensoes"][d]["aplicavel"]]
            agreg[d] = {"corretos": sum(1 for c in apl
                                        if c["dimensoes"][d]["correto"]),
                        "aplicaveis": len(apl)}
        crit_apl = [(c, d) for c in casos for d in CRITICAS
                    if c["dimensoes"][d]["aplicavel"]]
        saida["por_modelo"][modelo] = {
            "casos": casos,
            "dimensoes": agreg,
            "criticas": {"corretos": sum(1 for c, d in crit_apl
                                         if c["dimensoes"][d]["correto"]),
                         "aplicaveis": len(crit_apl)},
            "evidencia_valida": sum(1 for c in casos if c["evidencia_ok"]),
            "projecao_antiga": sum(1 for c in casos if c["acertou_antiga"]),
            "projecao_nova": sum(1 for c in casos if c["acertou_nova"]),
            "total": len(casos),
            "erros": {k: sum(1 for c in casos if c["erro"] == k)
                      for k in (MODEL_EXTRACTION_ERROR, MODEL_SEMANTIC_ERROR,
                                PROJECTION_ERROR, CONTRACT_GAP)},
        }
    return saida


def main() -> int:
    art = carregar_artefato()
    res = reavaliar(art)
    Path("out_bench_freellm").mkdir(parents=True, exist_ok=True)
    json.dump(res, io.open("out_bench_freellm/reavaliacao.json", "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=" * 100)
    print(f"REAVALIAÇÃO OFFLINE — {EVAL_VERSION} — ZERO chamadas ao provider")
    print("=" * 100)
    for m, d in res["por_modelo"].items():
        print(f"\n{m}")
        print(f"  projeção ANTIGA: {d['projecao_antiga']}/{d['total']}   "
              f"projeção NOVA: {d['projecao_nova']}/{d['total']}")
        print(f"  dimensões críticas: {d['criticas']['corretos']}"
              f"/{d['criticas']['aplicaveis']}")
        print("  por dimensão: " + " · ".join(
            f"{k}={v['corretos']}/{v['aplicaveis']}"
            for k, v in d["dimensoes"].items()))
        print(f"  evidência válida: {d['evidencia_valida']}/{d['total']}")
        print(f"  atribuição de erro: {d['erros']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
