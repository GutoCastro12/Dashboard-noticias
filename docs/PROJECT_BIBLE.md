# PROJECT BIBLE — Radar de Risco — Emissores

> Documento canônico. Legível por um agente novo em ~30-45 min. Marcações de
> proveniência: [REPO] arquivo/código versionado; [GIT] histórico de commits;
> [ACTIONS] `gh run`/log/artifact real; [PRODUCAO] dado real publicado
> (`risk_history.json`/`index.html` atuais); [RELATORIO-01/02/03] um dos três
> relatórios de chat em `docs/handover/raw/`; [USUARIO-2026-07-31] fato confirmado
> diretamente pelo usuário nesta tarefa; [INFERENCIA] dedução razoável não
> diretamente testada; [NAO-VERIFICADO] afirmação que não pôde ser confirmada.

> **Nota de proveniência importante**: no início desta tarefa, o clone local estava em
> `42e5820` (2026-07-30 21:33 UTC). Um `git fetch origin main` revelou que o remoto já
> estava em `75bd706` (2026-07-31 12:15 UTC) — 8 commits à frente, incluindo o resultado
> real do reparo de links v2 e `test_links.py`. O clone local foi atualizado por
> fast-forward (nenhuma mudança local foi perdida ou sobrescrita; `git status` não tinha
> nada a preservar além de `.claude/`/`relatorios/` não rastreados). Todas as afirmações
> abaixo referem-se ao HEAD **pós-fetch**, `75bd706`. Isso por si só é uma lição
> operacional: **sempre rodar `git fetch` antes de confiar no estado do clone local.**

---

## 1. Visão executiva

O "Radar de Risco — Emissores" é um dashboard interno de monitoramento de risco de
crédito, mantido para a área de risco da Vinci Partners [RELATORIO-01][RELATORIO-03]. Ele
acompanha ~160 emissores (ações, dívida corporativa, fundos, FIIs, participações
privadas) [REPO], coletando notícias públicas e comunicados oficiais (CVM/IPE no Brasil,
SEC/EDGAR nos EUA — atualmente desativado, ver §11), classificando eventos de risco de
crédito por uma taxonomia própria (recuperação judicial, falência, default, rebaixamento
de rating, fraude, M&A, covenant breach, troca de CEO etc.) e publicando um score
ponderado por emissor em um dashboard estático via GitHub Actions + hospedagem
(`render.yaml`) [REPO].

A decisão que o sistema suporta é: identificar rapidamente deterioração de crédito de um
emissor em carteira, sem depender de terminal pago (Bloomberg/Reuters), e sem gerar
alarmes falsos que corroam a confiança na ferramenta — o usuário declarou usar o
dashboard para apresentação executiva ao CFO [RELATORIO-03]. Valor para a área de risco:
visibilidade tempestiva e auditável sobre eventos de crédito relevantes, incluindo
cobertura internacional, com transparência sobre o que está e o que não está sendo
efetivamente monitorado.

Definição de sucesso, conforme estabelecida pelo próprio usuário ao longo dos três
relatórios: (a) zero eventos de terceiro atribuídos ao emissor errado; (b) uma ocorrência
econômica pontua uma vez; (c) M&A legítimo nunca suprimido por regra ampla; (d) links que
abrem de fato dentro da rede corporativa; (e) nenhuma afirmação de cobertura/produção
sem evidência real; (f) EDGAR não infla score sem medição de qualidade não-vazia.

---

## 2. Estado atual confirmado

