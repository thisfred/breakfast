"""
SAX style events again.
"""

import ast

from collections import ChainMap
from dataclasses import dataclass
from functools import singledispatch
from typing import ChainMap as CM
from typing import Dict, Iterator, List, Optional, Tuple, Union

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


class Event:
    def apply(  # pylint: disable=no-self-use
        self, state: "State"  # pylint: disable=unused-argument
    ) -> None:
        ...


@dataclass(frozen=True)
class Occurrence(Event):
    name: str
    position: Position
    node: ast.AST


class Regular(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_to_scope(self)


class Definition(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_definition_to_scope(self)


@dataclass(frozen=True)
class EnterScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_scope(self.name)


@dataclass(frozen=True)
class EnterAttributeScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_isolated_scope(self.name)


@dataclass(frozen=True)
class LeaveScope(Event):
    @staticmethod
    def apply(state: "State") -> None:
        state.leave_scope()


@dataclass(frozen=True)
class Alias(Event):
    existing: str
    new: str

    def apply(self, state: "State") -> None:
        state.add_alias(existing=self.existing, new=self.new)


class State:
    def __init__(self) -> None:
        self._namespace: List[str] = []
        self.scopes: Dict[
            Tuple[str, ...],
            CM[str, List[Occurrence]],  # pylint: disable=unsubscriptable-object
        ] = {(): ChainMap()}
        self.aliases: Dict[Tuple[str, ...], Tuple[str, ...]] = {}

    @property
    def namespace(self) -> Tuple[str, ...]:
        return tuple(self._namespace)

    @property
    def current_scope(
        self
    ) -> CM[str, List[Occurrence]]:  # pylint: disable=unsubscriptable-object
        assert self.namespace in self.scopes
        return self.scopes[self.namespace]

    def scope_for(
        self, namespace: Tuple[str, ...]
    ) -> Optional[CM[str, List[Occurrence]]]:  # pylint: disable=unsubscriptable-object
        return self.scopes.get(namespace)

    def lookup(self, name: str) -> List[Occurrence]:
        if name not in self.current_scope:
            namespace = self.namespace
            while namespace in self.aliases:
                namespace = self.aliases[namespace]
                alias_scope = self.scope_for(namespace)
                if alias_scope and name in alias_scope:
                    return alias_scope[name]

        return self.current_scope.setdefault(name, [])

    def process(self, event: Event):
        event.apply(self)

    def add_to_scope(self, occurrence: Occurrence) -> None:
        self.lookup(occurrence.name).append(occurrence)

    def add_definition_to_scope(self, occurrence: Occurrence) -> None:
        mapping = self.current_scope.maps[0]
        assert isinstance(mapping, dict)
        mapping.setdefault(occurrence.name, []).append(occurrence)

    def add_alias(self, new: str, existing: str) -> None:
        self.aliases[self.namespace + (new,)] = self.namespace + (existing,)

    def enter_scope(self, name: str) -> None:
        new_scope = self.current_scope.new_child()
        self._enter_scope(name, new_scope)

    def enter_isolated_scope(self, name: str) -> None:
        new_scope: CM[  # pylint: disable=unsubscriptable-object
            str, List[Occurrence]
        ] = ChainMap()
        self._enter_scope(name, new_scope)

    def _enter_scope(
        self,
        name: str,
        new_scope: CM[str, List[Occurrence]],  # pylint: disable=unsubscriptable-object
    ):
        self._namespace.append(name)
        self.scopes.setdefault(self.namespace, new_scope)

    def leave_scope(self) -> None:
        self._namespace.pop()


def node_position(
    node: ast.AST, source: Source, row_offset=0, column_offset=0
) -> Position:
    return Position(
        source=source,
        row=(node.lineno - 1) + row_offset,
        column=node.col_offset + column_offset,
    )


@singledispatch
def visit(node: ast.AST, source: Source) -> Iterator[Event]:
    """Visit a node.

    Copied and reworked from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    yield from generic_visit(node, source)


@singledispatch
def visit_definition(node: ast.AST, source: Source) -> Iterator[Event]:
    yield from visit(node, source)


@visit.register
def visit_name(node: ast.Name, source: Source) -> Iterator[Event]:
    yield Regular(name=node.id, position=node_position(node, source), node=node)


@visit_definition.register
def visit_name_definition(node: ast.Name, source: Source) -> Iterator[Event]:
    yield Definition(name=node.id, position=node_position(node, source), node=node)


@visit.register
def visit_class(node: ast.ClassDef, source: Source) -> Iterator[Event]:
    row_offset, column_offset = len(node.decorator_list), len("class ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    yield Definition(node.name, position, node)

    for base in node.bases:
        if isinstance(base, ast.Name):
            yield Alias(new=node.name, existing=base.id)
        yield from visit(base, source)

    yield EnterScope(node.name)

    for statement in node.body:
        yield from visit(statement, source)

    yield LeaveScope()


@visit.register
def visit_function(node: ast.FunctionDef, source: Source) -> Iterator[Event]:
    row_offset, column_offset = len(node.decorator_list), len("def ")
    position = node_position(
        node, source, row_offset=row_offset, column_offset=column_offset
    )
    yield Definition(node.name, position, node)
    yield EnterScope(node.name)

    for arg in node.args.args:
        position = node_position(arg, source)
        yield Regular(arg.arg, position, arg)

    yield from generic_visit(node, source)
    yield LeaveScope()


@visit.register
def visit_attribute(node: ast.Attribute, source: Source) -> Iterator[Event]:
    yield from visit(node.value, source)
    position = node_position(node, source)

    names = names_from(node.value)
    for name in names:
        position = source.find_after(node.attr, position)
        yield EnterAttributeScope(name)

    position = source.find_after(node.attr, position)
    yield Regular(node.attr, position, node)

    for _ in names:
        yield LeaveScope()


@visit.register
def visit_assign(node: ast.Assign, source: Source) -> Iterator[Event]:
    yield from generic_visit(node, source)

    target_names = get_names(node.targets[0])
    value_names = get_names(node.value)

    for target, value in zip(target_names, value_names):
        if target and value:
            yield Alias(new=target, existing=value)


def get_names(node: ast.AST):
    return [name_for(node)]


@singledispatch
def name_for(node: ast.AST) -> Optional[str]:  # pylint: disable= unused-argument
    return None


@name_for.register
def name_for_name(node: ast.Name) -> Optional[str]:
    return node.id


@name_for.register
def name_for_attribute(node: ast.Attribute) -> Optional[str]:
    return node.attr


@name_for.register
def name_for_call(node: ast.Call) -> Optional[str]:
    return name_for(node.func)


@visit.register
def visit_import(node: ast.Import, source: Source) -> Iterator[Event]:
    start = node_position(node, source)
    for alias in node.names:
        name = alias.name
        position = source.find_after(name, start)
        yield Regular(name, position, alias)


@visit.register
def visit_call(node: ast.Call, source: Source) -> Iterator[Event]:
    call_position = node_position(node, source)
    yield from visit(node.func, source)

    names = names_from(node.func)
    for arg in node.args:
        yield from visit(arg, source)

    for name in names:
        yield EnterAttributeScope(name)

    for keyword in node.keywords:
        if not keyword.arg:
            continue

        position = source.find_after(keyword.arg, call_position)
        yield Regular(keyword.arg, position, node)

    for _ in names:
        yield LeaveScope()


@visit.register
def visit_dict_comp(node: ast.DictComp, source: Source) -> Iterator[Event]:
    yield from visit_comp(node, source, node.key, node.value)


@visit.register
def visit_list_comp(node: ast.ListComp, source: Source) -> Iterator[Event]:
    yield from visit_comp(node, source, node.elt)


@visit.register
def visit_set_comp(node: ast.SetComp, source: Source) -> Iterator[Event]:
    yield from visit_comp(node, source, node.elt)


@visit.register
def visit_generator_exp(node: ast.GeneratorExp, source: Source) -> Iterator[Event]:
    yield from visit_comp(node, source, node.elt)


def visit_comp(
    node: Union[ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp],
    source: Source,
    *sub_nodes,
) -> Iterator[Event]:
    name = f"{type(node)}-{id(node)}"
    yield EnterScope(name)

    for generator in node.generators:
        yield from visit_definition(generator.target, source)
        yield from visit(generator.iter, source)
        for if_node in generator.ifs:
            yield from visit(if_node, source)

    for sub_node in sub_nodes:
        yield from visit(sub_node, source)

    yield LeaveScope()


def generic_visit(node, source: Source) -> Iterator[Event]:
    """Called if no explicit visitor function exists for a node.

    Copied and reworked from NodeVisitor in:

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
def names_from(node: ast.AST) -> Tuple[str, ...]:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_nemes(node: ast.Name) -> Tuple[str, ...]:
    return (node.id,)


@names_from.register
def attribute_nemes(node: ast.Attribute) -> Tuple[str, ...]:
    return names_from(node.value) + (node.attr,)


def get_occurrences(source: Source) -> List[Occurrence]:
    initial_node = source.get_ast()
    return [
        event
        for event in visit(initial_node, source=source)
        if isinstance(event, Occurrence)
    ]


def get_scopes(source: Source) -> List[Union[EnterScope, LeaveScope]]:
    initial_node = source.get_ast()
    return [
        event
        for event in visit(initial_node, source=source)
        if isinstance(event, (EnterScope, LeaveScope))
    ]


def all_occurrences_of(position: Position) -> List[Occurrence]:
    found: List[Occurrence] = []
    state = State()
    for event in visit(position.source.get_ast(), source=position.source):
        state.process(event)
        if isinstance(event, Occurrence) and event.position == position:
            found = state.lookup(event.name) or []

    return found


def all_occurrence_positions(position: Position) -> List[Position]:
    return sorted(o.position for o in all_occurrences_of(position))


def test_dogfood():
    """Test we can walk through a realistic file."""

    with open(__file__, "r") as source_file:
        source = Source(
            lines=tuple(l[:-1] for l in source_file.readlines()),
            module_name="test_attempt_7",
            file_name=__file__,
        )

    state = State()
    for event in visit(source.get_ast(), source=source):
        state.process(event)

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

    position = Position(source=source, row=2, column=4)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=2, column=4),
        Position(source=source, row=4, column=13),
        Position(source=source, row=5, column=8),
    ]


