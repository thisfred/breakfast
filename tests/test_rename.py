"""Tests for rename refactoring."""

from breakfast.position import Position
from breakfast.rename import AttributeNames, NameCollector, State, rename
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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=4),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_function_from_lines():
    source = [
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"]

    sources = rename(
        sources={'module': Source(source)},
        position=Position(source, row=0, column=4),
        old_name='fun_old',
        new_name='fun_new')

    assert list(sources['module'].get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=4),
        old_name='fun_old',
        new_name='fun_new')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=6),
        old_name='OldClass',
        new_name='NewClass')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=8),
        old_name='arg',
        new_name='new_arg')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=8),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=0),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=8),
        old_name='arg',
        new_name='new_arg')

    assert sources['module'].render() == target


def test_renames_method():
    source = dedent("""
    class A:

        def old(self):
            pass

    a = A()
    a.old()
    """)

    target = dedent("""
    class A:

        def new(self):
            pass

    a = A()
    a.new()
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=3, column=8),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_only_the_right_method_definition_and_calls():
    source = dedent("""
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

    target = dedent("""
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
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=3, column=8),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=5, column=4),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


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

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=7, column=20),
        old_name='property',
        new_name='renamed')

    assert sources['module'].render() == target


def test_renames_dict_comprehension_variables():
    source = dedent("""
    old = 100
    foo = {old: None for old in range(100) if old % 3}
    """)

    target = dedent("""
    old = 100
    foo = {new: None for new in range(100) if new % 3}
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=7),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_set_comprehension_variables():
    source = dedent("""
    old = 100
    foo = {old for old in range(100) if old % 3}
    """)

    target = dedent("""
    old = 100
    foo = {new for new in range(100) if new % 3}
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=7),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_list_comprehension_variables():
    source = dedent("""
    old = 100
    foo = [
        old for old in range(100) if old % 3]
    """)

    target = dedent("""
    old = 100
    foo = [
        new for new in range(100) if new % 3]
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=3, column=4),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_only_desired_list_comprehension_variables():
    source = dedent("""
    old = 100
    foo = [
        old for old in range(100) if old % 3]
    bar = [
        old for old in range(100) if old % 3]
    """)

    target = dedent("""
    old = 100
    foo = [
        new for new in range(100) if new % 3]
    bar = [
        old for old in range(100) if old % 3]
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=3, column=4),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_for_loop_variables():
    source = dedent("""
    old = None
    for i, old in enumerate([]):
        print(i)
        print(old)
    """)

    target = dedent("""
    new = None
    for i, new in enumerate([]):
        print(i)
        print(new)
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=7),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_dotted_assignments():
    source = dedent("""
    class Foo:
        def bar(self):
            self.old = some.qux()
    """)

    target = dedent("""
    class Foo:
        def bar(self):
            self.new = some.qux()
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=3, column=13),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_tuple_unpack():
    source = dedent("""
    foo, old = 1, 2
    """)

    target = dedent("""
    foo, new = 1, 2
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=5),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_multiple_assign():
    source = dedent("""
    foo, old = old, foo
    """)

    target = dedent("""
    foo, new = new, foo
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=5),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_double_dotted_assignments():
    source = dedent("""
    def find_occurrences(old, position):
        for _, occurrences in old.positions.items():
            if position in occurrences:
                return occurrences
    """)

    target = dedent("""
    def find_occurrences(new, position):
        for _, occurrences in new.positions.items():
            if position in occurrences:
                return occurrences
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=26),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_subscript():
    source = dedent("""
    def old():
        return 1

    a = {}
    a[old()] = old()
    """)

    target = dedent("""
    def new():
        return 1

    a = {}
    a[new()] = new()
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=1, column=4),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_renames_enclosing_scope_variables_in_comprehensions():
    source = dedent("""
    old = 3
    foo = [foo for foo in range(100) if foo % old]
    """)

    target = dedent("""
    new = 3
    foo = [foo for foo in range(100) if foo % new]
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=2, column=42),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_multiple_calls():
    source = Source(["bar.names.copy().items()"])
    visitor = AttributeNames()
    assert visitor.collect(source.get_ast()) == (
        'bar', 'names', 'copy', 'items')


def test_dogfooding():
    """Test that we can at least parse our own code."""
    with open('breakfast/rename.py', 'r') as source:
        wrapped = Source(lines=[l[:-1] for l in source.readlines()])
        state = State()
        visitor = NameCollector(name='whatever', state=state)
        visitor.process(wrapped, 'rename')


def test_rename_across_files():
    files = {
        'foo': dedent(
            """
            def old():
                pass
            """),
        'bar': dedent(
            """
            from foo import old
            old()
            """)}

    files = {m: Source(f.split('\n')) for m, f in files.items()}
    sources = rename(
        sources=files,
        position=Position(files['bar'], row=2, column=0),
        old_name='old',
        new_name='new')

    assert sources['foo'].render() == dedent("""
        def new():
            pass
        """)
    assert sources['bar'].render() == dedent("""
        from foo import new
        new()
        """)


def test_rename_with_multiple_imports_on_one_line():
    files = {
        'foo': dedent(
            """
            def old():
                pass

            def bar():
                pass
            """),
        'bar': dedent(
            """
            from foo import bar, old
            old()
            bar()
            """)}

    files = {m: Source(f.split('\n')) for m, f in files.items()}
    sources = rename(
        sources=files,
        position=Position(files['bar'], row=2, column=0),
        old_name='old',
        new_name='new')

    assert sources['bar'].render() == dedent("""
        from foo import bar, new
        new()
        bar()
        """)
    assert sources['foo'].render() == dedent("""
        def new():
            pass

        def bar():
            pass
        """)


def test_renames_static_method():
    source = dedent("""
    class A:

        @staticmethod
        def old(arg):
            pass

    a = A()
    a.old('foo')
    """)

    target = dedent("""
    class A:

        @staticmethod
        def new(arg):
            pass

    a = A()
    a.new('foo')
    """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(source, row=4, column=8),
        old_name='old',
        new_name='new')

    assert sources['module'].render() == target


def test_fails_to_rename_builtins():
    source = dedent("""
        class A:

            def foo(self, arg):
                print(arg)
        """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(row=4, column=8, source=source),
        old_name='print',
        new_name='new')

    assert sources['module'].render() == source


def test_renames_argument():
    source = dedent("""
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """)

    target = dedent("""
        class A:

            def foo(self, new):
                print(new)

            def bar(self):
                arg = "1"
                self.foo(new=arg)
        """)

    sources = rename(
        sources={'module': Source(source.split('\n'))},
        position=Position(row=8, column=17, source=source),
        old_name='arg',
        new_name='new')

    assert sources['module'].render() == target
