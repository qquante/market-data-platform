# GMGN: teste de 3 s / 5 s — CP-DATA-SPEED-13

**Meta não atingida.** As rampas 3/5, 5/5 e 8/5 acumularam atraso. Perfil mantido: **Trenches 8 s / Trending 16 s**, com as melhorias descritas abaixo. Não foi aprovado um novo ritmo de coleta nesta rodada. O painel continua atualizando a cada 2 s.

## Resultado real e perfil mantido

Revalidação final: 2026-09-11T22:17:29.533687+00:00 → 2026-09-11T22:20:29.646674+00:00, 18 snapshots / 180 s programados, teto local de fila 60 s, sem novos erros da fonte ou dos destinos. Isto é uma janela curta de recuperação; não homologa dias de operação. A tabela usa somente capturas depois dos primeiros 60 s; pedido HTTP, normalização e confirmação histórica são relógios distintos.

| Fonte | Configuração | Respostas | Intervalo médio real | Mediana de normalização | Mediana de confirmação Lake + CH |
|---|---:|---:|---:|---:|---:|
| Trenches | 8 s | 15 | 7,999 s | 3,742 s | 25,372 s |
| Trending | 16 s | 7 | 15,943 s | 3,705 s | 28,717 s |

Cobertura das estatísticas: Trenches: 15/15 normalizadas, 15/15 arquivadas, 15 hashes distintos; Trending: 7/7 normalizadas, 7/7 arquivadas, 7 hashes distintos. Hash diferente não prova novidade econômica em cada campo. Não foi medido evento on-chain → GMGN.

Na revalidação, os picos de idade foram normalizer 5,254 s, lake 31,633 s, clickhouse 13,100 s. Filas ao fim do ensaio: normalizer: 0 pendências, lake: 20 pendências, clickhouse: 10 pendências.

## Rampas e resultados

| Início UTC | Perfil | Teto de fila | Resultado | Pico normalização | Pico Lake | Pico CH |
|---|---|---:|---|---:|---:|---:|
| 20:51:42 | 3/5 | 35 s | Interrompida: fila | 14,779 s | 43,094 s | 27,762 s |
| 20:53:20 | 3/5 | 35 s | Excluída: dívida inicial | 4,334 s | 71,270 s | 40,139 s |
| 20:56:25 | 3/5 | 35 s | Interrompida: fila | 19,889 s | 54,841 s | 13,883 s |
| 21:00:41 | 3/5 | 35 s | Interrompida: fila | 18,766 s | 39,236 s | 33,543 s |
| 21:07:02 | 3/5 | 35 s | Interrompida: fila | 48,513 s | 26,083 s | 11,402 s |
| 21:17:39 | 3/5 | 35 s | Interrompida: fila | 41,744 s | 21,244 s | 19,907 s |
| 21:26:47 | 3/5 | 35 s | Interrompida: fila | 40,691 s | 28,956 s | 37,518 s |
| 21:34:06 | 3/5 | 35 s | Interrompida: fila | 32,752 s | 42,169 s | 52,255 s |
| 21:43:29 | 3/5 | 35 s | Interrompida: fila | 27,278 s | 40,253 s | 45,797 s |
| 21:50:52 | 5/5 | 35 s | Interrompida: fila | 45,632 s | 23,775 s | 9,695 s |
| 21:56:41 | 5/5 | 35 s | Interrompida: fila | 32,225 s | 37,455 s | 44,593 s |
| 22:03:23 | 8/5 | 35 s | Interrompida: fila | 11,890 s | 40,186 s | 39,025 s |
| 22:09:47 | 8/5 | 60 s | Interrompida: fila | 28,789 s | 68,935 s | 74,289 s |
| 22:17:29 | 8/16 | 60 s | Completou | 5,254 s | 31,633 s | 13,100 s |

