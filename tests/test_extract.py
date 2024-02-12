import pytest
from breakfast.refactoring.extract import (
    Edit,
    extract_function,
    extract_variable,
    slide_statements,
)

from tests import dedent, make_source


def test_extract_variable_should_insert_name_definition():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    insert, *_ = extract_variable(name="b", start=extraction_start, end=extraction_end)
    assert insert.text == "b = a + 3\n"


def test_extract_variable_should_replace_extracted_test_with_new_name():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    _, replace = extract_variable(name="b", start=extraction_start, end=extraction_end)
    assert replace == Edit(start=extraction_start, end=extraction_end, text="b")


def test_extract_variable_should_insert_name_definition_before_extraction_point():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    insert, *_ = extract_variable(name="b", start=extraction_start, end=extraction_end)

    assert insert.start < extraction_start


def test_extract_variable_should_replace_code_with_variable():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 21)
    edits = extract_variable(name="result", start=extraction_start, end=extraction_end)

    assert edits == (
        Edit(
            source.position(1, 0),
            source.position(1, 0),
            "result = some_calculation()\n",
        ),
        Edit(extraction_start, extraction_end, "result"),
    )


def test_extract_variable_will_not_extract_partial_expression():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 20)
    edits = extract_variable(name="result", start=extraction_start, end=extraction_end)
    assert not edits


def test_extract_variable_should_move_definition_before_current_statement():
    source = make_source(
        """
        a = (
            b,
            some_calculation() + 3,
            c,
        )
        """
    )
    extraction_start = source.position(3, 4)
    extraction_end = source.position(3, 21)
    insert, _ = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )
    assert insert.start.row == 1


def test_extract_variable_should_extract_value_in_index_position():
    source = make_source(
        """
        def node_position(self, node: AST) -> types.Position:
            line = self.guaranteed_lines[node.lineno - 1]
        """
    )
    extraction_start = source.position(2, 33)
    extraction_end = source.position(2, 47)
    insert, _ = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )
    assert insert.start.row == 2


def test_extract_variable_should_retain_indentation_level():
    source = make_source(
        """
        def f():
            a = (
                b,
                some_calculation() + 3,
                c,
            )
        """
    )
    extraction_start = source.position(4, 8)
    extraction_end = source.position(4, 25)
    insert, *_ = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )
    assert insert.start == source.position(2, 4)


def test_extract_variable_should_extract_all_identical_nodes_in_the_same_scope():
    source = make_source(
        """
        b = some_calculation() + 3
        a = (
            b,
            some_calculation() + 3,
            c,
        )
        some_calculation() + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 25)

    _, *edits = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )

    assert len(edits) == 3


def test_extract_variable_should_extract_before_first_occurrence():
    source = make_source(
        """
        b = some_calculation() + 3
        a = (
            b,
            some_calculation() + 3,
            c,
        )
        some_calculation() + 3
        """
    )
    extraction_start = source.position(4, 4)
    extraction_end = source.position(4, 25)

    insert, *edits = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )

    assert insert.start.row == 1
    assert insert.start.column == 0


def test_extract_variable_should_not_extract_occurrences_in_other_function():
    source = make_source(
        """
        def f():
            b = some_calculation() + 3

        def g():
            c = some_calculation() + 3
        """
    )
    extraction_start = source.position(2, 8)
    extraction_end = source.position(2, 29)
    _insert, *edits = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )

    assert len(edits) == 1


def test_extract_variable_should_not_extract_occurrences_in_other_method_of_the_same_name():
    source = make_source(
        """
        class A:
            def f():
                b = some_calculation() + 3

        class B:
            def f():
                c = some_calculation() + 3
        """
    )
    extraction_start = source.position(3, 12)
    extraction_end = source.position(3, 33)
    _insert, *edits = extract_variable(
        name="result", start=extraction_start, end=extraction_end
    )

    assert len(edits) == 1


def test_extract_function_should_insert_function_definition():
    source = make_source(
        """
        value = 0
        something = print(value + 8)
        """
    )
    extraction_start = source.position(2, 12)
    extraction_end = source.position(2, 27)

    insert, *_edits = extract_function(
        name="function", start=extraction_start, end=extraction_end
    )

    assert insert.text == dedent(
        """
        def function(value):
            print(value + 8)
        """
    )


def test_extract_function_should_insert_function_definition_with_multiple_statements():
    source = make_source(
        """
        value = 0
        print(value + 20)
        print(max(value, 0))
        """
    )
    extraction_start = source.position(2, 0)
    extraction_end = source.position(3, 21)

    insert, *_edits = extract_function(
        name="function", start=extraction_start, end=extraction_end
    )

    result = dedent(
        """
        def function(value):
            print(value + 20)
            print(max(value, 0))
        """
    )

    assert insert.text.rstrip() == result.rstrip()


def test_extract_function_should_create_arguments_for_local_variables():
    source = make_source(
        """
        value = 0
        other_value = 1
        print(value + 20)
        print(max(other_value, value))
        """
    )

    start = source.position(3, 0)
    end = source.position(10, 21)

    insert, *_edits = extract_function(name="function", start=start, end=end)

    result = dedent(
        """
        def function(value, other_value):
            print(value + 20)
            print(max(other_value, value))
        """
    )

    assert insert.text.rstrip() == result.rstrip()


def test_slide_statements_should_not_slide_beyond_first_usage():
    source = make_source(
        """
        value = 0
        print(value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[1]

    edits = slide_statements(first=first, last=last)

    assert not edits


@pytest.mark.xfail
def test_slide_statements_should_slide_past_irrelevant_statements():
    source = make_source(
        """
        value = 0
        other_value = 3
        print(value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[1]

    insert, delete = slide_statements(first=first, last=last)

    assert insert.start.row == 3
    assert delete.start.row == 1
