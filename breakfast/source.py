import re
from ast import parse
from functools import total_ordering

from breakfast.position import Position
from breakfast.names import Names


@total_ordering
class Source(object):

    word = re.compile(r'\w+|\W+')

    def __init__(self, lines, module_name='module', file_name=None):
        self.lines = lines
        self.changes = {}  # type: Dict[int, str]
        self.module_name = module_name
        self.file_name = file_name

    def __eq__(self, other):
        return self is other

    def __lt__(self, other):
        return self.module_name < other.module_name

    def __gt__(self, other):
        return other.module_name < self.module_name

    def rename(self, row, column, new_name, additional_sources=None):
        position = Position(self, row=row, column=column)
        old_name = self.get_name_at(position)
        visitor = Names()
        visitor.visit_source(self)
        for source in additional_sources or []:
            visitor.visit_source(source)

        for occurrence in reversed(visitor.get_occurrences(old_name,
                                                           position)):
            occurrence.source.replace(
                position=occurrence,
                old=old_name,
                new=new_name)

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

    def find_after(self, name, start):
        regex = re.compile('\\b{}\\b'.format(name))
        match = regex.search(self.get_string_starting_at(start))
        lines = len(self.lines)
        while start.row <= lines and not match:
            start = start.copy(row=start.row + 1, column=0)
            match = regex.search(self.get_string_starting_at(start))

        if match:
            return start.copy(column=start.column + match.span()[0])

    def get_string_starting_at(self, position):
        return self.lines[position.row][position.column:]
