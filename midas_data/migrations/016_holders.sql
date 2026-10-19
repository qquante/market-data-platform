-- Holder rows have wallet identity; they never enter the token Fact namespace.
CREATE TABLE holder_runs (
    capture_id uuid NOT NULL,
    parser_version text NOT NULL,
    run_seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    source_kind text NOT NULL,
    chain text NOT NULL,
    token_address text,
    occurred_at timestamptz NOT NULL,
    returned_rows integer CHECK (returned_rows BETWEEN 0 AND 100),
    accepted_rows integer NOT NULL CHECK (accepted_rows BETWEEN 0 AND 100),
    state text NOT NULL CHECK (state IN ('ready','partial','quarantined')),
    collection_scope text NOT NULL CHECK (collection_scope='partial_list'),
    PRIMARY KEY(capture_id,parser_version),
    FOREIGN KEY(capture_id,parser_version)
        REFERENCES normalization_runs(capture_id,normalizer_version)
);
CREATE INDEX holder_runs_token_idx ON holder_runs(source_kind,chain,token_address,occurred_at,run_seq);
CREATE TABLE holder_observations (
    capture_id uuid NOT NULL,
    parser_version text NOT NULL,
    holder_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 99),
    wallet_address text NOT NULL,
    observation jsonb NOT NULL,
    PRIMARY KEY(capture_id,parser_version,ordinal),
    UNIQUE(holder_id,parser_version),
    FOREIGN KEY(capture_id,parser_version) REFERENCES holder_runs(capture_id,parser_version)
);
