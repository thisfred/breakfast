from __future__ import annotations

import logging
import sys

from pytest import mark

from breakfast.names import NameCollector, all_occurrence_positions
from breakfast.project import Project
from breakfast.source import Position
from tests.conftest import (
    assert_renames_to,
    make_source,
)

logger = logging.getLogger(__name__)

STATIC_METHOD = "staticmethod"


def test_should_find_occurrences_along_longer_import_paths():
    source1 = make_source(
        """
    from cooking.kitchen import Stove

    stove = Stove()
    stove.broil()
    """,
        filename="cooking/chef.py",
    )
    source2 = make_source(
        """
    from cooking.stove import *
    """,
        filename="cooking/kitchen.py",
    )
    source3 = make_source(
        """
    class Stove:
        def bake():
            pass

        def broil():
            pass

        def saute():
            pass
    """,
        filename="cooking/stove.py",
    )
    positions = all_occurrence_positions(
        Position(source=source1, row=4, column=6),
        sources=[source1, source2, source3],
    )
    assert positions == [
        Position(source=source1, row=4, column=6),
        Position(source=source3, row=5, column=8),
    ]


def test_should_find_occurrences_along_relative_import_paths():
    source1 = make_source(
        """
        from ..d.e import C

        c = C()
        """,
        filename="a/b/c.py",
    )
    source2 = make_source(
        """
        class C:
            ...
        """,
        filename="a/d/e.py",
    )
    positions = all_occurrence_positions(
        Position(source=source2, row=1, column=6), sources=[source1, source2]
    )
    assert positions == [
        Position(source=source1, row=1, column=18),
        Position(source=source1, row=3, column=4),
        Position(source=source2, row=1, column=6),
    ]


def test_kwarg_value():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = 12

        def fun(b=var):
            foo = b
            return foo
        """,
        expected="""
        renamed = 12

        def fun(b=renamed):
            foo = b
            return foo
        """,
    )


def test_finds_global_variable_usage_from_definition():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = 12

        def fun():
            global var
            foo = var
        """,
        expected="""
        renamed = 12

        def fun():
            global renamed
            foo = renamed
        """,
    )


def test_finds_global_variable_from_local_usage():
    assert_renames_to(
        target="var",
        new="renamed",
        occurrence=3,
        code="""
        var = 12

        def fun():
            global var
            var = 10
        """,
        expected="""
        renamed = 12

        def fun():
            global renamed
            renamed = 10
        """,
    )


def test_reassignment_should_rename_local_variable():
    assert_renames_to(
        target="a",
        new="renamed",
        occurrence=3,
        code="""
        a = 0
        def fun():
            a = 1
            a = 2
        """,
        expected="""
        a = 0
        def fun():
            renamed = 1
            renamed = 2
        """,
    )


def test_distinguishes_local_variables_from_global():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        def fun():
            var = 12
            var2 = 13
            result = var + var2
            del var
            return result

        var = 20
        """,
        expected="""
        def fun():
            renamed = 12
            var2 = 13
            result = renamed + var2
            del renamed
            return result

        var = 20
        """,
    )


def test_finds_non_local_variable():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = 12

        def fun():
            result = var + 1
            return result

        var = 20
        """,
        expected="""
        renamed = 12

        def fun():
            result = renamed + 1
            return result

        renamed = 20
        """,
    )


def test_finds_non_local_variable_defined_after_use():
    assert_renames_to(
        target="var",
        occurrence=2,
        new="renamed",
        code="""
        def fun():
            result = var + 1
            return result

        var = 20
        """,
        expected="""
        def fun():
            result = renamed + 1
            return result

        renamed = 20
        """,
    )


def test_does_not_rename_random_attributes():
    assert_renames_to(
        target="path",
        new="renamed",
        code="""
        import os

        path = os.path.dirname(__file__)
        """,
        expected="""
        import os

        renamed = os.path.dirname(__file__)
        """,
    )


def test_finds_parameter():
    assert_renames_to(
        target="arg",
        new="renamed",
        code="""
        def fun(arg=1):
            print(arg)

        arg = 8
        fun(arg=arg)
        """,
        expected="""
        def fun(renamed=1):
            print(renamed)

        arg = 8
        fun(renamed=arg)
        """,
    )


def test_finds_function():
    assert_renames_to(
        target="fun",
        new="renamed",
        code="""
        def fun():
            return 'result'
        result = fun()
        """,
        expected="""
        def renamed():
            return 'result'
        result = renamed()
        """,
    )


