"""Real, opt-in recovery proofs for the isolated GMGN Docker foundation.

Only fixture captures and the deployment's midas_data database are used.
No service control, external provider, legacy source, or mocked database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("DATA_INTEGRATION") != "1",
        reason="Real recovery proofs require DATA_INTEGRATION=1",
    ),
]
UTC = timezone.utc
PUBLICATION_LOCK = 60608003


def _environment():
    api = os.getenv("DATA_TEST_API_URL", "http://api:8791")
    api_url = urlsplit(api)
    assert (
        api_url.scheme == "http"
        and api_url.hostname in {"api", "localhost", "127.0.0.1"}
        and api_url.port == 8791
        and not api_url.username
        and not api_url.password
        and not api_url.query
        and not api_url.fragment
        and api_url.path in {"", "/"}
    ), "Recovery API must be the isolated Compose/local endpoint"
    key = os.getenv("DATA_API_KEY")
    dsn = os.getenv("DATA_DATABASE_DSN")
    assert key and dsn, "Recovery requires the isolated deployment credentials"
    database = urlsplit(dsn)
    assert (
        database.scheme in {"postgresql", "postgres"}
        and database.hostname in {"postgres", "localhost", "127.0.0.1"}
        and database.path == "/midas_data"
    ), "Recovery is restricted to the midas_data operational database"
    return api, key, dsn


def _iso(value):
    return value.astimezone(UTC).isoformat()


def _now():
    return datetime.now(UTC)


def _capture(token, event_at, price):
    received = _now() - timedelta(seconds=1)
    meta = {
        "capture_id": str(uuid4()), "source_kind": "fixture",
        "endpoint": "token_info", "chain": "base",
        "requested_at": _iso(received - timedelta(milliseconds=100)),
        "received_at": _iso(received), "source_event_at": _iso(event_at),
        "http_status": 200, "request_params": {"address": token},
        "evidence_kind": "original_response",
    }
    raw = json.dumps(
        {"data": {"address": token, "price": {"price": price}}},
        separators=(",", ":"),
    ).encode()
    return meta, raw


async def _ingest(client, meta, raw):
    reply = await client.post(
        "/v1/captures",
        headers={"x-midas-metadata": json.dumps(meta), "content-type": "application/octet-stream"},
        content=raw,
    )
    assert reply.status_code == 202, reply.text
    assert reply.json()["capture_id"] == meta["capture_id"]


async def _ready(client, capture_id, *, analytics=False):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        reply = await client.get(f"/v1/captures/{capture_id}")
        assert reply.status_code == 200
        capture = reply.json()
        if capture["normalization_state"] == "ready" and capture["runs"]:
            runs = capture["runs"]
            if all(run["available_at"] for run in runs):
                if not analytics or all(
                    run["analytics_applied_at"] and run["analytics_receipt"]["ok"]
                    for run in runs
                ):
                    return capture
        await asyncio.sleep(0.3)
    pytest.fail(f"Fixture capture did not complete within the recovery deadline: {capture_id}")


def _query(token, cutoff):
    return {
        "chain": "base", "token": token, "source_kind": "fixture",
        "event_at": _iso(cutoff), "knowledge_cutoff": _iso(cutoff),
    }


async def _until(check, seconds, failure):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.025)
    pytest.fail(failure)


def test_temporal_read_waits_for_publication_and_preserves_the_prior_cut():
    asyncio.run(_temporal_barrier_proof())


async def _temporal_barrier_proof():
    import asyncpg
    from midas_data.contracts import CaptureMetadata
    from midas_data.normalize import normalize_capture
    from midas_data.raw_store import RawStore
    from midas_data.settings import Settings
    from midas_data.store import Store

    api, key, dsn = _environment()
    settings = Settings.from_env()
    store = await Store(dsn, RawStore(settings.raw_path, settings.max_capture_bytes)).open()
    control = None
    transaction = None
    query_task = writer_task = None
    try:
        control = await asyncpg.connect(
            dsn, command_timeout=6,
            server_settings={
                "application_name": "midas-data-qa-publication",
                "statement_timeout": "6000",
                "idle_in_transaction_session_timeout": "12000",
                "timezone": "UTC",
            },
        )
        assert await control.fetchval("SELECT current_database()") == "midas_data"
        token = "0x" + uuid4().hex + uuid4().hex[:8]
        event = _now() - timedelta(minutes=2)
        async with httpx.AsyncClient(
            base_url=api, headers={"authorization": "Bearer " + key},
            timeout=httpx.Timeout(12, connect=3), trust_env=False,
        ) as client:
            first, first_raw = _capture(token, event, "1.25")
            await _ingest(client, first, first_raw)
            await _ready(client, first["capture_id"], analytics=True)

            transaction = control.transaction()
            await transaction.start()
            await control.execute("SELECT pg_advisory_xact_lock($1::bigint)", PUBLICATION_LOCK)
            cutoff = await control.fetchval("SELECT clock_timestamp()")
            second, second_raw = _capture(token, event + timedelta(seconds=1), "2.5")
            await _ingest(client, second, second_raw)
            result = normalize_capture(CaptureMetadata.model_validate(second), second_raw)
            # Public Store writer, same immutable input as the normal worker.
            # It can commit facts but cannot publish them while the lock is held.
            writer_task = asyncio.create_task(
                store.record_normalization(UUID(second["capture_id"]), result)
            )

            async def facts_are_pending():
                return await control.fetchval(
                    "SELECT count(*)=1 AND bool_and(available_at IS NULL) "
                    "FROM facts WHERE capture_id=$1",
                    UUID(second["capture_id"]),
                )

            await _until(
                facts_are_pending, 4,
                "Writer did not reach committed facts with pending availability",
            )
            assert not writer_task.done(), "Publication unexpectedly bypassed the exclusive barrier"
            query_task = asyncio.create_task(client.get("/v1/as-known", params=_query(token, cutoff)))

            async def actual_api_reader_is_waiting():
                return await control.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid) "
                    "WHERE l.locktype='advisory' AND l.classid=0 AND l.objid=$1::oid "
                    "AND l.objsubid=1 AND l.mode='ShareLock' AND NOT l.granted "
                    "AND a.datname=current_database() AND a.application_name='midas-data')",
                    PUBLICATION_LOCK,
                )

            await _until(
                actual_api_reader_is_waiting, 3,
                "The HTTP temporal reader did not wait on the publication barrier",
            )
            assert not query_task.done(), "HTTP reader returned before publication was released"
            await transaction.commit()
            transaction = None

            await asyncio.wait_for(writer_task, timeout=8)
            reply = await asyncio.wait_for(query_task, timeout=8)
            assert reply.status_code == 200, reply.text
            original_items = reply.json()["items"]
            assert [(row["capture_id"], row["value_text"]) for row in original_items] == [
                (first["capture_id"], "1.25")
            ]
            # The new observation happened before event_at, but became
            # available after the fixed cutoff, so replay must still exclude it.
            repeated = await client.get("/v1/as-known", params=_query(token, cutoff))
            assert repeated.status_code == 200
            assert repeated.json()["items"] == original_items
            current_cutoff = await control.fetchval("SELECT clock_timestamp()")
            current = await client.get("/v1/as-known", params=_query(token, current_cutoff))
            assert current.status_code == 200, current.text
            assert [(row["capture_id"], row["value_text"]) for row in current.json()["items"]] == [
                (second["capture_id"], "2.5")
            ]
            await _ready(client, second["capture_id"], analytics=True)
    finally:
        if transaction is not None:
            await transaction.rollback()
        for task in (query_task, writer_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (query_task, writer_task) if task is not None),
            return_exceptions=True,
        )
        if control is not None:
            await control.close()
        await store.close()


def test_normalization_replay_is_immutable_and_conflicting_output_is_rejected():
    asyncio.run(_normalization_replay_proof())


async def _normalization_replay_proof():
    from midas_data.contracts import CaptureMetadata
    from midas_data.normalize import normalize_capture
    from midas_data.raw_store import RawStore
    from midas_data.settings import Settings
    from midas_data.store import CaptureConflict, Store

    api, key, dsn = _environment()
    settings = Settings.from_env()
    store = await Store(dsn, RawStore(settings.raw_path, settings.max_capture_bytes)).open()
    try:
        assert await store.pool.fetchval("SELECT current_database()") == "midas_data"
        token = "0x" + uuid4().hex + uuid4().hex[:8]
        meta, raw = _capture(token, _now() - timedelta(minutes=1), "0.123456789012345678901234567890123456789")
        async with httpx.AsyncClient(
            base_url=api, headers={"authorization": "Bearer " + key},
            timeout=httpx.Timeout(12, connect=3), trust_env=False,
        ) as client:
            await _ingest(client, meta, raw)
            before = await _ready(client, meta["capture_id"], analytics=True)
            cutoff = _now()
            before_query = await client.get("/v1/as-known", params=_query(token, cutoff))
            assert before_query.status_code == 200
            expected_facts = before_query.json()["items"]
            assert len(expected_facts) == 1
            result = normalize_capture(CaptureMetadata.model_validate(meta), raw)
            replies = await asyncio.gather(*(
                store.record_normalization(UUID(meta["capture_id"]), result)
                for _ in range(3)
            ))
            assert len({reply["batch_id"] for reply in replies}) == 1
            assert {reply["batch_id"] for reply in replies} == {before["runs"][0]["batch_id"]}
            after = await _ready(client, meta["capture_id"], analytics=True)
            assert len(after["runs"]) == 1
            assert after["runs"][0] == before["runs"][0]
            same_cut = await client.get("/v1/as-known", params=_query(token, cutoff))
            assert same_cut.status_code == 200
            assert same_cut.json()["items"] == expected_facts

            changed_raw = json.dumps(
                {"data": {"address": token, "price": {"price": "999"}}},
                separators=(",", ":"),
            ).encode()
            conflicting = normalize_capture(CaptureMetadata.model_validate(meta), changed_raw)
            assert conflicting.facts[0].fact_id == result.facts[0].fact_id
            assert conflicting.normalizer_version == result.normalizer_version
            with pytest.raises(CaptureConflict, match="immutable"):
                await store.record_normalization(UUID(meta["capture_id"]), conflicting)

            preserved = await _ready(client, meta["capture_id"], analytics=True)
            assert preserved["runs"] == before["runs"]
            evidence = await client.get(f"/v1/captures/{meta['capture_id']}/raw")
            assert evidence.status_code == 200
            assert evidence.content == raw
            final = await client.get("/v1/as-known", params=_query(token, cutoff))
            assert final.status_code == 200
            assert final.json()["items"] == expected_facts
    finally:
        await store.close()


def test_new_normalizer_version_replaces_current_interpretation_without_rewriting_history():
    asyncio.run(_normalization_revision_proof())


async def _normalization_revision_proof():
    from midas_data.contracts import CaptureMetadata
    from midas_data.normalize import normalize_capture
    from midas_data.raw_store import RawStore
    from midas_data.settings import Settings
    from midas_data.store import Store

    api, key, dsn = _environment()
    settings = Settings.from_env()
    store = await Store(dsn, RawStore(settings.raw_path, settings.max_capture_bytes)).open()
    try:
        assert await store.pool.fetchval("SELECT current_database()") == "midas_data"
        token = "0x" + uuid4().hex + uuid4().hex[:8]
        event = _now() - timedelta(minutes=1)
        meta, raw = _capture(token, event, "1.25")
        async with httpx.AsyncClient(
            base_url=api, headers={"authorization": "Bearer " + key},
            timeout=httpx.Timeout(12, connect=3), trust_env=False,
        ) as client:
            await _ingest(client, meta, raw)
            original = await _ready(client, meta["capture_id"], analytics=True)
            assert len(original["runs"]) == 1
            old_clock_reply = await client.get("/v1/clock")
            assert old_clock_reply.status_code == 200
            old_cut = datetime.fromisoformat(
                old_clock_reply.json()["knowledge_cutoff"].replace("Z", "+00:00")
            )
            original_query = await client.get("/v1/as-known", params=_query(token, old_cut))
            assert original_query.status_code == 200
            original_facts = original_query.json()["items"]
            assert len(original_facts) == 1
            old_fact = original_facts[0]
            assert old_fact["value_text"] == "1.25"

            normalized = normalize_capture(CaptureMetadata.model_validate(meta), raw)
            assert len(normalized.facts) == 1
            assert normalized.normalizer_version == original["runs"][0]["normalizer_version"]
            replacement_id = uuid4()
            replacement = normalized.facts[0].model_copy(update={
                "fact_id": replacement_id,
                "value_text": "9.75",
            })
            # This is an explicitly synthetic reinterpretation, not a provider
            # observation. The fixture's original bytes remain immutable.
            revised = normalized.model_copy(update={
                "normalizer_version": "fixture-v-next",
                "facts": [replacement],
            })
            await store.record_normalization(UUID(meta["capture_id"]), revised)
            current_capture = await _ready(client, meta["capture_id"], analytics=True)
            assert len(current_capture["runs"]) == 2
            assert {run["normalizer_version"] for run in current_capture["runs"]} == {
                normalized.normalizer_version, "fixture-v-next",
            }
            new_clock_reply = await client.get("/v1/clock")
            assert new_clock_reply.status_code == 200
            new_cut = datetime.fromisoformat(
                new_clock_reply.json()["knowledge_cutoff"].replace("Z", "+00:00")
            )
            assert new_cut > old_cut

            cases = (
                (old_cut, old_fact["fact_id"], normalized.normalizer_version, "1.25"),
                (new_cut, str(replacement_id), "fixture-v-next", "9.75"),
            )
            for cutoff, expected_id, expected_version, expected_price in cases:
                known = await client.get("/v1/as-known", params=_query(token, cutoff))
                assert known.status_code == 200, known.text
                window = await client.get("/v1/observations", params={
                    "chain": "base", "token": token, "metric": "price",
                    "start": _iso(event - timedelta(seconds=1)),
                    "end": _iso(event + timedelta(seconds=1)),
                    "knowledge_cutoff": _iso(cutoff),
                    "source_kind": "fixture",
                })
                assert window.status_code == 200, window.text
                for payload in (known.json(), window.json()):
                    rows = payload["items"]
                    assert len(rows) == 1, "A reprocessed capture must contribute one interpretation"
                    assert rows[0]["fact_id"] == expected_id
                    assert rows[0]["capture_id"] == meta["capture_id"]
                    assert rows[0]["normalizer_version"] == expected_version
                    assert rows[0]["value_text"] == expected_price
                    assert rows[0]["source_kind"] == "fixture"

            historical_replay = await client.get("/v1/as-known", params=_query(token, old_cut))
            assert historical_replay.status_code == 200
            assert historical_replay.json()["items"] == original_facts
            evidence = await client.get(f"/v1/captures/{meta['capture_id']}/raw")
            assert evidence.status_code == 200
            assert evidence.content == raw
    finally:
        await store.close()
