from typing import Any

from breakfast.names import Pop, Push, all_occurrence_positions, build_graph
from breakfast.position import Position
from breakfast.scope_graph import (
    NodeType,
    ScopeGraph,
    ScopeNode,
)
from tests import make_source

try:
    import graphviz
except ImportError:
    graphviz = None


def view_graph(graph: ScopeGraph) -> None:
    if graphviz is None:
        return

    digraph = graphviz.Digraph()
    digraph.attr(rankdir="BT")
    for same_rank_nodes in graph.group_by_rank():
        subgraph = graphviz.Digraph()
        subgraph.attr(rankdir="BT")
        subgraph.attr(rank="same")

        for node_id in same_rank_nodes:
            node = graph.nodes[node_id]
            render_node(node, subgraph, graph)

        digraph.subgraph(subgraph)

    for from_id, to_nodes in graph.edges.items():
        for edge, to_node_id in to_nodes:
            digraph.edge(
                str(from_id),
                str(to_node_id),
                label="e" if edge.to_enclosing_scope else "",
            )

    digraph.render(view=True)
    breakpoint()


def render_node(node: ScopeNode, subgraph: Any, scope_graph: ScopeGraph) -> None:
    if isinstance(node.action, Pop):
        subgraph.node(
            name=str(node.node_id),
            label=f"↑{node.node_id} {node.action.path}"
            + (
                f"<{node.position.row}, {node.position.column}>"
                if node.position
                else "<>"
            )
            + (
                f"{{{','.join(r.__doc__ for r in node.rules if r.__doc__)}}}"
                if node.rules
                else ""
            ),
            shape="box",
            peripheries="2" if node.node_type is NodeType.DEFINITION else "",
            style="dashed" if node.node_type is not NodeType.DEFINITION else "",
            color="#B10000",
            fontcolor="#B10000",
        )
    elif isinstance(node.action, Push):
        subgraph.node(
            name=str(node.node_id),
            label=f"↓{node.node_id} {node.action.path} "
            + (
                f"<{node.position.row}, {node.position.column}>"
                if node.position
                else "<>"
            )
            + (
                f"{{{','.join(r.__doc__ for r in node.rules if r.__doc__)}}}"
                if node.rules
                else ""
            ),
            shape="box",
            style="dashed",
            color="#00B1B1",
            fontcolor="#00B1B1",
        )

    elif node.action:
        subgraph.node(
            str(node.node_id), node.name or str(node.node_id) or "", shape="box"
        )
    else:
        subgraph.node(
            name=str(node.node_id),
            label=node.name or str(node.node_id) or "",
            shape="circle",
            style="filled" if node is scope_graph.root else "",
            fillcolor="black" if node is scope_graph.root else "",
            fixedsize="true",
            width="0.4" if node.name else "0.3",
            height="0.4" if node.name else "0.3",
            color="#B100B1" if node.name else "",
            fontcolor="#B100B1" if node.name else "",
        )


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
        module_name="stove",
    )

    graph = build_graph([source])
    definition = graph.traverse(graph.root, stack=("stove", ".", "broil"))
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
        module_name="stove",
    )
    source2 = make_source(
        """
    from stove import broil

    broil()
    """,
        module_name="kitchen",
    )

    graph = build_graph([source1, source2])
    definition = graph.traverse(graph.root, stack=("kitchen", ".", "broil"))
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
        module_name="stove",
    )
    graph = build_graph([source])
    definition = graph.traverse(
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
        module_name="chef",
    )
    source2 = make_source(
        """
    from stove import *
    """,
        module_name="kitchen",
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
        module_name="stove",
    )
    graph = build_graph([source1, source2, source3])
    definition = graph.traverse(graph.root, stack=("chef", ".", "stove", ".", "broil"))
    assert definition.position == Position(source3, 5, 8)


def test_assignment_occurrences() -> None:
    source1 = make_source(
        """
    from kitchen import Stove

    stove = Stove()
    stove.broil()
    """,
        module_name="chef",
    )
    source2 = make_source(
        """
    from stove import *
    """,
        module_name="kitchen",
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
        module_name="stove",
    )
    positions = all_occurrence_positions(
        Position(source1, 4, 6), sources=[source1, source2, source3]
    )
    assert positions == [
        Position(source3, 5, 8),
        Position(source1, 4, 6),
    ]


