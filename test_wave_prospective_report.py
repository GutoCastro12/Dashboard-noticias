#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_prospective_report.py — o holdout contado de um jeito só.

O ERRO QUE ORIGINOU ESTE ARQUIVO

As métricas do experimento vinham sendo recontadas à mão a cada wave. Uma
dessas contas chamou `projetar_pontuavel_v2` sem `event_id`. O parâmetro é
opcional na assinatura, mas a porta OBJETO só é alcançável quando ele é
passado — e é ela que carrega `transaction_object`, metade do que o Contract
V2 acrescentou. Sem o argumento, a porta some em silêncio, a projeção devolve
"pontuável" e o número sai plausível.

O resultado foi caro: um acerto do G1 no caso Eneva virou erro na planilha, a
correção foi apresentada como conserto de um número que estava certo, e o
valor errado virou baseline oficial por três waves.

Nenhum teste pegava isso, porque não havia teste — havia conta manual. É por
isso que este arquivo existe, e é por isso que o BLOCO B abaixo é o mais
importante dele: ele falha se alguém voltar a medir sem a porta.

REGRA DESTE ARQUIVO

Números atuais são ASSERÇÃO, nunca constante de lógica. Se o holdout crescer,
estes valores mudam e as asserções acompanham — o que não pode mudar é a
forma de contar.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import io
import json
import tempfile
from pathlib import Path

import bench_semantic_eval as be
import reliability_prospective_report as rp
import semantic_v2_shadow as sh

PASS = FAIL = 0
REAL = Path("risk_semantic_v2_shadow.json")
SHA_REAL_INICIAL = hashlib.sha256(REAL.read_bytes()).hexdigest()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def temp_sidecar(dados: dict) -> Path:
    p = Path(tempfile.mkdtemp(prefix="rep_")) / "shadow.json"
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True))
    return p


R = rp.gerar()
CASO = {(d["empresa"], d["candidato"]): d for d in R["casos"]}
G1, G2 = "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"

print("=" * 98)
print("BLOCO A — CONTAGEM: CASO NÃO É REGISTRO DE MODELO")
print("=" * 98)
c = R["contagens"]
check(c["casos_observados"] == 2, f"[1] casos observados = 2 ({c['casos_observados']})")
check(c["casos_revisados"] == 2, f"[2] casos revisados = 2 ({c['casos_revisados']})")
check(c["registros_de_modelo"] == 4,
      f"[3] registros de modelo = 4 ({c['registros_de_modelo']})")
check(c["casos_revisados"] != c["registros_de_modelo"],
      "[4] revisados NUNCA é 4 — G1 e G2 dividem a mesma verdade")
check(c["modelos"] == [G1, G2],
      f"[5] os modelos são DESCOBERTOS do sidecar, não fixados ({c['modelos']})")

print()
print("=" * 98)
print("BLOCO B — A PORTA OBJETO PRECISA SER ALCANÇÁVEL (o erro que motivou tudo)")
print("=" * 98)
_ev_g1 = CASO[("Eneva", "ma")]["modelos"][G1]
check(_ev_g1["porta"] == "OBJETO",
      f"[6] a projeção do G1 no Eneva sai pela porta OBJETO ({_ev_g1['porta']}) "
      f"— logo o relatório passou `event_id`")
# O evento tem de ser COMPLETO: com um dicionário mínimo a projeção morre na
# porta ASSERCAO e nunca chega em OBJETO — o que provaria outra coisa.
_EV_MA = {"event_asserted": "ASSERTED", "subject": "X", "company_role": "BUYER",
          "currentness": "CURRENT", "occurrence_novelty": "NEW_OCCURRENCE",
          "phase": "CONFIRMED", "centrality": "MAIN",
          "transaction_object": "ASSET_OR_BUSINESS_UNIT"}
_com = be.projetar_pontuavel_v2(_EV_MA, "X", None, "ma")
_sem = be.projetar_pontuavel_v2(_EV_MA, "X")
check(_com["porta"] == "OBJETO" and _com["pontuavel"] is False,
      f"[7a] com `event_id`, o mesmo evento é barrado pela porta OBJETO ({_com})")
