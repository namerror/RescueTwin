import math
from copy import deepcopy
import numpy as np
import pytest
from rescuetwin.core import Scene,Simulation,fixture,route,line_visible


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


def test_fire_spreads_through_opening_and_ignition_cannot_jump_wall():
    scenario = dict(survivors=[], ignitions=[[2, 3]], seed=7)
    wall = {(3, y) for y in range(7)}
    settings = dict(width=8, height=7, resolution=.15, exits=[(0, 3)],
                    default_material='upholstery')
    closed = Simulation(Scene(walls=wall, **settings), scenario)
    opened = Simulation(Scene(walls=wall - {(3, 3)}, **settings), scenario)
    assert closed.fire[3, 4] == 0
    peak = 0.
    for _ in range(200):
        closed.hazards(.2)
        opened.hazards(.2)
        peak = max(peak, opened.fire[3, 5])
    assert peak >= .12  # Active flame beyond the initial ignition footprint.
    assert not closed.heat[:, 4:].any()
    assert not closed.smoke[:, 4:].any()
    assert opened.smoke[:, 4:].max() > .015


def test_seeded_room_incident_ignites_cells_and_reset_clears_debris():
    scene = fixture()
    scenario = dict(survivors=[], ignitions=[], seed=7, incident_room='room-0')
    results = []
    for _ in range(2):
        sim = Simulation(scene, scenario)
        sim.status = 'running'
        while sim.incident is None and sim.time < 6:
            sim.tick()
        assert sim.incident is not None
        x, y = sim.incident['origin']
        assert sim.fire[y, x] > 0 and sim.smoke[y, x] > 0
        debris = sim.incident['debris']
        assert debris and not sim.scene.free(debris)
        assert debris not in sim.route
        assert Simulation(sim.base_scene, scenario).scene.free(debris)
        results.append(sim.incident)
    assert results[0] == results[1]


def test_incident_opens_partition_and_affects_people():
    scene = Scene(width=9, height=7, walls={(4, y) for y in range(7)},
                  exits=[(0, 3)], rooms=[dict(id='kitchen', name='Kitchen', cells=[(3, 3)])])
    scenario = dict(survivors=[[3, 3], [5, 3]], ignitions=[], seed=7, incident_room='kitchen')
    sim = Simulation(scene, scenario)
    sim.time = 6
    sim.advance_incident()
    assert (4, 3) in sim.incident['breached']
    assert sim.scene.free((4, 3)) and not scene.free((4, 3))
    assert sim.survivors[0]['status'] == 'casualty'
    assert sim.survivors[1]['injured'] and (5, 3) not in sim.immobile
    sim.robot = (5, 3)
    sim.observed[:] = 1
    sim.detections = [dict(id='d1', cell=(5, 3), position=scene.world((5, 3)), status='confirmed')]
    sim.pair()
    assert sim.survivors[1]['status'] == 'escorting'
    sim.heat.fill(0)
    sim.smoke.fill(0)
    sim.status = 'running'
    for _ in range(100):
        sim.tick()
        if sim.status != 'running':
            break
    assert sim.survivors[1]['status'] == 'evacuated'


def test_automatic_sprinkler_extinguishes_nearby_fire_without_crossing_wall():
    scene = fixture()
    scene.default_material = 'inert'
    sim = Simulation(scene, scenario=dict(survivors=[], ignitions=[[8, 10], [11, 10]], seed=7))
    sim.robot = (9, 10)
    sim.hazards(.2)
    assert sim.state()['sprinkler'] is None

    sim.status = 'running'
    sim.hazards(.2)
    spray = sim.state()['sprinkler']
    assert spray['origin'] == (9, 10)
    assert (8, 10) in spray['cells']
    assert (11, 10) not in spray['cells']
    assert (6, 8) not in spray['cells']
    assert .12 <= sim.state()['fire'][10][8] < sim.state()['fire'][10][11]

    for _ in range(30):
        sim.hazards(.2)
    assert sim.fire[10, 8] < .12
    assert sim.state()['sprinkler'] is None
    sim.robot = (6, 6)
    sim.hazards(.2)
    assert sim.fire[10, 8] < .12 <= sim.fire[10, 11]


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


