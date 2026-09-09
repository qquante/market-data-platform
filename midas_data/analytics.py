"""Bounded, synchronous analytic sinks for the isolated data platform.

DuckLake extensions must be installed in the image; this module only LOADs them.
The operational worker owns the distributed single-writer lock. This class also
serializes calls within a process. Storage receipts are not distributed commits.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import tempfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

SCHEMA_VERSION = "midas-analytics-v1"
MAX_BATCH_ROWS = 10_000
MAX_BATCH_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
LAKE_TIMEOUT_SECONDS = 40
CH_DATABASE = "midas_data"
FACT_COLUMNS = (
    "fact_id", "capture_id", "chain", "token_address", "metric", "value_text",
    "value_type", "unit", "category", "occurred_at", "time_basis", "pointer",
    "quality", "contract_version", "available_at", "normalizer_version",
    "source_kind", "evidence_kind",
)
TIME_COLUMNS = {"occurred_at", "available_at"}
NULLABLE_COLUMNS = {"value_text", "unit"}
RECEIPT_COLUMNS = (
    "batch_id", "checksum", "row_count", "source_counts_json",
    "available_frontier", "created_at", "schema_version",
)
SOURCE_KINDS = {"fixture", "gmgn", "legacy_gmgn"}
EVIDENCE_KINDS = {"original_response", "legacy_reserialized"}


class AnalyticsError(RuntimeError):
    """Safe diagnostic that never contains connection strings or response bodies."""


class BatchConflict(AnalyticsError):
    """An immutable identity or batch was reused with different content."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid timestamp") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _batch_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise ValueError("invalid batch_id")
    return value


