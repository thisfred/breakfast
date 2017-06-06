from breakfast.names import Names, is_prefix
from breakfast.position import Position
from tests import make_source


def test_is_prefix_fails_when_paths_are_equal():
    assert not is_prefix((1, 2), (1, 2))


def test_is_prefix_fails_when_path_is_shorter_than_prefix():
    assert not is_prefix((1, 2), (1,))


def test_is_prefix_succeeds_when_prefix_prefixes_the_path():
    assert is_prefix((1, 2), (1, 2, 3))


def test_visit_source_adds_name():
    source = make_source("""
    a = 1
    """)
    visitor = Names()
    visitor.visit_source(source)
    assert 1 == len(visitor.get_occurrences('a', Position(source, 1, 0)))


def test_does_not_rename_random_attributes():
    source = make_source("""
    import os

    path = os.path.dirname(__file__)
    """)
    visitor = Names()

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences(
        'path',
        Position(source, 3, 0))

    assert [Position(source, 3, 0)] == occurrences


def test_finds_local_variable():
    source = make_source("""
    def fun():
        old = 12
        old2 = 13
        result = old + old2
        del old
        return result

    old = 20
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 2, 4),
        Position(source, 4, 13),
        Position(source, 5, 8)] == visitor.get_occurrences(
            'old',
            Position(source, 2, 4))


def test_finds_non_local_variable():
    source = make_source("""
    old = 12

    def fun():
        result = old + 1
        return result

    old = 20
    """)
    visitor = Names()

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 1, 0))

    assert [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0)] == occurrences


def test_finds_method_names():
    source = make_source("""
    class A:

        def old(self):
            pass

    unbound = A.old
    """)
    visitor = Names()

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 3, 8))

    assert [
        Position(source, 3, 8),
        Position(source, 6, 12)] == occurrences


def test_finds_parameters():
    source = make_source("""
    def fun(arg, arg2):
        return arg + arg2
    fun(arg=1, arg2=2)
    """)
    visitor = Names()

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences(
        'arg',
        Position(source, 1, 8))
    assert [
        Position(source, 1, 8),
        Position(source, 2, 11),
        Position(source, 3, 4)] == occurrences


def test_only_finds_parameter():
    source = make_source("""
    def fun(old=1):
        print(old)

    old = 8
    fun(old=old)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 8),
        Position(source, 2, 10),
        Position(source, 5, 4)] == visitor.get_occurrences(
            'old',
            Position(source, 1, 8))


def test_finds_function():
    source = make_source("""
    def fun_old():
        return 'result'
    result = fun_old()
    """)

    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 4),
        Position(source, 3, 9)] == visitor.get_occurrences(
            'fun_old',
            Position(source, 1, 4))


def test_finds_class():
    source = make_source("""
    class OldClass:
        pass

    instance = OldClass()
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 6),
        Position(source, 4, 11)] == visitor.get_occurrences(
            'OldClass',
            Position(source, 1, 6))


def test_finds_passed_argument():
    source = make_source("""
    old = 2
    def fun(arg, arg2):
        return arg + arg2
    fun(1, old)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 0),
        Position(source, 4, 7)] == visitor.get_occurrences(
            'old',
            Position(source, 1, 0))


def test_finds_parameter_with_unusual_indentation():
    source = make_source("""
    def fun(arg, arg2):
        return arg + arg2
    fun(
        arg=\\
            1,
        arg2=2)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 8),
        Position(source, 2, 11),
        Position(source, 4, 4)] == visitor.get_occurrences(
            'arg',
            Position(source, 1, 8))


def test_does_not_find_method_of_unrelated_class():
    source = make_source("""
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
    """)
    visitor = Names()

    visitor.visit_source(source)

    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 3, 8))

    assert [
        Position(source, 3, 8),
        Position(source, 7, 13),
        Position(source, 20, 2)] == occurrences


def test_finds_definition_from_call():
    source = make_source("""
    def old():
        pass

    def bar():
        old()
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 4),
        Position(source, 5, 4)] == visitor.get_occurrences(
            'old',
            Position(source, 5, 4))


def test_finds_attribute_assignments():
    source = make_source("""
    class ClassName:

        def __init__(self, property):
            self.property = property

        def get_property(self):
            return self.property
    """)
    visitor = Names()

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences(
        'property',
        Position(source, 7, 20))

    assert [
        Position(source, 4, 13),
        Position(source, 7, 20)] == occurrences


