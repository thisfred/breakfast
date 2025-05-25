from __future__ import annotations

import ast
import logging
import sys
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import singledispatch
from pathlib import Path
from typing import Protocol, Self

from pytest import mark

from breakfast import types
from breakfast.names import (
    all_occurrence_positions,
)
from breakfast.source import Position, SubSource, TextRange
from breakfast.visitor import generic_visit
from tests.conftest import (
    apply_edits,
    assert_ast_equals,
    dedent,
    make_source,
    range_for,
)

logger = logging.getLogger(__name__)

STATIC_METHOD = "staticmethod"


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


# @mark.xfail
# def test_dogfood(project_root):
#     application = Project(root=project_root)
#     sources = application.find_sources()
#     collector = NameCollector.from_sources(sources)
#     assert collector is not None


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
        from kitchen import Stove

        stove = Stove()
        stove.broil()
        """,
        expected="""
        from kitchen import Stove

        renamed = Stove()
        renamed.broil()
        """,
    )


QualifiedName = tuple[str, ...]


@dataclass(frozen=True)
class Occurrence(Protocol):
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool


@dataclass(frozen=True)
class NameOccurrence:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_occurrence(occurrence=self)


@dataclass(frozen=True)
class SuperCall:
    occurrence: NameOccurrence

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_super_call(occurrence=self.occurrence)


@dataclass(frozen=True)
class Nonlocal:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool = False

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_nonlocal(occurrence=self)


@dataclass(frozen=True)
class Global:
    name: str
    position: types.Position
    ast: ast.AST | None
    is_definition: bool = False

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_global(occurrence=self)


@singledispatch
def occurrence(node: ast.AST, source: types.Source) -> NameOccurrence:
    raise NotImplementedError(f"Cannot make occurrence from {node:r}")


@occurrence.register
def name_occurrence(node: ast.Name, source: types.Source) -> NameOccurrence:
    return NameOccurrence(
        name=node.id,
        position=source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )


@occurrence.register
def class_occurrence(
    node: ast.ClassDef, source: types.Source
) -> NameOccurrence:
    return definition(node=node, source=source, prefix="class ")


@occurrence.register
def function_occurrence(
    node: ast.FunctionDef, source: types.Source
) -> NameOccurrence:
    return definition(node=node, source=source, prefix="def ")


@occurrence.register
def async_function_occurrence(
    node: ast.AsyncFunctionDef, source: types.Source
) -> NameOccurrence:
    return definition(node=node, source=source, prefix="async def ")


def definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    source: types.Source,
    prefix: str,
) -> NameOccurrence:
    name_occurrence = NameOccurrence(
        name=node.name,
        position=source.node_position(node) + len(prefix),
        ast=node,
        is_definition=True,
    )
    return name_occurrence


@dataclass(frozen=True)
class Module(NameOccurrence): ...


@dataclass(frozen=True)
class Import(NameOccurrence):
    from_module: str


@dataclass
class Name:
    attributes: dict[str, Name]
    types: list[Name]
    occurrences: set[Occurrence]


@dataclass
class Scope:
    names: dict[str, Name]
    children: list[Scope]
    blocks: dict[str, Scope]
    is_block: bool = False
    is_class: bool = False
    parent: Scope | None = None
    global_scope: Scope | None = None
    is_global: bool = False
    name: str | None = None

    @classmethod
    def new(cls, parent: Scope | None = None) -> Self:
        return cls(names={}, parent=parent, children=[], blocks={})

    def lookup(self, name: str) -> Name | None:
        if name in self.names:
            return self.names[name]

        if self.parent:
            return self.parent.lookup(name)

        return None

    def add_child(
        self, name: str | None = None, is_class: bool = False
    ) -> Scope:
        if name:
            child = Scope(
                names={},
                children=[],
                blocks={},
                parent=self,
                global_scope=self if self.is_global else self.global_scope,
                is_global=self.global_scope is None and not self.is_global,
                is_class=is_class,
                name=name,
            )
            self.blocks[name] = child
        else:
            child = Scope(
                names={},
                children=[],
                blocks={},
                parent=self,
                global_scope=self.global_scope,
            )
        self.children.append(child)
        return child


@dataclass
class EnterScope:
    name: str | None = None
    is_class: bool = False

    def __call__(self, applied_to: NameCollector):
        applied_to.enter_scope(self.name, self.is_class)


@dataclass
class EnterFunctionScope:
    occurrence: NameOccurrence

    def __call__(self, applied_to: NameCollector):
        applied_to.enter_function_scope(self.occurrence)


@dataclass
class MoveToScope:
    event: NameOccurrence | Attribute

    def __call__(self, applied_to: NameCollector):
        applied_to.move_to_scope(self.event)


@dataclass
class ReturnFromScope:
    def __call__(self, applied_to: NameCollector):
        applied_to.return_from_scope()


@dataclass
class LeaveScope:
    def __call__(self, applied_to: NameCollector):
        applied_to.leave_scope()


@dataclass
class Attribute:
    value: Occurrence | Attribute
    attribute: NameOccurrence

    def __call__(self, applied_to: NameCollector):
        applied_to.add_attribute(occurrence=self.attribute, value=self.value)


@dataclass
class ClassAttribute:
    class_occurrence: NameOccurrence
    attribute: NameOccurrence

    def __call__(self, applied_to: NameCollector):
        applied_to.add_class_attribute(
            attribute=self.attribute, class_occurrence=self.class_occurrence
        )


@dataclass(frozen=True)
class Bind:
    target: NameOccurrence | Attribute
    value: NameOccurrence | Attribute

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.bind(target=self.target, value=self.value)


@dataclass(frozen=True)
class BaseClass:
    class_occurrence: NameOccurrence | Attribute
    base: NameOccurrence | Attribute

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.add_base_class(
            class_occurrence=self.class_occurrence, base=self.base
        )


@dataclass
class FirstArgument:
    arg: NameOccurrence

    def __call__(self, applied_to: NameCollector):
        applied_to.add_first_argument(self.arg)


class Rewrite(Protocol):
    def __call__(self, qualified_name: QualifiedName) -> QualifiedName: ...


class Event(Protocol):
    def __call__(self, applied_to: NameCollector) -> None: ...


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


def all_occurrences(
    position: types.Position,
    *,
    sources: Iterable[types.Source],
    in_reverse_order: bool = False,
) -> set[Occurrence]:
    collector = NameCollector.from_sources(sources)
    return collector.all_occurrences_for(position)


def assert_renames_to(
    *,
    target: str,
    new: str,
    code: str | types.Source,
    expected: str,
    occurrence: int = 1,
    filename: str = "source.py",
):
    source = (
        make_source(code, filename=filename) if isinstance(code, str) else code
    )
    selection_range = range_for(target, source, occurrence)
    position = selection_range.start
    occurrences = all_occurrences(
        position, sources=[source], in_reverse_order=True
    )

    edits = [
        types.Edit(
            text_range=TextRange(
                start=o.position, end=o.position + len(target)
            ),
            text=new,
        )
        for o in occurrences
    ]
    actual = apply_edits(source=source, edits=edits)
    expected = dedent(expected).strip()
    assert_ast_equals(actual, expected)


@dataclass
class NameCollector:
    sources: dict[Path, types.Source]
    positions: dict[types.Position, Name | None]
    qualified_names: dict[QualifiedName, list[Occurrence]]
    namespace: QualifiedName
    rewrites: list[Rewrite]
    delays: deque[tuple[Scope, Iterator[Event]]]
    current_scope: Scope
    previous_scopes: list[Scope]
    name_scopes: dict[int, Scope]

    @classmethod
    def from_source(cls, source: types.Source) -> Self:
        return cls.from_sources(sources=[source])

    @classmethod
    def from_sources(cls, sources: Iterable[types.Source]) -> Self:
        instance = cls(
            sources={Path(source.path): source for source in sources},
            positions={},
            qualified_names=defaultdict(list),
            namespace=(),
            rewrites=[],
            delays=deque([]),
            current_scope=Scope.new(),
            previous_scopes=[],
            name_scopes={},
        )
        for source in sources:
            for event in find_names(source.ast, source):
                event(instance)
            while instance.delays:
                scope, iterator = instance.delays.popleft()
                old_current = instance.current_scope
                instance.current_scope = scope
                for event in iterator:
                    event(instance)
                instance.current_scope = old_current

        return instance

    def all_occurrences_for(self, position: types.Position) -> set[Occurrence]:
        name = self.positions[position]
        if name:
            return name.occurrences

        return set()

    def add_occurrence(self, occurrence: Occurrence) -> None:
        if not self.positions.get(occurrence.position):
            self.add_name(occurrence)

    def add_nonlocal(self, occurrence: Occurrence) -> None:
        name = self.current_scope.lookup(occurrence.name)
        if name:
            name.occurrences.add(occurrence)
            self.current_scope.names[occurrence.name] = name

    def add_global(self, occurrence: Occurrence) -> None:
        global_scope = self.current_scope.global_scope
        if global_scope is None:
            return

        name = global_scope.lookup(occurrence.name)
        if name is None:
            return

        name.occurrences.add(occurrence)
        self.current_scope.names[occurrence.name] = name

    def add_name(self, occurrence: Occurrence) -> Name | None:
        if occurrence.is_definition:
            name: Name | None = self.current_scope.names.setdefault(
                occurrence.name,
                Name(attributes={}, types=[], occurrences=set()),
            )
            self.current_scope.names[occurrence.name].occurrences.add(
                occurrence
            )
        else:
            name = self.current_scope.lookup(occurrence.name)
            if name:
                name.occurrences.add(occurrence)
        self.positions[occurrence.position] = name
        return name

    def add_class_attribute(
        self,
        attribute: NameOccurrence,
        class_occurrence: NameOccurrence,
    ) -> None:
        if self.current_scope.parent is None:
            return

        cls = self.current_scope.parent.names[class_occurrence.name]
        self.add_attribute_occurrence(attribute_occurrence=attribute, value=cls)

    def add_attribute(
        self,
        value: Occurrence | Attribute,
        occurrence: NameOccurrence,
    ) -> None:
        current = self.lookup_attribute_value(value)
        if current is None:
            return
        self.add_attribute_occurrence(
            value=current, attribute_occurrence=occurrence
        )

    def add_attribute_occurrence(
        self, attribute_occurrence: NameOccurrence, value
    ):
        for parent_type in (value, *value.types):
            if result := parent_type.attributes.get(attribute_occurrence.name):
                break
        else:
            if value.types:
                result = value.types[0].attributes.setdefault(
                    attribute_occurrence.name,
                    Name(attributes={}, types=[], occurrences=set()),
                )
            else:
                result = value.attributes.setdefault(
                    attribute_occurrence.name,
                    Name(attributes={}, types=[], occurrences=set()),
                )
        result = result
        found_attribute = result
        found_attribute.occurrences.add(attribute_occurrence)
        self.positions[attribute_occurrence.position] = found_attribute

    def enter_scope(
        self, name: str | None = None, is_class: bool = False
    ) -> None:
        self.current_scope = self.current_scope.add_child(name, is_class)

    def enter_function_scope(self, occurrence: NameOccurrence) -> None:
        if occurrence.position in self.positions:
            name = self.positions[occurrence.position]
        else:
            name = self.current_scope.lookup(occurrence.name)
        self.enter_scope(occurrence.name)
        if name is None:
            return
        self.name_scopes[id(name)] = self.current_scope

    def move_to_scope(self, event: NameOccurrence | Attribute) -> None:
        self.previous_scopes.append(self.current_scope)

        name = self.lookup_attribute_value(event)
        if name is None:
            self.enter_scope()
            return

        scope = self.name_scopes.get(id(name))
        if scope is None:
            self.enter_scope()
            return

        self.current_scope = scope

    def return_from_scope(self) -> None:
        self.current_scope = self.previous_scopes.pop()

    def leave_scope(self) -> None:
        if self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def delay(self, delayed: Iterator[Event]) -> None:
        self.delays.append((self.current_scope, delayed))

    def add_first_argument(self, arg: NameOccurrence) -> None:
        if not (
            self.current_scope.parent and self.current_scope.parent.is_class
        ):
            return

        target = self.lookup_attribute_value(arg)
        value = (
            self.current_scope.parent.parent.names.get(
                self.current_scope.parent.name
            )
            if self.current_scope.parent.parent
            and self.current_scope.parent.name
            else None
        )
        if target and value:
            target.types.append(value)

    def add_super_call(self, occurrence: NameOccurrence) -> None:
        if not (
            self.current_scope.parent and self.current_scope.parent.is_class
        ):
            return

        target: Name | None = self.current_scope.names.setdefault(
            occurrence.name, Name(attributes={}, types=[], occurrences=set())
        )
        self.current_scope.names[occurrence.name].occurrences.add(occurrence)
        class_name = (
            self.current_scope.parent.parent.names.get(
                self.current_scope.parent.name
            )
            if self.current_scope.parent.parent
            and self.current_scope.parent.name
            else None
        )
        if class_name is None:
            return

        if target:
            target.types.extend(class_name.types)

    def add_base_class(
        self,
        class_occurrence: NameOccurrence | Attribute,
        base: NameOccurrence | Attribute,
    ) -> None:
        class_name = self.lookup_attribute_value(class_occurrence)
        base_name = self.lookup_attribute_value(base)
        if class_name and base_name:
            class_name.types.append(base_name)

    def bind(
        self,
        target: NameOccurrence | Attribute,
        value: NameOccurrence | Attribute,
    ) -> None:
        target_name = self.lookup_attribute_value(target)
        value_name = self.lookup_attribute_value(value)
        if target_name and value_name:
            target_name.types.extend([value_name, *value_name.types])

    def lookup_attribute_value(
        self, value: Occurrence | Attribute
    ) -> Name | None:
        if isinstance(value, Attribute):
            name = self.lookup_attribute_value(value.value)
            if name is None:
                return None

            return lookup_attribute(value=name, attribute=value.attribute.name)
        else:
            return self.current_scope.lookup(value.name)


def lookup_attribute(value: Name, attribute: str) -> Name:
    for parent_type in (value, *value.types):
        if result := parent_type.attributes.get(attribute):
            break
    else:
        result = value.attributes.setdefault(
            attribute, Name(attributes={}, types=[], occurrences=set())
        )
    return result


def has_prefix(name: QualifiedName, prefix: QualifiedName) -> bool:
    if len(prefix) >= len(name):
        return False

    return name[: len(prefix)] == prefix


@singledispatch
def find_names(node: ast.AST, source: types.Source) -> Iterator[Event]:
    yield from generic_visit(find_names, node, source)


@find_names.register
def name(node: ast.Name, source: types.Source) -> Iterator[Event]:
    yield occurrence(node, source)


@find_names.register
def module(node: ast.Module, source: types.Source) -> Iterator[Event]:
    yield EnterScope(source.module_name)
    for statement in node.body:
        yield from find_names(statement, source)
    yield LeaveScope()


@find_names.register
def type_var(node: ast.TypeVar, source: types.Source) -> Iterator[Event]:
    yield NameOccurrence(
        node.name,
        position=source.node_position(node),
        ast=node,
        is_definition=True,
    )
    if node.bound:
        yield from find_names(node.bound, source)


@find_names.register
def function_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: types.Source
) -> Iterator[Event]:
    for decorator in node.decorator_list:
        yield from find_names(decorator, source)
    definition = occurrence(node, source)
    yield definition
    for default in node.args.defaults:
        yield from find_names(default, source)

    yield EnterFunctionScope(definition)

    for type_parameter in node.type_params:
        yield from find_names(type_parameter, source)

    return_event = None
    if node.returns:
        for event in annotation(node.returns, source):
            if isinstance(event, NameOccurrence | Attribute):
                return_event = event
            yield event

    if return_event:
        yield Bind(definition, return_event)

    in_static_method = any(
        d.id == STATIC_METHOD
        for d in node.decorator_list
        if isinstance(d, ast.Name)
    )
    yield from arguments(node.args, source, in_static_method=in_static_method)

    def process_body() -> Iterator[Event]:
        for statement in node.body:
            yield from find_names(statement, source)

    yield Delay(process_body())

    yield LeaveScope()


def arguments(
    arguments: ast.arguments, source: types.Source, *, in_static_method
) -> Iterator[Event]:
    for i, arg in enumerate(
        (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ):
        name_event = type_event = None
        for event in find_names(arg, source):
            yield event
            if isinstance(event, NameOccurrence):
                name_event = event
        if arg.annotation:
            for event in annotation(arg.annotation, source):
                yield event
                if isinstance(event, NameOccurrence):
                    type_event = event
            if name_event and type_event:
                yield Bind(name_event, type_event)
        if i == 0:
            if name_event and not in_static_method:
                yield FirstArgument(name_event)


def annotation(
    annotation: ast.AST | None, source: types.Source
) -> Iterator[Event]:
    if not annotation:
        return

    if isinstance(annotation, ast.Constant) and isinstance(
        annotation.value, str
    ):
        annotation_position = source.node_position(annotation)
        for statement in ast.parse(annotation.value).body:
            yield from find_names(
                statement,
                SubSource(
                    source=source,
                    start_position=annotation_position,
                    code=annotation.value,
                ),
            )
    else:
        yield from find_names(annotation, source)


@find_names.register
def class_definition(
    node: ast.ClassDef, source: types.Source
) -> Iterator[Event]:
    for decorator in node.decorator_list:
        yield from find_names(decorator, source)
    class_occurrence = occurrence(node, source)
    yield class_occurrence
    for base in node.bases:
        last_event = None
        for event in find_names(base, source):
            if isinstance(event, NameOccurrence | Attribute):
                last_event = event
            yield event
        if last_event:
            yield BaseClass(class_occurrence, last_event)

    yield EnterScope(class_occurrence.name, is_class=True)
    for type_parameter in node.type_params:
        yield from find_names(type_parameter, source)
    yield from class_body(node, source, class_occurrence)
    yield LeaveScope()


def class_body(
    node: ast.ClassDef, source: types.Source, class_occurrence: NameOccurrence
) -> Iterator[Event]:
    for statement in node.body:
        attribute_occurrence = None
        match statement:
            case ast.AnnAssign(target=ast.Name() as target):
                attribute_occurrence = occurrence(target, source)
            case ast.Assign(targets=[ast.Name() as target]):
                attribute_occurrence = occurrence(target, source)
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                attribute_occurrence = occurrence(statement, source)
        if attribute_occurrence is None:
            continue
        yield ClassAttribute(
            class_occurrence=class_occurrence,
            attribute=attribute_occurrence,
        )
        yield from find_names(statement, source)


@find_names.register
def call(node: ast.Call, source: types.Source) -> Iterator[Event]:
    for arg in node.args:
        yield from find_names(arg, source)
    for keyword in node.keywords:
        yield from find_names(keyword.value, source)
    last_event = None
    for event in find_names(node.func, source):
        last_event = event
        yield event

    if not isinstance(last_event, NameOccurrence | Attribute):
        return

    if isinstance(last_event, NameOccurrence) and last_event.name == "super":
        yield SuperCall(last_event)

    yield MoveToScope(last_event)
    yield from process_keywords(node, source)
    yield ReturnFromScope()


@find_names.register
def comprehension(
    node: ast.GeneratorExp | ast.SetComp | ast.ListComp | ast.DictComp,
    source: types.Source,
) -> Iterator[Event]:
    yield EnterScope()
    for generator in node.generators:
        yield from find_names(generator, source)
    for sub_node in sub_nodes(node):
        yield from find_names(sub_node, source)
    yield LeaveScope()


@singledispatch
def sub_nodes(node: ast.AST) -> Iterable[ast.AST]:
    return ()


@sub_nodes.register
def sub_nodes_comprehension(
    node: ast.GeneratorExp | ast.SetComp | ast.ListComp,
) -> Iterable[ast.AST]:
    return (node.elt,)


@sub_nodes.register
def sub_nodes_dictionary_comprehension(node: ast.DictComp) -> Iterable[ast.AST]:
    return (node.key, node.value)


def process_keywords(node: ast.Call, source: types.Source) -> Iterator[Event]:
    for keyword in node.keywords:
        if keyword.arg is not None:
            yield NameOccurrence(
                name=keyword.arg,
                position=source.node_position(keyword),
                ast=keyword,
                is_definition=False,
            )


@find_names.register
def arg(node: ast.arg, source: types.Source) -> Iterator[Event]:
    yield NameOccurrence(
        name=node.arg,
        position=source.node_position(node),
        ast=node,
        is_definition=True,
    )


@find_names.register
def attribute(node: ast.Attribute, source: types.Source) -> Iterator[Event]:
    last_event = None
    for event in find_names(node.value, source):
        if isinstance(event, Attribute | NameOccurrence):
            last_event = event
        yield event
    if last_event is None:
        return
    end = source.node_end_position(node.value)
    attribute = NameOccurrence(
        name=node.attr,
        position=end + 1 if end else source.node_position(node),
        ast=node,
        is_definition=isinstance(node.ctx, ast.Store),
    )
    yield Attribute(value=last_event, attribute=attribute)


def get_targets(
    node: ast.Assign,
    source: types.Source,
    targets=list[list[NameOccurrence | Attribute]],
):
    for target in node.targets:
        match target:
            case ast.Tuple(elts=elements):
                element_targets = []
                for element in elements:
                    last_target = None
                    for event in find_names(element, source):
                        if isinstance(event, NameOccurrence | Attribute):
                            last_target = event
                        yield event
                    if last_target:
                        element_targets.append(last_target)
                targets.append(element_targets)
            case _:
                last_target = None
                for event in find_names(target, source):
                    if isinstance(event, NameOccurrence | Attribute):
                        last_target = event
                    yield event
                if last_target:
                    targets.append([last_target])


def get_values(
    node: ast.Assign,
    source: types.Source,
    value_events=list[list[NameOccurrence | Attribute]],
):
    match node.value:
        case ast.Tuple(elts=elements):
            for element in elements:
                last_event = None
                for event in find_names(element, source):
                    if isinstance(event, NameOccurrence | Attribute):
                        last_event = event
                    yield event
                if last_event:
                    value_events.append(last_event)
        case _:
            for event in find_names(node.value, source):
                if isinstance(event, NameOccurrence | Attribute):
                    value_events[:] = [event]
                yield event


@find_names.register
def assignment(node: ast.Assign, source: types.Source) -> Iterator[Event]:
    targets: list[list[NameOccurrence | Attribute]] = []
    yield from get_targets(node, source, targets)
    value_events: list[NameOccurrence | Attribute] = []
    yield from get_values(node, source, value_events)
    if not value_events:
        return

    for target_events in targets:
        if len(target_events) != len(value_events):
            return
        for target_event, value_event in zip(
            target_events, value_events, strict=True
        ):
            yield Bind(target_event, value_event)


@find_names.register
def slice_node(node: ast.Subscript, source: types.Source) -> Iterator[Event]:
    yield from find_names(node.slice, source)
    yield from find_names(node.value, source)


@find_names.register
def alias(node: ast.alias, source: types.Source) -> Iterator[Event]:
    yield NameOccurrence(
        name=node.name,
        position=source.node_position(node),
        ast=node,
        is_definition=False,
    )


@dataclass
class Delay:
    delayed: Iterator[Event]

    def __call__(self, applied_to: NameCollector) -> None:
        applied_to.delay(delayed=self.delayed)


@find_names.register
def import_from(node: ast.ImportFrom, source: types.Source) -> Iterator[Event]:
    if node.module:
        yield Module(
            name=node.module,
            position=source.node_position(node) + len("from "),
            ast=node,
            is_definition=False,
        )
        for node_name in node.names:
            yield Import(
                name=node_name.name,
                position=source.node_position(node_name),
                ast=node_name,
                is_definition=False,
                from_module=node.module,
            )


@find_names.register
def match_case(node: ast.match_case, source: types.Source) -> Iterator[Event]:
    yield EnterScope()
    yield from find_names(node.pattern, source)
    if node.guard:
        yield from find_names(node.guard, source)
    for statement in node.body:
        yield from find_names(statement, source)
    yield LeaveScope()


@find_names.register
def match_as(node: ast.MatchAs, source: types.Source) -> Iterator[Event]:
    if node.name:
        yield NameOccurrence(
            name=node.name,
            position=source.node_position(node),
            ast=node,
            is_definition=True,
        )


@find_names.register
def nonlocal_node(node: ast.Nonlocal, source: types.Source) -> Iterator[Event]:
    position = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, position)
        yield Nonlocal(
            name=name,
            position=position,
            ast=node,
        )


@find_names.register
def global_node(node: ast.Global, source: types.Source) -> Iterator[Event]:
    position = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, position)
        yield Global(
            name=name,
            position=position,
            ast=node,
        )
