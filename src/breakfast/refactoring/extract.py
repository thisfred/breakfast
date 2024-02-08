import ast
import logging
from collections.abc import Iterator
from functools import singledispatch
from typing import Any

from breakfast.types import Edit, Position, Source

logger = logging.getLogger(__name__)


def get_single_expression_value(text: str) -> ast.AST | None:
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return None

    if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.Expr):
        return None

    return parsed.body[0].value


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


def generic_find_similar_nodes(
    source_ast: ast.AST, /, node: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    for _, value in ast.iter_fields(source_ast):
        if isinstance(value, list):
            for new_ast in value:
                if isinstance(new_ast, ast.AST):
                    yield from find_similar_nodes(new_ast, node, scope)

        elif isinstance(value, ast.AST):
            yield from find_similar_nodes(value, node, scope)


@singledispatch
def find_similar_nodes(
    source_ast: ast.AST, /, node: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    if is_structurally_identical(node, source_ast):
        yield scope, source_ast
    else:
        yield from generic_find_similar_nodes(source_ast, node, scope)


@find_similar_nodes.register
def find_similar_nodes_in_subscope(
    source_ast: ast.FunctionDef | ast.ClassDef, node: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    if is_structurally_identical(node, source_ast):
        yield scope, source_ast
    for _, value in ast.iter_fields(source_ast):
        if isinstance(value, list):
            for new_ast in value:
                if isinstance(new_ast, ast.AST):
                    yield from find_similar_nodes(
                        new_ast, node, (*scope, source_ast.name)
                    )
        elif isinstance(value, ast.AST):
            yield from find_similar_nodes(value, node, (*scope, source_ast.name))


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


def make_edit(source: Source, node: ast.AST, length: int, new_text: str) -> Edit:
    start = source.node_position(node)
    return Edit(start=start, end=start + (length - 1), text=new_text)


def extract_variable(name: str, start: Position, end: Position) -> tuple[Edit, ...]:
    extracted = start.text_through(end)
    logger.info(f"{extracted=}")

    if not (expression := get_single_expression_value(extracted)):
        return ()

    source = start.source
    source_ast = source.get_ast()

    other_occurrences = find_other_occurrences(
        source_ast=source_ast, node=expression, position=start
    )
    other_edits = [
        make_edit(source, o, len(extracted), new_text=name) for o in other_occurrences
    ]
    edits = sorted([Edit(start=start, end=end, text=name), *other_edits])
    first_edit_position = edits[0].start

    statement_start = None
    for statement in get_statements(source_ast):
        if (
            statement_position := source.node_position(statement)
        ) < first_edit_position:
            statement_start = statement_position

    insert_point = statement_start or first_edit_position.start_of_line
    indentation = " " * insert_point.column
    definition = f"{name} = {extracted}\n{indentation}"
    insert = Edit(start=insert_point, end=insert_point, text=definition)
    return (insert, *edits)


@singledispatch
def get_statements(node: ast.AST) -> Iterator[ast.AST]:
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for node in value:
                if isinstance(node, ast.AST):
                    yield from get_statements(node)
        elif isinstance(value, ast.AST):
            yield from get_statements(value)


@get_statements.register
def get_statements_from_expression(node: ast.Expr) -> Iterator[ast.AST]:
    yield from ()


@get_statements.register
def get_statements_from_node_with_body(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.AST]:
    for child in node.body:
        yield child
        yield from get_statements(child)
