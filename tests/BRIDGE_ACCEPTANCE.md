# Aceitação da ponte GMGN — S-DB-20260908-02

Owner: A6 QA. Data: 2026-09-08. Última prova UTC: 2026-09-09. Estado: suíte final do pipeline com microbatches e AC08 real aprovados. O restore da imagem anterior passou; medição de capacidade e novo backup/restore da imagem atual continuam como gates separados.

Escopo: ponte de leitura limitada da evidência já persistida. Não é CDC. Os testes usam PostgreSQL e API reais, exclusivamente fixtures no banco novo midas_data. Nenhuma consulta alcança a origem legada, nenhum teste faz requests GMGN e nenhum teste controla Docker.

## Isolamento e método

- Arquivo executável: [test_bridge_integration.py](test_bridge_integration.py).
- Opt-in obrigatório: DATA_INTEGRATION=1. Credenciais vêm do ambiente do projeto novo e não são impressas.
- A fonte de cada caso é um schema bridge_fixture_UUID criado no midas_data. PgSource usa search_path desse schema e conexão com default_transaction_read_only=on.
- Uma segunda conexão de fixture permite manter uma inserção sem commit enquanto a ponte avança.
- O cleanup valida banco e formato exato do schema antes de DROP SCHEMA. Ele não remove capturas, recibos ou tabelas operacionais.
- BridgeConfig usa source_name fixture:UUID e source_kind fixture, com lock por nome. Não compete pelo lock singleton da ponte de produção.
- O transportador realmente envia o bruto a /v1/captures. A exceção de ACK perdido é induzida no cliente somente depois de um aceite real; não é apresentada como interrupção física de rede.
- Contadores do adaptador apenas instrumentam chamadas PgSource reais; páginas e blobs não são substituídos por mocks.

## Casos e critérios

| Caso | Comportamento comprovado quando passar | Estado |
|---|---|---|
| B-01 — paginação da origem | 113 chamadas com timestamp idêntico, success e error, retornam em páginas 50/50/13, sem perda/duplicação; janela vazia retorna vazio; EXPLAIN real demonstra uso possível dos dois índices parciais. | PASS — continuous-pipeline-release.xml |
| B-02 — commit tardio e audit | Forward, recent e audit passam pelo timestamp de uma transação ainda não commitada; depois do commit, a chamada antiga fica fora do recent de 10 minutos e é encontrada por novo ciclo audit. Janelas vazias avançam; ACKs antigos não relêem blob. | PASS — continuous-pipeline-release.xml |
| B-03 — ACK perdido e sessão nova | API aceita bytes reais; exceção controlada impede receipt/checkpoint da ponte. Após fechar/reabrir a sessão de destino, a mesma captura é entregue como duplicate=true, um run permanece e o bruto é igual. | PASS — continuous-pipeline-release.xml |
| B-04 — exceções visíveis | Erro sem corpo e sucesso sem corpo viram unavailable; chain ausente e hash errado viram rejected com motivo. A chamada válida continua e os contadores/checkpoint refletem todas as linhas. | PASS — continuous-pipeline-release.xml |
| B-05 — identidade alterada | Alterar metadados da fonte fixture depois do ACK produz source_identity_changed; fingerprint original, captura original e orçamento já consumido permanecem; não ocorre nova entrega nem leitura de blob. | PASS — continuous-pipeline-release.xml |
| B-06 — guard inicial, histórico | A fixture original comprovou pausa por queue_limit=1. Esse contrato foi substituído em DB-09.1 pela independência da captura e pausa por armazenamento. | PASS histórico — continuous-pipeline-release.xml; evidência atual em [ADMISSION.md](../ADMISSION.md). O teste B-06 reescrito não foi executado como integração nesta etapa. |
| B-07 — bruto global sem chain | hot_searches com chain=None é aceito pela ponte como evidência bruta original, preserva null e bytes, cataloga campos e publica zero fatos de token; unsupported_endpoint permanece explícito. | PASS — continuous-pipeline-release.xml |

## Primeira execução integrada real

O A0 executou a suíte Docker; A6 leu o [JUnit continuous-v3.xml](../../.data/gmgn-data/evidence/continuous-v3.xml). Timestamp: 2026-09-08T23:05:29.630893+00:00; duração registrada: 33,751 s. O runner informou **311 testes aprovados, 2 falhas, 11 skips de PowerShell e 5 subtestes aprovados**. O XML totaliza 329 entradas, incluindo os subtestes.

