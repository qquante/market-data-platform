"""Explicit future-only links from durable rule signals to shared paper cash."""
from datetime import datetime,timezone
from decimal import Decimal
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from .features import _hash,_json
from .feature_windows import WindowUnavailable
from .paper_model import PaperPolicy,PaperRequest
from .paper_account import AccountAdmissionRefused,AccountConflict,position_definition
from .trajectory import TrajectoryLimit

BINDING_VERSION='gmgn-paper-binding-v1'
MAX_BINDINGS=16


class BindingRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version: Literal['gmgn-paper-binding-v1']=BINDING_VERSION
    rule_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    policy: PaperPolicy

    def definition(self,account):
        return dict(version=BINDING_VERSION,rule_id=self.rule_id,account_id='main',
            position_policy=position_definition(self.policy,account),
            start_policy='strictly after rule sequence observed at enable or resume; no backlog from pauses',
            delivery_policy='one automatic attempt per durable signal; admission refusals are final for this binding',
            pause_policy='binding pause skips backlog and stops new admissions; admitted positions continue',
            source_policy='already emitted signals remain eligible after source rule pause; pause binding to stop admission',
            ordering='ascending rule_id; at most one signal per binding per owner step')


class PaperBindings:
    def __init__(self,paper):
        self.paper=paper;self.entries={};self.connected=False

    def connection(self):
        if not self.connected:raise WindowUnavailable('Paper binding owner unavailable')
        return self.paper.connection()

    def disconnect(self):
        self.connected=False

    async def connect(self,con):
        rows=await con.fetch('SELECT * FROM paper_bindings ORDER BY rule_id LIMIT 17')
        if len(rows)>MAX_BINDINGS:raise WindowUnavailable('Stored paper binding budget exceeded')
        entries={}
        for row in rows:
            config=BindingRequest.model_validate_json(row['config'])
            if self.paper.account.policy is None:raise WindowUnavailable('Binding account missing')
            definition=config.definition(self.paper.account.policy)
            if row['rule_id']!=config.rule_id or row['definition_id']!=_hash(definition) or json.loads(row['definition'])!=definition:
                raise WindowUnavailable('Stored paper binding definition requires review')
            rule=self.paper.owner.rules.entries.get(config.rule_id)
            if rule is None or row['cursor']>rule['sequence']:raise WindowUnavailable('Binding cursor exceeds rule history')
            entries[row['rule_id']]=dict(config=config,definition_id=row['definition_id'],enabled=row['enabled'],
                cursor=row['cursor'],revision=row['revision'],last=json.loads(row['last_event']) if row['last_event'] else None)
        self.entries=entries;self.connected=True

    async def persist(self,key,entry,cursor,*,enabled=None,event=None):
        con=self.connection()
        active=entry['enabled'] if enabled is None else enabled
        revision=entry['revision']+(1 if event else 0)
        record=None
        if event:
            record=dict(version=BINDING_VERSION,rule_id=key,definition_id=entry['definition_id'],
                revision=revision,cursor=cursor,enabled=active,execution='simulated',
                recorded_at=datetime.now(timezone.utc).isoformat(),**event)
            await con.execute("""INSERT INTO paper_binding_events(rule_id,revision,kind,signal_sequence,position_id,record)
                VALUES($1,$2,$3,$4,$5,$6::jsonb)""",key,revision,event['kind'],event.get('signal_sequence'),
                event.get('position_id'),_json(record).decode())
        changed=await con.fetchval("""UPDATE paper_bindings SET cursor=$2,revision=$3,enabled=$4,
            last_event=COALESCE($5::jsonb,last_event),updated_at=clock_timestamp()
            WHERE rule_id=$1 AND cursor=$6 AND revision=$7 RETURNING cursor""",
            key,cursor,revision,active,_json(record).decode() if record else None,entry['cursor'],entry['revision'])
        if changed!=cursor:raise WindowUnavailable('Paper binding cursor changed; reload required')
        return dict(cursor=cursor,revision=revision,enabled=active,last=record or entry['last'])

    async def register(self,config):
        async with self.paper.owner.guard:
            con=self.connection();key=config.rule_id;old=self.entries.get(key)
            if self.paper.account.policy is None:raise AccountConflict('Initialize the shared paper account first')
            definition=config.definition(self.paper.account.policy);identity=_hash(definition)
            if old:
                if old['definition_id']!=identity:raise AccountConflict('Rule already bound to another immutable paper policy')
                if old['enabled']:return dict(rule_id=key,enabled=True,created=False,cursor=old['cursor'],execution='simulated')
            elif len(self.entries)>=MAX_BINDINGS:raise TrajectoryLimit('At most sixteen stored paper bindings')
            rule=self.paper.owner.rules.entries.get(key)
            if rule is None:raise ValueError('Register the active-rule definition first')
            if Decimal(config.policy.budget_usd)>Decimal(self.paper.account.policy.max_position_usd):
                raise ValueError('Binding budget exceeds the account position limit')
            cursor=rule['sequence']
            entry=old or dict(config=config,definition_id=identity,enabled=True,cursor=cursor,revision=0,last=None)
            try:
                async with con.transaction():
                    if old is None:
                        await con.execute("""INSERT INTO paper_bindings(rule_id,config,definition_id,definition,cursor)
                            VALUES($1,$2::jsonb,$3,$4::jsonb,$5)""",key,_json(config.model_dump()).decode(),
                            identity,_json(definition).decode(),cursor)
                    update=await self.persist(key,entry,cursor,enabled=True,event=dict(kind='enabled',
                        reason='future_signals_only',skipped_after=entry['cursor'],through_sequence=cursor))
                entry.update(update);self.entries[key]=entry
            except BaseException:
                self.paper.disconnect();raise
            return dict(rule_id=key,enabled=True,created=old is None,cursor=cursor,execution='simulated')

    async def pause(self,key):
        async with self.paper.owner.guard:
            con=self.connection();entry=self.entries[key]
            if not entry['enabled']:return
            cursor=self.paper.owner.rules.entries[key]['sequence']
            try:
                async with con.transaction():
                    update=await self.persist(key,entry,cursor,enabled=False,event=dict(kind='paused',
                        reason='operator_paused',skipped_after=entry['cursor'],through_sequence=cursor))
                entry.update(update)
            except BaseException:
                self.paper.disconnect();raise

    def listing(self):
        connected=self.connected and self.paper.connected and self.paper.owner.connection is not None
        return dict(version=BINDING_VERSION,owner_connected=connected,limit=MAX_BINDINGS,
            scope='automatic simulated admissions; source rules and position lifecycle are controlled separately',
            items=[dict(rule_id=k,config=e['config'].model_dump(),definition_id=e['definition_id'],enabled=e['enabled'],
                cursor=e['cursor'],revision=e['revision'],last_event=e['last']) for k,e in sorted(self.entries.items())])

    async def step(self):
        con=self.connection()
        # Read durable signal history only when a source sequence advanced; never poll it per reader.
        for key,entry in sorted(self.entries.items()):
            target=self.paper.owner.rules.entries[key]['sequence']
            if not entry['enabled'] or target<=entry['cursor']:continue
            try:
                async with con.transaction():
                    signal=await con.fetchrow("""SELECT sequence,decision FROM rule_evaluations
                        WHERE rule_id=$1 AND disposition='signal' AND sequence>$2 AND sequence<=$3
                        ORDER BY sequence LIMIT 1""",key,entry['cursor'],target)
                    if signal is None:
                        update=await self.persist(key,entry,target)
                    else:
                        seq=signal['sequence'];decision=json.loads(signal['decision'])
                        event=dict(signal_sequence=seq,signal_event_id=decision['event_id'])
                        try:
                            result=await self.paper.create_locked(PaperRequest(rule_id=key,signal_sequence=seq,policy=entry['config'].policy))
                        except ValueError as exc:
                            event.update(kind='rejected',reason=str(exc))
                            if isinstance(exc,AccountAdmissionRefused):event['account_check']=exc.account_check
                        else:
                            event.update(kind='admitted' if result['created'] else 'existing',
                                reason='signal_admitted' if result['created'] else 'signal_already_consumed',
                                position_id=result['position_id'])
                        update=await self.persist(key,entry,seq,event=event)
                entry.update(update)
            except BaseException:
                # Nested paper admission may have changed memory before the outer commit was acknowledged.
                # Stop all paper admissions until the existing owner reloads both durable ledgers.
                self.paper.disconnect();raise
