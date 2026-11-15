import json
from pathlib import Path
import zipfile
import pytest
from scripts.scene_assets import obj_data, package, safe_file, import_package
from rescuetwin.scenes import asset_file
from types import SimpleNamespace


def textured(tmp_path):
    (tmp_path/'wall.jpg').write_bytes(b'texture fixture')
    (tmp_path/'wall.mtl').write_text('newmtl wall\nmap_Kd wall.jpg\n')
    obj=tmp_path/'room.obj'
    obj.write_text('mtllib wall.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl wall\nf 1/1 2/2 3/3\n')
    return obj


def test_package_preserves_and_rewrites_materials(tmp_path):
    obj=textured(tmp_path);out=tmp_path/'out';out.mkdir()
    vertices,faces,assets,mtls,complete=package(obj,out)
    assert complete and len(vertices)==3 and len(faces)==1
    assert len(assets)==3 and len(mtls)==1
    assert 'wall.jpg' not in (out/mtls[0]).read_text()
    assert 'mtllib '+mtls[0] in (out/next(n for n in assets if n.endswith('.obj'))).read_text()


def test_missing_material_and_uvs_fail_closed(tmp_path):
    obj=textured(tmp_path);out=tmp_path/'out';out.mkdir()
    (tmp_path/'wall.jpg').unlink()
    with pytest.raises(ValueError,match='Missing'):package(obj,out)
    obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    with pytest.raises(ValueError,match='UV'):package(obj,out)
    assert package(obj,out,allow_untextured=True)[-1] is False


def test_invalid_indices_and_traversal(tmp_path):
    obj=tmp_path/'bad.obj';obj.write_text('v 0 0 0\nf 1 2 3\n')
    with pytest.raises(ValueError,match='index'):obj_data(obj)
    with pytest.raises(ValueError):safe_file(tmp_path,'../outside')
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape.obj','bad')
    with pytest.raises(ValueError,match='Unsafe'):import_package('m',archive,tmp_path)


def test_asset_allowlist(tmp_path):
    (tmp_path/'source.obj').write_text('fixture');(tmp_path/'private.txt').write_text('private')
    scene=SimpleNamespace(bundle=tmp_path,meta={'assets':{'source.obj':{}}})
    assert asset_file(scene,'source.obj')[0]==tmp_path/'source.obj'
    for name in ('private.txt','../source.obj'):
        with pytest.raises(ValueError):asset_file(scene,name)


