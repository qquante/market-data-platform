CREATE TABLE research_experiments (
    experiment_id text PRIMARY KEY CHECK(experiment_id ~ '^[0-9a-f]{64}$'),
    job_id text NOT NULL REFERENCES research_jobs(job_id),
    dataset_sha256 text NOT NULL,
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=16384),
    implementation jsonb NOT NULL CHECK(octet_length(implementation::text)<=8192),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','failed')),
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_until timestamptz,
    last_error text,
    result jsonb CHECK(octet_length(result::text)<=8388608),
    result_sha256 text
);
CREATE INDEX research_experiments_pending_idx ON research_experiments(created_at) WHERE state IN ('queued','running');
