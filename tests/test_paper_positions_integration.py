"""Private real PostgreSQL paper lifecycle, with synthetic source quotes and actual timers."""
import asyncio,json,os,hashlib
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import pytest
pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_paper_consumption_forward_fills_atomic_recovery_and_timeout(tmp_path,monkeypatch):
    async def run():
        import asyncpg,httpx
        import midas_data.feature_windows as fw
        from midas_data.active_rules import ActiveRules
        from midas_data.paper_positions import PaperPositions
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS
        settings=Settings.from_env();schema='paper_fixture_'+uuid4().hex
        admin=await asyncpg.connect(settings.database_dsn,command_timeout=15);pool=None;manager=None
        try:
            assert await admin.fetchval('SELECT current_database()')=='midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=4,command_timeout=15,
                server_settings={'search_path':schema,'timezone':'UTC'})
            async with pool.acquire() as con:
                for file in sorted(Path('midas_data/migrations').glob('*.sql')):await con.execute(file.read_text())
            store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
            start=datetime.now(timezone.utc)-timedelta(seconds=30);token='0x'+'a'*40
            async def capture(seconds,price,supply='100'):
                at=start+timedelta(seconds=seconds)
                meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='base',
                    requested_at=at-timedelta(seconds=1),received_at=at,http_status=200,evidence_kind='original_response')
                raw=json.dumps({'data':{'address':token,'price':{'price':price},'circulating_supply':supply}}).encode()
                await store.accept_capture(meta,raw)
                await store.record_normalization(meta.capture_id,normalize_capture(meta,raw))
            def owner():
                m=fw.FeatureWindows(store);m.rules=ActiveRules(m);m.paper=PaperPositions(m);return m
            await capture(0,'2');await capture(20,'2')
            manager=owner();await manager.connect()
            app=make_app(settings);app.state.store=store;app.state.feature_windows=manager
            window=dict(source_kind='fixture',chain='base',token=token,method=next(iter(METHODS)),
                        window_seconds=180,gap_after_seconds=120)
            rule=dict(comparison=dict(window=window,feature='market_cap_latest',operator='gte',threshold='0',unit='usd'),
                      debounce_seconds=0,cooldown_seconds=0)
            policy=dict(budget_usd='100',fee_bps=0,slippage_bps=0,entry_latency_seconds=0,
                entry_timeout_seconds=30,hold_seconds=2,max_price_age_seconds=120,max_signal_age_seconds=300)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def get(path,params=None):
                    r=await client.get(path,params=params);assert r.status_code==200,r.text;return r.json()
                async def create_signal(cooldown=0):
                    r=await client.post('/v1/rules/active',json=dict(rule,cooldown_seconds=cooldown))
                    assert r.status_code==202,r.text
                    key=r.json()['rule_id'];await manager.step()
                    event=manager.rules.entries[key]['last'];assert event['disposition']=='signal'
                    return key,event['sequence']
                r=await client.post('/v1/feature-windows',params=window);assert r.status_code==202
                account_policy=dict(initial_cash_usd='1000',max_position_usd='200',max_exposure_usd='1000',max_open_positions=4)
                assert (await client.post('/v1/paper/account',json=account_policy)).status_code==202
                rule_id,signal_sequence=await create_signal()
                body=dict(rule_id=rule_id,signal_sequence=signal_sequence,policy=policy)
                import midas_data.paper_positions as pp
                real_datetime=pp.datetime
                for offset in (-3600,3600):
                    class Shifted:
                        @classmethod
                        def now(cls,tz):return real_datetime.now(tz)+timedelta(seconds=offset)
                    monkeypatch.setattr(pp,'datetime',Shifted)
                    rejected=await client.post('/v1/paper/positions',json=body)
                    assert rejected.status_code==422 and 'Signal is too old or in the future' in rejected.text
                monkeypatch.setattr(pp,'datetime',real_datetime)
                assert (await client.post('/v1/paper/positions',json=dict(body,signal_sequence=999))).status_code==422
                replies=await asyncio.gather(*[client.post('/v1/paper/positions',json=body) for _ in range(20)])
                assert all(r.status_code==202 for r in replies)
                assert sum(r.json()['created'] for r in replies)==1
                position_id=replies[0].json()['position_id'];route='/v1/paper/positions/'+position_id
                assert all(r.json()['position_id']==position_id for r in replies)
                assert await pool.fetchval('SELECT count(*) FROM paper_positions')==1
                assert (await client.post('/v1/paper/positions',json=dict(body,policy=dict(policy,budget_usd='200')))).status_code==409
                await manager.step()
                assert (await get(route))['state']['reason']=='awaiting_new_price'
                assert await pool.fetchval("SELECT count(*) FROM paper_events WHERE kind='opened'")==0
                reserved=await get('/v1/paper/account')
                assert reserved['state']['cash_usd']=='1000' and reserved['state']['reserved_usd']=='100'
                old_revision=manager.paper.entries[position_id]['revision']
                await capture((datetime.now(timezone.utc)-start).total_seconds(),'2')
                con=manager.connection
                class FailOpen:
                    def __getattr__(self,name):return getattr(con,name)
                    async def execute(self,sql,*args):
                        if sql.startswith('INSERT INTO paper_events') and args[2]=='opened':
                            raise RuntimeError('Injected paper transaction failure')
                        return await con.execute(sql,*args)
                manager.connection=FailOpen()
                with pytest.raises(RuntimeError,match='Injected'):await manager.step()
                assert await pool.fetchval("SELECT count(*) FROM paper_events WHERE kind='opened'")==0
                assert await pool.fetchval('SELECT revision FROM paper_positions WHERE position_id=$1',position_id)==old_revision
                assert (await get('/v1/paper/account'))==reserved
                manager.connection=con;await manager.disconnect()
                manager=owner();await manager.connect();app.state.feature_windows=manager
                original=manager.paper.persist
                async def lost_ack(key,entry,*args,**kwargs):
                    saved=dict(entry)
                    await original(key,entry,*args,**kwargs)
                    if args[1]['kind']=='opened':
                        entry.clear();entry.update(saved)
                        raise RuntimeError('Injected paper lost acknowledgement')
                manager.paper.persist=lost_ack
                with pytest.raises(RuntimeError,match='acknowledgement'):await manager.step()
                assert await pool.fetchval("SELECT count(*) FROM paper_events WHERE kind='opened'")==1
                assert manager.paper.entries[position_id]['state']['status']=='entry_pending'
                await manager.disconnect()
                manager=owner();await manager.connect();app.state.feature_windows=manager
                assert manager.paper.entries[position_id]['state']['status']=='open'
                account=await get('/v1/paper/account')
                assert Decimal(account['state']['cash_usd'])==900 and Decimal(account['state']['invested_cost_usd'])==100
                assert Decimal(account['state']['reserved_usd'])==0 and account['revision']==3
                await manager.step()
                opened=(await get(route))['state']
                assert Decimal(opened['entry']['quantity'])==50
                assert await pool.fetchval("SELECT count(*) FROM paper_events WHERE kind='opened'")==1
                # Position exit is independent of pausing the entry rule.
                assert (await client.post('/v1/rules/active/'+rule_id+'/pause')).status_code==204
                reads=manager.reads
                await asyncio.gather(*[get(route) for _ in range(20)])
                assert manager.reads==reads
                monkeypatch.setattr(fw,'POLL_SECONDS',.05);manager.start()
                async def wait_for(predicate):
                    async with asyncio.timeout(10):
                        while not predicate():await asyncio.sleep(.05)
                await wait_for(lambda: manager.paper.entries[position_id]['state']['reason']=='awaiting_new_price')
                assert manager.paper.entries[position_id]['state']['status']=='open'
                # Lose the exit commit acknowledgement too; the running owner must reload closed state.
                close_persist=manager.paper.persist
                async def lost_close(key,entry,*args,**kwargs):
                    saved=dict(entry)
                    await close_persist(key,entry,*args,**kwargs)
                    if args[1]['kind']=='closed':
                        entry.clear();entry.update(saved)
                        raise RuntimeError('Injected lost exit acknowledgement')
                manager.paper.persist=lost_close
                # Supply changes too: paper P&L must use price 2 -> 3, not cap 200 -> 600.
                await capture((datetime.now(timezone.utc)-start).total_seconds(),'3',supply='200')
                await wait_for(lambda: position_id not in manager.paper.entries)
                closed=await get(route);assert closed['status']=='closed'
                assert Decimal(closed['state']['exit']['pnl_usd'])==50
                account=await get('/v1/paper/account')
                assert Decimal(account['state']['cash_usd'])==1050 and Decimal(account['state']['realized_pnl_usd'])==50
                assert account['revision']==4 and account['state']['active_positions']==0
                events=(await get(route+'/events'))['items']
                assert [e['kind'] for e in events].count('opened')==[e['kind'] for e in events].count('closed')==1
                assert sum(Decimal(e['cash_delta_usd']) for e in events)==50
                for event in events:
                    if event['kind'] in ('opened','closed'):
                        r=await client.get('/v1/rule-frames/'+event['frame_key'])
                        assert r.status_code==200 and hashlib.sha256(r.content).hexdigest()==event['frame_key']
                assert (await client.post('/v1/paper/positions',json=body)).json()['created'] is False
                page=await get(route+'/events',dict(limit=2));assert page['has_more']
                older=await get(route+'/events',dict(limit=2,before_revision=page['next_before_revision']))
                assert older['items'][0]['revision']<page['items'][-1]['revision']
                assert (await client.post('/v1/paper/positions',content=b'x'*8193)).status_code==413
                assert (await client.get(route,headers={'authorization':'bad'})).status_code==401
                assert (await client.get('/v1/paper/positions/'+'0'*64)).status_code==404
                await manager.close()
                manager=owner();await manager.connect();app.state.feature_windows=manager
                # Four pending lots consume four different signals; the fifth is refused before consumption.
                for number in range(5):
                    rid,seq=await create_signal(100+number)
                    r=await client.post('/v1/paper/positions',json=dict(rule_id=rid,signal_sequence=seq,
                        policy=dict(policy,entry_timeout_seconds=2)))
                    assert r.status_code==(202 if number<4 else 429),r.text
                assert len(manager.paper.entries)==4
                manager.start()
                await wait_for(lambda: not manager.paper.entries)
                assert await pool.fetchval("SELECT count(*) FROM paper_positions WHERE status='entry_expired'")==4
                assert await pool.fetchval("SELECT count(*) FROM paper_events WHERE kind='opened'")==1
                all_positions=await get('/v1/paper/positions',dict(limit=2))
                assert all_positions['has_more'] and len(all_positions['items'])==2
                old=await get('/v1/paper/positions',dict(before_number=all_positions['next_before_number']))
                assert len(old['items'])==3
                assert max(x['position_number'] for x in old['items'])<min(x['position_number'] for x in all_positions['items'])
                account=await get('/v1/paper/account')
                assert Decimal(account['state']['cash_usd'])==1050 and Decimal(account['state']['reserved_usd'])==0
                assert account['state']['active_positions']==0 and account['revision']==12
                account_events=(await get('/v1/paper/account/events'))['items']
                assert [e['kind'] for e in account_events].count('opened')==[e['kind'] for e in account_events].count('closed')==1
        finally:
            if manager:await manager.close()
            if pool:await pool.close()
            assert schema.startswith('paper_fixture_') and len(schema)==len('paper_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())
