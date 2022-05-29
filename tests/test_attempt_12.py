from breakfast.names import all_occurrence_positions
from breakfast.position import Position
from tests import make_source


def test_distinguishes_local_variables_from_global() -> None:
    source = make_source(
        """
        def fun():
            old = 12
            old2 = 13
            result = old + old2
            del old
            return result

        old = 20
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
    old = 12

    def fun():
        result = old + 1
        return result

    old = 20
    """
    )

    position = source.position(1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
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
        def fun(old=1):
            print(old)

        old = 8
        fun(old=old)
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
        def fun_old():
            return 'result'
        result = fun_old()
        """
    )

    assert [source.position(1, 4), source.position(3, 9)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_class() -> None:
    source = make_source(
        """
        class OldClass:
            pass

        instance = OldClass()
        """
    )

    assert [source.position(1, 6), source.position(4, 11)] == all_occurrence_positions(
        source.position(1, 6)
    )


def test_finds_method_name() -> None:
    source = make_source(
        """
        class A:

            def old(self):
                pass

        unbound = A.old
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
        old = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, old)
        """
    )

    assert [source.position(1, 0), source.position(4, 7)] == all_occurrence_positions(
        source.position(1, 0)
    )


def test_finds_parameter_with_unusual_indentation() -> None:
    source = make_source(
        """
        def fun(arg, arg2):
            return arg + arg2
        fun(
            arg=\\
                1,
            arg2=2)
        """
    )

    assert [
        source.position(1, 8),
        source.position(2, 11),
        source.position(4, 4),
    ] == all_occurrence_positions(source.position(1, 8))


def test_does_not_find_method_of_unrelated_class() -> None:
    source = make_source(
        """
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
        def old():
            pass

        def bar():
            old()
        """
    )

    assert [source.position(1, 4), source.position(5, 4)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_attribute_assignments() -> None:
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


def test_finds_dict_comprehension_variables() -> None:
    source = make_source(
        """
        old = 1
        foo = {old: None for old in range(100) if old % 3}
        old = 2
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
        old = 100
        foo = [
            old for old in range(100) if old % 3]
        old = 200
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
        old = 100
        foo = {old for old in range(100) if old % 3}
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
        old = 100
        foo = (old for old in range(100) if old % 3)
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_loop_variables() -> None:
    source = make_source(
        """
        old = None
        for i, old in enumerate(['foo']):
            print(i)
            print(old)
        print(old)
        """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=7),
        source.position(row=4, column=10),
        source.position(row=5, column=6),
    ]


def test_finds_tuple_unpack() -> None:
    source = make_source(
        """
    foo, old = 1, 2
    print(old)
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

            def old(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.old()
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=11, column=2),
    ]


def test_recognizes_multiple_assignments() -> None:
    source = make_source(
        """
    class A:
        def old(self):
            pass

    class B:
        def old(self):
            pass

    foo, bar = A(), B()
    foo.old()
    bar.old()
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
    old = 3
    res = [foo for foo in range(100) if foo % old]
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
            def old(arg):
                pass

        a = A()
        b = a.old('foo')
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

            def old(arg):
                pass

        b = A().old('foo')
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


def test_finds_global_variable() -> None:
    source = make_source(
        """
    b = 12

    def bar():
        global b
        b = 20
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 4),
    ]


def test_finds_nonlocal_variable() -> None:
    source = make_source(
        """
    b = 12

    def foo():
        b = 20
        def bar():
            nonlocal b
            b = 20
        return b
        b = 1

    print(b)
    """
    )

    position = Position(source, 4, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 4, 4),
        Position(source, 6, 17),
        Position(source, 7, 8),
        Position(source, 8, 11),
        Position(source, 9, 4),
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
        Position(source, 9, 8),
        Position(source, 10, 16),
    ]


def test_finds_imports() -> None:
    source = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """
    )
    position = Position(source, 1, 14)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 14),
        Position(source, 8, 0),
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


def test_finds_across_files() -> None:
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
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 1, 16),
        Position(source2, 2, 0),
    ]


def test_finds_namespace_imports() -> None:
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        import foo
        foo.old()
        """,
        module_name="bar",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 2, 4),
    ]


def test_finds_imports_with_paths() -> None:
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo.bar",
    )
    source2 = make_source(
        """
        from foo.bar import old
        old()
        """,
        module_name="qux",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, other_sources=[source2]) == [
        Position(source1, 1, 4),
        Position(source2, 1, 20),
        Position(source2, 2, 0),
    ]


def test_finds_multile_imports() -> None:
    source = make_source(
        """
        from a import b, c


        def foo():
            b = 1
            print(b)

        b()
        c()
        """
    )
    position = Position(source, 1, 17)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 17),
        Position(source, 9, 0),
    ]


def test_finds_aliased_imports() -> None:
    source = make_source(
        """
        from a import b as c, c as d


        def foo():
            b = 1
            print(b)

        c()
        d()
        """
    )
    position = Position(source, 1, 19)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 19),
        Position(source, 8, 0),
    ]
