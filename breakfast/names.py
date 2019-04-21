from ast import Attribute, Call, Name, NodeVisitor, Param, Store, Subscript, Tuple
from collections import defaultdict
from contextlib import contextmanager

from breakfast.position import Position


class Collector:
    def __init__(self):
        self.in_method = False
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
        self._in_class = False
        self._rewrites = {}

    @contextmanager
    def namespaces(self, names):
        for name in names:
            self._enter_namespace(name)
        yield
        for _ in names:
            self._leave_namespace()

    @contextmanager
    def enter_class(self, name):
        old = self._in_class
        self._in_class = True
        with self.namespace(name):
            yield
        self._in_class = old

    @contextmanager
    def enter_function(self, name):
        old_in_method = self.in_method
        self.in_method = self._in_class
        self.add_scope(name, self.in_method)
        with self.namespace(name):
            old_in_class = self._in_class
            self._in_class = False
            yield
        self._in_class = old_in_class
        self.in_method = old_in_method

    @contextmanager
    def namespace(self, name):
        self._enter_namespace(name)
        yield
        self._leave_namespace()

    def get_positions(self, position):
        self._post_process()
        for name, positions in self._occurrences.items():
            if name in self._definitions and any(position == p for p in positions):
                return sorted(positions)
        return []

    def add_global(self, name):
        full_name = self.get_namespaced(name)
        self._rewrites[full_name] = full_name[:1] + full_name[-1:]

    def add_nonlocal(self, name):
        full_name = self.get_namespaced(name)
        self._rewrites[full_name] = full_name[:-2] + full_name[-1:]

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

    def add_superclass(self, subclass, superclass):
        superclass = self.get_namespaced(superclass)
        if superclass in self._imports:
            superclass = self._imports[superclass]
        self._superclasses[self.get_namespaced(subclass)].append(superclass)

    def add_class_alias(self, alias):
        full_name = self.get_namespaced(alias)
        self._class_aliases[full_name] = full_name[:-2]

    def add_import(self, name, full_name):
        self._imports[self.get_namespaced(name)] = full_name

    def add_class(self, name):
        self._classes.add(self.get_namespaced(name))

    def _post_process(self):
        self._rewrite_selves()
        for name in self._occurrences.copy():
            if name in self._rewrites:
                new_name = self._rewrites[name]
                self._occurrences[new_name] += self._occurrences[name]
                del self._occurrences[name]
                continue

            if name not in self._definitions:
                rewritten = self._rewrite(name)
                if rewritten in self._definitions:
                    self._occurrences[rewritten] += self._occurrences[name]
                else:
                    definition = self._get_from_outer_scope(name)
                    if definition and definition in self._definitions:
                        self._occurrences[definition] += self._occurrences[name]
                del self._occurrences[name]

    def _get_from_outer_scope(self, name):
        for scope in self._scopes:
            if is_prefix(scope, name):
                return scope[:-1] + name[len(scope) :]

        for scope in self._method_scopes:
            if is_prefix(scope, name):
                return scope[:-2] + name[len(scope) :]

    def _enter_namespace(self, name):
        self._current_namespace = self.get_namespaced(name)

    def _leave_namespace(self):
        self._current_namespace = self._current_namespace[:-1]

    def _add_namespaced_occurrence(self, full_name, position):
        self._occurrences[full_name].append(position)

    def _rewrite(self, full_name):
        if full_name in self._imports:
            return self._imports[full_name]

        prefix = full_name[:-1]
        while prefix in self._aliases:
            full_name = self._aliases[prefix] + full_name[-1:]
            prefix = full_name[:-1]

        if full_name not in self._definitions and full_name[:-1] in self._classes:
            inherited = self._get_inherited_definition(full_name)
            if inherited:
                return inherited

        return full_name

    def _rewrite_selves(self):
        for name in self._occurrences.copy():
            for alias in self._class_aliases:
                if is_prefix(alias, name):
                    new_name = self._class_aliases[alias] + name[len(alias) :]
                    self._occurrences[new_name].extend(self._occurrences[name])
                    del self._occurrences[name]
                    if name in self._definitions:
                        self._definitions[new_name].extend(self._definitions[name])
                        del self._definitions[name]

    def _get_inherited_definition(self, full_name):
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


