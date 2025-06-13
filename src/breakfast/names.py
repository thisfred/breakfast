from __future__ import annotations

import ast
import logging

try:
    from ast import TypeVar
except ImportError:  # pragma: nocover
    TypeVar = None  # type: ignore[assignment,misc]

from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import singledispatch
from typing import Protocol, Self

from breakfast import types
from breakfast.types import Occurrence, Position
from breakfast.visitor import generic_visit

STATIC_METHOD = "staticmethod"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NameOccurrence:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool


@dataclass(frozen=True)
class SuperCall:
    occurrence: NameOccurrence


@dataclass(frozen=True)
class Nonlocal:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool = False


@dataclass(frozen=True)
class Global:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool = False


@dataclass
class Delay:
    delayed: Iterator[object]


@singledispatch
def occurrence(node: ast.AST, source: types.Source) -> NameOccurrence | None:
    return None


@occurrence.register
def name_occurrence(
    node: ast.Name, source: types.Source
) -> NameOccurrence | None:
    return NameOccurrence(
        name=node.id,
        position=source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )


@occurrence.register
def ann_assign(
    node: ast.AnnAssign, source: types.Source
) -> NameOccurrence | None:
    return occurrence(node.target, source=source)


@occurrence.register
def assign(node: ast.Assign, source: types.Source) -> NameOccurrence | None:
    return occurrence(node.targets[0], source=source)


@occurrence.register
def class_occurrence(
    node: ast.ClassDef, source: types.Source
) -> NameOccurrence | None:
    return definition(node=node, source=source, prefix="class ")


@occurrence.register
def function_occurrence(
    node: ast.FunctionDef, source: types.Source
) -> NameOccurrence | None:
    return definition(node=node, source=source, prefix="def ")


@occurrence.register
def async_function_occurrence(
    node: ast.AsyncFunctionDef, source: types.Source
) -> NameOccurrence | None:
    return definition(node=node, source=source, prefix="async def ")


def definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    source: types.Source,
    prefix: str = "",
) -> NameOccurrence | None:
    return NameOccurrence(
        name=node.name,
        position=source.node_position(node) + len(prefix),
        ast=node,
        is_definition=True,
    )


class Namespace(Protocol):
    attributes: dict[str, Name]


@dataclass
class Name:
    attributes: dict[str, Name]
    types: list[Namespace]
    occurrences: set[Occurrence]

    @classmethod
    def new(cls) -> Self:
        return cls(attributes={}, types=[], occurrences=set())


@dataclass
class Scope:
    module: tuple[str, ...]
    attributes: dict[str, Name]
    children: list[Scope]
    blocks: dict[str, Scope]
    is_block: bool = False
    is_class: bool = False
    parent: Scope | None = None
    name: str | None = None

    def lookup(self, name: str) -> Name | None:
        if name in self.attributes:
            return self.attributes[name]

        if self.parent:
            return self.parent.lookup(name)

        return None

    def get_or_create(self, occurrence: NameOccurrence) -> Name:
        result = self.attributes.setdefault(occurrence.name, Name.new())
        result.occurrences.add(occurrence)
        return result

    def add_child(
        self, name: str | None = None, is_class: bool = False
    ) -> Scope:
        if name:
            if name in self.blocks:
                return self.blocks[name]

            child = Scope(
                module=self.module,
                attributes={},
                children=[],
                blocks={},
                parent=self,
                is_class=is_class,
                name=name,
            )
            self.blocks[name] = child
        else:
            child = Scope(
                module=self.module,
                attributes={},
                children=[],
                blocks={},
                parent=self,
            )
        self.children.append(child)
        return child


@dataclass(frozen=True)
class EnterScope:
    name: str | None = None
    is_class: bool = False


@dataclass(frozen=True)
class EnterFunctionScope:
    occurrence: NameOccurrence


@dataclass(frozen=True)
class MoveToScope:
    event: NameOccurrence | Attribute


class ReturnFromScope: ...


@dataclass(frozen=True)
class MoveToModule:
    name: tuple[str, ...]


