#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot1_run.py — R7b piloto 1: o executor.

TRÊS MODOS, e o padrão é o seguro:

  dry   (padrão)  zero chamadas. Valida manifesto, payloads, plano, chaves de
                  cache, schemas, vazamento e caminhos de saída.
  mock            zero chamadas. Roda a cadeia inteira com respostas FALSAS.
  live            chama o provider. Exige `--confirm-live EXECUTAR-PILOTO`.

DUAS DECISÕES QUE PARECEM DETALHE E NÃO SÃO:

1. SÓ O MODELO PRIMÁRIO. `reliability_pilot_runner.cliente()` devolve
   `[primary] + fallbacks`, e `chamar()` percorre a lista. Aqui a lista é
   truncada em um. Fallback silencioso misturaria dois modelos dentro da mesma
   métrica, e o piloto perderia o sentido — se o primário falhar, isso é o
   RESULTADO, não um contratempo a contornar.

2. UMA TENTATIVA POR CHAMADA. `rd._gemini_call` repete uma vez em 429 por
   minuto. É razoável em produção e ruim aqui: a repetição acontece DENTRO da
   função, então o contador de orçamento passaria a ser aproximado. Este módulo
   faz exatamente um `generate_content`, contado ANTES de sair. Mesmo SDK,
   mesma configuração, mesmo objeto de modelo — só a política de repetição é
   nossa.

O teto é rígido: `MAX_PROVIDER_CALLS`. Qualquer chamada iniciada conta, tenha
ela sucesso, erro, timeout ou JSON inválido.

NÃO ESCREVE EM PRODUÇÃO. Toda saída vai para `out_reliability/r7b_pilot1/`.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sys
import time
from pathlib import Path

import reliability_pilot1_payloads as pp
import reliability_pilot1_sample as ps
import reliability_pilot_contract as pc
import reliability_pilot_validators as pv
import risk_dashboard as rd

RUN_VERSION = "r7b.pilot1.run.v1"
MAX_PROVIDER_CALLS = 40
OUTPUT_TOKEN_CAP = 900
CONFIRMACAO = "EXECUTAR-PILOTO"

RESULTADO = ps.OUTDIR / "pilot1_live_run.json"
CACHE = pp.CACHE

PRODUCAO = ("risk_history.json", "risk_enrichment_shadow.json",
            "risk_input_shadow.json", "index.html", "dashboard_risco.html",
            "run_meta.json", "config_risco.yaml")


class OrcamentoEsgotado(RuntimeError):
    pass


def hashes_de_producao() -> dict:
    import hashlib
    out = {}
    for f in PRODUCAO:
        p = Path(f)
        out[f] = (hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                  if p.exists() else "")
    return out


# ── provider ────────────────────────────────────────────────────────────────
def preparar_provider(cfg: dict):
    """Cliente do provider com UM único modelo: o primário configurado."""
    llm = cfg.get("llm") or {}
    chave = llm.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not chave:
        # a mensagem nunca revela tamanho, prefixo ou hash da chave
        raise RuntimeError("GEMINI_API_KEY ausente — o piloto para antes de "
                           "qualquer chamada ao provider")
    try:
        import google.generativeai as genai
    except Exception as exc:
        raise RuntimeError(f"SDK google-generativeai indisponível: {exc}") from exc
    genai.configure(api_key=chave)
    primario = llm.get("model") or "gemini-3-flash"
    return genai, primario, float(llm.get("rpm_sleep_seconds", 6.5))


def chamada_unica(genai, modelo: str, prompt: str, sleep_s: float) -> dict:
    """Exatamente um `generate_content`. Sem repetição, sem outro modelo."""
    t0 = time.time()
    try:
        m = genai.GenerativeModel(modelo)
        resp = m.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.0, response_mime_type="application/json",
                max_output_tokens=OUTPUT_TOKEN_CAP),
            request_options={"timeout": 90},
        )
        dt = round(time.time() - t0, 2)
        uso = {}
        try:
            um = getattr(resp, "usage_metadata", None)
            if um is not None:
                uso = {"prompt_tokens": getattr(um, "prompt_token_count", None),
                       "output_tokens": getattr(um, "candidates_token_count", None),
                       "total_tokens": getattr(um, "total_token_count", None)}
        except Exception:
            uso = {}
        modelo_real = ""
        try:
            modelo_real = getattr(resp, "model_version", "") or ""
        except Exception:
            modelo_real = ""
        bruto = resp.text
        time.sleep(sleep_s)
        try:
            return {"estado": "OK", "saida": json.loads(bruto), "latencia_s": dt,
                    "uso": uso, "modelo_real": modelo_real}
        except Exception:
            return {"estado": "JSON_INVALIDO", "saida": None, "latencia_s": dt,
                    "uso": uso, "modelo_real": modelo_real,
                    "amostra_bruta": (bruto or "")[:180]}
    except Exception as exc:
        return {"estado": "PROVIDER_ERROR", "saida": None,
                "latencia_s": round(time.time() - t0, 2),
                "uso": {}, "modelo_real": "",
                "motivo": f"{type(exc).__name__}: {str(exc)[:180]}"}


