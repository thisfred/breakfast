import sys

from pytest import mark

from breakfast.names import all_occurrence_positions, build_graph
from breakfast.source import Position
from tests.conftest import all_occurrence_position_tuples, make_source


def test_assignment_occurrences() -> None:
    source1 = make_source(
        """
    from kitchen import Stove

    stove = Stove()
    stove.broil()
    """,
        filename="chef.py",
    )
    source2 = make_source(
        """
    from stove import *
    """,
        filename="kitchen.py",
    )
    source3 = make_source(
        """
    class Stove:
        def bake():
            pass

        def broil():
            pass

        def saute():
            pass
    """,
        filename="stove.py",
    )
    positions = all_occurrence_positions(
        Position(source1, 4, 6), sources=[source1, source2, source3]
    )
    assert positions == [
        Position(source1, 4, 6),
        Position(source3, 5, 8),
    ]


def test_should_find_occurrences_along_longer_import_paths() -> None:
    source1 = make_source(
        """
    from cooking.kitchen import Stove

    stove = Stove()
    stove.broil()
    """,
        filename="cooking/chef.py",
    )
    source2 = make_source(
        """
    from cooking.stove import *
    """,
        filename="cooking/kitchen.py",
    )
    source3 = make_source(
        """
    class Stove:
        def bake():
            pass

        def broil():
            pass

        def saute():
            pass
    """,
        filename="cooking/stove.py",
    )
    positions = all_occurrence_positions(
        Position(source1, 4, 6), sources=[source1, source2, source3]
    )
    assert positions == [
        Position(source1, 4, 6),
        Position(source3, 5, 8),
    ]


def test_should_find_occurrences_along_relative_import_paths() -> None:
    source1 = make_source(
        """
        from ..d.e import C

        c = C()
        """,
        filename="a/b/c.py",
    )
    source2 = make_source(
        """
        class C:
            ...
        """,
        filename="a/d/e.py",
    )
    positions = all_occurrence_positions(
        Position(source2, 1, 6), sources=[source1, source2]
    )
    assert positions == [
        Position(source1, 1, 18),
        Position(source1, 3, 4),
        Position(source2, 1, 6),
    ]


def test_kwarg_value() -> None:
    source = make_source(
        """
        var = 12

        def fun(b=var):
            foo = b
            return foo
        """
    )

    position = Position(source, 3, 10)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 3, 10),
    ]


def test_finds_global_variable_usage_from_definition() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        global var
        foo = var
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 10),
    ]


def test_finds_global_variable_from_local_usage() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        global var
        foo = var
    """
    )

    position = Position(source, 5, 10)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 10),
    ]


def test_reassignment() -> None:
    source = make_source(
        """
        a = 0
        def fun():
            a = 1
            ...
            a = 2
        """,
        filename="module.py",
    )

    position = Position(source, 5, 4)
    assert all_occurrence_positions(position) == [
        Position(source, 3, 4),
        Position(source, 5, 4),
    ]


def test_distinguishes_local_variables_from_global() -> None:
    source = make_source(
        """
        def fun():
            var = 12
            var2 = 13
            result = var + var2
            del var
            return result

        var = 20
        """
    )

    position = source.position(row=2, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    ]


def test_finds_non_local_variable() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    ]


def test_finds_non_local_variable_defined_after_use() -> None:
    source = make_source(
        """
    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(5, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 2, 13),
        Position(source, 5, 0),
    ]


def test_does_not_rename_random_attributes() -> None:
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = source.position(row=3, column=0)

    assert all_occurrence_positions(position) == [source.position(row=3, column=0)]


def test_finds_parameter() -> None:
    source = make_source(
        """
        def fun(arg=1):
            print(arg)

        arg = 8
        fun(arg=arg)
        """
    )

    assert all_occurrence_positions(source.position(1, 8)) == [
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    ]


