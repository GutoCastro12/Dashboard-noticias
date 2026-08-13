#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark entre modelos Gemini gratuitos, medido contra verdade humana.

A PERGUNTA

Qual modelo gratuito é melhor NA NOSSA TAREFA — não qual é mais novo, maior ou
mais rápido. A tarefa é decidir, para um par artigo×empresa, se um evento
candidato é pontuável, quem é o sujeito, qual o papel da empresa e se o fato é
vigente. Já existe verdade humana adjudicada para isso.

REGRAS QUE NÃO SE NEGOCIAM

• A verdade humana NUNCA entra no prompt. Ela vive só do lado da avaliação.
• Cada caso × modelo = no máximo UMA invocação. Zero retry.
• Zero fallback ENTRE OS MODELOS: uma falha de G1 jamais vira resposta de G2,
  senão não estamos medindo dois modelos, estamos medindo a união deles.
• Disjuntor POR MODELO. Cota é contada por projeto+modelo, então G1 esgotado
  não diz nada sobre G2 — parar os dois seria jogar fora metade da medição.
• Teto duro de invocações reais, contadas ANTES de sair.

MODOS

    dry   — monta tudo, audita vazamento, não chama ninguém
    mock  — caminho completo com provedor FALSO
    live  — chama de verdade (exige GEMINI_API_KEY)
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import time
from pathlib import Path

import reliability_pilot1_payloads as pp
import reliability_pilot1_sample as ps
import reliability_pilot_contract as pc
import reliability_pilot_validators as pv
import risk_dashboard as rd

BENCH_VERSION = "bench.freellm.v1"
G1 = "gemini-3.1-flash-lite"
G2 = "gemini-3.5-flash-lite"
MODELOS = (G1, G2)

# gemini-3.6-flash fica FORA de propósito: é justamente o modelo cuja cota
# gratuita se mostrou insuficiente no cron 31738417162. Medi-lo de novo
# responderia uma pergunta que já foi respondida.
EXCLUIDO = "gemini-3.6-flash"

MAX_PROVIDER_CALLS = 30
CONFIRMACAO = "EXECUTAR-BENCHMARK"

# O cap antigo de 900 tokens nunca foi provado suficiente para este schema. São
# 11 dimensões, cada uma com uma citação literal, e um artigo pode trazer mais
# de um evento. Estimativa explícita: ~450 tokens por evento no pior caso, até
# 3 eventos, mais folga de envelope. O MESMO cap vale para os dois modelos.
OUTPUT_TOKEN_CAP = 1600

OUTDIR = Path("out_bench_freellm")
RESULTADO = OUTDIR / "bench_freellm.json"

# Casos com verdade humana E texto suficiente. Quem não tem input não entra e
# NÃO é substituído por um caso parecido — trocar item de amostra por
# conveniência já quase contaminou uma medição desta mesma família.
AUDIT_CASES = ("P1-002", "P1-003", "P1-004", "P1-005",
               "P1-007", "P1-008", "P1-009", "P1-010")
DISCOVERY_CASES = ("P1-011", "P1-013", "P1-014")

# ── mini-benchmark de TRADUÇÃO ──────────────────────────────────────────────
# Tarefa diferente da semântica, então vencedor possivelmente diferente. Os
# lotes são fixos e vêm do mesmo manifesto congelado: um em espanhol, um em
# inglês, escolhidos por carregarem o que de fato pode ser corrompido —
# entidades (YPF, Cencosud, Banorte, CVS Health, UnitedHealth), valores
# ($440 million), e terminologia jurídico-financeira (quiebra,
# reestructuración, Bankruptcy Court, Merger Probe, settlement).
LOTES_TRADUCAO = (
    ("es", ("P1-007", "P1-008", "P1-011", "P1-012", "P1-013")),
    ("en", ("P1-014", "P1-016", "P1-018")),
)

# Entidades e números que TÊM que sobreviver à tradução, por lote. Não é lista
# exaustiva: é o que um erro de tradução destruiria de forma verificável.
ENTIDADES_ESPERADAS = {
    "P1-007": ("Bicecorp", "Security"), "P1-008": ("YPF", "Aconcagua"),
    "P1-011": ("Banorte", "CDMX"), "P1-012": ("Cencosud",),
    "P1-013": ("Aconcagua",), "P1-014": ("Texas",),
    "P1-016": ("Omnicare", "CVS Health", "440"), "P1-018": ("UnitedHealth", "DOJ"),
}

