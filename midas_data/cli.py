from __future__ import annotations

import argparse
import asyncio
import json

from .settings import Settings


def main():
    parser=argparse.ArgumentParser(prog="midas-data")
    parser.add_argument("command",choices=["api","worker","status","rebuild-analytics","healthcheck","renormalize"])
    parser.add_argument("--source-kind",choices=["fixture","gmgn","legacy_gmgn"])
    parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args()
    if args.command=="renormalize" and (args.source_kind is None or not 1 <= args.limit <= 100):
        parser.error("renormalize requires --source-kind and --limit between 1 and 100")
    settings=Settings.from_env()
    if args.command=="api":
        import uvicorn
        from .api import make_app
        uvicorn.run(make_app(settings),host="0.0.0.0",port=8791,access_log=False,log_level="warning")
    elif args.command=="worker":
        from .worker import main as worker_main
        worker_main()
    else:
        asyncio.run(admin(settings,args.command,args.source_kind,args.limit))


async def admin(settings,command,source_kind=None,limit=100):
    from .raw_store import RawStore
    from .store import Store
    store=await Store(settings.database_dsn,RawStore(settings.raw_path,settings.max_capture_bytes)).open()
    try:
        if command=="healthcheck":
            fresh=await store.pool.fetchval("SELECT EXISTS(SELECT 1 FROM worker_health WHERE worker_name='data-worker' AND state='ready' AND heartbeat_at>clock_timestamp()-interval '45 seconds')")
            if not fresh:
                raise SystemExit(1)
        elif command=="status":
            print(json.dumps(await store.status(),default=str,indent=2))
        elif command=="renormalize":
            from .contracts import CaptureMetadata
            from .normalize import normalize_capture,NORMALIZER_VERSION
            async with store.pool.acquire() as owner:
                if not await owner.fetchval("SELECT pg_try_advisory_lock(60608002)"):
                    raise RuntimeError("Stop the NEW data worker before renormalizing")
                rows=await store.pool.fetch(
                    """SELECT c.capture_id,c.raw_sha256,c.metadata FROM captures c WHERE source_kind=$1
                    AND NOT EXISTS(SELECT 1 FROM normalization_runs r
                    WHERE r.capture_id=c.capture_id AND r.normalizer_version=$2)
                    ORDER BY c.recorded_at,c.capture_id LIMIT $3""",source_kind,NORMALIZER_VERSION,limit)
                processed=[]
                for row in rows:
                    await owner.fetchval("SELECT 1")
                    metadata=CaptureMetadata.model_validate_json(row["metadata"])
                    raw=await asyncio.to_thread(store.raw.read,row["raw_sha256"])
                    result=await asyncio.to_thread(normalize_capture,metadata,raw)
                    await store.record_normalization(row["capture_id"],result)
                    processed.append(str(row["capture_id"]))
                print(json.dumps({"normalizer_version":NORMALIZER_VERSION,"source_kind":source_kind,
                                  "processed":len(processed),"capture_ids":processed,
                                  "analytics":"pending worker verification"}))
        else:
            from .analytics import AnalyticsSinks
            sinks=AnalyticsSinks(settings.lake_catalog_dsn,settings.lake_path,settings.clickhouse_url,
                                settings.clickhouse_user,settings.clickhouse_password)
            async with store.pool.acquire() as owner:
                if not await owner.fetchval("SELECT pg_try_advisory_lock(60608002)"):
                    raise RuntimeError("Stop the NEW data worker before rebuilding analytics")
                try:
                    await asyncio.to_thread(sinks.ensure_schema)
                    after=""
                    count=0
                    while True:
                        batches=await store.pool.fetch(
                            "SELECT batch_id FROM normalization_runs WHERE available_at IS NOT NULL AND batch_id>$1 ORDER BY batch_id LIMIT 50",after)
                        if not batches:
                            break
                        for batch in batches:
                            rows=await store.batch_rows(batch["batch_id"])
                            await asyncio.to_thread(sinks.apply_batch,batch["batch_id"],rows)
                            receipt=await asyncio.to_thread(sinks.verify_batch,batch["batch_id"])
                            await store.mark_analytics(batch["batch_id"],receipt)
                            after=batch["batch_id"]
                            count+=1
                    print(json.dumps({"reconciled_batches":count,"snapshot":sinks.snapshot_info()},default=str))
                finally:
                    await asyncio.to_thread(sinks.close)
    finally:
        await store.close()


if __name__=="__main__":
    main()
