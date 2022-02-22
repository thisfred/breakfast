import ast

from collections import defaultdict
from contextlib import contextmanager
from functools import singledispatch
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


QualifiedName = Tuple[str, ...]


class Node:
    def __init__(self, parent: Optional["Node"], is_class=False):
        self.parent = parent
        self.children: Dict[str, "Node"] = defaultdict(lambda: Node(parent=self))
        self.occurrences: Set[Position] = set()
        self.is_class = is_class

    def add_occurrence(self, occurrence: Any):
        self.occurrences.add(occurrence)

    def __getitem__(self, name: str) -> "Node":
        return self.children[name]

    def __contains__(self, name: str) -> bool:
        return name in self.children

    def alias(self, other: "Node") -> None:
        for name, value in other.children.items():
            if name not in self.children:
                self.children[name] = value
            else:
                self.children[name].alias(value)

        other.children = self.children
        self.occurrences |= other.occurrences
        other.occurrences = self.occurrences


class State:
    def __init__(self, position: Position):
        self.position = position
        self.root = Node(parent=None)
        self.current_node = self.root
        self.current_path: QualifiedName = tuple()
        self.lookup_scopes = [self.root]
        self.found: Optional[Node] = None

    @contextmanager
    def scope(self, name: str, lookup_scope: bool = False):
        previous_node = self.current_node
        self.current_node = self.current_node[name]
        if lookup_scope:
            self.lookup_scopes.append(self.current_node)
        self.current_path += (name,)
        yield
        self.current_node = previous_node
        self.current_path = self.current_path[:-1]
        if lookup_scope:
            self.lookup_scopes.pop()

    def add_occurrence(self, *, position: Optional[Position] = None) -> None:
        if position:
            self.current_node.occurrences.add(position)
            if position == self.position:
                self.found = self.current_node
        print(
            f"{self.current_path}: {[(o.row,o.column) for o in self.current_node.occurrences]}"
        )

    def alias(self, path: QualifiedName) -> None:
        other_node = self.current_node

        for name in path:
            if name == "..":
                if other_node.parent:
                    other_node = other_node.parent
            else:
                other_node = other_node[name]

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
    with state.scope(source.module_name):
        with state.scope(".", lookup_scope=True):
            generic_visit(node, source, state)


@visit.register
def visit_name(node: ast.Name, source: Source, state: State) -> None:
    position = node_position(node, source)
    if isinstance(node.ctx, ast.Store):
        with state.scope(node.id):
            state.add_occurrence(position=position)
    else:
        if node.id not in state.current_node:
            for scope in state.lookup_scopes[::-1]:
                if node.id in scope or scope is state.root:
                    scope[node.id].alias(state.current_node[node.id])
                    break

        with state.scope(node.id):
            state.add_occurrence(position=node_position(node, source))


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
        state.add_occurrence(position=position)

        with state.scope("()"):
            for i, arg in enumerate(node.args.args):

                position = node_position(arg, source)
                with state.scope(arg.arg):
                    state.add_occurrence(position=position)
                    if i == 0 and is_method and not is_static_method(node):
                        with state.scope("."):
                            state.alias(("..", "..", "..", "()", "."))

            generic_visit(node, source, state)


@visit.register
def visit_class(node: ast.ClassDef, source: Source, state: State) -> None:
    position = node_position(node, source, column_offset=len("class "))

    with state.scope(node.name):
        state.add_occurrence(position=position)
        with state.scope("()"):
            with state.scope("."):
                for statement in node.body:
                    visit(statement, source, state)


@visit.register
def visit_call(node: ast.Call, source: Source, state: State) -> None:
    call_position = node_position(node, source)

    for arg in node.args:
        visit(arg, source, state)

    visit(node.func, source, state)

    names = names_from(node.func)
    # for name in names[:-1]:
    #     state.enter_scope(name)

    with state.scope(names[-1]):
        with state.scope("()"):

            for keyword in node.keywords:
                if not keyword.arg:
                    continue

                position = source.find_after(keyword.arg, call_position)
                with state.scope(keyword.arg):
                    state.add_occurrence(position=position)

    # for _ in names[:-1]:
    #     state.leave_scope()


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
    names = names_from(node.func)
    return names


def all_occurrence_positions(
    position: Position,
) -> Iterable[Position]:
    source = position.source
    state = State(position)
    visit(source.get_ast(), source=source, state=state)
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
