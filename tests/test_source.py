from breakfast.source import Source
from breakfast.position import Position
from tests import dedent


def test_find_definition_works_when_called_for_definition():
    source = Source(dedent("""
    def old():
        pass

    def bar():
        old()
    """))

    assert (
        Position(row=1, column=4) ==
        source.find_definition_for("old", Position(row=1, column=4)))


def test_find_definition_works_when_called_for_any_point_in_definition():
    source = Source(dedent("""
    def old():
        pass

    def bar():
        old()
    """))

    assert (
        Position(row=1, column=4) ==
        source.find_definition_for("old", Position(row=1, column=6)))


def test_find_definition_finds_function_definition_in_module_scope():
    source = Source(dedent("""
    def old():
        pass

    def bar():
        old()
    """))

    assert (
        Position(row=1, column=4) ==
        source.find_definition_for("old", Position(row=5, column=4)))


def test_find_definition_finds_variable_definition():
    source = Source(dedent("""
    old = 2

    def bar():
        return old + 10
    """))

    assert (
        Position(row=1, column=0) ==
        source.find_definition_for("old", Position(row=5, column=11)))


def test_finds_keyword_parameter():
    source = Source(dedent("""
    def fun(old=1):
        print(old)

    old = 8
    fun(old=old)
    """))

    assert (
        Position(row=1, column=8) ==
        source.find_definition_for('old', Position(row=1, column=10)))
