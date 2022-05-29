# pylint: disable=too-many-lines
import ast

from collections import defaultdict
from contextlib import ExitStack, contextmanager
from functools import singledispatch
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


QualifiedName = Tuple[str, ...]


class TreeTraversalError(RuntimeError):
    pass


class AliasError(RuntimeError):
    pass


class Node:
    def __init__(self, parent: Optional["Node"], path: QualifiedName = ()):
        self.parent = parent
        self.children: Dict[str, "Node"] = defaultdict(lambda: Node(parent=self))
        self.occurrences: Set[Position] = set()
        self.is_class = False
        self.path = path

    def add_occurrence(self, occurrence: Position):
        self.occurrences.add(occurrence)

    def __getitem__(self, name: str) -> "Node":
        node = self.children[name]

        if not node.path:
            node.path = self.path + (name,)

        return node

    def __contains__(self, name: str) -> bool:
        return name in self.children

    def alias_namespace(self, other: "Node") -> None:
        if "." in other and "." not in self:
            self.children["."] = other["."]
        elif "." in self and "." not in other:
            other.children["."] = self.children["."]
        else:
            self.children["."] = other["."]

    def alias(self, other: "Node") -> None:
        for name, value in other.children.items():
            if name not in self.children:
                self.children[name] = value

        for name, value in self.children.items():
            if name not in other.children:
                other.children[name] = value

        other.children = self.children
        self.occurrences |= other.occurrences
        other.occurrences = self.occurrences

    def flatten(
        self,
        prefix: QualifiedName = tuple(),
        seen: Optional[Set[Position]] = None,
    ) -> Dict[QualifiedName, List[Tuple[int, int]]]:
        if not seen:
            seen = set()

        result = {}
        next_values = []
        for key, value in self.children.items():
            new_prefix = prefix + (key,)
            if value.occurrences:
                occurrence = next(iter(value.occurrences))
                if occurrence in seen:
                    continue

                positions = [(o.row, o.column) for o in value.occurrences]
                result[new_prefix] = positions
                seen |= value.occurrences
            next_values.append((new_prefix, value))

        for new_prefix, value in next_values:
            result.update(value.flatten(prefix=new_prefix, seen=seen))

        return result


class State:
    def __init__(self, position: Position):
        self.position = position
        self.root = Node(parent=None)
        self.current_node = self.root
        self.current_path: QualifiedName = tuple()
        self.lookup_scopes = [self.root]
        self.found: Optional[Node] = None

    @contextmanager
    def scope(self, name: str, lookup_scope: bool = False, is_class: bool = False):
        previous_node = self.current_node
        self.current_node = self.current_node[name]
        self.current_node.is_class = is_class
        if lookup_scope:
            self.lookup_scopes.append(self.current_node)
        self.current_path += (name,)
        yield
        self.current_node = previous_node
        self.current_path = self.current_path[:-1]
        if lookup_scope:
            self.lookup_scopes.pop()

    @contextmanager
    def jump_to_scope(self, path: QualifiedName):
        previous_node = self.current_node
        previous_path = self.current_path
        self.current_node = self.root
        self.current_path = ()
        for name in path:
            self.current_node = self.current_node[name]
            self.current_path += (name,)
        yield
        self.current_node = previous_node
        self.current_path = previous_path

    def check_found(self, position: Position):
        if position == self.position:
            self.found = self.current_node

    def add_occurrence(self, position: Position) -> None:
        self.current_node.add_occurrence(position)
        self.check_found(position)

    def follow_path(self, path: QualifiedName) -> Node:
        other_node = self.current_node
        other_path = self.current_path

        for name in path:
            if name == "/":
                other_path = ()
                other_node = self.root
            elif name == "~":
                other_path = self.current_path[:2]
                for _ in range(len(self.current_path) - 2):
                    if other_node.parent:
                        other_node = other_node.parent
                    else:
                        raise TreeTraversalError()

            elif name == "..":
                other_path = other_path[:-1]
                if other_node.parent:
                    other_node = other_node.parent
                else:
                    raise TreeTraversalError()

            else:
                other_path += (name,)
                other_node = other_node[name]
        return other_node

    def alias(self, path: QualifiedName) -> None:
        other_node = self.follow_path(path)
        self.current_node.alias(other_node)