def test_finds_function() -> None:
    source = make_source(
        """
        def fun():
            return 'result'
        result = fun()
        """
    )

    assert [source.position(1, 4), source.position(3, 9)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_class() -> None:
    source = make_source(
        """
        class Class:
            pass

        instance = Class()
        """
    )

    assert [source.position(1, 6), source.position(4, 11)] == all_occurrence_positions(
        source.position(1, 6)
    )


def test_finds_method_name() -> None:
    source = make_source(
        """
        class A:

            def method(self):
                pass

        unbound = A.method
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=6, column=12),
    ]


def test_finds_passed_argument() -> None:
    source = make_source(
        """
        var = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, var)
        """
    )

    assert [source.position(1, 0), source.position(4, 7)] == all_occurrence_positions(
        source.position(1, 0)
    )


def test_does_not_find_method_of_unrelated_class() -> None:
    source = make_source(
        """
        class ClassThatShouldHaveMethodRenamed:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        class UnrelatedClass:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.method()
        b = UnrelatedClass()
        b.method()
        """
    )

    occurrences = all_occurrence_positions(source.position(3, 8))

    assert [
        source.position(3, 8),
        source.position(7, 13),
        source.position(20, 2),
    ] == occurrences


def test_finds_definition_from_call() -> None:
    source = make_source(
        """
        def fun():
            pass

        def bar():
            fun()
        """
    )

    assert [source.position(1, 4), source.position(5, 4)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_considers_self_properties_instance_properties() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(4, 13))

    assert [source.position(4, 13), source.position(7, 20)] == occurrences


def test_should_find_instance_properties_that_are_assigned_to() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                self.property = wat
        """
    )
    occurrences = all_occurrence_positions(source.position(4, 13))

    assert [source.position(4, 13), source.position(7, 13)] == occurrences


def test_should_find_class_attribute_when_assigned_to():
    source = make_source(
        """
        class ClassName:
            attr = None

            def method(self, *, arg):
                self.attr = arg
        """
    )
    position = source.position(5, 13)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (2, 4),
        (5, 13),
    ]


def test_finds_value_assigned_to_property() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(3, 23))

    assert [source.position(3, 23), source.position(4, 24)] == occurrences


def test_finds_dict_comprehension_variables() -> None:
    source = make_source(
        """
        var = 1
        foo = {var: None for var in range(100) if var % 3}
        var = 2
        """
    )

    position = source.position(row=2, column=21)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=21),
        source.position(row=2, column=42),
    ]


def test_finds_list_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = [
            var for var in range(100) if var % 3]
        var = 200
        """
    )

    position = source.position(row=3, column=12)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=4),
        source.position(row=3, column=12),
        source.position(row=3, column=33),
    ]


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = {var for var in range(100) if var % 3}
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = (var for var in range(100) if var % 3)
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_loop_variables_outside_loop() -> None:
    source = make_source(
        """
        var = None
        for var in ['foo']:
            print(var)
        print(var)
        """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=4),
        source.position(row=3, column=10),
        source.position(row=4, column=6),
    ]


def test_finds_loop_variables() -> None:
    source = make_source(
        """
        for a in []:
            print(a)
        """
    )

    position = source.position(row=1, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=4),
        source.position(row=2, column=10),
    ]


def test_finds_tuple_unpack() -> None:
    source = make_source(
        """
    foo, var = 1, 2
    print(var)
    """
    )

    position = source.position(row=1, column=5)

    assert all_occurrence_positions(position) == [
        source.position(1, 5),
        source.position(2, 6),
    ]


def test_finds_superclasses() -> None:
    source = make_source(
        """
        class A:
            def method(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.method()
        """
    )

    position = source.position(row=2, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=8),
        source.position(row=10, column=2),
    ]


def test_recognizes_multiple_assignment_1() -> None:
    source = make_source(
        """
    a = 1
    foo, bar = a, a
    """
    )

    position = source.position(row=1, column=0)
    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=11),
        source.position(row=2, column=14),
    ]


def test_recognizes_multiple_assignments() -> None:
    source = make_source(
        """
    class A:
        def method(self):
            pass

    class B:
        def method(self):
            pass

    foo, bar = A(), B()
    foo.method()
    bar.method()
    """
    )

    position = source.position(row=2, column=8)

    assert all_occurrence_positions(position) == [
        source.position(2, 8),
        source.position(10, 4),
    ]


def test_finds_enclosing_scope_variable_from_comprehension() -> None:
    source = make_source(
        """
    var = 3
    res = [foo for foo in range(100) if foo % var]
    """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(1, 0),
        source.position(2, 42),
    ]


def test_finds_static_method() -> None:
    source = make_source(
        """
        class A:

            @staticmethod
            def method(arg):
                pass

        a = A()
        b = a.method('foo')
        """
    )

    position = source.position(row=4, column=8)

    assert all_occurrence_positions(position) == [
        source.position(4, 8),
        source.position(8, 6),
    ]


def test_finds_method_after_call() -> None:
    source = make_source(
        """
        class A:

            def method(arg):
                pass

        b = A().method('foo')
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(6, 8),
    ]


