"""Real private PostgreSQL account admission, rollback, migration and recovery."""
import asyncio,json,os
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import pytest
pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]

@asynccontextmanager
async def fixture(tmp_path,legacy=False):
    import asyncpg,httpx
    from midas_data.active_rules import ActiveRules
    from midas_data.api import make_app
    from midas_data.contracts import CaptureMetadata
    from midas_data.feature_windows import FeatureWindows
    from midas_data.normalize import normalize_capture
    from midas_data.paper_positions import PaperPositions
    from midas_data.raw_store import RawStore
    from midas_data.settings import Settings
    from midas_data.store import Store
    from midas_data.trajectory import METHODS
    settings=Settings.from_env();schema='paper_account_fixture_'+uuid4().hex
    admin=await asyncpg.connect(settings.database_dsn,command_timeout=15);pool=None;current=[None]
    try:
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        await admin.execute(f'CREATE SCHEMA {schema}')
        pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=4,command_timeout=15,
            server_settings={'search_path':schema,'timezone':'UTC'})
        async with pool.acquire() as con:
            for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                if not legacy or path.name<'013':await con.execute(path.read_text())
        store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
        token='0x'+'c'*40
        async def capture(price='2'):
            at=datetime.now(timezone.utc)
            meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='base',
                requested_at=at-timedelta(seconds=1),received_at=at,http_status=200,evidence_kind='original_response')
            raw=json.dumps({'data':{'address':token,'price':{'price':price},'circulating_supply':'100'}}).encode()
            await store.accept_capture(meta,raw);await store.record_normalization(meta.capture_id,normalize_capture(meta,raw))
        def owner(with_paper=True):
            m=FeatureWindows(store);m.rules=ActiveRules(m)
            if with_paper:m.paper=PaperPositions(m)
            current[0]=m;return m
        manager=owner(not legacy);await manager.connect();await capture()
        app=make_app(settings);app.state.store=store;app.state.feature_windows=manager
        window=dict(source_kind='fixture',chain='base',token=token,method=next(iter(METHODS)),
            window_seconds=180,gap_after_seconds=120)
        execution=dict(budget_usd='60',fee_bps=0,slippage_bps=0,entry_latency_seconds=0,
            entry_timeout_seconds=2,hold_seconds=1,max_price_age_seconds=120,max_signal_age_seconds=120)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                headers={'authorization':'Bearer '+settings.api_key}) as client:
            r=await client.post('/v1/feature-windows',params=window);assert r.status_code==202
            async def signal(number):
                config=dict(comparison=dict(window=window,feature='market_cap_latest',operator='gte',threshold='0',unit='usd'),
                    debounce_seconds=0,cooldown_seconds=number)
                r=await client.post('/v1/rules/active',json=config);assert r.status_code==202,r.text
                await current[0].step();rid=r.json()['rule_id']
                event=current[0].rules.entries[rid]['last'];assert event['disposition']=='signal'
                return dict(rule_id=rid,signal_sequence=event['sequence'],policy=execution)
            async def reload():
                await current[0].close()
                m=owner();await m.connect();app.state.feature_windows=m;return m
            yield dict(pool=pool,client=client,manager=manager,current=current,owner=owner,app=app,
                signal=signal,reload=reload,capture=capture)
    finally:
        if current[0]:await current[0].close()
        if pool:await pool.close()
        assert schema.startswith('paper_account_fixture_') and len(schema)==len('paper_account_fixture_')+32
        await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()


