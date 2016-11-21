from breakfast.position import Position
from breakfast.rename import modified


def renamed(buffer_contents, old_name, row, column, new_name):
    for i, line in modified(source=buffer_contents,
                            cursor=Position(row=row, column=column),
                            old_name=old_name,
                            new_name=new_name):
        if buffer_contents[i] != line:
            yield (i, line)
