"""Tests for rename refactoring."""


from breakfast.rename import AttributeNames
from breakfast.source import Source
from tests import dedent, make_source


def test_renames_local_variable_in_function():
    source = make_source("""
    def fun():
        old = 12
        old2 = 13
        result = old + old2
        del old
        return result
    """)

    source.rename(row=2, column=4, new_name='new')

    assert dedent("""
        def fun():
            new = 12
            old2 = 13
            result = new + old2
            del new
            return result
        """) == source.render()


def test_renames_function_from_lines():
    source = Source([
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"])

    source.rename(row=0, column=4, new_name='fun_new')

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]


def test_renames_function():
    source = make_source("""
    def fun_old():
        return 'result'
    result = fun_old()
    """)

    source.rename(row=1, column=4, new_name='fun_new')

    assert dedent("""
        def fun_new():
            return 'result'
        result = fun_new()
        """) == source.render()


def test_renames_class():
    source = make_source("""
    class OldClass:
        pass

    instance = OldClass()
    """)

    source.rename(row=1, column=6, new_name='NewClass')

    assert dedent("""
        class NewClass:
            pass

        instance = NewClass()
        """) == source.render()


def test_renames_parameters():
    source = make_source("""
    def fun(arg, arg2):
        return arg + arg2
    fun(arg=1, arg2=2)
    """)

    source.rename(row=1, column=8, new_name='new_arg')

    assert dedent("""
        def fun(new_arg, arg2):
            return new_arg + arg2
        fun(new_arg=1, arg2=2)
        """) == source.render()


def test_does_not_rename_argument():
    source = make_source("""
    def fun(old=1):
        print(old)

    old = 8
    fun(old=old)
    """)

    source.rename(row=1, column=8, new_name='new')

    assert dedent("""
        def fun(new=1):
            print(new)

        old = 8
        fun(new=old)
        """) == source.render()


def test_renames_passed_argument():

    source = make_source("""
    old = 2
    def fun(arg, arg2):
        return arg + arg2
    fun(1, old)
    """)

    source.rename(row=1, column=0, new_name='new')

    assert dedent("""
        new = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, new)
        """) == source.render()


def test_renames_parameter_with_unusual_indentation():
    source = make_source("""
    def fun(arg, arg2):
        return arg + arg2
    fun(
        arg=\\
            1,
        arg2=2)
    """)

    source.rename(row=1, column=8, new_name='new_arg')

    assert dedent("""
        def fun(new_arg, arg2):
            return new_arg + arg2
        fun(
            new_arg=\\
                1,
            arg2=2)
        """) == source.render()


def test_renames_method():
    source = make_source("""
    class A:

        def old(self):
            pass

    a = A()
    a.old()
    """)

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
        class A:

            def new(self):
                pass

        a = A()
        a.new()
        """) == source.render()


def test_renames_only_the_right_method_definition_and_calls():
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

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
        class ClassThatShouldHaveMethodRenamed:

            def new(self, arg):
                pass

            def foo(self):
                self.new('whatever')


        class UnrelatedClass:

            def old(self, arg):
                pass

            def foo(self):
                self.old('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.new()
        b = UnrelatedClass()
        b.old()
        """) == source.render()


def test_renames_from_inner_scope():
    source = make_source("""
    def old():
        pass

    def bar():
        old()
    """)

    source.rename(row=5, column=4, new_name='new')

    assert dedent("""
        def new():
            pass

        def bar():
            new()
        """) == source.render()


def test_renames_attributes():
    source = make_source("""
    class ClassName:

        def __init__(self, property):
            self.property = property

        def get_property(self):
            return self.property
    """)

    source.rename(row=7, column=20, new_name='renamed')

    assert dedent("""
        class ClassName:

            def __init__(self, property):
                self.renamed = property

            def get_property(self):
                return self.renamed
        """) == source.render()


