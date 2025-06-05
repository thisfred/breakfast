from __future__ import annotations

import ast
import logging
from collections import defaultdict
from collections.abc import (
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)
from dataclasses import dataclass, replace
from functools import cached_property, singledispatch
from itertools import dropwhile, takewhile
from typing import ClassVar, Protocol, Self

from breakfast.code_generation import unparse
from breakfast.configuration import configuration
from breakfast.names import NameCollector
from breakfast.rewrites import ArgumentMapper, rewrite_body
from breakfast.search import (
    NodeFilter,
    find_names,
    find_other_nodes,
    find_returns,
    find_statements,
    find_yields,
    get_nodes,
    is_structurally_identical,
)
from breakfast.source import has_node_type
from breakfast.types import (
    DEFAULT,
    Edit,
    NodeWithRange,
    NotFoundError,
    Occurrence,
    Position,
    Ranged,
    ScopeWithRange,
    Sentinel,
    Source,
    TextRange,
)

logger = logging.getLogger(__name__)

INDENTATION = " " * configuration["code_generation"]["indentation"]
NEWLINE = "\n"
STATIC_METHOD = "staticmethod"
CLASS_METHOD = "classmethod"
PROPERTY = "property"
DUNDER_INIT = "__init__"
COMPUTE = "compute"


def register(refactoring: type[Refactoring]) -> type[Refactoring]:
    CodeSelection.register_refactoring(refactoring)
    return refactoring


@dataclass
class CodeSelection:
    text_range: TextRange
    sources: Sequence[Source]
    _refactorings: ClassVar[dict[str, type[Refactoring]]] = {}
    _names: NameCollector | None = None

    @property
    def names(self) -> NameCollector:
        if self._names is None:
            self._names = NameCollector.from_sources(self.sources)

        return self._names

    @property
    def source(self) -> Source:
        return self.text_range.source

    @property
    def start(self) -> Position:
        return self.text_range.start

    @property
    def end(self) -> Position:
        return self.text_range.end

    def __contains__(self, other: Ranged) -> bool:
        return other in self.text_range

    @classmethod
    def register_refactoring(cls, refactoring: type[Refactoring]) -> None:
        cls._refactorings[refactoring.name] = refactoring

    @property
    def refactorings(self) -> dict[str, Editor]:
        return {
            refactoring.name: refactoring_instance
            for refactoring in self._refactorings.values()
            if (refactoring_instance := refactoring.from_selection(self))
        }

    @cached_property
    def in_method(self) -> bool:
        return len(self.text_range.enclosing_scopes) > 1 and isinstance(
            self.text_range.enclosing_scopes[-2].node, ast.ClassDef
        )

    @cached_property
    def in_static_method(self) -> bool:
        return (
            self.in_method
            and isinstance(
                (scope_node := self.text_range.enclosing_scopes[-1].node),
                ast.FunctionDef,
            )
            and any(
                d.id == STATIC_METHOD
                for d in scope_node.decorator_list
                if isinstance(d, ast.Name)
            )
        )

    @cached_property
    def in_class_method(self) -> bool:
        return (
            self.in_method
            and isinstance(
                (scope_node := self.text_range.enclosing_scopes[-1].node),
                ast.FunctionDef,
            )
            and any(
                d.id == CLASS_METHOD
                for d in scope_node.decorator_list
                if isinstance(d, ast.Name)
            )
        )

    @cached_property
    def in_property(self) -> bool:
        return (
            self.in_method
            and isinstance(
                (scope_node := self.text_range.enclosing_scopes[-1].node),
                ast.FunctionDef,
            )
            and any(
                d.id == PROPERTY
                for d in scope_node.decorator_list
                if isinstance(d, ast.Name)
            )
        )

    def find_definition(self, position: Position) -> Occurrence | None:
        definitions = [
            o
            for o in self.names.all_occurrences_for(position)
            if o.is_definition
        ]
        if not definitions:
            return None
        return definitions[0]

    def all_occurrences(self, position: Position) -> Sequence[Occurrence]:
        return sorted(
            self.names.all_occurrences_for(position), key=lambda o: o.position
        )

    def rtrim(self) -> CodeSelection:
        lines = self.text_range.text.rstrip().split("\n")
        offset = 0
        last_line = lines[-1]
        start_offset = self.start.column if len(lines) == 1 else 0
        while self.end.column - (start_offset + offset) > len(last_line):
            offset += 1

        if offset == 0:
            return self

        return CodeSelection(
            sources=self.sources,
            text_range=replace(self.text_range, end=self.end - offset),
        )


