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


@dataclass(frozen=True)
class Edge:
    same_rank: bool = False
    to_enclosing_scope: bool = False


class Rule(Protocol):
    def __call__(self, edge: Edge) -> bool:
        ...


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
    rules: tuple[Rule, ...] = ()


NULL_SCOPE = ScopeNode(-1)


def no_lookup_in_enclosing_scope(edge: Edge) -> bool:
    """!e"""
    return not edge.to_enclosing_scope


@dataclass
class ScopeGraph:
    nodes: dict[int, ScopeNode]
    edges: dict[int, set[tuple[Edge, int]]]
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

    def get_parent(self, scope: ScopeNode) -> ScopeNode | None:
        for _, parent_id in self.edges[scope.node_id]:
            node = self.nodes[parent_id]
            if not (node.name or node.action or node.precondition):
                return node

        return None

    def _add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
        precondition: Precondition | None = None,
        action: Action | None = None,
        is_definition: bool = False,
        rules: tuple[Rule, ...] = (),
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
            rules=rules,
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
        same_rank: bool = False,
        to_enclosing_scope: bool = False,
        rules: tuple[Rule, ...] = (),
    ) -> ScopeNode:
        new_scope = self._add_scope(
            name=name,
            position=position,
            precondition=precondition,
            action=action,
            is_definition=is_definition,
            rules=rules,
        )

        if link_to:
            self.link(
                new_scope,
                link_to,
                same_rank=same_rank,
                to_enclosing_scope=to_enclosing_scope,
            )

        if link_from:
            self.link(
                link_from,
                new_scope,
                same_rank=same_rank,
                to_enclosing_scope=to_enclosing_scope,
            )

        return new_scope

    def _add_node(self, node: ScopeNode) -> None:
        self.nodes[node.node_id] = node
        if node.name:
            self.references[node.name].append(node)
        if node.position:
            self.positions[node.position].append(node)

    def link(
        self,
        scope_from: ScopeNode,
        scope_to: ScopeNode,
        same_rank: bool = False,
        to_enclosing_scope: bool = False,
    ) -> None:
        self.edges[scope_from.node_id].add(
            (
                Edge(same_rank=same_rank, to_enclosing_scope=to_enclosing_scope),
                scope_to.node_id,
            )
        )


def group_by_rank(graph: ScopeGraph) -> Iterable[set[int]]:
    edges_to: dict[int, set[tuple[Edge, int]]] = defaultdict(set)
    for node_id, to_nodes in graph.edges.items():
        for edge, other_id in to_nodes:
            edges_to[other_id].add((edge, node_id))

    seen_ids = set()
    groups = []
    for node_id in graph.nodes:
        if node_id in seen_ids:
            continue

        seen_ids.add(node_id)

        group = {node_id}

        to_check = [node_id]

        while to_check:
            other_ids = get_same_rank_links(to_check.pop(), graph, edges_to, seen_ids)
            group |= other_ids
            seen_ids |= other_ids
            to_check.extend(list(other_ids))

        groups.append(group)

    return groups


def get_same_rank_links(
    node_id: int,
    graph: ScopeGraph,
    edges_to: dict[int, set[tuple[Edge, int]]],
    seen_ids: set[int],
) -> set[int]:
    return (
        {n for e, n in graph.edges[node_id] if e.same_rank}
        | {n for e, n in edges_to[node_id] if e.same_rank}
    ) - seen_ids


