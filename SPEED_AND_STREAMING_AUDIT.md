> Atualização CP-DATA-SPEED-13: a meta 3/5 foi testada e não homologada; perfil mantido 8/16. [Estado atual, ensaios e limitações](SPEED_3S_5S_RESULTS.md). Este documento conserva evidências históricas.

# Frequência e streaming GMGN — 11/09/2026

> **Atualização CP-DATA-SPEED-12:** [otimização implantada e medida](SPEED_RESULTS.md). Perfil ativo 8 s / 16 s, painel 2 s; teste curto aprovado, histórico ~26 s. 5 s / 10 s ainda acumula fila. Três capturas NUL em quarentena; cobertura ampla continua aberta. Os valores anteriores abaixo são históricos.

Diagnóstico e proposta. Nenhum timer de produção foi alterado: Trenches 25 s, Trending 60 s e orçamento local de 1 peso/s. Esses intervalos contiveram o crescimento das filas internas; não são um mínimo demonstrado da GMGN.

## Medição real

Amostra: 12 capturas já armazenadas, seis por rota; corte 2026-09-11T19:31:43 UTC. Os 12 downloads locais tiveram SHA-256 conferido. Nenhuma consulta adicional à fonte.

| Medida | Trenches | Trending |
|---|---:|---:|
| Pedido → resposta, mediana | 669 ms | 328 ms |
| Faixa observada | 489–779 ms | 315–375 ms |
| Intervalo médio entre respostas | 27,00 s | 62,45 s |
| Bytes médios de bruto | 746.466 | 232.605 |
| Fatos normalizados por captura | 4.563,5 | 4.400 |

Pedido → resposta inclui o caminho do cliente e eventual espera anterior ao transporte. Não isola tempo de servidor ou evento on-chain → GMGN. Não é p95 nem teste de carga sustentada.

Em 19:33:06 UTC: ClickHouse sem fila ou erros, Lake com três lotes pendentes (mais antigo 4,56 s), normalizador sem fila. Recuperação no perfil reduzido não homologa maior frequência. Havia duas capturas em quarentena: bruto aceito não significa qualidade integral.

## O que usava WebSocket

- Fomo: `wss://prod-api.fomo.family/ws`, coletor persistente em `src/midas_fomo/collector.py`, assinaturas `topicType/topicId`, preço e detalhes. Havia limite de assinaturas individuais e fallback REST. Ranking por deltas/checkpoints está documentado em `docs/FOMO_COLLECTOR.md`. É outra fonte.
- Robinhood Trenches: `wss://rhtrenches.com/ws`, coletor persistente em `src/midas_gmgn/robinhood_trenches.py`, fills/status e reconciliação REST. É o feed de carteiras acompanhadas de Robinhood, não Trenches SOL da GMGN.
- Interface GMGN: captura passiva validada de `wss://ws.gmgn.ai/v2/ws`, em `docs/gmgn_api/LIVE_HTTP_WS_CAPTURE_AND_ADAPTER.md`; fixture de `following_wallet_activity` e outros canais de tokens observados. Isso não homologa um coletor completo e contínuo de Trenches + Trending na nova base.

O catálogo oficial consultado publica Trenches e Trending como HTTP. A referência a `version` para reconexão WS pertence a **hot-searches**; sozinha não informa endpoint, autenticação e recuperação de um stream de Trenches. Não encontrar esse contrato aqui não prova que inexista outro acesso oferecido pela GMGN.

## Alvo 1 s / 2 s

Pesos oficiais: Trenches 3, rank 1. Uma cadeia e uma configuração por rota: `3/1 + 1/2 = 3,5 pesos/s`; 129.600 consultas/dia. Enriquecimento, retries, outras cadeias/janelas e consumidores são adicionais. Os 8 pesos/s configurados no Redis local não provam a quota contratual efetiva.

Extrapolação desta amostra com a representação atual: **74,54 GB/dia de bruto descomprimido** e **584,37 milhões de fatos/dia**. Não é tráfego comprimido, tamanho final no disco nem teste nesse ritmo. O crescimento dos fatos precisa ser resolvido antes de aumentar amplamente a frequência.

## Próximas partes propostas

1. **Escrita eficiente:** preservar bruto, relógios e hash; estado atual mais alterações históricas com checkpoints reconstruíveis. Preservar também observações sem mudança para não perder evidência temporal. Corrigir a consulta global de identidades no ClickHouse com estrutura adequada, mantendo conflitos e replay. Lotes e workers separados já existem; melhorar esses caminhos.
2. **HTTP rápido:** reutilizar conexões; separar descoberta de enriquecimento com concorrência pequena e um orçamento compartilhado. Hoje a fonte executa em série e espera 250 ms por iteração. Holders lento não deve atrasar Trenches. Reservar 3,5 pesos/s para descoberta mais enriquecimento, dentro da quota efetiva.
3. **Painel rápido:** leitura leve do último estado validado, proveniência e idade por dado; SSE/WS ou consulta leve de 1 s. Hoje a operação atualiza a cada 10 s. Consultas históricas pesadas mantêm fluxo separado.
4. **Stream da fonte:** homologar o WS GMGN correspondente se o acesso existir, verificando campos, cobertura, relógios, reconexão e perdas. Push para mudanças; HTTP para snapshot/reconciliação. WS não elimina armazenamento nem fornece automaticamente todos os campos.
5. **Rampa real:** 5 s/10 s, depois alvo 1 s/2 s, verificando novidade das respostas, latência ponta a ponta, quota, bytes/fatos e fila sem crescimento. Esse teste curto não homologa operação por dias.

Sem teto fixo de tokens. A frequência varia por família; listas a cada 1–2 s não equivalem a consultar todas as famílias de dezenas de milhares de tokens individualmente nessa frequência. Estratégias permanecem adiadas.

## Evidência

- [Medições e IDs](../outputs/gmgn-speed-audit-20260911/measurements.json).
- [Estado real](../outputs/gmgn-speed-audit-20260911/status.json).
- [Gargalo e implantação anterior](CONTINUOUS_COLLECTION.md).
- [Catálogo oficial](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md).
- [CLI oficial](https://github.com/GMGNAI/gmgn-skills/blob/main/src/commands/market.ts).
- Código: `src/midas_gmgn/data_collector.py:523`, `src/midas_gmgn/openapi.py:345`, `data_platform/midas_data/analytics.py:495`, `data_platform/midas_data/ui/app.js:302`.
