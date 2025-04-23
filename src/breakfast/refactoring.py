import ast
import logging
from collections import defaultdict
from collections.abc import (
    Iterable,
    Mapping,
    MutableMapping,
    Sequence,
)
from dataclasses import dataclass, replace
from functools import cached_property, singledispatch
from itertools import dropwhile, takewhile
from typing import ClassVar, Protocol

from breakfast.code_generation import unparse
from breakfast.configuration import configuration
from breakfast.names import (
    all_occurrences,
    build_graph,
    find_definition,
)
from breakfast.rewrites import ArgumentMapper, rewrite_body
from breakfast.scope_graph import NodeType, ScopeGraph
from breakfast.search import (
    NodeFilter,
    find_names,
    find_other_nodes,
    find_returns,
    find_statements,
    get_nodes,
)
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


def register(refactoring: "type[Refactoring]") -> "type[Refactoring]":
    CodeSelection.register_refactoring(refactoring)
    return refactoring


@dataclass
class CodeSelection:
    text_range: TextRange
    _scope_graph: ScopeGraph | None = None
    _refactorings: ClassVar[dict[str, "type[Refactoring]"]] = {}

    @property
    def source(self) -> Source:
        return self.text_range.source

    @property
    def start(self) -> "Position":
        return self.text_range.start

    @property
    def end(self) -> "Position":
        return self.text_range.end

    def __contains__(self, other: Ranged) -> bool:
        return other in self.text_range

    @classmethod
    def register_refactoring(cls, refactoring: "type[Refactoring]") -> None:
        cls._refactorings[refactoring.name] = refactoring

    @property
    def refactorings(self) -> Sequence["type[Refactoring]"]:
        return [
            refactoring
            for refactoring in self._refactorings.values()
            if refactoring.applies_to(self)
        ]

    @property
    def scope_graph(self) -> ScopeGraph:
        if self._scope_graph is None:
            self._scope_graph = build_graph(
                [self.source], follow_redefinitions=False
            )
        return self._scope_graph

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

    @cached_property
    def name_at_cursor(self) -> str | None:
        return self.source.get_name_at(self.start)

    @cached_property
    def occurrences_of_name_at_cursor(self) -> Sequence[Occurrence]:
        try:
            return all_occurrences(self.start, graph=self.scope_graph)
        except NotFoundError:
            return ()

    def rtrim(self) -> "CodeSelection":
        lines = self.text_range.text.rstrip().split("\n")
        offset = 0
        last_line = lines[-1]
        start_offset = self.start.column if len(lines) == 1 else 0
        while self.end.column - (start_offset + offset) > len(last_line):
            offset += 1

        if offset == 0:
            return self

        return CodeSelection(
            text_range=replace(self.text_range, end=self.end - offset)
        )


class UsageCollector:
    def __init__(
        self,
        code_selection: CodeSelection,
        enclosing_scope: ScopeWithRange,
    ) -> None:
        self.selection = code_selection
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
            find_names(self.enclosing_scope.node, self.selection.source)
        ):
            if (
                occurrence.position < self.selection.start
                and occurrence.node_type is NodeType.DEFINITION
            ):
                if i == 1 and not (self.selection.in_static_method):
                    self.self_or_cls = occurrence
                self._defined_before[occurrence.name].append(occurrence)
            if occurrence.position in self.selection.text_range:
                self._used_in[occurrence.name].append(occurrence)
                if occurrence.node_type is NodeType.DEFINITION:
                    self._modified_in[occurrence.name].append(occurrence)
            if occurrence.position > self.selection.end:
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


class Refactoring(Protocol):
    name: str
    selection: CodeSelection

    def __init__(self, selection: CodeSelection): ...

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool: ...

    @property
    def edits(self) -> tuple[Edit, ...]: ...


