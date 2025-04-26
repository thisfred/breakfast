import logging
from ast import AST
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from breakfast.types import NodeType, NotFoundError, Position, Source

logger = logging.getLogger(__name__)


Path = tuple[str, ...]


class Action(Protocol):
    def __call__(self, stack: Path) -> Path: ...

    def precondition(self, stack: Path) -> bool: ...


class NodeCondition(Protocol):
    def __call__(self, node: "ScopeNode") -> bool: ...


class NotInScopeError(Exception):
    pass


@dataclass(frozen=True)
class Edge:
    same_rank: bool = False
    to_enclosing_scope: bool = False
    priority: int = 0


class Rule(Protocol):
    def __call__(self, edge: Edge) -> bool: ...


@dataclass(frozen=True)
class ScopeNode:
    node_id: int
    name: str | None = None
    position: Position | None = None
    action: Action | None = field(hash=False, default=None)
    node_type: NodeType = NodeType.SCOPE
    rules: tuple[Rule, ...] = ()
    ast: AST | None = None

    @property
    def source(self) -> Source | None:
        return self.position.source if self.position else None

    @property
    def entry(self) -> "ScopeNode":
        return self

    @property
    def exit(self) -> "ScopeNode":
        return self


class Fragment(Protocol):
    is_statement: bool = True

    @property
    def entry(self) -> ScopeNode: ...

    @property
    def exit(self) -> ScopeNode: ...


@dataclass
class IncompleteFragment:
    _entry: ScopeNode | Fragment
    _exit: ScopeNode | Fragment
    is_statement: bool = False

    @property
    def entry(self) -> ScopeNode:
        return self._entry.entry

    @property
    def exit(self) -> ScopeNode:
        return self._exit.exit