def test_finds_dict_comprehension_variables():
    source = make_source("""
    old = 100
    foo = {old: None for old in range(100) if old % 3}
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 2, 7),
        Position(source, 2, 21),
        Position(source, 2, 42)] == visitor.get_occurrences(
            'old',
            Position(source, 2, 42))


def test_finds_set_comprehension_variables():
    source = make_source("""
    old = 100
    foo = {old for old in range(100) if old % 3}
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 2, 7),
        Position(source, 2, 15),
        Position(source, 2, 36)] == visitor.get_occurrences(
            'old',
            Position(source, 2, 7))


def test_finds_list_comprehension_variables():
    source = make_source("""
    old = 100
    foo = [
        old for old in range(100) if old % 3]
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 3, 4),
        Position(source, 3, 12),
        Position(source, 3, 33)] == visitor.get_occurrences(
            'old',
            Position(source, 3, 12))


def test_finds_for_loop_variables():
    # Note that we could have chosen to treat the top level 'old' variable as
    # distinct from the loop variable, but since loop variables live on after
    # the loop, that would potentially change the behavior of the code.
    source = make_source("""
    old = None
    for i, old in enumerate(['foo']):
        print(i)
        print(old)
    print(old)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 0),
        Position(source, 2, 7),
        Position(source, 4, 10),
        Position(source, 5, 6)] == visitor.get_occurrences(
            'old',
            Position(source, 2, 7))


def test_finds_tuple_unpack():
    source = make_source("""
    foo, old = 1, 2
    print(old)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 1, 5),
        Position(source, 2, 6)] == visitor.get_occurrences(
            'old',
            Position(source, 1, 5))


def test_recognizes_multiple_assignments():
    source = make_source("""
    class A:
        def old(self):
            pass

    class B:
        def old(self):
            pass

    foo, bar = A(), B()
    foo.old()
    bar.old()
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 2, 8),
        Position(source, 10, 4)] == visitor.get_occurrences(
            'old',
            Position(source, 2, 8))


def test_finds_enclosing_scope_variable_from_comprehension():
    source = make_source("""
    old = 3
    res = [foo for foo in range(100) if foo % old]
    """)
    visitor = Names()

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 2, 42))

    assert [
        Position(source, 1, 0),
        Position(source, 2, 42)] == occurrences


def test_finds_across_files():
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
    visitor = Names()

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    assert [
        Position(other_source, 1, 16),
        Position(other_source, 2, 0),
        Position(source, 1, 4)] == visitor.get_occurrences(
            'old',
            Position(other_source, 2, 0))


def test_finds_multiple_imports_on_one_line():
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
    visitor = Names()

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    assert [
        Position(other_source, 1, 21),
        Position(other_source, 2, 0),
        Position(source, 1, 4)] == visitor.get_occurrences(
            'old',
            Position(other_source, 2, 0))


def test_finds_static_method():
    source = make_source("""
    class A:

        @staticmethod
        def old(arg):
            pass

    a = A()
    a.old('foo')
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 4, 8),
        Position(source, 8, 2)] == visitor.get_occurrences(
            'old',
            Position(source, 4, 8))


def test_finds_argument():
    source = make_source("""
    class A:

        def foo(self, arg):
            print(arg)

        def bar(self):
            arg = "1"
            self.foo(arg=arg)
    """)
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 3, 18),
        Position(source, 4, 14),
        Position(source, 8, 17)] == visitor.get_occurrences(
            'arg',
            Position(source, 8, 17))


def test_finds_method_but_not_function():
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
    visitor = Names()

    visitor.visit_source(source)

    assert [
        Position(source, 3, 8),
        Position(source, 7, 13)] == visitor.get_occurrences(
            'old',
            Position(source, 3, 8))


def test_fails_to_rename_builtins():
    source = make_source("""
        class A:

            def foo(self, arg):
                print(arg)
        """)
    visitor = Names()

    visitor.visit_source(source)

    assert [] == visitor.get_occurrences('print', Position(source, 4, 8))


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
    visitor = Names()

    visitor.visit_source(source)
    visitor.visit_source(other_source)

    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 3, 8))
    assert [
        Position(other_source, 6, 13),
        Position(source, 3, 8)] == occurrences


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

    visitor = Names()

    visitor.visit_source(source)
    occurrences = visitor.get_occurrences(
        'old',
        Position(source, 3, 8))

    assert [
        Position(source, 3, 8),
        Position(source, 11, 2)] == occurrences

# TODO: rename variable from global scope in method
# TODO: rename methods on super calls
# TODO: rename methods from multiple inheritance
# TODO: recognize 'cls' argument in @classmethods
# TODO: rename 'global' variables
# TODO: rename 'nonlocal' variables
# TODO: rename property setters
# TODO: import as
