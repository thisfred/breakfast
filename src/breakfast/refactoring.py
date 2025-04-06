import ast
import logging
from collections import defaultdict
from collections.abc import (
    Callable,
    Container,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)
from functools import cached_property, singledispatch
from itertools import chain, dropwhile, takewhile
from typing import Any, ClassVar, Protocol

from breakfast.code_generation import to_source, unparse
from breakfast.names import (
    all_occurrences,
    build_graph,
    find_definition,
)
from breakfast.scope_graph import NodeType, ScopeGraph
from breakfast.search import (
    NodeFilter,
    find_names,
    find_other_occurrences,
    find_returns,
    find_statements,
    get_nodes,
)
from breakfast.types import (
    Edit,
    NodeWithRange,
    NotFoundError,
    Occurrence,
    Position,
    ScopeWithRange,
    TextRange,
)
from breakfast.visitor import generic_transform

logger = logging.getLogger(__name__)

FOUR_SPACES = "    "
NEWLINE = "\n"
STATIC_METHOD = "staticmethod"
CLASS_METHOD = "classmethod"


COMPARISONS: dict[type[ast.AST], Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.NotEq: lambda a, b: a != b,
    ast.NotIn: lambda a, b: a not in b,
}


def register(refactoring: "type[Refactoring]") -> "type[Refactoring]":
    CodeSelection.register_refactoring(refactoring)
    return refactoring


class CodeSelection:
    _refactorings: ClassVar[dict[str, "type[Refactoring]"]] = {}

    def __init__(self, text_range: TextRange):
        self.text_range = text_range
        self.source = self.text_range.source
        self.scope_graph = build_graph(
            [self.source], follow_redefinitions=False
        )

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
    def name_at_cursor(self) -> str | None:
        return self.source.get_name_at(self.cursor)

    @cached_property
    def cursor(self) -> Position:
        return self.text_range.start

    @cached_property
    def occurrences_of_name_at_cursor(self) -> Sequence[Occurrence]:
        try:
            return all_occurrences(self.cursor, graph=self.scope_graph)
        except NotFoundError:
            return ()

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


