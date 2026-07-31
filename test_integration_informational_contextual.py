"""
test_integration_informational_contextual.py — testes de INTEGRAÇÃO entre as
duas funcionalidades trazidas para a branch `integration/informational-
entity-resolution`:

  (A) `fix/direct-informational-events` (ec1866c) — separação entre eventos
      diretos NÃO pontuáveis do próprio emissor (`informational_events_by_
      company`) e contexto de terceiro real (`context_events_by_company`).
  (B) `test/peru-watchlist-candidates` (c71796d) — resolução contextual de
      entidade OPT-IN (`resolve_entity_match`, `resolve_related_entity_
      mentions`, `search_terms`/`entity_cues`/`exclusion_cues`/
      `related_entities`, `entity_scope`/`entity_confidence`).

Sem rede, fixtures determinísticas inline (os mesmos 4 candidatos peruanos
usados em `test_entity_resolution.py`/`test_pipeline_integration.py` — NÃO
fazem parte de `config_risco.yaml` nem da watchlist real). `cfg =
rd.load_config("config_risco.yaml")` é usado só para reaproveitar a
TAXONOMIA real (52 eventos) — o `watchlist` é sempre substituído pelas
fixtures de teste, nunca os 160 emissores reais.

Cada título/resumo abaixo foi verificado para disparar exatamente o(s)
evento(s) esperado(s) via `classify_article` com a taxonomia real (não
reinventamos taxonomia paralela) — ver comentário em cada teste.
"""
import copy
import json

import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  ❌ [{n:02d}] {desc}")


cfg = rd.load_config("config_risco.yaml")  # só para a taxonomia real; watchlist é sempre sobrescrita


def art(title, summary=""):
    return {"title": title, "summary": summary}


def run(title, summary, watch):
    c = copy.deepcopy(cfg)
    c["watchlist"] = watch
    a = art(title, summary)
    rd.classify_and_attribute(a, c)
    return a


def _norm(d):
    return json.dumps(d, sort_keys=True, default=str)


# ── Fixtures (idênticas às usadas em test_entity_resolution.py / ──────────
# ── test_pipeline_integration.py, com pequenos acréscimos de cues só para ──
# ── exercitar a regra 5.4 de sobreposição alias+cues x exclusion_cue) ─────
YURA = {
    "name": "Yura",
    "aliases": ["Yura S.A.", "Yura SA", "Cemento Yura", "Cementos Yura", "Cementera Yura"],
    "search_terms": ["Yura S.A.", "Cemento Yura", "Cementera Yura", "Yura cemento"],
    "entity_cues": ["cemento", "cementera", "clinker", "grupo gloria", "indecopi",
                    "arequipa", "sancionada", "conducta anticompetitiva", "fabrica",
                    "los olivos"],
    "entity_cues_min": 2,
    "exclusion_cues": ["distrito de yura", "carretera de yura", "homicidio en yura",
                       "protesta", "vecinos del distrito", "yura corporation",
                       "south korea"],
    "related_entities": [
        {"entity_name": "SOBOCE", "legal_name": None, "relationship": "subsidiary_international",
         "attribution_mode": "context_unless_direct_holding_impact", "aliases": ["SOBOCE"]},
    ],
    "entity_scope": "legal_entity", "entity_confidence": "high",
}

COAZUCAR = {
    "name": "Coazucar",
    "aliases": ["Coazucar", "Coazúcar", "Coazucar del Perú", "Corporación Azucarera del Perú"],
    "entity_cues": ["azucar", "azucarera", "grupo gloria", "casa grande", "cartavio",
                    "chicama", "bonos", "resultados consolidados", "holding agroindustrial"],
    "entity_cues_min": 2,
    "exclusion_cues": ["la troncal", "ecuador", "ingenio san carlos"],
    "related_entities": [
        {"entity_name": "Casa Grande S.A.A.", "legal_name": "Casa Grande S.A.A.",
         "relationship": "subsidiary", "attribution_mode": "context_unless_direct_holding_impact",
         "aliases": ["Casa Grande"]},
    ],
    "entity_scope": "holding", "entity_confidence": "high",
}

