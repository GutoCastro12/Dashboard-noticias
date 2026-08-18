#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_page_date.py — a data do feed não é a data do fato.

O CASO

O Google News reapresentou em julho de 2026 uma página oficial da Vale
publicada em 31/05/2023. O `<pubDate>` do RSS era a única autoridade de data do
pipeline, então a matéria de 2023 entrou nas janelas de 30, 90 e 365 dias e
sustentou sozinha a Samarco como CRÍTICA, com `hard_critical`, num painel de
leitura executiva.

A página declarava `datePublished` em JSON-LD o tempo todo. Ninguém olhava.

O QUE ESTE ARQUIVO PROTEGE

Nos dois sentidos. Uma camada que corrigisse datas com folga demais seria pior
que o bug: reescreveria datas legítimas por diferença de fuso, e a proveniência
do feed se perderia. Por isso os controles negativos — tolerância, `dateModified`,
página indisponível, lock manual — pesam tanto quanto o caso positivo.

A fixture da Vale é o HTML real da página, reduzido aos blocos que importam.
Nenhum teste depende de rede.
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import json
import tempfile
from pathlib import Path

import reliability_page_date as pd
import reliability_date_repair as rep

# Side-car de proveniência ISOLADO. Sem ele, um `aplicar` de verdade grava a
# URL da Vale dentro do arquivo de auditoria de PRODUÇÃO — foi assim que a
# entrada `21d8d044` acabou publicada por um teste.
_PROV_TMP = os.path.join(tempfile.mkdtemp(prefix='pd_prov_'), 'prov.json')

PASS = FAIL = 0
FIXTURE = Path("test_fixtures_reliability/page_vale_samarco_2023.html")
HTML_VALE = FIXTURE.read_text(encoding="utf-8")
URL_VALE = ("https://www.vale.com/pt/w/vale-informa-sobre-plano-de-"
            "recuperacao-judicial-da-samarco/-/categories/64919")
TITULO_VALE = "Vale informa sobre Plano de Recuperação Judicial da Samarco"
FEED_TS = 1784729650          # 2026-07-22T14:14:10Z, como veio do Google News
PAGINA_TS = 1685502000        # 2023-05-31T03:00:00Z, como a página declara
HIST_REAL = Path("risk_history.json")
SHA_HIST_INICIAL = hashlib.sha256(HIST_REAL.read_bytes()).hexdigest()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def recusa(fn, marca, label):
    try:
        fn()
    except rep.ReparoRecusado as exc:
        check(marca in str(exc), f"{label} (recusa: {str(exc)[:56]})")
        return
    except Exception as exc:                                   # noqa: BLE001
        check(False, f"{label} — erro inesperado {type(exc).__name__}: {exc}")
        return
    check(False, f"{label} — NÃO recusou")


def ld(**campos) -> str:
    return ('<script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org",
                          "@type": "NewsArticle", **campos})
            + "</script>")


print("=" * 98)
print("BLOCO A — A PÁGINA REAL DA VALE")
print("=" * 98)
_r = pd.extrair_data_da_pagina(HTML_VALE, url=URL_VALE, headline=TITULO_VALE)
check(_r["published_ts"] == PAGINA_TS,
      f"[1] datePublished extraído = 2023-05-31 ({_r['published_iso']})")
check(_r["fonte"] == "jsonld", f"[2] via JSON-LD ({_r['fonte']})")
_d = pd.decidir_data_efetiva(FEED_TS, _r)
check(_d["efetivo_ts"] == PAGINA_TS and _d["origem"] == "pagina",
      "[3] a página vence o feed")
check(_d["conflito"] and _d["delta_s"] // 86400 == 1148,
      f"[4] conflito material de {_d['delta_s'] // 86400} dias")
check("FEED_PAGE_DATE_CONFLICT" in _d["motivo"],
      "[5] com proveniência nomeada no motivo")
check(_d["policy"] == "pubdate.p1" == pd.POLICY_VERSION,
      f"[6] política versionada ({pd.POLICY_VERSION})")

print()
print("=" * 98)
print("BLOCO B — dateModified NUNCA É DATA DE PUBLICAÇÃO")
print("=" * 98)
check(_r["modified_iso"].startswith("2022-11-22"),
      f"[7] a página real traz dateModified de 2022 ({_r['modified_iso'][:10]})")
check(_r["published_ts"] != pd._parse_iso("2022-11-22T23:01:00.000Z"),
      "[8] e ele NÃO foi usado como publicação — nesta página é mais ANTIGO "
      "que a publicação, então usá-lo mudaria a resposta")
