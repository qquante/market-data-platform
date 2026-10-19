-- Future-only paper subscriptions; source evaluations remain the durable inbox.
CREATE TABLE paper_bindings (
    rule_id text PRIMARY KEY REFERENCES active_rules(rule_id),
    config jsonb NOT NULL CHECK(octet_length(config::text)<=8192),
    definition_id text NOT NULL CHECK(definition_id ~ '^[0-9a-f]{64}$'),
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=16384),
    enabled boolean NOT NULL DEFAULT true,
    cursor bigint NOT NULL CHECK(cursor>=0),
    revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
    last_event jsonb CHECK(octet_length(last_event::text)<=8192),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE paper_binding_events (
    rule_id text NOT NULL REFERENCES paper_bindings(rule_id),
    revision bigint NOT NULL CHECK(revision>0),
    kind text NOT NULL CHECK(kind IN ('enabled','paused','admitted','existing','rejected')),
    signal_sequence bigint,
    position_id text REFERENCES paper_positions(position_id),
    record jsonb NOT NULL CHECK(octet_length(record::text)<=8192),
    PRIMARY KEY(rule_id,revision),
    UNIQUE(rule_id,signal_sequence),
    FOREIGN KEY(rule_id,signal_sequence) REFERENCES rule_evaluations(rule_id,sequence),
    CHECK((kind IN ('enabled','paused') AND signal_sequence IS NULL AND position_id IS NULL)
       OR (kind IN ('admitted','existing') AND signal_sequence IS NOT NULL AND position_id IS NOT NULL)
       OR (kind='rejected' AND signal_sequence IS NOT NULL AND position_id IS NULL))
);
CREATE INDEX rule_signals_idx ON rule_evaluations(rule_id,sequence) WHERE disposition='signal';
