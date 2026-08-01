"""
test_operational_risk_taxonomy.py — 20 testes determinísticos (sem rede) da
nova taxonomia de risco operacional (`incidente_operacional`,
`paralisacao_operacional`, `incidente_operacional_grave`, família
`disrupcao_operacional`) e regulatória (`sancao_regulatoria` separada de
`investigacao_regulatoria`, família `acao_regulatoria`), e da entidade
jurídica confirmada da Yobel (legal_entity/scoreable=true) com a Yobel SCM
Logistics como entidade relacionada.

Usa `cfg = rd.load_config("config_risco.yaml")` — o Yobel real desta branch
(não um fixture inline), já que o objetivo é validar exatamente o cadastro
de produção candidato desta etapa."""
import copy
import json

import risk_dashboard as rd

PASS = FAIL = 0


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  ❌ [{n:02d}] {desc}")


cfg = rd.load_config("config_risco.yaml")


def art(title, summary="", url="https://exemplo-teste.invalid/x", pub_ts=1785400000):
    return {"title": title, "summary": summary, "url": url, "source": "Teste",
            "domain": "exemplo-teste.invalid", "pub_ts": pub_ts,
            "pub_iso": "2026-07-24 12:00", "language": "es"}


def run(title, summary="", url="https://exemplo-teste.invalid/x", pub_ts=1785400000):
    a = art(title, summary, url, pub_ts)
    c = copy.deepcopy(cfg)
    rd.classify_and_attribute(a, c)
    return a


print("=" * 78)
print("TESTES — taxonomia de risco operacional + sanção regulatória + Yobel legal_entity")
print("=" * 78)

# ── 1) Incêndio leve/controlado → incidente_operacional, base 20 ──────────
a1 = run("Yobel: incendio en almacen de la fabrica es controlado por bomberos sin "
        "mayores danos en San Genaro, Los Olivos.")
tax = {e["id"]: e for e in cfg["taxonomy"]}
check(1, "incêndio controlado -> incidente_operacional, base 20",
      (a1.get("events_by_company") or {}).get("Yobel") == ["incidente_operacional"]
      and tax["incidente_operacional"]["score"] == 20)

# ── 2) Incêndio com produção suspensa → paralisacao_operacional, base 40 ──
a2 = run("Yobel Supply Chain Management suspendio sus operaciones tras el incendio "
        "en la planta de Los Olivos.")
check(2, "incêndio com produção suspensa -> paralisacao_operacional, base 40",
      (a2.get("events_by_company") or {}).get("Yobel") == ["paralisacao_operacional"]
      and tax["paralisacao_operacional"]["score"] == 40)

# ── 3) Incêndio grande com explosões → incidente_operacional_grave, base 60 ─
a3 = run("Yobel: incendio de gran magnitud con multiples explosiones destruye "
        "parte de la planta de Los Olivos; evacuacion preventiva.")
check(3, "incêndio grave com explosões -> incidente_operacional_grave, base 60",
      (a3.get("events_by_company") or {}).get("Yobel") == ["incidente_operacional_grave"]
      and tax["incidente_operacional_grave"]["score"] == 60)

# ── 4) Mesma ocorrência em várias FONTES (URLs diferentes) → 1 pontuação ──
c4 = copy.deepcopy(cfg)
a4a = art("Yobel: incendio de gran magnitud con multiples explosiones en su planta "
         "de Los Olivos.", url="https://fontea.invalid/1")
a4b = art("Incendio de grandes proporciones con explosiones sucesivas afecta planta "
         "de Yobel en Los Olivos, confirma la empresa.", url="https://fonteb.invalid/2")
rd.classify_and_attribute(a4a, c4)
rd.classify_and_attribute(a4b, c4)
hist4 = {"articles": {}}
rd.merge_into_history(hist4, [a4a, a4b], keep_days=400)
th4 = rd.calibrate_thresholds(hist4, cfg)
evo4 = rd.build_evolution(hist4, cfg, window_days=30, thresholds=th4)
row4 = next((r for r in evo4 if r["company"] == "Yobel"), None)
check(4, "mesmo fato relatado por 2 fontes diferentes -> 1 única contribuição no breakdown",
      row4 is not None and len(row4["breakdown"]) == 1)

# ── 5) Ocorrência recuperada por várias QUERIES (mesma url) → 1 pontuação ─
c5 = copy.deepcopy(cfg)
a5 = art("Yobel: incendio de gran magnitud con multiples explosiones en su planta "
        "de Los Olivos.", url="https://fontec.invalid/3")