YOBEL_BRAND = {
    "name": "Yobel",
    "aliases": ["Yobel", "Yobel Perú", "Yobel Peru", "Yobel SCM"],
    "entity_cues": ["cosméticos", "perfumería", "supply chain", "los olivos", "lima, perú",
                    "belcorp", "yanbal", "logística", "almacén", "fábrica"],
    "entity_cues_min": 2,
    "exclusion_cues": ["yobel méxico", "yobel mexico", "yobel colombia", "yobel ecuador"],
    "entity_scope": "brand_group", "entity_confidence": "medium", "scoreable": False,
}

TRUPAL_LEGACY = {"name": "Trupal", "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"]}

AMBEV_LEGACY = {"name": "Ambev", "aliases": ["Ambev", "ABEV3"]}

print("=" * 78)
print("TESTES DE INTEGRAÇÃO — informational_events_by_company (ec1866c) x")
print("resolução contextual de entidade opt-in (c71796d)")
print("=" * 78)

# ── 1) Yura direta e negativa ──────────────────────────────────────────────
# Dispara `investigacao_regulatoria`/`investigacao_gestora` (keyword literal
# "expediente sancionador"), sem colapso de família nem fase mitigadora →
# scoreable=True por padrão em `resolve_article_semantics`. Yura é sujeito
# direto (subject_company==monitored_company).
a1 = run("Yura S.A. enfrenta expediente sancionador de Indecopi por conducta anticompetitiva.",
         "", [YURA])
check(1, "Yura identificada como sujeito direto (companies == ['Yura'])",
      a1.get("companies") == ["Yura"])
check(1, "evento negativo pontuável só em events_by_company (nunca em informational/context)",
      bool((a1.get("events_by_company") or {}).get("Yura"))
      and not (a1.get("informational_events_by_company") or {}).get("Yura")
      and not (a1.get("context_events_by_company") or {}).get("Yura"))

# ── 2) Yura direta e positiva ──────────────────────────────────────────────
# "adquisición" dispara `ma`; `ma_is_legitimate` classifica como recompra de
# ações próprias ("buyback ... acciones propias") → event_id_corrigido =
# recompra_acoes, direction=positiva, scoreable=False, is_direct (subject ==
# Yura) → vai para informational_events_by_company, NUNCA para events_by_
# company/context/event_ids_for.
a2 = run("Cemento Yura anuncia adquisicion; la operacion es en realidad un "
         "buyback de acciones propias.", "", [YURA])
_info2 = (a2.get("informational_events_by_company") or {}).get("Yura") or []
check(2, "Yura identificada; evento direto positivo (recompra_acoes) presente",
      a2.get("companies") == ["Yura"] and len(_info2) == 1
      and _info2[0]["event_id"] == "recompra_acoes")
check(2, "direction=positiva, scoreable=False, só em informational_events_by_company",
      _info2 and _info2[0]["direction"] == "positiva" and _info2[0]["scoreable"] is False
      and not (a2.get("events_by_company") or {}).get("Yura")
      and not (a2.get("context_events_by_company") or {}).get("Yura")
      and "recompra_acoes" not in rd.event_ids_for(a2, "Yura"))

# ── 3) Yura geográfica ─────────────────────────────────────────────────────
# Nenhum alias corporativo presente ("distrito de Yura" não é "Cemento
# Yura"/"Yura S.A."); sem cues operacionais suficientes → matched=False,
# nenhum container, nenhum score.
a3 = run("Incendio de pastizales en el distrito de Yura.", "", [YURA])
check(3, "matched=False; nenhum container populado para Yura",
      "Yura" not in (a3.get("companies") or [])
      and not (a3.get("events_by_company") or {}).get("Yura")
      and not (a3.get("informational_events_by_company") or {}).get("Yura")
      and not (a3.get("context_events_by_company") or {}).get("Yura"))

