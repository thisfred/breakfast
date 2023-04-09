import ast
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import singledispatch
from typing import Protocol

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source

try:
    import graphviz
except ImportError:
    graphviz = None

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


class NodeType(Enum):
    SCOPE = auto()
    MODULE_SCOPE = auto()
    DEFINITION = auto()
    REFERENCE = auto()


@dataclass(frozen=True)
class ScopeNode:
    node_id: int
    name: str | None = None
    position: Position | None = None
    precondition: Precondition | None = field(hash=False, default=None)
    action: Action | None = field(hash=False, default=None)
    node_type: NodeType = NodeType.SCOPE


NULL_SCOPE = ScopeNode(-1)


@dataclass
class ScopeGraph:
    nodes: dict[int, ScopeNode]
    edges: dict[int, set[int]]
    root: ScopeNode
    references: dict[str, list[ScopeNode]]
    positions: dict[Position, list[ScopeNode]]
    module_roots: dict[str, ScopeNode]

    def __init__(self) -> None:
        self.max_id = 0
        self.root = ScopeNode(node_id=self.new_id())
        self.nodes = {self.root.node_id: self.root}
        self.edges = defaultdict(set)
        self.references = defaultdict(list)
        self.positions = defaultdict(list)
        self.module_roots = {}

    def new_id(self) -> int:
        new_id = self.max_id
        self.max_id += 1
        return new_id

    def _add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
        precondition: Precondition | None = None,
        action: Action | None = None,
        is_definition: bool = False,
    ) -> ScopeNode:
        if is_definition:
            node_type = NodeType.DEFINITION
        else:
            node_type = NodeType.REFERENCE if name else NodeType.SCOPE

        new_scope = ScopeNode(
            node_id=self.new_id(),
            name=name,
            position=position,
            precondition=precondition,
            action=action,
            node_type=node_type,
        )
        self._add_node(new_scope)
        return new_scope

    def add_top_scope(
        self,
        precondition: Precondition | None = None,
        action: Action | None = None,
        name: str | None = None,
    ) -> ScopeNode:
        new_scope = self._add_scope(name=name, precondition=precondition, action=action)
        return new_scope

    def add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
        precondition: Precondition | None = None,
        action: Action | None = None,
        is_definition: bool = False,
        link_from: ScopeNode | None = None,
        link_to: ScopeNode | None = None,
    ) -> ScopeNode:
        new_scope = self._add_scope(
            name=name,
            position=position,
            precondition=precondition,
            action=action,
            is_definition=is_definition,
        )

        if link_to:
            self.link(new_scope, link_to)

        if link_from:
            self.link(link_from, new_scope)

        return new_scope

    def _add_node(self, node: ScopeNode) -> None:
        self.nodes[node.node_id] = node
        if node.name and node.node_type is NodeType.REFERENCE:
            self.references[node.name].append(node)
        if node.position:
            self.positions[node.position].append(node)

    def link(self, scope_from: ScopeNode, scope_to: ScopeNode) -> None:
        self.edges[scope_from.node_id].add(scope_to.node_id)


def view_graph(graph: ScopeGraph) -> None:
    if graphviz is None:
        return

    dot = graphviz.Digraph()
    dot.attr(rankdir="BT")
    for node in graph.nodes.values():
        if isinstance(node.action, Pop):
            dot.node(
                str(node.node_id),
                f"↑{node.precondition.path}",  # type: ignore[union-attr]
                shape="box",
                peripheries="2" if node.node_type is NodeType.DEFINITION else "",
                style="dashed" if node.node_type is not NodeType.DEFINITION else "",
                color="#B10000",
                fontcolor="#B50000",
            )
        elif isinstance(node.action, Push):
            dot.node(
                str(node.node_id),
                f"↓{node.action.path}",
                shape="box",
                style="dashed",
                color="#00B5B5",
                fontcolor="#00B1B1",
            )

        elif node.precondition or node.action:
            dot.node(str(node.node_id), node.name or "", shape="box")
        else:
            dot.node(
                str(node.node_id),
                node.name or "",
                shape="circle",
                style="filled" if node is graph.root else "",
                fillcolor="black" if node is graph.root else "",
                fixedsize="true",
                width="0.3",
                height="0.3",
            )

    for from_id, to_nodes in graph.edges.items():
        # from_node = graph.nodes[from_id]
        for to_node_id in to_nodes:
            to_node = graph.nodes[to_node_id]
            dot.edge(
                str(from_id),
                str(to_node_id),
                len="0.1" if (to_node.action or to_node.precondition) else "",
            )

    dot.render(view=True)


