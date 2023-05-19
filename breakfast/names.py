import ast
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import singledispatch

from breakfast.position import Position
from breakfast.source import Source

QualifiedName = tuple[str, ...]


class TreeTraversalError(RuntimeError):
    pass


class AliasError(RuntimeError):
    pass


class Node:
    def __init__(self, parent: "Node | None", path: QualifiedName = ()):
        self.parent = parent
        self.children: dict[str, "Node"] = defaultdict(lambda: Node(parent=self))
        self.occurrences: set[Position] = set()
        self.is_class = False
        self.path = path

    def add_occurrence(self, occurrence: Position) -> None:
        self.occurrences.add(occurrence)

    def __getitem__(self, name: str) -> "Node":
        node = self.children[name]

        if not node.path:
            node.path = (*self.path, name)

        return node

    def __contains__(self, name: str) -> bool:
        return name in self.children

    def alias_namespace(self, other: "Node") -> None:
        if "." in other and "." not in self:
            self.children["."] = other["."]
        elif "." in self and "." not in other:
            other.children["."] = self.children["."]
        else:
            self.children["."] = other["."]

    def alias(self, other: "Node") -> None:
        for name, value in other.children.items():
            if name not in self.children:
                self.children[name] = value

        for name, value in self.children.items():
            if name not in other.children:
                other.children[name] = value

        other.children = self.children
        self.occurrences |= other.occurrences
        other.occurrences = self.occurrences

    def flatten(
        self,
        prefix: QualifiedName = (),
        seen: set[Position] | None = None,
    ) -> dict[QualifiedName, list[tuple[int, int]]]:
        if not seen:
            seen = set()

        result = {}
        next_values = []
        for key, value in self.children.items():
            new_prefix = (*prefix, key)
            if value.occurrences:
                occurrence = next(iter(value.occurrences))
                if occurrence in seen:
                    continue

                positions = [(o.row, o.column) for o in value.occurrences]
                result[new_prefix] = positions
                seen |= value.occurrences
            next_values.append((new_prefix, value))

        for new_prefix, value in next_values:
            result.update(value.flatten(prefix=new_prefix, seen=seen))

        return result


class State:
    def __init__(self, position: Position):
        self.position = position
        self.root = Node(parent=None)
        self.current_node = self.root
        self.current_path: QualifiedName = ()
        self.lookup_scopes = [self.root]
        self.found: Node | None = None

    @contextmanager
    def scope(
        self, name: str, lookup_scope: bool = False, is_class: bool = False
    ) -> Iterator[None]:
        previous_node = self.current_node
        self.current_node = self.current_node[name]
        self.current_node.is_class = is_class
        if lookup_scope:
            self.lookup_scopes.append(self.current_node)
        self.current_path += (name,)
        yield
        self.current_node = previous_node
        self.current_path = self.current_path[:-1]
        if lookup_scope:
            self.lookup_scopes.pop()

    @contextmanager
    def jump_to_scope(self, path: QualifiedName) -> Iterator[None]:
        previous_node = self.current_node
        previous_path = self.current_path
        self.current_node = self.root
        self.current_path = ()
        for name in path:
            self.current_node = self.current_node[name]
            self.current_path += (name,)
        yield
        self.current_node = previous_node
        self.current_path = previous_path

    def check_found(self, position: Position) -> None:
        if position == self.position:
            self.found = self.current_node

    def add_occurrence(self, position: Position) -> None:
        self.current_node.add_occurrence(position)
        self.check_found(position)

    def follow_path(self, path: QualifiedName) -> Node:
        other_node = self.current_node
        other_path = self.current_path

        for name in path:
            if name == "/":
                other_path = ()
                other_node = self.root
            elif name == "~":
                other_path = self.current_path[:2]
                for _ in range(len(self.current_path) - 2):
                    if other_node.parent:
                        other_node = other_node.parent
                    else:
                        raise TreeTraversalError()

            elif name == "..":
                other_path = other_path[:-1]
                if other_node.parent:
                    other_node = other_node.parent
                else:
                    raise TreeTraversalError()

            else:
                other_path += (name,)
                other_node = other_node[name]
        return other_node

    def alias(self, path: QualifiedName) -> None:
        other_node = self.follow_path(path)
        self.current_node.alias(other_node)