def test_shared_account_concurrent_reservations_rollback_restart_and_expiry(tmp_path,monkeypatch):
    async def run():
        from midas_data.paper_model import PaperRequest
        async with fixture(tmp_path) as f:
            client,pool,m=f['client'],f['pool'],f['manager']
            a,b,c=await f['signal'](1),await f['signal'](2),await f['signal'](3)
            assert (await client.get('/v1/paper/account')).status_code==404
            assert (await client.post('/v1/paper/positions',json=a)).status_code==409
            config=dict(initial_cash_usd='100',max_position_usd='70',max_exposure_usd='90',max_open_positions=2)
            replies=await asyncio.gather(*[client.post('/v1/paper/account',json=config) for _ in range(20)])
            assert all(r.status_code==202 for r in replies)
            assert sum(r.json()['created'] for r in replies)==1
            assert await pool.fetchval('SELECT count(*) FROM paper_account_events')==1
            assert (await client.post('/v1/paper/account',json=dict(config,initial_cash_usd='200'))).status_code==409
            assert (await client.post('/v1/paper/account',json=dict(config,max_open_positions=True))).status_code==422
            assert (await client.post('/v1/paper/account',content=b'x'*8193)).status_code==413
            assert (await client.get('/v1/paper/account',headers={'authorization':'bad'})).status_code==401
            # Fail after position+event writes: no reservation or consumed signal may survive rollback.
            con=m.connection
            class FailReserve:
                def __getattr__(self,name):return getattr(con,name)
                async def execute(self,sql,*args):
                    if sql.startswith('INSERT INTO paper_account_events') and args[1]=='requested':
                        raise RuntimeError('Injected account reservation failure')
                    return await con.execute(sql,*args)
            m.connection=FailReserve()
            with pytest.raises(RuntimeError,match='reservation failure'):
                await m.paper.create(PaperRequest(**a))
            assert await pool.fetchval('SELECT count(*) FROM paper_positions')==0
            assert await pool.fetchval('SELECT count(*) FROM paper_events')==0
            assert await pool.fetchval('SELECT revision FROM paper_account')==1
            m.connection=con;m=await f['reload']()
            # Two distinct signals contend for 100 cash: exactly one 60 reservation is accepted.
            replies=await asyncio.gather(*[client.post('/v1/paper/positions',json=body) for body in (a,b)])
            assert sorted(r.status_code for r in replies)==[202,429]
            rejected=(a,b)[next(i for i,r in enumerate(replies) if r.status_code==429)]
            accepted=replies[next(i for i,r in enumerate(replies) if r.status_code==202)].json()['position_id']
            assert await pool.fetchval('SELECT count(*) FROM paper_positions')==1
            state=(await client.get('/v1/paper/account')).json()
            assert state['balances']==dict(available_usd='40',committed_usd='60')
            # A smaller request has enough cash but exceeds exposure; it must remain unconsumed.
            r=await client.post('/v1/paper/positions',json=dict(rejected,policy=dict(rejected['policy'],budget_usd='40')))
            assert r.status_code==429 and 'exposure' in r.text
            # Lose the acknowledgement of the OUTER admission commit, after both ledgers were committed.
            con=m.connection
            class LostTransaction:
                def __init__(self,tx):self.tx=tx
                async def __aenter__(self):return await self.tx.__aenter__()
                async def __aexit__(self,*args):
                    result=await self.tx.__aexit__(*args)
                    if args[0] is None:raise RuntimeError('Injected outer commit acknowledgement loss')
                    return result
            class LostCommit:
                def __getattr__(self,name):return getattr(con,name)
                def transaction(self,*args,**kwargs):
                    tx=con.transaction(*args,**kwargs)
                    return tx if con.is_in_transaction() else LostTransaction(tx)
            smaller=dict(rejected,policy=dict(rejected['policy'],budget_usd='30'))
            m.connection=LostCommit()
            with pytest.raises(RuntimeError,match='acknowledgement loss'):
                await m.paper.create(PaperRequest(**smaller))
            assert await pool.fetchval('SELECT count(*) FROM paper_positions')==2
            m.connection=con;m=await f['reload']()
            assert (await client.post('/v1/paper/positions',json=smaller)).json()['created'] is False
            restored=(await client.get('/v1/paper/account')).json()
            assert restored['state']['reserved_usd']=='90' and restored['state']['active_positions']==2
            r=await client.post('/v1/paper/positions',json=dict(c,policy=dict(c['policy'],budget_usd='1')))
            assert r.status_code==429 and 'count limit' in r.text
            # No new prices: both reservations expire and cash becomes fully available again.
            import midas_data.feature_windows as fw
            monkeypatch.setattr(fw,'POLL_SECONDS',.05);m.start()
            async with asyncio.timeout(10):
                while m.paper.entries:await asyncio.sleep(.05)
            final=(await client.get('/v1/paper/account')).json()
            assert final['state']==dict(cash_usd='100',reserved_usd='0',invested_cost_usd='0',realized_pnl_usd='0',active_positions=0)
            assert final['revision']==5
            assert not (await client.post('/v1/paper/account',json=config)).json()['created']
            assert (await client.get('/v1/paper/account')).json()['revision']==5
            events=(await client.get('/v1/paper/account/events')).json()['items']
            assert [e['kind'] for e in events].count('requested')==[e['kind'] for e in events].count('entry_expired')==2
            page=(await client.get('/v1/paper/account/events',params=dict(limit=2))).json()
            older=(await client.get('/v1/paper/account/events',params=dict(before_revision=page['next_before_revision']))).json()
            assert len(older['items'])==3 and older['items'][0]['revision']<page['items'][-1]['revision']
            assert (await client.get('/v1/paper/account/events',params=dict(before_revision=2**63))).status_code==422
            # Startup compares durable reservations to the bounded active set.
            await m.close()
            await pool.execute("""UPDATE paper_account SET state=jsonb_set(state,'{reserved_usd}','"1"'::jsonb)""")
            from midas_data.feature_windows import WindowUnavailable
            m=f['owner']()
            with pytest.raises(WindowUnavailable,match='reservations differ'):await m.connect()
            await pool.execute("""UPDATE paper_account SET state=jsonb_set(state,'{reserved_usd}','"0"'::jsonb)""")
    asyncio.run(run())


