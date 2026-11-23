"""Local setup screen and process supervisor for the Gazebo policy simulator."""

from argparse import ArgumentParser
from dataclasses import dataclass, field
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
import time
from urllib.request import Request, urlopen
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
ROS_PACKAGE = ROOT / 'ros2_sim/src/rescuetwin_sim'
ROS_SETUP = Path('/opt/ros/jazzy/setup.bash')
sys.path.insert(0, str(ROS_PACKAGE / 'scripts'))
from prepare_policy_run import DEMO_IGNITION, PUBLIC_SPAWN, prepare, public_floor, validate_ignitions
from prepare_world import prepare as prepare_scan, validate_floor


class SetupError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Floor:
    id: str
    name: str
    scene: dict
    spawn: tuple
    bundle: Path | None = None
    reviewed: Path | None = None
    revision: str = 'public'

    def __post_init__(self):
        _, self.blocked, self.spawn_cell = validate_floor(self.scene, self.spawn)

    def public(self, saved):
        grid = self.scene['grid']
        ignitions = []
        if isinstance(saved, dict) and saved.get('revision') == self.revision:
            ignitions = saved.get('ignitions', [])
            try:
                validate_ignitions(self.scene, self.blocked, self.spawn_cell, ignitions)
            except ValueError:
                ignitions = []
        return dict(id=self.id, name=self.name, revision=self.revision, public=self.bundle is None,
                    grid={key: grid[key] for key in ('width', 'height', 'resolution', 'origin')},
                    blocked=sorted(self.blocked), spawn=self.spawn_cell, yaw=self.spawn[2],
                    exits=[exit['cell'] for exit in self.scene['exits']], ignitions=ignitions,
                    preset=[DEMO_IGNITION] if self.bundle is None else None)


def private_floor(bundle, reviewed=None, spawn=None):
    manifest = (bundle / 'scene.json').read_bytes()
    scene = json.loads(manifest)
    if spawn is None:
        grid = scene['grid']
        cell = scene.get('robot_start') or scene['exits'][0]['cell']
        center = tuple(origin + (i + .5) * grid['resolution'] for origin, i in zip(grid['origin'], cell))
        spawn = (*center, 0.)
    spawn = tuple(spawn)
    revision = digest(manifest + json.dumps(spawn).encode())
    identity = digest(os.fsencode(reviewed or bundle))[:24]
    return Floor(identity, scene.get('name') or 'Private floor', scene, spawn, bundle, reviewed, revision)


def discover_floors(ros_data, bundles):
    scene = public_floor()
    public = Floor('public', scene['name'], scene, PUBLIC_SPAWN, revision=digest(json.dumps(scene).encode()))
    floors = {'public': public}
    reviewed_bundles = set()
    reviewed_poses = set()
    for manifest in sorted(ros_data.glob('*/source.json')):
        if not (manifest.parent / 'scan').is_dir():
            continue
        try:
            source = json.loads(manifest.read_text())
            bundle = Path(source['bundle']).resolve()
            floor = private_floor(bundle, manifest.parent, source['spawn_xy_yaw'])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        key = (bundle, floor.spawn)
        if key in reviewed_poses:
            continue
        reviewed_poses.add(key)
        reviewed_bundles.add(bundle)
        floors[floor.id] = floor
    for manifest in sorted(bundles.glob('*/bundle/scene.json')):
        bundle = manifest.parent.resolve()
        if bundle in reviewed_bundles:
            continue
        try:
            floor = private_floor(bundle)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        floors[floor.id] = floor
    names = [floor.name for floor in floors.values()]
    for floor in floors.values():
        if names.count(floor.name) > 1 and floor.reviewed:
            floor.name = f'{floor.name} · {floor.reviewed.name}'
    return floors


def load_settings(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def source_environment(path, env):
    # Pass the path as a positional argument. ROS setup prints no environment to the terminal.
    result = subprocess.check_output(
        ['bash', '-c', 'source "$1" >/dev/null && env -0', 'gazebo-setup', str(path)], env=env)
    return dict(os.fsdecode(item).split('=', 1) for item in result.split(b'\0') if item)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop_process(process):
    if process.poll() is not None:
        return
    for sig, timeout in ((signal.SIGINT, 8), (signal.SIGTERM, 3)):
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=timeout)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()


@dataclass
class Session:
    run_dir: Path
    cancel: Event = field(default_factory=Event)
    phase: str = 'starting'
    message: str = 'Checking Gazebo setup…'
    worker: Thread | None = None


