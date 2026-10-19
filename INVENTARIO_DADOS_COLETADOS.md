# Inventário dos dados coletados — base GMGN

> Atualização 11/09/2026: [dicionário operacional CP-BASE-FIELDS-08](FIELD_DICTIONARY.md) instalado; 831 caminhos das cinco fontes e cobertura por ocorrência. As contagens e pendências abaixo pertencem ao checkpoint histórico indicado.

Snapshot de campos: **2026-09-11 15:36:07.187115+00:00**. Escopo: projeto Docker `midas-data-base`, origem GMGN, cadeia **Solana**. Estratégias ficam para uma etapa posterior.

## Coleta efetivamente ativa

| Fonte | Frequência e recorte real |
|---|---|
| Trending / ranking (`market_rank`) | Uma consulta a cada 60 s; janela 5m, ordenada por swaps; pede/observa 10 tokens. |
| Trenches / fases do lançamento (`trenches`) | Uma consulta a cada 60 s. Pede 2 por categoria; nas cinco respostas auditadas retornaram 60 em cada uma das três categorias (180 por captura). O limite solicitado não equivale ao limite observado. |
| Informações do token (`token_info`) | Uma vaga a cada 60 s; rotação de até 10 ativos, meta de 600 s por token; até 20 em espera. |
| Top holders (`token_top_holders`) | Uma vaga a cada 60 s para tokens já admitidos; pede até 100 carteiras, maior amount_percentage primeiro; meta de 600 s por token, sujeita à fila. |
| Candles OHLCV (`token_kline`) | Uma vaga a cada 60 s para tokens já admitidos; resolução 1m; solicita a última hora em from/to (ms); meta de 600 s por token, sujeita à fila. Barras ausentes não são preenchidas. |

Quota do coletor preservada: 1 peso/s. Cada endpoint tem seu turno; atualização do painel a cada 10 s não significa novo preço a cada 10 s. O registro de tokens é muito maior que os dez acompanhados individualmente.

## Conteúdo por categoria

| Categoria | Dados presentes ou preservados |
|---|---|
| Identidade e imagens | chain, address, name, symbol, logo, descrições e metadados visuais; variações de nomes e símbolos quando retornadas. |
| Preço e capitalização | price, market_cap, history_highest_market_cap, ATH e referências de preço por janela. Moeda/supply e qualidade dependem do endpoint; aliases não são homologados automaticamente. |
| Supply | circulating_supply, total_supply, max_supply, decimals; presença não implica escala conhecida em todos os endpoints. |
| Pools e liquidez | liquidity, initial_liquidity, maior pool, exchange, endereços base/quote, reservas/vaults, migração e referências de pool. |
| Volume e fluxo | volume, compras, vendas, swaps, volumes de compra/venda em 1m/5m/1h/6h/24h quando retornados; net_buy_24h e taxas/tips reportadas. |
| Criação e launchpad | plataforma, estado/progresso, creation/created/open/complete/migrated timestamps e duração até conclusão, respeitando o nome e unidade de cada contrato. |
| Criador e equipe | creator/dev, origem de fundos, criação de outros tokens, saldos/participações e metadados reportados; não equivale a investigar a carteira inteira. |
| Distribuição e carteiras sinalizadas | holder_count, top_10_holder_rate e listas/contagens/percentuais reportados de smart money, KOL/renowned, snipers, bundlers, bots, whales, carteiras novas e suspeitas. |
| Risco e permissões | rug_ratio, flags de honeypot, mint/freeze, open source/renounced, wash trading, impostos, burn e concentração conforme retornados. Flags da GMGN não são auditoria independente. |
| Atenção e promoção | visiting_count, hot_level, calls/menções e metadados de anúncios/boosts/trending do DexScreener. |
| Social e links | Twitter/X, Telegram, website, Instagram, TikTok e outros links do token_info; seguidores/seguindo, comunidade e alterações de nome/links quando reportados. Sem coleta dedicada de posts, comentários ou sentimento. |
| Holders individuais | Endereço, amount_percentage, saldo, custos/preço médio, compras/vendas, PnL reportado, transferências, tags, atividade e metadados. Apenas sete campos têm interpretação tipada no parser atual; o restante fica em bruto/catálogo. |
| Candles | time, open, high, low, close e volume validados pelo contrato específico; amount e source preservados sem interpretação automática. |
| Proveniência | Resposta HTTP original, SHA-256, capture_id, endpoint, parâmetros, status HTTP, request/receive/event/record/availability clocks, versão da interpretação, JSON Pointer, qualidade e motivos. |
| Dados calculados localmente | Registro dos tokens, valuation por método, trajetórias e resumos UTC, máximos observados, cruzamentos, janelas/features, filas/recursos, coortes/labels/experimentos e ledger paper. São derivados; não novas fontes GMGN. |

## O que esta lista comprova

O inventário abaixo inclui **todos os caminhos** encontrados nas cinco capturas publicadas mais recentes de cada uma das cinco famílias, com tipos, presença e qualidade. Índices de arrays viram `*`; objetos, arrays e campos nulos também aparecem. Ele não afirma que cobre todos os campos opcionais de todo o histórico ou todos os campos possíveis da API.
Os arquivos brutos de cada captura permanecem completos na base e podem ser baixados no dashboard. O catálogo registra campos não interpretados; capturado não significa homologado. `valid` é conformidade com o contrato local do campo, não confirmação independente da informação on-chain.
A semântica dos candles reaproveita `gmgn-candles-read-v1`; admissão `gmgn-candles-normalize-v1`. Barras fora da janela, incompletas, inconsistentes ou duplicadas continuam indisponíveis. O horário da barra não vira disponibilidade histórica.

JSON legível por máquina: [field-inventory.json](../outputs/gmgn-base-inventory-20260911/field-inventory.json). Cada amostra inclui capture_id, SHA-256 e parâmetros reais. O estado de captura nesse arquivo é o snapshot; as duas capturas finais de transição foram reclassificadas logo após esse snapshot.

## O que não estamos coletando de forma dedicada

- Outras cadeias (Ethereum, BSC, Base, Robinhood etc.) no novo coletor.
- Endpoints dedicados de traders, transações/trades individuais, portfólios/histórico completo de carteiras ou feeds de smart money/KOL.
- Posts completos de X, comentários, engajamento verificado e sentimento social.
- Dados independentes on-chain/RPC para auditar todos os campos e provar fills ou liquidez executável.
- Todas as fases e primeiros segundos de todos os tokens da cadeia.

Campos de traders/dev/social eventualmente embutidos em respostas atuais continuam preservados; isso não significa que essas famílias dedicadas estejam em operação.

## Dicionário exato observado

| Endpoint | Capturas inspecionadas | Caminhos distintos | Caminhos com valor escalar/nulo |
|---|---:|---:|---:|
| `market_rank` | 5 | 104 | 99 |
| `token_info` | 5 | 196 | 180 |
| `token_kline` | 5 | 15 | 11 |
| `token_top_holders` | 5 | 95 | 86 |
| `trenches` | 5 | 385 | 365 |

### Trending / ranking — `market_rank`

Uma consulta a cada 60 s; janela 5m, ordenada por swaps; pede/observa 10 tokens.

