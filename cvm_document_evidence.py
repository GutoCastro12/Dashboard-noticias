#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cvm_document_evidence.py — o fato relevante inteiro, não só o assunto.

O QUE ISTO CONSERTA

`fetch_cvm_fatos` monta cada registro a partir do CSV do dataset IPE:

    title   = "[Fato Relevante] {empresa}: {assunto}"
    summary = assunto

O resumo é o sufixo do título. Medido no corpus: 78% dos registros da CVM têm
evidência redundante e ZERO têm texto rico — a fonte de maior autoridade do
sistema é a de pior evidência, e a redundância é por construção, não acidente.

O custo disso foi medido em dois casos adjudicados. No Orizon, o input
congelado não dizia que a aquisição era de 51% do capital social; um modelo
inferiu além da evidência, o outro declarou `INSUFFICIENT_INPUT` — e estava
certo. A decisão humana precisou de evidência externa que estava, o tempo
todo, a uma requisição de distância: o `Link_Download` já vinha no CSV e o
documento nunca era buscado.

O MIME MENTE, E ISSO NÃO É DETALHE

`rad.cvm.gov.br` devolve `Content-Type: text/html` com corpo `%PDF-`. Medido em
6 de 6 documentos. Escolher o extrator pelo cabeçalho erraria em 100% dos
casos; escolher pelos bytes acerta em 100%. Por isso o sniffing é por
assinatura e o MIME declarado fica só como proveniência.

FASE 1 NÃO DECIDE NADA

A evidência é buscada, extraída, normalizada e persistida — e não é lida por
nenhum classificador. Nem escopo de M&A, nem atualidade, nem fase, nem papel,
nem `build_evolution`. Capturar e decidir são ondas separadas de propósito: se
a captura entrasse junto com o consumo, uma mudança de score não teria como ser
atribuída a uma das duas.

SÓ PROSPECTIVO

