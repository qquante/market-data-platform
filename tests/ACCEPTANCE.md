# Aceitação independente — base GMGN via Docker

Data: 2026-09-08. Owner: A6 QA. Estado: aceitação concluída no escopo da fundação local; resultado final em ../RESULTADOS.md e matriz abaixo.

Escopo: receber, preservar, normalizar, consultar, replicar e recuperar dados GMGN. Sem estratégias, trades, UI ou chamadas externas. Os cenários abaixo são o plano de aceitação; a matriz final distingue o que passou, o que foi exercitado parcialmente e o que permanece pendente.

## Regras da prova

- Executar somente no projeto Docker `midas-data`, com volumes e credenciais exclusivos. Falhas induzidas, restaurações e limpezas devem usar instâncias/volumes de teste explicitamente identificados, nunca o legado.
- Usar fixtures sintéticas determinísticas, claramente marcadas `source_kind=fixture`. Evidência legada importada possui cenário próprio e permanece identificada como reserializada quando o corpo original não está disponível.
- Registrar versão da imagem/schema/normalizador, horário UTC, IDs das capturas/lotes, comandos, contagens, hashes, fronteiras e resultado real. Credenciais e corpos privados não entram no relatório.
- Distinguir rejeição do envelope, quarentena do payload e falha transitória de infraestrutura. Nenhuma dessas condições pode aparecer como sucesso analítico silencioso.
- Só marcar PASS com a evidência de comportamento. Teste unitário isolado não comprova recuperação Docker, persistência após restart ou restauração de backup.

## AC-01 — isolamento, limites e superfície local

**Comportamento:** os componentes operam em Docker sem depender do processo host e sem alterar serviços ou volumes legados.

**Preparação/estímulo:** registrar inventário resumido dos contêineres legados e do projeto novo; inspecionar configuração resolvida sem imprimir segredos; subir exclusivamente o projeto novo; chamar saúde e uma consulta local. Tentar ingestão sem a credencial exigida pela implantação e uma requisição acima do limite de corpo.

**Esperado:** portas publicadas apenas em loopback; volumes exclusivos; limites explícitos de memória/conexões/lotes/tempo/corpo; saúde distingue processo vivo de destinos indisponíveis; acesso sem credencial é recusado; excesso é recusado antes de processamento pesado. Consultas GET não iniciam captura externa. Imagens e extensões têm versões registradas. Nenhum serviço legado é reiniciado/modificado.

**Prova Docker:** configuração sanitizada, inspeção de redes/volumes/port bindings, status HTTP e comparação do inventário antes/depois. Segurança de rede e recursos é verificação real, não apenas leitura do YAML.

## AC-02 — envelope, relógios e origem

**Comportamento:** envelopes inválidos são recusados; timestamps e proveniência válidos persistem sem serem reinterpretados.

**Preparação/estímulo:** enviar timestamps UTC e offsets equivalentes; depois enviar datetime sem timezone, requested_at posterior a received_at, origem/evidence_kind incompatíveis, status HTTP inválido e metadados com chave sensível. Enviar fixture e captura marcada como legado reserializado com relógios históricos.

**Esperado:** instantes válidos são normalizados a UTC mantendo equivalência; source_event_at, requested_at, received_at, captured_at e available_at não são colapsados. Casos inválidos têm motivo explícito e não produzem fatos aceitos. Uma fixture nunca aparece como dado GMGN real. Importação não apresenta reserialização como resposta HTTP original. As combinações permitidas de origem/evidência precisam estar definidas no contrato, não deduzidas pelo consumidor.

**Prova Docker:** respostas da API e leitura das linhas persistidas por IDs, incluindo igualdade dos instantes e proveniência em PostgreSQL e destinos analíticos. Logs sanitizados demonstram ausência dos valores sensíveis inseridos no caso negativo.

## AC-03 — bruto lossless e precisão ponta a ponta

**Comportamento:** a captura preserva bytes originais e os valores numéricos sem float intermediário.

