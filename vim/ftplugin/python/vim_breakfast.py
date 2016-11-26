from breakfast.position import Position
from breakfast.source import Source


def renamed(buffer_contents, old_name, row, column, new_name):
    source = Source.from_lines(buffer_contents)
    source.rename(
        cursor=Position(row=row, column=column),
        old_name=old_name,
        new_name=new_name)
    for i, line in source.get_changes():
        if buffer_contents[i] != line:
            yield (i, line)
