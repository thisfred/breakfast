import ast
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
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
    name: str | None = None
    position: Position | None = None
    precondition: Callable[[Path], bool] | None = None
    action: Callable[[Path], Path] | None = None


NULL_SCOPE = ScopeNode(-1)


@dataclass
class ScopePointers:
    module: ScopeNode = NULL_SCOPE
    parent: ScopeNode = NULL_SCOPE
    current: ScopeNode = NULL_SCOPE


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

    def _add_scope(
        self,
        *,
        parent_scope: ScopeNode | None = None,
        precondition: Precondition | None = None,
        action: Action | None = None,
    ) -> ScopeNode:
        new_scope = ScopeNode(
            node_id=self.new_id(), precondition=precondition, action=action
        )
        self._add_node(new_scope)
        if parent_scope:
            self.edges[new_scope.node_id] = {parent_scope.node_id: Edge()}
        return new_scope

    def add_top_scope(
        self,
        scope_pointers: ScopePointers,
        precondition: Precondition | None = None,
        action: Action | None = None,
    ) -> ScopePointers:
        scope_pointers = replace(
            scope_pointers,
            current=self._add_scope(precondition=precondition, action=action),
            parent=NULL_SCOPE,
        )
        return scope_pointers

    def add_child(
        self,
        scope_pointers: ScopePointers,
        precondition: Precondition | None = None,
        action: Action | None = None,
    ) -> ScopePointers:
        new_scope = self._add_scope(
            parent_scope=scope_pointers.current,
            precondition=precondition,
            action=action,
        )
        scope_pointers = replace(
            scope_pointers, parent=scope_pointers.current, current=new_scope
        )
        return scope_pointers

    def add_node(
        self,
        name: str | None = None,
        position: Position | None = None,
        precondition: Precondition | None = None,
        action: Action | None = None,
    ) -> ScopeNode:
        node = ScopeNode(
            node_id=self.new_id(),
            name=name,
            position=position,
            precondition=precondition,
            action=action,
        )
        self._add_node(node)
        return node

    def _add_node(self, node: ScopeNode) -> None:
        self.nodes[node.node_id] = node

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

    queue: deque[tuple[ScopeNode, Edge, Path]] = deque()
    for next_id, edge in graph.edges[node_id].items():
        next_node = graph.nodes[next_id]
        if next_node.precondition is None or next_node.precondition(stack):
            queue.append((next_node, edge, stack))

    while queue:
        (node, edge, stack) = queue.popleft()

        if node.action:
            stack = node.action(stack)

        if not stack:
            return node

        for next_id, edge in graph.edges[node.node_id].items():
            next_node = graph.nodes[next_id]
            if next_node.precondition is None or next_node.precondition(stack):
                queue.append((next_node, edge, stack))

    raise NotFoundError


