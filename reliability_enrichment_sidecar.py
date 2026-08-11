#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_enrichment_sidecar.py — 4I.2 R5b.

Coleta seletiva de contexto, com persistência em SIDE-CAR separado do
`risk_history.json`. Shadow: nada aqui alimenta scoring.

Três decisões de projeto, todas vindas de medição da R5a e não de preferência:

1. STRUCTURED-FIRST. A extração de `<p>` capturou script inline num artigo
   real e, por ter mais tokens, venceu a escolha de melhor excerto. Metadata
   estruturada (JSON-LD, og/meta) tem perfil de risco muito menor, então vem
   primeiro e o corpo só é considerado se ela não bastar.

2. QUALIDADE ANTES DE TAMANHO. A seleção ordena por procedência e limpeza; o
   comprimento é o último critério de desempate. "Maior texto vence" é
   exatamente a regra que escolheu JavaScript.

3. UMA REQUISIÇÃO POR URL. O mesmo HTML alimenta todos os métodos. O
   early-stop economiza PROCESSAMENTO e reduz superfície de contaminação, não
   requisições — a requisição já aconteceu.

Robots é gate de entrada: `disallow` encerra o artigo sem fetch, sem bypass,
sem rota alternativa.

Uso:
    python reliability_enrichment_sidecar.py --sample     # amostra da wave
    python reliability_enrichment_sidecar.py --report     # só telemetria
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import reliability_enrichment as enr
import reliability_enrichment_policy as pol
import reliability_input_audit as ia
import risk_dashboard as rd
import semantic_audit as sa

HISTORY = Path(os.environ.get("RELIABILITY_HISTORY") or "risk_history.json")
SIDECAR = Path(os.environ.get("RELIABILITY_SIDECAR") or "risk_enrichment_shadow.json")

SCHEMA_VERSION = "1.1"          # R6b: primary + supporting
EXTRACTOR_VERSION = "r6b.1"
# Versões que o leitor ainda entende. Registro antigo continua legível; o que
# muda é que ele não carrega `supporting` e será reprocessado quando a coleta
# prospectiva o encontrar de novo — nunca retroativamente (§26).
SCHEMA_COMPATIVEIS = ("1.0", "1.1")

# No máximo DOIS fragmentos por artigo: o estruturado que já basta na maioria
# dos casos, e um único apoio quando o papel do evento continua indefinido.
# Mais que isso reintroduz o corpo inteiro, que é justamente o que a R5b
# provou ser perigoso.
MAX_EVIDENCIAS = 2
# Janela de contexto do evento para o Tier 2: frases inteiras ao redor do
# termo, porque papel exige sujeito + verbo + objeto. Recortar a keyword
# isolada destruiria a evidência que se quer preservar.
JANELA_CONTEXTO = 420

# Excerto por fragmento. 1200 caracteres cobrem com folga o lead de uma
# notícia — nos artigos auditados o trecho decisivo apareceu nos primeiros
# ~400 — e mantêm o side-car na ordem de KB por artigo, não MB. Página
# inteira nunca é persistida.
MAX_EXCERPT = 1200
MAX_FRAGMENTOS = 6

# Ladder: procedência em ordem de confiança. Índice menor = mais confiável.
#
# TIER 0 é texto que o COLETOR já recebeu — nenhuma requisição extra, nenhum
# crawl de página. Medido em R5c: o `description` do Google News tem mediana
# de 1 token novo (é o título mais o veículo) e não serve; mas os feeds
# custom/RI entregam 10–14 tokens de conteúdo real, e o `content:encoded`,
# que o parser sequer lê hoje, chegou a 204 tokens novos num feed medido.
TIER = {
    "feed:content_encoded": (0, "collector"),
    "feed:description": (0, "collector"),
    "feed:summary": (0, "collector"),
    "collector:summary": (0, "collector"),
    "jsonld:description": (1, "structured"),
    "jsonld:articleBody": (1, "structured"),
    "meta:og:description": (1, "structured"),
    "meta:description": (1, "structured"),
    "meta:twitter:description": (1, "structured"),
    "html:paragrafos": (2, "page_text"),
}
# Campos que o coletor pode carregar. O contrato é o mesmo dos demais tiers:
# passa pelo quality gate ou não é usado. Estar preenchido não basta.
CAMPOS_TIER0 = (("feed:content_encoded", "content_encoded"),
                ("feed:description", "feed_description"),
                ("feed:summary", "feed_summary"),
                ("collector:summary", "summary"))