@register
@dataclass
class ExtractFunction:
    name = "extract function"
    selection: CodeSelection

    @property
    def source(self) -> Source:
        return self.selection.source

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.end > selection.start

    @property
    def edits(self) -> tuple[Edit, ...]:
        return make_extract_callable_edits(refactoring=self, name="f")

    @staticmethod
    def make_call(
        has_returns: bool,
        arguments: Sequence[Occurrence],
        return_node: ast.Return | None,
        name: str,
        self_or_cls_name: str | None,
    ) -> ast.Call | ast.Assign | ast.Return:
        func = ast.Name(id=name)
        return make_call(return_node, func, arguments, has_returns)

    def compute_new_level(
        self,
        enclosing_scope: ScopeWithRange,
        start_of_scope: Position,
    ) -> int:
        if isinstance(enclosing_scope.node, ast.Module | ast.FunctionDef):
            insert_position = self.source.node_position(
                enclosing_scope.node.body[0]
            )
        else:
            insert_position = self.selection.start.line.start

        new_level = insert_position.column // 4
        return new_level

    def get_insert_position(
        self,
        enclosing_scope: ScopeWithRange,
    ) -> Position:
        if isinstance(enclosing_scope.node, ast.Module | ast.FunctionDef):
            insert_position = self.source.node_position(
                enclosing_scope.node.body[0]
            )
        else:
            insert_position = self.selection.start.line.start
        return insert_position

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
    selection: CodeSelection, return_node: ast.Return | None
) -> list[ast.stmt]:
    if selection.text_range.expression is not None:
        return [ast.Return(value=selection.text_range.expression)]

    nodes = list(selection.text_range.statements)
    if not nodes:
        if enclosing_assignment := selection.text_range.enclosing_assignment:
            targets = enclosing_assignment.node.targets
            if len(targets) > 1:
                value: ast.expr | ast.Tuple = ast.Tuple(targets)
            else:
                value = targets[0]
            nodes = [enclosing_assignment.node, ast.Return(value=value)]

    if return_node:
        nodes.append(return_node)
    return nodes


@register
@dataclass
class ExtractMethod:
    name = "extract method"
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return (
            selection.end > selection.start
            and selection.in_method
            and not selection.in_static_method
        )

    @property
    def edits(self) -> tuple[Edit, ...]:
        return make_extract_callable_edits(refactoring=self, name="m")

    @staticmethod
    def make_call(
        has_returns: bool,
        arguments: Sequence[Occurrence],
        return_node: ast.Return | None,
        name: str,
        self_or_cls_name: str | None,
    ) -> ast.Call | ast.Assign | ast.Return:
        if self_or_cls_name:
            arguments = [o for o in arguments if o.name != self_or_cls_name]
            func: ast.Attribute | ast.Name = ast.Attribute(
                value=ast.Name(id=self_or_cls_name), attr=name
            )
            calling_statement = make_call(
                return_node, func, arguments, has_returns
            )
        else:
            func = ast.Name(id=name)
            calling_statement = make_call(
                return_node, func, arguments, has_returns
            )
        return calling_statement

    @staticmethod
    def compute_new_level(
        enclosing_scope: ScopeWithRange, start_of_scope: Position
    ) -> int:
        return start_of_scope.column // 4

    @staticmethod
    def get_insert_position(
        enclosing_scope: ScopeWithRange,
    ) -> Position:
        return (
            enclosing_scope.end.line.next.start
            if enclosing_scope.end.line.next
            else enclosing_scope.end.line.end
        )

    def make_decorators(self, usages: UsageCollector) -> list[ast.expr]:
        if (
            usages.self_or_cls
            and usages.self_or_cls.name not in usages.used_in_selection
        ):
            decorator_list: list[ast.expr] = [ast.Name(STATIC_METHOD)]
        else:
            if usages.self_or_cls and self.selection.in_class_method:
                decorator_list = [ast.Name(CLASS_METHOD)]
            else:
                decorator_list = []
        return decorator_list


