"""Offline checks for the operator assets generated alongside a policy world."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


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


@pytest.mark.parametrize('ignitions', [[[0, 0]], [[6, 6]], [[1, 1]], [[13, 6], [13, 6]]])
def test_invalid_ignition_leaves_output_path_unused(tmp_path, prepare, ignitions):
    run = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='[Ii]gnition'):
        prepare(run, None, None, ignitions, 7)
    assert not run.exists()
