"""Three bounded supervisors. Provider construction alone never makes a request."""
import json
import os
import time
from pathlib import Path
from .contracts import compact, observation
from .recording import digest, encoded
from .operations import Tools

ARCHITECTURES={
 'react':'Gather relevant evidence using tools, commit one skill, and reassess after its outcome.',
 'planner':'Request the mission recommendation. Compare it against up to two short alternative plans with evaluate_plans. Commit the best first action.',
 'contingent':'Identify a decision-changing uncertainty. Compare inspecting it with immediate rescue. Retain conditional next steps, but execute only one first action and branch only on observed outcomes.'}
INSTRUCTION='''Supervise a simulated rescue using supplied evidence only. Never assume unknown occupants absent or infer occupancy from marketed capacity. Use tool results to select actions. Human escort constraints and return margins are mandatory. A tool named dispatch accepts name and arguments_json. Commit using the provided snapshot_id and a unique key. Provide a concise evidence-based rationale, not private reasoning. End after committing one action. Unknown source/measurement uncertainty is not zero. Avoid revisiting unsuccessful viewpoints. Reporting does not imply evacuation.'''
DECLARATION=dict(name='dispatch',description='Execute a rescue tool',parameters=dict(type='object',properties=dict(
 name=dict(type='string',enum=['get_belief','get_evidence','list_actions','evaluate_routes','get_mission_recommendation','evaluate_plans','commit_action','get_action_status','cancel_action']),
 arguments_json=dict(type='string',description='JSON object containing the tool arguments')),required=['name','arguments_json']))
TOOL_HELP={
 'evaluate_routes':{'target':'[column,row]','escort':'optional bool'},
 'get_evidence':{'ids':['observation id']},'evaluate_plans':{'candidates':[['action id','action id']]},
 'commit_action':{'action_id':'id from list_actions','snapshot_id':'current snapshot','key':'unique commitment key','reason':'brief evidence rationale'},
 'cancel_action':{'reason':'explanation'}}

