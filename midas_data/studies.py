"""Bounded descriptive studies on frozen evidence; no fitted model or provider I/O."""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import timedelta
from decimal import Decimal,localcontext
from typing import Literal
from pydantic import Field,StrictInt,StrictStr,model_validator
from .contracts import ContractModel,utc_datetime
from .cohorts import known,latest_frame,labels
from .features import _hash
from .research_contracts import number

STUDY_VERSION='gmgn-descriptive-studies-v1'


class StudySpec(ContractModel):
    name:StrictStr=Field(min_length=1,max_length=80)
    kind:Literal['transitions','matched_controls','event_study','prefix_neighbors']
    cap_bins_usd:list[StrictStr]=Field(default_factory=list,max_length=49)
    time_block_seconds:StrictInt|None=Field(default=None,ge=1,le=86400)
    holder_exposure_max:StrictStr|None=None
    level_usd:StrictStr|None=None
    pre_seconds:StrictInt|None=Field(default=None,ge=1,le=86400)
    post_seconds:StrictInt|None=Field(default=None,ge=1,le=86400)
    resolution_seconds:StrictInt|None=Field(default=None,ge=1,le=86400)
    prefix_seconds:StrictInt|None=Field(default=None,ge=1,le=86400)
    prefix_steps:StrictInt|None=Field(default=None,ge=2,le=20)
    top_k:StrictInt=Field(default=20,ge=1,le=100)
    max_distance:StrictStr|None=None

    @model_validator(mode='after')
    def valid(self):
        bins=[number(v,positive=True) for v in self.cap_bins_usd]
        if bins!=sorted(set(bins)):raise ValueError('study_bins_unique_increasing')
        if self.kind in ('transitions','matched_controls','event_study') and not bins:
            raise ValueError('explicit_cap_bins_required')
        if self.kind in ('matched_controls','event_study') and self.time_block_seconds is None:
            raise ValueError('explicit_time_block_required')
        if self.kind in ('matched_controls','event_study'):
            if self.level_usd is None:raise ValueError('explicit_label_level_required')
            number(self.level_usd,positive=True)
        if self.kind=='matched_controls':
            if self.holder_exposure_max is None or number(self.holder_exposure_max)>1:
                raise ValueError('explicit_holder_exposure_fraction_required')
        if self.kind=='event_study':
            if None in (self.pre_seconds,self.post_seconds,self.resolution_seconds):
                raise ValueError('explicit_event_window_required')
            if (self.pre_seconds+self.post_seconds)//self.resolution_seconds+2>50:
                raise ValueError('event_lag_budget')
        if self.kind=='prefix_neighbors':
            if None in (self.prefix_seconds,self.prefix_steps,self.max_distance):
                raise ValueError('explicit_prefix_grid_and_support_required')
            number(self.max_distance)
        return self


def ordered(member):
    # Latest archived interpretations only: absence of an older revision remains unknown.
    by_id={p['capture_id']:p for series in ['entry_series','outcome_series','exit_series']
           for p in member['evidence'][series]['points']}
    return sorted(by_id.values(),key=lambda p:(utc_datetime(p['observed_at']),p['capture_id']))


def state(value,bins):
    return sum(number(value)>=number(b) for b in bins)


def ratio(a,b):
    return None if not b else str(Decimal(a)/b)


def rates(members,level):
    counts=Counter(next(x for x in m['outcome']['labels'] if x['level_usd']==level)['state'] for m in members)
    n=len(members);resolved=counts['reached']+counts['not_reached']
    return {'N':n,**{s:counts[s] for s in ['reached','not_reached','censored','unknown']},
        'resolved_rate':ratio(counts['reached'],resolved),
        'lower_bound':ratio(counts['reached'],n),'upper_bound':ratio(n-counts['not_reached'],n)}


