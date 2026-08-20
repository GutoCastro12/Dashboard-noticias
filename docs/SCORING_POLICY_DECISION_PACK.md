# Pacote de decisão — política de score

> Onda de MEDIÇÃO. Nada foi promovido, nenhum peso foi alterado, nenhum limiar
> foi tocado. Medições de `reliability_scoring_policy_shadow.py` sobre 836
> artigos em `948c28c`. Autoridade de produção: **nenhuma**.
>
> **A escolha do número é sua.** Este documento existe para que ela seja
> tomada por evidência.

## O achado, em uma linha

**Dois terços do Score de Risco do sistema vêm de famílias que o próprio
`config_risco.yaml` declara `direction: neutra`** — e removê-las **não muda o
status de nenhum emissor**.

## O controle é confiável

| | |
|---|---|
| status reproduzido | **63/63** |
| score reproduzido | **60/63** |
| resíduo nos outros 3 | exatamente **−0,5** |

A causa é nomeada: `breakdown` arredonda `contrib` a 0,1, e somar partes
arredondadas difere de arredondar a soma. Como **todas** as políticas usam o
mesmo método, o resíduo se cancela nas comparações.

## De onde vêm os pontos

| direção | ocorrências | pontos | % do sistema |
|---|---:|---:|---:|
| `ADVERSE` | 14 | 324,5 | **33,0%** |
| `CONTEXT_DEPENDENT` | 50 | 659,6 | **67,0%** |
| `FAVORABLE` | 0 | 0 | 0% |

**33 de 63 emissores pontuam 100% por evento de direção indeterminada.**
Trinta e cinco passam de 75%.

### Por família

| família | direção | peso | ocorr. | emissores | pontos vivos | % sistema | zerá-la muda status de |
|---|---|---:|---:|---:|---:|---:|---|
| `ma` | **contextual** | 40 | 24 | 23 | **402,0** | **40,8%** | ninguém |
| `rebaixamento_rating` | adversa | 80 | 2 | 2 | 98,8 | 10,0% | Cosan |
| `emissao_divida` | **contextual** | 35 | 9 | 8 | 89,3 | 9,1% | ninguém |
| `follow_on` | **contextual** | 30 | 6 | 6 | 88,4 | 9,0% | ninguém |
| `suspensao_negociacao` | adversa | 85 | 1 | 1 | 80,5 | 8,2% | ninguém |
| `troca_ceo` | **contextual** | 25 | 9 | 9 | 73,2 | 7,4% | ninguém |
| `recuperacao_judicial` | adversa | 100 | 1 | 1 | 41,6 | 4,2% | ninguém |
| `investigacao_regulatoria` | adversa | 30 | 4 | 4 | 32,4 | 3,3% | ninguém |
| `recomendacao_negativa` | adversa | 15 | 3 | 3 | 28,6 | 2,9% | ninguém |

**A maior fonte de pontos do sistema inteiro é uma família que a config chama
de neutra.** E as **únicas** famílias cujo zeramento move um status são
adversas.

## As quatro opções

| | P0 · atual | P1 · portão de direção | PM · peso parcial | P2 · cap por família |
|---|---|---|---|---|
| **significado** | evento material = evento de risco | contextual continua visível, soma zero sem evidência adversa | contextual soma uma fração | ocorrências distintas, contribuição limitada a uma por família |
| **total do sistema** | **984,1** | **324,5** | 489,2 (0,25) · 654,0 (0,50) | 966,6 |
| **mediana** | 9,0 | 0,0 | 3,0 · 5,3 | 9,0 |
| **p90** | 33,6 | 16,7 | 21,9 · 26,8 | 33,6 |
| **críticos** | 1 | **1** | 1 · 1 | 1 |
| **atenção** | 12 | **12** | 12 · 12 | 12 |
| **monitorar** | 50 | **50** | 50 · 50 | 50 |
| **muda status de** | — | **ninguém** | ninguém | ninguém |
| **vantagem** | nada a fazer | o score volta a significar "risco", não "atividade" | ajuste fino sem zerar | ataca multiplicidade |
| **modo de falha** | um emissor com três aquisições parece deteriorando | perde-se sinal se uma aquisição *for* adversa e nada a qualificar | o número é arbitrário | **não resolve o problema** |

### Maiores movimentos de ranking sob P1

| emissor | P0 | P1 | Δ rank |
|---|---:|---:|---:|
| Smart Fit | 30,7 | 0,0 | −44 |
| Orizon | 33,4 | 0,0 | −40 |
| Sabesp | 27,5 | 0,0 | −39 |
| Engie Brasil | 56,5 | 0,0 | −30 |
| JBS | 89,8 | 12,6 | −6 |
| B3 | 93,3 | 80,5 | 0 |
| Cosan · Tok&Stok · Lojas Renner · Yobel | — | **inalterados** | +1 a +9 |

Os cinco emissores cujo score já era 100% adverso **não perdem um ponto**.

## O que a medição desmentiu

### 1 · O cap por família não resolve nada

A hipótese era que a inflação viesse de **multiplicidade** de ocorrências. Não
vem: o cap remove **1,8%** do sistema (984,1 → 966,6) e não muda status de
ninguém. O portão de direção remove **67%**. **A inflação é o peso de existir**,
não a contagem.