def _traverse(graph: ScopeGraph, start: ScopeNode | None = None) -> Iterator[ScopeNode]:
    if not start:
        start = graph.root

    queue: deque[ScopeNode] = deque([start])

    seen: set[ScopeNode] = {start}
    while queue:
        node = queue.popleft()

        yield node

        for next_id in graph.edges[node.node_id]:
            next_node = graph.nodes[next_id]
            if next_node in seen:
                continue
            seen.add(next_node)
            queue.append(next_node)


def traverse(graph: ScopeGraph, scope: ScopeNode, stack: Path) -> ScopeNode:
    node_id = scope.node_id

    if scope.action:
        stack = scope.action(stack)

    queue: deque[tuple[ScopeNode, Path]] = deque()
    extend_queue(graph, node_id, stack, queue)

    while queue:
        (node, stack) = queue.popleft()

        if node.action:
            stack = node.action(stack)

        if node.node_type is NodeType.DEFINITION and not stack:
            return node

        extend_queue(graph, node.node_id, stack, queue)

    raise NotFoundError


def extend_queue(
    graph: ScopeGraph,
    node_id: int,
    stack: Path,
    queue: deque[tuple[ScopeNode, Path]],
) -> None:
    for next_id in graph.edges[node_id]:
        next_node = graph.nodes[next_id]
        if next_node.precondition is None or next_node.precondition(stack):
            queue.append((next_node, stack))


def all_occurrence_positions(
    position: Position, *, sources: Iterable[Source] | None = None
) -> set[Position]:
    graph = build_graph(sources or [position.source])
    scopes_for_position = graph.positions.get(position)
    if not scopes_for_position:
        raise NotFoundError

    for scope in scopes_for_position:
        if scope.name is not None:
            found = scope
            name = scope.name
            possible_occurrences = graph.references[name]
            break
    else:
        raise NotFoundError

    definitions: dict[ScopeNode, list[ScopeNode]] = defaultdict(list)
    found_definition = None
    for occurrence in possible_occurrences:
        definition = traverse(graph, occurrence, stack=())

        definitions[definition].append(occurrence)
        if found in (definition, occurrence):
            found_definition = definition

    if not found_definition:
        raise NotFoundError

    assert found_definition.position
    return {found_definition.position} | {
        d.position for d in definitions[found_definition] if d.position
    }


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
    current_scope: ScopeNode,
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
    node: ast.AST,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    return generic_visit(node, source, graph, current_scope)