def make_extract_callable_edits(
    refactoring: ExtractFunction | ExtractMethod, name: str
) -> tuple[Edit, ...]:
    enclosing_scope = refactoring.selection.text_range.enclosing_scopes[-1]
    usages = UsageCollector(refactoring.selection, enclosing_scope)
    return_node = make_return_node(
        usages.modified_in_selection, usages.used_after_selection
    )
    body = make_body(selection=refactoring.selection, return_node=return_node)
    if not body:
        logger.warning("Could not extract callable body.")
        return ()
    name = make_unique_name(
        original_name=name,
        enclosing_scope=refactoring.selection.text_range.enclosing_scopes[0],
    )
    arguments = make_arguments(
        usages.defined_before_selection, usages.used_in_selection
    )
    decorator_list = refactoring.make_decorators(usages=usages)
    callable_definition = make_function(
        decorator_list=decorator_list, name=name, body=body, arguments=arguments
    )
    start_of_scope = enclosing_scope.start
    new_level = refactoring.compute_new_level(
        enclosing_scope=enclosing_scope, start_of_scope=start_of_scope
    )

    has_returns = any(
        found
        for node in refactoring.selection.text_range.statements
        for found in find_returns(node)
    )
    calling_statement = refactoring.make_call(
        has_returns=has_returns,
        arguments=arguments,
        return_node=return_node,
        name=name,
        self_or_cls_name=(
            usages.self_or_cls.name if usages.self_or_cls else None
        ),
    )
    insert_position = refactoring.get_insert_position(
        enclosing_scope=enclosing_scope
    )

    all_edits = (
        replace_with_node(
            insert_position.as_range,
            callable_definition,
            add_newline_after=True,
            add_indentation_after=True,
            level=new_level,
        ),
        replace_with_node(
            text_range=refactoring.selection.text_range,
            node=calling_statement,
        ),
    )
    return all_edits


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
    has_returns: bool,
) -> ast.Call | ast.Assign | ast.Return:
    call = ast.Call(
        func=func,
        args=[],
        keywords=[
            ast.keyword(arg=o.name, value=ast.Name(o.name)) for o in arguments
        ],
    )
    if return_node:
        if isinstance(return_node.value, ast.expr):
            return ast.Assign(targets=[return_node.value], value=call)
    elif has_returns:
        return ast.Return(value=call)
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
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.end > selection.start

    @property
    def edits(self) -> tuple[Edit, ...]:
        extracted = self.selection.text_range.text

        if not (expression := self.get_single_expression_value(extracted)):
            logger.warning("Could not extract single expression value.")
            return ()

        other_occurrences = find_other_nodes(
            source_ast=self.selection.source.ast,
            node=expression,
            position=self.selection.start,
        )

        enclosing_scope = self.selection.text_range.enclosing_scopes[-1]
        name = make_unique_name(
            original_name="v", enclosing_scope=enclosing_scope
        )

        target_range = get_body_range(enclosing_scope=enclosing_scope)

        other_edits = [
            (start := self.selection.source.node_position(o))
            .to(start + len(extracted))
            .replace(name)
            for o in other_occurrences
            if (node_position := self.selection.source.node_position(o))
            and node_position in target_range
        ]
        edits = sorted(
            [
                Edit(text_range=self.selection.text_range, text=name),
                *other_edits,
            ]
        )
        first_edit_position = edits[0].start

        preceding_statement_positions = list(
            takewhile(
                lambda p: p < first_edit_position,
                (
                    self.selection.source.node_position(s)
                    for s in find_statements(self.selection.source.ast)
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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.name_at_cursor is not None

    @property
    def edits(self) -> tuple[Edit, ...]:
        grouped: dict[bool, list[Occurrence]] = defaultdict(list)
        for o in self.selection.occurrences_of_name_at_cursor:
            grouped[o.node_type is NodeType.DEFINITION].append(o)

        last_definition = grouped.get(True, [None])[-1]

        if last_definition is None:
            logger.warning("Could not find definition.")
            return ()
        assignment = last_definition.position.as_range.enclosing_assignment
        if assignment is None:
            logger.warning("Could not find assignment for definition.")
            return ()

        name = self.selection.name_at_cursor
        if name is None:
            logger.warning("No variable at cursor that can be inlined.")
            return ()
        if self.selection.start in assignment.range:
            after_cursor = (
                o
                for o in dropwhile(
                    lambda x: x.position <= self.selection.start,
                    self.selection.occurrences_of_name_at_cursor,
                )
            )
            to_replace: tuple[TextRange, ...] = tuple(
                o.position.to(o.position + len(name))
                for o in takewhile(
                    lambda x: x.node_type is not NodeType.DEFINITION,
                    after_cursor,
                )
                if o.position > self.selection.start
            )
        else:
            to_replace = (
                self.selection.start.to(self.selection.start + len(name)),
            )

        if self.selection.start in assignment.range:
            can_remove_last_definition = True
        else:
            other_occurrences = [
                o
                for o in grouped.get(False, [])
                if o.position not in self.selection.text_range
            ]
            last_occurrence = (
                other_occurrences[-1] if other_occurrences else None
            )
            can_remove_last_definition = (
                last_occurrence is None
                or last_occurrence.position < assignment.start
            )

        edits: tuple[Edit, ...] = tuple(
            replace_with_node(name_range, assignment.node.value)
            for name_range in to_replace
        )

        if can_remove_last_definition:
            if len(assignment.node.targets) == 1:
                delete = delete_range(assignment.range)
            else:
                assignment.node.targets = [
                    t
                    for t in assignment.node.targets
                    if isinstance(t, ast.Name) and t.id != name
                ]
                delete = replace_with_node(assignment.range, assignment.node)
            edits = (*edits, delete)

        return edits


@register
@dataclass
class InlineCall:
    name = "inline call"
    selection: CodeSelection

    @property
    def scope_graph(self) -> ScopeGraph:
        return self.selection.scope_graph

    @property
    def text_range(self) -> TextRange:
        return self.selection.text_range

    @property
    def source(self) -> Source:
        return self.selection.source

    @property
    def enclosing_call(self) -> NodeWithRange[ast.Call] | None:
        return self.text_range.enclosing_call

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.text_range.enclosing_call is not None

    @property
    def edits(self) -> tuple[Edit, ...]:
        call = self.enclosing_call

        if not call:
            logger.warning("No enclosing call.")
            return ()

        name_start = self.get_start_of_name(call=call)

        definition = find_definition(self.scope_graph, name_start)
        if definition is None or definition.position is None:
            logger.warning(f"No definition position {definition=}.")
            return ()
        if not isinstance(definition.ast, ast.FunctionDef):
            logger.warning(f"Not a function {definition.ast=}.")
            return ()

        node_filter = self.make_filter(definition)
        found = next(
            get_nodes(definition.position.source.ast, node_filter), None
        )
        if not isinstance(found, ast.FunctionDef | ast.AsyncFunctionDef):
            return ()

        body_range = self.get_body_range(definition=definition, found=found)

        result = self.maybe_remove_definition(
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
        return result

    @staticmethod
    def maybe_remove_definition(
        name_start: Position, definition: Occurrence
    ) -> tuple[Edit, ...]:
        number_of_occurrences = len(all_occurrences(name_start))
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
            return ()
        return (Edit(definition_range, text=""),)

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
        name_start = call.range.start
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
            definition_ast.args, body_range, returned_names
        )
        arg_mapper.add_substitutions(call, substitutions)

        result: list[ast.stmt] = []
        result = rewrite_body(
            function_definition=definition_ast, substitutions=substitutions
        )
        return result


@register
@dataclass
class SlideStatementsUp:
    name = "slide statements up"
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return True

    @property
    def edits(self) -> tuple[Edit, ...]:
        target = self.find_slide_target_before()
        if target is None:
            return ()

        first, last = (self.selection.start.line, self.selection.end.line)
        insert = target.insert(first.start.through(last.end).text + NEWLINE)
        delete = first.start.to(
            last.next.start if last.next else last.end
        ).replace("")
        return (insert, delete)

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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return True

    @property
    def edits(self) -> tuple[Edit, ...]:
        target = self.find_slide_target_after()
        if target is None:
            return ()

        first, last = (self.selection.start.line, self.selection.end.line)
        insert = target.insert(first.start.through(last.end).text + NEWLINE)
        delete = first.start.to(
            last.next.start if last.next else last.end
        ).replace("")
        return (insert, delete)

    def find_slide_target_after(self) -> Position | None:
        first, last = (self.selection.start.line, self.selection.end.line)
        lines = first.start.to(last.end)
        names_defined_in_range = lines.definitions
        enclosing_scope = self.selection.text_range.enclosing_scopes[-1]
        usages = UsageCollector(self.selection, enclosing_scope)
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

        first_usage_after_range = enclosing_nodes[index].range.start

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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return len(selection.text_range.enclosing_scopes) >= 3 and isinstance(
            selection.text_range.enclosing_scopes[-1].node,
            ast.FunctionDef | ast.AsyncFunctionDef,
        )

    @property
    def edits(self) -> tuple[Edit, ...]:
        enclosing_scope = self.selection.text_range.enclosing_scopes[-1]
        result: tuple[Edit, ...] = (Edit(enclosing_scope.range, ""),)

        if not (
            scope := self.closest_enclosing_non_class_scope(
                selection=self.selection
            )
        ):
            logger.warning("Not inside an appropriately nested scope.")
            return ()

        insert_position = (
            scope.end.line.next.start
            if scope.end.line.next
            else scope.end.line.end
        )
        result = (
            *result,
            replace_with_node(insert_position.as_range, enclosing_scope.node),
        )

        return result

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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return bool(selection.text_range.enclosing_nodes_by_type(ast.arg))

    @property
    def edits(self) -> tuple[Edit, ...]:
        if not self.is_parameter_unused:
            logger.warning(
                "Can't remove parameter that is used in function body."
            )
            return ()

        return (self.function_definition_edit, *self.call_edits)

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
                    for o in all_occurrences(self.arg.range.start)
                    if o.position in self.function_definition.range
                ]
            )
            == 1
        )

    @property
    def call_edits(self) -> Sequence[Edit]:
        call_edits = []
        index = self.function_definition.node.args.args.index(self.arg.node)
        for occurrence in all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.node_type is not NodeType.REFERENCE:
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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return bool(
            selection.text_range.enclosing_nodes_by_type(ast.FunctionDef)
        )

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]

    @property
    def edits(self) -> tuple[Edit, ...]:
        arg_name = make_unique_name(
            original_name="p", enclosing_scope=self.function_definition
        )
        return (
            self.function_definition_edit(arg_name),
            *self.call_edits(arg_name),
        )

    def call_edits(self, arg_name: str) -> Sequence[Edit]:
        call_edits = []
        for occurrence in all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.node_type is not NodeType.REFERENCE:
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
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return bool(selection.text_range.enclosing_nodes_by_type(ast.Dict))

    @property
    def edits(self) -> tuple[Edit, ...]:
        enclosing_assignment = self.selection.text_range.enclosing_assignment
        if enclosing_assignment is None:
            logger.warning("Dictionary value not assigned to a name.")
            return ()

        dict_node = self.selection.text_range.enclosing_nodes_by_type(ast.Dict)[
            -1
        ]
        mapping = dict(
            zip(dict_node.node.keys, dict_node.node.values, strict=True)
        )

        class_name = self.make_class_name(enclosing_assignment.node) or "Record"
        fake_module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="dataclasses",
                    names=[ast.alias(name="dataclass")],
                    level=0,
                ),
                ast.ClassDef(
                    name=class_name,
                    body=[
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
                        for key, value in mapping.items()
                        if isinstance(key, ast.Constant)
                    ],
                    decorator_list=[ast.Name(id="dataclass")],
                    bases=[],
                    keywords=[],
                    type_params=[],
                ),
            ],
            type_ignores=[],
        )
        definition = replace_with_node(
            enclosing_assignment.range.start.as_range,
            fake_module,
            add_newline_after=True,
        )
        assignment = replace_with_node(
            enclosing_assignment.range,
            ast.Assign(
                targets=enclosing_assignment.node.targets,
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

        references = []
        for occurrence in all_occurrences(
            self.selection.source.node_position(enclosing_assignment.node)
        ):
            if occurrence.node_type is not NodeType.REFERENCE:
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
                references.append(
                    replace_with_node(
                        subscripts[0].range,
                        ast.Attribute(value=node.value, attr=node.slice.value),
                    )
                )
        return (definition, assignment, *references)

    @staticmethod
    def make_class_name(assignment: ast.Assign) -> str | None:
        if not (assignment and isinstance(assignment.targets[0], ast.Name)):
            return None

        var_name = assignment.targets[0].id
        return to_class_name(var_name)


@register
@dataclass
class MethodToProperty:
    name = "convert method to property"
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.in_method and not selection.in_property

    @property
    def edits(self) -> tuple[Edit, ...]:
        definition = self.function_definition.node
        add_decorator = replace_with_node(
            self.function_definition.range,
            copy_function_def(
                definition,
                decorator_list=[
                    ast.Name(id=PROPERTY),
                    *definition.decorator_list,
                ],
            ),
        )
        replace_calls = []
        for occurrence in all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.node_type is not NodeType.REFERENCE:
                continue
            if not (
                calls := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Call
                )
            ):
                continue
            node = calls[-1].node

            replace_calls.append(replace_with_node(calls[-1].range, node.func))

        return (add_decorator, *replace_calls)

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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.in_property

    @property
    def edits(self) -> tuple[Edit, ...]:
        definition = self.function_definition.node
        new_function = copy_function_def(
            definition,
            decorator_list=[
                d
                for d in definition.decorator_list
                if (not isinstance(d, ast.Name) or d.id != PROPERTY)
            ],
        )
        start = self.function_definition.range.start
        for _ in definition.decorator_list:
            start = start.line.previous.start if start.line.previous else start
        range_with_decorators = start.through(self.function_definition.end)
        remove_decorator = replace_with_node(
            range_with_decorators, new_function
        )
        replace_references = []
        for occurrence in all_occurrences(
            self.selection.source.node_position(self.function_definition.node)
            + len("def ")
        ):
            if occurrence.node_type is not NodeType.REFERENCE:
                continue
            if not (
                attributes
                := occurrence.position.as_range.enclosing_nodes_by_type(
                    ast.Attribute
                )
            ):
                continue
            node = attributes[-1].node

            replace_references.append(
                replace_with_node(
                    attributes[-1].range,
                    ast.Call(func=node, args=[], keywords=[]),
                )
            )

        return (remove_decorator, *replace_references)

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
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.in_method and not selection.in_static_method

    @property
    def edits(self) -> tuple[Edit, ...]:
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
        replace_properties = []
        for assignment in assignments:
            if isinstance(assignment.targets[0], ast.Attribute) and isinstance(
                assignment.targets[0].value, ast.Name
            ):
                for occurrence in all_occurrences(
                    self.selection.source.node_position(assignment.targets[0])
                    + len(assignment.targets[0].value.id)
                    + 1
                ):
                    if occurrence.node_type is not NodeType.REFERENCE:
                        continue
                    attribute = (
                        occurrence.position.as_range.enclosing_nodes_by_type(
                            ast.Attribute
                        )[0]
                    )
                    replace_properties.append(
                        replace_with_node(
                            attribute.range,
                            ast.Attribute(
                                value=ast.Attribute(
                                    value=attribute.node.value,
                                    attr=property_name,
                                ),
                                attr=attribute.node.attr,
                            ),
                        )
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
        fake_module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="dataclasses",
                    names=[ast.alias(name="dataclass")],
                    level=0,
                ),
                ast.ClassDef(
                    name=class_name,
                    body=new_assignments,
                    decorator_list=[ast.Name(id="dataclass")],
                    bases=[],
                    keywords=[],
                    type_params=[],
                ),
            ],
            type_ignores=[],
        )
        add_class_definition = replace_with_node(
            self.selection.text_range.enclosing_scopes[0].range.start.as_range,
            fake_module,
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
        replace_assignments = replace_with_node(
            self.function_definition.range,
            copy_function_def(definition, body=new_body),
        )
        return (add_class_definition, replace_assignments, *replace_properties)

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


@register
@dataclass
class ReplaceWithMethodObject:
    name = "replace with method object"
    selection: CodeSelection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return (
            selection.end > selection.start
            and selection.in_method
            and not selection.in_static_method
        )

    @property
    def edits(self) -> tuple[Edit, ...]:
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

        insert_method_object_class = replace_with_node(
            self.selection.text_range.enclosing_scopes[0].range.start.as_range,
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

        replace_method = replace_with_node(
            self.function_definition.range, call_method_object
        )

        return (insert_method_object_class, replace_method)

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
            for o in all_occurrences(arg_position)
            if o.position in body_range and o.ast
        ]

    @property
    def function_definition(self) -> NodeWithRange[ast.FunctionDef]:
        return self.selection.text_range.enclosing_nodes_by_type(
            ast.FunctionDef
        )[-1]


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
