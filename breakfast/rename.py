"""Rename refactorings."""

from ast import Attribute, Call, Name, NodeVisitor, Param, Store, Tuple
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


class AttributeNames(NodeVisitor):

    def __init__(self):
        self._names = []

    def collect(self, attribute):
        self.visit(attribute)
        return tuple(self._names)

    def visit_Attribute(self, node):  # noqa
        self.generic_visit(node)
        self._names.append(node.attr)

    def visit_Name(self, node):  # noqa
        self._names.append(node.id)


class State:

    def __init__(self):
        self._scope = tuple()
        self._class_scope = False
        self._names = defaultdict(set)
        self._aliases = {}
        self._rewrites = {}
        self._definitions = set()

    def get_occurrences(self, position):
        for occurrences in self._names.values():
            if position in occurrences:
                return occurrences
        return []

    def post_process(self):
        for path, positions in self._names.copy().items():
            alternative = (
                self._rewrites.get(path) or self._shorten(path) or
                self._get_definition(path))
            if alternative and alternative != path:
                self._names[alternative] |= positions
                del self._names[path]
                continue
            if not alternative:
                del self._names[path]

    def occur(self, name, position, is_definition=True):
        self._names[self._scope + (name,)].add(position)
        if is_definition:
            self._definitions.add(self._scope + (name,))

    def link_to_enclosing_scope(self, name):
        self._add_alias(name, self._scope[:-1])

    def add_alias_in_scope(self, name, new_name):
        self._add_alias(name, self._scope + (new_name,))

    def add_rewrite(self, module, name):
        self._rewrites[self._scope + (name,)] = (module, name)

    def in_class(self):
        return self._class_scope

    def _add_alias(self, name, alias):
        self._aliases[self._scope + (name,)] = alias

    def _shorten(self, path):
        for long_form, alias in self._aliases.items():
            if is_prefix(long_form, path):
                return alias + path[len(long_form):]

    def _get_definition(self, path):
        if path in self._definitions:
            return path
        name = path[-1:]
        scope = path[:-1]
        while scope:
            scope = scope[:-1]
            shrunk = scope + name
            if shrunk in self._definitions:
                return shrunk

    @contextmanager
    def tuple_scope(self, names):
        self._scope = self._scope + names
        yield
        self._scope = self._scope[:-len(names)]

    @contextmanager
    def scope(self, name, class_scope=None):
        if class_scope is not None:
            previous = self._class_scope
            self._class_scope = class_scope
        if name:
            self._scope = self._scope + (name,)
        yield
        if name:
            self._scope = self._scope[:-1]
        if class_scope is not None:
            self._class_scope = previous


def is_prefix(prefix, path):
    return len(path) > len(prefix) and starts_with(path, prefix)


def starts_with(longer, shorter):
    return all(a == b for a, b in zip(shorter, longer))


class NameVisitor(NodeVisitor):

    def __init__(self, name, state):
        self._name = name
        self._state = state
        self._current_source = None

    def process(self, source, initial_scope):
        self._current_source = source
        with self._state.scope(initial_scope):
            self.visit(self._current_source.get_ast())
            self._state.post_process()

    def visit_Name(self, node):  # noqa
        if node.id != self._name:
            return

        position = self.position_from_node(node)
        self._state.occur(
            name=node.id,
            position=position,
            is_definition=self.is_definition(node))

    def visit_ClassDef(self, node):  # noqa
        if node.name == self._name:
            position = self.position_from_node(
                node=node,
                row_offset=len(node.decorator_list),
                column_offset=len('class '))
            self._state.occur(name=node.name, position=position)
        with self._state.scope(node.name, class_scope=True):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        if node.name == self._name:
            position = self.position_from_node(
                node=node,
                row_offset=len(node.decorator_list),
                column_offset=len('fun '))
            self._state.occur(name=node.name, position=position)

        is_method = self._state.in_class()
        with self._state.scope(node.name, class_scope=False):
            self.process_args(node.args.args, is_method)
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        name = node.attr
        if name == self._name:
            start = self.position_from_node(node=node)
            with self._state.scope(node.value.id):
                position = self._current_source.find_after(name, start)
                self._state.occur(name=name, position=position)

        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        names = names_from_node(node.targets[0])
        values = names_from_node(node.value)
        if names:
            for name, value in zip(names, values):
                self._state.add_alias_in_scope(name, value)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        names = AttributeNames().collect(node.func)
        with self._state.tuple_scope(names):
            for keyword in node.keywords:
                if keyword.arg != self._name:
                    continue
                self._state.occur(
                    name=keyword.arg,
                    position=self.arg_position_from_value(
                        value=keyword.value,
                        name=keyword.arg))

        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa
        start = position_from_node(source=self._current_source, node=node)
        for imported in node.names:
            name = imported.name
            self.add_rewrite(node.module, name)
            if name != self._name:
                continue
            position = self._current_source.find_after(name, start)
            self._state.occur(name=name, position=position)

    def visit_DictComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_SetComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_ListComp(self, node):  # noqa
        self.comp_visit(node)

    def position_from_node(self, node, column_offset=0, row_offset=0):
        return Position(
            source=self._current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset)

    @staticmethod
    def is_definition(node):
        return isinstance(node.ctx, Store) or isinstance(node.ctx, Param)

    def process_args(self, args, is_method):
        if args and is_method:
            arg = args[0]
            if isinstance(arg, Name):
                name = arg.id
            else:
                name = arg.arg
            self._state.link_to_enclosing_scope(name)

        for arg in args:
            if isinstance(arg, Name):
                # python 2: these will be caught by generic_visit
                continue

            # python 3
            if arg.arg != self._name:
                continue
            position = self.position_from_node(node=arg)
            self._state.occur(name=arg.arg, position=position)

    def arg_position_from_value(self, value, name):
        position = self.position_from_node(node=value)
        start = self._current_source.find_before('=', position)
        return self._current_source.find_before(name, start)

    def add_rewrite(self, module, name):
        self._state.add_rewrite(module, name)

    def comp_visit(self, node):
        position = position_from_node(source=self._current_source, node=node)
        # The dashes make sure it can never clash with an actual Python name.
        name = 'comprehension-%s-%s' % (position.row, position.column)
        with self._state.scope(name):
            self.generic_visit(node)


class Rename:

    def __init__(self, name, position=None, new_name=None):
        self._initial_source = position.source
        self._additional_sources = []
        self._name = name
        self._state = State()
        self._position = position
        self._new_name = new_name
        self._visitor = NameVisitor(name=name, state=self._state)

    def get_sources(self):
        return [self._initial_source] + self._additional_sources

    def process_sources(self):
        for source in self.get_sources():
            self._visitor.process(source, source.module_name)

    def get_occurrences(self, position):
        return self._state.get_occurrences(position)

    def add_source(self, source):
        self._additional_sources.append(source)

    def apply(self):
        self.process_sources()
        for occurrence in sorted(self.get_occurrences(self._position),
                                 reverse=True):
            occurrence.source.replace(
                position=occurrence,
                old=self._name,
                new=self._new_name)


def names_from_node(node):
    if isinstance(node, Tuple):
        return [name_from_node(n) for n in node.elts]

    return [name_from_node(node)]


def name_from_node(node):
    if isinstance(node, Attribute):
        return node.attr

    if isinstance(node, Call):
        return name_from_node(node.func)

    if isinstance(node, Name):
        return node.id


def position_from_node(source, node, column_offset=0, row_offset=0,
                       is_definition=False):
    return Position(
        source=source,
        row=(node.lineno - 1) + row_offset,
        column=node.col_offset + column_offset,
        is_definition=is_definition)
