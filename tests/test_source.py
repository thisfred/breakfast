from breakfast.source import Source


def test_ordering() -> None:
    source1 = Source(path="foo.py", project_root=".", input_lines=())
    source2 = Source(path="bar.py", project_root=".", input_lines=())
    assert source1 > source2
    assert source1 != source2
    assert source2 < source1
    assert source2 == source2


def test_module_name() -> None:
    source = Source(path=__file__, project_root=".", input_lines=())
    assert source.module_name == "tests.test_source"
