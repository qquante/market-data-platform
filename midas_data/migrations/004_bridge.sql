-- Raw captures without a chain are supported only for endpoints without a fact adapter.
ALTER TABLE captures ALTER COLUMN chain DROP NOT NULL;
CREATE TABLE bridge_state (
    source_name text PRIMARY KEY,
    origin_at timestamptz NOT NULL,
    binding_sha256 text NOT NULL,
    progress jsonb NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    state text NOT NULL DEFAULT 'starting',
    last_error text,
    accepted_count bigint NOT NULL DEFAULT 0,
    unavailable_count bigint NOT NULL DEFAULT 0,
    rejected_count bigint NOT NULL DEFAULT 0,
    accepted_raw_bytes bigint NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE bridge_delivery (
    source_name text NOT NULL REFERENCES bridge_state(source_name),
    capture_id uuid NOT NULL,
    source_received_at timestamptz NOT NULL,
    endpoint text NOT NULL,
    source_status text NOT NULL,
    fingerprint text NOT NULL,
    state text NOT NULL CHECK(state IN ('accepted','unavailable','rejected')),
    reason text,
    raw_sha256 text,
    raw_bytes bigint,
    ack_capture_id uuid REFERENCES captures(capture_id),
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 1,
    PRIMARY KEY(source_name,capture_id),
    CHECK(state <> 'accepted' OR ack_capture_id=capture_id AND raw_sha256 IS NOT NULL AND raw_bytes IS NOT NULL)
);
CREATE INDEX bridge_exceptions_idx ON bridge_delivery(source_name,state,source_received_at DESC)
    WHERE state <> 'accepted';
