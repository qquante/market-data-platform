# Qualificação da pesquisa e capacidade — 11/09/2026

> Estado CP-BASE-READY-07: [correção dos candles e limites atuais](BASE_READINESS.md); [inventário completo dos dados coletados](INVENTARIO_DADOS_COLETADOS.md). Estratégias adiadas pelo usuário.

Checkpoint **CP-RESEARCH-QUALIFICATION-03**. Escopo: somente `midas-data-base`; A0, sem subagentes. Implementação implantada e ensaios concluídos. **Validação de estratégia permanece pendente por insuficiência de cobertura**, não por ausência de código ou de execução do estudo. Nenhuma automação horária foi recriada.

## Resultado dos quatro pedidos

| Pedido | Entrega verificada | Limite / pendência real |
|---|---|---|
| 1. Dados econômicos | Método empírico explícito de Trenches, separado do método estrito. 280/300 entradas utilizáveis. | A equivalência formal do alias em USD não foi homologada pelo fornecedor. A política empírica foi aprendida depois dos dados; uso exploratório. |
| 2. Amostra e estudos | Universo congelado de 5.438 registros, amostra determinística de 300; três estratégias e três estudos executados; replay exato. | Só 50 saídas elegíveis e 42 pares entrada/saída. A amostra é insuficiente para validar estratégia. |
| 3. Famílias necessárias | OHLCV Solana em coleta contínua, mesma fila/quota; parser, API e painel. Holders preservados. | Traders, outras cadeias e conteúdo social não são necessários à hipótese atual e continuam sem nova coleta dedicada. Links sociais já existentes não são posts ou sinais sociais. |
| 4. Capacidade e crescimento | Dois períodos de 15 minutos de observação real, teste de pico e 4 consultas novas/s; fila/cache limitados; crescimento físico medido. | Não certifica dias de carga contínua, outras consultas, crescimento ilimitado ou atualização instantânea. |

## Dados econômicos sem reescrever o passado

Método opt-in: `gmgn.trenches.observed_market_cap_usd.v1`. Qualificação: `gmgn-sol-trenches-economic-observation-20260911-v1`. O contrato estrito `gmgn.trenches.v1.usd_market_cap` e os fatos originais `gmgn-p0-v4` permanecem intactos.

No bruto de 10:52:34 UTC, 179/180 linhas satisfizeram `abs(market_cap - price * total_supply) <= 0.02 + abs(market_cap) * 0.00001`. A linha divergente continua indisponível sob essa regra. Isto é uma verificação de consistência, não prova independente de moeda, supply circulante, pool ou preço executável. Não há reescala automática por decimals.

Três tokens foram consultados nos endpoints oficiais de informação e candles. Dois preços de Trenches caíram no intervalo OHLC do minuto comparado; um divergiu. Portanto não se afirma equivalência entre pools/endpoints. A primeira sonda usou aliases de intervalo incorretos (`from_timestamp/to_timestamp`); seus brutos foram preservados, mas **não provam que a janela pedida foi atendida**. A agenda implantada usa `from/to` em milissegundos e o leitor verifica a janela de cada barra.

Ausência, valor inválido, identidade/revisão incompatível, duplicação ou falha da consistência produzem indisponibilidade. A política empírica e seu caráter retrospectivo acompanham pontos, definições e fontes arquivadas. Não houve promoção global de fatos antigos para `valid`.