check(_sem["porta"] != "OBJETO" and _sem["pontuavel"] is True,
      f"[7b] e SEM o parâmetro ele passa como pontuável — a porta simplesmente "
      f"não existe ({_sem['porta']}). É exatamente esta a diferença que "
      f"transformou um acerto em erro na planilha.")
_fonte = inspect.getsource(rp.projecao_do_modelo)
check("candidate_event" in _fonte,
      "[8] o relatório deriva `event_id` do candidato do caso, sempre")
check(CASO[("Eneva", "ma")]["modelos"][G2]["porta"] != "OBJETO",
      "[9] e a porta não dispara para quem respondeu EQUITY_STAKE — não é "
      "rejeição automática de M&A")

print()
print("=" * 98)
print("BLOCO C — PONTUABILIDADE FINAL, DERIVADA")
print("=" * 98)
_f = R["pontuabilidade_final"]
check(_f["deterministico"]["acertos"] == 1 and _f["deterministico"]["denominador"] == 2,
      f"[10] determinístico 1/2 ({_f['deterministico']['acertos']}/"
      f"{_f['deterministico']['denominador']})")
check(_f[G1]["acertos"] == 2 and _f[G1]["denominador"] == 2,
      f"[11] G1 2/2 ({_f[G1]['acertos']}/{_f[G1]['denominador']})")
check(_f[G2]["acertos"] == 1 and _f[G2]["denominador"] == 2,
      f"[12] G2 1/2 ({_f[G2]['acertos']}/{_f[G2]['denominador']})")
check(all(v["denominador"] == R["contagens"]["casos_revisados"]
          for v in _f.values()),
      "[13] o denominador é o mesmo para todos: casos revisados e prospectivos")

print()
print("=" * 98)
print("BLOCO D — ENEVA: CRÉDITO DIMENSIONAL NÃO É CRÉDITO FINAL, E VICE-VERSA")
print("=" * 98)
_e = CASO[("Eneva", "ma")]
check(_e["humano_pontuavel"] is False and _e["veredito_humano"] == "FALSE_SCOPE",
      "[14] verdade humana: FALSE_SCOPE, não pontuável")
check(_e["modelos"][G1]["dimensoes"]["transaction_object"] == "ASSET_OR_BUSINESS_UNIT",
      "[15] G1 leu o objeto certo: ASSET_OR_BUSINESS_UNIT")
check(_e["modelos"][G1]["projecao_final"] is False,
      "[16] e por causa disso a projeção final do G1 é False — o acerto "
      "dimensional PROPAGA através da porta")
check(_e["modelos"][G2]["dimensoes"]["transaction_object"] == "EQUITY_STAKE",
      "[17] G2 leu EQUITY_STAKE")
check(_e["modelos"][G2]["projecao_final"] is True,
      "[18] e a projeção final do G2 fica True — errada, pelo motivo certo")
check(_e["deterministico_correto"] is False,
      "[19] o determinístico erra este caso")
check(_e["modelos"][G1]["dimensoes"]["occurrence_novelty"] == "NEW_OCCURRENCE"
      and _e["dimensoes_humanas"]["occurrence_novelty"] == "FOLLOW_UP",
      "[20] e o G1 ERRA a novidade neste mesmo caso — acertar o objeto não "
      "o absolve das outras dimensões")

print()
print("=" * 98)
print("BLOCO E — JBS")
print("=" * 98)
_j = CASO[("JBS", "troca_ceo")]
check(_j["humano_pontuavel"] is True
      and _j["veredito_humano"] == "TRUE_NEW_ANNOUNCEMENT",
      "[21] verdade humana: TRUE_NEW_ANNOUNCEMENT, pontuável")
check(_j["deterministico_correto"] is True, "[22] determinístico acerta")
check(_j["modelos"][G1]["projecao_final"] is True
      and _j["modelos"][G2]["projecao_final"] is True,
      "[23] os dois modelos acertam a pontuabilidade")
check(all(_j["modelos"][m]["dimensoes"]["occurrence_novelty"] == "NEW_OCCURRENCE"
          for m in (G1, G2)),
      "[24] os dois acertam a novidade")
