# Arquitetura de ocorrência / fase / renovação — desenho

> Onda de DESENHO. Nada foi implementado. Medições de `reliability_occurrence_reproducer.py`
> e `reliability_occurrence_blast.py` sobre o acervo de 838 artigos em `2b4cdd5`.
> Autoridade de score: **nenhuma**.

## O que a medição desmentiu

O backlog assumia **inflação de recência**: um acompanhamento vira representante e
renova o decaimento de um fato antigo. **É falso.** Em nenhuma das 16 ocorrências
multi-artigo o representante é o mais recente — ele é sempre o mais antigo retido,
e a âncora de decaimento é a data dele.

A causa é de **ordem de operações**:

```
candidatos → FUSÃO DE GÊMEOS → clustering por objeto → best_contrib → build_evolution
             (absorve o posterior      (chega tarde:
              como corroboração)        já não há o que separar)
```

A fusão usa como chave **mesma família + janela de `same_event_window_days` (10)**,
**sem porta de objeto** para famílias fora do opt-in. O artigo posterior vira
`corrob` e nunca compete em `best_contribs`.

Consequência dupla, e as duas importam:

- **não existe renovação** — um fechamento material não tem como reancorar;
- **objetos distintos são fundidos** antes de o separador por objeto agir.

## Os dois defeitos, medidos

### 1 · Sobre-fusão de objeto — causa concreta e pequena

`occurrence_identity` inclui o **nome do regulador** entre os marcadores:

```
Sabesp/EMAE      'cade|emae|tribunal'
Sabesp/Sanessol  'cade|sanessol'
```

`cade` é a ponte. Removido o ruído de regulador e conectivo, os objetos separam
sozinhos: `emae` · `sanessol` · `castilho`.

**30 pares empresa × família** (excluído o balde `Mercado (geral)`, que não é
emissor) contêm mais de um objeto disjunto. Os mais claros:

| objetos | artigos | span | par | objetos separados |
|---:|---:|---:|---|---|
| 3 | 9 | 223d | Sabesp / ma | emae · sanessol · castilho |
| 3 | 5 | 189d | BTG Pactual / ma | digimais · hsbc uruguai · safra |
| 3 | 4 | 71d | Petrobras / ma | lightsource · bacia de campos · wilson sons |
| 3 | 4 | 157d | Citigroup / ma | kaiser aluminium · associates · kard |
| 2 | 8 | 256d | Axia / emissao_divida | bndes · eletrobras |

**A produção impõe hoje uma única ocorrência pontuável por empresa × família.** Isso
é uma restrição arquitetural, não uma consequência do acervo: uma empresa pode
legitimamente ter duas aquisições, duas emissões ou duas ações de rating na janela.

### 2 · Ausência de renovação

**11 sequências** têm fechamento posterior a um anúncio anterior. E o efeito **não
é uniformemente positivo**:

| par | âncora atual | fechamento | efeito |
|---|---|---|---|
| Engie / follow_on | 2026-06-11 | 2026-08-10 | **+17,4** |
| Smart Fit / ma | 2026-07-09 | 2026-08-04 | **+11,3** — *humano confirmado* |
| Baker Hughes / ma | 2026-06-12 | 2026-07-20 | +10,3 |
| BTG / ma | 2026-06-24 | 2026-07-14 | +5,6 |
| Sabesp, Tupy, Cemig, **Suzano** | — | — | **já ancorados no fechamento** |
| Petrobras / ma | 2026-06-01 | 2026-05-21 | −0,8 |
| Capital One / ma | 2026-05-28 | 2026-04-07 | −3,6 |
| **Citigroup / ma** | 2026-08-14 | 2026-05-05 | **−28,4** |

> **Correção de um checkpoint anterior:** eu havia registrado a **Suzano** como
> divergência de renovação. Ela não é — o fechamento **já é** a âncora. Meu
> indicador (`representante_e_o_mais_antigo`) dava falso positivo em ocorrência de
> membro único, onde "mais antigo" e "mais recente" coincidem. **Só o Smart Fit
> diverge** entre as âncoras humanas.

O caso Citigroup fixa a regra: renovação é

```
âncora' = max(âncora_atual, data_da_fase_material)
```

nunca "use a data do fechamento".

### 3 · Alias não é inferível — e a Suzano está certa por acidente

`clark|kimberly` e `arbex|suzb3` são **disjuntos mesmo sem ruído**. A Suzano só
está correta porque a sobre-fusão a uniu — a mesma sobre-fusão que erra na Sabesp.
Corrigir a identidade **quebraria** a Suzano se nada mais for feito.

