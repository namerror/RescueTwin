"""Actual sponsor-scene texture, switching and RGB-D check; artifacts stay in data/."""
import base64
import json
from pathlib import Path
import numpy as np
from playwright.sync_api import sync_playwright


def check_depth(scene, frame, depth):
    """Compare three lower-image rays to the registered OBJ, in camera-Z metres."""
    from scene_assets import obj_data
    model = scene['frame']['model']
    vertices, faces, _, _ = obj_data(Path('data/prepared')/model/'bundle'/scene['mesh']['file'])
    matrix = np.array(scene['mesh']['transform'])
    vertices = (np.c_[vertices, np.ones(len(vertices))] @ matrix.T)[:, :3]
    tri = vertices[np.array([f[0] for f in faces])]
    a, e1, e2 = tri[:,0], tri[:,1]-tri[:,0], tri[:,2]-tri[:,0]
    pose = frame['pose']; origin = np.array([pose['x'],pose['y'],pose['z']])
    yaw = pose['yaw']; forward = np.array([np.cos(yaw),np.sin(yaw),0])
    right = np.array([np.sin(yaw),-np.cos(yaw),0])
    fx, fy, cx, cy = frame['intrinsics']; records = []
    for frac in (.2,.5,.8):
        row, col = int(depth.shape[0]*.8), int(depth.shape[1]*frac)
        ray = forward + right*((col+.5-cx)/fx) + np.array([0,0,-(row+.5-cy)/fy])
        h = np.cross(ray,e2); det = np.einsum('ij,ij->i',e1,h)
        valid = abs(det)>1e-9
        inv = np.divide(1,det,out=np.zeros_like(det),where=valid)
        delta = origin-a; u=np.einsum('ij,ij->i',delta,h)*inv
        q=np.cross(delta,e1); v=q@ray*inv
        t=np.einsum('ij,ij->i',e2,q)*inv
        valid &= (u>=0)&(v>=0)&(u+v<=1)&(t>.1)
        expected = float(t[valid].min()) if valid.any() else None
        actual = float(depth[row,col])
        records.append({'pixel':[col,row], 'mesh_depth':expected,'render_depth':actual})
    assert sum(r['mesh_depth'] is not None and abs(r['mesh_depth']-r['render_depth'])<.08 for r in records)>=2, records
    return records


def verify(url, models):
    out = Path('data/validation/textured-scenes'); out.mkdir(parents=True, exist_ok=True)
    reports, errors, frames, failed, acknowledgements = [], [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width':1100,'height':900})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('response', lambda r: failed.append(r.url.split('?')[0]) if r.status >= 400 and '/files/' in r.url else None)
        def capture(req):
            if req.url.endswith('/api/frames'): frames.append(req.post_data_json)
        page.on('request', capture)
        def acknowledge(response):
            if response.url.endswith('/api/frames'):
                assert response.ok, 'RGB-D frame rejected by backend'
                acknowledgements.append(response.json())
        page.on('response', acknowledge)
        page.request.post(url+'/api/control', data={'action':'reset'})
        page.goto(url)
        for i, model in enumerate([*models, models[0]]):
            sid = model+'-floor1'
            with page.expect_response(lambda r: r.url.endswith('/api/scene') and r.request.method == 'POST') as switched:
                page.select_option('#scene-select', sid)
            assert switched.value.ok
            print('Switched', model, flush=True)
            page.wait_for_function("id => window.rescuetwin.scene()?.id === id && !document.querySelector('#start').disabled", arg=sid, timeout=120000)
            scene = page.request.get(url+'/api/scene').json()
            assert scene['mesh']['textured'] and scene['frame']['model'] == model
            count = len(frames)
            ack_count = len(acknowledgements)
            page.locator('input[name=perception][value=image-yolo]').check()
            with page.expect_response(lambda r: r.url.endswith('/api/control') and r.request.method == 'POST') as started:
                page.locator('#start').click()
            assert started.value.ok, started.value.text()
            print('Started', started.value.json()['status'], flush=True)
            page.wait_for_function('n => window.rescuetwin.lastFrameSeq() > n', arg=-1, timeout=60000)
            for _ in range(120):
                if len(frames) > count: break
                page.wait_for_timeout(500)
            assert len(frames) > count, 'No new RGB-D frame after scene switch'
            page.locator('#pause').click()
            frame = frames[-1]
            for _ in range(120):
                if len(acknowledgements) > ack_count: break
                page.wait_for_timeout(500)
            assert len(acknowledgements) > ack_count and acknowledgements[-1]['seq'] == frame['seq']
            depth = np.frombuffer(base64.b64decode(frame['depth']), dtype='<f4').reshape(frame['height'], frame['width'])
            assert np.isfinite(depth).all() and (depth > 0).sum() > depth.size*.1
            prefix = out/f'{i}-{model}'
            prefix.with_suffix('.jpg').write_bytes(base64.b64decode(frame['rgb'].split(',',1)[1]))
            np.save(str(prefix)+'-depth.npy', depth)
            page.locator('#viewport').screenshot(path=str(prefix)+'-operator.png', timeout=120000)
            for height in ['1.0', '2.2', 'off', '1.6']:
                page.select_option('#cutaway-height', height)
                page.wait_for_timeout(150)
                if height in ('1.0','off'):
                    page.locator('#viewport').screenshot(path=str(prefix)+f'-cutaway-{height}.png',timeout=120000)
            reports.append({'model': model, 'mesh': scene['mesh']['file'], 'material_libraries': len(scene['mesh']['materials']),
                            'seq':frame['seq'], 'acknowledged':True, 'pose':frame['pose'], 'valid_depth':int((depth>0).sum()),
                            'depth_range':[float(depth[depth>0].min()),float(depth.max())], 'ray_checks':check_depth(scene, frame, depth)})
            page.locator('#reset').click()
            page.wait_for_function("document.querySelector('#run-status').textContent.trim() === 'IDLE'", timeout=30000)
        browser.close()
    report = {'scenes':reports,'page_errors':errors,'failed_assets':failed}
    (out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report))
    assert not errors and not failed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5174')
    parser.add_argument('--models', nargs='+', required=True)
    args = parser.parse_args()
    verify(args.url, args.models)
