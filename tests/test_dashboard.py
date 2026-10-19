import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
from midas_data.api import make_app
from midas_data.settings import Settings
from midas_data.resources import resource_snapshot


def test_resource_measurements_keep_absence_unknown_and_use_cgroup_scope(tmp_path):
    missing=resource_snapshot(tmp_path)
    assert not missing['available'] and missing['memory_bytes'] is None
    (tmp_path/'memory.current').write_text('1024')
    (tmp_path/'memory.max').write_text('4096')
    (tmp_path/'cpu.max').write_text('50000 100000')
    (tmp_path/'cpu.stat').write_text('usage_usec 123456\nuser_usec 1000\nsystem_usec 122456\n')
    values=resource_snapshot(tmp_path)
    assert values['memory_bytes']==1024 and values['memory_limit_bytes']==4096
    assert values['cpu_limit_cores']==.5 and values['cpu_usage_usec']==123456
    (tmp_path/'memory.max').write_text('max')
    assert resource_snapshot(tmp_path)['memory_limit_bytes'] is None


def test_public_shell_contains_no_secret_and_data_remains_protected():
    async def run():
        settings=Settings(database_dsn='fixture',lake_catalog_dsn='fixture',api_key='fixture-ui-contract-secret',
            nats_url='fixture',clickhouse_url='fixture',clickhouse_user='fixture',clickhouse_password='fixture',
            raw_path=Path('/tmp/unused-fixture'),lake_path='/tmp/unused-fixture');app=make_app(settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as c:
            for path in ['/','/ui/app.js','/ui/app.css','/ui/config']:
                response=await c.get(path)
                assert response.status_code==200
                assert response.headers['cache-control']=='no-store'
                assert response.headers['x-content-type-options']=='nosniff'
                assert "frame-ancestors 'none'" in response.headers['content-security-policy']
                assert settings.api_key not in response.text
            assert (await c.get('/ui/config')).json()=={'public_read':False}
            for path in ['/v1/status','/v1/holders','/v1/research/jobs','/v1/research/experiments','/docs','/openapi.json']:
                assert (await c.get(path)).status_code==401
    asyncio.run(run())


def test_research_definitions_reject_oversize_before_decoding_or_database_access():
    async def run():
        settings=Settings(database_dsn='fixture',lake_catalog_dsn='fixture',api_key='fixture-limit',
            nats_url='fixture',clickhouse_url='fixture',clickhouse_user='fixture',clickhouse_password='fixture',
            raw_path=Path('/tmp/unused-fixture'),lake_path='/tmp/unused-fixture')
        app=make_app(settings)
        async def chunks():
            for _ in range(17):yield b' '*1024
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture',
                headers={'Authorization':'Bearer '+settings.api_key}) as c:
            for path in ['/v1/research/jobs','/v1/research/jobs/fixture/experiments']:
                r=await c.post(path,content=chunks())
                assert r.status_code==413
                r=await c.post(path,content='{"not_a_field":"secret-to-omit"}')
                assert r.status_code==422 and 'secret-to-omit' not in r.text
    asyncio.run(run())


def test_local_dashboard_anonymous_reads_are_opt_in_and_writes_stay_authenticated():
    async def run():
        settings=Settings(database_dsn='fixture',lake_catalog_dsn='fixture',api_key='fixture-local-secret',
            nats_url='fixture',clickhouse_url='fixture',clickhouse_user='fixture',clickhouse_password='fixture',
            raw_path=Path('/tmp/unused-fixture'),lake_path='/tmp/unused-fixture',dashboard_public_read=True)
        app=make_app(settings)
        @app.api_route('/v1/auth-fixture', methods=['GET','HEAD','POST','PUT','PATCH','DELETE'], include_in_schema=False)
        async def probe():return {'ok':True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1:8791') as c:
            config=await c.get('/ui/config')
            assert config.json()=={'public_read':True}
            assert settings.api_key not in config.text
            assert config.headers['cache-control']=='no-store'
            for host in ['127.0.0.1:8791','localhost:8791','[::1]:8791']:
                assert (await c.get('/v1/auth-fixture',headers={'Host':host})).status_code==200
            for method in ['GET','HEAD']:
                assert (await c.request(method,'/v1/auth-fixture')).status_code==200
            for method in ['POST','PUT','PATCH','DELETE']:
                assert (await c.request(method,'/v1/auth-fixture')).status_code==401
                assert (await c.request(method,'/v1/auth-fixture',headers={'Authorization':'Bearer '+settings.api_key})).status_code==200
            for path in ['/v1/captures','/v1/research/jobs','/v1/research/jobs/fixture/experiments']:
                assert (await c.post(path,json={})).status_code==401
            for headers in [{'Host':'api:8791'},{'Host':'attacker.example'},{'Origin':'https://attacker.example'},
                            {'Origin':'null'},{'Origin':'http://['},{'Sec-Fetch-Site':'cross-site'}]:
                assert (await c.get('/v1/auth-fixture',headers=headers)).status_code==401
                assert (await c.get('/ui/config',headers=headers)).json()=={'public_read':False}
            assert (await c.get('/v1/auth-fixture',headers={'Origin':'http://127.0.0.1:8791','Sec-Fetch-Site':'same-origin'})).status_code==200
            for path in ['/docs','/openapi.json']:
                assert (await c.get(path)).status_code==401
            assert (await c.get('/openapi.json',headers={'Authorization':'Bearer '+settings.api_key})).status_code==200
    asyncio.run(run())
