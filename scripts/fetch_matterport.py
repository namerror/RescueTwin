"""Download source geometry metadata for preparation; does NOT certify exits."""
import argparse
import base64
import json
import os
from pathlib import Path
import urllib.request


def credentials():
    values={}
    if Path('.env').exists():
        for line in Path('.env').read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,_,value=line.partition('=');values[key.strip()]=value.strip().strip('\"').strip("'")
    values.update(os.environ)
    key=values.get('MATTERPORT_API_KEY') or values.get('MP_API_KEY')
    token=values.get('MATTERPORT_API_TOKEN') or values.get('MP_API_TOKEN')
    if not key or not token:raise SystemExit('Configure Matterport key/token in environment or .env')
    return key,token

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('model');args=parser.parse_args()
    key,token=credentials()
    query='''query($id: ID!) { model(id:$id) { id name
    floors { id label vertices { id position { x y } } edges { id type thickness vertices { id } } }
    rooms { id floor { id } classifications { label confidence } boundary { edges { id } } }
    panoLocations { id label position { x y z } }
    bundle(id:"mp:matterpak") { availability }
    assets { resources(format:"matterpak") { status filename url } }
    } }'''
    request=urllib.request.Request('https://api.matterport.com/api/models/graph',
        data=json.dumps({'query':query,'variables':{'id':args.model}}).encode(),
        headers={'Authorization':'Basic '+base64.b64encode(f'{key}:{token}'.encode()).decode(),'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=60) as response:body=json.load(response)
    if body.get('errors'):raise SystemExit('Matterport rejected query; inspect schema/permissions without logging credentials')
    out=Path('data/prepared')/args.model;out.mkdir(parents=True,exist_ok=True)
    def sanitized(value):
        if isinstance(value,dict):return {k:sanitized(v) for k,v in value.items() if k not in ('url','downloadUrl','urlTemplate','children')}
        if isinstance(value,list):return [sanitized(v) for v in value]
        return value
    (out/'source.json').write_text(json.dumps(sanitized(body['data']['model']),indent=2))
    print(f'Saved source metadata to {out}/source.json. Signed asset links expire. Exits remain UNVALIDATED.')
