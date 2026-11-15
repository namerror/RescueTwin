"""Merged UI acceptance check; run only against isolated backend/frontend servers."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def verify(url, output):
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--enable-gpu'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page.set_default_timeout(15000)
        errors, mutations = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_function("window.rescuetwin?.state()", timeout=90000)
        page.request.post(url + '/api/control', data={'action': 'reset'})
        page.wait_for_function("!document.querySelector('#start').disabled", timeout=90000)
        for speed in [2, 4, 8, 1]:
            page.click(f'[data-speed="{speed}"]')
            page.wait_for_function("s => window.rescuetwin.state().run_speed === s", arg=speed)
        page.locator('.view-options').evaluate('(el) => el.open = true')
        for view in ['follow', 'pov', 'plan', 'orbit']:
            page.click('#' + view)
            assert page.locator('#' + view).get_attribute('aria-pressed') == 'true'
            page.wait_for_timeout(300)
        page.locator('input[name=strategy][value=mission-astar]').check()
        page.locator('input[name=perception][value=analytic-development]').check()
        page.click('#start')
        page.wait_for_function("window.rescuetwin.state().status === 'running' && window.rescuetwin.state().strategy === 'mission-astar'")
        page.wait_for_function('window.rescuetwin.state().time > 0')
        page.click('#pause')
        page.wait_for_function("window.rescuetwin.state().status === 'paused'")
        paused = page.request.get(url + '/api/state').json()['time']
        page.wait_for_timeout(400)
        assert page.request.get(url + '/api/state').json()['time'] == paused
        page.click('#reset')
        page.wait_for_function("window.rescuetwin.state().status === 'idle'")
        with page.expect_response(lambda r: r.url.endswith('/api/runs')):
            page.click('#runs-refresh')
        page.wait_for_timeout(500)
        page.wait_for_function("document.querySelector('#runs-list').options.length > 1")
        rows = page.request.get(url + '/api/runs').json()
        run = next((r for r in rows if r['complete']), rows[0])
        page.select_option('#runs-list', run['id'])
        page.click('#replay-open')
        page.wait_for_timeout(1500)
        page.wait_for_function("document.querySelector('#replay-label').textContent.includes('READ ONLY')", timeout=90000)
        page.on('request', lambda r: mutations.append(r.url) if r.method == 'POST' and r.frame == page.main_frame else None)
        for control in ['start', 'reset', 'compare', 'record-next']:
            assert page.locator('#' + control).is_disabled(), control
        assert page.locator('[data-speed="4"]').is_disabled()
        page.click('#replay-step')
        page.select_option('#replay-speed', '4')
        page.click('#replay-play')
        page.wait_for_timeout(500)
        page.click('#replay-play')
        page.locator('#replay-seek').fill('0')
        page.wait_for_timeout(500)
        page.screenshot(path=str(output / 'replay.png'), full_page=True)
        assert not mutations, mutations
        with page.expect_download() as download:
            page.click('#replay-download')
        download.value.save_as(str(output / 'recording.zip'))
        page.click('#replay-live')
        page.wait_for_function("!document.querySelector('#start').disabled", timeout=90000)
        assert page.locator('#prepared-models').is_enabled()
        page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        page.click('#expand')
        page.wait_for_function('!!document.fullscreenElement')
        page.click('#expand')
        page.wait_for_function('!document.fullscreenElement')
        for width in [900, 390]:
            page.set_viewport_size({'width': width, 'height': 900})
            page.wait_for_timeout(400)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.screenshot(path=str(output / f'layout-{width}.png'), full_page=True)
        assert not errors, errors
        report = {'run': run['id'], 'errors': errors, 'replay_mutations': [],
                  'speeds': [1, 2, 4, 8], 'camera_views': ['follow', 'pov', 'plan', 'orbit'],
                  'mission_astar': True, 'pause': True, 'replay_download': True,
                  'return_live': True, 'fullscreen': True, 'responsive_widths': [1440, 900, 390]}
        (output / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5174')
    parser.add_argument('--output', type=Path, default=Path('data/validation/final-ui/controls'))
    args = parser.parse_args()
    verify(args.url, args.output)
