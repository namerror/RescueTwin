"""Deterministic development simulator. No calibrated fire or physical robotics claims."""
from __future__ import annotations
from dataclasses import dataclass, field
import heapq
import math
import random
from copy import deepcopy

Cell = tuple[int, int]

@dataclass
class Scene:
    id: str = 'development-apartment'
    name: str = 'Synthetic development apartment'
    width: int = 32
    height: int = 24
    resolution: float = .5
    walls: set[Cell] = field(default_factory=set)
    exits: list[Cell] = field(default_factory=lambda: [(0, 6), (31, 18)])
    rooms: list = field(default_factory=list)
    source: str = 'synthetic'

    def free(self, p):
        return 0 <= p[0] < self.width and 0 <= p[1] < self.height and tuple(p) not in self.walls

    def neighbors(self, p):
        return [q for q in [(p[0]+1,p[1]),(p[0]-1,p[1]),(p[0],p[1]+1),(p[0],p[1]-1)] if self.free(q)]

    def public(self):
        return dict(id=self.id,name=self.name,width=self.width,height=self.height,resolution=self.resolution,
                    walls=sorted(self.walls),exits=self.exits,rooms=self.rooms,source=self.source)


def fixture():
    s = Scene()
    s.walls = {(x,y) for x in range(s.width) for y in range(s.height)
               if x in (0,s.width-1) or y in (0,s.height-1)}
    s.walls |= {(10,y) for y in range(1,23) if y not in (6,7,17,18)}
    s.walls |= {(21,y) for y in range(1,23) if y not in (6,7,17,18)}
    s.walls |= {(x,12) for x in range(1,31) if x not in (5,6,15,16,25,26)}
    s.walls -= set(s.exits)
    for i,(x,y) in enumerate([(5,6),(15,6),(26,6),(5,18),(15,18),(26,18)]):
        s.rooms.append(dict(id=f'room-{i}',name=f'Room {i+1}',cells=[(x,y)]))
    return s


def route(scene, start, goal, heat, smoke, observed, adaptive=True, escort=False):
    """A* sees belief fields only; hard limits apply to both policies."""
    start,goal=tuple(start),tuple(goal)
    if not scene.free(start) or not scene.free(goal): return []
    queue=[(0,0,start)]; costs={start:0}; previous={}
    while queue:
        _,cost,p=heapq.heappop(queue)
        if cost != costs[p]: continue
        if p==goal:
            result=[p]
            while p in previous: p=previous[p]; result.append(p)
            return result[::-1]
        for q in scene.neighbors(p):
            x,y=q
            if heat[y][x] >= (.6 if escort else .8) or smoke[y][x] >= (.5 if escort else .8): continue
            if escort and not observed[y][x]: continue
            penalty=(8*heat[y][x]+5*smoke[y][x]+(0 if observed[y][x] else 2)) if adaptive else 0
            c=cost+1+penalty
            if c < costs.get(q,math.inf):
                costs[q]=c;previous[q]=p
                heapq.heappush(queue,(c+abs(x-goal[0])+abs(y-goal[1]),c,q))
    return []


