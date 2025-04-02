from pytest import mark

from breakfast.refactoring import (
    CodeSelection,
    Edit,
    ExtractFunction,
    ExtractMethod,
    ExtractVariable,
    InlineCall,
    InlineVariable,
    SlideStatementsDown,
    SlideStatementsUp,
)
from breakfast.source import Source, TextRange
from tests.conftest import assert_refactors_to, dedent, make_source


def test_extract_variable_should_insert_name_definition():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, *_ = refactor.edits
    assert insert.text == "v = a + 3\n"


def test_extract_variable_should_replace_extracted_test_with_result():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    _, replace = refactor.edits
    assert replace == Edit(
        TextRange(start=extraction_start, end=extraction_end), text="v"
    )


def test_extract_variable_should_insert_name_definition_before_extraction_point():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 9)

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, *_ = refactor.edits

    assert insert.start < extraction_start


def test_extract_variable_should_replace_code_with_variable():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 22)
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    edits = refactor.edits

    assert edits == (
        Edit(
            TextRange(source.position(1, 0), source.position(1, 0)),
            "v = some_calculation()\n",
        ),
        Edit(TextRange(extraction_start, extraction_end), "v"),
    )


def test_extract_variable_will_not_extract_partial_expression():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 21)
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    edits = refactor.edits
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
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, _ = refactor.edits
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
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, _ = refactor.edits
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
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, *_ = refactor.edits
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

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    _, *edits = refactor.edits

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

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, *edits = refactor.edits

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
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    _insert, *edits = refactor.edits

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
    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    _insert, *edits = refactor.edits

    assert len(edits) == 1


def test_extract_function_should_insert_function_definition():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="abs(value + 8)",
        code="""
        value = 0
        something = abs(value + 8)
        """,
        expected="""
        value = 0

        def f(value):
            something = abs(value + 8)
            return something

        something = f(value=value)
        """,
    )


def test_extract_function_should_insert_function_definition_with_multiple_statements():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target=("print(value + 20)", "print(max(value, 0))"),
        code="""
        value = 0
        print(value + 20)
        print(max(value, 0))
        """,
        expected="""
        value = 0

        def f(value):
            print(value + 20)
            print(max(value, 0))

        f(value=value)
        """,
    )


def test_extract_function_should_create_arguments_for_local_variables():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target=("print(value + 20)", "print(max(other_value, value))"),
        code="""
        value = 0
        other_value = 1
        print(value + 20)
        print(max(other_value, value))
        """,
        expected="""
        value = 0
        other_value = 1

        def f(value, other_value):
            print(value + 20)
            print(max(other_value, value))

        f(value=value, other_value=other_value)
        """,
    )


def test_extract_function_should_return_modified_variable_used_after_call():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="b = a + 2",
        code="""
        a = 1
        b = a + 2
        print(b)
        """,
        expected="""
        a = 1

        def f(a):
            b = a + 2
            return b

        b = f(a=a)
        print(b)
        """,
    )


def test_extract_function_should_extract_outside_function():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="a + 2",
        code="""
        def f():
            a = 1
            b = a + 2
            print(b)
        """,
        expected="""
        def f():
            a = 1
            b = f0(a=a)
            print(b)

        def f0(a):
            b = a + 2
            return b
        """,
    )


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
    refactor = ExtractFunction(CodeSelection(TextRange(start, end)))
    insert, *_edits = refactor.edits

    assert insert.start == source.position(5, 0)


def test_extract_function_should_handle_indented_arguments_of_enclosing_scope():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="b = a + 2",
        code="""
        def function(
            i,
            j,
        ):
            a = 1
            b = a + 2
            print(b)
        """,
        expected="""
        def function(
            i,
            j,
        ):
            a = 1
            b = f(a=a)
            print(b)

        def f(a):
            b = a + 2
            return b
        """,
    )


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

    refactor = ExtractFunction(CodeSelection(TextRange(start, end)))
    insert, replace = refactor.edits

    assert "f(a)" in insert.text


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

    refactor = ExtractFunction(CodeSelection(TextRange(start, end)))
    insert, replace = refactor.edits

    assert "f()" in insert.text


def test_extract_function_should_replace_extracted_code_with_function_call():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="b = a + 2",
        code="""
        def function():
            a = 1
            b = a + 2
            print(b)
        """,
        expected="""
        def function():
            a = 1
            b = f(a=a)
            print(b)

        def f(a):
            b = a + 2
            return b
        """,
    )


