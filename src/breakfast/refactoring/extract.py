import ast
import logging
from collections.abc import Iterable, Iterator
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
) -> Iterator[ast.AST]:
    for similar in find_similar_nodes(source_ast, node):
        if position.source.node_position(similar) == position:
            continue
        yield similar


def find_similar_nodes(source_ast: ast.AST, node: ast.AST) -> Iterator[ast.AST]:
    if is_structurally_identical(node, source_ast):
        yield source_ast
    else:
        for _, value in ast.iter_fields(source_ast):
            if isinstance(value, list):
                for new_ast in value:
                    if isinstance(new_ast, ast.AST):
                        yield from find_similar_nodes(new_ast, node)

            elif isinstance(value, ast.AST):
                yield from find_similar_nodes(value, node)


def is_structurally_identical(node: ast.AST, other_node: Any) -> bool:
    if type(node) != type(other_node):
        return False

    for name, value in ast.iter_fields(node):
        other_value = getattr(other_node, name, None)
        if not isinstance(value, ast.AST):
            return bool(value == other_value)
        else:
            if not is_structurally_identical(value, other_value):
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

    statement_start = None
    source = start.source
    source_ast = source.get_ast()
    for statement in visit(source_ast):
        if (statement_position := source.node_position(statement)) < start:
            statement_start = statement_position

    insert_point = statement_start or start.start_of_line
    indentation = " " * insert_point.column
    definition = f"{name} = {extracted}\n{indentation}"
    insert = Edit(start=insert_point, end=insert_point, text=definition)
    other_occurrences = find_other_occurrences(
        source_ast=source_ast, node=expression, position=start
    )
    other_edits = [
        make_edit(source, o, len(extracted), new_text=name) for o in other_occurrences
    ]
    edits = [Edit(start=start, end=end, text=name), *other_edits]
    return (insert, *edits)


def generic_visit(node: ast.AST) -> Iterator[ast.AST]:
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            yield from visit_all(value)

        elif isinstance(value, ast.AST):
            yield from visit(value)


@singledispatch
def visit(node: ast.AST) -> Iterator[ast.AST]:
    yield from generic_visit(node)


def visit_all(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    for node in nodes:
        if isinstance(node, ast.AST):
            yield from visit(node)


@visit.register
def visit_expression(node: ast.Expr) -> Iterator[ast.AST]:
    yield from ()


@visit.register
def visit_node_with_body(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.AST]:
    for child in node.body:
        yield child
        yield from visit(child)
