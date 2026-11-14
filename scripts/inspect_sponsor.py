"""Read-only, offline sponsor join inspection; never prints credentials."""
import argparse
import csv
import json
from pathlib import Path


def contains_model(value,model):
    if isinstance(value,dict):
        return value.get('videoKey')==model or any(contains_model(v,model) for v in value.values())
    if isinstance(value,list):return any(contains_model(v,model) for v in value)
    return False


def inspect(kit,model):
    with (kit/'commercial-properties.csv').open() as f:
        ids={int(r['PropertyId']) for r in csv.DictReader(f) if r['MatterportModelId']==model}
    matches=[]
    with (kit/'data/commercial-property.jsonl').open() as f:
        for line in f:
            record=json.loads(line)
            if record['id'] not in ids:continue
            plans=[]
            for plan in (record.get('propertyApartment') or {}).get('models',[]):
                if contains_model(plan,model):
                    plans.append({k:plan.get(k) for k in ('id','modelName','numBeds','maxOccupants','minSquareFeet','maxSquareFeet')})
            matches.append(dict(property_id=record['id'],model_id=model,plans=plans,
                                buildingConstructionMaterial=record.get('buildingConstructionMaterial'),
                                sprinklerType=record.get('sprinklerType'),
                                warning='0 means unknown; capacity is not live occupancy. Exits require manual scan validation.'))
    return matches

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('model');parser.add_argument('--kit',type=Path,default=Path('data/costar-hackathon-kit'))
    args=parser.parse_args();print(json.dumps(inspect(args.kit,args.model),indent=2))