class UsageCollector:
    def __init__(
        self,
        text_range: TextRange,
        enclosing_scope: ScopeWithRange,
        in_static_method: bool,
    ) -> None:
        self.in_static_method = in_static_method
        self.range = text_range
        self.enclosing_scope = enclosing_scope
        self._defined_before: dict[str, list[Occurrence]] = defaultdict(list)
        self._used_in: dict[str, list[Occurrence]] = defaultdict(list)
        self._modified_in: dict[str, list[Occurrence]] = defaultdict(list)
        self._used_after: dict[str, list[Occurrence]] = defaultdict(list)
        self.self_or_cls: Occurrence | None = None

    @property
    def defined_before_selection(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._defined_before:
            self._collect()

        return self._defined_before or {}

    @property
    def used_in_selection(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._used_in:
            self._collect()

        return self._used_in or {}

    @property
    def modified_in_selection(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._modified_in:
            self._collect()

        return self._modified_in or {}

    @property
    def used_after_selection(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._used_after:
            self._collect()

        return self._used_after or {}

    def _collect(self) -> None:
        for i, occurrence in enumerate(
            find_names(self.enclosing_scope.node, self.range.source)
        ):
            if (
                occurrence.position < self.range.start
                and occurrence.is_definition
            ):
                if i == 1 and not (self.in_static_method):
                    self.self_or_cls = occurrence
                self._defined_before[occurrence.name].append(occurrence)
            if occurrence.position in self.range:
                self._used_in[occurrence.name].append(occurrence)
                if occurrence.is_definition:
                    self._modified_in[occurrence.name].append(occurrence)
            if occurrence.position > self.range.end:
                self._used_after[occurrence.name].append(occurrence)

    def get_subsequent_usage(
        self,
        names_defined_in_range: Iterable[Occurrence],
    ) -> Position | None:
        occurrences_after_range = sorted(
            [
                o.position
                for n in names_defined_in_range
                for o in self.used_after_selection.get(n.name, [])
            ]
        )
        first_usage_after_range = (
            occurrences_after_range[0] if occurrences_after_range else None
        )
        return first_usage_after_range


class Editor(Protocol):
    @property
    def edits(self) -> Iterable[Edit]: ...


class Refactoring(Protocol):
    name: str

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None: ...


@register
@dataclass
class ExtractFunction:
    name = "extract function"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return ExtractFunctionEditor.from_selection(selection)


@dataclass
class ExtractFunctionEditor:
    selection: CodeSelection
    range: TextRange
    insert_position: Position
    new_level: int

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        if selection.end <= selection.start:
            return None
        match selection.text_range.enclosing_scopes:
            case (
                [
                    *_,
                    NodeWithRange(node=ast.ClassDef()),
                    NodeWithRange(node=ast.FunctionDef()),
                ] as scopes
            ):
                class_scope = scopes[-2]
                insert_position = (
                    class_scope.end.line.next.start
                    if class_scope.end.line.next
                    else class_scope.end.line.end
                )
                new_level = class_scope.start.column // 4
            case (
                [
                    *_,
                    NodeWithRange(node=ast.FunctionDef()),
                ] as scopes
            ):
                function_scope = scopes[-1]
                insert_position = (
                    function_scope.end.line.next.start
                    if function_scope.end.line.next
                    else function_scope.end.line.end
                )
                new_level = function_scope.start.column // 4
            case _:
                insert_position = selection.text_range.start.line.start
                new_level = insert_position.column // 4

        return cls(
            selection=selection,
            range=selection.text_range,
            insert_position=insert_position,
            new_level=new_level,
        )

    @property
    def edits(self) -> Iterator[Edit]:
        yield from make_extract_callable_edits(refactoring=self, name="f")

    @property
    def source(self) -> Source:
        return self.range.source

    @staticmethod
    def make_call(
        arguments: Sequence[Occurrence],
        return_node: ast.Return | None,
        name: str,
        self_or_cls_name: str | None,
        *,
        returns: bool,
        yields: bool,
    ) -> ast.Call | ast.Assign | ast.Return | ast.YieldFrom:
        func = ast.Name(id=name)
        return make_call(
            return_node, func, arguments, returns=returns, yields=yields
        )

    def make_decorators(self, usages: UsageCollector) -> list[ast.expr]:
        return []


def make_unique_name(
    original_name: str, enclosing_scope: ScopeWithRange
) -> str:
    name = original_name
    names_in_range = {
        occurrence.name for occurrence in enclosing_scope.range.names
    }
    counter = 0
    while name in names_in_range:
        name = f"{original_name}_{counter}"
        counter += 1
    return name


def make_body(
    text_range: TextRange, return_node: ast.Return | None
) -> list[ast.stmt]:
    if text_range.expression is not None:
        return [ast.Return(value=text_range.expression)]

    nodes = list(text_range.statements)
    if not nodes:
        if enclosing_assignment := text_range.enclosing_assignment:
            targets = enclosing_assignment.node.targets
            value: ast.expr | ast.Tuple = (
                ast.Tuple(targets) if len(targets) > 1 else targets[0]
            )
            nodes = [enclosing_assignment.node, ast.Return(value=value)]

    if return_node:
        nodes.append(return_node)
    return nodes or [ast.Pass()]


@register
@dataclass
class ExtractMethod:
    name = "extract method"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return ExtractMethodEditor.from_selection(selection)


@dataclass
class ExtractMethodEditor:
    selection: CodeSelection
    range: TextRange
    in_class_method: bool
    insert_position: Position
    new_level: int

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        enclosing_scope = selection.text_range.enclosing_scopes[-1]
        new_level = enclosing_scope.start.column // 4

        insert_position = (
            enclosing_scope.end.line.next.start
            if enclosing_scope.end.line.next
            else enclosing_scope.end.line.end
        )
        return (
            cls(
                selection=selection,
                range=selection.text_range,
                in_class_method=selection.in_class_method,
                new_level=new_level,
                insert_position=insert_position,
            )
            if (
                selection.end > selection.start
                and selection.in_method
                and not selection.in_static_method
            )
            else None
        )

    @property
    def edits(self) -> Iterator[Edit]:
        yield from make_extract_callable_edits(refactoring=self, name="m")

    @staticmethod
    def make_call(
        arguments: Sequence[Occurrence],
        return_node: ast.Return | None,
        name: str,
        self_or_cls_name: str | None,
        *,
        returns: bool,
        yields: bool,
    ) -> ast.Call | ast.Assign | ast.Return | ast.YieldFrom:
        if self_or_cls_name:
            arguments = [o for o in arguments if o.name != self_or_cls_name]
            func: ast.Attribute | ast.Name = ast.Attribute(
                value=ast.Name(id=self_or_cls_name), attr=name
            )
            calling_statement = make_call(
                return_node, func, arguments, returns=returns, yields=yields
            )
        else:
            func = ast.Name(id=name)
            calling_statement = make_call(
                return_node, func, arguments, returns=returns, yields=yields
            )
        return calling_statement

    def make_decorators(self, usages: UsageCollector) -> list[ast.expr]:
        if (
            usages.self_or_cls
            and usages.self_or_cls.name not in usages.used_in_selection
        ):
            decorator_list: list[ast.expr] = [ast.Name(STATIC_METHOD)]
        else:
            decorator_list = (
                [ast.Name(CLASS_METHOD)]
                if usages.self_or_cls and self.in_class_method
                else []
            )
        return decorator_list


def make_extract_callable_edits(
    refactoring: ExtractFunctionEditor | ExtractMethodEditor, name: str
) -> Iterator[Edit]:
    enclosing_scope = refactoring.range.enclosing_scopes[-1]
    usages = UsageCollector(
        refactoring.range,
        enclosing_scope,
        in_static_method=refactoring.selection.in_static_method,
    )
    return_node = make_return_node(
        usages.modified_in_selection, usages.used_after_selection
    )
    body = make_body(text_range=refactoring.range, return_node=return_node)

    name = make_unique_name(
        original_name=name,
        enclosing_scope=refactoring.range.enclosing_scopes[0],
    )
    arguments = make_arguments(
        usages.defined_before_selection, usages.used_in_selection
    )
    decorator_list = refactoring.make_decorators(usages=usages)
    callable_definition = make_function(
        decorator_list=decorator_list, name=name, body=body, arguments=arguments
    )

    returns = any(
        found
        for node in refactoring.range.statements
        for found in find_returns(node)
    )
    yields = any(
        found
        for node in refactoring.range.statements
        for found in find_yields(node)
    )
    calling_statement = refactoring.make_call(
        arguments=arguments,
        return_node=return_node,
        name=name,
        self_or_cls_name=(
            usages.self_or_cls.name if usages.self_or_cls else None
        ),
        returns=returns,
        yields=yields,
    )

    yield replace_with_node(
        refactoring.insert_position.as_range,
        callable_definition,
        add_newline_after=True,
        add_indentation_after=True,
        level=refactoring.new_level,
    )
    yield replace_with_node(
        text_range=refactoring.range,
        node=calling_statement,
    )


def make_function(
    *,
    decorator_list: list[ast.expr],
    name: str,
    arguments: Sequence[Occurrence],
    body: list[ast.stmt],
) -> ast.FunctionDef:
    args = ast.arguments(
        posonlyargs=[],
        args=[make_argument(o) for o in arguments],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )
    return ast.FunctionDef(
        name=name,
        args=args,
        body=body,
        decorator_list=decorator_list,
        returns=None,
        type_params=[],
    )


def make_call(
    return_node: ast.Return | None,
    func: ast.Name | ast.Attribute,
    arguments: Sequence[Occurrence],
    *,
    returns: bool,
    yields: bool,
) -> ast.Call | ast.Assign | ast.Return | ast.YieldFrom:
    if len(arguments) == 1:
        keywords = []
        args: list[ast.expr] = [ast.Name(o.name) for o in arguments]
    else:
        keywords = [
            ast.keyword(arg=o.name, value=ast.Name(o.name)) for o in arguments
        ]
        args = []

    call = ast.Call(func=func, args=args, keywords=keywords)
    if return_node:
        if isinstance(return_node.value, ast.expr):
            return ast.Assign(targets=[return_node.value], value=call)
    elif returns:
        return ast.Return(value=call)
    elif yields:
        return ast.YieldFrom(value=call)
    return call


def make_return_node(
    modified_in_extraction: Mapping[str, Sequence[Occurrence]],
    used_after_extraction: Mapping[str, Sequence[Occurrence]],
) -> ast.Return | None:
    returns = []
    for name, occurrences in modified_in_extraction.items():
        if name in used_after_extraction:
            returns.append(occurrences[0])
    if not returns:
        return None
    if len(returns) > 1:
        value: ast.expr | ast.Tuple = ast.Tuple(
            [o.ast for o in returns if isinstance(o.ast, ast.expr)]
        )
    elif isinstance(returns[0].ast, ast.expr):
        value = returns[0].ast

    return_node = ast.Return(value)
    return return_node


def make_arguments(
    defined_before_extraction: Mapping[str, Sequence[Occurrence]],
    used_in_extraction: Mapping[str, Sequence[Occurrence]],
) -> Sequence[Occurrence]:
    return [
        occurrences[0]
        for name, occurrences in defined_before_extraction.items()
        if name in used_in_extraction
    ]


def make_argument(occurrence: Occurrence) -> ast.arg:
    return ast.arg(
        arg=occurrence.name,
        annotation=occurrence.ast.annotation
        if isinstance(occurrence.ast, ast.arg)
        else None,
    )


@register
@dataclass
class ExtractVariable:
    name = "extract variable"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return ExtractVariableEditor.from_text_range(selection.text_range)


@dataclass
class ExtractVariableEditor:
    range: TextRange
    expression: ast.AST

    @classmethod
    def from_text_range(cls, text_range: TextRange) -> Self | None:
        if not text_range.end > text_range.start:
            return None

        if not (expression := cls.get_single_expression_value(text_range.text)):
            logger.warning("Could not extract single expression value.")
            return None

        return cls(range=text_range, expression=expression)

    @classmethod
    def get_single_expression_value(cls, text: str) -> ast.AST | None:
        try:
            parsed = ast.parse(text)
        except SyntaxError:
            return None

        if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.Expr):
            return None

        return parsed.body[0].value

    @property
    def edits(self) -> Iterator[Edit]:
        extracted = self.range.text

        other_occurrences = find_other_nodes(
            source_ast=self.range.source.ast,
            node=self.expression,
            position=self.range.start,
        )

        enclosing_scope = self.range.enclosing_scopes[-1]
        name = make_unique_name(
            original_name="v", enclosing_scope=enclosing_scope
        )

        target_range = get_body_range(enclosing_scope=enclosing_scope)

        other_edits = [
            (start := self.range.source.node_position(o))
            .to(start + len(extracted))
            .replace(name)
            for o in other_occurrences
            if (node_position := self.range.source.node_position(o))
            and node_position in target_range
        ]
        edits = sorted(
            [
                Edit(text_range=self.range, text=name),
                *other_edits,
            ]
        )
        first_edit_position = edits[0].start

        preceding_statement_positions = list(
            takewhile(
                lambda p: p < first_edit_position,
                (
                    self.range.source.node_position(s)
                    for s in find_statements(self.range.source.ast)
                ),
            )
        )

        statement_start = (
            preceding_statement_positions[-1]
            if preceding_statement_positions
            else None
        )

        insert_point = statement_start or first_edit_position.start_of_line
        indentation = " " * insert_point.column
        definition = f"{name} = {extracted}{NEWLINE}{indentation}"
        insert = insert_point.insert(definition)
        yield insert
        yield from edits


def get_body_range(enclosing_scope: ScopeWithRange) -> TextRange:
    if not isinstance(
        enclosing_scope.node,
        ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ):
        return enclosing_scope.range
    end = enclosing_scope.source.node_end_position(
        enclosing_scope.node.body[-1]
    )
    if end is None:
        return enclosing_scope.range
    return enclosing_scope.source.node_position(
        enclosing_scope.node.body[0]
    ).through(end)


@register
@dataclass
class InlineVariable:
    name = "inline variable"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        enclosing_names = selection.text_range.enclosing_nodes_by_type(ast.Name)
        if not enclosing_names:
            return None
        enclosing_name = enclosing_names[-1]
        try:
            occurrences_of_name_at_cursor = selection.all_occurrences(
                selection.text_range.start
            )
        except NotFoundError:
            return None

        return InlineVariableEditor(
            range=selection.text_range,
            occurrences_of_name_at_cursor=occurrences_of_name_at_cursor,
            name_at_cursor=enclosing_name.node.id,
        )


@dataclass
class InlineVariableEditor:
    range: TextRange
    name_at_cursor: str
    occurrences_of_name_at_cursor: Sequence[Occurrence]

    @property
    def edits(self) -> Iterator[Edit]:
        grouped: dict[bool, list[Occurrence]] = defaultdict(list)
        for o in self.occurrences_of_name_at_cursor:
            grouped[o.is_definition].append(o)

        last_definition = grouped.get(True, [None])[-1]

        if last_definition is None:
            logger.warning("Could not find definition.")
            return
        assignment = last_definition.position.as_range.enclosing_assignment
        if assignment is None:
            logger.warning("Could not find assignment for definition.")
            return

        if self.range.start in assignment.range:
            after_cursor = list(
                dropwhile(
                    lambda x: x.position <= assignment.range.end,
                    self.occurrences_of_name_at_cursor,
                )
            )
            to_replace: tuple[TextRange, ...] = tuple(
                o.position.to(o.position + len(self.name_at_cursor))
                for o in takewhile(
                    lambda x: not x.is_definition,
                    after_cursor,
                )
                if o.position > self.range.start
            )
        else:
            to_replace = (
                self.range.start.to(
                    self.range.start + len(self.name_at_cursor)
                ),
            )

        if self.range.start in assignment.range:
            can_remove_last_definition = True
        else:
            other_occurrences = [
                o
                for o in grouped.get(False, [])
                if o.position not in self.range
            ]
            last_occurrence = (
                other_occurrences[-1] if other_occurrences else None
            )
            can_remove_last_definition = (
                last_occurrence is None
                or last_occurrence.position < assignment.start
            )

        yield from (
            replace_with_node(name_range, assignment.node.value)
            for name_range in to_replace
        )

        if can_remove_last_definition:
            if len(assignment.node.targets) == 1:
                yield delete_range(assignment.range)
            else:
                assignment.node.targets = [
                    t
                    for t in assignment.node.targets
                    if isinstance(t, ast.Name) and t.id != self.name_at_cursor
                ]
                yield replace_with_node(assignment.range, assignment.node)


@register
@dataclass
class InlineCall:
    name = "inline call"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return (
            cls(selection=selection)
            if selection.text_range.enclosing_call
            else None
        )

    @property
    def text_range(self) -> TextRange:
        return self.selection.text_range

    @property
    def source(self) -> Source:
        return self.selection.source

    @property
    def enclosing_call(self) -> NodeWithRange[ast.Call] | None:
        return self.text_range.enclosing_call

    @property
    def edits(self) -> Iterator[Edit]:
        call = self.enclosing_call

        if not call:
            logger.warning("No enclosing call.")
            return

        name_start = self.get_start_of_name(call=call)

        definition = self.selection.find_definition(name_start)
        if definition is None or definition.position is None:
            logger.warning(f"No definition position {definition=}.")
            return
        if not isinstance(definition.ast, ast.FunctionDef):
            logger.warning(f"Not a function {definition.ast=}.")
            return

        node_filter = self.make_filter(definition)
        found = next(
            get_nodes(definition.position.source.ast, node_filter), None
        )
        if not isinstance(found, ast.FunctionDef | ast.AsyncFunctionDef):
            return

        body_range = self.get_body_range(definition=definition, found=found)

        result: Iterable[Edit] = self.maybe_remove_definition(
            name_start=name_start, definition=definition
        )

        new_statements = self.get_new_statements(
            call=call.node, body_range=body_range, definition_ast=definition.ast
        )

        return_ranges = [
            return_range
            for statement in definition.ast.body
            for r in find_returns(statement)
            if (return_range := self.source.node_range(r))
            and r.value is not None
        ]
        if return_ranges:
            name = "result"
            result = (Edit(call.range, text=name), *result)
        insert_range = (
            self.selection.start.line.start.as_range
            if return_ranges
            else call.range
        )
        body = (
            ast.Module(body=new_statements, type_ignores=[])
            if new_statements
            else ast.Pass()
        )
        result = (
            replace_with_node(insert_range, body, add_newline_after=True),
            *result,
        )
        yield from result

    def maybe_remove_definition(
        self, name_start: Position, definition: Occurrence
    ) -> Iterator[Edit]:
        number_of_occurrences = len(self.selection.all_occurrences(name_start))
        if (
            number_of_occurrences > 2
            or definition.ast is None
            or (
                definition_range := definition.position.source.node_range(
                    definition.ast
                )
            )
            is None
        ):
            return
        yield Edit(definition_range, text="")

    @staticmethod
    def get_body_range(
        definition: Occurrence, found: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> TextRange:
        children = found.body
        start_position = definition.position.source.node_position(children[0])
        end_position = (
            definition.position.source.node_end_position(children[-1])
            or start_position.line.end
        )
        body_range = start_position.to(end_position)
        return body_range

    @staticmethod
    def make_filter(definition: Occurrence) -> NodeFilter:
        def node_filter(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and definition.position.source.node_position(node).row
                == definition.position.row
            )

        return node_filter

    @staticmethod
    def get_start_of_name(call: NodeWithRange[ast.Call]) -> Position:
        name_start = call.start
        if isinstance(call.node.func, ast.Attribute):
            if (
                call.node.func.value.end_col_offset
                and call.node.func.col_offset
            ):
                name_start += (
                    call.node.func.value.end_col_offset
                    - call.node.func.col_offset
                ) + 1
        return name_start

    def get_new_statements(
        self,
        *,
        call: ast.Call,
        body_range: TextRange,
        definition_ast: ast.FunctionDef,
    ) -> list[ast.stmt]:
        substitutions: dict[ast.AST, ast.AST] = {}

        # TODO: make unique
        name = "result"
        returned_names = set()
        for statement in definition_ast.body:
            returns = find_returns(statement)
            for return_node in returns:
                if return_node.value:
                    substitutions[return_node] = ast.Assign(
                        targets=[ast.Name(id=name)], value=return_node.value
                    )
                    if isinstance(return_node.value, ast.Name):
                        returned_names.add(return_node.value.id)

        arg_mapper = ArgumentMapper(
            definition_ast.args,
            body_range,
            returned_names,
            sources=self.selection.sources,
        )
        arg_mapper.add_substitutions(call, substitutions)

        result: list[ast.stmt] = []
        result = rewrite_body(
            function_definition=definition_ast, substitutions=substitutions
        )
        return list(takewhile(lambda s: not isinstance(s, ast.Return), result))


@register
@dataclass
class SlideStatementsUp:
    name = "slide statements up"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return cls(selection=selection)

    @property
    def edits(self) -> Iterator[Edit]:
        target = self.find_slide_target_before()
        if target is None:
            return

        first, last = (self.selection.start.line, self.selection.end.line)
        yield target.insert(first.start.through(last.end).text + NEWLINE)
        yield first.start.to(
            last.next.start if last.next else last.end
        ).replace("")

    def find_slide_target_before(self) -> Position | None:
        first, last = (self.selection.start.line, self.selection.end.line)
        line = first
        while line.previous and line.previous.start.level >= first.start.level:
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
@dataclass
class SlideStatementsDown:
    name = "slide statements down"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return cls(selection=selection)

    @property
    def edits(self) -> Iterator[Edit]:
        target = self.find_slide_target_after()
        if target is None:
            return

        first, last = (self.selection.start.line, self.selection.end.line)
        yield target.insert(first.start.through(last.end).text + NEWLINE)
        yield first.start.to(
            last.next.start if last.next else last.end
        ).replace("")

    def find_slide_target_after(self) -> Position | None:
        first, last = (self.selection.start.line, self.selection.end.line)
        lines = first.start.to(last.end)
        names_defined_in_range = lines.definitions
        enclosing_scope = self.selection.text_range.enclosing_scopes[-1]
        usages = UsageCollector(
            self.selection.text_range,
            enclosing_scope,
            in_static_method=self.selection.in_static_method,
        )
        first_usage_after_range = usages.get_subsequent_usage(
            names_defined_in_range=names_defined_in_range
        )
        if not first_usage_after_range:
            return None

        enclosing_nodes = first_usage_after_range.as_range.enclosing_nodes
        origin_nodes = lines.enclosing_nodes
        index = 0
        while (
            index < len(origin_nodes)
            and origin_nodes[index].range == enclosing_nodes[index].range
        ):
            index += 1

        first_usage_after_range = enclosing_nodes[index].start

        if (
            first_usage_after_range
            and first_usage_after_range.row > last.row + 1
        ):
            return first_usage_after_range.start_of_line

        return None


@register
@dataclass
class MoveFunctionToParentScope:
    name = "move function to parent scope"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return cls(selection=selection) if cls.applies_to(selection) else None

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return len(selection.text_range.enclosing_scopes) >= 3 and isinstance(
            selection.text_range.enclosing_scopes[-1].node,
            ast.FunctionDef | ast.AsyncFunctionDef,
        )

    @property
    def edits(self) -> Iterator[Edit]:
        enclosing_scope = self.selection.text_range.enclosing_scopes[-1]
        result: tuple[Edit, ...] = (Edit(enclosing_scope.range, ""),)

        if not (
            scope := self.closest_enclosing_non_class_scope(
                selection=self.selection
            )
        ):
            logger.warning("Not inside an appropriately nested scope.")
            return

        insert_position = (
            scope.end.line.next.start
            if scope.end.line.next
            else scope.end.line.end
        )
        yield from (
            *result,
            replace_with_node(insert_position.as_range, enclosing_scope.node),
        )

    @staticmethod
    def closest_enclosing_non_class_scope(
        selection: CodeSelection,
    ) -> ScopeWithRange | None:
        scope = None
        i = len(selection.text_range.enclosing_scopes) - 3
        while i >= 0 and isinstance(
            (scope := selection.text_range.enclosing_scopes[i]), ast.ClassDef
        ):
            i -= 1
        return scope


@register
@dataclass
class RemoveParameter:
    name = "remove parameter"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return (
            cls(selection=selection)
            if selection.text_range.enclosing_nodes_by_type(ast.arg)
            else None
        )

    @property
    def edits(self) -> Iterator[Edit]:
        if not self.is_parameter_unused:
            logger.warning(
                "Can't remove parameter that is used in function body."
            )
            return

        yield from (self.function_definition_edit, *self.call_edits)

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]

    @property
    def arg(self) -> NodeWithRange[ast.arg]:
        return self.selection.text_range.enclosing_nodes_by_type(ast.arg)[-1]

    @property
    def is_parameter_unused(self) -> bool:
        return (
            len(
                [
                    o
                    for o in self.selection.all_occurrences(self.arg.start)
                    if o.position in self.function_definition.range
                ]
            )
            == 1
        )

    @property
    def call_edits(self) -> Sequence[Edit]:
        call_edits = []
        index = self.function_definition.node.args.args.index(self.arg.node)
        for occurrence in self.selection.all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.is_definition:
                continue
            if not (
                calls := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Call
                )
            ):
                continue
            call = calls[-1].node
            if call.args:
                new_call = ast.Call(
                    func=call.func,
                    args=call.args[:index] + call.args[index + 1 :],
                    keywords=call.keywords,
                )
                call_edits.append(replace_with_node(calls[-1].range, new_call))
            elif call.keywords:
                new_call = ast.Call(
                    func=call.func,
                    args=call.args,
                    keywords=[
                        kw
                        for kw in call.keywords
                        if kw.arg != self.arg.node.arg
                    ],
                )
                call_edits.append(replace_with_node(calls[-1].range, new_call))
        return call_edits

    @property
    def function_definition_edit(self) -> Edit:
        definition = self.function_definition.node
        return replace_with_node(
            self.function_definition.range,
            copy_function_def(
                definition=definition,
                args=copy_arguments(
                    definition.args,
                    args=[
                        a for a in definition.args.args if a != self.arg.node
                    ],
                ),
            ),
        )


@register
@dataclass
class AddParameter:
    name = "add parameter"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return (
            cls(selection=selection)
            if selection.text_range.enclosing_nodes_by_type(ast.FunctionDef)
            else None
        )

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]

    @property
    def edits(self) -> Iterator[Edit]:
        arg_name = make_unique_name(
            original_name="p", enclosing_scope=self.function_definition
        )
        yield from (
            self.function_definition_edit(arg_name),
            *self.call_edits(arg_name),
        )

    def call_edits(self, arg_name: str) -> Sequence[Edit]:
        call_edits = []
        for occurrence in self.selection.all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.is_definition:
                continue
            if not (
                calls := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Call
                )
            ):
                continue
            call = calls[-1].node
            new_call = ast.Call(
                func=call.func,
                args=call.args,
                keywords=[
                    *call.keywords,
                    ast.keyword(arg=arg_name, value=ast.Constant(value=None)),
                ],
            )
            call_edits.append(replace_with_node(calls[-1].range, new_call))
        return call_edits

    def function_definition_edit(self, arg_name: str) -> Edit:
        definition = self.function_definition.node
        return replace_with_node(
            self.function_definition.range,
            copy_function_def(
                definition,
                args=copy_arguments(
                    definition.args,
                    args=[*definition.args.args, ast.arg(arg=arg_name)],
                ),
            ),
        )