**Preparação/estímulo:** enviar um corpo com Unicode, espaços, escapes, notação exponencial, inteiro acima de 2^53 e decimal com mais de 38 dígitos; incluir um JSON inválido em captura separada. Guardar bytes/hash esperados fora da aplicação.

**Esperado:** leitura do bruto devolve os mesmos bytes e SHA-256. O JSON inválido permanece como evidência identificada e não vira fatos válidos. Métricas reconhecidas preservam valor numérico exato em value_text nos três destinos, ainda que a representação decimal canônica difira do lexema original; o lexema original continua no bruto. Não há arredondamento universal para Float64/Decimal(p,s). Caminhos e nomes de arquivos são gerados pela aplicação, sem path traversal por metadados.

**Prova Docker:** comparação binária/hash após recepção e após restart; comparação por Decimal de precisão explicitamente suficiente nos fatos PostgreSQL/DuckLake/ClickHouse. A comparação de precisão não deve usar float nem o contexto Decimal padrão insuficiente.

## AC-04 — identidade, conflito e concorrência

**Comportamento:** repetição de uma captura não duplica efeitos; capturas distintas com corpo igual continuam distintas.

**Preparação/estímulo:** enviar duas vezes e concorrentemente o mesmo capture_id, corpo e metadados; repetir o capture_id com corpo diferente e, em outro caso, metadados semanticamente diferentes; enviar o corpo original com novo capture_id.

**Esperado:** repetição idêntica mantém a identidade e os mesmos fatos lógicos. Reuso incompatível do ID produz conflito explícito sem sobrescrever evidência/manifesta/fatos. Novo capture_id gera outra observação, mesmo com hash de corpo igual. Falha/retry não cria duas avaliações do mesmo estado de captura nem dois lotes operacionais efetivos. O fingerprint de idempotência documenta os campos incluídos e exclui apenas valores gerados pelo servidor.

**Prova Docker:** respostas concorrentes, cardinalidade por capture_id/fact_id/batch_id, hash original intacto e consulta aos recibos. Verificar identidade lógica do ClickHouse, sem exigir que merges físicos já tenham ocorrido.

## AC-05 — semântica, desconhecidos e quarentena

**Comportamento:** categorias e fatos não atribuem significado sem contrato suficiente.

**Preparação/estímulo:** payload reconhecido com price, market cap reportado, supply e endereço em papéis conhecidos; campos opacos, objetos aninhados, listas, null, zero, false e string vazia. Incluir endereço de wallet em papel diferente do token. Exercitar limite de profundidade/quantidade com payload adversarial finito.

**Esperado:** null não vira zero; false não vira missing; endereço em papel desconhecido não identifica token por suposição. Market cap derivado não nasce sem supply/unidade homologados. Campo opaco é observado como unverified, com pointer e motivo; o bruto preserva todos os campos. Excesso de limites resulta em rejeição/quarentena explícita, não catálogo truncado silenciosamente. Mesma captura + versão produz IDs e resultado determinísticos, independentemente do número de retries.

**Prova Docker:** catálogo de campos e fatos comparados ao gabarito da fixture, issues persistidos e contagem completa de folhas dentro do limite. Casos acima do limite devem aparecer no painel/consulta de qualidade e não no conjunto de fatos válidos.

## AC-06 — consultas temporais, atraso e disponibilidade

**Comportamento:** uma consulta as-known não utiliza dado que ainda não estava disponível no corte informado.

**Preparação/estímulo:** ingerir observação de preço com horário de evento antigo depois de um corte de disponibilidade; ingerir correção/revisão suportada pelo modelo sem apagar a anterior; produzir captura cujo commit de fatos existe mas available_at ainda não foi registrado; consultar antes/depois do marcador e perto da virada UTC. Exercitar limites de página, janela e timeout.

**Esperado:** consulta utiliza ambos os relógios necessários, não só occurred_at. Fatos sem available_at ficam excluídos até reconciliação. O marcador recuperado é conservador e não retroage para simular disponibilidade histórica. Importação de dado antigo não se torna conhecimento disponível no passado. Curva respeita o dia UTC, preserva ordem, lacunas e versão de interpretação. Consultas limitadas devolvem cursor/limite explícito; número excessivo de linhas ou janela excessiva é recusado ou limitado de modo documentado.

