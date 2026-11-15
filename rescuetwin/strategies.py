"""Experimental multi-person search/escort strategies layered on the baseline controller in core.py.

`greedy` is the existing behaviour (one person, straight to the exit). The others only change *which goal* the
robot picks; A* still executes, and human escort limits plus the local interlock are never relaxed. Every detour
is planned with a safety margin: escort routes are computed with observed heat/smoke inflated by MARGIN, so the
group only moves where readings sit well below the human limits, and the exit must stay reachable from the detour
target under the same margin.

All inputs are robot belief and static geometry (planning_fields, observed cells, rooms, priors). No strategy reads
simulator survivor positions; contact and mobility are still decided simulator-side in core.pair().
"""
from __future__ import annotations

import math
from itertools import permutations

from .core import ESCORT_HEAT_LIMIT, ESCORT_SMOKE_LIMIT, ROBOT_SPEED, extract, line_visible, route_tree

STRATEGIES = ('greedy', 'greedy-credit', 'pickup', 'lookaround', 'horizon', 'zone', 'self-evac',
              'mission-astar', 'mission-astar-cached', 'time-aware', 'exposure-budget')
MISSION = ('mission-astar', 'mission-astar-cached')

MARGIN = 1.5                 # detour routes treat observed heat/smoke as 1.5x (limits effectively 0.4 / 0.33)
PICKUP_BUDGET_M = 8.0        # S1: extra travel allowed to collect an already-confirmed person
LOOK_BUDGET_M = 8.0          # S2: extra travel allowed for a peek viewpoint
MAX_PEEKS = 2                # S2: peeks per escort
MIN_GAIN_CELLS = 8           # S2: a peek must reveal at least this many unobserved room cells
REPLAN_S = 1.0               # minimum mission time between detour searches
ALPHA = 0.5                  # S3: cost weight per follower on extra travel (follower time inside)
P_ROOM = 0.35                # S3: prior chance an unsearched room of average likelihood holds a person
ZONE_M = 6.0                 # S4: rooms with an observation point this close (path) to a find form its zone
ZONE_SAFE_HEAT, ZONE_SAFE_SMOKE = .2, .2   # S4: never defer contact when the person's cell reads above these
SELF_EVAC_M = 12.0           # S5: longest route a person is directed to walk alone
WALK_SPEED = ROBOT_SPEED     # S5: same pace as an escort, so only the robot's return trip is saved
CREDIT_FRACTION = .9         # rooms this well observed count as searched (all non-greedy strategies)


def credit(sim):
    return sim.strategy == 'greedy-credit'   # isolated: it can skip people in the unseen 10%


# ------------------------------------------------------------------ shared planning helpers
def margin_tree(sim, start):
    heat, smoke = sim.planning_fields()
    return route_tree(sim.scene, start, (heat * MARGIN).tolist(), (smoke * MARGIN).tolist(), sim.observed.tolist(),
                      sim.mode == 'adaptive', escort=True)


def exit_costs(sim):
    """Cell -> margin escort cost to the nearest exit (approximately symmetric costs, computed from each exit)."""
    best = {}
    for e in sim.scene.exits:
        costs, _ = margin_tree(sim, e)
        for cell, cost in costs.items():
            if cost < best.get(cell, math.inf):
                best[cell] = cost
    return best


def unsearched_rooms(sim):
    searched = set(sim.searched_rooms())
    return [r for r in sim.scene.rooms if r['id'] not in searched and sim.rooms_state[r['id']]['status'] != 'inspected']