def test_finds_argument() -> None:
    source = make_source(
        """
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """
    )

    position = source.position(row=3, column=18)

    assert all_occurrence_positions(position) == [
        source.position(3, 18),
        source.position(4, 14),
        source.position(8, 17),
    ]


def test_finds_method_but_not_function() -> None:
    source = make_source(
        """
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """
    )
    position = source.position(3, 8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(7, 13),
    ]


def test_finds_global_variable_in_method_scope() -> None:
    source = make_source(
        """
    b = 12

    class Foo:

        def bar(self):
            return b
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 6, 15),
    ]


def test_treats_staticmethod_args_correctly() -> None:
    source = make_source(
        """
    class ClassName:

        def old(self):
            pass

        @staticmethod
        def foo(whatever):
            whatever.old()
    """
    )
    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [Position(source, 3, 8)]


def test_finds_nonlocal_variable() -> None:
    source = make_source(
        """
    b = 12

    def foo():
        b = 20
        def bar():
            nonlocal b
            b = 20
        b = 1
        return b

    print(b)
    """
    )

    position = Position(source, 4, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 4, 4),
        Position(source, 6, 17),
        Position(source, 7, 8),
        Position(source, 8, 4),
        Position(source, 9, 11),
    ]


def test_finds_multiple_definitions() -> None:
    source = make_source(
        """
    a = 12
    if a > 10:
        b = a + 100
    else:
        b = 3 - a
    print(b)
    """
    )
    position = Position(source, 3, 4)
    assert all_occurrence_positions(position) == [
        Position(source, 3, 4),
        Position(source, 5, 4),
        Position(source, 6, 6),
    ]


def test_finds_method_in_super_call() -> None:
    source = make_source(
        """
    class Foo:

        def bar(self):
            pass


    class Bar(Foo):

        def bar(self):
            super().bar()
    """
    )

    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [
        Position(source, 3, 8),
        Position(source, 10, 16),
    ]


def test_does_not_rename_imported_names() -> None:
    source = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """
    )
    position = Position(source, 5, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 5, 4),
        Position(source, 6, 10),
    ]


def test_finds_namespace_imports() -> None:
    source1 = make_source(
        """
        def old():
            pass
        """,
        filename="foo.py",
    )
    source2 = make_source(
        """
        import foo
        foo.old()
        """,
        filename="bar.py",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, sources=[source1, source2]) == [
        Position(source2, 2, 4),
        Position(source1, 1, 4),
    ]


def test_finds_default_values():
    source = make_source(
        """
        v = 0

        def f(a=v):
            ...
        """
    )
    position = Position(source, 1, 0)
    assert all_occurrence_positions(position, sources=[source]) == [
        Position(source, 1, 0),
        Position(source, 3, 8),
    ]


def test_finds_keyword_argument_values():
    source = make_source(
        """
        v = 0

        f(a=v)
        """
    )
    position = Position(source, 1, 0)
    assert all_occurrence_positions(position, sources=[source]) == [
        Position(source, 1, 0),
        Position(source, 3, 4),
    ]


def test_finds_unpacked_names():
    source = make_source(
        """
        for a, b in thing:
            print(a)
        """
    )

    position = Position(source, 1, 4)
    assert all_occurrence_positions(position, sources=[source]) == [
        Position(source, 1, 4),
        Position(source, 2, 10),
    ]


def test_unicode_strings():
    source = make_source(
        """
        node = Thing()
        var = "↑" + node.attr
        """
    )
    position = source.position(1, 0)
    assert all_occurrence_positions(position, sources=[source]) == [
        Position(source, 1, 0),
        Position(source, 2, 12),
    ]


def test_pattern_matching_should_only_find_occurrences_in_a_single_case():
    source = make_source(
        """
        match thing:
            case a if a > 2:
                print(a)

            case a:
                print(a)
        """
    )
    position = source.position(2, 9)
    assert all_occurrence_positions(position, sources=[source]) == [
        Position(source, 2, 9),
        Position(source, 2, 14),
        Position(source, 3, 14),
    ]


