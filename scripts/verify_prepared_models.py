"""Exercise every local prepared model in the browser; captures remain ignored."""
import argparse
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


def verify(url):
    out = Path('data/validation/prepared-models')
    out.mkdir(parents=True, exist_ok=True)
    errors, failed, results = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: failed.append(response.url.split('?')[0])
                if response.status >= 400 and '/files/' in response.url else None)
        page.goto(url)
        page.wait_for_function("window.rescuetwin?.scene() != null", timeout=60000)
        before = page.request.get(url + '/api/state').json()
        assert before['status'] == 'idle', 'Use an isolated idle backend for this check'
        catalog = page.request.get(url + '/api/models').json()
        assert catalog
        page.locator('#prepared-models').click()
        page.wait_for_function('n => document.querySelector("#model-library-select").options.length === n', arg=len(catalog))
        for entry in catalog:
            page.select_option('#model-library-select', entry['id'])
            page.wait_for_function('id => document.querySelector("#model-library-view").dataset.loadedModel === id', arg=entry['id'], timeout=90000)
            assert page.locator('#model-library-use').is_enabled() == bool(entry['scene_id'])
            info = page.locator('#model-library-info').inner_text()
            assert ('Preview only' in info) == (entry['status'] == 'preview-only')
            capture = out / f'{entry["id"]}.png'
            page.locator('#model-library-view').screenshot(path=str(capture))
            with Image.open(capture) as image:
                pixels = list(image.convert('RGB').getdata())
            visible_fraction = sum(max(pixel) > 65 for pixel in pixels) / len(pixels)
            assert visible_fraction > .01, f'{entry["id"]}: model appears empty or clipped ({visible_fraction})'
            results.append({'model': entry['id'], 'status': entry['status'], 'rendered': True,
                            'visible_fraction': visible_fraction})
            print('Rendered', entry['id'], entry['status'], flush=True)
        # A stale load must not replace the selected model after rapid switching.
        for entry in catalog[:4]:
            page.select_option('#model-library-select', entry['id'])
        page.wait_for_function('id => document.querySelector("#model-library-view").dataset.loadedModel === id', arg=catalog[3]['id'], timeout=90000)
        page.locator('#model-library-cutaway').uncheck()
        page.locator('#model-library-fit').click()
        page.screenshot(path=str(out / 'library.png'))
        after = page.request.get(url + '/api/state').json()
        assert (after['scene_id'], after['scenario'], after['time']) == (before['scene_id'], before['scenario'], before['time'])
        for entry in [m for m in catalog if m['scene_id']]:
            print('Switching simulation', entry['id'], flush=True)
            page.select_option('#model-library-select', entry['id'])
            page.wait_for_function('id => document.querySelector("#model-library-view").dataset.loadedModel === id', arg=entry['id'], timeout=90000)
            with page.expect_response(lambda r: r.url.endswith('/api/scene') and r.request.method == 'POST') as switched:
                page.locator('#model-library-use').click()
            assert switched.value.ok
            page.wait_for_function('id => window.rescuetwin.scene()?.id === id && !document.querySelector("#start").disabled', arg=entry['scene_id'], timeout=90000)
            page.locator('#prepared-models').click()
            page.wait_for_function('n => document.querySelector("#model-library-select").options.length === n', arg=len(catalog))
        page.locator('#model-library-close').click()
        assert page.locator('#load-map').inner_text() == 'Import custom floor plan JSON…'
        browser.close()
    report = {'models': results, 'page_errors': errors, 'failed_assets': failed, 'scene_switching': True,
              'preview_preserves_mission': True}
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    assert not errors and not failed, report
    print(f'Passed: {len(results)} models; scene switching; preview isolation', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5176')
    verify(parser.parse_args().url)
