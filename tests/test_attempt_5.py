from ast import Attribute, Call, Name, NodeVisitor, Param, Store, Tuple

from breakfast.position import Position
from tests import make_source


class NameSpace:
    def __init__(self, parent=None, is_class=False, cls=None):
        self._parent = parent
        self._children = {}
        self.occurrences = []
        self._is_class = is_class
        self._enclosing_class = cls
        self.points_to = None
        self._aliases = []

    def add_module(self, name):
        new = NameSpace(self)
        self._children[name] = new
        return self._children[name]

    def add_occurrence(self, name, position, force=False):
        return self.add_name(name, position, force=force)

    def add_definition(self, name, position):
        return self.add_name(name, position, force=True)

    def add_function_definition(self, name, position):
        return self.add_name(
            name, position, force=True, cls=self if self._is_class else None
        )

    def add_static_method(self, name, position):
        return self.add_name(name, position, force=True)

    def add_class_definition(self, name, position):
        return self.add_name(name, position, force=True, is_class=True)

    def add_parameter(self, name, number, position):
        parameter = self.add_name(name, position, force=True)
        if number == 0 and self._enclosing_class:
            self._enclosing_class.add_alias(parameter)
        return parameter

    def add_alias(self, alias_namespace):
        self._aliases.append(alias_namespace)
        alias_namespace.set_points_to(self)

    def set_points_to(self, cls):
        self.points_to = cls

    def get_namespace(self, name):
        namespace = self._children.get(name)
        if namespace is None:
            namespace = self._parent.get_namespace(name)
        while namespace.points_to:
            namespace = namespace.points_to
        return namespace

    def find_occurrences(self, name, position):
        if name in self._children:
            child = self._children[name]
            if position in child.occurrences:
                return child.occurrences

        for child in self._children.values():
            occurrences = child.find_occurrences(name, position)
            if occurrences:
                return occurrences

        return []

    def _add_child(self, name, position, is_class, cls):
        new = NameSpace(self, is_class=is_class, cls=cls)
        self.set_namespace(name, new)
        self._add_child_occurrence(name, position)

    def set_namespace(self, name, namespace):
        self._children[name] = namespace

    def add_name(self, name, position, force, is_class=False, cls=None):
        if name in self._children:
            self._add_child_occurrence(name, position)
        elif force or self._parent is None:
            self._add_child(name, position, is_class=is_class, cls=cls)
        else:
            enclosing_scope = self._parent
            # method bodies have no direct access to class namespace
            if enclosing_scope._is_class:
                enclosing_scope = enclosing_scope._parent
            return enclosing_scope.add_name(
                name, position, force=force, is_class=is_class, cls=cls
            )

        return self._children[name]

    def _add_child_occurrence(self, name, position):
        self._children[name].occurrences.append(position)


class NameVisitor(NodeVisitor):
    def __init__(self):
        self.current_source = None
        self.top = NameSpace()
        self.current = self.top

    def visit_source(self, source):
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def visit_Module(self, node):  # noqa
        old = self.current
        self.current = self.current.add_module(self.current_source.module_name)
        self.generic_visit(node)
        self.current = old

    def visit_ImportFrom(self, node):  # noqa
        start = self._position_from_node(node)
        import_path = node.module.split(".")
        import_namespace = self.top
        for path in import_path:
            import_namespace = import_namespace.get_namespace(path)
        for imported in node.names:
            name = imported.name
            position = self.current_source.find_after(name, start)
            original = import_namespace.add_occurrence(name, position, force=True)
            self.current.set_namespace(name, original)
            alias = imported.asname
            if alias:
                alias_position = self.current_source.find_after(alias, start)
                alias_namespace = self.current.add_definition(alias, alias_position)
                original.add_alias(alias_namespace)
                self.current.add_definition(alias, alias_position)

    def visit_Name(self, node):  # noqa
        position = self._position_from_node(node)
        if self._is_definition(node):
            self.current.add_definition(node.id, position)
        else:
            self.current.add_occurrence(node.id, position)

    def visit_FunctionDef(self, node):  # noqa
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("def ")
        )
        old = self.current
        if self._is_staticmethod(node):
            self.current = self.current.add_static_method(
                name=node.name, position=position
            )
        else:
            self.current = self.current.add_function_definition(
                name=node.name, position=position
            )
        for i, arg in enumerate(node.args.args):
            position = self._position_from_node(arg)
            self.current.add_parameter(name=arg.arg, number=i, position=position)
            # if i == 0 and in_method and not is_static:
            #     self._add_class_alias(arg)
        self.generic_visit(node)
        self.current = old

    def visit_ClassDef(self, node):  # noqa
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("class ")
        )
        old = self.current
        self.current = self.current.add_class_definition(
            name=node.name, position=position
        )
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
            self.current.add_definition(name=name, position=position)
        else:
            self.current.add_occurrence(name=name, position=position, force=True)
        self.current = old

    def visit_Call(self, node):  # noqa
        self.visit(node.func)
        old = self.current
        for name in self._names_from(node.func):
            self.current = self.current.get_namespace(name)
        start = self._position_from_node(node)
        for keyword in node.keywords:
            position = self.current_source.find_after(keyword.arg, start)
            self.current.add_occurrence(name=keyword.arg, position=position)
        self.current = old
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Assign(self, node):  # noqa
        self.generic_visit(node)
        if isinstance(node.value, Tuple):
            # multiple assignment
            values = [v for v in node.value.elts]
            targets = [t for t in node.targets[0].elts]
        else:
            values = [node.value]
            targets = [node.targets[0]]
        for target, value in zip(targets, values):
            target_ns = self.current
            for name in self._names_from(target):
                target_ns = target_ns.get_namespace(name)
            value_ns = self.current
            for name in self._names_from(value):
                value_ns = value_ns.get_namespace(name)
            value_ns.add_alias(target_ns)

    def visit_DictComp(self, node):  # noqa
        self._comp_visit(node, node.key, node.value)

    def visit_SetComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def visit_ListComp(self, node):  # noqa
        self._comp_visit(node, node.elt)

    def _comp_visit(self, node, *rest):
        position = self._position_from_node(node)
        # Invent a name for the ad hoc scope. The dashes make sure it can
        # never clash with an actual Python name.
        name = "comprehension-%s-%s" % (position.row, position.column)
        old = self.current
        self.current = self.current.add_definition(name=name, position=position)
        for generator in node.generators:
            self.visit(generator)
        for sub_node in rest:
            self.visit(sub_node)
        self.current = old

    @staticmethod
    def _is_definition(node):
        return isinstance(node.ctx, (Param, Store))

    @staticmethod
    def _is_staticmethod(node):
        return any(n.id == "staticmethod" for n in node.decorator_list)

    def _position_from_node(self, node, row_offset=0, column_offset=0):
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
        )

    def _names_from(self, node):
        if isinstance(node, Name):
            return (node.id,)

        if isinstance(node, Attribute):
            return self._names_from(node.value) + (node.attr,)

        if isinstance(node, Call):
            return self._names_from(node.func)

        return tuple()


