# GMGN — coleta contínua e contratos da base Docker

> Registro histórico anterior ao reset de 09/09/2026. Dados, dumps e evidências listados em .data/gmgn-data/evidence e .data/gmgn-data/backups foram incluídos na limpeza; links podem estar indisponíveis. Não representa runtime ou restauração atual. Estado e evidências preservadas: [SESSION_SUMMARY_20260909.md](SESSION_SUMMARY_20260909.md).

Sessão S-DB-20260908-02. Escopo: conectar a captura já existente à fundação isolada, ampliar contratos comprovados e planejar a migração histórica. Os resultados abaixo vêm de código, testes e Docker real. A coleta está conectada e os mecanismos de integridade passaram nos gates. A capacidade de recuperar o atraso acumulado ainda não está homologada; o fechamento distingue esse limite da integridade dos dados.

## Entrega

- Ponte incremental do PostgreSQL legado para a API da base nova. Lê apenas as colunas necessárias, usando papel exclusivo sem escrita nas tabelas.
- Preservação de bytes originais, hashes, identidade, origem, clocks UTC e contexto de consulta permitido.
- Ledger por captura, checkpoint durável e três percursos: avanço, revisão recente e auditoria desde a origem. Erro ou ACK incerto retêm a página para repetição.
- Chamadas sem bruto permanecem contabilizadas como indisponíveis. Endpoints ainda sem adaptador preservam bruto e catálogo, com estado de interpretação explícito.
- Normalizador gmgn-p0-v3 e registro de 122 paths: 80 com declaração documental do provedor e 42 ainda pendentes. Declaração documental não significa validação econômica automática.
- Catálogo por captura e origem, resumo limitado e truncamento explícito.
- COPY PostgreSQL, lote JSON tipado no DuckLake, recibos inline e manutenção dos arquivos pequenos sob o lock do escritor.

Stack mantida: Python/FastAPI/Pydantic, PostgreSQL, NATS JetStream, DuckDB/DuckLake/Parquet e ClickHouse. Responsabilidades e operação estão em [README](README.md) e [CONTINUOUS](CONTINUOUS.md).

## Evidência real e semântica

O início da ponte é 2026-09-08T23:12:50.282635Z. Não foi movido durante falhas ou manutenção para esconder atraso. A origem pertence ao cluster PostgreSQL 7679995080186957864.

No recorte auditado de 30 segundos desde essa origem, a leitura repetível encontrou 130 chamadas, cobrindo 16 endpoints: 124 com bruto aceito e seis indisponíveis na origem. Todos os IDs estavam no ledger; todos os 124 aceites já possuíam recibos analíticos nos dois destinos. O download pela API de oito amostras de endpoints distintos manteve o SHA256 exato. Evidência: [continuous-real-window.json](../.data/gmgn-data/evidence/continuous-real-window.json).

As dez capturas originais da fundação mantêm 30 interpretações: v1 com 476 fatos, v2 com 696 e v3 com 696. Todos os runs possuem recibos verificados nos dois destinos; versões anteriores não foram substituídas. Evidência: [continuous-versioned-replay.json](../.data/gmgn-data/evidence/continuous-versioned-replay.json).

O inventário e as fontes campo a campo estão em [FIELD_CONTRACTS](FIELD_CONTRACTS.md) e [registro v3](contracts/gmgn-p0-v3.json). Price, market cap, supply, percentuais, timestamps, flags, janelas e links não são tratados como conceitos intercambiáveis. Em particular, market cap reportado não vira FDV; links sociais continuam metadados do provedor, sem certificação de autoria ou de conteúdo.

## Confiabilidade

A imagem Docker 302e912b8747f172c43374ac8124610032eb2ec43380fcad1a20013e71191c16 passou em 419 casos executados, zero falhas/erros e 22 skips, em 76,214 segundos. Os skips pertencem ao host PowerShell e à integração agrupada opt-in, com gates separados. A contagem não soma testes sobrepostos nem trata o agregado de subtestes do JUnit como novos casos independentes. [Suíte final](../.data/gmgn-data/evidence/continuous-pipeline-release.xml), [grupos e retry](../.data/gmgn-data/evidence/continuous-retry-groups.xml), [inventário de gates](../.data/gmgn-data/evidence/continuous-gates.json).