class ReturnFromModule: ...


class LeaveScope: ...


@dataclass(frozen=True)
class Attribute:
    value: NameOccurrence | Attribute
    attribute: NameOccurrence


@dataclass(frozen=True)
class ClassAttribute:
    class_occurrence: NameOccurrence
    attribute: NameOccurrence


@dataclass(frozen=True)
class Bind:
    target: NameOccurrence | Attribute
    value: NameOccurrence | Attribute


@dataclass(frozen=True)
class BindImportFrom:
    occurrence: NameOccurrence
    module: tuple[str, ...]
    level: int


@dataclass(frozen=True)
class BindImport:
    occurrence: NameOccurrence | Attribute


@dataclass(frozen=True)
class BaseClass:
    class_occurrence: NameOccurrence | Attribute
    base: NameOccurrence | Attribute


@dataclass
class FirstArgument:
    arg: NameOccurrence


def all_occurrences(
    position: types.Position,
    *,
    sources: Sequence[types.Source],
) -> list[Occurrence]:
    collector = NameCollector.from_sources(sources)
    return sorted(
        collector.all_occurrences_for(position), key=lambda o: o.position
    )


def all_occurrence_positions(
    position: Position,
    *,
    sources: Sequence[types.Source],
) -> list[types.Position]:
    result = sorted(
        o.position for o in all_occurrences(position, sources=sources)
    )
    return result


