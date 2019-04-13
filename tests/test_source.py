from breakfast.source import Source


def test_ordering():
    source1 = Source(lines=None, module_name="foo")
    source2 = Source(lines=None, module_name="bar")
    assert source1 > source2
    assert source1 != source2
    assert source2 <= source1
    assert source2 == source2
