"""Rename refactorings."""

from ast import Attribute, Call, ClassDef, FunctionDef, Load, Name, NodeVisitor
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position
from breakfast.scope import Scope

TOP = Scope()


class NameCollector(NodeVisitor):
    def __init__(self):
        self._positions = defaultdict(list)
        self._scope = TOP
        self._lookup = {}

    def find_occurrences(self, position):
        for _, occurrences in self._positions.items():
            if position in occurrences:
                return occurrences

    def visit_Print(self, node):  # noqa
        # python 2
        self.add_name(
            position=position_from_node(node),
            name=('print',))
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        name = name_from_node(node)
        scope = self.get_definition_scope(node, name)
        self.add_name(
            position=position_from_node(node),
            name=scope.get_name(name))
        self.generic_visit(node)

    def in_scope(self, scope, name):
        return scope.get_name(name) in self._positions

    def visit_ClassDef(self, node):  # noqa
        class_name = self._scope.get_name(node.name)
        self.add_name(
            position=position_from_node(node),
            name=class_name)
        with self.scope(node.name, in_class=class_name):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        self.add_name(
            position=position_from_node(node),
            name=self._scope.get_name(node.name))

        is_method = self._scope.in_class_scope
        with self.scope(node.name):
            for i, arg in enumerate(node.args.args):
                if is_method and i == 0:
                    self._lookup[
                        self._scope.path +
                        (name_from_node(arg),)] = self._scope.direct_class
                if isinstance(arg, Name):
                    # python 2
                    continue

                # python 3
                self.add_name(
                    position=position_from_node(arg),
                    name=self._scope.get_name(arg.arg))
            self.generic_visit(node)

    def comp_visit(self, node):
        name = '$%s-%r' % (type(node), position_from_node(node))
        self.scoped_visit(name, node)

    def visit_DictComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_SetComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_ListComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_Attribute(self, node):  # noqa
        with self.scope(self.get_name(node.value)):
            path = self.lookup(self._scope.path)
            scope = self._scope
            while scope.path and scope.path != path:
                scope = scope.parent
            self.add_name(
                position=position_from_node(node),
                name=scope.get_name(node.attr))
            self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self._lookup[
                name_from_node(node.targets[0])] = name_from_node(
                    node.value.func)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                position = (
                    position_from_node(keyword.value) - (len(keyword.arg) + 1))
                self.add_name(
                    name=self._scope.get_name(keyword.arg),
                    position=position)
        self.generic_visit(node)

    def get_definition_scope(self, node, name):
        scope = self._scope
        if isinstance(node.ctx, Load) and (
                not scope.path or not scope.path[-1].startswith('$')):
            while scope and not self.in_scope(scope, name):
                scope = scope.parent
        return scope or TOP

    def scoped_visit(self, added_scope, node):
        with self.scope(added_scope):
            self.generic_visit(node)

    @contextmanager
    def scope(self, name, in_class=None):
        self._scope = self._scope.enter_scope(name=name, direct_class=in_class)
        yield
        self._scope = self._scope.parent

    def add_name(self, name, position):
        self._positions[name].append(position)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        if isinstance(node, Attribute):
            return self.lookup(node.attr)

    def lookup(self, name):
        return self._lookup.get(name, name)


def name_from_node(node):
    if isinstance(node, Attribute):
        return node.attr

    # XXX: this is even more horrible than plain is_instance (but can't do that
    # because the type does not appear to be importable), still thinking
    # about a better solution.
    if str(type(node)) == "<class '_ast.arg'>":
        return node.arg

    return node.id


def length_from_node(node):
    if isinstance(node, Name):
        return len(node.id)

    return 0


def position_from_node(node):
    extra_offset = 0
    if isinstance(node, ClassDef):
        extra_offset = len('class ')
    elif isinstance(node, FunctionDef):
        extra_offset = len('fun ')
    elif isinstance(node, Attribute):
        extra_offset = length_from_node(node.value) + 1

    return Position(row=node.lineno - 1, column=node.col_offset) + extra_offset
