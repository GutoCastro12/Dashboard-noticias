#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_occurrence_truth.py — a que ocorrência econômica o artigo pertence.

O QUE ESTE ARQUIVO PROTEGE

Que a verdade humana sobreviva ao algoritmo. `_occ_key` não serve como
identidade: nesta mesma sessão, duas correções legítimas mudaram a chave do
MESMO fato — Smart Fit foi de `ma#1` para `ma#0`, EQT de `ma#0` para `ma#1`.
Supervisão ancorada ali estaria hoje apontando para o lugar errado.

As seis fixtures cobrem as formas de "mesmo fato" que já foram adjudicadas, e
uma de "parece mas não é":

  Santander   comentário de analista meses depois
  Tupy        atividade do novo CEO depois da posse
  Yura        os dois lados da transição, sem uma palavra em comum
  Smart Fit   anúncio → aprovação → fechamento, fora de `troca_ceo`
  BRF         etapa societária de execução do que já foi feito
  Hapvida     DUAS trocas reais — o negativo que impede aprender a fundir

Um conjunto só com duplicatas ensinaria a fundir. É por isso que o registro de
relação existe, e é por isso que ele guarda DISTINCT e não SAME: o SAME já vem
de graça de dois artigos apontarem para a mesma ocorrência.

E protege o que o store NÃO pode fazer: `build_evolution`,
`assign_occurrence_clusters` e a classificação não o leem. Ele é supervisão,
nunca autoridade.
"""
from __future__ import annotations

import copy
import io
import json

import reliability_occurrence_truth as ot
import reliability_human_review_writer as hrw
import reliability_pilot_contract_v2 as v2
import semantic_v2_shadow as sh

PASS = FAIL = 0
QUANDO = "2026-08-16T00:00:00Z"
QUEM = "gustavo"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def vazio():
    return {"_meta": {"shadow_version": "teste"}, "observacoes": {}}


def criar(d, empresa, evento, data=None, ident=None):
    return ot.criar_ocorrencia(d, company=empresa, event_id=evento,
                               material_event_date=data, family_identity=ident,
                               adjudicated_by=QUEM, adjudicated_at_iso=QUANDO)


def membro(d, oid, ref, empresa, evento, nov, fase="UNKNOWN", ancora=None,
           supersedes=""):
    return ot.adicionar_membership(
        d, occurrence_truth_id=oid, article_ref_=ref, company=empresa,
        event_id=evento, occurrence_novelty=nov, material_phase=fase,
        should_refresh_anchor=ancora, adjudicated_by=QUEM,
        adjudicated_at_iso=QUANDO, supersedes=supersedes)


print("=" * 98)
print("§6/§7 IDENTIDADE INDEPENDENTE DO ALGORITMO")
print("=" * 98)
_a = ot.novo_occurrence_truth_id("troca_ceo", "Santander Brasil")
_b = ot.novo_occurrence_truth_id("troca_ceo", "Santander Brasil")
check(_a != _b, "[1] dois pedidos para a MESMA empresa/família dão ids distintos "
                "— o id é chave primária de uma decisão, não impressão digital do fato")
check(_a.startswith("troca_ceo:santander-brasil:"),
      f"[2] o prefixo é legível ({_a})")
check("ma#" not in _a and "#" not in _a, "[3] §15 `_occ_key` não entra no id")
_t = "Gilson Finkelsztain será o novo CEO do Santander"
check(_t.lower()[:10] not in _a.lower(), "[4] §16 o título não entra no id")
check("http" not in _a and "forbes" not in _a, "[5] §17 a URL não entra no id")
check("2026" not in _a and "03-19" not in _a, "[6] §18 a data não entra no id")
_d = vazio()
_o1 = criar(_d, "X", "ma")
check(ot.criar_ocorrencia.__doc__ is not None, "[7] emissão documentada")
try:
    ot.criar_ocorrencia(_d, company="X", event_id="ma", adjudicated_by=QUEM,
                        adjudicated_at_iso=QUANDO, occurrence_truth_id=_o1)
    check(False, "[8] §19 id duplicado deveria ser recusado")
except ot.VerdadeRecusada as e:
    check("ID_DUPLICADO" in str(e), f"[8] §19 id duplicado é recusado ({e})")

print()
print("=" * 98)
print("§15/§16 ENUMS")
print("=" * 98)
check(ot.OCCURRENCE_NOVELTY is v2.OCCURRENCE_NOVELTY,
      "[9] o enum de novidade é O MESMO objeto do Contract V2, não uma cópia "
      "que possa divergir numa edição futura")
check(list(ot.OCCURRENCE_NOVELTY) == ["NEW_OCCURRENCE", "FOLLOW_UP",
                                      "HISTORICAL_CONTEXT",
                                      "DESCRIPTOR_OR_BACKGROUND", "UNDETERMINED"],
      f"[10] e é o enum congelado ({list(ot.OCCURRENCE_NOVELTY)})")
check("MATERIAL_NEW_PHASE" not in ot.OCCURRENCE_NOVELTY,
      "[11] §16 fase material NÃO virou valor de novidade — o contrato está congelado")
check("MATERIAL_NEW_PHASE" not in ot.MATERIAL_PHASE
      and set(ot.MATERIAL_PHASE) == {"ANNOUNCEMENT", "APPOINTMENT",
                                     "REGULATORY_APPROVAL", "CLOSING",
                                     "COMPLETION", "IMPLEMENTATION", "NONE",
                                     "UNKNOWN"},
      f"[12] a fase é enum próprio e mínimo ({ot.MATERIAL_PHASE})")
check("AMENDMENT" not in ot.MATERIAL_PHASE,
      "[13] e não traz fase especulativa: `AMENDMENT` não tem caso adjudicado")
check(ot.should_create_occurrence("NEW_OCCURRENCE") is True
      and ot.should_create_occurrence("FOLLOW_UP") is False
      and ot.should_create_occurrence("UNDETERMINED") is False,
      "[14] §18 `should_create_occurrence` é DERIVADO da novidade")
_src = io.open("reliability_occurrence_truth.py", encoding="utf-8").read()
check('"should_create_occurrence"' not in _src,
      "[15] e não é gravado como campo — dois lugares para a mesma verdade "
      "seria convite à contradição")
check("same_event_as" not in _src.split('"""', 2)[2],
      "[16] §11 `same_event_as` não é campo canônico: dois artigos na mesma "
      "ocorrência já dizem isso")

