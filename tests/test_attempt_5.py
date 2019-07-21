import ast
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

import pytest

from breakfast.position import Position
from tests import make_source

if TYPE_CHECKING:
    from breakfast.source import Source


class NameSpace:
    def __init__(
        self,
        parent: Optional["NameSpace"] = None,
        is_class: bool = False,
        cls: Optional["NameSpace"] = None,
    ) -> None:
        self.parent = parent
        self._children: Dict[str, "NameSpace"] = {}
        self.occurrences: List[Position] = []
        self.is_class = is_class
        self._enclosing_class = cls
        self.points_to: Optional["NameSpace"] = None
        self._aliases: List["NameSpace"] = []

    def add_module(self, name: str) -> "NameSpace":
        new = NameSpace(self)
        self._children[name] = new
        return self._children[name]

    def add_occurrence(
        self, name: str, position: Position, force: bool = False
    ) -> "NameSpace":
        return self.add_name(name, position, force=force)

    def add_definition(self, name: str, position: Position) -> "NameSpace":
        return self.add_name(name, position, force=True)

    def add_function_definition(self, name: str, position: Position) -> "NameSpace":
        return self.add_name(
            name, position, force=True, cls=self if self.is_class else None
        )

    def add_static_method(self, name: str, position: Position) -> "NameSpace":
        return self.add_name(name, position, force=True)

    def add_class_definition(self, name: str, position: Position) -> "NameSpace":
        return self.add_name(name, position, force=True, is_class=True)

    def add_parameter(self, name: str, number: int, position: Position) -> "NameSpace":
        parameter = self.add_name(name, position, force=True)
        if number == 0 and self._enclosing_class:
            self._enclosing_class.add_alias(parameter)
        return parameter

    def add_alias(self, alias_namespace: "NameSpace") -> None:
        self._aliases.append(alias_namespace)
        alias_namespace.set_points_to(self)

    def set_points_to(self, cls: "NameSpace") -> None:
        self.points_to = cls

    def get_namespace(self, name: str) -> "NameSpace":
        namespace = self._children.get(name)
        if namespace is None:
            namespace = (self.parent or self).get_namespace(name)
        while namespace.points_to:
            namespace = namespace.points_to
        return namespace

    def find_occurrences(self, name: str, position: Position) -> List[Position]:
        if name in self._children:
            child = self._children[name]
            if position in child.occurrences:
                return child.occurrences

        for child in self._children.values():
            occurrences = child.find_occurrences(name, position)
            if occurrences:
                return occurrences

        return []

    def _add_child(
        self, name: str, position: Position, is_class: bool, cls: Optional["NameSpace"]
    ) -> None:
        new = NameSpace(self, is_class=is_class, cls=cls)
        self.set_namespace(name, new)
        self._add_child_occurrence(name, position)

    def set_namespace(self, name: str, namespace: "NameSpace") -> None:
        self._children[name] = namespace

    def add_name(
        self,
        name: str,
        position: Position,
        force: bool,
        is_class: bool = False,
        cls: Optional["NameSpace"] = None,
    ) -> "NameSpace":
        if name in self._children:
            self._add_child_occurrence(name, position)
        elif force or self.parent is None:
            self._add_child(name, position, is_class=is_class, cls=cls)
        else:
            enclosing_scope = self.parent
            # method bodies have no direct access to class namespace
            if enclosing_scope.is_class:
                if enclosing_scope.parent:
                    enclosing_scope = enclosing_scope.parent
            return enclosing_scope.add_name(
                name, position, force=force, is_class=is_class, cls=cls
            )

        return self._children[name]

    def _add_child_occurrence(self, name: str, position: Position) -> None:
        self._children[name].occurrences.append(position)


