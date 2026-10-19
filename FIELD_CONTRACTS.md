# Contratos de campos GMGN — revisão v3

> Atualização 11/09/2026: dicionário operacional CP-BASE-FIELDS-08 (not included in this repository) instalado; 831 caminhos das cinco fontes e cobertura por ocorrência. As contagens e pendências abaixo pertencem ao checkpoint histórico indicado.

> Inventário histórico v3. O runtime atual usa normalizador v4: janelas explícitas válidas do ranking já são promovidas. Trending é market_rank. Revisão atual de fontes/valuation: MARKET_SOURCES.md (not included in this repository); detalhes v4: TRAJECTORY_CONTRACTS.md (not included in this repository).

Revisão documental e unitária de 2026-09-08. Versão do normalizador: `gmgn-p0-v3`. Não é homologação econômica/on-chain, rollout ou replay executado.

## Escopo e critérios

Este inventário cobre exatamente os 122 caminhos de métricas extraídos hoje: 70 de `token_info` (I) e 52 de `market_rank` (R). São contratos por endpoint e caminho; não 122 conceitos independentes, nem as 616 colunas físicas do legado. O catálogo P0 de negócio continua em `docs/gmgn_api/refactor/CATALOGO_CONTRATOS_P0.md` e não é substituído por contagem de colunas.

Um campo `valid` satisfaz o contrato declarado pelo provedor e as restrições do adapter. Não certifica supply real, integridade financeira, segurança, afiliação social nem execução. Um `unverified` tem tipo preservado, mas a razão específica bloqueia agregação verificada e uso em thresholds. `invalid` conserva o scalar/pointer e emite issue; `missing` conserva null/string vazia. Chave ausente não cria fato nem zero.

As métricas têm grão de observação de token. Parte do conteúdo descreve atributos de pool, evento, janela, classificação de wallet ou texto social; isso não transforma o snapshot em linha de pool/trade/wallet/post. Os contratos de grãos adicionais continuam pendentes.

## Evidências e limites

- I: [documentação oficial GMGNAI](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md), seção de resposta de `token info`.
- R: [documentação oficial GMGNAI](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md), seção de resposta de `market trending`.
- Consulta documental em 2026-09-08; URLs apontam para main, sem afirmar commit imutável. Registro JSON inclui fontes, restrições e hashes da amostra. Não foram feitas novas chamadas autenticadas.
- Mapeamento real de rotas: `src/midas_gmgn/openapi.py:102` (`/v1/token/info`) e `:109` (`/v1/market/rank`). A semântica não é propagada para hot_searches/trenches/security/pools/holders/traders/kline/signals.
- Amostra local limitada: 10 capturas originais, SHA da resposta e do blob iguais ao bruto descompactado; 41.609 bytes. I: 7 observações (bsc 3, base 1, robinhood 3). R: 3 capturas com 5 itens observados (eth/stable/arc). Não são necessariamente 12 tokens únicos. Sem evidência real Solana nesta amostra.
- Tipos observados (integer/decimal/string/null) são evidência de representação JSON, não garantia da precisão produzida pelo servidor. O parser local preserva números via Decimal e texto sem float intermediário.

## Decisões que mudam a interpretação

- Preço atual em I/R e ATH de preço I: USD por token. Capitalizações R são valores reportados em USD, com base de supply desconhecida. Não rotular FDV, recalcular a coluna, produzir ATH de MC por preço × supply atual ou equiparar ao MC circulante.
- Supply circulante I já está em tokens humanos; decimals não é aplicado novamente. Total/max supply e supply circulante R continuam sem prova de escala. Não há market cap derivado.
- Liquidez I refere-se ao maior/principal pool. R informa USD, mas escopo pool/agregado continua pendente. Mesmo nome de métrica não permite unir origens sem contract_version.
- As 5 janelas I (1m/5m/1h/6h/24h) ficam no nome da métrica; preços de referência significam início da janela. Âncora interna/atualização do provedor não foi medida. R volume/swaps/buys/sells dependem de request.interval; sem dimensão de janela no Fact, ficam unverified, mesmo quando request_params tem interval.
- creation_timestamp/open_timestamp são atributos em segundos Unix. Zero é rejeitado para uso como evento; não vira token de 1970. migrated_timestamp não ganhou unidade por magnitude. occurred_at permanece source_event_at explícito ou received_at; não é substituído por criação, abertura ou ATH.
- Flags R de honeypot/open-source/renúncia são inteiros JSON 0/1, conservados como integer + boolean_code_01. Não há conversão truthy. Honeypot só tem aplicabilidade documental em BSC/Base; renúncia de mint/freeze somente SOL. Fora dessas cadeias, encoding conhecido fica unverified; null continua missing.
- launchpad_status I mantém enum inteiro 0/1/2 introduzido em v2. R mantém string sem importar aquele vocabulário. Apenas frações explicitamente 0–1 recebem limite [0,1]. Demais rate/percentage/tax não são divididos por 100 conforme magnitude.
- visiting_count pertence a atenção, não holders. Contagens de grupos de wallets conservam rótulos do provedor; não afirmam metodologia ou rentabilidade desses grupos.