check(all(_j["modelos"][m]["dimensoes"]["phase"] == "ANNOUNCED"
          for m in (G1, G2)),
      "[25] e os dois acertam a fase")
check(all(_j["modelos"][m]["evidencia_bruta"] == "INVALIDA" for m in (G1, G2)),
      "[26] a evidência BRUTA dos dois é inválida — telemetria original preservada")
check(all("H1_QUOTE_INEXISTENTE" in _j["modelos"][m]["marcas_brutas"]
          for m in (G1, G2)),
      "[27] com a marca H1 original, auditável")
check(all(_j["modelos"][m]["evidencia_q2"] == "VALIDA" for m in (G1, G2)),
      "[28] e sob o validador vigente as duas são válidas — as leituras "
      "coexistem, nenhuma sobrescreve a outra")

print()
print("=" * 98)
print("BLOCO F — DENOMINADORES POR DIMENSÃO")
print("=" * 98)
_d = R["dimensoes"]
check(_d[G1]["occurrence_novelty"]["acertos"] == 1
      and _d[G1]["occurrence_novelty"]["denominador"] == 2,
      f"[29] G1 novidade 1/2 ({_d[G1]['occurrence_novelty']['acertos']}/"
      f"{_d[G1]['occurrence_novelty']['denominador']})")
check(_d[G2]["occurrence_novelty"]["acertos"] == 1
      and _d[G2]["occurrence_novelty"]["denominador"] == 2,
      "[30] G2 novidade 1/2")
check(_d[G1]["transaction_object"]["acertos"] == 2
      and _d[G2]["transaction_object"]["acertos"] == 1,
      f"[31] objeto: G1 2/2, G2 1/2 — a dimensão separa os modelos")
# Os dois casos adjudicaram fase (JBS ANNOUNCED, Eneva CONFIRMED), então o
# denominador é 2. O ponto do teste não é o número: é que ele venha da verdade
# humana existente, e não do total de casos por conveniência.
_fases_humanas = sum(1 for d in R["casos"]
                     if "phase" in d["dimensoes_humanas"])
check(_d[G1]["phase"]["denominador"] == _fases_humanas == 2,
      f"[32] o denominador de fase é o nº de casos com fase adjudicada "
      f"({_d[G1]['phase']['denominador']} = {_fases_humanas})")
check(all(v["denominador"] <= R["contagens"]["casos_revisados"]
          for m in _d for v in _d[m].values()),
      "[33] nenhuma dimensão tem denominador maior que os casos revisados")
check(all((v["denominador"] == 0) == (v["acuracia"] is None)
          for m in _d for v in _d[m].values()),
      "[34] denominador zero ⇔ acurácia nula — ausência de verdade não vira "
      "erro do modelo nem acerto")

print()
print("=" * 98)
print("BLOCO G — EVIDÊNCIA EM DUAS CAMADAS")
print("=" * 98)
_ev = R["evidencia"]
check(_ev["bruta"]["validos"] == 2 and _ev["bruta"]["total"] == 4,
      f"[35] como observado: 2/4 ({_ev['bruta']['validos']}/{_ev['bruta']['total']})")
check(_ev["reavaliada_q2"]["validos"] == 4 and _ev["reavaliada_q2"]["total"] == 4,
      "[36] reavaliada: 4/4")
check(_ev["invalido_para_valido"] == 2 and _ev["valido_para_invalido"] == 0,
      "[37] 2 INVÁLIDA→VÁLIDA, 0 no sentido inverso")
check(R["fonte"]["validador_evidencia"] == "r7ba.q2",
      f"[38] e a versão do validador fica registrada ({R['fonte']['validador_evidencia']})")

print()
print("=" * 98)
print("BLOCO H — FILAS, DECISÃO E REPRODUTIBILIDADE")
print("=" * 98)
check(len(R["fila_de_revisao"]) == 0,
      f"[39] fila de revisão vazia ({len(R['fila_de_revisao'])})")
check(len(R["fila_de_divergencia"]) >= 1,
      f"[40] fila de divergência não vazia ({len(R['fila_de_divergencia'])}) — "
      f"Eneva tem modelos discordando entre si")