def view_graph(graph: ScopeGraph) -> None:
    if graphviz is None:
        return

    digraph = graphviz.Digraph()
    digraph.attr(rankdir="BT")
    for same_rank_nodes in group_by_rank(graph):
        subgraph = graphviz.Digraph()
        subgraph.attr(rankdir="BT")
        subgraph.attr(rank="same")

        for node_id in same_rank_nodes:
            node = graph.nodes[node_id]
            if isinstance(node.action, Pop):
                subgraph.node(
                    name=str(node.node_id),
                    label=f"↑{node.precondition.path}"  # type: ignore[union-attr]
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
                    label=f"↓{node.action.path}"
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

            elif node.precondition or node.action:
                subgraph.node(str(node.node_id), node.name or "", shape="box")
            else:
                subgraph.node(
                    name=str(node.node_id),
                    label=node.name or "",
                    shape="circle",
                    style="filled" if node is graph.root else "",
                    fillcolor="black" if node is graph.root else "",
                    fixedsize="true",
                    width="0.3",
                    height="0.3",
                )

        digraph.subgraph(subgraph)

    for from_id, to_nodes in graph.edges.items():
        for edge, to_node_id in to_nodes:
            digraph.edge(
                str(from_id),
                str(to_node_id),
                label="e" if edge.to_enclosing_scope else "",
            )

    print(digraph.source)
    digraph.render(view=True)


def _traverse(graph: ScopeGraph, start: ScopeNode | None = None) -> Iterator[ScopeNode]:
    if not start:
        start = graph.root

    queue: deque[ScopeNode] = deque([start])

    seen: set[ScopeNode] = {start}
    while queue:
        node = queue.popleft()

        yield node

        for _, next_id in graph.edges[node.node_id]:
            next_node = graph.nodes[next_id]
            if next_node in seen:
                continue
            seen.add(next_node)
            queue.append(next_node)


def traverse(graph: ScopeGraph, scope: ScopeNode, stack: Path) -> ScopeNode:
    node_id = scope.node_id
    rules = scope.rules

    if scope.action:
        stack = scope.action(stack)

    queue: deque[tuple[ScopeNode, Path]] = deque()
    extend_queue(graph, node_id, stack, queue, rules)

    while queue:
        (node, stack) = queue.popleft()

        if node.action:
            stack = node.action(stack)

        if node.node_type is NodeType.DEFINITION and not stack:
            return node

        extend_queue(graph, node.node_id, stack, queue, rules)

    raise NotFoundError


def extend_queue(
    graph: ScopeGraph,
    node_id: int,
    stack: Path,
    queue: deque[tuple[ScopeNode, Path]],
    rules: Iterable[Rule],
) -> None:
    for edge, next_id in graph.edges[node_id]:
        if not all(allowed(edge) for allowed in rules):
            continue
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

    possible_occurrences = []
    for scope in scopes_for_position:
        if scope.name is not None:
            name = scope.name
            possible_occurrences.extend(graph.references[name])
            break
    else:
        raise NotFoundError

    definitions: dict[ScopeNode, set[ScopeNode]] = defaultdict(set)
    found_definition = None
    for occurrence in possible_occurrences:
        try:
            definition = traverse(graph, occurrence, stack=())
        except NotFoundError:
            continue

        definitions[definition].add(occurrence)
        if position in (definition.position, occurrence.position):
            found_definition = definition

    if not found_definition:
        raise NotFoundError

    assert found_definition.position
    return consolidate_definitions(definitions, found_definition)


def consolidate_definitions(
    definitions: dict[ScopeNode, set[ScopeNode]], found_definition: ScopeNode
) -> set[Position]:
    groups: list[set[Position]] = []
    found_group: set[Position] = set()
    for definition, occurrences in definitions.items():
        positions = {o.position for o in occurrences if o.position}
        if definition.position:
            positions.add(definition.position)
        for group in groups:
            if positions & group:
                group |= positions
                if found_definition.position in group:
                    found_group = group
                break
        else:
            groups.append(positions)
            if found_definition.position in positions:
                found_group = positions

    return found_group


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
    graph.link(graph.root, current, same_rank=True)
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
            same_rank=True,
        )
        return current

    current = graph.add_scope(
        link_to=current_scope,
        name=name,
        position=position,
        action=Push(name),
        # same_rank=True,
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
    grandparent = graph.get_parent(current_scope) or parent_scope

    for node_target in node.targets:
        assert isinstance(node_target, ast.Name)

        # if this is a redefinition the definition is itself also a reference.
        graph.add_scope(
            link_to=grandparent,
            name=node_target.id,
            position=node_position(node_target, source),
            action=Push(node_target.id),
            rules=(no_lookup_in_enclosing_scope,),
        )

        current_scope = visit(node_target, source, graph, current_scope)

    reference_scope = visit(node.value, source, graph, grandparent)
    if reference_scope.name or reference_scope.action or reference_scope.precondition:
        graph.link(current_scope, reference_scope, same_rank=True)
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

    current_scope = graph.add_scope(
        link_to=current_scope, action=Push("."), same_rank=True
    )

    position = source.find_after(node.attr, position)
    current_scope = graph.add_scope(
        link_to=current_scope,
        name=node.attr,
        position=position,
        action=Push(node.attr),
        same_rank=True,
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
    graph.link(current_scope, original_scope, same_rank=True)
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
        same_rank=True,
    )

    current_scope = graph.add_scope(
        link_to=graph.module_roots[source.module_name], to_enclosing_scope=True
    )
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
        same_rank=True,
    )

    current_class_scope = graph.add_top_scope()
    for statement in node.body:
        current_class_scope = graph.add_scope(link_to=current_class_scope)
        visit(statement, source, graph, current_class_scope)

    current_class_scope = graph.add_scope(
        link_to=current_class_scope, precondition=Top("."), action=Pop(), same_rank=True
    )
    current_scope = graph.add_scope(
        link_to=current_class_scope,
        precondition=Top("()"),
        action=Pop(),
        same_rank=True,
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
                parent = graph.add_scope(
                    link_from=parent, action=Push(part), same_rank=True
                )
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
                same_rank=True,
            )
            for part in (module_path + (name,))[::-1]:
                parent = graph.add_scope(
                    link_from=parent,
                    name=local_name,
                    position=position,
                    action=Push(part),
                    same_rank=True,
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
            same_rank=True,
        )
        parent = graph.add_scope(
            link_from=parent,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
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


def test_reassignment() -> None:
    source = make_source(
        """
    var = 12
    var = 13
    """
    )

    position = Position(source, 2, 0)
    assert all_occurrence_positions(position) == {
        Position(source, 1, 0),
        Position(source, 2, 0),
    }


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

    assert all_occurrence_positions(position) == {
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    }


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

    assert all_occurrence_positions(position) == {
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    }
