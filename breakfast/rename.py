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
        self.add_occurrence(
            scope=self._scope.path,
            node=node,
            name='print')
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        self.add_occurrence(
            scope=self._scope.path,
            name=node.id,
            node=node,
            is_definition=(
                isinstance(node.ctx, Store) or isinstance(node.ctx, Param)))
        self.generic_visit(node)

    def visit_ClassDef(self, node):  # noqa
        class_name = self._scope.get_name(node.name)
        self.add_occurrence(
            scope=self._scope.path,
            node=node,
            is_definition=True)
        with self.scope(node.name, in_class=class_name):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        self.add_occurrence(
            scope=self._scope.path,
            node=node,
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
                    node=arg,
                    name=arg.arg,
                    is_definition=True)
            self.generic_visit(node)

    def comp_visit(self, node):
        position = position_from_node(node)
        name = 'comprehension-%s-%s' % (position.row, position.column)
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
                node=node,
                name=node.attr)
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        name = name_from_node(node.targets[0])
        if name:
            self._lookup[name] = name_from_node(node.value)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                self.add_occurrence(
                    scope=self._scope.path,
                    node=keyword.value,
                    name=keyword.arg,
                    offset=-(len(keyword.arg) + 1))
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

    def add_occurrence(self, scope, node, name=None, is_definition=False,
                       offset=0):
        name = name or node.name
        if name == self._name:
            self._occurrences[scope].append(
                Occurrence(
                    name=name,
                    position=position_from_node(node, extra_offset=offset),
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


def position_from_node(node, extra_offset=0):
    if isinstance(node, ClassDef):
        extra_offset += len('class ')
    elif isinstance(node, FunctionDef):
        extra_offset += len('fun ')
    elif isinstance(node, Attribute):
        extra_offset += len(node.value.id) + 1

    return Position(row=node.lineno - 1, column=node.col_offset) + extra_offset