As primeiras tentativas pertencem a versões intermediárias e limites de recursos diferentes, detalhados no diagnóstico. A rodada de 20:53 começou com dívida anterior e não demonstra o efeito de CPU. A fonte não ganhou falhas HTTP em nenhuma rampa; houve falhas de entrega local durante recriações, descritas adiante. Os recuos automáticos preservaram a fila. Em 8/5, mesmo o ensaio adicional com teto de 60 s chegou a 74,289 s no CH e 68,935 s no Lake; o atraso cresceu, não foi apenas um pico isolado. Nenhuma nova configuração agressiva ficou ativa.

11/09/2026. Horários em UTC. Base local `midas-data-base`, API/painel 8791. As listas Trenches/Trending são o recorte desta meta; info, holders e OHLCV continuam na agenda individual compartilhando quota. Nenhuma frequência por lista garante cobertura de todas as famílias de cada token.

## O que foi alterado

- Lake: uma verificação global de identidade dentro da transação, inclusive no replay; removida a repetição da mesma leitura global após a transação. Permanecem leitura de volta, comparação de hashes/contagens/fontes/fronteira, recibo e snapshot de commit. O worker conserva sua posse exclusiva em PostgreSQL e cada destino serializa suas operações.
- Lake e ClickHouse executam em dois processos separados, um por destino. Cada processo conserva suas conexões; exceções voltam ao owner, sem repetir escritas ocultamente. Fechamento aguarda os filhos antes de liberar a posse. Nenhum novo serviço Docker ou pacote externo.
- Normalização: conservado um consumidor com parsing em thread, até oito mensagens por ciclo, 10.000 fatos / 16 MiB por grupo e 40.000 campos por captura. O marcador de publicação continua protegido pela barreira exclusiva PostgreSQL. Foram testadas duas entradas de pull e parsing em processos, mas o experimento não sustentou a meta e foi retirado da versão final.
- Encerramento: a entrega termina o grupo em andamento e conserva os seguintes na fila persistente. O worker tem até 120 s para encerrar e aguarda ambos os processos de destino, mesmo se um fechamento falhar. Uma recriação anterior terminou com 137 no limite antigo de 40 s; a prova seguinte, capturada por `docker wait`, terminou com código 0 em 16,477 s (inclui a recriação).
- Agenda: próximos prazos de Trenches/Trending são ancorados na agenda anterior. Jitter deixa de se acumular a cada ciclo; slots já perdidos são pulados, sem rajada de compensação. Persistência, retry e gate Redis continuam ativos.
- Recursos: worker passou de 1,5 para até 4 CPUs e de 1500m para 3 GiB. O limite de 1500m também produziu 559 eventos de pressão durante os lotes maiores, sem OOM. PostgreSQL 2 → 4 GiB; ClickHouse 2 → 3 GiB. Limites explícitos persistidos no ambiente privado e suportados pelo Compose. Aumento de memória aplicado sem reiniciar os bancos.
- PostgreSQL 18.6: `wal_compression=lz4` ativado por reload e registrado no Compose para próximos starts. `fsync`, `full_page_writes` e `synchronous_commit` permaneceram `on`. O reload revelou a configuração de arquivo antiga de shared_buffers=128MB conflitando com os 256MB já ativos por linha de comando; o arquivo foi alinhado aos mesmos 256MB, sem mudar a memória compartilhada ativa, removendo pending_restart. Não houve descarte de índices/fatos, desativação de WAL ou alteração de transação financeira.

## Diagnóstico que orientou a mudança

Trenches real usado no perfil de parsing: 765.537 bytes brutos, 4.495 fatos e 23.074 campos; saída 5.975.509 bytes. Parsing + cálculo de tamanho levou 1,160812 s **com cProfile**; esse valor não inclui SQL e não equivale à latência de produção. O perfil revelou custo de criação de modelos, catálogo e serialização.

Somente aumentar a CPU ou separar processos não sustentou 3/5. A investigação de PostgreSQL observou 193 sessões ativas ao longo de 30 amostras: 135 esperas `WALWrite`, 2 `WalSync`, 24 `BufferContent`, 26 de barreira advisory, 4 de leitura e 2 sem espera. Uma consulta chegou a 3,806 s na janela. São observações repetidas de sessões, não 193 requisições únicas. EXPLAIN da publicação mostrou uso de `facts_capture_idx`; não se presumiu scan total.