def test_extract_function_should_return_multiple_values_where_necessary():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target=("a = 1", "b = a + 2"),
        code="""
        a = 1
        b = a + 2

        print(a)
        print(b)
        """,
        expected="""
        def f():
            a = 1
            b = a + 2
            return a, b

        a, b = f()
        print(a)
        print(b)
        """,
    )


def test_extract_function_should_handle_empty_lines():
    code = """\
b = 1

def function(a):

    b = a + 2
    print(b)"""
    source = Source(
        input_lines=tuple(line for line in code.split("\n")),
        path="",
        project_root=".",
    )
    start = source.position(4, 0)
    end = source.position(4, 12)
    refactor = ExtractFunction(CodeSelection(TextRange(start, end)))
    insert, replace = refactor.edits

    assert "def f(a):" in insert.text
    assert "b = f(a=a)" in replace.text


def test_extract_method_should_replace_extracted_code_with_method_call():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="self.b = a + 2",
        code="""
        class A:
            def f(self):
                a = 1
                self.b = a + 2
                print(self.b)
        """,
        expected="""
        class A:
            def f(self):
                a = 1
                self.m(a=a)
                print(self.b)

            def m(self, a):
                self.b = a + 2
        """,
    )


def test_extract_method_should_handle_multitarget_assignment():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="self.b = c = a + 2",
        code="""
        class A:
            def f(self):
                a = 1
                self.b = c = a + 2
                print(c)
        """,
        expected="""
        class A:
            def f(self):
                a = 1
                c = self.m(a=a)
                print(c)

            def m(self, a):
                self.b = c = a + 2
                return c
        """,
    )


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

    refactor = ExtractMethod(CodeSelection(TextRange(start, end)))
    insert, _replace = refactor.edits

    assert insert.start.row == 6


def test_extract_method_should_not_repeat_return_variables():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target=("start = ", "else end"),
        code="""
        class A:
            def extract_method(self, name: str) -> tuple[Edit, ...]:
                start = self.text_range.start
                end = self.text_range.end
                if start.row < end.row:
                    start = start.start_of_line
                    end = end.line.next.start if end.line.next else end

                print(start, end)
        """,
        expected="""
        class A:
            def extract_method(self, name: str) -> tuple[Edit, ...]:
                start, end = self.m()

                print(start, end)

            def m(self):
                start = self.text_range.start
                end = self.text_range.end
                if start.row < end.row:
                    start = start.start_of_line
                    end = end.line.next.start if end.line.next else end
                return start, end
        """,
    )


def test_extract_method_should_extract_static_method_when_self_not_used():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="text = start.through(end).text",
        code="""
        class C:
            def m1(self):
                start, end = self.extended_range
                text = start.through(end).text
                print(text)
        """,
        expected="""
        class C:
            def m1(self):
                start, end = self.extended_range
                text = self.m(start=start, end=end)
                print(text)

            @staticmethod
            def m(start, end):
                text = start.through(end).text
                return text
        """,
    )


def test_extract_method_should_extract_class_method_from_class_method():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="sum = cls.start + end",
        code="""
        class C:
            @classmethod
            def m1(cls, end):
                sum = cls.start + end
                print(sum)
        """,
        expected="""
        class C:
            @classmethod
            def m1(cls, end):
                sum = cls.m(end=end)
                print(sum)

            @classmethod
            def m(cls, end):
                sum = cls.start + end
                return sum
        """,
    )


def test_slide_statements_should_not_slide_beyond_first_usage():
    source = make_source(
        """
        value = 0
        print(value + 20)
        """
    )

    first = source.lines[1]
    last = source.lines[1]

    refactor = SlideStatementsDown(
        CodeSelection(TextRange(first.start, last.end))
    )
    edits = refactor.edits

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

    refactor = SlideStatementsDown(
        CodeSelection(TextRange(first.start, last.end))
    )
    edits = refactor.edits
    assert not edits


