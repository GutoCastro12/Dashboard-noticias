#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_auditor_input.py — o pacote do auditor precisa ser cego.

POR QUE ISSO É O TESTE QUE IMPORTA

Em produção, no momento da inferência, a verdade humana não existe. Um
construtor de insumo que a consulte funciona no teste e falha no dia seguinte —
e pior, a medição feita com ele é ficção, porque o modelo terá visto a resposta.

Então a suíte varre o `prompt_payload` serializado atrás de qualquer coisa que
só a adjudicação saberia: id de ocorrência humana, novidade adjudicada, fase,
âncora, relação DISTINCT, nome de revisor. A varredura é por substring, não por
chave — é isso que pega o vazamento escondido dentro de um texto livre.

E TESTA O AVALIADOR PELOS DOIS LADOS

Falso merge e falso split são erros opostos e ambos precisam ser detectáveis.
Fundir dois fatos reais apaga risco; separar um fato em dois infla. Uma suíte
que só verificasse o acerto não distinguiria um avaliador correto de um que diz
sempre a mesma coisa.

A Hapvida é o controle de falso merge: dizer que a troca de abril é a mesma de
dezembro TEM de pontuar como erro. O Santander é o de falso split: dizer que a
análise da XP é fato novo também.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_archival_source as arq
import reliability_occurrence_auditor_input as ai
import reliability_occurrence_truth as ot
import reliability_pilot_contract_v2 as v2

PASS = FAIL = 0
D = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))

# Referências resolvidas do histórico atual — nenhuma digitada de memória:
# elas vieram de `id_artigo` sobre a URL armazenada.
REFS = {
    "santander_0319": "88d41012b4ef62c3c3c3",
    "santander_0324": "6957d2d34de79a99466c",
    "santander_0530": "cad44d85917e8bb50e46",
    "tupy_0810": "9eb803c2493e648262ec",
    "yura_0720": "8c22ad696759a8820c0a",
    "smartfit_0804": "601562a812028d796edb",
    "brf_0918": "66a3cefcdc58b2a64470",
    "hapvida_0406": "54defbfc21b61d431ead",
}


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def pacote(empresa, familia, ref):
    return ai.construir_pacote(empresa, familia, ref, arq.HISTORICO)


print("=" * 98)
print("§18/§19 CONTRATOS E ENUMS")
print("=" * 98)
check(ai.INPUT_CONTRACT == "occurrence.auditor.input.v1",
      f"[1] contrato de entrada versionado ({ai.INPUT_CONTRACT})")
check(ai.OUTPUT_CONTRACT == "occurrence.auditor.output.v1",
      f"[2] e o de saída, separado ({ai.OUTPUT_CONTRACT})")
check("V3" not in ai.INPUT_CONTRACT and "V3" not in ai.OUTPUT_CONTRACT,
      "[3] nenhum dos dois se anuncia como Contract V3 — são envelope de "
      "requisição, não contrato de modelo")
check(ai.OUTPUT_NOVELTY is v2.OCCURRENCE_NOVELTY,
      "[4] §20 o enum de novidade é O MESMO objeto do Contract V2")
check(list(v2.OCCURRENCE_NOVELTY) == ["NEW_OCCURRENCE", "FOLLOW_UP",
                                      "HISTORICAL_CONTEXT",
                                      "DESCRIPTOR_OR_BACKGROUND", "UNDETERMINED"],
      "[5] e o contrato segue congelado")
check("UNDETERMINED" in ai.OUTPUT_CONFIANCA,
      "[6] §15 abstenção é caminho de primeira classe, não resíduo")

print()
print("=" * 98)
print("§53/§54 OS SEIS CASOS — PACOTE CEGO E VARREDURA DE VAZAMENTO")
print("=" * 98)
CASOS = [
    ("Santander Brasil", "troca_ceo", REFS["santander_0530"], "Santander"),
    ("Tupy", "troca_ceo", REFS["tupy_0810"], "Tupy"),
    ("Yura", "troca_ceo", REFS["yura_0720"], "Yura"),
    ("Smart Fit", "ma", REFS["smartfit_0804"], "Smart Fit"),
    ("BRF", "ma", REFS["brf_0918"], "BRF"),
    ("Hapvida", "troca_ceo", REFS["hapvida_0406"], "Hapvida"),
]
PACOTES = {}
_n = 7
for emp, fam, ref, rot in CASOS:
    p = pacote(emp, fam, ref)
    PACOTES[rot] = p
    check(p["prompt_payload"]["target_article"]["article_ref"] == ref,
          f"[{_n}] {rot}: pacote construído para o artigo certo")
    _n += 1
    check(ai.vazamentos(p) == [],
          f"[{_n}] {rot}: ZERO vazamento de verdade humana no payload "
          f"({ai.vazamentos(p) or 'nenhum'})")
    _n += 1

