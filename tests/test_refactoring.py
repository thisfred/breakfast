import pytest
from breakfast.refactoring import Edit, Refactor
from breakfast.source import Source, TextRange

from tests import dedent, make_source


def test_extract_variable_should_insert_name_definition():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_ = refactor.extract_variable(name="b")
    assert insert.text == "b = a + 3\n"


def test_extract_variable_should_replace_extracted_test_with_new_name():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    _, replace = refactor.extract_variable(name="b")
    assert replace == Edit(
        TextRange(start=extraction_start, end=extraction_end), text="b"
    )


def test_extract_variable_should_insert_name_definition_before_extraction_point():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_ = refactor.extract_variable(name="b")

    assert insert.start < extraction_start


def test_extract_variable_should_replace_code_with_variable():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 22)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    edits = refactor.extract_variable(name="result")

    assert edits == (
        Edit(
            TextRange(source.position(1, 0), source.position(1, 0)),
            "result = some_calculation()\n",
        ),
        Edit(TextRange(extraction_start, extraction_end), "result"),
    )


def test_extract_variable_will_not_extract_partial_expression():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 21)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    edits = refactor.extract_variable(name="result")
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
    extraction_end = source.position(3, 22)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, _ = refactor.extract_variable(name="result")
    assert insert.start.row == 1


def test_extract_variable_should_extract_value_in_index_position():
    source = make_source(
        """
        def node_position(self, node: AST) -> types.Position:
            line = self.guaranteed_lines[node.lineno - 1]
        """
    )
    extraction_start = source.position(2, 33)
    extraction_end = source.position(2, 48)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, _ = refactor.extract_variable(name="result")
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
    extraction_end = source.position(4, 26)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_ = refactor.extract_variable(name="result")
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
    extraction_end = source.position(1, 26)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    _, *edits = refactor.extract_variable(name="result")

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
    extraction_end = source.position(4, 26)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *edits = refactor.extract_variable(name="result")

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
    extraction_end = source.position(2, 30)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    _insert, *edits = refactor.extract_variable(name="result")

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
    extraction_end = source.position(3, 34)
    refactor = Refactor(TextRange(extraction_start, extraction_end))
    _insert, *edits = refactor.extract_variable(name="result")

    assert len(edits) == 1


