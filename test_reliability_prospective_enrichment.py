#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reliability_prospective_enrichment.py — 4I.2 R5c §30.

Tier 0, coleta prospectiva e durabilidade do side-car. Sem rede.

Dois contratos que estes testes existem para garantir:

  · PROSPECTIVO, NUNCA RETROATIVO. A primeira execução encontraria o estoque
    inteiro como "nunca visto" e viraria backfill acidental. Ela apenas
    semeia o marcador. Enriquecer o passado continua proibido.

  · O RISCO NÃO DEPENDE DO SIDE-CAR. Se o arquivo sumir, corromper ou vier
    vazio, o pipeline de produção precisa rodar exatamente igual — enrichment
    é observabilidade, não insumo.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import reliability_enrichment_policy as pol
import reliability_enrichment_sidecar as sc

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


_LIMPO = ("Representantes da companhia descobriram contas abertas em nome de "
          "clientes vivos e falecidos sem o conhecimento deles, o que causou "
          "prejuizo relevante ao caixa da empresa segundo os investigadores.")
_GNEWS = "Third suspect arrested in Company fraud case &nbsp;&nbsp; Veiculo"
TITULO = "Third suspect arrested in Company fraud case"

print("=" * 96)
print("BLOCO A — TIER 0: texto do coletor, zero requisição")
print("=" * 96)
_f = sc.fragmentos_tier0({"title": TITULO, "content_encoded": _LIMPO}, TITULO)
check(_f and _f[0]["tier"] == 0, "[1] `content:encoded` vira fragmento Tier 0")
check(_f[0]["method"] == "feed:content_encoded", "[2] a procedência é registrada")
check(sc.suficiente(_f[0]), "[3] texto real do feed passa no gate de suficiência")
_g = sc.fragmentos_tier0({"title": TITULO, "summary": _GNEWS}, TITULO)
check(_g and not sc.suficiente(_g[0]),
      f"[4] summary do Google News NÃO é contexto ({_g[0]['effective_new_tokens']} tokens, "
      f"flags={_g[0]['quality_flags']})")
check(sc.selecionar(_g)[0] is None,
      "[5] e por isso não é selecionável — preenchido não é informativo")
check(sc.fragmentos_tier0({}, TITULO) == [],
      "[6] sem campo do coletor não há Tier 0 inventado")
check(sc.TIER["feed:content_encoded"][0] < sc.TIER["jsonld:description"][0]
      < sc.TIER["html:paragrafos"][0],
      "[7] a ladder é Tier 0 → Tier 1 → Tier 2")

print()
print("=" * 96)
print("BLOCO B — early stop no Tier 0 evita a requisição, não só o parsing")
print("=" * 96)
_reg = sc.enriquecer_url("https://exemplo.invalid/x", f"{TITULO}. ",
                         {"title": TITULO, "content_encoded": _LIMPO})
check(_reg["page_fetch"] is False,
      "[8] Tier 0 suficiente ⇒ NENHUMA requisição de página é feita")
check(_reg["tier0_sufficient"] and _reg["status"] == "OK",
      "[9] o artigo fica enriquecido só com o que o coletor já trouxe")
check("nao_aplicavel" in _reg["robots_status"],
      f"[10] robots não se aplica a texto já entregue pelo feed ({_reg['robots_status']})")
check(_reg["selected"]["tier"] == 0, "[11] o fragmento selecionado é o do Tier 0")
_reg2 = sc.enriquecer_url("https://exemplo.invalid/y", f"{TITULO}. ",
                          {"title": TITULO, "summary": _GNEWS})
check(_reg2["page_fetch"] is True,
      "[12] Tier 0 insuficiente ⇒ a ladder desce e tenta a página")

print()
print("=" * 96)
print("BLOCO C — PROSPECTIVO: o passado nunca é enriquecido")
print("=" * 96)
_tmp = Path(tempfile.mkdtemp(prefix="r5c_"))
_hist = {"run_count": 10, "articles": {
    "u1": {"title": TITULO, "summary": _GNEWS, "event_ids": ["fraude"],
           "events_by_company": {"Petrobras": ["fraude"]}},
    "u2": {"title": "Empresa pede recuperacao judicial", "summary": "x",
           "event_ids": ["falencia"], "events_by_company": {"Vale": ["falencia"]}}}}
