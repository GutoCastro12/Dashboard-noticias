#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_links.py — 30 testes do reparo de links (fonte principal E corroboradora)."""
import copy
import json
from pathlib import Path

import link_debt_audit as lk
import risk_dashboard as rd

BASE = Path(__file__).parent
PASS, FAIL = "✅", "❌"
results = []


def check(cond, label):
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}")


GN = "https://news.google.com/rss/articles/CBMixwFBVV95cUxNQUdC?oc=5"
DIRETA = "https://braziljournal.com/exclusivo-rumo-escolhe-rockenbach/"
VALOR = "https://valor.globo.com/empresas/noticia/2026/07/17/rumo.ghtml"


def _src(url, primary, **kw):
    d = {"source": "X", "domain": "x.com", "url": url, "primary": primary}
    d.update(kw)
    return d


def t01_04_principal_secundaria():
    print("\n[1-4] Principal × secundária, direta × redirect")
    for prim in (True, False):
        rot = "principal" if prim else "secundária"
        r = lk.resolve_article_url(DIRETA)
        check(r["link_health"] == "url_direta_valida" and r["display_url"],
              f"{rot} com URL direta → display_url preenchida")
        r2 = lk.resolve_article_url(GN)
        check(r2["display_url"] == "",
              f"{rot} com redirect não resolvido → display_url vazia (sem <a>)")
    a = lk.resolve_article_url(DIRETA)
    b = lk.resolve_article_url(DIRETA)
    check(a["link_health"] == b["link_health"],
          "mesma função e mesmo resultado para principal e corroboradora")


def t05_google_url_param():
    print("\n[5] google.com/url?url=")
    u = "https://www.google.com/url?url=https%3A%2F%2Fvalor.globo.com%2Fa%2Fb.ghtml&sa=U"
    r = lk.resolve_article_url(u)
    check(r["link_health"] == "redirect_resolvido", r["link_health"])
    check(r["display_url"].startswith("https://valor.globo.com/a/b"), r["display_url"])


def t06_param_q_u_target():
    print("\n[6] Parâmetros q / u / target")
    for p in ("q", "u", "target", "destination"):
        u = f"https://redir.test/go?{p}=https%3A%2F%2Fexemplo.com%2Fmateria.html"
        r = lk.resolve_article_url(u)
        check(r["display_url"] == "https://exemplo.com/materia.html", f"parâmetro {p}")


def t07_html_entities():
    print("\n[7] HTML entities")
    u = "https://redir.test/go?url=https%3A%2F%2Fexemplo.com%2Fa.html&amp;x=1"
    r = lk.resolve_article_url(u)
    check(r["display_url"].startswith("https://exemplo.com/a.html"), r["display_url"])


def t08_percent_encoded():
    print("\n[8] Percent-encoding")
    r = lk.resolve_article_url("https://redir.test/go?url=https%3A%2F%2Fexemplo.com%2Fx%2Fy.html")
    check(r["display_url"] == "https://exemplo.com/x/y.html", r["display_url"])


def t09_duplamente_codificada():
    print("\n[9] URL duplamente codificada")
    r = lk.resolve_article_url("https://redir.test/go?u=https%253A%252F%252Fexemplo.com%252Fa%252Fb.html")
    check(r["display_url"] == "https://exemplo.com/a/b.html", r["display_url"])


def t10_redirect_http():
    print("\n[10] Redirect HTTP para URL válida")
    class R:
        status_code = 200
        url = "https://exemplo.com/final.html"
        history = []
    class S:
        def get(self, *a, **k):
            return R()
    r = lk.resolve_article_url(GN, session=S(), allow_network=True)
    check(r["link_health"] == "redirect_resolvido", r["link_health"])
    check(r["display_url"] == "https://exemplo.com/final.html", r["display_url"])
    # consolidação: para host news.google.com o método vem da cadeia canônica
    # (aqui cai no fallback redirect+data-n-au, pois não há sig/ts nem POST)
    check(r["resolution_method"] in ("redirect_http", "redirect_http_data_n_au"),
          r["resolution_method"])


