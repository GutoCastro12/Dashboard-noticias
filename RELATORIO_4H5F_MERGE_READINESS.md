# FASE 4H.5F — Merge Readiness / Final Hardening

**Recomendação final: READY FOR REVIEW.** Branch tecnicamente limpa, suíte
100% verde, nenhum bloqueador encontrado. **Não fazer merge** — aguardando
sua revisão, como pedido.

---

## 1-2. HEAD / commits / diff total contra `origin/main`

- Branch: `feature/edgar-corroboration-4h5`
- HEAD: `d1a9b4d`
- 4 commits acima de `origin/main` (`afb3d58`):
  1. `ebae571` — mecanismo de corroboração + 28 testes + replay offline
  2. `869e66b` — diff de config (coleta SEC ligada, scoring off)
  3. `a7843a7` — relatório do run real (Decisão B)
  4. `d1a9b4d` — hardening desta fase (testes atualizados + novos, fix Path, fixture portável)
- Diff total: **20 arquivos, 6.481 inserções, 1 deleção**.

## 4-5. Arquivos runtime adicionados / modificados

**Adicionados:**
- `edgar_corroboration_4h5.py` — módulo de corroboração (novo nesta integração)
- `edgar_canonical.py`, `edgar_dom.py`, `edgar_normalizer.py` — motor de
  classificação/parsing canônico, portado da 4H.3C-F (dependências diretas
  do módulo de corroboração)

**Modificados:**
- `risk_dashboard.py` — wiring da chamada de corroboração no CASO B (após
  `merge_into_history`/`resolve_history_urls`); fix pontual do bug `Path`→JSON
  no call site do shadow antigo; telemetria persistida em JSON.
- `config_risco.yaml` — 2 flags (`international_official_sources_enabled`,
  `official_sources.EUA.enabled`), nada mais.

## 6. Testes adicionados/modificados

- **Adicionados**: `test_edgar_corroboration_4h5.py` (28), `test_4h5_merge_readiness.py` (31).
- **Modificados** (premissa antiga → nova, não apagados):
  `test_edgar_4h3c.py`, `test_edgar_4h3d.py`, `test_edgar_4h3e.py`,
  `test_edgar_4h3f.py` — 1 assertion cada, invertida de "coleta EDGAR
  desligada" para "coleta EDGAR LIGADA (invariante desta branch) + scoring
  DESLIGADO".
- **Fixture nova**: `test_fixtures_4h5/baker_hughes_0001193125-26-305477.{html,json}`
  (8-K real, 56KB) — bundlada no repo para os testes rodarem sem corpus
  externo nem rede, em qualquer OS/CI.

## 7. Artifacts/relatórios versionados

`RELATORIO_4H5_RUN_REAL.md` (run real, Decisão B) e este relatório. Scripts
de suporte de teste (`edgar_shadow_4h3c.py`, `edgar_sections.py`,
`edgar_gold_4h3f.py`) e o script de análise ad-hoc `edgar_replay_4h5.py` —
ver classificação completa em §10.

## 8-9. Config final / blast-radius

Diff exato contra `origin/main` (9 linhas, confirmado — nenhuma outra linha
do arquivo mudou):
```diff
+international_official_sources_enabled: true
 official_sources:
   Brasil: ...
   EUA:
+    enabled: true
     regulador: SEC
```
Confirmado: nenhum peso, threshold, tier, taxonomia, `event_resolution` ou
bônus `[4,2,1]` alterado — o diff inteiro é essas 2 chaves + o comentário
explicativo.

`edgar_scoring_enabled` continua **ausente** do config (não foi adicionado
"por estética"). Resolução no código:
```python
def edgar_scoring_enabled(cfg: dict) -> bool:
    return (edgar_collection_enabled(cfg)
            and cfg.get("edgar_scoring_enabled", False) is True)
```
`cfg.get("edgar_scoring_enabled", False)` — chave ausente → `False` →
`False is True` → `False`. Confirmado em runtime: `edgar_collection_enabled=True`,
`edgar_scoring_enabled=False`.

