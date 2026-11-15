import json
import pytest
from scripts.residential_context import context


def kit(tmp_path):
    (tmp_path/'data').mkdir();(tmp_path/'schemas').mkdir()
    (tmp_path/'residential-listings.csv').write_text('ListingId,MatterportModelId\n12,scan\n12,wrong\n')
    listing=dict(id=12,property=dict(address=dict(fullStreetAddress='1 Main St'),stories=1),
                 media=[dict(videoKey='scan',externalReferenceType=36)])
    (tmp_path/'data/residential-listings.jsonl').write_text(json.dumps(listing)+'\n')
    (tmp_path/'data/residential-listing-features.jsonl').write_text(json.dumps(dict(id=12,features=[
        dict(id=2966,name='Fire Sprinkler System',boolValue=None,source='Mls'),
        dict(id=2893,name='Heating System Uses Gas',boolValue=False,source='Mls'),
        dict(id=3247,name='Sprinkler System',boolValue=True,source='Mls'),
        dict(id=1602,name='Gas Oven',boolValue=None,stringValues=['uninterpreted'],source='Mls'),
        dict(id=2965,name='Fire and Smoke Detector',boolValue=True,source='MachineLearning')]))+'\n')
    (tmp_path/'schemas/enums.json').write_text('{}')
    return tmp_path


def test_exact_residential_fixture_join_ignores_irrigation_and_machine_learning(tmp_path):
    result=context(kit(tmp_path),'scan',12);fields={f['key']:f for f in result['fields']}
    assert fields['has_sprinklers']['value'] is True
    assert fields['has_sprinklers']['feature_id']==2966
    assert fields['has_sprinklers']['field_path']=='features[0]'
    assert fields['gas_heating']['value'] is False
    assert 'smoke_detector' not in fields
    assert fields['gas_oven']['value'] is None
    assert result['max_occupants'] is None
    assert result['join_evidence'][0]['field_path']=='media[0].videoKey'
    with pytest.raises(ValueError,match='videoKey'):
        context(tmp_path,'wrong',12)