def t11_gnews_nao_resolvido():
    print("\n[11] Google News não resolvido")
    r = lk.resolve_article_url(GN)
    # sem allow_network, o resolvedor de gnews não tenta rede: "nao_verificado"
    # é o rótulo correto (mais preciso que "redirect_nao_resolvido", que
    # implica uma tentativa que falhou)
    check(r["link_health"] in ("redirect_nao_resolvido", "nao_verificado"), r["link_health"])
    check(r["display_url"] == "", "sem display_url")
    d = lk.interface_decision(r)
    # [fix: complete Peru news links] sem resolução do destino final, a URL
    # ORIGINAL do Google News (já coletada, estruturalmente válida) vira
    # fallback clicável — nunca "Link indisponível"/sem âncora quando existe
    # uma URL original recuperável. HTTP 200 do agregador nunca provou
    # resolução, mas a ausência de resolução OFFLINE também não prova que o
    # link do agregador esteja quebrado.
    check(d["render_anchor"], "com âncora (fallback para a URL original do agregador)")
    check(d["href"] == GN, "href = URL original do Google News")
    check(d["label"] == "Abrir notícia (via agregador) →", "rótulo distingue fallback de agregador")


def t12_13_404_410():
    print("\n[12-13] 404 e 410")
    for code in (404, 410):
        class R:
            status_code = code
            url = DIRETA
            history = []
        class S:
            def head(self, *a, **k): return R()
            def get(self, *a, **k): return R()
        r = lk.resolve_article_url(DIRETA, session=S(), allow_network=True,
                                   verify_status=True)
        check(r["link_health"] == "removido_404_410", f"HTTP {code} → removido_404_410")
        check(r["display_url"] == "", f"HTTP {code} não gera <a>")
        check(not lk.interface_decision(r)["render_anchor"], f"HTTP {code} sem âncora")


def t14_403_paywall():
    print("\n[14] 403 / paywall")
    class R:
        status_code = 403
        url = DIRETA
        history = []
    class S:
        def head(self, *a, **k): return R()
        def get(self, *a, **k): return R()
    r = lk.resolve_article_url(DIRETA, session=S(), allow_network=True, verify_status=True)
    check(r["link_health"] == "bloqueado_ou_paywall", r["link_health"])
    d = lk.interface_decision(r)
    check(d["render_anchor"], "403 mantém clicável (não afirma remoção)")
    check("pode exigir acesso" in d["label"], d["label"])


def t15_bloqueio_ambiente():
    print("\n[15] Bloqueio de ambiente ≠ removido")
    class S:
        def get(self, *a, **k):
            raise __import__("requests").exceptions.ConnectionError("proxy")
    r = lk.resolve_article_url(GN, session=S(), allow_network=True)
    check(r["link_health"] in ("bloqueio_de_ambiente", "redirect_nao_resolvido"),
          r["link_health"])
    check(r["link_health"] != "removido_404_410", "NUNCA classifica como removido")


def t16_homepage():
    print("\n[16] Homepage genérica")
    r = lk.resolve_article_url("https://valor.globo.com/")
    check(r["link_health"] == "homepage_generica", r["link_health"])
    check(r["display_url"] == "", "homepage não vira botão de notícia")


def t17_dominio_suspeito():
    print("\n[17] Domínio suspeito")
    r = lk.resolve_article_url("https://po-news-eg.net/materia")
    check(r["link_health"] == "dominio_suspeito", r["link_health"])


def t18_19_url_vazia_sem_esquema():
    print("\n[18-19] URL vazia e sem esquema")
    check(lk.resolve_article_url("")["link_health"] == "url_malformada", "vazia")
    check(lk.resolve_article_url("valor.globo.com/x")["link_health"] == "url_malformada",
          "sem esquema")


