from ast import parse

from breakfast.position import IllegalPosition, Position


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
        start = self.get_start(name=old, before=position)
        end = start + len(old)
        self.modify_line(start=start, end=end, new=new)

    def modify_line(self, start, end, new):
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[:start.column] + new + line[end.column:]
        self.changes[line_number] = modified_line

    def get_start(self, name, before):
        while not self.get_string_starting_at(before).startswith(name):
            before = self.get_previous_position(before)
        return before

    def get_string_starting_at(self, position):
        return self.lines[position.row][position.column:]

    def get_previous_position(self, position):
        try:
            return position - 1
        except IllegalPosition:
            return self.get_last_column(position.row - 1)

    def get_last_column(self, row):
        return Position(row=row, column=len(self.lines[row]) - 1)
