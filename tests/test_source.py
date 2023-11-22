from breakfast.source import Source


def test_ordering() -> None:
    source1 = Source(path=".", project_root=".", lines=(), module_name="foo")
    source2 = Source(path=".", project_root=".", lines=(), module_name="bar")
    assert source1 > source2
    assert source1 != source2
    assert source2 < source1
    assert source2 == source2
