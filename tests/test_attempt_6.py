import ast

from collections import ChainMap
from dataclasses import dataclass, field
from functools import singledispatch
from typing import ChainMap as CM
from typing import Iterator, List, Optional

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


@dataclass(frozen=True)
class Definition:
    position: Position
    occurrences: List["Occurrence"]

    def __hash__(self) -> int:
        return hash(self.position)


@dataclass(frozen=True)
class Scope:
    lookup: CM[str, Definition] = field(  # pylint: disable=unsubscriptable-object
        default_factory=ChainMap
    )


@dataclass()
class Occurrence:
    name: str
    position: Position
    node: ast.AST
    scope: Scope
    definition: Optional[Definition] = None


@singledispatch
def get_name_for(
    node: ast.AST, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return None


@get_name_for.register
def name(
    node: ast.Name, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return node.id


@get_name_for.register
def function_name(
    node: ast.FunctionDef, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return node.name


@get_name_for.register
def class_name(
    node: ast.ClassDef, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return node.name


@get_name_for.register
def attribute_name(
    node: ast.Attribute, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return node.attr


@get_name_for.register
def module_name(
    node: ast.Module, source: Source  # pylint: disable=unused-argument
) -> Optional[str]:
    return source.module_name


def create_occurrence(
    node: ast.AST, source: Source, scope: Scope
) -> Optional[Occurrence]:
    name = get_name_for(node, source)
    if not name:
        return None

    position = Position(source=source, row=node.lineno - 1, column=node.col_offset)
    definition = scope.lookup.get(name)
    occurrence = Occurrence(name=name, position=position, node=node, scope=Scope())
    if definition:
        definition.occurrences.append(occurrence)
    else:
        definition = Definition(position=position, occurrences=[])
        scope.lookup[occurrence.name] = definition
    occurrence.definition = definition
    return occurrence


@singledispatch
def new_scope(
    node: ast.AST,  # pylint: disable=unused-argument
    occurrence: Occurrence,  # pylint: disable=unused-argument
    current_scope: Scope,
) -> Scope:
    return current_scope


@new_scope.register
def name_scope(
    node: ast.Name,  # pylint: disable=unused-argument
    occurrence: Occurrence,
    current_scope: Scope,  # pylint: disable=unused-argument
) -> Scope:
    occurrence.scope = Scope()
    return occurrence.scope


@new_scope.register
def function_scope(
    node: ast.FunctionDef,  # pylint: disable=unused-argument
    occurrence: Occurrence,
    current_scope: Scope,
) -> Scope:
    new_scope = Scope(lookup=current_scope.lookup.new_child())
    occurrence.scope = new_scope
    return new_scope


@new_scope.register
def class_scope(
    node: ast.ClassDef,  # pylint: disable=unused-argument
    occurrence: Occurrence,
    current_scope: Scope,
) -> Scope:
    new_scope = Scope(lookup=current_scope.lookup.new_child())
    occurrence.scope = new_scope
    return new_scope


@singledispatch
def visit(node: ast.AST, source: Source, scope: Scope) -> Iterator[Occurrence]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    occurrence = create_occurrence(node, source, scope)
    if occurrence:
        yield occurrence
        scope = new_scope(node, occurrence, scope)

    yield from generic_visit(node, source, scope)


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, scope: Scope
) -> Iterator[Occurrence]:
    """Visit an Attribute node.

    For Attributes, we have to sort of turn things inside out to build up the nested
    scope correctly, because a.b.c shows up as `Attribute(value=a.b, attr=c)`.
    """
    occurrence = None
    for occurrence in visit(node.value, source, scope):
        yield occurrence
    if occurrence:
        scope = occurrence.scope
    occurrence = create_occurrence(node, source, scope)
    if occurrence:
        yield occurrence


def generic_visit(node, source: Source, scope: Scope) -> Iterator[Occurrence]:
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


def collect_names(source: Source) -> List[Occurrence]:
    initial_node = source.get_ast()
    top_level_scope = Scope()
    return [o for o in visit(initial_node, source=source, scope=top_level_scope)]


def all_occurrences_of(position: Position) -> List[Occurrence]:
    return [
        o
        for o in collect_names(position.source)
        if o.definition and o.definition.position == position
    ]


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
    assert all(r.definition and r.definition.position == position for r in results)


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
    assert results[0].definition
    assert results[0].definition.position == position


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