Cinco dos seis casos originais da ponte passaram, incluindo SQL/EXPLAIN real, commit atrasado, ACK perdido, mutação de origem e backpressure. B-04 falhou porque esperava source_metadata_invalid; após a alteração do contrato, CaptureMetadata rejeita chain ausente no adapter de token e o importador reporta invalid_capture_metadata. A expectativa foi corrigida, mantendo rejeição, ausência de captura e demais controles.

A outra falha foi no teste HTTP de catálogo: holder_count agora possui contrato valid/integer/count. O teste passou a exigir zero válido com unidade count, e total_supply continua comprovando exclusão padrão de unverified, valor exato e unidade desconhecida. Não se removeu a verificação de qualidade.

A reexecução continuous-v3-final.xml aprovou B-04, B-07 e a expectativa ajustada de holder_count. A execução posterior continuous-bulk.xml confirmou novamente os sete casos da ponte; o novo problema de catálogo é descrito abaixo. A coleta local nunca foi usada como prova de execução.

## Revisão do protocolo

A implementação conserva a ordem ACK durável → receipt da chamada confirmado → checkpoint da página. Esses commits não são uma transação distribuída, mas uma falha antes de gravar o checkpoint repete a página; ela não pula chamadas sem registro. IDs e fingerprint da entrega tornam a repetição verificável.

A consulta da origem usa duas pernas com status literal, compatíveis com os índices parciais existentes, e une apenas páginas limitadas de metadados antes de buscar blobs individualmente. O teste de EXPLAIN comprova elegibilidade dos índices; não é benchmark de desempenho no legado.

A cobertura significa chamadas com received_at a partir da origem configurada, encontradas em leituras enquanto continuam retidas. Uma chamada recebida antes da ativação e commitada depois permanece fora desse recorte. Concluir um ciclo audit significa revisitar o intervalo, sem declarar um snapshot fechado de commits. Retenção insuficiente ou crescimento acima da capacidade de releitura impedem garantia global de completude.

## Limites da aceitação por fixtures

- Catálogo explícito por captura, isolamento por origem e limites/truncamento passaram na suíte final descrita abaixo.
- Credencial SELECT da origem real, ativação contínua e confronto da cobertura dependem das evidências operacionais do A0; estes testes não consultam o legado e não provam sua retenção/completude.
- Compactação e o primeiro backup/restore contínuo possuem evidências próprias; a nova imagem de microbatches requer outro backup/restore após a medição coordenada pelo A0.
Correção revisada no código: BridgeConfig agora exige orçamento mínimo de PAGE_SIZE × MAX_RAW_BYTES (200 MiB), alinhado à reserva conservadora por página. Isso resolve a configuração inicialmente aceita que pausava antes da primeira chamada; não representa medição do consumo real.


## Provas adicionais de ingestão em lote

A otimização de Store.record_normalization para COPY binário preserva a obrigação de confirmar run, fatos e campos na mesma transação antes da publicação temporal. O caminho analítico também precisa preservar todos os registros ao ultrapassar seus antigos blocos de inserção e consultas de identidade.

Arquivo: [test_bulk_ingestion.py](test_bulk_ingestion.py). Os **2 casos opt-in passaram em Docker** no continuous-maintenance.xml, incluindo as assertions adicionais de truncamento do catálogo.

| Caso | Critério | Estado |
|---|---|---|
| BK-01 — ranking de 600 tokens | Uma captura market_rank gera exatamente 2.400 fatos de price, holder_count, name e symbol; ambos os destinos verificam contagem/checksum/proveniência, e o último token mantém decimal de 56 casas, holders=0 com unidade count e texto Unicode com aspas. Bruto permanece idêntico. | PASS — continuous-pipeline-release.xml |
| BK-02 — rollback do COPY | Uma interpretação fixture usa IDs inéditos e repete um deles dentro do mesmo lote; UniqueViolation remove run, fatos e campos dessa versão, sem afetar a interpretação normal nem o bruto aceito. | PASS — continuous-pipeline-release.xml |

Esses casos usam somente a API e o PostgreSQL da implantação isolada. O limite de espera de 180 segundos é um deadline do teste, não um SLO de produção ou medição de capacidade.


## Execução após COPY e diagnóstico do catálogo

