# Migração contínua e histórico GMGN — S-DB-20260908-02

Medições históricas obtidas em investigação somente leitura em 08/09/2026, aproximadamente 22:45–22:55 UTC: catálogo PostgreSQL, planos sem ANALYZE e três amostras distintas de no máximo 200 metadados. A revisão documental posterior confronta essas observações com `midas_data/bridge.py` efetivamente implementado nesta sessão. Não atualiza as medições nem afirma que o histórico foi migrado.

Nesta revisão foram lidos arquivos locais e alterado somente este documento. Nenhum request GMGN, consulta nova ao banco, teste, DDL, mudança de rede, start/stop, backup, ingestão ou operação Git foi executado. Ativação e evidências de runtime pertencem ao coordenador; os resultados de aceitação são registrados separadamente em [BRIDGE_ACCEPTANCE.md](tests/BRIDGE_ACCEPTANCE.md).

A ponte implementada aproveita a evidência já persistida pelo coletor legado. Ela lê metadados de chamadas `success` e `error`, recupera individualmente o bruto elegível dos sucessos e entrega à base nova com o call_id original. Chamadas `error` são registradas como `unavailable/source_error_without_body`; não são transformadas em uma resposta HTTP original inexistente. Endpoints sem adapter podem ter bruto aceito e catálogo de campos, mantendo `unsupported_endpoint` e nenhum fato interpretado. Não há garantia de CDC, fronteira histórica definitiva ou recuperação de um corpo que a origem não armazenou. Referências: `bridge.py:55–70`, `:259–309`.

## 1. Fatos observados e tamanho

| Item | Evidência desta leitura |
|---|---|
| Origem | Container midas-fomo-postgres-1; banco midas_fomo; PostgreSQL 18.6 |
| Identidade de cluster | pg_control_system.system_identifier = 7679995080186957864 |
| Relógio lido | 2026-09-08 22:52:16.728691 UTC |
| Rede da origem | midas-fomo_default |
| Redes da API nova | midas-data_data e midas-data_ingress |
| gmgn_api_calls | reltuples ~1.839.411; n_live_tup ~1.908.314; duas estimativas, não contagens exatas |
| Tamanho de calls | 1.503.993.856 bytes total; 1.170.423.808 tabela/TOAST; 333.570.048 índices |
| gmgn_payload_blobs | reltuples ~1.243.257; n_live_tup ~1.257.253; estimativas |
| Tamanho de blobs | 20.581.826.560 bytes total; 20.420.829.184 tabela/TOAST; 160.997.376 índice |
| Soma física destas duas tabelas | 22.085.820.416 bytes, cerca de 22,1 GB decimais; não é o tamanho total do GMGN nem o volume de exportação |
| Extremos success via índice | 02/09/2026 14:20:15 UTC até 08/09/2026 22:49:58 UTC, nas leituras feitas |
| Estatística de calls | autoanalyze em 08/09 14:13 UTC; ~125.540 modificações desde análise |
| Estatística de blobs | autoanalyze em 08/09 17:49 UTC; ~47.964 modificações desde análise |

O catálogo está defasado enquanto a captura continua. A diferença entre reltuples e n_live_tup não é um intervalo estatístico de confiança. Tamanho de tabela inclui layout, TOAST, índices e espaço recuperável; não equivale à soma dos bytes compactados, ao custo de transferir todos os calls ou à expansão futura de fatos/catálogo.

Amostras recentes, deliberadamente pequenas e distintas:

- Amostra A: 200 success em 43,108 s, aproximadamente 4,64 chamadas/s, com 180 referências a blob e 16 endpoints.
- Amostra B: 200 success, 188 referências a blob; soma por chamada de 8.635.404 bytes raw e 1.732.745 bytes compactados; maior raw 811.843 bytes; maior compactado 200.412 bytes; p50/p95 compactado 1.440/37.630 bytes.
- Amostra C: 200 success distribuídos em 17 endpoints. Hot_searches: 2/2 sem chain; market_rank: 12/21 sem blob; trenches: 8/14 sem blob; token_kline: 42 chamadas.
- pg_stats estimava 19 endpoints e null_frac de payload_sha256 ~7,94% na tabela inteira, com estatísticas antigas.

Amostras recentes não representam uniformemente seis dias de histórico. Não multiplicar a média recente pelo número estimado de linhas para prometer tamanho, duração ou cobertura. Uma chamada não equivale a um blob distinto: várias chamadas podem referenciar o mesmo hash.