@dataclass
class Gadget:
    nodes: Sequence["ScopeNode"]
    is_statement: bool = True

    @property
    def entry(self) -> ScopeNode:
        return self.nodes[0]

    @property
    def exit(self) -> ScopeNode:
        return self.nodes[-1]


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

    def _add_scope(
        self,
        *,
        name: str | None = None,
        position: Position | None = None,
        action: Action | None = None,
        is_definition: bool = False,
        rules: tuple[Rule, ...] = (),
        ast: AST | None = None,
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
            ast=ast,
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
        priority: int = 0,
        ast: AST | None = None,
    ) -> ScopeNode:
        new_scope = self._add_scope(
            name=name,
            position=position,
            action=action,
            is_definition=is_definition,
            rules=rules,
            ast=ast,
        )

        if link_to:
            self.add_edge(
                new_scope,
                link_to,
                same_rank=same_rank,
                to_enclosing_scope=to_enclosing_scope,
                priority=priority,
            )

        if link_from:
            self.add_edge(
                link_from,
                new_scope,
                same_rank=same_rank,
                to_enclosing_scope=to_enclosing_scope,
                priority=priority,
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
        priority: int = 0,
    ) -> None:
        self.edges[scope_from.node_id].add(
            (
                Edge(
                    same_rank=same_rank,
                    to_enclosing_scope=to_enclosing_scope,
                    priority=priority,
                ),
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
        self.add_edge(
            fragment_or_scope_1.exit,
            fragment_or_scope_2.entry,
            same_rank=same_rank,
        )
        return IncompleteFragment(fragment_or_scope_1, fragment_or_scope_2)

    def group_by_rank(self) -> Iterable[set[int]]:  # pragma: nocover
        edges_to: dict[int, set[tuple[Edge, int]]] = defaultdict(set)
        for node_id, to_nodes in self.edges.items():
            for edge, other_id in to_nodes:
                edges_to[other_id].add((edge, node_id))

        seen_ids = set()
        groups = []
        for node_id in self.nodes:
            if node_id in seen_ids:
                continue

            seen_ids.add(node_id)

            group = {node_id}

            to_check = [node_id]

            while to_check:
                other_ids = self.get_same_rank_links(
                    to_check.pop(), edges_to, seen_ids
                )
                group |= other_ids
                seen_ids |= other_ids
                to_check.extend(list(other_ids))

            groups.append(group)

        return groups

    def get_same_rank_links(
        self,
        node_id: int,
        edges_to: dict[int, set[tuple[Edge, int]]],
        seen_ids: set[int],
    ) -> set[int]:
        return (
            {n for e, n in self.edges[node_id] if e.same_rank}
            | {n for e, n in edges_to[node_id] if e.same_rank}
        ) - seen_ids

    def traverse(
        self, start: ScopeNode, condition: NodeCondition | None = None
    ) -> Iterator[ScopeNode]:
        queue: deque[ScopeNode] = deque([start])
        seen: set[ScopeNode] = {start}
        while queue:
            node = queue.popleft()

            yield node

            for _, next_id in self.edges[node.node_id]:
                next_node = self.nodes[next_id]
                if next_node in seen:
                    continue
                seen.add(next_node)
                if condition and not condition(node):
                    continue
                queue.append(next_node)

    def find_definition(self, scope: ScopeNode) -> ScopeNode:
        return self.traverse_with_stack(scope, stack=())

    def traverse_with_stack(self, scope: ScopeNode, stack: Path) -> ScopeNode:
        node_id = scope.node_id
        rules = scope.rules

        if scope.action:
            stack = scope.action(stack)

        queues: dict[int, deque[tuple[ScopeNode, Path]]] = {
            0: deque(),
            1: deque(),
        }
        self.extend_queues(node_id, stack, queues, rules)

        seen = set()
        while any(q for q in queues.values()):
            for _, queue in sorted(queues.items()):
                if not queue:
                    continue
                (node, stack) = queue.popleft()
                break

            if (node, stack) in seen:
                continue
            seen.add((node, stack))

            if node.action:
                stack = node.action(stack)

            if node.node_type is NodeType.DEFINITION and not stack:
                return node

            self.extend_queues(node.node_id, stack, queues, rules)

        raise NotFoundError()

    def extend_queues(
        self,
        node_id: int,
        stack: Path,
        queues: dict[int, deque[tuple[ScopeNode, Path]]],
        rules: Iterable[Rule],
    ) -> None:
        for edge, next_id in self.edges[node_id]:
            if not all(allowed(edge) for allowed in rules):
                continue
            next_node = self.nodes[next_id]
            if next_node.action is None or next_node.action.precondition(stack):
                queues[edge.priority].append((next_node, stack))


@dataclass
class Configuration:
    follow_redefinitions: bool = True


@dataclass
class State:
    scope_hierarchy: list[ScopeNode]
    inheritance_hierarchy: list[tuple[str, ...]]
    configuration: Configuration
    class_name: str | None = None
    instance_scope: ScopeNode | None = None
    self: str | None = None
    package_path: list[str] | None = None

    @contextmanager
    def instance(
        self, *, instance_scope: ScopeNode, class_name: str
    ) -> Iterator["State"]:
        old_instance_scope = self.instance_scope
        self.instance_scope = instance_scope
        old_class_name = self.class_name
        self.class_name = class_name
        yield self
        self.instance_scope = old_instance_scope
        self.class_name = old_class_name

    @contextmanager
    def base_classes(self, names: list[tuple[str, ...]]) -> Iterator["State"]:
        old_hierarchy = self.inheritance_hierarchy
        self.inheritance_hierarchy = names
        yield self
        self.inheritance_hierarchy = old_hierarchy

    @contextmanager
    def method(self, *, self_name: str | None) -> Iterator["State"]:
        self.old_self = self.self
        self.self = self_name
        yield self
        self.self = self.old_self

    @contextmanager
    def scope(self, scope: ScopeNode) -> Iterator["State"]:
        self.scope_hierarchy.append(scope)
        yield self
        self.scope_hierarchy.pop()

    @contextmanager
    def packages(self, names: Iterable[str]) -> Iterator["State"]:
        if self.package_path is None:
            self.package_path = []
        for name in names:
            self.package_path.append(name)
        yield self
        for _ in names:
            self.package_path.pop()