A6 leu [continuous-v3-final.xml](../../.data/gmgn-data/evidence/continuous-v3-final.xml) e [continuous-bulk.xml](../../.data/gmgn-data/evidence/continuous-bulk.xml). No segundo, o runner informou **323 testes aprovados, 14 skips, 5 subtestes aprovados e uma falha**; o JUnit totaliza 343 entradas e 77,846 s, com início em 2026-09-08T23:35:19.705702+00:00.

Os sete cenários da ponte passaram. BK-01 passou em **3,137 s** e BK-02 em **1,336 s** nessa execução específica, incluindo seus passos de teste. Esses tempos não são benchmark de capacidade da operação contínua.

A única falha foi a busca de um pointer token_info no catálogo agregado: o endpoint agrupava até 20 capturas e retornava apenas os primeiros 1.000 grupos ordenados por endpoint/pointer. O ranking de 600 tokens gerou mais de 3.600 paths, e market_rank precede token_info nessa ordem. A captura pequena estava persistida, mas seu campo não pertencia ao trecho retornado. Não houve evidência de perda do dado.

A correção da rota, autorizada ao A6, acrescenta capture_id opcional combinado obrigatoriamente com source_kind. A consulta seleciona a captura explícita ou até 20 capturas recentes, busca no máximo limit+1 grupos e retorna no máximo 1.000, com has_more/truncated e scope explícitos. Uma captura de outra origem não aparece mesmo quando seu ID é fornecido.

O teste de catálogo agora exige todos os campos da captura pequena correta e prova segregação por origem. O teste bulk exige exatamente 1.000 itens e truncamento verdadeiro, além de rejeitar limites superiores. Não se aumentaram limites nem se apagaram fixtures para ocultar a falha.

O catálogo continua sendo um resumo limitado: não oferece cursor after_pointer ou acesso completo a todos os campos de um ranking grande por esse endpoint. A rota /v1/captures/{id}/raw preserva a evidência original completa. AST da API e coleta de 19 casos HTTP + 2 bulk passaram antes da build; a suíte Docker final abaixo comprovou o comportamento da imagem com essas alterações.

## Suíte Docker final — manutenção e catálogo

O A0 informou exit 0 e A6 leu [continuous-maintenance.xml](../../.data/gmgn-data/evidence/continuous-maintenance.xml). Timestamp UTC: **2026-09-08T23:48:08.844825+00:00**; duração registrada **40,055 s**. O agregado JUnit registra **368 verificações, zero falhas, zero erros e 14 skips**: são **354 verificações não ignoradas, incluindo subtestes**. Existem 353 nós testcase nomeados, dos quais 339 aprovados e 14 ignorados; as 15 entradas adicionais do agregado não têm nomes individuais no XML, portanto não são apresentadas como novos testes independentes.

Os 14 skips pertencem ao contrato PowerShell de backup, executável no host Windows. O A0 informou sua execução separada com 14 testes e 15 subtestes aprovados. Essa informação não é confundida com execução desses casos dentro do Linux/Docker.

Os sete casos da ponte, os dois casos bulk e o teste HTTP de catálogo passaram. BK-01 levou **3,719 s**, BK-02 **1,110 s** e o catálogo **1,662 s** nessa execução. O catálogo pequeno recuperou os campos da captura solicitada e preservou segregação por origem; o ranking de 600 tokens retornou exatamente 1.000 grupos com has_more/truncated verdadeiros, rejeitando limites acima do contrato. Esses tempos incluem os passos de cada teste e não constituem benchmark de produção.

## Diagnóstico posterior do verificador de recuperação parcial

A prova operacional [continuous-partial-recovery.json](../../.data/gmgn-data/evidence/continuous-partial-recovery.json) registrou a interrupção do ClickHouse, recibo DuckLake único, analytics_applied_at nulo e worker degraded; o A0 reiniciou o destino. O artefato permaneceu **incomplete** porque o verificador falhou depois. Este documento não apresenta essa prova completa como aprovada.

A6 reproduziu somente SELECTs da fixture 44453327-0dc0-44c7-807c-72840a419642. As consultas antigas física e lógica retornaram HTTP 500 / ClickHouse 158 / TOO_MANY_ROWS. A identidade e o preço já estavam recuperados: uma projeção limitada ao batch, à origem fixture e ao fact_id retornou exatamente 0.123456789012345678901, lendo uma linha/207 bytes.

