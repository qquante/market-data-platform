# Holders: persistência, consultas e agenda

> Estado vigente em 11/09/2026: implantado e homologado na base real, CP-RESEARCH-UI-LIVE-02. [Aceite live, comandos e limites](LIVE_ACCEPTANCE.md). As referências abaixo a implantação pendente/preview registram o checkpoint local anterior.

Checkpoint CP-HOLDERS-STORE-01, 11/09/2026. Código implementado e testado; implantação ainda não realizada.

- Parser `gmgn-holders-v2`: envelope original `code=0/data/list`, catálogo com pointers originais, validação de parâmetros aninhados e identidade.
- `HolderStore` + migration 016: catálogo, observações por carteira, interpretação e consumo da fila publicados na mesma transação. Nenhum holder entra em `Fact` de token. PostgreSQL guarda a projeção de holders; recibos analíticos de zero fatos não significam projeção de holders no ClickHouse/DuckLake.
- GET `/v1/holders`: source/chain/token, janela até 31 dias, cutoff obrigatório, página até 200 snapshots. GET `/v1/holders/{capture_id}`: fonte/cutoff, até 100 linhas, 2 MiB por página. Lista parcial, vazia válida e quarentena são estados distintos.
- `FamilySchedule`: opt-in `DATA_RESEARCH_ENDPOINTS=token_top_holders`, um slot por minuto, peso 5 reservado no mesmo orçamento; somente tokens ativos do cadastro que já receberam token_info. Até 100 jobs retidos; raw pendente não é descartado. Vencimento, falha, atraso de ACK e interrupção preservam retomada.
- Imagens iniciais do checkpoint: `midas-data-app:holders-v2-20260911` e `midas-data-collector:holders-v1-20260911`. Foram substituídas na entrega local pelas imagens `midas-data-app:research-ui-v1-20260911` e `midas-data-collector:research-schedule-v1-20260911`, que incluem pesquisa, painel e as correções finais; nenhuma foi implantada.
- Contrato HTTP/persistência atual: [gmgn-holders-http-v2.json](contracts/gmgn-holders-http-v2.json). O JSON v1 registra o checkpoint histórico do parser CLI.

## Evidência executada

429 testes passaram: 368 contratos/parser/regressão, 3 testes PostgreSQL em schemas privados, 58 de coletor/agenda. Python 3.13. XMLs em `.scratch/checkpoint-holders-store-20260910`. A primeira chamada da suíte do coletor falhou na criação de temp por montar o workspace read-only; reexecutada com `--basetemp=/tmp/pytest-family`, 58/58 aprovados.

Uma chamada read-only real em 2026-09-11T03:01:25Z, HTTP 200, 23.426 bytes e dez holders, hash `ae8946b245115999a3532d58a84c9d313e0a7759acf7078598a82033682f1bb4`. Bytes, metadados originais e headers permitidos salvos. Parser: dez observações, 902 campos, zero issues. Nenhuma ingestão dessa captura no runtime ainda.

Probe pré-implantação em 03:12:45Z: zero regras/bindings/posições abertas; hashes dos ledgers preservados como baseline. Não confundir este snapshot com verificação posterior.

## Implantação pendente

A revisão automática rejeitou a alteração persistente de configuração e o reinício de API/worker por envolver coleta externa contínua e interrupção sem autorização explícita de implantação nesse ambiente. Foi solicitada ao usuário autorização específica para API, worker e coletor com holders limitado a uma consulta/minuto. **Não repetir nem contornar essa operação até a resposta.** O comando foi rejeitado antes de executar; imagens foram construídas, configuração/serviços não foram alterados por esta rodada.

Após autorização: aplicar imagem/migration aditiva, habilitar somente holders, ingerir o canário salvo com seus metadados verdadeiros, conferir ACK/consulta/catálogo, coletar novos snapshots, reiniciar worker/coletor e comparar ledgers/proveniência/quota/frescor. Critério 6.1.4 live permanece aberto.

Fonte oficial do envelope/rota: [cliente GMGN no commit fixado](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/src/client/OpenApiClient.ts), corroborado pelo HTTP real salvo.