def test_fire_proof_robot_crosses_fire_at_dash_speed(monkeypatch):
    from rescuetwin.core import ROBOT_DASH_SPEED, ROBOT_SPEED
    s=Simulation(scenario=dict(survivors=[[3,6]],ignitions=[[1,6]],seed=1))
    monkeypatch.setattr(s, 'automatic_sprinkler', lambda: False)
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
    s.observed[:]=1;s.sense=lambda:None
    s.robot=tuple(p['position']);s.add_evidence(s.scene.world(p['position']),1.,'test');s.pair()
    s.tick()
    assert s.route;q=s.route[0];s.heat[q[1]][q[0]]=1.;s.progress=s.scene.resolution
    here=s.robot;s.tick()
    assert s.robot==here and 'interlock' in s.events[-1]['message']


def test_cornered_escort_requests_assistance_and_never_relaxes_limits():
    s=Simulation();s.status='running';p=s.survivors[0]
    s.observed[:]=1;s.sense=lambda:None;s.hazards=lambda dt:None
    s.robot=tuple(p['position']);s.add_evidence(s.scene.world(p['position']),1.,'test');s.pair()
    assert p['id'] in s.escort                                   # route existed at contact
    s.belief_heat[:]=.9;s.heat[:]=.9;s.hazard_seen[:]=s.time     # then everything catches fire
    start=s.robot
    while s.time<120 and s.status=='running':s.tick()            # well past any former timeout
    assert s.holding and s.robot==start and not s.route         # never escorts through human-unsafe cells
    assert s.metrics['assistance_requests']==1                  # reported once, not every tick
    assert sum('Assistance requested' in e['message'] for e in s.events)==1


def test_no_viable_escort_route_at_contact_hands_off_and_reports():
    # Upstream test, updated for the constitution: no observed route at contact -> report and request responders.
    s=Simulation();s.status='running';p=s.survivors[0]
    s.robot=tuple(p['position']);d=s.add_evidence(s.scene.world(p['position']),1.,'test')
    s.pair()                                                       # nothing observed yet: no verified escort route
    assert not s.escort and d['status']=='handoff' and p['status']=='assistance-requested'
    assert s.reports[-1]['kind']=='assistance-request' and 'no route' in s.reports[-1]['action']
    s.time=s.scene.time_limit;s.tick()
    assert s.status=='unresolved' and s.metrics['evacuated']==0
    assert s.reports[-1]['kind']=='mission-end'


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

def _twin_rooms_scene(costar=None):
    """Two identical rooms at equal distance either side of the start; ties go to the left room."""
    from rescuetwin.core import Scene
    s=Scene(id='risk-test',name='Risk test',width=31,height=5,resolution=.5,meta={'costar':costar} if costar else {})
    s.walls={(x,y) for x in range(31) for y in range(5) if y in (0,4) or x in (0,30)}
    s.exits=[(15,2)];s.robot_start=(15,2)
    s.rooms=[dict(id='left',name='Left',label='Bedroom',cells=[(c,2) for c in range(8,11)],observe=[(9,2)],area_m2=4.),
             dict(id='right',name='Right',label='Bedroom',cells=[(c,2) for c in range(20,23)],observe=[(21,2)],area_m2=4.)]
    return s


def _first_target(scene,room_risk=None):
    scenario=dict(scene_id=scene.id,survivors=[],ignitions=[],seed=1,**({'room_risk':room_risk} if room_risk else {}))
    sim=Simulation(scene,scenario);sim.observed[:]=1
    return sim.choose()[-1]


def test_room_risk_orders_gas_then_neither_then_sprinkler():
    s=_twin_rooms_scene()
    assert _first_target(s)==(9,2)                                              # untagged: tie, left first
    assert _first_target(s,{'right':'gas_heater'})==(21,2)                      # gas heater beats neither
    assert _first_target(s,{'left':'sprinkler'})==(21,2)                        # neither beats sprinkler
    assert _first_target(s,{'left':'sprinkler','right':'gas_heater'})==(21,2)
    assert _first_target(s,{'left':'both','right':'sprinkler'})==(9,2)          # 1.2x beats 0.8x
    sim=Simulation(s,dict(scene_id=s.id,survivors=[],ignitions=[],seed=1,room_risk={'right':'gas_heater'}))
    assert {r['id']:r['tag'] for r in sim.observations()['risk_map']}=={'left':'unknown','right':'gas_heater'}
    assert any('Room risk map' in e['message'] for e in sim.events)


