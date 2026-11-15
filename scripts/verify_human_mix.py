"""Render the application's mixed appearance mode without driving the simulation."""
import argparse
import base64
import io
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from playwright.sync_api import sync_playwright
from rescuetwin.perception import PersonDetector


def verify(url):
    out=Path('data/validation/humans');out.mkdir(parents=True,exist_ok=True)
    html='''<html><body style="margin:0"><script type="module">
      import * as THREE from '/node_modules/.vite/deps/three.js';
      import {loadHumans,makeHuman,HUMAN_VARIANTS} from '/src/humans.ts';
      await loadHumans(()=>{},message=>{throw Error(message)});
      const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setSize(1024,512);document.body.append(renderer.domElement);
      const scene=new THREE.Scene();scene.background=new THREE.Color('#e9e7e3');scene.add(new THREE.HemisphereLight('#ffffff','#9199a4',2.3));
      const light=new THREE.DirectionalLight('#ffffff',2);light.position.set(2,5,3);scene.add(light);
      const floor=new THREE.Mesh(new THREE.PlaneGeometry(20,20),new THREE.MeshStandardMaterial({color:'#c7b8a2',roughness:1}));floor.rotation.x=-Math.PI/2;scene.add(floor);
      const variants=[];
      for(let i=0;i<6;i++){const actor=makeHuman('person-'+i,'mixed');actor.position.x=i-2.5;scene.add(actor);variants.push(actor.userData.humanVariant);}
      const camera=new THREE.PerspectiveCamera(2*Math.atan(.5)*180/Math.PI,2,.05,50);camera.position.set(0,1,4);camera.lookAt(0,1,0);renderer.render(scene,camera);
      window.mixQA={variants,expected:HUMAN_VARIANTS,rgb:renderer.domElement.toDataURL('image/png')};
    </script></body></html>'''
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page();page.route('**/human-mix-qa',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        page.goto(url+'/human-mix-qa');page.wait_for_function('window.mixQA!=null',timeout=90000)
        result=page.evaluate('window.mixQA');browser.close()
    data=base64.b64decode(result['rgb'].split(',',1)[1]);(out/'mixed-six.png').write_bytes(data)
    detections=PersonDetector().detect(Image.open(io.BytesIO(data)).convert('RGB'))
    report=dict(variants=result['variants'],one_of_each=result['variants']==result['expected'],person_detections=len(detections),
                confidences=[d.confidence for d in detections],fixture='plain synthetic group render; actual application mixed-mesh assignment')
    (out/'mixed-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    assert report['one_of_each'] and report['person_detections']==6


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:5178');verify(parser.parse_args().url)