# ── 4) Yura corporativa no distrito de Yura ───────────────────────────────
# Alias corporativo "Cemento Yura" + >=2 entity_cues operacionais ("cemento",
# "fabrica") presentes JUNTO com a exclusion_cue geográfica "distrito de
# yura" → a regra de integração 5.4 (alias_and_operational_cues_override_
# exclusion) atribui a Yura, e o evento negativo "guidance_negativo" (keyword
# "recorta previsiones") é roteado normalmente para events_by_company, sem
# duplicação.
a4 = run("Incendio afecta la fabrica de Cemento Yura en el distrito de Yura; "
         "la compania recorta previsiones de produccion.", "", [YURA])
_trace4 = next((t for t in a4.get("entity_resolution_trace", []) if t["candidate_company"] == "Yura"), {})
check(4, "exclusion geográfica NÃO bloqueia alias corporativo + cues operacionais",
      a4.get("companies") == ["Yura"]
      and _trace4.get("rule") == "alias_and_operational_cues_override_exclusion")
check(4, "evento roteado corretamente em events_by_company, sem duplicação em nenhum outro container",
      (a4.get("events_by_company") or {}).get("Yura") == ["guidance_negativo"]
      and not (a4.get("informational_events_by_company") or {}).get("Yura")
      and not (a4.get("context_events_by_company") or {}).get("Yura"))
# regra original preservada: alias presente + exclusion, mas SEM cues
# operacionais suficientes (só 1: "yura s.a.") continua REJEITADO — mesmo
# caso do teste [26] de test_entity_resolution.py, aqui verificado de novo
# via classify_and_attribute ponta-a-ponta (não só resolve_entity_match).
a4b = run("Homicidio en Yura: Yura S.A. no tiene relacion con el hecho.", "", [YURA])
check(4, "sem cues operacionais suficientes, exclusion_cue mantém precedência "
        "absoluta mesmo com alias presente (regra original preservada)",
      "Yura" not in (a4b.get("companies") or []))

# ── 5) Coazucar/Casa Grande (related entity, evento negativo) ─────────────
# "Casa Grande" não é alias de Coazucar; só 1 entity_cue de Coazucar bate
# ("casa grande") — abaixo do mínimo (2) — Coazucar não é atribuída. O
# evento (lucro_abaixo_consenso) nunca pontua nem informa a holding; a
# menção à relacionada fica só como telemetria (resolve_related_entity_
# mentions), nunca transferida automaticamente.
a5 = run("Casa Grande reporta perdidas operativas; resultado decepciona a los inversores.",
         "", [COAZUCAR])
_rel5 = next((t.get("related_entities_mentioned") for t in a5.get("entity_resolution_trace", [])
             if t["candidate_company"] == "Coazucar"), [])
check(5, "Coazucar NÃO recebe score nem informativo pelo evento da subsidiária",
      "Coazucar" not in (a5.get("companies") or [])
      and not (a5.get("events_by_company") or {}).get("Coazucar")
      and not (a5.get("informational_events_by_company") or {}).get("Coazucar"))
check(5, "menção à Casa Grande S.A.A. registrada na telemetria (subject != Coazucar)",
      len(_rel5) == 1 and _rel5[0]["entity_name"] == "Casa Grande S.A.A.")

# ── 6) Coazucar consolidada (evento direto, não related-a-si-mesma) ──────
a6 = run("Coazucar presenta resultados consolidados de sus subsidiarias; "
         "resultado decepciona a los inversores.", "", [COAZUCAR])
_rel6 = next((t.get("related_entities_mentioned") for t in a6.get("entity_resolution_trace", [])
             if t["candidate_company"] == "Coazucar"), [])
check(6, "Coazucar identificada como sujeito DIRETO (evento consolidado é dela mesma)",
      a6.get("companies") == ["Coazucar"]
      and (a6.get("events_by_company") or {}).get("Coazucar") == ["lucro_abaixo_consenso"])