**Blast radius agora protegido por teste** (`test_4h5_merge_readiness.py §4`,
7 checagens), não só por inspeção manual: confirma que as 2 flags ligam
exclusivamente SEC/EDGAR — nenhum dos 15 outros países em `official_sources`
tem `enabled=true`, `edgar_collection_enabled()` só lê `official_sources["EUA"]`
no código-fonte (verificado via introspecção do próprio código, não só do
config), `international_official_sources_enabled` aparece só 2× no módulo
inteiro (definição + log), e `fetch_cvm_fatos` (coletor real do Brasil) não
lê `official_sources` — sem acoplamento possível.

## 10. Resultado end-to-end CASO A (sem match) / CASO B (com match)

Contrato agora protegido por teste explícito (`§5`, 9 checagens), com o
MESMO 8-K real usado no run real (Baker Hughes/Chart Industries):

- **CASO A** (histórico vazio, nada para casar): candidatos avaliados > 0
  (coleta/classificação ocorreu), `corroborados=0`, **nenhum registro novo**
  em `history["articles"]`, Baker Hughes não aparece pontuando no
  `build_evolution` real.
- **CASO B** (ocorrência existente compatível): SEC entra em corroboração,
  continua sendo **1 ocorrência**, peso-base é o único da taxonomia (não
  duplicado), `sources=2` no breakdown (mecanismo normal de bônus, Reuters-
  equivalente + SEC).

## 11-13. Dedup SEC / idempotência

- **Reuters+SEC 8-K+SEC 8-K/A**: só a 1ª entrada SEC é aceita — `corrob_sources`
  fica com exatamente 1 entrada, não 2. Confirmado também que o `amendment`
  não gera bônus extra (`sources` permanece 2, não 3).
- **Item 1.01 + Item 2.03 do mesmo filing**: não viram 2 corroborações
  independentes (mesmo mecanismo de dedup por domínio `sec.gov`).
- **Reprocessamento 2×** (`apply_edgar_corroboration` chamado duas vezes
  sobre o mesmo histórico): mesmo número de ocorrências (1), mesmo número
  de fontes econômicas (1 SEC), mesmo score, 2ª passada não conta novo
  bônus (`corroborados=0` na repetição).

## 14-15. Event date invariance / source bonus invariance

Confirmado no run real (§5/§7 do relatório anterior, preservado): `pub_ts`
do registro corroborado permanece idêntico; `filing_date` da SEC (anterior
à notícia) não altera `event_date`/decay. Bônus: prova de que o mecanismo
normal `[4,2,1]` (nunca alterado) é o único caminho de score adicional —
nenhum multiplicador/bônus novo criado nesta integração.

## 16. UI/link

`§8` (4 checagens): a entrada SEC chega até `all_sources` (o que o
template renderiza), com `href` apontando para a URL real do accession
(`https://www.sec.gov/Archives/...`, nunca homepage/search), `render_anchor=True`
(gera `<a>` clicável) e rótulo com o Item (`"SEC · 8-K · Item 2.01"`).
Nenhuma mudança de template foi necessária — `link_fields`/`all_sources`/
`src-row` já existiam prontos para múltiplas fontes.

## 18. Estado do bug `Path`

Avaliação objetiva feita conforme pedido: `run_edgar_runtime_shadow` roda
automaticamente em **todo** run real com `edgar_collection_enabled=True` —
e essa agora é a invariante deliberada desta branch (collection sempre
ligada), não uma exceção pontual. Logo, o bug **rodaria em todo run futuro**
e foi corrigido com a menor mudança mecânica possível: `watch_files=[str(history_path), ...]`
no call site de `risk_dashboard.py` (1 linha), sem tocar `edgar_shadow_4h3b.py`
em si. Regressão coberta em `§9` (5 checagens): confirma que `watch_files`
todo em string não quebra mais, e que o padrão antigo (`Path` cru) É
comprovadamente a causa raiz (reproduzido isoladamente).

## 19. Dependências

- Nenhuma dependência Python nova: `edgar_canonical`/`edgar_dom`/`edgar_normalizer`
  usam só stdlib (confirmado — zero `import` não-stdlib/não-edgar nesses 3
  arquivos). `edgar_corroboration_4h5.py` usa `requests`, já dependência
  existente de `risk_dashboard.py` (usado por `fetch_edgar_filings` e outros
  coletores) — não é pacote novo.
- Nenhum pacote fora do ambiente do Actions (mesma lista de dependências de
  antes desta fase).

## 20. Busca por caminhos locais

