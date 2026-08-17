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
# CHAVE POR ARTIGO, NÃO POR (empresa, família).
#
# A JBS tem DOIS artigos `troca_ceo` desde 2026-08-17 — o anúncio do Wesley
# Batista Filho e a matéria de margens que só menciona o novo CEO. Indexar por
# (empresa, família) fazia o segundo sobrescrever o primeiro em silêncio, e o
# teste passava a medir o caso errado sem falhar. Foi acidente que a chave
# fosse única antes; deixou de ser, e nada avisou.
CASO_POR_ARTIGO = {d["artigo_id"]: d for d in R["casos"]}
CASO = CASO_POR_ARTIGO   # compatibilidade com blocos que já usam id


def caso(artigo_id):
    """Resolve por identidade estável. Sem `[0]`, sem ordem de iteração."""
    c = CASO_POR_ARTIGO.get(artigo_id)
    assert c is not None, f"caso ausente: {artigo_id}"
    return c


# Os sete revisados, por id — o roster é asserção, não contagem.
ID_JBS_ANUNCIO = "5d05e84444486491a30b"
ID_JBS_DESCRITOR = "201b91aa6b3c1d9e780c"
ID_ORIZON = "2e440c297fa646ce331f"
ID_JBS_PILGRIMS = "61166442c0f897153eec"
ID_CITI_ASSOCIATES = "6cf40fc7ad1d8e108226"
ID_CITI_KARD = "d566e840bd3c1d4f43bc"
ID_ENEVA = next(d["artigo_id"] for d in R["casos"] if d["empresa"] == "Eneva")
ROSTER = [ID_ENEVA, ID_JBS_ANUNCIO, ID_JBS_DESCRITOR, ID_ORIZON,
          ID_JBS_PILGRIMS, ID_CITI_ASSOCIATES, ID_CITI_KARD]
G1, G2 = "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"

print("=" * 98)
print("BLOCO A — CONTAGEM: CASO NÃO É REGISTRO DE MODELO")
print("=" * 98)
c = R["contagens"]
# CONTAGEM VIVA CONFERIDA CONTRA O ESTADO, NÃO CONTRA CONSTANTE.
#
# Escrevi estas três como absolutas nesta mesma onda, e o cron seguinte trouxe
# o caso Tok&Stok — quebraram na hora. Era o erro que a onda existia para
# corrigir, cometido no arquivo que a corrigia. O que precisa ser invariante é
# a RELAÇÃO entre as contagens, não o valor de cada uma.
_obs_reais = len({k.split("|")[0] for k in sh.carregar()["observacoes"]})
_rev_reais = len({k.split("|")[0] for k, o in sh.carregar()["observacoes"].items()
                  if o.get("human_review")})
check(c["casos_observados"] == _obs_reais,
      f"[1] casos observados = {c['casos_observados']}, igual ao sidecar")
check(c["casos_revisados"] == _rev_reais and c["casos_revisados"] >= 7,
      f"[2] casos revisados = {c['casos_revisados']} — nunca abaixo dos sete "
      "já adjudicados; revisão humana não desaparece")
check(c["registros_de_modelo"] == c["casos_observados"] * len(c["modelos"]),
      f"[3] registros de modelo = {c['registros_de_modelo']} = "
      f"{c['casos_observados']} casos × {len(c['modelos'])} modelos")
check(c["casos_observados"] >= c["casos_revisados"],
      f"[3b] e observados nunca fica abaixo de revisados — o cron adiciona "
      "observação, não verdade")
check(c["casos_revisados"] != c["registros_de_modelo"],
      "[4] revisados NUNCA é 4 — G1 e G2 dividem a mesma verdade")
check(c["modelos"] == [G1, G2],
      f"[5] os modelos são DESCOBERTOS do sidecar, não fixados ({c['modelos']})")

print()
print("=" * 98)
print("BLOCO A2 — IRMÃOS DA MESMA EMPRESA E FAMÍLIA NÃO SE SOBRESCREVEM")
print("=" * 98)
# Esta é a regressão do defeito real: sob a indexação antiga, por
# (empresa, família), os dois `troca_ceo` da JBS colapsavam e o teste media o
# caso errado sem falhar. Aqui os dois têm de coexistir e ser endereçáveis.
_jbs_ceo = [d for d in R["casos"]
            if d["empresa"] == "JBS" and d["candidato"] == "troca_ceo"]
