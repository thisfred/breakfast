"""Tests for rename refactoring."""

from breakfast.position import Position
from breakfast.source import Source

from tests import dedent


def test_renames_local_variable_in_function():
    source = dedent("""
    def fun():
        old = 12
        old2 = 13
        result = old + old2
        del old
        return result
    """)

    target = dedent("""
    def fun():
        new = 12
        old2 = 13
        result = new + old2
        del new
        return result
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=2, column=4),
        old_name='old',
        new_name='new')


def test_renames_function_from_list():
    source = Source.from_lines([
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"])

    source.rename(
        cursor=Position(row=0, column=4),
        old_name='fun_old',
        new_name='fun_new')

    assert [
        (0, "def fun_new():"),
        (2, "result = fun_new()")] == list(source.get_changes())


def test_renames_function():
    source = dedent("""
    def fun_old():
        return 'result'
    result = fun_old()
    """)

    target = dedent("""
    def fun_new():
        return 'result'
    result = fun_new()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=4),
        old_name='fun_old',
        new_name='fun_new')


def test_renames_from_any_character_in_the_name():
    source = dedent("""
    def fun_old():
        return 'result'
    result = fun_old()
    """)

    target = dedent("""
    def fun_new():
        return 'result'
    result = fun_new()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=7),
        old_name='fun_old',
        new_name='fun_new')


def test_renames_class():
    source = dedent("""
    class OldClass:
        pass

    instance = OldClass()
    """)

    target = dedent("""
    class NewClass:
        pass

    instance = NewClass()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=6),
        old_name='OldClass',
        new_name='NewClass')


def test_renames_parameters():
    source = dedent("""
    def fun(arg, arg2):
        return arg + arg2
    fun(arg=1, arg2=2)
    """)

    target = dedent("""
    def fun(new_arg, arg2):
        return new_arg + arg2
    fun(new_arg=1, arg2=2)
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=8),
        old_name='arg',
        new_name='new_arg')


def test_does_not_rename_argument():
    source = dedent("""
    def fun(old=1):
        print(old)

    old = 8
    fun(old=old)
    """)

    target = dedent("""
    def fun(new=1):
        print(new)

    old = 8
    fun(new=old)
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=10),
        old_name='old',
        new_name='new')


def test_renames_passed_argument():

    source = dedent("""
    old = 2
    def fun(arg, arg2):
        return arg + arg2
    fun(1, old)
    """)

    target = dedent("""
    new = 2
    def fun(arg, arg2):
        return arg + arg2
    fun(1, new)
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=0),
        old_name='old',
        new_name='new')


def test_renames_parameter_with_unusual_indentation():
    source = dedent("""
    def fun(arg, arg2):
        return arg + arg2
    fun(
        arg=\\
            1,
        arg2=2)
    """)

    target = dedent("""
    def fun(new_arg, arg2):
        return new_arg + arg2
    fun(
        new_arg=\\
            1,
        arg2=2)
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=1, column=8),
        old_name='arg',
        new_name='new_arg')


def test_renames_method():
    source = dedent("""
    class A:

        def old(self, arg):
            pass

    a = A()
    a.old()
    """)

    target = dedent("""
    class A:

        def new(self, arg):
            pass

    a = A()
    a.new()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=3, column=8),
        old_name='old',
        new_name='new')


def test_renames_only_the_right_method_definition_and_calls():
    source = dedent("""
    class ClassThatShouldHaveMethodRenamed:

        def old(self, arg):
            pass


    class UnrelatedClass:

        def old(self, arg):
            pass


    a = ClassThatShouldHaveMethodRenamed()
    a.old()
    b = UnrelatedClass()
    b.old()
    """)

    target = dedent("""
    class ClassThatShouldHaveMethodRenamed:

        def new(self, arg):
            pass


    class UnrelatedClass:

        def old(self, arg):
            pass


    a = ClassThatShouldHaveMethodRenamed()
    a.new()
    b = UnrelatedClass()
    b.old()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=3, column=8),
        old_name='old',
        new_name='new')


def test_renames_from_inner_scope():
    source = dedent("""
    def old():
        pass

    def bar():
        old()
    """)

    target = dedent("""
    def new():
        pass

    def bar():
        new()
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=5, column=4),
        old_name='old',
        new_name='new')


def test_renames_attributes():
    source = dedent("""
    class ClassName:

        def __init__(self, property):
            self.property = property

        def get_property(self):
            return self.property
    """)

    target = dedent("""
    class ClassName:

        def __init__(self, property):
            self.renamed = property

        def get_property(self):
            return self.renamed
    """)

    assert target == rename(
        source=source,
        cursor=Position(row=7, column=26),
        old_name='property',
        new_name='renamed')


def rename(source, cursor, old_name, new_name):
    wrapped_source = Source(source)
    wrapped_source.rename(cursor, old_name, new_name)
    return wrapped_source.render()
