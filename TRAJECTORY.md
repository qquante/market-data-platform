# 4.2 — série temporal por token e método

> Atualização 4.3: [curva e resumo do dia UTC](DAILY_TRAJECTORY.md) implantados, reutilizando esta série e preservando sua resposta. O relatório abaixo registra o checkpoint original da 4.2.

**Concluída e implantada em 10/09/2026.** Checkpoint **CP-TRAJECTORY-01**, A0, sem subagentes. Próxima entrega: **4.3**, curva e resumo do dia UTC com máximo observado e cobertura.

## Consulta disponível

`GET /v1/trajectory` exige autenticação da API. Parâmetros:

| Parâmetro | Contrato |
|---|---|
| `source_kind` | Obrigatório: fixture, gmgn ou legacy_gmgn, sem mistura |
| `chain`, `token` | Identidade canônica: EVM normalizado; Solana preserva caixa |
| `method` | Um dos três métodos abaixo; não existe seleção “all” |
| `start`, `end` | UTC ou offset explícito convertido para UTC; intervalo [start, end), positivo e de até 24 h |
| `knowledge_cutoff` | Obrigatório; obter de /v1/clock; end ≤ corte ≤ relógio atual |
| `max_points` | 500 por padrão, máximo 2.000; excesso gera 413, sem truncamento silencioso |
| `gap_after_seconds` | Opcional, 1–86.400 s; define quando sinalizar intervalo sem amostra elegível |

A resposta usa `gmgn-trajectory-v1`. Esta série é de **market cap em USD**, com preço como contexto do ponto; valores financeiros continuam decimais textuais.

| Método | Valor elegível | Limiar padrão de gap |
|---|---|---|
| `gmgn.token_info.price_times_circulating_supply.v1` | Preço × circulating supply da mesma captura/item/interpretação | 720 s |
| `gmgn.market_rank.v4.market_cap` | Market cap reportado pelo Trending | 120 s |
| `gmgn.trenches.v1.usd_market_cap` | Somente usd_market_cap homologado; alias market_cap não serve | 120 s |

Os limiares são convenções de leitura configuráveis, não um SLA da coleta. Métodos, fontes e escopos de pool não são fundidos. A origem não identifica necessariamente um pool equivalente entre endpoints.

Exemplo reproduzível da prova: chain `sol`, token `9HsYGS4H6RoTjBNiV4G38NzKVrXiWhkgCPMWrChZpump`, fonte `gmgn`, método token_info acima, início `2026-09-10T16:15:24.816086Z`, fim e corte `2026-09-10T19:15:24.816086Z`. A disponibilidade depende de conservar esse histórico.

## Tempo, revisões e lacunas

A ordenação usa **received_at da captura**, declarado pelo coletor, em UTC. Esse relógio descreve quando a resposta foi recebida; não afirma a hora de um negócio on-chain. O eixo não muda para source_event_at quando esse campo aparece.

Para cada captura, a consulta seleciona a **interpretação inteira mais recente publicada até o corte**, pela mesma ordem disponível/versão já usada pela base. Chegada atrasada ou nova revisão não altera uma consulta com corte anterior. Uma revisão sem operandos ou sem fatos invalida o ponto; não recupera valores de uma versão antiga.

Cada ponto carrega capture_id, normalizer_version, disponibilidade, quantidade de interpretações conhecidas, versão anterior, SHA da interpretação, SHA do bruto, anchor_fact_id e IDs dos operandos. `point_id` é o capture_id, único dentro da série identificada por fonte/chain/token/método; sozinho não identifica uma série global.

`state=unavailable` mantém o ponto e o motivo, com valor nulo. Duplicação do token em itens diferentes da mesma captura impede escolher arbitrariamente um deles. Empates no horário recebem contagem e interrupção explícita da continuidade; a ordenação por capture_id é determinística, sem inventar ordem de mercado.

`gaps` marca intervalos sem amostras elegíveis acima do limiar, incluindo bordas. Sem nenhum valor elegível, cobre todo o intervalo. `break_before` também interrompe a linha por ponto indisponível ou ordem ambígua, mesmo quando o intervalo é menor que o limiar. Não há interpolação, preenchimento com zero ou propagação do último valor.

**Limite de cobertura:** os candidatos são capturas vinculadas a fatos publicados daquele token, conhecidos no corte. Uma captura inicial sem nenhum fato identificável não aparece automaticamente como ponto; uma revisão vazia de captura anteriormente identificada aparece. Ausência de pontos não prova ausência de negócios, falta de atividade ou acompanhamento contínuo. A consulta não reconstrói períodos de admissão da agenda. `query_complete` declara completude apenas desse universo e intervalo.