def transitions(members,research,config):
    matrix=Counter();episodes=Counter();origins=Counter();membership=[]
    adjacent_seconds=defaultdict(Decimal);unknown_seconds=Decimal(0)
    for m in members:
        start=utc_datetime(m['entry_at']);end=min(utc_datetime(m['exit_at']),research.knowledge_cutoff)
        points=[p for p in ordered(m) if start<=utc_datetime(p['observed_at'])<=end]
        first=None;destination=None;last=None;unknown=Decimal(0);observed_pairs=0
        for p in points:
            at=utc_datetime(p['observed_at'])
            valid=known(p,research.knowledge_cutoff)
            if first is None and valid:
                first=p
                if (at-start).total_seconds()>research.max_gap_seconds:destination='lost_coverage'
            if last is not None:
                prev_at=utc_datetime(last['observed_at'])
                seconds=Decimal(str((at-prev_at).total_seconds()))
                connected=(known(last,research.knowledge_cutoff) and valid
                           and 0<seconds<=research.max_gap_seconds)
                if connected:
                    a,b=state(last['value_text'],config.cap_bins_usd),state(p['value_text'],config.cap_bins_usd)
                    matrix[(a,b)]+=1;observed_pairs+=1
                    if a==b:adjacent_seconds[a]+=seconds
                    if destination is None and first is not None and b!=state(first['value_text'],config.cap_bins_usd):
                        destination=str(b)
                else:
                    unknown+=max(Decimal(0),seconds)
                    if first is not None and destination is None:destination='lost_coverage'
            last=p
        if first is None:
            origin='unknown';destination='unknown'
        else:
            origin=str(state(first['value_text'],config.cap_bins_usd))
            if destination is None:
                if last is None or not known(last,research.knowledge_cutoff) or (end-utc_datetime(last['observed_at'])).total_seconds()>research.max_gap_seconds:
                    destination='lost_coverage'
                elif research.knowledge_cutoff<utc_datetime(m['exit_at']):destination='censored'
                else:destination='same_at_observed_samples'
        origins[origin]+=1;episodes[(origin,destination)]+=1;unknown_seconds+=unknown
        membership.append({'registry_id':m['registry_id'],'origin':origin,'destination':destination,
            'observed_pairs':observed_pairs,'unassigned_internal_seconds':str(unknown)})
    return {'N':len(members),'bins_usd':config.cap_bins_usd,'state_rule':'number of thresholds at or below observed value',
        'unit':'first observed episode per token; adjacent pair counts reported separately',
        'first_episode':[{'origin':a,'destination':b,'N':n,'origin_N':origins[a],'fraction':ratio(n,origins[a])}
                         for (a,b),n in sorted(episodes.items())],
        'adjacent_pairs':[{'origin':a,'destination':b,'N':n} for (a,b),n in sorted(matrix.items())],
        'same_state_seconds_bounds':{str(s):{'lower':'0','upper':str(v)} for s,v in sorted(adjacent_seconds.items())},
        'unassigned_internal_seconds':str(unknown_seconds),'membership':membership,
        'interpretation':'sampled transitions; time between samples is unobserved; no Markov or continuous-time probability',
        'uncertainty':'descriptive counts and exposure bounds; independent campaign groups unavailable'}


def stratum(member,research,config):
    if member['entry']['state']!='available':return None
    cap=member['entry']['point']['value_text']
    block=int((utc_datetime(member['entry_at'])-research.cohort_start).total_seconds())//config.time_block_seconds
    return block,state(cap,config.cap_bins_usd)


def matched_controls(members,research,config):
    strata=defaultdict(lambda:{'exposed':[],'control':[]});unknown=[];allocation=[]
    for m in members:
        key=stratum(m,research,config);h=m['holder']
        if key is None or h['state']!='available':unknown.append(m['registry_id']);continue
        side='exposed' if number(h['value_text'])<=number(config.holder_exposure_max) else 'control'
        strata[key][side].append(m);allocation.append({'registry_id':m['registry_id'],'side':side,'stratum':list(key)})
    pairs=[];unmatched=[];balance=[];exposed=[];controls=[]
    for key,sides in sorted(strata.items()):
        a=sorted(sides['exposed'],key=lambda m:(m['entry_at'],m['registry_id']))
        b=sorted(sides['control'],key=lambda m:(m['entry_at'],m['registry_id']))
        n=min(len(a),len(b))
        for x,y in zip(a[:n],b[:n]):
            pairs.append({'exposed_id':x['registry_id'],'control_id':y['registry_id'],'weight':'1',
                'cap_difference_usd':str(number(x['entry']['point']['value_text'])-number(y['entry']['point']['value_text'])),
                'anchor_difference_seconds':str((utc_datetime(x['entry_at'])-utc_datetime(y['entry_at'])).total_seconds())})
        exposed.extend(a[:n]);controls.extend(b[:n]);unmatched.extend(m['registry_id'] for m in a[n:]+b[n:])
        balance.append({'stratum':list(key),'exposed':len(a),'control':len(b),'matched_pairs':n})
    er,cr=rates(exposed,config.level_usd),rates(controls,config.level_usd)
    return {'N':len(members),'status':'descriptive_overlap' if pairs else 'not_identifiable',
        'matching':'exact predefined cap/time strata; earliest anchors; one-to-one without reuse; labels never select controls',
        'allocation':allocation,'pairs':pairs,'unknown_ids':unknown,'unmatched_ids':unmatched,'strata':balance,
        'exposed':er,'control':cr,
        'risk_difference_bounds':None if not pairs else [
            str(Decimal(er['lower_bound'])-Decimal(cr['upper_bound'])),
            str(Decimal(er['upper_bound'])-Decimal(cr['lower_bound']))],
        'inference':'conditional descriptive association; residual confounding, campaign clustering and selection unresolved',
        'confidence_interval':None,'confidence_interval_reason':'independent sampling/group and censoring assumptions not established'}


