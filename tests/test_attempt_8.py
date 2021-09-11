"""
SAX style events again.
"""
# pylint: disable=too-many-lines

import ast

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from functools import singledispatch
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


QualifiedName = Tuple[str, ...]


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


class Nonlocal(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_nonlocal(self)


class Definition(Occurrence):
    def apply(self, state: "State") -> None:
        state.add_definition(self)


@dataclass
class EnterScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_scope(self.name)


class EnterClassScope(EnterScope):
    def apply(self, state: "State") -> None:
        state.enter_class_scope(self.name)


@dataclass
class EnterAttributeScope(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.enter_attribute_scope(self.name)


@dataclass
class EnterSuperAttributeScope(Event):
    def apply(self, state: "State") -> None:
        state.enter_super_attribute_scope()


@dataclass
class LeaveScope(Event):
    @staticmethod
    def apply(state: "State") -> None:
        state.leave_scope()


@dataclass
class BaseClass(Event):
    sub_class: str
    base_class: str

    def apply(self, state: "State") -> None:
        state.add_base_class(sub_class=self.sub_class, base_class=self.base_class)


@dataclass
class Assignment(Event):
    existing: QualifiedName
    new: QualifiedName

    def apply(self, state: "State") -> None:
        state.add_assignment(existing=self.existing, new=self.new)


@dataclass
class SelfArgument(Event):
    name: str

    def apply(self, state: "State") -> None:
        state.add_self(name=self.name)


class State:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    def __init__(self) -> None:
        self._namespace: List[QualifiedName] = []
        self.aliases: Dict[QualifiedName, QualifiedName] = {}
        self.prefix_aliases: Dict[QualifiedName, QualifiedName] = {}
        self.base_classes: Dict[QualifiedName, List[QualifiedName]] = defaultdict(list)
        self.classes: Set[QualifiedName] = set()
        self.attribute_scopes: Set[QualifiedName] = set()
        self.qualified_names: Dict[QualifiedName, List[Occurrence]] = defaultdict(list)
        self._canonicalized: Dict[QualifiedName, List[Occurrence]] = defaultdict(list)

    @property
    def namespace(self) -> QualifiedName:
        return self._namespace[-1]

    @contextmanager
    def temp_namespace(self, namespace: QualifiedName):
        self._namespace.append(namespace)
        yield
        self._namespace.pop()

    @property
    def in_attribute_scope(self):
        return self.namespace in self.attribute_scopes

    def process(self, event: Event):
        event.apply(self)

    def add_occurrence(self, occurrence: Occurrence) -> None:
        self.qualified_names[self.namespace + (occurrence.name,)].append(occurrence)

    def add_nonlocal(self, occurrence: Occurrence) -> None:
        for index in range(1, len(self.namespace) + 1):
            temp_scope = self.namespace[:-index]
            outer = temp_scope + (occurrence.name,)
            if outer in self.qualified_names:
                self.aliases[self.namespace + (occurrence.name,)] = outer
                with self.temp_namespace(temp_scope):
                    self.add_occurrence(occurrence)
                break

    def add_definition(self, occurrence: Occurrence) -> None:
        self.qualified_names[self.namespace + (occurrence.name,)].append(occurrence)

    def add_alias(self, new: QualifiedName, existing: QualifiedName) -> None:
        self.aliases[self.namespace + new] = self.namespace + existing

    def add_assignment(self, new: QualifiedName, existing: QualifiedName) -> None:
        self.prefix_aliases[self.namespace + new] = self.namespace + existing

    def add_base_class(self, sub_class: str, base_class: str):
        self.base_classes[self.namespace + (sub_class,)].append(
            self.namespace + (base_class,)
        )

    def add_self(self, name: str) -> None:
        if self.namespace[:-1] in self.classes:
            full_name = self.namespace + (name,)
            self.prefix_aliases[full_name] = full_name[:-2]

    def enter_class_scope(self, name: str) -> None:
        self.enter_scope(name)
        self.classes.add(self.namespace)

    def enter_scope(self, name: str) -> None:
        self._enter_scope(name)

    def enter_attribute_scope(self, name: str) -> None:
        self._enter_scope(name)
        self.attribute_scopes.add(self.namespace)

    def enter_super_attribute_scope(self) -> None:
        if self.namespace[:-1] in self.classes:
            full_name = self.namespace[:-1]
        else:
            full_name = ("super",)
        self._jump_to_scope(self.base_classes[full_name][0])
        self.attribute_scopes.add(self.namespace)

    def _enter_scope(
        self,
        name: str,
    ) -> None:
        if self._namespace:
            self._namespace.append(self.namespace + (name,))
        else:
            self._namespace.append((name,))

    def _jump_to_scope(self, namespace: QualifiedName) -> None:
        self._namespace.append(namespace)

    def leave_scope(self) -> None:
        self._namespace.pop()

    def get_all_occurrences_for(
        self, qualified_name: QualifiedName
    ) -> List[Occurrence]:
        canonical_name = self.rewrite(qualified_name)
        definition = self.find_definition(canonical_name) or canonical_name
        occurrences = self.canonicalized_names[definition][:]
        for other, other_occurrences in self.canonicalized_names.items():
            if self.find_definition(other) == definition:
                occurrences.extend(other_occurrences)
        return occurrences

    @property
    def canonicalized_names(self) -> Mapping[QualifiedName, List[Occurrence]]:
        if self._canonicalized:
            return self._canonicalized

        self._canonicalized = defaultdict(list)
        for name, occurrences in self.qualified_names.items():
            rewritten = self.rewrite(name)
            self._canonicalized[rewritten].extend(occurrences)
        return self._canonicalized

    def find_definition(self, qualified_name: QualifiedName) -> Optional[QualifiedName]:
        assert len(qualified_name) > 1
        if self.is_definition(qualified_name):
            return qualified_name

        prefix, identifier = qualified_name[:-1], qualified_name[-1]

        if prefix in self.attribute_scopes:
            return None

        while prefix := prefix[:-1]:
            if prefix in self.attribute_scopes:
                return None
            if prefix in self.classes:
                continue
            if prefix + (identifier,) in self.canonicalized_names:
                return prefix + (identifier,)

        return None

    def rewrite(self, qualified_name: QualifiedName) -> QualifiedName:
        result = qualified_name
        result = self.rewrite_prefix_aliases(result)
        result = self.rewrite_aliases(result)
        return self.rewrite_base_classes(result)

    def rewrite_prefix_aliases(self, qualified_name: QualifiedName) -> QualifiedName:
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

    def rewrite_aliases(self, qualified_name: QualifiedName) -> QualifiedName:
        result = qualified_name
        while True:
            for alias, real_name in sorted(
                self.aliases.items(), key=lambda x: len(x[0]), reverse=True
            ):
                if not prefixes(alias, result) and alias != result:
                    continue
                result = substitute_prefix(qualified_name, alias, real_name)
                break
            else:
                break
        return result

    def rewrite_base_classes(self, qualified_name: QualifiedName) -> QualifiedName:
        result = qualified_name
        while result not in self.qualified_names:
            old_result = result
            prefix, identifier = result[:-1], result[-1]
            if prefix in self.base_classes:
                for base_class in self.base_classes[prefix]:
                    result = base_class + (identifier,)
                    if result in self.qualified_names:
                        break
            if old_result == result:
                break

        return result

    def is_definition(self, qualified_name: QualifiedName):
        return qualified_name in self.canonicalized_names and any(
            isinstance(o, Definition) for o in self.canonicalized_names[qualified_name]
        )


def prefixes(possible_prefix: QualifiedName, name: QualifiedName) -> bool:
    return (
        len(possible_prefix) < len(name)
        and name[: len(possible_prefix)] == possible_prefix
    )


def substitute_prefix(
    name: QualifiedName, old_prefix: QualifiedName, new_prefix: QualifiedName
) -> QualifiedName:
    return new_prefix + name[len(old_prefix) :]


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


@visit.register
def visit_name(node: ast.Name, source: Source) -> Iterator[Event]:
    yield Regular(name=node.id, position=node_position(node, source), node=node)


@visit.register
def visit_module(node: ast.Module, source: Source) -> Iterator[Event]:
    yield EnterScope(source.module_name)
    yield from generic_visit(node, source)
    yield LeaveScope()


@visit.register
def visit_class(node: ast.ClassDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("class "))
    yield Definition(node.name, position, node)

    for base in node.bases:
        if isinstance(base, ast.Name):
            yield BaseClass(sub_class=node.name, base_class=base.id)
        yield from visit(base, source)

    yield EnterClassScope(node.name)

    for statement in node.body:
        yield from visit(statement, source)

    yield LeaveScope()


@visit.register
def visit_function(node: ast.FunctionDef, source: Source) -> Iterator[Event]:
    position = node_position(node, source, column_offset=len("def "))
    yield Definition(node.name, position, node)
    yield EnterScope(node.name)

    for i, arg in enumerate(node.args.args):

        if i == 0 and not is_static_method(node):
            yield SelfArgument(name=arg.arg)

        position = node_position(arg, source)
        yield Definition(arg.arg, position, arg)

    yield from generic_visit(node, source)
    yield LeaveScope()


def visit_non_local_like(
    node: Union[ast.Global, ast.Nonlocal], source: Source
) -> Iterator[Event]:
    position = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, position)
        yield Nonlocal(name=name, position=position, node=node)


@visit.register
def visit_global(node: ast.Global, source: Source) -> Iterator[Event]:
    yield from visit_non_local_like(node, source)


@visit.register
def visit_nonlocal(node: ast.Nonlocal, source: Source) -> Iterator[Event]:
    yield from visit_non_local_like(node, source)


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_attribute(node: ast.Attribute, source: Source) -> Iterator[Event]:
    yield from visit(node.value, source)
    position = node_position(node, source)

    names = names_from(node.value)
    if isinstance(node.value, ast.Call) and names == ("super",):
        yield EnterSuperAttributeScope()
    else:
        for name in names:
            position = source.find_after(name, position)
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
            yield Assignment(new=target, existing=value)


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

    for arg in node.args:
        yield from visit(arg, source)

    names = names_from(node.func)
    yield from visit(node.func, source)
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


@singledispatch
def visit_definition(node: ast.AST, source: Source) -> Iterator[Event]:
    yield from visit(node, source)


@visit_definition.register
def visit_name_definition(node: ast.Name, source: Source) -> Iterator[Event]:
    yield Definition(name=node.id, position=node_position(node, source), node=node)


@visit_definition.register
def visit_tuple_definition(node: ast.Tuple, source: Source) -> Iterator[Event]:
    for element in node.elts:
        yield from visit_definition(element, source)


@visit_definition.register
def visit_attribute_definition(node: ast.Attribute, source: Source) -> Iterator[Event]:

    yield from visit(node.value, source)
    position = node_position(node, source)

    names = names_from(node.value)
    if isinstance(node.value, ast.Call) and names == ("super",):
        yield EnterSuperAttributeScope()
    else:
        for name in names:
            position = source.find_after(name, position)
            yield EnterAttributeScope(name)

    position = source.find_after(node.attr, position)
    yield Definition(node.attr, position, node)

    for _ in names:
        yield LeaveScope()


def get_names(value: ast.AST) -> List[QualifiedName]:
    if isinstance(value, ast.Tuple):
        return [names_for(v) for v in value.elts]

    return [names_for(value)]


@singledispatch
def names_for(node: ast.AST) -> QualifiedName:  # pylint: disable= unused-argument
    return ()


@names_for.register
def names_for_name(node: ast.Name) -> QualifiedName:
    return (node.id,)


@names_for.register
def names_for_attribute(node: ast.Attribute) -> QualifiedName:
    return names_for(node.value) + (node.attr,)


@names_for.register
def names_for_call(node: ast.Call) -> QualifiedName:
    return names_for(node.func)


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


def all_occurrences_of(position: Position) -> Sequence[Occurrence]:
    state = State()
    qualified_name = None
    for event in visit(position.source.get_ast(), source=position.source):
        state.process(event)
        if isinstance(event, Definition) and event.position == position:
            qualified_name = state.namespace + (event.name,)

    if not qualified_name:
        return []

    found = state.get_all_occurrences_for(qualified_name)

    return found


def all_occurrence_positions(position: Position) -> List[Position]:
    return sorted(set(o.position for o in all_occurrences_of(position)))


def all_events(source: Source) -> Iterator[Event]:
    for event in visit(source.get_ast(), source=source):
        yield event


def test_dogfood():
    """Test we can walk through a realistic file."""

    with open(__file__, "r", encoding="utf-8") as source_file:
        source = Source(
            lines=tuple(line[:-1] for line in source_file.readlines()),
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
        source.position(1, 4)
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
    occurrences = all_occurrence_positions(source.position(4, 13))

    assert [source.position(4, 13), source.position(7, 20)] == occurrences


def test_finds_dict_comprehension_variables():
    source = make_source(
        """
        old = 1
        foo = {old: None for old in range(100) if old % 3}
        old = 2
        """
    )

    position = source.position(row=2, column=21)

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

    position = source.position(row=3, column=12)

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

    position = source.position(row=2, column=15)

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

    position = source.position(row=2, column=15)

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

    position = source.position(row=1, column=0)

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

    position = source.position(row=1, column=0)

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
        b = a.old('foo')
        """
    )

    position = source.position(row=4, column=8)

    assert all_occurrence_positions(position) == [
        source.position(4, 8),
        source.position(8, 6),
    ]


def test_finds_method_after_call():
    source = make_source(
        """
        class A:

            def old(arg):
                pass

        b = A().old('foo')
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(6, 8),
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

    position = source.position(row=3, column=18)

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


def test_finds_global_variable_in_method_scope():
    source = make_source(
        """
    b = 12

    class Foo:

        def bar(self):
            return b
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 6, 15),
    ]


def test_treats_staticmethod_args_correctly():
    source = make_source(
        """
    class ClassName:

        def old(self):
            pass

        @staticmethod
        def foo(whatever):
            whatever.old()
    """
    )
    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [Position(source, 3, 8)]


def test_finds_global_variable():
    source = make_source(
        """
    b = 12

    def bar():
        global b
        b = 20
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 4),
    ]


def test_finds_nonlocal_variable():
    source = make_source(
        """
    b = 12

    def foo():
        b = 20
        def bar():
            nonlocal b
            b = 20
        return b
        b = 1

    print(b)
    """
    )

    position = Position(source, 4, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 4, 4),
        Position(source, 6, 17),
        Position(source, 7, 8),
        Position(source, 8, 11),
        Position(source, 9, 4),
    ]


def test_finds_multiple_definitions():
    source = make_source(
        """
    a = 12
    if a > 10:
        b = a + 100
    else:
        b = 3 - a
    print(b)
    """
    )
    position = Position(source, 3, 4)
    assert all_occurrence_positions(position) == [
        Position(source, 3, 4),
        Position(source, 5, 4),
        Position(source, 6, 6),
    ]


def test_finds_method_in_super_call():
    source = make_source(
        """
    class Foo:

        def bar(self):
            pass


    class Bar(Foo):

        def bar(self):
            super().bar()
    """
    )

    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [
        Position(source, 3, 8),
        Position(source, 10, 16),
    ]


def test_rename_method_of_subclass():
    source = make_source(
        """
    class Foo:

        def bar(self):
            pass


    class Bar(Foo):

        def bar(self):
            super().bar()
    """
    )

    position = Position(source, 9, 8)

    assert all_occurrence_positions(position) == [Position(source, 9, 8)]


# TODO: imports
