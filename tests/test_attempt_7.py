import ast

from functools import singledispatch
from typing import Iterator, Optional, Set, Tuple

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
def visit(node: ast.AST, prefix: str = "") -> Iterator[Tuple[str, str]]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    new_prefix = prefix
    name = name_for(node)
    if name:
        if isinstance(node, ast.FunctionDef):
            new_prefix = prefix + name + "|"
        elif isinstance(node, (ast.Attribute, ast.Name)):
            new_prefix = prefix + name + "."
        yield new_prefix, prefix + name

    yield from generic_visit(node, new_prefix)


@visit.register
def visit_attribute(node: ast.Attribute, prefix: str) -> Iterator[Tuple[str, str]]:
    """Visit an Attribute node.

    For Attributes, we have to sort of turn things inside out to build up the nested
    scope correctly, because a.b.c shows up as `Attribute(value=a.b, attr=c)`.
    """
    new_prefix = None
    for new_prefix, name in visit(node.value, prefix):
        yield new_prefix, name

    yield (new_prefix or prefix) + node.attr + ".", (new_prefix or prefix) + node.attr


def generic_visit(node: ast.AST, prefix: str) -> Iterator[Tuple[str, str]]:
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
    return set(name for _, name in visit(initial_node))


def test_prefixes_function_name():
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


def test_prefixes_attribute_names():
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )
    names = get_names(source)
    assert names == {"os", "path", "os.path", "os.path.dirname", "__file__"}
