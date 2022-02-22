import ast

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from functools import singledispatch
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple, Type

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


QualifiedName = Tuple[str, ...]


@dataclass
class Event:
    ...


@dataclass
class SelfArgument(Event):
    name: str


@dataclass
class EnterScope(Event):
    name: str


@dataclass
class LeaveScope(Event):
    ...


@dataclass
class EnterClassScope(EnterScope):
    ...


@dataclass
class EnterNamespace(Event):
    name: str


@dataclass
class LeaveNamespace(Event):
    ...


@dataclass
class EnterAttributeScope(Event):
    name: str


@dataclass
class EnterSuperNamespace(Event):
    ...


@dataclass
class BaseClass(Event):
    sub_class: str
    base_class: str


@dataclass
class Occurrence(Event):
    node: ast.AST
    name: str
    position: Position


@dataclass
class Definition(Occurrence):
    ...


@dataclass
class Assignment(Event):
    target: str
    source: str


class ScopeKind(Enum):
    SCOPE = auto()
    NAMESPACE = auto()


class Scope:
    def __init__(
        self: "Scope",
        path: QualifiedName = tuple(),
        parent: Optional["Scope"] = None,
        kind: ScopeKind = ScopeKind.SCOPE,
    ):
        self.definitions: Set[str] = set()
        self.path: QualifiedName = path
        self.parent = parent
        self.kind = kind

    def add_definition(self, name: str) -> None:
        self.definitions.add(name)

    def lookup(self, name: str) -> Optional[QualifiedName]:
        if name in self.definitions:
            return self.path + (name,)

        if self.parent and self.parent.kind == self.kind:
            return self.parent.lookup(name)

        return None

    def enter_scope(self, name: str) -> "Scope":
        new_scope = Scope(path=self.path + (name,), parent=self)
        return new_scope

    def enter_namespace(self, name: str) -> "Scope":
        new_scope = Scope(
            path=self.path + (name,), parent=self, kind=ScopeKind.NAMESPACE
        )
        return new_scope

    def jump_to_namespace(self, qualified_name: QualifiedName) -> "Scope":
        new_scope = Scope(path=qualified_name, parent=self, kind=ScopeKind.NAMESPACE)
        return new_scope

    def leave_scope(self) -> Optional["Scope"]:
        return self.parent


class State:
    def __init__(self: "State") -> None:
        self.scope: Scope = Scope()
        self.scopes = {self.scope.path: self.scope}
        self.attribute_scopes: Set[QualifiedName] = set()
        self.classes: Set[QualifiedName] = set()
        self.base_classes: Dict[QualifiedName, List[QualifiedName]] = defaultdict(list)
        self.prefix_aliases: Dict[QualifiedName, QualifiedName] = {}

    def rewrite(self, qualified_name: QualifiedName) -> QualifiedName:
        result = qualified_name
        while True:
            for alias, real_name in sorted(
                self.prefix_aliases.items(),
                key=lambda x: len(x[0]),
                reverse=True,
            ):
                if not prefixes(alias, result):
                    continue
                result = substitute_prefix(qualified_name, alias, real_name)
                break
            else:
                break
        return result

    def name_for_definition(self, name: str) -> Optional[QualifiedName]:
        self.scope.add_definition(name)
        return self.scope.lookup(name)

    def enter_scope(self, name: str) -> None:
        qualified_name = self.scope.path + (name,)
        if qualified_name in self.scopes:
            self.scope = self.scopes[qualified_name]
        else:
            self.scope = self.scope.enter_scope(name)
            self.scopes[self.scope.path] = self.scope

    def enter_namespace(self, name: str) -> None:
        qualified_name = self.scope.path + (name,)
        if qualified_name in self.scopes:
            self.scope = self.scopes[qualified_name]
        else:
            self.scope = self.scope.enter_namespace(name)
            self.scopes[self.scope.path] = self.scope

    def leave_scope(self) -> None:
        new_scope = self.scope.leave_scope()
        if new_scope:
            self.scope = new_scope

    def enter_attribute_namespace(self, name: str) -> None:
        self.enter_namespace(name)

    def enter_super_attribute_namespace(self) -> None:
        if self.scope.path[:-1] in self.classes:
            full_name = self.scope.path[:-1]
        else:
            full_name = ("super",)

        self.scope.jump_to_namespace(self.base_classes[full_name][0])

    def lookup(self, name: str) -> Optional[QualifiedName]:
        return self.scope.lookup(name)

    def mark_class(self) -> None:
        self.classes.add(self.scope.path)

    def add_base_class(self, sub_class: str, base_class: str):
        self.base_classes[self.scope.path + (sub_class,)].append(
            self.scope.path + (base_class,)
        )

    def add_self(self, name: str) -> None:
        if self.scope.path[:-2] in self.classes:
            full_name = self.scope.path + (name,)
            self.prefix_aliases[full_name] = full_name[:-3]

    def add_assignment(self, target: str, source: str) -> None:
        self.prefix_aliases[self.scope.path + (target,)] = self.scope.path + (source,)


