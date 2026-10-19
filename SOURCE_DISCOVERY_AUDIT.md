# Descoberta e acompanhamento GMGN — auditoria real de 11/09/2026

> Atualização posterior: CP-DATA-CONTINUOUS-11 (not included in this repository) implantou a nova agenda e retomou a coleta. Os números e limites abaixo descrevem a auditoria anterior.

Checkpoint CP-DATA-SOURCES-10. Auditoria solicitada pelo usuário após encontrar apenas 10 tokens ativos. Esta entrega conclui a investigação de fontes e do gargalo; **não removeu o teto nem implantou uma nova agenda**. D1/D2 do aceite amplo de dados continuam abertos até implementação e validação.

## Veredito

O projeto deve formar um histórico longitudinal de tokens desde a descoberta, conservar positivos, negativos e perda de cobertura e permitir pesquisa posterior. **Top 10 do Trending é uma população inadequada para isso.** Os 10 são um limite do nosso coletor, não um limite de tokens da GMGN. Na base havia 57.832 identidades SOL registradas, mas somente 10 admitidas ao acompanhamento individual.

Para esse fluxo, **Trenches é a fonte principal de descoberta de lançamentos; Trending é complementar para atividade, tokens antigos e retomadas. A agenda persistente própria deve comandar o acompanhamento depois da descoberta.** Os endpoints de detalhe acrescentam famílias e histórico. Nenhuma lista limitada da GMGN prova um censo de todos os tokens ou de todos os eventos da cadeia.

## O que foi realmente executado

- 13 pares Trenches + Trending SOL, em intervalos de 25 segundos: **17:57:09.185739 a 18:02:13.842720 UTC**, 304,657 segundos de observação.
- 26 consultas repetidas, 6 variações de parâmetros e 11 consultas instrumentadas de famílias: **43 consultas de mercado concluídas pela CLI**, sem erro reportado pelo cliente. As 11 instrumentadas também têm HTTP 200 e corpo original preservado antes do parse da CLI.
- Uma consulta adicional `portfolio info` procurou quota/plano: sucesso, mas a resposta não expôs esses campos. Identidade da conta e carteiras não foram retidas.
- Uma falha local de inicialização do observador Node no Windows ocorreu antes de qualquer HTTP. Corrigido o caminho do módulo; não é erro GMGN e não entra na contagem de consultas concluídas.
- `gmgn-cli` instalada: **1.6.0**. CLI e coletor usam a mesma chave, comparada somente em memória. Nenhuma chave foi exibida ou incorporada às evidências.
- 11 corpos HTTP tiveram SHA-256 verificado; mais 3 capturas originais da base foram baixadas e verificadas contra o hash registrado e o header de download.
- Sondas finitas, somente leitura. Não são um teste de pico ou de capacidade sustentada. Não habilitaram estratégias, operações financeiras, novas cadeias ou automações.

## Descoberta: o que apareceu em cinco minutos

| Seleção | Itens por resposta | Tokens únicos na janela | Novos após a primeira resposta |
|---|---:|---:|---:|
| Trenches `new_creation` | 59–60 | 173 | 113 |
| Trenches `near_completion` | 60 | 89 | 29 |
| Trenches `completed` | 60 | 78 | 18 |
| Trenches, união das categorias | 174–178 identidades por snapshot; até 180 ocorrências | **307** | não somar categorias sobrepostas |
| Trending 5m por swaps, top 100 | 100 | **152** | 52 |
| Primeiros 10 desse mesmo Trending | 10 | **16** | 6 |

A união Trenches + Trending teve **440 tokens**: 307 + 152 − 19 em comum. Foram **288 exclusivos do Trenches** e **133 exclusivos do Trending** nessa janela. Dos 440, **424 nunca apareceram no top 10 observado**. Isso não significa que não estejam no cadastro da base ou que nunca tenham sido consultados antes; demonstra o viés do mecanismo de admissão baseado em top 10.

