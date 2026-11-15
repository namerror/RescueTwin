"""Run a source-textured RGB-D/YOLO rescue on every prepared simulation scene.

Uses a synthetic nearby avatar and no fire; this checks mission continuity, not
emergency safety or real-camera perception. Run against an isolated idle server.
"""
import argparse
import base64
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from playwright.sync_api import sync_playwright
from rescuetwin.core import line_visible
from rescuetwin.scenes import available


def target(scene):
    start = tuple(scene.robot_start); sx, sy = scene.world(start)
    candidates = []
    for room in scene.rooms:
        for cell in room['cells']:
            cell = tuple(cell)
            if cell == start or cell in scene.exits: continue
            x, y = scene.world(cell); distance = math.hypot(x-sx, y-sy)
            if 1.5 <= distance <= 3.5 and line_visible(scene, start, cell):
                clearance = sum(scene.free((cell[0]+dx, cell[1]+dy)) for dx in (-1,0,1) for dy in (-1,0,1))
                candidates.append((clearance < 9, abs(math.atan2(y-sy,x-sx)), distance, cell))
    if not candidates: raise ValueError('No nearby line-of-sight placement')
    return list(min(candidates)[-1])


def verify(url, timeout, selected):
    out = Path('data/validation/catalog-simulations'); out.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in available().values() if s.source != 'synthetic' and (not selected or s.id.split('-')[0] in selected)]
    reports, errors, failed_assets = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width':1100,'height':900})
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.on('response',lambda response: failed_assets.append(response.url.split('?')[0]) if response.status >= 400 and '/files/' in response.url else None)
        frames = []
        page.on('request', lambda req: frames.append(req.post_data_json) if req.url.endswith('/api/frames') else None)
        page.request.post(url+'/api/control',data={'action':'reset'})
        page.goto(url)
        for scene in scenes:
            report={'model':scene.meta['frame']['model'], 'scene':scene.id}
            print('Checking',scene.id,flush=True)
            try:
                assert page.request.post(url+'/api/control',data={'action':'reset'}).ok
                assert page.request.post(url+'/api/scene',data={'id':scene.id}).ok
                page.wait_for_function('id => window.rescuetwin?.scene()?.id === id && !document.querySelector("#start").disabled',arg=scene.id,timeout=90000)
                current=page.request.get(url+'/api/scene').json()
                assert current['mesh']['textured'] and current['mesh']['validation']['passed']
                scenario=dict(scene_id=scene.id,survivors=[target(scene)],ignitions=[],seed=7)
                response=page.request.post(url+'/api/scenario',data=scenario); assert response.ok,response.text()
                (out/f'{report["model"]}-scenario.json').write_text(json.dumps(scenario,indent=2))
                frames.clear()
                response=page.request.post(url+'/api/control',data={'action':'start','perception':'image-yolo'}); assert response.ok,response.text()
                deadline=time.monotonic()+timeout
                state=None
                while time.monotonic()<deadline:
                    state=page.request.get(url+'/api/state').json()
                    if state['status'] in ('completed','unresolved'): break
                    page.wait_for_timeout(500)
                state=page.request.get(url+'/api/state').json()
                report.update(status=state['status'],time=state['time'],metrics=state['metrics'],degraded=state['degraded'],camera_frames=len(frames),textured=True)
                assert frames, 'No RGB-D camera frames'
                frame=frames[-1]
                depth=np.frombuffer(base64.b64decode(frame['depth']),dtype='<f4')
                assert np.isfinite(depth).all() and (depth>0).sum()>depth.size*.1
                report['valid_depth_pixels']=int((depth>0).sum())
                (out/f'{report["model"]}-camera.jpg').write_bytes(base64.b64decode(frame['rgb'].split(',',1)[1]))
                page.screenshot(path=str(out/f'{report["model"]}-mission.png'),full_page=True)
                assert state['status']=='completed' and state['metrics']['evacuated']==1 and not state['degraded'],report
                report['passed']=True
            except Exception as error:
                report.update(passed=False,error=str(error)[:1200])
                page.screenshot(path=str(out/f'{report["model"]}-failure.png'),full_page=True)
            reports.append(report)
            (out/'report.json').write_text(json.dumps(dict(scenes=reports,page_errors=errors,failed_assets=failed_assets),indent=2))
            print(json.dumps(report),flush=True)
        page.request.post(url+'/api/control',data={'action':'reset'})
        browser.close()
    assert not errors and not failed_assets and all(r['passed'] for r in reports), 'See catalog-simulations/report.json'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:5176');parser.add_argument('--timeout',type=int,default=180);parser.add_argument('--models',nargs='*')
    args=parser.parse_args();verify(args.url,args.timeout,args.models)
