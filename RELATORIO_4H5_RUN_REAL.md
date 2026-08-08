# FASE 4H.5 — Run real controlado: entrega final

**Decisão: B — Corroboração EDGAR confirmada confiável no run real. Pronta para
revisão antes de qualquer merge.** Não existe (nem foi considerada) opção de
scoring autônomo.

---

## 1-2. Blast radius das flags + diff exato de config

Confirmado por inspeção direta do código (não por suposição) antes de tocar
`config_risco.yaml`:
- `international_official_sources_enabled` é lido em **um único lugar** em todo
  `risk_dashboard.py`: dentro de `edgar_collection_enabled()`.
- `official_sources.EUA.enabled` é lido só por `edgar_collection_enabled()` e
  `fetch_edgar_filings()` — ambos exclusivos de EDGAR.
- `official_sources` tem 16 países cadastrados; nenhum além de EUA é lido por
  qualquer função de coleta (o coletor do Brasil usa `cvm_fatos_relevantes`,
  seção separada). Nenhum outro país tinha `enabled` setado.
- Não existe flag mais estreita: `edgar_collection_enabled()` já é o menor
  mecanismo, exige as duas chaves em AND.

Diff aplicado (commit `869e66b`):
```diff
+international_official_sources_enabled: true
 official_sources:
   ...
   EUA:
+    enabled: true
     regulador: SEC
```
Confirmado após load: `edgar_collection_enabled(cfg) == True`,
`edgar_scoring_enabled(cfg) == False`.

## 3. HEAD pré-run / commits

- Branch: `feature/edgar-corroboration-4h5`
- `ebae571` — implementação da corroboração (mecanismo, 28 testes, replay offline)
- `869e66b` — diff de config (coleta ligada, scoring off) — **HEAD do run**
- Push: `origin/feature/edgar-corroboration-4h5` (ambos os commits)

## 4-7. Run real

- **Run ID**: execução local única, log completo em `run_4h5_real.log`
  (preservado localmente, não commitado — ver §10).
- **Status**: `EXIT_CODE=0` — concluído sem erro fatal.
- **Artifact**: `dashboard_risco.html` gerado com sucesso (log linha 382);
  cópia do `risk_history.json` pós-run preservada em
  `run_4h5_artifact/risk_history_post_run.json` (local, não commitada).
- **Filings SEC coletados**: 214 (32 emissores elegíveis, janela de 90 dias).
- **Candidatos EDGAR avaliados para corroboração**: 7.
- **Matches**: 1.
- **Rejeitados (sem match)**: 6.
- **EDGAR-only**: 0 eventos entraram em scoring (por desenho — ver §8).

## 5/6. Revisão manual do match (100% — o único que houve)

