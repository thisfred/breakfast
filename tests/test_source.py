from breakfast.source import Source


def test_ordering():
    source1 = Source(lines=None, module_name='foo')
    source2 = Source(lines=None, module_name='bar')
    assert source1 > source2
    assert source1 != source2
    assert source2 <= source1
    assert source2 == source2


def test_renames_function_from_lines():
    source = Source([
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"])

    source.rename(row=0, column=4, new_name='fun_new')

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]