| Componente | Estado atual | Evidência | Confiança |
|---|---|---|---|
| Repositório | `GutoCastro12/Dashboard-noticias`, branch `main` @ `75bd706` | [GIT][ACTIONS] `gh repo view`, `git rev-parse` | Alta |
| Watchlist | 160 emissores (105 listada / 31 não_listada / 8 fii / 16 gestora_fundo) | [REPO] contagem direta em `config_risco.yaml` | Alta |
| `scoring_mode` | 144 `normal`, 16 `taxonomia_propria` | [REPO] contagem direta | Alta |
| Elegíveis ao EDGAR | 32 (por `cik`/`official.sec`) | [REPO] contagem direta | Alta |
| Coleta EDGAR em produção | **Desativada** (`international_official_sources_enabled` ausente no config) | [REPO][ACTIONS] run `30629264808`: "SEC/EDGAR: desativado ... 0 filings, score inalterado" | Alta |
| Scoring EDGAR | Desligado (`edgar_scoring_enabled` ausente ⇒ `False`) | [REPO] | Alta |
| Shadow mode EDGAR isolado | Existe (`workflow_edgar_shadow.yml`, `contents: read`, nunca commita) | [REPO][ACTIONS] | Alta |
| Parser canônico EDGAR (`edgar_canonical.py`) | **Não existe no repositório** | [REPO] `Glob` negativo | Alta |
| Auditoria semântica (`semantic_audit.py`) | Implementada, integrada, aplicada ao histórico real | [REPO][PRODUCAO] `apply_semantics_to_record` chamado por `risk_dashboard.py`; testes 102/102 | Alta |
| Padrão Vale/Samarco | Implementado e testado | [REPO] `t01`/`i01` em `test_semantica.py`, passou nesta tarefa | CONFIRMADO_NO_REPO |
| Pendência Santander (evento direto informativo tratado como contexto de terceiro) | **Confirmada, não corrigida** | [REPO] leitura de `EVENTO_INFORMATIVO`/`apply_semantics_to_record`/template | CONFIRMADO_NO_REPO (pendência) |
| Reparo de links v2 (batchexecute consolidado) | Aplicado e executado com rede real | [ACTIONS] run `30607645995`: 1045 auditados, 453 resolvidos, 31 pendentes; [PRODUCAO] `risk_history.json.links_repaired_at=2026-07-31T05:45:56Z` | CONFIRMADO_EM_PRODUCAO |
| `workflow_link_audit.yml` | `name: "Auditoria e reparo de links"`, `contents: write`, `mode: audit\|repair` | [REPO][GIT] pós-fetch | CONFIRMADO_NO_REPO |
| `update_risk_dashboard.yml` | Cron 4x/dia (dias úteis) + `workflow_dispatch`; roda com sucesso | [REPO][ACTIONS] runs recentes `success` | CONFIRMADO_NO_ACTIONS |
| `workflow_edgar_shadow.yml` | `workflow_dispatch`, `contents: read` | [REPO] | CONFIRMADO_NO_REPO |
| Suíte `--test-attribution` | 75/75 | [REPO] rodado nesta tarefa | CONFIRMADO_NO_REPO |
| `test_semantica.py` | 102/102 | [REPO] rodado nesta tarefa | CONFIRMADO_NO_REPO |
| `test_links.py` | 92/92 (mocks, sem rede real) | [REPO] rodado nesta tarefa | CONFIRMADO_NO_REPO |
| `test_edgar_4h2/3a/3b.py` | 23/23, 48/48, 59/59 | [REPO] rodado nesta tarefa | CONFIRMADO_NO_REPO |
| `--test-cvm-fixture` / `--test-fund-coverage` | 11/11 / 10/10 | [REPO] rodado nesta tarefa | CONFIRMADO_NO_REPO |
| `config_risco_4h3b_candidato.yaml` | Presente, não promovido a produção | [REPO] | CONFIRMADO_NO_REPO |
| `config_risco_4h2/3a/3c_candidato.yaml` | Não existem | [REPO] `Glob` negativo | Alta |
| `edgar_shadow_4h3c.py` / `test_edgar_4h3c.py` | Não existem | [REPO] `Glob` negativo | Alta |
| Manuais LaTeX PT/ES | Não fazem parte deste repositório (não encontrados) | [REPO] | NAO-VERIFICADO quanto a existirem em outro lugar |
| Deploy Render | `render.yaml` presente, não inspecionado ao vivo nesta tarefa (proibido acessar produção externa sem necessidade) | [REPO] | NAO-VERIFICADO |

---

## 3. Arquitetura atual

```
FONTES
  Google News (por locale/país) ─┐
  CVM/IPE (Brasil)                ├─► coleta (fetch_all / fetch_cvm_fatos / fetch_edgar_filings*)
  SEC/EDGAR (*desativado hoje*)  ─┤     * fetch_edgar_filings só roda de fato via
  RI (RSS/scraping Tier 1)       ─┘       workflow_edgar_shadow.yml (force=True, isolado)
        ↓
TRADUÇÃO (translate-then-classify)   translate_articles() / detect_language()
        ↓
CLASSIFICAÇÃO POR TAXONOMIA          classify_article()  [risk_dashboard.py]
        ↓
ATRIBUIÇÃO DE EMPRESAS               detect_companies()  [contexto vs. sujeito]
        ↓
PAPEL SEMÂNTICO                      mention_role() / semantic_role_guard()
        ↓
RESOLUÇÃO SEMÂNTICA (bloqueante)     semantic_audit.apply_semantics_to_record()
                                      [move não-pontuável → context_events_by_company;
                                       ver pendência Santander em §5/§7]
        ↓
RESOLUÇÃO POR FAMÍLIAS               resolve_event_families()  [por empresa, config
                                      event_resolution em config_risco.yaml]
        ↓
DEDUPLICAÇÃO / OCORRÊNCIA            dedupe_articles() / debt_occurrence_key()
        ↓
PERSISTÊNCIA                         merge_into_history() → risk_history.json
        ↓
RESOLUÇÃO DE LINKS                   resolve_history_urls() / link_debt_audit.
                                      resolve_gnews_token() [principal E corroboradora,
                                      mesmo resolvedor]
        ↓
SCORING                               build_evolution()  [peso-base × decaimento ×
                                      confiança + bônus de fontes; event_ids_for()]
        ↓
FEED / CHANGES                        build_feed() / build_changes()  [uma linha por
                                      empresa×artigo, event_ids_for(), nunca union]
        ↓
RENDERIZAÇÃO                          template_risco.html.j2 → dashboard_risco.html
                                      → cópia para index.html
        ↓
PUBLICAÇÃO                            commit via update_risk_dashboard.yml (cron +
                                      dispatch), hospedagem estática (render.yaml)
```