PRODUCAO = ("risk_history.json", "risk_enrichment_shadow.json",
            "risk_input_shadow.json", "index.html", "dashboard_risco.html",
            "run_meta.json", "config_risco.yaml")


def hashes_de_producao() -> dict:
    import hashlib
    out = {}
    for f in PRODUCAO:
        p = Path(f)
        out[f] = (hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                  if p.exists() else "")
    return out


# ── seleção ─────────────────────────────────────────────────────────────────
def montar_plano(man: dict, cfg: dict) -> tuple[list, list]:
    """Um plano declarado: (entradas, ausentes). Ausência é ausência."""
    pl = pp.construir_payloads(man, cfg)
    por = {(e["sample_id"], e["call_type"]): e
           for e in pl["audit"] + pl["discovery"]}
    entradas, ausentes = [], []
    for sid in AUDIT_CASES:
        e = por.get((sid, pc.CALL_AUDIT))
        (entradas if e else ausentes).append(e or {"sample_id": sid,
                                                   "call_type": pc.CALL_AUDIT})
    for sid in DISCOVERY_CASES:
        e = por.get((sid, pc.CALL_DISCOVERY))
        (entradas if e else ausentes).append(
            e or {"sample_id": sid, "call_type": pc.CALL_DISCOVERY})
    return entradas, ausentes


# ── avaliação contra a verdade humana ───────────────────────────────────────
def _pior_evento(saida: dict, event_id: str) -> dict | None:
    for ev in (saida or {}).get("events", []) or []:
        if ev.get("event_id") == event_id:
            return ev
    return ((saida or {}).get("events") or [None])[0]


def _mesmo_ente(a: str, b: str) -> bool:
    """Comparação tolerante de nomes: 'Cemig' ≟ 'Companhia Energética…'."""
    na = "".join(ch for ch in (a or "").lower() if ch.isalnum() or ch == " ").strip()
    nb = "".join(ch for ch in (b or "").lower() if ch.isalnum() or ch == " ").strip()
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def llm_considera_pontuavel(ev: dict | None, empresa: str = "",
                            aliases: list | None = None) -> bool | None:
    """Projeta o contrato de 11 dimensões numa decisão binária comparável.

    Não é o score de produção e não vira score: é a projeção mínima necessária
    para confrontar um veredito humano que é binário.

    O teste decisivo é o SUJEITO, não o papel. Um papel como SELLER não é
    intrinsecamente não-pontuável — vender ativo pode ser material. O que torna
    o caso Cemig um falso positivo é outro: o sujeito da aquisição é a Âmbar, e
    o evento estava sendo atribuído à Cemig. Ancorar no sujeito cobre esse caso
    e também o da YPF (falência que pertence a um terceiro) sem inventar uma
    regra por papel que a verdade humana não sustenta.
    """
    if not ev:
        return None
    if ev.get("event_asserted") != "ASSERTED":
        return False
    if ev.get("currentness") != "CURRENT":
        return False
    if ev.get("centrality") in ("BACKGROUND", "INCIDENTAL"):
        return False
    if ev.get("company_role") in ("MENTIONED", "UNRELATED", "UNKNOWN"):
        return False
    sujeito = ev.get("subject") or ""
    if sujeito and empresa:
        nomes = [empresa] + list(aliases or [])
        if not any(_mesmo_ente(sujeito, n) for n in nomes):
            return False          # o evento é de outra entidade
    return True


