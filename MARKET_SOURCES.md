# Fontes de mercado — Trending, Trenches e acompanhamento

Checkpoint CP-SOURCES-01, 09/09/2026. Owner A0, sem subagentes. Escopo: esclarecer as fontes, revisar a semântica e testar o comportamento do código já implantado. Nenhuma ativação de coleta real ou alteração da aplicação.

## O que existe de fato

Trending já está implementado como `market_rank`: `GET /v1/market/rank`, mapeado em `src/midas_gmgn/openapi.py`. Trenches é outro endpoint: `POST /v1/trenches`. O normalizador v4 suporta ambos; a atenção anterior a Trenches se devia à inclusão desse adaptador, não à substituição do Trending.

A base está rodando com fixtures. A nova regressão adicionou duas capturas sintéticas às cinco do checkpoint anterior; todas as sete estão prontas, sem pendências. Nenhum dado de provedor foi coletado nesta revisão. Não declarar cobertura real, frequência ou primeiros segundos capturados a partir desses testes.

## Diferença e função na pesquisa

| Fonte | O que representa | Papel no projeto |
|---|---|---|
| Trending / `market_rank` | Lista ordenada sob uma janela, ordenação e filtros. | Descobrir tokens em destaque e registrar contexto de atividade/entrada no ranking. O caminho para preço e market cap reportado já é suportado. |
| Trenches / `trenches` | Tokens organizados por etapa de lançamento: criação, proximidade de conclusão e concluídos. | Complementar a descoberta precoce e observar transições nos launchpads cobertos. Não garante todos os lançamentos nem o primeiro segundo. |
| `token_info` / acompanhamento por token | Observação individual, independente da permanência em listas. | Compor a continuidade futura do acompanhamento. Preço tem contrato; não inventar market cap ou ATH multiplicando por supply atual sem contrato temporal. |

Referências primárias consultadas em 09/09/2026: [GMGNAI — market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md), seções de comandos, opções e respostas; [GMGN — Trending](https://docs.gmgn.ai/index/trending). URLs mutáveis, sem commit fixado nesta revisão. Foram consultadas como documentação, sem executar CLI nem consumir a API de mercado.

## Semântica de valuation revisada

| Endpoint e caminho do item | Tratamento atual | Limite |
|---|---|---|
| `market_rank.price` | Preço reportado em USD/token, precisão preservada. | Não é cotação executável nem prova de fill. |
| `market_rank.market_cap` | Market cap reportado em USD. | Base de supply não especificada: não renomear para FDV ou capitalização circulante calculada. |
| `market_rank.history_highest_market_cap` | Máximo histórico reportado em USD. | Não informa quando ocorreu nem fornece o contexto daquele pico; observá-lo hoje não reconstrói o snapshot do passado. |
| `trenches.usd_market_cap` | Market cap reportado em USD, métrica própria. | Não presumir equivalência econômica com outros endpoints além do contrato documentado. |
| `trenches.market_cap` e `trenches.price` | Valores preservados como `unverified`. | A documentação consultada não comprovou esses caminhos exatos como aliases USD; continuam excluídos das consultas validadas. |

Volume e contagens do Trending usam a janela solicitada. Um ranking de 24h não é uma curva de market cap de 24h nem um fechamento diário: seus campos instantâneos e agregados precisam conservar os respectivos tempos. Dados de duas fontes não devem ser interpolados, somados ou substituídos apenas porque possuem nomes semelhantes.

## Regras da próxima coleta e análise — ainda não implementadas integralmente

Estas são decisões de desenho para a continuação, não prova de que o coletor esteja ativo:

1. Usar Trending e Trenches como fontes complementares de descoberta. Organizar o acompanhamento por `chain + token_address`, compartilhando observações entre estratégias, com orçamento e prioridade mensuráveis.
2. Preservar endpoint, request completo, chain, janela, ordenação, filtros efetivos, limite e horários. Não equiparar filtros omitidos a consulta sem filtros: a CLI documenta defaults por chain. Na integração futura, registrar o que foi efetivamente enviado e o que permanece desconhecido.
3. Guardar separadamente posição informada pelo provedor, índice na resposta e ordenação aplicada pelo cliente. O ordinal local não substitui rank reportado. Hoje `rank`/`hot_level` permanecem no bruto e catálogo, sem dimensão/fato específico homologado no adaptador.
4. Medir percentuais sobre a população observada de cada configuração de descoberta, com janela de acompanhamento e cobertura. Analisar somente tokens que entraram no Trending condiciona a amostra a esse evento e pode excluir os que falharam antes. Também não tratar os tokens visíveis em Trenches como todos os tokens existentes.
5. Conservar os tokens acompanhados após sua saída de uma lista, sujeito à política de acompanhamento que será definida. Ausência no ranking não significa preço zero, rug, falha de captura ou desaparecimento do token. Polling de listas não prova o instante exato de entrada/saída entre consultas.
6. Relacionar cada ponto da curva a fonte/contrato/tempo/qualidade; tratar lacunas explicitamente. Marcos observados e máximos reportados pelo provedor são evidências diferentes. Não inferir causalidade financeira de uma correlação.

## Regressão executada na base atual

Acrescentado `test_trending_valuation_keeps_context_and_ignores_unverified_trenches` em `data_platform/tests/test_integration.py`.

- Fixture Trending conserva request de 5m com ordenação/filtros, bytes originais, precisão de preço/market cap/máximo histórico, janela de volume e reenvio idempotente.
- Uma fixture posterior do mesmo token em Trenches contém preço/market cap não verificados e discrepantes. Esses campos ficam disponíveis explicitamente como não verificados e não substituem o Trending válido em `/v1/observations` ou `/v1/as-known`.
- As duas capturas exigem confirmação analítica completa em DuckLake e ClickHouse.

Resultado: **1 teste aprovado**, zero falhas/erros/skips, 2.362 s. Teste montado somente para leitura no serviço de verificação; aplicação e imagem operacional não foram reconstruídas. Estado final: sete fixtures, filas e entregas analíticas pendentes zeradas; coleta real ausente. Evidências: [trending.xml](../.scratch/checkpoint-market-sources-20260909/trending.xml), [trending.log](../.scratch/checkpoint-market-sources-20260909/trending.log), [status.json](../.scratch/checkpoint-market-sources-20260909/status.json).

## Próximo checkpoint

Modelar a consulta de contexto de descoberta: fonte, categoria/rank, janela e filtros, reutilizando o request/bruto já preservados. A homologação dos aliases de preço/market cap Trenches continua pendente e não deve impedir uso dos campos Trending já documentados. A ativação da nova origem contínua vem depois desse recorte. Não houve conexão por estratégia, nova fonte, migração ou reprocessamento histórico nesta etapa.

Atualização CP-CONTEXT-01: [contexto individual consultável implantado](OBSERVATION_CONTEXT.md). A rota expõe rank/posição/categoria/janela/filtros e relógios a partir do fato e bruto existentes; não é índice global de descoberta. Próxima etapa: rodada real limitada para validação dos campos.
