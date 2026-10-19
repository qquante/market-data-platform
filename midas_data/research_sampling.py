"""Outcome-blind bounded sampling; archived population makes selection replayable."""
import hashlib
from .features import _hash
MAX_FRAME=20_000
METHOD='sha256_registry_seed_v1'

def selected_ids(ids,seed,count):
    return sorted(sorted(ids,key=lambda key:(hashlib.sha256(f'{seed}:{key}'.encode()).hexdigest(),key))[:count])

def sample(rows,seed,count):
    ids=sorted(r['registry_id'] for r in rows)
    if len(ids)>MAX_FRAME or len(set(ids))!=len(ids):raise ValueError('sampling_frame_budget_or_duplicate')
    chosen=selected_ids(ids,seed,count)
    frame={'method':METHOD,'seed':seed,'registry_ids':ids,'population':len(ids),
           'selected':len(chosen),'inclusion_probability':f'{len(chosen)}/{len(ids)}' if ids else None}
    frame['sha256']=_hash(frame)
    wanted=set(chosen)
    return sorted([r for r in rows if r['registry_id'] in wanted],key=lambda r:r['registry_id']),frame

def verify(frame,members,seed,count):
    if not isinstance(frame,dict):raise ValueError('sampling_frame_missing')
    ids=frame.get('registry_ids')
    if not isinstance(ids,list) or any(type(x) is not int or x<1 for x in ids):raise ValueError('sampling_frame_ids')
    if ids!=sorted(set(ids)):raise ValueError('sampling_frame_order')
    selected,expected=sample([{'registry_id':i} for i in ids],seed,count)
    if frame!=expected or [r['registry_id'] for r in selected]!=sorted(m['registry_id'] for m in members):
        raise ValueError('sampling_membership_changed')
