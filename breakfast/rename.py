"""Rename refactorings."""

from ast import (
    Attribute, Call, ClassDef, FunctionDef, Name, NodeVisitor, Param, Store)
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position
from breakfast.scope import Scope
from breakfast.source import Source

TOP = Scope()


class Rename:

    def __init__(self, files):
        self.position = None
        self.old_name = None
        self.new_name = None
        self.visitor = None
        self.module = None
        self.sources = {
            m: Source(lines=l) for m, l in files.items()}

    def initialize(self, module, position, old_name, new_name):
        self.position = position
        self.old_name = old_name
        self.new_name = new_name
        self.module = module
        self.visitor = NameCollector(old_name)

    def apply(self):
        primary_source = self.sources[self.module]
        for module, source in self.sources.items():
            with self.visitor.scope(module):
                self.visitor.visit(source.get_ast())
        name_position = primary_source.get_start(
            name=self.old_name, before=self.position)
        for occurrence in self.find_occurrences(name_position):
            self.sources.get(occurrence.module, primary_source).replace(
                position=occurrence,
                old=self.old_name,
                new=self.new_name)

    def get_changes(self, module):
        return self.sources[module].get_changes()

    def get_result(self, module):
        return self.sources[module].render()

    def find_occurrences(self, position):
        grouped = self.group_occurrences()
        for _, positions in grouped.items():
            if position in positions:
                return sorted(positions, reverse=True)

    def group_occurrences(self):
        to_do = {}
        done = defaultdict(list)
        occurrences = self.visitor.occurrences
        for path in sorted(occurrences.keys(), reverse=True):
            path_occurrences = occurrences[path]
            for occurrence in path_occurrences:
                if occurrence.is_definition:
                    done[path] = path_occurrences
                    break
            else:
                to_do[path[:-1]] = path_occurrences

        for path in to_do:
            for prefix in self.get_prefixes(path, done):
                done[prefix].extend(to_do[path])
                break

        return done

    @staticmethod
    def get_prefixes(path, done):
        prefix = path
        while prefix and prefix not in done:
            prefix = prefix[:-1]
            yield prefix
        yield prefix


class NameCollector(NodeVisitor):

    def __init__(self, name):
        self.occurrences = defaultdict(list)
        self._scope = TOP
        self._lookup = {}
        self._name = name
        self._aliases = {}

    def visit_Print(self, node):  # noqa
        # python 2
        self.occur(
            scope=self._scope.path,
            position=self.position_from_node(node),
            name='print')

        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        if node.id != self._name:
            return

        position = self.position_from_node(
            node=node,
            is_definition=(
                isinstance(node.ctx, Store) or isinstance(node.ctx, Param)))
        self.occur(
            scope=self._scope.path,
            position=position,
            name=node.id)

    def visit_ClassDef(self, node):  # noqa
        class_name = self._scope.get_name(node.name)
        if node.name == self._name:
            self.add_definition(node=node, name=node.name)
        with self.scope(node.name, in_class=class_name):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        if node.name == self._name:
            self.add_definition(node=node, name=node.name)
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
                if arg.arg == self._name:
                    self.add_definition(node=arg, name=arg.arg)
            self.generic_visit(node)

    def visit_DictComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_SetComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_ListComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_Attribute(self, node):  # noqa
        if node.attr == self._name:
            with self.scope(self.get_name(node.value)):
                path = self.lookup(self._scope.path)
                scope = self._scope
                while scope.path and scope.path != path:
                    scope = scope.parent
                position = self.position_from_node(
                    node=node,
                    module=scope.path[0])
                self.occur(scope.path, position, node.attr)

        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        name = name_from_node(node.targets[0])
        if name:
            self._lookup[name] = name_from_node(node.value)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        with self.scope(self.get_name(node.func)):
            for keyword in node.keywords:
                if keyword.arg != self._name:
                    continue

                position = self.position_from_node(
                    node=keyword.value,
                    extra_offset=-(len(keyword.arg) + 1))
                self.occur(
                    scope=self._scope.path,
                    position=position,
                    name=keyword.arg)

        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa
        name = node.names[0].name
        if name != self._name:
            return

        self._aliases[
            self._scope.get_name(name)] = (node.module, name)
        position = self.position_from_node(
            node=node,
            # TODO: handle multiple imports
            extra_offset=len('from %s import ' % (node.module)))
        self.occur(self._scope.path, position, name)

    def comp_visit(self, node):
        """Create a unique scope for the comprehension."""
        position = self.position_from_node(node, module=self._scope.path[0])
        # The dashes make sure it can never clash with an actual Python name.
        name = 'comprehension-%s-%s' % (position.row, position.column)
        self.scoped_visit(name, node)

    def scoped_visit(self, added_scope, node):
        with self.scope(added_scope):
            self.generic_visit(node)

    @contextmanager
    def scope(self, name, in_class=None):
        self._scope = self._scope.enter_scope(
            name=name, direct_class=in_class)
        yield
        self._scope = self._scope.parent

    def occur(self, scope, position, name):
        path = self._aliases.get(scope + (name,), scope + tuple())
        self.occurrences[path].append(position)

    def add_definition(self, node, name):
        position = self.position_from_node(
            node=node,
            is_definition=True)
        self.occur(self._scope.path, position, name)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        if isinstance(node, Attribute):
            return self.lookup(node.attr)

    def lookup(self, name):
        return self._lookup.get(name, name)

    def position_from_node(self, node, module=None, extra_offset=0,
                           is_definition=False):
        if isinstance(node, ClassDef):
            extra_offset += len('class ')
        elif isinstance(node, FunctionDef):
            extra_offset += len('fun ')
        elif isinstance(node, Attribute):
            extra_offset += len(node.value.id) + 1

        return Position(
            row=node.lineno - 1,
            column=node.col_offset + extra_offset,
            module=module or self._scope.path[0],
            is_definition=is_definition)


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