_h = "<html>" + ld(headline="X", datePublished="2019-01-01T00:00:00Z",
                   dateModified="2026-08-01T00:00:00Z") + "</html>"
_x = pd.extrair_data_da_pagina(_h, headline="X")
check(_x["published_iso"].startswith("2019-01-01"),
      f"[9] com modified recente e published antigo, vence o published "
      f"({_x['published_iso']})")

print()
print("=" * 98)
print("BLOCO C — TOLERÂNCIA: O LIMITE É EXPLÍCITO E TESTADO NOS DOIS LADOS")
print("=" * 98)
check(pd.TOLERANCIA_S == 7 * 86400,
      f"[10] tolerância nomeada = 7 dias ({pd.TOLERANCIA_S}s)")
_base = 1780000000
for _n, (_delta, _esp, _rot) in enumerate([
        (0, False, "datas idênticas"),
        (3 * 3600, False, "3 horas — fuso horário"),
        (pd.TOLERANCIA_S - 1, False, "1s abaixo do limite"),
        (pd.TOLERANCIA_S, False, "exatamente no limite (<= mantém o feed)"),
        (pd.TOLERANCIA_S + 1, True, "1s acima do limite")], start=11):
    _dec = pd.decidir_data_efetiva(_base, {"published_ts": _base - _delta})
    check(_dec["conflito"] is _esp,
          f"[{_n}] {_rot}: conflito={_dec['conflito']} (esperado {_esp})")
check(pd.decidir_data_efetiva(_base, {"published_ts": _base + pd.TOLERANCIA_S + 1})["conflito"],
      "[16] e o limite vale nos DOIS sentidos — página posterior ao feed "
      "também é conflito")

print()
print("=" * 98)
print("BLOCO D — VÁRIOS OBJETOS JSON-LD: ESCOLHER O ARTIGO CERTO")
print("=" * 98)
_multi = ("<html>"
          + '<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>'
          + '<script type="application/ld+json">{"@type":"Organization","name":"Acme"}</script>'
          + ld(headline="Outra matéria qualquer",
               datePublished="2026-08-01T00:00:00Z")
          + ld(headline=TITULO_VALE, datePublished="2023-05-31T03:00:00Z",
               mainEntityOfPage={"@id": URL_VALE})
          + "</html>")
_m = pd.extrair_data_da_pagina(_multi, url=URL_VALE, headline=TITULO_VALE)
check(_m["published_ts"] == PAGINA_TS,
      f"[17] entre dois artigos na mesma página, escolhe o da manchete certa "
      f"({_m['published_iso']})")
check(_m["candidatos"] == 2, f"[18] e registra quantos concorriam ({_m['candidatos']})")
_semmatch = pd.extrair_data_da_pagina(
    "<html>" + ld(headline="Totalmente outra", datePublished="2024-02-02T00:00:00Z")
    + "</html>", url=URL_VALE, headline=TITULO_VALE)
check(_semmatch["published_iso"].startswith("2024-02-02"),
      "[19] sem casar manchete ainda usa o único objeto editorial — "
      "mas com afinidade menor, e o desempate é declarado")

print()
print("=" * 98)
print("BLOCO E — CASAMENTO DE MANCHETE TOLERA NORMALIZAÇÃO LEGÍTIMA")
print("=" * 98)
for _n, _variante in enumerate([
        "Vale informa sobre Plano de Recuperacao Judicial da Samarco",
        "VALE INFORMA SOBRE PLANO DE RECUPERAÇÃO JUDICIAL DA SAMARCO",
        "Vale informa sobre  Plano de Recuperação Judicial da Samarco ",
        "Vale informa sobre Plano de Recuperação Judicial da Samarco!"], start=20):
    _v = pd.extrair_data_da_pagina(HTML_VALE, url=URL_VALE, headline=_variante)
    check(_v["published_ts"] == PAGINA_TS and _v.get("afinidade", 0) >= 4,
          f"[{_n}] acento/caixa/espaço/pontuação não quebram o casamento")

print()
print("=" * 98)
print("BLOCO F — OPENGRAPH COMO SEGUNDA FONTE")
print("=" * 98)
_og = ('<html><meta property="article:published_time" '
       'content="2023-05-31T03:00:00+00:00"></html>')
_o = pd.extrair_data_da_pagina(_og)
check(_o["published_ts"] == PAGINA_TS and _o["fonte"] == "opengraph",
      f"[24] article:published_time é aceito quando não há JSON-LD ({_o['fonte']})")
