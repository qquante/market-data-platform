# CP-COLLECTOR-01 — coletor direto contínuo

> Atualização 10/09/2026: a agenda persistente foi implantada nas etapas 3.1–3.4. Política e estado atuais em [TOKEN_TRACKING.md](TOKEN_TRACKING.md); a descrição abaixo registra o checkpoint anterior.

**Ativo no Docker local**, somente leitura. Checkpoint verificado em 2026-09-09T19:28:44Z. Owner A0, sem subagentes. O coletor fica ligado após a entrega. Não depende do PostgreSQL legado e não contém executores ou chaves privadas.

## Perfil habilitado

| Job | Cadência | Seleção |
|---|---:|---|
| Trending / `market_rank` | 60 s | Solana, janela 5m, até 10 pedidos, ordem swaps decrescente |
| Trenches | 60 s | Solana, três categorias, limite solicitado 2 em cada uma; resposta efetiva pode exceder |
| `token_info` | 60 s | Um endereço por vez, alternando posições entre até dez endereços do último Trending |

Os candidatos de detalhes vencem após dois intervalos sem um Trending novo. Trata-se de enriquecimento amostral: não é acompanhamento persistente de todos os tokens, nem mantém automaticamente tokens que saíram do ranking. O ranking pode mudar a cada minuto, portanto a rotação por posição não garante cobertura individual completa. Os filtros explícitos e os horários são preservados. Defaults do provedor e universo não observado não são inferidos.

Uma única thread consulta a origem, com orçamento ponderado compartilhado de uma unidade de peso por segundo entre os três jobs, sem burst no restart. Cadência e tamanho de resposta/fila são configuráveis; o perfil atual usa 60 s. Esse orçamento cobre este coletor, não outras máquinas/consumidores da mesma conta GMGN. Não foi criado Redis ou outro framework. Coleta de primeiros segundos, streams, X/posts e estratégias não fazem parte deste checkpoint.

## Persistência e recuperação

1. Reserva da tentativa/UUID e próximo horário ficam persistidos antes da chamada.
2. Sucesso do provedor conserva bytes originais, hash, endpoint, chain, query/body e relógios UTC. Antes de qualquer envio, a resposta entra numa fila SQLite local, com WAL e `synchronous=FULL`, em volume nomeado Docker. É buffer de transporte temporário; PostgreSQL e os destinos continuam autoridades da base.
3. Um ciclo independente envia a captura à API com o mesmo UUID. Falha deixa bruto, metadados, tentativas e retry na fila; não faz outra chamada GMGN para repetir a entrega.
4. Em ACK incerto, consulta a captura pelo UUID, verifica identidade/hash e metadados, normalizando apenas a representação UTC dos relógios para comparação. Conflito HTTP 409 não é convertido em sucesso.
5. Somente após recibo de aceitação durável válido, a remoção da fila e o avanço do contador/último recibo ocorrem na mesma transação. O bruto definitivo já está na API; normalização, DuckLake e ClickHouse avançam independentemente depois.

Um lock de processo impede dois proprietários do mesmo volume. Conexão SQLite única e lock local serializam as escritas dos dois ciclos. O ciclo de origem não espera pelo envio à API; a entrega não depende de novas consultas ao provedor. Shutdown aguarda a chamada em andamento e a gravação; volumes e agendamentos sobrevivem à recriação do container.

Falha antes do commit local não é uma captura confirmada. Tentativa interrompida é sinalizada na reabertura; a resposta perdida antes de ficar durável não pode ser recuperada retroativamente por este polling. Slots de polling perdidos são contados, sem inventar backfill. Não se afirma cobertura contínua de eventos ocorridos entre consultas.

## Controles

- **4 MiB por resposta HTTP**, configurável; excesso é erro explícito e não se transforma em captura parcial.
- **512 MiB de bytes brutos + metadados pendentes**, configurável, com reserva para a próxima resposta. Não é limite de histórico acumulado nem número máximo de capturas. Ao atingir o orçamento, novas consultas aguardam; a entrega continua esvaziando a fila.
- Proteção por espaço livre real: menor valor entre filesystem do spool e filesystem do host, preservando 4 GiB mais margem da escrita. SQLite tem overhead próprio e reutiliza páginas liberadas; esse orçamento não promete devolver imediatamente ao host todo espaço já alocado.
- HTTP 429 pausa todos os jobs e respeita o maior valor entre reset informado e cooldown conservador de cinco minutos. HTTP 401/403 pausa por uma hora; falhas ficam visíveis para corrigir credencial/conta. Outros erros têm backoff por endpoint. Reiniciar não remove o cooldown.
- Container do coletor: usuário 65532, 256 MiB de RAM, 0,5 CPU, filesystem da imagem read-only, um secret de API GMGN montado read-only. Nunca recebe chave privada. Logs locais limitados a três arquivos de 10 MiB.
- Ausência da variável explícita `DATA_COLLECTOR_LIVE=1` impede iniciar chamadas. Ela está habilitada no arquivo privado da base para este perfil autorizado.