def node_position(
    node: ast.AST, source: Source, row_offset=0, column_offset=0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


def generic_visit(node: ast.AST, source: Source, state: State) -> None:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    visit(item, source, state)
        elif isinstance(value, ast.AST):
            visit(value, source, state)


@singledispatch
def visit(node: ast.AST, source: Source, state: State) -> None:
    generic_visit(node, source, state)


@visit.register
def visit_module(node: ast.Module, source: Source, state: State) -> None:
    with ExitStack() as stack:
        for name in source.module_name.split("."):
            stack.enter_context(state.scope(name))
            stack.enter_context(state.scope(".", lookup_scope=True))
        generic_visit(node, source, state)


@visit.register
def visit_name(node: ast.Name, source: Source, state: State) -> None:
    position = node_position(node, source)
    if isinstance(node.ctx, ast.Store):
        with state.scope(node.id):
            state.add_occurrence(position)
    else:
        if node.id not in state.current_node:
            for scope in state.lookup_scopes[::-1]:
                if node.id in scope or scope is state.root:
                    position = node_position(node, source)
                    with state.scope(node.id):
                        scope[node.id].add_occurrence(position)
                    state.check_found(position)
                    break
        else:
            with state.scope(node.id):
                state.add_occurrence(node_position(node, source))


def get_names(value: ast.AST) -> List[QualifiedName]:
    match value:
        case ast.Tuple(elts=elements):
            return [
                i
                for e in elements
                for i in get_names(e)  # pylint: disable=not-an-iterable
            ]
        case other:
            return [qualified_name_for(other)]


def qualified_name_for(node: ast.AST) -> QualifiedName:
    match node:
        case ast.Name(id=name):
            return (name,)
        case ast.Attribute(value=value, attr=attr):
            return qualified_name_for(value) + (attr,)
        case ast.Name(id=name):
            return (name,)
        case ast.Call(func=function):
            return qualified_name_for(function) + ("()",)
        case _:
            return ()


@visit.register
def visit_import(node: ast.Import, source: Source, state: State) -> None:
    start = node_position(node, source)

    current_path = ("/",) + state.current_path
    with state.jump_to_scope(()):
        _handle_imports(node, source, state, start, current_path)


@visit.register
def visit_import_from(node: ast.ImportFrom, source: Source, state: State) -> None:
    start = node_position(node, source, column_offset=len("from "))
    assert isinstance(node.module, str)

    current_path = ("/",) + state.current_path
    node_module_path: QualifiedName = tuple()
    for name in node.module.split("."):
        node_module_path += (name, ".")
    node_module_path = node_module_path[:-1]

    with state.jump_to_scope(node_module_path):
        state.add_occurrence(start)
        with state.scope("."):
            _handle_imports(node, source, state, start, current_path)


def _handle_imports(
    node: Union[ast.Import, ast.ImportFrom],
    source: Source,
    state: State,
    start: Position,
    current_path: QualifiedName,
):
    for alias in node.names:
        name = alias.name
        position = source.find_after(name, start)
        with state.scope(name):
            state.add_occurrence(position)
            path = current_path + (name,)
            state.alias(path)


@visit.register
def visit_assign(node: ast.Assign, source: Source, state: State) -> None:

    for node_target in node.targets:
        visit(node_target, source, state)
    visit(node.value, source, state)

    target_names = get_names(node.targets[0])
    value_names = get_names(node.value)
    for target, value in zip(target_names, value_names):

        if target and value:
            path: QualifiedName = ()
            with ExitStack() as stack:
                for name in target[:-1]:
                    stack.enter_context(state.scope(name))
                    stack.enter_context(state.scope("."))
                    path += ("..",)
                stack.enter_context(state.scope(target[-1]))
                path += ("..",)

                other_node = state.follow_path(path + value)
                state.current_node.alias_namespace(other_node)


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, state: State
) -> None:
    is_method = state.lookup_scopes[-1] and state.lookup_scopes[-1].is_class
    position = node_position(node, source, column_offset=len("def "))

    with state.scope(node.name):
        state.add_occurrence(position)

        with state.scope("()"):
            for i, arg in enumerate(node.args.args):
                position = node_position(arg, source)

                with state.scope(arg.arg):
                    state.add_occurrence(position)
                    if i == 0 and is_method and not is_static_method(node):
                        other_node = state.follow_path(("..", "..", "..", ".."))
                        state.current_node.alias_namespace(other_node)

            generic_visit(node, source, state)


@visit.register
def visit_class(node: ast.ClassDef, source: Source, state: State) -> None:
    position = node_position(node, source, column_offset=len("class "))

    for base in node.bases:
        visit(base, source, state)
        if isinstance(base, ast.Name):
            with state.scope(base.id):
                with state.scope("()"):
                    other_node = state.follow_path(("..", "..", node.name, "()"))
                    state.current_node.alias_namespace(other_node)

    with state.scope(node.name, lookup_scope=True, is_class=True):
        state.add_occurrence(position)

        with state.scope("()"):
            other_node = state.follow_path(("..",))
            state.current_node.alias_namespace(other_node)

            with state.scope("."):
                for statement in node.body:
                    visit(statement, source, state)


