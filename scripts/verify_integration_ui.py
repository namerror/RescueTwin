"""Browser smoke check against an isolated running RescueTwin backend/frontend.

Usage: python scripts/verify_integration_ui.py --url http://127.0.0.1:5174
Requires the e2e extra. Saves screenshots and a machine-readable summary in ignored data/.
"""
import argparse
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from rescuetwin.core import line_visible


def verify(url, output, timeout=180, model="9A46aT7FCPg", mission_only=False):
    output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1100,'height':900});page.set_default_timeout(120000);errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        captured=[0]
        def capture(request):
            if request.url.endswith('/api/frames'):
                captured[0]+=1
                if captured[0] in (1,3,10):
                    import base64
                    payload=request.post_data_json
                    (output/f'early-frame-{captured[0]}.jpg').write_bytes(base64.b64decode(payload['rgb'].split(',',1)[1]))
        page.on('request',capture)
        page.request.post(url+'/api/control',data={'action':'reset'})
        page.goto(url);page.wait_for_function("!document.querySelector('#start').disabled",timeout=45000)
        options=page.locator('#scene-select option').evaluate_all('(els) => els.map(e => e.value)')
        results=[]
        for sid in ([] if mission_only else ['9A46aT7FCPg-floor1','5p6KKvHqh3W-floor1']):
            page.select_option('#scene-select',sid)
            page.wait_for_function("id => document.querySelector('#scene-select').value === id && !document.querySelector('#start').disabled",arg=sid,timeout=45000)
            page.locator('#plan').click();page.screenshot(path=str(output/(sid+'-plan.png')),full_page=True)
            count=page.locator('#presets button').count()
            assert count>=4,(sid,count)
            results.append({'scene':sid,'loaded':True,'presets':count})
        with page.expect_response(lambda r:r.url.endswith('/api/scene') and r.request.method=='POST') as switched:
            page.select_option('#scene-select',model+'-floor1')
        assert switched.value.ok
        page.wait_for_function("!document.querySelector('#start').disabled")
        # Full image-mode mission with an illustrative nearby survivor and no ignition.
        scene=page.request.get(url+'/api/scene').json()
        start=tuple(scene['robot_start']);world=scene['origin'];res=scene['resolution']
        from rescuetwin.scenes import load_bundle
        model=scene['frame']['model'];loaded=load_bundle(Path('data/prepared')/model/'bundle')
        sx,sy=loaded.world(start);candidates=[]
        for room in scene['rooms']:
            for c in room['cells']:
                if tuple(c)==start or tuple(c) in map(tuple,scene['exits']):continue
                x,y=loaded.world(c);dx,dy=x-sx,y-sy;distance=(dx*dx+dy*dy)**.5;angle=abs(__import__('math').atan2(dy,dx))
                if 1.5<=distance<=3.5 and angle<=__import__('math').radians(25) and line_visible(loaded,start,tuple(c)):
                    candidates.append((angle,distance,c))
        if not candidates:
            report={'scene':scene['id'],'mission_status':'not_started','error':'No clear, forward-facing image-test survivor placement', 'page_errors':errors}
            (output/'browser-report.json').write_text(json.dumps(report,indent=2))
            raise AssertionError(report['error'])
        _,_,target=min(candidates,key=lambda item:(item[0],item[1]))
        results.append({'test_placement':target,'visibility':'line of sight, 1.5-3.5 m, within 25 degrees of initial camera'})
        scenario={'scene_id':scene['id'],'survivors':[target],'ignitions':[],'seed':7}
        (output/'clear-view-scenario.json').write_text(json.dumps(scenario,indent=2))
        page.locator('#scenario-file').set_input_files(str(output/'clear-view-scenario.json'))
        page.wait_for_function("document.querySelector('#apply').textContent.includes('•')")
        page.locator('#apply').click()
        page.wait_for_function("document.querySelector('#apply').textContent.includes('Scenario applied')")
        assert page.request.get(url+'/api/state').json()['scenario']==scenario
        page.locator('input[name=perception][value=image-yolo]').check()
        page.locator('#start').click()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=page.request.get(url+'/api/state').json()
            if state['status'] in ('completed','unresolved'):break
            page.wait_for_timeout(1000)
        state=page.request.get(url+'/api/state').json();page.screenshot(path=str(output/'mission.png'),full_page=True)
        results.append({'mission_status':state['status'],'time':state['time'],'metrics':state['metrics'],'degraded':state['degraded'],'perception':state['perception_mode']})
        if mission_only:
            report={'scenes':results,'page_errors':errors}
            (output/'browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report));browser.close()
            assert not errors, errors
            assert state['status']=='completed' and state['metrics']['evacuated']==1 and not state['degraded'], results[-1]
            return
        page.request.post(url+'/api/control',data={'action':'reset'})
        page.wait_for_function("document.querySelector('#run-status').textContent.trim() === 'IDLE'",timeout=15000)
        # Exercise the visible route-change timeline with an explicitly labeled
        # analytic development run. This does not establish YOLO hazard performance.
        page.locator('#presets button.preset').filter(has_text='Standard:').click()
        page.locator('#apply').click()
        page.wait_for_function("document.querySelector('#apply').textContent.includes('Scenario applied')")
        page.locator('input[name=perception][value=analytic-development]').check()
        page.locator('#start').click()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            route_state=page.request.get(url+'/api/state').json()
            if route_state['metrics']['route_changes']>0 or route_state['status'] in ('completed','unresolved'):break
            page.wait_for_timeout(500)
        assert route_state['metrics']['route_changes']>0,'No route change observed in standard rehearsal'
        page.wait_for_function("document.querySelector('#events').textContent.includes('Route updated from observed evidence.')")
        if route_state['status']=='running':page.locator('#pause').click()
        page.screenshot(path=str(output/'route-change.png'),full_page=True)
        results.append({'route_rehearsal':{'perception':route_state['perception_mode'],'time':route_state['time'],'metrics':route_state['metrics'],'events':route_state['events'],
            'limitation':'Observed-evidence route change; this metric alone does not isolate hazard causation or demonstrate a routing advantage.'}})
        page.request.post(url+'/api/control',data={'action':'reset'})
        page.wait_for_function("document.querySelector('#run-status').textContent.trim() === 'IDLE'",timeout=15000)
        preset=page.locator('#presets button.preset').filter(has_text='Capacity 25%')
        assert preset.count()==1,'Capacity 25% preset is not visible'
        preset.click();page.locator('#apply').click()
        page.wait_for_function("document.querySelector('#apply').textContent.includes('Scenario applied')",timeout=15000)
        page.locator('input[name=perception][value=analytic-development]').check()
        with page.expect_response(lambda response:response.url.endswith('/api/compare')) as comparison:
            page.locator('#compare').click()
        paired=comparison.value.json()
        (output/'capacity-comparison.json').write_text(json.dumps(paired,indent=2))
        page.wait_for_function("document.querySelector('#comparison-body').textContent.includes('capacity provenance')",timeout=45000)
        results.append({'costar_capacity_preset':'25%','comparison_provenance_visible':True,'scenario_hash_visible':'scenario sha256' in page.locator('#comparison-body').inner_text().lower()})
        assert results[-1]['scenario_hash_visible']
        page.screenshot(path=str(output/'capacity-comparison.png'),full_page=True)
        report={'scenes':results,'page_errors':errors};(output/'browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report));browser.close()
        assert not errors,errors
        assert state['status']=='completed' and state['metrics']['evacuated']==1 and not state['degraded']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:5174');p.add_argument('--timeout',type=int,default=180);p.add_argument('--model',default='9A46aT7FCPg');p.add_argument('--mission-only',action='store_true');p.add_argument('--output',type=Path,default=Path('data/validation/browser'));a=p.parse_args();verify(a.url,a.output,a.timeout,a.model,a.mission_only)
