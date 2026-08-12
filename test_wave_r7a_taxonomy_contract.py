#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7a_taxonomy_contract.py — 4I.2 R7a.

O INVENTÁRIO NÃO PODE MENTIR NEM ENVELHECER EM SILÊNCIO.

Duas formas de o instrumento apodrecer, ambas já vistas neste projeto:

1. COPIAR em vez de IMPORTAR. Relatórios antigos descreviam listas de eventos
   que o código não usava mais. Aqui o escopo de cada regra vem dos MESMOS
   objetos que `resolve_article_semantics` consulta, e há teste para isso.

2. CONFUNDIR SENTINELA COM CONCLUSÃO. `confirmation_level` nasce valendo
   "indefinido" em 82% dos pares; a primeira versão deste instrumento contava
   isso como fase resolvida e reportava 100% de cobertura. É o §7 do próprio
   brief — ter regra não é ter cobertura — aplicado ao medidor.

Tudo shadow. Produção não muda.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import risk_dashboard as rd
import semantic_audit as sa
import reliability_taxonomy_inventory as inv
import reliability_universal_assessment as uea

PASS = FAIL = 0
cfg = rd.load_config("config_risco.yaml")
AL = sa._aliases_map(cfg)
KWS = sa._keywords_por_evento(cfg)
SRC = io.open("semantic_audit.py", encoding="utf-8").read()


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ FALHOU: {label}")


def decidir(titulo, empresa, evs, resumo="", shadow=False):
    def _go():
        return sa.resolve_article_semantics(titulo, resumo, empresa, evs, AL,
                                            article_year=2026, source_domain="ex.com",
                                            keywords_por_evento=KWS, country="")
    if shadow:
        with sa.shadow_fraud_roles():
            return _go()
    return _go()


def u_de(titulo, empresa, ev, resumo="", shadow=False):
    r = decidir(titulo, empresa, [ev], resumo, shadow=shadow)
    d = next(x for x in r["decisoes"] if x["event_id"] == ev)
    return uea.montar(d, identity="t", texto=titulo)


print("=" * 100)
print("BLOCO A — a taxonomia é derivada do config, nunca deste arquivo")
print("=" * 100)
EV = inv.taxonomia(cfg)
ids = {e["event_id"] for e in EV}
check(len(EV) > 40, f"[1] taxonomia lida do config ({len(EV)} eventos)")
check(all(e["event_id"] and e["severity"] is not None for e in EV),
      "[2] todo evento tem id e severidade")
_raw = io.open("config_risco.yaml", encoding="utf-8").read()
check(all(f'id: {e["event_id"]}' in _raw or f'"{e["event_id"]}"' in _raw
          or f"'{e['event_id']}'" in _raw or e["event_id"] in _raw for e in EV),
      "[3] todo id inventariado existe no config")
_src_inv = io.open("reliability_taxonomy_inventory.py", encoding="utf-8").read()
_lista_literal = sum(1 for e in ids if f'"{e}"' in _src_inv)
check(_lista_literal <= 12,
      f"[4] o inventário não embute a taxonomia (só {_lista_literal} ids citados, "
      f"todos como escopo de regra)")

print()
print("=" * 100)
print("BLOCO B — o mapa de regras acompanha o runtime")
print("=" * 100)
check(all(r in SRC for r in inv.REGRAS),
      "[5] toda regra mapeada existe em semantic_audit.py")
import re  # noqa: E402
_no_codigo = set(re.findall(r'attribution_rule="(R_[A-Z_]+)"', SRC))
_faltando = _no_codigo - set(inv.REGRAS)
check(not _faltando, f"[6] nenhuma regra do runtime ficou fora do mapa ({_faltando})")
check(inv._escopo(inv._MA) == set(sa.EVENTOS_MA),
      "[7] escopo de M&A é o objeto do runtime, não uma cópia")
