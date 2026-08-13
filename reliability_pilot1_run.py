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


# ── classificação de erro do provider ───────────────────────────────────────
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
RATE_LIMITED = "RATE_LIMITED"
AUTH_ERROR = "AUTH_ERROR"
INVALID_REQUEST = "INVALID_REQUEST"
PROVIDER_ERROR = "PROVIDER_ERROR"
UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"

# Classes de erro que ABORTAM a corrida. Num piloto de avaliação, insistir
# depois de qualquer uma delas não produz dado — só queima orçamento. Foi
# exatamente o que aconteceu no run 31714780166: 39 chamadas recusadas em
# 4 segundos porque o executor continuou tentando.
INTERROMPEM = frozenset({QUOTA_EXHAUSTED, RATE_LIMITED, AUTH_ERROR,
                         INVALID_REQUEST})

# Tipos de exceção do google.api_core, casados por NOME para não exigir o SDK
# instalado. É a via preferida: o tipo é estrutural, a mensagem é prosa.
_TIPOS = {
    "ResourceExhausted": QUOTA_EXHAUSTED,
    "TooManyRequests": RATE_LIMITED,
    "Unauthenticated": AUTH_ERROR,
    "PermissionDenied": AUTH_ERROR,
    "Forbidden": AUTH_ERROR,
    "InvalidArgument": INVALID_REQUEST,
    "BadRequest": INVALID_REQUEST,
    "FailedPrecondition": INVALID_REQUEST,
    "NotFound": INVALID_REQUEST,
    "DeadlineExceeded": PROVIDER_ERROR,
    "ServiceUnavailable": PROVIDER_ERROR,
    "InternalServerError": PROVIDER_ERROR,
}
_CODIGOS = {429: RATE_LIMITED, 401: AUTH_ERROR, 403: AUTH_ERROR,
            400: INVALID_REQUEST, 404: INVALID_REQUEST}


