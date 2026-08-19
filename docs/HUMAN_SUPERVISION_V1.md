# Supervisão humana — Lote V1

> Fonte de dados: `risk_human_supervision.json` (`human.supervision.v1`).
> **Autoridade de score: NENHUMA.** Nenhum caminho de produção lê este arquivo.
> Escritor/validador: `reliability_human_supervision.py`. Testes: `test_wave_r7j_supervisao_humana.py`.

24 casos de revisão · 27 filiações (`article_id|company|family`) · 23 empresas ·
10 famílias · **22 casos claros, 1 indeterminado (Capital One), 1 com política
pendente (Copel)**.

O lote V1 **não** vive em `risk_semantic_v2_shadow.json`. Aquele artefato é
indexado por saída de modelo, e nenhum destes 24 casos tem saída de modelo —
foram escolhidos justamente por isso. Além disso, as dimensões do Contract V2
não expressam renovação de score, etapa processual, família errada nem
insuficiência de evidência. Os dois artefatos convivem; as 11 revisões do V2
não foram tocadas.

## As doze invariantes que o lote produziu

**H1 · Artigo atual ≠ ocorrência atual.**
A Engie (caso 11) traz "conclui follow-on" no título, mas o corpo situa a
conclusão em 17 de julho. Verbo forte no título não basta quando o corpo coloca
a ocorrência em data anterior.

**H2 · Mesma ocorrência ≠ proibição de renovar.**
Sabesp (03) e Smart Fit (05) são ambos "mesma ocorrência", e só um renova. A
distinção não é entre ocorrências, é entre **papéis dentro da ocorrência**.

**H3 · Etapa processual ≠ fechamento material.**
Assembleia mantida, pedido à CVM, tentativa de liminar (Sabesp) continuam o
processo. Fechamento (Smart Fit, Suzano) é realização material — não é uma
segunda aquisição, mas **pode** renovar.

**H4 · Descritor ≠ asserção de evento.**
"Novo CEO da B3 diz…" identifica quem fala. Cinco negativos (B3, Tupy, Pemex,
Santander, Rumo) contra um positivo (Vale) mostram que a fronteira é a
afirmação da mudança, não a expressão "novo CEO".

**H5 · Múltiplas fontes ≠ múltiplas ocorrências.**
Várias matérias sobre o rebaixamento da Cosan são corroboração da mesma
ocorrência. Pontua uma vez.

**H6 · A monitorada pode pontuar como alvo/investida.**
Natura (14): quem adquire é a Advent. Comprador não é o único papel pontuável.

**H7 · Não existe regra global única para pessoa × empresa.**
Três desfechos com evidência humana: Banco do Brasil **não** pontua (pessoa é o
alvo formal, banco é vínculo); os três bancos do caso Americanas **pontuam**
(executivos em exercício em investigação corporativa materialmente relevante);
a Vale **pontua** (o objeto apurado é ato de governança da própria companhia).
A razão do caso 09 é **relevância institucional**, não magnitude de score.

**H8 · Ações novas ≠ follow-on automático.**
PRIO (04) é aumento de capital por exercício de opções; Aegea (24) é aumento
com subscrição por acionistas. Nenhum é oferta pública subsequente, e o da
Aegea pode ser **credit-positive**.

**H9 · Reafirmação negativa pode ser sinal novo.**
"Mantém recomendação de venda" (Rumo, 18) é avaliação corrente de analista.
Rebaixamento não é requisito.

**H10 · Título insuficiente pode exigir o corpo.**
Yobel (15): parte dos títulos só diz "fábrica de cosméticos em Los Olivos"; o
corpo confirma que é da Yobel. Texto local mais rico melhora a atribuição.

**H11 · Evento relevante pode não caber na taxonomia.**
A suspensão da oferta pela CVM (Bradesco, 19) é relevante e não é
`emissao_cotas`. Registrado como lacuna, sem inventar família.

