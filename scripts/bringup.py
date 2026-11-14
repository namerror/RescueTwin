"""One-time bootstrap and foreground service supervisor. Standard library only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = '0.11.25'
EXTRAS = ['scene', 'vision', 'agent', 'test']


def dotenv(path, inherited):
    env = {}
    if path.exists():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'): continue
            if line.startswith('export '): line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key.strip()):
                raise ValueError(f'Invalid .env assignment at line {number}')
            value = value.strip()
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f'Invalid .env quoting at line {number}')
                value = value[1:-1]
            else: value = value.split(' #', 1)[0].rstrip()
            env[key.strip()] = value
    env.update(inherited)
    return env


def run(args, env, cwd=ROOT):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env)
    if result.returncode: raise RuntimeError(f'{Path(str(args[0])).name} command failed (exit {result.returncode})')


def fingerprint():
    h = hashlib.sha256()
    for name in ['uv.lock', 'pyproject.toml', '.python-version', 'frontend/package.json', 'frontend/package-lock.json']:
        h.update((ROOT/name).read_bytes())
    h.update((UV_VERSION+','.join(EXTRAS)).encode())
    return h.hexdigest()


def environment_state(python, env):
    code = "import sys,json,importlib.metadata as m; print(json.dumps({'python':sys.version.split()[0], 'packages':{d.metadata['Name'].lower():d.version for d in m.distributions()}}))"
    return json.loads(subprocess.check_output([str(python), '-c', code], env=env, text=True))


def bootstrap(env, force=False):
    state = ROOT/'data/bringup'; state.mkdir(parents=True, exist_ok=True)
    stamp = state/'installed.json'
    python = ROOT/'.venv/bin/python'
    modules = ROOT/'frontend/node_modules'
    expected = fingerprint()
    if stamp.exists() and not force:
        try: installed = json.loads(stamp.read_text())
        except ValueError: raise ValueError('Installation marker is damaged. Run ./setup.sh --install.') from None
        probe = subprocess.run([str(python), '-c', "import importlib.util,sys; sys.exit(not all(importlib.util.find_spec(n) for n in ['uvicorn','fastapi','numpy','shapely','ezdxf','PIL','ultralytics','google.genai','pytest']))"], env=env, capture_output=True) if python.exists() else None
        if installed.get('fingerprint') != expected or (python.exists() and installed.get('python_environment') != environment_state(python, env)) or not probe or probe.returncode or not (modules/'.bin/vite').exists() or not (modules/'.package-lock.json').exists() or hashlib.sha256((modules/'.package-lock.json').read_bytes()).hexdigest() != installed.get('node_modules_lock'):
            raise ValueError('Dependencies changed or are incomplete. Run ./setup.sh --install once.')
        print('Dependencies ready; skipping installation.', flush=True)
        return python
    uv = shutil.which('uv', path=env.get('PATH'))
    if uv and subprocess.check_output([uv, '--version'], text=True).split()[1] != UV_VERSION: uv = None
    if not uv:
        base = ROOT/'.bootstrap-venv'
        run([sys.executable, '-m', 'venv', base], env)
        run([base/'bin/python', '-m', 'pip', 'install', f'uv=={UV_VERSION}'], env)
        uv = str(base/'bin/uv')
    print('Installing locked dependencies (first use or --install).', flush=True)
    run([uv, 'sync', '--locked', '--inexact', '--python', '3.12.3', *[x for extra in EXTRAS for x in ('--extra', extra)]], env)
    run(['npm', 'ci', '--no-audit', '--no-fund'], env, ROOT/'frontend')
    pending = stamp.with_suffix('.next')
    pending.write_text(json.dumps({'fingerprint':expected, 'python_environment':environment_state(python, env), 'node_modules_lock':hashlib.sha256((modules/'.package-lock.json').read_bytes()).hexdigest()}))
    pending.replace(stamp)
    return python


def check_bundle(bundle, model):
    from rescuetwin.scenes import load_bundle
    scene = load_bundle(bundle)
    mesh = scene.meta.get('mesh') or {}
    if scene.source != 'sponsor-source' or scene.meta['frame']['model'] != model or not mesh.get('textured') or not mesh.get('validation', {}).get('passed'):
        raise ValueError('Bundle must be a validated textured source scene for the requested model')
    assets = scene.meta.get('assets') or {}
    if mesh['file'] not in assets or not mesh.get('materials') or any(n not in assets for n in mesh['materials']):
        raise ValueError('Bundle mesh/material asset declarations are incomplete')
    for name, info in assets.items():
        path = (bundle/name).resolve()
        if Path(name).name != name or not path.is_relative_to(bundle.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
            raise ValueError('Bundle asset missing or hash mismatch; rebuilding required')
    return scene.id


def cached_obj(root):
    status = root/'source-assets/matterpak-status.json'
    if not status.exists(): return None
    try:
        data = json.loads(status.read_text())
        if data.get('model') != root.name: return None
        for directory in sorted((root/'source-assets').glob('matterpak-*')):
            if not directory.is_dir(): continue
            if all((directory/item['name']).resolve().is_relative_to(directory.resolve()) and (directory/item['name']).is_file() and hashlib.sha256((directory/item['name']).read_bytes()).hexdigest() == item['sha256'] for item in data['extracted']):
                obj = (directory/data['obj']).resolve()
                if obj.is_relative_to(directory.resolve()) and obj.is_file(): return obj
    except (ValueError, KeyError, OSError): pass
    return None


def prepare(model, env):
    from prepare_scene import fetch, build
    from scene_assets import fetch_matterpak, import_package
    root = ROOT/'data/prepared'; dest = root/model; bundle = dest/'bundle'
    try:
        sid = check_bundle(bundle, model)
        print(f'Using validated cached textured scene {model}.', flush=True)
        return sid
    except (ValueError, KeyError, OSError): pass
    kit = ROOT/'data/costar-hackathon-kit'
    required = ['commercial-properties.csv', 'data/commercial-property.jsonl', 'data/apartment-listing.jsonl', 'schemas/enums.json']
    if any(not (kit/p).is_file() for p in required):
        raise ValueError('Place the sponsor CoStar kit at data/costar-hackathon-kit before preparing this scene.')
    obj = cached_obj(dest)
    if obj is None:
        print(f'Fetching MatterPak for {model} (credentials required).', flush=True)
        fetch_matterpak(model, root)
        obj = cached_obj(dest)
        if obj is None: raise ValueError('Downloaded MatterPak failed cache validation')
    # Build/import away from the live bundle. Failed reconciliation cannot replace it.
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        stage = Path(tmp); work = stage/model; work.mkdir()
        source = dest/'source'
        if source.exists(): shutil.copytree(source, work/'source')
        complete = False
        try:
            data = json.loads((work/'source/source.json').read_text())
            plans = data['assets']['floorplans']
            complete = all((work/'source'/p['file']).is_file() for p in plans) and bool(plans)
        except (OSError, KeyError, ValueError): pass
        if not complete: fetch(model, stage, None)
        build(model, stage, ROOT/'scenes'/f'{model}.json', kit)
        import_package(model, obj, stage)
        sid = check_bundle(work/'bundle', model)
        bundle.mkdir(parents=True, exist_ok=True)
        # Hash-named source assets never overwrite files used by the previous manifest.
        manifest = json.loads((work/'bundle/scene.json').read_text())
        for name in manifest['assets']:
            target = bundle/name
            if target.exists() and target.read_bytes() == (work/'bundle'/name).read_bytes(): continue
            pending = bundle/(name+'.next'); shutil.copyfile(work/'bundle'/name, pending); pending.replace(target)
        shutil.copyfile(work/'bundle/scene.before-source.json', bundle/'scene.before-source.json')
        pending = bundle/'scene.json.next'; shutil.copyfile(work/'bundle/scene.json', pending); pending.replace(bundle/'scene.json')
        for name in ['source-mesh-validation.json']:
            shutil.copyfile(work/name, dest/name)
        if not source.exists(): shutil.copytree(work/'source', source)
        return sid


def port(value):
    number = int(value)
    if not 1 <= number <= 65535: raise ValueError('Ports must be between 1 and 65535')
    return number


def available_port(number):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: sock.bind(('127.0.0.1', number))
        except OSError: raise ValueError(f'Port {number} is occupied. Set RESCUETWIN_BACKEND_PORT / RESCUETWIN_FRONTEND_PORT.') from None


def serve(python, env, sid, backend, frontend, environment_file=None):
    env = dict(env, RESCUETWIN_SCENE=sid, RESCUETWIN_DATA=str(ROOT/'data/prepared'), RESCUETWIN_API_URL=f'http://127.0.0.1:{backend}')
    children = []
    def stop(signum, frame): raise KeyboardInterrupt
    old = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        children.append(subprocess.Popen([str(python), '-m', 'uvicorn', 'rescuetwin.api:app', '--host', '127.0.0.1', '--port', str(backend)], cwd=ROOT, env=env, start_new_session=True))
        deadline = time.monotonic()+60
        while True:
            if children[0].poll() is not None: raise RuntimeError('Backend exited during startup')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{backend}/api/scene', timeout=2) as response: scene = json.load(response)
                with urllib.request.urlopen(f'http://127.0.0.1:{backend}/api/perception', timeout=2) as response: vision = json.load(response)
                if scene['id'] != sid or (environment_file is None and not vision['vision_installed']): raise RuntimeError('Backend scene/vision readiness mismatch')
                if environment_file is None and vision.get('error'): raise RuntimeError('YOLO initialization failed; check weights and vision dependencies')
                if environment_file is None and env.get('RESCUETWIN_PRELOAD_DETECTOR', '1') != '0' and not vision.get('detector_loaded'):
                    if time.monotonic() >= deadline: raise RuntimeError('YOLO initialization timed out')
                    time.sleep(.2); continue
                break
            except (OSError, TimeoutError):
                if time.monotonic() >= deadline: raise RuntimeError('Backend readiness timed out')
                time.sleep(.2)
        if environment_file is not None:
            request = urllib.request.Request(f'http://127.0.0.1:{backend}/api/environment',
                                             data=environment_file.read_bytes(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                sid = json.load(response)['scene_id']
        children.append(subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', str(frontend), '--strictPort'], cwd=ROOT/'frontend', env=env, start_new_session=True))
        print(f'\nRescueTwin: http://127.0.0.1:{frontend} | scene {sid} | Ctrl+C stops both services\n', flush=True)
        while True:
            if any(p.poll() is not None for p in children): raise RuntimeError('A service exited; stopping the other service')
            time.sleep(.25)
    finally:
        for p in children:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        for p in children:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                p.wait()
        for s, handler in old.items(): signal.signal(s, handler)


def main():
    parser = argparse.ArgumentParser(description='Install once, prepare missing textured scene data, then run backend + frontend. Root .env is supported; exported variables win.')
    parser.add_argument('--install', action='store_true', help='Resynchronize locked dependencies before starting')
    parser.add_argument('--environment', type=Path, help='Start with a local annotated floor-plan JSON and analytic perception')
    parser.add_argument('--runtime', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.chdir(ROOT)
    env = dotenv(ROOT/'.env', os.environ)
    os.environ.update(env)
    model = env.get('RESCUETWIN_MODEL_ID', '9A46aT7FCPg')
    if not re.fullmatch(r'[A-Za-z0-9]+', model) or not (ROOT/'scenes'/f'{model}.json').is_file():
        raise ValueError('Model needs reviewed scenes/MODEL.json annotations. Supported: '+', '.join(p.stem for p in (ROOT/'scenes').glob('*.json')))
    backend = port(env.get('RESCUETWIN_BACKEND_PORT','8000')); frontend = port(env.get('RESCUETWIN_FRONTEND_PORT','5173'))
    if backend == frontend: raise ValueError('Backend and frontend ports must differ')
    available_port(backend); available_port(frontend)
    if not shutil.which('npm') or not shutil.which('node'): raise ValueError('Install Node 24 (with npm) first; see .nvmrc')
    if subprocess.check_output(['node','--version'],text=True).strip().split('.')[0] != 'v24': raise ValueError('Use Node 24; see .nvmrc')
    env.setdefault('OMP_NUM_THREADS','1'); env.setdefault('MKL_NUM_THREADS','1')
    if not args.runtime:
        python = bootstrap(env, args.install)
        runtime_args = ['--environment', str(args.environment.resolve())] if args.environment else []
        os.execve(python, [str(python), str(Path(__file__).resolve()), '--runtime', *runtime_args], env)
    sys.path.insert(0, str(ROOT))
    if args.environment:
        from rescuetwin.environment import load_environment
        load_environment(args.environment)
        env['RESCUETWIN_PRELOAD_DETECTOR'] = '0'
        serve(Path(sys.executable), env, 'development-apartment', backend, frontend, args.environment)
        return
    sid = prepare(model, env)
    weights = Path(env.get('YOLO_WEIGHTS', ROOT/'data/weights/yolo26n.pt')).resolve()
    if not weights.exists():
        if env.get('YOLO_WEIGHTS'): raise ValueError('YOLO_WEIGHTS must point to an existing local weights file')
        weights.parent.mkdir(parents=True, exist_ok=True)
        from ultralytics import YOLO
        # Ultralytics downloads into cwd. Keep weights and captures in ignored data.
        before = Path.cwd()
        try:
            os.chdir(weights.parent)
            YOLO(weights.name)
        finally: os.chdir(before)
        if not weights.is_file(): raise ValueError('YOLO weights unavailable; set YOLO_WEIGHTS to a local model')
    env['YOLO_WEIGHTS'] = str(weights)
    print('Mission policy: '+('Gemini when available' if env.get('GEMINI_API_KEY') else 'deterministic (no Gemini key)'), flush=True)
    serve(Path(sys.executable), env, sid, backend, frontend)


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: sys.exit(0)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Bringup failed: {exc}', file=sys.stderr); sys.exit(1)
