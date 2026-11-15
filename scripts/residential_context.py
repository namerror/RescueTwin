"""Exact MLS-listing / Matterport / recorded-fixture joins. No contact fields exported."""
import csv
import json

try:
    from .inspect_sponsor import contains_model, enum_name, model_references
except ImportError:
    from inspect_sponsor import contains_model, enum_name, model_references

FIXTURES = {
    2893: ('gas_heating', 'Gas heating'), 2706: ('gas_heating', 'Natural gas heating'),
    2966: ('has_sprinklers', 'Fire sprinkler system'), 2965: ('smoke_detector', 'Fire and smoke detector'),
    1620: ('gas_water_heater', 'Gas water heater'),
    1602: ('gas_oven', 'Gas oven'), 1071: ('gas_oven', 'Built-in gas oven'),
    1604: ('gas_range', 'Gas range'), 1580: ('gas_range', 'Gas oven and range'),
    1577: ('gas_mixed_range', 'Gas and electric range'), 1582: ('gas_cooktop', 'Gas cooktop'),
    3137: ('gas_fireplace', 'Gas log fireplace'), 3410: ('gas_fireplace', 'Gas fireplace'),
    2739: ('gas_cooling', 'Gas-powered cooling'),
}


def record(kit, dataset, listing_id):
    with (kit / 'data' / (dataset+'.jsonl')).open() as stream:
        return next((r for line in stream if (r := json.loads(line))['id'] == listing_id), None)


def context(kit, model, listing_id):
    with (kit/'residential-listings.csv').open() as stream:
        joined = any(int(r['ListingId']) == listing_id and r['MatterportModelId'] == model for r in csv.DictReader(stream))
    listing = record(kit, 'residential-listings', listing_id)
    if not joined or listing is None or not contains_model(listing.get('media'), model):
        raise ValueError('Residential CSV ListingId and media videoKey must both match the model')
    feature_record = record(kit, 'residential-listing-features', listing_id)
    enums = json.loads((kit/'schemas/enums.json').read_text())
    prop = listing.get('property') or {}; address=prop.get('address') or {}
    fields=[]
    def field(key, label, path, value, units=None, derivation='copied', **extra):
        fields.append(dict(key=key,label=label,field_path=path,value=value,units=units,
            dataset='residential-listings',record_type='ResidentialListing',source_id=listing_id,derivation=derivation,**extra))
    street = ' '.join(str(address[k]) for k in ('streetNumber','streetDirection','streetName','streetType','unit') if address.get(k))
    # The schema's display address, when present, is preferable to reconstruction.
    street = address.get('fullStreetAddress') or address.get('streetAddress') or address.get('addressLine') or street
    field('address','Address','property.address.fullStreetAddress' if address.get('fullStreetAddress') else 'property.address',street or f'Residential listing {listing_id}',
          derivation='copied' if address.get('fullStreetAddress') else 'concatenated address components or source ID fallback')
    field('property_type','Property type','property.propertyType',enum_name(enums,'ListingPropertyType',prop.get('propertyType')),derivation='enum name')
    for key,label,path,units in [('stories','Stories','stories','stories'),('year_built','Year built','yearBuilt','year'),
                                ('beds','Bedrooms','bedsMin','bedrooms'),('baths','Bathrooms','bathsMin','bathrooms'),('area','Living area','sqftMin','sq ft')]:
        field(key,label,'property.'+path,prop.get(path),units)
    if feature_record:
        for index, feature in enumerate(feature_record.get('features') or []):
            if feature.get('id') not in FIXTURES or feature.get('source') != 'Mls': continue
            key,label=FIXTURES[feature['id']]
            # Per the kit schema: a feature with no value is a presence indicator.
            value=feature.get('boolValue')
            presence = value is None and feature.get('doubleValue') is None and not feature.get('stringValues')
            if presence: value=True
            fields.append(dict(key=key,label=label,value=value,units=None,dataset='residential-listing-features',
                source_id=listing_id,record_type='ResidentialListingFeatures',field_path=f'features[{index}]',
                feature_id=feature['id'],source_attribute=feature['source'],source_name=feature['name'],
                derivation='explicit boolValue' if feature.get('boolValue') is not None else 'MLS feature presence (no value), per dataset schema' if presence else 'non-boolean source value; interpreted as unknown',
                note='Home-level reported attribute; fixture location and condition are not verified.'))
    for key in FIXTURES.values():
        entries=[f for f in fields if f['key']==key[0]]
        known={f['value'] for f in entries if type(f['value']) is bool}
        if len(known)>1:
            for f in entries:
                f['recorded_value']=f['value'];f['value']=None
                f['note']+=' Conflicting source indicators; interpreted as unknown.'
    return dict(model_id=model,listing_id=listing_id,property_id=None,floor_plan_id=None,max_occupants=None,
        fields=fields,capacity_conflicts=[],join_evidence=[dict(dataset='residential-listings',source_id=listing_id,
            field_path=p,value=model) for p in model_references(listing,model)],
        join='residential-listings.csv ListingId -> residential-listings.id -> exact media.videoKey; features.id matches ListingId',
        disclaimer='Recorded MLS property and fixture attributes, not live occupancy or verified fixture positions. Capacity is unknown; contacts excluded.')
