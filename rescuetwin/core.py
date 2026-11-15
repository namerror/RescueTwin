"""Deterministic development simulator. No calibrated fire or physical robotics claims.

All tunables are metric so a 0.5 m synthetic grid and a 0.25 m scanned grid behave alike.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from pathlib import Path
import heapq
import math
import random
from copy import deepcopy

import os

import numpy as np

from . import occupancy, risk

Cell = tuple[int, int]

ROBOT_SPEED = 0.5            # m/s along the grid
# The robot is modelled as fire-proof: heat and smoke never block it on its own. Crossing a hazard cell it
# moves at its top speed; risk-aware routing still prefers the path with the least observed fire.
ROBOT_DASH_SPEED = 1.0       # m/s while the robot (or an escort) is in or entering a hazard cell
HAZARD_HEAT, HAZARD_SMOKE = 0.3, 0.5   # a cell counts as "in fire/dense smoke" at or above these
SPRAY_RADIUS_M = 1.5
SPRAY_COOLING_RATE = 2.5  # per mission second at the nozzle
SPRAY_FIRE_TRIGGER = .12
ESCORT_HEAT_LIMIT, ESCORT_SMOKE_LIMIT = .6, .5   # people are not fire-proof
# Robot decision constitution (docs/ROBOT_CONSTITUTION.md): human escort limits are never relaxed, by timeout
# or otherwise. A cornered escort holds, reports the person's location and requests assistance.
# Demo pacing: the hazard model advances at this fraction of mission time (1.0 = teammate model as-is).
HAZARD_TIME_SCALE = float(os.environ.get('RESCUETWIN_HAZARD_TIME_SCALE', '0.6'))
SEARCH_PRIOR_SMOOTHING_M = 3.0   # travel added to every candidate so likelihood, not just proximity, matters
SENSE_RANGE_M = 3.5          # analytic (fixture) sensing radius
CAMERA_RANGE_M = 4.0         # rendered depth range used for image perception
CAMERA_FOV = math.radians(90)
CAMERA_HEIGHT_M = 1.0
SCAN_RATE = math.radians(90)  # rad/s rotate-in-place at room observation points (image mode)
PERCEPTION_PERIOD = 0.4      # s of simulated time between rendered frames (image mode)
# Fire model from upstream PR #1 (floor2-hazard-preview), illustrative, not calibrated fire behaviour.
# Upstream's coefficients are per 0.5 m cell; diffusion is rescaled by (0.5 / resolution)^2 so the model
# behaves the same in metres on any grid (identical to upstream at 0.5 m).
UPSTREAM_IGNITION_HEAT = 0.9
HAZARD_SENSOR_RADIUS_M = 1.0
# Ignition threshold, burn rate, fuel load, smoke yield.
MATERIALS = {
    'inert': (1., 0., 0., 0.),
    'light': (.24, 1.05, .65, .48),
    'wood': (.31, .85, 1., .40),
    'upholstery': (.19, 1.25, 1.15, .72),
}
HAZARD_PREVIEW_LIMIT_S = 600.0
FLAME_VISIBLE_HEAT = 0.3           # flames are drawn and visible to sensing at or above this heat
IGNITION_RADIUS_M = 0.3
DEDUP_M = 0.75               # detections closer than this are the same potential survivor
FATAL_HEAT_DOSE = 12.0       # normalized heat-seconds above the escort heat limit (demo)
PAIR_M = 0.75                # robot must reach this close to start an escort
BELIEF_STALE_S = 60.0        # older hazard readings keep their risk penalty but stop being hard planning blocks
STALE_HEAT_CAP, STALE_SMOKE_CAP = .59, .49   # just under the strictest (escort) limits
CONFIRM_HITS = {'analytic-development': 1, 'image-yolo': 2}
# Constitution support (docs/ROBOT_CONSTITUTION.md).
ROOM_INSPECTED_FRACTION = 0.8    # share of a room's cells observed for "inspected"; less is "partly observed"
EXTRA_VIEWPOINTS = 2             # additional viewpoints tried in a partly observed room
CANDIDATE_RECHECK_S = 3.0        # an unconfirmed single-frame detection this old gets a second-viewpoint check
RECHECK_RANGE_M = (1.0, 3.0)     # second viewpoint: distance band from the detection, with line of sight
RECHECK_MIN_SHIFT_M = 1.0        # ... and at least this far from where it was first seen
WITHDRAW_MARGIN_S = 15.0         # leave for the exit when remaining time < estimated return time + margin
HANDOFF_RECHECK_S = 30.0         # a handed-off person who can follow is re-checked for a safe route this often


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
    default_material: str = "wood"
    materials: dict[Cell, str] = field(default_factory=dict)
    vents: set[Cell] = field(default_factory=set)

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
    """None if the cell is impassable for this move; otherwise its traversal cost (cells).

    The robot alone is fire-proof (no hazard limit). An escorted person always keeps human limits and only
    moves through observed cells."""
    if escort and (heat[y][x] >= ESCORT_HEAT_LIMIT or smoke[y][x] >= ESCORT_SMOKE_LIMIT): return None
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
        self.base_scene = self.scene
        default=dict(survivors=[[26,6],[15,18]],ignitions=[[25,18]],seed=7) if self.scene.id=='development-apartment' else dict(survivors=[],ignitions=[],seed=7)
        self.scenario=deepcopy(scenario or default)
        self.scenario.setdefault('scene_id',self.scene.id)
        if self.scenario['scene_id']!=self.scene.id: raise ValueError('Scenario belongs to a different scene')
        self.validate(self.scenario)
        self.incident = None
        self.incident_plan = None
        if room_id := self.scenario.get('incident_room'):
            room = next(room for room in self.scene.rooms if room['id'] == room_id)
            cells = sorted({tuple(cell) for cell in room['cells'] if self.scene.free(cell)})
            incident_rng = random.Random(self.scenario['seed'])
            self.incident_plan = (incident_rng.uniform(2., 6.), incident_rng.choice(cells), incident_rng)
        if perception not in CONFIRM_HITS: raise ValueError('Unknown perception mode')
        self.mode=mode;self.perception_mode=perception;self.time=0.;self.status='idle'
        self.robot=tuple(self.scene.robot_start or self.scene.exits[0]);self.heading=0.;self.progress=0.
        self.scan_remaining=0.
        self.free=self.scene.free_mask()
        self.diffusion_edges = (
            self.free[1:, :] & self.free[:-1, :],
            self.free[:, 1:] & self.free[:, :-1],
        )
        H,W=self.free.shape
        self.heat=np.zeros((H,W));self.smoke=np.zeros((H,W));self.fire=self.grid()
        profiles=np.empty((4,H,W))
        profiles[:]=np.array(MATERIALS[self.scene.default_material])[:,None,None]
        for (x,y),material in self.scene.materials.items():
            profiles[:,y,x]=MATERIALS[material]
        self.ignition_threshold,self.burn_rate,fuel,self.smoke_yield=profiles
        self.fuel=np.where(self.free,fuel,0.)
        exchange=self.grid()
        for x,y in set(map(tuple,self.scene.exits)) | self.scene.vents:
            exchange[y,x]=1.
        self.heat_decay=.04+.16*exchange
        self.smoke_decay=.015+.24*exchange
        self.ignition_cells=set();self.hazard_time=0.;self.hazards_running=False
        self.observed=np.zeros((H,W));self.belief_heat=np.zeros((H,W));self.belief_smoke=np.zeros((H,W));self.last_seen=np.full((H,W),-1.);self.hazard_seen=np.full((H,W),-1.)
        self.route=[];self.goal=None;self.visited=set();self.escort={}  # person ID to confirmed detection ID
        self.detections=[];self.next_detection=0;self.version=0
        self.events=[];self.metrics=dict(evacuated=0,exposure=0.,distance=0.,detections=0,false_detections=0,route_changes=0,rooms_inspected=0,robot_hazard_s=0.,assistance_requests=0,handoffs=0,possible_detections=0,reports=0)
        self.escort_blocked_since=None;self.assistance_requested=False
        self.searched_points = set()
        self.active_scan = None
        self.reports=[];self.decisions=[];self.decision_key=None;self.extra_views=[];self.withdraw_reason=None;self.comms_up=True
        self.immobile={tuple(p) for p in self.scenario.get('immobile',[])}
        self.agent_backoff_until=0.;self.metrics['inference_pause_s']=0.   # hosted-agent bookkeeping (decision-quality mode)
        self.survivors=[dict(id=f'person-{i}',position=tuple(p),status='unobserved') for i,p in enumerate(self.scenario['survivors'])]
        self.heat_dose = {person['id']: 0.0 for person in self.survivors}
        self.rng=random.Random(self.scenario['seed']);self.steps=0
        self.frame_request=None;self.frame_seq=0;self.degraded=False;self.last_frame=None;self.holding=False
        label='Synthetic fixture' if self.scene.source=='synthetic' else 'Scanned geometry with synthetic hazards and people'
        self.log(f"{label}; perception: {'analytic fixture (not image-based)' if perception=='analytic-development' else 'YOLO on rendered RGB-D'}.")
        # Search prior from static building information only (never from survivor placements).
        self.prior=occupancy.weights(self.scene,self.scenario.get('building_use'))
        self.log(occupancy.describe(self.scene,self.prior))
        if HAZARD_TIME_SCALE!=1:self.log(f'Demo pacing: fire and smoke advance at {HAZARD_TIME_SCALE:g}x mission time.')
        self.rooms_state={r['id']:dict(id=r['id'],name=r['name'],label=r.get('label'),status='unvisited',observed_fraction=0.,
                                        viewpoints_tried=0,reason=None) for r in self.scene.rooms}
        self.room_of={tuple(c):r['id'] for r in self.scene.rooms for c in r['cells']}
        radius = IGNITION_RADIUS_M / self.scene.resolution
        reach = math.floor(radius + 1e-9)
        for x, y in self.scenario['ignitions']:
            for yy in range(max(0, y - reach), min(H, y + reach + 1)):
                for xx in range(max(0, x - reach), min(W, x + reach + 1)):
                    if math.hypot(xx - x, yy - y) <= radius + 1e-9 and line_visible(self.scene, (x, y), (xx, yy)):
                        self.heat[yy, xx] = UPSTREAM_IGNITION_HEAT
                        self.ignition_cells.add((xx, yy))
        self.ignition_mask=np.zeros((H,W),bool)
        for xx,yy in self.ignition_cells:self.ignition_mask[yy,xx]=True
        self.fire=np.where(self.ignition_mask,self.heat,0.)
        self.ignition_strength = self.fire.copy()
        self.sprinkler_heads = []
        self.sprinkler_coverage = []
        self.active_sprinklers = set()
        self.spray_strength = self.grid()
        if (self.scene.bundle is not None and self.scene.source != 'synthetic'
                and risk.recorded_sprinklers(self.scene) is True):
            self.sprinkler_heads = self.place_sprinklers()
            self.sprinkler_coverage = [self.sprinkler_reach(head) for head in self.sprinkler_heads]

        sprinkler_rooms = {self.room_of[head] for head in self.sprinkler_heads}
        self.risk = risk.room_map(self.scene, self.scenario.get('room_risk'), sprinkler_rooms)
        self.log(risk.describe(self.scene, self.risk))

    def grid(self,value=0.): return np.full((self.scene.height,self.scene.width),float(value))

    def validate(self,scenario):
        room_id = scenario.get('incident_room')
        if room_id is not None and not any(
            room['id'] == room_id and any(self.scene.free(cell) for cell in room['cells'])
            for room in self.scene.rooms
        ):
            raise ValueError('Incident room must contain a walkable cell in this scene')
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
        if scenario.get('building_use') is not None and scenario['building_use'] not in occupancy.PROFILES: raise ValueError('Unknown building use')
        risk.validate(self.scene,scenario.get('room_risk'))
        from .humans import validate as validate_human_variant
        validate_human_variant(scenario.get('human_variant'))
        immobile=scenario.get('immobile',[])
        if not isinstance(immobile,list) or not {tuple(p) for p in immobile if isinstance(p,(list,tuple))}<=set(map(tuple,scenario['survivors'])) or len(immobile)!=len({tuple(p) for p in immobile}):
            raise ValueError('immobile: list distinct cells that are also survivor placements')
        outages=scenario.get('comms_outages',[])
        if not isinstance(outages,list) or len(outages)>10 or any(not isinstance(o,(list,tuple)) or len(o)!=2 or not all(isinstance(v,(int,float)) for v in o) or not 0<=o[0]<o[1] for o in outages):
            raise ValueError('comms_outages: up to 10 [start, end] second intervals with 0 <= start < end')

    def log(self,message):
        self.events.append(dict(time=round(self.time,2),message=message));self.events=self.events[-100:]

    # ---------------------------------------------------------------- hazards
    def advance_incident(self):
        if self.incident_plan is None or self.incident is not None:
            return
        scheduled, origin, rng = self.incident_plan
        if self.time < scheduled:
            return
        neighbours = sorted(self.scene.neighbors(origin))
        rng.shuffle(neighbours)
        exterior = set(map(tuple, self.scene.meta.get('exterior', ())))
        # Open a grid passage where a short run of wall cells separates indoor floor.
        candidates = []
        for x, y in sorted(self.scene.walls - exterior):
            distance = abs(x - origin[0]) + abs(y - origin[1])
            if distance > 9:
                continue
            for dx, dy in ((1, 0), (0, 1)):
                if not self.scene.free((x - dx, y - dy)):
                    continue
                strip = []
                for offset in range(4):
                    cell = (x + dx * offset, y + dy * offset)
                    if cell in exterior:
                        break
                    if self.scene.free(cell):
                        candidates.append((distance, (x, y), (dy, dx), strip))
                        break
                    if cell not in self.scene.walls:
                        break
                    strip.append(cell)
        breached = set()
        if candidates:
            _, (x, y), tangent, strip = min(candidates)
            tx, ty = tangent
            breached.update(strip)
            for _, cell, direction, adjacent in candidates:
                if direction == tangent and cell in {(x - tx, y - ty), (x + tx, y + ty)}:
                    breached.update(adjacent)
        occupied = {self.robot, *map(tuple, self.scene.exits),
                    *(person['position'] for person in self.survivors)}
        debris = next((cell for cell in neighbours if cell not in occupied), None)
        self.scene = replace(self.scene, walls=(self.scene.walls - breached) | ({debris} if debris else set()))
        for x, y in breached:
            self.free[y, x] = True
        if debris is not None:
            x, y = debris
            self.free[y, x] = False
            self.heat[y, x] = self.smoke[y, x] = self.fuel[y, x] = 0.
            self.fire[y, x] = 0.
            self.ignition_strength[y, x] = 0.
        self.diffusion_edges = (
            self.free[1:, :] & self.free[:-1, :],
            self.free[:, 1:] & self.free[:, :-1],
        )
        self.sprinkler_coverage = [self.sprinkler_reach(origin) for origin in self.sprinkler_heads]
        affected = {origin, *neighbours}
        for wall_cell in breached:
            affected.update(self.scene.neighbors(wall_cell))
        if debris:
            affected.discard(debris)
        for x, y in affected:
            self.ignition_cells.add((x, y))
            self.heat[y, x] = max(self.heat[y, x], UPSTREAM_IGNITION_HEAT)
            self.fire[y, x] = max(self.fire[y, x], UPSTREAM_IGNITION_HEAT)
            self.smoke[y, x] = max(self.smoke[y, x], .35)
        injured = 0
        for person in self.survivors:
            if person['status'] in ('evacuated', 'casualty') or person['position'] not in affected:
                continue
            person['injured'] = True
            injured += 1
            if person['position'] == origin:
                self.mark_casualty(person)
        self.incident = dict(origin=origin, debris=debris, breached=sorted(breached), time=round(self.time, 2))
        self.log(f'Kitchen incident: {len(breached)} wall cells opened, {injured} people affected.')

    def place_sprinklers(self):
        """Choose a single interior mounting point for each suitable scanned room."""
        clearance = .5 / self.scene.resolution
        reach = math.floor(clearance)
        offsets = [(dx, dy) for dx in range(-reach, reach + 1)
                   for dy in range(-reach, reach + 1)
                   if math.hypot(dx, dy) <= clearance]
        heads = []
        for room in self.scene.rooms:
            if room.get('label') in {'outdoor_other', 'balcony', 'patio', 'garage'}:
                continue
            cells = {tuple(cell) for cell in room['cells'] if self.scene.free(cell)}
            interior = [(x, y) for x, y in cells
                        if all((x + dx, y + dy) in cells for dx, dy in offsets)]
            if interior:
                center = tuple(sum(cell[axis] for cell in cells) / len(cells) for axis in (0, 1))
                head = min(interior, key=lambda cell: (math.dist(cell, center), cell))
                if head not in heads:
                    heads.append(head)
        return heads

    def sprinkler_reach(self, origin):
        cx, cy = origin
        reach = math.ceil(SPRAY_RADIUS_M / self.scene.resolution)
        rows, cols, weights = [], [], []
        for y in range(max(0, cy - reach), min(self.scene.height, cy + reach + 1)):
            for x in range(max(0, cx - reach), min(self.scene.width, cx + reach + 1)):
                distance = math.hypot(x - cx, y - cy) * self.scene.resolution
                if distance <= SPRAY_RADIUS_M and line_visible(self.scene, origin, (x, y)):
                    rows.append(y)
                    cols.append(x)
                    weights.append(1. - .5 * distance / SPRAY_RADIUS_M)
        return rows, cols, weights

    def update_sprinklers(self):
        self.spray_strength.fill(0.)
        active = set()
        if self.sprinkler_heads and (self.status == 'running' or self.hazards_running):
            hot = (self.heat >= HAZARD_HEAT) | (self.fire >= SPRAY_FIRE_TRIGGER)
            for i, (rows, cols, weights) in enumerate(self.sprinkler_coverage):
                if hot[rows, cols].any():
                    active.add(i)
                    self.spray_strength[rows, cols] = np.maximum(self.spray_strength[rows, cols], weights)
        if active - self.active_sprinklers:
            self.log('Automatic scene sprinkler activated.')
        self.active_sprinklers = active
        return bool(active)

    def hazards(self,dt):
        """Advance finite fuel, wall-confined diffusion and ambient exchange."""
        spraying = self.update_sprinklers()
        dt = dt * HAZARD_TIME_SCALE   # demo pacing; see HAZARD_TIME_SCALE
        scale = (.2 / self.scene.resolution) ** 2
        kh, ks = .30 * scale, .50 * scale
        steps = max(1, math.ceil(dt * (4 * max(kh, ks) + .255) / .8))
        step = dt / steps
        cooling = 1.
        if spraying:
            cooling = np.exp(-SPRAY_COOLING_RATE * self.spray_strength * step / HAZARD_TIME_SCALE)
        for _ in range(steps):
            self.hazard_time += step
            heat = self.heat * cooling if spraying else self.heat
            smoke = self.smoke
            if spraying:
                self.ignition_strength *= cooling
            source = self.ignition_strength if self.hazard_time <= 12 + 1e-8 else 0.
            burn = np.minimum(self.fuel / step,
                              .2 * self.burn_rate * np.maximum(0., heat - self.ignition_threshold) * self.fuel)
            self.fuel = np.maximum(0., self.fuel - burn * step)
            release = 5 * burn
            self.fire = np.maximum(source, np.clip(release, 0., 1.))
            self.heat = np.maximum(source, np.clip(heat + step * (
                kh * self._laplacian(heat) + release - self.heat_decay * heat), 0., 1.))
            self.smoke = np.clip(smoke + step * (
                ks * self._laplacian(smoke) + release * self.smoke_yield - self.smoke_decay * smoke), 0., 1.)

    def _laplacian(self,field):
        vertical, horizontal = self.diffusion_edges
        flux_y = (field[:-1, :] - field[1:, :]) * vertical
        flux_x = (field[:, :-1] - field[:, 1:]) * horizontal
        out = np.zeros_like(field)
        out[1:, :] += flux_y
        out[:-1, :] -= flux_y
        out[:, 1:] += flux_x
        out[:, :-1] -= flux_x
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

    def add_evidence(self,position,confidence,source,matched=()):
        """Merge a world-frame person observation into belief; returns the detection entry."""
        position=(float(position[0]),float(position[1]))
        cell=self.scene.cell(*position)
        if not self.scene.free(cell):
            near=[(abs(dx)+abs(dy),(cell[0]+dx,cell[1]+dy)) for dx in range(-2,3) for dy in range(-2,3) if self.scene.free((cell[0]+dx,cell[1]+dy))]
            if not near: return None
            cell=min(near)[1]
        # Associate each observation with the nearest track. A frame may update
        # each track once, even when two people stand within the dedup radius.
        # 'possible' tracks (reported, unconfirmed) can still be confirmed by later evidence.
        radius = 1e-6 if source == 'analytic-fixture' else DEDUP_M
        candidates = [d for d in self.detections
                      if d['id'] not in matched and d['status'] in ('confirmed', 'candidate', 'possible')
                      and math.dist(d['position'], position) <= radius]
        if candidates:
            d = min(candidates, key=lambda entry: math.dist(entry['position'], position))
            n = d['hits']
            d['position'] = [(old * n + new) / (n + 1) for old, new in zip(d['position'], position)]
            d['hits'] += 1
            d['confidence'] = max(d['confidence'], confidence)
            d['last_seen'] = self.time
            d['cell'] = list(self.nearest_free(self.scene.cell(*d['position'])))
            if d['status'] in ('candidate', 'possible') and d['hits'] >= CONFIRM_HITS[self.perception_mode]:
                self.confirm(d)
            return d
        d=dict(id=f'det-{self.next_detection}',position=list(position),cell=list(cell),confidence=confidence,hits=1,
               first_seen=self.time,last_seen=self.time,status='candidate',source=source,seen_from=list(self.robot))
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
        self.report('confirmed-person',tuple(d['cell']),confirmed=1,mobility='unknown until contact',
                    action='approaching for contact and a mobility check',detection=d['id'])

    # ---------------------------------------------------------------- reports (outside team)
    def floor_label(self):
        return self.scene.name.split('·')[-1].strip() if '·' in self.scene.name else self.scene.name

    def report(self,kind,cell,action,confirmed=0,possible=0,mobility=None,detection=None,**extra):
        """Queue an outside-team report. Delivery and acknowledgement are tracked; nothing is dropped."""
        cell=tuple(cell);x,y=self.scene.world(cell);room=self.room_of.get(cell)
        r=dict(id=f'rep-{len(self.reports)}',kind=kind,time=round(self.time,2),floor=self.floor_label(),
               room=(self.rooms_state.get(room) or {}).get('name'),cell=list(cell),position=[round(x,2),round(y,2)],
               confirmed=confirmed,possible=possible,mobility=mobility,detection=detection,
               hazards=dict(heat=round(float(self.belief_heat[cell[1],cell[0]]),2),smoke=round(float(self.belief_smoke[cell[1],cell[0]]),2)),
               last_usable_approach=list(self.robot),action=action,status='queued',attempts=0,delivered_at=None,acknowledged_at=None,**extra)
        self.reports.append(r);self.metrics['reports']+=1;self.version+=1
        self.log(f"Report queued for outside team: {kind} at {r['room'] or 'cell '+str(list(cell))}; {action}.")
        self.deliver_reports()
        return r

    def deliver_reports(self):
        up=not any(a<=self.time<b for a,b in self.scenario.get('comms_outages',[]))
        if up!=self.comms_up:
            self.comms_up=up;self.log('Communications restored; retrying queued reports.' if up else 'Communications lost; reports stay queued for retry.')
        for r in self.reports:
            if r['status']!='queued':continue
            r['attempts']+=1
            if up:r['status']='delivered';r['delivered_at']=round(self.time,2)

    def acknowledge(self,report_id):
        r=next((r for r in self.reports if r['id']==report_id),None)
        if r is None:raise ValueError('Unknown report')
        if r['status']!='delivered':raise ValueError('Only delivered reports can be acknowledged')
        r['status']='acknowledged';r['acknowledged_at']=round(self.time,2);self.version+=1
        self.log(f"Outside team acknowledged {r['id']} ({r['kind']}).")
        return r

    # ---------------------------------------------------------------- decision record
    def decide(self,kind,target,options,reason,trigger):
        """Record a consequential decision once, when the chosen target changes."""
        key=(kind,tuple(target) if target is not None else None)
        if key==self.decision_key:return
        self.decision_key=key
        self.decisions.append(dict(time=round(self.time,2),decision=kind,target=list(target) if target is not None else None,
                                   trigger=trigger,options=[dict(target=list(t),cost=round(float(c),1)) for c,t in sorted(options)[:3]],reason=reason))
        self.decisions=self.decisions[-200:]

    # ---------------------------------------------------------------- room accounting
    def update_rooms(self):
        for room in self.scene.rooms:
            st=self.rooms_state[room['id']]
            cells=room['cells'];seen=sum(self.observed[y,x]>0 for x,y in cells)
            st['observed_fraction']=round(seen/max(1,len(cells)),3)
            if st['status'] in ('inspected','inaccessible'):continue
            planned=[p for p in self.observe_points(room)]+[c for rid,c in self.extra_views if rid==room['id']]
            if not any(p in self.visited for p in planned):continue
            if st['observed_fraction']>=ROOM_INSPECTED_FRACTION:
                st['status']='inspected';st['reason']=None;self.log(f"Room inspected: {room['name']} ({st['observed_fraction']:.0%} observed).");continue
            if all(p in self.visited for p in planned) and self.scan_remaining<=0:
                if st['viewpoints_tried']<EXTRA_VIEWPOINTS and (view:=self.viewpoint_for_room(room)) is not None:
                    st['viewpoints_tried']+=1;self.extra_views.append((room['id'],view))
                    self.log(f"{room['name']} only {st['observed_fraction']:.0%} observed; trying another viewpoint.")
                elif st['status']!='partly-observed':
                    hidden=[(x,y) for x,y in cells if not self.observed[y,x]]
                    smoky=sum(self.belief_smoke[y,x]>=.5 for x,y in hidden)>len(hidden)/3
                    st['status']='partly-observed';st['reason']='smoke limited visibility' if smoky else 'occlusion or obstructions limited visibility'
                    self.log(f"Room partly observed: {room['name']} ({st['observed_fraction']:.0%}); {st['reason']}. Kept on the outstanding list.")

    def viewpoint_for_room(self,room):
        """Reachable, unvisited room cell that sees the most still-unobserved room cells."""
        costs,_=self.tree()
        hidden=[(x,y) for x,y in room['cells'] if not self.observed[y,x]]
        if not hidden:return None
        step=max(1,len(hidden)//60);hidden=hidden[::step]
        options=[tuple(c) for c in room['cells'] if tuple(c) in costs and tuple(c) not in self.visited]
        options=options[::max(1,len(options)//40)]
        reach=CAMERA_RANGE_M if self.perception_mode=='image-yolo' else SENSE_RANGE_M
        best=None
        for c in options:
            gain=sum(1 for h in hidden if math.dist(self.scene.world(c),self.scene.world(h))<=reach and line_visible(self.scene,c,h))
            if gain and (best is None or (gain,-costs[c])>best[0]):best=((gain,-costs[c]),c)
        return best[1] if best else None

    def mark_inaccessible(self,costs):
        for room in self.scene.rooms:
            st=self.rooms_state[room['id']]
            if st['status']=='unvisited' and not any(p in costs for p in self.observe_points(room)):
                st['status']='inaccessible';st['reason']='no traversable route to any observation point'
                self.log(f"Room inaccessible: {room['name']}; kept on the outstanding list.")

    # ---------------------------------------------------------------- second viewpoint for uncertain detections
    def recheck_target(self,costs):
        for d in self.detections:
            if d['status']!='candidate' or self.time-d['last_seen']<CANDIDATE_RECHECK_S:continue
            if d.get('recheck_cell') is None:
                origin=self.scene.world(tuple(d.get('seen_from',self.robot)));where=tuple(d['cell'])
                options=[]
                for c,cost in costs.items():
                    dist=math.dist(self.scene.world(c),d['position'])
                    if RECHECK_RANGE_M[0]<=dist<=RECHECK_RANGE_M[1] and math.dist(self.scene.world(c),origin)>=RECHECK_MIN_SHIFT_M and line_visible(self.scene,c,where):
                        options.append((cost,c))
                if not options:
                    self.possible(d,'no reachable second viewpoint');continue
                d['recheck_cell']=list(min(options)[1])
                self.log(f"Checking uncertain detection {d['id']} from a second viewpoint.")
            if tuple(d['recheck_cell']) in costs:return d,tuple(d['recheck_cell'])
            self.possible(d,'second viewpoint no longer reachable')
        return None,None

    def possible(self,d,reason):
        d['status']='possible';self.metrics['possible_detections']+=1;self.version+=1
        self.log(f"Possible detection {d['id']} not confirmed ({reason}); reported as possible.")
        self.report('possible-detection',tuple(d['cell']),possible=1,mobility='unknown',detection=d['id'],
                    action=f'not confirmed: {reason}; continuing search')

    def hazard_sensor(self):
        rx, ry = self.robot
        readings = []
        for x, y in self.ignition_cells:
            distance = math.hypot(x - rx, y - ry) * self.scene.resolution
            intensity = float(self.heat[y, x])
            if distance <= HAZARD_SENSOR_RADIUS_M and intensity > 0 and line_visible(self.scene, self.robot, (x, y)):
                readings.append((distance, -intensity))
        nearest = min(readings, default=None)
        return dict(position=self.robot, time_s=round(self.time, 2),
                    radius_m=HAZARD_SENSOR_RADIUS_M,
                    source_distance_m=nearest[0] if nearest else None,
                    source_heat_intensity=-nearest[1] if nearest else None,
                    smoke_intensity=float(self.smoke[ry, rx]))

    def observations(self):
        return dict(time=self.time,version=self.version,robot=self.robot,heading=self.heading,observed=self.observed.tolist(),
                    heat=self.belief_heat.round(3).tolist(),smoke=self.belief_smoke.round(3).tolist(),
                    hazard_sensor=self.hazard_sensor(),
                    searched_rooms=self.searched_rooms(),
                    survivors=[deepcopy(d) for d in self.detections if d['status'] in ('confirmed','escorting')],
                    possible=[deepcopy(d) for d in self.detections if d['status'] in ('candidate','possible')],
                    rooms_state=deepcopy(list(self.rooms_state.values())),
                    risk_map=deepcopy(list(self.risk.values())),
                    reports=[{k:r[k] for k in ('id','kind','status','time','room','action')} for r in self.reports],
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

    def search_weight(self,room_id):
        """Search priority of a room: building-use likelihood times the fire-risk fixture factor."""
        return self.prior['weights'].get(room_id,1.)*(self.risk.get(room_id) or {}).get('factor',1.)

    def observe_points(self,room):
        return [tuple(p) for p in room.get('observe',room['cells'])]

    def searched_rooms(self):
        return [room['id'] for room in self.scene.rooms
                if (points := self.observe_points(room)) and all(p in self.searched_points for p in points)]

    def choose(self):
        if self.escort:
            costs,prev=self.tree(True)
            options=[(costs[e],e) for e in self.scene.exits if e in costs]
            if options:
                self.escort_blocked_since=None
                return extract(prev,self.robot,min(options)[1])
            if self.escort_blocked_since is None:self.escort_blocked_since=self.time
            if not self.assistance_requested:
                # Constitution: no route satisfies escort limits -> report location and request assistance.
                # Limits are never relaxed; the robot keeps holding with the person and replans as conditions change.
                self.assistance_requested=True;self.metrics['assistance_requests']+=1
                x,y=self.scene.world(self.robot);d=', '.join(self.escort.values())
                self.log(f"Assistance requested: {d} at ({x:.1f}, {y:.1f}) m, cell {list(self.robot)}; "
                         f"no route to a verified exit within human escort limits. Holding with the person and replanning.")
            self.hold('Escort paused: no observed viable egress route; holding until conditions change.');return []
        costs,prev=self.tree()
        exits=[(costs[e],e) for e in self.scene.exits if e in costs]
        if not exits:
            self.stop('loss of an eligible return route');return []
        if self.withdraw_reason is None and self.scene.time_limit-self.time<min(exits)[0]*self.scene.resolution/ROBOT_SPEED+WITHDRAW_MARGIN_S:
            self.withdraw('controller time limit: leaving while a return route remains')
        if self.withdraw_reason is not None:
            self.decide('withdraw',min(exits)[1],exits,self.withdraw_reason,'withdrawal')
            return extract(prev,self.robot,min(exits)[1])
        if self.goal is not None:
            if self.robot==self.goal or self.goal in self.searched_points:self.goal=None
            else:
                selected=self.path(self.goal)
                if selected:return selected
                self.goal=None
        self.mark_inaccessible(costs)
        targets=[tuple(d['cell']) for d in self.detections if d['status']=='confirmed']
        options=[(costs[t],t) for t in targets if t in costs]
        if options:
            self.decide('approach-person',min(options)[1],options,'confirmed person: contact and mobility check before search continues','confirmed detection')
            return extract(prev,self.robot,min(options)[1])
        d,view=self.recheck_target(costs)
        if view is not None:
            if view==self.robot:return [self.robot]
            self.decide('second-viewpoint',view,[(costs[view],view)],f"uncertain detection {d['id']}: check from another viewpoint",'single-frame detection')
            return extract(prev,self.robot,view)
        # Coverage targets are static geometry, not hidden survivor locations.
        # Prior-weighted coverage: likelihood of people in the room over travel cost. With a uniform prior
        # (residential) this is nearest-first.
        smooth=SEARCH_PRIOR_SMOOTHING_M/self.scene.resolution
        remaining=[(self.search_weight(room['id']),p) for room in self.scene.rooms for p in self.observe_points(room) if p not in self.searched_points]
        remaining+=[(self.search_weight(rid),c) for rid,c in self.extra_views if c not in self.visited]
        options=[(-w/(costs[p]+smooth),costs[p],p) for w,p in remaining if p in costs]
        if options:
            best=min(options);room=self.room_of.get(best[2])
            self.decide('inspect-room',best[2],[(c,p) for _,c,p in options],
                        f"{(self.rooms_state.get(room) or {}).get('name','room')}: best likelihood/travel ({self.prior['use']} prior, risk {(self.risk.get(room) or {}).get('tag','unknown')})",'coverage')
            return extract(prev,self.robot,best[2])
        remaining=[p for _,p in remaining]
        retry=[(costs[tuple(d['cell'])],tuple(d['cell'])) for d in self.detections
               if d['status']=='handoff' and d.get('retry') and tuple(d['cell']) in costs]
        if retry and not options:
            due=[(c,t) for (c,t),d in zip(retry,[d for d in self.detections if d['status']=='handoff' and d.get('retry') and tuple(d['cell']) in costs]) if self.time>=d.get('retry_after',0)]
            if due:
                self.decide('recheck-handoff',min(due)[1],due,'search done; re-check whether a handed-off person now has a human-safe route','handoff pending')
                return extract(prev,self.robot,min(due)[1])
            self.hold('Search complete; waiting to re-check handed-off people for a human-safe route.');return []
        if targets or remaining:
            self.hold('No viable route to remaining targets; holding until observed hazards change.')
        return []

    def hold(self,message):
        """Targets exist but are currently unreachable. Fire burns out in the upstream model, so wait
        (the mission time limit still applies) instead of declaring the mission unresolved at once."""
        if not getattr(self,'was_holding',False):self.log(message)
        self.holding=True

    def withdraw(self,reason):
        if self.withdraw_reason is None:
            self.withdraw_reason=reason;self.goal=None;self.log(f'Withdrawing to the exit: {reason}.')

    def stop(self,reason):
        self.status='stopped';self.withdraw_reason=self.withdraw_reason or reason;self.log(f'Mission stopped: {reason}.');self.end_report(reason)

    def end_report(self,reason):
        """Best available accounting for each person and room, plus anything not yet delivered."""
        unresolved=[d['id'] for d in self.detections if d['status'] in ('candidate','possible','confirmed','escorting','handoff')]
        casualties=[d['id'] for d in self.detections if d['status']=='casualty']
        outstanding=[st['name'] for st in self.rooms_state.values() if st['status']!='inspected']
        unsent=sum(r['status']=='queued' for r in self.reports)
        self.report('mission-end',self.robot,action=f'mission ended: {reason}',
                    unresolved_detections=unresolved,casualties=casualties,outstanding_rooms=outstanding,unsent_reports=unsent,
                    evacuated=self.metrics['evacuated'],handoffs=self.metrics['handoffs'])

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
        self.advance_incident()
        if self.scan_remaining>0:
            turn=min(self.scan_remaining,SCAN_RATE*dt);self.heading=(self.heading+turn)%(2*math.pi);self.scan_remaining-=turn
            if self.scan_remaining<=1e-9:self.scan_done()
        self.sense()
        self.deliver_reports()
        for d in self.detections:
            if d['status']=='candidate' and d.get('recheck_cell')==list(self.robot) and not d.get('rechecking') and self.scan_remaining<=0:
                d['rechecking']=True;self.scan_remaining=2*math.pi if self.perception_mode=='image-yolo' else 0.
                if self.scan_remaining<=0:self.scan_done()
        self.visited.add(self.robot)
        if not self.escort and self.active_scan is None and self.robot not in self.searched_points:
            if any(self.robot in self.observe_points(room) for room in self.scene.rooms):
                self.active_scan = self.robot
                if self.perception_mode == 'image-yolo':
                    self.scan_remaining = 2 * math.pi
                    self.log('Scanning room from observation point.')
                else:
                    self.scan_done()
        self.pair()
        self.update_rooms()
        if self.status!='running':return
        if self.withdraw_reason and self.robot in self.scene.exits and not self.escort:
            self.status='withdrawn';self.log(f'Withdrawn at the exit: {self.withdraw_reason}.');self.end_report(self.withdraw_reason);return
        moving=self.scan_remaining<=0
        self.was_holding,self.holding=self.holding,False
        new=self.choose() if moving else [self.robot]
        if moving and self.was_holding and not self.holding and new:self.log('Route available again; resuming.')
        if not moving:self.holding=self.was_holding
        if moving and new and self.route and new[1:3]!=self.route[:2]:
            self.metrics['route_changes']+=1;self.log('Route updated from observed evidence.')
        self.route=new[1:]
        here_hot=self.heat[self.robot[1],self.robot[0]]>=HAZARD_HEAT or self.smoke[self.robot[1],self.robot[0]]>=HAZARD_SMOKE
        if here_hot:self.metrics['robot_hazard_s']+=dt
        ahead=self.route[0] if self.route else None
        ahead_hot=ahead is not None and (self.heat[ahead[1],ahead[0]]>=HAZARD_HEAT or self.smoke[ahead[1],ahead[0]]>=HAZARD_SMOKE)
        # Local measurement of its own and the next cell: cross hazards at top speed.
        speed=ROBOT_DASH_SPEED if (here_hot or ahead_hot) else ROBOT_SPEED
        self.progress+=speed*dt if moving and self.route else 0
        if self.progress>=self.scene.resolution-1e-9 and self.route:
            self.progress-=self.scene.resolution
            q=tuple(self.route[0]);x,y=q
            # Independent immediate interlock, never global planner input. The robot alone is fire-proof;
            # it protects an escorted person unless the escort is cornered (last resort).
            guard=bool(self.escort)
            if guard and (self.heat[y,x]>=ESCORT_HEAT_LIMIT or self.smoke[y,x]>=ESCORT_SMOKE_LIMIT):
                self.belief_heat[y,x]=self.heat[y,x];self.belief_smoke[y,x]=self.smoke[y,x];self.hazard_seen[y,x]=self.time
                self.observed[y,x]=1;self.progress=0;self.log('Local interlock stopped motion into hazard.')
            else:
                self.heading=math.atan2(y-self.robot[1],x-self.robot[0])
                self.robot=q;self.route=self.route[1:];self.metrics['distance']+=self.scene.resolution
                if self.escort:self.assistance_requested=False   # moving again: a later block is a new request
                if self.escort:
                    at_exit = q in self.scene.exits
                    for person in self.survivors:
                        detection_id = self.escort.get(person['id'])
                        if detection_id is None:
                            continue
                        person['position'] = q
                        if at_exit:
                            person['status'] = 'evacuated'
                            self.metrics['evacuated'] += 1
                            detection = next(d for d in self.detections if d['id'] == detection_id)
                            detection['status'] = 'evacuated'
                            self.log(f"Evacuated: {detection_id}")
                            self.version += 1
                    if at_exit:
                        self.escort.clear()
                        self.escort_blocked_since = None
                        self.assistance_requested = False
        elif not self.route:self.progress=0
        self.update_casualties(dt)
        if self.status!='running':return
        if self.survivors and self.metrics['evacuated']==len(self.survivors):
            self.status='completed';self.log('All configured survivors evacuated (simulator evaluation).');self.end_report('all configured people evacuated')
        elif self.survivors and all(p['status'] in ('evacuated','casualty') for p in self.survivors):
            self.status='unresolved';self.log('Mission ended with simulated casualties.')
            self.end_report('every configured person evacuated or a simulated casualty')
        elif (self.survivors and all(p['status'] in ('evacuated','assistance-requested','casualty') for p in self.survivors) and not self.escort
              and all(st['status']!='unvisited' for st in self.rooms_state.values())
              and not any(d['status']=='handoff' and d.get('retry') for d in self.detections)):
            self.status='accounted';self.log('All configured people evacuated or handed off to responders (simulator evaluation).')
            self.end_report('every configured person evacuated or handed off')
        elif (not new and self.scan_remaining<=0 and not self.holding) or self.time>=self.scene.time_limit:
            self.status='unresolved';self.log('Mission unresolved; unseen occupants are not confirmed absent.')
            self.end_report('time limit reached' if self.time>=self.scene.time_limit else 'no further reachable targets')
        if self.status=='running' and self.perception_mode=='image-yolo' and int(round(self.time/dt))%max(1,round(PERCEPTION_PERIOD/dt))==0:
            self.request_frame()

    def update_casualties(self, dt):
        for person in self.survivors:
            if person['status'] in ('evacuated', 'casualty'):
                continue
            x, y = person['position']
            heat, smoke = self.heat[y, x], self.smoke[y, x]
            self.metrics['exposure'] += float(dt * (heat + smoke))
            person_id = person['id']
            self.heat_dose[person_id] += float(dt * max(0., heat - ESCORT_HEAT_LIMIT))
            if self.heat_dose[person_id] < FATAL_HEAT_DOSE:
                continue
            self.mark_casualty(person)

    def mark_casualty(self, person):
        person['status'] = 'casualty'
        detection_id = self.escort.pop(person['id'], None)
        if detection_id is not None:
            next(d for d in self.detections if d['id'] == detection_id)['status'] = 'casualty'
            self.version += 1
            if not self.escort:
                self.escort_blocked_since = None
                self.assistance_requested = False

    def scan_done(self):
        self.scan_remaining = 0.
        rechecks=[d for d in self.detections if d.get('rechecking') and d['status']=='candidate']
        for d in rechecks:self.possible(d,'not confirmed from a second viewpoint')
        for d in self.detections:d.pop('rechecking',None)
        if self.active_scan is None:
            return
        before = set(self.searched_rooms())
        self.searched_points.add(self.active_scan)
        self.active_scan = None
        for room_id in sorted(set(self.searched_rooms()) - before):
            self.metrics['rooms_inspected'] += 1
            room = next(room for room in self.scene.rooms if room['id'] == room_id)
            self.log(f"Room search complete: {room['name']}.")

    def pair(self):
        """Simulator-side contact: the robot reaches a confirmed detection; truth decides who is there."""
        here = self.scene.world(self.robot)
        for d in self.detections:
            cell = tuple(d['cell'])
            retry = d['status'] == 'handoff' and d.get('retry') and self.time >= d.get('retry_after', 0)
            if ((d['status'] != 'confirmed' and not retry) or math.dist(d['position'], here) > PAIR_M
                    or not line_visible(self.scene, self.robot, cell)):
                continue
            wanted = ('assistance-requested',) if retry else ('unobserved',)
            candidates = [p for p in self.survivors if p['status'] in wanted
                          and math.dist(self.scene.world(p['position']), here) <= PAIR_M
                          and math.dist(self.scene.world(p['position']), d['position']) <= PAIR_M
                          and line_visible(self.scene, self.robot, p['position'])]
            person = min(candidates, key=lambda p: math.dist(self.scene.world(p['position']), d['position']),
                         default=None)
            if person is None:
                if cell == self.robot and not retry:   # a handoff re-check never retires the detection
                    d['status'] = 'not-found'
                    self.metrics['false_detections'] += 1
                    self.version += 1
                    self.log(f"No person found at {d['id']} on arrival; detection retired.")
                continue
            # Simulated contact check: the scenario says whether this person can follow (truth, evaluated on contact).
            if tuple(person['position']) in self.immobile:
                self.handoff(person, d, 'cannot follow the robot (simulated contact check)', 'cannot follow')
                continue
            costs, _ = self.tree(True)
            if not any(e in costs for e in self.scene.exits):
                if retry:
                    d['retry_after'] = self.time + HANDOFF_RECHECK_S
                    self.log(f"Re-checked {d['id']}: still no human-safe route; responders remain requested.")
                    continue
                self.handoff(person, d, 'no route to a verified exit within human escort limits', 'can follow')
                continue
            if retry:
                self.metrics['handoffs'] -= 1
                self.log(f"Re-checked {d['id']}: a human-safe route is now available.")
            person['status'] = 'escorting'
            person['position'] = self.robot
            self.escort[person['id']] = d['id']
            d['status'] = 'escorting'
            self.scan_remaining = 0
            self.active_scan = None
            self.goal = None
            self.version += 1
            self.report('escort-started', self.robot, confirmed=1, mobility='can follow', detection=d['id'],
                        action='guiding to the verified exit')
            self.log(f"Escort started: {d['id']}")

    def handoff(self,person,d,reason,mobility):
        """Constitution step 5: report the location and request responders, then continue the search."""
        person['status']='assistance-requested';d['status']='handoff';self.metrics['handoffs']+=1;self.metrics['assistance_requests']+=1;self.version+=1
        d['retry']=mobility=='can follow';d['retry_after']=self.time+HANDOFF_RECHECK_S
        self.log(f"Assistance requested for {d['id']}: {reason}. Continuing search.")
        self.report('assistance-request',self.robot,confirmed=1,mobility=mobility,detection=d['id'],
                    action=f'responders needed: {reason}; robot continues search')

    def state(self):
        public=[]
        for p in self.survivors:
            item=dict(p)
            if p['status']=='unobserved' and any(d['status']=='confirmed' and math.dist(d['position'],self.scene.world(p['position']))<=DEDUP_M for d in self.detections):
                item['status']='identified'
            public.append(item)
        return dict(scene_id=self.scene.id,scenario=self.scenario,time=round(self.time,2),status=self.status,
                    hazard_time=round(self.hazard_time,2),hazards_running=self.hazards_running,
                    incident=self.incident,
                    sprinklers=[
                        dict(origin=origin, cells=list(zip(self.sprinkler_coverage[i][1], self.sprinkler_coverage[i][0]))
                             if i in self.active_sprinklers and (self.status == 'running' or self.hazards_running) else [])
                        for i, origin in enumerate(self.sprinkler_heads)
                    ],
                    hazard_sensor=self.hazard_sensor(),fire=self.fire.round(3).tolist(),
                    robot=self.robot,heading=round(self.heading,4),pose=self.camera_pose(),survivors=public,
                    heat=self.heat.round(3).tolist(),smoke=self.smoke.round(3).tolist(),observed=self.observed.astype(int).tolist(),
                    route=self.route,events=self.events,metrics={k:(round(v,3) if isinstance(v,float) else v) for k,v in self.metrics.items()},
                    mode=self.mode,perception_mode=self.perception_mode,detections=self.detections,
                    searched_rooms=self.searched_rooms(),
                    frame_request=self.frame_request,degraded=self.degraded,scanning=self.scan_remaining>0,holding=self.holding,last_frame=self.last_frame,
                    reports=self.reports,rooms_state=list(self.rooms_state.values()),decisions=self.decisions[-30:],
                    withdraw_reason=self.withdraw_reason,comms_up=self.comms_up,risk_map=list(self.risk.values()))


def compare(scene,scenario,perception='analytic-development'):
    if perception!='analytic-development':raise ValueError('Headless comparison supports the analytic fixture detector only')
    reports={}
    for mode in ('baseline','adaptive'):
        sim=Simulation(scene,scenario,mode,perception);sim.status='running'
        while sim.status=='running':sim.tick()
        reports[mode]=dict(status=sim.status,time=round(sim.time,2),**{k:(round(v,3) if isinstance(v,float) else v) for k,v in sim.metrics.items()})
    return dict(seed=scenario['seed'],scene_id=scene.id,perception=perception,reports=reports,
                note='Deterministic policy comparison with the labelled analytic fixture detector; hosted agent excluded. Illustrative exposure units; no guaranteed adaptive improvement.')
