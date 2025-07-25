from __future__ import annotations

import ast
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import singledispatch
from typing import Protocol

from breakfast import types
from breakfast.types import Position, Source, TextRange
from breakfast.visitor import generic_visit

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Occurrence:
    name: str
    position: Position
    ast: ast.AST | None
    is_definition: bool

    @property
    def source(self) -> Source:
        return self.position.source

    @property
    def start(self) -> Position:
        return self.position

    @property
    def end(self) -> Position:
        return self.position + len(self.name)


def find_nested_statements(
    statements: Iterable[ast.stmt],
) -> Iterator[ast.stmt]:
    for child in statements:
        yield child
        yield from find_statements(child)


def identity(statements: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    yield from statements


@singledispatch
def find_statements(
    node: ast.AST, recursive_find: bool = True
) -> Iterator[ast.stmt]:
    yield from generic_visit(find_statements, node, recursive_find)


@find_statements.register
def find_statements_in_expression(
    node: ast.Expr, recursive_find: bool = True
) -> Iterator[ast.stmt]:
    yield from ()


@find_statements.register
def find_statements_in_node_with_body(
    node: ast.Module
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.ClassDef
    | ast.With
    | ast.AsyncWith,
    recursive_find: bool = True,
) -> Iterator[ast.stmt]:
    find = find_nested_statements if recursive_find else identity
    yield from find(node.body)


@find_statements.register
def find_statements_in_node_with_body_and_orelse(
    node: ast.For | ast.AsyncFor | ast.If | ast.While,
    recursive_find: bool = True,
) -> Iterator[ast.stmt]:
    find = find_nested_statements if recursive_find else identity
    yield from find(node.body)
    yield from find(node.orelse)


@find_statements.register
def find_statements_in_try(
    node: ast.Try | ast.TryStar, recursive_find: bool = True
) -> Iterator[ast.stmt]:
    find = find_nested_statements if recursive_find else identity
    yield from find(node.body)
    yield from find(node.orelse)
    yield from find(node.finalbody)


def nodes_in_range(node: ast.AST, text_range: TextRange) -> Iterator[ast.AST]:
    node_range = text_range.source.node_range(node)
    if (
        node_range := text_range.source.node_range(node)
    ) and node_range in text_range:
        yield node

    if node_range is None or node_range & text_range:
        yield from generic_visit(
            nodes_in_range, node=node, text_range=text_range
        )


def find_other_nodes(
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
        if startswith(scope, original_scope)
    ]


def startswith(scope: tuple[str, ...], original_scope: tuple[str, ...]) -> bool:
    return scope[: len(original_scope)] == original_scope


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


def is_structurally_identical(node: ast.AST, other_node: object) -> bool:
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
def find_names(node: ast.AST, source: Source) -> Iterator[types.Occurrence]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_name(
    node: ast.Name, source: Source
) -> Iterator[types.Occurrence]:
    yield Occurrence(
        name=node.id,
        position=source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )


@find_names.register
def find_names_in_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: Source
) -> Iterator[types.Occurrence]:
    yield Occurrence(
        name=node.name,
        position=source.position(node.lineno - 1, node.col_offset),
        ast=node,
        is_definition=True,
    )
    yield from generic_visit(find_names, node, source)


@find_names.register
def find_names_in_arg(
    node: ast.arg, source: Source
) -> Iterator[types.Occurrence]:
    yield Occurrence(
        name=node.arg,
        position=source.position(node.lineno - 1, node.col_offset),
        ast=node,
        is_definition=True,
    )


@find_names.register
def find_names_in_attribute(
    node: ast.Attribute, source: Source
) -> Iterator[types.Occurrence]:
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


@singledispatch
def find_yields(node: ast.AST) -> Iterator[ast.Yield | ast.YieldFrom]:
    yield from generic_visit(find_yields, node)


@find_yields.register
def find_yields_in_yield(
    node: ast.Yield,
) -> Iterator[ast.Yield | ast.YieldFrom]:
    yield node


@find_yields.register
def find_yields_in_yield_from(
    node: ast.YieldFrom,
) -> Iterator[ast.Yield | ast.YieldFrom]:
    yield node


@find_yields.register
def find_yields_in_nested_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.Yield | ast.YieldFrom]:
    yield from ()


class NodeFilter(Protocol):
    def __call__(self, node: ast.AST) -> bool: ...


def get_nodes(
    node: ast.AST, node_filter: NodeFilter | None = None
) -> Iterator[ast.AST]:
    if node_filter is None or node_filter(node):
        yield node

    yield from generic_visit(get_nodes, node, node_filter)
