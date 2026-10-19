"""Explicit synthetic capacity/replay harness; does not access any service."""
import argparse,json,time,resource,statistics,hashlib
from pathlib import Path
from midas_data.cohorts import labels,summarize
from midas_data.experiments import Strategy,compare
from midas_data.research_contracts import ResearchSpec
from midas_data.studies import StudySpec
from midas_data.features import _json,_hash
from test_experiments import member,bundle,definitions

def dataset(count):
    r,_=definitions();r=r.model_copy(update={'max_members':100})
    members=[]
    for i in range(count):
        m=member(i+1,4200+(i%3)*7200+(i//3)*3,exit_price='.5' if i%2 else '2')
        m['evidence']['outcome_series']['points'][1]['value_text']='300'
        m['outcome']=labels(m['evidence']['outcome_series']['points'],start=m['entry_at'],end=m['exit_at'],
            cutoff=r.knowledge_cutoff,gap_seconds=r.max_gap_seconds,levels=r.levels_usd)
        members.append(m)
    result=bundle(members);result['definition']=r.definition()
    result['job_id']=_hash({'definition':result['definition'],'implementation':result['implementation']})
    return result

def configuration(count):
    studies=[
        StudySpec(name='transition-fixture',kind='transitions',cap_bins_usd=['100','200','500']),
        StudySpec(name='control-fixture',kind='matched_controls',cap_bins_usd=['200'],time_block_seconds=3600,
            holder_exposure_max='.3',level_usd='200'),
        StudySpec(name='event-fixture',kind='event_study',cap_bins_usd=['200'],time_block_seconds=3600,
            level_usd='200',pre_seconds=60,post_seconds=60,resolution_seconds=60),
        StudySpec(name='prefix-fixture',kind='prefix_neighbors',prefix_seconds=60,prefix_steps=2,top_k=20,max_distance='.1')]
    _,spec=definitions(strategies=[Strategy(name='fixture-'+str(i),market_cap_min=str(i)) for i in range(count)],
        studies=studies,embargo_seconds=3600)
    return spec

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    target=Path(args.output);target.mkdir(parents=True,exist_ok=True);rows=[]
    for members in [1,20,100]:
        b=dataset(members)
        for strategies in [1,20]:
            spec=configuration(strategies);times=[];digests=[]
            for _ in range(3):
                begin=time.perf_counter();result=compare(b,spec);body=_json(result)
                times.append((time.perf_counter()-begin)*1000);digests.append(hashlib.sha256(body).hexdigest())
            assert len(set(digests))==1
            rows.append({'members':members,'strategies':strategies,'studies':len(spec.studies),
                'samples':len(times),'min_ms':round(min(times),2),'median_ms':round(statistics.median(times),2),
                'max_ms':round(max(times),2),'dataset_bytes':len(_json(b)),'result_bytes':len(body),'same_hash':True})
    for name,value in [('bundle',b),('spec',spec.model_dump(mode='json')),('expected-result',result)]:
        (target/(name+'.json')).write_bytes(_json(value))
    report={'scope':'synthetic fixed five-point token histories, four studies; no GMGN calls, no production throughput claim',
        'rows':rows,'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'container_limit_bytes':536870912,'cpu_limit_cores':0.5}
    (target/'research-growth.json').write_bytes(_json(report));print(json.dumps(report))

if __name__=='__main__':main()