Arquivos e funções por etapa [REPO]: `risk_dashboard.py` concentra coleta, classificação,
atribuição, scoring e CLI; `semantic_audit.py` concentra a resolução semântica
bloqueante (M&A, referência histórica, fase jurídica, colapso de família de rating);
`link_debt_audit.py` concentra classificação e resolução de URLs (`classify_link`,
`check_link_live`, `gnews_batchexecute`, `resolve_gnews_token`, `resolve_article_url`,
`group_debt_occurrences`, `interface_decision`); `template_risco.html.j2` é o único
consumidor de renderização.

---

## 4. Modelo de dados

Campos reais mais importantes por registro do histórico (`risk_history.json["articles"][url]`) [REPO]:

- `title`, `summary`, `title_original`/`summary_original`, `language`, `domain`, `pub_ts`, `captured_ts`
- `companies` — todas as empresas citadas no artigo (bruto, não usar para score)
- `companies_attributed` — empresas com ≥1 evento pontuável
- `context_companies` — empresas citadas sem evento pontuável (contexto)
- `events_by_company` — dict empresa → lista de `event_id` **pontuáveis**
- `context_events_by_company` — dict empresa → lista de eventos **não pontuáveis**, com
  `event_id`, `event_label`, `subject_company`, `relation_type`, `impact_type`,
  `event_scope`, `event_phase`, `direction`, `scoreable=False`, `event_id_corrigido`,
  `attribution_rule`, `attribution_confidence`, `attribution_evidence`, `nota`
  — **hoje usado tanto para contexto de terceiro real quanto para reclassificação
  informativa da própria empresa** (pendência, ver §7 DEC-Santander)
- `event_assessments` — lista de avaliações por (empresa, evento), com
  `subject_company`, `actor_company`, `affected_company`, `transaction_object/scope/role`,
  `event_phase`, `event_scope`, `direction`, `historical_reference`, `new_occurrence`,
  `confirmation_level`, `attribution_rule/confidence`, `scoreable`, `rejection_reason`,
  `legal_status`, `confirmation_status`
- `semantic_discards` — eventos removidos pela guarda semântica, com motivo
- `event_ids` — campo legado (união global), mantido só como fallback de compatibilidade
- URLs: `url` (principal), `corroborations`/`corrob_sources` (secundárias); cada uma
  ganha, após resolução, `display_url`, `link_health`
  (`url_direta_valida`/`redirect_resolvido`/`redirect_nao_resolvido`/`removido_404_410`/
  `bloqueado_ou_paywall`/`homepage_generica`/`dominio_suspeito`/`bloqueio_de_ambiente`/
  `url_malformada`), `resolution_method`

Nível de histórico (`risk_history.json` topo): `links_repaired_at` (timestamp da última
execução real do reparo de links — confirmado `2026-07-31T05:45:56+00:00` [PRODUCAO]).

`run_meta.json`: `run_count` (59 no momento desta auditoria [REPO]), `backfill` (bool,
confirmado `False`), `reclassify`, `audit_cvm`, `probe_sources`, `no_history`, `demo`,
`generated_at`, `run_finished_at`, `international_search_execution`,
`official_source_execution`.

`international_search_history.json`: histórico cumulativo de telemetria de busca
internacional por execução (até 8 runs, pela natureza efêmera do runner do Actions).

Eventos positivos/informativos diretos: existem na taxonomia (`acao_rating_positiva`,
`recompra_acoes`, `reorganizacao_societaria_interna`, `integracao_pos_aquisicao` etc. —
ver `EVENTO_INFORMATIVO` em `semantic_audit.py`), mas **não têm hoje um campo de destino
próprio** — são gravados no mesmo `context_events_by_company` que eventos de terceiro
(pendência §7).

---

## 5. Regras de negócio e invariantes

Ver a lista completa e numerada em `CLAUDE.md` (16 invariantes). Destaques específicos
desta seção:

- **Sujeito vs. fonte/filer**: a empresa dona do domínio, do RI, ou que publicou o
  comunicado não é automaticamente o sujeito do evento (`source_company != subject_company`).
