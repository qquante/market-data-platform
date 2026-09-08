# Importação limitada do legado GMGN

O importador copia evidência existente do PostgreSQL legado para a API local da base independente. Não consulta a GMGN, não abre conexões de estratégia, não faz trading e não altera a origem.

Arquivos: `scripts/gmgn-data-import.ps1`, `data_platform/midas_data/importer.py` e `data_platform/tests/test_importer.py`. O script depende da imagem atualizada `midas-data-app:0.1.0` e dos serviços da base já iniciados pelo coordenador. Ele não faz build nem inicia dependências.

## Origem e limites

A origem fixa é o container `midas-fomo-postgres-1`, usuário e banco `midas_fomo`. A consulta roda com `BEGIN READ ONLY`, `statement_timeout = '5s'`, timezone UTC e `ROLLBACK` ao terminar.

A CTE `recent AS MATERIALIZED` seleciona **as últimas 100 chamadas com `status='success'`**, ordenadas por `received_at_utc DESC`. Essa forma pode usar o índice parcial `gmgn_api_calls_success_time_idx`. Só depois são filtrados `token_info` e `market_rank`, escolhidas até 10 chamadas e feito o join por SHA com `gmgn_payload_blobs`.

Não há varredura histórica, paginação automática ou compensação buscando registros mais antigos. A amostra pode conter menos de 10 chamadas elegíveis. Alterar a janela exige outra decisão; aumentar o timeout ou remover o filtro de status não faz parte deste procedimento.

| Limite | Contrato |
|---|---|
| Chamadas consultadas na janela | 100 sucessos recentes |
| Respostas exportadas por execução | 1–10, conforme `-Limit` |
| Corpo descompactado | Até 4 MiB por chamada |
| Blob zlib comprimido | Até 4 MiB + 64 KiB |
| Arquivo JSONL | Até 16 MiB e 100 linhas físicas |
| Resposta da API lida pelo CLI | Até 1 MiB por request |
| Metadados HTTP | Até 16 KiB de JSON ASCII escapado |
| Destino | HTTP na porta 8791, apenas loopback ou hostname interno `api` |

O script limita o arquivo durante a escrita. Excesso ou falha deixa apenas um arquivo `.partial`, não inicia ingestão e não substitui exportações anteriores. Um arquivo com dez respostas muito grandes pode exceder 16 MiB; nesse caso, usar `-Limit` menor. O CLI verifica todos os registros antes de qualquer POST e não mantém todos os corpos descompactados simultaneamente.

## O que conta como evidência original

Em `src/midas_gmgn/forensic_store.py`, `record_success_call` recebe `evidence.raw_body`, calcula SHA-256 sobre esses bytes e armazena `zlib.compress(raw)` com `raw_bytes`. O mesmo digest é usado em `gmgn_api_calls.response_sha256` e `gmgn_payload_blobs.payload_sha256`.

Para cada linha, o importador exige:

1. Codec exatamente `zlib`, base64 válido e stream completo, sem dados anexados ou streams concatenados.
2. Descompactação limitada, com comprimento exatamente igual a `raw_bytes`.
3. `sha256(raw) == response_sha256 == payload_sha256`.
4. Metadados compatíveis com o contrato da base: UUID, endpoint permitido, HTTP de sucesso e relógios com timezone explícito.

Somente após esses checks a captura recebe `source_kind=legacy_gmgn` e `evidence_kind=original_response`. Isso comprova consistência com os bytes preservados pelo legado; não é assinatura da GMGN nem verificação independente da verdade econômica do conteúdo. Falhas de hash ou comprimento são recusadas; não há fallback para JSON reserializado e não há troca automática para outro tipo de evidência.

O POST envia **os bytes descompactados originais**, sem `json.dumps` do corpo. A precisão decimal, a representação original do JSON e os campos desconhecidos permanecem no bruto. Uma resposta original pode ter normalização `quarantined` ou `unsupported`; integridade do blob e qualidade semântica são dimensões distintas.

## Identidade, parâmetros e relógios

`capture_id` é o `call_id` original. Reexecutar o mesmo arquivo usa os mesmos IDs; a API aceita duplicatas idênticas e rejeita conflito de conteúdo/metadados com HTTP 409. O CLI não repete automaticamente uma requisição cujo resultado ficou incerto.

`requested_at_utc` e `received_at_utc` são preservados, convertidos somente para UTC. `source_event_at` fica `null`: o recebimento original é proxy de observação, não tempo do evento on-chain. O horário de gravação e a disponibilidade na nova base são definidos pelo armazenamento atual; não são retrodatados para o horário legado.

A inspeção limitada da origem encontrou `query.address` e `query.chain` em `token_info`; o ranking também apresentou `interval`, `limit`, `order_by` e `direction`. A allowlist é `chain`, `address`, `token_address`, `interval`, `limit`, `order_by`, `direction`. Valores permitidos de `request_params.query` são achatados para o contrato novo. Campos equivalentes na raiz podem ser mantidos se não conflitarem. Headers, cookies, chaves, parâmetros desconhecidos e metadados operacionais de estratégia não são copiados. O script aplica a mesma allowlist antes de escrever o JSONL.

## Execução controlada

Os comandos abaixo são o procedimento operacional; documentá-los não executa ingestão.

Exportar uma amostra sem chamar a nova API:

```powershell
.\scripts\gmgn-data-import.ps1 -Limit 10 -ExportOnly
```