def test_finds_global_variable() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        global var
        foo = var
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 10),
    ]


def test_reassignment() -> None:
    source = make_source(
        """
    var = 12
    var = 13
    """
    )

    position = Position(source, 2, 0)
    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 2, 0),
    ]


def test_distinguishes_local_variables_from_global() -> None:
    source = make_source(
        """
        def fun():
            var = 12
            var2 = 13
            result = var + var2
            del var
            return result

        var = 20
        """
    )

    position = source.position(row=2, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    ]


def test_finds_non_local_variable() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    ]


def test_finds_non_local_variable_defined_after_use() -> None:
    source = make_source(
        """
    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(5, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 2, 13),
        Position(source, 5, 0),
    ]


def test_does_not_rename_random_attributes() -> None:
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = source.position(row=3, column=0)

    assert all_occurrence_positions(position) == [source.position(row=3, column=0)]


def test_finds_parameter() -> None:
    source = make_source(
        """
        def fun(arg=1):
            print(arg)

        arg = 8
        fun(arg=arg)
        """
    )

    assert all_occurrence_positions(source.position(1, 8)) == [
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    ]


def test_finds_function() -> None:
    source = make_source(
        """
        def fun():
            return 'result'
        result = fun()
        """
    )

    assert [source.position(1, 4), source.position(3, 9)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_class() -> None:
    source = make_source(
        """
        class Class:
            pass

        instance = Class()
        """
    )

    assert [source.position(1, 6), source.position(4, 11)] == all_occurrence_positions(
        source.position(1, 6)
    )


def test_finds_method_name() -> None:
    source = make_source(
        """
        class A:

            def method(self):
                pass

        unbound = A.method
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=6, column=12),
    ]


def test_finds_passed_argument() -> None:
    source = make_source(
        """
        var = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, var)
        """
    )

    assert [source.position(1, 0), source.position(4, 7)] == all_occurrence_positions(
        source.position(1, 0)
    )


def test_does_not_find_method_of_unrelated_class() -> None:
    source = make_source(
        """
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
        """
    )

    occurrences = all_occurrence_positions(source.position(3, 8))

    assert [
        source.position(3, 8),
        source.position(7, 13),
        source.position(20, 2),
    ] == occurrences


def test_finds_definition_from_call() -> None:
    source = make_source(
        """
        def fun():
            pass

        def bar():
            fun()
        """
    )

    assert [source.position(1, 4), source.position(5, 4)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_considers_self_properties_instance_properties() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(4, 13))

    assert [source.position(4, 13), source.position(7, 20)] == occurrences


def test_finds_value_assigned_to_property() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(3, 23))

    assert [source.position(3, 23), source.position(4, 24)] == occurrences


def test_finds_dict_comprehension_variables() -> None:
    source = make_source(
        """
        var = 1
        foo = {var: None for var in range(100) if var % 3}
        var = 2
        """
    )

    position = source.position(row=2, column=21)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=21),
        source.position(row=2, column=42),
    ]


def test_finds_list_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = [
            var for var in range(100) if var % 3]
        var = 200
        """
    )

    position = source.position(row=3, column=12)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=4),
        source.position(row=3, column=12),
        source.position(row=3, column=33),
    ]


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = {var for var in range(100) if var % 3}
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = (var for var in range(100) if var % 3)
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_loop_variables() -> None:
    source = make_source(
        """
        var = None
        for var in ['foo']:
            print(var)
        print(var)
        """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=4),
        source.position(row=3, column=10),
        source.position(row=4, column=6),
    ]


def test_finds_tuple_unpack() -> None:
    source = make_source(
        """
    foo, var = 1, 2
    print(var)
    """
    )

    position = source.position(row=1, column=5)

    assert all_occurrence_positions(position) == [
        source.position(1, 5),
        source.position(2, 6),
    ]


def test_finds_superclasses() -> None:
    source = make_source(
        """
        class A:

            def method(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.method()
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=11, column=2),
    ]