`new_creation` renovou mediana de 10 entradas por intervalo de 25 s, máximo 14. Nenhum token ficou em todas as 13 listas de novos. O Trending top 100 renovou mediana de 6 entradas por intervalo, máximo 8; entre tokens presentes em respostas consecutivas, 69–81 trocaram de posição.

As categorias não são estados mutuamente exclusivos: houve 2–6 endereços simultaneamente em `new_creation` e `near_completion`. Nove endereços vistos em `new_creation` também apareceram posteriormente em `completed`. O arquivo `observed-trajectories.json` conserva exemplos com timestamps reportados, preço, MC, supply, liquidez e progress por resposta. **Entrada numa lista não é timestamp exato de migração, nem preço exato de cruzamento.**

## Parâmetros realmente testados

| Consulta | Resultado | Implicação |
|---|---|---|
| Trenches `limit=2` | 60 em cada categoria | O pedido pequeno não reduz essa resposta upstream |
| Trenches `limit=60` | 60 em cada categoria | Mesma quantidade observada |
| Trenches `limit=80` | usualmente 60 por categoria; um snapshot de novos teve 59 | O máximo 80 do help não é promessa de retorno |
| Somente `type=new_creation` | 60 novos; outras listas vazias | Seleção por categoria funciona e reduz o payload |
| Novos com idade `5m..15m` | 60; idades reportadas 302,6–484,6 s | Filtro de idade funcionou nessa sonda; 59 identidades adicionais à janela repetida |
| Trending `limit=10` vs `100` | 10 vs 100 | O limite do ranking é efetivo |
| Trending 1h por volume, top 100 | 100; 28 adicionais à janela repetida | Alterar janela/ordenação altera a população |
| Hot Searches SOL 24h `limit=500` | 119; 66 adicionais à janela repetida | Complementa descoberta/atenção; não assegura 500 resultados |
| Signals SOL, padrão | HTTP 200, lista vazia | Endpoint acessível; conteúdo e cobertura não homologados por uma resposta vazia |

A faixa 5–15 minutos devolveu apenas os 60 mais recentes elegíveis, com idade máxima de aproximadamente 8 minutos. Logo, uma resposta saturada **não fecha** a faixa inteira. Faixas menores com sobreposição e deduplicação podem ampliar cobertura, mas precisam de teste de fronteiras/retomada. Filtros por idade e launchpad não são um cursor histórico imutável.

Nos comandos de descoberta inspecionados, não há cursor para percorrer todos os tokens, nem rota de `token_info` para uma lista arbitrária de endereços. O batch útil já existente é a lista de mercado retornando muitos tokens; não devemos inventar uma consulta de preços em lote. Wallet stats/profits em lote é outra entidade e não resolve esse problema.

## Fonte e transformação: onde cada dado entra

| Família / papel | Fonte | Campos e diferenças relevantes |
|---|---|---|
| Descoberta e ciclo de lançamento | Trenches | `address`, `chain`, `created_timestamp`, `open_timestamp`, `complete_timestamp`, launchpad, pool, `progress`; registrar a participação em cada categoria |
| Mercado dos lançamentos | Trenches | `price`, `market_cap`, `total_supply`, `liquidity`, holders, compras/vendas/swaps e volume **24h**, riscos e contexto embutidos |
| Atividade e redescoberta | Trending | `creation_timestamp`, `price`, `market_cap`, `total_supply`, liquidez, posição, histórico de máxima reportada, `volume/buys/sells/swaps` da **janela solicitada**, variações e tags |
| Detalhe por endereço | Token info | Preço em `price.price`, supply total/circulante/máximo separados; janelas 1m/5m/1h/6h/24h, `pool`, `dev`, `link`, `stat`, `wallet_tags_stat`, migração e fee distribution |
| Segurança | Token security | Autoridades, flags, locks e riscos. No SOL consultado, honeypot era `null`, enquanto alguns campos paralelos eram 0; isso não é certificado de segurança |
| Pool | Token pool | Reservas, quote, vaults, pool, exchange e liquidez. Os **22 campos** recebidos também existiam em `token_info.pool` da amostra; valores vêm de instantes distintos |
| Holders | Top holders | 100 carteiras/contas na sonda: saldo, participação, custos/resultados reportados, transferências e tags. É uma lista parcial |
| Traders | Top traders | 100 na sonda; 97 endereços em comum com holders usando a ordenação padrão. Para estudar vendedores/saídas, explicitar ordenação; não confundir essa lista com log de todos os trades |
| Histórico de preços/fluxo | Kline | 8 barras 1m no token recente para a hora pedida; OHLC, volume, amount e time. Não criar barras ausentes nem inferir ordem intrabar |
| Atenção | Hot Searches / Signals | Visitas/ranking de buscas e sinais reportados. Não equivalem a posts completos ou a fluxo exaustivo de negociações |

