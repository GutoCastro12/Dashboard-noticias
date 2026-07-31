# OPERATIONS.md — Runbook operacional do Radar de Risco

Este documento é um guia de operação. Ele **não autoriza** executar nenhum dos passos
descritos por conta própria — cada workflow real só deve ser disparado pelo usuário (ou
por um agente com autorização explícita e específica para aquela execução).

## 0. Antes de qualquer coisa: verificar o estado real

```
git fetch origin main
git log --oneline -1 origin/main
git status --short
```

Esta própria tarefa descobriu que um clone local pode ficar atrás do remoto sem aviso —
sempre confirmar antes de assumir que o HEAD local é o estado de produção.

## 1. Como rodar os testes

```
export PYTHONIOENCODING=utf-8   # necessário no Windows para não quebrar em ✅/→
python -m py_compile risk_dashboard.py semantic_audit.py link_debt_audit.py edgar_audit_4h2.py edgar_shadow_4h3a.py edgar_shadow_4h3b.py
python test_semantica.py
python test_links.py
python test_edgar_4h2.py
python test_edgar_4h3a.py
python test_edgar_4h3b.py
python risk_dashboard.py --test-attribution
python risk_dashboard.py --test-cvm-fixture
python risk_dashboard.py --test-fund-coverage
```
Nenhum destes toca rede real, sobrescreve `risk_history.json`/`index.html` de produção,
ou faz commit/push. Ver `docs/TEST_MATRIX.md` para detalhes de cada um.

## 2. Como rodar o dashboard localmente sem alterar produção

Sempre usar `--output-history`/`--output-html` apontando para arquivos temporários, e
`--history`/`--config` apontando para cópias, nunca para os arquivos de produção
diretamente:

```
python risk_dashboard.py --config config_risco.yaml --history risk_history.json \
  --reclassify-semantic-only \
  --output-history /tmp/risk_history_teste.json \
  --output-html /tmp/index_teste.html \
  --audit-outdir /tmp/out_semantic_teste
```

Isso reprocessa o histórico existente com as regras semânticas atuais, sem fetch
externo, sem tradução via API, sem tocar nos arquivos reais. Comparar
`/tmp/risk_history_teste.json` com `risk_history.json` antes de decidir se promove.

O mesmo padrão vale para `--repair-links-only`:

```
python risk_dashboard.py --config config_risco.yaml --history risk_history.json \
  --repair-links-only \
  --output-history /tmp/risk_history_links_teste.json \
  --output-html /tmp/index_links_teste.html \
  --audit-outdir /tmp/out_link_repair_teste
```

`--repair-links-only` com rede real exige `LINK_REPAIR_ONLINE=1` no ambiente — sem essa
variável, roda em modo restrito/offline (não resolve `batchexecute` real).

**Nunca** rodar `--backfill`, `--reclassify` (sem `-semantic-only`), ou qualquer modo que
grave diretamente sobre `risk_history.json`/`index.html` de produção sem antes revisar a
saída em arquivo separado.

## 3. Como executar cada workflow pela GitHub UI

Todos os três workflows são `workflow_dispatch` (mais um cron para o principal). Para
rodar manualmente: GitHub → Actions → selecionar o workflow → "Run workflow" → escolher
branch `main` → preencher inputs → "Run workflow".

### 3.1 Update Risk Dashboard (`update_risk_dashboard.yml`)

- **Inputs seguros**: `reclassify=false`, `backfill=false`, `audit_cvm=true`,
  `probe_sources=true` (rodar com dados existentes, sem semear histórico).
- **Inputs perigosos**: `backfill=true` — reprocessa com janela de busca ampliada,
  só deve ser usado com aprovação explícita do usuário e por um motivo concreto
  (ex.: emissor novo recém-adicionado que precisa de histórico inicial).
- **Arquivos que pode commitar**: `risk_history.json`, `dashboard_risco.html`,
  `index.html`, `auditoria_cobertura_cvm.csv` (se `audit_cvm=true`),
  `probe_fontes_oficiais.csv` (se `probe_sources=true`), telemetria internacional.
- **Como verificar o Summary**: `gh run view <run_id>` mostra os jobs; para o log
  completo, `gh run view <run_id> --log` (runs longos: usar `--log-failed` se houver
  falha, ou `grep` no arquivo salvo).
- **Como validar deploy**: comparar `risk_history.json`/`run_meta.json` locais pós-pull
  com o commit gerado pelo workflow; conferir `run_meta.json.run_count` incrementou e
  `backfill` está `False` (salvo uso intencional).

### 3.2 Auditoria e reparo de links (`workflow_link_audit.yml`)

- **Modo `audit`**: só mede, não altera nada (mesmo assim, o workflow real usa
  `contents: write` — mas `audit` não deveria produzir diff em `risk_history.json`).
  Rodar isso primeiro sempre que houver dúvida sobre o estado dos links.
