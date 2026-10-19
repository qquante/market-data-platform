-- Explicit additive source amendment; original position policy and financial history stay intact.
ALTER TABLE paper_events DROP CONSTRAINT paper_events_kind_check;
ALTER TABLE paper_events ADD CONSTRAINT paper_events_kind_check
    CHECK(kind IN ('requested','waiting','opened','closed','entry_expired','price_source_changed'));
