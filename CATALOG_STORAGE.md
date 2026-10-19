# CP-CATALOG-01 — catálogo compacto e espera NATS

Implantado em 09/09/2026. Owner A0, sem subagentes. Imagem `midas-data-app:catalog-v1-20260909`, ID `sha256:4e9718d277956aea1d16c9aa641b9bec8cebe88c9ac181c091b689be767cfc75`. Normalizador `gmgn-p0-v4` preservado. API e worker atualizados; coletor e destinos mantidos ativos. Revisão/documentação encerrada em 2026-09-09T20:52:00.916389+00:00.

## Entrega

Cada nova interpretação grava o catálogo completo em **um pacote JSONB por captura/versão**, na tabela `field_catalog_batches`, com compressão nativa TOAST do PostgreSQL. O pacote conserva ordem, pointers, categorias, tipos, qualidade, motivos e `contract_id`, inclusive nulos. Os fatos numéricos e seus índices continuam na tabela `facts`; não foram substituídos por JSON.

A migration `008` apenas acrescenta a tabela. **Nenhuma linha histórica foi removida, reescrita ou reprocessada em massa.** `field_observations` continua legível; `/v1/catalog` escolhe capturas e interpretações publicadas antes de expandir os pacotes. Consulta conserva filtro de fonte/captura, limite de até vinte capturas, limite de resposta, contagens e `has_more`/`truncated`. Pacote tem precedência se os dois formatos existirem para o mesmo run, evitando contagem dupla.

A escrita de fatos, catálogo e run continua na mesma transação. Publicação temporal e ACK permanecem posteriores ao commit, com o mesmo hash canônico de conteúdo. Duplicação exata não cria outro pacote. Pointers repetidos são rejeitados antes de escrever, preservando a restrição que antes vinha da chave por campo. Limites de bytes/fatos/campos e o proprietário único de escrita permanecem ativos.

