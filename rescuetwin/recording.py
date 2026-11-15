"""Append-only local recordings. Reading never executes simulation or inference."""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile

ROOT = Path(os.environ.get('RESCUETWIN_RUNS', 'data/runs'))

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def directory(run_id):
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('Invalid run id')
    p = ROOT / run_id
    if not p.is_dir(): raise FileNotFoundError(run_id)
    return p

def manifest(run_id):
    return json.loads((directory(run_id) / 'manifest.json').read_text())

def listing():
    result = []
    for p in ROOT.glob('*/manifest.json'):
        try: result.append(json.loads(p.read_text()))
        except (ValueError, OSError): continue
    return sorted(result, key=lambda r: r['created_at'], reverse=True)

def artifact(run_id, name):
    if Path(name).name != name: raise ValueError('Invalid artifact')
    p = directory(run_id) / 'artifacts' / name
    if not p.is_file() or p.is_symlink(): raise FileNotFoundError(name)
    return p

def timeline(run_id, start=0, limit=25):
    rows = []
    for p in sorted((directory(run_id) / 'chunks').glob('*.json.gz')):
        if int(p.stem.split('.')[0]) < start: continue
        if len(rows) >= limit: break
        try:
            with gzip.open(p, 'rt') as f: rows.append(json.load(f))
        except (OSError, ValueError, EOFError): break
    return rows

