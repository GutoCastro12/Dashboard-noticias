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
