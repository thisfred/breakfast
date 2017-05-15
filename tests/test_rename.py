"""Tests for rename refactoring."""

from breakfast.rename import AttributeNames
from breakfast.source import Source
from tests import dedent, make_source

import pytest


def test_renames_function_from_lines():
    source = Source([
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"])

    source.rename(row=0, column=4, new_name='fun_new')

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]


def test_rename_across_files():
    source = make_source(
        """
        def old():
            pass
        """,
        module_name='foo')
    other_source = make_source(
        """
        from foo import old
        old()
        """,
        module_name='bar')

    other_source.rename(
        row=2, column=0, new_name='new', additional_sources=[source])

    assert dedent("""
        def new():
            pass
        """) == source.render()

    assert dedent("""
        from foo import new
        new()
        """) == other_source.render()


def test_rename_with_multiple_imports_on_one_line():
    source = make_source(
        """
        def old():
            pass

        def bar():
            pass
        """,
        module_name='foo')
    other_source = make_source(
        """
        from foo import bar, old
        old()
        bar()
        """,
        module_name='bar')

    other_source.rename(
        row=2, column=0, new_name='new', additional_sources=[source])

    assert dedent("""
        def new():
            pass

        def bar():
            pass
        """) == source.render()
    assert dedent("""
        from foo import bar, new
        new()
        bar()
        """) == other_source.render()


def test_renames_static_method():
    source = make_source("""
    class A:

        @staticmethod
        def old(arg):
            pass

    a = A()
    a.old('foo')
    """)

    source.rename(row=4, column=8, new_name='new')

    assert dedent("""
        class A:

            @staticmethod
            def new(arg):
                pass

        a = A()
        a.new('foo')
        """) == source.render()


def test_fails_to_rename_builtins():
    source_text = dedent("""
        class A:

            def foo(self, arg):
                print(arg)
        """)

    source = Source(source_text.split('\n'))
    source.rename(row=4, column=8, new_name='new')

    assert source_text == source.render()


def test_renames_argument():
    source = make_source("""
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """)

    source.rename(row=8, column=17, new_name='new')

    assert dedent("""
            class A:

                def foo(self, new):
                    print(new)

                def bar(self):
                    arg = "1"
                    self.foo(new=arg)
            """) == source.render()


def test_renames_multiple_assignment():
    source = make_source("""
    class A:

        def old(self):
            pass

    class B:
        pass

    b, a = B(), A()
    a.old()
    """)

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
        class A:

            def new(self):
                pass

        class B:
            pass

        b, a = B(), A()
        a.new()
        """) == source.render()


def test_renames_method_but_not_function():
    source = make_source("""
    class A:

        def old(self):
            pass

        def foo(self):
            self.old()

        def bar(self):
            old()

    def old():
        pass
    """)

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
        class A:

            def new(self):
                pass

            def foo(self):
                self.new()

            def bar(self):
                old()

        def old():
            pass
        """) == source.render()


def test_renames_method_in_imported_subclass():
    source = make_source("""
    class A:

        def old(self):
            pass
    """, module_name='foo')
    other_source = make_source("""
    from foo import A

    class B(A):

        def foo(self):
            self.old()

    class C(A):

        def old(self):
            pass

        def bar(self):
            self.old()
    """, module_name='bar')

    source.rename(
        row=3, column=8, new_name='new', additional_sources=[other_source])

    assert dedent("""
    class A:

        def new(self):
            pass
    """) == source.render()

    assert dedent("""
    from foo import A

    class B(A):

        def foo(self):
            self.new()

    class C(A):

        def old(self):
            pass

        def bar(self):
            self.old()
    """) == other_source.render()


def test_renames_method_in_renamed_instance_of_subclass():

    source = make_source("""
    class A:

        def old(self):
            pass

    class B(A):
        pass

    b = B()
    c = b
    c.old()
    """)

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
    class A:

        def new(self):
            pass

    class B(A):
        pass

    b = B()
    c = b
    c.new()
    """) == source.render()


@pytest.mark.skip
def test_does_not_rename_random_attributes():

    source = make_source("""
    import os

    path = os.path.dirname(__file__)
    """)

    source.rename(row=3, column=0, new_name='root')

    assert dedent("""
    import os

    root = os.path.dirname(__file__)
    """) == source.render()


# TODO: rename methods on super calls
# TODO: recognize 'cls' argument in @classmethods
# TODO: rename 'global' variables
# TODO: rename 'nonlocal' variables
# TODO: rename property setters
# TODO: import as
