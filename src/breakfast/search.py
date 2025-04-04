import ast
from collections.abc import Iterable, Iterator
from functools import singledispatch
from typing import Any, Protocol

from breakfast.types import NodeType, Occurrence, Position, Source
from breakfast.visitor import generic_visit


@singledispatch
def find_statements(node: ast.AST) -> Iterator[ast.stmt]:
    yield from generic_visit(find_statements, node)


@find_statements.register
def find_statements_in_expression(node: ast.Expr) -> Iterator[ast.stmt]:
    yield from ()


@find_statements.register
def find_statements_in_node_with_body(
    node: ast.Module
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.ClassDef
    | ast.With
    | ast.AsyncWith,
) -> Iterator[ast.stmt]:
    yield from find_nested_statements(node.body)


@find_statements.register
def find_statements_in_node_with_body_and_orelse(
    node: ast.For | ast.AsyncFor | ast.If | ast.While,
) -> Iterator[ast.stmt]:
    yield from find_nested_statements(node.body)
    yield from find_nested_statements(node.orelse)


@find_statements.register
def find_statements_in_try(node: ast.Try | ast.TryStar) -> Iterator[ast.stmt]:
    yield from find_nested_statements(node.body)
    yield from find_nested_statements(node.orelse)
    yield from find_nested_statements(node.finalbody)


def find_nested_statements(
    statements: Iterable[ast.stmt],
) -> Iterator[ast.stmt]:
    for child in statements:
        yield child
        yield from find_statements(child)


def find_other_occurrences(
    *, source_ast: ast.AST, node: ast.AST, position: Position
) -> list[ast.AST]:
    results = []
    original_scope: tuple[str, ...] = ()
    for scope, similar in find_similar_nodes(source_ast, node, scope=()):
        if position.source.node_position(similar) == position:
            original_scope = scope
            continue
        else:
            results.append((scope, similar))

    return [
        similar
        for scope, similar in results
        if is_compatible_with(scope, original_scope)
    ]


def is_compatible_with(
    scope: tuple[str, ...], original_scope: tuple[str, ...]
) -> bool:
    return all(scope[i] == s for i, s in enumerate(original_scope))


@singledispatch
def find_similar_nodes(
    source_ast: ast.AST, node: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    if is_structurally_identical(node, source_ast):
        yield scope, source_ast
    else:
        yield from generic_visit(find_similar_nodes, source_ast, node, scope)


@find_similar_nodes.register
def find_similar_nodes_in_subscope(
    source_ast: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    node: ast.AST,
    scope: tuple[str, ...],
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    if is_structurally_identical(node, source_ast):
        yield scope, source_ast
    else:
        yield from generic_visit(
            find_similar_nodes, source_ast, node, (*scope, source_ast.name)
        )


def is_structurally_identical(node: ast.AST, other_node: Any) -> bool:
    if type(node) is not type(other_node):
        return False

    for name, value in ast.iter_fields(node):
        other_value = getattr(other_node, name, None)
        if isinstance(value, ast.AST):
            if not is_structurally_identical(value, other_value):
                return False

        elif value != other_value:
            return False

    return True


@singledispatch
def find_names(node: ast.AST, source: Source) -> Iterator[Occurrence]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_name(node: ast.Name, source: Source) -> Iterator[Occurrence]:
    yield Occurrence(
        name=node.id,
        position=source.node_position(node),
        ast=node,
        node_type=NodeType.DEFINITION
        if isinstance(node.ctx, ast.Store)
        else NodeType.REFERENCE,
    )


@find_names.register
def find_names_in_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: Source
) -> Iterator[Occurrence]:
    yield Occurrence(
        name=node.name,
        position=source.position(node.lineno - 1, node.col_offset),
        ast=node,
        node_type=NodeType.DEFINITION,
    )
    for arg in node.args.args:
        yield Occurrence(
            name=arg.arg,
            position=source.position(arg.lineno - 1, arg.col_offset),
            ast=arg,
            node_type=NodeType.DEFINITION,
        )
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_attribute(
    node: ast.Attribute, source: Source
) -> Iterator[Occurrence]:
    yield from find_names(node.value, source)


@singledispatch
def find_returns(node: ast.AST) -> Iterator[ast.Return]:
    yield from generic_visit(find_returns, node)


@find_returns.register
def find_returns_in_return(node: ast.Return) -> Iterator[ast.Return]:
    yield node


@find_returns.register
def find_returns_in_nested_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.Return]:
    yield from ()


class NodeFilter(Protocol):
    def __call__(self, node: ast.AST) -> bool: ...


def get_nodes(
    node: ast.AST, node_filter: NodeFilter | None = None
) -> Iterator[ast.AST]:
    if node_filter is None or node_filter(node):
        yield node

    yield from generic_visit(get_nodes, node, node_filter)

    return None
