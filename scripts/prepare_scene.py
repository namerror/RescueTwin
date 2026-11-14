"""Prepare a sponsor scene bundle. `fetch` saves ignored source data; it does NOT certify exits.

Credentials are passed to curl on stdin and never printed. Signed asset URLs are used
in memory only and are not written to disk.
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_matterport import credentials  # noqa: E402

API = 'https://api.matterport.com/api/models/graph'
QUERY = '''query($id: ID!) { model(id:$id) { id name
 floors { id label vertices { id position { x y } } edges { id type thickness vertices { id } } }
 rooms { id floor { id } classifications { label confidence } boundary { edges { id } }
         dimensions(units: metric) { areaFloor width depth height } }
 panoLocations { id label position { x y z } skybox(resolution: "2k") { status children } }
 bundle(id:"mp:matterpak") { availability assets { url } }
 assets {
          floorplans(provider:"matterport") { format flags url width height resolution origin { x y } } }
} }'''


def graph(model):
    key, token = credentials()
    result = subprocess.run(['curl', '-sS', '-K', '-', '-m', '90', '-H', 'Content-Type: application/json',
                             '-d', json.dumps({'query': QUERY, 'variables': {'id': model}}), API],
                            input=f'user = "{key}:{token}"\n', capture_output=True, text=True)
    try:
        body = json.loads(result.stdout)
    except ValueError:
        raise SystemExit('Matterport returned a non-JSON response; check network and credentials')
    if body.get('errors') or not (body.get('data') or {}).get('model'):
        raise SystemExit('Matterport rejected query; inspect schema/permissions without logging credentials')
    return body['data']['model']


def download(url, target):
    result = subprocess.run(['curl', '-sS', '-f', '-L', '-m', '180', '-o', str(target), url], capture_output=True, text=True)
    return result.returncode == 0


def strip_urls(value):
    """Drop signed links before anything is written to disk."""
    if isinstance(value, dict):
        return {k: strip_urls(v) for k, v in value.items() if k not in ('url', 'children')}
    if isinstance(value, list):
        return [strip_urls(v) for v in value]
    return value


def fetch(model, root, panos):
    data = graph(model)
    out = root / model / 'source'
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, plan in enumerate(data['assets']['floorplans']):
        name = f"floorplan_{index}_{'-'.join(plan['flags'])}.{plan['format']}"
        if plan.get('url') and download(plan['url'], out / name):
            plan['file'] = name
            saved.append(name)
    wanted = set(panos or [])
    for pano in data['panoLocations']:
        if pano['label'] not in wanted and pano['id'] not in wanted:
            continue
        for face, url in enumerate((pano.get('skybox') or {}).get('children') or []):
            name = f"pano_{pano['label']}_face{face}.jpg"
            if download(url, out / name):
                saved.append(name)
    (out / 'source.json').write_text(json.dumps(strip_urls(data), indent=2))
    print(json.dumps(dict(model=model, saved=saved, matterpak=data['bundle']['availability'],
                          matterpak_download_available=bool(data['bundle'].get('assets')), mesh_downloaded=False, note='Exits remain UNVALIDATED until inspected.'), indent=2))


# ---------------------------------------------------------------------------
# Bundle build. Everything is in the Matterport model frame (metres, Z up).
# ---------------------------------------------------------------------------

STROKE_OPEN = 5          # px: removes dotted door swings and label text, keeps wall strokes
DOOR_MIN_WIDTH = 0.6     # m: narrowest opening accepted as a verified exit
WALL_THICKNESS = 0.10    # m: rendered thickness of derived walls


def room_polygons(data, floor):
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    V = {v['id']: (v['position']['x'], v['position']['y']) for v in floor['vertices']}
    E = {e['id']: [v['id'] for v in e['vertices']] for e in floor['edges']}
    rooms = []
    for r in data['rooms']:
        if (r.get('floor') or {}).get('id') != floor['id']:
            continue
        label = (r.get('classifications') or [{}])[0].get('label') if r.get('classifications') else None
        edges = [E[e['id']] for e in (r.get('boundary') or {}).get('edges', []) if e['id'] in E]
        polys = list(polygonize(unary_union([LineString([V[a], V[b]]) for a, b in edges]))) if edges else []
        dims = r.get('dimensions') or {}
        rooms.append(dict(id=r['id'], label=label, polygon=max(polys, key=lambda p: p.area) if polys else None,
                          area=dims.get('areaFloor'), height=dims.get('height')))
    return rooms


def open_mask(mask, size):
    from PIL import Image, ImageFilter
    image = Image.fromarray((mask * 255).astype('uint8'))
    image = image.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))
    return __import__('numpy').array(image) > 127


class Schematic:
    """Schematic floor plan PNG registered to the model frame via the DXF line extents."""

    def __init__(self, dxf_path, png_path):
        import ezdxf
        import numpy as np
        from PIL import Image
        self.lines = [((l.dxf.start.x, l.dxf.start.y), (l.dxf.end.x, l.dxf.end.y))
                      for l in ezdxf.readfile(dxf_path).modelspace() if l.dxftype() == 'LINE']
        dark = np.array(Image.open(png_path).convert('L')) < 100
        self.walls = open_mask(dark, STROKE_OPEN)
        ys, xs = np.nonzero(self.walls)
        X = [p[0] for line in self.lines for p in line]
        Y = [p[1] for line in self.lines for p in line]
        self.X0, self.X1, self.Y0, self.Y1 = min(X), max(X), min(Y), max(Y)
        best = None
        for half in (2, 3, 4, 5, 6):   # stroke half-width: choose the best DXF-to-pixel fit
            sx = (xs.max() - xs.min() - 2 * half) / (self.X1 - self.X0)
            sy = (ys.max() - ys.min() - 2 * half) / (self.Y1 - self.Y0)
            self.c0, self.r0, self.sx, self.sy = xs.min() + half, ys.min() + half, sx, sy
            score = self.dxf_agreement()
            if best is None or score > best[0]:
                best = (score, half, sx, sy)
        self.agreement, half, self.sx, self.sy = best
        self.c0, self.r0 = xs.min() + half, ys.min() + half
        self.integral = np.pad(self.walls.astype('int32').cumsum(0).cumsum(1), ((1, 0), (1, 0)))

    def px(self, x, y):
        return self.c0 + (x - self.X0) * self.sx, self.r0 + (self.Y1 - y) * self.sy

    def wall_at(self, x, y, radius_m=0.02):
        c, r = self.px(x, y)
        k = max(1, round(radius_m * self.sx))
        r0, r1 = max(0, int(r) - k), min(self.walls.shape[0], int(r) + k + 1)
        c0, c1 = max(0, int(c) - k), min(self.walls.shape[1], int(c) + k + 1)
        return r1 > r0 and c1 > c0 and bool(self.walls[r0:r1, c0:c1].any())

    def wall_in_box(self, x0, y0, x1, y1):
        c0, r1 = self.px(x0, y0)
        c1, r0 = self.px(x1, y1)
        H, W = self.walls.shape
        r0, r1 = max(0, int(r0)), min(H, int(r1) + 1)
        c0, c1 = max(0, int(c0)), min(W, int(c1) + 1)
        if r1 <= r0 or c1 <= c0:
            return False
        I = self.integral
        return I[r1, c1] - I[r0, c1] - I[r1, c0] + I[r0, c0] > 0

    def dxf_agreement(self):
        """Fraction of DXF line samples landing on a schematic wall stroke (door gaps excluded by design)."""
        hits = total = 0
        for (ax, ay), (bx, by) in self.lines:
            n = max(2, int(math.hypot(bx - ax, by - ay) / 0.05))
            for i in range(n + 1):
                t = i / n
                total += 1
                hits += self.wall_at(ax + t * (bx - ax), ay + t * (by - ay), 0.03)
        return hits / total


def wall_runs(schematic, min_length=0.05):
    """Split each DXF line where the schematic shows an opening; return wall segments in metres."""
    runs = []
    for (ax, ay), (bx, by) in schematic.lines:
        length = math.hypot(bx - ax, by - ay)
        n = max(2, int(length / 0.02))
        start = None
        for i in range(n + 2):
            t = min(i / n, 1.0)
            solid = i <= n and schematic.wall_at(ax + t * (bx - ax), ay + t * (by - ay))
            if solid and start is None:
                start = t
            elif not solid and start is not None:
                end = min((i - 1) / n, 1.0)
                if (end - start) * length >= min_length:
                    runs.append(((ax + start * (bx - ax), ay + start * (by - ay)), (ax + end * (bx - ax), ay + end * (by - ay))))
                start = None
    return runs


def write_gltf(path, segments, height, thickness):
    """Wall boxes in the viewer frame (glTF Y-up): model (x, y, z) -> (x, z, -y)."""
    import base64
    import struct
    positions, normals, indices = [], [], []
    for (ax, ay), (bx, by) in segments:
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        nx, ny = -uy * thickness / 2, ux * thickness / 2
        ax, ay, bx, by = ax - ux * thickness / 2, ay - uy * thickness / 2, bx + ux * thickness / 2, by + uy * thickness / 2
        corners = [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]
        faces = [((0, 1), (ny, -nx)), ((1, 2), (ux, uy)), ((2, 3), (-ny, nx)), ((3, 0), (-ux, -uy))]
        for (i, j), (fx, fy) in faces:
            norm = math.hypot(fx, fy) or 1
            normal = (fx / norm, 0.0, -fy / norm)
            base = len(positions)
            for (x, y), z in [(corners[i], 0), (corners[j], 0), (corners[j], height), (corners[i], height)]:
                positions.append((x, z, -y))
                normals.append(normal)
            indices += [base, base + 1, base + 2, base, base + 2, base + 3]
        base = len(positions)
        for x, y in corners:
            positions.append((x, height, -y))
            normals.append((0.0, 1.0, 0.0))
        indices += [base, base + 2, base + 1, base, base + 3, base + 2]
    pos = b''.join(struct.pack('<3f', *p) for p in positions)
    nor = b''.join(struct.pack('<3f', *n) for n in normals)
    ind = b''.join(struct.pack('<I', i) for i in indices)
    blob = pos + nor + ind
    xs, ys, zs = zip(*positions)
    gltf = {
        'asset': {'version': '2.0', 'generator': 'rescuetwin prepare_scene'},
        'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0, 'name': 'derived-walls'}],
        'materials': [{'name': 'wall', 'pbrMetallicRoughness': {'baseColorFactor': [0.86, 0.87, 0.88, 1], 'metallicFactor': 0, 'roughnessFactor': 0.9}}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1}, 'indices': 2, 'material': 0}]}],
        'buffers': [{'byteLength': len(blob), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(blob).decode()}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos), 'target': 34962},
                        {'buffer': 0, 'byteOffset': len(pos), 'byteLength': len(nor), 'target': 34962},
                        {'buffer': 0, 'byteOffset': len(pos) + len(nor), 'byteLength': len(ind), 'target': 34963}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3', 'min': [min(xs), min(ys), min(zs)], 'max': [max(xs), max(ys), max(zs)]},
                      {'bufferView': 1, 'componentType': 5126, 'count': len(normals), 'type': 'VEC3'},
                      {'bufferView': 2, 'componentType': 5125, 'count': len(indices), 'type': 'SCALAR'}],
    }
    Path(path).write_text(json.dumps(gltf))


def find_door(schematic, segment):
    """Longest wall-free run along an annotated exit segment (m), and its midpoint."""
    (ax, ay), (bx, by) = segment
    length = math.hypot(bx - ax, by - ay)
    n = max(2, int(length / 0.02))
    nx, ny = -(by - ay) / length, (bx - ax) / length
    best, start = (0.0, None), None
    for i in range(n + 2):
        t = i / n
        x, y = ax + t * (bx - ax), ay + t * (by - ay)
        free = i <= n and not any(schematic.wall_at(x + nx * o, y + ny * o) for o in (-0.08, -0.04, 0, 0.04, 0.08))
        if free and start is None:
            start = t
        elif not free and start is not None:
            width = ((i - 1) / n - start) * length
            if width > best[0]:
                mid = (start + (i - 1) / n) / 2
                best = (width, (ax + mid * (bx - ax), ay + mid * (by - ay)))
            start = None
    return best


def _build(model, root, annotation_path, kit):
    import shutil
    from collections import deque
    import numpy as np
    from PIL import Image, ImageDraw
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    from inspect_sponsor import context as costar_context

    ann = json.loads(Path(annotation_path).read_text())
    src = root / model / 'source'
    out = root / model / 'bundle'
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads((src / 'source.json').read_text())
    if len(data['floors']) != 1:
        raise ValueError('Multi-floor scans require explicitly scoped floor assets; MVP importer accepts one floor')
    floor = next(f for f in data['floors'] if f['label'] == ann['floor_label'])
    plans = {p.get('file', ''): p for p in data['assets']['floorplans']}
    schematic = Schematic(src / next(f for f in plans if f.endswith('_schematic.dxf')), src / next(f for f in plans if f.endswith('_schematic.png')))

    rooms = room_polygons(data, floor)
    inside_rooms = [r for r in rooms if r['polygon'] is not None and r['label'] not in (None, 'Void')]
    voids = [r for r in rooms if r['polygon'] is not None and r['label'] == 'Void']
    skipped = [dict(id=r['id'], label=r['label'], reason='no boundary polygon') for r in rooms if r['polygon'] is None]
    interior = unary_union([r['polygon'] for r in inside_rooms])
    void_area = unary_union([r['polygon'] for r in voids]) if voids else None
    interior_p, void_p = prep(interior), prep(void_area) if void_area is not None else None

    res = ann['resolution']
    minx, miny, maxx, maxy = interior.bounds
    ox, oy = math.floor((minx - 0.5) / res) * res, math.floor((miny - 0.5) / res) * res
    W, H = math.ceil((maxx + 0.5 - ox) / res), math.ceil((maxy + 0.5 - oy) / res)
    center = lambda c, r: (ox + (c + 0.5) * res, oy + (r + 0.5) * res)
    to_cell = lambda x, y: (int((x - ox) // res), int((y - oy) // res))

    kind = {}   # (c, r) -> 'free' | 'wall' | 'exterior' | 'void'
    for r in range(H):
        for c in range(W):
            x, y = center(c, r)
            if not interior_p.contains(Point(x, y)):
                kind[(c, r)] = 'exterior'
            elif void_p is not None and void_p.contains(Point(x, y)):
                kind[(c, r)] = 'void'
            elif schematic.wall_in_box(ox + c * res, oy + r * res, ox + (c + 1) * res, oy + (r + 1) * res):
                kind[(c, r)] = 'wall'
            else:
                kind[(c, r)] = 'free'

    exits, checks = [], {}
    for spec in ann['exits']:
        width, mid = find_door(schematic, spec['segment'])
        if width < DOOR_MIN_WIDTH:
            raise SystemExit(f"Exit {spec['id']}: schematic opening {width:.2f} m < {DOOR_MIN_WIDTH} m; refusing to invent an exit")
        free = [p for p, k in kind.items() if k == 'free']
        cell = min(free, key=lambda p: math.dist(center(*p), mid))
        boundary_distance = interior.exterior.distance(Point(*center(*cell)))
        exits.append(dict(id=spec['id'], label=spec['label'], cell=list(cell), position=[round(v, 3) for v in center(*cell)],
                          opening_width_m=round(width, 2), opening_center=[round(v, 3) for v in mid],
                          boundary_distance_m=round(boundary_distance, 3), evidence=spec['evidence'],
                          verified=spec['verified'], beyond=spec['beyond']))

    if ann.get('robot_start') and kind.get(tuple(ann['robot_start'])) != 'free':
        raise ValueError('Configured robot_start is blocked or outside the scanned floor')

    # Keep cells reachable from the first exit; everything else is recorded, not silently traversable.
    start = tuple(exits[0]['cell'])
    seen, queue = {start}, deque([start])
    while queue:
        c, r = queue.popleft()
        for q in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if kind.get(q) == 'free' and q not in seen:
                seen.add(q)
                queue.append(q)
    if ann.get('robot_start') and tuple(ann['robot_start']) not in seen:
        raise ValueError('Configured robot_start cannot reach the exit')
    unreachable = [p for p, k in kind.items() if k == 'free' and p not in seen]
    for p in unreachable:
        kind[p] = 'unreachable'

    counts = {}
    for name in ('Kitchen', 'Bedroom', 'Bathroom', 'Living Room', 'Hallway'):
        counts[name] = sum(1 for r in inside_rooms if r['label'] == name)
    numbering, room_out = {}, []
    panos = [(p['label'], p['position']['x'], p['position']['y']) for p in data['panoLocations']]
    for r in sorted(inside_rooms, key=lambda r: (r['polygon'].centroid.x, -r['polygon'].centroid.y)):
        poly, label = r['polygon'], r['label']
        numbering[label] = numbering.get(label, 0) + 1
        name = 'Kitchen / living / hall' if label == 'Kitchen' else (f"{label} {numbering[label]}" if counts.get(label, 0) > 1 else label)
        prepared = prep(poly)
        cells = [p for p, k in kind.items() if k == 'free' and p in seen and prepared.contains(Point(*center(*p)))]
        if not cells:
            skipped.append(dict(id=r['id'], label=label, reason='no reachable free cells'))
            continue
        inside = [p for p in panos if prepared.contains(Point(p[1], p[2]))]
        cx, cy = poly.centroid.x, poly.centroid.y
        if inside:
            pano = min(inside, key=lambda p: math.dist((p[1], p[2]), (cx, cy)))
            anchor, anchor_source = (pano[1], pano[2]), f"pano {pano[0]} (scan capture position)"
        else:
            rp = poly.representative_point()
            anchor, anchor_source = (rp.x, rp.y), 'polygon representative point'
        observe = min(cells, key=lambda p: math.dist(center(*p), anchor))
        room_out.append(dict(id=r['id'], name=name, label=label, cells=[list(p) for p in sorted(cells)], observe=[list(observe)],
                             observe_source=anchor_source, polygon=[[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords],
                             area_m2=round(poly.area, 2), matterport_area_m2=r['area'], panos=[p[0] for p in inside]))

    segments = wall_runs(schematic)
    heights = [r['height'] for r in inside_rooms if r['height']]
    wall_height = round(sorted(heights)[len(heights) // 2], 2) if heights else 2.4
    write_gltf(out / 'mesh.gltf', segments, wall_height, WALL_THICKNESS)

    # Alignment checks (written to the bundle and re-checked by tests).
    blocked = lambda p: kind.get(p, 'exterior') != 'free'
    samples = hits = 0
    for (ax, ay), (bx, by) in segments:
        n = max(1, int(math.hypot(bx - ax, by - ay) / 0.05))
        for i in range(n + 1):
            samples += 1
            hits += blocked(to_cell(ax + i / n * (bx - ax), ay + i / n * (by - ay)))
    checks['schematic_vs_dxf_line_agreement'] = round(schematic.agreement, 3)
    checks['mesh_walls_on_blocked_cells'] = round(hits / samples, 3)
    free_cells = [p for p, k in kind.items() if k == 'free']
    checks['room_cells_inside_polygon'] = 1.0
    checks['traversable_cells_in_a_room'] = round(sum(1 for p in free_cells if any(list(p) in r['cells'] for r in room_out)) / len(free_cells), 3)
    checks['exit_boundary_distance_m'] = exits[0]['boundary_distance_m']
    checks['exit_opening_width_m'] = exits[0]['opening_width_m']
    checks['unreachable_free_cells'] = len(unreachable)
    checks['rooms_reachable_from_exit'] = all(tuple(r['observe'][0]) in seen for r in room_out)

    photo_name = next((f for f in plans if f.endswith('_photogramy-alpha.png') and plans[f].get('resolution') == 100.0), None)
    floor_image = None
    if photo_name:
        meta = plans[photo_name]
        image = Image.open(src / photo_name).convert('RGBA')
        alpha = np.array(image)[:, :, 3]
        res_px = meta['resolution']
        top = meta['origin']['y'] + meta['height'] / res_px

        def covered(x, y):
            col, row = int((x - meta['origin']['x']) * res_px), int((top - y) * res_px)
            return 0 <= row < alpha.shape[0] and 0 <= col < alpha.shape[1] and alpha[row, col] > 0
        checks['traversable_cells_on_scan_floor_image'] = round(sum(covered(*center(*p)) for p in free_cells) / len(free_cells), 3)
        shutil.copy(src / photo_name, out / 'floor.png')
        floor_image = dict(file='floor.png', origin=[meta['origin']['x'], meta['origin']['y']], pixels_per_metre=res_px,
                           width=meta['width'], height=meta['height'], rows='row 0 is max y',
                           source=f"Matterport photogrammetric floor plan ({'-'.join(meta['flags'])})")

    failures = [k for k, v, ok in [
        ('schematic_vs_dxf_line_agreement', checks['schematic_vs_dxf_line_agreement'], checks['schematic_vs_dxf_line_agreement'] >= 0.8),
        ('mesh_walls_on_blocked_cells', checks['mesh_walls_on_blocked_cells'], checks['mesh_walls_on_blocked_cells'] >= 0.95),
        ('exit_boundary_distance_m', checks['exit_boundary_distance_m'], checks['exit_boundary_distance_m'] <= 2 * res),
        ('rooms_reachable_from_exit', checks['rooms_reachable_from_exit'], checks['rooms_reachable_from_exit']),
        ('traversable_cells_on_scan_floor_image', checks.get('traversable_cells_on_scan_floor_image', 1), checks.get('traversable_cells_on_scan_floor_image', 1) >= 0.85),
    ] if not ok]
    checks['passed'] = not failures
    checks['failed'] = failures

    costar = costar_context(kit, model, ann['property_id'], ann['floor_plan_id']) if kit else None
    blocked_cells = {k: sorted([list(p) for p, v in kind.items() if v == k]) for k in ('wall', 'exterior', 'void', 'unreachable')}
    if failures:
        raise SystemExit(f'Alignment checks failed: {failures}; scene manifest not replaced')
    scene = {
        'schema': 'rescuetwin.scene/1',
        'id': f"{model}-{floor['label'].lower().replace(' ', '')}",
        'name': f"{costar['fields'][0]['value'].split(',')[0] if costar else data['name']} · {data['name']} · {floor['label']}",
        'source': 'sponsor-derived',
        'frame': dict(name='matterport-model', units='m', up='+z', model=model, floor_id=floor['id'],
                      viewer_transform='three.js (X, Y, Z) = model (x, z, -y)'),
        'grid': dict(origin=[round(ox, 4), round(oy, 4)], resolution=res, width=W, height=H,
                     cell_center='origin + (col + 0.5, row + 0.5) * resolution', rows='+y', connectivity=4),
        'blocked': blocked_cells,
        'exits': exits,
        'not_exits': ann.get('not_exits', []),
        'rooms': room_out,
        'skipped_rooms': skipped,
        'robot_start': ann.get('robot_start', exits[0]['cell']),
        'time_limit': 600,
        'mesh': dict(file='mesh.gltf', source='derived: walls extruded from the schematic floor plan; source mesh/material validation not passed',
                     wall_height=wall_height, wall_thickness=WALL_THICKNESS, frame='viewer'),
        'floor_image': floor_image,
        'costar': costar,
        'checks': checks,
        'provenance': [
            dict(item='walls/doorways', source=f'Matterport schematic floor plan (DXF + PNG) for {model}', derivation=f'PNG strokes opened by {STROKE_OPEN} px, registered to DXF extents'),
            dict(item='rooms', source=f'Matterport model graph rooms/boundaries, floor {floor["id"]}', derivation='edge loops polygonized'),
            dict(item='observation points', source='Matterport pano capture positions', derivation='nearest pano to room centroid'),
            dict(item='exit', source=str(annotation_path), derivation='manual pano/plan inspection; opening width measured on schematic'),
            dict(item='hazards, survivors, robot', source='synthetic', derivation='simulation'),
        ],
        'assumptions': ['Static scanned geometry and perfect localization.', 'Single verified egress endpoint; corridor beyond it assumed available.',
                        'Matterport Void areas treated as blocked (not scanned).', 'Doors are open; door leaves are not modelled.'],
    }
    (out / 'scene.json').write_text(json.dumps(scene))

    preview = Image.open(src / photo_name).convert('RGB') if photo_name else Image.new('RGB', (W * 20, H * 20), 'white')
    draw = ImageDraw.Draw(preview, 'RGBA')
    meta = plans.get(photo_name) or {}

    def ipx(x, y):
        if not photo_name:
            return (x - ox) * 20, (oy + H * res - y) * 20
        return (x - meta['origin']['x']) * meta['resolution'], (meta['origin']['y'] + meta['height'] / meta['resolution'] - y) * meta['resolution']
    colors = {'wall': (200, 0, 0, 110), 'void': (120, 0, 160, 110), 'unreachable': (255, 160, 0, 140)}
    for p, k in kind.items():
        if k in colors:
            x, y = ox + p[0] * res, oy + p[1] * res
            draw.rectangle([ipx(x, y + res), ipx(x + res, y)], fill=colors[k])
    for (ax, ay), (bx, by) in segments:
        draw.line([ipx(ax, ay), ipx(bx, by)], fill=(0, 90, 255, 255), width=2)
    for r in room_out:
        draw.line([ipx(x, y) for x, y in r['polygon']], fill=(0, 160, 0, 255), width=2)
        x, y = center(*r['observe'][0])
        draw.ellipse([ipx(x - 0.12, y + 0.12), ipx(x + 0.12, y - 0.12)], fill=(0, 160, 0, 255))
        draw.text(ipx(x + 0.15, y), r['name'], fill=(0, 0, 0, 255))
    for e in exits:
        x, y = e['position']
        draw.ellipse([ipx(x - 0.2, y + 0.2), ipx(x + 0.2, y - 0.2)], fill=(0, 200, 160, 255))
    preview.save(out / 'preview.png')
    print(json.dumps(dict(scene=scene['id'], grid=scene['grid'], rooms=[r['name'] for r in room_out], exits=exits,
                          skipped_rooms=skipped, checks=checks), indent=2))
    if failures:
        raise SystemExit(f'Alignment checks failed: {failures}')


def build(model, root, annotation_path, kit):
    """Prepare in isolation; publish the manifest only after all checks pass."""
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        staging=Path(tmp)
        (staging/model).mkdir()
        (staging/model/'source').symlink_to((root/model/'source').resolve(),target_is_directory=True)
        _build(model,staging,annotation_path,kit)
        target=root/model/'bundle';target.mkdir(parents=True,exist_ok=True)
        built=staging/model/'bundle'
        for file in built.iterdir():
            if file.name!='scene.json':shutil.copyfile(file,target/file.name)
        shutil.copyfile(built/'scene.json',target/'scene.json.next')
        (target/'scene.json.next').replace(target/'scene.json')
        if (target/'scene.before-source.json').exists():shutil.copyfile(target/'scene.json',target/'scene.before-source.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('model')
    f.add_argument('--root', type=Path, default=Path('data/prepared'))
    f.add_argument('--panos', nargs='*', help='pano labels or ids whose skybox faces to save')
    b = sub.add_parser('build')
    b.add_argument('model')
    b.add_argument('--root', type=Path, default=Path('data/prepared'))
    b.add_argument('--annotations', type=Path, help='default: scenes/<model>.json')
    b.add_argument('--kit', type=Path, default=Path('data/costar-hackathon-kit'))
    args = parser.parse_args()
    if args.command == 'fetch':
        fetch(args.model, args.root, args.panos)
    else:
        build(args.model, args.root, args.annotations or Path('scenes') / f'{args.model}.json', args.kit)