def node_position(
    node: ast.AST, source: Source, row_offset: int = 0, column_offset: int = 0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


def generic_visit(
    node: ast.AST, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    scope_pointers = replace(
                        scope_pointers,
                        current=visit(item, source, graph, scope_pointers),
                    )
        elif isinstance(value, ast.AST):
            scope_pointers = replace(
                scope_pointers,
                current=visit(value, source, graph, scope_pointers),
            )
    return scope_pointers.current


@singledispatch
def visit(
    node: ast.AST, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    return generic_visit(node, source, graph, scope_pointers)


@visit.register
def visit_module(
    node: ast.Module, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    scope_pointers = graph.add_top_scope(scope_pointers)

    for statement_or_expression in node.body:
        scope_pointers = graph.add_child(scope_pointers)
        visit(statement_or_expression, source, graph, scope_pointers)

    scope_pointers = graph.add_child(
        scope_pointers, precondition=Top((source.module_name, ".")), action=Pop(2)
    )
    graph.link(
        graph.root,
        scope_pointers.current,
        precondition=Top((source.module_name, ".")),
        action=Pop(2),
    )
    return graph.root


@visit.register
def visit_name(
    node: ast.Name, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    name = node.id
    position = node_position(node, source)
    current_scope = scope_pointers.current

    if isinstance(node.ctx, ast.Store):
        definition = graph.add_node(
            name=name, position=position, precondition=Top((name,)), action=Pop(1)
        )
        graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))
        return definition

    reference = graph.add_node(name=name, position=position, action=Push((name,)))
    graph.link(reference, current_scope, action=Push((name,)))
    return reference


@visit.register
def visit_assign(
    node: ast.Assign, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    first_scope = scope_pointers.current

    parent_scope = next(
        graph.nodes[other_id]
        for other_id, edge in graph.edges[first_scope.node_id].items()
        if not (edge.precondition or edge.action)
    )

    for node_target in node.targets:
        current_scope = visit(node_target, source, graph, scope_pointers)

    # XXX: set parent?
    scope_pointers = replace(scope_pointers, current=parent_scope)
    reference_scope = visit(node.value, source, graph, scope_pointers)
    graph.link(current_scope, reference_scope)
    return first_scope


@visit.register
def visit_attribute(
    node: ast.Attribute,
    source: Source,
    graph: ScopeGraph,
    scope_pointers: ScopePointers,
) -> ScopeNode:
    current_scope = visit(node.value, source, graph, scope_pointers)

    position = node_position(node, source)
    names = names_from(node.value)
    for name in names:
        position = source.find_after(name, position)
    attribute = graph.add_node(
        name=node.attr, position=position, action=Push((".", node.attr))
    )
    graph.link(attribute, current_scope, action=Push((".", node.attr)))
    return attribute


@visit.register
def visit_call(
    node: ast.Call, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    scope_pointers = replace(
        scope_pointers, current=visit(node.func, source, graph, scope_pointers)
    )
    original_scope = scope_pointers.current

    scope_pointers = graph.add_top_scope(scope_pointers, action=Push(("()",)))
    graph.link(scope_pointers.current, original_scope, action=Push(("()",)))
    return scope_pointers.current


@visit.register
def visit_function_definition(
    node: ast.FunctionDef,
    source: Source,
    graph: ScopeGraph,
    scope_pointers: ScopePointers,
) -> ScopeNode:
    name = node.name
    position = node_position(node, source, column_offset=len("def "))
    definition = graph.add_node(
        name=name, position=position, precondition=Top((name,)), action=Pop(1)
    )
    current_scope = scope_pointers.current
    graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))

    scope_pointers = graph.add_top_scope(scope_pointers)
    for statement in node.body:
        scope_pointers = graph.add_child(scope_pointers)
        visit(statement, source, graph, scope_pointers)

    return current_scope


@visit.register
def visit_class_definition(
    node: ast.ClassDef, source: Source, graph: ScopeGraph, scope_pointers: ScopePointers
) -> ScopeNode:
    current_scope = scope_pointers.current
    name = node.name
    position = node_position(node, source, column_offset=len("class "))
    definition = graph.add_node(
        name=name, position=position, precondition=Top((name,)), action=Pop(1)
    )
    graph.link(current_scope, definition, precondition=Top((name,)), action=Pop(1))

    scope_pointers = graph.add_top_scope(scope_pointers)
    for statement in node.body:
        scope_pointers = graph.add_child(scope_pointers)
        visit(statement, source, graph, scope_pointers)

    scope_pointers = graph.add_child(
        scope_pointers, precondition=Top(("()", ".")), action=Pop(2)
    )
    # TODO: split this in two when we have to handle class attributes differently
    # from instance attributes
    graph.link(
        definition, scope_pointers.current, precondition=Top(("()", ".")), action=Pop(2)
    )

    return current_scope


@visit.register
def visit_import_from(
    node: ast.ImportFrom,
    source: Source,
    graph: ScopeGraph,
    scope_pointers: ScopePointers,
) -> ScopeNode:
    start = node_position(node, source, column_offset=len("from "))
    current_scope = scope_pointers.current
    if node.module is None:
        module_path: tuple[str, ...] = (".",)
    else:
        module_path = ()
        for module_name in node.module.split("."):
            module_path += (module_name, ".")
    for alias in node.names:
        name = alias.name
        if name == "*":
            import_scope = graph.add_node(
                action=Push(module_path),
            )
            graph.link(scope_pointers.current, import_scope)
            graph.link(
                import_scope,
                graph.root,
                action=Push(module_path),
            )
        else:
            local_name = alias.asname or name
            position = source.find_after(name, start)

            import_scope = graph.add_node(
                name=local_name,
                position=position,
                precondition=Top((local_name,)),
                action=Sequence((Pop(1), Push(module_path + (name,)))),
            )
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
    scope_pointers = ScopePointers()

    for source in sources:
        visit(
            source.get_ast(), source=source, graph=graph, scope_pointers=scope_pointers
        )

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