Outras famílias do escopo amplo — atividade de carteiras, criadores/tokens criados, feeds KOL/smartmoney, cadeias adicionais — continuam na matriz D1/D3. Não foram todas reconsultadas nesta auditoria, que se concentrou no gargalo de descoberta/acompanhamento SOL.

### Transformações que precisam continuar explícitas

1. A fonte de evidência é o corpo HTTP original da OpenAPI. `gmgn-cli --raw` faz `JSON.parse`, remove o envelope externo, sanitiza metadados textuais e serializa novamente. Na amostra instrumentada não houve diferenças de valores após retirar o envelope; os bytes continuam distintos. A CLI não deve substituir o bruto original do coletor.
2. Trending retornou outro envelope interno `code/data/rank`, enquanto Trenches retornou diretamente suas categorias depois da primeira remoção. O parser precisa reconhecer os formatos, sem achatar silenciosamente proveniência.
3. A documentação oficial diz que `near_completion` viria sempre como `pump`; **o observado foi `near_completion`**. Preservar e testar ambos conforme o contrato; não corrigir o dado para imitar a documentação.
4. A documentação declara filtros padrão do Trending SOL `renounced/frozen`. O cliente inspecionado não os injeta na query; o default fica no serviço quando o parâmetro é omitido. O comportamento sem esses filtros não foi homologado. “Sem filtro informado” não prova população irrestrita.
5. Trenches envia explicitamente `filters=[offchain,onchain]`, quote types `[4,5,3,1,13,0]` e `launchpad_platform_v2=true`; launchpads omitidos deixam o default ao servidor. Não classificar esse universo como toda a Solana.
6. `created_timestamp` do Trenches e `creation_timestamp` de outros endpoints pertencem a contratos distintos. Kline usa milissegundos no HTTP; a CLI recebe segundos e multiplica por 1000. Nunca juntar os campos só pelo nome.
7. MC reportado, MC calculado, supply total/circulante e valuation em quote devem permanecer separados. Dados de horários diferentes não compõem um snapshot econômico atômico. Campo ausente não vira zero e campo opaco permanece bloqueado para cálculo.
8. O repositório já registra observações antigas do WebSocket do site; isso não é um contrato OpenAPI atual validado para assinar milhares de tokens. Streaming pode ser avaliado como ampliação futura, sem presumir disponibilidade, autenticação ou cobertura.

## Gargalos do nosso código, confirmados

| Local | Restrição atual |
|---|---|
| `src/midas_gmgn/data_collector.py`, Config e main | 10 ativos / 20 esperando; validação ainda proíbe mais de 20 ativos |
| `source_once` | Ranking solicitado com limite 10 e candidatos novamente cortados por `[:10]` |
| `source_once` e `sync_tracking_once` | Só Trending alimenta candidatos; Trenches é guardado/registrado, mas não entra na agenda individual |
| `source_once` | Um slot de `token_info` por intervalo global de 60 s, independentemente do tamanho do universo |
| `token_schedule.py` | Admissão por vagas, concessão de 30 min, adiamentos e substituição de entradas expiradas; continuidade de pesquisa não é garantida |
| `family_schedule.py` | Holders/Kline dependem do token já admitido; máximo 100 jobs; mesma restrição de slots por endpoint |
| `request_gate.py` / compose ativo | Gate compartilhado já existe, mas `GMGN_RATE_REDIS_URL` não está configurada no coletor da base; ele usa orçamento local de 1 peso/s |
| `RawStore` | Gzip com hash dos bytes originais já implementado; preservar o mecanismo e suas verificações |

