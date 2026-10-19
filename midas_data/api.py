from __future__ import annotations

import json
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .collector_status import read_collector_status
from .daily_trajectory import build_daily, day_bounds
from .crossings import GRID_VERSION, build_crossings
from .contracts import CaptureMetadata
from .normalize import canonical_identity
from .observation_context import ContextUnavailable, build_observation_context
from .raw_store import RawStore, RawIntegrityError, StoragePressure
from .valuation import build_valuation, valuation_pointers
from .token_registry import TokenRegistry
from .holder_store import HolderStore
from .research_api import install_research_routes
from .dashboard import install_dashboard, PUBLIC_UI_PATHS, public_dashboard_read
from .resources import resource_snapshot
from .trajectory import METHODS, TrajectoryLimit, read_trajectory
from .features import SharedFeatures
from .rules import ComparisonRule, evaluate
from .rule_timers import ActiveRule
from .active_rules import ActiveRules
from .paper_model import PaperRequest
from .paper_prices import PriceSourceChange
from .paper_bindings import BindingRequest
from .paper_account import AccountPolicy,AccountConflict,account_row
from .paper_positions import PaperPositions, PaperConflict
from .feature_windows import FeatureWindows, WindowUnavailable, configuration
from .settings import Settings
from .store import CaptureConflict, FutureCutoff, Store, ValuationInputLimit


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "Timezone required; use UTC ISO8601")
    return value.astimezone(timezone.utc)


