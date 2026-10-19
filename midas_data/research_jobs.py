"""Durable, bounded research jobs over published local evidence. No provider I/O."""
from __future__ import annotations
import asyncio
from datetime import timedelta
import hashlib
import json
from importlib import metadata
from pathlib import Path
import platform

from .cohorts import latest_frame,labels,holder_feature,summarize
from .contracts import utc_datetime
from .features import _hash,_json
from .holder_store import HolderStore
from .holders import PARSER_VERSION
from .research_contracts import ResearchSpec,RESEARCH_VERSION,MAX_DATASET_BYTES,MAX_MEMBER_BYTES,MAX_INPUT_POINTS
from .store import CaptureConflict,FutureCutoff
from .trajectory import read_trajectory,TrajectoryLimit


class ResearchLimit(ValueError):pass
class OwnershipLost(RuntimeError):pass


async def require_owner(con,job):
    row=await con.fetchrow("""SELECT state,attempts,lease_until>clock_timestamp() AS active
        FROM research_jobs WHERE job_id=$1 FOR UPDATE""",job['job_id'])
    if row is None or row['state']!='running' or row['attempts']!=job['attempts'] or not row['active']:
        raise OwnershipLost('research_lease_lost')


def implementation():
    names=['research_contracts.py','research_jobs.py','cohorts.py','trajectory.py','valuation.py',
           'features.py','holder_store.py','holders.py','contracts.py','research_sampling.py','trench_economics.py']
    return {'python':platform.python_version(),'packages':{n:metadata.version(n) for n in ['asyncpg','pydantic']},
            'source_sha256':{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names}}