check(inv._escopo(inv._FRAUDE) == set(sa.EVENTOS_FRAUDE),
      "[8] escopo de fraude é o objeto do runtime")
check(inv._escopo(inv._ESTRITO) == set(sa.EVENTOS_SUJEITO_ESTRITO),
      "[9] escopo de sujeito estrito é o objeto do runtime")
_orig = sa.EVENTOS_MA
try:
    sa.EVENTOS_MA = {"ma", "__evento_inventado__"}
    check("R_MA_PAPEL_VENDEDOR" in inv.regras_do_evento("__evento_inventado__")
          and "R_MA_PAPEL_VENDEDOR" not in inv.regras_do_evento("__nunca_existiu__"),
          "[10] renomear um evento no runtime propaga para a matriz")
finally:
    sa.EVENTOS_MA = _orig
check(all(d in inv.DIMENSOES for r in inv.REGRAS for d in inv.REGRAS[r][0]),
      "[11] toda dimensão declarada é uma das onze do contrato")
check(inv.GENERICAS <= set(inv.REGRAS),
      "[12] as regras genéricas fazem parte do mapa")

print()
print("=" * 100)
print("BLOCO C — cobertura exige exercício, não existência de regex")
print("=" * 100)
_h = json.load(io.open("risk_history.json", encoding="utf-8"))
CEN = inv.censo(_h)
MAT = inv.matriz(EV, CEN)
check(set(MAT) == ids, "[13] a matriz cobre todos os eventos da taxonomia")
check(all(set(l) == set(inv.DIMENSOES) for l in MAT.values()),
      "[14] toda linha responde às dez colunas do contrato")
_niveis = {v["nivel"] for l in MAT.values() for v in l.values()}
check(_niveis <= {inv.STRONG, inv.PARTIAL, inv.INDIRECT, inv.NONE},
      f"[15] só níveis permitidos ({sorted(_niveis)})")
_strong_sem_exercicio = [
    (e, d) for e, l in MAT.items() for d, v in l.items()
    if v["nivel"] == inv.STRONG
    and not any(CEN["regra_por_evento"].get(e, {}).get(r) for r in v["regras"])]
check(not _strong_sem_exercicio,
      f"[16] STRONG sempre tem regra exercitada NAQUELE evento ({_strong_sem_exercicio[:2]})")
_generica_strong = [(e, d) for e, l in MAT.items() for d, v in l.items()
                    if v["nivel"] in (inv.STRONG, inv.PARTIAL)
                    and set(v["regras"]) <= inv.GENERICAS]
check(not _generica_strong,
      f"[17] regra genérica nunca vira cobertura própria ({_generica_strong[:2]})")
_sem_regra = [e for e, l in MAT.items()
              if all(v["nivel"] == inv.NONE for v in l.values())]
check(all(not CEN["candidatos"].get(e) for e in _sem_regra),
      "[18] evento sem nenhuma regra também não tem candidato ativo")
check(inv.maturidade({d: {"nivel": inv.NONE} for d in inv.DIMENSOES}) == "KEYWORD_ONLY",
      "[19] linha vazia é KEYWORD_ONLY")
check(inv.maturidade({d: {"nivel": inv.STRONG} for d in inv.DIMENSOES}) == "CONTRACT_RICH",
      "[20] linha cheia é CONTRACT_RICH")

print()
print("=" * 100)
print("BLOCO D — o censo lê os três campos, que têm formatos diferentes")
print("=" * 100)
_fake = {"articles": {"u": {
    "events_by_company": {"A": ["ma"]},
    "informational_events_by_company": {"A": [{"event_id": "falencia"}]},
    "context_events_by_company": {"A": [{"event_id": "default"}]},
    "event_assessments": [{"event_id": "ma", "monitored_company": "A",
                           "attribution_rule": "R_MA_LEGITIMO"}],
    "semantic_discards": [{"event_id": "fraude", "empresa": "A",
                           "regra": "R_VITIMA_NAO_E_AUTORA_DA_FRAUDE"}]}}}