def test_upgrade_preserves_legacy_lot_and_excludes_it_from_shared_cash(tmp_path,monkeypatch):
    async def run():
        from midas_data.features import _hash
        from midas_data.paper_model import PaperPolicy,PAPER_VERSION
        async with fixture(tmp_path,legacy=True) as f:
            client,pool,m=f['client'],f['pool'],f['manager']
            body=await f['signal'](1);p=PaperPolicy(**body['policy']);now=datetime.now(timezone.utc)
            key=_hash([PAPER_VERSION,body['rule_id'],body['signal_sequence']]);wid=next(iter(m.windows))
            state=dict(status='entry_pending',reason='awaiting_new_price',requested_at=now.isoformat(),
                entry_not_before=now.isoformat(),entry_deadline=(now+timedelta(seconds=20)).isoformat(),
                next_due_at=(now+timedelta(seconds=20)).isoformat(),budget_usd=p.budget_usd,execution='simulated')
            await pool.execute("""INSERT INTO paper_positions(position_id,rule_id,signal_sequence,policy_id,policy,definition,window_id,state,status)
                VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8::jsonb,'entry_pending')""",
                key,body['rule_id'],body['signal_sequence'],p.key(),json.dumps(p.model_dump()),json.dumps(p.definition()),wid,json.dumps(state))
            before=dict(await pool.fetchrow('SELECT * FROM paper_positions WHERE position_id=$1',key))
            await m.close()
            async with pool.acquire() as con:
                for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                    if path.name>='013':await con.execute(path.read_text())
            after=dict(await pool.fetchrow('SELECT * FROM paper_positions WHERE position_id=$1',key))
            assert after.pop('account_id') is None and after==before
            m=f['owner']();await m.connect();f['app'].state.feature_windows=m
            assert (await client.post('/v1/paper/positions',json=body)).json()['created'] is False
            config=dict(initial_cash_usd='100',max_position_usd='60',max_exposure_usd='100',max_open_positions=2)
            assert (await client.post('/v1/paper/account',json=config)).status_code==202
            await f['capture']('2');await m.step()
            assert m.paper.entries[key]['state']['status']=='open'
            import midas_data.feature_windows as fw
            monkeypatch.setattr(fw,'POLL_SECONDS',.05);m.start();await asyncio.sleep(1.1)
            await f['capture']('3')
            async with asyncio.timeout(10):
                while key in m.paper.entries:await asyncio.sleep(.05)
            old=(await client.get('/v1/paper/positions/'+key)).json()
            assert old['status']=='closed' and old['account_id'] is None and old['definition']==p.definition()
            assert Decimal(old['state']['exit']['pnl_usd'])==30
            account=(await client.get('/v1/paper/account')).json()
            assert account['revision']==1 and account['state']['cash_usd']=='100'
            assert await pool.fetchval('SELECT count(*) FROM paper_account_events')==1
            # Shared account identity and limits still apply after reloading alongside legacy history.
            m=await f['reload']()
            assert not m.paper.entries and m.paper.account.policy.initial_cash_usd=='100'
    asyncio.run(run())
