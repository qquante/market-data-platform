"""Explicit isolated fixture server for UI and HTTP load acceptance.

Creates only ui_fixture_<uuid> in the configured database, serves on 8794,
never starts a provider/analytics worker, and drops that private schema on shutdown.
"""
import asyncio,json,os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import asyncpg,uvicorn
from fastapi.responses import Response
from midas_data.api import make_app
from midas_data.dashboard import SECURITY
from midas_data.settings import Settings
from midas_data.store import Store
from midas_data.raw_store import RawStore
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.holders import parse_holders
from midas_data.holder_store import HolderStore
from midas_data.token_registry import TokenRegistry
from midas_data.feature_windows import FeatureWindows
from midas_data.active_rules import ActiveRules
from midas_data.paper_positions import PaperPositions
from midas_data.research_jobs import ResearchJobs

async def main():
    if os.getenv('DATA_INTEGRATION')!='1':raise RuntimeError('Private integration opt-in required')
    settings=replace(Settings.from_env(),api_key='fixture-ui-only',collector_status_path=None,raw_min_free_bytes=0)
    schema='ui_fixture_'+uuid4().hex;assert len(schema)==43
    admin=await asyncpg.connect(settings.database_dsn,command_timeout=15)
    pool=None;cleaned=False
    async def cleanup():
        nonlocal pool,cleaned
        if cleaned:return
        if pool:await pool.close();pool=None
        assert schema.startswith('ui_fixture_') and len(schema)==43
        await admin.execute('DROP SCHEMA IF EXISTS '+schema+' CASCADE')
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)',schema)
        cleaned=True
        proof={'schema':schema,'cleanup_verified':True,'checked_at':datetime.now(timezone.utc).isoformat()}
        Path('/evidence/preview-cleanup.json').write_text(json.dumps(proof,indent=2))
        print(json.dumps(proof),flush=True)
    try:
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        await admin.execute('CREATE SCHEMA '+schema)
        pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=6,command_timeout=15,
            server_settings={'search_path':schema,'timezone':'UTC','statement_timeout':'5000'})
        async with pool.acquire() as con:
            for p in sorted(Path('/workspace/midas_data/migrations').glob('*.sql')):
                await con.execute(p.read_text())
        store=Store('private-fixture',RawStore('/tmp/ui-fixture-raw',min_free_bytes=0));store.pool=pool
        now=datetime.now(timezone.utc);start=now.replace(hour=0,minute=0,second=0,microsecond=0)
        interval=max(1,(now-start).total_seconds()/32)
        for i in range(31):
            if i==18:continue
            received=start+timedelta(seconds=interval*i)
            value=Decimal(18000+i*3100+(i%5)*6000)
            body={'address':'FixtureTokenA','name':'Token de homologacao','symbol':'FIX',
                  'price':{'price':str(value/Decimal(1000000000))},'circulating_supply':'1000000000'}
            if i==11:body.pop('circulating_supply')
            m=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='sol',
                requested_at=received,received_at=received,http_status=200,evidence_kind='original_response',
                request_params={'address':'FixtureTokenA'})
            raw=json.dumps({'code':0,'data':body}).encode()
            await store.accept_capture(m,raw);await store.record_normalization(m.capture_id,normalize_capture(m,raw))
        await TokenRegistry(store).step()
        m=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_top_holders',chain='sol',
            requested_at=now,received_at=now,http_status=200,evidence_kind='original_response',
            request_params={'chain':'sol','address':'FixtureTokenA','limit':10,'order_by':'amount_percentage','direction':'desc'})
        raw=json.dumps({'code':0,'data':{'list':[{'address':'Wallet'+str(i),'balance':str(10000-i*100),
            'amount_percentage':'0.01','usd_value':str(100-i)} for i in range(10)]}}).encode()
        await store.accept_capture(m,raw);await HolderStore(store).record(parse_holders(m,raw))
        app=make_app(settings)
        @asynccontextmanager
        async def lifespan(app):
            app.state.store=store
            owner=FeatureWindows(store);owner.rules=ActiveRules(owner);owner.paper=PaperPositions(owner)
            app.state.feature_windows=owner;owner.start()
            async def research_loop():
                while True:
                    await ResearchJobs(store).step()
                    await asyncio.sleep(.25)
            jobs=asyncio.create_task(research_loop())
            try:yield
            finally:
                jobs.cancel();await asyncio.gather(jobs,return_exceptions=True)
                await owner.close()
                # Uvicorn re-raises SIGTERM after lifespan shutdown. Release private
                # fixtures here, before that signal can skip the outer async finally.
                await cleanup()
        app.router.lifespan_context=lifespan
        app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/']
        @app.get('/')
        async def fixture_index():
            body=Path('/workspace/midas_data/ui/index.html').read_text()
            body=body.replace('<main>','<main><p class="message error">HOMOLOGAÇÃO ISOLADA · dados fixture · esta tela não mostra a coleta real.</p>')
            return Response(body,media_type='text/html',headers=SECURITY)
        ready={'scope':'private SQL + HTTP fixture','schema':schema,'port':8794,
            'token':'FixtureTokenA','source_kind':'fixture','holder_capture_id':str(m.capture_id),
            'ready_at':datetime.now(timezone.utc).isoformat(),'key':'fixture-ui-only'}
        Path('/evidence/preview-ready.json').write_text(json.dumps(ready,indent=2))
        print(json.dumps(ready),flush=True)
        server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=8794,log_level='warning'))
        await server.serve()
    finally:
        await cleanup()
        await admin.close()

asyncio.run(main())