def test_slide_statements_should_not_slide_inside_if_else():
    source = make_source(
        """
        def update_item(item):
            is_backstage_passes = item.name == "bac"
            is_sulfuras = item.name == "sul"
            if is_sulfuras:
                return
            is_aged_brie = item.name == "age"
            if is_aged_brie:
                ...
            elif is_backstage_passes:
                ...
            else:
                ...
        """
    )

    first = source.lines[2]
    last = source.lines[2]

    refactor = SlideStatementsDown(
        CodeSelection(TextRange(first.start, last.end))
    )
    edits = refactor.edits
    assert edits[0].start.row == 7


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

    refactor = SlideStatementsDown(
        CodeSelection(TextRange(first.start, last.end))
    )
    insert, delete = refactor.edits

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

    refactor = SlideStatementsDown(
        CodeSelection(TextRange(first.start, last.end))
    )
    insert, delete = refactor.edits

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

    refactor = SlideStatementsUp(
        CodeSelection(TextRange(first.start, last.end))
    )
    insert, delete = refactor.edits

    assert insert.start.row == 2
    assert delete.start.row == 3


def test_extract_function_should_extract_to_global_scope():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="a += 1",
        code="""
        class C:
            @staticmethod
            def m():
                a = 1
                a += 1
                return a
        """,
        expected="""

        class C:
            @staticmethod
            def m():
                a = 1
                a = f(a=a)
                return a

        def f(a):
            a += 1
            return a
        """,
    )


def test_extract_function_should_consider_function_scope():
    source = make_source(
        """
        def function(p):
            if True:
                d = c(p)
                return d
        """
    )

    start = source.position(3, 0)
    end = source.position(4, 0)
    refactor = ExtractFunction(CodeSelection(TextRange(start, end)))
    insert, _ = refactor.edits

    assert "def f(p):" in insert.text


def test_extract_method_should_extract_part_of_a_line():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="self.text_range.start + 2",
        code="""
        class C:
            def inline_call(self) -> tuple[Edit, ...]:
                range_end = self.text_range.start + 2
        """,
        expected="""
        class C:
            def inline_call(self) -> tuple[Edit, ...]:
                range_end = self.m()

            def m(self):
                range_end = self.text_range.start + 2
                return range_end
        """,
    )


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
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    insert, edit = refactor.edits

    assert "result = 2" in insert.text
    assert "result" == edit.text
    assert edit.start == start
    assert edit.end == end


def test_inline_call_should_work_without_return_value():
    source = make_source(
        """
        def f(l):
            l.append(2)

        b = []
        f(b)
        """
    )
    start = source.position(5, 0)
    end = source.position(5, 0)
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    edit, *_ = refactor.edits

    assert "b.append(2)" in edit.text
    assert edit.start == start
    assert edit.end == source.position(5, 4)


def test_inline_call_should_work_when_given_position_within_called_name():
    source = make_source(
        """
        def function(l):
            l.append(2)

        b = []
        function(b)
        """
    )
    start = source.position(5, 3)
    end = source.position(5, 3)
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    edit, *_ = refactor.edits

    assert "b.append(2)" in edit.text
    assert edit.start == source.position(5, 0)
    assert edit.end == source.position(5, 11)


def test_inline_call_should_work_inside_branches():
    source = make_source(
        """
        def f(a):
            if a:
                print("true")
            else:
                print("false")

        if a:
            f(True)
        else:
            f(False)
        """
    )
    start = source.position(8, 4)
    end = source.position(8, 4)
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    edit, *_ = refactor.edits
    assert edit.start == source.position(8, 4)
    assert edit.end == source.position(8, 11)


def test_inline_call_should_work_when_cursor_is_in_call():
    source = make_source(
        """
        def f():
            return 2

        b = f()
        """
    )

    start = source.position(4, 4)
    refactor = InlineCall(CodeSelection(TextRange(start, start)))
    insert, edit = refactor.edits

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
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    insert, edit = refactor.edits

    assert "a = 2\nresult = a" in insert.text


def test_inline_call_should_substitute_parameters():
    assert_refactors_to(
        refactoring=InlineCall,
        target="f",
        occurrence=2,
        code="""
        def f(c):
            c += 1
            return c

        a = 2
        b = f(a)
        """,
        expected="""
        def f(c):
            c += 1
            return c

        a = 2
        a += 1
        result = a
        b = result
        """,
    )


def test_inline_call_should_substitute_parameters_in_attribute():
    assert_refactors_to(
        refactoring=InlineCall,
        target="f",
        occurrence=2,
        code="""
        def f(at):
            text = at.source.lines[at.row].text
            return text

        a = 2
        b = f(at=a)
        """,
        expected="""
        def f(at):
            text = at.source.lines[at.row].text
            return text

        a = 2
        text = a.source.lines[a.row].text
        result = text
        b = result
        """,
    )


