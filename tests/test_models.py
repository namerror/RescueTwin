"""Incomplete scan geometry is browsable without becoming a simulation scene."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rescuetwin import api, models, scenes


def downloaded(root, model='scan'):
    directory = root / model / 'source-assets'
    directory.mkdir(parents=True)
    (directory / 'mesh.obj').write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    (directory / 'inspection.json').write_text(json.dumps({'meshes': [
        {'file': 'mesh.obj', 'filename': 'out_res_50k.obj', 'downloaded': True, 'triangles': 1}]}))
    return directory


def test_preview_never_enters_simulation_registry(tmp_path, monkeypatch):
    downloaded(tmp_path)
    monkeypatch.setattr(scenes, 'DATA', tmp_path)
    assert list(models.catalog({})) == ['scan']
    entry = models.catalog({})['scan']
    assert models.public(entry)['scene_id'] is None
    assert entry['status'] == 'preview-only' and not entry['textured']
    assert not any(k.startswith('_') for k in models.public(entry))
    assert list(scenes.available()) == ['development-apartment']
    client = TestClient(api.app)
    assert client.get('/api/models').json()[0]['id'] == 'scan'
    assert client.get('/api/models/scan/files/mesh.obj').status_code == 200
    assert client.get('/api/models/scan/files/inspection.json').status_code == 404
    assert client.get('/api/models/missing/files/mesh.obj').status_code == 404
    assert client.post('/api/scene', json={'id': 'scan'}).status_code == 404


def test_model_assets_reject_traversal_and_symlink_escape(tmp_path, monkeypatch):
    source = downloaded(tmp_path)
    monkeypatch.setattr(scenes, 'DATA', tmp_path)
    entry = models.catalog({})['scan']
    with pytest.raises(ValueError):
        models.asset(entry, '../source-assets/mesh.obj')
    outside = tmp_path / 'private.obj'
    outside.write_text('not a public asset')
    (source / 'mesh.obj').unlink()
    (source / 'mesh.obj').symlink_to(outside)
    with pytest.raises(ValueError):
        models.asset(entry, 'mesh.obj')
    assert not models.catalog({})


def test_catalog_uses_validated_textured_bundle(tmp_path, monkeypatch):
    from test_scene import write_bundle
    directory = write_bundle(tmp_path / 'ready' / 'bundle')
    data = json.loads((directory / 'scene.json').read_text())
    data.update(frame={'model': 'ready'}, mesh={'format': 'obj', 'file': 'scan.obj',
                 'textured': True, 'materials': ['scan.mtl']}, assets=['scan.obj', 'scan.mtl', 'texture.jpg'])
    (directory / 'scene.json').write_text(json.dumps(data))
    for name in data['assets']:
        (directory / name).write_text('asset')
    monkeypatch.setattr(scenes, 'DATA', tmp_path)
    entry = models.catalog(scenes.available())['ready']
    assert entry['status'] == 'simulation-ready' and entry['scene_id'] == 'mini'
    assert entry['textured'] and entry['materials'] == ['scan.mtl']
    assert models.asset(entry, 'texture.jpg') == (directory / 'texture.jpg').resolve()
    with pytest.raises(ValueError):
        models.asset(entry, 'scene.json')
