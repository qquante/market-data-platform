# 4.4 — grade, passagens e reentradas

**Concluída e implantada em 10/09/2026.** Checkpoint **CP-CROSSINGS-01**, A0, sem subagentes. Entrega consultável e limitada, reutilizando os fatos da 4.2. Próximo: **4.5**, aceite integrado das consultas temporais.

## Grade homologada

Política `gmgn-market-cap-grid-v1`, USD, conforme [especificação da grade](../docs/gmgn_api/refactor/CURVA_DIARIA_E_GRADE_MARKET_CAP.md):

| De | Até | Passo |
|---:|---:|---:|
| 20k | 100k | 10k |
| 100k | 200k | 25k |
| 200k | 500k | 50k |
| 500k | 1M | 100k |
| 1M | 3M | 250k |
| 3M | 5M | 500k |
| 5M | 10M | 1M |
| 10M | 100M | 10M |

As fronteiras são únicas: **41 níveis até 10M, 50 até 100M**. A grade não exclui observações abaixo de 20k nem acima de 100M; apenas deixa de gerar níveis fora desse intervalo. Extensões acima de 100M continuam propostas, sem ativação automática.

## Consulta

`GET /v1/trajectory/crossings`, autenticada, exige source_kind, chain, token, method, start, end e knowledge_cutoff. Preserva os contratos de [TRAJECTORY.md](TRAJECTORY.md): intervalo [start,end), até 24 h, relógios UTC explícitos, fonte/método isolados e última interpretação inteira conhecida no corte.

Parâmetros adicionais:

| Parâmetro | Padrão / limite |
|---|---|
| grid_version | gmgn-market-cap-grid-v1; outra versão retorna 422 |
| max_points | 500 / máximo 2.000 |
| max_events | 1.000 / máximo 2.000; excesso retorna 413 sem eventos parciais |
| gap_after_seconds | Herda 720 s para token_info, 120 s para Trending/Trenches; ajustável |

A resposta conserva a série original e acrescenta `crossings`, versão `gmgn-crossings-v1`: grade, eventos, trechos de comparação, snapshots de contexto e contagens. Timeout de consulta de 5 s, dois leitores compartilhados e resposta de até 4 MiB permanecem ativos.

## Semântica de passagem

Estado acima do nível L significa **MC ≥ L**. Em duas observações consecutivas comparáveis:

- subida: anterior < L e atual ≥ L;
- descida: anterior ≥ L e atual < L;
- valor repetido no limiar não gera evento;
- sem predecessor elegível, apenas registra os níveis em que o trecho começou.

Tipos: `upcross`, `downcross`, `reentry`. Uma subida é reentry quando existe observação anterior ≥ L **no mesmo trecho contínuo desta consulta**. Isso inclui um trecho que começou acima, depois caiu e voltou. Upcross não afirma primeira passagem da vida do token.

Pontos indisponíveis, horários empatados e interrupções indicadas pela trajetória encerram a comparação. O próximo ponto elegível inicia outro trecho e informa seu estado inicial; não cria passagem através da lacuna nem mantém certeza de reentrada. O gap e os pontos indisponíveis continuam na curva.

A consulta pode atravessar a meia-noite sem reiniciar o trecho. Um novo recorte começando à meia-noite não traz predecessor implícito de outro dia. Estados de episódios globais, primeira passagem histórica integral e estratégia não são materializados neste recorte mínimo.

Cada evento contém nível, direção, tipo, segment_id, os dois point_ids, intervalo **(horário anterior, horário atual]**, disponibilidade necessária e event_id determinístico. `exact_crossing_at` é nulo. A disponibilidade necessária inclui o prefixo do trecho usado para classificar a reentrada.

Um salto atravessando vários níveis gera ocorrências com **os mesmos dois snapshots e o mesmo intervalo**. A ordem dos níveis na lista é apresentação, não caminho observado. Não cria preço/supply no nível intermediário, horário interpolado ou sequência intrabar.

IDs dependem da identidade da série, política/grade, limiar de gap, trecho, endpoints, hashes de interpretação, nível e tipo. Repetir o corte conserva IDs; revisões relevantes geram outra derivação. O cálculo é retrospectivo no corte consultado, sem emitir notificações, ordens ou decisões operacionais.

## Snapshots do contexto

`context_snapshots` contém uma cópia do contexto econômico para cada ponto referenciado por uma passagem ou início de trecho, sem repetir o mesmo contexto em cada nível do salto. Guarda:

- captura, point_id, SHA do bruto, SHA da interpretação e disponibilidade;
- preço e market cap, método e operandos;
- circulating supply, total/max supply, decimals, liquidez e volume 5m, com estado, motivo, unidade, pointer, fact_id e contrato da 4.1;
- versões da valuation e da política econômica.

Campo indisponível continua indisponível. Não procura valores em capturas futuras ou em outro token/item/método. Cada contexto pode ser auditado pela valuation individual do anchor_fact_id. O leitor usa fatos persistidos; a conferência dos bytes brutos continua sendo uma operação individual.

