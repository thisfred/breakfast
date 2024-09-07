from breakfast.names import build_graph
from breakfast.source import Position
from tests import make_source


def test_functions() -> None:
    source = make_source(
        """
    def bake():
        pass

    def broil():
        pass

    def saute():
        pass
    """,
        filename="stove.py",
    )

    graph = build_graph([source])
    definition = graph.traverse_with_stack(graph.root, stack=("stove", ".", "broil"))
    assert definition.position == Position(source, 4, 4)


def test_import() -> None:
    source1 = make_source(
        """
    def bake():
        pass

    def broil():
        pass

    def saute():
        pass
    """,
        filename="stove.py",
    )
    source2 = make_source(
        """
    from stove import broil

    broil()
    """,
        filename="kitchen.py",
    )

    graph = build_graph([source1, source2])
    definition = graph.traverse_with_stack(graph.root, stack=("kitchen", ".", "broil"))
    assert definition.position == Position(source1, 4, 4)


def test_classes() -> None:
    source = make_source(
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
    graph = build_graph([source])
    definition = graph.traverse_with_stack(
        graph.root, stack=("stove", ".", "Stove", "()", ".", "broil")
    )
    assert definition.position == Position(source, 5, 8)


def test_assignment() -> None:
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
    graph = build_graph([source1, source2, source3])
    definition = graph.traverse_with_stack(
        graph.root, stack=("chef", ".", "stove", ".", "broil")
    )
    assert definition.position == Position(source3, 5, 8)