Com um `token_info` por minuto, uma primeira passagem por 1.000 tokens levaria no mínimo **16h40**, mesmo sem falhas. Aumentar apenas `MAX_ACTIVE` criaria filas atrasadas e não frescor.

### Falha operacional encontrada durante a auditoria

O coletor da base falhou em Trenches com **HTTP 401 às 17:56:50 UTC**, antes da primeira sonda (17:57:09), e o código fixou pausa global até **18:56:50 UTC / 15:56:50 de Brasília**. Durante a janela, as capturas reais ficaram em **8.827**, sem crescimento. `process_alive=true` e Docker saudável coexistiam com `source_current=false`.

Rechecagem final às **18:12:46 UTC / 15:12:46 de Brasília**: 57.832 registrados, 10 ativos, **0 dentro da janela de frescor**, fonte ainda pausada e 8.827 capturas reais.

A mesma chave funcionou pela CLI. Isso comprova a pausa e a disponibilidade observada na CLI, mas **não determina a causa do 401 no transporte Docker**. O erro sanitizado não conserva corpo/headers suficientes para atribuí-lo a quota, IPv6, relógio ou rejeição de autenticação. Não foi alterada credencial ou removido cooldown manualmente. A falha precisa ser tratada com diagnóstico no transporte do coletor, evidência sanitizada de erro e retomada controlada; não confundir saúde do processo com saúde da fonte.

## Desenho a implementar, sem infraestrutura excessiva

1. **Cadastro permanente e agenda persistente para todo token descoberto no escopo.** Reutilizar o registro existente; deduplicar por fonte/cadeia/endereço; guardar primeira/última observação, origem, filtros, categorias e decisões. Retirar teto de entidades ativas/esperando. Não carregar o universo inteiro em memória; buscar somente o próximo lote de jobs devidos por índice.
2. **Trenches como entrada principal e Trending como complemento.** Admitir as três categorias e top 100, aproveitar os snapshots recebidos e conservar acompanhamento quando o token sair da lista. Usar faixas temporais sobrepostas para recuperação best-effort, registrando saturação e trechos não cobertos. Não filtrar a coleta inicial apenas por tokens “bons” ou por atingir 20k.
3. **Separar cadência da fonte e cadência por família/token.** Candidato inicial: Trenches a cada 15 s e Trending 5m por swaps a cada 30 s, com consultas menos frequentes a 1h/24h/atenção. São parâmetros propostos, não SLA medido. A idade da lista, sobreposição e atrasos indicam se precisam mudar.
4. **Atualizar os dados já presentes no lote antes de consultar detalhes.** `token_info` completa ou refresca família vencida. O bloco `pool` homologado pode satisfazer sua própria necessidade de cobertura; `pool_info` dedicado é acionado por falta, mudança ou necessidade de frequência. Não criar solicitações duplicadas para a mesma entidade/família/janela.
5. **Quota global por peso e scheduler por vencimento.** Reutilizar o gate existente para todos os consumidores da mesma chave. Priorizar primeira cobertura, mudanças rápidas, proximidade de marcos e atrasos; manter passagem mínima também por tokens sem atividade, fora das listas e com resultados negativos. Guardar frequência desejada, concedida e motivo do atraso. Nenhuma família vira “atualizada” apenas porque outra respondeu.
6. **Famílias caras com sua frequência própria.** Holders/traders, segurança, OHLCV e atividade de criadores têm jobs distintos. Conservar checkpoints da última barra/atividade, sobreposição e dedupe. Metadados estáveis precisam de refresh periódico e em mudanças; não são imutáveis para sempre. Novo campo recebido entra no bruto/inventário antes de qualquer interpretação econômica.
7. **Operação visível.** Dashboard deve mostrar cadastrados, com jobs, devidos, consultados recentemente, atrasados e sem cobertura por família, mais orçamento/consumo e estado da fonte. Teste finito com milhares de jobs persistidos, retomada após interrupção e competição pela quota antes de declarar capacidade live.

