#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_pilot_runner.py — 4I.2 R7b-A.

EXECUÇÃO DO PILOTO: ARCH-A (audit + discovery cega) × ARCH-B (call combinada).

Fail-closed por desenho. Sem chave, sem SDK ou com cota estourada, o runner
NÃO produz saída sintética: ele monta os payloads reais, grava-os, e marca
cada item como `NAO_EXECUTADO` com o motivo. Um piloto cujo instrumento
inventa dados quando o provider falta é pior que um piloto não executado —
a wave anterior mostrou o custo de medir com instrumento errado.

Duas propriedades permanecem verificáveis mesmo sem nenhuma chamada:

  - o payload da DISCOVERY não contém empresa monitorada nem candidatos;
  - nenhum payload contém score, peso, tier, trust ou threshold.

Elas são checadas aqui e no teste, sobre os payloads REAIS que seriam enviados.

CACHE: sete componentes de identidade + `call_type` (ver contrato). Local e
experimental — não é cache de produção.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import time
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa
import reliability_pilot_contract as ct
import reliability_pilot_input as pi
import reliability_pilot_validators as vl

OUTDIR = Path(os.environ.get("RELIABILITY_OUTDIR", "out_reliability/r7b_a"))
HISTORY = Path(os.environ.get("RELIABILITY_HISTORY", "risk_history.json"))
CACHE = OUTDIR / "llm_cache.json"

NAO_EXECUTADO = "NAO_EXECUTADO"
EXECUTADO = "EXECUTADO"
FALHA = "FALHA"


# ── cliente ─────────────────────────────────────────────────────────────────
class ProviderIndisponivel(RuntimeError):
    pass


def cliente(cfg: dict):
    """Reusa a integração existente (mesmo SDK, mesmo modelo, mesmos
    fallbacks). Não cria um segundo cliente de API."""
    llm = cfg.get("llm") or {}
    chave = llm.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not chave:
        raise ProviderIndisponivel(
            "GEMINI_API_KEY ausente (secret de Actions, não local)")
    try:
        import google.generativeai as genai
    except Exception as exc:
        raise ProviderIndisponivel(
            f"SDK google-generativeai não instalado: {exc}") from exc
    genai.configure(api_key=chave)
    modelos = [llm.get("model", "gemini-3-flash")] + list(
        llm.get("model_fallbacks") or [])
    return genai, modelos, float(llm.get("rpm_sleep_seconds", 6.5))