class NameVisitor(ast.NodeVisitor):
    def __init__(self, initial_source: "Source") -> None:
        self.current_source = initial_source
        self.top = NameSpace()
        self.current = self.top

    def visit_source(self, source: "Source") -> None:
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def visit_Module(self, node: ast.AST) -> None:  # noqa
        old = self.current
        self.current = self.current.add_module(self.current_source.module_name)
        self.generic_visit(node)
        self.current = old

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa
        if not node.module:
            return

        import_path = node.module.split(".")
        import_namespace = self.top
        for path in import_path:
            import_namespace = import_namespace.get_namespace(path)
        start = self._position_from_node(node)
        for imported in node.names:
            name = imported.name
            position = self.current_source.find_after(name, start)
            if position:
                original = import_namespace.add_occurrence(name, position, force=True)
                self.current.set_namespace(name, original)
            alias = imported.asname
            if alias:
                alias_position = self.current_source.find_after(alias, start)
                if not alias_position:
                    continue
                alias_namespace = self.current.add_definition(alias, alias_position)
                original.add_alias(alias_namespace)
                self.current.add_definition(alias, alias_position)

    def visit_Name(self, node: ast.Name) -> None:  # noqa
        position = self._position_from_node(node)
        if self._is_definition(node):
            self.current.add_definition(node.id, position)
        else:
            self.current.add_occurrence(node.id, position)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa
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

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("class ")
        )
        old = self.current
        self.current = self.current.add_class_definition(
            name=node.name, position=position
        )
        self.generic_visit(node)
        self.current = old

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa
        self.visit(node.value)
        old = self.current
        for name in self._names_from(node.value):
            self.current = self.current.get_namespace(name)
        name = node.attr
        start = self._position_from_node(node)
        position = self.current_source.find_after(name, start)
        if position:
            if self._is_definition(node):
                self.current.add_definition(name=name, position=position)
            else:
                self.current.add_occurrence(name=name, position=position, force=True)
        self.current = old

    def visit_Call(self, node: ast.Call) -> None:  # noqa
        self.visit(node.func)
        old = self.current
        for name in self._names_from(node.func):
            self.current = self.current.get_namespace(name)
        start = self._position_from_node(node)
        for keyword in node.keywords:
            if keyword.arg:
                position = self.current_source.find_after(keyword.arg, start)
                if position:
                    self.current.add_occurrence(name=keyword.arg, position=position)
        self.current = old
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa
        self.generic_visit(node)
        if isinstance(node.value, ast.Tuple):
            # multiple assignment
            values = [v for v in node.value.elts]
            idk = node.targets[0]
            if isinstance(idk, (ast.Tuple, ast.List, ast.Set)):
                targets = [t for t in idk.elts]
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

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa
        self._comp_visit(node, node.key, node.value)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa
        self._comp_visit(node, node.elt)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa
        self._comp_visit(node, node.elt)

    def _comp_visit(
        self, node: Union[ast.DictComp, ast.SetComp, ast.ListComp], *rest: ast.AST
    ) -> None:
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
    def _is_definition(node: Union[ast.Name, ast.Attribute]) -> bool:
        return isinstance(node.ctx, (ast.Param, ast.Store))

    @staticmethod
    def _is_staticmethod(node: ast.FunctionDef) -> bool:
        return any(
            n.id == "staticmethod"
            for n in node.decorator_list
            if isinstance(n, ast.Name)
        )

    def _position_from_node(
        self, node: ast.AST, row_offset: int = 0, column_offset: int = 0
    ) -> Position:
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
        )

    def _names_from(self, node: ast.AST) -> Tuple[str, ...]:
        if isinstance(node, ast.Name):
            return (node.id,)

        if isinstance(node, ast.Attribute):
            return self._names_from(node.value) + (node.attr,)

        if isinstance(node, ast.Call):
            return self._names_from(node.func)

        return tuple()


def find_occurrences(
    sources: List["Source"], old_name: str, position: Position
) -> List[Position]:
    visitor = NameVisitor(sources[0])
    for source in sources:
        visitor.visit_source(source)
    return visitor.top.find_occurrences(old_name, position)


def rename(
    *, sources: List["Source"], old_name: str, new_name: str, position: Position
) -> List["Source"]:
    for occurrence in find_occurrences(
        sources=sources, old_name=old_name, position=position
    ):
        occurrence.source.replace(occurrence, old_name, new_name)
    return sources


