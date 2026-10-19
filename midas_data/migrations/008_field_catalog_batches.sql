-- New captures use a TOAST-compressed packet; existing field rows stay intact.
CREATE TABLE field_catalog_batches (
    capture_id uuid NOT NULL,
    normalizer_version text NOT NULL,
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'array' AND jsonb_array_length(fields) <= 40000),
    PRIMARY KEY (capture_id, normalizer_version),
    FOREIGN KEY (capture_id, normalizer_version) REFERENCES normalization_runs(capture_id, normalizer_version)
);
