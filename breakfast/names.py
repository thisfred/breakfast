import ast

from collections import defaultdict
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    DefaultDict,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from breakfast.position import Position


if TYPE_CHECKING:
    from breakfast.source import Source


class Collector:  # pylint: disable=too-many-instance-attributes
    def __init__(self) -> None:
        self.in_method = False
        self._definitions: DefaultDict[Tuple[str, ...], List[Position]] = defaultdict(
            list
        )
        self._occurrences: DefaultDict[Tuple[str, ...], List[Position]] = defaultdict(
            list
        )
        self._class_aliases: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self._current_namespace: Tuple[str, ...] = tuple()
        self._aliases: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self._classes: Set[Tuple[str, ...]] = set()
        self._superclasses: DefaultDict[
            Tuple[str, ...], List[Tuple[str, ...]]
        ] = defaultdict(list)
        self._imports: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self._scopes: Set[Tuple[str, ...]] = set()
        self._method_scopes: Set[Tuple[str, ...]] = set()
        self._in_class: bool = False
        self._rewrites: Dict[Tuple[str, ...], Tuple[str, ...]] = {}

    @contextmanager
    def namespaces(self, names: Iterable[str]) -> Iterator[None]:
        for name in names:
            self._enter_namespace(name)
        yield
        for _ in names:
            self._leave_namespace()

    @contextmanager
    def enter_class(self, name: str) -> Iterator[None]:
        old = self._in_class
        self._in_class = True
        with self.namespace(name):
            yield
        self._in_class = old

    @contextmanager
    def enter_function(self, name: str) -> Iterator[None]:
        old_in_method = self.in_method
        self.in_method = self._in_class
        self.add_scope(name, self.in_method)
        with self.namespace(name):
            old_in_class = self._in_class
            self._in_class = False
            yield
        self._in_class = old_in_class
        self.in_method = old_in_method

    @contextmanager
    def namespace(self, name: str) -> Iterator[None]:
        self._enter_namespace(name)
        yield
        self._leave_namespace()

    def get_positions(self, position: Position) -> List[Position]:
        self._post_process()
        for name, positions in self._occurrences.items():
            if name in self._definitions and any(position == p for p in positions):
                return sorted(positions)
        return []

    def add_global(self, name: str) -> None:
        full_name = self.get_namespaced(name)
        self._rewrites[full_name] = full_name[:1] + full_name[-1:]

    def add_nonlocal(self, name: str) -> None:
        full_name = self.get_namespaced(name)
        self._rewrites[full_name] = full_name[:-2] + full_name[-1:]

    def add_scope(self, name: str, is_method: bool = False) -> None:
        if is_method:
            self._method_scopes.add(self.get_namespaced(name))
        else:
            self._scopes.add(self.get_namespaced(name))

    def add_definition(self, name: str, position: Position) -> None:
        full_name = self.get_namespaced(name)
        self._definitions[full_name].append(position)
        self._add_namespaced_occurrence(full_name, position)

    def add_occurrence(self, name: str, position: Position) -> None:
        full_name = self.get_namespaced(name)
        self._add_namespaced_occurrence(full_name, position)

    def get_namespaced(self, name: str) -> Tuple[str, ...]:
        return self._current_namespace + (name,)

    def add_alias(self, alias: str, name: str) -> None:
        self._aliases[self.get_namespaced(name)] = self.get_namespaced(alias)

    def add_superclass(self, subclass: str, superclass: str) -> None:
        namespaced_superclass = self.get_namespaced(superclass)
        if namespaced_superclass in self._imports:
            namespaced_superclass = self._imports[namespaced_superclass]
        self._superclasses[self.get_namespaced(subclass)].append(namespaced_superclass)

    def add_class_alias(self, alias: str) -> None:
        full_name = self.get_namespaced(alias)
        self._class_aliases[full_name] = full_name[:-2]

    def add_import(self, name: str, full_name: Tuple[str, ...]) -> None:
        self._imports[self.get_namespaced(name)] = full_name

    def add_class(self, name: str) -> None:
        self._classes.add(self.get_namespaced(name))

    def _post_process(self) -> None:
        self._rewrite_self()
        for name in self._occurrences.copy():
            if name in self._rewrites:
                new_name = self._rewrites[name]
                self._occurrences[new_name] += self._occurrences[name]
                del self._occurrences[name]
                continue

            if name not in self._definitions:
                rewritten = self._rewrite(name)
                if rewritten in self._definitions:
                    self._occurrences[rewritten] += self._occurrences[name]
                else:
                    definition = self._get_from_outer_scope(name)
                    if definition and definition in self._definitions:
                        self._occurrences[definition] += self._occurrences[name]
                del self._occurrences[name]

    def _get_from_outer_scope(self, name: Tuple[str, ...]) -> Optional[Tuple[str, ...]]:
        for scope in self._scopes:
            if is_prefix(scope, name):
                return scope[:-1] + name[len(scope) :]

        for scope in self._method_scopes:
            if is_prefix(scope, name):
                return scope[:-2] + name[len(scope) :]

        return None

    def _enter_namespace(self, name: str) -> None:
        self._current_namespace = self.get_namespaced(name)

    def _leave_namespace(self) -> None:
        self._current_namespace = self._current_namespace[:-1]

    def _add_namespaced_occurrence(
        self, full_name: Tuple[str, ...], position: Position
    ) -> None:
        self._occurrences[full_name].append(position)

    def _rewrite(self, full_name: Tuple[str, ...]) -> Tuple[str, ...]:
        if full_name in self._imports:
            return self._imports[full_name]

        prefix = full_name[:-1]
        while prefix in self._aliases:
            full_name = self._aliases[prefix] + full_name[-1:]
            prefix = full_name[:-1]

        if full_name not in self._definitions and full_name[:-1] in self._classes:
            inherited = self._get_inherited_definition(full_name)
            if inherited:
                return inherited

        return full_name

    def _rewrite_self(self) -> None:
        for name in self._occurrences.copy():
            for alias, value in self._class_aliases.items():
                if is_prefix(alias, name):
                    new_name = value + name[len(alias) :]
                    self._occurrences[new_name].extend(self._occurrences[name])
                    del self._occurrences[name]
                    if name in self._definitions:
                        self._definitions[new_name].extend(self._definitions[name])
                        del self._definitions[name]

    def _get_inherited_definition(
        self, full_name: Tuple[str, ...]
    ) -> Optional[Tuple[str, ...]]:
        cls = full_name[:-1]
        if cls not in self._superclasses:
            return None

        supers = self._superclasses[cls]
        seen: Set[Tuple[str, ...]] = set()
        name = full_name[-1]
        while supers:
            cls = supers[0]
            supers = supers[1:]
            if cls in seen:
                continue

            seen.add(cls)
            supername = cls + (name,)
            if supername in self._definitions:
                return supername

            if cls not in self._superclasses:
                continue

            new_supers = self._superclasses[cls]
            supers.extend(new_supers)

        return None


