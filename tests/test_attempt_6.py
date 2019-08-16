import ast

from collections import ChainMap
from dataclasses import dataclass, field
from functools import singledispatch
from typing import ChainMap as CM
from typing import Iterator, List, Optional, Tuple, Type

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


@dataclass()
class Scope:
    lookup: CM[  # pylint: disable=unsubscriptable-object
        str, List["Occurrence"]
    ] = field(default_factory=ChainMap)
    node_type: Optional[Type[ast.AST]] = None


@dataclass()
class Occurrence:
    name: str
    position: Position
    node: ast.AST
    scope: Scope


@singledispatch
def name_for(
    node: ast.AST, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return None


@name_for.register
def name(
    node: ast.Name, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return node.id


@name_for.register
def module_name(
    node: ast.Module, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return source.module_name


def node_position(
    node: ast.AST, source: Source, row_offset=0, column_offset=0
) -> Position:
    return Position(
        source=source,
        row=(node.lineno - 1) + row_offset,
        column=node.col_offset + column_offset,
    )


@singledispatch
def new_scope(
    node: ast.AST,  # pylint: disable=unused-argument
    occurrence: Occurrence,  # pylint: disable=unused-argument
    current_scope: Scope,
) -> Scope:
    return current_scope


@new_scope.register
def name_scope(
    node: ast.Name,
    occurrence: Occurrence,  # pylint: disable=unused-argument
    current_scope: Scope,  # pylint: disable=unused-argument
) -> Scope:
    return Scope(node_type=node.__class__)


@singledispatch
def visit(
    node: ast.AST, source: Source, scope: Scope
) -> Iterator[Tuple[Scope, Occurrence]]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    name = name_for(node, source)
    if name:
        position = node_position(node, source)
        occurrence = Occurrence(name=name, position=position, node=node, scope=scope)
        scope.lookup.setdefault(occurrence.name, []).append(occurrence)
        scope = new_scope(node, occurrence, scope)
        yield scope, occurrence

    yield from generic_visit(node, source, scope)


@visit.register
def visit_function(node: ast.FunctionDef, source: Source, scope: Scope):
    row_offset, column_offset = len(node.decorator_list), len("def ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    occurrence = Occurrence(name=node.name, position=position, node=node, scope=scope)
    scope.lookup.setdefault(occurrence.name, []).append(occurrence)

    if scope.node_type == ast.ClassDef:
        scope = Scope(node_type=node.__class__, lookup=scope.lookup.parents.new_child())

    else:
        scope = Scope(node_type=node.__class__, lookup=scope.lookup.new_child())

    yield scope, occurrence

    yield from generic_visit(node, source, scope)


@visit.register
def visit_class(node: ast.ClassDef, source: Source, scope: Scope):
    row_offset, column_offset = len(node.decorator_list), len("class ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    occurrence = Occurrence(name=node.name, position=position, node=node, scope=scope)
    scope.lookup.setdefault(occurrence.name, []).append(occurrence)
    scope = Scope(node_type=node.__class__, lookup=scope.lookup.new_child())
    yield scope, occurrence

    yield from generic_visit(node, source, scope)


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, scope: Scope
) -> Iterator[Tuple[Scope, Occurrence]]:
    """Visit an Attribute node.

    For Attributes, we have to sort of turn things inside out to build up the nested
    scope correctly, because a.b.c shows up as `Attribute(value=a.b, attr=c)`.
    """
    occurrence = None
    new_scope = scope
    for new_scope, occurrence in visit(node.value, source, scope):
        yield new_scope, occurrence

    position = node_position(node, source)
    occurrence = Occurrence(
        name=node.attr, position=position, node=node, scope=new_scope
    )
    new_scope.lookup.setdefault(occurrence.name, []).append(occurrence)
    yield new_scope, occurrence


def generic_visit(
    node, source: Source, scope: Scope
) -> Iterator[Tuple[Scope, Occurrence]]:
    """Called if no explicit visitor function exists for a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, source, scope)
        elif isinstance(value, ast.AST):
            yield from visit(value, source, scope)


def collect_occurrences(source: Source) -> List[Occurrence]:
    initial_node = source.get_ast()
    top_level_scope = Scope()
    return [o for _, o in visit(initial_node, source=source, scope=top_level_scope)]


def all_occurrences_of(position: Position) -> List[Occurrence]:
    original_occurrence = next(
        (o for o in collect_occurrences(position.source) if o.position == position),
        None,
    )
    if not original_occurrence:
        return []
    return original_occurrence.scope.lookup[original_occurrence.name]


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
    position = Position(source=source, row=2, column=4)

    results = all_occurrences_of(position)

    assert len(results) == 3


def test_module_global_does_not_see_function_local():
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
    position = Position(source=source, row=8, column=0)

    results = all_occurrences_of(position)

    assert len(results) == 1


def test_distinguishes_between_variable_and_attribute():
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )
    position = Position(source=source, row=3, column=0)

    results = all_occurrences_of(position)
    assert len(results) == 1


def test_finds_variable_in_closure():
    source = make_source(
        """
        old = 12

        def fun():
            result = old + 1
            return result

        old = 20
        """
    )
    position = Position(source=source, row=1, column=0)

    results = all_occurrences_of(position)
    assert len(results) == 3
