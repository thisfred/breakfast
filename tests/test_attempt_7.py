"""
SAX style events again.
"""

import ast

from dataclasses import dataclass
from functools import singledispatch
from typing import Iterator, List

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


class Event:
    pass


@dataclass
class Occurrence(Event):
    name: str
    position: Position
    node: ast.AST


@dataclass
class EnterScope(Event):
    node: ast.AST


@dataclass
class LeaveScope(Event):
    node: ast.AST


def node_position(
    node: ast.AST, source: Source, row_offset=0, column_offset=0
) -> Position:
    return Position(
        source=source,
        row=(node.lineno - 1) + row_offset,
        column=node.col_offset + column_offset,
    )


@singledispatch
def visit(node: ast.AST, source: Source) -> Iterator[Event]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    yield from generic_visit(node, source)


@visit.register
def visit_name(node: ast.Name, source: Source) -> Iterator[Event]:
    yield Occurrence(name=node.id, position=node_position(node, source), node=node)


def generic_visit(node, source: Source) -> Iterator[Event]:
    """Called if no explicit visitor function exists for a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, source)
        elif isinstance(value, ast.AST):
            yield from visit(value, source)


def test_finds_all_occurrences_of_function_local():
    source = make_source(
        """
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """
    )

    def get_occurrences(source: Source) -> List[Occurrence]:
        initial_node = source.get_ast()
        return [
            event
            for event in visit(initial_node, source=source)
            if isinstance(event, Occurrence)
        ]

    assert len(get_occurrences(source)) == 8