@register
@dataclass
class EncapsulateRecord:
    name = "encapsulate record"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        enclosing_assignment = selection.text_range.enclosing_assignment
        if enclosing_assignment is None:
            logger.warning("Dictionary value not assigned to a name.")
            return None

        match enclosing_assignment.node:
            case ast.Assign(targets):
                assignment = enclosing_assignment
                targets = targets
            case ast.AnnAssign(target):
                assignment = enclosing_assignment
                targets = [target]
            case _:
                return None

        return (
            EncapsulateRecordEditor(
                range=selection.text_range,
                enclosing_assignment=assignment,
                targets=targets,
                selection=selection,
            )
            if selection.text_range.enclosing_nodes_by_type(ast.Dict)
            else None
        )


@dataclass
class EncapsulateRecordEditor:
    range: TextRange
    enclosing_assignment: (
        NodeWithRange[ast.Assign] | NodeWithRange[ast.AnnAssign]
    )
    targets: list[ast.expr]
    selection: CodeSelection

    @property
    def edits(self) -> Iterator[Edit]:
        mapping = self.make_dictionary_mapping()
        new_statements = self.make_assignments(mapping=mapping)
        class_name = make_unique_name(
            self.make_class_name() or "Record", self.range.enclosing_scope
        )
        dataclass_definition = make_dataclass(
            class_name=class_name, new_statements=new_statements
        )
        yield replace_with_node(
            self.enclosing_assignment.start.as_range,
            dataclass_definition,
            add_newline_after=True,
        )
        yield replace_with_node(
            self.enclosing_assignment.range,
            ast.Assign(
                targets=self.targets,
                value=ast.Call(
                    func=ast.Name(id=class_name),
                    args=[],
                    keywords=[
                        ast.keyword(arg=key.value, value=value)
                        for (key, value) in mapping.items()
                        if isinstance(key, ast.Constant)
                    ],
                ),
            ),
        )

        for occurrence in self.selection.all_occurrences(
            self.range.source.node_position(self.enclosing_assignment.node)
        ):
            if occurrence.is_definition:
                continue
            if not (
                subscripts
                := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Subscript
                )
            ):
                continue
            node = subscripts[0].node
            if isinstance(node.slice, ast.Constant):
                yield replace_with_node(
                    subscripts[0].range,
                    ast.Attribute(value=node.value, attr=node.slice.value),
                )

    def make_dictionary_mapping(self) -> Mapping[ast.expr | None, ast.expr]:
        dict_node = self.range.enclosing_nodes_by_type(ast.Dict)[-1]
        mapping = dict(
            zip(dict_node.node.keys, dict_node.node.values, strict=True)
        )
        return mapping

    @staticmethod
    def make_assignments(
        mapping: Mapping[ast.expr | None, ast.expr],
    ) -> list[ast.stmt]:
        new_statements: list[ast.stmt] = [
            (
                ast.AnnAssign(
                    target=ast.Name(id=key.value),
                    annotation=annotation,
                    simple=1,
                )
                if (annotation := type_from(value))
                else ast.Assign(
                    targets=[ast.Name(id=key.value)],
                    value=ast.Constant(value=None),
                )
            )
            for (key, value) in mapping.items()
            if isinstance(key, ast.Constant)
        ]
        return new_statements

    def make_class_name(self) -> str | None:
        if not (isinstance(self.targets[0], ast.Name)):
            return None

        var_name = self.targets[0].id
        return to_class_name(var_name)


