-- Latest shared projections only; historical evidence remains in captures/facts.
CREATE INDEX normalization_publication_idx
    ON normalization_runs(available_at, capture_id, normalizer_version)
    WHERE available_at IS NOT NULL;
CREATE TABLE feature_windows (
    window_id text PRIMARY KEY CHECK(window_id ~ '^[0-9a-f]{64}$'),
    config jsonb NOT NULL CHECK(octet_length(config::text)<=2048),
    expires_at timestamptz NOT NULL,
    generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
    publication_cutoff timestamptz,
    next_due_at timestamptz,
    payload bytea CHECK(octet_length(payload)<=4194304),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((payload IS NULL) = (publication_cutoff IS NULL))
);
