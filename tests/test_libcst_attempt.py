from collections import defaultdict
from typing import Dict, List

from libcst import CSTVisitor, MetadataWrapper, Name, parse_module
from libcst.metadata import (
    CodeRange,
    PositionProvider,
    QualifiedNameProvider,
    ScopeProvider,
)
from pytest import mark

from breakfast.source import Source
from tests import make_source


class NameCollector(CSTVisitor):  # type: ignore

    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider, ScopeProvider)

    def __init__(self, line: int, column: int):
        self.names: Dict[str, List[CodeRange]] = defaultdict(list)
        self.found: List[CodeRange] = []
        self.looking_for = (line, column)

    def visit_Name(self, node: Name):  # pylint: disable=invalid-name
        metadata = self.get_metadata(QualifiedNameProvider, node)
        code_range = self.get_metadata(PositionProvider, node)
        for datum in metadata:
            self.names[datum.name].append(code_range)
            if self.looking_for == (code_range.start.line - 1, code_range.start.column):
                self.found = self.names[datum.name]
        return True


def rename(source: Source, *, row: int, column: int, new_name: str) -> str:
    position = source.position(row=row, column=column)
    parsed = parse_module("\n".join(source.lines))
    wrapper = MetadataWrapper(parsed)
    collector = NameCollector(position.row, position.column)
    wrapper.visit(collector)
    for code_range in collector.found:
        start = source.position(code_range.start.line - 1, code_range.start.column)
        end = source.position(code_range.end.line - 1, code_range.end.column)
        source.modify_line(start, end, new_name)
    return source.render()


def assert_renames(
    old_source: str, new_source: str, *, row: int, column: int, new_name: str = "new"
) -> None:
    source = make_source(old_source)

    assert rename(source, row=row, column=column, new_name=new_name) == "\n".join(
        make_source(new_source).lines
    )


def test_distinguishes_local_variables_from_global():
    assert_renames(
        """
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """,
        """
        def fun():
            new = 12
            old2 = 13
            result = new + old2
            del new
            return result

        old = 20
        """,
        row=2,
        column=4,
    )


def test_finds_non_local_variable():
    assert_renames(
        """
        old = 12

        def fun():
            result = old + 1
            return result

        old = 20
        """,
        """
        new = 12

        def fun():
            result = new + 1
            return result

        new = 20
        """,
        row=1,
        column=0,
    )


def test_does_not_rename_random_attributes():
    assert_renames(
        """
        import os

        path = os.path.dirname(__file__)
        """,
        """
        import os

        new = os.path.dirname(__file__)
        """,
        row=3,
        column=0,
    )


@mark.xfail()
def test_finds_parameter():
    assert_renames(
        """
        def fun(old=1):
            print(old)

        old = 8
        fun(old=old)
        """,
        """
        def fun(new=1):
            print(new)

        old = 8
        fun(new=old)
        """,
        row=1,
        column=8,
    )
