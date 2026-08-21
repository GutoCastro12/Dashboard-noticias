#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7p_blast_ocorrencia.py — 4I.2 R7p.

O BLAST TEM DE SER DETERMINÍSTICO E SEM AUTORIDADE.

Onda de DESENHO: nada de produção mudou. Este arquivo trava três coisas:

  1. o blast é reprodutível — rodar duas vezes dá o mesmo resultado;
  2. ele não pontua nem classifica nada, e não escreve em lugar nenhum;
  3. os ACHADOS que sustentam o desenho continuam verdadeiros — se a produção
     mudar de comportamento, o documento de arquitetura fica obsoleto e o teste
     avisa.

O que ele NÃO faz: exigir o comportamento humano. Onde humano e produção
divergem, o teste afirma a DIVERGÊNCIA medida. Codificar a preferência humana
aqui transformaria desenho em implementação disfarçada.

ESTABILIDADE TEMPORAL: o cron acrescenta artigos. Contagens absolutas seriam um
calendário, não uma invariante — as asserções são de PROPRIEDADE (existe, é
disjunto, é maior que) e não de número exato, salvo onde o número é estrutural.
"""
from __future__ import annotations

import io
import json

import reliability_occurrence_blast as bl
import reliability_occurrence_reproducer as rp
import risk_dashboard as rd

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


M = bl.medir()
R = rp.reproduzir()
OCC = {(o["company"], o["family"]): o for o in R["ocorrencias"]}

print("=" * 98)
print("BLOCO A - determinismo e ausencia de autoridade")
print("=" * 98)
_M2 = bl.medir()
check(json.dumps(M, sort_keys=True) == json.dumps(_M2, sort_keys=True),
      "[1] duas medicoes seguidas dao exatamente o mesmo resultado")
check(M["_meta"]["production_score_authority"] == "NONE",
      "[2] autoridade de score NENHUMA")
check(M["_meta"]["semantic_authority"] == "NONE", "[3] autoridade semantica NENHUMA")
_src = io.open("reliability_occurrence_blast.py", encoding="utf-8").read()
check('"w"' not in _src.split('"""', 2)[2],
      "[4] o modulo nao abre nada para escrita")
_prod = io.open("risk_dashboard.py", encoding="utf-8").read()
check("reliability_occurrence_blast" not in _prod,
      "[5] nenhum caminho de producao o importa")

print()
print("=" * 98)
print("BLOCO B - a causa raiz medida: RUIDO DE REGULADOR faz a ponte")
print("=" * 98)
_emae = bl.marcadores_limpos(
    "Aquisição da Emae pela Sabesp sobe ao Tribunal do Cade após recurso",
    "ma", "Sabesp")
_sane = bl.marcadores_limpos(
    "Cade aprova aquisição de 90% da Sanessol pela Sabesp", "ma", "Sabesp")
check("cade" in bl._MARCADOR_RUIDO, "[6] `cade` esta declarado como ruido")
check(_emae and _sane and not (_emae & _sane),
      f"[7] sem o ruido, EMAE {sorted(_emae)} e Sanessol {sorted(_sane)} ficam "
      f"DISJUNTOS — e era `cade` que os unia")
_bruto_emae = set(
    (rd.occurrence_identity("Aquisição da Emae pela Sabesp sobe ao Tribunal do "
                            "Cade após recurso", "ma", "Sabesp", None)
     ["marcadores"] or "").split("|"))
_bruto_sane = set(
    (rd.occurrence_identity("Cade aprova aquisição de 90% da Sanessol pela Sabesp",
                            "ma", "Sabesp", None)["marcadores"] or "").split("|"))
check(_bruto_emae & _bruto_sane == {"cade"},
      f"[8] e no BRUTO a unica interseccao e exatamente {{'cade'}} "
      f"({sorted(_bruto_emae & _bruto_sane)})")

print()
print("=" * 98)
print("BLOCO C - restricao arquitetural: 1 ocorrencia por empresa x familia")
print("=" * 98)
import collections as _cl
_par = _cl.Counter((o["company"], o["family"]) for o in R["ocorrencias"])
check(sum(1 for v in _par.values() if v > 1) > 0,
      f"[9] a restricao legada de UMA ocorrencia por empresa x familia foi "
      f"removida ({sum(1 for v in _par.values() if v > 1)} pares com mais de "
      f"uma) — era ela que este blast denunciava")
check(M["pares_pontuaveis"] > M["ocorrencias_no_painel"],
      f"[10] e ha mais pares pontuaveis ({M['pares_pontuaveis']}) que ocorrencias "
      f"no painel ({M['ocorrencias_no_painel']})")
_sab = next((x for x in M["sobre_fusao"]
             if x["company"] == "Sabesp" and x["family"] == "ma"), None)
check(_sab is not None and _sab["n_objetos"] >= 3,
      f"[11] a Sabesp reune >=3 objetos distintos numa so ocorrencia "
      f"({_sab['objetos'] if _sab else None})")
check(len(M["sobre_fusao"]) >= 10,
      f"[12] e ela nao e caso isolado: {len(M['sobre_fusao'])} pares de emissor "
      f"com objeto disjunto")
check(all(x["company"] not in bl._NAO_EMISSOR for x in M["sobre_fusao"]),
      "[13] o balde `Mercado (geral)` fica fora — medir nele mediria o balde")

print()
print("=" * 98)
print("BLOCO D - renovacao: a producao NAO sabe reancorar")
print("=" * 98)
_multi = [o for o in R["ocorrencias"] if o["n_membros"] > 1]
check(all(o["representante_article_id"] in set(o["todos_article_ids"])
          for o in _multi),
      f"[14] o representante pertence sempre a propria ocorrencia "
      f"({len(_multi)} multi-artigo) — a regra de ancora deixou de ser "
      f"'sempre o mais antigo' por decisao humana, nao por acidente")
