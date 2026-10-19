"""Versioned, read-only comparisons over shared windows; no timers or emissions."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import operator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .features import FEATURE_VERSION, _hash
from .feature_windows import WINDOW_VERSION, WindowUnavailable, configuration
from .trajectory import TRAJECTORY_VERSION

RULE_VERSION = 'gmgn-feature-comparison-rule-v1'
QUALITY_POLICY = 'strict_feature_coverage_v1'
UNITS = dict(market_cap_latest='usd', market_cap_min_observed='usd',
             market_cap_max_observed='usd', market_cap_sample_change_pct='percent')
OPERATORS = dict(gt=operator.gt, gte=operator.ge, lt=operator.lt,
                 lte=operator.le, eq=operator.eq, ne=operator.ne)


class RuleWindow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_kind: Literal['fixture', 'gmgn', 'legacy_gmgn']
    chain: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=128)
    window_seconds: int = Field(ge=1, le=86400)
    gap_after_seconds: int | None = Field(default=None, ge=1, le=86400)

    @model_validator(mode='after')
    def supported_window(self):
        self.canonical()
        return self

    def canonical(self):
        return configuration(**self.model_dump())


class ComparisonRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal['gmgn-feature-comparison-rule-v1'] = RULE_VERSION
    window: RuleWindow
    feature: Literal['market_cap_latest', 'market_cap_min_observed',
                     'market_cap_max_observed', 'market_cap_sample_change_pct']
    operator: Literal['gt', 'gte', 'lt', 'lte', 'eq', 'ne']
    threshold: str = Field(min_length=1, max_length=160, pattern=r'^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$')
    unit: Literal['usd', 'percent']
    quality_policy: Literal['strict_feature_coverage_v1'] = QUALITY_POLICY

    @field_validator('threshold')
    @classmethod
    def canonical_decimal(cls, value):
        # Plain finite decimals only. No floats or ambient-context normalization.
        if Decimal(value) == 0:
            return '0'
        return value.rstrip('0').rstrip('.') if '.' in value else value

    @model_validator(mode='after')
    def matching_unit(self):
        if self.unit != UNITS[self.feature]:
            raise ValueError('Unit must match the selected feature')
        return self

    def definition(self):
        value = self.model_dump()
        value['window'] = self.window.canonical()
        value['dependencies'] = dict(feature_version=FEATURE_VERSION,
            trajectory_version=TRAJECTORY_VERSION, features=sorted({self.feature, 'market_cap_latest'}))
        value['required_gates'] = ['fresh_window', 'latest_available', 'feature_available']
        if self.feature != 'market_cap_latest':
            value['required_gates'] += ['no_unavailable_points', 'no_ambiguous_timestamps', 'no_flagged_gaps']
        return value


def evaluate(rule: ComparisonRule, owner, *, now=None):
    """Consume only the owner's validated snapshot, with no SQL, registration or provider I/O."""
    definition = rule.definition()
    window_id = _hash(definition['window'])
    result = dict(version=RULE_VERSION, rule_id=_hash(definition), definition=definition,
        window_id=window_id, state='unknown', reason=None, feature=None, frame=None,
        quality=None, latest_gate=None,
        scope='condition only; no registration, timers, emission or durable evaluation history')

    def finish(state, reason):
        result.update(state=state, reason=reason)
        result['evaluation_id'] = _hash(result)
        return result

    if owner is None:
        return finish('unknown', 'window_owner_unavailable')
    try:
        payload = owner.read(window_id)
    except KeyError:
        return finish('unknown', 'window_not_registered')
    except WindowUnavailable:
        return finish('unknown', 'window_not_ready')

    snapshot = json.loads(payload)
    series = snapshot['result']
    frame = series['features']
    if (snapshot['version'] != WINDOW_VERSION or snapshot['window_id'] != window_id
            or frame['version'] != FEATURE_VERSION or series['version'] != TRAJECTORY_VERSION):
        return finish('unknown', 'incompatible_frame')
    # Check again after decoding: crossing the frame deadline cannot produce a decision.
    if (now if now is not None else datetime.now(timezone.utc)) >= datetime.fromisoformat(snapshot['next_due_at']):
        return finish('unknown', 'window_not_ready')
    result['frame'] = dict(window_id=window_id, generation=snapshot['generation'],
        frame_id=frame['frame_id'], feature_version=frame['version'],
        publication_cutoff=snapshot['publication_cutoff'], next_due_at=snapshot['next_due_at'],
        required_knowledge_at=frame['required_knowledge_at'], dependency_keys=frame['dependency_keys'])
    feature = frame['features'][rule.feature]
    result['feature'] = feature
    result['quality'] = frame['quality']
    latest = frame['features']['market_cap_latest']
    result['latest_gate'] = latest
    if latest['state'] != 'available':
        return finish('unknown', 'latest:' + latest['reason'])
    if feature['state'] != 'available':
        return finish('unknown', 'feature:' + feature['reason'])
    if feature['unit'] != rule.unit:
        return finish('unknown', 'feature_unit_mismatch')
    if rule.feature != 'market_cap_latest':
        for field in ('unavailable_points', 'ambiguous_timestamp_points', 'flagged_gap_count'):
            if frame['quality'][field]:
                return finish('unknown', 'quality:' + field)
    matched = OPERATORS[rule.operator](Decimal(feature['value_text']), Decimal(rule.threshold))
    return finish('true' if matched else 'false', 'comparison_matched' if matched else 'comparison_not_matched')
