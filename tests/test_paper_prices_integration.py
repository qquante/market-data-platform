"""Real private PostgreSQL, synthetic token_info prices and transactional ledgers."""
import asyncio,hashlib,json,os
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from midas_data.paper_prices import TRACKED_PAPER_VERSION,FRAME_VERSION,PriceSourceChange
from midas_data.paper_model import sell,PaperPolicy
from test_paper_account_integration import fixture
from test_paper_bindings import initialize
pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


async def capture(f,price='3',at=None):
    from midas_data.contracts import CaptureMetadata
    from midas_data.normalize import normalize_capture
    m=f['current'][0];now=at or datetime.now(timezone.utc);token='0x'+'c'*40
    meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='base',
        requested_at=now-timedelta(seconds=1),received_at=now,http_status=200,evidence_kind='original_response')
    # No supply or market cap: execution requires the independently homologated price only.
    raw=json.dumps({'data':{'address':token,'name':'Fixture','price':{'price':price}}}).encode()
    await m.store.accept_capture(meta,raw);await m.store.record_normalization(meta.capture_id,normalize_capture(meta,raw))
    return str(meta.capture_id)


async def create(f,number=1,tracked=True):
    body=await f['signal'](number)
    body['policy'].update(budget_usd='20',entry_timeout_seconds=720 if tracked else 30,hold_seconds=1)
    if tracked:body['policy']['version']=TRACKED_PAPER_VERSION
    r=await f['client'].post('/v1/paper/positions',json=body);assert r.status_code==202,r.text
    return r.json()['position_id'],body


async def opened_legacy(f):
    key,body=await create(f,tracked=False)
    await f['capture']('3');await f['current'][0].step()
    row=(await f['client'].get('/v1/paper/positions/'+key)).json();assert row['status']=='open'
    await f['client'].post('/v1/rules/active/'+body['rule_id']+'/pause')
    await f['client'].delete('/v1/feature-windows/'+row['window_id'])
    return key,body,row


