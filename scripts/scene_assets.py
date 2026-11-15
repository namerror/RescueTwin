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
                if len(words)!=2 or words[0] not in ('map_Kd', 'map_Ka'): raise ValueError('Unsupported MTL map/options; normalize package first')
                dep = safe_file(src.parent, words[1]); name = copy(dep)
                # Ambient maps are unused by our diffuse, baked-lighting renderer.
                if words[0] == 'map_Ka': continue
                line = 'map_Kd '+name
                if current is not None: materials[current] = True
            rewritten.append(line)
        text = '\n'.join(rewritten)+'\n'
        digest = hashlib.sha256(text.encode()).hexdigest(); name = digest[:20]+'.mtl'
        (output/name).write_text(text); names[src] = name; assets[name] = {'sha256': digest}
    textured = bool(used) and all(uv and material and materials.get(material) for _,uv,material in faces)
    if not textured and not allow_untextured: raise ValueError('No complete UV/material mapping; use --allow-untextured only for a labeled geometry experiment')
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith('mtllib '):
            line = 'mtllib '+' '.join(names[safe_file(path.parent,n)] for n in line.split()[1:])
        lines.append(line)
    obj_text = '\n'.join(lines)+'\n'
    obj_digest = hashlib.sha256(obj_text.encode()).hexdigest()
    obj_name = obj_digest[:20]+'.obj'
    (output/obj_name).write_text(obj_text)
    assets[obj_name]={'sha256':obj_digest}
    return vertices, faces, assets, [names[safe_file(path.parent,n)] for n in libraries], textured


BAND = (.15, 1.10)       # m above floor: robot body/mast band; triangles here obstruct motion
PORTAL_SPAN = 4          # cells searched each way for schematic wall on both sides of a door gap
THIN_WIDTH = .15         # m: max in-cell obstruction thickness for a door leaf/frame (0.04 m leaf + 2 x 0.05 m buffer)


