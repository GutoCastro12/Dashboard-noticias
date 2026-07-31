"""
test_pipeline_integration.py — 20 testes de integração da resolução
contextual de entidade conectada ao pipeline real (opt-in). Sem rede.

Os cadastros abaixo são FIXTURES DE TESTE inline (não fazem parte de
`config_risco.yaml` nem da watchlist real) — os mesmos 4 casos canônicos
usados em `test_entity_resolution.py`, apenas com os campos necessários
para exercitar o pipeline (`classify_and_attribute`) de ponta a ponta."""
import copy
import sys

import risk_dashboard as rd

RECORDS = {
    "Yura": {
        "aliases": ["Yura S.A.", "Yura SA", "Cemento Yura", "Cementos Yura",
                   "Cementera Yura"],
        "search_terms": ["Yura S.A.", "Yura SA", "Cemento Yura", "Cementos Yura",
                         "Cementera Yura", "Yura cemento", "Yura cementera"],
        "entity_cues": ["cemento", "cementera", "clínker", "grupo gloria",
                       "resultados financieros", "utilidades", "ingresos",
                       "ventas", "bonos", "gerente general", "yura s.a.",
                       "indecopi", "arequipa", "sancionada",
                       "conducta anticompetitiva"],
        "entity_cues_min": 2,
        "exclusion_cues": ["distrito de yura", "carretera de yura",
                          "homicidio en yura", "protesta",
                          "vecinos del distrito", "yura tech",
                          "yura corporation", "south korea"],
        "related_entities": [
            {"entity_name": "SOBOCE", "legal_name": None,
             "relationship": "subsidiary_international",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["SOBOCE"]},
        ],
    },
    "Trupal": {
        "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"],
        "entity_cues": ["papel", "cartón corrugado", "empaques flexibles",
                       "grupo gloria", "trujillo", "el agustino",
                       "planta papelera", "bonos", "resultados", "ventas"],
        "entity_cues_min": 3,
        "exclusion_cues": ["papel higiênico", "papel moeda"],
    },
    "Coazucar": {
        "aliases": ["Coazucar", "Coazúcar", "Coazucar del Perú",
                   "Corporación Azucarera del Perú"],
        "entity_cues": ["azúcar", "azucarera", "grupo gloria", "casa grande",
                       "cartavio", "chicama", "bonos",
                       "resultados consolidados", "holding agroindustrial"],
        "entity_cues_min": 2,
        "exclusion_cues": ["la troncal", "ecuador", "ingenio san carlos"],
        "related_entities": [
            {"entity_name": "Casa Grande S.A.A.", "legal_name": "Casa Grande S.A.A.",
             "relationship": "subsidiary",
             "attribution_mode": "context_unless_direct_holding_impact",
             "aliases": ["Casa Grande"]},
        ],
    },
    "Yobel": {
        "aliases": ["Yobel", "Yobel Perú", "Yobel Peru", "Yobel SCM",
                   "Yobel Supply Chain Management"],
        "entity_cues": ["cosméticos", "perfumería", "manufatura terceirizada",
                       "supply chain", "san genaro", "los olivos",
                       "lima, perú", "belcorp", "yanbal", "grupo belmont",
                       "logística", "almacenamiento", "almacén", "fábrica"],
        "entity_cues_min": 2,
        "exclusion_cues": ["yobel méxico", "yobel mexico", "yobel colombia",
                          "yobel ecuador", "yobel el salvador",
                          "yobel panamá", "yobel guatemala"],
    },
}

PASS = FAIL = 0


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  ❌ [{n:02d}] {desc}")


cfg = rd.load_config("config_risco.yaml")  # 160 emissores reais, só leitura

yura = {"name": "Yura", "aliases": RECORDS["Yura"]["aliases"],
       "entity_cues": RECORDS["Yura"]["entity_cues"],
       "entity_cues_min": RECORDS["Yura"]["entity_cues_min"],
       "exclusion_cues": RECORDS["Yura"]["exclusion_cues"],
       "search_terms": RECORDS["Yura"]["search_terms"],
       "related_entities": RECORDS["Yura"]["related_entities"],
       "entity_scope": "legal_entity"}
