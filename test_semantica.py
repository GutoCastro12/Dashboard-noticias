#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_semantica.py — 20 testes obrigatórios da auditoria semântica (item 20).

Autocontido: usa fixtures_semantica/config_teste.yaml, nunca o config de produção.
"""
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa

BASE = Path(__file__).parent
CFG_PATH = BASE / "fixtures_semantica" / "config_teste.yaml"
PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


CFG = rd.load_config(str(CFG_PATH)) if CFG_PATH.exists() else None
AL = ({c["name"]: (c.get("aliases") or [c["name"]]) for c in CFG["watchlist"]}
      if CFG else {})


def R(titulo, monitorada, eventos, resumo="", ano=2026):
    return sa.resolve_article_semantics(titulo, resumo, monitorada, eventos, AL,
                                        article_year=ano)


def d_de(r, ev):
    return next((d for d in r["decisoes"] if d["event_id"] == ev), {})


# ───────────────────────── 1–11: casos reais ─────────────────────────
def t01_vale_samarco():
    print("\n[1] Vale informa sobre RJ da Samarco")
    r = R("Vale informa sobre Plano de Recuperação Judicial da Samarco", "Vale",
          ["recuperacao_judicial"])
    d = d_de(r, "recuperacao_judicial")
    check(d.get("scoreable") is False, "Vale: RJ não pontua")
    check("Samarco" in str(d.get("subject_company")), f"subject = {d.get('subject_company')}")
    check(d.get("event_scope") == "indireto", "event_scope = indireto")
    check(d.get("relation_type"), f"relation_type preenchido ({d.get('relation_type')})")
    # o lado da Samarco mantém o evento direto
    r2 = R("Vale informa sobre Plano de Recuperação Judicial da Samarco",
           "Samarco Mineração", ["recuperacao_judicial"])
    d2 = d_de(r2, "recuperacao_judicial")
    check(d2.get("scoreable") is True, "Samarco MANTÉM a RJ como evento direto")


def t02_rumo_downgrade_outlook():
    print("\n[2] Rumo: downgrade + outlook na mesma ação")
    r = R("Rumo tem rating rebaixado e perspectiva negativa pela agência", "Rumo",
          ["rebaixamento_rating", "outlook_negativo"])
    check(d_de(r, "rebaixamento_rating").get("scoreable") is True, "downgrade pontua")
    check(d_de(r, "outlook_negativo").get("scoreable") is False, "outlook NÃO pontua")
    check("absorvido" in d_de(r, "outlook_negativo").get("rejection_reason", ""),
          "outlook registrado como absorvido pelo downgrade")
    c = sa.collapse_rating_actions(["rating_elevado", "outlook_positivo"])
    check(c["event_ids"] == ["rating_elevado"], "família positiva também colapsa")


def t03_gerdau_recompra():
    print("\n[3] Gerdau: recompra de ações não é M&A")
    r = R("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
          "Gerdau", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A removido")
    check(d.get("event_id_corrigido") == "recompra_acoes", "reclassificado como recompra_acoes")
    check(d.get("transaction_scope") == "capital_proprio", "transaction_scope = capital_proprio")


def t04_gerdau_transportadoras():
    print("\n[4] Gerdau leva transportadoras à falência")
    r = R("Gerdau descumpre lei federal e leva transportadoras brasileiras à beira da falência",
          "Gerdau", ["falencia"])
    d = d_de(r, "falencia")
    check(d.get("scoreable") is False, "Gerdau não recebe falência")
    check("transportadoras" in str(d.get("subject_company")).lower(),
          f"subject = {d.get('subject_company')}")
    check(d.get("actor_company") == "Gerdau", "actor_company = Gerdau")
    check(d.get("affected_company"), "affected_company preenchido")


def t05_jbs_encerramento():
    print("\n[5] JBS encerra ação sobre fraude")
    r = R("JBS paga R$ 174 milhões e encerra ação sobre fraude fiscal em MT", "JBS",
          ["fraude"])
    d = d_de(r, "fraude")
    check(d.get("scoreable") is False, "fraude crítica removida")
    check(d.get("direction") == "mitigadora", "direction = mitigadora")
    check(d.get("event_phase") in ("encerramento", "acordo", "pagamento"),
          f"fase = {d.get('event_phase')}")


def t06_santander_intragrupo():
    print("\n[6] Santander incorpora subsidiária integral")
    r = R("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
          "Santander Brasil", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A removido")
    check(d.get("transaction_scope") == "intragrupo", "transaction_scope = intragrupo")
    check(d.get("event_id_corrigido") == "reorganizacao_societaria_interna",
          "reclassificado como reorganização interna")


def t07_santander_pos_aquisicao():
    print("\n[7] Santander: resultado após aquisição antiga")
    r = R("Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
          "Santander Brasil", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A removido")
    check(d.get("event_id_corrigido") == "integracao_pos_aquisicao", "vira integração pós-aquisição")
    ok, mot = sa.ma_is_legitimate("O banco não avalia novas grandes aquisições", {})
    check(not ok and "negacao" in mot, "negação explícita bloqueia M&A")


def t08_btg_digimais():
    print("\n[8] BTG é possível comprador de banco em falência")
    r = R("A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo BTG Pactual",
          "BTG Pactual", ["falencia"])
    d = d_de(r, "falencia")
    check(d.get("scoreable") is False, "BTG não recebe falência")
    check("Digimais" in str(d.get("subject_company")), f"subject = {d.get('subject_company')}")
    r2 = R("BTG Pactual mira aquisição do Banco Digimais", "BTG Pactual", ["ma"])
    d2 = d_de(r2, "ma")
    check(d2.get("scoreable") is False, "rumor de M&A não pontua")
    check(d2.get("event_id_corrigido") == "rumor_ma", "reclassificado como rumor_ma")
    check(d2.get("confirmation_level") == "nao_confirmada", "confirmation_level = não confirmada")


def t09_cencosud_stmarche():
    print("\n[9] Cencosud compra empresa em RJ")
    r = R("Cencosud compra St. Marche em meio à recuperação judicial", "Cencosud",
          ["recuperacao_judicial", "ma"])
    drj = d_de(r, "recuperacao_judicial")
    check(drj.get("scoreable") is False, "Cencosud não recebe RJ")
    check("marche" in str(drj.get("subject_company")).lower(), "subject = St. Marche")
    check(drj.get("relation_type") == "alvo_aquisicao", "relation_type = alvo_aquisicao")
    check(d_de(r, "ma").get("scoreable") is True, "M&A da Cencosud é legítimo e pontua")


def t10_latam_aeronaves():
    print("\n[10] LATAM financia compra de aeronaves")
    r = R("LATAM Airlines obtém financiamento de US$ 505 milhões para aquisição de aeronaves",
          "LATAM Airlines", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A removido")
    check(d.get("transaction_object") == "aeronaves", "transaction_object = aeronaves")
    check(d.get("transaction_scope") == "capex", "transaction_scope = capex")
    check(d.get("event_id_corrigido") == "aquisicao_capex", "vira aquisicao_capex")


def t11_gm_historico():
    print("\n[11] GM em matéria histórica")
    r = R("This Day in History: June 1, 2009 - General Motors files for Chapter 11 reorganization",
          "General Motors", ["recuperacao_judicial"])
    d = d_de(r, "recuperacao_judicial")
    check(d.get("scoreable") is False, "GM não recebe RJ atual")
    check(d.get("historical_reference") is True, "historical_reference = true")
    check(d.get("new_occurrence") is False, "new_occurrence = false")
    check(r["historico"]["event_year"] == 2009, f"event_year = {r['historico']['event_year']}")


# ───────────────────── 12–15: positivos que devem PERMANECER ─────────────────────
def t12_ma_externo_verdadeiro():
    print("\n[12] M&A externo verdadeiro permanece")
    for t, e in [("Bunge conclui acordo de aquisição da Viterra", "Bunge"),
                 ("Cade aprova aquisição pela Suzano de 51% de sociedade de tissue", "Suzano"),
                 ("Halliburton Strengthens Business With Summit ESP Acquisition", "Halliburton")]:
        ok, mot = sa.ma_is_legitimate(t, {})
        check(ok, f"M&A legítimo preservado: {t[:52]}")


def t13_rj_direta_verdadeira():
    print("\n[13] RJ direta verdadeira permanece")
    r = R("Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão", "Tok&Stok",
          ["recuperacao_judicial"])
    d = d_de(r, "recuperacao_judicial")
    check(d.get("scoreable") is True, "RJ própria continua pontuando")
    check(d.get("subject_company") == "Tok&Stok", "subject permanece a própria empresa")
    r2 = R("Vazamento sobre calote de R$ 3,6 bi do Banco do Brasil", "Banco do Brasil",
           ["default"])
    check(d_de(r2, "default").get("scoreable") is True,
          "valor monetário não é confundido com entidade")


def t14_fraude_nova_acusacao():
    print("\n[14] Fraude nova formalmente acusada permanece")
    r = R("Justiça condena a empresa por fraude fiscal", "Tok&Stok", ["fraude"])
    d = d_de(r, "fraude")
    check(d.get("event_phase") == "condenacao", f"fase = {d.get('event_phase')}")
    check(d.get("direction") == "negativa", "direction = negativa")
    check(d.get("scoreable") is True, "condenação continua pontuando")


def t15_comunicado_terceiro():
    print("\n[15] Comunicado oficial sobre evento de terceiro")
    r = R("Ambev esclarece sobre 'pedido de falência'", "Ambev", ["falencia"])
    d = d_de(r, "falencia")
    check(d.get("scoreable") is False, "comunicado sobre terceiro não pontua")
    check(d.get("attribution_rule") in ("R_COMUNICADO_SOBRE_TERCEIRO",
                                        "R_POSSESSIVO_MESMA_ORACAO"),
          f"regra aplicada = {d.get('attribution_rule')}")


# ───────────────────── 16–20: emissões e links ─────────────────────
def t16_duas_fontes_mesma_emissao():
    print("\n[16] Duas fontes da mesma emissão = 1 ocorrência")
    import link_debt_audit as lda
    a = lda.debt_occurrence_key("Axia Energia", "Axia Energia capta R$ 1,5 bilhão em debêntures 3ª série",
                                "2026-06-10")
    b = lda.debt_occurrence_key("Axia Energia", "Axia conclui emissão de R$ 1,5 bi em debêntures (3ª série)",
                                "2026-06-12")
    check(a == b, f"mesma ocorrência econômica ({a})")


def t17_duas_emissoes_distintas():
    print("\n[17] Duas emissões distintas não são fundidas")
    import link_debt_audit as lda
    a = lda.debt_occurrence_key("Axia Energia", "Axia capta R$ 1,5 bilhão em debêntures 3ª série", "2026-06-10")
    b = lda.debt_occurrence_key("Axia Energia", "Axia capta R$ 800 milhões em debêntures 4ª série", "2026-06-11")
    check(a != b, "valores e séries diferentes → ocorrências distintas")


def t18_link_valido():
    print("\n[18] Link válido")
    import link_debt_audit as lda
    r = lda.classify_link("https://www.cvm.gov.br/noticias/fato-relevante-123")
    check(r["link_health"] == "estruturalmente_valido", f"{r['link_health']}")
    check(r["resolution_method"] == "direto", "resolução direta")


def t19_redirect_recuperavel():
    print("\n[19] Redirect do Google News recuperável")
    import link_debt_audit as lda
    r = lda.classify_link("https://news.google.com/rss/articles/CBMirgFBVV95cUxQYTdiN2N")
    check(r["link_health"] == "redirecionador_google", f"{r['link_health']}")
    check(r["resolution_method"] == "requer_resolucao", "exige resolução do redirect")
    check(r["fallback_url"] == "", "não usa homepage como fallback silencioso")


def t20_link_invalido():
    print("\n[20] Link estruturalmente inválido / domínio suspeito")
    import link_debt_audit as lda
    r = lda.classify_link("po-news-eg.net/artigo")
    check(r["link_health"] in ("url_malformada", "dominio_suspeito"), f"{r['link_health']}")
    r2 = lda.classify_link("")
    check(r2["link_health"] == "url_ausente", "URL vazia detectada")


def main():
    print("=" * 70)
    print("TESTES — AUDITORIA SEMÂNTICA (20 unitários + 20 de integração)")
    print("=" * 70)
    if CFG is None:
        print(f"{FAIL} fixture ausente: {CFG_PATH}")
        return 1
    for fn in [t01_vale_samarco, t02_rumo_downgrade_outlook, t03_gerdau_recompra,
               t04_gerdau_transportadoras, t05_jbs_encerramento, t06_santander_intragrupo,
               t07_santander_pos_aquisicao, t08_btg_digimais, t09_cencosud_stmarche,
               t10_latam_aeronaves, t11_gm_historico, t12_ma_externo_verdadeiro,
               t13_rj_direta_verdadeira, t14_fraude_nova_acusacao, t15_comunicado_terceiro,
               t16_duas_fontes_mesma_emissao, t17_duas_emissoes_distintas,
               t18_link_valido, t19_redirect_recuperavel, t20_link_invalido] + _INTEGRACAO:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 70)
    print(f"RESULTADO SEMÂNTICA: {ok}/{len(results)} checagens passaram")
    print("=" * 70)
    return 0 if ok == len(results) else 1




# ═══════════════════════════════════════════════════════════════════════
# TESTES DE INTEGRAÇÃO — pipeline real até score e HTML (item 11)
# ═══════════════════════════════════════════════════════════════════════
import json as _json
import tempfile as _tmp

import risk_dashboard as _rd
import semantic_audit as _sem


def _hist(*recs):
    arts = {}
    for i, (titulo, ebc) in enumerate(recs):
        arts[f"https://exemplo.test/{i}"] = {
            "title": titulo, "summary": "", "url": f"https://exemplo.test/{i}",
            "source": "Teste", "domain": "exemplo.test",
            "pub_ts": int(__import__("time").time()) - 86400 * 3,
            "pub_iso": "2026-07-27 10:00", "language": "pt",
            "companies": list(ebc.keys()), "events_by_company": {k: list(v) for k, v in ebc.items()},
            "event_ids": sorted({e for v in ebc.values() for e in v}),
            "mention_roles": {}, "companies_attributed": list(ebc.keys()),
            "context_companies": [], "corroborations": [], "corrob_sources": [],
        }
    return {"articles": arts, "run_count": 1, "resolved_urls": {}, "last_run": {}}


def _pipeline(history):
    """Roda o pipeline REAL: semântica → evolution → feed → changes."""
    for rec in history["articles"].values():
        _sem.apply_semantics_to_record(rec, CFG)
    th = _rd.calibrate_thresholds(history, CFG)
    evo = _rd.build_evolution(history, CFG, window_days=90, thresholds=th)
    feed = _rd.build_feed(history, CFG, window_days=90)
    changes = _rd.build_changes(history, CFG, [], {}, evo)
    return {"evolution": {r["company"]: r for r in evo}, "feed": feed,
            "changes": changes, "history": history}


def _score(res, emp):
    return (res["evolution"].get(emp) or {}).get("total_score", 0)


def _ctx(res, emp):
    return (res["evolution"].get(emp) or {}).get("context_events") or []


def _chips(res, emp):
    r = res["evolution"].get(emp) or {}
    return [c.get("event_id") for c in (r.get("events") or r.get("distinct_events") or [])]


def i01_vale_samarco():
    print("\n[I1] Integração: Vale/Samarco até score e chips")
    res = _pipeline(_hist(("Vale informa sobre Plano de Recuperação Judicial da Samarco",
                           {"Vale": ["recuperacao_judicial"],
                            "Samarco Mineração": ["recuperacao_judicial"]})))
    h = res["history"]["articles"]["https://exemplo.test/0"]
    check(h["events_by_company"]["Vale"] == [], "events_by_company['Vale'] vazio no histórico")
    check("recuperacao_judicial" in h["events_by_company"]["Samarco Mineração"],
          "Samarco mantém RJ")
    check(any(c["event_id"] == "recuperacao_judicial" for c in _ctx(res, "Vale")),
          "Vale tem context_events com a RJ")
    check(all(not c.get("scoreable") for c in _ctx(res, "Vale")), "contexto scoreable=false")
    check("recuperacao_judicial" not in _chips(res, "Vale"), "Vale sem chip de RJ")


def i02_gerdau_transportadoras():
    print("\n[I2] Integração: Gerdau/transportadoras")
    res = _pipeline(_hist(("Gerdau descumpre lei federal e leva transportadoras brasileiras à beira da falência",
                           {"Gerdau": ["falencia"]})))
    check(_score(res, "Gerdau") == 0, f"score real da Gerdau = 0 (obtido {_score(res,'Gerdau')})")
    check("falencia" not in _chips(res, "Gerdau"), "sem chip de falência")
    check(_ctx(res, "Gerdau"), "card preservado com bloco de contexto")


def i03_cencosud():
    print("\n[I3] Integração: Cencosud/St. Marche")
    res = _pipeline(_hist(("Cencosud compra St. Marche em meio à recuperação judicial",
                           {"Cencosud": ["recuperacao_judicial", "ma"]})))
    h = res["history"]["articles"]["https://exemplo.test/0"]
    check("recuperacao_judicial" not in h["events_by_company"]["Cencosud"], "RJ removida")
    check("ma" in h["events_by_company"]["Cencosud"], "M&A legítimo preservado")
    check(any(c["event_id"] == "recuperacao_judicial" for c in _ctx(res, "Cencosud")),
          "RJ vira contexto")


def i04_btg():
    print("\n[I4] Integração: BTG/Digimais")
    res = _pipeline(_hist(("A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo BTG Pactual",
                           {"BTG Pactual": ["falencia"]})))
    check("falencia" not in _chips(res, "BTG Pactual"), "BTG sem chip de falência")
    check(_score(res, "BTG Pactual") == 0, "score real do BTG = 0 neste registro")


def i05_gerdau_recompra():
    print("\n[I5] Integração: recompra da Gerdau")
    res = _pipeline(_hist(("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
                           {"Gerdau": ["ma"]})))
    check(_score(res, "Gerdau") == 0, "recompra não pontua")


def i06_jbs():
    print("\n[I6] Integração: JBS encerramento")
    res = _pipeline(_hist(("JBS paga R$ 174 milhões e encerra ação sobre fraude fiscal em MT",
                           {"JBS": ["fraude"]})))
    check(_score(res, "JBS") == 0, "encerramento não pontua como fraude")


def i07_santander_esfera():
    print("\n[I7] Integração: Santander/Esfera")
    res = _pipeline(_hist(("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
                           {"Santander Brasil": ["ma"]})))
    check(_score(res, "Santander Brasil") == 0, "reorganização intragrupo não pontua")


def i08_santander_tsb():
    print("\n[I8] Integração: Santander/TSB (pós-aquisição)")
    res = _pipeline(_hist(("Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
                           {"Santander Brasil": ["ma"]})))
    check(_score(res, "Santander Brasil") == 0, "contexto pós-aquisição não pontua")


def i09_latam():
    print("\n[I9] Integração: LATAM/aeronaves")
    res = _pipeline(_hist(("LATAM Airlines obtém financiamento de US$ 505 milhões para aquisição de aeronaves",
                           {"LATAM Airlines": ["ma"]})))
    check(_score(res, "LATAM Airlines") == 0, "capex não pontua como M&A")


def i10_gm():
    print("\n[I10] Integração: GM histórico")
    res = _pipeline(_hist(("This Day in History: June 1, 2009 - General Motors files for Chapter 11 reorganization",
                           {"General Motors": ["recuperacao_judicial"]})))
    check(_score(res, "General Motors") == 0, "matéria histórica não pontua em 2026")


def i11_rumo():
    print("\n[I11] Integração: Rumo downgrade+outlook = 1 contribuição")
    res = _pipeline(_hist(("Rumo tem rating rebaixado e perspectiva negativa pela agência",
                           {"Rumo": ["rebaixamento_rating", "outlook_negativo"]})))
    h = res["history"]["articles"]["https://exemplo.test/0"]
    check(h["events_by_company"]["Rumo"] == ["rebaixamento_rating"],
          f"só downgrade permanece ({h['events_by_company']['Rumo']})")
    r = res["evolution"].get("Rumo") or {}
    bd = r.get("breakdown") or []
    check(len([b for b in bd if b.get("event_id") in
               ("rebaixamento_rating", "outlook_negativo")]) <= 1,
          "breakdown com no máximo UMA contribuição da família de rating")


def i12_ma_legitimo():
    print("\n[I12] Integração: M&A externo legítimo pontua")
    res = _pipeline(_hist(("Bunge conclui acordo de aquisição da Viterra", {"Bunge": ["ma"]})))
    h = res["history"]["articles"]["https://exemplo.test/0"]
    check("ma" in h["events_by_company"]["Bunge"], "M&A legítimo preservado no histórico")
    check(_score(res, "Bunge") > 0, f"M&A legítimo pontua (score {_score(res,'Bunge')})")


def i13_rj_legitima():
    print("\n[I13] Integração: RJ direta legítima pontua")
    res = _pipeline(_hist(("Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão",
                           {"Tok&Stok": ["recuperacao_judicial"]})))
    check(_score(res, "Tok&Stok") > 0, f"RJ própria pontua ({_score(res,'Tok&Stok')})")


def i14_fraude_legitima():
    print("\n[I14] Integração: condenação por fraude pontua")
    res = _pipeline(_hist(("Justiça condena a empresa por fraude fiscal", {"JBS": ["fraude"]})))
    check(_score(res, "JBS") > 0, f"condenação pontua ({_score(res,'JBS')})")


def i15_i16_emissoes():
    print("\n[I15-16] Emissões: mesma ocorrência × ocorrências distintas")
    import link_debt_audit as lda
    regs = [{"emissor": "Axia Energia", "titulo": "Axia capta R$ 1,5 bilhão em debêntures 3ª série",
             "url": "u1", "fonte": "CVM"},
            {"emissor": "Axia Energia", "titulo": "Axia conclui emissão de R$ 1,5 bi em debêntures (3ª série)",
             "url": "u2", "fonte": "InfoMoney"},
            {"emissor": "Axia Energia", "titulo": "Axia capta R$ 800 milhões em debêntures 4ª série",
             "url": "u3", "fonte": "Exame"}]
    g = lda.group_debt_occurrences(regs)
    check(len(g) == 2, f"3 notícias → 2 ocorrências econômicas (obtido {len(g)})")
    maior = max(g.values(), key=lambda x: x["qtd_noticias"])
    check(maior["qtd_noticias"] == 2 and maior["qtd_fontes"] == 2,
          "ocorrência com 2 notícias e 2 fontes distintas")
    check(sum(x["qtd_noticias"] for x in g.values()) == 3 and len(g) == 2,
          "3 notícias ≠ 2 ocorrências: contagem de notícias não vira ordinal")


def i17_idempotencia():
    print("\n[I17] Idempotência da reclassificação")
    h1 = _hist(("Cencosud compra St. Marche em meio à recuperação judicial",
                {"Cencosud": ["recuperacao_judicial", "ma"]}))
    for rec in h1["articles"].values():
        _sem.apply_semantics_to_record(rec, CFG)
    snap = _json.dumps(h1, sort_keys=True, ensure_ascii=False)
    for rec in h1["articles"].values():
        _sem.apply_semantics_to_record(rec, CFG)
    check(_json.dumps(h1, sort_keys=True, ensure_ascii=False) == snap,
          "segunda aplicação não altera o histórico")


def i18_sem_fetch():
    print("\n[I18] Reclassificação sem fetch")
    import inspect
    src = inspect.getsource(_rd.run_semantic_reclassification)
    for proibido in ("fetch_all(", "fetch_cvm_fatos(", "fetch_edgar_filings(",
                     "fetch_ri_news_pages(", "fetch_custom_feeds("):
        check(proibido not in src, f"não chama {proibido[:-1]}")
    check("args.backfill" not in src and "--backfill" not in src,
          "não aciona backfill")


def i19_original_preservado():
    print("\n[I19] Original preservado até o aceite")
    import inspect
    src = inspect.getsource(_rd.run_semantic_reclassification)
    check("_copy.deepcopy(history)" in src, "backup interno antes de alterar")
    check("args.output_history or args.history" in src,
          "grava em --output-history; só sobrescreve se o usuário pedir")


def i20_html_sem_chips_indevidos():
    print("\n[I20] HTML final sem chips indevidos")
    hist = _hist(("Gerdau descumpre lei federal e leva transportadoras brasileiras à beira da falência",
                  {"Gerdau": ["falencia"]}))
    res = _pipeline(hist)
    th = _rd.calibrate_thresholds(hist, CFG)
    dbw = {"90": {"evolution": [v for v in res["evolution"].values()],
                  "feed": res["feed"]}}
    try:
        html = _rd.render_html(dbw, CFG, demo=False, changes=res["changes"],
                               payload_thresholds=th)
        check(len(html) > 1000, "HTML renderizado")
        check("Contexto relacionado" in html, "template contém o bloco de contexto")
        import re as _re
        bloco = _re.search(r'"company":\s*"Gerdau".{0,4000}', html, _re.S)
        check(bloco is None or '"event_id": "falencia"' not in
              bloco.group(0).split('"context_events"')[0],
              "Gerdau sem chip de falência fora do contexto")
    except Exception as exc:
        check(False, f"render_html falhou: {type(exc).__name__}: {str(exc)[:80]}")


_INTEGRACAO = [i01_vale_samarco, i02_gerdau_transportadoras, i03_cencosud, i04_btg,
               i05_gerdau_recompra, i06_jbs, i07_santander_esfera, i08_santander_tsb,
               i09_latam, i10_gm, i11_rumo, i12_ma_legitimo, i13_rj_legitima,
               i14_fraude_legitima, i15_i16_emissoes, i17_idempotencia,
               i18_sem_fetch, i19_original_preservado, i20_html_sem_chips_indevidos]


if __name__ == "__main__":
    raise SystemExit(main())