def avaliar_caso(item: dict, saida: dict | None, estado: str) -> dict:
    """Compara com a verdade humana SEM nunca tê-la mostrado ao modelo."""
    vh = (item.get("evaluation_only") or {}).get("human_truth") or {}
    det = (item.get("evaluation_only") or {}).get("deterministic") or {}
    eid = (item.get("candidate_events") or [""])[0]
    ev = _pior_evento(saida, eid) if estado == "OK" else None
    llm_pont = llm_considera_pontuavel(ev, item.get("company") or "",
                                       item.get("aliases") or [])
    humano = vh.get("human_scoreable")
    return {
        "sample_id": item["sample_id"],
        "company": item.get("company"),
        "event_id": eid,
        "stratum": item.get("stratum"),
        "input_track": item.get("input_track"),
        "role": item.get("role"),
        "estado": estado,
        "human_scoreable": humano,
        "human_label": vh.get("human_label"),
        "failure_dimension": vh.get("failure_dimension"),
        "s3_family": vh.get("s3_family"),
        "deterministic_scoreable": det.get("scoreable"),
        "deterministic_rule": det.get("rule"),
        "llm_scoreable": llm_pont,
        "acertou": (None if (llm_pont is None or humano is None)
                    else llm_pont == humano),
        "llm_subject": (ev or {}).get("subject"),
        "llm_company_role": (ev or {}).get("company_role"),
        "llm_currentness": (ev or {}).get("currentness"),
        "llm_phase": (ev or {}).get("phase"),
        "llm_centrality": (ev or {}).get("centrality"),
        "llm_event_asserted": (ev or {}).get("event_asserted"),
        "llm_transaction_object": (ev or {}).get("transaction_object"),
    }


# ── tradução: montagem e avaliação ──────────────────────────────────────────
def montar_lotes_traducao(man: dict) -> list[dict]:
    """Lotes fixos, com o MESMO prompt que a produção usa de verdade.

    Medir a tradução com um prompt inventado só para o benchmark responderia
    sobre um sistema que não existe.
    """
    porid = {i["sample_id"]: i for i in man["itens"]}
    lotes = []
    for idioma, ids in LOTES_TRADUCAO:
        itens, origem = [], []
        for n, sid in enumerate(ids):
            it = porid.get(sid)
            if not it:
                continue
            titulo = (it.get("title") or "")[:600]
            resumo = (it["input"].get("texto") or "")[:600]
            itens.append({"i": n, "lang": idioma, "title": titulo,
                          "summary": resumo})
            origem.append({"i": n, "sample_id": sid, "title": titulo,
                           "summary": resumo})
        prompt = (
            "Traduza para português do Brasil os campos 'title' e 'summary' das "
            "notícias financeiras abaixo. Preserve nomes próprios, tickers, números "
            "e siglas. Não interprete nem resuma: traduza fielmente. "
            "Responda SOMENTE com JSON no formato "
            '{"itens":[{"i":0,"title":"...","summary":"..."}]}.\n\n'
            + json.dumps(itens, ensure_ascii=False))
        lotes.append({"idioma": idioma, "itens": origem, "prompt": prompt,
                      "ids": [o["sample_id"] for o in origem]})
    return lotes


_NUM = __import__("re").compile(r"\d[\d.,]*")


def avaliar_traducao(lote: dict, saida: dict | None, estado: str) -> dict:
    """Fidelidade verificável: mapeamento, entidades e números.

    Não julga estilo. Julga o que a produção não pode perder: um índice que
    some quebra o mapeamento artigo→tradução; uma entidade ou um número que
    desaparece corrompe a evidência que o analista vai ler.
    """
    esperado = {o["i"]: o for o in lote["itens"]}
    itens = ((saida or {}).get("itens") or []) if estado == "OK" else []
    devolvidos = {}
    for x in itens:
        try:
            devolvidos[int(x["i"])] = x
        except (KeyError, ValueError, TypeError):
            continue

    faltando = sorted(set(esperado) - set(devolvidos))
    extras = sorted(set(devolvidos) - set(esperado))
    ent_ok, ent_perdidas, num_ok, num_perdidos, traduziu = 0, [], 0, [], 0
    for i, orig in esperado.items():
        got = devolvidos.get(i)
        if not got:
            continue
        texto = f"{got.get('title') or ''} {got.get('summary') or ''}"
        sid = orig["sample_id"]
        for ent in ENTIDADES_ESPERADAS.get(sid, ()):
            if ent.lower() in texto.lower():
                ent_ok += 1
            else:
                ent_perdidas.append(f"{sid}:{ent}")
        nums_orig = set(_NUM.findall(orig["title"]))
        for nm in nums_orig:
            if nm in texto:
                num_ok += 1
            else:
                num_perdidos.append(f"{sid}:{nm}")
        # tradução idêntica ao original = não traduziu
        if (got.get("title") or "").strip() != (orig["title"] or "").strip():
            traduziu += 1

    return {
        "idioma": lote["idioma"],
        "estado": estado,
        "itens_esperados": len(esperado),
        "itens_devolvidos": len(devolvidos),
        "mapeamento_completo": not faltando and not extras,
        "indices_faltando": faltando,
        "indices_extras": extras,
        "entidades_preservadas": ent_ok,
        "entidades_perdidas": ent_perdidas,
        "numeros_preservados": num_ok,
        "numeros_perdidos": num_perdidos,
        "titulos_efetivamente_traduzidos": traduziu,
    }