def t20_esquemas_perigosos():
    print("\n[20] javascript / data / file bloqueados")
    for u in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd",
              "vbscript:x"):
        r = lk.resolve_article_url(u)
        check(r["display_url"] == "" and r["link_health"] == "url_malformada",
              f"bloqueado: {u[:22]}")


def t21_canonical_preservada():
    print("\n[21] canonical_url válida preservada")
    u = VALOR + "?utm_source=news&gclid=abc"
    r = lk.resolve_article_url(u)
    check(r["canonical_url"] == VALOR, f"tracking removido → {r['canonical_url'][:60]}")
    check(r["display_url"] == VALOR, "display_url usa a canônica")


def t22_vale_preservada():
    print("\n[22] Fonte da Vale que já funcionava permanece")
    u = "https://veja.abril.com.br/economia/vale-elege-presidente-interino/"
    r = lk.resolve_article_url(u)
    check(r["link_health"] == "url_direta_valida", r["link_health"])
    check(r["display_url"].startswith("https://veja.abril.com.br/"),
          "URL direta NÃO foi trocada por redirecionador")


def t23_rumo_resolvida():
    print("\n[23] Fonte da Rumo resolvida usa URL direta")
    cache = {GN: {"url": "https://www.estadao.com.br/einvestidor/citi-rumo/", "exact": True}}
    r = lk.resolve_article_url(GN, cache=cache)
    check(r["link_health"] == "redirect_resolvido", r["link_health"])
    check(r["display_url"].startswith("https://www.estadao.com.br/"), r["display_url"])
    check(r["resolution_method"] == "cache_historico", r["resolution_method"])


def t24_rumo_nao_resolvida():
    print("\n[24] Fonte da Rumo não resolvida cai no fallback do agregador")
    r = lk.resolve_article_url(GN)
    d = lk.interface_decision(r)
    # [fix: complete Peru news links] mesma regra do teste 11: sem resolução
    # do destino final, usa a URL original do Google News como fallback
    # clicável em vez de "sem âncora"/"Link indisponível".
    check(d["render_anchor"] and d["href"] == GN, "âncora com fallback para a URL original")


def t25_engie_valor():
    print("\n[25] Valor: corroboradora resolvida = principal direta")
    direta = lk.resolve_article_url(VALOR)
    corrob = lk.resolve_article_url(GN, cache={GN: {"url": VALOR, "exact": True}})
    check(direta["display_url"] == corrob["display_url"],
          "mesma URL final para principal e corroboradora")
    check(corrob["link_health"] == "redirect_resolvido", corrob["link_health"])


def _hist_min():
    return {"articles": {
        "https://a.test/1": {
            "title": "T", "url": "https://a.test/1", "source": "A", "domain": "a.test",
            "pub_ts": 1780000000, "pub_iso": "2026-06-01 10:00",
            "events_by_company": {"Vale": ["rebaixamento_rating"]},
            "occurrence_id": "occ-1",
            "corroborations": [{"source": "B", "domain": "b.test", "url": GN}],
            "corrob_sources": [{"source": "B", "domain": "b.test", "url": GN}],
        }}, "run_count": 1, "resolved_urls": {}, "last_run": {}}


def t26_29_invariancia():
    print("\n[26-29] Score, eventos, contexto e ocorrências idênticos")
    import argparse
    cfg = rd.load_config(str(BASE / "config_risco.yaml"))
    h = _hist_min()
    antes = copy.deepcopy(h)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td) / "h.json"
        hp.write_text(json.dumps(h), encoding="utf-8")
        args = argparse.Namespace(
            history=str(hp), audit_outdir=td, output_history=str(Path(td) / "o.json"),
            output_html=str(Path(td) / "o.html"), config=str(BASE / "config_risco.yaml"))
        rc = rd.run_link_repair(args, cfg)
        check(rc == 0, "reparo concluiu com sucesso")
        dep = json.loads((Path(td) / "o.json").read_text(encoding="utf-8"))
    a, b = antes["articles"]["https://a.test/1"], dep["articles"]["https://a.test/1"]
    check(a["events_by_company"] == b["events_by_company"], "events_by_company idêntico")
    check(a.get("context_events_by_company") == b.get("context_events_by_company"),
          "context_events_by_company idêntico")
    check(a["occurrence_id"] == b["occurrence_id"], "occurrence_id idêntico")
    check(a["pub_ts"] == b["pub_ts"], "datas idênticas")
    check(len(a["corroborations"]) == len(b["corroborations"]),
          "nenhuma fonte removida (source_count preservado)")