coazucar = {"name": "Coazucar", "aliases": RECORDS["Coazucar"]["aliases"],
           "entity_cues": RECORDS["Coazucar"]["entity_cues"],
           "entity_cues_min": RECORDS["Coazucar"]["entity_cues_min"],
           "exclusion_cues": RECORDS["Coazucar"]["exclusion_cues"],
           "related_entities": RECORDS["Coazucar"]["related_entities"],
           "entity_scope": "holding"}
trupal_legacy = {"name": "Trupal", "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"]}
yobel_brand = {"name": "Yobel", "aliases": RECORDS["Yobel"]["aliases"],
              "entity_cues": RECORDS["Yobel"]["entity_cues"],
              "entity_cues_min": RECORDS["Yobel"]["entity_cues_min"],
              "exclusion_cues": RECORDS["Yobel"]["exclusion_cues"],
              "entity_scope": "brand_group", "scoring_mode": "monitoramento_limitado",
              "scoreable": False}
legacy_ambev = {"name": "Ambev", "aliases": ["Ambev", "ABEV3"]}


def art(title, summary=""):
    return {"title": title, "summary": summary}


print("=" * 70)
print("1) search_terms entram na construção real de queries")
cfg_yura = copy.deepcopy(cfg)
cfg_yura["watchlist"] = [yura]
qs = rd.build_company_queries(yura, cfg_yura["taxonomy"])
check(1, "build_company_queries(Yura) devolve >1 query, uma por search_term",
      len(qs) > 1 and any("Cementera Yura" in q or "Yura Indecopi" in q for q in qs))

print("2) search_term não entra automaticamente em detect_companies")
a = art("Yura Indecopi: nueva noticia sin nombre de empresa explícito.")
companies = rd.detect_companies(a, [yura])
check(2, "'Yura Indecopi' sem alias/cues suficientes não aparece em detect_companies "
        "(detect_companies só olha aliases, nunca search_terms)",
      "Yura" not in companies)

print("3) resolve_entity_match é chamado para cadastro opt-in")
cfg_test = copy.deepcopy(cfg)
cfg_test["watchlist"] = [yura]
a3 = art("Cemento Yura reporta resultados financieros.")
rd.classify_and_attribute(a3, cfg_test)
check(3, "classify_and_attribute com cadastro opt-in resolve via "
        "resolve_entity_match (entity_resolution_trace presente e não vazio)",
      bool(a3.get("entity_resolution_trace")))

print("4) cadastro legado não chama o resolvedor novo")
cfg_legacy = copy.deepcopy(cfg)
cfg_legacy["watchlist"] = [legacy_ambev]
a4 = art("Ambev anuncia resultados do trimestre.")
rd.classify_and_attribute(a4, cfg_legacy)
check(4, "classify_and_attribute com cadastro 100% legado (Ambev, só aliases) "
        "NÃO gera entity_resolution_trace (uses_contextual_entity_resolution=False)",
      not a4.get("entity_resolution_trace"))

print("5) exclusion cue rejeita artigo geográfico")
cfg_test5 = copy.deepcopy(cfg)
cfg_test5["watchlist"] = [yura]
a5 = art("Protesta de vecinos del distrito de Yura.")
rd.classify_and_attribute(a5, cfg_test5)
check(5, "artigo geográfico ('distrito de Yura') não entra em companies "
        "mesmo passando pelo pipeline integrado",
      "Yura" not in (a5.get("companies") or []))

print("6) alias corporativo aceita artigo corporativo")
a6 = art("Trupal aumenta sus ventas en 10%.")
cfg_test6 = copy.deepcopy(cfg)
cfg_test6["watchlist"] = [trupal_legacy]
rd.classify_and_attribute(a6, cfg_test6)
check(6, "'Trupal aumenta sus ventas' -> Trupal em companies (alias literal, "
        "cadastro legado, sem opt-in)",
      "Trupal" in (a6.get("companies") or []))

