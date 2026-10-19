"""Versioned bounded research definitions; no provider calls or execution."""
from __future__ import annotations
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from typing import Literal
from pydantic import Field,StrictInt,StrictStr,field_validator,model_validator
from .contracts import ContractModel,utc_datetime
from .features import _hash
from .trajectory import METHODS

RESEARCH_VERSION='gmgn-research-v1'
LABEL_VERSION='gmgn-observed-progression-v1'
MAX_DATASET_BYTES=8*1024*1024
MAX_MEMBER_BYTES=512*1024
MAX_INPUT_POINTS=20_000


def number(value,*,positive=False):
    if not isinstance(value,str) or len(value)>80:raise ValueError('bounded_decimal_string_required')
    try:d=Decimal(value)
    except InvalidOperation:raise ValueError('invalid_decimal') from None
    if not d.is_finite() or d<0 or (positive and d<=0) or abs(d.adjusted())>30:
        raise ValueError('nonnegative_finite_bounded_decimal_required')
    return d


class ResearchSpec(ContractModel):
    source_kind: Literal['fixture','gmgn','legacy_gmgn']
    chain: Literal['sol']='sol'
    cohort_start: datetime
    cohort_end: datetime
    knowledge_cutoff: datetime
    method: StrictStr
    entry_delay_seconds: StrictInt=Field(ge=0,le=3600)
    horizon_seconds: StrictInt=Field(ge=60,le=86400)
    max_gap_seconds: StrictInt=Field(ge=1,le=3600)
    levels_usd: list[StrictStr]=Field(min_length=1,max_length=50)
    max_members: StrictInt=Field(default=50,ge=1,le=300)
    max_points_per_series: StrictInt=Field(default=500,ge=1,le=2000)

    sampling_seed: StrictStr|None=Field(default=None,min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.-]+$')

    @field_validator('cohort_start','cohort_end','knowledge_cutoff',mode='before')
    @classmethod
    def clock(cls,v):return utc_datetime(v)

    @field_validator('method')
    @classmethod
    def method_valid(cls,v):
        if v not in METHODS:raise ValueError('unsupported_method')
        return v

    @field_validator('levels_usd')
    @classmethod
    def levels_valid(cls,v):
        values=[number(x,positive=True) for x in v]
        if values!=sorted(set(values)):raise ValueError('levels_must_be_unique_and_increasing')
        return v

    @model_validator(mode='after')
    def period(self):
        if not self.cohort_start<self.cohort_end<=self.knowledge_cutoff:
            raise ValueError('cohort_period_must_end_at_or_before_cutoff')
        if self.cohort_end-self.cohort_start>timedelta(days=7):
            raise ValueError('cohort_window_exceeds_seven_days')
        return self

    def definition(self):
        fields=self.model_dump(mode='json')
        if self.sampling_seed is None:fields.pop('sampling_seed')
        result=dict(version=RESEARCH_VERSION,label_version=LABEL_VERSION,**fields,
            universe='all first registrations in the selected period; source-specific observed sample',
            entry_clock='registered_at + entry_delay_seconds',feature_knowledge_mode='as_known_then',
            outcome_knowledge_mode='reconstructed_with_later_data',
            selection='no filtering on outcome or economic completeness',interpolation='none',
            sampling_probability=None,group_policy='token_unique; cross-token creator/campaign groups unknown')

        if self.sampling_seed is not None:
            result.update(selection='deterministic hash sample of full bounded registration frame; no outcome filtering',
                sampling_probability='selected / frame population; archived in sampling_frame')
        from .trench_economics import METHOD,QUALIFICATION
        if self.method==METHOD:
            result.update(economic_qualification=QUALIFICATION,semantic_policy='empirical interpretation learned later',
                feature_knowledge_mode='numeric evidence as_known_then; semantic interpretation retrospective')
        return result

    def key(self):return _hash(self.definition())