def node_position(
    node: ast.AST, source: Source, row_offset: int = 0, column_offset: int = 0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


def generic_visit(node: ast.AST, source: Source, state: State) -> None:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    visit(item, source, state)
        elif isinstance(value, ast.AST):
            visit(value, source, state)


@singledispatch
def visit(node: ast.AST, source: Source, state: State) -> None:
    generic_visit(node, source, state)


@visit.register
def visit_module(node: ast.Module, source: Source, state: State) -> None:
    with ExitStack() as stack:
        for name in source.module_name.split("."):
            stack.enter_context(state.scope(name))
            stack.enter_context(state.scope(".", lookup_scope=True))
        generic_visit(node, source, state)


@visit.register
def visit_name(node: ast.Name, source: Source, state: State) -> None:
    position = node_position(node, source)
    if isinstance(node.ctx, ast.Store):
        with state.scope(node.id):
            state.add_occurrence(position)
    else:
        if node.id not in state.current_node:
            for scope in state.lookup_scopes[::-1]:
                if node.id in scope or scope is state.root:
                    position = node_position(node, source)
                    with state.scope(node.id):
                        scope[node.id].add_occurrence(position)
                    state.check_found(position)
                    break
        else:
            with state.scope(node.id):
                state.add_occurrence(node_position(node, source))


def get_names(value: ast.AST) -> list[QualifiedName]:
    match value:
        case ast.Tuple(elts=elements):
            return [
                i
                for e in elements
                for i in get_names(e)  # pylint: disable=not-an-iterable
            ]
        case other:
            return [qualified_name_for(other)]


def qualified_name_for(node: ast.AST) -> QualifiedName:
    match node:
        case ast.Name(id=name):
            return (name,)
        case ast.Attribute(value=value, attr=attr):
            return (*qualified_name_for(value), attr)
        case ast.Call(func=function):
            return (*qualified_name_for(function), "()")
        case _:
            return ()


@visit.register
def visit_import(node: ast.Import, source: Source, state: State) -> None:
    start = node_position(node, source)

    current_path = ("/", *state.current_path)
    with state.jump_to_scope(()):
        _handle_imports(node, source, state, start, current_path)


@visit.register
def visit_import_from(node: ast.ImportFrom, source: Source, state: State) -> None:
    start = node_position(node, source, column_offset=len("from "))
    if not isinstance(node.module, str):
        raise AssertionError(f"{node.module=} should have been a string")

    current_path = ("/", *state.current_path)
    node_module_path: QualifiedName = ()
    for name in node.module.split("."):
        node_module_path += (name, ".")
    node_module_path = node_module_path[:-1]

    with state.jump_to_scope(node_module_path):
        state.add_occurrence(start)
        with state.scope("."):
            _handle_imports(node, source, state, start, current_path)


def _handle_imports(
    node: ast.Import | ast.ImportFrom,
    source: Source,
    state: State,
    start: Position,
    current_path: QualifiedName,
) -> None:
    for alias in node.names:
        name = alias.name
        position = source.find_after(name, start)
        with state.scope(name):
            state.add_occurrence(position)
            path = (*current_path, name)
            state.alias(path)


@visit.register
def visit_assign(node: ast.Assign, source: Source, state: State) -> None:
    for node_target in node.targets:
        visit(node_target, source, state)
    visit(node.value, source, state)

    target_names = get_names(node.targets[0])
    value_names = get_names(node.value)
    for target, value in zip(target_names, value_names, strict=True):
        if target and value:
            path: QualifiedName = ()
            with ExitStack() as stack:
                for name in target[:-1]:
                    stack.enter_context(state.scope(name))
                    stack.enter_context(state.scope("."))
                    path += ("..",)
                stack.enter_context(state.scope(target[-1]))
                path += ("..",)

                other_node = state.follow_path(path + value)
                state.current_node.alias_namespace(other_node)


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, state: State
) -> None:
    is_method = state.lookup_scopes[-1] and state.lookup_scopes[-1].is_class
    position = node_position(node, source, column_offset=len("def "))

    with state.scope(node.name):
        state.add_occurrence(position)

        with state.scope("()"):
            for i, arg in enumerate(node.args.args):
                position = node_position(arg, source)

                with state.scope(arg.arg):
                    state.add_occurrence(position)
                    if i == 0 and is_method and not is_static_method(node):
                        other_node = state.follow_path(("..", "..", "..", ".."))
                        state.current_node.alias_namespace(other_node)

            generic_visit(node, source, state)


