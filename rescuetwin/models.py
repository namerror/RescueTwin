"""Local prepared-model catalog. Geometry availability does not certify a mission map."""
import json
from pathlib import Path

from . import scenes as registry


def catalog(scenes):
    result = {}
    if not registry.DATA.is_dir():
        return result
    by_model = {(s.meta.get('frame') or {}).get('model'): s for s in scenes.values()}
    for directory in sorted(registry.DATA.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        model = directory.name
        scene = by_model.get(model)
        mesh = (scene.meta.get('mesh') or {}) if scene else {}
        if mesh.get('format') == 'obj':
            result[model] = dict(id=model, name=scene.name, status='simulation-ready',
                                 scene_id=scene.id, file=mesh['file'], materials=mesh.get('materials', []),
                                 textured=bool(mesh.get('textured')), transform=mesh.get('transform'),
                                 source=mesh.get('source'), reason='Validated navigation bundle and verified exit.',
                                 _directory=scene.bundle, _assets=set(scene.meta.get('assets') or {}) | {mesh['file']})
            continue
        source = directory / 'source-assets'
        if source.is_symlink():
            continue
        try:
            inspection = json.loads((source / 'inspection.json').read_text())
            # Prefer the full-footprint defurnished mesh over partial UV-bearing chunks.
            candidates = [m for m in inspection.get('meshes', [])
                          if m.get('downloaded') and m.get('file') and Path(m['file']).name == m['file']
                          and (source / m['file']).is_file()
                          and (source / m['file']).resolve().is_relative_to(source.resolve())]
            candidates.sort(key=lambda m: (m.get('filename') == 'out_res_50k.obj', m.get('triangles', 0)), reverse=True)
            if not candidates:
                continue
            mesh = candidates[0]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        result[model] = dict(id=model, name=model, status='preview-only', scene_id=None,
                             file=mesh['file'], materials=[], textured=False, transform=None,
                             source='Matterport downloaded OBJ; untextured geometry preview',
                             reason='Verified exits, a scoped floor and validated navigation are required before simulation.',
                             _directory=source, _assets={mesh['file']})
    return result


def public(entry):
    return {k: v for k, v in entry.items() if not k.startswith('_')}


def asset(entry, name):
    """Serve only the catalog's explicit assets, never source metadata or credentials."""
    root = entry['_directory'].resolve()
    path = (root / name).resolve()
    if Path(name).name != name or name not in entry['_assets'] or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('No such model file')
    return path