print()
print("=" * 98)
print("§30 FIXTURE SANTANDER — uma transição, três artigos")
print("=" * 98)
D = vazio()
SAN = criar(D, "Santander Brasil", "troca_ceo", "2026-03-19",
            {"incoming_person": "gilson finkelsztain",
             "outgoing_person": "mario leao"})
m1 = membro(D, SAN, "art-san-0319", "Santander Brasil", "troca_ceo",
            "NEW_OCCURRENCE", "ANNOUNCEMENT")
m2 = membro(D, SAN, "art-san-0324", "Santander Brasil", "troca_ceo", "FOLLOW_UP", "NONE")
m3 = membro(D, SAN, "art-san-0530", "Santander Brasil", "troca_ceo",
            "FOLLOW_UP", "NONE", ancora=False)
_refs = {m["occurrence_truth_id"] for m in ot.membros_de(D, SAN)}
check(_refs == {SAN} and len(ot.membros_de(D, SAN)) == 3,
      f"[17] os TRÊS artigos apontam para a MESMA ocorrência ({len(ot.membros_de(D, SAN))})")
check(ot.ocorrencias(D)[SAN]["material_event_date"] == "2026-03-19",
      "[18] a data do FATO é março, mesmo com o último artigo publicado em maio")
_m530 = [m for m in ot.memberships(D) if m["article_ref"] == "art-san-0530"][0]
check(_m530["should_refresh_anchor"] is False,
      "[19] e o comentário de maio não renova âncora")
