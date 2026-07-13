[README.md](https://github.com/user-attachments/files/29984332/README.md)
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

## Limiares adaptativos (calibrados na carteira, ancorados à realidade)

Os limiares de score acumulado ("Atenção elevada" e "Crítico") não são números fixos: a cada execução são recalibrados como **percentis da distribuição real da carteira** (amostra = pico semanal do score ponderado por emissor, só emissor-semanas ativos, janela de 90 dias — desenho que evita amostrar o mesmo episódio dezenas de vezes). Padrão: Atenção = p75, Crítico = p95 dos emissores ativos. Três âncoras impedem o sistema de descolar da realidade: **(1)** fatos duros (RJ, default, falência, fraude) são Crítico **absoluto**, nunca relativos ao regime; **(2)** o limiar efetivo só flutua dentro da banda `[0.6×, 1.5×]` do valor base — numa crise sistêmica tipo mar/2020, o limiar trava no teto e a tela fica vermelha *mesmo*, em vez de a crise ser normalizada para fora; **(3)** amostra insuficiente (< 40 emissor-semanas, típico do início) → valores base, com aviso explícito na UI e no log. Cada linha da evolução mostra também o **percentil do emissor na carteira** ("45 pts · pctl 92") — o contexto de regime sem perder o número absoluto auditável. Tudo em `evolution.status.adaptive`.

## Auditabilidade e confiança da fonte

- **Score decomposto e auditável**: na aba Evolução, clique em qualquer emissor para abrir a decomposição — evento a evento: data, fonte, dimensões de risco, peso-base × fator de decaimento × confiança da fonte = contribuição, somando exatamente o score exibido. Nada de número mágico.
- **Confiança da fonte pondera o score** (`source_trust`): cada notícia recebe um tier pelo domínio — Fonte oficial (CVM/B3/RI, peso 1.0), Agência de rating (1.0), Imprensa consolidada (0.9) e Fonte não verificada (0.6). Uma RJ divulgada em fato relevante vale cheio; a mesma manchete num site desconhecido vale 60%. Além do tier, há **ajuste fino por veículo** em `source_trust.overrides` (ex.: Valor, Brazil Journal, Reuters e Bloomberg pesam 0.95, acima dos 0.9 da imprensa em geral). O tier aparece como badge em cada notícia, junto do horário de publicação e de captura.
- **Feeds diretos de RI e agências** (`ri_feeds` / `custom_feeds`): qualquer emissor da watchlist pode ter as URLs RSS da página de comunicados do próprio RI — as notícias entram com confiança *oficial* (1.0), atribuição forçada ao emissor, e chegam antes da imprensa. O dedup reconhece o par "anúncio oficial × cobertura de imprensa" do mesmo fato (mesmo emissor + mesmos eventos + ≤2 dias) e mantém **a versão oficial**, mesmo quando a de imprensa chegou primeiro ao histórico. `custom_feeds` aceita qualquer RSS adicional com tier configurável — S&P/Moody's/Fitch não têm RSS público (são produtos comerciais), mas se a mesa contratar um feed, é só plugar com `trust_tier: agencia`; enquanto isso, rating actions chegam via imprensa e, quando o domínio é da própria agência, pesam 1.0 automaticamente.
- **Severidade ≠ direção**: severidade (🔴 Crítico / 🟠 Alto / 🟡 Médio / 🔵 Baixa) mede relevância; direção (▼ negativa / ◆ neutra-incerta / ▲ positiva) mede o sinal. M&A é Alto mas neutro — relevante, e o sentido depende da estrutura. Direção positiva zera o score; há filtro por direção no feed.
- **Dimensões de risco**: cada evento carrega tags de dimensão afetada (Crédito, Mercado, Liquidez, Governança, Operacional, Regulatório) — ex.: Covenant breach = Crédito • Liquidez • Governança.
- **Deterioração persistente**: acúmulo de sinais (padrão: 3+ ocorrências negativas de 2+ tipos em 45 dias) gera o selo "⚠ Deterioração persistente" e eleva o status para pelo menos Atenção elevada — a sequência importa mais que a manchete isolada.
- **Aba "O que mudou"**: primeira tela do dashboard — sinais novos capturados desde a última execução, transições de status (Monitorar → Atenção elevada) e maiores variações de score. Para o uso diário, o delta importa mais que o ranking absoluto.

## Nível de confirmação (gravidade ≠ confiabilidade)

Cada notícia carrega, além da severidade do evento, um **confirmation_level** — a confiabilidade da informação em si: 🟢 **Confirmado** (fonte oficial: RI/CVM/B3/SEC) · 🟡 **2+ fontes independentes** (a mesma história corroborada por veículos confiáveis distintos) · 🟠 **Uma fonte confiável** · 🔴 **Não confirmada/rumor** (fonte não verificada, sem corroboração). A corroboração nasce da deduplicação: quando duas coberturas do mesmo fato são fundidas, as fontes fundidas ficam registradas no sobrevivente (o badge mostra "também em: …" no tooltip). Um evento grave de rumor e um evento grave confirmado deixam de parecer a mesma coisa.

## Hierarquia de fontes oficiais por emissor

Cada emissor (bloco `official` na watchlist) tem a hierarquia: **1)** `rss` — feed RSS de comunicados do RI; **2)** `news` — página de notícias do RI, raspada por um **scraper multi-estratégia** que cobre qualquer tipo de site: (a) auto-descoberta de RSS anunciado na própria página, (b) extração de âncoras do HTML estático com captura da data de publicação no entorno do link, (c) mineração do JSON embutido de SPAs (`__NEXT_DATA__`/`ld+json` — pega sites Next.js/Nuxt sem renderizar nada), e (d) renderização headless via Playwright/Chromium para sites 100% JavaScript (o workflow instala e cacheia o navegador; desligue com `ri_scraper.use_browser: false`); **3)** CVM/IPE — automático para toda listada; **4)** imprensa via Google News. Itens raspados sem data de publicação entram com a data de captura da primeira vez em que foram vistos. **Resiliência a mudanças de caminho**: se a URL de `official.news` quebrar ou render pouco (sites de RI mudam de estrutura ao trocar de fornecedor — MZ, RIWeb, Ten Meetings…), o crawler volta à home (`official.ri`), varre os links e localiza sozinho a seção cujo caminho contenha os termos de `ri_scraper.preferred_paths` (fatos-relevantes → comunicados → notícias → resultados, em ordem de preferência) — o log mostra `auto-localizado (/novo-caminho/)` quando isso acontece. Os 16 emissores do Tier 1 já estão com as URLs de home e de seção de comunicados preenchidas. Os 16 emissores do Tier 1 (agrupados por setor: bancos, commodities, utilities, consumo, infraestrutura) já têm o bloco com a URL da página de RI preenchida como referência — **valide cada URL antes de preencher `rss`/`news`**, que são os campos que disparam coleta.

