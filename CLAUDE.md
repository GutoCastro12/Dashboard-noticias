# CLAUDE.md — Radar de Risco — Emissores

> Guia operacional curto. Para o documento canônico completo, ver `docs/PROJECT_BIBLE.md`.
> Para conflitos entre relatórios de chat e o repositório real, ver `docs/HANDOVER_CONFLICTS.md`.
> Para runbook operacional, ver `docs/OPERATIONS.md`. Para testes, `docs/TEST_MATRIX.md`. Para backlog, `docs/BACKLOG.md`.

## Missão

Dashboard interno de risco de crédito ("Radar de Risco — Emissores") usado pela área de
risco da Vinci Partners para monitorar deterioração de crédito de ~160 emissores em
carteira via notícias públicas e comunicados oficiais (CVM/IPE no Brasil, SEC/EDGAR nos
EUA), classificando eventos por taxonomia própria, calculando um score ponderado e
publicando um dashboard estático via GitHub Actions. É usado para apresentação executiva
(o usuário já declarou usá-lo para apresentar ao CFO) — falsos positivos custam
credibilidade, não só engenharia.

## Objetivo de negócio

Identificar deterioração de crédito rapidamente, sem depender de terminal pago, **sem
atribuir a um emissor monitorado eventos que na verdade pertencem a terceiros**
(fornecedores, investidas, alvos de aquisição) e sem inflar risco por dupla contagem do
mesmo fato econômico.

## Arquivos centrais

| Arquivo | Papel |
|---|---|
| `risk_dashboard.py` | Pipeline completo: coleta, classificação, atribuição, scoring, CLI, exports |
| `semantic_audit.py` | Motor de regras semânticas (padrão Vale/Samarco, M&A legítimo, fases jurídicas) |
| `link_debt_audit.py` | Resolução de URLs (Google News/batchexecute), saúde de links |
| `config_risco.yaml` | Configuração de produção: watchlist, taxonomia, `event_resolution`, flags EDGAR |
| `template_risco.html.j2` | Template Jinja2 do dashboard publicado |
| `risk_history.json` | Histórico de produção (dado, não código) |
| `run_meta.json`, `international_search_history.json` | Telemetria de execução |
| `.github/workflows/update_risk_dashboard.yml` | Workflow principal (cron 4x/dia + dispatch) |
| `.github/workflows/workflow_link_audit.yml` | Auditoria/reparo de links (`mode: audit\|repair`) |
| `.github/workflows/workflow_edgar_shadow.yml` | EDGAR em modo sombra isolado, nunca publica |
| `edgar_audit_4h2.py`, `edgar_shadow_4h3a.py`, `edgar_shadow_4h3b.py` | Diagnóstico/shadow do EDGAR por fase (não são caminho de produção) |

**Não existem no repositório** (mencionados em relatórios de chat, nunca aplicados):
`edgar_canonical.py`, `edgar_shadow_4h3c.py`, `test_edgar_4h3c.py`,
`config_risco_4h2_candidato.yaml`, `config_risco_4h3a_candidato.yaml`,
`config_risco_4h3c_candidato.yaml`. `config_risco_4h3b_candidato.yaml` existe mas **não**
é o config de produção.

## Fluxo de dados (resumo)

```
fontes (Google News, CVM/IPE, SEC/EDGAR se habilitado, RI)
  → coleta → tradução → classify_article (taxonomia)
  → detect_companies + mention_role (papel: sujeito/terceiro)
  → semantic_audit.apply_semantics_to_record (move não-pontuável para contexto)
  → resolve_event_families (1 principal por família semântica, por empresa)
  → merge_into_history (events_by_company, context_events_by_company, ...)
  → resolve_history_urls / link_debt_audit (URLs diretas, principal e corroboradora)
  → build_evolution (score real: peso-base × decaimento × confiança + bônus fonte)
  → build_feed / build_changes (event_ids_for por empresa, nunca união global)
  → template_risco.html.j2 → index.html / dashboard_risco.html
  → commit via GitHub Actions
```

## Comandos de teste seguros (não tocam rede, não sobrescrevem produção)

```
python -m py_compile risk_dashboard.py semantic_audit.py link_debt_audit.py
python test_semantica.py        # 102/102
python test_links.py            # 92/92 (mocks, sem rede)
python test_edgar_4h2.py        # 23/23
python test_edgar_4h3a.py       # 48/48
python test_edgar_4h3b.py       # 59/59
python risk_dashboard.py --test-attribution     # 75/75
python risk_dashboard.py --test-cvm-fixture     # 11/11
python risk_dashboard.py --test-fund-coverage   # 10/10
```
No Windows, definir `PYTHONIOENCODING=utf-8` antes de rodar (console cp1252 quebra a
impressão de ✅/→, não é falha real do teste). Ver `docs/TEST_MATRIX.md` para detalhes.

## Comandos/ações proibidas sem aprovação explícita do usuário