- Testes em Docker cobrem precisão decimal, UTC, null/false/zero, bytes e hashes, isolamento de fixtures, publicação temporal, versões e consultas as-known.
- Testes da ponte cobrem paginação com timestamps iguais, commit atrasado, ACK perdido após aceite real, reinício de sessão, duplicação, mudança de identidade, indisponibilidade, rejeição e backpressure.
- Um ranking fixture de 600 tokens e 2.400 fatos atravessa o caminho completo sem perder precisão de 56 casas decimais, Unicode ou holders zero.
- Uma violação de identidade durante COPY desfaz run, fatos e catálogo daquela interpretação, preservando a captura e o bruto.
- O bootstrap foi ensaiado num catálogo PostgreSQL vazio separado; recibos inline configurados e segunda inicialização sem novo snapshot. Nenhuma captura nesse catálogo de ensaio. [Prova](../.data/gmgn-data/evidence/continuous-bootstrap.json).
- AC08 interrompeu somente o ClickHouse novo. DuckLake confirmou o lote, PostgreSQL manteve analytics pendente e o worker exibiu erro. Após reinício, o retry concluiu, os destinos concordaram e replay HTTP não duplicou a fixture. [Prova da versão concorrente](../.data/gmgn-data/evidence/continuous-pipeline-partial.json).

A primeira tentativa do verificador AC08 atingiu o orçamento de leitura no conjunto já maior. O verificador foi corrigido com filtro antecipado pelo lote, mantendo a contagem de todos os fatos desse lote; o limite de linhas passou a 65.536 após EXPLAIN e medição, mantendo 8 MiB e cinco segundos. O teste final passou. Essa falha do verificador não foi apresentada como perda de dados.

## Gargalo identificado e correção

O fluxo real acumulou fila e acionou a pausa automática. Medições mostraram gravação por fato, repetição de buscas de identidade e milhares de arquivos Parquet pequenos. As mudanças preservam a transação de publicação, os conflitos e a conferência dos dois destinos.

A compactação encontrou dois problemas durante o gate real: spill tentava escrever em uma pasta sem permissão; depois, dois grupos simultâneos ultrapassaram o limite de temporários. O sink agora usa diretório temporário privado, spill limitado a 128 MB e RAM de 256 MB. A manutenção executa uma thread e no máximo um output por tabela, teto global de dois, restaurando a configuração anterior em finally.

Três rodadas limitadas da configuração proposta reduziram 5.303 arquivos ativos a nove: oito de fatos e um de recibos. Foram comparados identidades e row_hash globais, contagens, recibos, 12 lotes representativos e time travel no snapshot anterior em cada rodada. Todos permaneceram iguais. Não houve expiração de snapshots nem exclusão dos arquivos antigos. Esses três ensaios usaram um override controlado da chamada; a mesma configuração foi incorporada ao código e tem gate separado na imagem final.

Provas: [rodada 1](../.data/gmgn-data/evidence/continuous-compaction-single-probe.json), [rodada 2](../.data/gmgn-data/evidence/continuous-compaction-single-probe-2.json), [rodada 3](../.data/gmgn-data/evidence/continuous-compaction-single-probe-3.json). Tempos de consultas pontuais não são benchmark de capacidade; a vazão da coleta é medida separadamente.

## Histórico e próximos limites

[Plano de migração](MIGRATION.md): estimativas e amostras apontam cerca de 22,1 GB físicos nas duas tabelas de chamadas e blobs. O documento define pré-requisitos, lotes, reconciliação, limites e critérios para liberar backfill. Nenhuma migração integral foi executada.

