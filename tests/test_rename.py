"""Tests for rename refactoring."""

from breakfast.rename import Renamer, Position


def dedent(code: str, *, by: int=4) -> str:
    return '\n'.join(l[by:] for l in code.split('\n'))


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

    renamer = Renamer(
        source=source, position=Position(2, 4), old_name='old', new_name='new')

    assert target == renamer.rename()


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

    renamer = Renamer(
        source=source,
        position=Position(1, 0),
        old_name='fun_old',
        new_name='fun_new')

    assert target == renamer.rename()


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

    renamer = Renamer(
        source=source,
        position=Position(1, 0),
        old_name='OldClass',
        new_name='NewClass')

    assert target == renamer.rename()


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

    renamer = Renamer(
        source=source,
        position=Position(1, 8),
        old_name='arg',
        new_name='new_arg')

    assert target == renamer.rename()


def test_renames_method():
    source = dedent("""
    class A:

        def old(self, arg):
            pass

    a = A()
    b = a.old()
    """)

    target = dedent("""
    class A:

        def new(self, arg):
            pass

    a = A()
    b = a.new()
    """)

    renamer = Renamer(
        source=source,
        position=Position(3, 4),
        old_name='old',
        new_name='new')

    assert target == renamer.rename()
