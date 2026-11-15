"""Optional hosted mission decisions. Inputs are observation snapshots only."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSTITUTION = ROOT / 'docs' / 'ROBOT_CONSTITUTION.md'
DEFAULT_MODEL = 'gemini-3.8-flash'
# Fast models that answered within 2 s on 2026-11-14 while the 3.x Flash models returned 503 (high demand).
DEFAULT_FALLBACKS = 'gemini-3.5-flash-lite,gemini-3.1-flash-lite'
AGENT_ENV = ('GEMINI_API_KEY', 'GEMINI_MODEL', 'GEMINI_FALLBACK_MODELS', 'GEMINI_MODEL_BUDGET_S')


def load_agent_env(path=ROOT / '.env'):
    """Read only the Gemini settings from .env into the server process (never logged, never sent to the browser).
    Variables already set in the environment win."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.partition('=')
        key, value = key.strip().removeprefix('export ').strip(), value.strip().strip('"').strip("'")
        if sep and key in AGENT_ENV and value and not os.environ.get(key):
            os.environ[key] = value


def scrub(message):
    """Error text for logs: short, and with the API key removed if a library ever echoes it."""
    text = str(message)
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        text = text.replace(key, '<redacted>')
    return text[:200]
BASE_INSTRUCTION = ('You supervise a simulated rescue. Choose a select_goal tool call using only supplied evidence. '
                    'Prioritize confirmed survivors with viable evacuation routes; inspect unsearched rooms. '
                    'Your goal is kept until the robot reaches it, so pick one target and commit; do not alternate. '
                    'Rooms list travel_cost_m (risk-weighted, from the robot) and likelihood_weight (building-use prior): '
                    'prefer high likelihood per metre, skip rooms already inspected or with no remaining observation points. '
                    'Each room also has risk_tag/risk_factor from the room fire-risk map (pre-incident fixture tags, not sensor data): '
                    'search gas-heater rooms earlier, sprinklered rooms slightly later; multiply likelihood_weight by risk_factor. '
                    'A risk_tag of unknown means not recorded, not absent. '
                    'Unknown occupants are not absent. Never infer actual occupancy from capacity. Explain briefly.')


def system_instruction():
    """Tool-use rules plus the robot decision constitution; the controller still validates every choice."""
    try:
        policy = CONSTITUTION.read_text(encoding='utf-8')
    except OSError:
        return BASE_INSTRUCTION
    return BASE_INSTRUCTION + '\n\nFollow this decision constitution. The controller enforces limits; never request an exception.\n\n' + policy

TOOLS=[{'name':'select_goal','description':'Select a room inspection, confirmed survivor, or known exit. Controller validates and executes.',
        'parameters':{'type':'object','properties':{'kind':{'type':'string','enum':['room','survivor','exit']},
        'id':{'type':'string'},'reason':{'type':'string'}},'required':['kind','id','reason']}}]

def summarize(observation):
    """Compact belief for the prompt: full grids are large on scanned scenes, so send observed hazards only."""
    heat,smoke,observed=observation['heat'],observation['smoke'],observation['observed']
    hazards=[[x,y,heat[y][x],smoke[y][x]] for y in range(len(heat)) for x in range(len(heat[0])) if heat[y][x]>.05 or smoke[y][x]>.05]
    cells=sum(len(row) for row in observed)
    summary={k:v for k,v in observation.items() if k not in ('observed','heat','smoke')}
    summary.update(observed_fraction=round(sum(map(sum,observed))/max(cells,1),3),observed_hazard_cells_xy_heat_smoke=hazards[:200])
    return summary


class MissionAgent:
    """Gemini supervisor. Tries the configured model first, then the fallbacks when a model is overloaded (503),
    out of quota (429) or slow. The model that answered last is tried first next time."""
    def __init__(self):
        raise PermissionError("Legacy hosted selector disabled; use an explicitly approved campaign")
        from google import genai
        from google.genai import types
        from concurrent.futures import ThreadPoolExecutor
        # Gemini rejects server deadlines under 10 s, so the per-model budget is enforced here instead.
        self.client=genai.Client(api_key=os.environ['GEMINI_API_KEY'],
                                 http_options=types.HttpOptions(timeout=10_000,retry_options=types.HttpRetryOptions(attempts=1)))
        self.budget_s=float(os.environ.get('GEMINI_MODEL_BUDGET_S','3'))
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='gemini')
        primary=os.environ.get('GEMINI_MODEL') or DEFAULT_MODEL
        fallbacks=[m.strip() for m in os.environ.get('GEMINI_FALLBACK_MODELS',DEFAULT_FALLBACKS).split(',') if m.strip()]
        self.models=list(dict.fromkeys([primary,*fallbacks]))
        self.model=primary

    def decide(self,observation,scene,options=None):
        contents=json.dumps({'observation':summarize(observation),
                             'rooms':(options or {}).get('rooms') or [{'id':r['id'],'name':r['name'],'observe':r.get('observe',r['cells'][:1])} for r in scene.rooms],
                             'confirmed_survivors':(options or {}).get('survivors',[]),
                             'exits':scene.exits})
        config={'system_instruction':system_instruction(),'tools':[{'function_declarations':TOOLS}]}
        errors=[]
        for model in [self.model]+[m for m in self.models if m!=self.model]:
            from concurrent.futures import TimeoutError as Slow
            try:
                response=self.pool.submit(self.client.models.generate_content,model=model,contents=contents,config=config).result(timeout=self.budget_s)
            except Slow:
                errors.append(f'{model}: no answer within {self.budget_s:g} s');continue
            except Exception as e:   # overloaded, quota or network: try the next model
                errors.append(f'{model}: {type(e).__name__} {getattr(e,"code","")}'.strip());continue
            self.model=model
            for call in response.function_calls or []:
                if call.name=='select_goal':return dict(call.args)
            raise ValueError(f'{model} returned no select_goal action')
        raise RuntimeError('All Gemini models failed: '+'; '.join(errors))


