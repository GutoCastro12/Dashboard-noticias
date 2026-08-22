# DIRECTIONAL HUMAN REVIEW — BATCH D1

Pacote de revisão humana. **Nenhuma previsão de modelo** e **nenhum rótulo pré-preenchido**: as células de rótulo saem vazias de propósito.

- unidade: **OCORRÊNCIA**
- rótulos: `ADVERSE` · `FAVORABLE` · `NEUTRAL` · `MIXED` · `UNCERTAIN`
- manifesto: `85f1bfb171b75321`
- linhas: **30** (Tier 1 adverso 12 · Tier 2 contextual 18)

A pergunta **não** é "esse tipo de evento poderia ser ruim?". É: **a evidência local desta ocorrência sustenta que ela é ruim?**

Responda em bloco, no chat: `D01 ADVERSE`, `D02 UNCERTAIN`, …

---

## D01 — Tok&Stok / Recuperação Judicial

**Em meio à recuperação judicial, empresa compra 13,2% da dona da Tok&Stok; dois fundos deixam grupo**  
Exame · 2026-06-18 · fase `UNKNOWN` · 3 artigo(s) na ocorrência  
Contribui **97.9** (`ADVERSE`) para Tok&Stok, que hoje soma **98** = 97.9 adverso + 0.0 contextual · status `critico`

> Tok&Stok: Justiça aceita recuperação judicial de R$ 1,1 bilhão que impacta 2,2 mil funcionários — NSC Total
> Recuperação judicial afeta quem está esperando móveis de Tok&Stok e Mobly? Veja — InfoMoney

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D01 — Tok&Stok / Recuperação Judicial
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D02 — B3 / Suspensão de negociação

**B3 suspende negociação de ações da Refit**  
Valor Econômico · 2026-08-19 · fase `anuncio` · 1 artigo(s) na ocorrência  
Contribui **78.2** (`ADVERSE`) para B3, que hoje soma **91** = 78.2 adverso + 12.5 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

⚠️ ATTRIBUTION_REVIEW_CANDIDATE — a suspensao e das acoes da Refit, terceiro; a B3 e a bolsa que suspende. Ver OCCURRENCE_ARCHITECTURE L578. Se a atribuicao lhe parecer errada, responda UNCERTAIN e diga isso na observacao.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D02 — B3 / Suspensão de negociação
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D03 — Cosan / Rebaixamento de rating

**Moody’s rebaixa rating da Cosan (CSAN3) em meio à deterioração da qualidade de crédito da Raízen (RAIZ4)**  
Money Times · 2026-08-10 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **58.7** (`ADVERSE`) para Cosan, que hoje soma **120** = 119.6 adverso + 0.0 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D03 — Cosan / Rebaixamento de rating
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D04 — Rumo / Rebaixamento de rating

**Moody’s rebaixa rating corporativo da Rumo de ‘Ba2’ para ‘Ba3’ e altera perspectiva para negativa**  
Valor Econômico · 2026-07-17 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **35.7** (`ADVERSE`) para Rumo, que hoje soma **61** = 44.8 adverso + 16.5 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D04 — Rumo / Rebaixamento de rating
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D05 — Cosan / Rebaixamento de rating

**Moody’s rebaixa rating da Cosan (CSAN3) para B1 e mantém perspectiva negativa**  
Money Times · 2026-07-16 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **32.5** (`ADVERSE`) para Cosan, que hoje soma **120** = 119.6 adverso + 0.0 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D05 — Cosan / Rebaixamento de rating
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D06 — Vale / Investigação regulatória

**Exclusivo: Após consulta de investidor, CVM abre processo sobre apoio da Previ a candidato ao conselho da Vale**  
Valor Econômico · 2026-07-20 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **16.7** (`ADVERSE`) para Vale, que hoje soma **41** = 38.0 adverso + 3.1 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D06 — Vale / Investigação regulatória
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D07 — Lojas Renner / Guidance negativo

**Lojas Renner reduz guidance e lucro fica estável em R$ 405 mi**  
EuQueroInvestir · 2026-08-06 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **14.8** (`ADVERSE`) para Lojas Renner, que hoje soma **27** = 27.1 adverso + 0.0 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D07 — Lojas Renner / Guidance negativo
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D08 — Vale / Investigação regulatória

**CVM abre processo para apurar destituição de conselheiro da Vale**  
Folha de S.Paulo · 2026-07-23 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **13.8** (`ADVERSE`) para Vale, que hoje soma **41** = 38.0 adverso + 3.1 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D08 — Vale / Investigação regulatória
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D09 — JBS / Recomendação rebaixada

**Stephens reduz preço-alvo da JBS após resultado misto**  
Investing.com Brasil - Finanças, Câmbio e Investimentos · 2026-08-17 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **12.3** (`ADVERSE`) para JBS, que hoje soma **100** = 12.3 adverso + 87.3 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D09 — JBS / Recomendação rebaixada
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D10 — Lojas Renner / Recomendação rebaixada