def test_room_risk_building_default_keeps_unknown_and_validates():
    from rescuetwin import risk
    field=lambda v:dict(fields=[dict(key='has_sprinklers',value=v,dataset='commercial-property',source_id=1,field_path='hasSprinklers')])
    assert risk.building_default(_twin_rooms_scene())[0]=='unknown'
    assert risk.building_default(_twin_rooms_scene(field(None)))[0]=='unknown'  # null is unknown, never "no"
    assert risk.building_default(_twin_rooms_scene(field(False)))[0]=='unknown' # no gas data either
    yes=_twin_rooms_scene(field(True))
    assert risk.building_default(yes)[0]=='sprinkler'
    assert _first_target(yes,{'left':'neither'})==(9,2)                         # operator override beats building default
    s=_twin_rooms_scene()
    for bad in ({'kitchen':'gas_heater'},{'left':'unknown'},{'left':'propane'},['left']):
        with pytest.raises(ValueError,match='room_risk'):
            Simulation(s,dict(scene_id=s.id,survivors=[],ignitions=[],seed=1,room_risk=bad))


def test_two_nearby_confirmed_survivors_escort_together():
    sim = Simulation(scenario=dict(survivors=[[3,6],[3,7],[4,6]], ignitions=[], seed=1))
    sim.robot = (3,6)
    sim.observed[:] = 1   # constitution: an escort needs an observed route to a verified exit at contact
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

def test_hosted_agent_instruction_carries_the_constitution():
    from rescuetwin.agent import system_instruction
    text=system_instruction()
    assert 'A timeout never grants permission to exceed those limits.' in text and 'select_goal' in text


# ---- Constitution support: reports, mobility, room states, second viewpoint, withdrawal ----

def _contact(s, cell):
    s.observed[:]=1;s.sense=lambda:None
    s.robot=tuple(cell);d=s.add_evidence(s.scene.world(cell),1.,'test');s.pair()
    return d


def test_person_who_cannot_follow_is_reported_and_search_continues():
    s=Simulation(scenario=dict(survivors=[[26,6],[15,18]],ignitions=[],seed=1,immobile=[[26,6]]));s.status='running'
    d=_contact(s,(26,6))
    assert not s.escort and d['status']=='handoff' and s.metrics['handoffs']==1
    req=[r for r in s.reports if r['kind']=='assistance-request']
    assert req and req[0]['mobility']=='cannot follow' and req[0]['last_usable_approach']==[26,6] and 'responders' in req[0]['action']
    assert next(p for p in s.survivors if p['id']=='person-0')['status']=='assistance-requested'
    s.tick();assert s.status=='running'                                  # keeps searching for the other person


def test_mobility_is_unknown_until_contact():
    s=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1));s.status='running'
    s.add_evidence(s.scene.world((26,6)),1.,'test')
    assert s.reports[-1]['kind']=='confirmed-person' and s.reports[-1]['mobility']=='unknown until contact'


def test_reports_queue_during_comms_outage_and_need_acknowledgement():
    s=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1,comms_outages=[[0,5]]));s.status='running'
    r=s.report('confirmed-person',(26,6),action='test',confirmed=1)
    assert r['status']=='queued'
    with pytest.raises(ValueError):s.acknowledge(r['id'])
    s.time=5.0;s.deliver_reports()
    assert r['status']=='delivered' and r['attempts']>=2 and any('restored' in e['message'] for e in s.events)
    s.acknowledge(r['id']);assert r['status']=='acknowledged'
    with pytest.raises(ValueError):Simulation(scenario=dict(survivors=[],ignitions=[],seed=1,comms_outages=[[5,1]]))
    with pytest.raises(ValueError):Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1,immobile=[[3,3]]))


