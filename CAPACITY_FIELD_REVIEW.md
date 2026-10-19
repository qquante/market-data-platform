# CP-CAPACITY-01 — custo por armazenamento e revisão econômica

Checkpoint concluído em **2026-09-09T19:45:41.955670+00:00**, A0, sem subagentes. Coleta contínua preservada: Solana, Trending/Trenches/token info, três jobs de 60 segundos. Este recorte entrega medição repetível e revisão dos contratos; não aumenta taxa, cria estratégia ou certifica crescimento diário.

## Estado conferido

- Seis serviços Docker saudáveis. API recebendo, coletor atualizado, spool/normalização/DuckLake/ClickHouse sem pendências no checkpoint.
- **75 capturas aceitas pelo coletor desde sua ativação; 78 capturas reais prontas no total**, incluindo três anteriores. Vinte fixtures permanecem separadas.
- Cinco novos ciclos por endpoint entre as amostras: **15 capturas**, zero falhas na origem, sem slots perdidos. A captura original continua em bytes preservados; não houve chamadas adicionais à GMGN para esta revisão.
- Contador acumulado do normalizador: **3 falhas**, inalterado entre as amostras. Nos logs consultados aparecem `TimeoutError` às 19:28:15Z e 19:31:14Z; o diagnóstico antigo identifica a etapa, mas não a operação interna. A recuperação ocorreu, com fila zerada; a causa interna não foi determinada aqui. Não afirmar “zero falhas desde o início”. Destinos/relay/manutenção com contador zero.
- Nos dez minutos consultados às 2026-09-09T19:43:35.583003+00:00: dez capturas por endpoint. Máximo recepção→aceite **0,975 s**; recepção→ambos os destinos **4,666 s**. Isso mede processamento após a resposta, não a idade do preço na origem, nem cobertura dos primeiros segundos do token.
- Releitura física de **três lotes e 4.950 fatos** da amostra econômica: hashes dos brutos, contagens, checksums e recibos concordantes em DuckLake/ClickHouse. DuckLake aberto somente para leitura.

## Espaço e crescimento observado

Janela **317.145 s**, de 2026-09-09T19:40:24.810587+00:00 a 2026-09-09T19:45:41.955670+00:00. São volumes distintos, medidos sequencialmente com `du -sk`: bytes alocados, incluindo WAL, partes inativas e arquivos temporários. A coleta continua durante a leitura; o snapshot não é uma transação entre os seis volumes. Não inclui imagens, logs do container ou outros arquivos do host.

| Volume | Alocado ao final (MiB) | Variação (MiB) | Variação/min (MiB) |
|---|---:|---:|---:|
| postgres | 616.15 | +86.64 | +16.392 |
| clickhouse | 65.84 | +7.76 | +1.468 |
| nats | 0.08 | +0.01 | +0.001 |
| raw | 5.61 | +1.04 | +0.197 |
| lake | 16.34 | +2.99 | +0.565 |
| collector | 5.23 | +0.00 | +0.000 |

Total dos seis volumes: **709.25 MiB**. Espaço livre efetivo: **362.97 GiB**; o controle existente usa o menor espaço entre host e filesystem do armazenamento. Não somar banco/tabela/índice novamente ao total dos volumes.

PostgreSQL é o maior consumidor. Só `field_observations` ocupa **194.44 MiB**, dos quais **101.77 MiB** são índices; `facts` ocupa **100.80 MiB** com índices. Essas medidas são tamanhos físicos; `estimated_rows` é estimativa estatística, não contagem de integridade. O catálogo DuckLake também está no PostgreSQL e já está incluído no volume dele; os Parquet estão em `lake`.

Na janela de dez minutos, Trenches gerou **96.46% dos bytes brutos** e **95.18% dos campos catalogados**. Na resposta revisada havia 179 itens, apesar de dois solicitados por categoria. Isso identifica a origem dominante do trabalho; não prova que os índices atuais sejam dispensáveis.

