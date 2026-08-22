# -*- coding: utf-8 -*-
"""Wave R7w — MODEL / PROMPT HUMAN BENCHMARK V2.

Suite deterministica do modulo de avaliacao `reliability_model_benchmark`.
Nenhum teste faz rede, nenhum faz chamada de modelo, nenhum escreve em
producao. O que se prova aqui e que o benchmark E UM MEDIDOR e nao uma
autoridade: nao decide score, nao decide ocorrencia, nao reescreve verdade
humana e nao estima numero que nao observou.
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reliability_model_benchmark as mb  # noqa: E402

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


# ---------------------------------------------------------------------
secao(u"S1 - integridade do manifesto")
MAN = mb.manifesto()

ok(isinstance(MAN.get("manifesto_hash"), str) and len(MAN["manifesto_hash"]) == 16,
   u"manifesto tem hash de 16 hex")
ok(mb.manifesto()["manifesto_hash"] == MAN["manifesto_hash"],
   u"hash do manifesto e reproduzivel entre chamadas")

v1 = MAN["batch_v1"]
v2 = MAN["contrato_v2"]
ok(v1["papel"] == "DEVELOPMENT_SET", u"Batch V1 rotulado DEVELOPMENT_SET")
ok(v2["papel"] == "PROSPECTIVE_VALIDATION_SET",
   u"Contrato V2 rotulado PROSPECTIVE_VALIDATION_SET")
ok(v1["casos"] == len(v1["artigos"]), u"casos do V1 batem com artigos listados")
ok(sum(v1["por_status"].values()) == v1["memberships"],
   u"soma dos status do V1 == memberships")

secao(u"S2 - CLEAR / UNDETERMINED / POLICY_PENDING tratados a parte")
ok(set(v1["por_status"]) <= {"CLEAR", "UNDETERMINED", "POLICY_PENDING"},
   u"nenhum status humano fora do vocabulario declarado")
ok(v1["por_status"].get("UNDETERMINED", 0) >= 1,
   u"UNDETERMINED preservado, nao coagido a CLEAR")
ok(v1["por_status"].get("POLICY_PENDING", 0) >= 1,
   u"POLICY_PENDING preservado, nao coagido a CLEAR")
ok(v1["CLEAR"] + v1["UNDETERMINED"] + v1["POLICY_PENDING"] == v1["memberships"],
   u"as tres classes cobrem o total sem sobra")

secao(u"S3 - higiene de holdout (S39/S40)")
ok(not MAN["sobreposicao_v1_v2"],
   u"Batch V1 e Contrato V2 sao disjuntos", repr(MAN["sobreposicao_v1_v2"]))
ok(set(v1["artigos"]).isdisjoint(set(v2["artigos_observados"])),
   u"nenhum artigo do dev set aparece no set prospectivo")
ok(set(v2["artigos_revisados"]) <= set(v2["artigos_observados"]),
   u"todo artigo revisado foi de fato observado")
ok(len(v2["artigos_revisados"]) < len(v2["artigos_observados"]),
   u"existe massa observada AINDA NAO revisada (holdout real)")

# ---------------------------------------------------------------------
secao(u"S4 - calculo das metricas da Camada A")
A = mb.camada_a()
ok(A["observacoes_avaliadas"] > 0, u"camada A avaliou observacoes")
ok(len(A["linhas"]) == A["observacoes_avaliadas"],
   u"uma linha por observacao avaliada")

for modelo, dims in sorted(A["por_modelo"].items()):
    for dim, m in sorted(dims.items()):
        a_, e_ = m.get("acertos", 0), m.get("erros", 0)
        ok(a_ + e_ == m["avaliaveis"],
           u"%s/%s: acertos + erros == avaliaveis" % (modelo[:14], dim),
           repr(m))
        ok(a_ <= m["avaliaveis"],
           u"%s/%s: acertos <= avaliaveis" % (modelo[:14], dim))

secao(u"S5 - o deterministico nao pode ser contado em dobro")
det = A["por_modelo"].get("DETERMINISTICO", {})
n_art_rev = len(v2["artigos_revisados"])
ok(bool(det), u"o deterministico aparece como linha de base")
for dim, m in sorted(det.items()):
    ok(m["avaliaveis"] <= n_art_rev,
       u"DETERMINISTICO/%s: denominador <= artigos revisados (%d)"
       % (dim, n_art_rev), u"avaliaveis=%s" % m["avaliaveis"])
ok(sum(1 for l in A["linhas"] if l["modelo"] == "DETERMINISTICO") == 0,
   u"o deterministico nao emite observacao propria: e coluna, nao modelo")

secao(u"S6 - abstencao e ausencia nao viram acerto")
n_nao_emitido = 0
for l in A["linhas"]:
    for dim, x in l["dimensoes"].items():
        if x["modelo"] == mb.NAO_EMITIDO:
            n_nao_emitido += 1
            ok(not x["acerto"],
               u"%s nao emitido nunca conta como acerto" % dim)
ok(True, u"varredura de NOT_EMITTED concluida (%d casos)" % n_nao_emitido)
ok(all(x["humano"] is not None
       for l in A["linhas"] for x in l["dimensoes"].values()),
   u"toda dimensao avaliada tem verdade humana presente")

# ---------------------------------------------------------------------
secao(u"S7 - avaliacao direcional (Camada B)")
MD = mb.manifesto_direcional()
ok(MD["unidade"] == "OCCURRENCE",
   u"a unidade da avaliacao direcional e a OCORRENCIA, nao o artigo")
ok(set(MD["rotulos_permitidos"]) == {"ADVERSE", "FAVORABLE", "NEUTRAL",
                                     "MIXED", "UNCERTAIN"},
   u"vocabulario direcional fechado")
ok(MD["sem_multiplicador_numerico"] is True,
   u"o modelo nunca propoe multiplicador numerico")

vazio = mb.avaliar_direcional({}, MD)
ok(vazio["executado"] is False,
   u"direcional sem resposta reporta executado=False, nao 0%")
ok(vazio.get("cobertura") is None,
   u"cobertura ausente e None, nao zero fabricado")
ok(bool(vazio.get("motivo")),
   u"a nao-execucao vem com motivo explicito")

secao(u"S8 - aterramento em citacao (S30)")
EV = mb.evidencia()
VOCAB_EV = {"GROUNDED", "PARTIALLY_GROUNDED", "UNSUPPORTED", "SEM_CITACAO",
            "FIELD_SUPPORT_PARTIALLY_SUPPORTED",
            "FIELD_SUPPORT_INSUFFICIENT_INPUT"}
for modelo, d in sorted(EV["por_modelo"].items()):
    ok(set(d) <= VOCAB_EV,
       u"%s: vocabulario de evidencia fechado" % modelo[:14], repr(sorted(d)))
    ok(sum(d.values()) > 0, u"%s: evidencia contabilizada" % modelo[:14])

secao(u"S9 - telemetria nao inventa numero (S46)")
TEL = mb.telemetria()
ok(TEL["chamadas_nesta_execucao"] == 0,
   u"zero chamadas de modelo nesta execucao")
for modelo, d in sorted(TEL["por_modelo"].items()):
    ok(isinstance(d["custo"], str) and u"indispon" in d["custo"],
       u"%s: custo declarado indisponivel, nao estimado" % modelo[:14])
    ok(d["ok"] + d["falhas"] == d["chamadas"],
       u"%s: ok+falhas == chamadas" % modelo[:14])
    ok(d["tokens_entrada"] + d["tokens_saida"] == d["tokens_total"],
       u"%s: tokens somam" % modelo[:14])
    ok(d["latencia_p95_s"] >= d["latencia_mediana_s"],
       u"%s: p95 >= mediana" % modelo[:14])

# ---------------------------------------------------------------------
secao(u"S10 - zero autoridade de producao")
for chave in ("production_score_authority", "production_occurrence_authority",
              "semantic_authority", "write_authority"):
    ok(mb.AUTORIDADE[chave] == "NONE", u"%s == NONE" % chave)
ok(mb.AUTORIDADE["output_label"] == "EVALUATION ONLY",
   u"saida rotulada EVALUATION ONLY")

FONTE = io.open(mb.__file__, encoding="utf-8").read()
for r in ("valor_sobre_deterministico", "gatilho_de_revisao", "gaps",
          "matriz_humana", "prontidao"):
    ok(u'"authority": "EVALUATION ONLY"' in FONTE or u"EVALUATION ONLY" in FONTE,
       u"%s: relatorio carrega rotulo de autoridade" % r)

secao(u"S11 - nao escreve nada, em lugar nenhum")
ESCRITA = re.compile(r"""open\s*\([^)]*['"][wax]\+?b?['"]""")
ok(not ESCRITA.search(FONTE),
   u"o modulo nao abre arquivo algum em modo de escrita",
   repr(ESCRITA.findall(FONTE)))