check(ot.should_create_occurrence(_m530["occurrence_novelty"]) is False,
      "[20] nem cria ocorrência")
check(not ot.validar(D), f"[21] store consistente ({ot.validar(D)})")

print()
print("=" * 98)
print("§31 FIXTURE TUPY — âncora é UNKNOWN onde ninguém adjudicou")
print("=" * 98)
TUP = criar(D, "Tupy", "troca_ceo", "2026-03-30",
            {"incoming_person": "harro burmann"})
membro(D, TUP, "art-tup-0330", "Tupy", "troca_ceo", "NEW_OCCURRENCE", "ANNOUNCEMENT")
membro(D, TUP, "art-tup-0504", "Tupy", "troca_ceo", "FOLLOW_UP", "APPOINTMENT")
membro(D, TUP, "art-tup-0601", "Tupy", "troca_ceo", "FOLLOW_UP", "COMPLETION")
membro(D, TUP, "art-tup-0810", "Tupy", "troca_ceo", "FOLLOW_UP", "NONE")
check(len(ot.membros_de(D, TUP)) == 4, "[22] quatro artigos, uma ocorrência")
check(all(m["should_refresh_anchor"] is None for m in ot.membros_de(D, TUP)),
      "[23] âncora fica UNKNOWN nos quatro — a política não foi adjudicada, e "
      "inventar um valor seria pior que o vazio")
check({m["material_phase"] for m in ot.membros_de(D, TUP)}
      == {"ANNOUNCEMENT", "APPOINTMENT", "COMPLETION", "NONE"},
      "[24] §17 fases distintas convivem sem virar ocorrências distintas")

print()
print("=" * 98)
print("§32 FIXTURE YURA — os dois lados do mesmo fato, sem palavra em comum")
print("=" * 98)
YUR = criar(D, "Yura", "troca_ceo", "2026-03-04",
            {"outgoing_person": "juan carlos burga",
             "incoming_person": "gonzalo rueda castillo"})
membro(D, YUR, "art-yur-0304", "Yura", "troca_ceo", "NEW_OCCURRENCE", "ANNOUNCEMENT")
membro(D, YUR, "art-yur-0720", "Yura", "troca_ceo", "FOLLOW_UP", "APPOINTMENT")
check(len(ot.membros_de(D, YUR)) == 2,
      "[25] os dois relatos ficam na MESMA ocorrência")
_fi = ot.ocorrencias(D)[YUR]["family_identity"]
check(_fi["outgoing_person"] and _fi["incoming_person"],
      "[26] e a identidade guarda os dois papéis, que é o que os liga — "
      "nenhuma semelhança textual faria isso")
check(ot.ocorrencia_do_artigo(D, "art-yur-0720", "Yura", "troca_ceo") == YUR,
      "[27] consulta por artigo devolve a ocorrência certa")

print()
print("=" * 98)
print("§33/§34 FIXTURES FORA DE `troca_ceo`")
print("=" * 98)
SF = criar(D, "Smart Fit", "ma", "2025-12-02", {"target": "evolve"})
membro(D, SF, "art-sf-1202", "Smart Fit", "ma", "NEW_OCCURRENCE", "ANNOUNCEMENT")
membro(D, SF, "art-sf-0709", "Smart Fit", "ma", "FOLLOW_UP", "REGULATORY_APPROVAL")
membro(D, SF, "art-sf-0804", "Smart Fit", "ma", "FOLLOW_UP", "CLOSING")
check(len(ot.membros_de(D, SF)) == 3 and len(ot.ocorrencias(D)) == 4,
      "[28] Smart Fit: anúncio, Cade e fechamento numa ocorrência só")
