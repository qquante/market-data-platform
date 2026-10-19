"""Bounded token_info price frames on the existing paper owner and source facts."""
import asyncio
from datetime import datetime,timedelta,timezone
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from .features import _hash,_json
from .trajectory import read_trajectory,TrajectoryLimit
from .valuation import VALUATION_VERSION

TRACKED_PAPER_VERSION='gmgn-paper-timed-lot-v2'
PRICE_VERSION='gmgn-paper-token-info-price-v1'
FRAME_VERSION='gmgn-paper-price-frame-v1'
REFRESH_SECONDS=30
HISTORY_SECONDS=720
METHOD='gmgn.token_info.price_times_circulating_supply.v1'


def source_definition():
    return dict(version=PRICE_VERSION,endpoint='token_info',field='price/price',unit='usd_per_token',
        valuation_version=VALUATION_VERSION,selection='latest published token observation; no fallback on invalid latest',
        quality='homologated positive price; supply and market cap are not required',
        time_basis='capture_received_at; available_at must be known at publication cutoff',
        frame_seconds=REFRESH_SECONDS,history_seconds=HISTORY_SECONDS,max_points=50,
        minimum_entry_wait_seconds=720,interpolation='none',fallback='none')


class PriceSourceChange(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version: Literal['gmgn-paper-token-info-price-v1']=PRICE_VERSION
    expected_policy_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    reason: str=Field(min_length=1,max_length=240)


def price_result(series):
    points=[]
    fields=('point_id','capture_id','observed_at','capture_recorded_at','available_at','normalizer_version',
        'interpretation_sha256','raw_sha256','evidence_kind','anchor_fact_id','item_pointer','same_timestamp_count')
    for original in series['points']:
        price=(original.get('valuation_context') or {}).get('price') or {}
        point={k:original[k] for k in fields}
        point.update(price=price,state=price.get('state','unavailable'),
            reason=price.get('reason') if price else original.get('reason'),value_text=price.get('value_text'),unit='usd_per_token')
        points.append(point)
    return dict(version=PRICE_VERSION,source_kind=series['source_kind'],chain=series['chain'],
        token_address=series['token_address'],endpoint='token_info',points=points,
        coverage=dict(gap_after_seconds=HISTORY_SECONDS),knowledge_cutoff=series['knowledge_cutoff'])


class PaperPrices:
    def __init__(self,paper):
        self.paper=paper;self.frames={};self.reads=0

    def clear(self):self.frames.clear()

    @staticmethod
    def key(identity):return tuple(identity[k] for k in ('source_kind','chain','token_address'))

    def prune(self,identities):
        keys={self.key(i) for i in identities}
        self.frames={k:v for k,v in self.frames.items() if k in keys}

    async def read(self,identity,now):
        key=self.key(identity);old=self.frames.get(key)
        if old and now<old['next_due_at']:return old
        payload=None;error=None;generation=(old or {}).get('generation',0)+1
        due=now+timedelta(seconds=REFRESH_SECONDS)
        try:
            async with asyncio.timeout(5):
                con=self.paper.connection()
                async with con.transaction():
                    await con.execute('SELECT pg_advisory_xact_lock_shared(60608003)')
                    cutoff=await con.fetchval('SELECT clock_timestamp()')
                self.reads+=1
                series=await read_trajectory(self.paper.owner.store,source_kind=key[0],chain=key[1],token=key[2],
                    method=METHOD,start=cutoff-timedelta(seconds=HISTORY_SECONDS),end=cutoff,cutoff=cutoff,
                    max_points=50,gap_after_seconds=HISTORY_SECONDS,include_context=True)
                due=cutoff+timedelta(seconds=REFRESH_SECONDS)
                payload=_json(dict(version=FRAME_VERSION,source_policy_id=_hash(source_definition()),
                    generation=generation,publication_cutoff=cutoff,next_due_at=due,result=price_result(series)))
                if len(payload)>1024**2:raise TrajectoryLimit('Paper price frame exceeds 1 MiB')
        except Exception as exc:
            payload=None;error=type(exc).__name__
        entry=dict(payload=payload,next_due_at=due,generation=generation,error=error)
        self.frames[key]=entry
        return entry