def find_occurrences(sources, old_name, position):
    visitor = NameVisitor()
    for source in sources:
        visitor.visit_source(source)
    return visitor.top.find_occurrences(old_name, position)


def rename(sources, old_name, new_name, position):
    for occurrence in find_occurrences(
        sources=sources, old_name=old_name, position=position
    ):
        occurrence.source.replace(occurrence, old_name, new_name)
    return sources


def assert_renames(row, column, old_name, old_source, new_name, new_source):
    source = make_source(old_source)
    renamed = rename(
        sources=[source],
        old_name=old_name,
        new_name=new_name,
        position=Position(source, row, column),
    )
    assert make_source(new_source).render() == renamed[0].render()


def assert_renames_multi_source(position, old_name, old_sources, new_name, new_sources):
    renamed = rename(
        sources=old_sources, old_name=old_name, new_name=new_name, position=position
    )
    for actual, expected in zip(renamed, new_sources):
        assert make_source(expected).render() == actual.render()


def test_does_not_rename_random_attributes():
    assert_renames(
        row=3,
        column=0,
        old_name="path",
        old_source="""
        import os

        path = os.path.dirname(__file__)
        """,
        new_name="new_name",
        new_source="""
        import os

        new_name = os.path.dirname(__file__)
        """,
    )


def test_finds_local_variable():
    assert_renames(
        row=2,
        column=4,
        old_name="old",
        old_source="""
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
        """,
        new_name="new",
        new_source="""
        def fun():
            new = 12
            old2 = 13
            result = new + old2
            del new
            return result

        old = 20
        """,
    )


def test_finds_variable_in_closure():
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 12

        def fun():
            result = old + 1
            return result

        old = 20
        """,
        new_name="new",
        new_source="""
        new = 12

        def fun():
            result = new + 1
            return result

        new = 20
        """,
    )


def test_finds_method_names():
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        old_source="""
        class A:

            def old(self):
                pass

        unbound = A.old
        """,
        new_name="new",
        new_source="""
        class A:

            def new(self):
                pass

        unbound = A.new
        """,
    )


def test_finds_parameters():
    assert_renames(
        row=1,
        column=8,
        old_name="arg",
        old_source="""
        def fun(arg, arg2):
            return arg + arg2

        fun(arg=1, arg2=2)
        """,
        new_name="new",
        new_source="""
        def fun(new, arg2):
            return new + arg2

        fun(new=1, arg2=2)
        """,
    )


def test_finds_function():
    assert_renames(
        row=1,
        column=4,
        old_name="fun_old",
        old_source="""
        def fun_old():
            return 'result'

        result = fun_old()
        """,
        new_name="fun_new",
        new_source="""
        def fun_new():
            return 'result'

        result = fun_new()
        """,
    )


def test_finds_class():
    assert_renames(
        row=1,
        column=6,
        old_name="OldClass",
        old_source="""
        class OldClass:
            pass

        instance = OldClass()
        """,
        new_name="NewClass",
        new_source="""
        class NewClass:
            pass

        instance = NewClass()
        """,
    )


def test_finds_passed_argument():
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, old)
        """,
        new_name="new",
        new_source="""
        new = 2

        def fun(arg: int, arg2: int) -> int:
            return arg + arg2

        fun(1, new)
        """,
    )


