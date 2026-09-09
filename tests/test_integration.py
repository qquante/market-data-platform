"""Docker integration proofs for the isolated GMGN data foundation.

Opt-in only: DATA_INTEGRATION=1, DATA_API_KEY and the Compose API are required.
All created captures are fixtures. No Docker control, external provider requests,
legacy database access, or financial operations occur in this module.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("DATA_INTEGRATION") != "1",
        reason="Docker integration is opt-in with DATA_INTEGRATION=1",
    ),
]
UTC = timezone.utc
WAIT_SECONDS = 180


def now():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def token_address():
    # Synthetic EVM identity, scoped as fixture at every destination.
    return "0x" + uuid4().hex + uuid4().hex[:8]


def metadata(token, *, capture_id=None, event_at=None, received_at=None, **changes):
    received = received_at or now() - timedelta(seconds=1)
    result = {
        "capture_id": capture_id or str(uuid4()),
        "source_kind": "fixture",
        "endpoint": "token_info",
        "chain": "base",
        "requested_at": iso(received - timedelta(milliseconds=100)),
        "received_at": iso(received),
        "source_event_at": iso(event_at) if event_at is not None else None,
        "http_status": 200,
        "request_params": {"address": token},
        "evidence_kind": "original_response",
    }
    result.update(changes)
    return result


def body(token, price="1.25", **extra):
    data = {"address": token, "price": {"price": price}}
    data.update(extra)
    return json.dumps({"data": data}, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture(scope="module")
def client():
    key = os.getenv("DATA_API_KEY")
    assert key, "DATA_API_KEY is required for opted-in Docker integration"
    endpoint = os.getenv("DATA_TEST_API_URL", "http://api:8791")
    parsed = urlsplit(endpoint)
    assert (
        parsed.scheme == "http"
        and parsed.hostname in {"api", "localhost", "127.0.0.1"}
        and parsed.port == 8791
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    ), "Integration API must be the isolated Compose/local endpoint"
    with httpx.Client(
        base_url=endpoint,
        headers={"authorization": "Bearer " + key},
        timeout=httpx.Timeout(15, connect=5),
        trust_env=False,
    ) as session:
        response = session.get("/healthz")
        assert response.status_code == 200
        assert response.json()["scope"] == "data-only"
        yield session


def post(client, meta, raw):
    return client.post(
        "/v1/captures",
        headers={
            "x-midas-metadata": json.dumps(meta, separators=(",", ":")),
            "content-type": "application/octet-stream",
        },
        content=raw,
    )


def accepted(client, meta, raw):
    response = post(client, meta, raw)
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["capture_id"] == meta["capture_id"]
    assert data["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["bytes"] == len(raw)
    return data


def wait_capture(client, capture_id, *, state="ready", analytics=False):
    deadline = time.monotonic() + WAIT_SECONDS
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/v1/captures/{capture_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        runs = last["runs"]
        if last["normalization_state"] == state and runs and all(r["available_at"] for r in runs):
            if not analytics or all(r["analytics_applied_at"] and r["analytics_receipt"]["ok"] for r in runs):
                return last
        time.sleep(0.4)
    # The diagnostic deliberately omits raw metadata and deployment settings.
    pytest.fail(
        "Capture did not reach the required pipeline state: "
        + json.dumps({
            "capture_id": capture_id,
            "expected_state": state,
            "analytics_required": analytics,
            "actual_state": last.get("normalization_state") if last else None,
            "last_error": last.get("last_error") if last else None,
            "run_count": len(last.get("runs", [])) if last else 0,
        })
    )


def observations(client, token, *, metric="price", cutoff=None, start=None, end=None, **changes):
    reference = now()
    params = {
        "chain": "base",
        "token": token,
        "metric": metric,
        "start": iso(start or reference - timedelta(days=1)),
        "end": iso(end or reference),
        "knowledge_cutoff": iso(cutoff or reference),
        "source_kind": "fixture",
    }
    params.update(changes)
    response = client.get("/v1/observations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def as_known(client, token, event_at, cutoff, **changes):
    params = {
        "chain": "base",
        "token": token,
        "event_at": iso(event_at),
        "knowledge_cutoff": iso(cutoff),
        "source_kind": "fixture",
    }
    params.update(changes)
    response = client.get("/v1/as-known", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_lossless_raw_precision_clocks_and_analytic_receipts(client):
    token = token_address()
    exact = "0.123456789012345678901234567890123456789012345678901234567890"
    received = now() - timedelta(minutes=1)
    meta = metadata(token, received_at=received)
    # A number lexeme (not a quoted numeric string), Unicode and formatting
    # exercise the transport and parser, not merely Pydantic construction.
    raw = (
        '{\n  "data": {"address": "' + token + '", "price": {"price": '
        + exact + '}, "qa_text": "ação 🚀", "qa_exponent": 1e-40}\n}\n'
    ).encode("utf-8")
    reply = accepted(client, meta, raw)
    assert reply["duplicate"] is False
    saved = wait_capture(client, meta["capture_id"], analytics=True)
    response = client.get(f"/v1/captures/{meta['capture_id']}/raw")
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["x-content-sha256"] == hashlib.sha256(raw).hexdigest()
    rows = observations(client, token)["items"]
    assert len(rows) == 1
    fact = rows[0]
    assert fact["capture_id"] == meta["capture_id"]
    assert fact["value_text"] == exact
    assert Decimal(fact["value_text"]) == Decimal(exact)
    assert fact["unit"] == "usd_per_token"
    assert fact["quality"] == "valid"
    assert fact["time_basis"] == "received"
    assert instant(fact["occurred_at"]) == received
    assert instant(fact["available_at"]) >= instant(saved["runs"][0]["recorded_at"])
    assert fact["source_kind"] == saved["source_kind"] == "fixture"
    assert fact["evidence_kind"] == "original_response"
    assert "numeric_value" not in fact
    run = saved["runs"][0]
    assert run["fact_count"] == 1
    receipt = run["analytics_receipt"]
    assert receipt["batch_id"] == run["batch_id"]
    assert receipt["ok"] is True
    assert receipt["count"] == 1
    assert receipt["source_counts"] == {"fixture": 1}
    assert receipt["lake"]["ok"] and receipt["clickhouse"]["ok"]
    assert receipt["lake"]["checksum"] == receipt["clickhouse"]["checksum"]
    assert receipt["snapshot"]["scope"] == "batch_commit"
    assert isinstance(receipt["snapshot"]["snapshot_id"], int)
    # Validate the receipt against the publicly served complete fact, rather
    # than accepting two destinations that merely agree on the same bad value.
    canonical_fact = {key: value for key, value in fact.items() if key != "seq"}
    for key in ("occurred_at", "available_at"):
        canonical_fact[key] = instant(canonical_fact[key]).isoformat(timespec="microseconds").replace("+00:00", "Z")
    expected_checksum = hashlib.sha256(json.dumps(
        [canonical_fact], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert receipt["checksum"] == expected_checksum


def test_concurrent_retry_conflicts_and_distinct_capture_identity(client):
    token = token_address()
    meta = metadata(token)
    raw = body(token)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: post(client, meta, raw), range(4)))
    assert [response.status_code for response in responses] == [202] * 4
    replies = [response.json() for response in responses]
    assert sum(reply["duplicate"] is False for reply in replies) == 1
    assert sum(reply["duplicate"] is True for reply in replies) == 3
    original = wait_capture(client, meta["capture_id"], analytics=True)
    first_rows = observations(client, token)["items"]
    assert len(first_rows) == 1
    first_fact_id = first_rows[0]["fact_id"]
    conflict = post(client, meta, body(token, "2.5"))
    assert conflict.status_code == 409
    changed_meta = dict(meta, received_at=iso(instant(meta["received_at"]) + timedelta(seconds=1)))
    assert post(client, changed_meta, raw).status_code == 409
    assert client.get(f"/v1/captures/{meta['capture_id']}/raw").content == raw
    assert accepted(client, meta, raw)["duplicate"] is True
    same = wait_capture(client, meta["capture_id"], analytics=True)
    assert len(same["runs"]) == len(original["runs"]) == 1
    assert observations(client, token)["items"][0]["fact_id"] == first_fact_id
    other_meta = dict(meta, capture_id=str(uuid4()))
    assert accepted(client, other_meta, raw)["duplicate"] is False
    wait_capture(client, other_meta["capture_id"], analytics=True)
    rows = observations(client, token)["items"]
    assert len(rows) == 2
    assert len({row["fact_id"] for row in rows}) == 2
    assert {row["capture_id"] for row in rows} == {meta["capture_id"], other_meta["capture_id"]}


def test_as_known_excludes_late_knowledge_and_later_events(client):
    token = token_address()
    base_event = now() - timedelta(hours=3)
    first = metadata(token, event_at=base_event, received_at=base_event + timedelta(seconds=1))
    accepted(client, first, body(token, "1"))
    wait_capture(client, first["capture_id"])
    historical_cutoff = now()
    late = metadata(
        token, event_at=base_event + timedelta(minutes=2),
        received_at=base_event + timedelta(minutes=2, seconds=1),
    )
    accepted(client, late, body(token, "2"))
    wait_capture(client, late["capture_id"])
    later_event = metadata(
        token, event_at=base_event + timedelta(minutes=20),
        received_at=base_event + timedelta(minutes=20, seconds=1),
    )
    accepted(client, later_event, body(token, "3"))
    wait_capture(client, later_event["capture_id"])
    current_cutoff = now()
    event_cut = base_event + timedelta(minutes=10)
    historical = as_known(client, token, event_cut, historical_cutoff)
    current = as_known(client, token, event_cut, current_cutoff)
    after_event = as_known(client, token, base_event + timedelta(minutes=30), current_cutoff)
    assert [(row["metric"], row["value_text"]) for row in historical] == [("price", "1")]
    assert [(row["metric"], row["value_text"]) for row in current] == [("price", "2")]
    assert [(row["metric"], row["value_text"]) for row in after_event] == [("price", "3")]
    assert current[0]["capture_id"] == late["capture_id"]
    assert instant(current[0]["available_at"]) > historical_cutoff
    assert current[0]["time_basis"] == "source"


def test_observation_pagination_freezes_knowledge_during_backfill(client):
    token = token_address()
    event = now() - timedelta(hours=2)
    ids = []
    for index in range(2):
        meta = metadata(token, event_at=event + timedelta(seconds=index))
        accepted(client, meta, body(token, str(index + 1)))
        wait_capture(client, meta["capture_id"])
        ids.append(meta["capture_id"])
    cutoff = now()
    first_page = observations(client, token, cutoff=cutoff, limit=1)
    assert len(first_page["items"]) == 1
    cursor = first_page["next_after_seq"]
    assert isinstance(cursor, int)
    delayed = metadata(token, event_at=event - timedelta(minutes=1))
    accepted(client, delayed, body(token, "99"))
    wait_capture(client, delayed["capture_id"])
    rows = list(first_page["items"])
    for _ in range(4):
        page = observations(client, token, cutoff=cutoff, limit=1, after_seq=cursor)
        rows.extend(page["items"])
        cursor = page["next_after_seq"]
        if cursor is None:
            break
    else:
        pytest.fail("Cursor failed to terminate within a bounded number of pages")
    assert len(rows) == 2
    assert {row["capture_id"] for row in rows} == set(ids)
    assert len({row["fact_id"] for row in rows}) == 2
    assert delayed["capture_id"] in {row["capture_id"] for row in observations(client, token)["items"]}


def test_fixture_source_is_explicit_and_never_leaks_to_real_queries(client):
    token = token_address()
    meta = metadata(token)
    accepted(client, meta, body(token))
    saved = wait_capture(client, meta["capture_id"], analytics=True)
    cutoff = now()
    assert saved["source_kind"] == "fixture"
    assert observations(client, token, cutoff=cutoff)["items"]
    for source in ("gmgn", "legacy_gmgn"):
        assert observations(client, token, cutoff=cutoff, source_kind=source)["items"] == []
        assert as_known(client, token, cutoff, cutoff, source_kind=source) == []
        captures = client.get("/v1/captures", params={"source_kind": source, "limit": 200})
        assert captures.status_code == 200
        assert meta["capture_id"] not in {row["capture_id"] for row in captures.json()["items"]}
    assert client.get("/v1/captures").status_code == 422
    assert client.get("/v1/catalog").status_code == 422
    invalid_source = client.get("/v1/captures", params={"source_kind": "all"})
    assert invalid_source.status_code == 422


def test_catalog_preserves_unknown_null_false_zero_and_unverified_semantics(client):
    token = token_address()
    meta = metadata(token)
    raw = body(
        token, "2", holder_count=0, total_supply="1000000000",
        liquidity=None, link={"description": ""},
        qa_catalog_unknown={"a/b~c": [None, False, 0]},
    )
    accepted(client, meta, raw)
    saved = wait_capture(client, meta["capture_id"], analytics=True)
    assert client.get(f"/v1/captures/{meta['capture_id']}/raw").content == raw
    response = client.get("/v1/catalog", params={
        "source_kind": "fixture", "limit": 1000, "capture_id": meta["capture_id"],
    })
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["capture_id"] == meta["capture_id"]
    assert catalog["capture_limit"] == 1
    assert catalog["scope"] == "specified capture, latest published interpretation only"
    assert catalog["has_more"] is False and catalog["truncated"] is False
    assert len(catalog["items"]) == saved["runs"][0]["field_count"]
    assert all(row["endpoint"] == "token_info" and row["observations"] == 1 for row in catalog["items"])
    for other_source in ("gmgn", "legacy_gmgn"):
        excluded = client.get("/v1/catalog", params={
            "source_kind": other_source, "capture_id": meta["capture_id"], "limit": 1000,
        })
        assert excluded.status_code == 200
        assert excluded.json()["items"] == []
        assert excluded.json()["has_more"] is False and excluded.json()["truncated"] is False
    fields = catalog["items"]
    def catalog_has(pointer, **properties):
        return any(
            row["pointer"] == pointer and all(row[key] == value for key, value in properties.items())
            for row in fields
        )
    prefix = "/data/qa_catalog_unknown/a~1b~0c"
    assert catalog_has(prefix + "/0", quality="missing")
    assert catalog_has(prefix + "/1", value_type="boolean", quality="unverified")
    assert catalog_has(prefix + "/2", value_type="integer", category="unknown")
    assert catalog_has("/data/liquidity", quality="missing")
    assert catalog_has("/data/link/description", quality="missing")
    holders = observations(client, token, metric="holder_count")["items"]
    assert len(holders) == 1
    assert holders[0]["value_text"] == "0"
    assert holders[0]["quality"] == "valid"
    assert holders[0]["unit"] == "count"
    assert holders[0]["value_type"] == "integer"
    assert catalog_has("/data/holder_count", quality="valid", value_type="integer")
    assert observations(client, token, metric="total_supply")["items"] == []
    explicit = observations(client, token, metric="total_supply", include_unverified=True)["items"]
    assert len(explicit) == 1
    assert explicit[0]["value_text"] == "1000000000"
    assert explicit[0]["quality"] == "unverified"
    assert explicit[0]["unit"] is None
    assert explicit[0]["value_type"] == "decimal"
    assert observations(client, token, metric="market_cap", include_unverified=True)["items"] == []
    assert saved["runs"][0]["analytics_receipt"]["count"] == saved["runs"][0]["fact_count"]


@pytest.mark.parametrize(
    ("raw_kind", "expected_issue"),
    [
        ("invalid_json", "invalid_json"),
        ("duplicate_key", "duplicate_json_key"),
        ("identity_conflict", "identity_conflict"),
        ("http_error", "http_error"),
    ],
)
def test_bad_payload_is_preserved_but_quarantined_without_promoted_facts(client, raw_kind, expected_issue):
    token = token_address()
    meta = metadata(token)
    if raw_kind == "invalid_json":
        raw = b'{"data":'
    elif raw_kind == "duplicate_key":
        raw = ('{"data":{"address":"' + token + '","price":{"price":1,"price":2}}}').encode()
    elif raw_kind == "identity_conflict":
        raw = body(token_address())
    else:
        meta["http_status"] = 429
        raw = body(token)
    accepted(client, meta, raw)
    saved = wait_capture(client, meta["capture_id"], state="quarantined", analytics=True)
    assert client.get(f"/v1/captures/{meta['capture_id']}/raw").content == raw
    run = saved["runs"][0]
    assert run["fact_count"] == 0
    assert expected_issue in {issue["code"] for issue in run["issues"]}
    assert run["analytics_receipt"]["count"] == 0
    assert observations(client, token, include_unverified=True)["items"] == []


@pytest.mark.parametrize(
    "invalid_change",
    [
        {"received_at": "2026-09-08T12:00:00"},
        {"requested_at": "2099-01-01T00:00:00Z"},
        {"http_status": True},
        {"request_params": {"api_key": "synthetic-secret-must-not-echo"}},
    ],
)
def test_invalid_envelope_is_rejected_without_capture_or_secret_echo(client, invalid_change):
    token = token_address()
    meta = metadata(token)
    meta.update(invalid_change)
    response = post(client, meta, body(token))
    assert response.status_code == 422
    assert "synthetic-secret-must-not-echo" not in response.text
    assert client.get(f"/v1/captures/{meta['capture_id']}").status_code == 404


def test_authentication_and_query_budgets(client):
    unauthorized = client.get("/v1/status", headers={"authorization": "Bearer invalid-test-key"})
    assert unauthorized.status_code == 401
    token = token_address()
    meta = metadata(token)
    denied = client.post(
        "/v1/captures",
        headers={"authorization": "", "x-midas-metadata": json.dumps(meta)},
        content=body(token),
    )
    assert denied.status_code == 401
    assert client.get(f"/v1/captures/{meta['capture_id']}").status_code == 404
    reference = now()
    params = {
        "chain": "base", "token": token, "metric": "price",
        "start": iso(reference - timedelta(days=1)), "end": iso(reference),
        "knowledge_cutoff": iso(reference), "source_kind": "fixture",
    }
    invalid_queries = [
        {"limit": 5001},
        {"limit": 0},
        {"after_seq": -1},
        {"start": iso(reference - timedelta(days=8))},
        {"start": iso(reference + timedelta(seconds=1))},
        {"knowledge_cutoff": "2026-09-08T12:00:00"},
        {"token": "x" * 201},
    ]
    for changes in invalid_queries:
        response = client.get("/v1/observations", params=dict(params, **changes))
        assert response.status_code == 422, changes


def test_successful_empty_ranking_is_ready_with_preserved_evidence(client):
    token = token_address()
    meta = metadata(token, endpoint="market_rank", request_params={})
    raw = b'{"code":0,"data":{"rank":[]}}'
    accepted(client, meta, raw)
    saved = wait_capture(client, meta["capture_id"], state="ready", analytics=True)
    response = client.get(f"/v1/captures/{meta['capture_id']}/raw")
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["x-content-sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(saved["runs"]) == 1
    run = saved["runs"][0]
    assert run["fact_count"] == 0
    assert run["field_count"] > 0
    assert run["issues"] == []
    assert run["analytics_receipt"]["ok"] is True
    assert run["analytics_receipt"]["count"] == 0
    assert run["analytics_receipt"]["source_counts"] == {}


@pytest.mark.parametrize("route", ["/v1/observations", "/v1/as-known"])
def test_future_knowledge_cutoff_is_rejected(client, route):
    reference = now()
    params = {
        "chain": "base",
        "token": token_address(),
        "knowledge_cutoff": iso(reference + timedelta(days=1)),
        "source_kind": "fixture",
    }
    if route == "/v1/observations":
        params.update(
            metric="price",
            start=iso(reference - timedelta(hours=1)),
            end=iso(reference),
        )
    else:
        params["event_at"] = iso(reference)
    response = client.get(route, params=params)
    assert response.status_code == 422


def test_database_clock_cutoff_and_original_evm_case_resolve_the_same_fact(client):
    canonical_token = token_address()
    original_token = "0x" + canonical_token[2:].upper()
    meta = metadata(original_token, chain="BASE")
    accepted(client, meta, body(original_token, "3.141592653589793238462643383279"))
    wait_capture(client, meta["capture_id"])
    clock_reply = client.get("/v1/clock")
    assert clock_reply.status_code == 200
    clock = clock_reply.json()
    parsed = datetime.fromisoformat(clock["knowledge_cutoff"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed <= now()
    assert clock["timezone"] == "UTC"
    assert clock["basis"] == "serialized database publication"
    canonical = as_known(client, canonical_token, parsed, parsed)
    original = as_known(client, original_token, parsed, parsed, chain="BASE")
    assert len(canonical) == 1
    assert original == canonical
    assert canonical[0]["capture_id"] == meta["capture_id"]
    assert canonical[0]["chain"] == "base"
    assert canonical[0]["token_address"] == canonical_token
    observed = observations(client, original_token, cutoff=parsed, chain="BASE")["items"]
    assert [row["fact_id"] for row in observed] == [canonical[0]["fact_id"]]
