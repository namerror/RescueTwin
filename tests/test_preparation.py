import pytest
from shapely.geometry import box
from scripts.prepare_scene import apply_room_clips


def test_room_clip_cannot_add_floor_or_change_source_polygon():
    source=[dict(id='r',polygon=box(0,0,4,4))]
    result=apply_room_clips(source,[dict(room_id='r',polygon=[[-1,-1],[6,-1],[6,2],[-1,2]],evidence='Reviewed unscanned extension')],[])
    assert result[0]['polygon'].area==8
    assert result[0]['polygon'].difference(source[0]['polygon']).is_empty
    assert source[0]['polygon'].area==16


def test_room_clip_rejects_removed_capture_and_missing_evidence():
    source=[dict(id='r',polygon=box(0,0,4,4))]
    clip=dict(room_id='r',polygon=[[0,0],[4,0],[4,2],[0,2]],evidence='Reviewed boundary')
    with pytest.raises(ValueError,match='capture position'):
        apply_room_clips(source,[clip],[dict(position=dict(x=1,y=3))])
    with pytest.raises(ValueError,match='source evidence'):
        apply_room_clips(source,[{**clip,'evidence':''}],[])


def test_bundle_query_does_not_require_unrequested_floor_metadata(monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts import prepare_scene
    monkeypatch.setattr(prepare_scene,'credentials',lambda:('test-user','test-token'))
    monkeypatch.setattr(prepare_scene.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps({'data':{'model':{'bundle':{'assets':[]}}}})))
    assert prepare_scene.graph('scan',required=('bundle',))=={'bundle':{'assets':[]}}


@pytest.mark.parametrize('required, response', [
    (('bundle',), {'bundle': {'assets': []}}),
    (('assets',), {'assets': {'meshes': []}}),
])
def test_asset_queries_are_isolated_and_do_not_require_floorplans(monkeypatch, required, response):
    import json
    from types import SimpleNamespace
    from scripts import prepare_scene
    original = prepare_scene.QUERY
    query = 'query AssetInspection { model { id } }'
    monkeypatch.setattr(prepare_scene, 'credentials', lambda: ('test-user', 'test-token'))
    def run(args, **kwargs):
        assert json.loads(args[args.index('-d') + 1])['query'] == query
        return SimpleNamespace(stdout=json.dumps({'data': {'model': response}}))
    monkeypatch.setattr(prepare_scene.subprocess, 'run', run)
    assert prepare_scene.graph('scan', 1, required, query=query) == response
    assert prepare_scene.QUERY == original