## 2. De onde vêm os bytes e os relógios

O Module de captura legado utiliza GmgnOpenApiClient no caminho de execução observado (forensics.py:1777). O Transport padrão lê bytes da resposta HTTP e a evidência retém result.body (openapi.py:331–343, 520–527). GmgnForensicStore calcula SHA256, comprime zlib e guarda blob/call na mesma transação (forensic_store.py:596–646). A ponte deve preservar esses bytes e verificar os dois hashes, sem remontar JSON como substituto da evidência.

Received_at_utc é registrado depois da chamada ao provedor, antes de normalização local, compactação, aquisição de conexão e commit. Por exemplo, collect_trenches normaliza os itens antes de record_success_call (forensics.py:565–626). A tabela gmgn_api_calls não tem created_at, inserted_at, sequência de ingestão ou commit_at (forensic_schema.sql:26–49).

O pool tem command_timeout=90, max_size=8 (forensic_store.py:304–310). Esse timeout limita comandos; não fornece limite total entre recebimento e commit. Pode existir espera anterior à transação e entre etapas da aplicação.

Configuração observada da origem:

| Parâmetro | Valor |
|---|---|
| wal_level | replica |
| track_commit_timestamp | off |
| transaction_timeout | 0 |
| idle_in_transaction_session_timeout | 0 |
| max_replication_slots / max_wal_senders | 10 / 10 |
| Slots do banco consultado | Nenhum retornado |
| Papel usado no diagnóstico | Superuser e replication=true; permissões amplas, embora todas as consultas tenham sido read-only |

Uma amostra de pg_stat_activity mostrou uma transação com cerca de 39 ms. Isso não prova um limite máximo histórico: não vê trabalho antes de pool.acquire, nem impede que outra transação fique aberta depois.

A nova base deve distinguir:

- requested_at/received_at originais: relógios do coletor legado.
- source_seen_at: quando a ponte observou a linha pela primeira vez.
- captured_at/recorded_at/available_at novos: persistência/publicação na base nova.
- Interpretação, seus normalizer_version/contract_version e momento de disponibilidade.
- Um eventual commit LSN/commit timestamp futuro: indisponível neste caminho atual.

Não usar received_at da origem como available_at da base nova nem como evidência de consumo por estratégia.

## 3. Por que um cursor temporal simples perde linhas

Exemplo concreto:

1. A recebe resposta às 10:00:00 e espera para persistir.
2. B recebe às 10:00:01, confirma primeiro e é lida pela ponte.
3. A ponte avança para a chave de B.
4. A confirma depois, com received_at menor que o cursor, e desaparece de um polling estritamente crescente.

Acrescentar UUID ao cursor resolve empates de ordenação; não corrige ordem de commits. Uma janela sobreposta de 10 minutos cobre atrasos dentro dessa janela, mas não prova um limite que o sistema não possui.

