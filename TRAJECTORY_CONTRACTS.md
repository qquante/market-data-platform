# Trajetória GMGN — primeiro recorte, 09/09/2026

> Estado atual: [4.1 concluída — contrato econômico mínimo](ECONOMIC_INPUTS.md). A agenda 3.4 continua live; os snapshots e pendências descritos abaixo pertencem aos checkpoints anteriores.

> Atualização posterior: v4 implantada e verificada em base limpa, cinco serviços saudáveis e três smokes aprovados. [Checkpoint atual](CHECKPOINT_V4_20260909.md). A descrição anterior abaixo preserva o histórico pré-implantação; coleta real continua pendente.

Owner: A0, sozinho. Estado: código e testes concluídos, **sem implantação no runtime operacional**. A exclusão foi bloqueada pela revisão automática mesmo após confirmação; o usuário assumiu a limpeza manual e autorizou prosseguir no código. Lista em [MANUAL_RESET](MANUAL_RESET.md). Todos os containers Midas ficaram parados ao encerrar; outros projetos não foram alterados.

## Entrega

Normalizador `gmgn-p0-v4`; contratos `gmgn.token_info.v3` (sem mudança semântica), `gmgn.market_rank.v4` e `gmgn.trenches.v1`. As identidades dos fatos incluem a versão do normalizador. Nenhuma migration, alteração de banco existente ou reprocessamento no runtime foi aplicado.

- Trenches tem 32 caminhos explícitos neste primeiro recorte: 27 contratos declarados e cinco interpretações pendentes. Não é homologação de todos os campos do endpoint. O restante continua no bruto e catálogo com motivo explícito.
- Categorias reconhecidas: `new_creation`, `completed` e as duas representações alternativas da etapa intermediária: `pump` na documentação e `near_completion` nas capturas. Se ambas vierem juntas, a forma é ambígua e não promove fatos. O JSON Pointer conserva a representação exata recebida, o índice e a categoria; nenhuma categoria é fabricada dentro do corpo original. Categoria ausente difere de array vazio.
- Preço e market cap observados em Trenches conservam o valor decimal, mas permanecem sem moeda confirmada. A documentação explicita `usd_market_cap`; não há prova do alias para o `market_cap` observado. `usd_market_cap` mantém caminho e métrica próprios. Supply não recebe escala presumida; liquidez mantém USD documentado e escopo de pool/agregado pendente.
- Criação, abertura, conclusão e duração são atributos diferentes do instante da observação. Zero em um timestamp não vira 1970; duração negativa não passa como duração válida. Não há cálculo de ATH de market cap usando supply atual.
- Links e seguidores são metadados reportados: não comprovam post capturado, autoria ou afiliação.
- Ranking com intervalo explícito `1m`, `5m`, `1h`, `6h`, `24h` publica `volume_<intervalo>`, `buys_<intervalo>`, `sells_<intervalo>` e `swaps_<intervalo>`. O caminho bruto continua sem renomeação. São aceitos `request_params.interval` e `request_params.query.interval`, exigindo concordância quando ambos existem. Ausência, tipo inválido, intervalo desconhecido ou conflito não recebem valor padrão: o dado fica `unverified`. A janela está codificada na métrica nesta entrega; o horário interno exato de fechamento do provedor não foi medido.

## Armazenamento e leitura

O esquema de armazenamento existente é reutilizado: bruto imutável por checksum; captura com request/receive/source clocks; fatos com pointer, unidade, qualidade, contrato e `available_at`; recibos independentes em DuckLake e ClickHouse. A entrega completa continua exigindo ambos. Repetir a captura não duplica efeito.

`/v1/observations` e `/v1/as-known` funcionam com as métricas novas. Origem e corte de conhecimento continuam obrigatórios. Interpretações de endpoints diferentes não devem ser combinadas sem consultar contrato e unidade. A API exclui valores inválidos/ausentes e, por padrão, também os não verificados. SQL direto tem de conservar o mesmo filtro e a escolha de versão temporal.

Não foram criadas tabelas genéricas novas, frameworks, coleta por estratégia ou consultas de marcos. A categorização da etapa de Trenches está preservada no pointer; uma dimensão consultável independente faz parte do próximo modelo de trajetória. Janelas explícitas em coluna, entidades de pool/wallet/post, agregados e retenção seguem pendentes.

## Orçamento

O replay revelou corpos válidos com até 23.462 nós, acima do limite antigo de 20 mil. Somente Trenches passa a admitir 40 mil nós. Continuam 10 mil fatos e 16 MiB de saída por captura, além dos limites de transporte. Overflow não publica uma parte da captura. Um teste sintético de 3 categorias × 80 tokens, com mais de 30 mil campos, passou. Isso não certifica capacidade sustentada de coleta ou de armazenamento.

## Validação

- Docker Python 3.13 com dependências da imagem existente, código atual montado somente para leitura. Sem rebuild/rollout na base antiga.
- Suíte integrada em projeto isolado `midas-data-contracts-test`: **503 testes + 48 subtestes aprovados; 28 opcionais ignorados; zero falhas/erros; 40,94 s**. XML agregado informa 579 incluindo subtestes. Evidência: `.scratch/trajectory-v4/integrated.xml`.
- O teste novo passa por API, PostgreSQL, NATS, worker, DuckLake e ClickHouse: verifica bytes originais, precisão, duplicação, dois recibos, tempo e consulta de janelas diferentes. Dados sintéticos identificados como `fixture`; nenhum acesso à GMGN.
- Replay offline de 41 capturas existentes (13 Trenches, seis ranking, 22 info): 55.909 fatos e 241.818 observações de campos, sem falha fatal ou shape não reconhecido após os ajustes. Duas capturas Trenches são respostas legitimamente vazias. Os valores originais não foram ingeridos novamente em nenhum banco.
- Qualidade desse replay: 35.079 `valid`, 12.016 `unverified`, 3.481 `invalid`, 5.333 `missing`. Os inválidos incluem abertura/conclusão zero, duração negativa e preço zero; permanecem registrados e excluídos da curva validada. Não confundir ausência de falha de lote com ausência de problemas nos dados. Detalhes sanitizados: `.scratch/trajectory-v4/corpus.json`.

## Próximo recorte

Após o reset manual: construir uma imagem identificada para esta versão e subir somente a base nova. Homologar os caminhos exatos de preço/market cap Trenches; definir uma dimensão temporal/de categoria consultável; medir e reduzir repetição do catálogo antes de ampliar coleta. Depois conectar a coleta contínua com frequência e cobertura mensuráveis. Marcos configuráveis, cohorts, estratégias e visualização permanecem posteriores.

Referência oficial consultada em 09/09/2026: [GMGNAI — market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md). A URL aponta para main, sem declarar commit imutável. Registro por campo em `contracts/gmgn-trenches-v1.json`.

09/09/2026 — CP-SOURCES-01: Trending/market_rank revisado explicitamente e validado no fluxo completo para preço/market cap; aliases ambíguos de Trenches continuam não verificados. [Fontes, limites e checkpoint](MARKET_SOURCES.md).
