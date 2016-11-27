"""Rename refactorings."""

from ast import Call, ClassDef, FunctionDef, Name, NodeVisitor
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, List, Tuple  # noqa

from breakfast.position import Position


class NameVisitor(NodeVisitor):

    def __init__(self, old_name: str) -> None:
        self.old_name = old_name
        self.positions = defaultdict(list)  # type: Dict[Any, Any]
        self._scope = tuple()  # type: Tuple[str, ...]
        self.names = {}  # type: Dict[str, str]

    def replace_occurrences(self, source, position, new_name):
        original_scope = self.determine_scope(position)
        for scope, occurrences in self.positions.items():
            for occurrence in reversed(occurrences):
                if scope[:len(original_scope)] == original_scope:
                    source.replace(
                        position=occurrence,
                        old=self.old_name,
                        new=new_name)

    def determine_scope(self, position: Position) -> Tuple[str, ...]:
        for scope, positions in self.positions.items():
            if position in positions:
                return scope

        raise KeyError("Position not found.")

    def visit_Name(self, node: Name):  # noqa
        if node.id == self.old_name:
            self.add_node(node)

    def visit_FunctionDef(self, node: FunctionDef):  # noqa
        if node.name == self.old_name:
            self.add_node(node, offset=len('def '))

        with self.scope(node.name):
            for arg in node.args.args:
                if arg.arg == self.old_name:
                    self.add_node(arg)
            self.generic_visit(node)

    def visit_ClassDef(self, node: ClassDef):  # noqa
        if node.name == self.old_name:
            self.add_node(node, offset=len('class '))
        with self.scope(node.name):
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        with self.scope(self.get_name(node.value)):
            if node.attr == self.old_name:
                self.add_node(node, offset=len(node.value.id) + 1)
            self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self.names[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                if keyword.arg == self.old_name:
                    self.add_node(
                        node=keyword.value,
                        offset=-(len(self.old_name) + 1))
        self.generic_visit(node)

    @contextmanager
    def scope(self, name):
        self._scope += (name,)
        yield
        self._scope = self._scope[:-1]

    def add_node(self, node, offset=0):
        self.positions[self._scope].append(Position.from_node(node) + offset)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        return self.lookup(node.value.id)

    def lookup(self, name):
        return self.names.get(name, name)