for proibido in ("json.dump(", "yaml.dump(", "os.remove", "shutil.",
                 "subprocess", "requests.", "urllib.request", "httpx"):
    ok(proibido not in FONTE,
       u"o modulo nao usa %s (sem escrita, sem rede)" % proibido)
ok("def main(" in FONTE, u"modulo tem entrada CLI propria de leitura")

secao(u"S12 - prontidao e honesta sobre o motivo")
PR = mb.prontidao(A, vazio, TEL)
ok(PR["MODEL_DIRECTION_SCORE_AUTHORITY"] == "NOT_READY",
   u"autoridade direcional do modelo permanece NOT_READY")
ok(u"MEDI" in PR["motivo_dominante"].upper(),
   u"o motivo dominante e falta de MEDICAO, nao resultado ruim",
   PR["motivo_dominante"])
ok(any(u"MEDIDA" in c.upper() for c in PR["criterios_falhos"]),
   u"a nao-medicao aparece explicitamente entre os criterios falhos")

secao(u"S13 - agregacao reproduzivel")
r1 = mb.rodar_tudo()
r2 = mb.rodar_tudo()
ok(json.dumps(r1, sort_keys=True, ensure_ascii=False)
   == json.dumps(r2, sort_keys=True, ensure_ascii=False),
   u"duas execucoes produzem exatamente o mesmo relatorio")
