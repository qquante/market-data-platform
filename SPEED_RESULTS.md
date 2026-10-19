> Atualização CP-DATA-SPEED-13: a meta 3/5 foi testada e não homologada; perfil mantido 8/16. [Estado atual, ensaios e limitações](SPEED_3S_5S_RESULTS.md). Este documento conserva evidências históricas.

# Velocidade GMGN — CP-DATA-SPEED-12

11/09/2026, horários UTC. Implementação, implantação e testes reais na base local `midas-data-base` (8791). **Perfil mantido: Trenches 8 s / Trending 16 s; painel operacional 2 s.** Este é o perfil mais rápido que passou nesta rampa curta. Não é homologação por dias nem de cobertura integral por token. Estratégias e aceite amplo D1–D5 continuam abertos.

## Resultado medido

A comparação usa capturas HTTP realmente persistidas, não apenas os valores dos timers. Baseline: seis respostas por rota, descritas em [SPEED_AND_STREAMING_AUDIT.md](SPEED_AND_STREAMING_AUDIT.md). Perfil final: quatro minutos de observação; a tabela remove os primeiros 60 s após o restart, pois a agenda conserva próximas consultas previamente salvas.

| Medida | Antes | Perfil final | Ganho observado |
|---|---:|---:|---:|
| Intervalo médio Trenches | 27,00 s | 8,695 s (20 capturas) | 3,11×; intervalo -67,8% |
| Intervalo médio Trending | 62,45 s | 17,633 s (10 capturas) | 3,54×; intervalo -71,8% |
| Pedido → resposta Trenches, mediana | 669 ms | 478 ms | -28,5% nesta amostra |
| Pedido → resposta Trending, mediana | 328 ms | 279 ms | -15,2% nesta amostra |
| Atualização leve da operação no navegador | timer 10 s | 2,005 s / 1,989 s observados | timer 5× mais frequente |
| Consulta CH de 512 IDs ausentes | 690,74 ms | 82,70 ms | 8,35× nesta consulta |
| Bytes lidos nessa consulta CH | 278.269.630 | 5.515.068 | -98,0% |

Os 512 IDs ausentes são os mesmos UUIDs determinísticos nos dois testes, duas repetições. O histórico CH cresceu de cerca de 14,46 para 15,15 milhões de linhas entre medidas; não é benchmark em base congelada. O ganho 8,35× é da verificação de identidades, **não do pipeline inteiro**. HTTP inclui o caminho local do pedido; não mede evento on-chain → GMGN. Mudança de conteúdo, conexão e condições externas impedem atribuir todo ganho HTTP a uma única alteração.

No perfil final, recebimento → normalização teve mediana de 2,956 s em Trenches e 2,655 s em Trending. Recebimento → confirmação conjunta Lake + CH teve mediana de **26,166 s e 26,705 s**, máximos 35,400 s e 31,363 s. Esses relógios são distintos. Atualizar o painel a cada 2 s não transforma o histórico em dado com 2 s de idade.

As 20 respostas de Trenches e as 10 de Trending tinham hashes distintos. Isso prova mudança nos bytes recebidos, não novidade econômica em cada campo. OHLCV também pode repetir respostas válidas.

## Rampa real e decisão

| Perfil Trenches/Trending | Janela do ensaio | Resultado | Maior idade observada da fila Lake |
|---|---|---|---:|
| 5 s / 10 s | várias tentativas limitadas | Reprovado por acúmulo; uma tentativa parou por HTTP 401 | 51,55 s na versão final |
| 10 s / 20 s | 300 s programados, 30 snapshots | Passou, zero novos erros | 16,93 s |
| 8 s / 16 s | 240 s programados, 24 snapshots | Passou, zero novos erros; mantido | 33,81 s |
| 2 s / 4 s e 1 s / 2 s | Não executados live | Sem aceite; 5 s / 10 s já excedeu a capacidade de arquivamento | — |

O watchdog amostrou a cada 10 s e interrompeu por erro novo da fonte/destino, por três erros de monitoramento ou idade de fila acima de 35 s em duas amostras consecutivas. Esses limites são critérios locais do ensaio, não uma quota/garantia da GMGN. Recuo automático para o perfil anterior foi comprovado nas tentativas reprovadas. **O watchdog era finito e terminou; não foi instalada automação recorrente ou um controlador permanente.**

8 s / 16 s entre 20:20:08 e a última amostra 20:24:01: Lake oscilou, com 22 pendências no pico e 14 ao final; a idade caiu de 27,03 s na primeira amostra para 19,72 s na última. Normalizador chegou a quatro pendências/4,37 s; CH a seis/6,82 s. Todos os destinos ficaram sem novos erros. A janela de 10 s / 20 s teve mais folga e arquivou Trenches/Trending em medianas de 16,47 s/18,12 s; é o recuo recomendado se a fila do perfil final voltar a crescer.

