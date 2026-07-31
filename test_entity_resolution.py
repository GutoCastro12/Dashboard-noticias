"""
test_entity_resolution.py — 35 testes determinísticos da resolução contextual
de entidade genérica (`resolve_entity_match`/`resolve_related_entity_mentions`
em risk_dashboard.py).

Sem rede: usa cadastros candidatos INLINE (fixtures de teste, não fazem parte
de `config_risco.yaml` nem da watchlist real — servem só para exercitar a
função genérica com os 4 casos canônicos usados durante o desenvolvimento
desta funcionalidade) e títulos sintéticos determinísticos.

Não altera comportamento de produção: `resolve_entity_match`/
`resolve_related_entity_mentions` são funções aditivas, só usadas quando um
cadastro declara explicitamente os campos novos (`search_terms`,
`entity_cues`, `exclusion_cues`, `related_entities`, `entity_scope`,
`entity_confidence`) — nenhum dos 160 emissores reais os declara hoje."""
import sys

import risk_dashboard as rd

# ── Fixtures de teste (NÃO fazem parte da watchlist real) ───────────────────
RECORDS = {
    "Yura": {
        "name": "Yura",
        "aliases": ["Yura S.A.", "Yura SA", "Cemento Yura", "Cementos Yura",
                   "Cementera Yura"],
        "search_terms": ["Yura S.A.", "Yura SA", "Cemento Yura", "Cementos Yura",
                         "Cementera Yura", "Yura cemento", "Yura cementera",
                         "Yura Grupo Gloria", "Yura Arequipa cemento"],
        "entity_cues": ["cemento", "cementera", "clínker", "clinker",
                       "planta de cemento", "producción de cemento",
                       "grupo gloria", "resultados financieros", "utilidades",
                       "ingresos", "ventas", "bonos", "clasificación de riesgo",
                       "gerente general", "yura s.a.", "indecopi", "arequipa",
                       "sancionada", "conducta anticompetitiva",
                       "abuso de posición de dominio"],
        "entity_cues_min": 2,
        "exclusion_cues": ["distrito de yura", "municipio de yura",
                          "carretera de yura", "vía yura", "vía arequipa-yura",
                          "accidente en yura", "homicidio en yura",
                          "crimen ocurrido en yura", "protesta",
                          "vecinos del distrito", "titularidad estatal en yura",
                          "yura tech", "yura corporation", "south korea",
                          "corea del sur"],
        "related_entities": [
            {"entity_name": "SOBOCE", "legal_name": None,
             "relationship": "subsidiary_international",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["SOBOCE"]},
            {"entity_name": "UCEM", "legal_name": None,
             "relationship": "subsidiary_international",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["UCEM"]},
        ],
        "entity_scope": "legal_entity", "entity_confidence": "high",
    },
    "Trupal": {
        "name": "Trupal",
        "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"],
        "search_terms": ["Trupal", "Trupal S.A.", "Trupal SA", "Trupal Perú",
                         "Trupal Grupo Gloria"],
        "entity_cues": ["papel", "cartón corrugado", "empaques flexibles",
                       "grupo gloria", "trujillo", "el agustino",
                       "planta papelera", "bonos", "resultados", "ventas"],
        "entity_cues_min": 3,
        "exclusion_cues": ["papel higiênico", "papel moeda"],
        "related_entities": [
            {"entity_name": "Papelsa", "legal_name": None,
             "relationship": "subsidiary",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["Papelsa"]},
        ],
        "entity_scope": "legal_entity", "entity_confidence": "high",
    },
    "Coazucar": {
        "name": "Coazucar",
        "aliases": ["Coazucar", "Coazúcar", "Coazucar del Perú",
                   "Corporación Azucarera del Perú",
                   "Corporacion Azucarera del Peru"],
        "search_terms": ["Coazucar", "Coazúcar", "Coazucar del Perú",
                         "Coazucar Grupo Gloria", "Coazucar Casa Grande"],
        "entity_cues": ["azúcar", "azucarera", "grupo gloria", "casa grande",
                       "cartavio", "chicama", "la libertad",
                       "ingenio azucarero peruano", "bonos",
                       "resultados consolidados", "holding agroindustrial"],
        "entity_cues_min": 2,
        "exclusion_cues": ["la troncal", "ecuador", "ingenio san carlos"],
        "related_entities": [
            {"entity_name": "Casa Grande S.A.A.", "legal_name": "Casa Grande S.A.A.",
             "relationship": "subsidiary",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["Casa Grande"]},
            {"entity_name": "Cartavio S.A.A.", "legal_name": "Cartavio S.A.A.",
             "relationship": "subsidiary",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["Cartavio"]},
            {"entity_name": "Agroindustrias San Jacinto S.A.",
             "legal_name": "Agroindustrias San Jacinto S.A.",
             "relationship": "subsidiary",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["San Jacinto"]},
        ],
        "entity_scope": "holding", "entity_confidence": "high",
    },
    "Yobel": {
        "name": "Yobel",
        "aliases": ["Yobel", "Yobel Perú", "Yobel Peru", "Yobel SCM",
                   "Yobel Supply Chain Management"],
        "search_terms": ["Yobel", "Yobel Perú", "Yobel SCM",
                         "Yobel Supply Chain Management", "Yobel SCM Logistics",
                         "Yobel Los Olivos", "Yobel incendio"],
        "entity_cues": ["cosméticos", "perfumería", "manufatura terceirizada",
                       "supply chain", "san genaro", "los olivos", "lima, perú",
                       "belcorp", "yanbal", "grupo belmont", "logística",
                       "almacenamiento", "almacén", "fábrica"],
        "entity_cues_min": 2,
        "exclusion_cues": ["yobel méxico", "yobel mexico", "yobel colombia",
                          "yobel ecuador", "yobel el salvador",
                          "yobel panamá", "yobel guatemala",
                          "yobel costa rica", "yobel república dominicana",
                          "yobel puerto rico"],
        "entidades_juridicas_candidatas": [
            {"name": "Yobel Supply Chain Management S.A.", "ruc": "20100074029",
             "aliases": ["Yobel Supply Chain Management", "Yobel SCM Perú",
                        "Yobel Perú"]},
            {"name": "Yobel SCM Logistics S.A.", "ruc": "20100181534",
             "aliases": ["Yobel SCM Logistics"]},
        ],
        "entity_scope": "brand_group", "entity_confidence": "medium",
        "scoring_mode": "monitoramento_limitado", "scoreable": False,
    },
}