def fragmentos_tier0(rec: dict, base_titulo: str) -> list:
    """Texto já entregue pelo coletor. Zero requisição, zero crawl.

    Robots não se aplica aqui (§12): nada é buscado — este texto veio no mesmo
    feed que o pipeline já consome legitimamente.
    """
    frags = []
    for metodo, campo in CAMPOS_TIER0:
        txt = (rec.get(campo) or "").strip()
        if not txt:
            continue
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt)).strip()
        if not txt:
            continue
        frags.append({"method": metodo, "tier": 0, "kind": "collector",
                      "text_excerpt": txt[:MAX_EXCERPT],
                      "content_hash": hashlib.sha256(
                          txt.encode("utf-8", "replace")).hexdigest()[:16],
                      **qualidade(txt, base_titulo)})
    return frags

# Suficiência é TÉCNICA, não semântica: diz que já há texto limpo bastante
# para valer a pena, nunca se a empresa é vítima ou autora — isso continua
# com o runner semântico.
MIN_TOKENS_SUFICIENTE = 8
MIN_CHARS_SENTENCA = 60


def _sentence_like(txt: str) -> bool:
    """Tem cara de frase: comprimento, espaços e pontuação terminal."""
    return (len(txt) >= MIN_CHARS_SENTENCA and txt.count(" ") >= 8
            and bool(re.search(r"[.!?…]", txt)))


def qualidade(texto: str, base: str) -> dict:
    g = ia.ganho_efetivo(base, texto)
    flags = list(enr._boilerplate(texto))
    if g["duplicado"]:
        flags.append("duplicated_base")
    if not _sentence_like(texto):
        flags.append("malformed_text")
    return {"effective_new_tokens": g["tokens_novos"],
            "effective_new_chars": g["chars_novos"],
            "containment": g["containment"],
            "quality_flags": sorted(set(flags)),
            "sentence_like": _sentence_like(texto),
            "length": len(texto)}


def suficiente(frag: dict) -> bool:
    """Parar de descer a ladder? Só com texto limpo e materialmente novo."""
    return (frag["effective_new_tokens"] >= MIN_TOKENS_SUFICIENTE
            and frag["sentence_like"]
            and not frag["quality_flags"])


def janela_de_evento(texto: str, termos: list, largura: int = JANELA_CONTEXTO) -> str:
    """Frases inteiras ao redor do primeiro termo do evento.

    Papel semântico exige sujeito, verbo e objeto: recortar a keyword isolada
    devolveria exatamente o que não serve. Por isso a janela se expande até a
    fronteira de frase mais próxima nos dois lados.
    """
    for pos in _ocorrencias(texto or "", termos):
        yield _janela_em(texto, pos, largura)


def _ocorrencias(t: str, termos: list) -> list:
    baixo = t.lower()
    pos = set()
    for k in termos:
        if not k:
            continue
        i = baixo.find(k.lower())
        while i != -1:
            pos.add(i)
            i = baixo.find(k.lower(), i + 1)
    return sorted(pos)


def _janela_em(t: str, pos: int, largura: int) -> str:
    ini, fim = max(0, pos - largura), min(len(t), pos + largura)
    corte = t.rfind(". ", 0, ini + 1)
    ini = corte + 2 if corte != -1 else ini
    corte = t.find(". ", fim)
    fim = corte + 1 if corte != -1 else fim
    return re.sub(r"\s+", " ", t[ini:fim]).strip()


def papel_do_evento_indefinido(texto: str, empresa: str, evento: str,
                               aliases: list | None = None) -> bool:
    """O texto disponível deixa o PAPEL da empresa no evento sem resposta?

    Não é ground truth e não pergunta se a empresa é vítima — pergunta se o
    runtime CONSEGUE dizer. Enquanto a resposta for "não sei", vale procurar
    um fragmento de apoio; assim que houver papel explícito, para.
    """
    if evento not in sa.EVENTOS_FRAUDE:
        return False
    al = aliases or [empresa]
    if sa.detect_fraud_role(texto, empresa, al):
        return False
    return not sa.detect_fraud_victim_evidence(texto, empresa, al)


