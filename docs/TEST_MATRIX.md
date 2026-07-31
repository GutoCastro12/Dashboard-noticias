# TEST_MATRIX.md — Matriz de testes do Radar de Risco

Todos os resultados abaixo marcados "rodado nesta tarefa" foram executados de fato,
em 2026-07-31, com `C:\Users\User\AppData\Local\Programs\Python\Python310\python` e
`PYTHONIOENCODING=utf-8` (necessário no Windows; sem essa variável, o console cp1252
quebra ao imprimir ✅/→/ção, mas isso é um problema de encoding do terminal, não do teste
— confirmado comparando exit code 0 em ambos os casos e o traceback sendo
`UnicodeEncodeError` na própria chamada de `print`, não uma falha de asserção).

| Teste | Comando | Escopo | Rede? | Escreve arquivos? | Resultado atual | Evidência | Obrigatório antes de merge? |
|---|---|---|---|---|---|---|---|
| Compilação | `python -m py_compile risk_dashboard.py semantic_audit.py link_debt_audit.py edgar_audit_4h2.py edgar_shadow_4h3a.py edgar_shadow_4h3b.py test_semantica.py test_edgar_4h2.py test_edgar_4h3a.py test_edgar_4h3b.py test_links.py` | Sintaxe de todos os módulos principais | Não | Não (gera `.pyc` em cache) | **Exit 0** — rodado nesta tarefa | [REPO] | Sim |
| Semântica | `python test_semantica.py` | 20 testes unitários + ~82 de integração (`i01`-`i20`) do motor `semantic_audit.py` | Não | Não | **102/102** — rodado nesta tarefa | [REPO] | Sim |
| Links | `python test_links.py` | 92 testes de classificação/resolução de URL, com `MockSession` simulando `batchexecute` | Não (mocks) | Não | **92/92** — rodado nesta tarefa. Este arquivo **não existia** no clone local antes do `git fetch`; existe em `main`@`75bd706` | [REPO] | Sim |
| EDGAR 4H.2 | `python test_edgar_4h2.py` | Causa raiz do bug `formularios_gatilho` (string→lista) | Não | Não | **23/23** — rodado nesta tarefa | [REPO] | Sim |
| EDGAR 4H.3A | `python test_edgar_4h3a.py` | Matriz AND das flags mestre/fonte | Não | Não | **48/48** — rodado nesta tarefa | [REPO] | Sim |
| EDGAR 4H.3B | `python test_edgar_4h3b.py` | Gate real de shadow mode + atribuição por evidência | Não | Não | **59/59** — rodado nesta tarefa | [REPO] | Sim |
| EDGAR 4H.3C | `python test_edgar_4h3c.py` | Parser canônico (`edgar_canonical.py`) | — | — | **Arquivo não existe no repositório** — mencionado nos relatórios como entregue, nunca confirmado como aplicado | [REPO] Glob negativo | N/A (não pode rodar) |
| Atribuição empresa×evento | `python risk_dashboard.py --test-attribution` | Suíte principal: contexto vs. sujeito, famílias semânticas, clusterização (Engie), papel semântico (CVS/JPMorgan/Ford/Vale-Samarco), telemetria | Não | Não | **75/75** — rodado nesta tarefa | [REPO] | Sim |
| Fixture CVM | `python risk_dashboard.py --test-cvm-fixture` | Confiança de match por comprimento do termo (BRF/TIM) | Não (fixture local) | Não | **11/11** — rodado nesta tarefa | [REPO] | Sim |
| Cobertura de fundos | `python risk_dashboard.py --test-fund-coverage` | Taxonomia de Gestoras/Fundos, `applies_to`, `scoring_mode` | Não | Não | **10/10** — rodado nesta tarefa | [REPO] | Sim |
| Validação YAML dos workflows | `python -c "import yaml; yaml.safe_load(open(p))"` para os 3 arquivos em `.github/workflows/` | Sintaxe YAML | Não | Não | Não rodado explicitamente nesta tarefa (os workflows rodaram com sucesso real no Actions, o que já prova YAML válido) | [ACTIONS] | Recomendado, não crítico |
| Validação de HTML | Nenhum comando dedicado encontrado no repositório | — | — | — | **Não existe teste automatizado de validação de HTML** — validação visual manual é o único mecanismo relatado (Report-2/3) | [RELATORIO-02][RELATORIO-03] | Não obrigatório hoje (lacuna) |
| Invariância de score | Padrão de hash antes/depois embutido em `workflow_link_audit.yml` (`hashes_antes.txt` + `git diff --exit-code`) | Reparo de links não deve alterar score/histórico fora de campos de link | Depende do modo (repair usa rede) | Sim, mas só arquivos de link | Confirmado no workflow real (arquivo lido, mecanismo presente) | [REPO] | Sim, para qualquer PR que toque `--repair-links-only`/`--reclassify-semantic-only` |
| Invariância semântica | Nenhum comando dedicado de "antes/depois" isolado encontrado além do padrão de hash do workflow de links | — | — | — | Não verificado isoladamente para reclassificação semântica nesta tarefa | [NAO-VERIFICADO] | Recomendado — ver backlog |
| Idempotência (links) | Casos `[v16]` dentro de `test_links.py` ("Idempotência também para corroboradoras resolvidas por batchexecute") | Parte da suíte de links | Não (mock) | Não | Incluído nos 92/92 acima | [REPO] | Sim |
| Execução em pacote limpo | Mencionado nos relatórios (`py_compile` + suítes depois de extrair ZIP em diretório limpo) | — | — | — | Não repetido nesta tarefa (o repositório já é o "pacote limpo" — clone real pós-fetch) | [RELATORIO-03] | Não aplicável hoje |
| Testes reais em Actions | `gh run view <id> --log` para os runs de produção | Prova que o pipeline roda de ponta a ponta com rede real | Sim (já executado pelo Actions) | Sim (commit real) | **Confirmado**: run `30607645995` (links, 1045/453/31), run `30629264808` e `30607780196` (Update Risk Dashboard, `success`) | [ACTIONS] | Sim, como evidência complementar (não substitui os testes locais) |

## Observações importantes

- **Não copiar contagens antigas dos relatórios de chat.** Todos os números acima foram
  recontados nesta tarefa contra o estado real do repositório (`main`@`75bd706`, pós
  `git fetch`). Onde os relatórios de chat citavam números diferentes (ex.: suíte de
  atribuição com 62→68→71→75 casos ao longo do tempo), o número correto e atual é o que
  está na tabela.
- **Nenhum teste com rede real do EDGAR ou de link resolution foi executado nesta
  tarefa** — apenas os testes com mocks/fixtures locais. Evidência de rede real vem
  exclusivamente dos runs do GitHub Actions já concluídos (linha "Testes reais em
  Actions").
- **`test_links.py` é um achado positivo desta auditoria**: relatórios anteriores (em
  especial Report-3) descreviam a v2 do resolvedor de links como "entregue mas nunca
  confirmada" — o arquivo de teste e o resultado real em produção confirmam que já foi
  aplicada e validada com rede real.