Referências oficiais consultadas: [PostgreSQL 18 — TOAST](https://www.postgresql.org/docs/18/storage-toast.html), [funções JSON](https://www.postgresql.org/docs/18/functions-json.html). A compressão não exige um framework novo. O formato físico mudou; a interpretação econômica não mudou.

## Medição comparável

Benchmark no PostgreSQL real, **em tabelas temporárias isoladas**, repetindo cinco vezes os 22.929 campos de uma resposta Trenches já armazenada. A representação antiga foi copiada com seus índices; a nova usou chave por captura/versão e JSONB. As duas consultas retornaram conteúdo idêntico e o pacote completo foi comparado campo a campo.

| Medida da amostra | Linhas por campo | Pacote por captura |
|---|---:|---:|
| Espaço, incluindo índices/TOAST | 33,349,632 bytes | 2,940,928 bytes |
| Gravação das cinco cópias | 440.5 ms | 303.6 ms |
| Leitura/agrupamento dos cinco catálogos | 97.1 ms | 184.5 ms |

**Redução de 91,18% no espaço do catálogo desta amostra.** Não significa 91% de redução de toda a base nem devolução do espaço histórico. A leitura dessa amostra aumentou cerca de 87 ms; por isso a expansão continua restrita às capturas selecionadas. A medição é uma execução controlada com conteúdo repetido, não uma distribuição p95, garantia de velocidade ou ensaio de carga diária. Fatos, WAL, brutos, índices de fatos e destinos têm custos próprios.

## Timeout identificado e correção

No `nats-py 2.15.0` instalado, a espera `fetch(batch=8, timeout=1)` pode lançar `builtins.TimeoutError` quando o prazo restante vence antes da segunda solicitação. O worker só tratava explicitamente `nats.errors.TimeoutError` nessa fronteira. A exceção da espera subia como falha da etapa, provocando recuo de dois segundos.

Reprodução: cem polls em stream privado e vazio não reproduziram espontaneamente a variante built-in. Ao injetar prazo restante vencido no relógio do cliente instalado, com NATS real, ela foi reproduzida em `nats/js/client.py`, `_fetch_n`, linha 1289. O teste do worker falhou antes da correção e passou depois.

Agora somente a chamada `consumer.fetch` trata os dois tipos como espera sem mensagem e incrementa `fetch_timeouts`. Falhas de conexão do broker, leitura e persistência continuam falhas; testes comprovam que timeout de gravação preserva retry e não aciona processamento individual. O diagnóstico do normalizador informa operação (`broker`, `fetch`, `read_manifest`, `read_raw`, `normalize`, `persist`); falhas que escapam da etapa também registram duração. Não expõe resposta da GMGN, SQL com parâmetros ou credenciais.

**Limite da conclusão:** a fronteira errada foi reproduzida e corrigida. Os logs históricos não tinham stack/operação suficiente para atribuir retrospectivamente cada timeout a ela. Antes do rollout o contador acumulado já era 11; o valor 3 do checkpoint anterior era uma fotografia mais antiga. Contadores do worker reiniciam com seu processo; “zero após o rollout” não apaga o histórico.

## Provas executadas

- Três regressões falharam antes da mudança: deadline built-in, duplicação de pointer e representação por pacote.
- **62 testes focados aprovados**, zero falhas/erros/skips: contratos de lote, imutabilidade, publicação, retries/ACK, espera NATS e integração PostgreSQL/NATS em schema/stream privados.
- **2 testes integrados de ingestão em lote aprovados após o rollout**, com fixtures identificadas: captura grande, catálogo limitado/truncado, precisão, destinos e rollback por conflito.
- Compatibilidade: respostas HTTP completas do catálogo de três capturas antigas, salvas antes/depois, **idênticas**.
- Dados reais novos: **23.736 campos**, nos três endpoints, iguais ao reprocessamento dos respectivos brutos; hashes canônicos de interpretação preservados.
- Destinos reais: **três lotes/4.958 fatos**, hashes de bruto, contagens, checksums e recibos concordantes em DuckLake e ClickHouse, com Lake aberto read-only na conferência.
- Migration 008 aplicada com checksum conferido; seis serviços saudáveis. Coletor sem falhas de origem; **270 capturas reais prontas e 22 fixtures** no último status, filas e spool zerados, worker sem falhas desde a troca.

Durante uma leitura intermediária havia uma captura em trânsito com aproximadamente um segundo de idade. O status final conferiu seu processamento e filas vazias; não confundir trabalho momentâneo com atraso crescente. A janela pós-rollout é curta, não novo aceite de sessenta minutos.

## Operação e continuidade

`python scripts/gmgn-data-capacity.py` e `.\scripts\gmgn-data.ps1 capacity` incluem a tabela nova automaticamente no detalhamento PostgreSQL. O contador lógico de campos no script de evidência de compactação foi atualizado para ler os dois formatos sem contagem dupla. Consultas SQL manuais diretamente a `field_observations` veem apenas o histórico antigo; o catálogo pela API vê ambos.

O rollout foi aditivo. Um worker antigo ainda pode escrever o formato antigo, mas uma API anterior a esta versão não lê os novos pacotes; portanto uma reversão operacional deve preservar o leitor compatível com ambos. Não houve mudança de fonte, intervalo, chave, estratégia ou execução financeira.

Próximo recorte: fechar a fonte/contrato de preço e market cap para o acompanhamento persistente de tokens, usando métricas verificadas. Ainda faltam os contratos econômicos explicitamente pendentes, política de acompanhamento e curvas/marcos; não bloquear a coleta atual nem promover aliases por coincidência numérica. Migração/compactação do catálogo histórico, se desejada, é separada e não necessária para o ganho nas novas capturas.

Evidências em `.scratch/checkpoint-catalog-20260909/`: `benchmark.json`, `nats-repro.json`, `red.log`, `focused.xml`, `bulk.xml`, `catalog-before.json`, `catalog-after.json`, `new-packets.json`, `replay-result.json`, `physical-result.json`, snapshots de capacidade, `final-status.json`, `services.json`, logs de build/rollout e manifest de hashes. Falhas iniciais do harness (nome da factory da API e sintaxe do probe) foram corrigidas antes das provas finais; não foram falhas da base.