def test_v2_shared_price_without_supply_survives_window_removal_and_closes_once(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            c=f['client'];m=f['manager'];await initialize(f)
            a,ba=await create(f,1);b,bb=await create(f,2)
            for body in [ba,bb]:await c.post('/v1/rules/active/'+body['rule_id']+'/pause')
            wid=next(iter(m.windows));await c.delete('/v1/feature-windows/'+wid)
            await capture(f,'3');m.paper.prices.clear();reads=m.paper.prices.reads;await m.step()
            assert m.paper.prices.reads==reads+1 and len(m.paper.prices.frames)==1
            rows=[(await c.get('/v1/paper/positions/'+key)).json() for key in (a,b)]
            assert all(r['status']=='open' for r in rows)
            assert rows[0]['state']['entry_quote']['point_id']==rows[1]['state']['entry_quote']['point_id']
            source_route='/v1/paper/positions/'+a+'/price-source'
            assert (await c.post(source_route,json=dict(expected_policy_id=rows[0]['policy_id'],reason='already v2'))).status_code==409
            reads=m.paper.prices.reads
            await asyncio.gather(*[c.get('/v1/paper/positions/'+a) for _ in range(20)])
            assert m.paper.prices.reads==reads
            await asyncio.sleep(1.05);m=await f['reload']()
            cid=await capture(f,'4');await m.step()
            account=(await c.get('/v1/paper/account')).json()
            assert account['state']['active_positions']==0 and account['revision']==7
            assert not (await c.get('/v1/paper/tracking')).json()['items'] and not m.paper.prices.frames
            for original,key in zip(rows,(a,b)):
                closed=(await c.get('/v1/paper/positions/'+key)).json()
                assert closed['status']=='closed' and closed['state']['entry']==original['state']['entry']
                assert closed['state']['exit_quote']['point_id']==cid
                assert closed['state']['exit']==sell(PaperPolicy(**original['policy']),original['state']['entry'],'4')
                events=(await c.get('/v1/paper/positions/'+key+'/events')).json()['items']
                assert [e['kind'] for e in events].count('opened')==[e['kind'] for e in events].count('closed')==1
                for e in [e for e in events if e['kind'] in ('opened','closed')]:
                    r=await c.get('/v1/rule-frames/'+e['frame_key'])
                    assert hashlib.sha256(r.content).hexdigest()==e['frame_key'] and r.json()['version']==FRAME_VERSION
                    assert r.json()['result']['points'][-1]['reason'] is None
            m=await f['reload']();await m.step()
            assert (await c.get('/v1/paper/account')).json()==account
    asyncio.run(run())


def test_legacy_amendment_is_atomic_additive_future_only_and_idempotent_after_lost_ack(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            c=f['client'];db=f['pool'];await initialize(f)
            key,body,original=await opened_legacy(f);m=f['current'][0]
            account=(await c.get('/v1/paper/account')).json()
            route='/v1/paper/positions/'+key+'/price-source'
            request=dict(expected_policy_id=original['policy_id'],reason='Use tracked token_info for future exit')
            assert (await c.post(route,json=dict(request,expected_policy_id='0'*64))).status_code==409
            con=m.connection
            class Failure:
                def __getattr__(self,name):return getattr(con,name)
                async def execute(self,sql,*args):
                    if sql.startswith('INSERT INTO paper_events') and args[2]=='price_source_changed':
                        raise RuntimeError('Injected amendment failure')
                    return await con.execute(sql,*args)
            m.connection=Failure()
            with pytest.raises(RuntimeError,match='amendment failure'):
                await m.paper.change_price_source(key,PriceSourceChange(**request))
            assert (await c.get('/v1/paper/positions/'+key)).json()==original
            assert (await c.get('/v1/paper/account')).json()==account
            m.connection=con;m=await f['reload']()
            await asyncio.sleep(1.05);await capture(f,'9') # valid but predates the explicit amendment
            persist=m.paper.persist
            async def lost_ack(*args,**kwargs):
                await persist(*args,**kwargs)
                if args[3]['kind']=='price_source_changed':raise RuntimeError('Injected amendment acknowledgement loss')
            m.paper.persist=lost_ack
            with pytest.raises(RuntimeError,match='acknowledgement loss'):
                await m.paper.change_price_source(key,PriceSourceChange(**request))
            assert await db.fetchval("SELECT count(*) FROM paper_events WHERE kind='price_source_changed'")==1
            m=await f['reload']()
            repeated=await c.post(route,json=request);assert repeated.status_code==202 and not repeated.json()['created']
            assert (await c.post(route,json=dict(request,reason='different'))).status_code==409
            await m.step()
            still=(await c.get('/v1/paper/positions/'+key)).json()
            assert still['status']=='open' and still['state']['reason']=='awaiting_new_price'
            for field in ('policy','policy_id','definition','window_id','created_at'):
                assert still[field]==original[field]
            assert still['state']['entry']==original['state']['entry']
            assert (await c.get('/v1/paper/account')).json()==account
            await capture(f,'4');m.paper.prices.clear();await m.step()
            final=(await c.get('/v1/paper/positions/'+key)).json();assert final['status']=='closed'
            assert final['state']['exit_quote']['value_text']=='4'
            assert datetime.fromisoformat(final['state']['exit_quote']['observed_at'])>datetime.fromisoformat(still['state']['price_source_change']['effective_after'])
            assert not (await c.post(route,json=request)).json()['created']
            final_account=(await c.get('/v1/paper/account')).json()
            assert final_account['revision']==account['revision']+1
            m=await f['reload']();await m.step()
            assert (await c.get('/v1/paper/account')).json()==final_account
    asyncio.run(run())


def test_latest_invalid_and_tied_quotes_do_not_fall_back_and_price_failure_discards_cache(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            c=f['client'];m=f['manager'];await initialize(f);key,_=await create(f)
            await capture(f,'3');await capture(f,'bad');m.paper.prices.clear();await m.step()
            row=(await c.get('/v1/paper/positions/'+key)).json()
            assert row['status']=='entry_pending' and row['state']['reason']=='latest_observation_unavailable'
            now=datetime.now(timezone.utc)
            await capture(f,'4',now);await capture(f,'5',now)
            m.paper.prices.clear();await m.step()
            assert m.paper.entries[key]['state']['status']=='entry_pending'
            fetch=m.store._temporal_fetch
            async def failure(*args,**kwargs):raise TimeoutError()
            m.store._temporal_fetch=failure;m.paper.prices.clear();await m.step()
            assert m.paper.entries[key]['state']['reason']=='price_frame_unavailable'
            m.store._temporal_fetch=fetch;await capture(f,'6')
            m.paper.prices.clear();await m.step()
            assert m.paper.entries[key]['state']['status']=='open'
    asyncio.run(run())

def test_binding_admits_a_future_signal_with_explicit_v2_policy(tmp_path):
    async def run():
        from test_paper_bindings import rule,bind
        async with fixture(tmp_path) as f:
            await initialize(f);m=f['manager'];c=f['client']
            rid=await rule(f,threshold='250');await m.step()
            execution=dict(budget_usd='20',fee_bps=0,slippage_bps=0,entry_latency_seconds=0,
                entry_timeout_seconds=720,hold_seconds=1,max_price_age_seconds=120,max_signal_age_seconds=120,
                version=TRACKED_PAPER_VERSION)
            await bind(f,rid,dict(rule_id=rid,policy=execution))
            await f['capture']('3');await m.step()
            assert len(m.paper.entries)==1
            key=next(iter(m.paper.entries))
            assert m.paper.entries[key]['policy'].version==TRACKED_PAPER_VERSION
            await c.post('/v1/paper/bindings/'+rid+'/pause')
            await capture(f,'4');m.paper.prices.clear();await m.step()
            assert m.paper.entries[key]['state']['status']=='open'
            assert m.paper.entries[key]['state']['entry_quote']['endpoint']=='token_info'
    asyncio.run(run())
