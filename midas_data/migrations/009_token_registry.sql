-- Prospective registration only; existing captures are not backfilled.
CREATE TABLE token_registry_config (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO token_registry_config(version) VALUES('gmgn-token-registry-v1');
CREATE TABLE token_registry (
    registry_id bigserial UNIQUE NOT NULL,
    source_kind text NOT NULL CHECK(source_kind IN ('fixture','gmgn','legacy_gmgn')),
    chain text NOT NULL,
    token_address text NOT NULL,
    capture_id uuid NOT NULL,
    normalizer_version text NOT NULL,
    item_pointer text NOT NULL,
    registered_at timestamptz NOT NULL,
    PRIMARY KEY(source_kind,chain,token_address),
    FOREIGN KEY(capture_id,normalizer_version) REFERENCES normalization_runs(capture_id,normalizer_version)
);
CREATE INDEX token_registry_page_idx ON token_registry(source_kind,chain,registry_id);
CREATE TABLE token_registry_runs (
    capture_id uuid NOT NULL,
    normalizer_version text NOT NULL,
    completed_at timestamptz,
    identity_count integer CHECK(identity_count>=0),
    inserted_count integer CHECK(inserted_count>=0),
    attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
    retry_at timestamptz,
    last_error text,
    PRIMARY KEY(capture_id,normalizer_version),
    FOREIGN KEY(capture_id,normalizer_version) REFERENCES normalization_runs(capture_id,normalizer_version)
);
CREATE INDEX captures_registry_start_idx ON captures(recorded_at,capture_id)
    WHERE endpoint IN ('market_rank','trenches','token_info');
