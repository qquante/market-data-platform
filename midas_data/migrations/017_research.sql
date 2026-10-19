CREATE TABLE research_jobs (
    job_id text PRIMARY KEY CHECK(job_id ~ '^[0-9a-f]{64}$'),
    definition jsonb NOT NULL CHECK(octet_length(definition::text)<=16384),
    implementation jsonb NOT NULL CHECK(octet_length(implementation::text)<=16384),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','failed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    member_count integer,
    completed_count integer NOT NULL DEFAULT 0,
    input_points integer NOT NULL DEFAULT 0,
    output_bytes integer NOT NULL DEFAULT 0,
    last_error text,
    summary jsonb,
    bundle_sha256 text,
    bundle jsonb CHECK(octet_length(bundle::text)<=16777216)
);
CREATE INDEX research_jobs_queue_idx ON research_jobs(created_at) WHERE state IN ('queued','running');
CREATE TABLE research_members (
    job_id text NOT NULL REFERENCES research_jobs(job_id),
    registry_id bigint NOT NULL,
    membership jsonb NOT NULL CHECK(octet_length(membership::text)<=8192),
    payload jsonb CHECK(octet_length(payload::text)<=1048576),
    payload_sha256 text,
    PRIMARY KEY(job_id,registry_id),
    CHECK((payload IS NULL) = (payload_sha256 IS NULL))
);