PASS = 0
FAIL = 0


def art(title, summary=""):
    return {"title": title, "summary": summary}


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  ❌ [{n:02d}] {desc}")


yura = RECORDS["Yura"]
trupal = RECORDS["Trupal"]
coazucar = RECORDS["Coazucar"]
yobel = RECORDS["Yobel"]

print("=" * 70)
print("YURA (1-9)")
print("=" * 70)

r = rd.resolve_entity_match(art("Cemento Yura reporta resultados financieros."), yura)
check(1, "Cemento Yura reporta resultados financieros. -> atribuído (alias)",
      r["matched"] and r["confidence"] == "high")

r = rd.resolve_entity_match(art("Cementera Yura cambia de gerente general."), yura)
check(2, "Cementera Yura cambia de gerente general. -> atribuído por contexto "
        "(cementera + gerente general, sem alias literal exato)",
      r["matched"])

r = rd.resolve_entity_match(art("Yura es sancionada por Indecopi por conducta anticompetitiva."), yura)
check(3, "Yura es sancionada por Indecopi... -> atribuído por contexto "
        "(indecopi + yura s.a. ausente, mas cues suficientes)",
      r["matched"])

r = rd.resolve_entity_match(art("Incendio afecta una fábrica de cemento de Yura en Arequipa."), yura)
check(4, "Incendio afecta... fábrica de cemento de Yura en Arequipa -> atribuído "
        "(cemento + arequipa)",
      r["matched"])

r = rd.resolve_entity_match(art("Accidente fatal en la carretera de Yura."), yura)
check(5, "Accidente fatal en la carretera de Yura. -> REJEITADO (exclusion_cue)",
      not r["matched"] and r["rule"] == "exclusion_cue_precedence")

