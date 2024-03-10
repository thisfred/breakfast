import ast
from collections.abc import Iterator
from functools import singledispatch
from typing import Any

from breakfast.types import Position, Source
from breakfast.visitor import generic_visit


@singledispatch
def find_functions(node: ast.AST, up_to: Position | None) -> Iterator[ast.FunctionDef]:
    if up_to and hasattr(node, "lineno") and up_to.source.node_position(node) > up_to:
        return
    yield from generic_visit(find_functions, node, up_to)


@find_functions.register
def find_function_in_function(
    node: ast.FunctionDef, up_to: Position | None
) -> Iterator[ast.FunctionDef]:
    yield node
    for child in node.body:
        yield from find_functions(child, up_to=up_to)


@singledispatch
def find_scopes(
    node: ast.AST, up_to: Position | None
) -> Iterator[ast.FunctionDef | ast.ClassDef]:
    if up_to and hasattr(node, "lineno") and up_to.source.node_position(node) > up_to:
        return
    yield from generic_visit(find_scopes, node, up_to)


@find_scopes.register
def find_scope_in_function_or_class(
    node: ast.FunctionDef | ast.ClassDef, up_to: Position | None
) -> Iterator[ast.FunctionDef | ast.ClassDef]:
    yield node
    for child in node.body:
        yield from find_scopes(child, up_to=up_to)


@singledispatch
def find_statements(node: ast.AST) -> Iterator[ast.AST]:
    yield from generic_visit(find_statements, node)


@find_statements.register
def find_statements_in_expression(node: ast.Expr) -> Iterator[ast.AST]:
    yield from ()


@find_statements.register
def find_statements_in_node_with_body(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.AST]:
    for child in node.body:
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


def is_compatible_with(scope: tuple[str, ...], original_scope: tuple[str, ...]) -> bool:
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
    source_ast: ast.FunctionDef | ast.ClassDef, node: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    if is_structurally_identical(node, source_ast):
        yield scope, source_ast
    else:
        yield from generic_visit(
            find_similar_nodes, source_ast, node, (*scope, source_ast.name)
        )


def is_structurally_identical(node: ast.AST, other_node: Any) -> bool:
    if type(node) != type(other_node):
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
def find_names(
    node: ast.AST, source: Source
) -> Iterator[tuple[str, Position, ast.expr_context]]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_name(
    node: ast.Name, source: Source
) -> Iterator[tuple[str, Position, ast.expr_context]]:
    yield node.id, source.node_position(node), node.ctx


@find_names.register
def find_names_in_function(
    node: ast.FunctionDef, source: Source
) -> Iterator[tuple[str, Position, ast.expr_context]]:
    for arg in node.args.args:
        yield arg.arg, source.position(arg.lineno - 1, arg.col_offset), ast.Store()
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_attribute(
    node: ast.Attribute, source: Source
) -> Iterator[tuple[str, Position, ast.expr_context]]:
    yield from find_names(node.value, source)