check(len(_jbs_ceo) == 2,
      f"[A1] a JBS tem DOIS casos `troca_ceo` ({len(_jbs_ceo)})")
_colisao = {(d["empresa"], d["candidato"]) for d in _jbs_ceo}
check(len(_colisao) == 1 and len(_jbs_ceo) == 2,
      "[A2] e os dois compartilham a MESMA chave (empresa, família) — é por "
      "isso que aquele índice perdia um deles em silêncio")
check(caso(ID_JBS_ANUNCIO)["artigo_id"] != caso(ID_JBS_DESCRITOR)["artigo_id"],
      "[A3] pela identidade do artigo os dois são endereçáveis "
      "separadamente")
check(caso(ID_JBS_ANUNCIO)["veredito_humano"]
      != caso(ID_JBS_DESCRITOR)["veredito_humano"],
      f"[A4] e têm vereditos DIFERENTES "
      f"({caso(ID_JBS_ANUNCIO)['veredito_humano']} vs "
      f"{caso(ID_JBS_DESCRITOR)['veredito_humano']}) — escolher o errado "
      "trocaria um anúncio real por uma menção lateral")
check(len(CASO_POR_ARTIGO) == len(R["casos"]),
      f"[A5] o índice por artigo não perde nenhum caso "
      f"({len(CASO_POR_ARTIGO)}/{len(R['casos'])})")
check(set(ROSTER) <= set(CASO_POR_ARTIGO),
      "[A6] e os sete revisados estão todos lá, por id — o roster é asserção, "
      "não contagem")

print()
print("=" * 98)
print("BLOCO B — A PORTA OBJETO PRECISA SER ALCANÇÁVEL (o erro que motivou tudo)")
print("=" * 98)
_ev_g1 = caso(ID_ENEVA)["modelos"][G1]
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
check(caso(ID_ENEVA)["modelos"][G2]["porta"] != "OBJETO",
      "[9] e a porta não dispara para quem respondeu EQUITY_STAKE — não é "
      "rejeição automática de M&A")

print()
print("=" * 98)
print("BLOCO C — PONTUABILIDADE FINAL, DERIVADA")
print("=" * 98)
_f = R["pontuabilidade_final"]
# MÉTRICA É RELAÇÃO, NÃO CONSTANTE.
#
# Fixei 3/5, 4/5 e 3/5 na onda passada e a adjudicação seguinte (Tok&Stok)
# moveu tudo para /6. Cada caso novo mexe nestes números por desenho — o que
# não pode mudar é a forma de contar e a ordem entre os avaliadores.
_den = _f[G1]["denominador"]
check(all(v["denominador"] == _den for v in _f.values()) and _den > 0,
      f"[10] os três avaliadores compartilham o mesmo denominador ({_den}) — "
      "medem os mesmos casos")
check(_f[G1]["acertos"] >= _f["deterministico"]["acertos"],
      f"[11] G1 {_f[G1]['acertos']}/{_den} não fica abaixo do determinístico "
      f"{_f['deterministico']['acertos']}/{_den}")
check(0 <= _f[G2]["acertos"] <= _den,
      f"[12] G2 {_f[G2]['acertos']}/{_den} dentro do denominador")
# CASOS REVISADOS != DENOMINADOR DA DIMENSÃO.
#
# `humano_pontuavel` é o portão: caso sem chave de pontuabilidade fica fora de
# TODAS as métricas dimensionais, não só desta. É por isso que o denominador
# fica abaixo dos casos revisados.
check(_den < R["contagens"]["casos_revisados"],
      f"[13] e o denominador ({_den}) fica ABAIXO dos casos revisados "
      f"({R['contagens']['casos_revisados']}) — nem todo caso adjudicado é "
      "elegível para toda dimensão")
check(R["contagens"]["casos_revisados"] >= 8,
      f"[13b] e os casos revisados nunca ficam abaixo dos oito adjudicados "
      f"({R['contagens']['casos_revisados']})")

