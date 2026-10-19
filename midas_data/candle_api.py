"""Authenticated bounded reads from immutable captured candle responses."""
import asyncio,json
from datetime import datetime
from uuid import UUID
from fastapi import Request,HTTPException,Query
from .contracts import CaptureMetadata,utc_datetime
from .candles import parse_candles,CandleContract
from .raw_store import RawIntegrityError

def install_candle_routes(app,slots):
    @app.get('/v1/candles/{capture_id}')
    async def snapshot(request:Request,capture_id:UUID,knowledge_cutoff:datetime,
        source_kind:str=Query(...,pattern='^(fixture|gmgn|legacy_gmgn)$')):
        try:cutoff=utc_datetime(knowledge_cutoff)
        except ValueError:raise HTTPException(422,'Timezone required') from None
        if slots.locked():raise HTTPException(429,'Context reader busy',headers={'Retry-After':'1'})
        async with slots:
            store=request.app.state.store
            rows=await store._temporal_fetch(cutoff,"""SELECT metadata,raw_sha256,available_at FROM captures
                WHERE capture_id=$1 AND source_kind=$2 AND endpoint='token_kline' AND available_at<=$3""",
                capture_id,source_kind,cutoff)
            if not rows:raise HTTPException(404,'Candle capture unavailable at cutoff')
            record=rows[0];meta=CaptureMetadata.model_validate_json(record['metadata'])
            try:
                def read():return parse_candles(meta,store.raw.read(record['raw_sha256']),record['available_at'])
                return await asyncio.to_thread(read)
            except CandleContract as exc:raise HTTPException(422,{'candle_contract':str(exc)}) from None
            except RawIntegrityError:raise HTTPException(503,'Candle evidence unavailable') from None