Logo: alias precisa ser **declarado**, não adivinhado. Um gancho determinístico
(par de nomes com proveniência e autoria), não heurística de similaridade.

### 4 · Proveniência perdida na absorção

**133 artigos absorvidos · 27 resolvíveis por `article_id` · 106 não · 28 das 62
ocorrências afetadas.** A fusão guarda do absorvido apenas domínio e URL de
redirect do Google News. Qualquer linha do tempo de ocorrência exige preservar o
`article_id` canônico **antes** da absorção — é a menor mudança de proveniência
necessária, e é pré-requisito de tudo o mais.

## Seis conceitos que não podem virar um só

| | pergunta | onde vive hoje |
|---|---|---|
| **A** asserção | o artigo afirma um evento? | `semantic_audit` — resolvido |
| **B** identidade | é a mesma ocorrência econômica? | fusão + clustering — **fundidos cedo demais** |
| **C** fase | anúncio / etapa / fechamento / acompanhamento? | `_ident['fase']` — **computado e descartado** |
| **D** renovação | esta fase reancora? | **não existe** |
| **E** representante | qual artigo mostrar? | `best_contribs` — junto de (D) |
| **F** data efetiva | qual data ancora o risco? | data do artigo — **sem `effective_event_date`** |

`_ident['fase']` já produz `anuncio` / `aprovacao` / `encerramento` e **é jogado
fora** na decisão. É o menor ponto de entrada da arquitetura inteira.

## Taxonomia mínima de fase

Quatro estados, não nove. Estados frágeis demais viram rótulo sem consumidor:

| estado | o que é | renova? |
|---|---|---|
| `INICIACAO` | anúncio, acordo, compromisso | abre a ocorrência |
| `ETAPA` | aprovação regulatória, assembleia, liminar | **não** |
| `MATERIAL` | fechamento, conclusão, liquidação | **elegível** |
| `ACOMPANHAMENTO` | comentário, consequência, recapitulação | **não** |

Ancoradas em verdade humana: Sabesp = `ETAPA`; Smart Fit e Suzano = `MATERIAL`;
Tok&Stok, Engie e ISA = `ACOMPANHAMENTO`.

## Política de renovação

Separar **elegibilidade** de **renovação efetiva**:

```
refresh_eligible  = (fase == MATERIAL) e (política da família permite)
refresh_efetivo   = refresh_eligible e (data_fase > âncora_atual)
âncora'           = max(âncora_atual, data_fase)
```

Saída explícita: `refresh_authority`, `refresh_reason`, `effective_anchor_date` —
o painel precisa poder dizer **por que** o score reancorou.

## Representante ≠ âncora de score

São perguntas diferentes e devem ser campos diferentes:

- **representante de exibição** — o artigo que melhor explica o fato (em geral o
  anúncio, que tem contexto);
- **âncora de score** — a data que controla o decaimento (o marco material mais
  recente).

Smart Fit é o caso: o anúncio explica, o fechamento ancora.

## Reaproveitar `occurrence_truth` — não criar segunda verdade

O schema **já modela tudo**:

- membership: `material_phase`, `should_refresh_anchor`, `occurrence_novelty`,
  `article_ref`, `evidence`, `superseded_by`;
- occurrence: `family_identity`, `material_event_date`, `company`, `event_id`.

`material_event_date` é exatamente o `effective_event_date` que falta na produção.
**Nenhum schema novo é necessário.** Os papéis ficam:

- `risk_human_supervision.json` — julgamento **de artigo** (asserção, fase, papel);
- `occurrence_truth` — julgamento **de relação** (identidade, fase material, âncora).

## Identidade estável

`_occ_key` é hoje `empresa|família#índice` — o índice muda quando o conjunto muda.
Um id de ocorrência precisa ser função apenas de `(empresa, família, objeto
canônico)`, nunca de representante, data ou número de fontes. Acrescentar fonte,
acompanhamento ou fechamento **não pode** trocar o id.

## Três opções

### A · Identidade de objeto ANTES da fusão
Filtrar ruído de marcador e usar objeto como chave da fusão.
**Blast:** 30 pares passam a poder dividir; Sabesp vira 3 ocorrências.
**Ganha:** ataca a causa raiz na ordem certa. **Perde:** quebra a Suzano enquanto
não houver alias; muda score de muitas empresas de uma vez.

