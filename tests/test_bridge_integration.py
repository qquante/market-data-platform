"""Opt-in bridge proofs using real SQL fixtures and the real local capture API.

Only a unique bridge_fixture_* schema in midas_data is created/dropped.
The legacy database and Docker lifecycle are never accessed here.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import zlib

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("DATA_INTEGRATION") != "1", reason="Real bridge proofs are opt-in"),
]
UTC = timezone.utc


class _BridgeFixture(SimpleNamespace):
    """Pytest may render fixture arguments even without --showlocals."""

    def __repr__(self):
        return "BridgeFixture(<redacted>)"


def _env():
    dsn = os.environ["DATA_DATABASE_DSN"]
    parsed = urlsplit(dsn)
    assert parsed.hostname in {"postgres", "localhost", "127.0.0.1"}
    assert parsed.path == "/midas_data", "Fixture schemas are restricted to midas_data"
    api = os.getenv("DATA_TEST_API_URL", "http://api:8791")
    target = urlsplit(api)
    assert target.scheme == "http" and target.hostname in {"api", "localhost", "127.0.0.1"}
    assert target.port == 8791 and not target.username and not target.password
    assert not target.query and not target.fragment and target.path in {"", "/"}
    return dsn, api, os.environ["DATA_API_KEY"]


@asynccontextmanager
async def _fixture(*, age_minutes=3):
    import asyncpg
    from midas_data.bridge import Bridge, BridgeConfig, PgSource

    dsn, api, key = _env()
    suffix = uuid4().hex
    schema = "bridge_fixture_" + suffix
    assert re.fullmatch(r"bridge_fixture_[0-9a-f]{32}", schema)
    writer = await asyncpg.connect(dsn, command_timeout=10)
    source_connection = destination = second = None
    created = False
    try:
        assert await writer.fetchval("SELECT current_database()") == "midas_data"
        await writer.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await writer.execute(f"""
            CREATE TABLE "{schema}".gmgn_payload_blobs (
                payload_sha256 text PRIMARY KEY, codec text NOT NULL,
                raw_bytes bigint NOT NULL, compressed_bytes bigint NOT NULL, payload bytea NOT NULL
            );
            CREATE TABLE "{schema}".gmgn_api_calls (
                call_id uuid PRIMARY KEY, endpoint text NOT NULL, chain text,
                requested_at_utc timestamptz NOT NULL, received_at_utc timestamptz NOT NULL,
                status text NOT NULL CHECK(status IN ('success','error')),
                http_status integer, response_sha256 text, payload_sha256 text,
                request_params jsonb NOT NULL DEFAULT '{{}}'::jsonb
            );
            CREATE INDEX calls_success_time_idx ON "{schema}".gmgn_api_calls(received_at_utc)
                WHERE status='success';
            CREATE INDEX calls_errors_time_idx ON "{schema}".gmgn_api_calls(received_at_utc)
                WHERE status='error';
        """)
        second = await asyncpg.connect(dsn, command_timeout=10)
        source_connection = await asyncpg.connect(
            dsn, command_timeout=10,
            server_settings={"search_path": schema + ",public", "default_transaction_read_only": "on"},
        )
        assert await source_connection.fetchval("SHOW transaction_read_only") == "on"
        destination = await asyncpg.connect(dsn, command_timeout=10)
        origin = await writer.fetchval("SELECT clock_timestamp()") - timedelta(minutes=age_minutes)

        class CountedSource(PgSource):
            def __init__(self, connection):
                super().__init__(connection)
                self.pages = self.blobs = 0

            async def page(self, *args):
                self.pages += 1
                return await super().page(*args)

            async def blob(self, *args):
                self.blobs += 1
                return await super().blob(*args)

        source = CountedSource(source_connection)
        config = BridgeConfig(
            source_name="fixture:" + suffix, origin_at=origin,
            binding="midas_data/" + schema, source_kind="fixture",
        )
        async with httpx.AsyncClient(
            base_url=api, headers={"authorization": "Bearer " + key},
            timeout=httpx.Timeout(12, connect=3), trust_env=False,
        ) as client:
            replies = []

            async def admission():
                reply = await client.get("/v1/admission")
                reply.raise_for_status()
                return reply.json()

            async def deliver(capture):
                reply = await client.post(
                    "/v1/captures",
                    headers={"x-midas-metadata": capture.metadata_header(),
                             "content-type": "application/octet-stream"},
                    content=capture.raw,
                )
                assert reply.status_code == 202, reply.text
                value = reply.json()
                replies.append(value)
                return value

            bridge = await Bridge(config, destination, source, deliver, admission).open()
            yield _BridgeFixture(
                dsn=dsn, schema=schema, writer=writer, second=second,
                db=destination, source=source, config=config, bridge=bridge,
                client=client, replies=replies, deliver=deliver, admission=admission, origin=origin,
            )
    finally:
        for connection in (source_connection, destination, second):
            if connection is not None and not connection.is_closed():
                await connection.close()
        if created:
            assert await writer.fetchval("SELECT current_database()") == "midas_data"
            assert re.fullmatch(r"bridge_fixture_[0-9a-f]{32}", schema)
            await writer.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await writer.close()


async def _insert(f, *, at=None, call_id=None, status="success", chain="base",
                  retain=True, bad_hash=False, connection=None):
    connection = connection or f.writer
    at = at or f.origin + timedelta(seconds=10)
    call_id = call_id or uuid4()
    token = "0x" + uuid4().hex + uuid4().hex[:8]
    raw = json.dumps(
        {"data": {"address": token, "price": {"price": "0.123456789012345678901234567890123456789"}}},
        separators=(",", ":"),
    ).encode()
    sha = hashlib.sha256(raw).hexdigest()
    payload_sha = sha if status == "success" and retain else None
    if payload_sha:
        packed = zlib.compress(raw)
        await connection.execute(
            f'INSERT INTO "{f.schema}".gmgn_payload_blobs VALUES($1,$2,$3,$4,$5)',
            sha, "zlib", len(raw), len(packed), packed,
        )
    await connection.execute(
        f'INSERT INTO "{f.schema}".gmgn_api_calls VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)',
        call_id, "token_info", chain, at - timedelta(seconds=1), at, status,
        200 if status == "success" else 429, "0" * 64 if bad_hash else sha, payload_sha,
        json.dumps({"address": token}),
    )
    return SimpleNamespace(id=call_id, raw=raw, sha=sha, token=token, at=at)


async def _state(f):
    row = dict(await f.db.fetchrow("SELECT * FROM bridge_state WHERE source_name=$1", f.config.source_name))
    row["progress"] = json.loads(row["progress"])
    return row


async def _ledger(f, call_id):
    row = await f.db.fetchrow(
        "SELECT * FROM bridge_delivery WHERE source_name=$1 AND capture_id=$2",
        f.config.source_name, call_id,
    )
    return dict(row) if row else None


async def _ready(f, call_id):
    deadline = asyncio.get_running_loop().time() + 120
    while asyncio.get_running_loop().time() < deadline:
        response = await f.client.get(f"/v1/captures/{call_id}")
        assert response.status_code == 200
        capture = response.json()
        if capture["runs"] and all(
            run["available_at"] and run["analytics_applied_at"] and run["analytics_receipt"]["ok"]
            for run in capture["runs"]
        ):
            assert capture["source_kind"] == "fixture"
            return capture
        await asyncio.sleep(0.2)
    pytest.fail("Fixture capture did not reach both verified analytic destinations")


def test_source_union_paginates_both_statuses_with_more_than_fifty_equal_timestamps():
    asyncio.run(_source_paging())


async def _source_paging():
    from midas_data.bridge import PAGE_SQL, SAFE_PARAMS
    async with _fixture() as f:
        stamp = f.origin + timedelta(seconds=20)
        ids = []
        for index in range(113):
            row = await _insert(
                f, at=stamp, call_id=UUID(int=index + 1),
                status="success" if index % 2 else "error", retain=False,
            )
            ids.append(row.id)
        all_rows, at, after = [], f.origin, None
        sizes = []
        for _ in range(4):
            page = await f.source.page(at, after, f.origin + timedelta(seconds=60))
            sizes.append(len(page))
            all_rows.extend(page)
            if len(page) < 50:
                break
            at, after = page[-1]["received_at_utc"], page[-1]["call_id"]
        assert sizes == [50, 50, 13]
        assert [row["call_id"] for row in all_rows] == ids
        assert {row["status"] for row in all_rows} == {"success", "error"}
        assert f.source.blobs == 0
        assert await f.source.page(f.origin + timedelta(seconds=60), None, f.origin + timedelta(seconds=120)) == []
        # A real EXPLAIN verifies both partial indexes remain usable. Small
        # fixtures otherwise favor sequential scans, so disable that cost choice.
        async with f.source.connection.transaction(readonly=True):
            await f.source.connection.execute("SET LOCAL enable_seqscan=off")
            plan = await f.source.connection.fetch(
                "EXPLAIN (FORMAT TEXT) " + PAGE_SQL,
                f.origin, None, f.origin + timedelta(seconds=60), 50, list(SAFE_PARAMS),
            )
        plan_text = "\n".join(row[0] for row in plan)
        assert "calls_success_time_idx" in plan_text
        assert "calls_errors_time_idx" in plan_text


def test_empty_windows_advance_and_cyclic_audit_recovers_a_late_commit_behind_every_cursor():
    asyncio.run(_late_commit_audit())


async def _late_commit_audit():
    async with _fixture(age_minutes=15) as f:
        transaction = f.second.transaction()
        await transaction.start()
        committed = False
        try:
            late = await _insert(f, at=f.origin + timedelta(seconds=5), connection=f.second)
            known = await _insert(f, at=f.origin + timedelta(seconds=20))
            for _ in range(150):
                await f.bridge.step()
                state = await _state(f)
                progress = state["progress"]
                forward = datetime.fromisoformat(progress["forward_until"])
                recent = progress["lanes"]["recent"]
                audit = progress["lanes"]["audit"]
                if (
                    forward >= f.origin + timedelta(minutes=12)
                    and datetime.fromisoformat(recent["at"]) > late.at
                    and datetime.fromisoformat(audit["at"]) > late.at
                    and audit["cycles"] >= 1
                ):
                    break
            else:
                pytest.fail("Forward/reconciliation lanes did not advance over bounded empty windows")
            assert (await _ledger(f, known.id))["state"] == "accepted"
            assert await _ledger(f, late.id) is None
            await transaction.commit()
            committed = True
            discovered_by = None
            for _ in range(150):
                info = await f.bridge.step()
                receipt = await _ledger(f, late.id)
                if receipt is not None:
                    discovered_by = info["lane"]
                    assert receipt["state"] == "accepted"
                    break
            assert discovered_by == "audit", "An old late commit must be found by the full cyclic audit"
            assert len(f.replies) == 2
            assert f.source.blobs == 2, "ACKed rereads must not fetch the blob again"
            await _ready(f, known.id)
            await _ready(f, late.id)
        finally:
            if not committed:
                await transaction.rollback()


def test_lost_ack_keeps_checkpoint_and_restarts_with_the_same_capture_identity():
    asyncio.run(_lost_ack())


async def _lost_ack():
    import asyncpg
    from midas_data.bridge import Bridge
    from midas_data.importer import ImportFailure
    async with _fixture() as f:
        call = await _insert(f)
        before = (await _state(f))["progress"]

        async def accepted_but_ack_lost(capture):
            await f.deliver(capture)
            raise ImportFailure("fixture_ack_lost_after_real_acceptance")

        f.bridge.deliver = accepted_but_ack_lost
        with pytest.raises(ImportFailure, match="fixture_ack_lost"):
            await f.bridge.step()
        assert (await _state(f))["progress"] == before
        assert await _ledger(f, call.id) is None
        assert f.replies[0]["duplicate"] is False
        await f.db.close()  # Real connection/session loss, not Docker control.
        f.db = await asyncpg.connect(f.dsn, command_timeout=10)
        try:
            resumed = await Bridge(f.config, f.db, f.source, f.deliver, f.admission).open()
            await resumed.step()
            assert (await _ledger(f, call.id))["state"] == "accepted"
            assert (await _state(f))["progress"] != before
            assert [reply["capture_id"] for reply in f.replies] == [str(call.id), str(call.id)]
            assert [reply["duplicate"] for reply in f.replies] == [False, True]
            capture = await _ready(f, call.id)
            assert len(capture["runs"]) == 1
            raw = await f.client.get(f"/v1/captures/{call.id}/raw")
            assert raw.status_code == 200 and raw.content == call.raw
        finally:
            await f.db.close()


def test_missing_body_metadata_and_hash_errors_are_visible_and_do_not_hide_good_calls():
    asyncio.run(_visible_exceptions())


async def _visible_exceptions():
    async with _fixture() as f:
        no_body = await _insert(f, retain=False)
        error = await _insert(f, status="error")
        no_chain = await _insert(f, chain=None)
        bad_hash = await _insert(f, bad_hash=True)
        good = await _insert(f)
        await f.bridge.step()
        expected = (
            (no_body.id, "unavailable", "source_body_not_retained"),
            (error.id, "unavailable", "source_error_without_body"),
            (no_chain.id, "rejected", "invalid_capture_metadata"),
            (bad_hash.id, "rejected", "raw_sha256_mismatch"),
        )
        for call_id, status, reason in expected:
            row = await _ledger(f, call_id)
            assert row["state"] == status and row["reason"] == reason
            assert (await f.client.get(f"/v1/captures/{call_id}")).status_code == 404
        state = await _state(f)
        assert (state["accepted_count"], state["unavailable_count"], state["rejected_count"]) == (1, 2, 2)
        assert state["state"] == "degraded"
        assert state["progress"]["forward_until"] == (f.origin + timedelta(seconds=60)).isoformat()
        assert len(f.replies) == 1 and f.replies[0]["capture_id"] == str(good.id)
        await _ready(f, good.id)


def test_source_identity_mutation_is_blocked_without_overwriting_accepted_evidence():
    asyncio.run(_source_mutation())


async def _source_mutation():
    async with _fixture() as f:
        call = await _insert(f)
        await f.bridge.step()
        accepted = await _ledger(f, call.id)
        budget_before = (await _state(f))["accepted_raw_bytes"]
        await f.writer.execute(
            f'UPDATE "{f.schema}".gmgn_api_calls SET chain=$2 WHERE call_id=$1', call.id, "sol",
        )
        await f.bridge.step()  # recent lane rereads the same source identity
        rejected = await _ledger(f, call.id)
        assert rejected["state"] == "rejected"
        assert rejected["reason"] == "source_identity_changed"
        assert rejected["fingerprint"] == accepted["fingerprint"]
        assert rejected["ack_capture_id"] == call.id
        assert (await _state(f))["accepted_raw_bytes"] == budget_before
        assert len(f.replies) == 1 and f.source.blobs == 1
        original = await f.client.get(f"/v1/captures/{call.id}")
        assert original.status_code == 200
        assert original.json()["metadata"]["chain"] == "base"
        raw = await f.client.get(f"/v1/captures/{call.id}/raw")
        assert raw.content == call.raw
        await _ready(f, call.id)


def test_capture_continues_while_normalization_publication_is_blocked():
    asyncio.run(_capture_despite_backlog())


async def _capture_despite_backlog():
    async with _fixture() as f:
        call = await _insert(f)
        before = (await _state(f))["progress"]
        transaction = f.writer.transaction()
        await transaction.start()
        try:
            # Hold downstream publication: raw acceptance must stay independent.
            await f.writer.execute("SELECT pg_advisory_xact_lock(60608003)")
            token = "0x" + uuid4().hex + uuid4().hex[:8]
            stamp = datetime.now(UTC) - timedelta(seconds=1)
            capture_id = str(uuid4())
            meta = {
                "capture_id": capture_id, "source_kind": "fixture", "endpoint": "token_info",
                "chain": "base", "requested_at": (stamp - timedelta(seconds=1)).isoformat(),
                "received_at": stamp.isoformat(), "source_event_at": None, "http_status": 200,
                "request_params": {"address": token}, "evidence_kind": "original_response",
            }
            response = await f.client.post(
                "/v1/captures", headers={"x-midas-metadata": json.dumps(meta)},
                content=json.dumps({"data": {"address": token, "price": {"price": "1"}}}).encode(),
            )
            assert response.status_code == 202
            pages_before, blobs_before = f.source.pages, f.source.blobs
            info = await f.bridge.step()
            assert info["page"]["accepted"] == 1
            after = await _state(f)
            assert after["last_error"] is None and after["progress"] != before
            assert (await _ledger(f, call.id))["state"] == "accepted"
            assert f.source.pages == pages_before+1 and f.source.blobs == blobs_before+1
            assert len(f.replies) == 1
        finally:
            await transaction.rollback()
        await _ready(f, UUID(capture_id))


def test_global_raw_capture_preserves_missing_chain_without_inventing_token_facts():
    asyncio.run(_global_raw_capture())


async def _global_raw_capture():
    async with _fixture() as f:
        call_id = uuid4()
        stamp = f.origin + timedelta(seconds=10)
        raw = b'{"code":0,"data":[{"symbol":"FIXTURE_ONLY","searches":2}]}'
        sha = hashlib.sha256(raw).hexdigest()
        packed = zlib.compress(raw)
        await f.writer.execute(
            f'INSERT INTO "{f.schema}".gmgn_payload_blobs VALUES($1,$2,$3,$4,$5)',
            sha, "zlib", len(raw), len(packed), packed,
        )
        await f.writer.execute(
            f'INSERT INTO "{f.schema}".gmgn_api_calls VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)',
            call_id, "hot_searches", None, stamp - timedelta(seconds=1), stamp,
            "success", 200, sha, sha, "{}",
        )
        await f.bridge.step()
        receipt = await _ledger(f, call_id)
        assert receipt["state"] == "accepted" and receipt["reason"] is None
        assert receipt["raw_sha256"] == sha
        capture = await _ready(f, call_id)
        assert capture["chain"] is None
        assert capture["metadata"]["chain"] is None
        assert capture["metadata"]["endpoint"] == "hot_searches"
        assert capture["evidence_kind"] == "original_response"
        assert capture["normalization_state"] == "quarantined"
        assert len(capture["runs"]) == 1
        run = capture["runs"][0]
        assert run["fact_count"] == 0 and run["field_count"] > 0
        assert "unsupported_endpoint" in {issue["code"] for issue in run["issues"]}
        assert run["analytics_receipt"]["ok"] is True
        assert run["analytics_receipt"]["count"] == 0
        assert await f.db.fetchval("SELECT count(*) FROM facts WHERE capture_id=$1", call_id) == 0
        restored = await f.client.get(f"/v1/captures/{call_id}/raw")
        assert restored.status_code == 200 and restored.content == raw
        assert restored.headers["x-content-sha256"] == sha