def test_inline_call_should_substitute_keyword_arguments():
    assert_refactors_to(
        refactoring=InlineCall,
        target="f",
        occurrence=2,
        code="""
        def f(c):
            c += 1
            return c

        a = 2
        b = f(c=a)
        """,
        expected="""
        def f(c):
            c += 1
            return c

        a = 2
        a += 1
        result = a
        b = result
        """,
    )


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
    refactor = InlineCall(CodeSelection(TextRange(start, end)))
    insert, edit = refactor.edits

    assert "    a = 2\n    result = a" in insert.text


def test_inline_variable_should_replace_variable_with_expression():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="b",
        occurrence=1,
        code="""
        b = f()
        print(b)
        """,
        expected="""
        print(f())
        """,
    )


def test_inline_variable_should_delete_multiline_definition():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="b",
        occurrence=1,
        code="""
        b = f(
            2
        )
        print(b)
        """,
        expected="""
        print(f(2))
        """,
    )


def test_extract_function_should_pass_variables_used_as_arguments_on_as_parameters():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="g(c=function)",
        code="""
        def function():
            return 2

        def g(c):
            return abs(c())

        def h():
            return g(c=function)
        """,
        expected="""
        def function():
            return 2

        def g(c):
            return abs(c())

        def h():
            return f()

        def f():
            return g(c=function)
        """,
    )


def test_extract_variable_should_include_quotes():
    source = make_source(
        """
        item = 'wat'
        if (
            item.name != "AGED_BRIE"
            and item.name != "foo"
        ):
            if item.quality > 0:
                if item.name != "Sulfuras, Hand of Ragnaros":
                    item.quality = item.quality - 1
        else:
            if item.quality < 50:
                item.quality = item.quality + 1
                if item.name == "foo":
                    ...
        """
    )

    start = source.position(4, 21)
    end = source.position(4, 26)

    refactor = ExtractVariable(CodeSelection(TextRange(start, end)))
    edits = refactor.edits
    for edit in edits[1:]:
        assert edit.text_range.text == '"foo"'


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

    refactor = ExtractVariable(
        CodeSelection(TextRange(extraction_start, extraction_end))
    )
    insert, *_ = refactor.edits

    assert insert.text_range.start.row == 4
    assert insert.text_range.start.column == 4


def test_inline_variable_should_only_inline_after_definition():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="extracted",
        occurrence=2,
        code="""
        extracted = text_range.text.strip()
        extracted = f"{new_indentation}return {extracted}"
        assignment = ""

        return extracted, assignment
        """,
        expected="""
        extracted = text_range.text.strip()
        assignment = ""

        return f"{new_indentation}return {extracted}", assignment
        """,
    )


def test_inline_variable_should_not_remove_multi_target_assignment():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="extracted",
        occurrence=2,
        code="""
        foo = extracted = text_range.text.strip()
        print(foo)
        return extracted, assignment
        """,
        expected="""
        foo = text_range.text.strip()
        print(foo)
        return text_range.text.strip(), assignment
        """,
    )


def test_inline_variable_should_remove_unused_definition():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="extracted",
        occurrence=2,
        code="""
        extracted = text_range.text.strip()
        return extracted, assignment
        """,
        expected="""
        return text_range.text.strip(), assignment
        """,
    )


def test_inline_variable_should_not_remove_used_definition():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="extracted",
        occurrence=3,
        code="""
        extracted = text_range.text.strip()
        print(extracted)
        return extracted, assignment
        """,
        expected="""
        extracted = text_range.text.strip()
        print(extracted)
        return text_range.text.strip(), assignment
        """,
    )


def test_inline_variable_should_not_remove_use_after_refactor():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="extracted",
        occurrence=2,
        code="""
        extracted = text_range.text.strip()
        print(extracted)
        return extracted, assignment
        """,
        expected="""
        extracted = text_range.text.strip()
        print(text_range.text.strip())
        return extracted, assignment
        """,
    )


def test_inline_variable_should_inline_twice_from_definition():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="start",
        occurrence=1,
        code="""
        start = source.position(1, 0)
        refactor = Refactor(TextRange(start, start))
        """,
        expected="""
        refactor = Refactor(TextRange(source.position(1, 0), source.position(1, 0)))
        """,
    )


def test_inline_variable_should_inline_once_from_first_usage():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="start",
        occurrence=2,
        code="""
        start = source.position(1, 0)
        refactor = Refactor(TextRange(start, start))
        """,
        expected="""
        start = source.position(1, 0)
        refactor = Refactor(TextRange(source.position(1, 0), start))
        """,
    )