- **Contexto de terceiro vs. evento direto não pontuável**: hoje o código só tem UM
  destino para "não pontuável" (`context_events_by_company`), mas conceitualmente há
  quatro casos distintos que o usuário quer diferenciados:
  1. `subject_company != monitored_company` → contexto de terceiro real (Vale/Samarco) — **correto hoje**.
  2. `subject_company == monitored_company` e evento positivo → deveria ser "evento direto positivo/informativo", não contexto — **incorreto hoje** (caso Santander/lucro).
  3. `subject_company == monitored_company` e evento neutro (ex.: incorporação de subsidiária) → deveria ser "evento corporativo informativo", não contexto — **incorreto hoje** (caso Santander/Esfera).
  4. `subject_company == monitored_company` e evento negativo pontuável → `events_by_company` — **correto hoje**.
- **Famílias semânticas**: `resolve_event_families()` roda por empresa, nunca
  globalmente no artigo — precedência configurável em `config_risco.yaml` (`event_resolution`).
- **M&A legítimo vs. falso**: default é "M&A legítimo" quando há entidade nomeada como
  alvo/comprador; rejeição exige prova do contrário (recompra própria, ativo não
  empresarial, reorganização intragrupo, pós-aquisição, rumor não confirmado, negação
  explícita).
- **Fase jurídica**: desfecho (acordo/pagamento/encerramento/absolvição/arquivamento) tem
  precedência sobre menção a acusação antiga no mesmo texto.
- **Score real vs. peso-base**: qualquer número de "score impactado" deve vir de
  `build_evolution`, nunca de soma direta de peso-base da taxonomia (erro já cometido:
  −2005 vs. −269 reais).
- **Scoring EDGAR desligado**: `edgar_scoring_enabled` ausente/`False`.
- **Backfill explícito**: só `workflow_dispatch` manual com input `true`.
- **Links do Google News**: HTTP 200 de `news.google.com` nunca é resolução válida; toda
  fonte usa o mesmo resolvedor.

---

## 6. Decisões técnicas (merge dos três relatórios, sem duplicatas)

| Decisão | Problema | Justificativa | Estado atual | Evidência | Risco de regressão |
|---|---|---|---|---|---|
| Subgrupo por natureza do emissor, não instrumento | `Categoria` da posição usada como proxy | Ford/GM/Citigroup viravam "não listadas" por entrar via bond | CONFIRMADO_NO_REPO | [REPO] 105/31/8/16 no config | Alto se reclassificação futura usar `Categoria` de novo |
| `ticker` separado de `aliases` | Tickers curtos como alias geravam falso positivo | 48 tickers curtos removidos | CONFIRMADO_NO_REPO (não recontado nesta tarefa) | [RELATORIO-01] | Médio — checar amostra periodicamente |
| `edgar_eligible()` por relação com a SEC, não país | Critério por país deixava de fora ADRs estrangeiros | 19→32 elegíveis | CONFIRMADO_NO_REPO, 32 contado agora | [REPO] | Baixo |
| Separação `events_by_company`/`context_events_by_company`/`event_ids_for()` | Vazamento de atribuição entre empresas citadas no mesmo artigo | Caso CVS/JPMorgan | CONFIRMADO_NO_REPO, testado 75/75 | [REPO] | Alto se algum consumidor voltar a ler `rec["event_ids"]` direto |
| `resolve_event_families` por empresa | Dupla contagem rating+outlook (Rumo) | — | CONFIRMADO_NO_REPO | [REPO] `event_resolution` no config | Médio |
| Padrão Vale/Samarco (`subject_company`, `relation_type`) | Vale recebia RJ direta da Samarco | Investida/JV não é a monitorada | CONFIRMADO_NO_REPO, testado | [REPO][PRODUCAO] | Alto — não redesenhar sem os 75 casos passando |
| **Pendência: reclassificação informativa própria vs. contexto de terceiro** | Ambos caem em `context_events_by_company` | Usuário identificou que Santander/Esfera e Santander/lucro não deveriam usar o rótulo "contexto relacionado" | **PENDENTE** | [REPO][USUARIO-2026-07-31] | Alto — é uma lacuna de produto confirmada, não corrigir sem plano explícito |
| M&A com default invertido | Regra "exige palavra empresa/companhia" rejeitava 142 eventos legítimos | Viterra, Kimberly-Clark, Summit ESP | CONFIRMADO_NO_REPO, testado | [REPO] `ma_is_legitimate` | Alto — testar contra lista de positivos antes de qualquer nova regra |
| Score real via `build_evolution` | Relatório inicial usava soma de peso-base (−2005) | Não é o número exibido no dashboard | CONFIRMADO_NO_REPO (regra), valor não recalculado nesta tarefa | [REPO] | Médio |
| Emissor só-contexto continua visível | `per_company` só recebia entrada com evento pontuável | Escondia informação de contexto relevante | CONFIRMADO_NO_REPO (indício, não re-testado isoladamente nesta tarefa) | [RELATORIO-02][RELATORIO-03] | Médio |
| Resolvedor único de links (`resolve_gnews_token`, batchexecute) | Duas implementações divergentes (forte para principal, fraca para corroboradora) | 373 pendentes mesmo pós-v1 | CONFIRMADO_EM_PRODUCAO | [ACTIONS] run `30607645995` | Alto — não recriar segunda implementação "só para diagnóstico" |
| Gate EDGAR (`edgar_collection_enabled` AND estrito) | `or` em vez de `and` não travava de verdade | — | CONFIRMADO_NO_REPO | [REPO] | Alto se `main()` voltar a chamar `fetch_edgar_filings` incondicional |
| Scoring EDGAR como flag separada da coleta | `edgar_scoring_enabled` nunca era consultada | Modo sombra não existia de fato | CONFIRMADO_NO_REPO | [REPO][ACTIONS] artifact `edgar-4h3b-shadow-...` | Alto |
| `_normalize_edgar_forms` | `formularios_gatilho` gravado como string, `set()` iterava caracteres | Causa raiz do "zero filings" | CONFIRMADO_NO_REPO no código; **YAML ainda no formato antigo** (string) | [REPO] | Baixo — tolerado pelo código, mas higienizar o YAML é P2 |