def classificar_erro_provider(exc: Exception) -> dict:
    """Classifica pelo TIPO e pelo código estruturado antes da mensagem.

    A mensagem é o último recurso de propósito: casar substring em texto de
    provider é frágil e já nos mordeu em outros lugares. `ResourceExhausted`
    cobre tanto cota diária quanto limite por minuto no google.api_core, e o
    texto NÃO distingue os dois de forma confiável — por isso a distinção entre
    QUOTA_EXHAUSTED e RATE_LIMITED fica registrada como incerta em vez de
    afirmada. Para o piloto dá no mesmo: ambas interrompem.
    """
    nome = type(exc).__name__
    classe = _TIPOS.get(nome)
    base = "tipo_da_excecao" if classe else ""

    codigo = None
    for attr in ("code", "status_code", "grpc_status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            codigo = v
            break
        cv = getattr(v, "value", None)
        if isinstance(cv, int):
            codigo = cv
            break
    if not classe and codigo in _CODIGOS:
        classe, base = _CODIGOS[codigo], "codigo_estruturado"

    msg = str(exc)
    if not classe:
        baixo = msg.lower()
        if "exceeded your current quota" in baixo or "resource_exhausted" in baixo:
            classe, base = QUOTA_EXHAUSTED, "mensagem (ultimo recurso)"
        elif "rate limit" in baixo or "too many requests" in baixo:
            classe, base = RATE_LIMITED, "mensagem (ultimo recurso)"
        elif "api key" in baixo or "unauthenticated" in baixo:
            classe, base = AUTH_ERROR, "mensagem (ultimo recurso)"
        else:
            classe, base = UNKNOWN_PROVIDER_ERROR, "nao classificado"

    # QUOTA vs RATE_LIMIT: o mesmo tipo cobre os dois e o texto não separa.
    ambiguo = (classe in (QUOTA_EXHAUSTED, RATE_LIMITED)
               and nome == "ResourceExhausted")
    return {
        "classe": classe,
        "base_da_classificacao": base,
        "excecao": nome,
        "codigo": codigo,
        "quota_vs_rate_limit_indeterminado": ambiguo,
        "interrompe": classe in INTERROMPEM,
        # a mensagem do provider não carrega segredo, mas vai truncada
        "mensagem": msg[:240],
    }


# ── metadados de uso: DESCOBERTOS, não presumidos ───────────────────────────
def serializar_uso(um) -> dict:
    """Enumera os campos que o objeto REALMENTE tem.

    Lista fixa aqui seria chute: o SDK não está instalado localmente e os
    campos variam por versão e por modelo (`thoughts_token_count` e
    `cached_content_token_count` existem em algumas e não em outras). Descobrir
    é a única forma honesta de não inventar campo nem perder campo.
    """
    if um is None:
        return {}
    out, origem = {}, "nenhuma"
    desc = getattr(um, "DESCRIPTOR", None)
    campos = getattr(desc, "fields", None)
    if campos:
        origem = "protobuf_descriptor"
        for f in campos:
            try:
                out[f.name] = getattr(um, f.name, None)
            except Exception:
                out[f.name] = None
    if not out:
        origem = "introspeccao_de_atributos"
        for nome in dir(um):
            if nome.startswith("_"):
                continue
            try:
                v = getattr(um, nome)
            except Exception:
                continue
            if callable(v):
                continue
            if isinstance(v, (int, float, str, bool)) or v is None:
                out[nome] = v
    seguro = {}
    for k, v in out.items():
        seguro[k] = v if isinstance(v, (int, float, str, bool)) or v is None else str(v)
    seguro["_origem_dos_campos"] = origem
    return seguro


def extrair_finish_reason(resp) -> dict:
    """Motivo de parada + bloqueio de segurança, descobertos com defesa."""
    out = {"finish_reason": None, "finish_reason_nome": None,
           "block_reason": None, "n_candidatos": None}
    try:
        cands = getattr(resp, "candidates", None) or []
        out["n_candidatos"] = len(cands)
        if cands:
            fr = getattr(cands[0], "finish_reason", None)
            out["finish_reason"] = (fr if isinstance(fr, (int, str))
                                    else getattr(fr, "value", None))
            out["finish_reason_nome"] = (getattr(fr, "name", None)
                                         or (str(fr) if fr is not None else None))
    except Exception:
        pass
    try:
        pf = getattr(resp, "prompt_feedback", None)
        br = getattr(pf, "block_reason", None) if pf is not None else None
        out["block_reason"] = (getattr(br, "name", None)
                               or (str(br) if br is not None else None))
    except Exception:
        pass
    return out


# Nomes de finish_reason que indicam corte por limite de tokens. Só isto marca
# truncamento — NÃO a aritmética `total - prompt - output`, que não é campo do
# provider e cuja diferença pode ter várias origens.
_FINISH_TRUNCA = {"MAX_TOKENS", "LENGTH", "2"}


def diagnosticar_json(bruto: str, exc: Exception, fim: dict) -> dict:
    """O que se pode dizer de um JSON que não parseou, sem tentar consertá-lo."""
    b = bruto or ""
    nome = (fim.get("finish_reason_nome") or "").upper()
    if nome:
        truncou = nome in _FINISH_TRUNCA
    else:
        truncou = None                    # sem finish reason: UNKNOWN, não False
    return {
        "raw_len": len(b),
        "starts_with": b[:60],
        "ends_with": b[-60:],
        "parse_exception": type(exc).__name__,
        "parse_erro": str(exc)[:120],
        "finish_reason_nome": fim.get("finish_reason_nome"),
        "possible_output_truncation": truncou,
        "base_da_suspeita": ("finish_reason" if nome else
                             "indeterminado — provider não devolveu finish reason"),
    }


def chamada_unica(genai, modelo: str, prompt: str) -> dict:
    """Exatamente um `generate_content`. Sem repetição, sem outro modelo.

    O pacing NÃO acontece aqui: quem espaça é o laço, depois de QUALQUER
    tentativa. Dormir só no sucesso — como esta função fazia — permitiu que 39
    recusas disparassem em 4 segundos.
    """
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
            uso = serializar_uso(getattr(resp, "usage_metadata", None))
        except Exception:
            uso = {}
        fim = extrair_finish_reason(resp)
        modelo_real = ""
        try:
            modelo_real = getattr(resp, "model_version", "") or ""
        except Exception:
            modelo_real = ""
        try:
            bruto = resp.text
        except Exception as exc:
            return {"estado": "SEM_TEXTO", "saida": None, "latencia_s": dt,
                    "uso": uso, "modelo_real": modelo_real, "finish": fim,
                    "motivo": f"{type(exc).__name__}: {str(exc)[:180]}",
                    "erro": {"classe": PROVIDER_ERROR, "interrompe": False}}
        comum = {"latencia_s": dt, "uso": uso, "modelo_real": modelo_real,
                 "finish": fim, "raw_output": bruto,
                 "output_cap_solicitado": OUTPUT_TOKEN_CAP}
        try:
            return {"estado": "OK", "saida": json.loads(bruto), **comum}
        except Exception as exc:
            return {"estado": "JSON_INVALIDO", "saida": None,
                    "diagnostico_json": diagnosticar_json(bruto, exc, fim),
                    **comum}
    except Exception as exc:
        cls = classificar_erro_provider(exc)
        return {"estado": cls["classe"], "saida": None,
                "latencia_s": round(time.time() - t0, 2),
                "uso": {}, "modelo_real": "", "finish": {},
                "erro": cls, "motivo": f"{cls['excecao']}: {cls['mensagem']}"}


# ── execução ────────────────────────────────────────────────────────────────
def executar(modo: str, *, confirmado: bool, teto_execucao: int | None = None,
             espacamento_s: float | None = None) -> dict:
    """`teto_execucao` existe para exercitar o BACKSTOP em tempo de execução.

    São duas defesas distintas e o gate de plano dispara primeiro: se o plano
    já excede o orçamento, a corrida aborta antes de qualquer chamada. O
    contador por chamada é a segunda linha — protege o caso em que o plano
    estava errado. Sem este parâmetro não haveria como provar que a segunda
    linha funciona, porque a primeira nunca deixaria chegar lá.
    """
    cfg = rd.load_config("config_risco.yaml")
    man = ps.carregar_manifesto()
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
    espacamento = sleep_s if espacamento_s is None else espacamento_s
    disjuntor = None
    for ent in entradas:
        item = porid[ent["sample_id"]]
        texto = item["input"]["texto"]
        ck = ent["cache_key"]

        if ck in cache:
            r = {"estado": "OK", "saida": cache[ck], "latencia_s": 0.0,
                 "uso": {}, "modelo_real": "", "cache_hit": True}
        elif disjuntor is not None:
            # CIRCUIT BREAKER já acionado: as restantes NÃO tocam o provider.
            r = {"estado": f"SKIPPED_{disjuntor['classe']}", "saida": None,
                 "latencia_s": 0.0, "uso": {}, "modelo_real": "",
                 "cache_hit": False,
                 "motivo": f"pulada — corrida interrompida em "
                           f"{disjuntor['sample_id']} por {disjuntor['classe']}"}
        elif chamadas >= teto:
            r = {"estado": "CALL_BUDGET_EXHAUSTED", "saida": None,
                 "latencia_s": 0.0, "uso": {}, "modelo_real": "",
                 "cache_hit": False}
        else:
            chamadas += 1               # conta ANTES de sair — qualquer
            r = chamada_unica(genai, primario, ent["payload"]["prompt"])
            r["cache_hit"] = False
            if r["estado"] == "OK":
                cache[ck] = r["saida"]
            erro = r.get("erro") or {}
            if erro.get("interrompe"):
                disjuntor = {**erro, "sample_id": ent["sample_id"],
                             "call_type": ent["call_type"],
                             "na_chamada": chamadas}
            else:
                # PACING APÓS QUALQUER TENTATIVA — sucesso, JSON inválido ou
                # erro comum. Antes o sleep só existia no caminho de sucesso, e
                # foi por isso que 39 recusas couberam em 4 segundos.
                if espacamento > 0:
                    time.sleep(espacamento)

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
            "finish": r.get("finish") or {},
            "diagnostico_json": r.get("diagnostico_json"),
            "erro_classificado": r.get("erro"),
            "output_cap_solicitado": r.get("output_cap_solicitado"),
            # bruto fica SÓ no artefato; o log do Actions nunca imprime isto
            "raw_output": r.get("raw_output"),
        })
        linhas.append(linha)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"_meta": {"cache_version": pp.CACHE_VERSION, "MOCK": False},
               "entradas": cache},
              io.open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    puladas = sum(1 for l in linhas if str(l["estado"]).startswith("SKIPPED_"))
    return {"modo": "live",
            "estado": "INTERROMPIDO_POR_DISJUNTOR" if disjuntor else "OK",
            "gates": gates, "linhas": linhas,
            "contagem": dict(contagem), "provider_calls": chamadas,
            "modelo_primario": primario,
            "fallbacks_usados": 0, "retries": 0,
            "espacamento_s": espacamento,
            "circuit_breaker": disjuntor,
            "chamadas_puladas": puladas}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("dry", "mock", "live"), default="dry")
    ap.add_argument("--confirm-live", default="")
    ap.add_argument("--inter-call-seconds", type=float, default=None,
                    help="espaçamento após CADA tentativa; default vem do "
                         "config experimental (rpm_sleep_seconds)")
    args = ap.parse_args()

    antes = hashes_de_producao()
    res = executar(args.mode, confirmado=(args.confirm_live == CONFIRMACAO),
                   espacamento_s=args.inter_call_seconds)
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
        print(f"    {l['sample_id']:8s} {l['call_type']:9s} {l['estado']:26s} "
              f"cache={str(l.get('cache_hit')):5s} "
              f"{str(l.get('latencia_s') or ''):>6s}s "
              f"cmp={l['comparison_status']}")
    return 0 if res["estado"] in ("OK",) and not tocados else 1


if __name__ == "__main__":
    raise SystemExit(main())
