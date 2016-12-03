"""Rename refactorings."""

from ast import Attribute, Call, ClassDef, FunctionDef, Load, Name, NodeVisitor
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


class NameCollector(NodeVisitor):
    def __init__(self):
        self.positions = defaultdict(list)
        self._scope = tuple()
        self._in_class = None
        self._self = None
        self._lookup = {}

    def find_occurrences(self, position):
        for _, occurrences in self.positions.items():
            if position in occurrences:
                return occurrences

    def visit_Print(self, node):  # noqa
        # python 2
        self.add_name(tuple(), node, 'print')
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        scope = self._scope
        name = node.id
        if isinstance(node.ctx, Load) and (
                not scope or not scope[-1].startswith('$')):
            while scope and not self.in_scope(scope, name):
                scope = scope[:-1]
        self.add_name(scope, node, name)
        self.generic_visit(node)

    def in_scope(self, scope, name):
        return self.get_scoped_name(scope, name) in self.positions

    def visit_ClassDef(self, node):  # noqa
        self.add_name(self._scope, node, node.name)
        with self.scope(node.name,
                        in_class=self.get_scoped_name(self._scope, node.name)):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        self.add_name(self._scope, node, node.name)

        in_class = self._in_class
        with self.scope(node.name):
            for i, arg in enumerate(node.args.args):
                if in_class and i == 0:
                    if isinstance(arg, Name):
                        # python 2
                        self._self = arg.id
                    else:
                        self._self = arg.arg
                if isinstance(arg, Name):
                    # python 2
                    continue

                # python 3
                self.add_name(self._scope, arg, arg.arg)
            self.generic_visit(node)

    def visit_DictComp(self, node):  # noqa
        self.scoped_visit('$dc', node)

    def visit_SetComp(self, node):  # noqa
        self.scoped_visit('$sc', node)

    def visit_ListComp(self, node):  # noqa
        self.scoped_visit('$lc', node)

    def scoped_visit(self, added_scope, node):
        with self.scope(added_scope):
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        if self._in_class and node.value.id == self._self:
            self.add_name(self._in_class, node, node.attr)
        else:
            with self.scope(self.get_name(node.value)):
                self.add_name(self._scope, node, node.attr)
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self._lookup[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                self.add_name(
                    scope=self._scope,
                    node=keyword.value,
                    name=keyword.arg,
                    offset=-(len(keyword.arg) + 1))
        self.generic_visit(node)

    @contextmanager
    def scope(self, name, in_class=None):
        if in_class:
            in_class_before = self._in_class
            self._in_class = in_class
        self._scope += (name,)
        yield
        self._scope = self._scope[:-1]
        if in_class:
            self._in_class = in_class_before

    def get_scoped_name(self, scope, name):
        return scope + (name,)

    def add_name(self, scope, node, name, offset=0):
        self.positions[
            self.get_scoped_name(scope, name)].append(
                position_from_node(node) + offset)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        return self.lookup(node.value.id)

    def lookup(self, name):
        return self._lookup.get(name, name)


def position_from_node(node):
    extra_offset = 0
    if isinstance(node, ClassDef):
        extra_offset = len('class ')
    elif isinstance(node, FunctionDef):
        extra_offset = len('fun ')
    elif isinstance(node, Attribute):
        extra_offset = len(node.value.id) + 1

    return Position(row=node.lineno - 1, column=node.col_offset) + extra_offset
