"""Rename refactorings."""
from typing import Any, Callable, List, Tuple  # noqa
from collections import defaultdict
from ast import Call, ClassDef, FunctionDef, Name, NodeVisitor, parse
from breakfast.source import Source
from breakfast.position import Position


class NameVisitor(NodeVisitor):

    def __init__(self, old_name: str) -> None:
        self.old_name = old_name
        self.positions = defaultdict(list)  # type: Dict[Any, Any]
        self.scope = tuple()  # type: Tuple[str, ...]
        self.names = {}  # type: Dict[str, str]

    def replace_occurrences(self,
                            source: Source,
                            position: Position,
                            new_name: str):
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
            self.add_node(node, len('def '))

        self.scope += (node.name,)
        for arg in node.args.args:
            if arg.arg == self.old_name:
                self.add_node(arg)
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_ClassDef(self, node: ClassDef):  # noqa
        if node.name == self.old_name:
            self.add_node(node, len('class '))
        self.scope += (node.name,)
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_Attribute(self, node):  # noqa
        if node.attr == self.old_name:
            self.add_node(node, len(node.value.id) + 1)
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self.names[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        self.scope += (self.get_name(node.func),)
        for keyword in node.keywords:
            if keyword.arg == self.old_name:
                self.add_node(keyword.value, -(len(self.old_name) + 1))
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def add_node(self, node, offset=0):
        self.positions[self.scope].append(Position.from_node(node) + offset)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        return self.lookup(node.value.id)

    def lookup(self, name):
        return self.names.get(name, name)


def rename(*,
           source: str,
           cursor: Position,
           old_name: str,
           new_name: str) -> str:
    ast = parse(source)
    wrapped_source = Source(source)
    start = wrapped_source.get_start(name=old_name, before=cursor)
    visitor = NameVisitor(old_name=old_name)
    visitor.visit(ast)
    visitor.replace_occurrences(
        source=wrapped_source,
        position=start,
        new_name=new_name)
    return wrapped_source.render()


def modified(*,
             source: List[str],
             cursor: Position,
             old_name: str,
             new_name: str):
    ast = parse('\n'.join(source))
    wrapped_source = Source.from_list(source)
    start = wrapped_source.get_start(name=old_name, before=cursor)
    visitor = NameVisitor(old_name=old_name)
    visitor.visit(ast)
    visitor.replace_occurrences(
        source=wrapped_source,
        position=start,
        new_name=new_name)
    for change in wrapped_source.get_changes():
        yield change