A cobertura é por chamadas descobertas e intervalos revisitados de received_at. Esse horário não é o commit da transação de origem; sem um marcador de commit ou CDC não existe fronteira definitivamente fechada para commits arbitrariamente tardios. Corpo não armazenado na origem não pode ser recuperado por replay.

Os adaptadores econômicos atuais continuam concentrados em token_info e market_rank. Os demais endpoints preservam evidência, mas ainda exigem homologação para promover novos fatos. A captura de posts do X, estratégias, coortes, análise de convergências, snapshots por faixas de market cap e visualização pertencem às próximas camadas.

O limite de 32 GiB controla aceites de bruto da ponte, não a ocupação total dos bancos, dos snapshots ou do Docker. Retenção, alertas de capacidade e ensaio na carga pretendida continuam necessários antes de operação prolongada em escala.

## Fechamento operacional

A versão concorrente passou na suíte e na interrupção real do ClickHouse. A ponte retoma o mesmo checkpoint. A restauração anterior passou com 2.334 lotes e 320.697 fatos; backup/restauração da versão atual recebe registro próprio abaixo. DB-06.4 permanece aberto porque a janela medida não demonstrou recuperação sustentada do atraso.

Os hashes de 195 arquivos operacionais legados conferiram com a referência inicial. Nenhum código legado, estratégia ou operação financeira foi alterado. A única provisão no banco de origem foi o papel de leitura necessário à ponte.

## Lotes, retomada e encerramento

Grupos de até oito capturas, 10.000 fatos e 16 MiB amortizam commits sem fundir identidades. Cada interpretação mantém seu recibo, checksum e participação no snapshot; interpretações antigas continuam consultáveis. PostgreSQL conserva a separação entre persistir conteúdo e publicar sua disponibilidade. Erros de conteúdo são isolados; retries usam relógio do banco, atraso de 1 a 60 segundos e prioridade para primeiras tentativas. O caso de oito lotes inválidos bloqueando o nono foi reproduzido e corrigido.

Normalização e aplicação analítica se sobrepõem sob um proprietário, com um único escritor analítico. A manutenção aguarda as duas etapas. O encerramento espera a thread síncrona do sink terminar antes de liberar o lock, inclusive com falha de transporte. Recibos trocados, incompletos, inconsistentes ou incompatíveis com a contagem imutável provocam rollback da confirmação.

Um diagnóstico de teste revelou senha local no repr de uma fixture. O repr foi protegido, o XML privado sanitizado e a credencial do banco alvo rotacionada. A configuração também omite segredos de seu repr. Nenhuma credencial do provedor ou do legado foi alterada.

## Capacidade medida e o que permanece aberto

A janela final foi de 2026-09-09T01:25:23.868237+00:00 a 01:31:25.402012Z, duração de 361,534 segundos, 13 amostras. A medição [continuous-throughput-pipeline.jsonl](../.data/gmgn-data/evidence/continuous-throughput-pipeline.jsonl) contou recibos confirmados nos dois destinos: 3,239 capturas/s e 413,56 fatos/s. Os aceites foram 3,327 capturas/s. A fila outbox passou de 18 a 51; havia oito interpretações analíticas pendentes nas duas pontas. Zero rejeições e worker ready durante as amostras.

A fronteira avançou 300 segundos enquanto transcorreram 361,534 segundos. O atraso passou de aproximadamente 6.133,49 a 6.195,03 segundos. Portanto, a medição não comprova catch-up nem operação sem atraso. Heartbeat saudável e baixa fila não significam que todo o histórico desde a origem já foi alcançado.

Ao final dessa janela, havia 8.459 interpretações v3 de origem legada confirmadas, 1.140.656 fatos e 12.501.779 observações de campos. Incluem as dez capturas importadas antes do início da ponte; não são 8.459 tokens distintos nem métricas economicamente homologadas. Endpoints sem adapter podem ter recibo de zero fatos e catálogo em quarentena explícita.