def test_inline_variable_should_inline_once_from_second_usage():
    assert_refactors_to(
        refactoring=InlineVariable,
        target="start",
        occurrence=3,
        code="""
        start = source.position(1, 0)
        refactor = Refactor(TextRange(start, start))
        """,
        expected="""
        start = source.position(1, 0)
        refactor = Refactor(TextRange(start, source.position(1, 0)))
        """,
    )


def test_inline_call_should_inline_method_call():
    source = make_source(
        """
        class C:

            def containing_scopes(self):
                nodes = self.containing_nodes_by_type()
                return nodes

            def containing_nodes_by_type(self):
                return self
        """
    )

    start = source.position(4, 23)
    selection = CodeSelection(TextRange(start, start))

    refactoring = InlineCall(selection)
    insert, edit = refactoring.edits

    assert "result = self" in insert.text
    assert "result" == edit.text


def test_extract_callable_containing_return_statement_should_preserve_it():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target=("range_end = 3 + 2", "return range_end"),
        code="""
        def function():
            range_end = 3 + 2
            return range_end
        """,
        expected="""
        def function():
            return f()

        def f():
            range_end = 3 + 2
            return range_end
        """,
    )


def test_inline_callable_should_handle_multiline_return():
    assert_refactors_to(
        refactoring=InlineCall,
        target="f2",
        code="""
        def f1():
            return f2()


        def f2():
            print("a")
            return (
                1,
            )
        """,
        expected="""
        def f1():
            print("a")
            result = (1,)
            return result

        def f2():
            print("a")
            return (
                1,
            )
        """,
    )


def test_inline_callable_should_handle_multiple_returns():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        b = function(False)
        """,
        expected="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        result = 2
        b = result
        """,
    )


def test_inline_callable_should_eliminate_contradictions():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        b = function(False)
        """,
        expected="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        result = 2
        b = result
        """,
    )


def test_inline_callable_should_eliminate_tautologies():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        b = function(True)
        """,
        expected="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        result = 1
        b = result
        """,
    )


def test_inline_callable_should_not_eliminate_used_function():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        b = function(True)
        c = function(False)
        """,
        expected="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        result = 1
        b = result
        c = function(False)
        """,
    )


@mark.xfail
def test_inline_callable_should_eliminate_unused_function():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            if a is True:
                return 1
            else:
                return 2

        b = function(True)
        """,
        expected="""
        result = 1
        b = result
        """,
    )


def test_inline_callable_should_keep_arg_that_is_modified():
    assert_refactors_to(
        refactoring=InlineCall,
        target="function",
        occurrence=2,
        code="""
        def function(a):
            while a is True:
                a = False

            return a or True

        b = function(True)
        """,
        expected="""
        def function(a):
            while a is True:
                a = False

            return a or True

        while a is True:
            a = False

        result = a or True
        b = result
        """,
    )


def test_extract_variable_should_not_use_existing_name():
    assert_refactors_to(
        refactoring=ExtractVariable,
        target="a + 2",
        code="""
        a = 1
        v = 2
        b = a + 2

        print(b, v)
        """,
        expected="""
        a = 1
        v = 2
        v0 = a + 2
        b = v0

        print(b, v)
        """,
    )


def test_extract_function_should_not_use_existing_name():
    assert_refactors_to(
        refactoring=ExtractFunction,
        target="a + 2",
        code="""
        a = 1
        f = 2
        b = a + 2

        print(b, f)
        """,
        expected="""
        a = 1
        f = 2
        def f0(a):
           b = a + 2
           return b

        b = f0(a=a)
        print(b, f)
        """,
    )


def test_extract_method_should_not_use_existing_name():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="self.a + 2",
        code="""
        class C:
            def m(self):
                self.a = 1
                f = 2
                b = self.a + 2

                print(b, f)

            def m0(self):
                pass
        """,
        expected="""
        class C:
            def m(self):
                self.a = 1
                f = 2
                b = self.m1()

                print(b, f)

            def m1(self):
                b = self.a + 2
                return b

            def m0(self):
                pass
        """,
    )


@mark.xfail
def test_extract_method_should_extract_from_for_loop():
    assert_refactors_to(
        refactoring=ExtractMethod,
        target="            print(self, i)",
        code="""
        class C:
            def method(self):
                for i in range(10):
                    print(self, i)
        """,
        expected="""
        class C:
            def method(self):
                for i in range(10):
                    self.m(i)

            def m(self, i):
                print(self, i)
        """,
    )
