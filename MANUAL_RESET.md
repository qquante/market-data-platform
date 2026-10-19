# Limpeza manual dos dados Midas — 09/09/2026

> Atualização posterior, 09/09/2026: a limpeza Midas desta lista foi executada pelo usuário com LIMPAR_DADOS_MIDAS.ps1; saída de sucesso e ausência dos alvos foram conferidas. O usuário depois informou concluir a limpeza global do Docker; sem verificação pós-reset. As instruções abaixo preservam o inventário da limpeza restrita já realizada. Correção PowerShell, escopo e limites em [SESSION_SUMMARY_20260909.md](SESSION_SUMMARY_20260909.md).

No momento do inventário, a exclusão automática tinha sido bloqueada duas vezes pela revisão de aprovação; nenhum alvo havia sido apagado pelo agente. O usuário assumiu a limpeza manual e autorizou seguir no código/testes isolados. Essa limpeza manual foi concluída posteriormente, conforme a atualização acima.

Antes de remover os volumes, pare/remova seus containers pelo Docker Desktop. Não volte a iniciar executores antigos: os históricos de ordens e antiduplicação fazem parte da exclusão autorizada. Esta limpeza é local e não cancela ordens ou posições externas.

## Docker — lista exata de volumes

O script da limpeza restrita selecionou somente os volumes abaixo e não executou limpeza global do Docker. O projeto contracts-test continha somente fixtures desta validação. A orientação global posterior é uma etapa distinta, registrada no resumo consolidado.

### midas-data-base

- `midas-data-base_clickhouse`
- `midas-data-base_lake`
- `midas-data-base_nats`
- `midas-data-base_postgres`
- `midas-data-base_raw`

### midas-data

- `midas-data_clickhouse`
- `midas-data_lake`
- `midas-data_nats`
- `midas-data_postgres`
- `midas-data_raw`

### midas-data-restore

- `midas-data-restore_clickhouse`
- `midas-data-restore_lake`
- `midas-data-restore_nats`
- `midas-data-restore_postgres`
- `midas-data-restore_raw`

### midas-data-restore-v2

- `midas-data-restore-v2_clickhouse`
- `midas-data-restore-v2_lake`
- `midas-data-restore-v2_nats`
- `midas-data-restore-v2_postgres`
- `midas-data-restore-v2_raw`

### midas-data-restore-continuous

- `midas-data-restore-continuous_clickhouse`
- `midas-data-restore-continuous_lake`
- `midas-data-restore-continuous_nats`
- `midas-data-restore-continuous_postgres`
- `midas-data-restore-continuous_raw`

### midas-data-restore-continuous2

- `midas-data-restore-continuous2_clickhouse`
- `midas-data-restore-continuous2_lake`
- `midas-data-restore-continuous2_nats`
- `midas-data-restore-continuous2_postgres`
- `midas-data-restore-continuous2_raw`

### midas-data-restore-microbatch

- `midas-data-restore-microbatch_clickhouse`
- `midas-data-restore-microbatch_lake`
- `midas-data-restore-microbatch_nats`
- `midas-data-restore-microbatch_postgres`
- `midas-data-restore-microbatch_raw`

### midas-fomo

- `midas-fomo_fomo_postgres_data`
- `midas-fomo_fomo_redis_data`
- `midas-fomo_gmgn_execution_data`

### midas-data-contracts-test

- `midas-data-contracts-test_clickhouse`
- `midas-data-contracts-test_lake`
- `midas-data-contracts-test_nats`
- `midas-data-contracts-test_postgres`
- `midas-data-contracts-test_raw`

## Arquivos e diretórios de dados

Caminhos conferidos dentro do workspace. Podem ser removidos integralmente os seguintes diretórios de dados, após parar os processos do Midas:

- `C:\Users\PC\Desktop\Midas\.data\adams-public-history`
- `C:\Users\PC\Desktop\Midas\.data\creator-launch`
- `C:\Users\PC\Desktop\Midas\.data\fomo-telegram-tests`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-auto-execution`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-call-audit`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-captures`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-execution`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-manual-test`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-telegram-audio`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-telegram-dashboard`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-telegram-executor`
- `C:\Users\PC\Desktop\Midas\.data\launch-wallet-probes`
- `C:\Users\PC\Desktop\Midas\.data\pons-probe-20260907`
- `C:\Users\PC\Desktop\Midas\.data\rh-aggregate-trades`
- `C:\Users\PC\Desktop\Midas\.data\rh-precall-180s`
- `C:\Users\PC\Desktop\Midas\.data\robinhood-ws-probe`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-data\backups` (dumps, corpos antigos de replay e evidências locais; os relatórios textuais no código ficam preservados).
- `C:\Users\PC\Desktop\Midas\.data\gmgn-data\evidence` (dumps, corpos antigos de replay e evidências locais; os relatórios textuais no código ficam preservados).

Arquivos individuais e sidecars SQLite associados (`-wal`, `-shm`, `-journal`), se presentes:

- `C:\Users\PC\Desktop\Midas\.data\engineer-calls-forwarder.sqlite3`
- `C:\Users\PC\Desktop\Midas\.data\fomo-trade-journal.json`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-trending.sqlite3`
- `C:\Users\PC\Desktop\Midas\.data\midas.sqlite3`
- `C:\Users\PC\Desktop\Midas\.backups\gmgn-forensics-pre-v2-20260902-1225.dump`
- `C:\Users\PC\Desktop\Midas\.backups\gmgn-outcomes-v1-20260902-180519\gmgn-outcomes-before.dump`
- `C:\Users\PC\Desktop\Midas\.backups\gmgn-v4-queue-20260902\gmgn-v4-queue.dump`
- `C:\Users\PC\Desktop\Midas\.scratch\gmgn-refactor-investigation-20260908\capture-test-tmp-b64983d1\test_poll_stores_success_and_i0\trending.sqlite3`
- `C:\Users\PC\Desktop\Midas\.scratch\gmgn-refactor-investigation-20260908\capture-test-tmp-b64983d1\test_retention_prunes_complete0\retention.sqlite3`
- `C:\Users\PC\Desktop\Midas\.scratch\gmgn-refactor-investigation-20260908\capture-test-tmp-b64983d1\test_transient_error_retries_o0\retry.sqlite3`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-data\import-20260908T215742Z-17afd8ca.jsonl`
- `C:\Users\PC\Desktop\Midas\.data\gmgn-data\recovery-2079eb5d-9aea-4ec3-ac25-d9d2523848d3.raw`

## Arquivo externo autorizado

- `D:\Midas\fomo-archive` — histórico e backtests montados pelo legado.

## Preservar

- Código, SQL de schema/migrations, testes e documentação do repositório. SQL de estrutura não é uma base de dados preenchida.
- `.secrets`, `.env`, arquivos `*.env` e credenciais.
- `.data/midas-user.session` e seus arquivos auxiliares; `.data/fomo-based-forwarder` (sessão Telegram).
- `.data/fomo-chrome-profile` (perfil/autenticação do navegador).
- `.data/gmgn-data/base-runtime.env`, `runtime.env`, `bridge.env` e `host-space-probe`.
- Backups de código em `.backups`: a lista acima remove somente dumps/bancos, não fontes.

Não foi feita nova cópia/backup. Os arquivos de configuração preservados não significam autorização para reativar execução financeira. A implantação da versão nova fica para depois da limpeza manual.
