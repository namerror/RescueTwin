"""Bringup decisions and process ownership, without network downloads."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import pytest
from scripts import bringup as b


def test_dotenv_is_data_and_environment_wins(tmp_path):
    path = tmp_path/'.env'
    path.write_text('export RESCUETWIN_MODEL_ID="scene"\nKEY=from-file # comment\nVALUE=$(touch /tmp/do-not-run)\n')
    env = b.dotenv(path, {'KEY':'exported'})
    assert env['KEY']=='exported' and env['RESCUETWIN_MODEL_ID']=='scene'
    assert env['VALUE']=='$(touch /tmp/do-not-run)'
    path.write_text('not an assignment')
    with pytest.raises(ValueError, match='line 1'): b.dotenv(path,{})


def test_port_collision_and_range():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        with pytest.raises(ValueError,match='occupied'): b.available_port(s.getsockname()[1])
    for value in ('0','65536'):
        with pytest.raises(ValueError): b.port(value)


def test_cached_package_requires_hashes_and_safe_paths(tmp_path):
    src=tmp_path/'source-assets'; src.mkdir()
    package=src/'matterpak-test'; package.mkdir(); (package/'mesh.obj').write_text('mesh')
    data={'model':tmp_path.name,'obj':'mesh.obj','extracted':[{'name':'mesh.obj','sha256':hashlib.sha256(b'mesh').hexdigest()}]}
    (src/'matterpak-status.json').write_text(json.dumps(data))
    assert b.cached_obj(tmp_path)==package/'mesh.obj'
    (package/'mesh.obj').write_text('corrupt')
    assert b.cached_obj(tmp_path) is None
    data['extracted'][0]['name']='../../escape.obj'
    (src/'matterpak-status.json').write_text(json.dumps(data))
    assert b.cached_obj(tmp_path) is None


def install_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(b,'ROOT',tmp_path)
    monkeypatch.setattr(b,'fingerprint',lambda:'fingerprint')
    monkeypatch.setattr(b,'environment_state',lambda *a:{'python':'3.12.3'})
    node=tmp_path/'frontend/node_modules';(node/'.bin').mkdir(parents=True)
    (node/'.bin/vite').touch();(node/'.package-lock.json').write_text('{}')
    python=tmp_path/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
    stamp=tmp_path/'data/bringup/installed.json';stamp.parent.mkdir(parents=True)
    stamp.write_text(json.dumps({'fingerprint':'fingerprint','python_environment':{'python':'3.12.3'},'node_modules_lock':hashlib.sha256(b'{}').hexdigest()}))
    return python, stamp


def test_repeated_launch_never_installs(tmp_path, monkeypatch):
    python,_=install_tree(tmp_path,monkeypatch)
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess([],0))
    monkeypatch.setattr(b,'run',lambda *a,**k:pytest.fail('Must not install on repeated launch'))
    assert b.bootstrap({})==python


def test_changed_dependencies_require_explicit_install(tmp_path, monkeypatch):
    _,stamp=install_tree(tmp_path,monkeypatch)
    stamp.write_text('{}')
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess([],0))
    with pytest.raises(ValueError,match='--install'): b.bootstrap({})


def test_first_install_stamps_only_after_all_commands_succeed(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'ROOT',tmp_path);monkeypatch.setattr(b,'fingerprint',lambda:'f')
    monkeypatch.setattr(b.shutil,'which',lambda *a,**k:'/uv')
    monkeypatch.setattr(b.subprocess,'check_output',lambda *a,**k:'uv '+b.UV_VERSION)
    monkeypatch.setattr(b,'environment_state',lambda *a:{'python':'3.12.3'})
    commands=[]
    def command(args,env,cwd=None):
        commands.append(args)
        if args[0]=='npm':
            lock=tmp_path/'frontend/node_modules/.package-lock.json';lock.parent.mkdir(parents=True);lock.write_text('{}')
    monkeypatch.setattr(b,'run',command)
    b.bootstrap({})
    assert '--locked' in commands[0] and commands[1][:2]==['npm','ci']
    stamp=tmp_path/'data/bringup/installed.json'; assert stamp.exists();stamp.unlink()
    def fail(*args): raise RuntimeError('failed install')
    monkeypatch.setattr(b,'run',fail)
    with pytest.raises(RuntimeError):b.bootstrap({})
    assert not stamp.exists()


def test_scene_failure_preserves_published_bundle(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from scripts import prepare_scene
    monkeypatch.setattr(b,'ROOT',tmp_path)
    dest=tmp_path/'data/prepared/m';bundle=dest/'bundle';bundle.mkdir(parents=True)
    manifest=bundle/'scene.json';manifest.write_text('previous bundle')
    kit=tmp_path/'data/costar-hackathon-kit'
    for name in ['commercial-properties.csv','data/commercial-property.jsonl','data/apartment-listing.jsonl','schemas/enums.json']:
        p=kit/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    monkeypatch.setattr(b,'check_bundle',lambda *args:(_ for _ in ()).throw(ValueError('bad')))
    monkeypatch.setattr(b,'cached_obj',lambda *args:tmp_path/'mesh.obj')
    monkeypatch.setattr(prepare_scene,'fetch',lambda *args:(_ for _ in ()).throw(ValueError('fetch failed')))
    with pytest.raises(ValueError,match='fetch failed'): b.prepare('m',{}, annotation={**json.loads((Path(__file__).resolve().parents[1]/'scenes/9A46aT7FCPg.json').read_text()), 'model':'m'})
    assert manifest.read_text()=='previous bundle'


def test_service_failure_stops_owned_processes(monkeypatch):
    children=[];killed=[]
    class Process:
        def __init__(self,*a,**k):self.pid=100+len(children);self.code=None;children.append(self)
        def poll(self):return self.code if len(children)<2 else (1 if self is children[1] else None)
        def wait(self,timeout=None):self.code=0
    class Response:
        def __init__(self,data):self.data=data
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(b.subprocess,'Popen',Process)
    monkeypatch.setattr(b.urllib.request,'urlopen',lambda url,**k:Response({'id':'s'} if url.endswith('scene') else {'vision_installed':True,'detector_loaded':True}))
    monkeypatch.setattr(b.json,'load',lambda response:response.data)
    monkeypatch.setattr(b.os,'killpg',lambda pid,sig:killed.append(pid))
    with pytest.raises(RuntimeError,match='service exited'):b.serve(Path(sys.executable),{},'s',8000,5173)
    assert killed==[100,101]


def test_port_probe_allows_immediate_restart():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',0));number=server.getsockname()[1];server.listen()
        with socket.create_connection(('127.0.0.1',number)) as client:
            connection,_=server.accept();connection.close()
        with pytest.raises(ValueError,match='occupied'):b.available_port(number)
    b.available_port(number)


def test_missing_matterpak_attempts_fetch_and_preserves_bundle(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from scripts import scene_assets
    monkeypatch.setattr(b,'ROOT',tmp_path)
    dest=tmp_path/'data/prepared/m';bundle=dest/'bundle';bundle.mkdir(parents=True)
    (bundle/'scene.json').write_text('previous')
    kit=tmp_path/'data/costar-hackathon-kit'
    for name in ['commercial-properties.csv','data/commercial-property.jsonl','data/apartment-listing.jsonl','schemas/enums.json']:
        p=kit/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    monkeypatch.setattr(b,'check_bundle',lambda *args:(_ for _ in ()).throw(ValueError('bad')))
    monkeypatch.setattr(b,'cached_obj',lambda *args:None)
    calls=[]
    def fetch(model, root):
        calls.append(model)
        raise ValueError('No MatterPak download link; check credentials/access')
    monkeypatch.setattr(scene_assets,'fetch_matterpak',fetch)
    with pytest.raises(ValueError,match='credentials/access'):b.prepare('m',{}, annotation={**json.loads((Path(__file__).resolve().parents[1]/'scenes/9A46aT7FCPg.json').read_text()), 'model':'m'})
    assert calls==['m'] and (bundle/'scene.json').read_text()=='previous'


def human_fixture(tmp_path, monkeypatch):
    import io
    payload = b'glTF-pinned-test-payload'
    monkeypatch.setattr(b, 'ROOT', tmp_path)
    monkeypatch.setattr(b, 'HUMAN_SHA256', hashlib.sha256(payload).hexdigest())
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return io.BytesIO(payload)
    monkeypatch.setattr(b.urllib.request, 'urlopen', fetch)
    return payload, calls


def test_human_download_and_offline_cache(tmp_path, monkeypatch):
    payload, calls = human_fixture(tmp_path, monkeypatch)
    target = b.prepare_human()
    assert target.read_bytes() == payload and calls == [b.HUMAN_URL]
    monkeypatch.setattr(b.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Cache used network'))
    assert b.prepare_human() == target


def test_human_repairs_corrupt_cache(tmp_path, monkeypatch):
    payload, calls = human_fixture(tmp_path, monkeypatch)
    target = tmp_path/'frontend/public/models/Survivor.glb'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'corrupt')
    assert b.prepare_human().read_bytes() == payload
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['checksum', 'interrupted', 'oversize'])
def test_human_failed_download_is_atomic(tmp_path, monkeypatch, failure):
    import io
    payload, _ = human_fixture(tmp_path, monkeypatch)
    target = tmp_path/'frontend/public/models/Survivor.glb'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'previous')
    class Interrupted(io.BytesIO):
        def read(self, *args):
            raise OSError('interrupted')
    if failure == 'checksum':
        monkeypatch.setattr(b, 'HUMAN_SHA256', '0'*64)
    elif failure == 'oversize':
        monkeypatch.setattr(b, 'HUMAN_MAX_BYTES', 1)
    else:
        monkeypatch.setattr(b.urllib.request, 'urlopen', lambda *a, **k: Interrupted(payload))
    with pytest.raises(RuntimeError, match='rerun ./setup.sh'):
        b.prepare_human()
    assert target.read_bytes() == b'previous'
    assert not list(target.parent.glob('.survivor-*'))


def test_environment_state_skips_unnamed_distributions(monkeypatch):
    import contextlib
    import importlib.metadata as metadata
    import io
    from types import SimpleNamespace
    distributions = [
        SimpleNamespace(metadata={'Name': 'FastAPI'}, version='1.0'),
        SimpleNamespace(metadata={}, version='0.0'),
        SimpleNamespace(metadata={'Name': None}, version='0.0'),
        SimpleNamespace(metadata={'Name': ''}, version='0.0'),
    ]
    monkeypatch.setattr(metadata, 'distributions', lambda: distributions)
    def probe(args, **kwargs):
        # Execute the actual child-process probe, including its metadata access.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(args[2], {})
        return output.getvalue()
    monkeypatch.setattr(b.subprocess, 'check_output', probe)
    state = b.environment_state(sys.executable, {})
    assert state == {'python': sys.version.split()[0], 'packages': {'fastapi': '1.0'}}