**Lojas Renner (LREN3): Citi corta preço-alvo após revisão do guidance; ações caem**  
Money Times · 2026-08-17 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **12.3** (`ADVERSE`) para Lojas Renner, que hoje soma **27** = 27.1 adverso + 0.0 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D10 — Lojas Renner / Recomendação rebaixada
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D11 — Rumo / Recomendação rebaixada

**Citi vê incerteza com troca de CEO da Rumo (RAIL3) e mantém recomendação de venda**  
Estadão · 2026-07-16 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **5.8** (`ADVERSE`) para Rumo, que hoje soma **61** = 44.8 adverso + 16.5 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D11 — Rumo / Recomendação rebaixada
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D12 — Bradesco / Investigação regulatória

**Fraude nas Americanas: executivos de Itaú, Bradesco e Santander são alvo de buscas**  
Brasil de Fato · 2026-06-25 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **4.7** (`ADVERSE`) para Bradesco, que hoje soma **8** = 4.7 adverso + 3.7 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

*(controle esperado: `ADVERSE` — não vincula seu julgamento)*

```
D12 — Bradesco / Investigação regulatória
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D13 — JBS / M&A

**JBS propõe aquisição dos 18% restantes da Pilgrim’s Pride**  
Investing.com Brasil - Finanças, Câmbio e Investimentos · 2026-08-18 · fase `UNKNOWN` · 2 artigo(s) na ocorrência  
Contribui **40.4** (`CONTEXTUAL`) para JBS, que hoje soma **100** = 12.3 adverso + 87.3 contextual · status `atencao`

> UBS reitera recomendação de compra para JBS após proposta de aquisição da PPC — Investing.com Brasil - Finanças, Câmbio e Investimentos

```
D13 — JBS / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D14 — Engie Brasil / Follow-on

**Follow-on: Engie Brasil realiza oferta subsequente de ações na B3**  
Bora Investir · 2026-06-11 · fase `UNKNOWN` · 9 artigo(s) na ocorrência  
Contribui **28.6** (`CONTEXTUAL`) para Engie Brasil, que hoje soma **69** = 0.0 adverso + 69.0 contextual · status `atencao`

> Engie faz follow-on de R$ 8,3 bilhões para comprar fatia em Jirau e desalavancar — Brazil Journal
> Engie detalha acordo sobre fatia na Jirau financiado por aumento de capital — InfoMoney

```
D14 — Engie Brasil / Follow-on
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D15 — JBS / Troca de CEO

**JBS nomeia Wesley Batista Filho como novo CEO global a partir de janeiro**  
Bloomberg Línea Brasil · 2026-08-10 · fase `UNKNOWN` · 2 artigo(s) na ocorrência  
Contribui **24.5** (`CONTEXTUAL`) para JBS, que hoje soma **100** = 12.3 adverso + 87.3 contextual · status `atencao`

> JBS busca recuperar margens nos EUA com gado mexicano, e novo CEO promete continuidade — Bloomberg Línea Brasil

```
D15 — JBS / Troca de CEO
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D16 — Sabesp / M&A

**Sabesp: assembleia para aquisição das ações da EMAE não será remarcada, apesar de ação cautelar**  
Estadão · 2026-07-27 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **20.0** (`CONTEXTUAL`) para Sabesp, que hoje soma **27** = 0.0 adverso + 26.7 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D16 — Sabesp / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D17 — PRIO / Follow-on

**FATO RELEVANTE - AUMENTO DE CAPITAL SOCIAL POR EXERCÍCIO DE OPÇÕES DE COMPRA DE AÇÕES**  
PRIO · RI · 2026-07-29 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **17.5** (`CONTEXTUAL`) para PRIO, que hoje soma **17** = 0.0 adverso + 17.5 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D17 — PRIO / Follow-on
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D18 — CPFL Energia / Troca de CEO

**De Estagiário A CEO: Bruno Monte Assume A Presidência Da CPFL Renováveis**  
Cenário Energia · 2026-08-20 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **14.5** (`CONTEXTUAL`) para CPFL Energia, que hoje soma **15** = 0.0 adverso + 14.5 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D18 — CPFL Energia / Troca de CEO
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D19 — Suzano / M&A

**Suzano (SUZB3) conclui aquisição de 51% da Arbex por R$ 6,7 bilhões**  
InfoMoney · 2026-07-01 · fase `encerramento` · 1 artigo(s) na ocorrência  
Contribui **13.4** (`CONTEXTUAL`) para Suzano, que hoje soma **13** = 0.0 adverso + 13.4 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D19 — Suzano / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D20 — Engie Brasil / Grande emissão de dívida

**[Fato Relevante] Engie Brasil: Emissão de Debêntures (17ª Emissão)**  
CVM · Fato Relevante · 2026-07-06 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **13.1** (`CONTEXTUAL`) para Engie Brasil, que hoje soma **69** = 0.0 adverso + 69.0 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D20 — Engie Brasil / Grande emissão de dívida
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D21 — B3 / Troca de CEO

