import ast

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from functools import singledispatch
from uuid import UUID, uuid4

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


Path = tuple[str, ...]
Action = Callable[[Path], Path]
Precondition = Callable[[Path], bool]


@dataclass
class Edge:
    precondition: Callable[[Path], bool] | None = None
    action: Callable[[Path], Path] | None = None


class NodeType(Enum):
    SCOPE = "SCOPE"
    DEFINITION = "DEFINITION"
    REFERENCE = "REFERENCE"


@dataclass
class ScopeNode:
    node_id: UUID
    name: str | None
    position: Position | None
    node_type: NodeType = NodeType.SCOPE

    def __init__(
        self,
        name: str | None = None,
        position: Position | None = None,
        node_type: NodeType = NodeType.SCOPE,
    ):
        self.node_id = uuid4()
        self.name = name
        self.position = position
        self.node_type = node_type


@dataclass
class ScopeGraph:
    nodes: dict[UUID, ScopeNode]
    edges: dict[UUID, dict[UUID, Edge]]
    root: ScopeNode

    def __init__(self) -> None:
        self.root = ScopeNode()
        self.nodes = {self.root.node_id: self.root}
        self.edges = defaultdict(dict)

    def add_scope(self, *, parent_scope: ScopeNode | None = None) -> ScopeNode:
        new_scope = ScopeNode()
        self._add_node(new_scope)
        if parent_scope:
            self.edges[new_scope.node_id] = {parent_scope.node_id: Edge()}
        return new_scope

    def add_node(
        self,
        links_to: ScopeNode | None = None,
        name: str | None = None,
        position: Position | None = None,
        node_type: NodeType = NodeType.SCOPE,
    ) -> ScopeNode:
        node = ScopeNode(name=name, position=position, node_type=node_type)
        self._add_node(node)
        if links_to:
            self.edges[node.node_id] = {links_to.node_id: Edge()}
        return node

    def _add_node(self, node: ScopeNode) -> None:
        self.nodes[node.node_id] = node

    def walk(self, node: ScopeNode) -> Iterator[tuple[ScopeNode, Edge | None]]:
        yield node, None
        queue = deque(
            (node_id, edge) for node_id, edge in self.edges[node.node_id].items()
        )
        while queue:
            next_id, edge = queue.popleft()
            yield self.nodes[next_id], edge
            queue.extend(
                (next_id, edge) for (next_id, edge) in self.edges[next_id].items()
            )

    def find_scope(self, start: ScopeNode) -> ScopeNode:
        for node, _ in self.walk(start):
            if node.node_type == NodeType.SCOPE:
                return node

        raise NotInScopeError

    def link(
        self,
        scope_from: ScopeNode,
        scope_to: ScopeNode,
        precondition: Precondition | None = None,
        action: Action | None = None,
    ) -> None:
        self.edges[scope_from.node_id][scope_to.node_id] = Edge(
            precondition=precondition, action=action
        )


def traverse(graph: ScopeGraph, scope: ScopeNode, stack: Path) -> ScopeNode:
    if not stack:
        return scope

    node_id = scope.node_id

    queue = deque(
        (node_id, edge, stack) for node_id, edge in graph.edges[node_id].items()
    )
    seen = defaultdict(int)
    while queue:
        (next_id, edge, stack) = queue.popleft()
        seen[next_id] += 1

        print(f"{edge.precondition=}, {stack=}")
        if edge.precondition is None or edge.precondition(stack):
            if edge.action:
                stack = edge.action(stack)
                print(f"{edge.action=}, {stack=}")
            if not stack:
                return graph.nodes[next_id]
            else:
                queue.extend(
                    [
                        (next_id, edge, stack)
                        for (next_id, edge) in graph.edges[next_id].items()
                        if seen[next_id] < 5
                    ]
                )

    raise NotFoundError