`GET /v1/status` inclui `collector`: liveness, heartbeat, frescor por endpoint, tentativas/falhas, próximos horários, ciclos perdidos, pendências/idade/retries, recibo de aceitação e espaço. Processo vivo, origem atualizada e projeções atualizadas são sinais distintos. A saúde Docker do coletor indica processo/heartbeat; não homologa campos nem comprova cobertura completa. O comando CLI antigo de status do banco permanece com seu resumo anterior; o campo novo está na rota HTTP e no arquivo de status do coletor.

Cabeçalhos completos de resposta ainda não são campos consultáveis no manifesto da API. Os erros de rate limit alimentam o cooldown persistente; status e corpo original continuam rastreáveis nos registros existentes.

## Validação executada

- **34 testes focados aprovados**: fila durável, API indisponível, recibo divergente, bytes exatos, limite HTTP, ACK incerto e conflito, cooldown compartilhado/reabertura, candidatos vencidos, persistência que falha, ciclos perdidos e status fresco/stale.
- **Prova integrada Docker com fixtures e brutos existentes:** três capturas continuaram a ser persistidas enquanto uma conexão real à API estava indisponível no runner. Reabertura conservou IDs e bytes. Perda simulada de ACK após commit real foi reconciliada. Três capturas e 4.749 fatos chegaram aos dois destinos. Nenhuma chamada GMGN nessa prova; fixtures separadas por `source_kind`.
- **Operação real:** oito ciclos por fonte, 24 capturas novas aceitas, zero falhas e zero slots perdidos nos contadores. Reinício real preservou os 12 aceites já existentes naquele momento e o agendamento; ciclos posteriores avançaram normalmente.
- **Integridade real:** 27 brutos/lotes (24 novos mais três anteriores), **44.948 fatos**, hashes/checksums/recibos concordantes em DuckLake e ClickHouse. Seis serviços saudáveis, spool e filas de processamento zerados no checkpoint.

O probe antigo de auditoria tentou conferir todo o histórico em um grupo e atingiu o limite de leitura de 10.000 fatos do helper. Foi ajustado para verificar lotes completos separadamente. Isso foi falha do probe, não do processamento contínuo; os limites da aplicação não foram aumentados nem removidos.

Na amostra de recursos, coletor consumia 28,45 MiB de RAM e 0,19% de CPU. É fotografia momentânea, não pico certificado. Janela curta de 415,973 s: +5.725.336 bytes brutos capturados (~825.823 bytes/min). Espaço livre físico caiu 328.359.936 bytes no intervalo; esse delta inclui outras atividades do filesystem e não atribui todo crescimento ao banco. Ao final da amostra: ~363,65 GiB livres. Não extrapolamos capacidade diária ou prazo até lotar com essa janela curta.

## Operação

No PowerShell, a partir do repositório:

```powershell
Set-Location -LiteralPath 'C:\Users\PC\Desktop\Midas'
$composeArgs = @('--project-name','midas-data-base','--env-file','.data/gmgn-data/base-runtime.env','-f','compose.data.yaml')
docker compose @composeArgs ps
docker compose @composeArgs exec -T collector cat /var/lib/midas/collector/status.json
```

Pausar apenas a origem, preservando fila e dados: `docker compose @composeArgs stop collector`.
Retomar o perfil configurado: `docker compose @composeArgs up -d --no-deps collector`.
A API/worker continuam processando independentemente. O serviço de bridge legada não foi iniciado. Não remover o volume `midas-data-base_collector` para reiniciar.

## Próximo checkpoint

Atualização: [CP-CAPACITY-01 concluído](CAPACITY_FIELD_REVIEW.md), com medição por armazenamento, revisão econômica e amostra física adicional. A seção abaixo registra a transição original.

Medir crescimento atribuível a cada armazenamento e cobertura/atraso em uma janela representativa deste perfil, sem subir a taxa por suposição. Depois, fechar contratos econômicos pendentes e definir acompanhamento persistente por token antes de curvas/marcos. Este recorte não certifica 60 minutos, capacidade ilimitada ou todos os dados homologados.

## Artefatos

`src/midas_gmgn/data_collector.py`, `data_platform/midas_data/collector_status.py`, integração em API/settings e `compose.data.yaml`, `Dockerfile.data-collector` com contexto de build restrito, testes e evidências em `.scratch/checkpoint-collector-20260909/`. Controles na raiz atualizados. Dados/volumes existentes preservados; sem migrations novas, restauração de histórico ou execução financeira.

Imagens finais:

- `midas-data-app:collector-v1-20260909`: `sha256:f5f338cdbbb7dcea0b6df30ce5a0f2ae599a3d02ddc3690dd1b12aa1d4935409`.

- `midas-data-collector:direct-v1-20260909`: `sha256:4a598fd880922c608a02c2f49469c4cd5991b647cdc90cb9a8c6d53fe6aa90d3`.

Referência de pesos/cooldown: [documentação oficial GMGN Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md). Papéis das fontes: [MARKET_SOURCES](MARKET_SOURCES.md); limites e contexto: [TIME_RESPONSE_BUDGET](TIME_RESPONSE_BUDGET.md).

Atualização CP-CATALOG-01: API/worker agora usam `midas-data-app:catalog-v1-20260909`; perfil/imagem do coletor permanecem iguais. Catálogo compacto, compatibilidade e provas em [CATALOG_STORAGE](CATALOG_STORAGE.md).
