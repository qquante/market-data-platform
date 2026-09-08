from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required setting missing: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_dsn: str
    lake_catalog_dsn: str
    api_key: str
    nats_url: str
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    raw_path: Path
    lake_path: str
    max_capture_bytes: int = 4 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_dsn=required("DATA_DATABASE_DSN"),
            lake_catalog_dsn=required("DATA_LAKE_CATALOG_DSN"),
            api_key=required("DATA_API_KEY"),
            nats_url=required("DATA_NATS_URL"),
            clickhouse_url=required("DATA_CLICKHOUSE_URL"),
            clickhouse_user=required("DATA_CLICKHOUSE_USER"),
            clickhouse_password=required("DATA_CLICKHOUSE_PASSWORD"),
            raw_path=Path(os.getenv("DATA_RAW_PATH", "/var/lib/midas/raw")),
            lake_path=os.getenv("DATA_LAKE_PATH", "/var/lib/midas/lake"),
            max_capture_bytes=int(os.getenv("DATA_MAX_CAPTURE_BYTES", str(4 * 1024 * 1024))),
        )