def test_finds_class():
    assert_renames_to(
        target="Class",
        new="Renamed",
        code="""
        class Class:
            pass

        instance = Class()
        """,
        expected="""
        class Renamed:
            pass

        instance = Renamed()
        """,
    )


def test_finds_method_name():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class A:

            def method(self):
                pass

        unbound = A.method
        """,
        expected="""
        class A:

            def renamed(self):
                pass

        unbound = A.renamed
        """,
    )


def test_finds_passed_argument():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, var)
        """,
        expected="""
        renamed = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, renamed)
        """,
    )


def test_does_not_find_method_of_unrelated_class():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class ClassThatShouldHaveMethodRenamed:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        class UnrelatedClass:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.method()
        b = UnrelatedClass()
        b.method()
        """,
        expected="""
        class ClassThatShouldHaveMethodRenamed:

            def renamed(self, arg):
                pass

            def foo(self):
                self.renamed('whatever')


        class UnrelatedClass:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.renamed()
        b = UnrelatedClass()
        b.method()
        """,
    )


def test_finds_definition_from_call():
    assert_renames_to(
        target="fun",
        occurrence=2,
        new="renamed",
        code="""
        def fun():
            pass

        def bar():
            fun()
        """,
        expected="""
        def renamed():
            pass

        def bar():
            renamed()
        """,
    )


def test_considers_self_properties_instance_properties():
    assert_renames_to(
        target="property",
        occurrence=2,
        new="renamed",
        code="""
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """,
        expected="""
        class ClassName:

            def __init__(self, property):
                self.renamed = property

            def get_property(self):
                return self.renamed
        """,
    )


def test_should_find_instance_properties_that_are_assigned_to():
    assert_renames_to(
        target="property",
        occurrence=2,
        new="renamed",
        code="""
        class ClassName:

            def __init__(self, property):
                self.property = property

            def set_property(self):
                self.property = wat
        """,
        expected="""
        class ClassName:

            def __init__(self, property):
                self.renamed = property

            def set_property(self):
                self.renamed = wat
        """,
    )


def test_should_find_class_attribute_when_assigned_to():
    assert_renames_to(
        target="attr",
        occurrence=2,
        new="renamed",
        code="""
        class ClassName:
            attr = None

            def method(self, *, arg):
                self.attr = arg
        """,
        expected="""
        class ClassName:
            renamed = None

            def method(self, *, arg):
                self.renamed = arg
        """,
    )


def test_finds_value_assigned_to_property():
    assert_renames_to(
        target="property",
        new="renamed",
        code="""
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """,
        expected="""
        class ClassName:

            def __init__(self, renamed):
                self.property = renamed

            def get_property(self):
                return self.property
        """,
    )


def test_finds_dict_comprehension_variables():
    assert_renames_to(
        target="var",
        occurrence=2,
        new="renamed",
        code="""
        var = 1
        foo = {var: None for var in range(100) if var % 3}
        var = 2
        """,
        expected="""
        var = 1
        foo = {renamed: None for renamed in range(100) if renamed % 3}
        var = 2
        """,
    )


def test_finds_list_comprehension_variables():
    assert_renames_to(
        target="var",
        occurrence=3,
        new="renamed",
        code="""
        var = 100
        foo = [
            var for var in range(100) if var % 3]
        var = 200
        """,
        expected="""
        var = 100
        foo = [
            renamed for renamed in range(100) if renamed % 3]
        var = 200
        """,
    )


def test_finds_set_comprehension_variables():
    assert_renames_to(
        target="var",
        occurrence=3,
        new="renamed",
        code="""
        var = 100
        foo = {var for var in range(100) if var % 3}
        """,
        expected="""
        var = 100
        foo = {renamed for renamed in range(100) if renamed % 3}
        """,
    )


def test_finds_generator_expression_variables():
    assert_renames_to(
        target="var",
        occurrence=3,
        new="renamed",
        code="""
        var = 100
        foo = (var for var in range(100) if var % 3)
        """,
        expected="""
        var = 100
        foo = (renamed for renamed in range(100) if renamed % 3)
        """,
    )


def test_finds_loop_variables_outside_loop():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = None
        for var in ['foo']:
            print(var)
        print(var)
        """,
        expected="""
        renamed = None
        for renamed in ['foo']:
            print(renamed)
        print(renamed)
        """,
    )


def test_finds_loop_variables():
    assert_renames_to(
        target="a",
        new="renamed",
        code="""
        for a in []:
            print(a)
        """,
        expected="""
        for renamed in []:
            print(renamed)
        """,
    )


