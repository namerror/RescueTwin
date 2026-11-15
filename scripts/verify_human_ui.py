"""Check appearance selection, export and run locking; leave a six-appearance demo idle."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from rescuetwin.scenes import load_bundle


def verify(url):
    scene=load_bundle(Path('data/prepared/TSpwHx7D8yC/bundle'));points=[]
    for room in scene.rooms:
        for cell in sorted(room['cells'],key=lambda p:math.dist(p,room['observe'][0])):
            cell=tuple(cell)
            if cell in scene.exits or cell==scene.robot_start or any(math.dist(scene.world(cell),scene.world(other))<1 for other in points):continue
            points.append(cell);break
        if len(points)==6:break
    assert len(points)==6
    out=Path('data/validation/humans');out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1400,'height':1000});page.goto(url)
        page.wait_for_function('window.rescuetwin?.scene()!=null',timeout=90000)
        page.select_option('#scene-select',scene.id)
        page.wait_for_function('id=>window.rescuetwin.scene()?.id===id&&!document.querySelector("#start").disabled',arg=scene.id,timeout=90000)
        assert page.request.post(url+'/api/scenario',data=dict(scene_id=scene.id,survivors=[list(c) for c in points],ignitions=[],seed=7)).ok
        page.wait_for_function('window.rescuetwin.state()?.scenario.survivors.length===6')
        page.select_option('#human-variant','mixed');assert page.locator('#apply').is_enabled();page.locator('#apply').click()
        page.wait_for_function('window.rescuetwin.state()?.scenario.human_variant==="mixed"&&!document.querySelector("#start").disabled')
        with page.expect_download() as download:
            page.locator('#export').click()
        export=out/'mixed-six-scenario.json';download.value.save_as(str(export))
        saved=json.loads(export.read_text());assert saved['human_variant']=='mixed' and len(saved['survivors'])==6
        page.locator('#start').click();page.wait_for_function('window.rescuetwin.state()?.status==="running"')
        assert page.locator('#human-variant').is_disabled()
        page.locator('#pause').click();page.wait_for_function('window.rescuetwin.state()?.status==="paused"')
        page.locator('#reset').click();page.wait_for_function('window.rescuetwin.state()?.status==="idle"&&!document.querySelector("#start").disabled')
        page.screenshot(path=str(out/'mixed-ui.png'),full_page=True)
        card=page.locator('#property-card').inner_text();assert 'Gas heating' in card and 'Fire sprinkler system' in card and 'Fire and smoke detector' in card
        browser.close()
    report=dict(scene=scene.id,survivors=6,appearance='mixed',export_roundtrip=True,locked_during_run=True,recorded_fixtures_visible=True)
    (out/'ui-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:5178');verify(parser.parse_args().url)
