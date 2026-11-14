from copy import deepcopy
import numpy as np
import pytest
from rescuetwin.core import Simulation,fixture,route,line_visible


def test_invalid_placements():
    s=Simulation()
    for point in ([0,0],[-1,2],[1.2,3]):
        scenario=deepcopy(s.scenario);scenario['survivors']=[point]
        with pytest.raises(ValueError):s.validate(scenario)


def test_wall_blocks_path_and_sight():
    s=fixture();sim=Simulation(s)
    path=sim.path((26,6))
    assert path and all(s.free(p) for p in path)
    assert not line_visible(s,(9,10),(11,10))


def test_belief_has_no_hidden_survivors_or_remote_heat():
    s=Simulation();s.sense();o=s.observations()
    assert not o['survivors']
    assert 'scenario' not in o
    assert o['heat'][18][25]==0 and s.heat[18][25]>.8


def test_fire_deterministic_and_wall_impermeable():
    a,b=Simulation(),Simulation()
    for _ in range(10):a.hazards(.2);b.hazards(.2)
    assert np.array_equal(a.heat,b.heat)
    assert all(a.heat[y][x]==0 for x,y in a.scene.walls)


def test_risk_route_avoids_heat_and_escort_requires_observation():
    s=Simulation();s.observed=s.grid(1)
    for x in range(3,8):s.belief_heat[6][x]=.5
    p=s.path((8,6));assert all(not(y==6 and 3<=x<8) for x,y in p)
    s.observed=s.grid();assert not s.path((8,6),True)


def test_escort_completion_and_reset():
    s=Simulation(scenario=dict(survivors=[[3,6]],ignitions=[],seed=2));s.status='running'
    for _ in range(100):
        s.tick()
        if s.status!='running':break
    assert s.status=='completed' and s.metrics['evacuated']==1
    assert Simulation(s.scene,s.scenario).time==0


def test_agent_rejects_hidden_survivor_and_stale_decision():
    from rescuetwin.agent import apply_decision
    s=Simulation();s.status='running'
    with pytest.raises(ValueError):apply_decision(s,dict(kind='survivor',id='person-0'),0)
    s.time=11
    with pytest.raises(ValueError):apply_decision(s,dict(kind='room',id='room-0'),0)


def test_local_interlock_stops_before_hazardous_cell():
    s=Simulation(scenario=dict(survivors=[[3,6]],ignitions=[[1,6]],seed=1))
    s.status='running';start=s.robot
    for _ in range(4):s.tick()
    s.heat[6][1]=1.
    s.tick()
    assert s.robot==start
    assert s.belief_heat[6][1]>0


def test_no_viable_escort_route_reports_unresolved():
    s=Simulation();s.status='running';p=s.survivors[0]
    s.robot=tuple(p['position']);d=s.add_evidence(s.scene.world(p['position']),1.,'test')
    s.pair();assert s.escort==p['id'] and d['status']=='escorting'
    s.observed=s.grid();s.sense=lambda:None;s.tick()
    assert s.status=='running' and s.holding                 # waits: upstream fire can burn out
    assert 'holding' in s.events[-1]['message']
    s.time=s.scene.time_limit;s.tick()
    assert s.status=='unresolved' and s.metrics['evacuated']==0
    assert 'not confirmed absent' in s.events[-1]['message']