### B · Manter a fusão, dividir por objeto antes do score
Camada nova entre fusão e `best_contribs`.
**Blast:** mesmo efeito de score, sem tocar na fusão.
**Ganha:** menor diff. **Perde:** mantém a ordem errada; a corroboração já foi
perdida na fusão, e a linha do tempo continua impossível.

### C · Fusão consciente de ocorrência
Substituir a fusão de gêmeos por uma que produz **ocorrência com membros e fases**
em vez de um sobrevivente com corroboração.
**Blast:** o maior. **Ganha:** resolve identidade, fase, renovação, representante,
data efetiva e proveniência de absorvido **de uma vez**, porque todos são atributos
da mesma estrutura. **Perde:** exige modo sombra e comparação de corpus inteiro.

## Recomendação: **C, em modo sombra**

Não é a menor diff — é a única que não deixa a arquitetura errada de pé. B é o
menor diff e **mantém o defeito de ordem**: sem membros preservados não há linha do
tempo, não há renovação auditável e a proveniência dos 106 absorvidos continua
perdida. A recomendação do projeto tem sido escolher pela arquitetura, não pelo
tamanho do patch.

**Ponto de inserção:** `risk_dashboard.build_evolution`, entre a montagem de
`per_company` (~5268) e `best_contribs` (~5525), substituindo o laço de fusão
(~5401–5481). Arquivos: `risk_dashboard.py` e um módulo novo de ocorrência.

## Próxima onda — implementação em sombra, sem autoridade

1. estrutura de ocorrência com `occurrence_id` estável, membros, fase por membro,
   `anchor_date`, `display_representative`, `refresh_reason`;
2. derivar identidade **antes** da fusão, preservando `article_id` do absorvido;
3. gancho de alias **declarado** (Kimberly-Clark IFP = Arbex como primeiro caso);
4. rodar em paralelo à produção sobre o corpus inteiro e **comparar**, sem pontuar;
5. avaliar contra as âncoras humanas e contra `occurrence_truth`.

**Portões para promover:** todas as âncoras humanas corretas · blast sem caso
inexplicado · corroboração e representante preservados · score/status simulados e
revisados · snapshots arquivais intactos · bateria completa.

---

# Adendo — o que a Sombra V1 corrigiu neste desenho

> Escrito depois de implementar a opção C em `reliability_occurrence_shadow.py`.
> Três afirmações desta página não sobreviveram ao contato com o corpus.

## 1 · `company | family | objeto canônico` **não basta** como identidade

Esta página propôs identidade estável a partir de empresa + família + objeto
canônico. **É insuficiente**, e a própria verdade humana já o dizia: a Hapvida
tem **duas** trocas de CEO reais em quatro meses
(`troca_ceo:hapvida:3b55e5fc412d` × `…dc829e29aab1`, adjudicadas
`DISTINCT_OCCURRENCE`). Mesma empresa, mesma família, mesmo tipo de objeto —
dois eventos econômicos distintos. O mesmo vale para 16ª × 17ª emissão.

A identidade precisa de **duas camadas**:

| camada | pergunta | de onde sai |
|---|---|---|
| **objeto** | que ativo/entidade está envolvido? | marcadores com papel `OBJECT_MARKER` + alias declarado |
| **instância** | *qual* evento econômico sobre esse objeto é este? | feature discriminante do adaptador de família |

Discriminantes por família: `ma` → valor; `emissao_divida`/`follow_on` → série;
`troca_ceo` → pessoas; rating → agência + direção.

## 2 · O nome canônico do objeto **não pode** ser frequência nem núcleo comum

A formulação óbvia — token mais frequente do grupo, ou núcleo comum a todos os
membros — **quebra a invariante de id estável**: frequência cresce e núcleo
encolhe quando um membro entra. A primeira implementação trocou o
`occurrence_id` ao receber a etapa regulatória; o teste metamórfico pegou.

O nome sai do **membro de abertura** (a INICIAÇÃO mais antiga, ou o membro mais
antigo na falta dela) — imutável sob acréscimo posterior, e é a *initial
material event signature* de §16. Limite honesto: uma fonte publicada **antes**
da abertura conhecida desloca a abertura; por isso cada ocorrência carrega
`id_stability` (`CONTENT_STABLE` × `DATE_ANCHORED`).

## 3 · Nem toda família tem objeto **externo**

A oferta, a emissão, a recuperação judicial e a ação de rating são do **próprio
emissor**. Ali um nome próprio no título é o destino dos recursos (Engie/Jirau)
ou um terceiro citado de passagem (Tok&Stok/Mobly) — não a identidade do fato.
Deixar esses tokens fatiarem produziu *over-split* pior que a produção.

