import ast

from collections import defaultdict
from functools import singledispatch
from typing import Tuple


def generic_visit(node: ast.AST, scope, namespace):
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, scope, namespace)
        elif isinstance(value, ast.AST):
            yield from visit(value, scope, namespace)


@singledispatch
def visit(node: ast.AST, scope, namespace):
    """Visit a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    yield from generic_visit(node, scope, namespace)


@visit.register
def visit_module(node: ast.Module, scope, namespace):
    yield from generic_visit(node, scope, namespace)


@visit.register
def visit_name(node: ast.Name, scope, namespace):
    position = node_position(node)
    name = namespaced(namespace, node.id)
    if isinstance(node.ctx, ast.Store):
        scope[name] = []
        scope[name].append(position)
        yield scope[name]
    else:
        occurrences = scope[name]
        occurrences.append(position)
        yield occurrences


def namespaced(namespace: Tuple[str, ...], name: str) -> Tuple[str, ...]:
    return tuple(namespace) + (name,)


def node_position(node: ast.AST, row_offset=0, column_offset=0) -> Tuple[int, int]:
    return ((node.lineno - 1) + row_offset, node.col_offset + column_offset)


def test_adds_definition_to_scope():
    tree = ast.parse("old = 1")
    scope = defaultdict(list)
    namespace = []

    occurrences = list(visit(tree, scope, namespace))

    assert occurrences == [[(0, 0)]]
    assert scope == {("old",): [(0, 0)]}
