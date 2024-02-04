import ast
import logging
from collections.abc import Iterable, Iterator
from functools import singledispatch

from breakfast.types import Edit, Position

logger = logging.getLogger(__name__)


def is_single_expression(text: str) -> bool:
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return False

    if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.Expr):
        return False

    return True


def extract_variable(name: str, start: Position, end: Position) -> tuple[Edit, ...]:
    extracted = start.text_through(end)

    if not is_single_expression(extracted):
        return ()

    statement_start = None
    source = start.source
    for statement in visit(source.get_ast()):
        if (statement_position := source.node_position(statement)) < start:
            statement_start = statement_position

    insert_point = statement_start or start.start_of_line
    indentation = " " * insert_point.column
    definition = f"{name} = {extracted}\n{indentation}"
    return (
        Edit(start=insert_point, end=insert_point, text=definition),
        Edit(start=start, end=end, text=name),
    )


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
    return
    yield


@visit.register
def visit_module(node: ast.Module) -> Iterator[ast.AST]:
    yield from process_body(node.body)


@visit.register
def visit_function(node: ast.FunctionDef) -> Iterator[ast.AST]:
    yield from process_body(node.body)


@visit.register
def visit_class(node: ast.ClassDef) -> Iterator[ast.AST]:
    yield from process_body(node.body)


def process_body(body: Iterable[ast.AST]) -> Iterator[ast.AST]:
    for child in body:
        yield child
        yield from visit(child)