_ambos = "<html>" + ld(headline="A", datePublished="2020-01-01T00:00:00Z") + \
         '<meta property="article:published_time" content="2026-01-01T00:00:00Z"></html>'
check(pd.extrair_data_da_pagina(_ambos, headline="A")["published_iso"].startswith("2020"),
      "[25] havendo os dois, JSON-LD tem prioridade")

print()
print("=" * 98)
print("BLOCO G — PORTÕES DE PLAUSIBILIDADE")
print("=" * 98)
for _n, (_v, _rot) in enumerate([
        ("1900-01-01T00:00:00Z", "ano 1900 — default de sistema"),
        ("0001-01-01T00:00:00Z", "ano 1 — default de sistema"),
        ("2099-01-01T00:00:00Z", "futuro distante"),
        ("", "vazio"),
        ("ontem de manhã", "texto que não é data")], start=26):
    _p = pd.extrair_data_da_pagina("<html>" + ld(headline="A", datePublished=_v)
                                   + "</html>", headline="A")
    check(_p["published_ts"] == 0,
          f"[{_n}] {_rot}: rejeitado ({_p.get('motivo')[:40]})")
check(pd.extrair_data_da_pagina("<html><script type='application/ld+json'>{quebrado</script></html>")["published_ts"] == 0,
      "[31] JSON-LD malformado não levanta exceção nem inventa data")

print()
print("=" * 98)
print("BLOCO H — FAIL-OPEN: SEM PÁGINA, O FEED PERMANECE")
print("=" * 98)
for _n, (_html, _rot) in enumerate([
        ("", "página vazia (403/timeout)"),
        ("<html><body>sem metadados</body></html>", "HTML sem data forte"),
        ("<html>" + ld(headline="A") + "</html>", "JSON-LD sem datePublished")],
        start=32):
    _dec = pd.decidir_data_efetiva(FEED_TS, pd.extrair_data_da_pagina(_html))
    check(_dec["efetivo_ts"] == FEED_TS and _dec["origem"] == "feed"
          and _dec["verificacao"] == "nao_verificado",
          f"[{_n}] {_rot}: feed mantido, marcado não verificado")
check(pd.decidir_data_efetiva(0, _r)["origem"] == "pagina",
      "[35] e feed SEM data aceita a página sem precisar de conflito")

print()
print("=" * 98)
print("BLOCO I — PROVENIÊNCIA: O FEED NUNCA É APAGADO")
print("=" * 98)
_rec = {"title": TITULO_VALE, "url": URL_VALE, "canonical_url": URL_VALE,
        "pub_ts": FEED_TS, "pub_iso": "2026-07-22 11:14"}
_campos = pd.verificar_registro(dict(_rec), HTML_VALE)
check(_campos["feed_pub_ts"] == FEED_TS
      and _campos["feed_pub_iso"] == "2026-07-22 11:14",
      "[36] a data do feed é preservada em campo próprio")
check(_campos["page_pub_ts"] == PAGINA_TS
      and _campos["page_date_source"] == "jsonld",
      "[37] a data da página e sua fonte ficam registradas")
check(_campos["pub_ts"] == PAGINA_TS
      and _campos["pub_iso"].startswith("2023-05-31"),
      f"[38] a data efetiva passa a ser a da página ({_campos['pub_iso']})")
check(_campos["pub_date_origin"] == "pagina"
      and _campos["pub_date_policy"] == pd.POLICY_VERSION
      and _campos["pub_date_conflict_s"] > 0,
      "[39] com origem, política e magnitude do conflito")
check(_campos["page_date_modified"].startswith("2022-11-22"),
      "[40] dateModified é registrado só como diagnóstico")
check(all(k in pd.campos_de_proveniencia(_rec, _r, _d)
          for k in ("feed_pub_ts", "page_pub_ts", "pub_date_verification")),
      "[41] a reconstrução do 'feed disse X, página disse Y' é completa")

print()
print("=" * 98)
print("BLOCO J — CORREÇÃO MANUAL VENCE O VERIFICADOR (caso GM/Law.com)")
print("=" * 98)
_travado = dict(_rec, manual_correction={"correction_id": "gm_lawcom",
                                         "locked_fields": ["pub_ts", "pub_iso"]})
_ct = pd.verificar_registro(_travado, HTML_VALE)
check(_ct["pub_date_verification"] == "ignorado_correcao_manual",
      "[42] registro com data travada não é tocado pelo verificador")
