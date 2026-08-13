#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_cache_flush.py — nenhuma tradução válida pode ser perdida.

O DEFEITO QUE ISTO PROTEGE

Medido no cron 31738417162. O flush do sidecar de tradução vivia só no fim
feliz do laço de lotes, e cada `return` de interrupção — cota diária esgotada,
nenhum modelo disponível — saía por cima dele. Um run que traduzisse dois lotes
e esbarrasse na cota no terceiro DESCARTAVA as 40 traduções já obtidas: o run
seguinte pagaria de novo pelo mesmo trabalho, que é exatamente o desperdício
que o cache existe para eliminar.

INVARIANTE

    todo sucesso válido obtido ANTES do disjuntor é persistido.

Os cenários cobertos são os cinco caminhos de saída reais da função, mais o
caso misto (acertos de cache seguidos de cota), porque foi o caminho misto que
o cron real exercitou.

NENHUMA CHAMADA A PROVIDER. O provider é falso e o cache vive em diretório
temporário.
"""
from __future__ import annotations

import importlib
import inspect
import io
import json
import os
import re
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cacheflush_"))
os.environ["RISK_TRANSLATION_CACHE"] = str(TMP / "cache.json")

import translation_cache as tc
import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def _so_codigo(fonte: str) -> str:
    """Sem comentários e sem docstrings: este arquivo DOCUMENTA o padrão
    proibido para explicá-lo, e casar substring contra a própria explicação já
    produziu falso positivo antes."""
    sem = "\n".join(l.split("#")[0] for l in fonte.splitlines())
    return re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", sem)


CFG = rd.load_config("config_risco.yaml")


class _StubGenAI:
    class types:
        @staticmethod
        def GenerationConfig(**kw):
            return dict(kw)

    @staticmethod
    def configure(**kw):
        return None

    @staticmethod
    def GenerativeModel(nome):
        return object()


def _artigos(n: int, prefixo: str = "Company") -> list[dict]:
    return [{"title": f"{prefixo} {i} reports record loss", "language": "en",
             "summary": f"Summary number {i}", "url": f"http://x/{i}"}
            for i in range(n)]


def _resposta(lote_idx: int, tam: int) -> dict:
    return {"itens": [{"i": k, "title": f"PT titulo L{lote_idx}#{k}",
                       "summary": f"PT resumo L{lote_idx}#{k}"}
                      for k in range(tam)]}


def rodar_traducao(n_artigos: int, falha_no_lote: int, excecao,
                   persistente: bool = False, caminho: Path | None = None,
                   artigos: list | None = None) -> dict:
    """translate_articles com provider FALSO; devolve o cache lido do disco."""
    p = caminho or (TMP / f"c_{n_artigos}_{falha_no_lote}_"
                          f"{type(excecao).__name__}.json")
    if caminho is None and p.exists():
        p.unlink()
    os.environ["RISK_TRANSLATION_CACHE"] = str(p)
    importlib.reload(tc)
    rd._tc = tc

    chamadas = {"n": 0}
    orig_call, orig_genai = rd._gemini_call, rd.genai

    def falso(model, prompt, sleep_s):
        chamadas["n"] += 1
        # `persistente` existe porque "modelo indisponível" NÃO desiste na
        # primeira ocorrência: o código rotaciona pela lista de fallbacks e
        # retenta o MESMO lote. Só depois de esgotar a lista ele para — e é
        # esse caminho que precisa ser exercitado.
        bateu = (chamadas["n"] >= falha_no_lote if persistente
                 else chamadas["n"] == falha_no_lote)
        if excecao is not None and bateu:
            raise excecao
        tam = len(json.loads(prompt.split("\n\n", 1)[1]))
        return _resposta(chamadas["n"], tam)

    rd._gemini_call, rd.genai = falso, _StubGenAI
    os.environ["GEMINI_API_KEY"] = "chave-de-teste-nao-real"
    try:
        arts = artigos if artigos is not None else _artigos(n_artigos)
        traduzidos = rd.translate_articles(arts, CFG)
    finally:
        rd._gemini_call, rd.genai = orig_call, orig_genai
        os.environ.pop("GEMINI_API_KEY", None)
    disco = json.load(io.open(p, encoding="utf-8")) if p.exists() else None
    return {"cache": disco, "chamadas": chamadas["n"], "arts": arts,
            "traduzidos": traduzidos, "path": p}


print("=" * 98)
print("FLUSH PARCIAL — sucesso obtido antes do disjuntor TEM que sobreviver")
print("=" * 98)

# 50 artigos = 3 lotes (20/20/10). Cota estoura no 3º — o cenário do cron real.
r = rodar_traducao(50, 3, rd.GeminiQuotaExhausted("429 requests per day"))
check(r["chamadas"] == 3, f"[1] o laço fez 3 tentativas e parou (={r['chamadas']})")
check(r["cache"] is not None,
      "[2] o sidecar FOI gravado apesar da interrupção por cota")
ents = (r["cache"] or {}).get("entradas") or {}
check(len(ents) == 40,
      f"[3] os 40 artigos dos 2 lotes bem-sucedidos foram persistidos (={len(ents)})")
check(all(v.get("title") for v in ents.values()),
      "[4] SUCCESS-ONLY preservado: todo registro tem tradução de fato")
traduzidos_mem = sum(1 for a in r["arts"] if a.get("title_pt"))
check(traduzidos_mem == 40,
      f"[5] e as 40 traduções foram aplicadas em memória (={traduzidos_mem})")
check(all(not a.get("title_pt") for a in r["arts"][40:]),
      "[6] os 10 do lote interrompido seguem com o texto ORIGINAL")
check(r["traduzidos"] == 40,
      f"[7] o retorno conta as traduções obtidas, não zero (={r['traduzidos']})")

r2 = rodar_traducao(50, 3, rd.GeminiModelUnavailable("404 model gone"),
                    persistente=True)
ents2 = (r2["cache"] or {}).get("entradas") or {}
check(r2["cache"] is not None and len(ents2) == 40,
      f"[8] com TODOS os modelos indisponíveis no 3º lote, os 40 dos dois "
      f"primeiros sobrevivem (={len(ents2)})")
check(r2["chamadas"] > 3,
      f"[9] e antes de desistir ele rotacionou pelos fallbacks "
      f"({r2['chamadas']} tentativas para 3 lotes)")

r3 = rodar_traducao(50, 3, RuntimeError("erro qualquer do provider"))
ents3 = (r3["cache"] or {}).get("entradas") or {}
check(len(ents3) == 40,
      f"[10] erro comum no 3º lote: os 2 primeiros persistem (={len(ents3)})")
check(r3["chamadas"] == 3,
      "[11] e erro comum NÃO interrompe a corrida por si só")

r4 = rodar_traducao(50, 1, rd.GeminiQuotaExhausted("429 requests per day"))
ents4 = (r4["cache"] or {}).get("entradas") or {}
check(len(ents4) == 0,
      "[12] cota no PRIMEIRO lote: nada a persistir, e nada inventado")
check(all(not a.get("title_pt") for a in r4["arts"]),
      "[13] e todos os artigos mantêm o texto original")

r5 = rodar_traducao(50, 0, None)
ents5 = (r5["cache"] or {}).get("entradas") or {}
check(len(ents5) == 50 and r5["chamadas"] == 3,
      f"[14] caminho feliz intacto: 3 lotes, 50 registros (={len(ents5)})")

print()
print("=" * 98)
print("CAMINHO MISTO — acertos de cache seguidos de cota (o caso do cron real)")
print("=" * 98)
_p = TMP / "misto.json"
if _p.exists():
    _p.unlink()
# 1ª passada: 20 artigos traduzidos e cacheados.
_base = _artigos(20)
_r6 = rodar_traducao(20, 0, None, caminho=_p, artigos=_base)
check(len((_r6["cache"] or {}).get("entradas") or {}) == 20,
      "[15] primeira passada deixa 20 registros no cache")

# 2ª passada: os mesmos 20 (viram acerto) + 40 novos; cota no 1º lote NOVO.
_mistos = _artigos(20) + _artigos(40, prefixo="Novo")
_r7 = rodar_traducao(60, 1, rd.GeminiQuotaExhausted("429 requests per day"),
                     caminho=_p, artigos=_mistos)
_ents7 = (_r7["cache"] or {}).get("entradas") or {}
check(len(_ents7) == 20,
      f"[16] cota logo no 1º lote novo: o cache NÃO encolhe nem é zerado "
      f"(={len(_ents7)})")
check(sum(1 for a in _mistos[:20] if a.get("title_pt")) == 20,
      "[17] os 20 conhecidos foram servidos pelo cache, sem provider")
check(_r7["chamadas"] == 1,
      f"[18] e só UMA tentativa foi feita, para o material novo "
      f"(={_r7['chamadas']})")
check(_r7["traduzidos"] == 20,
      f"[19] o retorno soma os acertos de cache mesmo no caminho interrompido "
      f"(={_r7['traduzidos']})")

print()
print("=" * 98)
print("ESTRUTURA — um único ponto de gravação")
print("=" * 98)
_codigo = _so_codigo(inspect.getsource(rd.translate_articles))
check("finally:" in _codigo
      and re.search(r"finally:\s*\n\s*_flush_cache_traducao\(\)", _codigo),
      "[20] o laço é encerrado por um finally que chama o flush")
check(_codigo.count("_tc.gravar(") == 1,
      "[21] uma única chamada a gravar() em toda a função — não uma por branch")
check(_codigo.count("def _flush_cache_traducao") == 1,
      "[22] e um único helper de flush, definido antes de qualquer saída")

print()
print("=" * 98)
print(f"RESULTADO FLUSH PARCIAL DO CACHE: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