## Identidade, clocks e bruto sem cadeia

| Contrato estrutural | Regra e limite |
|---|---|
| address no objeto I/item R | Papel de token comprovado pela estrutura suportada. Endereço de pool/dev/wallet aninhado não vira token. Limite de 256 bytes UTF-8; existência on-chain não é verificada. |
| chain do objeto | Se presente deve coincidir com CaptureMetadata.chain. Se ausente, o namespace do transporte vem da metadata; isso não prova que o campo existia no bruto. |
| canonical_identity | Chain casefold, EVM conhecido com 0x + 40 hex em lowercase; Solana/outros preservam case do endereço. Nenhum alias eth/ethereum ou cadeia inventada. |
| CaptureMetadata.chain | Campo obrigatório, valor null permitido somente sem adapter. hot_searches sem cadeia conserva null, nunca global/unknown. I/R exigem string. |
| request_params.chain | String não nula deve coincidir com metadata.chain não nula. null permanece null/indeterminado, distinto de chave ausente; não preenche metadata.chain. |
| Fact.chain | Continua obrigatório e não nulo. Captura unsupported retorna catálogo e issue antes de qualquer casefold ou promoção de fatos. |
| requested_at / received_at | UTC aware; ordem validada. São clocks de transporte da origem, não evento token. requested_at não garante enqueue real; pode ocorrer após limitadores locais. |
| source_event_at | Opcional e explícito. Ausência usa received_at como proxy declarado, sem inferir evento de outro campo. |
| available_at / captured_at | Escritor A0 registra separadamente; normalizador não inventa clocks atuais. Recepção legada não vira disponibilidade histórica no destino novo. |

Referências: `midas_data/contracts.py:105`, `:180`, `:192`; `midas_data/normalize.py:307`, `:363`, `:394`, `:431`. Address/chain produzem classificações de identidade em FieldObservation (4 contratos de caminhos entre I/R), não métricas escalares extras nos 122 abaixo.

## Contagem de contratos

| Endpoint | Declarado e validado pelo adapter | Pendência semântica | Total |
|---|---:|---:|---:|
| token_info | 59 | 11 | 70 |
| market_rank | 21 | 31 | 52 |

A contagem declarada inclui flags com restrição por chain e campos documentados não presentes na pequena amostra. Não equivale a todos os campos homologados em todas as cadeias. Cada instância ainda pode resultar missing, invalid ou unverified por aplicabilidade.

## Matriz dos caminhos extraídos

Ponteiros relativos à entidade: I usa raiz ou `/data`; R usa `/<i>`, `/rank/<i>`, `/data/<i>`, `/data/rank/<i>` ou envelope duplo estrito `/data/data/rank/<i>`. O JSONPointer real fica preservado no fato. `D` = contrato declarado pelo provedor; `P` = pendente. A transformação comum é scalar → texto exato, validação de tipo/limites, sem aritmética; enum exige integer JSON literal. O registro JSON traz janela, grão, gate, constraints e cadeia permitida por linha.

### token_info

