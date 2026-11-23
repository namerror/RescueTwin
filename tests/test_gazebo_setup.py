"""Offline checks for the Gazebo floor picker and launch lifecycle."""

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event
import time

import pytest

from scripts import gazebo_setup


def read_fixture(tmp_path):
    root = tmp_path / 'prepared'
    bundle = root / 'synthetic' / 'bundle'
    bundle.mkdir(parents=True)
    scene = deepcopy(gazebo_setup.public_floor())
    scene.update(id='synthetic-fixture', name='Synthetic private floor', robot_start=[4, 3])
    scene['grid'].update(origin=[-3., 5.], resolution=.5)
    body = b'v 0.0 0.0 0.0\n'
    scene['mesh'].update(file='mesh.obj', materials=[])
    scene['assets'] = {'mesh.obj': {'sha256': hashlib.sha256(body).hexdigest()}}
    (bundle / 'mesh.obj').write_bytes(body)
    (bundle / 'scene.json').write_text(json.dumps(scene))
    return root, bundle


def test_discovery_resolves_bundle_and_prefers_reviewed_pose(tmp_path):
    bundles, bundle = read_fixture(tmp_path)
    data = tmp_path / 'gazebo'
    launcher = gazebo_setup.Launcher(data, bundles)
    catalog = launcher.catalog()['floors']
    assert len(catalog) == 2
    direct = launcher.floors[catalog[1]['id']]
    assert direct.spawn == (-.75, 6.75, 0.) and direct.spawn_cell == (4, 3)
    converted = launcher.prepare_floor(direct)
    assert (converted / 'scan' / 'mesh.obj').is_file()
    assert launcher.prepare_floor(direct) == converted

    reviewed = data / 'reviewed'
    (reviewed / 'scan').mkdir(parents=True)
    (reviewed / 'source.json').write_text(json.dumps({
        'bundle': str(bundle), 'spawn_xy_yaw': [-.25, 7.25, 1.2]}))
    public, floor = launcher.catalog()['floors']
    assert public['preset'] == [gazebo_setup.DEMO_IGNITION]
    assert floor['spawn'] == (5, 4) and floor['yaw'] == 1.2
    assert not {'scene', 'bundle', 'source', 'token'}.intersection(floor)
    assert launcher.prepare_floor(launcher.floors[floor['id']]) == reviewed


def test_setup_restores_last_placement_and_uses_fresh_runs(tmp_path, monkeypatch):
    launcher = gazebo_setup.Launcher(tmp_path / 'gazebo', tmp_path / 'prepared')
    floor, = launcher.catalog()['floors']
    request = dict(floor=floor['id'], revision=floor['revision'], ignitions=[[13, 6]])

    def run(session, floor, ignitions):
        launcher.set_status(session, 'running', 'ready')
        session.cancel.wait(3)
        launcher.set_status(session, 'idle', 'stopped')

    monkeypatch.setattr(launcher, 'run', run)
    launcher.start(request)
    first = launcher.session
    with pytest.raises(gazebo_setup.SetupError, match='Stop'):
        launcher.start(request)
    launcher.stop()
    first.worker.join(1)
    assert launcher.status()['phase'] == 'idle'

    launcher.start({**request, 'ignitions': [[12, 6]]})
    second = launcher.stop()
    second.worker.join(1)
    assert first.run_dir != second.run_dir
    restored, = gazebo_setup.Launcher(launcher.ros_data, launcher.bundle_root).catalog()['floors']
    assert restored['ignitions'] == [[12, 6]]
    for invalid in ([[0, 0]], [[1, 1]], [[6, 6]], [[13, 6], [13, 6]], [[13.1, 6]]):
        with pytest.raises(ValueError):
            launcher.start({**request, 'ignitions': invalid})


def test_preparation_failure_reports_error_and_allows_retry(tmp_path, monkeypatch):
    launcher = gazebo_setup.Launcher(tmp_path / 'gazebo', tmp_path / 'prepared')
    floor, = launcher.catalog()['floors']
    request = dict(floor=floor['id'], revision=floor['revision'], ignitions=[])

    def ensure_tools(*_):
        raise gazebo_setup.SetupError('Could not initialize Gazebo.')

    monkeypatch.setattr(launcher, 'ensure_tools', ensure_tools)
    launcher.start(request)
    launcher.session.worker.join(1)
    assert launcher.status()['phase'] == 'error'
    assert 'Could not initialize Gazebo' in launcher.status()['message']
    launcher.start(request)
    launcher.session.worker.join(1)
    assert launcher.status()['phase'] == 'error'


