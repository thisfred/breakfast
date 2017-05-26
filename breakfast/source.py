import re
from ast import Attribute, Call, Name, NodeVisitor, Param, Store, Tuple, parse
from collections import defaultdict
from contextlib import contextmanager
from functools import total_ordering

from breakfast.position import IllegalPosition, Position
from breakfast.rename import Rename


class Occurrences:
    def __init__(self, namespace):
        self.positions = []
        self.definitions = []
        self.namespace = namespace


class Tree:

    def __init__(self, class_name=None, base_classes=None):
        self.parent = None
        self.children = {}
        self.names = {}
        self.aliases = {}
        self.class_name = class_name
        self.base_classes = base_classes or []

    def add_namespace(self, name, inherit_namespace_from, child=None):
        child = child or Tree()
        if inherit_namespace_from is not None:
            child.names = inherit_namespace_from.names.copy()
        self.children[name] = child
        child.parent = self
        return child

    def add_function(self, name):
        return self.add_namespace(name=name, inherit_namespace_from=self)

    def add_method(self, name):
        return self.add_namespace(name=name, inherit_namespace_from=self.parent)

    def add_class(self, name, base_classes):
        return self.add_namespace(
            name=name,
            inherit_namespace_from=self,
            child=Tree(class_name=name, base_classes=base_classes))

    def add_definition(self, name, position):
        """Add point of definition of the name in the current namespace.

        If the name was not previously defined in this namespace, treat
        it as distinct from the same name in enclosing namespaces:

        in:
            def foo():
                a = 1
                a = 2

        we consider both `a`s to be the same variable.

        But in:

            a = 1
            def foo():
                a = 2

        we don't.
        """
        occurrences = self.names.get(name)
        if not occurrences or occurrences.namespace is not self:
            occurrences = Occurrences(self)
            self.names[name] = occurrences
        occurrences.positions.append(position)
        occurrences.definitions.append(position)

    def add_name(self, name, position):
        if name not in self.names:
            occurrences = Occurrences(self)
            self.names[name] = occurrences
        self.names[name].positions.append(position)

    def get_alias(self, name):
        return self.aliases.get(name, self.parent.get_alias(name))

    def get_occurrences(self, position):
        for occurrences in self.names.values():
            if any([p == position for p in occurrences.positions]):
                return occurrences.positions

        for child in self.children.values():
            positions = child.get_occurrences(position)
            if positions:
                return positions

    def walk(self):
        yield self
        for child in self.children.values():
            for node in child.walk():
                yield node

    def get_namespace(self, name):
        return self.children.get(name, self.parent.get_namespace(name))

    def add_alias(self, alias, name):
        self.aliases[alias] = name


class Root(Tree):

    def get_alias(self, name):
        return None

    def add_module(self, name):
        return self.add_namespace(
            name=name, child=Tree(), inherit_namespace_from=None)

    def get_namespace(self, name):
        return self


