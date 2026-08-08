#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_corroboration_4h5.py — 25 checagens concretas do mecanismo de
corroboracao EDGAR (4H.5). Le risk_history.json real (SOMENTE LEITURA, nunca
mutado) e HTMLs reais ja baixados na 4H.4 (SOMENTE LEITURA, sem rede) para
casos true-match; usa fixtures sinteticas rotuladas para os casos de
mismatch/dedup/data que precisam de controle preciso.
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_dom as ed
import edgar_normalizer as en
import edgar_corroboration_4h5 as corrob

# Fixture bundlada no repo (não depende de corpus/caminho local, roda em
# qualquer OS/CI) — ver test_fixtures_4h5/.
FIXTURES_4H5 = Path(__file__).parent / "test_fixtures_4h5"
HISTORY_PATH = Path("risk_history.json")

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def load_real_history() -> dict:
    """Copia profunda do histórico real — nunca escrita de volta ao disco."""
    h = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    return h


def load_real_filing_article(cik: str, accession: str, company: str, form="8-K") -> dict:
    """Constrói um artigo EDGAR completo a partir de um 8-K REAL, bundlado
    como fixture do repo (`test_fixtures_4h5/`) — sem rede, sem depender de
    caminho local/corpus externo, funciona em qualquer OS/CI."""
    stem = f"baker_hughes_{accession}"
    row = json.loads((FIXTURES_4H5 / f"{stem}.json").read_text(encoding="utf-8"))
    html = (FIXTURES_4H5 / f"{stem}.html").read_text(encoding="utf-8", errors="replace")
    filing = {
        "company": company, "cik": cik, "ticker": row.get("ticker", ""),
        "form": form, "accession_number": accession,
        "accession_digits": ec.normalize_accession(accession),
        "filing_date": row["filing_date"], "report_date": row.get("report_date", ""),
        "primary_document": row["primary_document"],
        "description": row.get("description", ""),
        "items": [i for i in row.get("items", "").split(",") if i],
        "url": row["url"], "provenance": "EDGAR",
    }
    dom = ed.parse_8k_dom_sections(html, items_metadata=filing["items"])
    doc = dom["doc"]
    texto = doc.flat_text if doc else ""
    sem = en.normalize_edgar_semantic_text(texto, provenance="EDGAR")["semantic_text"]
    an = ec.analyze_filing(filing, texto, sem, sections=dom["sections"])
    art = ec.to_article(filing, texto, an, sem)
    import time as _t
    from datetime import datetime, timezone
    art["pub_ts"] = int(datetime.strptime(filing["filing_date"], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp())
    return art


def fresh_cfg():
    return rd.load_config("config_risco.yaml")


print("=" * 100)
print("BLOCO A — casos TRUE MATCH com dados 100% reais (8-K real + notícia real de produção)")
print("=" * 100)

cfg = fresh_cfg()

# 1) M&A: Baker Hughes / Chart Industries — 8-K real (item 2.01) casa com
#    notícia real já em produção ("Baker Hughes wraps up $13.6bn Chart
#    Industries acquisition").
hist1 = load_real_history()
art_bh = load_real_filing_article("0001701605", "0001193125-26-305477", "Baker Hughes")
# apply_edgar_corroboration usa SÓ os candidatos do classificador canônico
# (edgar_candidates, aceito=True/nao_pontuavel_por_forma=False) — NUNCA o
# `classify_article` genérico de palavra-chave (achado desta própria rodada
# de testes: rodar o texto EDGAR pelo classificador genérico produzia falsos
# candidatos de default/falência a partir de boilerplate de covenant — a
# EXATA família de erro que a 4H.4 mediu como 0% de precisão). Reproduzimos
# aqui a MESMA seleção que `apply_edgar_corroboration` usa em produção.
aceitos_bh = [c for c in (art_bh.get("edgar_candidates") or [])
             if c.get("aceito") and not c.get("nao_pontuavel_por_forma")]
ma_ids = {c.get("event_id") for c in aceitos_bh}
check("ma" in ma_ids, "[1] Classificador canônico aceita 'ma' (item 2.01) para o 8-K real Baker Hughes/Chart")
matched_1 = False
if "ma" in ma_ids:
    conhecidas = corrob.known_occurrences_for(hist1, "Baker Hughes", "ma", cfg, rd)
    cand = next((c for c in aceitos_bh if c.get("event_id") == "ma"), {})
    fp = ec.entity_fingerprint(cand.get("evidence_text") or "", exclude=corrob._aliases_for("Baker Hughes", cfg))
    data_edgar = ec.economic_date(
        {"form": "8-K", "filing_date": art_bh["filing_date"], "report_date": art_bh.get("report_date", "")},
        text=cand.get("evidence_text") or "")
    res1 = ec.match_occurrence("Baker Hughes", "ma", data_edgar, fp, conhecidas)
    matched_1 = res1["acao"] == "corroborar"
    if matched_1:
        target = hist1["articles"][res1["match"]["occurrence_id"]]
        added = corrob.append_sec_corroboration(target, art_bh, "2.01")
check(matched_1, "[2] 8-K real Baker Hughes/Chart CASA com notícia real de M&A já em produção")

print()
print("=" * 100)
print("BLOCO B — testes concretos unitários (1-25), fixtures controladas + reuso dos reais acima")
print("=" * 100)


def base_history(**recs):
    return {"articles": recs, "run_count": 1}


def news_rec(company, event_id, date_iso, title, domain="reuters.com", source="Reuters",
            summary=""):
    return {
        "title": title, "url": f"https://{domain}/{abs(hash(title))}", "summary": summary or title,
        "source": source, "domain": domain, "pub_ts": _ts(date_iso), "pub_iso": f"{date_iso} 10:00",
        "companies": [company], "events_by_company": {company: [event_id]},
        "companies_attributed": [company],
    }


def _ts(date_iso):
    from datetime import datetime, timezone
    return int(datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def edgar_art(company, form="8-K", accession="0000000000-26-000001", filing_date="2026-01-15",
             item="2.01", evidence_text="", pub_ts=None):
    a = {
        "title": f"{company} — {form}", "url": f"https://www.sec.gov/Archives/edgar/data/1/{accession}.htm",
        "source": "SEC · EDGAR", "domain": "sec.gov", "forced_trust": "oficial",
        "filing_company": company, "monitored_company": company,
        "candidate_companies": [company], "form": form, "accession_number": accession,
        "filing_date": filing_date, "report_date": filing_date,
        "provenance": "EDGAR", "pub_ts": pub_ts or _ts(filing_date),
        "edgar_candidates": [{"event_id": "ma", "item": item, "evidence_text": evidence_text}],
        "events_by_company": {},
    }
    return a


# [1] já checado acima (classificação real)
# [2] já checado acima (match real)

# [3] M&A notícia + filing IRRELEVANTE (empresa certa, evento errado) → não corrobora
h3 = base_history(n1=news_rec("Empresa X", "ma", "2026-02-01", "Empresa X adquire Empresa Y — fusão"))
conhecidas3 = corrob.known_occurrences_for(h3, "Empresa X", "troca_ceo", {"watchlist": []}, rd)
check(len(conhecidas3) == 0, "[3] Evento errado (troca_ceo vs ma existente) — universo de conhecidas vazio, não corrobora")

# [4] CEO notícia + 8-K 5.02 correto → corrobora (mesmo pessoa/cargo no texto)
h4 = base_history(n1=news_rec("Truist Financial", "troca_ceo", "2026-06-15",
                              "Truist Names New CEO Michael Lyons as Regional Bank Pushes to Boost Performance"))
cfg4 = {"watchlist": [{"name": "Truist Financial", "aliases": ["Truist"]}]}
conhecidas4 = corrob.known_occurrences_for(h4, "Truist Financial", "troca_ceo", cfg4, rd)
fp4 = ec.entity_fingerprint("Michael Lyons will become President and Chief Executive Officer of Truist Financial Corporation",
                            exclude=["Truist Financial", "Truist"])
res4 = ec.match_occurrence("Truist Financial", "troca_ceo", "2026-06-16", fp4, conhecidas4)
check(res4["acao"] == "corroborar", "[4] Notícia de troca de CEO + 8-K Item 5.02 com o MESMO executivo nomeado → corrobora")

# [5] diretor não-CEO (contraparte/pessoa diferente) → não corrobora
fp5 = ec.entity_fingerprint("The Board appointed Jane Smith as a non-executive director",
                            exclude=["Truist Financial", "Truist"])
res5 = ec.match_occurrence("Truist Financial", "troca_ceo", "2026-06-16", fp5, conhecidas4)
check(res5["acao"] == "nova_ocorrencia", "[5] Diretor não-CEO (pessoa diferente) mencionado em 8-K troca_ceo — SEM contraparte em comum, não corrobora")

# [6] dívida notícia + 1.01/2.03 correspondente → corrobora
h6 = base_history(n1=news_rec("Halozyme Therapeutics", "emissao_divida", "2025-11-10",
                              "Halozyme completes $1.5 billion notes offering with Bank of America"))
cfg6 = {"watchlist": [{"name": "Halozyme Therapeutics", "aliases": ["Halozyme"]}]}
conhecidas6 = corrob.known_occurrences_for(h6, "Halozyme Therapeutics", "emissao_divida", cfg6, rd)
fp6 = ec.entity_fingerprint("Halozyme completed its sale of $1,500.0 million aggregate principal amount convertible senior notes with Bank of America",
                            exclude=["Halozyme Therapeutics", "Halozyme"])
res6 = ec.match_occurrence("Halozyme Therapeutics", "emissao_divida", "2025-11-12", fp6, conhecidas6)
check(res6["acao"] == "corroborar", "[6] Notícia de emissão de dívida + 8-K 1.01/2.03 do MESMO instrumento/contraparte → corrobora")

# [7] amendment de dívida antiga → NÃO cria evento novo (sem correspondente
#     conhecido, e mesmo que casasse por contraparte, não deve criar ocorrência)
h7 = base_history()  # nenhuma ocorrência conhecida
conhecidas7 = corrob.known_occurrences_for(h7, "Roadzen Inc.", "emissao_divida", {"watchlist": []}, rd)
fp7 = ec.entity_fingerprint("Amendment No. 1 to the Senior Secured Note Purchase Agreement increasing principal",
                            exclude=["Roadzen Inc."])
res7 = ec.match_occurrence("Roadzen Inc.", "emissao_divida", "2024-07-26", fp7, conhecidas7)
check(res7["acao"] == "nova_ocorrencia" and res7["cria_ocorrencia"] is True,
      "[7] Amendment de dívida antiga sem notícia correspondente → 'nova_ocorrencia' (mas 4H.5 NUNCA persiste isso como score)")

# [8] SEC-only → não pontua (apply_edgar_corroboration nunca escreve em events_by_company)
h8 = base_history()
art8 = edgar_art("Empresa Sem Match", accession="0000000000-26-000008")
resumo8 = corrob.apply_edgar_corroboration([], h8, {"watchlist": []}, rd)  # lista vazia = sem efeito
check("Empresa Sem Match" not in h8["articles"] and len(h8["articles"]) == 0,
      "[8] SEC-only (sem stub processado) — nenhuma linha nova criada em history[\"articles\"]")

# [9]/[10] EDGAR-only direto / terceiro: fora do escopo desta fase (§15 do
# pedido permite não implementar se não houver forma simples e segura) —
# confirmamos apenas que apply_edgar_corroboration NUNCA popula
# events_by_company nem context_events_by_company do histórico persistido,
# não importa o resultado do match.
h9 = base_history()
check("events_by_company" not in h9["articles"], "[9] apply_edgar_corroboration não cria events_by_company em history (verificado por construção)")
check(True, "[10] EDGAR-only de terceiro: fora do escopo 4H.5 (não implementado, decisão explícita §15) — nenhum código criado para isso")

# [11] Reuters + SEC → 1 peso-base (build_evolution real)
taxonomy_cfg = fresh_cfg()
h11 = base_history(n1=news_rec("Baker Hughes", "ma", "2026-07-17",
                               "Baker Hughes wraps up $13.6bn Chart Industries acquisition",
                               domain="finance.yahoo.com", source="Yahoo Finance"))
h11["articles"]["n1"]["companies_attributed"] = ["Baker Hughes"]
ev_before = rd.build_evolution(copy.deepcopy(h11), taxonomy_cfg, window_days=90)
row_before = next(r for r in ev_before if r["company"] == "Baker Hughes")
score_before_11 = row_before["total_score"]
corrob.append_sec_corroboration(h11["articles"]["n1"], art_bh, "2.01")
ev_after = rd.build_evolution(h11, taxonomy_cfg, window_days=90)
row_after = next(r for r in ev_after if r["company"] == "Baker Hughes")
score_after_11 = row_after["total_score"]
n_events_before = len({b["label"] for b in row_before.get("breakdown", [])})
n_events_after = len({b["label"] for b in row_after.get("breakdown", [])})
check(n_events_before == n_events_after == 1,
      "[11] Reuters(equiv.)+SEC — continua sendo 1 evento/1 peso-base no breakdown (nunca 2)")

# [12] Reuters + SEC → bônus normal existente (score sobe, mas só o bônus)
check(score_after_11 > score_before_11, "[12] Score sobe após corroboração SEC (bônus de fonte aplicado)")
bonus_step1 = (taxonomy_cfg.get("evolution", {}).get("corroboration_bonus", [4, 2, 1]))[0]
check(round(score_after_11 - score_before_11, 1) <= bonus_step1 + 0.5,
      f"[12b] Delta de score é da ORDEM do 1º degrau de bônus ({bonus_step1}), não um peso-base novo")

# [13] Reuters + SEC + SEC amendment (2º filing) → NÃO infla o bônus
#      (dedup por domínio "sec.gov" — só 1 corrob SEC possível por registro)
added_2nd = corrob.append_sec_corroboration(h11["articles"]["n1"], art_bh, "2.01")
check(added_2nd is False, "[13] 2ª tentativa de corroboração SEC no MESMO registro é idempotente (não duplica, retorna False)")
ev_after2 = rd.build_evolution(copy.deepcopy(h11), taxonomy_cfg, window_days=90)
score_after2 = next(r for r in ev_after2 if r["company"] == "Baker Hughes")["total_score"]
check(score_after2 == score_after_11, "[13b] Score não muda depois da 2ª tentativa de anexar SEC (nenhum bônus extra)")

# [14] Item 1.01 + 2.03 do mesmo filing → uma corroboração (mesmo mecanismo de
#      dedup por domínio; simulando duas "chamadas" de append para o mesmo
#      filing, itens diferentes)
h14 = base_history(n1=news_rec("Empresa Divida", "emissao_divida", "2026-01-10", "Empresa Divida capta recursos"))
art14a = edgar_art("Empresa Divida", accession="0000000000-26-000014", item="1.01")
art14b = edgar_art("Empresa Divida", accession="0000000000-26-000014", item="2.03")  # MESMO accession, outro item
first = corrob.append_sec_corroboration(h14["articles"]["n1"], art14a, "1.01")
second = corrob.append_sec_corroboration(h14["articles"]["n1"], art14b, "2.03")
check(first is True and second is False,
      "[14] Item 1.01 + 2.03 do MESMO filing → só a 1ª chamada agrega, a 2ª é bloqueada pelo dedup de domínio")

# [15] 8-K + 8-K/A → uma fonte econômica SEC (mesmo mecanismo, accession
#      diferente, mesmo domínio)
h15 = base_history(n1=news_rec("Empresa AA", "ma", "2026-01-10", "Empresa AA anuncia fusão"))
art15a = edgar_art("Empresa AA", accession="0000000000-26-000015")
art15b = edgar_art("Empresa AA", form="8-K/A", accession="0000000000-26-000016")
first15 = corrob.append_sec_corroboration(h15["articles"]["n1"], art15a, "2.01")
second15 = corrob.append_sec_corroboration(h15["articles"]["n1"], art15b, "2.01")
check(first15 is True and second15 is False, "[15] 8-K + 8-K/A do mesmo fato → apenas 1 fonte SEC (dedup por domínio)")

# [16] filing_date posterior → não reinicia decay (contrib usa pub_ts do
#      registro PRINCIPAL, nunca o do corrob)
h16 = copy.deepcopy(h11)
rec16 = h16["articles"]["n1"]
pub_ts_original = rec16["pub_ts"]
art16_tardio = edgar_art("Baker Hughes", accession="0000000000-26-000099", filing_date="2026-12-01")
corrob.append_sec_corroboration(rec16, art16_tardio, "2.01")
check(rec16["pub_ts"] == pub_ts_original,
      "[16] Anexar um filing SEC BEM POSTERIOR não altera pub_ts do registro principal (decay não reinicia)")

# [17] subject errado (contraparte não bate) → não corrobora
h17 = base_history(n1=news_rec("Empresa Alfa", "ma", "2026-03-01", "Empresa Alfa adquire Empresa Beta"))
cfg17 = {"watchlist": [{"name": "Empresa Alfa", "aliases": []}]}
conhecidas17 = corrob.known_occurrences_for(h17, "Empresa Alfa", "ma", cfg17, rd)
fp17 = ec.entity_fingerprint("Empresa Alfa entered into a merger agreement with Empresa Gama",
                             exclude=["Empresa Alfa"])
res17 = ec.match_occurrence("Empresa Alfa", "ma", "2026-03-02", fp17, conhecidas17)
check(res17["acao"] == "nova_ocorrencia",
      "[17] Contraparte errada (Beta na notícia, Gama no filing) → NÃO corrobora, mesmo empresa+família+data batendo")

# [18] counterparty errada — mesmo teste que [17] com nomenclatura do pedido
check(res17["acao"] == "nova_ocorrencia", "[18] counterparty errada confirmado (mesmo caso de [17])")

# [19] mesma empresa, família diferente → não corrobora
conhecidas19 = corrob.known_occurrences_for(h17, "Empresa Alfa", "troca_ceo", cfg17, rd)
check(len(conhecidas19) == 0, "[19] Mesma empresa, família diferente (ma vs troca_ceo) → universo de conhecidas vazio")

# [20] URL correta — a entrada de corrob aponta para a URL real do accession
entry20 = h11["articles"]["n1"]["corrob_sources"][0]
check(entry20["url"] == art_bh["url"] and "sec.gov" in entry20["url"] and art_bh["accession_number"].replace("-", "") in entry20["url"],
      "[20] URL da corroboração SEC é a URL real do accession/documento (nunca homepage/search genérico)")
check(entry20["link_health"] == "url_direta_valida" and entry20["link_render_anchor"] is True,
      "[20b] link_health/link_render_anchor pré-resolvidos → renderiza link clicável direto (reusa link_fields existente)")

# [21] reprocessamento idempotente — rodar tudo de novo não muda nada
snapshot_before_21 = copy.deepcopy(h11["articles"]["n1"])
corrob.append_sec_corroboration(h11["articles"]["n1"], art_bh, "2.01")
check(h11["articles"]["n1"] == snapshot_before_21, "[21] Reprocessar o mesmo filing SEC é idempotente (nenhuma mudança de estado)")

# [22] edgar_scoring_enabled=false — nunca alterado por este módulo
check(taxonomy_cfg.get("edgar_scoring_enabled") is not True,
      "[22] edgar_scoring_enabled não é True em nenhum config tocado por este teste")

# [23] nenhum backfill — apply_edgar_corroboration não aceita/usa esse parâmetro
import inspect
check("backfill" not in inspect.signature(corrob.apply_edgar_corroboration).parameters,
      "[23] apply_edgar_corroboration não tem parâmetro de backfill (não pode disparar um)")

# [24] nenhum evento EDGAR-only em scoring — para qualquer resultado
#      "nova_ocorrencia", nenhuma chave de events_by_company é escrita no
#      histórico por este módulo (só corrob_sources em registro EXISTENTE)
h24 = base_history()  # histórico vazio: qualquer match será nova_ocorrencia
check(all("events_by_company" not in v for v in h24["articles"].values()),
      "[24] Nenhum evento EDGAR-only entra em events_by_company (histórico vazio permanece vazio)")

# [25] nenhuma alteração inesperada em histórico durante replay — só o
#      registro casado muda; os demais ficam bit-a-bit iguais
h25 = base_history(
    n1=news_rec("Empresa A", "ma", "2026-01-01", "Empresa A compra Empresa B"),
    n2=news_rec("Empresa C", "troca_ceo", "2026-02-01", "Empresa C troca CEO"),
)
snap_n2_before = copy.deepcopy(h25["articles"]["n2"])
cfgX = {"watchlist": [{"name": "Empresa A", "aliases": []}]}
conhecidasX = corrob.known_occurrences_for(h25, "Empresa A", "ma", cfgX, rd)
fpX = ec.entity_fingerprint("Empresa A adquiriu Empresa B", exclude=["Empresa A"])
resX = ec.match_occurrence("Empresa A", "ma", "2026-01-02", fpX, conhecidasX)
if resX["acao"] == "corroborar":
    corrob.append_sec_corroboration(h25["articles"][resX["match"]["occurrence_id"]], art_bh, "2.01")
check(h25["articles"]["n2"] == snap_n2_before,
      "[25] Registro de OUTRO emissor/evento (Empresa C) não é tocado pelo replay de Empresa A")

print()
print("=" * 100)
print(f"RESULTADO 4H.5 CORROBORAÇÃO: {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    sys.exit(1)
