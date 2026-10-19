"""Private PostgreSQL demand projection: no quote, window or account mutation."""
import asyncio,json,os
from datetime import datetime,timedelta,timezone
import pytest
from test_paper_account_integration import fixture
from test_paper_bindings import initialize
pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_demand_survives_rule_pause_window_removal_and_restart_without_repricing(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            c=f['client'];m=f['manager'];db=f['pool']
            await initialize(f)
            body=await f['signal'](1);body['policy'].update(budget_usd='20',entry_timeout_seconds=30)
            key=(await c.post('/v1/paper/positions',json=body)).json()['position_id']
            await f['capture']('3');await m.step()
            before=(await c.get('/v1/paper/positions/'+key)).json()
            assert before['status']=='open'
            account=(await c.get('/v1/paper/account')).json()
            await c.post('/v1/rules/active/'+body['rule_id']+'/pause')
            await c.delete('/v1/feature-windows/'+before['window_id'])
            r=await c.get('/v1/paper/tracking');assert r.status_code==200
            expected=r.json();assert r.headers['cache-control']=='no-store'
            assert expected['scope']=='collection_only' and expected['price_policy_changed'] is False
            assert expected['items']==[dict(source_kind='fixture',chain='base',
                token_address='0x'+'c'*40,position_ids=[key])]
            assert (await c.get('/v1/paper/positions/'+key)).json()==before
            m=await f['reload']()
            assert (await c.get('/v1/paper/tracking')).json()==expected
            reads=m.reads
            replies=await asyncio.gather(*[c.get('/v1/paper/tracking') for _ in range(20)])
            assert all(r.json()==expected for r in replies) and m.reads==reads
            assert (await c.get('/v1/paper/account')).json()==account
            assert (await c.get('/v1/paper/positions/'+key)).json()==before
            assert (await c.get('/v1/paper/tracking',headers={'authorization':'bad'})).status_code==401
            m.paper.disconnect()
            assert (await c.get('/v1/paper/tracking')).status_code==503
    asyncio.run(run())


def test_shared_token_demand_deduplicates_positions_and_disappears_after_terminal_state(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            c=f['client'];m=f['manager'];db=f['pool'];await initialize(f)
            ids=[]
            for number in (1,2):
                body=await f['signal'](number);body['policy'].update(budget_usd='20',entry_timeout_seconds=30)
                r=await c.post('/v1/paper/positions',json=body);assert r.status_code==202
                ids.append(r.json()['position_id'])
            value=(await c.get('/v1/paper/tracking')).json()
            assert len(value['items'])==1 and value['items'][0]['position_ids']==ids
            # Expire both pending entries via the real transition clock; no quote and no debit.
            import midas_data.paper_positions as positions
            from unittest.mock import patch
            future=datetime.now(timezone.utc)+timedelta(seconds=31)
            class Clock:
                @staticmethod
                def now(tz):return future
            with patch.object(positions,'datetime',Clock):await m.paper.step()
            assert (await c.get('/v1/paper/tracking')).json()['items']==[]
            assert await db.fetchval("SELECT count(*) FROM paper_positions WHERE status='entry_expired'")==2
            assert (await c.get('/v1/paper/account')).json()['state']['cash_usd']=='100'
    asyncio.run(run())