check(6, "Coazucar nunca tratada como relacionada a si mesma",
      _rel6 == [])

# ── 7) Yobel paralização (brand_group) ────────────────────────────────────
# "investigación regulatoria"/"under investigation" (keywords literais)
# disparam eventos negativos que SERIAM pontuáveis por padrão — mas
# entity_scope=brand_group (regra 5.4) suprime de events_by_company via
# `suppress_non_scoreable_entity_scopes` e os torna COMPATÍVEIS com
# informational_events_by_company (regra de integração adicionada nesta
# branch), registrando entity_scope/entity_confidence/likely_entity/
# confirmação pendente.
a7 = run("Yobel bajo investigacion regulatoria: incendio paraliza temporalmente "
         "sus operaciones en Los Olivos.", "", [YOBEL_BRAND])
_info7 = (a7.get("informational_events_by_company") or {}).get("Yobel") or []
check(7, "Yobel (brand_group) identificada; nunca pontua (fora de events_by_company/event_ids_for)",
      a7.get("companies") == ["Yobel"]
      and not (a7.get("events_by_company") or {}).get("Yobel")
      and not any(rd.event_ids_for(a7, "Yobel")))
check(7, "evento direto autônomo da marca é COMPATÍVEL com informational_events_by_company "
        "(entity_scope/entity_confidence/likely_entity/pendência registrados)",
      len(_info7) == 2
      and all(e["entity_scope"] == "brand_group" and e["entity_confidence"] == "medium"
              and e["likely_entity"] == "Yobel" and e["scoreable"] is False
              for e in _info7))
check(7, "nunca aparece em context_events_by_company",
      not (a7.get("context_events_by_company") or {}).get("Yobel"))

# variante: entity_pending_confirmation também nunca pontua e registra a
# pendência de confirmação explicitamente (regra 5.5).
YOBEL_PENDING = {**YOBEL_BRAND, "entity_scope": "entity_pending_confirmation", "scoreable": True}
a7b = run("Yobel bajo investigacion regulatoria: incendio paraliza temporalmente "
          "sus operaciones en Los Olivos.", "", [YOBEL_PENDING])
_info7b = (a7b.get("informational_events_by_company") or {}).get("Yobel") or []
check(7, "entity_pending_confirmation também nunca pontua e marca entity_pending_confirmation=True",
      not (a7b.get("events_by_company") or {}).get("Yobel")
      and len(_info7b) == 2
      and all(e["entity_pending_confirmation"] is True and e["confirmation_status"] == "pendente"
              for e in _info7b))

# ── 8) Yobel México ────────────────────────────────────────────────────────
a8 = run("Yobel Mexico inaugura un nuevo centro de distribucion.", "", [YOBEL_BRAND])
check(8, "Yobel México NÃO atribuída à operação peruana; nenhum container, nenhum score",
      "Yobel" not in (a8.get("companies") or [])
      and not (a8.get("events_by_company") or {})
      and not (a8.get("informational_events_by_company") or {}))

# ── 9) Trupal resultado positivo (cadastro legado, sem opt-in) ────────────
# Mesmo padrão de recompra_acoes do teste 2, mas com Trupal 100% legado (só
# aliases) — prova que a rota informational funciona independentemente da
# camada contextual peruana estar ou não opt-in.
a9 = run("Trupal anuncia adquisicion; la operacion es en realidad un buyback "
         "de acciones propias.", "", [TRUPAL_LEGACY])
_info9 = (a9.get("informational_events_by_company") or {}).get("Trupal") or []
check(9, "Trupal (legado) identificada; evento direto positivo/informativo sem score",
      a9.get("companies") == ["Trupal"] and len(_info9) == 1
      and _info9[0]["direction"] == "positiva" and _info9[0]["scoreable"] is False
      and not (a9.get("events_by_company") or {}).get("Trupal"))
check(9, "Trupal legado não gera entity_resolution_trace (uses_contextual_entity_resolution=False)",
      not a9.get("entity_resolution_trace"))