print()
print("=" * 98)
print("§2/§72 VAZAMENTO — A VARREDURA PRECISA SABER FALHAR")
print("=" * 98)
_sujo = json.loads(json.dumps(PACOTES["Santander"]))
_sujo["prompt_payload"]["target_article"]["nota"] = (
    "occurrence_truth_id troca_ceo:santander-brasil:a6791cad2017")
check(ai.vazamentos(_sujo) != [],
      f"[{_n}] um id humano injetado no payload É detectado ({ai.vazamentos(_sujo)})")
_n += 1
_sujo2 = json.loads(json.dumps(PACOTES["Hapvida"]))
_sujo2["prompt_payload"]["detector_reasons"] = ["DISTINCT_OCCURRENCE"]
check("DISTINCT_OCCURRENCE" in ai.vazamentos(_sujo2),
      f"[{_n}] e o rótulo da relação negativa também — seria dar a resposta "
      "da Hapvida de graça")
_n += 1
_ids_humanos = set(ot.ocorrencias(D))
for rot, p in PACOTES.items():
    bruto = json.dumps(p["prompt_payload"], ensure_ascii=False)
    check(not any(i in bruto for i in _ids_humanos),
          f"[{_n}] {rot}: nenhum dos 7 ids humanos aparece no payload")
    _n += 1
for rot, p in PACOTES.items():
    check("gustavo" not in json.dumps(p["prompt_payload"], ensure_ascii=False),
          f"[{_n}] {rot}: nem o nome do adjudicador")
    _n += 1
check(all("evaluation_metadata" in p and "prompt_payload" in p
          for p in PACOTES.values()),
      f"[{_n}] §71 payload e metadados de avaliação são campos SEPARADOS")
_n += 1

print()
print("=" * 98)
print("§7/§8 O PACOTE VEM DO ESTADO PROVISÓRIO, NÃO DA VERDADE HUMANA")
print("=" * 98)
_san = PACOTES["Santander"]["prompt_payload"]
check(len(_san["candidate_occurrences"]) == 1,
      f"[{_n}] Santander: o alvo é excluído dos candidatos, sobra 1 "
      f"({len(_san['candidate_occurrences'])}) — oferecer a ocorrência que já "
      "contém o alvo seria responder a pergunta")
_n += 1
check(_san["candidate_occurrences"][0]["provisional_occurrence_id"].endswith("#0")
      or "#" in _san["candidate_occurrences"][0]["provisional_occurrence_id"],
      f"[{_n}] §8 o candidato usa `_occ_key` como referência PROVISÓRIA — "
      "legítimo aqui, porque é entrada do algoritmo e descartável")
_n += 1
check(_san["candidate_occurrences"][0]["candidate_label"] == "CANDIDATE_1",
      f"[{_n}] §32 e o modelo escolhe por rótulo neutro, não por chave humana")
_n += 1
check(_san["target_article"]["family_identity_extracted"]["follow_up_language"] is True,
      f"[{_n}] §44 a marca de acompanhamento entra: é evidência DETERMINÍSTICA "
      "de runtime, não rótulo humano")
_n += 1
check(_san["candidate_occurrences"][0]["family_identity_extracted"]
      .get("incoming_person") == ["gilson finkelsztain"],
      f"[{_n}] e a pessoa extraída do candidato também")
_n += 1

print()
print("=" * 98)
print("§9/§68 QUALIDADE DA EVIDÊNCIA TEXTUAL")
print("=" * 98)
_q = {p["prompt_payload"]["target_article"]["text_evidence_quality"]
      for p in PACOTES.values()}
check(_q <= set(ai.TEXT_EVIDENCE), f"[{_n}] valores dentro do enum ({sorted(_q)})")
_n += 1
check("TITLE_PLUS_REDUNDANT_SNIPPET" in _q,
      f"[{_n}] §17 e o pacote DIZ ao modelo que o resumo é o título repetido — "
      "94% do corpus é assim, e supor corpo de matéria faria alucinar detalhe")
_n += 1
check(all(p["prompt_payload"]["target_article"].get("local_snippet") is None
          for p in PACOTES.values()),
      f"[{_n}] nenhum snippet redundante é enviado como se fosse texto novo")
_n += 1

print()
print("=" * 98)
print("§42/§43 O QUE NÃO ENTRA")
print("=" * 98)
_todo = json.dumps({r: p["prompt_payload"] for r, p in PACOTES.items()},
                   ensure_ascii=False)
for termo, rot in (("total_score", "score"), ("critico", "status crítico"),
                   ("hard_critical", "criticidade"), ("peso_base", "peso")):
    check(termo not in _todo, f"[{_n}] §42 {rot} não entra — é a jusante e enviesaria")
    _n += 1
