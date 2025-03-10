import ast
import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from textwrap import dedent, indent
from typing import Protocol

from breakfast import types
from breakfast.names import all_occurrence_positions, build_graph, find_definition
from breakfast.scope_graph import ScopeGraph
from breakfast.search import find_other_occurrences, find_returns, find_statements
from breakfast.source import TextRange
from breakfast.types import Edit, NotFoundError, Position

logger = logging.getLogger(__name__)

FOUR_SPACES = "    "
NEWLINE = "\n"


class CodeSelection:
    def __init__(self, text_range: types.TextRange):
        self.text_range = text_range
        self.source = self.text_range.source
        self.scope_graph = build_graph([self.source], follow_redefinitions=False)

    def extract_variable(self, name: str) -> tuple[Edit, ...]:
        refactoring = ExtractVariable(self, name)
        return refactoring.edits

    @property
    def inside_method(self) -> bool:
        if self.text_range.enclosing_scopes:
            _, global_scope_range = self.text_range.enclosing_scopes[0]
        else:
            global_scope_range = None

        if not global_scope_range:
            return False

        in_method = global_scope_range.start.line.text.strip().startswith("class")
        return in_method

    def inline_variable(self) -> tuple[Edit, ...]:
        refactoring = InlineVariable(self)
        return refactoring.edits

    def inline_call(self, name: str = "result") -> tuple[Edit, ...]:
        refactoring = InlineCall(self, name)
        return refactoring.edits

    def extract_method(self, name: str) -> tuple[Edit, ...]:
        return self.extract_callable(name=name, is_method=True)

    def extract_function(self, name: str) -> tuple[Edit, ...]:
        return self.extract_callable(name=name)

    def extract_callable(
        self,
        name: str,
        is_method: bool = False,
    ) -> tuple[Edit, ...]:
        start, end = self.text_range.start, self.text_range.end
        original_indentation = start.indentation
        new_indentation = original_indentation if is_method else FOUR_SPACES

        names_in_range = self.full_line_range.names

        extracting_partial_line = start.row == end.row and start.column != 0
        if extracting_partial_line:
            extraction_range = self.text_range
            extractor = self.extract_expression
        else:
            extraction_range = self.full_line_range
            extractor = self.extract_statements

        extracted, assignment_or_return = extractor(
            text_range=extraction_range,
            names_in_range=names_in_range,
            new_indentation=new_indentation,
        )
        start_of_current_scope = self.find_start_of_scope(start=start)
        parameter_names: Sequence[str] = self.get_parameter_names(
            names_in_range=names_in_range,
            text_range=self.full_line_range,
            start_of_current_scope=start_of_current_scope,
        )
        self_name = "self" if is_method else None
        arguments = ", ".join(f"{n}={n}" for n in parameter_names if n != self_name)
        self_prefix = (self_name + ".") if self_name else ""

        call = f"{self_prefix}{name}({arguments})"
        replace_text = (
            call
            if extracting_partial_line
            else f"{original_indentation}{assignment_or_return}{call}{NEWLINE}"
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

        insert_position = self.find_callable_insert_point(
            start=start, is_global=not is_method
        )
        return (
            Edit(
                TextRange(insert_position, insert_position),
                text=f"{NEWLINE}{static_method}{definition_indentation}def {name}({parameters}):{NEWLINE}{extracted}{NEWLINE}",
            ),
            Edit(TextRange(start, end), text=replace_text),
        )

    def get_parameter_names(
        self,
        names_in_range: Sequence[tuple[str, Position, ast.expr_context]],
        text_range: types.TextRange,
        start_of_current_scope: Position,
    ) -> list[str]:
        return [
            name
            for position, name in self.find_names_defined_before_range(
                names_in_range, text_range
            )
            if position >= start_of_current_scope
            # If we are extracting code that passes a name as an argument to a another
            # function, it is very likely that we want to receive that as an argument as
            # well, rather than close over it or get it from the global scope:
            or text_range.contains_as_argument(name)
        ]

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
            assignment_or_return = f"{return_values_as_string} = "
        else:
            # XXX: Pretty sure this is way too simplistic
            assignment_or_return = "return "

        return extracted, assignment_or_return

    def slide_statements_down(self) -> tuple[Edit, ...]:
        refactoring = SlideStatements(self, SlideStatements.find_slide_target_after)
        return refactoring.edits

    def slide_statements_up(self) -> tuple[Edit, ...]:
        refactoring = SlideStatements(self, SlideStatements.find_slide_target_before)
        return refactoring.edits

    @property
    def full_line_range(self) -> types.TextRange:
        start = self.text_range.start
        end = self.text_range.end
        if start.row < end.row:
            start = start.start_of_line
            end = end.line.next.start if end.line.next else end

        return TextRange(start, end)

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
            for n, _ in find_names_used_after_position(
                [(n, p) for n, p, _ in names_in_range], self.scope_graph, end
            )
        }
        seen = set()
        return_values = []
        for name in names_modified_in_body:
            if name in names_used_after and name not in seen:
                seen.add(name)
                return_values.append(name)

        return return_values

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

    def find_start_of_scope(self, start: Position) -> Position:
        if not self.text_range.enclosing_scopes:
            return start.source.position(0, 0)

        node, global_scope_range = self.text_range.enclosing_scopes[0]

        return global_scope_range.start

    def find_callable_insert_point(
        self, start: Position, is_global: bool = False
    ) -> Position:
        if not self.text_range.enclosing_scopes:
            return start.source.position(start.row, 0)

        node, enclosing = (
            self.text_range.enclosing_scopes[0]
            if is_global
            else self.text_range.enclosing_scopes[-1]
        )

        return start.source.position(enclosing.end.row + 1, 0)