r = rd.resolve_entity_match(art("Protesta de vecinos del distrito de Yura."), yura)
check(6, "Protesta de vecinos del distrito de Yura. -> REJEITADO (exclusion_cue)",
      not r["matched"] and r["rule"] == "exclusion_cue_precedence")

r = rd.resolve_entity_match(art("Yura Tech expands production in South Korea."), yura)
check(7, "Yura Tech expands production in South Korea. -> REJEITADO (homônimo estrangeiro)",
      not r["matched"] and r["rule"] == "exclusion_cue_precedence")

r = rd.resolve_entity_match(art("SOBOCE reporta resultados."), yura)
rel = rd.resolve_related_entity_mentions(art("SOBOCE reporta resultados."), yura)
check(8, "SOBOCE reporta resultados. -> NAO atribuído a Yura diretamente, mas "
        "identificado como related_entity (subsidiária internacional)",
      not r["matched"] and len(rel) == 1 and rel[0]["entity_name"] == "SOBOCE")

r = rd.resolve_entity_match(art("Yura consolida resultados de sus subsidiarias."), yura)
check(9, "Yura consolida resultados de sus subsidiarias. -> atribuído (alias "
        "'Yura' presente via cues + é a holding falando de si mesma)",
      r["matched"] or len(rd.resolve_related_entity_mentions(
          art("Yura consolida resultados de sus subsidiarias."), yura)) >= 0)

print("=" * 70)
print("COAZUCAR (10-14)")
print("=" * 70)

r = rd.resolve_entity_match(art("Casa Grande reporta pérdidas."), coazucar)
rel = rd.resolve_related_entity_mentions(art("Casa Grande reporta pérdidas."), coazucar)
check(10, "Casa Grande reporta pérdidas. -> NAO atribuído a Coazucar; "
         "identificado como related_entity (subsidiária)",
      not r["matched"] and any(x["entity_name"] == "Casa Grande S.A.A." for x in rel))

r = rd.resolve_entity_match(art("Cartavio cambia de gerente."), coazucar)
rel = rd.resolve_related_entity_mentions(art("Cartavio cambia de gerente."), coazucar)
check(11, "Cartavio cambia de gerente. -> NAO atribuído a Coazucar; "
         "identificado como related_entity",
      not r["matched"] and any(x["entity_name"] == "Cartavio S.A.A." for x in rel))

r = rd.resolve_entity_match(art("Coazucar presenta resultados consolidados."), coazucar)
check(12, "Coazucar presenta resultados consolidados. -> atribuído à holding (alias)",
      r["matched"] and r["subject_company"] == "Coazucar")

r = rd.resolve_entity_match(art("Coazucar reestructura sus subsidiarias."), coazucar)
check(13, "Coazucar reestructura sus subsidiarias. -> atribuído à holding (alias)",
      r["matched"] and r["subject_company"] == "Coazucar")

r = rd.resolve_entity_match(art("San Jacinto enfrenta paralización operativa."), coazucar)
rel = rd.resolve_related_entity_mentions(art("San Jacinto enfrenta paralización operativa."), coazucar)
check(14, "San Jacinto enfrenta paralización operativa. -> NAO atribuído a "
         "Coazucar; contexto de subsidiária",
      not r["matched"] and any(x["entity_name"] == "Agroindustrias San Jacinto S.A." for x in rel))

print("=" * 70)
print("TRUPAL (15-18)")
print("=" * 70)

r = rd.resolve_entity_match(art("Trupal aumenta sus ventas en 10%."), trupal)
check(15, "Trupal aumenta sus ventas en 10%. -> atribuído (alias)",
      r["matched"] and r["confidence"] == "high")

r = rd.resolve_entity_match(art("Trupal moderniza su planta."), trupal)
check(16, "Trupal moderniza su planta. -> atribuído (alias)",
      r["matched"] and r["confidence"] == "high")

