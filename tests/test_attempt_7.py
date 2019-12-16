"""
SAX style events again.
"""

import ast

from collections import ChainMap
from dataclasses import dataclass
from functools import singledispatch
from typing import ChainMap as CM
from typing import Dict, Iterator, List, Optional, Set, Tuple, Union

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


class Event:
    def apply(  # pylint: disable=no-self-use
        self, state: "State"  # pylint: disable=unused-argument
    ) -> None:
        ...


@dataclass
class Occurrence(Event):
    name: str
    position: Position
    node: ast.AST


class Regular(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_occurrence(self)


class Definition(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_definition(self)


@dataclass
class EnterScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_scope(self.name)


class EnterFunctionScope(EnterScope):
    def apply(self, state: "State") -> None:
        state.enter_function_scope(self.name)


class EnterModuleScope(EnterScope):
    def apply(self, state: "State") -> None:
        state.enter_modulte_scope(self.name)


class EnterClassScope(EnterScope):
    def apply(self, state: "State") -> None:
        state.enter_class_scope(self.name)


@dataclass
class EnterAttributeScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_attribute_scope(self.name)


@dataclass
class LeaveScope(Event):
    @staticmethod
    def apply(state: "State") -> None:
        state.leave_scope()


@dataclass
class Alias(Event):
    existing: Tuple[str, ...]
    new: Tuple[str, ...]

    def apply(self, state: "State") -> None:
        state.add_alias(existing=self.existing, new=self.new)


@dataclass
class SelfArgument(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.add_self(name=self.name)


class State:
    def __init__(self) -> None:
        self._namespace: List[str] = []
        self.scopes: Dict[
            Tuple[str, ...],
            CM[str, List[Occurrence]],  # pylint: disable=unsubscriptable-object
        ] = {(): ChainMap()}
        self.aliases: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self.classes: Set[Tuple[str, ...]] = set()
        self.module_scope: Optional[
            CM[str, List[Occurrence]]  # pylint: disable=unsubscriptable-object
        ] = None
        self.attribute_scopes: Set[Tuple[str, ...]] = set()

    @property
    def namespace(self) -> Tuple[str, ...]:
        return tuple(self._namespace)

    @property
    def current_scope(
        self,
    ) -> CM[str, List[Occurrence]]:  # pylint: disable=unsubscriptable-object
        assert self.namespace in self.scopes
        return self.scopes[self.namespace]

    @property
    def in_attribute_scope(self):
        return self.namespace in self.attribute_scopes

    def scope_for(
        self, namespace: Tuple[str, ...]
    ) -> Optional[CM[str, List[Occurrence]]]:  # pylint: disable=unsubscriptable-object
        return self.scopes.get(namespace)

    def lookup_existing(self, name: str) -> Optional[List[Occurrence]]:
        if name in self.current_scope:
            return self.current_scope[name]

        alias = self.get_alias(name)
        if alias:
            return alias

        prefix_alias = self.get_prefix_alias(name)
        if prefix_alias:
            return prefix_alias

        return None

    def lookup(self, name: str) -> List[Occurrence]:
        existing = self.lookup_existing(name)
        if existing:
            return existing

        return self.current_scope.setdefault(name, [])

    def get_alias(self, name: str) -> Optional[List[Occurrence]]:
        namespace = self.namespace
        while namespace in self.aliases:
            namespace = self.aliases[namespace]
            alias_scope = self.scope_for(namespace)
            if alias_scope and name in alias_scope:
                return alias_scope[name]

        return None

    def get_prefix_alias(self, name: str) -> Optional[List[Occurrence]]:
        namespace = self.namespace
        for length in range(len(namespace), 0, -1):
            prefix, suffix = namespace[:length], namespace[length:]
            if prefix in self.aliases:
                namespace = self.aliases[prefix] + suffix
                alias_scope = self.scope_for(namespace)
                if alias_scope and name in alias_scope:
                    return alias_scope[name]

        return None

    def process(self, event: Event):
        event.apply(self)

    def add_occurrence(self, occurrence: Occurrence) -> None:
        existing = self.lookup_existing(occurrence.name)
        if existing:
            existing.append(occurrence)
        elif self.in_attribute_scope:
            self.lookup(occurrence.name).append(occurrence)
        else:
            # Use before definition, which means the definition has to come later. The
            # only place that can happen is in the module scope.
            assert self.module_scope is not None
            self.module_scope.setdefault(occurrence.name, []).append(occurrence)

    def add_definition(self, occurrence: Occurrence) -> None:
        mapping = self.current_scope.maps[0]
        assert isinstance(mapping, dict)
        mapping.setdefault(occurrence.name, []).append(occurrence)

    def add_alias(self, new: Tuple[str, ...], existing: Tuple[str, ...]) -> None:
        self.aliases[self.namespace + new] = self.namespace + existing

    def add_self(self, name: str) -> None:
        if self.namespace[:-1] in self.classes:
            full_name = self.namespace + (name,)
            self.aliases[full_name] = full_name[:-2]

    def enter_modulte_scope(self, name: str) -> None:
        self.enter_scope(name)
        self.module_scope = self.current_scope

    def enter_class_scope(self, name: str) -> None:
        self.enter_scope(name)
        self.classes.add(self.namespace)

    def enter_function_scope(self, name: str) -> None:
        if self.namespace in self.classes:
            new_scope = ChainMap(*self.current_scope.maps[1:]).new_child()
        else:
            new_scope = self.current_scope.new_child()
        self._enter_scope(name, new_scope)

    def enter_scope(self, name: str) -> None:
        new_scope = self.current_scope.new_child()
        self._enter_scope(name, new_scope)

    def enter_attribute_scope(self, name: str) -> None:
        full_name = self.namespace + (name,)
        if full_name in self.aliases and self.aliases[full_name] in self.classes:
            new_scope = self.scopes[self.aliases[full_name]]
        else:
            new_scope = ChainMap()
        self._enter_scope(name, new_scope)
        self.attribute_scopes.add(self.namespace)

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
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
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
def visit_module(node: ast.Module, source: Source) -> Iterator[Event]:
    yield EnterModuleScope(source.module_name)
    yield from generic_visit(node, source)
    yield LeaveScope()


@visit.register
def visit_class(node: ast.ClassDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("class "))
    yield Definition(node.name, position, node)

    for base in node.bases:
        if isinstance(base, ast.Name):
            yield Alias(new=(node.name,), existing=(base.id,))
        yield from visit(base, source)

    yield EnterClassScope(node.name)

    for statement in node.body:
        yield from visit(statement, source)

    yield LeaveScope()


@visit.register
def visit_function(node: ast.FunctionDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("def "))
    yield Definition(node.name, position, node)
    yield EnterFunctionScope(node.name)

    for i, arg in enumerate(node.args.args):

        if i == 0 and not is_static_method(node):
            yield SelfArgument(name=arg.arg)

        position = node_position(arg, source)
        yield Definition(arg.arg, position, arg)

    yield from generic_visit(node, source)
    yield LeaveScope()


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


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
    for node_target in node.targets:
        yield from visit_definition(node_target, source)
    yield from visit(node.value, source)

    target_names = get_names(node.targets[0])
    value_names = get_names(node.value)
    for target, value in zip(target_names, value_names):
        if target and value:
            yield Alias(new=target, existing=value)


def get_names(value: ast.AST) -> List[Tuple[str, ...]]:
    if isinstance(value, ast.Tuple):
        return [names_for(v) for v in value.elts]

    return [names_for(value)]


@singledispatch
def names_for(node: ast.AST) -> Tuple[str, ...]:  # pylint: disable= unused-argument
    return ()


@names_for.register
def names_for_name(node: ast.Name) -> Tuple[str, ...]:
    return (node.id,)


@names_for.register
def names_for_attribute(node: ast.Attribute) -> Tuple[str, ...]:
    return names_for(node.value) + (node.attr,)


@names_for.register
def names_for_call(node: ast.Call) -> Tuple[str, ...]:
    return names_for(node.func)


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

    for arg in node.args:
        yield from visit(arg, source)

    names = names_from(node.func)
    for name in names[:-1]:
        yield EnterAttributeScope(name)

    yield EnterScope(names[-1])

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
def name_names(node: ast.Name) -> Tuple[str, ...]:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> Tuple[str, ...]:
    return names_from(node.value) + (node.attr,)


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


def all_events(source: Source) -> Iterator[Event]:
    for event in visit(source.get_ast(), source=source):
        yield event


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

    position = source.position(row=2, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    ]


def test_finds_attributes():
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


def test_does_not_find_method_of_unrelated_class():
    source = make_source(
        """
        class ClassThatShouldHaveMethodRenamed:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.old()
        b = UnrelatedClass()
        b.old()
        """
    )

    occurrences = all_occurrence_positions(source.position(3, 8))

    assert [
        source.position(3, 8),
        source.position(7, 13),
        source.position(20, 2),
    ] == occurrences


def test_finds_definition_from_call():
    source = make_source(
        """
        def old():
            pass

        def bar():
            old()
        """
    )

    assert [source.position(1, 4), source.position(5, 4)] == all_occurrence_positions(
        source.position(5, 4)
    )


def test_finds_attribute_assignments():
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(7, 20))

    assert [source.position(4, 13), source.position(7, 20)] == occurrences


def test_finds_dict_comprehension_variables():
    source = make_source(
        """
        old = 1
        foo = {old: None for old in range(100) if old % 3}
        old = 2
        """
    )

    position = source.position(row=2, column=7)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=21),
        source.position(row=2, column=42),
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

    position = source.position(row=3, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=4),
        source.position(row=3, column=12),
        source.position(row=3, column=33),
    ]


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        old = 100
        foo = {old for old in range(100) if old % 3}
        """
    )

    position = source.position(row=2, column=7)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        old = 100
        foo = (old for old in range(100) if old % 3)
        """
    )

    position = source.position(row=2, column=7)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
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

    position = source.position(row=2, column=7)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=7),
        source.position(row=4, column=10),
        source.position(row=5, column=6),
    ]