Foi também o que causou a única **colisão de `occurrence_id`** medida: dois
artigos sobre o mesmo rebaixamento da S&P na Cosan, a 28 dias, caíam em baldes
anônimos distintos (janela de 10 dias) e geravam o mesmo id. A colisão é
**detectada e reportada** como bloqueador — remendá-la com sufixo de ordem
reintroduziria o índice de cluster que a arquitetura proíbe.

## O que a Sombra V1 entrega, medido

| | produção | sombra V1 |
|---|---:|---:|
| ocorrências (janela de 90d) | 62 | **79** |
| membros com `article_id` | — | **127/127** |
| artigos absorvidos sem id resolvível | **106** de 133 | **0** |
| colisões de id | — | **0** |

Concordância humana por dimensão: **fase 13/14** · **renovação 5/6** ·
**identidade 11/14** (mais 2 casos onde o artigo de abertura está **fora** da
janela de score — limite de janela, não erro de identidade). `occurrence_truth`:
fase **4/4** onde avaliável, nenhuma verdade fragmentada.

Controles: Sabesp separa `emae` × `castilho`; Smart Fit reancora no fechamento
(+); Suzano funde por alias declarado e a âncora **não se move** (já era o
fechamento); Tok&Stok, ISA e Engie não renovam por acompanhamento; **nenhum**
membro `ETAPA`/`ACOMPANHAMENTO` posterior à âncora renovou.

Engie demonstra §18 em dado real: representante = anúncio de 17/07, âncora =
conclusão de 10/08. **São campos diferentes porque são perguntas diferentes.**

## Bloqueadores de promoção que permanecem

1. **Cosan** — Moody's rebaixa; humano diz `NEW_OCCURRENCE`, sombra funde. O
   discriminante agência+direção não distingue dois rebaixamentos da **mesma**
   agência.
2. **Vale / investigação** — mesma classe: família sem discriminante.
3. **Natura** — humano diz `SAME_OCCURRENCE`; nenhum antecessor localizável no
   acervo.
4. **Engie / recapitulação** — "Engie lucra R$ 694 mi no 2º tri **e conclui**
   follow-on" é lido como `MATERIAL`. A asserção primária é o resultado; a
   conclusão vem em oração coordenada secundária. Distinguir isso exige análise
   de oração, não vocabulário — e **não** foi implementado para não sobreajustar.
   Mitigação medida: a âncora da Engie não muda por causa disso, porque o
   fechamento genuíno de 10/08 já a governa.

Nenhum deles foi "resolvido" ajustando regra até o número fechar. Todos estão na
fila de revisão como `REVIEW_CANDIDATE`.

---

# Adendo 2 — Sombra V2, calibrada por decisão humana

> `HUMAN_REVIEW_2026_08_20`. As quatro adjudicações P1 chegaram e são
> autoridade. Nada foi promovido à produção.

## O que cada decisão mudou na regra — e o que ela não autorizou

### Cosan · a agência é discriminante de instância

**Decisão:** ações de **agências diferentes** são ocorrências distintas; nem
empresa, nem família, nem direção, nem proximidade de data as fundem.

A V1 já separava S&P de Moody's. O que faltava era o inverso — §22-C: *mesma*
agência não implica mesma ocorrência. O adaptador passou a carregar o **nível
atribuído**, e o corte de episódio ficou: dentro da janela de corroboração, ou
mesmo nível ⇒ mesma ação; fora da janela sem nível comum ⇒ ações distintas.

| | |
|---|---|
| S&P 08/07 `B+` × S&P 05/08 `B+` | mesma ação renoticiada |
| Moody's 16/07 `B1` × Moody's 10/08 (sem nível) | **ações distintas** |

Isto é **identidade**, não política de renovação — e a página registra isso
explicitamente, porque a decisão humana não falou de renovação de rating.

### Vale · regulador sozinho não é identidade; e o representante muda

**Decisão:** desenvolvimentos do mesmo processo são a mesma ocorrência, e o
desenvolvimento substantivo **mais recente** é o representante principal.

O que a evidência local mostrou: **não há um processo único**. Dois artigos
*abrem* processos (20/07 sobre apoio da Previ a candidato; 23/07 sobre
destituição de conselheiro) com assuntos disjuntos. A regra passou a tratar
"abre processo" como **asserção de procedimento novo** — não absorvível como
corroboração de um artigo anterior que não abriu nenhum, nem dentro da janela de
10 dias. Sem identificador de processo e sem assunto comum, **não se força**
mesma ocorrência a partir de empresa + CVM.