@register
@dataclass
class MethodToProperty:
    name = "convert method to property"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return (
            cls(selection=selection)
            if selection.in_method and not selection.in_property
            else None
        )

    @property
    def edits(self) -> Iterator[Edit]:
        definition = self.function_definition.node
        yield replace_with_node(
            self.function_definition.range,
            copy_function_def(
                definition,
                decorator_list=[
                    ast.Name(id=PROPERTY),
                    *definition.decorator_list,
                ],
            ),
        )
        for occurrence in self.selection.all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.is_definition:
                continue
            if not (
                calls := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Call
                )
            ):
                continue
            node = calls[-1].node

            yield replace_with_node(calls[-1].range, node.func)

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


@register
@dataclass
class PropertyToMethod:
    name = "convert property to method"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return cls(selection=selection) if selection.in_property else None

    @property
    def edits(self) -> Iterator[Edit]:
        definition = self.function_definition.node
        new_function = copy_function_def(
            definition,
            decorator_list=[
                d
                for d in definition.decorator_list
                if (not isinstance(d, ast.Name) or d.id != PROPERTY)
            ],
        )
        start = self.function_definition.start
        for _ in definition.decorator_list:
            start = start.line.previous.start if start.line.previous else start
        range_with_decorators = start.through(self.function_definition.end)
        yield replace_with_node(range_with_decorators, new_function)
        for occurrence in self.selection.all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.is_definition:
                continue
            if not (
                attributes
                := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Attribute
                )
            ):
                continue
            node = attributes[-1].node

            yield replace_with_node(
                attributes[-1].range,
                ast.Call(func=node, args=[], keywords=[]),
            )

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


