"""Rename refactorings."""
from collections import namedtuple
from ast import ClassDef, FunctionDef, Name, NodeVisitor, parse


Position = namedtuple('Position', ['row', 'column'])
Range = namedtuple('Range', ['start', 'end'])


class NameTransformer:

    def __init__(self, old_name, new_name):
        self.old_name = old_name
        self.new_name = new_name

    def action(self, node):
        node.name = self.new_name

    def predicate(self, node):
        return node.name == self.old_name


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

    def __init__(self,
                 source: str,
                 position: Position,
                 old_name: str,
                 new_name: str) -> None:
        self.source = source
        self.ast = parse(source)
        self.position = position
        self.old_name = old_name
        self.new_name = new_name

    def rename(self) -> str:
        visitor = NameVisitor(source_name=self.old_name)
        visitor.visit(self.ast)
        lines = self.source.split('\n')

        for position in reversed(visitor.positions):
            line = lines[position.row]
            lines[position.row] = self.replace_at(
                line=line,
                column_offset=position.column)
        return '\n'.join(lines)

    def replace_at(self, line: str, column_offset: int) -> str:
        end = column_offset + len(self.old_name)
        return line[:column_offset] + self.new_name + line[end:]


def contains(node, position):
    return node.fromlineno <= position.row <= node.tolineno


def matches(node, position):
    return (
        node.col_offset == position.column and
        position.row == node.fromlineno)