# ── 10) Related entity negativa não vira informativo/pontuável da holding ─
# Evento de default ("incumplimiento de pago") da subsidiária Casa Grande:
# só 1 cue de Coazucar bate ("casa grande") — abaixo do mínimo — Coazucar
# não é atribuída, não pontua, não aparece em informational.
a10 = run("Casa Grande reporta incumplimiento de pago de una obligacion financiera.",
          "", [COAZUCAR])
check(10, "evento negativo da subsidiária não vira informativo nem pontuável da holding",
      "Coazucar" not in (a10.get("companies") or [])
      and not (a10.get("events_by_company") or {}).get("Coazucar")
      and not (a10.get("informational_events_by_company") or {}).get("Coazucar"))
_rel10 = next((t.get("related_entities_mentioned") for t in a10.get("entity_resolution_trace", [])
              if t["candidate_company"] == "Coazucar"), [])
check(10, "menção à relacionada continua registrada na telemetria (não some do radar)",
      len(_rel10) == 1 and _rel10[0]["entity_name"] == "Casa Grande S.A.A.")

# ── 11) Family secondary contextual (entidade opt-in corretamente resolvida) ─
# "rebaja de calificación" (rebaixamento_rating) + "perspectiva para
# negativa" (outlook_negativo) na MESMA notícia → colapso de família de
# rating: outlook é absorvido como SECUNDÁRIO do rebaixamento (mesma ação
# econômica). Mesmo com Yura corretamente resolvida via camada contextual
# opt-in, o outlook secundário NUNCA cria card independente em informational
# nem em context — fica só como metadado (`secondary_events`).
a11 = run("Yura S.A. sufre rebaja de calificacion y revisa perspectiva para negativa.",
          "", [YURA])
check(11, "Yura identificada (camada contextual) e apenas o evento principal pontua",
      a11.get("companies") == ["Yura"]
      and (a11.get("events_by_company") or {}).get("Yura") == ["rebaixamento_rating"])
check(11, "outlook secundário não cria card informativo nem de contexto — só metadado em secondary_events",
      not (a11.get("informational_events_by_company") or {}).get("Yura")
      and not (a11.get("context_events_by_company") or {}).get("Yura")
      and any(e["id"] == "outlook_negativo" and e["primary_event"] == "rebaixamento_rating"
              for e in (a11.get("secondary_events") or [])))
check(11, "não duplica ocorrência: só 1 evento em events_by_company",
      len((a11.get("events_by_company") or {}).get("Yura") or []) == 1)

# ── 12) Exclusividade dos containers (todos os artigos acima) ─────────────
_all_articles = {
    "1": a1, "2": a2, "3": a3, "4": a4, "5": a5, "6": a6, "7": a7, "7b": a7b,
    "8": a8, "9": a9, "10": a10, "11": a11,
}
_violacoes = []
for _label, _a in _all_articles.items():
    _ebc = _a.get("events_by_company") or {}
    _ctx = _a.get("context_events_by_company") or {}
    _info = _a.get("informational_events_by_company") or {}
    for _co in set(list(_ebc) + list(_ctx) + list(_info)):
        _ids_score = set(_ebc.get(_co) or [])
        _ids_ctx = {c.get("event_id") for c in (_ctx.get(_co) or [])}
        _ids_info = {c.get("event_id") for c in (_info.get(_co) or [])}
        _inter = ((_ids_score & _ids_ctx) | (_ids_score & _ids_info) | (_ids_ctx & _ids_info))
        if _inter:
            _violacoes.append((_label, _co, _inter))
check(12, "para cada empresa/event_id, nenhum evento aparece em mais de um container "
         "(events_by_company / context_events_by_company / informational_events_by_company) "
         f"em nenhum dos {len(_all_articles)} artigos testados",
      _violacoes == [])

