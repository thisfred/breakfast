import ast

from functools import singledispatch
from typing import Iterator, Optional, Set

from breakfast.source import Source
from tests import make_source


@singledispatch
def name_for(node: ast.AST,) -> Optional[str]:  # pylint: disable=unused-argument
    return None


@name_for.register
def name(node: ast.Name) -> Optional[str]:
    return node.id


@name_for.register
def function_name(node: ast.FunctionDef,) -> Optional[str]:
    return node.name


@name_for.register
def class_name(node: ast.ClassDef) -> Optional[str]:
    return node.name


@name_for.register
def attribute_name(node: ast.Attribute) -> Optional[str]:
    return node.attr


@singledispatch
def build_prefix(
    node: ast.AST, prefix: str, name: str  # pylint: disable=unused-argument
) -> str:
    return prefix


@build_prefix.register
def function_prefix(
    node: ast.FunctionDef, prefix: str, name: str  # pylint: disable=unused-argument
) -> str:
    return prefix + name + "|"


def visit(node: ast.AST, prefix: str = "") -> Iterator[str]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    name = name_for(node)
    if name:
        yield prefix + name
        prefix = build_prefix(node, prefix, name)

    yield from generic_visit(node, prefix)


def generic_visit(node: ast.AST, prefix: str) -> Iterator[str]:
    """Called if no explicit visitor function exists for a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, prefix)
        elif isinstance(value, ast.AST):
            yield from visit(value, prefix)


def get_names(source: Source) -> Set[str]:
    initial_node = source.get_ast()
    return set(name for name in visit(initial_node))


def test_finds_all_names():
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
    names = get_names(source)
    assert names == {"fun", "fun|old", "fun|old2", "fun|result", "old"}