---

## 7. Casos canônicos de regressão

| Caso | Erro histórico | Resultado esperado | Status atual | Teste |
|---|---|---|---|---|
| Vale / Samarco | RJ da Samarco atribuída à Vale | `events_by_company["Vale"]==[]`; Samarco mantém RJ; Vale recebe contexto | ✅ Confirmado | `test_semantica.py` t01/i01 |
| Rumo — rating/outlook | Dupla contagem (rebaixamento + outlook) | Só rebaixamento pontua | ✅ Mecanismo implementado; nunca observado ocorrendo em dado real de produção | `resolve_event_families`, teste sintético |
| CVS/JPMorgan (fraude) | JPMorgan (autor) recebendo fraude; CVS (ré) perdendo o evento | CVS mantém fraude (`allegation/lawsuit`); JPMorgan não | ✅ Confirmado | `--test-attribution` |
| Ford Motor (Lemon Law fraud) | Ford (autora) virando `defendant_accused` por resumo duplicado | Ford sem evento (é autora) | ✅ Confirmado | `--test-attribution` |
| Gerdau / recompra de ações | Recompra classificada como M&A | `recompra_acoes`, não pontua | ✅ Confirmado | `test_semantica.py` |
| Gerdau / transportadoras (falência de terceiro) | Falência de terceiro atribuída à Gerdau | Gerdau sem falência; contexto, score 0 | ✅ Confirmado | `test_semantica.py` |
| Cencosud / St. Marche | Cencosud (compradora) recebendo RJ do alvo | Cencosud sem RJ; M&A legítimo preservado | ✅ Confirmado | `test_semantica.py` |
| BTG / Digimais | Falência do Digimais no BTG; rumor tratado como M&A confirmado | BTG sem falência; rumor não pontua | ✅ Confirmado | `test_semantica.py` |
| JBS (encerramento de fraude antiga) | Fase de desfecho tratada como fraude nova | Não pontua como fraude crítica | ✅ Confirmado | `test_semantica.py` |
| **Santander / Esfera** | Incorporação de subsidiária integral | Deveria ser evento corporativo informativo direto | ⚠️ **Vai para `context_events_by_company` como "Contexto relacionado"** — pendência confirmada (§6, §5) | `test_semantica.py` (mecanismo existe, rótulo incorreto) |
| **Santander / TSB, Santander / lucro pós-aquisição** | Resultado pós-aquisição tratado como novo M&A | Deveria ser evento direto positivo/informativo, não novo M&A e não contexto | ⚠️ **Mesma pendência** — evento vira `integracao_pos_aquisicao`, mas cai no bucket de contexto | Pendência confirmada |
| LATAM / aeronaves | Financiamento de aeronaves tratado como M&A | `aquisicao_capex`, não pontua | ✅ Confirmado | `test_semantica.py` |
| General Motors / histórico | "This Day in History" (Chapter 11 de 2009) tratado como RJ atual | `historical_reference=true`, não pontua | ✅ Confirmado | `test_semantica.py` |
| M&A legítimos (Viterra, Kimberly-Clark, Summit ESP) | Regra ampla rejeitando M&A real | Devem continuar pontuando | ✅ Confirmado | `test_semantica.py` |
| Links Rumo, Engie/Valor | Corroboradoras não abriam na rede corporativa | URL direta ou "Link em verificação" sem botão quebrado | ✅ Confirmado em produção real (1045/453/31) | `test_links.py`, run `30607645995` |
| Vale/links (regressão) | Fontes que já funcionavam não podem quebrar | URL direta preservada | ✅ Confirmado | `test_links.py` |
| EDGAR mixed-event (rating + RJ de terceiro no mesmo artigo) | Rating vazava para a Samarco | Atribuição escopada por oração | ✅ Confirmado (indício de código) | Suíte de atribuição |