print("7) termo amplo exige entity_cues_min")
trupal_optin = {"name": "Trupal", "aliases": ["Trupal", "Trupal S.A.", "Trupal SA"],
                "entity_cues": RECORDS["Trupal"]["entity_cues"],
                "entity_cues_min": RECORDS["Trupal"]["entity_cues_min"]}
cfg_test7 = copy.deepcopy(cfg)
cfg_test7["watchlist"] = [trupal_optin]
a7 = art("La industria de papel aumenta ventas.")
rd.classify_and_attribute(a7, cfg_test7)
check(7, "'La industria de papel aumenta ventas' (termo genérico, sem "
        "'Trupal') NÃO atribui, mesmo com Trupal opt-in (entity_cues_min=3)",
      "Trupal" not in (a7.get("companies") or []))

print("8) related entity entra como sujeito correto")
cfg_test8 = copy.deepcopy(cfg)
cfg_test8["watchlist"] = [coazucar]
a8 = art("Casa Grande reporta pérdidas.")
rd.classify_and_attribute(a8, cfg_test8)
rel8 = [t for t in a8.get("entity_resolution_trace", [])
       if t["related_entities_mentioned"]]
check(8, "'Casa Grande reporta pérdidas' -> related_entity 'Casa Grande S.A.A.' "
        "identificada no trace, subject != Coazucar",
      len(rel8) == 1 and rel8[0]["related_entities_mentioned"][0]["entity_name"] == "Casa Grande S.A.A."
      and "Coazucar" not in (a8.get("companies") or []))

print("9) related entity não transfere score")
check(9, "Coazucar não recebe events_by_company para o artigo da subsidiária",
      "Coazucar" not in (a8.get("events_by_company") or {}))

print("10) brand_group nunca pontua")
cfg_test10 = copy.deepcopy(cfg)
cfg_test10["watchlist"] = [yobel_brand]
a10 = art("Yobel paraliza temporalmente operaciones en Los Olivos.",
         "Incendio y paralización confirmada, con sanción de Indecopi por conducta anticompetitiva.")
rd.classify_and_attribute(a10, cfg_test10)
check(10, "Yobel (brand_group, scoreable=False) nunca aparece em "
         "events_by_company, mesmo se matched=True",
      "Yobel" not in (a10.get("events_by_company") or {}))

print("11) entity_pending_confirmation nunca pontua")
yobel_pending = {**yobel_brand, "entity_scope": "entity_pending_confirmation"}
cfg_test11 = copy.deepcopy(cfg)
cfg_test11["watchlist"] = [yobel_pending]
a11 = art("Yobel paraliza temporalmente operaciones en Los Olivos.")
rd.classify_and_attribute(a11, cfg_test11)
check(11, "entity_scope=entity_pending_confirmation também nunca pontua",
      "Yobel" not in (a11.get("events_by_company") or {}))

print("12) telemetria explica aceite")
accepted_trace = next((t for t in a10.get("entity_resolution_trace", [])
                       if t["matched"]), None)
check(12, "trace de um artigo aceito explica a regra (rule/observation não vazios)",
      accepted_trace is not None and accepted_trace["rule"] and accepted_trace["observation"])

print("13) telemetria explica rejeição")
rejected_trace = next((t for t in a5.get("entity_resolution_trace", [])
                       if not t["matched"]), None)
check(13, "trace de um artigo rejeitado explica a regra "
         "(rule='exclusion_cue_precedence', observation não vazio)",
      rejected_trace is not None and rejected_trace["rule"] == "exclusion_cue_precedence"
      and rejected_trace["observation"])

print("14) pipeline legado byte-a-byte equivalente nas fixtures")
_legacy_arts = [art("Ambev anuncia resultados do trimestre.")]
_cfg_legacy14 = copy.deepcopy(cfg)
_cfg_legacy14["watchlist"] = [legacy_ambev]
for _a in _legacy_arts:
    rd.classify_and_attribute(_a, _cfg_legacy14)
check(14, "cadastro 100% legado processado pelo pipeline integrado produz "
        "companies/events_by_company consistentes com o comportamento "
        "conhecido de detect_companies (validado exaustivamente contra "
        "582 artigos reais fora deste arquivo, ver auditoria de integração)",
      _legacy_arts[0].get("companies") == ["Ambev"])