@dataclass
class NameCollector:
    positions: dict[types.Position, Name | None]
    delays: deque[tuple[Scope, Iterator[object]]]
    current_scope: Scope
    previous_scopes: list[Scope]
    name_scopes: dict[int, Scope]
    modules: dict[tuple[str, ...], Scope]

    @classmethod
    def from_sources(cls, sources: Sequence[types.Source]) -> Self:
        instance = None
        for source in import_ordered(sources):
            module = tuple(source.module_name)
            scope = Scope(module=module, attributes={}, blocks={}, children=[])
            if instance is None:
                instance = cls(
                    positions={},
                    delays=deque([]),
                    previous_scopes=[],
                    name_scopes={},
                    current_scope=scope,
                    modules={module: scope},
                )
            else:
                instance.enter_module(module)

            for event in find_names(source.ast, source):
                process(event, instance)
            while instance.delays:
                scope, iterator = instance.delays.popleft()
                old_current = instance.current_scope
                instance.current_scope = scope
                for event in iterator:
                    process(event, instance)
                instance.current_scope = old_current

        if instance is None:
            raise types.NotFoundError()
        return instance

    def all_occurrences_for(self, position: types.Position) -> set[Occurrence]:
        name = self.positions[position]
        if name:
            return name.occurrences

        return set()

    def add_occurrence(self, occurrence: NameOccurrence) -> None:
        if not self.positions.get(occurrence.position):
            self.add_name(occurrence)

    def add_nonlocal(self, occurrence: Occurrence) -> None:
        name = self.current_scope.lookup(occurrence.name)
        if name is None:
            return
        name.occurrences.add(occurrence)
        self.current_scope.attributes[occurrence.name] = name

    def add_global(self, occurrence: Occurrence) -> None:
        global_scope = self.modules[self.current_scope.module]
        if global_scope is None:
            return

        name = global_scope.lookup(occurrence.name)
        if name is None:
            return

        name.occurrences.add(occurrence)
        self.current_scope.attributes[occurrence.name] = name

    def add_name(self, occurrence: NameOccurrence) -> Name | None:
        if occurrence.is_definition:
            name: Name | None = self.current_scope.get_or_create(occurrence)
        else:
            name = self.current_scope.lookup(occurrence.name)
            if name:
                name.occurrences.add(occurrence)
        self.positions[occurrence.position] = name
        return name

    def add_class_attribute(
        self,
        attribute: NameOccurrence,
        class_occurrence: NameOccurrence,
    ) -> None:
        if self.current_scope.parent is None:
            return

        cls = self.current_scope.parent.attributes[class_occurrence.name]
        self.add_attribute_occurrence(value=cls, attribute_occurrence=attribute)

    def add_attribute(
        self,
        value: Occurrence | Attribute,
        occurrence: NameOccurrence,
    ) -> None:
        current = self.lookup(value)
        if current is None:
            return
        self.add_attribute_occurrence(
            value=current, attribute_occurrence=occurrence
        )

    def add_attribute_occurrence(
        self, value: Name, attribute_occurrence: NameOccurrence
    ) -> None:
        for parent_type in (value, *value.types):
            if result := parent_type.attributes.get(attribute_occurrence.name):
                break
        else:
            if value.types:
                result = value.types[0].attributes.setdefault(
                    attribute_occurrence.name,
                    Name(attributes={}, types=[], occurrences=set()),
                )
            else:
                result = value.attributes.setdefault(
                    attribute_occurrence.name,
                    Name(attributes={}, types=[], occurrences=set()),
                )
        result = result
        found_attribute = result
        found_attribute.occurrences.add(attribute_occurrence)
        self.positions[attribute_occurrence.position] = found_attribute

    def enter_module(self, module_name: tuple[str, ...]) -> None:
        self.current_scope = self.modules.setdefault(
            module_name,
            Scope(module=module_name, attributes={}, blocks={}, children=[]),
        )

    def enter_scope(
        self, name: str | None = None, is_class: bool = False
    ) -> None:
        self.current_scope = self.current_scope.add_child(name, is_class)

    def enter_function_scope(self, occurrence: NameOccurrence) -> None:
        if occurrence.position in self.positions:
            name = self.positions[occurrence.position]
        else:
            name = self.current_scope.lookup(occurrence.name)
        self.enter_scope(occurrence.name)
        if name is None:
            return
        self.name_scopes[id(name)] = self.current_scope

    def move_to_scope(self, event: NameOccurrence | Attribute) -> None:
        self.previous_scopes.append(self.current_scope)

        name = self.lookup(event)
        if name is None:
            self.enter_scope()
            return

        scope = self.name_scopes.get(id(name))
        if scope is None:
            self.enter_scope()
            return

        self.current_scope = scope

    def move_to_module(self, module: tuple[str, ...]) -> None:
        self.previous_scopes.append(self.current_scope)
        self.enter_module(module)

    def return_from_scope(self) -> None:
        self.current_scope = self.previous_scopes.pop()

    def leave_scope(self) -> None:
        if self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def delay(self, delayed: Iterator[object]) -> None:
        self.delays.append((self.current_scope, delayed))

    def add_first_argument(self, arg: NameOccurrence) -> None:
        if not (
            self.current_scope.parent and self.current_scope.parent.is_class
        ):
            return

        target = self.lookup(arg)
        value = (
            self.current_scope.parent.parent.attributes.get(
                self.current_scope.parent.name
            )
            if self.current_scope.parent.parent
            and self.current_scope.parent.name
            else None
        )
        if target and value:
            target.types.append(value)

    def add_super_call(self, occurrence: NameOccurrence) -> None:
        if not (
            self.current_scope.parent and self.current_scope.parent.is_class
        ):
            return

        target: Name | None = self.current_scope.attributes.setdefault(
            occurrence.name, Name(attributes={}, types=[], occurrences=set())
        )
        self.current_scope.attributes[occurrence.name].occurrences.add(
            occurrence
        )
        class_name = (
            self.current_scope.parent.parent.attributes.get(
                self.current_scope.parent.name
            )
            if self.current_scope.parent.parent
            and self.current_scope.parent.name
            else None
        )
        if class_name is None:
            return

        if target:
            target.types.extend(class_name.types)

    def add_base_class(
        self,
        class_occurrence: NameOccurrence | Attribute,
        base: NameOccurrence | Attribute,
    ) -> None:
        class_name = self.lookup(class_occurrence)
        base_name = self.lookup(base)
        if class_name and base_name:
            class_name.types.append(base_name)

    def bind(
        self,
        target: NameOccurrence | Attribute,
        value: NameOccurrence | Attribute,
    ) -> None:
        target_name = self.lookup(target)
        value_name = self.lookup(value)
        if target_name and value_name:
            target_name.types.extend([value_name, *value_name.types])

    def bind_import_from(
        self, occurrence: NameOccurrence, module: tuple[str, ...], level: int
    ) -> None:
        if level:
            module = (*self.current_scope.module[:-level], *module)
        if occurrence.name == "*":
            for key, value in self.modules[module].attributes.items():
                self.current_scope.attributes[key] = value
            return

        if module not in self.modules:
            return

        imported_name = self.modules[module].get_or_create(occurrence)
        self.current_scope.attributes[occurrence.name] = imported_name
        self.positions[occurrence.position] = imported_name

    def bind_import(self, occurrence: NameOccurrence | Attribute) -> None:
        module = self.modules.get(module_name(occurrence))
        name = self.lookup_or_create(occurrence)
        if module is None:
            return

        name.types.append(module)

    def lookup_or_create(self, occurrence: NameOccurrence | Attribute) -> Name:
        if isinstance(occurrence, Attribute):
            name = self.lookup_or_create(occurrence.value)
            return lookup_attribute(
                value=name, attribute=occurrence.attribute.name
            )
        else:
            result = self.current_scope.get_or_create(occurrence)
            self.positions[occurrence.position] = result
            return result

    def lookup(self, occurrence: Occurrence | Attribute) -> Name | None:
        if isinstance(occurrence, Attribute):
            name = self.lookup(occurrence.value)
            if name is None:
                return None

            return lookup_attribute(
                value=name, attribute=occurrence.attribute.name
            )
        else:
            return self.current_scope.lookup(occurrence.name)


