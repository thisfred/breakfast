import ast
import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import singledispatch

from breakfast.position import Position
from breakfast.scope_graph import (
    Fragment,
    NodeType,
    NotFoundError,
    Path,
    ScopeGraph,
    ScopeNode,
    State,
    no_lookup_in_enclosing_scope,
)
from breakfast.source import Source

logger = logging.getLogger(__name__)


def all_occurrence_positions(
    position: Position,
    *,
    sources: Iterable[Source] | None = None,
    in_reverse_order: bool = False,
    debug: bool = False,
) -> list[Position]:
    graph = build_graph(sources or [position.source])
    if debug:
        from breakfast.scope_graph.visualization import view_graph

        view_graph(graph)

    scopes_for_position = graph.positions.get(position)
    if not scopes_for_position:
        raise NotFoundError

    for scope in scopes_for_position:
        if scope.name is not None:
            possible_occurrences = graph.references[scope.name]
            break
    else:
        raise NotFoundError

    found_definition, definitions = find_definition(
        graph, position, possible_occurrences
    )

    if not found_definition.position:
        raise AssertionError("Should have found at least the original position.")

    return sorted(
        consolidate_definitions(definitions, found_definition), reverse=in_reverse_order
    )


def find_definition(
    graph: ScopeGraph, position: Position, possible_occurrences: Iterable[ScopeNode]
) -> tuple[ScopeNode, dict[ScopeNode, set[ScopeNode]]]:
    definitions: dict[ScopeNode, set[ScopeNode]] = defaultdict(set)
    found_definition = None
    for occurrence in possible_occurrences:
        if occurrence.node_type is NodeType.DEFINITION:
            definitions[occurrence].add(occurrence)
            if position == occurrence.position:
                found_definition = occurrence
            continue

        try:
            definition = graph.traverse(occurrence, stack=())
        except NotFoundError:
            continue

        definitions[definition].add(occurrence)
        if position in (definition.position, occurrence.position):
            found_definition = definition

    if not found_definition:
        raise NotFoundError

    return found_definition, definitions


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


def node_position(node: ast.AST, source: Source) -> Position:
    """
    Return the start position of the node in unicode characters.

    (Note that ast.AST's col_offset is in *bytes*)
    """
    logger.debug(f"{node=}, {node.lineno=},  {node.col_offset=}")
    line = source.guaranteed_lines[node.lineno - 1]
    if line.isascii():
        column_offset = node.col_offset
    else:
        byte_prefix = line.encode("utf-8")[: node.col_offset]
        column_offset = len(byte_prefix.decode("utf-8"))

    return source.position(row=(node.lineno - 1), column=column_offset)


