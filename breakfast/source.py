import re
from ast import parse

from breakfast.position import IllegalPosition, Position
from breakfast.rename import Rename


class Source:

    word = re.compile(r'\w+|\W+')

    def __init__(self, lines, module_name='module'):
        self.lines = lines
        self.changes = {}  # type: Dict[int, str]
        self.module_name = module_name

    def rename(self, row, column, new_name, additional_sources=None):
        position = Position(self, row=row, column=column)
        old_name = position.get_name()
        refactoring = Rename(
            name=old_name,
            position=position,
            new_name=new_name)
        for source in additional_sources or []:
            refactoring.add_source(source)
        refactoring.apply()

    def get_name_at(self, position):
        return self.word.search(self.get_string_starting_at(position)).group()

    def get_ast(self):
        return parse('\n'.join(self.lines))

    def render(self):
        return '\n'.join(
            self.changes.get(i, line)
            for i, line in enumerate(self.lines))

    def get_changes(self):
        for change in sorted(self.changes.items()):
            yield change

    def replace(self, position, old, new):
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start, end, new):
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[:start.column] + new + line[end.column:]
        self.changes[line_number] = modified_line

    def find_before(self, name, start):
        while not self.get_string_starting_at(start).startswith(name):
            try:
                start = start - 1
            except IllegalPosition:
                start = self.get_last_column(start.row - 1)

        return start

    def find_after(self, name, start):
        while not self.get_string_starting_at(start).startswith(name):
            start = start + 1
            if len(self.lines[start.row]) < start.column:
                start = start.copy(row=start.row + 1, column=0)

        return start

    def get_string_starting_at(self, position):
        return self.lines[position.row][position.column:]

    def get_last_column(self, row):
        return Position(
            source=self, row=row, column=len(self.lines[row]) - 1)
