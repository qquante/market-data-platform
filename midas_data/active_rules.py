"""Bounded active rules on the shared-window owner; local durable signals only."""
from datetime import datetime, timezone
import hashlib
import json
import time

from .features import _hash, _json
from .feature_windows import WindowUnavailable
from .rule_timers import ACTIVE_VERSION, ActiveRule, advance, instant
from .rules import evaluate
from .trajectory import TrajectoryLimit

MAX_RULES=16


class ActiveRules:
    def __init__(self, owner):
        self.owner=owner
        self.entries={}
        self.connected=False
        self.last_step_monotonic=None

    async def connect(self, con):
        rows=await con.fetch('SELECT * FROM active_rules ORDER BY rule_id LIMIT 17')
        if len(rows)>MAX_RULES:
            raise WindowUnavailable('Stored active-rule budget exceeded')
        entries={}
        for row in rows:
            config=ActiveRule.model_validate_json(row['config'])
            if config.key()!=row['rule_id'] or config.definition()!=json.loads(row['definition']):
                raise WindowUnavailable('Stored rule definition requires review')
            entries[row['rule_id']]=dict(config=config,enabled=row['enabled'],sequence=row['sequence'],
                state=json.loads(row['state']),last=json.loads(row['last_decision']) if row['last_decision'] else None,
                signature=None,force=True)
        self.entries=entries
        self.connected=True
        self.last_step_monotonic=None

    def disconnect(self):
        self.connected=False
        for entry in self.entries.values():
            entry.update(signature=None,force=True)

    def require_connection(self):
        if not self.connected or self.owner.connection is None or self.owner.connection.is_closed():
            raise WindowUnavailable('Active-rule owner unavailable')
        return self.owner.connection

    def signature(self, rule):
        key=_hash(rule.comparison.window.canonical())
        window=self.owner.windows.get(key)
        if window is None:
            return ('missing',)
        if not self.owner.usable(window,datetime.now(timezone.utc)):
            return ('pending',)
        return ('ready',window['generation'],window['publication_cutoff'])

    async def register(self, config):
        async with self.owner.guard:
            con=self.require_connection()
            key=config.key()
            old=self.entries.get(key)
            if old and old['enabled']:
                return dict(rule_id=key,enabled=True,created=False,collection_changed=False)
            if old is None and len(self.entries)>=MAX_RULES:
                raise TrajectoryLimit('At most sixteen stored rule definitions')
            window=self.owner.windows.get(_hash(config.comparison.window.canonical()))
            if window is None or window['expires_at']<=datetime.now(timezone.utc):
                raise ValueError('Register or renew the feature window before enabling its rule')
            try:
                if old:
                    await con.execute('UPDATE active_rules SET enabled=true,updated_at=clock_timestamp() WHERE rule_id=$1',key)
                    old.update(enabled=True,force=True,signature=None)
                else:
                    await con.execute("""INSERT INTO active_rules(rule_id,config,definition)
                        VALUES($1,$2::jsonb,$3::jsonb)""",key,_json(config.wire()).decode(),_json(config.definition()).decode())
                    self.entries[key]=dict(config=config,enabled=True,sequence=0,state={},last=None,signature=None,force=True)
                return dict(rule_id=key,enabled=True,created=old is None,collection_changed=False)
            except BaseException:
                self.disconnect()
                raise

    async def pause(self, key):
        async with self.owner.guard:
            self.require_connection()
            entry=self.entries[key]
            if not entry['enabled']:
                return
            now=datetime.now(timezone.utc)
            condition=dict(state='unknown',reason='operator_paused',frame=None,evaluation_id=None)
            state,decision=advance(entry['config'],entry['state'],condition,None,now,reset=True)
            try:
                await self.persist(key,entry,state,decision,condition,None,now,'pause',enabled=False)
            except BaseException:
                self.disconnect()
                raise
            entry.update(enabled=False,force=True,signature=None)

    def listing(self):
        now=datetime.now(timezone.utc)
        connected=self.connected and self.owner.connection is not None and not self.owner.connection.is_closed()
        items=[]
        for key,entry in self.entries.items():
            due=instant(entry['state'].get('next_due_at'))
            pending=(entry['enabled'] and (not connected or entry['force']
                or entry['signature']!=self.signature(entry['config']) or (due is not None and now>=due)))
            items.append(dict(rule_id=key,enabled=entry['enabled'],config=entry['config'].wire(),
                sequence=entry['sequence'],evaluation_pending=bool(pending),
                last_recorded_decision=entry['last'],state=entry['state']))
        return dict(version=ACTIVE_VERSION,owner_connected=connected,limit=MAX_RULES,items=items,
            scope='last recorded evaluations; internal signals are not orders, fills or delivered messages')

    async def persist(self,key,entry,state,decision,condition,payload,now,trigger,*,enabled=True):
        con=self.require_connection()
        sequence=entry['sequence']+1
        frame_key=hashlib.sha256(payload).hexdigest() if payload is not None else None
        record=dict(version=ACTIVE_VERSION,rule_id=key,sequence=sequence,evaluated_at=now.isoformat(),
            trigger=trigger,condition_id=condition['evaluation_id'],frame_key=frame_key,frame=condition['frame'],
            continuity_reset=trigger in ('recovery_or_registration','runtime_gap'),
            unevaluated_since=entry['state'].get('last_evaluated_at') if trigger in ('recovery_or_registration','runtime_gap') else None,
            true_since=state['true_since'],observed_through=state['observed_through'],
            next_due_at=state['next_due_at'],**decision)
        record['event_id']=_hash([key,sequence])
        state=dict(state,last_evaluated_at=now.isoformat())
        # One transaction prevents a signal without its state, or state without its evidence.
        async with con.transaction():
            if payload is not None:
                await con.execute("""INSERT INTO rule_frames(frame_key,payload) VALUES($1,$2)
                    ON CONFLICT(frame_key) DO NOTHING""",frame_key,payload)
            await con.execute("""INSERT INTO rule_evaluations(rule_id,sequence,decision,frame_key,disposition)
                VALUES($1,$2,$3::jsonb,$4,$5)""",key,sequence,_json(record).decode(),frame_key,decision['disposition'])
            changed=await con.fetchval("""UPDATE active_rules SET state=$3::jsonb,last_decision=$4::jsonb,
                sequence=$2,enabled=$5,updated_at=clock_timestamp()
                WHERE rule_id=$1 AND sequence=$6 RETURNING sequence""",
                key,sequence,_json(state).decode(),_json(record).decode(),enabled,entry['sequence'])
            if changed!=sequence:
                raise WindowUnavailable('Rule generation changed; reload required')
        entry.update(sequence=sequence,state=state,last=record)

    async def step(self):
        self.require_connection()
        sampled=time.monotonic()
        runtime_gap=self.last_step_monotonic is not None and sampled-self.last_step_monotonic>=10
        # Inspect at most sixteen in-memory signatures; unchanged rules do no I/O/evaluation.
        for key,entry in self.entries.items():
            if not entry['enabled']:
                continue
            config=entry['config']
            signature=self.signature(config)
            now=datetime.now(timezone.utc)
            due=instant(entry['state'].get('next_due_at'))
            reset=entry['force'] or runtime_gap
            if not reset and signature==entry['signature'] and (due is None or now<due):
                continue
            condition=evaluate(config.comparison,self.owner)
            payload=None
            snapshot=None
            if condition['frame'] is not None:
                # No await between predicate and the snapshot it consumed.
                payload=self.owner.read(condition['window_id'])
                snapshot=json.loads(payload)
            state,decision=advance(config,entry['state'],condition,snapshot,now,reset=reset)
            trigger=('runtime_gap' if runtime_gap else 'recovery_or_registration' if reset
                     else 'timer' if signature==entry['signature'] else 'window')
            await self.persist(key,entry,state,decision,condition,payload,now,trigger)
            entry.update(signature=signature,force=False)
        self.last_step_monotonic=sampled
