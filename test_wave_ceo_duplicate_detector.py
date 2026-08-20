#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_ceo_duplicate_detector.py — a duplicata do CEO aparece sozinha.

O CASO

O painel mostrava duas trocas de CEO no Santander. Houve uma. A terceira
matéria — análise da XP sobre a transição já anunciada — abriu ocorrência nova
porque estava a 67 dias da anterior e não tinha marcador que a reunisse.

Ninguém descobriu isso por teste. Descobriu-se olhando o painel. Este arquivo
existe para que a próxima duplicata apareça no relatório antes de alguém
precisar olhar.

O QUE ELE PROTEGE

Os quatro controles adjudicados, nos dois sentidos: Santander, Tupy e Yura
DEVEM ser sinalizadas; Hapvida NÃO PODE ser. A Hapvida é a proteção contra a
regra preguiçosa "mesma empresa + mesma família + datas próximas = duplicata" —
ela teve duas trocas reais em quatro meses.

E protege o que o detector NÃO faz: nenhuma ocorrência muda, nenhum score muda,
nenhuma família fora de `troca_ceo` é tocada. Detector que corrige em silêncio
é pior que duplicata visível.

POR QUE NÃO HÁ ASSERÇÃO DE PRAZO

Medido: o acompanhamento da Tupy vem 70 dias depois da ocorrência anterior, e a
segunda troca REAL da Hapvida vem 100 dias depois da primeira. Distâncias
parecidas, significados opostos. Um teste que fixasse janela estaria gravando
uma regra que a própria medição reprovou.
"""
from __future__ import annotations

import io
import json
import time

import reliability_ceo_duplicate_detector as det
import risk_dashboard as rd
import semantic_audit as sa

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
ALIAS = det._aliases_conhecidos(cfg)
H = json.load(io.open("risk_history.json", encoding="utf-8"))
SRC = io.open("reliability_ceo_duplicate_detector.py", encoding="utf-8").read()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


R = det.gerar()
POR_EMPRESA = {c["company"]: c for c in R["candidatos"]}


def ocorrencias(emp, fam):
    its = []
    for u, a in H["articles"].items():
        if fam not in ((a.get("events_by_company") or {}).get(emp) or []):
            continue
        t = a.get("title") or ""
        its.append({"u": u, "event_id": fam, "pub_ts": a.get("pub_ts"), "title": t,
                    "_ident": rd.occurrence_identity(t, fam, emp, AL.get(emp))})
    its.sort(key=lambda x: x["pub_ts"])
    rd.assign_occurrence_clusters(its, 45, None, AL)
    g = {}
    for o in its:
        g.setdefault(o["_occ_key"], []).append(o["u"])
    return sorted(tuple(sorted(v)) for v in g.values())


print("=" * 98)
print("§28 EXTRAÇÃO DE PESSOA — GRAMÁTICA, NÃO 'PALAVRA CAPITALIZADA'")
print("=" * 98)
_e = det.extrai_pessoas("Gilson Finkelsztain será o novo CEO do Santander", ALIAS)
check(_e["incoming_person"] == "gilson finkelsztain",
      f"[1] PT quem entra: 'X será o novo CEO' ({_e['incoming_person']!r})")
check(_e["outgoing_person"] == "", "[2] e quem sai fica desconhecido, não inventado")
_s = det.extrai_pessoas("Crise na Hapvida derruba Jorge Pinheiro do comando; "
                        "novo CEO é anunciado", ALIAS)
check(_s["outgoing_person"] == "jorge pinheiro",
      f"[3] PT quem sai: 'derruba X do comando' ({_s['outgoing_person']!r})")
_es = det.extrai_pessoas("Gonzalo Rueda Castillo es el nuevo gerente general "
                         "de Cemento Yura", ALIAS)
check(_es["incoming_person"] == "gonzalo rueda castillo",
      f"[4] ES 'X es el nuevo gerente general' ({_es['incoming_person']!r}) — "
      "sem isso a metade entrante da Yura se perde")
_es2 = det.extrai_pessoas("Cementos Yura, del Grupo Gloria, anuncia la salida "
                          "de Juan Carlos Burga como gerente general", ALIAS)
check(_es2["outgoing_person"] == "juan carlos burga",
      f"[5] ES 'salida de X' ({_es2['outgoing_person']!r})")
check(det.extrai_pessoas("Hapvida anuncia troca no comando e nomeia CFO como "
                         "novo CEO", ALIAS)["incoming_person"] == "",
      "[6] §12 cargo NÃO é pessoa: 'nomeia CFO como novo CEO' não extrai ninguém")
check(det.extrai_pessoas("EXCLUSIVO: Rumo escolhe Rockenbach, “ferroviário raiz”, "
                         "como novo CEO", ALIAS)["incoming_person"] == "",
      "[7] sobrenome de um token só é rejeitado — perder o caso é melhor que "
      "ancorar uma transição em nome ambíguo")
check(det.extrai_pessoas("Cemig (CMIG4) elege novo CEO", ALIAS)["incoming_person"] == ""
      and det.extrai_pessoas("[Fato Relevante] Vamos: Eleição de novo CEO",
                             ALIAS)["incoming_person"] == "",
      "[8] título neutro continua neutro — ausência não vira identidade")
check(det.normaliza_pessoa("Sr. José da Silva Júnior") == "jose da silva junior",
      f"[9] normalização: honorífico fora, acento resolvido, nome inteiro "
      f"preservado ({det.normaliza_pessoa('Sr. José da Silva Júnior')!r})")
check(det.normaliza_pessoa("Finkelsztain") == "",
      "[10] e um token só nunca vira identidade de pessoa")
check(det.normaliza_pessoa("Novo CEO") == "",
      "[11] nem uma sequência que só contém cargo")

print()
print("=" * 98)
print("§8 LINGUAGEM DE ACOMPANHAMENTO")
print("=" * 98)
check(det.eh_seguimento("XP vê troca de CEO no Santander (SANB11) sem ruptura "
                        "e aposta em alta rentabilidade"),
      "[12] 'vê troca de CEO' é acompanhamento")
check(det.eh_seguimento("“Tupy do futuro”: novo CEO faz giro por unidades e "
                        "reconhece solução para falta de mão de obra"),
      "[13] 'novo CEO faz giro' é atividade pós-transição")
check(det.eh_seguimento("Santander Brasil reforça continuidade estratégica com "
                        "troca de CEO e mira ROE acima de 20% até 2028"),
      "[14] 'continuidade estratégica com troca' é acompanhamento")
check(not det.eh_seguimento("Gilson Finkelsztain será o novo CEO do Santander"),
      "[15] um ANÚNCIO não é acompanhamento")
check(not det.eh_seguimento("Tupy (TUPY3) escolhe Harro Burmann como novo CEO"),
      "[16] nem a escolha do sucessor")
_seg = [a.get("title") for a in H["articles"].values()
        if det.eh_seguimento(a.get("title") or "")
        and any("troca_ceo" in (v or [])
                for v in (a.get("events_by_company") or {}).values())]
# A auditoria read-only contava 9. Ao promover para produção, dois padrões
# foram deliberadamente descartados por serem genéricos demais para significar
# acompanhamento de troca de CEO: "vai viajar" e "o que ... dizem sobre".
# Estreitar aqui é a direção segura — perder um aviso custa menos que sinalizar
# uma transição real como duplicata.
# 4I.2 R7n: a contagem caiu porque o problema foi resolvido A MONTANTE. A
# guarda de assercao (`R_TROCA_CEO_SEM_ASSERCAO`, `91f863e`) tirou o
# `troca_ceo` dos artigos de acompanhamento, e o alinhamento de producao
# gravou isso no historico. Artigo que nao e mais evento de CEO nao pode abrir
# ocorrencia duplicada — e o filtro acima exige `troca_ceo` em
# `events_by_company`. A assercao passa a ser sobre a DIRECAO: o numero nao
# pode voltar a subir sem que alguem explique por que.
check(len(_seg) <= 4,
      f"[17] no corpus, no maximo 4 artigos de `troca_ceo` ainda casam "
      f"acompanhamento ({len(_seg)}) — eram 7 antes da guarda de assercao")

print()
print("=" * 98)
print("§14/§15/§16 OS TRÊS POSITIVOS CONGELADOS")
print("=" * 98)
# Santander e Tupy ORIGINARAM este detector. A duplicata deixou de existir: a
# analise da XP e o giro do CEO da Tupy nao sao mais eventos de troca de CEO,
# por verdade humana (lote V1, casos 20 e 07) ja gravada no historico.
# Sinaliza-los agora exigiria reintroduzir o falso positivo — entao o contrato
# INVERTE, e passa a exigir que NAO aparecam, pelo motivo certo.
_sa = POR_EMPRESA.get("Santander Brasil")
check(_sa is None,
      "[18] Santander NAO e mais sinalizado — a duplicata sumiu na origem")
_sa_arts = [a for a in H["articles"].values()
            if "troca_ceo" in ((a.get("events_by_company") or {})
                               .get("Santander Brasil") or [])]
check(len(_sa_arts) >= 1,
      f"[19] o Santander mantem seus artigos LEGITIMOS de CEO ({len(_sa_arts)})")
check(any("será o novo CEO" in (a.get("title") or "") for a in _sa_arts),
      "[20] inclusive o anuncio de Gilson Finkelsztain, que e a ocorrencia real")
check(not any("XP vê troca de CEO" in (a.get("title") or "") for a in _sa_arts),
      "[21] e o comentario da XP saiu — nao e mais evento de CEO")

_tu = POR_EMPRESA.get("Tupy")
check(_tu is None, "[22] Tupy tambem nao e mais sinalizada — mesmo motivo")
_tu_arts = [a for a in H["articles"].values()
            if "troca_ceo" in ((a.get("events_by_company") or {})
                               .get("Tupy") or [])]
check(any("Harro Burmann" in (a.get("title") or "") for a in _tu_arts),
      f"[23] a sucessao real da Tupy segue inteira ({len(_tu_arts)} artigos)")
check(not any("faz giro" in (a.get("title") or "") for a in _tu_arts),
      "[24] e o giro por unidades saiu — nao e mais evento de CEO")

_yu = POR_EMPRESA.get("Yura")
check(_yu is not None, "[25] Yura SINALIZADA")
check(_yu and _yu["reasons"] == ["COMPLEMENTARY_OUTGOING_INCOMING"],
      f"[26] e pelo motivo que a torna especial: papéis complementares, não "
      f"pessoa repetida ({_yu['reasons'] if _yu else None})")
check(_yu and _yu["occurrence_a"]["outgoing_persons"] == ["juan carlos burga"]
      and _yu["occurrence_b"]["incoming_persons"] == ["gonzalo rueda castillo"],
      "[27] um lado dá quem sai, o outro quem entra — e não há palavra em comum")
check(_yu and _yu["dias_entre_ocorrencias"] > 120,
      f"[28] separadas por {_yu['dias_entre_ocorrencias'] if _yu else 0} dias: "
      "nenhuma janela temporal razoável as uniria")

print()
print("=" * 98)
print("§17 HAPVIDA — O NEGATIVO DURO")
print("=" * 98)
check("Hapvida" not in POR_EMPRESA,
      "[29] Hapvida NÃO é sinalizada como duplicata")
_hap = det._ocorrencias(H, cfg, "Hapvida")
check(len(_hap) == 2, f"[30] ela tem 2 ocorrências, e são 2 trocas reais ({len(_hap)})")
check(not all(o["seguimento"] for o in _hap[1]),
      "[31] a segunda contém um ANÚNCIO, não só comentário — é exatamente isso "
      "que a protege da fusão")
check(det._razoes(_hap[0], _hap[1]) == [],
      f"[32] e o detector não produz nenhum motivo para ela "
      f"({det._razoes(_hap[0], _hap[1])})")
_dias_hap = int((min(o["pub_ts"] for o in _hap[1])
                 - max(o["pub_ts"] for o in _hap[0])) / 86400)
_dias_yu = _yu["dias_entre_ocorrencias"] if _yu else 0
# 4I.2 R7n: a Tupy deixou de ser duplicata, entao o par de distancias passa a
# usar a Yura — que continua sendo duplicata confirmada. O argumento e o mesmo
# e segue valendo: PRAZO NAO DISTINGUE duplicata de transicao real.
check(_dias_yu > _dias_hap,
      f"[33] §10 a Yura (duplicata, {_dias_yu}d) esta MAIS distante que a "
      f"Hapvida (transicoes distintas, {_dias_hap}d) — prazo nao distingue, e "
      "nenhuma janela foi implementada")
check("45" not in SRC.split("_ocorrencias")[1].split("def _identidade")[0]
      or "assign_occurrence_clusters(its, 45" in SRC,
      "[34] o único 45 do módulo é o gap do resolvedor de produção, não uma "
      "janela de duplicata inventada")

print()
print("=" * 98)
print("§18/§19 AMBEV E B3 — CONSERVADORES")
print("=" * 98)
check("Ambev" not in POR_EMPRESA,
      "[35] Ambev não é sinalizada — evidência insuficiente permanece "
      "insuficiente, e o detector não inventa verdade")
check("B3" not in POR_EMPRESA,
      "[36] B3 não é sinalizada — o defeito dela é atribuição a montante "
      "(roundup de mercado e o CEO da Gol), não duplicata")
# "Sem hardcode" quer dizer sem nome em LÓGICA. Comentários que explicam por
# que uma regra existe — "Yura: um lado diz quem sai, o outro quem entra" — são
# documentação, e apagá-los para satisfazer a checagem tornaria o código pior.
# Então a verificação remove docstrings e comentários e olha o que executa.
import re as _re
_SEM_DOC = _re.sub(r'"""(?:.|\n)*?"""', " ", SRC)
_CODIGO = "\n".join(l.split("#")[0] for l in _SEM_DOC.splitlines())
_n = 37
for nome in ("Santander", "Tupy", "Yura", "Hapvida", "Ambev", "Gilson",
             "Finkelsztain", "Burmann", "Burga", "Castillo", "Pinheiro"):
    check(nome not in _CODIGO,
          f"[{_n}] §2 nenhum hardcode de `{nome}` fora da docstring")
    _n += 1
print()
print("=" * 98)
print("§29 CONJUNTO DE CANDIDATOS NO CORPUS INTEIRO")
print("=" * 98)
# Santander e Tupy sairam porque a duplicata deixou de existir na origem.
# Sobra a Yura, cuja duplicata e de PAPEIS COMPLEMENTARES e nao depende de
# linguagem de acompanhamento — por isso a correcao de assercao nao a alcanca.
check(sorted(POR_EMPRESA) == ["Yura"],
      f"[{_n}] resta 1 duplicata confirmada ({sorted(POR_EMPRESA)}) — eram 3 "
      f"antes da guarda de assercao")
_n += 1
check(R["empresas_avaliadas"] >= 19,
      f"[{_n}] as empresas com `troca_ceo` seguem avaliadas "
      f"({R['empresas_avaliadas']})")
_n += 1
check(R["ocorrencias_avaliadas"] >= 23,
      f"[{_n}] e as ocorrencias tambem ({R['ocorrencias_avaliadas']})")
_n += 1
check(all(c["classificacao"] == "suspected_duplicate" for c in R["candidatos"]),
      f"[{_n}] §31 todos são SUSPEITA, nenhum se declara duplicata confirmada")
_n += 1
check(all(c["autoridade"] == "warning_only" for c in R["candidatos"])
      and R["autoridade"] == "warning_only",
      f"[{_n}] e a autoridade declarada é somente-aviso")
_n += 1
check(all(c["revisado"] is False for c in R["candidatos"]),
      f"[{_n}] nascem não revisados — nenhum veredito humano é escrito")
_n += 1

print()
print("=" * 98)
print("§13/§40 SEM AUTORIDADE — NADA MUDA")
print("=" * 98)
for proibido in ("_occ_key\"] =", "_occ_key'] =", "events_by_company\"] =",
                 "build_evolution", "total_score", "json.dump(H", "risk_history.json\", \"w\""):
    check(proibido not in SRC,
          f"[{_n}] o módulo não contém `{proibido}` — sem caminho de mutação")
    _n += 1
check("def " in SRC and "merge" not in SRC.lower().split('"""', 2)[2],
      f"[{_n}] §40 não existe caminho de código que funda nada quando sinaliza")
