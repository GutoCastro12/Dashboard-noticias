#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_4h3f.py — parser DOM estrutural do 8-K (última fase técnica da
linha EDGAR). Sem rede.

Cobre os 22 casos do §17, na ordem do pedido, mais regressões diretas dos
bugs REAIS encontrados no corpus de 79 8-K (não hipotéticos):

  - dedup de candidato GLOBAL entre items (Baker Hughes/Chart Industries
    perdia o candidato de M&A do item 2.01 porque "ma" já tinha "nascido"
    no item 1.01);
  - heading do item entrando na busca de âncora (Truist CEO: só "Item 5.02"
    é negrito, o título vem em peso normal — o walker antigo parava cedo);
  - janela de exibição sem trava de seção (NextEra: padding de 320 chars
    antes da âncora invadia a capa/checkbox anterior);
  - "Item N.NN" como referência cruzada, não heading (Truist: "as defined
    in Item 5.03 below");
  - "stock purchase"/"acquir\w+" batendo em Employee Stock Purchase Plan
    (Halliburton, Baker Hughes) — vocabulário de M&A em boilerplate de
    plano de benefícios;
  - nome de EMPRESA aceito como "quem entrou" numa troca de CEO (NextEra
    Share Issuance: "Dominion Energy" != pessoa).
"""
from __future__ import annotations

import re
from pathlib import Path

import risk_dashboard as rd
import edgar_dom as ed
import edgar_canonical as ec
import edgar_normalizer as en

PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


# ── três templates reais de heading observados no corpus (§3/§4) ──────────
def _tpl_bold_span(item, titulo, corpo):
    """Template Ford/Bunge/NextEra: <b>/font-weight:700 cobrindo item+título."""
    return (f'<html><body><div><span style="font-weight:700">Item {item}. '
            f'{titulo}</span></div><div><span>{corpo}</span></div>')


def _tpl_table(item, titulo, corpo):
    """Template Truist: item em <td> bold isolado, título em <td> vizinho."""
    return (f'<html><body><table><tr>'
            f'<td><span style="font-weight:bold">Item&#8201;{item}</span></td>'
            f'<td><p>{titulo}. </p></td></tr></table>'
            f'<p>{corpo}</p>')


def _tpl_first_in_block(item, titulo, corpo):
    """Template Energy Transfer/Loews: SEM negrito, item é 1º conteúdo do bloco."""
    return (f'<html><body><div><span style="font-weight:400">Item {item}. '
            f'{titulo}. </span></div><div><span style="font-weight:400">'
            f'{corpo}</span></div>')


def _parse(html, items):
    dom = ed.parse_8k_dom_sections(html, items_metadata=items)
    return dom


def _filing(items, form="8-K"):
    return {"company": "Test Co", "cik": "0000000001", "form": form,
            "accession_number": "0000000001-26-000001",
            "accession_digits": "000000000126000001", "items": items,
            "report_date": "2026-07-20", "filing_date": "2026-07-20"}


def _run(html, items):
    dom = _parse(html, items)
    texto = dom["doc"].flat_text if dom["doc"] else ""
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    filing = _filing(items)
    an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
    return an, dom


# ─────────────────── 1–2: heading real vs. referência cruzada ────────────
def t01_heading_real_5_02():
    print("\n[1] Heading real do Item 5.02 (negrito)")
    html = _tpl_bold_span("5.02", "Departure of Directors or Certain Officers",
                          "On July 20, 2026, John Lawler resigned as Chief "
                          "Executive Officer. The Board appointed Sherry House "
                          "as Chief Executive Officer, effective immediately.")
    dom = _parse(html, ["5.02"])
    check(dom["sections"] and dom["sections"][0]["item"] == "5.02",
          "seção 5.02 identificada pelo heading em negrito")


def t02_referencia_cruzada_nao_abre_secao():
    print("\n[2] 'Item 5.03 below' (referência cruzada) não abre seção")
    html = ('<html><body><div><span style="font-weight:400">The terms are '
           'more fully described (as defined in Item 5.03 below), a copy of '
           'which is filed as an exhibit.</span></div>'
           '<div><span style="font-weight:700">Item 5.03. Amendments to '
           'Articles.</span></div><div><span>Real content here.</span></div>')
    dom = _parse(html, ["5.03"])
    check(len(dom["sections"]) == 1, "só UMA seção 5.03 (a referência não virou seção)")
    check(dom["sections"][0]["start_offset"] > html.find("as defined"),
          "a seção real começa DEPOIS da referência cruzada")


# ─────────────────────── 3: ordem do DOM preservada ───────────────────────
def t03_ordem_dom_preservada():
    print("\n[3] Seções na ORDEM do documento, não da lista de items")
    html = (_tpl_bold_span("1.01", "Entry into Agreement", "Texto A") +
           '<div><span style="font-weight:700">Item 5.02. Departure.'
           '</span></div><div><span>Texto B</span></div>')
    dom = _parse(html, ["5.02", "1.01"])  # metadata fora de ordem
    itens_ordem = [s["item"] for s in dom["sections"]]
    check(itens_ordem == ["1.01", "5.02"], f"ordem do DOM preservada: {itens_ordem}")


# ─────────────────────── 4–6: heading vs. capa/assinatura/tabela ─────────
def t04_capa_nao_invade_5_02():
    print("\n[4] Capa não invade Item 5.02")
    html = ('<html><body><div style="display:none"><ix:header>'
           '<ix:nonNumeric>0000037996</ix:nonNumeric>'
           '<ix:nonNumeric>hidden cover fact mentioning appoint director '
           'CEO</ix:nonNumeric></ix:header></div>' +
           _tpl_table("5.02", "Departure of Directors", "William H. Rogers, "
                     "Jr. will retire as Chief Executive Officer and "
                     "President effective immediately."))
    an, dom = _run(html, ["5.02"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is not None and a.get("aceito"), "troca de CEO reconhecida")
    check(a and "hidden cover fact" not in (a.get("evidence_text") or ""),
          "conteúdo oculto NÃO aparece na evidência")
    check(a and "Rogers" in (a.get("evidence_text") or ""),
          f"evidência real: {(a.get('evidence_text') or '')[:80]!r}")


def t05_assinatura_nao_invade_secao():
    print("\n[5] Assinatura não invade a última seção")
    html = (_tpl_bold_span("9.01", "Financial Statements and Exhibits",
                           "Real exhibit content here.") +
           '<div><span style="font-weight:700">SIGNATURES</span></div>'
           '<div><span>Pursuant to the Exchange Act, /s/ John Doe, CFO'
           '</span></div>')
    dom = _parse(html, ["9.01"])
    sec = dom["sections"][0]
    check("SIGNATURES" not in sec["text"] and "/s/" not in sec["text"],
          "bloco de assinatura excluído da última seção")


def t06_tabela_nao_invade_secao_errada():
    print("\n[6] Tabela de um item não invade o item seguinte")
    html = (_tpl_table("1.01", "Entry into Agreement", "Term A content.") +
           _tpl_table("9.01", "Financial Statements", "Exhibit table content."))
    dom = _parse(html, ["1.01", "9.01"])
    check(len(dom["sections"]) == 2, "duas seções distintas")
    check("Exhibit table" not in dom["sections"][0]["text"],
          "conteúdo do 9.01 não vaza para o 1.01")


# ─────────────────────── 7–8: metadata × DOM ──────────────────────────────
def t07_metadata_sem_dom_nao_usa_fallback():
    print("\n[7] Item declarado no metadata sem seção no DOM → section_not_found")
    html = _tpl_bold_span("1.01", "Entry into Agreement", "Real text mentioning "
                          "an acquisition somewhere in the document body.")
    dom = _parse(html, ["1.01", "2.01"])  # 2.01 declarado, sem heading no HTML
    check("2.01" in dom["items_missing_in_dom"], "2.01 corretamente marcado como faltante")
    an, _ = _run(html, ["1.01", "2.01"])
    c201 = [c for c in an["candidatos"] if c.get("item") == "2.01"]
    pont = [c for c in c201 if c.get("aceito") and not c.get("nao_pontuavel_por_forma")]
    check(pont == [], "item 2.01 sem seção NUNCA pontua por fallback ao documento inteiro")


def t08_section_not_found_nunca_scoreable():
    print("\n[8] section_not_found nunca sustenta scoreable_simulado")
    html = _tpl_bold_span("9.01", "Financial Statements", "Nothing relevant.")
    an, dom = _run(html, ["9.01", "5.02"])  # 5.02 declarado, ausente do HTML
    check("5.02" in dom["items_missing_in_dom"], "5.02 ausente do DOM, registrado")
    c502 = [c for c in an["candidatos"] if c.get("item") == "5.02" and c.get("aceito")
           and not c.get("nao_pontuavel_por_forma")]
    check(c502 == [], "nenhum candidato pontuável para item sem seção confirmada")


# ─────────────────────── 9–11: 5.02 real/falso ────────────────────────────
def t09_ceo_real_em_5_02():
    print("\n[9] CEO real em 5.02 (nome + cargo + verbo)")
    html = _tpl_table("5.02", "Departure of Directors or Certain Officers",
                      "After 40 years of service, William H. Rogers, Jr. will "
                      "retire as Chief Executive Officer and President "
                      "effective on September 1, 2026.")
    an, _ = _run(html, ["5.02"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is not None and a.get("aceito") and not a.get("nao_pontuavel_por_forma"),
          "troca de CEO real pontuável")
    check(a and a["officer_detail"]["quem_saiu"], "nome extraído")


def t10_diretor_nao_e_ceo():
    print("\n[10] Diretor (conselho) não vira troca de CEO")
    html = _tpl_table("5.02", "Departure of Directors or Certain Officers",
                      "On June 5, 2026, the Board of Directors appointed "
                      "Catherine P. Bessant as a director of the Company, "
                      "effective immediately.")
    an, _ = _run(html, ["5.02"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "nomeação de diretor não pontua como troca de CEO")


def t11_codm_nao_e_ceo():
    print("\n[11] CODM citado não vira troca de CEO")
    html = _tpl_bold_span("7.01", "Regulation FD Disclosure",
                          "our chief operating decision maker (CODM) is "
                          "Jeffrey Miller, Chairman, President and Chief "
                          "Executive Officer. The CODM assesses segments.")
    an, _ = _run(html, ["7.01"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"), "CODM não pontua")


# ─────────────────────── 12–16: item-scoped classification ───────────────
def t12_item_2_01_ma_real():
    print("\n[12] Item 2.01 M&A real (mecânica de contraprestação)")
    html = _tpl_bold_span("2.01", "Completion of Acquisition or Disposition of Assets",
                          "At the effective time of the Merger, each share of "
                          "common stock was canceled and extinguished and "
                          "converted into the right to receive $210.00 in cash.")
    an, _ = _run(html, ["2.01"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "ma"), None)
    check(a is not None and not a.get("nao_pontuavel_por_forma"),
          "M&A real pontuável")


def t13_item_2_03_sem_emissao():
    print("\n[13] Item 2.03 sem evidência de emissão não pontua")
    html = _tpl_bold_span("2.03", "Creation of a Direct Financial Obligation",
                          "This item discusses general obligations without "
                          "any specific debt instrument being created.")
    an, _ = _run(html, ["2.03"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "emissao_divida"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "2.03 sem evidência de emissão real não pontua")


def t14_item_2_04_default_real():
    print("\n[14] Item 2.04 default real (com evidência)")
    html = _tpl_bold_span("2.04", "Triggering Events That Accelerate",
                          "The Company failed to pay the principal due on the "
                          "notes, constituting an event of default which "
                          "accelerated the indebtedness under the credit facility.")
    an, _ = _run(html, ["2.04"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "default"), None)
    check(a is not None and not a.get("nao_pontuavel_por_forma"), "default real pontuável")


def t15_item_8_01_generico_exige_evidencia_forte():
    print("\n[15] Item 8.01 genérico exige evidência forte")
    html = _tpl_bold_span("8.01", "Other Events",
                          "The Company issued a press release.")
    an, _ = _run(html, ["8.01"])
    pont = [c for c in an["aceitos"] if not c.get("nao_pontuavel_por_forma")]
    check(pont == [], "8.01 sem evidência de evento material não pontua nada")


def t16_item_9_01_nunca_gera_sozinho():
    print("\n[16] Item 9.01 nunca gera evento sozinho")
    html = _tpl_bold_span("9.01", "Financial Statements and Exhibits",
                          "(d) Exhibits. Exhibit 99.1 Press release.")
    an, _ = _run(html, ["9.01"])
    check(an["event_ids"] == [], "9.01 puro não gera nenhum evento")


# ─────────────────────── 17–18: exhibits ──────────────────────────────────
def t17_exhibit_relevante_registrado():
    print("\n[17] Exhibit relevante citado no corpo é registrado")
    sec = {"item": "1.01", "text": "The Merger Agreement is attached as "
          "Exhibit 2.1 and incorporated herein by reference."}
    ex = ed.referenced_exhibits(sec)
    check(ex and ex[0]["exhibit_number"] == "2.1", f"exhibit 2.1 capturado: {ex}")
    check(ex[0]["referenced_by_item"] == "1.01", "item de origem preservado")


def t18_exhibit_index_nao_vira_evidencia():
    print("\n[18] Índice de exhibits não vira evidência de referência")
    sec = {"item": "9.01", "text": "Exhibit Index Exhibit No. Description "
          "2.1 Merger Agreement 99.1 Press Release"}
    ex = ed.referenced_exhibits(sec)
    check(ex == [], "índice de exhibits não gera referências (não é citação no corpo)")


# ─────────────────────── 19–20: imutabilidade ─────────────────────────────
def t19_raw_html_imutavel():
    print("\n[19] raw_html nunca é alterado")
    html = _tpl_bold_span("1.01", "Entry into Agreement", "Some content here.")
    original = str(html)
    dom = ed.parse_8k_dom_sections(html, items_metadata=["1.01"])
    check(html == original, "string de entrada idêntica após o parse")
    check(dom["doc"].raw_html == original, "raw_html armazenado é o mesmo objeto")


def t20_raw_text_imutavel():
    print("\n[20] raw_document_text (flat_text) imutável entre chamadas")
    html = _tpl_bold_span("1.01", "Entry into Agreement", "Some content here.")
    dom1 = ed.parse_8k_dom_sections(html, items_metadata=["1.01"])
    dom2 = ed.parse_8k_dom_sections(html, items_metadata=["1.01"])
    check(dom1["doc"].flat_text == dom2["doc"].flat_text,
          "flat_text determinístico entre chamadas")


# ─────────────────────── 21–22: scoring/produção ──────────────────────────
def t21_scoring_false():
    print("\n[21] edgar_scoring_enabled = false")
    prod = rd.load_config("config_risco.yaml")
    check(rd.edgar_scoring_enabled(prod) is False, "scoring desligado em produção")
    check(rd.edgar_collection_enabled(prod) is False, "coleta EDGAR desligada")


def t22_shadow_nao_altera_producao():
    print("\n[22] Shadow (com DOM) não altera produção")
    import edgar_shadow_4h3c as sh
    import tempfile
    cfg = rd.load_config(str(Path(__file__).parent / "fixtures_4h3b" / "config_teste.yaml"))
    cfg["international_official_sources_enabled"] = True
    cfg.setdefault("official_sources", {}).setdefault("EUA", {})["enabled"] = True
    cfg["edgar_scoring_enabled"] = False
    for w in cfg.get("watchlist", []):
        w.setdefault("cik", "0000037996")

    subs = {"name": "Ford Motor Co", "tickers": ["F"], "filings": {"recent": {
        "form": ["8-K"], "filingDate": ["2026-07-20"], "reportDate": ["2026-07-20"],
        "accessionNumber": ["0000037996-26-000143"], "primaryDocument": ["d.htm"],
        "primaryDocDescription": [""], "items": ["2.01"]}}}
    html = _tpl_bold_span("2.01", "Completion of Acquisition",
                          "At the effective time of the Merger, shares were "
                          "converted into the right to receive cash.")
    with tempfile.TemporaryDirectory() as td:
        meta = sh.run_shadow_4h3c(rd, cfg, outdir=td, watch_files=[], history={},
                                  submissions_fetcher=lambda c: subs,
                                  fetcher=lambda u: html)
        check(meta["scoring_enabled"] is False, "scoring_enabled = False")
        check(meta["persisted_records"] == 0, "persisted_records = 0")
        check(meta["arquivos_alterados"] == [], "nenhum arquivo de produção alterado")
        check(meta["corpos_recuperados"] >= 1, "corpo processado via DOM")


# ─────────────────── regressões diretas dos bugs reais ────────────────────
def t23_dedup_candidato_por_item_nao_global():
    print("\n[23] REGRESSÃO — candidato NÃO deduplica entre items diferentes")
    filing = {"company": "Baker Hughes", "form": "8-K",
             "items": ["1.01", "2.01", "2.03"]}
    cands = ec.candidate_events(filing)
    check(any(c["item"] == "1.01" and c["event_id"] == "ma" for c in cands),
          "1.01 gera candidato ma")
    check(any(c["item"] == "2.01" and c["event_id"] == "ma" for c in cands),
          "2.01 TAMBÉM gera candidato ma (não suprimido pelo de 1.01)")
    check(any(c["item"] == "1.01" and c["event_id"] == "emissao_divida" for c in cands)
          and any(c["item"] == "2.03" and c["event_id"] == "emissao_divida" for c in cands),
          "emissao_divida gerado tanto em 1.01 quanto em 2.03")


def t24_stock_purchase_plan_nao_e_ma():
    print("\n[24] REGRESSÃO — Employee Stock Purchase Plan não é M&A")
    html = _tpl_bold_span("5.07", "Submission of Matters to a Vote",
                          "A proposal to amend and restate the Company "
                          "Employee Stock Purchase Plan was approved, "
                          "enabling employees to acquire Company shares.")
    an, _ = _run(html, ["5.07"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "ma"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "plano de compra de ações a funcionários não pontua como M&A")


def t25_nome_empresa_nao_conta_como_pessoa():
    print("\n[25] REGRESSÃO — nome de empresa não conta como executivo")
    html = _tpl_bold_span("1.01", "Entry into a Material Definitive Agreement",
                          "NextEra Energy recommends that shareholders approve "
                          "the Share Issuance. NextEra Energy and Dominion "
                          "Energy have agreed to certain governance-related "
                          "matters; the Board will cause the appointment of "
                          "new members after the Effective Time.")
    an, _ = _run(html, ["1.01"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"),
          "cláusula de governança de M&A (empresa, não pessoa) não pontua")


TESTES = [t01_heading_real_5_02, t02_referencia_cruzada_nao_abre_secao,
          t03_ordem_dom_preservada, t04_capa_nao_invade_5_02,
          t05_assinatura_nao_invade_secao, t06_tabela_nao_invade_secao_errada,
          t07_metadata_sem_dom_nao_usa_fallback, t08_section_not_found_nunca_scoreable,
          t09_ceo_real_em_5_02, t10_diretor_nao_e_ceo, t11_codm_nao_e_ceo,
          t12_item_2_01_ma_real, t13_item_2_03_sem_emissao,
          t14_item_2_04_default_real, t15_item_8_01_generico_exige_evidencia_forte,
          t16_item_9_01_nunca_gera_sozinho, t17_exhibit_relevante_registrado,
          t18_exhibit_index_nao_vira_evidencia, t19_raw_html_imutavel,
          t20_raw_text_imutavel, t21_scoring_false, t22_shadow_nao_altera_producao,
          t23_dedup_candidato_por_item_nao_global, t24_stock_purchase_plan_nao_e_ma,
          t25_nome_empresa_nao_conta_como_pessoa]


def main():
    print("=" * 78)
    print("TESTE 4H.3F — parser DOM estrutural do 8-K (sem rede)")
    print("=" * 78)
    for t in TESTES:
        t()
    ok = sum(1 for c, _ in results if c)
    tot = len(results)
    print("\n" + "=" * 78)
    if ok == tot:
        print(f"RESULTADO 4H.3F: {ok}/{tot} checagens passaram")
    else:
        print(f"RESULTADO 4H.3F: {ok}/{tot} — FALHAS:")
        for c, l in results:
            if not c:
                print(f"  {FAIL} {l}")
    print("=" * 78)
    return 0 if ok == tot else 1


if __name__ == "__main__":
    raise SystemExit(main())
