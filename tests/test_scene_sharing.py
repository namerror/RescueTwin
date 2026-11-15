"""Portable scene recipes, cache behavior and batch failures without network calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest
from scripts import bringup as b, scene_sharing as s

RECIPE = json.loads((Path(__file__).resolve().parents[1]/'scenes/9A46aT7FCPg.json').read_text())


def recipe(model='m'):
    return {**deepcopy(RECIPE), 'model': model, 'source_to_model': [[1,0,0,2],[0,1,0,3],[0,0,1,0],[0,0,0,1]], 'floor_elevation_m': 1.2}


def manifest(*models):
    return {'schema': s.SCHEMA, 'default_model': models[0],
            'scenes': [{'model': m, 'annotation': recipe(m)} for m in models]}


def bundle(root, model='m', annotation=None):
    out = root/'data/prepared'/model/'bundle'
    out.mkdir(parents=True)
    assets = {}
    for name in ('scan.obj', 'scan.mtl', 'texture.jpg'):
        (out/name).write_text(name)
        assets[name] = {'sha256': hashlib.sha256(name.encode()).hexdigest()}
    data = {'schema': 'rescuetwin.scene/1', 'id': model+'-floor', 'source': 'sponsor-source',
            'frame': {'model': model}, 'checks': {'passed': True}, 'exits': [{'id': 'entry'}],
            'mesh': {'file': 'scan.obj', 'materials': ['scan.mtl'], 'textured': True, 'validation': {'passed': True}},
            'assets': assets}
    if annotation:
        data.update(annotation=annotation, annotation_sha256=s.digest(annotation))
    s.write_json(out/'scene.json', data)
    return out


def test_export_roundtrip_uses_local_recipe_without_dependencies(tmp_path):
    expected = recipe()
    bundle(tmp_path, annotation=expected)
    target = tmp_path/'share.json'
    data = s.export(tmp_path, target, 'm')
    assert s.validate(json.loads(target.read_text())) == manifest('m') == data
    assert not (tmp_path/'scenes').exists()
    assert not list(tmp_path.rglob('*.next'))


def test_export_requires_matching_annotation_and_intact_assets(tmp_path):
    out = bundle(tmp_path)
    with pytest.raises(ValueError, match='No annotated prepared scenes'):
        s.export(tmp_path, tmp_path/'share.json', 'm')
    s.write_json(tmp_path/'scenes/m.json', recipe('other'))
    with pytest.raises(ValueError, match='match'): s.export(tmp_path, tmp_path/'share.json', 'm')
    s.write_json(tmp_path/'scenes/m.json', recipe())
    (out/'texture.jpg').unlink()
    with pytest.raises(ValueError, match='corrupt'): s.export(tmp_path, tmp_path/'share.json', 'm')
    assert not (tmp_path/'share.json').exists()


def test_export_skips_unannotated_scenes_and_selects_included_default(tmp_path):
    unannotated = bundle(tmp_path, model='unannotated')
    # Unannotated scenes are skipped before their assets are checked.
    (unannotated/'texture.jpg').unlink()
    bundle(tmp_path, model='embedded', annotation=recipe('embedded'))
    bundle(tmp_path, model='tracked')
    s.write_json(tmp_path/'scenes/tracked.json', recipe('tracked'))
    data = s.export(tmp_path, tmp_path/'share.json', 'unannotated')
    assert data == manifest('embedded', 'tracked')


@pytest.mark.parametrize('change', [
    lambda d: d.update(schema='future'),
    lambda d: d['scenes'].append(deepcopy(d['scenes'][0])),
    lambda d: d['scenes'][0].update(model='../escape'),
    lambda d: d.update(default_model='missing'),
    lambda d: d['scenes'][0].pop('annotation'),
    lambda d: d['scenes'][0]['annotation'].update(exits=[]),
    lambda d: d['scenes'][0]['annotation'].update(resolution=float('nan')),
    lambda d: d['scenes'][0]['annotation'].update(source_to_model=[[0]*4]*4),
])
def test_invalid_manifest_has_no_side_effects(change, monkeypatch):
    data = manifest('m')
    change(data)
    monkeypatch.setattr(b, 'prepare', lambda *a, **k: pytest.fail('must validate first'))
    with pytest.raises(ValueError): b.import_scenes(data, {})


def test_batch_attempts_all_and_success_selects_default(monkeypatch):
    calls = []
    def prepare(model, env, annotation):
        calls.append(model)
        assert annotation == recipe(model)
        if model == 'bad': raise SystemExit('source unavailable')
        return model+'-floor'
    monkeypatch.setattr(b, 'prepare', prepare)
    with pytest.raises(ValueError, match='bad'): b.import_scenes(manifest('m', 'bad', 'last'), {})
    assert calls == ['m', 'bad', 'last']
    assert b.import_scenes(manifest('m', 'last'), {}) == 'm-floor'


def test_import_cache_reused_but_changed_recipe_rebuilds(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'ROOT', tmp_path)
    bundle(tmp_path, annotation=recipe())
    monkeypatch.setattr(b, 'check_bundle', lambda *a: 'm-floor')
    assert b.prepare('m', {}, annotation=recipe()) == 'm-floor'
    assert b.prepare('m', {}) == 'm-floor'
    changed = recipe(); changed['resolution'] = .5
    with pytest.raises(ValueError, match='CoStar kit'): b.prepare('m', {}, annotation=changed)
    assert s.local_annotation(tmp_path, 'm') == recipe()


def test_export_command_never_bootstraps_or_starts_services(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'ROOT', tmp_path)
    bundle(tmp_path, annotation=recipe())
    monkeypatch.setattr(sys, 'argv', ['bringup', '--export-scenes', str(tmp_path/'share.json')])
    monkeypatch.setattr(b, 'bootstrap', lambda *a: pytest.fail('no install'))
    monkeypatch.setattr(b, 'serve', lambda *a: pytest.fail('no startup'))
    monkeypatch.chdir(tmp_path)
    b.main()
    assert json.loads((tmp_path/'share.json').read_text()) == manifest('m')


@pytest.mark.parametrize('join', ['apartment', 'commercial', 'residential'])
def test_import_publication_and_registration_recipe(tmp_path, monkeypatch, join):
    from scripts import prepare_scene, scene_assets
    monkeypatch.setitem(sys.modules, 'prepare_scene', prepare_scene)
    monkeypatch.setitem(sys.modules, 'scene_assets', scene_assets)
    monkeypatch.setattr(b, 'ROOT', tmp_path)
    old = bundle(tmp_path, annotation=recipe())
    before = (old/'scene.json').read_bytes()
    files = (['residential-listings.csv', 'data/residential-listings.jsonl', 'data/residential-listing-features.jsonl']
             if join == 'residential' else ['commercial-properties.csv', 'data/commercial-property.jsonl', 'data/apartment-listing.jsonl'])
    for name in [*files, 'schemas/enums.json']:
        p = tmp_path/'data/costar-hackathon-kit'/name
        p.parent.mkdir(parents=True, exist_ok=True); p.touch()
    changed = recipe(); changed['resolution'] = .5
    if join != 'apartment':
        changed.pop('floor_plan_id')
    if join == 'residential':
        changed.pop('property_id')
        changed['residential_listing_id'] = 123
    checks = []
    def check(path, model):
        checks.append(path)
        return 'm-floor'
    monkeypatch.setattr(b, 'check_bundle', check)
    cache = []
    monkeypatch.setattr(b, 'cached_obj', lambda *a: tmp_path/'mesh.obj' if cache else None)
    monkeypatch.setattr(scene_assets, 'fetch_matterpak', lambda *a: cache.append('download'))
    def fetch(model, stage, panos):
        src = stage/model/'source'; src.mkdir()
        (src/'plan.png').write_text('plan')
        s.write_json(src/'source.json', {'assets': {'floorplans': [{'file': 'plan.png'}]}})
    monkeypatch.setattr(prepare_scene, 'fetch', fetch)
    def build(model, stage, path, kit):
        assert json.loads(path.read_text()) == changed
        assert kit == tmp_path/'data/costar-hackathon-kit'
        staged = bundle(stage)  # helper nests under data/prepared; move to build target
        staged.rename(stage/model/'bundle')
    monkeypatch.setattr(prepare_scene, 'build', build)
    fail = [True]
    def import_package(model, obj, stage, annotation_path):
        assert json.loads(annotation_path.read_text()) == changed
        if fail[0]: raise ValueError('reconciliation failed')
        out = stage/model/'bundle'
        (out/'scene.before-source.json').write_text('{}')
        (stage/model/'source-mesh-validation.json').write_text('{}')
    monkeypatch.setattr(scene_assets, 'import_package', import_package)
    with pytest.raises(ValueError, match='reconciliation'): b.prepare('m', {}, annotation=changed)
    assert (old/'scene.json').read_bytes() == before
    fail[0] = False
    assert b.prepare('m', {}, annotation=changed) == 'm-floor'
    assert s.local_annotation(tmp_path, 'm') == changed
    assert s.export(tmp_path, tmp_path/'share.json', 'm')['scenes'][0]['annotation'] == changed
    assert b.prepare('m', {}) == 'm-floor'
    assert cache == ['download']


def test_catalog_annotations_roundtrip_without_losing_reviewed_fields(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root/'scenes').glob('*.json')):
        reviewed = json.loads(path.read_text())
        bundle(tmp_path, path.stem, reviewed)
    data = s.export(tmp_path, tmp_path/'share.json', '9A46aT7FCPg')
    for entry in data['scenes']:
        assert entry['annotation'] == json.loads((root/'scenes'/f"{entry['model']}.json").read_text())
