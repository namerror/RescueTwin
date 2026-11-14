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
            assert state['scene']['source']=='synthetic'
            assert 'scenario' in state
