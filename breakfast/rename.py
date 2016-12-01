"""Rename refactorings."""

from ast import Attribute, Call, ClassDef, FunctionDef, Name, NodeVisitor, Store
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


class FindDefinitionVisitor(NodeVisitor):

    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.found = None

    def visit(self, node):  # noqa
        if self.found:
            return

        super(FindDefinitionVisitor, self).visit(node)

    def visit_FunctionDef(self, node):  # noqa
        if node.name == self.name:
            self.found_node(node)
            return

        for arg in node.args.args:
            # python 2
            if isinstance(arg, Name):
                if arg.id == self.name:
                    self.found_node(arg)
                    return
            # python 3
            elif arg.arg == self.name:
                self.found_node(arg)
                return

        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        if node.id == self.name and isinstance(node.ctx, Store):
            self.found_node(node)

    def visit_ClassDef(self, node):  # noqa
        if node.name == self.name:
            self.found_node(node)
        else:
            self.generic_visit(node)

    def found_node(self, node):
        self.found = position_from_node(node)

    def get_definition(self):
        return self.found


class NameVisitor(NodeVisitor):

    def __init__(self, old_name):
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

    def determine_scope(self, position):
        for scope, positions in self.positions.items():
            if position in positions:
                return scope

        raise KeyError("Position not found.")

    def visit_Name(self, node):  # noqa
        if node.id == self.old_name:
            self.add_node(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        if node.name == self.old_name:
            self.add_node(node)

        with self.scope(node.name):
            for arg in node.args.args:
                # python 2
                if isinstance(arg, Name):
                    continue
                # python 3
                elif arg.arg == self.old_name:
                    self.add_node(arg)
            self.generic_visit(node)

    def visit_ClassDef(self, node):  # noqa
        if node.name == self.old_name:
            self.add_node(node)
        with self.scope(node.name):
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        with self.scope(self.get_name(node.value)):
            if node.attr == self.old_name:
                self.add_node(node)
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
        self.positions[self._scope].append(position_from_node(node) + offset)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        return self.lookup(node.value.id)

    def lookup(self, name):
        return self.names.get(name, name)


def position_from_node(node):
    extra_offset = 0
    if isinstance(node, ClassDef):
        extra_offset = len('class ')
    elif isinstance(node, FunctionDef):
        extra_offset = len('fun ')
    elif isinstance(node, Attribute):
        extra_offset = len(node.value.id) + 1

    return Position(row=node.lineno - 1, column=node.col_offset) + extra_offset