def test_import_transaction_and_registration(tmp_path):
    from scripts.scene_assets import import_mesh
    from rescuetwin.scenes import load_bundle
    model='test';bundle=tmp_path/model/'bundle';bundle.mkdir(parents=True)
    scene={'schema':'rescuetwin.scene/1','id':'synthetic-material-test','name':'Synthetic material test','source':'synthetic',
           'grid':{'origin':[0,0],'resolution':.5,'width':4,'height':4},'blocked':{'wall':[]},
           'exits':[{'cell':[1,1]}],'robot_start':[1,1],
           'rooms':[{'id':'r','name':'Room','label':'Bedroom','cells':[[c,r] for r in range(4) for c in range(4)],'observe':[[2,2]]}],
           'mesh':{'wall_height':2.4},'provenance':[],'assumptions':[],'checks':{'passed':True}}
    manifest=bundle/'scene.json';manifest.write_text(json.dumps(scene));before=manifest.read_bytes()
    obj=textured(tmp_path)
    # Tiny triangle fails floor coverage and cannot replace the published bundle.
    with pytest.raises(ValueError,match='validation failed'):import_mesh(model,obj,tmp_path)
    assert manifest.read_bytes()==before
    obj.write_text('mtllib wall.mtl\nv 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nusemtl wall\nf 1/1 2/2 3/3 4/4\n')
    import_mesh(model,obj,tmp_path)
    loaded=load_bundle(bundle)
    assert loaded.meta['mesh']['textured'] and loaded.source=='synthetic'
    assert loaded.meta['mesh']['validation']['floor_coverage']==1
    assert loaded.meta['mesh']['transform']==[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
    assert (bundle/'scene.before-source.json').read_bytes()==before


# ---- Furniture-aware reconciliation (synthetic fixtures; not sponsor geometry) ----
# Grid 10x6 at 0.5 m. Schematic wall at col 5 with a door gap at rows 2-3. Room A cols 0-4, room B cols 6-9.

def quad(a, b, c, d): return [a, b, c, d]


def vbox(x0, x1, y0, y1, z0, z1):
    """Closed axis-aligned box as quads."""
    p = lambda x, y, z: (x, y, z)
    return [quad(p(x0,y0,z1),p(x1,y0,z1),p(x1,y1,z1),p(x0,y1,z1)),
            quad(p(x0,y0,z0),p(x1,y0,z0),p(x1,y0,z1),p(x0,y0,z1)), quad(p(x0,y1,z0),p(x1,y1,z0),p(x1,y1,z1),p(x0,y1,z1)),
            quad(p(x0,y0,z0),p(x0,y1,z0),p(x0,y1,z1),p(x0,y0,z1)), quad(p(x1,y0,z0),p(x1,y1,z0),p(x1,y1,z1),p(x1,y0,z1))]


def write_obj(path, quads, material=None):
    lines = ([f'mtllib {material}.mtl', f'usemtl {material}'] if material else [])
    n = 0
    for q in quads:
        lines += [f'v {x} {y} {z}' for x, y, z in q] + ['vt 0 0'] * 4
        lines.append('f ' + ' '.join(f'{n+i}/{n+i}' for i in range(1, 5))); n += 4
    path.write_text('\n'.join(lines) + '\n')
    return path


def apartment(tmp_path, model='furnished'):
    bundle = tmp_path/model/'bundle'; bundle.mkdir(parents=True)
    walls = [[5, r] for r in (0, 1, 4, 5)]
    scene = {'schema':'rescuetwin.scene/1','id':'synthetic-furniture-test','name':'Synthetic furniture test','source':'synthetic',
             'grid':{'origin':[0,0],'resolution':.5,'width':10,'height':6},'blocked':{'wall':walls},
             'exits':[{'cell':[0,2]}],'robot_start':[0,2],
             'rooms':[{'id':'a','name':'A','label':'Living','cells':[[c,r] for r in range(6) for c in range(5)],'observe':[[2,2]]},
                      {'id':'b','name':'B','label':'Bedroom','cells':[[c,r] for r in range(6) for c in range(6,10)],'observe':[[8,2]]}],
             'mesh':{'wall_height':2.4},'provenance':[],'assumptions':[],'checks':{'passed':True}}
    (bundle/'scene.json').write_text(json.dumps(scene))
    return bundle, scene


SHELL = [quad((0,0,0),(5,0,0),(5,3,0),(0,3,0)),                      # floor
         quad((2.75,0,0),(2.75,1,0),(2.75,1,2.4),(2.75,0,2.4)),       # wall below the door gap
         quad((2.75,2,0),(2.75,3,0),(2.75,3,2.4),(2.75,2,2.4))]       # wall above the door gap
DOOR_LEAF = [quad((2.75,1,0),(2.75,2,0),(2.75,2,2.1),(2.75,1,2.1))]  # closed door in the gap
TABLE = vbox(3.9, 4.6, .9, 1.6, 0, .75)                              # over room B's observation point


def test_furniture_blocks_door_leaf_clears_and_observation_relocates(tmp_path):
    from scripts.scene_assets import reconcile, obj_data
    _, scene = apartment(tmp_path)
    v, f, _, _ = obj_data(write_obj(tmp_path/'s.obj', SHELL + DOOR_LEAF + TABLE))
    checks, kinds, rooms, skipped, records = reconcile(scene, v, f)
    assert checks['passed'] and not skipped
    furniture = {tuple(c) for c in kinds['furniture']}
    assert furniture == {(c, r) for c in (7, 8, 9) for r in (1, 2, 3)}
    assert {tuple(p['cell']) for p in records['portals']} == {(5, 2), (5, 3)}
    assert all('door gap' in p['reason'] and p['thickness_m'] <= .15 for p in records['portals'])
    assert not furniture & {tuple(p['cell']) for p in records['portals']}  # furniture is never cleared
    (moved,) = records['relocations']
    assert moved['from'] == [8, 2] and moved['reason'] == 'furniture'
    room_b = next(r for r in rooms if r['id'] == 'b')
    assert tuple(moved['to']) not in furniture and moved['to'] in room_b['cells'] and room_b['observe'] == [moved['to']]
    assert scene['rooms'][1]['observe'] == [[8, 2]]  # pure: input untouched


def test_bulky_object_in_doorway_is_not_a_portal(tmp_path):
    from scripts.scene_assets import reconcile, obj_data
    _, scene = apartment(tmp_path)
    v, f, _, _ = obj_data(write_obj(tmp_path/'s.obj', SHELL + vbox(2.3, 3.2, 1.0, 2.0, 0, .9)))  # cabinet across the gap
    checks, kinds, rooms, skipped, records = reconcile(scene, v, f)
    assert not records['portals']
    assert [s['id'] for s in skipped] == ['b'] and 'no reachable cell' in skipped[0]['reason']
    assert checks['passed'] and [r['id'] for r in rooms] == ['a']


def test_exit_blocked_by_furniture_fails_and_keeps_bundle(tmp_path):
    from scripts.scene_assets import import_mesh
    bundle, _ = apartment(tmp_path); before = (bundle/'scene.json').read_bytes()
    obj = write_obj(tmp_path/'s.obj', SHELL + DOOR_LEAF + vbox(-.1, .6, .9, 1.6, 0, .9))  # sofa on the exit
    with pytest.raises(ValueError, match='validation failed'): import_mesh('furnished', obj, tmp_path, allow_untextured=True)
    assert (bundle/'scene.json').read_bytes() == before
    report = json.loads((tmp_path/'furnished'/'source-mesh-validation.json').read_text())
    assert not report['checks']['required_points_connected'] and [0, 2] in report['checks']['disconnected_points']


def test_textured_furnished_zip_imports_end_to_end(tmp_path):
    from scripts.scene_assets import import_package
    from rescuetwin.scenes import load_bundle
    bundle, _ = apartment(tmp_path)
    src = tmp_path/'pak'; src.mkdir()
    (src/'tex.jpg').write_bytes(b'texture fixture'); (src/'scan.mtl').write_text('newmtl scan\nmap_Kd tex.jpg\n')
    write_obj(src/'scan.obj', SHELL + DOOR_LEAF + TABLE, material='scan')
    archive = tmp_path/'pak.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name in ('scan.obj', 'scan.mtl', 'tex.jpg'): z.write(src/name, name)
    import_package('furnished', archive, tmp_path)
    loaded = load_bundle(bundle)
    assert loaded.meta['mesh']['textured'] and loaded.meta['mesh']['validation']['furniture_cells'] == 9
    assert len(loaded.meta['obstacle_overlay']['furniture']) == 9
    assert all(not loaded.free(tuple(c)) for c in loaded.meta['obstacle_overlay']['furniture'])
    assert loaded.free((5, 2)) and loaded.free((5, 3))  # door portals stay traversable
    assert len(loaded.meta['reconciliation']['relocations']) == 1
    assert any('furniture' in p['derivation'] for p in loaded.meta['provenance'])


def test_matterpak_member_selection_skips_point_cloud():
    from scripts.scene_assets import matterpak_members
    obj, keep = matterpak_members(['cloud.xyz', 'abc.obj', 'abc.mtl', 'abc_000.jpg', 'colorplan_000.jpg', 'floorplan_000.jpg'])
    assert obj == 'abc.obj' and keep == ['abc.obj', 'abc.mtl', 'abc_000.jpg']
    with pytest.raises(ValueError, match='exactly one OBJ'): matterpak_members(['a.obj', 'b.obj'])


def test_ambient_map_keeps_diffuse_and_validates_dependency(tmp_path):
    obj = textured(tmp_path); out = tmp_path/'out'; out.mkdir()
    (tmp_path/'wall.mtl').write_text('newmtl wall\nmap_Ka wall.jpg\nmap_Kd wall.jpg\n')
    _, _, assets, mtls, complete = package(obj, out)
    assert complete
    text = (out/mtls[0]).read_text()
    assert 'map_Kd ' in text and 'map_Ka' not in text
    (tmp_path/'wall.mtl').write_text('newmtl wall\nmap_Ka missing.jpg\nmap_Kd wall.jpg\n')
    with pytest.raises(ValueError, match='Missing'): package(obj, out)


def test_failed_publication_preserves_previous_assets(tmp_path, monkeypatch):
    from scripts.scene_assets import import_mesh
    import scripts.scene_assets as mod
    bundle, _ = apartment(tmp_path)
    obj = write_obj(tmp_path/'s.obj', SHELL + DOOR_LEAF)
    import_mesh('furnished', obj, tmp_path, allow_untextured=True)
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    write_obj(obj, SHELL + DOOR_LEAF + TABLE)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(mod.shutil, 'copyfile', fail)
    with pytest.raises(OSError, match='disk full'):
        import_mesh('furnished', obj, tmp_path, allow_untextured=True)
    assert all((bundle/n).read_bytes() == b for n, b in before.items())
    assert not list(bundle.glob('*.next'))


def test_bundle_assets_download_and_redacted_status(tmp_path, monkeypatch):
    import io
    import sys
    from scripts import prepare_scene, scene_assets
    monkeypatch.setitem(sys.modules, 'prepare_scene', prepare_scene)
    secret = 'https://example.invalid/pak.zip?secret=DO_NOT_SAVE'
    def graph(model, **kwargs):
        assert 'assets{url}' in prepare_scene.QUERY and 'resources' not in prepare_scene.QUERY
        return {'bundle': {'assets': [{'url': secret}]}}
    monkeypatch.setattr(prepare_scene, 'graph', graph)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('scan.obj', 'fixture'); z.writestr('scan.mtl', 'fixture'); z.writestr('tex.jpg', b'image')
    monkeypatch.setattr(scene_assets, 'RangeFile', lambda url: io.BytesIO(data.getvalue()))
    scene_assets.fetch_matterpak('m', tmp_path)
    status = tmp_path/'m/source-assets/matterpak-status.json'
    assert 'DO_NOT_SAVE' not in status.read_text()
    before = status.read_bytes()
    monkeypatch.setattr(scene_assets, 'RangeFile', lambda url: io.BytesIO(b'broken'))
    with pytest.raises(ValueError, match='download failed'): scene_assets.fetch_matterpak('m', tmp_path)
    assert status.read_bytes() == before
