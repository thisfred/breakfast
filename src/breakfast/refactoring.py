import ast
import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from itertools import dropwhile, takewhile
from textwrap import dedent, indent
from typing import ClassVar, Protocol

from breakfast import types
from breakfast.code_generation import to_source, unparse
from breakfast.names import (
    Occurrence,
    all_occurrences,
    build_graph,
    find_definition,
)
from breakfast.scope_graph import NodeType, ScopeGraph
from breakfast.search import (
    find_other_occurrences,
    find_returns,
    find_statements,
)
from breakfast.types import Edit, NotFoundError, Position

logger = logging.getLogger(__name__)

FOUR_SPACES = "    "
NEWLINE = "\n"


def register(refactoring: "type[Refactoring]") -> "type[Refactoring]":
    CodeSelection.register_refactoring(refactoring)
    return refactoring


class CodeSelection:
    _refactorings: ClassVar[dict[str, "type[Refactoring]"]] = {}

    def __init__(self, text_range: types.TextRange):
        self.text_range = text_range
        self.source = self.text_range.source
        self.scope_graph = build_graph(
            [self.source], follow_redefinitions=False
        )

    @classmethod
    def register_refactoring(cls, refactoring: "type[Refactoring]") -> None:
        cls._refactorings[refactoring.name] = refactoring

    def extract_variable(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["extract variable"](self)
        return refactoring.edits

    def extract_function(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["extract function"](self)
        return refactoring.edits

    def extract_method(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["extract method"](self)
        return refactoring.edits

    def inline_variable(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["inline variable"](self)
        return refactoring.edits

    def inline_call(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["inline call"](self)
        return refactoring.edits

    def slide_statements_down(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["slide statements down"](self)
        return refactoring.edits

    def slide_statements_up(self) -> tuple[Edit, ...]:
        refactoring = self._refactorings["slide statements up"](self)
        return refactoring.edits

    @property
    def inside_method(self) -> bool:
        global_scope = (
            scopes[0]
            if (scopes := self.text_range.enclosing_scopes())
            else None
        )

        if not global_scope:
            return False

        in_method = isinstance(global_scope.node, ast.ClassDef)
        return in_method

    @property
    def cursor(self) -> types.Position:
        return self.text_range.start

    def occurrences_of_name_at(
        self, position: Position
    ) -> Sequence[Occurrence]:
        return all_occurrences(position, graph=self.scope_graph)

    @property
    def full_line_range(self) -> types.TextRange:
        start = self.text_range.start
        end = self.text_range.end
        if start.row < end.row:
            start = start.start_of_line
            end = end.line.next.start if end.line.next else end

        return start.to(end)

    def find_usages_after_position(
        self,
        occurrences: Sequence[Occurrence],
        scope_graph: ScopeGraph,
        cutoff: Position,
    ) -> Iterator[Occurrence]:
        for occurrence in occurrences:
            try:
                usages = all_occurrences(occurrence.position, graph=scope_graph)
            except NotFoundError:
                continue
            for usage in usages:
                if usage.position > cutoff:
                    yield usage
                    break

    def find_names_used_after_position(
        self,
        names: Sequence[Occurrence],
        scope_graph: ScopeGraph,
        cutoff: Position,
    ) -> Iterator[Occurrence]:
        for name_occurrence in names:
            try:
                occurrences = all_occurrences(
                    name_occurrence.position, graph=scope_graph
                )
            except NotFoundError:
                continue
            for occurrence in occurrences:
                if occurrence.position > cutoff:
                    yield occurrence
                    break


class Refactoring(Protocol):
    name: str

    def __init__(self, selection: CodeSelection): ...
    @property
    def edits(self) -> tuple[Edit, ...]: ...


class ExtractCallable:
    def __init__(self, code_selection: CodeSelection):
        self.code_selection = code_selection

    def find_callable_insert_point(
        self, start: Position, is_global: bool = False
    ) -> Position:
        if not self.code_selection.text_range.enclosing_scopes():
            return start.source.position(start.row, 0)

        enclosing = (
            self.code_selection.text_range.enclosing_scopes()[0]
            if is_global
            else self.code_selection.text_range.enclosing_scopes()[-1]
        )

        return start.source.position(enclosing.range.end.row + 1, 0)

    def find_start_of_scope(self, start: Position) -> Position:
        if not self.code_selection.text_range.enclosing_scopes():
            return start.source.position(0, 0)

        global_scope = self.code_selection.text_range.enclosing_scopes()[0]

        return global_scope.range.start

    def get_parameter_names(
        self,
        names_in_range: Sequence[Occurrence],
        text_range: types.TextRange,
        start_of_current_scope: Position,
    ) -> list[str]:
        return [
            occurrence.name
            for occurrence in self.find_names_defined_before_range(
                names_in_range, text_range
            )
            if occurrence.position >= start_of_current_scope
            # If we are extracting code that passes a name as an argument to a another
            # function, it is very likely that we want to receive that as an argument as
            # well, rather than close over it or get it from the global scope:
            or text_range.contains_as_argument(occurrence.name)
        ]

    def find_names_defined_before_range(
        self,
        names: Sequence[Occurrence],
        text_range: types.TextRange,
    ) -> Iterable[Occurrence]:
        found = set()
        for name_occurrence in names:
            if name_occurrence.name in found:
                continue
            try:
                occurrences = all_occurrences(
                    name_occurrence.position,
                    graph=self.code_selection.scope_graph,
                )
            except NotFoundError:
                continue
            for occurrence in occurrences:
                if occurrence.position < text_range.start:
                    found.add(name_occurrence.name)
                    yield occurrence
                break


class Extractor(Protocol):
    def __init__(
        self,
        code_selection: CodeSelection,
        new_indentation: str,
    ): ...

    def extract(self) -> tuple[str, str]: ...


class ExpressionExtractor:
    def __init__(
        self,
        code_selection: CodeSelection,
        new_indentation: str,
    ):
        self.code_selection = code_selection
        self.text_range = code_selection.text_range
        self.names_in_range = self.code_selection.full_line_range.names
        self.new_indentation = new_indentation

    def extract(self) -> tuple[str, str]:
        extracted = self.text_range.text.strip()
        nodes = list(self.text_range.statements)
        assignment = ""
        match nodes:
            case [ast.Assign(targets=targets)]:
                level = len(self.new_indentation) // 4
                module = ast.Module(body=nodes, type_ignores=[])
                if all(isinstance(t, ast.Attribute) for t in targets):
                    extracted = "".join(to_source(module, level))
                else:
                    non_attribute_vars = [
                        unparse(t)
                        for t in targets
                        if not isinstance(t, ast.Attribute)
                    ]
                    assignment = " = ".join(non_attribute_vars)
                    extracted = "".join(to_source(module, level))
                    extracted = f"{extracted}\n{self.new_indentation}return {non_attribute_vars[0]}"
            case _:
                extracted = f"{self.new_indentation}result = {extracted}\n{self.new_indentation}return result"

        return extracted, assignment


class StatementsExtractor:
    def __init__(
        self,
        code_selection: CodeSelection,
        new_indentation: str,
    ):
        self.code_selection = code_selection
        self.text_range = code_selection.full_line_range
        self.names_in_range = self.code_selection.full_line_range.names
        self.new_indentation = new_indentation

    def extract(self) -> tuple[str, str]:
        return_values = self.get_return_values(
            names_in_range=self.names_in_range, end=self.text_range.end
        )

        nodes = list(self.text_range.statements)
        has_returns = any(
            found for node in nodes for found in find_returns(node)
        )

        level = len(self.new_indentation) // 4
        module = ast.Module(body=nodes, type_ignores=[])

        extracted = "".join(to_source(module, level))

        if return_values:
            return_values_as_string = f'{", ".join(return_values)}'
            extracted += f"{NEWLINE}{self.new_indentation}return {return_values_as_string}"
            assignment_or_return = f"{return_values_as_string} = "
        elif has_returns:
            assignment_or_return = "return "
        else:
            assignment_or_return = ""

        return extracted, assignment_or_return

    def get_return_values(
        self,
        names_in_range: Sequence[Occurrence],
        end: Position,
    ) -> list[str]:
        names_used_after = {
            occurrence.name
            for occurrence in self.code_selection.find_names_used_after_position(
                names_in_range, self.code_selection.scope_graph, end
            )
        }
        seen = set()
        return_values = []
        names_modified_in_body = [
            occurrence.name
            for occurrence in names_in_range
            if occurrence.node_type is NodeType.DEFINITION
        ]
        for name in names_modified_in_body:
            if name in names_used_after and name not in seen:
                seen.add(name)
                return_values.append(name)

        return return_values


@register
class ExtractFunction(ExtractCallable):
    name = "extract function"

    @property
    def edits(self) -> tuple[Edit, ...]:
        return self.extract_callable("function")

    def extract_callable(
        self,
        name: str,
    ) -> tuple[Edit, ...]:
        start, end = (
            self.code_selection.text_range.start,
            self.code_selection.text_range.end,
        )
        original_indentation = start.indentation
        new_indentation = FOUR_SPACES

        extracting_partial_line = start.row == end.row and start.column != 0
        extractor = (
            ExpressionExtractor
            if extracting_partial_line
            else StatementsExtractor
        )

        extracted, assignment_or_return = extractor(
            code_selection=self.code_selection,
            new_indentation=new_indentation,
        ).extract()

        start_of_current_scope = self.find_start_of_scope(start=start)
        names_in_range = self.code_selection.full_line_range.names
        parameter_names: Sequence[str] = self.get_parameter_names(
            names_in_range=names_in_range,
            text_range=self.code_selection.full_line_range,
            start_of_current_scope=start_of_current_scope,
        )

        arguments = ", ".join(f"{n}={n}" for n in parameter_names)
        call = f"{name}({arguments})"
        replace_text = (
            call
            if extracting_partial_line
            else f"{original_indentation}{assignment_or_return}{call}{NEWLINE}"
        )

        parameters = ", ".join(parameter_names)

        insert_position = self.find_callable_insert_point(
            start=start, is_global=True
        )
        edits = (
            Edit(
                insert_position.as_range,
                text=f"{NEWLINE}def {name}({parameters}):{NEWLINE}{extracted}{NEWLINE}",
            ),
            Edit(start.to(end), text=replace_text),
        )
        return edits


@register
class ExtractMethod(ExtractCallable):
    name = "extract method"

    @property
    def edits(self) -> tuple[Edit, ...]:
        return self.extract_callable("method")

    def extract_callable(
        self,
        name: str,
    ) -> tuple[Edit, ...]:
        start, end = (
            self.code_selection.text_range.start,
            self.code_selection.text_range.end,
        )
        original_indentation = start.indentation
        new_indentation = original_indentation

        extracting_partial_line = start.row == end.row and start.column != 0
        extractor = (
            ExpressionExtractor
            if extracting_partial_line
            else StatementsExtractor
        )

        extracted, assignment_or_return = extractor(
            code_selection=self.code_selection,
            new_indentation=new_indentation,
        ).extract()

        start_of_current_scope = self.find_start_of_scope(start=start)
        names_in_range = self.code_selection.full_line_range.names
        parameter_names = self.get_parameter_names(
            names_in_range=names_in_range,
            text_range=self.code_selection.full_line_range,
            start_of_current_scope=start_of_current_scope,
        )
        self_name = "self"
        arguments = ", ".join(
            f"{n}={n}" for n in parameter_names if n != self_name
        )
        self_prefix = self_name + "."

        call = f"{self_prefix}{name}({arguments})"
        replace_text = (
            call
            if extracting_partial_line
            else f"{original_indentation}{assignment_or_return}{call}{NEWLINE}"
        )

        parameters_with_self = [
            self_name,
            *(n for n in parameter_names if n != self_name),
        ]

        definition_indentation = original_indentation[:-4]
        if self_name in parameter_names:
            static_method = ""
            parameters = ", ".join(parameters_with_self)
        else:
            static_method = f"{definition_indentation}@staticmethod{NEWLINE}"
            parameters = ", ".join(parameter_names)

        insert_position = self.find_callable_insert_point(
            start=start, is_global=False
        )
        edits = (
            Edit(
                insert_position.as_range,
                text=f"{NEWLINE}{static_method}{definition_indentation}def {name}({parameters}):{NEWLINE}{extracted}{NEWLINE}",
            ),
            Edit(start.to(end), text=replace_text),
        )
        return edits


@register
class ExtractVariable:
    name = "extract variable"

    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.source = self.text_range.start.source

    @property
    def edits(self) -> tuple[Edit, ...]:
        name = "result"
        extracted = self.text_range.text

        if not (expression := self.get_single_expression_value(extracted)):
            logger.warning("Could not extract single expression value.")
            return ()

        other_occurrences = find_other_occurrences(
            source_ast=self.source.ast,
            node=expression,
            position=self.text_range.start,
        )
        other_edits = [
            (start := self.source.node_position(o))
            .to(start + len(extracted))
            .replace(name)
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
        for statement in find_statements(self.source.ast):
            if (
                statement_position := self.source.node_position(statement)
            ) < first_edit_position:
                statement_start = statement_position
            else:
                break

        insert_point = statement_start or first_edit_position.start_of_line
        indentation = " " * insert_point.column
        definition = f"{name} = {extracted}{NEWLINE}{indentation}"
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


@register
class InlineVariable:
    name = "inline variable"

    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.cursor = code_selection.cursor
        self.source = self.text_range.start.source
        self.code_selection = code_selection

    @property
    def edits(self) -> tuple[Edit, ...]:
        grouped: dict[bool, list[Occurrence]] = defaultdict(list)
        for o in self.code_selection.occurrences_of_name_at(self.cursor):
            grouped[o.node_type is NodeType.DEFINITION].append(o)

        last_definition = grouped.get(True, [None])[-1]

        if last_definition is None:
            logger.warning("Could not find definition.")
            return ()
        assignment = last_definition.position.as_range.enclosing_assignment()
        if assignment is None:
            logger.warning("Could not find assignment for definition.")
            return ()

        name = self.source.get_name_at(self.cursor)
        if self.cursor in assignment.range:
            after_cursor = (
                o
                for o in dropwhile(
                    lambda x: x.position <= self.cursor,
                    self.code_selection.occurrences_of_name_at(self.cursor),
                )
            )
            to_replace: tuple[types.TextRange, ...] = tuple(
                o.position.to(o.position + len(name))
                for o in takewhile(
                    lambda x: x.node_type is not NodeType.DEFINITION,
                    after_cursor,
                )
                if o.position > self.cursor
            )
        else:
            to_replace = (self.cursor.to(self.cursor + len(name)),)

        if self.cursor in assignment.range:
            can_remove_last_definition = True
        else:
            other_occurrences = [
                o
                for o in grouped.get(False, [])
                if o.position not in self.text_range
            ]
            last_occurrence = (
                other_occurrences[-1] if other_occurrences else None
            )
            can_remove_last_definition = (
                last_occurrence is None
                or last_occurrence.position < assignment.range.start
            )

        edits: tuple[Edit, ...] = tuple(
            Edit(name_range, text=unparse(assignment.node.value))
            for name_range in to_replace
        )

        if can_remove_last_definition:
            if len(assignment.node.targets) == 1:
                delete = Edit(assignment.range, text="")
            else:
                assignment.node.targets = [
                    t
                    for t in assignment.node.targets
                    if isinstance(t, ast.Name) and t.id != name
                ]
                delete = Edit(assignment.range, text=unparse(assignment.node))

            edits = (*edits, delete)

        return edits


@register
class InlineCall:
    name = "inline call"

    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.source = self.text_range.start.source
        self.scope_graph = code_selection.scope_graph
        self.enclosing_call = code_selection.text_range.enclosing_call

    @property
    def edits(self) -> tuple[Edit, ...]:
        call = self.enclosing_call()
        if not call:
            logger.warn("No enclosing call.")
            return ()

        name_start = call.range.start
        call_args = call.node.args
        if isinstance(call.node.func, ast.Attribute):
            call_args = [call.node.func.value, *call_args]
            if (
                call.node.func.value.end_col_offset
                and call.node.func.col_offset
            ):
                name_start += (
                    call.node.func.value.end_col_offset
                    - call.node.func.col_offset
                ) + 1

        definition = find_definition(self.scope_graph, name_start)
        if definition is None or definition.position is None:
            logger.warn(f"No definition position {definition=}.")
            return ()
        if not isinstance(definition.ast, ast.FunctionDef):
            logger.warn(f"Not a function {definition.ast=}.")
            return ()

        body_range = definition.position.body_for_callable
        if not body_range:
            return ()

        new_lines = self.get_new_lines(
            call=call.node, body_range=body_range, definition_ast=definition.ast
        )

        return_ranges = [
            return_range
            for statement in definition.ast.body
            for r in find_returns(statement)
            if (return_range := self.source.node_range(r))
        ]

        indentation = self.text_range.start.indentation
        name = "result"
        for return_range in return_ranges:
            offset = return_range.start.row - body_range.start.row
            new_lines[offset] = new_lines[offset].replace(
                "return ", f"{name} = ", 1
            )
        body = indent(
            dedent(NEWLINE.join(new_lines) + NEWLINE),
            indentation,
        )
        if not return_ranges:
            return (
                Edit(
                    call.range,
                    text=f"{body}",
                ),
            )

        insert_range = self.text_range.start.line.start.as_range

        return (
            Edit(
                insert_range,
                text=f"{body}",
            ),
            Edit(call.range, text=name),
        )

    def get_new_lines(
        self,
        *,
        call: ast.Call,
        body_range: types.TextRange,
        definition_ast: ast.FunctionDef,
    ) -> list[str]:
        substitutions = []
        seen = set()

        call_args = call.args
        if isinstance(call.func, ast.Attribute):
            call_args = [call.func.value, *call_args]
        for keyword in call.keywords:
            def_arg: ast.keyword | ast.arg = keyword
            seen.add(def_arg.arg)
            call_arg = keyword.value
            substitutions.extend(
                list(self.get_substitions(def_arg, call_arg, body_range))
            )

        for call_arg, def_arg in zip(
            call_args,
            (a for a in definition_ast.args.args if a.arg not in seen),
            strict=True,
        ):
            substitutions.extend(
                list(self.get_substitions(def_arg, call_arg, body_range))
            )

        return list(body_range.text_with_substitutions(substitutions))

    def get_substitions(
        self,
        def_arg: ast.keyword | ast.arg,
        call_arg: ast.expr,
        body_range: types.TextRange,
    ) -> Iterator[types.Edit]:
        assert def_arg.arg is not None  # noqa: S101
        arg_position = self.source.node_position(def_arg)
        value = (
            call_arg_range.text
            if (call_arg_range := self.source.node_range(call_arg))
            else ""
        )

        for occurrence in all_occurrences(arg_position):
            if occurrence.position not in body_range:
                continue
            yield Edit(
                occurrence.position.to(occurrence.position + len(def_arg.arg)),
                value,
            )


def get_return_value(new_lines: Sequence[str]) -> str:
    for line in new_lines[::-1]:
        if (stripped := line.strip()).startswith("return "):
            return stripped[len("return ") :]

    raise NotFoundError("No return found")


@register
class SlideStatementsUp:
    name = "slide statements up"

    def __init__(
        self,
        code_selection: CodeSelection,
    ):
        self.text_range = code_selection.text_range
        self.code_selection = code_selection

    @property
    def edits(self) -> tuple[Edit, ...]:
        target = self.find_slide_target_before()
        if target is None:
            return ()

        first, last = self.text_range.start.line, self.text_range.end.line
        insert = target.insert(first.start.through(last.end).text + NEWLINE)
        delete = first.start.to(
            last.next.start if last.next else last.end
        ).replace("")
        return (insert, delete)

    def find_slide_target_before(self) -> Position | None:
        first, last = (
            self.code_selection.text_range.start.line,
            self.code_selection.text_range.end.line,
        )
        original_indentation = first.start.indentation
        line = first
        while line.previous and len(line.previous.start.indentation) >= len(
            original_indentation
        ):
            line = line.previous
        if line == first:
            return None

        scope_before = line.start.to(
            first.previous.end if first.previous else first.start
        )

        text_range = first.start.to(last.end)
        names_in_range = {occurrence.name for occurrence in text_range.names}
        target = max(
            [
                line.start,
                *(
                    occurrence.position.line.next.start
                    if occurrence.position.line.next
                    else occurrence.position.line.end
                    for occurrence in scope_before.names
                    if occurrence.name in names_in_range
                ),
            ]
        )

        return target


@register
class SlideStatementsDown:
    name = "slide statements down"

    def __init__(
        self,
        code_selection: CodeSelection,
    ):
        self.text_range = code_selection.text_range
        self.code_selection = code_selection

    @property
    def edits(self) -> tuple[Edit, ...]:
        target = self.find_slide_target_after()
        if target is None:
            return ()

        first, last = self.text_range.start.line, self.text_range.end.line
        insert = target.insert(first.start.through(last.end).text + NEWLINE)
        delete = first.start.to(
            last.next.start if last.next else last.end
        ).replace("")
        return (insert, delete)

    def find_slide_target_after(self) -> Position | None:
        first, last = (
            self.code_selection.text_range.start.line,
            self.code_selection.text_range.end.line,
        )
        lines = first.start.to(last.end)
        names_defined_in_range = lines.definitions
        first_usage_after_range = next(
            (
                o.position
                for o in self.code_selection.find_names_used_after_position(
                    names_defined_in_range,
                    self.code_selection.scope_graph,
                    last.end,
                )
            ),
            None,
        )
        if not first_usage_after_range:
            return None

        enclosing_nodes = first_usage_after_range.as_range.enclosing_nodes()
        origin_nodes = lines.enclosing_nodes()
        index = 0
        while (
            index < len(origin_nodes)
            and origin_nodes[index].range == enclosing_nodes[index].range
        ):
            index += 1

        first_usage_after_range = enclosing_nodes[index].range.start

        if (
            first_usage_after_range
            and first_usage_after_range.row > last.row + 1
        ):
            return first_usage_after_range.start_of_line

        return None