# ── 13) event_ids_for exclui informational/brand_group/pending/family_secondary ─
_rec13 = {
    "events_by_company": {"Acme": ["evento_pontuavel"]},
    "context_events_by_company": {"Acme": [{"event_id": "evento_contexto"}]},
    "informational_events_by_company": {"Acme": [{"event_id": "evento_informativo"},
                                                  {"event_id": "evento_brand_group"}]},
    "event_assessments": [{"company": "Acme", "event_id": "evento_absorvido",
                           "family_secondary": True, "primary_event_id": "evento_pontuavel"}],
}
_ids13 = rd.event_ids_for(_rec13, "Acme")
check(13, "event_ids_for devolve SÓ o evento pontuável, excluindo informational/"
         "brand_group/pending/family_secondary/related não pontuável",
      _ids13 == ["evento_pontuavel"])

# ── 14) Idempotência combinada (camada contextual + informativa juntas) ──
# Segue o mesmo padrão das suítes existentes (test_pipeline_integration.py
# [15], test_fetch_all.py [13]): duas instâncias INDEPENDENTES do mesmo
# artigo bruto, processadas pelo pipeline integrado, produzem exatamente o
# mesmo JSON normalizado (chaves ordenadas) — sem duplicação, mesma ordem,
# mesmos scores/decisões.
_c14 = copy.deepcopy(cfg); _c14["watchlist"] = [YOBEL_BRAND]
_a14a = art("Yobel bajo investigacion regulatoria: incendio paraliza temporalmente "
            "sus operaciones en Los Olivos.")
_a14b = art("Yobel bajo investigacion regulatoria: incendio paraliza temporalmente "
            "sus operaciones en Los Olivos.")
rd.classify_and_attribute(_a14a, _c14)
rd.classify_and_attribute(_a14b, _c14)
check(14, "duas execuções independentes do pipeline integrado (brand_group + "
         "eventos informativos) produzem JSON idêntico", _norm(_a14a) == _norm(_a14b))

_c14b = copy.deepcopy(cfg); _c14b["watchlist"] = [YURA]
_a14c = art("Yura S.A. sufre rebaja de calificacion y revisa perspectiva para negativa.")
_a14d = art("Yura S.A. sufre rebaja de calificacion y revisa perspectiva para negativa.")
rd.classify_and_attribute(_a14c, _c14b)
rd.classify_and_attribute(_a14d, _c14b)
check(14, "idem para o caso de família secundária + resolução contextual opt-in",
      _norm(_a14c) == _norm(_a14d))

# ── 15) Compatibilidade legada (registro sem os novos campos) ────────────
a15 = run("Ambev anuncia resultados do trimestre.", "", [AMBEV_LEGACY])
check(15, "cadastro 100% legado (sem entity_scope/search_terms/entity_cues) "
        "não gera entity_resolution_trace nem exige telemetria contextual",
      not a15.get("entity_resolution_trace"))
check(15, "informational_events_by_company ausente é aceitável para registro legado "
        "(sem evento disparado nesse título, caminho antigo intacto)",
      a15.get("informational_events_by_company") is None
      and a15.get("companies") == ["Ambev"])
# idempotência via apply_semantics_to_record diretamente no rec (mesmo
# padrão de test_semantica.py [F6]) para um rec no formato ANTIGO (sem
# informational_events_by_company pré-existente) — não deve quebrar nem
# exigir o campo.
_rec15 = {"title": "Ambev anuncia resultados do trimestre.", "summary": "",
         "events_by_company": {"Ambev": []}}
_res15a = sa.apply_semantics_to_record(copy.deepcopy(_rec15), cfg)
_res15b = sa.apply_semantics_to_record(copy.deepcopy(_rec15), cfg)
check(15, "apply_semantics_to_record em registro legado sem eventos não quebra "
        "e é estável entre execuções",
      _res15a == _res15b and _res15a["mudou"] is False)

print("=" * 78)
print(f"RESULTADO INTEGRAÇÃO INFORMATIONAL x CONTEXTUAL: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 78)

import sys
sys.exit(0 if FAIL == 0 else 1)
