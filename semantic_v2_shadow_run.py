#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor do shadow prospectivo V2 — roda no cron, observa, não pontua.

Chamado depois do dashboard já estar gerado, pela mesma razão dos outros
shadows do repositório: o score já foi calculado e publicado, e nada daqui
pode alterá-lo.

FAIL-OPEN é a regra, não a exceção. Provider fora do ar, cota esgotada,
schema recusado, sidecar corrompido — tudo isso termina em log e segue. O
cron não pode cair por causa de uma camada observacional.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from pathlib import Path

import gemini_schema_adapter as ga
import reliability_pilot_contract_v2 as v2
import reliability_pilot_validators as pv
import risk_dashboard as rd
import semantic_v2_shadow as sh

OUTPUT_TOKEN_CAP = 1600
PACING_S = float(os.environ.get("RISK_SHADOW_PACING_S", "8"))


def _classificar(exc: Exception) -> dict:
    nome, msg = type(exc).__name__, str(exc)
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
    return {"classe": classe, "excecao": nome, "mensagem": msg[:200],
            "interrompe": classe in sh.INTERROMPEM or classe == "MODEL_UNAVAILABLE"}


def _uma_chamada(genai, modelo: str, payload: dict) -> dict:
    """Exatamente uma invocação. Zero retry, zero fallback de modelo."""
    t0 = time.time()
    esquema = payload.get("schema")
    try:
        m = genai.GenerativeModel(modelo)
        gc = genai.types.GenerationConfig(
            temperature=0.0, response_mime_type="application/json",
            max_output_tokens=OUTPUT_TOKEN_CAP,
            response_schema=ga.adaptar_schema(esquema))
    except Exception as exc:
        return {"estado": "CLIENT_SCHEMA_ERROR", "saida": None,
                "latencia_s": round(time.time() - t0, 2), "invocou_sdk": False,
                "erro": {"classe": "CLIENT_SCHEMA_ERROR",
                         "excecao": type(exc).__name__,
                         "mensagem": str(exc)[:200], "interrompe": True}}
    try:
        resp = m.generate_content(payload["prompt"], generation_config=gc,
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
        comum = {"latencia_s": dt, "uso": uso, "finish": fim, "invocou_sdk": True,
                 "modelo_real": getattr(resp, "model_version", "") or ""}
        try:
            saida = json.loads(bruto)
        except Exception:
            return {"estado": "JSON_INVALIDO", "saida": None, **comum}
        return {"estado": "OK",
                "saida": ga.normalizar_saida(saida, esquema), **comum}
    except Exception as exc:
        cls = _classificar(exc)
        return {"estado": cls["classe"], "saida": None, "invocou_sdk": True,
                "latencia_s": round(time.time() - t0, 2), "erro": cls}


def executar(historico: dict, cfg: dict, *, genai=None,
             limite: int = sh.MAX_CASOS_POR_RUN) -> dict:
    """Seleciona, observa e persiste. Nunca levanta para o chamador."""
    tel = {"shadow_version": sh.SHADOW_VERSION, "candidatos": 0,
           "selecionados": 0, "novos_registros": 0, "por_modelo": {},
           "concordancias": 0, "divergencias": 0, "sidecar_alterado": False}
    try:
        dados = sh.carregar()
        ja = {tuple(k.split("|")[:3]) for k in dados["observacoes"]}
        artigos = list((historico.get("articles") or {}).values())
        selecionados = sh.selecionar(artigos, historico, ja, limite)
        tel["candidatos"] = len(artigos)
        tel["selecionados"] = len(selecionados)
        if not selecionados:
            print("   🫥 shadow V2: nenhum caso prospectivo novo neste ciclo.")
            return tel

        if genai is None:
            chave_api = ((cfg.get("llm") or {}).get("gemini_api_key")
                         or os.environ.get("GEMINI_API_KEY", ""))
            if not chave_api:
                print("   ⚠️  shadow V2: sem chave — nenhuma observação (fail-open).")
                return tel
            try:
                import google.generativeai as genai_mod
                genai_mod.configure(api_key=chave_api)
                genai = genai_mod
            except Exception as exc:
                print(f"   ⚠️  shadow V2: SDK indisponível ({exc}) — fail-open.")
                return tel

        import semantic_audit as sa
        aliases = sa._aliases_map(cfg)
        disjuntor = {m: None for m in sh.MODELOS}
        novos = {}
        for caso in selecionados:
            art = caso["artigo"]
            texto = (art.get("summary") or art.get("title") or "")[:4000]
            try:
                payload = v2.payload_audit(
                    texto=texto, organizacao=caso["empresa"],
                    aliases=list(aliases.get(caso["empresa"]) or []),
                    event_ids=[caso["event_id"]],
                    pub_iso=art.get("pub_iso") or "")
            except Exception as exc:
                print(f"   ⚠️  shadow V2: payload recusado para "
                      f"{caso['empresa']} ({type(exc).__name__}) — pulando.")
                continue
            det = sh.snapshot_deterministico(art, caso["empresa"],
                                             caso["event_id"])
            veredito = {}
            for modelo in sh.MODELOS:
                t = tel["por_modelo"].setdefault(
                    modelo, {"planejadas": 0, "invocacoes_sdk": 0,
                             "sucessos": 0, "parada_por_cota": None})
                t["planejadas"] += 1
                if disjuntor[modelo] is not None:
                    # o OUTRO modelo segue: cota é por projeto+modelo
                    continue
                r = _uma_chamada(genai, modelo, payload)
                if r.get("invocou_sdk"):
                    t["invocacoes_sdk"] += 1
                erro = r.get("erro") or {}
                if erro.get("interrompe"):
                    disjuntor[modelo] = erro
                    t["parada_por_cota"] = erro.get("classe")
                elif PACING_S > 0:
                    time.sleep(PACING_S)
                val = {}
                if r["estado"] == "OK":
                    t["sucessos"] += 1
                    try:
                        val = pv.validar_audit(
                            r["saida"], texto=texto,
                            organizacao=caso["empresa"],
                            event_ids=[caso["event_id"]])
                    except Exception as exc:
                        val = {"ok": False, "erro": type(exc).__name__}
                k = sh.chave(caso["artigo_id"], caso["empresa"],
                             caso["event_id"], v2.CONTRACT_VERSION,
                             v2.PROMPT_VERSION, modelo)
                if k in dados["observacoes"]:
                    continue                 # imutável: a primeira vale
                novos[k] = {
                    "case_id": k, "article_id": caso["artigo_id"],
                    "url": art.get("url"), "company": caso["empresa"],
                    "candidate_event": caso["event_id"],
                    "title": art.get("title"),
                    "source": art.get("source"),
                    "article_pub_iso": art.get("pub_iso"),
                    "first_seen_iso": art.get("cap_iso"),
                    "contract_version": v2.CONTRACT_VERSION,
                    "prompt_version": v2.PROMPT_VERSION,
                    "schema_version": v2.SCHEMA_VERSION,
                    "shadow_version": sh.SHADOW_VERSION,
                    "requested_model": modelo,
                    "actual_model": r.get("modelo_real") or "",
                    "estado": r["estado"],
                    "saida": r.get("saida"),
                    "evidencia": val,
                    "deterministic": det,
                    "usage": r.get("uso") or {},
                    "latencia_s": r.get("latencia_s"),
                    "finish": r.get("finish") or {},
                    "erro": r.get("erro"),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime()),
                    "human_review": None,   # camada futura, nunca auto-preenchida
                }
                if r["estado"] == "OK":
                    veredito[modelo] = json.dumps(
                        (r["saida"] or {}).get("events"), sort_keys=True)
            if len(veredito) == 2:
                a, b = list(veredito.values())
                if a == b:
                    tel["concordancias"] += 1
                else:
                    tel["divergencias"] += 1

        dados["observacoes"].update(novos)
        tel["novos_registros"] = len(novos)
        tel["sidecar_alterado"] = sh.gravar(sh.fundir(dados, sh.carregar()))
        print(f"   🕶️  shadow V2: {tel['selecionados']} caso(s) prospectivo(s), "
              f"{tel['novos_registros']} observação(ões) nova(s), "
              f"{tel['concordancias']} concordância(s), "
              f"{tel['divergencias']} divergência(s).")
    except Exception as exc:
        # observação NUNCA derruba o cron
        print(f"   ⚠️  shadow V2 falhou ({type(exc).__name__}: "
              f"{str(exc)[:120]}) — pipeline segue.")
    return tel