Referências primárias conferidas: [cliente oficial de Kline](https://github.com/GMGNAI/gmgn-skills/blob/4e48e25328c114eb615a3419599366276479cd42/src/client/OpenApiClient.ts), [contratos oficiais de mercado](https://github.com/GMGNAI/gmgn-skills/blob/4e48e25328c114eb615a3419599366276479cd42/skills/gmgn-market/SKILL.md) e [campos de candles](https://github.com/GMGNAI/gmgn-skills/blob/4e48e25328c114eb615a3419599366276479cd42/skills/gmgn-kline-pattern/SKILL.md). O último descreve time em ms e volume em USD. Não adotamos sua sugestão de substituir volume ausente por zero.

## Amostra, protocolo e resultado científico

Protocolo congelado antes das consultas de desfecho: `.scratch/research-expansion-20260911/protocol.json`, SHA-256 `f7242a4246b6d12d96db0197d681c4329e36fa4bb59c57e52bfb7b26f4c8f7e8`.

Período 05:00–11:00 UTC; corte de conhecimento 11:10 UTC. Entrada 60 s após primeiro registro, horizonte 300 s, lacuna máxima 120 s, embargo 120 s. Cortes 07:00/09:00/11:00 UTC. Seleção por SHA-256 de seed e registry_id, seed `CP-RESEARCH-03-20260911`; nenhum filtro por ganho, liquidez ou completude. Todos os IDs do frame, denominadores e hash ficam no bundle. Fração de inclusão 300/5.438 nesse frame; isso não mede cobertura de todos os tokens da cadeia.

Migração aditiva 019 persiste o frame junto com o congelamento dos membros, sob o mesmo controle de lease. Teto de 20 mil registros no frame e 300 membros, mantendo 20 mil pontos, 8 MiB por bundle, 512 KiB por membro, 64 coortes e 128 experimentos. Excesso falha integralmente. Definições antigas sem seed e replay do bundle anterior continuam compatíveis.

| Partição | Tokens | Entradas | Saídas | Pares completos | Alta / queda / igual nos pares |
|---|---:|---:|---:|---:|---|
| Treino | 103 | 97 | 14 | 11 | 4 / 4 / 3 |
| Validação | 67 | 61 | 15 | 12 | 5 / 4 / 3 |
| Teste | 107 | 100 | 19 | 18 | 8 / 8 / 2 |
| Expurgados nas fronteiras | 23 | 22 | 2 | 1 | 0 / 1 / 0 |

As direções acima são mudanças brutas de cotação, sem custos; não são win rate nem retorno de uma carteira. O modelo de carteira usa taxa 100 bps, slippage 200 bps por lado, custo de rede USD 0,01 por lado, posição USD 10, caixa USD 1.000 e máximo de 20 posições. São cenários explícitos, sem evidência de fills.

Três critérios predefinidos: todas as entradas disponíveis; cap até 100 mil; cap de 100 mil a 1 milhão. Estudos: transições de capitalização, evento de 100 mil e vizinhos por prefixo. Os dois primeiros critérios deixam 20 posições sem saída em cada partição e bloqueiam novas admissões; retorno total corretamente permanece desconhecido. O terceiro tem apenas 3/0/1 encerramentos em treino/validação/teste. **Nenhuma estratégia foi aprovada ou selecionada automaticamente.**

No nível USD 20 mil: 31 atingiram em alguma observação, 19 não atingiram sob a política de amostragem, 221 ficaram censurados e 29 desconhecidos. Não se converte desaparecimento de Trenches em perda, zero, fechamento forçado ou amostra descartada. O estudo identifica a falta de continuidade como problema dominante. Horas de uma única sessão, exposição a holders ausente e grupos/campanhas não identificados impedem inferência forte.

**Próximo passo necessário para fechar o item 2:** uma coorte prospectiva predefinida, acompanhada até o horizonte mesmo depois de sair das listas, com frequência compatível com a lacuna exigida; depois reservar dias ainda não inspecionados para teste. A agenda atual de dez tokens/600 s não equivale a cobertura de todos os registros para horizonte de cinco minutos. OHLCV recebido depois ajuda a reconstruir trajetórias, mas não retroage o conhecimento disponível nem prova fills. Mais amostras da mesma lista sem continuidade não resolvem esse problema. Não foi criada uma automação de continuação nem declarado um estudo futuro como executado.

## OHLCV em operação

`DATA_RESEARCH_ENDPOINTS=token_top_holders,token_kline`. Uma requisição por minuto para cada família, dez tokens ativos elegíveis, alvo de revisita de 600 s. A quota global continua 1 peso/s; soma nominal dos endpoints passa de 10 para 12 pesos/min, abaixo do teto de 60. Aumento de utilização explícito, sem aumentar a quota contratada. Backoff/429, propriedade da captura, persistência, ACK e retomada são os mecanismos já existentes.

Cada Kline solicita uma hora em barras de 1 minuto, com `from/to` em ms. Leitor `gmgn-candles-read-v1`, rota autenticada `GET /v1/candles/{capture_id}?source_kind=gmgn&knowledge_cutoff=...`. Valida JSON, identidade, faixa, alinhamento, duplicação, OHLC, volume, fechamento e limites. Barra fora da janela ou incompleta permanece explicitamente indisponível. Nada é interpolado, nem substituído por zero. Relógio da barra, recebimento e publicação são separados.

Aceite em 2026-09-11T11:34:16.633502+00:00: **18 capturas / 10 tokens / 819 barras elegíveis entre 829 recebidas**. Intervalo mínimo 60.007745 s e publicação máxima 2.88191 s; hashes conferidos. Nenhuma falha de coleta OHLCV no status final.

São observações de barras por captura, com sobreposição entre janelas; não um total de candles únicos. O bruto/catalogue permanece na base; leitura versionada gera o contexto sob demanda. Não foi criada projeção OHLCV em fatos de token, ClickHouse ou DuckLake. Essa separação está indicada no retorno e evita confundir recibo analítico de zero fatos com uma tabela de candles.

O painel mostra a família e abre sua evidência; não faz consulta direta ao provedor.

## Capacidade medida e comportamento em picos

Mesmo token/janela/corte, resposta de 32.280 bytes e SHA idêntico antes/depois. Os dois slots do banco permanecem. Até 32 requisições são admitidas entre execução e espera, espera máxima 2 s; excedente recebe 429/Retry-After. Cache limitado a 32 entradas/16 MiB/60 s, chave com todos os parâmetros e corte exato. Sem cache de erro. Cancelamento, timeout, expiração, memória, fonte e corte testados. Resposta congelada em cache pode ser lida durante indisponibilidade do pool; um corte novo deve consultar o banco e falha corretamente se o pool estiver indisponível.

| Ensaio real local | Resultado | p95 das aceitas |
|---|---|---:|
| Antes, pico 20 | 2 aceitas / 18 com 429 | 118,6 ms |
| Depois, pico 20 com cache | 20/20 aceitas | 52,8 ms |
| Depois, pico 100 com cache | 100/100 aceitas | 407,3 ms |
| 100 cortes novos simultâneos, sem cache | 32 aceitas / 68 com 429 | 650,6 ms |
| Reenvio dessas 68, duas por vez | 68/68 aceitas | 39,7 ms |
| 240 cortes novos, 4/s durante 60 s | 240/240 aceitas | 38,2 ms |

Sem HTTP 500/timeouts nesses ensaios. Os testes de carga precedem apenas o ajuste final do cabeçalho de reutilização de features; esse ajuste foi validado pelo teste integrado com pool indisponível. Não extrapolar a consulta medida para janelas maiores ou todas as rotas.

Observação operacional: 31 amostras em 15 minutos antes e 31 depois; nenhum backlog no spool. Processamento corrente em 31/31 antes e 29/31 depois; as duas transições ocorreram nas reinstalações, com filas zero e recuperação comprovada. Pico de RAM da API nas amostras posteriores: 121.245.696 bytes, sob limite de 512 MiB. Isso é RAM do serviço, não do computador.

PostgreSQL cresceu 52.060.160 bytes no primeiro período limpo de 900,05 s: projeção linear de **4,65 GiB/dia para esse banco**, sob a carga observada. Não inclui raw, ClickHouse, DuckLake, WAL ou mudança de mix. O segundo período incluiu criação/remoção de schemas de teste e não serve como estimativa limpa. A reserva de disco e os limites continuam ativos; não foi implementado purge automático nem apagado histórico. Retenção de longo prazo ainda exige dimensionamento com a carga efetiva.

## Validação e implantação

**992 testes únicos passaram**, contando os relatórios XML sem duplicar casos repetidos; 33 skips do pacote estão declarados nos XML (dependências/ambiente host), sem serem contados como sucesso. Inclui testes de normalização/pesquisa existentes, nova qualificação, amostragem, candles, fila/cache, PostgreSQL isolado e coletor. Os gates de backup/restore host fora deste escopo não foram anunciados como reexecutados.

O ensaio identificou e corrigiu um erro de contabilidade na remoção LRU e um cabeçalho que anunciava o cálculo original ao reutilizar resposta. A verificação integrada foi atualizada para provar tanto cache congelado quanto timeout/recuperação de consulta nova. Na navegação, pontos sobrepostos foram ativados por teclado; não foi usado clique forçado. Uma primeira tentativa durante o reinício encontrou serviço ainda iniciando; a validação final foi executada com serviço saudável.

Browser real headless: 1440×1050 e 390×844, 12 pontos empíricos visíveis, candles abertos, manifesto/resultado baixados idênticos aos arquivos da API, sem erro JS ou overflow mobile, rotas não autenticadas 401, chave sem persistência no navegador. Replay offline da nova comparação e da comparação anterior reproduziu exatamente os artefatos salvos.

API/worker: `midas-data-app:research-qualified-v1-20260911`, imagem `sha256:2891a83c7f07708e2c61cb0c38b62ac2f9f8ad557d871d11b36bc1cdb5e513b9`.

Coletor: `midas-data-collector:research-candles-v1-20260911`, imagem `sha256:0b41435c85cf2ce5bf9d41984367d795f972fa13357e572e7de8c5a9d65ea7f8`.

Migrações 001–019; seis serviços saudáveis e sem OOM no aceite. Seis hashes de ledgers paper preservados, zero regras/vínculos ativos e zero posições abertas. Nenhuma chave privada, assinatura, broadcast ou alteração no executor Telegram. Configuração anterior e imagens para rollback preservadas no diretório privado apontado em `rollback-reference.json`; o checkpoint histórico anterior não foi sobrescrito.

## Evidências reproduzíveis

Diretório `.scratch/research-expansion-20260911/`:

- `protocol.json`, `research-spec.json`, `experiment-spec.json`, `research-bundle.json`, `experiment-result.json`, `experiment-replay.json`, `research-verdict.json`.
- `official-tree.json`, fontes oficiais arquivadas, brutos/manifestos da qualificação e `trench-arithmetic.json`.
- `candle-acceptance.json`, `http-load-before.json`, `http-load-after.json`, `http-cold-steady.json`, `sustained.jsonl`, `sustained-after.jsonl`.
- `packaged-platform.xml`, `packaged-sql-final.xml`, `collector-final.xml`, `final-cache-ui.xml`, `checks-summary.json`.
- `browser-acceptance.json`, screenshots, downloads do navegador, `before-deploy.json`, `final-live.json`, `containers.json`, `prior-bundle-replay.json`.

Para reproduzir o estudo sem rede: executar `python -m midas_data.experiments --bundle research-bundle.json --spec experiment-spec.json --output replay.json --verify experiment-result.json` na imagem registrada, montando os artefatos. O output preserva cenários sem desfecho e não escolhe estratégia.