for termo, rot in (("verdict", "veredito humano"), ("scoreable", "pontuabilidade"),
                   ("reviewed_at", "marca de revisão"), ("gold", "rótulo de gold")):
    check(termo not in _todo, f"[{_n}] §43 {rot} não entra")
    _n += 1

print()
print("=" * 98)
print("§60 AVALIADOR — OS DOIS LADOS DO ERRO")
print("=" * 98)
_p_san = PACOTES["Santander"]
_certo = ai.avaliar(_p_san, {"selected_candidate": "CANDIDATE_1",
                             "occurrence_novelty": "FOLLOW_UP",
                             "confidence": "HIGH"}, D)
check(_certo["avaliavel"] and _certo["linkage_correct"] is True
      and not _certo["false_merge"] and not _certo["false_split"],
      f"[{_n}] Santander: ligar a análise da XP à ocorrência de março é CORRETO")
_n += 1
_split = ai.avaliar(_p_san, {"selected_candidate": None,
                             "occurrence_novelty": "NEW_OCCURRENCE",
                             "confidence": "HIGH"}, D)
check(_split["false_split"] is True,
      f"[{_n}] §25 e dizer que é fato NOVO pontua como FALSO SPLIT")
_n += 1
_p_hap = PACOTES["Hapvida"]
_merge = ai.avaliar(_p_hap, {"selected_candidate": "CANDIDATE_1",
                             "occurrence_novelty": "FOLLOW_UP",
                             "confidence": "HIGH"}, D)
check(_merge["false_merge"] is True,
      f"[{_n}] §24/§66 Hapvida: dizer que a troca de abril é a mesma de "
      "dezembro pontua como FALSO MERGE — o controle negativo principal")
_n += 1
_certo_hap = ai.avaliar(_p_hap, {"selected_candidate": "CANDIDATE_2",
                                 "occurrence_novelty": "FOLLOW_UP",
                                 "confidence": "HIGH"}, D)
check(_certo_hap["linkage_correct"] is True and not _certo_hap["false_merge"],
      f"[{_n}] e ligá-la ao artigo de maio, que é a MESMA transição de abril, "
      "é correto — o avaliador distingue os dois candidatos")
_n += 1
_novo_hap = ai.avaliar(_p_hap, {"selected_candidate": None,
                                "occurrence_novelty": "NEW_OCCURRENCE",
                                "confidence": "HIGH"}, D)
check(_novo_hap["false_split"] is True,
      f"[{_n}] e dizer NOVA aqui é falso split, porque um irmão da própria "
      "ocorrência estava entre os candidatos")
_n += 1
# O caminho de ocorrência nova precisa existir e pontuar como CORRETO quando
# nenhum candidato carrega a verdade do alvo. Nesta situação nenhum artigo
# adjudicado da Hapvida sobra no pacote, então removo o candidato irmão para
# exercitar a lógica — é o avaliador que está sob teste, não o corpus.
_sem_irmao = json.loads(json.dumps(_p_hap))
_sem_irmao["prompt_payload"]["candidate_occurrences"] = [
    c for c in _sem_irmao["prompt_payload"]["candidate_occurrences"]
    if c["candidate_label"] == "CANDIDATE_1"]
_novo_ok = ai.avaliar(_sem_irmao, {"selected_candidate": None,
                                   "occurrence_novelty": "NEW_OCCURRENCE",
                                   "confidence": "HIGH"}, D)
check(_novo_ok["linkage_correct"] is True and not _novo_ok["false_split"]
      and not _novo_ok["false_merge"],
      f"[{_n}] §12 com só o candidato de dezembro à mesa, dizer NOVA é CORRETO "
      "— o caminho de ocorrência nova existe e é obrigatório")
_n += 1
_abst = ai.avaliar(_p_san, {"selected_candidate": None,
                            "occurrence_novelty": "UNDETERMINED",
                            "confidence": "UNDETERMINED"}, D)
check(_abst["abstained"] is True,
      f"[{_n}] §15 abstenção é registrada como tal, não como acerto nem erro")
_n += 1
check(ai.avaliar(_p_san, {"selected_candidate": "CANDIDATE_1",
                          "occurrence_novelty": "FOLLOW_UP"}, D)["human_occurrence"]
      not in json.dumps(_p_san["prompt_payload"], ensure_ascii=False),
      f"[{_n}] §22 o avaliador conhece o id humano; o payload, não")
_n += 1

print()
print("=" * 98)
print("§21/§22 AVALIAÇÃO COMPARA PERTINÊNCIA, NUNCA IDENTIFICADOR")
print("=" * 98)
_src = io.open("reliability_occurrence_auditor_input.py", encoding="utf-8").read()
import re as _re
_COD = "\n".join(l.split("#")[0] for l in
                 _re.sub(r'"""(?:.|\n)*?"""', " ", _src).splitlines())