def _carregar_cache() -> dict:
    if CACHE.exists():
        try:
            return json.load(io.open(CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _gravar_cache(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    io.open(CACHE, "w", encoding="utf-8").write(
        json.dumps(c, ensure_ascii=False, indent=1, sort_keys=True))


def chamar(payload: dict, *, genai, modelos, sleep_s: float, cache: dict,
           model_usado: list) -> dict:
    """Uma chamada, temperatura 0, JSON forçado, 1 retry (o do
    `_gemini_call`). Sem votação, sem self-consistency, sem multi-agente."""
    extra = f"{payload.get('organizacao','')}|{'.'.join(payload.get('event_ids') or [])}"
    for modelo in modelos:
        cid = ct.identidade_de_cache(call_type=payload["call_type"],
                                     texto=payload["texto"], model=modelo,
                                     extra=extra)
        if cid in cache:
            model_usado.append(modelo)
            return {"status": EXECUTADO, "cache_hit": True,
                    "model": modelo, "saida": cache[cid]}
        try:
            m = genai.GenerativeModel(modelo)
            saida = rd._gemini_call(m, payload["prompt"], sleep_s)
            cache[cid] = saida
            model_usado.append(modelo)
            return {"status": EXECUTADO, "cache_hit": False,
                    "model": modelo, "saida": saida}
        except rd.GeminiModelUnavailable:
            continue
        except rd.GeminiQuotaExhausted as exc:
            return {"status": FALHA, "motivo": f"cota esgotada: {exc}"}
        except Exception as exc:
            return {"status": FALHA, "motivo": f"{type(exc).__name__}: {exc}"}
    return {"status": FALHA, "motivo": "nenhum modelo disponível"}


# ── montagem dos payloads (independente de provider) ────────────────────────
def payloads_do_item(item: dict, rec: dict, al: dict, *, variante: str,
                     arch: str, tap_rec: dict | None = None) -> dict:
    fonte = tap_rec or rec
    if tap_rec:
        base = {"title": tap_rec.get("titulo") or "",
                "summary": tap_rec.get("resumo") or "",
                "pub_iso": tap_rec.get("pub_iso") or "",
                "domain": tap_rec.get("dominio") or ""}
    else:
        base = fonte
    inp = (pi.montar_v1(base, item["url"]) if variante == "V1"
           else pi.montar_v0(base))
    emp = item.get("empresa") or ""
    evs = [item["evento"]] if item.get("evento") else []
    out = {"input": inp, "payloads": {}}
    if arch == ct.ARCH_A:
        if evs:
            out["payloads"][ct.CALL_AUDIT] = ct.payload_audit(
                texto=inp["texto"], organizacao=emp,
                aliases=al.get(emp) or [], event_ids=evs,
                pub_iso=inp["pub_iso"], genero=inp["genero"])
        out["payloads"][ct.CALL_DISCOVERY] = ct.payload_discovery(
            texto=inp["texto"], pub_iso=inp["pub_iso"], genero=inp["genero"])
    else:
        out["payloads"][ct.CALL_COMBINED] = ct.payload_combined(
            texto=inp["texto"], organizacao=emp, aliases=al.get(emp) or [],
            event_ids=evs or ["(nenhum)"], pub_iso=inp["pub_iso"],
            genero=inp["genero"])
    return out


def provas_de_payload(montados: list) -> dict:
    """As duas garantias estruturais, medidas sobre os payloads REAIS."""
    disc = [m for m in montados if ct.CALL_DISCOVERY in m["payloads"]]
    vaz_emp = vaz_ev = 0
    for m in disc:
        p = m["payloads"][ct.CALL_DISCOVERY]
        emp = (m.get("empresa") or "").strip()
        # o texto do artigo pode citar a empresa legitimamente; o que não pode
        # é o PROMPT declarar qual empresa monitoramos, fora do texto.
        fora = p["prompt"].replace(p["texto"], "")
        if emp and emp.lower() in fora.lower():
            vaz_emp += 1
        if any(e and e in fora for e in (m.get("event_ids") or [])):
            vaz_ev += 1
    proibidos = collections.Counter()
    for m in montados:
        for p in m["payloads"].values():
            for t in ct.checar_payload(
                    {k: v for k, v in p.items() if k != "schema"},
                    texto_do_artigo=p["texto"]):
                proibidos[t] += 1
    return {"discovery_payloads": len(disc),
            "vazamento_empresa": vaz_emp, "vazamento_candidatos": vaz_ev,
            "termos_proibidos": dict(proibidos),
            "cego": vaz_emp == 0 and vaz_ev == 0,
            "sem_proibidos": not proibidos}


def executar(*, arch: str, variante: str, limite: int | None = None,
             so_montar: bool = False) -> dict:
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    al = sa._aliases_map(cfg)
    man = json.load(io.open(OUTDIR / "sample_manifest.json", encoding="utf-8"))
    tapf = OUTDIR / "tap_pre_filtro.json"
    tap = {}
    if tapf.exists():
        for it in (json.load(io.open(tapf, encoding="utf-8")).get("itens") or []):
            tap[(it.get("url"), it.get("empresa"))] = it

    itens = man["itens"]
    if arch == ct.ARCH_B:
        # subconjunto de controle: candidatos + eventless + tap
        alvo, por = [], collections.Counter()
        for i in itens:
            e = i["estrato"]
            cap = 4 if e in ("S1", "S3", "S4") else 6 if e in ("S6", "S7") else 0
            if por[e] < cap:
                alvo.append(i)
                por[e] += 1
        itens = alvo
    if limite:
        itens = itens[:limite]

    montados = []
    for i in itens:
        rec = (hist.get("articles") or {}).get(i["url"]) or {}
        tr = tap.get((i["url"], i.get("empresa")))
        if not rec and not tr:
            continue
        try:
            p = payloads_do_item(i, rec, al, variante=variante, arch=arch,
                                 tap_rec=tr)
        except ValueError as exc:
            montados.append({**i, "erro_payload": str(exc), "payloads": {}})
            continue
        montados.append({**i, **p})

    provas = provas_de_payload(montados)
    saidas, tel = [], collections.Counter()
    motivo_global = ""
    genai = modelos = None
    sleep_s = 6.5
    if not so_montar:
        try:
            genai, modelos, sleep_s = cliente(cfg)
        except ProviderIndisponivel as exc:
            motivo_global = str(exc)

    cache = _carregar_cache()
    model_usado = []
    for m in montados:
        reg = {"ident": m.get("ident"), "estrato": m.get("estrato"),
               "url": m.get("url"), "empresa": m.get("empresa"),
               "evento": m.get("evento"), "arch": arch, "variante": variante,
               "input_suficiente": (m.get("input") or {}).get("suficiente"),
               "chars_uteis": (m.get("input") or {}).get("chars_uteis"),
               "resultados": {}}
        for tipo, p in (m.get("payloads") or {}).items():
            if motivo_global or so_montar:
                reg["resultados"][tipo] = {"status": NAO_EXECUTADO,
                                           "motivo": motivo_global or "só montagem"}
                tel[NAO_EXECUTADO] += 1
                continue
            r = chamar(p, genai=genai, modelos=modelos, sleep_s=sleep_s,
                       cache=cache, model_usado=model_usado)
            tel[r["status"]] += 1
            if r["status"] == EXECUTADO:
                if tipo == ct.CALL_AUDIT:
                    v = vl.validar_audit(r["saida"], texto=p["texto"],
                                         organizacao=m.get("empresa") or "",
                                         aliases=al.get(m.get("empresa")) or [],
                                         event_ids=m.get("event_ids"))
                elif tipo == ct.CALL_DISCOVERY:
                    v = vl.validar_discovery(r["saida"], texto=p["texto"])
                else:
                    v = vl.validar_combined(r["saida"], texto=p["texto"],
                                            organizacao=m.get("empresa") or "",
                                            aliases=al.get(m.get("empresa")) or [],
                                            event_ids=m.get("event_ids"))
                r["validacao"] = v
            reg["resultados"][tipo] = r
        saidas.append(reg)
    if not so_montar and not motivo_global:
        _gravar_cache(cache)

    return {"arch": arch, "variante": variante,
            **ct.versoes((model_usado or ["(nenhum)"])[0]),
            "sample_version": man.get("sample_version"),
            "itens": len(montados), "telemetria": dict(tel),
            "provider_indisponivel": motivo_global,
            "provas_de_payload": provas,
            "payloads": [{"ident": m.get("ident"), "estrato": m.get("estrato"),
                          "call_types": sorted((m.get("payloads") or {})),
                          "chars_prompt": {k: len(v["prompt"])
                                           for k, v in (m.get("payloads") or {}).items()}}
                         for m in montados],
            "saidas": saidas,
            "gerado_em": int(time.time())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default=ct.ARCH_A, choices=[ct.ARCH_A, ct.ARCH_B])
    ap.add_argument("--variante", default="V0", choices=["V0", "V1"])
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--so-montar", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    r = executar(arch=a.arch, variante=a.variante, limite=a.limite,
                 so_montar=a.so_montar)
    dest = Path(a.out) if a.out else (
        OUTDIR / f"llm_run_{a.arch.lower().replace('-', '')}_{a.variante.lower()}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    io.open(dest, "w", encoding="utf-8").write(
        json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    p = r["provas_de_payload"]
    print(f"  arch {r['arch']} · variante {r['variante']} · itens {r['itens']}")
    print(f"  telemetria         : {r['telemetria']}")
    print(f"  discovery cega     : {p['cego']} "
          f"(vazamento empresa {p['vazamento_empresa']}, "
          f"candidatos {p['vazamento_candidatos']}, de {p['discovery_payloads']})")
    print(f"  sem termos proibidos: {p['sem_proibidos']} {p['termos_proibidos'] or ''}")
    if r["provider_indisponivel"]:
        print(f"  ⚠️  PROVIDER INDISPONÍVEL: {r['provider_indisponivel']}")
        print("      payloads montados e gravados; nenhuma saída sintética criada.")
    print(f"  → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
