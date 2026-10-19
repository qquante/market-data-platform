"""One explicitly funded simulated account, on the existing paper transaction."""
from decimal import Decimal,localcontext
from datetime import datetime,timezone
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from .features import _hash,_json
from .paper_model import CASH_CONTEXT
from .feature_windows import WindowUnavailable
from .trajectory import TrajectoryLimit

ACCOUNT_VERSION='gmgn-paper-account-v1'
ACCOUNT_CHECK_VERSION='gmgn-paper-account-check-v1'
MONEY_PATTERN=r'^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?$'


class AccountAdmissionRefused(TrajectoryLimit):
    def __init__(self,reason,account_check):
        super().__init__(reason)
        self.account_check=account_check


class AccountConflict(ValueError):
    pass


class AccountPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version: Literal['gmgn-paper-account-v1']=ACCOUNT_VERSION
    initial_cash_usd: str=Field(pattern=MONEY_PATTERN)
    max_position_usd: str=Field(pattern=MONEY_PATTERN)
    max_exposure_usd: str=Field(pattern=MONEY_PATTERN)
    max_open_positions: int=Field(ge=1,le=4)

    @field_validator('initial_cash_usd','max_position_usd','max_exposure_usd')
    @classmethod
    def positive_money(cls,value):
        if Decimal(value)<=0:raise ValueError('Account amounts must be positive')
        return value.rstrip('0').rstrip('.') if '.' in value else value

    @model_validator(mode='after')
    def limits(self):
        if Decimal(self.max_position_usd)>Decimal(self.max_exposure_usd):
            raise ValueError('Position limit exceeds total exposure limit')
        return self

    def definition(self):
        return dict(self.model_dump(),cash_scope='one shared simulated account per schema; legacy isolated lots excluded',
            exposure_basis='pending budgets plus open entry costs; no current market valuation',
            reserve_policy='reserve full budget at admission; release residual on entry and full reserve on timeout',
            funding_policy='explicit initial funding once; no automatic reset, deposit or withdrawal')

    def key(self):
        return _hash(self.definition())


def position_definition(policy,account):
    definition=policy.definition()
    if account is not None:
        definition.update(cash_scope='shared simulated account',account_id='main',
                          account_version=ACCOUNT_VERSION,account_policy_id=account.key())
    return definition


def initial_state(policy):
    return dict(cash_usd=policy.initial_cash_usd,reserved_usd='0',invested_cost_usd='0',
                realized_pnl_usd='0',active_positions=0)


def balances(policy,state):
    with localcontext(CASH_CONTEXT):
        cash,reserved,invested,pnl=[Decimal(state[k]) for k in
            ('cash_usd','reserved_usd','invested_cost_usd','realized_pnl_usd')]
        if (not all(v.is_finite() for v in (cash,reserved,invested,pnl))
                or not cash>=reserved>=0 or invested<0
                or not 0<=state['active_positions']<=policy.max_open_positions
                or reserved+invested>Decimal(policy.max_exposure_usd)
                or cash+invested!=Decimal(policy.initial_cash_usd)+pnl):
            raise WindowUnavailable('Paper account balance invariant failed')
        return dict(available_usd=str(cash-reserved),committed_usd=str(reserved+invested))


def account_change(policy,previous,kind,budget,position):
    """Cash includes reserved cash; committed exposure uses cost, never unrealized P&L."""
    with localcontext(CASH_CONTEXT):
        balances(policy,previous)
        cash,reserved,invested,pnl=[Decimal(previous[k]) for k in
            ('cash_usd','reserved_usd','invested_cost_usd','realized_pnl_usd')]
        count=previous['active_positions'];amount=Decimal(budget)
        if kind=='requested':
            if amount>Decimal(policy.max_position_usd):raise TrajectoryLimit('Paper position amount limit')
            if count>=policy.max_open_positions:raise TrajectoryLimit('Paper account position count limit')
            if amount>cash-reserved:raise TrajectoryLimit('Insufficient available paper cash')
            if reserved+invested+amount>Decimal(policy.max_exposure_usd):raise TrajectoryLimit('Paper committed exposure limit')
            reserved+=amount;count+=1
        elif kind=='opened':
            cost=Decimal(position['entry']['cost_usd'])
            if not 0<cost<=amount:raise WindowUnavailable('Paper entry exceeds its reservation')
            cash-=cost;reserved-=amount;invested+=cost
        elif kind=='entry_expired':
            reserved-=amount;count-=1
        elif kind=='closed':
            cost=Decimal(position['entry']['cost_usd']);proceeds=Decimal(position['exit']['proceeds_usd'])
            cash+=proceeds;invested-=cost;pnl+=proceeds-cost;count-=1
        else:raise ValueError('Unsupported paper account action')
        state=dict(cash_usd=str(cash),reserved_usd=str(reserved),invested_cost_usd=str(invested),
                   realized_pnl_usd=str(pnl),active_positions=count)
        balances(policy,state)
        delta={k:str(Decimal(state[k])-Decimal(previous[k])) for k in state if k!='active_positions'}
        delta['active_positions']=count-previous['active_positions']
        return state,delta


