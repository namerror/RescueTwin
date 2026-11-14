"""Optional hosted mission decisions. Inputs are observation snapshots only."""
import json
import os

TOOLS=[{'name':'select_goal','description':'Select a room inspection, confirmed survivor, or known exit. Controller validates and executes.',
        'parameters':{'type':'object','properties':{'kind':{'type':'string','enum':['room','survivor','exit']},
        'id':{'type':'string'},'reason':{'type':'string'}},'required':['kind','id','reason']}}]

class MissionAgent:
    def __init__(self):
        from google import genai
        self.client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
        self.model=os.environ.get('GEMINI_MODEL','gemini-3.8-flash')

    def decide(self,observation,scene):
        response=self.client.models.generate_content(model=self.model,
            contents=json.dumps({'observation':observation,'rooms':scene.rooms,'exits':scene.exits}),
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
        goal=tuple(room['cells'][0])
        if goal in sim.visited:raise ValueError('Room observation point already visited')
    elif kind=='survivor':
        p=next((p for p in sim.survivors if p['id']==identity and identity in sim.known and p['status']=='identified'),None)
        if p is None:raise ValueError('Survivor not confirmed')
        goal=tuple(p['position'])
    elif kind=='exit':
        if identity not in [str(i) for i in range(len(sim.scene.exits))]:raise ValueError('Unknown exit')
        goal=sim.scene.exits[int(identity)]
    else:raise ValueError('Unknown tool argument')
    if not sim.path(goal):raise ValueError('No viable route')
    sim.goal=goal;sim.log(f"Hosted agent: {kind} {identity}: {str(decision.get('reason',''))[:300]}")
