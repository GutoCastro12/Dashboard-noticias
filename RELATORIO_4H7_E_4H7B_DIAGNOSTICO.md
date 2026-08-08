# FASE 4H.7 — Retrieval reliability: encerrada como "sem bug"
# FASE 4H.7B — Cobertura EDGAR 6-K: diagnóstico + plano (SEM implementação)

## Parte 1 — 4H.7: por que não há retry/backoff a implementar

**Medição real** (não suposição): reconstruí o mesmo conjunto de 214 filings
do run de produção `31269015611` (mesmos 32 emissores elegíveis, mesma
janela de 90 dias, via `fetch_edgar_filings` real, sem alteração) e medi o
downloader ATUAL, sem modificação, contra a SEC ao vivo.

```
Por form: {'10-Q': 19, '8-K': 79, '6-K': 116}
OK (corpo real recuperado): 79/214
Por motivo de falha: form_fora_do_escopo_do_parser_dom_atual: 135
```

**135 = 116 (6-K) + 19 (10-Q), exatamente.** Dos 79 filings realmente
tentados (Form 8-K, o único que `enrich_with_body` tenta hoje —
`if filing["form"] == "8-K"`), **79/79 tiveram sucesso**: zero HTTP não-200,
zero timeout, zero exceção, zero connection reset. Repetido (reexecução
limpa) com resultado idêntico.

**Conclusão A confirmada pela evidência: não há problema de confiabilidade
de retrieval a corrigir.** Os "135 sem corpo" do run de produção não eram
falhas de rede — eram filings cujo Form nunca foi sequer tentado, por
desenho (8-K é o único form que o parser DOM/canônico foi construído e
validado para processar, desde a 4H.3C-F). Implementar retry/backoff sem
nenhuma falha observada seria, pela própria regra desta fase, "inventar
solução" para um problema que a medição não confirma. **Nenhum código foi
alterado.** Branch `feature/edgar-retrieval-reliability` fica sem commits
(nada a integrar) — decisão sua, confirmada: não implementar retry
preventivo sem evidência de falha real.

---

## Parte 2 — 4H.7B: diagnóstico de cobertura 6-K (nova fase, SEM código alterado)

**Pergunta**: quanto da lacuna de 135 já é interpretável com a
infraestrutura EXISTENTE, sem parser novo, e quais gaps reais bloqueiam a
corroboração?

### 2.1 — Quantos dos 135 são 6-K, quais emissores

**116 de 135** (86%) são Form 6-K, de **12 emissores** (todos estrangeiros
privados, exatamente os 12 que a 4H.4 já tinha identificado como
6-K-only):

| Emissor | N 6-K |
|---|---:|
| Ecopetrol | 24 |
| British American Tobacco | 21 |
| YPF | 19 |
| Nubank (Nu Holdings) | 12 |
| Cemex | 11 |
| LATAM Airlines | 7 |
| Telecom Argentina | 6 |
| Vesta | 6 |
| Toronto-Dominion Bank | 4 |
| Millicom (Tigo) | 3 |
| StoneCo | 2 |
| Grupo Aeroméxico | 1 |

Os **19 restantes são Form 10-Q**, um por emissor doméstico (dos 19 que já
filiam 8-K) — formulário **periódico**, já classificado desde a 4H.3C como
"não prova fato novo, sem seção econômica" (`PERIODIC_FORMS` em
`edgar_sections.py`, por desenho). **Não é gap — é escopo correto,
permanente.** Não investiguei mais a fundo, conforme pedido ("provavelmente
não explicam os 135" — confirmado: eles NUNCA teriam corpo processado, por
decisão de arquitetura já tomada e validada, não por limitação de
download).

### 2.2 — Quanto já conseguimos interpretar com a infraestrutura existente

**Achado central: `edgar_sections.py` (fase 4H.3E) já tem um extrator de
seção para 6-K inteiramente construído e calibrado num corpus real (113
docs reais — Nubank/YPF/Cemex, citados no próprio docstring do módulo)** —
`split_6k_release()`, que reconhece linha "Ref.:", dateline de press
release, lista "Contents" e manchete. Este módulo **já está portado na
branch** (usado hoje só por testes), mas **nunca é chamado** pelo caminho
real de corroboração, porque `enrich_with_body` só baixa corpo de Form 8-K.

**Medição real, sem alterar nenhum código**: baixei os 116 corpos 6-K reais
da janela atual (116/116 HTTP 200, zero falha de rede — reforça a
conclusão da Parte 1) e rodei cada um pelo `edgar_sections.evidence_sections`
+ `edgar_canonical.analyze_filing`, exatamente como já existem:

```
HTTP ok (corpo baixado): 116/116
Com >=1 seção identificada (edgar_sections): 71/116  (61%)
Com >=1 candidato ACEITO (edgar_canonical):  66/116  (57%)
Com algum candidato PONTUÁVEL (nunca deveria):  0/116  (confirma a 4H.3F intacta)
```

Distribuição dos candidatos aceitos (todos `nao_pontuavel_por_forma=True`,
nunca escoráveis — a mesma trava da 4H.3F que protege o 8-K também protege
o 6-K, sem nenhuma mudança):

| event_id | N candidatos |
|---|---:|
| troca_ceo | 51 |
| ma | 16 |
| emissao_divida | 11 |
| default | 6 |
| falencia | 2 |
| incidente_operacional | 2 |
| investigacao_regulatoria | 1 |
| fraude | 1 |
| recuperacao_judicial | 1 |

