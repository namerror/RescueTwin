import json
import pytest
from rescuetwin.core import Simulation
from rescuetwin import recording
from rescuetwin.contracts import observation
from rescuetwin.operations import Tools
from rescuetwin.reasoning import Supervisor, GeminiProvider

def test_recording_roundtrip_and_boundary(tmp_path,monkeypatch):
 monkeypatch.setattr(recording,'ROOT',tmp_path)
 a=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1))
 b=Simulation(scenario=dict(survivors=[[5,18]],ignitions=[],seed=1))
 assert observation(a)==observation(b)
 a.status='running';r=recording.Recorder(a);a.tick();r.capture(a)
 rows=recording.timeline(r.id)
 assert len(rows)==2 and rows[-1]['data']['state']['time']==a.time
 assert 'scenario' not in rows[-1]['data']['observation']
 assert recording.artifact(r.id,'scene.json').exists()
 assert recording.archive(r.id).exists()
 r.close();assert not recording.manifest(r.id)['complete']
 with pytest.raises(ValueError):recording.directory('../no')

def test_tools_and_scripted_architectures():
 for architecture in ('react','planner','contingent'):
  s=Simulation();s.status='running'
  def scripted(messages):
   first=json.loads(messages[0]['parts'][0]['text'])
   action=next(a for a in first['actions'] if a['type']=='inspect_room')
   if architecture!='react' and len(messages)==1:
    tools=[('evaluate_plans',{'candidates':[[action['id']]]})]
    if architecture=='planner':tools.insert(0,('get_mission_recommendation',{}))
    return [dict(function_call=dict(name='dispatch',args=dict(name=n,arguments_json=json.dumps(a)))) for n,a in tools]
   return [dict(function_call=dict(name='dispatch',args=dict(name='commit_action',arguments_json=json.dumps(dict(action_id=action['id'],snapshot_id=first['belief']['snapshot_id'],key='test')))))]
  supervisor=Supervisor(s,scripted,architecture);before=s.time;supervisor.step()
  assert s.time==before and supervisor.tools.active
  for _ in range(15):s.tick()
  assert s.metrics['distance']>0
  view=supervisor.tools.view()
  assert not hasattr(view,'survivors') and not hasattr(view,'heat') and not hasattr(view,'scenario')

def test_stale_commit_and_gate(tmp_path):
 s=Simulation();s.status='running';t=Tools(s);t.list_actions()
 with pytest.raises(ValueError,match='Stale'):t.commit('0','bad','x')
 p=tmp_path/'campaign.json';p.write_text('{}')
 with pytest.raises(PermissionError):GeminiProvider(p)
 from rescuetwin.agent import MissionAgent
 with pytest.raises(PermissionError):MissionAgent()

def test_modalities_and_unknown_heat():
 s=Simulation(scenario=dict(survivors=[],ignitions=[],seed=1));s.sense()
 o=observation(s)
 heat=[v for v in o['samples'] if v['modality']=='heat']
 smoke=[v for v in o['samples'] if v['modality']=='smoke']
 assert len(heat)==1 and len(smoke)>1
 assert all(v['measurement_uncertainty'] is None for v in heat+smoke)

def test_tools_stay_blind_to_remote_hazards():
 a=Simulation();b=Simulation();b.heat[20,25]=1;b.smoke[20,25]=1
 assert observation(a)==observation(b)
 assert Tools(a).recommendation()==Tools(b).recommendation()

def test_timeline_snapshot_and_idempotency(tmp_path,monkeypatch):
 monkeypatch.setattr(recording,'ROOT',tmp_path)
 s=Simulation();s.status='running';r=recording.Recorder(s)
 r.event('tool',{'result':'recorded'},s.time)
 snap=recording.snapshot(r.id,1)
 assert snap['snapshot']['seq']==0
 t=Tools(s);actions=t.list_actions();a=actions[0]
 first=t.commit(a['id'],observation(s)['snapshot_id'],'once')
 assert t.commit(a['id'],'stale','once') is first
 t.finish('interrupted','test')
 assert t.history[-1]['status']=='interrupted'

def test_reasoning_budget_falls_back_without_advancing_time():
 s=Simulation();s.status='running';before=s.time
 def unhelpful(messages):
  return [dict(function_call=dict(name='dispatch',args=dict(name='get_belief',arguments_json='{}')))]
 supervisor=Supervisor(s,unhelpful)
 supervisor.step()
 assert supervisor.calls==3 and s.time==before and s.metrics['agent_fallbacks']==1

def test_provider_cap_blocks_before_client(tmp_path,monkeypatch):
 genai=pytest.importorskip('google.genai')
 monkeypatch.setattr(genai,'Client',lambda **kwargs:pytest.fail('Must not construct an inference client'))
 p=tmp_path/'campaign.json';p.write_text(json.dumps(dict(approval='approve',model='gemini-3.5-flash-lite',rpm=15,tpm=250000,rpd=500)))
 provider=GeminiProvider(p)
 provider.ledger['requests']=[dict(at=0,tokens=0,key=str(i)) for i in range(144)]
 with pytest.raises(RuntimeError,match='quota exhausted'):provider([])