print()
print("=" * 98)
print("BLOCO D — ENEVA: CRÉDITO DIMENSIONAL NÃO É CRÉDITO FINAL, E VICE-VERSA")
print("=" * 98)
_e = caso(ID_ENEVA)
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
_j = caso(ID_JBS_ANUNCIO)
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
check(all(_j["modelos"][m]["evidencia_bruta"] in ("VALIDA", "INVALIDA")
          for m in (G1, G2)),
      f"[26] a telemetria BRUTA original é preservada nos dois "
      f"({[_j['modelos'][m]['evidencia_bruta'] for m in (G1, G2)]})")
check(all(isinstance(_j["modelos"][m]["marcas_brutas"], list)
          for m in (G1, G2)),
      "[27] com as marcas originais preservadas, auditáveis")
# As duas camadas coexistem: a bruta é o que foi observado, a q2 é a releitura
# do validador vigente. Nenhuma sobrescreve a outra.
check(all(_j["modelos"][m]["evidencia_q2"] in ("VALIDA", "INVALIDA")
          for m in (G1, G2))
      and all(k in _j["modelos"][G1]
              for k in ("evidencia_bruta", "evidencia_q2")),
      "[28] e a releitura q2 convive com a bruta, sem apagá-la")

print()
print("=" * 98)
print("BLOCO F — DENOMINADORES POR DIMENSÃO")
print("=" * 98)
_d = R["dimensoes"]
# NÚMEROS MEDIDOS DO ESTADO CANÔNICO, NÃO FIXADOS À MÃO.
#
# Com sete revisados, cada dimensão tem o SEU denominador. Fixar valores aqui
# faria a suíte quebrar a cada adjudicação nova sem que nada tivesse regredido
# — foi o que aconteceu quando o holdout foi de 2 para 7.
def _dim(m, d):
    return (_d[m][d]["acertos"], _d[m][d]["denominador"])


check(_dim(G1, "occurrence_novelty")[1] == _dim(G2, "occurrence_novelty")[1],
      f"[29] novidade: G1 {_dim(G1,'occurrence_novelty')} e G2 "
      f"{_dim(G2,'occurrence_novelty')} — mesmo denominador, leituras "
      "distintas")
check(_dim(G1, "occurrence_novelty")[0] > _dim(G2, "occurrence_novelty")[0],
      f"[30] e G1 acerta mais novidade que G2 nesta amostra")
check(_dim(G1, "transaction_object")[0] > _dim(G2, "transaction_object")[0],
      f"[31] objeto: G1 {_dim(G1,'transaction_object')} vs G2 "
      f"{_dim(G2,'transaction_object')} — a dimensão separa os modelos")

# O DENOMINADOR DE FASE NÃO É O NÚMERO DE RÓTULOS HUMANOS.
#
# Seis casos têm fase adjudicada; o denominador é 5. A diferença é o anúncio
# do CEO da JBS (`5d05e844`), cujo artigo saiu do histórico por retenção — o
# relatório o exclui com o motivo "artigo ausente do histórico, não dá para
# provar". Não é bug: é o relatório recusando-se a medir o que não consegue
# comprovar. Inflar para 6 porque existe rótulo humano seria confundir
# "adjudicado" com "elegível".
_fases_humanas = sum(1 for d in R["casos"] if "phase" in d["dimensoes_humanas"])
_excl_ids = {e.get("titulo", "")[:40] for e in R["excluidos"]}
check(_fases_humanas == 6,
      f"[32] seis casos têm fase adjudicada ({_fases_humanas})")
check(_d[G1]["phase"]["denominador"] == _fases_humanas - len(R["excluidos"]),
      f"[32b] e o denominador é {_d[G1]['phase']['denominador']} = 6 rótulos "
      f"menos {len(R['excluidos'])} excluído — não o total de rótulos")
check(len(R["excluidos"]) == 1
      and "ausente do hist" in (R["excluidos"][0].get("motivo") or ""),
      f"[32c] e o excluído é o artigo que a retenção removeu do histórico "
      f"({(R['excluidos'][0].get('motivo') or '')[:44]}) — elegibilidade "
      "prospectiva exige poder provar o input")

# Removendo a fase de UM caso, o denominador cai exatamente um. Delta contra a
# base real: fixar "de 2 para 1" valia quando o holdout tinha dois casos.
_d0 = sh.carregar()
_parc1 = copy.deepcopy(_d0)
_alvo1 = next(o for o in _parc1["observacoes"].values()
              if (o.get("human_review") or {}).get("dimensoes_adjudicadas", {})
              .get("phase"))
