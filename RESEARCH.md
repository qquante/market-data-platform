# Pesquisa reproduzível GMGN — implementação v1

> Atualização CP-DASHBOARD-ALL-05: [dashboard completo dos dados disponíveis, validação e limites](DASHBOARD_ALL_DATA.md).

> Estratégias anteriores importadas: [319 definições, parâmetros e compatibilidade](LEGACY_STRATEGIES.md).

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

> Estado vigente em 11/09/2026: implantado e homologado na base real, CP-RESEARCH-UI-LIVE-02. [Aceite live, comandos e limites](LIVE_ACCEPTANCE.md). As referências abaixo a implantação pendente/preview registram o checkpoint local anterior.

Checkpoint CP-RESEARCH-UI-01, 11/09/2026. Código e consumidores homologados em fixtures locais; implantação real pendente. O estado de entrega está em [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).

## Universo, clocks e labels — 6.2

ResearchSpec exige origem, cadeia Solana, período de primeira inscrição no token_registry, corte UTC, método de valuation, atraso de entrada, horizonte, tolerância de gaps e níveis. Universo: **todos os primeiros registros da origem dentro do período**, inclusive tokens sem preço/holders e resultados negativos. É uma amostra de descoberta observada pelo coletor, não todos os tokens da cadeia nem amostra aleatória. Não há filtro de vencedores.

A entrada ocorre em registered_at + entry_delay_seconds. Features de entrada/saída usam somente interpretações disponíveis no instante da decisão. Os labels usam a evidência retrospectiva disponível no knowledge_cutoff declarado; esse conhecimento posterior não entra nas decisões de entrada. Hash do código, versões Python/asyncpg/Pydantic, especificação, membership, clocks, capturas e hashes acompanham o bundle.

Para cada nível, reached/not_reached/censored/unknown conciliam exatamente N. Reached exige amostra observada no nível ou acima; not_reached exige horizonte maduro e cobertura da política de amostragem. Isso não prova ausência de um crossing entre amostras. Lacunas, pontos inválidos e horizonte incompleto conservam censura; sem pontos elegíveis permanece unknown. O relatório publica taxa entre resolvidos e bounds populacionais descritivos, com denominadores explícitos.

Holders: soma das frações de dez carteiras distintas em snapshot pronto, sem filtro de tag, amount_percentage/desc, disponível antes da entrada e recebido há no máximo uma hora. Sem dez linhas válidas, qualidade/seleção incompatível ou snapshot desconhecido: unknown. O denominador de supply do provedor não é certificado externamente. Entrada imediata após cadastro frequentemente não tem holders conhecidos; aumentar o atraso é uma escolha explícita do protocolo, nunca preenchimento retroativo.

## Fila e orçamento

Migration 017 cria research_jobs/research_members. POST registra um job pequeno; o worker processa em etapa separada. Membership é congelado antes dos cálculos; cada token concluído salva checkpoint e hash. Timeout do job: 120 s; lease: 150 s. Reinício retoma membros concluídos sem recalcular. Contador de tentativa e verificação da concessão impedem owner antigo de sobrescrever o novo resultado.

Limites v1: período de inscrição até 7 dias; até 100 membros (padrão 50); até 2.000 pontos/série (padrão 500); 20.000 pontos de entrada por job; 512 KiB/membro; bundle até 8 MiB; 64 jobs retidos. Ao exceder, o job inteiro falha de forma explícita: não retorna a primeira página como se fosse a população completa. Falhas e evidência parcial permanecem registradas. Não há exclusão automática por TTL.

## Simulação e comparação — 6.3/6.4

Migration 018 registra todas as tentativas de experimentos. Até 20 estratégias, 10 estudos e 128 tentativas retidas; configuração HTTP até 16 KiB; resultado até 4 MiB. A mesma definição, dataset e implementação retornam a tentativa existente. Mudança de versão cria outra identidade. Resultados/exportações são imutáveis e conferidos por SHA-256.

Cada estratégia declara filtros de market cap e/ou holders. Cada experimento exige cortes train/validation/test cronológicos, embargo, taxa, slippage, custo de rede por lado, notional, caixa inicial e máximo de posições. Não há otimização automática. O embargo mínimo cobre o lookback realmente usado; o horizonte inteiro é purgado nas fronteiras. IDs purgados e fora do período permanecem visíveis.

A simulação usa **preço USD por token**, nunca market cap como preço. Calcula quantidade, taxas, slippage, custo de rede, caixa, posições concorrentes, resultado fechado e perdas com Decimal de precisão 80. Reserva custo de rede de saída na admissão. Saídas são processadas antes de novas entradas no mesmo timestamp. Sem cotação de saída, a posição permanece aberta, conserva capital/reserva e o retorno total/NAV ficam desconhecidos. Drawdown publicado é de resultado de lotes fechados; não é drawdown marcado a mercado.

