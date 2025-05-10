import ast
import sys
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import singledispatch
from pathlib import Path
from typing import Protocol, Self

from pytest import mark

from breakfast import types
from breakfast.names import (
    all_occurrence_positions,
    build_graph,
)
from breakfast.project import Project
from breakfast.source import Position
from breakfast.types import contains
from breakfast.visitor import generic_visit
from tests.conftest import (
    assert_renames_to,
    make_source,
)


def test_assignment_occurrences():
    source1 = make_source(
        """
    from kitchen import Stove

    stove = Stove()
    stove.broil()
    """,
        filename="chef.py",
    )
    source2 = make_source(
        """
    from stove import *
    """,
        filename="kitchen.py",
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
        filename="stove.py",
    )
    positions = all_occurrence_positions(
        Position(source1, 4, 6), sources=[source1, source2, source3]
    )
    assert positions == [
        Position(source1, 4, 6),
        Position(source3, 5, 8),
    ]


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
        Position(source1, 4, 6), sources=[source1, source2, source3]
    )
    assert positions == [
        Position(source1, 4, 6),
        Position(source3, 5, 8),
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
        Position(source2, 1, 6), sources=[source1, source2]
    )
    assert positions == [
        Position(source1, 1, 18),
        Position(source1, 3, 4),
        Position(source2, 1, 6),
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
            foo = var
        """,
        expected="""
        renamed = 12

        def fun():
            global renamed
            foo = renamed
        """,
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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

            def get_property(self):
                self.property = wat
        """,
        expected="""
        class ClassName:

            def __init__(self, property):
                self.renamed = property

            def get_property(self):
                self.renamed = wat
        """,
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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
        all_occurrences=new_all_occurrences,
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

            def bar(self):
                super().renamed()
        """,
    )


def test_does_not_rename_imported_names():
    assert_renames_to(
        target="b",
        occurrence=2,
        new="renamed",
        code="""
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """,
        expected="""
        from a import b


        def foo():
            renamed = 1
            print(renamed)

        b()
        """,
    )


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
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, sources=[source1, source2]) == [
        Position(source2, 2, 4),
        Position(source1, 1, 4),
    ]


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


def test_should_find_class_used_in_string_annotation():
    assert_renames_to(
        target="C",
        new="Renamed",
        code="""
        class C:
            ...

        def f(c: "C"):
            ...
        """,
        expected="""
        class Renamed:
            ...

        def f(c: "Renamed"):
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
        b.c.d[b.f].e
        """,
        expected="""
        renamed = 1
        renamed.c.d[renamed.f].e
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


def test_attribute_in_string_annotation():
    assert_renames_to(
        target="Call",
        new="Renamed",
        code="""
        class ast:
            Call: str

        class C:
            @property
            def enclosing_call(self) -> "NodeWithRange[ast.Call] | None": ...
        """,
        expected="""
        class ast:
            Renamed: str

        class C:
            @property
            def enclosing_call(self) -> "NodeWithRange[ast.Renamed] | None": ...
        """,
    )


def test_dogfood(project_root):
    application = Project(root=project_root)
    sources = application.find_sources()
    graph = build_graph(sources=sources)
    assert graph is not None


@mark.xfail
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


QualifiedName = tuple[str, ...]


@dataclass(frozen=True)
class Occurrence:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool

    @property
    def source(self) -> types.Source:
        return self.position.source

    @property
    def start(self) -> types.Position:
        return self.position

    @property
    def end(self) -> types.Position:
        return self.position + len(self.name)

    def __contains__(self, other: types.Ranged) -> bool:
        return contains(self, other)

    def __call__(self, applied_to: "NameCollector") -> None:
        applied_to.add_occurrence(occurrence=self)


@dataclass
class EnterScope:
    name: str

    def __call__(self, applied_to: "NameCollector"):
        applied_to.enter_scope(name=self.name)


@dataclass
class EnterClassScope:
    name: str

    def __call__(self, applied_to: "NameCollector"):
        applied_to.enter_class_scope(name=self.name)
        applied_to.enter_scope(name=self.name)


@dataclass
class JumpToScope:
    scope: QualifiedName

    def __call__(self, applied_to: "NameCollector"):
        applied_to.jump_to(scope=self.scope)


@dataclass
class LeaveScope:
    def __call__(self, applied_to: "NameCollector"):
        applied_to.leave_scope()


@dataclass
class LeaveClassScope:
    def __call__(self, applied_to: "NameCollector"):
        applied_to.leave_scope()
        applied_to.leave_class_scope()


@dataclass
class FirstArgument:
    arg: ast.arg

    def __call__(self, applied_to: "NameCollector"):
        applied_to.add_first_argument(self.arg)


class Rewrite(Protocol):
    def __call__(self, qualified_name: QualifiedName) -> QualifiedName: ...


class Event(Protocol):
    def __call__(self, applied_to: "NameCollector") -> None: ...


@dataclass
class RewritePrefix:
    prefix: QualifiedName
    replacement: QualifiedName

    def applies_to(self, qualified_name: QualifiedName) -> bool:
        return has_prefix(qualified_name, self.prefix)

    def __call__(self, qualified_name: QualifiedName) -> QualifiedName:
        return (
            (
                *self.replacement,
                *qualified_name[len(self.prefix) :],
            )
            if self.applies_to(qualified_name)
            else qualified_name
        )


@dataclass
class Substitute:
    original_name: QualifiedName
    replacement: QualifiedName

    def applies_to(self, qualified_name: QualifiedName) -> bool:
        return self.original_name == qualified_name

    def __call__(self, qualified_name: QualifiedName) -> QualifiedName:
        return (
            self.replacement
            if self.applies_to(qualified_name)
            else qualified_name
        )


def new_all_occurrences(
    position: types.Position,
    *,
    sources: Iterable[types.Source],
    in_reverse_order: bool = False,
) -> Sequence[Occurrence]:
    collector = NameCollector.from_sources(sources)
    return collector.all_occurrences_for(position)


@dataclass
class NameCollector:
    sources: dict[Path, types.Source]
    names: dict[str, list[Occurrence]]
    positions: dict[types.Position, tuple[Occurrence, QualifiedName]]
    qualified_names: dict[QualifiedName, list[Occurrence]]
    scopes: list[QualifiedName]
    class_scopes: list[QualifiedName]
    namespace: QualifiedName
    rewrites: list[Rewrite]
    delays: deque[tuple[list[QualifiedName], Iterator[Event]]]
    scope_names: dict[QualifiedName, dict[str, QualifiedName]]

    @classmethod
    def from_source(cls, source: types.Source) -> Self:
        return cls.from_sources(sources=[source])

    @classmethod
    def from_sources(cls, sources: Iterable[types.Source]) -> Self:
        instance = cls(
            sources={Path(source.path): source for source in sources},
            names=defaultdict(list),
            positions={},
            qualified_names=defaultdict(list),
            scopes=[],
            namespace=(),
            rewrites=[],
            delays=deque([]),
            scope_names=defaultdict(dict),
            class_scopes=[],
        )
        for source in sources:
            instance.jump_to(tuple(source.module_name.split(".")))
            for event in find_names(source.ast, source):
                print(event)
                event(instance)
            instance.leave_scope()
            while instance.delays:
                scopes, iterator = instance.delays.popleft()
                instance.scopes = scopes
                for event in iterator:
                    print(event)
                    event(instance)

        return instance

    @property
    def in_class(self) -> bool:
        return bool(self.class_scopes)

    @property
    def canonical_names(self) -> Sequence[QualifiedName]:
        return [self.rewrite(n) for n in self.qualified_names]

    def all_occurrences_for(
        self, position: types.Position
    ) -> Sequence[Occurrence]:
        all_occurrences = []
        occurrence, qualified_name = self.positions[position]
        canonical_name = self.rewrite(qualified_name)
        for name, occurrences in self.qualified_names.items():
            if self.rewrite(name) == canonical_name:
                all_occurrences.extend(occurrences)
        return all_occurrences

    def rewrite(self, name: QualifiedName) -> QualifiedName:
        for rewrite in self.rewrites:
            name = rewrite(name)
        return name

    def add_occurrence(self, occurrence: Occurrence) -> None:
        self.names[occurrence.name].append(occurrence)

        qualified_name = (*self.scopes[-1], occurrence.name)
        print(f"{self.scopes[-1]=} 1")
        print(f"{qualified_name=} 1")
        if occurrence.name in self.scope_names[self.scopes[-1]]:
            qualified_name = self.scope_names[self.scopes[-1]][occurrence.name]
        elif occurrence.is_definition:
            self.scope_names[self.scopes[-1]][occurrence.name] = qualified_name
        else:
            for scope in self.scopes[-1::-1]:
                names = self.scope_names[scope]
                if occurrence.name in names:
                    qualified_name = names[occurrence.name]
        print(f"{qualified_name=} 2")
        self.positions[occurrence.position] = (occurrence, qualified_name)
        self.qualified_names[qualified_name].append(occurrence)

    def add_substitute(self, name: str, replacement: QualifiedName) -> None:
        self.rewrites.append(
            Substitute(
                original_name=(*self.scopes[-1], name),
                replacement=replacement,
            )
        )

    def add_prefix_rewrite(
        self, target: QualifiedName, value: QualifiedName
    ) -> None:
        self.rewrites.append(
            RewritePrefix(
                prefix=(*self.scopes[-1], *target),
                replacement=self.rewrite((*self.scopes[-1], *value)),
            )
        )

    def enter_scope(self, name: str) -> None:
        self.scopes.append((*self.scopes[-1], name))

    def enter_class_scope(self, name: str) -> None:
        self.class_scopes.append((*self.scopes[-1], name))

    def jump_to(self, scope: QualifiedName) -> None:
        self.scopes.append(scope)

    def leave_scope(self) -> None:
        self.scopes.pop()

    def leave_class_scope(self) -> None:
        self.class_scopes.pop()

    def delay(self, delayed: Iterator[Event]) -> None:
        self.delays.append((self.scopes[:], delayed))

    def add_first_argument(self, arg: ast.arg) -> None:
        if not self.in_class:
            return

        self.rewrites.append(
            RewritePrefix(
                prefix=(*self.scopes[-1], arg.arg),
                replacement=self.class_scopes[-1],
            )
        )


def has_prefix(name: QualifiedName, prefix: QualifiedName) -> bool:
    if len(prefix) >= len(name):
        return False

    return name[: len(prefix)] == prefix


@singledispatch
def find_names(node: ast.AST, source: types.Source) -> Iterator[Event]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def name(node: ast.Name, source: types.Source) -> Iterator[Event]:
    yield Occurrence(
        name=node.id,
        position=source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )


@find_names.register
def function_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: types.Source
) -> Iterator[Event]:
    match node:
        case ast.FunctionDef(name=name, args=args, body=body):
            yield Occurrence(
                name=name,
                position=source.node_position(node) + len("def "),
                ast=node,
                is_definition=True,
            )
            for default in args.defaults:
                yield from find_names(default, source)
            yield EnterScope(name)
            yield EnterScope("/")
            for i, arg in enumerate(args.args):
                if i == 0:
                    yield FirstArgument(arg)
                yield from find_names(arg, source)

            def process_body() -> Iterator[Event]:
                for statement in body:
                    yield from find_names(statement, source)

            yield Delay(process_body())
            yield LeaveScope()
            yield LeaveScope()


