"""Rename refactorings."""

from ast import Attribute, Call, Name, NodeVisitor, Param, Store
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
                self.visitor.set_source(source)
                self.visitor.collect_occurrences()
        for occurrence in self.find_occurrences(self.position):
            source = occurrence.source or primary_source
            source.replace(
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

    @staticmethod
    def done_or_todo(occurrences, done, to_do):
        if any(o.is_definition for o in occurrences):
            return done

        return to_do

    def group_occurrences(self):
        to_do = {}
        done = defaultdict(list)
        occurrences = self.visitor.occurrences
        for path, path_occurrences in sorted(occurrences.items(),
                                             reverse=True):
            self.done_or_todo(
                path_occurrences,
                done=done,
                to_do=to_do)[path] = path_occurrences

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


class NameCollector(NodeVisitor):

    def __init__(self, name):
        self.occurrences = defaultdict(list)
        self._scope = TOP
        self._lookup = {}
        self._name = name
        self._aliases = {}
        self.source = None

    def set_source(self, source):
        self.source = source

    def collect_occurrences(self):
        self.visit(self.source.get_ast())

    def visit_Print(self, node):  # noqa
        # python 2
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        if node.id != self._name:
            return

        position = Position.from_node(
            source=self.source,
            node=node,
            is_definition=(
                isinstance(node.ctx, Store) or isinstance(node.ctx, Param)))
        self.occur(
            scope=self._scope.path,
            position=position,
            name=node.id)

    def visit_ClassDef(self, node):  # noqa
        name = node.name
        self.add_definition(node, extra_offset=len('class '))
        with self.scope(name, in_class=self._scope.get_name(name)):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        self.add_definition(node, extra_offset=len('fun '))
        is_method = self._scope.in_class_scope
        with self.scope(node.name):
            self.process_args(node.args.args, is_method)
            self.generic_visit(node)

    def process_args(self, args, is_method):
        if args and is_method:
            arg = args[0]
            self._lookup[
                self._scope.path +
                (arg_or_id(arg),)] = self._scope.direct_class

        for arg in args:
            if isinstance(arg, Name):
                # python 2: these will be caught by generic_visit
                continue

            # python 3
            self.add_definition(node=arg, name=arg.arg)

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

                position = Position.from_node(
                    source=self.source,
                    node=node,
                    extra_offset=len(node.value.id) + 1)
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

                self.occur(
                    scope=self._scope.path,
                    position=self.arg_position_from_value(keyword.value),
                    name=keyword.arg)

        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa
        name = node.names[0].name
        if name != self._name:
            return

        self._aliases[
            self._scope.get_name(name)] = (node.module, name)

        position = Position.from_node(
            source=self.source,
            node=node,
            # TODO: handle multiple imports
            extra_offset=len('from %s import ' % (node.module)))
        self.occur(self._scope.path, position, name)

    def comp_visit(self, node):
        """Create a unique scope for the comprehension."""

        position = Position.from_node(source=self.source, node=node)
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

    def add_definition(self, node, extra_offset=0, name=None):
        name = name or node.name
        if name != self._name:
            return

        position = Position.from_node(
            source=self.source,
            node=node,
            extra_offset=extra_offset,
            is_definition=True)
        self.occur(self._scope.path, position, name)

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        if isinstance(node, Attribute):
            return self.lookup(node.attr)

    def lookup(self, name):
        return self._lookup.get(name, name)

    def arg_position_from_value(self, value):
        position = Position.from_node(
            source=self.source,
            node=value)
        start = self.source.find_backwards('=', position)
        return self.source.find_backwards(self._name, start)


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