O EXPLAIN da contagem física confirmou PrimaryKey (batch_id, source_kind), com seis granules em seis de oito parts. Os limites de 10.000 e 32.768 linhas falharam; o limite fixo de 65.536 passou, lendo 35.221 linhas/2.359.915 bytes naquele momento. O índice MergeTree opera sobre granules, não sobre uma linha exata, conforme a [documentação oficial](https://github.com/ClickHouse/ClickHouse/blob/master/docs/en/engines/table-engines/mergetree-family/mergetree.md). O limite revisado conserva 8 MiB e 5 segundos; ele não cresce automaticamente com a base e pode rejeitar planos futuros maiores.

O script [gmgn-data-partial-recovery.ps1](../../scripts/gmgn-data-partial-recovery.ps1) mantém a contagem de todos os fatos do lote, sem filtro de identidade que ocultaria fatos extras. A consulta lógica agora agrupa apenas o lote/identidade autorizado e conserva HAVING uniqExact(row_hash)=1. A evidência inclui escopo, orçamento e estatísticas das consultas. Falhas atravessam Python/Docker/PowerShell somente como type/reason controlados e são persistidas em evidence.failure; mensagens arbitrárias, DSNs e corpos HTTP continuam suprimidos.

O parser PowerShell e a AST Python passaram. A execução somente do bloco SELECT corrigido, extraído do próprio script, passou contra a fixture real: contagem física [1,1,1] e identidade/preço exatos; nessa leitura posterior a merges, ambas as consultas leram uma linha. A6 não executou POST, reinício, parada nem ciclo de outage. O A0 reexecutou depois a prova completa corrigida, com o resultado comprovado abaixo.

## AC08 — recuperação parcial real aprovada

A6 leu [continuous-partial-recovery-final.json](../../.data/gmgn-data/evidence/continuous-partial-recovery-final.json): **status passed**, entre **2026-09-09T00:01:54.0728322Z** e **00:02:10.2911711Z**. O ciclo de interrupção/reinício foi executado pelo A0, somente no ClickHouse do projeto novo, com uma captura fixture.

Durante a indisponibilidade, ClickHouse estava exited, DuckLake tinha exatamente um recibo/uma linha, analytics_applied_at permanecia nulo e o worker registrou degraded. Depois do retorno, o worker completou o mesmo batch; os dois recibos tinham ok=true, contagem 1, source_counts={fixture:1} e checksum idêntico **5f3fb2a1bda781202bb77df1da8d1ac6523d1fbea06b571c4b101ca115295000**.

O replay HTTP retornou duplicate=true, conservando uma captura, um run, um fato PostgreSQL, uma linha física DuckLake e uma linha física/lógica ClickHouse. A leitura do bruto confirmou integridade. A contagem física leu 31.443 linhas/2.106.789 bytes dentro do orçamento fixo; a consulta lógica leu uma linha/207 bytes. API, worker e ClickHouse terminaram running e o ClickHouse healthy. Snapshot DuckLake do recibo: 2162, com escopo batch_commit.

Essa prova encerrou AC08 para a falha parcial exercitada naquela imagem. Compactação e backup/restauração receberam evidências operacionais posteriores; seus resultados não são inferidos da suíte ou do AC08.

## Imagem final da continuação

A execução [continuous-release.xml](../../.data/gmgn-data/evidence/continuous-release.xml) concluiu com exit 0, 376 entradas JUnit, zero falhas/erros, 14 skips e 45,297 segundos. São 346 casos não ignorados e 16 verificações adicionais de subtestes, totalizando 362 verificações aprovadas. Inclui os testes de temporários privados, compactação serial, restauração de threads e rejeição de output acima do limite. Os 14 skips são exclusivos do PowerShell no host, com evidência própria já aprovada.

O gate [continuous-compaction-release.json](../../.data/gmgn-data/evidence/continuous-compaction-release.json) executou pela imagem final sem override: PASS, mesmos 12 batches, identidades, row_hash, recibos e snapshot anterior. Nenhuma nova compactação foi necessária nesse momento, pois as três rodadas anteriores já haviam reduzido a fragmentação abaixo do limiar. O ramo automático que volta a compactar com o crescimento da coleta recebe evidência operacional separada.

## Pipeline com microbatches — gates verificados em 09/09/2026 UTC

O A0 identificou a imagem congelada como **sha256:ac61520d72a2a6f01e7972281a4fc5c85a823d0f94d2ebcc5f010930331e1f1d**. Nesta rodada A6 leu os artefatos locais e executou somente os contratos PowerShell no host; nenhum comando Docker foi executado por A6 durante a medição.

[continuous-pipeline-release.xml](../../.data/gmgn-data/evidence/continuous-pipeline-release.xml) registra início em **2026-09-09T01:22:34.205411+00:00**, duração **76,214 s**, zero falhas/erros e 22 skips. São **419 casos nomeados aprovados**, 18 verificações adicionais no agregado de subtestes e 22 casos ignorados: 459 entradas agregadas, das quais 437 não ignoradas. Os sete casos da ponte e os dois bulk passaram novamente. O teste PostgreSQL real de publicação/COPY/conflito e recibos agrupados também passou.

Os skips são **17 testes PowerShell de backup** e **5 testes opt-in dos destinos agrupados**. Os primeiros passaram novamente no host: **17 testes em 12,813 s**, sem Docker. Os cinco casos agrupados têm prova separada em [continuous-retry-groups.xml](../../.data/gmgn-data/evidence/continuous-retry-groups.xml): 67 casos nomeados mais duas verificações adicionais, zero skips/falhas/erros, duração 10,688 s, início 01:12:33.892571Z. Esse gate também cobre Store, agrupamento do worker, pipeline e Settings; não é apresentado como execução dos cinco skips dentro da suíte principal.

O gap de identidade de recibo apontado na revisão inicial foi corrigido pelo A0: Store verifica batch_id interno, contagem, checksum, proveniência, fronteiras, confirmação dos dois destinos e snapshot antes da escrita. A suíte principal aprovou os cenários de recibo incompatível e rollback. Settings omite credenciais/URLs do repr; o teste específico passou. A fixture da ponte usa repr redigido, com prova local adicional de traceback pytest. Nenhum valor de credencial é registrado neste documento.

### AC08 da imagem atual

[continuous-pipeline-partial.json](../../.data/gmgn-data/evidence/continuous-pipeline-partial.json) registra **status passed**, de **01:23:54.0411135Z a 01:24:46.2012547Z**. Durante a falha parcial houve um fato/recibo DuckLake, analytics_applied_at nulo e worker degraded. Após o retorno, o mesmo lote foi verificado nos dois destinos, com uma captura, um run e um fato físico/lógico por destino, replay duplicate=true e raw íntegro. Checksum do lote: 92eacda5270a01833437e30d94e2891cc0fcf68a7e2ae515441a5710ae7f4b70; snapshot de commit: 5616. API, worker e ClickHouse terminaram running; ClickHouse healthy.

### Controle do próximo restore

O backup planejado é **acceptance-microbatch-20260909**; ele ainda não existia na verificação local desta rodada. O projeto planejado é **midas-data-restore-microbatch**, sempre novo e sem sobrescrever as réplicas anteriores. O A0 produzirá o backup depois da medição e liberará o RestoreCheck. A prova deve usar o runtime.env vigente, confirmar a imagem exata do manifesto e incluir a migração 005.

O teste de restauração deve manter a rede legada artificial e o bridge.env artificial inexistentes, iniciar somente infraestrutura/helpers da réplica e parar seus serviços em finally, preservando volumes. O escopo ClickHouse permanece normalization_runs PostgreSQL disponíveis; fixtures diretas dos testes de destino precisam continuar íntegras no Lake e não são presumidas reconstruídas no ClickHouse. A amostra limitada e os critérios estão em [BACKUP.md](../BACKUP.md#próxima-prova--microbatches-preparada-ainda-não-executada).

Estes gates comprovam os comportamentos exercitados. A série de medição ainda em andamento, a capacidade contínua em relação à chegada da fonte e o novo backup/restore não recebem PASS por antecipação.

### Evidência de backup v2 preparada para migração 005

O script host separa os quatro derivados de entrega (analytics_applied_at, analytics_receipt, analytics_retry_at e analytics_last_error) do fingerprint operacional. O delivery_sha256 conserva os quatro para exigir estado exato durante a cópia e antes do rebuild; analytics_attempts permanece operacional. Após o rebuild, os derivados podem estar reconciliados, com seus hashes pré/pós declarados. Baseline sem evidence_schema_version usa a fórmula v1 original; imagem/configuração exatas continuam obrigatórias. O inventário inclui 005_analytics_retry.sql e exclui env-files. **22 testes locais passaram em 12,823 s**, sem Docker. A fórmula teve prova local, não SQL real nesta etapa. Script liberado ao A0 para novo backup; restore-microbatch continua pendente e os resultados históricos acima permanecem restritos às imagens e dados ensaiados.