def event_study(members,research,config):
    events=[];no_event=[];rows=[];used_controls=set()
    lags=sorted(set(range(-config.pre_seconds,config.post_seconds+1,config.resolution_seconds))|{0})
    for m in members:
        previous=None;event=None
        for p in ordered(m):
            if not utc_datetime(m['entry_at'])<=utc_datetime(p['observed_at'])<=utc_datetime(m['exit_at']):continue
            if not known(p,research.knowledge_cutoff):previous=None;continue
            if (previous is not None and number(previous['value_text'])<number(config.level_usd)<=number(p['value_text'])
                    and 0<(utc_datetime(p['observed_at'])-utc_datetime(previous['observed_at'])).total_seconds()<=research.max_gap_seconds):
                event=(previous,p);break
            previous=p
        if event is None:no_event.append(m['registry_id']);continue
        lower,upper=event;anchor=max(utc_datetime(upper['observed_at']),utc_datetime(upper['available_at']))
        candidates=[]
        for c in members:
            if c['registry_id']==m['registry_id'] or c['registry_id'] in used_controls:continue
            if stratum(m,research,config) is None or stratum(c,research,config)!=stratum(m,research,config):continue
            if not utc_datetime(c['entry_at'])<=anchor<=utc_datetime(c['exit_at']):continue
            frame=latest_frame({'query_complete':True,'points':ordered(c)},anchor,research.max_gap_seconds)
            if frame['state']=='available' and number(frame['point']['value_text'])<number(config.level_usd):candidates.append(c)
        control=min(candidates,key=lambda c:c['registry_id']) if candidates else None
        if control:used_controls.add(control['registry_id'])
        events.append({'registry_id':m['registry_id'],'control_id':control['registry_id'] if control else None,
            'observed_interval':[lower['observed_at'],upper['observed_at']],'detected_at':anchor,
            'capture_id':upper['capture_id']})
        for lag in lags:
            at=anchor+timedelta(seconds=lag)
            def value(subject):
                if subject is None or at>research.knowledge_cutoff or not utc_datetime(subject['entry_at'])<=at<=utc_datetime(subject['exit_at']):return None
                f=latest_frame({'query_complete':True,'points':ordered(subject)},at,research.max_gap_seconds)
                if f['state']!='available':return None
                return {'value_text':f['point']['value_text'],'capture_id':f['point']['capture_id'],'available_at':f['point']['available_at']}
            rows.append({'registry_id':m['registry_id'],'lag_seconds':lag,'event':value(m),'control':value(control)})
    aggregates=[]
    for lag in lags:
        group=[r for r in rows if r['lag_seconds']==lag];paired=[r for r in group if r['event'] and r['control']]
        def stats(side):
            values=[number(r[side]['value_text']) for r in group if r[side]]
            return {'N':len(values),'mean':ratio(sum(values,Decimal(0)),len(values)),
                    'min':str(min(values)) if values else None,'max':str(max(values)) if values else None}
        aggregates.append({'lag_seconds':lag,'event_total':len(events),'event':stats('event'),'control':stats('control'),
            'paired_N':len(paired),'paired_mean_difference_usd':ratio(sum((number(r['event']['value_text'])-number(r['control']['value_text']) for r in paired),Decimal(0)),len(paired))})
    return {'N':len(members),'event_count':len(events),'no_qualified_event_ids':no_event,
        'events':events,'lag_summary':aggregates,'observations':rows,
        'selection':'retrospective first upward observed crossing; survivor/outcome selection explicit',
        'controls':'contemporaneous known-below-level risk set in predeclared entry strata; no future-outcome filter; no reuse',
        'anchor':'detection availability time; crossing remains interval censored',
        'inference':'descriptive aligned levels/pretrends; missing stays null; not evidence of causation',
        'uncertainty':'min/max and N by lag; no independent-group confidence interval'}


