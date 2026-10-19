from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


class StoragePressure(RuntimeError):
    pass


class RawIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawObject:
    sha256: str
    relative_path: str
    size_bytes: int


class RawStore:
    def __init__(self, root: Path, max_bytes: int = 4 * 1024 * 1024, *, min_free_bytes: int = 4 * 1024**3, host_capacity_path: Path | None = None):
        self.root = Path(root)
        self.max_bytes = max_bytes
        if type(min_free_bytes) is not int or not 0 <= min_free_bytes <= 1024**4:
            raise ValueError("invalid_storage_reserve")
        self.min_free_bytes = min_free_bytes
        self.host_capacity_path = Path(host_capacity_path) if host_capacity_path is not None else None
        self.root.mkdir(parents=True, exist_ok=True)

    def admission(self, write_bytes: int | None = None) -> dict:
        # Filesystem reserve, not lifetime accepted bytes or number of queued captures.
        # This is a pressure signal, not an atomic reservation against other writers.
        required = self.min_free_bytes + (self.max_bytes + 65536 if write_bytes is None else write_bytes)
        raw_free = shutil.disk_usage(self.root).free
        host_free = shutil.disk_usage(self.host_capacity_path).free if self.host_capacity_path is not None else None
        free = min(raw_free,host_free) if host_free is not None else raw_free
        return {"accepting": free >= required, "free_bytes": free,
                "raw_free_bytes": raw_free, "host_free_bytes": host_free,
                "required_free_bytes": required, "basis": "storage_filesystems_free_space"}

    def _path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid SHA256 object identity")
        return self.root / digest[:2] / (digest + ".gz")

    def read(self, digest: str) -> bytes:
        try:
            with gzip.open(self._path(digest), "rb") as source:
                raw = source.read(self.max_bytes + 1)
        except (OSError, EOFError) as exc:
            raise RawIntegrityError("Raw object cannot be decoded") from exc
        if len(raw) > self.max_bytes or hashlib.sha256(raw).hexdigest() != digest:
            raise RawIntegrityError("Raw object checksum/size mismatch")
        return raw

    @staticmethod
    def _sync_directory(path: Path):
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def write(self, raw: bytes) -> RawObject:
        if not isinstance(raw, bytes) or len(raw) > self.max_bytes:
            raise ValueError("Raw capture exceeds byte limit or is not bytes")
        digest = hashlib.sha256(raw).hexdigest()
        destination = self._path(digest)
        obj = RawObject(digest, destination.relative_to(self.root).as_posix(), len(raw))
        if destination.exists():
            if self.read(digest) != raw:
                raise RawIntegrityError("Existing object differs")
            self._sync_directory(self.root)
            self._sync_directory(destination.parent)
            return obj
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        if not self.admission(len(compressed))["accepting"]:
            raise StoragePressure("storage_backpressure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(compressed)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if self.read(digest) != raw:
                    raise RawIntegrityError("Concurrent object differs")
            if os.name != "nt":
                directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            os.unlink(temporary)
        return obj