@find_names.register
def class_definition(
    node: ast.ClassDef, source: types.Source
) -> Iterator[Event]:
    yield Occurrence(
        name=node.name,
        position=source.node_position(node) + len("class "),
        ast=node,
        is_definition=True,
    )
    yield EnterClassScope(node.name)

    for statement in node.body:
        yield from find_names(statement, source)

    yield LeaveClassScope()


@find_names.register
def call(node: ast.Call, source: types.Source) -> Iterator[Event]:
    for arg in node.args:
        yield from find_names(arg, source)
    for keyword in node.keywords:
        yield from find_names(keyword.value, source)
    for event in find_names(node.func, source):
        yield event
    match node.func:
        case ast.Name():
            name = node.func.id
            yield EnterScope(name)
            yield EnterScope("/")
            yield from process_keywords(node, source)
            yield LeaveScope()
            yield LeaveScope()
        case ast.Attribute():
            names = []
            for event in find_names(node.func.value, source):
                if isinstance(event, Occurrence):
                    names.append(event.name)
            for name in names:
                yield EnterScope(name)
            yield EnterScope("/")
            yield from process_keywords(node, source)
            yield LeaveScope()
            for _ in names:
                yield LeaveScope()


@find_names.register
def comprehension(
    node: ast.GeneratorExp | ast.SetComp | ast.ListComp, source: types.Source
) -> Iterator[Event]:
    yield EnterScope(f"/{node.lineno}-{node.col_offset}/")
    for generator in node.generators:
        yield from find_names(generator, source)
    yield from find_names(node.elt, source)
    yield LeaveScope()


