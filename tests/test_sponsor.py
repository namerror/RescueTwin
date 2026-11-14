from scripts.inspect_sponsor import contains_model


def test_model_join_is_exact_and_nested():
    assert contains_model({'models':[{'attachments':[{'videoKey':'abc'}]}]},'abc')
    assert not contains_model({'videoKey':'abc-extra','description':'abc'},'abc')
