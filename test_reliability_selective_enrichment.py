#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reliability_selective_enrichment.py — 4I.2 R5b §30.

Testes determinísticos, sem rede, da arquitetura de enriquecimento seletivo.

O caso que estes testes existem para nunca deixar voltar: no artigo do White &
Williams não havia metadata alguma, o extrator caiu no corpo, capturou o MENU
do site — cujas categorias diziam "Bankruptcy Sales, Chapter 11" — e isso
ressuscitou, no shadow, um falso positivo de falência que já tinha sido
corrigido em produção. O fragmento estava marcado como navegação e mesmo
assim foi aceito por ser "o melhor disponível".

Contrato que ficou: **sem contexto é melhor que contexto sujo**.
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import reliability_enrichment_policy as pol
import reliability_enrichment_sidecar as sc

PASS = FAIL = 0
FIX = Path("test_fixtures_reliability/enrichment")


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _html(n):
    return io.open(FIX / n, encoding="utf-8").read()


def _rec(title, summary="", events=("falencia",), companies=("Vale",)):
    return {"title": title, "summary": summary, "event_ids": list(events),
            "events_by_company": {c: list(events) for c in companies}}


CFG = {"taxonomy": [{"id": "falencia", "severity": "critico"},
                    {"id": "fraude", "severity": "critico"},
                    {"id": "ma", "severity": "alto"},
                    {"id": "troca_ceo", "severity": "medio"}]}

print("=" * 96)
print("BLOCO A — should_enrich: elegibilidade sem ground truth e sem nome próprio")
print("=" * 96)
_amb = _rec("Third suspect arrested in Company fraud case")
ok, s = pol.should_enrich(_amb, CFG)
check(ok, f"[1] crítico com papel em disputa é elegível (marcas={s['marcas_ambiguidade']})")
check("nome_de_caso" in s["marcas_ambiguidade"],
      "[2] `fraud case` é reconhecido como nome de caso, não como autoria")
ok2, s2 = pol.should_enrich(_rec("Fitch rebaixa rating da Empresa",
                                 events=("rebaixamento_rating",)), CFG)
check(not ok2, "[3] evento de severidade fora de crítico/alto não gasta requisição")
ok3, s3 = pol.should_enrich(_rec("Empresa pede recuperacao judicial",
                                 events=("falencia",)), CFG)
check(not ok3, f"[4] papel claro no título não precisa de contexto ({s3['marcas_ambiguidade']})")
_src = io.open("reliability_enrichment_policy.py", encoding="utf-8").read()
for termo in ("Duke", "General Motors", "Vale", "CVS", "whiteandwilliams"):
    check(termo not in _src, f"[5..9] a política não cita nenhuma empresa ('{termo}')")
check("review" not in _src and "FALSE_POSITIVE" not in _src,
      "[10] a política não consulta ground truth humano")

print()
print("=" * 96)
print("BLOCO B — ladder structured-first e early stop")
print("=" * 96)
_frags, _early = sc.processar_html(_html("jsonld.html"), "titulo base curto")
check(_early, "[11] metadata estruturada suficiente dispara early stop")
check(all(f["tier"] == 1 for f in _frags),
      f"[12] com early stop o corpo NÃO é aberto ({sorted({f['method'] for f in _frags})})")
_f2, _e2 = sc.processar_html(_html("sem_metadata_menu.html"), "titulo base curto")
check(not _e2 and any(f["tier"] == 2 for f in _f2),
      "[13] sem metadata alguma a ladder desce para o corpo")
_f3, _e3 = sc.processar_html(_html("script_em_paragrafo.html"), "titulo base curto")
check(_e3 and all(f["tier"] == 1 for f in _f3),
      "[13b] metadata limpa poupa o corpo mesmo quando o corpo é lixo")
check(sc.TIER["jsonld:description"][0] < sc.TIER["html:paragrafos"][0],
      "[14] a ordem da ladder é estruturado antes de texto de página")

print()
print("=" * 96)
print("BLOCO C — seleção por qualidade: sem contexto é melhor que contexto sujo")
print("=" * 96)
_menu = ("Menu About Contributors Blogs Podcasts Search Restructuring Perspectives "
         "6 minute read October 22, 2025 Categories: Bankruptcy Sales , Chapter 11 "
         "By John Doe. Compartilhe esta pagina e assine a newsletter.")
_sujo = {"method": "html:paragrafos", "tier": 2, "content_hash": "a",
         "text_excerpt": _menu, **sc.qualidade(_menu, "titulo qualquer")}
check(_sujo["quality_flags"], f"[15] menu de site é marcado como sujo: {_sujo['quality_flags']}")
_sel, _motivo = sc.selecionar([_sujo])
check(_sel is None,
      f"[16] fragmento sujo NÃO é selecionado mesmo sendo o único ({_motivo[:48]})")
_limpo_txt = ("Representantes da companhia descobriram contas abertas sem "
              "conhecimento dos clientes, causando prejuizo relevante ao caixa.")
_limpo = {"method": "meta:og:description", "tier": 1, "content_hash": "b",
          "text_excerpt": _limpo_txt, **sc.qualidade(_limpo_txt, "titulo qualquer")}