| Caminho JSON | Tipos observados | Capturas com o campo | Qualidades no catálogo | Contrato / motivo |
|---|---|---:|---|---|
| `/` | object | 5/5 | unverified | unmapped_field |
| `/code` | number | 5/5 | unverified | unmapped_field |
| `/data` | object | 5/5 | unverified | unmapped_field |
| `/data/code` | number | 5/5 | unverified | unmapped_field |
| `/data/data` | object | 5/5 | unverified | unmapped_field |
| `/data/data/rank` | array | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*` | object | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/address` | string | 5/5 | valid | gmgn.market_rank.v4:address |
| `/data/data/rank/*/bluechip_owner_percentage` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/bot_degen_count` | number | 5/5 | valid | gmgn.market_rank.v4:bot_degen_count |
| `/data/data/rank/*/bot_degen_rate` | number | 5/5 | unverified | gmgn.market_rank.v4:bot_degen_rate |
| `/data/data/rank/*/bundler_rate` | number | 5/5 | unverified | gmgn.market_rank.v4:bundler_rate |
| `/data/data/rank/*/burn_ratio` | number | 5/5 | unverified | gmgn.market_rank.v4:burn_ratio |
| `/data/data/rank/*/burn_status` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/buy_tax` | string | 5/5 | missing, unverified | gmgn.market_rank.v4:buy_tax |
| `/data/data/rank/*/buys` | number | 5/5 | valid | gmgn.market_rank.v4:buys |
| `/data/data/rank/*/chain` | string | 5/5 | valid | gmgn.market_rank.v4:chain |
| `/data/data/rank/*/creation_timestamp` | number | 5/5 | valid | gmgn.market_rank.v4:creation_timestamp |
| `/data/data/rank/*/creator` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/creator_close` | boolean | 5/5 | valid | gmgn.market_rank.v4:creator_close |
| `/data/data/rank/*/creator_token_status` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/cto_flag` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dev_team_hold_rate` | number | 5/5 | unverified | gmgn.market_rank.v4:dev_team_hold_rate |
| `/data/data/rank/*/dev_token_burn_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dev_token_burn_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_ad` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_ad_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_boost_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_boost_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_trending_bar` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_trending_bar_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_update_link` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/dexscr_update_link_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/end_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/entrapment_ratio` | number | 5/5 | unverified | gmgn.market_rank.v4:entrapment_ratio |
| `/data/data/rank/*/exchange` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/gas_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/history_highest_market_cap` | number | 5/5 | valid | gmgn.market_rank.v4:history_highest_market_cap |
| `/data/data/rank/*/holder_count` | number | 5/5 | valid | gmgn.market_rank.v4:holder_count |
| `/data/data/rank/*/hot_level` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/id` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/image_dup` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/initial_liquidity` | number | 5/5 | unverified | gmgn.market_rank.v4:initial_liquidity |
| `/data/data/rank/*/is_honeypot` | number | 5/5 | unverified | gmgn.market_rank.v4:is_honeypot |
| `/data/data/rank/*/is_og` | boolean | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/is_open_source` | number | 5/5 | valid | gmgn.market_rank.v4:is_open_source |
| `/data/data/rank/*/is_renounced` | number | 5/5 | valid | gmgn.market_rank.v4:is_renounced |
| `/data/data/rank/*/is_show_alert` | boolean | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/is_token_live` | boolean | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/is_wash_trading` | boolean | 5/5 | valid | gmgn.market_rank.v4:is_wash_trading |
| `/data/data/rank/*/launch_quote_address` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/launchpad` | string | 5/5 | unverified | gmgn.market_rank.v4:launchpad |
| `/data/data/rank/*/launchpad_platform` | string | 5/5 | valid | gmgn.market_rank.v4:launchpad_platform |
| `/data/data/rank/*/launchpad_status` | string | 5/5 | unverified | gmgn.market_rank.v4:launchpad_status |
| `/data/data/rank/*/liquidity` | number | 5/5 | unverified | gmgn.market_rank.v4:liquidity |
| `/data/data/rank/*/lock_percent` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/logo` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/market_cap` | number | 5/5 | valid | gmgn.market_rank.v4:market_cap |
| `/data/data/rank/*/migrated_pool_exchange` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/name` | string | 5/5 | valid | gmgn.market_rank.v4:name |
| `/data/data/rank/*/open_timestamp` | number | 5/5 | invalid, valid | gmgn.market_rank.v4:open_timestamp |
| `/data/data/rank/*/pool_type` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/pool_type_str` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/price` | number | 5/5 | valid | gmgn.market_rank.v4:price |
| `/data/data/rank/*/price_change_percent` | number | 5/5 | unverified | gmgn.market_rank.v4:price_change_percent |
| `/data/data/rank/*/price_change_percent1h` | number | 5/5 | unverified | gmgn.market_rank.v4:price_change_percent1h |
| `/data/data/rank/*/price_change_percent1m` | number | 5/5 | unverified | gmgn.market_rank.v4:price_change_percent1m |
| `/data/data/rank/*/price_change_percent5m` | number | 5/5 | unverified | gmgn.market_rank.v4:price_change_percent5m |
| `/data/data/rank/*/rank` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/rat_trader_amount_rate` | number | 5/5 | unverified | gmgn.market_rank.v4:rat_trader_amount_rate |
| `/data/data/rank/*/renounced_freeze_account` | number | 5/5 | valid | gmgn.market_rank.v4:renounced_freeze_account |
| `/data/data/rank/*/renounced_mint` | number | 5/5 | valid | gmgn.market_rank.v4:renounced_mint |
| `/data/data/rank/*/renowned_count` | number | 5/5 | valid | gmgn.market_rank.v4:renowned_count |
| `/data/data/rank/*/rug_ratio` | number | 5/5 | valid | gmgn.market_rank.v4:rug_ratio |
| `/data/data/rank/*/sell_tax` | string | 5/5 | missing, unverified | gmgn.market_rank.v4:sell_tax |
| `/data/data/rank/*/sells` | number | 5/5 | valid | gmgn.market_rank.v4:sells |
| `/data/data/rank/*/smart_degen_count` | number | 5/5 | valid | gmgn.market_rank.v4:smart_degen_count |
| `/data/data/rank/*/sniper_count` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/square_mentions` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/start_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/swaps` | number | 5/5 | valid | gmgn.market_rank.v4:swaps |
| `/data/data/rank/*/symbol` | string | 5/5 | valid | gmgn.market_rank.v4:symbol |
| `/data/data/rank/*/tcid` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/telegram` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/telegram_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/top70_sniper_hold_rate` | number | 5/5 | unverified | gmgn.market_rank.v4:top70_sniper_hold_rate |
| `/data/data/rank/*/top_10_holder_rate` | number | 5/5 | valid | gmgn.market_rank.v4:top_10_holder_rate |
| `/data/data/rank/*/total_supply` | number | 5/5 | unverified | gmgn.market_rank.v4:total_supply |
| `/data/data/rank/*/trans_name` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/trans_name_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/trans_symbol` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/trans_symbol_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_change_flag` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_create_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_del_post_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_rename_count` | number | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/twitter_username` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/visiting_count` | number | 5/5 | unverified | gmgn.market_rank.v4:visiting_count |
| `/data/data/rank/*/volume` | number | 5/5 | valid | gmgn.market_rank.v4:volume |
| `/data/data/rank/*/website` | string | 5/5 | unverified | unmapped_field |
| `/data/data/rank/*/website_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/message` | string | 5/5 | unverified | unmapped_field |
| `/data/reason` | string | 5/5 | unverified | unmapped_field |

Contratos de fatos disponíveis no código (não garantem presença na resposta):

| Caminho relativo | Métrica | Unidade | Qualidade máxima contratual |
|---|---|---|---|
| `bot_degen_count` | `bot_degen_count` | count | valid |
| `bot_degen_rate` | `bot_degen_rate` | não homologada | unverified |
| `bundler_rate` | `bundler_rate` | não homologada | unverified |
| `bundler_trader_amount_rate` | `bundler_trader_amount_rate` | não homologada | unverified |
| `burn_ratio` | `burn_ratio` | não homologada | unverified |
| `buy_tax` | `buy_tax` | não homologada | unverified |
| `buys` | `buys_5m` | count | valid |
| `circulating_supply` | `circulating_supply` | não homologada | unverified |
| `creation_timestamp` | `creation_timestamp` | unix_second | valid |
| `creator_close` | `creator_close` | não homologada | valid |
| `decimals` | `decimals` | não homologada | unverified |
| `dev_team_hold_rate` | `dev_team_hold_rate` | não homologada | unverified |
| `entrapment_ratio` | `entrapment_ratio` | não homologada | unverified |
| `history_highest_market_cap` | `history_highest_market_cap` | usd | valid |
| `holder_count` | `holder_count` | count | valid |
| `initial_liquidity` | `initial_liquidity` | não homologada | unverified |
| `insider_rate` | `insider_rate` | não homologada | unverified |
| `is_honeypot` | `is_honeypot` | boolean_code_01 | valid |
| `is_open_source` | `is_open_source` | boolean_code_01 | valid |
| `is_renounced` | `is_renounced` | boolean_code_01 | valid |
| `is_wash_trading` | `is_wash_trading` | não homologada | valid |
| `launchpad` | `launchpad` | não homologada | unverified |
| `launchpad_platform` | `launchpad_platform` | não homologada | valid |
| `launchpad_progress` | `launchpad_progress` | não homologada | unverified |
| `launchpad_status` | `launchpad_status` | não homologada | unverified |
| `liquidity` | `liquidity` | usd | unverified |
| `locked_ratio` | `locked_ratio` | não homologada | unverified |
| `market_cap` | `market_cap` | usd | valid |
| `max_supply` | `max_supply` | não homologada | unverified |
| `migrated_timestamp` | `migrated_timestamp` | não homologada | unverified |
| `name` | `name` | não homologada | valid |
| `open_timestamp` | `open_timestamp` | unix_second | valid |
| `price` | `price` | usd_per_token | valid |
| `price_change_percent` | `price_change_percent` | não homologada | unverified |
| `price_change_percent1h` | `price_change_percent1h` | não homologada | unverified |
| `price_change_percent1m` | `price_change_percent1m` | não homologada | unverified |
| `price_change_percent5m` | `price_change_percent5m` | não homologada | unverified |
| `rat_trader_amount_rate` | `rat_trader_amount_rate` | não homologada | unverified |
| `renounced_freeze_account` | `renounced_freeze_account` | boolean_code_01 | valid |
| `renounced_mint` | `renounced_mint` | boolean_code_01 | valid |
| `renowned_count` | `renowned_count` | count | valid |
| `rug_ratio` | `rug_ratio` | score_0_1 | valid |
| `sell_tax` | `sell_tax` | não homologada | unverified |
| `sells` | `sells_5m` | count | valid |
| `smart_degen_count` | `smart_degen_count` | count | valid |
| `swaps` | `swaps_5m` | count | valid |
| `symbol` | `symbol` | não homologada | valid |
| `top70_sniper_hold_rate` | `top70_sniper_hold_rate` | não homologada | unverified |
| `top_10_holder_rate` | `top_10_holder_rate` | ratio | valid |
| `total_supply` | `total_supply` | não homologada | unverified |
| `visiting_count` | `visiting_count` | não homologada | unverified |
| `volume` | `volume_5m` | usd | valid |

### Informações do token — `token_info`

Uma vaga a cada 60 s; rotação de até 10 ativos, meta de 600 s por token; até 20 em espera.

| Caminho JSON | Tipos observados | Capturas com o campo | Qualidades no catálogo | Contrato / motivo |
|---|---|---:|---|---|
| `/` | object | 5/5 | unverified | unmapped_field |
| `/code` | number | 5/5 | unverified | unmapped_field |
| `/data` | object | 5/5 | unverified | unmapped_field |
| `/data/address` | string | 5/5 | valid | gmgn.token_info.v3:address |
| `/data/ath_price` | number | 5/5 | valid | gmgn.token_info.v3:ath_price |
| `/data/banner` | string | 5/5 | unverified | unmapped_field |
| `/data/biggest_pool_address` | string | 5/5 | unverified | unmapped_field |
| `/data/circulating_supply` | string | 5/5 | valid | gmgn.token_info.v3:circulating_supply |
| `/data/creation_timestamp` | number | 5/5 | valid | gmgn.token_info.v3:creation_timestamp |
| `/data/decimals` | number | 5/5 | valid | gmgn.token_info.v3:decimals |
| `/data/dev` | object | 5/5 | unverified | unmapped_field |
| `/data/dev/address` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info` | object | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/ath_mc` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/ath_token` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/avatar` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/creation_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/name` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/ath_token_info/symbol` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/creator_address` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/creator_open_count` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/creator_token_balance` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/creator_token_status` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/cto_flag` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_ad` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_ad_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_boost_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_boost_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_trending_bar` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_trending_bar_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_update_link` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/dexscr_update_link_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/fund_from` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/fund_from_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/offchain` | boolean | 5/5 | unverified | unmapped_field |
| `/data/dev/top_10_holder_rate` | string | 5/5 | unverified | unmapped_field |
| `/data/dev/twitter_create_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/twitter_del_post_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/dev/twitter_name_change_history` | array | 5/5 | unverified | unmapped_field |
| `/data/dev/twitter_name_change_history/*` | object | 1/5 | unverified | unmapped_field |
| `/data/dev/twitter_name_change_history/*/address` | string | 1/5 | unverified | unmapped_field |
| `/data/dev/twitter_name_change_history/*/rename_timestamp` | number | 1/5 | unverified | unmapped_field |
| `/data/dev/twitter_name_change_history/*/twitter_username` | string | 1/5 | unverified | unmapped_field |
| `/data/fee_distribution` | object | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/launchpad` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data` | object | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/bonus_category` | array | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/bonus_category/*` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/fee_authority` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/is_charity` | boolean | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/is_locked` | boolean | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list` | array | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*` | object | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/has_claimed_fee` | boolean | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/is_charity` | boolean | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/is_creator` | boolean | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/pfp` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/royalty_bps` | number | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/twitter_username` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/username` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/list/*/wallet` | string | 5/5 | unverified | unmapped_field |
| `/data/fee_distribution/platform_data/show` | boolean | 5/5 | unverified | unmapped_field |
| `/data/holder_count` | number | 5/5 | valid | gmgn.token_info.v3:holder_count |
| `/data/image_dup_count` | number | 5/5 | unverified | unmapped_field |
| `/data/launchpad` | string | 5/5 | valid | gmgn.token_info.v3:launchpad |
| `/data/launchpad_platform` | string | 5/5 | valid | gmgn.token_info.v3:launchpad_platform |
| `/data/launchpad_progress` | number | 5/5 | valid | gmgn.token_info.v3:launchpad_progress |
| `/data/launchpad_status` | number | 5/5 | valid | gmgn.token_info.v3:launchpad_status |
| `/data/link` | object | 5/5 | unverified | unmapped_field |
| `/data/link/address` | string | 5/5 | unverified | unmapped_field |
| `/data/link/bitbucket` | string | 5/5 | unverified | unmapped_field |
| `/data/link/description` | string | 5/5 | missing | gmgn.token_info.v3:link.description |
| `/data/link/discord` | string | 5/5 | unverified | unmapped_field |
| `/data/link/facebook` | string | 5/5 | unverified | unmapped_field |
| `/data/link/fracaster` | null | 5/5 | missing | explicit_null |
| `/data/link/geckoterminal` | string | 5/5 | unverified | unmapped_field |
| `/data/link/github` | string | 5/5 | unverified | unmapped_field |
| `/data/link/gmgn` | string | 5/5 | unverified | unmapped_field |
| `/data/link/instagram` | string | 5/5 | unverified | unmapped_field |
| `/data/link/linkedin` | string | 5/5 | unverified | unmapped_field |
| `/data/link/medium` | string | 5/5 | unverified | unmapped_field |
| `/data/link/reddit` | string | 5/5 | unverified | unmapped_field |
| `/data/link/telegram` | string | 5/5 | missing | gmgn.token_info.v3:link.telegram |
| `/data/link/tiktok` | string | 5/5 | unverified | unmapped_field |
| `/data/link/twitter_username` | string | 5/5 | missing, valid | gmgn.token_info.v3:link.twitter_username |
| `/data/link/verify_status` | number | 5/5 | unverified | unmapped_field |
| `/data/link/website` | string | 5/5 | valid | gmgn.token_info.v3:link.website |
| `/data/link/youtube` | string | 5/5 | unverified | unmapped_field |
| `/data/liquidity` | string | 5/5 | valid | gmgn.token_info.v3:liquidity |
| `/data/locked_ratio` | number | 5/5 | valid | gmgn.token_info.v3:locked_ratio |
| `/data/logo` | string | 5/5 | unverified | unmapped_field |
| `/data/max_supply` | string | 5/5 | unverified | gmgn.token_info.v3:max_supply |
| `/data/migrated_pool` | string | 5/5 | unverified | unmapped_field |
| `/data/migrated_timestamp` | number | 5/5 | unverified | gmgn.token_info.v3:migrated_timestamp |
| `/data/migration_market_cap` | number | 5/5 | unverified | gmgn.token_info.v3:migration_market_cap |
| `/data/migration_market_cap_quote` | string | 5/5 | unverified | unmapped_field |
| `/data/name` | string | 5/5 | valid | gmgn.token_info.v3:name |
| `/data/og` | boolean | 5/5 | unverified | unmapped_field |
| `/data/open_timestamp` | number | 5/5 | valid | gmgn.token_info.v3:open_timestamp |
| `/data/pool` | object | 5/5 | unverified | unmapped_field |
| `/data/pool/address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/base_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/base_reserve` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/base_reserve_value` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/base_vault_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/creation_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/pool/creator` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/exchange` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/fee_ratio` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/initial_base_reserve` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/initial_liquidity` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/initial_quote_reserve` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/liquidity` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/pool_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/quote_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/quote_reserve` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/quote_reserve_value` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/quote_symbol` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/quote_vault_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/token0_address` | string | 5/5 | unverified | unmapped_field |
| `/data/pool/token1_address` | string | 5/5 | unverified | unmapped_field |
| `/data/price` | object | 5/5 | unverified | unmapped_field |
| `/data/price/address` | string | 5/5 | unverified | unmapped_field |
| `/data/price/buy_volume_1h` | string | 5/5 | valid | gmgn.token_info.v3:price.buy_volume_1h |
| `/data/price/buy_volume_1m` | string | 5/5 | valid | gmgn.token_info.v3:price.buy_volume_1m |
| `/data/price/buy_volume_24h` | string | 5/5 | valid | gmgn.token_info.v3:price.buy_volume_24h |
| `/data/price/buy_volume_5m` | string | 5/5 | valid | gmgn.token_info.v3:price.buy_volume_5m |
| `/data/price/buy_volume_6h` | string | 5/5 | valid | gmgn.token_info.v3:price.buy_volume_6h |
| `/data/price/buys_1h` | number | 5/5 | valid | gmgn.token_info.v3:price.buys_1h |
| `/data/price/buys_1m` | number | 5/5 | valid | gmgn.token_info.v3:price.buys_1m |
| `/data/price/buys_24h` | number | 5/5 | valid | gmgn.token_info.v3:price.buys_24h |
| `/data/price/buys_5m` | number | 5/5 | valid | gmgn.token_info.v3:price.buys_5m |
| `/data/price/buys_6h` | number | 5/5 | valid | gmgn.token_info.v3:price.buys_6h |
| `/data/price/hot_level` | number | 5/5 | unverified | unmapped_field |
| `/data/price/price` | string | 5/5 | valid | gmgn.token_info.v3:price.price |
| `/data/price/price_1h` | string | 5/5 | valid | gmgn.token_info.v3:price.price_1h |
| `/data/price/price_1m` | string | 5/5 | valid | gmgn.token_info.v3:price.price_1m |
| `/data/price/price_24h` | string | 5/5 | valid | gmgn.token_info.v3:price.price_24h |
| `/data/price/price_5m` | string | 5/5 | valid | gmgn.token_info.v3:price.price_5m |
| `/data/price/price_6h` | string | 5/5 | valid | gmgn.token_info.v3:price.price_6h |
| `/data/price/sell_volume_1h` | string | 5/5 | valid | gmgn.token_info.v3:price.sell_volume_1h |
| `/data/price/sell_volume_1m` | string | 5/5 | valid | gmgn.token_info.v3:price.sell_volume_1m |
| `/data/price/sell_volume_24h` | string | 5/5 | valid | gmgn.token_info.v3:price.sell_volume_24h |
| `/data/price/sell_volume_5m` | string | 5/5 | valid | gmgn.token_info.v3:price.sell_volume_5m |
| `/data/price/sell_volume_6h` | string | 5/5 | valid | gmgn.token_info.v3:price.sell_volume_6h |
| `/data/price/sells_1h` | number | 5/5 | valid | gmgn.token_info.v3:price.sells_1h |
| `/data/price/sells_1m` | number | 5/5 | valid | gmgn.token_info.v3:price.sells_1m |
| `/data/price/sells_24h` | number | 5/5 | valid | gmgn.token_info.v3:price.sells_24h |
| `/data/price/sells_5m` | number | 5/5 | valid | gmgn.token_info.v3:price.sells_5m |
| `/data/price/sells_6h` | number | 5/5 | valid | gmgn.token_info.v3:price.sells_6h |
| `/data/price/swaps_1h` | number | 5/5 | valid | gmgn.token_info.v3:price.swaps_1h |
| `/data/price/swaps_1m` | number | 5/5 | valid | gmgn.token_info.v3:price.swaps_1m |
| `/data/price/swaps_24h` | number | 5/5 | valid | gmgn.token_info.v3:price.swaps_24h |
| `/data/price/swaps_5m` | number | 5/5 | valid | gmgn.token_info.v3:price.swaps_5m |
| `/data/price/swaps_6h` | number | 5/5 | valid | gmgn.token_info.v3:price.swaps_6h |
| `/data/price/volume_1h` | string | 5/5 | valid | gmgn.token_info.v3:price.volume_1h |
| `/data/price/volume_1m` | string | 5/5 | valid | gmgn.token_info.v3:price.volume_1m |
| `/data/price/volume_24h` | string | 5/5 | valid | gmgn.token_info.v3:price.volume_24h |
| `/data/price/volume_5m` | string | 5/5 | valid | gmgn.token_info.v3:price.volume_5m |
| `/data/price/volume_6h` | string | 5/5 | valid | gmgn.token_info.v3:price.volume_6h |
| `/data/standard` | string | 5/5 | unverified | unmapped_field |
| `/data/stat` | object | 5/5 | unverified | unmapped_field |
| `/data/stat/bot_degen_count` | number | 5/5 | valid | gmgn.token_info.v3:stat.bot_degen_count |
| `/data/stat/bot_degen_rate` | string | 5/5 | unverified | gmgn.token_info.v3:stat.bot_degen_rate |
| `/data/stat/creator_created_count` | number | 5/5 | unverified | unmapped_field |
| `/data/stat/creator_hold_rate` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/creator_token_balance` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/degen_call_count` | number | 5/5 | unverified | unmapped_field |
| `/data/stat/dev_team_hold_rate` | string | 5/5 | unverified | gmgn.token_info.v3:stat.dev_team_hold_rate |
| `/data/stat/fresh_wallet_rate` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/holder_count` | number | 5/5 | unverified | unmapped_field |
| `/data/stat/private_vault_hold_rate` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/signal_count` | number | 5/5 | unverified | unmapped_field |
| `/data/stat/top70_sniper_hold_rate` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/top_10_holder_rate` | string | 5/5 | valid | gmgn.token_info.v3:stat.top_10_holder_rate |
| `/data/stat/top_bot_degen_percentage` | string | 5/5 | unverified | unmapped_field |
| `/data/stat/top_bundler_trader_percentage` | string | 5/5 | unverified | gmgn.token_info.v3:stat.top_bundler_trader_percentage |
| `/data/stat/top_entrapment_trader_percentage` | string | 5/5 | unverified | gmgn.token_info.v3:stat.top_entrapment_trader_percentage |
| `/data/stat/top_rat_trader_percentage` | string | 5/5 | unverified | gmgn.token_info.v3:stat.top_rat_trader_percentage |
| `/data/symbol` | string | 5/5 | valid | gmgn.token_info.v3:symbol |
| `/data/total_fee` | string | 5/5 | unverified | unmapped_field |
| `/data/total_supply` | string | 5/5 | unverified | gmgn.token_info.v3:total_supply |
| `/data/trade_fee` | string | 5/5 | unverified | unmapped_field |
| `/data/visiting_count` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat` | object | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/bundler_wallets` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/creator_wallets` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/fresh_wallets` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/rat_trader_wallets` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/renowned_wallets` | number | 5/5 | valid | gmgn.token_info.v3:wallet_tags_stat.renowned_wallets |
| `/data/wallet_tags_stat/smart_wallets` | number | 5/5 | valid | gmgn.token_info.v3:wallet_tags_stat.smart_wallets |
| `/data/wallet_tags_stat/sniper_wallets` | number | 5/5 | valid | gmgn.token_info.v3:wallet_tags_stat.sniper_wallets |
| `/data/wallet_tags_stat/top_wallets` | number | 5/5 | unverified | unmapped_field |
| `/data/wallet_tags_stat/whale_wallets` | number | 5/5 | unverified | unmapped_field |
| `/message` | string | 5/5 | unverified | unmapped_field |
| `/reason` | string | 5/5 | unverified | unmapped_field |