Busca feita em todos os arquivos runtime/teste da integração. **Achado real,
corrigido nesta fase**: `test_edgar_corroboration_4h5.py` e
`test_4h5_merge_readiness.py` dependiam de `C:\Users\Gustavo\DashRisk-corpus-4h4-html`
(corpus externo desta máquina) — **não rodariam em GitHub Actions (Linux)**.
Corrigido: a única fixture realmente usada (Baker Hughes/Chart Industries,
8-K real) foi bundlada em `test_fixtures_4h5/` (56KB), e ambos os arquivos
agora carregam dela via caminho relativo (`Path(__file__).parent`), sem
corpus externo, sem rede, portável para qualquer OS.

Achado **pré-existente, não tocado** (fora do escopo desta integração):
`test_edgar_4h3d.py` referencia `C:\Users\Gustavo\DashRisk-corpus-4h3c` para
um enriquecimento OPCIONAL de dados — já protegido por `if CORPUS.exists()...`,
portanto já seguro em CI (só pula o enriquecimento extra, não quebra). Não
alterado, por já ser seguro e por estar fora do escopo desta fase (4H.3D,
não 4H.5).

`edgar_replay_4h5.py` usa caminho local mas é script de análise ad-hoc, não
faz parte da suíte de testes nem é importado por nada — ver classificação
em §10 abaixo (grupo C).

## 21. Telemetria

Já respondia (via log): filings coletados (`fetch_edgar_filings`'s próprio
print), candidatos avaliados, corroborações novas, sem-match. **Contador
simples adicionado** (não framework novo): (a) filings sem corpo recuperado
(erro de coleta/parsing, derivado de `filings_recebidos - filings_com_corpo`)
agora aparece no log; (b) o `resumo` completo (matches + `sem_match_detalhe`,
com accession/item/motivo por candidato) passa a ser persistido em
`edgar_corroboration_4h5_resumo.json` a cada run com candidatos avaliados —
resolve a limitação que eu tinha registrado no relatório do run real (só o
agregado sobrevivia ao log).

## Seção 10 do pedido — separação runtime / testes / evidência

**A. Necessário em runtime** (importado pelo caminho real de produção,
confirmado via grep de `import`, não por suposição):
`risk_dashboard.py`, `config_risco.yaml`, `edgar_corroboration_4h5.py`,
`edgar_canonical.py`, `edgar_dom.py`, `edgar_normalizer.py`. `edgar_shadow_4h3b.py`
já existia em `origin/main` antes desta fase (não portado agora), importado
por `classify_and_attribute` e pelo CASO B.

**B. Testes permanentes** (devem acompanhar a integração):
`test_edgar_4h3c.py`, `test_edgar_4h3d.py`, `test_edgar_4h3e.py`,
`test_edgar_4h3f.py` (herdados, premissa atualizada), `test_edgar_corroboration_4h5.py`,
`test_4h5_merge_readiness.py`, `test_fixtures_4h5/`. Suporte de teste (não
runtime, mas necessário para os testes acima rodarem): `edgar_sections.py`
(usado só por `test_edgar_4h3e.py` e por `edgar_shadow_4h3c.py`),
`edgar_shadow_4h3c.py` (orquestrador OFFLINE usado só pelos testes 4H.3C/D/F
— **confirmado que `risk_dashboard.py` nunca o importa**, só os testes).

**C. Evidência experimental** (não faz parte do caminho operacional):
`edgar_gold_4h3f.py` — **achado**: está órfão, nada o importa (nem testes,
nem runtime); portado desnecessariamente da branch histórica. Não removido
nesta fase (não é bloqueador, remoção seria escopo novo) — registrado como
dívida técnica menor, candidato a remoção numa limpeza futura.
`edgar_replay_4h5.py` — script de análise ad-hoc do replay offline, usa
caminho local, não é suíte formal nem é importado por nada — mantido como
evidência do processo, não roda em CI. `RELATORIO_4H5_RUN_REAL.md`, este
relatório.

**Confirmado**: nenhum código experimental (4H.4/4H.4B — que nem sequer
estão nesta branch, ficaram isolados em `feature/edgar-scoring-qualification`
desde o início — nem `edgar_gold_4h3f.py`/`edgar_replay_4h5.py`) é importado
pelo runtime de produção.

