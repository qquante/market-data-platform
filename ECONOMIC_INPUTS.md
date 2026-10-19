# 4.1 — contrato econômico mínimo da trajetória

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

> Atualização 4.2: série temporal agora implantada; ver [TRAJECTORY.md](TRAJECTORY.md). Nessa etapa também foi corrigido o LIMIT 6 residual do SQL para atender aos 12 pointers declarados abaixo. O relatório original da 4.1 é preservado como histórico.

**Concluído e implantado em 10/09/2026.** Checkpoint CP-ECONOMIC-INPUTS-01, A0, sem subagentes. O recorte fecha os insumos da próxima série temporal; não implementa a série, curva diária ou marcos.

## Contratos de uso

| Campo | token_info | Trending / market_rank | Trenches |
|---|---|---|---|
| Preço atual | `price.price`, USD/token | `price`, USD/token | Alias `price` indisponível |
| Market cap atual | Derivada: preço × supply circulante da mesma captura | `market_cap` reportado, USD; base de supply não especificada | Somente `usd_market_cap` documentado; ausente na amostra real. Alias `market_cap` indisponível |
| Supply circulante | `circulating_supply`, tokens humanos | Indisponível para este cálculo | Indisponível para este cálculo |
| Liquidez | `liquidity`, USD do maior pool reportado | Escopo pool/agregado não homologado | Escopo pool/agregado não homologado |
| Volume de 5 min | `price.volume_5m`, USD | `volume`, somente quando a janela explícita da captura é 5m | Sem caminho de 5m homologado |
| Decimals | Metadado; não reaplicar à supply humana | Não homologado neste recorte | Não homologado neste recorte |
| Supply total / máxima | Indisponíveis como operandos/fallback do cálculo | Indisponíveis como operandos/fallback do cálculo | Indisponíveis como operandos/fallback do cálculo |

Preço, fórmula e escopo da liquidez foram reconferidos na [referência oficial Token](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md). A distinção entre capitalização reportada, aliases e janela de volume segue a [referência oficial Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md). Consulta em 10/09/2026; versão executável local `gmgn-trajectory-inputs-v1`.

Unidade conhecida não prova igualdade entre pools, agregações ou métodos. Liquidez reportada não é profundidade executável. Supply total/máxima não substitui circulating supply; preço ATH × supply atual não gera ATH histórico de market cap. Volume rolling de 5m não pode ser somado entre snapshots para calcular volume diário.

## Implementação

A rota autenticada existente `GET /v1/observations/{fact_id}/valuation`, com fonte e corte de conhecimento UTC obrigatórios, agora acrescenta `valuation.economic_inputs`:

- `version`: versão da política de insumos;
- `fields`: seis campos de contexto, cada um com estado, motivo quando indisponível, valor textual, pointer/fact_id quando existentes, qualidade, contrato e disponibilidade;
- `expected_unit` e `scope`: unidade exigida e restrição de uso da política; não homologam um campo indisponível;
- `contract_supported`: informa se existe contrato permitido para aquele campo/endpoint. Isso não garante presença, número válido ou janela correta.

Preço e capitalização permanecem nos campos existentes `valuation.price` e `valuation.market_cap`, sem modificar métodos, valores ou `series_key`. O contexto é opcional: ausência ou erro em liquidez/volume não invalida um market cap calculável.

A leitura busca até **12 pointers conhecidos**, reutiliza a mesma consulta SQL e exige a mesma captura, item, fonte, token, interpretação e corte temporal. Valores permanecem decimais textuais; faltante, inválido e não homologado não viram zero. `reason=absent` significa ausência do fato elegível naquele pointer, não uma busca em outras capturas.

A mudança é pequena em `midas_data/valuation.py`. Não há nova tabela, endpoint, serviço, dependência, normalizador, migration, coleta adicional ou reprocessamento histórico. Os contratos normalizados continuam v4; a política de leitura é versionada separadamente.

## Validação e runtime

**38 testes aprovados**, zero falhas/erros/skips: 14 novos casos econômicos, regressões de valuation e uma integração PostgreSQL em schema exclusivo. Provados: precisão decimal, zero observado, ausência, números inválidos, janela 1h que não vira 5m, parâmetros conflitantes, aliases não homologados, contexto opcional e isolamento entre captura/item/revisão/corte de conhecimento.

O primeiro helper de testes não adicionava os metadados anexados na publicação dos fatos e falhou com `normalizer_version` ausente. Corrigido o helper; o teste SQL real já passava. Não houve alteração na aplicação para mascarar essa falha.

**Prova real:** mesmas três capturas Solana comparadas antes/depois, com SHA256 do bruto conferido. Token info disponibilizou circulating supply, decimals, liquidez e volume 5m; Trending disponibilizou volume 5m e manteve liquidez indisponível; Trenches manteve os insumos sem contrato indisponíveis. Todos os valores liberados coincidiram com seus pointers no bruto. Valuation anterior e hashes dos registros de interpretação permaneceram exatamente iguais.

Apenas a API foi atualizada para `midas-data-app:economic-inputs-v1-20260910`. Worker conserva `tracking-v1-20260910`, coletor conserva `tracking-v2-20260910`; os cinco outros containers da base mantiveram IDs. A tag padrão da aplicação no env privado foi atualizada para a versão compatível; credenciais preservadas.

Evidências: `.scratch/checkpoint-economic-inputs-20260910/`, incluindo `contract-tests.xml`, build/deploy, seleção fixa, `live-before.json`, `live-after.json`, comparação de serviços e aceite.

## Próximo recorte

**4.2:** série temporal por token e método, com corte de conhecimento, revisões e gaps explícitos, consumindo somente campos elegíveis. O catálogo econômico amplo e os aliases Trenches continuam pendentes de evidência; não impedem iniciar a série com os métodos já homologados. Medições extensas continuam adiadas.
