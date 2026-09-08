# Backup e ensaio de restauração da base GMGN

Escopo: somente **midas-data**. O ensaio usa **midas-data-restore**, com rede e volumes próprios. O script não opera o projeto legado, não publica uma segunda API e não remove volumes ou diretórios.

Estado: sintaxe PowerShell 7 validada, 11 testes locais aprovados e dois backups/restores Docker com PASS real em 08/09/2026. Os testes locais usam arquivos temporários e substituições em memória; a prova operacional separada está no relatório descrito ao final.

## Execução

Use PowerShell 7 no mesmo host Docker, com imagens já construídas e carregadas. O env-file é obrigatório e passado explicitamente em todas as chamadas Compose. Seu conteúdo não é lido pelo script, incluído no backup ou impresso.

    # Execute após encerrar importações, testes de integração e manutenção.
    $backup = Join-Path (Get-Location) ('.data/gmgn-data/backups/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    & .\scripts\gmgn-data-backup.ps1 -Mode Backup -EnvFile .\.data\gmgn-data\runtime.env -BackupDirectory $backup

    # O diretório precisa conter manifesto e hashes válidos.
    & .\scripts\gmgn-data-backup.ps1 -Mode RestoreCheck -EnvFile .\.data\gmgn-data\runtime.env -BackupDirectory $backup

O script recusa diretório de backup já existente. O restore recusa recursos existentes do projeto selecionado, preservando tentativas anteriores. Para repetir, use um sufixo restrito via RestoreProject, conforme o exemplo ao final. Não há limpeza automática.

O restore exige os mesmos IDs de imagens, Compose, lock de dependências, inicializador PostgreSQL e todas as migrações SQL. O script não constrói ou baixa imagens. Guarde as imagens e a revisão correspondente separadamente; hashes verificam compatibilidade, mas não substituem os binários.

## Consistência e conteúdo

O backup valida os rótulos de propriedade dos cinco volumes novos. Registra quais serviços api/worker estavam ativos e reinicia somente esses no finally, inclusive após erro. Depois de interrompê-los, recusa outros containers da aplicação, volumes compartilhados com outro container em execução ou sessões conectadas aos dois bancos.

É necessária uma janela coordenada sem novos comandos de importação, rebuild, outros clientes de banco ou manutenção/GC DuckLake. As verificações antes/depois detectam alterações, mas não são uma trava distribuída contra administradores que iniciem um escritor durante a cópia.

| Arquivo | Conteúdo |
|---|---|
| midas_data.dump | Dump custom completo do banco operacional, schema, constraints e sequências. |
| midas_lake.dump | Dump custom completo do catálogo DuckLake, snapshots, referências e possíveis dados inline. |
| raw.tar | Volume raw completo, incluindo gzip originais e caminhos. |
| lake.tar | Volume DuckLake completo, incluindo arquivos de snapshots históricos. |
| baseline.json | Contagens e fingerprints, sem imprimir linhas capturadas. |
| manifest.json | Inventário SHA256/tamanho, imagens, hashes de configuração, volumes e relógios UTC. |
| manifest.sha256 | SHA256 do manifesto. |
| attempt.json | Marcador de início; o manifesto completo determina a validade da tentativa. |

Não há redirecionamento binário pelo PowerShell: pg_dump grava no container e docker cp transfere o arquivo. Os dumps temporários em /tmp dos containers ficam preservados; não há remoção automática.

Cada pg_dump dá consistência no próprio banco. A pausa coordenada mantém os dois bancos e os arquivos no mesmo estado durante a cópia. O catálogo é indispensável: um glob de Parquet não reconstrói snapshots, exclusões, dados inline ou evolução de schema. [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html), [DuckLake catálogo e conexão](https://ducklake.select/docs/stable/duckdb/usage/connecting).

SHA256 detecta corrupção, mas não autentica um backup alterado junto com o manifesto. Restaure artefatos confiáveis: PostgreSQL executa definições do dump. Senhas/roles globais não são exportadas; o inicializador recria as roles com o env-file fornecido. [PostgreSQL pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).

## Verificação

Antes/depois da cópia, uma leitura sem migrações calcula fingerprints das tabelas e sequências da aplicação e do catálogo. Cursores PostgreSQL e lotes DuckDB leem até 256 linhas por vez; raw é descomprimido em blocos de 512 KiB e conferido contra SHA256/tamanho registrados. DuckLake é anexado READ_ONLY, respeitando o catálogo. A evidência inclui contagens por source_kind, batches disponíveis, pendências, snapshot e hash de facts_log.

O custo é proporcional à base inteira, com leituras completas. Ordenações de tabelas sem PK e do lake podem usar disco temporário. É uma verificação offline: não há RTO/RPO nem duração comprovada para bases grandes nesta entrega.

No restore:

1. Verifica manifesto/artefatos antes de criar recursos.
2. Sobe somente PostgreSQL, NATS e ClickHouse isolados e aguarda healthchecks.
3. Restaura dumps com pg_restore --single-transaction --exit-on-error em bancos novos.
4. Extrai raw/lake em volumes vazios como UID 65532. Aceita apenas arquivos/diretórios e recusa caminhos fora do destino.
5. Compara bancos, catálogo, sequências, snapshot, raw e lake com a origem.
6. Executa o CLI rebuild-analytics, que verifica cada batch disponível contra DuckLake e ClickHouse.
7. Compara novamente conteúdo operacional, raw, lake e fontes. analytics_applied_at/analytics_receipt podem mudar no rebuild e têm hash separado para a comparação anterior.
8. Grava restore-before-rebuild.json, restore-after-rebuild.json e restore-check com recibo. Preserva todos os containers/volumes do ensaio.

ClickHouse é reconstruído a partir dos batches PostgreSQL e recibos DuckLake. JetStream começa vazio. O worker atual republica outbox não consumido após recriação do broker; o restore não inicia o worker e preserva/reportará pendências sem afirmar que foram normalizadas. Passed comprova os batches disponíveis no backup; não afirma backlog vazio.

Snapshot DuckLake identifica um commit. O backup preserva também occurred_at, recorded_at, received_at e available_at; não transforma horário do backup/snapshot em momento de conhecimento da fonte.

## Falhas e limites

Uma falha não produz manifesto completo válido nem relatório passed. Arquivos parciais são preservados. O finally tenta reiniciar os serviços novos antes ativos; falha de reinício é reportada e exige verificar seu estado. O restore não inicia, para nem altera a origem.

Erros brutos Docker/banco são suprimidos para evitar DSN exposto. Para diagnóstico, reproduza somente a etapa com logging sanitizado. Não imprima inspect completo, Compose config expandido ou env-file.

O backup fica no host local. Esta entrega não implementa cópia externa criptografada, retenção, WAL/PITR, gestão de chaves, ensaio em outra máquina ou agendamento. Uma cópia no mesmo disco não protege contra perda do host.

Isolamento usa --project-name, --file e --env-file, verificação de rótulos e recusa de volumes existentes. [Docker Compose CLI](https://docs.docker.com/reference/cli/docker/compose/).

## Testes locais

    python -B -m unittest discover -s data_platform/tests -p test_backup_contract.py -v

Cobrem manifesto válido, corrupção de arquivo/manifesto, traversal/duplicação no inventário, projeto/versão/status inválidos, recusa de restore existente, retomada somente dos serviços antes ativos após falha e argumentos explícitos Compose. Sem PowerShell/script de host, o teste declara skip. A prova operacional é o restore real separado.

## Evidência operacional de 08/09/2026

O primeiro ensaio Docker passou sem correções de runtime:

- Backup: 22:01:33–22:01:41 UTC; fingerprints antes/depois idênticos.
- Restore: 22:02:14–22:02:52 UTC; status passed, 66 batches reconstruídos e verificados.
- 66 capturas: 56 fixture e 10 legacy_gmgn, mantidas separadas; zero capturas pendentes.
- 60 objetos raw, 46.916 bytes descomprimidos; 530 fatos no lake; snapshot 67 preservado.
- PostgreSQL, catálogo, sequências, raw e lake iguais ao backup; rebuild conferiu os dois sinks.
- Origem: api, worker, postgres, nats e clickhouse running/healthy após o ensaio.
- Réplica: postgres, nats e clickhouse parados; todos os volumes preservados.

Artefato: .data/gmgn-data/backups/acceptance-20260908/restore-check-20260908T2202146134147Z.json.
Esses tempos são medidas desta amostra pequena, sem extrapolação de SLO para a base histórica inteira. O ensaio valida a restauração dos dados que existiam; não atesta a correção semântica dos adaptadores ou resolve quarentenas.

Para repetir sem apagar o ensaio anterior, o parâmetro RestoreProject aceita somente midas-data-restore ou o sufixo restrito -[a-z0-9]+, por exemplo:

    & .\scripts\gmgn-data-backup.ps1 -Mode RestoreCheck -RestoreProject midas-data-restore-v2 -EnvFile .\.data\gmgn-data\runtime.env -BackupDirectory <diretorio-do-novo-backup>

Cada projeto precisa estar vazio antes do ensaio. O script continua recusando sobrescrever recursos existentes e nunca aceita o projeto legado. A parametrização foi acrescentada após o primeiro PASS, recebeu testes locais e foi validada no ensaio real v2 descrito a seguir.

## Ensaio final v2 — 08/09/2026

O backup final e o restore no projeto **midas-data-restore-v2** passaram sem mudanças no script:

| Verificação | Evidência observada |
|---|---|
| Imagem da aplicação | sha256:572954c171396cb4a15e2c485d11642025bb72e49c8cc10bfb1f2b9a1dc7a066 |
| Migrações | 001_foundation, 002_query_indexes, 003_interpretation_index |
| Backup | 22:21:13–22:21:23 UTC, cerca de 10 s nesta amostra |
| Restore | 22:22:00–22:22:38 UTC, cerca de 38,5 s nesta amostra |
| Capturas preservadas | 109: 99 fixture e 10 legacy_gmgn |
| Batches verificados | 121, incluindo versões históricas de interpretação |
| Fatos no lake | 1.269; SHA256 259a2b0809b01b60ed04fd9c39780d1a651407e01687d06c5dc9ce34e8594200 |
| Raw íntegro | 97 objetos; 50.859 bytes descomprimidos |
| Snapshot preservado | 122 |
| Capturas pendentes | 0 |
| Reconstrução | 121 batches reconciliados; recibos e checksums validados em DuckLake e ClickHouse |
| Origem após ensaio | api, worker, postgres, nats e clickhouse running/healthy |
| Réplicas ao encerrar | midas-data-restore e midas-data-restore-v2 com serviços parados; volumes preservados |

Diretório final: .data/gmgn-data/backups/acceptance-v2-20260908.
Relatório: restore-check-20260908T2222000767210Z.json, status passed.

A igualdade abrange fingerprints de todas as tabelas e sequências restauradas, catálogo/snapshot DuckLake, raw e fatos; o rebuild preserva o histórico de interpretações. O número de batches pode superar capturas porque uma captura pode ter múltiplas versões do normalizador. Estas medidas não estabelecem SLO ou validade semântica de cada interpretação.

Também passou a prova adicional AC08, com 1 fixture única: ClickHouse parado, DuckLake com 1 fato/recibo, PG analytics_applied_at NULL e worker degraded. Depois do reinício em finally, o worker concluiu o retry, com 1 fato físico/lógico por destino, checksum igual, raw íntegro e replay HTTP duplicate=true. Evidência: .data/gmgn-data/partial-recovery-acceptance-20260908.json. O ClickHouse terminou healthy; nenhuma API externa foi chamada.