@visit.register
def visit_class(node: ast.ClassDef, source: Source, state: State) -> None:
    position = node_position(node, source, column_offset=len("class "))

    for base in node.bases:
        visit(base, source, state)
        if isinstance(base, ast.Name):
            with state.scope(base.id):
                with state.scope("()"):
                    other_node = state.follow_path(("..", "..", node.name, "()"))
                    state.current_node.alias_namespace(other_node)

    with state.scope(node.name, lookup_scope=True, is_class=True):
        state.add_occurrence(position)

        with state.scope("()"):
            other_node = state.follow_path(("..",))
            state.current_node.alias_namespace(other_node)

            with state.scope("."):
                for statement in node.body:
                    visit(statement, source, state)


@visit.register
def visit_call(node: ast.Call, source: Source, state: State) -> None:
    call_position = node_position(node, source)

    for arg in node.args:
        visit(arg, source, state)

    names = names_from(node.func)

    visit(node.func, source, state)
    position = node_position(node, source)
    lookup_scope = state.lookup_scopes[-1]
    if names == ("super",) and lookup_scope:
        with state.scope("super"):
            with state.scope("()"):
                state.current_node.alias_namespace(lookup_scope["()"])
    else:
        with ExitStack() as stack:
            if names:
                stack.enter_context(state.scope(names[0]))
                for name in names[1:]:
                    stack.enter_context(state.scope(name))
                stack.enter_context(state.scope("()"))

                for keyword in node.keywords:
                    if not keyword.arg:
                        continue

                    position = source.find_after(keyword.arg, call_position)
                    with state.scope(keyword.arg):
                        state.add_occurrence(position)


@singledispatch
def names_from(node: ast.AST) -> QualifiedName:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> QualifiedName:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> QualifiedName:
    return (*names_from(node.value), ".", node.attr)


@names_from.register
def call_names(node: ast.Call) -> QualifiedName:
    names = (*names_from(node.func), "()")
    return names


@visit.register
def visit_attribute(node: ast.Attribute, source: Source, state: State) -> None:
    visit(node.value, source, state)
    position = node_position(node, source)

    names = names_from(node.value)
    with ExitStack() as stack:
        for name in names:
            position = source.find_after(name, position)
            stack.enter_context(state.scope(name))

        stack.enter_context(state.scope("."))
        position = source.find_after(node.attr, position)
        stack.enter_context(state.scope(node.attr))
        state.add_occurrence(position)


def visit_comprehension(
    node: ast.DictComp | ast.ListComp | ast.SetComp | ast.GeneratorExp,
    source: Source,
    state: State,
    *sub_nodes: ast.AST,
) -> None:
    position = node_position(node, source)
    name = f"{type(node)}-{position.row},{position.column}"

    with state.scope(name):
        for generator in node.generators:
            visit(generator.target, source, state)
            visit(generator.iter, source, state)
            for if_node in generator.ifs:
                visit(if_node, source, state)

        for sub_node in sub_nodes:
            visit(sub_node, source, state)


@visit.register
def visit_dictionary_comprehension(
    node: ast.DictComp, source: Source, state: State
) -> None:
    visit_comprehension(node, source, state, node.key, node.value)


@visit.register
def visit_list_comprehension(node: ast.ListComp, source: Source, state: State) -> None:
    visit_comprehension(node, source, state, node.elt)


@visit.register
def visit_set_comprehension(node: ast.SetComp, source: Source, state: State) -> None:
    visit_comprehension(node, source, state, node.elt)


@visit.register
def visit_generator_exp(node: ast.GeneratorExp, source: Source, state: State) -> None:
    visit_comprehension(node, source, state, node.elt)


@visit.register
def visit_global(node: ast.Global, source: Source, state: State) -> None:
    position = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, position)
        with state.scope(name):
            state.add_occurrence(position)
            state.alias(("~", name))


@visit.register
def visit_nonlocal(node: ast.Nonlocal, source: Source, state: State) -> None:
    position = node_position(node, source)
    for name in node.names:
        position = source.find_after(name, position)
        with state.scope(name):
            state.add_occurrence(position)
            state.alias(("..", "..", "..", name))


def all_occurrence_positions(
    position: Position, other_sources: list[Source] | None = None
) -> list[Position]:
    source = position.source
    state = State(position)
    visit(source.get_ast(), source=source, state=state)
    for other in other_sources or []:
        visit(other.get_ast(), source=other, state=state)

    if state.found:
        return sorted(state.found.occurrences)

    return []