@register
@dataclass
class ExtractClass:
    name = "extract class"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Self | None:
        return (
            cls(selection=selection)
            if selection.in_method and not selection.in_static_method
            else None
        )

    @property
    def edits(self) -> Iterator[Edit]:
        definition = self.function_definition.node
        new_body: list[ast.stmt] = []
        call_added = False
        class_name = make_unique_name(
            original_name="C",
            enclosing_scope=self.selection.text_range.enclosing_scopes[0],
        )
        property_name = make_unique_name(
            original_name=class_name.lower(),
            enclosing_scope=self.selection.text_range.enclosing_nodes_by_type(
                ast.ClassDef
            )[-1],
        )
        assignments = [
            s
            for s in definition.body
            if (
                self.selection.source.node_position(s)
                in self.selection.text_range
            )
            and isinstance(s, ast.Assign)
        ]
        for assignment in assignments:
            if isinstance(assignment.targets[0], ast.Attribute) and isinstance(
                assignment.targets[0].value, ast.Name
            ):
                for occurrence in self.selection.all_occurrences(
                    self.selection.source.node_position(assignment.targets[0])
                    + len(assignment.targets[0].value.id)
                    + 1
                ):
                    if occurrence.is_definition:
                        continue
                    attribute = (
                        occurrence.position.as_range.enclosing_nodes_by_type(
                            ast.Attribute
                        )[0]
                    )
                    yield replace_with_node(
                        attribute.range,
                        ast.Attribute(
                            value=ast.Attribute(
                                value=attribute.node.value,
                                attr=property_name,
                            ),
                            attr=attribute.node.attr,
                        ),
                    )

        new_assignments: list[ast.stmt] = [
            ast.AnnAssign(
                target=ast.Name(id=a.targets[0].attr),
                annotation=annotation,
                simple=1,
            )
            if (annotation := type_from(a.value))
            else ast.Assign(
                targets=[ast.Name(id=a.targets[0].attr)],
                value=ast.Constant(value=None),
            )
            for a in assignments
            if isinstance(a.targets[0], ast.Attribute)
        ]
        dataclass_definition = make_dataclass(
            class_name=class_name, new_statements=new_assignments
        )
        yield replace_with_node(
            self.selection.text_range.enclosing_scopes[0].start.as_range,
            dataclass_definition,
            add_newline_after=True,
        )

        instantiation = ast.Assign(
            targets=[
                ast.Attribute(value=ast.Name(id="self"), attr=property_name)
            ],
            value=ast.Call(func=ast.Name(class_name), args=[], keywords=[]),
        )

        for statement in definition.body:
            if (
                self.selection.source.node_position(statement)
                in self.selection.text_range
            ):
                if isinstance(statement, ast.Assign):
                    if not call_added:
                        new_body.append(instantiation)
                        call_added = True
                    if isinstance(instantiation.value, ast.Call) and isinstance(
                        statement.targets[0], ast.Attribute
                    ):
                        instantiation.value.keywords.append(
                            ast.keyword(
                                arg=statement.targets[0].attr,
                                value=statement.value,
                            )
                        )
                else:
                    new_body.append(statement)
        yield replace_with_node(
            self.function_definition.range,
            copy_function_def(definition, body=new_body),
        )

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


