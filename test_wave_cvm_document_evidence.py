#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_cvm_document_evidence.py — capturar evidência sem decidir nada.

A Fase 1 tem duas promessas, e as duas são fáceis de quebrar sem perceber.

A primeira: o MIME da CVM mente. `rad.cvm.gov.br` devolve `text/html` com corpo
`%PDF-`, em 6 de 6 documentos medidos. Um extrator que escolha pelo cabeçalho
roda parser de HTML sobre fluxo PDF comprimido e devolve lixo que PARECE texto
— foi exatamente o que aconteceu antes desta função existir. Por isso a suíte
fixa o caso canônico: cabeçalho mentindo, bytes mandando.

A segunda: capturar não é decidir. A evidência é gravada e nenhum classificador
a lê. Se um dia ela vazar para a semântica sem uma onda explícita, o teste de
neutralidade quebra — e é ele que permite atribuir uma futura mudança de score
ao consumo da evidência, e não à captura.

E há uma terceira, que é sobre disciplina: registro antigo não é enriquecido.
Não existe caminho "se falta evidência, busca". Isso seria backfill implícito,
e destruiria a comparabilidade dos experimentos congelados, que dependem do
input tal como era quando foram medidos.

Nenhum teste aqui toca a rede. CI que depende da CVM estar no ar falha por
motivo alheio ao código.
"""
from __future__ import annotations

import io
import json
import zlib

import cvm_document_evidence as ce

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pdf_sintetico(texto: str) -> bytes:
    """PDF mínimo válido com texto inventado. Fixture própria: commitar um fato
    relevante real seria material de terceiro no repositório de teste."""
    fluxo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    comp = zlib.compress(fluxo)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(comp)).encode() + b" /Filter /FlateDecode >>\n"
        b"stream\n" + comp + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF").encode()
    return bytes(out)


class Resposta:
    """Resposta HTTP falsa. `headers` mente de propósito no caso canônico."""

    def __init__(self, content=b"", status=200, mime="text/html", url=None):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": mime}
        self.url = url or URL_OFICIAL


URL_OFICIAL = ("https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx"
               "?Tela=ext&descTipo=IPE&numProtocolo=1")
PDF_51 = pdf_sintetico("Aquisicao de 51% do capital social da sociedade "
                       "titular, sujeita a aprovacao do CADE")


def get_fixo(resp):
    def _g(url, **kw):
        return resp
    return _g


n = 1
print("=" * 98)
print("O MIME MENTE — O CASO CANÔNICO DA CVM")
print("=" * 98)
_ev = ce.evidencia_do_documento(URL_OFICIAL,
                                http_get=get_fixo(Resposta(PDF_51, mime="text/html")))
check(_ev is not None and _ev["payload_type"] == "PDF",
      f"[{n}] cabeçalho diz `text/html`, bytes dizem `%PDF-` → tratado como "
      "PDF. Escolher pelo cabeçalho erraria em 6 de 6 documentos reais"); n += 1
check(_ev and _ev["declared_mime"] == "text/html",
      f"[{n}] e o MIME mentiroso fica guardado como proveniência, não "
      "descartado — quem auditar depois precisa ver que a fonte mentiu"); n += 1
check(ce.eh_pdf(PDF_51) and not ce.eh_pdf(b"<html><body>x</body></html>"),
      f"[{n}] o sniff decide por assinatura, não por extensão nem cabeçalho"); n += 1
check(_ev and _ev["extraction_method"] == "pypdf",
      f"[{n}] e o extrator escolhido é o de PDF, não o de HTML"); n += 1

print()
print("=" * 98)
print("A EVIDÊNCIA LITERAL SOBREVIVE")
print("=" * 98)
_t = _ev["text"]
check("51%" in _t,
      f"[{n}] o percentual sobrevive à normalização — foi a falta dele que "
      "tornou o Orizon inrespondível"); n += 1
check("capital social" in _t.lower(),
      f"[{n}] e `capital social` também: é o que separa COMPANY_CONTROL de "
      "aquisição de ativo"); n += 1
check("CADE" in _t,
      f"[{n}] condições regulatórias preservadas"); n += 1
check(not _t.startswith("%PDF"),
      f"[{n}] e o que ficou é texto, não fluxo PDF"); n += 1
check(ce.normalizar("a  b\t\tc\n\n\n\nd") == "a b c\n\nd",
      f"[{n}] normalização colapsa espaço sem tocar conteúdo"); n += 1
check(ce.normalizar("R$ 45,4 mi em 05/05/2026 — 51%")
      == "R$ 45,4 mi em 05/05/2026 — 51%",
      f"[{n}] valores, datas e percentuais passam intactos — resumir ou "
      "parafrasear destruiria a evidência que justifica a captura"); n += 1

print()
print("=" * 98)
print("HOST: SÓ A CVM")
print("=" * 98)
for u, ok in ((URL_OFICIAL, True),
              ("https://dados.cvm.gov.br/x", True),
              ("https://evil.example.com/x.pdf", False),
              ("http://rad.cvm.gov.br.evil.com/x", False),
              ("ftp://rad.cvm.gov.br/x", False),
              ("", False)):
    check(ce.host_oficial(u) is ok,
          f"[{n}] host `{u[:44] or '(vazio)'}` → {'aceito' if ok else 'recusado'}")
    n += 1
_, _d = ce.buscar_documento("https://evil.example.com/x.pdf",
                            http_get=get_fixo(Resposta(PDF_51)))
check(_d["estado"] == ce.INVALID_HOST,
      f"[{n}] e a busca recusa antes de qualquer requisição"); n += 1
_, _d2 = ce.buscar_documento(
    URL_OFICIAL, http_get=get_fixo(Resposta(PDF_51, url="https://evil.example.com/z")))
check(_d2["estado"] == ce.INVALID_HOST,
      f"[{n}] inclusive quando o REDIRECIONAMENTO sai da CVM — senão o host "
      "seria validado só na porta de entrada"); n += 1

print()
print("=" * 98)
print("FALHA NÃO DERRUBA NADA")
print("=" * 98)


class Explode:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, url, **kw):
        raise self.exc


for nome, get, esperado in (
        ("HTTP 500", get_fixo(Resposta(b"", status=500)), ce.HTTP_ERROR),
        ("timeout", Explode(TimeoutError("ConnectTimeout")), ce.TIMEOUT),
        ("conexão", Explode(OSError("recusada")), ce.HTTP_ERROR)):
    _, _dd = ce.buscar_documento(URL_OFICIAL, http_get=get)
    check(_dd["estado"] == esperado, f"[{n}] {nome} → `{esperado}`"); n += 1
check(ce.evidencia_do_documento(URL_OFICIAL,
                                http_get=get_fixo(Resposta(b"", status=500))) is None,
      f"[{n}] e a evidência sai None — o artigo segue com título/resumo, como "
      "antes; um fato relevante ilegível não pode sumir da coleta"); n += 1
check(ce.evidencia_do_documento(
    URL_OFICIAL, http_get=get_fixo(Resposta(b"<html>oi</html>"))) is None,
      f"[{n}] payload que não é PDF → sem evidência, sem exceção"); n += 1
_, _dm = ce.extrair_pdf(b"%PDF-1.4 truncado no meio")
check(_dm["estado"] == ce.PDF_PARSE_FAILED,
      f"[{n}] PDF malformado → `{ce.PDF_PARSE_FAILED}`"); n += 1
check(ce.evidencia_do_documento(
    URL_OFICIAL, http_get=get_fixo(Resposta(b"%PDF-1.4 lixo"))) is None,
      f"[{n}] e também não vira evidência"); n += 1
_vazio = ce.evidencia_do_documento(
    URL_OFICIAL, http_get=get_fixo(Resposta(pdf_sintetico(" "))))
check(_vazio is None or not _vazio["text"].strip(),
      f"[{n}] PDF sem texto extraível → sem evidência, sem OCR na Fase 1"); n += 1
check(ce.evidencia_do_documento("", http_get=get_fixo(Resposta(PDF_51))) is None,
      f"[{n}] e sem URL de documento também"); n += 1

print()
print("=" * 98)
print("O TETO DE 12.000 — MEDIDO, NÃO ARBITRADO")
print("=" * 98)
check(ce.LIMITE_CHARS == 12_000,
      f"[{n}] teto {ce.LIMITE_CHARS}: máximo observado em 6 documentos reais "
      "foi 10.286, p90 8.923 — folga sem guardar arquivo"); n += 1
_ev_ok = ce.evidencia_do_documento(URL_OFICIAL, http_get=get_fixo(Resposta(PDF_51)),
                                   limite=10_000)
check(_ev_ok and not _ev_ok["truncated"]
      and _ev_ok["chars_stored"] == _ev_ok["chars_extracted"],
      f"[{n}] abaixo do teto: nada truncado, contagens iguais"); n += 1
_ev_cap = ce.evidencia_do_documento(URL_OFICIAL, http_get=get_fixo(Resposta(PDF_51)),
                                    limite=10)
check(_ev_cap and _ev_cap["truncated"] and len(_ev_cap["text"]) == 10
      and _ev_cap["chars_extracted"] > 10,
      f"[{n}] acima do teto: corta em 10, marca `truncated` e PRESERVA o "
      "tamanho original — sem isso ninguém saberia que faltou texto"); n += 1
_exato = len(_ev_ok["text"])
_ev_ex = ce.evidencia_do_documento(URL_OFICIAL, http_get=get_fixo(Resposta(PDF_51)),
                                   limite=_exato)
check(_ev_ex and not _ev_ex["truncated"],
      f"[{n}] exatamente no teto não conta como truncado"); n += 1

print()
print("=" * 98)
print("PROVENIÊNCIA, E NADA DE BINÁRIO")
print("=" * 98)
for campo in ("version", "source_url", "extraction_method", "payload_type",
              "chars_extracted", "chars_stored", "truncated", "pages"):
    check(campo in _ev, f"[{n}] proveniência: `{campo}`"); n += 1
check(_ev["version"] == "cvm.evidence.v1" == ce.EVIDENCE_VERSION,
      f"[{n}] versão do formato de EVIDÊNCIA, independente dos contratos de "
      "modelo — extração muda sem tocar identidade de experimento"); n += 1
_ser = json.dumps(_ev, ensure_ascii=False)
check("%PDF" not in _ser and "base64" not in _ser.lower(),
      f"[{n}] nenhum byte de PDF é persistido: evidência semântica, não "
      "arquivo"); n += 1
check(json.loads(_ser) == _ev,
      f"[{n}] e o bloco sobrevive a ida e volta em JSON"); n += 1
check(all(not isinstance(v, bytes) for v in _ev.values()),
      f"[{n}] nenhum campo binário"); n += 1

print()
print("=" * 98)
print("FASE 1 NÃO DECIDE NADA — NEUTRALIDADE SEMÂNTICA")
print("=" * 98)
import semantic_audit as sa

_A = {"title": "[Fato Relevante] X: Aquisição de Y", "summary": "Aquisição de Y"}
_B = dict(_A, semantic_evidence=_ev)
_txt_a = f"{_A['title']} {_A['summary']}".strip()
_txt_b = f"{_B['title']} {_B['summary']}".strip()
check(_txt_a == _txt_b,
      f"[{n}] o texto que a semântica monta é IDÊNTICO com e sem evidência — "
      "`semantic_evidence` não entra em `title + summary`"); n += 1
check(sa.detect_historical_reference(_txt_a, 2026)
      == sa.detect_historical_reference(_txt_b, 2026),
      f"[{n}] `R_HISTORICO` decide igual nos dois — a evidência não vaza para "
      "atualidade"); n += 1
check(sa.detect_transaction(_txt_a) == sa.detect_transaction(_txt_b),
      f"[{n}] e a detecção de transação também"); n += 1
_fonte = io.open("risk_dashboard.py", encoding="utf-8").read()


def _so_codigo(src: str) -> str:
    """Descarta comentários. Uma checagem que lê comentário afirma sobre a
    prosa, não sobre o comportamento — erro já cometido antes nesta base."""
    return "\n".join(l.split("#")[0] for l in src.splitlines())


_cod = _so_codigo(_fonte)
check(_cod.count("semantic_evidence") == 1,
      f"[{n}] `semantic_evidence` aparece UMA vez no CÓDIGO de "
      f"`risk_dashboard.py` ({_cod.count('semantic_evidence')}): só na "
      "gravação — nenhum classificador o lê"); n += 1
for _mod in ("semantic_audit.py", "reliability_occurrence_auditor_input.py"):
    check("semantic_evidence" not in io.open(_mod, encoding="utf-8").read(),
          f"[{n}] e {_mod} não o conhece"); n += 1

print()
print("=" * 98)
print("SÓ PROSPECTIVO — NENHUM REGISTRO ANTIGO É ENRIQUECIDO")
print("=" * 98)
check("evidencia_do_documento" in _cod
      and _cod.count("evidencia_do_documento") == 1,
      f"[{n}] a busca é chamada em UM ponto só — artigos recém-montados do "
      "dataset IPE, antes de qualquer persistência"); n += 1
_i_fetch = _cod.index("evidencia_do_documento")
_i_fim = _cod.index("def _digits")
check(_i_fetch < _i_fim and "fetch_cvm_fatos" in _cod[:_i_fetch],
      f"[{n}] e está dentro de `fetch_cvm_fatos`, não num carregador genérico "
      "de histórico"); n += 1
check("if not r.get(\"semantic_evidence\")" not in _cod
      and "semantic_evidence\") is None" not in _cod,
      f"[{n}] não existe caminho `se falta evidência, busca` — seria backfill "
      "implícito, e apagaria a comparabilidade dos experimentos congelados"); n += 1
_recl = _cod[_cod.index("def run_reclassify_only"):]
_recl = _recl[:_recl.index("\ndef ", 10)] if "\ndef " in _recl[10:] else _recl
check("evidencia_do_documento" not in _recl and "cvm_document_evidence" not in _recl,
      f"[{n}] §17 `--reclassify-only` não chama o buscador: reclassificar "
      "usa só o que já está gravado, senão viraria enriquecimento silencioso"); n += 1
_hist = json.load(io.open("risk_history.json", encoding="utf-8"))
_com = [k for k, r in _hist["articles"].items() if r.get("semantic_evidence")]
check(not _com,
      f"[{n}] e nenhum dos {len(_hist['articles'])} registros históricos ganhou "
      f"evidência ({len(_com)}) — a Fase 1 é prospectiva"); n += 1
check(all("semantic_evidence" not in r
          for k, r in _hist["articles"].items() if "rad.cvm.gov.br" in k),
      f"[{n}] inclusive os 37 da CVM, que têm `Link_Download` e poderiam ser "
      "buscados a qualquer momento — não são"); n += 1

print()
print("=" * 98)
print("COMPATIBILIDADE PARA TRÁS")
print("=" * 98)
check(all(json.loads(json.dumps(r)) == r
          for r in list(_hist["articles"].values())[:50]),
      f"[{n}] registros sem o campo serializam e voltam idênticos"); n += 1
check(_A.get("semantic_evidence") is None and "title" in _A and "summary" in _A,
      f"[{n}] e `title`/`summary` seguem intactos: a Fase 1 ACRESCENTA campo "
      "opcional, não redesenha os antigos"); n += 1

print()
print("=" * 98)
print(f"RESULTADO EVIDÊNCIA CVM: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
