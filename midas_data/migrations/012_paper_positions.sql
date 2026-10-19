-- Isolated paper lots; no wallet, venue, order or financial-execution integration.
CREATE TABLE paper_positions (
    position_number bigserial UNIQUE NOT NULL,
    position_id text PRIMARY KEY CHECK(position_id ~ '^[0-9a-f]{64}$'),
    rule_id text NOT NULL,
    signal_sequence bigint NOT NULL,
    policy_id text NOT NULL CHECK(policy_id ~ '^[0-9a-f]{64}$'),
    policy jsonb NOT NULL CHECK(octet_length(policy::text)<=8192),
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=8192),
    window_id text NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
    state jsonb NOT NULL CHECK(octet_length(state::text)<=16384),
    status text NOT NULL CHECK(status IN ('entry_pending','open','closed','entry_expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(rule_id,signal_sequence),
    FOREIGN KEY(rule_id,signal_sequence) REFERENCES rule_evaluations(rule_id,sequence)
);
CREATE INDEX paper_open_idx ON paper_positions(position_number) WHERE status IN ('entry_pending','open');
CREATE TABLE paper_events (
    position_id text NOT NULL REFERENCES paper_positions(position_id),
    revision bigint NOT NULL CHECK(revision>0),
    kind text NOT NULL CHECK(kind IN ('requested','waiting','opened','closed','entry_expired')),
    record jsonb NOT NULL CHECK(octet_length(record::text)<=16384),
    frame_key text REFERENCES rule_frames(frame_key),
    PRIMARY KEY(position_id,revision)
);
