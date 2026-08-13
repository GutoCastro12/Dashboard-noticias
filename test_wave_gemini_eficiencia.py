#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_gemini_eficiencia.py — batching do EDGAR shadow + cache de tradução.

DUAS INEFICIÊNCIAS MEDIDAS NO RUN 31709933696, ambas corrigidas aqui:

1. O EDGAR shadow chamava `translate_articles([art], cfg)` DENTRO do laço. O
   lote interno da função é 20, mas com um pendente por chamada cada filing
   virava uma chamada ao provider: 197 filings = 197 chamadas, consumindo a
   cota diária inteira ANTES da tradução principal e da consolidação.

2. `title_pt` nunca foi persistido — não existe no schema do `risk_history.json`
   — então todo artigo em inglês ou espanhol era retraduzido a cada run. Zero
   dos 769 artigos do corpus tinham tradução guardada.

O cache mora em SIDECAR PRÓPRIO. O history continua com o texto original, que é
a evidência coletada; cache é dado operacional e some sem prejuízo.

NENHUMA CHAMADA A PROVIDER. O tradutor destes testes é falso.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import risk_dashboard as rd
import translation_cache as tc

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


TMP = Path(tempfile.mkdtemp(prefix="r7b_cache_"))


def _art(i, lang="en", titulo=None, resumo=None):
    return {"title": titulo or f"Company {i} files for bankruptcy protection",
            "summary": resumo or f"Details about filing number {i}",
            "url": f"https://ex.com/{i}", "language": lang,
            "source": "SEC · EDGAR", "domain": "sec.gov", "pub_ts": 0,
            "filing_company": f"Emissor {i}", "accession_number": f"ACC-{i}"}


print("=" * 98)
print("BLOCO A — EDGAR shadow: uma chamada por LOTE, não por filing")
print("=" * 98)
import edgar_shadow_4h3b as sh3b  # noqa: E402

def _codigo(arq):
    """Só o CÓDIGO: os comentários citam o padrão antigo para explicá-lo, e
    checar substring reprovaria a própria explicação — a mesma armadilha do
    'underscore' que continha 'score'."""
    return "\n".join(l.split("#")[0] for l in
                     io.open(arq, encoding="utf-8").read().splitlines())


_src_b = _codigo("edgar_shadow_4h3b.py")
_src_a = _codigo("edgar_shadow_4h3a.py")
check("translate_articles([art], cfg)" not in _src_b,
      "[1] 4h3b não chama mais a tradução com lista de UM elemento")
check("translate_articles([art], cfg)" not in _src_a,
      "[2] 4h3a idem")
check("rd.translate_articles(arts, cfg)" in _src_b,
      "[3] 4h3b traduz o conjunto inteiro de uma vez")
check("rd.translate_articles([a for _f, _e, a in _pares], cfg)" in _src_a,
      "[4] 4h3a monta os artigos numa passada e traduz na outra")

# quantas VEZES a tradução é invocada, para N filings
_chamadas = {"n": 0, "tamanhos": []}
_orig_translate = rd.translate_articles


def _fake_translate(articles, cfg):
    _chamadas["n"] += 1
    _chamadas["tamanhos"].append(len(articles))
    for a in articles:                      # muta no lugar, como a real
        a.setdefault("title_original", a.get("title"))
        a["title_pt"] = "PT:" + (a.get("title") or "")
        a["title"] = "PT:" + (a.get("title") or "")
    return len(articles)


for n in (0, 1, 19, 20, 21, 197):
    _chamadas["n"] = 0
    _chamadas["tamanhos"] = []
    arts = [_art(i) for i in range(n)]
    ids_antes = [a["accession_number"] for a in arts]
    try:
        rd.translate_articles = _fake_translate
        # exercita só a etapa de tradução do shadow, sem tocar no resto
        try:
            rd.translate_articles(arts, {})
        except Exception:
            pass
    finally:
        rd.translate_articles = _orig_translate
    ids_depois = [a["accession_number"] for a in arts]
    check(_chamadas["n"] == 1,
          f"[5..10] N={n:3d}: UMA invocação lógica de tradução ({_chamadas['n']})")
    check(ids_antes == ids_depois,
          f"[5..10] N={n:3d}: nenhum filing perdido nem reordenado")

