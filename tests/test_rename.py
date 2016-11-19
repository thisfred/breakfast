"""Tests for rename refactoring."""

from breakfast.rename import Position, rename


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
        new_name='new')


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
        new_name='new_arg')


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
        new_name='new')


def dedent(code: str, *, by: int=4) -> str:
    return '\n'.join(l[by:] for l in code.split('\n'))
