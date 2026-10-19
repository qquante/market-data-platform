"""Research uses private SQL fixtures and archived quote evidence only."""
import asyncio,json,hashlib
from datetime import datetime,timedelta,timezone
import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.token_registry import TokenRegistry
from midas_data.research_contracts import ResearchSpec
from midas_data.research_jobs import ResearchJobs
from midas_data.features import _json
from test_token_registry import database,integration
from uuid import uuid4

pytestmark=[pytest.mark.integration,integration]


async def seed(store,address,price=True):
    now=datetime.now(timezone.utc)
    m=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='sol',
        requested_at=now-timedelta(seconds=2),received_at=now-timedelta(seconds=1),http_status=200,
        evidence_kind='original_response',request_params={'address':address})
    raw=json.dumps({'code':0,'data':{'address':address,'name':'Fixture',
        **({'price':{'price':'2'},'circulating_supply':'10'} if price else {})}}).encode()
    await store.accept_capture(m,raw);await store.record_normalization(m.capture_id,normalize_capture(m,raw))
    await TokenRegistry(store).step()


def spec(start,end,**changes):
    return ResearchSpec(**dict(dict(source_kind='fixture',cohort_start=start,cohort_end=end,knowledge_cutoff=end,
        method='gmgn.token_info.price_times_circulating_supply.v1',entry_delay_seconds=0,horizon_seconds=60,
        max_gap_seconds=120,levels_usd=['100']),**changes))


def test_job_freezes_all_registered_members_and_reproduces_export_after_late_input(tmp_path):
    async def run():
        import httpx
        from midas_data.api import make_app
        async with database(tmp_path) as (store,_,settings):
            start=await store.pool.fetchval('SELECT clock_timestamp()')
            await seed(store,'Available');await seed(store,'Unknown',False)
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            jobs=ResearchJobs(store);created=await jobs.create(spec(start,cutoff));key=created['job_id']
            assert (await jobs.create(spec(start,cutoff)))['duplicate']
            assert await jobs.step()==1
            status=(await jobs.status(key))[0]
            assert status['state']=='ready',status
            body,digest=await jobs.bundle(key);bundle=json.loads(body)
            assert bundle['summary']['population']==2
            assert bundle['summary']['entry_available']==1 and bundle['summary']['entry_unknown']==1
            assert all(m['outcome']['labels'][0]['state']=='unknown' for m in bundle['members'])
            assert hashlib.sha256(body).hexdigest()==digest
            await seed(store,'AfterCutoff')
            assert await ResearchJobs(store).bundle(key)==(body,digest)
            assert await jobs.step()==0
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture',
                    headers={'Authorization':'Bearer '+settings.api_key}) as c:
                r=await c.get('/v1/research/jobs/'+key+'/bundle')
                assert r.status_code==200 and r.content==body and r.headers['x-content-sha256']==digest
    asyncio.run(run())


def test_member_budget_fails_whole_population_without_silent_truncation(tmp_path):
    async def run():
        async with database(tmp_path) as (store,_,_settings):
            start=await store.pool.fetchval('SELECT clock_timestamp()')
            await seed(store,'One');await seed(store,'Two')
            end=await store.pool.fetchval('SELECT clock_timestamp()')
            jobs=ResearchJobs(store);job=await jobs.create(spec(start,end,max_members=1));await jobs.step()
            s=(await jobs.status(job['job_id']))[0]
            assert s['state']=='failed' and 'cohort_member_budget' in s['last_error']
            assert await jobs.bundle(job['job_id']) is None
            assert await store.pool.fetchval('SELECT count(*) FROM research_members')==0
    asyncio.run(run())


