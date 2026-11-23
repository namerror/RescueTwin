from io import BytesIO
import json
import sys
from urllib.error import HTTPError

import pytest

from ros2_sim.examples import policy_client


def test_client_discovers_active_descriptor_and_accepts_explicit_path(tmp_path, monkeypatch):
    default = tmp_path / 'active.json'
    explicit = tmp_path / 'custom.json'
    for path, token in ((default, 'default-test-token'), (explicit, 'custom-test-token')):
        path.write_text(json.dumps({'url': 'http://127.0.0.1:8091/run', 'token': token}))
    monkeypatch.setattr(policy_client, 'DEFAULT_DESCRIPTOR', default)
    monkeypatch.chdir(tmp_path.parent)
    assert policy_client.Client.from_descriptor().token == 'default-test-token'

    command = ['velocity', '-0.12', '0', '2']
    assert policy_client.parse_args(command).client is None
    for options in ([str(explicit)], ['--client', str(explicit)]):
        args = policy_client.parse_args(options + command)
        assert args.client == explicit and args.linear == -.12
        assert policy_client.Client.from_descriptor(args.client).token == 'custom-test-token'


def test_client_retries_lease_without_exposing_token_and_stops_on_interrupt(tmp_path, monkeypatch, capsys):
    descriptor = tmp_path / 'active.json'
    token = 'synthetic-policy-token'
    descriptor.write_text(json.dumps({'url': 'http://127.0.0.1:8091/run', 'token': token}))
    monkeypatch.setattr(policy_client, 'DEFAULT_DESCRIPTOR', descriptor)
    monkeypatch.setattr(sys, 'argv', ['policy_client.py', 'velocity', '0.10', '0', '2'])
    observation = dict(pose={'x': 1}, hazard={}, rgbd={'status': 'ready'}, observation_seq=21)
    requests = []

    def respond(request, *, timeout):
        assert request.get_header('Authorization') == f'Bearer {token}'
        route = request.selector.removeprefix('/run')
        data = json.loads(request.data) if request.data else None
        requests.append((route, data))
        if route == '/lease':
            if requests.count(('/lease', data)) == 1 and data['lease_id'] is None:
                raise HTTPError(request.full_url, 409, token, {}, None)
            return BytesIO(b'{"lease_id":"test-lease"}')
        if route == '/commands':
            if data['type'] == 'velocity':
                raise KeyboardInterrupt
            return BytesIO(b'{"status":"stopped"}')
        return BytesIO(json.dumps(observation).encode())

    monkeypatch.setattr(policy_client.urllib.request, 'urlopen', respond)
    assert policy_client.main() == 130
    assert requests.count(('/lease', {'lease_id': None})) == 2
    assert requests[-1][1]['type'] == 'stop'
    assert requests[-1][1]['lease_id'] == 'test-lease'
    output = capsys.readouterr()
    assert token not in output.out + output.err


def test_missing_descriptor_and_busy_lease_have_actionable_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(policy_client, 'DEFAULT_DESCRIPTOR', tmp_path / 'missing.json')
    with pytest.raises(policy_client.PolicyError, match='No active Gazebo run'):
        policy_client.Client.from_descriptor()

    client = policy_client.Client({'url': 'http://127.0.0.1:8091/run', 'token': 'test-token'})
    clock = iter([0., 1.3])
    monkeypatch.setattr(policy_client.time, 'monotonic', lambda: next(clock))

    def conflict(*_):
        raise HTTPError(client.url, 409, 'test-token', {}, None)

    monkeypatch.setattr(client, 'request', conflict)
    with pytest.raises(policy_client.PolicyError, match='motion lease is busy'):
        client.acquire()


def test_cli_reports_stale_run_without_printing_token(tmp_path, monkeypatch, capsys):
    descriptor = tmp_path / 'old.json'
    token = 'stale-synthetic-token'
    descriptor.write_text(json.dumps({'url': 'http://127.0.0.1:8091/run', 'token': token}))
    monkeypatch.setattr(sys, 'argv', ['policy_client.py', '--client', str(descriptor), 'velocity', '.1', '0', '2'])

    def unauthorized(request, *, timeout):
        raise HTTPError(request.full_url, 401, token, {}, None)

    monkeypatch.setattr(policy_client.urllib.request, 'urlopen', unauthorized)
    assert policy_client.main() == 1
    output = capsys.readouterr()
    assert 'Check the active run or client file' in output.err
    assert token not in output.err + output.out