**H12 · Precisão e recall são eixos separados.**
Pemex (16) e ISA (21) têm o artigo derivado no acervo e o anúncio original
ausente. Isso não é erro de classificação — é possível falha de cobertura, e
exige auditoria própria.

## Impacto vivo medido (não aplicado)

**123 pontos** em filiações que o humano marcou como não pontuáveis, e **3
empresas mudariam de status**:

| caso | empresa | família | pontos | papel |
|---|---|---|---:|---|
| 01 | Tok&Stok | recuperacao_judicial | 29 | FOLLOW_UP |
| 03 | Sabesp | ma | 21 | PROCESS_STEP |
| 04 | PRIO | follow_on | 18 | opções → **sai do painel** |
| 06 | B3 | troca_ceo | 13 | DESCRIPTOR_BACKGROUND |
| 07 | Tupy | troca_ceo | 13 | DESCRIPTOR_BACKGROUND |
| 11 | Engie Brasil | follow_on | 8 | RETROSPECTIVE_RECAP |
| 16 | Pemex | troca_ceo | 5 | descritor → **sai do painel** |
| 18 | Rumo | troca_ceo | 4 | THIRD_PARTY_COMMENTARY |
| 19 | Bradesco | emissao_cotas | 4 | família errada → **atenção → monitorar** |
| 20 | Santander Brasil | troca_ceo | 3 | ANALYST_COMMENTARY |
| 21 | ISA Energia | follow_on | 3 | STRATEGIC_COMMENTARY |
| 24 | Aegea | follow_on | 2 | subscrição de direitos |

Os fechamentos **não** são casos de remover score: Smart Fit contribui 17 e
Suzano 14, ambos confirmados como marco material que pode renovar.

## Filas abertas (somente leitura)

**Recall** — `POSSIBLE_MISSING_TRUE_ANNOUNCEMENT`: Pemex (nomeação anterior
ausente) e ISA (follow-on original ausente). Classificar entre
`OUTSIDE_WINDOW_POSSIBLE` e `PIPELINE_MISS_POSSIBLE` exige onda própria.

**Auditabilidade de fonte** — Capital One (`BROKEN_SINGLE_SOURCE`, fonte única
inacessível) e Tupy (`LINK_IN_VERIFICATION` na matéria do evento real).
Candidato de painel: avisar quando a **única** fonte que sustenta o score é
inacessível.

**Lacuna de taxonomia** — suspensão regulatória de oferta (Bradesco), aumento
de capital com subscrição (Aegea), aumento por exercício de opções (PRIO).
Separar "vale exibir" de "deve pontuar risco negativo".

**Política pendente** — Copel: emissão **proposta** com rating atribuído deve
pontuar na proposta ou só quando a captação se torna efetiva?

## Uso do conjunto

| tag | casos |
|---|---|
| NEGATIVE_REGRESSION | 01, 03, 04, 06, 07, 11, 16, 18(ceo), 19, 20, 21, 24 |
| POSITIVE_REGRESSION | 02, 05, 08, 09, 10, 14, 15, 17, 18(rec), 22, 23 |
| MINIMAL_PAIR | 03↔05, 06↔17, 18(ceo)↔18(rec), 04↔24, 09↔22 |
| OCCURRENCE_SUPERVISION | 01, 03, 05, 08, 11, 14, 20 |
| TAXONOMY_GAP | 04, 19, 24 |
| RECALL_AUDIT | 16, 21 |
| SOURCE_AUDITABILITY | 07, 13, 15 |
| POLICY_PENDING | 12 |

## Backlog de painel (não implementado)

**D1** linha do tempo da ocorrência (anúncio → etapa → fechamento → follow-up →
comentário) em vez de eventos aparentemente independentes · **D2** por que a
última fase renovou ou não o score · **D3** aviso de auditabilidade de fonte ·
**D4** sinalização interna de lacuna de taxonomia · **D5** corroboração não
multiplica ocorrência · **D6** relatório de recall.