def chamada_traducao(genai, modelo: str, lote: dict) -> dict:
    """Uma única chamada, mesmo cap dos demais, sem schema (o contrato da
    produção para tradução é JSON livre — usar schema só aqui mediria outro
    sistema)."""
    t0 = time.time()
    try:
        m = genai.GenerativeModel(modelo)
        resp = m.generate_content(
            lote["prompt"],
            generation_config=genai.types.GenerationConfig(
                temperature=0.0, response_mime_type="application/json",
                max_output_tokens=OUTPUT_TOKEN_CAP),
            request_options={"timeout": 90})
        dt = round(time.time() - t0, 2)
        uso = {}
        try:
            um = getattr(resp, "usage_metadata", None)
            uso = {k: getattr(um, k, None) for k in
                   ("prompt_token_count", "candidates_token_count",
                    "total_token_count")} if um is not None else {}
        except Exception:
            uso = {}
        fim = {}
        try:
            c = (getattr(resp, "candidates", None) or [None])[0]
            fr = getattr(c, "finish_reason", None)
            fim = {"finish_reason_nome": getattr(fr, "name", None) or str(fr)}
        except Exception:
            fim = {}
        bruto = resp.text
        comum = {"latencia_s": dt, "uso": uso, "finish": fim,
                 "modelo_real": getattr(resp, "model_version", "") or "",
                 "raw_output": bruto}
        try:
            return {"estado": "OK", "saida": json.loads(bruto), **comum}
        except Exception:
            return {"estado": "JSON_INVALIDO", "saida": None, **comum}
    except Exception as exc:
        cls = classificar(exc)
        return {"estado": cls["classe"], "saida": None,
                "latencia_s": round(time.time() - t0, 2), "uso": {},
                "finish": {}, "modelo_real": "", "erro": cls}


# ── provider ────────────────────────────────────────────────────────────────
class ProvedorFalsoBench:
    """Roteiro determinístico; nunca abre rede."""

    class types:
        @staticmethod
        def GenerationConfig(**kw):
            return dict(kw)

    def __init__(self, roteiro):
        self.roteiro, self.invocacoes = list(roteiro), 0

    def configure(self, **kw):
        return None

    def GenerativeModel(self, modelo):
        prov = self

        class _M:
            def generate_content(_s, prompt, **kw):
                prov.invocacoes += 1
                passo = (prov.roteiro.pop(0) if prov.roteiro
                         else '{"events":[]}')
                if isinstance(passo, Exception):
                    raise passo
                return type("R", (), {
                    "text": passo,
                    "usage_metadata": type("U", (), {
                        "prompt_token_count": 500,
                        "candidates_token_count": 300,
                        "total_token_count": 800})(),
                    "candidates": [type("C", (), {
                        "finish_reason": type("F", (), {"name": "STOP"})()})()],
                    "model_version": "MOCK",
                    "prompt_feedback": None})()
        return _M()


def classificar(exc: Exception) -> dict:
    nome = type(exc).__name__
    msg = str(exc)
    baixo = msg.lower()
    diario = ("perday" in baixo.replace(" ", "").replace("-", "")
              or "requests per day" in baixo)
    if nome == "ResourceExhausted" or "429" in msg or "quota" in baixo:
        classe = "QUOTA_EXHAUSTED" if diario else "RATE_LIMITED"
    elif "no longer available" in baixo or "404" in msg:
        classe = "MODEL_UNAVAILABLE"
    elif "api key" in baixo or "unauthenticated" in baixo:
        classe = "AUTH_ERROR"
    else:
        classe = "UNKNOWN_PROVIDER_ERROR"
    return {"classe": classe, "excecao": nome, "mensagem": msg[:240],
            "interrompe": classe in ("QUOTA_EXHAUSTED", "RATE_LIMITED",
                                     "AUTH_ERROR", "MODEL_UNAVAILABLE")}