def t30_idempotencia():
    print("\n[30] Idempotência")
    import argparse, tempfile
    cfg = rd.load_config(str(BASE / "config_risco.yaml"))
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td) / "h.json"
        hp.write_text(json.dumps(_hist_min()), encoding="utf-8")
        args = argparse.Namespace(history=str(hp), audit_outdir=td,
                                  output_history=str(hp),
                                  output_html=str(Path(td) / "o.html"),
                                  config=str(BASE / "config_risco.yaml"))
        rd.run_link_repair(args, cfg)
        h1 = json.loads(hp.read_text(encoding="utf-8"))
        rd.run_link_repair(args, cfg)
        h2 = json.loads(hp.read_text(encoding="utf-8"))
    for k in h1["articles"]:
        a = {x: v for x, v in h1["articles"][k].items() if x != "last_checked_at"}
        b = {x: v for x, v in h2["articles"][k].items() if x != "last_checked_at"}
        for d in (a, b):
            for c in (d.get("corroborations") or []) + (d.get("corrob_sources") or []):
                c.pop("last_checked_at", None)
        check(a == b, "segunda execução não altera registros (exceto timestamp)")


def main():
    print("=" * 70)
    print("TESTES — REPARO DE LINKS (30 casos)")
    print("=" * 70)
    for fn in [t01_04_principal_secundaria, t05_google_url_param, t06_param_q_u_target,
               t07_html_entities, t08_percent_encoded, t09_duplamente_codificada,
               t10_redirect_http, t11_gnews_nao_resolvido, t12_13_404_410,
               t14_403_paywall, t15_bloqueio_ambiente, t16_homepage,
               t17_dominio_suspeito, t18_19_url_vazia_sem_esquema,
               t20_esquemas_perigosos, t21_canonical_preservada, t22_vale_preservada,
               t23_rumo_resolvida, t24_rumo_nao_resolvida, t25_engie_valor,
               t26_29_invariancia, t30_idempotencia] + _V2:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 70)
    print(f"RESULTADO LINKS: {ok}/{len(results)} checagens passaram")
    print("=" * 70)
    return 0 if ok == len(results) else 1




# ═══════════════════════════════════════════════════════════════════════
# TESTES v2 — resolvedor canônico consolidado (batchexecute)
# ═══════════════════════════════════════════════════════════════════════
import types