Apenas estas janelas são comparáveis como ensaios controlados. Reinícios, materialização de índice, benchmarks e testes isolados fora delas podem disputar CPU/I/O. Os horários e snapshots completos estão nas evidências.

## Alterações implantadas

1. **ClickHouse:** índice adicional `fact_id_bloom_v2`, `bloom_filter(0.000001) GRANULARITY 1`, materializado nas partes existentes. O índice anterior permanece. A consulta global de identidades passa de 64 para 256 IDs por requisição. A tentativa com 512 foi descartada porque ultrapassava o orçamento de URL de 32 KiB em teste. Divisão recursiva somente no erro CH 307; erro de identidade e demais falhas continuam visíveis. Limites de memória/leitura não foram removidos.
2. **Entrega histórica:** até 32 capturas / 25.000 fatos por grupo, mantendo 10.000 fatos por captura e 16 MiB por grupo. Consultas, recibos, comparação de hash, replay e confirmação usam o mesmo limite. A seleção continua justa entre pendentes elegíveis e respeita o próximo retry. O normalizador permanece em oito capturas / 10.000 fatos; não recebeu o limite maior do histórico.
3. **Transporte do coletor:** conexão HTTPS reutilizada, TLS validado, host e cinco rotas read-only permitidos. Sem redirect ou retry oculto. Conexão falha é descartada; tentativas/recuperação seguem na agenda. O transporte customizado exige explicitamente o gate Redis compartilhado. A pausa fixa de 250 ms após sucesso foi removida; sem trabalho, espera 50 ms. Nenhuma dependência nova.
4. **Agenda:** aceita configuração explícita a partir de 1 s, validando o orçamento ponderado; isso permite ensaios, não autoriza/valida operação a 1 s. A configuração ativa é 8/16. Os próximos vencimentos e o universo persistente sobrevivem aos restarts.
5. **Dashboard:** operação atualiza a cada 2 s enquanto a área está visível; trava existente impede sobreposição. Não aumenta a frequência de consultas históricas pesadas nem o limite de duas consultas pesadas simultâneas.

Não foi implementado WS novo, concorrência de múltiplas consultas da fonte ou representação de histórico por diferenças. Os snapshots brutos e as observações normalizadas completas continuam preservados. O coletor de origem ainda é serial.

## Quota, universo e integridade

Configuração persistida em `.data/gmgn-data/base-runtime.env`: `DATA_TRENCHES_INTERVAL_SECONDS=8`, `DATA_RANK_INTERVAL_SECONDS=16`, `DATA_COLLECTOR_WEIGHT_PER_SECOND=1.3075`. Descoberta usa orçamento teórico `3/8 + 1/16 = 0,4375` pesos/s; reserva restante de 0,87 para enriquecimento. `shared_gate=true` confirmado. Os 8 pesos/s do Redis são configuração local, não prova de quota contratual. Defaults genéricos do Compose continuam conservadores (25/60/1); o perfil implantado depende desse arquivo de ambiente.

A agenda passou de **59.835 identidades antes desta implantação para 60.741 no snapshot de aceite**; sem teto de membros e sem reinício do cadastro. Ainda havia 59.115 sem primeira consulta individual, apenas 697 com recebimento aceito dentro da frequência e 60.075 jobs devidos. As frequências de listas não são frequência individual de todas as famílias de todos os tokens. Info, holders e OHLCV continuam sujeitos à agenda/quota.

Quatro capturas recentes (duas por lista) foram baixadas pela API local: todos os SHA-256 coincidiram com o registro e o cabeçalho; recibos finais dos dois destinos presentes. Outra verificação read-only comparou 4.400 fatos existentes e rejeitou conflito introduzido no último ID fornecido, sem escrever esse conflito. A prova ampliada de 13.200 fatos com replay é um teste SQL isolado; não deve ser apresentada como lote real GMGN dessa dimensão.

Durante a rodada ocorreu **um novo HTTP 401 em OHLCV**, 20:01:18 UTC; a própria recuperação voltou a funcionar às 20:02:18. Causa não estabelecida; não foi HTTP 429 e não há evidência para atribuir a IPv6. Quatro falhas adicionais de entrega local foram registradas no período das recriações da API (contador 16 → 20); nos dois perfis aprovados o contador permaneceu em 20 e a entrega estava recuperada.

As quarentenas aumentaram de duas para três: `token_info` de 19:57:36 UTC contém NUL incompatível com o contrato textual do PostgreSQL. Bruto preservado, normalização bloqueada por `invalid_unicode`. Nenhuma quarentena nova nas janelas aprovadas. Isso continua pendente de tratamento próprio; não remover o caractere silenciosamente para declarar dado íntegro.

## Validação

