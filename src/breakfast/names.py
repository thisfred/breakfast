import ast
import logging

try:
    from ast import TypeVar
except ImportError:  # pragma: nocover
    TypeVar = None  # type: ignore[assignment,misc]
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import singledispatch

from breakfast.scope_graph import (
    Configuration,
    Fragment,
    Gadget,
    IncompleteFragment,
    NodeType,
    NotFoundError,
    Path,
    ScopeGraph,
    ScopeNode,
    State,
    no_lookup_in_enclosing_scope,
)
from breakfast.source import SubSource
from breakfast.types import Position, Source

logger = logging.getLogger(__name__)

CLASS_OFFSET = 6  # len('class ')
DEF_OFFSET = 4  # len('def ')
ASYNC_DEF_OFFSET = 10  # len('async def ')


def all_occurrence_positions(
    position: Position,
    *,
    sources: Iterable[Source] | None = None,
    in_reverse_order: bool = False,
    debug: bool = False,
    graph: ScopeGraph | None = None,
) -> list[Position]:
    graph = graph or build_graph(sources or [position.source])
    if debug:  # pragma: nocover
        from breakfast.scope_graph.visualization import view_graph

        view_graph(graph)

    found_definition, definitions = find_definitions(graph=graph, position=position)

    if not found_definition.position:
        raise AssertionError("Should have found at least the original position.")

    return sorted(
        consolidate_definitions(definitions, found_definition), reverse=in_reverse_order
    )


def find_definitions(
    graph: ScopeGraph, position: Position
) -> tuple[ScopeNode, dict[ScopeNode, set[ScopeNode]]]:
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
            definition = graph.find_definition(occurrence)
        except NotFoundError:
            continue

        definitions[definition].add(occurrence)
        if position in (definition.position, occurrence.position):
            found_definition = definition

    if not found_definition:
        raise NotFoundError

    return found_definition, definitions


def find_definition(graph: ScopeGraph, position: Position) -> ScopeNode | None:
    try:
        return find_definitions(graph, position)[0]
    except NotFoundError:
        return None


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