`context_changes` compara preço e circulating supply entre os endpoints. Booleanos indicam mudança numérica comprovável; null significa comparação indisponível. Uma mudança de supply não é apresentada como alta causada pelo preço, demanda compradora ou operação executável.

## Implementação e limites

Novo módulo `midas_data/crossings.py`; pequenas extensões em trajectory.py para incluir contexto sob demanda e em api.py para a rota. As rotas anteriores mantêm seus formatos e defaults. A grade é uma definição compartilhada; busca de limiares usa [bisect_right](https://docs.python.org/3.13/library/bisect.html), e comparações preservam [Decimal](https://docs.python.org/3.13/library/decimal.html). Referências oficiais Python 3.13 consultadas nesta etapa.

Sem nova tabela, migration, serviço, cache, loop, normalizador ou coleta. Snapshots/eventos são derivados ao consultar e reproduzíveis pelo corte; não são novas gravações. Não relê o bruto de todos os pontos. O resumo diário continua na rota 4.3; gráficos visuais permanecem em 7.2.

A grade não torna as amostras cobertura contínua de mercado. As passagens são inferidas entre respostas observadas e no escopo da valuation escolhida, com as restrições de fonte/pool já declaradas pela 4.2.

## Testes e correção encontrada no aceite

**88 testes aprovados**, zero falhas, erros ou skips, no runtime Docker Python 3.13. São 21 casos da etapa e 67 regressões. Integração HTTP/PostgreSQL usa schema privado e fixtures sintéticas, sem inserir dados de teste no schema de produção.

Casos: 50 níveis e fronteiras únicas, igualdade, salto, subida/queda/reentrada, abaixo/acima da grade, precisão decimal, gap, valor indisponível, horários empatados, virada UTC, limites, revisão, chegada atrasada, corte anterior reproduzível e contexto igual à valuation individual.

A primeira rodada tinha 87 testes verdes, mas o aceite real encontrou **diferença textual no timestamp do contexto**: PostgreSQL JSONB retornava `.91247`, enquanto o leitor nativo retornava `.912470`. Mesmo instante, representações diferentes. Uma regressão isolada reproduziu a falha; a leitura opcional do contexto agora converte a disponibilidade para datetime UTC antes da serialização. A rodada final passou em 88 testes e a comparação real passou exatamente. Valores econômicos, instantes e dados armazenados não foram alterados.

Evidência da falha: `timestamp-reproduction.xml`; a primeira suíte permanece em `contract-tests-initial.xml`. A imagem final e o aceite corrigido substituem o candidato inicial.

## Prova real read-only

Corte fixo **2026-09-10T20:02:26.964021Z**, seis consultas sobre dois tokens já armazenados. Um recorte recente de três horas e outro do dia UTC 09/09:

| Recorte | Método | Pontos | Passagens | Reentradas | Tempo pontual |
|---|---|---:|---:|---:|---:|
| 3 h | token_info | 10 | 7 | 1 | 43 ms |
| 3 h | Trending | 30 | 24 | 6 | 68 ms |
| 3 h | Trenches | 18 | 0 | 0 | 32 ms |
| Dia UTC | token_info | 18 | 35 | 8 | 72 ms |
| Dia UTC | Trending | 147 | 115 | 43 | 462 ms |
| Dia UTC | Trenches | 23 | 0 | 0 | 62 ms |

**246 pontos, 181 passagens e 58 reentradas**. Enumeração independente de cada par de pontos e dos 50 limiares reproduziu todos os eventos e classificações; cada salto compartilhou referências reais, sem evento através de quebra. Os 41 pontos Trenches não elegíveis não produziram passagens.

As seis séries ficaram idênticas à API anterior no mesmo corte, assim como os três resumos diários comparados. Repetições preservaram respostas/IDs. Quatro contextos completos coincidiram com a valuation individual e tiveram SHA256 dos brutos conferido; fatos e interpretações selecionados permaneceram iguais.

Tempos são amostras locais deste corpus, não SLA ou teste de escala. Medições extensas e quota continuam no escopo já reduzido pelo usuário.

## Runtime e próximo passo

Somente API atualizada para `midas-data-app:crossings-v1-20260910`, imagem final `sha256:65efd723878944362b71e0f4a00e196c1a98daa416f972cc5f0a0e8c4cd78916`. Worker tracking-v1, coletor tracking-v2 e os três destinos mantiveram IDs; seis serviços saudáveis. DATA_APP_IMAGE atualizada no env privado, credenciais preservadas.

Evidências em `.scratch/checkpoint-crossings-20260910/`: seleção, before.json, seis respostas, live-acceptance.json, XMLs, build/deploy inicial/final, comparação de serviços e acceptance.json. Não houve chamada GMGN adicional de validação, reprocessamento histórico ou operação financeira.

**4.5:** fechar o aceite integrado de série, resumo diário, passagens e contextos, com cortes temporais, revisões, lacunas, virada UTC e limites. Etapas de features/estratégias seguem posteriores; não são iniciadas automaticamente.