def test_finds_attributes():
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = Position(source=source, row=3, column=0)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=3, column=0)
    ]


def test_finds_parameters():
    source = make_source(
        """
        def fun(arg, arg2):
            return arg + arg2
        fun(arg=1, arg2=2)
        """
    )

    position = Position(source=source, row=1, column=8)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=1, column=8),
        Position(source=source, row=2, column=11),
        Position(source=source, row=3, column=4),
    ]


def test_finds_method_name():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        unbound = A.old
        """
    )

    position = Position(source=source, row=3, column=8)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=3, column=8),
        Position(source=source, row=6, column=12),
    ]


def test_finds_dict_comprehension_variables():
    source = make_source(
        """
        old = 1
        foo = {old: None for old in range(100) if old % 3}
        old = 2
        """
    )

    position = Position(source=source, row=2, column=7)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=2, column=7),
        Position(source=source, row=2, column=21),
        Position(source=source, row=2, column=42),
    ]


def test_finds_list_comprehension_variables():
    source = make_source(
        """
        old = 100
        foo = [
            old for old in range(100) if old % 3]
        old = 200
        """
    )

    position = Position(source=source, row=3, column=4)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=3, column=4),
        Position(source=source, row=3, column=12),
        Position(source=source, row=3, column=33),
    ]


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        old = 100
        foo = {old for old in range(100) if old % 3}
        """
    )

    position = Position(source=source, row=2, column=7)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=2, column=7),
        Position(source=source, row=2, column=15),
        Position(source=source, row=2, column=36),
    ]


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        old = 100
        foo = (old for old in range(100) if old % 3)
        """
    )

    position = Position(source=source, row=2, column=7)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=2, column=7),
        Position(source=source, row=2, column=15),
        Position(source=source, row=2, column=36),
    ]


def test_finds_loop_variables():
    source = make_source(
        """
        old = None
        for i, old in enumerate(['foo']):
            print(i)
            print(old)
        print(old)
        """
    )

    position = Position(source=source, row=2, column=7)

    assert all_occurrence_positions(position) == [
        Position(source=source, row=1, column=0),
        Position(source=source, row=2, column=7),
        Position(source=source, row=4, column=10),
        Position(source=source, row=5, column=6),
    ]


def test_finds_superclasses():
    source = make_source(
        """
        class A:

            def old(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.old()
        """
    )

    position = Position(source=source, row=3, column=8)
    assert all_occurrence_positions(position) == [
        Position(source=source, row=3, column=8),
        Position(source=source, row=11, column=2),
    ]
