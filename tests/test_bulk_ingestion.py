"""Real opt-in proofs for bulk ingestion and binary COPY atomicity.

Only authenticated fixture captures in the isolated midas_data deployment are
created. No legacy database, external provider or Docker lifecycle is touched.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("DATA_INTEGRATION") != "1",
        reason="Bulk ingestion proofs require DATA_INTEGRATION=1",
    ),
]
UTC = timezone.utc


def _environment():
    dsn = os.environ["DATA_DATABASE_DSN"]
    database = urlsplit(dsn)
    assert database.hostname in {"postgres", "localhost", "127.0.0.1"}
    assert database.path == "/midas_data", "Bulk fixtures are restricted to midas_data"
    api = os.getenv("DATA_TEST_API_URL", "http://api:8791")
    target = urlsplit(api)
    assert target.scheme == "http" and target.hostname in {"api", "localhost", "127.0.0.1"}
    assert target.port == 8791 and not target.username and not target.password
    assert not target.query and not target.fragment and target.path in {"", "/"}
    return dsn, api, os.environ["DATA_API_KEY"]


def _metadata(endpoint, *, token=None):
    received = datetime.now(UTC) - timedelta(seconds=1)
    return {
        "capture_id": str(uuid4()), "source_kind": "fixture", "endpoint": endpoint,
        "chain": "base", "requested_at": (received - timedelta(seconds=1)).isoformat(),
        "received_at": received.isoformat(), "source_event_at": None, "http_status": 200,
        "request_params": {"address": token} if token else {},
        "evidence_kind": "original_response",
    }


async def _accept(client, meta, raw):
    response = await client.post(
        "/v1/captures",
        headers={"x-midas-metadata": json.dumps(meta, separators=(",", ":")),
                 "content-type": "application/octet-stream"},
        content=raw,
    )
    assert response.status_code == 202, response.text
    ack = response.json()
    assert ack["capture_id"] == meta["capture_id"]
    assert ack["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert ack["bytes"] == len(raw)
    return ack


async def _verified(client, capture_id):
    deadline = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/v1/captures/{capture_id}")
        assert response.status_code == 200
        capture = response.json()
        if capture["runs"] and all(
            run["available_at"] and run["analytics_applied_at"] and run["analytics_receipt"]["ok"]
            for run in capture["runs"]
        ):
            return capture
        await asyncio.sleep(0.3)
    pytest.fail(f"Bulk fixture did not reach both verified sinks within 180 seconds: {capture_id}")


async def _as_known(client, token):
    clock = await client.get("/v1/clock")
    assert clock.status_code == 200
    cutoff = clock.json()["knowledge_cutoff"]
    response = await client.get("/v1/as-known", params={
        "chain": "base", "token": token, "source_kind": "fixture",
        "event_at": cutoff, "knowledge_cutoff": cutoff,
    })
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_six_hundred_token_ranking_preserves_all_four_metrics_in_both_sinks():
    asyncio.run(_bulk_ranking())


async def _bulk_ranking():
    _, api, key = _environment()
    prefix = uuid4().hex
    rows = []
    for index in range(600):
        rows.append({
            "address": "0x" + prefix + f"{index:08x}",
            "price": "0." + "1234567890" * 5 + f"{index:06d}",
            "holder_count": 0,
            "name": f"Preço d'Água \"Ω\" 🚀 {index}",
            "symbol": f"Q'Ω{index}",
        })
    raw = json.dumps({"code": 0, "data": {"rank": rows}}, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    meta = _metadata("market_rank")
    async with httpx.AsyncClient(
        base_url=api, headers={"authorization": "Bearer " + key},
        timeout=httpx.Timeout(15, connect=3), trust_env=False,
    ) as client:
        ack = await _accept(client, meta, raw)
        assert ack["duplicate"] is False
        capture = await _verified(client, meta["capture_id"])
        assert capture["normalization_state"] == "ready"
        assert capture["source_kind"] == "fixture"
        assert len(capture["runs"]) == 1
        run = capture["runs"][0]
        assert run["issues"] == []
        assert run["fact_count"] == 600 * 4
        assert run["field_count"] > run["fact_count"]
        receipt = run["analytics_receipt"]
        assert receipt["ok"] is True
        assert receipt["count"] == 2400
        assert receipt["source_counts"] == {"fixture": 2400}
        for sink in ("lake", "clickhouse"):
            assert receipt[sink]["ok"] is True
            assert receipt[sink]["receipt_present"] is True
            assert receipt[sink]["count"] == 2400
            assert receipt[sink]["source_counts"] == {"fixture": 2400}
            assert receipt[sink]["checksum"] == receipt["checksum"]
        assert receipt["snapshot"]["scope"] == "batch_commit"
        catalog_reply = await client.get("/v1/catalog", params={
            "source_kind": "fixture", "capture_id": meta["capture_id"], "limit": 1000,
        })
        assert catalog_reply.status_code == 200
        catalog = catalog_reply.json()
        assert catalog["capture_id"] == meta["capture_id"]
        assert catalog["capture_limit"] == 1
        assert catalog["scope"] == "specified capture, latest published interpretation only"
        assert len(catalog["items"]) == 1000
        assert catalog["has_more"] is True and catalog["truncated"] is True
        assert all(row["endpoint"] == "market_rank" and row["observations"] == 1 for row in catalog["items"])
        assert (await client.get("/v1/catalog", params={
            "source_kind": "fixture", "limit": 1001,
        })).status_code == 422
        assert (await client.get("/v1/catalog", params={
            "source_kind": "fixture", "capture_limit": 21,
        })).status_code == 422

        # Checking the tail crosses the former 100-row insert chunks and
        # 256-identity lookups; every metric must survive with its exact text.
        last = rows[-1]
        facts = await _as_known(client, last["address"])
        assert len(facts) == 4
        actual = {fact["metric"]: fact for fact in facts}
        assert set(actual) == {"price", "holder_count", "name", "symbol"}
        assert actual["price"]["value_text"] == last["price"]
        assert actual["price"]["unit"] == "usd_per_token"
        assert actual["price"]["quality"] == "valid"
        assert actual["holder_count"]["value_text"] == "0"
        assert actual["holder_count"]["value_type"] == "integer"
        assert actual["holder_count"]["unit"] == "count"
        assert actual["holder_count"]["quality"] == "valid"
        assert actual["name"]["value_text"] == last["name"]
        assert actual["symbol"]["value_text"] == last["symbol"]
        assert all(fact["capture_id"] == meta["capture_id"] for fact in facts)
        evidence = await client.get(f"/v1/captures/{meta['capture_id']}/raw")
        assert evidence.status_code == 200 and evidence.content == raw


def test_binary_copy_duplicate_identity_rolls_back_run_facts_and_fields_atomically():
    asyncio.run(_copy_atomicity())


async def _copy_atomicity():
    import asyncpg
    from midas_data.contracts import CaptureMetadata
    from midas_data.normalize import normalize_capture
    from midas_data.raw_store import RawStore
    from midas_data.settings import Settings
    from midas_data.store import Store

    dsn, api, key = _environment()
    settings = Settings.from_env()
    store = await Store(dsn, RawStore(settings.raw_path, settings.max_capture_bytes)).open()
    try:
        assert await store.pool.fetchval("SELECT current_database()") == "midas_data"
        token = "0x" + uuid4().hex + uuid4().hex[:8]
        meta = _metadata("token_info", token=token)
        raw = json.dumps({
            "data": {"address": token, "price": {"price": "1.23456789012345678901234567890123456789"},
                     "holder_count": 0, "name": "COPY d'Água Ω"}},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        async with httpx.AsyncClient(
            base_url=api, headers={"authorization": "Bearer " + key},
            timeout=httpx.Timeout(15, connect=3), trust_env=False,
        ) as client:
            await _accept(client, meta, raw)
            original = await _verified(client, meta["capture_id"])
            original_facts = await _as_known(client, token)
            assert len(original_facts) == 3
            normalized = normalize_capture(CaptureMetadata.model_validate(meta), raw)
            unique = [fact.model_copy(update={"fact_id": uuid4()}) for fact in normalized.facts]
            assert len(unique) == 3
            assert len({fact.fact_id for fact in unique}) == 3
            custom_version = "fixture-copy-atomicity-" + uuid4().hex
            invalid = normalized.model_copy(update={
                "normalizer_version": custom_version,
                "facts": [*unique, unique[0]],
            })
            with pytest.raises(asyncpg.UniqueViolationError):
                await store.record_normalization(UUID(meta["capture_id"]), invalid)

            # COPY may process earlier records before discovering the duplicate.
            # The surrounding transaction must also remove the inserted run and
            # any field observations, while leaving the accepted raw untouched.
            capture_id = UUID(meta["capture_id"])
            for table in ("normalization_runs", "facts", "field_observations"):
                count = await store.pool.fetchval(
                    f"SELECT count(*) FROM {table} WHERE capture_id=$1 AND normalizer_version=$2",
                    capture_id, custom_version,
                )
                assert count == 0, f"Partial COPY effects survived in {table}"
            assert await store.pool.fetchval(
                "SELECT count(*) FROM facts WHERE fact_id=ANY($1::uuid[])",
                [fact.fact_id for fact in unique],
            ) == 0
            preserved = await _verified(client, meta["capture_id"])
            assert preserved["runs"] == original["runs"]
            assert await _as_known(client, token) == original_facts
            evidence = await client.get(f"/v1/captures/{meta['capture_id']}/raw")
            assert evidence.status_code == 200 and evidence.content == raw
    finally:
        await store.close()