A política de representante ganhou consciência de família:

| tipo de família | representante |
|---|---|
| **transação** (`ma`, `follow_on`, emissão) | a iniciação, que explica o fato |
| **estado contínuo** (`investigacao_regulatoria`, `recuperacao_judicial`) | o desenvolvimento **substantivo** mais recente |

"Mais recente" não é "o último publicado": um acompanhamento nunca vira
principal. Tok&Stok continua representada pela aceitação da RJ, não pela matéria
de consequência.

E a **renovação da investigação segue em aberto**. A decisão humana foi sobre
exibição; equiparar as duas coisas teria inventado verdade.

### Natura · um compromisso, marcos sucessivos

**Decisão:** 30/03 (compromisso vinculante de 8%–10%), 02/07 (6,6% + 1,4% via
TRS) e 31/07 (8%, o mínimo comprometido) são **uma** transação econômica.

O gancho não é o nome *Advent*: é o **vínculo econômico explícito** — o fato
relevante diz que a aquisição *"decorre do Compromisso Vinculante"*. Atingir o
piso comprometido também conta como cumprimento, e não como a "assembleia" que
aparece na mesma manchete. Sem a frase de vínculo, o mesmo fato **não** vira
marco material.

Os três marcos preservam o mesmo `occurrence_id`.

> **Limite de coleta, não de identidade.** O acervo tem **um único** artigo
> Advent (02/07). Os de 30/03 e 31/07 nunca foram capturados — nenhuma regra de
> ocorrência poderia ligá-los. A métrica passou a separar `WINDOW_LIMITED`
> (existe, fora da janela) de `CORPUS_LIMITED` (nunca coletado), para não
> contabilizar lacuna de cobertura como erro de identidade. A cronologia entra
> como fixture com proveniência; `risk_human_supervision.json` fica intocado,
> porque seu schema indexa por `article_id` e esses artigos não têm um.

### Engie · `conclui` sozinho não decide fase

**Decisão:** *"lucra R$ 694 mi no 2º tri e conclui follow-on"* é recapitulação
de fato já ocorrido — acompanhamento, sem renovação.

Era exatamente o bloqueador que o Adendo 1 se recusou a resolver por vocabulário.
A distinção implementada é **sintática**: asserção primária de *outro* evento na
oração principal + verbo material depois da coordenação. A ordem importa —
"conclui aquisição **e** lucra R$ 100 mi" continua material.

E a data efetiva de qualquer `ACOMPANHAMENTO` passou a ser declarada
**desconhecida** em vez de carimbar a data de publicação como data do fato.

## Uma regressão que a V1 tinha introduzido

Ao medir a Yobel descobriu-se que a V1 **partia** o incêndio em três
(`incidente_operacional`, `incidente_operacional_grave`,
`paralisacao_operacional`), triplicando o score. A produção já agrupava esses
`event_id` por família opt-in (`merge_occurrences_across_articles`) — a sombra
não. Corrigido: famílias opt-in agrupam pela **instalação**, ausência de marcador
não contradiz um local conhecido, e a identidade por marcador não expira com o
tempo. Yobel voltou a ser **uma** ocorrência, e sua mudança de status sumiu.

Era uma regressão contra comportamento que já estava certo — o tipo de coisa que
só aparece quando se mede tudo, não só os casos em disputa.

## Resultado

| dimensão | V1 | V2 |
|---|---:|---:|
| identidade | 11/14 | **13/13** |
| fase | 13/14 | **14/14** |
| renovação | 5/6 | **6/6** |
| representante | — | **3/3** |
| data efetiva | — | **3/3** |
| ocorrências | 79 | 79 |
| colisões de id | 0 | **0** |
| proveniência | 127/127 | **127/127** |

Âncoras anteriores intactas: Tok&Stok, Sabesp (`emae` × `castilho`), Smart Fit
(renova), Suzano (alias, delta zero), Santander (zero ocorrências), ISA.

## O que continua bloqueando a promoção

**Uma** mudança de status: JBS `atenção → crítico`, +56,7, causada por divisões
ainda `AMBIGUOUS` em `ma` (1→3) e `troca_ceo` (1→2). Nomear a causa não é ter
respaldo humano para ela, e o portão foi endurecido para exigir o segundo.