def decision_options(sim):
    """Planner-side context for the model, derived from the robot's belief and static map only: room state,
    observed share, travel distance over the current risk-aware cost map, and the building-use likelihood."""
    costs,_=sim.tree()
    rooms=[]
    for room in sim.scene.rooms:
        st=sim.rooms_state[room['id']];points=[p for p in sim.observe_points(room) if p not in sim.searched_points and p!=sim.robot]
        reach=[costs[p] for p in points if p in costs]
        rooms.append(dict(id=room['id'],name=room['name'],label=room.get('label'),state=st['status'],observed_fraction=st['observed_fraction'],
                          remaining_observation_points=len(points),travel_cost_m=round(min(reach)*sim.scene.resolution,1) if reach else None,
                          likelihood_weight=sim.prior['weights'].get(room['id'],1.),
                          risk_tag=sim.risk[room['id']]['tag'],risk_factor=sim.risk[room['id']]['factor'],
                          risk_source=sim.risk[room['id']]['source']))
    survivors=[dict(id=d['id'],travel_cost_m=round(costs[tuple(d['cell'])]*sim.scene.resolution,1) if tuple(d['cell']) in costs else None)
               for d in sim.detections if d['status']=='confirmed']
    return dict(rooms=rooms,survivors=survivors,building_use=sim.prior['use'])


BACKOFF_FAILED_S, BACKOFF_REJECTED_S = 5.0, 1.0   # mission seconds the built-in planner steers after a bad call


def wants_decision(sim):
    """The robot is waiting on the model: running, no committed goal, not escorting or withdrawing, not backing off."""
    return (sim.status=='running' and needs_decision(sim) and not sim.escort and not sim.withdraw_reason
            and sim.time>=sim.agent_backoff_until)


def needs_decision(sim):
    """Ask the model only when the robot has no committed goal (none set, reached, or invalidated)."""
    return sim.goal is None or sim.robot==sim.goal


def apply_decision(sim,decision,observation_time):
    """Reject stale decisions, hidden identities, and unsupported destinations."""
    if sim.status!='running' or sim.time-observation_time>10:raise ValueError('Stale mission decision')
    if sim.escort:raise ValueError('Active escort owns the navigation goal')
    if sim.withdraw_reason:raise ValueError('Robot is withdrawing; no new goals')
    kind,identity=decision.get('kind'),decision.get('id')
    if kind=='room':
        room=next((r for r in sim.scene.rooms if r['id']==identity),None)
        if room is None:raise ValueError('Unknown room')
        # Skip points already searched (room-search memory) and the robot's own cell (no paused-clock deadlock).
        points=[p for p in sim.observe_points(room) if p not in sim.searched_points and p!=sim.robot]
        goal=next((p for p in points if sim.path(p)),None)
        if goal is None:raise ValueError('Room has no remaining observation point that is reachable (searched, unreachable, or the robot is already there)')
    elif kind=='survivor':
        # Only perception-assigned detection IDs are valid; simulator person IDs never are.
        d=next((d for d in sim.detections if d['id']==identity and d['status']=='confirmed'),None)
        if d is None:raise ValueError('Survivor not confirmed')
        goal=tuple(d['cell'])
    elif kind=='exit':
        if identity not in [str(i) for i in range(len(sim.scene.exits))]:raise ValueError('Unknown exit')
        goal=sim.scene.exits[int(identity)]
    else:raise ValueError('Unknown tool argument')
    if goal==sim.robot:raise ValueError('Robot is already at that goal')
    if not sim.path(goal):raise ValueError('No viable route')
    # Guarantee at least one mission tick before the model is asked again (no hold can loop without progress).
    sim.agent_backoff_until=max(sim.agent_backoff_until,sim.time+1e-6)
    sim.goal=goal;sim.log(f"Hosted agent: {kind} {identity}: {str(decision.get('reason',''))[:300]}")
