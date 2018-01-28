from ast import Attribute, Call, Name, NodeVisitor, Param, Store

from tests import make_source

from breakfast.position import Position


class NameSpace:

    def __init__(self, parent=None, is_class=False, cls=None):
        self._parent = parent
        self._children = {}
        self.occurrences = []
        self._is_class = is_class
        self._enclosing_class = cls
        self.points_to = None
        self._aliases = []

    def add_name(self, name, position, force=False):
        return self._add_name(name, position, force=force)

    def add_definition(self, name, position):
        return self._add_name(name, position, force=True)

    def add_function_definition(self, name, position):
        return self._add_name(
            name,
            position,
            force=True,
            cls=self if self._is_class else None)

    def add_static_method(self, name, position):
        return self._add_name(name, position, force=True)

    def add_class_definition(self, name, position):
        return self._add_name(name, position, force=True, is_class=True)

    def add_parameter(self, name, number, position):
        parameter = self._add_name(name, position, force=True)
        if number == 0 and self._enclosing_class:
            self._enclosing_class.add_alias(parameter)
        return parameter

    def add_alias(self, alias):
        self._aliases.append(alias)
        alias.set_points_to(self)

    def set_points_to(self, cls):
        self.points_to = cls

    def get_namespace(self, name):
        ns = self._children[name]
        while ns.points_to:
            ns = ns.points_to
        return ns

    def find_occurrences(self, name, position):
        if name in self._children:
            child = self._children[name]
            if position in child.occurrences:
                return child.occurrences

        for child in self._children.values():
            occurrences = child.find_occurrences(name, position)
            if occurrences is not None:
                return occurrences

        return []

    def add_occurrence(self, position):
        self.occurrences.append(position)

    def _add_name(self, name, position, force, is_class=False, cls=None):
        if name in self._children:
            self._children[name].add_occurrence(position)
        elif force or self._parent is None:
            new = NameSpace(self, is_class=is_class, cls=cls)
            self._children[name] = new
            self._children[name].add_occurrence(position)
        else:
            return self._parent._add_name(
                name,
                position,
                force=force,
                is_class=is_class,
                cls=cls)

        return self._children[name]


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
        if self._is_definition(node):
            self.current.add_definition(node.id, position)
        else:
            self.current.add_name(node.id, position)

    def visit_FunctionDef(self, node):  # noqa
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("def "))
        old = self.current
        if self._is_staticmethod(node):
            self.current = self.current.add_static_method(
                name=node.name,
                position=position)
        else:
            self.current = self.current.add_function_definition(
                name=node.name,
                position=position)
        for i, arg in enumerate(node.args.args):
            position = self._position_from_node(arg)
            self.current.add_parameter(
                name=arg.arg,
                number=i,
                position=position)
            # if i == 0 and in_method and not is_static:
            #     self._add_class_alias(arg)
        self.generic_visit(node)
        self.current = old

    def visit_ClassDef(self, node):  # noqa
        position = self._position_from_node(
            node=node,
            row_offset=len(node.decorator_list),
            column_offset=len("class "))
        old = self.current
        self.current = self.current.add_class_definition(
            name=node.name,
            position=position)
        self.generic_visit(node)
        self.current = old

    def visit_Attribute(self, node):  # noqa
        self.visit(node.value)
        old = self.current
        for name in self._names_from(node.value):
            self.current = self.current.get_namespace(name)
        name = node.attr
        start = self._position_from_node(node)
        position = self.current_source.find_after(name, start)
        if self._is_definition(node):
            self.current.add_definition(
                name=name,
                position=position)
        else:
            self.current.add_name(
                name=name,
                position=position,
                force=True)
        self.current = old

    def visit_Call(self, node):  # noqa
        self.visit(node.func)
        old = self.current
        for name in self._names_from(node.func):
            self.current = self.current.get_namespace(name)
        start = self._position_from_node(node)
        for keyword in node.keywords:
            position = self.current_source.find_after(keyword.arg, start)
            self.current.add_name(
                name=keyword.arg,
                position=position)
        self.current = old
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Assign(self, node):  # noqa
        self.generic_visit(node)
        target = self.current
        for name in self._names_from(node.targets[0]):
            target = target.get_namespace(name)
        value = self.current
        for name in self._names_from(node.value):
            value = value.get_namespace(name)
        value.add_alias(target)

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    @staticmethod
    def _is_staticmethod(node):
        return any(n.id == 'staticmethod' for n in node.decorator_list)

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

        if isinstance(node, Call):
            return self._names_from(node.func)

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


def test_finds_parameters():
    assert_renames(
        row=1,
        column=8,
        old_name='arg',
        old_source="""
        def fun(arg, arg2):
            return arg + arg2

        fun(arg=1, arg2=2)
        """,
        new_name='new',
        new_source="""
        def fun(new, arg2):
            return new + arg2

        fun(new=1, arg2=2)
        """)


def test_finds_function():
    assert_renames(
        row=1,
        column=4,
        old_name='fun_old',
        old_source="""
        def fun_old():
            return 'result'

        result = fun_old()
        """,
        new_name='fun_new',
        new_source="""
        def fun_new():
            return 'result'

        result = fun_new()
        """)


def test_finds_class():
    assert_renames(
        row=1,
        column=6,
        old_name='OldClass',
        old_source="""
        class OldClass:
            pass

        instance = OldClass()
        """,
        new_name='NewClass',
        new_source="""
        class NewClass:
            pass

        instance = NewClass()
        """)


def test_finds_passed_argument():
    assert_renames(
        row=1,
        column=0,
        old_name='old',
        old_source="""
        old = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, old)
        """,
        new_name='new',
        new_source="""
        new = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, new)
        """)


def test_does_not_find_method_of_unrelated_class():
    assert_renames(
        row=3,
        column=8,
        old_name='old',
        new_name='new',
        old_source="""
        class ClassThatShouldHaveMethodRenamed:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.old()
        b = UnrelatedClass()
        b.old()
        """,
        new_source="""
        class ClassThatShouldHaveMethodRenamed:

            def new(self, arg):
                pass

            def foo(self):
                self.new('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.new()
        b = UnrelatedClass()
        b.old()
        """)


# TODO: rename parameter default value
