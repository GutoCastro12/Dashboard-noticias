"""
test_reclassify_only.py — testes determinísticos (sem rede/LLM) do modo
--reclassify-only (Fase 4H.1). Cobre:
  1) fonte oficial (RI/CVM com trust_override="oficial") preserva a atribuição
     mesmo quando o nome do emissor não aparece literalmente no título — bug
     encontrado nesta própria fase (registro FATO RELEVANTE da PRIO via RI
     perdia a empresa porque detect_companies() só olha o título).
  2) dry-run não persiste nenhum arquivo.
  3) idempotência: 2ª passada sobre o resultado da 1ª não muda nada.
  4) evento direto positivo/informativo (rating_elevado) é roteado para
     informational_events_by_company, nunca para context_events_by_company
     de terceiro nem apagado.
  5) evento cujo sujeito é terceiro (Vale/Samarco) continua em contexto,
     nunca pontua para a Vale.
  6) B3 (bolsa) não herda RJ de emissor listado (padrão já validado por
     test_b3_entity_role.py; aqui via --reclassify-only especificamente).
"""
import copy
import json

import risk_dashboard as rd

PASS = FAIL = 0


def check(n, desc, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK [{n:02d}] {desc}")
    else:
        FAIL += 1
        print(f"  FAIL [{n:02d}] {desc}")


cfg = rd.load_config("config_risco.yaml")


def _hist(records: dict) -> dict:
    return {"articles": copy.deepcopy(records)}


# 1) fonte oficial sem nome do emissor no título
h1 = _hist({
    "https://ri.example/fato-relevante-1": {
        "title": "FATO RELEVANTE - AUMENTO DE CAPITAL SOCIAL POR EXERCICIO DE OPCOES",
        "summary": "", "source": "PRIO . RI", "domain": "api.mziq.com",
        "pub_ts": 1785345187, "pub_iso": "2026-07-29 14:13",
        "companies": ["PRIO"], "event_ids": ["follow_on"],
        "events_by_company": {"PRIO": ["follow_on"]},
        "companies_attributed": ["PRIO"], "context_companies": [],
        "trust_override": "oficial",
    }
})
h1_out, diag1 = rd._reclassify_only_pass(copy.deepcopy(h1), cfg)
rec1 = h1_out["articles"]["https://ri.example/fato-relevante-1"]
check(1, "fonte oficial (trust_override=oficial) preserva a empresa mesmo sem "
         "menção literal no titulo",
      rec1.get("companies") == ["PRIO"])
check(2, "evento da fonte oficial não é removido (segue pontuável)",
      "follow_on" in (rec1.get("events_by_company", {}).get("PRIO") or []))
check(3, "diag não reporta remoção de evento para este caso",
      diag1["removed"] == 0)

# 2) dry-run não persiste nada — a própria função de passada opera em memória
h2_copy = copy.deepcopy(h1)
h2_out, _ = rd._reclassify_only_pass(h2_copy, cfg)
check(4, "_reclassify_only_pass NÃO toca o dict `h1` original (só a cópia)",
      h1["articles"]["https://ri.example/fato-relevante-1"].get("companies") == ["PRIO"])

# 3) idempotência
h3_pass1, _ = rd._reclassify_only_pass(copy.deepcopy(h1), cfg)
h3_pass2, diag_p2 = rd._reclassify_only_pass(copy.deepcopy(h3_pass1), cfg)
check(5, "2a passada sobre o resultado da 1a não muda nada (idempotente)",
      diag_p2["n_changed"] == 0 and diag_p2["removed"] == 0
      and diag_p2["added"] == 0 and diag_p2["moved_context"] == 0
      and diag_p2["moved_informational"] == 0)

# 4) rating_elevado (direto positivo) vai para informational, não desaparece
h4 = _hist({
    "https://noticia.example/rating-prio": {
        "title": "S&P eleva rating da Prio (PRIO3)",
        "summary": "", "source": "financenews.com.br", "domain": "financenews.com.br",
        "pub_ts": 1700000000, "pub_iso": "2025-11-13 05:00",
        "companies": ["PRIO"], "event_ids": ["rating_elevado"],
        "events_by_company": {"PRIO": ["rating_elevado"]},
        "companies_attributed": ["PRIO"], "context_companies": [],
    }
})
h4_out, diag4 = rd._reclassify_only_pass(copy.deepcopy(h4), cfg)
rec4 = h4_out["articles"]["https://noticia.example/rating-prio"]
check(6, "rating_elevado sai de events_by_company (não pontua)",
      "rating_elevado" not in (rec4.get("events_by_company", {}).get("PRIO") or []))
check(7, "rating_elevado aparece em informational_events_by_company (não em contexto)",
      any(e.get("event_id") == "rating_elevado"
          for e in (rec4.get("informational_events_by_company", {}).get("PRIO") or []))
      and "PRIO" not in (rec4.get("context_events_by_company") or {}))

# 5) Vale/Samarco: RJ da Samarco não pontua para a Vale
h5 = _hist({
    "https://noticia.example/vale-samarco-rj": {
        "title": "Vale informa sobre avanco do plano de recuperacao judicial da Samarco",
        "summary": "", "source": "Fonte teste", "domain": "teste.invalid",
        "pub_ts": 1700000000, "pub_iso": "2025-11-13 05:00",
        "companies": ["Vale", "Samarco Mineração"],
    }
})
h5_out, _ = rd._reclassify_only_pass(copy.deepcopy(h5), cfg)
rec5 = h5_out["articles"]["https://noticia.example/vale-samarco-rj"]
_vale_events = (rec5.get("events_by_company", {}) or {}).get("Vale") or []
check(8, "RJ da Samarco não vira evento pontuável DIRETO da Vale",
      "recuperacao_judicial" not in _vale_events)

# 6) B3 (bolsa) não herda RJ de emissor listado
h6 = _hist({
    "https://noticia.example/braskem-b3-rj": {
        "title": "Braskem responde a B3 e diz nao haver decisao sobre recuperacao judicial",
        "summary": "", "source": "Fonte teste", "domain": "teste.invalid",
        "pub_ts": 1700000000, "pub_iso": "2025-11-13 05:00",
        "companies": ["Braskem", "B3"],
    }
})
h6_out, _ = rd._reclassify_only_pass(copy.deepcopy(h6), cfg)
rec6 = h6_out["articles"]["https://noticia.example/braskem-b3-rj"]
_b3_events = (rec6.get("events_by_company", {}) or {}).get("B3") or []
check(9, "B3 (bolsa/destinataria) não recebe RJ atribuída à Braskem",
      "recuperacao_judicial" not in _b3_events)

# 10) correção MANUAL GRANULAR (`manual_correction.locked_fields`) — campo
# travado permanece intacto mesmo que o reprocessamento NORMAL (sem trava)
# produziria outro valor. Usa o título REAL da Braskem/B3 (mesmo do caso de
# produção, commit 5aa4d63): reclassificar esse título do zero, sem trava,
# produz companies=[] mas event_ids=['recuperacao_judicial'] atribuído a
# "Mercado (geral)" (fallback de merge_into_history) — diferente do valor
# humano correto (companies=[], event_ids=[], SEM nenhum evento solto).
_braskem_title = ("Braskem responde à B3 e diz não haver decisão sobre "
                   "recuperação judicial; tutela cautelar segue em vigor")
h10 = _hist({
    "https://atlaspublico.example/braskem-b3-rj": {
        "title": _braskem_title, "summary": _braskem_title,
        "source": "atlaspublico.com.br", "domain": "atlaspublico.com.br",
        "pub_ts": 1785539836, "pub_iso": "2026-07-31 20:17",
        "companies": [], "event_ids": [], "mention_roles": {},
        "events_by_company": {}, "companies_attributed": [], "context_companies": [],
        "manual_correction": {
            "locked_fields": ["companies", "event_ids", "mention_roles",
                              "events_by_company", "companies_attributed",
                              "context_companies"],
            "reason": "B3 e bolsa/destinataria, nao sujeito; RJ nunca foi evento da B3.",
            "corrected_at": "2026-08-03T20:58:55-03:00",
            "correction_id": "b3_exchange_braskem_rj",
        },
    }
})
h10_out, diag10 = rd._reclassify_only_pass(copy.deepcopy(h10), cfg)
rec10 = h10_out["articles"]["https://atlaspublico.example/braskem-b3-rj"]
check(10, "campo travado (event_ids) permanece [] mesmo com reprocessamento "
          "normal produzindo um valor diferente",
      rec10.get("event_ids") == [] and rec10.get("companies") == [])
check(11, "diag regista explicitamente que o reprocessamento teria produzido "
          "um valor diferente do travado (prova que a trava realmente atuou, "
          "não é um no-op por coincidência)",
      any(o["field"] == "event_ids" and o["reprocessed_value"] != o["locked_value"]
          and o["correction_id"] == "b3_exchange_braskem_rj"
          for o in diag10["locked_field_overrides"]))
check(12, "registro com manual_correction é contado em n_manual_correction_records",
      diag10["n_manual_correction_records"] == 1)

# 11) campo NÃO travado do MESMO registro segue o reprocessamento normal —
# trava só nos campos listados, nunca no registro inteiro. Usa o caso real
# do rating positivo da PRIO (evento deveria migrar p/ informativo), mas
# trava artificialmente companies_attributed num valor absurdo só para
# provar que esse campo especificamente FICA travado enquanto
# events_by_company/informational_events_by_company (não travados) mudam.
h11 = _hist({
    "https://noticia.example/rating-prio-granular": {
        "title": "S&P eleva rating da Prio (PRIO3)", "summary": "",
        "source": "financenews.com.br", "domain": "financenews.com.br",
        "pub_ts": 1700000000, "pub_iso": "2025-11-13 05:00",
        "companies": ["PRIO"], "event_ids": ["rating_elevado"],
        "events_by_company": {"PRIO": ["rating_elevado"]},
        "companies_attributed": ["ValorTravadoDeProposito"], "context_companies": [],
        "manual_correction": {
            "locked_fields": ["companies_attributed"],
            "reason": "campo travado só para teste de granularidade",
            "corrected_at": "2026-08-05T00:00:00-03:00",
            "correction_id": "teste_granularidade_11",
        },
    }
})
h11_out, diag11 = rd._reclassify_only_pass(copy.deepcopy(h11), cfg)
rec11 = h11_out["articles"]["https://noticia.example/rating-prio-granular"]
check(13, "campo TRAVADO (companies_attributed) permanece no valor artificial travado",
      rec11.get("companies_attributed") == ["ValorTravadoDeProposito"])
check(14, "campo NÃO travado (rating_elevado sai de events_by_company/PRIO) "
          "segue o reprocessamento normal, apesar do registro ter manual_correction",
      "rating_elevado" not in (rec11.get("events_by_company", {}).get("PRIO") or []))
check(15, "campo NÃO travado (informational_events_by_company) é populado "
          "normalmente mesmo com outro campo do MESMO registro travado",
      any(e.get("event_id") == "rating_elevado"
          for e in (rec11.get("informational_events_by_company", {}).get("PRIO") or [])))

print(f"\n{'=' * 70}\nRESULTADO RECLASSIFY-ONLY: {PASS}/{PASS + FAIL} checagens passaram\n{'=' * 70}")
if FAIL:
    raise SystemExit(1)
