CREATE TABLE analytics_deliveries (
    batch_id text NOT NULL REFERENCES normalization_runs(batch_id),
    destination text NOT NULL CHECK(destination IN ('lake','clickhouse')),
    created_at timestamptz NOT NULL,
    applied_at timestamptz,
    receipt jsonb,
    retry_at timestamptz,
    attempts bigint NOT NULL DEFAULT 0 CHECK(attempts>=0),
    last_error text,
    PRIMARY KEY(batch_id,destination),
    CHECK((applied_at IS NULL) = (receipt IS NULL))
);
CREATE INDEX analytics_destination_pending_idx ON analytics_deliveries
    (destination,retry_at NULLS FIRST,created_at,batch_id) WHERE applied_at IS NULL;
CREATE FUNCTION sync_analytics_deliveries() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE dest text; ack jsonb;
BEGIN
    IF NEW.available_at IS NULL THEN RETURN NEW; END IF;
    FOREACH dest IN ARRAY ARRAY['lake','clickhouse'] LOOP
        ack := NULL;
        IF NEW.analytics_applied_at IS NOT NULL THEN
            IF (NEW.analytics_receipt->>'ok') IS DISTINCT FROM 'true'
               OR (NEW.analytics_receipt->dest->>'ok') IS DISTINCT FROM 'true' THEN
                RAISE EXCEPTION 'Invalid legacy analytics receipt';
            END IF;
            ack := (NEW.analytics_receipt->dest) || jsonb_build_object('batch_id',NEW.batch_id,'destination',dest);
            IF dest='lake' THEN ack:=ack || jsonb_build_object('snapshot',NEW.analytics_receipt->'snapshot'); END IF;
        END IF;
        INSERT INTO analytics_deliveries(batch_id,destination,created_at,applied_at,receipt)
        VALUES(NEW.batch_id,dest,NEW.available_at,NEW.analytics_applied_at,ack)
        ON CONFLICT(batch_id,destination) DO UPDATE SET
            applied_at=COALESCE(analytics_deliveries.applied_at,EXCLUDED.applied_at),
            receipt=COALESCE(analytics_deliveries.receipt,EXCLUDED.receipt),
            retry_at=CASE WHEN EXCLUDED.applied_at IS NOT NULL THEN NULL ELSE analytics_deliveries.retry_at END,
            last_error=CASE WHEN EXCLUDED.applied_at IS NOT NULL THEN NULL ELSE analytics_deliveries.last_error END;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER normalization_delivery_progress
    AFTER INSERT OR UPDATE OF available_at,analytics_applied_at,analytics_receipt ON normalization_runs
    FOR EACH ROW EXECUTE FUNCTION sync_analytics_deliveries();
-- Fire the compatibility initializer without altering publication timestamps.
UPDATE normalization_runs SET available_at=available_at WHERE available_at IS NOT NULL;
