"""Bounded Store group contracts; fake transport tests and opt-in isolated SQL proof."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from midas_data.contracts import CaptureMetadata, FieldObservation, Issue, NormalizationResult
from midas_data.normalize import normalize_capture
from midas_data.store import CaptureConflict, Store, canonical


def item(*, endpoint="token_info", text="1.2300"):
    capture_id = uuid4()
    meta = CaptureMetadata(capture_id=capture_id, source_kind="fixture", endpoint=endpoint,
        chain="base", requested_at="2026-09-08T12:00:00Z", received_at="2026-09-08T12:00:01Z",
        http_status=200, evidence_kind="original_response")
    result = normalize_capture(meta, json.dumps({"address": "0x" + uuid4().hex + "12345678",
        "price": {"price": text}, "holder_count": 0}).encode())
    return capture_id, result


def full_receipt(batch_id, count=2):
    summary={'count':count,'checksum':'a'*64,'source_counts':{'fixture':count} if count else {},
             'available_frontier':'2026-09-08T00:00:00Z' if count else None}
    return {'batch_id':batch_id,'ok':True,**summary,
            'lake':{**summary,'source_counts':dict(summary['source_counts']),'schema_version':'midas-analytics-v1','ok':True,'receipt_present':True},
            'clickhouse':{**summary,'source_counts':dict(summary['source_counts']),'schema_version':'midas-analytics-v1','ok':True,'receipt_present':True},
            'snapshot':{'snapshot_id':1,'snapshot_time':'2026-09-08T00:00:01Z','scope':'batch_commit'}}


class Transaction:
    def __init__(self, con):
        self.con = con

    async def __aenter__(self):
        self.snapshot = deepcopy(self.con.data)
        self.con.events.append("begin")

    async def __aexit__(self, kind, value, trace):
        if kind:
            self.con.data = self.snapshot
            self.con.events.append("rollback")
        else:
            self.con.events.append("commit")


class Connection:
    def __init__(self, items):
        self.data = {"captures": {cid: {"capture_id": cid, "source_kind": "fixture", "evidence_kind": "original_response",
            "normalization_state": "pending", "available_at": None} for cid, _ in items},
            "runs": {}, "facts": {}, "fields": [], "outbox": {cid: None for cid, _ in items}}
        self.events = []
        self.copies = []
        self.clocks = 0
        self.fail_publish = False
        self.fail_copy = None

    def transaction(self):
        return Transaction(self)

    async def fetchval(self, sql, *args):
        assert "clock_timestamp()" in sql
        self.clocks += 1
        return datetime(2026, 9, 8, tzinfo=timezone.utc) + timedelta(seconds=self.clocks)

    async def fetch(self, sql, *args):
        if sql.startswith("SELECT capture_id,source_kind"):
            return [self.data["captures"][cid] for cid in args[0] if cid in self.data["captures"]]
        if "INSERT INTO normalization_runs" in sql:
            result = []
            for cid, version, batch, nf, nc, issues, digest in zip(*args):
                key = cid, version
                if key not in self.data["runs"]:
                    row = {"capture_id": cid, "normalizer_version": version, "batch_id": batch,
                        "content_sha256": digest, "fact_count": nf, "field_count": nc, "issues": issues,
                        "available_at": None, "analytics_applied_at": None, "analytics_receipt": None,
                        "analytics_retry_at":None,"analytics_attempts":0,"analytics_last_error":None}
                    self.data["runs"][key] = row
                    result.append(row)
            return result
        if sql.startswith("SELECT r.capture_id"):
            return [self.data["runs"][key] for key in zip(*args) if key in self.data["runs"]]
        if "UPDATE normalization_runs r SET available_at" in sql:
            result = []
            for key in zip(args[0], args[1]):
                if key in self.data["runs"]:
                    row = self.data["runs"][key]
                    row["available_at"] = row["available_at"] or args[2]
                    result.append(row)
            return result
        if "SET analytics_applied_at" in sql:
            result = []
            for batch_id, receipt in zip(args[0], args[1]):
                for row in self.data["runs"].values():
                    if row["batch_id"] == batch_id:
                        row.update(analytics_applied_at=args[2], analytics_receipt=receipt, analytics_retry_at=None, analytics_last_error=None)
                        result.append(row)
            return result
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            assert "pg_advisory_xact_lock(60608003)" in sql and "shared" not in sql
            self.events.append("publication_lock")
            if self.fail_publish:
                raise RuntimeError("injected publication failure")
        elif "normalization_state=input.state" in sql:
            for cid, state in zip(*args):
                self.data["captures"][cid]["normalization_state"] = state
        elif "UPDATE captures SET available_at" in sql:
            for cid in args[0]:
                row = self.data["captures"][cid]
                row["available_at"] = row["available_at"] or args[1]
        elif "UPDATE facts f SET available_at" in sql:
            for row in self.data["facts"].values():
                key = row["capture_id"], row["normalizer_version"]
                if key in set(zip(*args)) and row["available_at"] is None:
                    row["available_at"] = self.data["runs"][key]["available_at"]
        elif "UPDATE outbox SET consumed_at" in sql:
            assert self.events.count("commit") >= 1
            for cid in args[0]:
                self.data["outbox"][cid] = self.data["outbox"][cid] or args[1]
        else:
            raise AssertionError(sql)

    async def copy_records_to_table(self, table, *, records, columns):
        self.copies.append((table, len(records)))
        if self.fail_copy == table:
            raise RuntimeError("injected COPY failure")
        for record in records:
            row = dict(zip(columns, record))
            if table == "facts":
                if row["fact_id"] in self.data["facts"]:
                    raise asyncpg.UniqueViolationError("duplicate fixture fact")
                self.data["facts"][row["fact_id"]] = {**row, "available_at": None}
            else:
                self.data["fields"].append(row)


class Acquire:
    def __init__(self, con):
        self.con = con

    async def __aenter__(self):
        return self.con

    async def __aexit__(self, *args):
        pass


class Pool:
    def __init__(self, con):
        self.con = con
        self.acquires = 0

    def acquire(self):
        self.acquires += 1
        return Acquire(self.con)

    async def fetchrow(self, sql, *args):
        assert 'SET analytics_retry_at=statement_timestamp()' in sql
        for row in self.con.data['runs'].values():
            if row['batch_id']==args[0] and row['available_at'] is not None and row['analytics_applied_at'] is None:
                now=await self.con.fetchval('SELECT clock_timestamp()')
                row['analytics_retry_at']=now+timedelta(seconds=min(60,2**min(row['analytics_attempts'],6)))
                row['analytics_attempts']+=1
                row['analytics_last_error']=args[1]
                return {k:row[k] for k in ('batch_id','analytics_retry_at','analytics_attempts','analytics_last_error')}
        return None


def fake_store(items):
    store = Store("unused fixture DSN", None)
    con = Connection(items)
    store.pool = Pool(con)
    return store, con


def run(awaitable):
    return asyncio.run(awaitable)


def test_eight_outputs_use_one_content_commit_two_copies_and_one_publication():
    items = [item() for _ in range(8)]
    store, con = fake_store(items)
    replies = run(store.record_normalizations(items))
    assert [r["batch_id"] for r in replies] == [hashlib.sha256((str(cid) + ":" + result.normalizer_version).encode()).hexdigest() for cid, result in items]
    assert con.events == ["begin", "commit", "begin", "publication_lock", "commit"]
    assert con.clocks == 1 and store.pool.acquires == 1
    assert [name for name, _ in con.copies] == ["facts", "field_observations"]
    assert len(con.data["facts"]) == 16 and len(con.data["runs"]) == 8
    markers = {row["available_at"] for row in con.data["runs"].values()}
    assert len(markers) == 1 and None not in markers
    assert set(con.data["outbox"].values()) == markers
    assert {r["available_at"] for r in con.data["facts"].values()} == markers
    assert all(r["source_kind"] == "fixture" and r["evidence_kind"] == "original_response" for r in con.data["facts"].values())
    assert {r["numeric_value"] for r in con.data["facts"].values()} == {0, __import__('decimal').Decimal('1.2300')}


def test_individual_api_delegates_without_changing_reply_hash_or_output():
    one = item()
    store, con = fake_store([one])
    reply = run(store.record_normalization(*one))
    assert reply == run(store.record_normalizations([one]))[0]
    result = one[1]
    expected = hashlib.sha256(canonical({k: [v.model_dump(mode="json") for v in getattr(result, k)] for k in ("facts", "fields", "issues")}).encode()).hexdigest()
    assert con.data["runs"][(one[0], result.normalizer_version)]["content_sha256"] == expected
    assert len(con.copies) == 2  # Replay did not COPY a second time.


def test_mixed_replay_preserves_original_per_run_fact_capture_and_outbox_markers():
    first, second = item(), item()
    store, con = fake_store([first, second])
    run(store.record_normalization(*first))
    old = deepcopy(con.data)
    run(store.record_normalizations([first, second]))
    assert con.data["runs"][(first[0], first[1].normalizer_version)] == old["runs"][(first[0], first[1].normalizer_version)]
    assert con.data["captures"][first[0]]["available_at"] == old["captures"][first[0]]["available_at"]
    assert con.data["outbox"][first[0]] == old["outbox"][first[0]]
    assert all(con.data["facts"][fid] == row for fid, row in old["facts"].items())
    assert con.data["outbox"][second[0]] > con.data["outbox"][first[0]]


def test_immutable_conflict_rolls_back_every_new_run_in_the_group():
    first, second = item(), item()
    store, con = fake_store([first, second])
    run(store.record_normalization(*first))
    snapshot = deepcopy(con.data)
    changed = first[1].model_copy(update={"issues": [Issue(code="fixture", pointer="", detail="changed")]})
    with pytest.raises(CaptureConflict, match="immutable"):
        run(store.record_normalizations([second, (first[0], changed)]))
    assert con.data == snapshot and con.events[-1] == "rollback"


def test_copy_failure_rolls_back_all_runs_facts_catalogue_and_capture_states():
    items = [item(), item()]
    store, con = fake_store(items)
    snapshot = deepcopy(con.data)
    con.fail_copy = "field_observations"
    with pytest.raises(RuntimeError, match="COPY"):
        run(store.record_normalizations(items))
    assert con.data == snapshot
    assert con.clocks == 0 and "publication_lock" not in con.events


def test_lost_publication_leaves_unpublished_content_replayable_and_outbox_unconsumed():
    items = [item(), item()]
    store, con = fake_store(items)
    con.fail_publish = True
    with pytest.raises(RuntimeError, match="publication"):
        run(store.record_normalizations(items))
    assert len(con.data["runs"]) == 2 and len(con.data["facts"]) == 4
    assert all(r["available_at"] is None for r in con.data["runs"].values())
    assert all(v is None for v in con.data["outbox"].values())
    con.fail_publish = False
    run(store.record_normalizations(items))
    assert len(con.copies) == 2
    assert all(v is not None for v in con.data["outbox"].values())


@pytest.mark.parametrize('code,state', [('unsupported_endpoint','quarantined'),('invalid_json','quarantined'),('invalid_field','ready')])
def test_group_keeps_issue_quarantine_policy(code, state):
    cid = uuid4()
    result = NormalizationResult(normalizer_version='fixture-policy', issues=[Issue(code=code, pointer='', detail='fixture')])
    store, con = fake_store([(cid,result)])
    reply = run(store.record_normalizations([(cid,result)]))[0]
    assert con.data['captures'][cid]['normalization_state'] == state
    assert reply['issues'] == [result.issues[0].model_dump(mode='json')]


@pytest.mark.parametrize('count', [0,9])
def test_group_count_rejects_before_database(count):
    items=[item() for _ in range(count)]
    store,con=fake_store(items)
    with pytest.raises(ValueError,match='count_limit'):
        run(store.record_normalizations(items))
    assert store.pool.acquires==0


def test_duplicate_capture_ids_rejected_even_with_different_versions():
    one=item()
    changed=one[1].model_copy(update={'normalizer_version':'different-version'})
    store,con=fake_store([one])
    with pytest.raises(ValueError,match='duplicate_capture'):
        run(store.record_normalizations([one,(one[0],changed)]))
    assert store.pool.acquires==0


def test_total_fact_limit_is_checked_before_database():
    one=item()
    many=one[1].model_copy(update={'facts':[one[1].facts[0]]*10001})
    store,con=fake_store([one])
    with pytest.raises(ValueError,match='fact_count_limit'):
        run(store.record_normalizations([(one[0],many)]))
    assert store.pool.acquires==0


def test_size_helper_counts_utf8_envelope_exactly_and_keeps_issues_outside_budget():
    cid,result=item()
    field=FieldObservation(pointer='/x',category='unknown',value_type='string',quality='unverified',reason='é😀')
    result=result.model_copy(update={'fields':[field], 'issues':[Issue(code='fixture',pointer='',detail='x'*1000)]})
    envelope={k:[v.model_dump(mode='json') for v in getattr(result,k)] for k in ('facts','fields')}
    assert Store.normalization_size(result)==len(canonical(envelope).encode('utf-8'))
    assert Store.normalization_size(result)>len(canonical(envelope))


def test_exact_single_capture_16mib_limit_accepted_and_aggregate_plus_one_rejected():
    cap=16*1024*1024
    field=FieldObservation(pointer='/x',category='unknown',value_type='string',quality='unverified',reason='x')
    base=NormalizationResult(normalizer_version='fixture-size',fields=[field])
    padding=cap-Store.normalization_size(base)
    exact=base.model_copy(update={'fields':[field.model_copy(update={'reason':'x'*(padding+1)})]})
    cid=uuid4(); store,con=fake_store([(cid,exact)])
    assert Store.normalization_size(exact)==cap
    assert run(store.record_normalization(cid,exact))['field_count']==1
    other=item(); store,con=fake_store([(cid,exact),other])
    with pytest.raises(ValueError,match='output_size_limit'):
        run(store.record_normalizations([(cid,exact),other]))
    assert store.pool.acquires==0
    too_big=exact.model_copy(update={'fields':[field.model_copy(update={'reason':'x'*(padding+2)})]})
    with pytest.raises(ValueError,match='output_size_limit'):
        run(store.record_normalization(cid,too_big))
    assert store.pool.acquires==0


def test_analytics_group_is_atomic_and_single_wrapper_compatible():
    items=[item(),item()];store,con=fake_store(items)
    replies=run(store.record_normalizations(items))
    receipts={r['batch_id']:full_receipt(r['batch_id'],r['fact_count']) for r in replies}
    before=len(con.events)
    run(store.mark_analytics_many(receipts))
    assert con.events[before:]==['begin','commit']
    markers={r['analytics_applied_at'] for r in con.data['runs'].values()}
    assert len(markers)==1 and None not in markers
    assert all(r['analytics_receipt']==canonical(receipts[r['batch_id']]) for r in con.data['runs'].values())
    batch=replies[0]['batch_id']
    run(store.mark_analytics(batch,receipts[batch]))
    assert next(r for r in con.data['runs'].values() if r['batch_id']==batch)['analytics_receipt']==canonical(receipts[batch])


@pytest.mark.parametrize('receipt',[{'ok':False},{'ok':'yes'},{},None,{'ok':True,'bad':float('nan')}])
def test_every_analytics_receipt_is_validated_before_any_write(receipt):
    one=item();store,con=fake_store([one])
    with pytest.raises((ValueError,RuntimeError)):
        run(store.mark_analytics_many({'good':full_receipt('good'),'bad':receipt}))
    assert store.pool.acquires==0


def test_missing_analytics_batch_rolls_back_other_updates():
    one=item();store,con=fake_store([one])
    batch=run(store.record_normalization(*one))['batch_id']
    snapshot=deepcopy(con.data)
    with pytest.raises(CaptureConflict,match='missing'):
        run(store.mark_analytics_many({batch:full_receipt(batch),'missing':full_receipt('missing')}))
    assert con.data==snapshot


@pytest.mark.parametrize('count',[0,9])
def test_analytics_group_count_rejected_before_database(count):
    store,con=fake_store([])
    with pytest.raises(ValueError,match='count_limit'):
        run(store.mark_analytics_many({str(i):{'ok':True} for i in range(count)}))
    assert store.pool.acquires==0


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Requires isolated Docker PostgreSQL opt-in')
def test_postgres_group_copy_hash_conflict_publication_and_receipt_atomicity():
    run(_postgres_group_proof())


async def _postgres_group_proof():
    dsn=os.environ['DATA_DATABASE_DSN']
    target=urlsplit(dsn)
    assert target.hostname in {'postgres','localhost','127.0.0.1'} and target.path=='/midas_data'
    schema='store_batch_fixture_'+uuid4().hex
    assert re.fullmatch(r'store_batch_fixture_[a-f0-9]{32}',schema)
    admin=await asyncpg.connect(dsn,timeout=5)
    pool=None
    try:
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        await admin.execute(f'CREATE SCHEMA {schema}')
        pool=await asyncpg.create_pool(dsn,min_size=1,max_size=2,command_timeout=15,
            server_settings={'search_path':schema,'timezone':'UTC','statement_timeout':'15000'})
        async with pool.acquire() as con:
            # Real foundation constraints and foreign keys, isolated from runtime.
            schema_sql=Path(__file__).parents[1].joinpath('midas_data/migrations/001_foundation.sql').read_text(encoding='utf-8')
            await con.execute(schema_sql)
            await con.execute(Path(__file__).parents[1].joinpath('midas_data/migrations/005_analytics_retry.sql').read_text(encoding='utf-8'))
            await con.execute("INSERT INTO raw_objects(sha256,relative_path,size_bytes) VALUES($1,'fixture',0)",'0'*64)
            items=[item() for _ in range(5)]
            for cid,result in items:
                await con.execute("""INSERT INTO captures(capture_id,fingerprint,raw_sha256,metadata,source_kind,evidence_kind,endpoint,chain,received_at)
                    VALUES($1,'fixture',$2,'{}','fixture','original_response','token_info','base',clock_timestamp())""",cid,'0'*64)
                await con.execute('INSERT INTO outbox(capture_id) VALUES($1)',cid)
        store=Store('unused',None);store.pool=pool
        replies=await store.record_normalizations(items[:2])
        markers=await pool.fetch('SELECT available_at FROM normalization_runs ORDER BY capture_id')
        assert len({r['available_at'] for r in markers})==1 and markers[0]['available_at'] is not None
        original=await pool.fetchval('SELECT available_at FROM normalization_runs WHERE capture_id=$1',items[0][0])
        await store.record_normalizations([items[0],items[2]])
        assert await pool.fetchval('SELECT available_at FROM normalization_runs WHERE capture_id=$1',items[0][0])==original
        assert await pool.fetchval('SELECT count(*) FROM facts')==6
        modified=items[0][1].model_copy(update={'issues':[Issue(code='fixture-change',pointer='',detail='changed')]})
        with pytest.raises(CaptureConflict,match='immutable'):
            await store.record_normalizations([items[3],(items[0][0],modified)])
        assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE capture_id=$1',items[3][0])==0
        assert await pool.fetchval('SELECT consumed_at FROM outbox WHERE capture_id=$1',items[3][0]) is None
        duplicate=items[4][1].model_copy(update={'facts':[items[3][1].facts[0]]})
        with pytest.raises(asyncpg.UniqueViolationError):
            await store.record_normalizations([items[3],(items[4][0],duplicate)])
        assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE capture_id=ANY($1::uuid[])',[items[3][0],items[4][0]])==0
        assert await pool.fetchval('SELECT count(*) FROM facts')==6
        receipts={r['batch_id']:{**full_receipt(r['batch_id'],r['fact_count']),'fixture':'precisão é😀'} for r in replies}
        await store.mark_analytics_many(receipts)
        before=await pool.fetch('SELECT batch_id,analytics_applied_at,analytics_receipt FROM normalization_runs WHERE batch_id=ANY($1::text[]) ORDER BY batch_id',list(receipts))
        assert len({r['analytics_applied_at'] for r in before})==1
        assert all(json.loads(r['analytics_receipt'])==receipts[r['batch_id']] for r in before)
        with pytest.raises(CaptureConflict,match='missing'):
            await store.mark_analytics_many({replies[0]['batch_id']:full_receipt(replies[0]['batch_id']),'missing-fixture':full_receipt('missing-fixture')})
        after=await pool.fetch('SELECT batch_id,analytics_applied_at,analytics_receipt FROM normalization_runs WHERE batch_id=ANY($1::text[]) ORDER BY batch_id',list(receipts))
        assert after==before
        # Eight deferred old runs must not hide an untouched ninth member.
        nine=[item() for _ in range(9)]
        async with pool.acquire() as con:
            for cid,result in nine:
                await con.execute("""INSERT INTO captures(capture_id,fingerprint,raw_sha256,metadata,source_kind,evidence_kind,endpoint,chain,received_at)
                    VALUES($1,'fixture',$2,'{}','fixture','original_response','token_info','base',clock_timestamp())""",cid,'0'*64)
                await con.execute('INSERT INTO outbox(capture_id) VALUES($1)',cid)
        groups=await store.record_normalizations(nine[:8])
        ninth=await store.record_normalization(*nine[8])
        for reply in groups:
            before_clock=await pool.fetchval('SELECT clock_timestamp()')
            deferred=await store.defer_analytics(reply['batch_id'],'BatchConflict')
            after_clock=await pool.fetchval('SELECT clock_timestamp()')
            assert before_clock+timedelta(seconds=1)<=deferred['analytics_retry_at']<=after_clock+timedelta(seconds=1)
        eligible=await pool.fetch("""SELECT batch_id FROM normalization_runs WHERE capture_id=ANY($1::uuid[])
            AND available_at IS NOT NULL AND analytics_applied_at IS NULL
            AND (analytics_retry_at IS NULL OR analytics_retry_at<=statement_timestamp())
            ORDER BY analytics_retry_at NULLS FIRST,recorded_at,batch_id LIMIT 8""",[cid for cid,_ in nine])
        assert eligible and eligible[0]['batch_id']==ninth['batch_id']
        await store.mark_analytics_many({groups[0]['batch_id']:full_receipt(groups[0]['batch_id'])})
        cleared=await pool.fetchrow('SELECT analytics_attempts,analytics_retry_at,analytics_last_error FROM normalization_runs WHERE batch_id=$1',groups[0]['batch_id'])
        assert cleared['analytics_attempts']==1 and cleared['analytics_retry_at'] is None and cleared['analytics_last_error'] is None
    finally:
        if pool:
            await pool.close()
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        assert re.fullmatch(r'store_batch_fixture_[a-f0-9]{32}',schema)
        await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await admin.close()


@pytest.mark.parametrize('path,value',[
    (('batch_id',),'other'),(('lake','ok'),False),(('clickhouse','receipt_present'),False),
    (('count',),True),(('lake','count'),3),(('clickhouse','checksum'),'b'*64),
    (('lake','source_counts'),{'gmgn':2}),(('lake','available_frontier'),'2026-09-08T00:00:02Z'),
    (('snapshot','snapshot_id'),True),(('snapshot','scope'),'latest'),
    (('snapshot','snapshot_time'),'2026-09-08T00:00:01'),(('snapshot',),None),
    (('lake','schema_version'),'unrecognized'),
])
def test_complete_receipt_contract_rejects_mismatch_before_write(path,value):
    one=item();store,con=fake_store([one]);receipt=full_receipt('fixture')
    target=receipt
    for key in path[:-1]: target=target[key]
    target[path[-1]]=value
    with pytest.raises(RuntimeError,match='receipt'):
        run(store.mark_analytics_many({'fixture':receipt}))
    assert store.pool.acquires==0


def test_frontier_comparison_normalizes_offsets_and_snapshot_requires_aware_clock():
    receipt=full_receipt('fixture')
    receipt['lake']['available_frontier']='2026-09-07T21:00:00-03:00'
    Store._validate_analytics_receipt('fixture',receipt)


def test_receipt_count_must_match_immutable_run_and_rolls_back():
    one=item();store,con=fake_store([one]);batch=run(store.record_normalization(*one))['batch_id']
    before=deepcopy(con.data)
    with pytest.raises(CaptureConflict,match='count'):
        run(store.mark_analytics_many({batch:full_receipt(batch,3)}))
    assert con.data==before


def test_retry_backoff_is_capped_and_success_clears_error_without_resetting_attempts():
    one=item();store,con=fake_store([one]);batch=run(store.record_normalization(*one))['batch_id']
    for attempt,delay in enumerate([1,2,4,8,16,32,60,60],1):
        result=run(store.defer_analytics(batch,'BatchConflict'))
        now=datetime(2026,9,8,tzinfo=timezone.utc)+timedelta(seconds=con.clocks)
        assert result['analytics_attempts']==attempt
        assert (result['analytics_retry_at']-now).total_seconds()==delay
    run(store.mark_analytics_many({batch:full_receipt(batch)}))
    row=next(iter(con.data['runs'].values()))
    assert row['analytics_attempts']==8 and row['analytics_retry_at'] is None and row['analytics_last_error'] is None
    assert run(store.defer_analytics(batch,'LateError')) is None


@pytest.mark.parametrize('code',['ValueError: secret body','https://secret.example', 'x'*65, None])
def test_retry_stores_only_sanitized_exception_class_codes(code):
    one=item();store,con=fake_store([one]);batch=run(store.record_normalization(*one))['batch_id']
    assert run(store.defer_analytics(batch,code))['analytics_last_error']=='AnalyticsError'


def test_retry_does_not_publish_unknown_or_unpublished_run():
    one=item();store,con=fake_store([one]);con.fail_publish=True
    with pytest.raises(RuntimeError):run(store.record_normalization(*one))
    row=next(iter(con.data['runs'].values()))
    assert run(store.defer_analytics(row['batch_id'],'BatchConflict')) is None
    assert run(store.defer_analytics('missing','BatchConflict')) is None