class Refactoring(Protocol):
    def __init__(self, selection: CodeSelection): ...
    @property
    def edits(self) -> tuple[Edit, ...]: ...


class ExtractVariable:
    def __init__(self, code_selection: CodeSelection, name: str):
        self.text_range = code_selection.text_range
        self.name = name
        self.source = self.text_range.start.source

    @property
    def edits(self) -> tuple[Edit, ...]:
        extracted = self.text_range.text

        if not (expression := self.get_single_expression_value(extracted)):
            logger.warning("Could not extract single expression value.")
            return ()

        other_occurrences = find_other_occurrences(
            source_ast=self.source.ast, node=expression, position=self.text_range.start
        )
        other_edits = [
            TextRange(
                (start := self.source.node_position(o)), start + len(extracted)
            ).replace(self.name)
            for o in other_occurrences
        ]
        edits = sorted(
            [
                Edit(text_range=self.text_range, text=self.name),
                *other_edits,
            ]
        )
        first_edit_position = edits[0].start

        statement_start = None
        for statement in find_statements(self.source.ast):
            if (
                statement_position := self.source.node_position(statement)
            ) < first_edit_position:
                statement_start = statement_position
            else:
                break

        insert_point = statement_start or first_edit_position.start_of_line
        indentation = " " * insert_point.column
        definition = f"{self.name} = {extracted}{NEWLINE}{indentation}"
        insert = insert_point.insert(definition)
        return (insert, *edits)

    @staticmethod
    def get_single_expression_value(text: str) -> ast.AST | None:
        try:
            parsed = ast.parse(text)
        except SyntaxError:
            return None

        if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.Expr):
            return None

        return parsed.body[0].value


class InlineVariable:
    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.source = self.text_range.start.source
        self.scope_graph = code_selection.scope_graph
        self.enclosing_assignment = code_selection.text_range.enclosing_assignment

    @property
    def edits(self) -> tuple[Edit, ...]:
        if self.enclosing_assignment is None:
            logger.warn("No enclosing assignment.")
            return ()

        assignment, assignment_range = self.enclosing_assignment
        value_range = assignment_range.start.source.node_range(assignment.value)
        if value_range is None:
            logger.warn("Could not determine range.")
            return ()

        position = assignment_range.start

        name = self.source.get_name_at(position)
        assignment_value = value_range.text

        try:
            occurrences = all_occurrence_positions(position, graph=self.scope_graph)
        except NotFoundError:
            logger.warn("Could not determine range.")
            return ()

        edits = (
            Edit(
                TextRange(occurrence, occurrence + len(name)),
                text=assignment_value,
            )
            for occurrence in occurrences
            if occurrence != position
        )

        delete = Edit(
            TextRange(
                value_range.start.line.start,
                value_range.end.line.next.start
                if value_range.end.line.next
                else position.line.end,
            ),
            text="",
        )

        return (delete, *edits) if edits else ()