## Implementação e orçamento

Novo módulo `midas_data/trajectory.py`, uma rota pequena e reutilização da valuation homologada. PostgreSQL lê índices existentes e a barreira de publicação já usada pelos cortes temporais; não há tabela, migration, serviço, cache, dependência ou processamento em segundo plano novo.

A leitura usa os fatos persistidos e referências ao bruto; **não relê todos os brutos** nem reconfirma cada hash por requisição. Para auditoria de um ponto, a rota de valuation individual mantém sua leitura/verificação original. A prova desta entrega conferiu três amostras reais por esse caminho.

A API compartilha os dois leitores de contexto existentes; saturação retorna 429. Espera de consulta limitada a 5 s, resposta a 4 MiB; ultrapassar o orçamento falha explicitamente. Cada ponto admite os 12 pointers econômicos conhecidos mais name/symbol, com uma linha extra para detectar excesso. Excesso ou ambiguidade torna o ponto indisponível.

Correção complementar da **4.1**: a lista de leitura já declarava 12 pointers, mas o SQL ainda usava LIMIT 6. Agora lê até 12 e detecta excesso com a 13ª linha, retornando erro explícito em vez de resultado parcial. O teste integrado com sete campos passou a exigir o motivo correto para total_supply/max_supply; assim distingue campo não homologado de campo omitido pelo limite antigo.

A seleção por captura usa [LATERAL e funções de janela do PostgreSQL](https://www.postgresql.org/docs/18/queries-table-expressions.html), respeitando a [barreira de locks transacionais](https://www.postgresql.org/docs/18/explicit-locking.html) existente. Documentação oficial consultada em 10/09/2026.

## Provas desta entrega

**52 testes aprovados**, zero falhas, erros ou skips, na imagem Python 3.13 do projeto. Incluem a regressão econômica, precisão decimal, formatos de item, gaps, empate de horários e integração HTTP + PostgreSQL em schema privado. Fixtures sintéticas provaram chegada atrasada, revisão de valor, remoção de operando, revisão vazia, corte antigo estável, intervalo exclusivo à direita, fonte/método separados, autenticação e limites da API. Nenhuma captura de teste foi inserida no schema de produção.

**Aceite real read-only**, corte `2026-09-10T19:15:24.816086Z`, janela de três horas para o mesmo token:

| Método | Pontos observados | Elegíveis | Gaps | Consulta pontual |
|---|---:|---:|---:|---:|
| token_info | 11 | 11 | 3 | 55,5 ms |
| Trending | 39 | 39 | 2 | 108,4 ms |
| Trenches | 28 | 0 | 1 | 51,5 ms |

Trenches conservou 28 valores indisponíveis; não promoveu aliases sem contrato. Três pontos token_info coincidiram com a valuation individual e tiveram SHA256 do bruto conferido. Capturas, fatos e registros imutáveis das interpretações selecionadas permaneceram iguais. As três consultas repetidas com o mesmo corte produziram respostas idênticas.

Esses tempos descrevem consultas pontuais locais, não capacidade sustentada, carga concorrente ou garantia de frescor. A agenda 3.4 mantém sua frequência e quota. A primeira seleção do probe pegou um token recente com apenas dois pontos e não cumpriu a amostra de três; ajustada a seleção para um histórico existente com ao menos três pontos, sem alterar aplicação ou coleta.

Apenas a API foi atualizada para `midas-data-app:trajectory-v1-20260910`, imagem `sha256:5c927a5541072634ed468866d9f769bb41f82bc27af095a251ca6aac3d18a1f1`. Os cinco outros containers conservaram IDs e os seis serviços ficaram saudáveis. Worker permanece tracking-v1; coletor tracking-v2. A tag padrão DATA_APP_IMAGE do env privado foi atualizada para futuras recriações compatíveis; credenciais preservadas.

Evidências em `.scratch/checkpoint-trajectory-20260910/`: testes XML, build/deploy, séries completas, live-acceptance.json, comparação de serviços e acceptance.json. Não houve chamada GMGN adicional de validação, reprocessamento histórico ou operação financeira.

## O que falta

**4.3:** curva do dia UTC, resumo diário e máximo observado com cobertura, reutilizando esta série. **4.4:** grade versionada, cruzamentos e reentradas. **4.5:** aceite integrado das consultas temporais, incluindo virada UTC e os consumidores dos próximos recortes.

Medições extensas, ampliação do catálogo sem evidência e mudanças de quota continuam adiadas.