class UsageCollector:
    def __init__(
        self,
        code_selection: CodeSelection,
        enclosing_scope: ScopeWithRange,
    ) -> None:
        self.code_selection = code_selection
        self.enclosing_scope = enclosing_scope
        self._defined_before_extraction: dict[str, list[Occurrence]] = (
            defaultdict(list)
        )
        self._used_in_extraction: dict[str, list[Occurrence]] = defaultdict(
            list
        )
        self._modified_in_extraction: dict[str, list[Occurrence]] = defaultdict(
            list
        )
        self._used_after_extraction: dict[str, list[Occurrence]] = defaultdict(
            list
        )
        self.self_or_cls: Occurrence | None = None

    @property
    def defined_before_extraction(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._defined_before_extraction:
            self._collect()

        return self._defined_before_extraction or {}

    @property
    def used_in_extraction(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._used_in_extraction:
            self._collect()

        return self._used_in_extraction or {}

    @property
    def modified_in_extraction(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._modified_in_extraction:
            self._collect()

        return self._modified_in_extraction or {}

    @property
    def used_after_extraction(self) -> Mapping[str, Sequence[Occurrence]]:
        if not self._used_after_extraction:
            self._collect()

        return self._used_after_extraction or {}

    def _collect(self) -> None:
        for i, occurrence in enumerate(
            find_names(self.enclosing_scope.node, self.code_selection.source)
        ):
            if (
                occurrence.position < self.code_selection.text_range.start
                and occurrence.node_type is NodeType.DEFINITION
            ):
                if i == 1 and not (self.code_selection.in_static_method):
                    self.self_or_cls = occurrence
                self._defined_before_extraction[occurrence.name].append(
                    occurrence
                )
            if occurrence.position in self.code_selection.text_range:
                self._used_in_extraction[occurrence.name].append(occurrence)
                if occurrence.node_type is NodeType.DEFINITION:
                    self._modified_in_extraction[occurrence.name].append(
                        occurrence
                    )
            if occurrence.position > self.code_selection.text_range.end:
                self._used_after_extraction[occurrence.name].append(occurrence)

    def get_subsequent_usage(
        self,
        names_defined_in_range: Iterable[Occurrence],
    ) -> Position | None:
        occurrences_after_range = sorted(
            [
                o.position
                for n in names_defined_in_range
                for o in self.used_after_extraction.get(n.name, [])
            ]
        )
        first_usage_after_range = (
            occurrences_after_range[0] if occurrences_after_range else None
        )
        return first_usage_after_range


class Refactoring(Protocol):
    name: str

    def __init__(self, selection: CodeSelection): ...
    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool: ...

    @property
    def edits(self) -> tuple[Edit, ...]: ...


@register
class ExtractFunction:
    name = "extract function"

    def __init__(self, code_selection: CodeSelection):
        self.code_selection = code_selection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.text_range.end > selection.text_range.start

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
        new_level = start_of_scope.column // 4
        if not isinstance(enclosing_scope.node, ast.Module):
            match self.code_selection.text_range.enclosing_scopes:
                case [
                    NodeWithRange(node=ast.Module()),
                    NodeWithRange(node=ast.ClassDef()),
                    *_,
                ]:
                    new_level = 0
        return new_level

    def get_insert_position(
        self,
        enclosing_scope: ScopeWithRange,
    ) -> Position:
        if isinstance(enclosing_scope.node, ast.Module):
            insert_position = self.code_selection.text_range.start.line.start
        else:
            match self.code_selection.text_range.enclosing_scopes:
                case (
                    [
                        NodeWithRange(node=ast.Module()),
                        NodeWithRange(node=ast.ClassDef()),
                        *_,
                    ] as matched
                ):
                    last_line = matched[1].range.end.line
                case _:
                    last_line = self.code_selection.text_range.enclosing_scopes[
                        -1
                    ].range.end.line
            insert_position = (
                next_line.start
                if (next_line := last_line.next)
                else last_line.end
            )
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
        name = f"{original_name}{counter}"
        counter += 1
    return name


def make_body(
    selection: CodeSelection, return_node: ast.Return | None
) -> list[ast.stmt]:
    nodes = list(selection.text_range.statements)
    if not nodes:
        if enclosing_assignment := selection.text_range.enclosing_assignment:
            targets = enclosing_assignment.node.targets
            if len(targets) > 1:
                value: ast.expr | ast.Tuple = ast.Tuple(targets)
            else:
                value = targets[0]
            nodes = [enclosing_assignment.node, ast.Return(value=value)]
        else:
            if (
                enclosing_returns
                := selection.text_range.enclosing_nodes_by_type(ast.Return)
            ):
                nodes = [enclosing_returns[-1].node]

    if return_node:
        nodes.append(return_node)
    return nodes


@register
class ExtractMethod:
    name = "extract method"

    def __init__(self, code_selection: CodeSelection):
        self.code_selection = code_selection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return (
            selection.text_range.end > selection.text_range.start
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
            enclosing_scope.range.end.line.next.start
            if enclosing_scope.range.end.line.next
            else enclosing_scope.range.end.line.end
        )

    def make_decorators(self, usages: UsageCollector) -> list[ast.expr]:
        if (
            usages.self_or_cls
            and usages.self_or_cls.name not in usages.used_in_extraction
        ):
            decorator_list: list[ast.expr] = [ast.Name(STATIC_METHOD)]
        else:
            if usages.self_or_cls and self.code_selection.in_class_method:
                decorator_list = [ast.Name(CLASS_METHOD)]
            else:
                decorator_list = []
        return decorator_list


def make_extract_callable_edits(
    refactoring: ExtractFunction | ExtractMethod, name: str
) -> tuple[Edit, ...]:
    enclosing_scope = refactoring.code_selection.text_range.enclosing_scopes[-1]
    usages = UsageCollector(refactoring.code_selection, enclosing_scope)
    return_node = make_return_node(
        usages.modified_in_extraction, usages.used_after_extraction
    )
    body = make_body(
        selection=refactoring.code_selection, return_node=return_node
    )
    if not body:
        logger.warning("Could not extract callable body.")
        return ()
    name = make_unique_name(
        name,
        enclosing_scope=refactoring.code_selection.text_range.enclosing_scopes[
            0
        ],
    )
    arguments = make_arguments(
        usages.defined_before_extraction, usages.used_in_extraction
    )
    decorator_list = refactoring.make_decorators(usages=usages)
    callable_definition = make_function(
        decorator_list=decorator_list, name=name, body=body, arguments=arguments
    )
    start_of_scope = enclosing_scope.range.start
    new_level = refactoring.compute_new_level(
        enclosing_scope=enclosing_scope, start_of_scope=start_of_scope
    )
    definition_text = f"{NEWLINE}{"".join(to_source(callable_definition, level=new_level))}{NEWLINE}"
    has_returns = any(
        found
        for node in refactoring.code_selection.text_range.statements
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
    call_text = "".join(to_source(calling_statement, level=0))
    original_indentation = (
        refactoring.code_selection.text_range.start.indentation
    )
    call_text = (
        f"{original_indentation}{call_text}"
        if refactoring.code_selection.text_range.start.column == 0
        else call_text
    )
    insert_position = refactoring.get_insert_position(
        enclosing_scope=enclosing_scope
    )
    all_edits = (
        Edit(insert_position.as_range, text=definition_text),
        Edit(
            refactoring.code_selection.text_range.start.to(
                refactoring.code_selection.text_range.end
            ),
            text=call_text,
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
    return ast.arg(arg=occurrence.name)


@register
class ExtractVariable:
    name = "extract variable"

    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.source = self.text_range.start.source

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.text_range.end > selection.text_range.start

    @property
    def edits(self) -> tuple[Edit, ...]:
        extracted = self.text_range.text

        if not (expression := self.get_single_expression_value(extracted)):
            logger.warning("Could not extract single expression value.")
            return ()

        other_occurrences = find_other_occurrences(
            source_ast=self.source.ast,
            node=expression,
            position=self.text_range.start,
        )

        enclosing_scope = self.text_range.enclosing_scopes[-1]
        name = make_unique_name("v", enclosing_scope=enclosing_scope)
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

        preceding_statement_positions = list(
            takewhile(
                lambda p: p < first_edit_position,
                (
                    self.source.node_position(s)
                    for s in find_statements(self.source.ast)
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


@register
class InlineVariable:
    name = "inline variable"

    def __init__(self, code_selection: CodeSelection):
        self.text_range = code_selection.text_range
        self.cursor = code_selection.cursor
        self.source = self.text_range.start.source
        self.code_selection = code_selection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return selection.name_at_cursor is not None

    @property
    def edits(self) -> tuple[Edit, ...]:
        grouped: dict[bool, list[Occurrence]] = defaultdict(list)
        for o in self.code_selection.occurrences_of_name_at_cursor:
            grouped[o.node_type is NodeType.DEFINITION].append(o)

        last_definition = grouped.get(True, [None])[-1]

        if last_definition is None:
            logger.warning("Could not find definition.")
            return ()
        assignment = last_definition.position.as_range.enclosing_assignment
        if assignment is None:
            logger.warning("Could not find assignment for definition.")
            return ()

        name = self.code_selection.name_at_cursor
        if name is None:
            logger.warning("No variable at cursor that can be inlined.")
            return ()
        if self.cursor in assignment.range:
            after_cursor = (
                o
                for o in dropwhile(
                    lambda x: x.position <= self.cursor,
                    self.code_selection.occurrences_of_name_at_cursor,
                )
            )
            to_replace: tuple[TextRange, ...] = tuple(
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

        number_of_occurrences = len(all_occurrences(name_start))
        if number_of_occurrences <= 2:
            edits: tuple[Edit, ...] = (
                (Edit(definition_range, text=""),)
                if (
                    definition_range := definition.position.source.node_range(
                        definition.ast
                    )
                )
                else ()
            )

        else:
            edits = ()

        new_statements = self.get_new_statements(
            call=call.node, body_range=body_range, definition_ast=definition.ast
        )

        indentation = self.text_range.start.indentation

        return_ranges = [
            return_range
            for statement in definition.ast.body
            for r in find_returns(statement)
            if (return_range := self.source.node_range(r))
        ]
        if return_ranges:
            name = "result"
            edits = (Edit(call.range, text=name), *edits)
        insert_range = (
            self.text_range.start.line.start.as_range
            if return_ranges
            else call.range
        )
        body = "".join(
            to_source(
                ast.Module(body=new_statements, type_ignores=[]),
                level=len(indentation) // 4,
            )
        )
        edits = (
            Edit(
                insert_range,
                text=f"{body or "pass"}{NEWLINE}",
            ),
            *edits,
        )
        return edits

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

        seen = set()
        for keyword in call.keywords:
            argument: ast.keyword | ast.arg = keyword
            seen.add(argument.arg)
            value = keyword.value

            self.add_substitutions(
                argument=argument,
                value=value,
                body_range=body_range,
                substitutions=substitutions,
                returned_names=returned_names,
            )

        values = call.args
        if isinstance(call.func, ast.Attribute):
            values = [call.func.value, *values]
        for argument, value in zip(
            (a for a in definition_ast.args.args if a.arg not in seen),
            values,
            strict=True,
        ):
            self.add_substitutions(
                argument=argument,
                value=value,
                body_range=body_range,
                substitutions=substitutions,
                returned_names=returned_names,
            )

        result: list[ast.stmt] = []
        result = [
            s
            for node in definition_ast.body
            for s in substitute_nodes(node, substitutions)
            if isinstance(s, ast.stmt)
        ]
        return result

    def add_substitutions(
        self,
        argument: ast.keyword | ast.arg,
        value: ast.expr,
        body_range: TextRange,
        substitutions: MutableMapping[ast.AST, ast.AST],
        returned_names: Container[str],
    ) -> None:
        occurrences = self.get_occurrences(argument, body_range)
        if argument.arg in returned_names or all(
            o.node_type is NodeType.REFERENCE for o in occurrences
        ):
            for occurrence in occurrences:
                if occurrence.ast:
                    substitutions[occurrence.ast] = value

    def get_occurrences(
        self, argument: ast.keyword | ast.arg, body_range: TextRange
    ) -> Sequence[Occurrence]:
        assert argument.arg is not None  # noqa: S101
        arg_position = self.source.node_position(argument)
        return [
            o
            for o in all_occurrences(arg_position)
            if o.position in body_range and o.ast
        ]


@register
class SlideStatementsUp:
    name = "slide statements up"

    def __init__(
        self,
        code_selection: CodeSelection,
    ):
        self.text_range = code_selection.text_range
        self.code_selection = code_selection

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return True

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

    @classmethod
    def applies_to(cls, selection: CodeSelection) -> bool:
        return True

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
        enclosing_scope = self.code_selection.text_range.enclosing_scopes[-1]
        usages = UsageCollector(self.code_selection, enclosing_scope)
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


@singledispatch
def substitute_nodes(
    node: ast.AST,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    if node in substitutions:
        yield from generic_transform(
            substitute_nodes, substitutions[node], substitutions
        )
    else:
        yield from generic_transform(substitute_nodes, node, substitutions)


@substitute_nodes.register
def substitute_nodes_in_name(
    node: ast.Name,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    substitution = substitutions.get(node)
    if substitution is None:
        yield node
    else:
        yield substitution


@substitute_nodes.register
def substitute_nodes_in_constant(
    node: ast.Constant,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    yield node


@substitute_nodes.register
def substitute_nodes_in_attribute(
    node: ast.Attribute,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    new_value = next(substitute_nodes(node.value, substitutions), None)
    if isinstance(new_value, ast.expr):
        yield ast.Attribute(new_value, attr=node.attr)
    else:
        yield node


@substitute_nodes.register
def substitute_nodes_in_if(
    node: ast.If,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    test = next(substitute_nodes(node.test, substitutions), None)
    body = (
        s
        for s in (
            chain.from_iterable(
                substitute_nodes(s, substitutions) for s in node.body
            )
        )
        if isinstance(s, ast.stmt)
    )
    orelse = (
        s
        for s in (
            chain.from_iterable(
                substitute_nodes(s, substitutions) for s in node.orelse
            )
        )
        if isinstance(s, ast.stmt)
    )
    if test:
        if always_true(test):
            yield from body
        elif always_false(test):
            yield from orelse
        else:
            if as_list := list(orelse):
                yield (
                    ast.If(
                        test=test,
                        body=list(body) or [ast.Pass()],
                        orelse=as_list,
                    )
                    if isinstance(test, ast.expr)
                    else node
                )
            elif as_list := list(body):
                yield (
                    ast.If(test=test, body=as_list, orelse=[])
                    if isinstance(test, ast.expr)
                    else node
                )


@substitute_nodes.register
def substitute_nodes_in_bool_op(
    node: ast.BoolOp,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    transformed = chain.from_iterable(
        substitute_nodes(value, substitutions) for value in node.values
    )
    if isinstance(node.op, ast.And):
        new_values = [
            v
            for v in transformed
            if not always_true(v) and isinstance(v, ast.expr)
        ]
        if len(new_values) == 1:
            yield new_values[0]
        else:
            yield (
                ast.BoolOp(op=ast.And(), values=new_values)
                if new_values
                else node
            )
    elif isinstance(node.op, ast.Or):
        new_values = [
            v
            for v in transformed
            if not always_false(v) and isinstance(v, ast.expr)
        ]
        if len(new_values) == 1:
            yield new_values[0]
        else:
            yield (
                ast.BoolOp(op=ast.Or(), values=new_values)
                if new_values
                else node
            )
    else:
        yield node


@singledispatch
def always_true(node: ast.AST) -> bool:
    return False


@always_true.register
def always_true_constant(node: ast.Constant) -> bool:
    return bool(node.value)


@always_true.register
def always_true_unary_op(node: ast.UnaryOp) -> bool:
    if isinstance(node.op, ast.Not):
        return always_false(node.operand)

    return False


@always_true.register
def always_true_compare(node: ast.Compare) -> bool:
    if not isinstance(node.left, ast.Constant):
        return False
    prev = node.left.value
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        if not isinstance(comparator, ast.Constant):
            return False
        if not COMPARISONS[type(op)](prev, comparator.value):
            return False
        prev = comparator.value
    return True


@always_true.register
def always_true_bool_op(node: ast.BoolOp) -> bool:
    if isinstance(node.op, ast.Or):
        for value in node.values:
            if always_true(value):
                return True
        return False

    if isinstance(node.op, ast.And):
        for value in node.values:
            if not always_true(value):
                return False
        return True

    return False


@singledispatch
def always_false(node: ast.AST) -> bool:
    return False


@always_false.register
def always_false_unary_op(node: ast.UnaryOp) -> bool:
    if isinstance(node.op, ast.Not):
        return always_true(node.operand)

    return False


@always_false.register
def always_false_constant(node: ast.Constant) -> bool:
    return not bool(node.value)


@always_false.register
def always_false_compare(node: ast.Compare) -> bool:
    if not isinstance(node.left, ast.Constant):
        return False
    prev = node.left.value
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        if not isinstance(comparator, ast.Constant):
            return False
        if COMPARISONS[type(op)](prev, comparator.value):
            return False
        prev = comparator.value
    return True


@always_false.register
def always_false_bool_op(node: ast.BoolOp) -> bool:
    if isinstance(node.op, ast.And):
        for value in node.values:
            if always_false(value):
                return True
        return False

    if isinstance(node.op, ast.Or):
        for value in node.values:
            if not always_false(value):
                return False
        return True

    return False
