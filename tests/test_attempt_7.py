"""
SAX style events again.
"""

import ast

from collections import ChainMap
from dataclasses import dataclass
from functools import singledispatch
from typing import ChainMap as CM
from typing import Iterator, List, Union

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


class Event:
    def apply(  # pylint: disable=no-self-use
        self, scopes: "Scopes"  # pylint: disable=unused-argument
    ) -> None:
        ...


@dataclass
class Occurrence(Event):
    name: str
    position: Position
    node: ast.AST

    def apply(self, scopes: "Scopes") -> None:
        scopes.add_to_scope(self)


@dataclass
class EnterScope(Event):
    node: ast.AST

    @staticmethod
    def apply(scopes: "Scopes") -> None:
        scopes.enter_new_scope()


@dataclass
class EnterAttributeScope(Event):
    node: ast.AST

    @staticmethod
    def apply(scopes: "Scopes") -> None:
        scopes.enter_isolated_scope()


@dataclass
class LeaveScope(Event):
    node: ast.AST

    @staticmethod
    def apply(scopes: "Scopes") -> None:
        scopes.leave_scope()


class Scopes:
    def __init__(self) -> None:
        self.lookups: List[
            CM[str, List[Occurrence]]  # pylint: disable=unsubscriptable-object
        ] = [ChainMap()]

    def lookup(self, name: str) -> List[Occurrence]:
        assert self.lookups
        current = self.lookups[-1]
        return current.setdefault(name, [])

    def process(self, event: Event):
        event.apply(self)

    def add_to_scope(self, occurrence: Occurrence) -> None:
        self.lookup(occurrence.name).append(occurrence)

    def enter_new_scope(self) -> None:
        self.lookups.append(self.lookups[-1].new_child())

    def enter_isolated_scope(self) -> None:
        self.lookups.append(ChainMap())

    def leave_scope(self) -> None:
        self.lookups.pop()


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


@visit.register
def visit_class(node: ast.ClassDef, source: Source):
    row_offset, column_offset = len(node.decorator_list), len("class ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    yield Occurrence(node.name, position, node)

    yield EnterScope(node)
    yield from generic_visit(node, source)
    yield LeaveScope(node)


@visit.register
def visit_function(node: ast.FunctionDef, source: Source):
    row_offset, column_offset = len(node.decorator_list), len("def ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    yield Occurrence(node.name, position, node)
    yield EnterScope(node)

    for arg in node.args.args:

        position = node_position(arg, source)
        yield Occurrence(arg.arg, position, arg)

    yield from generic_visit(node, source)
    yield LeaveScope(node)


@visit.register
def visit_attribute(node: ast.Attribute, source: Source) -> Iterator[Event]:
    yield from visit(node.value, source)
    position = node_position(node, source)
    yield EnterAttributeScope(node)
    yield Occurrence(node.attr, position, node)


@visit.register
def visit_import(node: ast.Import, source: Source) -> Iterator[Event]:
    start = node_position(node, source)
    for alias in node.names:
        name = alias.name
        position = source.find_after(name, start)
        yield Occurrence(name, position, alias)


@visit.register
def visit_call(node: ast.Call, source: Source):
    call_position = node_position(node, source)
    yield from visit(node.func, source)

    for arg in node.args:
        yield from visit(arg, source)
    for keyword in node.keywords:
        if not keyword.arg:
            continue

        position = source.find_after(keyword.arg, call_position)
        yield Occurrence(keyword.arg, position, node)


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


def get_occurrences(source: Source) -> List[Occurrence]:
    initial_node = source.get_ast()
    return [
        event
        for event in visit(initial_node, source=source)
        if isinstance(event, Occurrence)
    ]


def get_scopes(source: Source) -> List[Union[EnterScope, LeaveScope]]:
    initial_node = source.get_ast()
    return [
        event
        for event in visit(initial_node, source=source)
        if isinstance(event, (EnterScope, LeaveScope))
    ]


def all_occurrences_of(position: Position) -> List[Occurrence]:
    found: List[Occurrence] = []
    scopes = Scopes()
    for event in visit(position.source.get_ast(), source=position.source):
        scopes.process(event)
        if isinstance(event, Occurrence) and event.position == position:
            found = scopes.lookup(event.name) or []

    return found


def all_occurrence_positions(position: Position) -> List[Position]:
    return sorted(o.position for o in all_occurrences_of(position))


def test_distinguishes_local_variables_from_global():
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

    position = Position(source=source, row=2, column=4)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=2, column=4),
        Position(source=source, row=4, column=13),
        Position(source=source, row=5, column=8),
    ]


def test_finds_attributes():
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = Position(source=source, row=3, column=0)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=3, column=0)
    ]


def test_finds_parameters():
    source = make_source(
        """
        def fun(arg, arg2):
            return arg + arg2
        fun(arg=1, arg2=2)
        """
    )

    assert [o.name for o in get_occurrences(source)] == [
        "fun",
        "arg",
        "arg2",
        "arg",
        "arg2",
        "fun",
        "arg",
        "arg2",
    ]


def test_finds_class_name():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        unbound = A.old
        """
    )

    assert [o.name for o in get_occurrences(source)] == [
        "A",
        "old",
        "self",
        "unbound",
        "A",
        "old",
    ]


def test_finds_class_scope():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        unbound = A.old
        """
    )

    assert [o.node.__class__ for o in get_scopes(source)] == [
        ast.ClassDef,
        ast.FunctionDef,
        ast.FunctionDef,
        ast.ClassDef,
    ]


def test_finds_dict_comprehension_variables():
    source = make_source(
        """
        foo = {old: None for old in range(100) if old % 3}
        """
    )

    assert [o.name for o in get_occurrences(source)] == [
        "foo",
        "old",
        "old",
        "range",
        "old",
    ]


def test_finds_loop_variables():
    source = make_source(
        """
        for i, old in enumerate(['foo']):
            print(i)
            print(old)
        """
    )

    assert [o.name for o in get_occurrences(source)] == [
        "i",
        "old",
        "enumerate",
        "print",
        "i",
        "print",
        "old",
    ]


def test_finds_superclasses():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.old()
        """
    )

    assert [o.name for o in get_occurrences(source)] == [
        "A",
        "old",
        "self",
        "B",
        "A",
        "b",
        "B",
        "c",
        "b",
        "c",
        "old",
    ]