Contratos de fatos disponíveis no código (não garantem presença na resposta):

| Caminho relativo | Métrica | Unidade | Qualidade máxima contratual |
|---|---|---|---|
| `ath_price` | `ath_price` | usd_per_token | valid |
| `circulating_supply` | `circulating_supply` | token | valid |
| `creation_timestamp` | `creation_timestamp` | unix_second | valid |
| `decimals` | `decimals` | decimal_places | valid |
| `holder_count` | `holder_count` | count | valid |
| `initial_liquidity` | `initial_liquidity` | não homologada | unverified |
| `launchpad` | `launchpad` | não homologada | valid |
| `launchpad_platform` | `launchpad_platform` | não homologada | valid |
| `launchpad_progress` | `launchpad_progress` | ratio | valid |
| `launchpad_status` | `launchpad_status` | não homologada | valid |
| `link/description` | `description` | não homologada | valid |
| `link/telegram` | `telegram` | não homologada | valid |
| `link/twitter` | `twitter` | não homologada | unverified |
| `link/twitter_username` | `twitter_username` | não homologada | valid |
| `link/website` | `website` | não homologada | valid |
| `liquidity` | `liquidity` | usd | valid |
| `locked_ratio` | `locked_ratio` | ratio | valid |
| `max_supply` | `max_supply` | não homologada | unverified |
| `migrated_timestamp` | `migrated_timestamp` | não homologada | unverified |
| `migration_market_cap` | `migration_market_cap` | usd | unverified |
| `name` | `name` | não homologada | valid |
| `open_timestamp` | `open_timestamp` | unix_second | valid |
| `price/buy_volume_1h` | `buy_volume_1h` | usd | valid |
| `price/buy_volume_1m` | `buy_volume_1m` | usd | valid |
| `price/buy_volume_24h` | `buy_volume_24h` | usd | valid |
| `price/buy_volume_5m` | `buy_volume_5m` | usd | valid |
| `price/buy_volume_6h` | `buy_volume_6h` | usd | valid |
| `price/buys_1h` | `buys_1h` | count | valid |
| `price/buys_1m` | `buys_1m` | count | valid |
| `price/buys_24h` | `buys_24h` | count | valid |
| `price/buys_5m` | `buys_5m` | count | valid |
| `price/buys_6h` | `buys_6h` | count | valid |
| `price/price` | `price` | usd_per_token | valid |
| `price/price_1h` | `window_reference_price_1h` | usd_per_token | valid |
| `price/price_1m` | `window_reference_price_1m` | usd_per_token | valid |
| `price/price_24h` | `window_reference_price_24h` | usd_per_token | valid |
| `price/price_5m` | `window_reference_price_5m` | usd_per_token | valid |
| `price/price_6h` | `window_reference_price_6h` | usd_per_token | valid |
| `price/sell_volume_1h` | `sell_volume_1h` | usd | valid |
| `price/sell_volume_1m` | `sell_volume_1m` | usd | valid |
| `price/sell_volume_24h` | `sell_volume_24h` | usd | valid |
| `price/sell_volume_5m` | `sell_volume_5m` | usd | valid |
| `price/sell_volume_6h` | `sell_volume_6h` | usd | valid |
| `price/sells_1h` | `sells_1h` | count | valid |
| `price/sells_1m` | `sells_1m` | count | valid |
| `price/sells_24h` | `sells_24h` | count | valid |
| `price/sells_5m` | `sells_5m` | count | valid |
| `price/sells_6h` | `sells_6h` | count | valid |
| `price/swaps_1h` | `swaps_1h` | count | valid |
| `price/swaps_1m` | `swaps_1m` | count | valid |
| `price/swaps_24h` | `swaps_24h` | count | valid |
| `price/swaps_5m` | `swaps_5m` | count | valid |
| `price/swaps_6h` | `swaps_6h` | count | valid |
| `price/volume_1h` | `volume_1h` | usd | valid |
| `price/volume_1m` | `volume_1m` | usd | valid |
| `price/volume_24h` | `volume_24h` | usd | valid |
| `price/volume_5m` | `volume_5m` | usd | valid |
| `price/volume_6h` | `volume_6h` | usd | valid |
| `stat/bot_degen_count` | `bot_degen_count` | count | valid |
| `stat/bot_degen_rate` | `bot_degen_rate` | não homologada | unverified |
| `stat/dev_team_hold_rate` | `dev_team_hold_rate` | não homologada | unverified |
| `stat/top_10_holder_rate` | `top_10_holder_rate` | ratio | valid |
| `stat/top_bundler_trader_percentage` | `top_bundler_trader_percentage` | não homologada | unverified |
| `stat/top_entrapment_trader_percentage` | `top_entrapment_trader_percentage` | não homologada | unverified |
| `stat/top_rat_trader_percentage` | `top_rat_trader_percentage` | não homologada | unverified |
| `symbol` | `symbol` | não homologada | valid |
| `total_supply` | `total_supply` | não homologada | unverified |
| `wallet_tags_stat/renowned_wallets` | `renowned_wallets` | count | valid |
| `wallet_tags_stat/smart_wallets` | `smart_wallets` | count | valid |
| `wallet_tags_stat/sniper_wallets` | `sniper_wallets` | count | valid |