class Launcher:
    def __init__(self, ros_data, bundle_root, headless=False):
        self.ros_data = ros_data
        self.bundle_root = bundle_root
        self.state_dir = ros_data / 'launcher'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.client_path = self.state_dir / 'policy-client.json'
        self.settings_path = self.state_dir / 'last-setup.json'
        self.settings = load_settings(self.settings_path)
        self.floors = {}
        self.lock = Lock()
        self.session = None
        self.headless = headless

    def catalog(self):
        floors = discover_floors(self.ros_data, self.bundle_root)
        with self.lock:
            self.floors = floors
            return {'floors': [floor.public(self.settings.get(floor.id, {})) for floor in floors.values()],
                    'selected': self.settings.get('_selected') if self.settings.get('_selected') in floors else 'public'}

    def status(self):
        with self.lock:
            session = self.session
            if session is None:
                return dict(phase='idle', message='')
            state = dict(phase=session.phase, message=session.message)
            if session.phase == 'running':
                state['client'] = str(self.client_path)
            return state

    def set_status(self, session, phase, message):
        with self.lock:
            session.phase, session.message = phase, message

    def publish_client(self, session):
        with self.lock:
            if session.cancel.is_set():
                return
            descriptor = session.run_dir / 'policy-client.json'
            self.client_path.symlink_to(descriptor.relative_to(self.state_dir))
            session.phase, session.message = 'running', 'Gazebo and the robot camera are ready.'
            example = shlex.quote(str(ROOT / 'ros2_sim/examples/policy_client.py'))
            print(f'Policy client file: {self.client_path}\n'
                  f'Try in a second terminal: python3 {example} velocity 0.10 0 2', flush=True)

    def clear_client(self):
        if self.client_path.is_symlink():
            self.client_path.unlink(missing_ok=True)

    def start(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('floor'), str):
            raise SetupError('Choose a floor before launching.')
        with self.lock:
            if self.session and self.session.worker.is_alive():
                raise SetupError('Stop the current run before starting another.')
            floor = self.floors.get(payload['floor'])
            if floor is None:
                raise SetupError('Choose a floor before launching.')
            if payload.get('revision') != floor.revision:
                raise SetupError('This floor changed. Reload the setup screen and place the ignition again.')
            ignitions = payload.get('ignitions')
            validate_ignitions(floor.scene, floor.blocked, floor.spawn_cell, ignitions)
            saved = self.settings | {'_selected': floor.id,
                                     floor.id: dict(revision=floor.revision, ignitions=ignitions)}
            with tempfile.NamedTemporaryFile(mode='w', dir=self.state_dir, delete=False) as pending:
                json.dump(saved, pending)
            Path(pending.name).replace(self.settings_path)
            self.settings = saved
            session = Session(self.state_dir / 'runs' / uuid.uuid4().hex)
            session.worker = Thread(target=self.run, args=(session, floor, ignitions), daemon=True)
            self.session = session
            session.worker.start()

    def stop(self):
        with self.lock:
            session = self.session
            if session and session.worker.is_alive():
                session.cancel.set()
                session.phase, session.message = 'stopping', 'Stopping Gazebo…'
                self.clear_client()
            return session

    def run_command(self, session, arguments, output, env, error, cwd=ROOT):
        with subprocess.Popen(arguments, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                              start_new_session=True) as child:
            while child.poll() is None:
                if session.cancel.wait(.1):
                    stop_process(child)
                    raise InterruptedError
            if child.returncode:
                raise SetupError(error)

    def ensure_tools(self, session, output):
        if not ROS_SETUP.is_file():
            raise SetupError('Install ROS 2 Jazzy and the system packages in the Gazebo quickstart.')
        if not (os.environ.get('DISPLAY') or (not self.headless and os.environ.get('WAYLAND_DISPLAY'))):
            raise SetupError('Start setup from a desktop session, or use the headless setup in the quickstart.')
        env = source_environment(ROS_SETUP, os.environ.copy())
        uv, colcon = (shutil.which(name, path=env.get('PATH')) for name in ('uv', 'colcon'))
        if not all((uv, colcon, shutil.which('gz', path=env.get('PATH')))):
            raise SetupError('Install uv and the system packages in the one-time Gazebo quickstart.')
        env.setdefault('ROS_LOG_DIR', str(self.state_dir / 'ros-logs'))
        lock_hash = digest((ROOT / 'uv.lock').read_bytes() + (ROOT / 'pyproject.toml').read_bytes())
        package_hash = digest(b''.join(
            str(p.relative_to(ROS_PACKAGE)).encode() + p.read_bytes()
            for p in sorted(ROS_PACKAGE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts))
        venv = self.state_dir / 'venv'
        python = venv / 'bin/python'
        install = self.state_dir / 'install'
        ros_overlay = install / 'local_setup.bash'
        stamp = self.state_dir / 'dependencies.json'
        installed = load_settings(stamp)
        if installed.get('python') != lock_hash or not python.is_file():
            self.set_status(session, 'starting', 'Preparing the Gazebo Python environment…')
            self.run_command(session, [uv, 'sync', '--locked', '--python', '/usr/bin/python3',
                                       '--extra', 'ros-policy'], output,
                             {**env, 'UV_PROJECT_ENVIRONMENT': str(venv)},
                             'Python setup failed. Check the local setup log and retry.')
        if installed.get('ros') != package_hash or not ros_overlay.is_file():
            self.set_status(session, 'starting', 'Building the RescueTwin ROS package…')
            self.run_command(session, [colcon, '--log-base', str(self.state_dir / 'colcon-log'),
                                       'build', '--base-paths', str(ROS_PACKAGE),
                                       '--build-base', str(self.state_dir / 'build'),
                                       '--install-base', str(install), '--packages-select', 'rescuetwin_sim',
                                       '--cmake-args', '-DPython3_EXECUTABLE=/usr/bin/python3'],
                             output, env, 'ROS build failed. Check the local setup log and retry.')
        stamp.write_text(json.dumps({'python': lock_hash, 'ros': package_hash}))
        models_path = 'opt/ros/jazzy/share/turtlebot3_gazebo/models'
        models = [Path('/') / models_path, self.ros_data / 'robot' / models_path,
                  self.state_dir / 'robot' / models_path]
        for path in models:
            if (path / 'turtlebot3_burger/model.sdf').is_file() and (
                    path / 'turtlebot3_burger/model.config').is_file() and (path / 'turtlebot3_common/meshes').is_dir():
                return python, path, source_environment(ros_overlay, env)
        self.set_status(session, 'starting', 'Preparing TurtleBot3 assets…')
        with tempfile.TemporaryDirectory(dir=self.state_dir) as stage:
            self.run_command(session, ['apt-get', 'download', 'ros-jazzy-turtlebot3-gazebo'],
                             output, env, 'TurtleBot3 download failed. Check the ROS apt setup and retry.', cwd=stage)
            package, = Path(stage).glob('ros-jazzy-turtlebot3-gazebo_*.deb')
            self.run_command(session, ['dpkg-deb', '-x', package, self.state_dir / 'robot'],
                             output, env, 'TurtleBot3 extraction failed. Check the local setup log and retry.')
        return python, models[-1], source_environment(ros_overlay, env)

    def prepare_floor(self, floor):
        if floor.bundle is None:
            return None
        if floor.reviewed:
            source = json.loads((floor.reviewed / 'source.json').read_text())
            if Path(source['bundle']).resolve() != floor.bundle:
                raise SetupError('This floor changed. Reload the setup screen and place the ignition again.')
            current = private_floor(floor.bundle, floor.reviewed, source['spawn_xy_yaw'])
        else:
            current = private_floor(floor.bundle)
        if current.revision != floor.revision:
            raise SetupError('This floor changed. Reload the setup screen and place the ignition again.')
        if floor.reviewed:
            return floor.reviewed
        output = self.state_dir / 'floors' / floor.revision
        if not (output / 'source.json').is_file():
            prepare_scan(floor.bundle, output, floor.spawn)
        return output

    def run(self, session, floor, ignitions):
        log_dir = self.state_dir / 'logs'
        log_dir.mkdir(exist_ok=True)
        log = log_dir / (session.run_dir.name + '.log')
        process = None
        phase, message = 'idle', 'Gazebo stopped.'
        try:
            with log.open('w') as output:
                python, models, env = self.ensure_tools(session, output)
                if session.cancel.is_set():
                    return
                self.set_status(session, 'starting', 'Preparing the selected floor…')
                private = self.prepare_floor(floor)
                prepare(session.run_dir, models, private, ignitions, 7)
                if session.cancel.is_set():
                    return
                self.set_status(session, 'starting', 'Starting Gazebo and the robot camera…')
                port = free_port()
                command = ['ros2', 'launch', 'rescuetwin_sim', 'bringup.launch.py',
                           f'run_dir:={session.run_dir}', f'model_dir:={models}',
                           f'policy_python:={python}', f'policy_port:={port}',
                           f'ros_domain_id:={100 + secrets.randbelow(100)}',
                           f'gz_partition:=rescuetwin_{session.run_dir.name}']
                if private:
                    command.extend(['world:=private', f'floor_dir:={private}'])
                if self.headless:
                    command.append('headless:=true')
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 60
                ready = False
                while not session.cancel.wait(.25):
                    if process.poll() is not None:
                        raise SetupError('Gazebo closed.' if ready else 'Gazebo could not start.')
                    if not ready:
                        ready = policy_ready(session.run_dir, port)
                        if ready:
                            self.publish_client(session)
                        elif time.monotonic() > deadline:
                            raise SetupError('The robot camera did not become ready.')
        except InterruptedError:
            pass
        except SetupError as error:
            phase, message = 'error', str(error)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
            phase, message = 'error', 'Setup failed. Check the selected floor, its assets, and the local log.'
            with log.open('a') as output:
                import traceback
                traceback.print_exc(file=output)
        finally:
            with self.lock:
                if session.phase == 'running':
                    session.phase, session.message = 'stopping', 'Stopping Gazebo…'
                self.clear_client()
            try:
                if process is not None:
                    stop_process(process)
            finally:
                with self.lock:
                    if session.cancel.is_set():
                        phase, message = 'idle', 'Gazebo stopped.'
                    elif phase == 'error':
                        message += f' Local log: {log}'
                    session.phase, session.message = phase, message


def policy_ready(run, port):
    try:
        client = json.loads((run / 'policy-client.json').read_text())
        url = f'http://127.0.0.1:{port}/policy/v1/runs/'
        if not client['url'].startswith(url):
            return False
        req = Request(client['url'] + '/observations', headers={'Authorization': 'Bearer ' + client['token']})
        with urlopen(req, timeout=.5) as response:
            obs = json.load(response)
        return obs['localization_status'] == 'ready' and obs['rgbd']['status'] == 'ready'
    except (OSError, ValueError, KeyError):
        return False


def make_handler(launcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, data, content_type='application/json', status=200):
            if content_type == 'application/json':
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.allowed():
                self.respond({'error': 'Use the local setup URL.'}, status=403)
            elif self.path == '/':
                self.respond(Path(__file__).with_name('gazebo_setup.html').read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/setup.js':
                self.respond(Path(__file__).with_name('gazebo_setup.js').read_bytes(), 'text/javascript; charset=utf-8')
            elif self.path == '/api/floors':
                self.respond(launcher.catalog())
            elif self.path == '/api/status':
                self.respond(launcher.status())
            else:
                self.respond({'error': 'Page not found.'}, status=404)

        def do_POST(self):
            if (not self.allowed() or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}'
                    or self.headers.get_content_type() != 'application/json'):
                self.respond({'error': 'Use the local setup screen.'}, status=403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16_384:
                    raise SetupError('Invalid setup request.')
                payload = json.loads(self.rfile.read(length))
                if self.path == '/api/launch':
                    launcher.start(payload)
                elif self.path == '/api/stop':
                    launcher.stop()
                else:
                    self.respond({'error': 'Page not found.'}, status=404)
                    return
            except (ValueError, SetupError) as error:
                self.respond({'error': str(error)}, status=400)
                return
            self.respond(launcher.status())

    return Handler


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true', help='Launch without Gazebo and RViz windows')
    parser.add_argument('--no-browser', action='store_true', help='Print the setup URL without opening it')
    args = parser.parse_args()
    ros_data = ROOT / 'data/ros2_sim'
    bundle_root = Path(os.environ.get('RESCUETWIN_DATA', ROOT / 'data/prepared'))
    launcher = Launcher(ros_data, bundle_root, args.headless)
    with (launcher.state_dir / 'setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Gazebo setup is already open. Use its browser tab or stop that launcher.\n')
        launcher.clear_client()
        with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(launcher)) as server:
            url = f'http://127.0.0.1:{server.server_port}'
            print(f'Gazebo setup is open at {url}. Press Ctrl+C to stop.', flush=True)
            if not args.no_browser:
                Thread(target=webbrowser.open, args=(url,), daemon=True).start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                session = launcher.stop()
                if session and session.worker:
                    session.worker.join()


if __name__ == '__main__':
    main()