class Names(ast.NodeVisitor):
    def __init__(self, source: "Source") -> None:
        self.current_source = source
        self.collector = Collector()

    def visit_source(self, source: "Source") -> None:
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def get_occurrences(self, _: Any, position: Position) -> List[Position]:
        return self.collector.get_positions(position)

    def visit_Module(self, node: ast.Module) -> None:  # pylint: disable=invalid-name
        with self.collector.namespace(self.current_source.module_name):
            self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # pylint: disable=invalid-name
        if self._is_definition(node):
            action = self.collector.add_definition
        else:
            action = self.collector.add_occurrence
        position = self._position_from_node(node)
        name = node.id
        action(name, position=position)

    def visit_ClassDef(  # pylint: disable=invalid-name
        self, node: ast.ClassDef
    ) -> None:
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("class ")
        )
        self.collector.add_class(node.name)
        self.collector.add_definition(name=node.name, position=position)
        for base in node.bases:
            if isinstance(base, ast.Name):
                self.collector.add_superclass(node.name, base.id)
            self.visit(base)
        with self.collector.enter_class(node.name):
            for statement in node.body:
                self.visit(statement)

    def visit_FunctionDef(  # pylint: disable=invalid-name
        self, node: ast.FunctionDef
    ) -> None:
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("def ")
        )
        self.collector.add_definition(name=node.name, position=position)
        is_static = is_staticmethod(node)
        with self.collector.enter_function(node.name):
            for i, arg in enumerate(node.args.args):
                if i == 0 and self.collector.in_method and not is_static:
                    # this is 'self' in a method definition
                    self._add_self(arg)
                self._add_parameter(arg)
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # pylint: disable=invalid-name
        start = self._position_from_node(node)
        self.visit(node.func)
        names = self._names_from(node.func)
        with self.collector.namespaces(names):
            if self.current_source:
                for keyword in node.keywords:
                    if not keyword.arg or not start:
                        continue
                    position = self.current_source.find_after(keyword.arg, start)
                    self.collector.add_occurrence(name=keyword.arg, position=position)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Import(self, node: ast.Import) -> None:  # pylint: disable=invalid-name
        start = self._position_from_node(node)
        for alias in node.names:
            name = alias.name
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)

    def visit_ImportFrom(  # pylint: disable=invalid-name
        self, node: ast.ImportFrom
    ) -> None:
        start = self._position_from_node(node)
        for imported in node.names:
            name = imported.name
            alias = imported.asname
            full_name: Tuple[str, ...] = tuple(name)
            if node.module:
                full_name = (node.module, name)
            self.collector.add_import(alias or name, full_name)
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            if alias:
                position = self.current_source.find_after(alias, start)
                self.collector.add_definition(alias, position)

    def visit_Attribute(  # pylint: disable=invalid-name
        self, node: ast.Attribute
    ) -> None:
        start = self._position_from_node(node)
        self.visit(node.value)
        names = self._names_from(node.value)
        with self.collector.namespaces(names):
            name = node.attr
            position = self.current_source.find_after(name, start)
            if self._is_definition(node):
                self.collector.add_definition(name, position)
            else:
                self.collector.add_occurrence(name, position)

    def visit_Assign(self, node: ast.Assign) -> None:  # pylint: disable=invalid-name
        target_names = self._get_names(node.targets[0])
        value_names = self._get_names(node.value)
        self.generic_visit(node)
        for target, value in zip(target_names, value_names):
            if target and value:
                self.collector.add_alias(value, target)

    def visit_DictComp(  # pylint: disable=invalid-name
        self, node: ast.DictComp
    ) -> None:
        self._comp_visit(node, node.key, node.value)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # pylint: disable=invalid-name
        self._comp_visit(node, node.elt)

    def visit_ListComp(  # pylint: disable=invalid-name
        self, node: ast.ListComp
    ) -> None:
        self._comp_visit(node, node.elt)

    def visit_Global(self, node: ast.Global) -> None:  # pylint: disable=invalid-name
        start = self._position_from_node(node)
        for name in node.names:
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            self.collector.add_global(name)

        self.generic_visit(node)

    def visit_Nonlocal(  # pylint: disable=invalid-name
        self, node: ast.Nonlocal
    ) -> None:
        start = self._position_from_node(node)
        for name in node.names:
            position = self.current_source.find_after(name, start)
            self.collector.add_occurrence(name, position)
            self.collector.add_nonlocal(name)

    def _comp_visit(
        self, node: Union[ast.DictComp, ast.SetComp, ast.ListComp], *rest: ast.AST
    ) -> None:
        position = self._position_from_node(node)
        # The dashes make sure it can never clash with an actual Python name.
        name = "comprehension-%s-%s" % (position.row, position.column)
        # ns = self.current.add_namespace(
        #     name, inherit_namespace_from=self.current)
        self.collector.add_scope(name)
        with self.collector.namespace(name):
            # visit the generators *before* the expression that uses the
            # generated values, to make sure the generator expression is
            # defined before use.
            for generator in node.generators:
                self.visit(generator)
            for sub_node in rest:
                self.visit(sub_node)

    def _add_self(self, arg: ast.arg) -> None:
        alias = arg.arg
        self.collector.add_class_alias(alias)

    def _add_parameter(self, arg: ast.arg) -> None:
        position = self._position_from_node(arg)
        self.collector.add_definition(name=arg.arg, position=position)

    def _names_from(self, node: ast.AST) -> Tuple[str, ...]:
        if isinstance(node, ast.Name):
            return (node.id,)

        if isinstance(node, ast.Attribute):
            return self._names_from(node.value) + (node.attr,)

        if isinstance(node, ast.Subscript):
            return self._names_from(node.value)

        return tuple()

    def _position_from_node(
        self, node: ast.AST, row_offset: int = 0, column_offset: int = 0
    ) -> Position:
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
        )

    @staticmethod
    def _is_definition(node: Union[ast.Name, ast.Attribute]) -> bool:
        return isinstance(node.ctx, (ast.Param, ast.Store))

    def _get_names(self, value: ast.AST) -> List[Optional[str]]:
        if isinstance(value, ast.Tuple):
            return [self._get_value_name(v) for v in value.elts]

        return [self._get_value_name(value)]

    def _get_value_name(self, value: ast.AST) -> Optional[str]:
        if isinstance(value, ast.Attribute):
            return value.attr

        if isinstance(value, ast.Name):
            return value.id

        if isinstance(value, ast.Call):
            return self._get_value_name(value.func)

        return None


def is_staticmethod(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


def is_prefix(prefix: Tuple[str, ...], full_name: Tuple[str, ...]) -> bool:
    if len(full_name) <= len(prefix):
        return False

    return full_name[: len(prefix)] == prefix