check(R["decisao"]["vencedor"] is None, "[41] vencedor = NENHUM")
check(R["decisao"]["amostra_pequena"] and R["decisao"]["progresso"] == "2/25",
      f"[42] aviso de amostra pequena, progresso {R['decisao']['progresso']}")
check(R["fonte"]["sidecar_sha256"]
      == hashlib.sha256(REAL.read_bytes()).hexdigest(),
      "[43] o relatório carimba o SHA-256 da fonte")
check(R["fonte"]["freeze_iso"] == sh.CONTRACT_FREEZE_ISO
      and R["fonte"]["freeze_epoch"] == sh.CONTRACT_FREEZE_TS,
      "[44] e o freeze, para que um relatório antigo possa ser reproduzido")
_r2 = rp.gerar()
_a = {k: v for k, v in R.items() if k != "gerado_em"}
_b = {k: v for k, v in _r2.items() if k != "gerado_em"}
check(json.dumps(_a, sort_keys=True) == json.dumps(_b, sort_keys=True),
      "[45] duas gerações seguidas dão conteúdo analítico idêntico")
check(R["escopo_do_contrato"] == [{"contract_version": "v2",
                                   "prompt_version": "r7ba.p2"}],
      f"[46] escopo segmentado por contrato/prompt ({R['escopo_do_contrato']})")
check(not R["integridade"], f"[47] zero erro de integridade ({R['integridade']})")
check(len(R["excluidos"]) == 0, "[48] zero caso não prospectivo")

print()
print("=" * 98)
print("BLOCO I — CASO NOVO SEM REVISÃO NÃO MEXE NO DENOMINADOR")
print("=" * 98)
_d0 = sh.carregar()
_novo = copy.deepcopy(_d0)
_aid = sh.id_artigo("https://exemplo.com/caso-3")
_hist = json.load(io.open("risk_history.json", encoding="utf-8"))
_hist["articles"]["https://exemplo.com/caso-3"] = {
    "title": "Cemig anuncia novo CEO", "summary": "Cemig anuncia novo CEO",
    "captured_ts": sh.CONTRACT_FREEZE_TS + 3600, "companies": ["Cemig"]}
_hp = Path(tempfile.mkdtemp(prefix="hist_")) / "h.json"
io.open(_hp, "w", encoding="utf-8").write(json.dumps(_hist, ensure_ascii=False))
_novo["observacoes"][sh.chave(_aid, "Cemig", "troca_ceo", "v2", "r7ba.p2", G1)] = {
    "article_id": _aid, "company": "Cemig", "candidate_event": "troca_ceo",
    "url": "https://exemplo.com/caso-3", "title": "Cemig anuncia novo CEO",
    "source": "Fonte X", "first_seen_iso": "2026-08-15 01:00",
    "contract_version": "v2", "schema_version": "r7ba.s2",
    "prompt_version": "r7ba.p2", "actual_model": G1, "estado": "OK",
    "deterministic": {"scoreable": True},
    "saida": {"events": [{"event_id": "troca_ceo", "subject": "Cemig",
                          "occurrence_novelty": "NEW_OCCURRENCE"}]},
    "evidencia": {"aceitos": 1}, "human_review": None}
_r3 = rp.gerar(temp_sidecar(_novo), str(_hp))
check(_r3["contagens"]["casos_observados"] == 3,
      f"[49] observados sobe para 3 ({_r3['contagens']['casos_observados']})")
check(_r3["contagens"]["casos_revisados"] == 2,
      "[50] revisados continua 2")
check(len(_r3["fila_de_revisao"]) == 1,
      f"[51] fila de revisão vira 1 ({len(_r3['fila_de_revisao'])})")
check(_r3["pontuabilidade_final"][G1]["denominador"] == 2,
      "[52] e o denominador de acurácia NÃO muda — caso sem verdade não conta")
check(_r3["casos"][[i for i, d in enumerate(_r3["casos"])
                    if d["empresa"] == "Cemig"][0]]["modelos_ausentes"] == [G2],
      "[53] caso com só um modelo funciona, e o ausente é reportado")