def test_extract_function_should_insert_function_definition():
    source = make_source(
        """
        value = 0
        something = abs(value + 8)
        """
    )
    extraction_start = source.position(2, 12)
    extraction_end = source.position(2, 27)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_edits = refactor.extract_function(name="function")

    assert insert.text == dedent(
        """
        def function(value):
            return abs(value + 8)
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

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_edits = refactor.extract_function(name="function")

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
    end = source.position(4, 21)

    refactor = Refactor(TextRange(start, end))
    insert, *_edits = refactor.extract_function(name="function")

    result = dedent(
        """
        def function(value, other_value):
            print(value + 20)
            print(max(other_value, value))
        """
    )

    assert insert.text.rstrip() == result.rstrip()


def test_extract_function_should_return_modified_variable_used_after_call():
    source = make_source(
        """
        a = 1
        b = a + 2
        print(b)
        """
    )
    start = source.position(2, 0)
    end = source.position(2, 9)
    refactor = Refactor(TextRange(start, end))
    insert, *_edits = refactor.extract_function(name="function")

    result = dedent(
        """
        def function(a):
            b = a + 2
            return b
        """
    )

    assert insert.text.rstrip() == result.rstrip()


def test_extract_function_should_extract_outside_function():
    source = make_source(
        """
        def f():
            a = 1
            b = a + 2
            print(b)
        """
    )
    start = source.position(3, 0)
    end = source.position(3, 13)
    refactor = Refactor(TextRange(start, end))
    insert, *_edits = refactor.extract_function(name="function")

    result = """
def function(a):
    b = a + 2
    return b
"""

    assert insert.text.rstrip() == result.rstrip()


def test_extract_function_should_extract_after_current_scope():
    source = make_source(
        """
        def f():
            a = 1
            b = a + 2
            print(b)
        """
    )
    start = source.position(3, 0)
    end = source.position(3, 12)
    refactor = Refactor(TextRange(start, end))
    insert, *_edits = refactor.extract_function(name="function")

    assert insert.start == source.position(5, 0)


def test_extract_function_should_handle_indented_arguments_of_enclosing_scope():
    source = make_source(
        """
        def f(
            i,
            j,
        ):
            a = 1
            b = a + 2
            print(b)
        """
    )
    start = source.position(6, 0)
    end = source.position(6, 12)
    refactor = Refactor(TextRange(start, end))
    insert, *_edits = refactor.extract_function(name="function")

    assert insert.start == source.position(8, 0)


def test_extract_function_should_only_consider_variables_in_scope():
    source = make_source(
        """
        b = 1

        def f2(a):
            b = a + 2
            print(b)
        """
    )
    start = source.position(4, 0)
    end = source.position(4, 12)

    refactor = Refactor(TextRange(start, end))
    insert, replace = refactor.extract_function(name="function")

    assert "function(a)" in insert.text


def test_extract_function_should_only_pass_in_variables_defined_in_local_scope():
    source = make_source(
        """
        class C:
            ...

        def f2():
            a = C()
            print(a)
        """
    )
    start = source.position(5, 0)
    end = source.position(6, 0)

    refactor = Refactor(TextRange(start, end))
    insert, replace = refactor.extract_function(name="function")

    assert "function()" in insert.text


def test_extract_function_should_replace_extracted_code_with_function_call():
    source = make_source(
        """
        def f():
            a = 1
            b = a + 2
            print(b)
        """
    )
    start = source.position(3, 0)
    end = source.position(3, 12)

    refactor = Refactor(TextRange(start, end))
    _insert, replace = refactor.extract_function(name="function")

    assert replace.text == "    b = function(a=a)\n"
    assert replace.start == source.position(3, 0)
    assert replace.end == source.position(3, 12)


def test_extract_function_should_return_multiple_values_where_necessary():
    source = make_source(
        """
        a = 1
        b = a + 2

        print(a)
        print(b)
        """
    )
    start = source.position(1, 0)
    end = source.position(3, 0)
    refactor = Refactor(TextRange(start, end))
    insert, replace = refactor.extract_function(name="function")

    assert "a, b = function()" in replace.text
    assert "return a, b" in insert.text


def test_extract_function_should_handle_empty_lines():
    code = """\
b = 1

def f(a):

    b = a + 2
    print(b)"""
    source = Source(
        input_lines=tuple(line for line in code.split("\n")),
        path="",
        project_root=".",
    )
    start = source.position(4, 0)
    end = source.position(4, 12)
    refactor = Refactor(TextRange(start, end))
    insert, replace = refactor.extract_function(name="function")

    assert "def function(a):" in insert.text
    assert "b = function(a=a)" in replace.text


def test_extract_method_should_replace_extracted_code_with_method_call():
    source = make_source(
        """
        class A:
            def f(self):
                a = 1
                self.b = a + 2
                print(b)
        """
    )
    start = source.position(4, 8)
    end = source.position(4, 21)

    refactor = Refactor(TextRange(start, end))
    _insert, replace = refactor.extract_method(name="method")

    assert replace.text == "self.method(a=a)"
    assert replace.start == source.position(4, 8)
    assert replace.end == source.position(4, 21)


def test_extract_method_should_extract_after_current_method():
    source = make_source(
        """
        class A:
            def f(self):
                a = 1
                self.b = a + 2
                print(b)
        """
    )
    start = source.position(4, 8)
    end = source.position(4, 21)

    refactor = Refactor(TextRange(start, end))
    insert, _replace = refactor.extract_method(name="method")

    assert insert.start.row == 6


def test_extract_method_should_not_repeat_return_variables():
    source = make_source(
        """
        class A:
            def extract_method(self, name: str) -> tuple[Edit, ...]:
                start = self.text_range.start
                end = self.text_range.end
                if start.row < end.row:
                    start = start.start_of_line
                    end = end.line.next.start if end.line.next else end

                print(start, end)
        """
    )
    start = source.position(3, 0)
    end = source.position(8, 0)

    refactor = Refactor(TextRange(start, end))
    _insert, replace = refactor.extract_method(name="method")

    assert replace.text.startswith("        start, end =")


def test_extract_method_should_extract_static_method_when_self_not_used():
    source = make_source(
        """
        class C:
            def m(self):
                start, end = self.extended_range
                text = start.through(end).text
        """
    )

    start = source.position(4, 0)
    end = source.position(5, 0)

    refactor = Refactor(TextRange(start, end))
    insert, _replace = refactor.extract_method(name="method")

    assert "    @staticmethod\n    def method(start, end):" in insert.text


def test_slide_statements_should_not_slide_beyond_first_usage():
    source = make_source(
        """
        value = 0
        print(value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[1]

    refactor = Refactor(TextRange(first.start, last.end))
    edits = refactor.slide_statements_down()

    assert not edits


def test_slide_statements_should_not_slide_into_nested_scopes():
    source = make_source(
        """
        value = 0
        print(
            value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[1]

    refactor = Refactor(TextRange(first.start, last.end))
    edits = refactor.slide_statements_down()
    assert not edits


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

    refactor = Refactor(TextRange(first.start, last.end))
    insert, delete = refactor.slide_statements_down()

    assert insert.start.row == 3
    assert delete.start.row == 1


def test_slide_statements_should_slide_multiple_lines():
    source = make_source(
        """
        value = 0
        other_value = 3
        ...
        ...
        print(value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[2]

    refactor = Refactor(TextRange(first.start, last.end))
    insert, delete = refactor.slide_statements_down()

    assert insert.start.row == 5
    assert insert.text == dedent(
        """\
        value = 0
        other_value = 3
        """
    )


def test_slide_statements_up_should_slide_past_irrelevant_statements():
    source = make_source(
        """
        value = 0
        other_value = 3
        print(value + 20)
        """
    )

    first = source.lines[3]
    last = source.lines[3]

    refactor = Refactor(TextRange(first.start, last.end))
    insert, delete = refactor.slide_statements_up()

    assert insert.start.row == 2
    assert delete.start.row == 3


def test_extract_function_should_extract_to_global_scope():
    source = make_source(
        """
        class C:
            def m():
                a = 1
                a += 1
                return a
        """
    )

    start = source.position(4, 0)
    end = source.position(5, 0)
    refactor = Refactor(TextRange(start, end))
    insert, _ = refactor.extract_function(name="function")

    assert insert.start.row == 6


def test_extract_function_should_consider_function_scope():
    source = make_source(
        """
        def f(p):
            if True:
                d = c(p)
                return d
        """
    )

    start = source.position(3, 0)
    end = source.position(4, 0)
    refactor = Refactor(TextRange(start, end))
    insert, _ = refactor.extract_function(name="g")

    assert "def g(p):" in insert.text


def test_extract_method_should_extract_part_of_a_line():
    source = make_source(
        """
        def inline_call(self) -> tuple[Edit, ...]:
            range_end = self.text_range.start + 2
        """
    )

    start = source.position(2, 16)
    end = source.position(2, 49)

    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.extract_method("f")

    assert dedent(
        """
        def f(self):
            return self.text_range.start + 2
        """
    ) == dedent(insert.text)

    assert edit.text == "self.f()"


def test_inline_call_should_replace_call_with_function_return_value():
    source = make_source(
        """
        def f():
            return 2

        b = f()
        """
    )

    start = source.position(4, 4)
    end = source.position(4, 7)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "result = 2" in insert.text
    assert "result" == edit.text
    assert edit.start == start
    assert edit.end == end


def test_inline_call_should_work_when_cursor_is_in_call():
    source = make_source(
        """
        def f():
            return 2

        b = f()
        """
    )

    start = source.position(4, 4)
    refactor = Refactor(TextRange(start, start))
    insert, edit = refactor.inline_call(name="result")

    assert "result = 2" in insert.text
    assert "result" == edit.text
    assert edit.start == start
    assert edit.end == start + 3


def test_inline_call_should_extract_body_before_assignment():
    source = make_source(
        """
        def f():
            a = 2
            return a

        b = f()
        """
    )

    start = source.position(5, 4)
    end = source.position(5, 6)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "a = 2\nresult = a" in insert.text


def test_inline_call_should_substitute_parameters():
    source = make_source(
        """
        def f(c):
            c += 1
            return c

        a = 2
        b = f(a)
        """
    )

    start = source.position(6, 4)
    end = source.position(6, 7)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "a += 1\nresult = a" in insert.text


def test_inline_call_should_substitute_parameters_in_attribute():
    source = make_source(
        """
        def f(at):
            text = at.source.lines[at.row].text
            return text

        a = 2
        b = f(at=a)
        """
    )

    start = source.position(6, 4)
    end = source.position(6, 7)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "text = a.source.lines[a.row].text" in insert.text


def test_inline_call_should_substitute_keyword_arguments():
    source = make_source(
        """
        def f(c):
            c += 1
            return c

        a = 2
        b = f(c=a)
        """
    )

    start = source.position(6, 4)
    end = source.position(6, 7)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "a += 1\nresult = a" in insert.text


def test_inline_call_should_indent_in_new_context():
    source = make_source(
        """
        def f():
            a = 2
            return a

        def g():
            b = f()
        """
    )

    start = source.position(6, 8)
    end = source.position(6, 10)
    refactor = Refactor(TextRange(start, end))
    insert, edit = refactor.inline_call(name="result")

    assert "    a = 2\n    result = a" in insert.text


def test_inline_variable_should_replace_variable_with_expression():
    source = make_source(
        """
        b = f()
        print(b)
        """
    )

    start = source.position(1, 0)
    refactor = Refactor(TextRange(start, start))
    *_, edit = refactor.inline_variable()

    assert "f()" == edit.text
    assert edit.start == source.position(2, 6)
    assert edit.end == source.position(2, 6)


def test_inline_variable_should_delete_definition():
    source = make_source(
        """
        b = f(
            2
        )
        print(b)
        """
    )

    start = source.position(1, 0)
    refactor = Refactor(TextRange(start, start))
    delete, *_edit = refactor.inline_variable()

    assert "" == delete.text
    assert delete.start == source.position(1, 0)
    assert delete.end == source.position(4, 0)


def test_extract_function_should_pass_variables_used_as_arguments_on_as_parameters():
    source = make_source(
        """
        def f():
            return 2

        def g(f):
            return abs(f())

        def h():
            return g(f=f)
        """
    )

    start = source.position(8, 4)
    end = source.position(8, 16)
    refactor = Refactor(TextRange(start, end))
    insert, _ = refactor.extract_function(name="function")
    assert "def function(f):" in insert.text


def test_refactor_inside_method_is_true_for_range_inside_method():
    source = make_source(
        """
        class C:
            def f(self, a: list[int]) -> tuple[str, ...]:
                a += 1
                return max([a])
        """
    )

    start = source.position(3, 0)
    end = source.position(4, 0)

    refactor = Refactor(TextRange(start, end))
    assert refactor.inside_method


@pytest.mark.xfail
def test_extract_variable_should_extract_within_for_loop():
    source = make_source(
        """
        for position in all_occurrence_positions(arg_position):
            if position not in body_range:
                continue
            yield TextRange(position, position + len(def_arg.arg)), value
        """
    )
    extraction_start = source.position(4, 10)
    extraction_end = source.position(4, 58)

    refactor = Refactor(TextRange(extraction_start, extraction_end))
    insert, *_ = refactor.extract_variable(name="result")

    assert insert.text_range.start.row == 4
    assert insert.text_range.start.column == 4


def test_inline_variable_should_only_inline_after_definition():
    source = make_source(
        """
        extracted = text_range.text.strip()
        extracted = f"{new_indentation}return {extracted}"
        assignment = ""

        return extracted, assignment
        """
    )

    start = source.position(2, 0)
    refactor = Refactor(TextRange(start, start))
    insert, *_edits = refactor.inline_variable()
    cut_off = source.position(3, 0)
    assert all(e.text_range.start > cut_off for e in _edits)