### Candles OHLCV — `token_kline`

Uma vaga a cada 60 s para tokens já admitidos; resolução 1m; solicita a última hora em from/to (ms); meta de 600 s por token, sujeita à fila. Barras ausentes não são preenchidas.

| Caminho JSON | Tipos observados | Capturas com o campo | Qualidades no catálogo | Contrato / motivo |
|---|---|---:|---|---|
| `/` | object | 5/5 | unverified | unmapped_field |
| `/code` | number | 5/5 | unverified | unmapped_field |
| `/data` | object | 5/5 | unverified | unmapped_field |
| `/data/list` | array | 5/5 | unverified | unmapped_field |
| `/data/list/*` | object | 5/5 | unverified | unmapped_field |
| `/data/list/*/amount` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/close` | string | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:close |
| `/data/list/*/high` | string | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:high |
| `/data/list/*/low` | string | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:low |
| `/data/list/*/open` | string | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:open |
| `/data/list/*/source` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/time` | number | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:time |
| `/data/list/*/volume` | string | 5/5 | invalid, unverified, valid | gmgn-candles-read-v1:volume |
| `/message` | string | 5/5 | unverified | unmapped_field |
| `/reason` | string | 5/5 | unverified | unmapped_field |

### Top holders — `token_top_holders`

Uma vaga a cada 60 s para tokens já admitidos; pede até 100 carteiras, maior amount_percentage primeiro; meta de 600 s por token, sujeita à fila.