def generic_visit(
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    logger.debug(f"{node!r} not matched")
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            yield from visit_all(value, source, graph, state)

        elif isinstance(value, ast.AST):
            yield from visit(value, source, graph, state)


@singledispatch
def visit(
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from generic_visit(node, source, graph, state)


def visit_all(
    nodes: Iterable[ast.AST], source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    for node in nodes:
        if isinstance(node, ast.AST):
            yield from visit(node, source, graph, state)


@visit.register
def visit_module(
    node: ast.Module, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    module_root = graph.add_scope()
    graph.module_roots[source.module_name] = module_root

    # XXX: when we encounter a module name like a.b.c, we want to connect to existing
    # nodes for a and b if they exist already.
    current = graph.root
    packages = []
    for part in source.module_name.split("."):
        packages.append(part)
        found = None
        for _, other_id in graph.edges[current.node_id]:
            other_node = graph.nodes[other_id]
            if isinstance(other_node.action, Pop) and other_node.action.path == part:
                found = other_node
                break
        if found:
            current = found
            found = None
            for _, other_id in graph.edges[current.node_id]:
                dot_node = graph.nodes[other_id]
                if isinstance(dot_node.action, Pop) and dot_node.action.path == ".":
                    found = dot_node
                    break
            if found:
                current = found
            else:
                current = graph.add_scope(
                    link_from=current,
                    action=Pop("."),
                )

        else:
            current = graph.add_scope(
                link_from=current,
                action=Pop(part),
                is_definition=True,
            )
            current = graph.add_scope(
                link_from=current,
                action=Pop("."),
            )

    graph.add_edge(current, module_root, same_rank=True)
    with state.scope(module_root):
        with state.packages(packages):
            current = process_body(node.body, source, graph, state, current)

    graph.add_edge(module_root, current)

    yield Gadget(graph.root, graph.root)


@visit.register
def visit_name(
    node: ast.Name, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    name = node.id
    position = source.node_position(node)
    if isinstance(node.ctx, ast.Store):
        if state.configuration.follow_redefinitions:
            scopes = [
                graph.add_scope(
                    name=name,
                    position=position,
                    action=Push(name),
                    rules=(no_lookup_in_enclosing_scope,),
                    ast=node,
                ),
                graph.add_scope(
                    name=name,
                    position=position,
                    action=Pop(name),
                    is_definition=True,
                    ast=node,
                ),
            ]
        else:
            scopes = [
                graph.add_scope(
                    name=name,
                    position=position,
                    action=Pop(name),
                    is_definition=True,
                    ast=node,
                ),
            ]

    else:
        scopes = [
            graph.add_scope(name=name, position=position, action=Push(name), ast=node)
        ]

    for scope in scopes:
        yield IncompleteFragment(scope, scope)


def build_target_fragments(
    node: ast.Assign,
    source: Source,
    graph: ScopeGraph,
    state: State,
    current_scope: ScopeNode,
    current_parent: ScopeNode,
) -> list[Fragment]:
    target_fragments = []
    for node_target in node.targets:
        for fragment in visit(node_target, source, graph, state):
            if isinstance(fragment.entry.action, Pop):
                graph.add_edge(current_scope, fragment.entry, same_rank=True)
                target_fragments.append(fragment)
            elif isinstance(fragment.exit.action, Push):
                graph.add_edge(fragment.exit, current_parent, same_rank=True)

    return target_fragments


@visit.register
def visit_assign(
    node: ast.Assign, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    # XXX: Pretty hacky: haven't yet figured out an elegant/safe way to handle multiple
    # targets.
    exit_scope = graph.add_scope()
    current_parent = exit_scope
    current_scope = graph.add_scope(link_to=current_parent)
    target_fragments = build_target_fragments(
        node=node,
        source=source,
        graph=graph,
        state=state,
        current_scope=current_scope,
        current_parent=current_parent,
    )
    value_fragments = []
    for fragment in visit(node.value, source, graph, state):
        match fragment:
            case Gadget():
                yield fragment
            case _:
                value_fragments.append(fragment)
    if len(value_fragments) == len(target_fragments):
        for target_fragment, value_fragment in zip(
            target_fragments, value_fragments, strict=True
        ):
            graph.add_edge(
                target_fragment.exit,
                value_fragment.entry,
                same_rank=True,
            )
            graph.add_edge(value_fragment.exit, current_parent)
    else:
        if target_fragments:
            # XXX: this handles things like binary operator expressions on the rhs,
            # which I need to find a different solution for yet.
            for value_fragment in value_fragments:
                graph.add_edge(
                    target_fragments[0].exit,
                    value_fragment.entry,
                    same_rank=True,
                )
                graph.add_edge(value_fragment.exit, current_parent)

    yield Gadget(current_scope, exit_scope)


@visit.register
def visit_subscript(
    node: ast.Subscript, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    for slice_fragment in visit(node.slice, source, graph, state):
        graph.add_edge(slice_fragment.exit, current_scope)
        yield Gadget(current_scope, current_scope)

    yield from visit(node.value, source, graph, state)


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    expressions = []
    for fragment in visit(node.value, source, graph, state):
        match fragment:
            case IncompleteFragment():
                expressions.append(fragment)
            case _:
                yield fragment

    position = source.node_position(node)
    names = names_from(node.value)
    positions = []
    for name in (*names, node.attr):
        logger.debug(f"{name=}, {position=}")
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
    final_fragment = None
    for fragment in expressions:
        graph.add_edge(dot_scope, fragment.entry, same_rank=True)
        final_fragment = fragment

    yield IncompleteFragment(
        in_scope,
        final_fragment.exit if final_fragment else dot_scope,
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

    # XXX: hate this
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

    for node in graph.traverse(dot_scope):
        if isinstance(node.action, Pop) and node.action.path == attribute:
            break
    else:
        graph.add_scope(
            link_from=dot_scope,
            name=attribute,
            position=attribute_position,
            action=Pop(attribute),
            same_rank=True,
            is_definition=True,
            # XXX: make this a link that is only traversed after other possibilities are
            # exhausted, because if there is a class attribute, we want to reach that.
            priority=1,
        )


@visit.register
def visit_call(
    node: ast.Call, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    for fragment in visit_all(node.args, source, graph, state):
        scope = graph.add_scope()
        graph.connect(fragment, scope, same_rank=True)
        yield Gadget(scope, scope)

    yield from visit_all(node.args, source, graph, state)

    in_scope = graph.add_scope(action=Push("()"))

    expressions = []
    for fragment in visit(node.func, source, graph, state):
        match fragment:
            case IncompleteFragment():
                expressions.append(fragment)
            case _:
                yield fragment

    for fragment in expressions:
        graph.add_edge(in_scope, fragment.entry, same_rank=True)

        keyword_position = source.node_position(node)
        for keyword in node.keywords:
            if not keyword.arg:
                continue

            yield from visit(keyword.value, source, graph, state)
            keyword_position = source.node_position(keyword)
            graph.add_scope(
                link_to=in_scope,
                name=keyword.arg,
                position=keyword_position,
                action=Push(keyword.arg),
                same_rank=True,
            )

        yield IncompleteFragment(in_scope, fragment.exit)


@visit.register
def visit_for_loop(
    node: ast.For, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    exit_scope = graph.add_scope()
    current_parent = exit_scope
    current_scope = graph.add_scope(link_to=current_parent)

    for fragment in visit(node.target, source, graph, state):
        if isinstance(fragment.entry.action, Pop):
            graph.add_edge(current_scope, fragment.entry, same_rank=True)
        elif isinstance(fragment.exit.action, Push):
            graph.add_edge(fragment.exit, current_parent, same_rank=True)

    yield Gadget(current_scope, exit_scope)
    yield from visit(node.iter, source, graph, state)
    yield from visit_all(node.body, source, graph, state)
    yield from visit_all(node.orelse, source, graph, state)


def add_super_gadgets(
    state: State, current_scope: ScopeNode, graph: ScopeGraph
) -> ScopeNode:
    for base in state.inheritance_hierarchy:
        parent = current_scope
        current_scope = graph.add_scope(link_to=current_scope)
        previous = graph.add_scope(
            link_from=current_scope,
            name="super",
            action=Pop("super"),
            is_definition=True,
            same_rank=True,
        )
        for i, name in enumerate(base):
            previous = graph.add_scope(
                link_from=previous,
                name=name,
                action=Push(name),
                same_rank=True,
            )
            if i < len(base) - 1:
                previous = graph.add_scope(
                    link_from=previous,
                    name=".",
                    action=Push("."),
                    same_rank=True,
                )

        graph.add_edge(previous, parent)

    return current_scope


@visit.register
def visit_function_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: Source,
    graph: ScopeGraph,
    state: State,
) -> Iterator[Fragment]:
    yield from visit_all(node.decorator_list, source, graph, state)
    name = node.name
    offset = ASYNC_DEF_OFFSET if isinstance(node, ast.AsyncFunctionDef) else DEF_OFFSET
    position = source.node_position(node) + offset
    in_scope = out_scope = graph.add_scope()

    call_scope = graph.add_scope(
        link_from=in_scope,
        name=name,
        position=position,
        action=Pop(name),
        is_definition=True,
        same_rank=True,
        ast=node,
    )
    function_definition = graph.add_scope(
        link_from=call_scope,
        action=Pop("()"),
        same_rank=True,
    )

    current_scope = graph.add_scope(
        link_to=state.scope_hierarchy[-1] or graph.module_roots[source.module_name],
        to_enclosing_scope=True,
    )
    parent_scope = current_scope

    if type_params := getattr(node, "type_params", None):
        yield from visit_all(type_params, source, graph, state)
    yield from visit_all(node.args.defaults, source, graph, state)

    found = False
    for fragment in visit_type_annotation(node.returns, source, graph, state):
        if not found:
            found = True
            graph.add_edge(function_definition.exit, fragment.entry, same_rank=True)

        yield fragment

    is_method = (
        state.instance_scope
        and not is_static_method(node)
        and not is_class_method(node)
    )

    if is_method:
        current_scope = add_super_gadgets(
            state=state, current_scope=current_scope, graph=graph
        )

    self_name = None
    for i, arg in enumerate(node.args.args):
        current_scope = graph.add_scope(link_to=current_scope)
        arg_position = source.node_position(arg)

        arg_definition = graph.add_scope(
            link_from=current_scope,
            name=arg.arg,
            position=arg_position,
            action=Pop(arg.arg),
            is_definition=True,
            same_rank=True,
            ast=arg,
        )
        found = False
        for fragment in visit_type_annotation(arg.annotation, source, graph, state):
            if not found:
                found = True
                graph.add_edge(arg_definition.exit, fragment.entry, same_rank=True)
            yield fragment

        if i == 0 and is_method and state.class_name:
            self_name = arg.arg
            call = graph.add_scope(
                link_from=arg_definition, action=Push("()"), same_rank=True
            )
            class_name = state.class_name
            class_name_scope = graph.add_scope(
                link_from=call,
                name=class_name,
                action=Push(class_name),
                same_rank=True,
            )
            graph.add_edge(class_name_scope, parent_scope)

    graph.add_edge(function_definition, current_scope)

    function_bottom = graph.add_scope()

    with state.method(self_name=self_name):
        with state.scope(function_bottom):
            current_scope = process_body(node.body, source, graph, state, current_scope)
    graph.add_edge(function_bottom, current_scope)
    yield Gadget(in_scope, out_scope)


def visit_type_annotation(
    annotation: ast.AST | None, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    if not annotation:
        return

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        annotation_position = source.node_position(annotation)
        yield from visit_all(
            # XXX: parse always returns a module node, which we want to skip here,
            # because the string annotation is part of the current module's scope.
            ast.parse(annotation.value).body,
            SubSource(
                source=source,
                start_position=annotation_position,
                code=annotation.value,
            ),
            graph,
            state,
        )
    else:
        yield from visit(annotation, source, graph, state)


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
                case Gadget(entry_point, exit_point):
                    graph.connect(fragment, current_scope)
                    current_scope = (
                        entry_point
                        if isinstance(entry_point, ScopeNode)
                        else entry_point.entry
                    )
                case IncompleteFragment(
                    entry_point, exit_point
                ) if entry_point is exit_point and isinstance(
                    entry_point, ScopeNode
                ) and entry_point.node_type is NodeType.DEFINITION:
                    current_scope = graph.add_scope(link_to=current_scope)
                    graph.connect(current_scope, fragment)
                case IncompleteFragment(_, exit_point):
                    current_scope = graph.add_scope(link_to=current_scope)
                    graph.connect(exit_point, current_scope, same_rank=True)
                case _:
                    continue
    return current_scope


def is_static_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


def is_class_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        n.id == "classmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_class_definition(
    node: ast.ClassDef, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    yield from visit_all(node.decorator_list, source, graph, state)
    name = node.name

    current_scope = graph.add_scope()
    original_scope = current_scope

    position = source.node_position(node) + CLASS_OFFSET

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
        ast=node,
    )
    base_fragments = []
    base_names = []
    for base in node.bases:
        base_fragment = None
        for fragment in visit(base, source, graph, state):
            if base_fragment:
                base_fragment = graph.connect(fragment, base_fragment)
            else:
                base_fragment = fragment
        if base_fragment:
            graph.connect(parent, base_fragment)
            if state.scope_hierarchy:
                graph.add_edge(base_fragment.exit, state.scope_hierarchy[-1])
            base_fragments.append(base_fragment)
        base_names.append(names_from(base))

    if type_params := getattr(node, "type_params", None):
        yield from visit_all(type_params, source, graph, state)
    class_top_scope = graph.add_scope(link_to=original_scope)
    current_class_scope: ScopeNode = class_top_scope
    with state.instance(instance_scope=i_scope, class_name=name):
        with state.base_classes(base_names):
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

    yield Gadget(original_scope, original_scope)


def _get_relative_module_path(node: ast.ImportFrom, state: State) -> Iterable[str]:
    if node.level == 0:
        return

    module_path = tuple(state.package_path[: -node.level]) if state.package_path else ()

    for p in module_path:
        yield p
        yield "."


@visit.register
def visit_import_from(
    node: ast.ImportFrom, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    module_path = tuple(_get_relative_module_path(node, state))

    if node.module is None:
        module_path += (".",)
    else:
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
            position = source.node_position(alias)

            parent = graph.add_scope(
                link_from=current_scope,
                name=local_name,
                position=position,
                action=Pop(local_name),
                same_rank=True,
                ast=alias,
            )
            for part in (*module_path, name)[::-1]:
                parent = graph.add_scope(
                    link_from=parent,
                    name=part,
                    position=position,
                    action=Push(part),
                    same_rank=True,
                )

            graph.add_edge(parent, graph.root)
    yield Gadget(current_scope, current_scope)


@visit.register
def visit_import(
    node: ast.Import, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    for alias in node.names:
        name = alias.name
        local_name = alias.asname or name
        position = source.node_position(alias)

        local = graph.add_scope(
            link_from=current_scope,
            name=local_name,
            position=position,
            action=Pop(local_name),
            same_rank=True,
            ast=alias,
        )
        remote = graph.add_scope(
            link_from=local,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
            ast=alias,
        )
        graph.add_edge(remote, graph.root)
    yield Gadget(current_scope, current_scope)


@visit.register
def visit_global(
    node: ast.Global, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    start = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, start)

        parent = graph.add_scope(
            link_from=current_scope,
            name=name,
            position=position,
            action=Pop(name),
            same_rank=True,
            ast=node,
        )
        parent = graph.add_scope(
            link_from=parent,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
            ast=node,
        )
        graph.add_edge(parent, graph.module_roots[source.module_name])

    yield Gadget(current_scope, current_scope)


@visit.register
def visit_nonlocal(
    node: ast.Nonlocal, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    start = source.node_position(node)
    for name in node.names:
        position = source.find_after(name, start)

        parent = graph.add_scope(
            link_from=current_scope,
            name=name,
            position=position,
            action=Pop(name),
            same_rank=True,
            ast=node,
        )
        parent = graph.add_scope(
            link_from=parent,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
            ast=node,
        )
        graph.add_edge(
            parent, state.scope_hierarchy[-2] or graph.module_roots[source.module_name]
        )

    yield Gadget(current_scope, current_scope)


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

    yield IncompleteFragment(current_scope, top_scope)


@visit.register
def visit_match_as(
    node: ast.MatchAs, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    if node.name:
        start = source.node_position(node)
        position = source.find_after(node.name, start)
        graph.add_scope(
            link_from=current_scope,
            name=node.name,
            position=position,
            action=Pop(node.name),
            same_rank=True,
            is_definition=True,
            ast=node,
        )
    if node.pattern:
        yield from visit(node.pattern, source, graph, state)

    yield Gadget(current_scope, current_scope)


@visit.register
def visit_type_var(
    node: TypeVar, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    position = source.node_position(node)
    name = node.name
    scope = graph.add_scope(
        name=name, position=position, action=Pop(name), is_definition=True, ast=node
    )
    if node.bound:
        yield from visit(node.bound, source, graph, state)
    yield IncompleteFragment(scope, scope)


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
def names_from(node: ast.AST) -> Path:
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


def build_graph(
    sources: Iterable[Source], follow_redefinitions: bool = True
) -> ScopeGraph:
    graph = ScopeGraph()
    configuration = Configuration(follow_redefinitions=follow_redefinitions)
    state = State(
        scope_hierarchy=[], inheritance_hierarchy=[], configuration=configuration
    )

    for source in sources:
        for _ in visit(source.ast, source=source, graph=graph, state=state):
            pass

    return graph
