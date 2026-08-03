# -*- coding: utf-8 -*-
"""Testes de regressão — Bug 1 (B3 bolsa x B3 S.A. companhia).

Fixtures determinísticas, sem rede. Usa a watchlist real de
`config_risco.yaml` (mecanismo genérico `mention_guard.contexto_patterns`,
não hard-coded para B3 — qualquer emissor pode declarar os mesmos padrões).

Rodar: PYTHONIOENCODING=utf-8 python test_b3_entity_role.py
"""
import sys
import yaml

import risk_dashboard as rd

with open("config_risco.yaml", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)
WATCHLIST = CFG["watchlist"]


def detect(title):
    art = {"title": title}
    found = rd.detect_companies(art, WATCHLIST)
    return found, art.get("mention_roles", {})


CASES = [
    # (nome, título, empresas que DEVEM aparecer, empresas que NÃO DEVEM aparecer)
    ("1_braskem_responde_b3",
     "Braskem responde à B3 e diz não haver decisão sobre recuperação judicial, "
     "tutela cautelar segue em vigor",
     [], ["B3"]),  # Braskem não está na watchlist deste config; B3 é o que importa
    ("2_b3_lucro_receita",
     "B3 divulga lucro e receita do trimestre",
     ["B3"], []),
    ("3_vale_sobe_na_b3",
     "Ações da Vale sobem na B3",
     ["Vale"], ["B3"]),
    ("4_b3_instabilidade",
     "B3 sofre instabilidade operacional",
     ["B3"], []),
    ("5_petrobras_esclarecimentos_b3",
     "Petrobras envia esclarecimentos à B3",
     ["Petrobras"], ["B3"]),
    ("6_b3_aquisicao",
     "B3 anuncia aquisição de empresa de tecnologia",
     ["B3"], []),
    ("7_cvm_b3_pedem_explicacoes",
     "CVM e B3 pedem explicações à companhia",
     [], ["B3"]),
    ("8_sancao_b3",
     "Sanção contra a B3 por falha de mercado",
     ["B3"], []),
]

# Casos extra citados no pedido, cobertura adicional do padrão genérico
EXTRA = [
    ("9_oficio_da_b3",
     "Vale recebe ofício da B3 sobre negociação atípica de ações",
     [], ["B3"]),
    ("10_protocolado_na_b3",
     "Documento sobre a operação foi protocolado na B3",
     [], ["B3"]),
    ("11_b3_solicitou",
     "Petrobras é questionada após B3 solicitar esclarecimentos sobre volume negociado",
     [], ["B3"]),
]


def run():
    failures = []
    for name, title, must_have, must_not_have in CASES + EXTRA:
        found, roles = detect(title)
        for c in must_have:
            if c not in found:
                failures.append(f"[{name}] esperado '{c}' em {found} — título: {title!r}")
        for c in must_not_have:
            if c in found:
                failures.append(f"[{name}] '{c}' NÃO deveria estar em {found} — título: {title!r}")
    total = len(CASES) + len(EXTRA)
    ok = total - len(failures)
    print(f"test_b3_entity_role: {ok}/{total} passou")
    if failures:
        for f_ in failures:
            print("  FAIL:", f_)
        sys.exit(1)
    print("OK - todos os casos de desambiguação B3 bolsa x B3 S.A. passaram")


def run_history_regression():
    """Bug 2 (RJ falsamente atribuída à B3) + dedup de Troca de CEO, contra o
    risk_history.json real deste worktree (candidato, não produção). Confirma:
    - nenhum evento de recuperacao_judicial sobra na B3;
    - a Troca de CEO vira 1 única ocorrência com as 5 fontes reais agrupadas;
    - a CONTRIBUIÇÃO dessa ocorrência é IDÊNTICA em toda janela onde ela
      aparece (90/180/365d) — a janela só decide presença/ausência, nunca
      recalcula data/decaimento/bônus (bug de inconsistência entre janelas
      corrigido nesta branch);
    - o evento fica corretamente AUSENTE nas janelas 7/30d (data econômica
      de 2026-05-19 é anterior ao corte dessas janelas)."""
    import json

    with open("risk_history.json", encoding="utf-8") as f:
        history = json.load(f)

    failures = []

    def b3_row(window_days):
        rows = rd.build_evolution(history, CFG, window_days=window_days)
        return next((r for r in rows if r["company"] == "B3"), None)

    # RJ nunca aparece na B3, em nenhuma janela
    for w in (7, 30, 90, 180, 365):
        row = b3_row(w)
        if row and any(e["event_id"] == "recuperacao_judicial" for e in row["events"]):
            failures.append(f"janela {w}d: recuperacao_judicial ainda presente na B3")

    # ausente nas janelas curtas (data econômica > corte)
    for w in (7, 30):
        row = b3_row(w)
        if row is not None:
            failures.append(f"janela {w}d: B3 deveria estar ausente (evento fora do corte), veio {row}")

    contribs = {}
    for w in (90, 180, 365):
        row = b3_row(w)
        if row is None:
            failures.append(f"janela {w}d: B3 deveria estar presente (Troca de CEO dentro do corte)")
            continue
        ceo_entries = [e for e in row["breakdown"] if e["label"] == "Troca de CEO"]
        if len(ceo_entries) != 1:
            failures.append(f"janela {w}d: esperada 1 ocorrência de Troca de CEO, veio {len(ceo_entries)}")
            continue
        e = ceo_entries[0]
        if e["sources"] != 5:
            failures.append(f"janela {w}d: esperado 5 fontes agrupadas, veio {e['sources']}")
        if e["date"] != "2026-05-19":
            failures.append(f"janela {w}d: data econômica esperada 2026-05-19, veio {e['date']}")
        contribs[w] = e["contrib"]

    if len(set(contribs.values())) > 1:
        failures.append(f"contribuição da Troca de CEO varia entre janelas: {contribs} "
                        "(deveria ser idêntica em toda janela onde a ocorrência aparece)")

    total = 1
    ok = total - (1 if failures else 0)
    print(f"test_b3_history_regression: {ok}/{total} passou")
    if failures:
        for f_ in failures:
            print("  FAIL:", f_)
        sys.exit(1)
    print("OK - RJ removida da B3; Troca de CEO consolidada (5 fontes); "
          f"contribuição idêntica entre janelas: {contribs}")


if __name__ == "__main__":
    run()
    run_history_regression()