_n += 1

_antes = {(e, f): ocorrencias(e, f)
          for e in ("Santander Brasil", "Hapvida", "Tupy", "Yura")
          for f in ("troca_ceo",)}
det.gerar()
_depois = {(e, f): ocorrencias(e, f)
           for e in ("Santander Brasil", "Hapvida", "Tupy", "Yura")
           for f in ("troca_ceo",)}
check(_antes == _depois,
      f"[{_n}] rodar o detector não altera nenhuma ocorrência de `troca_ceo`")
_n += 1
check(ocorrencias("Smart Fit", "ma") == [tuple(sorted(
          u for u, a in H["articles"].items()
          if "ma" in ((a.get("events_by_company") or {}).get("Smart Fit") or [])))],
      f"[{_n}] §34 Smart Fit segue em 1 ocorrência de M&A")
_n += 1
check(len(ocorrencias("EQT Corporation", "ma")) == 2,
      f"[{_n}] §34 EQT segue em 2")
_n += 1
check(len(ocorrencias("BRF", "ma")) == 1, f"[{_n}] §34 BRF segue em 1")
_n += 1
check(len(ocorrencias("Tok&Stok", "recuperacao_judicial")) == 1,
      f"[{_n}] §33 Tok&Stok segue com 1 ocorrência de RJ")
