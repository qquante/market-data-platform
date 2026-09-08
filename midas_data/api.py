from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .contracts import CaptureMetadata
from .normalize import canonical_identity
from .raw_store import RawStore, RawIntegrityError
from .settings import Settings
from .store import CaptureConflict, FutureCutoff, Store


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "Timezone required; use UTC ISO8601")
    return value.astimezone(timezone.utc)


def make_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = await Store(settings.database_dsn, RawStore(settings.raw_path,settings.max_capture_bytes)).open()
        yield
        await app.state.store.close()

    app = FastAPI(title="Midas GMGN Data", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        if request.url.path not in ("/healthz",):
            expected = "Bearer " + settings.api_key
            if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
                return JSONResponse({"detail":"Unauthorized"},status_code=401)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail":[{"loc":list(e["loc"]),"msg":e["msg"],"type":e["type"]} for e in exc.errors()]},status_code=422)

    @app.exception_handler(FutureCutoff)
    async def invalid_cutoff(request, exc):
        return JSONResponse({"detail":"knowledge_cutoff cannot be in the future"},status_code=422)

    @app.get("/healthz")
    async def health(request: Request):
        await request.app.state.store.pool.fetchval("SELECT 1")
        return {"ok":True,"scope":"data-only"}

    @app.get("/v1/clock")
    async def clock(request: Request):
        async with request.app.state.store.pool.acquire() as con:
            async with con.transaction(isolation="read_committed"):
                await con.execute("SELECT pg_advisory_xact_lock_shared(60608003)")
                cutoff=await con.fetchval("SELECT clock_timestamp()")
        return {"knowledge_cutoff":cutoff,"timezone":"UTC","basis":"serialized database publication"}

    @app.get("/v1/status")
    async def status(request: Request):
        return await request.app.state.store.status()

    @app.post("/v1/captures",status_code=202)
    async def ingest(request: Request):
        metadata_text=request.headers.get("x-midas-metadata")
        if not metadata_text or len(metadata_text)>16384:
            raise HTTPException(422,"x-midas-metadata JSON header required, maximum 16KiB")
        try:
            metadata=CaptureMetadata.model_validate_json(metadata_text)
        except ValidationError as exc:
            raise HTTPException(422,exc.errors(include_input=False,include_context=False)) from None
        raw=bytearray()
        async for part in request.stream():
            if len(raw)+len(part)>settings.max_capture_bytes:
                raise HTTPException(413,"Capture exceeds byte budget")
            raw.extend(part)
        try:
            return await request.app.state.store.accept_capture(metadata,bytes(raw))
        except CaptureConflict:
            raise HTTPException(409,"capture_id conflict; original capture preserved") from None
        except RawIntegrityError:
            raise HTTPException(503,"Raw integrity verification failed") from None

    @app.get("/v1/captures")
    async def captures(request: Request, source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                       limit: int=Query(50,ge=1,le=200)):
        rows=await request.app.state.store.pool.fetch(
            """SELECT capture_id,source_kind,evidence_kind,endpoint,chain,received_at,recorded_at,
            available_at,normalization_state,raw_sha256 FROM captures WHERE source_kind=$1
            ORDER BY recorded_at DESC,capture_id DESC LIMIT $2""",source_kind,limit)
        return {"items":[dict(r) for r in rows],"limit":limit}

    @app.get("/v1/captures/{capture_id}")
    async def capture(request: Request,capture_id: UUID):
        result=await request.app.state.store.get_capture(capture_id)
        if result is None:
            raise HTTPException(404,"Capture not found")
        return result

    @app.get("/v1/captures/{capture_id}/raw")
    async def raw(request: Request,capture_id: UUID):
        result=await request.app.state.store.get_capture(capture_id)
        if result is None:
            raise HTTPException(404,"Capture not found")
        import asyncio
        body=await asyncio.to_thread(request.app.state.store.raw.read,result["raw_sha256"])
        return Response(content=body,media_type="application/octet-stream",
                        headers={"X-Content-SHA256":result["raw_sha256"]})

    @app.get("/v1/observations")
    async def observations(request: Request,chain: str,token: str,metric: str,
                           start: datetime,end: datetime,knowledge_cutoff: datetime,
                           source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                           limit: int=Query(500,ge=1,le=5000),after_seq: int=Query(0,ge=0),
                           include_unverified: bool=False):
        try:
            chain,token=canonical_identity(chain,token)
        except ValueError:
            raise HTTPException(422,"Invalid chain/token identity") from None
        start,end,cutoff=aware(start),aware(end),aware(knowledge_cutoff)
        if cutoff > datetime.now(timezone.utc):
            raise HTTPException(422,"knowledge_cutoff cannot be in the future")
        if end<=start or (end-start).total_seconds()>7*86400:
            raise HTTPException(422,"Interval must be positive and at most 7 days")
        if any(len(x)>200 for x in (chain,token,metric)):
            raise HTTPException(422,"Filter exceeds length budget")
        rows=await request.app.state.store.observations(chain,token,metric,start,end,cutoff,source_kind,limit,after_seq,include_unverified)
        return {"items":rows,"knowledge_cutoff":cutoff,"source_kind":source_kind,
                "next_after_seq":rows[-1]["seq"] if len(rows)==limit else None,
                "available_at_basis":"serialized publication after facts commit",
                "include_unverified":include_unverified}

    @app.get("/v1/as-known")
    async def as_known(request: Request,chain: str,token: str,event_at: datetime,knowledge_cutoff: datetime,
                       source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                       include_unverified: bool=False):
        try:
            chain,token=canonical_identity(chain,token)
        except ValueError:
            raise HTTPException(422,"Invalid chain/token identity") from None
        event_at,cutoff=aware(event_at),aware(knowledge_cutoff)
        if cutoff > datetime.now(timezone.utc):
            raise HTTPException(422,"knowledge_cutoff cannot be in the future")
        if any(len(x)>200 for x in (chain,token)):
            raise HTTPException(422,"Filter exceeds length budget")
        rows=await request.app.state.store.as_known(chain,token,event_at,cutoff,source_kind,include_unverified)
        return {"items":rows,"event_at":event_at,"knowledge_cutoff":cutoff,
                "available_at_basis":"serialized publication after facts commit",
                "source_kind":source_kind,"include_unverified":include_unverified}

    @app.get("/v1/catalog")
    async def catalog(request: Request,source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                      limit: int=Query(200,ge=1,le=1000),
                      capture_limit: int=Query(20,ge=1,le=100)):
        rows=await request.app.state.store.pool.fetch(
            """WITH recent AS MATERIALIZED (SELECT capture_id,endpoint FROM captures WHERE source_kind=$1
            ORDER BY recorded_at DESC,capture_id DESC LIMIT $3)
            SELECT c.endpoint,f.pointer,f.category,f.value_type,f.quality,f.contract_id,
            count(*) AS observations FROM recent c
            JOIN LATERAL (SELECT normalizer_version FROM normalization_runs
              WHERE capture_id=c.capture_id AND available_at IS NOT NULL
              ORDER BY available_at DESC,normalizer_version DESC LIMIT 1) selected_run ON true
            JOIN field_observations f ON f.capture_id=c.capture_id
              AND f.normalizer_version=selected_run.normalizer_version
            GROUP BY 1,2,3,4,5,6 ORDER BY 1,2 LIMIT $2""",source_kind,limit,capture_limit)
        return {"items":[dict(r) for r in rows],"limit":limit,"source_kind":source_kind,
                "scope":"latest captures, latest published interpretation only","capture_limit":capture_limit,
                "meaning":"observed fields; valid is not blanket economic homologation"}

    return app
