# BACKLOG.md — Backlog consolidado (três relatórios + achados desta auditoria)

Itens já confirmados como concluídos (ex.: reparo de links v2, que resolveu 453/484
redirecionadores) **não** entram aqui como pendência — os 31 links ainda pendentes
entram como item de melhoria/monitoramento, não como falha do reparo.

## P0 — risco de produção / bloqueante

### P0-1 — Corrigir o roteamento de eventos diretos não pontuáveis do próprio emissor (caso Santander)
- **Objetivo**: distinguir, dentro de `semantic_audit.py`, "contexto de terceiro real"
  (`subject_company != monitored_company`, ex. Vale/Samarco) de "evento direto
  informativo/positivo da própria empresa" (`subject_company == monitored_company`,
  ex. Santander/Esfera, Santander lucro pós-aquisição). Hoje ambos caem no mesmo campo
  `context_events_by_company` e no mesmo rótulo de UI "Contexto relacionado · não
  pontua", o que é semanticamente incorreto para o segundo caso.
- **Motivação**: confirmado pelo usuário em 2026-07-31 e verificado por leitura direta
  do código (`EVENTO_INFORMATIVO`, `apply_semantics_to_record` em `semantic_audit.py`;
  bloco `.context-block` em `template_risco.html.j2`) nesta auditoria — não é apenas uma
  alegação de relatório de chat.
- **Arquivos prováveis**: `semantic_audit.py` (função `apply_semantics_to_record`,
  possivelmente um novo campo `direct_informational_events_by_company` ou um campo
  `origin`/`bucket` dentro de cada entrada de `context_events_by_company` que diferencie
  `terceiro` de `proprio_informativo`); `template_risco.html.j2` (novo bloco de
  renderização, rótulo diferente de "Contexto relacionado"); `risk_dashboard.py` se
  `build_evolution`/`build_feed` precisarem ler o novo campo.
- **Dependências**: nenhuma — é isolado, não depende de EDGAR nem de links.
- **Critério de aceite**: caso de teste determinístico com a notícia "Santander supera
  estimativas de lucro com expansão da base de clientes após aquisição" — resultado
  esperado: evento direto, positivo/informativo, direção positiva, não pontuável como
  risco negativo, sem aparecer em "Contexto relacionado · não pontua"; e a incorporação
  da Esfera classificada como evento corporativo direto neutro/informativo do próprio
  Santander, não como contexto. Regra formal a implementar:
  `subject_company != monitored_company` → `context_events_by_company` (comportamento
  atual, manter); `subject_company == monitored_company` e evento positivo → evento
  direto positivo/informativo (novo comportamento); `subject_company ==
  monitored_company` e evento neutro → evento corporativo informativo (novo
  comportamento); `subject_company == monitored_company` e evento negativo pontuável →
  `events_by_company` (comportamento atual, manter).
- **Risco**: sem essa correção, qualquer evento neutro/positivo do próprio emissor
  reclassificado pela auditoria semântica continuará aparecendo como se fosse
  informação de terceiro, confundindo o usuário sobre a origem do sinal.
- **Estado**: PENDENTE, confirmado nesta auditoria.

### P0-2 — Confirmar periodicamente que `edgar_scoring_enabled` permanece desligado
- **Objetivo**: garantir que nenhuma alteração futura ative scoring EDGAR sem ciclo de
  evidência real e aprovação explícita.
- **Motivação**: é a invariante mais crítica identificada nos três relatórios.
- **Arquivos**: `config_risco.yaml`.
- **Critério de aceite**: leitura direta do arquivo confirmando ausência/`false`.
- **Estado**: CONFIRMADO NESTA AUDITORIA (`edgar_scoring_enabled` ausente) — item de
  vigilância recorrente, não uma tarefa de implementação.

## P1 — necessário para a próxima entrega

### P1-1 — Mesclar/criar o parser canônico do EDGAR (`edgar_canonical.py`)
- **Objetivo**: eliminar a divergência entre o parser de diagnóstico
  (`sample_filings()` em `edgar_audit_4h2.py`, que descarta `items` do 8-K) e um parser
  de produção único.