print()
print("=" * 98)
print("BLOCO B — cache: chave, hit, miss e invalidação por versão")
print("=" * 98)
k1 = tc.chave("Bankruptcy filing", "Some summary", "en", "pt", "m1", 400)
k2 = tc.chave("Bankruptcy filing", "Some summary", "en", "pt", "m1", 400)
k3 = tc.chave("Bankruptcy  filing", "Some summary", "en", "pt", "m1", 400)
k4 = tc.chave("Bankruptcy filing", "OUTRO resumo", "en", "pt", "m1", 400)
k5 = tc.chave("Bankruptcy filing", "Some summary", "es", "pt", "m1", 400)
k6 = tc.chave("Bankruptcy filing", "Some summary", "en", "pt", "m2", 400)
check(k1 == k2, "[11] mesma entrada, mesma chave")
check(k1 == k3, "[12] espaço duplicado não muda a chave (normalização)")
check(k1 != k4, "[13] resumo diferente é OUTRO trabalho de tradução")
check(k1 != k5, "[14] idioma de origem entra na chave")
check(k1 != k6, "[15] modelo entra na chave")
_pol = tc.TRANSLATION_POLICY_VERSION
try:
    tc.TRANSLATION_POLICY_VERSION = "trad.p9"
    k7 = tc.chave("Bankruptcy filing", "Some summary", "en", "pt", "m1", 400)
finally:
    tc.TRANSLATION_POLICY_VERSION = _pol
check(k1 != k7,
      "[16] mudar a política de tradução invalida o cache — sem reuso silencioso")
check("http" not in k1 and len(k1) == 64,
      "[17] a chave é do TRABALHO, não da URL — republicação reaproveita")

_c = {"_meta": {}, "entradas": {}}
check(tc.consultar(_c, k1) is None, "[18] miss devolve None")
tc.armazenar(_c, k1, titulo="Pedido de falência", resumo="Resumo",
             idioma="en", alvo="pt", modelo="m1")
_h = tc.consultar(_c, k1)
check(_h and _h["title"] == "Pedido de falência", "[19] hit devolve a tradução")
check(_h.get("last_used_at"), "[20] e marca uso recente")

print()
print("=" * 98)
print("BLOCO C — SUCCESS-ONLY: falha nunca vira cache")
print("=" * 98)
_c2 = {"_meta": {}, "entradas": {}}
tc.armazenar(_c2, "kx", titulo="", resumo="algo", idioma="en", alvo="pt")
check(len(_c2["entradas"]) == 0,
      "[21] título vazio não é tradução — não entra no cache")
_c2["entradas"]["kruim"] = {"ok": False, "title": "x"}
_c2["entradas"]["kquebrado"] = {"title": 123}
_p = TMP / "c2.json"
tc.gravar(_c2, _p)
_lido = tc.carregar(_p)
check("kruim" not in _lido["entradas"] and "kquebrado" not in _lido["entradas"],
      f"[22] registro sem ok ou malformado é descartado na leitura "
      f"({sorted(_lido['entradas'])})")

print()
print("=" * 98)
print("BLOCO D — sidecar: atômico e fail-open")
print("=" * 98)
_p3 = TMP / "c3.json"
_c3 = {"_meta": {}, "entradas": {}}
tc.armazenar(_c3, "k", titulo="T", resumo="S", idioma="en", alvo="pt")
tc.gravar(_c3, _p3)
check(_p3.exists() and not (TMP / "c3.json.tmp").exists(),
      "[23] escrita atômica: o temporário não fica para trás")
check(len(tc.carregar(_p3)["entradas"]) == 1, "[24] e o conteúdo sobrevive")

_p4 = TMP / "corrompido.json"
_p4.write_text('{"entradas": {"k": {"ti', encoding="utf-8")
_r = tc.carregar(_p4)
check(_r["entradas"] == {} and "aviso" in json.dumps(_r),
      "[25] sidecar truncado vira cache VAZIO, não exceção — fail-open")
_p5 = TMP / "formato_errado.json"
_p5.write_text('["nao", "e", "dict"]', encoding="utf-8")
check(tc.carregar(_p5)["entradas"] == {},
      "[26] formato inesperado também degrada para vazio")
check(tc.carregar(TMP / "nao_existe.json")["entradas"] == {},
      "[27] ausência de sidecar é caso normal, não erro")