### 2 · O peso de score quase não decide status

Apenas **3 de 63** emissores alcançam o limiar de `atenção` (60) **pelo score**.
Os outros nove chegam lá por `n_negative_types >= 2` ou `persistent`. O único
`crítico` (Tok&Stok) vem de `hard_critical` — RJ, peso 100 ≥ 90.

Por isso P1 remove dois terços do score e **muda zero status**. Três emissores
(Engie, BTG, Cemig) são `atenção` com **100%** de score contextual, e
continuariam `atenção` sob P1.

**O verdadeiro gatilho é `n_negative_types >= 2`.** Quando ele também passa a
contar só famílias com autoridade de score (variante P1b), **seis** emissores
caem para `monitorar`: Santander, BTG, Vale, Yura, Cemig, Bradesco. Nenhum
sobe.

São, portanto, **duas decisões separadas** — e é honesto dizê-lo:

1. um evento contextual deve somar pontos?
2. um evento contextual deve contar como "tipo negativo" para o status?

### 3 · A política de renovação em aberto perde a consequência de score

As **5** renovações materiais medidas são **todas** de família contextual
(`ma`, `follow_on`). **Nenhuma** é adversa. Sob P1, as perguntas em aberto de
renovação para `ma`, `follow_on` e `emissao_divida` deixam de afetar o Score de
Risco — sobram linha do tempo e destaque de exibição, que são decisões de
painel, não de risco.

Isso simplifica três das quatro políticas pendentes sem decidir nenhuma.

## Controles que se mantêm

- **Adverso intacto**: nos 7 emissores com evento adverso *e* contextual, o
  portão preserva o adverso ao ponto (B3 80,5 · Rumo 40,1 · Vale 17,7 …).
  Zerar M&A não apaga um rebaixamento.
- **Crítico preservado**: Tok&Stok segue `crítico`, 100% adverso.
- **Nenhum score negativo**: favorável = zero ponto, nunca compensação. Uma
  aquisição benigna não abate um default.
- **Rating**: 3 ocorrências distintas na Cosan, **zero** renovações — não há
  inflação por artigo posterior da mesma ação.
- **Identidade não depende de score**: com peso de `ma` zero, dobrado ou normal,
  as **mesmas** ocorrências, ids e membros. A JBS mantém 2 M&A, 1 CEO e 1
  dívida com risco zero, todos com `article_id`.

## JBS, o caso que explica

| política | score | status |
|---|---:|---|
| P0 atual | 89,8 | atenção |
| PM 0,25 | 31,9 | atenção |
| PM 0,50 | 51,2 | atenção |
| **P1** | **12,6** | **atenção** |

Decomposição: M&A Pilgrim's 41,5 · CEO 25,2 · **recomendação rebaixada 12,6** ·
dívida 10,5. **Só a recomendação é adversa** — 14% do total. E a JBS permanece
`atenção` em qualquer política, porque `persistent` a segura.

Índice de materialidade sob risco zero: **4 eventos materiais, 4 famílias,
115 de peso-base**. Continua tudo visível.

## Recomendação arquitetural

**Separar materialidade, direção e autoridade de score.** É a única leitura
consistente com o que a config já declara: um campo `direction` existe, é
preenchido, e hoje não é consultado na hora de pontuar.

**Não recomendo um número.** A série 0 → 0,25 → 0,50 → 1,00 é **linear e sem
ponto de inflexão** — nenhum valor intermediário se justifica pela medição, só
por julgamento de risco. Esse julgamento é seu.

### Sobreposição direcional — desenho, não implementação

O caminho natural depois: um evento material ganha autoridade de score quando
**um qualificador adverso explícito** o acompanha (M&A + evidência de estresse
de alavancagem; emissão + custo fora de mercado). Isso preserva o portão e
recupera o sinal que ele perde. Ponto de entrada: o mesmo fator em
`base_contrib`, alimentado por um sinal semântico separado. **Não implementado.**

## Acoplamento e promoção

Localizados no código, não supostos:

| o quê | `risk_dashboard.py` |
|---|---:|
| `best_contribs` | L5513 |
| fórmula da contribuição | L5526 |
| chave de ocorrência | L5531 |
| `weighted_total` | L5539 |
| `n_negative_types` | L5572 |
| regra de status | L5654 |

Um portão de direção entra em **um** ponto: o fator aplicado a `base_contrib`.
A contagem de tipos do status é um **segundo** ponto, e é decisão separada.

Como o total é a soma de uma contribuição por `_occ_key`, promover a estrutura
de ocorrência **muda** o score. Por isso a promoção tem de ser **conjunta**:
ocorrência + política de score na mesma onda, com o número escolhido por você.

### Onda combinada, quando houver decisão

1. portão de direção em `best_contribs`, com o fator vindo de `direction`;
2. decidir separadamente `n_negative_types`;
3. promover a estrutura de ocorrência da Sombra V3 (identidade, fases, âncora,
   representante, proveniência);
4. rodar corpus inteiro comparando **antes/depois** por emissor, com transição
   de status enumerada;
5. só então avaliar recalibração de limiar — **depois** da semântica, nunca
   antes.

Portões: concordância humana de ocorrência intacta · proveniência 100% ·
ids estáveis · corroboração preservada · nenhuma transição de status
inexplicada · congelados intactos · bateria completa.
