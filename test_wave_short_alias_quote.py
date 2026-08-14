#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_short_alias_quote.py — nome curto não é citação trivial.

O QUE ACONTECEU

No segundo caso prospectivo do Contract V2 — JBS / `troca_ceo` — os dois
modelos responderam `subject_quote = "JBS"`. A manchete é:

    "Wesley Batista Filho será o novo CEO Global da JBS a partir de janeiro
     de 2027"

"JBS" está lá, literalmente. Ainda assim o validador devolveu
`H1_QUOTE_INEXISTENTE` e marcou os dois registros como evidência inválida.

A causa não era o modelo: `quote_valida` rejeitava toda citação com menos de 4
caracteres. O piso existia por um motivo legítimo — impedir que "de", "of" ou
"a" passassem como evidência só por aparecerem em qualquer texto —, mas
confundia DUAS coisas diferentes: comprimento e trivialidade.

O QUE A CORREÇÃO SEPARA

Citação longa segue idêntica à v1. Citação curta passa a valer quando é um
TOKEN COMPLETO e não é palavra funcional. É uma regra sobre a natureza do
token, não sobre nomes: nenhuma empresa aparece no validador.

POR QUE ISSO IMPORTA MAIS QUE UM CASO

Nove das 164 empresas monitoradas têm nome com menos de 4 caracteres. Para
todas elas, qualquer citação do nome nu era reprovada — o que contaminava a
métrica de validade de evidência do estudo prospectivo justamente nas empresas
de nome curto. Não afetava score nem semântica de produção: este validador é
infraestrutura de avaliação, e este arquivo também prova isso.
"""
from __future__ import annotations

import inspect
import io
import json
import re

import reliability_pilot_validators as pv
import reliability_evidence_reeval as rr

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


JBS_TXT = ("Wesley Batista Filho será o novo CEO Global da JBS a partir de "
           "janeiro de 2027")

print("=" * 98)
print("BLOCO A — O CASO EXATO QUE ORIGINOU A CORREÇÃO")
print("=" * 98)
check(pv.quote_valida("JBS", JBS_TXT),
      "[1] subject_quote 'JBS' na manchete real do case #2: VÁLIDA")
check(not pv.quote_valida_v1("JBS", JBS_TXT),
      "[2] e a v1 continua reprovando — o passado não foi reescrito")
check(pv.QUOTE_VALIDATOR_VERSION == "r7ba.q2",
      f"[3] a correção tem versão própria ({pv.QUOTE_VALIDATOR_VERSION})")

print()
print("=" * 98)
print("BLOCO B — OS NOVE NOMES CURTOS DA WATCHLIST")
print("=" * 98)
CURTOS = ["B3", "BRF", "JBS", "WEG", "SMU", "YPF", "AGV", "Jgp", "Spx"]
for _n, nome in enumerate(CURTOS, start=4):
    txt = f"A companhia {nome} divulgou fato relevante nesta quinta-feira."
    check(pv.quote_valida(nome, txt) and not pv.quote_valida_v1(nome, txt),
          f"[{_n}] '{nome}' como token literal: v1 reprovava, v2 aceita")

print()
print("=" * 98)
print("BLOCO C — TOKENS TRIVIAIS SEGUEM BLOQUEADOS")
print("=" * 98)
TRIVIAIS = [("de", "o modelo de negócio da companhia"),
            ("da", "a fábrica da empresa foi vendida"),
            ("do", "o resultado do trimestre veio acima"),
            ("em", "a operação em curso segue"),
            ("no", "no primeiro trimestre houve alta"),
            ("na", "na assembleia os acionistas aprovaram"),
            ("a", "a companhia informou o mercado"),
            ("o", "o conselho aprovou a proposta"),
            ("e", "receita e lucro subiram"),
            ("of", "the board of directors approved"),
            ("to", "the plan to expand was approved"),
            ("in", "growth in the quarter was strong"),
            ("an", "an agreement was signed"),
            ("is", "the company is expanding"),
            ("the", "the company announced results"),
            ("and", "revenue and profit rose"),
            ("el", "el consejo aprobó la propuesta"),
            ("la", "la empresa informó al mercado")]
for _n, (q, txt) in enumerate(TRIVIAIS, start=13):
    check(not pv.quote_valida(q, txt),
          f"[{_n}] {q!r} presente no texto mas trivial: segue INVÁLIDA")

print()
print("=" * 98)
print("BLOCO D — SUBSTRING ACIDENTAL NÃO VALIDA")
print("=" * 98)
SUBSTR = [("B3", "o ticker b3sa3 caiu no pregão de ontem"),
          ("BRF", "a subsidiaria brfoods nao existe sob esse nome"),
          ("JBS", "o arquivo jbsdata.csv foi processado"),
          ("WEG", "a palavra wegener aparece no relatorio"),
          ("Spx", "o time spxracing patrocina o evento"),
          ("YPF", "o codigo ypfx99 identifica o lote")]
for _n, (q, txt) in enumerate(SUBSTR, start=31):
    check(not pv.quote_valida(q, txt),
          f"[{_n}] {q!r} dentro de token maior: INVÁLIDA (não fecha token)")

print()
print("=" * 98)
print("BLOCO E — NORMALIZAÇÃO E PONTUAÇÃO DE BORDA")
print("=" * 98)
check(pv.quote_valida("JBS", "a controladora (JBS) informou o mercado"),
      "[37] parênteses ao redor do alias no texto não impedem o casamento")
check(pv.quote_valida("(JBS)", "a controladora JBS informou o mercado"),
      "[38] parênteses NA CITAÇÃO são normalizados fora")
check(pv.quote_valida("jbs", JBS_TXT) and pv.quote_valida("JbS", JBS_TXT),
      "[39] caixa é indiferente, como já era na v1")
check(pv.quote_valida("B3", "a B3, bolsa brasileira, comunicou o fato"),
      "[40] vírgula colada ao alias não quebra o token")
check(pv.quote_valida("YPF", "acordo entre YPF e Petrobras foi assinado"),
      "[41] alias entre espaços simples")
check(not pv.quote_valida("XYZ", JBS_TXT),
      "[42] alias curto AUSENTE do texto segue inválido — nada foi afrouxado")

print()
print("=" * 98)
print("BLOCO F — CITAÇÃO LONGA: COMPORTAMENTO IDÊNTICO À v1")
print("=" * 98)
LONGAS = [("será o novo CEO Global", JBS_TXT, True),
          ("Wesley Batista Filho", JBS_TXT, True),
          ("será o novo CFO Global", JBS_TXT, False),
          ("aquisição de participação", JBS_TXT, False),
          ("janeiro de 2027", JBS_TXT, True)]
for _n, (q, txt, esp) in enumerate(LONGAS, start=43):
    a, b = pv.quote_valida_v1(q, txt), pv.quote_valida(q, txt)
    check(a == b == esp,
          f"[{_n}] {q[:34]!r}: v1={a} v2={b} (esperado {esp})")
check(pv.quote_valida(None, JBS_TXT) and pv.quote_valida("", JBS_TXT),
      "[48] ausência declarada continua não sendo alucinação")

print()
print("=" * 98)
print("BLOCO G — REGRA GERAL, NÃO REMENDO DE EMPRESA")
print("=" * 98)
_fonte = inspect.getsource(pv.quote_valida) + inspect.getsource(pv._token_completo)
_cod = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "",
              "\n".join(l.split("#")[0] for l in _fonte.splitlines()))
for _n, nome in enumerate(CURTOS, start=49):
    check(nome.lower() not in _cod.lower(),
          f"[{_n}] o validador não menciona '{nome}'")
check(not any(n.lower() in {t.lower() for t in pv._QUOTE_TRIVIAL}
              for n in CURTOS),
      "[58] nenhum alias real de emissor está na lista de triviais")
check(all(len(t) <= 3 for t in pv._QUOTE_TRIVIAL),
      "[59] a lista de triviais só contém tokens de 1–3 chars — acima disso "
      "a regra nem consulta")

print()
print("=" * 98)
print("BLOCO H — INFRAESTRUTURA DE AVALIAÇÃO, SEM AUTORIDADE SOBRE SCORE")
print("=" * 98)
for _n, mod in enumerate(("risk_dashboard.py", "semantic_audit.py"), start=60):
    src = io.open(mod, encoding="utf-8").read()
    check("pilot_validators" not in src and "evidence_reeval" not in src,
          f"[{_n}] {mod} não importa o validador nem a reavaliação")
check("risk_history" not in inspect.getsource(pv),
      "[62] o validador não lê histórico de produção")
_rr = inspect.getsource(rr)
check("gravar" not in _rr and "write" not in _rr.lower().replace("written", ""),
      "[63] a reavaliação não tem caminho de escrita")

print()
print("=" * 98)
print("BLOCO I — REAVALIAÇÃO DERIVADA SOBRE O CORPUS REAL")
print("=" * 98)
res = rr.carregar_e_reavaliar()
check(res["valido_para_invalido"] == 0,
      f"[64] nenhuma evidência VÁLIDA virou inválida ({res['valido_para_invalido']})")
check(res["invalido_para_valido"] == 2,
      f"[65] exatamente 2 viraram válidas ({res['invalido_para_valido']})")
_mud = [(r["empresa"], c["campo"], c["quote"])
        for r in res["registros"] for c in r["mudancas"]]
check({(e, c) for e, c, _ in _mud} == {("JBS", "subject_quote")},
      f"[66] e as duas são JBS/subject_quote ({sorted(set(_mud))})")
check(all(q == "JBS" for _, _, q in _mud),
      "[67] a citação em questão é exatamente 'JBS'")
check(not res["sem_texto_reconstruivel"],
      f"[68] todo registro teve o texto reconstruído "
      f"({res['sem_texto_reconstruivel']})")
check(res["eventos_validos_v1"] == 2 and res["eventos_validos_v2"] == 4,
      f"[69] validade por evento: v1 {res['eventos_validos_v1']}/4 → "
      f"v2 {res['eventos_validos_v2']}/4")

print()
print("=" * 98)
print("BLOCO J — O TEXTO REAVALIADO É O MESMO QUE O VALIDADOR VIU")
print("=" * 98)
_run = io.open("semantic_v2_shadow_run.py", encoding="utf-8").read()
check('(art.get("summary") or art.get("title") or "")[:4000]' in _run,
      "[70] o runner constrói o texto como (summary or title)[:4000]")
check(inspect.getsource(rr.texto_do_artigo).count("summary") == 1
      and "4000" in inspect.getsource(rr) and rr.LIMITE_TEXTO == 4000,
      "[71] e a reavaliação usa exatamente a mesma expressão — se uma mudar, "
      "este teste quebra")
_h = json.load(io.open("risk_history.json", encoding="utf-8"))
_sh = json.load(io.open("risk_semantic_v2_shadow.json", encoding="utf-8"))
_o = [o for o in _sh["observacoes"].values() if o["company"] == "JBS"][0]
check(rr.texto_do_artigo(_h["articles"][_o["url"]]).strip().startswith(
      "Wesley Batista Filho"),
      "[72] o texto reconstruído para o case #2 começa pela manchete real")

print()
print("=" * 98)
print("BLOCO K — VERDADE HUMANA E SAÍDAS DOS MODELOS INTACTAS")
print("=" * 98)
_casos = {}
for o in _sh["observacoes"].values():
    _casos.setdefault((o["company"], o["candidate_event"]), []).append(o)
check(len(_casos) == 2 and all(len(v) == 2 for v in _casos.values()),
      f"[73] seguem 2 casos × 2 modelos ({sorted(_casos)})")
_en = _casos[("Eneva", "ma")][0]["human_review"]
check(_en["verdict"] == "FALSE_SCOPE"
      and _en["dimensoes_adjudicadas"]["occurrence_novelty"] == "FOLLOW_UP",
      "[74] case #1 intacto: FALSE_SCOPE / FOLLOW_UP")
_jb = _casos[("JBS", "troca_ceo")][0]["human_review"]
check(_jb["verdict"] == "TRUE_NEW_ANNOUNCEMENT"
      and _jb["dimensoes_adjudicadas"]["occurrence_novelty"] == "NEW_OCCURRENCE",
      "[75] case #2 intacto: TRUE_NEW_ANNOUNCEMENT / NEW_OCCURRENCE")
check(all(o["evidencia"]["aceitos"] == 0
          for o in _casos[("JBS", "troca_ceo")]),
      "[76] a telemetria ORIGINAL do JBS segue registrando aceitos=0 — a "
      "correção não reescreveu o que foi observado")
check(all("H1_QUOTE_INEXISTENTE" in o["evidencia"]["eventos"][0]["_validacao"]["marcas"]
          for o in _casos[("JBS", "troca_ceo")]),
      "[77] e a marca H1 original continua lá, auditável")

print()
print("=" * 98)
print(f"RESULTADO ALIAS CURTO / VALIDADOR DE QUOTE: {PASS}/{PASS + FAIL} "
      f"checagens passaram")
print("=" * 98)
raise SystemExit(1 if FAIL else 0)