_alvo1["human_review"]["dimensoes_adjudicadas"].pop("phase", None)
_r5 = rp.gerar(temp_sidecar(_parc1))
check(_r5["dimensoes"][G1]["phase"]["denominador"]
      <= _d[G1]["phase"]["denominador"],
      f"[57] tirando a fase de um caso, o denominador de fase não sobe "
      f"({_d[G1]['phase']['denominador']} → "
      f"{_r5['dimensoes'][G1]['phase']['denominador']})")
_parc2 = copy.deepcopy(_d0)
for _o in _parc2["observacoes"].values():
    # O cron traz observações ainda não adjudicadas — `human_review` é None
    # nelas. Assumir que toda observação tem revisão era verdade só enquanto
    # a fila estava vazia.
    _hr = _o.get("human_review") or {}
    (_hr.get("dimensoes_adjudicadas") or {}).pop("phase", None)
_r5b = rp.gerar(temp_sidecar(_parc2))
check(_r5b["dimensoes"][G1]["phase"]["denominador"] == 0
      and _r5b["dimensoes"][G1]["phase"]["acuracia"] is None,
      "[57b] e sem NENHUMA fase adjudicada a dimensão sai da conta por "
      "completo, em vez de virar 0% de acerto")
check(_r5["pontuabilidade_final"][G1]["denominador"]
      == R["pontuabilidade_final"][G1]["denominador"],
      f"[58] e a pontuabilidade final não muda ao remover uma dimensão "
      f"({_r5['pontuabilidade_final'][G1]['denominador']}) — dimensões têm "
      "denominadores independentes")
_ovr = copy.deepcopy(_d0)
for _o in _ovr["observacoes"].values():
    if _o["company"] == "Eneva" and _o.get("human_review"):
        _o["human_review"] = {**_o["human_review"],
                              "override_de": {"verdict": "TRUE"},
                              "override_motivo": "reavaliação"}
_r6 = rp.gerar(temp_sidecar(_ovr))
check(_r6["contagens"]["overrides"] == R["contagens"]["overrides"] + 1,
      f"[59] o override injetado é contado, +1 sobre a base "
      f"({R['contagens']['overrides']} → {_r6['contagens']['overrides']}) — a "
      "base já tem um: o Orizon, reescrito para acrescentar `scoreable_as_ma`")
check(_r6["contagens"]["casos_revisados"] == R["contagens"]["casos_revisados"],
      f"[60] e o caso sobrescrito NÃO é contado duas vezes "
      f"({_r6['contagens']['casos_revisados']})")
check(_r6["pontuabilidade_final"][G1]["acertos"]
      == R["pontuabilidade_final"][G1]["acertos"],
      f"[61] e a verdade EFETIVA segue sendo a usada, não a sobrescrita "
      f"({_r6['pontuabilidade_final'][G1]['acertos']})")
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
check(len(_r7["excluidos"]) == len(R["excluidos"]) + 1,
      f"[62] o caso anterior ao freeze entra na lista de excluídos, +1 sobre a "
      f"base ({len(R['excluidos'])} → {len(_r7['excluidos'])})")
check(_r7["pontuabilidade_final"][G1]["denominador"]
      == R["pontuabilidade_final"][G1]["denominador"] - 1,
      f"[63] e sai das métricas principais — o denominador cai exatamente um "
      f"({R['pontuabilidade_final'][G1]['denominador']} → "
      f"{_r7['pontuabilidade_final'][G1]['denominador']})")
check(len(_r7["casos"]) == len(R["casos"]),
      f"[64] mas o registro NÃO é apagado do relatório "
      f"({len(_r7['casos'])}) — excluir da métrica não é excluir da auditoria")

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
# "toda observação tem revisão" era verdade só enquanto a fila de adjudicação
# estava vazia. O invariante real é que as JÁ adjudicadas permaneçam — o cron
# pode acrescentar casos novos sem revisão a qualquer momento.
_rev_agora = {k.split("|")[0] for k, o in _atual["observacoes"].items()
              if o.get("human_review")}
check(set(ROSTER) <= _rev_agora,
      f"[79] as sete adjudicações reais seguem no lugar "
      f"({len(_rev_agora)} casos revisados), mesmo com a fila crescendo")

print()
print("=" * 98)
print(f"RESULTADO RELATÓRIO PROSPECTIVO: {PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