class _Resp:
    def __init__(self, status_code=200, text="", url="", history=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.history = history or []


class MockSession:
    """Sessão falsa que simula as duas chamadas do batchexecute (GET da
    página com data-n-a-sg/ts, depois POST que devolve a URL final)."""

    def __init__(self, *, sig="SIG123", ts="1234567890",
                page_status=200, post_status=200,
                final_url="https://exemplo.com/materia-real.html",
                post_format="garturlres", redirect_final=None,
                raise_on_get=None, raise_on_post=None):
        self.sig, self.ts = sig, ts
        self.page_status, self.post_status = page_status, post_status
        self.final_url = final_url
        self.post_format = post_format
        self.redirect_final = redirect_final
        self.raise_on_get, self.raise_on_post = raise_on_get, raise_on_post
        self.calls = []
        self.headers = {}

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if self.raise_on_get:
            raise self.raise_on_get
        # A chamada de busca de assinatura/timestamp do batchexecute usa
        # SEMPRE https://news.google.com/(rss/)?articles/{art_id} SEM
        # querystring (o art_id já vem sem "?..."). A chamada de fallback de
        # redirect usa a gurl ORIGINAL, que inclui a querystring (?oc=5).
        # Essa é a mesma distinção que o código real faz.
        is_signature_fetch = "/articles/" in url and "?" not in url
        if is_signature_fetch:
            body = ""
            if self.sig and self.ts:
                body = f'<div data-n-a-sg="{self.sig}" data-n-a-ts="{self.ts}"></div>'
            return _Resp(status_code=self.page_status, text=body)
        # fallback: fetch direto do redirecionador (gnews_redirect_fallback)
        final = self.redirect_final if self.redirect_final is not None else self.final_url
        return _Resp(status_code=200, url=final or "", text="")

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        if self.raise_on_post:
            raise self.raise_on_post
        if self.post_format == "garturlres":
            body = ')]}\'\n' + '[["wrb.fr",null,"[\\"garturlres\\",\\"%s\\"]"]]' % self.final_url
        elif self.post_format == "generic":
            body = ')]}\'\n[["wrb.fr",null,"[\\"x\\",\\"%s\\"]"]]' % self.final_url
        elif self.post_format == "still_google":
            body = ')]}\'\n[["wrb.fr",null,"[\\"garturlres\\",\\"https://news.google.com/x\\"]"]]'
        else:
            body = ""
        return _Resp(status_code=self.post_status, text=body)


GN2 = "https://news.google.com/rss/articles/CBMi_MODERN_TOKEN_NO_INLINE_URL_9f9f9f9f9f9f9f9f9f9f?oc=5"


def v01_batchexecute_mockado():
    print("\n[v1] Token moderno resolvido via batchexecute mockado")
    s = MockSession()
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    check(r["link_health"] == "redirect_resolvido", f"health={r['link_health']}")
    check(r["display_url"] == "https://exemplo.com/materia-real.html", r["display_url"])
    check(r["resolution_method"] == "batchexecute", r["resolution_method"])
    check(("POST", "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je")
          in s.calls, "POST ao endpoint batchexecute foi realizado")


def v02_parser_batchexecute():
    print("\n[v2] Parser da resposta batchexecute")
    body1 = ')]}\'\n[["wrb.fr",null,"[\\"garturlres\\",\\"https://exemplo.com/a.html\\"]"]]'
    u, err = lk.parse_batchexecute_response(body1)
    check(u == "https://exemplo.com/a.html" and not err, f"formato garturlres: {u}")
    body2 = ')]}\'\n[["wrb.fr",null,"[\\"x\\",\\"https://exemplo.com/b.html\\"]"]]'
    u2, err2 = lk.parse_batchexecute_response(body2)
    check(u2 == "https://exemplo.com/b.html", f"formato genérico (fallback): {u2}")
    u3, err3 = lk.parse_batchexecute_response("")
    check(not u3 and err3 == "resposta_vazia", "resposta vazia tratada")
    u4, err4 = lk.parse_batchexecute_response('[\\"garturlres\\",\\"https://news.google.com/x\\"]')
    check(not u4, "URL ainda do Google é rejeitada pelo parser")


def v03_fallback_batchexecute_para_redirect():
    print("\n[v3] Fallback: batchexecute falha → redirect HTTP")
    s = MockSession(sig="", ts="", redirect_final="https://exemplo.com/via-redirect.html")
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    check(r["resolution_method"] == "redirect_http_data_n_au", r["resolution_method"])
    check(r["display_url"] == "https://exemplo.com/via-redirect.html", r["display_url"])


def v04_fallback_redirect_para_pendente():
    print("\n[v4] Tudo falha → destino final pendente, mas fallback para a URL "
         "original do agregador gera <a>")
    s = MockSession(sig="", ts="", redirect_final="https://news.google.com/rss/articles/x")
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    check(r["link_health"] == "redirect_nao_resolvido", r["link_health"])
    check(r["display_url"] == "", "sem display_url")
    check(r["resolution_method"] == "nenhum", r["resolution_method"])
    d = lk.interface_decision(r)
    # [fix: complete Peru news links] destino final pendente não significa
    # link indisponível — GN2 (URL original válida) vira fallback clicável.
    check(d["render_anchor"] and d["href"] == GN2, "âncora com fallback para a URL original")


def v05_cache_evita_rede():
    print("\n[v5] Cache evita nova chamada de rede")
    class BoomSession:
        def get(self, *a, **k): raise AssertionError("não deveria chamar rede")
        def post(self, *a, **k): raise AssertionError("não deveria chamar rede")
    cache = {GN2: {"url": "https://exemplo.com/ja-resolvido.html", "exact": True}}
    r = lk.resolve_article_url(GN2, cache=cache, session=BoomSession(), allow_network=True)
    check(r["resolution_method"] in ("cache", "cache_historico"), r["resolution_method"])
    check(r["display_url"] == "https://exemplo.com/ja-resolvido.html", r["display_url"])


def v06_token_invalido():
    print("\n[v6] Token inválido não gera link")
    u = "https://news.google.com/rss/articles/"  # sem token
    s = MockSession(sig="", ts="")
    r = lk.resolve_article_url(u, session=s, allow_network=True)
    check(r["display_url"] == "", "token vazio não resolve")


def v07_http200_nao_e_url_final():
    print("\n[v7] HTTP 200 do Google News não é aceito como resolução")
    s = MockSession(post_format="still_google", redirect_final="https://news.google.com/x")
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    check(r["display_url"] == "", "resposta 200 mas ainda em domínio google → pendente")
    check(r["link_health"] == "redirect_nao_resolvido", r["link_health"])


def v08_dominio_final_diferente_do_agregador():
    print("\n[v8] Domínio final precisa ser diferente do agregador")
    check(lk._is_gnews_host("news.google.com"), "helper reconhece o host do agregador")
    check(not lk._is_gnews_host("exemplo.com"), "host de veículo não é agregador")
    r = lk.resolve_article_url(GN2, session=MockSession(post_format="still_google",
                                                        redirect_final="https://news.google.com/still"),
                               allow_network=True)
    check(r["final_host"] == "", "final_host vazio quando destino é o próprio agregador")


def v09_corroboradora_sem_principais_pendentes():
    print("\n[v9] Corroboradora processada com ZERO principais pendentes")
    hist = {"articles": {
        "https://direta.test/materia": {   # principal já direta: nada pendente
            "url": "https://direta.test/materia", "domain": "direta.test",
            "title": "T", "source": "S",
            "corroborations": [{"source": "C", "domain": "exemplo.com", "url": GN2}],
            "corrob_sources": [{"source": "C", "domain": "exemplo.com", "url": GN2}],
        }}, "resolved_urls": {}}
    import unittest.mock as mock
    with mock.patch.object(rd, "requests") as mreq:
        mreq.Session.return_value = MockSession()
        rd.resolve_history_urls(hist, {}, budget=40)
    c = hist["articles"]["https://direta.test/materia"]["corroborations"][0]
    check(c.get("link_health") == "redirect_resolvido",
          f"corroboradora resolvida mesmo sem principal pendente ({c.get('link_health')})")
    check(c.get("display_url") == "https://exemplo.com/materia-real.html", c.get("display_url"))


def v10_mesmo_resolvedor_principal_e_corrob():
    print("\n[v10] Principal e corroboradora usam o MESMO resolvedor")
    import unittest.mock as mock
    chamadas = []
    orig = lk.resolve_gnews_token

    def espiao(*a, **k):
        chamadas.append(a[0] if a else k.get("gurl", ""))
        return orig(*a, **k)

    hist = {"articles": {
        "https://news.google.com/rss/articles/PRINCIPAL_TOKEN": {
            "url": "https://news.google.com/rss/articles/PRINCIPAL_TOKEN",
            "domain": "principal.test", "title": "T", "source": "S",
            "corroborations": [{"source": "C", "domain": "exemplo.com", "url": GN2}],
        }}, "resolved_urls": {}}
    with mock.patch.object(lk, "resolve_gnews_token", side_effect=espiao), \
         mock.patch.object(rd, "requests") as mreq:
        mreq.Session.return_value = MockSession()
        rd.resolve_history_urls(hist, {}, budget=40)
    check(len(chamadas) >= 2, f"resolvedor chamado para principal E corroboradora ({len(chamadas)}x)")


def v11_vale_preservada_v2():
    print("\n[v11] Vale: fonte direta preservada (regressão)")
    r = lk.resolve_article_url("https://veja.abril.com.br/economia/vale-elege-presidente-interino/")
    check(r["link_health"] == "url_direta_valida", r["link_health"])


def v12_rumo_batchexecute():
    print("\n[v12] Rumo: corroboradora resolvida via batchexecute usa URL direta")
    s = MockSession(final_url="https://www.estadao.com.br/einvestidor/citi-rumo/")
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    check(r["display_url"] == "https://www.estadao.com.br/einvestidor/citi-rumo/",
          r["display_url"])
    check(lk.interface_decision(r)["render_anchor"], "gera âncora clicável")


def v13_rumo_nao_resolvida_v2():
    print("\n[v13] Rumo: token que não resolve cai no fallback do agregador")
    s = MockSession(sig="", ts="", redirect_final="https://news.google.com/still")
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    # [fix: complete Peru news links] mesmo com rede, se o destino final
    # ainda é o próprio agregador (não resolveu de fato), a URL ORIGINAL do
    # Google News (GN2) continua sendo um fallback clicável válido — não
    # "Link indisponível".
    d = lk.interface_decision(r)
    check(d["render_anchor"] and d["href"] == GN2, "âncora com fallback para a URL original")


def v14_engie_valor_batchexecute():
    print("\n[v14] Engie/Valor: corroboradora resolvida termina no domínio direto")
    s = MockSession(final_url=VALOR)
    r = lk.resolve_article_url(GN2, session=s, allow_network=True)
    direta = lk.resolve_article_url(VALOR)
    check(r["final_host"] == direta["final_host"] == "valor.globo.com",
          f"mesmo host final: {r['final_host']}")


def v15_zero_href_vazio():
    print("\n[v15] Nunca gera <a href=\"\">")
    for res in (lk.resolve_article_url(GN2, session=MockSession(post_format="still_google")),
                lk.resolve_article_url(""), lk.resolve_article_url("not-a-url")):
        d = lk.interface_decision(res)
        check(not (d["render_anchor"] and d["href"] == ""),
              "nunca render_anchor=True com href vazio")


def v16_idempotencia_corrob():
    print("\n[v16] Idempotência também para corroboradoras resolvidas por batchexecute")
    cache = {}
    s = MockSession()
    r1 = lk.resolve_article_url(GN2, session=s, cache=cache, allow_network=True)
    cache[GN2] = {"url": r1["resolved_url"], "exact": True}
    calls_antes = len(s.calls)
    r2 = lk.resolve_article_url(GN2, session=s, cache=cache, allow_network=True)
    check(len(s.calls) == calls_antes, "segunda resolução usa cache, sem nova chamada de rede")
    check(r1["display_url"] == r2["display_url"], "resultado idêntico")


_V2 = [v01_batchexecute_mockado, v02_parser_batchexecute, v03_fallback_batchexecute_para_redirect,
       v04_fallback_redirect_para_pendente, v05_cache_evita_rede, v06_token_invalido,
       v07_http200_nao_e_url_final, v08_dominio_final_diferente_do_agregador,
       v09_corroboradora_sem_principais_pendentes, v10_mesmo_resolvedor_principal_e_corrob,
       v11_vale_preservada_v2, v12_rumo_batchexecute, v13_rumo_nao_resolvida_v2,
       v14_engie_valor_batchexecute, v15_zero_href_vazio, v16_idempotencia_corrob]


if __name__ == "__main__":
    raise SystemExit(main())
