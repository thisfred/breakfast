import os

from pytest import mark

from breakfast.names import Names, is_prefix
from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


def test_is_prefix_fails_when_paths_are_equal():
    assert not is_prefix(("1", "2"), ("1", "2"))


def test_is_prefix_fails_when_path_is_shorter_than_prefix():
    assert not is_prefix(("1", "2"), ("1",))


def test_is_prefix_succeeds_when_prefix_prefixes_the_path():
    assert is_prefix(("1", "2"), ("1", "2", "3"))


def test_visit_source_adds_name():
    source = make_source(
        """
    a = 1
    """
    )
    visitor = Names(source=source)
    visitor.visit_source(source)
    assert len(visitor.get_occurrences("a", Position(source, 1, 0))) == 1


def test_does_not_rename_random_attributes():
    source = make_source(
        """
    import os

    path = os.path.dirname(__file__)
    """
    )
    visitor = Names(source)

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences("path", Position(source, 3, 0))

    assert [Position(source, 3, 0)] == occurrences


def test_finds_local_variable():
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
    visitor = Names(source)

    visitor.visit_source(source)

    assert [
        Position(source, 2, 4),
        Position(source, 4, 13),
        Position(source, 5, 8),
    ] == visitor.get_occurrences("old", Position(source, 2, 4))


def test_finds_non_local_variable():
    source = make_source(
        """
    old = 12

    def fun():
        result = old + 1
        return result

    old = 20
    """
    )
    visitor = Names(source)

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences("old", Position(source, 1, 0))

    assert [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    ] == occurrences


def test_finds_across_files():
    source = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    other_source = make_source(
        """
        from foo import old
        old()
        """,
        module_name="bar",
    )
    visitor = Names(source)

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    assert (
        sorted(
            [
                Position(other_source, 1, 16),
                Position(other_source, 2, 0),
                Position(source, 1, 4),
            ]
        )
        == sorted(visitor.get_occurrences("old", Position(other_source, 2, 0)))
    )


def test_finds_multiple_imports_on_one_line():
    source = make_source(
        """
        def old():
            pass

        def bar():
            pass
        """,
        module_name="foo",
    )
    other_source = make_source(
        """
        from foo import bar, old
        old()
        bar()
        """,
        module_name="bar",
    )
    visitor = Names(source)

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    assert (
        sorted(
            [
                Position(other_source, 1, 21),
                Position(other_source, 2, 0),
                Position(source, 1, 4),
            ]
        )
        == sorted(visitor.get_occurrences("old", Position(other_source, 2, 0)))
    )


@mark.skip
def test_finds_static_method():
    source = make_source(
        """
    class A:

        @staticmethod
        def old(arg):
            pass

    a = A()
    a.old('foo')
    """
    )
    visitor = Names(source)

    visitor.visit_source(source)

    assert [Position(source, 4, 8), Position(source, 8, 2)] == visitor.get_occurrences(
        "old", Position(source, 4, 8)
    )


def test_finds_argument():
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
    visitor = Names(source)

    visitor.visit_source(source)

    assert [
        Position(source, 3, 18),
        Position(source, 4, 14),
        Position(source, 8, 17),
    ] == visitor.get_occurrences("arg", Position(source, 8, 17))


def test_finds_method_but_not_function():
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
    visitor = Names(source)

    visitor.visit_source(source)

    assert [Position(source, 3, 8), Position(source, 7, 13)] == visitor.get_occurrences(
        "old", Position(source, 3, 8)
    )


def test_fails_to_rename_builtins():
    source = make_source(
        """
        class A:

            def foo(self, arg):
                print(arg)
        """
    )
    visitor = Names(source)

    visitor.visit_source(source)

    assert [] == visitor.get_occurrences("print", Position(source, 4, 8))


def test_finds_method_in_imported_subclass():
    source = make_source(
        """
    class A:

        def old(self):
            pass
    """,
        module_name="foo",
    )
    other_source = make_source(
        """
    from foo import A

    class B(A):

        def foo(self):
            self.old()

    class C(A):

        def old(self):
            pass

        def bar(self):
            self.old()
    """,
        module_name="bar",
    )
    visitor = Names(source)

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    occurrences = sorted(visitor.get_occurrences("old", Position(source, 3, 8)))
    assert (
        sorted([Position(other_source, 6, 13), Position(source, 3, 8)]) == occurrences
    )


def test_finds_method_in_renamed_instance_of_subclass():
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

    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("old", Position(source, 3, 8))

    assert [Position(source, 3, 8), Position(source, 11, 2)] == occurrences


def test_finds_global_variable_in_method_scope():
    source = make_source(
        """
    b = 12

    class Foo:

        def bar(self):
            return b
    """
    )

    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("b", Position(source, 1, 0))

    assert [Position(source, 1, 0), Position(source, 6, 15)] == occurrences


def test_treats_staticmethod_args_correctly():
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
    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("old", Position(source, 3, 8))

    assert [Position(source, 3, 8)] == occurrences


def test_finds_global_variable():
    source = make_source(
        """
    b = 12

    def bar():
        global b
        b = 20
    """
    )
    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("b", Position(source, 1, 0))

    assert [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 4),
    ] == occurrences


def test_finds_nonlocal_variable():
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
    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("b", Position(source, 4, 4))

    assert [
        Position(source, 4, 4),
        Position(source, 6, 17),
        Position(source, 7, 8),
        Position(source, 8, 11),
        Position(source, 9, 4),
    ] == occurrences


def test_finds_method_in_aliased_imported_subclass():
    source = make_source(
        """
    class A:

        def old(self):
            pass
    """,
        module_name="foo",
    )
    other_source = make_source(
        """
    from foo import A as D

    class B(D):

        def foo(self):
            self.old()

    class C(D):

        def old(self):
            pass

        def bar(self):
            self.old()
    """,
        module_name="bar",
    )
    visitor = Names(source)

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    occurrences = sorted(visitor.get_occurrences("old", Position(source, 3, 8)))
    assert (
        sorted([Position(other_source, 6, 13), Position(source, 3, 8)]) == occurrences
    )


def test_finds_multiple_definitions():
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
    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("b", Position(source, 3, 4))
    assert [
        Position(source, 3, 4),
        Position(source, 5, 4),
        Position(source, 6, 6),
    ] == occurrences


def test_dogfooding():
    """Make sure we can read and process a realistic file."""
    with open(os.path.join("breakfast", "names.py"), "r") as source_file:
        source = Source(
            lines=tuple(line[:-1] for line in source_file.readlines()),
            module_name="breakfast.names",
            file_name="breakfast/names.py",
        )

    visitor = Names(source)

    visitor.visit_source(source)
    assert visitor.get_occurrences("whatever", Position(source, 3, 8)) == []


@mark.skip
def test_finds_method_in_super_call():
    source = make_source(
        """
    class Foo:

        def bar(self):
            pass


    class Bar(Foo):

        def bar(self):
            super(Bar, self).bar()
    """
    )

    visitor = Names(source)

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences("bar", Position(source, 3, 8))

    assert [Position(source, 3, 8), Position(source, 10, 26)] == occurrences


# TODO: rename methods on super calls
# TODO: calls in the middle of an attribute: foo.bar().qux
# TODO: import as