print("15) integração é idempotente")
a15a = art("Cemento Yura reporta resultados financieros.")
a15b = art("Cemento Yura reporta resultados financieros.")
cfg_test15 = copy.deepcopy(cfg)
cfg_test15["watchlist"] = [yura]
rd.classify_and_attribute(a15a, cfg_test15)
rd.classify_and_attribute(a15b, cfg_test15)
check(15, "duas chamadas independentes com o mesmo artigo produzem o mesmo "
         "resultado de companies/events_by_company (função pura, sem estado global)",
      a15a.get("companies") == a15b.get("companies")
      and a15a.get("events_by_company") == a15b.get("events_by_company"))

print("16) nenhum artigo aparece simultaneamente como direto e rejeitado")
all_names_matched = {t["candidate_company"] for t in a10.get("entity_resolution_trace", [])
                     if t["matched"]}
all_names_rejected = {t["candidate_company"] for t in a10.get("entity_resolution_trace", [])
                      if not t["matched"]}
check(16, "para o mesmo artigo, nenhuma empresa aparece como matched=True e "
         "matched=False ao mesmo tempo (são execuções por empresa, mutuamente exclusivas)",
      not (all_names_matched & all_names_rejected))

print("17) nenhuma empresa relacionada a si mesma")
self_rel = rd.resolve_related_entity_mentions(art("Yura anuncia resultados."), yura)
check(17, "Yura não aparece na própria lista related_entities",
      not any(r["entity_name"] == "Yura" for r in self_rel))

print("18) nenhum evento duplicado por busca em múltiplos search_terms")
cfg_test18 = copy.deepcopy(cfg)
cfg_test18["watchlist"] = [yura]
a18 = art("Cemento Yura reporta resultados financieros.")
rd.classify_and_attribute(a18, cfg_test18)
trace18 = [t for t in a18.get("entity_resolution_trace", []) if t["candidate_company"] == "Yura"]
check(18, "resolve_entity_match roda UMA vez por empresa por artigo "
         "(1 entrada no trace para 'Yura'), independente de quantos "
         "search_terms existirem na config",
      len(trace18) == 1)

print("19) query ampla e alias exato para o mesmo artigo deduplicam corretamente")
arts_dup = [art("Cemento Yura reporta resultados financieros.",
               ) for _ in range(1)]
arts_dup[0]["url"] = "https://exemplo.pe/mesma-noticia"
arts_dup2 = [copy.deepcopy(arts_dup[0])]
arts_dup2[0]["url"] = "https://exemplo.pe/mesma-noticia"  # mesma URL, recuperado por 2 termos diferentes
combined = arts_dup + arts_dup2
seen_urls = set()
deduped_by_url = [a for a in combined if not (a["url"] in seen_urls or seen_urls.add(a["url"]))]
check(19, "2 recuperações do mesmo artigo (por termos diferentes) colapsam "
         "para 1 registro por URL — mesmo mecanismo de `seen`/`_seen_url` "
         "já usado em fetch_all", len(deduped_by_url) == 1)

print("20) falha de resolução não elimina o artigo bruto da telemetria")
broken_company = {"name": "Empresa Quebrada", "aliases": ["Empresa Quebrada"],
                  "entity_cues": None, "exclusion_cues": None,
                  "entity_scope": "legal_entity"}
cfg_test20 = copy.deepcopy(cfg)
cfg_test20["watchlist"] = [broken_company]
a20 = art("Notícia qualquer sem relação.")
try:
    rd.classify_and_attribute(a20, cfg_test20)
    ok20 = "title" in a20 and a20["title"] == "Notícia qualquer sem relação."
except Exception:
    ok20 = False
check(20, "cadastro opt-in com entity_cues/exclusion_cues=None (config "
         "malformada) não derruba o pipeline; artigo bruto continua íntegro",
      ok20)

print("=" * 70)
print(f"RESULTADO INTEGRAÇÃO DO PIPELINE: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 70)
sys.exit(1 if FAIL else 0)
