import ast
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import singledispatch
from typing import Protocol

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source

Path = tuple[str, ...]


class Action(Protocol):
    def __call__(self, stack: Path) -> Path:
        ...


class Precondition(Protocol):
    def __call__(self, stack: Path) -> bool:
        ...


class NotFoundError(Exception):
    pass


class NotInScopeError(Exception):
    pass


@dataclass
class Edge:
    precondition: Callable[[Path], bool] | None = None
    action: Callable[[Path], Path] | None = None


@dataclass
class ScopeNode:
    node_id: int
    name: str | None
    position: Position | None

    def __init__(
        self,
        node_id: int,
        name: str | None = None,
        position: Position | None = None,
    ):
        self.node_id = node_id
        self.name = name
        self.position = position


@dataclass
class ScopeGraph:
    nodes: dict[int, ScopeNode]
    edges: dict[int, dict[int, Edge]]
    root: ScopeNode

    def __init__(self) -> None:
        self.max_id = 0
        self.root = ScopeNode(node_id=self.new_id())
        self.nodes = {self.root.node_id: self.root}
        self.edges = defaultdict(dict)

    def new_id(self) -> int:
        new_id = self.max_id
        self.max_id += 1
        return new_id

    def add_scope(self, *, parent_scope: ScopeNode | None = None) -> ScopeNode:
        new_scope = ScopeNode(node_id=self.new_id())
        self._add_node(new_scope)
        if parent_scope:
            self.edges[new_scope.node_id] = {parent_scope.node_id: Edge()}
        return new_scope

    def add_node(
        self,
        name: str | None = None,
        position: Position | None = None,
    ) -> ScopeNode:
        node = ScopeNode(node_id=self.new_id(), name=name, position=position)
        self._add_node(node)
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
            node = self.nodes[next_id]
            yield self.nodes[next_id], edge
            queue.extend(
                (next_id, edge) for (next_id, edge) in self.edges[next_id].items()
            )

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
    while queue:
        (next_id, edge, stack) = queue.popleft()

        if edge.action:
            stack = edge.action(stack)

        if not stack:
            return graph.nodes[next_id]

        queue.extend(
            [
                (next_id, edge, stack)
                for (next_id, edge) in graph.edges[next_id].items()
                if edge.precondition is None or edge.precondition(stack)
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
    node: ast.AST, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    current_scope = visit(item, source, graph, current_scope)
        elif isinstance(value, ast.AST):
            current_scope = visit(value, source, graph, current_scope)
    return current_scope


@singledispatch
def visit(
    node: ast.AST, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    return generic_visit(node, source, graph, current_scope)


@visit.register
def visit_module(
    node: ast.Module, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    current_scope = graph.add_scope()

    for statement_or_expression in node.body:
        current_scope = graph.add_scope(parent_scope=current_scope)
        visit(statement_or_expression, source, graph, current_scope)

    graph.link(
        graph.root,
        current_scope,
        precondition=Top((source.module_name, ".")),
        action=Pop(2),
    )
    return graph.root


@visit.register
def visit_name(
    node: ast.Name, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    name = node.id
    position = node_position(node, source)

    if isinstance(node.ctx, ast.Store):
        definition = graph.add_node(name=name, position=position)
        graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))
        return definition

    reference = graph.add_node(name=name, position=position)
    graph.link(reference, current_scope, action=Push((name,)))
    return reference


@visit.register
def visit_assign(
    node: ast.Assign, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    first_scope = current_scope

    parent_scope = next(
        graph.nodes[other_id]
        for other_id, edge in graph.edges[first_scope.node_id].items()
        if not (edge.precondition or edge.action)
    )

    for node_target in node.targets:
        current_scope = visit(node_target, source, graph, current_scope)

    reference_scope = visit(node.value, source, graph, parent_scope)
    graph.link(current_scope, reference_scope)
    return first_scope


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    current_scope = visit(node.value, source, graph, current_scope)

    position = node_position(node, source)
    names = names_from(node.value)
    for name in names:
        position = source.find_after(name, position)
    attribute = graph.add_node(name=node.attr, position=position)
    graph.link(attribute, current_scope, action=Push((".", node.attr)))
    return attribute


@visit.register
def visit_call(
    node: ast.Call, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    current_scope = visit(node.func, source, graph, current_scope)

    new_scope = graph.add_scope(parent_scope=current_scope)
    graph.link(current_scope, new_scope, action=Push(("()",)))

    return new_scope


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    name = node.name
    position = node_position(node, source, column_offset=len("def "))
    definition = graph.add_node(name=name, position=position)
    graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))

    current_function_scope = graph.add_scope()
    for statement in node.body:
        current_function_scope = visit(statement, source, graph, current_function_scope)

    return current_scope


@visit.register
def visit_class_definition(
    node: ast.ClassDef, source: Source, graph: ScopeGraph, current_scope: ScopeNode
) -> ScopeNode:
    name = node.name
    position = node_position(node, source, column_offset=len("class "))
    definition = graph.add_node(name=name, position=position)
    graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))

    current_class_scope = graph.add_scope()
    for statement in node.body:
        current_class_scope = visit(statement, source, graph, current_class_scope)

    # TODO: split this in two when we have to handle class attributes differently
    # from instance attributes
    graph.link(
        definition, current_class_scope, precondition=Top(("()", ".")), action=Pop(2)
    )

    return current_scope


@visit.register
def visit_import_from(
    node: ast.ImportFrom, source: Source, graph: ScopeGraph, current_scope: ScopeNode
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
            graph.link(
                current_scope,
                graph.root,
                action=Push(module_path),
            )
        else:
            local_name = alias.asname or name
            position = source.find_after(name, start)

            import_scope = graph.add_node(name=local_name, position=position)
            graph.link(
                current_scope,
                import_scope,
                precondition=Top((local_name,)),
                action=Sequence((Pop(1), Push(module_path + (name,)))),
            )
            graph.link(import_scope, graph.root)
    return current_scope


@dataclass
class Top:
    path: Path

    def __call__(self, stack: Path) -> bool:
        return stack[: len(self.path)] == self.path


@dataclass
class Pop:
    number: int

    def __call__(self, stack: Path) -> Path:
        return stack[self.number :]


@dataclass
class Push:
    path: Path

    def __call__(self, stack: Path) -> Path:
        return self.path + stack


@dataclass
class Sequence:
    actions: tuple[Action, ...]

    def __call__(self, stack: Path) -> Path:
        for action in self.actions:
            stack = action(stack)

        return stack


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


def build_graph(sources: Iterable[Source]) -> ScopeGraph:
    graph = ScopeGraph()

    for source in sources:
        visit(source.get_ast(), source=source, graph=graph, current_scope=graph.root)

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

    definition = traverse(graph, graph.root, stack=("chef", ".", "stove", ".", "broil"))
    assert definition.position == Position(source3, 5, 8)
