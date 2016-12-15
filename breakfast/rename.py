"""Rename refactorings."""

from ast import (
    Attribute, Call, ClassDef, FunctionDef, Name, NodeVisitor, Param, Store)
from collections import defaultdict
from contextlib import contextmanager

from breakfast.occurrence import Occurrence, Position
from breakfast.scope import Scope

TOP = Scope()


class NameCollector(NodeVisitor):

    def __init__(self, name):
        self._occurrences = defaultdict(list)
        self._scope = TOP
        self._lookup = {}
        self._name = name

    def group_occurrences(self):
        to_do = {}
        done = defaultdict(list)
        for path in sorted(self._occurrences.keys(), reverse=True):
            occurrences = self._occurrences[path]
            for occurrence in self._occurrences[path]:
                if occurrence.is_definition:
                    done[path] = [o.position for o in occurrences]
                    break
            else:
                to_do[path[:-1]] = [o.position for o in occurrences]
        for path in to_do:
            prefix = path
            while prefix and prefix not in done:
                prefix = prefix[:-1]
            done[prefix].extend(to_do[path])
        return done

    def find_occurrences(self, position):
        grouped = self.group_occurrences()
        for positions in grouped.values():
            if position in positions:
                return positions

    def visit_Module(self, node):  # noqa
        with self.scope("module"):
            self.generic_visit(node)

    def visit_Print(self, node):  # noqa
        # python 2
        position = position_from_node(node)
        self.add_occurrence(
            scope=self._scope.path,
            name='print',
            position=position)
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        name = node.id
        position = position_from_node(node)
        self.add_occurrence(
            scope=self._scope.path,
            name=name,
            position=position,
            is_definition=(
                isinstance(node.ctx, Store) or isinstance(node.ctx, Param)))
        self.generic_visit(node)

    def visit_ClassDef(self, node):  # noqa
        class_name = self._scope.get_name(node.name)
        position = position_from_node(node)
        self.add_occurrence(
            scope=self._scope.path,
            name=node.name,
            position=position,
            is_definition=True)
        with self.scope(node.name, in_class=class_name):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        position = position_from_node(node)
        self.add_occurrence(
            scope=self._scope.path,
            name=node.name,
            position=position,
            is_definition=True)
        is_method = self._scope.in_class_scope
        with self.scope(node.name):
            for i, arg in enumerate(node.args.args):
                if is_method and i == 0:
                    self._lookup[
                        self._scope.path +
                        (arg_or_id(arg),)] = self._scope.direct_class
                if isinstance(arg, Name):
                    # python 2
                    continue

                # python 3

                self.add_occurrence(
                    scope=self._scope.path,
                    name=arg.arg,
                    position=position_from_node(arg),
                    is_definition=True)
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

            self.add_occurrence(
                scope=scope.path,
                name=node.attr,
                position=position_from_node(node))
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        name = name_from_node(node.targets[0])
        if name:
            self._lookup[name] = name_from_node(node.value)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                position = (
                    position_from_node(keyword.value) - (len(keyword.arg) + 1))
                self.add_occurrence(
                    scope=self._scope.path,
                    name=keyword.arg,
                    position=position)
        self.generic_visit(node)

    def scoped_visit(self, added_scope, node):
        with self.scope(added_scope):
            self.generic_visit(node)

    @contextmanager
    def scope(self, name, in_class=None):
        if name is None:
            yield
        else:
            self._scope = self._scope.enter_scope(
                name=name, direct_class=in_class)
            yield
            self._scope = self._scope.parent

    def add_occurrence(self, scope, name, position, is_definition=False):
        if name == self._name:
            self._occurrences[scope].append(
                Occurrence(
                    name=name,
                    position=position,
                    is_definition=is_definition))

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        if isinstance(node, Attribute):
            return self.lookup(node.attr)

    def lookup(self, name):
        return self._lookup.get(name, name)


def arg_or_id(arg):
    if isinstance(arg, Name):
        # python2
        return arg.id

    return arg.arg


def name_from_node(node):
    if isinstance(node, Attribute):
        return node.attr

    if isinstance(node, Call):
        return name_from_node(node.func)

    if isinstance(node, Name):
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
