"""Retains all bounded experiment attempts and their reproducible results."""
import asyncio,hashlib,json
from pathlib import Path
from .experiments import ExperimentSpec,compare
from .features import _hash,_json
from .research_jobs import ResearchJobs,ResearchLimit
from .store import CaptureConflict

MAX_RESULT_BYTES=4*1024*1024


def implementation():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
            for n in ['experiments.py','studies.py','cohorts.py','research_contracts.py','experiment_store.py']}


class ExperimentStore:
    def __init__(self,store):self.store=store

    async def create(self,job_id,spec):
        row=await self.store.pool.fetchrow("SELECT bundle_sha256 FROM research_jobs WHERE job_id=$1 AND state='ready'",job_id)
        if row is None:raise ResearchLimit('dataset_not_ready')
        code=implementation();definition=spec.model_dump(mode='json')
        key=_hash({'dataset':row['bundle_sha256'],'definition':definition,'implementation':code})
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                await con.execute('SELECT pg_advisory_xact_lock(60608008)')
                old=await con.fetchrow('SELECT experiment_id,state FROM research_experiments WHERE experiment_id=$1',key)
                if old:return dict(old,duplicate=True)
                if await con.fetchval('SELECT count(*) FROM research_experiments')>=128:
                    raise ResearchLimit('experiment_retention_budget')
                await con.execute("""INSERT INTO research_experiments(experiment_id,job_id,dataset_sha256,definition,implementation)
                    VALUES($1,$2,$3,$4::jsonb,$5::jsonb)""",key,job_id,row['bundle_sha256'],_json(definition).decode(),_json(code).decode())
        return {'experiment_id':key,'state':'queued','duplicate':False}

    async def step(self):
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                row=await con.fetchrow("""UPDATE research_experiments SET state='running',attempts=attempts+1,
                    lease_until=clock_timestamp()+interval '150 seconds',updated_at=clock_timestamp()
                    WHERE experiment_id=(SELECT experiment_id FROM research_experiments WHERE state='queued'
                        OR(state='running' AND lease_until<clock_timestamp())
                        ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *""")
        if row is None:return 0
        try:
            if json.loads(row['implementation'])!=implementation():raise ValueError('experiment_implementation_changed')
            archived=await ResearchJobs(self.store).bundle(row['job_id'])
            if archived is None or archived[1]!=row['dataset_sha256']:raise CaptureConflict('dataset_identity_changed')
            spec=ExperimentSpec.model_validate_json(row['definition'])
            result=await asyncio.to_thread(compare,json.loads(archived[0]),spec)
            body=_json(result)
            if len(body)>MAX_RESULT_BYTES:raise ResearchLimit('experiment_result_budget')
            await self.store.pool.execute("""UPDATE research_experiments SET state='ready',result=$2::jsonb,
                result_sha256=$3,lease_until=NULL,updated_at=clock_timestamp(),last_error=NULL
                WHERE experiment_id=$1 AND state='running' AND attempts=$4 AND lease_until>clock_timestamp()""",
                row['experiment_id'],body.decode(),hashlib.sha256(body).hexdigest(),row['attempts'])
        except asyncio.CancelledError:raise
        except Exception as exc:
            await self.store.pool.execute("""UPDATE research_experiments SET state='failed',last_error=$2,
                lease_until=NULL,updated_at=clock_timestamp() WHERE experiment_id=$1 AND state='running' AND attempts=$3""",
                row['experiment_id'],type(exc).__name__,row['attempts'])
        return 1

    async def status(self,experiment_id=None):
        rows=await self.store.pool.fetch("""SELECT experiment_id,job_id,dataset_sha256,state,attempts,created_at,
            updated_at,last_error,result_sha256,definition FROM research_experiments
            WHERE ($1::text IS NULL OR experiment_id=$1) ORDER BY created_at DESC,experiment_id LIMIT 128""",experiment_id)
        return [{**dict(r),'definition':json.loads(r['definition'])} for r in rows]

    async def result(self,key):
        row=await self.store.pool.fetchrow("""SELECT result,result_sha256 FROM research_experiments
            WHERE experiment_id=$1 AND state='ready'""",key)
        if row is None:return None
        body=_json(json.loads(row['result']))
        if hashlib.sha256(body).hexdigest()!=row['result_sha256']:raise CaptureConflict('experiment_result_hash_mismatch')
        return body,row['result_sha256']