def account_row(row):
    policy=AccountPolicy.model_validate_json(row['config'])
    if policy.key()!=row['policy_id'] or policy.definition()!=json.loads(row['definition']):
        raise WindowUnavailable('Stored paper account definition requires review')
    state=json.loads(row['state'])
    return dict(row,config=policy.model_dump(),definition=policy.definition(),state=state,
                balances=balances(policy,state),execution='simulated',
                scope='last persisted cash and entry cost; no mark-to-market')


class PaperAccount:
    def __init__(self,paper):
        self.paper=paper;self.policy=None

    async def load(self,con):
        row=await con.fetchrow("SELECT * FROM paper_account WHERE account_id='main'")
        if row:account_row(row)
        self.policy=AccountPolicy.model_validate_json(row['config']) if row else None
        return json.loads(row['state']) if row else None

    async def initialize(self,policy):
        async with self.paper.owner.guard:
            con=self.paper.connection()
            try:
                async with con.transaction():
                    row=await con.fetchrow("SELECT * FROM paper_account WHERE account_id='main' FOR UPDATE")
                    if row:
                        account_row(row)
                        if row['policy_id']!=policy.key():raise AccountConflict('Paper account already initialized with another policy')
                        self.policy=policy
                        return dict(account_id='main',created=False,execution='simulated')
                    state=initial_state(policy)
                    await con.execute("""INSERT INTO paper_account(account_id,policy_id,config,definition,state)
                        VALUES('main',$1,$2::jsonb,$3::jsonb,$4::jsonb)""",policy.key(),
                        _json(policy.model_dump()).decode(),_json(policy.definition()).decode(),_json(state).decode())
                    record=dict(version=ACCOUNT_VERSION,account_id='main',revision=1,kind='initialized',
                        execution='simulated',policy_id=policy.key(),state=state,initial_cash_usd=policy.initial_cash_usd,
                        recorded_action_at=datetime.now(timezone.utc).isoformat())
                    await con.execute("""INSERT INTO paper_account_events(account_id,revision,kind,record)
                        VALUES('main',1,'initialized',$1::jsonb)""",_json(record).decode())
                self.policy=policy
            except AccountConflict:raise
            except BaseException:
                self.paper.disconnect();raise
            return dict(account_id='main',created=True,execution='simulated')

    async def apply(self,con,key,revision,entry,state,event,now):
        if not entry.get('account_id') or event['kind'] in ('waiting','price_source_changed'):return
        row=await con.fetchrow("SELECT * FROM paper_account WHERE account_id='main' FOR UPDATE")
        if row is None:raise WindowUnavailable('Paper account disappeared')
        account_row(row)
        policy=AccountPolicy.model_validate_json(row['config'])
        if policy.key()!=self.policy.key():raise WindowUnavailable('Paper account policy changed')
        previous=json.loads(row['state'])
        try:
            updated,delta=account_change(policy,previous,event['kind'],entry['policy'].budget_usd,state)
        except TrajectoryLimit as exc:
            if event['kind']!='requested':raise
            # Capture the locked revision BEFORE the failed admission's savepoint rolls back.
            check=dict(version=ACCOUNT_CHECK_VERSION,checked_at=now.isoformat(),account_id='main',
                account_revision=row['revision'],account_policy_id=policy.key(),state_sha256=_hash(previous))
            raise AccountAdmissionRefused(str(exc),check) from exc
        seq=row['revision']+1
        record=dict(version=ACCOUNT_VERSION,account_id='main',revision=seq,kind=event['kind'],
            position_id=key,position_revision=revision,recorded_action_at=now.isoformat(),
            execution='simulated',policy_id=policy.key(),delta=delta,state=updated)
        await con.execute("""INSERT INTO paper_account_events(account_id,revision,kind,position_id,position_revision,record)
            VALUES('main',$1,$2,$3,$4,$5::jsonb)""",seq,event['kind'],key,revision,_json(record).decode())
        changed=await con.fetchval("""UPDATE paper_account SET state=$1::jsonb,revision=$2,updated_at=clock_timestamp()
            WHERE account_id='main' AND revision=$3 RETURNING revision""",_json(updated).decode(),seq,row['revision'])
        if changed!=seq:raise WindowUnavailable('Paper account revision changed')