_n += 1
_fams = set()
for a in H["articles"].values():
    for evs in (a.get("events_by_company") or {}).values():
        _fams |= set(evs or [])
check(det.FAMILIA == "troca_ceo" and len(_fams) > 5,
      f"[{_n}] §5 o escopo é uma família só, entre as {len(_fams)} existentes")
_n += 1

print()
print("=" * 98)
print("§41 DETERMINISMO E FORMA DO ARTEFATO")
print("=" * 98)
_r2 = det.gerar()
check(json.dumps(R, sort_keys=True) == json.dumps(_r2, sort_keys=True),
      f"[{_n}] mesma entrada, saída idêntica — nenhuma ordenação instável")
_n += 1
check([c["company"] for c in R["candidatos"]]
      == sorted(c["company"] for c in R["candidatos"]),
      f"[{_n}] candidatos em ordem determinística")
_n += 1
check(R["schema_version"] == 1 and R["detector_version"] == "ceo.dup.v1",
      f"[{_n}] §20 artefato versionado ({R['detector_version']})")
_n += 1
for campo in ("company", "event_id", "reasons", "occurrence_a", "occurrence_b",
              "classificacao", "autoridade", "revisado", "pergunta_de_revisao"):
    check(all(campo in c for c in R["candidatos"]),
          f"[{_n}] §20/§25 todo candidato traz `{campo}`")
    _n += 1
check(all(art.get("titulo") and art.get("data")
          for c in R["candidatos"] for occ in (c["occurrence_a"], c["occurrence_b"])
          for art in occ["artigos"]),
      f"[{_n}] §25 e traz título e data de cada artigo — contexto suficiente "
      "para um auditor decidir sem abrir o histórico")
_n += 1
_md = det.markdown(R)
check("Suspeitas de duplicata" in _md and "somente aviso" in _md,
      f"[{_n}] §22 o resumo legível existe e se declara aviso")
_n += 1
check("juan carlos burga" in _md and "gonzalo rueda castillo" in _md,
      f"[{_n}] §22 e mostra as pessoas detectadas, nao JSON cru")
_n += 1
check(len(_md) < 12000,
      f"[{_n}] §22 o resumo é conciso ({len(_md)} chars), não um despejo")
_n += 1

print()
print("=" * 98)
print(f"RESULTADO DETECTOR DE DUPLICATA DE CEO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