def test_partly_observed_room_tries_another_viewpoint_then_reports_reason():
    from rescuetwin.core import Scene
    sc=Scene(id='rooms',name='Rooms · Floor 1',width=12,height=8,resolution=.5)
    sc.walls={(x,y) for x in range(12) for y in range(8) if x in (0,11) or y in (0,7)}|{(6,y) for y in range(1,6)}
    sc.exits=[(1,1)];sc.robot_start=(1,1)
    cells=[(x,y) for x in range(1,11) for y in range(1,7) if (x,y) not in sc.walls]
    sc.rooms=[dict(id='r',name='Hall',label='Living room',cells=cells,observe=[(2,3)])]
    s=Simulation(sc,dict(scene_id='rooms',survivors=[],ignitions=[],seed=1));s.status='running'
    s.observed[:]=0;s.visited.add((2,3));s.observed[3,2]=1
    s.update_rooms()
    assert s.rooms_state['r']['viewpoints_tried']==1 and s.extra_views
    for _,c in s.extra_views:s.visited.add(c)
    s.rooms_state['r']['viewpoints_tried']=2;s.update_rooms()
    assert s.rooms_state['r']['status']=='partly-observed' and 'visibility' in s.rooms_state['r']['reason']


def test_unconfirmed_detection_gets_second_viewpoint_or_is_reported_possible():
    s=Simulation(scenario=dict(survivors=[],ignitions=[],seed=1),perception='image-yolo');s.status='running'
    s.observed[:]=1
    d=s.add_evidence(s.scene.world((15,6)),.6,'yolo');assert d['status']=='candidate'
    s.time+=5;costs,_=s.tree()
    det,view=s.recheck_target(costs)
    assert det is d and view is not None and math.dist(s.scene.world(view),d['position'])<=3.0
    d['rechecking']=True;s.scan_done()
    assert d['status']=='possible' and s.reports[-1]['kind']=='possible-detection' and s.metrics['possible_detections']==1


def test_withdrawal_before_time_limit_and_decisions_are_recorded():
    s=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1));s.status='running'
    for _ in range(20):s.tick()
    assert s.decisions and {'time','decision','trigger','options','reason'}<=set(s.decisions[0])
    s.time=s.scene.time_limit-8;s.tick()                       # return (~4 s) + 15 s margin > 8 s left
    assert s.withdraw_reason and 'time limit' in s.withdraw_reason
    while s.status=='running' and s.time<s.scene.time_limit+5:s.tick()
    assert s.status=='withdrawn' and s.reports[-1]['kind']=='mission-end' and 'outstanding_rooms' in s.reports[-1]


def test_agent_context_is_belief_only_and_goals_are_committed():
    import json as _json
    from rescuetwin.agent import decision_options, needs_decision, summarize
    a=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1));a.status='running'
    b=Simulation(scenario=dict(survivors=[[5,18]],ignitions=[],seed=1));b.status='running'
    ctx_a,ctx_b=decision_options(a),decision_options(b)
    assert ctx_a==ctx_b                                           # survivor placement changes nothing the model sees
    text=_json.dumps([ctx_a,summarize(a.observations())])
    assert 'person-' not in text and 'scenario' not in text
    assert {'travel_cost_m','likelihood_weight','state'}<=set(ctx_a['rooms'][0])
    assert needs_decision(a)
    a.goal=(15,6);assert not needs_decision(a)                    # committed until reached
    a.robot=(15,6);assert needs_decision(a)


def test_agent_cannot_deadlock_a_paused_clock_on_the_robots_own_cell():
    from rescuetwin.agent import apply_decision, decision_options, wants_decision
    s=Simulation(scenario=dict(survivors=[[26,6]],ignitions=[],seed=1));s.status='running'
    room=s.scene.rooms[0];s.robot=tuple(s.observe_points(room)[0])        # standing on the room's only point
    with pytest.raises(ValueError,match='remaining observation point'):
        apply_decision(s,dict(kind='room',id=room['id'],reason='x'),s.time)
    assert next(r for r in decision_options(s)['rooms'] if r['id']==room['id'])['remaining_observation_points']==0
    other=s.scene.rooms[3];apply_decision(s,dict(kind='room',id=other['id'],reason='x'),s.time)
    assert not wants_decision(s)                                            # committed goal, and a tick is owed first
    s.goal=None;assert not wants_decision(s)                                # still owed a tick
    s.tick();assert s.time>=s.agent_backoff_until