class Recorder:
    def __init__(self, sim, experiment=None):
        self.id = uuid.uuid4().hex
        self.path = ROOT / self.id
        (self.path / 'chunks').mkdir(parents=True)
        (self.path / 'artifacts').mkdir()
        self.seq = 0
        self.last_decision=None;self.last_tracks=None;self.last_reports=None
        scene = sim.scene.public()
        self.put('scene.json', encoded(scene))
        avatar_root=Path('frontend/public/models')
        if (avatar_root/'humans.json').is_file():self.put('avatar-humans.json',(avatar_root/'humans.json').read_bytes())
        for src in avatar_root.glob('*.glb'):
            self.put('avatar-'+src.name, src.read_bytes())
        assets = {}
        if sim.scene.bundle:
            for name in set(sim.scene.meta.get('assets') or {}) | {'floor.png', 'mesh.gltf', 'preview.png'}:
                src = sim.scene.bundle / name
                if not src.is_file() or src.is_symlink() or Path(name).name != name: continue
                sha = hashlib.sha256(src.read_bytes()).hexdigest()
                store = ROOT / 'assets' / sha
                store.parent.mkdir(exist_ok=True)
                if not store.exists(): shutil.copyfile(src, store)
                dest = self.path / 'artifacts' / name
                try: os.link(store, dest)
                except OSError: shutil.copyfile(store, dest)
                assets[name] = sha
        try:
            revision = subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()
            diff = subprocess.check_output(['git','diff','HEAD'])
        except (OSError, subprocess.CalledProcessError): revision, diff = 'unknown', b''
        code_files={}
        for base in ('rescuetwin','frontend/src','scripts'):
            for source in Path(base).rglob('*'):
                if source.is_file() and source.suffix in ('.py','.ts','.css'):
                    code_files[str(source)]=hashlib.sha256(source.read_bytes()).hexdigest()
        from . import core
        runtime={k:v for k,v in vars(core).items() if k.isupper() and isinstance(v,(str,int,float,bool,dict))}
        weights=Path(os.environ.get('YOLO_WEIGHTS','data/weights/yolo26n.pt'))
        weight_sha=hashlib.sha256(weights.read_bytes()).hexdigest() if weights.is_file() else None
        self.meta = dict(code_sha256=digest(code_files),code_files=code_files,runtime=runtime,vision_weights_sha256=weight_sha,schema='rescuetwin.run/1', id=self.id, created_at=time.time(), scene_id=sim.scene.id,
                         scene_name=sim.scene.name, scene_sha256=digest(scene), scenario_sha256=digest(sim.scenario),
                         scenario=sim.scenario, mode=sim.mode, strategy=(experiment or {}).get('policy',sim.strategy), perception=sim.perception_mode,
                         revision=revision, diff_sha256=hashlib.sha256(diff).hexdigest(), assets=assets,
                         hazard_time_scale=__import__('rescuetwin.core',fromlist=['HAZARD_TIME_SCALE']).HAZARD_TIME_SCALE, tick_s=.2, coordinate_frame='model_xy_metres_grid_column_row', experiment=experiment, status='recording', complete=False, entries=0)
        self.flush()
        self.capture(sim, 'start')
    def flush(self):
        tmp = self.path / 'manifest.tmp'
        tmp.write_bytes(encoded(self.meta)); tmp.replace(self.path / 'manifest.json')
    def put(self, name, data):
        if Path(name).name != name: raise ValueError('Invalid artifact')
        p = self.path / 'artifacts' / name
        if not p.exists(): p.write_bytes(data)
        return name
    def event(self, kind, data, time_s):
        row = dict(seq=self.seq, time=time_s, kind=kind, data=data)
        p = self.path / 'chunks' / f'{self.seq:08d}.json.gz'
        tmp = p.with_suffix('.tmp')
        with gzip.open(tmp, 'wt', compresslevel=1) as f: json.dump(row, f, allow_nan=False)
        tmp.replace(p)
        self.seq += 1
        self.meta['entries'] = self.seq
        if kind not in ('tick','frame'):
            self.meta.setdefault('highlights',[]).append(dict(seq=row['seq'],time=time_s,kind=kind))
        self.flush()
    def capture(self, sim, kind='tick'):
        from .contracts import observation
        signature=digest(sim.decisions)
        tracks=digest([(d['id'],d['status']) for d in sim.detections])
        reports=digest(sim.reports)
        if kind=='tick':
            if tracks!=self.last_tracks:kind='detection'
            elif reports!=self.last_reports:kind='report'
            elif signature!=self.last_decision:kind='decision'
        self.last_decision=signature;self.last_tracks=tracks;self.last_reports=reports
        tools=getattr(sim,'agent_tools',None)
        self.event(kind, dict(state=sim.state(), observation=observation(sim), actions=dict(active=tools.active,history=tools.history) if tools else None), sim.time)
        if sim.status not in ('running','paused','idle') and not self.meta['complete']:
            self.meta.update(complete=True, status=sim.status, metrics=sim.state()['metrics'])
            self.flush()
    def frame(self, body, result, time_s):
        seq = self.seq
        rgb = self.put(f'{seq}-rgb.jpg', base64.b64decode(body['rgb'].split(',')[-1]))
        depth = self.put(f'{seq}-depth.f32.gz', gzip.compress(base64.b64decode(body['depth']),compresslevel=1))
        self.event('frame', dict(input={k:v for k,v in body.items() if k not in ('rgb','depth')}, rgb=rgb, depth=depth, result=result), time_s)
    def close(self, reason='interrupted'):
        if not self.meta['complete']:
            self.meta.update(status=reason)
            self.flush()

def archive(run_id):
    root = directory(run_id)
    target = root / 'download.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for parent in ('artifacts','chunks'):
            for p in (root / parent).iterdir():
                if p.is_file() and not p.is_symlink(): z.write(p, p.relative_to(root))
        z.write(root / 'manifest.json', 'manifest.json')
    return target

def snapshot(run_id, seq):
    root=directory(run_id);state=frame=None
    for p in reversed(sorted((root/'chunks').glob('*.json.gz'))):
        if int(p.name.split('.')[0])>seq:continue
        with gzip.open(p,'rt') as f:row=json.load(f)
        if state is None and 'state' in row['data']:state=row
        if frame is None and row['kind']=='frame':frame=row
        if state is not None and frame is not None:break
    return dict(snapshot=state,frame=frame)
