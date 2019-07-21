from typing import TYPE_CHECKING, List

import pytest

from breakfast.occurrences import find_occurrences
from breakfast.position import Position
from tests import make_source


if TYPE_CHECKING:
    from breakfast.source import Source  # noqa: F401


def rename(
    *, sources: List["Source"], old_name: str, new_name: str, position: Position
) -> List["Source"]:
    for occurrence in find_occurrences(
        sources=sources, old_name=old_name, position=position
    ):
        occurrence.source.replace(occurrence, old_name, new_name)
    return sources


def assert_renames(
    *,
    row: int,
    column: int,
    old_name: str,
    old_source: str,
    new_name: str,
    new_source: str
) -> None:
    source = make_source(old_source)
    renamed = rename(
        sources=[source],
        old_name=old_name,
        new_name=new_name,
        position=Position(source, row, column),
    )
    assert make_source(new_source).render() == renamed[0].render()


def assert_renames_multi_source(
    position: Position,
    old_name: str,
    old_sources: List["Source"],
    new_name: str,
    new_sources: List[str],
) -> None:
    renamed = rename(
        sources=old_sources, old_name=old_name, new_name=new_name, position=position
    )
    for actual, expected in zip(renamed, new_sources):
        assert make_source(expected).render() == actual.render()


def test_does_not_rename_random_attributes() -> None:
    assert_renames(
        row=3,
        column=0,
        old_name="path",
        old_source="""
        import os

        path = os.path.dirname(__file__)
        """,
        new_name="new_name",
        new_source="""
        import os

        new_name = os.path.dirname(__file__)
        """,
    )


def test_finds_local_variable() -> None:
    assert_renames(
        row=2,
        column=4,
        old_name="old",
        old_source="""
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """,
        new_name="new",
        new_source="""
        def fun():
            new = 12
            old2 = 13
            result = new + old2
            del new
            return result

        old = 20
        """,
    )


def test_finds_variable_in_closure() -> None:
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 12

        def fun():
            result = old + 1
            return result

        old = 20
        """,
        new_name="new",
        new_source="""
        new = 12

        def fun():
            result = new + 1
            return result

        new = 20
        """,
    )


def test_finds_method_names() -> None:
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        old_source="""
        class A:

            def old(self):
                pass

        unbound = A.old
        """,
        new_name="new",
        new_source="""
        class A:

            def new(self):
                pass

        unbound = A.new
        """,
    )


def test_finds_parameters() -> None:
    assert_renames(
        row=1,
        column=8,
        old_name="arg",
        old_source="""
        def fun(arg, arg2):
            return arg + arg2

        fun(arg=1, arg2=2)
        """,
        new_name="new",
        new_source="""
        def fun(new, arg2):
            return new + arg2

        fun(new=1, arg2=2)
        """,
    )


def test_finds_function() -> None:
    assert_renames(
        row=1,
        column=4,
        old_name="fun_old",
        old_source="""
        def fun_old():
            return 'result'

        result = fun_old()
        """,
        new_name="fun_new",
        new_source="""
        def fun_new():
            return 'result'

        result = fun_new()
        """,
    )


def test_finds_class() -> None:
    assert_renames(
        row=1,
        column=6,
        old_name="OldClass",
        old_source="""
        class OldClass:
            pass

        instance = OldClass()
        """,
        new_name="NewClass",
        new_source="""
        class NewClass:
            pass

        instance = NewClass()
        """,
    )


def test_finds_passed_argument() -> None:
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, old)
        """,
        new_name="new",
        new_source="""
        new = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, new)
        """,
    )


def test_does_not_find_method_of_unrelated_class() -> None:
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        new_name="new",
        old_source="""
        class ClassThatShouldHaveMethodRenamed:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.old()
        b = UnrelatedClass()
        b.old()
        """,
        new_source="""
        class ClassThatShouldHaveMethodRenamed:

            def new(self, arg):
                pass

            def foo(self):
                self.new('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.new()
        b = UnrelatedClass()
        b.old()
        """,
    )


def test_finds_static_method() -> None:
    assert_renames(
        row=4,
        column=8,
        old_name="old",
        old_source="""
        class A:

            @staticmethod
            def old(arg):
                pass

        a = A()
        a.old('foo')
        """,
        new_name="new",
        new_source="""
        class A:

            @staticmethod
            def new(arg):
                pass

        a = A()
        a.new('foo')
        """,
    )


def test_finds_argument() -> None:
    assert_renames(
        row=8,
        column=17,
        old_name="arg",
        old_source="""
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """,
        new_name="new_arg",
        new_source="""
        class A:

            def foo(self, new_arg):
                print(new_arg)

            def bar(self):
                arg = "1"
                self.foo(new_arg=arg)
        """,
    )


def test_finds_method_but_not_function() -> None:
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        old_source="""
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """,
        new_name="new",
        new_source="""
        class A:

            def new(self):
                pass

            def foo(self):
                self.new()

            def bar(self):
                old()

        def old():
            pass
        """,
    )


def test_finds_definition_from_call() -> None:
    assert_renames(
        row=5,
        column=4,
        old_name="old",
        old_source="""
        def old():
            pass

        def bar():
            old()
        """,
        new_name="new",
        new_source="""
        def new():
            pass

        def bar():
            new()
        """,
    )