# ── execução ────────────────────────────────────────────────────────────────
def executar(modo: str, *, confirmado: bool, teto_execucao: int | None = None) -> dict:
    """`teto_execucao` existe para exercitar o BACKSTOP em tempo de execução.

    São duas defesas distintas e o gate de plano dispara primeiro: se o plano
    já excede o orçamento, a corrida aborta antes de qualquer chamada. O
    contador por chamada é a segunda linha — protege o caso em que o plano
    estava errado. Sem este parâmetro não haveria como provar que a segunda
    linha funciona, porque a primeira nunca deixaria chegar lá.
    """
    cfg = rd.load_config("config_risco.yaml")
    man = json.load(io.open(ps.MANIFESTO, encoding="utf-8"))
    pl = pp.construir_payloads(man, cfg)
    porid = {i["sample_id"]: i for i in man["itens"]}
    entradas = pl["audit"] + pl["discovery"]

    # ── gates que rodam em TODOS os modos ───────────────────────────────────
    vazamentos = []
    for ent in pl["audit"]:
        it = porid[ent["sample_id"]]
        r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                                 cego_a_empresa=False)
        if not r["ok"]:
            vazamentos.append((ent["sample_id"], "AUDIT", r["problemas"]))
    for ent in pl["discovery"]:
        it = porid[ent["sample_id"]]
        r = pp.auditar_vazamento(ent, texto_do_artigo=it["input"]["texto"],
                                 cego_a_empresa=True, empresa=it["company"] or "")
        if not r["ok"]:
            vazamentos.append((ent["sample_id"], "DISCOVERY", r["problemas"]))

    plano = len(entradas)
    gates = {
        "vazamento": "OK" if not vazamentos else "FALHOU",
        "vazamentos": vazamentos,
        "chamadas_planejadas": plano,
        "teto": MAX_PROVIDER_CALLS,
        "plano_dentro_do_teto": plano <= MAX_PROVIDER_CALLS,
        "chaves_de_cache_unicas": len({e["cache_key"] for e in entradas}),
        "sem_colisao_de_cache": len({e["cache_key"] for e in entradas}) == plano,
    }
    if vazamentos or plano > MAX_PROVIDER_CALLS:
        return {"modo": modo, "estado": "ABORTADO_ANTES_DE_CHAMAR",
                "gates": gates, "linhas": [], "provider_calls": 0}

    if modo == "dry":
        return {"modo": "dry", "estado": "OK", "gates": gates,
                "linhas": [], "provider_calls": 0,
                "nota": "nenhuma chamada ao provider; apenas validação"}

    if modo == "mock":
        mock = pp.rodar_mock(pl, man)
        return {"modo": "mock", "estado": "OK", "gates": gates,
                "linhas": mock["linhas"], "contagem": mock["contagem"],
                "provider_calls": 0,
                "nota": "respostas FALSAS; nenhuma chamada ao provider"}

    # ── live ────────────────────────────────────────────────────────────────
    if not confirmado:
        return {"modo": "live", "estado": "ABORTADO_SEM_CONFIRMACAO",
                "gates": gates, "linhas": [], "provider_calls": 0,
                "nota": f"--confirm-live {CONFIRMACAO} é obrigatório"}

    genai, primario, sleep_s = preparar_provider(cfg)
    cache = {}
    if CACHE.exists():
        try:
            cache = (json.load(io.open(CACHE, encoding="utf-8"))
                     .get("entradas") or {})
        except Exception:
            cache = {}
    # cache de execução MOCK não pode ser reaproveitado como se fosse resposta
    cache = {k: v for k, v in cache.items() if not (isinstance(v, dict)
                                                    and v.get("mock"))}

    linhas, contagem = [], collections.Counter()
    chamadas = 0
    teto = MAX_PROVIDER_CALLS if teto_execucao is None else teto_execucao
    for ent in entradas:
        item = porid[ent["sample_id"]]
        texto = item["input"]["texto"]
        ck = ent["cache_key"]

        if ck in cache:
            r = {"estado": "OK", "saida": cache[ck], "latencia_s": 0.0,
                 "uso": {}, "modelo_real": "", "cache_hit": True}
        elif chamadas >= teto:
            r = {"estado": "CALL_BUDGET_EXHAUSTED", "saida": None,
                 "latencia_s": 0.0, "uso": {}, "modelo_real": "",
                 "cache_hit": False}
        else:
            chamadas += 1               # conta ANTES de sair — qualquer
            r = chamada_unica(genai, primario, ent["payload"]["prompt"], sleep_s)
            r["cache_hit"] = False
            if r["estado"] == "OK":
                cache[ck] = r["saida"]

        val = {}
        estado = r["estado"]
        if estado == "OK":
            try:
                if ent["call_type"] == pc.CALL_AUDIT:
                    val = pv.validar_audit(r["saida"], texto=texto,
                                           organizacao=item["company"],
                                           event_ids=item["candidate_events"])
                else:
                    val = pv.validar_discovery(r["saida"], texto=texto)
            except Exception as exc:
                val = {"ok": False, "erro": f"{type(exc).__name__}: {exc}"[:120]}
            if not val.get("ok", True):
                estado = "EVIDENCIA_INVALIDA"

        contagem[f"{ent['call_type'].lower()}:{estado}"] += 1
        linha = pp.comparar(item, r.get("saida"), estado, "live",
                            ent["call_type"], val)
        linha.update({
            "cache_hit": r.get("cache_hit"),
            "latencia_s": r.get("latencia_s"),
            "modelo_solicitado": primario,
            "modelo_retornado": r.get("modelo_real") or "",
            "uso_de_tokens": r.get("uso") or {},
            "motivo": r.get("motivo", ""),
            "validacao": val,
            "saida_llm": r.get("saida"),
        })
        linhas.append(linha)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"_meta": {"cache_version": pp.CACHE_VERSION, "MOCK": False},
               "entradas": cache},
              io.open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    return {"modo": "live", "estado": "OK", "gates": gates, "linhas": linhas,
            "contagem": dict(contagem), "provider_calls": chamadas,
            "modelo_primario": primario,
            "fallbacks_usados": 0, "retries": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("dry", "mock", "live"), default="dry")
    ap.add_argument("--confirm-live", default="")
    args = ap.parse_args()

    antes = hashes_de_producao()
    res = executar(args.mode, confirmado=(args.confirm_live == CONFIRMACAO))
    depois = hashes_de_producao()
    tocados = [f for f in PRODUCAO if antes[f] != depois[f]]

    res["_meta"] = {
        "run_version": RUN_VERSION,
        "pilot_version": pp.PILOT_VERSION,
        "sample_version": ps.SAMPLE_VERSION,
        "max_provider_calls": MAX_PROVIDER_CALLS,
        "output_token_cap": OUTPUT_TOKEN_CAP,
        "producao_antes": antes, "producao_depois": depois,
        "producao_tocada": tocados,
    }
    ps.OUTDIR.mkdir(parents=True, exist_ok=True)
    json.dump(res, io.open(RESULTADO, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("=" * 96)
    print(f"PILOT-1 RUN — modo={res['modo']} estado={res['estado']}")
    print("=" * 96)
    g = res["gates"]
    print(f"  vazamento: {g['vazamento']} | plano: {g['chamadas_planejadas']}"
          f"/{g['teto']} | cache sem colisão: {g['sem_colisao_de_cache']}")
    print(f"  chamadas ao provider: {res['provider_calls']}")
    if res.get("contagem"):
        print(f"  estados: {res['contagem']}")
    print(f"  produção tocada: {tocados or 'NENHUM arquivo'}")
    print(f"  resultado → {RESULTADO}")
    # log enxuto por chamada: nunca prompt, nunca corpo, nunca resposta
    for l in res.get("linhas", [])[:200]:
        print(f"    {l['sample_id']:8s} {l['call_type']:9s} {l['estado']:22s} "
              f"cache={str(l.get('cache_hit')):5s} "
              f"{str(l.get('latencia_s') or ''):>6s}s "
              f"cmp={l['comparison_status']}")
    return 0 if res["estado"] in ("OK",) and not tocados else 1


if __name__ == "__main__":
    raise SystemExit(main())