ClickHouse chegou a apresentar delta negativo na amostra intermediária: compactação/limpeza de partes inativas libera espaço enquanto a ingestão continua. WAL e spool também reutilizam espaço. Não converter esta janela curta em “dias até lotar”, nem em crescimento diário garantido. Para medir um dia real, comparar snapshots separados pelo período correspondente. Não foi iniciada automação nova.

## Como repetir

Na raiz do repositório, com o Docker ativo:

```powershell
.\scripts\gmgn-data.ps1 capacity
python scripts/gmgn-data-capacity.py --output .data/gmgn-data/capacity-start.json
# Em outro momento, mantendo a mesma base/volumes:
python scripts/gmgn-data-capacity.py --previous .data/gmgn-data/capacity-start.json --output .data/gmgn-data/capacity-end.json
```

O comando é somente leitura e usa as imagens existentes; Python no host e as dependências já presentes na API. Não aplica migrations nem imprime credenciais. Executa uma varredura de volume por vez, com timeout de 30 s por varredura e SQL limitado a dez segundos. Falha gera `complete=false`, erro por componente, total indisponível e código de saída diferente de zero; nunca aceita saída parcial de `du`. Não tratar a varredura como métrica para polling frequente. As amostras atuais levaram cerca de **2,2 s** cada.

O JSON separa: volumes alocados; bancos PostgreSQL; vinte maiores tabelas/índices; bytes brutos lógicos; cem maiores grupos de partes ClickHouse, ativas e inativas; status da coleta/pipeline. As listagens limitadas são detalhamento, não inventário completo nem parcelas adicionais ao total físico. `--previous` rejeita amostra incompleta, outro projeto e tempo não crescente. Reinicialização de volumes invalida a interpretação do delta como crescimento contínuo.

## Contratos econômicos revisados

A documentação primária mantém preço e capitalização USD explícitos no Trending; em Trenches a chave documentada é `usd_market_cap`. A amostra real trouxe **zero ocorrências dessa chave em 179 itens**, e `market_cap`/`price` em todos. A unidade de um nome não é transferida para outro. [GMGNAI — Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md).

No token info, preço está em `price.price`; circulating supply já usa tokens humanos, e liquidez é do maior pool. O máximo reportado não reconstrói o instante/contexto do pico. [GMGNAI — Token](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md).

**Decisão: nenhum contrato alterado.** Normalizador `gmgn-p0-v4` preservado, sem promoção por coincidência numérica. O manifesto [econômico](contracts/gmgn-economic-review-20260909.json) liga cada caminho à amostra, SHA256, versão, unidade, motivo, presença e qualidade efetivamente gravada. Não contém cálculo novo nem muda os fatos existentes.

Amostra: último lote completamente aplicado por endpoint no momento da consulta — Trending 10 itens, token info 1, Trenches 179. “Presente” significa não nulo; o fato ainda precisa satisfazer tipo/valor/qualidade. Ausente não vira zero. Contrato `valid` é semântica de campo do provedor, não confirmação on-chain.

| Endpoint | Caminho relativo ao item | Presente/itens | Nulos / ausentes | Unidade contratada | Contrato |
|---|---|---:|---:|---|---|
| market_rank | `decimals` | 0/10 | 0 / 10 | não definida | unverified |
| market_rank | `total_supply` | 10/10 | 0 / 0 | não definida | unverified |
| market_rank | `circulating_supply` | 0/10 | 0 / 10 | não definida | unverified |
| market_rank | `max_supply` | 0/10 | 0 / 10 | não definida | unverified |
| market_rank | `liquidity` | 10/10 | 0 / 0 | usd | unverified |
| market_rank | `initial_liquidity` | 10/10 | 0 / 0 | não definida | unverified |
| market_rank | `price` | 10/10 | 0 / 0 | usd_per_token | valid |
| market_rank | `market_cap` | 10/10 | 0 / 0 | usd | valid |
| market_rank | `history_highest_market_cap` | 10/10 | 0 / 0 | usd | valid |
| market_rank | `price_change_percent` | 10/10 | 0 / 0 | não definida | unverified |
| market_rank | `price_change_percent1m` | 10/10 | 0 / 0 | não definida | unverified |
| market_rank | `price_change_percent5m` | 10/10 | 0 / 0 | não definida | unverified |
| market_rank | `price_change_percent1h` | 10/10 | 0 / 0 | não definida | unverified |
| token_info | `decimals` | 1/1 | 0 / 0 | decimal_places | valid |
| token_info | `total_supply` | 1/1 | 0 / 0 | não definida | unverified |
| token_info | `circulating_supply` | 1/1 | 0 / 0 | token | valid |
| token_info | `max_supply` | 1/1 | 0 / 0 | não definida | unverified |
| token_info | `liquidity` | 1/1 | 0 / 0 | usd | valid |
| token_info | `initial_liquidity` | 0/1 | 0 / 1 | não definida | unverified |
| token_info | `price.price` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `ath_price` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `migration_market_cap` | 1/1 | 0 / 0 | usd | unverified |
| token_info | `price.price_1m` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `price.price_5m` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `price.price_1h` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `price.price_6h` | 1/1 | 0 / 0 | usd_per_token | valid |
| token_info | `price.price_24h` | 1/1 | 0 / 0 | usd_per_token | valid |
| trenches | `usd_market_cap` | 0/179 | 0 / 179 | usd | valid |
| trenches | `market_cap` | 179/179 | 0 / 0 | não definida | unverified |
| trenches | `price` | 179/179 | 0 / 0 | não definida | unverified |
| trenches | `liquidity` | 179/179 | 0 / 0 | usd | unverified |
| trenches | `total_supply` | 179/179 | 0 / 0 | não definida | unverified |
| trenches | `decimals` | 179/179 | 0 / 0 | não definida | unverified |

Permanecem não verificados: aliases econômicos de Trenches; escala humana/atômica de supplies não documentados; escopo de liquidez das listas; initial liquidity; percentuais/ratios conflitantes do Trending; supply/evento/quote do migration market cap. Não somar liquidez entre fontes, ajustar supply por decimals automaticamente ou reconstruir market cap antigo com supply de hoje.

## Validação e limite do fechamento

Comando Python e entrada PowerShell executados no Docker real; sintaxe PowerShell aprovada. Cinco verificações focadas: preservação de delta negativo, rejeição de snapshot parcial, projeto diferente, tempo inválido e falha de probe sem fabricar total. A última é simulada, sem parar serviços. A prova de dados dos três lotes usa conteúdo e destinos reais. Não repetimos toda a suíte, pois o pipeline não foi alterado.

Evidências: `.scratch/checkpoint-capacity-20260909/` (`first.json`, `second.json`, `final.json`, `command-smoke.json`, `checks.json`, `services.json`, `economic-result.json`, `physical-result.json` e probes). Código operacional novo: `scripts/gmgn-data-capacity.py`, entrada `capacity` no wrapper existente. Nenhum rebuild/restart, mudança de taxa, exclusão, migration ou execução financeira nesta entrega.

## Próximo recorte

Atualização: [CP-CATALOG-01 implantado](CATALOG_STORAGE.md), reduzindo o custo dos novos catálogos e corrigindo a fronteira de timeout de espera NATS. O parágrafo seguinte registra o encaminhamento original.

Antes de ampliar volume, reduzir a repetição/custo do catálogo de campos a partir destas medidas, conservando os brutos e a possibilidade de reconstrução e verificando os índices contra consultas reais. Resolver os contratos restantes com evidência da fonte; enquanto isso, usar apenas métricas compatíveis/verificadas nas consultas econômicas. O acompanhamento persistente por token, curva diária/marcos e estratégias continuam etapas posteriores. A base acompanha o perfil observado; capacidade sustentada diária e a causa dos timeouts históricos não foram certificadas neste checkpoint.
