from ast import Attribute, Call, Name, NodeVisitor, Param, Store, Tuple
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


class Collector:

    def __init__(self):
        self._occurrences = defaultdict(list)
        self._definitions = defaultdict(list)
        self._class_aliases = {}
        self._current_namespace = tuple()
        self._aliases = {}
        self._classes = set()
        self._superclasses = defaultdict(list)
        self._imports = {}
        self._scopes = set()
        self._method_scopes = set()

    def get_positions(self, position):
        self.post_process()
        for name, positions in self._occurrences.items():
            if (name in self._definitions and
                    any(position == p for p in positions)):
                return sorted(positions)

        return []

    def rewrite_selves(self):
        for name in self._occurrences.copy():
            for alias in self._class_aliases:
                if is_prefix(alias, name):
                    new_name = (self._class_aliases[alias] + name[len(alias):])
                    self._occurrences[new_name].extend(self._occurrences[name])
                    del self._occurrences[name]
                    if name in self._definitions:
                        self._definitions[new_name].extend(
                            self._definitions[name])
                        del self._definitions[name]

    def post_process(self):
        self.rewrite_selves()
        for name in self._occurrences.copy():

            if name not in self._definitions:
                rewritten = self.rewrite(name)
                if rewritten in self._definitions:
                    self._occurrences[rewritten] += self._occurrences[name]
                else:
                    definition = self.get_from_outer_scope(name)
                    if definition and definition in self._definitions:
                        self._occurrences[
                            definition] += self._occurrences[name]

                del self._occurrences[name]

    def get_from_outer_scope(self, name):
        for scope in self._scopes:
            if is_prefix(scope, name):
                return scope[:-1] + name[len(scope):]

    def enter_namespace(self, name):
        self._current_namespace = self.get_namespaced(name)

    def leave_namespace(self):
        self._current_namespace = self._current_namespace[:-1]

    def add_scope(self, name, is_method=False):
        if is_method:
            self._method_scopes.add(self.get_namespaced(name))
        else:
            self._scopes.add(self.get_namespaced(name))

    def add_definition(self, name, position):
        full_name = self.get_namespaced(name)
        self._definitions[full_name].append(position)
        self._add_namespaced_occurrence(full_name, position)

    def add_occurrence(self, name, position):
        full_name = self.get_namespaced(name)
        self._add_namespaced_occurrence(full_name, position)

    def get_namespaced(self, name):
        return self._current_namespace + (name,)

    def add_alias(self, alias, name):
        self._aliases[self.get_namespaced(name)] = self.get_namespaced(alias)

    def rewrite(self, full_name):
        if full_name in self._imports:
            return self._imports[full_name]

        prefix = full_name[:-1]
        while prefix in self._aliases:
            full_name = self._aliases[prefix] + full_name[-1:]
            prefix = full_name[:-1]

        if (full_name not in self._definitions and
                full_name[:-1] in self._classes):
            inherited = self.get_inherited_definition(full_name)
            if inherited:
                return inherited

        return full_name

    def add_superclass(self, subclass, superclass):
        superclass = self.get_namespaced(superclass)
        if superclass in self._imports:
            superclass = self._imports[superclass]
        self._superclasses[self.get_namespaced(subclass)].append(superclass)

    def get_inherited_definition(self, full_name):
        cls = full_name[:-1]
        if cls not in self._superclasses:
            return

        supers = self._superclasses[cls]
        seen = set()
        name = full_name[-1]
        while supers:
            cls = supers[0]
            supers = supers[1:]
            if cls in seen:
                continue

            seen.add(cls)
            supername = cls + (name,)
            if supername in self._definitions:
                return supername

            if cls not in self._superclasses:
                continue

            new_supers = self._superclasses[cls]
            supers.extend(new_supers)

    def _add_namespaced_occurrence(self, full_name, position):
        self._occurrences[full_name].append(position)

    def add_class_alias(self, alias):
        full_name = self.get_namespaced(alias)
        self._class_aliases[full_name] = full_name[:-2]

    def add_import(self, name, full_name):
        self._imports[self.get_namespaced(name)] = full_name

    def add_class(self, name):
        self._classes.add(self.get_namespaced(name))