Política de renovação, sem esconder incerteza:

| família | estado | aberto |
|---|---|---|
| `ma` | `HUMAN_CONFIRMED` | — |
| `follow_on` | `PARTIALLY_ESTABLISHED` | se uma conclusão **corrente** genuína renova |
| `rebaixamento_rating` | `IDENTITY_ONLY` | nenhuma decisão sobre renovação |
| `investigacao_regulatoria` | `REPRESENTATIVE_ONLY` | se um desenvolvimento novo renova |
| `emissao_divida` | `UNREVIEWED` | tudo |

BTG e Baker Hughes seguem `UNREVIEWED`: a calibração de hoje não lhes empresta
verdade.

---

# Adendo 3 — Sombra V3: JBS, e a separação entre ocorrência e score

> `HUMAN_REVIEW_2026_08_20`. A decisão da JBS fechou o último bloqueador de
> ocorrência. Nada foi promovido.

## A decisão e a ressalva que veio junto

As três transações da JBS são **ocorrências distintas**: os 18% restantes da
Pilgrim's Pride, a compra da Walkers Deli pela Pilgrim's no Reino Unido, e os
US$ 150 mi em Omã. E o humano acrescentou o que importa: **elas não são
necessariamente adversas**. Separar corretamente não prova que a JBS merece
severidade maior.

Essa ressalva reorganizou a onda inteira.

## A inflação não vinha das transações

A V2 media JBS `atenção → crítico`, +56,4, e eu a classifiquei como divisão
ambígua. **Estava errado.** Medindo artigo a artigo, as duas parcelas extras
vinham de dois textos que não são eventos econômicos:

| artigo | o que a V2 fazia | o que é |
|---|---|---|
| "UBS reitera recomendação de compra para JBS **após** proposta de aquisição da PPC" | abria uma 3ª ocorrência de M&A | análise de casa **sobre** a proposta |
| "JBS busca recuperar margens nos EUA com gado mexicano, **e novo CEO promete continuidade**" | abria uma 2ª ocorrência de CEO | descritor de cargo, sem asserção |

Duas regras, ambas escopadas:

- **asserção primária de analista** → `ACOMPANHAMENTO`, **exceto** nas famílias
  em que a ação da casa *é* o evento (`recomendacao_negativa`,
  `rebaixamento_rating`). O corte de preço-alvo da Stephens segue sendo evento.
- **descritor sem asserção** → reusa `detect_troca_ceo_sem_assercao`, a guarda
  `R_TROCA_CEO_SEM_ASSERCAO` **já publicada em produção**. Reimplementá-la
  criaria duas opiniões sobre a mesma coisa.

E um princípio geral: **um acompanhamento não abre ocorrência**. Sem objeto
próprio, ele se ancora na ocorrência mais próxima — criar uma seria inventar um
evento econômico a partir de um comentário.

Resultado: JBS **90,3** simulado contra **90** de produção, mesmo status. O
delta de status desapareceu **do corpus inteiro** — zero.

## Em M&A o objeto é o ALVO

O controle sintético fundiu a proposta pela Pilgrim's com a compra da Walkers
*pela* Pilgrim's: os dois títulos citam a Pilgrim's, mas numa ela é alvo e na
outra é compradora. O objeto passou a ser o que vem **depois** da pista de
aquisição — e a pista só vale seguida de conectivo de complemento
("aquisição **da** Walkers", "aquisição **em** Omã"), senão "aquisição
**decorre** do Compromisso Vinculante" tomaria o compromisso como alvo.

Também se recuperou o nome próprio de exatamente três letras, que a produção
descarta pelo mínimo de quatro em `_marcadores_operacao` — era o objeto de uma
das três transações: **Omã**.

## O que não está no acervo

**A transação Walkers não existe em `risk_history.json`.** A sombra alcança 2
das 3 transações humanas; a terceira é reportada como não coletada, não
fabricada. Mesma classe do caso Natura: lacuna de **coleta**, e nomeá-la é
melhor do que fingir concordância.

## Identidade não pode depender de score

Metamórfica obrigatória, agora travada: os mesmos artigos com peso de `ma`
**zero**, normal e **dobrado** produzem `occurrence_id` e membros **idênticos**.
Só a contribuição muda. Sem isso, "corrigir" identidade até o score fechar
seria sempre possível — e a arquitetura não valeria nada.

## O que a taxonomia já dizia, e ninguém tinha somado