**Prova Docker:** respostas com cortes fixos e leitura da proveniência que explica inclusão/exclusão. Comparar PostgreSQL com projeções somente dentro da fronteira publicada comum; atraso do sink deve ser visível, não interpretado como ausência de mercado.

## AC-07 — falha de processo, outbox e retomada

**Comportamento:** falhas entre gravações não geram perda silenciosa nem sucesso prematuro.

**Preparação/estímulo:** em ambiente de teste, interromper o escritor/worker em pontos controlados: bruto gravado antes do manifesto; manifesto/outbox confirmado antes da publicação; publicação antes de registrar recibo; fatos confirmados antes de available_at. Indisponibilizar temporariamente apenas o NATS do projeto novo e depois restaurá-lo.

**Esperado:** captura aceita tem evidência durável; arquivos órfãos ficam identificados/reconciliáveis. Outbox pendente reaparece e progride depois da retomada. Redelivery é tolerado. Marcadores operacionais só avançam depois dos efeitos correspondentes. A fila e a idade do item mais antigo tornam a indisponibilidade visível. A prova declara a janela de persistência configurada do broker e não confunde confirmação de publicação com fsync por mensagem ou alta disponibilidade.

**Prova Docker:** falhas reais ou failpoints explicitamente registrados, reinício dos contêineres próprios e reconciliação de conjunto de capture_ids/fact_ids esperado. Um teste que apenas relança a função sem interromper o processo não basta para classificar este cenário como PASS completo.

## AC-08 — aplicação parcial e replay dos destinos

**Comportamento:** DuckLake e ClickHouse convergem após aplicação parcial, sem exigir transação distribuída.

**Preparação/estímulo:** aplicar lote, simular queda entre confirmação do primeiro destino e segundo destino, e depois entre confirmação de ambos e recibo operacional. Reentregar o mesmo lote. Tentar mesmo batch_id com conjunto/conteúdo incompatível. Incluir lote maior que uma página de verificação.

**Esperado:** recibo DuckLake e fatos pertencem ao mesmo commit; ClickHouse verifica identidade única e conteúdo completo sem depender somente da janela finita de dedup. Reentrega converge em contagem/hash/conteúdo; batch_id incompatível é erro, nunca recibo antigo de sucesso. Verificação pagina o conjunto inteiro, não uma amostra. Recibo operacional só conclui após ambos os destinos estarem verificados; fronteiras independentes continuam consultáveis.

**Prova Docker:** snapshot ID do lake, recibos e hashes por lote, contagem completa por destino e logs de retomada sem credenciais. Comparação precisa conferir valores, clocks e provenance, além de apenas contar fact_ids.

## AC-09 — primeira evidência real importada com limite

**Comportamento:** uma importação pequena demonstra utilidade com dados reais sem modificar o legado ou inventar fidelidade.

**Preparação/estímulo:** após autorização operacional do A0, importar recorte explícito, com limite de linhas/tempo/bytes, por credencial/transação read-only da origem. Não disparar chamadas GMGN. Repetir o recorte.

**Esperado:** filtro e IDs de origem são registrados; importação é retomável e idempotente com identidade estável da captura importada. Relógios históricos permanecem separados de captured_at/available_at atuais. JSON reserializado usa evidence_kind=legacy_reserialized; não é denominado corpo HTTP original. A consulta local demonstra bruto, campos, fatos e qualidade de pelo menos uma captura real, incluindo limites de cobertura. Não há escrita ou mudança de serviços na origem.

**Prova Docker:** configuração read-only sanitizada da transação, recibo limitado, contagens/IDs comparáveis entre execuções e proveniência navegável. Ausência de credencial/acesso seguro deixa este cenário pendente; fixtures não o substituem.

## AC-10 — backup, restauração independente e rebuild

**Comportamento:** o conjunto autoritativo volta a operar a partir de backup coerente em volumes separados.