def make_dataclass(
    class_name: str, new_statements: list[ast.stmt]
) -> ast.Module:
    return ast.Module(
        body=[
            ast.ImportFrom(
                module="dataclasses",
                names=[ast.alias(name="dataclass")],
                level=0,
            ),
            ast.ClassDef(
                name=class_name,
                body=new_statements,
                decorator_list=[ast.Name(id="dataclass")],
                bases=[],
                keywords=[],
                type_params=[],
            ),
        ],
        type_ignores=[],
    )


@register
@dataclass
class ReplaceWithMethodObject:
    name = "replace with method object"
    selection: CodeSelection

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return cls(selection=selection) if cls.applies_to(selection) else None

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return (
            selection.end > selection.start
            and selection.in_method
            and not selection.in_static_method
        )

    @property
    def edits(self) -> Iterator[Edit]:
        original_class_name = self.selection.text_range.enclosing_nodes_by_type(
            ast.ClassDef
        )[-1].node.name
        arg_name = to_variable_name(original_class_name)
        instance = ast.arg(
            arg=arg_name, annotation=ast.Name(original_class_name)
        )
        new_args = copy_arguments(
            self.function_definition.node.args,
            args=[
                self.function_definition.node.args.args[0],
                instance,
                *self.function_definition.node.args.args[1:],
            ],
        )
        init = copy_function_def(
            self.function_definition.node,
            name=DUNDER_INIT,
            args=new_args,
            body=[
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(new_args.args[0].arg), attr=arg.arg
                        )
                    ],
                    value=ast.Name(arg.arg),
                )
                for arg in new_args.args[1:]
            ],
        )
        substitutions: dict[ast.AST, ast.AST] = {}
        self_arg = self.function_definition.node.args.args[0]
        self.add_substitutions(
            argument=self_arg,
            value=ast.Attribute(value=ast.Name(self_arg.arg), attr=arg_name),
            substitutions=substitutions,
        )
        for argument in self.function_definition.node.args.args[1:]:
            self.add_substitutions(
                argument=argument,
                value=ast.Attribute(
                    value=ast.Name(self_arg.arg), attr=argument.arg
                ),
                substitutions=substitutions,
            )

        compute = copy_function_def(
            self.function_definition.node,
            args=copy_arguments(
                self.function_definition.node.args,
                args=self.function_definition.node.args.args[:1],
            ),
            name=COMPUTE,
            body=rewrite_body(self.function_definition.node, substitutions),
        )
        new_class_name = make_unique_name(
            to_class_name(self.function_definition.node.name),
            enclosing_scope=self.selection.text_range.enclosing_scopes[0],
        )
        method_object_class = ast.ClassDef(
            name=new_class_name,
            body=[init, compute],
            decorator_list=[],
            bases=[],
            keywords=[],
            type_params=[],
        )

        yield replace_with_node(
            self.selection.text_range.enclosing_scopes[0].start.as_range,
            method_object_class,
            add_newline_after=True,
        )

        mapping = {
            arg_name: self_arg.arg,
            **{
                k.arg: k.arg
                for k in self.function_definition.node.args.args[1:]
            },
        }
        call_method_object = copy_function_def(
            self.function_definition.node,
            body=[
                ast.Return(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Call(
                                func=ast.Name(new_class_name),
                                args=[],
                                keywords=[
                                    ast.keyword(arg=name, value=ast.Name(value))
                                    for name, value in mapping.items()
                                ],
                            ),
                            attr=COMPUTE,
                        ),
                        args=[],
                        keywords=[],
                    )
                )
            ],
        )

        yield replace_with_node(
            self.function_definition.range, call_method_object
        )

    def add_substitutions(
        self,
        argument: ast.keyword | ast.arg,
        value: ast.expr,
        substitutions: MutableMapping[ast.AST, ast.AST],
    ) -> None:
        occurrences = self.get_occurrences(argument)
        for occurrence in occurrences:
            if occurrence.ast:
                substitutions[occurrence.ast] = value

    def get_occurrences(
        self, argument: ast.keyword | ast.arg
    ) -> Sequence[Occurrence]:
        assert argument.arg is not None  # noqa: S101
        arg_position = self.selection.source.node_position(argument)
        body_range = self.selection.source.node_position(
            self.function_definition.node.body[0]
        ).through(self.function_definition.end)
        return [
            o
            for o in self.selection.all_occurrences(arg_position)
            if o.position in body_range and o.ast
        ]

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


