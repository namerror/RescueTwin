"""Optional hosted mission decisions. Inputs are observation snapshots only."""
import json
import os

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
    def __init__(self):
        from google import genai
        self.client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
        self.model=os.environ.get('GEMINI_MODEL','gemini-3.8-flash')

    def decide(self,observation,scene):
        response=self.client.models.generate_content(model=self.model,
            contents=json.dumps({'observation':summarize(observation),
                                 'rooms':[{'id':r['id'],'name':r['name'],'observe':r.get('observe',r['cells'][:1])} for r in scene.rooms],
                                 'exits':scene.exits}),
            config={'system_instruction':'You supervise a simulated rescue. Choose a select_goal tool call using only supplied evidence. Prioritize confirmed survivors with viable evacuation routes; inspect unsearched rooms. Unknown occupants are not absent. Never infer actual occupancy from capacity. Explain briefly.',
                    'tools':[{'function_declarations':TOOLS}]})
        for call in response.function_calls or []:
            if call.name=='select_goal':return dict(call.args)
        raise ValueError('Model returned no select_goal action')


def apply_decision(sim,decision,observation_time):
    """Reject stale decisions, hidden identities, and unsupported destinations."""
    if sim.status!='running' or sim.time-observation_time>10:raise ValueError('Stale mission decision')
    if sim.escort:raise ValueError('Active escort owns the navigation goal')
    kind,identity=decision.get('kind'),decision.get('id')
    if kind=='room':
        room=next((r for r in sim.scene.rooms if r['id']==identity),None)
        if room is None:raise ValueError('Unknown room')
        goal=tuple(sim.observe_points(room)[0])
        if goal in sim.visited:raise ValueError('Room observation point already visited')
    elif kind=='survivor':
        # Only perception-assigned detection IDs are valid; simulator person IDs never are.
        d=next((d for d in sim.detections if d['id']==identity and d['status']=='confirmed'),None)
        if d is None:raise ValueError('Survivor not confirmed')
        goal=tuple(d['cell'])
    elif kind=='exit':
        if identity not in [str(i) for i in range(len(sim.scene.exits))]:raise ValueError('Unknown exit')
        goal=sim.scene.exits[int(identity)]
    else:raise ValueError('Unknown tool argument')
    if not sim.path(goal):raise ValueError('No viable route')
    sim.goal=goal;sim.log(f"Hosted agent: {kind} {identity}: {str(decision.get('reason',''))[:300]}")
