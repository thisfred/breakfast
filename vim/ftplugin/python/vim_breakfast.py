from breakfast.occurrence import Position
from breakfast.rename import Rename


def do_rename(buffer_contents, old_name, row, column, new_name):
    refactoring = Rename(
        lines=buffer_contents,
        position=Position(row=row, column=column),
        old_name=old_name,
        new_name=new_name)
    refactoring.apply()
    for i, line in refactoring.get_changes():
        if buffer_contents[i] != line:
            yield (i, line)
