from typing import Dict, List, Optional

from breakfast.position import Position


class Scope:
    def __init__(
        self,
        parent: Optional["Scope"] = None,
        is_class: bool = False,
        class_scope: Optional["Scope"] = None,
    ) -> None:
        self.parent = parent
        self._children: Dict[str, "Scope"] = {}
        self.occurrences: List[Position] = []
        self.is_class = is_class
        self._enclosing_class_scope = class_scope
        self.points_to: Optional["Scope"] = None
        self._aliases: List["Scope"] = []

    def add_module(self, name: str) -> "Scope":
        new = Scope(self)
        self._children[name] = new
        return self._children[name]

    def add_occurrence(
        self, name: str, position: Position, force: bool = False
    ) -> "Scope":
        return self.add_name(name, position, force=force)

    def add_function_definition(self, name: str, position: Position) -> "Scope":
        return self.add_name(
            name, position, force=True, class_scope=self if self.is_class else None
        )

    def add_static_method(self, name: str, position: Position) -> "Scope":
        return self.add_name(name, position, force=True)

    def add_class_definition(self, name: str, position: Position) -> "Scope":
        return self.add_name(name, position, force=True, is_class=True)

    def add_parameter(self, name: str, number: int, position: Position) -> "Scope":
        parameter = self.add_name(name, position, force=True)
        if number == 0 and self._enclosing_class_scope:
            self._enclosing_class_scope.add_alias(parameter)
        return parameter

    def add_alias(self, alias: "Scope") -> None:
        self._aliases.append(alias)
        alias.set_points_to(self)

    def set_points_to(self, class_scope: "Scope") -> None:
        self.points_to = class_scope

    def get_scope(self, name: str) -> "Scope":
        scope = self._children.get(name)
        if scope is None:
            scope = (self.parent or self).get_scope(name)
        while scope.points_to:
            scope = scope.points_to
        return scope

    def find_occurrences(self, name: str, position: Position) -> List[Position]:
        if name in self._children:
            child = self._children[name]
            if position in child.occurrences:
                return child.occurrences

        for child in self._children.values():
            occurrences = child.find_occurrences(name, position)
            if occurrences:
                return occurrences

        return []

    def add_scope(self, name: str, scope: "Scope") -> None:
        self._children[name] = scope

    def add_definition(self, name: str, position: Position) -> "Scope":
        if name in self._children:
            self._add_child_occurrence(name, position)
        else:
            new = Scope(self)
            self.add_scope(name, new)
            self._add_child_occurrence(name, position)
        return self._children[name]

    def add_name(  # pylint: disable=too-many-arguments
        self,
        name: str,
        position: Position,
        force: bool,
        is_class: bool = False,
        class_scope: Optional["Scope"] = None,
    ) -> "Scope":
        if name in self._children:
            self._add_child_occurrence(name, position)
        elif force or self.parent is None:
            new = Scope(self, is_class=is_class, class_scope=class_scope)
            self.add_scope(name, new)
            self._add_child_occurrence(name, position)
        else:
            enclosing_scope = self.parent
            # method bodies have no direct access to class scope
            if enclosing_scope.is_class:
                if enclosing_scope.parent:
                    enclosing_scope = enclosing_scope.parent
            return enclosing_scope.add_name(
                name, position, force=force, is_class=is_class, class_scope=class_scope
            )

        return self._children[name]

    def _add_child_occurrence(self, name: str, position: Position) -> None:
        self._children[name].occurrences.append(position)
