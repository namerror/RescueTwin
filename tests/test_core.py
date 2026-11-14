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


def test_fire_proof_robot_crosses_fire_at_dash_speed():
    from rescuetwin.core import ROBOT_DASH_SPEED, ROBOT_SPEED
    s=Simulation(scenario=dict(survivors=[[3,6]],ignitions=[[1,6]],seed=1))
    s.status='running'
    for _ in range(4):s.tick()
    q=s.route[0];s.heat[q[1]][q[0]]=1.;s.progress=0
    before=s.robot;s.tick()
    assert s.progress==pytest.approx(ROBOT_DASH_SPEED*.2) and ROBOT_DASH_SPEED>ROBOT_SPEED
    for _ in range(3):s.tick()
    assert s.robot!=before and 'interlock' not in ' '.join(e['message'] for e in s.events)
    assert s.metrics['robot_hazard_s']>0


def test_escort_interlock_still_protects_people():
    s=Simulation(scenario=dict(survivors=[[3,6]],ignitions=[[1,6]],seed=1))
    s.status='running';p=s.survivors[0]
    s.robot=tuple(p['position']);s.add_evidence(s.scene.world(p['position']),1.,'test');s.pair()
    s.observed[:]=1;s.sense=lambda:None;s.tick()
    assert s.route;q=s.route[0];s.heat[q[1]][q[0]]=1.;s.progress=s.scene.resolution
    here=s.robot;s.tick()
    assert s.robot==here and 'interlock' in s.events[-1]['message']


def test_cornered_escort_takes_least_fire_route_after_timeout():
    from rescuetwin.core import ESCORT_LAST_RESORT_S
    s=Simulation();s.status='running';p=s.survivors[0]
    s.robot=tuple(p['position']);s.add_evidence(s.scene.world(p['position']),1.,'test');s.pair()
    s.observed[:]=1;s.sense=lambda:None;s.hazards=lambda dt:None
    s.belief_heat[:]=.9;s.heat[:]=.9; s.hazard_seen[:]=s.time   # everything on fire: no human-safe route
    s.tick();assert s.holding and not s.last_resort
    while s.time<ESCORT_LAST_RESORT_S+.5:s.tick()
    assert s.last_resort and s.metrics['last_resort_escorts']==1 and s.route
    assert any('least-fire route' in e['message'] for e in s.events)


def test_no_viable_escort_route_reports_unresolved():
    s=Simulation();s.status='running';p=s.survivors[0]
    s.robot=tuple(p['position']);d=s.add_evidence(s.scene.world(p['position']),1.,'test')
    s.pair();assert s.escort=={p['id']:d['id']} and d['status']=='escorting'
    s.observed=s.grid();s.sense=lambda:None;s.tick()
    assert s.status=='running' and s.holding                 # waits: upstream fire can burn out
    assert 'holding' in s.events[-1]['message']
    s.time=s.scene.time_limit;s.tick()
    assert s.status=='unresolved' and s.metrics['evacuated']==0
    assert 'not confirmed absent' in s.events[-1]['message']


@pytest.mark.demo_pacing
def test_demo_pacing_is_a_pure_time_rescale_of_the_hazard_model():
    import rescuetwin.core as core
    scale=core.HAZARD_TIME_SCALE;assert 0<scale<1
    paced=Simulation(scenario=dict(survivors=[],ignitions=[[25,18]],seed=1))
    for _ in range(300):paced.hazards(.2)                 # 60 s of mission time
    try:
        core.HAZARD_TIME_SCALE=1.0
        model=Simulation(scenario=dict(survivors=[],ignitions=[[25,18]],seed=1))
        for _ in range(300):model.hazards(.2*scale)        # 60*scale s of teammate model time
    finally:core.HAZARD_TIME_SCALE=scale
    assert paced.hazard_time==pytest.approx(60*scale)
    assert np.allclose(paced.heat,model.heat) and np.allclose(paced.smoke,model.smoke)


def _rooms_scene():
    """Two rooms either side of the start: a small one close by and a large main hall further away."""
    from rescuetwin.core import Scene
    s=Scene(id='prior-test',name='Prior test',width=30,height=5,resolution=.5)
    s.walls={(x,y) for x in range(30) for y in range(5) if y in (0,4) or x in (0,29)}
    s.exits=[(15,2)];s.robot_start=(15,2)
    s.rooms=[dict(id='small',name='Small',label='Bedroom',cells=[(c,2) for c in range(11,14)],observe=[(12,2)],area_m2=4.),
             dict(id='hall',name='Hall',label='Living room',cells=[(c,r) for c in range(19,28) for r in (1,2,3)],observe=[(24,2)],area_m2=60.)]
    return s


def test_building_use_prior_changes_search_order():
    s=_rooms_scene()
    house=Simulation(s,dict(scene_id='prior-test',survivors=[],ignitions=[],seed=1))
    studio=Simulation(s,dict(scene_id='prior-test',survivors=[],ignitions=[],seed=1,building_use='assembly'))
    for sim in (house,studio):sim.observed[:]=1
    assert house.prior['use']=='residential' and house.choose()[-1]==(12,2)      # anyone anywhere: nearest first
    assert studio.prior['main_room']=='hall' and studio.choose()[-1]==(24,2)    # yoga class: main room first
    assert any('Search prior' in e['message'] for e in studio.events)


def test_prior_ignores_survivor_placements_and_validates_use():
    s=_rooms_scene()
    a=Simulation(s,dict(scene_id='prior-test',survivors=[[12,2]],ignitions=[],seed=1,building_use='assembly'))
    b=Simulation(s,dict(scene_id='prior-test',survivors=[[26,3]],ignitions=[],seed=1,building_use='assembly'))
    assert a.prior==b.prior
    with pytest.raises(ValueError,match='building use'):
        Simulation(s,dict(scene_id='prior-test',survivors=[],ignitions=[],seed=1,building_use='casino'))

def test_two_nearby_confirmed_survivors_escort_together():
    sim = Simulation(scenario=dict(survivors=[[3,6],[3,7],[4,6]], ignitions=[], seed=1))
    sim.robot = (3,6)
    first = sim.add_evidence(sim.scene.world((3,6)), 1., 'analytic-fixture')
    second = sim.add_evidence(sim.scene.world((3,7)), 1., 'analytic-fixture')
    sim.pair()
    assert set(sim.escort.values()) == {first['id'], second['id']}
    assert sim.survivors[2]['status'] == 'unobserved'

    sim.status = 'running'
    sim.observed[:] = 1
    sim.sense = lambda: None
    for _ in range(50):
        sim.tick()
        if sim.metrics['evacuated']:
            break
    assert sim.metrics['evacuated'] == 2
    assert not sim.escort
    assert sim.survivors[2]['status'] == 'unobserved'
