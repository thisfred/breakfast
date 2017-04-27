"""Rename refactorings."""

from ast import (
    Attribute, Call, ClassDef, Name, NodeVisitor, Param, Store, Tuple)
from collections import OrderedDict, namedtuple
from contextlib import contextmanager

from breakfast.position import Position

ScopedPosition = namedtuple('ScopedPosition', ['scope', 'position'])


class Rename:
    """A refactoring to rename any variable, function, method or class."""

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
        self._visitor.post_process()

    def _get_sources(self):
        return [self._initial_source] + self._additional_sources


class Scope:
    """Tree of nested scopes."""

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
    """Visitor that collects name and scope information from the AST."""

    def __init__(self, name):
        self._name = name
        self._names = Names()
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

    def post_process(self):
        self._names.post_process()

    def get_occurrences(self, position):
        return self._names.get_occurrences(position)

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
        self._names.add_base_classes(
            scope=self._scope,
            class_name=node.name,
            base_classes=node.bases)
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
        start = self._position_from_node(node=node)
        with self.scope(names=self.get_id(node), position=start):
            if name == self._name:
                position = self._current_source.find_after(
                    name, start).copy(node=node)
                self.occur(
                    name=name,
                    position=position,
                    is_definition=self._is_definition(node))
            self.generic_visit(node)

    def get_id(self, node):
        try:
            return node.value.id

        except AttributeError:
            return self.get_id(node.value)

    def visit_Assign(self, node):  # noqa
        names = names_from_node(node.targets[0])
        values = names_from_node(node.value)
        for name, value in zip(names, values):
            self._names.add_rewrite(
                scope=self._scope,
                name=name,
                alternative=self._scope.path + (value,))
        self.generic_visit(node)

    def occur(self, name, position, is_definition=False):
        self._names.occur(
            scope=self._scope,
            name=name,
            position=position,
            is_definition=is_definition)

    def visit_Call(self, node):  # noqa
        names = AttributeNames().collect(node.func)
        with self.scope(names,
                        position=position_from_node(
                            source=self._current_source, node=node)):
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
            self._names.add_import(node.module, self._scope.path, name)
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
        self.occur(name=node.name, position=position, is_definition=True)
        return position

    def _position_from_node(self, node, column_offset=0, row_offset=0):
        return Position(
            source=self._current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
            node=node)

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    def _process_args(self, args, is_method):
        if args and is_method:
            arg = args[0]
            if isinstance(arg, Name):
                name = arg.id
            else:
                name = arg.arg
            self._names.add_rewrite(self._scope, name, self._scope.parent.path)

        for arg in args:
            if isinstance(arg, Name):
                # python 2: these will be caught by generic_visit
                continue

            # python 3
            if arg.arg != self._name:
                continue
            position = self._position_from_node(node=arg)
            self.occur(name=arg.arg, position=position, is_definition=True)

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


class Names:
    """Collector for names found by NameVisitor."""

    def __init__(self):
        self._scope = tuple()
        self._names = OrderedDict()
        self._rewrites = OrderedDict()
        self._imports = OrderedDict()
        self._definitions = set()
        self._scopes = OrderedDict()
        self._base_classes = OrderedDict()

    def get_occurrences(self, position):
        for occurrences in self._names.values():
            if position in occurrences:
                return occurrences
        return []

    def post_process(self):
        self.apply_imports()
        self.apply_rewrites()
        self.find_definitions()

    def apply_imports(self):
        for name, alias in self._imports.items():
            if name in self._names:
                self.move_into(name, alias)

    def apply_rewrites(self):
        done = False
        while not done:
            done = True
            for long_form, rewrite in self._rewrites.items():
                for path in list(self._names.keys()):
                    if self.is_prefix(long_form, path):
                        done = False
                        new_path = self.replace_prefix(
                            path=path,
                            old_prefix=long_form,
                            new_prefix=rewrite)
                        self.move_into(path, new_path)

    def find_definitions(self):
        for path in list(self._names.keys()):
            if path in sorted(self._definitions):
                continue
            alternative = self._get_definition(path)
            if alternative and alternative != path:
                self.move_into(path, alternative)
                continue
            del self._names[path]

    def move_into(self, old_path, new_path):
        self._names[new_path] = (
            self._names.get(new_path, set()) | self._names[old_path])
        del self._names[old_path]
        if old_path in self._definitions:
            self._definitions.remove(old_path)
            self._definitions.add(new_path)

    def occur(self, scope, name, position, is_definition):
        path = scope.path
        self._scopes[path] = scope
        self._names.setdefault(path + (name,), set()).add(position)

        if is_definition:
            self._definitions.add(path + (name,))

    def add_rewrite(self, scope, name, alternative):
        path = scope.path + (name,)
        self._rewrites[path] = alternative

    def add_import(self, module, path, name):
        self._imports[path + (name,)] = (module, name)

    def add_base_classes(self, scope, class_name, base_classes):
        self._base_classes[scope.path + (class_name,)] = [
            scope.path + (name.id,) for name in base_classes]

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
        for sub_class, bases in self._base_classes.items():
            if self.is_prefix(sub_class, path):
                for base in bases:
                    base_path = self.replace_prefix(
                        path=path,
                        old_prefix=sub_class,
                        new_prefix=self._imports.get(base, base))
                    definition = self._get_definition(base_path)
                    if definition:
                        return definition

    @staticmethod
    def replace_prefix(path, old_prefix, new_prefix):
        return new_prefix + path[len(old_prefix):]

    @staticmethod
    def is_prefix(prefix, path):
        return (
            len(prefix) < len(path) and
            all(a == b for a, b in zip(prefix, path)))


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


def position_from_node(source, node, column_offset=0, row_offset=0):
    return Position(
        source=source,
        row=(node.lineno - 1) + row_offset,
        column=node.col_offset + column_offset,
        node=node)