## §13/§14 do pedido — interpretação estatística e resultado 4H.4/4H.4B preservados

Registrado explicitamente, sem reinterpretação: o run real produziu **1 TRUE
CORROBORATION, 0 FALSE MATCH** — isso prova o **funcionamento fim-a-fim do
mecanismo em dado real**, não uma precisão estatística de 100%. A confiança
na integração vem da combinação de arquitetura conservadora (match exige
empresa+família+contraparte+data, nunca só proximidade), 28+31 testes,
replay offline, o run real, e o fail-safe explícito para não-match (nunca
cria ocorrência, nunca pontua). **A decisão da 4H.4/4H.4B continua válida e
não foi reaberta**: EDGAR não está qualificado para originar sozinho eventos
pontuáveis em nenhuma família. O Blind Holdout dessa fase permanece intacto,
na branch `feature/edgar-scoring-qualification`, não tocado por esta fase.

## 22-25. Suíte final completa

| Suíte | N |
|---|---:|
| Semântica | 196 |
| Links | 94 |
| Atribuição | 75 |
| CVM fixture | 11 |
| Cobertura de fundos | 10 |
| 4H.3C | 137 |
| 4H.3D | 50 |
| 4H.3E | 35 |
| 4H.3F | 40 |
| 4H.5 corroboração | 28 |
| 4H.5F merge-readiness | 31 |
| **TOTAL** | **707** |

**707/707 — 100% verde. Zero falhas. Zero skip novo. Zero xfail.**

## 25 (pedido) — nenhuma chamada de rede nesta etapa

Confirmado: toda a suíte acima roda sem rede (fixture bundlada substitui o
corpus externo; nenhum teste depende de `requests`/SEC/notícias ao vivo).
Nenhum novo run real foi executado — o run real da 4H.5 já cumpriu seu
objetivo e não foi repetido.

## 26-27. Backfill / merge

Nenhum backfill rodado. Nenhum merge feito.

## 28. Limitações conhecidas

1. `edgar_gold_4h3f.py` órfão (nenhum importador) — dívida técnica menor,
   candidato a remoção futura, não bloqueador.
2. `edgar_shadow_4h3b.py` (tooling diagnóstico legado) não foi revisado além
   do fix pontual do call site — se um dia parar de ser só diagnóstico,
   merece auditoria própria (fora do escopo desta fase).
3. Telemetria persistida (`edgar_corroboration_4h5_resumo.json`) é nova
   nesta fase e não tem teste unitário dedicado (é um `write_text` simples,
   dentro de try/except, baixo risco) — coberta indiretamente pela
   correção em si ter sido validada por compilação e pela suíte completa
   permanecer verde.
4. Amostra real de corroboração continua pequena (N=1, do run anterior) —
   não é limitação desta fase de hardening, é o tamanho real do universo
   elegível hoje (32 emissores EDGAR).

## 29. Riscos residuais

- Nenhum risco de blast radius (protegido por teste, não só por inspeção).
- Nenhum risco de regressão de CI (fixture portátil, sem rede, sem caminho
  local nos arquivos runtime/teste da integração).
- Risco operacional conhecido e aceito: com `edgar_collection_enabled=true`
  permanente, `fetch_edgar_filings` fará chamadas reais à SEC em todo run
  de produção, se esta branch for mergeada — consistente com a arquitetura
  aprovada (§1 da sua mensagem), não uma surpresa.
- Nenhum risco de scoring autônomo — `edgar_scoring_enabled` não existe em
  nenhum caminho de código novo desta fase, só é lido (nunca escrito) pelo
  mecanismo existente.

## 30. Recomendação final

**READY FOR REVIEW.**

Branch `feature/edgar-corroboration-4h5` (`d1a9b4d`) tecnicamente limpa:
suíte 707/707, blast radius protegido por teste, contrato CASO A/CASO B
protegido por teste, dedup e idempotência comprovados, UI/link comprovado,
bug pré-existente corrigido no ponto mínimo necessário, zero dependência
nova, zero caminho Windows nos arquivos runtime/teste da integração, zero
rede nesta etapa, zero backfill.

**Não fiz merge. Não rodei backfill. Não rodei novo shadow. Não reabri
scoring autônomo do EDGAR.**

**FIM DA FASE 4H.5F. PARANDO PARA REVISÃO.**