As métricas separadas mostraram uma iteração com 8.364 fatos e 18.396 campos: computação 342 ms, persistência PostgreSQL 5.722 ms e etapa analítica 2.020 ms; as durações se sobrepõem. A persistência continua relevante. O índice primário do catálogo, com chave textual por pointer, ocupava cerca de 1.730 MB; o índice categórico tinha 84 MB e nenhum scan observado. Isso orienta a próxima investigação, mas não autoriza remover índices sem comparar planos e contratos. DB-06.7 registra a revisão do catálogo e de suas consultas.

O script de backup atualizado passou também em 22 testes locais do PowerShell; esse gate valida o contrato do script, enquanto a restauração real tem evidência própria.

Captura de posts do X, estratégias, marcos por market cap e migração histórica integral continuam fora da implementação desta etapa. O próximo trabalho é estabilizar capacidade e crescimento físico, mantendo as provas de identidade, temporalidade, consulta e recuperação.
## Backup da versão atual

Backup completo entre 2026-09-09T01:38:27Z e 01:48:27Z, com API, worker e ponte retomados. Contém 10.173 capturas (9.805 legadas e 368 fixtures), 9.978 lotes publicados, 1.370.635 linhas no Lake, 14.374.176 observações de campos e 226 capturas ainda pendentes preservadas. O [manifesto](../.data/gmgn-data/backups/acceptance-microbatch-20260909/manifest.json) registra hashes e imagem Docker exata; não contém as configurações privadas.

O ID da imagem resolvido pelo Docker é sha256:302e912b8747f172c43374ac8124610032eb2ec43380fcad1a20013e71191c16. O valor ac61520d72a2a6f01e7972281a4fc5c85a823d0f94d2ebcc5f010930331e1f1d visto na saída do build é o digest da configuração. Não são duas versões diferentes do runtime. RestoreCheck usa o ID exato do manifesto.

Restauração dessa versão concluída com prova complementar de reconciliação no projeto midas-data-restore-microbatch. O gate estrito original falhou ao rejeitar a entrega de oito lotes que já estavam pendentes no backup; correção automatizada segue em DB-08.3. Detalhes e resultado qualificado abaixo.
## Restauração da versão atual — dados validados em 09/09/2026 UTC

Resultado: restauração dos dados aprovada por comparação integral e reconciliação explícita de entregas pendentes. Relatório: [restore-check-20260909T1159358782366Z.json](../.data/gmgn-data/backups/acceptance-microbatch-20260909/restore-check-20260909T1159358782366Z.json). A aplicação e o script de backup não foram alterados nesta etapa.

- Antes do rebuild: bancos, catálogo, sequências, snapshot 5931, raw e fatos exatamente iguais ao backup. Preservadas 10.173 capturas: 9.805 legacy_gmgn e 368 fixture; não são tokens distintos.
- Rebuild: 9.978 lotes PostgreSQL disponíveis reconciliados e conferidos nos dois destinos. ClickHouse: 1.369.613 fatos físicos e 9.978 recibos.
- Depois do rebuild: conteúdo operacional, raw, fontes e 226 capturas pendentes de normalização permaneceram iguais. As 14.374.176 observações de campos mantiveram contagem e fingerprint.
- O dump original comprova oito lotes já publicados no PostgreSQL com entrega analítica pendente. O rebuild acrescentou exatamente 204 fatos e oito recibos ao Lake, atingindo 1.370.839 linhas e snapshot 5939. Os fatos acrescentados correspondem integralmente ao conteúdo desses lotes no PostgreSQL.
- As 1.370.635 linhas originais continuam com o mesmo fingerprint no snapshot histórico 5931 e no estado atual excluídos somente os oito lotes novos. Zero linhas inesperadas, perdas, alterações ou duplicações nessa reconciliação.
- Quatro fixtures diretas conservaram checksums, recibos, snapshot compartilhado e consulta histórica; continuam corretamente fora do ClickHouse reconstruído. O total global do Lake inclui fixtures sem normalization_run e não deve ser igualado automaticamente ao total do ClickHouse.
- PostgreSQL, NATS e ClickHouse da réplica terminaram parados com exit 0 e sem OOM; todos os volumes foram preservados. PostgreSQL, API, worker e bridge da origem estavam saudáveis no fechamento; nenhum serviço da origem foi iniciado ou parado por este ensaio.