def test_should_find_class_used_in_method_annotation():
    source = make_source(
        """
        class C:
            ...

        class D:
            def f(self, c: C) -> C:
                ...
        """
    )

    position = source.position(1, 6)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (5, 19),
        (5, 25),
    ]


def test_should_find_class_used_in_string_annotation():
    source = make_source(
        """
        class C:
            ...

        def f(c: "C"):
            ...
        """
    )

    position = source.position(1, 6)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (4, 10),
    ]


def test_should_find_class_used_in_return_annotation():
    source = make_source(
        """
        class C:
            ...

        def f() -> C:
            ...
        """
    )

    position = source.position(1, 6)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (4, 11),
    ]


def test_none_type_annotation_should_not_break_things():
    source = make_source(
        """
        def f() -> None:
            ...
        """
    )
    position = source.position(1, 4)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 4),
    ]


def test_should_rename_annotated_class_property():
    source = make_source(
        """
        class C:
            property: str

            def f(self):
                self.property = ""
        """
    )
    position = source.position(2, 4)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (2, 4),
        (5, 13),
    ]


@mark.skipif(sys.version_info < (3, 12), reason="requires Python 3.12 or higher")
def test_should_rename_type_parameters():
    source = make_source(
        """
        def f[T](a: Iterable[T]) -> T:
            ...
        """
    )
    position = source.position(1, 6)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (1, 21),
        (1, 28),
    ]


@mark.skipif(sys.version_info < (3, 12), reason="requires Python 3.12 or higher")
def test_should_consider_type_vars_local_to_function():
    source = make_source(
        """
        def f[T](a: Iterable[T]) -> T:
            ...

        def f2[T]() -> T:
            ...
        """
    )
    position = source.position(1, 6)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (1, 21),
        (1, 28),
    ]


@mark.skipif(sys.version_info < (3, 12), reason="requires Python 3.12 or higher")
def test_should_rename_type_parameters_in_class():
    source = make_source(
        """
        class C[T]:
            def m(self, a:T) -> T:
                ...
        """
    )
    position = source.position(1, 8)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 8),
        (2, 18),
        (2, 24),
    ]


@mark.skipif(sys.version_info < (3, 12), reason="requires Python 3.12 or higher")
def test_should_rename_type_variable_bounds():
    source = make_source(
        """
        class V:
            ...

        type T[U: V] = X[U]

        """
    )
    position = source.position(1, 6)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 6),
        (4, 10),
    ]


def test_should_consider_parameter_instance_of_type_annotation():
    source = make_source(
        """
        class C:
            def m():
                ...

        def f(a: C):
            a.m()

        """
    )
    position = source.position(2, 8)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (2, 8),
        (6, 6),
    ]


def test_should_consider_return_value_instance_of_type_annotation():
    source = make_source(
        """
        class C:
            def m():
                ...

        def f() -> C:
            ...

        a = f()
        a.m()

        """
    )
    position = source.position(2, 8)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (2, 8),
        (9, 2),
    ]


def test_should_find_decorators():
    source = make_source(
        """
        def f():
            ...

        @f
        def g():
            ...
        """
    )
    position = source.position(1, 4)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 4),
        (4, 1),
    ]


def test_should_find_multiple_assignment_in_method():
    source = make_source(
        """
        class C:
            def m(self):
                start, end = self.extended_range
                text = start.through(end).text
        """
    )

    position = source.position(4, 29)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (3, 15),
        (4, 29),
    ]


def test_should_find_arguments_in_chained_calls():
    source = make_source(
        """
        a = 1
        b = c.d(a).e
        """
    )

    position = source.position(2, 8)

    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 0),
        (2, 8),
    ]


def test_should_find_async_function_definition():
    source = make_source(
        """
        async def f():
            ...

        a = await f()
        """
    )
    position = source.position(4, 10)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 10),
        (4, 10),
    ]


def test_should_find_name_in_index_lookup():
    source = make_source(
        """
        b = 1
        b.c.d[b.f].e
        """
    )

    position = source.position(1, 0)
    assert all_occurrence_position_tuples(position, sources=[source]) == [
        (1, 0),
        (2, 0),
        (2, 6),
    ]


def test_name_for_type_of_keyword_only_argument_should_be_found():
    source = make_source(
        """
        def make_code_action(
            *,
            refactor: Refactor,
        ) -> CodeAction:
            ...
        """
    )

    graph = build_graph(sources=[source])

    assert len(graph.references["Refactor"]) == 1
