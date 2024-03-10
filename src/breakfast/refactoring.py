import ast
import logging
import re
from collections.abc import Iterable, Sequence
from textwrap import dedent

from breakfast import types
from breakfast.names import all_occurrence_positions, build_graph, find_definition
from breakfast.search import (
    find_arguments_passed_in_range,
    find_functions,
    find_names,
    find_other_occurrences,
    find_statements,
)
from breakfast.source import TextRange
from breakfast.types import Edit, Line, NotFoundError, Position, Source

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
        return self.extract_callable(name=name, is_method=True)

    def extract_function(self, name: str) -> tuple[Edit, ...]:
        return self.extract_callable(name=name)

    def inline_call(self, name: str) -> tuple[Edit, ...]:
        range_end = self.text_range.start + 2
        text_range = TextRange(self.text_range.start, range_end)

        insert_range = TextRange(
            text_range.start.line.start, text_range.start.line.start
        )

        definition = find_definition(self.scope_graph, self.text_range.start)
        if definition.position is None:
            return ()

        self.source.lines[definition.position.row]
        lines = get_body_for_callable(at=definition.position)
        text = lines[-1].text.strip()
        if text.startswith("return"):
            return_value = text[len("return ") :]
        else:
            return_value = None

        body = (
            NEWLINE.join(f"{line.text}" for line in lines[:-1])
            + NEWLINE
            + f"{name} = {return_value}"
        )

        return (
            Edit(
                insert_range,
                text=f"{body}",
            ),
            Edit(text_range, text=name),
        )

    def extract_callable(
        self,
        name: str,
        is_method: bool = False,
    ) -> tuple[Edit, ...]:
        start, end = self.text_range.start, self.text_range.end
        original_indentation = get_indentation(at=start)
        new_indentation = original_indentation if is_method else FOUR_SPACES

        names_in_range = self.get_names_in_range(self.extended_range)
        extracting_partial_line = start.row == end.row and start.column != 0
        if extracting_partial_line:
            extracted, assignment = self.extract_expression(
                text_range=self.text_range,
                names_in_range=names_in_range,
                new_indentation=new_indentation,
            )
        else:
            extracted, assignment = self.extract_statements(
                text_range=self.extended_range,
                names_in_range=names_in_range,
                new_indentation=new_indentation,
            )
        start_of_current_scope = find_start_of_scope(start=start)
        parameter_names: Sequence[str] = self.get_parameter_names(
            names_in_range=names_in_range,
            text_range=self.extended_range,
            start_of_current_scope=start_of_current_scope,
        )
        self_name = "self" if is_method else None
        arguments = ", ".join(f"{n}={n}" for n in parameter_names if n != self_name)
        self_prefix = (self_name + ".") if self_name else ""
        call = f"{self_prefix}{name}({arguments})"
        replace_text = (
            call
            if extracting_partial_line
            else f"{original_indentation}{assignment}{call}{NEWLINE}"
        )
        parameters_with_self = (
            [
                self_name,
                *(n for n in parameter_names if n != self_name),
            ]
            if self_name
            else parameter_names
        )

        definition_indentation = original_indentation[:-4] if is_method else ""
        if is_method:
            if self_name in parameter_names:
                static_method = ""
                parameters = ", ".join(parameters_with_self)
            else:
                static_method = f"{definition_indentation}@staticmethod{NEWLINE}"
                parameters = ", ".join(parameter_names)
        else:
            static_method = ""
            parameters = ", ".join(parameter_names)

        insert_position = find_callable_insert_point(
            start=start, is_global=not is_method
        )
        insert = Edit(
            TextRange(start=insert_position, end=insert_position),
            text=f"{NEWLINE}{static_method}{definition_indentation}def {name}({parameters}):{NEWLINE}{extracted}{NEWLINE}",
        )

        replace = Edit(TextRange(start=start, end=end), text=replace_text)
        return (insert, replace)

    def get_parameter_names(
        self,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        text_range: types.TextRange,
        start_of_current_scope: Position,
    ) -> list[str]:
        parameter_names = [
            name
            for position, name in self.find_names_defined_before_range(
                names_in_range, text_range
            )
            if position >= start_of_current_scope
            or passed_as_argument_within(name, text_range)
        ]

        return parameter_names

    def extract_expression(
        self,
        text_range: types.TextRange,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        new_indentation: str,
    ) -> tuple[str, str]:
        extracted = text_range.text.strip()
        extracted = f"{new_indentation}return {extracted}"
        assignment = ""

        return extracted, assignment

    def extract_statements(
        self,
        text_range: types.TextRange,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        new_indentation: str,
    ) -> tuple[str, str]:
        return_values = self.get_return_values(
            names_in_range=names_in_range, end=text_range.end
        )
        extracted = NEWLINE.join(
            [
                f"{new_indentation}{line}".rstrip()
                for line in dedent(text_range.text).split(NEWLINE)
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
            (
                p
                for _, p in self.find_names_used_after_position(
                    names_defined_in_range, last.end
                )
            ),
            None,
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


def find_start_of_scope(start: Position) -> Position:
    enclosing = start.source.get_largest_enclosing_scope_range(start)
    if enclosing is None:
        return start.source.position(0, 0)

    return enclosing.start


def find_callable_insert_point(start: Position, is_global: bool = False) -> Position:
    enclosing = (
        start.source.get_largest_enclosing_scope_range(start)
        if is_global
        else start.source.get_enclosing_function_range(start)
    )

    if enclosing is None:
        return start.source.position(start.row, 0)

    return start.source.position(enclosing.end.row + 1, 0)


def get_indentation(at: Position) -> str:
    text = at.source.lines[at.row].text
    if not (indentation_match := INDENTATION.match(text)):
        return ""

    if not (groups := indentation_match.groups()):
        return ""

    return groups[0]


def get_single_expression_value(text: str) -> ast.AST | None:
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return None

    if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.Expr):
        return None

    return parsed.body[0].value


def make_edit(source: Source, node: ast.AST, length: int, new_text: str) -> Edit:
    start = source.node_position(node)
    return Edit(TextRange(start=start, end=start + (length - 1)), text=new_text)


def make_insert(at: Position, text: str) -> Edit:
    return Edit(TextRange(start=at, end=at), text=text)


def make_delete(start: Position, end: Position) -> Edit:
    return Edit(TextRange(start=start, end=end), text="")


def get_body_for_callable(at: Position) -> Sequence[Line]:
    source = at.source
    for definition in find_functions(source.get_ast(), up_to=at):
        if source.node_position(definition) == (at - 4):
            found = definition
            break
    else:
        return []

    children = list(found.body)
    first_row = source.node_position(children[0]).row
    if end_position := source.node_end_position(children[-1]):
        last_row = end_position.row
    else:
        last_row = first_row

    return source.lines[first_row : last_row + 1]


def passed_as_argument_within(name: str, text_range: types.TextRange) -> bool:
    for n in find_arguments_passed_in_range(
        text_range.start.source.get_ast(), text_range
    ):
        if n == name:
            return True
    return False