---

## 8. Fontes e cobertura

Fontes ativas [REPO]: Google News (por locale/país), CVM/IPE (Brasil, dataset IPE),
RI (RSS/scraping Tier 1 declarado em `official_sources`/`fund_sources`), SEC/EDGAR
(**desativado hoje** — ver §11), fontes internacionais rotacionadas por Tier (buckets
por hash do emissor, `fetch_every_n_runs`).

Distinção configurado × tentado × sucesso × resultado: mantida via `run_meta.json`
(`international_search_execution`, `official_source_execution`) e
`international_search_history.json` (cumulativo, até 8 execuções). Esta tarefa **não**
recalculou os números de cobertura internacional atuais (seria necessário rodar
`--audit-international-coverage`, que é leitura/agregação, não backfill — pode ser feito
em tarefa futura sem risco). O `run_count` atual é **59** [REPO].

Fontes oficiais latino-americanas (CMF Chile, BMV/CNBV México, SIMEV Colômbia, CNV
Argentina): permanecem como "documentar apenas" — nenhum scraper implementado
[RELATORIO-01][RELATORIO-03], decisão do usuário condicionada a resultado de probe real.

---

## 9. Scoring

O score exibido no dashboard é o resultado de `build_evolution()` — peso-base da
taxonomia × decaimento temporal por meia-vida × confiança da fonte + bônus de fontes
corroborando o mesmo evento [REPO][RELATORIO-03 DEC-08]. **Não** é soma direta de
peso-base — esse erro já foi cometido uma vez (relatório inicial de auditoria semântica
apresentou −2005 pontos de peso-base, quando o score real removido era −269). Esta
tarefa não recalculou o valor atual (seria necessário rodar `--reclassify-semantic-only`
contra um arquivo de saída separado, fora do escopo de leitura pura). Qualquer relatório
futuro de "score impactado" deve citar explicitamente que veio de `build_evolution`.

---

## 10. Links

Causa raiz histórica: `resolve_history_urls()` só processava as chaves principais de
`history["articles"]`; `corroborations`/`corrob_sources` nunca eram resolvidas — medido
39,0% das principais vs. 98,6% das corroboradoras com redirecionador do Google não
resolvido [RELATORIO-03 DEC-10]. Depois, uma segunda causa raiz: existiam duas
implementações divergentes do resolvedor do Google News (uma forte, com `batchexecute`,
usada só para principais; outra fraca, sem `batchexecute`, usada para corroboradoras e
para todo o `--repair-links-only`) [RELATORIO-03 DEC-11].

Resolvedor canônico hoje: `link_debt_audit.resolve_gnews_token()` (cache → decode inline
→ `batchexecute` → redirect + `data-n-au`), usado por todos os caminhos [REPO].

**Resultado real mais recente, confirmado via `gh run view --log` no run `30607645995`
(2026-07-31T05:44Z, modo `repair`, rede real habilitada)** [ACTIONS]:
- Links auditados: **1045**
- Fontes principais: **572** / corroboradoras: **473**
- Redirecionadores encontrados: **484** (453 resolvidos + 31 pendentes)
- Registros com campo de link alterado: **368**
- Saúde principais: `url_direta_valida`=553, `redirect_nao_resolvido`=16,
  `homepage_generica`=2, `dominio_suspeito`=1
- Saúde corroboradoras: `redirect_resolvido`=453, `redirect_nao_resolvido`=15,
  `url_direta_valida`=5
- `http_404_410`=0, `http_403_paywall`=0

`risk_history.json` de produção confirma `links_repaired_at = 2026-07-31T05:45:56+00:00`
[PRODUCAO] — batendo exatamente com o horário do run.

Interface para links não resolvidos: `link_display_decision()`/`interface_decision()`
decide se exibe botão ("Abrir notícia") ou "Link em verificação" sem `<a>` quebrado — sem
gerar `href` vazio, sem esquema `javascript:`/`data:`/`file:`, sem apontar para
`news.google.com`/`google.com/url` [REPO — confirmado por `test_links.py`, casos
específicos de "nunca gera `<a>`" e "nunca render_anchor com href vazio"].

Invariância de score/eventos durante reparo: `--repair-links-only` só altera campos de
link, nunca score/eventos — não recomputado/reverificado nesta tarefa via hash, mas o
padrão de checagem de invariância (hash antes/depois, abort automático) está documentado
no workflow [REPO].

---

## 11. EDGAR

Histórico dos bugs (conforme Report-3, seção 7 DEC-01 a DEC-05):
1. `formularios_gatilho` gravado como string YAML em vez de lista → `set()` iterava
   caracteres → zero filings para todos os 32 elegíveis. Corrigido por
   `_normalize_edgar_forms()`.
