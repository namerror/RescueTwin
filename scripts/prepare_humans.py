"""Verify all six cached human meshes, or reproduce missing variants with pinned source assets.

Cold conversion needs Blender 5.2.2 (BLENDER_BIN may override its location).
Warm runs verify SHA256 and require neither Blender nor network access.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parent.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_variants():
    manifest=json.loads((ROOT/'frontend/public/models/humans.json').read_text())
    assets=ROOT/'frontend/public/models'
    missing=[v for v in manifest['variants'] if not (assets/v['file']).is_file() or digest(assets/v['file'])!=v['sha256']]
    if not missing:
        print('Using six verified cached human meshes.',flush=True)
        return
    if any(v['id']=='adult-male' for v in missing):
        from bringup import prepare_human
        prepare_human()
        missing=[v for v in missing if v['id']!='adult-male']
    if not missing:return
    blender=os.environ.get('BLENDER_BIN') or shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender'
    if not Path(blender).is_file():
        raise RuntimeError('Missing human variants: install Blender 5.2.2, set BLENDER_BIN, or copy the verified GLBs listed in public/models/humans.json.')
    version=subprocess.check_output([blender,'--version'],text=True).splitlines()[0]
    if '5.2.2' not in version:
        raise RuntimeError('Human asset reproduction requires pinned Blender 5.2.2; warm cached assets work without Blender.')
    sources=json.loads((ROOT/'scripts/human_sources.json').read_text())
    root=ROOT/'data/humans';texture_dir=root/'textures';texture_dir.mkdir(parents=True,exist_ok=True)
    from PIL import Image
    for item in sources['files']:
        if not any('/'+v['source_asset']+'/' in item['path'] for v in missing):continue
        path=root/'source'/item['path'];path.parent.mkdir(parents=True,exist_ok=True)
        if not path.is_file() or digest(path)!=item['sha256']:
            url=f'https://raw.githubusercontent.com/microsoft/Microsoft-Rocketbox/{sources["revision"]}/{item["path"]}'
            with urllib.request.urlopen(url,timeout=90) as response:
                data=response.read(item['bytes']+1)
            if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
                raise ValueError('Human source checksum mismatch')
            path.write_bytes(data)
        if path.suffix=='.tga':
            with Image.open(path) as im:
                image=im.convert('RGBA');image.thumbnail((1024,1024));image.save(texture_dir/(path.stem+'.png'))
    for variant in missing:
        source=next(root/'source'/i['path'] for i in sources['files'] if i['path'].endswith('/'+variant['source_asset']+'.fbx'))
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            output=Path(temporary)/variant['file']
            result=subprocess.run([blender,'--background','--factory-startup','--python-exit-code','1','--python',str(ROOT/'scripts/convert_human.py'),
                                   '--',str(source),str(output),str(texture_dir)],capture_output=True,text=True)
            if result.returncode or not output.is_file() or digest(output)!=variant['sha256']:
                raise RuntimeError(f'Human conversion did not reproduce {variant["id"]}; existing assets preserved.')
            shutil.copyfile(output,assets/variant['file'])
        print('Prepared',variant['id'],flush=True)


if __name__=='__main__':ensure_variants()
