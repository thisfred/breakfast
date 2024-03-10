from breakfast.source import Source, TextRange

from tests import make_source


def test_ordering():
    source1 = Source(path="foo.py", project_root=".", input_lines=())
    source2 = Source(path="bar.py", project_root=".", input_lines=())
    assert source1 > source2
    assert source1 != source2
    assert source2 < source1
    assert source2 == source2


def test_module_name():
    source = Source(path=__file__, project_root=".", input_lines=())
    assert source.module_name == "tests.test_source"


def test_get_enclosing_function_range_should_return_function_definition_range():
    source = make_source(
        """
        class C:
            def m(self):
                start, end = self.extended_range
                text = start.through(end).text
        """
    )
    position = source.position(4, 0)

    text_range = source.get_enclosing_function_range(position)
    assert text_range == TextRange(source.position(2, 4), source.position(4, 38))


def test_get_largest_enclosing_range_should_return_class_definition_range():
    source = make_source(
        """
        class C:
            def m(self):
                start, end = self.extended_range
                text = start.through(end).text
        """
    )
    position = source.position(4, 0)

    text_range = source.get_largest_enclosing_scope_range(position)
    assert text_range == TextRange(source.position(1, 0), source.position(4, 38))