@singledispatch
def process(event: object, collector: NameCollector) -> None:
    raise NotImplementedError(f"{event=}")


@process.register
def _(event: NameOccurrence, collector: NameCollector) -> None:
    if not collector.positions.get(event.position):
        collector.add_name(event)


@process.register
def _(event: EnterScope, collector: NameCollector) -> None:
    collector.enter_scope(event.name, event.is_class)


@process.register
def _(event: EnterFunctionScope, collector: NameCollector) -> None:
    collector.enter_function_scope(event.occurrence)


@process.register
def _(event: MoveToScope, collector: NameCollector) -> None:
    collector.move_to_scope(event=event.event)


@process.register
def _(
    event: ReturnFromScope | ReturnFromModule, collector: NameCollector
) -> None:
    collector.return_from_scope()


@process.register
def _(event: LeaveScope, collector: NameCollector) -> None:
    collector.leave_scope()


@process.register
def _(event: MoveToModule, collector: NameCollector) -> None:
    collector.move_to_module(module=event.name)


@process.register
def _(event: ReturnFromModule, collector: NameCollector) -> None:
    collector.return_from_scope()


@process.register
def _(event: Attribute, collector: NameCollector) -> None:
    collector.add_attribute(occurrence=event.attribute, value=event.value)


@process.register
def _(event: ClassAttribute, collector: NameCollector) -> None:
    collector.add_class_attribute(
        attribute=event.attribute, class_occurrence=event.class_occurrence
    )


@process.register
def _(event: BaseClass, collector: NameCollector) -> None:
    collector.add_base_class(
        class_occurrence=event.class_occurrence, base=event.base
    )


@process.register
def _(event: Bind, collector: NameCollector) -> None:
    collector.bind(target=event.target, value=event.value)


@process.register
def _(event: BindImportFrom, collector: NameCollector) -> None:
    collector.bind_import_from(
        occurrence=event.occurrence, module=event.module, level=event.level
    )


@process.register
def _(event: BindImport, collector: NameCollector) -> None:
    collector.bind_import(occurrence=event.occurrence)


@process.register
def _(event: FirstArgument, collector: NameCollector) -> None:
    collector.add_first_argument(event.arg)


@process.register
def _(event: Delay, collector: NameCollector) -> None:
    collector.delay(delayed=event.delayed)


@process.register
def _(event: SuperCall, collector: NameCollector) -> None:
    collector.add_super_call(occurrence=event.occurrence)


