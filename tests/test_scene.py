"""Scene bundles, placement, walls, fire spread and missions on bundle scenes."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from rescuetwin import presets
from rescuetwin.core import Simulation, compare, line_visible
from rescuetwin.scenes import load_bundle

ROOT = Path(__file__).resolve().parent.parent
REAL = Path(os.environ.get('RESCUETWIN_TEST_BUNDLE', ROOT / 'data' / 'test-bundle'))
KIT = ROOT / 'data' / 'costar-hackathon-kit'


def test_default_scene_respects_private_environment_selection(monkeypatch):
    from types import SimpleNamespace
    from rescuetwin.scenes import default_scene
    from rescuetwin.core import fixture
    first = SimpleNamespace(id='first-floor', source='sponsor-source', meta={'frame': {'model': 'first'}})
    second = SimpleNamespace(id='second-floor', source='sponsor-source', meta={'frame': {'model': 'second'}})
    synthetic = fixture()
    registry = {s.id: s for s in (first, second, synthetic)}
    monkeypatch.delenv('RESCUETWIN_SCENE', raising=False)
    monkeypatch.delenv('RESCUETWIN_MODEL_ID', raising=False)
    assert default_scene(registry) is first
    monkeypatch.setenv('RESCUETWIN_MODEL_ID', 'second')
    assert default_scene(registry) is second
    monkeypatch.setenv('RESCUETWIN_SCENE', first.id)
    assert default_scene(registry) is first
    monkeypatch.delenv('RESCUETWIN_SCENE')
    assert default_scene({synthetic.id: synthetic}) is synthetic


def write_bundle(directory, door=True, capacity=4):
    """Two 4 x 3 m rooms at 0.25 m, separated by a wall with an optional 1 m doorway; exit on the west wall."""
    W, H, res = 34, 14, .25
    blocked = {'wall': [], 'exterior': []}
    for y in range(H):
        for x in range(W):
            edge = x in (0, W - 1) or y in (0, H - 1)
            divider = x == 17 and not (door and 6 <= y <= 9)
            exit_cell = (x, y) == (0, 7)
            if (edge and not exit_cell) or divider:
                blocked['exterior' if edge else 'wall'].append([x, y])
    rooms = [dict(id='a', name='Room A', label='Kitchen', cells=[[x, y] for x in range(1, 17) for y in range(1, 13)], observe=[[8, 7]]),
             dict(id='b', name='Room B', label='Bedroom', cells=[[x, y] for x in range(18, 33) for y in range(1, 13)], observe=[[25, 7]])]
    costar = None if capacity is None else dict(max_occupants=capacity, fields=[dict(key='max_occupants', source_id=1, field_path='propertyApartment.models[0].maxOccupants', dataset='commercial-property')])
    scene = dict(schema='rescuetwin.scene/1', id='mini', name='Mini', source='sponsor-derived',
                 grid=dict(origin=[-1.0, -2.0], resolution=res, width=W, height=H), blocked=blocked,
                 exits=[dict(id='exit', label='Door', cell=[0, 7])], rooms=rooms, robot_start=[0, 7], time_limit=300,
                 costar=costar, checks=dict(passed=True))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'scene.json').write_text(json.dumps(scene))
    return directory


@pytest.fixture
def mini(tmp_path):
    return load_bundle(write_bundle(tmp_path / 'mini'))


def test_bundle_loads_in_metric_frame(mini):
    assert mini.resolution == .25 and mini.origin == (-1.0, -2.0)
    assert mini.world((0, 0)) == (-0.875, -1.875)
    assert mini.cell(*mini.world((5, 9))) == (5, 9)
    assert mini.exits == [(0, 7)] and mini.free((0, 7))
    assert not mini.free((17, 2)) and mini.free((17, 7))
    assert [r['observe'] for r in mini.rooms] == [[(8, 7)], [(25, 7)]]


def test_bundle_rejects_bad_schema_and_blocked_exit(tmp_path):
    d = write_bundle(tmp_path / 'bad')
    data = json.loads((d / 'scene.json').read_text())
    data['schema'] = 'other'
    (d / 'scene.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_bundle(d)
    data['schema'] = 'rescuetwin.scene/1'
    data['exits'][0]['cell'] = [17, 2]
    (d / 'scene.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_bundle(d)


@pytest.mark.parametrize('cell', [[17, 2], [0, 0], [-1, 3], [40, 3], [0, 7]])
def test_invalid_placements_on_bundle(mini, cell):
    with pytest.raises(ValueError):
        Simulation(mini, dict(scene_id='mini', survivors=[cell], ignitions=[], seed=1))


def test_scenario_for_other_scene_rejected(mini):
    with pytest.raises(ValueError):
        Simulation(mini, dict(scene_id='elsewhere', survivors=[], ignitions=[], seed=1))


def test_routes_and_sight_respect_walls(mini):
    sim = Simulation(mini, dict(scene_id='mini', survivors=[], ignitions=[], seed=1))
    sim.observed[:] = 1
    path = sim.path((25, 2))
    assert path and all(mini.free(p) for p in path)
    assert any(p[0] == 17 and 6 <= p[1] <= 9 for p in path)   # crosses only at the doorway
    assert not line_visible(mini, (15, 2), (19, 2))


def fire_arrival(scene, cell, seconds=400):
    sim = Simulation(scene, dict(scene_id=scene.id, survivors=[], ignitions=[[15, 2]], seed=1))
    for step in range(int(seconds / .2)):
        sim.hazards(.2)
        assert np.all(sim.heat[~sim.free] == 0) and np.all(sim.smoke[~sim.free] == 0)
        if sim.heat[cell[1], cell[0]] > .5:
            return step * .2
    return None


def test_fire_crosses_only_through_doorway(tmp_path):
    with_door = load_bundle(write_bundle(tmp_path / 'door'))
    no_door = load_bundle(write_bundle(tmp_path / 'closed', door=False))
    across_wall, same_side = (18, 2), (12, 2)    # both 0.75 m from the ignition cell (15, 2)
    t_across, t_same = fire_arrival(with_door, across_wall), fire_arrival(with_door, same_side)
    assert t_across is not None and t_same is not None and t_across > t_same   # had to go round via the doorway
    assert fire_arrival(no_door, across_wall) is None


def test_fire_is_deterministic_and_burns_out(mini):
    a = Simulation(mini, dict(scene_id='mini', survivors=[], ignitions=[[10, 5]], seed=1))
    b = Simulation(mini, dict(scene_id='mini', survivors=[], ignitions=[[10, 5]], seed=99))
    peak = 0
    for _ in range(3000):                         # 600 s, the hazard preview limit
        a.hazards(.2); b.hazards(.2)
        peak = max(peak, int((a.heat > .3).sum()))
    assert np.array_equal(a.heat, b.heat) and np.array_equal(a.smoke, b.smoke)
    assert peak > 20 and a.heat.max() < .3        # spreads, then burns out (upstream PR #1 behaviour)
    assert round(a.hazard_time, 6) == 600


def test_material_fuel_and_residual_smoke():
    scenario = dict(survivors=[], ignitions=[[25, 18]], seed=1)
    inert = Simulation(scenario=scenario)
    inert.fuel.fill(0)
    combustible = Simulation(scenario=scenario)
    for _ in range(75):
        inert.hazards(.2)
        combustible.hazards(.2)
    assert inert.smoke.max() == 0
    assert combustible.smoke.max() > 0
    for _ in range(200):
        combustible.hazards(.2)
        if combustible.fire.max() == 0:
            break
    assert combustible.fire.max() == 0
    remaining = combustible.smoke.sum()
    assert remaining > 0
    for _ in range(150):
        combustible.hazards(.2)
    assert 0 < combustible.smoke.sum() < remaining


def test_mission_on_bundle_evacuates_through_exit(mini):
    scenario = dict(scene_id='mini', survivors=[[28, 3]], ignitions=[], seed=1)
    report = compare(mini, scenario)['reports']
    for mode in ('baseline', 'adaptive'):
        assert report[mode]['status'] == 'completed' and report[mode]['evacuated'] == 1


def test_mission_astar_strategy_is_exposed_on_sim_state(mini):
    sim = Simulation(mini, dict(scene_id='mini', survivors=[[28, 3]], ignitions=[], seed=1), strategy='mission-astar')
    state = sim.state()
    assert state['strategy'] == 'mission-astar'
    assert sim.strategy == 'mission-astar'


def test_mission_astar_urgency_uses_belief_and_raises_threatened_rooms(mini):
    from rescuetwin.strategies import URGENCY_GAIN, mission_targets, room_urgency
    sim = Simulation(mini, dict(scene_id='mini', survivors=[[28, 3]], ignitions=[], seed=1), strategy='mission-astar')
    assert all(u == 1. for u in room_urgency(sim).values())        # nothing observed: every room neutral
    room = next(r for r in sim.scene.rooms if sim.observe_points(r))
    weight = lambda: sum(t['p'] for t in mission_targets(sim) if t['room'] == room['id'])
    before = weight()
    x, y = room['cells'][0]
    sim.belief_heat[y, x] = 1.                                       # observed hazard inside one room
    assert room_urgency(sim)[room['id']] == pytest.approx(1. + URGENCY_GAIN)
    assert weight() == pytest.approx(before * (1. + URGENCY_GAIN))


def test_mission_astar_never_plans_room_searches_while_escorting(mini):
    from rescuetwin.strategies import mission_targets
    sim = Simulation(mini, dict(scene_id='mini', survivors=[[28, 3]], ignitions=[], seed=1), strategy='mission-astar')
    assert any(not t['person'] for t in mission_targets(sim))
    sim.escort = {'p0': 'det-0'}
    assert all(t['person'] for t in mission_targets(sim))


def test_baseline_strategy_never_computes_mission_urgency(mini):
    sim = Simulation(mini, dict(scene_id='mini', survivors=[[28, 3]], ignitions=[], seed=1))
    sim.status = 'running'
    for _ in range(50):
        sim.tick()
    assert sim.strategy == 'greedy' and 'room_urgency' not in sim.__dict__


def test_observations_carry_detection_ids_not_simulator_ids(mini):
    sim = Simulation(mini, dict(scene_id='mini', survivors=[[10, 7]], ignitions=[], seed=1))
    sim.status = 'running'
    for _ in range(200):
        sim.tick()
        if sim.detections:
            break
    text = json.dumps(sim.observations())
    assert 'person-' not in text and 'scenario' not in text and 'det-0' in text


def test_capacity_preset_uses_marketed_capacity(mini):
    preset = presets.build(mini, 'capacity-stress', seed=3)
    assert preset['capacity'] == 4 and len(preset['scenario']['survivors']) == 4
    assert 'not actual' in preset['note']
    Simulation(mini, preset['scenario'])          # placements validate
    assert preset['scenario'] == presets.build(mini, 'capacity-stress', seed=3)['scenario']


def test_capacity_preset_unavailable_without_costar(tmp_path):
    scene = load_bundle(write_bundle(tmp_path / 'nocap', capacity=None))
    entries = {p['id']: p for p in presets.available(scene)}
    assert entries['capacity-stress'].get('unavailable')
    Simulation(scene, entries['standard']['scenario'])


@pytest.mark.skipif(not (REAL / 'scene.json').exists(), reason='prepared sponsor bundle not present (data/ is ignored)')
def test_real_bundle_alignment_and_mission():
    scene = load_bundle(REAL)
    checks = scene.meta['checks']
    assert checks['passed'] and checks['mesh_walls_on_blocked_cells'] >= .95 and checks['rooms_reachable_from_exit']
    assert len(scene.exits) == 1 and scene.meta['exit_info'][0]['opening_width_m'] >= .6
    assert all((REAL / name).is_file() for name in scene.meta['assets'])
    assert (REAL / scene.meta['mesh']['file']).is_file()
    preset = presets.build(scene, 'standard')
    report = compare(scene, preset['scenario'])['reports']
    assert all(r['status'] in ('completed', 'accounted', 'withdrawn', 'unresolved') for r in report.values())


@pytest.mark.skipif(not KIT.exists() or not os.environ.get('RESCUETWIN_TEST_ANNOTATION'),
                    reason='set RESCUETWIN_TEST_ANNOTATION to a private apartment annotation')
def test_costar_context_provenance():
    import sys
    sys.path.insert(0, str(ROOT / 'scripts'))
    from inspect_sponsor import context
    annotation = json.loads(Path(os.environ['RESCUETWIN_TEST_ANNOTATION']).read_text())
    model, property_id, plan_id = (annotation[k] for k in ('model', 'property_id', 'floor_plan_id'))
    ctx = context(KIT, model, property_id, plan_id)
    fields = {f['key']: f for f in ctx['fields']}
    assert ctx['max_occupants'] is not None and fields['max_occupants']['field_path'].endswith('.maxOccupants')
    assert fields['sprinkler_type']['value'] is not None
    assert all('email' not in json.dumps(f).lower() for f in ctx['fields'])
    with pytest.raises(ValueError):
        context(KIT, model, property_id, -1)


def test_capacity_levels_deduplicate_and_keep_counts(mini):
    entries=[p for p in presets.available(mini,3) if p['id'].startswith('capacity-')]
    assert [p['requested_count'] for p in entries]==[1,2,4]
    for p in entries:
        assert p['placed_count']==len(p['scenario']['survivors'])
        Simulation(mini,p['scenario'])
    mini.meta['costar']['capacity_conflicts']=[{'value':9}]
    assert presets.build(mini,'capacity-stress') is None


def test_experiment_context_is_stable_and_reports_actual_placements(mini):
    scenario=presets.build(mini,'capacity-50',3)['scenario']
    context=presets.experiment_context(mini,scenario)
    assert context['placed_count']==2 and context['marketed_capacity']==4
    assert context['scenario_sha256']==presets.experiment_context(mini,dict(reversed(list(scenario.items()))))['scenario_sha256']
    scenario['seed']=4
    assert context['scenario_sha256']!=presets.experiment_context(mini,scenario)['scenario_sha256']