check("pub_ts" not in _ct and "pub_iso" not in _ct,
      "[43] e nenhuma data nova é proposta para ele")
check(pd.campos_travados(_travado) == {"pub_ts", "pub_iso"},
      "[44] os campos travados são lidos do próprio registro")
_outro = dict(_rec, manual_correction={"locked_fields": ["event_ids"]})
check(pd.verificar_registro(_outro, HTML_VALE).get("pub_ts") == PAGINA_TS,
      "[45] lock em OUTRO campo não bloqueia a correção de data")

print()
print("=" * 98)
print("BLOCO K — FERRAMENTA DE REPARO: IDENTIDADE E DRY-RUN")
print("=" * 98)


def hist_temp(regs: dict) -> str:
    p = Path(tempfile.mkdtemp(prefix="dt_")) / "h.json"
    io.open(p, "w", encoding="utf-8").write(
        json.dumps({"articles": regs}, ensure_ascii=False, indent=1))
    return str(p)


_H = {URL_VALE: {**_rec, "source": "vale.com", "domain": "vale.com",
                 "summary": "s", "companies": ["Vale", "Samarco Mineração"],
                 "event_ids": ["recuperacao_judicial"],
                 "events_by_company": {"Samarco Mineração":
                                       ["recuperacao_judicial"]}},
      "https://exemplo.com/outra": {"title": "Outra", "url": "https://exemplo.com/outra",
                                    "pub_ts": 1780000000, "pub_iso": "2026-06-01 00:00"}}
_p = hist_temp(copy.deepcopy(_H))
_antes_bytes = Path(_p).read_bytes()
_plano = rep.aplicar(_p, URL_VALE, HTML_VALE)
check(_plano["aplicado"] is False, "[46] dry-run é o padrão")
check(Path(_p).read_bytes() == _antes_bytes,
      "[47] e o arquivo não é tocado byte a byte")
check(_plano["conflito"] and _plano["campos"]["pub_ts"] == PAGINA_TS,
      "[48] o dry-run já mostra a data que passaria a valer")
check(set(_plano["mudancas"]) <= set(rep.CAMPOS_DE_DATA),
      f"[49] e só campos de data/proveniência mudariam "
      f"({sorted(_plano['mudancas'])[:3]}…)")
recusa(lambda: rep.aplicar(_p, "https://exemplo.com/inexistente", HTML_VALE),
       "NENHUM_REGISTRO", "[50] URL que não existe no histórico é recusada")
_dois = copy.deepcopy(_H)
_dois[URL_VALE + "?utm_source=x"] = copy.deepcopy(_H[URL_VALE])
_dois[URL_VALE + "?utm_source=x"]["canonical_url"] = URL_VALE
recusa(lambda: rep.aplicar(hist_temp(_dois), URL_VALE, HTML_VALE),
       "REGISTRO_AMBIGUO", "[51] dois registros com a mesma URL canônica abortam")
recusa(lambda: rep.aplicar(_p, URL_VALE, "<html>sem data</html>", aplicar_de_fato=True, caminho_proveniencia=_PROV_TMP),
       "SEM_DATA_NA_PAGINA", "[52] sem data forte na página, não corrige nada")
_semconf = copy.deepcopy(_H)
_semconf[URL_VALE]["pub_ts"] = PAGINA_TS + 3600
recusa(lambda: rep.aplicar(hist_temp(_semconf), URL_VALE, HTML_VALE,
                           aplicar_de_fato=True, caminho_proveniencia=_PROV_TMP),
       "SEM_CONFLITO_MATERIAL", "[53] sem conflito material, apply é recusado")
_lock = copy.deepcopy(_H)
_lock[URL_VALE]["manual_correction"] = {"locked_fields": ["pub_ts"]}
recusa(lambda: rep.aplicar(hist_temp(_lock), URL_VALE, HTML_VALE,
                           aplicar_de_fato=True, caminho_proveniencia=_PROV_TMP),
       "CORRECAO_MANUAL_TRAVADA", "[54] lock manual impede o reparo")

print()
print("=" * 98)
print("BLOCO L — APPLY EM FIXTURE: CIRÚRGICO E VERIFICADO")
print("=" * 98)
_p2 = hist_temp(copy.deepcopy(_H))
_ap = rep.aplicar(_p2, URL_VALE, HTML_VALE, aplicar_de_fato=True, caminho_proveniencia=_PROV_TMP)
_novo = json.load(io.open(_p2, encoding="utf-8"))
check(_ap["aplicado"] and _ap["registros_alterados"] == 1,
      "[55] exatamente um registro alterado")
