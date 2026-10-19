"""Durable per-signal paper lots on the existing window owner; no execution adapter."""
from datetime import datetime,timedelta,timezone
import hashlib,json
from decimal import Decimal,localcontext
from .features import _hash,_json
from .feature_windows import WindowUnavailable
from .paper_model import PAPER_VERSION,PaperPolicy,requested_state,transition,CASH_CONTEXT
from .paper_account import PaperAccount,AccountConflict,position_definition
from .paper_bindings import PaperBindings
from .rule_timers import ActiveRule,instant
from .trajectory import TrajectoryLimit
from .paper_prices import PaperPrices,PriceSourceChange,TRACKED_PAPER_VERSION,source_definition

MAX_OPEN_LOTS=4


class PaperConflict(ValueError):
    pass


class PaperPositions:
    def __init__(self,owner):
        self.owner=owner;self.entries={};self.connected=False
        self.account=PaperAccount(self)
        self.bindings=PaperBindings(self)
        self.prices=PaperPrices(self)

    def connection(self):
        if not self.connected or self.owner.connection is None or self.owner.connection.is_closed():
            raise WindowUnavailable('Paper owner unavailable')
        return self.owner.connection

    def disconnect(self):
        self.connected=False
        self.bindings.disconnect()
        self.prices.clear()

    async def connect(self,con):
        account_state=await self.account.load(con)
        rows=await con.fetch("""SELECT p.*,r.config AS rule_config FROM paper_positions p JOIN active_rules r USING(rule_id) WHERE p.status IN ('entry_pending','open')
            ORDER BY position_number LIMIT 5""")
        if len(rows)>MAX_OPEN_LOTS:raise WindowUnavailable('Stored paper position budget exceeded')
        entries={}
        for row in rows:
            policy=PaperPolicy.model_validate_json(row['policy'])
            if row['account_id'] and self.account.policy is None:raise WindowUnavailable('Paper account missing')
            definition=position_definition(policy,self.account.policy if row['account_id'] else None)
            if (_hash(definition)!=row['policy_id'] or definition!=json.loads(row['definition'])
                    or row['position_id']!=_hash([PAPER_VERSION,row['rule_id'],row['signal_sequence']])):
                raise WindowUnavailable('Stored paper definition requires review')
            rule=ActiveRule.model_validate_json(row['rule_config']);cfg=rule.comparison.window.canonical()
            if rule.key()!=row['rule_id'] or _hash(cfg)!=row['window_id']:
                raise WindowUnavailable('Stored paper identity requires review')
            identity=dict(source_kind=cfg['source_kind'],chain=cfg['chain'],token_address=cfg['token'])
            state=json.loads(row['state']);amendment=state.get('price_source_change')
            if policy.version==TRACKED_PAPER_VERSION and state.get('price_identity')!=identity:
                raise WindowUnavailable('Stored paper price identity requires review')
            if amendment:
                PriceSourceChange.model_validate(amendment['request'])
                event=await con.fetchval('SELECT record FROM paper_events WHERE position_id=$1 AND revision=$2',
                    row['position_id'],amendment['revision'])
                if (policy.version==TRACKED_PAPER_VERSION or amendment['identity']!=identity
                        or amendment['definition']!=source_definition() or amendment['source_policy_id']!=_hash(source_definition())
                        or amendment['request']['expected_policy_id']!=row['policy_id']
                        or amendment['original_policy_id']!=row['policy_id'] or amendment['original_window_id']!=row['window_id']
                        or amendment['revision']>row['revision'] or not event
                        or json.loads(event).get('kind')!='price_source_changed'
                        or json.loads(event).get('price_source_change')!=amendment):
                    raise WindowUnavailable('Stored paper source amendment requires review')
            entries[row['position_id']]=dict(policy=policy,price_identity=identity,window_id=row['window_id'],revision=row['revision'],
                state=json.loads(row['state']),signature=None,force=True,account_id=row['account_id'],policy_id=row['policy_id'])
        if account_state is not None:
            active=[e for e in entries.values() if e['account_id']]
            with localcontext(CASH_CONTEXT):
                reserved=sum((Decimal(e['policy'].budget_usd) for e in active if e['state']['status']=='entry_pending'),Decimal(0))
                invested=sum((Decimal(e['state']['entry']['cost_usd']) for e in active if e['state']['status']=='open'),Decimal(0))
                if (reserved!=Decimal(account_state['reserved_usd']) or invested!=Decimal(account_state['invested_cost_usd'])
                        or len(active)!=account_state['active_positions']):
                    raise WindowUnavailable('Paper account reservations differ from active positions')
        self.entries=entries;self.connected=True
        await self.bindings.connect(con)

    async def create(self,request):
        async with self.owner.guard:
            return await self.create_locked(request)

    async def create_locked(self,request):
        """Caller holds the existing owner guard; may join a binding transaction."""
        con=self.connection();policy=request.policy
        key=_hash([PAPER_VERSION,request.rule_id,request.signal_sequence])
        old=await con.fetchrow('SELECT policy,status FROM paper_positions WHERE position_id=$1',key)
        if old:
            if json.loads(old['policy'])!=policy.model_dump():raise PaperConflict('Signal already consumed with a different paper policy')
            return dict(position_id=key,status=old['status'],created=False,execution='simulated')
        if len(self.entries)>=MAX_OPEN_LOTS:raise TrajectoryLimit('At most four pending or open paper positions')
        signal=await con.fetchrow("""SELECT e.disposition,e.decision,r.config FROM rule_evaluations e
            JOIN active_rules r USING(rule_id) WHERE e.rule_id=$1 AND e.sequence=$2""",
            request.rule_id,request.signal_sequence)
        if signal is None or signal['disposition']!='signal':
            raise ValueError('A persisted internal signal is required')
        now=datetime.now(timezone.utc);event=json.loads(signal['decision'])
        age=(now-instant(event['evaluated_at'])).total_seconds()
        if not 0<=age<=policy.max_signal_age_seconds:raise ValueError('Signal is too old or in the future')
        rule=ActiveRule.model_validate_json(signal['config'])
        window_id=_hash(rule.comparison.window.canonical())
        window=self.owner.windows.get(window_id)
        if window is None or window['expires_at']<=now:
            raise ValueError('Register or renew the signal feature window first')
        cfg=rule.comparison.window.canonical()
        identity=dict(source_kind=cfg['source_kind'],chain=cfg['chain'],token_address=cfg['token'])
        state=requested_state(policy,now,event['event_id'],identity)
        if self.account.policy is None:raise AccountConflict('Initialize the shared paper account before new positions')
        definition=position_definition(policy,self.account.policy);policy_id=_hash(definition)
        entry=dict(policy=policy,price_identity=identity,window_id=window_id,revision=0,state=state,signature=None,force=True,
                   account_id='main',policy_id=policy_id)
        try:
            async with con.transaction():
                await con.execute("""INSERT INTO paper_positions(position_id,rule_id,signal_sequence,policy_id,policy,definition,window_id,state,status,account_id)
                    VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8::jsonb,'entry_pending','main')""",
                    key,request.rule_id,request.signal_sequence,policy_id,_json(policy.model_dump()).decode(),
                    _json(definition).decode(),window_id,_json(state).decode())
                await self.persist(key,entry,state,dict(kind='requested',reason='signal_consumed',cash_delta_usd='0'),
                                   None,now,'request')
            self.entries[key]=entry
        except (AccountConflict,TrajectoryLimit):raise
        except BaseException:
            self.disconnect();raise
        return dict(position_id=key,status='entry_pending',created=True,execution='simulated')

    async def change_price_source(self,key,request):
        async with self.owner.guard:
            con=self.connection()
            row=await con.fetchrow('SELECT policy_id,state,status FROM paper_positions WHERE position_id=$1',key)
            if row is None:raise KeyError(key)
            if row['policy_id']!=request.expected_policy_id:raise PaperConflict('Original policy does not match')
            old=json.loads(row['state']).get('price_source_change')
            if old:
                if old['request']!=request.model_dump():raise PaperConflict('Price source amendment is immutable')
                return dict(position_id=key,created=False,price_source_change=old,execution='simulated')
            entry=self.entries.get(key)
            if row['status']!='open' or entry is None or entry['policy'].version==TRACKED_PAPER_VERSION:
                raise PaperConflict('Only an open legacy position can change its future price source')
            now=await con.fetchval('SELECT clock_timestamp()')
            amendment=dict(request=request.model_dump(),definition=source_definition(),source_policy_id=_hash(source_definition()),
                identity=entry['price_identity'],effective_after=now.isoformat(),revision=entry['revision']+1,
                original_policy_id=entry['policy_id'],original_window_id=entry['window_id'])
            state=dict(entry['state'],price_source_change=amendment)
            try:
                await self.persist(key,entry,state,dict(kind='price_source_changed',reason=request.reason,
                    price_source_change=amendment,cash_delta_usd='0'),None,now,'explicit_source_amendment')
            except BaseException:
                self.disconnect();raise
            entry.update(force=True,signature=None)
            return dict(position_id=key,created=True,price_source_change=amendment,execution='simulated')

    async def tracking_demands(self):
        """Collection demand survives ranking/window expiry; it never reprices a lot."""
        async with self.owner.guard:
            con=self.connection()
            rows=await con.fetch("""SELECT p.position_id,p.rule_id,r.config FROM paper_positions p
                JOIN active_rules r USING(rule_id) WHERE p.status IN ('entry_pending','open')
                ORDER BY p.position_number LIMIT 5""")
            if len(rows)>MAX_OPEN_LOTS:raise WindowUnavailable('Stored paper position budget exceeded')
            grouped={}
            for row in rows:
                rule=ActiveRule.model_validate_json(row['config'])
                if rule.key()!=row['rule_id']:raise WindowUnavailable('Stored paper tracking identity requires review')
                cfg=rule.comparison.window.canonical()
                key=(cfg['source_kind'],cfg['chain'],cfg['token'])
                grouped.setdefault(key,[]).append(row['position_id'])
            return dict(version='gmgn-paper-tracking-demand-v1',scope='collection_only',
                price_policy_changed=False,max_positions=MAX_OPEN_LOTS,
                items=[dict(source_kind=k[0],chain=k[1],token_address=k[2],position_ids=ids)
                       for k,ids in sorted(grouped.items())])

    def signature(self,entry):
        window=self.owner.windows.get(entry['window_id'])
        if window is None:return ('missing',)
        if not self.owner.usable(window,datetime.now(timezone.utc)):return ('pending',)
        return ('ready',window['generation'],window['publication_cutoff'])

    async def persist(self,key,entry,state,event,payload,now,trigger):
        con=self.connection();revision=entry['revision']+1
        frame_key=hashlib.sha256(payload).hexdigest() if payload else None
        record=dict(version=PAPER_VERSION,position_id=key,revision=revision,event_id=_hash([key,revision]),
            recorded_action_at=now.isoformat(),execution='simulated',trigger=trigger,
            frame_key=frame_key,policy_id=entry['policy_id'],account_id=entry.get('account_id'),**event)
        async with con.transaction():
            if payload:
                await con.execute('INSERT INTO rule_frames(frame_key,payload) VALUES($1,$2) ON CONFLICT(frame_key) DO NOTHING',frame_key,payload)
            await con.execute("""INSERT INTO paper_events(position_id,revision,kind,record,frame_key)
                VALUES($1,$2,$3,$4::jsonb,$5)""",key,revision,event['kind'],_json(record).decode(),frame_key)
            updated=await con.fetchval("""UPDATE paper_positions SET revision=$2,state=$3::jsonb,status=$4,
                updated_at=clock_timestamp() WHERE position_id=$1 AND revision=$5 RETURNING revision""",
                key,revision,_json(state).decode(),state['status'],entry['revision'])
            if updated!=revision:raise WindowUnavailable('Paper revision changed; reload required')
            await self.account.apply(con,key,revision,entry,state,event,now)
        entry.update(revision=revision,state=state)

    async def step(self):
        self.connection()
        for key,entry in list(self.entries.items()):
            now=datetime.now(timezone.utc)
            tracked=entry['policy'].version==TRACKED_PAPER_VERSION or bool(entry['state'].get('price_source_change'))
            frame=None
            if tracked:
                if entry['state']['status']=='open' and now<instant(entry['state']['exit_due_at']):continue
                frame=await self.prices.read(entry['price_identity'],now)
                now=datetime.now(timezone.utc)
                signature=('token_info',frame['generation'],frame['next_due_at'])
            else:signature=self.signature(entry)
            due=instant(entry['state'].get('next_due_at'))
            reset=entry['force']
            if not reset and signature==entry['signature'] and (due is None or now<due):continue
            payload=None;snapshot=None
            if tracked:payload=frame['payload']
            else:
                try:payload=self.owner.read(entry['window_id'])
                except (KeyError,WindowUnavailable):pass
            if payload:snapshot=json.loads(payload)
            state,event=transition(entry['policy'],entry['state'],snapshot,now)
            trigger='recovery_or_request' if reset else 'timer' if signature==entry['signature'] else 'token_info_frame' if tracked else 'window'
            if event is not None:
                # Store only an actual consumed quote frame; waits have a diagnostic frame too when available.
                await self.persist(key,entry,state,event,payload,now,trigger)
            entry.update(signature=signature,force=False)
            if state['status'] in ('closed','entry_expired'):self.entries.pop(key)
        await self.bindings.step()
        self.prices.prune([e['price_identity'] for e in self.entries.values()
            if e['policy'].version==TRACKED_PAPER_VERSION or e['state'].get('price_source_change')])
