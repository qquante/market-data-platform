# Parser offline de holders — 6.1.2

**CP-HOLDERS-PARSER-01 concluído.** Sessão de 10/09/2026; verificações em 11/09 UTC. Implementação local, ainda sem persistência ou integração à coleta. Próximo: **6.1.3**.

## Entrega

[holders.py](midas_data/holders.py) implementa `parse_holders(metadata, raw)`, `HolderParseResult`, `HolderObservation` e `HolderValue`. Um módulo, sem dependência nova. Reutiliza os helpers internos de JSON, pointers e números do normalizador existente; esse normalizador não foi alterado.

O resultado conserva metadata, SHA-256 dos bytes, versão, seleção da chamada e catálogo completo. Cada linha tem identidade estável por captura/versão/pointer, token consultado e carteira distintos, token account opcional, relógio e valores com qualidade/motivo. `value_type` descreve o tipo JSON observado; `declared_type` descreve o tipo do contrato. Valores ausentes não são criados.

O parser aceita somente `token_top_holders`, cadeia Solana e objeto de dados direto `{"list":[...]}`, declarado como `legacy_reserialized`. Envelope HTTP, endereço de papel ambíguo no topo ou contexto incompatível produzem issue sem holders. Parâmetros de seleção ausentes permanecem ausentes; parâmetros explícitos inválidos não recebem defaults silenciosos. `returned_rows=null` é distinto de lista vazia observada, com zero.

- Saldos usam Decimal; lamports permanecem inteiros. Não há soma de aliases, reaplicação de decimals ou conversão de escala por magnitude.
- Alias divergente preserva os dois valores como unverified; wallet repetida preserva todas as linhas e sinaliza identidade ambígua.
- Wallet inválida impede aquela linha semântica; conflito de cadeia invalida os holders da captura inteira. Bruto permanece sob responsabilidade do chamador, recuperável pelo hash.
- Limites: 8 MiB de entrada, 20.000 nós, profundidade 32, 100 linhas e 16 MiB de saída JSON. Erro estrutural ou de saída não retorna catálogo/linhas parciais.
- O resultado não contém `Fact`, não calcula total de holders/top10 e não registra endpoint na API, worker ou coletor.

Contrato: [gmgn-holders-v1.json](contracts/gmgn-holders-v1.json), agora com status `offline_parser_implemented`. Sem alteração da semântica v1 definida na [6.1.1](HOLDERS_CONTRACT.md).

## Prova

**343 testes passaram em Docker/Python 3.13, sem rede e sem banco:**

| Grupo | Resultado |
|---|---|
| [test_holders.py](tests/test_holders.py) | 85 passaram em 0,81 s |
| Contratos, normalizador de tokens e trenches | 258 passaram em 2,34 s |

Cobertura comportamental: precisão, unidade nativa, domínio da fração, missing/zero, alias conflitante, identidade, duplicação, contexto, Unicode, JSON inválido, limites e ausência de promoção a fatos de token. Não é benchmark de capacidade ou frescor.

Também foram reproduzidos os cinco arquivos usados na 6.1.1: três listas com 100/1/100 linhas, uma lista vazia válida e um arquivo vazio de CLI falha. Resultado: **201 observações, 1.407 valores de contrato válidos e 17.802 nós de catálogo**. Os resultados repetidos foram idênticos byte a byte. O arquivo vazio gerou `invalid_json`, sem se transformar em lista vazia válida.

Os corpos são respostas CLI salvas. Os metadados usados nesse replay são **fixture**, com clocks/status HTTP sintéticos explícitos; os arquivos não fornecem os clocks/status HTTP originais. Nada foi importado como evidência live. O contrato atual de captura exige esses campos; sua representação/importação real precisa ser resolvida antes da persistência. A fidelidade dos números anteriores à serialização CLI continua limitada.

Evidências: [.scratch/checkpoint-holders-parser-20260910](../.scratch/checkpoint-holders-parser-20260910), com os dois XMLs dos testes, `replay_samples.py`, `sample-replay.json`, backups e validação final.

Validação final em 11/09 às 02:48:40Z: 101 arquivos existentes de código/SQL/Compose preservados; seis serviços mantiveram IDs, imagens e estado saudável. API/coletor/worker não receberam esta implementação offline.

## Próximo recorte

**6.1.3:** persistir snapshots/linhas de holders em estrutura própria, com idempotência e consulta limitada por captura/token/carteira; manter a proveniência e resolver o contrato de envelope/clocks para dados reais. Depois, **6.1.4**, canário pequeno na agenda existente. Nenhum serviço novo ou coleta contínua de holders nesta etapa.
