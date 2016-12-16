from ast import parse
from collections import defaultdict

from breakfast.occurrence import Position
from breakfast.rename import NameCollector


class Source:

    def __init__(self, text):
        self.lines = text.split('\n')
        self.changes = {}  # type: Dict[int, str]

    @classmethod
    def from_lines(cls, lines):
        instance = cls("")
        instance.lines = lines
        return instance

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
        if position.column == 0:
            new_row = position.row - 1
            position = Position(
                row=new_row, column=len(self.lines[new_row]) - 1)
        else:
            position = Position(row=position.row, column=position.column - 1)
        return position

    def rename(self, cursor, old_name, new_name):
        name_position = self.get_start(name=old_name, before=cursor)
        visitor = NameCollector(old_name)
        visitor.visit(self.get_ast())
        for occurrence in find_occurrences(name_position,
                                           visitor.occurrences):
            self.replace(
                position=occurrence,
                old=old_name,
                new=new_name)


def find_occurrences(position, occurrences):
    grouped = group_occurrences(occurrences)
    for positions in grouped.values():
        if position in positions:
            return sorted(positions, reverse=True)


def group_occurrences(occurrences):
    to_do = {}
    done = defaultdict(list)
    for path in sorted(occurrences.keys(), reverse=True):
        path_occurrences = occurrences[path]
        positions = [o.position for o in path_occurrences]
        for occurrence in path_occurrences:
            if occurrence.is_definition:
                done[path] = positions
                break
        else:
            to_do[path[:-1]] = positions

    for path in to_do:
        for prefix in get_prefixes(path, done):
            done[prefix].extend(to_do[path])
            break

    return done


def get_prefixes(path, done):
    prefix = path
    while prefix and prefix not in done:
        prefix = prefix[:-1]
        yield prefix
    yield prefix