@process.register
def _(event: Nonlocal, collector: NameCollector) -> None:
    collector.add_nonlocal(occurrence=event)


@process.register
def _(event: Global, collector: NameCollector) -> None:
    collector.add_global(occurrence=event)


def lookup_attribute(value: Name, attribute: str) -> Name:
    for parent_type in (value, *value.types):
        if result := parent_type.attributes.get(attribute):
            break
    else:
        result = value.attributes.setdefault(
            attribute, Name(attributes={}, types=[], occurrences=set())
        )
    return result


@singledispatch
def find_names(node: ast.AST, source: types.Source) -> Iterator[object]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def name(node: ast.Name, source: types.Source) -> Iterator[object]:
    if name_occurrence := occurrence(node, source):
        yield name_occurrence


@find_names.register
def type_var(node: ast.TypeVar, source: types.Source) -> Iterator[object]:
    yield NameOccurrence(
        node.name,
        position=source.node_position(node),
        ast=node,
        is_definition=True,
    )
    if node.bound:
        yield from find_names(node.bound, source)


@find_names.register
def function_definition(  # noqa: C901
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: types.Source
) -> Iterator[object]:
    for decorator in node.decorator_list:
        yield from find_names(decorator, source)

    if not (definition := occurrence(node, source)):
        return

    yield definition
    for default in node.args.defaults:
        yield from find_names(default, source)

    yield EnterFunctionScope(definition)

    for type_parameter in node.type_params:
        yield from find_names(type_parameter, source)

    return_event = None
    if node.returns:
        for event in annotation(node.returns, source):
            if isinstance(event, NameOccurrence | Attribute):
                return_event = event
            yield event

    if return_event:
        yield Bind(definition, return_event)

    in_static_method = any(
        d.id == STATIC_METHOD
        for d in node.decorator_list
        if isinstance(d, ast.Name)
    )
    yield from arguments(node.args, source, in_static_method=in_static_method)

    def process_body() -> Iterator[object]:
        for statement in node.body:
            yield from find_names(statement, source)

    # TODO: can factor out traversal and get rid of this
    yield Delay(process_body())

    yield LeaveScope()


@find_names.register
def class_definition(
    node: ast.ClassDef, source: types.Source
) -> Iterator[object]:
    for decorator in node.decorator_list:
        yield from find_names(decorator, source)
    if not (class_occurrence := occurrence(node, source)):
        return

    yield class_occurrence
    for base in node.bases:
        last_event = None
        for event in find_names(base, source):
            if isinstance(event, NameOccurrence | Attribute):
                last_event = event
            yield event
        if last_event:
            yield BaseClass(class_occurrence, last_event)

    yield EnterScope(class_occurrence.name, is_class=True)
    for type_parameter in node.type_params:
        yield from find_names(type_parameter, source)
    yield from class_body(node, source, class_occurrence)
    yield LeaveScope()


@find_names.register
def call(node: ast.Call, source: types.Source) -> Iterator[object]:
    for arg in node.args:
        yield from find_names(arg, source)
    for keyword in node.keywords:
        yield from find_names(keyword.value, source)
    last_event = None
    for event in find_names(node.func, source):
        last_event = event
        yield event

    if not isinstance(last_event, NameOccurrence | Attribute):
        return

    if isinstance(last_event, NameOccurrence) and last_event.name == "super":
        yield SuperCall(last_event)

    yield MoveToScope(last_event)
    yield from process_keywords(node, source)
    yield ReturnFromScope()


@find_names.register
def comprehension(
    node: ast.GeneratorExp | ast.SetComp | ast.ListComp | ast.DictComp,
    source: types.Source,
) -> Iterator[object]:
    yield EnterScope()
    for generator in node.generators:
        yield from find_names(generator, source)
    for sub_node in sub_nodes(node):
        yield from find_names(sub_node, source)
    yield LeaveScope()


@find_names.register
def import_from(node: ast.ImportFrom, source: types.Source) -> Iterator[object]:
    if node.module is None:
        return

    module = tuple(node.module.split("."))
    for name in node.names:
        last_event = None
        for event in find_names(name, source):
            if isinstance(event, NameOccurrence):
                last_event = event
        if last_event:
            yield BindImportFrom(
                occurrence=last_event, module=module, level=node.level
            )