def chamada_unica(genai, modelo: str, ent: dict) -> dict:
    """Exatamente um generate_content. Structured output nos DOIS modelos."""
    t0 = time.time()
    try:
        m = genai.GenerativeModel(modelo)
        cfg_gen = {"temperature": 0.0,
                   "response_mime_type": "application/json",
                   "max_output_tokens": OUTPUT_TOKEN_CAP}
        esquema = (ent.get("payload") or {}).get("schema")
        if esquema:
            cfg_gen["response_schema"] = esquema
        resp = m.generate_content(
            ent["payload"]["prompt"],
            generation_config=genai.types.GenerationConfig(**cfg_gen),
            request_options={"timeout": 90})
        dt = round(time.time() - t0, 2)
        uso, fim = {}, {}
        try:
            um = getattr(resp, "usage_metadata", None)
            uso = {k: getattr(um, k, None) for k in
                   ("prompt_token_count", "candidates_token_count",
                    "total_token_count")} if um is not None else {}
        except Exception:
            uso = {}
        try:
            c = (getattr(resp, "candidates", None) or [None])[0]
            fr = getattr(c, "finish_reason", None)
            fim = {"finish_reason_nome": getattr(fr, "name", None) or str(fr)}
        except Exception:
            fim = {}
        bruto = resp.text
        comum = {"latencia_s": dt, "uso": uso, "finish": fim,
                 "modelo_real": getattr(resp, "model_version", "") or "",
                 "raw_output": bruto, "output_cap_solicitado": OUTPUT_TOKEN_CAP}
        try:
            return {"estado": "OK", "saida": json.loads(bruto), **comum}
        except Exception:
            return {"estado": "JSON_INVALIDO", "saida": None, **comum}
    except Exception as exc:
        cls = classificar(exc)
        return {"estado": cls["classe"], "saida": None,
                "latencia_s": round(time.time() - t0, 2), "uso": {},
                "finish": {}, "modelo_real": "", "erro": cls}