def assert_renames(
    *,
    row: int,
    column: int,
    old_name: str,
    old_source: str,
    new_name: str,
    new_source: str
) -> None:
    source = make_source(old_source)
    renamed = rename(
        sources=[source],
        old_name=old_name,
        new_name=new_name,
        position=Position(source, row, column),
    )
    assert make_source(new_source).render() == renamed[0].render()


def assert_renames_multi_source(
    position: Position,
    old_name: str,
    old_sources: List["Source"],
    new_name: str,
    new_sources: List[str],
) -> None:
    renamed = rename(
        sources=old_sources, old_name=old_name, new_name=new_name, position=position
    )
    for actual, expected in zip(renamed, new_sources):
        assert make_source(expected).render() == actual.render()


def test_does_not_rename_random_attributes() -> None:
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


def test_finds_local_variable() -> None:
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


def test_finds_variable_in_closure() -> None:
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


def test_finds_method_names() -> None:
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


def test_finds_parameters() -> None:
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


def test_finds_function() -> None:
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


def test_finds_class() -> None:
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


def test_finds_passed_argument() -> None:
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


def test_does_not_find_method_of_unrelated_class() -> None:
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


def test_finds_static_method() -> None:
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


def test_finds_argument() -> None:
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


def test_finds_method_but_not_function() -> None:
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


def test_finds_definition_from_call() -> None:
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


def test_finds_attribute_assignments() -> None:
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


def test_finds_dict_comprehension_variables() -> None:
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


def test_finds_list_comprehension_variables() -> None:
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


def test_finds_set_comprehension_variables() -> None:
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


def test_finds_for_loop_variables() -> None:
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


def test_finds_enclosing_scope_variable_from_comprehension() -> None:
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


def test_finds_tuple_unpack() -> None:
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


def test_recognizes_multiple_assignments() -> None:
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


def test_finds_across_sources() -> None:
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
        old_sources=[source1, source2],
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


def test_finds_multiple_imports_on_one_line() -> None:
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
        old_sources=[source1, source2],
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


def test_finds_calls_in_the_middle_of_an_attribute_chain() -> None:
    assert_renames(
        row=5,
        column=8,
        old_name="old",
        old_source="""
        class Bar:
            baz = 'whatever'

        class Foo:
            def old():
                return Bar()

        foo = Foo()
        result = foo.old().baz
        """,
        new_name="new",
        new_source="""
        class Bar:
            baz = 'whatever'

        class Foo:
            def new():
                return Bar()

        foo = Foo()
        result = foo.new().baz
        """,
    )


def test_finds_renamed_imports() -> None:
    source1 = make_source(
        """
        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar as old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def bar():
                pass
            """,
            """
            from foo import bar as new
            new()
            """,
        ],
    )


def test_finds_properties_of_renamed_imports() -> None:
    source1 = make_source(
        """
        def bar():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        from foo import bar as old
        old()
        """,
        module_name="bar",
    )

    assert_renames_multi_source(
        position=Position(source=source2, row=2, column=0),
        old_name="old",
        old_sources=[source1, source2],
        new_name="new",
        new_sources=[
            """
            def bar():
                pass
            """,
            """
            from foo import bar as new
            new()
            """,
        ],
    )


def test_finds_default_value() -> None:
    assert_renames(
        row=1,
        column=0,
        old_name="old",
        old_source="""
        old = 2

        def fun(arg=old):
            old = 1
            return arg + old
        """,
        new_name="new",
        new_source="""
        new = 2

        def fun(arg=new):
            old = 1
            return arg + old
        """,
    )


@pytest.mark.skip
def test_finds_name_defined_after_usage1() -> None:
    assert_renames(
        row=4,
        column=4,
        old_name="old",
        old_source="""
        def foo():
            old()

        def old():
            pass
        """,
        new_name="new",
        new_source="""
        def foo():
            new()

        def new():
            pass
        """,
    )


@pytest.mark.skip
def test_finds_name_defined_after_usage2() -> None:
    assert_renames(
        row=2,
        column=4,
        old_name="old",
        old_source="""
        def foo():
            old()


        def old():
            pass
        """,
        new_name="new",
        new_source="""
        def foo():
            new()


        def new():
            pass
        """,
    )