def fila_de_revisao(dados: dict | None = None, limite: int = 20) -> list:
    """Fila curta: só o que de fato merece olho humano.

    Prioridade A→F conforme o desenho: divergência entre modelos primeiro,
    depois divergência contra o determinístico.
    """
    dados = dados or sh.carregar()
    obs = dados.get("observacoes") or {}
    porcaso = {}
    for o in obs.values():
        porcaso.setdefault((o["article_id"], o["company"],
                            o["candidate_event"]), []).append(o)
    fila = []
    for (aid, emp, ev), lista in porcaso.items():
        if any(o.get("human_review") for o in lista):
            continue
        oks = [o for o in lista if o["estado"] == "OK"]
        eventos = {o["requested_model"]:
                   json.dumps((o.get("saida") or {}).get("events"),
                              sort_keys=True) for o in oks}
        divergem = len(set(eventos.values())) > 1
        det = (lista[0].get("deterministic") or {})
        prio, motivo = 0, []
        if divergem:
            prio += 100
            motivo.append("modelos divergem")
        if det.get("scoreable"):
            prio += 20
            motivo.append("determinístico pontua")
        if len(oks) < len(lista):
            prio += 5
            motivo.append("alguma observação sem resposta")
        fila.append({"article_id": aid, "company": emp, "candidate_event": ev,
                     "title": lista[0].get("title"),
                     "url": lista[0].get("url"),
                     "deterministic": det, "prioridade": prio,
                     "motivo": ", ".join(motivo) or "amostra geral",
                     "observacoes": len(lista)})
    fila.sort(key=lambda x: (-x["prioridade"], x["article_id"]))
    return fila[:limite]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--historico", default="risk_history.json")
    ap.add_argument("--config", default="config_risco.yaml")
    ap.add_argument("--fila", action="store_true",
                    help="só imprime a fila de revisão; zero chamadas")
    args = ap.parse_args()
    if args.fila:
        for f in fila_de_revisao():
            print(f"  [{f['prioridade']:3d}] {f['company']} · "
                  f"{f['candidate_event']} · {f['motivo']}")
            print(f"        {(f['title'] or '')[:100]}")
        return 0
    hist = json.load(io.open(args.historico, encoding="utf-8"))
    cfg = rd.load_config(args.config)
    tel = executar(hist, cfg)
    Path("out_reliability").mkdir(parents=True, exist_ok=True)
    json.dump(tel, io.open("out_reliability/semantic_v2_shadow_run.json", "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