@register
@dataclass
class ConvertToIfExpression:
    name = "convert if statement to if expression"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return MakeIfExpression.from_text_range(selection.text_range)


@dataclass
class MakeIfExpression:
    target: ast.Name | ast.Attribute | ast.Subscript
    annotation: ast.expr | None
    if_value: ast.expr
    test: ast.expr
    else_value: ast.expr
    range: TextRange

    @classmethod
    def from_text_range(cls, text_range: TextRange) -> Self | None:
        if not (if_statements := text_range.enclosing_nodes_by_type(ast.If)):
            return None
        match if_statements[-1].node:
            case ast.If(
                test=test,
                body=[ast.Assign(targets=[if_target], value=if_value)],
                orelse=[ast.Assign(targets=[else_target], value=else_value)],
            ) if isinstance(
                if_target, ast.Name | ast.Attribute | ast.Subscript
            ):
                if not is_structurally_identical(if_target, else_target):
                    return None
                return cls(
                    target=if_target,
                    annotation=None,
                    if_value=if_value,
                    test=test,
                    else_value=else_value,
                    range=if_statements[-1].range,
                )
            case ast.If(
                test=test,
                body=[
                    ast.AnnAssign(
                        target=if_target,
                        annotation=annotation,
                        value=if_value,
                    )
                ],
                orelse=[ast.Assign(targets=[else_target], value=else_value)],
            ):
                if (
                    not is_structurally_identical(if_target, else_target)
                    or if_value is None
                ):
                    return None
                return cls(
                    target=if_target,
                    annotation=annotation,
                    if_value=if_value,
                    test=test,
                    else_value=else_value,
                    range=if_statements[-1].range,
                )
            case _:
                return None

    @property
    def edits(self) -> Iterator[Edit]:
        if self.annotation:
            yield replace_with_node(
                self.range,
                ast.AnnAssign(
                    target=self.target,
                    annotation=self.annotation,
                    value=ast.IfExp(
                        body=self.if_value,
                        test=self.test,
                        orelse=self.else_value,
                    ),
                    simple=1,
                ),
            )
        else:
            yield replace_with_node(
                self.range,
                ast.Assign(
                    targets=[self.target],
                    value=ast.IfExp(
                        body=self.if_value,
                        test=self.test,
                        orelse=self.else_value,
                    ),
                ),
            )


