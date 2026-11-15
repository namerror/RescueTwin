"""Paired benchmark of the experimental multi-person strategies (rescuetwin/strategies.py).

Every strategy runs on the same scenarios, scene, seed, hazards and perception (the labelled analytic fixture
detector, adaptive routing). Exposure is in illustrative simulated units, not real-world safety.

    .venv/bin/python scripts/strategy_benchmark.py --scenarios 30 --output data/validation/strategies.json
"""
import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuetwin import presets  # noqa: E402
from rescuetwin.core import Simulation, route  # noqa: E402
from rescuetwin.scenes import available  # noqa: E402
from rescuetwin.strategies import STRATEGIES  # noqa: E402


def scenarios(scene, count, seed=0):
    rng = random.Random(seed)
    out = []
    rooms = [r for r in scene.rooms if r['cells']]
    for i in range(count):
        k = rng.choice((2, 3, 3, 4))
        taken, people = [], []
        for room in rng.sample(rooms, min(k, len(rooms))):
            cell = presets._pick(rng, scene, room, taken, 1.0)
            if cell:
                taken.append(cell); people.append(list(cell))
        fires = []
        for _ in range(20):
            room = rng.choice(rooms)
            cell = presets._pick(rng, scene, room, taken, 2.0)
            if cell and all(abs(cell[0] - p[0]) + abs(cell[1] - p[1]) > 4 for p in people):
                fires.append(list(cell)); break
        out.append(dict(name=f'random-{i}', scenario=dict(survivors=people, ignitions=fires, seed=1000 + i, scene_id=scene.id)))
    # Stress family: fire placed 2-3 cells beside the middle of a person's walking route to the exit, so escort
    # routes pass near a growing fire (what the time-aware and exposure-budget planners are for).
    for i in range(count):
        k = rng.choice((1, 2, 3))
        taken, people = [], []
        for room in rng.sample(rooms, min(k, len(rooms))):
            cell = presets._pick(rng, scene, room, taken, 1.0)
            if cell:
                taken.append(cell); people.append(list(cell))
        if not people:
            continue
        zero = [[0.] * scene.width for _ in range(scene.height)]
        ones = [[1.] * scene.width for _ in range(scene.height)]
        path = route(scene, scene.exits[0], tuple(people[0]), zero, zero, ones, adaptive=False)
        middle = path[len(path) // 3: 2 * len(path) // 3] or path
        on_path = set(path)
        options = [(x + dx, y + dy) for x, y in middle for dx in range(-3, 4) for dy in range(-3, 4)
                   if 2 <= abs(dx) + abs(dy) <= 3 and scene.free((x + dx, y + dy)) and (x + dx, y + dy) not in on_path
                   and (x + dx, y + dy) not in set(map(tuple, people)) and (x + dx, y + dy) not in set(scene.exits)
                   and (x + dx, y + dy) != tuple(scene.robot_start or scene.exits[0])]
        if not options:
            continue
        fire = list(rng.choice(sorted(set(options))))
        out.append(dict(name=f'exit-fire-{i}', scenario=dict(survivors=people, ignitions=[fire], seed=5000 + i, scene_id=scene.id)))
    for kind in ('standard', 'capacity-stress'):
        try:
            out.append(dict(name=kind, scenario=presets.build(scene, kind)['scenario']))
        except Exception:
            pass
    return out


def run(args):
    scene_id, name, scenario, strategy = args
    scene = SCENES[scene_id]
    sim = Simulation(scene, scenario, 'adaptive', 'analytic-development', strategy)
    sim.status = 'running'
    started = time.process_time()
    last_out = None
    while sim.status == 'running':
        before = sim.metrics['evacuated']
        sim.tick()
        if sim.metrics['evacuated'] > before:
            last_out = sim.time
    n = len(sim.survivors)
    return dict(scene=scene_id, scenario=name, strategy=strategy, people=n, status=sim.status, time=round(sim.time, 1),
                evacuated=sim.metrics['evacuated'], casualties=sum(p['status'] == 'casualty' for p in sim.survivors),
                never_found=sum(p['status'] == 'unobserved' for p in sim.survivors),
                handoffs=sim.metrics['handoffs'], all_out_time=round(last_out, 1) if sim.metrics['evacuated'] == n and n else None,
                distance=round(sim.metrics['distance'], 1), exposure=round(sim.metrics['exposure'], 2),
                detours=sim.metrics.get('detours', 0), self_evacuated=sim.metrics.get('self_evacuated', 0), cpu_s=round(time.process_time() - started, 2),
                escort_exposure=round(sim.metrics.get('escort_exposure', 0.), 3), plans=sim.metrics.get('plans', 0),
                trees_built=sim.metrics.get('trees_built', 0), tree_requests=sim.metrics.get('tree_requests', 0))


SCENES = available()


def summarize(rows):
    table = {}
    keyed = {(r['scene'], r['scenario'], r['strategy']): r for r in rows}
    for strategy in sorted({r['strategy'] for r in rows}, key=STRATEGIES.index):
        mine = [r for r in rows if r['strategy'] == strategy]
        people = sum(r['people'] for r in mine)
        # Paired time saving vs greedy on scenarios where both evacuated everyone.
        savings = [keyed[(r['scene'], r['scenario'], 'greedy')]['all_out_time'] - r['all_out_time'] for r in mine
                   if r['all_out_time'] is not None and keyed[(r['scene'], r['scenario'], 'greedy')]['all_out_time'] is not None]
        table[strategy] = dict(
            runs=len(mine), evacuated_pct=round(100 * sum(r['evacuated'] for r in mine) / max(people, 1), 1),
            casualties=sum(r['casualties'] for r in mine), never_found=sum(r['never_found'] for r in mine), handoffs=sum(r['handoffs'] for r in mine),
            all_out_runs=sum(r['all_out_time'] is not None for r in mine),
            median_all_out_s=statistics.median([r['all_out_time'] for r in mine if r['all_out_time'] is not None] or [0]),
            mean_saving_vs_greedy_s=round(statistics.mean(savings), 1) if savings else None,
            faster_runs=sum(s > 0.5 for s in savings), slower_runs=sum(s < -0.5 for s in savings),
            mean_distance_m=round(statistics.mean(r['distance'] for r in mine), 1),
            mean_exposure=round(statistics.mean(r['exposure'] for r in mine), 2),
            mean_escort_exposure=round(statistics.mean(r['escort_exposure'] for r in mine), 3),
            mean_cpu_s=round(statistics.mean(r['cpu_s'] for r in mine), 2),
            trees_built=sum(r['trees_built'] for r in mine), tree_requests=sum(r['tree_requests'] for r in mine))
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenarios', type=int, default=30)
    parser.add_argument('--strategies', default=','.join(STRATEGIES))
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--output', type=Path)
    a = parser.parse_args()
    jobs = []
    for scene_id, scene in SCENES.items():
        for item in scenarios(scene, a.scenarios):
            for strategy in a.strategies.split(','):
                jobs.append((scene_id, item['name'], item['scenario'], strategy))
    with ProcessPoolExecutor(a.workers) as pool:
        rows = list(pool.map(run, jobs, chunksize=1))
    result = dict(note='Analytic fixture detector, adaptive routing, illustrative exposure units; paired on identical inputs.',
                  summary=summarize(rows),
                  by_scene={s: summarize([r for r in rows if r['scene'] == s]) for s in SCENES},
                  by_family={f: summarize([r for r in rows if r['scenario'].startswith(f)]) for f in ('random', 'exit-fire')},
                  rows=rows)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2))
    print('done', len(rows), 'runs')


if __name__ == '__main__':
    main()
