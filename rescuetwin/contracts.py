"""Agent evidence is an allowlist, independent of operator truth and scenario inputs."""
from copy import deepcopy
from .core import ESCORT_HEAT_LIMIT, ESCORT_SMOKE_LIMIT
from .recording import digest

def observation(sim):
    samples=[]
    for y in range(sim.scene.height):
        for x in range(sim.scene.width):
            for modality in ('heat','smoke'):
                timestamps=getattr(sim, modality+'_sampled_at', sim.hazard_seen)
                t=float(timestamps[y,x])
                if t < 0: continue
                value=float(getattr(sim,'belief_'+modality)[y,x])
                samples.append(dict(id=f'{modality}-{x}-{y}-{t:.3f}',cell=[x,y],modality=modality,
                                    value=round(value,3),unit='normalized_simulation',observed_at_s=t,
                                    source='analytic_local_simulated_sensor',measurement_uncertainty=None))
    result=dict(schema='rescuetwin.observation/1',time=sim.time,robot=list(sim.robot),heading=sim.heading,
                map_id=sim.base_scene.id,topology=dict(rooms=[dict(id=r['id'],name=r['name'],viewpoints=[list(p) for p in sim.observe_points(r)]) for r in sim.base_scene.rooms],exits=[list(e) for e in sim.base_scene.exits]),hazard_sensor=sim.hazard_sensor(),remaining_time_s=max(0,sim.scene.time_limit-sim.time),
                reports=[{k:r.get(k) for k in ('id','kind','status','time','room','action','confirmed','possible','mobility')} for r in sim.reports],
                fixture_provenance=[{k:f.get(k) for k in ('key','dataset','source_id','record_type','field_path','value','units','derivation')} for f in ((sim.base_scene.meta.get('costar') or {}).get('fields') or []) if f.get('key','').startswith('gas_') or f.get('key') in ('has_sprinklers','sprinkler_type')],
                rooms=deepcopy(list(sim.rooms_state.values())),risk=deepcopy(list(sim.risk.values())),
                tracks=deepcopy(sim.detections),samples=samples,observed=sim.observed.astype(int).tolist(),
                escort_count=len(sim.escort),withdraw_reason=sim.withdraw_reason,perception=sim.perception_mode,sensor_health=dict(degraded=sim.degraded,waiting_for_frame=sim.needs_frame()),
                constraints=dict(escort_heat_lt=ESCORT_HEAT_LIMIT,escort_smoke_lt=ESCORT_SMOKE_LIMIT,escort_observed_only=True))
    result['snapshot_id']=digest(result)
    return result

def compact(sim):
    o=observation(sim)
    samples=o.pop('samples');o.pop('observed')
    o['hazards_by_room']=[]
    for room in sim.scene.rooms:
        cells=set(room['cells']); local=[s for s in samples if tuple(s['cell']) in cells]
        o['hazards_by_room'].append(dict(room=room['id'],samples=len(local),
            heat_max=max((s['value'] for s in local if s['modality']=='heat'),default=None),
            smoke_max=max((s['value'] for s in local if s['modality']=='smoke'),default=None),
            oldest_observation_s=min((s['observed_at_s'] for s in local),default=None)))
    return o