- **Modo `repair`**: aplica a resolução real (`batchexecute` com rede) e commita.
  Só rodar quando o objetivo explícito for reduzir o número de pendentes. Resultado mais
  recente confirmado: 1045 auditados, 453 resolvidos, 31 pendentes, 368 registros
  alterados (run `30607645995`, 2026-07-31).
- **Input `verify_status`**: liga verificação HTTP adicional (mais lento, mais preciso
  para 404/410/403).
- **Arquivos que pode commitar**: `risk_history.json`, `index.html`,
  `dashboard_risco.html` — **apenas campos de link**, nunca score/eventos.
- **Como validar**: conferir o Summary do run (`gh run view <run_id>`) mostra "Links
  auditados", "Registros com campo de link alterado", contagem de saúde por
  principal/corroboradora. Confirmar que `risk_history.json["links_repaired_at"]`
  foi atualizado.

### 3.3 EDGAR shadow run (`workflow_edgar_shadow.yml`)

- Roda em modo `shadow`/`dry-run`, `contents: read` — **nunca commita nada**.
- Usar apenas para diagnóstico/medição de qualidade da coleta EDGAR, nunca para tentar
  "ativar" nada em produção (a ativação de scoring exige mudança de config, não deste
  workflow).
- Sobe artifact `edgar-4h3b-<modo>-<run_id>-<timestamp>` com os resultados — baixar com
  `gh run download <run_id>` só se necessário (artifacts podem ser grandes).

## 4. Como verificar o Summary e artifacts de qualquer run

```
gh run list --limit 20
gh run view <run_id>                 # jobs, status, link para o run
gh run view <run_id> --log           # log completo (cuidado com runs longos)
gh run view <run_id> --log-failed    # só os passos que falharam
gh api repos/GutoCastro12/Dashboard-noticias/actions/runs/<run_id>/artifacts
gh run download <run_id> --dir /caminho/temporario   # só se precisar inspecionar o artifact
```

## 5. Como validar deploy

1. Confirmar que o run mais recente do `Update Risk Dashboard` terminou com `success`
   (`gh run list --limit 5`).
2. `git fetch origin main` e conferir se `risk_history.json`/`run_meta.json` locais
   batem com o commit mais recente.
3. Se houver hospedagem estática (Render, via `render.yaml`), verificar visualmente o
   dashboard publicado — mas isso é uma ação de rede externa; só fazer com o
   consentimento do usuário se envolver navegação/autenticação.

## 6. Como reverter com segurança

- **Nunca** usar `git push --force` ou `git reset --hard` sem instrução explícita do
  usuário.
- Se um commit automático do workflow introduziu um problema, o caminho seguro é criar
  um novo commit que reverte (`git revert <sha>`), não reescrever histórico.
- Antes de reverter, rodar a suíte de testes segura contra o estado alvo do revert para
  confirmar que ele não reintroduz um bug já corrigido (ver casos canônicos no
  `PROJECT_BIBLE.md` §7).

## 7. Se um workflow alterar arquivos não autorizados

1. Não fazer commit/push adicional imediatamente.
2. Rodar `git diff <sha_anterior> <sha_do_run>` para ver exatamente o que mudou.
3. Comparar com a lista de "arquivos que o workflow pode commitar" (seção 3 acima).
4. Se houver arquivo fora da lista esperada (ex.: `config_risco.yaml`, algum `.py`),
   tratar como incidente: reportar ao usuário antes de qualquer ação corretiva, e não
   reverter unilateralmente sem confirmação (o workflow pode ter sido intencionalmente
   alterado pelo próprio usuário).

## 8. Procedimentos por tipo de operação

| Operação | Como | Risco |
|---|---|---|
| Atualização normal | Deixar o cron rodar (4x/dia, dias úteis) ou `workflow_dispatch` com todos os inputs padrão (`reclassify=false, backfill=false`) | Baixo |
| Auditoria de links | `workflow_link_audit.yml` modo `audit` | Baixo (não deveria alterar nada) |
| Reparo de links | `workflow_link_audit.yml` modo `repair` | Médio — altera `risk_history.json`/HTML; sempre rodar `audit` antes |
| EDGAR shadow | `workflow_edgar_shadow.yml` | Nenhum (não commita, `contents: read`) |
| Auditoria internacional | `--audit-international-coverage` local, ou via `audit_cvm=true`/`probe_sources=true` no workflow principal | Baixo (leitura/agregação) |
| Reclassificação semântica | `--reclassify-semantic-only` **contra arquivo de saída separado primeiro** | Médio — só promover a produção após comparar antes/depois |
| **Backfill** | `workflow_dispatch` com `backfill=true` **apenas com aprovação explícita e motivo documentado** | **Alto — operação excepcional.** Nunca rodar por rotina, nunca no cron |
