import gzip
import hashlib

import pytest

from midas_data.raw_store import RawStore, RawIntegrityError


def test_capture_bytes_survive_replay_without_json_reserialization(tmp_path):
    raw = b'{"price":0.0000000000000000001234567890123456789, "unknown": null}\n'
    store = RawStore(tmp_path, max_bytes=1024)
    obj = store.write(raw)
    assert obj.sha256 == hashlib.sha256(raw).hexdigest()
    assert store.read(obj.sha256) == raw
    assert store.write(raw) == obj
    assert len(list(tmp_path.rglob("*.gz"))) == 1


def test_corrupt_existing_object_is_rejected_instead_of_trusted(tmp_path):
    store = RawStore(tmp_path, max_bytes=1024)
    obj = store.write(b"original")
    (tmp_path / obj.relative_path).write_bytes(gzip.compress(b"changed"))
    with pytest.raises(RawIntegrityError):
        store.read(obj.sha256)
    with pytest.raises(RawIntegrityError):
        store.write(b"original")


def test_limits_and_hash_path_are_enforced(tmp_path):
    store = RawStore(tmp_path, max_bytes=4)
    with pytest.raises(ValueError):
        store.write(b"12345")
    with pytest.raises(ValueError):
        store.read("../outside")
