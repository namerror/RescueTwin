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


def test_reports_ack_withdraw_and_scenario_fields():
    from fastapi.testclient import TestClient
    from rescuetwin import api
    client = TestClient(api.app)
    client.post('/api/control', json={'action': 'reset'})
    base = client.get('/api/presets').json()[0]['scenario']
    extra = dict(base, immobile=[base['survivors'][0]], comms_outages=[[0, 2]])
    state = client.post('/api/scenario', json=extra).json()
    assert state['scenario']['immobile'] == [base['survivors'][0]] and state['scenario']['comms_outages'] == [[0, 2]]
    api.sim.status = 'running'
    r = api.sim.report('confirmed-person', tuple(base['survivors'][1]), action='test')
    assert r['status'] == 'queued'                                              # comms outage at t=0
    assert client.post(f"/api/reports/{r['id']}/ack").status_code == 409        # not delivered yet
    api.sim.time = 3; api.sim.deliver_reports()
    assert client.post(f"/api/reports/{r['id']}/ack").json()['reports'][-1]['status'] == 'acknowledged'
    assert client.post('/api/control', json={'action': 'withdraw'}).json()['withdraw_reason'] == 'operator stop'
    client.post('/api/control', json={'action': 'reset'}); client.post('/api/scenario', json=base)
    assert 'immobile' not in client.get('/api/state').json()['scenario']


def test_agent_env_loading_scrubbing_and_toggle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from rescuetwin import api
    from rescuetwin.agent import load_agent_env, scrub
    env = tmp_path / '.env'
    env.write_text('MP_API_TOKEN=not-for-agent\nGEMINI_API_KEY="abc123secret"\nexport GEMINI_MODEL=model-x\n')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False); monkeypatch.setenv('GEMINI_MODEL', 'already-set')
    load_agent_env(env)
    import os
    assert os.environ['GEMINI_API_KEY'] == 'abc123secret' and os.environ['GEMINI_MODEL'] == 'already-set'
    assert 'MP_API_TOKEN' not in os.environ or os.environ['MP_API_TOKEN'] != 'not-for-agent'
    assert 'abc123secret' not in scrub('request failed for key abc123secret')
    client = TestClient(api.app)
    info = client.get('/api/agent').json()
    assert 'GEMINI_API_KEY' not in str(info) and 'abc123secret' not in str(info)
    assert client.post('/api/agent', json={'enabled': False}).json()['enabled'] is False


def test_decision_quality_mode_holds_clock_only_while_waiting_on_the_model(monkeypatch):
    from fastapi.testclient import TestClient
    from rescuetwin import api
    client = TestClient(api.app)
    client.post('/api/control', json={'action': 'reset'})
    monkeypatch.setitem(api.agent_status, 'state', 'ready')
    info = client.post('/api/agent', json={'enabled': True, 'pause_clock': True}).json()
    assert info['pause_clock'] and 'not real-time' in api.sim.events[-1]['message']
    api.sim.status = 'running'
    assert api.inference_hold(api.sim)                    # no committed goal: wait for the model
    api.sim.goal = (15, 6)
    assert not api.inference_hold(api.sim)                # committed goal: clock runs
    monkeypatch.setitem(api.agent_status, 'thinking', True)
    assert api.inference_hold(api.sim)                    # model answering: clock frozen
    monkeypatch.setitem(api.agent_status, 'thinking', False); api.sim.goal = None
    api.sim.agent_backoff_until = api.sim.time + 5
    assert not api.inference_hold(api.sim)                # after a failed call the built-in planner steers
    api.sim.agent_backoff_until = 0
    client.post('/api/agent', json={'pause_clock': False})
    assert not api.inference_hold(api.sim)                # real-time mode never holds
    monkeypatch.setitem(api.agent_status, 'enabled', False)
    client.post('/api/control', json={'action': 'reset'})


def test_room_risk_scenario_and_risk_map():
    with TestClient(app) as client:
        client.post('/api/control',json={'action':'reset'})
        room=client.get('/api/scene').json()['rooms'][0]['id']
        body={'survivors':[],'ignitions':[],'seed':1,'room_risk':{room:'gas_heater'}}
        state=client.post('/api/scenario',json=body).json()
        assert state['scenario']['room_risk']=={room:'gas_heater'}
        mapped=client.get('/api/risk-map').json()
        assert {t['id']:t['factor'] for t in mapped['tags']}=={'gas_heater':1.5,'sprinkler':.8,'both':1.2,'neither':1.}
        tagged=next(r for r in mapped['rooms'] if r['id']==room)
        assert tagged['tag']=='gas_heater' and tagged['source']=='operator'
        assert all(r['tag']=='unknown' for r in mapped['rooms'] if r['id']!=room)
        assert client.post('/api/scenario',json={**body,'room_risk':{room:'propane'}}).status_code==422
        client.post('/api/scenario',json={'survivors':[],'ignitions':[],'seed':1})
        assert 'room_risk' not in client.get('/api/state').json()['scenario']


def test_run_speed_is_wall_clock_only_and_validated():
    import rescuetwin.api as api
    with TestClient(app) as client:
        original = api.run_speed
        try:
            assert client.post('/api/speed', json={'speed': 4}).json()['run_speed'] == 4
            assert client.get('/api/state').json()['run_speed'] == 4
            assert client.post('/api/speed', json={'speed': 3}).status_code == 422
            assert client.post('/api/speed', json={'speed': 0}).status_code == 422
            assert api.TICK == .2          # simulated timestep is unchanged by the pace
        finally:
            api.run_speed = original
