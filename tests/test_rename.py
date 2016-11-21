"""Tests for rename refactoring."""

from breakfast.rename import NameVisitor, Position, modified, rename
import pytest


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
    source = [
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"]

    target = [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]

    assert target == [
        change for change in modified(
            source=source,
            cursor=Position(row=0, column=4),
            old_name='fun_old',
            new_name='fun_new')]


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


def test_renames_parameter():
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


@pytest.mark.skip("TODO")
def test_renames():
    source = dedent("""
    def test(old=1):
        print(old)

    old = 8
    test(old=old)
    """)

    target = dedent("""
    def test(new=1):
        print(new)

    old = 8
    test(new=old)
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


def test_raises_key_error():
    visitor = NameVisitor("foo")
    missing_position = Position(row=8, column=4)
    with pytest.raises(KeyError):
        visitor.determine_scope(missing_position)


def dedent(code: str, *, by: int=4) -> str:
    return '\n'.join(l[by:] for l in code.split('\n'))
