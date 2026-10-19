# Admissão de capturas — DB-09.1, 09/09/2026

Primeira etapa implantada: a entrada de bruto e manifesto deixou de depender da contagem de trabalho analítico pendente. A barreira de 500 pendências e o orçamento cumulativo de 32 GiB foram retirados. Capacidade sustentada continua pendente.

## Comportamento atual

- A ponte consulta GET `/v1/admission` autenticado antes de outra página. A API verifica espaço antes de aceitar uma captura nova; um objeto novo também verifica o tamanho comprimido antes de gravar.
- A referência é o menor espaço livre entre o filesystem de bruto e o filesystem físico do host configurado. `DATA_RAW_MIN_FREE_BYTES` define uma reserva de 4 GiB por padrão, mais o espaço da próxima captura. Esse padrão é configurável e não foi dimensionado como SLA.
- No host observado, Docker informava aproximadamente 686 GB livres, enquanto C: tinha aproximadamente 48 GB. O VHD Docker está em C:. O bind somente leitura `./.data/gmgn-data/host-space-probe:/var/lib/midas/host-space:ro`, configurado por `DATA_STORAGE_HOST_PATH`, permite medir esse volume físico. Em outro host, a sonda precisa representar o disco físico correspondente.
- Sob pressão conhecida de armazenamento, a API retorna HTTP 503, `storage_backpressure` e `Retry-After: 5`. A ponte pausa sem promover a página; retoma ao recuperar espaço. Falha de leitura da sonda não é tratada como autorização para capturar.
- Replay exato de uma captura já aceita continua permitido para reconciliar ACK incerto. Uma captura nova reutilizando o mesmo bruto continua sujeita ao guard. IDs, hashes, ledger e origem do checkpoint foram preservados.
- O worker também consulta a pressão antes de iniciar outra iteração; conserva os grupos limitados existentes. Pausa recente por armazenamento é saudável para liveness; no bridge, `ready_for_capture` permanece falso. Erro ou heartbeat antigo não recebe essa classificação.
- `accepted_raw_bytes` continua como contador de auditoria, sem controlar admissão. `DATA_BRIDGE_RAW_BUDGET` deixou de ser usado. Limites por payload, página, grupo, memória, SQL e JetStream permanecem.

O espaço livre é uma amostra, não uma reserva atômica contra escritores concorrentes. A proteção não elimina crescimento físico, não apaga dados, não implementa retenção e não garante capacidade infinita. Medição via [shutil.disk_usage](https://docs.python.org/3.13/library/shutil.html#shutil.disk_usage); capacidade e retenção do stream continuam conceitos separados, conforme [JetStream](https://docs.nats.io/learn/jetstream/your-first-stream).

## Validação concluída

- Suíte sem integrações: **419 testes passaram, mais 50 subtests; 23 pulados e 38 integrações deselecionadas**. O XML contabiliza 492 entradas incluindo subtests e skips; não são 492 testes adicionais.
- **Dois testes PostgreSQL reais passaram** na réplica isolada: admissão acima de 500 com replay/normalização e atomicidade de publicação/conflito de hash. As chamadas de origem do primeiro teste são fixtures em memória; Store, API, bruto e SQL são reais. O schema privado foi removido ao terminar; réplica parada normalmente, volumes preservados.
- Casos locais cobrem disco insuficiente/recuperação, capacidade física do host, sonda ausente, 503 no meio da página, idempotência, cursor, saúde e pausa/retomada do worker.
- Na observação operacional de **42,734 segundos**, cinco amostras registraram a ponte `ready`: **305 novas capturas aceitas**, pendências **875 → 1.149**, e **20 capturas adicionais verificadas**. Não são tokens distintos. A entrada prosseguiu acima do antigo limite.
- O atraso forward foi de 42.855,148 para 42.777,882 segundos; continuava em aproximadamente 11h53. A fila cresceu. Essa amostra comprova a retirada do bloqueio da entrada, não saída sustentável, cobertura integral ou recuperação completa do atraso.

API, worker e bridge terminaram `running/healthy`, com imagem `sha256:9a6bc9fdda6ed0fbc80a5bf10e0b52373e516d2b881a9ef0a48e85d90af923cc`. Schema 006 mantido, sem migration nova. Origem da ponte preservada: `2026-09-08T23:12:50.282635+00:00`. Apenas esses três serviços da fundação foram recriados; PostgreSQL e serviços legados foram preservados.

Evidência: [resultado e hashes](../.data/gmgn-data/evidence/admission-20260909/result.json), [amostras reais](../.data/gmgn-data/evidence/admission-20260909/live.jsonl), [capacidade](../.data/gmgn-data/evidence/admission-20260909/capacity.json), [suíte](../.data/gmgn-data/evidence/admission-20260909/unit-tests.txt) e [PostgreSQL](../.data/gmgn-data/evidence/admission-20260909/postgres-final.txt). Testes focados anteriores se sobrepõem à suíte final e não são somados. Os testes históricos completos da ponte e um novo backup/restore integral não foram repetidos nesta etapa.

## Próximos passos delimitados

1. DB-09.2: separar avanço/recibo dos consumidores de normalização, DuckLake e ClickHouse. Primeiro provar que um destino lento não bloqueia os demais, com recuperação idempotente de falha parcial.
2. DB-09.3: medir e definir quais detalhes históricos ficam em Parquet e quais projeções precisam de PostgreSQL/ClickHouse; migrar somente depois de provar equivalência e recuperação. Nenhuma exclusão ou migração histórica foi feita aqui.
3. DB-06.4: medir entrada, saída verificada, idade da fila e crescimento físico por uma janela representativa. Só então dimensionar concorrência e afirmar capacidade de recuperar atraso.

Nenhum framework novo, estratégia, UI ou execução financeira nesta entrega. O próximo trabalho é DB-09.2; não aumentar paralelismo às cegas enquanto persistência e confirmações continuam acopladas.