print()
print("=" * 98)
print("BLOCO J — TERCEIRO MODELO ENTRA SEM MUDAR CÓDIGO")
print("=" * 98)
_g3 = copy.deepcopy(_d0)
_um = [o for o in _d0["observacoes"].values() if o["company"] == "JBS"][0]
_g3["observacoes"][sh.chave(_um["article_id"], "JBS", "troca_ceo", "v2",
                            "r7ba.p2", "modelo-G3")] = {
    **copy.deepcopy(_um), "actual_model": "modelo-G3"}
_r4 = rp.gerar(temp_sidecar(_g3))
check(_r4["contagens"]["registros_de_modelo"] == 5,
      f"[54] registros vão a 5 ({_r4['contagens']['registros_de_modelo']})")
check(_r4["contagens"]["casos_observados"] == 2, "[55] casos continuam 2")
check("modelo-G3" in _r4["pontuabilidade_final"]
      and _r4["pontuabilidade_final"]["modelo-G3"]["denominador"] >= 1,
      "[56] o G3 aparece nas métricas sozinho, sem alterar o gerador")

print()
print("=" * 98)
print("BLOCO K — REVISÃO PARCIAL, OVERRIDE E NÃO-PROSPECTIVO")
print("=" * 98)
_parc = copy.deepcopy(_d0)
for _k, _o in _parc["observacoes"].items():
    if _o["company"] == "JBS":
        _o["human_review"]["dimensoes_adjudicadas"].pop("phase", None)
_r5 = rp.gerar(temp_sidecar(_parc))
check(_r5["dimensoes"][G1]["phase"]["denominador"] == 1,
      f"[57] tirando a fase do JBS, o denominador de fase cai de 2 para 1 "
      f"({_r5['dimensoes'][G1]['phase']['denominador']})")
_parc2 = copy.deepcopy(_d0)
for _o in _parc2["observacoes"].values():
    _o["human_review"]["dimensoes_adjudicadas"].pop("phase", None)
_r5b = rp.gerar(temp_sidecar(_parc2))
check(_r5b["dimensoes"][G1]["phase"]["denominador"] == 0
      and _r5b["dimensoes"][G1]["phase"]["acuracia"] is None,
      "[57b] e sem NENHUMA fase adjudicada a dimensão sai da conta por "
      "completo, em vez de virar 0% de acerto")
check(_r5["pontuabilidade_final"][G1]["denominador"] == 2,
      "[58] e a pontuabilidade final não é afetada por dimensão ausente")
_ovr = copy.deepcopy(_d0)
for _o in _ovr["observacoes"].values():
    if _o["company"] == "Eneva":
        _o["human_review"] = {**_o["human_review"],
                              "override_de": {"verdict": "TRUE"},
                              "override_motivo": "reavaliação"}
_r6 = rp.gerar(temp_sidecar(_ovr))
check(_r6["contagens"]["overrides"] == 1,
      f"[59] override é contado ({_r6['contagens']['overrides']})")
check(_r6["contagens"]["casos_revisados"] == 2,
      "[60] e o caso não é contado duas vezes")
check(_r6["pontuabilidade_final"][G1]["acertos"] == 2,
      "[61] a verdade EFETIVA (a atual) é a usada, não a sobrescrita")
_pre = copy.deepcopy(_d0)
_hist2 = json.load(io.open("risk_history.json", encoding="utf-8"))
for _o in _pre["observacoes"].values():
    if _o["company"] == "Eneva":
        _art = _hist2["articles"][_o["url"]]
        _art["captured_ts"] = sh.CONTRACT_FREEZE_TS - 3600
        _art.pop("pub_ts", None)
_hp2 = Path(tempfile.mkdtemp(prefix="hist2_")) / "h.json"
io.open(_hp2, "w", encoding="utf-8").write(json.dumps(_hist2, ensure_ascii=False))
_r7 = rp.gerar(temp_sidecar(_pre), str(_hp2))
check(len(_r7["excluidos"]) == 1,
      f"[62] caso anterior ao freeze é excluído ({len(_r7['excluidos'])})")
check(_r7["pontuabilidade_final"][G1]["denominador"] == 1,
      f"[63] e sai das métricas principais — denominador cai para 1 "
      f"({_r7['pontuabilidade_final'][G1]['denominador']})")