- Rodar `--backfill` ou disparar workflow com `backfill=true`.
- Ativar `edgar_scoring_enabled: true` no `config_risco.yaml`.
- Alterar pesos-base, thresholds, tiers de materialidade, `event_resolution`, taxonomia.
- Disparar qualquer workflow real (`gh workflow run`) ou fazer commit/push.
- Rodar `--reclassify` ou `--repair-links-only` contra `risk_history.json` de produção
  sem antes rodar para arquivo de saída separado (`--output-history`) e comparar.

## Invariantes críticas (não regredir)

1. `edgar_scoring_enabled` permanece desligado até novo ciclo de evidência real e aprovação explícita.
2. Backfill nunca roda implicitamente — só `workflow_dispatch` manual com input explícito.
3. `events_by_company` contém **apenas** eventos pontuáveis atribuídos à empresa correta.
4. `context_events_by_company` é reservado a eventos de **terceiros relacionados** —
   **não** a eventos diretos não pontuáveis da própria empresa (pendência aberta, ver
   caso Santander abaixo e `docs/BACKLOG.md` P0).
5. `event_ids_for()` é a fonte oficial de eventos por empresa — nenhum consumidor deve ler `rec["event_ids"]`/`rec["companies"]` diretamente para decidir pontuação.
6. Uma ocorrência econômica pontua uma vez (`resolve_event_families`, por empresa, nunca globalmente).
7. Palavra-chave cria candidato, nunca prova sozinha sujeito/fase/data/direção/confirmação.
8. M&A legítimo (aquisição nomeada, participação societária, fusão) não pode ser apagado por regra ampla — já causou 142 falsos positivos numa iteração anterior.
9. Score do dashboard é o resultado real de `build_evolution` (peso-base × decaimento × confiança + bônus de fontes) — nunca soma direta de peso-base da taxonomia.
10. HTTP 200 de `news.google.com` nunca prova URL resolvida; toda fonte (principal e corroboradora) usa o mesmo resolvedor (`link_debt_audit.resolve_gnews_token`).
11. Subgrupo cadastral reflete a natureza do emissor, nunca o instrumento da posição na carteira.
12. Ticker cadastral é separado de `aliases` de busca; nenhum termo curto (<5-6 caracteres) deve virar alias inseguro.
13. País/região = domicílio do emissor, nunca a praça de negociação do instrumento.
14. Configurado ≠ executado; executado ≠ resultado. Nunca afirmar cobertura sem telemetria real (`run_meta.json`, `international_search_history.json`).
15. **Nunca trate relatórios de chat como prova do estado atual sem confirmar no repositório.**
16. **Nunca altere scoring EDGAR, backfill, pesos, thresholds, tiers ou taxonomia sem aprovação explícita.**

## Pendência semântica conhecida — Santander (confirmada em código, não corrigida)

`semantic_audit.py` (`EVENTO_INFORMATIVO`, `apply_semantics_to_record`) e
`template_risco.html.j2` tratam **qualquer** evento não-pontuável — seja de terceiro
real (Vale/Samarco) ou reclassificação informativa da própria empresa (Santander/Esfera,
Santander lucro pós-aquisição) — no mesmo campo `context_events_by_company` e sob o
mesmo rótulo de UI "Contexto relacionado · não pontua". O usuário confirmou em
2026-07-31 que isso está errado para o caso Santander: quando `subject_company ==
monitored_company`, o evento deveria aparecer como "evento direto positivo/informativo",
não como contexto relacionado. Ver `docs/HANDOVER_CONFLICTS.md` (C-12) e
`docs/BACKLOG.md` (P0) antes de tocar nesse código.

## Fluxo obrigatório antes de editar qualquer coisa

1. `git fetch origin main` e confirmar que o clone local não está atrás do remoto (isso
   já causou uma falsa impressão de "workflow desatualizado" nesta própria tarefa).
2. Ler `docs/PROJECT_BIBLE.md` §2 (estado atual confirmado) e `docs/HANDOVER_CONFLICTS.md`.
3. Rodar a suíte de testes segura (acima) e confirmar que os números batem com `docs/TEST_MATRIX.md`.
4. Se a mudança tocar atribuição/semântica/M&A/EDGAR/links, ler as invariantes acima e os casos de regressão em `docs/PROJECT_BIBLE.md` §7.
5. Nunca editar `config_risco.yaml` (pesos/tiers/taxonomia) sem aprovação explícita do usuário.

## Definição de pronto

Uma mudança só é "pronta" quando: os testes relevantes citados acima passam (números
completos, nenhum caso removido); a invariância de score/eventos foi comprovada quando
aplicável (hash antes/depois); nenhuma regra nova rejeita M&A/RJ/fraude legítimos
conhecidos (ver casos canônicos no `PROJECT_BIBLE.md`); há evidência real de execução
(Actions/produção), não apenas teste local — e essa evidência está documentada com a
marcação de proveniência correta ([REPO]/[GIT]/[ACTIONS]/[PRODUCAO]).
