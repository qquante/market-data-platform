-- Additive: historical all-member jobs retain NULL and byte-identical definitions.
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS sampling_frame jsonb;
