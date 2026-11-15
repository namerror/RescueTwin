import json
from pathlib import Path
import pytest

from rescuetwin.core import Simulation, fixture
from rescuetwin.humans import VARIANTS


def test_appearance_is_frozen_scenario_input_and_not_agent_evidence():
    scene=fixture()
    for variant in (*VARIANTS,'mixed'):
        scenario=dict(scene_id=scene.id,survivors=[],ignitions=[],seed=7,human_variant=variant)
        sim=Simulation(scene,scenario)
        assert sim.state()['scenario']['human_variant']==variant
        assert 'human_variant' not in json.dumps(sim.observations())
    with pytest.raises(ValueError,match='human_variant'):
        Simulation(scene,dict(scene_id=scene.id,survivors=[],ignitions=[],seed=7,human_variant='unknown'))
    from rescuetwin.presets import experiment_context
    base=dict(scene_id=scene.id,survivors=[],ignitions=[],seed=7)
    assert experiment_context(scene,base)['scenario_sha256']!=experiment_context(scene,{**base,'human_variant':'mixed'})['scenario_sha256']


def test_mesh_manifest_has_each_gender_age_variant_and_distinct_assets():
    path=Path(__file__).resolve().parents[1]/'frontend/public/models/humans.json'
    variants=json.loads(path.read_text())['variants']
    assert {v['id'] for v in variants}==set(VARIANTS)
    assert len({v['sha256'] for v in variants})==6
    assert len({v['source_asset'] for v in variants})==6
    assert {v['presentation'] for v in variants}=={'male','female'}
    assert max(v['height_m'] for v in variants if v['age_group']=='child')<min(v['height_m'] for v in variants if v['age_group']=='adult')


def test_api_appearance_roundtrip_and_validation():
    from fastapi.testclient import TestClient
    from rescuetwin import api
    client=TestClient(api.app);client.post('/api/control',json={'action':'reset'})
    body=dict(scene_id=api.sim.scene.id,survivors=[],ignitions=[],seed=7,human_variant='mixed')
    response=client.post('/api/scenario',json=body)
    assert response.status_code==200 and response.json()['scenario']['human_variant']=='mixed'
    assert client.post('/api/scenario',json={**body,'human_variant':'invalid'}).status_code==422
    del body['human_variant'];response=client.post('/api/scenario',json=body)
    assert 'human_variant' not in response.json()['scenario']