def test_finds_tuple_unpack():
    source = make_source(
        """
    foo, old = 1, 2
    print(old)
    """
    )

    position = source.position(row=1, column=5)

    assert all_occurrence_positions(position) == [
        source.position(1, 5),
        source.position(2, 6),
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

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=11, column=2),
    ]


def test_recognizes_multiple_assignments():
    source = make_source(
        """
    class A:
        def old(self):
            pass

    class B:
        def old(self):
            pass

    foo, bar = A(), B()
    foo.old()
    bar.old()
    """
    )

    position = source.position(row=2, column=8)

    assert all_occurrence_positions(position) == [
        source.position(2, 8),
        source.position(10, 4),
    ]


def test_finds_enclosing_scope_variable_from_comprehension():
    source = make_source(
        """
    old = 3
    res = [foo for foo in range(100) if foo % old]
    """
    )

    position = source.position(row=2, column=42)

    assert all_occurrence_positions(position) == [
        source.position(1, 0),
        source.position(2, 42),
    ]


def test_finds_static_method():
    source = make_source(
        """
        class A:

            @staticmethod
            def old(arg):
                pass

        a = A()
        a.old('foo')
        """
    )

    position = source.position(row=4, column=8)

    assert all_occurrence_positions(position) == [
        source.position(4, 8),
        source.position(8, 2),
    ]


def test_finds_argument():
    source = make_source(
        """
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """
    )

    position = source.position(row=8, column=17)

    assert all_occurrence_positions(position) == [
        source.position(3, 18),
        source.position(4, 14),
        source.position(8, 17),
    ]


def test_finds_method_but_not_function():
    source = make_source(
        """
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """
    )
    position = source.position(3, 8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(7, 13),
    ]