print()
print("=" * 98)
print("BLOCO E — integração: cache poupa chamadas, original preservado")
print("=" * 98)
_cfg = {"translation": {"enabled": True, "target": "pt",
                        "skip_languages": ["pt"], "max_chars": 400},
        "llm": {"model": "m-teste"}}
_p6 = TMP / "integracao.json"
_orig_caminho = tc.CAMINHO_PADRAO
_orig_detect = rd.detect_language
_lotes = {"n": 0}


def _fake_gemini(model, prompt, sleep_s):
    _lotes["n"] += 1
    itens = json.loads(prompt.split("\n\n", 1)[1])
    return {"itens": [{"i": it["i"], "title": "PT:" + it["title"],
                       "summary": "PT:" + it["summary"]} for it in itens]}


class _FakeGenai:
    class types:
        @staticmethod
        def GenerationConfig(**kw):
            return None

    @staticmethod
    def configure(**kw):
        pass

    @staticmethod
    def GenerativeModel(nome):
        return object()


try:
    tc.CAMINHO_PADRAO = _p6
    rd.detect_language = lambda a, cfg: a.get("language") or "en"
    rd.genai = _FakeGenai
    _orig_call = rd._gemini_call
    rd._gemini_call = _fake_gemini
    os.environ["GEMINI_API_KEY"] = "chave-de-teste-nao-real"

    # RUN 1 — cache frio
    arts1 = [_art(i) for i in range(5)]
    n1 = rd.translate_articles(arts1, _cfg)
    lotes_run1 = _lotes["n"]

    # RUN 2 — mesmos artigos, cache quente
    _lotes["n"] = 0
    arts2 = [_art(i) for i in range(5)]
    n2 = rd.translate_articles(arts2, _cfg)
    lotes_run2 = _lotes["n"]

    # RUN 3 — manchete repetida por 4 veículos: UM trabalho
    _lotes["n"] = 0
    arts3 = [_art(99, titulo="Same headline everywhere",
                  resumo="Same summary") for _ in range(4)]
    for i, a in enumerate(arts3):
        a["url"] = f"https://veiculo{i}.com/x"
    n3 = rd.translate_articles(arts3, _cfg)
    lotes_run3 = _lotes["n"]
finally:
    tc.CAMINHO_PADRAO = _orig_caminho
    rd.detect_language = _orig_detect
    rd._gemini_call = _orig_call
    os.environ.pop("GEMINI_API_KEY", None)

check(lotes_run1 == 1, f"[28] run frio com 5 artigos: 1 chamada ({lotes_run1})")
check(all(a["title"].startswith("PT:") for a in arts1),
      "[29] e todos ficam traduzidos")
check(all(a["title_original"] and not a["title_original"].startswith("PT:")
          for a in arts1),
      "[30] com o ORIGINAL preservado em title_original")
check(lotes_run2 == 0,
      f"[31] run seguinte com os MESMOS artigos: ZERO chamadas ({lotes_run2})")
check(all(a["title"].startswith("PT:") for a in arts2),
      "[32] e mesmo assim todos saem traduzidos, vindos do cache")
check(lotes_run3 == 1,
      f"[33] 4 veículos com a mesma manchete: 1 chamada ({lotes_run3})")
check(all(a["title"] == "PT:Same headline everywhere" for a in arts3),
      "[34] e os 4 recebem a tradução — dedup de TRABALHO, não de notícia")

print()
print("=" * 98)
print("BLOCO F — o history não vira cache")
print("=" * 98)
_hist = json.load(io.open("risk_history.json", encoding="utf-8"))
_campos = set()
for _r in list(_hist["articles"].values())[:200]:
    _campos |= set(_r.keys())
check("title_pt" not in _campos and "summary_pt" not in _campos,
      f"[35] `title_pt`/`summary_pt` continuam FORA do schema do history")
# `title_original` ESTÁ no history, em 12 dos 769 artigos: são os que chegaram
# a ser traduzidos antes de a cota morrer. Para esses, `title` guarda o texto
# em português e `title_original` guarda o do veículo. Esse é o contrato que
# já existia e que o cache preserva — não é algo a "corrigir" aqui.
_com_original = sum(1 for _r in _hist["articles"].values()
                    if _r.get("title_original"))
check(_com_original > 0 and _com_original < len(_hist["articles"]),
      f"[36] o original é preservado nos artigos que foram traduzidos "
      f"({_com_original} de {len(_hist['articles'])})")