def reconcile(scene, vertices, faces):
    """Project mesh geometry onto the schematic grid and separate walls from furniture.

    Pure function: returns (checks, blocked_kinds, rooms, skipped, records); never mutates `scene`.
    Furniture cells are never cleared. A schematic door-gap cell may be cleared only when its mesh
    obstruction is thin (door leaf/frame/threshold); every clearing and relocation is recorded.
    """
    from shapely.geometry import Polygon, Point, MultiPoint, box
    from shapely.ops import unary_union
    floor_faces, obstacles = [], []
    for indices, _, _ in faces:
        tri = vertices[indices]; lo, hi = tri[:, 2].min(), tri[:, 2].max()
        poly = Polygon(tri[:, :2])
        if poly.is_valid and poly.area > 1e-8 and lo >= -.20 and hi <= BAND[0]: floor_faces.append(poly)
        if hi > BAND[0] and lo < BAND[1]:
            obstacles.append(MultiPoint(tri[:, :2]).convex_hull.buffer(.05))
    floors = unary_union(floor_faces).buffer(.10); obstacles = unary_union(obstacles)
    grid = scene['grid']; res = grid['resolution']; ox, oy = grid['origin']; W, H = grid['width'], grid['height']
    cell_box = lambda p: box(ox+p[0]*res, oy+p[1]*res, ox+(p[0]+1)*res, oy+(p[1]+1)*res)
    centre = lambda p: Point(ox+(p[0]+.5)*res, oy+(p[1]+.5)*res)
    walls = {tuple(c) for c in scene['blocked'].get('wall', [])}
    shell = walls | {tuple(c) for c in scene['blocked'].get('exterior', [])}   # wall-like for attribution
    blocked = {tuple(c) for group in scene['blocked'].values() for c in group}
    free = {(c, r) for r in range(H) for c in range(W) if (c, r) not in blocked}
    covered = {p for p in free if floors.covers(centre(p))}
    hits, thickness = {}, {}
    for p in free:
        if not obstacles.intersects(cell_box(p)): continue
        part = obstacles.intersection(cell_box(p)); hits[p] = part.area / res**2
        rect = part.minimum_rotated_rectangle.exterior.coords if part.area > 0 else []
        longest = max((Point(rect[i]).distance(Point(rect[i+1])) for i in range(len(rect)-1)), default=0)
        thickness[p] = part.area / longest if longest > 0 else 0.0
    near_wall = lambda p: any((p[0]+dx, p[1]+dy) in shell for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def door_gap(p):
        for (dx, dy) in ((1, 0), (0, 1)):
            sides = [any((p[0]+s*k*dx, p[1]+s*k*dy) in walls for k in range(1, PORTAL_SPAN+1)) for s in (1, -1)]
            across = [(p[0]+s*dy, p[1]+s*dx) for s in (1, -1)]   # perpendicular neighbours: the passage
            if all(sides) and all(q in free for q in across): return True
        return False
    # Wall-attributable: next to a schematic wall, or inside a schematic door gap (leaf/frame).
    source_walls = {p for p in hits if near_wall(p) or door_gap(p)}
    furniture = set(hits) - source_walls
    def in_exit_opening(p):
        for e in scene['exits']:
            if tuple(e['cell']) == p: return True
            centre_xy, width = e.get('opening_center'), e.get('opening_width_m')
            if centre_xy and width and centre(p).distance(Point(*centre_xy)) <= width / 2: return True
        return False
    portals, reasons = set(), {}
    for p in source_walls:
        if p not in covered or thickness[p] > THIN_WIDTH: continue
        if in_exit_opening(p): portals.add(p); reasons[p] = 'thin obstruction in verified exit opening (scan shows door leaf; treated as openable)'
        elif door_gap(p): portals.add(p); reasons[p] = 'thin obstruction in schematic door gap (door leaf/frame/threshold; treated as openable)'
    obstructed = (source_walls - portals) | furniture
    safe = covered - obstructed
    start = tuple(scene['robot_start']); seen = set(); todo = [start] if start in safe else []
    while todo:
        p = todo.pop()
        if p in seen: continue
        seen.add(p)
        todo.extend(q for q in ((p[0]+1, p[1]), (p[0]-1, p[1]), (p[0], p[1]+1), (p[0], p[1]-1)) if q in safe and q not in seen)
    anchors = [start] + [tuple(e['cell']) for e in scene['exits']]
    rooms, skipped, relocations = [], [], []
    for room in scene['rooms']:
        cells = [p for p in room['cells'] if tuple(p) in seen]
        if not cells:
            skipped.append({'id': room['id'], 'label': room.get('label'), 'reason': 'no reachable cell after furniture/mesh obstacle projection'})
            continue
        observe = []
        for p in room['observe']:
            p = tuple(p)
            if p in seen: observe.append(list(p)); continue
            q = min(cells, key=lambda c: (c[0]-p[0])**2 + (c[1]-p[1])**2)
            relocations.append({'room': room['id'], 'from': list(p), 'to': list(q),
                                'reason': 'furniture' if p in furniture else 'mesh obstacle or unscanned floor'})
            if list(q) not in observe: observe.append(list(q))
        rooms.append({**room, 'cells': cells, 'observe': observe})
    wall_agreement = (sum(obstacles.distance(centre(c)) <= res for c in walls) / len(walls)) if walls else 1.0
    checks = {'wall_agreement': wall_agreement, 'floor_coverage': len(covered)/max(1, len(free)),
              'new_obstacle_cells': len(obstructed), 'furniture_cells': len(furniture),
              'source_wall_cells': len(source_walls - portals), 'portals_cleared': len(portals),
              'relocations': len(relocations), 'rooms_skipped': len(skipped),
              'disconnected_points': [list(p) for p in anchors if p not in seen],
              'required_points_connected': all(p in seen for p in anchors), 'band_m': list(BAND)}
    checks['passed'] = (checks['floor_coverage'] >= .85 and checks['wall_agreement'] >= .85
                        and checks['required_points_connected'] and bool(rooms))
    kinds = {'source_unscanned': free - covered, 'source_walls': source_walls - portals,
             'furniture': furniture, 'source_unreachable': safe - seen}
    kinds = {k: [list(p) for p in sorted(v)] for k, v in kinds.items()}
    records = {'portals': [{'cell': list(p), 'obstruction_fraction': round(hits[p], 3), 'thickness_m': round(thickness[p], 3),
                            'reason': reasons[p]} for p in sorted(portals)],
               'relocations': relocations}
    return checks, kinds, rooms, skipped, records


def import_mesh(model, path, root, allow_untextured=False, annotation_path=None):
    bundle=root/model/'bundle'
    manifest=bundle/'scene.json'
    scene=json.loads(manifest.read_text())
    if (scene.get('mesh') or {}).get('format')=='obj' and (bundle/'scene.before-source.json').exists():
        scene=json.loads((bundle/'scene.before-source.json').read_text())
    config_path=Path(annotation_path) if annotation_path is not None else Path('scenes')/(model+'.json')
    config=json.loads(config_path.read_text()) if config_path.exists() else {}
    matrix=np.array(config.get('source_to_model',np.eye(4).tolist()),dtype=float)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1]): raise ValueError('Invalid source_to_model affine matrix')
    floor=float(config.get('floor_elevation_m',0))
    if not np.isfinite(floor) or abs(np.linalg.det(matrix[:3,:3]))<1e-9:raise ValueError('Invalid source registration')
    matrix[2,3]-=floor
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp); vertices,faces,assets,mtls,textured=package(path,out,allow_untextured)
        vertices=(np.c_[vertices,np.ones(len(vertices))]@matrix.T)[:,:3]
        checks,kinds,rooms,skipped,records=reconcile(scene,vertices,faces)
        report={'model':model,'textured':textured,'checks':checks,'reconciliation':records,'skipped_rooms':skipped,
                'bounds':[vertices.min(axis=0).tolist(),vertices.max(axis=0).tolist()]}
        (bundle.parent/'source-mesh-validation.json').write_text(json.dumps(report,indent=2))
        if not checks['passed']: raise ValueError('Source geometry validation failed; existing bundle unchanged. See source-mesh-validation.json')
        if not (bundle/'scene.before-source.json').exists(): shutil.copyfile(bundle/'scene.json',bundle/'scene.before-source.json')
        for name in assets:
            target = bundle/name
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != assets[name]['sha256']:
                    raise ValueError('Published asset hash mismatch; existing bundle unchanged')
                continue
            staging = bundle/(name+'.next')
            try:
                shutil.copyfile(out/name, staging)
                staging.replace(target)
            finally:
                staging.unlink(missing_ok=True)
        scene['blocked'].update(kinds)
        scene['rooms']=rooms
        scene['skipped_rooms']=(scene.get('skipped_rooms') or [])+skipped
        scene['reconciliation']=records
        source_name='Synthetic test OBJ' if scene['source']=='synthetic' else 'Matterport source OBJ'
        scene['mesh']={'file':next(n for n in assets if n.endswith('.obj')),'format':'obj','materials':mtls,'textured':textured,'frame':'model','transform':matrix.tolist(), 'wall_height':scene['mesh']['wall_height'], 'source':source_name+(' with verified materials' if textured else '; UNTEXTURED geometry experiment'), 'validation':checks}
        scene['assets']=assets;scene['source']='sponsor-source' if scene['source']!='synthetic' else 'synthetic';scene['floor_image']=None
        scene['provenance'].append({'item':'rendered mesh','source':f'{source_name} {model}; SHA256 '+hashlib.sha256(path.read_bytes()).hexdigest(),'derivation':f'source OBJ; explicit affine registration; mesh triangles in robot band {BAND[0]}-{BAND[1]} m projected to grid; cells within one cell of schematic walls or in schematic door gaps = source_walls, all others = furniture (never cleared); thin door-gap obstructions cleared as portals and recorded'})
        scene['assumptions'].append('Navigation is a scan-derived grid checked against source mesh, not a certified traversability map.')
        (bundle/'scene.json.next').write_text(json.dumps(scene,indent=2))
        (bundle/'scene.json.next').replace(bundle/'scene.json')
        print(json.dumps(report))