class ResearchJobs:
    MAX_JOBS=64

    def __init__(self,store):self.store=store

    async def create(self,spec):
        definition=spec.definition();code=implementation()
        key=_hash({'definition':definition,'implementation':code})
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                await con.execute('SELECT pg_advisory_xact_lock(60608008)')
                if spec.knowledge_cutoff>await con.fetchval('SELECT clock_timestamp()'):
                    raise FutureCutoff('future research cutoff')
                old=await con.fetchrow('SELECT job_id,state FROM research_jobs WHERE job_id=$1',key)
                if old:return dict(old,duplicate=True)
                if await con.fetchval('SELECT count(*) FROM research_jobs')>=self.MAX_JOBS:
                    raise ResearchLimit('Research retention budget reached; export and review retained jobs')
                await con.execute('INSERT INTO research_jobs(job_id,definition,implementation) VALUES($1,$2::jsonb,$3::jsonb)',
                                  key,_json(definition).decode(),_json(code).decode())
        return {'job_id':key,'state':'queued','duplicate':False}

    async def membership(self,job,spec):
        if job['member_count'] is not None:return
        from .research_sampling import MAX_FRAME,sample
        budget=MAX_FRAME if spec.sampling_seed is not None else spec.max_members
        members=await self.store._temporal_fetch(spec.knowledge_cutoff,"""SELECT t.registry_id,t.token_address,
            t.chain,t.source_kind,t.registered_at,t.capture_id,t.normalizer_version,t.item_pointer,c.raw_sha256
            FROM token_registry t JOIN captures c USING(capture_id)
            WHERE t.source_kind=$1 AND t.chain=$2 AND t.registered_at>=$3 AND t.registered_at<$4
                AND t.registered_at<=$5 ORDER BY t.registry_id LIMIT $6""",
            spec.source_kind,spec.chain,spec.cohort_start,spec.cohort_end,spec.knowledge_cutoff,budget+1)
        if len(members)>budget:raise ResearchLimit('cohort_member_budget; reduce registration period')
        frame=None
        if spec.sampling_seed is not None:members,frame=sample(members,spec.sampling_seed,spec.max_members)
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                await require_owner(con,job)
                for r in members:
                    await con.execute("""INSERT INTO research_members(job_id,registry_id,membership)
                        VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING""",job['job_id'],r['registry_id'],_json(dict(r)).decode())
                await con.execute('UPDATE research_jobs SET member_count=$2,sampling_frame=$3::jsonb WHERE job_id=$1',
                    job['job_id'],len(members),_json(frame).decode() if frame is not None else None)

    async def series(self,spec,token,start,end,cutoff):
        if start>=end:return {'points':[],'query_complete':True}
        return await read_trajectory(self.store,source_kind=spec.source_kind,chain=spec.chain,token=token,
            method=spec.method,start=start,end=end,cutoff=cutoff,max_points=spec.max_points_per_series,
            gap_after_seconds=spec.max_gap_seconds)

    async def member(self,spec,registration):
        entry_at=utc_datetime(registration['registered_at'])+timedelta(seconds=spec.entry_delay_seconds)
        exit_at=entry_at+timedelta(seconds=spec.horizon_seconds)
        token=registration['token_address'];cutoff=spec.knowledge_cutoff
        gap=timedelta(seconds=spec.max_gap_seconds);epsilon=timedelta(microseconds=1)
        empty={'points':[],'query_complete':True}
        entry_series=await self.series(spec,token,entry_at-gap,entry_at+epsilon,entry_at) if entry_at<=cutoff else empty
        exit_series=await self.series(spec,token,exit_at-gap,exit_at+epsilon,exit_at) if exit_at<=cutoff else empty
        outcomes=await self.series(spec,token,entry_at,min(exit_at,cutoff)+epsilon,cutoff) if entry_at<=cutoff else empty
        snapshot=None
        if entry_at<=cutoff:
            runs=await self.store._temporal_fetch(entry_at,"""SELECT h.capture_id FROM holder_runs h
                JOIN normalization_runs n ON n.capture_id=h.capture_id AND n.normalizer_version=h.parser_version
                WHERE h.source_kind=$1 AND h.chain=$2 AND h.token_address=$3 AND h.parser_version=$4
                  AND n.available_at<=$5 AND h.occurred_at>=$6 AND h.occurred_at<=$5
                ORDER BY h.occurred_at DESC,n.available_at DESC,h.run_seq DESC LIMIT 1""",
                spec.source_kind,spec.chain,token,PARSER_VERSION,entry_at,entry_at-timedelta(seconds=3600))
            if runs:snapshot=await HolderStore(self.store).snapshot(runs[0]['capture_id'],spec.source_kind,entry_at)
        points=len(entry_series['points'])+len(exit_series['points'])+len(outcomes['points'])
        result=dict(registration,entry_at=entry_at,exit_at=exit_at,
            entry=latest_frame(entry_series,entry_at,spec.max_gap_seconds),
            exit=latest_frame(exit_series,exit_at,spec.max_gap_seconds),
            holder=holder_feature(snapshot,entry_at,3600),
            outcome=labels(outcomes['points'],start=entry_at,end=exit_at,cutoff=cutoff,
                           gap_seconds=spec.max_gap_seconds,levels=spec.levels_usd),
            evidence={'entry_series':entry_series,'exit_series':exit_series,'outcome_series':outcomes,
                      'holder_snapshot':snapshot},input_points=points)
        return result

    async def build(self,job):
        definition=json.loads(job['definition'])
        spec=ResearchSpec.model_validate({k:v for k,v in definition.items() if k in ResearchSpec.model_fields})
        if json.loads(job['implementation'])!=implementation():
            raise ResearchLimit('implementation_changed; create a new versioned job')
        await self.membership(job,spec)
        rows=await self.store.pool.fetch('SELECT * FROM research_members WHERE job_id=$1 ORDER BY registry_id',job['job_id'])
        members=[];total_bytes=0;total_points=0
        for row in rows:
            if row['payload'] is not None:
                payload=json.loads(row['payload'])
                if _hash(payload)!=row['payload_sha256']:raise CaptureConflict('Research member hash mismatch')
            else:
                payload=await self.member(spec,json.loads(row['membership']))
                payload=json.loads(_json(payload))
            size=len(_json(payload));total_bytes+=size;total_points+=payload['input_points']
            if size>MAX_MEMBER_BYTES or total_bytes>MAX_DATASET_BYTES or total_points>MAX_INPUT_POINTS:
                raise ResearchLimit('research_input_or_output_budget; reduce the cohort/window')
            if row['payload'] is None:
                async with self.store.pool.acquire() as con:
                    async with con.transaction():
                        await require_owner(con,job)
                        await con.execute("""UPDATE research_members SET payload=$3::jsonb,payload_sha256=$4
                            WHERE job_id=$1 AND registry_id=$2 AND payload IS NULL""",
                            job['job_id'],row['registry_id'],_json(payload).decode(),_hash(payload))
                        await con.execute("""UPDATE research_jobs SET completed_count=$2,input_points=$3,
                            output_bytes=$4,updated_at=clock_timestamp() WHERE job_id=$1""",
                            job['job_id'],len(members)+1,total_points,total_bytes)
            members.append(payload)
        summary=summarize(members,spec.levels_usd)
        bundle={'version':RESEARCH_VERSION,'job_id':job['job_id'],'definition':definition,
                'implementation':json.loads(job['implementation']),'members':members,'summary':summary}
        if spec.sampling_seed is not None:
            frame=await self.store.pool.fetchval('SELECT sampling_frame FROM research_jobs WHERE job_id=$1',job['job_id'])
            bundle['sampling_frame']=json.loads(frame)
            from .research_sampling import verify
            verify(bundle['sampling_frame'],members,spec.sampling_seed,spec.max_members)
        body=_json(bundle)
        if len(body)>MAX_DATASET_BYTES:raise ResearchLimit('bundle_byte_budget')
        await self.store.pool.execute("""UPDATE research_jobs SET state='ready',summary=$2::jsonb,
            bundle=$3::jsonb,bundle_sha256=$4,updated_at=clock_timestamp(),lease_until=NULL,last_error=NULL
            WHERE job_id=$1 AND state='running' AND attempts=$5 AND lease_until>clock_timestamp()""",
            job['job_id'],_json(summary).decode(),body.decode(),hashlib.sha256(body).hexdigest(),job['attempts'])

    async def step(self):
        # Durable lease survives process loss; partially published members are replayed by hash.
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                job=await con.fetchrow("""UPDATE research_jobs SET state='running',attempts=attempts+1,
                    lease_until=clock_timestamp()+interval '150 seconds',updated_at=clock_timestamp()
                    WHERE job_id=(SELECT job_id FROM research_jobs WHERE state='queued'
                       OR (state='running' AND lease_until<clock_timestamp())
                       ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *""")
        if job is None:
            from .experiment_store import ExperimentStore
            return await ExperimentStore(self.store).step()
        try:await asyncio.wait_for(self.build(job),timeout=120)
        except asyncio.CancelledError:raise
        except OwnershipLost:return 0
        except Exception as exc:
            # Preserve all attempts and partial evidence; no winner-only retry/discard.
            detail=str(exc) if isinstance(exc,(ResearchLimit,TrajectoryLimit)) else type(exc).__name__
            await self.store.pool.execute("""UPDATE research_jobs SET state='failed',last_error=$2,
                lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=$1 AND state='running' AND attempts=$3""",
                job['job_id'],detail[:300],job['attempts'])
        return 1

    async def status(self,job_id=None):
        rows=await self.store.pool.fetch("""SELECT job_id,state,created_at,updated_at,attempts,
            member_count,completed_count,input_points,output_bytes,last_error,summary,bundle_sha256,definition
            FROM research_jobs WHERE ($1::text IS NULL OR job_id=$1) ORDER BY created_at DESC,job_id LIMIT 64""",job_id)
        return [{**dict(r),'summary':json.loads(r['summary']) if r['summary'] else None,
                 'definition':json.loads(r['definition'])} for r in rows]

    async def bundle(self,job_id):
        row=await self.store.pool.fetchrow("SELECT bundle,bundle_sha256 FROM research_jobs WHERE job_id=$1 AND state='ready'",job_id)
        if row is None:return None
        body=_json(json.loads(row['bundle']))
        if hashlib.sha256(body).hexdigest()!=row['bundle_sha256']:raise CaptureConflict('Research bundle hash mismatch')
        return body,row['bundle_sha256']
