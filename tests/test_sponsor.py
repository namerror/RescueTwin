from scripts.inspect_sponsor import contains_model
import json
import pytest


def test_model_join_is_exact_and_nested():
    assert contains_model({'models':[{'attachments':[{'videoKey':'abc','videoExternalReferenceType':36}]}]},'abc')
    assert not contains_model({'videoKey':'abc-extra','description':'abc'},'abc')


def test_wrong_or_missing_reference_type_is_not_matterport():
    assert not contains_model({'videoKey':'abc'},'abc')
    assert not contains_model({'videoKey':'abc','videoExternalReferenceType':12},'abc')
    assert contains_model({'videoKey':'abc','externalReferenceType':234},'abc')


def test_property_only_join_retains_provenance_without_inventing_capacity(tmp_path):
    from scripts.inspect_sponsor import context
    (tmp_path/'data').mkdir(); (tmp_path/'schemas').mkdir()
    (tmp_path/'commercial-properties.csv').write_text('PropertyId,MatterportModelId\n7,scan\n7,wrong\n')
    (tmp_path/'data/commercial-property.jsonl').write_text(json.dumps(dict(id=7,
        attachments=[dict(videoKey='scan',videoExternalReferenceType=36)],
        primaryAddress=dict(streetNumber='1',streetName='Main',city='City',state='CA')))+'\n')
    (tmp_path/'data/apartment-listing.jsonl').write_text('')
    (tmp_path/'schemas/enums.json').write_text('{}')
    result=context(tmp_path,'scan',7)
    assert result['max_occupants'] is None and result['floor_plan_id'] is None
    assert result['join_evidence'][0]['field_path']=='attachments[0].videoKey'
    assert not {'max_occupants','floor_plan','beds','baths','area'} & {f['key'] for f in result['fields']}
    assert all('[None]' not in f['field_path'] for f in result['fields'])
    with pytest.raises(ValueError,match='videoKey'):
        context(tmp_path,'wrong',7)