2. Flag mestre em `or` em vez de `and` → não travava de verdade. Corrigido:
   `edgar_collection_enabled = global is True and source is True`.
3. `edgar_scoring_enabled` existia mas nunca era consultada em `main()` → filings
   entravam incondicionalmente no pipeline pontuável. Corrigido: roteamento explícito em
   três listas (`production_articles`/`edgar_shadow_articles`/`edgar_scoring_articles`).
4. Parser de diagnóstico (`sample_filings()` em `edgar_audit_4h2.py`) descartava `items`
   do 8-K, produzindo títulos vazios no primeiro shadow real — invalidando a avaliação de
   qualidade daquele ciclo. Um parser canônico (`edgar_canonical.py`) foi proposto para
   unificar, **mas nunca chegou a ser mesclado no repositório** [REPO — confirmado
   ausente].

Gate mestre: `edgar_collection_enabled(cfg)` exige `international_official_sources_enabled
is True` **e** `official_sources.EUA.enabled is True`. Hoje **nenhuma das duas chaves
está presente** no `config_risco.yaml` de produção ⇒ coleta desligada [REPO]. Confirmado
em produção real: log do run `30629264808` (Update Risk Dashboard, 2026-07-31T12:04Z)
diz literalmente: *"🇺🇸 SEC/EDGAR: desativado (exige international_official_sources_enabled=true
E official_sources.EUA.enabled=true) — 0 filings, score inalterado."* [ACTIONS]

Separação collection/scoring: `edgar_scoring_enabled(cfg)` = `edgar_collection_enabled(cfg)
and cfg.get("edgar_scoring_enabled", False) is True` — AND estrito, scoring nunca liga
sem a coleta também estar ligada [REPO].

Shadow mode: só roda via `workflow_edgar_shadow.yml`, isolado com `force=True`,
`permissions: contents: read` (nunca commita), prova invariância por hash antes de tocar
qualquer coisa [REPO]. Um artifact real (`edgar-4h3b-shadow-30558094852-...`) confirmou
`scoring_enabled: false`, `persisted_records: 0` numa execução anterior
[RELATORIO-03][ACTIONS, não re-baixado nesta tarefa].

O que falta para scoring: (a) `edgar_canonical.py` (parser único) precisa existir e ser
mesclado; (b) um segundo shadow real precisa medir qualidade de classificação de forma
não-vazia (o primeiro ficou vazio por perda de `items`); (c) aprovação explícita do
usuário. **Proibição atual**: não ativar `edgar_scoring_enabled=true` em nenhum config de
produção sem esse ciclo completo.

`sample_filings()`/`edgar_audit_4h2.py` continuam existindo como script de diagnóstico —
não confirmado nesta tarefa se algum caminho de produção ainda os chama incondicionalmente
(ver backlog P1: verificar e isolar).

---

## 12. Workflows e operação

| Workflow | Arquivo | Trigger | Permissões | O que pode alterar | Quando usar |
|---|---|---|---|---|---|
| Update Risk Dashboard | `.github/workflows/update_risk_dashboard.yml` | cron `0 10,13,16,19 * * 1-5` + `workflow_dispatch` (inputs `reclassify`, `backfill`, `audit_cvm`, `probe_sources`) | `contents: write` | `risk_history.json`, `dashboard_risco.html`, `index.html`, telemetria | Ciclo normal (cron) ou reprocessamento controlado via dispatch |
| Auditoria e reparo de links | `.github/workflows/workflow_link_audit.yml` | `workflow_dispatch` (input `mode: audit\|repair`, `verify_status`) | `contents: write` (necessário só no modo `repair`) | `risk_history.json`, `index.html`, `dashboard_risco.html` (só campos de link) | `audit` para medir sem alterar; `repair` só quando quiser aplicar |
| EDGAR shadow run (4H.3B) | `.github/workflows/workflow_edgar_shadow.yml` | `workflow_dispatch` | `contents: read` (nunca commita) | Nada — só sobe artifact de diagnóstico | Diagnóstico isolado, nunca produção |

Ver `docs/OPERATIONS.md` para o runbook completo (como rodar cada um pela UI, inputs
seguros/perigosos, como verificar Summary/artifacts, como reverter).

---

## 13. Testes

Ver `docs/TEST_MATRIX.md` para a matriz completa. Resumo: todos os testes não
destrutivos disponíveis foram executados nesta tarefa e passaram integralmente —
`py_compile` (10 arquivos, exit 0), `test_semantica.py` (102/102), `test_links.py`
(92/92), `test_edgar_4h2.py` (23/23), `test_edgar_4h3a.py` (48/48), `test_edgar_4h3b.py`
(59/59), `--test-attribution` (75/75), `--test-cvm-fixture` (11/11),
`--test-fund-coverage` (10/10). Nenhum teste que exigiria rede real ou alteraria arquivos
de produção foi executado.

---

## 14. Problemas conhecidos