def test_interruption_resumes_checkpoint_without_recomputing_published_member(tmp_path):
    async def run():
        from unittest.mock import AsyncMock
        async with database(tmp_path) as (store,_,_settings):
            start=await store.pool.fetchval('SELECT clock_timestamp()')
            await seed(store,'ResumeOne');await seed(store,'ResumeTwo')
            end=await store.pool.fetchval('SELECT clock_timestamp()')
            jobs=ResearchJobs(store);created=await jobs.create(spec(start,end));key=created['job_id']
            original=jobs.member;seen=[]
            async def interrupted(definition,registration):
                seen.append(registration['registry_id'])
                if len(seen)==2:raise asyncio.CancelledError()
                return await original(definition,registration)
            jobs.member=interrupted
            with pytest.raises(asyncio.CancelledError):await jobs.step()
            status=(await jobs.status(key))[0]
            assert status['state']=='running' and status['completed_count']==1
            checkpoint=await store.pool.fetchrow("SELECT registry_id,payload_sha256 FROM research_members WHERE payload IS NOT NULL")
            # A second owner must wait for the persistent lease, even after process loss.
            assert await ResearchJobs(store).step()==0
            await store.pool.execute("UPDATE research_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=$1",key)
            resumed=ResearchJobs(store);resumed.member=AsyncMock(wraps=resumed.member)
            assert await resumed.step()==1
            assert resumed.member.await_count==1
            assert resumed.member.call_args.args[1]['registry_id']!=checkpoint['registry_id']
            final=(await resumed.status(key))[0]
            assert final['state']=='ready' and final['attempts']==2 and final['completed_count']==2
            assert await store.pool.fetchval("SELECT payload_sha256 FROM research_members WHERE registry_id=$1",checkpoint['registry_id'])==checkpoint['payload_sha256']
    asyncio.run(run())


def test_concurrent_retention_admission_keeps_all_existing_jobs(tmp_path):
    async def run():
        from midas_data.research_jobs import ResearchLimit
        async with database(tmp_path) as (store,_,_settings):
            end=await store.pool.fetchval('SELECT clock_timestamp()');start=end-timedelta(hours=1)
            jobs=ResearchJobs(store)
            # Retained failures count too; no automatic pruning to favor successful trials.
            for i in range(63):
                await store.pool.execute("INSERT INTO research_jobs(job_id,definition,implementation,state) VALUES($1,'{}','{}','failed')",format(i,'064x'))
            specs=[spec(start,end,levels_usd=[str(i+100)]) for i in range(8)]
            outcomes=await asyncio.gather(*(jobs.create(s) for s in specs),return_exceptions=True)
            assert sum(isinstance(x,dict) for x in outcomes)==1
            assert sum(isinstance(x,ResearchLimit) for x in outcomes)==7
            assert await store.pool.fetchval('SELECT count(*) FROM research_jobs')==64
            assert await store.pool.fetchval("SELECT count(*) FROM research_jobs WHERE state='failed'")==63
            admitted=next(x for x in outcomes if isinstance(x,dict))
            chosen=specs[outcomes.index(admitted)]
            assert (await jobs.create(chosen))['duplicate']
    asyncio.run(run())


@pytest.mark.parametrize('late_failure',[False,True])
def test_expired_owner_cannot_overwrite_a_new_owner_result(tmp_path,late_failure):
    async def run():
        async with database(tmp_path) as (store,_,_settings):
            start=await store.pool.fetchval('SELECT clock_timestamp()');await seed(store,'OwnerRace')
            end=await store.pool.fetchval('SELECT clock_timestamp()');old=ResearchJobs(store)
            key=(await old.create(spec(start,end)))['job_id'];original=old.member;published=[]
            async def interleave(definition,registration):
                payload=await original(definition,registration)
                await store.pool.execute("UPDATE research_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=$1",key)
                assert await ResearchJobs(store).step()==1
                published.append(await ResearchJobs(store).bundle(key))
                if late_failure:raise RuntimeError('late obsolete owner')
                return payload
            old.member=interleave
            await old.step()
            status=(await old.status(key))[0]
            assert status['state']=='ready' and status['attempts']==2
            assert await old.bundle(key)==published[0]
            assert status['last_error'] is None
    asyncio.run(run())


def test_sampled_job_freezes_population_and_replays_after_late_registration(tmp_path):
    async def run():
        from midas_data.experiments import verify_bundle
        async with database(tmp_path) as (store,_,settings):
            start=await store.pool.fetchval('SELECT clock_timestamp()')
            for i in range(8):await seed(store,'Sample'+str(i),i%2==0)
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            jobs=ResearchJobs(store);created=await jobs.create(spec(start,cutoff,max_members=3,sampling_seed='fixture-frozen-v1'))
            await jobs.step();status=(await jobs.status(created['job_id']))[0]
            assert status['state']=='ready',status
            body,digest=await jobs.bundle(created['job_id']);bundle=json.loads(body)
            assert len(bundle['members'])==3 and bundle['sampling_frame']['population']==8
            verify_bundle(bundle)
            await seed(store,'Later')
            assert await jobs.bundle(created['job_id'])==(body,digest)
    asyncio.run(run())
