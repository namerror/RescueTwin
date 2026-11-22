"""Offline regression checks for the policy boundary and shared motion gate."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import numpy as np

from rescuetwin.policy_motion import Clearance, Motion
from rescuetwin.policy_runtime import Runtime, pack_frame


def runtime():
    return Runtime(dict(map=dict(frame='map', resolution=.25, origin=[0., 0.],
                                 width=16, height=12, blocked=[[8, y] for y in range(12)
                                                               if y not in (5, 6)]),
                        exit_cell=[1, 1], ignitions=[[2, 2]], seed=7))


def test_narrow_doorway_uses_same_clearance_as_velocity_gate():
    clearance = runtime().clearance
    start, goal = (1.25, .95), (3.15, .95)
    path = clearance.route(start, goal)
    assert path and path[-1] == goal
    assert all(clearance.segment(a, b) for a, b in zip([start, *path[:-1]], path))
    assert clearance.stopping_arc((1.80, 1.5, 0.), (.15, 0.), (0., 0.))
    assert not clearance.stopping_arc((1.80, .95, 0.), (.15, 0.), (0., 0.))


def test_expiry_preemption_and_motion_gate():
    t = [0.]
    clearance = runtime().clearance
    motion = Motion(clearance, now=lambda: t[0])
    lease = motion.renew(None)
    assert motion.renew(None) is None
    pose = (1., 1.5, 0.)
    move = dict(type='velocity', linear_mps=.15, angular_radps=0.)
    first = motion.submit(dict(move, command_id='1'), pose)
    assert motion.tick(pose, (0., 0.)) == (.15, 0.)
    t[0] = .26
    assert motion.tick(pose, (0., 0.)) == (0., 0.)
    assert first['reason'] == 'command_expired'
    second = motion.submit(dict(move, command_id='2'), pose)
    goal = motion.submit(dict(type='navigate_to_pose', command_id='3', yaw=0.,
                              path=[(3., 1.5)]), pose)
    assert second['status'] == 'cancelled'
    assert motion.tick(pose, (0., 0.)) == (0., 0.)
    assert motion.tick(pose, (0., 0.)) == (.15, 0.)
    assert motion.tick(None, (0., 0.)) == (0., 0.)
    assert goal['reason'] == 'stale_localization_or_clock'
    pending = motion.submit(dict(move, command_id='4'), pose)
    t[0] = 1.01
    assert motion.tick(pose, (0., 0.)) == (0., 0.)
    assert pending['reason'] == 'lease_expired'
    assert motion.renew(lease) is None


def test_policy_api_allows_only_run_data_and_valid_commands():
    service = runtime()
    client = TestClient(service.app())
    root = '/policy/v1/runs/' + service.run_id
    headers = {'Authorization': 'Bearer ' + service.token}
    assert client.get(root + '/observations').status_code == 401
    assert client.get(root + '/state', headers=headers).status_code == 404
    observation = client.get(root + '/observations', headers=headers).json()
    assert observation['localization_status'] == 'unavailable'
    assert 'ignitions' not in observation and 'full_hazard' not in observation
    assert client.get(root + '/frames/latest', headers=headers).json()['status'] == 'missing'
    lease = client.post(root + '/lease', json={}, headers=headers).json()['lease_id']
    command = dict(command_id='1', lease_id=lease, observation_seq=1,
                   type='velocity', linear_mps=.5, angular_radps=0)
    assert client.post(root + '/commands', json=command, headers=headers).status_code == 422
    command['linear_mps'] = .1
    assert client.post(root + '/commands', json=command, headers=headers).status_code == 409
    command = dict(command_id='stop', lease_id=lease, observation_seq=0, type='stop')
    result = client.post(root + '/commands', json=command, headers=headers).json()
    assert result['status'] == 'stopped' and 'lease_id' not in result


def test_frame_preserves_geometry_and_marks_invalid_pixels():
    import base64

    rgb = SimpleNamespace(width=2, height=1, step=6, encoding='rgb8', data=bytes(range(6)))
    depth = SimpleNamespace(width=2, height=1, step=8, encoding='32FC1', is_bigendian=False,
                            data=np.array([1.5, np.inf], dtype='<f4').tobytes())
    info = SimpleNamespace(width=2, height=1, k=[1.] * 9)
    frame = pack_frame(rgb, depth, info)
    assert np.frombuffer(base64.b64decode(frame['depth']), dtype='<f4').tolist() == [1.5, 0.]
    service = runtime()
    service.frame = dict(frame, stamp_ns=1, frame_id='1')
    service.clock_ns = 1_000_000_000
    assert service.frame_status() == 'stale'