**Preparação/estímulo:** criar capturas e ao menos um lote confirmado; executar procedimento documentado de backup com coordenação de escritores/checkpoints; restaurar em volumes com novos nomes e identidade de teste. Não apagar volumes primários. Reconstruir projeção ClickHouse a partir do conjunto autoritativo; verificar a estratégia de restauração/reconstrução de outbox e broker.

**Esperado:** backup inclui PostgreSQL operacional, catálogo DuckLake, arquivos Parquet referenciados, bruto/manifestos e versões necessárias. Expiração/GC não invalida arquivos durante a cópia. Capturas/fatos/recibos e snapshots referenciados retornam coerentes; nenhuma credencial é exposta no manifesto de evidência. Projeção reconstruída converge nas fronteiras verificadas. Procedimento declara RPO/RTO medidos e limites, incluindo ausência de HA em host único.

**Prova Docker:** execução real de restore em volumes isolados, hash dos brutos, consultas ao snapshot do lake, IDs/conteúdo/checksums dos fatos e teste de nova ingestão após restauração. Apenas criar um arquivo de backup ou reiniciar o mesmo volume não prova recuperação.

## Evidência executada em Docker

O A0 executou a suíte completa no contêiner de verificação e informou exit code 0: **166 casos aprovados**, sendo **146 unitários, 18 HTTP e 2 de recuperação com PostgreSQL real**. A6 escreveu/revisou os testes e validou a coleta local; a execução Docker foi realizada pelo A0. A saída concisa foi informada pelo coordenador, sem atribuir a A6 uma execução que não realizou.

Arquivos executáveis: [test_integration.py](test_integration.py) e [test_recovery.py](test_recovery.py). Depois desse resultado foi acrescentado **um caso HTTP de /v1/clock + identidade EVM canônica**, ainda pendente do último build/run neste registro. A coleta atual dos arquivos QA identifica 19 HTTP + 2 recovery; ela não transforma o novo caso em PASS.

### Prova operacional de interrupção e retomada

O [procedimento de recuperação](../../scripts/gmgn-data-recovery.ps1), executado pelo A0, produziu [o recibo local](../../.data/gmgn-data/recovery-2079eb5d-9aea-4ec3-ac25-d9d2523848d3.json), lido por A6 em modo read-only.

- Execução registrada: **2026-09-08T21:53:17.4174077Z**.
- Origem: fixture; capture_id: **2079eb5d-9aea-4ec3-ac25-d9d2523848d3**.
- A captura foi aceita e confirmada como pending com o worker parado.
- O procedimento aplicou SIGKILL à API do projeto novo, reiniciou seu NATS e retomou API/worker.
- Resultado: ready, um normalization_run, um fato e repetição identificada como duplicate=true.
- SHA-256 do bruto recuperado: **0949f9c810d380a9e37fc03d6b9d71b5bde645d952fcadc18580e08083aa69ce**.
- DuckLake e ClickHouse verificaram um fato, origem fixture e o mesmo checksum de lote; snapshot DuckLake **37**, com escopo batch_commit.
- Checksum confirmado nos dois destinos: **aeaff01c5405b46e0ea7652a6e6093d908bddb4619c0b52145097d06774e45d4**.

Isso prova o cenário descrito, incluindo retomada de uma captura aceita e ainda não normalizada. Não prova queda de energia do host, perda do disco, crash em todos os intervalos entre duas gravações ou restauração de backup.

## Matriz de resultado atual

