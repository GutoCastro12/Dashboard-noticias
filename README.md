[README.md](https://github.com/user-attachments/files/29868348/README.md)
# Radar de Risco — Monitor de Notícias de Emissores

Dashboard de acompanhamento de notícias para a área de risco, com classificação por severidade, **scoring semanal**, **evolução por emissor em 90 dias** e **monitoramento em três tiers**. Arquitetura: `YAML → fetch Google News → classificação → HTML estático via Jinja2`, sem backend.

## Como funciona

```
config_risco.yaml ──► risk_dashboard.py
                        1. Fetch:      Google News RSS (agendado por tier)
                                       + fatos relevantes da CVM (dados abertos, IPE)
                        2. Classifica: taxonomia por keywords (palavra inteira, sem acento/caixa)
                                       + regra de especificidade + direção (negativo pontua,
                                       positivo é contexto)
                        3. Deduplica:  mesma matéria em vários veículos conta 1x (similaridade)
                        4. Valida:     eventos CRÍTICOS confirmados via Gemini antes de pontuar
                                       (opcional; descarta especulação/negação — fail-open)
                        5. Agrega:     score semanal (radar, 7d) e evolução (90d) com decaimento
                        6. Persiste:   risk_history.json (retenção 120d; classificação original
                                       é preservada — URLs conhecidas não são reprocessadas)
                        7. Renderiza:  dashboard_risco.html
```

## Uso

```bash
pip install -r requirements.txt
python risk_dashboard.py --config config_risco.yaml          # execução real
python risk_dashboard.py --config config_risco.yaml --demo   # dados simulados
```

## As três visões do dashboard

**1. Radar semanal** — ranking por score acumulado em 7 dias, com selo ATENÇÃO IMEDIATA acima do threshold (80 pts).

**2. Evolução por emissor (90 dias)** — o risco raramente surge de um evento isolado; deteriorações acontecem em sequência (outlook negativo → downgrade → renegociação → RJ). Cada emissor ganha uma linha do tempo com um ponto por sinal (colorido pela severidade, clicável, com opacidade proporcional à recência), uma **sparkline com a trajetória do score** (reconstruída dia a dia do histórico — mostra a inclinação da deterioração) e um status:

| Status | Regra (configurável em `evolution.status`) |
|---|---|
| 🔴 **Crítico** | Fato duro na janela (evento com score bruto ≥ 90: RJ, default, falência, fraude) — não "envelhece" — OU score ponderado ≥ 140 |
| 🟠 **Atenção elevada** | Score ponderado ≥ 60 OU 2+ tipos de evento negativos distintos |
| 🟡 **Monitorar** | Qualquer evento negativo na janela |

**Decaimento temporal**: o score acumulado usa meia-vida de 30 dias (`evolution.decay`) — evento de hoje vale 100%, de 30 dias vale 50%, de 60 dias vale 25%. O status prioriza deterioração *recente*; só fatos duros mantêm o vermelho indefinidamente dentro da janela.

**Eventos positivos como contexto**: upgrade de rating, outlook positivo e recomendação elevada aparecem no feed (grupo 🟢) e como pontos verdes na timeline, mas têm score 0 — nunca pontuam risco. Emissores só com sinais positivos não entram na evolução.

**3. Feed classificado** — todas as notícias da semana agrupadas por severidade, com filtros por texto, severidade e empresa.

## Tiers de monitoramento

| Tier | Emissores | Frequência de busca |
|---|---|---|
| **T1** | 15 nomes de maior exposição (PETR, VALE3, ITUB4, BBAS3, BBDC4, BPAC11, WEGE3, ABEV3, SUZB3, PRIO3, VBBR3, RENT3, RADL3, RDOR3, EQTL3) | Toda execução (com o workflow 4x/dia ≈ tempo real) |
| **T2** | Demais large caps da watchlist (~32 nomes) | Execuções alternadas |
| **T3** | Empresas menores | Desligado; ative por emissor com `force_fetch: true` quando houver exposição relevante |

Frequências em `tiers.fetch_every_n_runs`. A watchlist já inclui todos os tickers como aliases (PETR4/PETR3, SANB11/SANB4/SANB3 etc.), com matching por palavra inteira para evitar falsos positivos de siglas curtas.

## Severidades e scoring por notícia

| Severidade | Eventos (score) |
|---|---|
| 🔴 **Crítico** | Recuperação Judicial (100) · Default (100) · Falência (95) · Fraude (90) · Suspensão de negociação (85) · Intervenção regulatória (85) · Covenant breach (80) · Rebaixamento de rating (80) · Renúncia do auditor (75) |
| 🟠 **Alto impacto** | Lucro muito abaixo do consenso (45) · M&A (40) · Guidance negativo (35) · Grande emissão de dívida (35) · Follow-on (30) · Troca de CEO (25) |
| 🟡 **Médio** | Revisão de outlook (20) · Revisão de recomendação (15) · Mudanças regulatórias (15) · Índice (10) · Pequenas aquisições (10) |

Score semanal = soma dos tipos de evento distintos na janela + bônus de intensidade (`extra_per_article`, teto `extra_cap`). Eventos podem declarar `suppresses` para prevalecer sobre genéricos (ex.: "Pequenas aquisições" suprime "M&A").

## Fontes

- **Google News RSS** — consultas por emissor (agendadas por tier) + buscas amplas de mercado.
- **CVM · Fatos Relevantes** (`cvm_fatos_relevantes` no config) — dataset IPE de dados abertos (dados.cvm.gov.br). RJ, waiver, renúncia de auditor e troca de comando são protocolados aqui antes de virar manchete, e a atribuição de empresa vem do próprio protocolo (sem ambiguidade). O arquivo anual é baixado a cada execução e filtrado pelos últimos `lookback_days` dias.

## Qualidade do sinal

- **Palavra inteira**: keywords e aliases casam por palavra inteira sobre texto normalizado ("OPA" não casa com "opaco"; "TIM" não casa com "timing").
- **Direção**: outlook/recomendação têm variantes negativa (pontua) e positiva (contexto, score 0) — antes, "perspectiva positiva" pontuava como risco.
- **Deduplicação** (`dedup`): títulos com similaridade ≥ 0.80, mesmas empresas e datas próximas (±3 dias) contam uma vez — dentro do lote e contra o histórico. Fica a publicação mais antiga.
- **Validação LLM dos críticos** (`llm.enabled` + `validate_critical`): eventos críticos detectados por keyword são confirmados pelo Gemini antes de pontuar — descarta especulação ("pode ser rebaixada"), negação ("afasta risco de RJ") e menções históricas. Só artigos novos são validados (URLs já no histórico não reprocessam) e falha da API mantém a keyword (fail-open).

## Deploy — 100% gratuito (GitHub Actions + Render Static Site)

Arquitetura: **GitHub Actions** roda o pipeline de graça e commita o resultado (`risk_history.json` + `index.html`) de volta no repositório; **Render Static Site** (tier free) fica de olho no mesmo repositório e republica sozinho a cada push. Sem Cron Job pago, sem disco, histórico versionado no Git.

### 1. Suba o projeto para o GitHub

```bash
cd radar-de-risco
mkdir -p .github/workflows
mv update_risk_dashboard.yml .github/workflows/
git init
git add .
git commit -m "Radar de Risco — versão inicial"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/radar-de-risco.git
git push -u origin main
```

O workflow precisa estar em `.github/workflows/` — é aí que o GitHub procura arquivos de Actions.

### 2. Rode o workflow pela primeira vez

GitHub → aba **Actions** → **Update Risk Dashboard** → **Run workflow**. Ele instala as dependências, roda `risk_dashboard.py`, gera o `dashboard_risco.html`, copia para `index.html` e commita os dois de volta no repositório (o `GITHUB_TOKEN` usado no `git push` já vem automático do Actions — não precisa criar nada). Depois disso ele roda sozinho 4x/dia em dias úteis, conforme o `cron:` no workflow.

### 3. Conecte o Render ao repositório

1. [render.com](https://render.com) → **New → Blueprint**
2. Conecte o repositório `radar-de-risco` (o Render lê o `render.yaml` automaticamente e propõe o serviço `radar-de-risco-dashboard`, tier **Free**)
3. **Apply**

A cada novo commit do GitHub Actions (ou seja, a cada execução do pipeline), o Static Site do Render redeploya sozinho — a URL pública fica em algo como `radar-de-risco-dashboard.onrender.com`.

### Custo: R$ 0

GitHub Actions é gratuito para repositórios públicos (e tem minutos grátis mensais mesmo em privados); Static Site do Render é gratuito sem spin-down, já que ele só serve arquivo estático — a limitação de "dorme após 15 min" é dos **Web Services** free, não dos Static Sites.

### Por que commitar o histórico em vez de usar `actions/cache`?

`actions/cache` evicta entradas não acessadas há 7 dias e tem limite de 10GB por repositório — arriscado para um histórico que você quer manter por 90 dias (a evolução por emissor). Commitar direto no Git é permanente, versionado e dá pra auditar (você vê no histórico do GitHub exatamente quando cada emissor mudou de status).

## Limitações conhecidas

- Keywords ainda podem errar em manchetes ambíguas de severidade média/alta (a validação LLM cobre só os críticos por padrão; o fallback `classify_with_gemini` cobre artigos sem match).
- Com decay ligado, um evento crítico "mole" antigo (ex.: downgrade de 80 dias) decai para Atenção/Monitorar por desenho — o alerta em tempo real aconteceu na época, via radar semanal.
- Atribuição de empresa exige citação no **título** (precisão > recall). Notícias com evento mas sem emissor identificado entram como "Mercado (geral)" e não pontuam.
- A evolução de 90 dias só fica completa depois de ~90 dias de execuções acumuladas (o histórico começa vazio).
- Google News RSS não é API oficial: sujeito a limites não documentados (o script espaça as requisições).