def prefixes(possible_prefix: QualifiedName, name: QualifiedName) -> bool:
    return (
        len(possible_prefix) < len(name)
        and name[: len(possible_prefix)] == possible_prefix
    )


def substitute_prefix(
    name: QualifiedName, old_prefix: QualifiedName, new_prefix: QualifiedName
) -> QualifiedName:
    return new_prefix + name[len(old_prefix) :]


@singledispatch
def visit(node: ast.AST, source: Source) -> Iterator[Event]:
    """Visit a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    yield from generic_visit(node, source)


@visit.register
def visit_module(node: ast.Module, source: Source) -> Iterator[Event]:
    yield EnterScope(name=source.module_name)
    yield from generic_visit(node, source)
    yield LeaveScope()


@visit.register
def visit_name(node: ast.Name, source: Source) -> Iterator[Event]:
    if isinstance(node.ctx, ast.Store):
        cls: Type[Occurrence] = Definition
    else:
        cls = Occurrence

    yield cls(node=node, name=node.id, position=node_position(node, source))


@visit.register
def visit_function(node: ast.FunctionDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("def "))
    yield Definition(node=node, name=node.name, position=position)
    yield EnterScope(name=node.name)
    yield EnterScope(name="<function>")

    for i, arg in enumerate(node.args.args):

        if i == 0 and not is_static_method(node):
            yield SelfArgument(name=arg.arg)

        position = node_position(arg, source)
        yield Definition(node=arg, position=position, name=arg.arg)

    yield EnterScope(name="<local>")
    yield from generic_visit(node, source)
    yield LeaveScope()
    yield LeaveScope()
    yield LeaveScope()


@visit.register
def visit_call(node: ast.Call, source: Source) -> Iterator[Event]:
    call_position = node_position(node, source)

    for arg in node.args:
        yield from visit(arg, source)

    names = names_from(node.func)
    yield from visit(node.func, source)
    for name in names[:-1]:
        yield EnterAttributeScope(name)

    yield EnterScope(names[-1])
    yield EnterScope(name="<function>")

    for keyword in node.keywords:
        if not keyword.arg:
            continue

        position = source.find_after(keyword.arg, call_position)
        yield Definition(node=node, name=keyword.arg, position=position)

    yield LeaveScope()
    yield LeaveScope()
    for _ in names[:-1]:
        yield LeaveNamespace()


@visit.register
def visit_class(node: ast.ClassDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("class "))
    yield Definition(node=node, name=node.name, position=position)

    for base in node.bases:
        if isinstance(base, ast.Name):
            yield BaseClass(sub_class=node.name, base_class=base.id)
        yield from visit(base, source)

    yield EnterClassScope(node.name)

    for statement in node.body:
        yield from visit(statement, source)

    yield LeaveScope()


@visit.register
def visit_attribute(node: ast.Attribute, source: Source) -> Iterator[Event]:
    yield from visit(node.value, source)
    position = node_position(node, source)

    names = names_from(node.value)
    if names == ("super",) and isinstance(node.value, ast.Call):
        yield EnterSuperNamespace()
    else:
        for name in names:
            position = source.find_after(name, position)
            yield EnterAttributeScope(name)

    position = source.find_after(node.attr, position)
    yield Occurrence(node=node, name=node.attr, position=position)

    for _ in names:
        yield LeaveScope()


@visit.register
def visit_assign(node: ast.Assign, source: Source) -> Iterator[Event]:
    target_names = names_from(node.targets[0])
    value_names = names_from(node.value)
    for target, value in zip(target_names, value_names):
        if target and value:
            yield Assignment(target=target, source=value)

    for node_target in node.targets:
        yield from visit(node_target, source)
    yield from visit(node.value, source)


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


def node_position(
    node: ast.AST, source: Source, row_offset=0, column_offset=0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


@singledispatch
def names_from(node: ast.AST) -> QualifiedName:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> QualifiedName:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> QualifiedName:
    return names_from(node.value) + (node.attr,)


@names_from.register
def call_names(node: ast.Call) -> QualifiedName:
    names = names_from(node.func)
    return names


def generic_visit(node: ast.AST, source: Source) -> Iterator[Event]:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, source)
        elif isinstance(value, ast.AST):
            yield from visit(value, source)


@singledispatch
def event_to_names(
    _event: Event, _state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def definition(
    event: Definition, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    qualified_name = state.name_for_definition(event.name)
    if qualified_name:
        yield (state.rewrite(qualified_name), event.position)


@event_to_names.register
def occurrence(
    event: Occurrence, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    qualified_name = state.lookup(event.name)
    if qualified_name:
        yield (state.rewrite(qualified_name), event.position)


@event_to_names.register
def enter_scope(
    event: EnterScope, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.enter_scope(event.name)
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def leave_scope(
    _event: LeaveScope, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.leave_scope()
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def leave_namespace(
    _event: LeaveNamespace, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.leave_scope()
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def enter_attribute_namespace(
    event: EnterAttributeScope, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.enter_attribute_namespace(event.name)
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def enter_super_attribute_namespace(
    _event: EnterSuperNamespace, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.enter_super_attribute_namespace()
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def enter_class_scope(
    event: EnterClassScope, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.enter_scope(event.name)
    state.mark_class()
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def base_class(
    event: BaseClass, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.add_base_class(event.sub_class, event.base_class)
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def self_argument(
    event: SelfArgument, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.add_self(name=event.name)
    return
    yield  # pylint: disable=unreachable


@event_to_names.register
def assignment(
    event: Assignment, state: State
) -> Iterator[Tuple[QualifiedName, Position]]:
    state.add_assignment(target=event.target, source=event.source)
    return
    yield  # pylint: disable=unreachable


def events_to_names(
    events: Iterable[Event],
) -> Iterator[Tuple[QualifiedName, Position]]:
    state = State()
    for event in events:
        yield from event_to_names(event, state)


def filter_by_position(
    names: Iterable[Tuple[QualifiedName, Position]], position: Position
) -> Iterator[Position]:
    temp: Dict[QualifiedName, List[Position]] = defaultdict(list)
    found_name = None
    for occurrence_name, occurrence_position in names:
        print(occurrence_name, occurrence_position)
        if found_name:
            if occurrence_name == found_name:
                yield occurrence_position
        elif occurrence_position == position:
            found_name = occurrence_name
            for previous_position in temp[occurrence_name]:
                yield previous_position
            yield occurrence_position
        else:
            temp[occurrence_name].append(occurrence_position)


def all_occurrence_positions(
    position: Position,
) -> Iterable[Position]:
    source = position.source
    events = visit(source.get_ast(), source=source)
    names = events_to_names(events)
    positions = filter_by_position(names, position)
    return sorted(positions)


def test_dogfood():
    """Test we can walk through a realistic file."""

    with open(__file__, "r", encoding="utf-8") as source_file:
        source = Source(
            lines=tuple(line[:-1] for line in source_file.readlines()),
            module_name="test_attempt_7",
            file_name=__file__,
        )

    events = visit(source.get_ast(), source=source)
    names = events_to_names(events)

    for _ in names:
        pass

    # the test is that we get here without errors.
    assert True


def test_distinguishes_local_variables_from_global():
    source = make_source(
        """
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """
    )

    position = source.position(row=2, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    ]


def test_finds_non_local_variable():
    source = make_source(
        """
    old = 12

    def fun():
        result = old + 1
        return result

    old = 20
    """
    )

    position = source.position(1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    ]


def test_does_not_rename_random_attributes():
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = source.position(row=3, column=0)

    assert all_occurrence_positions(position) == [source.position(row=3, column=0)]


def test_finds_parameter():
    source = make_source(
        """
        def fun(old=1):
            print(old)

        old = 8
        fun(old=old)
        """
    )

    assert [
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    ] == all_occurrence_positions(source.position(1, 8))


def test_finds_function():
    source = make_source(
        """
        def fun_old():
            return 'result'
        result = fun_old()
        """
    )

    assert [source.position(1, 4), source.position(3, 9)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_class():
    source = make_source(
        """
        class OldClass:
            pass

        instance = OldClass()
        """
    )

    assert [source.position(1, 6), source.position(4, 11)] == all_occurrence_positions(
        source.position(1, 6)
    )


def test_finds_method_name():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        unbound = A.old
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=6, column=12),
    ]


def test_finds_passed_argument():
    source = make_source(
        """
        old = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, old)
        """
    )

    assert [source.position(1, 0), source.position(4, 7)] == all_occurrence_positions(
        source.position(1, 0)
    )


def test_finds_parameter_with_unusual_indentation():
    source = make_source(
        """
        def fun(arg, arg2):
            return arg + arg2
        fun(
            arg=\\
                1,
            arg2=2)
        """
    )

    assert [
        source.position(1, 8),
        source.position(2, 11),
        source.position(4, 4),
    ] == all_occurrence_positions(source.position(1, 8))