| Caso | Resultado | Prova e limite atual |
|---|---|---|
| AC-01 | PASS no escopo local | Rede/volumes/portas inspecionados, API em localhost, cinco serviços saudáveis, autenticação e limites de query; HTTP413 para corpo acima de4MiB sem criar captura. |
| AC-02 | PASS | Contratos/UTC/origem em fixtures; importação original real verificada separadamente em AC-09. Não presume semântica econômica não documentada. |
| AC-03 | PASS no recorte exercitado | Precisão acima de38dígitos, bytes, unknown/null/false/zero, hash, quarentena e leitura após interrupção/restore. |
| AC-04 | PASS | Concorrência/idempotência/conflito, replay imutável, interpretação posterior sem dupla contagem e histórico preservado. |
| AC-05 | PASS nos adaptadores v2 | Inclui correção comprovada de envelope duplo e enum. Qualidade unverified/invalid/missing permanece explícita; não homologa toda a GMGN. |
| AC-06 | PASS no recorte implementado | UTC do banco, barreira real, cortes históricos, paginação, canonicalização e escolha da interpretação elegível. Consultas de pesquisa continuam futuras. |
| AC-07 | PASS no cenário executado | SIGKILL API + restart NATS recuperaram captura pending, bruto e recibos. Não extrapolar para toda janela de crash ou perda do host. |
| AC-08 | PASS no cenário executado | ClickHouse parado após commit lake: PG pendente/degraded; retomada automática, um fato nos destinos, raw igual e duplicate=true. |
| AC-09 | PASS na amostra limitada | Dez capturas/41.609 bytes originais com10 hashes; origem READ ONLY. Após v2,10 ready e20 runs v1/v2 conferidos nos sinks. |
| AC-10 | PASS em volumes separados | Restore final midas-data-restore-v2:121 lotes,1.269 fatos históricos,97 raw, snapshot122; fingerprints PG/catálogo/raw/lake iguais e CH reconstruído. |

## Contratos resolvidos e limites da entrega

1. Captura usa fingerprint do envelope canônico + hash do bruto, responde 409 em conflito e preserva capturas distintas mesmo quando compartilham bytes.
2. Normalização compara conteúdo imutável da mesma captura/versão. Fixtures mantêm a origem nos fatos e recibos, e queries exigem source_kind.
3. A disponibilidade dos fatos é publicada com lock transacional exclusivo e relógio PostgreSQL; leituras temporais usam lock compartilhado durante a consulta READ COMMITTED. A prova recovery observou o leitor real esperando em pg_locks e confirmou que o corte anterior permanece estável após publicar a captura seguinte.
4. Rotas atuais cobrem recepção, bruto/proveniência, status, catálogo limitado às capturas recentes, observations e as-known. /v1/clock foi acrescentado para fornecer o corte UTC do banco; seu novo teste público estava pendente no último resultado informado.
5. Backup/restore, importação real e cobertura de falhas parciais entre destinos têm controles próprios e só avançam com seus respectivos recibos.
6. Os PASS acima descrevem comportamento funcional em fixtures e nos cenários executados. Não são benchmark de capacidade, atestado de alta disponibilidade nem homologação das 14 consultas de pesquisa.

Nenhum teste QA acessou provedores externos ou classificou fixture como GMGN real. O A0 controla builds, serviços e execução operacional.

## Fechamento final do A0 — 2026-09-08

A matriz acima substitui os estados parciais dos marcos anteriores. Suíte final:234 passed,11 skipped,5 subtests no Docker; os11 casos PowerShell passaram no host com10 subtests. Os22 testes de integração (19 HTTP +3 recovery) estão incluídos. /v1/clock, canonicalização e reprocessamento com um único fato no corte atual passaram. Registros, digests, amostra real e limites estão em [RESULTADOS](../RESULTADOS.md).

Importação original:10 capturas/41.609 bytes, todos os hashes iguais. Reprocessamento real conservou10 runs v1(476 fatos) e criou10 runs v2(696 fatos), com recibos de ambos os destinos;10 capturas ready. Não tratar todos esses fatos como medidas econômicas válidas ou independentes.

AC08: [.data/gmgn-data/partial-recovery-acceptance-20260908.json](../../.data/gmgn-data/partial-recovery-acceptance-20260908.json). AC10: [restore final v2](../../.data/gmgn-data/backups/acceptance-v2-20260908/restore-check-20260908T2222000767210Z.json),121 lotes verificados, PG/catálogo/bruto/lake iguais e ClickHouse reconstruído. As duas réplicas de teste foram paradas preservando volumes; origem5healthy. Não houve teste de queda de energia/perda do host nem benchmark da massa histórica inteira.