O `config_risco.yaml` declara `direction` para cada família. **Oito famílias
declaradas `neutra` pontuam pela mera existência do evento**, somando **180
pontos** de peso-base:

| família | peso | severidade | direção declarada |
|---|---:|---|---|
| `ma` | 40 | alto | **neutra** |
| `emissao_divida` | 35 | alto | **neutra** |
| `follow_on` | 30 | alto | **neutra** |
| `troca_ceo` | 25 | alto | **neutra** |
| `mudanca_regulatoria` · `emissao_cotas` · `indice` · `pequenas_aquisicoes` | 15·15·10·10 | médio | **neutra** |

Na JBS isso é literal: **86%** do score simulado vem de famílias de direção
indeterminada. Das cinco ocorrências, **uma** é adversa pela declaração da
própria taxonomia — o corte de preço-alvo, 12,6 de 90,3.

O painel hoje conflaciona **evento material** com **evento ruim**. Nenhum peso
foi tocado; a medição é somente leitura, e não se criou motor de polaridade
nenhum: fora do que a taxonomia declara `negativa`, a direção sai
`DIRECTION_UNDETERMINED` — nunca "positiva" por conta própria.

## Promoção em dois estágios não é segura

Lido do código, não suposto:

```python
k = o.get("_occ_key") or o["event_id"]          # best_contribs
return sum(b["contrib"] for b in best_contribs(...).values())
```

O total do emissor é **uma contribuição por `_occ_key`, somada**. Dividir uma
ocorrência em duas **acrescenta uma parcela**. Promover a estrutura de
ocorrência sem mexer no score exigiria manter duas chaves vivas ao mesmo tempo
— uma para exibir e outra para pontuar — que é a verdade dupla inconsistente
que a própria onda proíbe.

**Portanto: calibração de política de score ANTES da promoção de ocorrência.**

## Dois veredictos, não um

| | estado |
|---|---|
| **Ocorrência** | identidade 13/13 · fase 14/14 · renovação 6/6 · representante 3/3 · data efetiva 3/3 · ids estáveis · proveniência 127/127 · zero fusão inexplicada · **zero bloqueadores** |
| **Score** | política de renovação aberta em 4 famílias · 8 famílias `neutra` pontuando por existir · **não pronta** |

O booleano único de `promocao()` teria dito "pronto" agora que a identidade está
limpa. Foi substituído por `prontidao()`, que reporta os dois lados separados —
identidade limpa **não** autoriza autoridade de pontuação.

## Fora de escopo, e permanecem separados

- **B3/Gol** — `ATTRIBUTION_REVIEW_CANDIDATE`; a raiz é atribuição, não ocorrência.
- **Pemex** — `RECALL_AUDIT_CANDIDATE`.
- **ISA** — o anúncio original **existe** no acervo e é o representante. A suspeita
  de recall do lote V1 **não se confirma**; o artefato humano registra "candidato",
  não afirmação, então não foi alterado.

## Backlog de painel

Linha do tempo da ocorrência: iniciação → etapa → material → acompanhamento, com
representante de exibição, âncora de score, motivo da renovação e fontes
corroboradoras nomeadas.

---

# Adendo 4 — Promoção à produção: ocorrência + score com portão de direção

> Onda de PROMOÇÃO. As duas mudanças foram juntas porque o total do emissor é
> a soma de uma contribuição por `_occ_key`: mexer na identidade muda o score
> por construção.

## A decisão humana, implementada

| | |
|---|---|
| **Política A** | evento de família `neutra` **não soma risco** por existir |
| **Política B** | evento de família `neutra` **não conta como tipo negativo** |
| **Favorável** | zero risco, zero tipo negativo, e **nunca subtrai** |
| **Adverso** | mecânica de score preservada, intacta |

Uma **fonte única** de autoridade — `tem_autoridade_adversa()` — é consultada
pela contribuição, pela contagem de tipos negativos e pelo gatilho de evento
crítico. Nada infere autoridade de "score > 0": decaimento e arredondamento
tornariam a regra circular.

O motor **não mantém lista paralela de famílias**: lê `direction` da própria
config, que já estava preenchida e nunca era consultada na hora de pontuar.

## Uma terceira noção de "negativo" que precisava do mesmo portão

`n_negative_types` era a decisão explícita; mas o alerta de **persistência**
("N sinais negativos em D dias") é a mesma noção sob outro nome. Deixá-lo
contar atividade contextual manteria um emissor em `atenção` exatamente pelo
motivo que a política removeu. Foi gateado junto — e é o que move a JBS.