check({m["material_phase"] for m in ot.membros_de(D, SF)}
      == {"ANNOUNCEMENT", "REGULATORY_APPROVAL", "CLOSING"},
      "[29] com fases distintas — prova que o esquema vale fora de CEO")
BRF = criar(D, "BRF", "ma", "2025-08-05", {"counterparties": ["marfrig"]})
membro(D, BRF, "art-brf-0918", "BRF", "ma", "FOLLOW_UP", "IMPLEMENTATION")
check(len(ot.membros_de(D, BRF)) == 1
      and ot.membros_de(D, BRF)[0]["material_phase"] == "IMPLEMENTATION",
      "[30] BRF: a etapa societária é execução do que já foi feito")
check(ot.should_create_occurrence(
      ot.membros_de(D, BRF)[0]["occurrence_novelty"]) is False,
      "[31] e não cria uma segunda ocorrência de M&A")

print()
print("=" * 98)
print("§35 FIXTURE HAPVIDA — O NEGATIVO")
print("=" * 98)
H1 = criar(D, "Hapvida", "troca_ceo", "2025-12-22")
H2 = criar(D, "Hapvida", "troca_ceo", "2026-04-06")
membro(D, H1, "art-hap-1222", "Hapvida", "troca_ceo", "NEW_OCCURRENCE", "ANNOUNCEMENT")
membro(D, H2, "art-hap-0406", "Hapvida", "troca_ceo", "NEW_OCCURRENCE", "ANNOUNCEMENT")
ot.adicionar_relacao(D, occurrence_a=H1, occurrence_b=H2,
                     relation="DISTINCT_OCCURRENCE",
                     evidence="duas trocas de comando reais em quatro meses",
                     adjudicated_by=QUEM, adjudicated_at_iso=QUANDO)
check(H1 != H2 and len(ot.relacoes(D)) == 1,
      "[32] duas ocorrências distintas, com a relação explícita")
check(ot.relacoes(D)[0]["relation"] == "DISTINCT_OCCURRENCE",
      "[33] §14 o negativo é gravável — sem ele o conjunto só ensina a fundir")
check("SAME_OCCURRENCE" not in ot.RELATION,
      "[34] §13 e não existe relação SAME: ela seria redundante com a "
      "pertinência, e redundância é onde nasce contradição")
try:
    ot.adicionar_relacao(D, occurrence_a=H1, occurrence_b=H1,
                         relation="DISTINCT_OCCURRENCE", adjudicated_by=QUEM,
                         adjudicated_at_iso=QUANDO)
    check(False, "[35] relação reflexiva deveria ser recusada")
except ot.VerdadeRecusada as e:
    check("REFLEXIVA" in str(e), f"[35] relação de uma ocorrência consigo é recusada ({e})")
check(not ot.validar(D), f"[36] store inteiro consistente ({ot.validar(D)})")

print()
print("=" * 98)
print("§37/§38 VALIDAÇÃO — O QUE É RECUSADO")
print("=" * 98)
_n = 37
for rot, kw, esperado in (
        ("ocorrência inexistente", dict(oid="troca_ceo:x:aaaaaaaaaaaa"), "OCORRENCIA_INEXISTENTE"),
        ("empresa divergente", dict(oid=SAN, empresa="Outra"), "EMPRESA_DIVERGENTE"),
        ("família divergente", dict(oid=SAN, evento="ma"), "FAMILIA_DIVERGENTE"),
        ("novidade fora do enum", dict(oid=SAN, nov="MATERIAL_NEW_PHASE"), "NOVIDADE_INVALIDA"),
        ("fase fora do enum", dict(oid=SAN, fase="INVENTADA"), "FASE_INVALIDA")):
    base = dict(oid=SAN, ref="art-novo", empresa="Santander Brasil",
                evento="troca_ceo", nov="FOLLOW_UP", fase="NONE")
    base.update(kw)
    try:
        membro(copy.deepcopy(D), base["oid"], base["ref"], base["empresa"],
               base["evento"], base["nov"], base["fase"])
        check(False, f"[{_n}] {rot} deveria ser recusado")
    except ot.VerdadeRecusada as e:
        check(esperado in str(e), f"[{_n}] {rot} é recusado ({str(e)[:44]})")
    _n += 1
