-- Definitions/state and compact decisions refer to one shared immutable frame.
CREATE TABLE active_rules (
    rule_id text PRIMARY KEY CHECK(rule_id ~ '^[0-9a-f]{64}$'),
    config jsonb NOT NULL CHECK(octet_length(config::text)<=8192),
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=16384),
    enabled boolean NOT NULL DEFAULT true,
    sequence bigint NOT NULL DEFAULT 0 CHECK(sequence>=0),
    state jsonb NOT NULL DEFAULT '{}' CHECK(octet_length(state::text)<=8192),
    last_decision jsonb CHECK(octet_length(last_decision::text)<=16384),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE rule_frames (
    frame_key text PRIMARY KEY CHECK(frame_key ~ '^[0-9a-f]{64}$'),
    payload bytea NOT NULL CHECK(octet_length(payload)<=4194304),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE rule_evaluations (
    rule_id text NOT NULL REFERENCES active_rules(rule_id),
    sequence bigint NOT NULL CHECK(sequence>0),
    decision jsonb NOT NULL CHECK(octet_length(decision::text)<=16384),
    frame_key text REFERENCES rule_frames(frame_key),
    disposition text NOT NULL CHECK(disposition IN ('signal','suppressed','pending','abstain')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(rule_id,sequence)
);
