"""Deterministic development simulator. No calibrated fire or physical robotics claims.

All tunables are metric so a 0.5 m synthetic grid and a 0.25 m scanned grid behave alike.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import heapq
import math
import random
from copy import deepcopy

import numpy as np

Cell = tuple[int, int]

ROBOT_SPEED = 0.5            # m/s along the grid
SENSE_RANGE_M = 3.5          # analytic (fixture) sensing radius
CAMERA_RANGE_M = 4.0         # rendered depth range used for image perception
CAMERA_FOV = math.radians(90)
CAMERA_HEIGHT_M = 1.0
SCAN_RATE = math.radians(90)  # rad/s rotate-in-place at room observation points (image mode)
PERCEPTION_PERIOD = 0.4      # s of simulated time between rendered frames (image mode)
# Fire model from upstream PR #1 (floor2-hazard-preview), illustrative, not calibrated fire behaviour.
# Upstream's coefficients are per 0.5 m cell; diffusion is rescaled by (0.5 / resolution)^2 so the model
# behaves the same in metres on any grid (identical to upstream at 0.5 m).
UPSTREAM_CELL_M = 0.5
UPSTREAM_IGNITION_HOLD_S = 45.0    # ignition cells are held at heat >= 0.9 for this long
UPSTREAM_IGNITION_HEAT = 0.9
UPSTREAM_BURN_THRESHOLD = 0.14
UPSTREAM_BURN_GAIN = 0.85
UPSTREAM_FUEL_USE = 0.2
UPSTREAM_HEAT_DIFFUSION = 0.3      # per neighbour difference per second, at 0.5 m cells
UPSTREAM_SMOKE_DIFFUSION = 0.5
UPSTREAM_HEAT_DECAY = 0.025
UPSTREAM_SMOKE_YIELD = 0.6
UPSTREAM_SMOKE_DECAY = 0.015
HAZARD_PREVIEW_LIMIT_S = 600.0
FLAME_VISIBLE_HEAT = 0.3           # flames are drawn and visible to sensing at or above this heat
IGNITION_RADIUS_M = 0.3
DEDUP_M = 0.75               # detections closer than this are the same potential survivor
PAIR_M = 0.75                # robot must reach this close to start an escort
BELIEF_STALE_S = 60.0        # older hazard readings keep their risk penalty but stop being hard planning blocks
STALE_HEAT_CAP, STALE_SMOKE_CAP = .59, .49   # just under the strictest (escort) limits
CONFIRM_HITS = {'analytic-development': 1, 'image-yolo': 2}


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
    origin: tuple[float, float] = (0., 0.)
    robot_start: Cell | None = None
    time_limit: float = 240.
    meta: dict = field(default_factory=dict)
    bundle: Path | None = None

    def free(self, p):
        return 0 <= p[0] < self.width and 0 <= p[1] < self.height and tuple(p) not in self.walls

    def neighbors(self, p):
        return [q for q in [(p[0]+1,p[1]),(p[0]-1,p[1]),(p[0],p[1]+1),(p[0],p[1]-1)] if self.free(q)]

    def world(self, p):
        return (self.origin[0] + (p[0] + .5) * self.resolution, self.origin[1] + (p[1] + .5) * self.resolution)

    def cell(self, x, y):
        return (int((x - self.origin[0]) // self.resolution), int((y - self.origin[1]) // self.resolution))

    def free_mask(self):
        mask = np.ones((self.height, self.width), bool)
        for x, y in self.walls:
            if 0 <= x < self.width and 0 <= y < self.height:
                mask[y, x] = False
        return mask

    def summary(self):
        checks = self.meta.get('checks') or {}
        return dict(id=self.id, name=self.name, source=self.source, exits=len(self.exits), rooms=len(self.rooms),
                    checks_passed=checks.get('passed'), has_mesh=bool(self.meta.get('mesh')))

    def public(self):
        exterior = {tuple(p) for p in self.meta.get('exterior', [])}
        return dict(id=self.id, name=self.name, width=self.width, height=self.height, resolution=self.resolution,
                    origin=list(self.origin), walls=sorted(p for p in self.walls if p not in exterior),
                    exterior=sorted(exterior), exits=self.exits,
                    rooms=[{k: v for k, v in r.items()} for r in self.rooms], source=self.source,
                    robot_start=self.robot_start or self.exits[0], time_limit=self.time_limit,
                    **{k: v for k, v in self.meta.items() if k not in ('exterior',)})


def fixture():
    s = Scene()
    s.walls = {(x,y) for x in range(s.width) for y in range(s.height)
               if x in (0,s.width-1) or y in (0,s.height-1)}
    s.walls |= {(10,y) for y in range(1,23) if y not in (6,7,17,18)}
    s.walls |= {(21,y) for y in range(1,23) if y not in (6,7,17,18)}
    s.walls |= {(x,12) for x in range(1,31) if x not in (5,6,15,16,25,26)}
    s.walls -= set(s.exits)
    for i,(x,y) in enumerate([(5,6),(15,6),(26,6),(5,18),(15,18),(26,18)]):
        s.rooms.append(dict(id=f'room-{i}',name=f'Room {i+1}',cells=[(x,y)],observe=[(x,y)]))
    s.meta = dict(assumptions=['Synthetic development fixture; not a property.'])
    return s


def step_cost(heat, smoke, observed, x, y, adaptive, escort):
    """None if the cell violates hard limits; otherwise the traversal cost (cells)."""
    if heat[y][x] >= (.6 if escort else .8) or smoke[y][x] >= (.5 if escort else .8): return None
    if escort and not observed[y][x]: return None
    return 1 + ((8*heat[y][x]+5*smoke[y][x]+(0 if observed[y][x] else 2)) if adaptive else 0)


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
            step=step_cost(heat,smoke,observed,q[0],q[1],adaptive,escort)
            if step is None: continue
            c=cost+step
            if c < costs.get(q,math.inf):
                costs[q]=c;previous[q]=p
                heapq.heappush(queue,(c+abs(q[0]-goal[0])+abs(q[1]-goal[1]),c,q))
    return []


def route_tree(scene, start, heat, smoke, observed, adaptive=True, escort=False):
    """Single-source Dijkstra with the same costs/limits as route(); one pass serves every goal."""
    start=tuple(start); costs={start:0.}; previous={}; queue=[(0.,start)]
    while queue:
        cost,p=heapq.heappop(queue)
        if cost != costs[p]: continue
        for q in scene.neighbors(p):
            step=step_cost(heat,smoke,observed,q[0],q[1],adaptive,escort)
            if step is None: continue
            c=cost+step
            if c < costs.get(q,math.inf):
                costs[q]=c;previous[q]=p;heapq.heappush(queue,(c,q))
    return costs,previous


def extract(previous, start, goal):
    goal=tuple(goal);path=[goal]
    while path[-1]!=tuple(start):
        if path[-1] not in previous: return []
        path.append(previous[path[-1]])
    return path[::-1]


def line_visible(scene,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    n=max(abs(dx),abs(dy),1)*2
    return all(scene.free((round(a[0]+dx*i/n),round(a[1]+dy*i/n))) for i in range(n+1))


def angle_diff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Simulation:
    def __init__(self,scene=None,scenario=None,mode='adaptive',perception='analytic-development'):
        self.scene=scene or fixture()
        default=dict(survivors=[[26,6],[15,18]],ignitions=[[25,18]],seed=7) if self.scene.id=='development-apartment' else dict(survivors=[],ignitions=[],seed=7)
        self.scenario=deepcopy(scenario or default)
        self.scenario.setdefault('scene_id',self.scene.id)
        if self.scenario['scene_id']!=self.scene.id: raise ValueError('Scenario belongs to a different scene')
        self.validate(self.scenario)
        if perception not in CONFIRM_HITS: raise ValueError('Unknown perception mode')
        self.mode=mode;self.perception_mode=perception;self.time=0.;self.status='idle'
        self.robot=tuple(self.scene.robot_start or self.scene.exits[0]);self.heading=0.;self.progress=0.
        self.scan_remaining=0.
        self.free=self.scene.free_mask()
        H,W=self.free.shape
        self.heat=np.zeros((H,W));self.smoke=np.zeros((H,W));self.fuel=np.where(self.free,1.,0.)
        self.ignition_cells=set();self.hazard_time=0.;self.hazards_running=False
        self.observed=np.zeros((H,W));self.belief_heat=np.zeros((H,W));self.belief_smoke=np.zeros((H,W));self.last_seen=np.full((H,W),-1.);self.hazard_seen=np.full((H,W),-1.)
        self.route=[];self.goal=None;self.visited=set();self.escort=None;self.escort_detection=None
        self.detections=[];self.next_detection=0;self.version=0
        self.events=[];self.metrics=dict(evacuated=0,exposure=0.,distance=0.,detections=0,false_detections=0,route_changes=0,rooms_inspected=0)
        self.survivors=[dict(id=f'person-{i}',position=tuple(p),status='unobserved') for i,p in enumerate(self.scenario['survivors'])]
        self.rng=random.Random(self.scenario['seed']);self.steps=0
        self.frame_request=None;self.frame_seq=0;self.degraded=False;self.last_frame=None;self.holding=False
        label='Synthetic fixture' if self.scene.source=='synthetic' else 'Scanned geometry with synthetic hazards and people'
        self.log(f"{label}; perception: {'analytic fixture (not image-based)' if perception=='analytic-development' else 'YOLO on rendered RGB-D'}.")
        for x,y in self.scenario['ignitions']:
            cx,cy=self.scene.world((x,y))
            for yy in range(H):
                for xx in range(W):
                    if self.free[yy,xx] and math.dist(self.scene.world((xx,yy)),(cx,cy))<=max(IGNITION_RADIUS_M,self.scene.resolution*.5):
                        self.heat[yy,xx]=UPSTREAM_IGNITION_HEAT;self.ignition_cells.add((xx,yy))
        self.ignition_mask=np.zeros((H,W),bool)
        for xx,yy in self.ignition_cells:self.ignition_mask[yy,xx]=True

    def grid(self,value=0.): return np.full((self.scene.height,self.scene.width),float(value))

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
                start=tuple(self.scene.robot_start or self.scene.exits[0])
                if tuple(p)==start: raise ValueError('Keep the robot start cell clear')
                seen.add(tuple(p))
        if set(map(tuple,scenario['survivors'])) & set(map(tuple,scenario['ignitions'])): raise ValueError('Survivor and ignition overlap')

    def log(self,message):
        self.events.append(dict(time=round(self.time,2),message=message));self.events=self.events[-100:]

    # ---------------------------------------------------------------- hazards
    def hazards(self,dt):
        """Upstream PR #1 fire model in NumPy: ignition cells are held hot for 45 s; cells above the burn
        threshold consume fuel and produce heat and smoke; heat and smoke diffuse only between 4-connected
        free cells (walls block, doorways connect); everything decays once fuel is spent."""
        free=self.free;scale=(UPSTREAM_CELL_M/self.scene.resolution)**2
        kh,ks=UPSTREAM_HEAT_DIFFUSION*scale,UPSTREAM_SMOKE_DIFFUSION*scale
        n=max(1,math.ceil(dt*4*max(kh,ks)/.8));h=dt/n
        for _ in range(n):
            self.hazard_time+=h
            heat,smoke=self.heat,self.smoke
            source=np.where(self.ignition_mask,UPSTREAM_IGNITION_HEAT,0.) if self.hazard_time<=UPSTREAM_IGNITION_HOLD_S else 0.
            burn=np.maximum(0.,heat-UPSTREAM_BURN_THRESHOLD)*self.fuel*UPSTREAM_BURN_GAIN
            self.fuel=np.maximum(0.,self.fuel-burn*h*UPSTREAM_FUEL_USE)
            self.heat=np.where(free,np.maximum(source,np.minimum(1.,heat+h*(kh*self._laplacian(heat)+burn-UPSTREAM_HEAT_DECAY*heat))),0.)
            self.smoke=np.where(free,np.clip(smoke+h*(ks*self._laplacian(smoke)+burn*UPSTREAM_SMOKE_YIELD-UPSTREAM_SMOKE_DECAY*smoke),0,1),0.)

    def _laplacian(self,field):
        free=self.free;out=np.zeros_like(field)
        for axis,shift in ((0,1),(0,-1),(1,1),(1,-1)):
            neighbour=np.roll(field,shift,axis);ok=np.roll(free,shift,axis)
            if axis==0: (ok[0,:] if shift==1 else ok[-1,:]).fill(False)
            else: (ok[:,0] if shift==1 else ok[:,-1]).fill(False)
            out+=np.where(ok&free,neighbour-field,0.)
        return out

    # ---------------------------------------------------------------- sensing
    def camera_pose(self):
        x,y=self.scene.world(self.robot)
        return dict(x=x,y=y,z=CAMERA_HEIGHT_M,yaw=self.heading)

    def sense(self):
        rx,ry=self.robot
        image=self.perception_mode=='image-yolo'
        reach=CAMERA_RANGE_M if image else SENSE_RANGE_M
        r=int(math.ceil(reach/self.scene.resolution))
        wx,wy=self.scene.world(self.robot)
        changed=False
        for y in range(max(0,ry-r),min(self.scene.height,ry+r+1)):
            for x in range(max(0,rx-r),min(self.scene.width,rx+r+1)):
                if not self.free[y,x] and (x,y)!=(rx,ry): continue
                cx,cy=self.scene.world((x,y));d=math.dist((cx,cy),(wx,wy))
                if d>reach: continue
                if image and (x,y)!=(rx,ry) and abs(angle_diff(math.atan2(cy-wy,cx-wx),self.heading))>CAMERA_FOV/2: continue
                if not line_visible(self.scene,self.robot,(x,y)): continue
                # Analytic visual hazard sensing (not image-based): smoke density is visible along the line of
                # sight, flames are visible where heat >= FLAME_VISIBLE_HEAT. Other heat is only measured locally.
                self.belief_smoke[y,x]=self.smoke[y,x];self.hazard_seen[y,x]=self.time
                if self.smoke[y,x]>.5: continue   # dense smoke hides what is in the cell
                if self.heat[y,x]>=FLAME_VISIBLE_HEAT: self.belief_heat[y,x]=self.heat[y,x]
                elif self.belief_heat[y,x]>0 and self.heat[y,x]<self.belief_heat[y,x]: self.belief_heat[y,x]=self.heat[y,x]
                changed|=not self.observed[y,x];self.observed[y,x]=1;self.last_seen[y,x]=self.time
        self.belief_heat[ry,rx]=self.heat[ry,rx];self.belief_smoke[ry,rx]=self.smoke[ry,rx];self.hazard_seen[ry,rx]=self.time
        # The robot measures its own cell directly, smoke or not, so the path it has walked is observed.
        changed|=not self.observed[ry,rx];self.observed[ry,rx]=1;self.last_seen[ry,rx]=self.time
        if self.perception_mode=='analytic-development':
            # Labelled fixture detector: emits evidence at visible proxy positions, never their identities.
            for p in self.survivors:
                x,y=p['position']
                if p['status']=='unobserved' and self.last_seen[y,x]==self.time:
                    self.add_evidence(self.scene.world((x,y)),1.,'analytic-fixture')
        if changed:self.version+=1

    def add_evidence(self,position,confidence,source):
        """Merge a world-frame person observation into belief; returns the detection entry."""
        position=(float(position[0]),float(position[1]))
        cell=self.scene.cell(*position)
        if not self.scene.free(cell):
            near=[(abs(dx)+abs(dy),(cell[0]+dx,cell[1]+dy)) for dx in range(-2,3) for dy in range(-2,3) if self.scene.free((cell[0]+dx,cell[1]+dy))]
            if not near: return None
            cell=min(near)[1]
        for d in self.detections:
            if d['status'] in ('confirmed','candidate') and math.dist(d['position'],position)<=DEDUP_M:
                n=d['hits'];d['position']=[(d['position'][0]*n+position[0])/(n+1),(d['position'][1]*n+position[1])/(n+1)]
                d['hits']+=1;d['confidence']=max(d['confidence'],confidence);d['last_seen']=self.time
                d['cell']=list(self.nearest_free(self.scene.cell(*d['position'])))
                if d['status']=='candidate' and d['hits']>=CONFIRM_HITS[self.perception_mode]:self.confirm(d)
                return d
        d=dict(id=f'det-{self.next_detection}',position=list(position),cell=list(cell),confidence=confidence,hits=1,
               first_seen=self.time,last_seen=self.time,status='candidate',source=source)
        self.next_detection+=1;self.detections.append(d);self.version+=1
        if CONFIRM_HITS[self.perception_mode]<=1:self.confirm(d)
        return d

    def nearest_free(self,cell):
        if self.scene.free(cell): return tuple(cell)
        near=[(abs(dx)+abs(dy),(cell[0]+dx,cell[1]+dy)) for dx in range(-3,4) for dy in range(-3,4) if self.scene.free((cell[0]+dx,cell[1]+dy))]
        return min(near)[1] if near else tuple(cell)

    def confirm(self,d):
        d['status']='confirmed';self.metrics['detections']+=1;self.version+=1
        self.log(f"Potential survivor confirmed: {d['id']} ({d['source']}, {d['hits']} observation{'s' if d['hits']>1 else ''}).")

    def observations(self):
        return dict(time=self.time,version=self.version,robot=self.robot,heading=self.heading,observed=self.observed.tolist(),
                    heat=self.belief_heat.round(3).tolist(),smoke=self.belief_smoke.round(3).tolist(),
                    survivors=[deepcopy(d) for d in self.detections if d['status'] in ('confirmed','escorting')],
                    perception=self.perception_mode,events=self.events[-8:])

    # ---------------------------------------------------------------- planning
    def planning_fields(self):
        """Belief as the planner sees it: readings older than BELIEF_STALE_S keep their risk penalty but are
        capped below the hard limits, so a hazard that may have burned out can be re-approached. The local
        interlock still reads the next cell before every move, so stale beliefs never permit unsafe entry."""
        stale=(self.hazard_seen>=0)&(self.time-self.hazard_seen>BELIEF_STALE_S)
        heat=np.where(stale,np.minimum(self.belief_heat,STALE_HEAT_CAP),self.belief_heat)
        smoke=np.where(stale,np.minimum(self.belief_smoke,STALE_SMOKE_CAP),self.belief_smoke)
        return heat,smoke

    def path(self,goal,escort=False):
        heat,smoke=self.planning_fields()
        return route(self.scene,self.robot,goal,heat,smoke,self.observed,self.mode=='adaptive',escort)

    def tree(self,escort=False):
        heat,smoke=self.planning_fields()
        return route_tree(self.scene,self.robot,heat.tolist(),smoke.tolist(),self.observed.tolist(),self.mode=='adaptive',escort)

    def observe_points(self,room):
        return [tuple(p) for p in room.get('observe',room['cells'])]

    def choose(self):
        if self.escort:
            costs,prev=self.tree(True)
            options=[(costs[e],e) for e in self.scene.exits if e in costs]
            if options:return extract(prev,self.robot,min(options)[1])
            self.hold('Escort paused: no observed viable egress route; holding until conditions change.');return []
        if self.goal is not None:
            if self.robot==self.goal:self.goal=None
            else:
                selected=self.path(self.goal)
                if selected:return selected
                self.goal=None
        costs,prev=self.tree()
        targets=[tuple(d['cell']) for d in self.detections if d['status']=='confirmed']
        options=[(costs[t],t) for t in targets if t in costs]
        if options:return extract(prev,self.robot,min(options)[1])
        # Coverage targets are static geometry, not hidden survivor locations.
        remaining=[p for room in self.scene.rooms for p in self.observe_points(room) if p not in self.visited]
        options=[(costs[p],p) for p in remaining if p in costs]
        if options:return extract(prev,self.robot,min(options)[1])
        if targets or remaining:
            self.hold('No viable route to remaining targets; holding until observed hazards change.')
        return []

    def hold(self,message):
        """Targets exist but are currently unreachable. Fire burns out in the upstream model, so wait
        (the mission time limit still applies) instead of declaring the mission unresolved at once."""
        if not getattr(self,'was_holding',False):self.log(message)
        self.holding=True

    def path_cost(self,path):
        return sum(1+(8*self.belief_heat[y][x]+5*self.belief_smoke[y][x] if self.mode=='adaptive' else 0) for x,y in path)

    # ---------------------------------------------------------------- loop
    def needs_frame(self):
        return self.perception_mode=='image-yolo' and self.frame_request is not None

    def request_frame(self):
        self.frame_seq+=1
        self.frame_request=dict(seq=self.frame_seq,time=round(self.time,3),pose=self.camera_pose(),robot=list(self.robot))

    def tick(self,dt=.2):
        if self.status!='running' or self.needs_frame():return
        self.time+=dt;self.steps+=1;self.hazards(dt)
        if self.scan_remaining>0:
            turn=min(self.scan_remaining,SCAN_RATE*dt);self.heading=(self.heading+turn)%(2*math.pi);self.scan_remaining-=turn
            if self.scan_remaining<=1e-9:self.scan_done()
        self.sense()
        if self.robot not in self.visited:
            self.visited.add(self.robot)
            if any(self.robot in self.observe_points(r) for r in self.scene.rooms):
                if self.perception_mode=='image-yolo' and not self.escort:
                    self.scan_remaining=2*math.pi;self.log('Scanning room from observation point.')
                else:self.metrics['rooms_inspected']+=1
        self.pair()
        if self.status!='running':return
        moving=self.scan_remaining<=0
        self.was_holding,self.holding=self.holding,False
        new=self.choose() if moving else [self.robot]
        if moving and self.was_holding and not self.holding and new:self.log('Route available again; resuming.')
        if not moving:self.holding=self.was_holding
        if moving and new and self.route and new[1:3]!=self.route[:2]:
            self.metrics['route_changes']+=1;self.log('Route updated from observed evidence.')
        self.route=new[1:]
        self.progress+=ROBOT_SPEED*dt if moving and self.route else 0
        if self.progress>=self.scene.resolution-1e-9 and self.route:
            self.progress-=self.scene.resolution
            q=tuple(self.route[0]);x,y=q
            hl,sl=(.6,.5) if self.escort else (.8,.8)
            # Independent immediate sensor/collision interlock, never global planner input.
            if self.heat[y,x]>=hl or self.smoke[y,x]>=sl:
                self.belief_heat[y,x]=self.heat[y,x];self.belief_smoke[y,x]=self.smoke[y,x];self.hazard_seen[y,x]=self.time
                self.observed[y,x]=1;self.progress=0;self.log('Local interlock stopped motion into hazard.')
            else:
                self.heading=math.atan2(y-self.robot[1],x-self.robot[0])
                self.robot=q;self.route=self.route[1:];self.metrics['distance']+=self.scene.resolution
                if self.escort:
                    p=next(p for p in self.survivors if p['id']==self.escort);p['position']=q
                    if q in self.scene.exits:
                        p['status']='evacuated';self.metrics['evacuated']+=1;self.escort=None
                        d=next(d for d in self.detections if d['id']==self.escort_detection);d['status']='evacuated';self.escort_detection=None
                        self.log(f"Evacuated: {d['id']}");self.version+=1
        elif not self.route:self.progress=0
        for p in self.survivors:
            if p['status']!='evacuated':
                x,y=p["position"];self.metrics["exposure"]+=float(dt*(self.heat[y,x]+self.smoke[y,x]))
        if self.survivors and self.metrics['evacuated']==len(self.survivors):self.status='completed';self.log('All configured survivors evacuated (simulator evaluation).')
        elif (not new and self.scan_remaining<=0 and not self.holding) or self.time>=self.scene.time_limit:
            self.status='unresolved';self.log('Mission unresolved; unseen occupants are not confirmed absent.')
        if self.status=='running' and self.perception_mode=='image-yolo' and int(round(self.time/dt))%max(1,round(PERCEPTION_PERIOD/dt))==0:
            self.request_frame()

    def scan_done(self):
        self.scan_remaining=0.;self.metrics['rooms_inspected']+=1;self.log('Room scan complete.')

    def pair(self):
        """Simulator-side contact: the robot reaches a confirmed detection; truth decides who is there."""
        if self.escort:return
        here=self.scene.world(self.robot)
        for d in self.detections:
            if d['status']!='confirmed' or math.dist(self.scene.world(tuple(d['cell'])),here)>PAIR_M:continue
            person=next((p for p in self.survivors if p['status']=='unobserved' and math.dist(self.scene.world(p['position']),here)<=PAIR_M),None)
            if person is None:
                if tuple(d['cell'])==self.robot:
                    d['status']='not-found';self.metrics['false_detections']+=1;self.version+=1
                    self.log(f"No person found at {d['id']} on arrival; detection retired.")
                continue
            person['status']='escorting';person['position']=self.robot;self.escort=person['id']
            d['status']='escorting';self.escort_detection=d['id'];self.scan_remaining=0;self.version+=1
            self.log(f"Escort started: {d['id']}");return

    def state(self):
        public=[]
        for p in self.survivors:
            item=dict(p)
            if p['status']=='unobserved' and any(d['status']=='confirmed' and math.dist(d['position'],self.scene.world(p['position']))<=DEDUP_M for d in self.detections):
                item['status']='identified'
            public.append(item)
        return dict(scene_id=self.scene.id,scenario=self.scenario,time=round(self.time,2),status=self.status,
                    hazard_time=round(self.hazard_time,2),hazards_running=self.hazards_running,
                    robot=self.robot,heading=round(self.heading,4),pose=self.camera_pose(),survivors=public,
                    heat=self.heat.round(3).tolist(),smoke=self.smoke.round(3).tolist(),observed=self.observed.astype(int).tolist(),
                    route=self.route,events=self.events,metrics={k:(round(v,3) if isinstance(v,float) else v) for k,v in self.metrics.items()},
                    mode=self.mode,perception_mode=self.perception_mode,detections=self.detections,
                    frame_request=self.frame_request,degraded=self.degraded,scanning=self.scan_remaining>0,holding=self.holding,last_frame=self.last_frame)


def compare(scene,scenario,perception='analytic-development'):
    if perception!='analytic-development':raise ValueError('Headless comparison supports the analytic fixture detector only')
    reports={}
    for mode in ('baseline','adaptive'):
        sim=Simulation(scene,scenario,mode,perception);sim.status='running'
        while sim.status=='running':sim.tick()
        reports[mode]=dict(status=sim.status,time=round(sim.time,2),**{k:(round(v,3) if isinstance(v,float) else v) for k,v in sim.metrics.items()})
    return dict(seed=scenario['seed'],scene_id=scene.id,perception=perception,reports=reports,
                note='Deterministic policy comparison with the labelled analytic fixture detector; hosted agent excluded. Illustrative exposure units; no guaranteed adaptive improvement.')