def test_active_client_path_tracks_the_ready_run_and_is_cleared(tmp_path, monkeypatch, capsys):
    launcher = gazebo_setup.Launcher(tmp_path / 'gazebo', tmp_path / 'prepared')
    floor, = launcher.catalog()['floors']
    request = dict(floor=floor['id'], revision=floor['revision'], ignitions=[])
    ready = Event()
    exited = Event()
    credential = 'synthetic-run-token'

    class Process:
        def poll(self):
            return 1 if exited.is_set() else None

    def prepare(run_dir, *_):
        run_dir.mkdir(parents=True)
        (run_dir / 'policy-client.json').write_text(json.dumps({'token': credential}))

    def wait_until_running():
        deadline = time.monotonic() + 3
        while launcher.status()['phase'] == 'starting' and time.monotonic() < deadline:
            time.sleep(.02)
        assert launcher.status()['phase'] == 'running'

    monkeypatch.setattr(launcher, 'ensure_tools', lambda *_: ('python', 'models', {}))
    monkeypatch.setattr(gazebo_setup, 'free_port', lambda: 8091)
    monkeypatch.setattr(gazebo_setup, 'prepare', prepare)
    monkeypatch.setattr(gazebo_setup.subprocess, 'Popen', lambda *_, **__: Process())
    monkeypatch.setattr(gazebo_setup, 'policy_ready', lambda *_: ready.is_set())
    monkeypatch.setattr(gazebo_setup, 'stop_process', lambda _: None)

    launcher.client_path.symlink_to('missing')
    launcher.clear_client()
    assert not launcher.client_path.is_symlink()
    launcher.start(request)
    assert 'client' not in launcher.status()
    assert not launcher.client_path.is_symlink()
    ready.set()
    wait_until_running()
    first = launcher.session.run_dir / 'policy-client.json'
    assert launcher.client_path.resolve() == first
    assert launcher.status()['client'] == str(launcher.client_path)
    output = capsys.readouterr().out
    assert 'velocity 0.10 0 2' in output
    assert credential not in output + json.dumps(launcher.status())

    launcher.stop().worker.join(3)
    assert launcher.status()['phase'] == 'idle'
    assert not launcher.client_path.is_symlink()
    assert first.is_file()

    launcher.start(request)
    wait_until_running()
    assert launcher.client_path.resolve() == launcher.session.run_dir / 'policy-client.json'
    assert launcher.client_path.resolve() != first
    exited.set()
    launcher.session.worker.join(3)
    assert launcher.status()['phase'] == 'error'
    assert 'client' not in launcher.status()
    assert not launcher.client_path.is_symlink()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is optional for the standalone launcher')
def test_visible_map_points_round_trip_to_gazebo_grid():
    source = Path(gazebo_setup.__file__).with_name('gazebo_setup.js').read_bytes()
    uri = 'data:text/javascript;base64,' + base64.b64encode(source).decode()
    subprocess.run(['node', '--input-type=module', '-e', """
import assert from 'node:assert/strict';
const {mapCell} = await import(process.argv[1]);
const area = {left: 20, top: 46, width: 623, height: 372};
for (const grid of [{width: 11, height: 6, origin: [-3, 8], resolution: .5},
                    {width: 25, height: 17, origin: [17, -3], resolution: .25}]) {
  for (const [x, y] of [[0, 0], [2, 3], [grid.width - 1, grid.height - 1]]) {
    const px = area.left + area.width * (x + .5) / grid.width;
    const py = area.top + area.height * (grid.height - y - .5) / grid.height;
    assert.deepEqual(mapCell(px, py, area, grid), [x, y]);
  }
  assert.equal(mapCell(area.left - 1, area.top, area, grid), null);
  assert.equal(mapCell(area.left + area.width, area.top, area, grid), null);
}
""", uri], check=True)