@find_names.register
def dict_comprehension(
    node: ast.DictComp, source: types.Source
) -> Iterator[Event]:
    yield EnterScope(f"/{node.lineno}-{node.col_offset}/")
    print(ast.dump(node.key))
    print(ast.dump(node.value))
    for generator in node.generators:
        yield from find_names(generator, source)
    yield from find_names(node.key, source)
    yield from find_names(node.value, source)
    yield LeaveScope()


def process_keywords(node: ast.Call, source: types.Source) -> Iterator[Event]:
    for keyword in node.keywords:
        if keyword.arg is not None:
            yield Occurrence(
                name=keyword.arg,
                position=source.node_position(keyword),
                ast=keyword,
                is_definition=False,
            )


@find_names.register
def arg(node: ast.arg, source: types.Source) -> Iterator[Event]:
    yield Occurrence(
        name=node.arg,
        position=source.node_position(node),
        ast=node,
        is_definition=True,
    )


@find_names.register
def attribute(node: ast.Attribute, source: types.Source) -> Iterator[Event]:
    names = []
    if not isinstance(node.value, ast.Attribute):
        yield from find_names(node.value, source)
    for event in find_names(node.value, source):
        if isinstance(event, Occurrence):
            names.append(event.name)
    for name in names:
        yield EnterScope(name)
    end = source.node_end_position(node.value)
    yield Occurrence(
        name=node.attr,
        position=end + 1 if end else source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )
    for _ in names:
        yield LeaveScope()


