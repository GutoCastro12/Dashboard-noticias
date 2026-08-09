#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_semantica.py — 20 testes obrigatórios da auditoria semântica (item 20).

Autocontido: usa fixtures_semantica/config_teste.yaml, nunca o config de produção.
"""
from pathlib import Path

import re
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
    # conteúdo principal é o RESULTADO (lucro acima do esperado), não a
    # integração — a aquisição vira só `secondary_context_id` histórico
    check(d.get("event_id_corrigido") == "resultado_acima_expectativas",
          f"vira resultado_acima_expectativas (obtido {d.get('event_id_corrigido')})")
    check(d.get("secondary_context_id") == "integracao_pos_aquisicao",
          "integração pós-aquisição fica como contexto secundário, não evento principal")
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
    # ── FIXTURE ADJUDICADA (4I.2 Wave B3) ──────────────────────────────────
    # A versão anterior usava "Vazamento sobre calote de R$ 3,6 bi do Banco do
    # Brasil" como VEÍCULO para testar que valor monetário não vira entidade,
    # e exigia `scoreable is True` para o BB. A auditoria 4I demonstrou depois
    # que essa segunda premissa é economicamente incorreta: naquele título o
    # BB é o CREDOR lesado (a quantia é devida A ele), não o devedor em
    # default — veredito WRONG_RELATION, confirmado por adjudicação explícita.
    # A invariante monetária, que é legítima, foi preservada e agora é testada
    # DIRETAMENTE na função responsável, sem carregar junto uma relação
    # credor/devedor já refutada. O caso BB virou regressão canônica em
    # test_wave_b3_credor_devedor.py.
    import semantic_audit as _sa
    for _txt in ("Empresa X pede recuperação judicial de R$ 1,1 bilhão",
                 "Companhia Y assume dívida de R$ 500 milhões",
                 "Emissor Z declara default de R$ 3,6 bilhões"):
        _ent = _sa.detect_debtor_subject(_txt, "Tok&Stok", ["Tok&Stok"])
        check(not re.search(r"r\$|\d|bilh|milh|^bi$", _ent or "", re.I),
              f"valor monetário não é confundido com entidade ({_ent or 'vazio'!r})")


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
               t18_link_valido, t19_redirect_recuperavel, t20_link_invalido] \
              + _INTEGRACAO + _ROTEAMENTO + _FAMILIA_SECUNDARIA:
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


# ═══════════════════════════════════════════════════════════════════════
# ROTEAMENTO DE EVENTOS DIRETOS NÃO PONTUÁVEIS DO PRÓPRIO EMISSOR (r01-r20)
#
# fix/direct-informational-events — separa, dentro do que já é "não
# pontuável", o evento cujo SUJEITO é a própria empresa monitorada (positivo/
# neutro/informativo direto → informational_events_by_company) do evento cujo
# sujeito é um TERCEIRO real (contexto → context_events_by_company).
# Nenhuma empresa pode ser tratada como "entidade relacionada" a si mesma.
# ═══════════════════════════════════════════════════════════════════════

def _rec_com_evento(titulo, empresa, event_ids, url="https://exemplo.test/r", resumo=""):
    rec = {"title": titulo, "summary": resumo, "url": url, "source": "Teste",
           "domain": "exemplo.test", "pub_ts": int(__import__("time").time()) - 86400,
           "pub_iso": "2026-07-30 10:00", "language": "pt",
           "companies": [empresa], "events_by_company": {empresa: list(event_ids)},
           "event_ids": list(event_ids), "mention_roles": {},
           "companies_attributed": [empresa], "context_companies": []}
    _sem.apply_semantics_to_record(rec, CFG)
    return rec


def r01_santander_tsb_informational():
    print("\n[R1] Santander supera expectativas após aquisição antiga → informativo, não contexto")
    rec = _rec_com_evento(
        "Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
        "Santander Brasil", ["ma"], url="https://exemplo.test/r01")
    info = (rec.get("informational_events_by_company") or {}).get("Santander Brasil") or []
    ctx = (rec.get("context_events_by_company") or {}).get("Santander Brasil") or []
    check(len(info) == 1, f"1 evento informativo registrado (obtido {len(info)})")
    e = info[0] if info else {}
    check(e.get("subject_company") == "Santander Brasil", "subject_company = Santander Brasil")
    check(e.get("monitored_company") == "Santander Brasil", "monitored_company = Santander Brasil")
    check(e.get("relation_type") == "direto", "relation_type = direto")
    check(e.get("event_scope") == "direto", "event_scope = direto")
    check(e.get("scoreable") is False, "scoreable = false")
    check(e.get("direction") == "positiva", "direction = positiva")
    check(e.get("new_ma_occurrence") is False, "new_ma_occurrence = false")
    check(e.get("historical_transaction_reference") is True, "historical_transaction_reference = true")
    check(e.get("display_category") == "positivo", "display_category = positivo")
    check(e.get("event_id") == "resultado_acima_expectativas",
          f"evento principal = resultado_acima_expectativas (obtido {e.get('event_id')})")
    check(e.get("secondary_context") == "integracao_pos_aquisicao",
          "integração pós-aquisição fica como contexto secundário do resultado")
    check("Santander Brasil" not in {c for c, v in (rec.get("context_events_by_company") or {}).items() if v},
          "Santander Brasil não aparece em context_events_by_company")
    check(ctx == [], "context_events_by_company['Santander Brasil'] vazio")
    check("ma" not in rec["events_by_company"].get("Santander Brasil", []),
          "não gera evento de M&A em events_by_company")


def r02_santander_esfera_informational():
    print("\n[R2] Santander incorpora Esfera (subsidiária integral) → informativo, não contexto")
    rec = _rec_com_evento(
        "Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
        "Santander Brasil", ["ma"], url="https://exemplo.test/r02")
    info = (rec.get("informational_events_by_company") or {}).get("Santander Brasil") or []
    check(len(info) == 1, f"1 evento informativo registrado (obtido {len(info)})")
    e = info[0] if info else {}
    check(e.get("subject_company") == "Santander Brasil", "subject_company = Santander Brasil")
    check(e.get("event_id") == "reorganizacao_societaria_interna",
          f"event_id = reorganizacao_societaria_interna (obtido {e.get('event_id')})")
    check(e.get("transaction_scope") == "intragrupo", "transaction_scope = intragrupo")
    check(e.get("scoreable") is False, "scoreable = false")
    check(e.get("change_of_control") is False, "change_of_control = false")
    check(e.get("external_ma") is False, "external_ma = false")
    ctx = (rec.get("context_events_by_company") or {}).get("Santander Brasil") or []
    check(ctx == [], "context_events_by_company['Santander Brasil'] vazio")
    check("ma" not in rec["events_by_company"].get("Santander Brasil", []),
          "não gera M&A externo")


def r03_vale_samarco_contexto_preservado():
    print("\n[R3] Vale/Samarco continua sendo contexto de TERCEIRO")
    rec = _rec_com_evento("Vale informa sobre Plano de Recuperação Judicial da Samarco",
                          "Vale", ["recuperacao_judicial"], url="https://exemplo.test/r03")
    ctx = (rec.get("context_events_by_company") or {}).get("Vale") or []
    info = (rec.get("informational_events_by_company") or {}).get("Vale") or []
    check(len(ctx) == 1, "Vale recebe 1 evento de contexto")
    check(ctx and "Samarco" in str(ctx[0].get("subject_company")), "sujeito = Samarco (terceiro)")
    check(info == [], "Vale não recebe evento informativo próprio (é caso de terceiro)")


def r04_cencosud_stmarche_contexto_preservado():
    print("\n[R4] Cencosud/St. Marche: contexto de terceiro + M&A legítimo preservados")
    rec = _rec_com_evento("Cencosud compra St. Marche em meio à recuperação judicial",
                          "Cencosud", ["recuperacao_judicial", "ma"],
                          url="https://exemplo.test/r04")
    ctx = (rec.get("context_events_by_company") or {}).get("Cencosud") or []
    check(any("marche" in str(c.get("subject_company", "")).lower() for c in ctx),
          "RJ da St. Marche vira contexto de terceiro para a Cencosud")
    check("ma" in rec["events_by_company"].get("Cencosud", []),
          "M&A legítimo da Cencosud continua pontuável (events_by_company)")


def r05_btg_digimais_contexto_e_rumor_informativo():
    print("\n[R5] BTG: falência do Digimais = contexto; rumor de M&A do próprio BTG = informativo")
    rec1 = _rec_com_evento(
        "A falência fraudulenta do banco Digimais e a suspeita oferta de compra pelo BTG Pactual",
        "BTG Pactual", ["falencia"], url="https://exemplo.test/r05a")
    ctx = (rec1.get("context_events_by_company") or {}).get("BTG Pactual") or []
    check(any("digimais" in str(c.get("subject_company", "")).lower() for c in ctx),
          "falência do Digimais = contexto de terceiro para o BTG")
    rec2 = _rec_com_evento("BTG Pactual mira aquisição do Banco Digimais", "BTG Pactual", ["ma"],
                          url="https://exemplo.test/r05b")
    info2 = (rec2.get("informational_events_by_company") or {}).get("BTG Pactual") or []
    check(len(info2) == 1, "rumor de M&A do próprio BTG vira evento informativo (não contexto)")
    check(info2 and info2[0].get("event_id") == "rumor_ma", "event_id = rumor_ma")
    check(info2 and info2[0].get("subject_company") == "BTG Pactual",
          "sujeito do rumor é o próprio BTG — não é entidade relacionada a si mesma")


def r06_evento_direto_positivo_generico():
    print("\n[R6] Evento direto positivo genérico de empresa monitorada")
    rec = _rec_com_evento("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
                          "Gerdau", ["ma"], url="https://exemplo.test/r06")
    info = (rec.get("informational_events_by_company") or {}).get("Gerdau") or []
    check(len(info) == 1, "1 evento informativo (recompra)")
    check(info and info[0].get("event_id") == "recompra_acoes", "event_id = recompra_acoes")
    check(info and info[0].get("direction") == "positiva", "direction = positiva")
    check(info and info[0].get("display_category") == "positivo", "display_category = positivo")
    check(info and info[0].get("subject_company") == "Gerdau", "subject_company = Gerdau (direto)")


def r07_evento_direto_neutro_generico():
    print("\n[R7] Evento direto neutro/informativo genérico de empresa monitorada")
    rec = _rec_com_evento("LATAM Airlines obtém financiamento de US$ 505 milhões para aquisição de aeronaves",
                          "LATAM Airlines", ["ma"], url="https://exemplo.test/r07")
    info = (rec.get("informational_events_by_company") or {}).get("LATAM Airlines") or []
    check(len(info) == 1, "1 evento informativo (aquisição de ativo, não empresa)")
    check(info and info[0].get("event_id") == "aquisicao_capex", "event_id = aquisicao_capex")
    check(info and info[0].get("display_category") == "informativo", "display_category = informativo")


def r08_evento_direto_negativo_pontuavel_preservado():
    print("\n[R8] Evento direto negativo pontuável continua pontuando (regressão)")
    rec = _rec_com_evento("Justiça condena a empresa por fraude fiscal", "Tok&Stok", ["fraude"],
                          url="https://exemplo.test/r08")
    check("fraude" in rec["events_by_company"].get("Tok&Stok", []),
          "fraude continua em events_by_company (pontuável)")
    info = (rec.get("informational_events_by_company") or {}).get("Tok&Stok") or []
    ctx = (rec.get("context_events_by_company") or {}).get("Tok&Stok") or []
    check(info == [] and ctx == [], "evento pontuável não vaza para nenhum bucket não pontuável")


def r09_nenhuma_empresa_e_relacionada_a_si_mesma():
    print("\n[R9] Nenhuma empresa pode ser 'entidade relacionada' a si mesma")
    casos = [
        ("Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
         "Santander Brasil", ["ma"]),
        ("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
         "Santander Brasil", ["ma"]),
        ("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
         "Gerdau", ["ma"]),
        ("JBS paga R$ 174 milhões e encerra ação sobre fraude fiscal em MT", "JBS", ["fraude"]),
    ]
    todas_ok = True
    for i, (titulo, empresa, evs) in enumerate(casos):
        rec = _rec_com_evento(titulo, empresa, evs, url=f"https://exemplo.test/r09-{i}")
        for c in (rec.get("context_events_by_company") or {}).get(empresa) or []:
            if _sem._n(c.get("subject_company", "")) == _sem._n(empresa):
                todas_ok = False
        for c in (rec.get("informational_events_by_company") or {}).get(empresa) or []:
            if c.get("subject_company") != empresa:
                todas_ok = False
    check(todas_ok, "nenhum registro tem subject_company==empresa dentro de context, "
                    "nem informational com subject_company != empresa monitorada")


def r10_informativo_nunca_entra_em_contexto():
    print("\n[R10] Evento direto informativo NUNCA entra em context_events_by_company")
    rec_tsb = _rec_com_evento(
        "Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
        "Santander Brasil", ["ma"], url="https://exemplo.test/r10a")
    rec_esf = _rec_com_evento(
        "Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
        "Santander Brasil", ["ma"], url="https://exemplo.test/r10b")
    check(not (rec_tsb.get("context_events_by_company") or {}).get("Santander Brasil"),
          "TSB: Santander Brasil sem entrada em context_events_by_company")
    check(not (rec_esf.get("context_events_by_company") or {}).get("Santander Brasil"),
          "Esfera: Santander Brasil sem entrada em context_events_by_company")


def r11_pos_aquisicao_nao_gera_novo_ma():
    print("\n[R11] Referência pós-aquisição não gera novo M&A")
    r = R("Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
          "Santander Brasil", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A não pontua")
    check(d.get("event_id_corrigido") == "resultado_acima_expectativas",
          "evento principal = resultado_acima_expectativas, não M&A novo")
    check(d.get("secondary_context_id") == "integracao_pos_aquisicao",
          "integração pós-aquisição vira contexto secundário do resultado")
    check(d.get("transaction_scope") == "historico_pos_aquisicao",
          f"transaction_scope = historico_pos_aquisicao (obtido {d.get('transaction_scope')})")


def r12_intragrupo_nao_gera_ma_externo():
    print("\n[R12] Reorganização intragrupo não gera M&A externo")
    r = R("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
          "Santander Brasil", ["ma"])
    d = d_de(r, "ma")
    check(d.get("scoreable") is False, "M&A não pontua")
    check(d.get("event_id_corrigido") == "reorganizacao_societaria_interna",
          "reclassificado como reorganização societária interna")
    check(d.get("transaction_scope") == "intragrupo", "transaction_scope = intragrupo")


def r13_ma_externo_legitimo_classificado():
    print("\n[R13] M&A externo legítimo continua sendo classificado (regressão)")
    rec = _rec_com_evento("Bunge conclui acordo de aquisição da Viterra", "Bunge", ["ma"],
                          url="https://exemplo.test/r13")
    check("ma" in rec["events_by_company"].get("Bunge", []), "M&A legítimo preservado")
    info = (rec.get("informational_events_by_company") or {}).get("Bunge") or []
    check(info == [], "M&A legítimo não vira evento informativo")


def r14_vale_samarco_continua_contexto():
    print("\n[R14] Vale/Samarco aparece como contexto de terceiro no card de evolução (regressão)")
    res = _pipeline(_hist(("Vale informa sobre Plano de Recuperação Judicial da Samarco",
                          {"Vale": ["recuperacao_judicial"],
                           "Samarco Mineração": ["recuperacao_judicial"]})))
    ctx = _ctx(res, "Vale")
    check(any(c["event_id"] == "recuperacao_judicial" for c in ctx), "Vale tem context_events com a RJ")
    info = (res["evolution"].get("Vale") or {}).get("informational_events") or []
    check(info == [], "Vale não tem informational_events neste caso (é contexto de terceiro)")


def r15_html_secao_sinal_positivo():
    print("\n[R15] Evento positivo direto aparece na seção 'Sinal positivo · não pontua' do HTML")
    hist = _hist(("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
                 {"Gerdau": ["ma"]}))
    res = _pipeline(hist)
    th = _rd.calibrate_thresholds(hist, CFG)
    dbw = {"90": {"evolution": [v for v in res["evolution"].values()], "feed": res["feed"]}}
    html = _rd.render_html(dbw, CFG, demo=False, changes=res["changes"], payload_thresholds=th)
    check("Sinal positivo" in html, "template contém a seção 'Sinal positivo · não pontua'")
    check('"display_category": "positivo"' in html or '"display_category":"positivo"' in html
          or '\\"display_category\\": \\"positivo\\"' in html,
          "payload contém evento com display_category=positivo")


def r16_html_secao_evento_informativo():
    print("\n[R16] Evento neutro direto aparece na seção 'Evento informativo · não pontua' do HTML")
    hist = _hist(("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
                 {"Santander Brasil": ["ma"]}))
    res = _pipeline(hist)
    th = _rd.calibrate_thresholds(hist, CFG)
    dbw = {"90": {"evolution": [v for v in res["evolution"].values()], "feed": res["feed"]}}
    html = _rd.render_html(dbw, CFG, demo=False, changes=res["changes"], payload_thresholds=th)
    check("Evento informativo" in html, "template contém a seção 'Evento informativo · não pontua'")
    check('"display_category": "informativo"' in html or '"display_category":"informativo"' in html
          or '\\"display_category\\": \\"informativo\\"' in html,
          "payload contém evento com display_category=informativo")


def r17_nao_entra_em_event_ids_for():
    print("\n[R17] Eventos diretos não pontuáveis nunca voltam por event_ids_for")
    rec = _rec_com_evento(
        "Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
        "Santander Brasil", ["ma"], url="https://exemplo.test/r17")
    ids = _rd.event_ids_for(rec, "Santander Brasil")
    check("ma" not in ids, "'ma' não volta por event_ids_for")
    check("integracao_pos_aquisicao" not in ids, "evento informativo não volta por event_ids_for")
    check(ids == [], f"event_ids_for vazio para este registro (obtido {ids})")


def r18_nao_altera_score():
    print("\n[R18] Eventos diretos não pontuáveis não alteram score")
    res = _pipeline(_hist(("Santander supera estimativas de lucro com expansão da base de "
                          "clientes após aquisição", {"Santander Brasil": ["ma"]})))
    check(_score(res, "Santander Brasil") == 0, "score do Santander = 0 (só sinal informativo)")
    res2 = _pipeline(_hist(("Santander Brasil aprova incorporação da Esfera Fidelidade, "
                           "subsidiária integral", {"Santander Brasil": ["ma"]})))
    check(_score(res2, "Santander Brasil") == 0, "score do Santander = 0 (reorganização interna)")


def r19_registros_legados_compativeis():
    print("\n[R19] Registros legados (sem os campos novos) continuam funcionando")
    legado = {"title": "Notícia antiga", "summary": "", "url": "https://exemplo.test/legado",
             "source": "Teste", "domain": "exemplo.test", "pub_ts": int(__import__("time").time()),
             "pub_iso": "2026-01-01 10:00", "companies": ["Vale"],
             "event_ids": ["recuperacao_judicial"]}
    ids = _rd.event_ids_for(legado, "Vale")
    check(ids == ["recuperacao_judicial"], "fallback para event_ids legado funciona")
    ctx_vazio = _rd._build_context_events({"articles": {"u": legado}}, "Vale", 0)
    info_vazio = _rd._build_informational_events({"articles": {"u": legado}}, "Vale", 0)
    check(ctx_vazio == [], "_build_context_events não quebra em registro legado")
    check(info_vazio == [], "_build_informational_events não quebra em registro legado")


def r20_idempotencia_roteamento():
    print("\n[R20] Idempotência: segunda aplicação da regra não muda o registro novamente")
    rec = {"title": "Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
          "summary": "", "url": "https://exemplo.test/r20", "source": "Teste",
          "domain": "exemplo.test", "pub_ts": int(__import__("time").time()) - 86400,
          "pub_iso": "2026-07-30 10:00", "language": "pt", "companies": ["Santander Brasil"],
          "events_by_company": {"Santander Brasil": ["ma"]}, "event_ids": ["ma"],
          "mention_roles": {}, "companies_attributed": ["Santander Brasil"],
          "context_companies": []}
    _sem.apply_semantics_to_record(rec, CFG)
    snap1 = _json.dumps(rec, sort_keys=True, ensure_ascii=False)
    _sem.apply_semantics_to_record(rec, CFG)
    snap2 = _json.dumps(rec, sort_keys=True, ensure_ascii=False)
    check(snap1 == snap2, "segunda aplicação não altera o registro")
    check(len((rec.get("informational_events_by_company") or {}).get("Santander Brasil") or []) == 1,
          "continua exatamente 1 evento informativo após a 2ª aplicação (sem duplicar)")


_ROTEAMENTO = [r01_santander_tsb_informational, r02_santander_esfera_informational,
               r03_vale_samarco_contexto_preservado, r04_cencosud_stmarche_contexto_preservado,
               r05_btg_digimais_contexto_e_rumor_informativo, r06_evento_direto_positivo_generico,
               r07_evento_direto_neutro_generico, r08_evento_direto_negativo_pontuavel_preservado,
               r09_nenhuma_empresa_e_relacionada_a_si_mesma, r10_informativo_nunca_entra_em_contexto,
               r11_pos_aquisicao_nao_gera_novo_ma, r12_intragrupo_nao_gera_ma_externo,
               r13_ma_externo_legitimo_classificado, r14_vale_samarco_continua_contexto,
               r15_html_secao_sinal_positivo, r16_html_secao_evento_informativo,
               r17_nao_entra_em_event_ids_for, r18_nao_altera_score,
               r19_registros_legados_compativeis, r20_idempotencia_roteamento]


# ═══════════════════════════════════════════════════════════════════════
# CORREÇÃO 2026-07-31 (2ª etapa): evento principal econômico do Santander/TSB
# + distinção entre evento AUTÔNOMO não pontuável e componente SECUNDÁRIO de
# uma família semântica já representada por evento principal.
# ═══════════════════════════════════════════════════════════════════════

def f01_downgrade_outlook_nao_vira_informativo_independente():
    print("\n[F1] Downgrade + outlook negativo: só o downgrade é principal, "
          "outlook NÃO vira card informativo independente")
    rec = _rec_com_evento("Rumo tem rating rebaixado e perspectiva negativa pela agência",
                          "Rumo", ["rebaixamento_rating", "outlook_negativo"],
                          url="https://exemplo.test/f01")
    check("rebaixamento_rating" in rec["events_by_company"].get("Rumo", []),
          "downgrade continua pontuável (principal)")
    check("outlook_negativo" not in rec["events_by_company"].get("Rumo", []),
          "outlook não fica em events_by_company")
    info = (rec.get("informational_events_by_company") or {}).get("Rumo") or []
    ctx = (rec.get("context_events_by_company") or {}).get("Rumo") or []
    check(not any(c.get("event_id") == "outlook_negativo" for c in info),
          "outlook NÃO cria card em informational_events_by_company")
    check(not any(c.get("event_id") == "outlook_negativo" for c in ctx),
          "outlook NÃO cria card em context_events_by_company")
    assess = next((a for a in rec.get("event_assessments", [])
                  if a.get("company") == "Rumo" and a.get("event_id") == "outlook_negativo"), {})
    check(assess.get("family_secondary") is True, "outlook marcado como family_secondary=true")
    check(assess.get("primary_event_id") == "rebaixamento_rating",
          f"primary_event_id = rebaixamento_rating (obtido {assess.get('primary_event_id')})")


def f02_prio_rating_mais_ma_family_secondary():
    print("\n[F2] PRIO: rating elevado (principal) + M&A pós-aquisição descartado "
          "no MESMO artigo → componente secundário, não card independente")
    rec = _rec_com_evento(
        "Prio (PRIO3): S&P Global eleva nota de crédito da petrolífera após aquisição de Peregrino",
        "PRIO", ["rating_elevado", "ma"], url="https://exemplo.test/f02")
    check("rating_elevado" in rec["events_by_company"].get("PRIO", []),
          "rating_elevado continua pontuável (principal do mesmo artigo)")
    info = (rec.get("informational_events_by_company") or {}).get("PRIO") or []
    ctx = (rec.get("context_events_by_company") or {}).get("PRIO") or []
    check(info == [], "M&A pós-aquisição NÃO vira card informativo independente para a PRIO")
    check(ctx == [], "M&A pós-aquisição também não vira contexto (é a própria PRIO)")
    assess = next((a for a in rec.get("event_assessments", [])
                  if a.get("company") == "PRIO" and a.get("event_id") == "ma"), {})
    check(assess.get("family_secondary") is True, "'ma' marcado como family_secondary=true")
    check(assess.get("primary_event_id") == "rating_elevado",
          f"primary_event_id = rating_elevado (obtido {assess.get('primary_event_id')})")


def f03_evento_neutro_autonomo_ainda_aparece():
    print("\n[F3] Evento neutro AUTÔNOMO (sem principal concorrente) continua aparecendo como informativo")
    rec = _rec_com_evento("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
                          "Santander Brasil", ["ma"], url="https://exemplo.test/f03")
    info = (rec.get("informational_events_by_company") or {}).get("Santander Brasil") or []
    check(len(info) == 1, "evento autônomo (sem outro evento pontuável no artigo) continua "
                          "com card próprio em informational_events_by_company")


def f04_family_secondary_nao_altera_score():
    print("\n[F4] Family secondary não gera contribuição de score própria")
    res = _pipeline(_hist(("Rumo tem rating rebaixado e perspectiva negativa pela agência",
                          {"Rumo": ["rebaixamento_rating", "outlook_negativo"]})))
    chips = _chips(res, "Rumo")
    check("rebaixamento_rating" in chips, "downgrade aparece como chip")
    check("outlook_negativo" not in chips, "outlook absorvido não aparece como chip separado")
    info = (res["evolution"].get("Rumo") or {}).get("informational_events") or []
    check(info == [], "Rumo não tem informational_events (outlook é secundário, não autônomo)")


def f05_exclusividade_de_containers():
    print("\n[F5] Exclusividade: mesma empresa/evento nunca aparece em mais de um container")
    casos = [
        ("Santander supera estimativas de lucro com expansão da base de clientes após aquisição",
         "Santander Brasil", ["ma"]),
        ("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
         "Santander Brasil", ["ma"]),
        ("Prio (PRIO3): S&P Global eleva nota de crédito da petrolífera após aquisição de Peregrino",
         "PRIO", ["rating_elevado", "ma"]),
        ("Rumo tem rating rebaixado e perspectiva negativa pela agência",
         "Rumo", ["rebaixamento_rating", "outlook_negativo"]),
        ("Vale informa sobre Plano de Recuperação Judicial da Samarco",
         "Vale", ["recuperacao_judicial"]),
        ("Metalúrgica Gerdau aprova programa de recompra de ações próprias GOAU4",
         "Gerdau", ["ma"]),
    ]
    ok = True
    for i, (titulo, empresa, evs) in enumerate(casos):
        rec = _rec_com_evento(titulo, empresa, evs, url=f"https://exemplo.test/f05-{i}")
        ebc_ids = set(rec.get("events_by_company", {}).get(empresa, []))
        ctx_ids = {c.get("event_id") for c in
                  (rec.get("context_events_by_company") or {}).get(empresa, [])}
        info_ids = {c.get("event_id") for c in
                   (rec.get("informational_events_by_company") or {}).get(empresa, [])}
        # os "event_id" em ctx/info podem já vir corrigidos (ex.: recompra_acoes),
        # mas o critério de exclusividade é sobre o event_id ORIGINAL de entrada
        for original_ev in evs:
            containers = sum([
                original_ev in ebc_ids,
                any(c.get("event_id") == original_ev for c in
                    (rec.get("context_events_by_company") or {}).get(empresa, [])),
                any((rec.get("event_assessments") or []) and a.get("event_id") == original_ev
                    and a.get("scoreable") is False and not a.get("family_secondary")
                    and (rec.get("informational_events_by_company") or {}).get(empresa)
                    for a in (rec.get("event_assessments") or [])
                    if a.get("event_id") == original_ev),
            ])
            if containers > 1:
                ok = False
    check(ok, "nenhum evento original aparece em mais de um container simultaneamente")


def f06_idempotencia_family_secondary():
    print("\n[F6] Idempotência com a distinção autônomo × secundário de família")
    rec = {"title": "Prio (PRIO3): S&P Global eleva nota de crédito da petrolífera após aquisição de Peregrino",
          "summary": "", "url": "https://exemplo.test/f06", "source": "Teste",
          "domain": "exemplo.test", "pub_ts": int(__import__("time").time()) - 86400,
          "pub_iso": "2026-07-30 10:00", "language": "pt", "companies": ["PRIO"],
          "events_by_company": {"PRIO": ["rating_elevado", "ma"]},
          "event_ids": ["rating_elevado", "ma"], "mention_roles": {},
          "companies_attributed": ["PRIO"], "context_companies": []}
    _sem.apply_semantics_to_record(rec, CFG)
    snap1 = _json.dumps(rec, sort_keys=True, ensure_ascii=False)
    n_assess1 = len(rec.get("event_assessments", []))
    _sem.apply_semantics_to_record(rec, CFG)
    snap2 = _json.dumps(rec, sort_keys=True, ensure_ascii=False)
    check(snap1 == snap2, "segunda aplicação não altera o registro (caso family_secondary)")
    check(len(rec.get("event_assessments", [])) == n_assess1,
          "não duplica event_assessments na 2ª aplicação")


def f07_html_family_secondary_sem_bloco_independente():
    print("\n[F7] HTML: componente secundário de família NÃO cria bloco independente; "
          "principal, autônomo e contexto de terceiro continuam aparecendo")
    hist = _hist(
        ("Prio (PRIO3): S&P Global eleva nota de crédito da petrolífera após aquisição de Peregrino",
         {"PRIO": ["rating_elevado", "ma"]}),
        ("Santander Brasil aprova incorporação da Esfera Fidelidade, subsidiária integral",
         {"Santander Brasil": ["ma"]}),
        ("Vale informa sobre Plano de Recuperação Judicial da Samarco",
         {"Vale": ["recuperacao_judicial"], "Samarco Mineração": ["recuperacao_judicial"]}),
    )
    res = _pipeline(hist)
    th = _rd.calibrate_thresholds(hist, CFG)
    dbw = {"90": {"evolution": [v for v in res["evolution"].values()], "feed": res["feed"]}}
    html = _rd.render_html(dbw, CFG, demo=False, changes=res["changes"], payload_thresholds=th)
    check(len(html) > 1000, "HTML renderizado")
    prio_info = (res["evolution"].get("PRIO") or {}).get("informational_events") or []
    check(prio_info == [], "PRIO: nenhum bloco informativo independente para o M&A secundário")
    check("Evento informativo" in html, "seção 'Evento informativo · não pontua' existe no template "
                                        "(usada pelo caso autônomo Santander/Esfera)")
    check("Contexto relacionado" in html, "seção de contexto de terceiro continua no template (Vale/Samarco)")


_FAMILIA_SECUNDARIA = [f01_downgrade_outlook_nao_vira_informativo_independente,
                       f02_prio_rating_mais_ma_family_secondary,
                       f03_evento_neutro_autonomo_ainda_aparece,
                       f04_family_secondary_nao_altera_score,
                       f05_exclusividade_de_containers,
                       f06_idempotencia_family_secondary,
                       f07_html_family_secondary_sem_bloco_independente]


if __name__ == "__main__":
    raise SystemExit(main())
