#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edgar_4h3e.py — evidência escopada por seção do filing.

Sem rede. O briefing da fase chegou truncado (terminava no §2), então os casos
abaixo cobrem os contratos que o §1/§2 definem explicitamente — três camadas de
texto, segmentação por item/seção e o efeito no que pode pontuar — mais as
regressões medidas no corpus real.
"""
import re
from pathlib import Path

import risk_dashboard as rd
import edgar_canonical as ec
import edgar_normalizer as en
import edgar_sections as es

BASE = Path(__file__).parent
PASS, FAIL = "✅", "❌"
results = []

CAPA = ("UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 "
        "FORM 8-K CURRENT REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES "
        "EXCHANGE ACT OF 1934 Indicate by check mark whether the registrant is an "
        "emerging growth company as defined in Rule 405 of the Securities Act of 1933. ")


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


def _f(form="8-K", items=()):
    return {"company": "Ford Motor", "cik": "0000037996", "ticker": "F",
            "form": form, "accession_number": "0000037996-26-000143",
            "accession_digits": "000003799626000143", "filing_date": "2026-07-24",
            "report_date": "2026-07-20", "primary_document": "d.htm",
            "description": "", "items": list(items), "url": "https://sec.gov/x",
            "provenance": "EDGAR"}


def _pipe(raw, form="8-K", items=(), com_secoes=True):
    sem = en.normalize_edgar_semantic_text(raw)["semantic_text"]
    secs = None
    if com_secoes:
        secs = es.evidence_sections(raw, form=form, items=list(items))["sections"]
    return ec.analyze_filing(_f(form, items), raw, sem, sections=secs), secs


def t01_tres_camadas():
    print("\n[1] Três camadas de texto coexistem e o bruto é intocado")
    raw = CAPA + "Item 2.01 Completion of Acquisition. On July 16, 2026, the Company " \
                 "completed the acquisition of Chart Industries, Inc."
    copia = str(raw)
    sem = en.normalize_edgar_semantic_text(raw)["semantic_text"]
    secs = es.evidence_sections(raw, form="8-K", items=["2.01"])["sections"]
    check(raw == copia, "raw_document_text inalterado")
    check(len(sem) == len(raw), "semantic_text preserva comprimento")
    check(secs and all("start_offset" in s and "end_offset" in s for s in secs),
          "evidence_sections carrega offsets para o bruto")
    s = secs[0]
    check(raw[s["start_offset"]:s["end_offset"]].startswith("Item 2.01"),
          "offsets apontam para o trecho certo do BRUTO")
    check(s["source"] == "8-K" and s["kind"] == "item", "kind e source registrados")


def t02_8k_fatiado_por_item():
    print("\n[2] 8-K é fatiado por Item N.NN")
    raw = (CAPA + "Item 1.01 Entry into a Material Definitive Agreement. Texto A. "
           "Item 5.02 Departure of Directors. Texto B. "
           "Item 9.01 Financial Statements and Exhibits. Texto C.")
    res = es.evidence_sections(raw, form="8-K", items=["1.01", "5.02", "9.01"])
    itens = {s["item"] for s in res["sections"]}
    check(res["estrategia"] == "item_8k", "estratégia = item_8k")
    check(itens == {"1.01", "5.02", "9.01"}, f"três itens isolados: {sorted(itens)}")
    s102 = next(s for s in res["sections"] if s["item"] == "1.01")
    check("Texto A" in s102["text"] and "Texto B" not in s102["text"],
          "cada seção termina no item seguinte (sem vazar)")


def t03_6k_sem_item_usa_release():
    print("\n[3] 6-K não tem item — usa estrutura de press release")
    raw = ("FORM 6-K Nu Holdings Ltd. (Address of principal executive office) "
           "Nubank to Add a Banking License in Brazil through the Acquisition of "
           "Banco Porto Real\nSão Paulo, July 20, 2026 – Nu Holdings Ltd. announced "
           "today that it has entered into a share purchase agreement.")
    res = es.evidence_sections(raw, form="6-K")
    kinds = {s["kind"] for s in res["sections"]}
    check(res["estrategia"] == "release_6k", "estratégia = release_6k")
    check(kinds & {"headline", "dateline"}, f"manchete/dateline capturados: {kinds}")
    texto = " ".join(s["text"] for s in res["sections"])
    check("Banco Porto Real" in texto, "a contraparte real entra na seção")


def t04_ref_line_latam():
    print("\n[4] Linha 'Ref.:' dos ofícios latino-americanos vira seção")
    raw = ("FORM 6-K YPF S.A. Buenos Aires, August 5, 2026. COMISIÓN NACIONAL DE "
           "VALORES Ref. : Material Event – Portfolio Optimization Strategy: "
           "Assignment of Mendoza Non-Operated Cluster. Ladies and Gentlemen,")
    res = es.evidence_sections(raw, form="6-K")
    refs = [s for s in res["sections"] if s["kind"] == "ref_line"]
    check(refs, "ref_line capturada")
    check(refs and "Portfolio Optimization" in refs[0]["heading"],
          f"assunto declarado preservado: {refs[0]['heading'][:60] if refs else '—'}")


def t05_periodico_sem_secao():
    print("\n[5] Formulário periódico não tem seção econômica")
    for form in ("10-Q", "10-K", "20-F", "40-F"):
        res = es.evidence_sections("Item 3. Defaults Upon Senior Securities", form=form)
        check(res["sections"] == [] and res["estrategia"].startswith("periodico"),
              f"{form}: sem seção econômica")


def t06_so_item_pontua():
    print("\n[6] Só seção com estrutura garantida pela SEC pode pontuar")
    # 4H.3F DEMOTE: o "item" textual desta fase (edgar_sections, texto plano)
    # foi rebaixado — o run 31206358785 mostrou 4/4 pontuáveis finais vindos
    # de capa/assinatura. A partir da 4H.3F, nenhum kind deste módulo pontua;
    # só `item_dom` (edgar_dom.py, parser real do HTML) sustenta pontuável —
    # ver test_edgar_4h3f.py.
    check(es.KINDS_PONTUAVEIS == frozenset(),
          "nenhum kind de edgar_sections (texto plano) pontua mais")
    raw = ("FORM 6-K Company Announces Major Acquisition Of Target Corporation Today\n"
           "The company completed the acquisition of Target Corporation.")
    an, secs = _pipe(raw, form="6-K")
    a = next((c for c in an["aceitos"] if c["event_id"] == "ma"), None)
    check(a is not None, "evento continua RECONHECIDO a partir da manchete")
    check(a and a.get("nao_pontuavel_por_forma"),
          "mas manchete não pontua (heurística de layout, não estrutura da SEC)")


def t07_fora_de_secao_nao_pontua():
    print("\n[7] Âncora fora de qualquer seção não pontua")
    raw = (CAPA + "Some narrative text mentioning an acquisition of assets buried "
           "in a paragraph with no item marker and no headline structure at all.")
    an, secs = _pipe(raw, form="8-K")
    a = next((c for c in an["aceitos"] if c["event_id"] == "ma"), None)
    if a is not None:
        check(a.get("nao_pontuavel_por_forma") or a.get("fora_de_secao"),
              "evento fora de seção é marcado como não pontuável")
    else:
        check(True, "nenhum evento pontuável fora de seção")
    check(an["escopo"] == "por_secao", "análise declara o escopo usado")


def t08_item_real_continua_pontuando():
    print("\n[8] Item 8-K via TEXTO reconhece o evento, mas não pontua mais")
    # 4H.3F DEMOTE: até a 4H.3E este caso pontuava (kind="item" textual). A
    # partir da 4H.3F, só `item_dom` (edgar_dom.py) sustenta pontuável — este
    # teste passa a documentar o novo piso: reconhecido, porém informativo.
    # O caso equivalente PONTUÁVEL com parser DOM está em test_edgar_4h3f.py.
    raw = (CAPA + "Item 2.03 Creation of a Direct Financial Obligation. On July 20, "
           "2026, the Company issued $1.5 billion aggregate principal amount of "
           "5.250% senior notes due 2034.")
    an, _ = _pipe(raw, form="8-K", items=["2.03"])
    a = next((c for c in an["aceitos"] if c["event_id"] == "emissao_divida"), None)
    check(a is not None, "emissão reconhecida")
    check(a and a.get("nao_pontuavel_por_forma"),
          "mas NÃO pontua mais via item textual (só item_dom pontua)")


def t09_offsets_validos_no_bruto():
    print("\n[9] Offsets da seção são válidos no texto bruto")
    raw = CAPA + "Item 2.01 Completion. The Company completed the acquisition of Chart."
    res = es.evidence_sections(raw, form="8-K", items=["2.01"])
    for s in res["sections"]:
        trecho = raw[s["start_offset"]:s["end_offset"]]
        check(trecho.strip().startswith("Item"),
              "recorte pelos offsets bate com o bruto")
    pos = raw.index("completed")
    sec = es.section_at(res["sections"], pos)
    check(sec is not None and sec["item"] == "2.01",
          "section_at localiza a seção que contém o offset")


def t10_sem_secao_nao_inventa():
    print("\n[10] Documento-invólucro não gera seção nem evento pontuável")
    raw = ("FORM 6-K LATAM Airlines Group S.A. (Address of principal executive "
           "offices) A. The following exhibit is attached: EXHIBIT NO. DESCRIPTION "
           "99.1 LATAM Airlines Group 2Q 2026 Results")
    res = es.evidence_sections(raw, form="6-K")
    an, _ = _pipe(raw, form="6-K")
    pont = [c for c in an["aceitos"] if not c.get("nao_pontuavel_por_forma")]
    check(pont == [], "invólucro de exhibit não produz evento pontuável")
    check(res["cobertura"] in ("documento_inteiro", "por_release"),
          f"cobertura declarada: {res['cobertura']}")


def t11_regressoes_anteriores():
    print("\n[11] Regressões das fases anteriores continuam válidas")
    # 10-Q: Item 3 padrão não vira default
    an, _ = _pipe("Item3. Defaults Upon Senior Securities 61 Item4. Mine Safety",
                  form="10-Q")
    a = next((c for c in an["aceitos"] if c["event_id"] == "default"), None)
    check(a is None or a.get("nao_pontuavel_por_forma"), "10-Q: Item 3 não pontua")
    # CODM não vira troca de CEO
    an2, _ = _pipe("our chief operating decision maker (CODM) is Jeffrey Miller, "
                   "Chairman of the Board, President and Chief Executive Officer.",
                   form="10-Q")
    a2 = next((c for c in an2["aceitos"] if c["event_id"] == "troca_ceo"), None)
    check(a2 is None or a2.get("nao_pontuavel_por_forma"), "CODM não pontua")
    # 1933/1934 continuam neutralizados
    sem = en.normalize_edgar_semantic_text(CAPA)["semantic_text"]
    check("1933" not in sem and "1934" not in sem, "anos-lei seguem neutralizados")


def t12_producao_isolada():
    print("\n[12] Scoring autônomo intocado (collection deliberadamente ligada)")
    # NOVA INVARIANTE (4H.5F): collection EDGAR deliberadamente LIGADA
    # nesta branch; scoring autônomo continua e sempre continuará OFF.
    prod = rd.load_config(str(BASE / "config_risco.yaml"))
    check(rd.edgar_scoring_enabled(prod) is False, "edgar_scoring_enabled = False")
    check(rd.edgar_collection_enabled(prod) is True, "coleta EDGAR LIGADA (invariante desta branch)")


def t13_determinismo():
    print("\n[13] Segmentação é determinística e idempotente")
    raw = CAPA + "Item 2.01 Completion. Completed the acquisition of Chart Industries."
    a = es.evidence_sections(raw, form="8-K", items=["2.01"])
    b = es.evidence_sections(raw, form="8-K", items=["2.01"])
    check(a == b, "duas chamadas produzem seções idênticas")
    an1, _ = _pipe(raw, form="8-K", items=["2.01"])
    an2, _ = _pipe(raw, form="8-K", items=["2.01"])
    check(an1["event_ids"] == an2["event_ids"], "classificação estável")


TESTES = [t01_tres_camadas, t02_8k_fatiado_por_item, t03_6k_sem_item_usa_release,
          t04_ref_line_latam, t05_periodico_sem_secao, t06_so_item_pontua,
          t07_fora_de_secao_nao_pontua, t08_item_real_continua_pontuando,
          t09_offsets_validos_no_bruto, t10_sem_secao_nao_inventa,
          t11_regressoes_anteriores, t12_producao_isolada, t13_determinismo]


def main():
    print("=" * 78)
    print("TESTE 4H.3E — evidência escopada por seção do filing (sem rede)")
    print("=" * 78)
    for t in TESTES:
        t()
    ok = sum(1 for c, _ in results if c)
    tot = len(results)
    print("\n" + "=" * 78)
    if ok == tot:
        print(f"RESULTADO 4H.3E: {ok}/{tot} checagens passaram")
    else:
        print(f"RESULTADO 4H.3E: {ok}/{tot} — FALHAS:")
        for c, l in results:
            if not c:
                print(f"  {FAIL} {l}")
    print("=" * 78)
    return 0 if ok == tot else 1


if __name__ == "__main__":
    raise SystemExit(main())
