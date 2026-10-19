-- One simulated account. Existing isolated lots retain NULL account_id and their original ledger.
CREATE TABLE paper_account (
    account_id text PRIMARY KEY CHECK(account_id='main'),
    policy_id text NOT NULL CHECK(policy_id ~ '^[0-9a-f]{64}$'),
    config jsonb NOT NULL CHECK(octet_length(config::text)<=4096),
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=8192),
    state jsonb NOT NULL CHECK(octet_length(state::text)<=16384),
    revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE paper_positions ADD COLUMN account_id text REFERENCES paper_account(account_id);
CREATE TABLE paper_account_events (
    account_id text NOT NULL REFERENCES paper_account(account_id),
    revision bigint NOT NULL CHECK(revision>0),
    kind text NOT NULL CHECK(kind IN ('initialized','requested','opened','entry_expired','closed')),
    position_id text,
    position_revision bigint,
    record jsonb NOT NULL CHECK(octet_length(record::text)<=16384),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account_id,revision),
    UNIQUE(position_id,position_revision),
    FOREIGN KEY(position_id,position_revision) REFERENCES paper_events(position_id,revision),
    CHECK((kind='initialized' AND position_id IS NULL AND position_revision IS NULL)
       OR (kind<>'initialized' AND position_id IS NOT NULL AND position_revision IS NOT NULL))
);