@find_names.register
def import_node(node: ast.Import, source: types.Source) -> Iterator[object]:
    for name in node.names:
        last_event = None
        for event in find_names(name, source):
            if isinstance(event, NameOccurrence | Attribute):
                last_event = event

    if last_event is None:
        return

    yield BindImport(occurrence=last_event)


@find_names.register
def arg(node: ast.arg, source: types.Source) -> Iterator[object]:
    yield NameOccurrence(
        name=node.arg,
        position=source.node_position(node),
        ast=node,
        is_definition=True,
    )


@find_names.register
def attribute(node: ast.Attribute, source: types.Source) -> Iterator[object]:
    last_event = None
    for event in find_names(node.value, source):
        if isinstance(event, Attribute | NameOccurrence):
            last_event = event
        yield event
    if last_event is None:
        return
    end = source.node_end_position(node.value)
    attribute = NameOccurrence(
        name=node.attr,
        position=end + 1 if end else source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )
    yield Attribute(value=last_event, attribute=attribute)


@find_names.register
def assignment(node: ast.Assign, source: types.Source) -> Iterator[object]:
    targets: list[list[NameOccurrence | Attribute]] = []
    yield from get_targets(node, source, targets)
    value_events: list[NameOccurrence | Attribute] = []
    yield from get_values(node, source, value_events)
    if not value_events:
        return

    for target_events in targets:
        if len(target_events) != len(value_events):
            return
        for target_event, value_event in zip(
            target_events, value_events, strict=True
        ):
            yield Bind(target_event, value_event)


@find_names.register
def slice_node(node: ast.Subscript, source: types.Source) -> Iterator[object]:
    yield from find_names(node.slice, source)
    yield from find_names(node.value, source)


@find_names.register
def alias(node: ast.alias, source: types.Source) -> Iterator[object]:
    yield NameOccurrence(
        name=node.name,
        position=source.node_position(node),
        ast=node,
        is_definition=False,
    )


@find_names.register
def match_case(node: ast.match_case, source: types.Source) -> Iterator[object]:
    yield EnterScope()
    yield from find_names(node.pattern, source)
    if node.guard:
        yield from find_names(node.guard, source)
    for statement in node.body:
        yield from find_names(statement, source)
    yield LeaveScope()


@find_names.register
def match_as(node: ast.MatchAs, source: types.Source) -> Iterator[object]:
    if node.name:
        yield NameOccurrence(
            name=node.name,
            position=source.node_position(node),
            ast=node,
            is_definition=True,
        )


@find_names.register
def nonlocal_node(node: ast.Nonlocal, source: types.Source) -> Iterator[object]:
    position = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, position)
        yield Nonlocal(
            name=name,
            position=position,
            ast=node,
        )


@find_names.register
def global_node(node: ast.Global, source: types.Source) -> Iterator[object]:
    position = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, position)
        yield Global(
            name=name,
            position=position,
            ast=node,
        )


def arguments(
    arguments: ast.arguments, source: types.Source, *, in_static_method: bool
) -> Iterator[object]:
    for i, arg in enumerate(
        (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg else []),
            *([arguments.kwarg] if arguments.kwarg else []),
        )
    ):
        name_event = type_event = None
        for event in find_names(arg, source):
            yield event
            if isinstance(event, NameOccurrence):
                name_event = event
        if arg.annotation:
            for event in annotation(arg.annotation, source):
                yield event
                if isinstance(event, NameOccurrence):
                    type_event = event
            if name_event and type_event:
                yield Bind(name_event, type_event)
        if i == 0:
            if name_event and not in_static_method:
                yield FirstArgument(name_event)


def annotation(
    annotation: ast.AST | None, source: types.Source
) -> Iterator[object]:
    if not annotation:
        return

    yield from find_names(annotation, source)


