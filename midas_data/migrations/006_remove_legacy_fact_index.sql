-- Public temporal queries always filter source_kind and use the newer indexes
-- facts_observations_idx / facts_as_known_idx. Keep uniqueness and batch indexes.
-- Removing this legacy index avoids maintaining it on both COPY and publication.
DROP INDEX facts_token_time_idx;