**Confirmado**: pendência de roteamento Santander (§7); `formularios_gatilho` no YAML
ainda em formato de string (tolerado pelo código, mas não higienizado); parser canônico
EDGAR nunca mesclado; múltiplos configs candidatos de fases anteriores não consolidados
(`config_risco_4h3b_candidato.yaml`).

**Pendente de validação**: se `resolve_event_families` (colapso rating+outlook) já
ocorreu de fato em um caso real do histórico de produção (nunca observado, só testado
sinteticamente); se `edgar_audit_4h2.py`/`sample_filings()` ainda são chamados por algum
caminho de produção.

**Risco de produção**: nenhum identificado nesta auditoria além da pendência Santander
(que é uma lacuna de qualidade semântica, não uma falha de disponibilidade).

**Dívida técnica**: configs candidatos remanescentes; `formularios_gatilho` como string;
ausência de scrapers para CMF/BMV/SIMEV/CNV (decisão deliberada, não dívida urgente).

**Questão metodológica**: se/quando eventos indiretos mitigadores devem ter impacto
positivo no score (proposta `PROPOSTA_SCORE_INDIRETO.md` mencionada nos relatórios, não
encontrada no repositório atual — não aprovada, não implementada).

---

## 15. Backlog priorizado

Ver `docs/BACKLOG.md` para a lista completa com P0-P3.

---

## 16. Quickstart (30 min para um agente novo)

1. `git fetch origin main` e confirmar que o clone não está atrás do remoto (5 min).
2. Ler este documento por inteiro (15 min).
3. Ler `docs/HANDOVER_CONFLICTS.md` para saber o que os relatórios de chat erram hoje (5 min).
4. Rodar a suíte de testes segura do `CLAUDE.md`/`docs/TEST_MATRIX.md` e confirmar que os
   números batem com os documentados aqui (5 min).
5. Antes de qualquer mudança, ler `docs/OPERATIONS.md` para saber o que cada workflow
   pode e não pode fazer.

---

## 17. Glossário

- **Emissor / empresa monitorada**: entidade da watchlist cujo risco é acompanhado.
- **`events_by_company`**: eventos pontuáveis atribuídos corretamente por empresa.
- **`context_events_by_company`**: eventos não pontuáveis — hoje mistura contexto de
  terceiro real com reclassificação informativa da própria empresa (pendência).
- **`event_ids_for(registro, empresa)`**: função central, única fonte de verdade de
  quais eventos pontuam para uma empresa específica.
- **`scoreable`**: booleano — se o evento contribui ao score.
- **`subject_company` / `monitored_company`**: sujeito real do evento vs. empresa da
  watchlist sendo avaliada — podem divergir (Vale ≠ Samarco).
- **Padrão Vale/Samarco**: mecanismo canônico de mover evento de terceiro para contexto
  sem pontuar a empresa monitorada errada.
- **`resolve_event_families`**: resolve exclusividade semântica (1 evento pontuável por
  família, por empresa, nunca globalmente no artigo).
- **Shadow mode**: coleta/classificação de uma fonte sem que o resultado entre no
  histórico pontuável nem afete o score.
- **`edgar_scoring_enabled` / `edgar_collection_enabled`**: flags separadas — coleta pode
  estar ligada com scoring desligado (shadow real), nunca o contrário.
- **`batchexecute`**: endpoint interno do Google News usado para decodificar o destino
  real de um link `news.google.com/rss/articles/...`.
- **`link_health`**: classificação do estado de um link após resolução.
- **Score real / ponderado**: resultado de `build_evolution` — nunca soma de peso-base.
- **Tier**: nível de materialidade do emissor na carteira (T1/T2/T3 por representação financeira).
- **`applies_to`**: campo de evento da taxonomia que define a quais grupos de ativos ele se aplica.

---

## 18. Fontes e proveniência

- Objetivos, motivações, histórico de decisões, casos de regressão nomeados: três
  relatórios de chat em `docs/handover/raw/` (Report-1: Fases 1-4A, cobertura/watchlist;
  Report-2: Fases 4D-4H, atribuição empresa×evento e telemetria; Report-3: EDGAR shadow,
  auditoria semântica, resolução de links).
- Estado atual de código, config, contagens: leitura direta dos arquivos em
  `C:\Users\User\OneDrive\files_DashRisk` pós-`git fetch`.
- Execução real de produção: `gh run list`/`gh run view --log` para os runs
  `30607645995` (reparo de links), `30629264808` e `30607780196` (Update Risk Dashboard).
- Fatos mais recentes e a pendência Santander: fornecidos diretamente pelo usuário em
  2026-07-31, verificados por leitura de código nesta tarefa (não apenas aceitos por
  afirmação).
- Resultados de teste: execução direta nesta tarefa com
  `C:\Users\User\AppData\Local\Programs\Python\Python310\python`.