def class_body(
    node: ast.ClassDef, source: types.Source, class_occurrence: NameOccurrence
) -> Iterator[object]:
    for statement in node.body:
        attribute_occurrence = None
        if attribute_occurrence := occurrence(statement, source):
            yield ClassAttribute(
                class_occurrence=class_occurrence,
                attribute=attribute_occurrence,
            )
        yield from find_names(statement, source)


@singledispatch
def sub_nodes(node: ast.AST) -> Iterable[ast.AST]:
    return ()


@sub_nodes.register
def sub_nodes_comprehension(
    node: ast.GeneratorExp | ast.SetComp | ast.ListComp,
) -> Iterable[ast.AST]:
    return (node.elt,)


@sub_nodes.register
def sub_nodes_dictionary_comprehension(node: ast.DictComp) -> Iterable[ast.AST]:
    return (node.key, node.value)


def process_keywords(node: ast.Call, source: types.Source) -> Iterator[object]:
    for keyword in node.keywords:
        if keyword.arg is not None:
            yield NameOccurrence(
                name=keyword.arg,
                position=source.node_position(keyword),
                ast=keyword,
                is_definition=False,
            )


def get_targets(
    node: ast.Assign,
    source: types.Source,
    targets: list[list[NameOccurrence | Attribute]],
) -> Iterator[object]:
    for target in node.targets:
        match target:
            case ast.Tuple(elts=elements):
                element_targets = []
                for element in elements:
                    last_target = None
                    for event in find_names(element, source):
                        if isinstance(event, NameOccurrence | Attribute):
                            last_target = event
                        yield event
                    if last_target:
                        element_targets.append(last_target)
                targets.append(element_targets)
            case _:
                last_target = None
                for event in find_names(target, source):
                    if isinstance(event, NameOccurrence | Attribute):
                        last_target = event
                    yield event
                if last_target:
                    targets.append([last_target])


def get_values(
    node: ast.Assign,
    source: types.Source,
    value_events: list[NameOccurrence | Attribute],
) -> Iterator[object]:
    match node.value:
        case ast.Tuple(elts=elements):
            for element in elements:
                last_event = None
                for event in find_names(element, source):
                    if isinstance(event, NameOccurrence | Attribute):
                        last_event = event
                    yield event
                if last_event:
                    value_events.append(last_event)
        case _:
            for event in find_names(node.value, source):
                if isinstance(event, NameOccurrence | Attribute):
                    value_events[:] = [event]
                yield event


def import_ordered(sources: Sequence[types.Source]) -> Sequence[types.Source]:
    if len(sources) == 1:
        return sources

    result = []
    processed: set[tuple[str, ...]] = set()
    encountered: dict[tuple[str, ...], int] = {}
    queue = deque(sources)
    while queue:
        source = queue.popleft()
        imports = imported_modules(source)
        if not imports - processed:
            result.append(source)
            processed.add(module(source))
        else:
            queue.append(source)
            if encountered.get(source.module_name, -1) == len(processed):
                break
            encountered[source.module_name] = len(processed)

    return (*result, *queue)


def imported_modules(source: types.Source) -> set[tuple[str, ...]]:
    return set(find_imported_modules(source.ast, source))


def module(source: types.Source) -> tuple[str, ...]:
    return tuple(source.module_name)


def module_name(occurrence: NameOccurrence | Attribute) -> tuple[str, ...]:
    if isinstance(occurrence, NameOccurrence):
        return (occurrence.name,)

    return (*module_name(occurrence.value), occurrence.attribute.name)


@singledispatch
def find_imported_modules(
    node: ast.AST, source: types.Source
) -> Iterator[tuple[str, ...]]:
    yield from generic_visit(find_imported_modules, node, source)


@find_imported_modules.register
def _(node: ast.ImportFrom, source: types.Source) -> Iterator[tuple[str, ...]]:
    if node.module is None:
        return
    yield (*source.module_name[: -node.level], *node.module.split("."))


@find_imported_modules.register
def _(node: ast.Import, source: types.Source) -> Iterator[tuple[str, ...]]:
    for alias in node.names:
        yield tuple(alias.name.split("."))
