import ast

from collections import ChainMap
from dataclasses import dataclass
from functools import singledispatch
from typing import ChainMap as CM
from typing import List, Optional

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


@dataclass(frozen=True)
class Definition:
    position: Position
    occurrences: List["Occurrence"]

    def __hash__(self) -> int:
        return hash(self.position)


@dataclass()
class Occurrence:
    name: str
    position: Position
    node: Optional[ast.AST] = None
    definition: Optional[Definition] = None


@singledispatch
def get_name_for(node: ast.AST,) -> Optional[str]:  # pylint: disable=unused-argument
    ...


@get_name_for.register
def name(node: ast.Name,) -> Optional[str]:
    return node.id


@get_name_for.register
def attribute(node: ast.Attribute,) -> Optional[str]:
    return node.attr


@get_name_for.register
def function_definition(node: ast.FunctionDef,) -> Optional[str]:
    return node.name


def create_occurrence(
    node: ast.AST,
    source: Source,
    scope: CM[str, Definition],  # pylint: disable=unsubscriptable-object
) -> Optional[Occurrence]:
    name = get_name_for(node)
    if not name:
        return None

    position = Position(source=source, row=node.lineno - 1, column=node.col_offset)
    definition = scope.get(name)
    occurrence = Occurrence(name=name, position=position, node=node)
    if definition:
        definition.occurrences.append(occurrence)
    else:
        definition = Definition(position=position, occurrences=[])
        scope[occurrence.name] = definition
    occurrence.definition = definition
    return occurrence


@singledispatch
def new_scope(node, current_scope):  # pylint: disable=unused-argument
    return current_scope


@new_scope.register
def function_scope(
    node: ast.FunctionDef, current_scope
):  # pylint: disable=unused-argument
    return current_scope.new_child()


def visit(
    node: ast.AST, source: Source, scope: CM[str, Definition]
):  # pylint: disable=unsubscriptable-object
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    occurrence = create_occurrence(node, source, scope)
    if occurrence:
        yield occurrence

    yield from generic_visit(node, source, new_scope(node, scope))


def generic_visit(
    node, source: Source, scope: CM[str, Definition]
):  # pylint: disable=unsubscriptable-object
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
    return [o for o in visit(node=source.get_ast(), source=source, scope=ChainMap())]


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


# def test_distinguishes_between_variable_and_attribute():
#     source = make_source(
#         """
#         import os

#         path = os.path.dirname(__file__)
#         """
#     )
#     results = collect_names(source)