def peek_views(sim, costs, to_exit, now, budget_m, limit=6):
    """Reachable cells (within the detour budget) that reveal the most unobserved cells of unsearched rooms."""
    res = sim.scene.resolution
    reach = 4.0 if sim.perception_mode == 'image-yolo' else 3.5
    hidden = [(r['id'], tuple(c)) for r in unsearched_rooms(sim) for c in r['cells'] if not sim.observed[c[1], c[0]]]
    if not hidden:
        return []
    hidden = hidden[::max(1, len(hidden) // 120)]
    cells = [c for c, cost in costs.items() if c in to_exit and (cost + to_exit[c] - now) * res <= budget_m]
    cells = cells[::max(1, len(cells) // 80)]
    views = []
    for c in cells:
        wc = sim.scene.world(c)
        seen = {}
        for room, h in hidden:
            if math.dist(wc, sim.scene.world(h)) <= reach and line_visible(sim.scene, c, h):
                seen[room] = seen.get(room, 0) + 1
        gain = sum(seen.values())
        if gain >= MIN_GAIN_CELLS:
            views.append(dict(cell=c, gain=gain, rooms=seen, detour=(costs[c] + to_exit[c] - now) * res))
    views.sort(key=lambda v: -v['gain'] / (v['detour'] + 1.0))
    return views[:limit]


def confirmed_cells(sim):
    return [tuple(d['cell']) for d in sim.detections if d['status'] == 'confirmed']


def room_probability(sim, room_id):
    weights = [sim.search_weight(r['id']) for r in sim.scene.rooms] or [1.]
    mean = sum(weights) / len(weights)
    return min(1., P_ROOM * sim.search_weight(room_id) / mean)


# ------------------------------------------------------------------ escort-time goal selection
def escort_goal(sim):
    """Path to a detour target while escorting, or None to head straight for the exit (baseline behaviour)."""
    if sim.withdraw_reason:
        return None
    if sim.strategy in MISSION:
        return mission_goal(sim)
    if sim.strategy == 'time-aware':
        return time_aware_exit(sim)
    if sim.strategy == 'exposure-budget':
        return budget_exit(sim)
    if sim.strategy not in ('pickup', 'lookaround', 'horizon', 'zone'):
        return None
    if sim.detour_goal is not None:
        if sim.robot == sim.detour_goal:
            if sim.detour_kind == 'peek' and sim.perception_mode == 'image-yolo':
                sim.scan_remaining = 2 * math.pi
            sim.detour_goal = None
        else:
            costs, prev = margin_tree(sim, sim.robot)
            if sim.detour_goal in costs:
                return extract(prev, sim.robot, sim.detour_goal)
            sim.detour_goal = None
    if sim.time - sim.detour_planned_at < REPLAN_S:
        return None
    sim.detour_planned_at = sim.time
    costs, prev = margin_tree(sim, sim.robot)
    to_exit = exit_costs(sim)
    if sim.robot not in to_exit:
        return None
    now = to_exit[sim.robot]
    res = sim.scene.resolution
    # Time-limit guard with the group: the detour plus the walk out must fit with the usual margin.
    def fits(detour_m):
        return sim.time + (now * res + detour_m) / ROBOT_SPEED + 15 < sim.scene.time_limit
    target, kind = None, None
    if sim.strategy in ('pickup', 'lookaround', 'zone'):
        picks = [((costs[t] + to_exit[t] - now) * res, t) for t in confirmed_cells(sim) if t in costs and t in to_exit]
        picks = [p for p in picks if p[0] <= PICKUP_BUDGET_M and fits(p[0])]
        if picks:
            target, kind = min(picks)[1], 'pickup'
    if target is None and sim.strategy == 'lookaround' and sim.peeks < MAX_PEEKS:
        views = [v for v in peek_views(sim, costs, to_exit, now, LOOK_BUDGET_M) if fits(v['detour'])]
        if views:
            target, kind = views[0]['cell'], 'peek'
            sim.peeks += 1
    if sim.strategy == 'horizon':
        target, kind = horizon_choice(sim, costs, to_exit, now, fits)
    if target is None:
        return None
    sim.detour_goal, sim.detour_kind = target, kind
    sim.metrics['detours'] = sim.metrics.get('detours', 0) + 1
    sim.decide(f'escort-{kind}', target, [(costs[target], target)], f'{kind} detour during escort ({sim.strategy})', 'escort')
    return extract(prev, sim.robot, target)


def horizon_choice(sim, costs, to_exit, now, fits):
    """S3: score exit-now against every 1- or 2-stop detour. Visiting a stop now saves a later round trip from the
    exit (2 x its distance to the exit) with the chance it holds someone; the cost is the extra travel, weighted up
    by the number of people following."""
    res = sim.scene.resolution
    stops = [dict(cell=t, value=1., kind='pickup') for t in confirmed_cells(sim) if t in costs and t in to_exit]
    for v in peek_views(sim, costs, to_exit, now, 2 * LOOK_BUDGET_M, limit=4):
        value = sum(room_probability(sim, room) * min(1., n / max(1, len([c for c in next(r for r in sim.scene.rooms if r['id'] == room)['cells']
                                                                           if not sim.observed[c[1], c[0]]])))
                    for room, n in v['rooms'].items())
        stops.append(dict(cell=v['cell'], value=min(1., value), kind='peek'))
    if not stops:
        return None, None
    stops = stops[:6]
    pair = {}
    for a in stops:
        tree, _ = margin_tree(sim, a['cell'])
        for b in stops:
            pair[a['cell'], b['cell']] = tree.get(b['cell'], math.inf)
    followers = max(1, len(sim.escort))
    best = (0., None)
    for n in (1, 2):
        for seq in permutations(stops, n):
            travel = costs[seq[0]['cell']] + sum(pair[a['cell'], b['cell']] for a, b in zip(seq, seq[1:])) + to_exit[seq[-1]['cell']]
            detour = (travel - now) * res
            if not math.isfinite(detour) or not fits(detour):
                continue
            gain = sum(s['value'] * 2 * to_exit[s['cell']] * res for s in seq)
            score = gain - (1 + ALPHA * followers) * detour
            if score > best[0]:
                best = (score, seq[0])
    return (best[1]['cell'], best[1]['kind']) if best[1] else (None, None)


# ------------------------------------------------------------------ S4: sweep a zone before contact
def zone_target(sim, costs, prev):
    """While a zone sweep is open, search its remaining observation points before making contact."""
    if sim.strategy != 'zone' or sim.escort:
        return None
    found = [d for d in sim.detections if d['status'] == 'confirmed']
    if sim.zone is None and found:
        free = sim.grid()
        tree, _ = route_tree(sim.scene, tuple(found[0]['cell']), free.tolist(), free.tolist(),
                             (free + 1).tolist(), adaptive=False)
        rooms = unsearched_rooms(sim)
        sim.zone = {p for r in rooms for p in sim.observe_points(r)
                    if any(tree.get(q, math.inf) * sim.scene.resolution <= ZONE_M for q in sim.observe_points(r))}
        sim.log(f'Zone sweep: {len(sim.zone)} nearby observation points before contact.')
    if not sim.zone:
        return None
    for d in found:   # never let a found person wait in worsening conditions
        x, y = d['cell']
        if sim.belief_heat[y, x] >= ZONE_SAFE_HEAT or sim.belief_smoke[y, x] >= ZONE_SAFE_SMOKE:
            sim.zone = set()
            return None
    remaining = [(costs[p], p) for p in sim.zone if p not in sim.searched_points and p in costs]
    if not remaining:
        sim.zone = set()
        return None
    target = min(remaining)[1]
    sim.decide('zone-sweep', target, remaining, 'sweep nearby rooms before contact, then escort everyone together', 'zone')
    return extract(prev, sim.robot, target)


def defer_contact(sim):
    return sim.strategy == 'zone' and bool(sim.zone) and not sim.escort


# ------------------------------------------------------------------ S5: direct a person to walk out alone
def self_evacuate(sim, person, d):
    """At contact: if a short, observed route within margin limits exists, the person walks out alone."""
    if sim.strategy != 'self-evac':
        return False
    costs, prev = margin_tree(sim, sim.robot)
    options = [(costs[e], e) for e in sim.scene.exits if e in costs]
    if not options:
        return False
    path = extract(prev, sim.robot, min(options)[1])
    if not path or len(path) * sim.scene.resolution > SELF_EVAC_M:
        return False
    person['status'] = 'self-evacuating'
    d['status'] = 'self-evacuating'
    sim.walkers.append(dict(person=person, detection=d, path=path[1:], progress=0.))
    sim.version += 1
    sim.report('directed-to-exit', sim.robot, confirmed=1, mobility='can follow', detection=d['id'],
               action='person directed along an observed route to the exit; robot continues search')
    sim.log(f"Directed {d['id']} to walk to the exit ({len(path) * sim.scene.resolution:.1f} m); continuing search.")
    return True


def advance_walkers(sim, dt):
    for person in sim.survivors:   # exposure of people while they follow the robot (evaluation metric)
        if person['status'] == 'escorting':
            x, y = person['position']
            sim.metrics['escort_exposure'] = sim.metrics.get('escort_exposure', 0.) + float(dt * (sim.heat[y, x] + sim.smoke[y, x]))
    for w in list(sim.walkers):
        person, d = w['person'], w['detection']
        if person['status'] != 'self-evacuating':
            sim.walkers.remove(w)
            continue
        w['progress'] += WALK_SPEED * dt
        while w['progress'] >= sim.scene.resolution and w['path']:
            x, y = w['path'][0]
            if sim.heat[y, x] >= ESCORT_HEAT_LIMIT or sim.smoke[y, x] >= ESCORT_SMOKE_LIMIT:
                # Blocked on the way: stop and become a responder handoff the robot will re-check.
                sim.walkers.remove(w)
                d['position'] = list(sim.scene.world(person['position']))
                d['cell'] = list(person['position'])
                sim.handoff(person, d, 'self-evacuation route blocked by hazard', 'can follow')
                break
            w['progress'] -= sim.scene.resolution
            person['position'] = (x, y)
            w['path'] = w['path'][1:]
            if (x, y) in sim.scene.exits:
                person['status'] = 'evacuated'
                d['status'] = 'evacuated'
                sim.metrics['evacuated'] += 1
                sim.metrics['self_evacuated'] = sim.metrics.get('self_evacuated', 0) + 1
                sim.version += 1
                sim.log(f"Evacuated (walked out): {d['id']}")
                sim.walkers.remove(w)
                break


# ------------------------------------------------------------------ observation credit
def credit_observed_rooms(sim):
    """Rooms seen almost completely (e.g. by a peek) count as searched, so the robot does not walk back to them."""
    if not credit(sim):
        return
    for room in sim.scene.rooms:
        if sim.rooms_state[room['id']]['observed_fraction'] >= CREDIT_FRACTION:
            points = [p for p in sim.observe_points(room) if p not in sim.searched_points]
            if points:
                sim.searched_points.update(points)


# ================================================================== A*-family experiments (algorithms 3-6)
import heapq  # noqa: E402
import numpy as np  # noqa: E402

LATENCY_ALPHA = 0.5          # 3: cost per metre per follower, relative to delaying a p=1 target by a metre
REPLAN_MISSION_S = 1.0       # 3: replan cadence (also on reaching the current goal)
CACHE_EPS = 0.05             # 4: reuse cached trees until a belief reading moves this much or new cells are seen
RATE_WINDOW_S = 3.0          # 5: belief snapshots this far apart give the observed growth rate
EXPOSURE_BUDGET = 1.0        # 6: allowed (heat + smoke) x seconds per follower on the way out
LAMBDAS = (0., 2., 8., 32.)  # 6: exposure prices tried, cheapest route first
URGENCY_GAIN = 2.0           # 3: a room under maximal observed threat weighs (1 + gain)x an untouched one
URGENCY_HORIZON_S = 10.0     # 3: threat is judged where observed growth would take it this far ahead
URGENCY_REACH_CELLS = 3      # 3: hazard this many cells outside a room still threatens it


# ---------------------------------------------------------------- 4: cached / lazily repaired distance trees
def _fields_changed(sim):
    heat, smoke = sim.planning_fields()
    snap = sim.__dict__.get('tree_cache_snapshot')
    if snap is None or snap[0] != sim.version or float(np.max(np.abs(heat - snap[1]))) > CACHE_EPS \
            or float(np.max(np.abs(smoke - snap[2]))) > CACHE_EPS:
        sim.tree_cache_snapshot = (sim.version, heat.copy(), smoke.copy())
        sim.tree_cache = {}
        return True
    return False


def cached_tree(sim, start, escort):
    """Algorithm 4 stand-in for D* Lite: keep each source's tree until the belief changes beyond CACHE_EPS.
    (A true D* Lite repairs only the affected part of the tree; this reuses or rebuilds it whole.)"""
    sim.metrics['tree_requests'] = sim.metrics.get('tree_requests', 0) + 1
    if sim.strategy == 'mission-astar-cached':
        _fields_changed(sim)
        key = (tuple(start), escort)
        if key in sim.tree_cache:
            return sim.tree_cache[key]
    sim.metrics['trees_built'] = sim.metrics.get('trees_built', 0) + 1
    tree = margin_tree(sim, start) if escort else _free_tree(sim, start)
    if sim.strategy == 'mission-astar-cached':
        sim.tree_cache[(tuple(start), escort)] = tree
    return tree


def _free_tree(sim, start):
    heat, smoke = sim.planning_fields()
    return route_tree(sim.scene, start, heat.tolist(), smoke.tolist(), sim.observed.tolist(), sim.mode == 'adaptive')


# ---------------------------------------------------------------- 3: A* over mission states
def _dilate(a, k):
    """k passes of a 3x3 max filter: a hazard reading also counts for cells within k cells of it."""
    for _ in range(k):
        pad = np.pad(a, 1)
        a = np.max([pad[1 + dy:1 + dy + a.shape[0], 1 + dx:1 + dx + a.shape[1]]
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
    return a


def room_urgency(sim):
    """Per-room urgency multiplier in [1, 1 + URGENCY_GAIN] from robot belief only (never simulator truth).
    Threat is the observed heat/smoke near the room, projected URGENCY_HORIZON_S ahead with observed growth and
    normalised by the human escort limits. Rooms with no observed hazard (including unseen ones) stay neutral (1)."""
    heat, smoke = sim.planning_fields()
    rate_h, rate_s = growth_rates(sim)
    threat = np.maximum((heat + rate_h * URGENCY_HORIZON_S) / ESCORT_HEAT_LIMIT,
                        (smoke + rate_s * URGENCY_HORIZON_S) / ESCORT_SMOKE_LIMIT)
    near = _dilate(np.clip(threat, 0., 1.), URGENCY_REACH_CELLS)
    return {room['id']: 1. + URGENCY_GAIN * max((float(near[y, x]) for x, y in room['cells']), default=0.)
            for room in sim.scene.rooms}


def mission_targets(sim):
    """Mission stops with priority weights: chance someone is there x urgency of the room's conditions.
    While escorting, only confirmed people remain as stops, so the plan goes to the exit (via any confirmed pickup)."""
    searched = set(sim.searched_rooms())
    urgency = room_urgency(sim)
    sim.room_urgency = urgency
    targets = []
    for room in sim.scene.rooms:
        # With people following, never detour to search rooms: escort them out (picking up confirmed people only).
        if room['id'] in searched or sim.escort:
            continue
        points = [p for p in sim.observe_points(room) if p not in sim.searched_points]
        for p in points:
            targets.append(dict(cell=p, p=room_probability(sim, room['id']) * urgency[room['id']] / len(points),
                                person=False, room=room['id']))
    for d in sim.detections:
        if d['status'] == 'confirmed':
            room = sim.room_of.get(tuple(d['cell']))
            targets.append(dict(cell=tuple(d['cell']), p=urgency.get(room, 1.), person=True, room=room))
    return targets


def mission_plan(sim):
    """A* over (position, targets done, people following). Cost = priority-weighted latency: every metre travelled
    delays each unvisited target by its weight p (occupancy chance x hazard urgency), and costs LATENCY_ALPHA per
    follower. People must end at an exit.
    h = sum p_j * d_free(here, j) + alpha * followers * d_free(here, exit) is a lower bound (free distances are
    never longer than escort-limited ones)."""
    targets = mission_targets(sim)
    exits = [tuple(e) for e in sim.scene.exits]
    followers = len(sim.escort)
    if not targets and not followers:
        return None
    nodes = [sim.robot] + [t['cell'] for t in targets] + exits
    res = sim.scene.resolution
    free = [cached_tree(sim, n, False)[0] for n in nodes]
    esc = [cached_tree(sim, n, True)[0] for n in nodes]
    T = len(targets); E = range(T + 1, T + 1 + len(exits))
    D = lambda tree, i, j: tree[i].get(nodes[j], math.inf) * res
    weights = [t['p'] for t in targets]
    full = (1 << T) - 1
    # Targets nobody can reach are dropped from the goal (reported by the baseline logic instead).
    for j in range(T):
        if not math.isfinite(D(free, 0, j + 1)) and not any(math.isfinite(D(free, e, j + 1)) for e in E):
            full &= ~(1 << j)
    def h(i, mask, f):
        rest = sum(weights[j] * D(free, i, j + 1) for j in range(T) if full >> j & 1 and not mask >> j & 1)
        out = min((D(free, i, e) for e in E), default=math.inf) if f else 0.
        return rest + LATENCY_ALPHA * f * out
    start = (0, 0, followers)
    queue = [(h(*start), 0., start, None)]
    best = {start: 0.}; first = {start: None}
    expanded = 0
    while queue:
        _, g, state, _ = heapq.heappop(queue)
        if g > best.get(state, math.inf) + 1e-9:
            continue
        i, mask, f = state
        if mask & full == full and f == 0:
            sim.metrics['astar_expanded'] = sim.metrics.get('astar_expanded', 0) + expanded
            return first[state]
        expanded += 1
        if expanded > 20000:
            break
        waiting = sum(weights[j] for j in range(T) if full >> j & 1 and not mask >> j & 1)
        tree = esc if f else free
        moves = [(j + 1, mask | 1 << j, f + (1 if targets[j]['person'] else 0)) for j in range(T)
                 if full >> j & 1 and not mask >> j & 1]
        if f:
            moves += [(e, mask, 0) for e in E]
        for j, m2, f2 in moves:
            d = D(tree, i, j)
            if not math.isfinite(d):
                continue
            g2 = g + d * (waiting + LATENCY_ALPHA * f)
            s2 = (j, m2, f2)
            if g2 < best.get(s2, math.inf) - 1e-9:
                best[s2] = g2
                first[s2] = first[state] if first[state] is not None else nodes[j]
                heapq.heappush(queue, (g2 + h(j, m2, f2), g2, s2, None))
    return None


def mission_goal(sim, costs=None, prev=None):
    """Path toward the first stop of the current mission plan (replanned each second or on arrival)."""
    if sim.strategy not in MISSION or sim.withdraw_reason:
        return None
    goal = sim.__dict__.get('mission_target')
    if goal is None or goal == sim.robot or sim.time - sim.__dict__.get('mission_planned_at', -math.inf) >= REPLAN_MISSION_S:
        sim.mission_planned_at = sim.time
        goal = sim.mission_target = mission_plan(sim)
        sim.metrics['plans'] = sim.metrics.get('plans', 0) + 1
    if goal is None or goal == sim.robot:
        return None
    escort = bool(sim.escort)
    costs, prev = cached_tree(sim, sim.robot, escort)
    if goal not in costs:
        sim.mission_target = None
        return None
    room = sim.room_of.get(goal)
    urgency = sim.__dict__.get('room_urgency', {}).get(room, 1.)
    sim.decide('mission-plan', goal, [(costs[goal], goal)],
               f'first stop of the lowest priority-weighted latency mission plan (room urgency x{urgency:.2f})', 'mission A*')
    return extract(prev, sim.robot, goal)


# ---------------------------------------------------------------- 5: time-aware escort routing
def growth_rates(sim):
    snap = sim.__dict__.get('rate_snapshot')
    if snap is None or sim.time - snap[0] >= RATE_WINDOW_S:
        if snap is not None:
            dt = sim.time - snap[0]
            seen = sim.hazard_seen > snap[0]          # re-observed since the snapshot: a measured change
            grow_h = np.where(seen, np.maximum(0., sim.belief_heat - snap[1]) / dt, 0.)
            grow_s = np.where(seen, np.maximum(0., sim.belief_smoke - snap[2]) / dt, 0.)
            # Fire fronts move: let each growth rate reach one cell further (3x3 max filter).
            pad = lambda a: np.pad(a, 1)
            spread = lambda a: np.max([pad(a)[1 + dy:1 + dy + a.shape[0], 1 + dx:1 + dx + a.shape[1]]
                                       for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
            sim.rates = (spread(grow_h), spread(grow_s))
        sim.rate_snapshot = (sim.time, sim.belief_heat.copy(), sim.belief_smoke.copy())
    return sim.__dict__.get('rates') or (np.zeros_like(sim.belief_heat), np.zeros_like(sim.belief_smoke))


def time_aware_exit(sim):
    """Space-time Dijkstra: each cell is judged at the time the group would reach it, using belief plus observed
    growth (never simulator truth). Human limits apply to the predicted values."""
    rate_h, rate_s = growth_rates(sim)
    heat, smoke = sim.planning_fields()
    observed = sim.observed
    res = sim.scene.resolution
    adaptive = sim.mode == 'adaptive'
    start = sim.robot
    costs = {start: 0.}; metres = {start: 0.}; prev = {}
    queue = [(0., start)]
    exits = set(map(tuple, sim.scene.exits))
    while queue:
        c, p = heapq.heappop(queue)
        if c != costs[p]:
            continue
        if p in exits:
            sim.metrics['time_aware_routes'] = sim.metrics.get('time_aware_routes', 0) + 1
            return extract(prev, start, p)
        for q in sim.scene.neighbors(p):
            x, y = q
            if not observed[y, x]:
                continue
            tau = (metres[p] + res) / ROBOT_SPEED
            ph = min(1., heat[y, x] + rate_h[y, x] * tau)
            ps = min(1., smoke[y, x] + rate_s[y, x] * tau)
            if ph >= ESCORT_HEAT_LIMIT or ps >= ESCORT_SMOKE_LIMIT:
                continue
            step = 1 + ((8 * ph + 5 * ps) if adaptive else 0)
            if c + step < costs.get(q, math.inf):
                costs[q] = c + step; metres[q] = metres[p] + res; prev[q] = p
                heapq.heappush(queue, (c + step, q))
    return None   # predicted blocked: fall back to the baseline escort route / hold logic


# ---------------------------------------------------------------- 6: exposure-budgeted escort routing
def budget_exit(sim):
    """Shortest escort route whose follower exposure stays under EXPOSURE_BUDGET; prices exposure with increasing
    lambda (Lagrangian search) and falls back to the least-exposure route found."""
    heat, smoke = sim.planning_fields()
    observed = sim.observed
    res = sim.scene.resolution
    per_cell = (heat + smoke) * res / ROBOT_SPEED
    budget = EXPOSURE_BUDGET * max(1, len(sim.escort))
    exits = [tuple(e) for e in sim.scene.exits]
    fallback = None
    for lam in LAMBDAS:
        costs = {sim.robot: 0.}; prev = {}; queue = [(0., sim.robot)]
        while queue:
            c, p = heapq.heappop(queue)
            if c != costs[p]:
                continue
            for q in sim.scene.neighbors(p):
                x, y = q
                if not observed[y, x] or heat[y, x] >= ESCORT_HEAT_LIMIT or smoke[y, x] >= ESCORT_SMOKE_LIMIT:
                    continue
                c2 = c + 1 + lam * per_cell[y, x] / res
                if c2 < costs.get(q, math.inf):
                    costs[q] = c2; prev[q] = p; heapq.heappush(queue, (c2, q))
        options = [(costs[e], e) for e in exits if e in costs]
        if not options:
            return None
        path = extract(prev, sim.robot, min(options)[1])
        exposure = sum(per_cell[y, x] for x, y in path[1:]) * max(1, len(sim.escort))
        if exposure <= budget:
            sim.metrics['budget_lambda_sum'] = sim.metrics.get('budget_lambda_sum', 0.) + lam
            return path
        if fallback is None or exposure < fallback[0]:
            fallback = (exposure, path)
    sim.metrics['budget_exceeded'] = sim.metrics.get('budget_exceeded', 0) + 1
    return fallback[1] if fallback else None
