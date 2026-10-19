from datetime import datetime, timedelta, timezone
from uuid import UUID
import base64
import hashlib
import zlib

import pytest

from midas_data.bridge import (BridgeConfig, RawLegacyRow, EXTRA_PARAMS, PAGE_SIZE, WINDOW,
                               initial_progress, prepare_lane, finish_page, validate_source_dsn)
from midas_data.contracts import CaptureMetadata
from midas_data.importer import verify_row, ImportFailure

UTC = timezone.utc
ORIGIN = datetime(2026,9,8,12,tzinfo=UTC)


def test_lanes_are_fair_and_audit_boundary_is_fixed_until_completion():
    p = initial_progress(ORIGIN)
    now = ORIGIN + timedelta(hours=1)
    name,lane = prepare_lane(p,ORIGIN,now)
    assert name == "forward" and lane["end"] == (ORIGIN+WINDOW).isoformat()
    finish_page(p,name,[],now)
    name,lane = prepare_lane(p,ORIGIN,now)
    assert name == "recent" and lane["cycle_end"] == (ORIGIN+WINDOW).isoformat()
    finish_page(p,name,[],now)
    name,lane = prepare_lane(p,ORIGIN,now)
    assert name == "audit" and lane["cycle_end"] == (ORIGIN+WINDOW).isoformat()
    fixed = lane["cycle_end"]
    p["forward_until"] = now.isoformat()
    assert prepare_lane(p,ORIGIN,now+timedelta(days=1))[1]["cycle_end"] == fixed
    finish_page(p,"audit",[],now)
    assert p["turn"] == 0 and p["lanes"]["audit"]["cycles"] == 1


def test_full_page_preserves_window_and_tie_cursor():
    p = initial_progress(ORIGIN)
    name,lane = prepare_lane(p,ORIGIN,ORIGIN+timedelta(minutes=2))
    end = lane["end"]
    rows = [{"received_at_utc":ORIGIN,"call_id":UUID(int=i)} for i in range(PAGE_SIZE)]
    finish_page(p,name,rows,ORIGIN+timedelta(minutes=2))
    assert lane["end"] == end and lane["at"] == ORIGIN.isoformat()
    assert lane["after"] == str(UUID(int=PAGE_SIZE-1))
    assert p["forward_until"] == ORIGIN.isoformat()


@pytest.mark.parametrize("dsn",[
    "postgresql://midas_fomo:secret@midas-fomo-postgres-1:5432/midas_fomo",
    "postgresql://midas_data_reader:secret@postgres:5432/midas_fomo",
    "postgresql://midas_data_reader:secret@midas-fomo-postgres-1:5432/other",
    "postgresql://midas_data_reader:secret@midas-fomo-postgres-1:5432/midas_fomo?options=bad",
    "postgresql://midas_data_reader@midas-fomo-postgres-1:5432/midas_fomo",
])
def test_production_source_requires_dedicated_host_role_database(dsn):
    with pytest.raises(ValueError,match="dedicated_readonly"):
        validate_source_dsn(dsn)


def test_valid_dsn_and_configuration_binding_contains_origin_not_password():
    validate_source_dsn("postgresql://midas_data_reader:secret@midas-fomo-postgres-1:5432/midas_fomo")
    a = BridgeConfig("a",ORIGIN,"cluster1")
    b = BridgeConfig("a",ORIGIN,"cluster2")
    c = BridgeConfig("a",ORIGIN+timedelta(seconds=1),"cluster1")
    assert len({a.fingerprint,b.fingerprint,c.fingerprint}) == 3
    with pytest.raises(ValueError,match="fixture"):
        BridgeConfig("production",ORIGIN,"c",source_kind="fixture")


def raw_row(endpoint, chain, params):
    raw = b'{"code":0,"data":[]}'
    sha = hashlib.sha256(raw).hexdigest()
    return RawLegacyRow(export_version="gmgn-legacy-v1",call_id=UUID(int=42),
        endpoint=endpoint,chain=chain,requested_at_utc=ORIGIN,received_at_utc=ORIGIN,
        http_status=200,request_params=params,response_sha256=sha,payload_sha256=sha,
        codec="zlib",raw_bytes=len(raw),payload_base64=base64.b64encode(zlib.compress(raw)).decode())


def test_unknown_endpoint_preserves_null_chain_original_body_and_query_horizons():
    row = raw_row("hot_searches",None,{"query":{"chain":None}})
    capture = verify_row(row,additional_allowed=EXTRA_PARAMS,allow_lists=True)
    assert capture.metadata.chain is None and capture.metadata.request_params["chain"] is None
    assert capture.raw == b'{"code":0,"data":[]}'
    row = raw_row("token_kline","sol",{"query":{"chain":"sol","resolution":"1s","from":100,"to":110,
                                             "horizon_seconds":10,"api_key":"never-copy"}})
    capture = verify_row(row,additional_allowed=EXTRA_PARAMS,allow_lists=True)
    assert capture.metadata.request_params == {"chain":"sol","resolution":"1s","from":100,"to":110,"horizon_seconds":10}
    assert "api_key" not in capture.metadata_header()


def test_new_wallet_body_context_is_bounded_and_manual_import_behavior_is_unchanged():
    row = raw_row("wallet_profits","sol",{"body":{"chain":"sol","period":"7d","wallet_addresses":["Wallet1","Wallet2"]}})
    capture = verify_row(row,additional_allowed=EXTRA_PARAMS,allow_lists=True)
    assert capture.metadata.request_params["wallet_addresses"] == ["Wallet1","Wallet2"]
    assert capture.metadata.request_params["period"] == "7d"
    # Default original importer still strips non-I/R context.
    assert verify_row(row).metadata.request_params == {}
    row = raw_row("wallet_profits","sol",{"body":{"wallet_addresses":["a"]*101}})
    with pytest.raises(ImportFailure,match="invalid_allowlisted"):
        verify_row(row,additional_allowed=EXTRA_PARAMS,allow_lists=True)

@pytest.mark.parametrize("failures,info,expected", [
    (0,{"page":{"accepted":50}},0.1),
    (0,{"page":{"accepted":30,"unavailable":5,"duplicate":15}},0.1),
    (0,{"page":{"accepted":49}},1.0),
    (0,{"pending":500},1.0),
    (0,{"state":"paused"},5.0),
    (0,{"page":{}},1.0),
    (1,{"page":{"accepted":50}},3),
    (8,{},15),
])
def test_poll_delay_only_accelerates_full_successful_pages(failures,info,expected):
    from midas_data.bridge import poll_delay
    assert poll_delay(failures,info) == expected