**Após Chapter 11 e saída da B3, Gol anuncia novo CEO e faz mudanças na diretoria**  
Investidor10 · 2026-08-13 · fase `anuncio` · 1 artigo(s) na ocorrência  
Contribui **12.5** (`CONTEXTUAL`) para B3, que hoje soma **91** = 78.2 adverso + 12.5 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

⚠️ ATTRIBUTION_REVIEW_CANDIDATE — o CEO novo e da Gol, nao da B3. Ver OCCURRENCE_ARCHITECTURE L578.

```
D21 — B3 / Troca de CEO
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D22 — Natura &Co / M&A

**[Fato Relevante] Natura &Co: Aquisição de Participação Relevante pela Advent**  
CVM · Fato Relevante · 2026-07-02 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **12.3** (`CONTEXTUAL`) para Natura &Co, que hoje soma **18** = 0.0 adverso + 17.6 contextual · status `monitorar`

> Aquisição de Participação Relevante pela Advent

```
D22 — Natura &Co / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D23 — JBS / M&A

**JBS mira expansão global com listagem nos EUA e aquisição em Omã**  
Poder360 · 2026-07-23 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **12.2** (`CONTEXTUAL`) para JBS, que hoje soma **100** = 12.3 adverso + 87.3 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D23 — JBS / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D24 — Axia Energia / Grande emissão de dívida

**[Fato Relevante] Axia Energia: 6ª Emissão de Debêntures da AXIA Energia Sul**  
CVM · Fato Relevante · 2026-06-26 · fase `UNKNOWN` · 4 artigo(s) na ocorrência  
Contribui **11.2** (`CONTEXTUAL`) para Axia Energia, que hoje soma **21** = 0.0 adverso + 21.5 contextual · status `monitorar`

> 6ª Emissão de Debêntures da AXIA Energia Sul
> AXIA Energia Sul capta R$ 1,9 bilhão com debêntures e financiamento do BNDES — BPMoney
> Axia Energia (AXIA3) aprovou emissão de debêntures simples no valor de R$ 500 mi — InfoMoney

```
D24 — Axia Energia / Grande emissão de dívida
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D25 — JBS / Grande emissão de dívida

**JBS capta R$ 400 mi para operação de biodiesel; cadeia do agro é acompanhada pelo SNFZ11**  
suno.com.br · 2026-07-21 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **10.2** (`CONTEXTUAL`) para JBS, que hoje soma **100** = 12.3 adverso + 87.3 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D25 — JBS / Grande emissão de dívida
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D26 — BTG Pactual / Follow-on

**Brava (BRAV3) pode subir 10% com avanço de oferta de ações, diz BTG**  
Estadão · 2026-07-08 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **9.6** (`CONTEXTUAL`) para BTG Pactual, que hoje soma **38** = 0.0 adverso + 37.9 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D26 — BTG Pactual / Follow-on
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D27 — Rumo / Troca de CEO

**EXCLUSIVO: Rumo escolhe Rockenbach, “ferroviário raiz”, como novo CEO**  
Brazil Journal · 2026-06-22 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **7.6** (`CONTEXTUAL`) para Rumo, que hoje soma **61** = 44.8 adverso + 16.5 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D27 — Rumo / Troca de CEO
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D28 — Sabesp / M&A

**Sabesp conclui aquisição de participação na concessionária Castilho por R$ 30,7 milhões**  
Valor Econômico · 2026-05-31 · fase `encerramento` · 1 artigo(s) na ocorrência  
Contribui **6.8** (`CONTEXTUAL`) para Sabesp, que hoje soma **27** = 0.0 adverso + 26.7 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D28 — Sabesp / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D29 — Natura &Co / M&A

**Natura aprova incorporação de holding; ações começam a ser negociadas em julho**  
Estadão · 2026-05-31 · fase `aprovacao` · 1 artigo(s) na ocorrência  
Contribui **5.4** (`CONTEXTUAL`) para Natura &Co, que hoje soma **18** = 0.0 adverso + 17.6 contextual · status `monitorar`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D29 — Natura &Co / M&A
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```

## D30 — Vale / Troca de CEO

**Novo CEO da Vale (VALE3): o que Embraer e Klabin dizem sobre participação de diretores na concorrência**  
Estadão · 2026-05-28 · fase `UNKNOWN` · 1 artigo(s) na ocorrência  
Contribui **3.1** (`CONTEXTUAL`) para Vale, que hoje soma **41** = 38.0 adverso + 3.1 contextual · status `atencao`

> `LOCAL_EVIDENCE_INSUFFICIENT` — nada além do título acima está armazenado localmente: um único artigo, sem corpo. É também tudo o que o contrato do modelo receberia.

```
D30 — Vale / Troca de CEO
Label: [ADVERSE | FAVORABLE | NEUTRAL | MIXED | UNCERTAIN]
Observação opcional:
```