- JUnit host: 173 casos distintos na união das três suítes, 162 aprovados e 11 dependentes de serviços inicialmente ignorados. As execuções sobrepostas não foram somadas como casos novos.
- Redis real: 11 testes de gate aprovados em namespaces privados (inclui nove casos antes ignorados). PostgreSQL real: os dois casos de progresso/fairness aprovados em schemas privados após a imagem final. Não houve testes financeiros ou alteração de ledger financeiro.
- Cobertura: TLS/transporte/reuso/falha/tamanho/allowlist; aplicação do gate compartilhado ao transporte customizado; orçamento 1 s/2 s; identidade CH com conflito na última parte; divisão por limite de leitura; ordenação/retry; grupo 13.200 fatos, replay sem duplicar, limite individual de 10.000, grupo 16 MiB, 32 capturas e rejeição da 33ª; normalizador ainda limitado a oito.
- Navegador Chromium real, headless, desktop/mobile: 17 verificações aprovadas. Leitura local sem chave, cinco fontes, cobertura, cadastro/dados acessíveis, timer observado em 2,005 s/1,989 s, no máximo uma requisição de status em voo, sem erros JavaScript/HTTP 5xx e sem escritas.
- Índice novo materializado sem falhas; API, worker, coletor, PostgreSQL, CH e NATS saudáveis no snapshot de aceite. Bancos/volumes mantidos. API/worker `midas-data-app:speed-v3-20260911`; coletor `midas-data-collector:speed-v1-20260911`. IDs completos em `runtime.json`.

Falhas iniciais de testes expuseram os limites de URL/lote e foram corrigidas antes da implantação final. A infraestrutura host de pytest tinha ACL inválida em seu diretório temporário padrão; foi usado diretório temporário novo explícito. Não foi executada uma suíte global de trading ou teste de carga por dias.

## Operação e próximos passos

O gargalo restante demonstrado é o histórico DuckLake: uma busca global de identidades para 4.400 fatos consumiu cerca de 7,27 s na sonda real. Lotes maiores amortizaram esse custo, mas não eliminaram a leitura global nem a repetição de milhares de fatos por snapshot. **Não basta aumentar quota ou reduzir timers para chegar a 1 s / 2 s.**

Próxima fatia: reduzir o custo de confirmação/identidade do Lake e a repetição histórica, preservando unicidade global, conflitos, observações sem mudança, bruto, relógios e reconstrução temporal; comparar novamente 5 s / 10 s. Uma representação por estado atual + alterações/checkpoints precisa de contrato de replay e não foi incluída nesta mudança. Validar uso de disco e comportamento com crescimento antes de novos ritmos. O acesso WS completo de Trenches/Trending continua sem contrato homologado neste projeto.

Para recuar a coleta, conservar as imagens atuais e alterar somente os três valores do ambiente: **10/20/1.22**; se ainda necessário, **25/60/1**. Aplicar com:

```powershell
docker compose -p midas-data-base --env-file .data/gmgn-data/base-runtime.env -f compose.data.yaml up -d --no-deps collector
```

O backup privado anterior é `.data/gmgn-data/base-runtime.before-speed-20260911.env`; não publicar seu conteúdo. Recuo de timer foi testado; retorno de código antigo após grupos maiores não foi homologado e não faz parte desse procedimento. Não remover volumes, fatos, recibos, jobs ou o índice para mudar a frequência.

`ensure_schema` cria o índice de forma idempotente. Em outra base com partes antigas, sua implantação também requer operação explícita de administrador:

```sql
ALTER TABLE midas_data.facts_log MATERIALIZE INDEX fact_id_bloom_v2;
SELECT mutation_id, is_done, parts_to_do, latest_fail_reason
FROM system.mutations
WHERE database='midas_data' AND table='facts_log';
```

Recarregar uma aba do dashboard aberta antes da implantação para carregar o JavaScript novo. Nenhuma automação horária foi recriada; executores financeiros e serviços paralelos permaneceram fora desta mudança.

## Evidências e referências

[Pacote da execução](../outputs/gmgn-speed-20260911/README.md): fases, snapshots, latências reais, benchmark, integridade, JUnit, navegador, runtime e manifesto SHA-256. [Baseline anterior](../outputs/gmgn-speed-audit-20260911/measurements.json). Fontes primárias consultadas: [GMGN, catálogo e pesos HTTP](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md), [ClickHouse, índices de skipping e materialização](https://clickhouse.com/docs/concepts/features/performance/skip-indexes/skipping-indexes), [DuckDB, Bloom em Parquet](https://duckdb.org/2025/03/07/parquet-bloom-filters-in-duckdb), [DuckLake, roadmap](https://ducklake.select/roadmap). Não foi instalado DuckDB preview nem substituído o contrato de identidade por uma suposição sobre Bloom filters.
