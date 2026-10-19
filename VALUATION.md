# CP-VALUATION-01 — consulta de preço e market cap

> Estado atual: [4.1 concluída — contrato econômico mínimo](ECONOMIC_INPUTS.md). A agenda 3.4 continua live; os snapshots e pendências descritos abaixo pertencem aos checkpoints anteriores.

Concluído em 09/09/2026. A implementação já estava implantada na API; esta rodada revisou os contratos, executou os testes de fechamento e repetiu a conferência sobre dados reais existentes. Owner A0, sem subagentes. Coleta preservada no perfil Solana de três jobs/60 s.

## Contrato entregue

`GET /v1/observations/{fact_id}/valuation` exige `source_kind` e `knowledge_cutoff` UTC. A resposta inclui contexto, versão `gmgn-valuation-v1`, valores decimais em texto, método, qualidade e IDs dos insumos.

| Fonte | Preço | Market cap |
|---|---|---|
| Trending / market_rank | `price`, reportado em USD/token | `market_cap`, reportado em USD, sem presumir a base de supply |
| token_info | `price.price`, reportado em USD/token | Estimativa `price.price × circulating_supply`, explicitamente derivada |
| Trenches | Alias `price` ainda não homologado | Apenas `usd_market_cap` sob contrato; o alias `market_cap` não o substitui |

A fórmula do token info foi reconferida na [documentação oficial GMGNAI](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md); os campos de listas foram confrontados com [GMGNAI Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md). Evidência local e contratos v4 continuam determinando o uso permitido.

Insumos pertencem à mesma captura, item, token, fonte e interpretação, publicados até o corte solicitado. Não se usa supply de outra captura ou interpretação posterior. O cálculo decimal exato não depende da precisão global nem aplica `decimals` novamente. Métodos diferentes têm `series_key` diferente. A rota consulta uma observação específica; estado mais recente, ATH, curva diária completa e marcos são entregas posteriores.

## Provas do fechamento

- **24 testes aprovados, zero falhas/erros/skips, 0,954 s:** 23 casos unitários e uma integração com PostgreSQL real em schema temporário exclusivo. Cobrem versões, captura/item, corte temporal, separação fixture/GMGN, evidência ausente e preservação dos fatos. Nenhuma chamada GMGN faz parte da suíte.
- **Três consultas sobre capturas reais já armazenadas:** Trending reportado disponível (8,98 ms), token info derivado disponível (6,77 ms), Trenches indisponível (72,52 ms). Comparação com bytes brutos/SHA256 e preservação dos runs aprovada. Latências pontuais, sem alegação de benchmark sustentado.
- No snapshot da prova: **489 capturas GMGN e 22 fixtures prontas**, seis serviços saudáveis, zero reinícios dos containers observados, filas e erros pendentes zerados. As fixtures do teste integrado ficaram em schema temporário separado da coleta.
- A implementação inicial possui 53 testes focados registrados em `.scratch/checkpoint-valuation-20260909/focused.xml`. Não somar aos 24 como casos únicos; há sobreposição.

Evidências desta rodada: `.scratch/checkpoint-valuation-close-20260909/`, arquivos `valuation.xml`, `valuation.log`, `live-result.json`, `services.txt` e manifestos de hashes. A suíte completa da base não foi reexecutada. Código de aplicação, configuração, imagens e serviços não foram alterados neste fechamento.

## Runtime e próximo recorte

API: `midas-data-app:valuation-v1-20260909`, SHA256 `54c39b57013893a914d7283cf686b898ae0d8c0a5834701dce55ed7bce9114be`. Worker permanece em `catalog-v1-20260909`; valuation é cálculo de leitura na API e não requer nova normalização ou migration. Coletor permanece em `direct-v1-20260909`.

Checkpoint concluído para a consulta individual. Permanecem pendentes aliases econômicos Trenches, capacidade representativa, curvas/marcos e estratégias. O próximo recorte pequeno é o registro persistente dos tokens observados, com identidade `chain + address` e proveniência da descoberta, antes de alterar o agendamento de acompanhamento. Saída do ranking não deve apagar a identidade registrada. Política de retenção, prioridade e frequência precisa ficar explícita; não ampliar automaticamente a taxa atual.