**57% dos 6-K reais já produzem candidato usável com ZERO código novo.**
Exemplo real verificado manualmente (Nubank, accession `0001292814-26-003814`,
20/07/2026): seção `dateline` identificada corretamente, candidato `ma`
aceito, com o próprio motivo do classificador já dizendo
`"seção 'dateline' é heurística de layout, não estrutura garantida pela
SEC — corrobora 'ma', não prova"` — o desenho original da 4H.3E/F **já
previa** este uso como corroboração, não como prova autônoma.

### 2.3 — Gaps reais que impedem corroboração hoje (nenhum é "parser novo")

1. **`enrich_with_body` nunca tenta 6-K** (`if filing["form"] == "8-K"`) —
   gate de 1 linha, puramente estrutural, sem heurística nova.
2. **`apply_edgar_corroboration` filtra só candidatos `aceito=True AND
   not nao_pontuavel_por_forma`** — isso exclui **todos** os candidatos
   6-K por definição, porque `nao_pontuavel_por_forma=True` é como a 4H.3F
   marca justamente os candidatos "corrobora, não prova" (dateline/
   headline/contents/varredura livre). É uma decisão arquitetural real a
   tomar: usar `aceito=True` (sem exigir `not nao_pontuavel_por_forma`)
   como critério de ENTRADA no matching de corroboração — nunca de
   scoring, que continua exigindo `item_dom` e permanece intocado.
3. **`evidence_text` vem vazio** nos candidatos `nao_pontuavel_por_forma`
   (por desenho — nunca foram feitos para exibição/pontuação). Para
   corroboração, preciso do texto da seção para `entity_fingerprint` —
   hoje ele existe em `section["text"]`/`c["section_heading"]`, só não é
   copiado para `evidence_text`. Ajuste estrutural, não heurística nova.

**Nenhum desses 3 gaps exige tocar `edgar_dom.py`, `edgar_sections.py` ou
o motor de classificação (`ITEM_SEMANTICS`/`candidate_events`/
`evaluate_candidate`) — são só pontos de integração no módulo de
corroboração e no seu filtro de candidatos.**

### 2.4 — O que NÃO foi tocado (confirmação explícita)

Nenhuma linha de código foi alterada nesta fase (nem 4H.7 nem 4H.7B) —
`git status` limpo em ambas as branches, além de artefatos de cache já
ignorados. Nenhum peso, threshold, tier, taxonomia, `event_resolution`,
matching (`ec.match_occurrence`) ou source bonus tocado. `edgar_scoring_enabled`
não entra em nenhuma consideração desta fase — o achado do §2.3 item 2 é
estritamente sobre elegibilidade de CORROBORAÇÃO, nunca sobre scoring
autônomo (`_KINDS_PONTUAVEIS`/`item_dom` continuam a única porta para
scoring, essa trava não é tocada em lugar nenhum deste diagnóstico).

---

## Parte 3 — Plano de implementação proposto para uma fase futura (NÃO EXECUTADO)

Caso você autorize uma fase de implementação:

1. **`enrich_with_body`**: estender o gate de `if form == "8-K"` para
   `if form in ("8-K", "6-K")`, chamando `edgar_sections.evidence_sections`
   (já existente) quando `form == "6-K"` em vez de `edgar_dom.parse_8k_dom_sections`
   (que é 8-K-específico, nunca chamado para 6-K).
2. **Candidato → corroboração**: no filtro de `apply_edgar_corroboration`,
   avaliar deliberadamente se `nao_pontuavel_por_forma=True` deve
   participar do MATCHING de corroboração (nunca do scoring) — decisão
   explícita seguindo a mesma filosofia da 4H.5 (match exige empresa +
   família + contraparte + data, nunca só forma de origem do candidato).
   Se autorizado: usar `aceito=True` como critério de entrada,
   independente de `nao_pontuavel_por_forma`.
3. **`evidence_text` para candidatos 6-K**: propagar `section["text"]`
   (já existe em `edgar_sections`) para `evidence_text` do candidato, só
   quando `nao_pontuavel_por_forma=True` E a família bate com uma
   ocorrência já conhecida (nunca para exibição/pontuação — só para
   `entity_fingerprint`).
4. **Testes obrigatórios** (mínimo): 116 6-K reais como fixture/replay
   controlado (mesmo padrão da 4H.5F — bundlar 1-2 fixtures reais, não
   corpus externo); confirmar `FALSE MATCH=0` nos matches que a extensão
   produzir; confirmar que candidatos 6-K NUNCA entram em
   `events_by_company`/scoring (regressão explícita, reaproveitando os
   testes já existentes de invariância); confirmar que a trava
   `_KINDS_PONTUAVEIS`/`item_dom` da 4H.3F permanece intocada (teste de
   regressão direto).
5. **Gate offline → run real controlado → revisão manual de TODO match
   novo → merge**, seguindo exatamente o mesmo fluxo rigoroso já usado em
   4H.5/4H.5F/4H.6 (Validation-like, sem backfill, sem scoring autônomo).

**Este plano não foi implementado. Nenhum código foi escrito para ele.**
Fico aguardando sua decisão sobre se e quando abrir essa fase de
implementação.

## Estado final

- `main`: inalterado desde a 4H.6 (`8178ea6`).
- `feature/edgar-retrieval-reliability`: criada, usada só para diagnóstico,
  **sem commits** (nada a integrar — decisão "sem bug" confirmada).
- `feature/edgar-6k-coverage-diagnosis`: criada para este diagnóstico —
  **sem commits de código**, só este relatório pendente de decisão sobre
  onde/se versionar.
- `edgar_scoring_enabled`: continua false. `edgar_collection_enabled`:
  continua true. Nada mudou no comportamento de produção.

**FIM DO DIAGNÓSTICO 4H.7/4H.7B. PARANDO PARA REVISÃO — aguardando decisão
sobre abrir (ou não) a fase de implementação da cobertura 6-K.**