def test_finds_tuple_unpack():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        foo, var = 1, 2
        print(var)
        """,
        expected="""
        foo, renamed = 1, 2
        print(renamed)
        """,
    )


def test_finds_superclasses():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class A:
            def method(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.method()
        """,
        expected="""
        class A:
            def renamed(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.renamed()
        """,
    )


def test_recognizes_multiple_assignment_1():
    assert_renames_to(
        target="a",
        new="renamed",
        code="""
        a = 1
        foo, bar = a, a
        """,
        expected="""
        renamed = 1
        foo, bar = renamed, renamed
        """,
    )


def test_recognizes_multiple_assignments():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class A:
            def method(self):
                pass

        class B:
            def method(self):
                pass

        foo, bar = A(), B()
        foo.method()
        bar.method()
        """,
        expected="""
        class A:
            def renamed(self):
                pass

        class B:
            def method(self):
                pass

        foo, bar = A(), B()
        foo.renamed()
        bar.method()
        """,
    )


def test_finds_enclosing_scope_variable_from_comprehension():
    assert_renames_to(
        target="var",
        new="renamed",
        code="""
        var = 3
        res = [foo for foo in range(100) if foo % var]
        """,
        expected="""
        renamed = 3
        res = [foo for foo in range(100) if foo % renamed]
        """,
    )


def test_finds_static_method():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class A:

            @staticmethod
            def method(arg):
                pass

        a = A()
        b = a.method('foo')
        """,
        expected="""
        class A:

            @staticmethod
            def renamed(arg):
                pass

        a = A()
        b = a.renamed('foo')
        """,
    )


def test_finds_method_after_call():
    assert_renames_to(
        target="method",
        new="renamed",
        code="""
        class A:

            def method(arg):
                pass

        b = A().method('foo')
        """,
        expected="""
        class A:

            def renamed(arg):
                pass

        b = A().renamed('foo')
        """,
    )


def test_finds_argument():
    assert_renames_to(
        target="arg",
        new="renamed",
        code="""
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """,
        expected="""
        class A:

            def foo(self, renamed):
                print(renamed)

            def bar(self):
                arg = "1"
                self.foo(renamed=arg)
        """,
    )


def test_finds_method_but_not_function():
    assert_renames_to(
        target="old",
        new="renamed",
        code="""
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """,
        expected="""
        class A:

            def renamed(self):
                pass

            def foo(self):
                self.renamed()

            def bar(self):
                old()

        def old():
            pass
        """,
    )


def test_finds_global_variable_in_method_scope():
    assert_renames_to(
        target="b",
        new="renamed",
        code="""
        b = 12

        class Foo:

            def bar(self):
                return b
        """,
        expected="""
        renamed = 12

        class Foo:

            def bar(self):
                return renamed
        """,
    )


def test_treats_staticmethod_args_correctly():
    assert_renames_to(
        target="old",
        new="renamed",
        code="""
        class ClassName:

            def old(self):
                pass

            @staticmethod
            def foo(whatever):
                whatever.old()
        """,
        expected="""
        class ClassName:

            def renamed(self):
                pass

            @staticmethod
            def foo(whatever):
                whatever.old()
        """,
    )


def test_finds_nonlocal_variable():
    assert_renames_to(
        target="b",
        occurrence=2,
        new="renamed",
        code="""
        b = 12

        def foo():
            b = 20
            def bar():
                nonlocal b
                b = 20
            b = 1
            return b

        print(b)
        """,
        expected="""
        b = 12

        def foo():
            renamed = 20
            def bar():
                nonlocal renamed
                renamed = 20
            renamed = 1
            return renamed

        print(b)
        """,
    )


def test_finds_multiple_definitions():
    assert_renames_to(
        target="b",
        new="renamed",
        code="""
        a = 12
        if a > 10:
            b = a + 100
        else:
            b = 3 - a
        print(b)
        """,
        expected="""
        a = 12
        if a > 10:
            renamed = a + 100
        else:
            renamed = 3 - a
        print(renamed)
        """,
    )


def test_finds_method_in_super_call():
    assert_renames_to(
        target="bar",
        new="renamed",
        code="""
        class Foo:

            def bar(self):
                pass


        class Bar(Foo):

            def bar(self):
                super().bar()
        """,
        expected="""
        class Foo:

            def renamed(self):
                pass


        class Bar(Foo):

            def renamed(self):
                super().renamed()
        """,
    )


