from ast import Attribute, Name, NodeVisitor, Param, Store

from tests import make_source

from breakfast.position import Position


class NameSpace:

    def __init__(self, parent=None):
        self._parent = parent
        self._children = {}
        self.occurrences = []

    def add_name(self, name, position, is_definition=False, force=False):
        if name in self._children:
            self._children[name].add_occurrence(position)
        elif is_definition or force or self._parent is None:
            new = NameSpace(self)
            self._children[name] = new
            self._children[name].add_occurrence(position)
        else:
            return self._parent.add_name(name, position, is_definition)

        return self._children[name]

    def get_name(self, name):
        return self._children[name]

    def add_occurrence(self, position):
        self.occurrences.append(position)

    def find_occurrences(self, name, position):
        if name in self._children:
            child = self._children[name]
            if position in child.occurrences:
                return child.occurrences
        for child in self._children.values():
            occurrences = child.find_occurrences(name, position)
            if occurrences is not None:
                return occurrences


class NameVisitor(NodeVisitor):

    def __init__(self):
        self.current_source = None
        self.top = NameSpace()
        self.current = self.top

    def visit_source(self, source):
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def visit_Name(self, node):  # noqa
        position = self._position_from_node(node)
        self.current.add_name(
            node.id, position, is_definition=self._is_definition(node))

    def visit_FunctionDef(self, node):  # noqa
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("def "))
        old = self.current
        self.current = self.current.add_name(
            name=node.name,
            position=position,
            is_definition=True)
        self.generic_visit(node)
        self.current = old

    def visit_ClassDef(self, node):  # noqa
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("class "))
        old = self.current
        self.current = self.current.add_name(
            name=node.name,
            position=position,
            is_definition=True)
        self.generic_visit(node)
        self.current = old

    def visit_Attribute(self, node):  # noqa
        self.visit(node.value)
        old = self.current
        for name in self._names_from(node.value):
            self.current = self.current.get_name(name)
        name = node.attr
        start = self._position_from_node(node)
        position = self.current_source.find_after(name, start)
        self.current.add_name(
            name=name,
            position=position,
            is_definition=self._is_definition(node),
            force=True)
        self.current = old

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    def _position_from_node(self, node, row_offset=0, column_offset=0):
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset)

    def _names_from(self, node):
        if isinstance(node, Name):
            return (node.id,)

        if isinstance(node, Attribute):
            return self._names_from(node.value) + (node.attr,)

        return tuple()


def find_occurrences(*, source, old_name, position):
    visitor = NameVisitor()
    visitor.visit_source(source)
    return visitor.top.find_occurrences(old_name, position)


def rename(*, source, old_name, new_name, position):
    for occurrence in find_occurrences(source=source,
                                       old_name=old_name,
                                       position=position):
        source.replace(occurrence, old_name, new_name)
    return source.render()


def assert_renames(*, row, column, old_name, old_source, new_name, new_source):
    source = make_source(old_source)
    renamed = rename(
        source=source,
        old_name=old_name,
        new_name=new_name,
        position=Position(source, row, column))
    assert make_source(new_source).render() == renamed


def test_does_not_rename_random_attributes():
    assert_renames(
        row=3,
        column=0,
        old_name='path',
        old_source="""
        import os

        path = os.path.dirname(__file__)
        """,
        new_name='new_name',
        new_source="""
        import os

        new_name = os.path.dirname(__file__)
        """)


def test_finds_local_variable():
    assert_renames(
        row=2,
        column=4,
        old_name='old',
        old_source="""
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """,
        new_name='new',
        new_source="""
        def fun():
            new = 12
            old2 = 13
            result = new + old2
            del new
            return result

        old = 20
        """)


def test_finds_variable_in_closure():
    assert_renames(
        row=1,
        column=0,
        old_name='old',
        old_source="""
        old = 12

        def fun():
            result = old + 1
            return result

        old = 20
        """,
        new_name='new',
        new_source="""
        new = 12

        def fun():
            result = new + 1
            return result

        new = 20
        """)


def test_finds_method_names():
    assert_renames(
        row=3,
        column=8,
        old_name='old',
        old_source="""
        class A:

            def old(self):
                pass

        unbound = A.old
        """,
        new_name='new',
        new_source="""
        class A:

            def new(self):
                pass

        unbound = A.new
        """)