O gate automático original falhou porque exige igualdade absoluta da contagem/hash do Lake após o rebuild. Essa condição rejeitou a reposição legítima dos 204 fatos. A falha permanece em restore-strict-gate-failure.json; não foi reclassificada como PASS do script. O resultado aprovado usa a prova complementar exata em restore-lake-reconciliation.json, cruzada com restore-original-delivery-pending.json, além das comparações integrais antes/depois e das amostras em restore-fixtures-before.json e restore-fixtures-after.json. Esse caso foi incorporado ao verificador em DB-08.3, descrito abaixo, mantendo a rejeição de crescimento sem prova.

A validação retomada ocorreu entre 2026-09-09T11:19:04.0576329Z e 2026-09-09T11:59:35.7980684Z, duração 2431.74 segundos, incluindo a investigação dessa condição. Importação e cópia ocorreram na etapa anterior e têm registros próprios. Esta é uma prova local, sem estabelecer RTO, capacidade sustentada, retenção ou recuperação em outro host.


## Correção DB-08.3 — verificador automático, 09/09/2026

O RestoreCheck agora registra, antes do rebuild, quais lotes PostgreSQL já estavam publicados e ainda não tinham entrega analítica confirmada. O plano também preserva fingerprints dos recibos e dos metadados dos snapshots existentes. O helper do host é executado na imagem fixada no manifesto; nenhuma imagem, dependência, serviço de coleta ou estratégia foi alterada.

Depois do rebuild, o verificador exige igualdade integral do conteúdo operacional e raw. No Lake, exige que todas as linhas originais permaneçam iguais tanto no snapshot histórico quanto no estado atual, que os recibos e metadados anteriores sejam preservados, e que cada acréscimo pertença ao conjunto previamente pendente. Os fatos novos devem corresponder exatamente ao PostgreSQL em conteúdo, horário, identidade e contagem física, com um recibo válido e vínculo exato entre batch_id, checksum e snapshot. Lotes vazios exigem recibo; uma entrega pendente que já existia no Lake mantém o snapshot anterior. Nenhum aumento arbitrário, perda, alteração ou duplicação é aceito.

Validação: **40 casos locais passaram**, sem falhas ou skips: 26 contratos PowerShell e 14 regressões do reconciliador. Incluem o fluxo completo do RestoreCheck com substituições controladas, a ordem plano → rebuild → prova final e casos negativos de perda, duplicação, valor/horário alterado, metadados trocados e recibos incompletos.

Na réplica real preservada, o planejador leu o estado atual; o verificador final passou no replay histórico dos oito lotes e 204 fatos, incluindo cinco lotes vazios. O plano histórico desse replay foi reconstruído a partir do dump original e do snapshot preservado; as comparações gerais pós-rebuild reutilizam a evidência integral registrada anteriormente. Não foi executado um novo backup, uma nova importação ou um novo rebuild completo. A réplica encerrou com exit 0 e volumes preservados.

Evidências: [resultado final](../.data/gmgn-data/evidence/restore-gate-20260909/result-final.json), [prova de reconciliação](../.data/gmgn-data/evidence/restore-gate-20260909/restore-rebuild-reconciliation-final.json), [testes locais](../.data/gmgn-data/evidence/restore-gate-20260909/local-tests.json) e [encerramento](../.data/gmgn-data/evidence/restore-gate-20260909/cleanup.json). O relatório guarda os hashes do script e do helper usados.

DB-08.3 está concluído. A falha anterior permanece como evidência histórica; não foi reescrita como uma execução bem-sucedida da versão antiga. Próximo trabalho: DB-06.7, uma comparação isolada do custo do catálogo antes de qualquer mudança de índice; depois DB-06.4, comprovação de recuperação do atraso.