def prefix(member,research,config):
    end=utc_datetime(member['entry_at'])+timedelta(seconds=config.prefix_seconds)
    if end>min(utc_datetime(member['exit_at']),research.knowledge_cutoff):return None
    values=[];refs=[]
    for i in range(config.prefix_steps):
        at=utc_datetime(member['entry_at'])+timedelta(seconds=config.prefix_seconds*i/(config.prefix_steps-1))
        f=latest_frame({'query_complete':True,'points':ordered(member)},at,research.max_gap_seconds)
        if f['state']!='available':return None
        values.append(number(f['point']['value_text'],positive=True));refs.append(f['point']['capture_id'])
    return {'vector':[v/values[0]-1 for v in values],'capture_ids':refs,'end':end}


def prefix_neighbors(train,queries,research,config,train_end):
    index=[];unavailable_train=[]
    for m in train:
        vector=prefix(m,research,config)
        if vector is None or utc_datetime(m['exit_at'])>=train_end:
            unavailable_train.append(m['registry_id']);continue
        index.append((m,vector))
    results=[]
    for m in queries:
        vector=prefix(m,research,config)
        if vector is None or utc_datetime(m['entry_at'])<=train_end:
            results.append({'registry_id':m['registry_id'],'status':'insufficient_prefix','neighbors':[]});continue
        ranked=[]
        for c,cv in index:
            if c['token_address']==m['token_address']:continue
            contributions=[abs(a-b) for a,b in zip(vector['vector'],cv['vector'])]
            distance=sum(contributions,Decimal(0))/len(contributions)
            ranked.append((distance,c['registry_id'],c,cv,contributions))
        ranked.sort(key=lambda r:r[:2])
        chosen=[r for r in ranked if r[0]<=number(config.max_distance)][:config.top_k]
        neighbors=[]
        # Selection has ended: only now attach future labels from each archived training neighbor.
        for distance,key,c,cv,contributions in chosen:
            after=labels(ordered(c),start=cv['end'],end=utc_datetime(c['exit_at']),cutoff=train_end,
                         gap_seconds=research.max_gap_seconds,levels=research.levels_usd)
            neighbors.append({'registry_id':key,'distance':str(distance),'contributions':[str(v) for v in contributions],
                'prefix_capture_ids':cv['capture_ids'],'after_prefix':after})
        results.append({'registry_id':m['registry_id'],'status':'descriptive_neighbors' if chosen else 'outside_support',
            'nearest_distance':str(ranked[0][0]) if ranked else None,'neighbors':neighbors,
            'prefix_capture_ids':vector['capture_ids'],'eligible_training_N':len(ranked)})
    return {'index_sha256':_hash([{'id':m['registry_id'],'vector':[str(x) for x in v['vector']],'refs':v['capture_ids']} for m,v in index]),
        'encoder':'fixed relative-to-first market-cap grid; mean absolute distance; no fitting, no imputation',
        'training_N':len(index),'unavailable_training_ids':unavailable_train,'queries':results,
        'selection_uses_outcomes':False,'self_excluded':True,'correlated_campaign_exclusion':'unavailable: group identities unknown',
        'base_rates':{level:rates([dict(m,outcome=labels(ordered(m),start=v['end'],end=utc_datetime(m['exit_at']),
            cutoff=train_end,gap_seconds=research.max_gap_seconds,levels=research.levels_usd)) for m,v in index],level) for level in research.levels_usd},
        'inference':'descriptive retrieval only; OOD uses explicit distance ceiling; no independent-neighbor probability'}


def run_studies(bundle,research,groups,experiment):
    outputs=[]
    with localcontext() as ctx:
        ctx.prec=80
        for config in experiment.studies:
            if config.level_usd is not None and config.level_usd not in research.levels_usd:
                raise ValueError('study_level_not_in_frozen_labels')
            if config.kind=='prefix_neighbors':
                if config.prefix_seconds>=research.horizon_seconds:raise ValueError('prefix_requires_followup')
                result=prefix_neighbors(groups['train'],groups['validation']+groups['test'],research,config,experiment.train_end)
            else:
                fn={'transitions':transitions,'matched_controls':matched_controls,'event_study':event_study}[config.kind]
                result={name:fn(groups[name],research,config) for name in ['train','validation','test']}
            outputs.append({'study_id':_hash(config.model_dump()),'definition':config.model_dump(mode='json'),'result':result})
    return {'version':STUDY_VERSION,'trials':len(outputs),'outputs':outputs,
        'evidence_policy':'frozen archived interpretations; revisions not present in the archive remain unknown',
        'scope':'bounded descriptive studies; no causal, continuous-market or independent-token generalization'}
