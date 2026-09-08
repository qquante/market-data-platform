CREATE TABLE raw_objects (
    sha256 text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    relative_path text NOT NULL UNIQUE,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE captures (
    capture_id uuid PRIMARY KEY,
    fingerprint text NOT NULL,
    raw_sha256 text NOT NULL REFERENCES raw_objects(sha256),
    metadata jsonb NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('fixture','gmgn','legacy_gmgn')),
    evidence_kind text NOT NULL CHECK (evidence_kind IN ('original_response','legacy_reserialized')),
    endpoint text NOT NULL,
    chain text NOT NULL,
    received_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    available_at timestamptz,
    normalization_state text NOT NULL DEFAULT 'pending' CHECK (normalization_state IN ('pending','ready','quarantined')),
    last_error text
);
CREATE INDEX captures_queue_idx ON captures(recorded_at) WHERE normalization_state='pending';
CREATE INDEX captures_source_idx ON captures(source_kind, received_at, capture_id);
CREATE TABLE outbox (
    capture_id uuid PRIMARY KEY REFERENCES captures(capture_id),
    publish_count integer NOT NULL DEFAULT 0,
    published_at timestamptz,
    consumed_at timestamptz,
    last_error text
);
CREATE INDEX outbox_pending_idx ON outbox(published_at) WHERE consumed_at IS NULL;
CREATE TABLE normalization_runs (
    capture_id uuid NOT NULL REFERENCES captures(capture_id),
    normalizer_version text NOT NULL,
    batch_id text NOT NULL UNIQUE,
    content_sha256 text NOT NULL,
    fact_count integer NOT NULL CHECK (fact_count >= 0),
    field_count integer NOT NULL CHECK (field_count >= 0),
    issues jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    available_at timestamptz,
    analytics_applied_at timestamptz,
    analytics_receipt jsonb,
    PRIMARY KEY (capture_id, normalizer_version)
);
CREATE TABLE facts (
    fact_id uuid PRIMARY KEY,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    capture_id uuid NOT NULL REFERENCES captures(capture_id),
    normalizer_version text NOT NULL,
    source_kind text NOT NULL,
    evidence_kind text NOT NULL,
    chain text NOT NULL,
    token_address text NOT NULL,
    metric text NOT NULL,
    value_text text,
    numeric_value numeric,
    value_type text NOT NULL CHECK (value_type IN ('decimal','integer','boolean','string')),
    unit text,
    category text NOT NULL,
    occurred_at timestamptz NOT NULL,
    time_basis text NOT NULL CHECK (time_basis IN ('source','received')),
    pointer text NOT NULL,
    quality text NOT NULL CHECK (quality IN ('valid','unverified','invalid','missing')),
    contract_version text NOT NULL,
    available_at timestamptz,
    FOREIGN KEY (capture_id,normalizer_version) REFERENCES normalization_runs(capture_id,normalizer_version)
);
CREATE INDEX facts_token_time_idx ON facts(chain,token_address,metric,occurred_at DESC,seq DESC);
CREATE INDEX facts_capture_idx ON facts(capture_id,normalizer_version);
CREATE TABLE field_observations (
    capture_id uuid NOT NULL,
    normalizer_version text NOT NULL,
    pointer text NOT NULL,
    category text NOT NULL,
    value_type text NOT NULL,
    quality text NOT NULL,
    reason text NOT NULL,
    contract_id text,
    PRIMARY KEY (capture_id,normalizer_version,pointer),
    FOREIGN KEY (capture_id,normalizer_version) REFERENCES normalization_runs(capture_id,normalizer_version)
);
CREATE INDEX field_catalog_idx ON field_observations(category,quality);
CREATE TABLE worker_health (
    worker_name text PRIMARY KEY,
    heartbeat_at timestamptz NOT NULL,
    state text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE import_cursors (
    source_name text PRIMARY KEY,
    cursor_value text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    details jsonb NOT NULL DEFAULT '{}'
);