@dataclass
class Accumulator:
    qualified_name: QualifiedName = ()
    scopes: int = 0

    def process(self, event: Event) -> str | None:
        match event:
            case Occurrence(name=name):
                if not self.scopes:
                    self.qualified_name = (*self.qualified_name, name)
                    return name
            case EnterScope():
                self.scopes += 1
            case LeaveScope():
                self.scopes -= 1

        return None


@find_names.register
def assignment(node: ast.Assign, source: types.Source) -> Iterator[Event]:
    target_accumulators = []
    for target in node.targets:
        accumulator = Accumulator()
        for event in find_names(target, source):
            yield event
            accumulator.process(event)
        target_accumulators.append(accumulator)

    value_accumulator = Accumulator()
    for event in find_names(node.value, source):
        yield event
        value_accumulator.process(event)

    for accumulator in target_accumulators:
        yield AddPrefixRewrite(
            target=accumulator.qualified_name,
            value=value_accumulator.qualified_name,
        )


@find_names.register
def alias(node: ast.alias, source: types.Source) -> Iterator[Event]:
    yield Occurrence(
        name=node.name,
        position=source.node_position(node),
        ast=node,
        is_definition=False,
    )


@dataclass
class AddSubstitute:
    name: str
    replacement: QualifiedName

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_substitute(name=self.name, replacement=self.replacement)


@dataclass
class AddPrefixRewrite:
    target: QualifiedName
    value: QualifiedName

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_prefix_rewrite(target=self.target, value=self.value)


@dataclass
class Delay:
    delayed: Iterator[Event]

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.delay(delayed=self.delayed)


@find_names.register
def import_from(node: ast.ImportFrom, source: types.Source) -> Iterator[Event]:
    name = node.module
    if name:
        yield JumpToScope(())
        yield Occurrence(
            name=name,
            position=source.node_position(node) + len("from "),
            ast=node,
            is_definition=False,
        )
        yield EnterScope(name)
        rewrites = []
        for node_name in node.names:
            for event in find_names(node_name, source):
                yield event
                if isinstance(event, Occurrence):
                    rewrites.append(
                        AddSubstitute(event.name, (name, event.name))
                    )
        yield LeaveScope()
        yield LeaveScope()
        yield from rewrites


def test_name_collector_should_collect_names_in_source_file():
    source = make_source(
        """
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        filename="chef.py",
    )

    collector = NameCollector.from_source(source)

    assert set(collector.names.keys()) == {
        "kitchen",
        "Stove",
        "stove",
        "broil",
    }


def test_name_collector_should_collect_positions_for_names():
    source = make_source(
        """
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        filename="chef.py",
    )

    collector = NameCollector.from_source(source)

    assert set(collector.positions.keys()) == {
        source.position(row=1, column=5),
        source.position(row=1, column=20),
        source.position(row=3, column=0),
        source.position(row=3, column=8),
        source.position(row=4, column=0),
        source.position(row=4, column=6),
    }


def test_name_collector_should_collect_qualified_names():
    source = make_source(
        """
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        filename="chef.py",
    )
    collector = NameCollector.from_source(source)
    assert set(collector.qualified_names) == {
        ("kitchen",),
        ("kitchen", "Stove"),
        ("chef", "Stove"),
        ("chef", "stove"),
        ("chef", "stove", "broil"),
    }


def test_name_collector_should_collect_canonical_names():
    source = make_source(
        """
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        filename="chef.py",
    )
    collector = NameCollector.from_source(source)
    assert set(collector.canonical_names) == {
        ("kitchen",),
        ("kitchen", "Stove"),
        ("chef", "stove"),
        ("kitchen", "Stove", "broil"),
    }


def test_rename_should_find_local_variable():
    assert_renames_to(
        target="stove",
        new="renamed",
        code="""
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        expected="""
        from kitchen import Stove

        renamed = Stove()
        renamed.broil()
        """,
        all_occurrences=new_all_occurrences,
    )
