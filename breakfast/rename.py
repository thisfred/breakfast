"""Rename refactorings."""
from typing import List
from collections import namedtuple
from itertools import takewhile
from ast import ClassDef, FunctionDef, Name, NodeVisitor, parse


Position = namedtuple('Position', ['row', 'column'])


class FindNameVisitor(NodeVisitor):

    def __init__(self, position: Position) -> None:
        self.position = position
        self.found = None

    def visit_Name(self, node: Name):  # noqa
        if (node.lineno - 1 == self.position.row and
                node.col_offset == self.position.column):
            self.found = node


class NameVisitor(NodeVisitor):

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.positions = []  # type: List[Position]

    def visit_Name(self, node: Name):  # noqa
        if node.id == self.source_name:
            self.positions.append(
                Position(row=node.lineno - 1, column=node.col_offset))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: FunctionDef):  # noqa
        if node.name == self.source_name:
            self.positions.append(
                Position(
                    row=node.lineno - 1,
                    column=node.col_offset + len('def ')))
        for arg in node.args.args:
            if arg.arg == self.source_name:
                self.positions.append(
                    Position(row=arg.lineno - 1, column=arg.col_offset))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ClassDef):  # noqa
        if node.name == self.source_name:
            self.positions.append(
                Position(
                    row=node.lineno - 1,
                    column=node.col_offset + len('class ')))
        self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa
        if node.attr == self.source_name:
            column = node.col_offset + len(node.value.id) + 1
            self.positions.append(
                Position(row=node.lineno - 1, column=column))
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa
        for keyword in node.keywords:
            if keyword.arg == self.source_name:
                self.positions.append(
                    Position(
                        row=keyword.value.lineno - 1,
                        column=keyword.value.col_offset - (
                            len(self.source_name) + 1)))
        self.generic_visit(node)


class Renamer:

    def __init__(self, *,
                 source: str,
                 position: Position,
                 new_name: str) -> None:
        self.lines = source.split('\n')
        self.ast = parse(source)
        self.position = position
        self.new_name = new_name
        self.old_name = get_name_at_position(self.lines, self.position)

    def rename(self) -> str:

        visitor = NameVisitor(source_name=self.old_name)
        visitor.visit(self.ast)

        for position in reversed(visitor.positions):
            while not is_at(
                    lines=self.lines,
                    substring=self.old_name,
                    position=position):
                position = previous(position, self.lines)
            line = self.lines[position.row]
            self.lines[position.row] = self.replace_at(
                line=line,
                column_offset=position.column)
        return '\n'.join(self.lines)

    def replace_at(self, *, line: str, column_offset: int) -> str:
        end = column_offset + len(self.old_name)
        return line[:column_offset] + self.new_name + line[end:]


def is_at(*, lines: List[str], substring: str, position: Position) -> bool:
    return lines[position.row][position.column:].startswith(substring)


def previous(position: Position, lines: List[str]) -> Position:
    if position.column == 0:
        new_row = position.row - 1
        position = Position(row=new_row, column=len(lines[new_row]) - 1)
    else:
        position = Position(row=position.row, column=position.column - 1)
    return position


def get_name_at_position(lines: List[str], position: Position):
    return "".join(
        takewhile(
            lambda c: valid(c),
            [char for char in lines[position.row][position.column:]]))


def valid(char: str):
    return char == '_' or char.isalnum()