class Names(NodeVisitor):

    def __init__(self):
        self.root = Root()
        self.current_source = None
        self.current = self.root

    def visit_source(self, source):
        self.current_source = source
        self.visit(self.current_source.get_ast())
        self.current = self.root

    def get_occurrences(self, _, position):
        return sorted(self.root.get_occurrences(position))

    def visit_Module(self, node):  # noqa
        self.current = self.current.add_module(self.current_source.module_name)
        self.generic_visit(node)
        self.current = self.current.parent

    def visit_Name(self, node):  # noqa
        if self._is_definition(node):
            action = self.current.add_definition
        else:
            action = self.current.add_name
        position = self.position_from_node(node)
        name = node.id
        action(name, position=position)
        alias = self.current.get_alias(name)
        return self.current.get_namespace(alias or name)

    def visit_ClassDef(self, node):  # noqa
        position = self.position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("class "))
        self.current.add_definition(
            name=node.name,
            position=position)
        namespace = self.current.add_class(
            node.name, base_classes=[base.id for base in node.bases])
        with self.namespace(namespace):
            self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa
        position = self.position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("def "))
        self.current.add_definition(
            name=node.name,
            position=position)
        class_name = self.current.class_name
        with self.namespace(self.current.add_method(node.name)
                            if class_name is not None
                            else self.current.add_function(node.name)):
            for i, arg in enumerate(node.args.args):
                if i == 0 and class_name:
                    # this is 'self' in a method definition
                    if isinstance(arg, Name):
                        alias = arg.id
                    else:
                        alias = arg.arg
                    self.current.add_alias(alias, class_name)

                if isinstance(arg, Name):
                    # python 2: these will be caught by generic_visit
                    continue

                # python 3
                position = self.position_from_node(arg)
                self.current.add_definition(
                    name=arg.arg,
                    position=position)
            self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        ns = self.visit(node.func) or self.current
        with self.namespace(ns):
            start = self.position_from_node(node)
            for keyword in node.keywords:
                position = self.current_source.find_after(keyword.arg, start)
                self.current.add_name(
                    name=keyword.arg,
                    position=position)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Import(self, node):  # noqa
        start = self.position_from_node(node)
        for alias in node.names:
            name = alias.name
            self.current.add_namespace(
                name, inherit_namespace_from=self.current)
            position = self.current_source.find_after(name, start)
            self.current.add_name(name, position)

    def visit_ImportFrom(self, node):  # noqa
        start = self.position_from_node(node)
        for imported in node.names:
            name = imported.name
            position = self.current_source.find_after(name, start)
            self.current.names[name] = self.root.children[
                node.module].names[name]
            self.current.add_name(name, position)

    def visit_Attribute(self, node):  # noqa
        start = self.position_from_node(node)
        name = node.attr
        position = self.current_source.find_after(name, start)
        namespace = self.visit(node.value)
        if name not in namespace.names:
            for class_name in namespace.base_classes:
                base_occurrences = namespace.names.get(class_name)
                parent_space = base_occurrences.namespace
                base_space = parent_space.get_namespace(class_name)

                if name in base_space.names:
                    base_space.add_name(name, position)
                    return base_space.get_namespace(name)

        namespace.add_name(name, position)
        return namespace.get_namespace(name)

    def visit_Assign(self, node):  # noqa
        target_names = self.get_names(node.targets[0])
        value_names = self.get_names(node.value)
        for target, value in zip(target_names, value_names):
            if target and value:
                self.current.add_alias(target, value)
        self.generic_visit(node)

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
        ns = self.current.add_namespace(
            name, inherit_namespace_from=self.current)
        with self.namespace(ns):
            # visit the generators *before* the expression that uses the
            # generated values, to make sure the generator expression is
            # defined before use.
            for generator in node.generators:
                self.visit(generator)
            for sub_node in rest:
                self.visit(sub_node)

    @contextmanager
    def namespace(self, namespace):
        previous = self.current
        self.current = namespace
        yield
        self.current = previous

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
        if isinstance(value, Tuple):
            return [self.get_value_name(v) for v in value.elts]

        if isinstance(value, Attribute):
            return value.attr

        if isinstance(value, Name):
            return value.id

        if isinstance(value, Call):
            return self.get_value_name(value.func)

        return None


@total_ordering
class Source(object):

    word = re.compile(r'\w+|\W+')

    def __init__(self, lines, module_name='module', file_name=None):
        self.lines = lines
        self.changes = {}  # type: Dict[int, str]
        self.module_name = module_name
        self.file_name = file_name

    def __hash__(self):
        return hash(self.module_name)

    def rename(self, row, column, new_name, additional_sources=None):
        position = Position(self, row=row, column=column)
        old_name = position.get_name()
        refactoring = Rename(
            name=old_name,
            position=position,
            new_name=new_name)
        for source in additional_sources or []:
            refactoring.add_source(source)
        refactoring.apply()

    def get_name_at(self, position):
        return self.word.search(self.get_string_starting_at(position)).group()

    def get_ast(self):
        return parse('\n'.join(self.lines))

    def render(self):
        return '\n'.join(
            self.changes.get(i, line)
            for i, line in enumerate(self.lines))

    def get_changes(self):
        for change in sorted(self.changes.items()):
            yield change

    def replace(self, position, old, new):
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start, end, new):
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[:start.column] + new + line[end.column:]
        self.changes[line_number] = modified_line

    def find_before(self, name, start):
        while not self.get_string_starting_at(start).startswith(name):
            try:
                start = start - 1
            except IllegalPosition:
                start = self.get_last_column(start.row - 1)

        return start

    def find_after(self, name, start):
        while not self.get_string_starting_at(start).startswith(name):
            start = start + 1
            if len(self.lines[start.row]) < start.column:
                start = start.copy(row=start.row + 1, column=0)

        return start

    def get_string_starting_at(self, position):
        return self.lines[position.row][position.column:]

    def get_last_column(self, row):
        return Position(
            source=self, row=row, column=len(self.lines[row]) - 1)

    def __eq__(self, other):
        return self is other

    def __lt__(self, other):
        return self.module_name < other.module_name

    def __gt__(self, other):
        return other.module_name < self.module_name