É simulação de cotações com custos de cenário, não fill real, prova de liquidez, capacidade executável ou rentabilidade futura. Comparar/reranquear pelo test consome esse holdout; resultados são exploratórios. Não foram enviados pedidos financeiros.

## Estudos descritivos adicionais

São opções do campo studies do mesmo experimento, com definições e tentativas preservadas. Não criam uma segunda infraestrutura.

| Contrato | Implementação v1 | Limitação publicada no resultado |
|---|---|---|
| Q05 | Funil por níveis/horizonte, negativos, censura, bounds e membership | Probabilidade de inclusão e independência entre tokens desconhecidas |
| Q06 | Primeiro episódio por token, destinos concorrentes, pares adjacentes e bounds de exposição | Gaps não viram permanência; não assume Markov ou tempo contínuo |
| Q07 | Exposição de holders; pareamento 1:1 sem reutilização em estratos explícitos de cap/tempo | Exibe unknown/unmatched/balance; sem overlap retorna not_identifiable; sem conclusão causal |
| Q08 | Primeiro crossing observado para cima, intervalo do evento, âncora na disponibilidade, lags e controles contemporâneos | Seleção retrospectiva por evento; missing não vira zero; N por lag, min/max e diferenças pareadas |
| Q09 | Grade fixa do prefixo, escala relativa ao primeiro valor, distância absoluta média e vizinhos somente do train | Distância não usa labels; próprio token excluído; grupos de campanha desconhecidos; fora de suporte explícito |
| Q10 | Comparação das mesmas populações/splits, decisões, custos, caixa e ledger | Carteiras independentes por estratégia/split; sem seleção automática nem inferência de fills |

Os estudos trabalham nas interpretações congeladas no bundle. Uma revisão antiga ausente no arquivo continua desconhecida. Não inventam histórico de revisões nem preenchem lacunas. Intervalos de confiança inferenciais, ajuste causal, clustering por criador/campanha, estimadores de sobrevivência contínua e índices ANN não foram implementados: os dados/hipóteses necessários ainda não foram homologados. Bounds descritivos e falta de identificação ficam explícitos.

A pesquisa v1 usa token_info/rank/trenches existentes + holders. Traders, carteiras completas, OHLCV externo e Q14 social continuam expansões dependentes de contratos/corpus específicos; não são campos homologados por este checkpoint. Não foi ativada busca social nem inventada associação entre token e publicação.

## API e reprodução

Rotas protegidas por Bearer:

- POST/GET /v1/research/jobs; GET /v1/research/jobs/{id}; GET /v1/research/jobs/{id}/bundle.
- POST /v1/research/jobs/{id}/experiments.
- GET /v1/research/experiments; GET /v1/research/experiments/{id}; GET /v1/research/experiments/{id}/result.

POST retorna 202; orçamento de retenção/dataset indisponível retorna 409; definição inválida 422; corpo acima de 16 KiB 413. GET não cria coleta nem experimentos. A interface permite copiar uma definição completa, criar uma tentativa, inspecionar inclusive falhas e baixar artefato com hash verificado.

Exemplo de reprodução já executado, com **100 tokens sintéticos, 20 estratégias e quatro estudos**:

```powershell
docker run --rm --pull never --network none --read-only --tmpfs /tmp --entrypoint python -v 'C:\Users\PC\Desktop\Midas\.scratch\checkpoint-final-20260911:/evidence' midas-data-app:research-ui-v1-20260911 -m midas_data.experiments --bundle /evidence/bundle.json --spec /evidence/spec.json --output /evidence/offline-replay.json --verify /evidence/expected-result.json
```

O comando não consulta a GMGN. Configuração/artefatos de exemplo são cenários de teste, não parâmetros financeiros recomendados. A reprodução recalcula os dados derivados e rejeita mudanças em membership, clocks, labels, resumo e checksum. No ensaio, resultado idêntico em processo novo: result_sha256 750dbc5366330f3b1a5365d3e480b77b656f2a3aeaad33ffa8760e0d72dba8c6.

Fontes metodológicas consultadas: [divisão temporal e gap — scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [Bailey et al., Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), [Hernán e Robins, Causal Inference](https://www.hsph.harvard.edu/miguel-hernan/wp-content/uploads/sites/1268/2024/04/hernanrobins_WhatIf_26apr24.pdf). A implementação é própria e limitada; não reproduz automaticamente os estimadores desses trabalhos.

Schemas de entrada gerados diretamente pelos modelos da imagem final: [ResearchSpec](contracts/research-spec-v1.schema.json) e [ExperimentSpec/StudySpec](contracts/experiment-spec-v1.schema.json).
