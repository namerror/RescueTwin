from fastapi.testclient import TestClient
from rescuetwin.api import app


def test_controls_and_observation_boundary():
    with TestClient(app) as client:
        assert client.post('/api/control',json={'action':'reset'}).status_code==200
        assert 'scenario' not in client.get('/api/observations').json()
        assert client.post('/api/scenario',json={'survivors':[[3,6]],'ignitions':[],'seed':1}).status_code==200
        assert client.post('/api/control',json={'action':'start'}).status_code==200
        assert client.post('/api/scenario',json={'survivors':[],'ignitions':[],'seed':1}).status_code==409
        assert client.post('/api/control',json={'action':'pause'}).status_code==200
        assert client.post('/api/control',json={'action':'reset'}).status_code==200
        assert client.post('/api/control',json={'action':'destroy'}).status_code==422


def test_invalid_scenario_does_not_replace_state():
    with TestClient(app) as client:
        client.post('/api/control',json={'action':'reset'})
        before=client.get('/api/state').json()['scenario']
        assert client.post('/api/scenario',json={'survivors':[[0,0]],'ignitions':[],'seed':1}).status_code==422
        assert client.get('/api/state').json()['scenario']==before


def test_operator_websocket_snapshot():
    with TestClient(app) as client:
        with client.websocket_connect('/api/stream') as stream:
            state=stream.receive_json()
            assert state['scene_id']=='development-apartment'
            assert 'scenario' in state
        assert client.get('/api/scene').json()['source']=='synthetic'


def test_hazard_preview_runs_without_robot_mission():
    with TestClient(app) as client:
        client.post('/api/control',json={'action':'reset'})
        state=client.post('/api/control',json={'action':'toggle_hazards'}).json()
        assert state['hazards_running'] and state['status']=='paused'
        assert client.post('/api/scenario',json={'survivors':[],'ignitions':[],'seed':1}).status_code==409
        assert client.post('/api/control',json={'action':'start'}).status_code==200
        assert client.post('/api/control',json={'action':'toggle_hazards'}).status_code==409   # robot running
        paused=client.post('/api/control',json={'action':'pause'}).json()
        assert not paused['hazards_running']
        client.post('/api/control',json={'action':'reset'})


def test_building_use_presets_and_scenario_roundtrip():
    from fastapi.testclient import TestClient
    from rescuetwin import api
    client = TestClient(api.app)
    client.post('/api/control', json={'action': 'reset'})
    uses = client.get('/api/building-uses').json()
    assert {p['id'] for p in uses['profiles']} >= {'residential', 'assembly'} and uses['inferred'] == 'residential'
    plain = client.get('/api/presets').json()[0]['scenario']
    assert 'building_use' not in plain                                   # unchanged hashes for existing scenarios
    studio = client.get('/api/presets?building_use=assembly').json()[0]['scenario']
    assert studio['building_use'] == 'assembly'
    state = client.post('/api/scenario', json=studio).json()
    assert state['scenario']['building_use'] == 'assembly'
    assert client.get('/api/presets?building_use=casino').status_code == 422
    client.post('/api/scenario', json=plain)