check("_occ_key" in _COD and "provisional_occurrence_id" in _COD,
      f"[{_n}] `_occ_key` aparece SÓ como referência provisória")
_n += 1
check("article_ref" in _COD,
      f"[{_n}] e a avaliação casa por `article_ref` — comparar `ma#0` com um id "
      "humano não significaria nada, são espaços distintos")
_n += 1
# 4I.2 R7m: o defeito medido por este experimento é HISTÓRICO — as duas
# ocorrências provisórias do Santander vivem no snapshot congelado. Ler o
# acervo vivo aqui faria a correção semântica de `troca_ceo` apagar o próprio
# defeito que o auditor existe para medir.
_san_prov = len(ai._ocorrencias_provisorias(
    json.load(io.open(arq.HISTORICO, encoding="utf-8")),
    __import__("risk_dashboard").load_config("config_risco.yaml"),
    "Santander Brasil", "troca_ceo"))
_san_hum = len({m["occurrence_truth_id"] for m in ot.memberships_ativas(D)
                if m["company"] == "Santander Brasil"})
check(_san_prov == 2 and _san_hum == 1,
      f"[{_n}] Santander tem {_san_prov} ocorrências provisórias e {_san_hum} "
      "humana — e isso NÃO é inconsistência, é exatamente o defeito a medir")
_n += 1

print()
print("=" * 98)
print("§16/§17 RECUPERAÇÃO DE CANDIDATOS")
print("=" * 98)
_yur = PACOTES["Yura"]["prompt_payload"]
check(len(_yur["candidate_occurrences"]) == 1,
      f"[{_n}] Yura: o relato de março aparece como candidato")
_n += 1
check(_yur["candidate_occurrences"][0]["family_identity_extracted"]
      .get("outgoing_person") == ["juan carlos burga"],
      f"[{_n}] §63 com quem SAI extraído, e o alvo trazendo quem ENTRA — o "
      "modelo tem de inferir a complementaridade, e nada no pacote a afirma")
_n += 1
check("complementar" not in json.dumps(_yur, ensure_ascii=False).lower()
      and "same transition" not in json.dumps(_yur, ensure_ascii=False).lower(),
      f"[{_n}] nenhuma dica humana de que papéis complementares são o mesmo fato")
_n += 1
check(int(_yur["candidate_occurrences"][0]["last_date"][5:7]) == 3,
      f"[{_n}] §16 e o candidato de março entra apesar dos 138 dias — tempo "
      "ordena contexto, não decide identidade")
_n += 1

print()
print("=" * 98)
print("§35 PEGADA DE CONTEXTO")
print("=" * 98)
_tam = {r: len(json.dumps(p["prompt_payload"], ensure_ascii=False))
        for r, p in PACOTES.items()}
check(max(_tam.values()) < 8000,
      f"[{_n}] o maior pacote tem {max(_tam.values())} caracteres "
      f"(~{max(_tam.values()) // 4} tokens) — contexto não é o gargalo")
_n += 1
check(all(len(p["prompt_payload"]["candidate_occurrences"]) <= 5
          for p in PACOTES.values()),
      f"[{_n}] §34 teto de 5 candidatos respeitado — cobre 100% do corpus atual")
_n += 1
check(all(len(c["representative_articles"]) <= 4
          for p in PACOTES.values() for c in p["prompt_payload"]["candidate_occurrences"]),
      f"[{_n}] §18 e no máximo 4 manchetes por candidato")
_n += 1

print()
print("=" * 98)
print("§80 NENHUMA ESCRITA, NENHUMA AUTORIDADE")
print("=" * 98)
_antes = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8")),
                    sort_keys=True)
for emp, fam, ref, _rot in CASOS:
    pacote(emp, fam, ref)
_depois = json.dumps(json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8")),
                     sort_keys=True)
check(_antes == _depois,
      f"[{_n}] construir os seis pacotes não altera o store de verdade")
_n += 1
check("gravar" not in _COD and "adicionar_membership" not in _COD
      and "criar_ocorrencia" not in _COD,
      f"[{_n}] o módulo não tem caminho de escrita")
_n += 1
check("build_evolution" not in _COD,
      f"[{_n}] §42 nem lê score")
_n += 1
_rd = io.open("risk_dashboard.py", encoding="utf-8").read()
check("occurrence_auditor" not in _rd,
      f"[{_n}] §78 e produção não o importa — zero autoridade")
_n += 1
for prov in ("gemini", "groq", "openai", "genai", "requests.post"):
    check(prov not in _COD.lower(),
          f"[{_n}] §81 nenhuma chamada a provider (`{prov}`)")
    _n += 1

print()
print("=" * 98)
print(f"RESULTADO INSUMO DO AUDITOR: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