def test_does_not_rename_imported_names():
    source1 = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """,
        filename="c.py",
    )
    source2 = make_source(
        """
        b = 2
        """,
        filename="a.py",
    )
    positions = all_occurrence_positions(
        Position(source=source1, row=5, column=4), sources=[source1, source2]
    )

    assert positions == [
        Position(source=source1, row=5, column=4),
        Position(source=source1, row=6, column=10),
    ]


def test_finds_namespace_imports():
    source1 = make_source(
        """
        def old():
            pass
        """,
        filename="foo.py",
    )
    source2 = make_source(
        """
        import foo
        foo.old()
        """,
        filename="bar.py",
    )
    position = Position(source=source1, row=1, column=4)
    assert all_occurrence_positions(
        position, sources=[source1, source2]
    ) == sorted(
        [
            Position(source=source1, row=1, column=4),
            Position(source=source2, row=2, column=4),
        ]
    )


def test_finds_default_values():
    assert_renames_to(
        target="v",
        new="renamed",
        code="""
        v = 0

        def f(a=v):
            ...
        """,
        expected="""
        renamed = 0

        def f(a=renamed):
            ...
        """,
    )


def test_finds_keyword_argument_values():
    assert_renames_to(
        target="v",
        new="renamed",
        code="""
        v = 0

        f(a=v)
        """,
        expected="""
        renamed = 0

        f(a=renamed)
        """,
    )


def test_finds_unpacked_names():
    assert_renames_to(
        target="a",
        new="renamed",
        code="""
        for a, b in thing:
            print(a)
        """,
        expected="""
        for renamed, b in thing:
            print(renamed)
        """,
    )


def test_unicode_strings():
    assert_renames_to(
        target="node",
        new="renamed",
        code="""
        node = Thing()
        var = "↑" + node.attr
        """,
        expected="""
        renamed = Thing()
        var = "↑" + renamed.attr
        """,
    )


def test_pattern_matching_should_only_find_occurrences_in_a_single_case():
    assert_renames_to(
        target="a",
        new="renamed",
        code="""
        match thing:
            case a if a > 2:
                print(a)

            case a:
                print(a)
        """,
        expected="""
        match thing:
            case renamed if renamed > 2:
                print(renamed)

            case a:
                print(a)
        """,
    )


def test_should_find_class_used_in_method_annotation():
    assert_renames_to(
        target="C",
        new="Renamed",
        code="""
        class C:
            ...

        class D:
            def f(self, c: C) -> C:
                ...
        """,
        expected="""
        class Renamed:
            ...

        class D:
            def f(self, c: Renamed) -> Renamed:
                ...
        """,
    )


def test_should_find_class_used_in_return_annotation():
    assert_renames_to(
        target="C",
        new="Renamed",
        code="""
        class C:
            ...

        def f() -> C:
            ...
        """,
        expected="""
        class Renamed:
            ...

        def f() -> Renamed:
            ...
        """,
    )


def test_none_type_annotation_should_not_break_things():
    assert_renames_to(
        target="f",
        new="renamed",
        code="""
        def f() -> None:
            ...
        """,
        expected="""
        def renamed() -> None:
            ...
        """,
    )


def test_should_rename_annotated_class_property():
    assert_renames_to(
        target="property",
        new="renamed",
        code="""
        class C:
            property: str

            def f(self):
                self.property = ""
        """,
        expected="""
        class C:
            renamed: str

            def f(self):
                self.renamed = ""
        """,
    )


@mark.skipif(
    sys.version_info < (3, 12), reason="requires Python 3.12 or higher"
)
def test_should_rename_type_parameters():
    assert_renames_to(
        target="T",
        new="Renamed",
        code="""
        def f[T](a: Iterable[T]) -> T:
            ...
        """,
        expected="""
        def f[Renamed](a: Iterable[Renamed]) -> Renamed:
            ...
        """,
    )


@mark.skipif(
    sys.version_info < (3, 12), reason="requires Python 3.12 or higher"
)
def test_should_consider_type_vars_local_to_function():
    assert_renames_to(
        target="T",
        new="Renamed",
        code="""
        def f[T](a: Iterable[T]) -> T:
            ...

        def f2[T]() -> T:
            ...
        """,
        expected="""
        def f[Renamed](a: Iterable[Renamed]) -> Renamed:
            ...

        def f2[T]() -> T:
            ...
        """,
    )


@mark.skipif(
    sys.version_info < (3, 12), reason="requires Python 3.12 or higher"
)
def test_should_rename_type_parameters_in_class():
    assert_renames_to(
        target="T",
        new="Renamed",
        code="""
        class C[T]:
            def m(self, a:T) -> T:
                ...
        """,
        expected="""
        class C[Renamed]:
            def m(self, a:Renamed) -> Renamed:
                ...
        """,
    )


@mark.skipif(
    sys.version_info < (3, 12), reason="requires Python 3.12 or higher"
)
def test_should_rename_type_variable_bounds():
    assert_renames_to(
        target="V",
        new="Renamed",
        code="""
        class V:
            ...

        type T[U: V] = X[U]
        """,
        expected="""
        class Renamed:
            ...

        type T[U: Renamed] = X[U]
        """,
    )


def test_should_consider_parameter_instance_of_type_annotation():
    assert_renames_to(
        target="m",
        new="renamed",
        code="""
        class C:
            def m():
                ...

        def f(a: C):
            a.m()
        """,
        expected="""
        class C:
            def renamed():
                ...

        def f(a: C):
            a.renamed()
        """,
    )


def test_should_consider_return_value_instance_of_type_annotation():
    assert_renames_to(
        target="m",
        new="renamed",
        code="""
        class C:
            def m():
                ...

        def f() -> C:
            ...

        a = f()
        a.m()
        """,
        expected="""
        class C:
            def renamed():
                ...

        def f() -> C:
            ...

        a = f()
        a.renamed()
        """,
    )


def test_should_find_decorators():
    assert_renames_to(
        target="f",
        new="renamed",
        code="""
        def f():
            ...

        @f
        def g():
            ...
        """,
        expected="""
        def renamed():
            ...

        @renamed
        def g():
            ...
        """,
    )


def test_should_find_multiple_assignment_in_method():
    assert_renames_to(
        target="end",
        new="renamed",
        code="""
        class C:
            def m(self):
                start, end = self.extended_range
                text = start.through(end).text
        """,
        expected="""
        class C:
            def m(self):
                start, renamed = self.extended_range
                text = start.through(renamed).text
        """,
    )


def test_should_find_arguments_in_chained_calls():
    assert_renames_to(
        target="a",
        occurrence=2,
        new="renamed",
        code="""
        a = 1
        b = c.d(a).e
        """,
        expected="""
        renamed = 1
        b = c.d(renamed).e
        """,
    )


def test_should_find_async_function_definition():
    assert_renames_to(
        target="f",
        occurrence=2,
        new="renamed",
        code="""
        async def f():
            ...

        a = await f()
        """,
        expected="""
        async def renamed():
            ...

        a = await renamed()
        """,
    )


def test_should_find_name_in_index_lookup():
    assert_renames_to(
        target="b",
        occurrence=1,
        new="renamed",
        code="""
        b = 1
        b.c.d[b.f].e = 2
        """,
        expected="""
        renamed = 1
        renamed.c.d[renamed.f].e = 2
        """,
    )


def test_name_for_type_of_keyword_only_argument_should_be_found():
    assert_renames_to(
        target="Refactor",
        new="Renamed",
        code="""
        class Refactor:
            ...

        def make_code_action(
            *,
            refactor: Refactor,
        ) -> CodeAction:
            ...
        """,
        expected="""
        class Renamed:
            ...

        def make_code_action(
            *,
            refactor: Renamed,
        ) -> CodeAction:
            ...
        """,
    )


def test_should_find_attribute_in_index():
    assert_renames_to(
        target="this",
        new="renamed",
        code="""
        def add_occurrence(this, applied_to):
            applied_to.positions[this.position] = this
        """,
        expected="""
        def add_occurrence(renamed, applied_to):
            applied_to.positions[renamed.position] = renamed
        """,
    )


def test_rename_should_find_local_variable():
    assert_renames_to(
        target="stove",
        new="renamed",
        code="""
        stove = Stove()
        stove.broil()
        """,
        expected="""
        renamed = Stove()
        renamed.broil()
        """,
    )


@mark.xfail
def test_rename_should_rename_class_fields_in_classmethod():
    assert_renames_to(
        target="field",
        new="renamed",
        code="""
        class C:
            def __init__(self, field: str) -> None:
                self.field = field

            @classmethod
            def from_string(cls, string: str) -> Self:
                return cls(field=string)
        """,
        expected="""
        class C:
            def __init__(self, renamed: str) -> None:
                self.field = renamed

            @classmethod
            def from_string(cls, string: str) -> Self:
                return cls(renamed=string)
        """,
    )


def test_dogfood(project_root):
    application = Project(root=project_root)
    sources = application.find_sources()
    collector = NameCollector.from_sources(sources)
    assert collector is not None
