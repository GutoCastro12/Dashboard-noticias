#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reliability_ceo_duplicate_detector.py — suspeita de troca de CEO contada duas vezes.

POR QUE ESTE MÓDULO EXISTE

O painel mostrava duas trocas de CEO no Santander. Houve uma: Mario Leão saiu,
Gilson Finkelsztain entrou. A terceira matéria — análise da XP sobre a transição
já anunciada — abriu ocorrência nova porque estava a 67 dias da anterior, acima
do gap de 45, e não tinha nenhum marcador que a reunisse ao anúncio.

Isso não foi descoberto por teste nem por alarme. Foi descoberto porque um
humano olhou o painel. Enquanto a segurança depender disso, cada duplicata
custa o tempo de alguém e fica em produção até ser notada — a do Santander
ficou dois meses, e vale 3,7 pontos contra 0,9 do evento verdadeiro, porque o
decaimento favorece a cópia recente.

Este módulo faz a triagem que faltava: aponta pares de ocorrências da mesma
empresa que provavelmente são a mesma transição, com o motivo explícito.

O QUE ELE NÃO FAZ

Nada. Literalmente: não funde ocorrência, não altera `_occ_key`, não toca
score, âncora, bônus de fonte, histórico, classificação ou dashboard. Ele lê e
descreve. A palavra do relatório é SUSPEITA, nunca duplicata confirmada — quem
confirma é gente, depois.

POR QUE SÓ `troca_ceo`

É a única família onde a precisão foi medida: 3 duplicatas confirmadas
encontradas, 3 de 3, e zero falso positivo no conjunto adjudicado. O heurístico
anterior (marcador vazio) achava 1 de 3, marcava a Hapvida por engano e perdia
Tupy e Yura. Generalizar antes de medir foi o erro que esta família já evitou
uma vez.

POR QUE NÃO EXISTE JANELA DE TEMPO

Medido no corpus: o acompanhamento da Tupy vem 98 dias depois do anúncio, e a
SEGUNDA troca real da Hapvida vem 100 dias depois da primeira. Duas distâncias
praticamente iguais, significados opostos. Qualquer regra de prazo confundiria
as duas. O que separa é o papel das pessoas e a linguagem, nunca o calendário.
O tempo entra no relatório como informação, jamais como critério.

O CASO QUE OBRIGA A OLHAR PAPEL, E NÃO SEMELHANÇA

Na Yura, um artigo anuncia a saída de Juan Carlos Burga e outro, 138 dias
depois, a entrada de Gonzalo Rueda Castillo. São os dois lados da MESMA
transição, e não compartilham uma única palavra em comum. Nenhuma comparação
por texto os une; o que os une é um lado dar quem sai e o outro quem entra,
sem conflito entre eles.

E O CASO QUE OBRIGA A NÃO FUNDIR

A Hapvida teve duas trocas de comando reais em quatro meses. A ocorrência
posterior contém um ANÚNCIO, não só comentário — e é por isso que ela não é
sinalizada. Uma ocorrência que anuncia transição é transição.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from pathlib import Path

import risk_dashboard as rd
import semantic_audit as sa

DETECTOR_VERSION = "ceo.dup.v1"
SCHEMA_VERSION = 1
FAMILIA = "troca_ceo"
AUTORIDADE = "warning_only"

# ── Vocabulário fechado ──────────────────────────────────────────────────────
# Cargo não é pessoa. "nomeia CFO como novo CEO" não nomeia ninguém, e tratar
# `CFO` como nome criaria identidade a partir de um crachá.
_NAO_PESSOA = {
    "ceo", "cfo", "coo", "cto", "cio", "presidente", "presidenta", "diretor",
    "diretora", "diretoria", "conselho", "comando", "cargo", "gerente",
    "gerencia", "general", "chief", "executive", "officer", "chairman",
    "board", "company", "grupo", "banco", "holding", "novo", "nova", "new",
    "nuevo", "nueva", "sucessao", "sucessor", "renuncia", "saida", "salida",
    "troca", "mudanca", "mudancas", "eleicao", "eleito", "nomeacao", "anuncia",
    "anuncio", "names", "named", "fato", "relevante", "exclusivo", "conheca",
    "the", "and", "for", "its", "como", "que", "dos", "das", "sem", "com",
    "por", "para", "veja", "confira", "qual", "vai", "sob", "apos",
}
_CAPW = r"[A-ZÁÉÍÓÚÂÊÔÃÕÜÑÇ][\wÀ-ÿ\-']*"
_NOME = rf"(?:{_CAPW}(?:\s+(?:d[aeo]s?\s+|del\s+|de\s+la\s+)?{_CAPW}){{0,3}})"

# Gramática de QUEM ENTRA. Exige contexto sintático de cargo — nunca "qualquer
# palavra capitalizada", que foi medido como 14% de falso-positivo noutra onda.
_ENTRA = [
    rf"({_NOME})\s+ser[áa]\s+o?\s*nov[oa]\s+(?:ceo|presidente|diretor)",
    rf"({_NOME})\s+[ée]\s+escolhid[oa]\s+como\s+nov[oa]\s+(?:ceo|presidente)",
    # PT "é o novo" e ES "es el nuevo": o corpus tem emissores peruanos e
    # chilenos, e só a forma portuguesa perderia a metade entrante da Yura.
    rf"({_NOME})\s+(?:[ée]s?|es)\s+(?:o|el)?\s*(?:nov[oa]|nuev[oa])\s+"
    rf"(?:ceo|gerente\s+general|presidente|director\s+general)",
    rf"({_NOME})\s+asume\s+(?:como\s+)?(?:la\s+)?(?:gerencia|ceo|direcci[óo]n)",
    rf"nombrad[oa]\s+({_NOME})",
    rf"escolhe\s+({_NOME})[^,]*,?\s*(?:[^,]*,\s*)?como\s+nov[oa]\s+(?:ceo|presidente)",
    rf"nomeia\s+({_NOME})\s+como\s+nov[oa]\s+(?:ceo|presidente)",
    rf"designa\s+a\s+({_NOME})\s+como\s+(?:gerente\s+general|ceo|presidente)",
    rf"elege\s+({_NOME})\s+como\s+nov[oa]\s+(?:ceo|presidente)",
    rf"conhe[çc]a\s+({_NOME}),\s*nov[oa]\s+(?:ceo|presidente)",
    rf"({_NOME}),\s*nov[oa]\s+(?:ceo|presidente|gerente\s+general)",
    rf"names?\s+({_NOME})\s+(?:as\s+)?(?:new\s+)?(?:ceo|chief\s+executive)",
    rf"({_NOME})\s+(?:to\s+become|named|appointed)\s+(?:as\s+)?(?:new\s+)?ceo",
    rf"appoints?\s+({_NOME})\s+as\s+(?:new\s+)?ceo",
    rf"({_NOME})\s+succeeds\s+",
]
# Gramática de QUEM SAI.
_SAI = [
    rf"sa[íi]da\s+de\s+({_NOME})",
    rf"salida\s+de\s+({_NOME})",
    rf"derruba\s+({_NOME})\s+d[oe]\s+comando",
    rf"({_NOME})\s+deixa(?:r[áa])?\s+(?:o\s+)?(?:comando|cargo|ceo)",
    rf"({_NOME})\s+deja\s+la\s+gerencia",
    rf"({_NOME})\s+renuncia\b",
    rf"ren[úu]ncia\s+de\s+({_NOME})",
    rf"({_NOME})\s+steps?\s+down",
    rf"succeeds\s+({_NOME})",
    rf"substitui\s+({_NOME})",
    rf"no\s+lugar\s+de\s+({_NOME})",
    rf"replaces?\s+({_NOME})",
]
# Linguagem de ACOMPANHAMENTO: fala de uma troca já conhecida, não anuncia
# outra. Medido: 9 artigos no corpus, 9 acertos, nenhum falso positivo.
_SEGUIMENTO = [
    r"\bv[êe]\s+(?:a\s+)?troca\s+de\s+(?:ceo|comando)",
    r"\bap[óo]s\s+(?:a\s+)?troca\s+de\s+(?:ceo|comando)",
    r"\bcom\s+(?:a\s+)?troca\s+de\s+(?:ceo|comando)",
    r"troca\s+de\s+ceo\s+d[ae]\s+.{0,40}\bsurpreende",
    r"\brepercuss[ãa]o\s+d[ao]\s+troca",
    r"\bimpacto\s+d[ao]\s+troca",
    r"\bcontinuidade\s+(?:estrat[ée]gica\s+)?com\s+troca",
    r"nov[oa]\s+ceo\s+(?:j[áa]\s+)?(?:faz|tra[çc]a|tenta|vai|prepara|inicia)",
    r"como\s+o\s+nov[oa]\s+ceo",
    r"sob\s+press[ãa]o,\s*nov[oa]\s+ceo",
    r"incerteza\s+com\s+troca\s+de\s+ceo",
]
_RX_ENTRA = [(re.compile(p, re.I), p) for p in _ENTRA]
_RX_SAI = [(re.compile(p, re.I), p) for p in _SAI]
_RX_SEG = [re.compile(p, re.I) for p in _SEGUIMENTO]