| Caminho bruto relativo → métrica | Tipo / unidade | D/P | Significado e fronteira | Observação de formato na amostra |
|---|---|---|---|---|
| `/name` → `name` | string / pendente | D | Nome informado pelo provedor; texto não confiável, sem identidade econômica implícita. Razão: `provider_field_contract_checked`. | string:7 |
| `/symbol` → `symbol` | string / pendente | D | Ticker informado pelo provedor; não é identificador único. Razão: `provider_field_contract_checked`. | string:7 |
| `/decimals` → `decimals` | integer / decimal_places | D | Precisão declarada do token; não autoriza reescalar um supply já humano. Razão: `provider_field_contract_checked`. | integer:7 |
| `/holder_count` → `holder_count` | integer / count | D | Quantidade reportada de holders únicos; não equivale a compradores ou traders históricos. Razão: `provider_unique_holder_count`. | integer:7 |
| `/total_supply` → `total_supply` | decimal / pendente | P | Supply total reportado; escala humana/atômica deste caminho não comprovada. Razão: `human_vs_atomic_supply_scale_unverified`. | string:7 |
| `/circulating_supply` → `circulating_supply` | decimal / token | D | Supply circulante já em unidades humanas de token; preservar Decimal/texto, não dividir por 10^decimals. Razão: `provider_human_token_units_no_decimal_rescaling`. | string:7 |
| `/max_supply` → `max_supply` | decimal / pendente | P | Supply máximo reportado; escala e significado de sentinelas não comprovados. Razão: `human_vs_atomic_supply_scale_unverified`. | string:7 |
| `/creation_timestamp` → `creation_timestamp` | integer / unix_second | D | Atributo de criação reportado para o token, em segundos Unix; não é relógio de recepção. Razão: `provider_event_attribute_unix_seconds`. | integer:7 |
| `/open_timestamp` → `open_timestamp` | integer / unix_second | D | Atributo de abertura reportado para o token, em segundos Unix; não substitui criação. Razão: `provider_event_attribute_unix_seconds`. | integer:7 |
| `/migrated_timestamp` → `migrated_timestamp` | integer / pendente | P | Caminho reconhecido, unidade e evento de migração exatos não documentados. Razão: `exact_migration_timestamp_path_unit_unverified`. | integer:7 |
| `/liquidity` → `liquidity` | decimal / usd | D | Liquidez USD do maior/principal pool informado pelo provedor; não agregado de todos pools. Razão: `provider_main_pool_liquidity_usd`. | string:7 |
| `/initial_liquidity` → `initial_liquidity` | decimal / pendente | P | Liquidez inicial reportada; unidade e evento inicial exatos pendentes. Razão: `initial_liquidity_currency_event_scope_unverified`. | absent:7 |
| `/launchpad` → `launchpad` | string / pendente | D | Identificador do launchpad; vocabulário depende da origem. Razão: `provider_field_contract_checked`. | string:7 |
| `/launchpad_status` → `launchpad_status` | integer / pendente | D | Estado reportado pelo endpoint; enums entre origens não são equivalentes. Razão: `provider_field_contract_checked`. | integer:7 |
| `/launchpad_platform` → `launchpad_platform` | string / pendente | D | Nome da plataforma de lançamento/pool informado pelo provedor. Razão: `provider_field_contract_checked`. | string:7 |
| `/launchpad_progress` → `launchpad_progress` | decimal / ratio | D | Progresso reportado do launchpad; escala homologada somente em token_info. Razão: `provider_field_contract_checked`. | decimal:2, integer:5 |
| `/locked_ratio` → `locked_ratio` | decimal / ratio | D | Parcela de supply bloqueado quando o endpoint documenta escala e denominador. Razão: `provider_field_contract_checked`. | integer:7 |
| `/price/price` → `price` | decimal / usd_per_token | D | Preço reportado USD por token; não é garantia de execução, fill ou preço de cada pool. Razão: `provider_field_contract_checked`. | string:7 |
| `/ath_price` → `ath_price` | decimal / usd_per_token | D | Preço máximo histórico reportado; não informa o instante do pico nem market cap nesse pico. Razão: `provider_reported_ath_price_peak_time_unspecified`. | decimal:1, integer:6 |
| `/migration_market_cap` → `migration_market_cap` | decimal / usd | P | Capitalização de migração reportada; base de supply/evento/quote pendente. Razão: `migration_cap_supply_basis_event_quote_unverified`. | decimal:2, integer:5 |
| `/stat/top_10_holder_rate` → `top_10_holder_rate` | decimal / ratio | D | Fração reportada do supply concentrada no top 10; metodologia/exclusões do provedor não verificadas. Razão: `provider_top10_supply_share`. | string:7 |
| `/stat/dev_team_hold_rate` → `dev_team_hold_rate` | decimal / pendente | P | Participação reportada da equipe; escala e denominador exatos pendentes. Razão: `ratio_scale_denominator_window_unverified`. | string:7 |
| `/stat/top_rat_trader_percentage` → `top_rat_trader_percentage` | decimal / pendente | P | Participação reportada de volume dos rat traders; janela e escala pendentes. Razão: `volume_share_scale_window_unverified`. | string:7 |
| `/stat/top_bundler_trader_percentage` → `top_bundler_trader_percentage` | decimal / pendente | P | Participação reportada de volume dos bundlers; janela e escala pendentes. Razão: `volume_share_scale_window_unverified`. | string:7 |
| `/stat/top_entrapment_trader_percentage` → `top_entrapment_trader_percentage` | decimal / pendente | P | Participação reportada de volume de entrapment; janela e escala pendentes. Razão: `volume_share_scale_window_unverified`. | string:7 |
| `/stat/bot_degen_rate` → `bot_degen_rate` | decimal / pendente | P | Razão de bot/degen reportada; população/escala/janela pendentes. Razão: `ratio_scale_denominator_window_unverified`. | string:7 |
| `/stat/bot_degen_count` → `bot_degen_count` | integer / count | D | Contagem de wallets rotuladas bot/degen pelo provedor; classificação não é verificação independente. Razão: `provider_field_contract_checked`. | integer:7 |
| `/wallet_tags_stat/smart_wallets` → `smart_wallets` | integer / count | D | Contagem de wallets rotuladas smart money que detêm o token segundo o provedor. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:7 |
| `/wallet_tags_stat/renowned_wallets` → `renowned_wallets` | integer / count | D | Contagem de wallets rotuladas KOL que detêm o token segundo o provedor. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:7 |
| `/wallet_tags_stat/sniper_wallets` → `sniper_wallets` | integer / count | D | Contagem de wallets rotuladas sniper; janela/população histórica vs atual não usada para comparação automática. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:7 |
| `/link/twitter_username` → `twitter_username` | string / pendente | D | Texto de username de X; não converter em URL nem afirmar vínculo ou autoria. Razão: `provider_untrusted_metadata_text`. | string:7 |
| `/link/telegram` → `telegram` | string / pendente | D | Texto do link reportado de Telegram; sem validação de URL, identidade ou afiliação. Razão: `provider_untrusted_metadata_text`. | string:7 |
| `/link/website` → `website` | string / pendente | D | Texto do link de website; sem validação de URL, propriedade ou afiliação. Razão: `provider_untrusted_metadata_text`. | string:7 |
| `/link/description` → `description` | string / pendente | D | Descrição reportada; dado não confiável, nunca instrução executável. Razão: `provider_untrusted_metadata_text`. | string:7 |
| `/link/twitter` → `twitter` | string / pendente | P | Alias sem documentação exata; não assumir username ou URL. Razão: `undocumented_twitter_alias_not_username_or_url`. | absent:7 |
| `/price/price_1m` → `window_reference_price_1m` | decimal / usd_per_token | D | Preço reportado no início da janela 1m; não OHLC, média nem preço de criação. Razão: `provider_price_at_start_of_named_window`. | string:7 |
| `/price/volume_1m` → `volume_1m` | decimal / usd | D | Volume volume reportado em USD na janela 1m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buy_volume_1m` → `buy_volume_1m` | decimal / usd | D | Volume buy_volume reportado em USD na janela 1m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/sell_volume_1m` → `sell_volume_1m` | decimal / usd | D | Volume sell_volume reportado em USD na janela 1m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buys_1m` → `buys_1m` | integer / count | D | Contagem reportada de buys na janela 1m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/sells_1m` → `sells_1m` | integer / count | D | Contagem reportada de sells na janela 1m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/swaps_1m` → `swaps_1m` | integer / count | D | Contagem reportada de swaps na janela 1m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/price_5m` → `window_reference_price_5m` | decimal / usd_per_token | D | Preço reportado no início da janela 5m; não OHLC, média nem preço de criação. Razão: `provider_price_at_start_of_named_window`. | string:7 |
| `/price/volume_5m` → `volume_5m` | decimal / usd | D | Volume volume reportado em USD na janela 5m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buy_volume_5m` → `buy_volume_5m` | decimal / usd | D | Volume buy_volume reportado em USD na janela 5m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/sell_volume_5m` → `sell_volume_5m` | decimal / usd | D | Volume sell_volume reportado em USD na janela 5m; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buys_5m` → `buys_5m` | integer / count | D | Contagem reportada de buys na janela 5m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/sells_5m` → `sells_5m` | integer / count | D | Contagem reportada de sells na janela 5m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/swaps_5m` → `swaps_5m` | integer / count | D | Contagem reportada de swaps na janela 5m; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/price_1h` → `window_reference_price_1h` | decimal / usd_per_token | D | Preço reportado no início da janela 1h; não OHLC, média nem preço de criação. Razão: `provider_price_at_start_of_named_window`. | string:7 |
| `/price/volume_1h` → `volume_1h` | decimal / usd | D | Volume volume reportado em USD na janela 1h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buy_volume_1h` → `buy_volume_1h` | decimal / usd | D | Volume buy_volume reportado em USD na janela 1h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/sell_volume_1h` → `sell_volume_1h` | decimal / usd | D | Volume sell_volume reportado em USD na janela 1h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buys_1h` → `buys_1h` | integer / count | D | Contagem reportada de buys na janela 1h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/sells_1h` → `sells_1h` | integer / count | D | Contagem reportada de sells na janela 1h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/swaps_1h` → `swaps_1h` | integer / count | D | Contagem reportada de swaps na janela 1h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/price_6h` → `window_reference_price_6h` | decimal / usd_per_token | D | Preço reportado no início da janela 6h; não OHLC, média nem preço de criação. Razão: `provider_price_at_start_of_named_window`. | string:7 |
| `/price/volume_6h` → `volume_6h` | decimal / usd | D | Volume volume reportado em USD na janela 6h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buy_volume_6h` → `buy_volume_6h` | decimal / usd | D | Volume buy_volume reportado em USD na janela 6h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/sell_volume_6h` → `sell_volume_6h` | decimal / usd | D | Volume sell_volume reportado em USD na janela 6h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buys_6h` → `buys_6h` | integer / count | D | Contagem reportada de buys na janela 6h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/sells_6h` → `sells_6h` | integer / count | D | Contagem reportada de sells na janela 6h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/swaps_6h` → `swaps_6h` | integer / count | D | Contagem reportada de swaps na janela 6h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/price_24h` → `window_reference_price_24h` | decimal / usd_per_token | D | Preço reportado no início da janela 24h; não OHLC, média nem preço de criação. Razão: `provider_price_at_start_of_named_window`. | string:7 |
| `/price/volume_24h` → `volume_24h` | decimal / usd | D | Volume volume reportado em USD na janela 24h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buy_volume_24h` → `buy_volume_24h` | decimal / usd | D | Volume buy_volume reportado em USD na janela 24h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/sell_volume_24h` → `sell_volume_24h` | decimal / usd | D | Volume sell_volume reportado em USD na janela 24h; não somar janelas sobrepostas. Razão: `provider_volume_named_window_no_cross_window_sum`. | string:7 |
| `/price/buys_24h` → `buys_24h` | integer / count | D | Contagem reportada de buys na janela 24h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/sells_24h` → `sells_24h` | integer / count | D | Contagem reportada de sells na janela 24h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |
| `/price/swaps_24h` → `swaps_24h` | integer / count | D | Contagem reportada de swaps na janela 24h; zero preserva zero, ausência não vira zero. Razão: `provider_transaction_count_named_window`. | integer:7 |

### market_rank

| Caminho bruto relativo → métrica | Tipo / unidade | D/P | Significado e fronteira | Observação de formato na amostra |
|---|---|---|---|---|
| `/name` → `name` | string / pendente | D | Nome informado pelo provedor; texto não confiável, sem identidade econômica implícita. Razão: `provider_field_contract_checked`. | string:5 |
| `/symbol` → `symbol` | string / pendente | D | Ticker informado pelo provedor; não é identificador único. Razão: `provider_field_contract_checked`. | string:5 |
| `/decimals` → `decimals` | integer / pendente | P | Precisão declarada do token; não autoriza reescalar um supply já humano. Razão: `exact_path_scale_contract_unverified`. | absent:5 |
| `/holder_count` → `holder_count` | integer / count | D | Quantidade reportada de holders únicos; não equivale a compradores ou traders históricos. Razão: `provider_unique_holder_count`. | integer:5 |
| `/total_supply` → `total_supply` | decimal / pendente | P | Supply total reportado; escala humana/atômica deste caminho não comprovada. Razão: `human_vs_atomic_supply_scale_unverified`. | integer:5 |
| `/circulating_supply` → `circulating_supply` | decimal / pendente | P | Supply circulante reportado; prova de escala é específica por endpoint. Razão: `human_vs_atomic_supply_scale_unverified`. | absent:5 |
| `/max_supply` → `max_supply` | decimal / pendente | P | Supply máximo reportado; escala e significado de sentinelas não comprovados. Razão: `human_vs_atomic_supply_scale_unverified`. | absent:5 |
| `/creation_timestamp` → `creation_timestamp` | integer / unix_second | D | Atributo de criação reportado para o token, em segundos Unix; não é relógio de recepção. Razão: `provider_event_attribute_unix_seconds`. | integer:5 |
| `/open_timestamp` → `open_timestamp` | integer / unix_second | D | Atributo de abertura reportado para o token, em segundos Unix; não substitui criação. Razão: `provider_event_attribute_unix_seconds`. | integer:5 |
| `/migrated_timestamp` → `migrated_timestamp` | integer / pendente | P | Caminho reconhecido, unidade e evento de migração exatos não documentados. Razão: `exact_migration_timestamp_path_unit_unverified`. | absent:5 |
| `/liquidity` → `liquidity` | decimal / usd | P | Liquidez USD reportada; documentação não resolve pool principal vs soma de pools. Razão: `ranking_liquidity_pool_vs_aggregate_scope_unverified`. | decimal:3, integer:2 |
| `/initial_liquidity` → `initial_liquidity` | decimal / pendente | P | Liquidez inicial reportada; unidade e evento inicial exatos pendentes. Razão: `initial_liquidity_currency_event_scope_unverified`. | decimal:2, integer:3 |
| `/launchpad` → `launchpad` | string / pendente | P | Identificador do launchpad; vocabulário depende da origem. Razão: `endpoint_launchpad_vocabulary_unverified`. | string:5 |
| `/launchpad_status` → `launchpad_status` | string / pendente | P | Estado reportado pelo endpoint; enums entre origens não são equivalentes. Razão: `endpoint_launchpad_vocabulary_unverified`. | string:5 |
| `/launchpad_platform` → `launchpad_platform` | string / pendente | D | Nome da plataforma de lançamento/pool informado pelo provedor. Razão: `provider_field_contract_checked`. | string:5 |
| `/launchpad_progress` → `launchpad_progress` | decimal / pendente | P | Progresso reportado do launchpad; escala homologada somente em token_info. Razão: `endpoint_ratio_scale_denominator_unverified`. | absent:5 |
| `/locked_ratio` → `locked_ratio` | decimal / pendente | P | Parcela de supply bloqueado quando o endpoint documenta escala e denominador. Razão: `endpoint_ratio_scale_denominator_unverified`. | absent:5 |
| `/price` → `price` | decimal / usd_per_token | D | Preço reportado USD por token; não é garantia de execução, fill ou preço de cada pool. Razão: `provider_field_contract_checked`. | decimal:5 |
| `/market_cap` → `market_cap` | decimal / usd | D | Capitalização reportada em USD; supply utilizado não informado, não rotular FDV nem recalcular. Razão: `provider_reported_usd_cap_supply_basis_unspecified`. | decimal:4, integer:1 |
| `/history_highest_market_cap` → `history_highest_market_cap` | decimal / usd | D | Maior capitalização histórica reportada em USD; supply e instante do pico não informados. Razão: `provider_reported_usd_cap_supply_basis_unspecified`. | integer:5 |
| `/buy_tax` → `buy_tax` | decimal / pendente | P | Taxa reportada na compra; escala desta origem pendente; string vazia conservada, sem conversão em zero. Razão: `ranking_tax_scale_unverified_empty_sentinel_not_converted`. | string:5 |
| `/sell_tax` → `sell_tax` | decimal / pendente | P | Taxa reportada na venda; escala desta origem pendente; string vazia conservada, sem conversão em zero. Razão: `ranking_tax_scale_unverified_empty_sentinel_not_converted`. | string:5 |
| `/rug_ratio` → `rug_ratio` | decimal / score_0_1 | D | Escore de risco reportado entre 0 e 1; não tratar como probabilidade calibrada ou veredito. Razão: `provider_risk_score_not_calibrated_probability`. | integer:5 |
| `/bundler_rate` → `bundler_rate` | decimal / pendente | P | Participação reportada de volume de bots/bundlers; não concentração de supply. Razão: `volume_share_scale_window_unverified`. | integer:5 |
| `/bundler_trader_amount_rate` → `bundler_trader_amount_rate` | decimal / pendente | P | Semântica exata deste caminho pendente; não alias de bundler_rate. Razão: `exact_ranking_path_semantics_unverified_no_alias`. | absent:5 |
| `/insider_rate` → `insider_rate` | decimal / pendente | P | Semântica exata deste caminho pendente; não alias de rat_trader_amount_rate. Razão: `exact_ranking_path_semantics_unverified_no_alias`. | absent:5 |
| `/rat_trader_amount_rate` → `rat_trader_amount_rate` | decimal / pendente | P | Participação reportada de volume de insiders; janela e escala pendentes. Razão: `volume_share_scale_window_unverified`. | integer:5 |
| `/entrapment_ratio` → `entrapment_ratio` | decimal / pendente | P | Razão de negociação reportada; denominador, escala e janela pendentes. Razão: `ratio_scale_denominator_window_unverified`. | decimal:3, integer:2 |
| `/top_10_holder_rate` → `top_10_holder_rate` | decimal / ratio | D | Fração reportada do supply concentrada no top 10; metodologia/exclusões do provedor não verificadas. Razão: `provider_top10_supply_share`. | decimal:3, integer:2 |
| `/top70_sniper_hold_rate` → `top70_sniper_hold_rate` | decimal / pendente | P | Razão reportada de holdings atuais do grupo top 70 snipers; definição/escala pendentes. Razão: `ratio_scale_denominator_window_unverified`. | decimal:1, integer:4 |
| `/dev_team_hold_rate` → `dev_team_hold_rate` | decimal / pendente | P | Participação reportada da equipe; escala e denominador exatos pendentes. Razão: `ratio_scale_denominator_window_unverified`. | integer:5 |
| `/burn_ratio` → `burn_ratio` | decimal / pendente | P | Caminho exato não documentado; não alias de dev_token_burn_ratio. Razão: `exact_ranking_path_semantics_unverified_no_alias`. | integer:5 |
| `/bot_degen_rate` → `bot_degen_rate` | decimal / pendente | P | Razão de bot/degen reportada; população/escala/janela pendentes. Razão: `ratio_scale_denominator_window_unverified`. | decimal:1, integer:4 |
| `/swaps` → `swaps` | integer / count | P | Contagem de swaps no intervalo da requisição; intervalo ainda ausente no Fact. Razão: `queried_window_not_encoded_in_fact`. | integer:5 |
| `/buys` → `buys` | integer / count | P | Contagem de compras no intervalo da requisição; intervalo ainda ausente no Fact. Razão: `queried_window_not_encoded_in_fact`. | integer:5 |
| `/sells` → `sells` | integer / count | P | Contagem de vendas no intervalo da requisição; intervalo ainda ausente no Fact. Razão: `queried_window_not_encoded_in_fact`. | integer:5 |
| `/visiting_count` → `visiting_count` | integer / pendente | P | Contagem/heat de atenção a esclarecer; não quantidade de holders. Razão: `visit_heat_unique_population_window_unverified`. | integer:5 |
| `/smart_degen_count` → `smart_degen_count` | integer / count | D | Contagem de wallets rotuladas smart money mantendo o token segundo o provedor. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:5 |
| `/renowned_count` → `renowned_count` | integer / count | D | Contagem de wallets KOL mantendo o token segundo o provedor. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:5 |
| `/bot_degen_count` → `bot_degen_count` | integer / count | D | Contagem de wallets rotuladas bot/degen pelo provedor; classificação não é verificação independente. Razão: `provider_tagged_wallet_count_methodology_unspecified`. | integer:5 |
| `/volume` → `volume` | decimal / usd | P | Volume reportado em USD no intervalo da requisição; intervalo ainda ausente no Fact. Razão: `queried_window_not_encoded_in_fact`. | decimal:2, integer:3 |
| `/price_change_percent` → `price_change_percent` | decimal / pendente | P | Variação reportada; tabela de resposta diz percentual e filtros dizem razão. Escala não resolvida, sem multiplicação/divisão por 100. Razão: `percent_vs_ratio_documentation_conflict`. | decimal:3, integer:2 |
| `/price_change_percent1m` → `price_change_percent1m` | decimal / pendente | P | Variação reportada; tabela de resposta diz percentual e filtros dizem razão. Escala não resolvida, sem multiplicação/divisão por 100. Razão: `percent_vs_ratio_documentation_conflict`. | decimal:3, integer:2 |
| `/price_change_percent5m` → `price_change_percent5m` | decimal / pendente | P | Variação reportada; tabela de resposta diz percentual e filtros dizem razão. Escala não resolvida, sem multiplicação/divisão por 100. Razão: `percent_vs_ratio_documentation_conflict`. | decimal:3, integer:2 |
| `/price_change_percent1h` → `price_change_percent1h` | decimal / pendente | P | Variação reportada; tabela de resposta diz percentual e filtros dizem razão. Escala não resolvida, sem multiplicação/divisão por 100. Razão: `percent_vs_ratio_documentation_conflict`. | decimal:4, integer:1 |
| `/is_honeypot` → `is_honeypot` | integer / boolean_code_01 | D | Flag do provedor 1/0 para honeypot; documentação limita a BSC/Base, demais cadeias sem homologação. Razão: `provider_integer_flag_not_independent_security_verdict`. | integer:5 |
| `/is_open_source` → `is_open_source` | integer / boolean_code_01 | D | Flag do provedor 1/0 de código aberto; não prova de segurança do contrato. Razão: `provider_integer_flag_not_independent_security_verdict`. | integer:5 |
| `/is_renounced` → `is_renounced` | integer / boolean_code_01 | D | Flag do provedor 1/0 de renúncia; não alias automático de mint/freeze. Razão: `provider_integer_flag_not_independent_security_verdict`. | integer:5 |
| `/renounced_mint` → `renounced_mint` | integer / boolean_code_01 | D | Flag do provedor 1/0 de renúncia de mint; aplicabilidade documentada somente Solana. Razão: `provider_integer_flag_not_independent_security_verdict`. | null:5 |
| `/renounced_freeze_account` → `renounced_freeze_account` | integer / boolean_code_01 | D | Flag do provedor 1/0 de renúncia de freeze; aplicabilidade documentada somente Solana. Razão: `provider_integer_flag_not_independent_security_verdict`. | null:5 |
| `/is_wash_trading` → `is_wash_trading` | boolean / pendente | D | Booleano literal reportado para wash trading; metodologia de detecção não verificada. Razão: `provider_boolean_flag_not_independent_security_verdict`. | boolean:5 |
| `/creator_close` → `creator_close` | boolean / pendente | D | Booleano literal reportado de estado do criador; não extrair eventos/horários deste flag. Razão: `provider_boolean_flag_not_independent_security_verdict`. | boolean:5 |

## Resultado local e próximos gates

Validação final: **249 testes unitários passaram**, sem skips/falhas, em Python 3.14.5, Pydantic 2.13.4, pytest 9.1.1 e HTTPX 0.28.1 locais. O projeto declara Python >=3.13,<3.14 e Pydantic 2.13.5: a validação no runtime Docker alvo continua necessária. Nenhuma dependência foi instalada nesta tarefa.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest -c data_platform/pyproject.toml -p no:cacheprovider -o addopts='' data_platform/tests/test_contracts.py data_platform/tests/test_normalize.py data_platform/tests/test_importer.py -q
```