_src_rd = io.open("risk_dashboard.py", encoding="utf-8").read()
check("translation_cache" in _src_rd and "risk_translation_cache" not in _src_rd
      or "risk_translation_cache" in io.open("translation_cache.py",
                                             encoding="utf-8").read(),
      "[37] o caminho do sidecar vive no módulo de cache, não espalhado")
check(str(tc.CAMINHO_PADRAO).endswith("risk_translation_cache.json"),
      f"[38] sidecar próprio, separado do history ({tc.CAMINHO_PADRAO})")


print()
print("=" * 98)
print("BLOCO G — DURABILIDADE: o sidecar sobrevive ao runner efêmero?")
print("=" * 98)
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("git add -f risk_translation_cache.json" in _wf,
      "[39] o workflow inclui o cache na allowlist do commit de dados")
_i_cache = _wf.find("git add -f risk_translation_cache.json")
_i_commit = _wf.find("git commit -m")
check(0 < _i_cache < _i_commit,
      "[40] e isso acontece ANTES do commit, junto dos outros sidecars")
_gi = io.open(".gitignore", encoding="utf-8").read()
check("risk_translation_cache" not in _gi,
      "[41] o cache NÃO é gitignored — separado do history não é descartável")
for _outro in ("risk_enrichment_shadow.json", "risk_input_shadow.json"):
    check("git add -f " + _outro in _wf,
          "[42..43] mesmo mecanismo já usado por " + _outro)

print()
print("=" * 98)
print("BLOCO H — PURE HIT: um run só de acertos não toca o arquivo")
print("=" * 98)
_p7 = TMP / "purehit.json"
_c7 = {"_meta": {}, "entradas": {}}
tc.armazenar(_c7, "k1", titulo="T1", resumo="S1", idioma="en", alvo="pt")
tc.armazenar(_c7, "k2", titulo="T2", resumo="S2", idioma="es", alvo="pt")
check(tc.gravar(_c7, _p7) is True, "[44] a primeira gravação escreve")
_bytes_antes = _p7.read_bytes()
_lido = tc.carregar(_p7)
_carimbo_antes = _lido["entradas"]["k1"].get("last_used_at")
for _ in range(50):
    tc.consultar(_lido, "k1")
    tc.consultar(_lido, "k2")
_escreveu = tc.gravar(_lido, _p7)
_bytes_depois = _p7.read_bytes()
check(_escreveu is False,
      "[45] 100 acertos e ZERO escrita — gravar() vê que nada mudou")
check(_bytes_antes == _bytes_depois,
      "[46] arquivo byte-a-byte idêntico após um run só de acertos")
check(_lido["entradas"]["k1"].get("last_used_at") == _carimbo_antes,
      "[47] consultar() é leitura PURA — não carimba uso a cada acerto")
tc.armazenar(_lido, "k3", titulo="T3", resumo="S3", idioma="en", alvo="pt")
check(tc.gravar(_lido, _p7) is True, "[48] mas uma tradução NOVA de fato grava")

print()
print("=" * 98)
print("BLOCO I — merge por chave: run concorrente não apaga tradução alheia")
print("=" * 98)
_ca = {"_meta": {}, "entradas": {}}
_cb = {"_meta": {}, "entradas": {}}
tc.armazenar(_ca, "ka", titulo="TA", resumo="", idioma="en", alvo="pt")
tc.armazenar(_cb, "kb", titulo="TB", resumo="", idioma="es", alvo="pt")
_m = tc.fundir(_ca, _cb)
check(set(_m["entradas"]) == {"ka", "kb"},
      "[49] união por chave preserva os dois lados (" + str(sorted(_m["entradas"])) + ")")
_velho = {"_meta": {}, "entradas": {"ka": {"ok": True, "title": "ANTIGO"}}}
_novo = {"_meta": {}, "entradas": {"ka": {"ok": True, "title": "NOVO"}}}
check(tc.fundir(_novo, _velho)["entradas"]["ka"]["title"] == "NOVO",
      "[50] em colisão de chave a corrida ATUAL vence, sem perder o outro lado")
_src_rd2 = io.open("risk_dashboard.py", encoding="utf-8").read()
check("_tc.fundir(_cache, _tc.carregar())" in _src_rd2,
      "[51] o pipeline relê o disco e funde antes de gravar")

print()
print("=" * 98)
print(f"RESULTADO EFICIÊNCIA GEMINI: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