try:
    ot.adicionar_membership(copy.deepcopy(D), occurrence_truth_id=SAN,
                            article_ref_="art-x", company="Santander Brasil",
                            event_id="troca_ceo", occurrence_novelty="FOLLOW_UP",
                            should_refresh_anchor="talvez", adjudicated_by=QUEM,
                            adjudicated_at_iso=QUANDO)
    check(False, f"[{_n}] âncora fora do domínio deveria ser recusada")
except ot.VerdadeRecusada as e:
    check("ANCORA_INVALIDA" in str(e), f"[{_n}] âncora só aceita True/False/None ({str(e)[:40]})")
_n += 1
try:
    criar(copy.deepcopy(D), "X", "ma", "19/03/2026")
    check(False, f"[{_n}] data malformada deveria ser recusada")
except ot.VerdadeRecusada as e:
    check("DATA_INVALIDA" in str(e), f"[{_n}] data malformada é recusada ({str(e)[:40]})")
_n += 1
_semdata = vazio()
criar(_semdata, "X", "ma", None)
check(not ot.validar(_semdata),
      f"[{_n}] §20 mas data DESCONHECIDA é legítima — nada obriga a inventar uma")
_n += 1
_orfao = copy.deepcopy(D)
_orfao[ot.OCCURRENCE_TRUTH_NS]["memberships"].append(
    {"membership_id": "zz", "occurrence_truth_id": "troca_ceo:nada:bbbbbbbbbbbb",
     "article_ref": "a", "company": "X", "event_id": "ma",
     "occurrence_novelty": "FOLLOW_UP", "material_phase": "NONE",
     "should_refresh_anchor": None})
check(("ORPHAN_MEMBERSHIP", "zz") in ot.validar(_orfao),
      f"[{_n}] §32 o validador detecta pertinência órfã")
_n += 1
_ciclo = vazio()
_c1 = criar(_ciclo, "X", "ma")
_x1 = membro(_ciclo, _c1, "a", "X", "ma", "FOLLOW_UP")
_ciclo[ot.OCCURRENCE_TRUTH_NS]["memberships"][0]["superseded_by"] = _x1
check(any(p[0] == "SUPERSESSION_CYCLE" for p in ot.validar(_ciclo)),
      f"[{_n}] §38 e detecta ciclo de substituição")
_n += 1

print()
print("=" * 98)
print("§25/§26 IMUTABILIDADE E SUBSTITUIÇÃO")
print("=" * 98)
E = vazio()
o1 = criar(E, "Emp", "ma", "2026-01-01")
o2 = criar(E, "Emp", "ma", "2026-06-01")
mA = membro(E, o1, "art-1", "Emp", "ma", "FOLLOW_UP")
try:
    membro(E, o2, "art-1", "Emp", "ma", "FOLLOW_UP")
    check(False, f"[{_n}] mover artigo sem `supersedes` deveria ser recusado")
except ot.VerdadeRecusada as e:
    check("PERTINENCIA_ATIVA_CONFLITANTE" in str(e),
          f"[{_n}] §37 mover artigo sem declarar correção é recusado ({str(e)[:44]})")
_n += 1
mB = membro(E, o2, "art-1", "Emp", "ma", "FOLLOW_UP", supersedes=mA)
check(ot.ocorrencia_do_artigo(E, "art-1", "Emp", "ma") == o2,
      f"[{_n}] com `supersedes`, a correção vale")
_n += 1
_ant = [m for m in ot.memberships(E) if m["membership_id"] == mA][0]
check(_ant["superseded_by"] == mB and _ant in ot.memberships(E),
      f"[{_n}] §26 e a pertinência anterior CONTINUA no arquivo, marcada — "
      "apagar destruiria o rastro da correção")