@visit.register
def visit_module(
    node: ast.Module,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    current = graph.add_top_scope()

    module_root = graph.add_top_scope()
    graph.module_roots[source.module_name] = module_root

    for statement_or_expression in node.body:
        current = graph.add_scope(link_to=current)
        visit(
            statement_or_expression,
            source,
            graph,
            current,
        )
    graph.link(module_root, current)

    current = graph.add_scope(link_to=module_root, precondition=Top("."), action=Pop())
    current = graph.add_scope(
        link_to=current,
        precondition=Top(source.module_name),
        action=Pop(),
        is_definition=True,
    )
    graph.link(
        graph.root,
        current,
    )
    return graph.root


@visit.register
def visit_name(
    node: ast.Name,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    name = node.id
    position = node_position(node, source)

    if isinstance(node.ctx, ast.Store):
        current = graph.add_scope(
            link_from=current_scope,
            name=name,
            position=position,
            precondition=Top(name),
            action=Pop(),
            is_definition=True,
        )
        return current

    current = graph.add_scope(
        link_to=current_scope,
        name=name,
        position=position,
        action=Push(name),
    )
    return current


@visit.register
def visit_assign(
    node: ast.Assign,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    parent_scope = current_scope

    for node_target in node.targets:
        assert isinstance(node_target, ast.Name)
        current_scope = visit(node_target, source, graph, current_scope)
        graph.add_scope(
            link_to=parent_scope,
            name=node_target.id,
            position=node_position(node_target, source),
            action=Push(node_target.id),
        )

    reference_scope = visit(node.value, source, graph, parent_scope)
    graph.link(current_scope, reference_scope)
    return parent_scope


@visit.register
def visit_attribute(
    node: ast.Attribute,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    current_scope = visit(node.value, source, graph, current_scope)

    position = node_position(node, source)
    names = names_from(node.value)
    for name in names:
        position = source.find_after(name, position)

    current_scope = graph.add_scope(link_to=current_scope, action=Push("."))

    position = source.find_after(node.attr, position)
    current_scope = graph.add_scope(
        link_to=current_scope,
        name=node.attr,
        position=position,
        action=Push(node.attr),
    )

    return current_scope


@visit.register
def visit_call(
    node: ast.Call,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    current_scope = visit(node.func, source, graph, current_scope)
    original_scope = current_scope

    current_scope = graph.add_top_scope(action=Push("()"))
    graph.link(current_scope, original_scope)
    return current_scope


@visit.register
def visit_function_definition(
    node: ast.FunctionDef,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    name = node.name
    position = node_position(node, source, column_offset=len("def "))

    graph.add_scope(
        link_from=current_scope,
        name=name,
        position=position,
        precondition=Top(name),
        action=Pop(),
        is_definition=True,
    )

    current_scope = graph.add_scope(link_to=graph.module_roots[source.module_name])
    for statement in node.body:
        current_scope = graph.add_scope(link_to=current_scope)
        visit(statement, source, graph, current_scope)

    return current_scope


@visit.register
def visit_class_definition(
    node: ast.ClassDef,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    name = node.name
    position = node_position(node, source, column_offset=len("class "))
    original_scope = current_scope

    parent = graph.add_scope(
        link_from=current_scope,
        name=name,
        position=position,
        precondition=Top(name),
        action=Pop(),
        is_definition=True,
    )

    current_class_scope = graph.add_top_scope()
    for statement in node.body:
        current_class_scope = graph.add_scope(link_to=current_class_scope)
        visit(statement, source, graph, current_class_scope)

    current_class_scope = graph.add_scope(
        link_to=current_class_scope, precondition=Top("."), action=Pop()
    )
    current_scope = graph.add_scope(
        link_to=current_class_scope, precondition=Top("()"), action=Pop()
    )
    graph.link(parent, current_scope)

    return original_scope


@visit.register
def visit_import_from(
    node: ast.ImportFrom,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
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
            parent = current_scope
            for part in module_path[::-1]:
                parent = graph.add_scope(link_from=parent, action=Push(part))
            graph.link(parent, graph.root)
        else:
            local_name = alias.asname or name
            position = source.find_after(name, start)

            parent = graph.add_scope(
                link_from=current_scope,
                name=local_name,
                position=position,
                precondition=Top(local_name),
                action=Pop(),
            )
            for part in (module_path + (name,))[::-1]:
                parent = graph.add_scope(
                    link_from=parent,
                    name=local_name,
                    position=position,
                    action=Push(part),
                )

            graph.link(parent, graph.root)
    return current_scope


@visit.register
def visit_global(
    node: ast.Global,
    source: Source,
    graph: ScopeGraph,
    current_scope: ScopeNode,
) -> ScopeNode:
    start = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, start)

        parent = graph.add_scope(
            link_from=current_scope,
            name=name,
            position=position,
            precondition=Top(name),
            action=Pop(),
        )
        parent = graph.add_scope(
            link_from=parent,
            name=name,
            position=position,
            action=Push(name),
        )
        graph.link(parent, graph.module_roots[source.module_name])

    return current_scope


@dataclass(frozen=True)
class Top:
    path: str

    def __call__(self, stack: Path) -> bool:
        return bool(stack) and stack[0] == self.path


@dataclass
class Pop:
    def __call__(self, stack: Path) -> Path:
        return stack[1:]


@dataclass
class Push:
    path: str

    def __call__(self, stack: Path) -> Path:
        return (self.path,) + stack


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
        visit(source.get_ast(), source=source, graph=graph, current_scope=NULL_SCOPE)

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
    assert positions == {
        Position(source3, 5, 8),
        Position(source1, 4, 6),
    }


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

    assert all_occurrence_positions(position) == {
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 10),
    }
