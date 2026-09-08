CREATE INDEX captures_source_recorded_idx ON captures(source_kind,recorded_at DESC,capture_id DESC);
CREATE INDEX captures_error_idx ON captures(capture_id) WHERE last_error IS NOT NULL;
CREATE INDEX normalization_analytics_pending_idx ON normalization_runs(recorded_at,batch_id)
 WHERE available_at IS NOT NULL AND analytics_applied_at IS NULL;
CREATE INDEX facts_as_known_idx ON facts(source_kind,chain,token_address,metric,unit,contract_version,
 occurred_at DESC,available_at DESC,seq DESC) WHERE available_at IS NOT NULL;
CREATE INDEX facts_observations_idx ON facts(source_kind,chain,token_address,metric,occurred_at,seq);