## `is_positive()` não cobria `mitigadora`

Auditando o enum: `is_positive()` cobre só `direction == "positiva"` (9
famílias). As **6 `mitigadora`** entravam em `negatives` e somavam risco. Todas
têm peso 0 hoje, então a mudança é **semântica, não numérica** — mas fica
explícita em vez de latente.

Nenhuma família está sem `direction`: **não há caso UNKNOWN** para o portão
reinterpretar em silêncio.

## Uma regressão que a promoção criou, e a correção

A âncora da Tok&Stok caiu de 21/08 para 18/06: **95 → 22**, quatro vezes menos
recência num evento **adverso**. A regra da sombra — âncora na iniciação, só
avança em fase material — foi desenhada para **transações**, onde o anúncio
data o fato.

Uma **recuperação judicial em curso com notícia fresca é risco fresco.**
Famílias de **estado contínuo** (`recuperacao_judicial`,
`investigacao_regulatoria`) passaram a ancorar no desenvolvimento
**substantivo** mais recente — a mesma regra que o humano adjudicou para o
*representante* no caso Vale. Tok&Stok voltou a **98**, crítica.

E o representante virou a compra de 13,2% em meio à RJ, não a matéria de
consequência ao consumidor — que segue corroboração, como o humano decidiu.

> Esta é a única regra da produção que a Sombra V3 não tem. A sombra continua
> sendo o oráculo do contrato V3; a produção acrescenta uma regra a mais, com
> o motivo registrado.

## O blast

| | antes | depois |
|---|---:|---:|
| score total | 1034 | **470** |
| mediana | 9,5 | 0,0 |
| p90 | 33,0 | 22,5 |
| crítico | 1 | **1** |
| atenção | 12 | **4** |
| monitorar | 51 | **59** |
| ocorrências pontuáveis | 65 | **78** |
| membros com `article_id` | — | **107/107** |

**Oito transições, todas `atenção → monitorar`, nenhuma para cima, nenhum
crítico novo.** Cemig, BTG e Engie tinham **zero** evento adverso; Bradesco,
Yura, Santander e JBS têm um só tipo adverso; Vale é `BOTH` — seu score
**subiu** (20 → 38, três processos CVM distintos adjudicados) e ainda assim
caiu de status, porque um tipo adverso não promove.

Distribuição de tipos negativos: antes **11** emissores tinham ≥2; agora **2**.

## Deriva em famílias adversas: 9 idênticas, 5 explicadas

| emissor | ocorrências | score | por quê |
|---|---|---|---|
| Cosan | 1 → 3 | 61 → 120 | separação por agência, adjudicada |
| Vale | 1 → 3 | 17 → 38 | dois processos CVM distintos, adjudicados |
| Rumo | 1 → 2 | 5,9 → 9,2 | duas avaliações de analista distintas |
| Tok&Stok | 1 → 1 | 95 → 98 | 5 fontes em vez de 2 — proveniência recuperada |
| Yobel | 1 → 1 | 22 → 31 | família opt-in unindo os três estágios |

Nenhuma inesperada.

## Equivalência com os oráculos

- **Ocorrência**: 78 das 79 ocorrências da Sombra V3, com membros e
  representantes idênticos. A 79ª é artefato da sombra, que monta candidatos
  por conta própria e inclui um artigo que a produção já filtrava **antes**
  da promoção.
- **Score**: a política `P1b` (portão de direção + portão de tipos) reproduz a
  produção em **64/64 emissores, em score e em status** — exatamente, sem o
  resíduo de arredondamento do diagnóstico, porque a produção usa a
  contribuição canônica.

## Migração de testes

Vários testes codificavam o mundo pré-promoção: "a produção perde `article_id`
dos absorvidos", "uma ocorrência por empresa × família", "o representante é
sempre o mais antigo", "M&A legítimo pontua". Eram **medidas de defeito**, não
invariantes — travá-las exigiria manter o defeito.

Cada uma foi trocada pela propriedade que realmente protegia: a lacuna está
**fechada**; a restrição legada **caiu** (e a verdade humana exige que tenha
caído); a âncora tem **duas regras deliberadas**; e o M&A legítimo continua
**visível**, que é o que a guarda dos 142 falsos negativos sempre defendeu.

## O que continua fora

Sobreposição direcional (evento contextual + qualificador adverso explícito →
autoridade de score): ponto de entrada documentado, **não implementado**.
Recalibração de limiar: deliberadamente separável e observável depois, agora
que a semântica mudou.