_c = inv.censo(_fake)
check(_c["pontuaveis"]["ma"] == 1, "[21] events_by_company lido como lista de ids")
check(_c["informativos"]["falencia"] == 1,
      "[22] informational_events_by_company lido como lista de dicts")
check(_c["contexto"]["default"] == 1,
      "[23] context_events_by_company lido como lista de dicts")
check(_c["regras_exercitadas"]["R_MA_LEGITIMO"] == 1
      and _c["regras_exercitadas"]["R_VITIMA_NAO_E_AUTORA_DA_FRAUDE"] == 1,
      "[24] regras contadas em assessments E em discards")
check(sum(CEN["candidatos"].values()) > 0 and sum(CEN["pontuaveis"].values()) > 0,
      "[25] o censo do corpus real não está vazio")

print()
print("=" * 100)
print("BLOCO E — DEFAULTED não é ESTABLISHED (o núcleo do contrato)")
print("=" * 100)
_u = u_de("Vale announced a debt issuance", "Vale", "emissao_divida")
check(_u.subject.status == uea.DEFAULTED,
      f"[26] sujeito herdado do runtime é DEFAULTED ({_u.subject.status})")
check(_u.subject.value == "Vale",
      "[27] o VALOR continua sendo o da produção — o contrato observa, não altera")
_u2 = u_de("Bankruptcy Court Orders Texas to Strike Allegations In State Data "
           "Privacy Suit Against General Motors", "General Motors", "falencia")
check(_u2.decision_rule == "R_FORO_JUDICIAL_NAO_PROVA_INSOLVENCIA",
      f"[28] GM/W&W continua sendo barrado pela F2 ({_u2.decision_rule})")
check(_u2.event_occurrence.status == uea.ESTABLISHED,
      "[29] quando a regra responde ocorrência, a dimensão é ESTABLISHED")
check(_u2.subject.status == uea.ESTABLISHED,
      "[30] F2 também estabelece o sujeito")
check("event_occurrence" not in _u2.missing_dimensions(),
      "[31] missing_dimensions exclui o que foi comprovado")
check("currentness" in _u.missing_dimensions(),
      "[32] ausência de marcador histórico NÃO é prova de atualidade")
check(0.0 <= _u.completeness() <= 1.0 and _u.completeness() < 1.0,
      f"[33] completude é fração e nenhum par real está completo ({_u.completeness():.2f})")

print()
print("=" * 100)
print("BLOCO F — sentinela não é conclusão")
print("=" * 100)
check("indefinido" not in uea.CONFIRMACAO_REAL,
      "[34] 'indefinido' está fora dos níveis reais de confirmação")
_d = {"monitored_company": "X", "event_id": "ma", "subject_company": "X",
      "confirmation_level": "indefinido", "event_phase": "", "scoreable": True,
      "attribution_rule": ""}
check(uea.montar(_d, identity="t").phase.status == uea.DEFAULTED,
      "[35] confirmation_level='indefinido' deixa a fase DEFAULTED")
_d2 = dict(_d, confirmation_level="confirmado")
check(uea.montar(_d2, identity="t").phase.status == uea.ESTABLISHED,
      "[36] confirmação real estabelece a fase")
_d3 = dict(_d, event_phase="acusacao_civil", confirmation_level="indefinido")
check(uea.montar(_d3, identity="t").phase.status == uea.ESTABLISHED,
      "[37] fase jurídica reconhecida estabelece a dimensão mesmo sem confirmação")

print()
print("=" * 100)
print("BLOCO G — o contrato enxerga o defeito do L8")
print("=" * 100)
_L8 = "Supplier Alfa was found liable for fraud. Vale settled a separate contract dispute."
_p = u_de(_L8, "Vale", "fraude")
_s = u_de(_L8, "Vale", "fraude", shadow=True)
check(_p.subject.status == uea.DEFAULTED,
      f"[38] em produção o sujeito do L8 é DEFAULTED ({_p.subject.status})")
