"""Download cached source plans/panoramas and textured MatterPaks for the local catalog.

Uses isolated subprocesses because the existing API helpers configure their query globally.
Does not invent annotations or promote unvalidated navigation bundles.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import hashlib
from pathlib import Path
import subprocess
import sys


def download(model, root):
    directory = root / model
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / 'preparation-download.log'
    steps = []
    if not (directory / 'source/source.json').is_file():
        steps.append([sys.executable, 'scripts/prepare_scene.py', 'fetch', model, '--root', str(root), '--panos', '0'])
    if not (directory / 'source-assets/matterpak-status.json').is_file():
        steps.append([sys.executable, 'scripts/scene_assets.py', 'matterpak', model, '--root', str(root)])
    for command in steps:
        result = subprocess.run(command, capture_output=True, text=True)
        # These CLIs emit URL-free reports; their network helpers redact failures.
        with log.open('a') as stream:
            stream.write(result.stdout + result.stderr)
        if result.returncode:
            return dict(model=model, downloaded=False, failed_step=command[2])
    return dict(model=model, downloaded=True)


def prepare(model, root):
    directory = root / model
    manifest = directory / 'bundle/scene.json'
    if manifest.exists():
        scene = json.loads(manifest.read_text())
        if (scene.get('mesh') or {}).get('textured') and (scene['mesh'].get('validation') or {}).get('passed'):
            return dict(model=model, prepared=True, cached=True)
    commands = [[sys.executable, 'scripts/prepare_scene.py', 'build', model, '--root', str(root)]]
    status = json.loads((directory / 'source-assets/matterpak-status.json').read_text())
    digest = hashlib.sha256(json.dumps(status['extracted'], sort_keys=True).encode()).hexdigest()[:20]
    obj = directory / 'source-assets' / ('matterpak-' + digest) / status['obj']
    commands.append([sys.executable, 'scripts/scene_assets.py', 'import', model, str(obj), '--root', str(root)])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        with (directory / 'preparation-build.log').open('a') as stream:
            stream.write(result.stdout + result.stderr)
        if result.returncode:
            return dict(model=model, prepared=False, failed_step=command[2])
    return dict(model=model, prepared=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='*')
    parser.add_argument('--root', type=Path, default=Path('data/prepared'))
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--phase', choices=['download', 'build'], default='download')
    args = parser.parse_args()
    wanted = args.models or [p.name for p in sorted(args.root.iterdir()) if (p / 'source-assets/inspection.json').is_file()]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(download if args.phase == 'download' else prepare, model, args.root) for model in wanted]
        for job in as_completed(jobs):
            result = job.result(); results.append(result)
            print(json.dumps(result), flush=True)
    (args.root / f'catalog-{args.phase}.json').write_text(json.dumps(results, indent=2))
    if not all(r.get('downloaded', r.get('prepared', False)) for r in results):
        raise SystemExit(1)