def import_package(model, path, root, allow_untextured=False, annotation_path=None):
    if path.suffix.lower() != '.zip':
        return import_mesh(model,path,root,allow_untextured,annotation_path)
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp).resolve()  # macOS temp dirs resolve via /private; compare like with like
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 2_000_000_000:
                raise ValueError('Archive exceeds 2 GB unpacked limit')
            for entry in archive.infolist():
                if not (base/entry.filename).resolve().is_relative_to(base) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Unsafe archive entry')
            archive.extractall(base)
        objects=list(base.rglob('*.obj'))
        if len(objects)!=1: raise ValueError('Archive must contain one complete OBJ; select or assemble the correct package explicitly')
        return import_mesh(model,objects[0],root,allow_untextured,annotation_path)


def inspect(model, root, fetch=False):
    import prepare_scene as prep
    query='''query($id: ID!){model(id:$id){id bundle(id:"mp:matterpak"){assets{url}} assets{ meshes{id filename format resolution status url} textures{id format resolution quality status textures urlTemplate} resources(format:"matterpak"){status filename url}}}}'''
    response=prep.graph(model, query=query, required=('assets',));data=response['assets'];out=root/model/'source-assets';out.mkdir(parents=True,exist_ok=True)
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
    report['matterpak'] = {'source':'model.bundle(mp:matterpak).assets',
                          'download_links':sum(bool(a.get('url')) for a in (response.get('bundle') or {}).get('assets', []))}
    (out/'inspection.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


class RangeFile:
    """Read-only seekable view of a remote file via HTTP Range, so zipfile reads only what it needs."""
    def __init__(self, url):
        import urllib.request
        self._req = urllib.request; self.url = url; self.pos = 0
        with self._req.urlopen(self._req.Request(url, headers={'Range': 'bytes=0-0'}), timeout=60) as r:
            if r.status != 206 or not r.headers.get('Content-Range'):
                raise ValueError('MatterPak server does not support range downloads')
            self.size = int(r.headers['Content-Range'].split('/')[-1])
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos
    def read(self, n=-1):
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        if end <= self.pos: return b''
        with self._req.urlopen(self._req.Request(self.url, headers={'Range': f'bytes={self.pos}-{end-1}'}), timeout=180) as r:
            if r.status != 206:
                raise ValueError('MatterPak range request was not honored')
            data = r.read(end-self.pos+1)
            if len(data) != end-self.pos:
                raise ValueError('MatterPak range response has incorrect length')
        self.pos += len(data); return data


def matterpak_members(names):
    """Pick the textured OBJ, its MTLs and textures; skip point clouds and everything else."""
    objs = [n for n in names if n.lower().endswith('.obj')]
    if len(objs) != 1: raise ValueError(f'MatterPak must contain exactly one OBJ, found {len(objs)}')
    keep = [n for n in names if n.lower().endswith(('.obj', '.mtl', '.jpg', '.jpeg', '.png'))
            and not Path(n).name.lower().startswith(('floorplan', 'colorplan'))]
    return objs[0], keep


def fetch_matterpak(model, root):
    """Download only the textured OBJ package from a MatterPak, if the API offers one. Never logs URLs."""
    import prepare_scene as prep
    query = 'query($id: ID!){model(id:$id){bundle(id:"mp:matterpak"){assets{url}}}}'
    data = prep.graph(model, query=query, required=('bundle',))
    links = [a['url'] for a in (data.get('bundle') or {}).get('assets', []) if a.get('url')]
    if len(links) != 1:
        raise ValueError(f'MatterPak for {model}: expected one bundle ZIP link, found {len(links)}')
    out = root/model/'source-assets'; out.mkdir(parents=True, exist_ok=True)
    # A content-addressed extraction cannot corrupt an earlier downloaded package.
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        dest = Path(tmp).resolve()
        try:
            with zipfile.ZipFile(RangeFile(links[0])) as archive:
                entries = archive.infolist()
                for entry in entries:
                    if not (dest/entry.filename).resolve().is_relative_to(dest) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Unsafe archive entry')
                obj, keep = matterpak_members([i.filename for i in entries])
                # Point clouds are skipped, so only the extracted members count toward the limit.
                kept = set(keep)
                if sum(i.file_size for i in entries if i.filename in kept) > 2_000_000_000:
                    raise ValueError('Extracted MatterPak members exceed 2 GB unpacked limit')
                for name in keep:
                    target = dest/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        except ValueError:
            raise
        except Exception:
            raise ValueError('MatterPak download failed; retry discovery/download (previous package unchanged)') from None
        extracted = [{'name': n, 'sha256': hashlib.sha256((dest/n).read_bytes()).hexdigest()} for n in keep]
        digest = hashlib.sha256(json.dumps(extracted, sort_keys=True).encode()).hexdigest()
        published = out/('matterpak-'+digest[:20])
        if not published.exists(): dest.rename(published)
        status = {'model': model, 'source': 'model.bundle(mp:matterpak).assets',
                  'checked_at': datetime.now(timezone.utc).isoformat(), 'obj': obj,
                  'listing': [{'name': i.filename, 'bytes': i.file_size} for i in entries], 'extracted': extracted}
        pending = out/'matterpak-status.json.next'
        pending.write_text(json.dumps(status, indent=2)); pending.replace(out/'matterpak-status.json')
    print(f'Extracted {len(keep)} files; import with: scripts/scene_assets.py import {model} {published/obj}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    for command in ('inspect','import','matterpak'):
        p=sub.add_parser(command);p.add_argument('model');p.add_argument('--root',type=Path,default=Path('data/prepared'))
        if command=='inspect':p.add_argument('--fetch',action='store_true')
        elif command=='import':p.add_argument('obj',type=Path);p.add_argument('--allow-untextured',action='store_true')
    a=parser.parse_args()
    if a.command=='inspect':inspect(a.model,a.root,a.fetch)
    elif a.command=='matterpak':fetch_matterpak(a.model,a.root)
    else:import_package(a.model,a.obj,a.root,a.allow_untextured)