def generic_visit(
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            yield from visit_all(value, source, graph, state)

        elif isinstance(value, ast.AST):
            yield from visit(value, source, graph, state)


@singledispatch
def visit(
    node: ast.AST, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    logger.debug(
        "visiting %s, %s:%s:%s",
        repr(node),
        source.path,
        getattr(node, "lineno", "??"),
        getattr(node, "col_offset", "??"),
    )
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
    yield Fragment(graph.root, graph.root)


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
        yield Fragment(scope, scope, is_statement=False)


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
    expressions = []
    for fragment in visit(node.value, source, graph, state):
        if not fragment.is_statement:
            expressions.append(fragment)
        else:
            yield fragment

    position = node_position(node, source)
    logger.debug(f"{position=}")
    names = names_from(node.value)
    logger.debug(f"{names=}")
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

    yield Fragment(
        in_scope,
        final_fragment.exit if final_fragment else dot_scope,
        is_statement=False,
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
    # XXX: a hack to handle super().something. this will break when `super` is
    # redefined.
    names = names_from(node.func)
    if names == ("super",) and state.class_name:
        top = graph.add_scope()
        bottom = graph.add_scope()
        pop = graph.add_scope(link_from=bottom, action=Pop("super"), same_rank=True)
        for super_class_fragment in state.inheritance_hierarchy:
            copied = graph.copy(super_class_fragment)
            graph.connect(pop, copied, same_rank=True)
            graph.connect(copied, top)
        graph.add_edge(bottom, top)
        yield Fragment(bottom, top)

    yield from visit_all(node.args, source, graph, state)

    in_scope = graph.add_scope(action=Push("()"))

    expressions = []
    for fragment in visit(node.func, source, graph, state):
        if not fragment.is_statement:
            expressions.append(fragment)
        else:
            yield fragment

    for fragment in expressions:
        graph.add_edge(in_scope, fragment.entry, same_rank=True)

        keyword_position = node_position(node, source)
        for keyword in node.keywords:
            if not keyword.arg:
                continue

            yield from visit(keyword.value, source, graph, state)
            keyword_position = node_position(keyword, source)
            graph.add_scope(
                link_to=in_scope,
                name=keyword.arg,
                position=keyword_position,
                action=Push(keyword.arg),
                same_rank=True,
            )

        yield Fragment(in_scope, fragment.exit, is_statement=False)


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

    yield Fragment(current_scope, exit_scope)
    yield from visit(node.iter, source, graph, state)
    yield from visit_all(node.body, source, graph, state)
    yield from visit_all(node.orelse, source, graph, state)


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    name = node.name
    # Offset by len("def ")
    position = node_position(node, source) + 4
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
        link_to=state.scope_hierarchy[-1] or graph.module_roots[source.module_name],
        to_enclosing_scope=True,
    )
    parent_scope = current_scope

    is_method = (
        state.instance_scope
        and not is_static_method(node)
        and not is_class_method(node)
    )
    yield from visit_all(node.args.defaults, source, graph, state)

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
                case Fragment(entry_point, exit_point, is_statement=True):
                    graph.connect(fragment, current_scope)
                    current_scope = (
                        entry_point
                        if isinstance(entry_point, ScopeNode)
                        else entry_point.entry
                    )
                case Fragment(_, exit_point, is_statement=False):
                    current_scope = graph.add_scope(link_to=current_scope)
                    graph.connect(exit_point, current_scope, same_rank=True)
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
    position = node_position(node, source) + 6

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
    base_fragments = []
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

    class_top_scope = graph.add_scope()
    current_class_scope: ScopeNode = class_top_scope
    with state.instance(instance_scope=i_scope, class_name=name):
        with state.base_classes(base_fragments):
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
            position = node_position(alias, source)

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
                    name=part,
                    position=position,
                    action=Push(part),
                    same_rank=True,
                )

            graph.add_edge(parent, graph.root)
    yield Fragment(current_scope, current_scope)


@visit.register
def visit_import(
    node: ast.Import, source: Source, graph: ScopeGraph, state: State
) -> Iterator[Fragment]:
    current_scope = graph.add_scope()

    for alias in node.names:
        name = alias.name
        local_name = alias.asname or name
        position = node_position(alias, source)

        local = graph.add_scope(
            link_from=current_scope,
            name=local_name,
            position=position,
            action=Pop(local_name),
            same_rank=True,
        )
        remote = graph.add_scope(
            link_from=local,
            name=name,
            position=position,
            action=Push(name),
            same_rank=True,
        )
        graph.add_edge(remote, graph.root)
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
def visit_nonlocal(
    node: ast.Nonlocal, source: Source, graph: ScopeGraph, state: State
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
        graph.add_edge(
            parent, state.scope_hierarchy[-2] or graph.module_roots[source.module_name]
        )

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


def build_graph(sources: Iterable[Source]) -> ScopeGraph:
    graph = ScopeGraph()
    state = State(scope_hierarchy=[], inheritance_hierarchy=[])

    for source in sources:
        for _ in visit(source.get_ast(), source=source, graph=graph, state=state):
            pass

    return graph