def line_visible(scene,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    n=max(abs(dx),abs(dy),1)*2
    return all(scene.free((round(a[0]+dx*i/n),round(a[1]+dy*i/n))) for i in range(n+1))


class Simulation:
    def __init__(self,scene=None,scenario=None,mode='adaptive'):
        self.scene=scene or fixture()
        self.scenario=deepcopy(scenario or dict(survivors=[[26,6],[15,18]],ignitions=[[25,18]],seed=7))
        self.validate(self.scenario)
        self.mode=mode;self.time=0.;self.status='idle';self.robot=self.scene.exits[0]
        self.heat=self.grid();self.smoke=self.grid();self.fuel=self.grid(1.)
        self.observed=self.grid();self.belief_heat=self.grid();self.belief_smoke=self.grid()
        self.last_seen=self.grid(-1.);self.route=[];self.goal=None;self.visited=set();self.known=set();self.escort=None
        self.events=[];self.metrics=dict(evacuated=0,exposure=0.,distance=0.)
        self.survivors=[dict(id=f'person-{i}',position=tuple(p),status='unobserved') for i,p in enumerate(self.scenario['survivors'])]
        self.rng=random.Random(self.scenario['seed']);self.steps=0
        self.perception_mode='analytic-development';self.log('Synthetic fixture; analytic development sensing. Not a validated property rehearsal.')
        for x,y in self.scenario['ignitions']: self.heat[y][x]=.9

    def grid(self,value=0.): return [[value for _ in range(self.scene.width)] for _ in range(self.scene.height)]

    def validate(self,scenario):
        if not isinstance(scenario.get('seed'),int): raise ValueError('seed must be an integer')
        for key in ('survivors','ignitions'):
            points=scenario.get(key)
            if not isinstance(points,list) or len(points)>20: raise ValueError(f'{key}: use at most 20 placements')
            seen=set()
            for p in points:
                if not isinstance(p,(list,tuple)) or len(p)!=2 or any(type(v) is not int for v in p) or not self.scene.free(p):
                    raise ValueError(f'{key}: placement must be a traversable integer grid cell')
                if tuple(p) in seen: raise ValueError(f'{key}: duplicate placement')
                if tuple(p) in self.scene.exits: raise ValueError('Keep egress endpoints clear')
                seen.add(tuple(p))
        if set(map(tuple,scenario['survivors'])) & set(map(tuple,scenario['ignitions'])): raise ValueError('Survivor and ignition overlap')

    def log(self,message):
        self.events.append(dict(time=round(self.time,2),message=message));self.events=self.events[-100:]

    def hazards(self,dt):
        h=self.grid();s=self.grid()
        for y in range(self.scene.height):
            for x in range(self.scene.width):
                p=(x,y)
                if not self.scene.free(p): continue
                ns=self.scene.neighbors(p);heat=self.heat[y][x];smoke=self.smoke[y][x]
                burn=max(0.,heat-.18)*self.fuel[y][x]*.5
                self.fuel[y][x]=max(0.,self.fuel[y][x]-burn*dt*.2)
                h[y][x]=max(0.,min(1.,heat+dt*(.3*sum(self.heat[b][a]-heat for a,b in ns)+burn-.025*heat)))
                s[y][x]=max(0.,min(1.,smoke+dt*(.5*sum(self.smoke[b][a]-smoke for a,b in ns)+burn*.6-.015*smoke)))
        self.heat,self.smoke=h,s

    def sense(self):
        # Explicit analytic adapter for core development; image detector is separate.
        rx,ry=self.robot
        for y in range(max(0,ry-7),min(self.scene.height,ry+8)):
            for x in range(max(0,rx-7),min(self.scene.width,rx+8)):
                if math.hypot(x-rx,y-ry)<=7 and line_visible(self.scene,self.robot,(x,y)):
                    if self.smoke[y][x]>.5: continue
                    self.observed[y][x]=1;self.last_seen[y][x]=self.time
                    # Local scalar readings only; no remote ground truth heat leak.
                    if (x,y)==self.robot:
                        self.belief_heat[y][x]=self.heat[y][x];self.belief_smoke[y][x]=self.smoke[y][x]
        if self.perception_mode=='analytic-development':
            for p in self.survivors:
                x,y=p['position']
                if p['status']=='unobserved' and self.last_seen[y][x]==self.time:
                    self.confirm(p['id'])

    def confirm(self,identity):
        self.known.add(identity)
        p=next(p for p in self.survivors if p['id']==identity)
        p['status']='identified';self.log(f'Potential survivor confirmed: {identity}')

    def observations(self):
        return dict(time=self.time,robot=self.robot,observed=self.observed,heat=self.belief_heat,smoke=self.belief_smoke,
                    survivors=[deepcopy(p) for p in self.survivors if p['id'] in self.known],events=self.events[-8:])

    def path(self,goal,escort=False):
        return route(self.scene,self.robot,goal,self.belief_heat,self.belief_smoke,self.observed,self.mode=='adaptive',escort)

    def choose(self):
        if self.escort:
            candidates=[self.path(e,True) for e in self.scene.exits]
            candidates=[p for p in candidates if p]
            if candidates:return min(candidates,key=self.path_cost)
            self.log('Escort paused: no observed viable egress route.');return []
        if self.goal is not None:
            if self.robot==self.goal:self.goal=None
            else:
                selected=self.path(self.goal)
                if selected:return selected
                self.goal=None
        targets=[p for p in self.survivors if p['id'] in self.known and p['status']=='identified']
        options=[self.path(p['position']) for p in targets];options=[p for p in options if p]
        if options:return min(options,key=self.path_cost)
        # Coverage targets are static geometry, not hidden survivor locations.
        options=[]
        for room in self.scene.rooms:
            for p in room['cells']:
                if tuple(p) not in self.visited:
                    path=self.path(p)
                    if path:options.append(path)
        return min(options,key=self.path_cost) if options else []

    def path_cost(self,path):
        return sum(1+(8*self.belief_heat[y][x]+5*self.belief_smoke[y][x] if self.mode=='adaptive' else 0) for x,y in path)

    def tick(self,dt=.2):
        if self.status!='running':return
        self.time+=dt;self.steps+=1;self.hazards(dt);self.sense()
        self.visited.add(self.robot)
        for p in self.survivors:
            if p['id'] in self.known and p['status']=='identified' and tuple(p['position'])==self.robot and not self.escort:
                self.escort=p['id'];p['status']='escorting';self.log(f"Escort started: {p['id']}")
        new=self.choose()
        if new and self.route and new[1:3]!=self.route[:2]:self.log('Route updated from observed evidence.')
        self.route=new[1:]
        # .5 m cells, .5 m/s; one cell per second.
        if self.steps%5==0 and self.route:
            q=tuple(self.route[0]);x,y=q
            hl,sl=(.6,.5) if self.escort else (.8,.8)
            # Independent immediate sensor/collision interlock, never global planner input.
            if self.heat[y][x]>=hl or self.smoke[y][x]>=sl:
                self.belief_heat[y][x]=self.heat[y][x];self.belief_smoke[y][x]=self.smoke[y][x]
                self.observed[y][x]=1;self.log('Local interlock stopped motion into hazard.');return
            self.robot=q;self.route=self.route[1:];self.metrics['distance']+=self.scene.resolution
            if self.escort:
                p=next(p for p in self.survivors if p['id']==self.escort);p['position']=q
                if q in self.scene.exits:
                    p['status']='evacuated';self.metrics['evacuated']+=1;self.log(f"Evacuated: {p['id']}");self.escort=None
        for p in self.survivors:
            if p['status']!='evacuated':
                x,y=p['position'];self.metrics['exposure']+=dt*(self.heat[y][x]+self.smoke[y][x])
        if self.metrics['evacuated']==len(self.survivors):self.status='completed';self.log('All configured survivors evacuated (simulator evaluation).')
        elif not new or self.time>=240:
            self.status='unresolved';self.log('Mission unresolved; unseen occupants are not confirmed absent.')

    def state(self):
        return dict(scene=self.scene.public(),scenario=self.scenario,time=round(self.time,2),status=self.status,
                    robot=self.robot,survivors=self.survivors,heat=self.heat,smoke=self.smoke,observed=self.observed,
                    route=self.route,events=self.events,metrics=self.metrics,mode=self.mode,perception_mode=self.perception_mode)


def compare(scene,scenario):
    reports={}
    for mode in ('baseline','adaptive'):
        sim=Simulation(scene,scenario,mode);sim.status='running'
        while sim.status=='running':sim.tick()
        reports[mode]=dict(status=sim.status,time=round(sim.time,2),**sim.metrics)
    return dict(seed=scenario['seed'],reports=reports,note='Deterministic policy comparison with analytic development sensing; hosted agent excluded. Illustrative exposure units; no guaranteed adaptive improvement.')