check(_p.scoreable is True,
      "[39] e mesmo assim o evento pontua — é o defeito, agora visível")
check(_s.subject.status == uea.ESTABLISHED,
      f"[40] no shadow o sujeito passa a ser comprovado ({_s.subject.status})")
check(_s.decision_rule == "R_LIABILITY_DE_TERCEIRO",
      f"[41] pela regra de terceiro ({_s.decision_rule})")
check(len(_s.missing_dimensions()) < len(_p.missing_dimensions()),
      f"[42] o shadow reduz as dimensões em aberto "
      f"({len(_p.missing_dimensions())} → {len(_s.missing_dimensions())})")

print()
print("=" * 100)
print("BLOCO H — shadow puro: nada escreve, nada é chamado em produção")
print("=" * 100)
_su = io.open("reliability_universal_assessment.py", encoding="utf-8").read()
_si = io.open("reliability_taxonomy_inventory.py", encoding="utf-8").read()
for nome, s in (("universal_assessment", _su), ("taxonomy_inventory", _si)):
    check(all(x not in s for x in ("save_history", "merge_into_history",
                                   "--apply", "--backfill")),
          f"[43..44] {nome} não escreve em history")
_wf = io.open(".github/workflows/update_risk_dashboard.yml", encoding="utf-8").read()
check("taxonomy_inventory" not in _wf and "universal_assessment" not in _wf,
      "[45] nenhum dos dois é chamado pelo workflow")
check(sa.shadow_fraud_roles_ativo() is False,
      "[46] o interruptor de fraude segue desligado por padrão")
_antes = io.open("risk_history.json", "rb").read()
uea.avaliar_corpus(_h, cfg, limite=25)
check(io.open("risk_history.json", "rb").read() == _antes,
      "[47] varrer o corpus não toca risk_history.json")

print()
print("=" * 100)
print("BLOCO I — determinismo e isolamento")
print("=" * 100)
_a = uea.resumo(uea.avaliar_corpus(_h, cfg, limite=60))
_b = uea.resumo(uea.avaliar_corpus(_h, cfg, limite=60))
check(dict(_a["candidatos"]) == dict(_b["candidatos"])
      and _a["completude"] == _b["completude"],
      "[48] duas varreduras seguidas dão o mesmo resultado")
_c1 = inv.matriz(EV, inv.censo(_h))
check(_c1 == MAT, "[49] a matriz é determinística")
_sh = uea.resumo(uea.avaliar_corpus(_h, cfg, limite=200, shadow=True))
_no = uea.resumo(uea.avaliar_corpus(_h, cfg, limite=200))
check(dict(_sh["candidatos"]) == dict(_no["candidatos"]),
      "[50] shadow não inventa nem remove candidatos")
check(sa.shadow_fraud_roles_ativo() is False,
      "[51] o interruptor volta desligado depois da varredura shadow")

print()
print("=" * 100)
print("BLOCO J — o instrumento roda como programa, não só como import")
print("=" * 100)
_env = dict(os.environ, PYTHONIOENCODING="utf-8",
            PYTHONPATH=os.getcwd(),
            RELIABILITY_OUTDIR="out_taxonomy_inventory_test")
for mod in ("reliability_taxonomy_inventory.py", "reliability_universal_assessment.py"):
    r = subprocess.run([sys.executable, mod, "--json"], capture_output=True,
                       text=True, env=_env, timeout=1800)
    check(r.returncode == 0, f"[52..53] {mod} executa e sai 0")

print()
print("=" * 100)
print(f"RESULTADO WAVE R7a (taxonomy semantic contract): {PASS}/{PASS+FAIL} checagens passaram")
print("=" * 100)
if FAIL:
    raise SystemExit(1)