def selecionar_evidencias(frags: list, base: str, empresa: str, evento: str,
                          aliases: list | None = None) -> tuple[list, str]:
    """PRIMARY estruturado e, se o papel seguir indefinido, um SUPPORTING.

    A R5c parava no primeiro fragmento tecnicamente suficiente e descartava o
    resto. Isso custou a evidência decisiva de um caso real: a metadata dizia
    apenas que houve uma prisão, enquanto o corpo dizia quem descobriu a
    fraude e quem sofreu o prejuízo. Suficiência técnica não é completude de
    evidência.
    """
    primary, motivo = selecionar(frags)
    if not primary:
        return [], motivo
    texto = f"{base} {primary['text_excerpt']}"
    if not papel_do_evento_indefinido(texto, empresa, evento, aliases):
        return [primary], f"{motivo}; papel explícito no primary"
    # papel ainda indefinido: um único apoio LIMPO pode completar a evidência
    cands = [f for f in frags
             if f["content_hash"] != primary["content_hash"]
             and not f["quality_flags"] and f["sentence_like"]
             and f["effective_new_tokens"] > 0]
    for f in sorted(cands, key=_ordem, reverse=True):
        g = ia.ganho_efetivo(texto, f["text_excerpt"])
        if g["duplicado"] or g["tokens_novos"] < 5:
            continue               # apoio que só repete o primary não entra
        apoio = dict(f)
        # A janela é escolhida por NECESSIDADE DE EVIDÊNCIA: entre as janelas
        # possíveis ao redor do termo, fica a primeira que de fato resolve o
        # papel. Isso é determinístico e não consulta ground truth — pergunta
        # apenas se o runtime passa a conseguir responder.
        escolhida = ""
        for janela in janela_de_evento(f["text_excerpt"],
                                       ["fraud", "fraude", "scam", "golpe"]):
            if not janela:
                continue
            if not papel_do_evento_indefinido(f"{texto} {janela}", empresa,
                                              evento, aliases):
                escolhida = janela
                break
        if not escolhida:
            continue                   # este apoio não completa a evidência
        apoio["text_excerpt"] = escolhida[:MAX_EXCERPT]
        apoio["length"] = len(apoio["text_excerpt"])
        apoio["window_of_event"] = True
        return ([primary, apoio][:MAX_EVIDENCIAS],
                f"{motivo}; apoio {f['method']} porque o papel seguia indefinido")
    return [primary], f"{motivo}; nenhum apoio limpo disponível"


def _ordem(frag: dict) -> tuple:
    """Qualidade e procedência antes de comprimento (§10)."""
    return (TIER.get(frag["method"], (9, "?"))[0],
            len(frag["quality_flags"]),
            0 if frag["sentence_like"] else 1,
            -frag["effective_new_tokens"],
            -frag["length"])


def selecionar(frags: list) -> tuple[dict | None, str]:
    """Só fragmento LIMPO é selecionado. Sem contexto é melhor que contexto sujo.

    Medido em R5b: no artigo do White & Williams não havia metadata alguma, o
    fallback caiu no Tier 2 e capturou o menu do site — cujas CATEGORIAS
    ("Bankruptcy Sales, Chapter 11") reintroduziram termos de insolvência
    fora do nome do tribunal e ressuscitaram um falso positivo que já havia
    sido corrigido em produção. O fragmento estava marcado com `navegacao` e
    ainda assim foi aceito por ser "o melhor disponível". Não é mais.
    """
    uteis = [f for f in frags if f["effective_new_tokens"] > 0
             and "duplicated_base" not in f["quality_flags"]]
    if not uteis:
        return None, "nenhum fragmento com conteúdo novo"
    limpos = [f for f in uteis if not f["quality_flags"] and f["sentence_like"]]
    if not limpos:
        return None, ("nenhum fragmento limpo; enriquecimento descartado "
                      f"(descartados: {sorted({t for f in uteis for t in f['quality_flags']})})")
    return sorted(limpos, key=_ordem)[0], "melhor fragmento limpo, por procedência"


