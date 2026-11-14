from scripts.inspect_sponsor import contains_model


def test_model_join_is_exact_and_nested():
    assert contains_model({'models':[{'attachments':[{'videoKey':'abc','videoExternalReferenceType':36}]}]},'abc')
    assert not contains_model({'videoKey':'abc-extra','description':'abc'},'abc')


def test_wrong_or_missing_reference_type_is_not_matterport():
    assert not contains_model({'videoKey':'abc'},'abc')
    assert not contains_model({'videoKey':'abc','videoExternalReferenceType':12},'abc')
    assert contains_model({'videoKey':'abc','externalReferenceType':234},'abc')