Os pesos por veículo foram refinados em 5 categorias (`source_trust.overrides`): **A** oficial 1.00 · **B** agências e wires 0.99 (Reuters, Bloomberg, S&P/Moody's/Fitch) · **C** econômicos premium 0.95–0.97 (Valor, Pipeline, Brazil Journal, Broadcast, NeoFeed) · **D** financeiros 0.90–0.93 (InfoMoney, Exame, E-Investidor, Investing, Money Times) · **E** generalistas 0.88–0.90 (Estadão, Folha, O Globo, CNN, UOL).

## Navegação e janelas de tempo

O dashboard abre com **três abas** — 📊 Radar de emissores, 📈 Evolução por emissor e 📰 Notícias classificadas — e um **seletor de janela global** (7 dias, 30 dias, 90 dias ou 1 ano) que recalcula as três visões instantaneamente (tudo pré-computado no build; a troca é client-side, sem recarregar). O estado fica na URL (`#evolucao/90`), então dá para compartilhar um link já aberto na visão e janela desejadas. O selo ATENÇÃO IMEDIATA só aparece em janelas de até 30 dias (score acumulado de 1 ano não é comparável ao threshold semanal).

Observação: janelas longas só ficam completas conforme o histórico acumula — o `risk_history.json` começa vazio e retém 400 dias.

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

## Classes de ativo

A watchlist aceita três classes via `type` (padrão `empresa`):

| Classe | Como monitora | Observações |
|---|---|---|
| `empresa` | Google News (nome + tickers) + fatos relevantes CVM/IPE | Cobertura completa |
| `fii` | Google News com termos de fundo (rendimento, vacância, inadimplência, emissão de cotas, CRI…) + eventos próprios na taxonomia: Default de CRI (🔴 80), Inadimplência de locatário (🟠 40), Corte de rendimento (🟠 35), Vacância (🟠 30), Emissão de cotas (🟡 15) | Fatos relevantes de FIIs saem via Fundos.NET/B3 e **não** estão no dataset IPE — a cobertura vem da imprensa especializada (Clube FII, InfoMoney etc.), que replica rápido |
| `nao_listada` | Google News por nome, com termos de crédito/reestruturação | Sem ticker a cobertura depende da imprensa; se a empresa tiver **debêntures registradas** na CVM (categoria B), os fatos relevantes dela entram normalmente pela fonte CVM/IPE |

Os 8 FIIs listados geridos pela Vinci já estão na watchlist (VILG11, VISC11, VINO11, VIUR11, VCRI11, VICA11, VIGT11, VORE11) com badge própria no dashboard. Para o portfólio de private equity, adicione as empresas com `type: nao_listada`, `tier: 3` e `force_fetch: true` conforme conseguir a lista de posições.

## Watchlist derivada da base de posições

A watchlist foi expandida a partir da base de posições da Vinci (30/06/2026, R$ 79 bi de exposição): 24 emissores novos entraram com tier proporcional à exposição consolidada em BRL (anotada em comentário em cada entrada), incluindo o portfólio de private equity e crédito privado (`type: nao_listada`). Ficaram de fora, por desenho: veículos próprios da Vinci (fundos/FIDCs internos — risco monitorado no nível dos ativos subjacentes), imóveis individuais dos fundos imobiliários (shoppings/lajes não são emissores de notícia; o risco aparece via os FIIs e locatários), títulos públicos e caixa, e SPVs de nome genérico ("Duna", "Madeira Ltd", "AGV") cujo monitoramento por keyword geraria mais falso positivo que sinal — se houver nomes completos/razões sociais melhores, dá para incluí-los com aliases seguros. Emissores cujo nome é palavra comum ("Vamos", "Motiva", "Ciranda", "Stone") usam apenas aliases compostos, e o matcher considera somente os aliases quando existem.

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

- **Guardas de negação**: manchetes que citam o evento para negá-lo ou dá-lo por superado não pontuam — "para **evitar** recuperação judicial", "**afasta** rebaixamento", "**deixou** a RJ para trás", "S&P **reafirma** rating". Configurável por evento via `negations` na taxonomia.
- **Títulos normalizados**: o sufixo " - Veículo" que o Google News anexa (e "| Seção") é removido na origem — era ele que deixava a mesma matéria escapar da deduplicação entre veículos.
- **Reclassificação retroativa**: `python risk_dashboard.py --config config_risco.yaml --reclassify` reprocessa TODO o histórico com as regras atuais (taxonomia, negações, dedup, validação LLM) e salva limpo. No GitHub, use o botão **Run workflow** marcando a opção "Reprocessar todo o histórico". Rode sempre que atualizar keywords — classificações antigas não se corrigem sozinhas.

- **Palavra inteira**: keywords e aliases casam por palavra inteira sobre texto normalizado ("OPA" não casa com "opaco"; "TIM" não casa com "timing").
- **Direção**: outlook/recomendação têm variantes negativa (pontua) e positiva (contexto, score 0).
- **Fraude restrita a fraude corporativa** ("fraude contábil", "manipulação de resultados"...) — condenações por golpes contra clientes ou menções a fraude de terceiros não disparam mais o evento crítico.
- **Investigação ≠ intervenção**: "CVM abre processo"/buscas viraram o evento próprio "Investigação regulatória" (🟠 alto, 30 pts); "Intervenção regulatória" (🔴 85) ficou reservada a intervenção de fato (BC, liquidação extrajudicial, RAET).
- **Deduplicação** (`dedup`): três regras combinadas — similaridade de título ≥ 0.75, sobreposição de palavras ≥ 0.50, ou (para notícias de mercado sem emissor da watchlist) mesmo tipo de evento + mesmo protagonista citado + datas próximas. Foi isso que colapsou, por exemplo, 15 manchetes da mesma falência para 1. Fica a publicação mais antiga.
- **Consolidação LLM por emissor** (`llm.consolidate`, **ligada por padrão**): em vez de validar artigo por artigo, o Gemini (`gemini-2.5-flash`) recebe todas as manchetes novas de cada empresa numa única chamada e faz três coisas: (1) **dedup semântico** — agrupa manchetes que cobrem o mesmo fato mesmo com títulos totalmente reescritos (o caso "mesma operação em 2 veículos → todos os eventos ×2" que similaridade textual não pega); (2) confirma quais eventos de fato ocorreram (descarta especulação, negação, caso requentado, e inflação de eventos — emissão de debêntures não vira também follow-on por causa de "captação"); (3) verifica se a empresa é **protagonista** (assessoras, credoras e citadas de passagem são removidas). Requer o secret `GEMINI_API_KEY`; sem ele, segue só com keywords (fail-open) e o log avisa em destaque. Só artigos novos são analisados; a chamada em lote + `rpm_sleep_seconds` respeitam o limite de 10 req/min do free tier.

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

### 2. Rode o workflow pela primeira vez (com backfill)

Na primeira execução, marque a opção **backfill** no botão Run workflow: ela roda uma busca ampliada (notícias de 30 dias, fatos relevantes CVM do ano inteiro, todos os tiers) para semear o histórico — sem isso, o dashboard começa mostrando só o que saiu no dia, e as janelas de 30/90/365 dias vão enchendo aos poucos conforme as execuções acumulam. O backfill encurta essa espera para a parte que as fontes permitem recuperar retroativamente.

### 2b. Execuções normais

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