_side = {"schema_version": sc.SCHEMA_VERSION, "articles": {}}
_novos = sc.novos_do_run(_hist, _side)
check(_novos == [],
      f"[13] a primeira passada SEMEIA e não devolve nada para enriquecer ({len(_novos)})")
check(len(_side["first_seen_run"]) == 2, "[14] mas marca todo o estoque como já visto")
check(_side["seeded_at_run"] == 10, "[15] o run da semeadura fica registrado")
_hist["articles"]["u3"] = {"title": TITULO, "summary": _GNEWS,
                           "event_ids": ["fraude"],
                           "events_by_company": {"Petrobras": ["fraude"]}}
_hist["run_count"] = 11
_novos2 = sc.novos_do_run(_hist, _side)
check([n[0] for n in _novos2] == ["u3"],
      f"[16] só o artigo NOVO do run seguinte é candidato ({[n[0] for n in _novos2]})")
check(sc.novos_do_run(_hist, _side) == [],
      "[17] rodar de novo não redescobre o mesmo artigo — sem requisição duplicada")

print()
print("=" * 96)
print("BLOCO D — o risco não depende do side-car")
print("=" * 96)
CFG = {"taxonomy": [{"id": "fraude", "severity": "critico"}]}
for nome, conteudo in (("ausente", None), ("vazio", ""),
                       ("corrompido", "{isto nao e json"),
                       ("schema_estranho", '{"outra_coisa": 1}')):
    p = _tmp / f"side_{nome}.json"
    if conteudo is not None:
        p.write_text(conteudo, encoding="utf-8")
    os.environ["RELIABILITY_SIDECAR"] = str(p)
    os.environ["RELIABILITY_HISTORY"] = str(_tmp / "hist.json")
    (_tmp / "hist.json").write_text(json.dumps(_hist, ensure_ascii=False),
                                    encoding="utf-8")
    import importlib
    importlib.reload(sc)
    tel = sc.coletar_prospectivo(CFG, limite=0)
    check(isinstance(tel, dict) and "new_articles" in tel,
          f"[18..21] side-car {nome}: a coleta devolve telemetria em vez de estourar")
os.environ.pop("RELIABILITY_SIDECAR", None)
os.environ.pop("RELIABILITY_HISTORY", None)
import importlib
importlib.reload(sc)

_src = io.open("reliability_enrichment_sidecar.py", encoding="utf-8").read()
check("except Exception" in _src.split("def coletar_prospectivo")[1].split("\ndef ")[0],
      "[22] a coleta prospectiva isola qualquer falha — cron não cai por isso")
check("return 0" in _src.split('if "--prospective"')[1].split("if \"--report\"")[0],
      "[23] o modo prospectivo sempre sai com código 0, nunca bloqueia publicação")

print()
print("=" * 96)
print("BLOCO E — side-car durável, versionado e sem vazamento")
print("=" * 96)
check(sc.SIDECAR.name != "risk_history.json" and "history" not in sc.SIDECAR.name,
      f"[24] o side-car é arquivo próprio: {sc.SIDECAR.name}")
check("os.replace" in _src, "[25] a gravação é atômica — run interrompido não corrompe")
check(all(x not in _src for x in ("save_history", "merge_into_history", "--apply")),
      "[26] nenhum caminho de escrita em history")
check(sc.MAX_REQUESTS_POR_RUN <= 60,
      f"[27] há teto explícito de requisições por run ({sc.MAX_REQUESTS_POR_RUN})")
check("limite_atingido" in _src, "[28] atingir o teto é reportado, não silencioso")
check(sc.SCHEMA_VERSION and sc.EXTRACTOR_VERSION and pol.POLICY_VERSION,
      "[29] schema, extractor e política são versionados")

print()
print("=" * 96)
print(f"RESULTADO PROSPECTIVE ENRICHMENT: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
