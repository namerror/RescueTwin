"""Offline campaign proposal. Does not instantiate a Gemini client or grant approval."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from rescuetwin.scenes import available
from rescuetwin.core import Simulation
from rescuetwin.reasoning import Supervisor, DECLARATION
from rescuetwin.recording import encoded
from scripts.experiment_campaign import cases

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--models',nargs='+',required=True);args=parser.parse_args()
 registry=available();rows=[]
 for model in args.models:
  scene=registry[model+'-floor1'];case=next(c for c in cases(scene) if c['name']=='pickup')
  sim=Simulation(scene,case['scenario']);sim.status='running';sim.sense()
  captured=[]
  Supervisor(sim,lambda messages:captured.append(messages) or []).step()
  size=len(encoded(captured[0]))+len(encoded(DECLARATION))
  rows.append(dict(scene_id=scene.id,scenario=case['scenario'],initial_request_bytes=size,estimated_input_tokens=size/4))
 maximum_requests=144;mean_input=sum(r['estimated_input_tokens'] for r in rows)/len(rows)
 proposal=dict(approval='pending',model='gemini-3.5-flash-lite',thinking='low',output_token_limit=1024,
  rpm=None,tpm=None,rpd=None,quota_source='Supply current account quotas before explicit campaign approval',wall_clock_limit_s=1800,
  maximum_requests=maximum_requests,requests_per_mission=24,architectures=['react','planner','contingent'],scenarios=rows,
  estimated_paid_usd=maximum_requests*(mean_input*.30+1024*2.50)/1e6,
  conservative_paid_usd_ceiling=maximum_requests*(32000*.30+1024*2.50)/1e6,
  free_tier_usd=0,recommended_paid_cap_if_needed_usd=2,
  caveat='Byte/4 token estimate is heuristic; use quota/cost reservations and actual usage. Rendering validation must pass before a scored campaign.')
 path=Path('data/experiments/campaign-proposal.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(proposal,indent=2))
 print(json.dumps({k:v for k,v in proposal.items() if k!='scenarios'},indent=2))
if __name__=='__main__':main()
