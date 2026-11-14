"""Reproducible offline capacity experiments; analytic development perception only."""
import argparse
import hashlib
import json
from pathlib import Path
from rescuetwin.scenes import load_bundle
from rescuetwin.presets import available
from rescuetwin.core import compare


def run(bundle, seed=7):
    scene=load_bundle(bundle);results=[]
    for preset in available(scene,seed):
        if not preset['id'].startswith('capacity-') or preset.get('unavailable'): continue
        scenario=preset['scenario']
        frozen=json.dumps(scenario,sort_keys=True,separators=(',',':'))
        result=compare(scene,scenario)
        for metrics in result['reports'].values():
            metrics['unresolved']=preset['placed_count']-metrics['evacuated']
        results.append(dict(preset=preset,scenario_sha256=hashlib.sha256(frozen.encode()).hexdigest(),comparison=result))
    return dict(scene_id=scene.id,seed=seed,perception='analytic-development',costar=scene.meta.get('costar'),
        note='Hypothetical capacity load; not actual occupancy. Paired policies share frozen inputs and immediate safety rules. Exposure is normalized simulation output, not real-world safety validation.',results=results)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('bundle',type=Path);p.add_argument('--seed',type=int,default=7);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=run(a.bundle,a.seed);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({'scene':report['scene_id'],'experiments':len(report['results']),'output':str(a.output),'perception':report['perception']}))