def _aliases_conhecidos(cfg):
    out = set()
    for emp, als in (sa._aliases_map(cfg) or {}).items():
        for a in list(als or []) + [emp]:
            n = rd.normalize(a)
            if n:
                out.add(n)
    return out


def normaliza_pessoa(bruto: str, aliases: set | None = None) -> str:
    """Nome de pessoa como UMA identidade, não como marcadores soltos.

    Exige ao menos dois tokens significativos: um sobrenome sozinho
    ('Rockenbach') é ambíguo demais para ancorar uma transição, e preferimos
    perder o caso a inventá-lo."""
    s = unicodedata.normalize("NFKD", bruto or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\"'“”‘’.,;:()\[\]]", " ", s)
    s = re.sub(r"\b(sr|sra|dr|dra|mr|mrs|ms)\b\.?", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip().lower()
    toks = [t for t in s.split() if t and t not in _NAO_PESSOA]
    if aliases:
        toks = [t for t in toks if t not in aliases]
    return " ".join(toks) if len(toks) >= 2 else ""


def extrai_pessoas(titulo: str, aliases: set | None = None) -> dict:
    """Papéis de pessoa a partir do título. Cada lado pode ser vazio, e vazio
    quer dizer DESCONHECIDO — nunca 'pessoa diferente'."""
    t = titulo or ""
    entra = sai = ""
    provas = []
    for rx, p in _RX_ENTRA:
        m = rx.search(t)
        if m:
            n = normaliza_pessoa(m.group(1), aliases)
            if n:
                entra = n
                provas.append({"papel": "incoming_person", "regra": "explicit_new_ceo",
                               "trecho": m.group(0)[:80]})
                break
    for rx, p in _RX_SAI:
        m = rx.search(t)
        if m:
            n = normaliza_pessoa(m.group(1), aliases)
            if n and n != entra:
                sai = n
                provas.append({"papel": "outgoing_person", "regra": "explicit_leaves",
                               "trecho": m.group(0)[:80]})
                break
    return {"incoming_person": entra, "outgoing_person": sai, "evidencia": provas}


def eh_seguimento(titulo: str) -> bool:
    """Título fala de uma troca já conhecida em vez de anunciar outra."""
    return any(rx.search(titulo or "") for rx in _RX_SEG)


def _ocorrencias(historico, cfg, empresa):
    """Reconstrói as ocorrências pelo resolvedor de PRODUÇÃO, em cópias
    próprias. Nada aqui é devolvido ao histórico nem ao pipeline."""
    al = sa._aliases_map(cfg)
    alias_glob = _aliases_conhecidos(cfg)
    its = []
    for u, a in (historico.get("articles") or {}).items():
        if FAMILIA not in ((a.get("events_by_company") or {}).get(empresa) or []):
            continue
        t = a.get("title") or ""
        pes = extrai_pessoas(t, alias_glob)
        its.append({"u": u, "event_id": FAMILIA, "pub_ts": a.get("pub_ts") or 0,
                    "title": t, "fonte": a.get("source") or "",
                    "_ident": rd.occurrence_identity(t, FAMILIA, empresa, al.get(empresa)),
                    "pessoas": pes, "seguimento": eh_seguimento(t)})
    its.sort(key=lambda x: (x["pub_ts"], x["u"]))
    rd.assign_occurrence_clusters(its, 45, None, al)
    grupos = {}
    for o in its:
        grupos.setdefault(o["_occ_key"], []).append(o)
    return [grupos[k] for k in sorted(grupos, key=lambda k: (
        min(o["pub_ts"] for o in grupos[k]), k))]


def _identidade(membros):
    return ({o["pessoas"]["incoming_person"] for o in membros
             if o["pessoas"]["incoming_person"]},
            {o["pessoas"]["outgoing_person"] for o in membros
             if o["pessoas"]["outgoing_person"]})


def _razoes(a, b):
    """Motivos estruturados pelos quais A e B parecem a MESMA transição.

    Lista vazia = não sinalizar. Nenhum motivo é opaco ou numérico: cada um
    aponta a evidência que o produziu."""
    ea, sa_ = _identidade(a)
    eb, sb = _identidade(b)
    out = []
    if ea and eb and (ea & eb):
        out.append("SAME_INCOMING_PERSON")
    if sa_ and sb and (sa_ & sb):
        out.append("SAME_OUTGOING_PERSON")
    if ea and eb and not (ea & eb):
        return []          # entrantes distintos: são transições diferentes
    if out:
        out.append("ROLE_COMPATIBLE_PERSON_IDENTITY")
        return out
    # Yura: um lado diz quem sai, o outro quem entra, e nada os contradiz.
    if (sa_ and eb and not ea and not sb) or (ea and sb and not eb and not sa_):
        return ["COMPLEMENTARY_OUTGOING_INCOMING"]
    # Santander/Tupy: a ocorrência posterior é INTEIRA de acompanhamento e a
    # anterior tem identidade de pessoa. Exigir que TODOS os artigos sejam de
    # acompanhamento é o que protege a Hapvida, cuja segunda ocorrência contém
    # um anúncio de verdade.
    if (ea or sa_) and not (eb or sb) and all(o["seguimento"] for o in b):
        return ["LATER_OCCURRENCE_ALL_FOLLOW_UP",
                "FOLLOW_UP_WITH_NO_NEW_PERSON_CONFLICT"]
    return []


def _iso(ts):
    import time
    return time.strftime("%Y-%m-%d", time.gmtime(ts or 0))


def _resumo_occ(membros, idx):
    ent, sai = _identidade(membros)
    return {
        "occurrence_ref": f"occ{idx}",
        "occurrence_key": membros[0]["_occ_key"],
        "n_artigos": len(membros),
        "primeira_data": _iso(min(o["pub_ts"] for o in membros)),
        "ultima_data": _iso(max(o["pub_ts"] for o in membros)),
        "incoming_persons": sorted(ent),
        "outgoing_persons": sorted(sai),
        "artigos": [{"data": _iso(o["pub_ts"]), "fonte": o["fonte"],
                     "titulo": o["title"], "seguimento": o["seguimento"],
                     "evidencia_pessoa": o["pessoas"]["evidencia"]}
                    for o in sorted(membros, key=lambda z: (z["pub_ts"], z["u"]))],
    }


def gerar(historico: str = "risk_history.json", config: str = "config_risco.yaml") -> dict:
    cfg = rd.load_config(config)
    H = json.load(io.open(historico, encoding="utf-8"))
    empresas = sorted({e for a in (H.get("articles") or {}).values()
                       for e, evs in (a.get("events_by_company") or {}).items()
                       if FAMILIA in (evs or [])})
    candidatos, n_occ = [], 0
    for emp in empresas:
        occs = _ocorrencias(H, cfg, emp)
        n_occ += len(occs)
        for i in range(len(occs)):
            for j in range(i + 1, len(occs)):
                razoes = _razoes(occs[i], occs[j])
                if not razoes:
                    continue
                candidatos.append({
                    "company": emp,
                    "event_id": FAMILIA,
                    "classificacao": "suspected_duplicate",
                    "autoridade": AUTORIDADE,
                    "revisado": False,
                    "reasons": razoes,
                    "dias_entre_ocorrencias": int(
                        (min(o["pub_ts"] for o in occs[j])
                         - max(o["pub_ts"] for o in occs[i])) / 86400),
                    "occurrence_a": _resumo_occ(occs[i], i),
                    "occurrence_b": _resumo_occ(occs[j], j),
                    "pergunta_de_revisao": (
                        f"As matérias de {emp} descrevem UMA troca de CEO ou "
                        f"trocas distintas?"),
                })
    candidatos.sort(key=lambda c: (c["company"], c["occurrence_a"]["occurrence_ref"],
                                   c["occurrence_b"]["occurrence_ref"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "detector_version": DETECTOR_VERSION,
        "event_id": FAMILIA,
        "autoridade": AUTORIDADE,
        "historico": historico,
        "empresas_avaliadas": len(empresas),
        "ocorrencias_avaliadas": n_occ,
        "candidatos": candidatos,
    }


def markdown(r: dict) -> str:
    L = ["## Suspeitas de duplicata de troca de CEO", ""]
    L.append(f"*{r['detector_version']} · somente aviso · não funde, não pontua, "
             f"não altera o painel.*")
    L.append("")
    L.append(f"**{len(r['candidatos'])} suspeita(s)** em "
             f"{r['ocorrencias_avaliadas']} ocorrências de "
             f"{r['empresas_avaliadas']} empresas.")
    if not r["candidatos"]:
        L.append("")
        L.append("Nenhuma ocorrência de `troca_ceo` parece repetida.")
        return "\n".join(L) + "\n"
    for c in r["candidatos"]:
        a, b = c["occurrence_a"], c["occurrence_b"]
        L.append("")
        L.append(f"### {c['company']}")
        pes = sorted(set(a["incoming_persons"] + b["incoming_persons"]))
        sai = sorted(set(a["outgoing_persons"] + b["outgoing_persons"]))
        L.append(f"- **A** {a['primeira_data']} → {a['ultima_data']} "
                 f"({a['n_artigos']} artigo(s))")
        L.append(f"- **B** {b['primeira_data']} → {b['ultima_data']} "
                 f"({b['n_artigos']} artigo(s)) · {c['dias_entre_ocorrencias']} dias depois")
        if pes:
            L.append(f"- entra: {', '.join(pes)}")
        if sai:
            L.append(f"- sai: {', '.join(sai)}")
        L.append(f"- motivo: {', '.join(c['reasons'])}")
        L.append(f"- {c['pergunta_de_revisao']}")
        for occ in (a, b):
            for art in occ["artigos"]:
                marca = " *(acompanhamento)*" if art["seguimento"] else ""
                L.append(f"  - {art['data']} · {art['fonte']} — {art['titulo']}{marca}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Detector de suspeita de duplicata em `troca_ceo` "
                    "(somente aviso; não altera nada).")
    p.add_argument("--historico", default="risk_history.json")
    p.add_argument("--config", default="config_risco.yaml")
    p.add_argument("--json-out", default=None)
    p.add_argument("--md-out", default=None)
    p.add_argument("--step-summary", action="store_true",
                   help="anexa o resumo ao GITHUB_STEP_SUMMARY, se existir")
    a = p.parse_args(argv)
    r = gerar(a.historico, a.config)
    md = markdown(r)
    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True))
        print(f"JSON  -> {a.json_out}")
    if a.md_out:
        Path(a.md_out).parent.mkdir(parents=True, exist_ok=True)
        io.open(a.md_out, "w", encoding="utf-8").write(md)
        print(f"MD    -> {a.md_out}")
    if a.step_summary:
        # Falha de destino do relatório não pode derrubar o build nem tocar
        # dado: o detector é aviso. A exceção de LÓGICA, essa, sobe.
        destino = __import__("os").environ.get("GITHUB_STEP_SUMMARY", "")
        if destino:
            try:
                with io.open(destino, "a", encoding="utf-8") as fh:
                    fh.write("\n" + md)
            except OSError as e:
                print(f"aviso: não consegui escrever no step summary ({e})")
    if not (a.json_out or a.md_out):
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
