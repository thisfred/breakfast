"""Tests for rename refactoring."""

from breakfast.position import Position
from breakfast.rename import NameCollector, Rename
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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=4),
        old_name='old',
        new_name='new')


def test_renames_function_from_lines():
    refactoring = Rename(
        files={'module': [
            "def fun_old():",
            "    return 'result'",
            "result = fun_old()"]})
    refactoring.initialize(
        module='module',
        position=Position(row=0, column=4),
        old_name='fun_old',
        new_name='fun_new')

    refactoring.apply()

    assert [
        (0, "def fun_new():"),
        (2, "result = fun_new()")] == list(refactoring.get_changes('module'))


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=1, column=4),
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=1, column=8),
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=7, column=20),
        old_name='property',
        new_name='renamed')


def test_renames_dict_comprehension_variables():
    source = dedent("""
    old = 100
    foo = {old: None for old in range(100) if old % 3}
    """)

    target = dedent("""
    old = 100
    foo = {new: None for new in range(100) if new % 3}
    """)

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=7),
        old_name='old',
        new_name='new')


def test_renames_set_comprehension_variables():
    source = dedent("""
    old = 100
    foo = {old for old in range(100) if old % 3}
    """)

    target = dedent("""
    old = 100
    foo = {new for new in range(100) if new % 3}
    """)

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=7),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=3, column=4),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=3, column=4),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=7),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=3, column=13),
        old_name='old',
        new_name='new')


def test_renames_tuple_unpack():
    source = dedent("""
    foo, old = 1, 2
    """)

    target = dedent("""
    foo, new = 1, 2
    """)

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=1, column=5),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=26),
        old_name='old',
        new_name='new')


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

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=1, column=4),
        old_name='old',
        new_name='new')


def test_renames_enclosing_scope_variables_in_comprehensions():
    source = dedent("""
    old = 3
    foo = [foo for foo in range(100) if foo % old]
    """)

    target = dedent("""
    new = 3
    foo = [foo for foo in range(100) if foo % new]
    """)

    assert target == rename_in_single_file(
        source=source,
        cursor=Position(row=2, column=42),
        old_name='old',
        new_name='new')


def test_dogfooding():
    """Test that we can at least parse our own code."""
    with open('breakfast/rename.py', 'r') as source:
        wrapped = Source(lines=[l[:-1] for l in source.readlines()])
        visitor = NameCollector('position')
        visitor.set_source(wrapped)
        visitor.collect_occurrences()


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

    refactoring = Rename(files={m: f.split('\n') for m, f in files.items()})
    refactoring.initialize(
        module='bar',
        position=Position(row=2, column=0),
        old_name='old',
        new_name='new')
    refactoring.apply()
    assert refactoring.get_result('foo') == dedent("""
        def new():
            pass
        """)
    assert refactoring.get_result('bar') == dedent("""
        from foo import new
        new()
        """)


def rename_in_single_file(source, cursor, old_name, new_name):
    refactoring = Rename(files={'module': source.split('\n')})
    refactoring.initialize(
        module='module',
        position=cursor,
        old_name=old_name,
        new_name=new_name)
    refactoring.apply()
    return refactoring.get_result('module')
