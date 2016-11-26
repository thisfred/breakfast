from typing import Any, Callable, List, Tuple  # noqa
from breakfast.position import Position
from breakfast.rename import NameVisitor
from ast import parse

FORWARD = 1
BACKWARD = -1


class Source:

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.split('\n')
        self.changes = {}  # type: Dict[int, str]

    @classmethod
    def from_lines(cls, lines: List[str]):
        instance = cls("")
        instance.text = '\n'.join(lines)
        instance.lines = lines
        return instance

    def render(self):
        return '\n'.join(
            self.changes.get(i, line)
            for i, line in enumerate(self.lines))

    def get_changes(self):
        for change in sorted(self.changes.items()):
            yield change

    def replace(self, *, position: Position, old: str, new: str):
        start = self.get_start(name=old, before=position)
        end = start + len(old)
        self.modify_line(start=start, end=end, new=new)

    def modify_line(self, *, start, end, new):
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[:start.column] + new + line[end.column:]
        self.changes[line_number] = modified_line

    def get_start(self, *, name: str, before: Position) -> Position:
        while not self.get_string_starting_at(before).startswith(name):
            before = self.get_previous_position(before)
        return before

    def get_string_starting_at(self, position: Position) -> str:
        return self.lines[position.row][position.column:]

    def get_previous_position(self, position: Position) -> Position:
        if position.column == 0:
            new_row = position.row - 1
            position = Position(
                row=new_row, column=len(self.lines[new_row]) - 1)
        else:
            position = Position(row=position.row, column=position.column - 1)
        return position

    def get_ast(self):
        return parse(self.text)

    def rename(self, cursor, old_name, new_name):
        start = self.get_start(name=old_name, before=cursor)
        visitor = NameVisitor(old_name=old_name)
        visitor.visit(self.get_ast())
        visitor.replace_occurrences(
            source=self,
            position=start,
            new_name=new_name)
