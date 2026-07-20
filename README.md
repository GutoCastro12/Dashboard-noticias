[README.md](https://github.com/user-attachments/files/30173816/README.md)
# Radar de Risco — Monitor de Notícias de Emissores

## Resolução de links do Google News (correção, jul/2026)

**Problema:** algumas notícias abriam apenas a home do veículo (ex.: NSC Total, Migalhas, Valor) em vez do artigo específico — o fallback ficava "preso".

**Causa raiz (dupla):**
1. O `_gnews_batchexecute` usava um payload desatualizado do endpoint interno do Google, então a resolução falhava e caía no fallback.
2. Quando caía no fallback, a **home do veículo era gravada permanentemente** em `rec["url"]`, e o token original do Google News se perdia — impossibilitando o retry nas execuções seguintes.

**Correções:**
- Reescrito `_gnews_batchexecute` com o método atual (googlenewsdecoder): busca os parâmetros de decodificação (`data-n-a-sg` + `data-n-a-ts`) tentando `/articles/` antes de `/rss/articles/`, e monta o payload no formato aceito hoje (`rpcids=Fbv4je`, header Referer).
- O fallback (home) **não é mais gravado** como `url` do registro. Ele vai para um campo separado `display_url`, usado apenas na exibição; o token original permanece em `url` para ser retentado. Só quando a resolução acerta o artigo exato é que `url` é sobrescrito.
- Novo helper `link_for_display(rec)` (Python) e `linkOf(o)` (template JS): exibem o `display_url` quando o `url` ainda é do Google News, senão o artigo real.

**Ressalva:** essa correção **não é testável no sandbox** (a rede aqui não alcança `news.google.com`). A validação real acontece na primeira execução em produção: os links pendentes são resolvidos em lotes de até 40 por rodada, com retry nas execuções seguintes. Se o Google impuser rate-limit (HTTP 429), os que faltarem são retentados na próxima das 4 rodadas diárias.


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

- **Score decomposto e auditável**: na aba Radar de emissores, clique em qualquer emissor para abrir a decomposição — evento a evento: data, fonte, dimensões de risco, peso-base × fator de decaimento × confiança da fonte = contribuição, somando exatamente o score exibido. Nada de número mágico.
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

O dashboard abre com **três abas** — 🔔 O que mudou, 📊 Radar de emissores e 📰 Notícias classificadas — e um **seletor de janela global** (7 dias, 30 dias, 90 dias ou 1 ano) que recalcula as visões instantaneamente (tudo pré-computado no build; a troca é client-side, sem recarregar). O estado fica na URL (`#radar/90`), então dá para compartilhar um link já aberto na visão e janela desejadas. A aba Radar de emissores é a visão central por emissor: reúne ranking, score, classificação, evento mais grave, trajetória, decomposição e fontes, com um filtro por grupo de ativos (Todos, Empresas listadas, Não listadas (PE/Crédito), FIIs).

Observação: janelas longas só ficam completas conforme o histórico acumula — o `risk_history.json` começa vazio e retém 400 dias.

## As três visões do dashboard

**1. Radar semanal** — ranking por score acumulado em 7 dias, com selo ATENÇÃO IMEDIATA acima do threshold (80 pts).

**2. Radar de emissores (90 dias)** — o risco raramente surge de um evento isolado; deteriorações acontecem em sequência (outlook negativo → downgrade → renegociação → RJ). Cada emissor ganha uma linha do tempo com um ponto por sinal (colorido pela severidade, clicável, com opacidade proporcional à recência), uma **sparkline com a trajetória do score** (reconstruída dia a dia do histórico — mostra a inclinação da deterioração) e um status:

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

## Consolidação das abas: Radar de emissores agora é a visão completa (jul/2026)

O dashboard passou de quatro para **três abas**. A antiga aba "Evolução por emissor" (a visão completa: ranking, score ponderado, classificação, evento mais grave, trajetória, decomposição do score e fontes) **foi incorporada** à aba **"Radar de emissores"**, que antes mostrava apenas um ranking simplificado. O ranking simplificado e seu código (`aggregate_scores`, layout `radar-row`/`bar-fill`/`attn-tag`) foram **removidos** — não havia motivo para manter duas telas quando uma já continha um superconjunto da outra.

A navegação final é: **🔔 O que mudou · 📊 Radar de emissores · 📰 Notícias classificadas**. Links antigos com `#evolucao` são redirecionados automaticamente para `#radar` (retrocompatibilidade).

**Filtro por grupo de ativos.** A nova aba Radar de emissores ganhou um filtro em chips — **Todos · Empresas listadas · Não listadas (PE/Crédito) · FIIs** — que combina com a busca textual e com a janela selecionada, mantendo a ordenação por score dentro do grupo. A fonte do grupo é o campo `type` da watchlist (`empresa`/`nao_listada`/`fii`), normalizado no pipeline para `asset_group` (`listed_companies`/`nao_listada`/`fii`) e enviado no payload — sem classificação hardcoded no frontend. Emissores sem tipo reconhecido caem em "Não classificado" internamente, nunca aparecendo como `undefined`/`null`/`NaN`. (A watchlist não distingue Private Equity de Crédito Privado — ambos são `nao_listada` —, por isso o filtro os agrupa num único chip.)

## Radar alinhado à Evolução — nº de notícias não afeta o score (v1.0, jul/2026)

Corrigida uma contradição real: o Radar (`aggregate_scores`) usava um bônus de intensidade de `+5` por notícia extra (cap `+20`), o que fazia o número de matérias afetar o score e, portanto, a ordenação — contradizendo a regra de que republicações não inflam. O Radar agora usa **exatamente a mesma fórmula da Evolução**: peso-base × confiança da fonte de maior peso + bônus de corroboração limitado `[4,2,1]` por fonte independente (domínio distinto, incluindo `corrob_sources`). Removidos do config os parâmetros obsoletos `count_repeated_events`, `extra_per_article` e `extra_cap`. Testado: 3 republicações do mesmo domínio → score idêntico a 1 fonte; 3 fontes independentes → +6 de bônus. A afirmação "o número de notícias não influencia a ordenação" passa a ser verdadeira em todo o sistema.

## Cobertura aberta de fontes (v1.0, jul/2026)

Removidos `dashboard.sources` (allowlist de 12 domínios) e `restrict_to_sources` do `config_risco.yaml`. Essa allowlist estava inerte em produção (`restrict_to_sources: false`) e foi confirmado, por busca no código, que as chaves não eram usadas em nenhuma outra etapa do pipeline (coleta, classificação, scoring, template ou workflow) — apenas no filtro opcional de coleta, agora removido. A decisão (da área de Riscos) é adotar **cobertura aberta**: toda fonte encontrada é elegível desde que a notícia satisfaça os critérios de inclusão (emissor + evento), e a confiabilidade é governada exclusivamente por `source_trust` (tiers + overrides + peso padrão 0,60 para fontes não cadastradas). Isso reduz o risco de falsos negativos por omissão de veículos relevantes fora de uma lista fechada.

## Explicabilidade do score (jul/2026)

Melhorias focadas em tornar o dashboard auditável — responder "por que este emissor vem antes daquele?" e "quais fatos e fontes produziram esse score?":

- **Dois selos separados** em cada emissor: RISCO DO EMISSOR (nível geral: Crítico / Atenção elevada / Monitorar, considerando todos os eventos, recência e decaimento) e EVENTO MAIS GRAVE (severidade do evento isolado mais grave). Resolve a aparência de contradição de um ativo com evento crítico mas classificação geral de atenção elevada.
- **Ordenação explícita e documentada**: score total (desc) → evento mais grave → crítico mais recente → mais eventos críticos distintos. Não ordena por número de notícias (republicações do mesmo fato não influenciam a posição).
- **Bônus limitado de corroboração**: republicações do mesmo evento não multiplicam o score — a 1ª notícia dá o peso principal e cada fonte independente adicional dá um bônus pequeno e decrescente (`corroboration_bonus: [4, 2, 1]`, capado). Três veículos noticiando a mesma RJ = 1 evento confirmado com bônus de credibilidade, não 3 eventos.
- **Lista de fontes ao abrir o evento**: clicar no nome de um evento na decomposição lista todas as fontes (veículo, horário, badge "principal", link direto "Abrir notícia →"), deixando claro quem informou o quê.
- **Composição do score visível**: a decomposição ganhou as colunas Peso base, × Decaimento, × Confiança e Bônus fontes, somando a contribuição — sem expor fórmula proprietária, mas explicando cada ponto.
- **Contadores separados no topo**: além de "N eventos classificados", mostra para a severidade crítica "N notícias críticas · N eventos únicos · N emissores afetados" (o número de eventos únicos é mais informativo que o de matérias brutas).
- **Marcadores de evento no gráfico**: cada ponto da trajetória mostra data, evento e severidade ao passar o mouse, explicando por que a curva subiu.
- **Persistência de corroborações entre execuções**: quando três veículos noticiam o mesmo evento em execuções diferentes (um por dia, p.ex.), o dedup entre-execuções remove as duplicatas mas agora **grava as fontes no registro sobrevivente** (`corrob_sources`, com horário) — a evolução, que lê do histórico, lista todas as fontes mesmo dias depois. Antes, só a primeira sobrevivia e a contagem caía para "1 fonte". O dedup também reconhece a mesma história entre veículos de imprensa com títulos diferentes ("pede RJ" / "Justiça aceita RJ") quando é o mesmo emissor + mesmo evento grave + janela curta.
- **Reclassify preserva corroborações**: o modo `--reclassify` zera e reconstrói o histórico a partir dos registros existentes — e agora carrega junto os campos `corrob_sources`/`corroborations`, senão a contagem multi-fonte era apagada a cada reclassify (as duplicatas originais já não estão no histórico para serem recontadas). Era a causa de eventos com várias fontes voltarem a mostrar "1 fonte" após um reclassify.
- **Correção de links antigos do histórico**: a resolução de URLs do Google News agora reprocessa também os registros JÁ no histórico (gravados por execuções anteriores com o link-redirecionador), até 40 por execução — o passivo de links quebrados é limpo em poucas passadas.
- **Links diretos para o veículo**: os redirects do Google News (`news.google.com/rss/articles/…`, que davam "Aviso de redirecionamento" para todos exceto quem clicou originalmente) são resolvidos para o link direto do veículo por três métodos em cascata — decodificação inline do token, o endpoint interno `batchexecute` do Google (o mais confiável para os tokens atuais) e, por fim, seguir o redirect HTTP. Links que caem no fallback (home do veículo, quando o Google rate-limita) são marcados e **retentados nas execuções seguintes** até virarem o artigo exato, então um bloqueio temporário não deixa o link imperfeito para sempre. Resultado cacheado no histórico.

## Refinamentos de interface (jul/2026)

Melhorias de usabilidade e acessibilidade preservando o tema escuro e a estrutura de abas: contadores do topo corrigidos (fallback seguro — nunca exibem `undefined`/`NaN`) com legenda "N eventos classificados · janela de X" e tooltip de critério por severidade; indicador de frescor "● Atualizado há X min" (verde < 2h, amarelo 2–12h, vermelho > 12h, recalculado a cada minuto a partir do timestamp de geração); busca global no cabeçalho que filtra emissor/ticker/evento/título simultaneamente em todas as abas e combina com os filtros de janela e severidade; variação de score por emissor (`▲ +N` em vermelho para deterioração, `▼` em verde para melhora) com destaque nas linhas que pioraram; contexto por emissor no Radar (evento principal responsável + horário da última notícia); ícone de informação explicando o cálculo do score; estados de interface (skeleton inicial, vazio com `role=status`, erro com botão "Tentar novamente"); e acessibilidade (abas como `<button>` navegáveis por seta/teclado com `aria-selected`, `aria-label` nos contadores e busca, severidade sempre com rótulo textual além da cor, `prefers-reduced-motion` respeitado, responsivo em desktop e mobile).

## Dedup determinístico do mesmo evento (independe do LLM)

Além da consolidação via LLM, a visão de Evolução colapsa deterministicamente as ocorrências do **mesmo tipo de evento, para o mesmo emissor, dentro de `evolution.same_event_window_days` (10 dias)**: viram um único sinal, representado pela fonte de maior confiança, com as demais registradas como corroboração. Isso garante que, mesmo quando a análise LLM não roda (cota do free tier esgotada, p.ex.), três notícias sobre a mesma recuperação judicial não virem "RJ ×3" inflando a contagem — viram um sinal com "◆3 fontes", o que **dá credibilidade sem somar pontos** (o score de um tipo de evento já usava só a ocorrência de maior contribuição). O chip mostra o número de fontes corroborantes, e a decomposição ganhou uma coluna "Fontes" com o detalhe no tooltip.

## Nota de correção (73 emissores, quota do Gemini)

Um bug real apareceu na primeira execução com a watchlist expandida: uma função de classificação LLM legada (de uma versão anterior à consolidação em lote, per-artigo e sem controle de rate limit) tinha ficado no código e era chamada **junto** com a consolidação nova a cada execução — dobrando o volume de chamadas à API e usando um modelo antigo hardcoded (`gemini-2.5-flash-lite`, hoje descontinuado para contas novas). Isso, somado ao `llm.model` do config ter regredido para essa mesma versão antiga, estourou a cota diária do free tier (20 req/dia) rapidinho, e como o erro de cota diária era tratado como transitório, o pipeline ficava retentando por minutos a cada empresa — daí o log de 35 mil linhas. Corrigido: função legada removida, e a cota diária esgotada agora aborta a análise LLM do restante da execução imediatamente (fail-open) em vez de retentar. **Atualização (jul/2026)**: desde 1º de abril de 2026 o free tier da API do Gemini só inclui os modelos Flash/Flash-Lite da geração 3 — a família 2.5 inteira virou paga, e contas novas recebem 404 nela. O modelo padrão agora é `gemini-3-flash` (10 req/min, 1.500 req/dia grátis), com `llm.model_fallbacks` tentados em ordem quando o principal retorna 404 (o Google reorganiza o free tier periodicamente); se nenhum modelo da lista existir, a análise LLM aborta com uma única mensagem clara em vez de falhar empresa por empresa. Também: falha 403/anti-bot no scraper de RI agora tenta o navegador headless antes de desistir (resolve o caso do Itaú e da PRIO na run anterior).

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