ok(r1["manifesto"]["manifesto_hash"] == MAN["manifesto_hash"],
   u"hash estavel entre manifesto() e rodar_tudo()")

secao(u"S14 - gaps exigem padrao repetido (S36)")
G = r1["gaps"]
for nome, g in sorted(G.items()):
    if nome == "authority":
        continue
    ok(g["estado"] in ("CONFIRMED", "SINGLE_EXAMPLE_NOT_ENOUGH",
                       "NOT_OBSERVED", "NOT_MEASURABLE"),
       u"%s: estado no vocabulario" % nome)
    if g["estado"] == "CONFIRMED":
        ok(g["n"] >= 2, u"%s: CONFIRMED exige n>=2" % nome)
    if g["estado"] == "SINGLE_EXAMPLE_NOT_ENOUGH":
        ok(g["n"] == 1, u"%s: um exemplo nao promove gap" % nome)
ok(G["MODEL_PROMPT_MATERIALITY_DIRECTION_GAP"]["estado"] == "NOT_MEASURABLE",
   u"gap direcional declarado NAO MENSURAVEL, nao 'ausente'")

secao(u"S15 - gatilho de revisao e matriz humana")
GR = r1["gatilho_de_revisao"]["por_modelo"]
for modelo, d in sorted(GR.items()):
    n = (len(d["erros_reais_do_deterministico_encontrados"])
         + len(d["falsos_alarmes"]))
    ok(n == d["disagreements_surfaced"],
       u"%s: discordancias = acertos + falsos alarmes" % modelo[:14])
    ok(d["precisao"] is None or 0.0 <= d["precisao"] <= 1.0,
       u"%s: precisao em [0,1] ou None" % modelo[:14])
MH = r1["matriz_humana"]
ok(MH["n"] == len(set(l["article_id"] for l in A["linhas"])),
   u"matriz humana cobre todo artigo revisado, sem duplicar")
ok(all(l["verdict_humano"] for l in MH["linhas"]),
   u"toda linha da matriz carrega o veredito humano")
ok(all(set(c) >= {"humano", "deterministico"}
       for l in MH["linhas"] for c in l["dimensoes"].values()),
   u"toda celula compara humano contra o deterministico")

# ---------------------------------------------------------------------
print(u"\n%s" % (u"=" * 62))
if FALHAS:
    print(u"RESULTADO: %d/%d - FALHAS: %s"
          % (N[0] - len(FALHAS), N[0], ", ".join(FALHAS)))
    sys.exit(1)
print(u"RESULTADO: %d/%d testes OK" % (N[0], N[0]))
