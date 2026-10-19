# Checkpoint — base limpa v4 em operação

Verificado em 2026-09-09T16:54:46.5917197Z. Owner A0, sem agentes adicionais. Escopo solicitado: avanço curto até uma base funcional, sem coleta real, estratégia ou refatoração adicional.

## Entrega e estado verificado

- Docker inicialmente sem containers e volumes; C: com 399.582.720.000 bytes livres (372,1 GiB). Esta verificação pós-reset substitui a pendência de inventário anterior, mas não determina qual opção de limpeza foi usada nem o espaço recuperado exclusivamente por cada ação.
- Construída a imagem `midas-data-app:trajectory-v4-20260909` a partir do código atual. Identidade retornada por Docker: `sha256:a5a2ab3e0f6ddbc97ca635e585a697757e3447e0ab36dcbc4e6b76a7289f06d5`. Normalizador verificado dentro da API: `gmgn-p0-v4`.
- Projeto `midas-data-base` provisionado em cinco volumes novos. PostgreSQL, NATS, ClickHouse, API e worker estão `running/healthy`; DuckLake/Parquet são operados pelo worker com catálogo PostgreSQL.
- API local: `http://127.0.0.1:8791`. Configuração privada existente preservada; somente `DATA_APP_IMAGE` atualizado em `.data/gmgn-data/base-runtime.env`. Nenhum segredo impresso. Nenhuma mudança de código de aplicação ou dependência.
- Base deixada em execução com política `unless-stopped`. Nenhuma ponte/coleta, executor ou serviço de outro projeto iniciado.

## Teste focado na base implantada

Três testes existentes passaram, zero falhas/erros/skips; tempo JUnit 6.803 s:

1. Bruto exato, SHA256, precisão decimal, relógios e recibos coincidentes em DuckLake/ClickHouse.
2. Trenches e janelas do ranking em todo o fluxo, incluindo reenvio sem efeito duplicado e separação de market cap verificado/não verificado.
3. Dados de teste excluídos das consultas de origem real.

Os testes geraram cinco capturas `source_kind=fixture`; todas normalizadas e confirmadas nos dois destinos. Elas permanecem explicitamente identificadas na base. Nenhuma chamada à GMGN foi necessária.

Estado final: outbox pendente 0, mensagens não publicadas 0, entregas analíticas pendentes 0; erros das filas Lake/ClickHouse 0. `accepting`, `worker_live`, `processing_ready` e `processing_current` verdadeiros. Espaço livre final no C: 396,820,365,312 bytes (369.6 GiB).

Evidências: [smoke.xml](../.scratch/checkpoint-v4-20260909/smoke.xml), [status.json](../.scratch/checkpoint-v4-20260909/status.json), [runtime.json](../.scratch/checkpoint-v4-20260909/runtime.json), [containers.jsonl](../.scratch/checkpoint-v4-20260909/containers.jsonl), [build.log](../.scratch/checkpoint-v4-20260909/build.log) e [startup.log](../.scratch/checkpoint-v4-20260909/startup.log). Os horários canônicos estão em UTC; o arquivo bruto status.json preserva a resposta antes de conversão de datas pelo PowerShell.

## Limite e próximo checkpoint

Este é um smoke real do Docker recém-implantado com entradas sintéticas, não benchmark, coleta viva, nova suíte completa ou prova de capacidade sustentada. A suíte integrada anterior de 503 testes permanece histórica. Nenhuma observação de 60 minutos foi reintroduzida. O timeout antigo não reproduzido continua pendente.

Próximo recorte: homologar os caminhos exatos de preço/market cap de Trenches, mantendo valores sem comprovação fora da curva validada. Dimensões consultáveis de contexto/tempo, custo do catálogo, nova origem contínua, curvas/marcos, X e estratégias continuam pendentes. Encerrada a execução neste checkpoint, sem iniciar esses itens.
