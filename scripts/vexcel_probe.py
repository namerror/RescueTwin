"""Read-only parcel coverage probe; does not assert indoor registration or select the first parcel."""
import argparse
import csv
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone


def probe(model, output):
    values=dict(os.environ)
    if Path('.env').exists():
        for line in Path('.env').read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,_,value=line.partition('=');values.setdefault(key.strip(),value.strip().strip('\"').strip("'"))
    key=values.get('VEXCEL_API_KEY')
    if not key:raise ValueError('VEXCEL_API_KEY is not configured')
    if any(c in key for c in '\r\n"'):raise ValueError('Invalid credential format')
    with open('matterport_examples/models_index.csv') as f:
        row=next(r for r in csv.DictReader(f) if r['sid']==model)
    lat,lon=float(row['lat']),float(row['lon'])
    params={'wkt':f'POINT({lon} {lat})','srid':4326,'layer':'urban,urban-r,wide-area,urban-g','latest':'true','include':'precomputed','unit-system':'imperial'}
    result=subprocess.run(['curl','-sS','-K','-','--max-time','45','-w','\n%{http_code}','https://api.vexcelgroup.com/v2/property/extract?'+urlencode(params)],input=f'header = "X-API-KEY: {key}"\n',capture_output=True,text=True)
    body,_,code=result.stdout.rpartition('\n')
    report={'model':model,'checked_at':datetime.now(timezone.utc).isoformat(),'endpoint':'property/extract','http_status':code,'coordinate_source':'models_index.csv '+row['latlon_source'],'point':[lon,lat],'srid':4326,'units':'imperial','selected_parcel':None,'registration':'unverified; coordinate lookup is not indoor alignment'}
    if result.returncode==0 and code=='200':
        data=json.loads(body)
        report['candidates']=[{'id':f.get('id'),'geometry':f.get('geometry'),'properties':{k:v for k,v in f.get('properties',{}).items() if k.startswith(('metadata.','parcel-','structure.','roof-')) or k=='structures'}} for f in data.get('features',[])]
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2))
    print(json.dumps({'model':model,'http_status':code,'candidate_parcels':len(report.get('candidates',[])),'selected_parcel':None,'output':str(output)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('--output',type=Path,required=True);a=p.parse_args();probe(a.model,a.output)
