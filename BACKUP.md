# Backup e ensaio de restauração da base GMGN

Escopo: somente **midas-data**. O ensaio usa **midas-data-restore** ou sufixos restritos desse nome, com rede e volumes próprios. O script não opera o projeto legado, não publica uma segunda API e não remove volumes ou diretórios.

Estado: sintaxe PowerShell 7 validada, 17 testes locais aprovados e ensaio REAL da base contínua com PASS em 09/09/2026 UTC, além dos dois PASS anteriores. O ensaio contínuo e a correção da leitura da saída da sonda estão descritos ao final. Os testes locais usam arquivos temporários e substituições em memória; a prova operacional separada está no relatório descrito ao final.

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

O backup valida os rótulos de propriedade dos cinco volumes novos. Registra quais serviços api/worker/bridge estavam ativos e reinicia somente esses no finally, inclusive após erro. Depois de interrompê-los, recusa outros containers da aplicação, volumes compartilhados com outro container em execução ou sessões conectadas aos dois bancos.

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
7. Compara novamente conteúdo operacional, raw, lake e fontes. Na evidência v2, analytics_applied_at, analytics_receipt, analytics_retry_at e analytics_last_error são derivados e podem mudar no rebuild. O hash de delivery inclui exatamente esses quatro campos, além do batch_id: é exigido igual antes/depois da cópia e no restore antes do rebuild. analytics_attempts permanece no fingerprint operacional e deve continuar idêntico após o rebuild.
8. Grava restore-before-rebuild.json, restore-after-rebuild.json e restore-check com recibo. Preserva todos os containers/volumes do ensaio.

Novos baselines registram evidence_schema_version=2. Baselines anteriores, sem esse campo, usam a fórmula v1 original pelo script atual; não é necessário recuperar a versão antiga do script host. Isso não remove as exigências de imagem e configuração exatas do manifesto. Na v2, a extração dos campos de retry usa to_jsonb(r), que produz null quando a coluna ainda não existe; a migração 005 continua coberta pelo inventário de hashes quando presente. O relatório separa o estado operacional restaurado exatamente antes do rebuild do estado de entrega reconciliado depois e registra ambos os hashes de delivery. Não declara igualdade desses derivados após a reconstrução.

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

## Backup com ponte contínua

Quando bridge estiver running no projeto midas-data, o backup pausa api, worker e bridge que estavam ativos. O finally retoma somente o conjunto registrado; bridge ausente ou parada permanece assim. A lista não inclui qualquer módulo do projeto legado.

A leitura de todas as tabelas operacionais já inclui bridge_state e bridge_delivery: origin, binding, cursores das lanes, ledger e contadores são preservados nos dumps/fingerprints. Estes dados de domínio não substituem a configuração privada exigida para uma futura retomada de coleta.

Runtime.env e bridge.env não são copiados nem participam dos config_sha256. O manifesto registra explicitamente runtime_env_included=false e bridge_env_included=false. As migrações SQL continuam incluídas automaticamente, inclusive a migração da ponte; o teste local verifica que não há leitura dos env-files ao calcular os digests.

RestoreCheck mantém a ponte desligada. Só seleciona postgres/nats/clickhouse e usa verify com --no-deps. O wrapper recusa comandos dirigidos a api, worker, bridge ou ao perfil ingestion no projeto de restore. O Compose da ponte declara seu env_file como optional; o ensaio não exige DSN, chave ou parâmetros da origem legada para reconstruir os dados.

O relatório distingue restore_bridge_started=false e legacy_credentials_required=false. Retomar a ponte em uma implantação recuperada é uma ação posterior e explícita: configurar a origem correta, o mesmo binding/origin e garantir que outra ponte não esteja consumindo com o mesmo papel. Não iniciar coleta automaticamente a partir de um restore-check.

