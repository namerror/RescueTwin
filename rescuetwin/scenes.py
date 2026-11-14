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
    grid = data['grid']
    blocked = {tuple(p) for cells in data['blocked'].values() for p in cells}
    exits = [tuple(e['cell']) for e in data['exits']]
    if not exits:
        raise ValueError(f'{directory}: no verified exits')
    rooms = [dict(id=r['id'], name=r['name'], label=r.get('label'), cells=[tuple(p) for p in r['cells']],
                  observe=[tuple(p) for p in r['observe']], polygon=r.get('polygon')) for r in data['rooms']]
    meta = {k: data.get(k) for k in ('frame', 'grid', 'mesh', 'floor_image', 'costar', 'checks', 'provenance',
                                     'assumptions', 'not_exits', 'skipped_rooms')}
    meta['exit_info'] = data['exits']
    meta['blocked_kinds'] = {k: len(v) for k, v in data['blocked'].items()}
    meta['exterior'] = data['blocked'].get('exterior', [])
    scene = Scene(id=data['id'], name=data['name'], width=grid['width'], height=grid['height'],
                  resolution=grid['resolution'], walls=blocked, exits=exits, rooms=rooms, source=data['source'],
                  origin=tuple(grid['origin']), robot_start=tuple(data.get('robot_start') or exits[0]),
                  time_limit=float(data.get('time_limit', 600)), meta=meta, bundle=directory)
    for p in exits + [p for r in rooms for p in r['observe']]:
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
    return real[0] if real else scenes['development-apartment']
