"""Versioned price-only semantics and preserved legacy paper definitions."""
from copy import deepcopy
from datetime import timedelta
import pytest
from pydantic import ValidationError
from midas_data.features import _hash
from midas_data.paper_prices import FRAME_VERSION,PRICE_VERSION,TRACKED_PAPER_VERSION,source_definition,price_result
from midas_data.paper_model import PaperPolicy,quote,transition
from test_paper_model import policy,snapshot,at,pending

IDENTITY=dict(source_kind='fixture',chain='base',token_address='0x'+'a'*40)


def tracked_frame():
    frame=snapshot()
    frame.update(version=FRAME_VERSION,source_policy_id=_hash(source_definition()))
    frame['result'].update(version=PRICE_VERSION,endpoint='token_info',**IDENTITY)
    return frame


def test_v2_is_explicit_and_legacy_definition_keeps_its_shape():
    old=policy();new=policy(version=TRACKED_PAPER_VERSION,entry_timeout_seconds=720)
    assert 'price_source' not in old.definition()
    assert new.definition()['price_source']==source_definition()
    assert old.key()!=new.key()
    assert PaperPolicy.model_validate_json(old.model_dump_json()).definition()==old.definition()
    assert new.model_dump()['version']==TRACKED_PAPER_VERSION


@pytest.mark.parametrize('timeout,latency',[(719,0),(720,1),(300,10)])
def test_tracked_entry_wait_cannot_assume_instant_collection(timeout,latency):
    with pytest.raises(ValidationError,match='720'):
        policy(version=TRACKED_PAPER_VERSION,entry_timeout_seconds=timeout,entry_latency_seconds=latency)


@pytest.mark.parametrize('change',[
    lambda f:f.update(version='legacy'),
    lambda f:f.update(source_policy_id='0'*64),
    lambda f:f['result'].update(endpoint='market_rank'),
    lambda f:f['result'].update(chain='sol'),
    lambda f:f['result'].update(token_address='different'),
])
def test_tracked_quote_rejects_wrong_frame_source_and_identity(change):
    frame=tracked_frame();change(frame)
    assert quote(frame,policy(),at(11),at(5),identity=IDENTITY)[1]=='price_source_mismatch'


def test_tracked_quote_carries_source_and_rejects_old_future_stale_or_ambiguous_sample():
    frame=tracked_frame()
    value,reason=quote(frame,policy(),at(11),at(5),identity=IDENTITY)
    assert reason is None and value['endpoint']=='token_info' and value['source_policy_id']==_hash(source_definition())
    assert quote(frame,policy(),at(11),at(11),identity=IDENTITY)[1]=='awaiting_new_price'
    assert quote(frame,policy(),at(9),at(5),identity=IDENTITY)[1]=='price_not_known'
    assert quote(frame,policy(),at(50),at(5),identity=IDENTITY)[1]=='price_stale'
    frame['result']['points'][-1]['same_timestamp_count']=2
    assert quote(frame,policy(),at(11),at(5),identity=IDENTITY)[1]=='latest_observation_unavailable'
    assert quote(None,policy(),at(11),at(5),identity=IDENTITY)[1]=='price_frame_unavailable'