class Supervisor:
 def __init__(self,sim,provider,architecture='planner'):
  if architecture not in ARCHITECTURES:raise ValueError('Unknown architecture')
  self.tools=Tools(sim);sim.agent_tools=self.tools
  self.provider=provider;self.architecture=architecture;self.last_trigger=None;self.calls=0
 def trigger(self):
  s=self.tools.sim
  return digest([s.searched_rooms(),[(d['id'],d['status']) for d in s.detections],len(self.tools.history),int(s.time//30)])
 def step(self):
  s=self.tools.sim
  if s.status!='running' or s.withdraw_reason or self.tools.active:return
  trigger=self.trigger()
  if trigger==self.last_trigger:return
  self.last_trigger=trigger
  actions=self.tools.list_actions()
  # Do not send grids or frame bytes. Full evidence is available through scoped tools.
  from .agent import CONSTITUTION
  policy=CONSTITUTION.read_text()
  messages=[dict(role='user',parts=[dict(text=json.dumps(dict(instruction=INSTRUCTION+policy,architecture=ARCHITECTURES[self.architecture],
       belief=compact(s),actions=actions,tool_arguments=TOOL_HELP,memory=self.tools.history[-6:])))])]
  used=set();executions=0;started=time.monotonic()
  try:
   for _ in range(3):
    if self.calls>=24:raise RuntimeError('Mission request cap reached')
    self.calls+=1;response=self.provider(messages)
    if getattr(s,'recorder',None):s.recorder.event('model',dict(architecture=self.architecture,request=messages,response=response),s.time)
    messages.append(dict(role='model',parts=response))
    results=[]
    for part in response:
     call=part.get('function_call') or part.get('functionCall')
     if not call:continue
     executions+=1
     if executions>8:raise RuntimeError('Tool execution cap reached')
     try:
      args=call['args'];name=args['name'];arguments=json.loads(args['arguments_json'])
      if name=='commit_action' and self.architecture!='react':
       required={'evaluate_plans'} | ({'get_mission_recommendation'} if self.architecture=='planner' else set())
       if not required<=used:raise ValueError('Obtain planner/evaluation results in an earlier model round before committing')
      result=self.tools.dispatch(name,arguments)
     except (ValueError,KeyError,TypeError) as error:result={'error':str(error)}
     results.append(dict(function_response=dict(name='dispatch',response={'result':result})))
     if self.tools.active or len(self.tools.history) and self.tools.history[-1]['started_at']==s.time:return
    if not results:break
    for part in response:
     call=part.get('function_call') or part.get('functionCall')
     if call:used.add(call['args']['name'])
    messages.append(dict(role='user',parts=results))
   raise RuntimeError('No action committed within reasoning budget')
  except Exception as error:
   if getattr(s,'recorder',None):s.recorder.event('model-error',dict(request=messages,error_type=type(error).__name__),s.time)
   self.tools.finish('failed',str(error))
   s.metrics['agent_fallbacks']=s.metrics.get('agent_fallbacks',0)+1
   s.log('Agent fallback: '+type(error).__name__)
  finally:s.metrics['reasoning_wall_s']=s.metrics.get('reasoning_wall_s',0)+time.monotonic()-started

class GeminiProvider:
 """One in-flight request, durable cache and ledger. Approval is external to this module."""
 def __init__(self, campaign_file):
  self.path=Path(campaign_file);self.config=json.loads(self.path.read_text())
  if self.config.get('approval')!='approve':raise PermissionError('Explicit campaign approval required')
  if self.config.get('model')!='gemini-3.5-flash-lite':raise ValueError('Pin the reviewed model')
  for k in ('rpm','tpm','rpd'):
   if not isinstance(self.config.get(k),int) or self.config[k]<=0:raise ValueError('Confirmed project quota required: '+k)
  self.cache=self.path.parent/'responses';self.cache.mkdir(exist_ok=True)
  self.ledger_path=self.path.parent/'ledger.json'
  self.ledger=json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else dict(requests=[],reserved_usd=0.)
  self.started=time.monotonic()
 def __call__(self,messages):
  from .agent import load_agent_env
  from google import genai
  from google.genai import types
  key=digest(dict(model=self.config['model'],messages=messages,tools=DECLARATION,thinking='low',output=1024))
  cache=self.cache/(key+'.json')
  if cache.exists():return json.loads(cache.read_text())
  raw=encoded(messages)
  # Bytes are a conservative token reservation; include tools and protocol allowance.
  tokens=len(raw)+len(encoded(DECLARATION))+512
  if tokens>32000:raise ValueError('Request too large; scope evidence first')
  reserve=(tokens*.30+1024*2.50)/1e6
  now=time.time();entries=self.ledger['requests']
  if len(entries)>=144 or len([e for e in entries if now-e['at']<86400])>=self.config['rpd']:raise RuntimeError('Campaign/daily quota exhausted')
  if self.ledger['reserved_usd']+reserve>self.config.get('budget_usd',2.):raise RuntimeError('Conservative campaign cost ceiling reached')
  if tokens>self.config['tpm']:raise RuntimeError('Request exceeds project TPM; scope evidence first')
  while True:
   now=time.time();minute=[e for e in entries if now-e['at']<60]
   if len(minute)<self.config['rpm'] and sum(e['tokens'] for e in minute)+tokens<=self.config['tpm']:break
   delay=max(.01,60-(now-minute[0]['at'])) if minute else 60
   if time.monotonic()-self.started+delay>=1800:raise RuntimeError('Campaign deadline')
   time.sleep(delay)
  remaining=1800-(time.monotonic()-self.started)
  if remaining<10:raise RuntimeError('Campaign deadline')
  entry=dict(at=time.time(),tokens=tokens,key=key,status='pending');entries.append(entry)
  self.ledger['reserved_usd']+=reserve
  self.ledger_path.write_bytes(encoded(self.ledger))
  # A pending/failed identical request is never repeated automatically.
  if any(e['key']==key for e in entries[:-1]):raise RuntimeError('Uncertain previous request; manual review required')
  load_agent_env()
  client=genai.Client(api_key=os.environ['GEMINI_API_KEY'],http_options=types.HttpOptions(timeout=int(min(60,remaining)*1000),retry_options=types.HttpRetryOptions(attempts=1)))
  try:
   response=client.models.generate_content(model=self.config['model'],contents=messages,config=dict(
    tools=[{'function_declarations':[DECLARATION]}],max_output_tokens=1024,thinking_config={'thinking_level':'low'}))
   parts=[p.model_dump(mode='json',exclude_none=True) for p in response.candidates[0].content.parts]
   cache.write_bytes(encoded(parts));entry.update(status='complete',usage=response.usage_metadata.model_dump(mode='json'))
   return parts
  finally:self.ledger_path.write_bytes(encoded(self.ledger))
