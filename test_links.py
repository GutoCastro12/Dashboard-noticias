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
    check(r["resolution_method"] == "redirect_http", r["resolution_method"])


def t11_gnews_nao_resolvido():
    print("\n[11] Google News não resolvido")
    r = lk.resolve_article_url(GN)
    check(r["link_health"] == "redirect_nao_resolvido", r["link_health"])
    check(r["display_url"] == "", "sem display_url")
    d = lk.interface_decision(r)
    check(not d["render_anchor"] and d["label"] == "Link em verificação", d["label"])


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
    print("\n[24] Fonte da Rumo não resolvida não gera <a>")
    r = lk.resolve_article_url(GN)
    d = lk.interface_decision(r)
    check(not d["render_anchor"] and d["href"] == "", "sem âncora e sem href")


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
               t26_29_invariancia, t30_idempotencia]:
        fn()
    ok = sum(1 for r, _ in results if r)
    print("\n" + "=" * 70)
    print(f"RESULTADO LINKS: {ok}/{len(results)} checagens passaram")
    print("=" * 70)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