check(len(_r7["casos"]) == 2,
      "[64] mas o registro NÃO é apagado do relatório")

print()
print("=" * 98)
print("BLOCO L — INTEGRIDADE FALA ALTO")
print("=" * 98)
_dup = copy.deepcopy(_d0)
_alvo = [k for k, o in _d0["observacoes"].items() if o["company"] == "JBS"][0]
_dup["observacoes"][_alvo + "|copia"] = copy.deepcopy(_d0["observacoes"][_alvo])
_r8 = rp.gerar(temp_sidecar(_dup))
check(any(e["tipo"] == "REGISTRO_DUPLICADO" for e in _r8["integridade"]),
      f"[65] registro duplicado do mesmo modelo é sinalizado ({_r8['integridade']})")
_div = copy.deepcopy(_d0)
_ks = [k for k, o in _d0["observacoes"].items() if o["company"] == "JBS"]
_div["observacoes"][_ks[0]]["human_review"] = {
    **_div["observacoes"][_ks[0]]["human_review"], "verdict": "OUTRA_COISA"}
_r9 = rp.gerar(temp_sidecar(_div))
check(any(e["tipo"] == "VERDADE_DIVERGENTE_ENTRE_MODELOS"
          for e in _r9["integridade"]),
      "[66] verdade humana divergente entre modelos do mesmo caso é sinalizada")
_enum = copy.deepcopy(_d0)
for _o in _enum["observacoes"].values():
    if _o["company"] == "JBS":
        _o["human_review"]["dimensoes_adjudicadas"]["phase"] = "POSSE"
_r10 = rp.gerar(temp_sidecar(_enum))
check(any(e["tipo"] == "ENUM_HUMANO_INVALIDO" for e in _r10["integridade"]),
      "[67] enum humano fora do contrato é sinalizado, não calculado em silêncio")

print()
print("=" * 98)
print("BLOCO M — DERIVADO, SEM AUTORIDADE, SEM CONSTANTES ESCONDIDAS")
print("=" * 98)
for _n, _mod in ((68, "risk_dashboard.py"), (69, "semantic_audit.py")):
    _src = io.open(_mod, encoding="utf-8").read()
    check("prospective_report" not in _src,
          f"[{_n}] {_mod} não importa o relatório")
_rp_src = io.open("reliability_prospective_report.py", encoding="utf-8").read()
# Remover comentários E docstrings: sem isso a prosa do módulo entra na busca
# e "adjudicações gravaram" faz parecer que existe uma chamada de escrita.
import re as _re
_cod = _re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
               "\n".join(l.split("#")[0] for l in _rp_src.splitlines()))
for _n, _t in ((70, "Eneva"), (71, "JBS"), (72, "gemini-3.1"), (73, "gemini-3.5")):
    check(_t not in _cod, f"[{_n}] o gerador não menciona '{_t}'")
check("sh.gravar" not in _cod and "gravar(" not in _cod,
      "[74] o gerador não chama nenhuma função de escrita do sidecar")
_escritas = _re.findall(r'io\.open\([^)]*"w"', _cod)
check(len(_escritas) == 2,
      f"[75] as únicas escritas são os dois arquivos de saída pedidos por flag "
      f"({len(_escritas)}) — a fonte nunca é destino")
_md = rp.markdown(R)
check("AMOSTRA PEQUENA DEMAIS" in _md and "Vencedor: NENHUM" in _md,
      "[76] o markdown avisa da amostra pequena e não declara vencedor")
check("| avaliador | acertos | denominador | acurácia |" in _md,
      "[77] e sempre mostra o denominador ao lado da acurácia")

print()
print("=" * 98)
print("BLOCO N — A FONTE TERMINOU COMO COMEÇOU")
print("=" * 98)
check(hashlib.sha256(REAL.read_bytes()).hexdigest() == SHA_REAL_INICIAL,
      "[78] risk_semantic_v2_shadow.json byte a byte idêntico ao início")
_atual = sh.carregar()
check(all(o.get("human_review") for o in _atual["observacoes"].values()),
      "[79] as quatro adjudicações reais seguem no lugar")

print()
print("=" * 98)
print(f"RESULTADO RELATÓRIO PROSPECTIVO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
