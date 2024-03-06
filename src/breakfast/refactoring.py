import ast
import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from functools import singledispatch
from textwrap import dedent
from typing import Any

from breakfast import types
from breakfast.names import all_occurrence_positions, build_graph
from breakfast.source import TextRange
from breakfast.types import Edit, Line, NotFoundError, Position, Source
from breakfast.visitor import generic_visit

logger = logging.getLogger(__name__)

INDENTATION = re.compile(r"^(\s+)")
FOUR_SPACES = "    "
NEWLINE = "\n"


class Refactor:
    def __init__(self, text_range: types.TextRange):
        self.text_range = text_range
        self.source = self.text_range.start.source
        self.source_ast = self.source.get_ast()
        self.scope_graph = build_graph([self.source])

    def extract_variable(self, name: str) -> tuple[Edit, ...]:
        extracted = self.text_range.text
        logger.info(f"{extracted=}")

        if not (expression := get_single_expression_value(extracted)):
            return ()

        other_occurrences = find_other_occurrences(
            source_ast=self.source_ast, node=expression, position=self.text_range.start
        )
        other_edits = [
            make_edit(self.source, o, len(extracted), new_text=name)
            for o in other_occurrences
        ]
        edits = sorted(
            [
                Edit(text_range=self.text_range, text=name),
                *other_edits,
            ]
        )
        first_edit_position = edits[0].start

        statement_start = None
        for statement in find_statements(self.source_ast):
            if (
                statement_position := self.source.node_position(statement)
            ) < first_edit_position:
                statement_start = statement_position

        insert_point = statement_start or first_edit_position.start_of_line
        indentation = " " * insert_point.column
        definition = f"{name} = {extracted}{NEWLINE}{indentation}"
        insert = make_insert(at=insert_point, text=definition)
        return (insert, *edits)

    def extract_method(self, name: str) -> tuple[Edit, ...]:
        self_name = "self"
        self_prefix = f"{self_name}."
        return self.extract_callable(
            name=name,
            indent_definition=True,
            self_name=self_name,
            self_prefix=self_prefix,
        )

    def extract_function(self, name: str) -> tuple[Edit, ...]:
        return self.extract_callable(
            name=name,
            new_indentation=FOUR_SPACES,
        )

    def extract_callable(
        self,
        name: str,
        indent_definition: bool = False,
        new_indentation: str | None = None,
        self_name: str | None = None,
        self_prefix: str = "",
    ) -> tuple[Edit, ...]:
        start, end = self.extended_range.start, self.extended_range.end
        original_indentation = get_indentation(at=start)
        if new_indentation is None:
            new_indentation = original_indentation

        names_in_range = self.get_names_in_range(self.extended_range)

        return_values = self.get_return_values(names_in_range=names_in_range, end=end)

        extracted, assignment = self.extract_text(
            text_range=self.extended_range,
            return_values=return_values,
            new_indentation=new_indentation,
        )

        start_of_current_scope = find_start_of_scope(
            start=start,
            current_indentation=original_indentation,
        )

        parameter_names = self.get_parameter_names(
            names_in_range=names_in_range,
            text_range=self.extended_range,
            start_of_current_scope=start_of_current_scope,
            self_name=self_name,
        )
        if self_name is None:
            parameters_with_self = parameter_names
        else:
            parameters_with_self = [self_name, *parameter_names]
        parameters = ", ".join(parameters_with_self)

        definition_indentation = original_indentation[:-4] if indent_definition else ""
        insert = Edit(
            TextRange(start=start_of_current_scope, end=start_of_current_scope),
            text=f"{NEWLINE}{definition_indentation}def {name}({parameters}):{NEWLINE}{extracted}{NEWLINE}",
        )

        arguments = ", ".join(f"{n}={n}" for n in parameter_names)
        call = f"{self_prefix}{name}({arguments})"
        replace = Edit(
            TextRange(start=start, end=end),
            text=f"{original_indentation}{assignment}{call}{NEWLINE}",
        )
        return (insert, replace)

    def get_parameter_names(
        self,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        text_range: types.TextRange,
        start_of_current_scope: Position,
        self_name: str | None = None,
    ) -> list[str]:
        parameter_names = [
            name
            for position, name in self.find_names_defined_before_range(
                names_in_range, text_range
            )
            if position >= start_of_current_scope
            and (self_name is None or name != self_name)
        ]

        return parameter_names

    def extract_text(
        self,
        text_range: types.TextRange,
        return_values: Sequence[str],
        new_indentation: str,
    ) -> tuple[str, str]:
        text = text_range.text
        extracted = NEWLINE.join(
            [
                f"{new_indentation}{line}".rstrip()
                for line in dedent(text).split(NEWLINE)
            ]
        )
        if return_values:
            return_values_as_string = f'{", ".join(return_values)}'
            extracted += f"{NEWLINE}{new_indentation}return {return_values_as_string}"
            assignment = f"{return_values_as_string} = "
        else:
            assignment = ""

        return extracted, assignment

    def slide_statements_down(self) -> tuple[Edit, ...]:
        first, last = self.text_range.start.line, self.text_range.end.line
        target = self.find_slide_target_after(first, last)
        if target is None:
            return ()
        insert = make_insert(
            at=target, text=first.start.through(last.end).text + NEWLINE
        )
        delete = make_delete(
            start=first.start, end=last.next.start if last.next else last.end
        )
        return (insert, delete)

    def slide_statements_up(self) -> tuple[Edit, ...]:
        first, last = self.text_range.start.line, self.text_range.end.line
        target = self.find_slide_target_before(first, last)
        if target is None:
            return ()
        insert = make_insert(
            at=target, text=first.start.through(last.end).text + NEWLINE
        )
        delete = make_delete(
            start=first.start, end=last.next.start if last.next else last.end
        )
        return (insert, delete)

    @property
    def extended_range(self) -> types.TextRange:
        start = self.text_range.start
        end = self.text_range.end
        if start.row < end.row:
            start = start.start_of_line
            end = end.line.next.start if end.line.next else end

        return TextRange(start, end)

    def find_slide_target_after(self, first: Line, last: Line) -> Position | None:
        text_range = TextRange(first.start, last.end)
        names_defined_in_range = self.get_names_defined_in_range(text_range)
        first_usage_after_range = next(
            p
            for _, p in self.find_names_used_after_position(
                names_defined_in_range, last.end
            )
        )
        if not first_usage_after_range:
            return None

        original_indentation = get_indentation(at=first.start)

        while (
            first_usage_after_range.row > last.row + 1
            and get_indentation(at=first_usage_after_range) != original_indentation
        ):
            previous = first_usage_after_range.line.previous
            if not previous:
                break
            first_usage_after_range = previous.start

        if first_usage_after_range and first_usage_after_range.row > last.row + 1:
            return first_usage_after_range.start_of_line

        return None

    def find_slide_target_before(self, first: Line, last: Line) -> Position | None:
        original_indentation = get_indentation(at=first.start)
        line = first
        while (
            line.previous
            and get_indentation(at=line.previous.start) >= original_indentation
        ):
            line = line.previous
        if line == first:
            return None

        scope_before = TextRange(
            line.start, first.previous.end if first.previous else first.start
        )

        text_range = TextRange(first.start, last.end)
        names_in_range = {n for n, _, _ in self.get_names_in_range(text_range)}
        target = max(
            line.start,
            *(
                position.line.next.start if position.line.next else position.line.end
                for name, position, _ in self.get_names_in_range(scope_before)
                if name in names_in_range
            ),
        )

        return target

    def get_return_values(
        self,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        end: Position,
    ) -> list[str]:
        names_modified_in_body = [
            name for name, _, ctx in names_in_range if isinstance(ctx, ast.Store)
        ]
        names_used_after = {
            n
            for n, _ in self.find_names_used_after_position(
                [(n, p) for n, p, _ in names_in_range], end
            )
        }
        seen = set()
        return_values = []
        for name in names_modified_in_body:
            if name in names_used_after and name not in seen:
                seen.add(name)
                return_values.append(name)

        return return_values

    def get_names_in_range(
        self, text_range: types.TextRange
    ) -> Sequence[tuple[str, Position, ast.expr_context]]:
        names = []
        for name, position, context in find_names(self.source_ast, self.source):
            if position < text_range.start:
                continue
            if position > text_range.end:
                break
            names.append((name, position, context))

        return names

    def find_names_defined_before_range(
        self,
        names: Sequence[tuple[str, Position, ast.expr_context]],
        text_range: types.TextRange,
    ) -> Iterable[tuple[Position, str]]:
        found = set()
        for name, position, _ in names:
            if name in found:
                continue
            try:
                occurrences = all_occurrence_positions(position, graph=self.scope_graph)
            except NotFoundError:
                continue
            for occurrence in occurrences:
                if occurrence < text_range.start:
                    found.add(name)
                    yield occurrence, name
                break

    def find_names_used_after_position(
        self,
        names: Sequence[tuple[str, Position]],
        cutoff: Position,
    ) -> Iterable[tuple[str, Position]]:
        for name, position in names:
            try:
                occurrences = all_occurrence_positions(position, graph=self.scope_graph)
            except NotFoundError:
                continue
            for occurrence in occurrences:
                if occurrence > cutoff:
                    yield name, occurrence
                    break

    def get_names_defined_in_range(
        self, text_range: TextRange
    ) -> list[tuple[str, Position]]:
        names_defined_in_range = [
            (name, position)
            for name, position, ctx in self.get_names_in_range(text_range)
            if isinstance(ctx, ast.Store)
        ]

        return names_defined_in_range


def find_start_of_scope(start: Position, current_indentation: str) -> Position:
    start_of_current_scope = start
    while (
        get_indentation(at=start_of_current_scope) >= current_indentation
        or start_of_current_scope.line.text == ""
        or start_of_current_scope.line.text.lstrip().startswith(")")
    ) and start_of_current_scope.line.previous:
        start_of_current_scope = start_of_current_scope.line.previous.start

    return start_of_current_scope


def get_indentation(at: Position) -> str:
    text = at.source.lines[at.row].text
    if not (indentation_match := INDENTATION.match(text)):
        return ""
    if not (groups := indentation_match.groups()):
        return ""

    return groups[0]


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
    return Edit(TextRange(start=start, end=start + (length - 1)), text=new_text)


def make_insert(at: Position, text: str) -> Edit:
    return Edit(TextRange(start=at, end=at), text=text)


def make_delete(start: Position, end: Position) -> Edit:
    return Edit(TextRange(start=start, end=end), text="")


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
