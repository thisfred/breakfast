import ast
import logging
import re
from collections.abc import Container, Iterable, Iterator
from functools import singledispatch
from typing import Any

from breakfast.types import Edit, Line, Position, Source, TextRange
from breakfast.visitor import generic_visit

logger = logging.getLogger(__name__)

INDENTATION = re.compile(r"^(\s+)")
FOUR_SPACES = "    "


def extract_variable(name: str, start: Position, end: Position) -> tuple[Edit, ...]:
    extracted = start.through(end).text
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
    if start.row < end.row:
        start = start.start_of_line
        end = end.line.next.start if end.line.next else end

    scope = get_scope(at=start)
    source_ast = start.source.get_ast()
    names_modified_in_body = find_modified_names(source_ast, start, end)
    names_used_after = find_names_defined_before_range(
        end, scope.end, scope_start=scope.start
    )
    return_values = [n for n in names_modified_in_body if n in set(names_used_after)]

    original_indentation = get_indentation(at=start)
    new_indentation = original_indentation + FOUR_SPACES
    text = start.through(end).text
    extracted = "\n".join(
        [f"{FOUR_SPACES}{line}".rstrip() for line in text.split("\n")]
    )
    if return_values:
        return_values_as_string = f'{", ".join(return_values)}'

        extracted += f"\n{new_indentation}return {return_values_as_string}"
        assignment = f"{return_values_as_string} = "
    else:
        assignment = ""

    local_names = find_names_defined_before_range(start, end, scope_start=scope.start)
    params = ", ".join(local_names)
    insert = Edit(
        start=start,
        end=start,
        text=f"\n{original_indentation}def {name}({params}):\n{extracted}\n",
    )
    args = ", ".join(f"{n}={n}" for n in local_names)
    call = f"{name}({args})"
    replace = Edit(
        start=start, end=end, text=f"{original_indentation}{assignment}{call}\n"
    )
    return (insert, replace)


def get_scope(at: Position) -> TextRange:
    line = at.source.lines[at.row]
    indentation = get_indentation(at)
    start_line = end_line = line
    while (previous_line := start_line.previous) and (
        previous_line.text.strip() == ""
        or previous_line.text.startswith(indentation)
        or previous_line.text.lstrip().startswith("def ")
    ):
        start_line = previous_line
        if start_line.text.lstrip().startswith(
            "def "
        ) and not start_line.text.startswith(indentation):
            break

    while (next_line := end_line.next) and (
        next_line.text.strip() == "" or next_line.text.startswith(indentation)
    ):
        end_line = next_line

    text_range = start_line.start.through(end_line.end)
    return text_range


def get_indentation(at: Position) -> str:
    text = at.source.lines[at.row].text
    if not (indentation_match := INDENTATION.match(text)):
        return ""
    if not (groups := indentation_match.groups()):
        return ""

    return groups[0]


def find_modified_names(
    source_ast: ast.AST, start: Position, end: Position
) -> list[str]:
    defined_inside_subtree = []
    for name, position, ctx in find_names(source_ast, start.source):
        if position > end:
            break
        if position < start:
            continue
        if isinstance(ctx, ast.Store):
            defined_inside_subtree.append(name)

    return defined_inside_subtree


def slide_statements(first: Line, last: Line) -> tuple[Edit, ...]:
    target = find_slide_target(first, last)
    if target is None:
        return ()
    insert = Edit(
        start=target, end=target, text=first.start.through(last.end).text + "\n"
    )
    delete = Edit(start=first.start, end=last.end, text="")
    return (insert, delete)


def find_slide_target(first: Line, last: Line) -> Position | None:
    source = first.start.source
    source_ast = source.get_ast()
    names_defined_in_statements = find_modified_names(source_ast, first.start, last.end)
    target = find_first_usage(
        source, source_ast, set(names_defined_in_statements), after=last
    )
    if not target:
        return None

    lines = source.lines[first.row : last.row + 1]
    original_indentation = min(get_indentation(at=line.start) for line in lines)

    while (
        target.row > last.row + 1 and get_indentation(at=target) != original_indentation
    ):
        previous = target.line.previous
        if not previous:
            break
        target = previous.start

    if target and target.row > last.row + 1:
        return target.start_of_line

    return None


def find_first_usage(
    source: Source, source_ast: ast.AST, names: Container[str], after: Line
) -> Position | None:
    for name, position, _ in find_names(source_ast, source):
        if position.row <= after.row:
            continue
        if name in names:
            return position

    return None


def find_names_defined_before_range(
    start: Position, end: Position, *, scope_start: Position
) -> Iterable[str]:
    source = start.source
    source_ast = source.get_ast()

    defined_outside_subtree = set()
    loaded_in_subtree = set()
    order = {}
    for i, (name, position, ctx) in enumerate(find_names(source_ast, source)):
        if position < scope_start:
            continue
        if position > end:
            break
        if isinstance(ctx, ast.Store) and position < start:
            defined_outside_subtree.add(name)
        elif isinstance(ctx, ast.Load) and position > start and position < end:
            loaded_in_subtree.add(name)
            if name not in order:
                order[name] = i

    return sorted(
        defined_outside_subtree & loaded_in_subtree,
        key=lambda name: order.get(name, 100),
    )


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