O PostgreSQL acumulava 768.817 eventos de limite de memória e o ClickHouse 12.018, sem OOM. Esses contadores são cumulativos, não frequência instantânea. O WAL acumulado desde 10/09 tinha cerca de 141,9 GB e 19,15 milhões de imagens de páginas; a compressão foi escolhida para reduzir essa escrita. Não se atribui um percentual de ganho à compressão isoladamente: a rodada seguinte também ampliou a memória dos bancos.

## Experimentos descartados

Duas lanes de normalização, tanto com threads quanto com processos, foram implementadas e testadas isoladamente. Nenhuma sustentou 3/5; a versão com processos também reprovou 5/5. Foram removidas do runtime final. Os testes de IPC daquela tentativa são evidência de comportamento, não evidência de ganho ou parte da versão entregue.

Sonda CH read-only sobre os mesmos 4.096 IDs reais: grupos de 256 levaram 1,213 s; 512 via corpo multipart, 1,139 s; 1.024, 1,248 s. 2.048 atingiu o limite de leitura (307) e 4.096 recebeu erro 1000. Não foi adotada essa mudança: ganho pequeno/inconsistente e limites preservados. A consulta global CH e seus grupos de 256 continuam intactos.

As amostras anteriores têm cerca de 4.542 fatos por resposta Trenches e 4.400 por Trending. Somente essas duas listas a 3/5 projetariam aproximadamente **2.394 fatos/s ou 207 milhões de fatos/dia**, antes de enriquecimentos e das cópias/índices de armazenamento. É projeção pelos tamanhos da amostra, não volume sustentado observado nem garantia de payload constante. A obtenção HTTP dos bytes e a disponibilização de todo o histórico são etapas diferentes.

## Segurança e limites do ensaio

Watchdog finito: snapshots a cada 10 s, retorno ao perfil anterior se surgir falha nova na origem, erro de destino ou três falhas de monitoramento. Nas primeiras rampas, duas observações consecutivas com qualquer fila acima de 35 s interrompiam o teste, mesmo que a fila acima do teto fosse diferente entre amostras. O ensaio adicional de estabilidade 8/5 adotou teto de 60 s explicitamente: não foi uma aprovação pelo critério anterior de 35 s. Nenhuma dessas idades é limite de quota da GMGN. Verificação inicial impede iniciar outra rampa com fila antiga acima de 25 s. Uma tentativa anterior à inclusão dessa verificação começou com dívida antiga e foi excluída das comparações.

Não existe watchdog permanente nem automação horária. As janelas curtas não homologam dias de operação, retenção/crescimento ou atraso evento on-chain → GMGN. D1–D5 e estratégias seguem abertos. Três quarentenas `invalid_unicode` já existiam no início. Duas adicionais de `token_info`, às 21:07:35 e 21:59:25 UTC, surgiram durante a rodada: NUL nas respostas originais, brutos preservados e cálculo bloqueado. Total: cinco quarentenas. Preservação do bruto não significa aceite semântico desses campos.

## Referências técnicas