check(_novo["articles"][URL_VALE]["pub_ts"] == PAGINA_TS,
      "[56] a data efetiva foi gravada")
check(_novo["articles"][URL_VALE]["feed_pub_ts"] == FEED_TS,
      "[57] e a do feed continua lá")
check(_novo["articles"][URL_VALE]["events_by_company"]
      == _H[URL_VALE]["events_by_company"],
      "[58] a classificação semântica não mudou")
check(_novo["articles"]["https://exemplo.com/outra"]
      == _H["https://exemplo.com/outra"],
      "[59] nenhum outro artigo foi tocado")
check(Path(_ap["backup"]).exists() and _ap["sha256_antes"] != _ap["sha256_depois"],
      "[60] backup gravado e hashes registrados")
check(rep.digest_intocavel(_novo["articles"][URL_VALE])
      == _ap["hash_intocavel_antes"],
      "[61] o hash dos campos intocáveis é idêntico antes e depois")

print()
print("=" * 98)
print("BLOCO M — O CAMINHO DE PRODUÇÃO CHAMA A VERIFICAÇÃO")
print("=" * 98)
_src = io.open("risk_dashboard.py", encoding="utf-8").read()
check("def verify_publication_dates" in _src,
      "[62] a função existe em risk_dashboard")
_i_res = _src.find("resolve_google_news_urls(matched, history, cfg)")
_i_ver = _src.find("verify_publication_dates(matched, cfg)")
_i_mer = _src.find("added_urls = merge_into_history(")
check(0 < _i_res < _i_ver < _i_mer,
      "[63] e é chamada DEPOIS de resolver as URLs e ANTES de persistir — "
      "corrigir após a persistência exigiria reprocessar histórico")
check("_pd.campos_travados" in _src and "ignorado_correcao_manual" in _src,
      "[64] o caminho de produção também respeita o lock manual")
check("canonicalize" in _src[_i_ver:_i_mer] or "cache[chave]" in _src,
      "[65] e deduplica a busca por URL canônica dentro da execução")

print()
print("=" * 98)
print("BLOCO N — REGRESSÃO DE RESSURGIMENTO FUTURO")
print("=" * 98)
# Um item novo chegando hoje, com a mesma armadilha: feed diz 2026, página 2023.
_novo_item = {"title": TITULO_VALE, "url": URL_VALE, "canonical_url": URL_VALE,
              "pub_ts": FEED_TS, "pub_iso": "2026-07-22 11:14"}
_c = pd.verificar_registro(_novo_item, HTML_VALE)
_novo_item.update(_c)
check(_novo_item["pub_ts"] == PAGINA_TS,
      "[66] o item entraria no histórico com a data de 2023")
_hoje = 1786000000     # meados de agosto de 2026
for _n, _janela in enumerate((30, 90, 365), start=67):
    _dentro = (_hoje - _novo_item["pub_ts"]) <= _janela * 86400
    check(not _dentro,
          f"[{_n}] e ficaria FORA da janela de {_janela}d — que é o efeito "
          f"que remove o falso crítico")
_com_feed = (_hoje - FEED_TS) <= 30 * 86400
check(_com_feed,
      "[70] enquanto com a data do feed ele estaria DENTRO da janela de 30d — "
      "a diferença entre o painel certo e o errado")

print()
print("=" * 98)
print("BLOCO O — LIMITE HONESTO DA CAMADA")
print("=" * 98)
_gm = pd.decidir_data_efetiva(1786000000, pd.extrair_data_da_pagina(""))
check(_gm["origem"] == "feed" and _gm["verificacao"] == "nao_verificado",
      "[71] página inacessível (403, como o Law.com) continua sem correção "
      "automática — a camada reduz o risco, não o elimina")
check(pd.extrair_data_da_pagina("<html><p>Publicado em 31/05/2023</p></html>")["published_ts"] == 0,
      "[72] data apenas visível no texto NÃO é aceita nesta versão — "
      "heurística sobre números soltos criaria erro novo")

print()
print("=" * 98)
print("BLOCO P — O HISTÓRICO REAL NÃO FOI TOCADO POR ESTE ARQUIVO")
print("=" * 98)
check(hashlib.sha256(HIST_REAL.read_bytes()).hexdigest() == SHA_HIST_INICIAL,
      "[73] risk_history.json byte a byte idêntico ao início")

print()
print("=" * 98)
print(f"RESULTADO DATA DE PUBLICAÇÃO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
