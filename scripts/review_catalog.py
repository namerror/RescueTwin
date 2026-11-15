"""Create ignored plan/entry contact sheets and measured perimeter opening candidates."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from shapely.ops import unary_union

from prepare_scene import Schematic, find_door, room_polygons


def review(directory):
    source = directory / 'source'
    data = json.loads((source / 'source.json').read_text())
    plans = data['assets']['floorplans']
    schematic = Schematic(source / next(p['file'] for p in plans if p.get('file', '').endswith('_schematic.dxf')),
                          source / next(p['file'] for p in plans if p.get('file', '').endswith('_schematic.png')))
    floor = data['floors'][0]
    rooms = room_polygons(data, floor)
    interior = unary_union([r['polygon'] for r in rooms if r['polygon'] is not None and r['label'] not in (None, 'Void')])
    polygons = [interior] if interior.geom_type == 'Polygon' else list(interior.geoms)
    pano = next((p for p in data['panoLocations'] if p['label'] == '0'), None)
    candidates = []
    image = Image.open(source / next(p['file'] for p in plans if p.get('file', '').endswith('_schematic.png'))).convert('RGB')
    draw = ImageDraw.Draw(image)
    for poly in polygons:
        coords = list(poly.exterior.coords)
        for a, b in zip(coords, coords[1:]):
            width, mid = find_door(schematic, (a, b))
            if width < .6:
                continue
            index = len(candidates)
            distance = ((mid[0]-pano['position']['x'])**2 + (mid[1]-pano['position']['y'])**2)**.5 if pano else None
            candidates.append(dict(index=index, width=round(width, 3), midpoint=mid, segment=[a, b], distance_to_pano0=distance))
            x, y = schematic.px(*mid)
            draw.ellipse((x-10,y-10,x+10,y+10),fill='#ff7f00')
            draw.text((x+12,y-10),f'{index} ({mid[0]:.2f},{mid[1]:.2f})',fill='red',stroke_width=1)
    for p in data['panoLocations']:
        x, y = schematic.px(p['position']['x'],p['position']['y'])
        draw.ellipse((x-3,y-3,x+3,y+3),fill='#00a0ff')
        draw.text((x+4,y+2),p['label'],fill='#0040ff')
    image.save(directory / 'opening-plan.png')
    out = Image.new('RGB',(1500,1450),'#182633'); labels=ImageDraw.Draw(out)
    out.paste(ImageOps.contain(image,(1480,680)),(10,40))
    labels.text((10,10),f'{directory.name}: {data["name"]}',fill='white')
    for i, f in enumerate(sorted(source.glob('pano_0_face*.jpg'))):
        pos=((i%3)*500,(i//3)*350+740)
        out.paste(ImageOps.contain(Image.open(f).convert('RGB'),(490,320)),(pos[0],pos[1]+25))
        labels.text(pos,f.name,fill='white')
    out.save(directory / 'entry-review.jpg')
    (directory/'opening-candidates.json').write_text(json.dumps(candidates,indent=2))
    print(directory.name, 'floors', len(data['floors']), 'pano0',pano['position'] if pano else None,
          'candidates',[(c['index'],round(c['distance_to_pano0'] or 0,2),[round(x,2) for x in c['midpoint']],c['width']) for c in candidates],flush=True)


if __name__ == '__main__':
    for p in sorted(Path('data/prepared').iterdir()):
        if (p/'source/source.json').is_file() and (p/'source-assets/inspection.json').is_file():
            review(p)
