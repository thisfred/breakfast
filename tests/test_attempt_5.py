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