def test_renames_dict_comprehension_variables():
    source = make_source("""
    old = 100
    foo = {old: None for old in range(100) if old % 3}
    """)

    source.rename(row=2, column=7, new_name='new')

    assert dedent("""
        old = 100
        foo = {new: None for new in range(100) if new % 3}
        """) == source.render()


def test_renames_set_comprehension_variables():
    source = make_source("""
    old = 100
    foo = {old for old in range(100) if old % 3}
    """)

    source.rename(row=2, column=7, new_name='new')

    assert dedent("""
        old = 100
        foo = {new for new in range(100) if new % 3}
        """) == source.render()


def test_renames_list_comprehension_variables():
    source = make_source("""
    old = 100
    foo = [
        old for old in range(100) if old % 3]
    """)

    source.rename(row=3, column=4, new_name='new')

    assert dedent("""
        old = 100
        foo = [
            new for new in range(100) if new % 3]
        """) == source.render()


def test_renames_only_desired_list_comprehension_variables():
    source = make_source("""
    old = 100
    foo = [
        old for old in range(100) if old % 3]
    bar = [
        old for old in range(100) if old % 3]
    """)

    source.rename(row=3, column=4, new_name='new')

    assert dedent("""
        old = 100
        foo = [
            new for new in range(100) if new % 3]
        bar = [
            old for old in range(100) if old % 3]
        """) == source.render()


def test_renames_for_loop_variables():
    source = make_source("""
    old = None
    for i, old in enumerate([]):
        print(i)
        print(old)
    """)

    source.rename(row=2, column=7, new_name='new')

    assert dedent("""
        new = None
        for i, new in enumerate([]):
            print(i)
            print(new)
        """) == source.render()


def test_renames_dotted_assignments():
    source = make_source("""
    class Foo:
        def bar(self):
            self.old = some.qux()
    """)

    source.rename(row=3, column=13, new_name='new')

    assert dedent("""
        class Foo:
            def bar(self):
                self.new = some.qux()
        """) == source.render()


def test_renames_tuple_unpack():
    source = make_source("""
    foo, old = 1, 2
    """)

    source.rename(row=1, column=5, new_name='new')

    assert dedent("""
        foo, new = 1, 2
        """) == source.render()


def test_renames_double_dotted_assignments():
    source = make_source("""
    def find_occurrences(old, position):
        for _, occurrences in old.positions.items():
            if position in occurrences:
                return occurrences
    """)

    source.rename(row=2, column=26, new_name='new')

    assert dedent("""
        def find_occurrences(new, position):
            for _, occurrences in new.positions.items():
                if position in occurrences:
                    return occurrences
        """) == source.render()


def test_renames_subscript():
    source = make_source("""
    def old():
        return 1

    a = {}
    a[old()] = old()
    """)

    source.rename(row=1, column=4, new_name='new')

    assert dedent("""
        def new():
            return 1

        a = {}
        a[new()] = new()
        """) == source.render()


def test_renames_enclosing_scope_variables_in_comprehensions():
    source = make_source("""
    old = 3
    foo = [foo for foo in range(100) if foo % old]
    """)

    source.rename(row=2, column=42, new_name='new')

    assert dedent("""
        new = 3
        foo = [foo for foo in range(100) if foo % new]
        """) == source.render()


def test_multiple_calls():
    source = Source(["bar.names.copy().items()"])
    visitor = AttributeNames()
    assert (
        'bar', 'names', 'copy', 'items') == visitor.collect(source.get_ast())


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


def test_renames_method_in_subclass():
    source = make_source("""
    class A:

        def old(self):
            pass

    class B(A):

        def foo(self):
            self.old()

    class C(A):

        def old(self):
            pass

        def bar(self):
            self.old()
    """)

    source.rename(row=3, column=8, new_name='new')

    assert dedent("""
    class A:

        def new(self):
            pass

    class B(A):

        def foo(self):
            self.new()

    class C(A):

        def old(self):
            pass

        def bar(self):
            self.old()
    """) == source.render()

# TODO: rename @properties
# TODO: rename class variables
# TODO: recognize 'cls' argument in @classmethods
# TODO: rename 'global' variables
