import ast

from typing import TYPE_CHECKING, List, Tuple, Union

from breakfast.position import Position
from breakfast.scope import Scope


if TYPE_CHECKING:
    from breakfast.source import Source


def find_occurrences(
    sources: List["Source"], old_name: str, position: Position
) -> List[Position]:
    visitor = NameVisitor(sources[0])
    for source in sources:
        visitor.visit_source(source)
    return visitor.top.find_occurrences(old_name, position)


class NameVisitor(ast.NodeVisitor):
    def __init__(self, initial_source: "Source") -> None:
        self.current_source = initial_source
        self.top = Scope()
        self.current = self.top

    def visit_source(self, source: "Source") -> None:
        self.current_source = source
        parsed = self.current_source.get_ast()
        self.visit(parsed)

    def visit_Module(self, node: ast.AST) -> None:  # pylint: disable=invalid-name
        old = self.current
        self.current = self.current.add_module(self.current_source.module_name)
        self.generic_visit(node)
        self.current = old

    def visit_ImportFrom(  # pylint: disable=invalid-name
        self, node: ast.ImportFrom
    ) -> None:
        if not node.module:
            return

        import_path = node.module.split(".")
        import_scope = self.top
        for path in import_path:
            import_scope = import_scope.get_scope(path)
        start = self._position_from_node(node)
        for imported in node.names:
            name = imported.name
            position = self.current_source.find_after(name, start)
            if position:
                original = import_scope.add_occurrence(name, position, force=True)
                self.current.add_scope(name, original)
            alias = imported.asname
            if alias:
                alias_position = self.current_source.find_after(alias, start)
                if not alias_position:
                    continue
                alias_scope = self.current.add_definition(alias, alias_position)
                original.add_alias(alias_scope)
                self.current.add_definition(alias, alias_position)

    def visit_Name(self, node: ast.Name) -> None:  # pylint: disable=invalid-name
        position = self._position_from_node(node)
        if self._is_definition(node):
            self.current.add_definition(node.id, position)
        else:
            self.current.add_occurrence(node.id, position)

    def visit_FunctionDef(  # pylint: disable=invalid-name
        self, node: ast.FunctionDef
    ) -> None:
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("def ")
        )
        old = self.current
        if self._is_staticmethod(node):
            self.current = self.current.add_static_method(
                name=node.name, position=position
            )
        else:
            self.current = self.current.add_function_definition(
                name=node.name, position=position
            )
        for i, arg in enumerate(node.args.args):
            position = self._position_from_node(arg)
            self.current.add_parameter(name=arg.arg, number=i, position=position)
            # if i == 0 and in_method and not is_static:
            #     self._add_class_alias(arg)
        self.generic_visit(node)
        self.current = old

    def visit_ClassDef(  # pylint: disable=invalid-name
        self, node: ast.ClassDef
    ) -> None:
        position = self._position_from_node(
            node=node, row_offset=len(node.decorator_list), column_offset=len("class ")
        )
        old = self.current
        self.current = self.current.add_class_definition(
            name=node.name, position=position
        )
        self.generic_visit(node)
        self.current = old

    def visit_Attribute(  # pylint: disable=invalid-name
        self, node: ast.Attribute
    ) -> None:
        self.visit(node.value)
        old = self.current
        for name in self._names_from(node.value):
            self.current = self.current.get_scope(name)
        name = node.attr
        start = self._position_from_node(node)
        position = self.current_source.find_after(name, start)
        if position:
            if self._is_definition(node):
                self.current.add_definition(name=name, position=position)
            else:
                self.current.add_occurrence(name=name, position=position, force=True)
        self.current = old

    def visit_Call(self, node: ast.Call) -> None:  # pylint: disable=invalid-name
        self.visit(node.func)
        old = self.current
        for name in self._names_from(node.func):
            self.current = self.current.get_scope(name)
        start = self._position_from_node(node)
        for keyword in node.keywords:
            if keyword.arg:
                position = self.current_source.find_after(keyword.arg, start)
                if position:
                    self.current.add_occurrence(name=keyword.arg, position=position)
        self.current = old
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Assign(self, node: ast.Assign) -> None:  # pylint: disable=invalid-name
        self.generic_visit(node)
        if isinstance(node.value, ast.Tuple):
            # multiple assignment
            values = [v for v in node.value.elts]
            idk = node.targets[0]
            if isinstance(idk, (ast.Tuple, ast.List, ast.Set)):
                targets = [t for t in idk.elts]
        else:
            values = [node.value]
            targets = [node.targets[0]]
        for target, value in zip(targets, values):
            target_ns = self.current
            for name in self._names_from(target):
                target_ns = target_ns.get_scope(name)
            value_ns = self.current
            for name in self._names_from(value):
                value_ns = value_ns.get_scope(name)
            value_ns.add_alias(target_ns)

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

    def _comp_visit(
        self, node: Union[ast.DictComp, ast.SetComp, ast.ListComp], *rest: ast.AST
    ) -> None:
        position = self._position_from_node(node)
        # Invent a name for the ad hoc scope. The dashes make sure it can
        # never clash with an actual Python name.
        name = "comprehension-%s-%s" % (position.row, position.column)
        old = self.current
        self.current = self.current.add_definition(name=name, position=position)
        for generator in node.generators:
            self.visit(generator)
        for sub_node in rest:
            self.visit(sub_node)
        self.current = old

    @staticmethod
    def _is_definition(node: Union[ast.Name, ast.Attribute]) -> bool:
        return isinstance(node.ctx, (ast.Param, ast.Store))

    @staticmethod
    def _is_staticmethod(node: ast.FunctionDef) -> bool:
        return any(
            n.id == "staticmethod"
            for n in node.decorator_list
            if isinstance(n, ast.Name)
        )

    def _position_from_node(
        self, node: ast.AST, row_offset: int = 0, column_offset: int = 0
    ) -> Position:
        return Position(
            source=self.current_source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
        )

    def _names_from(self, node: ast.AST) -> Tuple[str, ...]:
        if isinstance(node, ast.Name):
            return (node.id,)

        if isinstance(node, ast.Attribute):
            return self._names_from(node.value) + (node.attr,)

        if isinstance(node, ast.Call):
            return self._names_from(node.func)

        return tuple()