Validação inicial desta mudança: 14 testes locais aprovados, com cenários de ponte ativa e inativa, falha com retomada e bloqueio de writers no restore. Os dois PASS anteriores ocorreram antes dessa extensão. O ensaio da base contínua, incluindo o teste sem rede/credenciais legadas, é registrado abaixo; o perfil desativado sozinho não comprova portabilidade. [Perfis Compose](https://docs.docker.com/compose/how-tos/profiles/).

## Base contínua — diagnóstico da sonda em 09/09/2026 UTC

O backup acceptance-continuous-20260908 possui manifesto complete. Inclui 2.605 capturas (276 fixture e 2.329 legacy_gmgn), 2.334 lotes disponíveis, 299 capturas pendentes, 1.967 objetos raw/117.241.108 bytes descomprimidos e 320.697 fatos no lake. Snapshot de origem: 2344. O estado operacional inclui 42 registros bridge_state e 2.545 bridge_delivery; suas linhas e fingerprints integram a comparação completa.

O primeiro restore-continuous preservou dumps/arquivos, mas interrompeu o gate em Get-Evidence com “no unique result”. A reprodução no mesmo volume terminou com ExitCode=0 e OOMKilled=false: a barra de progresso DuckDB, sem newline, antecedia o marcador JSON por 72 caracteres. Não era JSON ausente ou truncado. A extração do resultado preservado demonstrou igualdade integral com baseline.json. Evidência sanitizada: [restore-probe-framing-diagnostic.json](../.data/gmgn-data/backups/acceptance-continuous-20260908/restore-probe-framing-diagnostic.json).

A sonda agora desativa enable_progress_bar, usa Python unbuffered e publica o resultado com newline e flush explícitos. O parser exige exatamente um marcador em toda a saída, tolera o prefixo nativo observado e recusa JSON inválido sem imprimir seu conteúdo. O recibo rebuild-analytics usa o mesmo enquadramento. Isso trata progresso como saída de terminal, sem alterar fingerprints ou afrouxar a comparação dos dados. [Configuração DuckDB](https://duckdb.org/docs/stable/configuration/overview).

O teste local que reproduz o prefixo falhou antes do patch e passou depois. Os 17 testes de contrato PowerShell passaram em 12,713 s; também verificam marcador ausente/duplicado, inclusive na mesma linha, e supressão do conteúdo em erros de JSON. São testes locais com substituição da saída de Compose, separados do ensaio real abaixo.

A reexecução usa o projeto novo midas-data-restore-continuous2, com DATA_LEGACY_NETWORK=midas-data-absent-legacy-acceptance e DATA_BRIDGE_ENV_FILE=./.data/gmgn-data/absent-bridge-for-restore.env; ambos foram confirmados inexistentes. Somente postgres/nats/clickhouse e o helper verify do restore são executados. A origem midas-data não é operada por esse ensaio, e ambas as réplicas são paradas em finally, preservando volumes e a primeira tentativa. O rebuild e as comparações posteriores passaram; o resultado final está registrado a seguir.

## Restauração contínua aprovada — 09/09/2026 UTC

O script corrigido terminou com **exit 0**, e o relatório [restore-check-20260909T0035199638922Z.json](../.data/gmgn-data/backups/acceptance-continuous-20260908/restore-check-20260909T0035199638922Z.json) registra **status passed** no projeto midas-data-restore-continuous2. Período: **00:35:19.9665935Z–00:50:54.455971Z**, duração de **934,489 s** (15 min 34,5 s) nesta amostra e configuração. A medida não estabelece RTO/SLO para outra carga.

O backup de origem foi executado pelo A0 entre 00:19:22.1805269Z e 00:21:14.5641165Z, registrando api, bridge e worker como previamente ativos. Runtime.env e bridge.env ficaram excluídos. O gate de restauração do A6 não iniciou/parou serviços da origem.

| Verificação | Resultado real |
|---|---|
| Imagem usada pelo restore | sha256:ac8e88e13f89f3ae6afe2aa87f3e93c74d7f3dafbdc41da8302ad4418ed84725 |
| Comparação antes do rebuild | PostgreSQL operacional, catálogo, sequências, snapshot, raw e fatos idênticos ao baseline |
| Lotes reconciliados | 2.334, todos os disponíveis no backup |
| Capturas preservadas | 2.605; 276 fixture e 2.329 legacy_gmgn, segregadas |
| Pendências preservadas | 299 antes e 299 depois; não foram processadas pelo ensaio |
| Raw íntegro | 1.967 objetos; 117.241.108 bytes descomprimidos |
| Fatos no lake | 320.697; SHA256 38627fa050fedff18b29fa00215a0ba988d25385714a7612ea5a301d16a9c73d |
| Snapshot observado | 2344, preservado; o recibo declara latest_catalog_snapshot_observed_not_batch_commit_id |
| Estado da ponte | 42 registros bridge_state; fingerprint 40f3b61a5628cbd0b7f81dc3152f83a689d35ef9a952caa4c7c8187197843cf2 preservado |
| Ledger da ponte | 2.545 registros bridge_delivery; fingerprint b4ed72d95231f553645f007e6e77a38ed506fe8ac559bae7c17efe108985c0c1 preservado |
| Comparação depois do rebuild | Conteúdo operacional, raw, lake, fontes, disponíveis e pendências iguais ao baseline |
| Isolamento | source_services_touched=false, restore_api_worker_started=false, restore_bridge_started=false e legacy_credentials_required=false |
| Encerramento | Ambos os projetos continuous/continuous2 sem containers running; serviços exited(0), volumes preservados |

A rede legada artificial e o bridge.env artificial continuaram inexistentes ao final. Isso comprova o ensaio sem essas dependências neste host; não é um teste em outro host ou cópia externa de desastre. A primeira tentativa incompleta, seu helper de diagnóstico e seus volumes foram preservados.

As comparações podem ser conferidas em [restore-before-rebuild.json](../.data/gmgn-data/backups/acceptance-continuous-20260908/restore-before-rebuild.json) e [restore-after-rebuild.json](../.data/gmgn-data/backups/acceptance-continuous-20260908/restore-after-rebuild.json). O PASS qualifica os dados existentes no backup e os lotes exercitados, preservando explicitamente o backlog; não afirma capacidade contínua maior que a taxa de chegada da fonte.

## Próxima prova — microbatches (preparada, ainda não executada)

O commit DuckLake pode agrupar até oito lotes; cada lote conserva seu batch_id, recibo, checksum, contagem, proveniência e fronteira de disponibilidade. Os membros novos podem compartilhar snapshot_id sem se tornarem um lote único. O verificador deve associar batch_id e checksum ao mesmo objeto membro no metadata do commit e preservar o snapshot antigo de membros já gravados.

Os testes opt-in de test_analytics_batches.py gravam fixtures diretamente em AnalyticsSinks, com IDs group-fixture-UUID. Não criam captures, raw_objects ou normalization_runs para esses fatos. São evidência sintética dos destinos, não capturas GMGN. O backup integral do Lake precisa conservá-las, inclusive memberships repetidos em lotes distintos e recibos de lotes vazios.

A reconstrução ClickHouse tem escopo **normalization_runs PostgreSQL com available_at preenchido**. As fixtures diretas continuam preservadas no Lake/catalog, mas não são reconstruídas automaticamente no ClickHouse por esse comando. Isso deve ficar explícito no relatório: contagem total do ClickHouse restaurado não precisa ser igual à contagem total do Lake quando existem lotes sem registro operacional. Não remover fixtures, reduzir o hash global do Lake ou substituir contagens esperadas para fazer a prova passar.

Plano para restore-microbatch, após os gates da nova imagem e backup coordenado pelo A0:

1. Manter todas as comparações integrais já existentes. Registrar explicitamente a seleção PostgreSQL usada pelo rebuild, total de lotes disponíveis e capturas ainda pendentes.
2. Antes do backup, registrar uma amostra limitada de no máximo oito batch_ids diretos: um commit antigo, membros de um commit compartilhado e um lote vazio. Para cada membro, guardar checksum/contagem/recibo/snapshot; comprovar que não existe normalization_run correspondente. Contar identidades por lote, pois a mesma identidade imutável pode pertencer a dois lotes sem constituir duplicação indevida.
3. No projeto de restore novo, provar que o hash físico integral do Lake e os membros/recibos/snapshots da amostra são iguais. Para a amostra sem registro PostgreSQL, registrar explicitamente a ausência esperada no ClickHouse reconstruído. Não aplicar verify_batch completo a esses lotes esperando ambos os destinos: nesse escopo de restore, o resultado seria false por definição.
4. Exigir que o CLI reconcilie todos os lotes PostgreSQL disponíveis, com um recibo próprio por membro e os dois destinos verificados. Uma falha parcial não pode marcar analytics_applied_at de membros sem confirmação; repetição não pode criar fatos extras por batch_id.
5. Registrar resultado da prova, escopo do ClickHouse, preservação dos dados que existem somente no Lake, pendências e parada final das réplicas em um sidecar restore-microbatch. A amostra complementa o fingerprint global; não o substitui.

Revisão estática: apply_batches verifica cada recibo contra os fatos do próprio lote e associa os snapshots por batch_id+checksum. mark_analytics_many confirma o grupo em uma transação PostgreSQL. A revisão inicial identificou a ausência de validação do batch_id interno contra a chave do mapa, sem encontrar troca nos callers. O A0 corrigiu essa defesa e ampliou o contrato dos recibos; os testes correspondentes passaram em continuous-pipeline-release.xml. O novo restore continua pendente.

Controle preparado para o próximo gate: backupdir .data/gmgn-data/backups/acceptance-microbatch-20260909; RestoreProject midas-data-restore-microbatch; imagem esperada informada pelo A0 ac61520d72a2a6f01e7972281a4fc5c85a823d0f94d2ebcc5f010930331e1f1d. O diretório ainda não existia ao preparar esse controle. O A0 conclui a medição, produz o backup e libera a execução; não criar recursos ou disparar RestoreCheck antes dessa liberação. Conferir manifesto completo, hashes, imagem e migração 005 antes de qualquer cópia; usar runtime.env vigente, sem expor seu conteúdo. Os 17 contratos locais de backup passaram novamente em 12,813 s; essa execução não acessou Docker.

Gate host da evidência v2: **22 testes passaram em 12,823 s**, sem Docker, incluindo fórmula de retry/rebuild, detecção de alteração em cada campo derivado, preservação de analytics_attempts e conteúdo, versão v1 sem alteração, seleção da versão no probe e inventário da migração 005 sem leitura dos env-files. Esses testes executam funções puras e contratos PowerShell locais; não são prova PostgreSQL da fórmula. O A0 recebeu liberação para produzir o novo backup. O novo RestoreCheck ainda não foi executado.
