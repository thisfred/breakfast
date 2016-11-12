"""Rename refactorings."""
from typing import Any, List, Tuple  # noqa
from collections import defaultdict
from itertools import takewhile
from ast import Attribute, Call, ClassDef, FunctionDef, Name, NodeVisitor, parse


class Position:

    def __init__(self, *, row, column):
        self.row = row
        self.column = column

    @classmethod
    def from_node(cls, node):
        return cls(row=node.lineno - 1, column=node.col_offset)

    def _add_offset(self, offset: int):
        return Position(row=self.row, column=self.column + offset)

    def __add__(self, column_offset: int):
        return self._add_offset(column_offset)

    def __sub__(self, column_offset: int):
        return self._add_offset(-column_offset)

    def __eq__(self, other) -> bool:
        return self.row == other.row and self.column == other.column

    def __repr__(self):
        return 'Position(row=%s, column=%s)' % (self.row, self.column)


class NameVisitor(NodeVisitor):

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.positions = defaultdict(list)  # type: Dict[Any, Any]
        self.scope = tuple()  # type: Tuple[str, ...]
        self.names = {}

    def find_scope(self, position: Position) -> Tuple[str, ...]:
        for scope, positions in self.positions.items():
            if position in positions:
                return scope
        raise KeyError("Position not found.")

    def visit_Name(self, node: Name):  # noqa
        if node.id == self.source_name:
            self.positions[self.scope].append(Position.from_node(node))

    def visit_FunctionDef(self, node: FunctionDef):  # noqa
        if node.name == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len('def '))

        self.scope += (node.name,)
        for arg in node.args.args:
            if arg.arg == self.source_name:
                self.positions[self.scope].append(Position.from_node(arg))
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_ClassDef(self, node: ClassDef):  # noqa
        if node.name == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len('class '))
        self.scope += (node.name,)
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def visit_Attribute(self, node):  # noqa
        if node.attr == self.source_name:
            self.positions[self.scope].append(
                Position.from_node(node) + len(node.value.id) + 1)
        self.generic_visit(node)

    def visit_Assign(self, node):  # noqa
        if isinstance(node.value, Call):
            self.names[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        self.scope += (self.get_name(node.func),)
        for keyword in node.keywords:
            if keyword.arg == self.source_name:
                self.positions[self.scope].append(
                    Position.from_node(keyword.value) -
                    (len(self.source_name) + 1))
        self.generic_visit(node)
        self.scope = self.scope[:-1]

    def get_name(self, node):
        if isinstance(node, Name):
            return self.lookup(node.id)

        if isinstance(node, Attribute):
            return self.lookup(node.value.id)

        return self.lookup(node.attr)

    def lookup(self, name):
        return self.names.get(name, name)


class Renamer:

    def __init__(self, *,
                 source: str,
                 position: Position,
                 new_name: str) -> None:
        self.lines = source.split('\n')
        self.ast = parse(source)
        self.position = position
        self.new_name = new_name
        self.old_name = self.get_name_at_position(self.position)

    def rename(self) -> str:

        visitor = NameVisitor(source_name=self.old_name)
        visitor.visit(self.ast)

        original_scope = visitor.find_scope(self.position)
        for scope, positions in visitor.positions.items():
            for position in reversed(positions):
                if scope[:len(original_scope)] == original_scope:
                    actual_position = self.walk_back_to(
                        name=self.old_name,
                        start=position)
                    self.replace_name_at(actual_position)
        return '\n'.join(self.lines)

    def walk_back_to(self, *, name: str, start: Position) -> Position:
        while not self.is_at(substring=self.old_name, position=start):
            start = self.get_previous_position(start)
        return start

    def replace_name_at(self, start: Position) -> str:
        end = start + len(self.old_name)
        line = self.lines[start.row]
        self.lines[start.row] = \
            line[:start.column] + self.new_name + line[end.column:]

    def is_at(self, *, substring: str, position: Position) -> bool:
        return self.lines[position.row][position.column:].startswith(substring)

    def get_previous_position(self, position: Position) -> Position:
        if position.column == 0:
            new_row = position.row - 1
            position = Position(
                row=new_row, column=len(self.lines[new_row]) - 1)
        else:
            position = Position(row=position.row, column=position.column - 1)
        return position

    def get_name_at_position(self, position: Position) -> str:
        return "".join(
            takewhile(
                lambda c: valid_name_character(c),
                [char for char in self.get_string_starting_at(position)]))

    def get_string_starting_at(self, position: Position) -> str:
        return self.lines[position.row][position.column:]


def valid_name_character(char: str) -> bool:
    return char == '_' or char.isalnum()