def _row(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != set(FACT_COLUMNS):
        raise ValueError("fact columns do not match analytics v1")
    result = {}
    for name in FACT_COLUMNS:
        value = row[name]
        if name in TIME_COLUMNS:
            result[name] = _utc(value)
        elif value is None and name in NULLABLE_COLUMNS:
            result[name] = None
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        else:
            result[name] = value
    for name in ("fact_id", "capture_id"):
        try:
            result[name] = str(UUID(result[name]))
        except (ValueError, AttributeError):
            raise ValueError(f"invalid {name}") from None
    enums = {
        "source_kind": SOURCE_KINDS, "evidence_kind": EVIDENCE_KINDS,
        "value_type": {"decimal", "integer", "boolean", "string"},
        "quality": {"valid", "unverified", "invalid", "missing"},
        "time_basis": {"source", "received"},
    }
    for name, allowed in enums.items():
        if result[name] not in allowed:
            raise ValueError(f"invalid {name}")
    for name in ("chain", "token_address", "metric", "contract_version", "normalizer_version"):
        if not result[name]:
            raise ValueError(f"{name} cannot be empty")
    return result


def _identity(row: dict) -> tuple[str, str]:
    return row["source_kind"], row["fact_id"]


def _row_hash(row: dict) -> str:
    return hashlib.sha256(_json(row).encode("utf-8")).hexdigest()


def _prepare(rows: list[dict], *, stored: bool = False) -> tuple[list[dict], dict]:
    if not isinstance(rows, list) or len(rows) > MAX_BATCH_ROWS:
        raise ValueError("batch row limit exceeded")
    unique = {}
    size = 0
    for original in rows:
        row = _row(original)
        size += len(_json(row).encode("utf-8"))
        if size > MAX_BATCH_BYTES:
            raise ValueError("batch byte limit exceeded")
        key = _identity(row)
        if key in unique:
            if not stored or unique[key] != row:
                raise BatchConflict("duplicate or conflicting fact identity")
        unique[key] = row
    ordered = sorted(unique.values(), key=_identity)
    source_counts: dict[str, int] = {}
    for row in ordered:
        source_counts[row["source_kind"]] = source_counts.get(row["source_kind"], 0) + 1
    summary = {
        "count": len(ordered),
        "checksum": hashlib.sha256(_json(ordered).encode("utf-8")).hexdigest(),
        "source_counts": source_counts,
        "available_frontier": max((r["available_at"] for r in ordered), default=None),
        "schema_version": SCHEMA_VERSION,
    }
    return ordered, summary



def batch_size(rows: list[dict]) -> int:
    """Canonical fact bytes for worker group planning; excludes transport framing."""
    if not isinstance(rows, list) or len(rows) > MAX_BATCH_ROWS:
        raise ValueError("batch row limit exceeded")
    size = 0
    for row in rows:
        size += len(_json(_row(row)).encode("utf-8"))
        if size > MAX_BATCH_BYTES:
            raise ValueError("batch byte limit exceeded")
    return size


def _prepare_batches(batches: list[tuple[str, list[dict]]]) -> dict[str, tuple[list[dict], dict]]:
    if not isinstance(batches, list) or not 1 <= len(batches) <= 8:
        raise ValueError("group must contain between 1 and 8 batches")
    prepared = {}
    identities = {}
    count = size = 0
    for item in batches:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("group entries must be batch_id and rows pairs")
        batch = _batch_id(item[0])
        if batch in prepared:
            raise BatchConflict("duplicate batch_id in group")
        rows, summary = _prepare(item[1])
        count += len(rows)
        size += sum(len(_json(row).encode("utf-8")) for row in rows)
        if count > MAX_BATCH_ROWS or size > MAX_BATCH_BYTES:
            raise ValueError("group row or byte limit exceeded")
        for row in rows:
            key = _identity(row)
            if key in identities and identities[key] != row:
                raise BatchConflict("conflicting fact identity across group batches")
            identities[key] = row
        prepared[batch] = (rows, summary)
    return prepared


def _literal(value: str) -> str:
    # SQL identifiers are constants; runtime values normally use parameters.
    return "'" + value.replace("'", "''") + "'"


def _dsn_with_limits(dsn: str) -> str:
    if dsn.startswith(("postgres://", "postgresql://")):
        parts = urlsplit(dsn)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["connect_timeout"] = "5"
        params["options"] = "-c statement_timeout=30000 -c lock_timeout=5000"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
    return dsn + " connect_timeout=5 options='-c statement_timeout=30000 -c lock_timeout=5000'"


def _receipt_matches(receipt: dict, summary: dict) -> bool:
    return (
        receipt["checksum"] == summary["checksum"]
        and int(receipt["row_count"]) == summary["count"]
        and json.loads(receipt["source_counts_json"]) == summary["source_counts"]
        and receipt["available_frontier"] == summary["available_frontier"]
        and receipt["schema_version"] == SCHEMA_VERSION
    )


class AnalyticsSinks:
    def __init__(
        self, lake_catalog_dsn: str, lake_path: str, clickhouse_url: str,
        clickhouse_user: str, clickhouse_password: str,
    ):
        import httpx

        parsed = urlsplit(clickhouse_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("clickhouse_url must be a base HTTP URL without credentials or query")
        if not lake_catalog_dsn or not lake_path:
            raise ValueError("lake catalog and path are required")
        self._dsn = lake_catalog_dsn
        self._path = lake_path
        self._lake = None
        self._lake_temp_directory = None
        self._lock = threading.RLock()
        self._ready = False
        self._closed = False
        self._http = httpx.Client(
            base_url=clickhouse_url.rstrip("/") + "/",
            auth=(clickhouse_user, clickhouse_password),
            timeout=httpx.Timeout(30.0, connect=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            trust_env=False, follow_redirects=False,
        )

    def _open_lake(self) -> None:
        if self._lake is not None:
            return
        import duckdb

        connection = duckdb.connect(":memory:")
        self._lake = connection
        try:
            # The non-root worker cannot spill into WORKDIR /app/.tmp. Own a
            # private OS temporary directory and cap spill without raising RAM.
            self._lake_temp_directory = tempfile.TemporaryDirectory(prefix="midas-data-duckdb-")
            self._sql(f"SET temp_directory={_literal(self._lake_temp_directory.name)}")
            self._sql("SET max_temp_directory_size='128MB'")
            self._sql("SET threads=2")
            self._sql("SET memory_limit='256MB'")
            self._sql("LOAD ducklake")
            self._sql("LOAD postgres")
            connection_string = "ducklake:postgres:" + _dsn_with_limits(self._dsn)
            self._sql(
                f"ATTACH {_literal(connection_string)} AS lake "
                f"(DATA_PATH {_literal(self._path)}, DATA_INLINING_ROW_LIMIT 0)"
            )
        except Exception:
            try:
                self._dispose_lake()
            except Exception:
                pass
            raise AnalyticsError("DuckLake initialization failed; check image extensions and isolated catalog") from None

    def _dispose_lake(self) -> None:
        connection, self._lake = self._lake, None
        temporary = getattr(self, "_lake_temp_directory", None)
        self._lake_temp_directory = None
        try:
            if connection is not None:
                connection.close()
        finally:
            if temporary is not None:
                temporary.cleanup()

    def _sql(self, query: str, params: list | tuple | None = None) -> list[tuple]:
        timer = threading.Timer(LAKE_TIMEOUT_SECONDS, self._lake.interrupt)
        timer.daemon = True
        timer.start()
        try:
            return self._lake.execute(query, params or []).fetchall()
        except Exception:
            raise AnalyticsError("DuckLake operation failed") from None
        finally:
            timer.cancel()

    def _ch(self, query: str, *, params: dict | None = None, data: list[dict] | None = None, result: bool = False, param_body: bool = False) -> list[dict]:
        settings = {
            "query": query,
            "max_execution_time": "30",
            "max_memory_usage": str(256 * 1024 * 1024),
            "date_time_input_format": "best_effort",
            "date_time_output_format": "iso",
            "wait_end_of_query": "1",
        }
        if result:
            settings["max_bytes_to_read"] = str(128 * 1024 * 1024)
        bindings = {"param_" + key: str(value) for key, value in (params or {}).items()}
        if param_body and data is not None:
            raise ValueError("multipart query parameters cannot share an insert body")
        if not param_body:
            settings.update(bindings)
        body = (("\n".join(_json(row) for row in data) + "\n").encode("utf-8") if data else b"")
        try:
            with self._http.stream(
                "POST", "", params=settings,
                **({"files": {key: (None, value) for key, value in bindings.items()}} if param_body else {"content": body}),
            ) as response:
                if response.status_code != 200:
                    raise AnalyticsError(f"ClickHouse operation failed (HTTP {response.status_code})")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise AnalyticsError("ClickHouse response byte limit exceeded")
                    chunks.extend(chunk)
            if not result:
                if chunks.strip():
                    raise AnalyticsError("ClickHouse returned unexpected command output")
                return []
            payload = json.loads(chunks)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise AnalyticsError("ClickHouse returned invalid result")
            if len(payload["data"]) > MAX_BATCH_ROWS:
                raise AnalyticsError("ClickHouse result row limit exceeded")
            return payload["data"]
        except AnalyticsError:
            raise
        except Exception:
            raise AnalyticsError("ClickHouse request failed") from None

    @staticmethod
    def _lake_types() -> dict[str, str]:
        return {
            **{c: ("TIMESTAMP WITH TIME ZONE" if c in TIME_COLUMNS else "VARCHAR") for c in FACT_COLUMNS},
            "row_hash": "VARCHAR", "batch_id": "VARCHAR",
        }

    @staticmethod
    def _ch_types() -> dict[str, str]:
        return {
            **{c: ("DateTime64(6, 'UTC')" if c in TIME_COLUMNS else "Nullable(String)" if c in NULLABLE_COLUMNS else "String") for c in FACT_COLUMNS},
            "row_hash": "String", "batch_id": "String",
        }

    def _check_lake_schema(self, table: str, expected: dict[str, str]) -> None:
        actual = {r[0]: r[1] for r in self._sql(f"DESCRIBE lake.{table}")}
        if actual != expected:
            raise AnalyticsError(f"incompatible DuckLake schema: {table}")

    def _check_ch_schema(self, table: str, expected: dict[str, str]) -> None:
        actual = {r["name"]: r["type"] for r in self._ch(f"DESCRIBE TABLE {CH_DATABASE}.{table} FORMAT JSON", result=True)}
        if actual != expected:
            raise AnalyticsError(f"incompatible ClickHouse schema: {table}")

    def _check_ch_kind(self, table: str, expected_engine: str) -> None:
        metadata = self._ch(
            "SELECT engine,create_table_query FROM system.tables "
            "WHERE database={database:String} AND name={table:String} FORMAT JSON",
            params={"database": CH_DATABASE, "table": table}, result=True,
        )
        if len(metadata) != 1 or metadata[0]["engine"] != expected_engine:
            raise AnalyticsError(f"incompatible ClickHouse engine: {table}")
        if expected_engine == "View":
            sql = re.sub(r"\s+", "", metadata[0]["create_table_query"]).replace(chr(96), "").lower()
            required = ("argmax(tuple(", "groupbysource_kind,fact_id", "havinguniqexact(row_hash)=1")
            if not all(fragment in sql for fragment in required):
                raise AnalyticsError("incompatible ClickHouse identity view")

    def ensure_schema(self) -> None:
        with self._lock:
            if self._closed:
                raise AnalyticsError("analytics sinks are closed")
            self._open_lake()
            lake_types = self._lake_types()
            receipt_lake = {
                c: "BIGINT" if c == "row_count" else "TIMESTAMP WITH TIME ZONE" if c == "created_at" else "VARCHAR"
                for c in RECEIPT_COLUMNS
            }
            self._sql("BEGIN")
            try:
                for table, types in (("facts_log", lake_types), ("batch_receipts", receipt_lake)):
                    self._sql(f"CREATE TABLE IF NOT EXISTS lake.{table} (" + ",".join(f"{n} {t}" for n, t in types.items()) + ")")
                    self._check_lake_schema(table, types)
                # Identical transport retries collapse; conflicting identities are
                # excluded rather than silently choosing one economic fact.
                fields = ",".join(FACT_COLUMNS)
                self._sql(
                    f"CREATE VIEW IF NOT EXISTS lake.facts AS SELECT {fields} FROM lake.facts_log "
                    "WHERE (source_kind,fact_id) IN (SELECT source_kind,fact_id FROM lake.facts_log "
                    "GROUP BY source_kind,fact_id HAVING count(DISTINCT row_hash)=1) "
                    f"GROUP BY {fields}"
                )
                self._check_lake_schema("facts", {c: lake_types[c] for c in FACT_COLUMNS})
                self._sql("COMMIT")
            except Exception:
                try:
                    self._sql("ROLLBACK")
                except AnalyticsError:
                    pass
                raise
            # DuckLake rejects set_option on transaction-local tables, so apply
            # this idempotent storage option only after schema creation commits.
            inlining = self._sql(
                "SELECT value FROM lake.options() WHERE option_name='data_inlining_row_limit' "
                "AND scope='TABLE' AND scope_entry='main.batch_receipts' LIMIT 2"
            )
            if inlining != [("1000",)]:
                self._sql("CALL lake.set_option('data_inlining_row_limit', 1000, table_name => 'batch_receipts')")
            self._ch(f"CREATE DATABASE IF NOT EXISTS {CH_DATABASE}")
            ch_types = self._ch_types()
            receipt_ch = {
                c: "UInt64" if c == "row_count" else "DateTime64(6, 'UTC')" if c == "created_at" else "Nullable(String)" if c == "available_frontier" else "String"
                for c in RECEIPT_COLUMNS
            }
            for table, types, ordering in (
                ("facts_log", ch_types, "(batch_id,source_kind,fact_id)"),
                ("batch_receipts_log", receipt_ch, "(batch_id,checksum)"),
            ):
                self._ch(
                    f"CREATE TABLE IF NOT EXISTS {CH_DATABASE}.{table} ("
                    + ",".join(f"{n} {t}" for n, t in types.items())
                    + (", INDEX fact_id_bloom fact_id TYPE bloom_filter(0.001) GRANULARITY 1" if table == "facts_log" else "")
                    + f") ENGINE=MergeTree ORDER BY {ordering}"
                )
                self._check_ch_schema(table, types)
                self._check_ch_kind(table, "MergeTree")
            tuple_fields = ",".join(FACT_COLUMNS)
            selected = ",".join(f"tupleElement(r,{i}) AS {name}" for i, name in enumerate(FACT_COLUMNS, 1))
            self._ch(
                f"CREATE VIEW IF NOT EXISTS {CH_DATABASE}.facts AS SELECT {selected} FROM ("
                f"SELECT argMax(tuple({tuple_fields}),row_hash) AS r FROM {CH_DATABASE}.facts_log "
                "GROUP BY source_kind,fact_id HAVING uniqExact(row_hash)=1)"
            )
            self._check_ch_schema("facts", {c: ch_types[c] for c in FACT_COLUMNS})
            self._check_ch_kind("facts", "View")
            self._ready = True

    def _receipt(self, batch: str, *, lake: bool) -> dict | None:
        if lake:
            records = self._sql(
                "SELECT " + ",".join(RECEIPT_COLUMNS) + " FROM lake.batch_receipts WHERE batch_id=? LIMIT 10001",
                [batch],
            )
            records = [dict(zip(RECEIPT_COLUMNS, r)) for r in records]
        else:
            records = self._ch(
                f"SELECT * FROM {CH_DATABASE}.batch_receipts_log WHERE batch_id={{batch:String}} LIMIT 10001 FORMAT JSON",
                params={"batch": batch}, result=True,
            )
        if not records:
            return None
        expected = {k: records[0][k] for k in RECEIPT_COLUMNS if k != "created_at"}
        for record in records:
            comparable = {k: record[k] for k in RECEIPT_COLUMNS if k != "created_at"}
            comparable["row_count"] = int(comparable["row_count"])
            expected["row_count"] = int(expected["row_count"])
            if comparable != expected:
                raise BatchConflict("conflicting batch receipts")
        return records[0]

    def _read_batch(self, batch: str, *, lake: bool) -> list[dict]:
        columns = ",".join((*FACT_COLUMNS, "row_hash"))
        if lake:
            records = self._sql(
                f"SELECT {columns} FROM lake.facts_log WHERE batch_id=? GROUP BY {columns} LIMIT 10001", [batch],
            )
            records = [dict(zip((*FACT_COLUMNS, "row_hash"), r)) for r in records]
        else:
            records = self._ch(
                f"SELECT {columns} FROM {CH_DATABASE}.facts_log WHERE batch_id={{batch:String}} "
                f"GROUP BY {columns} LIMIT 10001 FORMAT JSON", params={"batch": batch}, result=True,
            )
        if len(records) > MAX_BATCH_ROWS:
            raise AnalyticsError("stored batch row limit exceeded")
        result = []
        for record in records:
            digest = record.pop("row_hash")
            normalized = _row(record)
            if digest != _row_hash(normalized):
                raise BatchConflict("stored fact checksum mismatch")
            result.append(normalized)
        return result

    def _check_identities(self, rows: list[dict], *, lake: bool) -> None:
        expected = {_identity(row): _row_hash(row) for row in rows}
        ids = sorted({row["fact_id"] for row in rows})
        if not ids:
            return
        chunks = [ids] if lake else [ids[start:start + 256] for start in range(0, len(ids), 256)]
        for chunk in chunks:
            if lake:
                # One bounded relation of IDs avoids rereading file metadata for
                # every 256 facts. The same global conflict guard still applies.
                records = self._sql(
                    "SELECT source_kind,fact_id,row_hash FROM lake.facts_log "
                    "WHERE fact_id IN (SELECT unnest(?::VARCHAR[])) "
                    "GROUP BY source_kind,fact_id,row_hash LIMIT 10001", [chunk],
                )
            else:
                bindings = {f"id{i}": value for i, value in enumerate(chunk)}
                placeholders = ",".join("{id" + str(i) + ":String}" for i in range(len(chunk)))
                result = self._ch(
                    f"SELECT source_kind,fact_id,row_hash FROM {CH_DATABASE}.facts_log WHERE fact_id IN ({placeholders}) "
                    "GROUP BY source_kind,fact_id,row_hash LIMIT 10001 FORMAT JSON",
                    params=bindings, result=True,
                )
                records = [(r["source_kind"], r["fact_id"], r["row_hash"]) for r in result]
            if len(records) > MAX_BATCH_ROWS:
                raise AnalyticsError("identity lookup row limit exceeded")
            for source, fact, digest in records:
                key = (source, fact)
                if key in expected and expected[key] != digest:
                    raise BatchConflict("fact identity already exists with different content")

    @staticmethod
    def _make_receipt(batch: str, summary: dict) -> dict:
        return {
            "batch_id": batch, "checksum": summary["checksum"], "row_count": summary["count"],
            "source_counts_json": _json(summary["source_counts"]),
            "available_frontier": summary["available_frontier"],
            "created_at": _utc(datetime.now(timezone.utc)), "schema_version": SCHEMA_VERSION,
        }

    def _apply_lake(self, batch: str, rows: list[dict], summary: dict) -> None:
        self._sql("BEGIN")
        try:
            receipt = self._receipt(batch, lake=True)
            if receipt is not None:
                if not _receipt_matches(receipt, summary):
                    raise BatchConflict("batch_id already has different DuckLake content")
            else:
                self._check_identities(rows, lake=True)
                columns = (*FACT_COLUMNS, "row_hash", "batch_id")
                # A single insert produces one changeset, rather than a tiny
                # Parquet file for each 100 rows. JSON is a bound parameter with
                # explicit types: decimal values remain exact VARCHARs and JSON
                # null remains SQL NULL. _prepare enforces the whole batch budget.
                if rows:
                    payload = _json([dict(row, row_hash=_row_hash(row), batch_id=batch) for row in rows])
                    structure = _json([{column: self._lake_types()[column] for column in columns}])
                    self._sql(
                        f"INSERT INTO lake.facts_log ({','.join(columns)}) "
                        f"SELECT {','.join('item.' + column for column in columns)} FROM UNNEST(from_json_strict(?::JSON, ?::JSON)) AS batch_rows(item)",
                        [payload, structure],
                    )
                self._sql(
                    "CALL lake.set_commit_message('midas-data','apply_batch',extra_info=>?)",
                    [_json({"batch_id": batch, "checksum": summary["checksum"], "schema_version": SCHEMA_VERSION})],
                )
                receipt = self._make_receipt(batch, summary)
                self._sql(
                    f"INSERT INTO lake.batch_receipts ({','.join(RECEIPT_COLUMNS)}) VALUES ("
                    + ",".join("?" for _ in RECEIPT_COLUMNS) + ")",
                    [receipt[c] for c in RECEIPT_COLUMNS],
                )
            self._sql("COMMIT")
        except Exception:
            try:
                self._sql("ROLLBACK")
            except AnalyticsError:
                pass
            raise

    def _apply_ch(self, batch: str, rows: list[dict], summary: dict) -> None:
        receipt = self._receipt(batch, lake=False)
        if receipt is not None and not _receipt_matches(receipt, summary):
            raise BatchConflict("batch_id already has different ClickHouse content")
        self._check_identities(rows, lake=False)
        existing, _ = _prepare(self._read_batch(batch, lake=False), stored=True)
        expected = {_identity(row): row for row in rows}
        for row in existing:
            if expected.get(_identity(row)) != row:
                raise BatchConflict("partial ClickHouse batch contains unexpected facts")
        present = {_identity(row) for row in existing}
        missing = [dict(row, row_hash=_row_hash(row), batch_id=batch) for row in rows if _identity(row) not in present]
        if missing:
            self._ch(f"INSERT INTO {CH_DATABASE}.facts_log FORMAT JSONEachRow", data=missing)
        if receipt is None:
            self._ch(
                f"INSERT INTO {CH_DATABASE}.batch_receipts_log FORMAT JSONEachRow",
                data=[self._make_receipt(batch, summary)],
            )

    def apply_batch(self, batch_id: str, rows: list[dict]) -> dict:
        batch = _batch_id(batch_id)
        prepared, summary = _prepare(rows)
        with self._lock:
            if not self._ready:
                self.ensure_schema()
            self._assert_open()
            self._apply_lake(batch, prepared, summary)
            self._apply_ch(batch, prepared, summary)
            result = self.verify_batch(batch)
            if not result["ok"]:
                raise AnalyticsError("batch not verified in both sinks; retry the same batch")
            return result


    def _receipts_many(self, batches: list[str], *, lake: bool) -> dict[str, dict]:
        columns = ",".join(RECEIPT_COLUMNS)
        if lake:
            records = self._sql(
                f"SELECT {columns} FROM lake.batch_receipts "
                "WHERE batch_id IN (SELECT unnest(?::VARCHAR[])) LIMIT 10001", [batches],
            )
            records = [dict(zip(RECEIPT_COLUMNS, r)) for r in records]
        else:
            records = self._ch(
                f"SELECT {columns} FROM {CH_DATABASE}.batch_receipts_log "
                "WHERE batch_id IN (SELECT arrayJoin(JSONExtract({batches:String},'Array(String)'))) "
                "LIMIT 10001 FORMAT JSON", params={"batches": _json(batches)}, result=True,
            )
        if len(records) > MAX_BATCH_ROWS:
            raise AnalyticsError("group receipt row limit exceeded")
        result = {}
        for record in records:
            batch = record["batch_id"]
            if batch not in batches:
                raise AnalyticsError("unexpected group receipt")
            record["row_count"] = int(record["row_count"])
            previous = result.get(batch)
            if previous is not None and any(
                previous[key] != record[key] for key in RECEIPT_COLUMNS if key != "created_at"
            ):
                raise BatchConflict("conflicting batch receipts")
            result[batch] = record
        return result

    def _read_batches(self, batches: list[str], *, lake: bool) -> dict[str, list[dict]]:
        names = ("batch_id", *FACT_COLUMNS, "row_hash")
        columns = ",".join(names)
        if lake:
            records = self._sql(
                f"SELECT {columns} FROM lake.facts_log "
                "WHERE batch_id IN (SELECT unnest(?::VARCHAR[])) "
                f"GROUP BY {columns} LIMIT 10001", [batches],
            )
            records = [dict(zip(names, r)) for r in records]
        else:
            records = self._ch(
                f"SELECT {columns} FROM {CH_DATABASE}.facts_log "
                "WHERE batch_id IN (SELECT arrayJoin(JSONExtract({batches:String},'Array(String)'))) "
                f"GROUP BY {columns} LIMIT 10001 FORMAT JSON",
                params={"batches": _json(batches)}, result=True,
            )
        if len(records) > MAX_BATCH_ROWS:
            raise AnalyticsError("stored group row limit exceeded")
        result = {batch: [] for batch in batches}
        size = 0
        for record in records:
            batch = record.pop("batch_id")
            if batch not in result:
                raise AnalyticsError("unexpected stored group batch")
            digest = record.pop("row_hash")
            normalized = _row(record)
            canonical = _json(normalized).encode("utf-8")
            size += len(canonical)
            if size > MAX_BATCH_BYTES:
                raise AnalyticsError("stored group byte limit exceeded")
            if digest != hashlib.sha256(canonical).hexdigest():
                raise BatchConflict("stored fact checksum mismatch")
            result[batch].append(normalized)
        return result

    def _check_group_identities(self, rows: list[dict], *, lake: bool) -> None:
        if lake:
            self._check_identities(rows, lake=True)
            return
        expected = {_identity(row): _row_hash(row) for row in rows}
        if not expected:
            return
        # One POST/query with bounded multipart fields avoids HTTPX's URI limit
        # and ClickHouse's per-form-field limit. At most 20 fields of 512 UUIDs.
        ids = sorted({row["fact_id"] for row in rows})
        params = {f"ids{i // 512}": _json(ids[i:i + 512]) for i in range(0, len(ids), 512)}
        arrays = ",".join("JSONExtract({" + key + ":String},'Array(String)')" for key in params)
        records = self._ch(
            f"SELECT source_kind,fact_id,row_hash FROM {CH_DATABASE}.facts_log "
            f"WHERE fact_id IN (SELECT arrayJoin(arrayConcat({arrays}))) "
            "GROUP BY source_kind,fact_id,row_hash LIMIT 10001 FORMAT JSON",
            params=params, result=True, param_body=True,
        )
        if len(records) > MAX_BATCH_ROWS:
            raise AnalyticsError("identity lookup row limit exceeded")
        for record in records:
            key = (record["source_kind"], record["fact_id"])
            if key in expected and expected[key] != record["row_hash"]:
                raise BatchConflict("fact identity already exists with different content")

    def _insert_lake_group(self, table: str, records: list[dict], types: dict[str, str]) -> None:
        if not records:
            return
        columns = tuple(types)
        self._sql(
            f"INSERT INTO lake.{table} ({','.join(columns)}) "
            f"SELECT {','.join('item.' + c for c in columns)} "
            "FROM UNNEST(from_json_strict(?::JSON, ?::JSON)) AS group_rows(item)",
            [_json(records), _json([types])],
        )

    def _apply_lake_batches(self, prepared: dict[str, tuple[list[dict], dict]]) -> None:
        self._sql("BEGIN")
        try:
            existing = self._receipts_many(list(prepared), lake=True)
            new = {}
            for batch, (rows, summary) in prepared.items():
                receipt = existing.get(batch)
                if receipt is not None:
                    if not _receipt_matches(receipt, summary):
                        raise BatchConflict("batch_id already has different DuckLake content")
                else:
                    new[batch] = (rows, summary)
            if new:
                self._check_group_identities(
                    [row for rows, _ in prepared.values() for row in rows], lake=True,
                )
                self._insert_lake_group("facts_log", [
                    dict(row, row_hash=_row_hash(row), batch_id=batch)
                    for batch, (rows, _) in new.items() for row in rows
                ], self._lake_types())
                metadata = {
                    "group_version": "midas-analytics-group-v1", "schema_version": SCHEMA_VERSION,
                    "batches": [{"batch_id": batch, "checksum": new[batch][1]["checksum"]} for batch in sorted(new)],
                }
                self._sql(
                    "CALL lake.set_commit_message('midas-data','apply_batches',extra_info=>?)",
                    [_json(metadata)],
                )
                receipt_types = {
                    c: ("BIGINT" if c == "row_count" else "TIMESTAMP WITH TIME ZONE" if c == "created_at" else "VARCHAR") for c in RECEIPT_COLUMNS
                }
                self._insert_lake_group("batch_receipts", [
                    self._make_receipt(batch, summary) for batch, (_, summary) in new.items()
                ], receipt_types)
            self._sql("COMMIT")
        except Exception:
            try:
                self._sql("ROLLBACK")
            except AnalyticsError:
                pass
            raise

    def _apply_ch_batches(self, prepared: dict[str, tuple[list[dict], dict]]) -> None:
        receipts = self._receipts_many(list(prepared), lake=False)
        for batch, (_, summary) in prepared.items():
            if batch in receipts and not _receipt_matches(receipts[batch], summary):
                raise BatchConflict("batch_id already has different ClickHouse content")
        self._check_group_identities([row for rows, _ in prepared.values() for row in rows], lake=False)
        existing = self._read_batches(list(prepared), lake=False)
        missing = []
        for batch, (rows, _) in prepared.items():
            stored, _ = _prepare(existing[batch], stored=True)
            expected = {_identity(row): row for row in rows}
            for row in stored:
                if expected.get(_identity(row)) != row:
                    raise BatchConflict("partial ClickHouse batch contains unexpected facts")
            present = {_identity(row) for row in stored}
            missing.extend(
                dict(row, row_hash=_row_hash(row), batch_id=batch)
                for row in rows if _identity(row) not in present
            )
        if missing:
            self._ch(f"INSERT INTO {CH_DATABASE}.facts_log FORMAT JSONEachRow", data=missing)
        absent = [self._make_receipt(batch, summary) for batch, (_, summary) in prepared.items() if batch not in receipts]
        if absent:
            self._ch(f"INSERT INTO {CH_DATABASE}.batch_receipts_log FORMAT JSONEachRow", data=absent)

    def apply_batches(self, batches: list[tuple[str, list[dict]]]) -> dict[str, dict]:
        """Apply up to eight batches under one Lake commit; retain each receipt."""
        prepared = _prepare_batches(batches)
        with self._lock:
            self._assert_open()
            if not self._ready:
                self.ensure_schema()
            self._apply_lake_batches(prepared)
            self._apply_ch_batches(prepared)
            results = self._verify_batches(list(prepared))
            for batch, (_, summary) in prepared.items():
                result = results[batch]
                if not result["ok"] or any(
                    result[key] != summary[key] for key in ("count", "checksum", "source_counts", "available_frontier")
                ):
                    raise AnalyticsError("group not verified in both sinks; retry the same batches")
            return results

    def _verify_batches(self, batches: list[str]) -> dict[str, dict]:
        results = {batch: {"batch_id": batch, "ok": False} for batch in batches}
        for name, is_lake in (("lake", True), ("clickhouse", False)):
            receipts = self._receipts_many(batches, lake=is_lake)
            stored = self._read_batches(batches, lake=is_lake)
            self._check_group_identities([row for rows in stored.values() for row in rows], lake=is_lake)
            for batch in batches:
                _, summary = _prepare(stored[batch], stored=True)
                receipt = receipts.get(batch)
                results[batch][name] = dict(
                    summary, receipt_present=receipt is not None,
                    ok=receipt is not None and _receipt_matches(receipt, summary),
                )
        snapshots = self._batch_snapshots({
            batch: result["lake"]["checksum"] for batch, result in results.items() if result["lake"]["ok"]
        })
        for batch, result in results.items():
            lake, ch = result["lake"], result["clickhouse"]
            result.update({key: lake[key] for key in ("count", "checksum", "source_counts", "available_frontier")})
            result["snapshot"] = snapshots.get(batch)
            result["ok"] = bool(lake["ok"] and ch["ok"] and lake["checksum"] == ch["checksum"] and result["snapshot"])
        return results

    def _batch_snapshots(self, checksums: dict[str, str]) -> dict[str, dict]:
        if not checksums:
            return {}
        requested = [{
            "batch_id": batch,
            "legacy": _json({"batch_id": batch, "checksum": checksum, "schema_version": SCHEMA_VERSION}),
            "member": _json([{"batch_id": batch, "checksum": checksum}]),
        } for batch, checksum in checksums.items()]
        # Both ID and checksum must occur in the SAME member object. try_cast
        # makes unrelated/non-JSON historical commit metadata harmless.
        rows = self._sql(
            "WITH requested AS (SELECT item.* FROM UNNEST(from_json_strict(?::JSON, "
            "'[{\"batch_id\":\"VARCHAR\",\"legacy\":\"VARCHAR\",\"member\":\"VARCHAR\"}]'::JSON)) AS r(item)), "
            "snapshots AS MATERIALIZED (SELECT snapshot_id,snapshot_time,commit_extra_info,"
            "try_cast(commit_extra_info AS JSON) AS metadata FROM lake.snapshots()) "
            "SELECT requested.batch_id,snapshot_id,snapshot_time FROM requested JOIN snapshots ON "
            "commit_extra_info=requested.legacy OR ("
            "json_extract_string(metadata,'$.group_version')='midas-analytics-group-v1' AND "
            "json_extract_string(metadata,'$.schema_version')=? AND "
            "json_contains(json_extract(metadata,'$.batches'),requested.member::JSON)) "
            "QUALIFY row_number() OVER (PARTITION BY requested.batch_id ORDER BY snapshot_id)=1",
            [_json(requested), SCHEMA_VERSION],
        )
        result = {}
        for batch, snapshot_id, snapshot_time in rows:
            if batch not in checksums or batch in result:
                raise AnalyticsError("invalid group snapshot result")
            result[batch] = {"snapshot_id": int(snapshot_id), "snapshot_time": _utc(snapshot_time), "scope": "batch_commit"}
        return result


    def _assert_open(self) -> None:
        if self._closed:
            raise AnalyticsError("analytics sinks are closed")

    def verify_batch(self, batch_id: str) -> dict:
        batch = _batch_id(batch_id)
        with self._lock:
            self._assert_open()
            if not self._ready:
                self.ensure_schema()
            result = {"batch_id": batch, "ok": False}
            for name, is_lake in (("lake", True), ("clickhouse", False)):
                receipt = self._receipt(batch, lake=is_lake)
                rows, summary = _prepare(self._read_batch(batch, lake=is_lake), stored=True)
                self._check_identities(rows, lake=is_lake)
                result[name] = dict(summary, receipt_present=receipt is not None, ok=receipt is not None and _receipt_matches(receipt, summary))
            lake, ch = result["lake"], result["clickhouse"]
            result["ok"] = lake["ok"] and ch["ok"] and lake["checksum"] == ch["checksum"]
            result.update({key: lake[key] for key in ("count", "checksum", "source_counts", "available_frontier")})
            result["snapshot"] = self._batch_snapshot(batch, lake["checksum"]) if lake["ok"] else None
            result["ok"] = result["ok"] and result["snapshot"] is not None
            return result

    def _batch_snapshot(self, batch: str, checksum: str) -> dict | None:
        metadata = _json({"batch_id": batch, "checksum": checksum, "schema_version": SCHEMA_VERSION})
        rows = self._sql(
            "SELECT snapshot_id,snapshot_time FROM lake.snapshots() "
            "WHERE commit_extra_info=? ORDER BY snapshot_id LIMIT 1", [metadata],
        )
        if not rows:
            return self._batch_snapshots({batch: checksum}).get(batch)
        return {"snapshot_id": int(rows[0][0]), "snapshot_time": _utc(rows[0][1]), "scope": "batch_commit"}


    def compact_files(self, max_output_files: int = 2, min_input_files: int = 64) -> dict:
        """Merge small active files under the caller's distributed writer lock.

        The output budget is global across both tables. No snapshot expiry,
        file deletion, checkpoint or inline-data flush is performed here.
        """
        if type(max_output_files) is not int or not 1 <= max_output_files <= 2:
            raise ValueError("max_output_files must be an integer between 1 and 2")
        if type(min_input_files) is not int or not 2 <= min_input_files <= 10_000:
            raise ValueError("min_input_files must be an integer between 2 and 10000")
        with self._lock:
            self._assert_open()
            if not self._ready:
                self.ensure_schema()
            remaining = max_output_files
            result = {
                "scope": "small_active_files", "max_output_files": max_output_files,
                "min_input_files": min_input_files, "target_file_size_bytes": 8_000_000,
                "max_input_file_size_bytes": 1_048_576,
                "files_processed": 0, "files_created": 0, "tables": {},
            }
            for table in ("facts_log", "batch_receipts"):
                entry = {"files_processed": 0, "files_created": 0}
                result["tables"][table] = entry
                if remaining == 0:
                    entry["status"] = "output_budget_exhausted"
                    continue
                # This only lists catalog metadata. LIMIT caps the count needed
                # for the threshold; it does not claim to bound metadata scanned.
                counts = self._sql(
                    "SELECT count(*) FROM (SELECT DISTINCT data_file "
                    "FROM ducklake_list_files('lake', ?, schema => 'main') "
                    "WHERE data_file_size_bytes < 1048576 AND delete_file IS NULL LIMIT ?)",
                    [table, min_input_files],
                )
                if len(counts) != 1 or len(counts[0]) != 1:
                    raise AnalyticsError("invalid DuckLake file metadata result")
                observed = int(counts[0][0])
                entry["eligible_files_observed"] = observed
                entry["count_capped_at_threshold"] = observed == min_input_files
                if observed < min_input_files:
                    entry["status"] = "below_threshold"
                    continue
                size = self._sql(
                    "SELECT value FROM lake.options() WHERE option_name='target_file_size' "
                    "AND scope='TABLE' AND scope_entry=? LIMIT 2", ["main." + table],
                )
                # DuckLake persists ParseMemoryLimit('8MB') as decimal bytes.
                if size != [("8000000",)]:
                    self._sql(
                        "CALL lake.set_option('target_file_size', '8MB', table_name => ?, schema => 'main')",
                        [table],
                    )
                # Parallel COPY groups exceeded the bounded spill budget in the
                # live gate. Merge one group per table with one execution thread;
                # restore the caller's setting even when the merge fails.
                previous_threads = int(self._sql("SELECT current_setting('threads')")[0][0])
                try:
                    self._sql("SET threads=1")
                    merged = self._sql(
                        "CALL ducklake_merge_adjacent_files('lake', ?, schema => 'main', "
                        "max_compacted_files => ?, max_file_size => 1048576)", [table, 1],
                    )
                finally:
                    self._sql(f"SET threads={previous_threads}")
                processed = created = 0
                for record in merged:
                    if len(record) != 4 or record[0] != "main" or record[1] != table:
                        raise AnalyticsError("invalid DuckLake compaction result")
                    inputs, outputs = int(record[2]), int(record[3])
                    if inputs < 1 or outputs != 1:
                        raise AnalyticsError("invalid DuckLake compaction counts")
                    processed += inputs
                    created += outputs
                if created > 1 or created > remaining:
                    raise AnalyticsError("DuckLake compaction exceeded output budget")
                entry.update(
                    status="compacted" if created else "no_mergeable_group",
                    files_processed=processed, files_created=created,
                )
                result["files_processed"] += processed
                result["files_created"] += created
                remaining -= created
            return result

    def snapshot_info(self) -> dict:
        with self._lock:
            self._assert_open()
            if not self._ready:
                self.ensure_schema()
            snapshots = self._sql("SELECT snapshot_id,snapshot_time,schema_version FROM lake.snapshots() ORDER BY snapshot_id DESC LIMIT 1")
            row = snapshots[0] if snapshots else None
            return {
                "catalog": "lake", "snapshot_id": int(row[0]) if row else None,
                "snapshot_time": _utc(row[1]) if row else None,
                "catalog_schema_version": int(row[2]) if row else None,
                "schema_version": SCHEMA_VERSION,
                "scope": "latest_catalog_snapshot_observed_not_batch_commit_id",
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                try:
                    self._dispose_lake()
                finally:
                    self._http.close()
            except Exception:
                raise AnalyticsError("analytics sinks close failed") from None
            finally:
                self._closed = True
                self._ready = False
