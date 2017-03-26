"""Rename refactorings."""

from ast import (
    Attribute, Call, ClassDef, Name, NodeVisitor, Param, Store, Tuple)
from collections import defaultdict
from collections import namedtuple
from contextlib import contextmanager

from breakfast.position import Position

ScopedPosition = namedtuple('ScopedPosition', ['scope', 'position'])


class Rename:

    def __init__(self, name, position, new_name):
        self._additional_sources = []
        self._name = name
        self._visitor = NameVisitor(name=name)
        self._position = position
        self._initial_source = position.source
        self._new_name = new_name

    def add_source(self, source):
        self._additional_sources.append(source)

    def apply(self):
        self._process_sources()
        for occurrence in sorted(self._visitor.get_occurrences(self._position),
                                 reverse=True):
            occurrence.source.replace(
                position=occurrence,
                old=self._name,
                new=self._new_name)

    def _process_sources(self):
        for source in self._get_sources():
            self._visitor.process(source, source.module_name)

    def _get_sources(self):
        return [self._initial_source] + self._additional_sources


class Scope:

    def __init__(self, name=None, parent=None, position=None):
        self.name = name
        self.parent = parent
        self.position = position
        self.children = []
        self.is_class_definition = (
            position and position.node and
            isinstance(position.node, ClassDef))

    def add_child(self, name, position):
        child = Scope(
            name=name,
            parent=self,
            position=position)
        self.children.append(child)
        return child

    @property
    def path(self):
        names = [self.name]
        parent = self.parent
        while parent:
            names.append(parent.name)
            parent = parent.parent
        return tuple(n for n in reversed(names) if n)

    def render(self, indentation=0):
        return '{}- {} {}\n'.format(
            " " * indentation, self.name, self.position) + ''.join(
                child.render(indentation=indentation + 2) for child in
                self.children)


