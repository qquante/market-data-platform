from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required setting missing: {name}")
    return value


def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} outside supported range")
    return value


@dataclass(frozen=True)
class Settings:
    database_dsn: str = field(repr=False)
    lake_catalog_dsn: str = field(repr=False)
    api_key: str = field(repr=False)
    nats_url: str = field(repr=False)
    clickhouse_url: str = field(repr=False)
    clickhouse_user: str
    clickhouse_password: str = field(repr=False)
    raw_path: Path
    lake_path: str
    max_capture_bytes: int = 4 * 1024 * 1024
    raw_min_free_bytes: int = 4 * 1024**3
    raw_host_capacity_path: Path | None = None
    collector_status_path: Path | None = None
    dashboard_public_read: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            dashboard_public_read=os.getenv("DATA_DASHBOARD_PUBLIC_READ", "0").lower() in {"1", "true"},
            database_dsn=required("DATA_DATABASE_DSN"),
            lake_catalog_dsn=required("DATA_LAKE_CATALOG_DSN"),
            api_key=required("DATA_API_KEY"),
            nats_url=required("DATA_NATS_URL"),
            clickhouse_url=required("DATA_CLICKHOUSE_URL"),
            clickhouse_user=required("DATA_CLICKHOUSE_USER"),
            clickhouse_password=required("DATA_CLICKHOUSE_PASSWORD"),
            raw_path=Path(os.getenv("DATA_RAW_PATH", "/var/lib/midas/raw")),
            lake_path=os.getenv("DATA_LAKE_PATH", "/var/lib/midas/lake"),
            collector_status_path=Path(os.environ['DATA_COLLECTOR_STATUS_PATH']) if os.getenv('DATA_COLLECTOR_STATUS_PATH') else None,
            max_capture_bytes=int(os.getenv("DATA_MAX_CAPTURE_BYTES", str(4 * 1024 * 1024))),
            raw_min_free_bytes=int(os.getenv("DATA_RAW_MIN_FREE_BYTES", str(4 * 1024**3))),
            raw_host_capacity_path=Path(os.environ["DATA_STORAGE_HOST_PATH"]) if os.getenv("DATA_STORAGE_HOST_PATH") else None,
        )
