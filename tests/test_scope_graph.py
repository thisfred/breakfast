import ast
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import singledispatch
from typing import Literal, Protocol

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

    def precondition(self, stack: Path) -> bool:
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
    INSTANCE = auto()
    CLASS = auto()


@dataclass(frozen=True)
class ScopeNode:
    node_id: int
    name: str | None = None
    position: Position | None = None
    action: Action | None = field(hash=False, default=None)
    node_type: NodeType = NodeType.SCOPE
    rules: tuple[Rule, ...] = ()


@dataclass(frozen=True)
class Fragment:
    entry: ScopeNode
    exit: ScopeNode
    kind: Literal["expression"] | Literal["statement"] = "statement"


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
            if not (node.name or node.action):
                return node

        return None

    def _add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
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
            action=action,
            node_type=node_type,
            rules=rules,
        )
        self._add_node(new_scope)
        return new_scope

    def add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
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
            action=action,
            is_definition=is_definition,
            rules=rules,
        )

        if link_to:
            self.add_edge(
                new_scope,
                link_to,
                same_rank=same_rank,
                to_enclosing_scope=to_enclosing_scope,
            )

        if link_from:
            self.add_edge(
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

    def add_edge(
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

    def connect(
        self,
        fragment_or_scope_1: Fragment | ScopeNode,
        fragment_or_scope_2: Fragment | ScopeNode,
        *,
        same_rank: bool = False,
    ) -> Fragment:
        match fragment_or_scope_1:
            case Fragment(in_scope, out_scope):
                entry_scope = in_scope
                from_node = out_scope
            case scope_node if isinstance(scope_node, ScopeNode):
                entry_scope = scope_node
                from_node = scope_node

        match fragment_or_scope_2:
            case Fragment(in_scope, out_scope):
                to_node = in_scope
                exit_scope = out_scope
            case scope_node if isinstance(scope_node, ScopeNode):
                to_node = scope_node
                exit_scope = scope_node

        self.add_edge(from_node, to_node, same_rank=same_rank)
        return Fragment(entry_scope, exit_scope)


@dataclass
class State:
    enclosing_scope: ScopeNode | None = None
    instance_scope: ScopeNode | None = None
    class_name: str | None = None
    self: str | None = None

    @contextmanager
    def instance(
        self, *, instance_scope: ScopeNode, class_name: str
    ) -> Iterator["State"]:
        if instance_scope:
            old_instance_scope = self.instance_scope
            self.instance_scope = instance_scope
        if class_name:
            old_class_name = self.class_name
            self.class_name = class_name
        yield self
        if instance_scope:
            self.instance_scope = old_instance_scope
        if class_name:
            self.class_name = old_class_name

    @contextmanager
    def method(self, *, self_name: str | None) -> Iterator["State"]:
        self.old_self = self.self
        self.self = self_name
        yield self
        self.self = self.old_self

    @contextmanager
    def scope(self, scope: ScopeNode) -> Iterator["State"]:
        old_scope = self.enclosing_scope
        self.enclosing_scope = scope
        yield self
        self.enclosing_scope = old_scope


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
                    style="filled" if node is graph.root else "",
                    fillcolor="black" if node is graph.root else "",
                    fixedsize="true",
                    width="0.4" if node.name else "0.3",
                    height="0.4" if node.name else "0.3",
                    color="#B100B1" if node.name else "",
                    fontcolor="#B100B1" if node.name else "",
                )

        digraph.subgraph(subgraph)

    for from_id, to_nodes in graph.edges.items():
        for edge, to_node_id in to_nodes:
            digraph.edge(
                str(from_id),
                str(to_node_id),
                label="e" if edge.to_enclosing_scope else "",
            )

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
        if next_node.action is None or next_node.action.precondition(stack):
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
            possible_occurrences = graph.references[scope.name]
            break
    else:
        raise NotFoundError

    definitions: dict[ScopeNode, set[ScopeNode]] = defaultdict(set)
    found_definition = None
    for occurrence in possible_occurrences:
        if occurrence.node_type is NodeType.DEFINITION:
            definitions[occurrence].add(occurrence)
            if position == occurrence.position:
                found_definition = occurrence
            continue

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
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                yield from visit(item, source, graph, state)

        elif isinstance(value, ast.AST):
            yield from visit(value, source, graph, state)


@singledispatch
def visit(
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from generic_visit(node, source, graph, state)


@visit.register
def visit_module(
    node: ast.Module, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current: ScopeNode = graph.add_scope()

    module_root = graph.add_scope()
    graph.module_roots[source.module_name] = module_root

    with state.scope(module_root):
        current = process_body(node.body, source, graph, state, current)

    graph.add_edge(module_root, current)

    current = graph.add_scope(link_to=module_root, action=Pop("."))
    current = graph.add_scope(
        link_to=current,
        action=Pop(source.module_name),
        is_definition=True,
    )
    graph.add_edge(graph.root, current, same_rank=True)
    yield Fragment(entry=graph.root, exit=graph.root)


@visit.register
def visit_name(
    node: ast.Name, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    name = node.id
    position = node_position(node, source)

    if isinstance(node.ctx, ast.Store):
        scopes = [
            graph.add_scope(
                name=name,
                position=position,
                action=Push(name),
                rules=(no_lookup_in_enclosing_scope,),
            ),
            graph.add_scope(
                name=name,
                position=position,
                action=Pop(name),
                is_definition=True,
            ),
        ]
    else:
        scopes = [
            graph.add_scope(
                name=name,
                position=position,
                action=Push(name),
            )
        ]

    for scope in scopes:
        yield Fragment(entry=scope, exit=scope, kind="expression")


@visit.register
def visit_assign(
    node: ast.Assign, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    # XXX: Pretty hacky: haven't yet figured out an elegant/safe way to handle multiple
    # targets.
    exit_scope = graph.add_scope()
    current_parent = exit_scope
    current_scope = graph.add_scope(link_to=current_parent)
    target_fragments = []
    for node_target in node.targets:
        for fragment in visit(node_target, source, graph, state):
            if isinstance(fragment.entry.action, Pop):
                graph.add_edge(current_scope, fragment.entry, same_rank=True)
                target_fragments.append(fragment)
            elif isinstance(fragment.exit.action, Push):
                graph.add_edge(fragment.exit, current_parent, same_rank=True)
    value_fragments = list(visit(node.value, source, graph, state))
    if len(value_fragments) == len(target_fragments):
        for i, value_fragment in enumerate(value_fragments):
            graph.add_edge(
                target_fragments[i].exit,
                value_fragment.entry,
                same_rank=True,
            )
            graph.add_edge(value_fragment.exit, current_parent)
    else:
        if target_fragments:
            # XXX: this handles things like binary operator expressions on the rhs,
            # which we need to find a different solution for yet.
            for value_fragment in value_fragments:
                graph.add_edge(
                    target_fragments[0].exit,
                    value_fragment.entry,
                    same_rank=True,
                )
                graph.add_edge(value_fragment.exit, current_parent)

    yield Fragment(current_scope, exit_scope)


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    position = node_position(node, source)
    names = names_from(node.value)
    positions = []
    for name in (*names, node.attr):
        new_position = source.find_after(name, position)
        positions.append(new_position)
        position = new_position

    in_scope = graph.add_scope(
        name=node.attr,
        position=position,
        action=Push(node.attr),
    )
    dot_scope = graph.add_scope(
        link_from=in_scope,
        action=Push("."),
        same_rank=True,
    )

    for fragment in visit(node.value, source, graph, state):
        graph.add_edge(dot_scope, fragment.entry, same_rank=True)

    yield Fragment(
        in_scope,
        fragment.exit if fragment else dot_scope,
        kind="expression",
    )
    if not isinstance(node.ctx, ast.Store):
        return

    previous_fragment = None
    for name, name_position in zip(names, positions[:-1], strict=True):
        fragment = graph.connect(
            graph.add_scope(name=name, position=name_position, action=Pop(name)),
            graph.add_scope(action=Pop(".")),
            same_rank=True,
        )
        if previous_fragment:
            fragment = graph.connect(previous_fragment, fragment, same_rank=True)
        previous_fragment = fragment

    fragment = graph.connect(
        fragment,
        graph.add_scope(action=Pop(node.attr), position=position, same_rank=True),
    )
    yield fragment

    if len(names) == 1 and state.instance_scope and names[0] == state.self:
        add_instance_property(graph, state, node.attr, position, state.instance_scope)


def add_instance_property(
    graph: ScopeGraph,
    state: State,
    attribute: str,
    attribute_position: Position,
    instance_scope: ScopeNode,
) -> None:
    # XXX: this is a hack to set the property on the instance node, so it can be found
    # by other methods referencing it, as well as code accessing it directly on an
    # instance.
    for _, other_id in graph.edges[instance_scope.node_id]:
        dot_scope = graph.nodes[other_id]
        if isinstance(dot_scope.action, Pop) and dot_scope.action.path == ".":
            break
    else:
        dot_scope = graph.add_scope(
            link_from=instance_scope,
            action=Pop("."),
            same_rank=True,
        )

    for _, other_id in graph.edges[dot_scope.node_id]:
        property_scope = graph.nodes[other_id]
        if (
            isinstance(property_scope.action, Pop)
            and property_scope.action.path == attribute
        ):
            break
    else:
        property_scope = graph.add_scope(
            link_from=dot_scope,
            name=attribute,
            position=attribute_position,
            action=Pop(attribute),
            same_rank=True,
            is_definition=True,
        )


@visit.register
def visit_call(
    node: ast.Call, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    for arg in node.args:
        yield from visit(arg, source, graph, state)

    in_scope = graph.add_scope(action=Push("()"))

    for fragment in visit(node.func, source, graph, state):
        graph.add_edge(in_scope, fragment.entry, same_rank=True)

        keyword_position = node_position(node, source)
        for keyword in node.keywords:
            if not keyword.arg:
                continue

            keyword_position = source.find_after(keyword.arg, keyword_position)
            graph.add_scope(
                link_to=in_scope,
                name=keyword.arg,
                position=keyword_position,
                action=Push(keyword.arg),
                same_rank=True,
            )

        yield Fragment(in_scope, fragment.exit, kind="expression")


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    name = node.name
    # Offset by len("def ")
    position = node_position(node, source, column_offset=4)
    in_scope = out_scope = graph.add_scope()

    call_scope = graph.add_scope(
        link_from=in_scope,
        name=name,
        position=position,
        action=Pop(name),
        is_definition=True,
        same_rank=True,
    )
    function_definition = graph.add_scope(
        link_from=call_scope,
        action=Pop("()"),
        same_rank=True,
    )

    current_scope = graph.add_scope(
        link_to=graph.module_roots[source.module_name], to_enclosing_scope=True
    )
    parent_scope = current_scope

    is_method = (
        state.instance_scope
        and not is_static_method(node)
        and not is_class_method(node)
    )
    self_name = None
    for i, arg in enumerate(node.args.args):
        current_scope = graph.add_scope(link_to=current_scope)
        arg_position = node_position(arg, source)
        arg_definition = graph.add_scope(
            link_from=current_scope,
            name=arg.arg,
            position=arg_position,
            action=Pop(arg.arg),
            is_definition=True,
            same_rank=True,
        )

        if i == 0 and is_method and state.class_name:
            self_name = arg.arg
            call = graph.add_scope(
                link_from=arg_definition, action=Push("()"), same_rank=True
            )
            class_name = graph.add_scope(
                link_from=call,
                name=state.class_name,
                action=Push(state.class_name),
                same_rank=True,
            )
            graph.add_edge(class_name, parent_scope)

    graph.add_edge(function_definition, current_scope)

    with state.method(self_name=self_name):
        current_scope = process_body(node.body, source, graph, state, current_scope)
    yield Fragment(in_scope, out_scope)


def process_body(
    body: Iterable[ast.AST],
    source: Source,
    graph: ScopeGraph,
    state: State,
    current_scope: ScopeNode,
) -> ScopeNode:
    for statement in body:
        for fragment in visit(statement, source, graph, state):
            match fragment:
                case Fragment(entry_point, exit_point, kind="statement"):
                    graph.add_edge(exit_point, current_scope)
                    current_scope = entry_point
                case Fragment(_, exit_point, kind="expression"):
                    current_scope = graph.add_scope(link_to=current_scope)
                    graph.add_edge(exit_point, current_scope, same_rank=True)
                case _:
                    continue
    return current_scope


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


def is_class_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "classmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_class_definition(
    node: ast.ClassDef, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    name = node.name

    current_scope = graph.add_scope()
    original_scope = current_scope

    # Offset by len("class ")
    position = node_position(node, source, column_offset=6)

    instance_scope = graph.add_scope(action=Pop("."), same_rank=True)
    i_scope = graph.add_scope(
        link_to=instance_scope,
        name="I",
        same_rank=True,
    )

    parent = graph.add_scope(
        link_from=current_scope,
        name=name,
        position=position,
        action=Pop(name),
        is_definition=True,
        same_rank=True,
    )
    for base in node.bases:
        link_to = None
        for fragment in visit(base, source, graph, state):
            if link_to:
                graph.add_edge(fragment.exit, link_to)
            link_to = fragment.entry
        if link_to:
            graph.add_edge(parent, link_to)
        if state.enclosing_scope:
            graph.add_edge(fragment.exit, state.enclosing_scope)

    class_top_scope = graph.add_scope()
    current_class_scope: ScopeNode = class_top_scope
    with state.instance(instance_scope=i_scope, class_name=name):
        current_class_scope = process_body(
            node.body, source, graph, state, current_class_scope
        )

    graph.add_edge(instance_scope, current_class_scope)

    graph.add_scope(
        link_from=parent,
        link_to=instance_scope,
        name="C",
        same_rank=True,
    )

    current_scope = graph.add_scope(action=Pop("()"), same_rank=True)
    graph.add_edge(current_scope, i_scope)
    graph.add_edge(parent, current_scope)

    yield Fragment(original_scope, original_scope)


@visit.register
def visit_import_from(
    node: ast.ImportFrom, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()
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
            graph.add_edge(parent, graph.root)
        else:
            local_name = alias.asname or name
            position = source.find_after(name, start)

            parent = graph.add_scope(
                link_from=current_scope,
                name=local_name,
                position=position,
                action=Pop(local_name),
                same_rank=True,
            )
            for part in (*module_path, name)[::-1]:
                parent = graph.add_scope(
                    link_from=parent,
                    name=local_name,
                    position=position,
                    action=Push(part),
                    same_rank=True,
                )

            graph.add_edge(parent, graph.root)
    yield Fragment(current_scope, current_scope)


@visit.register
def visit_global(
    node: ast.Global, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    start = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, start)

        parent = graph.add_scope(
            link_from=current_scope,
            name=name,
            position=position,
            action=Pop(name),
            same_rank=True,
        )
        parent = graph.add_scope(
            link_from=parent,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
        )
        graph.add_edge(parent, graph.module_roots[source.module_name])

    yield Fragment(current_scope, current_scope)


@visit.register
def visit_list_comprehension(
    node: ast.ListComp, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from visit_comprehension(node, source, graph, state, node.elt)


@visit.register
def visit_dictionary_comprehension(
    node: ast.DictComp, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from visit_comprehension(node, source, graph, state, node.key, node.value)


@visit.register
def visit_set_comprehension(
    node: ast.SetComp,
    source: Source,
    graph: ScopeGraph,
    state: State,
) -> Iterator[Fragment]:
    yield from visit_comprehension(node, source, graph, state, node.elt)


@visit.register
def visit_generator_expression(
    node: ast.GeneratorExp, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from visit_comprehension(node, source, graph, state, node.elt)


def visit_comprehension(
    node: ast.DictComp | ast.ListComp | ast.SetComp | ast.GeneratorExp,
    source: Source,
    graph: ScopeGraph,
    state: State,
    *sub_nodes: ast.AST,
) -> Iterator[Fragment]:
    top_scope = current_scope = graph.add_scope()
    for generator in node.generators:
        current_scope = graph.add_scope(link_to=current_scope)
        for fragment in visit(generator.target, source, graph, state):
            graph.add_edge(current_scope, fragment.entry)

        current_scope = graph.add_scope(link_to=current_scope)
        for fragment in visit(generator.iter, source, graph, state):
            graph.add_edge(fragment.exit, current_scope)

        current_scope = graph.add_scope(link_to=current_scope)
        for if_node in generator.ifs:
            for fragment in visit(if_node, source, graph, state):
                graph.add_edge(fragment.exit, current_scope)

    for sub_node in sub_nodes:
        current_scope = graph.add_scope(link_to=current_scope)
        for fragment in visit(sub_node, source, graph, state):
            graph.add_edge(fragment.exit, current_scope)

    yield Fragment(current_scope, top_scope)


@dataclass(frozen=True)
class Pop:
    path: str

    def __call__(self, stack: Path) -> Path:
        return stack[1:]

    def precondition(self, stack: Path) -> bool:
        return bool(stack) and stack[0] == self.path


@dataclass(frozen=True)
class Push:
    path: str

    def __call__(self, stack: Path) -> Path:
        return (self.path, *stack)

    def precondition(self, stack: Path) -> bool:
        return True


@singledispatch
def names_from(node: ast.AST) -> Path:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> Path:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> Path:
    return (*names_from(node.value), ".", node.attr)


@names_from.register
def call_names(node: ast.Call) -> Path:
    names = (*names_from(node.func), "()")
    return names


def build_graph(sources: Iterable[Source]) -> ScopeGraph:
    graph = ScopeGraph()
    state = State()

    for source in sources:
        for _ in visit(source.get_ast(), source=source, graph=graph, state=state):
            pass

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

    assert all_occurrence_positions(position) == {
        Position(source, 2, 13),
        Position(source, 5, 0),
    }


def test_does_not_rename_random_attributes() -> None:
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = source.position(row=3, column=0)

    assert all_occurrence_positions(position) == {source.position(row=3, column=0)}


def test_finds_parameter() -> None:
    source = make_source(
        """
        def fun(arg=1):
            print(arg)

        arg = 8
        fun(arg=arg)
        """
    )

    assert all_occurrence_positions(source.position(1, 8)) == {
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    }


def test_finds_function() -> None:
    source = make_source(
        """
        def fun():
            return 'result'
        result = fun()
        """
    )

    assert {source.position(1, 4), source.position(3, 9)} == all_occurrence_positions(
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

    assert {source.position(1, 6), source.position(4, 11)} == all_occurrence_positions(
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

    assert all_occurrence_positions(position) == {
        source.position(row=3, column=8),
        source.position(row=6, column=12),
    }


def test_finds_passed_argument() -> None:
    source = make_source(
        """
        var = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, var)
        """
    )

    assert {source.position(1, 0), source.position(4, 7)} == all_occurrence_positions(
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

    assert {
        source.position(3, 8),
        source.position(7, 13),
        source.position(20, 2),
    } == occurrences


def test_finds_definition_from_call() -> None:
    source = make_source(
        """
        def fun():
            pass

        def bar():
            fun()
        """
    )

    assert {source.position(1, 4), source.position(5, 4)} == all_occurrence_positions(
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

    assert {source.position(4, 13), source.position(7, 20)} == occurrences


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

    assert {source.position(3, 23), source.position(4, 24)} == occurrences


def test_finds_dict_comprehension_variables() -> None:
    source = make_source(
        """
        var = 1
        foo = {var: None for var in range(100) if var % 3}
        var = 2
        """
    )

    position = source.position(row=2, column=21)

    assert all_occurrence_positions(position) == {
        source.position(row=2, column=7),
        source.position(row=2, column=21),
        source.position(row=2, column=42),
    }


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

    assert all_occurrence_positions(position) == {
        source.position(row=3, column=4),
        source.position(row=3, column=12),
        source.position(row=3, column=33),
    }


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = {var for var in range(100) if var % 3}
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == {
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    }


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = (var for var in range(100) if var % 3)
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == {
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    }


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

    assert all_occurrence_positions(position) == {
        source.position(row=1, column=0),
        source.position(row=2, column=4),
        source.position(row=3, column=10),
        source.position(row=4, column=6),
    }


def test_finds_tuple_unpack() -> None:
    source = make_source(
        """
    foo, var = 1, 2
    print(var)
    """
    )

    position = source.position(row=1, column=5)

    assert all_occurrence_positions(position) == {
        source.position(1, 5),
        source.position(2, 6),
    }


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

    assert all_occurrence_positions(position) == {
        source.position(row=3, column=8),
        source.position(row=11, column=2),
    }


def test_recognizes_multiple_assignment_1() -> None:
    source = make_source(
        """
    a = 1
    foo, bar = a, a
    """
    )

    position = source.position(row=1, column=0)
    assert all_occurrence_positions(position) == {
        source.position(row=1, column=0),
        source.position(row=2, column=11),
        source.position(row=2, column=14),
    }