# ── execução ────────────────────────────────────────────────────────────────
def executar(modo: str, *, confirmado: bool, teto: int = MAX_PROVIDER_CALLS,
             espacamento_s: float = 8.0, provedores=None) -> dict:
    cfg = rd.load_config("config_risco.yaml")
    man = ps.carregar_manifesto()
    porid = {i["sample_id"]: i for i in man["itens"]}
    entradas, ausentes = montar_plano(man, cfg)

    vaz = []
    for ent in entradas:
        it = porid[ent["sample_id"]]
        r = pp.auditar_vazamento(
            ent, texto_do_artigo=it["input"]["texto"],
            cego_a_empresa=(ent["call_type"] == pc.CALL_DISCOVERY),
            empresa=(it.get("company") or ""))
        if not r["ok"]:
            vaz.append((ent["sample_id"], ent["call_type"], r["problemas"]))

    lotes_trad = montar_lotes_traducao(man)
    planejadas = (len(entradas) + len(lotes_trad)) * len(MODELOS)
    gates = {
        "lotes_traducao": [{"idioma": l["idioma"], "ids": l["ids"]}
                           for l in lotes_trad],
        "bench_version": BENCH_VERSION,
        "modelos": list(MODELOS),
        "modelo_excluido": EXCLUIDO,
        "vazamento": "OK" if not vaz else "FALHOU",
        "vazamentos": vaz,
        "itens_ausentes": ausentes,
        "casos": len(entradas),
        "chamadas_planejadas": planejadas,
        "teto": teto,
        "plano_dentro_do_teto": planejadas <= teto,
        "output_token_cap": OUTPUT_TOKEN_CAP,
        "structured_output": True,
        "retry": 0,
        "fallback_entre_modelos": 0,
    }
    if vaz or ausentes or planejadas > teto:
        return {"modo": modo, "estado": "ABORTADO_ANTES_DE_CHAMAR",
                "gates": gates, "por_modelo": {}, "provider_calls": 0}

    if modo == "dry":
        return {"modo": "dry", "estado": "OK", "gates": gates,
                "por_modelo": {}, "provider_calls": 0,
                "nota": "nenhuma chamada; apenas montagem e auditoria"}

    if modo == "live" and not confirmado:
        return {"modo": "live", "estado": "ABORTADO_SEM_CONFIRMACAO",
                "gates": gates, "por_modelo": {}, "provider_calls": 0,
                "nota": f"--confirm {CONFIRMACAO} é obrigatório"}

    simulado = (modo == "mock")
    if not simulado:
        chave = os.environ.get("GEMINI_API_KEY", "")
        if not chave:
            return {"modo": modo, "estado": "ABORTADO_SEM_CHAVE",
                    "gates": gates, "por_modelo": {}, "provider_calls": 0,
                    "nota": "GEMINI_API_KEY ausente — nada foi chamado"}
        try:
            import google.generativeai as genai
        except Exception as exc:
            return {"modo": modo, "estado": "ABORTADO_SEM_SDK",
                    "gates": gates, "por_modelo": {}, "provider_calls": 0,
                    "nota": f"SDK indisponível: {exc}"}
        genai.configure(api_key=chave)

    chamadas = 0
    por_modelo: dict = {}
    for modelo in MODELOS:
        prov = (provedores or {}).get(modelo) if simulado else genai
        if simulado and prov is None:
            prov = ProvedorFalsoBench([])
        linhas, contagem = [], collections.Counter()
        # DISJUNTOR POR MODELO: cota é por projeto+modelo.
        disjuntor = None
        for ent in entradas:
            item = porid[ent["sample_id"]]
            if disjuntor is not None:
                r = {"estado": f"SKIPPED_{disjuntor['classe']}", "saida": None,
                     "latencia_s": 0.0, "uso": {}, "finish": {}}
            elif chamadas >= teto:
                r = {"estado": "CALL_BUDGET_EXHAUSTED", "saida": None,
                     "latencia_s": 0.0, "uso": {}, "finish": {}}
            else:
                chamadas += 1              # conta ANTES de sair
                r = chamada_unica(prov, modelo, ent)
                erro = r.get("erro") or {}
                if erro.get("interrompe"):
                    disjuntor = {**erro, "sample_id": ent["sample_id"],
                                 "na_chamada": chamadas}
                elif espacamento_s > 0 and not simulado:
                    time.sleep(espacamento_s)   # pacing após QUALQUER tentativa

            val = {}
            estado = r["estado"]
            if estado == "OK":
                try:
                    if ent["call_type"] == pc.CALL_AUDIT:
                        val = pv.validar_audit(
                            r["saida"], texto=item["input"]["texto"],
                            organizacao=item["company"],
                            event_ids=item["candidate_events"])
                    else:
                        val = pv.validar_discovery(
                            r["saida"], texto=item["input"]["texto"])
                except Exception as exc:
                    val = {"ok": False, "erro": f"{type(exc).__name__}"}
                if not val.get("ok", True):
                    estado = "EVIDENCIA_INVALIDA"
            contagem[f"{ent['call_type'].lower()}:{estado}"] += 1
            linha = avaliar_caso(item, r.get("saida"), estado)
            linha.update({"call_type": ent["call_type"], "modelo": modelo,
                          "latencia_s": r.get("latencia_s"),
                          "uso": r.get("uso") or {}, "finish": r.get("finish"),
                          "validacao": val, "erro": r.get("erro")})
            linhas.append(linha)

        # ── tradução: mesmos lotes, mesmo teto, mesmo disjuntor ────────────
        trad = []
        for lote in lotes_trad:
            if disjuntor is not None:
                rt = {"estado": f"SKIPPED_{disjuntor['classe']}", "saida": None,
                      "latencia_s": 0.0, "uso": {}, "finish": {}}
            elif chamadas >= teto:
                rt = {"estado": "CALL_BUDGET_EXHAUSTED", "saida": None,
                      "latencia_s": 0.0, "uso": {}, "finish": {}}
            else:
                chamadas += 1
                rt = chamada_traducao(prov, modelo, lote)
                erro = rt.get("erro") or {}
                if erro.get("interrompe"):
                    disjuntor = {**erro, "sample_id": f"traducao:{lote['idioma']}",
                                 "na_chamada": chamadas}
                elif espacamento_s > 0 and not simulado:
                    time.sleep(espacamento_s)
            aval = avaliar_traducao(lote, rt.get("saida"), rt["estado"])
            aval.update({"modelo": modelo, "latencia_s": rt.get("latencia_s"),
                         "uso": rt.get("uso") or {}, "finish": rt.get("finish"),
                         "erro": rt.get("erro")})
            contagem[f"traducao:{rt['estado']}"] += 1
            trad.append(aval)

        audits = [l for l in linhas if l["call_type"] == pc.CALL_AUDIT]
        comparaveis = [l for l in audits if l["acertou"] is not None]
        por_modelo[modelo] = {
            "linhas": linhas,
            "traducao": trad,
            "traducao_resumo": {
                "lotes_ok": sum(1 for t in trad if t["estado"] == "OK"),
                "mapeamento_completo": sum(1 for t in trad
                                           if t["mapeamento_completo"]),
                "entidades_preservadas": sum(t["entidades_preservadas"] for t in trad),
                "entidades_perdidas": [e for t in trad for e in t["entidades_perdidas"]],
                "numeros_preservados": sum(t["numeros_preservados"] for t in trad),
                "numeros_perdidos": [n for t in trad for n in t["numeros_perdidos"]],
                "titulos_traduzidos": sum(t["titulos_efetivamente_traduzidos"]
                                          for t in trad),
            },
            "contagem": dict(contagem),
            "circuit_breaker": disjuntor,
            "invocacoes": (prov.invocacoes if simulado else None),
            "audits_comparaveis": len(comparaveis),
            "acertos": sum(1 for l in comparaveis if l["acertou"]),
            "schema_valido": sum(1 for l in linhas if l["estado"] != "JSON_INVALIDO"),
            "evidencia_valida": sum(1 for l in linhas
                                    if l["estado"] != "EVIDENCIA_INVALIDA"),
            "latencia_media": (round(sum(l["latencia_s"] or 0 for l in linhas)
                                     / max(1, len(linhas)), 2)),
            "tokens_saida": sum((l["uso"] or {}).get("candidates_token_count") or 0
                                for l in linhas),
        }

    return {"modo": modo, "estado": "OK", "gates": gates,
            "por_modelo": por_modelo,
            "provider_calls": 0 if simulado else chamadas,
            "invocacoes_simuladas": chamadas if simulado else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("dry", "mock", "live"), default="dry")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--inter-call-seconds", type=float, default=8.0)
    args = ap.parse_args()

    antes = hashes_de_producao()
    res = executar(args.mode, confirmado=(args.confirm == CONFIRMACAO),
                   espacamento_s=args.inter_call_seconds)
    depois = hashes_de_producao()
    res["_meta"] = {"bench_version": BENCH_VERSION,
                    "sample_version": ps.SAMPLE_VERSION,
                    "producao_antes": antes, "producao_depois": depois,
                    "producao_tocada": [f for f in PRODUCAO
                                        if antes[f] != depois[f]]}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    json.dump(res, io.open(RESULTADO, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    g = res["gates"]
    print("=" * 96)
    print(f"BENCH FREE-LLM — modo={res['modo']} estado={res['estado']}")
    print("=" * 96)
    print(f"  modelos: {g['modelos']} (excluído: {g['modelo_excluido']})")
    print(f"  casos: {g['casos']} | planejadas: {g['chamadas_planejadas']}"
          f"/{g['teto']} | vazamento: {g['vazamento']}")
    print(f"  chamadas reais ao provider: {res['provider_calls']}")
    for m, d in (res.get("por_modelo") or {}).items():
        print(f"    {m:26s} acertos={d['acertos']}/{d['audits_comparaveis']} "
              f"estados={d['contagem']}")
    print(f"  produção tocada: {res['_meta']['producao_tocada'] or 'NENHUM'}")
    print(f"  resultado → {RESULTADO}")
    return 0 if res["estado"] == "OK" and not res["_meta"]["producao_tocada"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