| Caminho JSON | Tipos observados | Capturas com o campo | Qualidades no catálogo | Contrato / motivo |
|---|---|---:|---|---|
| `/` | object | 5/5 | unverified | unmapped_field |
| `/code` | number | 5/5 | unverified | unmapped_field |
| `/data` | object | 5/5 | unverified | unmapped_field |
| `/data/list` | array | 5/5 | unverified | unmapped_field |
| `/data/list/*` | object | 5/5 | unverified | unmapped_field |
| `/data/list/*/account_address` | string | 5/5 | valid | gmgn.token_top_holders.v1:account_address |
| `/data/list/*/accu_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/accu_cost` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/addr_type` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/address` | string | 5/5 | valid | gmgn.token_top_holders.v1:address |
| `/data/list/*/amount_cur` | number | 5/5 | valid | gmgn.token_top_holders.v1:amount_cur |
| `/data/list/*/amount_percentage` | number | 5/5 | valid | gmgn.token_top_holders.v1:amount_percentage |
| `/data/list/*/avatar` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/avg_cost` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/avg_sold` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/balance` | number | 5/5 | valid | gmgn.token_top_holders.v1:balance |
| `/data/list/*/buy_amount_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/buy_tx_count_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/buy_volume_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/cost` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/cost_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/created_at` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/current_buy_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/current_sell_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/current_transfer_in_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/current_transfer_out_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/end_holding_at` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/exchange` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_bought_cost` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_bought_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_sold_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_sold_income` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_transfer_in_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_transfer_in_cost` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_transfer_out_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_transfer_out_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/history_transfer_out_income` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/is_new` | boolean | 5/5 | unverified | unmapped_field |
| `/data/list/*/is_on_curve` | boolean | 5/5 | unverified | unmapped_field |
| `/data/list/*/is_suspicious` | boolean | 5/5 | unverified | unmapped_field |
| `/data/list/*/last_active_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/last_block` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/maker_token_tags` | array | 5/5 | unverified | unmapped_field |
| `/data/list/*/maker_token_tags/*` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/native_balance` | string | 5/5 | valid | gmgn.token_top_holders.v1:native_balance |
| `/data/list/*/native_transfer` | null, object | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/native_transfer/amount` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/native_transfer/from_address` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/native_transfer/name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/native_transfer/timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/native_transfer/tx_hash` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/netflow_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/netflow_usd` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/profit` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/profit_change` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/realized_pnl` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/realized_profit` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/sell_amount_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/sell_amount_percentage` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/sell_tx_count_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/sell_volume_cur` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/start_holding_at` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/tags` | array | 5/5 | unverified | unmapped_field |
| `/data/list/*/tags/*` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer` | object | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer/address` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer/name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/token_transfer/timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer/tx_hash` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer/type` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_in` | object | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_in/address` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_in/name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/token_transfer_in/timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_in/tx_hash` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_in/type` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_out` | object | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_out/address` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_out/name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/token_transfer_out/timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_out/tx_hash` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/token_transfer_out/type` | string | 5/5 | unverified | unmapped_field |
| `/data/list/*/total_cost` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/transfer_in` | boolean | 5/5 | unverified | unmapped_field |
| `/data/list/*/transfer_in_count` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/transfer_out_count` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/twitter_name` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/twitter_username` | null, string | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/unrealized_pnl` | null, number | 5/5 | missing, unverified | explicit_null, unmapped_field |
| `/data/list/*/unrealized_profit` | number | 5/5 | unverified | unmapped_field |
| `/data/list/*/usd_value` | number | 5/5 | valid | gmgn.token_top_holders.v1:usd_value |
| `/data/list/*/wallet_tag_v2` | string | 5/5 | unverified | unmapped_field |
| `/message` | string | 5/5 | unverified | unmapped_field |
| `/reason` | string | 5/5 | unverified | unmapped_field |