class InlineCall:
    def __init__(self, code_selection: CodeSelection, name: str = "result"):
        self.text_range = code_selection.text_range
        self.source = self.text_range.start.source
        self.scope_graph = code_selection.scope_graph
        self.enclosing_call = code_selection.text_range.enclosing_call
        self.name = name

    @property
    def edits(self) -> tuple[Edit, ...]:
        if not self.enclosing_call:
            logger.warn("No enclosing call.")
            return ()

        call, call_range = self.enclosing_call

        name_start = call_range.start
        call_args = call.args
        if isinstance(call.func, ast.Attribute):
            call_args = [call.func.value, *call_args]
            if call.func.value.end_col_offset and call.func.col_offset:
                name_start += (
                    call.func.value.end_col_offset - call.func.col_offset
                ) + 1

        definition = find_definition(self.scope_graph, name_start)
        if definition is None or definition.position is None:
            logger.warn(f"No definition position {definition=}.")
            return ()
        if not isinstance(definition.ast, ast.FunctionDef):
            logger.warn(f"Not a function {definition.ast=}.")
            return ()

        returns = [
            r for statement in definition.ast.body for r in find_returns(statement)
        ]

        body_range = definition.position.body_for_callable
        if not body_range:
            return ()

        substitutions = []
        seen = set()

        for keyword in call.keywords:
            def_arg: ast.keyword | ast.arg = keyword
            seen.add(def_arg.arg)
            call_arg = keyword.value
            substitutions.extend(
                list(self.get_substitions(def_arg, call_arg, body_range))
            )

        for call_arg, def_arg in zip(
            call_args,
            (a for a in definition.ast.args.args if a.arg not in seen),
            strict=True,
        ):
            substitutions.extend(
                list(self.get_substitions(def_arg, call_arg, body_range))
            )

        new_lines = body_range.text_with_substitutions(substitutions)

        if (
            len(returns) == 1
            and returns[0].value
            and (return_range := self.source.node_range(returns[0]))
        ):
            n = (return_range.start.row - body_range.start.row) - len(new_lines)

            insert_range = TextRange(
                self.text_range.start.line.start, self.text_range.start.line.start
            )
            indentation = self.text_range.start.indentation
            return_value = " ".join(
                " ".join(return_range.text_with_substitutions(substitutions))
                .strip()
                .split()[1:]
            )

            body = (
                indent(
                    dedent(NEWLINE.join(line for line in new_lines[:n]) + NEWLINE),
                    indentation,
                )
                + f"{indentation}{self.name} = {return_value}{NEWLINE}"
            )
            replace = Edit(call_range, text=self.name)
            return (
                Edit(
                    insert_range,
                    text=f"{body}",
                ),
                replace,
            )
        else:
            indentation = self.text_range.start.indentation
            body = indent(
                dedent(NEWLINE.join(line for line in new_lines) + NEWLINE),
                indentation,
            )
            return (
                Edit(
                    call_range,
                    text=f"{body}",
                ),
            )

    def get_substitions(
        self,
        def_arg: ast.keyword | ast.arg,
        call_arg: ast.expr,
        body_range: types.TextRange,
    ) -> Iterator[tuple[types.TextRange, str]]:
        assert def_arg.arg is not None  # noqa: S101
        arg_position = self.source.node_position(def_arg)
        value = (
            call_arg_range.text
            if (call_arg_range := self.source.node_range(call_arg))
            else ""
        )

        for position in all_occurrence_positions(arg_position):
            if position not in body_range:
                continue
            yield TextRange(position, position + len(def_arg.arg)), value


def get_return_value(new_lines: Sequence[str]) -> str:
    for line in new_lines[::-1]:
        if (stripped := line.strip()).startswith("return "):
            return stripped[len("return ") :]

    raise NotFoundError("No return found")


class SlideStatements:
    def __init__(
        self,
        code_selection: CodeSelection,
        find_target: Callable[[CodeSelection], Position | None],
    ):
        self.text_range = code_selection.text_range
        self.code_selection = code_selection
        self.find_target = find_target

    @property
    def edits(self) -> tuple[Edit, ...]:
        target = self.find_target(self.code_selection)
        if target is None:
            return ()

        first, last = self.text_range.start.line, self.text_range.end.line
        insert = target.insert(first.start.through(last.end).text + NEWLINE)
        delete = TextRange(
            first.start, last.next.start if last.next else last.end
        ).replace("")
        return (insert, delete)

    @staticmethod
    def find_slide_target_after(selection: CodeSelection) -> Position | None:
        first, last = selection.text_range.start.line, selection.text_range.end.line
        lines = TextRange(first.start, last.end)
        names_defined_in_range = lines.definitions
        first_usage_after_range = next(
            (
                p
                for _, p in find_names_used_after_position(
                    names_defined_in_range, selection.scope_graph, last.end
                )
            ),
            None,
        )
        if not first_usage_after_range:
            return None

        enclosing_nodes = TextRange(
            first_usage_after_range, first_usage_after_range
        ).enclosing_nodes
        origin_nodes = lines.enclosing_nodes
        index = 0
        while (
            index < len(origin_nodes)
            and origin_nodes[index][1] == enclosing_nodes[index][1]
        ):
            index += 1

        first_usage_after_range = enclosing_nodes[index][1].start

        if first_usage_after_range and first_usage_after_range.row > last.row + 1:
            return first_usage_after_range.start_of_line

        return None

    @staticmethod
    def find_slide_target_before(selection: CodeSelection) -> Position | None:
        first, last = selection.text_range.start.line, selection.text_range.end.line
        original_indentation = first.start.indentation
        line = first
        while line.previous and len(line.previous.start.indentation) >= len(
            original_indentation
        ):
            line = line.previous
        if line == first:
            return None

        scope_before = TextRange(
            line.start, first.previous.end if first.previous else first.start
        )

        text_range = TextRange(first.start, last.end)
        names_in_range = {n for n, _, _ in text_range.names}
        target = max(
            [
                line.start,
                *(
                    position.line.next.start
                    if position.line.next
                    else position.line.end
                    for name, position, _ in scope_before.names
                    if name in names_in_range
                ),
            ]
        )

        return target


def find_names_used_after_position(
    names: Sequence[tuple[str, Position]],
    scope_graph: ScopeGraph,
    cutoff: Position,
) -> Iterable[tuple[str, Position]]:
    for name, position in names:
        try:
            occurrences = all_occurrence_positions(position, graph=scope_graph)
        except NotFoundError:
            continue
        for occurrence in occurrences:
            if occurrence > cutoff:
                yield name, occurrence
                break