- **Motivação**: o primeiro shadow real do EDGAR ficou vazio (títulos "Ford Motor — 8-K:
  8-K") por essa perda de dados, invalidando a avaliação de qualidade daquele ciclo.
- **Arquivos prováveis**: novo `edgar_canonical.py`; `risk_dashboard.py`
  (`fetch_edgar_filings` delegando a ele); `edgar_shadow_4h3b.py`.
- **Dependências**: nenhuma de código; depende de acesso de rede real (só via GitHub
  Actions) para o segundo shadow de validação.
- **Critério de aceite**: `filings_with_sufficient_evidence > 0` num shadow real, e
  revisão manual dos itens críticos/altos confirmando que a atribuição de sujeito não
  regride (mesma guarda de `mention_role`/`semantic_audit`).
- **Estado**: PENDENTE — arquivo não existe no repositório.

### P1-2 — Verificar se `edgar_audit_4h2.py`/`sample_filings()` ainda são chamados por algum caminho de produção
- **Objetivo**: garantir que o parser fraco de diagnóstico nunca alimenta o pipeline
  pontuável.
- **Arquivos**: `risk_dashboard.py`, `edgar_shadow_4h3a.py`, `edgar_shadow_4h3b.py`.
- **Critério de aceite**: grep/trace confirmando que `sample_filings()` só é chamado
  pelos próprios scripts de diagnóstico isolado.
- **Estado**: NÃO VERIFICADO NESTA AUDITORIA (é leitura, pode ser feito por um agente
  futuro sem risco).

### P1-3 — Consolidar ou remover `config_risco_4h3b_candidato.yaml`
- **Objetivo**: eliminar ambiguidade sobre qual config é o vigente.
- **Motivação**: é o único config candidato remanescente no repositório; os demais
  (`_4h2_`, `_4h3a_`, `_4h3c_`) já não existem.
- **Arquivos**: `config_risco_4h3b_candidato.yaml`.
- **Critério de aceite**: decisão explícita do usuário — manter como referência
  histórica documentada, ou remover.
- **Estado**: PENDENTE DE DECISÃO (não é uma ação de código a fazer sem aprovação).

### P1-4 — Higienizar `formularios_gatilho` no YAML para lista nativa
- **Objetivo**: eliminar a inconsistência de representação (hoje é uma string literal
  `"['8-K', '6-K', '10-K', '10-Q', '20-F']"` em vez de uma lista YAML).
- **Motivação**: o código já tolera esse formato via `_normalize_edgar_forms()`, então
  não é um bug funcional — mas é uma fonte de confusão para qualquer editor manual do
  config.
- **Arquivos**: `config_risco.yaml`.
- **Critério de aceite**: `formularios_gatilho` como lista YAML nativa, testes EDGAR
  continuam 100% passando.
- **Estado**: PENDENTE, baixo risco.

### P1-5 — Observar um caso real de downgrade+outlook (família de rating) em produção
- **Objetivo**: validar `resolve_event_families` com um dado real, não só sintético.
- **Motivação**: o mecanismo nunca foi observado ocorrendo de fato no histórico real de
  produção segundo os relatórios.
- **Critério de aceite**: identificar pelo menos um registro real onde a família colapsou
  corretamente.
- **Estado**: PENDENTE, sem ação de código — é uma observação/monitoramento.

## P2 — melhoria relevante

### P2-1 — Monitorar os 31 links ainda pendentes de resolução
- **Objetivo**: não é uma falha — é o resíduo esperado do reparo v2 (453 de 484
  redirecionadores resolvidos). Continuar monitorando se esse número cai em futuras
  execuções de `repair`.
- **Estado**: MELHORIA CONTÍNUA, não bloqueante.

### P2-2 — Rodar `--audit-international-coverage` para recalcular números de cobertura atuais
- **Objetivo**: os números de cobertura internacional (62/62 emissores, EDGAR
  0 filings) citados nos relatórios são de execuções anteriores; recalcular com
  `run_meta.json`/`international_search_history.json` atuais (`run_count=59`).
- **Arquivos**: comando existente, apenas leitura/agregação.
- **Estado**: PENDENTE, baixo risco (não decidido nesta tarefa por ser fora do escopo de
  leitura pura solicitado).

### P2-3 — Fechar decisões pendentes de fases anteriores (10 gestoras grandes, Pátria/Morgan Stanley, VIGT11)
- **Objetivo**: decisões de classificação cadastral mencionadas no Report-1, nunca
  fechadas.
- **Estado**: PENDENTE DE DECISÃO DO USUÁRIO, não uma tarefa técnica.

### P2-4 — Regenerar CSVs de auditoria de atribuição, se ainda relevantes
- **Objetivo**: `auditoria_atribuicao_entidade.csv`, `eventos_indiretos_reclassificados.csv`,
  `conflitos_direcao_evento.csv`, `relatorio_atribuicao_impacto.md` foram pedidos nos
  relatórios mas nenhum existe no repositório hoje.
- **Estado**: PENDENTE, decidir se ainda são necessários dado que a auditoria semântica
  já foi aplicada e aprovada visualmente pelo usuário.

## P3 — ideia futura

### P3-1 — Scrapers para CMF (Chile), BMV/CNBV (México), SIMEV (Colômbia), CNV (Argentina)
- **Estado**: deliberadamente fora de escopo até novo probe real; não é dívida técnica
  urgente.

### P3-2 — Coletor dedicado de agências de rating (S&P, Moody's, Fitch)
- **Estado**: deliberadamente adiado; cobertura via imprensa + `source_trust` alto.

### P3-3 — Metodologia de multiplicador de score para eventos indiretos mitigadores
- **Estado**: proposta (`PROPOSTA_SCORE_INDIRETO.md`, não encontrada no repositório
  atual) nunca aprovada; não implementar sem aprovação explícita.

### P3-4 — Ativar `search_locale.primary`/`fallbacks` para emissores com zero resultado de mídia
- **Estado**: mecanismo implementado, nunca aplicado a nenhum emissor real.
