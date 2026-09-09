ALTER TABLE normalization_runs
    ADD COLUMN analytics_retry_at timestamptz,
    ADD COLUMN analytics_attempts bigint NOT NULL DEFAULT 0 CHECK (analytics_attempts >= 0),
    ADD COLUMN analytics_last_error text;
CREATE INDEX normalization_analytics_retry_pending_idx
    ON normalization_runs(analytics_retry_at NULLS FIRST,recorded_at,batch_id)
    WHERE available_at IS NOT NULL AND analytics_applied_at IS NULL;
