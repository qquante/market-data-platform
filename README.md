# Market Data Platform

An auditable market-data backend with ingestion contracts, durable source preservation and recoverable event processing.

## Features

- Source contracts and normalization keep raw observations distinct from derived features.
- Outbox and delivery modules support recovery, deduplication and replay.
- API, dashboard, holder tracking and paper-account research are separate modules.

## Stack

Python 3.13 · FastAPI · Pydantic · asyncpg · PostgreSQL · DuckDB · NATS JetStream · Docker

PostgreSQL stores normalized and operational state; DuckDB supports analytical paths. NATS supports event delivery. Deployment-level Redis/ClickHouse references require the parent infrastructure configuration.

## Getting started

Use Python 3.13, create a virtual environment, then install this package with `python -m pip install -e .`. Inspect pyproject.toml before installing; Python 3.14 is outside its declared range.

## How it works

```text
Source observations → contracts and normalization → durable records/outbox → delivery workers → API and research views
```

## Current limitations

This is an extracted data-platform slice. Parent compose files and private source access are not bundled. Historical acceptance documents are not new production or load-test evidence.

## Validation

Python 3.13.16 on Windows: 988 tests and 39 subtests passed with warnings treated as errors; 34 tests were skipped and 203 integration-marked tests were deselected. PostgreSQL, NATS, Docker and live data providers were not started.

```console
python -m pip install -e .
python -m pip install pytest==9.1.1
python -m pytest -m "not integration" tests -W error -o addopts= -q
```

The standalone restore-reconciliation helper is included in `scripts/` for its offline regression tests. Running it against real databases requires the parent restore configuration.

## License

Project-authored source code is licensed under the [MIT License](LICENSE). Third-party components and datasets retain their respective rights and licenses.