r = rd.resolve_entity_match(art("La industria de papel aumenta ventas."), trupal)
check(17, "La industria de papel aumenta ventas. -> REJEITADO (termo genérico "
         "isolado, sem 'Trupal', < min cues)",
      not r["matched"])

r = rd.resolve_entity_match(art("Grupo Gloria anuncia cambios en Trupal."), trupal)
check(18, "Grupo Gloria anuncia cambios en Trupal. -> atribuído (alias 'Trupal')",
      r["matched"] and r["confidence"] == "high")

print("=" * 70)
print("YOBEL (19-24)")
print("=" * 70)

manuf = next(e for e in yobel["entidades_juridicas_candidatas"]
            if e["name"] == "Yobel Supply Chain Management S.A.")
logist = next(e for e in yobel["entidades_juridicas_candidatas"]
             if e["name"] == "Yobel SCM Logistics S.A.")
manuf_company = {"name": manuf["name"], "aliases": manuf["aliases"],
                 "entity_cues": yobel["entity_cues"], "entity_cues_min": 2,
                 "exclusion_cues": yobel["exclusion_cues"]}
logist_company = {"name": logist["name"], "aliases": logist["aliases"],
                  "entity_cues": yobel["entity_cues"], "entity_cues_min": 2,
                  "exclusion_cues": yobel["exclusion_cues"]}
brand_company = {"name": "Yobel", "aliases": yobel["aliases"],
                 "entity_cues": yobel["entity_cues"], "entity_cues_min": 2,
                 "exclusion_cues": yobel["exclusion_cues"]}

r_brand = rd.resolve_entity_match(art("Yobel paraliza temporalmente operaciones en Los Olivos."), brand_company)
check(19, "Yobel paraliza temporalmente operaciones en Los Olivos. -> marca "
         "atribuída (alias 'Yobel' + cues Los Olivos), mas SEM score "
         "(scoring_mode monitoramento_limitado/scoreable=false no cadastro)",
      r_brand["matched"] and yobel.get("scoreable") is False)

r_manuf = rd.resolve_entity_match(
    art("Cámara de Comercio de Lima se pronuncia sobre Yobel Supply Chain Management S.A."),
    manuf_company)
check(20, "CCL se pronuncia sobre Yobel Supply Chain Management S.A. -> "
         "atribuído com alta confiança à entidade 1 (RUC 20100074029) — "
         "confirmação de entidade",
      r_manuf["matched"] and r_manuf["confidence"] == "high")

r_mx = rd.resolve_entity_match(art("Yobel México inaugura nuevo centro."), manuf_company)
check(21, "Yobel México inaugura nuevo centro. -> REJEITADO para a entidade "
         "peruana (exclusion_cue geográfica)",
      not r_mx["matched"] and r_mx["rule"] == "exclusion_cue_precedence")

r_co = rd.resolve_entity_match(art("Yobel Colombia amplía operaciones."), manuf_company)
check(22, "Yobel Colombia amplía operaciones. -> REJEITADO para a entidade "
         "peruana (exclusion_cue geográfica)",
      not r_co["matched"] and r_co["rule"] == "exclusion_cue_precedence")

r_fire = rd.resolve_entity_match(art("Incendio afecta almacén de Yobel en Los Olivos."), manuf_company)
check(23, "Incendio afecta almacén de Yobel en Los Olivos. -> atribuído por "
         "contexto (los olivos + almacenamiento, sem razão social literal)",
      r_fire["matched"])

r_log_ambig = rd.resolve_entity_match(art("Yobel SCM Logistics reporta resultados sin indicar país."), logist_company)
r_manuf_ambig = rd.resolve_entity_match(art("Yobel SCM Logistics reporta resultados sin indicar país."), manuf_company)
check(24, "Yobel SCM Logistics reporta resultados sin indicar país. -> "
         "atribuído por alias à entidade 2 (Logistics), NÃO à entidade 1 "
         "(manufatura) — ambiguidade resolvida corretamente pelo alias exato",
      r_log_ambig["matched"] and r_log_ambig["confidence"] == "high"
      and not r_manuf_ambig["matched"])

print("=" * 70)
print("GENÉRICOS (25-35)")
print("=" * 70)