@register
@dataclass
class ConvertToIfStatement:
    name = "convert if expression to if statement"

    @classmethod
    def from_selection(cls, selection: CodeSelection) -> Editor | None:
        return MakeIfStatement.from_text_range(selection.text_range)


@dataclass
class MakeIfStatement:
    target: ast.Name | ast.Attribute | ast.Subscript
    test: ast.expr
    if_value: ast.expr
    else_value: ast.expr
    range: TextRange
    annotation: ast.expr | None = None

    @classmethod
    def from_text_range(cls, text_range: TextRange) -> Self | None:
        if not (
            assignments := [
                n
                for n in text_range.enclosing_nodes
                if has_node_type(n, ast.Assign)
                or has_node_type(n, ast.AnnAssign)
            ]
        ):
            return None
        match assignments[-1].node:
            case ast.Assign(
                targets=[target],
                value=ast.IfExp(test=test, body=body, orelse=orelse),
            ) if isinstance(target, ast.Name | ast.Attribute | ast.Subscript):
                return cls(
                    target=target,
                    test=test,
                    if_value=body,
                    else_value=orelse,
                    range=assignments[-1].range,
                )
            case ast.AnnAssign(
                target=target,
                annotation=annotation,
                value=ast.IfExp(test=test, body=body, orelse=orelse),
            ):
                return cls(
                    target=target,
                    annotation=annotation,
                    test=test,
                    if_value=body,
                    else_value=orelse,
                    range=assignments[-1].range,
                )
            case _:
                return None

    @property
    def edits(self) -> Iterator[Edit]:
        yield replace_with_node(
            self.range,
            ast.If(
                test=self.test,
                body=[
                    ast.AnnAssign(
                        target=self.target,
                        annotation=self.annotation,
                        value=self.if_value,
                        simple=1,
                    )
                    if self.annotation
                    else ast.Assign(targets=[self.target], value=self.if_value)
                ],
                orelse=[
                    ast.Assign(targets=[self.target], value=self.else_value)
                ],
            ),
        )


def to_class_name(var_name: str) -> str:
    return "".join(s.lower().title() for s in var_name.split("_"))


def to_variable_name(var_name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in var_name)[1:]


@singledispatch
def type_from(node: ast.AST) -> ast.expr | None:
    return None


@type_from.register
def type_from_constant(node: ast.Constant) -> ast.expr | None:
    return ast.Name(id=type(node.value).__name__)


def render_node(node: ast.AST, text_range: TextRange, level: int | None) -> str:
    call_text = unparse(node, level=level or text_range.start.level)
    if text_range.start.column == 0:
        call_text = f"{INDENTATION * text_range.start.level}{call_text}"
    return call_text


def delete_range(text_range: TextRange) -> Edit:
    return Edit(text_range, "")


def replace_with_node(
    text_range: TextRange,
    node: ast.AST,
    *,
    add_newline_after: bool = False,
    add_indentation_after: bool = False,
    level: int | None = None,
) -> Edit:
    return Edit(
        text_range,
        text=render_node(node=node, text_range=text_range, level=level)
        + (NEWLINE if add_newline_after else "")
        + (
            INDENTATION * (level or text_range.start.level)
            if add_indentation_after
            else ""
        ),
    )


def copy_function_def(
    definition: ast.FunctionDef,
    *,
    name: str | Sentinel = DEFAULT,
    args: ast.arguments | Sentinel = DEFAULT,
    body: list[ast.stmt] | Sentinel = DEFAULT,
    decorator_list: list[ast.expr] | Sentinel = DEFAULT,
    returns: ast.expr | Sentinel = DEFAULT,
    type_params: list[ast.type_param] | Sentinel = DEFAULT,
) -> ast.FunctionDef:
    new_function = ast.FunctionDef(
        name=default(name, definition.name),
        args=default(args, definition.args),
        body=default(body, definition.body),
        decorator_list=default(decorator_list, definition.decorator_list),
        returns=default(returns, definition.returns),
        type_params=default(type_params, definition.type_params),
    )
    return new_function


def copy_arguments(
    arguments: ast.arguments,
    *,
    posonlyargs: list[ast.arg] | Sentinel = DEFAULT,
    args: list[ast.arg] | Sentinel = DEFAULT,
    vararg: ast.arg | Sentinel = DEFAULT,
    kwonlyargs: list[ast.arg] | Sentinel = DEFAULT,
    kw_defaults: list[ast.expr | None] | Sentinel = DEFAULT,
    kwarg: ast.arg | Sentinel = DEFAULT,
    defaults: list[ast.expr] | Sentinel = DEFAULT,
) -> ast.arguments:
    return ast.arguments(
        posonlyargs=default(posonlyargs, arguments.posonlyargs),
        args=default(args, arguments.args),
        vararg=default(vararg, arguments.vararg),
        kwonlyargs=default(kwonlyargs, arguments.kwonlyargs),
        kw_defaults=default(kw_defaults, arguments.kw_defaults),
        kwarg=default(kwarg, arguments.kwarg),
        defaults=default(defaults, arguments.defaults),
    )


def default[T](value: T | Sentinel, default: T) -> T:
    return default if value is DEFAULT else value