class NameVisitor(NodeVisitor):

    def __init__(self, name):
        self._name = name
        self._state = State()
        self._current_source = None
        self._scope = Scope()

    @contextmanager
    def scope(self, names, position):
        previous = self._scope
        if not isinstance(names, tuple):
            names = (names,)
        for name in names:
            self._scope = self._scope.add_child(
                name=name,
                position=position)
        yield
        self._scope = previous

    def process(self, source, initial_scope):
        self._current_source = source
        with self.scope(names=initial_scope,
                        position=Position(source, 0, 0)):
            self.visit(self._current_source.get_ast())
            self._state.post_process()

    def get_occurrences(self, position):
        return self._state.get_occurrences(position)

    def visit_Name(self, node):  # noqa
        if node.id != self._name:
            return

        position = self._position_from_node(node)
        self.occur(
            name=node.id,
            position=position,
            is_definition=self._is_definition(node))

    def visit_ClassDef(self, node):  # noqa
        prefix = 'class'
        if node.name == self._name:
            self._occur_definition(node=node, prefix=prefix)
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len(prefix) + 1)
        with self.scope(names=node.name, position=position):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        prefix = 'def'
        if node.name == self._name:
            self._occur_definition(node=node, prefix=prefix)
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len(prefix) + 1)
        is_method = self._scope.is_class_definition
        with self.scope(names=node.name, position=position):
            self._process_args(node.args.args, is_method)
            self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        name = node.attr
        if name == self._name:
            start = self._position_from_node(node=node)
            with self.scope(names=node.value.id, position=start):
                position = self._current_source.find_after(
                    name, start).copy(node=node)
                self.occur(name=name, position=position)

        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        names = names_from_node(node.targets[0])
        values = names_from_node(node.value)
        if names:
            for name, value in zip(names, values):
                self._state.add_alias(
                    scope=self._scope,
                    name=name,
                    alias=self._scope.path + (value,))
        self.generic_visit(node)

    def occur(self, name, position, is_definition=True):
        self._state.occur(
            name=name,
            position=position,
            is_definition=is_definition,
            scope=self._scope)

    def visit_Call(self, node):  # noqa
        names = AttributeNames().collect(node.func)
        with self.scope(
                names,
                position=position_from_node(source=self._current_source,
                                            node=node)):
            for keyword in node.keywords:
                if keyword.arg != self._name:
                    continue
                position = self._arg_position_from_value(
                    value=keyword.value,
                    name=keyword.arg)
                self.occur(
                    name=keyword.arg,
                    position=position)

        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa
        start = position_from_node(source=self._current_source, node=node)
        for imported in node.names:
            name = imported.name
            self._state.add_rewrite(node.module, self._scope.path, name)
            if name != self._name:
                continue
            position = self._current_source.find_after(
                name, start).copy(node=node)
            self.occur(name=name, position=position)

    def visit_DictComp(self, node):  # noqa
        self._comp_visit(node)

    def visit_SetComp(self, node):  # noqa
        self._comp_visit(node)

    def visit_ListComp(self, node):  # noqa
        self._comp_visit(node)

    def _occur_definition(self, node, prefix):
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len(prefix) + 1)
        self.occur(name=node.name, position=position)
        return position

    def _position_from_node(self, node, column_offset=0, row_offset=0):
        return Position(
            source=self._current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
            node=node)

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, Store) or isinstance(node.ctx, Param)

    def _process_args(self, args, is_method):
        if args and is_method:
            arg = args[0]
            if isinstance(arg, Name):
                name = arg.id
            else:
                name = arg.arg
            self._state.add_alias(self._scope, name, self._scope.parent.path)

        for arg in args:
            if isinstance(arg, Name):
                # python 2: these will be caught by generic_visit
                continue

            # python 3
            if arg.arg != self._name:
                continue
            position = self._position_from_node(node=arg)
            self.occur(name=arg.arg, position=position)

    def _arg_position_from_value(self, value, name):
        position = self._position_from_node(node=value)
        start = self._current_source.find_before('=', position)
        return self._current_source.find_before(name, start)

    def _comp_visit(self, node):
        position = position_from_node(source=self._current_source, node=node)
        # The dashes make sure it can never clash with an actual Python name.
        name = 'comprehension-%s-%s' % (position.row, position.column)
        with self.scope(names=name, position=position):
            self.generic_visit(node)


class State:

    def __init__(self):
        self._scope = tuple()
        self._names = defaultdict(set)
        self._aliases = {}
        self._rewrites = {}
        self._definitions = set()
        self._scopes = {}

    def get_occurrences(self, position):
        for occurrences in self._names.values():
            if position in occurrences:
                return occurrences
        return []

    def post_process(self):
        # from pprint import pprint;
        # pprint(self._aliases);
        # pprint(self._rewrites);
        # pprint(self._definitions);
        # pprint(self._names);
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
        # from pprint import pprint;
        # pprint(self._names);

    def occur(self, name, position, is_definition, scope):
        path = scope.path
        self._scopes[path] = scope
        self._names[path + (name,)].add(position)

        if is_definition:
            self._definitions.add(path + (name,))

    def add_alias(self, scope, name, alias):
        self._aliases[scope.path + (name,)] = alias

    def add_rewrite(self, module, path, name):
        self._rewrites[path + (name,)] = (module, name)

    def _shorten(self, path):
        for long_form, alias in self._aliases.items():
            if is_prefix(long_form, path):
                return alias + path[len(long_form):]

    def is_class_scope(self, path):
        scope = self._scopes.get(path)
        if scope:
            return scope.is_class_definition

        return False

    def _get_definition(self, path):
        if path in self._definitions:
            return path
        name = path[-1:]
        scope = path[:-1]
        while scope:
            scope = scope[:-1]
            if not self.is_class_scope(scope):
                shrunk = scope + name
                if shrunk in self._definitions:
                    return shrunk


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
        is_definition=is_definition,
        node=node)


def is_prefix(prefix, path):
    return len(path) > len(prefix) and starts_with(path, prefix)


def starts_with(longer, shorter):
    return all(a == b for a, b in zip(shorter, longer))