[PostgreSQL 18, compressão e durabilidade do WAL](https://www.postgresql.org/docs/18/runtime-config-wal.html), [Python 3.13, threads/GIL](https://docs.python.org/3.13/library/threading.html), [Python, processos e spawn](https://docs.python.org/3.13/library/multiprocessing.html), [NATS, contrato do consumidor pull](https://github.com/nats-io/nats.docs/blob/master/nats-concepts/jetstream/consumers.md). O suporte a LZ4 foi confirmado no `pg_settings` do binário real antes da alteração. Nenhuma atualização experimental de banco/Python foi instalada.

## Próxima mudança necessária

Nenhum timer novo aprovado implica que basta liberar mais quota. O formato atual transforma cada token da lista em dezenas de linhas de fatos, repetindo metadados e mantendo índices/cópias em PostgreSQL, Lake e ClickHouse. A próxima fatia deve medir e implementar snapshots agrupados por token/captura, preservando campos, tipos/qualidade, Decimal, bruto, relógios, identidade e replay, com projeções compatíveis para pesquisa. Comparar os mesmos dados e recibos antes de migrar consumidores. Isso ainda não foi implementado; não declarar compressão lógica ou estratégias prontas.

Até essa revisão, recuar frequência no ambiente privado usando 8/16/1.3075, 10/20/1.22 ou 25/60/1 conforme as filas. Aplicar somente o coletor com `docker compose -p midas-data-base --env-file .data/gmgn-data/base-runtime.env -f compose.data.yaml up -d --no-deps collector`. Não remover volumes, jobs, fatos ou recibos. O backup privado anterior à rodada é `.data/gmgn-data/base-runtime.before-speed35-20260911.env`; não publicar esse arquivo. Retorno às imagens antigas não faz parte do procedimento de frequência.

A compressão WAL foi ativada com ALTER SYSTEM/reload e também está no comando do Compose. Uma reversão específica deve alinhar os dois, mantendo durabilidade ligada. `shared_buffers` permanece em 256MB. Os limites de memória/CPU do ambiente foram ampliados para o teste; são tetos, não reservas de uso constante. Nenhuma automação recorrente, executor financeiro ou cadeia foi operada.

## Validação e estado final

- 91 casos da suíte de analytics/identidades/Lake passaram. A prova SQL do Lake usa tabelas DuckDB isoladas e metadados de snapshot substituídos; não é uma simulação de carga GMGN. O runtime real foi verificado separadamente por hashes, brutos e recibos.
- 27 testes da versão final de worker/processos de destino/grupos passaram. Incluem ausência de retry oculto, falha de filho, serialização por destino, encerramento aguardando os filhos e parada antes de outro grupo. As duas lanes e seus testes experimentais foram retirados do código final.
- 25 testes de coleta/transporte/prazos passaram; 2 testes PostgreSQL reais passaram em schemas privados (COPY, conflitos, replay, publicação e grupos concorrentes). As execuções se sobrepõem; não somar suas contagens como casos únicos. A suíte intermediária de 164 casos teve 2 integrações ignoradas, executadas separadamente no PostgreSQL real.
- Chromium real/headless: 17 verificações, desktop/mobile, sem escrita, sem erro JS/HTTP 5xx, fonte/cobertura separadas e acesso local sem chave. Intervalos observados: 1,997 e 1,991 s.
- Quatro capturas antigas conservaram bruto SHA-256, interpretações e recibos exatos após reinícios. Outras quatro foram verificadas no encerramento. O runtime coincide com os oito hashes de código do workspace; NATS conserva o durable normalizer-v1, ACK explícito e teto 32.
- Entrega local: contador de falhas 20 → 35; recreações da API geraram indisponibilidade temporária. No snapshot final: last_error=None, spool pendente=0. Isso não é HTTP 429 da GMGN.
- Universo no snapshot final: 64131 identidades. Ritmos de listas não são cobertura individual de todas as famílias; lacunas continuam visíveis no dashboard. Cinco respostas token_info estão em quarentena por NUL, duas novas nesta rodada.

Quota local final: 1,3075 pesos/s, descoberta 0,4375, saldo de 0,87 para enriquecimento; gate Redis compartilhado confirmado. A configuração local não é prova de quota contratual do provedor. API/worker `midas-data-app:speed35-v6-20260911`; coletor `midas-data-collector:speed35-v2-20260911`. Os seis serviços da base estão descritos em runtime.json. Não foram reexecutados testes financeiros nem homologação global D1–D5.

[Evidências da execução](../outputs/gmgn-speed35-20260911/README.md). O relatório anterior [SPEED_RESULTS.md](SPEED_RESULTS.md) permanece como registro histórico de outra janela; não comparar seus números como se o tamanho da base, os recursos e a carga concorrente estivessem congelados.
