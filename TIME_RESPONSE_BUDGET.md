# CP-TIME-BUDGET-01 — estados temporais e orçamento de resposta

Concluído em 09/09/2026. Owner A0, sem agentes adicionais. Escopo: respostas reais já salvas no CP-LIVE-01, sem chamadas novas à GMGN e sem coleta contínua.

## Entrega operacional

A rota existente `/v1/observations/{fact_id}/context` agora retorna `gmgn-observation-context-v2`, com dois grupos adicionais:

- `temporal_attributes`: campos de evento específicos de cada endpoint, incluindo valor textual original, pointer, unidade e estado. Distingue `absent`, `null`, `empty`, `zero_unusable`, `negative_invalid`, `invalid_type`, `invalid_range` e `reported`. `usable_seconds_text` é nulo quando não utilizável. Zero de duração pode ser reportado; zero de timestamp não representa uma data de 1970. `reported` significa atributo fornecido que passou nas validações locais, não verificação on-chain de ocorrência, cronologia ou exatidão da fonte.
- `response`: bytes originais, pointer e quantidade real de itens na lista/categoria. `requested_limit_comparison` informa `exceeded`, `within_requested`, `unknown` ou `not_applicable`. A contagem inclui entradas inválidas presentes na lista original. Não corta itens nem declara cobertura completa quando a resposta fica abaixo do limite.

`temporal_consistency.duration_matches_reported_clocks` compara criação, conclusão e duração somente quando os três números são utilizáveis. Retorna nulo quando faltam relógios utilizáveis; nunca substitui a duração reportada por uma calculada. Zero não comprova evento ainda não ocorrido. Strings numéricas inteiras estritas são permitidas; booleanos, frações e sintaxe exponencial não são coerções válidas para esses atributos.

Os relógios de captura/recebimento/publicação e os fatos v4 permanecem como estavam. É uma interpretação consultável do bruto, com versão própria; nenhuma migration, regravação de fatos ou revisão silenciosa dos recibos. As qualidades `invalid` já registradas permanecem; o novo contexto explica os estados em detalhe. Preço, market cap, moeda/base de oferta e aliases sem comprovação continuam pendentes.

## Proteção na entrada do cliente

`GmgnOpenApiClient` recebeu `max_response_bytes`, inteiro positivo configurável, padrão **4 MiB**, igual ao orçamento padrão de ingresso da API desta base. O transporte urllib lê no máximo orçamento + um byte para detectar excesso, fecha a resposta e lança `GmgnOpenApiError` com `api_code=MIDAS_RESPONSE_TOO_LARGE` antes de interpretar JSON. A regra vale para sucesso e erros HTTP; o cliente também rejeita conteúdo excessivo devolvido por transportes customizados.

Não há retry automático, truncamento aceito como evidência completa ou avanço de checkpoint por essa rejeição. O corpo acima do orçamento não é preservado integralmente por esse transporte. O futuro coletor precisa persistir o diagnóstico sanitizado e replanejar a captura; não pode marcar a tentativa como concluída. Em transportes customizados, o guard do cliente controla admissão, mas o próprio transporte precisa limitar a leitura para controlar memória.

A mudança está no código local do cliente, com testes offline; ele ainda não está ligado a um serviço de coleta. No Docker, foi implantada a atualização do contexto. Orçamentos já existentes de admissão, nós JSON, fatos e saída normalizada continuam em vigor. Não foram adicionados frameworks ou conexões por estratégia.

## Diagnóstico do limite Trenches

Hipóteses verificadas: localização errada do parâmetro; corte no cliente oficial; comportamento no servidor. O builder instalado do `gmgn-cli` usa `limit` dentro de cada categoria, como o Midas, e seu handler entrega os dados sem `slice` por limite. O pedido salvo com limite 2 trouxe 60 entradas em cada uma das três categorias. Não há evidência suficiente para explicar o comportamento interno do servidor ou garantir que os outros filtros foram aplicados.

Por isso o builder não foi alterado por tentativa, nem o excedente foi descartado. Agora o contexto mostra `list_item_count=60`, pedido `limit=2` e comparação `exceeded`. A documentação oficial descreve o limite por categoria; não é garantia de tamanho efetivo. A causa exata no servidor permanece não determinada. Referência: [GMGN Market](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-market/SKILL.md), seções de parâmetros Trenches e campos temporais; conferido também `gmgn-cli/dist/client/OpenApiClient.js` e `commands/market.js` instalados.

## Provas

- Testes antes da mudança: 25 casos novos falharam; 13 existentes passaram. Depois da mudança: **93 testes focados aprovados**, incluindo contratos Trenches, contexto, cliente e leitura limitada de sucesso/HTTPError.
- **Um teste integrado Docker aprovado**, verificando fonte, corte de conhecimento, recibos, atributos temporais e quantidade da resposta.
- Replay offline dos três brutos originais: 4.749 fatos e 23.831 campos com as contagens/versão do normalizador preservadas. Trenches normalizou em 0,212 s nesta amostra isolada; não é certificação de carga sustentada.
- Estados temporais nas respostas reais: 242 zeros inutilizáveis e 45 negativos. Nos 180 itens Trenches, 15 tinham os três atributos utilizáveis e duração consistente; os outros 165 não permitiam essa comparação. Nenhum delta foi inventado para eles.
- Releitura física: três brutos/lotes, 4.749 fatos e checksums/recibos concordantes nos dois destinos. Seis pares de consultas de contexto/série aprovados; reenvio local idempotente aprovado, sem nova chamada ao provedor.
- Cinco serviços saudáveis, filas e erros analíticos pendentes zero. A integração adicionou duas fixtures; elas seguem separadas dos três registros reais por `source_kind`.

Imagem implantada: `midas-data-app:context-v2-20260909`.

ID da imagem: `sha256:fdaf6ee28758699eab230d316cdb85625a3a381a164e9bbfa7cd60061af5f9f9`. API/worker recriados; PostgreSQL, NATS, ClickHouse e volumes preservados.

Evidências: `.scratch/checkpoint-time-budget-20260909/` contém testes red/focados/integrado, logs de build/deploy, replay, consultas, verificação física e manifesto da imagem. Não há suíte massiva repetida nem prova de 60 minutos nesta entrega.

## Próximo recorte

Conectar um coletor contínuo limitado ao novo ingresso, com um orçamento compartilhado por fonte, persistência local antes do envio, UUID estável/retry de ACK incerto e recuperação de falhas. Excesso de resposta deve ser falha visível e retomável, nunca evidência parcial ou descarte silencioso. Medir bytes, quantidade realmente recebida, atraso e crescimento; não dimensionar apenas pelo `limit` pedido. As interpretações econômicas pendentes permanecem explicitamente não verificadas e não devem contaminar estratégias.
