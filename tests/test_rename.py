"""Tests for rename refactoring."""

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


def test_fails_to_rename_builtins():
    source_text = dedent("""
        class A:

            def foo(self, arg):
                print(arg)
        """)

    source = Source(source_text.split('\n'))
    source.rename(row=4, column=8, new_name='new')

    assert source_text == source.render()


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
