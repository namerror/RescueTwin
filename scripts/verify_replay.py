"""Read-only browser smoke test against an existing recorded run."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:5197');a=p.parse_args()
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,args=['--enable-gpu']);page=browser.new_page(viewport={'width':1400,'height':1000})
 errors=[];mutations=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(a.url);page.wait_for_selector('#replay-open')
 rows=page.request.get(a.url+'/api/runs').json();run=next(r for r in rows if r['complete'])
 page.select_option('#runs-list',run['id']);page.click('#replay-open');page.wait_for_function("document.querySelector('#replay-label').textContent.includes('READ ONLY')",timeout=90000)
 page.wait_for_timeout(1500)
 page.on('request',lambda r:mutations.append(r.url) if r.method=='POST' and r.frame==page.main_frame else None)
 page.click('#replay-step');page.wait_for_timeout(500)
 assert page.locator('#start').is_disabled()
 assert not mutations,mutations
 assert not errors,errors
 Path('data/validation/replay').mkdir(parents=True,exist_ok=True)
 page.screenshot(path='data/validation/replay/browser.png',full_page=True)
 print(dict(run=run['id'],errors=errors,mutations=mutations,label=page.locator('#replay-label').inner_text()))
 browser.close()