def test_does_not_find_method_of_unrelated_class():
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        new_name="new",
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
        """,
    )


def test_finds_static_method():
    assert_renames(
        row=4,
        column=8,
        old_name="old",
        old_source="""
        class A:

            @staticmethod
            def old(arg):
                pass

        a = A()
        a.old('foo')
        """,
        new_name="new",
        new_source="""
        class A:

            @staticmethod
            def new(arg):
                pass

        a = A()
        a.new('foo')
        """,
    )


def test_finds_argument():
    assert_renames(
        row=8,
        column=17,
        old_name="arg",
        old_source="""
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """,
        new_name="new_arg",
        new_source="""
        class A:

            def foo(self, new_arg):
                print(new_arg)

            def bar(self):
                arg = "1"
                self.foo(new_arg=arg)
        """,
    )


def test_finds_method_but_not_function():
    assert_renames(
        row=3,
        column=8,
        old_name="old",
        old_source="""
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """,
        new_name="new",
        new_source="""
        class A:

            def new(self):
                pass

            def foo(self):
                self.new()

            def bar(self):
                old()

        def old():
            pass
        """,
    )


def test_finds_definition_from_call():
    assert_renames(
        row=5,
        column=4,
        old_name="old",
        old_source="""
        def old():
            pass

        def bar():
            old()
        """,
        new_name="new",
        new_source="""
        def new():
            pass

        def bar():
            new()
        """,
    )


def test_finds_attribute_assignments():
    assert_renames(
        row=7,
        column=20,
        old_name="property",
        old_source="""
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """,
        new_name="new_property",
        new_source="""
        class ClassName:

            def __init__(self, property):
                self.new_property = property

            def get_property(self):
                return self.new_property
        """,
    )


def test_finds_dict_comprehension_variables():
    assert_renames(
        row=2,
        column=42,
        old_name="old",
        old_source="""
        old = 100
        foo = {old: None for old in range(100) if old % 3}
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = {new: None for new in range(100) if new % 3}
        """,
    )


def test_finds_list_comprehension_variables():
    assert_renames(
        row=3,
        column=12,
        old_name="old",
        old_source="""
        old = 100
        foo = [
            old for old in range(100) if old % 3]
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = [
            new for new in range(100) if new % 3]
        """,
    )


def test_finds_set_comprehension_variables():
    assert_renames(
        row=2,
        column=7,
        old_name="old",
        old_source="""
        old = 100
        foo = {old for old in range(100) if old % 3}
        """,
        new_name="new",
        new_source="""
        old = 100
        foo = {new for new in range(100) if new % 3}
        """,
    )


def test_finds_for_loop_variables():
    # Note that we could have chosen to treat the top level 'old' variable as
    # distinct from the loop variable, but since loop variables live on after
    # the loop, that would potentially change the behavior of the code.
    assert_renames(
        row=2,
        column=7,
        old_name="old",
        old_source="""
        old = None
        for i, old in enumerate(['foo']):
            print(i)
            print(old)
        print(old)
        """,
        new_name="new",
        new_source="""
        new = None
        for i, new in enumerate(['foo']):
            print(i)
            print(new)
        print(new)
        """,
    )


def test_finds_enclosing_scope_variable_from_comprehension():
    assert_renames(
        row=2,
        column=42,
        old_name="old",
        old_source="""
        old = 3
        res = [foo for foo in range(100) if foo % old]
        """,
        new_name="new",
        new_source="""
        new = 3
        res = [foo for foo in range(100) if foo % new]
        """,
    )


def test_finds_tuple_unpack():
    assert_renames(
        row=1,
        column=5,
        old_name="old",
        old_source="""
        foo, old = 1, 2
        print(old)
        """,
        new_name="new",
        new_source="""
        foo, new = 1, 2
        print(new)
        """,
    )


def test_recognizes_multiple_assignments():
    assert_renames(
        row=2,
        column=8,
        old_name="old",
        old_source="""
        class A:
            def old(self):
                pass

        class B:
            def old(self):
                pass

        foo, bar = A(), B()
        foo.old()
        bar.old()
        """,
        new_name="new",
        new_source="""
        class A:
            def new(self):
                pass

        class B:
            def old(self):
                pass

        foo, bar = A(), B()
        foo.new()
        bar.old()
        """,
    )


def test_finds_across_sources():
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=(source1, source2),
        new_name="new",
        new_sources=[
            """
            def new():
                pass
            """,
            """
            from foo import new
            new()
            """,
        ],
    )


def test_finds_multiple_imports_on_one_line():
    source1 = make_source(
        """
        def old():
            pass

        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar, old
        old()
        bar()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=(source1, source2),
        new_name="new",
        new_sources=[
            """
            def new():
                pass

            def bar():
                pass
            """,
            """
            from foo import bar, new
            new()
            bar()
            """,
        ],
    )


# TODO: calls in the middle of an attribute: foo.bar().qux
# TODO: import as
# TODO: rename parameter default value
# TODO: rename something in a function body that is defined after the function:
