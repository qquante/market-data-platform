CREATE INDEX normalization_available_version_idx
 ON normalization_runs(capture_id,available_at DESC,normalizer_version DESC)
 WHERE available_at IS NOT NULL;
