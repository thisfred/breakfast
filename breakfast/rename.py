"""Rename refactorings."""

from ast import Attribute, Call, Name, NodeVisitor, Param, Store
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


def rename(sources, position, old_name, new_name):
    visitor = NewNameCollector(position, name=old_name)
    for module, source in sources.items():
        visitor.process(
            source=source,
            initial_scope=module)
    for occurrence in sorted(visitor.get_occurrences(), reverse=True):
        occurrence.source.replace(
            position=occurrence,
            old=old_name,
            new=new_name)
    return sources


class NewNameCollector(NodeVisitor):

    def __init__(self, position, name):
        self.names = defaultdict(set)
        self.aliases = {}
        self.rewrites = {}
        self.definitions = set()
        self._class_scope = False
        self.source = None
        self._scope = None
        self._position = position
        self._name = name

    def process(self, source, initial_scope):
        self.source = source
        self._scope = (initial_scope,)
        self.visit(self.source.get_ast())
        for path, positions in self.names.copy().items():
            alternative = (
                self.rewrites.get(path) or self.shorten(path) or
                self.get_definition(path))
            if alternative and alternative != path:
                self.names[alternative] |= positions
                del self.names[path]
                continue
            if not alternative:
                del self.names[path]
                continue

    def get_definition(self, path):
        if path in self.definitions:
            return path
        name = path[-1:]
        scope = path[:-1]
        while scope:
            scope = scope[:-1]
            shrunk = scope + name
            if shrunk in self.definitions:
                return shrunk

    def get_occurrences(self):
        for occurrences in self.names.values():
            if self._position in occurrences:
                return occurrences
        return []

    def shorten(self, path):
        for long_form, alias in self.aliases.items():
            if self.is_prefix(long_form, path):
                return alias + path[len(long_form):]

    def is_prefix(self, prefix, path):
        return len(path) > len(prefix) and self.starts_with(path, prefix)

    @staticmethod
    def starts_with(longer, shorter):
        return all(a == b for a, b in zip(shorter, longer))

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

    @staticmethod
    def is_definition(node):
        return isinstance(node.ctx, Store) or isinstance(node.ctx, Param)

    def occur(self, name, position, is_definition=True):
        self.names[self._scope + (name,)].add(position)
        if is_definition:
            self.definitions.add(self._scope + (name,))

    def add_alias(self, arg):
        if isinstance(arg, Name):
            name = arg.id
        else:
            name = arg.arg

        self.aliases[self._scope + (name,)] = self._scope[:-1]

    def position_from_node(self, node, column_offset=0, row_offset=0):
        return Position(
            source=self.source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset)

    def visit_Name(self, node):  # noqa
        if node.id != self._name:
            return

        position = self.position_from_node(node)
        self.occur(node.id, position, is_definition=self.is_definition(node))

    def visit_ClassDef(self, node):  # noqa
        if node.name == self._name:
            position = self.position_from_node(
                node=node,
                row_offset=len(node.decorator_list),
                column_offset=len('class '))
            self.occur(name=node.name, position=position)
        with self.scope(node.name, class_scope=True):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        if node.name == self._name:
            position = self.position_from_node(
                node=node,
                row_offset=len(node.decorator_list),
                column_offset=len('fun '))
            self.occur(name=node.name, position=position)

        is_method = self._class_scope
        with self.scope(node.name, class_scope=False):
            self.process_args(node.args.args, is_method)
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        name = node.attr
        if name == self._name:
            start = self.position_from_node(node=node)
            with self.scope(node.value.id):
                position = self.source.find_after(name, start)
                self.occur(name=name, position=position)

        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        # TODO: handle multiple assignment
        name = name_from_node(node.targets[0])
        if name:
            self.aliases[self._scope + (name,)] = self._scope + (
                name_from_node(node.value),)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        if isinstance(node.func, Name):
            names = (node.func.id,)
        else:
            attribute = node.func
            names = tuple()
            while isinstance(attribute, Attribute):
                names = (attribute.attr,) + names
                attribute = attribute.value
            names = (attribute.id,) + names

        with self.tuple_scope(names):
            for keyword in node.keywords:
                if keyword.arg != self._name:
                    continue
                self.occur(
                    name=keyword.arg,
                    position=self.arg_position_from_value(
                        value=keyword.value,
                        name=keyword.arg))

        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa
        start = position_from_node(source=self.source, node=node)
        for imported in node.names:
            name = imported.name
            self.rewrites[self._scope + (name,)] = (node.module, name)
            if name != self._name:
                continue
            position = self.source.find_after(name, start)
            self.occur(name=name, position=position)

    def visit_DictComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_SetComp(self, node):  # noqa
        self.comp_visit(node)

    def visit_ListComp(self, node):  # noqa
        self.comp_visit(node)

    def comp_visit(self, node):
        position = position_from_node(source=self.source, node=node)
        # The dashes make sure it can never clash with an actual Python name.
        name = 'comprehension-%s-%s' % (position.row, position.column)
        with self.scope(name):
            self.generic_visit(node)

    def arg_position_from_value(self, value, name):
        position = self.position_from_node(node=value)
        start = self.source.find_before('=', position)
        return self.source.find_before(name, start)

    def process_args(self, args, is_method):
        if args and is_method:
            arg = args[0]
            self.add_alias(arg)

        for arg in args:
            if isinstance(arg, Name):
                # python 2: these will be caught by generic_visit
                continue

            # python 3
            if arg.arg != self._name:
                continue
            position = self.position_from_node(node=arg)
            self.occur(name=arg.arg, position=position)


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
