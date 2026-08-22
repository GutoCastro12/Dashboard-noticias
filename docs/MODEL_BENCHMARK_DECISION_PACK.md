# MODEL / PROMPT + DIRECTIONAL HUMAN BENCHMARK — pack de decisão

`reliability_model_benchmark.py` · `model.benchmark.v2` · manifesto `0ab22591f1799e44`
Autoridade: **NENHUMA** sobre score, ocorrência, semântica ou escrita. Rótulo de saída:
`EVALUATION ONLY`.

## A pergunta desta onda

> Um LLM pode modular com segurança a DIREÇÃO de uma ocorrência contextual já
> confirmada, sem se tornar autoridade sobre a EXISTÊNCIA da ocorrência?

A resposta honesta desta execução é: **ainda não se sabe, porque a medição não foi
possível.** Isso não é o mesmo que "o modelo foi mal".

## Por que a Camada B não rodou

Dois bloqueios independentes, ambos de instrumentação:

1. **O contrato `v2` não emite campo de direção.** As 48 observações congeladas
   trazem `event_asserted`, `company_role`, `subject`, `occurrence_novelty`, `phase`,
   `currentness`, `centrality`, `transaction_object`, `relation`, `related_entity` —
   e nenhum campo direcional. Não há o que pontuar.
2. **Não há credencial de modelo neste ambiente.** Nenhuma variável de API e nenhuma
   chave em `config_risco.yaml`. **Zero chamadas de modelo nesta execução.**

Logo: `MODEL_DIRECTION_SCORE_AUTHORITY = NOT_READY`, por **falta de medição**.

O manifesto direcional foi construído mesmo assim, para que a próxima onda tenha o
alvo pronto: 63 ocorrências (TIER1 = 14 adversas de referência, TIER2 = 49
contextuais), unidade = OCORRÊNCIA, vocabulário fechado
`ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN`, **sem multiplicador numérico
proposto pelo modelo**.

## Higiene de holdout

| conjunto | papel | tamanho |
|---|---|---|
| Batch V1 | `DEVELOPMENT_SET` — desenhou as guardas determinísticas | 24 casos / 27 memberships (CLEAR 25, UNDETERMINED 1, POLICY_PENDING 1) |
| Contrato V2 | `PROSPECTIVE_VALIDATION_SET` | 48 observações / 24 artigos; 22 revisadas / 11 artigos; **13 artigos ainda não revisados** |

**Interseção V1 ∩ V2 = 0 artigos.** Reportar os dois juntos apagaria a distinção entre
o conjunto que desenhou as regras e o que as testa.

## Camada A — acurácia por dimensão (22 observações revisadas)

`DETERMINISTICO` é coluna, não modelo: seu veredito é idêntico nas duas observações do
mesmo artigo e por isso é contado **uma vez por artigo**, nunca por observação.

| dimensão | determinístico | G1 `gemini-3.1-flash-lite` | G2 `gemini-3.5-flash-lite` |
|---|---|---|---|
| subject | — | **11/11** | 9/11 |
| currentness | — | **8/8** | **8/8** |
| event_asserted | 9/11 | **10/11** | 9/11 |
| company_role | 5/10 | **10/11** | 9/11 |
| occurrence_novelty | — | **9/11** | 8/11 |
| centrality | — | **6/6** | 5/6 |
| transaction_object | — | **5/6** | 4/6 |
| phase | 0/4 | **4/6** | **4/6** |
| relation | — | **2/2** | 0/2 |
| related_entity | — | **2/2** | **2/2** |
| `scoreable` (derivado) | 2/5 | **3/5** | **3/5** |

G1 vence ou empata em **todas** as dez dimensões. O segundo modelo não paga o próprio
custo: as duas discordâncias G1×G2 se cancelam (G1 ganha em `troca_ceo`, G2 em
`recomendacao_negativa`, ambas na JBS).

## O papel mais seguro medido: gatilho de revisão, não decisor

O modelo não decide; ele levanta a mão quando discorda do determinístico.

| modelo | discordâncias | erros REAIS do determinístico achados | falsos alarmes | precisão |
|---|---|---|---|---|
| G1 | 3 | JBS/troca_ceo, Tok&Stok/recuperacao_judicial | JBS/recomendacao_negativa | **0,667** |
| G2 | 1 | Tok&Stok/recuperacao_judicial | — | **1,000** |

G1 resgata 2 e regride 1. G2 resgata 1 e não regride. Em ambos, o volume é baixo — o
que é bom: um gatilho que dispara sempre não é gatilho.

## Onde os dois modelos falham JUNTOS

**Banco do Brasil** — "CVM abre processo contra **presidente** do Banco do Brasil".
Humano: `company_role = MENTIONED`, não pontuável — processo contra dirigente não é
evento da companhia. G1 e G2 dizem `SUBJECT` e pontuável. O determinístico também
erra. É o único ponto do conjunto revisado em que **nada** acerta.

## Gaps de prompt — nenhum promovido

§36 exige padrão causal **repetido**. Um exemplo é anedota.

| gap | n | estado |
|---|---|---|
| `MODEL_PROMPT_PERSON_COMPANY_GAP` | 1 (Banco do Brasil) | `SINGLE_EXAMPLE_NOT_ENOUGH` |
| `MODEL_PROMPT_CEO_ASSERTION_GAP` | 1 (JBS, só G2 erra) | `SINGLE_EXAMPLE_NOT_ENOUGH` |
| `MODEL_PROMPT_MATERIALITY_DIRECTION_GAP` | 0 | `NOT_MEASURABLE` |

**Portanto Prompt V3 NÃO está justificado nesta onda.** Ajustar o prompt agora seria
ajustar a um único caso — e os 13 artigos não revisados existem justamente para que
essa tentação seja resistida.

## Aterramento em citação

| modelo | GROUNDED | PARTIALLY_GROUNDED | suporte de campo parcial/insuficiente |
|---|---|---|---|
| G1 | 20 | 4 | 1 |
| G2 | 22 | 2 | 3 |

## Telemetria (observações CONGELADAS — zero chamadas nesta execução)

| modelo | chamadas | falhas | tokens in/out | latência méd/med/p95 | custo |
|---|---|---|---|---|---|
| G1 | 24 | 0 | 31.740 / 7.126 | 3,45 / 3,14 / 6,19 s | indisponível |
| G2 | 24 | 0 | 31.740 / 7.150 | 1,60 / 1,57 / 1,94 s | indisponível |

O custo **não foi estimado**: a observação congelada não registra preço. G2 é ~2× mais
rápido e mede pior — velocidade não compra acurácia aqui.

## Decisão de arquitetura recomendada

**Nenhuma autoridade de score para o modelo.** O único papel sustentado pela evidência
desta onda é **gatilho de revisão humana** sobre `scoreable`, com G1 sozinho.
`multiplicador_direcional()` permanece puro, retornando 1.0, sem chamada de modelo.

## Próxima onda

Opção **B — DIRECTIONAL HUMAN LABELING**: rotular direção humana sobre as 63
ocorrências do manifesto (ou ao menos o TIER1 de 14) **antes** de qualquer chamada de
modelo. Sem verdade direcional humana, uma re-execução com credencial mediria a
concordância do modelo contra nada.
