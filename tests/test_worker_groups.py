from midas_data.worker import group_would_overflow, MAX_GROUP_CAPTURES
from midas_data.analytics import MAX_BATCH_BYTES, MAX_BATCH_ROWS
import pytest


@pytest.mark.parametrize("count,rows,size,next_rows,next_size,expected", [
    (0,0,0,1,1,False),
    (7,9999,MAX_BATCH_BYTES-1,1,1,False),
    (8,0,0,0,1,True),
    (1,10000,0,1,1,True),
    (1,0,MAX_BATCH_BYTES,0,1,True),
    (1,10000,MAX_BATCH_BYTES,0,0,False),
    (0,0,0,MAX_BATCH_ROWS+1,MAX_BATCH_BYTES+1,False),
])
def test_group_boundaries_do_not_drop_single_items(count,rows,size,next_rows,next_size,expected):
    assert group_would_overflow(count,rows,size,next_rows,next_size) is expected


def test_empty_facts_still_flush_after_eight_capture_receipts():
    groups=[]
    current=[]
    size=0
    for identifier in range(19):
        if group_would_overflow(len(current),0,size,0,2):
            groups.append(current)
            current=[]
            size=0
        current.append(identifier)
        size+=2
    groups.append(current)
    assert list(map(len,groups)) == [MAX_GROUP_CAPTURES,MAX_GROUP_CAPTURES,3]
    assert [item for group in groups for item in group] == list(range(19))