def processar_html(html: str, base: str) -> tuple[list, bool]:
    """Extrai seguindo a ladder e para cedo se a metadata já bastar."""
    achados = dict(enr.extrair(html))
    frags, early = [], False
    for tier in (1, 2):
        metodos = [m for m, (t, _) in TIER.items() if t == tier and m in achados]
        for m in metodos:
            q = qualidade(achados[m], base)
            frags.append({"method": m, "tier": TIER[m][0], "kind": TIER[m][1],
                          "text_excerpt": achados[m][:MAX_EXCERPT],
                          "content_hash": hashlib.sha256(
                              achados[m].encode("utf-8", "replace")).hexdigest()[:16],
                          **q})
        if tier == 1 and any(suficiente(f) for f in frags):
            early = True
            break                      # §14: metadata bastou, não abre o corpo
    return frags[:MAX_FRAGMENTOS], early


def carregar_sidecar() -> dict:
    if SIDECAR.exists():
        return json.load(io.open(SIDECAR, encoding="utf-8"))
    return {"schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION,
            "policy_version": pol.POLICY_VERSION, "articles": {}}


def _reaproveitavel(anterior: dict) -> bool:
    """§21/§22: só reaproveita o que veio deste extractor e desta política."""
    return (anterior.get("extractor_version") == EXTRACTOR_VERSION
            and anterior.get("policy_version") == pol.POLICY_VERSION
            and anterior.get("status") in ("OK", "BLOCKED_BY_ROBOTS",
                                           "BLOCKED_BY_SOURCE"))


def enriquecer_url(url: str, base: str, rec: dict | None = None) -> dict:
    """Sobe a ladder do Tier 0 até o Tier 2, parando assim que bastar.

    O Tier 0 roda ANTES de qualquer rede: se o coletor já trouxe contexto
    suficiente, nenhuma requisição é feita — o que economiza custo e reduz a
    exposição a robots, e não apenas processamento.
    """
    reg = {"attempted_at": int(time.time()), "fragments": [],
           "extractor_version": EXTRACTOR_VERSION,
           "policy_version": pol.POLICY_VERSION,
           "schema_version": SCHEMA_VERSION}

    t0 = fragmentos_tier0(rec or {}, (rec or {}).get("title") or base)
    if t0 and any(suficiente(f) for f in t0):
        sel, motivo = selecionar(t0)
        reg.update(status="OK", fragments=t0[:MAX_FRAGMENTOS], early_stop=True,
                   tier0_sufficient=True, page_fetch=False,
                   robots_status="nao_aplicavel (sem fetch de página)",
                   selected=({"method": sel["method"], "tier": sel["tier"],
                              "content_hash": sel["content_hash"],
                              "effective_new_tokens": sel["effective_new_tokens"],
                              "selection_reason": motivo} if sel else None))
        return reg
    reg["tier0_sufficient"] = False
    reg["page_fetch"] = True

    permitido, robots_status = enr._robots_permite(url)
    reg["robots_status"] = robots_status
    if not permitido:
        reg["status"] = "BLOCKED_BY_ROBOTS"
        return reg
    ini = time.time()
    try:
        import requests
        r = requests.get(url, timeout=enr.TIMEOUT, headers={"User-Agent": enr.UA})
        reg["latency_ms"] = int((time.time() - ini) * 1000)
        reg["http_status"] = r.status_code
        if r.status_code >= 400:
            reg["status"] = ("BLOCKED_BY_SOURCE"
                             if r.status_code in (401, 402, 403, 451)
                             else f"HTTP_{r.status_code}")
            return reg
        if "charset" not in (r.headers.get("Content-Type") or "").lower():
            r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
    except Exception as exc:                            # noqa: BLE001
        reg["latency_ms"] = int((time.time() - ini) * 1000)
        reg["status"] = "ERROR"
        reg["error"] = type(exc).__name__
        return reg

    frags, early = processar_html(html, base)
    frags = t0 + frags                      # Tier 0 continua concorrendo
    sel, motivo = selecionar(frags)
    reg.update(status="OK" if frags else "NO_CONTENT", fragments=frags,
               early_stop=early,
               selected=({"method": sel["method"], "tier": sel["tier"],
                          "content_hash": sel["content_hash"],
                          "effective_new_tokens": sel["effective_new_tokens"],
                          "selection_reason": motivo} if sel else None))
    return reg