Conferência estática adicional: 122 IDs únicos, conjunto endpoint/path e tipo/unidade/qualidade iguais aos specs ativos; cada registro possui razão, descrição e fonte. Isso verifica cobertura do inventário, não a veracidade do dado externo.

Na mesma amostra de 10 capturas, normalização v3 em memória: **696 fatos**; qualidades `{"invalid": 46, "missing": 32, "unverified": 181, "valid": 437}`; **1023 nós unknown**, incluindo containers/ocorrências repetidas; issues `{"invalid_field": 46}`. Isso é preview local, não replay persistido nem verificação dos dois sinks.

As 15 ocorrências R antes rejeitadas por boolean agora conservam código inteiro: 10 flags de open-source/renúncia satisfazem o contrato, 5 honeypot em eth/stable/arc continuam unverified por aplicabilidade. Os preços/ATH/preços de janela zero continuam rejeitados; timestamps de abertura zero também não viram época 1970. A ausência da fonte não foi preenchida com estimativa.

Regressões v1/v2 permanecem testadas: limites de memória, IDs, precisão, relógios, identidade, erros do provedor, estrutura exata de envelope duplo, pointers originais e enum de lançamento. A versão v3 produz IDs distintos e não altera fatos v1/v2 já armazenados. Replay e seleção de versões são responsabilidade do coordenador e não foram executados nesta tarefa.

Gates restantes: suite Docker na imagem alvo; revisão das consultas para manter endpoint/contrato/janela; replay explícito de v3; ampliar prova real por chain sem inferir dos formatos; decidir dimensão temporal dos agregados R; homologar campos pendentes e construir adapters de outros endpoints. Bruto dos demais endpoints pode ser preservado, mas extração sem adapter permanece unsupported. Implementação de estratégias/trading/UI está fora deste escopo.

Registro estruturado: `contracts/gmgn-p0-v3.json`. Ele não é carregado como configuração pelo runtime; mudança interpretativa exige versão do adapter e teste.