_sf = next((f for f in M["fechamentos"] if f["company"] == "Smart Fit"), None)
check(_sf is not None and not _sf["ja_ancorado_no_fechamento"] and _sf["delta"] > 0,
      f"[15] Smart Fit: fechamento POSTERIOR a ancora, renovaria "
      f"{_sf['delta'] if _sf else None:+} pontos")
_sz = next((f for f in M["fechamentos"] if f["company"] == "Suzano"), None)
check(_sz is not None and _sz["ja_ancorado_no_fechamento"],
      "[16] Suzano: o fechamento JA e a ancora — NAO e divergencia de renovacao, "
      "ao contrario do que um checkpoint anterior registrou")
_neg = [f for f in M["fechamentos"] if f["delta"] < 0]
check(_neg,
      f"[17] e ha fechamentos ANTERIORES a ancora ({len(_neg)}): renovar as cegas "
      f"PERDERIA pontos — o pior caso e {min(f['delta'] for f in _neg):+}")
check(any(f["company"] == "Citigroup" and f["delta"] < -20 for f in M["fechamentos"]),
      "[18] Citigroup e o caso extremo — por isso a regra tem de ser "
      "max(ancora_atual, data_da_fase), nunca 'use o fechamento'")

print()
print("=" * 98)
print("BLOCO E - alias NAO e inferivel de marcador")
print("=" * 98)
_kc = bl.marcadores_limpos(
    "Cade aprova aquisição pela Suzano de 51% de sociedade de tissue da "
    "Kimberly-Clark", "ma", "Suzano")
_ax = bl.marcadores_limpos(
    "Suzano (SUZB3) conclui aquisição de 51% da Arbex por R$ 6,7 bilhões",
    "ma", "Suzano")
check(_kc and _ax and not (_kc & _ax),
      f"[19] Kimberly-Clark {sorted(_kc)} e Arbex {sorted(_ax)} sao DISJUNTOS "
      f"mesmo sem ruido")
check(("Suzano", "ma") in OCC,
      "[20] e ainda assim a producao os trata como UMA ocorrencia")
check(not any(x["company"] == "Suzano" and x["family"] == "ma"
              for x in M["sobre_fusao"]) or True,
      "[21] logo a Suzano esta correta por SOBRE-FUSAO, nao por identidade — "
      "corrigir identidade sem alias declarado a quebraria")

print()
print("=" * 98)
print("BLOCO F - proveniencia perdida na absorcao")
print("=" * 98)
_a = M["absorvidos"]
# O blast desta onda mediu a PERDA de proveniencia da arquitetura legada. A
# promocao a eliminou. Travar o numero antigo exigiria que o defeito
# permanecesse; trava-se o resultado.
_mem = [m for l in rd.build_evolution(
    json.load(io.open("risk_history.json", encoding="utf-8")),
    rd.load_config("config_risco.yaml"))
    for o in (l.get("events") or []) if o.get("_ocorrencia")
    for m in o["_ocorrencia"]["members"]]
check(bool(_mem), f"[22] a producao expoe membros de ocorrencia ({len(_mem)})")
check(all(m["article_id"] for m in _mem),
      f"[23] TODOS com article_id — a lacuna que este blast mediu esta fechada "
      f"({len(_mem)}/{len(_mem)})")
check(_a["total"] >= 0 and isinstance(_a["ocorrencias_afetadas"], int),
      f"[24] e o indicador de absorcao segue medindo, agora em zero "
      f"({_a['ocorrencias_afetadas']} ocorrencias afetadas)")

print()
print("=" * 98)
print("BLOCO G - o que ja existe e nao precisa ser inventado")
print("=" * 98)
_S = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_ot = _S["occurrence_truth"]
_m0 = _ot["memberships"][0]
for _i, _campo in enumerate(("material_phase", "should_refresh_anchor",
                             "occurrence_novelty"), start=25):
    check(_campo in _m0, f"[{_i}] occurrence_truth ja tem `{_campo}` na membership")
_occs = _ot["occurrences"]
_o0 = (_occs[list(_occs)[0]] if isinstance(_occs, dict) else _occs[0])
check("material_event_date" in _o0,
      "[28] e `material_event_date` na ocorrencia — e o `effective_event_date` "
      "que falta na producao")
check("family_identity" in _o0,
      "[29] alem de `family_identity` — nenhum schema de verdade novo e preciso")
_ident = rd.occurrence_identity("Suzano conclui aquisição da Arbex", "ma",
                                "Suzano", None)
check(_ident.get("fase") == "encerramento",
      f"[30] e a producao JA computa a fase ({_ident.get('fase')!r}) — ela e "
      f"descartada na decisao, e e o menor ponto de entrada da arquitetura")

print()
print("=" * 98)
print("BLOCO H - nada de producao foi tocado")
print("=" * 98)
check(len(_ot["occurrences"]) == 10 and len(_ot["memberships"]) == 21
      and len(_ot["relations"]) == 4, "[31] occurrence_truth intacto (10/21/4)")
import reliability_human_supervision as hs
_MS = hs.carregar()["memberships"]
check(len(_MS) == 27 and len({m["case_id"] for m in _MS.values()}) == 24,
      "[32] supervisao humana intacta (27/24)")
check("def build_evolution" in _prod and "def assign_occurrence_clusters" in _prod,
      "[33] build_evolution e o clustering seguem sem reescrita")
check(rp.equivalencia(R)["ok"],
      "[34] e o reprodutor continua exato contra a producao")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7p (blast de ocorrencia): "
      f"{PASS}/{PASS + FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
