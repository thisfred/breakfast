import ast
import logging
from collections.abc import Container, Iterable, Iterator
from functools import singledispatch
from typing import Any

from breakfast.types import Edit, Line, Position, Source
from breakfast.visitor import generic_visit

logger = logging.getLogger(__name__)


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
    for statement in find_statements(source_ast):
        if (
            statement_position := source.node_position(statement)
        ) < first_edit_position:
            statement_start = statement_position

    insert_point = statement_start or first_edit_position.start_of_line
    indentation = " " * insert_point.column
    definition = f"{name} = {extracted}\n{indentation}"
    insert = Edit(start=insert_point, end=insert_point, text=definition)
    return (insert, *edits)


def extract_function(name: str, start: Position, end: Position) -> tuple[Edit, ...]:
    indentation = "    "
    if start.row < end.row:
        start = start.start_of_line
        end = end.next_line
    text = start.text_through(end)

    logger.info(f"{text=}")
    extracted = "\n".join([f"{indentation}{line}" for line in text.split("\n")])
    logger.info(f"{extracted=}")

    insert_point = start
    local_names = find_undefined_local_names(start, end)
    params = ", ".join(local_names)
    insert = Edit(
        start=insert_point,
        end=insert_point,
        text=f"\ndef function({params}):\n{extracted}\n",
    )
    return (insert,)


def slide_statements(first: Line, last: Line) -> tuple[Edit, ...]:
    target = find_slide_target(first, last)
    if target.row <= first.row:
        return ()
    insert = Edit(start=target, end=target, text=first.text + "\n")
    delete = Edit(start=first.start, end=last.end, text="")
    return (insert, delete)


def find_slide_target(first: Line, last: Line) -> Position:
    source = first.start.source
    source_ast = source.get_ast()
    names_defined_in_statements = find_names_defined(source_ast, first, last)
    first_usage = find_first_usage(source_ast, names_defined_in_statements, after=last)
    if first_usage and first_usage.row > last.row + 1:
        return first_usage.start_of_line

    return first.start


def find_names_defined(source_ast: ast.AST, first: Line, last: Line) -> set[str]:
    defined_inside_subtree = set()
    for name in find_names(source_ast):
        position = first.source.node_position(name)
        if position > last.end:
            break
        if position < first.start:
            continue
        if isinstance(name.ctx, ast.Store):
            defined_inside_subtree.add(name.id)
    return defined_inside_subtree


def find_first_usage(
    source_ast: ast.AST, names: Container[str], after: Line
) -> Position | None:
    for name in find_names(source_ast):
        position = after.source.node_position(name)

        if position <= after.start:
            continue
        if name.id in names:
            return position

    return None


def find_undefined_local_names(start: Position, end: Position) -> Iterable[str]:
    source = start.source
    source_ast = source.get_ast()

    defined_outside_subtree = set()
    loaded_in_subtree = set()
    order = {}
    for i, name in enumerate(find_names(source_ast)):
        position = source.node_position(name)
        if position > end:
            break
        if isinstance(name.ctx, ast.Store) and position < start:
            defined_outside_subtree.add(name.id)
        elif isinstance(name.ctx, ast.Load) and position > start and position < end:
            loaded_in_subtree.add(name.id)
            if name.id not in order:
                order[name.id] = i

    return sorted(
        defined_outside_subtree & loaded_in_subtree,
        key=lambda name: order.get(name, 100),
    )


@singledispatch
def find_names(node: ast.AST) -> Iterator[ast.Name]:
    yield from generic_visit(find_names, node)


@find_names.register
def find_names_in_name(node: ast.Name) -> Iterator[ast.Name]:
    yield node


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


def make_edit(source: Source, node: ast.AST, length: int, new_text: str) -> Edit:
    start = source.node_position(node)
    return Edit(start=start, end=start + (length - 1), text=new_text)


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
