"""One bounded owner of durable rolling feature snapshots; consumers read memory."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import logging
import time

from .features import SharedFeatures, _hash, _json
from .normalize import canonical_identity
from .trajectory import METHODS, TrajectoryLimit, read_trajectory

WINDOW_VERSION = 'gmgn-live-window-v1'
MAX_WINDOWS, POLL_SECONDS, MAX_FRAME_SECONDS, LEASE_SECONDS = 4, 2, 60, 1800
PUBLICATION_BATCH, MAX_BYTES = 128, 4*1024**2
log = logging.getLogger('midas-data')


class WindowUnavailable(RuntimeError):
    pass


def configuration(source_kind, chain, token, method, window_seconds, gap_after_seconds=None):
    chain, token = canonical_identity(chain, token)
    if source_kind not in ('gmgn', 'fixture', 'legacy_gmgn') or method not in METHODS:
        raise ValueError('Unsupported window source or method')
    gap = gap_after_seconds if gap_after_seconds is not None else METHODS[method][1]
    if not 1 <= window_seconds <= 86400 or not 1 <= gap <= 86400:
        raise ValueError('Window and gap must be between 1 and 86400 seconds')
    return dict(version=WINDOW_VERSION, source_kind=source_kind, chain=chain, token=token,
                method=method, window_seconds=window_seconds, gap_after_seconds=gap)


def next_due(series):
    """Semantic boundaries plus a bounded clock/reconciliation refresh."""
    now, duration = series['end'], series['end']-series['start']
    gap = timedelta(seconds=series['coverage']['gap_after_seconds'])
    epsilon = timedelta(microseconds=1)
    candidates = [now+timedelta(seconds=MAX_FRAME_SECONDS)]
    points = series['points']
    candidates += [p['observed_at']+duration+epsilon for p in points]  # [start,end): left boundary is inclusive.
    valid = [p for p in points if p['state']=='available']
    if valid:
        candidates += [valid[0]['observed_at']+duration-gap, valid[-1]['observed_at']+gap+epsilon]
    if points:
        candidates.append(points[-1]['observed_at']+gap+epsilon)
    return min(value for value in candidates if value > now)


class FeatureWindows:
    def __init__(self, store, slots=None):
        self.store, self.slots = store, slots if slots is not None else asyncio.Semaphore(2)
        self.features = SharedFeatures()
        self.windows = {}
        self.guard = asyncio.Lock()
        self.connection = None
        self.task = None
        self.frontier = None
        self.checked_monotonic = 0.
        self.last_error = None
        self.rules = None
        self.paper = None
        self.reads = self.polls = self.publication_updates = self.clock_updates = 0

    async def connect(self):
        con = await self.store.pool.acquire()
        try:
            if not await con.fetchval('SELECT pg_try_advisory_lock(hashtext(current_schema()),60608005)'):
                raise WindowUnavailable('Feature window owner already running')
            rows = await con.fetch('SELECT * FROM feature_windows WHERE expires_at>clock_timestamp() ORDER BY window_id LIMIT 5')
            if len(rows)>MAX_WINDOWS:
                raise WindowUnavailable('Stored window budget exceeded')
            for row in rows:
                cfg = json.loads(row['config'])
                expected = configuration(cfg['source_kind'], cfg['chain'], cfg['token'], cfg['method'],
                                         cfg['window_seconds'], cfg['gap_after_seconds'])
                if cfg != expected or row['window_id'] != _hash(expected):
                    raise WindowUnavailable('Stored window definition requires review')
            self.windows = {r['window_id']:dict(r, config=json.loads(r['config']), dirty='recovery',
                                              ready=False, error=None) for r in rows}
            self.frontier = min((r['publication_cutoff'] for r in rows if r['publication_cutoff']), default=None)
            self.checked_monotonic = 0.
            self.connection = con
            if self.rules:
                await self.rules.connect(con)
            if self.paper:
                await self.paper.connect(con)
        except BaseException:
            self.connection = None
            if self.rules:
                self.rules.disconnect()
            if self.paper:
                self.paper.disconnect()
            # Pool reset releases session locks, including when initialization fails.
            await self.store.pool.release(con)
            raise

    async def disconnect(self):
        if self.paper:
            self.paper.disconnect()
        if self.rules:
            self.rules.disconnect()
        con, self.connection = self.connection, None
        self.checked_monotonic = 0.
        for entry in self.windows.values():
            entry['ready'] = False
        if con is not None:
            await self.store.pool.release(con)

    def start(self):
        self.task = asyncio.create_task(self.run(), name='shared-feature-windows')

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        else:
            await self.disconnect()

    async def run(self):
        try:
            while True:
                try:
                    async with self.guard:
                        if self.connection is None:
                            await self.connect()
                        await self.step()
                        self.last_error = None
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    log.warning('Feature windows paused: %s', self.last_error)
                    async with self.guard:
                        await self.disconnect()
                # A short, bounded wait also discovers publications; reads do not poll SQL.
                await asyncio.sleep(POLL_SECONDS)
        finally:
            async with self.guard:
                await self.disconnect()

    async def register(self, config):
        async with self.guard:
            if self.connection is None:
                raise WindowUnavailable('Feature window owner unavailable')
            con = self.connection
            now = await con.fetchval('SELECT clock_timestamp()')
            key = _hash(config)
            active = {k:v for k,v in self.windows.items() if v['expires_at']>now}
            if key not in active and len(active)>=MAX_WINDOWS:
                raise TrajectoryLimit('At most four active feature windows')
            # Registration never asks the collector for additional data.
            async with con.transaction():
                await con.execute('DELETE FROM feature_windows WHERE expires_at<=$1',now)
                row = await con.fetchrow("""INSERT INTO feature_windows(window_id,config,expires_at)
                    VALUES($1,$2::jsonb,$3) ON CONFLICT(window_id) DO UPDATE
                    SET expires_at=EXCLUDED.expires_at RETURNING *""",key,json.dumps(config),now+timedelta(seconds=LEASE_SECONDS))
            self.windows = active
            if key in self.windows:
                self.windows[key]['expires_at'] = row['expires_at']
            else:
                self.windows[key] = dict(row,config=config,dirty='initial',ready=False,error=None)
            return dict(window_id=key,expires_at=row['expires_at'],state='registered',
                        collection_changed=False)

    async def remove(self, key):
        async with self.guard:
            if self.connection is None:
                raise WindowUnavailable('Feature window owner unavailable')
            await self.connection.execute('DELETE FROM feature_windows WHERE window_id=$1',key)
            self.windows.pop(key,None)

    def listing(self):
        now = datetime.now(timezone.utc)
        return dict(version=WINDOW_VERSION,owner_connected=self.connection is not None,
            last_error=self.last_error,limits=dict(windows=MAX_WINDOWS,points=2000,frame_bytes=MAX_BYTES,
                publication_batch=PUBLICATION_BATCH,poll_seconds=POLL_SECONDS,lease_seconds=LEASE_SECONDS),
            counters=dict(trajectory_reads=self.reads,publication_polls=self.polls,
                          publication_updates=self.publication_updates,clock_updates=self.clock_updates),
            items=[dict(window_id=k,config=e['config'],generation=e['generation'],
                expires_at=e['expires_at'],publication_cutoff=e['publication_cutoff'],
                next_due_at=e['next_due_at'],state='ready' if self.usable(e,now) else 'pending',
                last_error=e['error']) for k,e in self.windows.items() if e['expires_at']>now])

    def usable(self, entry, now):
        return (self.connection is not None and not self.connection.is_closed() and entry['ready']
            and entry['expires_at']>now and entry['next_due_at'] is not None and now<entry['next_due_at']
            and time.monotonic()-self.checked_monotonic < 10)

    def read(self, key):
        entry = self.windows.get(key)
        if entry is None:
            raise KeyError(key)
        if not self.usable(entry,datetime.now(timezone.utc)):
            raise WindowUnavailable('Feature window refresh pending or lease expired')
        return bytes(entry['payload'])

    async def step(self):
        con = self.connection
        # Empty registry costs one small clock query, never a scan of facts/history.
        async with con.transaction(isolation='read_committed'):
            await con.execute('SELECT pg_advisory_xact_lock_shared(60608003)')
            cutoff = await con.fetchval('SELECT clock_timestamp()')
            sampled_monotonic = time.monotonic()
            events = []
            if self.windows and self.frontier is not None:
                events = await con.fetch("""SELECT r.capture_id,r.available_at,c.source_kind,c.chain,c.endpoint,c.received_at
                    FROM (SELECT capture_id,available_at,normalizer_version FROM normalization_runs
                          WHERE available_at>$1 AND available_at<=$2 ORDER BY available_at,capture_id,normalizer_version LIMIT 129) r
                    JOIN captures c USING(capture_id)""",self.frontier,cutoff)
            self.polls += 1
        active = {k:v for k,v in self.windows.items() if v['expires_at']>cutoff}
        self.windows = active
        interested = set()
        if 0<len(events)<=PUBLICATION_BATCH and active:
            pairs = await con.fetch("""SELECT DISTINCT capture_id,token_address FROM facts
                WHERE capture_id=ANY($1::uuid[]) AND token_address=ANY($2::text[]) AND available_at<=$3""",
                list({r['capture_id'] for r in events}),list({e['config']['token'] for e in active.values()}),cutoff)
            interested = {(r['capture_id'],r['token_address']) for r in pairs}
        # Mark every affected entry before any await/refresh so it cannot serve the old frame.
        for entry in active.values():
            cfg = entry['config']
            affected = len(events)>PUBLICATION_BATCH or any(
                r['source_kind']==cfg['source_kind'] and r['chain']==cfg['chain']
                and r['endpoint']==METHODS[cfg['method']][0]
                and r['received_at']>=cutoff-timedelta(seconds=cfg['window_seconds'])
                and (r['capture_id'],cfg['token']) in interested for r in events)
            if affected:
                entry['dirty'] = 'publication'
            if not entry['dirty'] and entry['next_due_at']<=cutoff:
                entry['dirty'] = 'clock'
            if entry['dirty']:
                entry['ready'] = False
        for key,entry in list(active.items()):
            if not entry['dirty']:
                continue
            if self.slots.locked():
                continue
            try:
                async with self.slots:
                    async with asyncio.timeout(5):
                        cfg = entry['config']
                        self.reads += 1
                        series = await read_trajectory(self.store,source_kind=cfg['source_kind'],
                            chain=cfg['chain'],token=cfg['token'],method=cfg['method'],max_points=2000,
                            start=cutoff-timedelta(seconds=cfg['window_seconds']),end=cutoff,cutoff=cutoff,
                            gap_after_seconds=cfg['gap_after_seconds'])
                        result,_ = self.features.build(series)
                        due = next_due(series)
                        # Future-received captures are not observations yet; wake after their entry boundary.
                        future = [r['received_at']+timedelta(microseconds=1) for r in events
                            if r['source_kind']==cfg['source_kind'] and r['chain']==cfg['chain']
                            and r['endpoint']==METHODS[cfg['method']][0] and r['received_at']>=cutoff
                            and (r['capture_id'],cfg['token']) in interested]
                        if future:
                            due = min(due,min(future))
                        generation = entry['generation']+1
                        payload = _json(dict(version=WINDOW_VERSION,window_id=key,generation=generation,
                            publication_cutoff=cutoff,next_due_at=due,trigger=entry['dirty'],
                            persistence='latest durable projection; strategy consumption and frame history are not recorded',result=result))
                        if len(payload)>MAX_BYTES:
                            raise TrajectoryLimit('Shared feature frame exceeds 4 MiB')
                        # The owner session writes frame, consumed cutoff and timer together.
                        await con.execute("""UPDATE feature_windows SET payload=$2,publication_cutoff=$3,
                            next_due_at=$4,generation=$5,updated_at=clock_timestamp() WHERE window_id=$1""",
                            key,payload,cutoff,due,generation)
                if entry['dirty']=='publication':
                    self.publication_updates += 1
                elif entry['dirty']=='clock':
                    self.clock_updates += 1
                entry.update(payload=payload,publication_cutoff=cutoff,next_due_at=due,generation=generation,
                             dirty=None,ready=True,error=None)
            except Exception as exc:
                entry['error'] = type(exc).__name__
                entry['ready'] = False
        self.frontier = cutoff
        self.checked_monotonic = sampled_monotonic
        if self.rules:
            await self.rules.step()
        if self.paper:
            await self.paper.step()
