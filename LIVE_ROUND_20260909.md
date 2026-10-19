# CP-LIVE-01 — primeira rodada real limitada, 09/09/2026

Rodada concluída em 2026-09-09T18:45:39Z; integridade física conferida em 18:46:58Z. Owner A0, sem agentes adicionais. Foram feitas exatamente três chamadas de mercado, somente leitura, na Solana. A base Docker permaneceu na imagem `context-v1-20260909`, normalizador `gmgn-p0-v4`, sem rebuild, migração ou alteração de código de aplicação.

## O que entrou

| Origem | Bytes originais | Fatos | Campos catalogados | Recebimento até publicação | Recebimento até os dois destinos |
|---|---:|---:|---:|---:|---:|
| market_rank | 7125 | 132 | 296 | 0.084 s | 0.679 s |
| trenches | 785823 | 4549 | 23334 | 1.25 s | 3.377 s |
| token_info | 5507 | 68 | 201 | 0.64 s | 1.167 s |

Total: **798.455 bytes originais, 4.749 fatos e 23.831 campos catalogados**. Estes são contagens de registros/ocorrências, não de parâmetros distintos nem de valores todos homologados. As 15 fixtures anteriores continuam separadas por `source_kind=fixture`; as três respostas reais usam `source_kind=gmgn` e `evidence_kind=original_response`.

Pedidos efetivamente enviados:

- Trending (`market_rank`): `chain=sol`, `interval=5m`, `limit=3`, `order_by=swaps`, `direction=desc`. Resposta: três itens, no envelope `/data/data/rank`; adapter existente reconheceu a forma. Métricas de atividade preservaram a janela: `volume_5m`, `swaps_5m`, `buys_5m`, `sells_5m`.
- Trenches: `chain=sol`, corpo `version=v2`; categorias `new_creation`, `near_completion`, `completed`, cada uma com `limit=2`, `filters=[offchain,onchain]`, `launchpad_platform_v2=true`, `quote_address_type=[4,5,3,1,13,0]`. A resposta trouxe **60 itens por categoria, 180 no total**. O limite solicitado não limitou a resposta nesta observação. Ainda não foi determinada a causa; isso não prova regra fixa do provedor.
- `token_info`: um endereço efetivamente retornado pelo Trending, sem escolha manual ou consulta financeira. Uma resposta de detalhes.

O cliente existente `GmgnOpenApiClient.call_with_evidence` conservou os bytes HTTP antes de qualquer interpretação. Cada resposta foi salva antes do ingresso, com UUID estável e SHA256; nenhuma resposta serializada de CLI foi tratada como original. Credencial ficou apenas em memória. URL efetiva e cabeçalhos de resposta permitidos foram preservados nos sidecars locais `*.http.json`, sem credenciais; esses cabeçalhos ainda não são dimensões consultáveis do manifesto na API.

## Verificações executadas

- HTTP 200 nas três chamadas; nenhum retry ao provedor. Durações de resposta: 0,340 s, 0,810 s e 0,270 s.
- Releitura dos três brutos pela API: hashes iguais às respostas originais salvas.
- Releitura física dos três lotes no DuckLake, anexado `READ_ONLY`, e no ClickHouse: 4.749 fatos, contagens/checksums/recibos concordantes. Nenhuma chamada de criação de schema nessa auditoria.
- Seis pares de consultas de contexto e série passaram, abrangendo as três origens e as três categorias de Trenches. IDs, origem, valor textual exato e seleção temporal conferidos. As seis leituras de contexto levaram entre 2,496 e 81,133 ms nesta amostra; não é benchmark de capacidade.
- Reenvio local de uma captura já aceita foi reconhecido como duplicado, reutilizando bruto e metadados. Nenhuma quarta chamada GMGN.
- Cinco serviços `running/healthy`. Publicação, normalização e ambos os destinos sem pendências; zero erros analíticos pendentes. O contador cumulativo do normalizador já era 1 antes da rodada e permaneceu 1; não foi investigada sua causa neste checkpoint, nem se afirma histórico sem falhas.
- Espaço livre efetivo medido: aproximadamente 365,1 GiB. Não se extrapolou crescimento diário a partir de três respostas.

Os relógios ficam em UTC: início local da chamada, recebimento, registro, publicação e confirmação. `source_event_at` ficou ausente; recebimento não foi promovido a instante do evento no provedor. O início local antecede eventual espera do gate do cliente e não comprova instante exato de despacho na rede.

## Qualidade observada

| Origem | Válidos | Não verificados | Ausentes/nulos | Inválidos |
|---|---:|---:|---:|---:|
| Trending | 71 | 54 | 6 | 1 |
| Trenches | 2.860 | 900 | 503 | 286 |
| Token info | 57 | 9 | 2 | 0 |

No Trending, um `open_timestamp=0`. No Trenches, 120 `open_timestamp=0`, 120 `complete_timestamp=0`, um `created_timestamp=0` e 45 `complete_cost_time` negativos. Os contratos vigentes marcaram esses **287 valores** como inválidos, preservando o original; não viraram datas de 1970 nem durações utilizáveis. Distinguir sentinela de evento ainda não ocorrido de dado inválido exige um contrato explícito antes de mudar essa classificação.

Os campos de Trenches `price`, `market_cap`, `liquidity`, `total_supply` e duração ainda têm limites de interpretação previamente documentados. A resposta real comprova presença, forma e persistência; não homologa moeda, alias ou base de oferta apenas por comparação numérica. Os campos fora dos contratos seguem no bruto e catálogo, sem descarte e sem promoção automática a fato validado.

## Próximo checkpoint

1. Usar estas respostas salvas, sem repetir chamadas, para definir estados de tempo/sentinelas e fechar os caminhos econômicos que tenham comprovação de semântica.
2. Conferir o contrato do tamanho da resposta Trenches e estabelecer orçamento de bytes/trabalho para a coleta; não depender apenas do `limit` enviado.
3. Em seguida, conectar o coletor contínuo com orçamento compartilhado, retries/checkpoints e cobertura explícita, medindo entrada/saída e crescimento. Esta rodada não certifica coleta contínua, pico sustentado, universo completo, primeiros segundos ou trajetórias de tokens.

Coleta contínua, executores, estratégias e análises de trajetória permanecem desligados/não implementados neste recorte. A etapa histórica de migração foi cancelada pelo usuário após o reset e não é reaberta aqui.

## Evidência e referências

Em `.scratch/checkpoint-live-20260909/`: `collect_once.py` (rodada única protegida contra repetição), `*.raw`, `*.metadata.json`, `*.http.json`, `*.receipt.json`, `*.capture.json`, `collection-result.json`, `physical_probe.py`, `physical-result.json`, `query_probe.py`, `query-result.json`, `summary.json`, `services.json` e estados antes/depois. Os scripts de prova são locais, não um coletor de produção. O probe de consultas inicialmente usou o nome incorreto `holders` na seleção da amostra; foi corrigido para `holder_count` e passou, sem alteração da aplicação ou novas chamadas GMGN.

Contratos e escopo: [fontes](MARKET_SOURCES.md), [contexto](OBSERVATION_CONTEXT.md), [trajetória](TRAJECTORY_CONTRACTS.md). Referências oficiais consultadas para os pedidos: [GMGN Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md) e [GMGN Token](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md).