MAX_REQUESTS_POR_RUN = 40


def novos_do_run(hist: dict, side: dict) -> list:
    """Artigos VISTOS PELA PRIMEIRA VEZ neste run.

    `captured_ts` recente não serve: reprocessamento reescreve carimbos e
    traria histórico de volta. A identidade é a ausência do artigo no
    side-car — que persiste entre runs — combinada com o `run_count` em que
    o artigo foi registrado. Sem backfill: o estoque anterior nunca entra.
    """
    vistos = set(side.get("articles") or {})
    marcados = side.get("first_seen_run") or {}
    run = int(hist.get("run_count") or 0)
    # PRIMEIRA execução: o estoque inteiro pareceria "novo" e viraria backfill
    # acidental. A primeira passada apenas SEMEIA o marcador e não enriquece
    # nada — enriquecimento retroativo continua proibido.
    seed = not marcados
    novos = []
    for url, rec in hist["articles"].items():
        ident = rec.get("canonical_url") or url
        if ident in vistos or ident in marcados:
            continue
        if not seed:
            novos.append((ident, url, rec))
        marcados[ident] = run
    side["first_seen_run"] = marcados
    side["seeded_at_run"] = side.get("seeded_at_run", run if seed else None)
    return novos


def coletar_prospectivo(cfg=None, limite: int = MAX_REQUESTS_POR_RUN) -> dict:
    """Ponto de entrada do cron. NUNCA pode derrubar o pipeline (§29)."""
    tel = {"new_articles": 0, "eligible": 0, "tier0_attempted": 0,
           "tier0_sufficient": 0, "page_fetch_attempted": 0, "OK": 0,
           "BLOCKED_BY_ROBOTS": 0, "BLOCKED_BY_SOURCE": 0, "ERROR": 0,
           "NO_CONTENT": 0, "early_stop": 0, "tier2_usage": 0,
           "limite_atingido": False, "latencias": [], "metodos": {}}
    try:
        cfg = cfg or rd.load_config("config_risco.yaml")
        hist = json.load(io.open(HISTORY, encoding="utf-8"))
        side = carregar_sidecar()
        novos = novos_do_run(hist, side)
        tel["new_articles"] = len(novos)
        ultimo_host = ""
        for ident, url, rec in novos:
            ok, s = pol.should_enrich(rec, cfg)
            if not ok:
                continue
            tel["eligible"] += 1
            if tel["page_fetch_attempted"] >= limite:
                tel["limite_atingido"] = True
                continue
            tel["tier0_attempted"] += 1
            host = urlparse(url).netloc
            base = f"{rec.get('title') or ''}. {rec.get('summary') or ''}"
            reg = enriquecer_url(url, base, rec)
            if reg.get("page_fetch"):
                tel["page_fetch_attempted"] += 1
                if host == ultimo_host:
                    time.sleep(enr.PAUSA_ENTRE_HOSTS)
                ultimo_host = host
            else:
                tel["tier0_sufficient"] += 1
            reg.update(canonical_url=ident, title=(rec.get("title") or "")[:200],
                       eligibility=s, first_seen_run=int(hist.get("run_count") or 0))
            side["articles"][ident] = reg
            tel[reg["status"]] = tel.get(reg["status"], 0) + 1
            tel["early_stop"] += bool(reg.get("early_stop"))
            tel["tier2_usage"] += sum(1 for f in reg["fragments"] if f["tier"] == 2)
            if reg.get("latency_ms"):
                tel["latencias"].append(reg["latency_ms"])
            for f in reg["fragments"]:
                tel["metodos"][f["method"]] = tel["metodos"].get(f["method"], 0) + 1
        gravar_sidecar(side)
    except Exception as exc:                            # noqa: BLE001
        # §29: enrichment é observabilidade. Falhar aqui não pode impedir a
        # publicação do dashboard — o pipeline de risco não depende disto.
        tel["fatal_error"] = f"{type(exc).__name__}: {exc}"
        print(f" ⚠️  enrichment shadow falhou e foi ignorado: {tel['fatal_error']}")
    tel["latencia_media_ms"] = (int(sum(tel["latencias"]) / len(tel["latencias"]))
                                if tel["latencias"] else 0)
    tel.pop("latencias")
    return tel