class Names(NodeVisitor):
    def __init__(self):
        self.current_source = None
        self.collector = Collector()

    def visit_source(self, source):
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def get_occurrences(self, _, position):
        return self.collector.get_positions(position)

    def visit_Module(self, node):  # noqa
        with self.collector.namespace(self.current_source.module_name):
            self.generic_visit(node)

    def visit_Name(self, node):  # noqa
        if self._is_definition(node):
            action = self.collector.add_definition
        else:
            action = self.collector.add_occurrence
        position = self._position_from_node(node)
        name = node.id
        action(name, position=position)

    def visit_ClassDef(self, node):  # noqa
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("class ")
        )
        self.collector.add_class(node.name)
        self.collector.add_definition(name=node.name, position=position)
        for base in node.bases:
            self.collector.add_superclass(node.name, base.id)
            self.visit(base)
        with self.collector.enter_class(node.name):
            for statement in node.body:
                self.visit(statement)

    def visit_FunctionDef(self, node):  # noqa
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("def ")
        )
        self.collector.add_definition(name=node.name, position=position)
        is_static = is_staticmethod(node)
        with self.collector.enter_function(node.name):
            for i, arg in enumerate(node.args.args):
                if i == 0 and self.collector.in_method and not is_static:
                    # this is 'self' in a method definition
                    self._add_self(arg)
                self._add_parameter(arg)
            self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        start = self._position_from_node(node)
        self.visit(node.func)
        names = self._names_from(node.func)
        with self.collector.namespaces(names):
            for keyword in node.keywords:
                position = self.current_source.find_after(keyword.arg, start)
                self.collector.add_occurrence(name=keyword.arg, position=position)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Import(self, node):  # noqa
        start = self._position_from_node(node)
        for alias in node.names:
            name = alias.name
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)

    def visit_ImportFrom(self, node):  # noqa
        start = self._position_from_node(node)
        for imported in node.names:
            name = imported.name
            alias = imported.asname
            full_name = (node.module, name)
            self.collector.add_import(alias or name, full_name)
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            if alias:
                position = self.current_source.find_after(alias, start)
                self.collector.add_definition(alias, position)

    def visit_Attribute(self, node):  # noqa
        start = self._position_from_node(node)
        self.visit(node.value)
        names = self._names_from(node.value)
        with self.collector.namespaces(names):
            name = node.attr
            position = self.current_source.find_after(name, start)
            if self._is_definition(node):
                self.collector.add_definition(name, position)
            else:
                self.collector.add_occurrence(name, position)

    def visit_Assign(self, node):  # noqa
        target_names = self._get_names(node.targets[0])
        value_names = self._get_names(node.value)
        self.generic_visit(node)
        for target, value in zip(target_names, value_names):
            if target and value:
                self.collector.add_alias(value, target)

    def visit_DictComp(self, node):  # noqa
        self._comp_visit(node, node.key, node.value)

    def visit_SetComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def visit_ListComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def visit_Global(self, node):  # noqa
        start = self._position_from_node(node)
        for name in node.names:
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            self.collector.add_global(name)

        self.generic_visit(node)

    def visit_Nonlocal(self, node):  # noqa
        start = self._position_from_node(node)
        for name in node.names:
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            self.collector.add_nonlocal(name)

    def _comp_visit(self, node, *rest):
        position = self._position_from_node(node)
        # The dashes make sure it can never clash with an actual Python name.
        name = "comprehension-%s-%s" % (position.row, position.column)
        # ns = self.current.add_namespace(
        #     name, inherit_namespace_from=self.current)
        self.collector.add_scope(name)
        with self.collector.namespace(name):
            # visit the generators *before* the expression that uses the
            # generated values, to make sure the generator expression is
            # defined before use.
            for generator in node.generators:
                self.visit(generator)
            for sub_node in rest:
                self.visit(sub_node)

    def _add_self(self, arg):
        if isinstance(arg, Name):
            # python 2
            alias = arg.id
        else:
            alias = arg.arg
        self.collector.add_class_alias(alias)

    def _add_parameter(self, arg):
        if isinstance(arg, Name):
            # python 2: these will be caught by generic_visit
            return

        # python 3
        position = self._position_from_node(arg)
        self.collector.add_definition(name=arg.arg, position=position)

    def _names_from(self, node):
        if isinstance(node, Name):
            return (node.id,)

        if isinstance(node, Attribute):
            return self._names_from(node.value) + (node.attr,)

        if isinstance(node, Subscript):
            return self._names_from(node.value)

        return tuple()

    def _position_from_node(self, node, row_offset=0, column_offset=0):
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
        )

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    def _get_names(self, value):
        if isinstance(value, Tuple):
            return [self._get_value_name(v) for v in value.elts]

        return [self._get_value_name(value)]

    def _get_value_name(self, value):
        if isinstance(value, Attribute):
            return value.attr

        if isinstance(value, Name):
            return value.id

        if isinstance(value, Call):
            return self._get_value_name(value.func)

        return None


def is_staticmethod(node):
    return any(n.id == "staticmethod" for n in node.decorator_list)


def is_prefix(prefix, full_name):
    if len(full_name) <= len(prefix):
        return False

    return full_name[: len(prefix)] == prefix
