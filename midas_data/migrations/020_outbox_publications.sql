-- Additive receipt table; no rewrite or backfill of capture/fact history.
SET LOCAL lock_timeout = '2s';
CREATE TABLE outbox_publications (
    capture_id uuid PRIMARY KEY REFERENCES outbox(capture_id),
    stream text NOT NULL CHECK (stream = 'MIDAS_DATA'),
    sequence bigint NOT NULL CHECK (sequence > 0),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