Não existe caminho "se falta evidência, busca". Enriquecer registro antigo ao
encontrá-lo de novo seria backfill implícito, e destruiria a comparabilidade
dos experimentos congelados que dependem do input tal como era.
"""
from __future__ import annotations

import io
import re
import unicodedata

EVIDENCE_VERSION = "cvm.evidence.v1"

# Só documentos da própria CVM. Sem isto, o helper vira um buscador de URL
# arbitrária — e o alvo natural de um documento hostil seria justamente um link
# embutido no PDF. Nenhum link interno é seguido.
HOSTS_OFICIAIS = ("rad.cvm.gov.br", "www.rad.cvm.gov.br",
                  "dados.cvm.gov.br", "www.dados.cvm.gov.br",
                  "cvm.gov.br", "www.cvm.gov.br")

TIMEOUT_S = 45
MAX_REDIRECTS = 5

# 12.000 caracteres. Medido em 6 documentos reais: p50 8.917, p90 8.923, máximo
# 10.286 — nenhum truncado neste teto. Número maior guardaria arquivo sem
# ganho; menor cortaria o corpo de um fato relevante médio ao meio.
LIMITE_CHARS = 12_000

ASSINATURA_PDF = b"%PDF-"

# Estados de diagnóstico. Toda falha é nomeada: "não deu certo" não permite
# distinguir documento inacessível de documento sem texto extraível, e as duas
# pedem ações diferentes.
FETCH_OK = "FETCH_OK"
HTTP_ERROR = "HTTP_ERROR"
TIMEOUT = "TIMEOUT"
INVALID_HOST = "INVALID_HOST"
DOCUMENT_URL_MISSING = "DOCUMENT_URL_MISSING"
UNSUPPORTED_PAYLOAD = "UNSUPPORTED_PAYLOAD"
PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
PDF_NO_EXTRACTABLE_TEXT = "PDF_NO_EXTRACTABLE_TEXT"
EMPTY_TEXT = "EMPTY_TEXT"


def host_oficial(url: str) -> bool:
    """Aceita apenas hosts da CVM, incluindo destino de redirecionamento."""
    if not url or not isinstance(url, str):
        return False
    m = re.match(r"^https?://([^/:?#]+)", url.strip(), re.I)
    if not m:
        return False
    return m.group(1).lower() in HOSTS_OFICIAIS


def eh_pdf(dados: bytes) -> bool:
    """Decide pelos BYTES, nunca pelo `Content-Type`.

    A CVM entrega PDF anunciando `text/html`. Confiar no cabeçalho faria o
    extrator de HTML rodar sobre fluxo PDF comprimido e devolver lixo — que foi
    exatamente o resultado antes desta função existir."""
    return bool(dados) and dados[:5] == ASSINATURA_PDF


def normalizar(texto: str) -> str:
    """Limpeza conservadora. O objetivo é evidência LITERAL.

    Preserva percentuais, valores, datas, nomes e a linguagem jurídica —
    remover "boilerplate" por parecer repetitivo apagaria justamente as
    condições precedentes e as menções ao CADE, que são o conteúdo material."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFC", texto)
    t = t.replace("\x00", " ")
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def extrair_pdf(dados: bytes) -> tuple:
    """Extrai texto página a página, em memória.

    Uma página ilegível não descarta o documento: fatos relevantes trazem o
    material nas primeiras páginas e anexos escaneados no fim, e perder tudo
    por causa do anexo seria o pior dos dois mundos."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:                       # pragma: no cover
        return "", {"estado": PDF_PARSE_FAILED, "erro": f"pypdf ausente: {exc}"}
    try:
        leitor = PdfReader(io.BytesIO(dados))
        paginas = len(leitor.pages)
    except Exception as exc:
        return "", {"estado": PDF_PARSE_FAILED, "erro": str(exc)[:200],
                    "paginas": 0, "paginas_com_texto": 0}
    partes, com_texto = [], 0
    for pag in leitor.pages:
        try:
            t = pag.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            com_texto += 1
        partes.append(t)
    bruto = "\n".join(partes)
    diag = {"paginas": paginas, "paginas_com_texto": com_texto}
    if not bruto.strip():
        # PDF só-imagem. Sem OCR na Fase 1 — cai para título/resumo.
        diag["estado"] = PDF_NO_EXTRACTABLE_TEXT
        return "", diag
    diag["estado"] = FETCH_OK
    return bruto, diag


def buscar_documento(url: str, *, http_get=None) -> tuple:
    """Busca o documento oficial. `http_get` existe para o teste não depender
    da CVM estar no ar — CI que precisa de rede externa é CI que falha por
    motivo alheio ao código."""
    if not url:
        return b"", {"estado": DOCUMENT_URL_MISSING}
    if not host_oficial(url):
        return b"", {"estado": INVALID_HOST, "url": url[:120]}
    if http_get is None:                              # pragma: no cover
        import requests
        http_get = requests.get
    try:
        r = http_get(url, timeout=TIMEOUT_S, allow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        nome = type(exc).__name__
        estado = TIMEOUT if "Timeout" in nome else HTTP_ERROR
        return b"", {"estado": estado, "erro": f"{nome}: {str(exc)[:120]}"}
    status = getattr(r, "status_code", 0)
    if status != 200:
        return b"", {"estado": HTTP_ERROR, "http_status": status}
    final = getattr(r, "url", url) or url
    if not host_oficial(final):
        # Redirecionamento para fora da CVM: recusa em vez de seguir.
        return b"", {"estado": INVALID_HOST, "url_final": str(final)[:120]}
    return (getattr(r, "content", b"") or b""), {
        "estado": FETCH_OK, "http_status": status,
        "mime_declarado": (getattr(r, "headers", {}) or {}).get("content-type", ""),
        "url_final": str(final)}


def evidencia_do_documento(url: str, *, http_get=None,
                           limite: int = LIMITE_CHARS) -> dict | None:
    """Devolve o bloco `semantic_evidence` ou None quando não há evidência.

    Devolver None em vez de um bloco vazio é deliberado: um campo presente e
    vazio seria indistinguível, mais tarde, de um documento genuinamente sem
    texto — e o chamador só deve gravar o que de fato obteve."""
    dados, diag = buscar_documento(url, http_get=http_get)
    if diag["estado"] != FETCH_OK or not dados:
        return None
    if not eh_pdf(dados):
        return None
    bruto, dpdf = extrair_pdf(dados)
    if dpdf["estado"] != FETCH_OK:
        return None
    texto = normalizar(bruto)
    if not texto:
        return None
    truncado = len(texto) > limite
    return {
        "version": EVIDENCE_VERSION,
        "text": texto[:limite],
        "source_url": diag.get("url_final") or url,
        "extraction_method": "pypdf",
        "payload_type": "PDF",
        "declared_mime": diag.get("mime_declarado", ""),
        "chars_extracted": len(texto),
        "chars_stored": min(len(texto), limite),
        "truncated": truncado,
        "pages": dpdf.get("paginas", 0),
        "pages_with_text": dpdf.get("paginas_com_texto", 0),
    }