A prioridade é corrigir a retomada da fonte e implementar D2 em pequenas partes verificáveis. Não precisa trocar Postgres/NATS/ClickHouse ou adicionar outro sistema de filas para eliminar esse teto.

## Quanto custa manter milhares

Pesos documentados: info/security/pool = 1; kline = 2; holders/traders = 5. Uma rodada dessas seis famílias custa **15 pesos por token**, antes de descoberta, retries e outras carteiras. O endpoint de pool pode ser evitado quando o bloco de info satisfizer o contrato, reduzindo redundância, não garantindo cobertura ilimitada.

A documentação oficial anuncia 20 pesos/s, mas os headers das 11 sondas não trouxeram limite restante e a consulta da conta não expôs quota. A menção antiga a plano Plus não foi revalidada. **Não tratar 20 pesos/s como capacidade livre exclusiva desta base.**

Exemplo aritmético, não benchmark: com **8 pesos/s efetivamente destinados a essas consultas**, ignorando descoberta/erros/latência:

| Tokens | Passagem de info apenas | Passagem das seis famílias, 15 pesos/token |
|---:|---:|---:|
| 1.000 | 2m05 | 31m15 |
| 10.000 | 20m50 | 5h12m30 |
| 57.832 | 2h00m29 | 30h07m15 |

Outra forma de verificar: 10.000 tokens com info a cada 10 minutos exigem **16,67 pesos/s só de info**. Portanto, “sem teto arbitrário de tokens” é correto; “todos os dados de todos os tokens instantaneamente” não cabe nessa API com a quota documentada. Frequência efetiva e lacunas devem ser explícitas, sem descarte silencioso da população.

Latência medida de ponta a ponta pela CLI (inclui inicialização): Trenches mediana 0,787 s / máximo 1,540 s; Trending mediana 0,564 s / máximo 0,845 s. Isso não mede atraso entre evento on-chain e atualização da GMGN, nem garante throughput concorrente.

Projeção offline dos payloads amostrados com o gzip nível 6 já usado pela base: Trenches a cada 15 s ≈ **1,06 GB/dia** e Trending a cada 30 s ≈ **0,11 GB/dia** em brutos comprimidos. Exclui demais famílias, Postgres, lake, ClickHouse, índices e backups. É estimativa para orçamento de disco, não medição de crescimento sustentado.

## Aceite e evidência

Concluído: investigação de cinco minutos, comparação de fontes/parâmetros, identificação das restrições no código/runtime, verificação de brutos, inventário observado de dez endpoints de mercado e proposta concreta de correção.

Pendente: resolver o 401/retomada no transporte do coletor; retirar limites de população e os slots fixos; ligar Trenches à agenda; homologar famílias restantes e contratos; testar justiça/frescor/retomada com milhares; backup/restore e crescimento da base atual. Estratégias continuam adiadas.

Evidências reproduzíveis: `outputs/gmgn-source-audit-20260911/analysis.json`, `additional-analysis.json`, `schema-inventory.json`, `observed-trajectories.json`, manifests de hashes, comandos/scripts, 13 snapshots do estado da base, corpos CLI e 11 corpos HTTP originais. O inventário de caminhos desta sonda não promove campos a contratos econômicos.

Fontes oficiais consultadas em 11/09/2026: [GMGN Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md), [GMGN Token](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md), [cliente oficial](https://github.com/GMGNAI/gmgn-skills/blob/main/src/client/OpenApiClient.ts), [Agent API](https://docs.gmgn.ai/index/gmgn-agent-api). Para defaults/transporte da instalação, as cópias da CLI 1.6.0 em `cli-source/` têm precedência sobre mudanças posteriores do branch main.
