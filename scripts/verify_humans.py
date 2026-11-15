"""Real YOLO checks for the actual browser human loader; component fixtures plus full missions."""
import argparse
import base64
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from playwright.sync_api import sync_playwright
from rescuetwin.perception import PersonDetector
from rescuetwin.scenes import load_bundle
from scripts.verify_catalog_simulations import target


def verify(url, model, selected=None, missions_only=False):
    out=Path('data/validation/humans');out.mkdir(parents=True,exist_ok=True)
    variants=json.loads(Path('frontend/public/models/humans.json').read_text())['variants']
    if selected: variants=[v for v in variants if v['id'] in selected]
    detector=PersonDetector();views=[];missions=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1100,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);page.wait_for_function('window.rescuetwin?.scene()!=null',timeout=90000)
        await_setup="""async () => {
          const THREE=await import('/node_modules/.vite/deps/three.js');
          const humans=await import('/src/humans.ts');
          await humans.loadHumans(()=>{},message=>{throw new Error(message)});
          document.querySelector('#app').style.display='none';
          const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setSize(512,384);document.body.append(renderer.domElement);
          const scene=new THREE.Scene();scene.background=new THREE.Color('#e9e7e3');scene.add(new THREE.HemisphereLight('#ffffff','#9199a4',2.3));
          const light=new THREE.DirectionalLight('#ffffff',2);light.position.set(2,5,3);scene.add(light);
          const floor=new THREE.Mesh(new THREE.PlaneGeometry(20,20),new THREE.MeshStandardMaterial({color:'#c7b8a2',roughness:1}));floor.rotation.x=-Math.PI/2;scene.add(floor);
          const camera=new THREE.PerspectiveCamera(2*Math.atan(384/512)*180/Math.PI,512/384,.05,50);camera.position.set(0,1,2.5);camera.lookAt(0,1,0);
          window.humanQA={render:(id,angle)=>{
            const actor=humans.makeHuman('person-0',id);if(!actor)throw new Error('Missing template '+id);
            actor.rotation.y=angle;scene.add(actor);actor.updateMatrixWorld(true);renderer.render(scene,camera);
            const box=new THREE.Box3().setFromObject(actor,true);const rgb=renderer.domElement.toDataURL('image/png');scene.remove(actor);
            return {rgb,height:box.max.y-box.min.y,minimum:box.min.y};
          }};
        }"""
        if not missions_only:page.evaluate(await_setup)
        for variant in ([] if missions_only else variants):
            for index,angle in enumerate((0,1.5707963267948966,3.141592653589793,4.71238898038469)):
                capture=page.evaluate('([id,angle])=>window.humanQA.render(id,angle)',[variant['id'],angle])
                data=base64.b64decode(capture['rgb'].split(',',1)[1]);file=out/f'{variant["id"]}-{index}.png';file.write_bytes(data)
                detected=detector.detect(Image.open(io.BytesIO(data)).convert('RGB'))
                row=dict(variant=variant['id'],orientation=index,confidence=max((d.confidence for d in detected),default=0),height_m=capture['height'],ground_y=capture['minimum'])
                row['passed']=row['confidence']>=.35 and abs(row['height_m']-variant['height_m'])<.02 and abs(row['ground_y'])<.01
                views.append(row);print('Rendered YOLO',variant['id'],index,round(row['confidence'],3),flush=True)
        if not missions_only:(out/'component-report.json').write_text(json.dumps(dict(views=views,weights=detector.weights,device=detector.device,fixture='plain synthetic render; not sponsor imagery'),indent=2))
        page.close();page=browser.new_page(viewport={'width':1100,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
        page.request.post(url+'/api/control',data={'action':'reset'})
        page.goto(url)
        scene=load_bundle(Path('data/prepared')/model/'bundle')
        assert page.request.post(url+'/api/scene',data={'id':scene.id}).ok
        page.wait_for_function('id=>window.rescuetwin?.scene()?.id===id&&!document.querySelector("#start").disabled',arg=scene.id,timeout=90000)
        # Compile/warm the detector separately from the acceptance runs. These are
        # lockstep image checks, not cold-start or real-time performance claims.
        warm=dict(scene_id=scene.id,survivors=[target(scene)],ignitions=[],seed=7)
        assert page.request.post(url+'/api/scenario',data=warm).ok
        assert page.request.post(url+'/api/control',data={'action':'start','perception':'image-yolo'}).ok
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            warm_state=page.request.get(url+'/api/state').json()
            if warm_state['last_frame'] is not None:break
            page.wait_for_timeout(500)
        assert warm_state['last_frame'] is not None,'Detector warm-up produced no processed camera frame'
        for variant in variants:
            # Exercise the UI reset too: it resets this camera client's sequence
            # counter before the backend starts a new simulation at sequence 1.
            page.locator('#reset').click()
            page.wait_for_function('window.rescuetwin.state()?.status==="idle"&&!document.querySelector("#reset").disabled')
            scenario=dict(scene_id=scene.id,survivors=[target(scene)],ignitions=[],seed=7,human_variant=variant['id'])
            response=page.request.post(url+'/api/scenario',data=scenario);assert response.ok,response.text()
            page.wait_for_function('id=>window.rescuetwin.state()?.scenario.human_variant===id',arg=variant['id'])
            assert page.locator('#human-variant').input_value()==variant['id']
            response=page.request.post(url+'/api/control',data={'action':'start','perception':'image-yolo'});assert response.ok,response.text()
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                state=page.request.get(url+'/api/state').json()
                if state['status'] in ('completed','unresolved'):break
                page.wait_for_timeout(500)
            state=page.request.get(url+'/api/state').json();page.screenshot(path=str(out/f'{variant["id"]}-mission.png'),full_page=True)
            row=dict(variant=variant['id'],status=state['status'],time=state['time'],metrics=state['metrics'],degraded=state['degraded'])
            row['passed']=state['status']=='completed' and state['metrics']['evacuated']==1 and not state['degraded'];missions.append(row)
            (out/'mission-report.json').write_text(json.dumps(dict(scene=scene.id,missions=missions,page_errors=errors,detector_warmed=True),indent=2));print('Mission',json.dumps(row),flush=True)
        page.request.post(url+'/api/control',data={'action':'reset'});browser.close()
    assert all(r['passed'] for r in views) and all(r['passed'] for r in missions) and not errors,'See data/validation/humans reports'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:5178');parser.add_argument('--model',required=True);parser.add_argument('--variants',nargs='*');parser.add_argument('--missions-only',action='store_true');args=parser.parse_args();verify(args.url,args.model,args.variants,args.missions_only)