### Trenches / fases do lançamento — `trenches`

Uma consulta a cada 60 s. Pede 2 por categoria; nas cinco respostas auditadas retornaram 60 em cada uma das três categorias (180 por captura). O limite solicitado não equivale ao limite observado.

| Caminho JSON | Tipos observados | Capturas com o campo | Qualidades no catálogo | Contrato / motivo |
|---|---|---:|---|---|
| `/` | object | 5/5 | unverified | unmapped_field |
| `/code` | number | 5/5 | unverified | unmapped_field |
| `/data` | object | 5/5 | unverified | unmapped_field |
| `/data/completed` | array | 5/5 | unverified | unmapped_field |
| `/data/completed/*` | object | 5/5 | unverified | unmapped_field |
| `/data/completed/*/address` | string | 5/5 | valid | gmgn.trenches.v1:address |
| `/data/completed/*/bonus_category` | array | 5/5 | unverified | unmapped_field |
| `/data/completed/*/bonus_category/*` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/bot_degen_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/bot_degen_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/bundler_mhr` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/bundler_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/burn_status` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/buy_tips` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/buys_24h` | number | 5/5 | valid | gmgn.trenches.v1:buys_24h |
| `/data/completed/*/callout_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/chain` | string | 5/5 | valid | gmgn.trenches.v1:chain |
| `/data/completed/*/co_d` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/complete_cost_time` | number | 5/5 | valid | gmgn.trenches.v1:complete_cost_time |
| `/data/completed/*/complete_timestamp` | number | 5/5 | valid | gmgn.trenches.v1:complete_timestamp |
| `/data/completed/*/created_timestamp` | number | 5/5 | valid | gmgn.trenches.v1:created_timestamp |
| `/data/completed/*/creation_tool` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_balance_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_created_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_created_inner_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_created_open_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_created_open_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/creator_token_status` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/cto_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/decimals` | number | 5/5 | unverified | gmgn.trenches.v1:decimals |
| `/data/completed/*/dev_team_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dev_token_burn_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dev_token_burn_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dexscr_ad` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dexscr_boost_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dexscr_trending_bar` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/dexscr_update_link` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/end_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/entrapment_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/exchange` | string | 5/5 | valid | gmgn.trenches.v1:exchange |
| `/data/completed/*/fee_params` | array | 5/5 | unverified | unmapped_field |
| `/data/completed/*/fee_params/*` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/fresh_wallet_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/fund_from` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/fund_from_address` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/fund_from_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/has_at_least_one_social` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/holder_count` | number | 5/5 | valid | gmgn.trenches.v1:holder_count |
| `/data/completed/*/image_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/instagram` | string | 5/5 | missing | gmgn.trenches.v1:instagram |
| `/data/completed/*/is_og` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/is_token_live` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/is_wash_trading` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/launch_creator` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/launch_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/launchpad` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/launchpad_platform` | string | 5/5 | valid | gmgn.trenches.v1:launchpad_platform |
| `/data/completed/*/launchpad_status` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/liquidity` | number | 5/5 | unverified | gmgn.trenches.v1:liquidity |
| `/data/completed/*/logo` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/logo_small_base64` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/market_cap` | number | 5/5 | unverified | gmgn.trenches.v1:market_cap |
| `/data/completed/*/mk` | object | 5/5 | unverified | unmapped_field |
| `/data/completed/*/mk/o` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/mk/v` | array | 5/5 | unverified | unmapped_field |
| `/data/completed/*/mk/v/*` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/name` | string | 5/5 | valid | gmgn.trenches.v1:name |
| `/data/completed/*/net_buy_24h` | number | 5/5 | valid | gmgn.trenches.v1:net_buy_24h |
| `/data/completed/*/new_wallet_volume` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/offchain` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/open_timestamp` | number | 5/5 | valid | gmgn.trenches.v1:open_timestamp |
| `/data/completed/*/pool_address` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/price` | number | 5/5 | unverified | gmgn.trenches.v1:price |
| `/data/completed/*/private_vault_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/progress` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/quote_address` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/quote_address_type` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/rat_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/renounced_freeze_account` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/renounced_mint` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/renowned_count` | number | 5/5 | valid | gmgn.trenches.v1:renowned_count |
| `/data/completed/*/route_mint_a` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/route_mint_b` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/route_pool` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/route_vault_a` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/route_vault_b` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/rug_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/s_qsafe` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/sells_24h` | number | 5/5 | valid | gmgn.trenches.v1:sells_24h |
| `/data/completed/*/seq_index` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/show_sharing_fee` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/smart_degen_count` | number | 5/5 | valid | gmgn.trenches.v1:smart_degen_count |
| `/data/completed/*/sniper_count` | number | 5/5 | valid | gmgn.trenches.v1:sniper_count |
| `/data/completed/*/standard` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/start_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/status` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/suspected_insider_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/swaps_24h` | number | 5/5 | valid | gmgn.trenches.v1:swaps_24h |
| `/data/completed/*/symbol` | string | 5/5 | valid | gmgn.trenches.v1:symbol |
| `/data/completed/*/telegram` | string | 5/5 | missing, valid | gmgn.trenches.v1:telegram |
| `/data/completed/*/telegram_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/tg_call_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/tiktok` | string | 5/5 | missing | gmgn.trenches.v1:tiktok |
| `/data/completed/*/top70_sniper_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/top_10_holder_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/total_buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/total_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/total_sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/total_supply` | number | 5/5 | unverified | gmgn.trenches.v1:total_supply |
| `/data/completed/*/trade_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/trans_name` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/trans_name_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/trans_symbol` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/trans_symbol_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/tweet_publish_time` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter` | string | 5/5 | missing, valid | gmgn.trenches.v1:twitter |
| `/data/completed/*/twitter_change_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_community_id` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_community_name` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_create_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_del_post_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_handle` | string | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_is_tweet` | boolean | 5/5 | unverified | unmapped_field |
| `/data/completed/*/twitter_rename_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/visiting_count` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/volume_24h` | number | 5/5 | valid | gmgn.trenches.v1:volume_24h |
| `/data/completed/*/website` | string | 5/5 | missing, valid | gmgn.trenches.v1:website |
| `/data/completed/*/website_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/completed/*/x_user_follower` | number | 5/5 | valid | gmgn.trenches.v1:x_user_follower |
| `/data/completed/*/x_user_following` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion` | array | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*` | object | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/address` | string | 5/5 | valid | gmgn.trenches.v1:address |
| `/data/near_completion/*/bonus_category` | array | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/bonus_category/*` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/bot_degen_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/bot_degen_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/bundler_mhr` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/bundler_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/burn_status` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/buy_tips` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/buy_volume_24h` | number | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/buys_24h` | number | 5/5 | valid | gmgn.trenches.v1:buys_24h |
| `/data/near_completion/*/callout_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/chain` | string | 5/5 | valid | gmgn.trenches.v1:chain |
| `/data/near_completion/*/co_d` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/complete_cost_time` | number | 5/5 | invalid, valid | gmgn.trenches.v1:complete_cost_time |
| `/data/near_completion/*/complete_timestamp` | number | 5/5 | invalid, valid | gmgn.trenches.v1:complete_timestamp |
| `/data/near_completion/*/created_timestamp` | number | 5/5 | valid | gmgn.trenches.v1:created_timestamp |
| `/data/near_completion/*/creation_tool` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_balance_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_created_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_created_inner_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_created_open_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_created_open_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/creator_token_status` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/cto_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/decimals` | number | 5/5 | unverified | gmgn.trenches.v1:decimals |
| `/data/near_completion/*/dev_team_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dev_token_burn_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dev_token_burn_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dexscr_ad` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dexscr_boost_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dexscr_trending_bar` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/dexscr_update_link` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/end_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/entrapment_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/exchange` | string | 5/5 | valid | gmgn.trenches.v1:exchange |
| `/data/near_completion/*/fee_params` | array | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/fee_params/*` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/fresh_wallet_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/fund_from` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/fund_from_address` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/fund_from_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/has_at_least_one_social` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/holder_count` | number | 5/5 | valid | gmgn.trenches.v1:holder_count |
| `/data/near_completion/*/image_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/is_honeypot` | string | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/is_og` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/is_token_live` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/is_wash_trading` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/launchpad` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/launchpad_platform` | string | 5/5 | valid | gmgn.trenches.v1:launchpad_platform |
| `/data/near_completion/*/launchpad_status` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/liquidity` | number | 5/5 | unverified | gmgn.trenches.v1:liquidity |
| `/data/near_completion/*/logo` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/logo_small_base64` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/market_cap` | number | 5/5 | unverified | gmgn.trenches.v1:market_cap |
| `/data/near_completion/*/mk` | object | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/mk/o` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/mk/v` | array | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/mk/v/*` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/name` | string | 5/5 | valid | gmgn.trenches.v1:name |
| `/data/near_completion/*/net_buy_24h` | number | 5/5 | valid | gmgn.trenches.v1:net_buy_24h |
| `/data/near_completion/*/new_wallet_volume` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/offchain` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/open_source` | string | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/open_timestamp` | number | 5/5 | invalid | gmgn.trenches.v1:open_timestamp |
| `/data/near_completion/*/owner_renounced` | string | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/pool_address` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/price` | number | 5/5 | unverified | gmgn.trenches.v1:price |
| `/data/near_completion/*/private_vault_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/progress` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/quote_address` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/quote_address_type` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/rat_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/renounced_freeze_account` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/renounced_mint` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/renowned_count` | number | 5/5 | valid | gmgn.trenches.v1:renowned_count |
| `/data/near_completion/*/route_mint_a` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/route_pool` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/rug_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/s_qsafe` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/sell_volume_24h` | number | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/sells_24h` | number | 5/5 | valid | gmgn.trenches.v1:sells_24h |
| `/data/near_completion/*/seq_index` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/show_sharing_fee` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/smart_degen_count` | number | 5/5 | valid | gmgn.trenches.v1:smart_degen_count |
| `/data/near_completion/*/sniper_count` | number | 5/5 | valid | gmgn.trenches.v1:sniper_count |
| `/data/near_completion/*/standard` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/start_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/status` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/suspected_insider_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/swaps_24h` | number | 5/5 | valid | gmgn.trenches.v1:swaps_24h |
| `/data/near_completion/*/symbol` | string | 5/5 | valid | gmgn.trenches.v1:symbol |
| `/data/near_completion/*/telegram` | string | 5/5 | missing, valid | gmgn.trenches.v1:telegram |
| `/data/near_completion/*/telegram_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/tg_call_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/top70_sniper_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/top_10_holder_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/total_buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/total_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/total_sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/total_supply` | number | 5/5 | unverified | gmgn.trenches.v1:total_supply |
| `/data/near_completion/*/trade_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/trans_name` | string | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/trans_name_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/trans_symbol` | string | 4/5 | unverified | unmapped_field |
| `/data/near_completion/*/trans_symbol_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/tweet_publish_time` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter` | string | 5/5 | missing, valid | gmgn.trenches.v1:twitter |
| `/data/near_completion/*/twitter_change_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_create_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_del_post_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_handle` | string | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_is_tweet` | boolean | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/twitter_rename_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/visiting_count` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/volume_24h` | number | 5/5 | valid | gmgn.trenches.v1:volume_24h |
| `/data/near_completion/*/website` | string | 5/5 | missing, valid | gmgn.trenches.v1:website |
| `/data/near_completion/*/website_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/near_completion/*/x_user_follower` | number | 5/5 | valid | gmgn.trenches.v1:x_user_follower |
| `/data/near_completion/*/x_user_following` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation` | array | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*` | object | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/address` | string | 5/5 | valid | gmgn.trenches.v1:address |
| `/data/new_creation/*/bonus_category` | array | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/bonus_category/*` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/bot_degen_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/bot_degen_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/bundler_mhr` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/bundler_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/burn_status` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/buy_tips` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/buys_24h` | number | 5/5 | valid | gmgn.trenches.v1:buys_24h |
| `/data/new_creation/*/callout_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/chain` | string | 5/5 | valid | gmgn.trenches.v1:chain |
| `/data/new_creation/*/co_d` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/complete_cost_time` | number | 5/5 | invalid | gmgn.trenches.v1:complete_cost_time |
| `/data/new_creation/*/complete_timestamp` | number | 5/5 | invalid | gmgn.trenches.v1:complete_timestamp |
| `/data/new_creation/*/created_timestamp` | number | 5/5 | valid | gmgn.trenches.v1:created_timestamp |
| `/data/new_creation/*/creation_tool` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_balance_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_created_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_created_inner_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_created_open_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_created_open_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/creator_token_status` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/cto_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/decimals` | number | 5/5 | unverified | gmgn.trenches.v1:decimals |
| `/data/new_creation/*/dev_team_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dev_token_burn_amount` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dev_token_burn_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dexscr_ad` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dexscr_boost_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dexscr_trending_bar` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/dexscr_update_link` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/end_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/entrapment_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/exchange` | string | 5/5 | valid | gmgn.trenches.v1:exchange |
| `/data/new_creation/*/fee_params` | array | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/fee_params/*` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/fresh_wallet_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/fund_from` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/fund_from_address` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/fund_from_ts` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/has_at_least_one_social` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/holder_count` | number | 5/5 | valid | gmgn.trenches.v1:holder_count |
| `/data/new_creation/*/image_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/is_og` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/is_token_live` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/is_wash_trading` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/launchpad` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/launchpad_platform` | string | 5/5 | valid | gmgn.trenches.v1:launchpad_platform |
| `/data/new_creation/*/launchpad_status` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/liquidity` | number | 5/5 | unverified | gmgn.trenches.v1:liquidity |
| `/data/new_creation/*/logo` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/logo_small_base64` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/market_cap` | number | 5/5 | unverified | gmgn.trenches.v1:market_cap |
| `/data/new_creation/*/mk` | object | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/mk/o` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/mk/v` | array | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/mk/v/*` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/name` | string | 5/5 | valid | gmgn.trenches.v1:name |
| `/data/new_creation/*/net_buy_24h` | number | 5/5 | valid | gmgn.trenches.v1:net_buy_24h |
| `/data/new_creation/*/new_wallet_volume` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/offchain` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/open_timestamp` | number | 5/5 | invalid | gmgn.trenches.v1:open_timestamp |
| `/data/new_creation/*/pool_address` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/price` | number | 5/5 | unverified | gmgn.trenches.v1:price |
| `/data/new_creation/*/private_vault_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/progress` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/quote_address` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/quote_address_type` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/rat_trader_amount_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/renounced_freeze_account` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/renounced_mint` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/renowned_count` | number | 5/5 | valid | gmgn.trenches.v1:renowned_count |
| `/data/new_creation/*/route_mint_a` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/route_pool` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/rug_ratio` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/s_qsafe` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/sells_24h` | number | 5/5 | valid | gmgn.trenches.v1:sells_24h |
| `/data/new_creation/*/seq_index` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/show_sharing_fee` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/smart_degen_count` | number | 5/5 | valid | gmgn.trenches.v1:smart_degen_count |
| `/data/new_creation/*/sniper_count` | number | 5/5 | valid | gmgn.trenches.v1:sniper_count |
| `/data/new_creation/*/standard` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/start_live_timestamp` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/status` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/suspected_insider_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/swaps_24h` | number | 5/5 | valid | gmgn.trenches.v1:swaps_24h |
| `/data/new_creation/*/symbol` | string | 5/5 | valid | gmgn.trenches.v1:symbol |
| `/data/new_creation/*/telegram` | string | 5/5 | missing, valid | gmgn.trenches.v1:telegram |
| `/data/new_creation/*/telegram_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/tg_call_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/top70_sniper_hold_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/top_10_holder_rate` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/total_buy_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/total_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/total_sell_tax` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/total_supply` | number | 5/5 | unverified | gmgn.trenches.v1:total_supply |
| `/data/new_creation/*/trade_fee` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/trans_name` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/trans_name_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/trans_symbol` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/trans_symbol_zhcn` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/tweet_publish_time` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter` | string | 5/5 | missing, valid | gmgn.trenches.v1:twitter |
| `/data/new_creation/*/twitter_change_flag` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_create_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_del_post_token_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_handle` | string | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_is_tweet` | boolean | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/twitter_rename_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/visiting_count` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/volume_24h` | number | 5/5 | valid | gmgn.trenches.v1:volume_24h |
| `/data/new_creation/*/website` | string | 5/5 | missing, valid | gmgn.trenches.v1:website |
| `/data/new_creation/*/website_dup` | number | 5/5 | unverified | unmapped_field |
| `/data/new_creation/*/x_user_follower` | number | 5/5 | valid | gmgn.trenches.v1:x_user_follower |
| `/data/new_creation/*/x_user_following` | number | 5/5 | unverified | unmapped_field |
| `/message` | string | 5/5 | unverified | unmapped_field |
| `/reason` | string | 5/5 | unverified | unmapped_field |

Contratos de fatos disponíveis no código (não garantem presença na resposta):

| Caminho relativo | Métrica | Unidade | Qualidade máxima contratual |
|---|---|---|---|
| `buys_24h` | `buys_24h` | count | valid |
| `complete_cost_time` | `complete_cost_time` | second | valid |
| `complete_timestamp` | `complete_timestamp` | unix_second | valid |
| `created_timestamp` | `created_timestamp` | unix_second | valid |
| `decimals` | `decimals` | não homologada | unverified |
| `exchange` | `exchange` | não homologada | valid |
| `holder_count` | `holder_count` | count | valid |
| `instagram` | `instagram` | não homologada | valid |
| `launchpad_platform` | `launchpad_platform` | não homologada | valid |
| `liquidity` | `liquidity` | usd | unverified |
| `market_cap` | `market_cap` | não homologada | unverified |
| `name` | `name` | não homologada | valid |
| `net_buy_24h` | `net_buy_24h` | usd | valid |
| `open_timestamp` | `open_timestamp` | unix_second | valid |
| `price` | `price` | não homologada | unverified |
| `renowned_count` | `renowned_count` | count | valid |
| `sells_24h` | `sells_24h` | count | valid |
| `smart_degen_count` | `smart_degen_count` | count | valid |
| `sniper_count` | `sniper_count` | count | valid |
| `swaps_1h` | `swaps_1h` | count | valid |
| `swaps_1m` | `swaps_1m` | count | valid |
| `swaps_24h` | `swaps_24h` | count | valid |
| `symbol` | `symbol` | não homologada | valid |
| `telegram` | `telegram` | não homologada | valid |
| `tiktok` | `tiktok` | não homologada | valid |
| `total_supply` | `total_supply` | não homologada | unverified |
| `twitter` | `twitter` | não homologada | valid |
| `usd_market_cap` | `usd_market_cap` | usd | valid |
| `volume_1h` | `volume_1h` | usd | valid |
| `volume_24h` | `volume_24h` | usd | valid |
| `website` | `website` | não homologada | valid |
| `x_user_follower` | `x_user_follower` | count | valid |

## Referências técnicas

Rotas e parâmetros conferidos no [cliente oficial GMGN, revisão preservada](https://raw.githubusercontent.com/GMGNAI/gmgn-skills/4e48e25328c114eb615a3419599366276479cd42/src/client/OpenApiClient.ts). A evidência de presença dos campos vem dos brutos locais listados no JSON, não de presumir que a documentação implica cobertura.
Contratos locais: [FIELD_CONTRACTS](FIELD_CONTRACTS.md), [Holders](HOLDERS_PARSER.md), [Qualificação](RESEARCH_QUALIFICATION.md), [Correção e estado da base](BASE_READINESS.md).
