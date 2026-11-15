"""Synthetic textured OBJ renderer test. Not sponsor imagery or image-detection validation."""
import base64
import json
from pathlib import Path
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from scene_assets import package


def verify(url='http://127.0.0.1:5174'):
    root=Path('data/validation/material-fixture');root.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(16,16),(20,190,60)).save(root/'green.png')
    (root/'surface.mtl').write_text('newmtl green\nKd 1 1 1\nmap_Kd green.png\n')
    # Floor and east wall, winding faces the interior.
    obj=root/'fixture.obj';obj.write_text('mtllib surface.mtl\nv 0 0 0\nv 4 0 0\nv 4 4 0\nv 0 4 0\nv 4 0 3\nv 4 4 3\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nusemtl green\nf 1/1 2/2 3/3 4/4\nf 3/1 2/2 5/3 6/4\n')
    out=root/'assets';out.mkdir(exist_ok=True);_,_,assets,mtls,_=package(obj,out)
    cells=[[c,r] for r in range(8) for c in range(8)]
    scene={'id':'material-fixture','name':'SYNTHETIC material fixture','width':8,'height':8,'resolution':.5,'origin':[0,0], 'walls':[],'exterior':[],'exits':[[2,2]],'source':'synthetic','robot_start':[2,2],'time_limit':600,
           'rooms':[{'id':'r','name':'Test','label':'Bedroom','cells':cells,'observe':[[2,2]]}],
           'mesh':{'file':next(n for n in assets if n.endswith('.obj')),'format':'obj','materials':mtls,'textured':True,'frame':'model','wall_height':3,'source':'SYNTHETIC test OBJ'},'assets':assets,
           'obstacle_overlay':{'furniture':[[5,5],[5,6],[6,5],[6,6]],'source_walls':[[7,r] for r in range(8)]}}
    zeros=[[0]*8 for _ in range(8)];seq=[0];frames=[];errors=[]
    state={'scene_id':scene['id'],'scenario':{'scene_id':scene['id'],'survivors':[],'ignitions':[],'seed':7},'time':0,'status':'idle','robot':[2,2],'heading':0,'pose':{'x':1.25,'y':1.25,'z':1,'yaw':0},'survivors':[], 'heat':zeros,'smoke':zeros,'observed':zeros,'route':[],'events':[],'metrics':{},'mode':'adaptive','strategy':'greedy','perception_mode':'image-yolo','detections':[],'frame_request':None,'degraded':False,'scanning':False,'last_frame':None,'paired':None,'hazard_time':0,'hazards_running':False}
    def respond(route):
        path=route.request.url.split('/api/',1)[1]
        if '/files/' in path:
            file=out/path.rsplit('/',1)[1]
            if file.name not in assets:return route.fulfill(status=404)
            return route.fulfill(path=str(file))
        if path=='scene':body=scene
        elif path=='scenes':body=[{'id':scene['id'],'name':scene['name'],'source':'synthetic','checks_passed':True}]
        elif path=='building-uses':body={'inferred':'residential','source':'synthetic fixture','profiles':[{'id':'residential','label':'Residential','rationale':'Synthetic test'}]}
        elif path=='risk-map':body={'rooms':[],'tags':[],'building_default':'unknown','building_source':'synthetic fixture','note':''}
        elif path.startswith('presets'):body=[]
        elif path=='state':
            seq[0]+=1;state['frame_request']={'seq':seq[0],'time':0,'pose':state['pose']};body=state
        elif path=='renderer':body={'owner':True}
        elif path=='runs':body=[]
        elif path=='frames':
            payload=route.request.post_data_json
            (root/'robot-rgb.jpg').write_bytes(base64.b64decode(payload['rgb'].split(',',1)[1]))
            depth=np.frombuffer(base64.b64decode(payload['depth']),dtype='<f4');frames.append({'valid_depth_pixels':int((depth>0).sum()),'max_depth':float(depth.max())})
            body={'seq':payload['seq'],'time':0,'accepted':[],'rejected':[]}
        else:body={}
        route.fulfill(json=body)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1400,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/api/**',respond)
        page.goto(url);page.wait_for_function("!document.querySelector('#start').disabled",timeout=30000)
        for _ in range(30):
            if frames:break
            page.wait_for_timeout(500)
        before=frames[-1]['valid_depth_pixels']
        page.locator('.view-options').evaluate('(el) => el.open = true')
        page.locator('#walls-layer').uncheck()
        count=len(frames)
        for _ in range(20):
            page.wait_for_timeout(250)
            if len(frames)>count:break
        assert frames[-1]['valid_depth_pixels']==before,'Operator wall toggle changed robot depth'
        page.locator('#walls-layer').check()
        assert page.locator('#furniture-control').is_visible() and page.locator('#cutaway-control').is_visible()
        page.locator('#furniture-layer').check(); page.locator('#cutaway-height').select_option('off')
        count=len(frames)
        for _ in range(20):
            page.wait_for_timeout(250)
            if len(frames)>count:break
        assert frames[-1]['valid_depth_pixels']==before,'Operator overlay/cutaway changed robot depth'
        page.locator('.view-options').evaluate('(el) => el.open = true');page.locator('#plan').click();page.screenshot(path=str(root/'topdown.png'),full_page=True)
        assert frames,'No rendered RGB-D frame'
        rgb=np.array(Image.open(root/'robot-rgb.jpg'));colored=((rgb[:,:,1]>rgb[:,:,0]*1.2)&(rgb[:,:,1]>rgb[:,:,2]*1.2)).mean()
        result={'synthetic_fixture':True,'green_pixel_fraction':float(colored),'frames':frames[-1:],'page_errors':errors,'overlay_and_cutaway_operator_only':True}
        page.route('**/files/*.mtl',lambda route:route.fulfill(status=404))
        page.reload();page.wait_for_timeout(2000)
        result['missing_material_blocks_start']=page.locator('#start').is_disabled()
        assert result['missing_material_blocks_start']
        (root/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));browser.close()
        assert colored>.2 and not errors and frames[-1]['valid_depth_pixels']>1000


if __name__=='__main__':verify()
