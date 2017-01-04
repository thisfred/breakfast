from ast import parse

from breakfast.position import Position


class Source:

    def __init__(self, lines):
        self.lines = lines
        self.changes = {}  # type: Dict[int, str]

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
            start = start.previous()
        return start

    def find_after(self, name, start):
        while not self.get_string_starting_at(start).startswith(name):
            start = start.next()
        return start

    def get_string_starting_at(self, position):
        return self.lines[position.row][position.column:]

    def get_last_column(self, row):
        return Position(
            source=self, row=row, column=len(self.lines[row]) - 1)

    def position_from_node(self, node, column_offset=0, row_offset=0,
                           is_definition=False):
        return Position.from_node(
            source=self,
            node=node,
            column_offset=column_offset,
            row_offset=row_offset,
            is_definition=is_definition)