| Campo | Valor |
|---|---|
| Emissor | Baker Hughes |
| Ocorrência existente | `https://www.stocktitan.net/sec-filings/BKR/10-q-baker-hughes-co-quarterly-earnings-report-2e09bbebf207.html` |
| Título da ocorrência | "Baker Hughes (Nasdaq: BKR) lifts cash, adds debt to fund Chart acquisition" |
| event_family | `ma` |
| Fonte principal (antes) | Stock Titan (stocktitan.net) |
| Accession SEC | `0001193125-26-305477` |
| Form | 8-K |
| Item | 2.01 (Completion of Acquisition or Disposition of Assets) |
| Evidence SEC | "At the effective time of the Merger..., each share of common stock of Chart... issued and outstanding immediately prior to the Effective Time... owned by Baker Hughes..." |
| Motivo do match | nível 1 — mesma empresa, mesma família (`ma`), contraparte em comum `['chart']`, lag 11 dias (tolerância da família M&A: 30 dias) |
| subject_company | Baker Hughes (filer = sujeito, sem ambiguidade de subsidiária/terceiro neste caso) |
| Contraparte | Chart Industries |
| event_date existente | 2026-07-27 (pub_iso do registro Stock Titan) |
| filing_date SEC | 2026-07-16 |
| `corrob_sources` antes | `[]` (vazio) |
| `corrob_sources` depois | `[{"source": "SEC · 8-K · Item 2.01", "domain": "sec.gov", "url": "https://www.sec.gov/Archives/edgar/data/1701605/000119312526305477/d105425d8k.htm", "when": "15/07 21:00", ...campos de link já resolvidos}]` |
| Score do emissor (Baker Hughes, 90d) antes | 26 (status: monitorar, ranking #21) |
| Score do emissor depois | 26 (status: monitorar, ranking #21) — **sem delta** |
| Motivo exato do "sem delta" | `best_contribs()` mantém só a ocorrência de MAIOR contribuição por `event_id`/emissor. A ocorrência vencedora do M&A de Baker Hughes é a matéria da Yahoo Finance (17/07, já com 11 fontes acumuladas, bônus já saturado no teto `[4,2,1]`=7). A ocorrência do Stock Titan (onde a SEC foi anexada) não é a vencedora — ganhar visibilidade no score exigiria que ELA fosse a de maior contribuição, o que não é o caso aqui. Mecanismo pré-existente do `build_evolution` (um vencedor por família), não específico de EDGAR — o mesmo aconteceria se uma 12ª notícia comum fosse anexada a essa mesma ocorrência não-vencedora. |
| **Classificação** | **TRUE CORROBORATION** — empresa, família, contraparte (Chart Industries) e Item (2.01, "Completion") todos coerentes com o fato real e público (Baker Hughes concluiu a aquisição da Chart Industries em julho de 2026, amplamente noticiado). |

**FALSE MATCH = 0. AMBIGUOUS = 0.** Gate de precisão do match satisfeito.

### Os 6 candidatos sem match (nível resumo — ver §10 sobre detalhamento não capturado)

O log confirma 6 candidatos EDGAR avaliados que não encontraram ocorrência
correspondente nas fontes normais (nenhum criou registro novo, nenhum
pontuou — comportamento correto por desenho, `apply_edgar_corroboration`
nunca escreve em `events_by_company`). **Limitação registrada**: esta
execução não persistiu o detalhe individual (`sem_match_detalhe`) desses 6
candidatos em arquivo — só o resumo agregado foi impresso no log. Não
justifica um segundo run real só para capturar esse detalhe (o gate exigido
é sobre os MATCHES, não sobre os rejeitados); ver §"Limitações" para a
correção de infraestrutura recomendada (gravar `resumo` completo em JSON) a
aplicar antes de um próximo run, sem necessidade de reabrir esta fase agora.

## 6 (bônus de fonte) — prova explícita

- **Reuters(equiv.)+SEC**: bônus normal do sistema, comprovado nos testes
  [11]/[12]/[12b] (offline) — delta de score da ordem do 1º degrau
  `[4,2,1]`, nunca um peso-base novo.
- **Reuters+SEC 8-K+SEC 8-K/A**: testes [13]/[14]/[15] provam que o dedup
  por `domain="sec.gov"` bloqueia qualquer segunda entrada SEC no mesmo
  registro — `sec.gov` representa **uma única fonte econômica** para fins de
  bônus, qualquer que seja o número de accessions. No run real, isso não foi
  exercitado com dois accessions reais (só 1 filing casou), mas o mecanismo
  é o MESMO código testado offline — não há caminho de execução diferente
  entre o teste e o run real.

## 7. Data / decay

- Confirmado no run real: `pub_ts`/`pub_iso` do registro Stock Titan
  permaneceram `2026-07-27` (idênticos antes/depois — `git diff` do
  registro mostra SOMENTE a adição de `corrob_sources`/`corroborations`,
  nenhum outro campo do registro mudou).
- `filing_date` da SEC (2026-07-16, ANTERIOR à notícia corroborada) não
  alterou `event_date`/decay — confirmado tanto pelo código (`corrob`
  nunca é lido por `decay_weight`) quanto pelo score idêntico antes/depois.
- **Nenhum bloqueador de data encontrado.**

## 8. EDGAR-only

- 6 candidatos EDGAR-only apareceram (nenhuma ocorrência correspondente).
- Confirmado para todos: nenhum peso-base, nenhum score, nenhum
  `events_by_company` novo — por construção (`apply_edgar_corroboration`
  nunca chama `merge_into_history` nem escreve `events_by_company`;
  confirmado pelos testes [7]/[8]/[9]/[10]/[24] e pelo `git diff` do run
  real, que não mostra nenhum registro NOVO em `history["articles"]").
- **Nenhum EDGAR-only alterou score. Sem bloqueador.**

## 9. Invariância

**Alterações esperadas** (ocorreram, só isso):
- Nova fonte SEC em 1 ocorrência existente (Baker Hughes/Chart, Stock Titan).
- Nenhum bônus visível de score neste caso específico (ocorrência não era a
  vencedora — ver §5).
- Campos de link/UI (`display_url`, `link_health`, `link_render_anchor`,
  `link_label`) preenchidos corretamente, prontos para renderizar via
  `all_sources`/`src-row` já existente.

**Alterações proibidas** (nenhuma ocorreu — verificado):
- ✅ Nenhuma ocorrência nova criada (`len(history["articles"])` antes/depois
  idêntico nos registros tocados; nenhum registro novo com `domain="sec.gov"`
  como registro PRINCIPAL).
- ✅ Nenhum peso-base novo (base=40 idêntico antes/depois na decomposição).
- ✅ Nenhuma `event_family` alterada.
- ✅ Nenhuma data econômica alterada.
- ✅ Nenhum decay alterado.
- ✅ Nenhum scoring EDGAR-only.
- ✅ Nenhuma alteração de peso/config econômica (`config_risco.yaml` só tem
  o diff de coleta, nada de pesos/thresholds/tiers/taxonomia/`event_resolution`).

## 27. Suíte de testes

672/676 (os 4 gaps são a MESMA causa única e esperada: 3 checagens
"config de produção com EDGAR desligado" — ver commit `869e66b` — agora
refletem o estado intencional desta branch, não uma regressão de código).
Suíte de corroboração 4H.5 própria: 28/28.

## 28. Arquivos alterados nesta etapa (run real)

Permanecem alterados (commitados): `config_risco.yaml` (diff de 2 flags).
Revertidos após inspeção (não commitados, para não persistir dado de teste
como se fosse produção): `risk_history.json`, `dashboard_risco.html`,
`international_search_history.json`, `out_coverage_diagnosis/*`,
`run_meta.json`. Preservados localmente como evidência (não commitados):
`run_4h5_real.log`, `run_4h5_artifact/risk_history_post_run.json`,
`edgar_runtime_shadow_*.csv` (ver achado abaixo).

## 29. Hashes / invariância de produção

- HEAD pré-run: `869e66b2f1edc9c4cacb183d64e39a6ae2f91a0e`.
- `risk_history.json` e `dashboard_risco.html`: revertidos ao estado
  commitado (`git status` limpo para ambos após o run) — a branch não carrega
  nenhum dado de execução real como se fosse permanente.
- `config_risco.yaml`: hash muda SÓ pelo diff de 2 flags (`869e66b`),
  nenhuma outra chave tocada.
- **`main`/produção real: intocado.** Este run rodou inteiramente na branch
  local, nunca tocou o remoto além do push dos 2 commits de código/config.

## Achado adicional (não bloqueador): bug pré-existente em `edgar_shadow_4h3b.py`

`run_edgar_runtime_shadow` (função DIAGNÓSTICA pré-existente, de fase
anterior, não escrita nesta fase) falhou no run real com
`TypeError: keys must be str, int, float, bool or None, not WindowsPath` —
ao tentar serializar `hashes_antes`/`hashes_depois` em JSON usando um objeto
`Path` (não string) como parte da estrutura. O erro é **capturado** pelo
try/except já existente em `risk_dashboard.py` ("shadow de runtime falhou...
filings seguem FORA do pipeline pontuável") — não interrompeu o run, não
afetou minha corroboração (que roda em bloco try/except separado, DEPOIS
desse). As 214 classificações já tinham sido escritas em CSV antes do erro
(confirmado: `edgar_runtime_shadow_classification.csv` e
`edgar_runtime_shadow_deduplication.csv` têm 215 linhas cada, header + 214
filings). **Não corrigido nesta fase** (fora do escopo do meu módulo, função
puramente diagnóstica sem efeito em produção) — registrado para eventual
correção futura, não bloqueia a decisão desta fase.

## Limitações registradas

1. Detalhe individual dos 6 candidatos EDGAR-only não foi persistido em
   arquivo neste run (só o resumo agregado apareceu no log) — recomendação:
   antes de um próximo run real, adicionar 1 linha em
   `edgar_corroboration_4h5.py` para gravar `resumo` completo (matches +
   `sem_match_detalhe`) em JSON, exatamente como já existe para o shadow
   diagnóstico. Não implementado agora para não gastar um 2º run real só
   para validar essa gravação.
2. Amostra de matches reais é pequena (N=1) — esperado dado que só 32
   emissores são elegíveis a EDGAR e a janela de 90 dias limita a
   sobreposição real com notícias já corroboradas. Não é uma limitação do
   mecanismo, é o tamanho real do universo elegível hoje.
3. O bug pré-existente em `edgar_shadow_4h3b.py` (acima) deveria ser
   corrigido antes de depender do diagnóstico de shadow para auditoria
   contínua — hoje ele falha silenciosamente (capturado) toda vez que rodar
   com coleta ligada.

## 30. Decisão final

**B — Corroboração EDGAR confirmada confiável no run real (1 match real,
100% revisado, classificado TRUE CORROBORATION, zero FALSE MATCH, zero
AMBIGUOUS, zero EDGAR-only pontuando, peso-base/decay/data comprovadamente
invariantes). Pronta para sua revisão antes de qualquer merge.**

Não fazer merge. Não rodar backfill. `edgar_scoring_enabled` permanece
false. Nenhuma família além da corroboração está habilitada — não existe,
nem foi criado, nenhum caminho de scoring autônomo para EDGAR.

**FIM DO RUN REAL 4H.5. PARANDO PARA REVISÃO.**