def node_position(
    node: ast.AST, source: Source, row_offset: int = 0, column_offset: int = 0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


def generic_visit(
    node: ast.AST,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    print(f"{node=}")
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    current_node = visit(item, source, graph, current_node)
        elif isinstance(value, ast.AST):
            current_node = visit(value, source, graph, current_node)
    assert current_node
    return current_node


@singledispatch
def visit(
    node: ast.AST,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    return generic_visit(node, source, graph, current_node)


@visit.register
def visit_module(
    node: ast.Module,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:

    module_scope = graph.add_scope()
    final_scope = graph.find_scope(generic_visit(node, source, graph, module_scope))
    graph.link(
        graph.root,
        final_scope,
        precondition=top((source.module_name, ".")),
        action=pop(2),
    )
    return graph.root


@visit.register
def visit_name(
    node: ast.Name,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    current_scope = graph.find_scope(current_node) if current_node else None
    new_scope = graph.add_scope(parent_scope=current_scope)
    name = node.id
    position = node_position(node, source, column_offset=len("def "))
    print(f"{name=}")

    if isinstance(node.ctx, ast.Store):
        definition = graph.add_node(
            name=name, position=position, node_type=NodeType.DEFINITION
        )

        graph.link(new_scope, definition, precondition=top((name,)), action=pop(1))

        return definition

    else:
        reference = graph.add_node(
            name=name, position=position, node_type=NodeType.REFERENCE
        )

        graph.link(reference, new_scope, action=push((name,)))
        return new_scope


# @visit.register
# def visit_attribute(
#     node: ast.Attribute,
#     source: Source,
#     graph: ScopeGraph,
#     current_node: ScopeNode | None = None,
# ) -> ScopeNode:
#     new_scope = graph.add_node(links_to=current_node)

#     position = node_position(node, source)
#     names = names_from(node.value)
#     for name in names:
#         position = source.find_after(name, position)

#     definition = graph.add_node(name=node.attr, position=position)

#     current_node = visit(node.value, source, graph, current_node)

#     node.value, node.attr


@singledispatch
def names_from(node: ast.AST) -> Path:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> Path:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> Path:
    return names_from(node.value) + (".", node.attr)


@names_from.register
def call_names(node: ast.Call) -> Path:
    names = names_from(node.func) + ("()",)
    return names


# @visit.register
# def visit_call(
#     node: ast.Call,
#     source: Source,
#     graph: ScopeGraph,
#     current_node: ScopeNode | None = None,
# ) -> ScopeNode:

#     current_node = visit(node.func, source, graph, current_node)

#     new_scope = graph.add_node(links_to=current_node)
#     graph.link(current_node, new_scope, action=push(("()",)))

#     return new_scope


@visit.register
def visit_function_definition(
    node: ast.FunctionDef,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    current_scope = graph.find_scope(current_node) if current_node else None
    new_scope = graph.add_scope(parent_scope=current_scope)

    name = node.name
    position = node_position(node, source, column_offset=len("def "))
    definition = graph.add_node(name=name, position=position)
    graph.link(new_scope, definition, precondition=top((name,)), action=pop(1))

    return new_scope


@visit.register
def visit_class(
    node: ast.ClassDef,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    current_scope = graph.find_scope(current_node) if current_node else None
    new_scope = graph.add_scope(parent_scope=current_scope)

    name = node.name
    position = node_position(node, source, column_offset=len("class "))
    definition = graph.add_node(name=name, position=position)
    graph.link(new_scope, definition, precondition=top((name,)), action=pop(1))

    current_node = graph.add_scope()
    for statement in node.body:
        current_node = graph.find_scope(visit(statement, source, graph, current_node))

    # TODO: split this in two when we have to handle class attributes differently
    # from instance attributes
    graph.link(definition, current_node, precondition=top(("()", ".")), action=pop(2))

    return new_scope


@visit.register
def visit_import_from(
    node: ast.ImportFrom,
    source: Source,
    graph: ScopeGraph,
    current_node: ScopeNode | None = None,
) -> ScopeNode:
    start = node_position(node, source, column_offset=len("from "))
    if node.module is None:
        module_path: tuple[str, ...] = (".",)
    else:
        module_path = ()
        for module_name in node.module.split("."):
            module_path += (module_name, ".")
    for alias in node.names:
        name = alias.name
        if name == "*":
            new_scope = graph.add_node(links_to=current_node)
            graph.link(
                new_scope,
                graph.root,
                action=push(module_path),
            )
        else:
            local_name = alias.asname or name
            position = source.find_after(name, start)
            new_scope = graph.add_node(links_to=current_node)

            import_scope = graph.add_node(name=local_name, position=position)
            graph.link(
                new_scope,
                import_scope,
                precondition=top((local_name,)),
                action=sequence(pop(1), push(module_path + (name,))),
            )
            graph.link(import_scope, graph.root)
        current_node = new_scope
    return new_scope


def top(path: Path) -> Precondition:
    def precondition(stack: Path) -> bool:
        return stack[: len(path)] == path

    return precondition


def pop(number: int) -> Action:
    def action(stack: Path) -> Path:
        return stack[number:]

    return action


def push(path: Path) -> Action:
    def execute(stack: Path) -> Path:

        return path + stack

    return execute


def sequence(*actions: Action) -> Action:
    def execute(stack: Path) -> Path:
        for action in actions:
            stack = action(stack)
        return stack

    return execute


class NotFoundError(Exception):
    pass


class NotInScopeError(Exception):
    pass


def build_graph(sources: Iterable[Source]) -> ScopeGraph:
    graph = ScopeGraph()

    for source in sources:
        visit(source.get_ast(), source=source, graph=graph)

    return graph


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
    definition = traverse(graph, graph.root, stack=("stove", ".", "broil"))
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
    definition = traverse(graph, graph.root, stack=("kitchen", ".", "broil"))
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
    definition = traverse(
        graph, graph.root, stack=("stove", ".", "Stove", "()", ".", "broil")
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

    from pprint import pprint

    pprint(graph.edges)
    definition = traverse(graph, graph.root, stack=("chef", ".", "stove", ".", "broil"))
    assert definition.position == Position(source3, 5, 8)