@visit.register
def visit_call(node: ast.Call, source: Source, state: State) -> None:
    call_position = node_position(node, source)

    for arg in node.args:
        visit(arg, source, state)

    names = names_from(node.func)

    visit(node.func, source, state)
    position = node_position(node, source)
    lookup_scope = state.lookup_scopes[-1]
    if names == ("super",) and lookup_scope:
        with state.scope("super"):
            with state.scope("()"):
                state.current_node.alias_namespace(lookup_scope["()"])
    else:
        with ExitStack() as stack:
            if names:
                stack.enter_context(state.scope(names[0]))
                for name in names[1:]:
                    stack.enter_context(state.scope(name))
                stack.enter_context(state.scope("()"))

                for keyword in node.keywords:
                    if not keyword.arg:
                        continue

                    position = source.find_after(keyword.arg, call_position)
                    with state.scope(keyword.arg):
                        state.add_occurrence(position)


@singledispatch
def names_from(node: ast.AST) -> QualifiedName:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> QualifiedName:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> QualifiedName:
    return names_from(node.value) + (".", node.attr)


@names_from.register
def call_names(node: ast.Call) -> QualifiedName:
    names = names_from(node.func) + ("()",)
    return names


@visit.register
def visit_attribute(node: ast.Attribute, source: Source, state: State) -> None:
    visit(node.value, source, state)
    position = node_position(node, source)

    names = names_from(node.value)
    with ExitStack() as stack:
        for name in names:
            position = source.find_after(name, position)
            stack.enter_context(state.scope(name))

        stack.enter_context(state.scope("."))
        position = source.find_after(node.attr, position)
        stack.enter_context(state.scope(node.attr))
        state.add_occurrence(position)


def visit_comp(
    node: Union[ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp],
    source: Source,
    state: State,
    *sub_nodes,
) -> None:
    position = node_position(node, source)
    name = f"{type(node)}-{position.row},{position.column}"

    with state.scope(name):

        for generator in node.generators:
            visit(generator.target, source, state)
            visit(generator.iter, source, state)
            for if_node in generator.ifs:
                visit(if_node, source, state)

        for sub_node in sub_nodes:
            visit(sub_node, source, state)


@visit.register
def visit_dict_comp(node: ast.DictComp, source: Source, state: State) -> None:
    visit_comp(node, source, state, node.key, node.value)


@visit.register
def visit_list_comp(node: ast.ListComp, source: Source, state: State) -> None:
    visit_comp(node, source, state, node.elt)


@visit.register
def visit_set_comp(node: ast.SetComp, source: Source, state: State) -> None:
    visit_comp(node, source, state, node.elt)


@visit.register
def visit_generator_exp(node: ast.GeneratorExp, source: Source, state: State) -> None:
    visit_comp(node, source, state, node.elt)


@visit.register
def visit_global(node: ast.Global, source: Source, state: State) -> None:
    position = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, position)
        with state.scope(name):
            state.add_occurrence(position)
            state.alias(("~", name))


@visit.register
def visit_nonlocal(node: ast.Nonlocal, source: Source, state: State) -> None:
    position = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, position)
        with state.scope(name):
            state.add_occurrence(position)
            state.alias(("..", "..", "..", name))


def all_occurrence_positions(
    position: Position, other_sources: Optional[List[Source]] = None
) -> Iterable[Position]:
    source = position.source
    state = State(position)
    visit(source.get_ast(), source=source, state=state)
    for other in other_sources or []:
        visit(other.get_ast(), source=other, state=state)

    if state.found:
        return sorted(state.found.occurrences)

    return []


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

    assert all_occurrence_positions(source.position(1, 8)) == [
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    ]


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
        Position(source, 9, 8),
        Position(source, 10, 16),
    ]


def test_finds_imports():
    source = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """
    )
    position = Position(source, 1, 14)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 14),
        Position(source, 8, 0),
    ]


def test_does_not_rename_imported_names():
    source = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """
    )
    position = Position(source, 5, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 5, 4),
        Position(source, 6, 10),
    ]


def test_finds_across_files():
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import old
        old()
        """,
        module_name="bar",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 1, 16),
        Position(source2, 2, 0),
    ]


def test_finds_namespace_imports():
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        import foo
        foo.old()
        """,
        module_name="bar",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 2, 4),
    ]


def test_finds_imports_with_paths():
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo.bar",
    )
    source2 = make_source(
        """
        from foo.bar import old
        old()
        """,
        module_name="qux",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 1, 20),
        Position(source2, 2, 0),
    ]
