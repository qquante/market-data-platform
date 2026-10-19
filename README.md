# Market Data Platform

An auditable market-data backend with ingestion contracts, durable source preservation and recoverable event processing.

![review](https://img.shields.io/badge/review-private-374151) ![primary](https://img.shields.io/badge/primary-Python-2563EB) [![LinkedIn](https://img.shields.io/badge/LinkedIn-Douglas%20Silva-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/douglas-silva-data-analyst/)

[Overview](#overview) · [Architecture](#architecture) · [Setup](#setup) · [Validation](#validation) · [Roadmap](#roadmap) · [Review notes](docs/REVIEW.md)

## Overview

- Source contracts and normalization keep raw observations distinct from derived features.
- Outbox and delivery modules support recovery, deduplication and replay.
- API, dashboard, holder tracking and paper-account research are separate modules.

**Technology:** Python 3.13 · FastAPI · Pydantic · asyncpg · PostgreSQL · DuckDB · NATS JetStream · Docker

**Persistence and data:** PostgreSQL stores normalized and operational state; DuckDB supports analytical paths. NATS supports event delivery. Deployment-level Redis/ClickHouse references require the parent infrastructure configuration.

## Architecture

```text
Source observations → contracts and normalization → durable records/outbox → delivery workers → API and research views
```

| Location | Review focus |
| --- | --- |
| [`midas_data/api.py`](midas_data/api.py) | Source/configuration inspected for scope and structure |
| [`midas_data/delivery.py`](midas_data/delivery.py) | Source/configuration inspected for scope and structure |
| [`midas_data/normalize.py`](midas_data/normalize.py) | Source/configuration inspected for scope and structure |
| [`midas_data/bridge.py`](midas_data/bridge.py) | Source/configuration inspected for scope and structure |
| [`pyproject.toml`](pyproject.toml) | Source/configuration inspected for scope and structure |

Internal package and module names are retained where changing them would affect imports or compatibility. This descriptive repository name does not imply a completed package/API migration.

## Setup

Use Python 3.13, create a virtual environment, then install this package with pip install -e .. Inspect pyproject.toml before installing; Python 3.14 is outside its declared range.

Start from an isolated environment. Use `.env.example` when present and inspect the configuration loader: an example file is not necessarily loaded automatically. Runtime databases, real account sessions and private credentials are not bundled. Live integration prerequisites are separate from local tests.

## Validation

Static parsing checked 133 Python files and 13 strict JSON files. Python syntax errors: 0; JSON errors: 0. No functional test/build result is claimed for this preparation.

Available test scripts are inventoried in [the review notes](docs/REVIEW.md). Choose offline checks before operating any integration.

These results apply to the selected checks on the preparation copy. They do not establish deployment readiness, full coverage, live provider availability or measured performance. CI definitions, when present under `docs/ci-templates/`, are inactive review templates; no remote CI badge is claimed.

## Limits and current status

This is an extracted data-platform slice. Parent compose files and private source access are not bundled. Historical acceptance documents are not new production or load-test evidence.

This private snapshot is for code and portfolio review. Generated runtime material and unreviewed binary assets may be excluded. [Review notes](docs/REVIEW.md) record preparation scope, findings and outstanding validation.

## Roadmap

- [ ] Provide a standalone compose file and synthetic source fixtures.
- [ ] Validate with the declared Python 3.13 runtime and isolated PostgreSQL/NATS services.
- [ ] Review parent infrastructure contracts before presenting a self-contained deployment.

## History and attribution

3 existing commits were retained after path/content filtering. Original authors and author/committer dates were retained; commit hashes may change. Upstream commits are not presented as new work by the maintainer.

No open-source license is granted by this preparation. A license and attribution review is required before public redistribution.

## Maintainer and feedback

Douglas de Oliveira Silva · [GitHub](https://github.com/qquante) · [LinkedIn](https://www.linkedin.com/in/douglas-silva-data-analyst/)

For this private review, report reproducible issues with a minimal fixture and the exact check output. Keep account data, credentials and generated sensitive reports out of issues and commits. Existing contribution guidelines apply where present.

**Topics:** `python`, `fastapi`, `postgresql`, `duckdb`, `nats`, `data-engineering`, `event-driven`, `backend`
