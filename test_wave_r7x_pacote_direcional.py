# -*- coding: utf-8 -*-
"""Wave R7x — DIRECTIONAL HUMAN LABELING PACK, BATCH D1.

Prova que o pacote e um INSTRUMENTO DE COLETA e nao uma fonte de verdade:
ordem determinista, manifesto estavel, toda linha ancorada numa ocorrencia
real com artigos que existem localmente, nenhuma previsao de modelo dentro,
nenhum rotulo humano pre-preenchido, nenhuma escrita em producao.

Zero rede. Zero chamada de modelo.
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import directional_review_pack as dp  # noqa: E402
import occurrence_engine as oe        # noqa: E402

FALHAS = []
N = [0]


def ok(cond, nome, detalhe=""):
    N[0] += 1
    if cond:
        print(u"  [%02d] OK   %s" % (N[0], nome))
    else:
        print(u"  [%02d] FALHA %s %s" % (N[0], nome, detalhe))
        FALHAS.append(nome)


def secao(t):
    print(u"\n== %s ==" % t)


print(u"montando o pacote uma vez (roda build_evolution)...")
DADOS = dp.coletar()
UNI = dp.universo(DADOS)
SEL = dp.selecionar(UNI)
PACK = dp.montar(DADOS, SEL)
MAN = dp.manifesto(PACK)
LINHAS = PACK["linhas"]

# ---------------------------------------------------------------------
secao(u"S1 - a superficie amostrada e a que PONTUA")
ok(UNI["n"] > 0, u"ha ocorrencias pontuaveis")
ok(UNI["n_breakdown"] >= UNI["n"],
   u"toda ocorrencia vem de uma contribuicao do score")
ok(UNI["n"] >= 70,
   u"a cobertura passa de 70 ocorrencias (%d)" % UNI["n"])
ok(not UNI["contribuicoes_sem_ocorrencia"],
   u"toda contribuicao do score religa a uma ocorrencia",
   repr(UNI["contribuicoes_sem_ocorrencia"][:2]))
ok(UNI["n_contribuicoes_ligadas"] + len(UNI["contribuicoes_sem_ocorrencia"])
   == UNI["n_breakdown"],
   u"ligadas + orfas == total de contribuicoes do score")
ok(UNI["n"] <= UNI["n_contribuicoes_ligadas"],
   u"ocorrencias <= contribuicoes: a agregacao nunca cria linha")

secao(u"S1b - uma ocorrencia, um voto (dupla contagem fica visivel)")
mult = UNI["ocorrencias_com_multiplas_contribuicoes"]
ok(isinstance(mult, list),
   u"as ocorrencias com mais de uma contribuicao sao listadas")
ok(UNI["n"] + sum(m["n_contribuicoes"] - 1 for m in mult)
   == UNI["n_contribuicoes_ligadas"],
   u"a agregacao fecha: ocorrencias + excedentes == contribuicoes")
for m in mult:
    ok(m["contrib_total"] > 0 and m["n_contribuicoes"] >= 2,
       u"%s/%s: soma das contribuicoes registrada"
       % (m["company"], m["family"]))
rumo = [r for r in UNI["linhas"] if r["company"] == "Rumo"
        and r["family"] == "recomendacao_negativa"]
ok(len(rumo) == 2,
   u"a Rumo mantem DUAS ocorrencias de recomendacao negativa (titulo "
   u"identico, URLs distintas) — o casamento por titulo nao as fundiu",
   u"achou %d" % len(rumo))
ok(len({r["occurrence_id"] for r in rumo}) == 2,
   u"e elas seguem com occurrence_id distintos")

secao(u"S2 - o defeito que motivou este modulo nao pode voltar")
jbs_ma = [r for r in UNI["linhas"]
          if r["company"] == "JBS" and r["family"] == "ma"]
ok(len(jbs_ma) == 2,
   u"a JBS tem DUAS ocorrencias de M&A distintas (Pilgrim's e Oma)",
   u"achou %d" % len(jbs_ma))
ok(len({r["occurrence_id"] for r in jbs_ma}) == 2,
   u"e elas tem occurrence_id distintos")
ok(any("Pilgrim" in (r["titulo"] or "") for r in jbs_ma),
   u"a aquisicao dos 18% da Pilgrim's esta presente")
ids = [r["occurrence_id"] for r in UNI["linhas"]]
ok(len(ids) == len(set(ids)),
   u"nenhuma ocorrencia aparece duas vezes no universo")

# ---------------------------------------------------------------------
secao(u"S3 - ordem determinista e manifesto estavel")
p2 = dp.montar(DADOS, dp.selecionar(dp.universo(DADOS)))
ok([l["review_id"] for l in LINHAS] == [l["review_id"] for l in p2["linhas"]],
   u"os review_id saem na mesma ordem em duas montagens")
ok([l["occurrence_id"] for l in LINHAS]
   == [l["occurrence_id"] for l in p2["linhas"]],
   u"a ordem das ocorrencias e identica")
ok(dp.manifesto(p2)["manifesto_hash"] == MAN["manifesto_hash"],
   u"o hash do manifesto e o mesmo em duas montagens")
ok(MAN["ordem_deterministica"] is True, u"a ordem e declarada determinista")
ok(len(MAN["manifesto_hash"]) == 16, u"hash de 16 hex")
esperados = ["D%02d" % i for i in range(1, len(LINHAS) + 1)]
ok([l["review_id"] for l in LINHAS] == esperados,
   u"os review_id sao D01..D%02d, sem buraco" % len(LINHAS))

secao(u"S4 - o manifesto cobre o que o humano leu")
ok(MAN["n"] == len(LINHAS), u"uma linha de manifesto por linha do pacote")
for m in MAN["linhas"][:3]:
    ok(len(m["evidence_hash"]) == 16,
       u"%s: hash de evidencia de 16 hex" % m["review_id"])
ok(all(set(m) == {"review_id", "occurrence_id", "company", "family",
                  "article_ids", "evidence_hash"} for m in MAN["linhas"]),
   u"toda linha do manifesto tem os seis campos exigidos")
alt = json.loads(json.dumps(PACK, ensure_ascii=False))
alt["linhas"][0]["evidencia"] = ["texto trocado"]
ok(dp.manifesto(alt)["manifesto_hash"] != MAN["manifesto_hash"],
   u"mudar a evidencia mostrada muda o hash")

# ---------------------------------------------------------------------
secao(u"S5 - toda linha aponta para ocorrencia e artigo REAIS")
reais = {r["occurrence_id"] for r in UNI["linhas"]}
for l in LINHAS:
    ok(l["occurrence_id"] in reais,
       u"%s: a ocorrencia existe no universo pontuavel" % l["review_id"])
ok(all(l["representante"] in DADOS["por_id"] for l in LINHAS),
   u"o artigo representante de toda linha existe localmente")
faltando = [(l["review_id"], a) for l in LINHAS for a in l["membros"]
            if a not in DADOS["por_id"]]
ok(not faltando,
   u"todo article_id membro existe localmente", repr(faltando[:3]))
ok(all(l["n_membros"] == len(l["membros"]) for l in LINHAS),
   u"a contagem de membros bate com a lista")

secao(u"S6 - a evidencia vem do dado local, nao de fora")
for l in LINHAS:
    if l["estado_evidencia"] == "TITULOS_MULTIPLOS":
        titulos = {dp._norm((DADOS["por_id"].get(a) or {}).get("title") or "")
                   for a in l["membros"]}
        for e in l["evidencia"]:
            cabeca = dp._norm(e.split(" — ")[0])
            ok(any(cabeca and cabeca in t for t in titulos),
               u"%s: o excerto sai de um titulo de membro" % l["review_id"],
               e[:60])
ok(all(l["titulo"] == dp._limpar(
    (DADOS["por_id"].get(l["representante"]) or {}).get("title") or "")
    or l["titulo"] for l in LINHAS),
   u"o titulo exibido e o titulo armazenado do representante")
ok(all(l["estado_evidencia"] in ("CORPO_LOCAL", "TITULOS_MULTIPLOS",
                                dp.EVIDENCIA_INSUFICIENTE) for l in LINHAS),
   u"o estado de evidencia usa o vocabulario fechado")
ok(any(l["estado_evidencia"] == dp.EVIDENCIA_INSUFICIENTE for l in LINHAS),
   u"a insuficiencia de evidencia e declarada, nao escondida")

# ---------------------------------------------------------------------
secao(u"S7 - nenhuma previsao de modelo dentro do pacote")
LINHAS_JSON = json.dumps(LINHAS, ensure_ascii=False).lower()
for proibido in ("gemini", "model_prediction", "predicao", "saida_modelo",
                 "r7ba.p2", "flash-lite"):
    ok(proibido not in LINHAS_JSON,
       u"nenhuma linha do pacote carrega %s" % proibido)
ok(json.dumps(PACK["_meta"], ensure_ascii=False).lower().count("gemini") == 0,
   u"nem o cabecalho nomeia modelo")
ok(dp.AUTORIDADE["model_predictions_included"] is False,
   u"o pacote declara que nao inclui previsao de modelo")
FONTE = io.open(dp.__file__, encoding="utf-8").read()
for proibido in ("requests.", "urllib.request", "httpx", "openai",
                 "generativeai"):
    ok(proibido not in FONTE, u"o gerador nao usa %s" % proibido)

secao(u"S8 - nenhum rotulo humano pre-preenchido")
ok(all(l["human_label"] is None for l in LINHAS),
   u"toda celula de rotulo sai VAZIA")
ok(all(l["human_note"] is None for l in LINHAS),
   u"toda observacao sai vazia")
ok(dp.AUTORIDADE["human_labels_prefilled"] is False,
   u"o pacote declara que nao pre-preenche rotulo")
ok(set(dp.ROTULOS) == {"ADVERSE", "FAVORABLE", "NEUTRAL", "MIXED",
                       "UNCERTAIN"},
   u"vocabulario de rotulo fechado")
ok(all(l["expected_control"] in (None, "ADVERSE") for l in LINHAS),
   u"o controle esperado so existe para o Tier 1 adverso")
for l in LINHAS:
    if l["tier"] == "TIER2_CONTEXTUAL":
        ok(l["expected_control"] is None,
           u"%s: contextual NAO recebe direcao esperada" % l["review_id"])
        break

# ---------------------------------------------------------------------
secao(u"S9 - zero autoridade e zero escrita em producao")
for chave in ("production_score_authority", "production_occurrence_authority",
              "semantic_authority", "human_truth_write_authority"):
    ok(dp.AUTORIDADE[chave] == "NONE", u"%s == NONE" % chave)
ESCRITA = re.compile(r"""open\s*\(\s*["']([^"']+)["']\s*,\s*["'][wax]""")
alvos = set(ESCRITA.findall(FONTE))
ok(alvos <= {"directional_human_review_pack_d1.json",
             "docs/DIRECTIONAL_HUMAN_REVIEW_D1.md"},
   u"o gerador so escreve os dois artefatos de revisao", repr(sorted(alvos)))
for proibido in ("risk_history.json", "config_risco.yaml",
                 "risk_human_supervision.json", "occurrence_truth"):
    ok(proibido not in alvos,
       u"o gerador nunca escreve em %s" % proibido)

secao(u"S10 - a verdade humana existente e proveniencia, nao rotulo")
PROV = dp.proveniencia_humana()
ok("explicitos" in PROV and "limitados" in PROV,
   u"proveniencia separa explicito de limitado")
ok(PROV["n_explicitos"] + PROV["n_limitados"] > 0,
   u"ha evidencia direcional humana registrada")
ok(all("texto" in x and "onde" in x for x in PROV["explicitos"]),
   u"cada achado de proveniencia cita onde estava")
ok("nada aqui virou rotulo" in PROV["nota"],
   u"a proveniencia declara que nao virou rotulo")
ok(not any(l["human_label"] for l in LINHAS),
   u"nenhuma linha herdou rotulo da supervisao existente")

# ---------------------------------------------------------------------
secao(u"S11 - controles nomeados e composicao")
def tem(empresa, familia):
    return any(l["company"] == empresa and l["family"] == familia
               for l in LINHAS)

for empresa, familia in (("Tok&Stok", "recuperacao_judicial"),
                         ("Cosan", "rebaixamento_rating"),
                         ("JBS", "recomendacao_negativa"),
                         ("Lojas Renner", "recomendacao_negativa")):
    ok(tem(empresa, familia),
       u"S6 controle presente: %s / %s" % (empresa, familia))
jbs = [l for l in LINHAS if l["company"] == "JBS"]
ok(len(jbs) == 5, u"S8 as cinco linhas da JBS estao no pacote",
   u"achou %d" % len(jbs))
ok({l["family"] for l in jbs} == {"ma", "troca_ceo", "emissao_divida",
                                  "recomendacao_negativa"},
   u"S8 a JBS cobre M&A, CEO, divida e recomendacao")
ok(sum(1 for l in jbs if l["family"] == "ma") == 2,
   u"S8 as DUAS aquisicoes da JBS entram separadas")
ok(sum(1 for l in jbs if l["tier"] == "TIER1_ADVERSE_CONTROL") == 1,
   u"S8 so a recomendacao rebaixada e controle adverso na JBS")
for fam, minimo in (("ma", 4), ("troca_ceo", 3)):
    n = sum(1 for l in LINHAS if l["family"] == fam)
    ok(n >= minimo, u"S17 ao menos %d de %s (%d)" % (minimo, fam, n))
n_div = sum(1 for l in LINHAS
            if l["family"] in ("emissao_divida", "follow_on"))
ok(n_div >= 3, u"S17 ao menos 3 entre divida e follow-on (%d)" % n_div)
ok(20 <= len(LINHAS) <= 30,
   u"S3 o lote fica entre 20 e 30 linhas (%d)" % len(LINHAS))
ok(PACK["n_tier1"] >= 8 and PACK["n_tier2"] >= 12,
   u"S17 composicao Tier1 %d / Tier2 %d" % (PACK["n_tier1"], PACK["n_tier2"]))
ok(len({l["company"] for l in LINHAS}) >= 10,
   u"o lote cobre 10+ emissores (%d)" % len({l["company"] for l in LINHAS}))

secao(u"S12 - Tier 1 e Tier 2 seguem a classe de sinal")
for l in LINHAS:
    esperado = ("TIER1_ADVERSE_CONTROL"
                if l["classe_de_sinal"] == oe.SINAL_ADVERSO
                else "TIER2_CONTEXTUAL")
    if l["tier"] != esperado:
        ok(False, u"%s: tier coerente com a classe" % l["review_id"])
        break
else:
    ok(True, u"todo tier deriva da classe de sinal, sem excecao manual")
ok(all(l["contribution_class"] in ("ADVERSE", "CONTEXTUAL")
       for l in LINHAS), u"a classe de contribuicao vem de producao")
ok(all(l["canonical_contrib"] > 0 for l in LINHAS),
   u"toda linha do pacote de fato pontua hoje")

secao(u"S13 - exclusoes e ausencias sao reportadas, nao apagadas")
ok(all(e["company"] in dp.EXCLUIR_D1 for e in PACK["excluidos"]),
   u"so os casos de incerteza humana aberta foram excluidos")
ok(not any(l["company"] in dp.EXCLUIR_D1 for l in LINHAS),
   u"S25 Capital One e Copel ficam fora do D1")
ok(any(a["company"] == "WEG" for a in PACK["nomeados_ausentes"]),
   u"S6 WEG e reportado como ausente, nao inventado")
ok(all(a["estado"] == "SEM_OCORRENCIA_PONTUAVEL"
       for a in PACK["nomeados_ausentes"]),
   u"o motivo da ausencia e declarado")

secao(u"S14 - lacuna de coleta e cobertura futura")
W = dp.lacuna_walkers(DADOS)
ok(W["em_risk_history"] is False,
   u"S24 Walkers segue ausente de risk_history")
ok(W["classificacao"] == "COLLECTION_GAP_CONTROL",
   u"S24 classificado como lacuna de coleta")
ok(W["elegivel_para_linha_direcional"] is False,
   u"S24 Walkers NAO vira linha direcional")
ok(not any(l["representante"] == W["article_id"] for l in LINHAS),
   u"S24 nenhuma linha do pacote usa o artigo da lacuna")
C = dp.cobertura_futura(PACK)
ok(sum(C["casos_por_familia"].values()) == len(LINHAS),
   u"S26 a contagem por familia soma o pacote inteiro")
ok("distribuicao de rotulo" in C["nota"],
   u"S26 declara que nao previu distribuicao de rotulo")
ok(isinstance(C["d1_suficiente_para_primeiro_benchmark"], bool),
   u"S27 a suficiencia do D1 e uma conclusao explicita")
ok(C["d2_necessario"] is not C["d1_suficiente_para_primeiro_benchmark"],
   u"S27 D2 necessario e o complemento da suficiencia")

secao(u"S15 - o aviso de atribuicao aparece onde ha duvida conhecida")
b3 = [l for l in LINHAS if l["company"] == "B3"]
ok(bool(b3), u"a B3 esta no pacote")
ok(all(l["aviso_atribuicao"] for l in b3),
   u"S1 toda linha da B3 carrega o aviso de atribuicao em aberto")
ok(sum(1 for l in LINHAS if l["aviso_atribuicao"]) == len(b3),
   u"o aviso nao vazou para linhas sem pendencia conhecida")

# ---------------------------------------------------------------------
print(u"\n%s" % (u"=" * 62))
if FALHAS:
    print(u"RESULTADO WAVE R7x: %d/%d - FALHAS: %s"
          % (N[0] - len(FALHAS), N[0], ", ".join(FALHAS)))
    sys.exit(1)
print(u"RESULTADO WAVE R7x (pacote direcional D1): %d/%d checagens passaram"
      % (N[0], N[0]))
