"""Inspect sponsor assets and import verified OBJ packages without exposing signed URLs."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from urllib.parse import urljoin, urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path
import numpy as np


def safe_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing or unsafe asset dependency')
    return path


def obj_data(path):
    vertices, uvs, faces, libraries, used = [], [], [], [], set()
    material = None
    for line in path.read_text().splitlines():
        words = line.split()
        if not words or words[0].startswith('#'): continue
        tag, args = words[0], words[1:]
        if tag == 'v': vertices.append([float(x) for x in args[:3]])
        elif tag == 'vt': uvs.append([float(x) for x in args[:2]])
        elif tag == 'mtllib': libraries.extend(args)
        elif tag == 'usemtl': material = ' '.join(args); used.add(material)
        elif tag == 'f':
            indices, texture = [], []
            for word in args:
                parts = word.split('/'); index = int(parts[0])
                index = index - 1 if index > 0 else len(vertices) + index
                if not 0 <= index < len(vertices): raise ValueError('Invalid OBJ vertex index')
                indices.append(index)
                uv = int(parts[1]) if len(parts) > 1 and parts[1] else None
                uv = (uv - 1 if uv > 0 else len(uvs) + uv) if uv is not None else None
                if uv is not None and not 0 <= uv < len(uvs): raise ValueError('Invalid OBJ UV index')
                texture.append(uv)
            if len(indices) < 3: raise ValueError('Invalid OBJ face')
            for i in range(1, len(indices)-1):
                faces.append((indices[0:1]+indices[i:i+2], all(texture[j] is not None for j in (0,i,i+1)), material))
    if not vertices or not faces: raise ValueError('Empty OBJ geometry')
    if any(len(v)!=3 for v in vertices) or not np.isfinite(vertices).all(): raise ValueError('Invalid OBJ geometry')
    if any(len(uv)!=2 for uv in uvs) or (uvs and not np.isfinite(uvs).all()): raise ValueError('Invalid OBJ texture coordinates')
    return np.array(vertices), faces, libraries, used


def package(path, output, allow_untextured=False):
    """Flatten an OBJ package, rewriting only validated dependency references."""
    vertices, faces, libraries, used = obj_data(path)
    names, assets, materials = {}, {}, {}
    def copy(src):
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        name = digest[:20] + src.suffix.lower()
        if src.suffix.lower() not in ('.png','.jpg','.jpeg','.mtl'): raise ValueError('Unsupported material dependency')
        shutil.copyfile(src, output/name); assets[name] = {'sha256':digest}
        names[src] = name
        return name
    for library in libraries:
        src = safe_file(path.parent, library); rewritten = []; current = None
        for line in src.read_text().splitlines():
            words = line.split()
            if words and words[0] == 'newmtl': current = ' '.join(words[1:]); materials[current] = False
            if words and words[0].lower().startswith(('map_', 'bump', 'disp', 'decal', 'refl')):
                if len(words)!=2 or words[0] != 'map_Kd': raise ValueError('Unsupported MTL map/options; normalize package first')
                dep = safe_file(src.parent, words[1]); name = copy(dep)
                line = 'map_Kd '+name
                if current is not None: materials[current] = True
            rewritten.append(line)
        name = copy(src); (output/name).write_text('\n'.join(rewritten)+'\n')
        assets[name]['sha256'] = hashlib.sha256((output/name).read_bytes()).hexdigest()
    textured = bool(used) and all(uv and material and materials.get(material) for _,uv,material in faces)
    if not textured and not allow_untextured: raise ValueError('No complete UV/material mapping; use --allow-untextured only for a labeled geometry experiment')
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith('mtllib '):
            line = 'mtllib '+' '.join(names[safe_file(path.parent,n)] for n in line.split()[1:])
        lines.append(line)
    (output/'source.obj').write_text('\n'.join(lines)+'\n')
    assets['source.obj']={'sha256':hashlib.sha256((output/'source.obj').read_bytes()).hexdigest()}
    return vertices, faces, assets, [names[safe_file(path.parent,n)] for n in libraries], textured


def import_mesh(model, path, root, allow_untextured=False):
    from shapely.geometry import Polygon, Point, box
    from shapely.ops import unary_union
    bundle=root/model/'bundle'
    manifest=bundle/'scene.json'
    scene=json.loads(manifest.read_text())
    if (scene.get('mesh') or {}).get('format')=='obj' and (bundle/'scene.before-source.json').exists():
        scene=json.loads((bundle/'scene.before-source.json').read_text())
    config_path=Path('scenes')/(model+'.json')
    config=json.loads(config_path.read_text()) if config_path.exists() else {}
    matrix=np.array(config.get('source_to_model',np.eye(4).tolist()),dtype=float)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1]): raise ValueError('Invalid source_to_model affine matrix')
    floor=float(config.get('floor_elevation_m',0))
    if not np.isfinite(floor) or abs(np.linalg.det(matrix[:3,:3]))<1e-9:raise ValueError('Invalid source registration')
    matrix[2,3]-=floor
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp); vertices,faces,assets,mtls,textured=package(path,out,allow_untextured)
        vertices=(np.c_[vertices,np.ones(len(vertices))]@matrix.T)[:,:3]
        floor_faces=[]; obstacles=[]
        for indices,_,_ in faces:
            tri=vertices[indices]; lo,hi=tri[:,2].min(),tri[:,2].max()
            poly=Polygon(tri[:,:2])
            if poly.is_valid and poly.area>1e-8 and lo>=-.20 and hi<=.15: floor_faces.append(poly)
            # Project triangles crossing the robot's body/mast band. Conservative, no invented clearance.
            if hi>.15 and lo<1.10:
                from shapely.geometry import MultiPoint
                obstacles.append(MultiPoint(tri[:,:2]).convex_hull.buffer(.05))
        floors=unary_union(floor_faces).buffer(.10); obstacles=unary_union(obstacles)
        grid=scene['grid']; res=grid['resolution']; ox,oy=grid['origin']
        blocked={tuple(c) for group in scene['blocked'].values() for c in group}
        free={(c,r) for r in range(grid['height']) for c in range(grid['width']) if (c,r) not in blocked}
        covered={p for p in free if floors.covers(Point(ox+(p[0]+.5)*res,oy+(p[1]+.5)*res))}
        hits={p for p in free if obstacles.intersects(box(ox+p[0]*res,oy+p[1]*res,ox+(p[0]+1)*res,oy+(p[1]+1)*res))}
        start=tuple(scene['robot_start']); safe=covered-hits; seen=set(); todo=[start] if start in safe else []
        while todo:
            p=todo.pop()
            if p in seen: continue
            seen.add(p)
            todo.extend(q for q in ((p[0]+1,p[1]),(p[0]-1,p[1]),(p[0],p[1]+1),(p[0],p[1]-1)) if q in safe and q not in seen)
        required=[tuple(e['cell']) for e in scene['exits']]+[tuple(p) for room in scene['rooms'] for p in room['observe']]
        wall_cells=scene['blocked'].get('wall',[])
        wall_agreement=sum(obstacles.distance(Point(ox+(c+.5)*res,oy+(r+.5)*res)) <= res for c,r in wall_cells)/max(1,len(wall_cells)) if wall_cells else 1.0
        checks={'wall_agreement':wall_agreement,'disconnected_points':[list(p) for p in required if p not in seen], 'floor_coverage':len(covered)/max(1,len(free)), 'new_obstacle_cells':len(hits), 'required_points_connected':all(p in seen for p in required)}
        checks['passed']=checks['floor_coverage']>=.85 and checks['wall_agreement']>=.85 and checks['required_points_connected']
        report={'model':model,'textured':textured,'checks':checks,'bounds':[vertices.min(axis=0).tolist(),vertices.max(axis=0).tolist()]}
        (bundle.parent/'source-mesh-validation.json').write_text(json.dumps(report,indent=2))
        if not checks['passed']: raise ValueError('Source geometry validation failed; existing bundle unchanged. See source-mesh-validation.json')
        if not (bundle/'scene.before-source.json').exists(): shutil.copyfile(bundle/'scene.json',bundle/'scene.before-source.json')
        for name in assets: shutil.copyfile(out/name,bundle/name)
        scene['blocked']['source_unscanned']=[list(p) for p in sorted(free-covered)]
        scene['blocked']['source_obstacles']=[list(p) for p in sorted(hits)]
        scene['blocked']['source_unreachable']=[list(p) for p in sorted(safe-seen)]
        for room in scene['rooms']: room['cells']=[p for p in room['cells'] if tuple(p) in seen]
        source_name='Synthetic test OBJ' if scene['source']=='synthetic' else 'Matterport source OBJ'
        scene['mesh']={'file':'source.obj','format':'obj','materials':mtls,'textured':textured,'frame':'model','transform':matrix.tolist(), 'wall_height':scene['mesh']['wall_height'], 'source':source_name+(' with verified materials' if textured else '; UNTEXTURED geometry experiment'), 'validation':checks}
        scene['assets']=assets;scene['source']='sponsor-source' if scene['source']!='synthetic' else 'synthetic';scene['floor_image']=None
        scene['provenance'].append({'item':'rendered mesh','source':f'{source_name} {model}; SHA256 '+hashlib.sha256(path.read_bytes()).hexdigest(),'derivation':'source OBJ; explicit affine registration; conservative mesh obstacle projection'})
        scene['assumptions'].append('Navigation is a scan-derived grid checked against source mesh, not a certified traversability map.')
        (bundle/'scene.json.next').write_text(json.dumps(scene,indent=2))
        (bundle/'scene.json.next').replace(bundle/'scene.json')
        print(json.dumps(report))


def import_package(model, path, root, allow_untextured=False):
    if path.suffix.lower() != '.zip':
        return import_mesh(model,path,root,allow_untextured)
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 2_000_000_000:
                raise ValueError('Archive exceeds 2 GB unpacked limit')
            for entry in archive.infolist():
                if not (base/entry.filename).resolve().is_relative_to(base) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Unsafe archive entry')
            archive.extractall(base)
        objects=list(base.rglob('*.obj'))
        if len(objects)!=1: raise ValueError('Archive must contain one complete OBJ; select or assemble the correct package explicitly')
        return import_mesh(model,objects[0],root,allow_untextured)


def inspect(model, root, fetch=False):
    import prepare_scene as prep
    prep.QUERY='''query($id: ID!){model(id:$id){id assets{ meshes{id filename format resolution status url} textures{id format resolution quality status textures urlTemplate} resources(format:"matterpak"){status filename url}}}}'''
    data=prep.graph(model)['assets'];out=root/model/'source-assets';out.mkdir(parents=True,exist_ok=True)
    report={'model':model,'checked_at':datetime.now(timezone.utc).isoformat(),'meshes':[],'textures':[],'resources':[]}
    for group in report.keys() & data.keys():
        for index,a in enumerate(data[group]):
            entry={k:v for k,v in a.items() if k not in ('url','urlTemplate')};entry['has_download']=bool(a.get('url') or a.get('urlTemplate'))
            if fetch and group=='meshes' and a.get('url'):
                name=f'mesh-{index}.obj';dest=out/name
                entry['downloaded']=prep.download(a['url'],dest)
                if entry['downloaded']:
                    entry['file']=name;entry['sha256']=hashlib.sha256(dest.read_bytes()).hexdigest()
                    v,f,l,u=obj_data(dest);entry.update(vertices=len(v),triangles=len(f),uv_faces=sum(x[1] for x in f),material_libraries=l,bounds=[v.min(axis=0).tolist(),v.max(axis=0).tolist()])
                    dependencies=[]
                    for library in l:
                        if Path(library).name != library: raise ValueError('Unsafe remote material reference')
                        u=urlsplit(urljoin(a['url'],library));original=urlsplit(a['url'])
                        url=urlunsplit((u.scheme,u.netloc,u.path,original.query,''))
                        ok=prep.download(url,out/library)
                        dependencies.append({'file':library,'downloaded':ok})
                        if ok:
                            for line in (out/library).read_text().splitlines():
                                words=line.split()
                                if words and words[0]=='map_Kd' and len(words)==2:
                                    image=words[1]
                                    if Path(image).name != image: raise ValueError('Unsafe remote texture reference')
                                    vurl=urlsplit(urljoin(url,image))
                                    ok=prep.download(urlunsplit((vurl.scheme,vurl.netloc,vurl.path,original.query,'')),out/image)
                                    dependencies.append({'file':image,'downloaded':ok})
                    entry['dependencies']=dependencies
            report[group].append(entry)
    (out/'inspection.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    for command in ('inspect','import'):
        p=sub.add_parser(command);p.add_argument('model');p.add_argument('--root',type=Path,default=Path('data/prepared'))
        if command=='inspect':p.add_argument('--fetch',action='store_true')
        else:p.add_argument('obj',type=Path);p.add_argument('--allow-untextured',action='store_true')
    a=parser.parse_args()
    if a.command=='inspect':inspect(a.model,a.root,a.fetch)
    else:import_package(a.model,a.obj,a.root,a.allow_untextured)