READ COMMITTED vê o que já estava confirmado no início de cada consulta; consultas seguintes podem ver linhas que a anterior não via. Manter uma transação REPEATABLE READ longa também não resolve o contínuo e pode reter um snapshot velho. [PostgreSQL: isolamento](https://www.postgresql.org/docs/18/transaction-iso.html).

Xmin não é um log de commits com índice/cobertura durável. Sequências, mesmo se adicionadas futuramente, não dão por si só ordem de commit. Pg_xact_commit_timestamp não ajuda no estado atual: o rastreamento está desligado; habilitá-lo não produziria informação retroativa completa, e esses registros têm retenção limitada. [PostgreSQL: informações de transação](https://www.postgresql.org/docs/18/functions-info.html).

## 4. Três lanes implementadas e cobertura condicional

Forward + recent + audit têm estado persistido separado dentro do mesmo progresso, com turnos por página e um escritor ativo por origem. O lock de sessão impede escritores simultâneos da ponte de produção. A cobertura continua condicionada à retenção e à capacidade de revisita (`bridge.py:94–135`, `:200–220`).

| Lane | Estado persistido e finalidade |
|---|---|
| Forward | Avança em janelas de até 60 s e páginas de até 50 metadados, keyset recebido/UUID, com limite de leitura no relógio da origem menos 2 s. Só uma janela esgotada atualiza forward_until. |
| Recent | Cada ciclo fixa seu limite superior em forward_until; percorre os 10 minutos anteriores a esse limite, respeitando origin. Portanto não significa necessariamente os últimos 10 minutos do relógio atual quando forward está atrasado. |
| Audit | Cada ciclo fixa a faixa de origin até forward_until observado no começo do ciclo. Termina essa faixa e depois inicia outra; não persegue um limite móvel durante o ciclo. Recupera commits atrás dos outros cursores enquanto as linhas seguem retidas. |

Cada janela usa intervalo temporal semiaberto, com desempate estável por call_id. O cursor de página permanece dentro da janela até esgotá-la. Reinício usa estado durável, nunca apenas a última chave em RAM. Timeout/erro na consulta não é janela vazia.

A cobertura eventual é condicional para linhas que:

1. tenham status success/error e received_at dentro do escopo desde origin;
2. já estejam confirmadas quando uma consulta alcançar seu ponto temporal;
3. continuem retidas até uma revisita efetiva; a cobertura de bruto exige também corpo válido ainda disponível;
4. sejam alcançadas por lanes que consigam continuar progredindo sob os limites e falhas existentes;
5. recebam ACK validado e receipt confirmado, ou resultado unavailable/rejected registrado antes do checkpoint.

Um ciclo completo significa faixa revisitada, não todos os commits encerrados. A condição publicada pelo código é `received_at_from_origin_revisited_not_commit_complete`. Backpressure pausa todas as lanes; falha de entrega mantém a mesma página/turno pendente. Logo a alternância por página não garante prazo de revisita ou progresso diante de conflito persistente.

Uma linha pode confirmar após a ativação com received_at anterior a origin. Ela está fora desse escopo. Antecipar origin com um warmup captura casos comuns, mas não substitui o backfill nem cobre atraso pré-origin arbitrário.

Nenhuma janela vira definitiva. Para cada ciclo, publicar início, limite superior fixo, último checkpoint, conclusão e idade. O alvo não deve correr para frente durante o mesmo ciclo; isso pode impedir que ele termine. Pausas por backpressure, falhas, retenção e déficit de capacidade aumentam o prazo de revisita e devem ficar visíveis.

Estado implementado: binding/fingerprint da origem, origin, lane/at/after/end, cycle_end/cycle_started_at/cycles/last_cycle_at, forward_until, heartbeat, último erro, contadores accepted/unavailable/rejected e accepted_raw_bytes. O pulso expõe lane, resultados da página, relógio da origem, forward_lag_seconds e a declaração condicional de cobertura. Idade de ciclo, timeouts por motivo, bytes pendentes, vazão de blobs e latências devem ser derivados ou acrescentados à observabilidade; não tratá-los como todas métricas já instrumentadas.

## 5. ACK, ledger e cobertura real

A classe Bridge concentra paginação, verificação, ledger e retomada. PgSource lê a origem; verify_row é compartilhado com o importador de arquivo. Essa fronteira permite testar ACK perdido, commit atrasado e retomada com fonte/transporte substituíveis, sem duplicar a validação de bytes no contínuo e no futuro backfill.

O ledger usa source_name + call_id, fingerprint do descritor, status de origem, resultado, tentativas, motivo e clocks. Antes do checkpoint da página, a ponte exige HTTP 202 com capture_id/hash/bytes/duplicate coerentes e confirma o receipt accepted no PostgreSQL novo. Uma falha após aceite HTTP, mas antes do receipt/checkpoint, relê a página; a mesma identidade pode retornar duplicate=true. Capturas já aceitas com fingerprint igual usam o ledger sem reler o blob. Esses commits não são uma transação distribuída (`bridge.py:229–309`, `:342–357`).

Os resultados de não entrega são distintos:

- `unavailable`: erro de origem sem corpo (`source_error_without_body`) ou success sem payload_sha256 (`source_body_not_retained`). Nenhum bruto é fabricado.
- `rejected`: inconsistência verificável de metadados/hashes, blob referenciado ausente, codec/limite indevido ou mudança do fingerprint da origem. Chain ausente só é inválida para adapters de token ou metadados explicitamente conflitantes; não é rejeição genérica de uma captura bruta global.
- Falha de transporte, HTTP 409/outro status diferente de 202 ou ACK inconsistente: exceção, pulso degraded e **nenhum avanço daquela página**. Retenta o mesmo ID/corpo/metadados; não converte a falha em rejeição permanente nem passa para a página seguinte. Linhas anteriores já confirmadas na página são reconhecidas pelo ledger ao repetir. Falhas consecutivas têm backoff e limite para exigir reconexão do processo (`bridge.py:297–306`, `:379–440`).

Unavailable/rejected com fingerprint inalterado voltam a ser avaliados nas revisitas. Mudança do descritor conserva o fingerprint original e registra source_identity_changed: a ponte não aceita silenciosamente a identidade mutada. Um HTTP 409 persistente exige diagnóstico/reconciliação; retries não resolvem por si só uma incompatibilidade de metadados.

Reconciliação de calls já confirmados lê somente metadados e consulta o ledger novo. Não buscar novamente todos os blobs em cada lane. Comparar identidade/hash/descritor evita tratar alteração da origem como duplicata inocente. Não deduplicar calls diferentes apenas por payload_sha256.

Há uma lacuna observada antes da ponte: o legado deliberadamente não guarda todo bruto de trenches/market_rank. RAW_PAYLOAD_MIN_SECONDS varia de 30 s a 3.600 s conforme a série (forensic_model.py:30–37; forensics.py:470–487). Nesses casos response_sha256 pode existir sem payload_sha256. O hash não permite reconstruir o corpo; relacionar outro call de mesmo token não repõe essa evidência.

A cobertura tem denominadores distintos:

| Medida | Denominador correto |
|---|---|
| Chamadas examinadas | Success + error dentro da faixa recebida desde origin e da política declarada |
| Bruto disponível | Chamadas examinadas com corpo original válido, ainda retido, verificável e dentro dos limites; não todas as tentativas |
| Bruto aceito na base nova | Brutos disponíveis entregues e confirmados |
| Interpretação suportada | Capturas aceitas cujo endpoint/shape/versão possui Adapter |
| Fatos verificáveis | Fatos efetivamente emitidos e reconciliados por versão |
| Lacunas de entrega | Unavailable e rejected separados por motivo; retries/409 pendentes separados de rejeições; nenhum removido do denominador |

Chamadas status=error entram na paginação e no ledger da implementação atual, mas o legado registra seu erro sem blob da resposta (forensic_store.py:650–693). Portanto contabilizar o erro não comprova retenção de seu corpo. PAGE_SQL combina duas pernas limitadas (success e error), ordena o conjunto por recebido/UUID e entrega no máximo 50 linhas. Outros valores de status, se existirem, não fazem parte desse contrato. Não declarar captura de todas as tentativas ou completude de bruto a partir apenas do ledger.

## 6. Contratos da versão de partida e estado implementado

As lacunas iniciais foram confrontadas com o código atual; o que depende de operação permanece um gate separado:

- Transporte e interpretação agora têm escopos distintos. RawLegacyRow estende o contrato limitado do importador para endpoint string e chain nullable. Adapters interpretativos continuam somente token_info/market_rank (`bridge.py:80–83`; [FIELD_CONTRACTS.md](FIELD_CONTRACTS.md)).
- Chain NULL de hot_searches pode ser aceito como **bruto sem chain**; não recebe namespace global/unknown. CaptureMetadata exige cadeia não nula para I/R, Fact.chain permanece não nula e unsupported_endpoint retorna catálogo antes de qualquer operação de chain. request_params.chain null é preservado; string explícita deve ser coerente com metadata.
- Capture_id permanece o call_id original. Os imports I/R usam a política anterior de parâmetros para conservar a mesma metadata; a ponte amplia a allowlist apenas em outros endpoints. Alteração silenciosa de request_params/evidence_kind/chain pode provocar HTTP 409 e bloquear a página até reconciliação.
- SAFE_PARAMS considera chaves permitidas na raiz/query/body, com orçamento do objeto de origem. Campos como from/to/resolution/horizon_seconds e listas de wallets podem ser conservados nos endpoints ampliados. Headers, credenciais e request arbitrário completo não são exportados. O fingerprint inclui política/statuses/binding/origin; alterar configuração incompatível impede retomada silenciosa (`bridge.py:35–51`, `:188–193`, `:285–287`).
- O normalizador cataloga nós bounded antes de retornar unsupported_endpoint. Aceitar bruto sem fatos ainda gera FieldObservation e trabalho de persistência; os limites de payload/nós/saída continuam aplicáveis.
- Erro de origem, falta de corpo, tamanho e inconsistência têm resultado individual no ledger; não desaparecem por filtro posterior ao LIMIT. O recorte SQL continua explicitamente success/error.
- A medição inicial usou papel privilegiado. O runtime atual exige usuário midas_data_reader, valida identidade do cluster/read-only e rejeita atributos elevados; a configuração desse papel e a prova de permissões reais pertencem ao coordenador. Esta revisão não criou papel nem verificou permissões live (`bridge.py:360–414`).

## 7. Índices, custo e alternativas

Na leitura histórica de 08/09/2026, EXPLAIN sem ANALYZE confirmou a consulta success então proposta:

- Consulta success, janela de 60 s, keyset recebido/UUID, ordem crescente e LIMIT 50: Index Scan Backward em gmgn_api_calls_success_time_idx e Incremental Sort.
- O índice parcial existente tem somente received_at_utc DESC. UUID é desempate com filtro/sort; o índice série/endpoint não substitui um índice global de commit.
- Blob por hash: Index Scan usando gmgn_payload_blobs_pkey.
- O plano estimou cerca de uma linha para a janela recente, apesar da vazão observada. Estatísticas antigas limitam a precisão do custo estimado; não foi medido tempo por EXPLAIN ANALYZE.

Na implementação atual, cada perna success/error tem LIMIT 50 antes da união; a página final também tem LIMIT 50, sem JOIN de blobs. A elegibilidade dos índices da consulta ampliada tem evidência de fixture em BRIDGE_ACCEPTANCE.md, distinta do plano medido historicamente no legado. Não promover aquela leitura success-only a benchmark da consulta atual.

O limite de 50 metadados não implica 50 corpos lidos: accepted iguais usam ledger, error/corpo não retido registram unavailable, e demais casos leem no máximo um blob por vez. Limite por bruto: 4 MiB descomprimidos, além do limite compactado. A conexão da origem pode ficar aberta, mas cada transação READ ONLY termina antes da entrega HTTP. Runtime configura statement_timeout 5 s, lock_timeout 1 s e idle_in_transaction_session_timeout 5 s, com SQL limitado (`bridge.py:146–167`, `:398–404`).

Audit repetido custa O(chamadas desde origin) em metadados por ciclo. O custo cresce com a idade da ponte. A fronteira fixa permite concluir cada ciclo; não fornece por si só SLA de revisita. Aumentar concorrência sem medir pode disputar I/O e pool com o coletor.

O guard de admissão implementado roda antes de ler outra página (`bridge.py:169–185`, `:311–324`):

- **Queue 500:** soma dois probes limitados a 500 linhas cada — outbox não consumido e normalization_runs disponíveis ainda não aplicados aos destinos. A soma pode chegar a 1.000 e pausa quando >=500. Não é um COUNT completo, nem necessariamente 500 chamadas distintas ou 500 batches; não converter esse número em segundos de entrada.
- **Raw 32 GiB:** orçamento padrão de accepted_raw_bytes desta ponte/origem, com **reserva de 200 MiB por página** (50 × 4 MiB). Pausa quando bytes já contabilizados + reserva excederem o orçamento; mínimo de configuração é 200 MiB.
- Bytes são contabilizados conservadoramente na primeira aceitação da captura; chamadas distintas com mesmo blob continuam contando, e mutação posterior não reduz o orçamento. Esse contador não mede espaço físico único, uso total do projeto, disco livre ou bytes atualmente pendentes.
- Pausa publica queue_backpressure ou raw_admission_budget e não lê página/blob nem avança lanes. Não existe DELETE/retention automático neste guard. Orçamento atingido exige decisão operacional; não libera espaço por inferência.

Esses limites são proteções iniciais, não capacidade comprovada. Medir tamanho de campos/fatos, latência de normalização/sinks, atraso forward e idade dos ciclos; o histórico só permanece recuperável enquanto a origem conservar o que precisa ser relido.

| Alternativa | Parecer concreto |
|---|---|
| Exportação HTTP existente | Não encontrada rota de bruto/calls paginados nos routers GMGN inspecionados. As rotas de token/status/agregados não substituem o histórico bruto. |
| Script gmgn-data-import.ps1 | Já exporta por SELECT read-only, mas limita aos 100 success recentes, seleciona até 10 I/R e não tem cursor contínuo (linhas 31–87). Reutilizar verificação, não esse recorte como backfill. |
| Polling timestamp + overlap finito | Simples, mas perde commits além do overlap. |
| Polling com audit cíclico persistente | Implementado nesta etapa, com cobertura condicional, ledger e custo explícitos; ativação/medição em registros do coordenador. |
| Commit timestamps | Desligados e não retroativos; não resolvem histórico completo. |
| CDC lógico | Alvo possível para ordem de commit e menor reread; indisponível sem mudar wal_level e criar configuração/slot apropriados. Não habilitado nesta investigação. |
| Replicação física | Não entrega por si só o contrato de calls para a ponte; replicar cluster inteiro não é requisito desta etapa. |

Slots lógicos exigem operação própria e retenção monitorada; também podem repetir dados após crash, portanto o ledger idempotente continua necessário. Não criar slot como simples consulta read-only: ele retém recursos na origem. [PostgreSQL: logical decoding](https://www.postgresql.org/docs/18/logicaldecoding-explanation.html).

## 8. Plano de backfill gradual

O histórico anterior a origin **não foi migrado por esta investigação/revisão**. Continua como plano de trabalho com estado e orçamento próprios, reutilizando o verificador/ledger. A coleta contínua não deve retroceder seu cursor para absorver milhões de calls. Audit só revisita desde a origin configurada; não comprova backfill dos dias anteriores.

1. Congelar o escopo do primeiro plano: identidade da origem, menor received_at observado e limite superior de planejamento; começar por um intervalo recente fechado por conveniência, sem chamá-lo definitivo.
2. Produzir manifesto de cobertura de metadados em janelas curtas, com counts por endpoint, disponibilidade de corpo e rejeições. Paginar por chave, sem OFFSET, sem count full, sem exportar tudo para RAM.
3. Piloto proposto de até 1.000 calls ou 64 MiB compactados, o que ocorrer primeiro, incluindo I/R e endpoints sem interpretação; orçamento separado e menor que o contínuo. Medir bytes, raw distintos, campos/fatos por captura, vazão sustentável e impacto na origem.
4. Expandir gradualmente para uma hora, depois um dia, conforme métricas reais. Recibos e checkpoints por página; parar em backpressure, erros de integridade ou identidade divergente.
5. Verificar amostras por endpoint, igualdade de hashes, total de calls no ledger da faixa e contagens por versão no destino. Um replay de interpretação preserva a versão anterior.
6. Repassar as faixas planejadas em reconciliação posterior. Sem retenção formal e cursor de commit não há instante no qual a faixa possa ser declarada imutável por inferência.

Referência de custo apenas de metadados: ~1,9 milhão de calls corresponde a ~38 mil páginas de 50. A uma página/s, aproximadamente 10,6 h; a duas páginas/s, aproximadamente 5,3 h. São contas hipotéticas sem blobs, normalização, concorrência, espera, lanes compartilhadas ou retries, não uma previsão de duração da migração.

Não transferir todo o banco de ~22,1 GB para obter a primeira evidência. O tamanho físico de blobs inclui dados compartilhados e overhead; medir o piloto antes de dimensionar espaço da base nova. As capturas distintas continuam distintas mesmo quando RawStore reutiliza o mesmo corpo.

Não foi localizada rotina de DELETE/TRUNCATE das duas tabelas nos caminhos examinados; isso não é uma política de retenção nem garantia contra manutenção externa. Cobertura anterior ao primeiro registro visível e blobs nunca persistidos permanecem desconhecidos.

## 9. Gates de aceitação da ponte

1. Identidade: origem correta e escopo persistido, sem alias postgres ambíguo, sem carregar módulos executores legados.
2. Ordenação: teste controlado com commit atrasado além do recent, inserção atrás de cursor de página e retomada de audit; exercitar pela Interface, em origem de teste.
3. Durabilidade: queda após ACK e antes de checkpoint, HTTP incerto e fonte temporariamente indisponível; nenhum salto silencioso ou segunda captura.
4. Cobertura: success+error com ledger distinto de unavailable/rejected; bruto sem chain aceito apenas sem adapter; oversize/hash inválido/unsupported explícitos. HTTP 409/ACK incerto mantêm página pendente, sem rejeição permanente ou cadeia/corpo fabricados.
5. Custo: queries limitadas, um blob por vez, alternância de lanes enquanto páginas concluem, pausa/retomada sob queue 500 e orçamento raw 32 GiB com reserva 200 MiB; distinguir limites de vazão efetivamente observada.
6. Proveniência: valores/bytes/clocks preservados, metadados compatíveis com os imports existentes e estado separado entre bruto aceito, interpretação e recibo de ambos os destinos.

Esta revisão confirmou o comportamento acima por leitura do código e corrigiu o plano; não executou a ponte, testes ou migração histórica. O status operacional e os PASS de integração devem ser lidos nos registros atualizados do coordenador e em BRIDGE_ACCEPTANCE.md, sem transformar constatação static-only em prova de execução. FIELD_CONTRACTS.md permaneceu intacto.