def test_finds_attribute_assignments() -> None:
    assert_renames(
        row=7,
        column=20,
        old_name="property",
        old_source="""
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """,
        new_name="new_property",
        new_source="""
        class ClassName:

            def __init__(self, property):
                self.new_property = property

            def get_property(self):
                return self.new_property
        """,
    )


def test_finds_dict_comprehension_variables() -> None:
    assert_renames(
        row=2,
        column=42,
        old_name="old",
        old_source="""
        old = 100
        foo = {old: None for old in range(100) if old % 3}
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = {new: None for new in range(100) if new % 3}
        """,
    )


def test_finds_list_comprehension_variables() -> None:
    assert_renames(
        row=3,
        column=12,
        old_name="old",
        old_source="""
        old = 100
        foo = [
            old for old in range(100) if old % 3]
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = [
            new for new in range(100) if new % 3]
        """,
    )


def test_finds_set_comprehension_variables() -> None:
    assert_renames(
        row=2,
        column=7,
        old_name="old",
        old_source="""
        old = 100
        foo = {old for old in range(100) if old % 3}
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = {new for new in range(100) if new % 3}
        """,
    )


def test_finds_for_loop_variables() -> None:
    # Note that we could have chosen to treat the top level 'old' variable as
    # distinct from the loop variable, but since loop variables live on after
    # the loop, that would potentially change the behavior of the code.
    assert_renames(
        row=2,
        column=7,
        old_name="old",
        old_source="""
        old = None
        for i, old in enumerate(['foo']):
            print(i)
            print(old)
        print(old)
        """,
        new_name="new",
        new_source="""
        new = None
        for i, new in enumerate(['foo']):
            print(i)
            print(new)
        print(new)
        """,
    )


def test_finds_enclosing_scope_variable_from_comprehension() -> None:
    assert_renames(
        row=2,
        column=42,
        old_name="old",
        old_source="""
        old = 3
        res = [foo for foo in range(100) if foo % old]
        """,
        new_name="new",
        new_source="""
        new = 3
        res = [foo for foo in range(100) if foo % new]
        """,
    )


def test_finds_tuple_unpack() -> None:
    assert_renames(
        row=1,
        column=5,
        old_name="old",
        old_source="""
        foo, old = 1, 2
        print(old)
        """,
        new_name="new",
        new_source="""
        foo, new = 1, 2
        print(new)
        """,
    )


def test_recognizes_multiple_assignments() -> None:
    assert_renames(
        row=2,
        column=8,
        old_name="old",
        old_source="""
        class A:
            def old(self):
                pass

        class B:
            def old(self):
                pass

        foo, bar = A(), B()
        foo.old()
        bar.old()
        """,
        new_name="new",
        new_source="""
        class A:
            def new(self):
                pass

        class B:
            def old(self):
                pass

        foo, bar = A(), B()
        foo.new()
        bar.old()
        """,
    )


def test_finds_across_sources() -> None:
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def new():
                pass
            """,
            """
            from foo import new
            new()
            """,
        ],
    )


def test_finds_multiple_imports_on_one_line() -> None:
    source1 = make_source(
        """
        def old():
            pass

        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar, old
        old()
        bar()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def new():
                pass

            def bar():
                pass
            """,
            """
            from foo import bar, new
            new()
            bar()
            """,
        ],
    )


def test_finds_calls_in_the_middle_of_an_attribute_chain() -> None:
    assert_renames(
        row=5,
        column=8,
        old_name="old",
        old_source="""
        class Bar:
            baz = 'whatever'

        class Foo:
            def old():
                return Bar()

        foo = Foo()
        result = foo.old().baz
        """,
        new_name="new",
        new_source="""
        class Bar:
            baz = 'whatever'

        class Foo:
            def new():
                return Bar()

        foo = Foo()
        result = foo.new().baz
        """,
    )


def test_finds_renamed_imports() -> None:
    source1 = make_source(
        """
        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar as old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def bar():
                pass
            """,
            """
            from foo import bar as new
            new()
            """,
        ],
    )


def test_finds_properties_of_renamed_imports() -> None:
    source1 = make_source(
        """
        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar as old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def bar():
                pass
            """,
            """
            from foo import bar as new
            new()
            """,
        ],
    )


def test_finds_default_value() -> None:
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 2

        def fun(arg=old):
            old = 1
            return arg + old
        """,
        new_name="new",
        new_source="""
        new = 2

        def fun(arg=new):
            old = 1
            return arg + old
        """,
    )


@pytest.mark.skip
def test_finds_name_defined_after_usage1() -> None:
    assert_renames(
        row=4,
        column=4,
        old_name="old",
        old_source="""
        def foo():
            old()

        def old():
            pass
        """,
        new_name="new",
        new_source="""
        def foo():
            new()

        def new():
            pass
        """,
    )


@pytest.mark.skip
def test_finds_name_defined_after_usage2() -> None:
    assert_renames(
        row=2,
        column=4,
        old_name="old",
        old_source="""
        def foo():
            old()


        def old():
            pass
        """,
        new_name="new",
        new_source="""
        def foo():
            new()


        def new():
            pass
        """,
    )