rd.classify_and_attribute(a5, c5)
hist5 = {"articles": {}}
added5a = rd.merge_into_history(hist5, [copy.deepcopy(a5)], keep_days=400)
added5b = rd.merge_into_history(hist5, [copy.deepcopy(a5)], keep_days=400)  # 2ª "query" recupera a mesma url
check(5, "mesma url recuperada por 2 execuções/queries -> só mesclada 1 vez",
      len(added5a) == 1 and len(added5b) == 0)

# ── 6) Incidente grave + paralisação no MESMO artigo → só base 60 ─────────
a6 = run("Yobel: incendio de gran magnitud con multiples explosiones y la empresa "
        "suspendio sus operaciones tras el siniestro en Los Olivos.")
check(6, "grave + paralisação no mesmo artigo -> só incidente_operacional_grave pontua "
        "(paralisação vira metadado secundário, não soma 40+60)",
      (a6.get("events_by_company") or {}).get("Yobel") == ["incidente_operacional_grave"]
      and any(e["id"] == "paralisacao_operacional" and e["primary_event"] == "incidente_operacional_grave"
             for e in (a6.get("secondary_events") or [])))

# ── 7) Incêndio nas proximidades sem atingir a empresa → nenhum evento ────
a7 = run("Incendio forestal cerca de las instalaciones de Yobel en Los Olivos, sin "
        "impacto en la empresa.")
check(7, "incêndio nas proximidades sem atingir a empresa -> nenhum evento",
      not a7.get("events"))

# ── 8) Simulado de incêndio → nenhum evento ────────────────────────────────
a8 = run("Yobel realizo un simulacro de incendio en sus instalaciones de Los Olivos.")
check(8, "simulado/exercício de incêndio -> nenhum evento",
      not a8.get("events"))

# ── 9) Manutenção programada → nenhuma paralisação de risco ───────────────
a9 = run("La planta de Yobel en Los Olivos entra en mantenimiento programado por "
        "dos semanas.")
check(9, "manutenção programada -> nenhum evento de paralisação",
      "paralisacao_operacional" not in [e["id"] for e in (a9.get("events") or [])])

# ── 10) Investigação da Indecopi → investigacao_regulatoria, base 30 ──────
a10 = run("Indecopi abre investigacion regulatoria contra Yobel por presunta "
         "conducta anticompetitiva.")
check(10, "investigação aberta -> investigacao_regulatoria, base 30 (não sancao_regulatoria)",
      "investigacao_regulatoria" in (a10.get("events_by_company") or {}).get("Yobel", [])
      and "sancao_regulatoria" not in (a10.get("events_by_company") or {}).get("Yobel", [])
      and tax["investigacao_regulatoria"]["score"] == 30)

# ── 11) Multa confirmada → sancao_regulatoria, base 45 (família absorve a "investigação") ─
a11 = run("Indecopi impuso una sancion regulatoria confirmada contra Yobel por "
         "conducta anticompetitiva.")
check(11, "multa/sanção confirmada -> sancao_regulatoria, base 45, "
         "'investigação' genérica vira metadado secundário (não soma 30+45)",
      (a11.get("events_by_company") or {}).get("Yobel") == ["sancao_regulatoria"]
      and tax["sancao_regulatoria"]["score"] == 45
      and any(e["id"] == "investigacao_regulatoria" and e["primary_event"] == "sancao_regulatoria"
             for e in (a11.get("secondary_events") or [])))

# ── 12) Multa anulada / empresa absolvida → nenhuma sanção ────────────────
a12 = run("Yobel fue absuelta y el proceso administrativo fue archivado; multa anulada.")
check(12, "empresa absolvida / multa anulada -> nenhuma sanção (negação bloqueia o keyword)",
      "sancao_regulatoria" not in [e["id"] for e in (a12.get("events") or [])])

# ── 13) Evento da entidade relacionada (Yobel SCM Logistics) não transfere score ─
a13 = run("Yobel SCM Logistics S.A. (RUC 20100181534) reporta demora en despachos "
         "por problema tecnico en su sistema.")
check(13, "menção só à Yobel SCM Logistics (RUC relacionado) não atribui nem pontua Yobel",
      "Yobel" not in (a13.get("companies") or [])
      and not (a13.get("events_by_company") or {}).get("Yobel"))
rel13 = next((t.get("related_entities_mentioned") for t in a13.get("entity_resolution_trace", [])
             if t["candidate_company"] == "Yobel"), [])
check(13, "menção à Yobel SCM Logistics registrada como entidade relacionada na telemetria",
      len(rel13) == 1 and rel13[0]["entity_name"] == "Yobel SCM Logistics")

# ── 14) Yobel Supply Chain Management recebe o incêndio como sujeito direto ─
a14 = run("Yobel Supply Chain Management S.A. (RUC 20100074029) sufre incendio de "
         "gran magnitud con multiples explosiones en su fabrica de San Genaro, "
         "Los Olivos.")