_n += 1
check(len(ot.memberships_ativas(E)) == 1 and len(ot.memberships(E)) == 2,
      f"[{_n}] só a nova conta como ativa")
_n += 1
try:
    membro(E, o1, "art-1", "Emp", "ma", "FOLLOW_UP", supersedes=mA)
    check(False, f"[{_n}] substituir duas vezes a mesma deveria ser recusado")
except ot.VerdadeRecusada as e:
    check("JA_SUBSTITUIDA" in str(e), f"[{_n}] substituir a mesma duas vezes é recusado")
_n += 1
check(not ot.validar(E), f"[{_n}] e o store segue consistente após a correção")
_n += 1

print()
print("=" * 98)
print("§39 IDA E VOLTA, §43 SERIALIZAÇÃO DETERMINÍSTICA")
print("=" * 98)
_ser = json.dumps(D, ensure_ascii=False, indent=1, sort_keys=True)
_volta = json.loads(_ser)
check(_volta == D, f"[{_n}] serializar e recarregar devolve o mesmo store")
_n += 1
check(json.dumps(json.loads(_ser), ensure_ascii=False, indent=1, sort_keys=True) == _ser,
      f"[{_n}] e a serialização é estável")
_n += 1
check(len(ot.ocorrencias(_volta)) == 7 and len(ot.memberships_ativas(_volta)) == 15,
      f"[{_n}] 7 ocorrências e 15 pertinências sobrevivem à volta "
      f"({len(ot.ocorrencias(_volta))}, {len(ot.memberships_ativas(_volta))})")
_n += 1

print()
print("=" * 98)
print("§43 O NAMESPACE NÃO É DESCARTADO PELO MERGE DO CRON")
print("=" * 98)
_fundido = sh.fundir(D, vazio())
check(ot.OCCURRENCE_TRUTH_NS in _fundido,
      f"[{_n}] `fundir` PRESERVA o namespace — antes ele reconstruía o dicionário "
      "com `_meta` e `observacoes` e apagaria a verdade humana no primeiro cron")
_n += 1
check(len(ot.ocorrencias(_fundido)) == len(ot.ocorrencias(D)),
      f"[{_n}] com todas as ocorrências intactas")
_n += 1
check(sh.fundir(vazio(), D).get(ot.OCCURRENCE_TRUTH_NS) is not None,
      f"[{_n}] e também quando ele vem do outro lado da fusão")
_n += 1
_obs = {"_meta": {}, "observacoes": {"k": {"a": 1}}}
check(sh.fundir(_obs, {"_meta": {}, "observacoes": {"k": {"a": 2}}})
      ["observacoes"]["k"]["a"] == 1,
      f"[{_n}] e a regra de que a PRIMEIRA observação vence continua valendo")
_n += 1

print()
print("=" * 98)
print("§48 SEM AUTORIDADE — PRODUÇÃO NÃO LÊ NADA DISTO")
print("=" * 98)
_rd = io.open("risk_dashboard.py", encoding="utf-8").read()
_sa = io.open("semantic_audit.py", encoding="utf-8").read()
for mod, nome in ((_rd, "risk_dashboard.py"), (_sa, "semantic_audit.py")):
    check("reliability_occurrence_truth" not in mod and ot.OCCURRENCE_TRUTH_NS not in mod,
          f"[{_n}] {nome} não importa nem lê a verdade de ocorrência")
    _n += 1
# A docstring do módulo CITA `build_evolution` e `assign_occurrence_clusters`
# para dizer que eles não leem nada daqui. Citar não é chamar, e apagar a
# explicação para satisfazer a checagem tornaria o módulo pior — então a
# verificação olha o código executável.
import re as _re
_SEM_DOC = _re.sub(r'"""(?:.|\n)*?"""', " ", _src)
_SRC_COD = "\n".join(l.split("#")[0] for l in _SEM_DOC.splitlines())
check("build_evolution" not in _SRC_COD and "assign_occurrence_clusters" not in _SRC_COD,
      f"[{_n}] e o módulo não chama score nem clusterização")
