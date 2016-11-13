"""Rename refactorings."""
from typing import Any, Callable, List, Tuple  # noqa
from collections import defaultdict
from ast import Call, ClassDef, FunctionDef, Name, NodeVisitor, parse
from breakfast.source import Source
from breakfast.position import Position


class NameVisitor(NodeVisitor):

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.positions = defaultdict(list)  # type: Dict[Any, Any]
        self.scope = tuple()  # type: Tuple[str, ...]
        self.names = {}

    def find_scope(self, position: Position) -> Tuple[str, ...]:
        for scope, positions in self.positions.items():
            if position in positions:
                return scope

        raise KeyError("Position not found.")

    def visit_Name(self, node: Name):  # noqa
        if node.id == self.source_name:
            self.positions[self.scope].append(Position.from_node(node))

    def visit_FunctionDef(self, node: FunctionDef):  # noqa
        if node.name == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len('def '))

        self.scope += (node.name,)
        for arg in node.args.args:
            if arg.arg == self.source_name:
                self.positions[self.scope].append(Position.from_node(arg))
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_ClassDef(self, node: ClassDef):  # noqa
        if node.name == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len('class '))
        self.scope += (node.name,)
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_Attribute(self, node):  # noqa
        if node.attr == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len(node.value.id) + 1)
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self.names[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        self.scope += (self.get_name(node.func),)
        for keyword in node.keywords:
            if keyword.arg == self.source_name:
                self.positions[self.scope].append(
                    Position.from_node(keyword.value) -
                    (len(self.source_name) + 1))
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        return self.lookup(node.value.id)

    def lookup(self, name):
        return self.names.get(name, name)


class Renamer:

    def __init__(self, *,
                 source: str,
                 position: Position,
                 new_name: str) -> None:
        self.new_name = new_name
        self.position = position
        self.source = Source(source)
        self.ast = parse(source)
        self.old_name, self.position = self.source.get_name_and_position(
            self.position)

    def rename(self) -> str:
        visitor = NameVisitor(source_name=self.old_name)
        visitor.visit(self.ast)

        original_scope = visitor.find_scope(self.position)
        for scope, positions in visitor.positions.items():
            for position in reversed(positions):
                if scope[:len(original_scope)] == original_scope:
                    self.replace_name_at(position)
        return self.source.render()

    def replace_name_at(self, position: Position):
        self.source.replace(
            position=position,
            old=self.old_name,
            new=self.new_name)
