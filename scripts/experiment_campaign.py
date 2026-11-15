"""Bounded recorded missions through the real browser renderer. No Gemini unless an approved backend campaign is configured."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from rescuetwin.scenes import available
from rescuetwin.recording import digest
from rescuetwin.core import line_visible
from playwright.sync_api import sync_playwright

def cases(scene):
    start=scene.robot_start
    rooms=[r for r in scene.rooms if r['cells']]
    people=[]
    for room in sorted(rooms,key=lambda r:r['id']):
        p=next((p for p in room['observe'] if p!=start and p not in scene.exits),None)
        if p and list(p) not in people:people.append(list(p))
        if len(people)==2:break
    base=dict(scene_id=scene.id,survivors=people,ignitions=[],seed=71)
    fire=next((p for r in reversed(rooms) for p in r['observe'] if list(p) not in people and p!=start and p not in scene.exits),None)
    from scripts.verify_catalog_simulations import target
    first=target(scene)
    nearby={**base,'survivors':[first]}
    import math
    candidates=[tuple(c) for room in rooms for c in room['cells'] if tuple(c)!=tuple(first) and tuple(c)!=start and tuple(c) not in scene.exits
      and 1.2<=math.dist(scene.world(tuple(c)),scene.world(tuple(first)))<=2.5
      and 1.5<=math.dist(scene.world(tuple(c)),scene.world(start))<=3.5
      and abs(math.atan2(c[1]-start[1],c[0]-start[0]))<.65
      and line_visible(scene,start,tuple(c))]
    second=min(candidates,key=lambda c:abs(math.dist(scene.world(c),scene.world(tuple(first)))-1.5)) if candidates else tuple(first)
    pickup={**base,'survivors':[first,list(second)] if second!=tuple(first) else [first]}
    return [dict(name='pickup',scenario=pickup),dict(name='nearby',scenario=nearby),dict(name='distributed',scenario=base),dict(name='hazard',scenario={**base,'ignitions':[list(fire)] if fire else []})]

def main():
 p=argparse.ArgumentParser();p.add_argument('--scenario-file');p.add_argument('--url',default='http://127.0.0.1:5197');p.add_argument('--models',nargs='+',required=True);p.add_argument('--perception',default='image-yolo',choices=['image-yolo','analytic-development']);p.add_argument('--cases',nargs='+',default=['distributed','hazard']);p.add_argument('--policies',nargs='+',default=['distance','greedy','mission-astar']);p.add_argument('--approve',action='store_true');p.add_argument('--timeout',type=float,default=90);p.add_argument('--output',default='data/experiments/deterministic.json');args=p.parse_args()
 if any(s.startswith('agent-') for s in args.policies) and not args.approve:p.error('Gemini campaign requires explicit approval, plus approved backend configuration')
 out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
 rows=json.loads(out.read_text()) if out.exists() else []
 custom=json.loads(Path(args.scenario_file).read_text()) if args.scenario_file else None
 def selected_cases(scene):
  return [dict(name=Path(args.scenario_file).stem,scenario=custom)] if custom and custom['scene_id']==scene.id else ([] if custom else cases(scene))
 registry=available();started=time.monotonic()
 ledger_path=out.parent/'ledger.json'
 if any(policy.startswith('agent-') for policy in args.policies) and ledger_path.exists():
  ledger=json.loads(ledger_path.read_text())
  first=ledger.get('campaign_started_at') or min((r['at'] for r in ledger['requests']),default=time.time())
  started-=max(0,time.time()-first)
 if args.perception=='analytic-development':
  from rescuetwin.core import Simulation
  from rescuetwin.recording import Recorder
  for model in args.models:
   if model!=args.models[0] and any(policy.startswith('agent-') for policy in args.policies) and ledger_path.exists() and 144-len(json.loads(ledger_path.read_text())['requests'])<72:
    print('Skipping second scene: insufficient request allocation for a complete matched block',flush=True);break
   if model!=args.models[0] and any(policy.startswith('agent-') for policy in args.policies) and time.monotonic()-started>900:
    print('Skipping second scene: insufficient time for a matched three-architecture block',flush=True);break
   scene=registry[model+'-floor1']
   for case in selected_cases(scene):
    if not custom and case['name'] not in args.cases:continue
    for policy in args.policies:
     if policy.startswith('agent-'):raise ValueError('Use YOLO for scored Gemini runs')
     identity=digest([scene.id,case,policy,args.perception])
     if any(r['identity']==identity for r in rows):continue
     sim=Simulation(scene,case['scenario'],'baseline' if policy=='distance' else 'adaptive',args.perception,'mission-astar' if policy=='mission-astar' else 'greedy');sim.status='running'
     recorder=Recorder(sim,dict(policy=policy,latency_excluded=True));sim.recorder=recorder;deadline=time.monotonic()+args.timeout
     while sim.status=='running' and time.monotonic()<deadline:sim.tick();recorder.capture(sim)
     recorder.close()
     row=dict(identity=identity,scene=scene.id,case=case['name'],policy=policy,perception=args.perception,run_id=recorder.id,status=sim.status,time=sim.time,metrics=sim.state()['metrics'],degraded=False,page_errors=[],scenario=case['scenario'])
     rows.append(row);out.write_text(json.dumps(rows,indent=2));print(json.dumps({k:row[k] for k in ('scene','case','policy','status','time','degraded')}),flush=True)
  return
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--enable-gpu']);page=browser.new_page(viewport={'width':1200,'height':900})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(args.url)
  for model in args.models:
   if model!=args.models[0] and any(policy.startswith('agent-') for policy in args.policies) and ledger_path.exists() and 144-len(json.loads(ledger_path.read_text())['requests'])<72:
    print('Skipping second scene: insufficient request allocation for a complete matched block',flush=True);break
   if model!=args.models[0] and any(policy.startswith('agent-') for policy in args.policies) and time.monotonic()-started>900:
    print('Skipping second scene: insufficient time for a matched three-architecture block',flush=True);break
   scene=registry[model+'-floor1']
   for case in selected_cases(scene):
    if not custom and case['name'] not in args.cases:continue
    for policy in args.policies:
     identity=digest([scene.id,case,policy,args.perception])
     if any(r['identity']==identity for r in rows):continue
     if time.monotonic()-started>1800:break
     page.request.post(args.url+'/api/control',data={'action':'reset'})
     page.request.post(args.url+'/api/scene',data={'id':scene.id})
     page.wait_for_function("id => window.rescuetwin?.scene()?.id === id && !document.querySelector('#start').disabled",arg=scene.id,timeout=90000)
     result=page.request.post(args.url+'/api/experiments/run',data=dict(scene_id=scene.id,scenario=case['scenario'],policy=policy,perception=args.perception))
     if not result.ok:raise RuntimeError(result.text())
     run_id=result.json()['run_id'];deadline=min(time.monotonic()+args.timeout,started+1800);state={}
     while time.monotonic()<deadline:
      state=page.request.get(args.url+'/api/state').json()
      if state['status'] not in ('running','paused'):break
      page.wait_for_timeout(250)
     row=dict(identity=identity,scene=scene.id,case=case['name'],policy=policy,perception=args.perception,run_id=run_id,
       status=state.get('status') if state.get('status') not in ('running','paused') else 'wall_timeout',time=state.get('time'),metrics=state.get('metrics'),degraded=state.get('degraded'),page_errors=list(errors),scenario=case['scenario'])
     rows.append(row);out.write_text(json.dumps(rows,indent=2));print(json.dumps({k:row[k] for k in ('scene','case','policy','status','time','degraded')}),flush=True)
     try:page.screenshot(path=str(out.parent/(run_id+'.png')),timeout=5000)
     except Exception:pass
     page.request.post(args.url+'/api/control',data={'action':'reset'})
  browser.close()
if __name__=='__main__':main()
