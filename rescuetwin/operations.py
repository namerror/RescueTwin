"""Bounded skills and planning tools. Numerical planning receives an explicit belief-only view."""
from copy import deepcopy
from types import SimpleNamespace
import math
import uuid
from .core import route, route_tree, extract, ROBOT_SPEED, line_visible
from .contracts import observation, compact

class Tools:
    def __init__(self, sim):
        self.sim=sim; self.actions={}; self.active=None; self.history=[]; self.keys={};self.rate_state={}
    def view(self):
        s=self.sim
        # No truth grids, survivors, ignition lists, scenario or simulator stepping methods.
        v=SimpleNamespace(scene=s.base_scene,robot=s.robot,observed=s.observed.copy(),
            belief_heat=s.belief_heat.copy(),belief_smoke=s.belief_smoke.copy(),hazard_seen=s.hazard_seen.copy(),
            time=s.time,version=s.version,escort={str(i):d for i,d in enumerate(s.escort.values())},
            detections=deepcopy(s.detections),searched_points=set(s.searched_points),rooms_state=deepcopy(s.rooms_state),
            room_of=s.room_of.copy(),metrics={},strategy='mission-astar',mode=s.mode)
        v.__dict__.update(deepcopy(self.rate_state))
        fields=tuple(a.copy() for a in s.planning_fields())
        v.planning_fields=lambda: fields
        v.searched_rooms=lambda: list(s.searched_rooms())
        v.observe_points=lambda r: [tuple(p) for p in r.get('observe',r['cells'])]
        weights={r['id']:s.search_weight(r['id']) for r in v.scene.rooms}
        v.search_weight=lambda rid: weights.get(rid,1.)
        return v
    def routes(self,target,escort=None,start=None):
        v=self.view(); escort=bool(self.sim.escort) if escort is None else escort
        start=tuple(start or v.robot); target=tuple(target)
        if not v.scene.free(start) or not v.scene.free(target):return dict(eligible=False,reason='Invalid static-map endpoint',path=[])
        h,m=v.planning_fields()
        p=route(v.scene,start,target,h,m,v.observed,v.mode=='adaptive',escort)
        if not p:return dict(eligible=False,reason='No route admissible under current belief',path=[])
        length=(len(p)-1)*v.scene.resolution
        readings=[float(v.hazard_seen[y,x]) for x,y in p]
        return dict(eligible=True,path=p,length_m=round(length,3),eta_s=round(length/ROBOT_SPEED,3),
                    risk_cost=sum(1+8*h[y,x]+5*m[y,x] for x,y in p[1:]),
                    observed_fraction=sum(bool(v.observed[y,x]) for x,y in p)/len(p),
                    oldest_reading_s=min(readings),heat_max=max(float(h[y,x]) for x,y in p),
                    smoke_max=max(float(m[y,x]) for x,y in p),assumption='Latest observations persist; unknown space is uncertain')
    def list_actions(self):
        s=self.sim; out=[]
        def add(kind,target,**extra):
            a=dict(type=kind,target=list(target),**extra)
            if self.routes(target)['eligible']:out.append(a)
        if not s.escort:
            for room in s.base_scene.rooms:
                for p in s.observe_points(room):
                    if p not in s.searched_points and p!=s.robot:add('inspect_room',p,room_id=room['id'])
            for d in s.detections:
                if d['status'] in ('candidate','possible'):
                    for p in s.base_scene.neighbors(s.robot):
                        distance=math.dist(s.base_scene.world(p),d['position'])
                        if 1<=distance<=3 and line_visible(s.base_scene,p,tuple(d['cell'])):
                            add('verify_detection',p,track_id=d['id']);break
            # Geometry-derived region observation points; do not expose hidden hazards.
            for room in s.base_scene.rooms:
                p=s.observe_points(room)[0]
                if p!=s.robot:
                    add('inspect_corridor',p,region_id=room['id'])
                    add('recheck_hazard',p,region_id=room['id'])
        for d in s.detections:
            if d['status']=='confirmed':add('approach_person',d['cell'],track_id=d['id'])
        for i,p in enumerate(s.base_scene.exits):
            add('escort_to_exit' if s.escort else 'withdraw',p,exit_id=str(i))
        out.append(dict(type='wait_and_observe',target=list(s.robot),duration_s=2.))
        for d in s.detections:
            if d['status'] in ('confirmed','possible'):out.append(dict(type='request_assistance',target=d['cell'],track_id=d['id']))
        self.actions={str(i):a for i,a in enumerate(out)}
        return [dict(id=k,**v) for k,v in self.actions.items()]
    def recommendation(self):
        from .strategies import mission_plan
        v=self.view();goal=mission_plan(v)
        self.rate_state={k:v.__dict__[k] for k in ('rate_snapshot','rates') if k in v.__dict__}
        return dict(target=list(goal) if goal is not None else None,source='unchanged mission-astar')
    def evaluate_plans(self,candidates):
        if len(candidates)>3:raise ValueError('At most three candidates')
        out=[]
        for seq in candidates:
            if not 1<=len(seq)<=3:raise ValueError('One to three actions per plan')
            start=self.sim.robot; legs=[]
            for identity in seq:
                a=self.actions[str(identity)];r=self.routes(a['target'],start=start);legs.append(r);start=a['target']
            out.append(dict(actions=seq,eligible=all(r['eligible'] for r in legs),
                eta_s=sum(r.get('eta_s',0) for r in legs),risk_cost=sum(r.get('risk_cost',0) for r in legs),
                assumption='Belief-only geometric rollout; no predicted discoveries or hidden outcomes',legs=legs))
        return out
    def commit(self,action_id,snapshot_id,key,reason='',expected_outcome=None,reconsider_on=None,conditional_next=None):
        if key in self.keys:return self.keys[key]
        if snapshot_id!=observation(self.sim)['snapshot_id']:raise ValueError('Stale snapshot')
        if self.sim.status!='running' or self.sim.withdraw_reason:raise ValueError('Mission not available')
        if self.active:raise ValueError('An action is already active')
        a=deepcopy(self.actions[str(action_id)])
        if a['type']!='request_assistance' and not self.routes(a['target'])['eligible']:raise ValueError('Route invalidated')
        if self.sim.escort and a['type']=='approach_person':
            from .strategies import margin_tree
            view=self.view();costs,_=margin_tree(view,view.robot)
            target=tuple(a['target']);back,_=margin_tree(view,target)
            if target not in costs or not any(e in back for e in view.scene.exits):raise ValueError('Pickup lacks margin route and egress')
        a.update(id=uuid.uuid4().hex,status='running',started_at=self.sim.time,reason=reason[:300],expected_outcome=expected_outcome,reconsider_on=reconsider_on or [],conditional_next=conditional_next or {})
        self.active=a;self.keys[key]=a;self.sim.goal=None
        if a['type']=='withdraw':self.sim.withdraw('agent selected withdrawal')
        if a['type']=='request_assistance':
            self.sim.report('assistance-request',tuple(a['target']),action='Agent requests simulated responder assistance',detection=a['track_id'])
            self.finish('success')
        return a
    def finish(self,status,reason=None):
        if self.active:
            self.active.update(status=status,finished_at=self.sim.time,outcome=reason)
            self.history.append(self.active);self.active=None
    def navigation(self):
        a=self.active;s=self.sim
        if a is None:return None
        if s.withdraw_reason:self.finish('interrupted','Withdrawal');return None
        if s.escort and a['type'] not in ('approach_person','escort_to_exit','wait_and_observe'):
            self.finish('interrupted','Escort takes precedence');return None
        exits=[self.routes(e) for e in s.base_scene.exits]
        viable=[r for r in exits if r['eligible']]
        if not viable or min(r['eta_s'] for r in viable)+15>s.scene.time_limit-s.time:
            self.finish('interrupted','Return margin');s.withdraw('controller return margin');return None
        if a['type']=='wait_and_observe':
            if s.time-a['started_at']>=a['duration_s']:self.finish('success');return None
            s.holding=True;return []
        target=tuple(a['target'])
        if s.robot==target:
            if a['type']=='approach_person':
                track=next((d for d in s.detections if d['id']==a['track_id']),{})
                self.finish('success' if track.get('status') in ('escorting','handoff','evacuated') else 'failed','Contact outcome: '+track.get('status','missing'))
                return None
            if s.scan_remaining>0:return [s.robot]
            if a['type'] in ('inspect_room','verify_detection','inspect_corridor','recheck_hazard') and not a.get('scan_started'):
                a['scan_started']=True
                if s.perception_mode=='image-yolo':s.scan_remaining=2*math.pi;return [s.robot]
            self.finish('success');return None
        if a['type']=='approach_person' and s.escort:
            self.finish('success','Contact established');return None
        r=self.routes(target)
        if not r['eligible']:self.finish('failed',r['reason']);return None
        return [tuple(p) for p in r['path']]
    def dispatch(self,name,arguments):
        methods={'get_belief':lambda **kw:compact(self.sim),
            'get_evidence':lambda ids: [s for s in observation(self.sim)['samples'] if s['id'] in ids],
            'list_actions':self.list_actions,'evaluate_routes':self.routes,
            'get_mission_recommendation':self.recommendation,'evaluate_plans':self.evaluate_plans,
            'commit_action':self.commit,'get_action_status':lambda:dict(active=self.active,history=self.history),
            'cancel_action':lambda reason:self.finish('interrupted',reason)}
        if name not in methods:raise ValueError('Unknown tool')
        result=methods[name](**arguments)
        self.sim.__dict__.get('recorder') and self.sim.recorder.event('tool',dict(name=name,arguments=arguments,result=result),self.sim.time)
        return result
