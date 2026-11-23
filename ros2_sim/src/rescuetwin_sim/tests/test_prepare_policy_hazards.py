"""Offline checks for the operator assets generated alongside a policy world."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


MESH_URI = "world/model[@name='scan_floor']/link/visual/geometry/mesh/uri"


@pytest.fixture
def prepare(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import prepare_policy_run

    monkeypatch.setattr(prepare_policy_run, 'instrument_robot', lambda _, target: target.mkdir(parents=True))
    return prepare_policy_run.prepare


def test_prepared_ignition_matches_grid_and_has_local_textures(tmp_path, prepare):
    run = tmp_path / 'hazard'
    prepare(run, None, None, [[13, 6]], 17)
    config = json.loads((run / 'policy.json').read_text())
    assert config['hazard_visuals'] == {'version': 1, 'ceiling_m': 2.4}
    assert config['seed'] == 17 and config['ignitions'] == [[13, 6]]
    world = ET.parse(run / 'world.sdf')
    emitters = world.findall("world/model[@name='rescuetwin_hazard_effects']/link/particle_emitter")
    active = [emitter for emitter in emitters if emitter.findtext('emitting') == 'true']
    assert len(emitters) == 56
    assert {emitter.attrib['name'] for emitter in active} == {'flame_0', 'core_0'}
    assert all(emitter.findtext('pose').split()[:2] == ['3.375', '1.625'] for emitter in active)
    assert all((run / emitter.findtext('material/pbr/metal/albedo_map')).is_file()
               for emitter in emitters)
    assert world.find(MESH_URI) is None
    assert not (run / 'scan_render').exists()


@pytest.fixture
def private_floor(tmp_path, prepare):
    from prepare_policy_run import PUBLIC_SPAWN, public_floor

    floor = tmp_path / 'floor'
    scan = floor / 'scan'
    scan.mkdir(parents=True)
    (scan / 'wall.mtl').write_text('newmtl paint\nmap_Kd wall.png\n')
    (scan / 'wall.png').write_bytes(b'synthetic texture link target')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    scene = public_floor()
    scene['mesh']['file'] = 'wall.obj'
    scene['assets'] = {name: {} for name in ('wall.obj', 'wall.mtl', 'wall.png')}
    (bundle / 'scene.json').write_text(json.dumps(scene))
    (floor / 'source.json').write_text(json.dumps({'bundle': str(bundle), 'spawn_xy_yaw': PUBLIC_SPAWN}))
    return floor


@pytest.mark.parametrize('ignitions', [[], [[13, 6]]])
def test_private_run_adds_normals_to_local_render_copy(tmp_path, prepare, private_floor, ignitions):
    floor = private_floor
    source = floor / 'scan/wall.obj'
    geometry = ('mtllib wall.mtl\ng panel\nv 0 0 0\nv 0 1 0\nv 0 1 1\nv 0 0 1\n'
                'vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nusemtl paint\n')
    original = (geometry + 'f 1/1 2/2 3/3 4/4\nvn 0 1 0\n'
                'f -1/4 3/3/-1 2/2 # mixed\nvn -1 0 0\n'
                'f 1/1 1/1 1/1\nf 1/1/1 2/2/1 3/3/2\n')
    source.write_text(original)
    run = tmp_path / 'policy'
    prepare(run, None, floor, ignitions, 17)
    world = ET.parse(run / 'world.sdf')
    assert world.findtext(MESH_URI) == 'scan_render/wall.obj'
    assert world.findtext("world/plugin[@name='gz::sim::systems::Sensors']/render_engine") == 'ogre2'
    assert (run / 'scan').resolve() == (floor / 'scan').resolve()
    render = run / 'scan_render'
    assert (render / 'wall.obj').read_text() == (
        'vn 0 1 0\nvn -1 0 0\n' + geometry +
        'vn 1 0 0\nf 1/1/3 2/2/3 3/3/3 4/4/3\n'
        'vn -1 0 0\nf -1/4/4 3/3/1 2/2/4 # mixed\n'
        'vn 0 0 1\nf 1/1/5 1/1/5 1/1/5\nf 1/1/1 2/2/1 3/3/2\n')
    assert all((render / name).resolve() == (floor / 'scan' / name).resolve()
               for name in ('wall.mtl', 'wall.png'))
    assert source.read_text() == original


def test_private_run_reuses_mesh_with_complete_normals(tmp_path, prepare, private_floor):
    original = ('mtllib wall.mtl\nv 0 0 0\nv 0 1 0\nv 0 0 1\nvt 0 0\nvt 1 0\nvt 0 1\n'
                'vn 1 0 0\nusemtl paint\nf 1/1/-1 2/2/-1 3/3/-1\n')
    (private_floor / 'scan/wall.obj').write_text(original)
    run = tmp_path / 'policy'
    prepare(run, None, private_floor, [], 17)
    assert ET.parse(run / 'world.sdf').findtext(MESH_URI) == 'scan/wall.obj'
    assert (run / 'scan/wall.obj').read_text() == original
    assert not (run / 'scan_render').exists()


@pytest.mark.parametrize('ignitions', [[[0, 0]], [[6, 6]], [[1, 1]], [[13, 6], [13, 6]]])
def test_invalid_ignition_leaves_output_path_unused(tmp_path, prepare, ignitions):
    run = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='[Ii]gnition'):
        prepare(run, None, None, ignitions, 7)
    assert not run.exists()