class Names(NodeVisitor):

    def __init__(self):
        self.current_source = None
        self.collector = Collector()
        self._class_name = None

    def visit_source(self, source):
        self.current_source = source
        self.visit(self.current_source.get_ast())

    def get_occurrences(self, _, position):
        return self.collector.get_positions(position)

    def visit_Module(self, node):  # noqa
        with self.namespace(self.current_source.module_name):
            self.generic_visit(node)

    def add_occurrence(self, name, position):
        self.collector.add_occurrence(name, position)

    def add_definition(self, name, position):
        self.collector.add_definition(name, position)

    def add_alias(self, alias, name):
        self.collector.add_alias(alias, name)

    @contextmanager
    def namespace(self, name):
        self.collector.enter_namespace(name)
        yield
        self.collector.leave_namespace()

    @contextmanager
    def enter_class(self, name):
        old = self._class_name
        self._class_name = name
        with self.namespace(name):
            yield
        self._class_name = old

    @contextmanager
    def enter_function(self, name, is_method):
        self.collector.add_scope(name, is_method)
        with self.namespace(name):
            old = self._class_name
            self._class_name = None
            yield
        self._class_name = old

    def visit_Name(self, node):  # noqa
        if self._is_definition(node):
            action = self.add_definition
        else:
            action = self.add_occurrence
        position = self.position_from_node(node)
        name = node.id
        action(name, position=position)

    def visit_ClassDef(self, node):  # noqa
        position = self.position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("class "))
        self.collector.add_class(node.name)
        self.add_definition(
            name=node.name,
            position=position)
        for base in node.bases:
            self.collector.add_superclass(node.name, base.id)
            self.visit(base)
        with self.enter_class(node.name):
            for statement in node.body:
                self.visit(statement)

    def visit_FunctionDef(self, node):  # noqa
        position = self.position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("def "))
        self.add_definition(
            name=node.name,
            position=position)
        class_name = self._class_name
        with self.enter_function(node.name,
                                 is_method=self._class_name is not None):
            for i, arg in enumerate(node.args.args):
                if i == 0 and class_name:
                    # this is 'self' in a method definition
                    if isinstance(arg, Name):
                        # python 2
                        alias = arg.id
                    else:
                        alias = arg.arg
                    self.collector.add_class_alias(alias)

                if isinstance(arg, Name):
                    # python 2: these will be caught by generic_visit
                    continue

                # python 3
                position = self.position_from_node(arg)
                self.add_definition(
                    name=arg.arg,
                    position=position)
            self.generic_visit(node)

    def names_from(self, node):
        if isinstance(node, Name):
            return (node.id,)

        if isinstance(node, Attribute):
            return self.names_from(node.value) + (node.attr,)

    def visit_Call(self, node):  # noqa
        start = self.position_from_node(node)
        self.visit(node.func)
        names = self.names_from(node.func)
        for name in names:
            position = self.current_source.find_after(name, start)
            self.collector.enter_namespace(name)
        for keyword in node.keywords:
            position = self.current_source.find_after(keyword.arg, start)
            self.collector.add_occurrence(
                name=keyword.arg,
                position=position)
        for _ in names:
            self.collector.leave_namespace()
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Import(self, node):  # noqa
        start = self.position_from_node(node)
        for alias in node.names:
            name = alias.name
            position = self.current_source.find_after(name, start)
            self.add_occurrence(name, position)

    def visit_ImportFrom(self, node):  # noqa
        start = self.position_from_node(node)
        for imported in node.names:
            name = imported.name
            full_name = (node.module, name)
            self.collector.add_import(name, full_name)
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)

    def visit_Attribute(self, node):  # noqa
        start = self.position_from_node(node)
        self.visit(node.value)
        names = self.names_from(node.value)
        for name in names:
            self.collector.enter_namespace(name)
        name = node.attr
        position = self.current_source.find_after(name, start)
        if self._is_definition(node):
            self.add_definition(name, position)
        else:
            self.collector.add_occurrence(name, position)
        for _ in names:
            self.collector.leave_namespace()
        # if name not in namespace.names:
        #     for class_name in namespace.base_classes:
        #         base_occurrences = namespace.names.get(class_name)
        #         parent_space = base_occurrences.namespace
        #         base_space = parent_space.get_namespace(class_name)

        #         if name in base_space.names:
        #             base_space.add_name(name, position)
        #             return base_space.get_namespace(name)

        # if self._is_definition(node):
        #     namespace.add_definition(name, position)
        # else:
        #     namespace.add_name(name, position)
        # return namespace.get_namespace(name)

    def visit_Assign(self, node):  # noqa
        target_names = self.get_names(node.targets[0])
        value_names = self.get_names(node.value)
        self.generic_visit(node)
        for target, value in zip(target_names, value_names):
            if target and value:
                self.add_alias(value, target)

    def visit_DictComp(self, node):  # noqa
        self._comp_visit(node, node.key, node.value)

    def visit_SetComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def visit_ListComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def _comp_visit(self, node, *rest):
        position = self.position_from_node(node)
        # The dashes make sure it can never clash with an actual Python name.
        name = 'comprehension-%s-%s' % (position.row, position.column)
        # ns = self.current.add_namespace(
        #     name, inherit_namespace_from=self.current)
        self.collector.add_scope(name)
        with self.namespace(name):
            # visit the generators *before* the expression that uses the
            # generated values, to make sure the generator expression is
            # defined before use.
            for generator in node.generators:
                self.visit(generator)
            for sub_node in rest:
                self.visit(sub_node)

    def position_from_node(self, node, row_offset=0, column_offset=0):
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset)

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    def get_names(self, value):
        if isinstance(value, Tuple):
            return [self.get_value_name(v) for v in value.elts]

        return [self.get_value_name(value)]

    def get_value_name(self, value):
        if isinstance(value, Attribute):
            return value.attr

        if isinstance(value, Name):
            return value.id

        if isinstance(value, Call):
            return self.get_value_name(value.func)

        return None


def is_prefix(prefix, full_name):
    if len(full_name) <= len(prefix):
        return False

    return full_name[:len(prefix)] == prefix
