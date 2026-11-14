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
    assert 'mtllib '+mtls[0] in (out/'source.obj').read_text()


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
