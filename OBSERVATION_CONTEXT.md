# Checkpoint CP-CONTEXT-01 — contexto por observação

Verificado em 2026-09-09T18:34:55.460690+00:00. Owner A0, sem subagentes. Implementado, testado e implantado no projeto `midas-data-base`.

## Consulta disponível

`GET /v1/observations/{fact_id}/context?source_kind=fixture&knowledge_cutoff=<UTC ISO8601>`

Usa a autenticação Bearer existente. Obtenha o `fact_id` em `/v1/observations` ou `/v1/as-known`; reutilize o mesmo corte de conhecimento. Origem obrigatória: `fixture`, `gmgn` ou `legacy_gmgn`. A consulta retorna a versão exata do fato identificado, não escolhe automaticamente a última versão de todo o token.

A resposta inclui o fato original, sem `numeric_value`, e contexto versionado `gmgn-observation-context-v1`:

| Bloco | Conteúdo |
|---|---|
| `source` | Endpoint, origem, tipo de evidência, ID da captura, checksum do bruto e status HTTP. |
| `entity` | Chain/token, JSON Pointer do item, índice zero-based, posição um-based, categoria exata, etapa de lançamento e rank informado. |
| `request` | Parâmetros preservados, janela, ordenação, direção, limite e blocos de filtros/ranges com seus caminhos. |
| `clocks` | Requisição, recepção, evento informado, registro da captura, tempo do fato e disponibilidade. Fonte e base temporal permanecem explícitas. |

Rank reportado não é índice da lista. Na prova real da API, o token está na posição 1 da resposta e tem rank reportado 12; ambos foram preservados. Rank é serializado como texto para não perder precisão no consumidor. Ausente, null e inválido são estados diferentes; nenhum deles vira posição 1 automaticamente.

Trenches preserva `pump` ou `near_completion` no pointer/categoria original e expõe a etapa `near_completion` separadamente. Só os parâmetros de categoria correspondentes ao item são selecionados. O restante do request continua disponível integralmente.

Filtros são os registrados no request, sem inventar defaults do provedor ou declarar ausência de filtro quando a configuração é desconhecida. Parâmetros conflitantes conservam os caminhos e valor resolvido null. Janela de ranking inválida ou ausente não recebe um padrão.

## Custo e integridade

- Consulta PostgreSQL por `fact_id` e join com a captura, sem nova tabela, índice ou migração. Usa a barreira temporal existente, origem obrigatória e corte de publicação.
- Decodificação de somente um bruto por consulta, com SHA256 conferido e limites estruturais do normalizador. Identidade e pointer do item precisam corresponder ao fato.
- Leitura/decodificação fora do event loop e até duas requisições de contexto admitidas simultaneamente por processo da API; ocupação devolve 429/Retry-After. Esta configuração não é um benchmark de carga.
- Sem duplicar os parâmetros em todos os fatos e sem reprocessar o histórico. Metadados e bruto existentes atendem às capturas antigas compatíveis.
- Origem incorreta, ID ausente ou fato ainda indisponível no corte: 404. Sem autenticação: 401. Corte futuro/sem timezone: 422. Evidência ausente, corrompida ou sem estrutura verificável: 503 sanitizado; não inventar contexto.

Escopo entregue: consulta individual para inspecionar uma observação. Busca global/coortes por rank, categoria ou filtros e contexto para tokens sem nenhum fato normalizado não foram implementados. Não afirmar cobertura de todos os tokens apenas por esta rota existir.

## Alterações e implantação

- Novo `midas_data/observation_context.py`, método pontual em `store.py`, rota em `api.py`.
- Testes em `tests/test_observation_context.py` e `tests/test_integration.py`.
- Imagem `midas-data-app:context-v1-20260909`, identidade `sha256:fa90220a9adb90d5f9e4816d159d5e9167e12a35c8d694ba4cfe0e08c2be5631`.
- API e worker recriados na nova imagem; PostgreSQL, NATS, ClickHouse e volumes preservados. Normalizador continua `gmgn-p0-v4`; nenhuma mudança nas identidades dos fatos ou nos contratos econômicos.
- `.data/gmgn-data/base-runtime.env` recebeu somente a nova tag da aplicação. Nenhuma credencial impressa. Coleta real/estratégias continuam desligadas.

## Provas e estado final

- **26 testes focados** aprovados (0,804 s); **quatro integrações** aprovadas (10,673 s), zero falhas/erros/skips nas suítes executadas.
- Integrações verificaram contexto Trending/Trenches, rank versus posição, precisão, filtros, fronteira temporal, isolamento de origem e autenticação; também repetiram três provas existentes de bruto/recibos/valuation.
- Releitura posterior, somente leitura, conferiu **15 capturas, 15 lotes e 34 fatos**, SHA256 dos brutos e checksums/recibos nos dois destinos. Isso inclui as sete fixtures anteriores, preservadas, e oito novas dos testes.
- Cinco serviços `running/healthy`; publicação, normalização e destinos sem pendências. Apenas `source_kind=fixture`; nenhuma ponte ativa.

Evidências: [focused.xml](../.scratch/checkpoint-context-20260909/focused.xml), [integrated.xml](../.scratch/checkpoint-context-20260909/integrated.xml), [resultado operacional](../.scratch/checkpoint-context-20260909/result.json), [exemplo real da resposta com dados sintéticos](../.scratch/checkpoint-context-20260909/example.json), [build](../.scratch/checkpoint-context-20260909/build.log), [implantação](../.scratch/checkpoint-context-20260909/deploy.log).

## Próximo recorte

Preparar a nova origem e executar uma rodada real limitada de Trending, Trenches e token info, conferindo os campos pendentes contra o bruto e o contexto retornado. Sem iniciar ainda coleta contínua, estratégias ou análise em escala. A comprovação deste checkpoint é funcional, com fixtures; capacidade sustentada permanece pendente.