def test_recognizes_multiple_assignment_1() -> None:
    source = make_source(
        """
    a = 1
    foo, bar = a, a
    """
    )

    position = source.position(row=1, column=0)
    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=11),
        source.position(row=2, column=14),
    ]


def test_recognizes_multiple_assignments() -> None:
    source = make_source(
        """
    class A:
        def method(self):
            pass

    class B:
        def method(self):
            pass

    foo, bar = A(), B()
    foo.method()
    bar.method()
    """
    )

    position = source.position(row=2, column=8)

    assert all_occurrence_positions(position) == [
        source.position(2, 8),
        source.position(10, 4),
    ]


def test_finds_enclosing_scope_variable_from_comprehension() -> None:
    source = make_source(
        """
    var = 3
    res = [foo for foo in range(100) if foo % var]
    """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(1, 0),
        source.position(2, 42),
    ]


def test_finds_static_method() -> None:
    source = make_source(
        """
        class A:

            @staticmethod
            def method(arg):
                pass

        a = A()
        b = a.method('foo')
        """
    )

    position = source.position(row=4, column=8)

    assert all_occurrence_positions(position) == [
        source.position(4, 8),
        source.position(8, 6),
    ]


def test_finds_method_after_call() -> None:
    source = make_source(
        """
        class A:

            def method(arg):
                pass

        b = A().method('foo')
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(6, 8),
    ]


def test_finds_argument() -> None:
    source = make_source(
        """
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """
    )

    position = source.position(row=3, column=18)

    assert all_occurrence_positions(position) == [
        source.position(3, 18),
        source.position(4, 14),
        source.position(8, 17),
    ]


def test_finds_method_but_not_function() -> None:
    source = make_source(
        """
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """
    )
    position = source.position(3, 8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(7, 13),
    ]


def test_finds_global_variable_in_method_scope() -> None:
    source = make_source(
        """
    b = 12

    class Foo:

        def bar(self):
            return b
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 6, 15),
    ]


def test_treats_staticmethod_args_correctly() -> None:
    source = make_source(
        """
    class ClassName:

        def old(self):
            pass

        @staticmethod
        def foo(whatever):
            whatever.old()
    """
    )
    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [Position(source, 3, 8)]


def test_finds_nonlocal_variable() -> None:
    source = make_source(
        """
    b = 12

    def foo():
        b = 20
        def bar():
            nonlocal b
            b = 20
        b = 1
        return b

    print(b)
    """
    )

    position = Position(source, 4, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 4, 4),
        Position(source, 6, 17),
        Position(source, 7, 8),
        Position(source, 8, 4),
        Position(source, 9, 11),
    ]


def test_finds_multiple_definitions() -> None:
    source = make_source(
        """
    a = 12
    if a > 10:
        b = a + 100
    else:
        b = 3 - a
    print(b)
    """
    )
    position = Position(source, 3, 4)
    assert all_occurrence_positions(position) == [
        Position(source, 3, 4),
        Position(source, 5, 4),
        Position(source, 6, 6),
    ]


def test_finds_method_in_super_call() -> None:
    source = make_source(
        """
    class Foo:

        def bar(self):
            pass


    class Bar(Foo):

        def bar(self):
            super().bar()
    """
    )

    position = Position(source, 3, 8)

    assert all_occurrence_positions(position) == [
        Position(source, 3, 8),
        Position(source, 10, 16),
    ]


def test_does_not_rename_imported_names() -> None:
    source = make_source(
        """
        from a import b


        def foo():
            b = 1
            print(b)

        b()
        """
    )
    position = Position(source, 5, 4)

    assert all_occurrence_positions(position) == [
        Position(source, 5, 4),
        Position(source, 6, 10),
    ]


def test_finds_namespace_imports() -> None:
    source1 = make_source(
        """
        def old():
            pass
        """,
        module_name="foo",
    )
    source2 = make_source(
        """
        import foo
        foo.old()
        """,
        module_name="bar",
    )
    position = Position(source1, 1, 4)
    assert all_occurrence_positions(position, sources=[source1, source2]) == [
        Position(source1, 1, 4),
        Position(source2, 2, 4),
    ]