_n += 1
check("_occ_key" not in _SRC_COD,
      f"[{_n}] §6 `_occ_key` não aparece no código do módulo")
_n += 1

print()
print("=" * 98)
print("§40/§41 DRY-RUN É O PADRÃO")
print("=" * 98)
import tempfile
import pathlib
_tmp = pathlib.Path(tempfile.mkdtemp()) / "side.json"
io.open(_tmp, "w", encoding="utf-8").write(
    json.dumps(vazio(), ensure_ascii=False, indent=1, sort_keys=True))
_b0 = io.open(_tmp, "rb").read()
_rc = hrw.main(["occurrence-create", "--sidecar", str(_tmp), "--empresa", "Z",
                "--evento", "ma", "--revisor", QUEM, "--quando", QUANDO])
check(_rc == 0 and io.open(_tmp, "rb").read() == _b0,
      f"[{_n}] §40 sem `--apply` o arquivo fica byte a byte idêntico")
_n += 1
_rc = hrw.main(["occurrence-create", "--sidecar", str(_tmp), "--empresa", "Z",
                "--evento", "ma", "--revisor", QUEM, "--quando", QUANDO, "--apply"])
_depois = json.load(io.open(_tmp, encoding="utf-8"))
check(_rc == 0 and len(ot.ocorrencias(_depois)) == 1,
      f"[{_n}] §41 com `--apply` a ocorrência é gravada")
_n += 1
check(_depois["observacoes"] == {} and "_meta" in _depois,
      f"[{_n}] e `observacoes`/`_meta` seguem intocados")
_n += 1
_rc = hrw.main(["occurrence-create", "--sidecar", str(_tmp), "--empresa", "Z",
                "--evento", "ma", "--apply"])
check(_rc == 2, f"[{_n}] escrita sem proveniência é recusada (código {_rc})")
_n += 1
_rc = hrw.main(["occurrence-member", "--sidecar", str(_tmp), "--occurrence-id",
                "nao-existe", "--empresa", "Z", "--evento", "ma",
                "--novidade", "FOLLOW_UP", "--artigo-id", "a",
                "--revisor", QUEM, "--quando", QUANDO, "--apply"])
check(_rc == 2, f"[{_n}] e pertinência órfã é recusada (código {_rc})")
_n += 1

print()
print("=" * 98)
print("§22/§44/§47 REFERÊNCIA DE ARTIGO, VERSÃO E LEITURA")
print("=" * 98)
check(ot.article_ref("https://x.com/a") == sh.id_artigo("https://x.com/a"),
      f"[{_n}] §22 a referência de artigo REUSA `id_artigo`, que o projeto já tem")
_n += 1
check(ot.article_ref("https://x.com/a") != ot.article_ref("https://x.com/b"),
      f"[{_n}] e distingue artigos")
_n += 1
check(ot.OCCURRENCE_TRUTH_SCHEMA_VERSION == 1,
      f"[{_n}] §44/§45 a versão do esquema é própria e começa em 1")
_n += 1
check(getattr(v2, "CONTRACT_VERSION", "v2") == "v2",
      f"[{_n}] §10 e o Contract V2 não foi tocado")
_n += 1
_rel = ot.relatorio(D)
check("Verdade de ocorrência" in _rel and "gilson finkelsztain" in _rel
      and "DISTINCT_OCCURRENCE" in _rel,
      f"[{_n}] §47 leitura devolve ocorrência, identidade e relação — insumo "
      "pronto para avaliação futura, sem nenhuma chamada de modelo")
_n += 1

print()
print("=" * 98)
print(f"RESULTADO VERDADE DE OCORRÊNCIA: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