check(14, "Yobel Supply Chain Management identificada como sujeito DIRETO do incêndio",
      a14.get("companies") == ["Yobel"]
      and (a14.get("events_by_company") or {}).get("Yobel") == ["incidente_operacional_grave"])

# ── 15) Yobel SCM Logistics não recebe duplicação do MESMO incêndio ───────
# (o mesmo artigo do teste 14 não gera nenhuma linha/score para "Yobel SCM
# Logistics" porque essa entidade não é um emissor cadastrado por si só —
# só existe como relacionada dentro do trace da Yobel.)
check(15, "nenhum emissor separado 'Yobel SCM Logistics' recebe o mesmo incêndio "
         "(não existe como company própria, só como relacionada)",
      "Yobel SCM Logistics" not in (a14.get("companies") or [])
      and all(c not in ("Yobel SCM Logistics",) for c in cfg["watchlist"] if False)
      and not any(c.get("name") == "Yobel SCM Logistics" for c in cfg["watchlist"]))

# ── 16) RUC 20100074029 → atribuição direta (mesmo caso do teste 14) ──────
check(16, "RUC 20100074029 (Yobel Supply Chain Management) -> atribuição direta confirmada",
      a14.get("companies") == ["Yobel"])

# ── 17) RUC 20100181534 → contexto relacionado, salvo impacto direto comprovado ─
check(17, "RUC 20100181534 (Yobel SCM Logistics) sem evidência de impacto direto "
         "-> excluído da Yobel (regra B: não pontua, só relacionada)",
      "Yobel" not in (a13.get("companies") or []))

# ── 18) Idempotência ────────────────────────────────────────────────────
c18 = copy.deepcopy(cfg)
b18a = art("Yobel: incendio de gran magnitud con multiples explosiones en su planta "
          "de Los Olivos.", url="https://idem.invalid/1")
b18b = art("Yobel: incendio de gran magnitud con multiples explosiones en su planta "
          "de Los Olivos.", url="https://idem.invalid/1")
rd.classify_and_attribute(b18a, c18)
rd.classify_and_attribute(b18b, c18)


def _norm(d):
    return json.dumps(d, sort_keys=True, default=str)


check(18, "duas execuções independentes do mesmo artigo produzem JSON idêntico",
      _norm(b18a) == _norm(b18b))

# ── 19) Exclusividade entre containers (novos eventos) ─────────────────────
_all_new = {"1": a1, "2": a2, "3": a3, "6": a6, "10": a10, "11": a11, "14": a14}
_violacoes19 = []
for _label, _a in _all_new.items():
    _ebc = _a.get("events_by_company") or {}
    _ctx = _a.get("context_events_by_company") or {}
    _info = _a.get("informational_events_by_company") or {}
    for _co in set(list(_ebc) + list(_ctx) + list(_info)):
        _ids_score = set(_ebc.get(_co) or [])
        _ids_ctx = {c.get("event_id") for c in (_ctx.get(_co) or [])}
        _ids_info = {c.get("event_id") for c in (_info.get(_co) or [])}
        _inter = ((_ids_score & _ids_ctx) | (_ids_score & _ids_info) | (_ids_ctx & _ids_info))
        if _inter:
            _violacoes19.append((_label, _co, _inter))
check(19, "nenhum evento novo aparece em mais de um container simultaneamente",
      _violacoes19 == [])

# ── 20) Invariância dos emissores antigos (161 emissores reais que não são os ─
# 4 candidatos peruanos) — nenhuma keyword nova pode alterar sua classificação,
# EXCETO se o texto genuinamente contiver as novas keywords (o que deve ser
# listado explicitamente, não escondido). Verificado à parte via script de
# comparação sobre os 585 artigos reais (ver relatório) — aqui só confirmamos
# que a taxonomia em si preserva os ids/pesos ANTIGOS inalterados.
_old_untouched = {
    "investigacao_regulatoria": 30, "rebaixamento_rating": 80,
    "outlook_negativo": None,  # score não fixado neste teste, só existência
}
check(20, "event_id investigacao_regulatoria preserva score-base 30 (não alterado "
         "por esta tarefa, só reorganizado com sancao_regulatoria nova)",
      tax["investigacao_regulatoria"]["score"] == 30
      and tax["investigacao_regulatoria"]["severity"] == "alto"
      and tax["investigacao_regulatoria"]["direction"] == "negativa")

print("=" * 78)
print(f"RESULTADO TAXONOMIA OPERACIONAL: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 78)

import sys
sys.exit(0 if FAIL == 0 else 1)