_sel2, _m2 = sc.selecionar([_sujo, _limpo])
check(_sel2 and _sel2["method"] == "meta:og:description",
      "[17] havendo fragmento limpo, ele vence o volumoso e sujo")
_grande_txt = "palavra " * 300 + "e outras coisas irrelevantes de rodape."
_grande = {"method": "html:paragrafos", "tier": 2, "content_hash": "c",
           "text_excerpt": _grande_txt, **sc.qualidade(_grande_txt, "titulo")}
_sel3, _ = sc.selecionar([_grande, _limpo])
check(_sel3["method"] == "meta:og:description",
      "[18] tamanho NÃO decide: estruturado limpo vence corpo maior")
check(sc.selecionar([])[0] is None, "[19] sem fragmentos não há seleção inventada")
# regressão exata do caso que quebrou o shadow em R5b
_gm, _ = sc.processar_html(_html("sem_metadata_menu.html"), "Bankruptcy Court Orders Texas")
_selgm, _mgm = sc.selecionar(_gm)
check(_selgm is None,
      f"[19b] artigo sem metadata cujo corpo é menu NÃO é enriquecido ({_mgm[:44]})")

print()
print("=" * 96)
print("BLOCO D — qualidade e conteúdo efetivamente novo")
print("=" * 96)
_q = sc.qualidade("Titulo repetido igualzinho", "Titulo repetido igualzinho")
check("duplicated_base" in _q["quality_flags"],
      "[20] repetição da base é marcada, não contada como enriquecimento")
_js = dict(__import__("reliability_enrichment").extrair(_html("script_em_paragrafo.html")))
check("codigo_javascript" in sc.qualidade(_js["html:paragrafos"], "x")["quality_flags"],
      "[21] JavaScript capturado em `<p>` é marcado como código")
check(not sc.qualidade("curto", "x")["sentence_like"],
      "[22] texto sem cara de frase não passa por sentence_like")
check(sc.suficiente({"effective_new_tokens": 20, "sentence_like": True,
                     "quality_flags": []}),
      "[23] suficiência exige conteúdo novo, frase e zero flag")
check(not sc.suficiente({"effective_new_tokens": 20, "sentence_like": True,
                         "quality_flags": ["newsletter"]}),
      "[24] um único flag de sujeira derruba a suficiência")

print()
print("=" * 96)
print("BLOCO E — side-car: cache, versionamento, limites e isolamento")
print("=" * 96)
check(sc.SIDECAR.name != "risk_history.json",
      f"[25] o side-car é arquivo separado: {sc.SIDECAR.name}")
_hist_src = io.open("reliability_enrichment_sidecar.py", encoding="utf-8").read()
check(all(x not in _hist_src for x in ("save_history", "merge_into_history", "--apply")),
      "[26] o módulo não tem caminho de escrita em history")
check(sc._reaproveitavel({"extractor_version": sc.EXTRACTOR_VERSION,
                          "policy_version": pol.POLICY_VERSION, "status": "OK"}),
      "[27] cache reaproveita coleta da mesma versão")
check(not sc._reaproveitavel({"extractor_version": "antiga",
                              "policy_version": pol.POLICY_VERSION, "status": "OK"}),
      "[28] extractor antigo invalida o cache")
check(not sc._reaproveitavel({"extractor_version": sc.EXTRACTOR_VERSION,
                              "policy_version": "antiga", "status": "OK"}),
      "[29] política antiga invalida o cache")
check(not sc._reaproveitavel({"extractor_version": sc.EXTRACTOR_VERSION,
                              "policy_version": pol.POLICY_VERSION, "status": "ERROR"}),
      "[30] erro transitório é re-tentado, robots/source não")
check(all(len(f["text_excerpt"]) <= sc.MAX_EXCERPT for f in _frags),
      f"[31] excerto respeita o limite de {sc.MAX_EXCERPT} caracteres")
check(len(_frags) <= sc.MAX_FRAGMENTOS,
      f"[32] no máximo {sc.MAX_FRAGMENTOS} fragmentos por artigo")
check("html" not in {k for f in _frags for k in f},
      "[33] o HTML completo nunca é persistido")

print()
print("=" * 96)
print("BLOCO F — determinismo e gate de robots")
print("=" * 96)
check(sc.processar_html(_html("jsonld.html"), "b") ==
      sc.processar_html(_html("jsonld.html"), "b"),
      "[34] mesmo HTML produz exatamente o mesmo resultado")
_corpo = _hist_src.split("def enriquecer_url(")[1].split("\ndef ")[0]
check(_corpo.index("_robots_permite") < _corpo.index("requests.get"),
      "[35] robots é consultado ANTES da requisição")
check("BLOCKED_BY_ROBOTS" in _corpo, "[36] disallow encerra o artigo, sem rota alternativa")
check("BLOCKED_BY_SOURCE" in _corpo and "401" in _corpo,
      "[37] paywall/auth vira BLOCKED_BY_SOURCE, sem tentativa de bypass")

print()
print("=" * 96)
print(f"RESULTADO SELECTIVE ENRICHMENT: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 96)
if FAIL:
    raise SystemExit(1)