def gravar_sidecar(side: dict) -> None:
    side.update(schema_version=SCHEMA_VERSION, extractor_version=EXTRACTOR_VERSION,
                policy_version=pol.POLICY_VERSION, gerado_em=int(time.time()))
    tmp = SIDECAR.with_suffix(".tmp")
    tmp.write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SIDECAR)


def rodar(limite: int = 60) -> dict:
    cfg = rd.load_config("config_risco.yaml")
    hist = json.load(io.open(HISTORY, encoding="utf-8"))
    side = carregar_sidecar()
    tel = {"elegiveis": 0, "tentados": 0, "cache_hit": 0, "OK": 0,
           "BLOCKED_BY_ROBOTS": 0, "BLOCKED_BY_SOURCE": 0, "ERROR": 0,
           "NO_CONTENT": 0, "early_stop": 0, "latencias": [], "metodos": {}}
    ultimo_host = ""
    for url, rec in hist["articles"].items():
        ok, s = pol.should_enrich(rec, cfg)
        if not ok:
            continue
        tel["elegiveis"] += 1
        ident = rec.get("canonical_url") or url
        anterior = side["articles"].get(ident)
        if anterior and _reaproveitavel(anterior):
            tel["cache_hit"] += 1
            continue
        if tel["tentados"] >= limite:
            continue
        host = urlparse(url).netloc
        if host == ultimo_host:
            time.sleep(enr.PAUSA_ENTRE_HOSTS)
        ultimo_host = host
        base = f"{rec.get('title') or ''}. {rec.get('summary') or ''}"
        reg = enriquecer_url(url, base)
        reg.update(canonical_url=ident, title=(rec.get("title") or "")[:200],
                   eligibility=s)
        side["articles"][ident] = reg
        tel["tentados"] += 1
        tel[reg["status"]] = tel.get(reg["status"], 0) + 1
        tel["early_stop"] += bool(reg.get("early_stop"))
        if reg.get("latency_ms"):
            tel["latencias"].append(reg["latency_ms"])
        for f in reg["fragments"]:
            tel["metodos"][f["method"]] = tel["metodos"].get(f["method"], 0) + 1
        print(f"  [{reg['status']:18s}] early={str(reg.get('early_stop')):5s} "
              f"frags={len(reg['fragments'])} {reg.get('latency_ms', 0):>5d}ms "
              f"{reg['title'][:48]}")

    side.update(schema_version=SCHEMA_VERSION, extractor_version=EXTRACTOR_VERSION,
                policy_version=pol.POLICY_VERSION, gerado_em=int(time.time()))
    json.dump(side, io.open(SIDECAR, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    tel["latencia_media_ms"] = (int(sum(tel["latencias"]) / len(tel["latencias"]))
                                if tel["latencias"] else 0)
    tel.pop("latencias")
    return tel


def main() -> int:
    if "--prospective" in sys.argv:
        tel = coletar_prospectivo()
        print("=" * 96)
        print("PROSPECTIVE SHADOW ENRICHMENT")
        print("=" * 96)
        for k, v in tel.items():
            print(f"  {k:24s} {v}")
        if SIDECAR.exists():
            print(f"  side-car                 {SIDECAR} "
                  f"({SIDECAR.stat().st_size / 1024:.1f} KB)")
        print("=" * 96)
        return 0                      # §29: nunca falha o run
    if "--report" in sys.argv:
        side = carregar_sidecar()
        print(json.dumps({"schema": side.get("schema_version"),
                          "extractor": side.get("extractor_version"),
                          "artigos": len(side.get("articles") or {})},
                         ensure_ascii=False, indent=1))
        return 0
    tel = rodar()
    print("\n" + "=" * 96)
    print("TELEMETRIA DA COLETA SELETIVA")
    print("=" * 96)
    for k, v in tel.items():
        print(f"  {k:22s} {v}")
    print(f"  side-car               {SIDECAR} "
          f"({SIDECAR.stat().st_size / 1024:.1f} KB)")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
