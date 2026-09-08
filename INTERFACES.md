# Interfaces — base GMGN v1

Escopo: recepção, armazenamento e verificação de dados; nenhum motor de estratégia.

## Ownership e contrato mínimo

Pacote Python independente midas_data em data_platform/. Configuração/migrations/armazenamento: A0. contracts.py/normalize.py: A1. analytics.py: A3. Não importar pacotes de execução legada.

CaptureMetadata (Pydantic): capture_id UUID; source_kind fixture|gmgn|legacy_gmgn; endpoint string; chain string; requested_at/received_at datetimes UTC obrigatórios; source_event_at datetime UTC opcional; http_status inteiro; request_params dict; evidence_kind original_response|legacy_reserialized. Datetime sem timezone é inválido; requested_at <= received_at. source_kind=fixture permanece fixture em todos destinos. Não aceitar credenciais em metadados.

normalize_capture(metadata, raw: bytes) -> NormalizationResult: facts list[Fact], fields list[FieldObservation], issues list[Issue]. Serialização por model_dump(mode='json'). Normalizador determinista, JSON numbers com Decimal, sem float intermediário. IDs UUID5 por captura+versão+pointer+metric; capturas distintas não são deduplicadas por payload idêntico.

Fact: fact_id UUID; capture_id UUID; chain/token_address strings; metric string; value_text string ou null; value_type decimal|integer|boolean|string; unit string ou null; category string; occurred_at UTC; time_basis source|received; pointer string; quality valid|unverified|invalid|missing; contract_version string. Todos esses campos fazem parte do objeto e do output JSON. value_text preserva valor exato. Fato não ganha market cap derivado sem supply/unidade homologados. Semântica opaca permanece unverified. token_address precisa vir de papel comprovado na estrutura suportada.

FieldObservation: pointer, category, value_type, quality, reason strings; contract_id opcional. O bruto preserva o conteúdo completo; catálogo observa todos os campos/folhas, incluindo desconhecidos, com limite explícito de quantidade/profundidade (rejeitar/quarentenar excesso, não truncar silenciosamente). Issue: code, pointer, detail. NormalizationResult também carrega normalizer_version string.

A0 acrescenta persistência: available_at = horário registrado pelo escritor após confirmação do commit dos fatos; esse marcador é um limite conservador da disponibilidade no armazenamento, não prova de consumo por estratégia. Capture tem captured_at local próprio; clocks originais importados permanecem separados. Fatos persistidos sem marcador após crash ficam pendentes de reconciliação e não entram nas consultas as-known.

## Interface dos destinos analíticos

analytics.py implementa classe AnalyticsSinks(lake_catalog_dsn: str, lake_path: str, clickhouse_url: str, clickhouse_user: str, clickhouse_password: str). Métodos síncronos executados fora do event loop pelo A0: ensure_schema(), apply_batch(batch_id: str, rows: list[dict]) -> dict, verify_batch(batch_id: str) -> dict, snapshot_info() -> dict. Conexões/chamadas bounded; fechamento via close().

rows são Fact.model_dump(mode='json') acrescidos de available_at string ISO UTC e normalizer_version. Todos timestamps ISO aware. Armazenar valores exatos como texto no histórico/projeção v1, com tipos/unidades explícitos; não converter globalmente para Float64 nem inventar Decimal(p,s) universal. Contrato numérico para agregações é posteriormente restrito por métrica. APIs podem usar PostgreSQL NUMERIC para curvas verificadas.

apply_batch deve ser idempotente mesmo após processo cair entre destino e retorno. DuckLake mantém recibo batch_id e fatos no mesmo commit; ClickHouse pode usar fatos versionados+view de identidade única e verificar checksum/contagem dos IDs, sem depender só de janela de dedup finita. batch_id é hash/UUID determinista por conjunto ordenado de fatos+versão; retorno reporta snapshot, contagens e fronteiras por destino. Não tratar dois destinos como transação distribuída. A0 só marca recibo operacional após verificar ambos; reentrega recupera destino parcial.

DuckLake usa catálogo PostgreSQL separado da base operacional e Parquet em volume Linux. CH tem credenciais de ambiente local fornecidas pelo A0. Nenhum sink recebe DSN legado. ensure_schema é idempotente e valida incompatibilidade; mudanças estruturais exigem migração versionada. Não expor credenciais em logs/exceções.

## Runtime

A0 implementa API local, Evidence Store durável com hash, PostgreSQL e outbox, publisher JetStream, normalizador e processamento em lotes. API expõe recepção de corpo bruto+metadados e consultas limitadas; nenhum efeito de captura em GET. Endpoint de ingestão só aceita origem local/credencial de implantação; testes não se conectam a GMGN. Captura automática de GMGN não será duplicada: importação limitada read-only do legado fornece a primeira evidência real.

A0 coordenará versões reais instaláveis e build; agentes não instalam/alteram Docker. Testes unitários não importam componentes legados nem fazem requests externos. Testes de integração usam exclusivamente projeto midas-data e volumes próprios.
