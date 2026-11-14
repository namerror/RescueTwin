"""Read-only, offline sponsor join inspection; never prints credentials or contacts."""
import argparse
import csv
import json
from pathlib import Path

CAPACITY_NOTE = 'Marketed maximum occupants for this floor plan; not live or actual occupancy.'


def contains_model(value,model):
    if isinstance(value,dict):
        return value.get('videoKey')==model or any(contains_model(v,model) for v in value.values())
    if isinstance(value,list):return any(contains_model(v,model) for v in value)
    return False


def property_ids(kit,model):
    with (kit/'commercial-properties.csv').open() as f:
        return {int(r['PropertyId']) for r in csv.DictReader(f) if r['MatterportModelId']==model}


def records(kit,ids):
    with (kit/'data/commercial-property.jsonl').open() as f:
        for line in f:
            record=json.loads(line)
            if record['id'] in ids:yield record


def inspect(kit,model):
    matches=[]
    for record in records(kit,property_ids(kit,model)):
        plans=[]
        for plan in (record.get('propertyApartment') or {}).get('models',[]):
            if contains_model(plan,model):
                plans.append({k:plan.get(k) for k in ('id','modelName','numBeds','maxOccupants','minSquareFeet','maxSquareFeet')})
        matches.append(dict(property_id=record['id'],model_id=model,plans=plans,
                            buildingConstructionMaterial=record.get('buildingConstructionMaterial'),
                            sprinklerType=record.get('sprinklerType'),
                            warning='0 means unknown; capacity is not live occupancy. Exits require manual scan validation.'))
    return matches


def enum_name(enums,enum,value):
    """Return the schema name for an enum value; 0 stays 'Unknown', never a known value."""
    if value is None:return 'Unknown (null)'
    return enums.get(enum,{}).get(str(value),f'Unmapped ({value})')


def context(kit,model,property_id,plan_id):
    """CoStar context with field-level provenance for one verified property/model/floor-plan join."""
    if property_id not in property_ids(kit,model):
        raise ValueError(f'PropertyId {property_id} is not joined to model {model} in commercial-properties.csv')
    record=next(records(kit,{property_id}),None)
    if record is None:raise ValueError(f'PropertyId {property_id} missing from commercial-property.jsonl')
    enums=json.loads((kit/'schemas/enums.json').read_text())
    plans=(record.get('propertyApartment') or {}).get('models',[])
    index,plan=next(((i,p) for i,p in enumerate(plans) if p.get('id')==plan_id),(None,None))
    if plan is None or not contains_model(plan,model):
        raise ValueError(f'Floor plan {plan_id} does not carry a videoKey attachment for {model}')
    source=dict(dataset='commercial-property',source_id=property_id,record_type='CommercialProperty')

    def field(key,label,path,value,units=None,derivation='copied',note=None):
        entry=dict(key=key,label=label,value=value,units=units,field_path=path,derivation=derivation,**source)
        if note:entry['note']=note
        return entry

    address=record.get('primaryAddress') or {}
    street=' '.join(str(address[k]) for k in ('streetNumber','streetDirection','streetName','streetType') if address.get(k))
    built=[e.get('year') for e in record.get('constructionEvents') or [] if e.get('eventType')==2]
    plan_path=f'propertyApartment.models[{index}]'
    fields=[
        field('address','Address',"primaryAddress.{streetNumber,streetDirection,streetName,streetType,city,state}",
              f"{street}, {address.get('city')}, {address.get('state')}",derivation='concatenated'),
        field('property_type','Property type','propertyType',enum_name(enums,'PropertyType',record.get('propertyType')),derivation='enum name'),
        field('apartment_style','Apartment style','propertyApartment.apartmentStyleType',
              enum_name(enums,'ApartmentStyleType',(record.get('propertyApartment') or {}).get('apartmentStyleType')),derivation='enum name'),
        field('stories','Stories above grade','numStoriesAboveGrade',record.get('numStoriesAboveGrade'),'stories'),
        field('total_units','Total units','propertyApartment.totalNumUnits',(record.get('propertyApartment') or {}).get('totalNumUnits'),'units'),
        field('year_built','Construction complete','constructionEvents[eventType=2].year',built[0] if built else None,'year'),
        field('construction_material','Construction material','buildingConstructionMaterial',
              enum_name(enums,'BuildingConstructionMaterialType',record.get('buildingConstructionMaterial')),derivation='enum name',
              note='Structural system, not interior fuel load.'),
        field('sprinkler_type','Sprinkler type','sprinklerType',enum_name(enums,'SprinklerType',record.get('sprinklerType')),derivation='enum name',
              note='Unknown does not mean absent.'),
        field('has_sprinklers','Sprinklers present','hasSprinklers',record.get('hasSprinklers'),
              note='null = unknown, not no.'),
        field('floor_plan','Matched floor plan',f'{plan_path}.modelName',plan.get('modelName'),derivation=f'matched by id {plan_id} and videoKey {model}'),
        field('beds','Bedrooms',f'{plan_path}.numBeds',plan.get('numBeds'),'bedrooms'),
        field('baths','Bathrooms',f'{plan_path}.numBaths',plan.get('numBaths'),'bathrooms'),
        field('area','Floor-plan area',f'{plan_path}.maxSquareFeet',plan.get('maxSquareFeet'),'sq ft'),
        field('max_occupants','Marketed max occupants',f'{plan_path}.maxOccupants',plan.get('maxOccupants'),'people',note=CAPACITY_NOTE),
    ]
    return dict(property_id=property_id,model_id=model,floor_plan_id=plan_id,
                max_occupants=plan.get('maxOccupants'),fields=fields,
                join='commercial-properties.csv PropertyId -> commercial-property.id; floor plan videoKey == Matterport model id',
                disclaimer='Real CoStar property record. Capacity is marketed, not actual occupancy. Contacts are excluded.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('model');parser.add_argument('--kit',type=Path,default=Path('data/costar-hackathon-kit'))
    parser.add_argument('--property',type=int);parser.add_argument('--plan',type=int)
    args=parser.parse_args()
    if args.property and args.plan:print(json.dumps(context(args.kit,args.model,args.property,args.plan),indent=2))
    else:print(json.dumps(inspect(args.kit,args.model),indent=2))
