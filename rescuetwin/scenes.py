"""Scene registry: the synthetic fixture plus prepared sponsor bundles (data/prepared/*/bundle)."""
from __future__ import annotations
import json
import os
from pathlib import Path
from .core import Scene, fixture

DATA = Path(os.environ.get('RESCUETWIN_DATA', Path(__file__).resolve().parent.parent / 'data' / 'prepared'))
BUNDLE_FILES = {'mesh.gltf': 'model/gltf+json', 'floor.png': 'image/png', 'preview.png': 'image/png'}
SCHEMA = 'rescuetwin.scene/1'


def load_bundle(directory: Path) -> Scene:
    directory = Path(directory)
    data = json.loads((directory / 'scene.json').read_text())
    if data.get('schema') != SCHEMA:
        raise ValueError(f'{directory}: unsupported scene schema {data.get("schema")!r}')
    if (data.get('checks') or {}).get('passed') is False or ((data.get('mesh') or {}).get('validation') or {}).get('passed') is False:
        raise ValueError('Scene validation failed')
    grid = data['grid']
    blocked = {tuple(p) for cells in data['blocked'].values() for p in cells}
    exits = [tuple(e['cell']) for e in data['exits']]
    if not exits:
        raise ValueError(f'{directory}: no verified exits')
    rooms = [dict(id=r['id'], name=r['name'], label=r.get('label'), cells=[tuple(p) for p in r['cells']],
                  observe=[tuple(p) for p in r['observe']], polygon=r.get('polygon'),
                  area_m2=r.get('area_m2')) for r in data['rooms']]
    meta = {k: data.get(k) for k in ('frame', 'grid', 'mesh', 'floor_image', 'costar', 'checks', 'provenance',
                                     'assumptions', 'not_exits', 'skipped_rooms', 'assets')}
    meta['exit_info'] = data['exits']
    meta['blocked_kinds'] = {k: len(v) for k, v in data['blocked'].items()}
    meta['exterior'] = data['blocked'].get('exterior', [])
    # Operator-only overlay: which blocked cells came from scan furniture vs. scan walls/door frames.
    meta['obstacle_overlay'] = {k: data['blocked'][k] for k in ('furniture', 'source_walls') if k in data['blocked']}
    meta['reconciliation'] = data.get('reconciliation')
    scene = Scene(id=data['id'], name=data['name'], width=grid['width'], height=grid['height'],
                  resolution=grid['resolution'], walls=blocked, exits=exits, rooms=rooms, source=data['source'],
                  origin=tuple(grid['origin']), robot_start=tuple(data.get('robot_start') or exits[0]),
                  time_limit=float(data.get('time_limit', 600)), meta=meta, bundle=directory)
    for p in [tuple(scene.robot_start)] + exits + [p for r in rooms for p in r['observe']]:
        if not scene.free(p):
            raise ValueError(f'{directory}: exit/observation cell {p} is blocked')
    return scene


def available() -> dict[str, Scene]:
    scenes = {}
    if DATA.is_dir():
        for path in sorted(DATA.glob('*/bundle/scene.json')):
            try:
                scene = load_bundle(path.parent)
            except (ValueError, KeyError, OSError) as error:
                print(f'Skipping scene bundle {path.parent}: {error}')
                continue
            scenes[scene.id] = scene
    synthetic = fixture()
    scenes[synthetic.id] = synthetic
    return scenes


def default_scene(scenes: dict[str, Scene]) -> Scene:
    wanted = os.environ.get('RESCUETWIN_SCENE')
    if wanted:
        if wanted not in scenes:
            raise RuntimeError(f'RESCUETWIN_SCENE={wanted} not found; available: {sorted(scenes)}')
        return scenes[wanted]
    real = [s for s in scenes.values() if s.source != 'synthetic']
    model = os.environ.get('RESCUETWIN_MODEL_ID')
    return next((s for s in real if (s.meta.get('frame') or {}).get('model') == model), real[0]) if real else scenes['development-apartment']


def asset_file(scene, name):
    """Only declared, local assets may be served; reject traversal and symlinks out of bundle."""
    import mimetypes
    if scene.bundle is None or Path(name).name != name:
        raise ValueError('No such scene file')
    allowed = set(BUNDLE_FILES) | set(scene.meta.get('assets') or {})
    path = (scene.bundle / name).resolve()
    if name not in allowed or not path.is_relative_to(scene.bundle.resolve()) or not path.is_file():
        raise ValueError('No such scene file')
    return path, BUNDLE_FILES.get(name) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