def make_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = await Store(settings.database_dsn, RawStore(settings.raw_path,settings.max_capture_bytes,min_free_bytes=settings.raw_min_free_bytes,host_capacity_path=settings.raw_host_capacity_path)).open()
        app.state.feature_windows = FeatureWindows(app.state.store, context_slots)
        app.state.feature_windows.rules = ActiveRules(app.state.feature_windows)
        app.state.feature_windows.paper = PaperPositions(app.state.feature_windows)
        app.state.feature_windows.start()
        try:
            yield
        finally:
            await app.state.feature_windows.close()
            await app.state.store.close()

    app = FastAPI(title="Midas GMGN Data", version="0.1.0", lifespan=lifespan)

    install_research_routes(app)
    install_dashboard(app, settings)
    context_slots = asyncio.Semaphore(2)
    from .candle_api import install_candle_routes
    install_candle_routes(app, context_slots)
    from .explorer import install_explorer_routes
    install_explorer_routes(app, context_slots)
    from .field_dictionary_api import install_dictionary_routes
    install_dictionary_routes(app, context_slots)
    from .read_cache import ReadCache,ReaderBusy
    trajectory_cache=ReadCache(context_slots)
    app.state.trajectory_cache=trajectory_cache
    feature_cache = SharedFeatures()
    app.state.feature_cache = feature_cache

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        anonymous_read = (
            request.method in {"GET", "HEAD"}
            and request.url.path.startswith("/v1/")
            and public_dashboard_read(request, settings.dashboard_public_read)
        )
        if request.url.path != "/healthz" and request.url.path not in PUBLIC_UI_PATHS and not anonymous_read:
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

    @app.exception_handler(ValuationInputLimit)
    async def valuation_input_limit(request, exc):
        return JSONResponse({"detail":"Valuation input budget exceeded; no partial result"},status_code=503)

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
        result = await request.app.state.store.status()
        result['collector'] = await asyncio.to_thread(read_collector_status, settings.collector_status_path)
        result['token_registry'] = await TokenRegistry(request.app.state.store).status()
        result['api_resources'] = await asyncio.to_thread(resource_snapshot)
        result['trajectory_reader']=trajectory_cache.status()
        return result

    @app.get("/v1/admission")
    async def admission(request: Request):
        return await asyncio.to_thread(request.app.state.store.raw.admission)

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
        except StoragePressure:
            raise HTTPException(503,"storage_backpressure",headers={"Retry-After":"5"}) from None
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

    @app.get("/v1/holders")
    async def holders(request: Request, chain: str, token: str, start: datetime, end: datetime,
                      knowledge_cutoff: datetime,
                      source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                      limit: int=Query(50,ge=1,le=200),after_seq: int=Query(0,ge=0)):
        try:
            chain,token=canonical_identity(chain,token)
            if chain != 'sol': raise ValueError('unsupported_chain')
            return await HolderStore(request.app.state.store).listing(
                source_kind,chain,token,aware(start),aware(end),aware(knowledge_cutoff),limit,after_seq)
        except FutureCutoff: raise
        except ValueError: raise HTTPException(422,"Invalid holder query; Solana and a window of at most 31 days required") from None

    @app.get("/v1/holders/{capture_id}")
    async def holder_snapshot(request: Request, capture_id: UUID, knowledge_cutoff: datetime,
                              source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                              limit: int=Query(100,ge=1,le=100),after_ordinal: int=Query(-1,ge=-1,le=99)):
        result=await HolderStore(request.app.state.store).snapshot(
            capture_id,source_kind,aware(knowledge_cutoff),limit,after_ordinal)
        if result is None: raise HTTPException(404,"Holder snapshot unavailable at cutoff")
        return result

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

    @app.get("/v1/observations/{fact_id}/context")
    async def observation_context(request: Request, fact_id: UUID, knowledge_cutoff: datetime,
                                  source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$")):
        cutoff = aware(knowledge_cutoff)
        if cutoff > datetime.now(timezone.utc):
            raise HTTPException(422, "knowledge_cutoff cannot be in the future")
        if context_slots.locked():
            raise HTTPException(429, "Context reader busy", headers={"Retry-After": "1"})
        async with context_slots:
            record = await request.app.state.store.observation_context_record(fact_id, source_kind, cutoff)
            if record is None:
                raise HTTPException(404, "Observation not available for this source and cutoff")
            fact, capture = record
            def read_context():
                raw = request.app.state.store.raw.read(capture['raw_sha256'])
                return build_observation_context(fact, capture, raw)
            try:
                context = await asyncio.to_thread(read_context)
            except (RawIntegrityError, ContextUnavailable):
                raise HTTPException(503, "Observation context evidence unavailable") from None
            return {"observation": fact, "context": context, "knowledge_cutoff": cutoff,
                    "scope": "specified fact version; not latest token state or complete discovery history"}

    @app.get("/v1/observations/{fact_id}/valuation")
    async def observation_valuation(request: Request, fact_id: UUID, knowledge_cutoff: datetime,
                                    source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$")):
        cutoff=aware(knowledge_cutoff)
        if cutoff > datetime.now(timezone.utc):
            raise HTTPException(422,"knowledge_cutoff cannot be in the future")
        if context_slots.locked():
            raise HTTPException(429,"Context reader busy",headers={"Retry-After":"1"})
        async with context_slots:
            store=request.app.state.store
            record=await store.observation_context_record(fact_id,source_kind,cutoff)
            if record is None:
                raise HTTPException(404,"Observation not available for this source and cutoff")
            fact,capture=record
            def read_context():
                return build_observation_context(fact,capture,store.raw.read(capture['raw_sha256']))
            try:
                context=await asyncio.to_thread(read_context)
            except (RawIntegrityError,ContextUnavailable):
                raise HTTPException(503,"Valuation evidence unavailable") from None
            pointer=context['entity']['item_pointer']
            rows=await store.valuation_facts(fact_id,source_kind,cutoff,valuation_pointers(pointer))
            return {"anchor_fact_id":fact_id,"knowledge_cutoff":cutoff,"context":context,
                    "valuation":build_valuation(fact,capture['endpoint'],pointer,rows)}

    @app.get('/v1/tokens/lookup')
    async def lookup_registered_tokens(request:Request,chain:str,knowledge_cutoff:datetime,
            addresses:list[str]=Query(...,min_length=1,max_length=100),
            source_kind:str=Query(...,pattern='^(fixture|gmgn|legacy_gmgn)$')):
        try:
            normalized=[canonical_identity(chain,address) for address in addresses]
            chain=normalized[0][0];tokens=[pair[1] for pair in normalized]
            if len(set(tokens))!=len(tokens):raise ValueError('duplicate identity')
        except ValueError:raise HTTPException(422,'Invalid or duplicate chain/token identity') from None
        return await TokenRegistry(request.app.state.store).lookup(source_kind,chain,aware(knowledge_cutoff),tokens)

    @app.get("/v1/tokens")
    async def registered_tokens(request: Request, chain: str, knowledge_cutoff: datetime,
                                source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                                limit: int=Query(100,ge=1,le=200),
                                after_registry_id: int=Query(0,ge=0),token: str | None=None):
        try:
            chain,address=canonical_identity(chain,token if token is not None else "_")
        except ValueError:
            raise HTTPException(422,"Invalid chain/token identity") from None
        cutoff=aware(knowledge_cutoff)
        return await TokenRegistry(request.app.state.store).listing(
            source_kind,chain,cutoff,limit,after_registry_id,address if token is not None else None)


    async def trajectory_response(request, chain, token, method, start, end, knowledge_cutoff,
                                  source_kind, max_points, gap_after_seconds, day=None,
                                  grid_version=None, max_events=1000, features=False):
        try:
            chain,token=canonical_identity(chain,token)
        except ValueError:
            raise HTTPException(422,"Invalid chain/token identity") from None
        start,end,cutoff=aware(start),aware(end),aware(knowledge_cutoff)
        if method not in METHODS:
            raise HTTPException(422,{"message":"Unsupported trajectory method","allowed":list(METHODS)})
        if cutoff>datetime.now(timezone.utc) or end>cutoff:
            raise HTTPException(422,"Require end <= knowledge_cutoff <= current time")
        if end<=start or (end-start).total_seconds()>86400:
            raise HTTPException(422,"Interval must be positive and at most 24 hours")
        key=(source_kind,chain,token,method,start,end,cutoff,max_points,gap_after_seconds,day,grid_version,max_events,features)
        async def compute():
            headers = {}
            try:
                async with asyncio.timeout(5):
                    result=await read_trajectory(request.app.state.store,source_kind=source_kind,
                        chain=chain,token=token,method=method,start=start,end=end,cutoff=cutoff,
                        max_points=max_points,gap_after_seconds=gap_after_seconds,
                        include_context=grid_version is not None)
                if grid_version is not None:
                    result=build_crossings(result,grid_version=grid_version,max_events=max_events)
                if day is not None:
                    result=build_daily(result,day)
                if features:
                    result,reuse=feature_cache.build(result)
                    headers['X-Midas-Feature-Cache']=f"reused={reuse['reused']}; computed={reuse['computed']}"
            except TrajectoryLimit as exc:
                raise HTTPException(413,str(exc)) from None
            except TimeoutError:
                raise HTTPException(503,"Trajectory query timed out; reduce interval") from None
            body=json.dumps(jsonable_encoder(result),separators=(',',':'),allow_nan=False).encode()
            if len(body)>4*1024**2:
                raise HTTPException(413,"Trajectory response exceeds 4 MiB; reduce interval")
            return body,headers
        try:(body,headers),cache_state=await trajectory_cache.run(key,compute)
        except ReaderBusy:raise HTTPException(429,"Context queue full or wait exceeded",headers={"Retry-After":"1"}) from None
        if features and cache_state.startswith('hit'):
            total=sum(int(part.split('=')[1]) for part in headers['X-Midas-Feature-Cache'].split(';'))
            headers={**headers,'X-Midas-Feature-Cache':f'reused={total}; computed=0'}
        return Response(content=body,media_type="application/json",headers={**headers,'X-Midas-Trajectory-Cache':cache_state})

    @app.get("/v1/trajectory")
    async def trajectory(request: Request, chain: str, token: str, method: str,
                         start: datetime, end: datetime, knowledge_cutoff: datetime,
                         source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                         max_points: int=Query(500,ge=1,le=2000),
                         gap_after_seconds: int | None=Query(None,ge=1,le=86400)):
        return await trajectory_response(request,chain,token,method,start,end,knowledge_cutoff,
                                         source_kind,max_points,gap_after_seconds)

    @app.get("/v1/trajectory/daily")
    async def daily_trajectory(request: Request, chain: str, token: str, method: str,
                               day: date, knowledge_cutoff: datetime,
                               source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                               max_points: int=Query(2000,ge=1,le=2000),
                               gap_after_seconds: int | None=Query(None,ge=1,le=86400)):
        cutoff=aware(knowledge_cutoff)
        try:
            start,end,_=day_bounds(day,cutoff)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        return await trajectory_response(request,chain,token,method,start,end,cutoff,
                                         source_kind,max_points,gap_after_seconds,day=day)

    @app.get("/v1/trajectory/crossings")
    async def trajectory_crossings(request: Request, chain: str, token: str, method: str,
                                   start: datetime, end: datetime, knowledge_cutoff: datetime,
                                   source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                                   grid_version: str=GRID_VERSION,
                                   max_points: int=Query(500,ge=1,le=2000),
                                   max_events: int=Query(1000,ge=1,le=2000),
                                   gap_after_seconds: int | None=Query(None,ge=1,le=86400)):
        if grid_version!=GRID_VERSION:
            raise HTTPException(422,"Unsupported market-cap grid")
        return await trajectory_response(request,chain,token,method,start,end,knowledge_cutoff,
            source_kind,max_points,gap_after_seconds,grid_version=grid_version,max_events=max_events)

    @app.get("/v1/trajectory/features")
    async def trajectory_features(request: Request, chain: str, token: str, method: str,
                                  start: datetime, end: datetime, knowledge_cutoff: datetime,
                                  source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                                  max_points: int=Query(500,ge=1,le=2000),
                                  gap_after_seconds: int | None=Query(None,ge=1,le=86400)):
        return await trajectory_response(request,chain,token,method,start,end,knowledge_cutoff,
            source_kind,max_points,gap_after_seconds,features=True)

    async def read_rule_body(request, model):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 8192:
                raise HTTPException(413,"Rule definition exceeds 8KiB")
            body.extend(part)
        try:
            return model.model_validate_json(bytes(body))
        except ValidationError as exc:
            raise HTTPException(422,exc.errors(include_input=False,include_context=False)) from None

    def active_owner(request):
        owner = getattr(request.app.state, 'feature_windows', None)
        if owner is None or owner.rules is None:
            raise HTTPException(503,"Active-rule owner unavailable")
        return owner.rules

    @app.post("/v1/rules/active",status_code=202)
    async def register_active_rule(request: Request):
        config = await read_rule_body(request, ActiveRule)
        try:
            async with asyncio.timeout(5):
                return await active_owner(request).register(config)
        except TrajectoryLimit as exc:
            raise HTTPException(429,str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Active-rule registration unavailable") from None

    @app.get("/v1/rules/active")
    async def list_active_rules(request: Request):
        return JSONResponse(active_owner(request).listing(),headers={"Cache-Control":"no-store"})

    @app.post("/v1/rules/active/{rule_id}/pause",status_code=204)
    async def pause_active_rule(request: Request,rule_id: str):
        try:
            async with asyncio.timeout(5):
                await active_owner(request).pause(rule_id)
        except KeyError:
            raise HTTPException(404,"Rule not found") from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Active-rule pause unavailable") from None
        return Response(status_code=204)

    @app.get("/v1/rules/active/{rule_id}/evaluations")
    async def rule_evaluations(request: Request,rule_id: str,limit: int=Query(50,ge=1,le=100),
                               before_sequence: int | None=Query(None,ge=1)):
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch("""SELECT decision FROM rule_evaluations
                    WHERE rule_id=$1 AND ($2::bigint IS NULL OR sequence<$2)
                    ORDER BY sequence DESC LIMIT $3""",rule_id,before_sequence,limit+1)
        except TimeoutError:
            raise HTTPException(503,"Rule history unavailable") from None
        values=[json.loads(r['decision']) for r in rows[:limit]]
        return dict(items=values,has_more=len(rows)>limit,
                    next_before_sequence=values[-1]['sequence'] if len(rows)>limit else None)

    @app.get("/v1/rule-frames/{frame_key}")
    async def rule_frame(request: Request,frame_key: str):
        try:
            async with asyncio.timeout(5):
                payload=await request.app.state.store.pool.fetchval(
                    "SELECT payload FROM rule_frames WHERE frame_key=$1",frame_key)
        except TimeoutError:
            raise HTTPException(503,"Rule frame unavailable") from None
        if payload is None:
            raise HTTPException(404,"Rule frame not found")
        return Response(content=bytes(payload),media_type="application/json")

    def paper_owner(request):
        owner=getattr(request.app.state,'feature_windows',None)
        if owner is None or owner.paper is None:
            raise HTTPException(503,"Paper owner unavailable")
        return owner.paper

    @app.get("/v1/paper/tracking")
    async def paper_tracking(request: Request):
        try:
            async with asyncio.timeout(5):
                result=await paper_owner(request).tracking_demands()
                return JSONResponse(result,headers={"Cache-Control":"no-store"})
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper tracking demand unavailable") from None

    def paper_row(row):
        return dict(row,policy=json.loads(row['policy']),definition=json.loads(row['definition']),
                    state=json.loads(row['state']))

    @app.post("/v1/paper/account",status_code=202)
    async def initialize_paper_account(request: Request):
        config=await read_rule_body(request,AccountPolicy)
        try:
            async with asyncio.timeout(5):
                return await paper_owner(request).account.initialize(config)
        except AccountConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper account initialization unavailable") from None

    @app.get("/v1/paper/account")
    async def paper_account(request: Request):
        try:
            async with asyncio.timeout(5):
                row=await request.app.state.store.pool.fetchrow("SELECT * FROM paper_account WHERE account_id='main'")
                if row is None:raise HTTPException(404,"Paper account not initialized")
                return JSONResponse(jsonable_encoder(account_row(row)),headers={"Cache-Control":"no-store"})
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper account state unavailable") from None

    @app.get("/v1/paper/account/events")
    async def paper_account_events(request: Request,limit: int=Query(50,ge=1,le=100),
                                   before_revision: int | None=Query(None,ge=1,le=9223372036854775807)):
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch("""SELECT record FROM paper_account_events
                    WHERE account_id='main' AND ($1::bigint IS NULL OR revision<$1)
                    ORDER BY revision DESC LIMIT $2""",before_revision,limit+1)
        except TimeoutError:
            raise HTTPException(503,"Paper account history unavailable") from None
        values=[json.loads(row['record']) for row in rows[:limit]]
        return dict(items=values,has_more=len(rows)>limit,
                    next_before_revision=values[-1]['revision'] if len(rows)>limit else None)

    @app.post("/v1/paper/bindings",status_code=202)
    async def register_paper_binding(request: Request):
        config=await read_rule_body(request,BindingRequest)
        try:
            async with asyncio.timeout(5):
                return await paper_owner(request).bindings.register(config)
        except AccountConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except TrajectoryLimit as exc:
            raise HTTPException(429,str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper binding registration unavailable") from None

    @app.get("/v1/paper/bindings")
    async def paper_bindings(request: Request):
        return JSONResponse(jsonable_encoder(paper_owner(request).bindings.listing()),headers={"Cache-Control":"no-store"})

    @app.post("/v1/paper/bindings/{rule_id}/pause",status_code=204)
    async def pause_paper_binding(request: Request,rule_id: str):
        try:
            async with asyncio.timeout(5):
                await paper_owner(request).bindings.pause(rule_id)
        except KeyError:
            raise HTTPException(404,"Paper binding not found") from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper binding pause unavailable") from None
        return Response(status_code=204)

    @app.get("/v1/paper/bindings/{rule_id}/events")
    async def paper_binding_events(request: Request,rule_id: str,limit: int=Query(50,ge=1,le=100),
                                   before_revision: int | None=Query(None,ge=1,le=9223372036854775807)):
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch("""SELECT record FROM paper_binding_events
                    WHERE rule_id=$1 AND ($2::bigint IS NULL OR revision<$2)
                    ORDER BY revision DESC LIMIT $3""",rule_id,before_revision,limit+1)
        except TimeoutError:
            raise HTTPException(503,"Paper binding history unavailable") from None
        values=[json.loads(row['record']) for row in rows[:limit]]
        return dict(items=values,has_more=len(rows)>limit,
                    next_before_revision=values[-1]['revision'] if len(rows)>limit else None)

    @app.post("/v1/paper/positions",status_code=202)
    async def create_paper_position(request: Request):
        config=await read_rule_body(request,PaperRequest)
        try:
            async with asyncio.timeout(5):
                return await paper_owner(request).create(config)
        except (PaperConflict,AccountConflict) as exc:
            raise HTTPException(409,str(exc)) from None
        except TrajectoryLimit as exc:
            raise HTTPException(429,str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper admission unavailable") from None

    @app.post("/v1/paper/positions/{position_id}/price-source",status_code=202)
    async def change_paper_price_source(request: Request,position_id: str):
        config=await read_rule_body(request,PriceSourceChange)
        try:
            async with asyncio.timeout(5):
                return await paper_owner(request).change_price_source(position_id,config)
        except KeyError:
            raise HTTPException(404,"Paper position not found") from None
        except PaperConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Paper price source change unavailable") from None

    @app.get("/v1/paper/positions")
    async def paper_positions(request: Request,limit: int=Query(50,ge=1,le=100),
                              before_number: int | None=Query(None,ge=1,le=9223372036854775807)):
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch("""SELECT * FROM paper_positions
                    WHERE ($1::bigint IS NULL OR position_number<$1)
                    ORDER BY position_number DESC LIMIT $2""",before_number,limit+1)
        except TimeoutError:
            raise HTTPException(503,"Paper positions unavailable") from None
        values=[paper_row(row) for row in rows[:limit]]
        return dict(items=values,has_more=len(rows)>limit,
            next_before_number=values[-1]['position_number'] if len(rows)>limit else None,
            scope='last persisted simulated positions; account_id identifies shared cash, NULL means legacy isolated lot; no current mark')

    @app.get("/v1/paper/positions/{position_id}")
    async def paper_position(request: Request,position_id: str):
        try:
            async with asyncio.timeout(5):
                row=await request.app.state.store.pool.fetchrow('SELECT * FROM paper_positions WHERE position_id=$1',position_id)
        except TimeoutError:
            raise HTTPException(503,"Paper position unavailable") from None
        if row is None:raise HTTPException(404,"Paper position not found")
        return paper_row(row)

    @app.get("/v1/paper/positions/{position_id}/events")
    async def paper_events(request: Request,position_id: str,limit: int=Query(50,ge=1,le=100),
                           before_revision: int | None=Query(None,ge=1,le=9223372036854775807)):
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch("""SELECT record FROM paper_events
                    WHERE position_id=$1 AND ($2::bigint IS NULL OR revision<$2)
                    ORDER BY revision DESC LIMIT $3""",position_id,before_revision,limit+1)
        except TimeoutError:
            raise HTTPException(503,"Paper ledger unavailable") from None
        values=[json.loads(row['record']) for row in rows[:limit]]
        return dict(items=values,has_more=len(rows)>limit,
            next_before_revision=values[-1]['revision'] if len(rows)>limit else None)

    @app.post("/v1/rules/evaluate")
    async def evaluate_rule(request: Request):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 8192:
                raise HTTPException(413,"Rule definition exceeds 8KiB")
            body.extend(part)
        try:
            rule = ComparisonRule.model_validate_json(bytes(body))
        except ValidationError as exc:
            raise HTTPException(422,exc.errors(include_input=False,include_context=False)) from None
        result = evaluate(rule, getattr(request.app.state, 'feature_windows', None))
        return JSONResponse(result, headers={"Cache-Control":"no-store"})

    def window_owner(request):
        owner = getattr(request.app.state, 'feature_windows', None)
        if owner is None:
            raise HTTPException(503,"Feature window owner unavailable")
        return owner

    @app.post("/v1/feature-windows",status_code=202)
    async def register_feature_window(request: Request, chain: str, token: str, method: str,
                                      source_kind: str=Query(...,pattern="^(fixture|gmgn|legacy_gmgn)$"),
                                      window_seconds: int=Query(900,ge=1,le=86400),
                                      gap_after_seconds: int | None=Query(None,ge=1,le=86400)):
        try:
            config=configuration(source_kind,chain,token,method,window_seconds,gap_after_seconds)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        try:
            async with asyncio.timeout(5):
                return await window_owner(request).register(config)
        except TrajectoryLimit as exc:
            raise HTTPException(429,str(exc)) from None
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Feature window registration unavailable") from None

    @app.get("/v1/feature-windows")
    async def feature_windows_status(request: Request):
        return window_owner(request).listing()

    @app.get("/v1/feature-windows/{window_id}")
    async def shared_feature_window(request: Request,window_id: str):
        try:
            payload=window_owner(request).read(window_id)
        except KeyError:
            raise HTTPException(404,"Feature window not registered") from None
        except WindowUnavailable:
            raise HTTPException(503,"Feature window refresh pending or lease expired") from None
        return Response(content=payload,media_type="application/json",headers={"Cache-Control":"no-store"})

    @app.delete("/v1/feature-windows/{window_id}",status_code=204)
    async def remove_feature_window(request: Request,window_id: str):
        try:
            async with asyncio.timeout(5):
                await window_owner(request).remove(window_id)
        except (WindowUnavailable,TimeoutError):
            raise HTTPException(503,"Feature window removal unavailable") from None
        return Response(status_code=204)

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
                      capture_limit: int=Query(20,ge=1,le=20),
                      capture_id: UUID | None=None):
        selected_capture_limit=1 if capture_id is not None else capture_limit
        rows=await request.app.state.store.catalog(source_kind,limit+1,selected_capture_limit,capture_id)
        has_more=len(rows)>limit
        return {"items":[dict(r) for r in rows[:limit]],"limit":limit,"source_kind":source_kind,
                "scope":("specified capture, latest published interpretation only" if capture_id is not None
                         else "latest captures, latest published interpretation only"),
                "capture_id":str(capture_id) if capture_id is not None else None,
                "capture_limit":selected_capture_limit,"has_more":has_more,"truncated":has_more,
                "coverage":"bounded catalog summary; raw endpoint preserves complete source evidence",
                "meaning":"observed fields; valid is not blanket economic homologation"}

    @app.get("/v1/ingestion")
    async def ingestion(request: Request, limit: int=Query(50,ge=1,le=100)):
        return await request.app.state.store.ingestion_status(limit)
    return app