A saída informa o caminho gerado em `.data/gmgn-data/import-<UTC>-<id>.jsonl`. Validar esse arquivo sem POST:

```powershell
.\scripts\gmgn-data-import.ps1 -InputFile '.data/gmgn-data/import-<UTC>-<id>.jsonl' -ValidateOnly
```

Importar exatamente o arquivo já validado, quando a execução estiver liberada:

```powershell
.\scripts\gmgn-data-import.ps1 -InputFile '.data/gmgn-data/import-<UTC>-<id>.jsonl'
```

Também é possível exportar e importar em uma execução com `-Limit 10`, sem as flags de validação/exportação. O script monta o arquivo como `/import/export.jsonl:ro` no serviço `verify`, usa `--no-deps` e executa `python -m midas_data.importer`. A chave é lida de `DATA_API_KEY` no ambiente fornecido pelo Compose via `.data/gmgn-data/runtime.env`; o script não lê nem imprime seus valores e a chave não aparece em argumento de processo.

Uso direto do CLI no host, com `DATA_API_KEY` já definido no ambiente:

```powershell
python -m midas_data.importer '.data/gmgn-data/import-<UTC>-<id>.jsonl' --api-url http://127.0.0.1:8791
```

Dentro da rede Compose, o endereço é `http://api:8791`. Redirecionamentos não são seguidos e proxies de ambiente ficam desabilitados no cliente criado pelo importador.

## Como ler o resultado

- `hashes_verified` e `records` descrevem a pré-validação completa do arquivo: IDs, hashes, endpoints e tamanhos, sem corpos.
- `normalization_preview` traz contagens locais por qualidade, total de campos desconhecidos e códigos de issues. É uma prévia do normalizador da imagem do CLI, não comprovação de persistência nos destinos.
- `accepted` conta apenas respostas HTTP 202 cujo UUID, SHA e tamanho coincidem com a captura enviada. `duplicates` informa quantas já existiam idênticas; ambas as contagens podem ser menores que as linhas pré-validadas se ocorrer uma falha durante o envio.
- `catalog_sample` é uma leitura da API após os envios, limitada aos primeiros 200 grupos do catálogo das capturas legadas mais recentes. Reporta `unknown_observations` e `valid_observations`; pode estar truncada, incluir outra importação concorrente ou ainda refletir normalização pendente. Não é um total completo nem uma amostra necessariamente exclusiva deste arquivo.
- Falhas HTTP/validação usam códigos estáveis sem ecoar respostas, credenciais ou conteúdo de linhas. Em resultado incerto, repetir **o mesmo arquivo**, preservando seus IDs.

HTTP 202 e o catálogo operacional não comprovam commit em DuckLake e ClickHouse. O coordenador deve conferir os IDs retornados, os runs de normalização e os recibos verificados dos dois destinos. Campos `valid` também não equivalem à homologação econômica de todos os parâmetros.

## Validação inicial do importador (v1)

39 testes unitários do importador passaram usando fixtures e `httpx.MockTransport`, sem requests reais. Cobertura: SHA/comprimento, zlib truncado/concatenado e limite de descompactação, JSONL limitado, relógios, allowlist, pré-validação antes do primeiro POST, bytes exatos, idempotência por ID, reconhecimento do ACK, URLs locais, redirects, erro sem corpo e amostra de catálogo.

A sintaxe do PowerShell foi validada por AST sem executar o script. Junto dos testes de contratos/normalização, a suíte A1 totalizou 160 testes aprovados no runtime local. As únicas consultas reais desta tarefa leram nomes de parâmetros dentro da janela limitada e transação READ ONLY. **Nenhuma importação real, build ou alteração de serviço foi executada pelo A1 nesta entrega.** A repetição dos testes no Docker e a importação de evidência real ficam sob execução do coordenador.


## Correção do normalizador v2

`gmgn-p0-v2` corrige somente os dois problemas demonstrados na primeira amostra real: o envelope duplo de ranking e o tipo de `launchpad_status` em `token_info`.

O formato adicional de ranking exige envelope externo com exatamente `code` inteiro 0 e `data`; o interno exige `code` inteiro 0, `data` e pode conter `message`/`reason`; o `data` interno contém exatamente um array `rank`. Os pointers continuam como `/data/data/rank/<índice>/<campo>`. Erro interno não promove fatos; envelopes ambíguos, extras estruturais ou um terceiro nível não são desembrulhados automaticamente.

Em `token_info`, `launchpad_status` aceita os inteiros documentados 0, 1 e 2. Outros valores e coerção implícita de string/boolean/decimal são rejeitados. O ranking mantém seu campo string `unverified`: a documentação de seus filtros usa outra convenção, que não deve receber por inferência o significado do enum de `token_info`. Os zeros de preço continuam bloqueados para uso como preço positivo.

Os fatos e campos emitidos agora usam contratos `gmgn.<endpoint>.v2`. A versão entra na identidade determinística dos fatos; a revisão não sobrescreve fatos, runs ou recibos v1. O replay persistido v2 é uma operação separada do coordenador. `export_version=gmgn-legacy-v1` permanece correto, pois descreve o arquivo de evidência, não a versão do normalizador.

A suíte A1 após a correção passou em **186 testes**: os 160 anteriores e 26 casos adicionais, incluindo três réplicas estruturais sintéticas das respostas observadas, pointers originais, erros internos, ambiguidade, enum e IDs separados de v1. Nenhum build, ingestão ou replay persistido foi executado pelo A1 nesta correção.
