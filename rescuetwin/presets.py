"""Scenario presets. Capacity-stress uses marketed CoStar capacity as a hypothetical load, never as occupancy."""
from __future__ import annotations
import math
import random

CAPACITY_LABEL = ('Hypothetical load: survivor count derived from the marketed maximum occupants of the matched '
                  'CoStar floor plan. This is not actual or live occupancy.')
MAX_PLACEMENTS = 20
MIN_EXIT_DISTANCE_M = 2.0


def _eligible(scene, room, taken):
    start = tuple(scene.robot_start or scene.exits[0])
    blocked = set(scene.exits) | {start} | set(taken)
    cells = [tuple(c) for c in room['cells'] if scene.free(c) and tuple(c) not in blocked]
    far = [c for c in cells if min(math.dist(scene.world(c), scene.world(e)) for e in scene.exits) >= MIN_EXIT_DISTANCE_M]
    return far or cells


def _pick(rng, scene, room, taken, spacing):
    cells = _eligible(scene, room, taken)
    spaced = [c for c in cells if all(math.dist(scene.world(c), scene.world(t)) >= spacing for t in taken)]
    pool = spaced or cells
    return rng.choice(sorted(pool)) if pool else None


def _rooms(scene, label):
    return [r for r in scene.rooms if (r.get('label') or '') == label]


def _ignition(rng, scene, taken):
    """Far corner of the unoccupied side room nearest the exit: threatens the egress corridor over
    time without sealing it at t=0. Placement is illustrative, not a fire-origin prediction."""
    exit_xy = scene.world(scene.exits[0])
    occupied = {id(r) for r in scene.rooms for c in taken if tuple(c) in {tuple(p) for p in r['cells']}}
    side = [r for r in scene.rooms if id(r) not in occupied and r.get('label') not in ('Kitchen', None)]
    if not side:
        side = [r for r in scene.rooms if id(r) not in occupied] or scene.rooms
    steps = _walking_steps(scene, scene.exits[0])
    room = min(side, key=lambda r: steps.get(tuple(r['observe'][0] if r.get('observe') else r['cells'][0]), math.inf))
    cells = [c for c in _eligible(scene, room, taken) if c in steps]
    return max(cells, key=lambda c: (steps[c], math.dist(scene.world(c), exit_xy))) if cells else None


def _walking_steps(scene, start):
    from collections import deque
    start = tuple(start)
    steps, queue = {start: 0}, deque([start])
    while queue:
        p = queue.popleft()
        for q in scene.neighbors(p):
            if q not in steps:
                steps[q] = steps[p] + 1
                queue.append(q)
    return steps


def build(scene, kind, seed=7):
    rng = random.Random(seed)
    taken = []
    if kind == 'standard':
        if scene.id == 'development-apartment':
            return dict(id='standard', label='Standard: 2 survivors, 1 ignition', note='Synthetic fixture default.',
                        scenario=dict(scene_id=scene.id, survivors=[[26, 6], [15, 18]], ignitions=[[25, 18]], seed=seed))
        bedrooms = _rooms(scene, 'Bedroom') or [r for r in scene.rooms if r.get('label') != 'Kitchen'] or scene.rooms
        survivors = []
        for room in bedrooms[-2:]:
            cell = _pick(rng, scene, room, taken, 1.0)
            if cell:
                survivors.append(list(cell)); taken.append(cell)
        ignition = _ignition(rng, scene, taken)
        return dict(id='standard', label=f'Standard: {len(survivors)} survivors, 1 ignition',
                    note='Illustrative placements; survivors and fire are synthetic.',
                    scenario=dict(scene_id=scene.id, survivors=survivors, ignitions=[list(ignition)] if ignition else [], seed=seed))
    if kind in ('capacity-stress', 'capacity-25', 'capacity-50'):
        costar = scene.meta.get('costar') or {}
        capacity = costar.get('max_occupants')
        if type(capacity) is not int or capacity <= 0 or costar.get('capacity_conflicts'):
            return None
        fraction = {'capacity-25': .25, 'capacity-50': .5, 'capacity-stress': 1.0}[kind]
        requested = math.ceil(capacity * fraction)
        count = min(requested, MAX_PLACEMENTS)
        # Distribute round-robin over bedrooms first, then other rooms; capacity says nothing about where people are.
        order = (_rooms(scene, 'Bedroom') + [r for r in scene.rooms if r.get('label') not in ('Bedroom', 'Bathroom')]
                 + _rooms(scene, 'Bathroom')) or scene.rooms
        survivors, i = [], 0
        while len(survivors) < count and i < count * 4:
            cell = _pick(rng, scene, order[i % len(order)], taken, 0.75)
            i += 1
            if cell:
                survivors.append(list(cell)); taken.append(cell)
        ignition = _ignition(rng, scene, taken)
        field = next((f for f in costar.get('fields', []) if f['key'] == 'max_occupants'), {})
        return dict(id=kind, label=f'Capacity {fraction:.0%}: {len(survivors)} survivors (marketed max {capacity})',
                    note=CAPACITY_LABEL, capacity=capacity, fraction=fraction, requested_count=requested, placed_count=len(survivors),
                    provenance=dict(source_id=field.get('source_id'), field_path=field.get('field_path'), dataset=field.get('dataset')),
                    truncated=len(survivors) < requested,
                    scenario=dict(scene_id=scene.id, survivors=survivors, ignitions=[list(ignition)] if ignition else [], seed=seed))
    raise ValueError(f'Unknown preset {kind!r}')


def available(scene, seed=7):
    presets = [build(scene, 'standard', seed)]
    stress = build(scene, 'capacity-stress', seed)
    if stress:
        seen=set()
        for kind in ('capacity-25','capacity-50','capacity-stress'):
            entry=build(scene,kind,seed)
            if entry['requested_count'] not in seen:
                presets.append(entry);seen.add(entry['requested_count'])
    else:
        presets.append(dict(id='capacity-stress', label='Capacity stress unavailable', unavailable=True,
                            note='No unambiguous matched CoStar floor-plan maxOccupants for this scene; nothing is inferred.'))
    return presets


def experiment_context(scene, scenario):
    """Operator/evaluation provenance only; never part of agent observations."""
    import hashlib
    import json
    costar=scene.meta.get('costar') or {}
    canonical=json.dumps(scenario,sort_keys=True,separators=(',',':'))
    return dict(scenario_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
                placed_count=len(scenario['survivors']),marketed_capacity=costar.get('max_occupants'),
                capacity_provenance=next((f for f in costar.get('fields',[]) if f['key']=='max_occupants'),None),
                disclaimer='Scenario survivor count is hypothetical; marketed capacity is not live occupancy.')
