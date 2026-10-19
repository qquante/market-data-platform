import asyncio,hashlib,json
import pytest
from midas_data.features import _json
from midas_data.experiment_store import ExperimentStore
from midas_data.research_jobs import ResearchJobs
from midas_data.experiments import compare
from test_experiments import bundle,definitions
from test_token_registry import database,integration

pytestmark=[pytest.mark.integration,integration]


def test_all_trials_are_durable_including_failure_and_results_reproduce(tmp_path):
    async def run():
        async with database(tmp_path) as (store,_,_settings):
            b=bundle();body=_json(b);key=b['job_id'];digest=hashlib.sha256(body).hexdigest()
            await store.pool.execute("""INSERT INTO research_jobs(job_id,definition,implementation,state,
                bundle,bundle_sha256,summary,member_count,completed_count)
                VALUES($1,$2::jsonb,$3::jsonb,'ready',$4::jsonb,$5,$6::jsonb,1,1)""",
                key,_json(b['definition']).decode(),_json(b['implementation']).decode(),body.decode(),digest,_json(b['summary']).decode())
            _,spec=definitions();owner=ExperimentStore(store)
            good=await owner.create(key,spec)
            assert (await owner.create(key,spec))['duplicate']
            bad=await owner.create(key,spec.model_copy(update={'embargo_seconds':0}))
            assert await ResearchJobs(store).step()==1
            assert await ResearchJobs(store).step()==1
            states={r['experiment_id']:r['state'] for r in await owner.status()}
            assert states=={good['experiment_id']:'ready',bad['experiment_id']:'failed'}
            result,result_hash=await ExperimentStore(store).result(good['experiment_id'])
            assert result==_json(compare(b,spec))
            assert hashlib.sha256(result).hexdigest()==result_hash
            assert await owner.result(bad['experiment_id']) is None
            assert await owner.step()==0
            assert await store.pool.fetchval('SELECT count(*) FROM research_experiments')==2
    asyncio.run(run())


def test_retention_is_atomic_under_concurrent_experiment_requests(tmp_path):
    async def run():
        from midas_data.research_jobs import ResearchLimit
        async with database(tmp_path) as (store,_,_settings):
            b=bundle();body=_json(b);key=b['job_id'];digest=hashlib.sha256(body).hexdigest()
            await store.pool.execute("""INSERT INTO research_jobs(job_id,definition,implementation,state,bundle,bundle_sha256)
                VALUES($1,$2::jsonb,$3::jsonb,'ready',$4::jsonb,$5)""",
                key,_json(b['definition']).decode(),_json(b['implementation']).decode(),body.decode(),digest)
            for i in range(127):
                await store.pool.execute("""INSERT INTO research_experiments(experiment_id,job_id,dataset_sha256,definition,implementation,state)
                    VALUES($1,$2,$3,'{}','{}','failed')""",format(i,'064x'),key,digest)
            _,spec=definitions();owner=ExperimentStore(store)
            variants=[spec.model_copy(update={'network_usd_per_side':str(i)}) for i in range(8)]
            results=await asyncio.gather(*(owner.create(key,s) for s in variants),return_exceptions=True)
            assert sum(isinstance(r,dict) for r in results)==1
            assert sum(isinstance(r,ResearchLimit) for r in results)==7
            assert await store.pool.fetchval('SELECT count(*) FROM research_experiments')==128
            assert await store.pool.fetchval("SELECT count(*) FROM research_experiments WHERE state='failed'")==127
    asyncio.run(run())


def test_all_four_studies_and_twenty_strategies_export_through_authenticated_api(tmp_path):
    async def run():
        import httpx
        from midas_data.api import make_app
        from research_capacity import dataset,configuration
        async with database(tmp_path) as (store,_,settings):
            b=dataset(100);spec=configuration(20);body=_json(b);key=b['job_id']
            await store.pool.execute("""INSERT INTO research_jobs(job_id,definition,implementation,state,bundle,bundle_sha256)
                VALUES($1,$2::jsonb,$3::jsonb,'ready',$4::jsonb,$5)""",
                key,_json(b['definition']).decode(),_json(b['implementation']).decode(),body.decode(),hashlib.sha256(body).hexdigest())
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture',
                    headers={'Authorization':'Bearer '+settings.api_key}) as c:
                created=await c.post('/v1/research/jobs/'+key+'/experiments',content=_json(spec.model_dump(mode='json')))
                assert created.status_code==202,created.text
                experiment_id=created.json()['experiment_id']
                assert await ResearchJobs(store).step()==1
                result=await c.get('/v1/research/experiments/'+experiment_id+'/result')
                assert result.status_code==200,result.text
                assert result.content==_json(compare(b,spec))
                assert result.headers['x-content-sha256']==hashlib.sha256(result.content).hexdigest()
                assert result.json()['studies']['trials']==4
                assert len(result.json()['results'])==20
    asyncio.run(run())