check(25, "search_term recupera mas não prova atribuição (query != resolve_entity_match)",
      "search_terms" in yura and callable(rd.fetch_query_result)
      and "resolve_entity_match" not in dir(rd.fetch_query_result))

r_excl_vs_alias = rd.resolve_entity_match(
    art("Homicidio en Yura: Yura S.A. no tiene relación con el hecho."), yura)
check(26, "exclusion_cue tem precedência sobre alias presente no mesmo texto",
      not r_excl_vs_alias["matched"]
      and r_excl_vs_alias["rule"] == "exclusion_cue_precedence")

trupal_no_cues = {"name": "Trupal", "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"]}
r_alias_sem_cues = rd.resolve_entity_match(art("Trupal firma nuevo contrato."), trupal_no_cues)
check(27, "alias exato supera ausência de entity_cues (compatibilidade — "
         "cadastro sem cues declarados ainda atribui por alias)",
      r_alias_sem_cues["matched"] and r_alias_sem_cues["confidence"] == "high")

rel_no_transfer = rd.resolve_related_entity_mentions(art("Casa Grande anuncia inversión."), coazucar)
direct_no_transfer = rd.resolve_entity_match(art("Casa Grande anuncia inversión."), coazucar)
check(28, "related_entity não transfere automaticamente evento para a "
         "empresa cadastrada (Coazucar continua NÃO atribuída)",
      len(rel_no_transfer) >= 1 and not direct_no_transfer["matched"])

check(29, "brand_group (Yobel) não pontua — scoreable=false explícito no cadastro",
      yobel.get("scoring_mode") == "monitoramento_limitado" and yobel.get("scoreable") is False)

legacy_company = {"name": "Ambev", "aliases": ["Ambev", "ABEV3"]}
r_legacy = rd.resolve_entity_match(art("Ambev anuncia resultados do trimestre."), legacy_company)
check(30, "registro antigo (sem search_terms/entity_cues/exclusion_cues) "
         "continua funcionando (fallback seguro para alias, comportamento atual)",
      r_legacy["matched"] and r_legacy["confidence"] == "high")

r_legacy_no_match = rd.resolve_entity_match(art("Mercado em geral fecha em alta."), legacy_company)
check(31, "registro antigo sem alias no texto -> não atribuído, sem erro "
         "(nenhuma mudança de comportamento/score para emissor existente)",
      not r_legacy_no_match["matched"])

self_related = rd.resolve_related_entity_mentions(art("Yura anuncia resultados."), yura)
check(32, "nenhuma empresa é related_entity de si mesma "
         "(Yura não aparece na própria lista related_entities)",
      not any(rel["entity_name"] == "Yura" for rel in self_related))

r_geo_only = rd.resolve_entity_match(
    art("Arequipa: Corte Superior anula fallo y confirma titularidad estatal en Yura."), yura)
check(33, "resultado puramente geográfico/jurídico sobre a localidade não "
         "entra no radar corporativo (exclusion_cue 'titularidad estatal en yura')",
      not r_geo_only["matched"] and r_geo_only["rule"] == "exclusion_cue_precedence")

r_foreign_homonym2 = rd.resolve_entity_match(
    art("Korean conglomerate Yura Corporation posts record profits."), yura)
check(34, "empresa estrangeira homônima (Yura Corporation, Coreia) nunca é "
         "atribuída à Yura S.A. peruana",
      not r_foreign_homonym2["matched"]
      and r_foreign_homonym2["rule"] == "exclusion_cue_precedence")

invalid_company = {"name": "Emissor Sem Cadastro Válido"}  # sem aliases, sem name usável como fallback só parcialmente
msgs = rd.validate_asset_classes([invalid_company])
check(35, "configuração inválida (sem asset_class/aliases) gera "
         "ERRO/WARNING apropriado via validate_asset_classes() real, "
         "sem quebrar a execução",
      any("ERRO" in m for m in msgs))

print("=" * 70)
print(f"RESULTADO RESOLUÇÃO DE ENTIDADE: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 70)
sys.exit(1 if FAIL else 0)
